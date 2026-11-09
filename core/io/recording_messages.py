"""Qt-free message codecs shared by recording adapters and legacy MCAP import."""

from __future__ import annotations

import dataclasses
import io
import struct
from collections.abc import Mapping

import numpy as np
from PIL import Image

from core.io.recording_types import FrameData
from core.model.entities import CloudEntity, ScalarField

_FIELD_TYPES = {1: "i1", 2: "u1", 3: "i2", 4: "u2", 5: "i4", 6: "u4", 7: "f4", 8: "f8"}
_KINDS = {
    "PointCloud2": "pointcloud",
    "Image": "image",
    "CompressedImage": "image",
    "CameraInfo": "camera_info",
    "Imu": "imu",
}


def message_kind(message_type: str) -> str:
    return _KINDS.get(message_type.rsplit("/", 1)[-1], "message")


def bounded_value(value, max_items=64, max_string=2048):
    """Convert messages to plain values with a shared node and string budget."""
    budget = [max_items, max_string * 3]

    def convert(item, depth=0):
        if budget[0] <= 0 or depth > 6:
            return "<truncated>"
        budget[0] -= 1
        if item is None or isinstance(item, (bool, int, float)):
            return item
        if isinstance(item, np.generic):
            return item.item()
        if isinstance(item, str):
            limit = min(max_string, max(0, budget[1]))
            budget[1] -= min(len(item), limit)
            return item[:limit] + ("…" if len(item) > limit else "")
        if isinstance(item, (bytes, bytearray, memoryview)):
            return {"byte_length": len(item), "preview_hex": bytes(item[:32]).hex()}
        if isinstance(item, np.ndarray):
            return {
                "shape": list(item.shape),
                "dtype": str(item.dtype),
                "preview": convert(item.reshape(-1)[:16].tolist(), depth + 1),
            }
        if isinstance(item, Mapping):
            result = {}
            for key, val in item.items():
                if budget[0] <= 0:
                    result["…"] = "<truncated>"
                    break
                result[str(key)[:80]] = convert(val, depth + 1)
            return result
        if isinstance(item, (list, tuple)):
            result = [
                convert(val, depth + 1) for val in item[: min(32, max(0, budget[0]))]
            ]
            if len(result) < len(item):
                result.append(f"<{len(item)-len(result)} more items>")
            return result
        if dataclasses.is_dataclass(item):
            names = [field.name for field in dataclasses.fields(item)]
        elif hasattr(item, "__slots__"):
            names = item.__slots__
        elif hasattr(item, "__dict__"):
            names = vars(item)
        else:
            return convert(str(item), depth + 1)
        return convert(
            {
                name: getattr(item, name)
                for name in names
                if not name.startswith("_") and hasattr(item, name)
            },
            depth + 1,
        )

    return convert(value)


def message_header(msg):
    header = getattr(msg, "header", None)
    stamp = getattr(header, "stamp", None)
    sec = getattr(stamp, "sec", getattr(stamp, "secs", None))
    ns = getattr(stamp, "nanosec", getattr(stamp, "nsec", getattr(stamp, "nsecs", 0)))
    timestamp = None if sec is None else int(sec) * 1_000_000_000 + int(ns)
    return timestamp, str(getattr(header, "frame_id", ""))


def has_leading_header(definition):
    """Recognize a first serialized Header field in a ROS .msg definition."""
    if isinstance(definition, bytes):
        definition = definition.decode("utf-8", errors="replace")
    for line in definition.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" in line:
            continue
        fields = line.split()
        return (
            len(fields) == 2
            and fields[0] in ("std_msgs/Header", "std_msgs/msg/Header", "Header")
            and fields[1] == "header"
        )
    return False


