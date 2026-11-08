from types import SimpleNamespace as NS
import io
import struct
import numpy as np
import pytest
from PIL import Image


def header():
    return NS(stamp=NS(sec=1700000000, nanosec=123456789), frame_id="camera")


def test_raw_image_honors_padding_bgr_and_header():
    from core.io.recording_messages import decode_message

    msg = NS(
        header=header(),
        width=2,
        height=2,
        encoding="bgr8",
        step=8,
        is_bigendian=False,
        data=bytes([1, 2, 3, 4, 5, 6, 99, 99, 7, 8, 9, 10, 11, 12, 99, 99]),
    )
    frame = decode_message(msg, "sensor_msgs/msg/Image", 7)
    assert frame.value.tolist() == [[[3, 2, 1], [6, 5, 4]], [[9, 8, 7], [12, 11, 10]]]
    assert frame.timestamp_ns == 1700000000123456789
    assert frame.record_time_ns == 7
    assert frame.frame_id == "camera"


def test_depth_image_preserves_dtype_and_uses_display_scaling():
    from core.io.recording_messages import decode_message

    msg = NS(
        header=header(),
        width=2,
        height=1,
        encoding="16UC1",
        step=6,
        is_bigendian=True,
        data=struct.pack(">HHH", 100, 200, 999),
    )
    frame = decode_message(msg, "sensor_msgs/msg/Image", 1)
    assert frame.value.dtype == np.uint8
    assert frame.value.tolist() == [[0, 255]]
    assert frame.metadata["original_dtype"] == ">u2"
    assert frame.metadata["depth_display"] is True


def test_compressed_png_is_selected_by_schema_and_camera_info_is_canonical():
    from core.io.recording_messages import decode_message, message_kind

    buf = io.BytesIO()
    Image.fromarray(np.array([[[10, 20, 30]]], dtype=np.uint8)).save(buf, format="PNG")
    frame = decode_message(
        NS(header=header(), format="png", data=buf.getvalue()),
        "sensor_msgs/msg/CompressedImage",
        2,
    )
    assert frame.value.tolist() == [[[10, 20, 30]]]
    assert message_kind("std_msgs/msg/String") == "message"
    info = decode_message(
        NS(
            header=header(),
            width=640,
            height=480,
            k=list(range(9)),
            d=[1, 2],
            r=list(range(9)),
            p=list(range(12)),
            distortion_model="plumb_bob",
        ),
        "sensor_msgs/msg/CameraInfo",
        2,
    )
    assert info.value["K"] == list(range(9))
    assert info.value["width"] == 640


def test_pointcloud_preserves_padding_big_endian_and_auxiliary_fields():
    from core.io.recording_messages import decode_message

    fields = [
        NS(name=n, offset=o, datatype=t, count=1)
        for n, o, t in [
            ("x", 0, 7),
            ("y", 4, 7),
            ("z", 8, 7),
            ("rgb", 12, 6),
            ("ring", 16, 4),
        ]
    ]
    point = struct.pack(">fffIH", 1, 2, 3, 0xFF8040, 7)
    msg = NS(
        header=header(),
        width=1,
        height=2,
        point_step=18,
        row_step=22,
        is_bigendian=True,
        fields=fields,
        data=point + b"pad!" + point + b"pad!",
    )
    cloud = decode_message(msg, "sensor_msgs/msg/PointCloud2", 1).value
    assert cloud.xyz.tolist() == [[1, 2, 3], [1, 2, 3]]
    assert cloud.fields["ring"].data.tolist() == [7, 7]
    assert cloud.fields["rgb"].data.tolist() == [[255, 128, 64], [255, 128, 64]]


def test_unknown_messages_are_bounded_and_rvl_is_explicit():
    from core.io.recording_messages import decode_message

    frame = decode_message(
        NS(data=b"a" * 100000, values=list(range(100000))), "vendor/msg/Blob", 8
    )
    assert len(str(frame.value)) < 12000
    assert frame.timestamp_ns is None
    with pytest.raises(ValueError, match="RVL"):
        decode_message(
            NS(header=header(), format="16UC1; compressedDepth rvl", data=b""),
            "sensor_msgs/msg/CompressedImage",
            1,
        )


def test_legacy_cloud_decoder_uses_padded_rows():
    from core.io.mcap_reader import pointcloud2_to_frame

    fields = [
        NS(name=n, offset=i * 4, datatype=7, count=1)
        for i, n in enumerate(("x", "y", "z", "intensity"))
    ]
    msg = NS(
        header=header(),
        width=1,
        height=2,
        fields=fields,
        point_step=16,
        row_step=20,
        is_bigendian=False,
        data=struct.pack("<ffff", 1, 2, 3, 4)
        + b"pad!"
        + struct.pack("<ffff", 5, 6, 7, 8)
        + b"pad!",
    )
    frame = pointcloud2_to_frame(msg)
    assert frame.xyz.tolist() == [[1, 2, 3], [5, 6, 7]]
    assert frame.intensity.tolist() == [4, 8]


def test_compressed_depth_png_and_yuv422():
    from core.io.recording_messages import decode_message

    data = io.BytesIO()
    Image.fromarray(np.array([[100, 200]], dtype=np.uint16)).save(data, format="PNG")
    msg = NS(
        header=header(),
        format="16UC1; compressedDepth png",
        data=struct.pack("<iff", 0, 0, 0) + data.getvalue(),
    )
    frame = decode_message(msg, "sensor_msgs/msg/CompressedImage", 1)
    assert frame.value.tolist() == [[0, 255]]
    assert frame.metadata["depth_display"] is True
    msg = NS(
        header=header(),
        width=2,
        height=1,
        encoding="yuv422",
        step=4,
        is_bigendian=False,
        data=bytes([128, 16, 128, 235]),
    )
    frame = decode_message(msg, "sensor_msgs/msg/Image", 1)
    assert frame.value.tolist() == [[[0, 0, 0], [254, 254, 254]]]


def test_serialized_header_reads_nanoseconds_without_decoding_payload():
    from core.io.recording_messages import serialized_header

    raw = (
        b"\x00\x01\x00\x00"
        + struct.pack("<iII", -1, 999999999, 4)
        + b"cam\0"
        + b"huge opaque image"
    )
    assert serialized_header(raw, "sensor_msgs/msg/Image", "cdr") == (-1, "cam")
    raw = (
        struct.pack("<IIII", 7, 1700000000, 123456789, 3)
        + b"cam"
        + b"huge opaque image"
    )
    assert serialized_header(raw, "sensor_msgs/Image", "ros1") == (
        1700000000123456789,
        "cam",
    )


def test_compressed_mono16_preserves_source_dtype_and_maps_display_range():
    from core.io.recording_messages import decode_message

    buffer = io.BytesIO()
    Image.fromarray(np.array([[1000, 2000]], dtype=np.uint16)).save(
        buffer, format="PNG"
    )
    message = NS(
        header=header(), format="mono16; png compressed", data=buffer.getvalue()
    )
    frame = decode_message(message, "sensor_msgs/msg/CompressedImage", 1)
    assert frame.value.tolist() == [[0, 255]]
    assert frame.value.dtype == np.uint8
    assert frame.metadata["original_dtype"] == "uint16"
    assert frame.metadata["original_mode"] == "I;16"
    assert frame.metadata["depth_display"] is False
    assert "display_scaling" in frame.metadata
