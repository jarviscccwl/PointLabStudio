"""pcap / pcapng 读取（纯 numpy 实现，无第三方依赖）。

只做导入雷达包需要的一件事：按时间顺序取出 UDP 载荷。支持：

- pcap（微秒/纳秒时间戳，大小端）与 pcapng（SHB/IDB/EPB/SPB，多接口）；
- 链路层：Ethernet（含 802.1Q/802.1ad VLAN）、Linux SLL / SLL2、原始 IPv4、原始 IPv6；
- IPv4 / IPv6 + UDP（忽略 IPv4 分片与 IPv6 扩展头）。

雷达包里的 DIFOP/MSOP 都走 UDP，因此不需要理解上层应用协议。
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, replace
from typing import Iterator

from core.cancellation import check_cancel

PCAP_MAGIC_LE = 0xA1B2C3D4
PCAP_MAGIC_BE = 0xD4C3B2A1
PCAP_MAGIC_NS_LE = 0xA1B23C4D
PCAP_MAGIC_NS_BE = 0x4D3CB2A1
PCAPNG_MAGIC = 0x0A0D0D0A

LINKTYPE_NULL = 0
LINKTYPE_ETHERNET = 1
LINKTYPE_RAW = 101
LINKTYPE_IPV4 = 228
LINKTYPE_IPV6 = 229
LINKTYPE_LINUX_SLL = 113
LINKTYPE_LINUX_SLL2 = 276

ETHERTYPE_IPV4 = 0x0800
ETHERTYPE_IPV6 = 0x86DD
VLAN_TYPES = (0x8100, 0x88A8, 0x9100)


class PcapError(ValueError):
    """文件不是合法的 pcap/pcapng，或结构损坏。"""


@dataclass(frozen=True)
class UdpDatagram:
    timestamp: float
    source_ip: str
    source_port: int
    dest_port: int
    payload: bytes
    timestamp_ns: int | None = None
    offset: int = 0
    next_offset: int = 0
    context: dict | None = None

    @property
    def address(self) -> str:
        return f"{self.source_ip}:{self.source_port}"


def _ip_text(raw: bytes) -> str:
    if len(raw) == 4:
        return ".".join(str(b) for b in raw)
    groups = [raw[i] << 8 | raw[i + 1] for i in range(0, 16, 2)]
    best, start = (0, 0, 0), 0
    while start < 8:
        if groups[start] == 0:
            end = start
            while end < 8 and groups[end] == 0:
                end += 1
            if end - start > best[0]:
                best = (end - start, start, end)
            start = end
        else:
            start += 1
    if best[0] < 2:
        return ":".join(f"{g:x}" for g in groups)
    head = ":".join(f"{g:x}" for g in groups[: best[1]])
    tail = ":".join(f"{g:x}" for g in groups[best[2]:])
    return f"{head}::{tail}"


def _udp_from_ipv4(data: bytes, offset: int, timestamp: float) -> UdpDatagram | None:
    if offset + 20 > len(data):
        return None
    version_ihl = data[offset]
    if version_ihl >> 4 != 4:
        return None
    ihl = (version_ihl & 0x0F) * 4
    if ihl < 20 or offset + ihl + 8 > len(data):
        return None
    flags_offset = struct.unpack_from(">H", data, offset + 6)[0]
    if flags_offset & 0x3FFF:  # 分片需要重组，当前读取器跳过全部分片
        return None
    if data[offset + 9] != 17:
        return None
    udp = offset + ihl
    sport, dport, length = struct.unpack_from(">HHH", data, udp)
    end = min(len(data), udp + length) if length >= 8 else len(data)
    if udp + 8 > end:
        return None
    return UdpDatagram(timestamp, _ip_text(data[offset + 12:offset + 16]),
                       sport, dport, data[udp + 8:end])


def _udp_from_ipv6(data: bytes, offset: int, timestamp: float) -> UdpDatagram | None:
    if offset + 40 > len(data) or data[offset] >> 4 != 6:
        return None
    next_header = data[offset + 6]
    cursor = offset + 40
    for _ in range(8):
        if next_header == 17:
            break
        if next_header not in (0, 43, 60, 51) or cursor + 8 > len(data):
            # Fragmented, encrypted and unknown payloads need separate decoding.
            return None
        ext_len = (data[cursor + 1] + 2) * 4 if next_header == 51 else (data[cursor + 1] + 1) * 8
        next_header = data[cursor]
        cursor += ext_len
    else:
        return None
    if cursor + 8 > len(data):
        return None
    sport, dport, length = struct.unpack_from(">HHH", data, cursor)
    end = min(len(data), cursor + length) if length >= 8 else len(data)
    if cursor + 8 > end:
        return None
    return UdpDatagram(timestamp, _ip_text(data[offset + 8:offset + 24]),
                       sport, dport, data[cursor + 8:end])


def _udp_from_frame(data: bytes, linktype: int, timestamp: float) -> UdpDatagram | None:
    if linktype == LINKTYPE_ETHERNET:
        if len(data) < 14:
            return None
        ethertype = struct.unpack_from(">H", data, 12)[0]
        offset = 14
        while ethertype in VLAN_TYPES:
            if offset + 4 > len(data):
                return None
            ethertype = struct.unpack_from(">H", data, offset + 2)[0]
            offset += 4
        if ethertype == ETHERTYPE_IPV4:
            return _udp_from_ipv4(data, offset, timestamp)
        if ethertype == ETHERTYPE_IPV6:
            return _udp_from_ipv6(data, offset, timestamp)
        return None

    if linktype == LINKTYPE_LINUX_SLL:
        if len(data) < 16:
            return None
        proto = struct.unpack_from(">H", data, 14)[0]
        return _udp_from_ipv4(data, 16, timestamp) if proto == ETHERTYPE_IPV4 \
            else _udp_from_ipv6(data, 16, timestamp)

    if linktype == LINKTYPE_LINUX_SLL2:
        if len(data) < 20:
            return None
        proto = struct.unpack_from(">H", data, 0)[0]
        return _udp_from_ipv4(data, 20, timestamp) if proto == ETHERTYPE_IPV4 \
            else _udp_from_ipv6(data, 20, timestamp)

    if linktype == LINKTYPE_RAW:
        return _udp_from_ipv6(data, 0, timestamp) if data and data[0] >> 4 == 6 else _udp_from_ipv4(data, 0, timestamp)

    if linktype == LINKTYPE_IPV4:
        return _udp_from_ipv4(data, 0, timestamp)

    if linktype == LINKTYPE_IPV6:
        return _udp_from_ipv6(data, 0, timestamp)

    if linktype == LINKTYPE_NULL and len(data) >= 4:
        family = struct.unpack_from("<I", data, 0)[0]
        return _udp_from_ipv4(data, 4, timestamp) if family == 2 \
            else _udp_from_ipv6(data, 4, timestamp)

    return None


def _read_exact(handle, size: int, path: str) -> bytes:
    chunk = handle.read(size)
    if len(chunk) != size:
        raise PcapError(f"文件在 {path} 处提前结束，可能已损坏或被截断。")
    return chunk


def _located_packet(data, linktype, timestamp_ns, offset, next_offset, context):
    packet = _udp_from_frame(data, linktype, (timestamp_ns or 0) / 1e9)
    if packet is not None:
        return replace(packet, timestamp_ns=timestamp_ns, offset=offset,
                       next_offset=next_offset, context=context)
    return None


def _iter_pcap(handle, path, first, cancel, stop_offset=None, context=None):
    if context is None:
        magic = struct.unpack("<I", first)[0]
        if magic in (PCAP_MAGIC_LE, PCAP_MAGIC_NS_LE):
            endian = "<"
        elif magic in (PCAP_MAGIC_BE, PCAP_MAGIC_NS_BE):
            endian = ">"
        else:
            raise PcapError(f"无法识别的 pcap 文件头: {first.hex()}")
        multiplier = 1 if magic in (PCAP_MAGIC_NS_LE, PCAP_MAGIC_NS_BE) else 1000
        header = first + _read_exact(handle, 20, path)
        linktype = struct.unpack_from(endian + "I", header, 20)[0] & 0xFFFF
        context = dict(format="pcap", endian=endian, multiplier=multiplier, linktype=linktype)
    endian = context["endian"]
    while stop_offset is None or handle.tell() < stop_offset:
        check_cancel(cancel)
        offset = handle.tell()
        record = handle.read(16)
        if not record:
            return
        if len(record) != 16:
            raise PcapError("pcap 记录头不完整。")
        seconds, fraction, length, _ = struct.unpack(endian + "IIII", record)
        if length > 64 * 1024 * 1024:
            raise PcapError("pcap 数据包长度非法。")
        data = _read_exact(handle, length, path)
        stamp = seconds * 1_000_000_000 + fraction * context["multiplier"]
        packet = _located_packet(data, context["linktype"], stamp, offset, handle.tell(), context)
        if packet is not None:
            yield packet


def _interface(body, endian):
    linktype, _, snaplen = struct.unpack_from(endian + "HHI", body)
    divisor, offset_seconds = 1_000_000, 0
    cursor = 8
    while cursor + 4 <= len(body):
        code, length = struct.unpack_from(endian + "HH", body, cursor)
        cursor += 4
        if code == 0:
            break
        if cursor + length > len(body):
            raise PcapError("pcapng 接口选项不完整。")
        value = body[cursor:cursor + length]
        if code == 9 and length == 1:
            resolution = value[0]
            divisor = 2 ** (resolution & 0x7F) if resolution & 0x80 else 10 ** resolution
        elif code == 14 and length == 8:
            offset_seconds = struct.unpack(endian + "q", value)[0]
        cursor += (length + 3) & ~3
    return dict(linktype=linktype, snaplen=snaplen, divisor=divisor, offset_seconds=offset_seconds)


def _iter_pcapng(handle, path, first, cancel, stop_offset=None, context=None):
    interfaces = list(context["interfaces"]) if context else []
    endian = context["endian"] if context else "<"
    if context is None:
        handle.seek(-4, 1)
    while stop_offset is None or handle.tell() < stop_offset:
        check_cancel(cancel)
        offset = handle.tell()
        head = handle.read(8)
        if not head:
            return
        if len(head) != 8:
            raise PcapError("pcapng 块头不完整。")
        if head[:4] == bytes.fromhex("0a0d0d0a"):
            bom = _read_exact(handle, 4, path)
            if bom == bytes.fromhex("4d3c2b1a"):
                endian = "<"
            elif bom == bytes.fromhex("1a2b3c4d"):
                endian = ">"
            else:
                raise PcapError("pcapng 字节序标记无效。")
            block_len = struct.unpack(endian + "I", head[4:])[0]
            if block_len < 28 or block_len > 64 * 1024 * 1024 or block_len % 4:
                raise PcapError("pcapng SHB 长度非法。")
            rest = bom + _read_exact(handle, block_len - 12, path)
            block_type = PCAPNG_MAGIC
            interfaces = []
        else:
            block_type, block_len = struct.unpack(endian + "II", head)
            if block_len < 12 or block_len > 64 * 1024 * 1024 or block_len % 4:
                raise PcapError("pcapng 块长度非法。")
            rest = _read_exact(handle, block_len - 8, path)
        if struct.unpack(endian + "I", rest[-4:])[0] != block_len:
            raise PcapError("pcapng 块长度尾标不匹配。")
        body = rest[:-4]
        if block_type == 1 and len(body) >= 8:
            interfaces.append(_interface(body, endian))
            continue
        timestamp_ns = None
        if block_type == 6 and len(body) >= 20:
            interface_id, high, low, length, _ = struct.unpack_from(endian + "IIIII", body)
            if interface_id >= len(interfaces) or 20 + length > len(body):
                raise PcapError("pcapng 数据包接口或长度非法。")
            interface = interfaces[interface_id]
            ticks = (high << 32) | low
            timestamp_ns = (ticks * 1_000_000_000 // interface["divisor"] +
                            interface["offset_seconds"] * 1_000_000_000)
            data = body[20:20 + length]
        elif block_type == 3 and len(body) >= 4 and interfaces:
            interface = interfaces[0]
            original = struct.unpack_from(endian + "I", body)[0]
            length = min(original, interface["snaplen"] or original)
            if 4 + length > len(body):
                raise PcapError("pcapng 简单数据包不完整。")
            data = body[4:4 + length]
        else:
            continue
        packet_context = dict(format="pcapng", endian=endian, interfaces=list(interfaces))
        packet = _located_packet(data, interface["linktype"], timestamp_ns,
                                 offset, handle.tell(), packet_context)
        if packet is not None:
            yield packet


def iter_udp_datagrams(path: str, cancel=None, *, start_offset=None,
                       stop_offset=None, context=None) -> Iterator[UdpDatagram]:
    """Read UDP payloads, optionally seeking straight to an indexed record range.

    Offsets and the container context on each datagram can be saved persistently.
    PCAPNG simple packets have timestamp_ns=None because their clock is absent.
    """
    path = os.path.abspath(os.path.expanduser(path))
    check_cancel(cancel)
    with open(path, "rb") as handle:
        if start_offset is not None:
            if context is None:
                raise PcapError("按偏移读取需要索引中的容器上下文。")
            handle.seek(start_offset)
            first = b""
            format_name = context["format"]
        else:
            first = _read_exact(handle, 4, path)
            format_name = "pcapng" if first == bytes.fromhex("0a0d0d0a") else "pcap"
        reader = _iter_pcapng if format_name == "pcapng" else _iter_pcap
        yield from reader(handle, path, first, cancel, stop_offset, context)
