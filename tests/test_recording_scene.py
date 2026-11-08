"""Scene filtering must never turn hidden data into an implicit file operation."""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QPointF, Qt
from PySide6.QtGui import QContextMenuEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMenu

from core.io.recording_types import SourceInfo, StreamInfo
from core.model.entities import CloudEntity
from ui.widgets.scene_panel import ENTITY_ROLE, PATH_ROLE, RECORDING_ROLE, ScenePanel


class RecordingSceneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cloud_root = self.root / "clouds"
        self.front = self.cloud("Lidar/front_scan.PLY", visible=True)
        self.rear = self.cloud("Rear/other_scan.ply")
        self.source_a = SourceInfo(
            str(self.root / "captures" / "DriveA.MCAP"), "MCAP",
            [StreamInfo("front", "/rig/camera/front/image_raw", "image",
                        "sensor_msgs/msg/Image", 12),
             StreamInfo("imu", "/imu/raw", "imu", "sensor_msgs/msg/Imu", 120)],
        )
        self.source_b = SourceInfo(
            str(self.root / "other" / "DriveB.MCAP"), "MCAP",
            [StreamInfo("points", "/lidar/points", "pointcloud", count=8)],
        )
        self.recordings = [(self.source_a, ["front", "imu"]), (self.source_b, ["points"])]
        self.panel = ScenePanel()
        self.panel.resize(380, 700)
        self.panel.add_roots([str(self.cloud_root)])
        self.panel.set_entities([self.front, self.rear])
        self.panel.set_recordings(self.recordings)
        self.panel.show()
        self.app.processEvents()

    def tearDown(self):
        popup = QApplication.activePopupWidget()
        if popup:
            popup.close()
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def cloud(self, relative, visible=False):
        path = self.cloud_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return CloudEntity(path.name, str(path), visible=visible, load_state="unloaded")

    def items(self):
        def walk(parent):
            for index in range(parent.childCount()):
                item = parent.child(index)
                yield item
                yield from walk(item)
        return list(walk(self.panel.list.invisibleRootItem()))

    def cloud_item(self, entity):
        return next(item for item in self.items() if item.data(0, ENTITY_ROLE) is entity)

    def folder(self, path):
        return next(item for item in self.items() if item.data(0, PATH_ROLE) == str(path))

    def search(self, query):
        field = self.panel.findChild(QLineEdit, "sceneSearch")
        self.assertIsNotNone(field, "The scene inventory needs a search field")
        field.setText(query)
        self.app.processEvents()

    def matches(self):
        result = set()
        for item in self.items():
            if item.isHidden():
                continue
            entity = item.data(0, ENTITY_ROLE)
            recording = item.data(0, RECORDING_ROLE)
            if entity is not None:
                result.add(entity.name)
            elif recording is not None and recording[1] is not None:
                result.add(recording)
        return result

    def menu(self, item):
        tree = self.panel.list
        tree.scrollToItem(item)
        self.app.processEvents()
        point = tree.visualItemRect(item).center()
        QTest.mouseClick(tree.viewport(), Qt.RightButton, Qt.NoModifier, point)
        QApplication.sendEvent(tree.viewport(), QContextMenuEvent(
            QContextMenuEvent.Mouse, point, tree.viewport().mapToGlobal(point)))
        self.app.processEvents()
        menu = QApplication.activePopupWidget()
        self.assertIsInstance(menu, QMenu)
        return menu

    def test_search_matches_file_topic_and_source_case_insensitively(self):
        cases = [
            (" FRONT_SCAN ", {"front_scan.PLY"}, 1),
            ("/CAMERA/FRONT", {(self.source_a.path, "front")}, 1),
            ("drivea.mcap", {(self.source_a.path, "front"), (self.source_a.path, "imu")}, 2),
            ("Rear", {"other_scan.ply"}, 1),
        ]
        for query, expected, count in cases:
            with self.subTest(query=query):
                self.search(query)
                self.assertEqual(self.matches(), expected)
                self.assertRegex(self.panel.lbl_count.text(), rf"{count}\s*/\s*5")

    def test_parents_follow_matching_descendants_and_no_match_has_a_clear_state(self):
        self.search("front_scan")
        self.assertFalse(self.folder(self.cloud_root).isHidden())
        self.assertFalse(self.folder(self.cloud_root / "Lidar").isHidden())
        self.assertTrue(self.folder(self.cloud_root / "Rear").isHidden())
        recording = self.panel.recording_item(self.source_a.path, "front")
        self.assertTrue(recording.parent().isHidden())
        self.assertTrue(recording.parent().parent().isHidden())
        self.search("missing-stream")
        message = self.panel.findChild(QLabel, "sceneNoMatches")
        self.assertIsNotNone(message)
        self.assertTrue(message.isVisibleTo(self.panel))
        self.assertFalse(self.panel.empty.isVisibleTo(self.panel))
        self.assertEqual(self.matches(), set())
        self.assertRegex(self.panel.lbl_count.text(), r"0\s*/\s*5")
        self.search("")
        self.assertFalse(message.isVisibleTo(self.panel))
        self.assertEqual(len(self.matches()), 5)

    def test_hidden_cloud_and_recording_selections_survive_filtering_and_rebuild(self):
        cloud = self.cloud_item(self.front)
        recording = self.panel.recording_item(self.source_a.path, "front")
        cloud.setSelected(True)
        recording.setSelected(True)
        recording.parent().setSelected(True)
        self.panel.list.setCurrentItem(recording, 0, QItemSelectionModel.NoUpdate)
        events = []
        self.panel.visibility_changed.connect(lambda value: events.append(("visibility", value)))
        self.panel.entity_selected.connect(lambda value: events.append(("cloud", value)))
        self.panel.recording_selected.connect(lambda value: events.append(("recording", value)))

        self.search("other_scan")
        self.panel.set_entities([self.front, self.rear])
        self.panel.set_recordings(self.recordings)
        cloud = self.cloud_item(self.front)
        recording = self.panel.recording_item(self.source_a.path, "front")
        self.assertTrue(cloud.isHidden())
        self.assertTrue(cloud.isSelected())
        self.assertTrue(recording.isSelected())
        self.assertTrue(recording.parent().isSelected())
        self.assertIs(self.panel.list.currentItem(), recording)
        self.assertTrue(self.front.visible)
        self.assertFalse(self.rear.visible)
        self.assertEqual(events, [])
        self.search("")
        self.assertEqual(set(self.panel.list.selectedItems()), {cloud, recording, recording.parent()})

    def test_search_expands_matches_then_restores_expansion_after_rebuild(self):
        self.folder(self.cloud_root / "Lidar").setExpanded(False)
        self.panel.recording_item(self.source_a.path, "front").parent().setExpanded(False)
        self.search("front")
        self.assertTrue(self.folder(self.cloud_root / "Lidar").isExpanded())
        self.assertTrue(self.panel.recording_item(self.source_a.path, "front").parent().isExpanded())
        self.panel.set_entities([self.front, self.rear])
        self.assertTrue(self.cloud_item(self.rear).isHidden())
        self.search("")
        self.assertFalse(self.folder(self.cloud_root / "Lidar").isExpanded())
        self.assertFalse(self.panel.recording_item(self.source_a.path, "front").parent().isExpanded())
        self.assertTrue(self.folder(self.cloud_root / "Rear").isExpanded())

    def test_filter_tracks_incremental_import_rename_and_removal(self):
        self.search("new_capture")
        new = self.cloud("Lidar/new_capture.ply")
        self.panel.add_entity(new)
        self.assertEqual(self.matches(), {"new_capture.ply"})
        self.assertRegex(self.panel.lbl_count.text(), r"1\s*/\s*6")
        new.name = "renamed.ply"
        new.path = str(self.cloud_root / "Lidar" / "renamed.ply")
        self.panel.update_entity(new)
        self.assertEqual(self.matches(), set())
        self.search("renamed")
        self.assertEqual(self.matches(), {"renamed.ply"})
        self.panel.remove_entity(new)
        self.assertEqual(self.matches(), set())
        self.assertRegex(self.panel.lbl_count.text(), r"0\s*/\s*5")

    def test_saved_tree_state_uses_unfiltered_expansion(self):
        self.folder(self.cloud_root / "Lidar").setExpanded(False)
        self.search("front_scan")
        state = self.panel.tree_state()
        self.assertNotIn(str(self.cloud_root / "Lidar"), state["expanded"])
        self.panel.restore_tree_state(state)
        self.assertTrue(self.folder(self.cloud_root / "Lidar").isExpanded())
        self.assertTrue(self.cloud_item(self.rear).isHidden())
        self.search("")
        self.assertFalse(self.folder(self.cloud_root / "Lidar").isExpanded())

    def test_path_remap_preserves_hidden_folder_selection_and_collapse(self):
        hidden = self.folder(self.cloud_root / "Rear")
        hidden.setSelected(True)
        hidden.setExpanded(False)
        self.search("front_scan")
        replacement = self.root / "renamed-clouds"
        for entity in (self.front, self.rear):
            entity.path = str(replacement / Path(entity.path).relative_to(self.cloud_root))
        self.panel.remap_paths(str(self.cloud_root), str(replacement))
        remapped = self.folder(replacement / "Rear")
        self.assertTrue(remapped.isHidden())
        self.assertTrue(remapped.isSelected())
        self.search("")
        self.assertFalse(remapped.isExpanded())

    def test_regroup_preserves_hidden_folder_selection_and_collapse(self):
        hidden = self.folder(self.cloud_root / "Rear")
        hidden.setSelected(True)
        hidden.setExpanded(False)
        self.search("front_scan")
        self.panel.regroup_paths([str(self.cloud_root / "Rear")], str(self.cloud_root / "Lidar"))
        moved = self.folder(self.cloud_root / "Lidar" / "Rear")
        self.assertTrue(moved.isHidden())
        self.assertTrue(moved.isSelected())
        self.search("")
        self.assertFalse(moved.isExpanded())

    def test_filtered_recursive_selection_excludes_hidden_files_and_folder_paths(self):
        folder = self.folder(self.cloud_root)
        folder.setSelected(True)
        self.cloud_item(self.rear).setSelected(True)
        self.search("front_scan")
        payload = self.panel._selection_payload()
        self.assertEqual(payload["entities"], [self.front])
        self.assertEqual(payload["folders"], [])
        self.assertEqual(payload["source_paths"], [self.front.path])
        self.assertEqual(self.panel.list._selected_source_paths(), [self.front.path])
        self.assertTrue(self.cloud_item(self.rear).isSelected())
        self.search("")
        payload = self.panel._selection_payload()
        self.assertEqual(set(payload["entities"]), {self.front, self.rear})
        self.assertEqual(payload["folders"], [str(self.cloud_root)])
        self.assertEqual(payload["source_paths"], [str(self.cloud_root)])

    def test_filtered_folder_menu_cannot_rename_or_remove_hidden_contents(self):
        self.search("front_scan")
        removed = []
        self.panel.remove_selection_requested.connect(removed.append)
        menu = self.menu(self.folder(self.cloud_root))
        actions = {action.text(): action for action in menu.actions()}
        self.assertFalse(actions["重命名"].isEnabled())
        actions["删除"].trigger()
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["entities"], [self.front])
        self.assertEqual(removed[0]["source_paths"], [self.front.path])
        self.assertEqual(removed[0]["folders"], [])
        self.assertTrue(Path(self.front.path).exists())
        self.assertTrue(Path(self.rear.path).exists())

    def test_filtered_name_drag_only_requests_matching_source_files(self):
        self.search("front_scan")
        self.folder(self.cloud_root).setSelected(True)
        item = self.cloud_item(self.front)
        item.setSelected(True)
        self.cloud_item(self.rear).setSelected(True)
        tree = self.panel.list
        rect = tree.visualItemRect(item)
        start = QPoint(tree._check_rect(item).right() + 8, rect.center().y())
        finish = tree.visualItemRect(self.folder(self.cloud_root)).center()
        moves = []
        self.panel.move_requested.connect(lambda paths, target: moves.append((paths, target)))
        QTest.mousePress(tree.viewport(), Qt.LeftButton, Qt.NoModifier, start)
        QApplication.sendEvent(tree.viewport(), QMouseEvent(
            QEvent.MouseMove, QPointF(finish), QPointF(tree.viewport().mapToGlobal(finish)),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QTest.mouseRelease(tree.viewport(), Qt.LeftButton, Qt.NoModifier, finish)
        self.assertEqual(moves, [([self.front.path], str(self.cloud_root))])
        self.assertTrue(Path(self.rear.path).exists())

    def test_recording_actions_use_full_source_identity_for_roots_and_streams(self):
        duplicate = SourceInfo(str(self.root / "duplicate" / "DriveA.MCAP"), "MCAP",
                               [StreamInfo("front", "/other/front", "image", count=1)])
        self.panel.set_recordings([*self.recordings, (duplicate, ["front"])])
        requested, reloaded = [], []
        self.assertTrue(hasattr(self.panel, "recording_options_requested"))
        self.assertTrue(hasattr(self.panel, "recording_reload_requested"))
        self.panel.recording_options_requested.connect(requested.append)
        self.panel.recording_reload_requested.connect(reloaded.append)
        for path in (self.source_a.path, duplicate.path):
            stream = self.panel.recording_item(path, "front")
            for item in (stream.parent(), stream):
                menu = self.menu(item)
                actions = {action.text(): action for action in menu.actions()}
                actions["数据包概览 / 重新选择…"].trigger()
                actions["重新读取数据源"].trigger()
                menu.close()
                self.assertEqual(requested[-1], path)
                self.assertEqual(reloaded[-1], path)
        self.assertEqual(len(requested), 4)
        self.assertEqual(len(reloaded), 4)


if __name__ == "__main__":
    unittest.main()
