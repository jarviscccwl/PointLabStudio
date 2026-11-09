"""Ordinary point selection is defined by the edited 3D volume."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
import numpy as np
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
from core.model.entities import CloudEntity
from core.object_tracking import Box3D


class SceneBoxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from ui.widgets.scene_box import SceneBoxController
        self.window = QWidget()
        self.window.theme = "dark"
        self.window.entities = [CloudEntity("cloud", xyz=np.array([[0, 0, 0], [1, 1, 1], [0, 0, 20]], np.float32))]
        class View(QWidget):
            def set_annotation_boxes(self, boxes): self.boxes = boxes
            def set_interaction_mode(self, mode): self.mode = mode
            def _apply_selection_highlight(self, selected): self.selected = selected
        class Host(QWidget):
            def attach_box_panel(self, panel): self.layout().addWidget(panel)
        self.window.viewport = View(self.window)
        self.window.frame_browser = Host(self.window)
        QVBoxLayout(self.window.frame_browser)
        self.window._on_viewport_selection = lambda value: setattr(self.window, "selection", value)
        self.window._clear_point_selection = lambda: setattr(self.window, "selection", [])
        self.window._flash_status = lambda *args: None
        self.control = SceneBoxController(self.window)

    def tearDown(self):
        self.control.close()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_edited_depth_excludes_background_and_keeps_source_points_unchanged(self):
        cloud = self.window.entities[0]
        before = cloud.xyz.copy()
        self.control.start()
        self.control.initialize([(cloud, np.arange(3))])
        self.control.apply(Box3D((.5, .5, .5), (1.1, 1.1, 1.1)))
        np.testing.assert_array_equal(self.window.selection[0][1], [0, 1])
        np.testing.assert_array_equal(cloud.xyz, before)
        self.assertEqual(set(self.control.editor.views), {"top", "front", "side"})

    def test_changed_scene_invalidates_selection_instead_of_using_old_geometry(self):
        cloud = self.window.entities[0]
        self.control.start()
        self.control.initialize([(cloud, np.arange(3))])
        cloud.xyz = cloud.xyz + 2
        self.control.refresh_entities()
        self.assertFalse(self.control.active)
        self.assertEqual(self.window.selection, [])
        self.assertEqual(self.window.viewport.boxes, [])

    def test_reframing_keeps_panel_layout_and_disables_old_box_edits(self):
        cloud = self.window.entities[0]
        self.control.start()
        self.control.initialize([(cloud, np.arange(3))])
        previous = self.control.box
        self.control.start()
        self.assertFalse(self.control.panel.isHidden())
        self.assertFalse(self.control.editor.isEnabled())
        self.assertFalse(self.control.apply(Box3D((99, 0, 0), (1, 1, 1))))
        self.assertEqual(self.control.box, previous)
