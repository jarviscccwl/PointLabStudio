import json
from pathlib import Path
import pytest


def make_mcap(path):
    from mcap.writer import Writer

    with path.open("wb") as f:
        writer = Writer(f)
        writer.start()
        schema = writer.register_schema(
            name="vendor/Thing", encoding="jsonschema", data=b"{}"
        )
        channel = writer.register_channel(
            topic="/compressed/unknown", message_encoding="json", schema_id=schema
        )
        for sequence, value in enumerate([1, 2, 3]):
            writer.add_message(
                channel_id=channel,
                log_time=1700000000000000001,
                publish_time=1700000000000000000,
                sequence=sequence,
                data=json.dumps({"value": value}).encode(),
            )
        writer.finish()


def test_mcap_metadata_unknown_decoding_duplicate_timestamp_and_cache(
    tmp_path, monkeypatch
):
    from core.io.recording import open_source

    monkeypatch.setenv("POINTLAB_RECORDING_CACHE", str(tmp_path / "cache"))
    path = tmp_path / "test.mcap"
    make_mcap(path)
    source = open_source(str(path))
    info = source.inspect()
    assert info.streams[0].kind == "message"
    assert info.streams[0].count == 3
    assert info.start_ns == 1700000000000000001
    progress = []
    source.build_index(progress=progress.append)
    sid = info.streams[0].id
    assert source.read_frame(sid, 1).value == {"value": 2}
    assert source.read_frame(sid, 2).record_time_ns == 1700000000000000001
    assert source.read_frame(sid, 2).timestamp_ns is None
    assert progress[-1] == 1
    again = open_source(str(path))
    again.build_index()
    assert again.read_frame(sid, 0).value == {"value": 1}
    assert list((tmp_path / "cache").glob("*"))


def test_discovery_finds_mixed_sources_and_collapses_rosbag_folder(tmp_path):
    from core.io.recording import discover_sources, is_data_source

    bag = tmp_path / "bag"
    bag.mkdir()
    (bag / "metadata.yaml").write_text(
        "rosbag2_bagfile_information:\n  storage_identifier: sqlite3\n  relative_file_paths: [a.db3]\n"
    )
    (bag / "a.db3").touch()
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "cloud.pcd").touch()
    paths = discover_sources([str(tmp_path), str(bag / "a.db3")])
    assert set(paths) == {str(bag), str(tmp_path / "notes.txt")}
    assert not is_data_source(str(tmp_path))


def test_text_table_and_cancel(tmp_path):
    from core.io.recording import open_source
    from core.cancellation import OperationCancelled

    path = tmp_path / "values.csv"
    path.write_text("a,b\n1,2\n3,4\n")
    source = open_source(str(path))
    assert source.inspect().streams[0].kind == "table"
    source.build_index()
    assert source.read_frame("0", 0).value["rows"] == [["1", "2"], ["3", "4"]]
    with pytest.raises(OperationCancelled):
        source.build_index(cancel=lambda: True)


@pytest.mark.parametrize("version", [1, 2])
def test_rosbags_native_roundtrip_and_nanosecond_index(tmp_path, version):
    from rosbags.typesys import Stores, get_typestore
    from core.io.recording import open_source

    store = get_typestore(Stores.ROS2_HUMBLE)
    msgtype = "std_msgs/msg/String"
    msg = store.types[msgtype](data="hello")
    if version == 1:
        from rosbags.rosbag1 import Writer

        path = tmp_path / "test.bag"
        with Writer(path) as writer:
            connection = writer.add_connection("/status", msgtype, typestore=store)
            writer.write(
                connection, 1700000000000000011, store.serialize_ros1(msg, msgtype)
            )
    else:
        from rosbags.rosbag2 import Writer

        path = tmp_path / "testbag"
        with Writer(path, version=9) as writer:
            connection = writer.add_connection("/status", msgtype, typestore=store)
            writer.write(
                connection, 1700000000000000011, store.serialize_cdr(msg, msgtype)
            )
    source = open_source(str(path))
    info = source.inspect()
    source.build_index()
    assert source.read_frame(info.streams[0].id, 0).value["data"] == "hello"
    assert (
        source.read_frame(info.streams[0].id, 0).record_time_ns == 1700000000000000011
    )
    if version == 2:
        standalone = open_source(str(next(path.glob("*.db3"))))
        standalone.build_index()
        assert (
            standalone.read_frame(standalone.info.streams[0].id, 0).value["data"]
            == "hello"
        )


