"""Two-pane color controls and calibrated image/annotation integration."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication

from core.calibration import CameraCalibration
from core.calibration_files import read_calibration_bundle
from core.io.recording_types import FrameData
from core.model.entities import CloudEntity, ScalarField
from core.object_tracking import Box3D, ObjectTrack, Observation
from ui.widgets.recording_browser import BrowserStream, RecordingBrowser, _find_key
from test_recording_navigation import PreviewViewport
from test_recording_ui import MemorySource, wait_for, dispose_widget


class AnnotationPreviewViewport(PreviewViewport):
    selection_changed = Signal(object)

    def set_entities(self, entities):
        super().set_entities(entities)
        self.boxes = []  # Real viewport resets annotations on entity replacement.

    def set_annotation_boxes(self, boxes):
        self.boxes = boxes

    def set_interaction_mode(self, mode):
        self.mode = mode

    def focus_entity(self, entity):
        self.focus_count = getattr(self, "focus_count", 0) + 1


class FusionBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.browser = RecordingBrowser(viewport_factory=AnnotationPreviewViewport)

    def tearDown(self):
        self.browser.cancel()
        self.idle()
        self.browser.stop()
        dispose_widget(self.browser)

    def idle(self):
        wait_for(self.app, lambda: self.browser._worker is None and self.browser._pending is None)

    def load(self, cloud_first=True):
        points = np.array([[0, 0, 2], [1, 0, 2], [-8, 0, 2]], np.float32)
        clouds = [FrameData("pointcloud", CloudEntity(str(i), xyz=points.copy(), fields={
            "intensity": ScalarField("intensity", np.array([0., 50, 100])),
            "ring": ScalarField("ring", np.array([1., 2, 3])),
        }), i * 10**9, i * 10**9) for i in range(3)]
        images = [FrameData("image", np.full((80, 100, 3), [240, 40, 20], np.uint8),
                            i * 10**9, i * 10**9) for i in range(3)]
        self.raw_clouds = clouds
        self.source = MemorySource(dict(([("cloud", clouds), ("image", images)] if cloud_first
                                        else [("image", images), ("cloud", clouds)])))
        self.browser.set_entries([BrowserStream(self.source, stream, self.source.index[stream.id])
                                  for stream in self.source.info.streams])
        self.idle()
        self.browser.secondary.setCurrentIndex(_find_key(self.browser.secondary,
            (self.source.path, "image" if cloud_first else "cloud")))
        self.browser.compare.setChecked(True)
        self.idle()

    def calibrate(self):
        self.browser.calibration = CameraCalibration(
            np.array([[40., 0, 50], [0, 40, 40], [0, 0, 1]]), np.zeros(0),
            100, 80, T_camera_lidar=np.eye(4))
        self.browser._request_frame()
        self.idle()

    def test_color_preferences_apply_to_secondary_and_survive_navigation_reload_restore(self):
        self.load(cloud_first=False)
        browser = self.browser
        browser.set_color_field("ring")
        browser.set_colormap("inferno")
        browser.set_color_range((0., 5.))
        self.assertEqual(browser.right.cloud.color_mode, "field")
        self.assertEqual(browser.right.cloud.colormap, "inferno")
        browser.position.setValue(2)
        self.idle()
        self.assertEqual(browser.right.cloud.color_field, "ring")
        self.assertEqual(browser.right.cloud.color_range, (0., 5.))
        self.assertEqual(browser.right.cloud.colormap, "inferno")
        browser.reload_topic((self.source.path, "cloud"))
        self.idle()
        saved = browser.state()
        browser.set_colormap("default")
        browser.restore_state(saved)
        self.idle()
        self.assertEqual(browser.right.cloud.colormap, "inferno")
        self.assertEqual(browser.right.cloud.color_field, "ring")
        self.assertEqual(browser.right.cloud.color_range, (0., 5.))

    def test_image_color_is_preview_only_and_mode_change_restores_base_coloring(self):
        self.load()
        self.calibrate()
        browser = self.browser
        browser.calibration_panel.colorize.setChecked(True)
        self.idle()
        self.assertEqual(browser.left.cloud.color_mode, "rgb")
        np.testing.assert_allclose(browser.left.cloud.fields["rgb"].data[:2],
                                   np.tile(np.array([240, 40, 20]) / 255, (2, 1)), atol=1e-5)
        self.assertNotIn("rgb", self.raw_clouds[0].value.fields)
        browser.set_color_mode("intensity")
        self.assertFalse(browser.calibration_panel.colorize.isChecked())
        self.assertEqual(browser.left.cloud.color_mode, "intensity")
        self.assertNotIn("rgb", browser.left.cloud.fields)

    def test_color_choice_while_reading_reaches_new_frame(self):
        self.load()
        self.browser.position.setValue(2)
        self.browser.set_color_mode("intensity")
        self.browser.set_colormap("jet")
        self.idle()
        self.assertEqual(self.browser.left.cloud.colormap, "jet")
        self.assertEqual(self.browser.left.cloud.color_mode, "intensity")

    def test_color_edits_keep_active_preview_identity_and_raw_fields(self):
        self.load()
        cloud = self.browser.left.cloud
        reads = len(self.source.reads)
        self.browser.set_color_field("ring")
        self.browser.set_colormap("inferno")
        self.assertIs(self.browser.left.cloud, cloud)
        self.assertEqual(len(self.source.reads), reads)
        self.assertEqual(self.raw_clouds[0].value.color_mode, "height")
        self.assertEqual(self.raw_clouds[0].value.colormap, "default")

    def test_new_cloud_source_fits_once_but_adjacent_frames_preserve_view(self):
        self.load()
        viewport = self.browser.left.cloud_view
        self.assertEqual(viewport.focus_count, 1)
        self.browser.position.setValue(2)
        self.idle()
        self.assertEqual(viewport.focus_count, 1)
        other = MemorySource({"cloud": self.raw_clouds})
        other.path += ".another-capture"
        self.browser.set_entries([BrowserStream(other, stream, other.index[stream.id])
                                  for stream in other.info.streams])
        self.idle()
        self.assertEqual(viewport.focus_count, 2)

    def test_undistorted_projection_and_image_assignment_use_same_coordinates(self):
        self.load()
        self.calibrate()
        browser = self.browser
        browser.calibration = replace(browser.calibration, D=np.array([.2, -.05, 0., 0., 0.]))
        browser.undistort.setChecked(True)
        browser.overlay.setChecked(True)
        browser.calibration_panel.colorize.setChecked(True)
        self.idle()
        self.assertEqual(browser._display_calibration.D.size, 0)
        self.assertIn("投影预览", browser.status.text())
        self.assertIn("图像赋色", browser.status.text())
        self.assertEqual(browser.left.cloud.color_mode, "rgb")
        self.assertNotIn("标定显示失败", browser.status.text())

    def test_manual_box_updates_image_layer_without_reading_or_replacing_pixmap(self):
        self.load()
        self.calibrate()
        browser = self.browser
        control = browser.tracking
        entry = browser.entry(browser.primary.currentData())
        track = ObjectTrack("target", "树", {0: Observation(0, Box3D((0, 0, 5), (2, 2, 2)))})
        control._streams[control._key(entry)] = {"signature": control._entry_signature(entry),
                                                "tracks": {track.id: track}}
        control.selected_id = track.id
        control._refresh()
        self.assertEqual(len(browser.right.image.projected_boxes), 1)
        first = browser.right.image.projected_boxes[0]["segments"].copy()
        reads = len(self.source.reads)
        pixmap_key = browser.right.image._pixmap.pixmap().cacheKey()
        self.assertTrue(control.apply_box_edit(Box3D((.5, 0, 5), (2, 2, 2))))
        self.assertFalse(np.array_equal(first, browser.right.image.projected_boxes[0]["segments"]))
        self.assertEqual(len(self.source.reads), reads)
        self.assertEqual(browser.right.image._pixmap.pixmap().cacheKey(), pixmap_key)
        browser.position.setValue(2)
        self.idle()
        self.assertEqual(browser.right.image.projected_boxes, [])

    def test_color_change_keeps_boxes_in_both_cloud_and_image(self):
        self.load()
        self.calibrate()
        browser = self.browser
        entry = browser.entry(browser.primary.currentData())
        track = ObjectTrack("target", "树", {0: Observation(0, Box3D((0, 0, 5), (2, 2, 2)))})
        browser.tracking._streams[browser.tracking._key(entry)] = {
            "signature": browser.tracking._entry_signature(entry), "tracks": {track.id: track}}
        browser.tracking._refresh()
        browser.set_colormap("inferno")
        self.assertEqual(len(browser.left.cloud_view.boxes), 1)
        self.assertEqual(len(browser.right.image.projected_boxes), 1)

    def test_image_color_keeps_theme_solid_color_for_invisible_points(self):
        self.load()
        self.calibrate()
        for theme, base in (("light", [.22, .25, .30]), ("dark", [.85, .85, .88])):
            with self.subTest(theme=theme):
                self.browser.set_theme(theme)
                self.browser.set_color_mode("none")
                self.browser.calibration_panel.colorize.setChecked(True)
                self.idle()
                np.testing.assert_allclose(self.browser.left.cloud.fields["rgb"].data[2], base)

    def test_no_matched_image_clears_color_and_projected_boxes(self):
        self.load()
        self.calibrate()
        browser = self.browser
        browser.calibration_panel.colorize.setChecked(True)
        self.idle()
        browser.offset.setValue(10000)
        self.idle()
        self.assertIsNone(browser.right.frame)
        self.assertNotIn("rgb", browser.left.cloud.fields)
        self.assertEqual(browser.right.image.projected_boxes, [])

    def test_swapping_panes_preserves_fusion_and_image_boxes(self):
        self.load()
        self.calibrate()
        browser = self.browser
        entry = browser.entry(browser.primary.currentData())
        track = ObjectTrack("target", "树", {0: Observation(0, Box3D((0, 0, 5), (2, 2, 2)))},
                            category="树木")
        browser.tracking._streams[browser.tracking._key(entry)] = {
            "signature": browser.tracking._entry_signature(entry), "tracks": {track.id: track}}
        browser.tracking._refresh()
        browser.calibration_panel.colorize.setChecked(True)
        self.idle()
        browser.swap_views()
        self.idle()
        self.assertEqual(browser.left.frame.kind, "image")
        self.assertEqual(browser.right.cloud.color_mode, "rgb")
        self.assertEqual(len(browser.left.image.projected_boxes), 1)
        self.assertEqual(browser.left.image.projected_boxes[0]["category"], "树木")
        self.assertEqual(browser.right.cloud_view.boxes[0]["category"], "树木")

    def test_fusion_flags_and_display_preferences_round_trip(self):
        self.load()
        self.calibrate()
        browser = self.browser
        browser.set_colormap("viridis")
        browser.calibration_panel.colorize.setChecked(True)
        self.idle()
        saved = browser.state()
        browser.set_color_mode("intensity")
        browser.restore_state(saved)
        self.idle()
        self.assertTrue(browser.calibration_panel.colorize.isChecked())
        self.assertEqual(browser.left.cloud.color_mode, "rgb")
        self.assertEqual(browser.colormap, "viridis")

    def test_manual_intrinsics_refresh_projection_and_boxes_and_persist_after_reload(self):
        self.load()
        self.calibrate()
        browser = self.browser
        entry = browser.entry(browser.primary.currentData())
        track = ObjectTrack("target", "树", {0: Observation(0, Box3D((0, 0, 5), (2, 2, 2)))})
        browser.tracking._streams[browser.tracking._key(entry)] = {
            "signature": browser.tracking._entry_signature(entry), "tracks": {track.id: track}}
        browser.tracking._refresh()
        browser.overlay.setChecked(True)
        browser.calibration_panel.colorize.setChecked(True)
        self.idle()
        segments = browser.right.image.projected_boxes[0]["segments"].copy()
        updated = replace(browser.calibration, K=np.array([[50., 0, 55], [0, 50, 42], [0, 0, 1]]))
        browser._apply_calibration(updated, "手动修正")
        self.idle()
        self.assertFalse(np.array_equal(segments, browser.right.image.projected_boxes[0]["segments"]))
        self.assertEqual(browser._display_calibration.K[0, 0], 50)
        self.assertEqual(browser.left.cloud.color_mode, "rgb")
        saved = browser.state()
        for action in (lambda: browser.position.setValue(2), browser.reload_topic,
                       lambda: browser.restore_state(saved), browser.swap_views):
            action()
            self.idle()
            np.testing.assert_array_equal(browser.calibration.K, updated.K)
            self.assertIn("手动修正", browser.calibration_panel.calibration_label.text())

    def test_manual_calibration_disables_automatic_camera_info_replacement(self):
        self.load()
        info = {"K": [[40, 0, 50], [0, 40, 40], [0, 0, 1]], "D": [], "width": 100, "height": 80}
        camera_source = MemorySource({"camera": [FrameData("camera_info", info, i * 10**9, i * 10**9)
                                                 for i in range(3)]})
        camera_source.path += ".camera"
        browser = self.browser
        browser.set_entries(browser.entries + [BrowserStream(camera_source, stream, camera_source.index[stream.id])
                                               for stream in camera_source.info.streams])
        browser.camera.setCurrentIndex(_find_key(browser.camera, (camera_source.path, "camera")))
        self.idle()
        self.assertEqual(browser.calibration.K[0, 0], 40)
        updated = replace(browser.calibration, K=np.array([[60., 0, 55], [0, 60, 42], [0, 0, 1]]))
        browser._apply_calibration(updated, "手动修正")
        browser.position.setValue(2)  # Also invalidates an in-flight refresh.
        self.idle()
        self.assertIsNone(browser.camera.currentData())
        self.assertEqual(browser.calibration.K[0, 0], 60)
        self.assertIsNotNone(browser.state()["calibration"])

    def test_apply_calibration_updates_panel_even_before_data_is_loaded(self):
        value = CameraCalibration(np.eye(3), [], 100, 80)
        self.browser._apply_calibration(value, "新建内参")
        self.assertTrue(self.browser.calibration_panel.save_button.isEnabled())
        self.assertIn("新建内参", self.browser.calibration_panel.calibration_label.text())

    def test_export_preserves_manual_origin_and_candidate_validation_status(self):
        self.load()
        self.calibrate()
        self.browser._apply_calibration(self.browser.calibration, "手动修正内参；外参 candidate：验证 failed")
        self.idle()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            with patch("ui.widgets.recording_browser.QFileDialog.getSaveFileName", return_value=(str(path), "")):
                self.browser._save_calibration()
            document = read_calibration_bundle(str(path)).documents[0]
            self.assertIn("手动修正", document.status)
            self.assertIn("failed", document.status)
            self.assertIn("calibration_source", json.loads(path.read_text(encoding="utf-8")))


if __name__ == '__main__':
    unittest.main()
