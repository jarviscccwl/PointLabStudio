"""Calibration readiness, inspectable parameters and projection display contracts."""

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.calibration import calibration_from_dict
from core.calibration_display import projection_colors
from ui.widgets.calibration_controls import CalibrationPanel


def camera(with_extrinsics=False):
    return calibration_from_dict({
        "K": [[100, 2, 50], [0, 120, 40], [0, 0, 1]],
        "D": [0.1, -0.05, 0, 0, 0],
        "width": 100,
        "height": 80,
        "T_camera_lidar": (
            [[0, 0, 1, 0.2], [0, 1, 0, -0.1], [-1, 0, 0, 3], [0, 0, 0, 1]]
            if with_extrinsics else None
        ),
    })


class CalibrationPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.panel = CalibrationPanel()
        self.addCleanup(self.panel.close)

    def test_missing_calibration_cannot_enable_processing_or_save(self):
        self.panel.set_calibration(None, has_image=True, has_cloud=True)
        self.assertIsNone(self.panel.camera.currentData())
        self.assertFalse(self.panel.undistort.isEnabled())
        self.assertFalse(self.panel.overlay.isEnabled())
        self.assertFalse(self.panel.save_button.isEnabled())
        self.assertFalse(self.panel.details_button.isEnabled())
        self.assertIn("内参", self.panel.calibration_label.text())
        self.assertIn("外参", self.panel.calibration_label.text())

    def test_intrinsics_enable_undistortion_without_inventing_extrinsics(self):
        calibration = camera()
        self.panel.set_calibration(calibration, "包内相机", has_image=True, has_cloud=True)
        self.assertTrue(self.panel.undistort.isEnabled())
        self.assertFalse(self.panel.overlay.isEnabled())
        self.assertTrue(self.panel.save_button.isEnabled())
        self.assertTrue(self.panel.reset_button.isEnabled())
        self.assertIn("外参缺失", self.panel.calibration_label.text())
        restored = calibration_from_dict(json.loads(self.panel.parameters.toPlainText()))
        self.assertIsNone(restored.T_camera_lidar)
        self.assertIsNone(calibration.T_camera_lidar)

    def test_processing_requires_actual_image_and_cloud_frames(self):
        calibration = camera(True)
        for has_image, has_cloud, undistort, overlay in (
            (False, False, False, False),
            (False, True, False, False),
            (True, False, True, False),
            (True, True, True, True),
        ):
            with self.subTest(image=has_image, cloud=has_cloud):
                self.panel.set_calibration(
                    calibration, has_image=has_image, has_cloud=has_cloud
                )
                self.assertEqual(self.panel.undistort.isEnabled(), undistort)
                self.assertEqual(self.panel.overlay.isEnabled(), overlay)

    def test_projection_style_is_editable_only_for_an_applicable_enabled_overlay(self):
        self.panel.set_calibration(camera(True), has_image=True, has_cloud=True)
        self.assertFalse(self.panel.projection_color.isEnabled())
        self.assertTrue(self.panel.projection_options.isHidden())
        self.panel.overlay.setChecked(True)
        self.assertFalse(self.panel.projection_options.isHidden())
        for control in (self.panel.projection_color, self.panel.projection_opacity,
                        self.panel.projection_size):
            self.assertTrue(control.isEnabled())
        self.panel.set_calibration(camera(), has_image=True, has_cloud=True)
        self.assertFalse(self.panel.projection_color.isEnabled())

    def test_readiness_refresh_preserves_selections_without_emitting_changes(self):
        self.panel.set_calibration(camera(True), has_image=True, has_cloud=True)
        self.panel.overlay.setChecked(True)
        self.panel.undistort.setChecked(True)
        events = []
        self.panel.overlay.toggled.connect(lambda value: events.append(value))
        self.panel.undistort.toggled.connect(lambda value: events.append(value))
        self.panel.display_changed.connect(lambda: events.append("display"))
        self.panel.set_calibration(camera(), has_image=False, has_cloud=False)
        self.assertTrue(self.panel.overlay.isChecked())
        self.assertTrue(self.panel.undistort.isChecked())
        self.assertEqual(events, [])

    def test_details_are_read_only_roundtrippable_and_explain_units_and_direction(self):
        calibration = camera(True)
        self.panel.set_calibration(calibration, "camera.yaml", has_image=True, has_cloud=True)
        self.assertTrue(self.panel.parameters.isReadOnly())
        data = json.loads(self.panel.parameters.toPlainText())
        self.assertEqual(data["K"], [[100, 2, 50], [0, 120, 40], [0, 0, 1]])
        self.assertEqual(data["D"], [0.1, -0.05, 0, 0, 0])
        self.assertEqual(data["translation_units"], "m")
        self.assertEqual(data["T_camera_lidar"][0], [0, 0, 1, 0.2])
        restored = calibration_from_dict(data)
        np.testing.assert_array_equal(restored.T_camera_lidar, calibration.T_camera_lidar)
        self.assertIn("雷达 → 相机", self.panel.parameter_help.text())
        self.assertIn("px", self.panel.parameter_help.text())
        self.assertIn("无量纲", self.panel.parameter_help.text())
        self.assertIn("米", self.panel.parameter_help.text())

    def test_errors_are_visible_as_plain_text_and_resettable(self):
        self.panel.set_calibration(None, error="错误 <camera>：矩阵不完整")
        self.assertIn("矩阵不完整", self.panel.calibration_label.text())
        self.assertEqual(self.panel.calibration_label.textFormat(), Qt.PlainText)
        self.assertTrue(self.panel.reset_button.isEnabled())
        self.panel.set_calibration(None)
        self.assertNotIn("矩阵不完整", self.panel.calibration_label.text())
        self.assertEqual(self.panel.parameters.toPlainText(), "")

    def test_display_changes_emit_user_values_and_restore_without_signals(self):
        events = []
        self.panel.display_changed.connect(lambda: events.append(self.panel.display_options()))
        self.assertEqual(self.panel.display_options(), {"color": "depth", "opacity": 0.7, "size": 2})
        self.panel.projection_color.setCurrentIndex(self.panel.projection_color.findData("height"))
        self.panel.projection_opacity.setValue(35)
        self.panel.projection_size.setValue(4)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[-1], {"color": "height", "opacity": 0.35, "size": 4})
        self.panel.set_display_options({"color": "solid", "opacity": 0.8, "size": 3})
        self.assertEqual(len(events), 3)
        self.assertEqual(self.panel.display_options(), {"color": "solid", "opacity": 0.8, "size": 3})

    def test_restored_display_values_are_bounded_and_invalid_values_preserve_current(self):
        self.panel.set_display_options({"color": "height", "opacity": 4, "size": -1})
        self.assertEqual(self.panel.display_options(), {"color": "height", "opacity": 1.0, "size": 1})
        self.panel.set_display_options({"color": "unknown", "opacity": float("nan"), "size": "bad"})
        self.assertEqual(self.panel.display_options(), {"color": "height", "opacity": 1.0, "size": 1})
        self.panel.set_display_options({"opacity": -2, "size": 30})
        self.assertEqual(self.panel.display_options(), {"color": "height", "opacity": 0.1, "size": 8})


