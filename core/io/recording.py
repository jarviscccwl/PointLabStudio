"""Indexed, read-only sources for sensor recordings and ordinary data files."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
import csv
import codecs
import hashlib
import json
import os
from pathlib import Path
import tempfile

import numpy as np
from PIL import Image
import yaml

from core.cancellation import check_cancel
from core.io.recording_types import (
    RecordingSource,
    SourceInfo,
    StreamInfo,
    FrameRef,
    FrameData,
)
from core.io.recording_messages import (
    bounded_value,
    decode_message,
    has_leading_header,
    message_kind,
    serialized_header,
)
from core.io.scanner import natural_key

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif"}
SOURCE_EXTENSIONS = IMAGE_EXTENSIONS | {
    ".mcap",
    ".bag",
    ".db3",
    ".txt",
    ".csv",
    ".log",
    ".json",
    ".yaml",
    ".yml",
    ".tsv",
    ".pcap",
    ".pcapng",
}
_PARSER_VERSION = 3
_PREVIEW_BYTES = 262144
_MAX_PAGE_BYTES = 16 * 1024 * 1024


class _CsvPageLimit(Exception):
    """The next complete CSV record does not fit the current page budget."""


def _bag_metadata(path):
    metadata = Path(path) / "metadata.yaml"
    if not metadata.is_file():
        return None
    try:
        data = yaml.safe_load(metadata.read_text(encoding="utf-8"))
        info = (
            data.get("rosbag2_bagfile_information") if isinstance(data, dict) else None
        )
        if (
            isinstance(info, dict)
            and info.get("storage_identifier") in ("sqlite3", "mcap")
            and isinstance(info.get("relative_file_paths"), list)
            and info["relative_file_paths"]
        ):
            return info
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        pass
    return None


def is_data_source(path: str) -> bool:
    path = Path(path).expanduser()
    if path.is_dir():
        return bool(_bag_metadata(path) or _image_files(path))
    return path.is_file() and path.suffix.lower() in SOURCE_EXTENSIONS


def _image_files(path):
    return sorted(
        (
            file
            for file in Path(path).iterdir()
            if file.is_file() and file.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda file: natural_key(file.name),
    )


def discover_sources(paths: list[str], cancel=None) -> list[str]:
    found = {}
    bags = set()
    image_folders = set()
    for item in paths:
        check_cancel(cancel)
        path = Path(item).expanduser().resolve()
        # A split file explicitly selected together with its bag still belongs to the bag.
        if (
            path.is_file()
            and path.suffix.lower() in (".db3", ".mcap")
            and _bag_metadata(path.parent)
        ):
            path = path.parent
        if _bag_metadata(path):
            bags.add(path)
            found[os.path.normcase(str(path))] = str(path)
        elif path.is_dir():
            for root, dirs, files in os.walk(path):
                check_cancel(cancel)
                root_path = Path(root)
                if _bag_metadata(root_path):
                    bags.add(root_path)
                    found[os.path.normcase(str(root_path))] = str(root_path)
                    dirs[:] = []
                else:
                    if any(
                        Path(name).suffix.lower() in IMAGE_EXTENSIONS for name in files
                    ):
                        image_folders.add(root_path)
                        found[os.path.normcase(str(root_path))] = str(root_path)
                    for filename in files:
                        candidate = root_path / filename
                        if (
                            candidate.suffix.lower()
                            in SOURCE_EXTENSIONS - IMAGE_EXTENSIONS
                        ):
                            found[os.path.normcase(str(candidate))] = str(candidate)
        elif is_data_source(str(path)):
            found[os.path.normcase(str(path))] = str(path)
    return sorted(
        [
            value
            for value in found.values()
            if (
                Path(value) in bags
                or not any(bag in Path(value).parents for bag in bags)
            )
            and not (
                Path(value).suffix.lower() in IMAGE_EXTENSIONS
                and Path(value).parent in image_folders
            )
        ],
        key=natural_key,
    )


def _source_files(path):
    path = Path(path)
    if path.is_dir():
        metadata = _bag_metadata(path)
        if metadata:
            return [path / "metadata.yaml"] + [
                path / str(name) for name in metadata["relative_file_paths"]
            ]
        return []
    return [path]


def _size(path):
    return sum(file.stat().st_size for file in _source_files(path))


class _CachedSource(RecordingSource):
    def __init__(self, path):
        super().__init__(path)
        self._source_signature = None

    def cache_token(self):
        """Return the current disk identity for callers caching decoded frames."""
        signature = self._cache_location()[1]
        return (
            str(Path(self.path).resolve()),
            signature["parser"],
            tuple(tuple(entry) for entry in signature["files"]),
        )

    def _invalidate(self):
        self.close()
        self.info = None
        self.index = {}
        self._source_signature = None

    def _refresh_source(self, cancel=None):
        check_cancel(cancel)
        signature = self._cache_location()[1]
        if self._source_signature != signature:
            self._invalidate()
            self._source_signature = signature
        return signature

    def _verify_unchanged(self, signature, cancel=None):
        check_cancel(cancel)
        if self._cache_location()[1] != signature:
            self._invalidate()
            raise ValueError(
                "Source changed while reading. Retry to reload its metadata and index."
            )

    def _cache_location(self):
        root = Path(
            os.environ.get("POINTLAB_RECORDING_CACHE")
            or str(
                Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
                / "PointLabStudio"
                / "recording-index"
            )
        )
        identity = str(Path(self.path).resolve())
        signature = [
            [str(file), file.stat().st_size, file.stat().st_mtime_ns]
            for file in _source_files(self.path)
        ]
        key = hashlib.sha256(identity.encode()).hexdigest()
        return root / (key + ".json"), {"parser": _PARSER_VERSION, "files": signature}

    def _load_cache(self):
        try:
            path, signature = self._cache_location()
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["signature"] == signature:
                return {
                    sid: [FrameRef(**ref) for ref in refs]
                    for sid, refs in data["index"].items()
                }
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return {}

    def _save_cache(self, index, signature, cancel=None):
        temporary = None
        try:
            path = self._cache_location()[0]
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
            ) as handle:
                temporary = Path(handle.name)
                json.dump(
                    {
                        "signature": signature,
                        "index": {
                            sid: [asdict(ref) for ref in refs]
                            for sid, refs in index.items()
                        },
                    },
                    handle,
                    separators=(",", ":"),
                )
            self._verify_unchanged(signature, cancel)
            os.replace(temporary, path)
        except OSError:
            pass  # Cache failures never make read-only recordings unusable.
        finally:
            if temporary and temporary.exists():
                temporary.unlink(missing_ok=True)

    def build_index(self, stream_ids=None, cancel=None, progress=None):
        signature = self._refresh_source(cancel)
        info = self.info or self.inspect(cancel)
        selected = (
            {str(sid) for sid in stream_ids}
            if stream_ids is not None
            else {s.id for s in info.streams}
        )
        unknown = selected - {s.id for s in info.streams}
        if unknown:
            raise KeyError(f"Unknown recording streams: {sorted(unknown)}")
        if progress:
            progress(0.0)
        cached = self._load_cache()
        self._verify_unchanged(signature, cancel)
        candidate_index = dict(self.index)
        for sid, refs in cached.items():
            if sid not in candidate_index and sid in {
                stream.id for stream in info.streams
            }:
                candidate_index[sid] = refs
        missing = selected - candidate_index.keys()
        if missing:
            pending = {sid: [] for sid in missing}
            ordinals = defaultdict(int)
            total = sum(s.count or 0 for s in info.streams if s.id in missing)
            seen = 0
            for sid, timestamp, raw, encoding, locator in self._messages(missing):
                check_cancel(cancel)
                stream = next(s for s in info.streams if s.id == sid)
                header_time, _ = serialized_header(
                    raw,
                    stream.message_type,
                    encoding,
                    stream.metadata.get("has_leading_header", False),
                )
                ordinal = ordinals[(sid, timestamp)]
                ordinals[(sid, timestamp)] += 1
                refs = pending[sid]
                refs.append(
                    FrameRef(
                        len(refs),
                        header_time,
                        int(timestamp),
                        {"ordinal": ordinal, **locator},
                    )
                )
                seen += 1
                if progress and seen % 128 == 0:
                    progress(min(0.99, seen / max(total, 1)))
            candidate_index.update(pending)
            self._verify_unchanged(signature, cancel)
            self._save_cache(candidate_index, signature, cancel)
        self._verify_unchanged(signature, cancel)
        self.index = candidate_index
        if progress:
            progress(1.0)
        return {sid: self.index[sid] for sid in selected}

    def _reference(self, stream_id, index, cancel):
        self._refresh_source(cancel)
        if stream_id not in self.index:
            self.build_index([stream_id], cancel)
        if index < 0 or index >= len(self.index[stream_id]):
            raise IndexError(f"Frame {index} is outside stream {stream_id}.")
        return self.index[stream_id][index]


def _raw_frame(raw, stream, record_time, error, encoding="", **metadata):
    timestamp, frame_id = serialized_header(
        raw,
        stream.message_type,
        encoding,
        stream.metadata.get("has_leading_header", False),
    )
    return FrameData(
        "message",
        {"byte_length": len(raw), "preview_hex": bytes(raw[:64]).hex()},
        timestamp_ns=timestamp,
        record_time_ns=int(record_time),
        frame_id=frame_id,
        metadata={
            "message_type": stream.message_type,
            "decode_error": str(error),
            "raw_payload": True,
            **metadata,
        },
    )


class McapSource(_CachedSource):
    def __init__(self, path):
        super().__init__(path)
        self._channels = {}
        self._schemas = {}
        self._factories = None
        self._has_chunk_index = False

    def inspect(self, cancel=None):
        signature = self._refresh_source(cancel)
        from mcap.reader import make_reader

        self._channels = {}
        self._schemas = {}
        self._factories = None
        with open(self.path, "rb") as handle:
            reader = make_reader(handle)
            summary = reader.get_summary()
            self._has_chunk_index = bool(summary and summary.chunk_indexes)
            stats = summary.statistics if summary else None
            if summary and stats:
                self._channels = dict(summary.channels)
                self._schemas = dict(summary.schemas)
                if not self._definitions_complete(stats):
                    self._scan_metadata(handle, stats, cancel)
                counts = stats.channel_message_counts
                start, end = stats.message_start_time, stats.message_end_time
            else:
                counts, start, end = self._scan_metadata(handle, cancel=cancel)
            streams = []
            for channel in self._channels.values():
                schema = self._schemas.get(channel.schema_id)
                name = schema.name if schema else ""
                streams.append(
                    StreamInfo(
                        str(channel.id),
                        channel.topic,
                        message_kind(name),
                        name,
                        counts.get(channel.id, 0),
                        metadata={
                            "message_encoding": channel.message_encoding,
                            "schema_encoding": schema.encoding if schema else "",
                            "has_leading_header": (
                                has_leading_header(schema.data) if schema else False
                            ),
                            **channel.metadata,
                        },
                    )
                )
        self._verify_unchanged(signature, cancel)
        self.info = SourceInfo(self.path, "MCAP", streams, start, end, _size(self.path))
        return self.info

    def _definitions_complete(self, stats):
        return (
            len(self._channels) >= stats.channel_count
            and len(self._schemas) >= stats.schema_count
            and all(
                channel.schema_id == 0 or channel.schema_id in self._schemas
                for channel in self._channels.values()
            )
        )

    def _scan_metadata(self, handle, stats=None, cancel=None):
        from mcap.records import Channel, DataEnd, Message, Schema
        from mcap.stream_reader import StreamReader

        counts = defaultdict(int)
        start = end = None
        handle.seek(0)
        for record in StreamReader(handle).records:
            check_cancel(cancel)
            if isinstance(record, Channel):
                self._channels[record.id] = record
            elif isinstance(record, Schema):
                self._schemas[record.id] = record
            elif isinstance(record, Message) and stats is None:
                counts[record.channel_id] += 1
                start = (
                    record.log_time if start is None else min(start, record.log_time)
                )
                end = record.log_time if end is None else max(end, record.log_time)
            elif isinstance(record, DataEnd):
                break
            if stats is not None and self._definitions_complete(stats):
                break
        if stats is not None and not self._definitions_complete(stats):
            raise ValueError(
                "MCAP data section is missing channel or schema definitions."
            )
        return counts, start, end

    def _make_reader(self, handle):
        """Recover optional summary definitions while retaining native chunk seeking."""
        from mcap.reader import make_reader

        reader = make_reader(handle)
        summary = reader.get_summary()
        if summary is not None:
            summary.channels.update(self._channels)
            summary.schemas.update(self._schemas)
        return reader

    def _messages(self, selected):
        topics = list({self._channels[int(sid)].topic for sid in selected})
        with open(self.path, "rb") as handle:
            reader = self._make_reader(handle)
            for schema, channel, message in reader.iter_messages(
                topics=topics, log_time_order=self._has_chunk_index
            ):
                if str(channel.id) in selected:
                    locator = {
                        "sequence": message.sequence,
                        "publish_time_ns": message.publish_time,
                    }
                    yield (
                        str(channel.id),
                        message.log_time,
                        message.data,
                        channel.message_encoding,
                        locator,
                    )

    def _decode(self, schema, channel, message, stream):
        raw = message.data
        try:
            if channel.message_encoding == "json":
                if len(raw) > _PREVIEW_BYTES:
                    raise ValueError(
                        "JSON message exceeds the 256 KiB structured preview limit."
                    )
                return FrameData(
                    "message",
                    bounded_value(json.loads(raw)),
                    record_time_ns=message.log_time,
                    metadata={
                        "message_type": stream.message_type,
                        "publish_time_ns": message.publish_time,
                    },
                )
            if self._factories is None:
                from mcap_ros1.decoder import DecoderFactory as Ros1Factory
                from mcap_ros2.decoder import DecoderFactory as Ros2Factory

                self._factories = [Ros2Factory(), Ros1Factory()]
            decoder = None
            for factory in self._factories:
                decoder = factory.decoder_for(channel.message_encoding, schema)
                if decoder is not None:
                    break
            if decoder is None:
                raise ValueError(
                    f'Unsupported MCAP schema/message encoding {schema.encoding if schema else "none"}/{channel.message_encoding}.'
                )
            result = decode_message(
                decoder(raw), stream.message_type, message.log_time, name=stream.name
            )
            result.metadata["publish_time_ns"] = message.publish_time
            return result
        except Exception as exc:
            return _raw_frame(
                raw,
                stream,
                message.log_time,
                exc,
                encoding=channel.message_encoding,
                publish_time_ns=message.publish_time,
            )

    def read_frame(self, stream_id, index, cancel=None):
        ref = self._reference(str(stream_id), index, cancel)
        signature = self._source_signature
        channel = self._channels[int(stream_id)]
        stream = next(s for s in self.info.streams if s.id == str(stream_id))
        ordinal = 0
        with open(self.path, "rb") as handle:
            reader = self._make_reader(handle)
            # SeekingReader consults chunk and message time indexes for this one-ns window.
            for schema, candidate, message in reader.iter_messages(
                topics=[channel.topic],
                starting_at=ref.record_time_ns,
                ending_before=ref.record_time_ns + 1,
                log_time_order=self._has_chunk_index,
            ):
                check_cancel(cancel)
                if candidate.id != channel.id:
                    continue
                if ordinal == ref.locator["ordinal"]:
                    result = self._decode(schema, candidate, message, stream)
                    self._verify_unchanged(signature, cancel)
                    return result
                ordinal += 1
        raise ValueError(
            "Indexed MCAP frame could not be found. The source may have changed."
        )


class RosbagSource(_CachedSource):
    def __init__(self, path):
        super().__init__(path)
        self._is_ros1 = Path(path).is_file() and Path(path).suffix.lower() == ".bag"
        self._encoding = "ros1" if self._is_ros1 else "cdr"
        self._reader = None
        self._typestore = None
        self._connections = {}

    def _open(self):
        if self._reader is None:
            if self._is_ros1:
                from rosbags.rosbag1 import Reader
            else:
                from rosbags.rosbag2 import Reader
            reader = Reader(Path(self.path))
            reader.open()
            self._reader = reader
            self._connections = {
                str(connection.id): connection for connection in reader.connections
            }
        return self._reader

    def inspect(self, cancel=None):
        signature = self._refresh_source(cancel)
        reader = self._open()
        streams = [
            StreamInfo(
                str(connection.id),
                connection.topic,
                message_kind(connection.msgtype),
                connection.msgtype,
                connection.msgcount,
                metadata={
                    "message_encoding": self._encoding,
                    "has_leading_header": has_leading_header(connection.msgdef.data),
                },
            )
            for connection in reader.connections
        ]
        self._verify_unchanged(signature, cancel)
        self.info = SourceInfo(
            self.path,
            "ROS1 BAG" if self._is_ros1 else "ROS2 bag",
            streams,
            reader.start_time if reader.message_count else None,
            reader.end_time - 1 if reader.message_count else None,
            _size(self.path),
        )
        return self.info

    def _messages(self, selected):
        reader = self._open()
        for connection, timestamp, raw in reader.messages(
            connections=[self._connections[sid] for sid in selected]
        ):
            yield str(connection.id), timestamp, raw, self._encoding, {}

    def _deserialize(self, raw, connection):
        if self._typestore is None:
            from rosbags.interfaces import MessageDefinitionFormat
            from rosbags.typesys import (
                Stores,
                get_types_from_idl,
                get_types_from_msg,
                get_typestore,
            )

            store = Stores.ROS1_NOETIC if self._is_ros1 else Stores.ROS2_HUMBLE
            typestore = get_typestore(store)
            definitions = {}
            for candidate in self._open().connections:
                definition = candidate.msgdef
                if definition.format == MessageDefinitionFormat.MSG:
                    definitions.update(
                        get_types_from_msg(definition.data, candidate.msgtype)
                    )
                elif definition.format == MessageDefinitionFormat.IDL:
                    separator = "=" * 80 + "\n"
                    if definition.data.startswith(separator + "IDL: "):
                        for part in definition.data.split(separator)[1:]:
                            _, idl = part.split("\n", 1)
                            definitions.update(get_types_from_idl(idl))
                    else:
                        definitions.update(get_types_from_idl(definition.data))
            typestore.register(definitions)
            self._typestore = typestore
        if self._is_ros1:
            return self._typestore.deserialize_ros1(raw, connection.msgtype)
        if connection.ext.serialization_format != "cdr":
            raise ValueError(
                f"Unsupported ROS2 serialization {connection.ext.serialization_format}."
            )
        return self._typestore.deserialize_cdr(raw, connection.msgtype)

    def read_frame(self, stream_id, index, cancel=None):
        ref = self._reference(str(stream_id), index, cancel)
        signature = self._source_signature
        reader = self._open()
        connection = self._connections[str(stream_id)]
        stream = next(
            stream for stream in self.info.streams if stream.id == str(stream_id)
        )
        messages = reader.messages(
            connections=[connection],
            start=ref.record_time_ns,
            stop=ref.record_time_ns + 1,
        )
        for ordinal, (_, timestamp, raw) in enumerate(messages):
            check_cancel(cancel)
            if ordinal != ref.locator["ordinal"]:
                continue
            try:
                message = self._deserialize(raw, connection)
                result = decode_message(
                    message, connection.msgtype, timestamp, name=connection.topic
                )
            except Exception as exc:
                result = _raw_frame(
                    raw, stream, timestamp, exc, encoding=self._encoding
                )
            self._verify_unchanged(signature, cancel)
            return result
        raise ValueError(
            "Indexed ROS bag frame could not be found. The source may have changed."
        )

    def close(self):
        self._typestore = None
        self._connections = {}
        if self._reader is not None:
            self._reader.close()
            self._reader = None


class FileSource(RecordingSource):
    def __init__(self, path):
        super().__init__(path)
        self._file_signature = None
        self._table_header = None
        self._table_start = 0

    def _identity(self):
        stat = Path(self.path).stat()
        return stat.st_size, stat.st_mtime_ns

    def inspect(self, cancel=None):
        check_cancel(cancel)
        self._file_signature = self._identity()
        self._table_header = None
        suffix = Path(self.path).suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            kind = "image"
        elif suffix in (".csv", ".tsv"):
            kind = "table"
        elif suffix in (".json", ".yaml", ".yml"):
            kind = "message"
        else:
            kind = "text"
        metadata = {}
        if kind == "image":
            with Image.open(self.path) as image:
                metadata = {
                    "width": image.width,
                    "height": image.height,
                    "mode": image.mode,
                    "frame_count": getattr(image, "n_frames", 1),
                }
        self.info = SourceInfo(
            self.path,
            "File",
            [StreamInfo("0", Path(self.path).name, kind, count=1, metadata=metadata)],
            size_bytes=self._file_signature[0],
        )
        return self.info

    def build_index(self, stream_ids=None, cancel=None, progress=None):
        check_cancel(cancel)
        self.info = self.info or self.inspect(cancel)
        if stream_ids is not None and any(str(sid) != "0" for sid in stream_ids):
            raise KeyError("File sources have one stream, 0.")
        if progress:
            progress(0.0)
        self.index = {"0": [FrameRef(0, None, None)]}
        if progress:
            progress(1.0)
        return self.index

    def read_frame(self, stream_id, index, cancel=None):
        check_cancel(cancel)
        if str(stream_id) != "0" or index != 0:
            raise IndexError("File sources have one preview frame.")
        info = self.info or self.inspect(cancel)
        if info.streams[0].kind != "image":
            return self.read_page(cancel=cancel)
        metadata = dict(info.streams[0].metadata)
        with Image.open(self.path) as image:
            image.load()
            value = np.array(image)
            metadata.update(original_mode=image.mode, original_dtype=str(value.dtype))
            if value.ndim == 2 and value.dtype.itemsize > 1:
                metadata["display_scaling"] = "finite 1–99 percentile in viewer"
            elif image.mode not in ("L", "RGB", "RGBA"):
                image = image.convert("RGB")
                value = np.array(image)
            metadata["shape"] = list(value.shape)
        return FrameData("image", value, metadata=metadata)

    def _csv_record(self, handle, byte_limit, state, cancel):
        start = handle.tell()

        def lines():
            while True:
                check_cancel(cancel)
                remaining = byte_limit - (handle.tell() - start)
                if remaining <= 0:
                    if handle.peek(1):
                        raise _CsvPageLimit()
                    return
                line_start = handle.tell()
                raw = handle.readline(remaining + 1)
                if not raw:
                    return
                if len(raw) > remaining:
                    raise _CsvPageLimit()
                encoding = "utf-8-sig" if line_start == 0 else "utf-8"
                try:
                    yield raw.decode(encoding)
                except UnicodeDecodeError:
                    state["encoding_replaced"] = True
                    yield raw.decode(encoding, errors="replace")

        delimiter = "\t" if Path(self.path).suffix.lower() == ".tsv" else ","
        try:
            return next(csv.reader(lines(), delimiter=delimiter, strict=True), None)
        except csv.Error as exc:
            raise ValueError(f"Cannot read complete CSV record: {exc}") from exc

    def _table_page(self, handle, offset, row_limit, byte_limit, state, cancel):
        if self._table_header is None:
            handle.seek(0)
            try:
                self._table_header = (
                    self._csv_record(handle, byte_limit, state, cancel) or []
                )
            except _CsvPageLimit as exc:
                raise ValueError(
                    "CSV header exceeds byte_limit. Increase the page byte_limit."
                ) from exc
            self._table_start = handle.tell()
        handle.seek(self._table_start if offset == 0 else offset)
        start = handle.tell()
        rows = []
        while len(rows) < row_limit and handle.tell() - start < byte_limit:
            record_start = handle.tell()
            try:
                record = self._csv_record(
                    handle, byte_limit - (record_start - start), state, cancel
                )
            except _CsvPageLimit as exc:
                handle.seek(record_start)
                if rows:
                    break
                raise ValueError(
                    "CSV record exceeds byte_limit. Increase the page byte_limit."
                ) from exc
            if record is None:
                break
            rows.append(record)
        return {"columns": list(self._table_header), "rows": rows}

    def read_page(
        self, offset=0, *, row_limit=1000, byte_limit=_PREVIEW_BYTES, cancel=None
    ):
        """Read one bounded UTF-8 text/table page using the returned byte cursor."""
        check_cancel(cancel)
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("Page offset must be a nonnegative byte cursor.")
        if not isinstance(row_limit, int) or not 1 <= row_limit <= 10000:
            raise ValueError("row_limit must be between 1 and 10000.")
        if not isinstance(byte_limit, int) or not 4 <= byte_limit <= _MAX_PAGE_BYTES:
            raise ValueError("byte_limit must be between 4 bytes and 16 MiB.")
        signature = self._identity()
        if (
            self._file_signature is not None
            and signature != self._file_signature
            and offset != 0
        ):
            raise ValueError("Source changed. Restart paging at offset zero.")
        if self.info is None or signature != self._file_signature:
            self.inspect(cancel)
        kind = self.info.streams[0].kind
        if kind == "image":
            raise ValueError("Image files do not provide text/table pages.")
        size = signature[0]
        if offset > size:
            raise ValueError("Page offset is beyond the end of the file.")
        metadata = {
            "offset": offset,
            "preview_bytes": byte_limit,
            "source_bytes": size,
            "encoding": "utf-8",
            "encoding_errors": "replace",
            "encoding_limitations": "UTF-8 with optional BOM is supported. UTF-16/GB18030 require conversion.",
        }
        state = {"encoding_replaced": False}
        with open(self.path, "rb") as handle:
            if kind == "table":
                value = self._table_page(
                    handle, offset, row_limit, byte_limit, state, cancel
                )
                cursor = handle.tell()
            else:
                handle.seek(offset)
                raw = handle.read(byte_limit)
                decoder_type = codecs.getincrementaldecoder(
                    "utf-8-sig" if offset == 0 else "utf-8"
                )
                decoder = decoder_type(errors="strict")
                try:
                    text = decoder.decode(raw, final=handle.tell() == size)
                except UnicodeDecodeError:
                    decoder = decoder_type(errors="replace")
                    text = decoder.decode(raw, final=handle.tell() == size)
                    state["encoding_replaced"] = True
                pending_bytes = decoder.getstate()[0]
                cursor = offset + len(raw) - len(pending_bytes)
                value = text
                if kind == "message" and offset == 0 and cursor == size:
                    try:
                        parsed = (
                            json.loads(text)
                            if Path(self.path).suffix.lower() == ".json"
                            else yaml.safe_load(text)
                        )
                        value = bounded_value(parsed)
                    except (ValueError, TypeError, yaml.YAMLError) as exc:
                        kind = "text"
                        metadata["parse_error"] = str(exc)
                else:
                    kind = "text"
        check_cancel(cancel)
        if self._identity() != signature:
            self.info = None
            self._table_header = None
            raise ValueError(
                "Source changed while reading the page. Restart at offset zero."
            )
        next_offset = cursor if cursor < size else None
        metadata.update(next_offset=next_offset, truncated=next_offset is not None)
        if state["encoding_replaced"]:
            metadata["encoding_warning"] = (
                "Invalid UTF-8 bytes were replaced. UTF-16/GB18030 are unsupported."
            )
        if kind == "table":
            value["truncated"] = metadata["truncated"]
        return FrameData(kind, value, metadata=metadata)


class ImageSequenceSource(RecordingSource):
    """The direct images of a folder form one naturally ordered, lazy sequence."""

    def __init__(self, path):
        super().__init__(path)
        self._files = []

    def cache_token(self):
        """Track directory membership while the caller tracks each selected file."""
        stat = os.stat(self.path)
        return (
            os.path.abspath(self.path),
            stat.st_size,
            stat.st_mtime_ns,
        )

    def inspect(self, cancel=None):
        check_cancel(cancel)
        self._files = _image_files(self.path)
        stream = StreamInfo(
            "0",
            Path(self.path).name,
            "image",
            count=len(self._files),
            metadata={"ordering": "natural filename order"},
        )
        self.info = SourceInfo(
            self.path,
            "Image sequence",
            [stream],
            size_bytes=sum(file.stat().st_size for file in self._files),
        )
        return self.info

    def build_index(self, stream_ids=None, cancel=None, progress=None):
        check_cancel(cancel)
        self.info = self.info or self.inspect(cancel)
        if stream_ids is not None and any(str(sid) != "0" for sid in stream_ids):
            raise KeyError("Image sequences have one stream, 0.")
        if progress:
            progress(0.0)
        refs = []
        for index, file in enumerate(self._files):
            check_cancel(cancel)
            refs.append(FrameRef(index, None, None, str(file)))
        self.index = {"0": refs}
        if progress:
            progress(1.0)
        return self.index

    def read_frame(self, stream_id, index, cancel=None):
        check_cancel(cancel)
        if str(stream_id) not in self.index:
            self.build_index([stream_id], cancel)
        if index < 0 or index >= len(self.index[str(stream_id)]):
            raise IndexError("Image sequence frame index is out of range.")
        path = self.index[str(stream_id)][index].locator
        result = FileSource(path).read_frame("0", 0, cancel)
        result.metadata.update(
            filename=Path(path).name, path=path, sequence_index=index
        )
        return result


def open_source(path: str) -> RecordingSource:
    path = str(Path(path).expanduser().resolve())
    if not is_data_source(path):
        raise ValueError(f"Unsupported recording or file source: {path}")
    suffix = Path(path).suffix.lower()
    if suffix in (".pcap", ".pcapng"):
        from core.io.pcap_source import PcapSource

        return PcapSource(path)
    if Path(path).is_dir():
        return RosbagSource(path) if _bag_metadata(path) else ImageSequenceSource(path)
    if suffix in (".bag", ".db3"):
        return RosbagSource(path)
    if suffix == ".mcap":
        return McapSource(path)
    return FileSource(path)
