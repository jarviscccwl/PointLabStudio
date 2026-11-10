"""Export the selected fused frames, never a stale/missing comparison frame."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
import numpy as np
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from core.io.native_io import read_pcd, read_ply
from core.io.recording_types import FrameData
from core.model.entities import CloudEntity, ScalarField
from ui.widgets.data_view import ImageView
from ui.widgets.recording_browser import BrowserStream, RecordingBrowser
from ui.widgets.recording_export import ExportOptions
from test_recording_navigation import PreviewViewport
from test_recording_ui import MemorySource, wait_for, dispose_widget


class ExportViewport(PreviewViewport):
    def snapshot_rgb(self):
        value = int(self.entities[0].xyz[0, 0])
        return np.full((80, 100, 3), (value, 80, 100), np.uint8)


class RecordingExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.browser = RecordingBrowser(viewport_factory=ExportViewport)
        frames = {'cloud': [FrameData('pointcloud', CloudEntity(str(i), xyz=np.array([[i, 0, 2]], np.float32)),
                                     i * 100000000, i * 100000000) for i in range(5)],
                  'image': [FrameData('image', np.full((80, 100, 3), i * 30, np.uint8),
                                     i * 100000000, i * 100000000) for i in range(5)]}
        self.source = MemorySource(frames)
        self.browser.set_entries([BrowserStream(self.source, s, self.source.index[s.id]) for s in self.source.info.streams])
        self.browser.compare.setChecked(True)
        self.idle()
        self.controller = self.browser.exporter

    def idle(self):
        wait_for(self.app, lambda: self.browser._worker is None and self.browser._pending is None)

    def tearDown(self):
        self.controller.cancel()
        wait_for(self.app, lambda: not self.controller.busy)
        self.browser.cancel()
        self.idle()
        self.browser.stop()
        dispose_widget(self.browser)
        self.tmp.cleanup()

    def test_range_exports_inclusive_frames_and_restores_position(self):
        self.browser.position.setValue(5)
        self.idle()
        target = Path(self.tmp.name) / 'range.png'
        self.controller.start(target, ExportOptions(1, 3, format='png', layout='image'), show_progress=False)
        wait_for(self.app, lambda: not self.controller.busy)
        self.idle()
        self.assertEqual(self.controller.last_error, '')
        self.assertEqual(self.browser.position.value(), 5)
        self.assertEqual(self.browser.slider.value(), 4)
        self.assertIn('已导出 3 帧', self.browser.status.text())
        self.assertTrue(self.browser.last_export_action.isEnabled())
        images = sorted(Path(self.tmp.name).iterdir())
        self.assertEqual([path.name for path in images],
                         ['range_000002.png', 'range_000003.png', 'range_000004.png'])
        self.assertEqual(self.controller.last_output, str(images[0]))
        for i, path in enumerate(images, 1):
            with Image.open(path) as picture:
                self.assertEqual(picture.size, (100, 80))
                self.assertEqual(np.asarray(picture)[40, 50, 0], i * 30)

    def test_single_stream_export_defaults_to_available_picture_content(self):
        from ui.widgets.recording_export import ExportDialog
        for kind, layout in [('pointcloud', 'cloud'), ('image', 'image')]:
            with self.subTest(kind=kind):
                value = (CloudEntity('cloud', xyz=np.array([[0., 0., 2.]], np.float32))
                         if kind == 'pointcloud' else np.zeros((40, 60, 3), np.uint8))
                source = MemorySource({kind: [FrameData(kind, value)]})
                self.browser.set_entries([BrowserStream(source, s, source.index[s.id])
                                          for s in source.info.streams])
                self.browser.compare.setChecked(False)
                self.idle()
                dialog = ExportDialog(self.browser)
                try:
                    self.assertEqual(dialog.layout_choice.currentData(), layout)
                    for index in range(dialog.layout_choice.count()):
                        self.assertEqual(dialog.layout_choice.model().item(index).isEnabled(),
                                         dialog.layout_choice.itemData(index) == layout)
                finally:
                    dialog.close()
                    dialog.deleteLater()

    def test_empty_comparison_range_does_not_offer_empty_image_exports(self):
        from ui.widgets.recording_export import ExportDialog
        self.browser.set_entries([BrowserStream(self.source, s,
            [] if s.kind == 'image' else self.source.index[s.id]) for s in self.source.info.streams])
        self.browser.compare.setChecked(True)
        self.idle()
        dialog = ExportDialog(self.browser)
        try:
            self.assertEqual(dialog.layout_choice.currentData(), 'cloud')
            self.assertFalse(dialog.layout_choice.model().item(1).isEnabled())
        finally:
            dialog.close()
            dialog.deleteLater()

    def test_animation_decodes_exact_range(self):
        import cv2
        target = Path(self.tmp.name) / '我的动画.mp4'
        self.controller.start(target, ExportOptions(0, 2, size=(320, 160)), show_progress=False)
        wait_for(self.app, lambda: not self.controller.busy)
        self.assertEqual(self.controller.last_error, '')
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [target])
        self.assertEqual(self.controller.last_output, str(target))
        capture = cv2.VideoCapture(str(target))
        try:
            frames = []
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frames.append(frame)
            self.assertEqual(len(frames), 3)
            self.assertEqual(frames[0].shape, (160, 320, 3))
            self.assertGreater(float(frames[2][:, 160:].mean()), float(frames[0][:, 160:].mean()) + 20)
        finally:
            capture.release()

    def test_gif_uses_custom_name_and_contains_only_the_selected_frames(self):
        target = Path(self.tmp.name) / '自定义 名称.gif'
        self.controller.start(target, ExportOptions(1, 3, format='gif', layout='image', size=(100, 80)),
                              show_progress=False)
        wait_for(self.app, lambda: not self.controller.busy)
        self.assertEqual(self.controller.last_error, '')
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [target])
        with Image.open(target) as animation:
            self.assertEqual(animation.n_frames, 3)
            for index, value in enumerate((30, 60, 90)):
                animation.seek(index)
                self.assertEqual(np.asarray(animation.convert('RGB'))[40, 50, 0], value)

    def test_each_single_frame_format_creates_only_the_named_file(self):
        expected = set()
        for fmt in ('png', 'ply', 'pcd'):
            with self.subTest(format=fmt):
                target = Path(self.tmp.name) / f'我的单帧.{fmt}'
                self.controller.start(target, ExportOptions(2, 2, format=fmt, layout='image'),
                                      show_progress=False)
                wait_for(self.app, lambda: not self.controller.busy)
                self.idle()
                self.assertEqual(self.controller.last_error, '')
                expected.add(target)
                self.assertEqual(set(Path(self.tmp.name).iterdir()), expected)
                if fmt == 'png':
                    with Image.open(target) as picture:
                        self.assertEqual(np.asarray(picture)[40, 50, 0], 60)
                else:
                    cloud = (read_ply if fmt == 'ply' else read_pcd)(str(target))
                    np.testing.assert_array_equal(cloud.xyz, [[2, 0, 2]])

    def test_explicit_combination_creates_only_named_png_and_ply_pairs(self):
        target = Path(self.tmp.name) / '组合.png'
        self.controller.start(target, ExportOptions(1, 2, format='png_ply', layout='image'),
                              show_progress=False)
        wait_for(self.app, lambda: not self.controller.busy)
        self.assertEqual(self.controller.last_error, '')
        self.assertEqual(sorted(path.name for path in Path(self.tmp.name).iterdir()),
                         ['组合_000002.ply', '组合_000002.png', '组合_000003.ply', '组合_000003.png'])
        for position in (2, 3):
            cloud = read_ply(str(Path(self.tmp.name) / f'组合_{position:06d}.ply'))
            np.testing.assert_array_equal(cloud.xyz, [[position - 1, 0, 2]])

    def test_cancel_keeps_existing_output_and_removes_own_temporary_files(self):
        target = Path(self.tmp.name) / 'cancelled.gif'
        target.write_bytes(b'existing result')
        self.controller.start(target, ExportOptions(0, 4, format='gif'), overwrite=True, show_progress=False)
        self.assertIn(self.controller.worker, self.browser.background_threads())
        self.controller.cancel()
        wait_for(self.app, lambda: not self.controller.busy)
        self.idle()
        self.assertEqual(target.read_bytes(), b'existing result')
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [target])
        self.assertTrue(self.browser.toolbar.isEnabled())

    def test_read_error_is_failure_not_a_successful_shorter_sequence(self):
        original = self.source.read_frame
        def broken(stream, index, cancel=None):
            if index == 2:
                raise ValueError('damaged source')
            return original(stream, index, cancel)
        self.source.read_frame = broken
        target = Path(self.tmp.name) / 'broken.gif'
        target.write_bytes(b'existing result')
        self.controller.start(target, ExportOptions(0, 4, format='gif'), overwrite=True, show_progress=False)
        wait_for(self.app, lambda: not self.controller.busy)
        self.assertIn('damaged source', self.controller.last_error)
        self.assertEqual(target.read_bytes(), b'existing result')
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [target])

    def test_image_snapshot_contains_projection_and_box_at_native_size(self):
        image = ImageView()
        image.set_image(np.zeros((80, 100, 3), np.uint8), np.array([[50, 40]]),
                        np.array([[255, 0, 0]], np.uint8), 6, 1)
        image.set_projected_boxes([{'segments': np.array([[[10, 10], [80, 10]]])}])
        snapshot = image.snapshot_rgb()
        self.assertEqual(snapshot.shape, (80, 100, 3))
        self.assertGreater(snapshot[40, 50, 0], 240)
        self.assertGreater(snapshot[10, 40, 1], 120)
        dispose_widget(image)

    def test_existing_destination_is_not_overwritten_without_explicit_permission(self):
        target = Path(self.tmp.name) / 'existing.gif'
        target.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            self.controller.start(target, ExportOptions(0, 1, format='gif'), show_progress=False)
        self.assertEqual(target.read_bytes(), b'keep')
        self.assertFalse(self.controller.busy)
        self.assertTrue(self.browser.toolbar.isEnabled())

    def test_multiframe_collision_is_checked_before_starting(self):
        existing = Path(self.tmp.name) / 'chosen_000003.png'
        existing.write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            self.controller.start(existing.with_name('chosen.png'), ExportOptions(1, 3, format='png'),
                                  show_progress=False)
        self.assertEqual(existing.read_bytes(), b'keep')
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [existing])
        self.assertFalse(self.controller.busy)

    def test_save_dialog_uses_the_chosen_file_name(self):
        target = Path(self.tmp.name) / '用户命名.gif'
        options = ExportOptions(1, 2, format='gif', layout='image', size=(100, 80))
        with patch('ui.widgets.recording_export.ExportDialog.exec', return_value=QDialog.Accepted), \
                patch('ui.widgets.recording_export.ExportDialog.options', return_value=options), \
                patch('ui.widgets.recording_export.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            self.controller.open_dialog()
        wait_for(self.app, lambda: not self.controller.busy)
        self.assertEqual(self.controller.last_error, '')
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [target])
        with Image.open(target) as animation:
            self.assertEqual(animation.n_frames, 2)

    def test_declining_batch_overwrite_leaves_every_existing_file_unchanged(self):
        targets = [Path(self.tmp.name) / 'chosen_000002.png', Path(self.tmp.name) / 'chosen_000003.png']
        for target in targets:
            target.write_bytes(b'keep')
        options = ExportOptions(1, 3, format='png', layout='image')
        with patch('ui.widgets.recording_export.ExportDialog.exec', return_value=QDialog.Accepted), \
                patch('ui.widgets.recording_export.ExportDialog.options', return_value=options), \
                patch('ui.widgets.recording_export.QFileDialog.getSaveFileName',
                      return_value=(str(Path(self.tmp.name) / 'chosen.png'), '')), \
                patch('ui.widgets.recording_export.QMessageBox.question', return_value=QMessageBox.No):
            self.controller.open_dialog()
        self.assertFalse(self.controller.busy)
        self.assertEqual(set(Path(self.tmp.name).iterdir()), set(targets))
        for target in targets:
            self.assertEqual(target.read_bytes(), b'keep')

    def test_confirmed_batch_overwrite_updates_named_files_and_preserves_unrelated_files(self):
        targets = [Path(self.tmp.name) / 'chosen_000002.png', Path(self.tmp.name) / 'chosen_000003.png']
        for target in targets:
            target.write_bytes(b'previous result')
        unrelated = Path(self.tmp.name) / 'keep.txt'
        unrelated.write_bytes(b'unrelated')
        options = ExportOptions(1, 2, format='png', layout='image')
        with patch('ui.widgets.recording_export.ExportDialog.exec', return_value=QDialog.Accepted), \
                patch('ui.widgets.recording_export.ExportDialog.options', return_value=options), \
                patch('ui.widgets.recording_export.QFileDialog.getSaveFileName',
                      return_value=(str(Path(self.tmp.name) / 'chosen.png'), '')), \
                patch('ui.widgets.recording_export.QMessageBox.question', return_value=QMessageBox.Yes):
            self.controller.open_dialog()
        wait_for(self.app, lambda: not self.controller.busy)
        self.assertEqual(self.controller.last_error, '')
        self.assertEqual(set(Path(self.tmp.name).iterdir()), {*targets, unrelated})
        self.assertEqual(unrelated.read_bytes(), b'unrelated')
        for target, value in zip(targets, (30, 60)):
            with Image.open(target) as picture:
                self.assertEqual(np.asarray(picture)[40, 50, 0], value)

    def test_declining_overwrite_after_extension_is_added_preserves_existing_file(self):
        target = Path(self.tmp.name) / 'chosen.gif'
        target.write_bytes(b'keep')
        options = ExportOptions(1, 2, format='gif')
        with patch('ui.widgets.recording_export.ExportDialog.exec', return_value=QDialog.Accepted), \
                patch('ui.widgets.recording_export.ExportDialog.options', return_value=options), \
                patch('ui.widgets.recording_export.QFileDialog.getSaveFileName',
                      return_value=(str(Path(self.tmp.name) / 'chosen'), '')), \
                patch('ui.widgets.recording_export.QMessageBox.question', return_value=QMessageBox.No):
            self.controller.open_dialog()
        self.assertFalse(self.controller.busy)
        self.assertEqual(target.read_bytes(), b'keep')
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [target])

    def test_colored_cloud_export_keeps_original_rgb_outside_camera(self):
        browser = self.browser
        original = np.array([[.2, .8, .1], [.2, .1, .8], [.3, .4, .5]], np.float32)
        cloud = CloudEntity('color', xyz=np.array([[0, 0, 1], [0, 0, 2], [0, 0, 3]], np.float32),
                            fields={'rgb': ScalarField('rgb', original, 'color')})
        browser._source_frames = [FrameData('pointcloud', cloud), None]
        browser._image_colors = (np.array([[1., 0, 0]], np.float32), np.array([1]))
        browser.calibration_panel.colorize.blockSignals(True)
        browser.calibration_panel.colorize.setChecked(True)
        browser.calibration_panel.colorize.blockSignals(False)
        exported = browser.export_cloud_entity(0)
        np.testing.assert_array_equal(exported.fields['rgb'].data[[0, 2]], original[[0, 2]])
        np.testing.assert_array_equal(exported.fields['rgb'].data[1], [1, 0, 0])
        np.testing.assert_array_equal(exported.fields['image_color_valid'].data, [0, 1, 0])
        np.testing.assert_array_equal(cloud.fields['rgb'].data, original)

    def test_missing_image_is_blank_and_does_not_reuse_the_previous_image(self):
        self.source.index['image'][2] = replace(self.source.index['image'][2], record_time_ns=9000000000)
        self.browser._matcher_cache.clear()
        self.browser._clear_prepared()
        target = Path(self.tmp.name) / 'missing.png'
        self.controller.start(target, ExportOptions(2, 3, format='png', layout='image'), show_progress=False)
        wait_for(self.app, lambda: not self.controller.busy)
        self.assertEqual(self.controller.last_error, '')
        self.assertEqual(sorted(path.name for path in Path(self.tmp.name).iterdir()),
                         ['missing_000003.png', 'missing_000004.png'])
        with Image.open(Path(self.tmp.name) / 'missing_000003.png') as missing:
            self.assertEqual(missing.size, (1280, 720))
            self.assertLess(np.asarray(missing).mean(), 45)
        with Image.open(Path(self.tmp.name) / 'missing_000004.png') as actual:
            self.assertEqual(np.asarray(actual)[40, 50, 0], 90)

    def test_restore_original_image_zoom_after_different_sized_export(self):
        self.source.frames['image'][2].value = np.full((300, 600, 3), 60, np.uint8)
        self.browser.right.image.scale(2.5, 2.5)
        self.browser.right.image._auto_fit = False
        transform = self.browser.right.image.transform()
        target = Path(self.tmp.name) / 'zoom.png'
        self.controller.start(target, ExportOptions(2, 2, format='png', layout='image'), show_progress=False)
        wait_for(self.app, lambda: not self.controller.busy)
        self.idle()
        self.assertEqual(self.browser.right.image.transform(), transform)
        self.assertFalse(self.browser.right.image._auto_fit)

    def test_range_handles_change_export_selection_without_seeking_and_roundtrip(self):
        self.browser.range_button.setChecked(True)
        self.browser.slider.setSelection(1, 3)
        self.assertEqual(self.browser.position.value(), 1)
        self.assertEqual(self.browser.range_first.value(), 2)
        self.assertEqual(self.browser.range_last.value(), 4)
        state = self.browser.state()
        self.browser.slider.setSelection(0, 0)
        self.browser.restore_state(state)
        self.idle()
        self.assertEqual(self.browser.slider.selection(), (1, 3))
        self.assertTrue(self.browser.slider.selectionEnabled())


if __name__ == '__main__':
    unittest.main()