def test_image_directory_groups_only_direct_images_in_natural_order(tmp_path):
    from PIL import Image
    from core.io.recording import discover_sources, open_source, is_data_source

    for name, value in [("frame10.png", 10), ("frame2.png", 2)]:
        Image.new("RGB", (1, 1), (value, 0, 0)).save(tmp_path / name)
    (tmp_path / "notes.txt").write_text("notes")
    paths = discover_sources([str(tmp_path), str(tmp_path / "frame2.png")])
    assert set(paths) == {str(tmp_path), str(tmp_path / "notes.txt")}
    assert is_data_source(str(tmp_path))
    source = open_source(str(tmp_path))
    assert source.inspect().streams[0].count == 2
    source.build_index()
    assert source.read_frame("0", 0).value[0, 0, 0] == 2
    assert source.read_frame("0", 1).value[0, 0, 0] == 10
    assert source.read_frame("0", 1).metadata["filename"] == "frame10.png"


def test_invalid_yaml_remains_a_bounded_text_preview(tmp_path):
    from core.io.recording import open_source, discover_sources

    path = tmp_path / "metadata.yaml"
    path.write_text("invalid: [unterminated")
    assert discover_sources([str(tmp_path)]) == [str(path)]
    frame = open_source(str(path)).read_frame("0", 0)
    assert frame.kind == "text"
    assert "parse_error" in frame.metadata


def test_index_cache_invalidates_changed_recording(tmp_path, monkeypatch):
    from core.io.recording import open_source

    monkeypatch.setenv("POINTLAB_RECORDING_CACHE", str(tmp_path / "cache"))
    path = tmp_path / "test.mcap"
    make_mcap(path)
    source = open_source(str(path))
    source.build_index()
    from mcap.writer import Writer

    with path.open("wb") as handle:
        writer = Writer(handle)
        writer.start()
        cid = writer.register_channel(
            topic="/other", message_encoding="json", schema_id=0
        )
        writer.add_message(
            channel_id=cid, log_time=5, publish_time=0, data=b'{"different":true}'
        )
        writer.finish()
    changed = open_source(str(path))
    changed.build_index()
    assert len(changed.index[changed.info.streams[0].id]) == 1
    assert changed.read_frame(changed.info.streams[0].id, 0).value == {
        "different": True
    }


def test_mcap_ros1_custom_definition_decodes_without_topic_guessing(tmp_path):
    from core.io.recording import open_source
    from mcap.writer import Writer
    import struct

    path = tmp_path / "ros1.mcap"
    with path.open("wb") as handle:
        writer = Writer(handle)
        writer.start()
        schema = writer.register_schema(
            name="custom/Reading", encoding="ros1msg", data=b"int32 value\n"
        )
        channel = writer.register_channel(
            topic="/camera/compressed", message_encoding="ros1", schema_id=schema
        )
        writer.add_message(
            channel_id=channel, log_time=12, publish_time=8, data=struct.pack("<i", 42)
        )
        writer.finish()
    source = open_source(str(path))
    source.build_index()
    frame = source.read_frame(source.info.streams[0].id, 0)
    assert frame.kind == "message"
    assert frame.value["value"] == 42
    assert frame.timestamp_ns is None
    assert frame.metadata["publish_time_ns"] == 8


def test_custom_ros1_header_is_indexed_without_decoding_message(tmp_path):
    from core.io.recording import open_source
    from mcap.writer import Writer
    import struct

    path = tmp_path / "header.mcap"
    definition = (
        "std_msgs/Header header\nint32 value\n"
        + "=" * 80
        + "\nMSG: std_msgs/Header\nuint32 seq\ntime stamp\nstring frame_id\n"
    )
    with path.open("wb") as handle:
        writer = Writer(handle)
        writer.start()
        schema = writer.register_schema(
            name="custom/Reading", encoding="ros1msg", data=definition.encode()
        )
        channel = writer.register_channel(
            topic="/value", message_encoding="ros1", schema_id=schema
        )
        raw = struct.pack("<IIII", 1, 10, 5, 3) + b"cam" + struct.pack("<i", 42)
        writer.add_message(channel_id=channel, log_time=20, publish_time=0, data=raw)
        writer.finish()
    source = open_source(str(path))
    source.build_index()
    sid = source.info.streams[0].id
    assert source.index[sid][0].timestamp_ns == 10000000005
    assert source.read_frame(sid, 0).timestamp_ns == 10000000005


