"""Seekable RoboSense EM4 recordings with an atomic, source-validated user cache."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile

from core.cancellation import check_cancel
from core.io import pcap_reader, rs_em4
from core.io.recording_types import FrameData, FrameRef, RecordingSource, SourceInfo, StreamInfo
from core.model.entities import CloudEntity, ScalarField

PARSER_VERSION = 2
CLOCK_WARNING = "Device and capture clocks are preserved separately. Their synchronization and offset are unverified."
NOMINAL_WARNING = "No valid DIFOP2 calibration was found. Nominal angles are approximate."


class PcapSource(RecordingSource):
    """Decode uncompressed EM4 UDP only, preserving incomplete captured frames."""

    def __init__(self, path: str, cache_dir=None, *, drop_noise=True, min_range_m=0.0):
        super().__init__(os.path.abspath(os.path.expanduser(path)))
        cache_root = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CACHE_HOME")
        self.cache_dir = Path(cache_dir) if cache_dir is not None else (
            Path(cache_root) if cache_root else Path.home() / ".cache"
        ) / "PointLabStudio" / "recording-index"
        self.drop_noise = drop_noise
        self.min_range_m = min_range_m
        self._calibrations: dict[str, list[dict]] = {}
        self._identity = None
        self._indexed = False

    def _source_identity(self):
        stat = os.stat(self.path)
        return dict(path=os.path.normcase(os.path.realpath(self.path)),
                    size=stat.st_size, mtime_ns=stat.st_mtime_ns, parser_version=PARSER_VERSION)

    def _cache_path(self):
        key = hashlib.sha256(os.path.normcase(os.path.realpath(self.path)).encode("utf-8")).hexdigest()
        return self.cache_dir / (key + ".json")

    @staticmethod
    def _stream_id(datagram):
        return f"em4/{datagram.source_ip}:{datagram.source_port}"

    @staticmethod
    def _stream(datagram):
        stream_id = PcapSource._stream_id(datagram)
        return StreamInfo(stream_id, f"EM4 {datagram.address}", "pointcloud", "RoboSense EM4 MSOP",
                          metadata=dict(protocol="RoboSense EM4", source_ip=datagram.source_ip,
                                        source_port=datagram.source_port, clock_domain="capture",
                                        timestamp_domain="device"))

    def inspect(self, cancel=None):
        check_cancel(cancel)
        identity = self._source_identity()
        if self._indexed and identity == self._identity:
            return self.info
        streams, warnings = {}, [CLOCK_WARNING]
        # Inspection is bounded. Indexing discovers streams appearing later in the file.
        for datagram in pcap_reader.iter_udp_datagrams(self.path, cancel=cancel, stop_offset=8 * 1024 * 1024):
            magic = datagram.payload[:4]
            if magic == rs_em4.MSOP_MAGIC:
                streams.setdefault(self._stream_id(datagram), self._stream(datagram))
                try:
                    rs_em4.parse_msop(datagram.payload)
                except rs_em4.Em4FormatError as exc:
                    if str(exc) not in warnings:
                        warnings.append(str(exc))
            elif magic == rs_em4.MSOP_FRAGMENT_MAGIC:
                warnings.append("Fragmented/compressed EM4 MSOP is recognized but decoding is unsupported.")
        if not streams:
            warnings.append("No supported EM4 MSOP was recognized in the inspection sample. Other PCAP sensor protocols are unsupported.")
        self.info = SourceInfo(self.path, "pcapng" if self.path.lower().endswith(".pcapng") else "pcap",
                               list(streams.values()), size_bytes=identity["size"], warnings=list(dict.fromkeys(warnings)),
                               metadata=dict(protocol="RoboSense EM4" if streams else "unknown",
                                             inspection_sample_bytes=min(identity["size"], 8 * 1024 * 1024)))
        return self.info

    def _load_cache(self, identity, cancel):
        check_cancel(cancel)
        try:
            with self._cache_path().open("r", encoding="utf-8") as handle:
                cached = json.load(handle)
            if cached["identity"] != identity:
                return False
            info = cached["info"]
            info["streams"] = [StreamInfo(**value) for value in info["streams"]]
            for stream in info["streams"]:
                stream.metadata.update(clock_domain="capture", timestamp_domain="device")
            index = {stream: [FrameRef(**value) for value in refs] for stream, refs in cached["index"].items()}
            check_cancel(cancel)
            self.info = SourceInfo(**info)
            self.index = index
            self._calibrations = cached["calibrations"]
            self._identity = identity
            self._indexed = True
            return True
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _save_cache(self, cancel):
        check_cancel(cancel)
        temporary = None
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.cache_dir,
                                             prefix="index-", suffix=".tmp", delete=False) as handle:
                temporary = handle.name
                json.dump(dict(identity=self._identity, info=asdict(self.info),
                               index={key: [asdict(ref) for ref in refs] for key, refs in self.index.items()},
                               calibrations=self._calibrations), handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            check_cancel(cancel)
            os.replace(temporary, self._cache_path())
        except OSError as exc:
            self.info.warnings.append(f"Index cache could not be saved: {exc}")
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def build_index(self, stream_ids=None, cancel=None, progress=None):
        check_cancel(cancel)
        if progress:
            progress(0.0)
        identity = self._source_identity()
        if (self._indexed and identity == self._identity) or self._load_cache(identity, cancel):
            if progress:
                progress(1.0)
            return self._selected_index(stream_ids)
        streams, index, active, calibrations = {}, {}, {}, {}
        warnings = [CLOCK_WARNING]
        last_progress = 0.0

        def finish(stream_id, end_offset):
            current = active.pop(stream_id)
            locator = current["locator"]
            locator["end_offset"] = end_offset
            locator["missing_packets"] = current["expected"] - len(current["packet_ids"])
            locator["partial"] = locator["missing_packets"] != 0
            refs = index[stream_id]
            refs.append(FrameRef(len(refs), current["timestamp_ns"], current["record_time_ns"], locator))

        for datagram in pcap_reader.iter_udp_datagrams(self.path, cancel=cancel):
            if progress and datagram.next_offset / max(identity["size"], 1) - last_progress >= .005:
                last_progress = min(datagram.next_offset / max(identity["size"], 1), 1.0)
                progress(last_progress)
            payload = datagram.payload
            if payload[:4] == rs_em4.DIFOP2_MAGIC:
                try:
                    calibration = rs_em4.Em4Calibration.from_difop2(payload)
                    if calibration.yaw_offsets.any():
                        warning = ("EM4 manual yaw-offset mapping is ambiguous across 26 half-zones and 13 values. "
                                   "A repeating 13-value mapping is used. Verify nonzero yaw calibration with the sensor vendor.")
                        if warning not in warnings:
                            warnings.append(warning)
                    entries = calibrations.setdefault(datagram.source_ip, [])
                    if not entries or entries[-1]["payload"] != payload.hex():
                        entries.append(dict(offset=datagram.offset, payload=payload.hex()))
                except rs_em4.Em4FormatError as exc:
                    if str(exc) not in warnings:
                        warnings.append(str(exc))
                continue
            if payload[:4] == rs_em4.MSOP_FRAGMENT_MAGIC:
                raise rs_em4.Em4CompressedError("Fragmented/compressed EM4 MSOP (55aa5a02) decoding is unsupported.")
            if payload[:4] != rs_em4.MSOP_MAGIC:
                continue
            packet = rs_em4.parse_msop(payload)
            stream_id = self._stream_id(datagram)
            streams.setdefault(stream_id, self._stream(datagram))
            index.setdefault(stream_id, [])
            previous = active.get(stream_id)
            if previous and packet.packet_index < previous["last_packet"]:
                finish(stream_id, datagram.offset)
            if stream_id not in active:
                active[stream_id] = dict(timestamp_ns=packet.timestamp_ns,
                    record_time_ns=datagram.timestamp_ns, packet_ids=set(),
                    expected=packet.columns * (4 if packet.return_mode == 0 else 2),
                    locator=dict(start_offset=datagram.offset, context=datagram.context,
                                 source_ip=datagram.source_ip, time_mode=packet.time_mode,
                                 raw_timestamp_hex=packet.raw_timestamp.hex(),
                                 return_mode=packet.return_mode))
            current = active[stream_id]
            current["last_packet"] = packet.packet_index
            current["packet_ids"].add(packet.packet_index)
            if datagram.timestamp_ns is None:
                warning = "PCAPNG simple packet has no capture timestamp. record_time_ns is absent."
                if warning not in warnings:
                    warnings.append(warning)
        check_cancel(cancel)
        for stream_id in list(active):
            finish(stream_id, identity["size"])
        if self._source_identity() != identity:
            raise pcap_reader.PcapError("Recording changed during indexing. Reopen it after recording finishes.")
        if not streams:
            warnings.append("No supported EM4 MSOP found. Decoding of other PCAP sensor protocols is unsupported.")
        for stream_id, stream in streams.items():
            refs = index[stream_id]
            stream.count = len(refs)
            capture_times = [ref.record_time_ns for ref in refs if ref.record_time_ns is not None]
            if capture_times:
                stream.start_ns = min(capture_times)
                stream.end_ns = max(capture_times)
            if stream.metadata["source_ip"] not in calibrations:
                warnings.append(NOMINAL_WARNING)
            if any(ref.locator["return_mode"] == 0 for ref in refs):
                warnings.append("Dual return data is decoded using the first echo only.")
            partial = sum(ref.locator["partial"] for ref in refs)
            if partial:
                warnings.append(f"{stream.name}: {partial} partial frames, including missing packets or capture boundaries.")
        times = [ref.record_time_ns for refs in index.values() for ref in refs
                 if ref.record_time_ns is not None]
        self.info = SourceInfo(self.path, "pcapng" if self.path.lower().endswith(".pcapng") else "pcap",
                               list(streams.values()), min(times) if times else None,
                               max(times) if times else None, identity["size"], list(dict.fromkeys(warnings)),
                               metadata=dict(protocol="RoboSense EM4" if streams else "unknown",
                                             clock_domain="capture", parser_version=PARSER_VERSION))
        self.index, self._calibrations, self._identity = index, calibrations, identity
        self._save_cache(cancel)
        self._indexed = True
        if progress:
            progress(1.0)
        return self._selected_index(stream_ids)

    def _selected_index(self, stream_ids):
        if stream_ids is None:
            return self.index
        return {key: self.index[key] for key in stream_ids if key in self.index}

    def read_frame(self, stream_id, index, cancel=None):
        check_cancel(cancel)
        if not self._indexed:
            self.build_index(cancel=cancel)
        if self._source_identity() != self._identity:
            raise pcap_reader.PcapError("Recording changed since indexing. Rebuild its index before reading.")
        if index < 0:
            raise IndexError(index)
        ref = self.index[stream_id][index]
        locator = ref.locator
        entries = self._calibrations.get(locator["source_ip"], [])
        eligible = [entry for entry in entries if entry["offset"] <= locator["start_offset"]]
        calibration_entry = eligible[-1] if eligible else (entries[0] if entries else None)
        calibration = rs_em4.Em4Calibration.from_difop2(bytes.fromhex(calibration_entry["payload"])) if calibration_entry else None
        assembler = rs_em4.Em4FrameAssembler(calibration, self.drop_noise, self.min_range_m)
        for datagram in pcap_reader.iter_udp_datagrams(self.path, cancel=cancel,
                start_offset=locator["start_offset"], stop_offset=locator["end_offset"], context=locator["context"]):
            if self._stream_id(datagram) == stream_id and datagram.payload[:4] == rs_em4.MSOP_MAGIC:
                assembler.add(datagram.payload)
        frame = assembler.flush()
        if frame is None:
            raise pcap_reader.PcapError("Indexed frame no longer contains valid EM4 points.")
        metadata = dict(protocol="RoboSense EM4", clock_domain="device", clock_relation="unverified",
                        calibration="DIFOP2" if calibration_entry else "nominal", partial=locator["partial"],
                        missing_packets=locator["missing_packets"], return_mode=locator["return_mode"],
                        time_mode=locator["time_mode"], raw_timestamp_hex=locator["raw_timestamp_hex"],
                        echo_policy="first" if locator["return_mode"] == 0 else "single",
                        source=self.path, stream_id=stream_id, frame_index=index,
                        timestamp_ns=ref.timestamp_ns, record_time_ns=ref.record_time_ns)
        cloud = CloudEntity(f"{Path(self.path).name} [{index}]", path=self.path, xyz=frame.xyz,
                            fields={"intensity": ScalarField("intensity", frame.intensity)},
                            info=json.dumps(metadata, ensure_ascii=False))
        check_cancel(cancel)
        return FrameData("pointcloud", cloud, ref.timestamp_ns, ref.record_time_ns,
                         f"{stream_id}/{index}", metadata)