def serialized_header(data, message_type, encoding, has_header=False):
    """Read only a standard sensor message's leading Header, without its payload."""
    if message_kind(message_type) == "message" and not has_header:
        return None, ""
    raw = memoryview(data)
    try:
        if encoding == "cdr":
            endian = "<" if raw[1] & 1 else ">"
            offset = 4
        elif encoding == "ros1":
            endian, offset = "<", 4  # ROS1 Header begins with sequence.
        else:
            return None, ""
        seconds_type = "I" if encoding == "ros1" else "i"
        sec, ns, length = struct.unpack_from(endian + seconds_type + "II", raw, offset)
        if ns >= 1_000_000_000 or length > len(raw) - offset - 12:
            return None, ""
        text = (
            bytes(raw[offset + 12 : offset + 12 + length])
            .rstrip(b"\0")
            .decode("utf-8", errors="replace")
        )
        return sec * 1_000_000_000 + ns, text
    except (IndexError, ValueError, struct.error):
        return None, ""


def pointcloud2_to_cloud(msg, name="PointCloud2"):
    width, height = int(msg.width), int(msg.height)
    point_step = int(msg.point_step)
    row_step = int(getattr(msg, "row_step", 0)) or width * point_step
    if width <= 0 or height <= 0 or point_step <= 0 or row_step < width * point_step:
        raise ValueError("PointCloud2 has invalid dimensions or row/point strides.")
    raw = bytes(msg.data)
    required = (height - 1) * row_step + width * point_step
    if len(raw) < required:
        raise ValueError(
            f"PointCloud2 payload is truncated ({len(raw)} < {required} bytes)."
        )
    names, formats, offsets = [], [], []
    endian = ">" if msg.is_bigendian else "<"
    for field in msg.fields:
        if int(field.datatype) not in _FIELD_TYPES or field.name in names:
            continue
        dtype = np.dtype(endian + _FIELD_TYPES[int(field.datatype)])
        count = max(int(field.count), 1)
        if (
            int(field.offset) < 0
            or int(field.offset) + dtype.itemsize * count > point_step
        ):
            raise ValueError(f"PointCloud2 field {field.name} exceeds point_step.")
        names.append(field.name)
        formats.append(dtype if count == 1 else (dtype, (count,)))
        offsets.append(int(field.offset))
    if not {"x", "y", "z"}.issubset(names):
        raise ValueError("PointCloud2 is missing x/y/z fields.")
    dtype = np.dtype(
        dict(names=names, formats=formats, offsets=offsets, itemsize=point_step)
    )
    points = np.ndarray(
        (height, width), dtype=dtype, buffer=raw, strides=(row_step, point_step)
    ).reshape(-1)
    xyz = np.column_stack([points[key] for key in ("x", "y", "z")]).astype(np.float32)
    valid = np.isfinite(xyz).all(axis=1)
    fields = {}
    for key in names:
        if key in ("x", "y", "z"):
            continue
        values = np.asarray(points[key])
        if key in ("rgb", "rgba") and values.dtype.itemsize == 4 and values.ndim == 1:
            packed = values.view(np.dtype(endian + "u4")).astype(np.uint32)
            shifts = (16, 8, 0, 24) if key == "rgba" else (16, 8, 0)
            colors = np.column_stack(
                [(packed >> shift) & 255 for shift in shifts]
            ).astype(np.uint8)
            fields[key] = ScalarField(key, np.ascontiguousarray(colors[valid]), "color")
        else:
            fields[key] = ScalarField(
                key,
                np.ascontiguousarray(
                    values[valid].astype(values.dtype.newbyteorder("="))
                ),
            )
    return CloudEntity(name=name, xyz=np.ascontiguousarray(xyz[valid]), fields=fields)


def _display_depth(array):
    finite = np.isfinite(array) & (array > 0)
    result = np.zeros(array.shape, dtype=np.uint8)
    if finite.any():
        low, high = np.percentile(array[finite], [1, 99])
        if high > low:
            result[finite] = np.clip(
                (array[finite].astype(np.float64) - low) * 255 / (high - low), 0, 255
            ).astype(np.uint8)
        else:
            result[finite] = 255
    return result


