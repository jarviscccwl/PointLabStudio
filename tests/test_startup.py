"""Startup artwork and embedded canvas window ownership."""
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication
from ui.startup import StartupScreen


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_splash_has_artwork_and_closes_without_extra_windows(self):
        splash = StartupScreen()
        try:
            self.assertFalse(splash.pixmap().isNull())
            self.assertEqual(splash.pixmap().deviceIndependentSize().width(), 780)
            for percent in (0, 45, 90, 100):
                splash.set_progress(percent)
                self.assertEqual(splash.progress, percent)
                pixel = splash.pixmap().toImage()
                ratio = pixel.devicePixelRatio()
                color = pixel.pixelColor(round(390 * ratio), round(389 * ratio)).name()
                self.assertEqual(color, splash.colors["accent"].lower() if percent >= 90
                                 else splash.colors["panel3"].lower())
            self.assertTrue(splash.isVisible())
        finally:
            splash.close()
        self.assertFalse(splash.isVisible())

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


if __name__ == "__main__":
    unittest.main()
