"""Project lifecycle checks with real Qt widgets and isolated project files."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QWidget

from core.model.entities import CloudEntity, MeshEntity, ScalarField
from ui.widgets.scene_panel import ScenePanel


class SessionViewport(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.state = {"position": [0, -8, 4], "rotation": [0, 0, 0, 1], "fov": 50}

    def camera_state(self):
        return self.state


class ProjectSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        try:
            from ui.project_session import ProjectSessionMixin
        except ImportError:
            self.fail("Project session workflow is not implemented")

        class SessionWindow(ProjectSessionMixin, QMainWindow):
            def __init__(self):
                super().__init__()
                self.setWindowTitle("PointLab Studio")
                self.entities = []
                self.meshes = []
                self.scene_panel = ScenePanel()
                self.setCentralWidget(self.scene_panel)
                self.viewport = SessionViewport(self)
                self.clip = {"enabled": False, "min": None, "max": None}
                self.busy = False
                self.clear_allowed = True
                self.save_allowed = True
                self._init_project_session()

            def _clip_state(self):
                return self.clip

            def _project_operation_blocked(self):
                return self.busy

            def _clear_project_scene(self):
                if not self.clear_allowed:
                    return False
                self.entities.clear()
                self.meshes.clear()
                self.scene_panel.restore_tree_state({})
                self.scene_panel.set_entities([])
                self.clip = {"enabled": False, "min": None, "max": None}
                return True

            def save_project(self, path):
                if not self.save_allowed:
                    return False
                Path(path).write_text(json.dumps({"version": 2, "files": [
                    {"path": entity.path, "name": entity.name} for entity in self.entities
                ]}), encoding="utf-8")
                return True

            def open_project(self, path):
                # The application serializer appends; UI open must clear first.
                data = json.loads(Path(path).read_text(encoding="utf-8"))
                self.entities.extend(CloudEntity(entry.get("name", "source"), entry["path"])
                                     for entry in data["files"] if entry.get("path"))
                self.scene_panel.set_entities(self.entities)
                return True

        self.temp = tempfile.TemporaryDirectory(prefix="PointLab会话 ")
        self.base = Path(self.temp.name)
        self.window = SessionWindow()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.dispose_window)

    def dispose_window(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def cloud(self, name="source.xyz", derived=False):
        path = None if derived else str(self.base / name)
        entity = CloudEntity(name, path, xyz=np.zeros((3, 3), dtype=np.float32))
        self.window.entities.append(entity)
        self.window.scene_panel.set_entities(self.window.entities)
        return entity

    def saved_cloud(self):
        entity = self.cloud()
        self.window._mark_project_saved(str(self.base / "current.pls"))
        return entity

    def test_initial_empty_project_is_clean(self):
        self.assertFalse(self.window._has_unsaved_project())
        self.assertIsNone(self.window._project_path)
        self.assertNotIn("*", self.window.windowTitle())

    def test_import_and_source_metadata_edits_are_dirty(self):
        entity = self.cloud()
        self.assertFalse(self.window._has_unsaved_project())
        self.window._mark_project_saved(str(self.base / "current.pls"))
        for attr, changed in (("name", "renamed.xyz"), ("visible", False),
                              ("visibility_pinned", True), ("point_size", 5),
                              ("color_mode", "intensity"), ("colormap", "viridis"),
                              ("color_field", "intensity"), ("color_range", (1, 9)),
                              ("path", str(self.base / "moved.xyz"))):
            with self.subTest(attr=attr):
                before = getattr(entity, attr)
                setattr(entity, attr, changed)
                self.assertTrue(self.window._has_unsaved_project())
                setattr(entity, attr, before)
                self.assertFalse(self.window._has_unsaved_project())

    def test_background_source_loading_does_not_dirty_saved_project(self):
        entity = self.saved_cloud()
        entity.xyz = np.ones((200, 3), dtype=np.float32)
        entity.fields = {"intensity": ScalarField("intensity", np.ones(200))}
        entity.load_state = "ready"
        entity.loaded, entity.tier, entity.info = 1.0, "M", "Loaded 200 points"
        self.assertFalse(self.window._has_unsaved_project())

    def test_browsing_closes_without_prompt_but_results_are_protected(self):
        entity = self.cloud()
        entity.color_mode = "height"
        self.window.viewport.state["fov"] = 70
        self.window._update_project_title()
        self.assertIn("点云浏览", self.window.windowTitle())
        self.assertNotIn("*", self.window.windowTitle())
        with patch("ui.project_session.QMessageBox.question", side_effect=AssertionError("Browsing prompt")):
            self.assertTrue(self.window._confirm_unsaved_project())
        self.cloud("filtered", derived=True)
        self.assertTrue(self.window._has_unsaved_project())
        with patch("ui.project_session.QMessageBox.question", return_value=QMessageBox.Cancel):
            self.assertFalse(self.window._confirm_unsaved_project())

    def test_derived_result_data_replacement_and_removal_are_dirty(self):
        entity = self.cloud("filtered", derived=True)
        self.window._mark_project_saved(str(self.base / "derived.pls"))
        before = entity.xyz
        entity.xyz = np.ones((3, 3), dtype=np.float32)
        self.assertTrue(self.window._has_unsaved_project())
        entity.xyz = before
        self.assertFalse(self.window._has_unsaved_project())
        self.window.entities.remove(entity)
        self.assertTrue(self.window._has_unsaved_project())

    def test_mesh_display_camera_clip_and_tree_edits_are_dirty(self):
        self.saved_cloud()
        mesh = MeshEntity("mesh", np.zeros((3, 3)), np.array([[0, 1, 2]]))
        self.window.meshes.append(mesh)
        self.assertTrue(self.window._has_unsaved_project())
        self.window._mark_project_saved(str(self.base / "current.pls"))
        mesh.opacity = .5
        self.assertTrue(self.window._has_unsaved_project())
        mesh.opacity = 1
        self.window.viewport.state["fov"] = 60
        self.assertTrue(self.window._has_unsaved_project())
        self.window.viewport.state["fov"] = 50
        self.window.clip = {"enabled": True, "min": np.zeros(3), "max": np.ones(3)}
        self.assertTrue(self.window._has_unsaved_project())
        self.window.clip = {"enabled": False, "min": None, "max": None}
        self.window.scene_panel.add_roots([str(self.base)])
        self.assertTrue(self.window._has_unsaved_project())

    def test_title_shows_absolute_path_and_follows_dirty_timer(self):
        entity = self.saved_cloud()
        self.assertIn(str(self.base / "current.pls"), self.window.windowTitle())
        self.assertNotIn("*", self.window.windowTitle())
        entity.name = "changed.xyz"
        from PySide6.QtTest import QTest
        QTest.qWait(self.window._project_title_timer.interval() + 150)
        self.assertIn("*", self.window.windowTitle())
        self.window._mark_project_saved(str(self.base / "new.pls"))
        self.assertNotIn("*", self.window.windowTitle())
        self.assertIn(str(self.base / "new.pls"), self.window.windowTitle())

    def test_first_save_chooses_path_and_later_save_reuses_it(self):
        self.cloud()
        path = self.base / "工作区"
        with patch("ui.project_session.QFileDialog.getSaveFileName", return_value=(str(path), "")):
            self.assertTrue(self.window._save_project_dialog())
        saved = self.base / "工作区.pls"
        self.assertTrue(saved.is_file())
        self.assertEqual(self.window._project_path, str(saved))
        self.assertFalse(self.window._has_unsaved_project())
        self.window.entities[0].name = "edited.xyz"
        with patch("ui.project_session.QFileDialog.getSaveFileName", side_effect=AssertionError("Save should reuse path")):
            self.assertTrue(self.window._save_project_dialog())
        self.assertEqual(json.loads(saved.read_text(encoding="utf-8"))["files"][0]["name"], "edited.xyz")

    def test_save_as_chooses_new_path_and_cancel_preserves_session(self):
        self.saved_cloud()
        original = self.window._project_path
        with patch("ui.project_session.QFileDialog.getSaveFileName", return_value=("", "")):
            self.assertFalse(self.window._save_project_as_dialog())
        self.assertEqual(self.window._project_path, original)
        new = self.base / "copy.pls"
        with patch("ui.project_session.QFileDialog.getSaveFileName", return_value=(str(new), "")):
            self.assertTrue(self.window._save_project_as_dialog())
        self.assertTrue(new.is_file())
        self.assertEqual(self.window._project_path, str(new))

    def test_unsaved_cancel_prevents_reset_and_discard_allows_new(self):
        entity = self.cloud(derived=True)
        with patch("ui.project_session.QMessageBox.question", return_value=QMessageBox.Cancel):
            self.assertFalse(self.window._new_project())
        self.assertEqual(self.window.entities, [entity])
        with patch("ui.project_session.QMessageBox.question", return_value=QMessageBox.Discard):
            self.assertTrue(self.window._new_project())
        self.assertEqual(self.window.entities, [])
        self.assertIsNone(self.window._project_path)
        self.assertFalse(self.window._has_unsaved_project())

    def test_unsaved_save_must_succeed_before_reset(self):
        entity = self.cloud(derived=True)
        self.window.save_allowed = False
        path = self.base / "save.pls"
        with patch("ui.project_session.QMessageBox.question", return_value=QMessageBox.Save), patch(
            "ui.project_session.QFileDialog.getSaveFileName", return_value=(str(path), "")
        ):
            self.assertFalse(self.window._close_project())
        self.assertEqual(self.window.entities, [entity])
        self.window.save_allowed = True
        with patch("ui.project_session.QMessageBox.question", return_value=QMessageBox.Save), patch(
            "ui.project_session.QFileDialog.getSaveFileName", return_value=(str(path), "")
        ):
            self.assertTrue(self.window._close_project())
        self.assertTrue(path.is_file())
        self.assertEqual(self.window.entities, [])
        self.assertIsNone(self.window._project_path)

    def test_unsaved_save_dialog_cancel_prevents_close(self):
        entity = self.cloud(derived=True)
        with patch("ui.project_session.QMessageBox.question", return_value=QMessageBox.Save), patch(
            "ui.project_session.QFileDialog.getSaveFileName", return_value=("", "")
        ):
            self.assertFalse(self.window._close_project())
        self.assertEqual(self.window.entities, [entity])

    def test_open_replaces_existing_project_after_discard(self):
        self.cloud("old.xyz")
        path = self.base / "open.pls"
        path.write_text(json.dumps({"version": 2, "files": [
            {"name": "new.xyz", "path": str(self.base / "new.xyz")}
        ]}), encoding="utf-8")
        with patch("ui.project_session.QFileDialog.getOpenFileName", return_value=(str(path), "")), patch(
            "ui.project_session.QMessageBox.question", return_value=QMessageBox.Discard
        ):
            self.assertTrue(self.window._open_project_dialog())
        self.assertEqual([entity.name for entity in self.window.entities], ["new.xyz"])
        self.assertEqual(self.window._project_path, str(path))
        self.assertFalse(self.window._has_unsaved_project())

    def test_open_cancel_keeps_existing_project(self):
        entity = self.cloud(derived=True)
        path = self.base / "open.pls"
        path.write_text('{"version": 2, "files": []}', encoding="utf-8")
        with patch("ui.project_session.QFileDialog.getOpenFileName", return_value=(str(path), "")), patch(
            "ui.project_session.QMessageBox.question", return_value=QMessageBox.Cancel
        ):
            self.assertFalse(self.window._open_project_dialog())
        self.assertEqual(self.window.entities, [entity])

    def test_unreadable_or_malformed_project_never_clears_current_scene(self):
        entity = self.saved_cloud()
        invalid = self.base / "invalid.pls"
        bad_contents = ["not json", "[]", "{}", '{"files": "wrong"}',
                        '{"files": ["wrong"]}', '{"files": [], "browser": []}',
                        '{"files": [{"path": 42}]}', '{"files": [{}]}',
                        '{"files": [], "derived": [{}]}',
                        '{"files": [], "meshes": [{"asset": []}]}',
                        '{"files": [], "browser": {"current": {}}}',
                        '{"files": [], "scene_tree": {"roots": {}}}',
                        '{"files": [], "camera": {"position": [1, 2]}}',
                        '{"files": [], "clip": {"enabled": true, "min": null, "max": null}}',
                        '{"files": [{"path": "cloud.xyz", "point_size": "invalid"}]}']
        with patch("ui.project_session.QMessageBox.warning"):
            for content in bad_contents + [None]:
                with self.subTest(content=content):
                    self.window.entities[:] = [entity]
                    self.window.scene_panel.set_entities(self.window.entities)
                    self.window._mark_project_saved(str(self.base / "current.pls"))
                    if content is None:
                        invalid.unlink()
                    else:
                        invalid.write_text(content, encoding="utf-8")
                    with patch("ui.project_session.QFileDialog.getOpenFileName", return_value=(str(invalid), "")):
                        self.assertFalse(self.window._open_project_dialog())
                    self.assertEqual(self.window.entities, [entity])
                    self.assertEqual(self.window._project_path, str(self.base / "current.pls"))

    def test_busy_operations_and_failed_clear_preserve_current_project(self):
        entity = self.saved_cloud()
        self.window.busy = True
        for action in (self.window._new_project, self.window._close_project,
                       self.window._save_project_dialog, self.window._save_project_as_dialog,
                       self.window._open_project_dialog):
            with self.subTest(action=action.__name__):
                self.assertFalse(action())
                self.assertEqual(self.window.entities, [entity])
        self.window.busy = False
        self.window.clear_allowed = False
        self.assertFalse(self.window._new_project())
        self.assertEqual(self.window.entities, [entity])
        self.assertEqual(self.window._project_path, str(self.base / "current.pls"))


if __name__ == "__main__":
    unittest.main()
