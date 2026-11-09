"""Selection, filtering and cancellation contracts of the package picker."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from core.io.recording_types import RecordingSource, SourceInfo, StreamInfo
from ui.widgets.source_overview import SourceOverviewDialog


class InventorySource(RecordingSource):
    def __init__(self, path, streams):
        super().__init__(os.path.abspath(path))
        self.info = SourceInfo(
            self.path, "MCAP", streams, 1_000_000_000, 3_000_000_000, 4096
        )
        self.scan_streams = []

    def build_index(self, stream_ids=None, cancel=None, progress=None):
        self.scan_streams.append(stream_ids)
        for stream in self.info.streams:
            stream.count = 42
        progress(1.0)


def wait_for(app, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    raise AssertionError("Package picker operation did not finish")


class SourceOverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.first = InventorySource(
            "day-one/capture.mcap",
            [
                StreamInfo("camera", "/sensor_head/front", "image", "sensor_msgs/msg/Image", 12),
                StreamInfo("imu", "/sensors/imu", "imu", "sensor_msgs/msg/Imu", 120),
            ],
        )
        self.second = InventorySource(
            "day-two/capture.mcap",
            [StreamInfo("lidar", "/points", "pointcloud", "sensor_msgs/msg/PointCloud2")],
        )
        self.dialog = SourceOverviewDialog([self.first, self.second])

    def tearDown(self):
        if self.dialog._scan is not None:
            self.dialog._scan.requestInterruption()
            wait_for(self.app, lambda: self.dialog._scan is None)
        self.dialog.close()
        self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def visible_ids(self):
        result = []
        for index in range(self.dialog.tree.topLevelItemCount()):
            root = self.dialog.tree.topLevelItem(index)
            if root.isHidden():
                continue
            for child_index in range(root.childCount()):
                item = root.child(child_index)
                if not item.isHidden():
                    result.append(item.data(0, Qt.UserRole)[1])
        return result

    def test_details_are_opt_in_and_keep_selection(self):
        before = self.dialog.selection()
        self.assertTrue(self.dialog.detail_panel.isHidden())
        self.dialog.details_toggle.click()
        self.assertFalse(self.dialog.detail_panel.isHidden())
        self.dialog.details_toggle.click()
        self.assertTrue(self.dialog.detail_panel.isHidden())
        self.assertEqual(self.dialog.selection(), before)

    def test_reconfigured_time_range_keeps_original_shared_time_origin(self):
        self.dialog.set_selection({
            "sources": {self.first.path: ["camera"]},
            "range": {"mode": "time", "start_seconds": 1, "end_seconds": 2,
                      "origin_ns": 1_000_000_000},
        })
        self.dialog.start_seconds.setValue(1.5)
        self.assertEqual(self.dialog.selection()["range"].get("origin_ns"), 1_000_000_000)
        self.assertIn("原导入起点", self.dialog.range_hint.text())

    def test_search_matches_topic_schema_and_source_path_case_insensitively(self):
        for query, expected in (
            ("  SENSOR_HEAD  ", ["camera"]),
            ("msg/image", ["camera"]),
            ("day-two", ["lidar"]),
            ("capture.mcap", ["camera", "imu", "lidar"]),
            ("no match", []),
            ("", ["camera", "imu", "lidar"]),
        ):
            with self.subTest(query=query):
                self.dialog.search.setText(query)
                self.assertEqual(self.visible_ids(), expected)
        self.assertEqual(self.dialog.selection()["sources"], {
            self.first.path: ["camera", "imu"], self.second.path: ["lidar"]
        })

    def test_kind_and_search_filters_are_combined(self):
        self.dialog.kind_filter.setCurrentIndex(self.dialog.kind_filter.findData("image"))
        self.assertEqual(self.visible_ids(), ["camera"])
        self.dialog.search.setText("day-two")
        self.assertEqual(self.visible_ids(), [])
        self.dialog.kind_filter.setCurrentIndex(self.dialog.kind_filter.findData("pointcloud"))
        self.assertEqual(self.visible_ids(), ["lidar"])

    def test_visible_actions_retain_hidden_selections_and_update_total_counts(self):
        self.dialog.search.setText("msg/image")
        self.dialog.deselect_visible_button.click()
        self.assertEqual(self.dialog.selection()["sources"], {
            self.first.path: ["imu"], self.second.path: ["lidar"]
        })
        self.assertIn("已选 2 / 3", self.dialog.selection_summary.text())
        self.assertIn("显示 1", self.dialog.selection_summary.text())
        self.dialog.select_visible_button.click()
        self.assertEqual(self.dialog.selection()["sources"][self.first.path], ["camera", "imu"])
        self.dialog.search.clear()
        self.dialog.deselect_visible_button.click()
        self.assertEqual(self.dialog.selection()["sources"], {})
        self.assertIn("已选 0 / 3", self.dialog.selection_summary.text())

    def test_restore_matches_full_source_path_and_existing_stream_ids(self):
        self.dialog.search.setText("day-two")
        selected = {
            "sources": {self.first.path: ["imu", "removed"], "gone.mcap": ["camera"]},
            "range": {"mode": "frames", "first": 4, "last": 8,
                      "start_seconds": 1.25, "end_seconds": 3.5},
        }
        self.dialog.set_selection(selected)
        self.assertEqual(self.dialog.selection(), {
            "sources": {self.first.path: ["imu"]}, "range": selected["range"]
        })
        self.assertEqual(self.visible_ids(), ["lidar"])
        self.assertEqual(self.dialog.first.value(), 5)
        self.assertEqual(self.dialog.last.value(), 9)
        self.assertIn("已选 1 / 3", self.dialog.selection_summary.text())

    def test_all_range_hides_bounds_and_restoring_time_shows_only_time_fields(self):
        self.assertEqual(self.dialog.selection()["range"]["mode"], "all")
        self.assertTrue(self.dialog.range_controls.isHidden())
        self.dialog.set_selection({
            "sources": {self.second.path: ["lidar"]},
            "range": {"mode": "time", "start_seconds": 2.5, "end_seconds": 5.75},
        })
        self.assertFalse(self.dialog.range_controls.isHidden())
        self.assertTrue(self.dialog.first.isHidden())
        self.assertFalse(self.dialog.start_seconds.isHidden())
        self.assertEqual(self.dialog.selection()["range"]["start_seconds"], 2.5)
        self.assertEqual(self.dialog.selection()["range"]["end_seconds"], 5.75)

    def test_scan_retains_hidden_and_unchecked_streams_and_active_filters(self):
        self.dialog.tree.topLevelItem(0).child(0).setCheckState(0, Qt.Unchecked)
        self.dialog.search.setText("msg/image")
        self.dialog._start_scan()
        wait_for(self.app, lambda: self.dialog._scan is None)
        self.assertEqual(self.first.scan_streams, [["imu"]])
        self.assertEqual(self.second.scan_streams, [["lidar"]])
        self.assertEqual(self.visible_ids(), ["camera"])
        self.assertEqual(self.dialog.selection()["sources"], {
            self.first.path: ["imu"], self.second.path: ["lidar"]
        })
        self.assertEqual(self.dialog.tree.topLevelItem(1).child(0).text(2), "42")

    def test_full_probe_scan_discovers_streams_without_selecting_other_empty_sources(self):
        self.dialog.close()
        self.dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.first.info.streams = []
        self.second.info.streams = []

        def discover(stream_ids=None, cancel=None, progress=None):
            self.first.scan_streams.append(stream_ids)
            self.first.info.streams = [StreamInfo("late", "/late/lidar", "pointcloud", count=3)]

        self.first.build_index = discover
        self.dialog = SourceOverviewDialog([self.first, self.second])
        self.dialog.tree.topLevelItem(1).setCheckState(0, Qt.Unchecked)
        self.dialog.search.setText("day-one")
        self.dialog._start_scan()
        wait_for(self.app, lambda: self.dialog._scan is None)
        self.assertEqual(self.first.scan_streams, [None])
        self.assertEqual(self.dialog.selection()["sources"], {self.first.path: ["late"]})
        self.assertEqual(self.dialog.tree.topLevelItem(1).checkState(0), Qt.Unchecked)
        self.assertEqual(self.dialog.search.text(), "day-one")

    def test_restore_empty_stream_list_does_not_select_unknown_file_for_scanning(self):
        self.first.info.streams = []
        self.dialog.set_selection({"sources": {self.first.path: []}})
        self.assertEqual(self.dialog.tree.topLevelItem(0).checkState(0), Qt.Unchecked)
        self.dialog._start_scan()
        self.assertIsNone(self.dialog._scan)
        self.assertEqual(self.first.scan_streams, [])

    def test_scan_prevents_filter_changes_while_source_metadata_is_being_replaced(self):
        started, release = threading.Event(), threading.Event()

        def slow_scan(stream_ids=None, cancel=None, progress=None):
            started.set()
            while not cancel() and not release.wait(0.005):
                pass

        self.first.build_index = slow_scan
        self.dialog.search.setText("day-one")
        self.dialog._start_scan()
        try:
            wait_for(self.app, started.is_set)
            self.assertFalse(self.dialog.search.isEnabled())
            self.assertFalse(self.dialog.kind_filter.isEnabled())
            self.assertFalse(self.dialog.deselect_visible_button.isEnabled())
        finally:
            release.set()
            wait_for(self.app, lambda: self.dialog._scan is None)
        self.assertTrue(self.dialog.search.isEnabled())
        self.assertTrue(self.dialog.kind_filter.isEnabled())
        self.assertEqual(self.dialog.search.text(), "day-one")

    def test_close_waits_for_scan_to_stop_before_dismissing_dialog(self):
        started, cancelled, release = threading.Event(), threading.Event(), threading.Event()

        def slow_scan(stream_ids=None, cancel=None, progress=None):
            started.set()
            while not cancel():
                if release.wait(0.005):
                    return
            cancelled.set()
            release.wait(3)

        self.first.build_index = slow_scan
        self.dialog.show()
        self.dialog._start_scan()
        try:
            wait_for(self.app, started.is_set)
            self.dialog.close()
            wait_for(self.app, cancelled.is_set)
            self.assertTrue(self.dialog.isVisible())
            self.assertIsNotNone(self.dialog._scan)
        finally:
            release.set()
            wait_for(self.app, lambda: self.dialog._scan is None)
        self.assertFalse(self.dialog.isVisible())
        self.assertEqual(self.dialog.result(), QDialog.Rejected)

    def test_details_keep_clock_and_calibration_information_readable(self):
        stream = self.first.info.streams[0]
        stream.metadata = {"clock_domain": "capture", "calibration": {"source": "nominal"}}
        self.first.info.warnings = ["Clock synchronization is unverified."]
        root = self.dialog.tree.topLevelItem(0)
        self.dialog.tree.setCurrentItem(root.child(0))
        details = self.dialog.details.toPlainText()
        self.assertIn("capture", details)
        self.assertIn("nominal", details)
        self.assertIn("Clock synchronization is unverified.", details)
        self.assertNotIn("{'", details)
        self.assertEqual(root.toolTip(0), self.first.path)
        self.assertIn(stream.name, root.child(0).toolTip(0))
        self.assertIn(stream.message_type, details)

    def test_empty_selection_and_reversed_range_do_not_accept(self):
        self.dialog.set_selection({"sources": {}, "range": {"mode": "all"}})
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.Rejected)
        self.assertTrue(self.dialog.status.text())
        self.dialog.set_selection({
            "sources": {self.first.path: ["camera"]},
            "range": {"mode": "frames", "first": 10, "last": 2},
        })
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.Rejected)
        self.dialog.last.setValue(20)
        self.dialog.accept()
        self.assertEqual(self.dialog.result(), QDialog.Accepted)


if __name__ == "__main__":
    unittest.main()