def test_uppercase_bag_extension_uses_ros1_decode(tmp_path):
    from core.io.recording import open_source
    from rosbags.typesys import Stores, get_typestore
    from rosbags.rosbag1 import Writer

    store = get_typestore(Stores.ROS1_NOETIC)
    path = tmp_path / "uppercase.BAG"
    with Writer(path) as writer:
        connection = writer.add_connection(
            "/status", "std_msgs/msg/String", typestore=store
        )
        message = store.types["std_msgs/msg/String"](data="upper")
        writer.write(
            connection, 123, store.serialize_ros1(message, "std_msgs/msg/String")
        )
    source = open_source(str(path))
    source.build_index()
    assert source.read_frame(source.info.streams[0].id, 0).value == {"data": "upper"}


def test_plain_file_and_image_sequence_do_not_fabricate_record_time(tmp_path):
    from core.io.recording import open_source
    from PIL import Image

    text = tmp_path / "notes.txt"
    text.write_text("untimed")
    Image.new("RGB", (1, 1)).save(tmp_path / "frame.png")
    for path in (text, tmp_path):
        source = open_source(str(path))
        refs = source.build_index()["0"]
        assert refs[0].record_time_ns is None
        assert refs[0].timestamp_ns is None
        assert source.read_frame("0", 0).record_time_ns is None


def write_json_mcap(path, values, **writer_options):
    from mcap.writer import Writer

    with path.open("wb") as handle:
        writer = Writer(handle, **writer_options)
        writer.start()
        schema = writer.register_schema(
            name="vendor/Thing", encoding="jsonschema", data=b"{}"
        )
        channel = writer.register_channel(
            topic="/value", message_encoding="json", schema_id=schema
        )
        for index, value in enumerate(values):
            writer.add_message(
                channel_id=channel,
                log_time=100 + index,
                publish_time=0,
                data=json.dumps({"value": value}).encode(),
            )
        writer.finish()


def test_same_source_refreshes_index_and_reads_after_replacement(tmp_path, monkeypatch):
    from core.io.recording import open_source

    monkeypatch.setenv("POINTLAB_RECORDING_CACHE", str(tmp_path / "cache"))
    path = tmp_path / "replace.mcap"
    write_json_mcap(path, [1])
    source = open_source(str(path))
    source.build_index()
    sid = source.info.streams[0].id
    write_json_mcap(path, [20, 30, 40])
    assert source.read_frame(sid, 2).value == {"value": 40}
    assert len(source.build_index()[sid]) == 3
    reopened = open_source(str(path))
    assert len(reopened.build_index()[sid]) == 3


def test_index_rejects_source_mutation_during_scan(tmp_path, monkeypatch):
    from core.io.recording import open_source

    monkeypatch.setenv("POINTLAB_RECORDING_CACHE", str(tmp_path / "cache"))
    path = tmp_path / "racing.mcap"
    write_json_mcap(path, [1, 2])
    source = open_source(str(path))
    original_messages = source._messages

    def mutating_messages(selected):
        yield from original_messages(selected)
        write_json_mcap(path, [100, 200, 300])

    monkeypatch.setattr(source, "_messages", mutating_messages)
    with pytest.raises(ValueError, match="changed"):
        source.build_index()
    assert source.index == {}
    assert not list((tmp_path / "cache").glob("*.json"))


def test_cancel_during_cache_load_does_not_publish_or_complete(tmp_path, monkeypatch):
    from core.io.recording import open_source
    from core.cancellation import OperationCancelled

    monkeypatch.setenv("POINTLAB_RECORDING_CACHE", str(tmp_path / "cache"))
    path = tmp_path / "cached.mcap"
    write_json_mcap(path, [1, 2])
    original = open_source(str(path))
    original.build_index()
    source = open_source(str(path))
    cache_load = source._load_cache
    canceled = False

    def canceling_load():
        nonlocal canceled
        result = cache_load()
        canceled = True
        return result

    monkeypatch.setattr(source, "_load_cache", canceling_load)
    progress = []
    with pytest.raises(OperationCancelled):
        source.build_index(cancel=lambda: canceled, progress=progress.append)
    assert source.index == {}
    assert 1.0 not in progress


@pytest.mark.parametrize(
    "writer_options",
    [
        {"repeat_schemas": False},
        {"repeat_channels": False},
        {"repeat_schemas": False, "repeat_channels": False},
    ],
)
def test_mcap_sparse_summary_recovers_definitions_and_preserves_indexed_reads(
    tmp_path, writer_options
):
    from core.io.recording import open_source

    path = tmp_path / "sparse.mcap"
    write_json_mcap(path, [12, 34], **writer_options)
    source = open_source(str(path))
    info = source.inspect()
    assert info.streams[0].message_type == "vendor/Thing"
    assert info.streams[0].count == 2
    source.build_index()
    assert source._has_chunk_index
    assert source.read_frame(info.streams[0].id, 1).value == {"value": 34}


