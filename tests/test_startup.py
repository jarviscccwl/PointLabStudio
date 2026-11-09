"""Startup artwork and embedded canvas window ownership."""
import os
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from ui.startup import StartupScreen


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_stopping_viewport_drains_pending_gpu_presentation_before_deletion(self):
        from ui.viewport import PointCloudViewport

        viewport = PointCloudViewport()
        viewport.resize(320, 240)
        QTest.qWait(100)
        with self.assertNoLogs("rendercanvas", level="ERROR"):
            # Start one actual asynchronous download and destroy the widget
            # before the event loop can dispatch its completion callback.
            viewport._subwidget._draw_and_present(force_sync=False)
            viewport.stop()
            viewport.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            QTest.qWait(200)

    def test_stopping_collapsed_viewport_cancels_pending_gpu_presentation(self):
        from ui.viewport import PointCloudViewport

        viewport = PointCloudViewport()
        viewport.resize(320, 240)
        QTest.qWait(100)
        with self.assertNoLogs("rendercanvas", level="ERROR"):
            viewport._subwidget._draw_and_present(force_sync=False)
            viewport._subwidget.resize(0, 0)
            self.assertEqual(viewport.get_physical_size(), (0, 0))
            viewport.stop()
            viewport.stop()
            viewport.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            QTest.qWait(200)

    def test_splash_has_artwork_and_closes_without_extra_windows(self):
        splash = StartupScreen()
        try:
            self.assertFalse(splash.pixmap().isNull())
            self.assertEqual(splash.pixmap().deviceIndependentSize().width(), 780)
            for percent in (0, 45, 90, 100):
                splash.set_progress(percent)
                self.assertEqual(splash.progress, percent)
            self.assertTrue(splash.isVisible())
        finally:
            splash.close()
        self.assertFalse(splash.isVisible())

    def test_splash_animates_during_an_unchanged_loading_stage(self):
        splash = StartupScreen()
        try:
            before = splash.pixmap().toImage()
            QTest.qWait(120)
            self.assertNotEqual(before, splash.pixmap().toImage())
            self.assertIsNone(splash.progress)
        finally:
            splash.close()

    def test_sparse_progress_and_completion_do_not_jump_the_visible_fill(self):
        splash = StartupScreen()
        try:
            # setPixmap may service Qt events even inside a progress setter.
            # Isolate each report from timer frames, then test real animation.
            splash._timer.stop()
            splash.set_progress(0)
            splash.set_progress(80)
            self.assertEqual(splash.displayed_progress, 0)
            splash._timer.start()
            QTest.qWait(70)
            before = splash.displayed_progress
            self.assertGreater(before, 0)
            self.assertLess(before, 80)
            splash._timer.stop()
            splash.set_progress(100)
            self.assertEqual(splash.progress, 100)
            self.assertEqual(splash.displayed_progress, before)
            splash._timer.start()
            QTest.qWait(70)
            self.assertGreater(splash.displayed_progress, before)
            self.assertLess(splash.displayed_progress, 100)
        finally:
            splash.close()

    def test_unknown_activity_never_disappears_at_cycle_boundaries(self):
        splash = StartupScreen(auto_show=False)
        try:
            splash.set_progress(None)
            for elapsed_ms in (0, 1, 1750, 1800):
                with patch.object(splash._clock, "elapsed", return_value=elapsed_ms):
                    splash._paint_frame()
                frame = splash.pixmap().toImage()
                ratio = frame.devicePixelRatio()
                lit_pixels = sum(
                    frame.pixelColor(round(x * ratio), round(386 * ratio)).green() > 120
                    for x in range(44, 736)
                )
                self.assertGreater(lit_pixels, 30, f"Activity vanished at {elapsed_ms} ms")
        finally:
            splash.close()

    def test_progress_updates_cannot_reopen_a_closed_splash(self):
        splash = StartupScreen()
        splash.close()
        splash.set_progress(90)
        self.assertFalse(splash.isVisible())

    def test_unknown_progress_animates_without_implying_completion(self):
        splash = StartupScreen()
        try:
            splash.set_progress(None, "Loading components")
            QTest.qWait(60)
            self.assertIsNone(splash.progress)
            splash.set_progress(99)
            QTest.qWait(60)
            self.assertLess(splash.displayed_progress, 100)
            splash.hide()
            self.assertFalse(splash._timer.isActive())
        finally:
            splash.close()

    def test_primary_and_comparison_never_show_as_top_level_windows(self):
        from ui.main_window import MainWindow
        from ui.viewport import PointCloudViewport

        shown = []

        class Watcher(QObject):
            def eventFilter(self, watched, event):
                if event.type() == QEvent.Show and isinstance(watched, PointCloudViewport):
                    shown.append(watched.isWindow())
                return False

        watcher = Watcher()
        self.app.installEventFilter(watcher)
        try:
            with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, {"POINTLAB_HOME": home}):
                window = MainWindow()
                try:
                    window.show()
                    window.frame_browser.compare.setChecked(True)
                    self.app.processEvents()
                    self.assertFalse(window.viewport.isWindow())
                    self.assertFalse(window.frame_browser.secondary.isWindow())
                    self.assertTrue(shown)
                    self.assertNotIn(True, shown)
                finally:
                    window._project_baseline = window._project_signature()
                    window.close()
        finally:
            self.app.removeEventFilter(watcher)


