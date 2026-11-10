"""Reload one topic without disturbing other caches or annotation state."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import unittest
from types import SimpleNamespace
import numpy as np
from PySide6.QtWidgets import QApplication
from core.io.recording_types import FrameData
from core.model.entities import CloudEntity
from test_recording_navigation import PreviewViewport
from test_recording_ui import MemorySource, dispose_widget, wait_for
from ui.widgets.recording_browser import BrowserStream, RecordingBrowser
from ui.recording_session import RecordingSessionMixin


class TopicReloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.browser = RecordingBrowser(viewport_factory=PreviewViewport)
        self.source = MemorySource({
            "lidar": [FrameData("pointcloud", CloudEntity(str(i), xyz=np.full((8, 3), i, np.float32)), i * 100_000_000, i * 100_000_000) for i in range(4)],
            "camera": [FrameData("image", np.full((8, 10, 3), i, np.uint8), i * 100_000_000, i * 100_000_000) for i in range(4)]})
        self.browser.set_entries([BrowserStream(self.source, s, self.source.index[s.id]) for s in self.source.info.streams])
        self.browser.secondary.setCurrentIndex(next(
            index for index in range(self.browser.secondary.count())
            if self.browser.secondary.itemData(index)[1] == "camera"))
        self.browser.compare.setChecked(True)
        self.idle()

    def idle(self):
        wait_for(self.app, lambda: self.browser._worker is None and self.browser._pending is None)

    def tearDown(self):
        self.browser.cancel()
        self.idle()
        self.browser.stop()
        dispose_widget(self.browser)

    def test_reload_selected_topic_rereads_only_it_and_preserves_position_and_sync(self):
        self.browser.position.setValue(3)
        self.idle()
        before = list(self.source.reads)
        old_view = self.browser.left.cloud_view
        self.browser.offset.setValue(.001)
        self.idle()
        self.assertTrue(self.browser.reload_topic())
        self.idle()
        self.assertEqual(self.browser.position.value(), 3)
        self.assertTrue(self.browser.compare.isChecked())
        self.assertEqual(self.browser.offset.value(), .001)
        self.assertEqual(self.source.reads[len(before):], [("lidar", 2)])
        self.assertIsNot(self.browser.left.cloud_view, old_view)
        self.assertEqual(self.browser._displayed_indices[0], 2)

    def test_reload_during_slow_read_stays_serial_and_discards_old_data(self):
        started, release = threading.Event(), threading.Event()
        original = self.source.read_frame
        active = 0
        maximum = 0
        def read(stream_id, index, cancel=None):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            try:
                if stream_id == "lidar" and index == 1 and not started.is_set():
                    started.set()
                    release.wait(2)
                return original(stream_id, index, cancel)
            finally:
                active -= 1
        self.source.read_frame = read
        self.browser.position.setValue(2)
        try:
            wait_for(self.app, started.is_set)
            self.assertTrue(self.browser.reload_topic())
        finally:
            release.set()
        self.idle()
        self.assertEqual(maximum, 1)
        self.assertEqual(self.browser._displayed_indices[0], 1)
        self.assertFalse(self.browser.play.isChecked())
        self.assertEqual(self.source.reads.count(("lidar", 1)), 2)

    def test_old_failed_request_does_not_clear_newer_display(self):
        frames = self.browser.current_frames
        self.browser._show_error("old failed request", self.browser._generation - 1)
        self.assertIs(self.browser.current_frames, frames)

    def test_tree_reload_of_inactive_comparison_topic_opens_and_reads_it(self):
        browser = self.browser
        topic = browser.secondary.currentData()
        browser.compare.setChecked(False)
        self.idle()
        before = len(self.source.reads)
        host = SimpleNamespace(recording_browser=browser, _recording_importing=False,
                               _show_recording_browser=lambda: None)
        RecordingSessionMixin._reload_recording_topic(host, topic)
        self.idle()
        self.assertEqual(browser.primary.currentData(), topic)
        self.assertIn(("camera", 0), self.source.reads[before:])
        self.assertEqual(browser.current_frames[0].kind, "image")
