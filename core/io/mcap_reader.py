"""MCAP 容器读取：把 sensor_msgs/PointCloud2 转成逐帧点云。

依赖 `mcap` + `mcap-ros2-support`（见 requirements.txt）。只读取点到点云所需的字段：
x / y / z / intensity（若存在），忽略 TF、图像等其它话题。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterator

import numpy as np

PC2_SCHEMAS = (
    "sensor_msgs/msg/PointCloud2",
    "sensor_msgs/PointCloud2",
    "PointCloud2",
)

# PointCloud2 的 PointField.datatype
_DATATYPES = {
    1: np.int8,
    2: np.uint8,
    3: np.int16,
    4: np.uint16,
    5: np.int32,
    6: np.uint32,
    7: np.float32,
    8: np.float64,
}


class McapFormatError(ValueError):
    """不是可读取的 MCAP，或文件里没有 PointCloud2。"""


@dataclass(frozen=True)
class McapTopic:
    topic: str
    schema: str
    message_count: int = 0

    @property
    def label(self) -> str:
        return f"{self.topic}  ({self.schema.rsplit('/', 1)[-1]}, {self.message_count} 帧)"


@dataclass
class LidarFrame:
    xyz: np.ndarray  # (N, 3) float32，单位米
    intensity: np.ndarray | None  # (N,) float32
    timestamp: float  # 秒

    @property
    def point_count(self) -> int:
        return int(self.xyz.shape[0])


def _is_pointcloud_schema(name: str) -> bool:
    return name in PC2_SCHEMAS or name.rsplit("/", 1)[-1] == "PointCloud2"


def _require_mcap():
    import importlib.util

    missing = [name for name in ("mcap", "mcap_ros2") if importlib.util.find_spec(name) is None]
    if missing:  # pragma: no cover - 依赖未安装时的提示
        raise McapFormatError(
            "读取 .mcap 需要 mcap 与 mcap-ros2-support："
            "pip install mcap mcap-ros2-support lz4 zstandard"
        )


def list_topics(path: str) -> list[McapTopic]:
    """列出文件里所有 PointCloud2 话题；没有时抛 McapFormatError。"""
    _require_mcap()
    from mcap.reader import make_reader

    path = os.path.abspath(os.path.expanduser(path))
    counts: dict[str, int] = {}
    topics: dict[str, McapTopic] = {}
    with open(path, "rb") as handle:
        reader = make_reader(handle)
        summary = reader.get_summary()
        if summary is not None:
            schemas = {sid: rec.name for sid, rec in summary.schemas.items()}
            for channel in summary.channels.values():
                name = schemas.get(channel.schema_id, "")
                if _is_pointcloud_schema(name):
                    stats = summary.statistics
                    count = 0
                    if stats is not None:
                        count = stats.channel_message_counts.get(channel.id, 0)
                    topics[channel.topic] = McapTopic(channel.topic, name, count)
        if not topics:
            handle.seek(0)
            reader = make_reader(handle)
            schemas: dict[int, str] = {}
            for schema, channel, _message in reader.iter_messages():
                if schema is not None:
                    schemas[schema.id] = schema.name
                name = schemas.get(channel.schema_id, "")
                if _is_pointcloud_schema(name):
                    key = channel.topic
                    counts[key] = counts.get(key, 0) + 1
                    topics[key] = McapTopic(key, name, counts[key])
    if not topics:
        raise McapFormatError("该 mcap 中没有 sensor_msgs/PointCloud2 话题。")
    return sorted(topics.values(), key=lambda t: t.topic)


def _stamp_to_seconds(stamp) -> float:
    sec = getattr(stamp, "sec", None)
    if sec is None:
        return 0.0
    nsec = getattr(stamp, "nanosec", getattr(stamp, "nsec", 0)) or 0
    return float(sec) + float(nsec) * 1e-9


def _pointcloud_dtype(fields, point_step: int) -> np.dtype:
    names, formats, offsets = [], [], []
    for field in fields:
        name = field.name
        dtype = _DATATYPES.get(int(field.datatype))
        if dtype is None or name in names:
            continue
        count = max(int(field.count or 1), 1)
        names.append(name)
        formats.append(np.dtype(dtype) if count == 1 else np.dtype((dtype, (count,))))
        offsets.append(int(field.offset))
    if "x" not in names or "y" not in names or "z" not in names:
        raise McapFormatError("PointCloud2 缺少 x/y/z 字段。")
    return np.dtype({"names": names, "formats": formats, "offsets": offsets,
                     "itemsize": int(point_step)})


def pointcloud2_to_frame(msg) -> LidarFrame:
    """PointCloud2 消息对象 → LidarFrame。"""
    width = int(getattr(msg, "width", 0))
    height = int(getattr(msg, "height", 1))
    count = width * height
    point_step = int(getattr(msg, "point_step", 0))
    data = getattr(msg, "data", b"")
    if isinstance(data, np.ndarray):
        raw = np.ascontiguousarray(data, dtype=np.uint8).tobytes()
    elif isinstance(data, (bytes, bytearray, memoryview)):
        raw = bytes(data)
    else:  # mcap 的 ros2 解码器可能把 uint8[] 解成 list
        raw = bytes(bytearray(data))
    if count <= 0 or point_step <= 0:
        raise McapFormatError("PointCloud2 为空（width/height 为 0）。")
    need = count * point_step
    if len(raw) < need:
        raise McapFormatError(f"PointCloud2 数据不完整：{len(raw)} < {need} 字节。")
    dtype = _pointcloud_dtype(msg.fields, point_step)
    if bool(getattr(msg, "is_bigendian", False)):
        dtype = dtype.newbyteorder(">")
    points = np.frombuffer(raw, dtype=dtype, count=count)
    xyz = np.empty((count, 3), dtype=np.float32)
    try:
        xyz[:, 0] = points["x"]
        xyz[:, 1] = points["y"]
        xyz[:, 2] = points["z"]
    except (ValueError, TypeError) as exc:
        raise McapFormatError(f"PointCloud2 坐标字段类型异常：{exc}") from exc
    intensity = None
    for key in ("intensity", "i", "reflectivity"):
        if key in dtype.names:
            intensity = np.asarray(points[key], dtype=np.float32).reshape(-1)
            break
    valid = np.isfinite(xyz).all(axis=1)
    if intensity is not None:
        intensity = intensity[valid]
    return LidarFrame(xyz=np.ascontiguousarray(xyz[valid]),
                      intensity=intensity,
                      timestamp=_stamp_to_seconds(getattr(msg.header, "stamp", None)))


def iter_frames(path: str, topic: str | None = None, start: int = 0,
                count: int | None = None, cancel=None,
                progress=None) -> Iterator[LidarFrame]:
    """按消息顺序产出 PointCloud2 帧；start/count 以帧为单位。"""
    _require_mcap()
    from mcap_ros2.reader import read_ros2_messages

    start = max(int(start), 0)
    selected = 0
    produced = 0
    for message in read_ros2_messages(path, topics=topic, log_time_order=True):
        if cancel is not None:
            from core.cancellation import check_cancel
            check_cancel(cancel)
        ros_msg = message.ros_msg
        if not hasattr(ros_msg, "point_step") or not hasattr(ros_msg, "fields"):
            continue
        selected += 1
        if selected <= start:
            continue
        frame = pointcloud2_to_frame(ros_msg)
        if progress is not None:
            progress(0.0)
        produced += 1
        yield frame
        if count is not None and produced >= count:
            return
