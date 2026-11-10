"""Native range-slider interaction checks; run serially with other Qt tests."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QSlider, QStyle, QStyleOptionSlider
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ui.widgets.frame_range_slider import FrameRangeSlider


class FrameRangeSliderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        layout = QVBoxLayout(self.window)
        self.before = QLineEdit()
        self.slider = FrameRangeSlider()
        self.after = QLineEdit()
        layout.addWidget(self.before)
        layout.addWidget(self.slider)
        layout.addWidget(self.after)
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.window.resize(520, 180)
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def enable(self, first=20, last=80):
        self.slider.setSelection(first, last)
        self.slider.setSelectionEnabled(True)
        self.app.processEvents()

    def handle(self, which):
        # These are the same rectangles painted and hit-tested by the widget;
        # endpoint expectations below come from literal frame indices.
        return self.slider._selection_handle_rects()[which].center()

    def native_handle(self, value):
        option = QStyleOptionSlider()
        self.slider.initStyleOption(option)
        option.sliderPosition = value
        option.sliderValue = value
        return self.slider.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self.slider,
        ).center()

    def drag(self, first, last):
        QTest.mousePress(self.slider, Qt.LeftButton, pos=first)
        QTest.mouseMove(self.slider, last)
        QTest.mouseRelease(self.slider, Qt.LeftButton, pos=last)
        self.app.processEvents()

    def test_normal_slider_keeps_native_range_signal_and_keyboard_navigation(self):
        self.assertIsInstance(self.slider, QSlider)
        self.assertFalse(self.slider.selectionEnabled())
        native_ranges, selections = [], []
        self.slider.rangeChanged.connect(lambda lo, hi: native_ranges.append((lo, hi)))
        self.slider.selectionChanged.connect(lambda lo, hi: selections.append((lo, hi)))
        self.slider.setRange(0, 60)
        self.assertEqual(native_ranges, [(0, 60)])
        self.assertEqual(self.slider.selection(), (0, 60))
        self.slider.setSelection(10, 40)
        self.assertEqual(native_ranges, [(0, 60)])
        self.assertEqual(selections[-1], (10, 40))
        self.slider.setFocus()
        QTest.keyClick(self.slider, Qt.Key_Right)
        self.assertEqual(self.slider.value(), 51)
        self.assertEqual(self.slider.selection(), (10, 40))

    def test_selection_clamps_sorts_and_allows_single_frame_without_seeking(self):
        changes, seeks = [], []
        self.slider.selectionChanged.connect(lambda lo, hi: changes.append((lo, hi)))
        self.slider.valueChanged.connect(seeks.append)
        self.slider.setSelection(90, 10)
        self.assertEqual(self.slider.selection(), (10, 90))
        self.slider.setSelection(-8, 150)
        self.assertEqual(self.slider.selection(), (0, 100))
        self.slider.setSelection(25, 25)
        self.slider.setSelection(25, 25)
        self.assertEqual(self.slider.selection(), (25, 25))
        self.assertEqual(changes, [(10, 90), (0, 100), (25, 25)])
        self.assertEqual(seeks, [])

    def test_bounds_shrink_clamps_each_endpoint_and_emits_the_actual_selection(self):
        self.enable(20, 80)
        changes = []
        self.slider.selectionChanged.connect(lambda lo, hi: changes.append((lo, hi)))
        self.slider.setMaximum(30)
        self.assertEqual(self.slider.selection(), (20, 30))
        self.slider.setMinimum(25)
        self.assertEqual(self.slider.selection(), (25, 30))
        self.slider.setMaximum(25)
        self.assertEqual(self.slider.selection(), (25, 25))
        self.assertEqual(changes, [(20, 30), (25, 30), (25, 25)])

    def test_blocked_signals_do_not_leave_selection_outside_new_bounds(self):
        self.enable(20, 80)
        changes = []
        self.slider.selectionChanged.connect(lambda lo, hi: changes.append((lo, hi)))
        self.slider.blockSignals(True)
        try:
            self.slider.setRange(0, 10)
        finally:
            self.slider.blockSignals(False)
        self.assertEqual(self.slider.selection(), (10, 10))
        self.assertEqual(changes, [])

    def test_range_drag_does_not_seek_or_emit_native_slider_drag_signals(self):
        self.enable()
        self.slider.setTracking(False)
        changes, seeks, native_drag = [], [], []
        self.slider.selectionChanged.connect(lambda lo, hi: changes.append((lo, hi)))
        self.slider.valueChanged.connect(seeks.append)
        self.slider.sliderPressed.connect(lambda: native_drag.append("press"))
        self.slider.sliderReleased.connect(lambda: native_drag.append("release"))
        start = self.handle("first")
        end = QPoint(self.native_handle(35).x(), start.y())
        self.drag(start, end)
        self.assertEqual(self.slider.selection(), (35, 80))
        self.assertEqual(changes[-1], (35, 80))
        self.assertEqual(self.slider.value(), 50)
        self.assertEqual(seeks, [])
        self.assertEqual(native_drag, [])

    def test_endpoints_cannot_cross_and_both_collapsed_handles_remain_draggable(self):
        self.enable()
        start = self.handle("first")
        self.drag(start, QPoint(self.slider.width() + 40, start.y()))
        self.assertEqual(self.slider.selection(), (80, 80))
        self.assertNotEqual(self.handle("first"), self.handle("last"))
        end = self.handle("last")
        # Relative motion preserves the grab offset of separated collapsed handles.
        delta = self.native_handle(95).x() - self.native_handle(80).x()
        self.drag(end, end + QPoint(delta, 0))
        self.assertEqual(self.slider.selection(), (80, 95))
        end = self.handle("last")
        self.drag(end, QPoint(-40, end.y()))
        self.assertEqual(self.slider.selection(), (80, 80))

    def test_collapsed_handles_at_first_frame_are_both_accessible(self):
        self.enable(0, 0)
        rectangles = self.slider._selection_handle_rects()
        self.assertFalse(rectangles["first"].intersects(rectangles["last"]))
        self.assertTrue(self.slider.rect().contains(rectangles["first"]))
        self.assertTrue(self.slider.rect().contains(rectangles["last"]))
        end = self.handle("last")
        delta = self.native_handle(10).x() - self.native_handle(0).x()
        self.drag(end, end + QPoint(delta, 0))
        self.assertEqual(self.slider.selection(), (0, 10))

    def test_playhead_still_drags_independently_when_range_selection_is_visible(self):
        self.enable()
        self.slider.setTracking(False)
        changes, releases = [], []
        self.slider.selectionChanged.connect(lambda lo, hi: changes.append((lo, hi)))
        self.slider.sliderReleased.connect(lambda: releases.append(self.slider.sliderPosition()))
        self.drag(self.native_handle(50), self.native_handle(65))
        self.assertEqual(self.slider.value(), 65)
        self.assertEqual(self.slider.selection(), (20, 80))
        self.assertEqual(changes, [])
        self.assertEqual(releases, [65])

    def test_tab_visits_both_endpoints_then_exits_without_a_keyboard_trap(self):
        self.enable()
        self.slider.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.slider, Qt.Key_Tab)
        QTest.keyClick(self.slider, Qt.Key_Right)
        self.assertEqual(self.slider.selection(), (21, 80))
        self.assertEqual(self.slider.value(), 50)
        QTest.keyClick(self.slider, Qt.Key_Tab)
        QTest.keyClick(self.slider, Qt.Key_Left)
        self.assertEqual(self.slider.selection(), (21, 79))
        QTest.keyClick(self.slider, Qt.Key_Tab)
        self.assertIs(self.app.focusWidget(), self.after)
        QTest.keyClick(self.after, Qt.Key_Backtab)
        QTest.keyClick(self.slider, Qt.Key_Right)
        self.assertEqual(self.slider.selection(), (21, 80))

    def test_keyboard_home_end_escape_and_disable_preserve_the_playhead_contract(self):
        self.enable()
        self.slider.setFocus()
        QTest.keyClick(self.slider, Qt.Key_Tab)
        QTest.keyClick(self.slider, Qt.Key_End)
        self.assertEqual(self.slider.selection(), (80, 80))
        QTest.keyClick(self.slider, Qt.Key_Home)
        self.assertEqual(self.slider.selection(), (0, 80))
        QTest.keyClick(self.slider, Qt.Key_Escape)
        QTest.keyClick(self.slider, Qt.Key_Right)
        self.assertEqual(self.slider.value(), 51)
        QTest.keyClick(self.slider, Qt.Key_Tab)
        self.slider.setSelectionEnabled(False)
        QTest.keyClick(self.slider, Qt.Key_Right)
        self.assertEqual(self.slider.value(), 52)
        self.assertEqual(self.slider.selection(), (0, 80))

    def test_inverted_appearance_maps_range_drag_to_the_correct_frame(self):
        self.slider.setInvertedAppearance(True)
        self.enable()
        start = self.handle("first")
        self.drag(start, QPoint(self.native_handle(35).x(), start.y()))
        self.assertEqual(self.slider.selection(), (35, 80))

    def test_vertical_orientation_preserves_selection_and_native_playhead(self):
        self.slider.setOrientation(Qt.Vertical)
        self.window.resize(200, 480)
        self.enable()
        start = self.handle("first")
        self.drag(start, QPoint(start.x(), self.native_handle(35).y()))
        self.assertEqual(self.slider.selection(), (35, 80))
        self.assertEqual(self.slider.value(), 50)

    def test_theme_disabled_and_empty_range_remain_paintable_and_accessible(self):
        self.enable(0, 0)
        self.slider.setRange(0, 0)
        for theme in ("dark", "light"):
            self.slider.set_theme(theme)
            self.slider.setEnabled(False)
            self.assertFalse(self.slider.grab().isNull())
            self.assertTrue(self.slider.accessibleName())
            self.assertIn("Tab", self.slider.toolTip())
            self.slider.setEnabled(True)
        self.slider.setFocus()
        QTest.keyClick(self.slider, Qt.Key_Tab)
        QTest.keyClick(self.slider, Qt.Key_Right)
        self.assertEqual(self.slider.selection(), (0, 0))
        self.assertEqual(self.slider.value(), 0)


if __name__ == "__main__":
    unittest.main()
