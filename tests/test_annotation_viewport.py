"""Annotation geometry and scene integration without creating a GPU device."""
import copy
import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pygfx as gfx
from PySide6.QtWidgets import QApplication, QWidget

from core.model.entities import CloudEntity, ScalarField
from ui.viewport.annotation_overlay import (
    AnnotationOverlay, annotation_corners, points_in_annotation_box,
)
from ui.viewport.viewport import PointCloudViewport


def box(**changes):
    value = {"id": "7", "name": "目标 7", "center": [0, 0, 0],
             "size": [4, 2, 2], "yaw": np.pi / 2,
             "selected": True, "manual": True}
    value.update(changes)
    return value


class SceneViewport(QWidget):
    def __init__(self):
        super().__init__()
        self.resize(400, 300)
        self.theme = "dark"
        self.scene = gfx.Scene()
        self.camera = gfx.PerspectiveCamera(50, 4 / 3)
        self.camera.local.position = (0, -10, 6)
        self.camera.look_at((0, 0, 0))
        self.entities = []
        self._needs_draw = False
        self._running = True
        self._proj_cache = {"old": None}
        self._clip_box = None
        self._clip_planes = lambda: []
        self.refresh = Mock()
        self._sel_highlight = object()
        self._selected_point = ("existing selection", 3)
        self._annotation_overlay = AnnotationOverlay(self)


class AnnotationGeometryTests(unittest.TestCase):
    def test_rotated_corners_preserve_length_width_and_height(self):
        corners = annotation_corners(box(center=[3, 4, 5]))
        self.assertEqual(corners.shape, (8, 3))
        np.testing.assert_allclose(corners.min(axis=0), [2, 2, 4], atol=1e-6)
        np.testing.assert_allclose(corners.max(axis=0), [4, 6, 6], atol=1e-6)
        np.testing.assert_allclose(np.linalg.norm(corners[1] - corners[0]), 4)
        np.testing.assert_allclose(np.linalg.norm(corners[3] - corners[0]), 2)

    def test_point_membership_uses_rotated_local_axes_and_finite_points(self):
        xyz = np.array([[0, 1.9, 0], [1.9, 0, 0], [1, 2, 1],
                        [0, 0, 1.1], [np.nan, 0, 0]])
        np.testing.assert_array_equal(points_in_annotation_box(xyz, box()),
                                      [True, False, True, False, False])


class AnnotationOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = SceneViewport()

    def tearDown(self):
        self.view._annotation_overlay.clear()
        self.view.close()

    def test_wire_labels_and_highlight_leave_cloud_and_screen_selection_untouched(self):
        xyz = np.array([[0, 1.9, 0], [1.9, 0, 0], [0, 0, 0]], dtype=np.float32)
        colors = np.arange(9, dtype=np.float32).reshape(3, 3)
        entity = CloudEntity(name="cloud", xyz=xyz,
                             fields={"rgb": ScalarField("rgb", colors, kind="color")})
        self.view.entities = [entity]
        original = xyz.copy(), colors.copy(), self.view._sel_highlight
        supplied = box()
        expected = copy.deepcopy(supplied)
        PointCloudViewport.set_annotation_boxes(self.view, [supplied])
        overlay = self.view._annotation_overlay
        self.assertEqual(len(overlay.wires), 1)
        self.assertEqual(len(overlay.labels), 1)
        self.assertIn("7", overlay.labels[0].text())
        self.assertIn("人工", overlay.labels[0].text())
        self.assertEqual(overlay.wires[0].geometry.positions.data.shape, (24, 3))
        np.testing.assert_array_equal(overlay.highlight.geometry.positions.data, xyz[[0, 2]])
        np.testing.assert_array_equal(entity.xyz, original[0])
        np.testing.assert_array_equal(entity.fields["rgb"].data, original[1])
        self.assertIs(self.view._sel_highlight, original[2])
        self.assertEqual(self.view._selected_point, ("existing selection", 3))
        self.assertEqual(supplied, expected)
        supplied["center"][0] = 99
        self.assertEqual(overlay.boxes[0]["center"][0], 0)

    def test_manual_auto_and_selected_wire_styles_are_distinct(self):
        overlay = self.view._annotation_overlay
        overlay.set_boxes([box(selected=False), box(id="8", manual=False, selected=False),
                           box(id="9", selected=True)])
        manual, automatic, selected = overlay.wires
        self.assertNotEqual(manual.material.color, automatic.material.color)
        self.assertGreater(selected.material.thickness, manual.material.thickness)
        self.assertIn("自动建议", overlay.labels[1].text())

    def test_set_entities_clears_previous_frame_and_reapplication_works(self):
        overlay = self.view._annotation_overlay
        overlay.set_boxes([box()])
        old_wires = list(overlay.wires)
        PointCloudViewport.set_entities(self.view, [])
        self.assertEqual(overlay.boxes, [])
        self.assertEqual(overlay.labels, [])
        self.assertIsNone(overlay.highlight)
        self.assertFalse(any(wire in self.view.scene.children for wire in old_wires))
        self.assertEqual(self.view._proj_cache, {})
        self.view.refresh.assert_called_once()
        PointCloudViewport.set_annotation_boxes(self.view, [box()])
        self.assertEqual(len(overlay.wires), 1)

    def test_highlight_is_bounded_ignores_invisible_clouds_and_obeys_clip_planes(self):
        self.view.entities = [CloudEntity(name="large", xyz=np.zeros((400_001, 3), np.float32)),
                              CloudEntity(name="hidden", xyz=np.ones((5, 3), np.float32), visible=False)]
        self.view._clip_planes = lambda: [(1, 0, 0, 0)]
        overlay = self.view._annotation_overlay
        overlay.set_boxes([box(size=[10, 10, 10])])
        count = len(overlay.highlight.geometry.positions.data)
        self.assertGreater(count, 0)
        self.assertLessEqual(count, overlay.MAX_HIGHLIGHT_POINTS)
        self.assertEqual(overlay.highlight.material.clipping_planes, [(1, 0, 0, 0)])
        self.view.entities = [self.view.entities[1]]
        overlay.set_boxes([box(size=[10, 10, 10])])
        self.assertIsNone(overlay.highlight)

    def test_unselected_boxes_and_late_updates_after_stop_do_not_add_highlights(self):
        self.view.entities = [CloudEntity(name="cloud", xyz=np.zeros((3, 3), np.float32))]
        overlay = self.view._annotation_overlay
        PointCloudViewport.set_annotation_boxes(self.view, [box(selected=False)])
        self.assertEqual(len(overlay.wires), 1)
        self.assertIsNone(overlay.highlight)
        overlay.clear()
        self.view._running = False
        PointCloudViewport.set_annotation_boxes(self.view, [box()])
        self.assertEqual(overlay.boxes, [])
        self.assertEqual(overlay.labels, [])

    def test_theme_update_projection_invalid_boxes_and_repeated_clear_are_safe(self):
        overlay = self.view._annotation_overlay
        overlay.set_boxes([box(), box(id="bad", size=[-1, 2, 3]),
                           box(id="nan", yaw=float("nan"))])
        self.assertEqual(len(overlay.boxes), 1)
        overlay.update_labels()
        self.assertFalse(overlay.labels[0].isHidden())
        dark = overlay.wires[0].material.color
        self.view.theme = "light"
        overlay.update_theme()
        self.assertNotEqual(dark, overlay.wires[0].material.color)
        overlay.clear()
        overlay.clear()
        overlay.update_labels()
        overlay.update_theme()
        self.assertEqual(overlay.wires, [])

    def test_box_edits_reuse_scene_objects_and_label_changes_reuse_highlight(self):
        self.view.entities = [CloudEntity("cloud", xyz=np.array([[0., 0., 0.], [3., 0., 0.]], np.float32))]
        overlay = self.view._annotation_overlay
        overlay.set_boxes([box(yaw=0)])
        wire, label, highlight = overlay.wires[0], overlay.labels[0], overlay.highlight
        overlay.set_boxes([box(yaw=0, name="renamed", manual=False)])
        self.assertIs(overlay.wires[0], wire)
        self.assertIs(overlay.labels[0], label)
        self.assertIs(overlay.highlight, highlight)
        self.assertIn("renamed", label.text())
        self.assertIn("自动建议", label.text())
        old_positions = wire.geometry.positions.data.copy()
        overlay.set_boxes([box(yaw=0, center=[3., 0., 0.])])
        self.assertIs(overlay.wires[0], wire)
        self.assertIs(overlay.labels[0], label)
        np.testing.assert_allclose(wire.geometry.positions.data, old_positions + [3., 0., 0.])
        np.testing.assert_array_equal(overlay.highlight.geometry.positions.data, [[3., 0., 0.]])

    def test_translations_and_resizes_share_point_transform_but_rotation_refreshes_it(self):
        self.view.entities = [CloudEntity("cloud", xyz=np.array([[0., 0., 0.], [3., 0., 0.], [0., 1.5, 0.]], np.float32))]
        overlay = self.view._annotation_overlay
        overlay.set_boxes([box(yaw=0, size=[2., 2., 2.])])
        rotated = overlay._local_points
        overlay.set_boxes([box(yaw=0, center=[3., 0., 0.], size=[2., 2., 2.])])
        self.assertIs(overlay._local_points, rotated)
        np.testing.assert_array_equal(overlay.highlight.geometry.positions.data, [[3., 0., 0.]])
        overlay.set_boxes([box(yaw=0, size=[2., 4., 2.])])
        self.assertIs(overlay._local_points, rotated)
        np.testing.assert_array_equal(overlay.highlight.geometry.positions.data, [[0., 0., 0.], [0., 1.5, 0.]])
        overlay.set_boxes([box(yaw=np.pi / 2, size=[4., 2., 2.])])
        self.assertIsNot(overlay._local_points, rotated)
        np.testing.assert_array_equal(overlay.highlight.geometry.positions.data, [[0., 0., 0.], [0., 1.5, 0.]])

    def test_highlight_reloads_replaced_arrays_and_explicit_point_refresh(self):
        entity = CloudEntity("cloud", xyz=np.array([[0., 0., 0.], [3., 0., 0.]], np.float32))
        self.view.entities = [entity]
        overlay = self.view._annotation_overlay
        overlay.set_boxes([box(yaw=0, size=[2., 2., 2.])])
        entity.xyz = np.array([[.5, 0., 0.], [8., 0., 0.]], np.float32)
        overlay.set_boxes([box(yaw=0, size=[2., 2., 2.])])
        np.testing.assert_array_equal(overlay.highlight.geometry.positions.data, [[.5, 0., 0.]])
        entity.xyz[0] = [.75, 0., 0.]
        overlay.refresh_highlight()
        np.testing.assert_array_equal(overlay.highlight.geometry.positions.data, [[.75, 0., 0.]])
        entity.visible = False
        overlay.set_boxes([box(yaw=0, size=[2., 2., 2.])])
        self.assertIsNone(overlay.highlight)

    def test_cached_highlight_combines_roll_pitch_boxes_and_ignores_nonfinite_points(self):
        xyz = np.array([[10., 0., 1.5], [11.5, 0., 0.], [-10., 0., 1.5],
                        [-10., 1.5, 0.], [np.nan, 0., 0.]], np.float64)
        self.view.entities = [CloudEntity("cloud", xyz=xyz)]
        overlay = self.view._annotation_overlay
        records = [box(id="pitch", yaw=0, pitch=np.pi / 2, center=[10., 0., 0.], size=[4., 2., 2.]),
                   box(id="roll", yaw=0, roll=np.pi / 2, center=[-10., 0., 0.], size=[2., 4., 2.])]
        overlay.set_boxes(records)
        np.testing.assert_array_equal(overlay.highlight.geometry.positions.data, [[10., 0., 1.5], [-10., 0., 1.5]])
        records[1]["selected"] = False
        overlay.set_boxes(records)
        np.testing.assert_array_equal(overlay.highlight.geometry.positions.data, [[10., 0., 1.5]])


if __name__ == "__main__":
    unittest.main()
