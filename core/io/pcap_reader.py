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
from dataclasses import dataclass
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

    @property
    def address(self) -> str:
        return f"{self.source_ip}:{self.source_port}"


def _ip_text(raw: bytes) -> str:
    if len(raw) == 4:
        return ".".join(str(b) for b in raw)
    groups = [raw[i] << 8 | raw[i + 1] for i in range(0, 16, 2)]
    best, run, start = (0, 0, 0), 0, 0
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
    if flags_offset & 0x1FFF:  # 非首片分片：没有完整 UDP 头
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
        if cursor + 8 > len(data):
            return None
        ext_len = (data[cursor + 1] + 1) * 8
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

    if linktype in (LINKTYPE_RAW, LINKTYPE_IPV4):
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


def _iter_pcap(handle, path: str, first: bytes, cancel) -> Iterator[UdpDatagram]:
    magic = struct.unpack("<I", first)[0]
    if magic in (PCAP_MAGIC_LE, PCAP_MAGIC_NS_LE):
        endian, scale = "<", (1e-9 if magic == PCAP_MAGIC_NS_LE else 1e-6)
    elif magic in (PCAP_MAGIC_BE, PCAP_MAGIC_NS_BE):
        endian, scale = ">", (1e-9 if magic == PCAP_MAGIC_NS_BE else 1e-6)
    else:
        raise PcapError(f"无法识别的 pcap 文件头: {first.hex()}")
    header = first + _read_exact(handle, 20, path)
    linktype = struct.unpack_from(endian + "I", header, 20)[0]
    while True:
        if cancel is not None:
            check_cancel(cancel)
        record = handle.read(16)
        if not record:
            return
        if len(record) != 16:
            raise PcapError("pcap 记录头不完整。")
        ts_sec, ts_frac, incl_len, _orig_len = struct.unpack(endian + "IIII", record)
        payload = handle.read(incl_len)
        if len(payload) != incl_len:
            raise PcapError("pcap 数据包被截断。")
        packet = _udp_from_frame(payload, linktype, ts_sec + ts_frac * scale)
        if packet is not None:
            yield packet


def _iter_pcapng(handle, path: str, first: bytes, cancel) -> Iterator[UdpDatagram]:
    if struct.unpack("<I", first)[0] != PCAPNG_MAGIC:
        raise PcapError("不是 pcapng 文件。")
    interfaces: list[int] = []
    endian = "<"
    raw_len = _read_exact(handle, 4, path)
    # SHB 的块长度尚未知字节序：两个方向各解一次，取合理的那个。
    candidates = [struct.unpack("<I", raw_len)[0], struct.unpack(">I", raw_len)[0]]
    block_len = next((n for n in candidates if 12 <= n < 1 << 20), 0)
    if not block_len:
        raise PcapError("pcapng 第一个块长度非法。")
    endian = "<" if block_len == candidates[0] else ">"
    block = first + raw_len + _read_exact(handle, block_len - 8, path)
    body = block[8:-4]
    if len(body) < 4:
        raise PcapError("pcapng SHB 缺少字节序标记。")
    byte_order_magic = body[:4]
    if byte_order_magic == b"\x4d\x3c\x2b\x1a":
        endian = "<"
    elif byte_order_magic == b"\x1a\x2b\x3c\x4d":
        endian = ">"
    else:
        raise PcapError("pcapng 字节序标记无效。")

    while True:
        if cancel is not None:
            check_cancel(cancel)
        head = handle.read(8)
        if not head:
            return
        if len(head) != 8:
            raise PcapError("pcapng 块头不完整。")
        block_type, block_len = struct.unpack(endian + "II", head)
        if block_len < 12:
            raise PcapError("pcapng 块长度非法。")
        rest = _read_exact(handle, block_len - 8, path)
        body = rest[:-4]
        if block_type == 0x00000001 and len(body) >= 8:  # IDB
            linktype = struct.unpack_from(endian + "H", body, 0)[0]
            interfaces.append(linktype)
        elif block_type == 0x00000006 and len(body) >= 20:  # EPB
            interface_id, ts_high, ts_low, cap_len = struct.unpack_from(endian + "IIII", body, 0)
            data = body[20:20 + cap_len]
            if interface_id < len(interfaces):
                timestamp = ((ts_high << 32) | ts_low) * 1e-6
                packet = _udp_from_frame(data, interfaces[interface_id], timestamp)
                if packet is not None:
                    yield packet
        elif block_type == 0x00000003 and len(body) >= 4 and interfaces:  # SPB
            orig_len = struct.unpack_from(endian + "I", body, 0)[0]
            data = body[4:4 + orig_len]
            packet = _udp_from_frame(data, interfaces[0], 0.0)
            if packet is not None:
                yield packet
        elif block_type == 0x00000005 and len(body) >= 20:  # ISB：时钟走时，忽略
            continue


def iter_udp_datagrams(path: str, cancel=None) -> Iterator[UdpDatagram]:
    """按文件顺序逐个产出 UDP 载荷（pcap 或 pcapng 自动识别）。"""
    path = os.path.abspath(os.path.expanduser(path))
    with open(path, "rb") as handle:
        first = handle.read(4)
        if len(first) != 4:
            raise PcapError(f"文件为空或过短: {path}")
        magic_le = struct.unpack("<I", first)[0]
        if magic_le == PCAPNG_MAGIC:
            yield from _iter_pcapng(handle, path, first, cancel)
        else:
            yield from _iter_pcap(handle, path, first, cancel)
