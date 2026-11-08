import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from core.calibration import (
    CameraCalibration,
    calibration_from_dict,
    calibration_to_dict,
    from_camera_info,
    load_calibration,
    project_points,
)


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "K": [[100, 0, 50], [0, 100, 40], [0, 0, 1]],
            "D": [], "width": 100, "height": 80,
        }

    def calibrated(self, **changes):
        return calibration_from_dict({**self.data, "T_camera_lidar": np.eye(4).tolist(), **changes})

    def test_camera_info_intrinsics_have_no_default_extrinsics(self):
        camera = from_camera_info({
            "k": [100, 0, 50, 0, 100, 40, 0, 0, 1],
            "d": [0, 0, 0, 0, 0], "width": 100, "height": 80,
            "distortion_model": "plumb_bob",
        })
        self.assertIsNone(camera.T_camera_lidar)
        with self.assertRaisesRegex(ValueError, "extrinsic|T_camera_lidar"):
            project_points(np.array([[0, 0, 1]]), camera)

    def test_camera_info_zero_operational_parameters_preserve_full_frame(self):
        camera = from_camera_info({
            **self.data, "binning_x": 0, "binning_y": 0,
            "roi": {"x_offset": 0, "y_offset": 0, "width": 0, "height": 0,
                    "do_rectify": False},
        })
        np.testing.assert_array_equal(camera.K, self.data["K"])
        self.assertEqual((camera.width, camera.height), (100, 80))
        self.assertIsNone(camera.T_camera_lidar)

    def test_camera_info_binning_uses_effective_raw_image_dimensions(self):
        camera = from_camera_info({**self.data, "binning_x": 2, "binning_y": 4})
        np.testing.assert_allclose(camera.K, [[50, 0, 25], [0, 25, 10], [0, 0, 1]])
        self.assertEqual((camera.width, camera.height), (50, 20))

    def test_camera_info_crop_adjusts_principal_point_before_binning(self):
        original = {
            **self.data, "K": [[100, 10, 50], [0, 100, 40], [0, 0, 1]],
            "binning_x": 2, "binning_y": 2,
            "roi": {"x_offset": 10, "y_offset": 10, "width": 60, "height": 40,
                    "do_rectify": True},
        }
        camera = from_camera_info(original)
        np.testing.assert_allclose(camera.K, [[50, 5, 20], [0, 50, 15], [0, 0, 1]])
        self.assertEqual((camera.width, camera.height), (30, 20))
        camera = calibration_from_dict({"T_camera_lidar": np.eye(4).tolist()}, base=camera)
        pixels, indices = project_points(np.array([[0, 0, 1], [0.1, -0.1, 1]]), camera)
        np.testing.assert_allclose(pixels, [[20, 15], [24.5, 10]])
        np.testing.assert_array_equal(indices, [0, 1])
        restored = calibration_from_dict(calibration_to_dict(camera))
        np.testing.assert_array_equal(restored.K, camera.K)
        self.assertEqual((restored.width, restored.height), (30, 20))
        self.assertEqual(original["K"][0][2], 50)

    def test_camera_info_roi_without_binning_preserves_focal_lengths(self):
        camera = from_camera_info({
            **self.data,
            "roi": {"x_offset": 10, "y_offset": 10, "width": 60, "height": 40},
        })
        np.testing.assert_allclose(camera.K, [[100, 0, 40], [0, 100, 30], [0, 0, 1]])
        self.assertEqual((camera.width, camera.height), (60, 40))

    def test_camera_info_partial_binning_blocks_use_integer_output_dimensions(self):
        camera = from_camera_info({**self.data, "width": 101, "height": 81,
                                   "binning_x": 2, "binning_y": 3})
        self.assertEqual((camera.width, camera.height), (50, 27))
        np.testing.assert_allclose(camera.K, [[50, 0, 25], [0, 100 / 3, 40 / 3], [0, 0, 1]])

    def test_camera_info_raw_geometry_accepts_distinct_rectification_parameters(self):
        camera = from_camera_info({
            **self.data, "binning_x": 1, "binning_y": 1,
            "roi": {"x_offset": 0, "y_offset": 0, "width": 100, "height": 80},
            "R": [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
            "P": [[80, 0, 45, -8], [0, 80, 35, 0], [0, 0, 1, 0]],
        })
        np.testing.assert_array_equal(camera.K, self.data["K"])
        self.assertEqual((camera.width, camera.height), (100, 80))

    def test_camera_info_rejects_invalid_operational_parameters(self):
        invalid_roi = [
            None, [], {"x_offset": -1}, {"y_offset": float("nan")},
            {"width": float("inf"), "height": 20}, {"width": True, "height": 20},
            {"width": 1.5, "height": 20}, {"width": 50, "height": 0},
            {"width": 0, "height": 20}, {"x_offset": 1},
            {"x_offset": 51, "width": 50, "height": 20},
            {"y_offset": 61, "width": 50, "height": 20},
            {"width": 101, "height": 80}, {"width": 100, "height": 81},
        ]
        for roi in invalid_roi:
            with self.subTest(roi=roi), self.assertRaisesRegex(ValueError, "roi|ROI"):
                from_camera_info({**self.data, "roi": roi})
        for binning in [-1, 0.5, float("nan"), float("inf"), True, 101]:
            with self.subTest(binning=binning), self.assertRaisesRegex(ValueError, "binning"):
                from_camera_info({**self.data, "binning_x": binning})
        with self.assertRaisesRegex(ValueError, "binning"):
            from_camera_info({**self.data, "binning_y": 81})

    def test_projection_filters_invalid_depth_nonfinite_and_bounds(self):
        xyz = np.array([[0, 0, 2], [0.5, 0, 2], [0, 0, -1], [0, 0, 0],
                        [np.nan, 0, 1], [1, 0, 1], [-0.5, -0.4, 1]])
        pixels, indices = project_points(xyz, self.calibrated())
        np.testing.assert_array_equal(indices, [0, 1, 6])
        np.testing.assert_allclose(pixels, [[50, 40], [75, 40], [0, 0]])

    def test_transform_direction_inverse_and_translation_units(self):
        camera_to_lidar = np.eye(4)
        camera_to_lidar[0, 3] = -100
        camera = calibration_from_dict({**self.data, "T_lidar_camera": camera_to_lidar.tolist(),
                                        "translation_units": "cm"})
        pixels, indices = project_points(np.array([[0, 0, 4]]), camera)
        np.testing.assert_allclose(pixels, [[75, 40]])
        np.testing.assert_array_equal(indices, [0])

    def test_image_size_scales_intrinsics_and_bounds(self):
        pixels, indices = project_points(np.array([[0, 0, 1], [0.49, 0, 1]]), self.calibrated(), (200, 160))
        np.testing.assert_allclose(pixels, [[100, 80], [198, 80]])
        np.testing.assert_array_equal(indices, [0, 1])

    def test_rotated_translated_extrinsics_are_applied_before_projection(self):
        transform = [[0, -1, 0, 0.1], [1, 0, 0, -0.2], [0, 0, 1, 1], [0, 0, 0, 1]]
        camera = self.calibrated(T_camera_lidar=transform)
        pixels, indices = project_points(np.array([[0.2, 0.1, 1]]), camera)
        np.testing.assert_allclose(pixels, [[50, 40]])
        np.testing.assert_array_equal(indices, [0])
        restored = calibration_from_dict(calibration_to_dict(camera))
        np.testing.assert_array_equal(restored.T_camera_lidar, transform)

    def test_pinhole_distortion_and_skew(self):
        camera = self.calibrated(D=[0.1, 0, 0, 0, 0], K=[[100, 10, 50], [0, 100, 40], [0, 0, 1]])
        pixels, _ = project_points(np.array([[0.2, 0.1, 1]]), camera)
        np.testing.assert_allclose(pixels, [[71.105, 50.05]])

    def test_rational_distortion_divides_radial_factor(self):
        camera = self.calibrated(distortion_model="rational_polynomial", D=[0, 0, 0, 0, 0, 1, 0, 0])
        pixels, _ = project_points(np.array([[0.5, 0, 1]]), camera)
        np.testing.assert_allclose(pixels, [[90, 40]])

    def test_equidistant_projection_handles_optical_axis(self):
        camera = self.calibrated(distortion_model="equidistant", D=[0, 0, 0, 0])
        pixels, _ = project_points(np.array([[0, 0, 1], [0.5, 0, 1]]), camera)
        np.testing.assert_allclose(pixels, [[50, 40], [96.36476090008061, 40]])

    def test_projection_empty_input_preserves_shapes(self):
        pixels, indices = project_points(np.empty((0, 3)), self.calibrated())
        self.assertEqual(pixels.shape, (0, 2))
        self.assertEqual(indices.shape, (0,))

    def test_singular_rational_distortion_is_filtered(self):
        camera = self.calibrated(distortion_model="rational_polynomial", D=[0, 0, 0, 0, 0, -4, 0, 0])
        pixels, indices = project_points(np.array([[0.5, 0, 1], [0, 0, 1]]), camera)
        np.testing.assert_array_equal(indices, [1])
        np.testing.assert_allclose(pixels, [[50, 40]])

    def test_invalid_point_shape_and_image_size_are_rejected(self):
        for points in [np.zeros((3,)), np.zeros((2, 4))]:
            with self.subTest(shape=points.shape), self.assertRaises(ValueError):
                project_points(points, self.calibrated())
        for size in [(0, 100), (100.5, 80), (100,)]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                project_points(np.zeros((0, 3)), self.calibrated(), size)

    def test_ros_yaml_file_and_workspace_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "camera.yaml"
            path.write_text("image_width: 100\nimage_height: 80\ncamera_matrix:\n  rows: 3\n  cols: 3\n"
                            "  data: [100, 0, 50, 0, 100, 40, 0, 0, 1]\n"
                            "distortion_coefficients:\n  data: [0, 0, 0, 0, 0]\n", encoding="utf-8")
            camera = load_calibration(str(path))
        restored = calibration_from_dict(json.loads(json.dumps(calibration_to_dict(camera))))
        np.testing.assert_array_equal(restored.K, self.data["K"])
        self.assertIsNone(restored.T_camera_lidar)

    def test_extrinsics_only_file_merges_camera_info_without_mutating_base(self):
        base = from_camera_info(self.data)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "extrinsics.json"
            path.write_text(json.dumps({"T_camera_lidar": np.eye(4).tolist()}), encoding="utf-8")
            camera = load_calibration(str(path), base=base)
        np.testing.assert_array_equal(camera.K, base.K)
        np.testing.assert_array_equal(camera.T_camera_lidar, np.eye(4))
        self.assertIsNone(base.T_camera_lidar)

    def test_generic_transform_requires_explicit_direction(self):
        with self.assertRaisesRegex(ValueError, "direction"):
            calibration_from_dict({**self.data, "transform": np.eye(4).tolist()})
        camera = calibration_from_dict({**self.data, "transform": np.eye(4).tolist(),
                                        "transform_direction": "lidar_to_camera"})
        np.testing.assert_array_equal(camera.T_camera_lidar, np.eye(4))

    def test_validation_rejects_invalid_geometry_and_models(self):
        cases = [
            {"K": [[0, 0, 50], [0, 100, 40], [0, 0, 1]]},
            {"K": [[100, 0, 50], [0, 100, 40], [0, 1, 1]]},
            {"K": [[np.nan, 0, 50], [0, 100, 40], [0, 0, 1]]},
            {"width": 0}, {"height": 1.5}, {"distortion_model": "unknown"},
            {"D": [1, 2]}, {"distortion_model": "equidistant", "D": []},
            {"distortion_model": "rational_polynomial", "D": [0] * 5},
            {"T_camera_lidar": np.diag([2, 1, 1, 1]).tolist()},
            {"T_camera_lidar": np.diag([-1, 1, 1, 1]).tolist()},
            {"T_camera_lidar": np.diag([1, 1, 1, 2]).tolist()},
            {"translation_units": "inch"},
            {"translation_units": []}, {"distortion_model": []},
            {"T_camera_lidar": np.eye(4).tolist(), "transform_direction": "camera_to_lidar"},
            {"T_camera_lidar": np.eye(4).tolist(), "T_lidar_camera": np.eye(4).tolist()},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                calibration_from_dict({**self.data, **changes})

    def test_untrusted_yaml_tags_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.yaml"
            path.write_text("!!python/object/apply:builtins.print ['unsafe']", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_calibration(str(path))


if __name__ == "__main__":
    unittest.main()
