"""Playback timing and keyboard actions through the real recording browser."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.io.recording_types import FrameData
from test_recording_navigation import PreviewViewport
from test_recording_ui import MemorySource, dispose_widget, wait_for
from ui.widgets.recording_browser import BrowserStream, RecordingBrowser


class PlaybackControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.browser = RecordingBrowser(viewport_factory=PreviewViewport)
        self.source = MemorySource({"camera": [
            FrameData("image", np.full((12, 16, 3), i, np.uint8), i, i)
            for i in range(4)
        ]})
        stream = self.source.info.streams[0]
        self.browser.set_entries([BrowserStream(self.source, stream, self.source.index[stream.id])])
        self.idle()

    def idle(self):
        wait_for(self.app, lambda: self.browser._worker is None and self.browser._pending is None)

    def tearDown(self):
        self.browser.cancel()
        self.idle()
        self.browser.stop()
        dispose_widget(self.browser)

    def test_start_playing_loaded_frame_does_not_present_it_again(self):
        presented = []
        self.browser.frame_changed.connect(presented.append)
        self.browser.rate.setValue(.1)
        self.browser.play.setChecked(True)
        self.idle()
        self.assertEqual(presented, [])
        self.assertTrue(self.browser.timer.isActive())
        self.assertEqual(self.browser.position.value(), 1)

    def test_read_time_is_part_of_requested_frame_interval(self):
        original_read = self.source.read_frame
        def slow_read(stream, index, cancel=None):
            time.sleep(.25)
            return original_read(stream, index, cancel)
        self.source.read_frame = slow_read
        self.browser.rate.setValue(2)
        self.browser.play.setChecked(True)
        self.browser.position.setValue(2)
        self.idle()
        self.browser.timer.stop()
        self.assertLessEqual(self.browser.timer.interval(), 300)
        self.assertEqual(self.browser._displayed_indices[0], 1)

    def test_rate_change_reschedules_wait_and_pause_during_read_does_not_restart(self):
        self.browser.rate.setValue(.1)
        self.browser.play.setChecked(True)
        self.idle()
        self.browser.rate.setValue(2)
        self.assertLessEqual(self.browser.timer.interval(), 500)
        self.browser.position.setValue(2)
        self.browser.play.setChecked(False)
        self.idle()
        self.assertFalse(self.browser.timer.isActive())
        self.assertEqual(self.browser.position.value(), 2)

    def test_play_from_final_frame_restarts_at_first_frame(self):
        self.browser.position.setValue(4)
        self.idle()
        self.browser.rate.setValue(.1)
        self.browser.play.setChecked(True)
        self.idle()
        self.assertTrue(self.browser.play.isChecked())
        self.assertEqual(self.browser._displayed_indices[0], 0)

    def test_preview_shortcuts_seek_and_toggle_without_hijacking_frame_input(self):
        self.browser.resize(900, 600)
        self.browser.show()
        self.browser.activateWindow()
        image = self.browser.left.image
        image.setFocus()
        QTest.qWait(50)
        QTest.keyClick(image, Qt.Key_End)
        self.idle()
        self.assertEqual(self.browser.position.value(), 4)
        QTest.keyClick(image, Qt.Key_Home)
        self.idle()
        self.assertEqual(self.browser.position.value(), 1)
        self.browser.rate.setValue(.1)
        QTest.keyClick(image, Qt.Key_Space)
        self.assertTrue(self.browser.play.isChecked())
        QTest.keyClick(image, Qt.Key_Space)
        self.assertFalse(self.browser.play.isChecked())
        self.browser.position.setValue(3)
        self.idle()
        self.browser.position.setFocus()
        QTest.keyClick(self.browser.position, Qt.Key_Home)
        self.assertEqual(self.browser.position.value(), 3)


if __name__ == "__main__":
    unittest.main()
