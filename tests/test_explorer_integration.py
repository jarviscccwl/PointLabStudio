import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from ui.main_window import MainWindow


class ExplorerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="PointLab目录 ")
        self.base = Path(self.temp.name)
        self.env = patch.dict("os.environ", {"POINTLAB_HOME": str(self.base / "settings")})
        self.env.start()
        self.root = self.base / "数据集"
        self.files = []
        for group in ("分组A", "分组B", "分组C"):
            folder = self.root / group
            folder.mkdir(parents=True)
            for index in range(2):
                path = folder / f"点云{index}.xyz"
                path.write_text("0 0 0\n1 0 0\n0 1 0\n", encoding="utf-8")
                self.files.append(path)
        self.window = MainWindow()
        self.window._restoring_project = True
        self.window._register_files([str(p) for p in self.files])
        for entity in self.window.entities:
            entity.xyz = np.zeros((3, 3), dtype=np.float32)
            entity.loaded, entity.load_state = 1.0, "ready"
        self.window._restoring_project = False

    def tearDown(self):
        self.wait_idle()
        self.window._project_baseline = self.window._project_signature()
        self.window.close()
        self.app.processEvents()
        self.env.stop()
        self.temp.cleanup()

    def wait_idle(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not (getattr(self.window, "_source_operation", None)
                    or self.window._threads or self.window._scan_threads):
                return
            time.sleep(.01)
        self.fail("Background file operation did not finish")

    def add_roots(self):
        self.assertTrue(hasattr(self.window.scene_panel, "add_roots"))
        self.window.scene_panel.add_roots([str(self.root)])

    def test_bulk_registration_builds_tree_once_and_keeps_frames_unloaded(self):
        paths = [str(self.root / f"{index:06d}.bin") for index in range(4541)]
        panel = self.window.scene_panel
        self.window._restoring_project = True
        with patch.object(panel, "_rebuild", wraps=panel._rebuild) as rebuild, \
             patch.object(panel, "add_entity", side_effect=AssertionError("per-frame rebuild")):
            self.window._register_files(paths + paths[:2])
        self.window._restoring_project = False
        rebuild.assert_called_once()
        self.assertEqual(len(self.window.entities), 4547)
        self.assertTrue(all(e.xyz is None for e in self.window.entities[6:]))
        self.assertEqual([e.path for e in self.window.entities[6:]], paths)

    def test_number_keys_select_color_in_scene_and_viewport(self):
        from core.model.entities import ScalarField
        from ui.main_window import COLOR_SHORTCUT_MODES
        entity = self.window.entities[0]
        entity.fields["intensity"] = ScalarField("intensity", np.arange(3))
        self.window.scene_panel.select_entity(entity)
        self.window.show()
        self.window.activateWindow()
        for widget in (self.window.scene_panel.list, self.window.viewport):
            widget.setFocus()
            self.app.processEvents()
            for key, mode in enumerate(COLOR_SHORTCUT_MODES, 1):
                QTest.keyClick(widget, Qt.Key_0 + key)
                self.assertEqual(entity.color_mode, mode)
                self.assertEqual(self.window.cmb_color.currentData(), mode)
        self.window.log_view.setReadOnly(False)
        self.window.btn_log.setChecked(True)
        self.window.log_view.setFocus()
        self.app.processEvents()
        QTest.keyClick(self.window.log_view, Qt.Key_1)
        self.assertEqual(entity.color_mode, "none")
        self.assertEqual([self.window.cmb_color.itemData(i)
                          for i in range(self.window.cmb_color.count())],
                         list(COLOR_SHORTCUT_MODES))
        self.assertNotIn("auto", COLOR_SHORTCUT_MODES)

    def test_project_preserves_import_roots_and_expansion(self):
        self.add_roots()
        self.window.scene_panel.restore_tree_state({
            "roots": [str(self.root)], "expanded": [str(self.root), str(self.root / "分组B")],
        })
        project = self.base / "工作区.pls"
        self.assertTrue(self.window.save_project(str(project)))
        data = json.loads(project.read_text(encoding="utf-8"))
        self.assertIn("scene_tree", data)
        self.assertEqual(data["scene_tree"]["roots"], ["数据集"])
        self.assertIn("数据集/分组B", data["scene_tree"]["expanded"])

    def test_batch_remove_is_one_undo_and_never_deletes_disk_files(self):
        self.add_roots()
        self.assertTrue(hasattr(self.window, "_remove_selection"))
        self.window._remove_selection({
            "entities": [self.window.entities[0]], "folders": [str(self.root / "分组B")],
        })
        self.assertEqual(len(self.window.entities), 3)
        self.assertTrue(all(p.exists() for p in self.files))
        self.window._undo()
        self.assertEqual(len(self.window.entities), 6)
        self.window._redo()
        self.assertEqual(len(self.window.entities), 3)

    def test_rename_updates_disk_active_paths_and_removed_undo_paths(self):
        self.add_roots()
        self.assertTrue(hasattr(self.window, "_rename_source"))
        removed = self.window.entities[0]
        self.window._remove_selection({"entities": [removed], "folders": []})
        old = self.root / "分组A"
        self.window._rename_source(str(old), "新分组")
        self.wait_idle()
        self.assertFalse(old.exists())
        self.assertTrue((self.root / "新分组" / "点云0.xyz").exists())
        self.window._undo()
        self.assertIn(removed, self.window.entities)
        self.assertEqual(removed.path, str(self.root / "新分组" / "点云0.xyz"))
        self.assertTrue(all(Path(e.path).exists() for e in self.window.entities))

    def test_external_move_appends_and_retains_existing_clouds(self):
        self.add_roots()
        self.assertTrue(hasattr(self.window, "_move_sources"))
        external = self.base / "新增.xyz"
        external.write_text("1 2 3\n2 3 4\n3 4 5\n")
        original_entities = list(self.window.entities)
        self.window._move_sources([str(external)], str(self.root / "分组C"))
        self.wait_idle()
        self.assertFalse(external.exists())
        moved = self.root / "分组C" / "新增.xyz"
        self.assertTrue(moved.exists())
        self.assertEqual(len(self.window.entities), 7)
        self.assertTrue(all(e in self.window.entities for e in original_entities))

    def test_rename_collision_preserves_both_files_and_scene(self):
        self.add_roots()
        self.assertTrue(hasattr(self.window, "_rename_source"))
        original = self.window.entities[0]
        self.window._rename_source(original.path, "点云1.xyz")
        self.wait_idle()
        self.assertEqual(original.path, str(self.files[0]))
        self.assertTrue(all(p.exists() for p in self.files))
        self.assertEqual(len(self.window.entities), 6)

    def test_batch_undo_and_redo_preserve_files_imported_after_removal(self):
        self.add_roots()
        target = self.window.entities[0]
        self.window._remove_selection({"entities": [target], "folders": []})
        added = self.base / "后来导入.xyz"
        added.write_text("0 0 0\n1 2 3\n")
        self.window.open_paths([str(added)])
        self.wait_idle()
        imported = next(e for e in self.window.entities if e.path == str(added))
        self.window._undo()
        self.assertIn(target, self.window.entities)
        self.assertIn(imported, self.window.entities)
        self.window._redo()
        self.assertNotIn(target, self.window.entities)
        self.assertIn(imported, self.window.entities)

    def test_reloading_is_blocked_during_path_operation(self):
        with patch.object(self.window, "_source_operation", object()):
            self.window._reload_entity(self.window.entities[0])
            self.assertIsNone(self.window._filter_thread)

    def test_project_open_round_trip_preserves_collapsed_children(self):
        self.add_roots()
        self.window.scene_panel.restore_tree_state({"roots": [str(self.root)],
                                                   "expanded": [str(self.root)]})
        project = self.base / "折叠状态.pls"
        self.assertTrue(self.window.save_project(str(project)))
        self.window._project_baseline = self.window._project_signature()
        self.window.close()
        self.app.processEvents()
        self.window = MainWindow()
        self.assertTrue(self.window.open_project(str(project)))
        self.wait_idle()
        expanded = self.window.scene_panel.tree_state()["expanded"]
        self.assertNotIn(str(self.root / "分组B"), expanded)

    def test_directory_scan_includes_empty_folders_and_retains_existing_entities(self):
        empty = self.root / "空文件夹"
        empty.mkdir()
        original = list(self.window.entities)
        self.window.open_paths([str(self.root)])
        self.wait_idle()
        self.assertEqual(self.window.entities, original)
        self.assertIn(str(empty), self.window.scene_panel.tree_state()["directories"])

    def test_later_directory_import_survives_root_removal_undo_redo(self):
        self.add_roots()
        self.window._remove_selection({"entities": [], "folders": [str(self.root)]})
        self.assertEqual(self.window.entities, [])
        another = self.base / "另一组"
        another.mkdir()
        path = another / "新增.xyz"
        path.write_text("0 0 0\n1 2 3\n")
        self.window.open_paths([str(another)])
        self.wait_idle()
        imported = self.window.entities[0]
        self.window._undo()
        self.assertEqual(len(self.window.entities), 7)
        self.assertIn(imported, self.window.entities)
        self.assertIn(str(another), self.window.scene_panel.roots())
        self.window._redo()
        self.assertEqual(self.window.entities, [imported])
        self.assertIn(str(another), self.window.scene_panel.roots())

    def test_project_io_is_blocked_while_paths_are_moving(self):
        project = self.base / "不可过早保存.pls"
        with patch.object(self.window, "_source_operation", object()):
            self.assertFalse(self.window.save_project(str(project)))
            self.assertFalse(project.exists())
            self.assertFalse(self.window.open_project(str(project)))
            self.assertEqual(len(self.window.entities), 6)

    def test_internal_move_updates_existing_entity_without_duplication(self):
        self.add_roots()
        entity = self.window.entities[0]
        destination = self.root / "新目标"
        destination.mkdir()
        old_path = Path(entity.path)
        self.window._move_sources([entity.path], str(destination))
        self.wait_idle()
        self.assertEqual(len(self.window.entities), 6)
        self.assertEqual(entity.path, str(destination / old_path.name))
        self.assertFalse(old_path.exists())
        self.assertTrue(Path(entity.path).exists())

    def test_overlay_checkboxes_remain_pinned_during_normal_browsing(self):
        first, second, third = self.window.entities[:3]
        self.window.scene_panel.set_visible(first, True)
        self.window.scene_panel.set_visible(second, True)
        self.window.scene_panel.select_entity(third)
        self.assertTrue(first.visible and first.visibility_pinned)
        self.assertTrue(second.visible and second.visibility_pinned)
        self.assertTrue(third.visible)

    def test_rename_refuses_to_change_a_loading_file(self):
        old = self.files[0]
        with patch.object(self.window, "_threads", {str(old): object()}):
            self.window._rename_source(str(old), "正在加载不能改")
            self.assertIsNone(self.window._source_operation)
            self.assertTrue(old.exists())

    def test_context_temporary_show_applies_whole_batch_without_pinning(self):
        first, second, third = self.window.entities[:3]
        first.visible = second.visible = False
        self.assertTrue(hasattr(self.window, "_on_visibility_selection"))
        self.window._on_visibility_selection({"entities": [first, second], "folders": []}, "show")
        self.assertTrue(first.visible and second.visible)
        self.assertFalse(first.visibility_pinned or second.visibility_pinned)
        self.window.scene_panel.select_entity(third)
        self.assertFalse(first.visible or second.visible)

    def test_context_batch_pin_and_unpin_preserve_visibility_and_one_step_undo(self):
        first, second, third = self.window.entities[:3]
        first.visible = second.visible = False
        self.assertTrue(hasattr(self.window, "_on_visibility_selection"))
        selection = {"entities": [first, second], "folders": []}
        self.window._on_visibility_selection(selection, "pin")
        self.window.scene_panel.select_entity(third)
        self.assertTrue(first.visible and second.visible)
        self.assertTrue(first.visibility_pinned and second.visibility_pinned)
        self.window._on_visibility_selection(selection, "unpin")
        self.assertFalse(first.visibility_pinned or second.visibility_pinned)
        self.assertTrue(first.visible and second.visible)
        self.window._undo()
        self.assertTrue(first.visibility_pinned and second.visibility_pinned)

    def test_cancelled_delete_keeps_scene_and_originals(self):
        selection = {"entities": self.window.entities[:2], "folders": []}
        self.assertTrue(hasattr(self.window, "_request_remove_selection"))
        with patch.object(self.window, "_choose_delete_mode", return_value=None):
            self.window._request_remove_selection(selection)
        self.assertEqual(len(self.window.entities), 6)
        self.assertTrue(all(p.exists() for p in self.files))

    def test_scene_delete_choice_retains_disk_files(self):
        selection = {"entities": self.window.entities[:2], "folders": []}
        self.assertTrue(hasattr(self.window, "_request_remove_selection"))
        with patch.object(self.window, "_choose_delete_mode", return_value="scene"):
            self.window._request_remove_selection(selection)
        self.assertEqual(len(self.window.entities), 4)
        self.assertTrue(all(p.exists() for p in self.files))
        self.window._undo()
        self.assertEqual(len(self.window.entities), 6)

    def test_cancelled_move_does_not_change_source_or_group(self):
        self.add_roots()
        entity = self.window.entities[0]
        self.assertTrue(hasattr(self.window, "_request_move_sources"))
        with patch.object(self.window, "_choose_move_mode", return_value=None):
            self.window._request_move_sources([entity.path], str(self.root / "分组C"))
        self.assertEqual(entity.path, str(self.files[0]))
        self.assertTrue(self.files[0].exists())

    def test_group_move_changes_tree_and_project_without_moving_original(self):
        self.add_roots()
        entity = self.window.entities[0]
        destination = self.root / "新分组"
        destination.mkdir()
        self.window.scene_panel.add_directories([str(destination)])
        self.assertTrue(hasattr(self.window, "_request_move_sources"))
        with patch.object(self.window, "_choose_move_mode", return_value="scene"):
            self.window._request_move_sources([entity.path], str(destination))
        self.assertEqual(entity.path, str(self.files[0]))
        self.assertTrue(self.files[0].exists())
        self.assertEqual(self.window.scene_panel.display_path(entity.path),
                         str(self.root / "新分组" / "点云0.xyz"))
        project = self.base / "分组.pls"
        self.assertTrue(self.window.save_project(str(project)))
        data = json.loads(project.read_text(encoding="utf-8"))
        self.assertEqual(data["scene_tree"]["placements"],
                         {"数据集/分组A/点云0.xyz": "数据集/新分组/点云0.xyz"})

    @unittest.skipUnless(os.environ.get("POINTLAB_TEST_SYSTEM_TRASH") == "1",
                         "Explicit opt-in required for the system Recycle Bin")
    def test_trash_choice_removes_originals_and_undo_restores_entire_batch(self):
        chosen = self.window.entities[:2]
        self.assertTrue(hasattr(self.window, "_trash_selection"))
        with patch.object(self.window, "_choose_delete_mode", return_value="disk"):
            self.window._request_remove_selection({"entities": chosen, "folders": []})
        self.wait_idle()
        self.assertFalse(self.files[0].exists() or self.files[1].exists())
        self.assertEqual(len(self.window.entities), 4)
        self.window._undo()
        self.wait_idle()
        self.assertTrue(self.files[0].exists() and self.files[1].exists())
        self.assertTrue(all(e in self.window.entities for e in chosen))

    @unittest.skipUnless(os.environ.get("POINTLAB_TEST_SYSTEM_TRASH") == "1",
                         "Explicit opt-in required for the system Recycle Bin")
    def test_trash_undo_collision_keeps_replacement_and_allows_retry(self):
        target = self.window.entities[0]
        self.assertTrue(hasattr(self.window, "_trash_selection"))
        self.window._trash_selection({"entities": [target], "folders": []})
        self.wait_idle()
        self.files[0].write_text("replacement")
        self.window._undo()
        self.wait_idle()
        self.assertEqual(self.files[0].read_text(encoding="utf-8"), "replacement")
        self.assertNotIn(target, self.window.entities)
        self.assertTrue(self.window.history.can_undo)
        self.files[0].rename(self.files[0].with_suffix(".replacement"))
        self.window._undo()
        self.wait_idle()
        self.assertIn(target, self.window.entities)
        self.assertIn("0 0 0", self.files[0].read_text(encoding="utf-8"))

    def test_save_action_reuses_current_path_and_ctrl_s_writes_changes(self):
        from PySide6.QtGui import QKeySequence, QShortcut
        project = self.base / "当前工程.pls"
        self.assertTrue(self.window.save_project(str(project)))
        keys = ["Ctrl+S"]
        with patch("ui.project_session.QFileDialog.getSaveFileName", side_effect=AssertionError("Repeated save dialog")):
            for index, key in enumerate(keys):
                self.window.entities[0].name = f"saved-{index}"
                shortcuts = [s for s in self.window.findChildren(QShortcut) if s.key() == QKeySequence(key)]
                self.assertEqual(len(shortcuts), 1)
                shortcuts[0].activated.emit()
                self.assertEqual(json.loads(project.read_text(encoding="utf-8"))["files"][0]["name"], f"saved-{index}")
                self.assertFalse(self.window._has_unsaved_project())

    def test_new_project_cancel_preserves_scene_and_discard_keeps_originals(self):
        from PySide6.QtWidgets import QMessageBox
        self.window._mark_project_saved(str(self.base / "current.pls"))
        self.window.entities[0].color_mode = "intensity"
        with patch("ui.project_session.QMessageBox.question", return_value=QMessageBox.Cancel):
            self.assertFalse(self.window._new_project())
        self.assertEqual(len(self.window.entities), 6)
        with patch("ui.project_session.QMessageBox.question", return_value=QMessageBox.Discard):
            self.assertTrue(self.window._new_project())
        self.assertEqual(self.window.entities, [])
        self.assertEqual(self.window.scene_panel.roots(), [])
        self.assertTrue(all(p.exists() for p in self.files))
        self.assertFalse(self.window._has_unsaved_project())

    def test_dropped_workspace_uses_the_validated_open_flow(self):
        path = str(self.base / "workspace.pls")
        with patch.object(self.window, "_open_project_path", return_value=True) as opened:
            self.window.open_paths([path])
            opened.assert_called_once_with(path)
        with patch.object(self.window, "_open_project_path") as opened:
            self.window.open_paths([path, str(self.files[0])])
            opened.assert_not_called()
        self.assertEqual(len(self.window.entities), 6)

    def test_project_grouping_roundtrip_retains_source_and_batch_pin(self):
        self.add_roots()
        destination = self.root / "目标"
        destination.mkdir()
        self.window.scene_panel.add_directories([str(destination)])
        entity = self.window.entities[0]
        with patch.object(self.window, "_choose_move_mode", return_value="scene"):
            self.window._request_move_sources([entity.path], str(destination))
        self.window._on_visibility_selection({"entities": self.window.entities[:2]}, "pin")
        project = self.base / "重开工程.pls"
        self.assertTrue(self.window.save_project(str(project)))
        self.assertTrue(self.window._clear_project_scene())
        self.assertTrue(self.window.open_project(str(project)))
        self.wait_idle()
        entity = next(e for e in self.window.entities if e.path == str(self.files[0]))
        self.assertEqual(self.window.scene_panel.display_path(entity.path), str(destination / "点云0.xyz"))
        self.assertTrue(entity.visibility_pinned and entity.visible)
        self.assertEqual(len(self.window.entities), 6)

    def test_group_undo_after_physical_rename_uses_updated_display_name(self):
        self.add_roots()
        destination = self.root / "目标"
        destination.mkdir()
        self.window.scene_panel.add_directories([str(destination)])
        old = str(self.root / "分组A")
        with patch.object(self.window, "_choose_move_mode", return_value="scene"):
            self.window._request_move_sources([old], str(destination))
        self.wait_idle()
        self.window._rename_source(old, "新名字")
        self.wait_idle()
        self.window._undo()
        self.window._redo()
        actual = str(self.root / "新名字" / "点云0.xyz")
        self.assertEqual(self.window.scene_panel.display_path(actual), str(destination / "新名字" / "点云0.xyz"))

    def test_moving_existing_folder_does_not_reimport_removed_scene_children(self):
        self.add_roots()
        removed = self.window.entities[0]
        self.window._remove_selection({"entities": [removed], "folders": []})
        destination = self.root / "目标"
        destination.mkdir()
        self.window.scene_panel.add_directories([str(destination)])
        with patch.object(self.window, "_choose_move_mode", return_value="scene"):
            self.window._request_move_sources([str(self.root / "分组A")], str(destination))
        self.wait_idle()
        self.assertEqual(len(self.window.entities), 5)
        self.assertFalse(any(e.path == removed.path for e in self.window.entities))

    def test_invalid_placement_metadata_cannot_clear_existing_project(self):
        from PySide6.QtWidgets import QMessageBox
        project = self.base / "坏工程.pls"
        project.write_text(json.dumps({"files": [], "scene_tree": {"placements": []}}))
        original = list(self.window.entities)
        with patch("ui.project_session.QFileDialog.getOpenFileName", return_value=(str(project), "")), \
             patch("ui.project_session.QMessageBox.question", return_value=QMessageBox.Discard), \
             patch("ui.project_session.QMessageBox.warning"), \
             patch("ui.main_window.QMessageBox.warning"):
            self.assertFalse(self.window._open_project_dialog())
        self.assertEqual(self.window.entities, original)

    def test_partial_trash_keeps_failed_foreign_logical_descendant_in_scene(self):
        self.add_roots()
        external = self.base / "foreign.xyz"
        external.write_text("0 0 0\n1 2 3\n")
        self.window._register_files([str(external)])
        self.wait_idle()
        foreign = next(e for e in self.window.entities if e.path == str(external))
        folder = self.root / "分组A"
        self.window.scene_panel.regroup_paths([str(external)], str(folder))
        before_group = self.window.scene_panel.display_path(str(external))
        trashed = self.base / "模拟回收站"
        folder.rename(trashed)
        self.window._record_trashed_selection([(str(folder), str(trashed))],
            {"entities": [foreign], "folders": [str(folder)]})
        self.assertTrue(external.exists())
        self.assertIn(foreign, self.window.entities)
        self.assertEqual(self.window.scene_panel.display_path(foreign.path), before_group)
        # Restore the synthetic successful portion so this test leaves no missing sources.
        self.window._undo()
        self.wait_idle()
        self.assertTrue(folder.exists())

    def test_failed_save_preserves_committed_derived_asset_and_manifest(self):
        from core.model.entities import CloudEntity
        derived = CloudEntity("derived", None, xyz=np.ones((3, 3), dtype=np.float32))
        self.window._add_entity(derived)
        project = self.base / "可靠保存.pls"
        self.assertTrue(self.window.save_project(str(project)))
        old_manifest = project.read_bytes()
        asset = self.base / json.loads(old_manifest)["derived"][0]["asset"]
        old_asset = asset.read_bytes()
        derived.xyz = np.full((3, 3), 2, dtype=np.float32)
        with patch("ui.main_window.os.replace", side_effect=OSError("manifest publish failed")), \
             patch("ui.main_window.QMessageBox.warning"):
            self.assertFalse(self.window._save_project_dialog())
        self.assertEqual(project.read_bytes(), old_manifest)
        self.assertEqual(asset.read_bytes(), old_asset)

    def test_unchanged_derived_asset_is_reused_across_saves(self):
        from core.model.entities import CloudEntity
        self.window._add_entity(CloudEntity("derived", None, xyz=np.ones((3, 3), dtype=np.float32)))
        project = self.base / "复用资产.pls"
        self.assertTrue(self.window.save_project(str(project)))
        asset = json.loads(project.read_text(encoding="utf-8"))["derived"][0]["asset"]
        self.window.entities[0].visible = not self.window.entities[0].visible
        self.assertTrue(self.window.save_project(str(project)))
        self.assertEqual(json.loads(project.read_text(encoding="utf-8"))["derived"][0]["asset"], asset)
        self.assertEqual(len(list((self.base / "复用资产.assets").glob("*.npz"))), 1)

    def test_remove_empty_child_folder_and_undo_retains_disk_directory(self):
        self.add_roots()
        empty = self.root / "空文件夹"
        empty.mkdir()
        self.window.scene_panel.add_directories([str(empty)])
        self.window._remove_selection({"entities": [], "folders": [str(empty)]})
        self.assertNotIn(str(empty), self.window.scene_panel.tree_state()["directories"])
        self.assertTrue(empty.exists())
        self.window._undo()
        self.assertIn(str(empty), self.window.scene_panel.tree_state()["directories"])

    def test_reveal_source_opens_parent_with_safe_local_url(self):
        self.assertTrue(hasattr(self.window, "_reveal_source"))
        source = self.base / "有 空格 $name.xyz"
        source.write_text("0 0 0\n")
        with patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True) as launch:
            self.assertTrue(self.window._reveal_source(str(source)))
        self.assertEqual(Path(launch.call_args.args[0].toLocalFile()), source.parent)
        self.assertTrue(source.exists())

    def test_reveal_missing_source_does_not_launch_file_manager(self):
        self.assertTrue(hasattr(self.window, "_reveal_source"))
        with patch("PySide6.QtGui.QDesktopServices.openUrl") as launch:
            self.assertFalse(self.window._reveal_source(str(self.base / "missing.xyz")))
        launch.assert_not_called()

    def test_empty_state_open_menu_can_import_a_complete_folder(self):
        self.assertTrue(self.window._clear_project_scene())
        button = self.window.scene_panel.empty.findChild(QPushButton)
        self.assertIsNotNone(button.menu(), "The initial Open button must offer files and folders")
        with patch("ui.main_window.QFileDialog.getExistingDirectory", return_value=str(self.root)):
            next(a for a in button.menu().actions() if a.text() == "打开文件夹…").trigger()
        self.wait_idle()
        self.assertEqual({e.path for e in self.window.entities}, {str(p) for p in self.files})
        self.assertIn(str(self.root), self.window.scene_panel.roots())
        self.assertIn(str(self.root / "分组A"), self.window.scene_panel.tree_state()["directories"])

    def test_empty_state_open_menu_still_imports_selected_point_cloud_files(self):
        self.assertTrue(self.window._clear_project_scene())
        button = self.window.scene_panel.empty.findChild(QPushButton)
        self.assertIsNotNone(button.menu(), "The initial Open button must offer files and folders")
        with patch("ui.main_window.QFileDialog.getOpenFileNames", return_value=([str(self.files[1])], "")):
            next(a for a in button.menu().actions() if a.text() == "打开文件…").trigger()
        self.wait_idle()
        self.assertEqual([e.path for e in self.window.entities], [str(self.files[1])])


if __name__ == "__main__":
    unittest.main()