def test_ros2_directory_named_bag_uses_ros2_reader_and_cdr(tmp_path):
    from core.io.recording import open_source
    from rosbags.rosbag2 import Writer
    from rosbags.typesys import Stores, get_typestore

    path = tmp_path / "capture.bag"
    store = get_typestore(Stores.ROS2_HUMBLE)
    with Writer(path, version=9) as writer:
        connection = writer.add_connection(
            "/status", "std_msgs/msg/String", typestore=store
        )
        message = store.types["std_msgs/msg/String"](data="ROS2 directory")
        writer.write(
            connection, 123, store.serialize_cdr(message, "std_msgs/msg/String")
        )
    source = open_source(str(path))
    info = source.inspect()
    assert info.format == "ROS2 bag"
    source.build_index()
    assert source.read_frame(info.streams[0].id, 0).value == {"data": "ROS2 directory"}


def test_text_pages_preserve_utf8_characters_and_cover_entire_file(tmp_path):
    from core.io.recording import open_source

    path = tmp_path / "unicode.txt"
    content = "第一行🙂\n第二行é🙂\n最后一行"
    path.write_bytes(content.encode("utf-8"))
    source = open_source(str(path))
    offset = 0
    pages = []
    while offset is not None:
        frame = source.read_page(offset, byte_limit=8)
        assert frame.metadata["offset"] == offset
        assert "�" not in frame.value
        pages.append(frame.value)
        offset = frame.metadata["next_offset"]
    assert "".join(pages) == content
    initial = source.read_frame("0", 0)
    assert initial.metadata["offset"] == 0
    assert initial.metadata["next_offset"] is None


def test_csv_pages_keep_header_and_whole_quoted_records(tmp_path):
    from core.io.recording import open_source

    path = tmp_path / "records.csv"
    path.write_bytes(
        'name,value\n甲,"line one\nline two"\n乙,2\n丙,3\n'.encode("utf-8")
    )
    source = open_source(str(path))
    first = source.read_page(row_limit=1, byte_limit=40)
    assert first.value["columns"] == ["name", "value"]
    assert first.value["rows"] == [["甲", "line one\nline two"]]
    second = source.read_page(first.metadata["next_offset"], row_limit=1, byte_limit=40)
    assert second.value["columns"] == ["name", "value"]
    assert second.value["rows"] == [["乙", "2"]]
    third = source.read_page(second.metadata["next_offset"], row_limit=1, byte_limit=40)
    assert third.value["rows"] == [["丙", "3"]]
    assert third.metadata["next_offset"] is None


def test_large_csv_opening_is_bounded_and_all_rows_are_pageable(tmp_path):
    from core.io.recording import open_source

    path = tmp_path / "long.csv"
    path.write_text("value\n" + "".join(f"{index}\n" for index in range(1003)))
    source = open_source(str(path))
    first = source.read_frame("0", 0)
    assert len(first.value["rows"]) == 1000
    assert first.metadata["truncated"] is True
    second = source.read_page(first.metadata["next_offset"])
    assert second.value["rows"] == [["1000"], ["1001"], ["1002"]]
    assert second.metadata["next_offset"] is None


def test_oversized_json_uses_text_pages_and_changed_source_rejects_old_cursor(tmp_path):
    from core.io.recording import open_source

    path = tmp_path / "large.json"
    content = json.dumps({"data": "x" * 300000})
    path.write_text(content)
    source = open_source(str(path))
    first = source.read_frame("0", 0)
    assert first.kind == "text"
    assert first.metadata["next_offset"] is not None
    next_offset = first.metadata["next_offset"]
    path.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="changed"):
        source.read_page(next_offset)
    assert source.read_page().value == {"changed": True}


def test_page_limits_cancel_and_encoding_limitations_are_explicit(tmp_path):
    from core.io.recording import open_source
    from core.cancellation import OperationCancelled

    path = tmp_path / "legacy.txt"
    path.write_bytes("测试".encode("gb18030"))
    source = open_source(str(path))
    frame = source.read_page()
    assert frame.metadata["encoding"] == "utf-8"
    assert frame.metadata["encoding_warning"]
    with pytest.raises(OperationCancelled):
        source.read_page(cancel=lambda: True)
    with pytest.raises(ValueError):
        source.read_page(byte_limit=0)
    path = tmp_path / "wide.csv"
    path.write_bytes(b'value\n"record with multiple\nphysical lines"\n')
    source = open_source(str(path))
    with pytest.raises(ValueError, match="byte_limit"):
        source.read_page(byte_limit=16)
    assert source.read_page(byte_limit=128).value["rows"] == [
        ["record with multiple\nphysical lines"]
    ]


