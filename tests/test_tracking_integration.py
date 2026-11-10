"""Tracking through real MCAP imports, source refresh and workspace save/restore."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from contextlib import contextmanager
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtTest import QTest

from recording_fixtures import write_sensor_mcap
from test_recording_ui import dispose_widget, wait_for
from ui.main_window import MainWindow
from ui.widgets.source_overview import SourceOverviewDialog
from core.object_tracking import Box3D


class TrackingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="PointLab tracking ")
        self.base = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"POINTLAB_HOME": str(self.base / "settings")})
        self.env.start()
        self.window = MainWindow()
        self.path = self.base / "capture.mcap"
        xyz = np.random.default_rng(3).uniform(-.5, .5, (220, 3)) * (2, 1, 1)
        self.clouds = [(5_000_000_000 + i * 100_000_000, xyz + (i * .1, 0, 0)) for i in range(4)]
        write_sensor_mcap(self.path, clouds=self.clouds)
        self.window._open_recording_sources([str(self.path)], prompt=False)
        self.idle()

    def idle(self):
        wait_for(self.app, lambda: not (
            self.window._recording_threads() or self.window._recording_importing
            or self.window._recording_queue or self.window.recording_browser._pending is not None
        ), timeout=30)

    def tearDown(self):
        self.idle()
        self.window._project_baseline = self.window._project_signature()
        dispose_widget(self.window)
        self.env.stop()
        self.temp.cleanup()

    def add_target(self, position=2):
        browser = self.window.recording_browser
        browser.position.setValue(position)
        self.idle()
        tracking = browser.tracking
        tracking.set_mode("box")
        cloud = browser.current_frames[0].value
        browser.left.cloud_view.selection_changed.emit([(cloud, np.arange(len(cloud.xyz)))])
        return tracking

    def test_global_box_tool_embeds_editor_and_reloading_preserves_full_pose(self):
        tracking = self.add_target()
        self.window.show()
        self.window.activateWindow()
        self.window.btn_box_select.click()
        self.app.processEvents()
        self.assertFalse(tracking.dialog.isWindow())
        self.assertTrue(tracking.dialog.isVisible())
        self.assertTrue(self.window.recording_browser.tracking_button.isHidden())
        edited = Box3D((.1, 0, 0), (2, 1, 1), .3, .1, -.2)
        tracking.dialog.editor.box_edited.emit(edited)
        before = tracking.source_state(str(self.path))
        self.window.recording_browser.reload_topic()
        self.idle()
        self.assertEqual(tracking.source_state(str(self.path)), before)
        self.assertEqual(tracking.dialog.editor.box, edited)
        tracking.dialog.close()
        self.assertFalse(self.window.btn_box_select.isChecked())

    def test_extraction_enters_scene_and_supports_undo_redo_and_workspace(self):
        tracking = self.add_target()
        tracking.show()
        before = self.window.recording_browser.current_frames[0].value
        tracking.dialog.extract_button.click()
        self.idle()
        self.assertIs(self.window.content_stack.currentWidget(), self.window.frame_browser)
        extracted = self.window.scene_panel.current_entity()
        self.assertIsNot(extracted, before)
        np.testing.assert_array_equal(extracted.xyz, before.xyz)
        self.assertIn(extracted, self.window.entities)
        self.window._undo()
        self.assertNotIn(extracted, self.window.entities)
        self.window._redo()
        self.assertIn(extracted, self.window.entities)
        self.assertTrue(tracking.has_annotations)
        project = self.base / "extracted.pls"
        self.assertTrue(self.window.save_project(str(project)))
        self.assertTrue(self.window.open_project(str(project)))
        self.idle()
        restored = next(entity for entity in self.window.entities if entity.name == extracted.name)
        np.testing.assert_array_equal(restored.xyz, extracted.xyz)
        self.assertEqual(restored.info, extracted.info)

    def test_visible_association_action_tracks_both_directions_and_browses_results(self):
        tracking = self.add_target()
        self.window.show()
        tracking.show()
        dialog = tracking.dialog
        self.app.processEvents()
        self.assertTrue(dialog.track_button.isVisible())
        self.assertTrue(dialog.tabs.isHidden())
        dialog.track_button.click()
        self.assertTrue(tracking.busy)
        self.idle()
        observations = tracking._track().observations
        self.assertEqual(sorted(observations), [0, 1, 2, 3])
        self.assertTrue(observations[1].manual)
        self.assertEqual(dialog.result_frames.count(), 4)
        dialog.result_next.click()
        self.idle()
        self.assertEqual(self.window.recording_browser._displayed_indices[0], 2)
        self.assertIsNotNone(dialog.editor.box)
        dialog.result_previous.click()
        self.idle()
        self.assertEqual(self.window.recording_browser._displayed_indices[0], 1)

    def test_frame_without_box_can_return_to_nearest_annotation(self):
        tracking = self.add_target()
        self.window.show()
        tracking.show()
        browser = self.window.recording_browser
        browser.position.setValue(4)
        self.idle()
        self.assertIsNone(tracking.dialog.editor.box)
        self.assertFalse(tracking.dialog.track_button.isEnabled())
        self.assertTrue(tracking.dialog.return_button.isVisible())
        tracking.dialog.return_button.click()
        self.idle()
        self.assertEqual(browser._displayed_indices[0], 1)
        self.assertIsNotNone(tracking.dialog.editor.box)
        self.assertTrue(tracking.dialog.track_button.isEnabled())
        self.assertEqual(sorted(tracking._track().observations), [1])

    def test_keyboard_result_review_keeps_editor_focus_for_the_next_correction(self):
        tracking = self.add_target()
        self.window.show()
        self.window.activateWindow()
        tracking.show()
        self.assertTrue(tracking.start_tracking("forward", 2, .5))
        self.idle()
        view = tracking.dialog.editor.views["top"]
        view.setFocus()
        self.app.processEvents()
        QTest.keyClick(view, Qt.Key_PageDown, Qt.ControlModifier)
        self.idle()
        self.assertEqual(self.window.recording_browser._displayed_indices[0], 2)
        self.assertTrue(view.hasFocus())
        old = tracking._track().observations[2].box
        QTest.keyClick(view, Qt.Key_D)
        self.assertGreater(tracking._track().observations[2].box.center[0], old.center[0])
        self.assertTrue(tracking._track().observations[2].manual)
        QTest.keyClick(view, Qt.Key_PageDown, Qt.ControlModifier)
        self.idle()
        self.assertEqual(self.window.recording_browser._displayed_indices[0], 3)
        self.assertTrue(view.hasFocus())

    def test_small_workspace_keeps_three_view_handles_actions_and_previews_visible(self):
        path = self.base / "layout.mcap"
        write_sensor_mcap(path, clouds=self.clouds,
                          images=[(stamp, np.full((180, 320, 3), 90, np.uint8)) for stamp, _ in self.clouds])
        self.window._open_recording_sources([str(path)], prompt=False)
        self.idle()
        browser = self.window.recording_browser
        for combo, kind in ((browser.primary, "pointcloud"), (browser.secondary, "image")):
            combo.setCurrentIndex(next(index for index in range(combo.count())
                                       if (entry := browser.entry(combo.itemData(index))) is not None
                                       and entry.source.path == str(path) and entry.stream.kind == kind))
        browser.compare.setChecked(True)
        self.idle()
        tracking = self.add_target(1)
        self.window.show()
        self.window.activateWindow()

        @contextmanager
        def isolated_size():
            try:
                yield
            finally:
                if tracking.dialog is not None:
                    tracking.dialog.progress.hide()
                    tracking.dialog.cancel_button.hide()
                    tracking.dialog.close()
                browser.show()
                self.app.processEvents()

        for width, height in ((1024, 670), (980, 620), (1024, 700), (1280, 820)):
            with self.subTest(width=width, height=height), isolated_size():
                self.window.resize(width, height)
                self.app.processEvents()
                preview_height = browser.panes.height()
                tracking.show()
                QTest.qWait(40)
                self.assertEqual(self.window.size(), QSize(width, height))
                dialog = tracking.dialog
                geometry = (browser.box_splitter.sizes(), dialog.editor.size(), dialog.scroll.viewport().size())
                for view in dialog.editor.views.values():
                    self.assertTrue(view.visibleRegion().boundingRect().contains(view.rect()), geometry)
                    usable = view.rect().adjusted(6, 30, -6, -6)
                    for point in [*view.handle_positions().values(), view._rotation_handle()]:
                        self.assertTrue(usable.contains(point.toPoint()), (geometry, point, usable))
                for control in (dialog.track_button, dialog.result_previous, dialog.result_frames, dialog.result_next):
                    self.assertTrue(control.visibleRegion().boundingRect().contains(control.rect()), geometry)
                self.assertGreaterEqual(browser.left.cloud_view.height(), 48, geometry)
                self.assertGreaterEqual(browser.right.image.viewport().height(), 48, geometry)
                dialog.progress.show()
                dialog.cancel_button.show()
                self.app.processEvents()
                self.assertEqual(self.window.size(), QSize(width, height))
                for view in dialog.editor.views.values():
                    self.assertTrue(view.visibleRegion().boundingRect().contains(view.rect()))
                    usable = view.rect().adjusted(6, 30, -6, -6)
                    for point in [*view.handle_positions().values(), view._rotation_handle()]:
                        self.assertTrue(usable.contains(point.toPoint()))
                for control in (dialog.track_button, dialog.cancel_button, dialog.result_frames):
                    self.assertTrue(control.visibleRegion().boundingRect().contains(control.rect()))
                self.assertGreaterEqual(browser.left.cloud_view.height(), 48)
                self.assertGreaterEqual(browser.right.image.viewport().height(), 48)
                dialog.progress.hide()
                dialog.cancel_button.hide()
                self.app.processEvents()
                if width == 1280:
                    total = sum(browser.box_splitter.sizes())
                    browser.box_splitter.setSizes([total - 310, 310])
                    browser.box_splitter.splitterMoved.emit(total - 310, 1)
                    manual_sizes = browser.box_splitter.sizes()
                    dialog._fit_host_layout()
                    self.assertEqual(browser.box_splitter.sizes(), manual_sizes)
                original_sizes = browser.box_splitter.sizes()
                coordinate = dialog.center[0].value() + .1
                dialog.center[0].setValue(coordinate)
                dialog.parameters_button.click()
                QTest.qWait(40)
                self.assertEqual(self.window.size(), QSize(width, height))
                floating = dialog._settings_window.isVisible()
                if height <= 670:
                    self.assertTrue(floating)
                elif width == 1280:
                    self.assertFalse(floating)
                # The borderline size can use either presentation: native
                # title/font/frame metrics differ from the offscreen platform.
                self.assertIs(dialog.scroll.parentWidget(), dialog._settings_window if floating else dialog)
                self.assertGreaterEqual(dialog.scroll.viewport().height(), 70)
                for view in dialog.editor.views.values():
                    self.assertTrue(view.visibleRegion().boundingRect().contains(view.rect()))
                self.assertTrue(dialog.track_button.visibleRegion().boundingRect().contains(dialog.track_button.rect()))
                self.assertTrue(dialog.result_frames.visibleRegion().boundingRect().contains(dialog.result_frames.rect()))
                dialog.associate_button.click()
                dialog.parameters_button.click()
                self.app.processEvents()
                self.assertAlmostEqual(dialog.center[0].value(), coordinate)
                dialog.apply_button.click()
                self.assertAlmostEqual(tracking._track().observations[0].box.center[0], coordinate)
                dialog.parameters_button.click()
                self.app.processEvents()
                self.assertFalse(dialog._settings_window.isVisible())
                self.assertEqual(browser.box_splitter.sizes(), original_sizes)
                if floating:
                    dialog.parameters_button.click()
                    self.app.processEvents()
                    self.assertTrue(dialog._settings_window.isVisible())
                    browser.hide()
                    self.app.processEvents()
                    self.assertFalse(dialog._settings_window.isVisible())
                    browser.show()
                    self.app.processEvents()
                    dialog.parameters_button.click()
                    self.app.processEvents()
                    self.assertTrue(dialog._settings_window.isVisible())
                dialog.close()
                self.app.processEvents()
                self.assertFalse(dialog._settings_window.isVisible())
                self.assertGreaterEqual(browser.panes.height(), preview_height - 1)
                self.assertEqual(browser.panes.minimumHeight(), 165)

    def test_box_editor_with_export_range_preserves_a_readable_main_preview(self):
        self._check_default_preview_with_export_range(True)

    def test_opening_range_after_editor_preserves_a_readable_main_preview(self):
        self._check_default_preview_with_export_range(False)

    def test_short_workspace_with_export_range_keeps_three_views_and_actions_visible(self):
        self._check_default_preview_with_export_range(True, (980, 620), 48)

    def test_closing_comparison_restores_range_inputs_when_space_is_available(self):
        browser, _ = self._check_default_preview_with_export_range(True)
        self.window.resize(1024, 650)
        QTest.qWait(60)
        self.assertTrue(browser.range_row.isHidden())
        browser.compare.setChecked(False)
        self.idle()
        QTest.qWait(60)
        self.assertTrue(browser.range_row.isVisible())
        self.assertEqual(browser.slider.selection(), (1, 2))

    def _check_default_preview_with_export_range(self, range_first, size=(1280, 820), minimum=170):
        path = self.base / "preview-layout.mcap"
        write_sensor_mcap(path, clouds=self.clouds,
                          images=[(stamp, np.full((180, 320, 3), 90, np.uint8)) for stamp, _ in self.clouds])
        self.window._open_recording_sources([str(path)], prompt=False)
        self.idle()
        browser = self.window.recording_browser
        for combo, kind in ((browser.primary, "pointcloud"), (browser.secondary, "image")):
            combo.setCurrentIndex(next(i for i in range(combo.count())
                if (entry := browser.entry(combo.itemData(i))) is not None
                and entry.source.path == str(path) and entry.stream.kind == kind))
        browser.compare.setChecked(True)
        self.idle()
        tracking = self.add_target()
        self.window.resize(*size)
        browser.range_button.setChecked(range_first)
        browser.slider.setSelection(1, 2)
        self.window.show()
        self.app.processEvents()
        tracking.show()
        QTest.qWait(60)
        if not range_first:
            browser.range_button.setChecked(True)
            QTest.qWait(60)
        geometry = (browser.box_splitter.sizes(), tracking.dialog.editor.size(),
                    browser.left.cloud_view.height(), tracking.dialog.layout().minimumSize().height(),
                    browser.range_row.sizeHint().height(), tracking.dialog.status.sizeHint().height())
        self.assertEqual(self.window.size(), QSize(*size))
        self.assertGreaterEqual(browser.left.cloud_view.height(), minimum, geometry)
        self.assertGreaterEqual(browser.right.image.viewport().height(), minimum, geometry)
        for widget in (*tracking.dialog.editor.views.values(), tracking.dialog.track_button,
                       tracking.dialog.result_frames, browser.export_button):
            self.assertTrue(widget.visibleRegion().boundingRect().contains(widget.rect()), geometry)
        self.assertLess(tracking.dialog.editor.geometry().bottom(),
                        tracking.dialog.track_button.mapTo(tracking.dialog, QPoint()).y(), geometry)
        view = tracking.dialog.editor.views["top"]
        view.setFocus()
        QTest.keyClick(view, Qt.Key_D)
        self.assertTrue(tracking.navigate(1))
        self.idle()
        self.assertTrue(tracking.navigate(-1))
        self.idle()
        QTest.qWait(60)
        self.assertLess(tracking.dialog.editor.geometry().bottom(),
                        tracking.dialog.track_button.mapTo(tracking.dialog, QPoint()).y(), geometry)
        self.assertEqual(browser.slider.selection(), (1, 2))
        self.window.resize(1280, 820)
        QTest.qWait(60)
        self.assertTrue(browser.range_row.isVisible())
        self.assertEqual((browser.range_first.value(), browser.range_last.value()), (2, 3))

        # Opening the range after the editor must re-balance the default layout.
        tracking.dialog.close()
        browser.range_button.setChecked(False)
        tracking.show()
        QTest.qWait(60)
        browser.range_button.setChecked(True)
        QTest.qWait(60)
        self.assertGreaterEqual(browser.left.cloud_view.height(), 170,
                                (browser.box_splitter.sizes(), tracking.dialog.editor.size()))
        return browser, tracking

    def test_box_undo_shortcut_is_local_inside_main_window(self):
        tracking = self.add_target()
        self.window.show()
        self.window.activateWindow()
        tracking.show()
        view = tracking.dialog.editor.views["top"]
        view.setFocus()
        QTest.qWait(60)
        old = tracking._track().observations[1].box
        QTest.keyClick(view, Qt.Key_D)
        self.assertNotEqual(tracking._track().observations[1].box, old)
        QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(tracking._track().observations[1].box, old)

    def test_box_button_tracks_workspace_and_scene_tool_opens_on_first_click(self):
        tracking = self.add_target()
        self.window.show()
        tracking.show()
        self.assertTrue(self.window.btn_box_select.isChecked())
        cloud = self.window.recording_browser.current_frames[0].value
        self.window._add_recording_cloud(cloud)
        self.assertFalse(self.window.btn_box_select.isChecked())
        self.window.btn_box_select.click()
        self.assertTrue(self.window._scene_box_tool.armed)
        self.window._show_recording_browser()
        self.assertTrue(self.window.btn_box_select.isChecked())
        self.assertTrue(tracking.dialog.isVisible())

    def test_tracking_is_unsaved_work_and_round_trips_original_frames_after_range_change(self):
        tracking = self.add_target()
        self.assertTrue(tracking.has_annotations)
        self.assertTrue(self.window._has_unsaved_project())
        browser = self.window.recording_browser
        browser.rate.setValue(.1)
        browser.play.setChecked(True)
        self.idle()
        self.assertTrue(tracking.start_tracking("both", count=2, max_distance=.5))
        self.assertFalse(browser.play.isChecked())
        self.assertFalse(browser.timer.isActive())
        self.idle()
        payload = tracking.source_state(str(self.path))
        observations = payload["streams"][0]["tracks"][0]["observations"]
        self.assertEqual([item["frame_index"] for item in observations], [0, 1, 2, 3])
        self.assertTrue(observations[1]["manual"])
        self.assertFalse(observations[0]["manual"])

        def choose(dialog):
            options = dialog.selection()
            options["range"] = {"mode": "frames", "first": 1, "last": 2}
            dialog.set_selection(options)
            return QDialog.Accepted

        with patch.object(SourceOverviewDialog, "exec", choose):
            self.window._configure_recording_source(str(self.path))
            self.idle()
        self.assertEqual(tracking.source_state(str(self.path)), payload)
        self.assertEqual(self.window.recording_browser.position.value(), 1)
        project = self.base / "targets.pls"
        self.assertTrue(self.window.save_project(str(project)))
        dispose_widget(self.window)
        self.window = MainWindow()
        self.assertTrue(self.window.open_project(str(project)))
        self.idle()
        self.assertEqual(self.window.recording_browser.tracking.source_state(str(self.path)), payload)
        self.assertFalse(self.window._has_unsaved_project())
        self.assertEqual(self.window.recording_browser._displayed_indices[0], 1)

    def test_changed_source_invalidates_boxes_instead_of_attaching_them_to_new_frames(self):
        tracking = self.add_target()
        self.assertTrue(tracking.has_annotations)
        changed = [(stamp, xyz + (20, 0, 0)) for stamp, xyz in self.clouds]
        write_sensor_mcap(self.path, clouds=changed)
        self.window._reload_recording_source(str(self.path))
        self.idle()
        self.assertFalse(tracking.has_annotations)
        self.assertIn("标注", self.window.log_view.toPlainText())

    def test_recording_preview_space_plays_without_scene_visibility_shortcut_conflict(self):
        self.window.show()
        self.window.activateWindow()
        browser = self.window.recording_browser
        browser.rate.setValue(.1)
        viewport = browser.left.cloud_view
        viewport.setFocus()
        QTest.qWait(50)
        QTest.keyClick(viewport, Qt.Key_Space)
        self.assertTrue(browser.play.isChecked())
        QTest.keyClick(viewport, Qt.Key_Space)
        self.assertFalse(browser.play.isChecked())

    def test_panel_reenables_after_navigation_and_manual_correction_survives_retracking(self):
        tracking = self.add_target()
        tracking.show()
        browser = self.window.recording_browser
        browser.position.setValue(3)
        self.idle()
        self.assertTrue(tracking.dialog.new_button.isEnabled())
        self.assertFalse(tracking.dialog.apply_button.isEnabled())
        target_id = tracking.selected_id
        self.assertTrue(tracking.set_mode("box", reframe=True))
        cloud = browser.current_frames[0].value
        browser.left.cloud_view.selection_changed.emit([(cloud, np.arange(len(cloud.xyz)))])
        tracking.dialog.center[0].setValue(tracking.dialog.center[0].value() + .02)
        tracking.apply_manual_edit()
        before = tracking.source_state(str(self.path))["streams"][0]["tracks"][0]
        corrected = next(item for item in before["observations"] if item["frame_index"] == 2)
        browser.position.setValue(2)
        self.idle()
        self.assertTrue(tracking.dialog.track_button.isEnabled())
        self.assertTrue(tracking.start_tracking("forward", 2, .5))
        self.idle()
        after = tracking.source_state(str(self.path))["streams"][0]["tracks"][0]
        self.assertEqual(after["id"], target_id)
        self.assertEqual(next(item for item in after["observations"] if item["frame_index"] == 2), corrected)

    def test_panel_navigates_adjacent_frames_and_focuses_box_without_changing_cloud(self):
        tracking = self.add_target()
        tracking.show()
        browser = self.window.recording_browser
        viewport = browser.left.cloud_view
        cloud = browser.current_frames[0].value
        original = cloud.xyz.copy()
        target_id = tracking.selected_id
        tracking.dialog.center[0].setValue(3.0)
        tracking.apply_manual_edit()
        tracking.dialog.focus_button.click()
        self.assertAlmostEqual(viewport._pivot[0], 3.0, places=5)
        self.assertIs(viewport.entities[0], cloud)
        np.testing.assert_array_equal(cloud.xyz, original)
        tracking.dialog.next_button.click()
        self.idle()
        self.assertEqual(browser._displayed_indices[0], 2)
        self.assertEqual(tracking.selected_id, target_id)
        self.assertFalse(tracking.dialog.focus_button.isEnabled())
        tracking.dialog.previous_button.click()
        self.idle()
        self.assertEqual(browser._displayed_indices[0], 1)
        self.assertTrue(tracking.dialog.focus_button.isEnabled())

    def test_panel_navigation_and_focus_reject_an_unavailable_source(self):
        tracking = self.add_target()
        tracking.show()
        browser = self.window.recording_browser
        source = browser.entry(browser.primary.currentData()).source
        position = browser.position.value()
        with patch.object(source, "cache_token", return_value=None):
            tracking._refresh()
            self.assertFalse(tracking.dialog.next_button.isEnabled())
            self.assertFalse(tracking.dialog.focus_button.isEnabled())
            self.assertFalse(tracking.navigate(1))
            self.assertFalse(tracking.focus_target())
        self.assertEqual(browser.position.value(), position)

    def test_navigation_cancels_tracking_and_serializes_readers(self):
        from core.cancellation import check_cancel

        tracking = self.add_target()
        browser = self.window.recording_browser
        source = browser.entry(browser.primary.currentData()).source
        original_read = source.read_frame
        entered = threading.Event()
        active = [0, 0]

        def read(stream_id, index, cancel=None):
            active[0] += 1
            active[1] = max(active[1], active[0])
            try:
                if not entered.is_set():
                    entered.set()
                    while not cancel():
                        time.sleep(.002)
                    check_cancel(cancel)
                return original_read(stream_id, index, cancel)
            finally:
                active[0] -= 1

        with patch.object(source, "read_frame", read):
            self.assertTrue(tracking.start_tracking("forward", 2, .5))
            wait_for(self.app, entered.is_set)
            browser.position.setValue(3)
            self.idle()
        self.assertEqual(active[1], 1)
        self.assertEqual(browser._displayed_indices[0], 2)
        observations = tracking.source_state(str(self.path))["streams"][0]["tracks"][0]["observations"]
        self.assertEqual([item["frame_index"] for item in observations], [1])

    def test_background_preload_keeps_current_box_editable_and_navigation_serial(self):
        from core.cancellation import check_cancel

        tracking = self.add_target()
        browser = self.window.recording_browser
        browser._schedule_prefetch = lambda: None
        browser._prefetch_timer.stop()
        self.window.show()
        source = browser.entry(browser.primary.currentData()).source
        read_frame = source.read_frame
        entered, release = threading.Event(), threading.Event()
        active = [0, 0]

        def read(stream_id, index, cancel=None):
            active[0] += 1
            active[1] = max(active[1], active[0])
            try:
                if index == 3:
                    entered.set()
                    while not release.is_set():
                        check_cancel(cancel)
                        time.sleep(.002)
                return read_frame(stream_id, index, cancel)
            finally:
                active[0] -= 1

        try:
            with patch.object(source, "read_frame", read):
                browser._start_read(prefetch_position=3)
                wait_for(self.app, entered.is_set)
                tracking.show()
                self.app.processEvents()
                dialog = tracking.dialog
                self.assertTrue(dialog.editor.isEnabled())
                self.assertTrue(dialog.focus_button.isEnabled())
                self.assertFalse(dialog.track_button.isEnabled())
                old = tracking._track().observations[1].box
                view = dialog.editor.views["top"]
                view.setFocus()
                QTest.keyClick(view, Qt.Key_D)
                self.assertNotEqual(tracking._track().observations[1].box, old)
                self.assertTrue(tracking.focus_target())
                self.assertTrue(tracking.navigate(1))
                self.idle()
                self.assertEqual(browser._displayed_indices[0], 2)
                self.assertEqual(active[1], 1)
                self.assertTrue(tracking.navigate(-1))
                self.idle()
                self.assertTrue(dialog.track_button.isEnabled())
        finally:
            release.set()
            self.idle()

    def test_returning_to_prepared_frame_remains_editable_after_old_read_is_cancelled(self):
        from core.cancellation import check_cancel

        tracking = self.add_target()
        browser = self.window.recording_browser
        browser._schedule_prefetch = lambda: None
        browser._prefetch_timer.stop()
        self.window.show()
        tracking.show()
        source = browser.entry(browser.primary.currentData()).source
        read_frame = source.read_frame
        entered, release = threading.Event(), threading.Event()

        def read(stream_id, index, cancel=None):
            if index == 3:
                entered.set()
                while not release.is_set():
                    check_cancel(cancel)
                    time.sleep(.002)
            return read_frame(stream_id, index, cancel)

        try:
            with patch.object(source, "read_frame", read):
                browser.position.setValue(4)
                wait_for(self.app, entered.is_set)
                hits = browser._prepared_hits
                browser.position.setValue(2)
                self.assertGreater(browser._prepared_hits, hits)
                self.idle()
                self.assertTrue(tracking.dialog.editor.isEnabled())
                self.assertTrue(tracking.dialog.focus_button.isEnabled())
                self.assertTrue(tracking.dialog.track_button.isEnabled())
                old = tracking._track().observations[1].box
                view = tracking.dialog.editor.views["top"]
                view.setFocus()
                QTest.keyClick(view, Qt.Key_D)
                self.assertNotEqual(tracking._track().observations[1].box, old)
        finally:
            release.set()
            self.idle()

    def test_batch_naming_dialog_holds_neighbor_reads_until_job_starts(self):
        tracking = self.add_target()
        browser = self.window.recording_browser
        browser._prefetch_timer.stop()
        self.window.show()
        tracking.show()
        destination = self.base / "named-target.pcd"

        def choose(*args, **kwargs):
            browser._start_prefetch(force=True)
            self.assertIsNone(browser._worker)
            return str(destination), "PCD 点云 (*.pcd)"

        with patch('ui.widgets.object_tracking.QFileDialog.getSaveFileName', side_effect=choose):
            self.assertTrue(tracking.extract_associated())
        self.idle()
        self.assertTrue(destination.is_file())

    def _check_offline_annotations_survive_save(self, include_available_source):
        tracking = self.add_target()
        expected = tracking.source_state(str(self.path))
        if include_available_source:
            notes = self.base / "notes.txt"
            notes.write_text("available", encoding="utf-8")
            self.window._open_recording_sources([str(notes)], prompt=False)
            self.idle()
        project = self.base / "offline.pls"
        self.assertTrue(self.window.save_project(str(project)))
        dispose_widget(self.window)
        self.path.rename(self.path.with_suffix(".offline"))
        self.window = MainWindow()
        self.assertTrue(self.window.open_project(str(project)))
        self.idle()
        self.assertTrue(self.window.recording_browser.tracking.has_annotations)
        self.assertFalse(self.window._has_unsaved_project())
        copy = self.base / "saved-again.pls"
        self.assertTrue(self.window.save_project(str(copy)))
        sources = json.loads(copy.read_text(encoding="utf-8"))["recordings"]["sources"]
        retained = next(item for item in sources if Path(item["path"]).name == self.path.name)
        self.assertEqual(retained["annotations"], expected)
        self.path.with_suffix(".offline").rename(self.path)
        self.window._open_recording_sources([str(self.path)], prompt=False)
        self.idle()
        self.assertEqual(self.window.recording_browser.tracking.source_state(str(self.path)), expected)
        stored = self.window._recording_state()["sources"]
        self.assertEqual(sum(item["path"] == str(self.path) for item in stored), 1)

    def test_missing_recording_annotations_survive_save_with_an_available_source(self):
        self._check_offline_annotations_survive_save(True)

    def test_all_recordings_offline_still_preserve_annotation_workspace(self):
        self._check_offline_annotations_survive_save(False)


if __name__ == "__main__":
    unittest.main()
