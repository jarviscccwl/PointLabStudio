"""Adjacent frame preparation must stay serial and reject stale projections."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from unittest.mock import patch
from dataclasses import replace
import numpy as np
from PySide6.QtWidgets import QApplication
from core.calibration import CameraCalibration
from core.io.recording_types import FrameData
from core.model.entities import CloudEntity
from ui.widgets.recording_browser import BrowserStream, RecordingBrowser
from test_recording_navigation import PreviewViewport
from test_recording_ui import MemorySource, wait_for, dispose_widget


class PreparedNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.browser = RecordingBrowser(viewport_factory=PreviewViewport)
        cloud = lambda i: CloudEntity(str(i), xyz=np.array([[i * .1, 0, 2]], np.float32))
        self.source = MemorySource({"cloud": [FrameData("pointcloud", cloud(i), i, i) for i in range(6)],
                                    "image": [FrameData("image", np.full((80, 100, 3), i, np.uint8), i, i) for i in range(6)]})
        self.browser.set_entries([BrowserStream(self.source, s, self.source.index[s.id]) for s in self.source.info.streams])
        self.browser.compare.setChecked(True)
        self.idle()
        self.browser._apply_calibration(CameraCalibration([[40, 0, 50], [0, 40, 40], [0, 0, 1]], [],
                                                         100, 80, T_camera_lidar=np.eye(4)), "test")
        self.browser.overlay.setChecked(True)
        self.idle()

    def idle(self):
        wait_for(self.app, lambda: self.browser._worker is None and self.browser._pending is None)

    def tearDown(self):
        self.browser.cancel()
        self.idle()
        self.browser.stop()
        dispose_widget(self.browser)

    def test_next_frame_is_prepared_without_changing_visible_frame(self):
        browser = self.browser
        browser._start_prefetch(force=True)
        self.idle()
        self.assertEqual(browser.position.value(), 1)
        self.assertEqual(browser._displayed_indices[0], 0)
        reads = len(self.source.reads)
        browser.position.setValue(2)
        self.idle()
        self.assertEqual(len(self.source.reads), reads)
        self.assertGreater(browser._prepared_hits, 0)
        self.assertEqual(browser._displayed_indices[0], 1)
        self.assertEqual(browser.right.frame.value[0, 0, 0], 1)

    def test_new_calibration_never_uses_old_prepared_projection(self):
        browser = self.browser
        browser._start_prefetch(force=True)
        self.idle()
        changed = replace(browser.calibration, K=np.array([[60, 0, 55], [0, 60, 42], [0, 0, 1]]))
        browser._apply_calibration(changed, "edited")
        browser.position.setValue(2)
        self.idle()
        np.testing.assert_array_equal(browser._display_calibration.K, changed.K)

    def test_reload_drops_prepared_frames_and_reads_source_again(self):
        browser = self.browser
        browser._start_prefetch(force=True)
        self.idle()
        reads = len(self.source.reads)
        browser.reload_topic()
        self.idle()
        self.assertGreater(len(self.source.reads), reads)

    def test_rapid_reverse_shows_final_position_and_cache_stays_bounded(self):
        for position in [2, 3, 5, 4, 2, 6, 1, 3]:
            self.browser.position.setValue(position)
        self.idle()
        self.assertEqual(self.browser._displayed_indices[0], 2)
        self.assertLessEqual(self.browser._prepared_bytes, 192 * 1024**2)

    def test_background_completion_does_not_refresh_current_box_editor(self):
        with patch.object(self.browser.tracking, 'on_frame_changed') as refresh:
            self.browser._start_prefetch(force=True)
            self.idle()
            refresh.assert_not_called()

    def test_prepared_frame_updates_annotations_once(self):
        self.browser._start_prefetch(force=True)
        self.idle()
        tracking = self.browser.tracking
        with patch.object(tracking, '_refresh', wraps=tracking._refresh) as refresh:
            self.browser.position.setValue(2)
            self.idle()
        self.assertEqual(self.browser._displayed_indices[0], 1)
        self.assertEqual(refresh.call_count, 1)

    def test_bad_neighbor_is_not_retried_until_reload(self):
        read = self.source.read_frame
        attempts = []
        def broken(stream, index, *args, **kwargs):
            if index == 1:
                attempts.append((stream, index))
                raise ValueError('damaged frame')
            return read(stream, index, *args, **kwargs)
        self.source.read_frame = broken
        self.browser._start_prefetch(force=True)
        self.idle()
        failed = len(attempts)
        for _ in range(3):
            self.browser._start_prefetch(force=True)
            self.idle()
        self.assertEqual(len(attempts), failed)
        self.browser.reload_topic()
        self.idle()
        self.browser._start_prefetch(force=True)
        self.idle()
        self.assertGreater(len(attempts), failed)

    def test_disconnected_source_fails_in_worker_without_stranded_request(self):
        with patch.object(self.source, 'cache_token', side_effect=OSError('drive disconnected')):
            self.browser.position.setValue(2)
            self.idle()
            self.assertIn('drive disconnected', self.browser.status.text())
            self.assertFalse(self.browser.current_frames)

    def test_disabled_browser_never_starts_background_read(self):
        self.browser.setEnabled(False)
        self.browser._start_prefetch(force=True)
        self.assertIsNone(self.browser._worker)

    def test_cached_playback_still_respects_selected_frame_rate(self):
        import time
        browser = self.browser
        browser._start_prefetch(force=True)
        self.idle()
        browser.play.setChecked(True)
        browser._read_started_at = time.monotonic() - 2
        browser.position.setValue(2)
        self.assertGreater(browser.timer.remainingTime(), 50)


if __name__ == "__main__":
    unittest.main()