@pytest.mark.parametrize("version", [1, 2])
def test_same_rosbag_source_reopens_changed_recording(tmp_path, version):
    from core.io.recording import open_source
    from rosbags.typesys import Stores, get_typestore

    store = get_typestore(Stores.ROS2_HUMBLE)
    path = tmp_path / ("recording.bag" if version == 1 else "recording")

    def write_recording(values):
        if version == 1:
            from rosbags.rosbag1 import Writer

            writer = Writer(path)
        else:
            from rosbags.rosbag2 import Writer

            writer = Writer(path, version=9)
        with writer:
            connection = writer.add_connection(
                "/status", "std_msgs/msg/String", typestore=store
            )
            for index, value in enumerate(values):
                message = store.types["std_msgs/msg/String"](data=value)
                raw = (
                    store.serialize_ros1(message, "std_msgs/msg/String")
                    if version == 1
                    else store.serialize_cdr(message, "std_msgs/msg/String")
                )
                writer.write(connection, 100 + index, raw)

    write_recording(["original"])
    source = open_source(str(path))
    source.build_index()
    sid = source.info.streams[0].id
    assert source.read_frame(sid, 0).value == {"data": "original"}
    source.close()
    path.rename(path.with_name("previous"))
    write_recording(["new first", "new second"])
    assert source.read_frame(sid, 1).value == {"data": "new second"}
    assert len(source.build_index()[sid]) == 2
    source.close()


def test_cached_source_token_changes_with_current_source_identity(tmp_path):
    from core.io.recording import open_source

    path = tmp_path / "identity.mcap"
    write_json_mcap(path, [1])
    source = open_source(str(path))
    token = source.cache_token()
    assert hash(token)
    write_json_mcap(path, [100, 200])
    assert source.cache_token() != token


def test_image_sequence_token_is_compact_and_reads_changed_frame_without_traversal(
    tmp_path, monkeypatch
):
    from core.io import recording
    from PIL import Image

    path = tmp_path / "frame.png"
    Image.new("RGB", (1, 1), (1, 2, 3)).save(path)
    source = recording.open_source(str(tmp_path))
    source.build_index()
    assert source.read_frame("0", 0).value[0, 0].tolist() == [1, 2, 3]

    def unexpected_directory_traversal(_path):
        pytest.fail(
            "Cache token and indexed frame read must not enumerate image files."
        )

    monkeypatch.setattr(recording, "_image_files", unexpected_directory_traversal)
    token = source.cache_token()
    hash(token)
    assert len(token) == 3
    assert all(not isinstance(item, (tuple, list)) for item in token)

    Image.new("RGB", (3, 2), (4, 5, 6)).save(path)
    frame = source.read_frame("0", 0)
    assert frame.value.shape == (2, 3, 3)
    assert frame.value[0, 0].tolist() == [4, 5, 6]


def test_image_sequence_token_changes_when_directory_membership_changes(tmp_path):
    from core.io.recording import open_source
    from PIL import Image

    Image.new("RGB", (1, 1)).save(tmp_path / "frame1.png")
    source = open_source(str(tmp_path))
    token = source.cache_token()
    Image.new("RGB", (1, 1)).save(tmp_path / "frame2.png")
    assert source.cache_token() != token


@pytest.mark.parametrize(
    "suffix,dtype,mode",
    [
        (".png", "uint16", "I;16"),
        (".tiff", "uint16", "I;16"),
        (".tiff", "int32", "I"),
    ],
)
def test_standalone_high_bit_grayscale_preserves_samples_for_display_scaling(
    tmp_path, suffix, dtype, mode
):
    import numpy as np
    from PIL import Image
    from core.io.recording import open_source

    path = tmp_path / ("mono" + suffix)
    Image.fromarray(np.array([[1000, 2000]], dtype=dtype)).save(path)
    source = open_source(str(path))
    assert source.build_index()["0"][0].record_time_ns is None
    frame = source.read_frame("0", 0)
    assert frame.value.tolist() == [[1000, 2000]]
    assert str(frame.value.dtype) == dtype
    assert frame.metadata["original_dtype"] == dtype
    assert frame.metadata["original_mode"] == mode
    assert frame.metadata["display_scaling"] == "finite 1–99 percentile in viewer"
    assert frame.timestamp_ns is None
    assert frame.record_time_ns is None
