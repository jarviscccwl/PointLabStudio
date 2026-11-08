"""Native-library and point-cloud IO checks for the desktop environment."""
from pathlib import Path
import tempfile
import unittest

import numpy as np

from core.algorithms.filters import voxel_downsample
from core.io.exporters import export_entity
from core.io.readers import ReadOptions, read_cloud
from core.model.entities import CloudEntity, ScalarField


class PointCloudSmokeTests(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(42)
        self.xyz = rng.uniform(-1, 1, (4096, 3)).astype(np.float32)
        self.rgb = (self.xyz + 1) / 2
        self.entity = CloudEntity(
            name="测试点云", xyz=self.xyz,
            fields={"rgb": ScalarField("rgb", self.rgb, kind="color")},
        )

    def test_ply_pcd_las_round_trip_in_unicode_directory(self):
        with tempfile.TemporaryDirectory(prefix="PointLab点云 ") as folder:
            for extension in ("ply", "pcd", "las"):
                with self.subTest(extension=extension):
                    path = Path(folder) / f"彩色 点云.{extension}"
                    export_entity(self.entity, str(path))
                    cloud = read_cloud(str(path), ReadOptions())
                    self.assertEqual(cloud.point_count, len(self.xyz))
                    np.testing.assert_allclose(cloud.xyz, self.xyz, atol=0.0011)
                    np.testing.assert_allclose(cloud.fields["rgb"].data, self.rgb, atol=1 / 255)

    def test_open3d_native_library_reads_exported_ply(self):
        import open3d as o3d

        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "points.ply")
            export_entity(self.entity, path)
            cloud = o3d.io.read_point_cloud(path)
            np.testing.assert_allclose(np.asarray(cloud.points), self.xyz)
            self.assertTrue(cloud.has_colors())

    def test_voxel_filter_reduces_points_and_preserves_color(self):
        xyz, rgb, _ = voxel_downsample(self.xyz, self.rgb, voxel=0.3)
        self.assertGreater(len(xyz), 0)
        self.assertLess(len(xyz), len(self.xyz))
        self.assertEqual(rgb.shape, xyz.shape)
        self.assertTrue(np.isfinite(xyz).all())


if __name__ == "__main__":
    unittest.main()