def _yuv_to_rgb(y, u, v):
    y = y.astype(np.float32) - 16
    u, v = u.astype(np.float32) - 128, v.astype(np.float32) - 128
    return np.clip(
        np.stack(
            [
                1.164 * y + 1.596 * v,
                1.164 * y - 0.392 * u - 0.813 * v,
                1.164 * y + 2.017 * u,
            ],
            axis=-1,
        ),
        0,
        255,
    ).astype(np.uint8)


def _bayer_rgb(array, pattern):
    # Bilinear interpolation with edge replication, avoiding an OpenCV dependency.
    result = []
    for channel in "rgb":
        mask = np.zeros(array.shape, dtype=np.float32)
        for offset, color in enumerate(pattern):
            if color == channel:
                mask[offset // 2 :: 2, offset % 2 :: 2] = 1
        weighted = array.astype(np.float32) * mask
        padded, masks = np.pad(weighted, 1, mode="edge"), np.pad(mask, 1, mode="edge")
        numerator, denominator = np.zeros_like(mask), np.zeros_like(mask)
        for dy in range(3):
            for dx in range(3):
                weight = (2 if dy == 1 else 1) * (2 if dx == 1 else 1)
                numerator += (
                    padded[dy : dy + array.shape[0], dx : dx + array.shape[1]] * weight
                )
                denominator += (
                    masks[dy : dy + array.shape[0], dx : dx + array.shape[1]] * weight
                )
        result.append(numerator / np.maximum(denominator, 1))
    return np.stack(result, axis=-1).astype(array.dtype)


def raw_image(msg):
    encoding = str(msg.encoding)
    key = encoding.lower()
    formats = {
        "rgb8": ("u1", 3),
        "bgr8": ("u1", 3),
        "rgba8": ("u1", 4),
        "bgra8": ("u1", 4),
        "mono8": ("u1", 1),
        "8uc1": ("u1", 1),
        "mono16": ("u2", 1),
        "16uc1": ("u2", 1),
        "32fc1": ("f4", 1),
        "yuv422": ("u1", 2),
        "yuv422_yuy2": ("u1", 2),
        "uyvy": ("u1", 2),
        "yuyv": ("u1", 2),
    }
    if key.startswith("bayer_") and key.endswith(("8", "16")):
        pattern = key[6:10]
        if pattern not in ("rggb", "bggr", "gbrg", "grbg"):
            raise ValueError(f"Unsupported Bayer encoding {encoding}.")
        code, channels = ("u2" if key.endswith("16") else "u1"), 1
    elif key in formats:
        code, channels = formats[key]
    else:
        raise ValueError(f"Unsupported raw image encoding {encoding}.")
    dtype = np.dtype((">" if msg.is_bigendian else "<") + code)
    height, width, step = int(msg.height), int(msg.width), int(msg.step)
    row_bytes = width * channels * dtype.itemsize
    raw = bytes(msg.data)
    if (
        height <= 0
        or width <= 0
        or step < row_bytes
        or len(raw) < (height - 1) * step + row_bytes
    ):
        raise ValueError(
            "Raw image has invalid dimensions, row step, or truncated data."
        )
    shape = (height, width) if channels == 1 else (height, width, channels)
    strides = (
        (step, dtype.itemsize)
        if channels == 1
        else (step, channels * dtype.itemsize, dtype.itemsize)
    )
    array = np.ndarray(shape, dtype=dtype, buffer=raw, strides=strides)
    metadata = {
        "encoding": encoding,
        "original_dtype": dtype.str,
        "step": step,
        "shape": list(shape),
        "is_bigendian": bool(msg.is_bigendian),
    }
    if key in ("bgr8", "bgra8"):
        array = array[..., [2, 1, 0, 3] if channels == 4 else [2, 1, 0]]
    elif key in ("16uc1", "32fc1", "mono16"):
        metadata["depth_display"] = key != "mono16"
        metadata["display_scaling"] = "positive finite 1–99 percentile"
        array = _display_depth(array)
    elif key.startswith("bayer_"):
        array = _bayer_rgb(array, key[6:10])
        if dtype.itemsize == 2:
            array = (array.astype(np.float32) / 256).astype(np.uint8)
    elif channels == 2:
        if width % 2:
            raise ValueError("YUV422 images require an even width.")
        pairs = array.reshape(height, width // 2, 4)
        if key in ("yuv422_yuy2", "yuyv"):
            y = pairs[..., [0, 2]].reshape(height, width)
            u, v = pairs[..., 1], pairs[..., 3]
        else:
            y = pairs[..., [1, 3]].reshape(height, width)
            u, v = pairs[..., 0], pairs[..., 2]
        array = _yuv_to_rgb(y, np.repeat(u, 2, axis=1), np.repeat(v, 2, axis=1))
    return np.ascontiguousarray(array), metadata


def compressed_image(msg):
    encoding = str(msg.format)
    raw = bytes(msg.data)
    metadata = {"encoding": encoding, "compressed_bytes": len(raw)}
    depth = "compresseddepth" in encoding.lower()
    if depth and "rvl" in encoding.lower():
        raise ValueError(
            "compressedDepth RVL is not supported. PNG depth is supported."
        )
    if depth:
        offset = raw.find(b"\x89PNG\r\n\x1a\n", 0, 64)
        if offset < 0:
            raise ValueError("compressedDepth has no PNG payload.")
        payload = raw[offset:]
    else:
        payload = raw
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        array = np.array(image)
        metadata.update(original_mode=image.mode, original_dtype=str(array.dtype))
        if depth:
            if encoding.lower().startswith("32fc1"):
                if offset < 12:
                    raise ValueError(
                        "32FC1 compressedDepth is missing its quantization header."
                    )
                _, a, b = struct.unpack_from("<iff", raw)
                inverse = array.astype(np.float32)
                array = np.divide(
                    a,
                    inverse - b,
                    out=np.zeros_like(inverse),
                    where=(inverse != 0) & (inverse != b),
                )
                metadata.update(
                    stored_dtype=metadata["original_dtype"], original_dtype="float32"
                )
            metadata.update(
                depth_display=True, display_scaling="positive finite 1–99 percentile"
            )
            array = _display_depth(array)
        elif array.ndim == 2 and array.dtype.itemsize > 1:
            metadata.update(
                depth_display=False, display_scaling="positive finite 1–99 percentile"
            )
            array = _display_depth(array)
        else:
            if image.mode not in ("L", "RGB", "RGBA"):
                image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                array = np.array(image)
    metadata["shape"] = list(array.shape)
    return array, metadata


def decode_message(
    msg, message_type: str, record_time_ns: int, *, name=""
) -> FrameData:
    kind = message_kind(message_type)
    timestamp, frame_id = message_header(msg)
    metadata = {
        "message_type": message_type,
        "timestamp_ns": timestamp,
        "frame_id": frame_id,
    }
    if kind == "pointcloud":
        value = pointcloud2_to_cloud(msg, name or message_type)
        metadata.update(
            width=int(msg.width), height=int(msg.height), shape=list(value.xyz.shape)
        )
    elif kind == "image":
        value, image_metadata = (
            compressed_image(msg)
            if message_type.endswith("/CompressedImage")
            else raw_image(msg)
        )
        metadata.update(image_metadata)
    elif kind == "camera_info":
        value = {
            "width": int(msg.width),
            "height": int(msg.height),
            "distortion_model": str(msg.distortion_model),
        }
        for key in ("K", "D", "R", "P"):
            value[key] = (
                np.asarray(getattr(msg, key, getattr(msg, key.lower(), [])))
                .reshape(-1)
                .tolist()
            )
        for key in ("binning_x", "binning_y", "roi"):
            if hasattr(msg, key):
                value[key] = bounded_value(getattr(msg, key))
    else:
        value = bounded_value(msg)
    return FrameData(kind, value, timestamp, int(record_time_ns), frame_id, metadata)
