"""User-facing recording selection and scene integration regressions."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog, QWidget
from PySide6.QtTest import QTest

from core.io.recording_types import (
    FrameData,
    FrameRef,
    RecordingSource,
    SourceInfo,
    StreamInfo,
)
from ui.widgets.recording_browser import BrowserStream, RecordingBrowser
from ui.widgets.source_overview import SourceOverviewDialog
from ui.widgets.scene_panel import ScenePanel, RECORDING_ROLE


class SampleSource(RecordingSource):
    def __init__(self):
        super().__init__(os.path.abspath("sample.mcap"))
        self.info = SourceInfo(
            self.path,
            "MCAP",
            [
                StreamInfo("camera", "/camera", "image", "sensor_msgs/msg/Image", 12),
                StreamInfo("imu", "/imu", "imu", "sensor_msgs/msg/Imu", 120),
            ],
            1_000_000_000,
            2_000_000_000,
            1024,
        )


class RecordingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_overview_selects_all_streams_without_entering_frame_count(self):
        source = SampleSource()
        dialog = SourceOverviewDialog([source])
        self.assertEqual(dialog.selection()["sources"][source.path], ["camera", "imu"])
        self.assertEqual(dialog.selection()["range"]["mode"], "all")
        dispose_widget(dialog)

    def test_unchecked_stream_is_excluded(self):
        source = SampleSource()
        dialog = SourceOverviewDialog([source])
        dialog.tree.topLevelItem(0).child(0).setCheckState(0, Qt.Unchecked)
        self.assertEqual(dialog.selection()["sources"][source.path], ["imu"])
        dispose_widget(dialog)

    def test_recordings_remain_in_scene_when_cloud_list_is_rebuilt(self):
        source = SampleSource()
        panel = ScenePanel()
        panel.set_recordings([(source.info, ["camera", "imu"])])
        panel.set_entities([])
        item = panel.recording_item(source.path, "camera")
        self.assertIsNotNone(item)
        self.assertEqual(item.data(0, RECORDING_ROLE), (source.path, "camera"))
        self.assertFalse(panel.empty.isVisibleTo(panel))
        dispose_widget(panel)

    def test_unknown_probe_can_scan_whole_capture_before_selection(self):
        source = SampleSource()
        source.info.streams = []

        def build(stream_ids=None, cancel=None, progress=None):
            self.assertIsNone(stream_ids)
            source.info.streams = [
                StreamInfo("late", "late lidar", "pointcloud", count=3)
            ]

        source.build_index = build
        dialog = SourceOverviewDialog([source])
        dialog._start_scan()
        wait_for(self.app, lambda: dialog._scan is None)
        self.assertEqual(dialog.selection()["sources"][source.path], ["late"])
        dispose_widget(dialog)


class MemorySource(RecordingSource):
    def __init__(self, frames):
        super().__init__(os.path.abspath("memory.mcap"))
        self.frames = frames
        self.reads = []
        self.info = SourceInfo(
            self.path,
            "MCAP",
            [
                StreamInfo(key, key, values[0].kind, count=len(values))
                for key, values in frames.items()
            ],
        )
        self.index = {
            key: [
                FrameRef(i, frame.timestamp_ns, frame.record_time_ns)
                for i, frame in enumerate(values)
            ]
            for key, values in frames.items()
        }

    def read_frame(self, stream_id, index, cancel=None):
        self.reads.append((stream_id, index))
        time.sleep(0.01)
        return self.frames[stream_id][index]


def wait_for(app, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    raise AssertionError("Recording operation did not finish")


def dispose_widget(widget):
    """Destroy test-owned Qt trees before Python interpreter shutdown."""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class RecordingBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.browser = RecordingBrowser()

    def tearDown(self):
        self.browser.cancel()
        wait_for(self.app, lambda: self.browser._worker is None)
        self.browser.stop()
        dispose_widget(self.browser)

    def load(self, source):
        self.browser.set_entries(
            [
                BrowserStream(source, stream, source.index[stream.id])
                for stream in source.info.streams
            ]
        )
        self.idle()

    def idle(self):
        wait_for(
            self.app,
            lambda: self.browser._worker is None and self.browser._pending is None,
        )

    def test_rapid_navigation_publishes_latest_frame_only(self):
        source = MemorySource(
            {"a": [FrameData("text", str(i), i, i) for i in range(12)]}
        )
        self.load(source)
        for position in (2, 3, 8, 12):
            self.browser.position.setValue(position)
        self.idle()
        self.assertEqual(self.browser.left.text.toPlainText(), "11")
        self.assertEqual(self.browser.position.value(), 12)

    def test_matching_uses_original_frame_indices_and_clears_unmatched_view(self):
        source = MemorySource(
            {
                "a": [FrameData("text", "left", None, 100_000_000)],
                "b": [
                    FrameData("text", str(i), None, i * 10_000_000) for i in range(12)
                ],
            }
        )
        entries = [
            BrowserStream(
                source,
                stream,
                (
                    source.index[stream.id][5:]
                    if stream.id == "b"
                    else source.index[stream.id]
                ),
            )
            for stream in source.info.streams
        ]
        self.browser.set_entries(entries)
        self.browser.compare.setChecked(True)
        self.idle()
        self.assertEqual(self.browser.right.text.toPlainText(), "10")
        self.browser.basis.setCurrentIndex(1)
        self.idle()
        self.assertIsNone(self.browser.right.frame)
        self.assertIn("未匹配", self.browser.status.text())

    def test_dynamic_camera_info_follows_image_timestamp(self):
        def camera(focal):
            return {
                "width": 8,
                "height": 6,
                "K": [focal, 0, 4, 0, focal, 3, 0, 0, 1],
                "D": [],
                "distortion_model": "plumb_bob",
            }

        source = MemorySource(
            {
                "image": [
                    FrameData(
                        "image",
                        np.zeros((6, 8, 3), np.uint8),
                        i * 1_000_000_000,
                        i * 1_000_000_000,
                    )
                    for i in range(2)
                ],
                "camera": [
                    FrameData(
                        "camera_info",
                        camera(100 + i),
                        i * 1_000_000_000,
                        i * 1_000_000_000,
                    )
                    for i in range(2)
                ],
            }
        )
        self.load(source)
        self.assertEqual(self.browser.calibration.K[0, 0], 100)
        self.browser.position.setValue(2)
        self.idle()
        self.assertEqual(self.browser.calibration.K[0, 0], 101)

    def test_arrow_keys_in_text_search_do_not_navigate_frames(self):
        source = MemorySource({"a": [FrameData("text", str(i)) for i in range(2)]})
        self.load(source)
        self.browser.show()
        self.browser.activateWindow()
        self.browser.left.search.setText("abc")
        self.browser.left.search.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.browser.left.search, Qt.Key_Right)
        self.assertEqual(self.browser.position.value(), 1)

    def test_empty_entries_clear_exported_frame_and_navigation(self):
        self.load(MemorySource({"a": [FrameData("text", "old")]}))
        self.browser.set_entries([])
        self.idle()
        self.assertEqual(self.browser.current_frames, [])
        self.assertFalse(self.browser.scene_button.isEnabled())

    def test_pending_frame_cannot_export_previous_payload(self):
        self.load(MemorySource({"a": [FrameData("text", str(i)) for i in range(2)]}))
        self.browser.position.setValue(2)
        self.assertEqual(self.browser.current_frames, [])
        self.assertFalse(self.browser.export_button.isEnabled())
        with patch("ui.widgets.recording_browser.QFileDialog.getSaveFileName") as save:
            self.browser.export_current()
            save.assert_not_called()
        self.idle()

    def test_restore_preserves_explicit_camera_and_does_not_dirty_on_frame_change(self):
        camera = {
            "width": 8,
            "height": 6,
            "K": [100, 0, 4, 0, 100, 3, 0, 0, 1],
            "D": [],
        }
        source = MemorySource(
            {
                "text": [FrameData("text", "one")],
                "image": [
                    FrameData("image", np.zeros((6, 8, 3), np.uint8), i, i)
                    for i in range(2)
                ],
                "camera1": [FrameData("camera_info", camera, 0, 0)],
                "camera2": [FrameData("camera_info", camera, 0, 0)],
            }
        )
        self.load(source)
        self.browser.restore_state(
            {
                "primary": [source.path, "image"],
                "camera": [source.path, "camera2"],
                "position": 2,
            }
        )
        self.idle()
        self.assertEqual(self.browser.camera.currentData(), (source.path, "camera2"))
        self.assertEqual(self.browser.position.value(), 2)
        self.assertIsNotNone(self.browser.calibration)
        self.assertIsNone(self.browser.state()["calibration"])

    def test_offset_edits_reuse_one_matcher(self):
        source = MemorySource(
            {
                key: [FrameData("text", str(i), i, i) for i in range(3)]
                for key in ("a", "b")
            }
        )
        self.load(source)
        self.browser.compare.setChecked(True)
        self.idle()
        for offset in range(10):
            self.browser.offset.setValue(offset)
            self.idle()
        self.assertEqual(len(self.browser._matcher_cache), 1)

    def test_source_change_cannot_report_match_from_old_timestamp_index(self):
        source = MemorySource(
            {key: [FrameData("text", key, 0, 0)] for key in ("a", "b")}
        )
        source.cache_token = lambda: "original"
        self.load(source)
        self.browser.compare.setChecked(True)
        self.idle()
        source.cache_token = lambda: "modified"
        self.browser._request_frame()
        self.idle()
        self.assertIsNone(self.browser.left.frame)
        self.assertIsNone(self.browser.right.frame)
        self.assertIn("源文件已更改", self.browser.status.text())
        self.assertNotIn("时间差", self.browser.status.text())


class RecordingWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.main_window import MainWindow

        self.temp = tempfile.TemporaryDirectory(prefix="PointLab数据浏览 ")
        self.base = Path(self.temp.name)
        self.env = patch.dict(
            os.environ, {"POINTLAB_HOME": str(self.base / "settings")}
        )
        self.env.start()
        self.window = MainWindow()

    def idle(self):
        wait_for(
            self.app,
            lambda: not (
                self.window._recording_importing
                or self.window._recording_queue
                or self.window._recording_threads()
                or self.window._scan_threads
                or self.window._threads
            ),
        )

    def tearDown(self):
        self.idle()
        self.window._project_baseline = self.window._project_signature()
        dispose_widget(self.window)
        self.app.processEvents()
        self.env.stop()
        self.temp.cleanup()

    def test_plain_image_and_text_open_through_normal_entry(self):
        image = self.base / "测试.png"
        Image.new("RGB", (20, 12), (20, 40, 60)).save(image)
        text = self.base / "说明.txt"
        text.write_text("点云与图像\n测试内容", encoding="utf-8")
        self.window.open_paths([str(image), str(text)])
        QTest.qWait(20)
        self.idle()
        self.assertEqual(len(self.window.recording_browser.entries), 2)
        self.assertEqual(self.window.recording_browser.left.frame.kind, "image")
        self.window._select_recording((str(text), "0"))
        self.idle()
        self.assertIn("测试内容", self.window.recording_browser.left.text.toPlainText())
        self.assertEqual(self.window.entities, [])

    def test_recording_header_color_and_colormap_work_on_either_cloud_pane(self):
        from core.model.entities import CloudEntity
        source = MemorySource({
            "cloud": [FrameData("pointcloud", CloudEntity("cloud", xyz=np.array([[0, 0, 1], [1, 0, 2]], np.float32)), 1, 1)],
            "image": [FrameData("image", np.zeros((12, 16, 3), np.uint8), 1, 1)],
        })
        browser = self.window.recording_browser
        browser.set_entries([BrowserStream(source, stream, source.index[stream.id])
                             for stream in source.info.streams])
        browser.compare.setChecked(True)
        self.window._show_recording_browser()
        self.idle()
        self.assertTrue(self.window.cmb_color.isEnabled())
        self.assertTrue(all(action.isEnabled() for action in self.window._cmap_group))
        next(action for action in self.window._cmap_group if action.data() == "inferno").trigger()
        self.assertEqual(browser.left.cloud.colormap, "inferno")
        browser.swap_views()
        self.idle()
        self.window._sync_view_controls(None)
        self.assertTrue(self.window.cmb_color.isEnabled())
        self.window.cmb_color.setCurrentIndex(self.window.cmb_color.findData("none"))
        self.assertEqual(browser.right.cloud.color_mode, "none")
        self.assertEqual(browser.right.cloud.colormap, "inferno")
        from core.calibration import CameraCalibration
        browser.calibration = CameraCalibration(np.array([[5., 0, 8], [0, 5, 6], [0, 0, 1]]),
                                                np.zeros(0), 16, 12, T_camera_lidar=np.eye(4))
        browser.calibration_panel.colorize.setChecked(True)
        self.idle()
        self.assertEqual(self.window.cmb_color.currentData(), "rgb")
        self.window.cmb_color.activated.emit(self.window.cmb_color.currentIndex())
        self.assertFalse(browser.calibration_panel.colorize.isChecked())
        self.assertEqual(browser.right.cloud.color_mode, "rgb")
        for button in (self.window.btn_process, self.window.btn_measure,
                       self.window.btn_select, self.window.btn_clip, self.window.btn_navigation):
            self.assertTrue(button.isHidden())
        self.window._show_cloud_browser()
        for button in (self.window.btn_process, self.window.btn_measure,
                       self.window.btn_select, self.window.btn_clip, self.window.btn_navigation):
            self.assertFalse(button.isHidden())

    def test_capture_folder_opens_synchronized_pair_at_first_matching_frame(self):
        from recording_fixtures import write_sensor_mcap

        folder = self.base / "capture"
        folder.mkdir()
        xyz = np.array([[0, 0, 1], [1, 2, 3]], np.float32)
        pixels = np.zeros((12, 16, 3), np.uint8)
        write_sensor_mcap(folder / "a_camera.mcap",
                          images=[(i * 10**9, pixels) for i in (2, 3)])
        cloud_path = folder / "z_cloud.mcap"
        write_sensor_mcap(cloud_path, clouds=[(i * 10**9, xyz) for i in (1, 2, 3)])
        with patch.object(SourceOverviewDialog, "exec", return_value=QDialog.Accepted):
            self.window.open_paths([str(folder)])
            self.idle()
        browser = self.window.recording_browser
        self.assertEqual(browser.left.frame.kind, "pointcloud")
        self.assertEqual(browser.right.frame.kind, "image")
        self.assertTrue(browser.compare.isChecked())
        self.assertEqual(browser.position.value(), 2)
        self.assertEqual(browser.position.maximum(), 3)
        browser.position.setValue(3)
        self.idle()
        self.window._reload_recording_source(str(cloud_path))
        self.idle()
        self.assertEqual(browser.position.value(), 3)
        self.assertTrue(browser.compare.isChecked())
        unrelated = self.base / "other_camera.mcap"
        write_sensor_mcap(unrelated, images=[(3 * 10**9, pixels)])
        self.window._open_recording_sources([str(unrelated)], prompt=False)
        self.idle()
        self.assertFalse(browser.compare.isChecked())
        self.assertEqual(browser.left.frame.kind, "image")

    def test_data_only_workspace_round_trip_and_duplicate_open(self):
        from ui.main_window import MainWindow

        file = self.base / "数据.csv"
        file.write_text("time,value\n1,2\n2,3\n", encoding="utf-8")
        self.window.open_paths([str(file)])
        QTest.qWait(20)
        self.idle()
        self.window.recording_browser.offset.setValue(25)
        self.idle()
        project = self.base / "数据.pls"
        self.assertTrue(self.window.save_project(str(project)))
        dispose_widget(self.window)
        self.app.processEvents()
        self.window = MainWindow()
        self.assertTrue(self.window.open_project(str(project)))
        self.idle()
        self.assertEqual(self.window.recording_browser.offset.value(), 25)
        self.assertEqual(self.window.recording_browser.left.frame.kind, "table")
        self.assertFalse(self.window._has_unsaved_project())
        self.window.open_paths([str(file)])
        QTest.qWait(20)
        self.idle()
        self.assertEqual(len(self.window.recording_browser.entries), 1)
        self.window._remove_recording(str(file))
        self.idle()
        self.assertEqual(self.window.recording_browser.entries, [])
        self.assertTrue(file.exists())

    def test_csv_pages_are_accessible_beyond_initial_preview(self):
        file = self.base / "多行.csv"
        file.write_text(
            "id,value\n" + "".join(f"{i},{i * 2}\n" for i in range(2100)),
            encoding="utf-8",
        )
        self.window.open_paths([str(file)])
        QTest.qWait(20)
        self.idle()
        browser = self.window.recording_browser
        self.assertEqual(browser.left.frame.value["rows"][0][0], "0")
        browser.left.next_page.click()
        self.idle()
        self.assertEqual(browser.left.frame.value["rows"][0][0], "1000")
        browser.left.previous_page.click()
        self.idle()
        self.assertEqual(browser.left.frame.value["rows"][0][0], "0")
        browser.left.next_page.click()
        self.idle()
        file.write_text("id,value\nchanged,7\n", encoding="utf-8")
        browser._request_frame()
        self.idle()
        self.assertIn("源文件已更改", browser.status.text())
        self.window.open_paths([str(file)])
        QTest.qWait(20)
        self.idle()
        self.assertEqual(browser.left.frame.value["rows"][0][0], "changed")

    def test_numeric_table_mapping_is_a_persistable_scene_cloud(self):
        from ui.widgets.text_cloud_import import TextCloudImportDialog

        file = self.base / "坐标.csv"
        file.write_text("intensity,z,y,x\n10,3,2,1\n20,6,5,4\n", encoding="utf-8")
        with patch.object(TextCloudImportDialog, "exec", return_value=QDialog.Accepted):
            self.window._import_numeric_text(str(file))
            self.idle()
        self.assertEqual(len(self.window.entities), 1)
        cloud = self.window.entities[0]
        np.testing.assert_array_equal(cloud.xyz, [[1, 2, 3], [4, 5, 6]])
        self.assertIsNone(cloud.path)
        self.assertTrue(file.exists())

    def test_close_during_inspection_cancels_and_closes_open_source(self):
        source = SampleSource()
        source.closed = False
        source.entered = False

        def inspect(cancel=None):
            source.entered = True
            while not cancel():
                time.sleep(0.005)
            return source.info

        source.inspect = inspect
        source.close = lambda: setattr(source, "closed", True)
        with (
            patch("ui.recording_session.discover_sources", return_value=[source.path]),
            patch("ui.recording_session.open_source", return_value=source),
        ):
            self.window._open_recording_sources([source.path])
            wait_for(self.app, lambda: source.entered)
            self.window.close()
            self.idle()
        self.assertTrue(source.closed)
        self.assertTrue(self.window._closing)

    def test_reload_action_reads_changed_file_without_losing_settings_or_dirty_state(self):
        path = self.base / "refresh.csv"
        path.write_text("id,value\nold,1\n", encoding="utf-8")
        self.window._open_recording_sources([str(path)], prompt=False)
        self.idle()
        browser = self.window.recording_browser
        browser.offset.setValue(25)
        self.idle()
        self.assertTrue(self.window.save_project(str(self.base / "refresh.pls")))
        browser.set_point_size(4)
        old_source = self.window._recording_sources[str(path)]
        path.write_text("id,value\nchanged,22\n", encoding="utf-8")
        browser.reload_action.trigger()
        self.idle()
        self.assertIsNot(self.window._recording_sources[str(path)], old_source)
        self.assertEqual(browser.left.frame.value["rows"], [["changed", "22"]])
        self.assertEqual(browser.offset.value(), 25)
        self.assertEqual(browser.point_size, 4)
        self.assertTrue(self.window._has_unsaved_project())

    def test_reconfigure_prefills_selection_and_keeps_original_frame_in_new_range(self):
        folder = self.base / "camera"
        folder.mkdir()
        for i in range(5):
            Image.new("RGB", (12, 8), (i, 0, 0)).save(folder / f"{i}.png")
        self.window._open_recording_sources([str(folder)], prompt=False)
        self.idle()
        browser = self.window.recording_browser
        browser.position.setValue(4)
        self.idle()
        self.assertTrue(self.window.save_project(str(self.base / "selection.pls")))
        seen = []

        def choose(dialog):
            seen.append(dialog.selection())
            dialog.set_selection({"sources": {str(folder): ["0"]},
                                  "range": {"mode": "frames", "first": 1, "last": 4}})
            return QDialog.Accepted

        with patch.object(SourceOverviewDialog, "exec", choose):
            self.window.scene_panel.recording_options_requested.emit(str(folder))
            self.idle()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["sources"], {str(folder): ["0"]})
        self.assertEqual(seen[0]["range"]["mode"], "all")
        self.assertEqual([ref.index for ref in browser.entries[0].frames], [1, 2, 3, 4])
        self.assertEqual(browser.position.value(), 3)
        self.assertEqual(browser.left.frame.metadata["filename"], "3.png")
        self.assertTrue(self.window._has_unsaved_project())

    def test_cancel_source_options_leaves_existing_view_usable(self):
        path = self.base / "keep.txt"
        path.write_text("keep original view", encoding="utf-8")
        self.window._open_recording_sources([str(path)], prompt=False)
        self.idle()
        browser = self.window.recording_browser
        old_source = self.window._recording_sources[str(path)]
        seen = []

        def cancel(dialog):
            seen.append(dialog.selection())
            return QDialog.Rejected

        with patch.object(SourceOverviewDialog, "exec", cancel):
            browser.options_action.trigger()
            self.idle()
        self.assertEqual(len(seen), 1)
        self.assertIs(self.window._recording_sources[str(path)], old_source)
        self.assertEqual(browser.left.frame.value, "keep original view")
        self.assertTrue(browser.isEnabled())
        self.assertIn("已取消", self.window.status_task.text())
        self.assertNotIn("正在", self.window.status_task.text())

    def test_reconfigure_removing_primary_returns_to_single_view(self):
        from mcap.writer import Writer

        path = self.base / "pair.mcap"
        with path.open("wb") as handle:
            writer = Writer(handle)
            writer.start()
            for topic in ("/a", "/b"):
                channel = writer.register_channel(topic, "json", 0)
                writer.add_message(channel, 100, b'{"value": 1}', 100)
            writer.finish()
        self.window._open_recording_sources([str(path)], prompt=False)
        self.idle()
        browser = self.window.recording_browser
        browser.compare.setChecked(True)
        self.idle()
        secondary = browser.secondary.currentData()

        def choose(dialog):
            dialog.set_selection({"sources": {str(path): [secondary[1]]}, "range": {"mode": "all"}})
            return QDialog.Accepted

        with patch.object(SourceOverviewDialog, "exec", choose):
            browser.options_action.trigger()
            self.idle()
        self.assertEqual(browser.primary.currentData(), secondary)
        self.assertFalse(browser.compare.isChecked())
        self.assertTrue(browser.right.isHidden())
        self.assertFalse(browser.compare.isEnabled())


if __name__ == "__main__":
    unittest.main()