class ProjectionColorTests(unittest.TestCase):
    def test_depth_uses_camera_z_and_height_uses_lidar_z(self):
        xyz = np.array([[-2, 0, -10], [0, 0, 20], [2, 0, 10]], dtype=float)
        original = xyz.copy()
        depth = projection_colors(xyz, camera(True), "depth")
        height = projection_colors(xyz, camera(True), "height")
        np.testing.assert_array_equal(depth[[0, 2]], [[250, 235, 56], [20, 51, 153]])
        np.testing.assert_array_equal(height[[0, 1]], [[20, 51, 153], [250, 235, 56]])
        np.testing.assert_array_equal(xyz, original)
        self.assertEqual(depth.dtype, np.uint8)

    def test_depth_requires_explicit_extrinsics(self):
        for calibration in (None, camera()):
            with self.subTest(calibration=calibration), self.assertRaisesRegex(ValueError, "T_camera_lidar"):
                projection_colors(np.array([[0, 0, 1]]), calibration, "depth")

    def test_colors_are_quantized_to_sixteen_existing_palette_samples(self):
        xyz = np.zeros((1024, 3))
        xyz[:, 2] = np.linspace(-10, 10, len(xyz))
        colors = projection_colors(xyz, None, "height")
        self.assertEqual(colors.shape, (1024, 3))
        self.assertEqual(len(np.unique(colors, axis=0)), 16)
        np.testing.assert_array_equal(colors[[0, -1]], [[20, 51, 153], [250, 235, 56]])

    def test_solid_empty_and_constant_inputs_keep_color_shape(self):
        xyz = np.array([[0, 0, 1], [0, 0, 1]])
        np.testing.assert_array_equal(
            projection_colors(xyz, None, "solid"), [[40, 230, 140], [40, 230, 140]]
        )
        np.testing.assert_array_equal(
            projection_colors(xyz, None, "height"), [[20, 51, 153], [20, 51, 153]]
        )
        for mode in ("depth", "height", "solid"):
            colors = projection_colors(np.empty((0, 3)), camera(True), mode)
            self.assertEqual(colors.shape, (0, 3))
            self.assertEqual(colors.dtype, np.uint8)

    def test_invalid_points_modes_and_transformed_depth_are_rejected(self):
        for xyz in (np.zeros(3), np.zeros((2, 4)), [[0, 0, np.nan]], [[0, np.inf, 1]], [["x", 0, 1]]):
            with self.subTest(xyz=xyz), self.assertRaises(ValueError):
                projection_colors(xyz, camera(True), "solid")
        with self.assertRaisesRegex(ValueError, "mode"):
            projection_colors(np.zeros((1, 3)), camera(True), "unknown")
        calibration = camera(True)
        calibration.T_camera_lidar[2, 0] = np.nan
        with self.assertRaisesRegex(ValueError, "finite"):
            projection_colors(np.zeros((1, 3)), calibration, "depth")


if __name__ == "__main__":
    unittest.main()
