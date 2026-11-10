"""Extraction uses source geometry, including points omitted by display LOD."""
import numpy as np
import pytest

from core.cancellation import OperationCancelled
from core.model.entities import CloudEntity, ScalarField
from core.object_tracking import Box3D, box_rotation


def test_extract_rotated_box_preserves_full_resolution_and_independent_fields():
    from core.box_extraction import extract_box

    box = Box3D((10., -2., 5.), (2., 4., 6.), .7, .3, -.2)
    local = np.tile([[0, 0, 0], [1, 2, 3], [1.1, 0, 0]], (100_001, 1))
    xyz = local @ box_rotation(box).T + box.center
    xyz = np.vstack([xyz, [np.nan, 0, 0], [0, np.inf, 0]])
    count = len(xyz)
    source = CloudEntity("source", path="original.pcap", xyz=xyz,
                         fields={"intensity": ScalarField("intensity", np.arange(count)),
                                 "rgb": ScalarField("rgb", np.zeros((count, 3), np.uint8), "rgb"),
                                 "ring": ScalarField("ring", np.ones(count, np.uint16))},
                         color_mode="field", color_field="ring", color_range=(0, 2),
                         colormap="viridis", point_size=4.)
    result = extract_box(source, box, name="tree", info="frame 11")
    expected = np.flatnonzero(np.tile([True, True, False], 100_001))
    np.testing.assert_array_equal(result.xyz, xyz[expected])
    assert result.point_count == 200_002
    assert result.path is None and result.name == "tree" and result.info == "frame 11"
    assert result.color_mode == "field" and result.color_field == "ring"
    assert result.color_range == (0, 2) and result.colormap == source.colormap
    assert result.point_size == 4.
    assert not np.shares_memory(result.xyz, source.xyz)
    assert not np.shares_memory(result.transform, source.transform)
    for name, field in source.fields.items():
        np.testing.assert_array_equal(result.fields[name].data, field.data[expected])
        assert result.fields[name].kind == field.kind
        assert not np.shares_memory(result.fields[name].data, field.data)


def test_extract_empty_and_cancelled_never_return_partial_cloud():
    from core.box_extraction import extract_box

    source = CloudEntity("outside", xyz=np.full((300_000, 3), 10., np.float32))
    box = Box3D((0, 0, 0), (2, 2, 2))
    assert extract_box(source, box) is None
    progress = []
    with pytest.raises(OperationCancelled):
        extract_box(source, box, cancel=lambda: bool(progress), progress=progress.append)
    assert progress and progress[-1] < 1


def test_batch_writes_only_named_cloud_files_with_original_frame_numbers(tmp_path):
    from core.box_extraction import extract_track
    from core.io.native_io import read_pcd
    from core.object_tracking import Observation
    from test_object_tracking_ui import MemorySource

    source = MemorySource(indices=(10, 11, 12))
    source.frames[10].value.fields["ring"] = ScalarField("ring", np.arange(100, dtype=np.uint16))
    source.frames[10].value.fields["time_ns"] = ScalarField("time_ns", np.arange(100, dtype=np.uint64) + 1700000000000000000)
    source.frames[10].value.fields["descriptor"] = ScalarField("descriptor", np.arange(400, dtype=np.float64).reshape(100, 4) / 7)
    observations = [Observation(10, Box3D((0, 0, 0), (2, 2, 2)), True),
                    Observation(12, Box3D((.2, 0, 0), (2, 2, 2)), False)]
    output = extract_track(source, "cloud", observations, tmp_path / "路边树.pcd", "tree")
    assert [p.name for p in output] == ["路边树_000011.pcd", "路边树_000013.pcd"]
    assert source.reads == [10, 12]
    for path, index in zip(output, [10, 12]):
        cloud = read_pcd(path)
        np.testing.assert_allclose(cloud.xyz, source.frames[index].value.xyz)
        assert cloud.point_count == 100
    np.testing.assert_array_equal(read_pcd(output[0]).field("ring"), np.arange(100))
    restored = read_pcd(output[0])
    for name in ("ring", "time_ns", "descriptor"):
        expected = source.frames[10].value.field(name)
        assert restored.field(name).dtype == expected.dtype
        np.testing.assert_array_equal(restored.field(name), expected)
    assert set(tmp_path.iterdir()) == set(output)


@pytest.mark.parametrize("failure", ["cancel", "changed", "read_error"])
def test_batch_never_publishes_partial_files(tmp_path, failure):
    from core.box_extraction import extract_track
    from core.object_tracking import Observation
    from test_object_tracking_ui import MemorySource

    source = MemorySource()
    source.token = "initial"
    original_read = source.read_frame
    def read(*args, **kwargs):
        if source.reads and failure == "read_error":
            raise OSError("disconnected")
        return original_read(*args, **kwargs)
    source.read_frame = read
    def progress(fraction):
        if fraction > 0 and failure == "changed":
            source.token = "modified"
    observations = [Observation(i, Box3D((0, 0, 0), (2, 2, 2)), True) for i in (10, 11)]
    with pytest.raises((OperationCancelled, ValueError, OSError)):
        extract_track(source, "cloud", observations, tmp_path / "tree.pcd", "tree", token="initial",
                      cancel=lambda: failure == "cancel" and bool(source.reads), progress=progress)
    assert not list(tmp_path.iterdir())


def test_single_extraction_keeps_custom_name_and_skips_empty_frames(tmp_path):
    from core.box_extraction import extract_track
    from core.object_tracking import Observation
    from test_object_tracking_ui import MemorySource

    source = MemorySource()
    target = tmp_path / "单棵树.ply"
    observation = Observation(10, Box3D((0, 0, 0), (2, 2, 2)))
    assert extract_track(source, "cloud", [observation], target, "tree") == (target,)
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        extract_track(source, "cloud", [observation], target, "tree")
    assert target.read_bytes() == original
    empty = Observation(11, Box3D((100, 0, 0), (2, 2, 2)))
    assert extract_track(source, "cloud", [empty], tmp_path / "empty.pcd", "tree") == ()
    assert list(tmp_path.iterdir()) == [target]


def test_overwrite_empty_frame_fails_without_leaving_a_stale_frame_in_new_batch(tmp_path):
    from core.box_extraction import extract_track
    from core.object_tracking import Observation
    from test_object_tracking_ui import MemorySource

    source = MemorySource()
    observations = [Observation(i, Box3D((0, 0, 0), (2, 2, 2))) for i in (10, 11)]
    target = tmp_path / "tree.pcd"
    outputs = extract_track(source, "cloud", observations, target, "tree")
    originals = {p: p.read_bytes() for p in outputs}
    observations[1] = Observation(11, Box3D((100, 0, 0), (2, 2, 2)))
    with pytest.raises(ValueError, match="空框"):
        extract_track(source, "cloud", observations, target, "tree", overwrite=True)
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == originals
