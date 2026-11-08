"""End-user navigation, comparison and contextual recording controls."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import Signal, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from core.io.recording_types import FrameData
from core.model.entities import CloudEntity
from ui.widgets.recording_browser import BrowserStream, RecordingBrowser
from test_recording_ui import MemorySource, wait_for


class PreviewViewport(QWidget):
    files_dropped = Signal(list)
    key_cycle_color = Signal()
    key_reset_view = Signal()
    key_point_size = Signal(float)
    key_next_file = Signal()
    key_prev_file = Signal()
    fps_updated = Signal(float)

    def __init__(self, theme="dark", parent=None):
        super().__init__(parent)
    def set_entities(self, entities):
        self.entities = entities
    def focus_entity(self, entity):
        pass
    def reset_view(self):
        pass
    def set_point_size(self):
        pass
    def set_theme(self, theme):
        pass
    def stop(self):
        pass


class RecordingNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.browser = RecordingBrowser(viewport_factory=PreviewViewport)

    def tearDown(self):
        self.browser.cancel()
        wait_for(self.app, lambda: self.browser._worker is None)
        self.browser.stop()
        self.browser.close()

    def idle(self):
        wait_for(self.app, lambda: self.browser._worker is None and self.browser._pending is None)

    def load(self, frames):
        self.source = MemorySource(frames)
        self.browser.set_entries([BrowserStream(self.source, stream, self.source.index[stream.id])
                                  for stream in self.source.info.streams])
        self.idle()
        return self.source

    def test_single_view_hides_comparison_and_advanced_controls(self):
        self.load({"text": [FrameData("text", "content")]})
        self.assertTrue(self.browser.secondary.isHidden())
        self.assertTrue(self.browser.settings_panel.isHidden())
        self.assertFalse(self.browser.jump_button.isEnabled())
        self.assertFalse(self.browser.play.isEnabled())
        self.assertTrue(self.browser.comparison_row.isHidden())
        self.assertTrue(self.browser.status.isHidden())

    def test_settings_do_not_take_preview_space_and_close_resets_button(self):
        self.browser.resize(820, 560)
        self.browser.show()
        self.app.processEvents()
        before = self.browser.panes.size()
        self.browser.settings_button.click()
        self.app.processEvents()
        self.assertTrue(self.browser.settings_panel.isVisible())
        self.assertFalse(self.browser.settings_panel.isModal())
        self.assertEqual(self.browser.panes.size(), before)
        QTest.keyClick(self.browser.settings_panel, Qt.Key_Escape)
        self.assertFalse(self.browser.settings_button.isChecked())
        self.assertTrue(self.browser.settings_panel.isHidden())

    def test_dragging_timeline_reads_only_released_position(self):
        source = self.load({"imu": [FrameData("imu", {"i": i}, i, i) for i in range(20)]})
        reads = len(source.reads)
        self.browser.slider.setSliderDown(True)
        self.browser.slider.setSliderPosition(5)
        self.browser.slider.setSliderPosition(15)
        self.app.processEvents()
        self.assertEqual(self.browser.position.value(), 1)
        self.assertEqual(len(source.reads), reads)
        self.browser.slider.setSliderDown(False)
        self.idle()
        self.assertEqual(self.browser.position.value(), 16)
        self.assertEqual(source.reads[-1], ("imu", 15))
        self.assertEqual(len(source.reads), reads + 1)

    def test_projection_style_reaches_image_and_round_trips(self):
        from core.calibration import calibration_from_dict

        cloud = CloudEntity("cloud", xyz=np.array([[0, 0, 2], [0, 0, -1]], dtype=np.float32))
        self.load({"image": [FrameData("image", np.zeros((80, 100, 3), np.uint8), 0, 0)],
                   "lidar": [FrameData("pointcloud", cloud, 0, 0)]})
        self.browser.compare.setChecked(True)
        self.idle()
        self.browser.calibration = calibration_from_dict({
            "K": [[100, 0, 50], [0, 100, 40], [0, 0, 1]], "D": [],
            "width": 100, "height": 80, "T_camera_lidar": np.eye(4).tolist(),
        })
        self.browser.calibration_panel.set_display_options({"color": "solid", "size": 4, "opacity": .4})
        with patch.object(self.browser.left.image, "set_image", wraps=self.browser.left.image.set_image) as draw:
            self.browser.overlay.setChecked(True)
            self.idle()
            args = draw.call_args.args
            np.testing.assert_array_equal(args[2], [[40, 230, 140]])
            self.assertEqual(args[3:], (4, .4))
        self.assertFalse(self.browser.calibration_panel.projection_options.isHidden())
        state = self.browser.state()
        self.browser.calibration_panel.set_display_options({"color": "depth", "size": 1})
        self.browser.restore_state(state)
        self.idle()
        self.assertEqual(self.browser.calibration_panel.display_options(), state["projection_style"])
        self.assertFalse(self.browser.comparison_row.isHidden())

    def test_time_jump_uses_selected_clock_and_unsorted_original_indices(self):
        source = self.load({"imu": [FrameData("imu", {"index": i}, None, time)
                                   for i, time in enumerate((2_000_000_000, 1_000_000_000, 1_500_000_000))]})
        self.assertTrue(self.browser.jump_to_time(.49))
        self.idle()
        self.assertEqual(self.browser.position.value(), 3)
        self.assertEqual(self.browser.current_frames[0].value["index"], 2)
        self.browser.basis.setCurrentIndex(1)
        self.idle()
        self.assertFalse(self.browser.jump_button.isEnabled())
        self.assertFalse(self.browser.jump_to_time(.49))

    def test_comparison_prefers_camera_for_lidar_instead_of_diagnostics(self):
        cloud = CloudEntity("cloud", xyz=np.array([[0, 0, 2]], dtype=np.float32))
        source = self.load({"diagnostics": [FrameData("message", {}, 0, 0)],
                            "imu": [FrameData("imu", {}, 0, 0)],
                            "image": [FrameData("image", np.zeros((5, 6, 3), np.uint8), 0, 0)],
                            "lidar": [FrameData("pointcloud", cloud, 0, 0)]})
        self.browser.select_stream((source.path, "lidar"))
        self.browser.compare.setChecked(True)
        self.idle()
        self.assertEqual(self.browser.secondary.currentData(), (source.path, "image"))
        self.assertEqual(self.browser.right.frame.kind, "image")

    def test_swap_uses_accepted_secondary_frame_and_reverses_offset(self):
        source = self.load({
            "a": [FrameData("text", "left", 100_000_000, 100_000_000)],
            "b": [FrameData("text", f"right{i}", stamp, stamp)
                  for i, stamp in enumerate((50_000_000, 95_000_000, 300_000_000))],
        })
        self.browser.compare.setChecked(True)
        self.browser.offset.setValue(5)
        self.idle()
        self.browser.swap_views()
        self.idle()
        self.assertEqual(self.browser.primary.currentData(), (source.path, "b"))
        self.assertEqual(self.browser.position.value(), 2)
        self.assertEqual(self.browser.offset.value(), -5)
        self.assertEqual(self.browser.left.frame.value, "right1")
        self.assertEqual(self.browser.right.frame.value, "left")

    def test_secondary_export_uses_secondary_payload_and_frame_index(self):
        self.load({"a": [FrameData("text", "left", 100, 100)],
                   "b": [FrameData("text", f"right{i}", stamp, stamp)
                         for i, stamp in enumerate((0, 100, 200))]})
        self.browser.compare.setChecked(True)
        self.idle()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "right.txt"
            with patch("ui.widgets.recording_browser.QFileDialog.getSaveFileName", return_value=(str(path), "")) as choose:
                self.browser.export_current(side=1)
            self.assertEqual(path.read_text(encoding="utf-8"), "right1")
            self.assertIn("b_000001_100", choose.call_args.args[2])

    def test_details_include_both_precise_clocks_without_expanding_by_default(self):
        self.load({"imu": [FrameData("imu", {"x": 1}, 1700000000123456789, 1700000000123556789)]})
        self.assertFalse(self.browser.left.details_toggle.isChecked())
        text = self.browser.left.details.toPlainText()
        self.assertIn("1700000000123456789", text)
        self.assertIn("1700000000123556789", text)


if __name__ == "__main__":
    unittest.main()
