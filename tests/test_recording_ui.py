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

from PySide6.QtCore import Qt
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
        dialog.close()

    def test_unchecked_stream_is_excluded(self):
        source = SampleSource()
        dialog = SourceOverviewDialog([source])
        dialog.tree.topLevelItem(0).child(0).setCheckState(0, Qt.Unchecked)
        self.assertEqual(dialog.selection()["sources"][source.path], ["imu"])
        dialog.close()

    def test_recordings_remain_in_scene_when_cloud_list_is_rebuilt(self):
        source = SampleSource()
        panel = ScenePanel()
        panel.set_recordings([(source.info, ["camera", "imu"])])
        panel.set_entities([])
        item = panel.recording_item(source.path, "camera")
        self.assertIsNotNone(item)
        self.assertEqual(item.data(0, RECORDING_ROLE), (source.path, "camera"))
        self.assertFalse(panel.empty.isVisibleTo(panel))
        panel.close()

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
        dialog.close()


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
        self.browser.close()

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
        self.window.close()
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
        self.window.close()
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


if __name__ == "__main__":
    unittest.main()
