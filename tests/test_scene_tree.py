"""Scene explorer regressions exercised with real Qt views and input events."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QContextMenuEvent, QDragEnterEvent, QDropEvent, QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QStyle, QStyleOptionViewItem, QTreeWidget

from core.model.entities import CloudEntity
from ui.widgets.scene_panel import ScenePanel


class SceneTreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "扫描"
        (self.root / "组2").mkdir(parents=True)
        (self.root / "组10").mkdir()
        self.entities = [self.cloud("组10/frame10.ply"), self.cloud("组2/frame10.ply"),
                         self.cloud("组2/frame2.ply"), self.cloud("frame1.ply")]
        self.panel = ScenePanel()
        self.panel.resize(360, 440)
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

    def cloud(self, relative):
        path = self.root / relative
        path.touch()
        return CloudEntity(path.name, str(path), visible=False, load_state="unloaded")

    def populate(self):
        self.panel.add_roots([str(self.root)])
        self.panel.set_entities(self.entities)
        self.panel.list.expandAll()
        self.app.processEvents()

    def test_append_to_known_folder_does_not_rescan_all_entity_roots(self):
        from unittest.mock import patch
        self.populate()
        entity = self.cloud("frame20.ply")
        with patch.object(self.panel, "_display_roots", side_effect=AssertionError("full scan")):
            self.panel.add_entity(entity)
        self.assertIsNotNone(self.item(entity))

    def test_theme_icons_and_status_column_remain_visible(self):
        self.populate()
        for theme in ("light", "dark"):
            self.panel.set_theme(theme)
            self.assertFalse(self.panel.list.isColumnHidden(1))
            self.assertFalse(self.folder(self.root).icon(0).isNull())
            self.assertFalse(self.item(self.entities[0]).icon(0).isNull())
        self.panel.resize(232, 440)
        self.app.processEvents()
        self.assertTrue(self.panel.list.isColumnHidden(1))

    @unittest.skipUnless(os.name == "nt", "Windows drive paths")
    def test_files_on_different_drives_have_separate_roots(self):
        entities = [CloudEntity("a.xyz", r"C:\data\a.xyz"),
                    CloudEntity("b.xyz", r"E:\data\b.xyz")]
        self.panel.set_entities(entities)
        self.assertEqual(set(self.panel.roots()), {r"C:\data", r"E:\data"})
        self.assertEqual(self.panel.card_count(), 2)

    def items(self):
        def walk(parent):
            for i in range(parent.childCount()):
                item = parent.child(i)
                yield item
                yield from walk(item)
        return list(walk(self.panel.list.invisibleRootItem()))

    def folder(self, path):
        return next(i for i in self.items() if i.data(0, Qt.UserRole + 1) == str(path))

    def item(self, entity):
        return next(i for i in self.items() if i.data(0, Qt.UserRole) is entity)

    def click(self, item, modifiers=Qt.NoModifier):
        rect = self.panel.list.visualItemRect(item)
        QTest.mouseClick(self.panel.list.viewport(), Qt.LeftButton, modifiers,
                         QPoint(rect.right() - 15, rect.center().y()))
        self.app.processEvents()

    def rubber(self, start, finish):
        view = self.panel.list.viewport()
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, start)
        QApplication.sendEvent(view, QMouseEvent(
            QEvent.MouseMove, QPointF(finish), QPointF(view.mapToGlobal(finish)),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, finish)
        self.app.processEvents()

    def menu(self, item):
        tree = self.panel.list
        point = tree.visualItemRect(item).center()
        QTest.mouseClick(tree.viewport(), Qt.RightButton, Qt.NoModifier, point)
        QApplication.sendEvent(tree.viewport(), QContextMenuEvent(
            QContextMenuEvent.Mouse, point, tree.viewport().mapToGlobal(point)))
        self.app.processEvents()
        menu = QApplication.activePopupWidget()
        self.assertIsInstance(menu, QMenu)
        return menu

    def test_hierarchy_groups_import_roots_and_orders_newest_first(self):
        self.assertIsInstance(self.panel.list, QTreeWidget)
        self.populate()
        root = self.panel.list.topLevelItem(0)
        self.assertEqual(root.text(0), "扫描")
        self.assertEqual([root.child(i).text(0) for i in range(root.childCount())],
                         ["frame1.ply", "组2", "组10"])
        folder = root.child(1)
        self.assertEqual([folder.child(i).text(0) for i in range(folder.childCount())],
                         ["frame2.ply", "frame10.ply"])
        self.assertEqual(self.panel.card_count(), 4)

    def test_new_layer_and_its_group_stay_on_top_after_rebuild(self):
        self.populate()
        newest = self.cloud("组10/new.ply")
        self.panel.add_entity(newest)
        for rebuild in (False, True):
            if rebuild:
                self.panel.set_entities([*self.entities, newest])
            root = self.folder(self.root)
            self.assertEqual(root.child(0).text(0), "组10")
            self.assertIs(root.child(0).child(0).data(0, Qt.UserRole), newest)

    def test_file_click_browses_and_checkboxes_preserve_multiple_visibility(self):
        self.populate()
        chosen, visible = [], []
        self.panel.entity_selected.connect(chosen.append)
        self.panel.visibility_changed.connect(visible.append)
        self.click(self.item(self.entities[2]))
        self.assertEqual(chosen, [self.entities[2]])
        for entity in self.entities[1:3]:
            item = self.item(entity)
            tree = self.panel.list
            option = QStyleOptionViewItem()
            option.initFrom(tree)
            index = tree.indexFromItem(item, 0)
            tree.itemDelegate().initStyleOption(option, index)
            option.rect = tree.visualRect(index)
            indicator = tree.style().subElementRect(
                QStyle.SE_ItemViewItemCheckIndicator, option, tree)
            QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier, indicator.center())
        self.assertEqual(visible, self.entities[1:3])
        self.assertTrue(all(e.visible for e in self.entities[1:3]))
        self.assertFalse(self.entities[0].visible)
        self.assertEqual(chosen, [self.entities[2]])

    def test_folder_single_click_only_expands_and_does_not_load_descendants(self):
        self.populate()
        chosen = []
        self.panel.entity_selected.connect(chosen.append)
        folder = self.folder(self.root / "组2")
        self.click(folder)
        self.assertFalse(folder.isExpanded())
        self.click(folder)
        self.assertTrue(folder.isExpanded())
        self.assertIsNone(self.panel.current_entity())
        self.assertEqual(chosen, [])
        self.assertTrue(all(not e.visible and e.load_state == "unloaded" for e in self.entities))

    def test_rubber_band_selects_intersecting_visible_folder_and_file_rows_only(self):
        self.populate()
        chosen, removed = [], []
        self.panel.entity_selected.connect(chosen.append)
        self.panel.remove_selection_requested.connect(removed.append)
        folder = self.folder(self.root / "组2")
        folder.setExpanded(False)
        self.app.processEvents()
        folder_rect = self.panel.list.visualItemRect(folder)
        other = self.folder(self.root / "组10")
        file_rect = self.panel.list.visualItemRect(self.item(self.entities[0]))
        self.rubber(QPoint(280, folder_rect.bottom() - 1),
                    QPoint(310, file_rect.top() + 1))
        self.assertEqual(set(self.panel.list.selectedItems()),
                         {folder, other, self.item(self.entities[0])})
        self.assertEqual(chosen, [])
        self.assertEqual(removed, [])
        self.assertFalse(any(e.visible for e in self.entities))
        QTest.keyClick(self.panel.list, Qt.Key_Delete)
        self.assertEqual(removed, [])
        self.assertEqual(self.panel.card_count(), 4)

    def test_context_delete_preserves_selection_and_deduplicates_descendants(self):
        self.populate()
        folder = self.folder(self.root / "组2")
        folder.setSelected(True)
        self.item(self.entities[1]).setSelected(True)
        self.item(self.entities[0]).setSelected(True)
        removed = []
        self.panel.remove_selection_requested.connect(removed.append)
        menu = self.menu(self.item(self.entities[1]))
        self.assertEqual(len(self.panel.list.selectedItems()), 3)
        next(a for a in menu.actions() if a.text() == "删除").trigger()
        self.assertEqual(len(removed), 1)
        self.assertEqual(set(removed[0]["entities"]), set(self.entities[:3]))
        self.assertEqual(removed[0]["folders"], [str(self.root / "组2")])
        self.assertEqual(self.panel.card_count(), 4)
        self.assertTrue(all(Path(e.path).exists() for e in self.entities))

    def test_context_visibility_captures_recursive_mixed_selection_for_all_actions(self):
        self.populate()
        selected_folder = self.folder(self.root / "组2")
        selected_folder.setExpanded(False)
        selected_folder.setSelected(True)
        clicked = self.item(self.entities[0])
        clicked.setSelected(True)
        received = []
        self.panel.visibility_selection_requested.connect(
            lambda payload, action: received.append((payload, action)))
        menu = self.menu(clicked)
        actions = {action.text(): action for action in menu.actions()}
        self.assertNotIn("聚焦视图", actions)
        self.assertNotIn("重新加载", actions)
        self.panel.list.clearSelection()
        self.item(self.entities[3]).setSelected(True)
        for label, action in [("临时显示", "show"), ("临时隐藏", "hide"),
                              ("固定叠加显示", "pin"), ("取消固定叠加", "unpin")]:
            actions[label].trigger()
            payload, actual_action = received[-1]
            self.assertEqual(actual_action, action)
            self.assertEqual(set(payload["entities"]), set(self.entities[:3]))
            self.assertEqual(payload["folders"], [str(self.root / "组2")])
        self.assertFalse(any(e.visible or e.visibility_pinned for e in self.entities))

    def test_multiple_folder_context_menu_exposes_recursive_visibility_actions(self):
        self.populate()
        self.folder(self.root / "组2").setSelected(True)
        folder = self.folder(self.root / "组10")
        folder.setSelected(True)
        received = []
        self.panel.visibility_selection_requested.connect(
            lambda payload, action: received.append((payload, action)))
        menu = self.menu(folder)
        next(a for a in menu.actions() if a.text() == "固定叠加显示").trigger()
        self.assertEqual(set(received[0][0]["entities"]), set(self.entities[:3]))
        self.assertEqual(received[0][1], "pin")
        self.assertEqual(len(self.panel.list.selectedItems()), 2)

    def test_project_regroup_changes_hierarchy_without_source_or_disk_changes(self):
        self.populate()
        source = self.entities[3].path
        target = str(self.root / "组2")
        self.panel.select_entity(self.entities[3])
        self.folder(self.root / "组10").setExpanded(False)
        self.panel.regroup_paths([source], target)
        self.assertEqual(self.panel.display_path(source), str(self.root / "组2" / "frame1.ply"))
        self.assertEqual(self.entities[3].path, source)
        self.assertTrue(Path(source).exists())
        self.assertIs(self.item(self.entities[3]).parent(), self.folder(target))
        self.assertTrue(self.item(self.entities[3]).isSelected())
        self.assertFalse(self.folder(self.root / "组10").isExpanded())

    def test_project_folder_regroup_preserves_sources_selection_and_saved_placements(self):
        self.populate()
        source = str(self.root / "组10")
        destination = str(self.root / "组2")
        logical = str(self.root / "组2" / "组10")
        self.folder(source).setExpanded(False)
        self.folder(source).setSelected(True)
        self.panel.regroup_paths([source], destination)
        self.assertEqual(self.panel.display_path(self.entities[0].path),
                         str(Path(logical) / "frame10.ply"))
        self.assertEqual(self.panel.source_path(logical), source)
        self.assertEqual(self.folder(logical).data(0, Qt.UserRole + 2), source)
        self.assertTrue(self.folder(logical).isSelected())
        self.assertFalse(self.folder(logical).isExpanded())
        self.assertFalse(any(i.data(0, Qt.UserRole + 1) == source for i in self.items()))
        state = self.panel.tree_state()
        self.assertEqual(state["placements"], {source: logical})
        self.assertIn(source, state["directories"])
        self.assertNotIn(logical, state["directories"])
        self.panel.restore_tree_state({"roots": [str(self.root)]})
        self.panel.restore_tree_state(state)
        self.assertEqual(self.panel.source_path(logical), source)
        self.assertFalse(self.folder(logical).isExpanded())
        self.assertTrue(Path(source).exists())
        self.assertFalse(Path(logical).exists())

    def test_regroup_rejects_cycles_and_folder_or_cloud_name_collisions_atomically(self):
        self.populate()
        with self.assertRaises(ValueError):
            self.panel.regroup_paths([self.entities[0].path], str(self.root / "组2"))
        with self.assertRaises(ValueError):
            self.panel.regroup_paths([str(self.root / "组2")], str(self.root / "组2"))
        clash = self.cloud("组2/组10")
        self.panel.add_entity(clash)
        before = self.panel.tree_state()
        with self.assertRaises(ValueError):
            self.panel.regroup_paths([str(self.root / "组10")], str(self.root / "组2"))
        self.assertEqual(self.panel.tree_state(), before)

    def test_regroup_payload_tracks_visible_descendants_and_physical_trash_sources(self):
        self.populate()
        source = self.entities[3].path
        target = str(self.root / "组2")
        self.panel.regroup_paths([source], target)
        self.folder(target).setSelected(True)
        payload = self.panel._selection_payload()
        self.assertEqual(set(payload["entities"]), set(self.entities[1:]))
        self.assertEqual(payload["folders"], [target])
        self.assertEqual(set(payload["source_paths"]), {target, source})
        self.panel.list.clearSelection()
        self.panel.regroup_paths([self.entities[2].path], str(self.root / "组10"))
        self.folder(target).setSelected(True)
        self.assertEqual(set(self.panel._selection_payload()["entities"]),
                         {self.entities[1], self.entities[3]})

    def test_remap_updates_group_placement_keys_and_values_without_losing_collapse(self):
        self.populate()
        source = str(self.root / "组10")
        logical = str(self.root / "组2" / "组10")
        self.panel.regroup_paths([source], str(self.root / "组2"))
        self.folder(logical).setExpanded(False)
        self.panel.select_entity(self.entities[3])
        renamed = self.root.parent / "新根"
        for entity in self.entities:
            entity.path = str(renamed / Path(entity.path).relative_to(self.root))
        self.panel.remap_paths(str(self.root), str(renamed))
        new_source = str(renamed / "组10")
        new_logical = str(renamed / "组2" / "组10")
        self.assertEqual(self.panel.tree_state()["placements"], {new_source: new_logical})
        self.assertEqual(self.panel.source_path(new_logical), new_source)
        self.assertFalse(self.folder(new_logical).isExpanded())
        self.assertIs(self.panel.current_entity(), self.entities[3])
        self.panel.clear_placements([new_source])
        self.assertEqual(self.panel.tree_state()["placements"], {})
        self.assertEqual(self.panel.display_path(self.entities[0].path), self.entities[0].path)

    def test_dragging_grouped_folder_keeps_foreign_descendants_nested(self):
        self.populate()
        folder = str(self.root / "组10")
        foreign = self.entities[3].path
        self.panel.regroup_paths([foreign], folder)
        self.panel.regroup_paths([folder, foreign], str(self.root / "组2"))
        new_folder = str(self.root / "组2" / "组10")
        self.assertEqual(self.panel.display_path(foreign),
                         str(Path(new_folder) / "frame1.ply"))
        self.assertIs(self.item(self.entities[3]).parent(), self.folder(new_folder))

    def test_physical_rename_preserves_group_parent_and_renames_logical_folder(self):
        self.populate()
        source = str(self.root / "组10")
        destination = str(self.root / "组2")
        old_logical = str(self.root / "组2" / "组10")
        self.panel.regroup_paths([source], destination)
        self.panel.regroup_paths([self.entities[3].path], old_logical)
        self.folder(old_logical).setExpanded(False)
        self.folder(old_logical).setSelected(True)
        renamed = str(self.root / "新组")
        self.entities[0].path = str(Path(renamed) / "frame10.ply")
        self.panel.remap_paths(source, renamed)
        logical = str(self.root / "组2" / "新组")
        self.assertEqual(self.panel.source_path(logical), renamed)
        self.assertEqual(self.panel.display_path(self.entities[3].path),
                         str(Path(logical) / "frame1.ply"))
        self.assertFalse(self.folder(logical).isExpanded())
        self.assertTrue(self.folder(logical).isSelected())
        self.assertFalse(any(i.data(0, Qt.UserRole + 1) == old_logical for i in self.items()))

    def test_internal_drag_highlights_folder_without_changing_selection_until_release(self):
        self.populate()
        item = self.item(self.entities[0])
        tree = self.panel.list
        view = tree.viewport()
        row = tree.visualItemRect(item)
        start = QPoint(tree._check_rect(item).right() + 8, row.center().y())
        target = self.folder(self.root / "组2")
        finish = tree.visualItemRect(target).center()
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, start)
        QApplication.sendEvent(view, QMouseEvent(
            QEvent.MouseMove, QPointF(finish), QPointF(view.mapToGlobal(finish)),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        self.assertTrue(tree._drop_band.isVisible())
        self.assertEqual(tree._drop_band.geometry().y(), tree.visualItemRect(target).y())
        self.assertEqual(tree.selectedItems(), [item])
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, finish)
        self.assertFalse(tree._drop_band.isVisible())

    def test_updates_and_appends_preserve_expansion_and_entity_selection(self):
        self.populate()
        folder = self.folder(self.root / "组10")
        folder.setExpanded(False)
        selected = self.entities[2]
        self.panel.select_entity(selected)
        added = self.cloud("组2/frame3.ply")
        self.panel.add_entity(added)
        self.panel.set_entities([*self.entities, added])
        self.assertFalse(self.folder(self.root / "组10").isExpanded())
        self.assertIs(self.panel.current_entity(), selected)
        self.assertTrue(self.item(selected).isSelected())
        self.assertEqual(self.panel.card_count(), 5)

    def test_roots_are_minimal_and_state_roundtrips_without_changing_visibility(self):
        self.populate()
        self.panel.add_roots([str(self.root / "组2"), str(self.root)])
        self.folder(self.root / "组10").setExpanded(False)
        state = self.panel.tree_state()
        self.assertEqual(state["roots"], [str(self.root)])
        self.assertIn(str(self.root / "组2"), state["expanded"])
        self.assertNotIn(str(self.root / "组10"), state["expanded"])
        self.panel.list.collapseAll()
        self.panel.restore_tree_state(state)
        self.assertTrue(self.folder(self.root / "组2").isExpanded())
        self.assertFalse(self.folder(self.root / "组10").isExpanded())
        self.assertFalse(any(e.visible for e in self.entities))

    def test_remap_keeps_expanded_and_selected_identities_after_parent_changes_paths(self):
        self.populate()
        self.panel.select_entity(self.entities[2])
        self.folder(self.root / "组10").setExpanded(False)
        new = self.root.parent / "已重命名"
        for entity in self.entities:
            entity.path = str(new / Path(entity.path).relative_to(self.root))
        self.panel.remap_paths(str(self.root), str(new))
        self.assertEqual(self.panel.roots(), [str(new)])
        self.assertTrue(self.folder(new / "组2").isExpanded())
        self.assertFalse(self.folder(new / "组10").isExpanded())
        self.assertIs(self.panel.current_entity(), self.entities[2])

    def drag_inside(self, item, target=None):
        tree = self.panel.list
        rect = tree.visualItemRect(item)
        start = QPoint(tree._check_rect(item).right() + 8, rect.center().y())
        if item.data(0, Qt.UserRole) is None:
            start = QPoint(tree.visualRect(tree.indexFromItem(item, 0)).left() + 22,
                           rect.center().y())
        finish = (tree.visualItemRect(target).center() if target else
                  QPoint(tree.viewport().width() - 5, tree.viewport().height() - 5))
        view = tree.viewport()
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, start)
        QApplication.sendEvent(view, QMouseEvent(
            QEvent.MouseMove, QPointF(finish), QPointF(view.mapToGlobal(finish)),
            Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, finish)
        self.app.processEvents()

    def test_name_drag_emits_folder_drop_for_selected_physical_paths_without_native_loop(self):
        self.populate()
        folder = self.folder(self.root / "组2")
        folder.setSelected(True)
        self.item(self.entities[1]).setSelected(True)
        item = self.item(self.entities[0])
        item.setSelected(True)
        moves, browsed = [], []
        self.panel.move_requested.connect(lambda paths, target: moves.append((paths, target)))
        self.panel.entity_selected.connect(browsed.append)
        self.drag_inside(item, self.folder(self.root))
        self.assertEqual(len(moves), 1)
        self.assertEqual(set(moves[0][0]), {str(self.root / "组2"), self.entities[0].path})
        self.assertEqual(moves[0][1], str(self.root))
        self.assertEqual(browsed, [])
        self.assertEqual(len(self.panel.list.selectedItems()), 3)
        self.assertEqual(self.panel.card_count(), 4)
        self.assertFalse(any(e.visible for e in self.entities))

    def test_internal_name_drag_to_file_and_blank_requests_append(self):
        self.populate()
        opened = []
        self.panel.files_open_requested.connect(opened.append)
        self.drag_inside(self.item(self.entities[0]), self.item(self.entities[3]))
        self.drag_inside(self.item(self.entities[0]))
        self.assertEqual(opened, [[self.entities[0].path], [self.entities[0].path]])

    def test_rename_menu_emits_path_and_new_name_without_touching_disk(self):
        self.populate()
        renamed = []
        self.panel.rename_requested.connect(lambda path, name: renamed.append((path, name)))
        for item, old, new in [(self.item(self.entities[0]), self.entities[0].path, "new10.ply"),
                               (self.folder(self.root / "组2"), str(self.root / "组2"), "新组")]:
            menu = self.menu(item)
            action = next(a for a in menu.actions() if a.text() == "重命名")
            def accept_name(value=new):
                dialog = QApplication.activeModalWidget()
                self.assertIsInstance(dialog, QInputDialog)
                dialog.setTextValue(value)
                dialog.accept()
            QTimer.singleShot(0, accept_name)
            action.trigger()
            menu.close()
            self.assertEqual(renamed[-1], (old, new))
            self.assertTrue(Path(old).exists())
            self.assertFalse((Path(old).parent / new).exists())

    def test_reveal_menu_uses_actual_source_after_project_regrouping(self):
        self.populate()
        entity = self.entities[3]
        self.panel.regroup_paths([entity.path], str(self.root / "组2"))
        requested = []
        self.assertTrue(hasattr(self.panel, "reveal_source_requested"))
        self.panel.reveal_source_requested.connect(requested.append)
        menu = self.menu(self.item(entity))
        action = next(a for a in menu.actions() if a.text() == "打开文件所在文件夹")
        self.assertTrue(action.isEnabled())
        action.trigger()
        self.assertEqual(requested, [str(self.root / "frame1.ply")])
        self.assertTrue(Path(entity.path).exists())

    def test_reveal_menu_is_disabled_for_unsaved_derived_cloud(self):
        entity = CloudEntity("处理结果", path=None)
        self.panel.set_entities([entity])
        menu = self.menu(self.item(entity))
        actions = [a for a in menu.actions() if a.text() == "打开文件所在文件夹"]
        self.assertEqual(len(actions), 1)
        self.assertFalse(actions[0].isEnabled())

    def test_standalone_legacy_entities_infer_directory_groups(self):
        self.panel.set_entities(self.entities)
        root = self.panel.list.topLevelItem(0)
        self.assertEqual(root.text(0), "扫描")
        self.assertEqual(root.childCount(), 3)
        self.assertEqual(self.panel.roots(), [str(self.root)])
        extra = self.cloud("组2/frame3.ply")
        self.panel.add_entity(extra)
        self.assertEqual(self.panel.card_count(), 5)
        self.assertEqual(self.folder(self.root / "组2").childCount(), 3)

    def test_compact_sidebar_keeps_file_names_readable_with_long_loading_status(self):
        self.panel.resize(232, 440)
        self.entities[1].load_state = "error"
        self.populate()
        item = self.item(self.entities[1])
        name_rect = self.panel.list.visualRect(self.panel.list.indexFromItem(item, 0))
        self.assertGreater(name_rect.width(), 100)
        self.assertIn("加载失败", item.toolTip(0))

    def test_app_themes_preserve_compact_names_and_checkbox_gestures(self):
        from ui.theme.style import build_stylesheet
        original = self.app.styleSheet()
        try:
            for theme in ("dark", "light"):
                with self.subTest(theme=theme):
                    self.app.setStyleSheet(build_stylesheet(theme))
                    self.panel.resize(232, 440)
                    self.test_file_click_browses_and_checkboxes_preserve_multiple_visibility()
                    self.test_compact_sidebar_keeps_file_names_readable_with_long_loading_status()
                    for entity in self.entities:
                        entity.visible = False
        finally:
            self.app.setStyleSheet(original)

    def test_incremental_legacy_import_widens_group_without_duplicate_folders(self):
        self.panel.add_entity(self.entities[0])
        self.panel.add_entity(self.entities[1])
        self.assertEqual(self.panel.list.topLevelItemCount(), 1)
        self.assertEqual(self.panel.list.topLevelItem(0).text(0), "扫描")
        self.assertEqual(self.folder(self.root / "组10").childCount(), 1)
        self.assertEqual(self.folder(self.root / "组2").childCount(), 1)

    def drop(self, paths, item=None):
        tree = self.panel.list
        point = (tree.visualItemRect(item).center() if item else
                 QPoint(tree.viewport().width() - 5, tree.viewport().height() - 5))
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
        enter = QDragEnterEvent(point, Qt.MoveAction | Qt.CopyAction, mime,
                               Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(tree.viewport(), enter)
        event = QDropEvent(QPointF(point), Qt.MoveAction | Qt.CopyAction, mime,
                           Qt.LeftButton, Qt.NoModifier)
        QApplication.sendEvent(tree.viewport(), event)
        self.app.processEvents()
        self.assertTrue(event.isAccepted())

    def test_folder_drop_requests_move_and_file_or_blank_drop_requests_append(self):
        self.populate()
        moves, imports = [], []
        self.panel.move_requested.connect(lambda p, target: moves.append((p, target)))
        self.panel.files_open_requested.connect(imports.append)
        paths = [self.entities[3].path]
        self.drop(paths, self.folder(self.root / "组2"))
        self.assertEqual(moves, [(paths, str(self.root / "组2"))])
        self.drop(paths, self.item(self.entities[0]))
        self.drop(paths)
        self.assertEqual(imports, [paths, paths])
        self.assertEqual(self.panel.card_count(), 4)
        self.assertTrue(Path(paths[0]).exists())

    def test_empty_source_directories_are_visible_drop_targets_after_last_file_removed(self):
        self.populate()
        empty = self.root / "空组" / "子组"
        empty.mkdir(parents=True)
        self.panel.add_directories([str(self.root), str(empty), str(self.root / "组10")])
        self.app.processEvents()
        self.assertEqual(self.folder(empty).childCount(), 0)
        self.panel.remove_entity(self.entities[0])
        emptied = self.folder(self.root / "组10")
        self.assertEqual(emptied.childCount(), 0)
        moves = []
        self.panel.move_requested.connect(lambda paths, target: moves.append((paths, target)))
        self.drop([self.entities[3].path], emptied)
        self.assertEqual(moves, [([self.entities[3].path], str(self.root / "组10"))])
        self.panel.set_entities(self.entities[1:])
        self.assertEqual(self.folder(empty).childCount(), 0)
        self.assertEqual(self.folder(self.root / "组10").childCount(), 0)

    def test_empty_directory_state_roundtrips_and_remaps_with_root(self):
        self.populate()
        empty = self.root / "空组" / "子组"
        self.panel.add_directories([str(empty)])
        self.folder(self.root / "空组").setExpanded(False)
        state = self.panel.tree_state()
        self.assertIn(str(empty), state["directories"])
        self.panel.restore_tree_state({"roots": [], "expanded": [], "directories": []})
        self.panel.restore_tree_state(state)
        self.assertEqual(self.folder(empty).childCount(), 0)
        self.assertFalse(self.folder(self.root / "空组").isExpanded())
        new = self.root.parent / "新根"
        for entity in self.entities:
            entity.path = str(new / Path(entity.path).relative_to(self.root))
        self.panel.remap_paths(str(self.root), str(new))
        self.assertIn(str(new / "空组" / "子组"), self.panel.tree_state()["directories"])
        self.assertNotIn(str(empty), self.panel.tree_state()["directories"])
        self.assertFalse(self.folder(new / "空组").isExpanded())

    def test_legacy_state_without_directories_still_derives_file_hierarchy(self):
        self.populate()
        self.panel.restore_tree_state({"roots": [str(self.root)], "expanded": [str(self.root)]})
        self.assertEqual(self.folder(self.root / "组10").childCount(), 1)
        self.assertFalse(self.folder(self.root / "组10").isExpanded())

    def test_scrollbar_wheel_uses_same_physical_direction_as_tree(self):
        self.populate()
        for n in range(50):
            self.panel.add_entity(self.cloud(f"frame{n + 20}.ply"))
        self.app.processEvents()
        bar = self.panel.list.verticalScrollBar()
        results = []
        for inverted in (False, True):
            bar.setValue(150)
            sign = -1 if inverted else 1
            event = QWheelEvent(QPointF(5, 100), QPointF(5, 100), QPoint(0, sign * 35),
                                QPoint(0, sign * 120), Qt.NoButton, Qt.NoModifier,
                                Qt.NoScrollPhase, inverted)
            QApplication.sendEvent(bar, event)
            results.append(bar.value())
        self.assertEqual(results[0], results[1])
        self.assertLess(results[0], 150)

    def test_wheel_physical_direction_is_independent_of_natural_scrolling(self):
        self.populate()
        for n in range(50):
            self.panel.add_entity(self.cloud(f"frame{n + 20}.ply"))
        self.app.processEvents()
        bar = self.panel.list.verticalScrollBar()
        self.assertGreater(bar.maximum(), 200)
        for pixel in (False, True):
            results = []
            for inverted in (False, True):
                bar.setValue(150)
                sign = -1 if inverted else 1
                event = QWheelEvent(QPointF(100, 100), QPointF(100, 100),
                                    QPoint(0, sign * 35) if pixel else QPoint(),
                                    QPoint(0, sign * 120), Qt.NoButton, Qt.NoModifier,
                                    Qt.NoScrollPhase, inverted)
                QApplication.sendEvent(self.panel.list.viewport(), event)
                results.append(bar.value())
                self.assertTrue(event.isAccepted())
            self.assertEqual(results[0], results[1])
            self.assertLess(results[0], 150)


if __name__ == "__main__":
    unittest.main()
