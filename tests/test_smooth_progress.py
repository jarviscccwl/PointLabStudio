"""Real progress stays immediate while its visual fill eases toward it."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QStyleFactory

from ui.theme.style import build_stylesheet
from ui.theme.tokens import get_theme
from ui.widgets.smooth_progress import SmoothProgressBar, SmoothProgressDialog


class SmoothProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def bar(self):
        bar = SmoothProgressBar()
        bar.resize(300, 20)
        bar.setValue(0)
        bar.show()
        self.widgets.append(bar)
        QTest.qWait(20)
        return bar

    def test_actual_value_and_signal_are_immediate_but_fill_interpolates(self):
        bar = self.bar()
        signals = QSignalSpy(bar.valueChanged)
        bar.setValue(80)
        self.assertEqual(bar.value(), 80)
        self.assertEqual(signals.count(), 1)
        self.assertLess(bar.displayed_value, 80)
        QTest.qWait(70)
        self.assertGreater(bar.displayed_value, 0)
        self.assertLess(bar.displayed_value, 80)
        QTest.qWait(700)
        self.assertAlmostEqual(bar.displayed_value, 80, places=2)
        self.assertEqual(signals.count(), 1)

    def test_first_visible_report_eases_from_empty(self):
        bar = SmoothProgressBar()
        self.widgets.append(bar)
        bar.show()
        bar.setValue(80)
        self.assertEqual(bar.value(), 80)
        self.assertEqual(bar.displayed_value, 0)
        QTest.qWait(70)
        self.assertGreater(bar.displayed_value, 0)
        self.assertLess(bar.displayed_value, 80)

    def test_completion_eases_without_delaying_the_real_value(self):
        bar = self.bar()
        bar.setValue(80)
        QTest.qWait(70)
        before = bar.displayed_value
        bar.setValue(100)
        self.assertEqual(bar.value(), 100)
        self.assertEqual(bar.text(), "100%")
        self.assertEqual(bar.displayed_value, before)
        QTest.qWait(70)
        self.assertGreater(bar.displayed_value, before)
        self.assertLess(bar.displayed_value, 100)

    def test_busy_fill_moves_and_hidden_fill_stops(self):
        bar = self.bar()
        bar.setRange(0, 0)
        phase = getattr(bar, "busy_phase", 0)
        QTest.qWait(70)
        self.assertNotEqual(getattr(bar, "busy_phase", 0), phase)
        bar.hide()
        phase = bar.busy_phase
        QTest.qWait(50)
        self.assertEqual(bar.busy_phase, phase)
        self.assertFalse(bar._timer.isActive())
        bar.show()
        QTest.qWait(50)
        self.assertNotEqual(bar.busy_phase, phase)
        bar.close()
        self.assertFalse(bar._timer.isActive())

    def test_busy_activity_stays_visible_across_its_cycle(self):
        original = self.app.styleSheet()
        try:
            self.app.setStyleSheet("")
            bar = self.bar()
            bar.setTextVisible(False)
            palette = bar.palette()
            palette.setColor(QPalette.Highlight, QColor("#A94DC1"))
            bar.setPalette(palette)
            empty = bar.grab().toImage()
            bar.setRange(0, 0)
            bar._timer.stop()
            for phase in (0.0, 0.5, 0.99):
                bar.busy_phase = phase
                frame = bar.grab().toImage()
                contrasted = sum(
                    max(abs(a - b) for a, b in zip(
                        frame.pixelColor(x, y).getRgb()[:3],
                        empty.pixelColor(x, y).getRgb()[:3])) > 25
                    for y in range(frame.height()) for x in range(frame.width())
                )
                self.assertGreater(contrasted, 20, f"Busy activity vanished at phase {phase}")
        finally:
            self.app.setStyleSheet(original)

    def test_busy_activity_uses_the_current_light_and_dark_theme_accent(self):
        original = self.app.styleSheet()
        try:
            self.app.setStyleSheet("")
            for style_name in (None, "Fusion"):
                bar = self.bar()
                if style_name is not None:
                    style = QStyleFactory.create(style_name)
                    style.setParent(bar)
                    bar.setStyle(style)
                bar.setTextVisible(False)
                bar.setRange(0, 0)
                for theme in ("dark", "light"):
                    self.app.setStyleSheet(build_stylesheet(theme))
                    bar._timer.stop()
                    accent = QColor(get_theme(theme)["accent"]).getRgb()[:3]
                    for phase in (0.0, 0.5, 0.99):
                        with self.subTest(style=style_name, theme=theme, phase=phase):
                            bar.busy_phase = phase
                            frame = bar.grab().toImage()
                            accented = sum(
                                max(abs(a - b) for a, b in zip(
                                    frame.pixelColor(x, y).getRgb()[:3], accent)) <= 10
                                for y in range(frame.height()) for x in range(frame.width())
                            )
                            self.assertGreater(accented, 20)
                bar.close()
        finally:
            self.app.setStyleSheet(original)

    def test_regression_and_range_changes_never_draw_ahead_of_actual(self):
        bar = self.bar()
        bar.setValue(85)
        QTest.qWait(70)
        bar.setValue(10)
        self.assertLessEqual(bar.displayed_value, 10)
        bar.setRange(20, 40)
        bar.setValue(30)
        self.assertGreaterEqual(bar.displayed_value, 20)
        self.assertLessEqual(bar.displayed_value, 30)
        bar.setValue(40)
        self.assertLess(bar.displayed_value, 40)
        bar.reset()
        self.assertEqual(bar.displayed_value, 20)
        self.assertFalse(bar._timer.isActive())

    def test_deleting_an_active_busy_bar_destroys_its_animation_timer(self):
        bar = self.bar()
        bar.setRange(0, 0)
        self.assertTrue(bar._timer.isActive())
        destroyed = QSignalSpy(bar._timer.destroyed)
        self.widgets.remove(bar)
        bar.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.assertEqual(destroyed.count(), 1)
        QTest.qWait(40)

    def test_dialog_preserves_completion_and_cancellation(self):
        dialog = SmoothProgressDialog("Loading", "Cancel", 0, 100)
        self.widgets.append(dialog)
        dialog.setMinimumDuration(0)
        dialog.show()
        dialog.setValue(25)
        bar = dialog.findChild(SmoothProgressBar)
        self.assertIsNotNone(bar)
        self.assertEqual(dialog.value(), 25)
        dialog.setValue(100)
        self.assertFalse(dialog.isVisible())
        self.assertFalse(bar._timer.isActive())
        dialog.setRange(0, 0)
        dialog.show()
        QTest.qWait(40)
        self.assertTrue(bar._timer.isActive())
        dialog.cancel()
        self.assertTrue(dialog.wasCanceled())
        self.assertFalse(dialog.isVisible())
        self.assertFalse(bar._timer.isActive())

    def test_dialog_auto_close_false_can_finish_and_restart(self):
        dialog = SmoothProgressDialog("Loading", "Cancel", 0, 10)
        self.widgets.append(dialog)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.show()
        dialog.setValue(10)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.value(), 10)
        bar = dialog.findChild(SmoothProgressBar)
        self.assertLess(bar.displayed_value, 10)
        QTest.qWait(750)
        self.assertAlmostEqual(bar.displayed_value, 10, places=2)
        dialog.reset()
        dialog.setRange(0, 100)
        dialog.show()
        dialog.setValue(0)
        dialog.setValue(80)
        self.assertLess(bar.displayed_value, 80)

    def test_dialog_auto_reset_with_auto_close_disabled_empties_visible_fill(self):
        dialog = SmoothProgressDialog("Loading", "Cancel", 0, 10)
        self.widgets.append(dialog)
        dialog.setAutoClose(False)
        dialog.show()
        dialog.setValue(10)
        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.value(), -1)
        bar = dialog.findChild(SmoothProgressBar)
        self.assertEqual(bar.displayed_value, 0)
        self.assertFalse(bar._timer.isActive())



if __name__ == "__main__":
    unittest.main()
