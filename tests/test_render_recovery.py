"""A failed GPU presentation must not leave later cloud frames frozen."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication
from core.model.entities import CloudEntity
from ui.viewport import PointCloudViewport
from test_recording_ui import dispose_widget


class RenderRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = PointCloudViewport()
        self.view.resize(420, 300)
        self.view.show()
        self.view.set_entities([CloudEntity("before", xyz=np.random.default_rng(1).normal(size=(200, 3)).astype(np.float32))])
        self.spin(.15)

    def tearDown(self):
        self.view.stop()
        dispose_widget(self.view)

    def spin(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)

    def test_transient_overlay_error_preserves_dirty_frame_and_next_draw(self):
        self.view._needs_draw = True
        with patch.object(self.view._annotation_overlay, "update_labels", side_effect=ValueError("bad label")):
            try:
                self.view._animate()
            except ValueError:
                self.fail("Overlay exception escaped the draw callback and stopped scheduling")
        self.assertTrue(self.view._needs_draw)
        self.spin(.2)
        self.assertFalse(self.view._needs_draw)

    def test_failed_async_present_recovers_and_presents_later_cloud(self):
        context = self.view._subwidget._canvas_context
        original = context._rc_present
        failed = []
        def fail_once(*args, **kwargs):
            if not failed:
                failed.append(True)
                raise RuntimeError("simulated one-off bitmap present failure")
            return original(*args, **kwargs)
        with self.assertLogs("rendercanvas", level="ERROR"):
            with patch.object(context, "_rc_present", side_effect=fail_once):
                self.view._needs_draw = True
                self.view.request_draw()
                self.spin(.15)
        self.assertTrue(failed)
        old_image = self.view._subwidget._last_image[0].copy()
        cloud = CloudEntity("after", xyz=np.random.default_rng(3).normal(size=(500, 3)).astype(np.float32))
        self.view.set_entities([cloud])
        self.spin(2.2)
        self.assertNotEqual(self.view._subwidget._last_image[0], old_image,
                            "Frame data changed but the actual Qt bitmap stayed frozen")
        self.assertFalse(self.view._needs_draw)

    def test_hidden_view_resumes_drawing_new_data_and_stops_watchdog_on_close(self):
        self.view.hide()
        self.view.set_entities([CloudEntity("changed", xyz=np.ones((8, 3), np.float32))])
        self.spin(.1)
        self.view.show()
        self.spin(.25)
        self.assertFalse(self.view._needs_draw)
        self.view.stop()
        self.assertFalse(self.view._render_watchdog.isActive())

    def test_successfully_presented_recoveries_do_not_report_a_stuck_cloud(self):
        problems = []
        self.view.render_problem.connect(problems.append)
        self.view.force_draw()
        previous = self.view._subwidget._last_image[0].copy()
        for seed in (11, 12, 13, 14):
            cloud = CloudEntity("updated", xyz=np.random.default_rng(seed).normal(size=(400, 3)).astype(np.float32))
            self.view.set_entities([cloud])
            # A delayed Qt callback is not a failed presentation. Exercise the
            # watchdog with a real synchronous recovery on each new cloud.
            self.view._last_frame_time -= 2
            self.view._check_render_health()
            presented = self.view._subwidget._last_image[0].copy()
            self.assertNotEqual(presented, previous)
            previous = presented
        self.assertEqual(problems, [], "Successful redraws must not pause playback")

    def test_repeated_failed_presentations_still_report_a_problem(self):
        self.view.force_draw()
        problems = []
        self.view.render_problem.connect(problems.append)
        context = self.view._subwidget._canvas_context
        with self.assertLogs("rendercanvas", level="ERROR"):
            with patch.object(context, "_rc_present", side_effect=RuntimeError("display unavailable")):
                for _ in range(3):
                    self.view._last_frame_time -= 2
                    self.view._check_render_health()
        self.assertEqual(len(problems), 1)
