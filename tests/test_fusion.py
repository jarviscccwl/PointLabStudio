"""Calibrated fusion geometry, visibility and non-destructive image sampling."""
import unittest

import numpy as np

from core.calibration import CameraCalibration, project_points
from core.object_tracking import Box3D
from core.fusion import project_box_segments, sample_image_colors


def camera(**kwargs):
    values = dict(K=np.array([[40., 0, 50], [0, 40, 40], [0, 0, 1]]),
                  D=np.zeros(0), width=100, height=80, T_camera_lidar=np.eye(4))
    values.update(kwargs)
    return CameraCalibration(**values)


class FusionGeometryTests(unittest.TestCase):
    def test_color_samples_correct_pixels_and_does_not_modify_inputs(self):
        image = np.zeros((80, 100, 3), np.uint8)
        image[40, 50] = [255, 30, 10]
        image[40, 70] = [10, 180, 250]
        points = np.array([[0, 0, 2], [1, 0, 2], [0, 0, -2], [9, 0, 1],
                           [np.nan, 0, 1]], dtype=np.float32)
        original = image.copy()
        colors, indices = sample_image_colors(points, image, camera())
        np.testing.assert_array_equal(indices, [0, 1])
        np.testing.assert_allclose(colors * 255, [[255, 30, 10], [10, 180, 250]], atol=1e-4)
        np.testing.assert_array_equal(image, original)
        self.assertTrue(np.isnan(points[-1, 0]))

    def test_occluded_points_do_not_receive_foreground_color(self):
        image = np.full((80, 100, 3), 128, np.uint8)
        points = np.array([[0, 0, 4], [0, 0, 2], [0, 0, 2.02], [1, 0, 2]])
        _, indices = sample_image_colors(points, image, camera())
        np.testing.assert_array_equal(indices, [1, 2, 3])

    def test_resized_image_uses_scaled_intrinsics_and_extrinsic_translation(self):
        transform = np.eye(4)
        transform[0, 3] = 1
        image = np.zeros((40, 50, 3), np.uint8)
        image[20, 35] = [1, 128, 255]
        colors, indices = sample_image_colors(np.array([[0, 0, 2]]), image,
                                             camera(T_camera_lidar=transform))
        np.testing.assert_array_equal(indices, [0])
        np.testing.assert_allclose(colors[0] * 255, [1, 128, 255], atol=1e-4)

    def test_grayscale_and_transparent_image_are_handled(self):
        points = np.array([[0, 0, 2.]])
        colors, _ = sample_image_colors(points, np.full((80, 100), 90, np.uint8), camera())
        np.testing.assert_allclose(colors[0], np.full(3, 90 / 255))
        rgba = np.full((80, 100, 4), 255, np.uint8)
        rgba[40, 50, 3] = 0
        colors, indices = sample_image_colors(points, rgba, camera())
        self.assertEqual(len(indices), 0)
        self.assertEqual(colors.shape, (0, 3))

    def test_calibration_is_required_and_empty_cloud_is_valid(self):
        image = np.zeros((80, 100, 3), np.uint8)
        with self.assertRaisesRegex(ValueError, 'extrinsics'):
            sample_image_colors(np.zeros((1, 3)), image, camera(T_camera_lidar=None))
        colors, indices = sample_image_colors(np.empty((0, 3)), image, camera())
        self.assertEqual(colors.shape, (0, 3))
        self.assertEqual(indices.shape, (0,))

    def test_box_edges_match_known_pinhole_corners(self):
        segments = project_box_segments(Box3D((0, 0, 5), (2, 2, 2)), camera(), (100, 80))
        self.assertEqual(segments.shape[1:], (2, 2))
        self.assertGreaterEqual(len(segments), 12)
        np.testing.assert_allclose(segments.min(axis=(0, 1)), [40, 30])
        np.testing.assert_allclose(segments.max(axis=(0, 1)), [60, 50])

    def test_box_crossing_near_plane_and_image_bounds_is_clipped(self):
        box = Box3D((0, 0, .5), (3, 3, 2), yaw=.2, roll=.15)
        segments = project_box_segments(box, camera(), (100, 80))
        self.assertGreater(len(segments), 0)
        self.assertTrue(np.isfinite(segments).all())
        self.assertTrue((segments >= 0).all())
        self.assertTrue((segments[..., 0] <= 99).all())
        self.assertTrue((segments[..., 1] <= 79).all())
        self.assertEqual(project_box_segments(Box3D((0, 0, -5), (2, 2, 2)),
                                             camera(), (100, 80)).shape, (0, 2, 2))

    def test_distorted_box_edges_follow_same_model_as_points(self):
        for model, coefficients in (("plumb_bob", [.2, -.05, .01, .02, 0]),
                                    ("equidistant", [.1, -.02, 0, 0])):
            with self.subTest(model=model):
                cal = camera(D=np.asarray(coefficients), distortion_model=model)
                segments = project_box_segments(Box3D((1, .5, 4), (1, 1, 1)), cal, (100, 80))
                corner, _ = project_points(np.array([[.5, 0, 3.5]]), cal)
                self.assertLess(np.linalg.norm(segments.reshape(-1, 2) - corner, axis=1).min(), 1e-8)

    def test_rational_distortion_pole_does_not_create_cross_image_edges(self):
        cal = camera(D=np.array([0, 0, 0, 0, 0, -1., 0, 0]),
                     distortion_model="rational_polynomial")
        segments = project_box_segments(Box3D((1.5, 0, 1), (1, .1, .1)), cal, (100, 80))
        self.assertGreater(len(segments), 0)
        # Both real branches lie to the left of the principal point or outside
        # the image. Finite endpoint samples must not bridge across the pole.
        self.assertTrue((segments[..., 0] < 50).all())

    def test_fisheye_edge_near_camera_is_subpixel_accurate(self):
        from core.object_tracking import box_corners
        cal = CameraCalibration(np.array([[400., 0, 500], [0, 400, 400], [0, 0, 1]]),
                                np.zeros(4), 1000, 800, "equidistant", np.eye(4))
        box = Box3D((.65, .85, .7), (.65, 2.35, 4), yaw=-.7, roll=.65, pitch=.4)
        segments = project_box_segments(box, cal, (1000, 800))
        corners = box_corners(box)
        actual, _ = project_points(np.linspace(corners[1], corners[5], 2000), cal)
        delta = segments[:, 1] - segments[:, 0]
        for point in actual[::3]:
            fractions = np.clip(np.sum((point - segments[:, 0]) * delta, axis=1)
                                / np.maximum(np.sum(delta * delta, axis=1), 1e-20), 0, 1)
            distance = np.linalg.norm(point - segments[:, 0] - fractions[:, None] * delta, axis=1).min()
            self.assertLess(distance, .6)


if __name__ == '__main__':
    unittest.main()
