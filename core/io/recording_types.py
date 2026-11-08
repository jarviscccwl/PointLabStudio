"""Shared, UI-independent contracts for indexed sensor recordings."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import os


@dataclass
class StreamInfo:
    id: str
    name: str
    kind: str
    message_type: str = ""
    count: int | None = None
    start_ns: int | None = None
    end_ns: int | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class SourceInfo:
    path: str
    format: str
    streams: list[StreamInfo] = field(default_factory=list)
    start_ns: int | None = None
    end_ns: int | None = None
    size_bytes: int = 0
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FrameRef:
    index: int
    timestamp_ns: int | None
    record_time_ns: int | None
    locator: Any = None


@dataclass
class FrameData:
    kind: str
    value: Any
    timestamp_ns: int | None = None
    record_time_ns: int | None = None
    frame_id: str = ""
    metadata: dict = field(default_factory=dict)


class RecordingSource:
    """A source keeps lightweight frame references, never all decoded payloads."""

    def __init__(self, path: str):
        self.path = path
        self.info: SourceInfo | None = None
        self.index: dict[str, list[FrameRef]] = {}

    def inspect(self, cancel=None) -> SourceInfo:
        raise NotImplementedError

    def build_index(self, stream_ids=None, cancel=None, progress=None):
        raise NotImplementedError

    def read_frame(self, stream_id: str, index: int, cancel=None) -> FrameData:
        raise NotImplementedError

    def cache_token(self):
        """Cheap identity used by bounded decoded-frame caches."""
        try:
            stat = os.stat(self.path)
            return stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def close(self):
        """Readers may override this if they retain an open file handle."""
