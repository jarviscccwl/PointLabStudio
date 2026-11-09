"""Interaction regressions for shared data views and point-size controls."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QWidget

from core.io.recording_types import FrameData
from ui.widgets.data_view import DataView, ImageView
from ui.widgets.param_dialog import ParamDialog


class UiPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_status_message_timeout_is_cancelled_when_its_window_is_destroyed(self):
        from ui.main_window import MainWindow

        class StatusWindow(QWidget):
            _flash_status = MainWindow._flash_status

            def _mode_status_text(self):
                return "ready"

        window = StatusWindow()
        window.status_mode = QLabel(window)
        window._flash_status("working", 10)
        QTest.qWait(30)
        self.assertEqual(window.status_mode.text(), "ready")
        errors = []
        with patch.object(sys, "excepthook", lambda *args: errors.append(args)):
            window._flash_status("closing", 30)
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            QTest.qWait(60)
        self.assertEqual(errors, [])

    def test_filter_coalesces_typing_and_enter_applies_immediately(self):
        view = DataView()
        self.addCleanup(view.close)
        rows = [[20, "beta"], [3, "alpha"], [10, "alphabet"]]
        view.show_frame(FrameData("table", {"columns": ["id", "name"], "rows": rows}))
        view.table.sortByColumn(0, Qt.AscendingOrder)
        original = view.table.model()
        view.search.setText("a")
        view.search.setText("alpha")
        self.assertIs(view.table.model(), original)
        QTest.qWait(250)
        self.assertEqual(view.table.model().rows, [[3, "alpha"], [10, "alphabet"]])
        view.search.setText("beta")
        view.search.returnPressed.emit()
        self.assertFalse(view._filter_timer.isActive())
        self.assertEqual(view.table.model().rows, [[20, "beta"]])
        self.assertEqual(rows[0], [20, "beta"])
        view.search.clear()
        view.clear()
        self.assertFalse(view._filter_timer.isActive())

    def test_new_page_applies_pending_filter_to_current_rows(self):
        view = DataView()
        self.addCleanup(view.close)
        view.show_frame(FrameData("table", {"columns": ["name"], "rows": [["old"]]}))
        view.search.setText("new")
        view.show_frame(FrameData("table", {"columns": ["name"], "rows": [["new"], ["other"]]}))
        self.assertEqual(view.table.model().rows, [["new"]])
        self.assertFalse(view._filter_timer.isActive())

    def test_image_zoom_handles_touchpad_horizontal_scroll_and_limits(self):
        view = ImageView()
        self.addCleanup(view.close)

        def wheel(pixel=QPoint(), angle=QPoint()):
            event = QWheelEvent(QPointF(), QPointF(), pixel, angle, Qt.NoButton,
                                Qt.NoModifier, Qt.NoScrollPhase, False)
            view.wheelEvent(event)

        wheel(angle=QPoint(120, 0))
        self.assertEqual(view.transform().m11(), 1)
        self.assertTrue(view._auto_fit)
        wheel(pixel=QPoint(0, 25))
        self.assertAlmostEqual(view.transform().m11(), 1.2 ** 0.5)
        wheel(pixel=QPoint(0, -25))
        self.assertAlmostEqual(view.transform().m11(), 1)
        for _ in range(30):
            wheel(angle=QPoint(0, 1200))
        self.assertAlmostEqual(view.transform().m11(), 64)
        for _ in range(30):
            wheel(angle=QPoint(0, -1200))
        self.assertAlmostEqual(view.transform().m11(), .01)

    def test_point_size_updates_display_without_recomputing_statistics(self):
        from ui.main_window import MainWindow

        entity = SimpleNamespace(point_size=2)
        window = SimpleNamespace(
            content_stack=Mock(), recording_browser=Mock(), scene_panel=Mock(),
            lbl_size=Mock(), overview=Mock(), viewport=Mock(), frame_browser=Mock(),
            _reference_entity=Mock(return_value=entity),
        )
        window.scene_panel.current_entity.return_value = entity
        MainWindow._on_header_size(window, 7)
        self.assertEqual(entity.point_size, 3.5)
        window.overview.set_entity.assert_not_called()
        window.overview.v_pointsize.setText.assert_called_once_with("3.5  px")
        window.viewport.set_point_size.assert_called_once_with()
        window.frame_browser.set_reference.assert_called_once_with(entity)

    def test_parameter_dialog_keeps_values_and_labels(self):
        dialog = ParamDialog(None, "参数", [
            {"key": "count", "label": "邻域点数", "kind": "int", "default": 20},
            {"key": "size", "label": "体素尺寸", "default": .05},
        ])
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.values(), {"count": 20, "size": .05})
        self.assertEqual(dialog._widgets["count"].accessibleName(), "邻域点数")
        buttons = dialog.findChild(QDialogButtonBox)
        self.assertEqual(buttons.button(QDialogButtonBox.Ok).text(), "确定")
        self.assertEqual(buttons.button(QDialogButtonBox.Ok).objectName(), "primary")

    def test_calibration_save_round_trip_and_reset(self):
        from core.calibration import calibration_from_dict, calibration_to_dict, load_calibration
        from ui.widgets.recording_browser import RecordingBrowser

        browser = RecordingBrowser()
        self.addCleanup(browser.close)
        calibration = calibration_from_dict({
            "K": [[100, 0, 50], [0, 100, 40], [0, 0, 1]], "D": [],
            "width": 100, "height": 80,
        })
        browser.calibration = calibration
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "camera.json")
            with patch("ui.widgets.recording_browser.QFileDialog.getSaveFileName", return_value=(path, "")):
                browser._save_calibration()
            self.assertEqual(calibration_to_dict(load_calibration(path)), calibration_to_dict(calibration))
            blocked = Path(directory) / "blocked.json"
            blocked.mkdir()
            with patch("ui.widgets.recording_browser.QFileDialog.getSaveFileName", return_value=(str(blocked), "")), \
                    patch("ui.widgets.recording_browser.QMessageBox.warning") as warning:
                browser._save_calibration()
            warning.assert_called_once()
        with patch.object(browser, "_request_frame") as request:
            browser._reset_calibration()
        request.assert_called_once_with()
        self.assertIsNone(browser.calibration)
        self.assertFalse(browser.overlay.isChecked())
        self.assertFalse(browser.calibration_panel.save_button.isEnabled())