class StartupProcessTests(unittest.TestCase):
    @staticmethod
    def _replies(child):
        replies = queue.Queue()

        def read_replies():
            for line in child.stdout:
                replies.put(json.loads(line))

        threading.Thread(target=read_replies, daemon=True).start()
        return replies

    def test_child_handles_progress_and_exits_when_parent_pipe_closes(self):
        self._check_child(sys.executable)

    def test_desktop_child_entry_handles_progress_without_starting_the_workbench(self):
        self._check_child(sys.executable, entry=("-m", "apps.desktop"))

    @unittest.skipUnless(os.name == "nt", "pythonw is Windows-only")
    def test_console_free_child_accepts_progress_and_exits_on_pipe_eof(self):
        self._check_child(str(Path(sys.executable).with_name("pythonw.exe")))

    def _check_child(self, executable, entry=("-m", "ui.startup")):
        child = subprocess.Popen(
            [executable, "-B", *entry, "--splash-child", "dark"],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        replies = self._replies(child)
        try:
            self.assertEqual(replies.get(timeout=10), {"ready": True})
            child.stdin.write(json.dumps({"progress": None, "message": "Importing"}) + "\n")
            child.stdin.flush()
            self.assertEqual(replies.get(timeout=3), {"progress": None})
            child.stdin.write(json.dumps({"progress": 99}) + "\n")
            child.stdin.flush()
            self.assertEqual(replies.get(timeout=3), {"progress": 99})
            child.stdin.close()
            self.assertEqual(child.wait(timeout=5), 0)
            self.assertEqual(child.stderr.read(), "")
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()
            if not child.stdin.closed:
                child.stdin.close()

    def test_controller_close_is_idempotent_and_reaps_child(self):
        from ui.startup import StartupProcess

        splash = StartupProcess()
        child = splash._process
        try:
            splash.set_progress(None, "Loading")
        finally:
            splash.close()
            splash.close()
        self.assertIsNotNone(child.poll())
        splash.set_progress(90)
        self.assertIsNone(splash._local)

    def test_controller_pipe_reaches_the_source_and_frozen_child_entries(self):
        from ui.startup import StartupProcess

        real_launch = subprocess.Popen
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                def capture_child(command, **options):
                    if frozen:
                        # A source interpreter stands in for the bundled EXE;
                        # run the real desktop dispatch with the same arguments.
                        command = [sys.executable, "-B", "-m", "apps.desktop", *command[1:]]
                    options.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    return real_launch(command, **options)

                with patch.object(sys, "frozen", frozen, create=True), \
                        patch("ui.startup.subprocess.Popen", side_effect=capture_child):
                    splash = StartupProcess()
                child = splash._process
                replies = self._replies(child)
                try:
                    self.assertEqual(replies.get(timeout=10), {"ready": True})
                    splash.set_progress(None, "A native import is still working")
                    self.assertEqual(replies.get(timeout=3), {"progress": None})
                    splash.set_progress(75, "Measured work")
                    self.assertEqual(replies.get(timeout=3), {"progress": 75})
                    splash.close()
                    self.assertEqual(child.returncode, 0)
                    self.assertEqual(child.stderr.read(), "")
                finally:
                    splash.close()
                    child.stdout.close()
                    child.stderr.close()


class StartupRoutingTests(unittest.TestCase):
    def test_child_reads_the_parent_pipe_when_windowed_python_has_no_stdin(self):
        script = """
import sys
sys.stdin = None
from ui.startup import _open_child_input
with _open_child_input() as stream:
    print(stream.read(), end="")
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", script], input="parent pipe still works\n",
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "parent pipe still works\n")

    @unittest.skipUnless(os.name == "nt", "Windows windowed executable handles")
    def test_explicit_child_pipe_survives_missing_console_streams(self):
        import msvcrt

        script = """
import os
import sys
os.close(0)
sys.stdin = None
from ui.startup import _open_child_input
with _open_child_input(int(sys.argv[1])) as stream:
    print(stream.read(), end="")
"""
        reader, writer = os.pipe()
        handle = msvcrt.get_osfhandle(reader)
        os.set_handle_inheritable(handle, True)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        with os.fdopen(writer, "w", encoding="utf-8") as stream:
            try:
                child = subprocess.Popen(
                    [sys.executable, "-B", "-c", script, str(handle)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    startupinfo=startup, text=True, encoding="utf-8",
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            finally:
                os.close(reader)
            stream.write("frozen input remains readable\n")
        try:
            stdout, stderr = child.communicate(timeout=10)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()
        self.assertEqual(child.returncode, 0, stderr)
        self.assertEqual(stdout, "frozen input remains readable\n")

    def test_frozen_controller_uses_the_executable_child_entry(self):
        from ui.startup import StartupProcess

        with patch.object(sys, "frozen", True, create=True), \
                patch("ui.startup.subprocess.Popen") as launch, \
                patch("ui.startup.StartupScreen", side_effect=AssertionError(
                    "Frozen startup must not block the splash on the importing thread")):
            splash = StartupProcess("light")
            try:
                command = launch.call_args.args[0]
                self.assertEqual(command[:3], [sys.executable, "--splash-child", "light"])
                if os.name == "nt":
                    self.assertEqual(command[3], "--splash-input")
                    inherited = launch.call_args.kwargs["startupinfo"].lpAttributeList["handle_list"]
                    self.assertEqual(inherited, [int(command[4])])
            finally:
                splash.close()

    def test_desktop_child_entry_bypasses_settings_and_the_main_window(self):
        from apps.desktop import main

        with patch.dict(sys.modules, {"core.settings": None, "ui.main_window": None}), \
                patch("ui.startup._run_splash_child", return_value=0) as child:
            with self.assertRaises(SystemExit) as result:
                main(["--splash-child", "light", "--splash-input", "456"])
        self.assertEqual(result.exception.code, 0)
        child.assert_called_once_with("light", input_handle=456)

    def test_close_kills_and_reaps_an_unresponsive_child(self):
        from ui.startup import StartupProcess

        child = subprocess.Popen(
            [sys.executable, "-B", "-c", "import time; time.sleep(30)"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            with patch("ui.startup.subprocess.Popen", return_value=child):
                splash = StartupProcess()
            # Windows terminate is forceful; simulate that first attempt losing
            # its race while retaining a real process for the final kill/reap.
            with patch.object(child, "terminate", return_value=None):
                try:
                    splash.close()
                except subprocess.TimeoutExpired:
                    self.fail("Closing startup left an unresponsive child running")
            self.assertIsNotNone(child.poll())
            splash.close()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
            if not child.stdin.closed:
                child.stdin.close()

if __name__ == "__main__":
    unittest.main()
