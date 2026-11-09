"""Extracted recordings keep typed and multi-component sensor attributes."""
import numpy as np
import pytest

from core.io.native_io import read_pcd, write_pcd
from core.model.entities import CloudEntity, ScalarField


def test_ascii_pcd_preserves_uint64_and_vector_count(tmp_path):
    path = tmp_path / "typed.pcd"
    path.write_text("VERSION 0.7\nFIELDS x y z time_ns descriptor\n"
                    "SIZE 4 4 4 8 8\nTYPE F F F U F\nCOUNT 1 1 1 1 3\n"
                    "WIDTH 2\nHEIGHT 1\nPOINTS 2\nDATA ascii\n"
                    "1 2 3 1700000000000000001 .1 .2 .3\n"
                    "4 5 6 1700000000000000002 .4 .5 .6\n")
    cloud = read_pcd(path)
    np.testing.assert_array_equal(cloud.field("time_ns"), [1700000000000000001, 1700000000000000002])
    assert cloud.field("time_ns").dtype == np.uint64
    np.testing.assert_array_equal(cloud.field("descriptor"), [[.1, .2, .3], [.4, .5, .6]])


def test_binary_pcd_keeps_colors_normals_and_signed_scalar(tmp_path):
    cloud = CloudEntity("typed", xyz=np.array([[1, 2, 3], [4, 5, 6]], np.float32), fields={
        "rgb": ScalarField("rgb", np.array([[255, 128, 0], [0, 0, 255]], np.uint8), "color"),
        "normal": ScalarField("normal", np.array([[0, 0, 1], [0, 1, 0]], np.float32), "normal"),
        "flags": ScalarField("flags", np.array([-128, 127], np.int8)),
    })
    path = tmp_path / "binary.pcd"
    write_pcd(cloud, path)
    restored = read_pcd(path)
    np.testing.assert_allclose(restored.field("rgb"), cloud.field("rgb") / 255., atol=1e-7)
    for name in ("normal", "flags"):
        np.testing.assert_array_equal(restored.field(name), cloud.field(name))
        assert restored.field(name).dtype == cloud.field(name).dtype


def test_invalid_pcd_fields_fail_before_creating_output(tmp_path):
    cloud = CloudEntity("invalid", xyz=np.zeros((1, 3)),
                        fields={"bad\nname": ScalarField("bad\nname", np.array([2.]))})
    path = tmp_path / "invalid.pcd"
    with pytest.raises(ValueError, match="字段名称"):
        write_pcd(cloud, path)
    assert not path.exists()


def test_reserved_names_with_other_shapes_remain_numeric_fields(tmp_path):
    cloud = CloudEntity("numeric", xyz=np.zeros((2, 3), np.float32), fields={
        "normal": ScalarField("normal", np.array([11, 29], np.int16)),
        "rgb": ScalarField("rgb", np.array([[1, 2], [3, 4]], np.uint16)),
    })
    path = tmp_path / "reserved.pcd"
    write_pcd(cloud, path)
    restored = read_pcd(path)
    for name in cloud.fields:
        np.testing.assert_array_equal(restored.field(name), cloud.field(name))
        assert restored.field(name).dtype == cloud.field(name).dtype
