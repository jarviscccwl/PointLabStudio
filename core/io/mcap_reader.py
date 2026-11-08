"""Compatibility API for PointCloud2 MCAP import, using shared recording codecs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import numpy as np

from core.io.recording_messages import message_header, pointcloud2_to_cloud

PC2_SCHEMAS = ("sensor_msgs/msg/PointCloud2", "sensor_msgs/PointCloud2", "PointCloud2")


class McapFormatError(ValueError):
    """The MCAP is unreadable or contains no PointCloud2 stream."""


@dataclass(frozen=True)
class McapTopic:
    topic: str
    schema: str
    message_count: int = 0

    @property
    def label(self) -> str:
        return (
            f"{self.topic}  ({self.schema.rsplit('/', 1)[-1]}, {self.message_count} 帧)"
        )


@dataclass
class LidarFrame:
    xyz: np.ndarray
    intensity: np.ndarray | None
    timestamp: float
    timestamp_ns: int | None = None
    record_time_ns: int | None = None
    frame_id: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def point_count(self) -> int:
        return int(self.xyz.shape[0])


def _is_pointcloud_schema(name: str) -> bool:
    return name.rsplit("/", 1)[-1] == "PointCloud2"


def _stamp_to_seconds(stamp) -> float:
    sec = getattr(stamp, "sec", getattr(stamp, "secs", 0))
    ns = getattr(stamp, "nanosec", getattr(stamp, "nsec", getattr(stamp, "nsecs", 0)))
    return float(sec) + float(ns) * 1e-9


def list_topics(path: str) -> list[McapTopic]:
    from core.io.recording import McapSource

    try:
        source = McapSource(path)
        info = source.inspect()
        topics = [
            McapTopic(stream.name, stream.message_type, stream.count or 0)
            for stream in info.streams
            if stream.kind == "pointcloud"
        ]
    except Exception as exc:
        raise McapFormatError(f"Cannot inspect MCAP: {exc}") from exc
    if not topics:
        raise McapFormatError("该 mcap 中没有 sensor_msgs/PointCloud2 话题。")
    return sorted(topics, key=lambda topic: topic.topic)


def pointcloud2_to_frame(msg) -> LidarFrame:
    try:
        cloud = pointcloud2_to_cloud(msg)
    except ValueError as exc:
        raise McapFormatError(str(exc)) from exc
    intensity = next(
        (
            cloud.fields[key].data.astype(np.float32)
            for key in ("intensity", "i", "reflectivity")
            if key in cloud.fields
        ),
        None,
    )
    timestamp, frame_id = message_header(msg)
    return LidarFrame(cloud.xyz, intensity, (timestamp or 0) * 1e-9,
                      timestamp_ns=timestamp, frame_id=frame_id,
                      metadata={"timestamp_ns": timestamp, "frame_id": frame_id})


def iter_frames(
    path: str,
    topic: str | None = None,
    start: int = 0,
    count: int | None = None,
    cancel=None,
    progress=None,
) -> Iterator[LidarFrame]:
    from core.cancellation import check_cancel
    from core.io.recording import McapSource

    source = McapSource(path)
    try:
        info = source.inspect(cancel)
        streams = [
            stream
            for stream in info.streams
            if stream.kind == "pointcloud" and (topic is None or stream.name == topic)
        ]
        if not streams:
            raise McapFormatError("该 mcap 中没有所选 sensor_msgs/PointCloud2 话题。")
        if count is not None and count <= 0:
            return
        selected = {int(stream.id): stream for stream in streams}
        skipped = produced = 0
        with open(path, "rb") as handle:
            reader = source._make_reader(handle)
            for schema, channel, message in reader.iter_messages(
                topics=[stream.name for stream in streams],
                log_time_order=source._has_chunk_index,
            ):
                check_cancel(cancel)
                if channel.id not in selected:
                    continue
                if skipped < max(0, int(start)):
                    skipped += 1
                    continue
                data = source._decode(schema, channel, message, selected[channel.id])
                if data.kind != "pointcloud":
                    raise McapFormatError(
                        data.metadata.get(
                            "decode_error", "PointCloud2 decoding failed."
                        )
                    )
                cloud = data.value
                intensity = next(
                    (
                        cloud.fields[key].data.astype(np.float32)
                        for key in ("intensity", "i", "reflectivity")
                        if key in cloud.fields
                    ),
                    None,
                )
                produced += 1
                if progress:
                    progress(
                        produced / max(count or sum(s.count or 0 for s in streams), 1)
                    )
                metadata = dict(data.metadata)
                metadata.update(source=path, stream_id=str(channel.id), frame_id=data.frame_id)
                yield LidarFrame(cloud.xyz, intensity, (data.timestamp_ns or 0) * 1e-9,
                                 timestamp_ns=data.timestamp_ns, record_time_ns=data.record_time_ns,
                                 frame_id=data.frame_id, metadata=metadata)
                if count is not None and produced >= count:
                    return
    finally:
        source.close()
