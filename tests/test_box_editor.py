"""CPU-only interaction checks for the reusable source-coordinate box editor."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import math

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.object_tracking import Box3D, box_corners, box_rotation


@pytest.fixture
def editor():
    from ui.widgets.box_editor import BoxEditor
    app = QApplication.instance() or QApplication([])
    widget = BoxEditor()
    widget.resize(900, 280)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()


def load(editor, box=None, points=None):
    box = box or Box3D([0, 0, 0], [4, 2, 2])
    points = box_corners(box) if points is None else points
    editor.set_data(points, box)
    return box, points


def test_native_point_polygon_preserves_coordinates_and_empty_input():
    from ui.widgets.box_editor import _point_polygon

    points = np.array([[1.25, -4.5], [0, 0], [123456.25, -0.125]])
    polygon = _point_polygon(points[::-1])
    np.testing.assert_array_equal([[point.x(), point.y()] for point in polygon], points[::-1])
    assert _point_polygon(np.empty((0, 2))).isEmpty()


def test_set_data_is_silent_and_own_echo_keeps_undo(editor):
    box, points = load(editor)
    changes = []
    editor.box_edited.connect(changes.append)
    view = editor.views["top"]
    view.setFocus()
    QTest.keyClick(view, Qt.Key_D)
    assert len(changes) == 1
    moved = changes[-1]
    assert moved.center[0] > box.center[0]
    editor.set_data(points, moved)
    assert len(changes) == 1
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    assert editor.box == box
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert editor.box == moved


def test_wheel_magnifies_view_without_changing_box_or_history(editor):
    box, _ = load(editor)
    view = editor.views["top"]
    handles = view.handle_positions()
    before = (handles[(1, 1)] - handles[(0, 0)]).manhattanLength()
    event = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(), QPoint(0, 120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(view, event)
    handles = view.handle_positions()
    assert (handles[(1, 1)] - handles[(0, 0)]).manhattanLength() > before
    assert editor.box == box
    assert not editor._undo


@pytest.mark.parametrize("name,axis", [("top", 2), ("front", 0), ("side", 1)])
def test_keyboard_rotates_in_each_source_plane_and_shift_moves_finely(editor, name, axis):
    box, _ = load(editor)
    view = editor.views[name]
    view.setFocus()
    QTest.keyClick(view, Qt.Key_Q)
    rotation = box_rotation(editor.box)
    assert not np.allclose(rotation, np.eye(3))
    np.testing.assert_allclose(rotation[:, axis], np.eye(3)[:, axis], atol=1e-12)
    QTest.keyClick(view, Qt.Key_T)
    QTest.keyClick(view, Qt.Key_D)
    coarse = np.linalg.norm(np.subtract(editor.box.center, box.center))
    QTest.keyClick(view, Qt.Key_T)
    QTest.keyClick(view, Qt.Key_D, Qt.ShiftModifier)
    fine = np.linalg.norm(np.subtract(editor.box.center, box.center))
    assert 0 < fine < coarse


def test_dragging_positive_face_keeps_opposite_face_fixed(editor):
    box, _ = load(editor, Box3D([1, 2, 3], [4, 2, 2], 0.3, roll=.12, pitch=.18))
    view = editor.views["top"]
    start = view.handle_positions()[(1, 0)].toPoint()
    QTest.mousePress(view, Qt.LeftButton, pos=start)
    QTest.mouseMove(view, start + QPoint(25, -5))
    QTest.mouseRelease(view, Qt.LeftButton, pos=start + QPoint(25, -5))
    edited = editor.box
    assert edited != box
    old_negative = np.asarray(box.center) - box_rotation(box)[:, 0] * box.size[0] / 2
    new_negative = np.asarray(edited.center) - box_rotation(edited)[:, 0] * edited.size[0] / 2
    np.testing.assert_allclose(new_negative, old_negative, atol=1e-9)
    assert min(edited.size) > 0
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    assert editor.box == box


def test_double_click_center_fits_original_points_ignoring_nonfinite_and_outside(editor):
    box = Box3D([0, 0, 0], [4, 4, 4], .3, roll=.2, pitch=-.25)
    actual = Box3D([.2, .1, -.1], [2, 1, 1], .3, roll=.2, pitch=-.25)
    points = np.concatenate([box_corners(actual), [[np.nan, 0, 0], [100, 100, 100]]])
    load(editor, box, points)
    view = editor.views["top"]
    QTest.mouseDClick(view, Qt.LeftButton, pos=view.handle_positions()[(0, 0)].toPoint())
    np.testing.assert_allclose(editor.box.center, actual.center, atol=1e-12)
    np.testing.assert_allclose(editor.box.size, actual.size, atol=1e-12)
    assert editor.box.roll == box.roll and editor.box.pitch == box.pitch


def test_frame_or_explicit_target_switch_resets_history_and_clear_disables_events(editor):
    box, points = load(editor)
    view = editor.views["top"]
    view.setFocus()
    QTest.keyClick(view, Qt.Key_D)
    next_box = Box3D([8, 0, 0], [2, 2, 2])
    editor.set_data(points.copy(), next_box)
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    assert editor.box == next_box
    QTest.keyClick(view, Qt.Key_D)
    editor.set_data(points, next_box, reset=True)
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    assert editor.box == next_box
    editor.setEnabled(False)
    QTest.keyClick(view, Qt.Key_D)
    assert editor.box == next_box
    editor.clear()
    QTest.keyClick(view, Qt.Key_D)
    assert editor.box is None


def test_draw_sampling_does_not_drop_extreme_points_from_exact_fit(editor):
    box = Box3D([0, 0, 0], [4, 4, 4])
    points = np.zeros((100001, 3))
    points[1] = [-1.9, -1.8, -1.7]
    points[-2] = [1.6, 1.5, 1.4]
    load(editor, box, points)
    assert len(editor.display_points) <= 12000
    view = editor.views["front"]
    QTest.mouseDClick(view, Qt.LeftButton, pos=view.handle_positions()[(0, 0)].toPoint())
    np.testing.assert_allclose(editor.box.size, [3.5, 3.3, 3.1])
    np.testing.assert_allclose(points[1], [-1.9, -1.8, -1.7])


def test_annotation_geometry_agrees_with_roll_pitch_and_ignores_nonfinite_rows():
    from ui.viewport.annotation_overlay import annotation_corners, points_in_annotation_box
    box = Box3D([1, 2, 3], [4, 2, 1], .4, roll=.3, pitch=-.5)
    np.testing.assert_allclose(annotation_corners(box.to_dict()), box_corners(box))
    points = np.concatenate([box_corners(box), [[np.inf, 0, 0]]])
    with np.errstate(invalid="raise"):
        np.testing.assert_array_equal(points_in_annotation_box(points, box.to_dict()), [True] * 8 + [False])


def test_double_click_face_moves_only_selected_face(editor):
    points = np.array([[-1, -.5, -.5], [1, .5, .5]])
    box, _ = load(editor, points=points)
    view = editor.views["top"]
    QTest.mouseDClick(view, Qt.LeftButton, pos=view.handle_positions()[(1, 0)].toPoint())
    np.testing.assert_allclose(editor.box.center, [-.5, 0, 0])
    np.testing.assert_allclose(editor.box.size, [3, 2, 2])
    assert editor.box.yaw == box.yaw


def test_ctrl_drag_fits_plane_points_but_preserves_depth(editor):
    box = Box3D([0, 0, 0], [4, 4, 4])
    load(editor, box, np.array([[-1, -.5, 0], [1, .5, 0], [0, 0, 3], [20, 20, 0]]))
    view = editor.views["top"]
    first, last = view._project([[-1.2, .7, 0], [1.2, -.7, 0]])
    start, end = QPoint(*map(round, first)), QPoint(*map(round, last))
    QTest.mousePress(view, Qt.LeftButton, Qt.ControlModifier, start)
    QTest.mouseMove(view, end)
    QTest.mouseRelease(view, Qt.LeftButton, Qt.ControlModifier, end)
    np.testing.assert_allclose(editor.box.size, [2, 1, 4])
    np.testing.assert_allclose(editor.box.center, [0, 0, 0])


def test_corner_drag_is_one_undo_step_and_cannot_invert_faces(editor):
    box, _ = load(editor)
    view = editor.views["top"]
    start = view.handle_positions()[(1, 1)].toPoint()
    QTest.mousePress(view, Qt.LeftButton, pos=start)
    QTest.mouseMove(view, start + QPoint(5, -5))
    QTest.mouseMove(view, start + QPoint(10, -10))
    QTest.mouseMove(view, start + QPoint(-200, 200))
    QTest.mouseRelease(view, Qt.LeftButton, pos=start + QPoint(-200, 200))
    assert min(editor.box.size) > 0
    np.testing.assert_allclose(np.subtract(editor.box.center, np.asarray(editor.box.size) / 2), [-2, -1, -1])
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    assert editor.box == box


def test_rotation_handle_and_rotation_fit_are_undoable(editor):
    box, _ = load(editor)
    view = editor.views["top"]
    start = view._rotation_handle().toPoint()
    center = view.handle_positions()[(0, 0)].toPoint()
    end = center + QPoint(center.y() - start.y(), start.x() - center.x())
    QTest.mousePress(view, Qt.LeftButton, pos=start)
    QTest.mouseMove(view, end)
    QTest.mouseRelease(view, Qt.LeftButton, pos=end)
    assert abs(abs(editor.box.yaw) - math.pi / 2) < .1
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    assert editor.box == box
    QTest.keyClick(view, Qt.Key_R)
    np.testing.assert_allclose(editor.box.size, [2, 4, 2], atol=1e-12)
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    QTest.keyClick(view, Qt.Key_F)
    assert editor.box.yaw < 0
    np.testing.assert_allclose(editor.box.size, [2, 4, 2], atol=1e-12)
    QTest.keyClick(view, Qt.Key_G)
    assert editor.box.yaw > 0


def test_frame_switch_cancels_drag_before_delayed_motion(editor):
    load(editor)
    view = editor.views["top"]
    start = view.handle_positions()[(0, 0)].toPoint()
    QTest.mousePress(view, Qt.LeftButton, pos=start)
    new_box = Box3D([6, 0, 0], [2, 2, 2])
    editor.set_data(np.zeros((1, 3)), new_box, reset=True)
    QTest.mouseMove(view, start + QPoint(20, 0))
    QTest.mouseRelease(view, Qt.LeftButton, pos=start + QPoint(20, 0))
    assert editor.box == new_box


def test_keyboard_events_without_view_focus_do_not_edit(editor):
    box, _ = load(editor)
    editor.views["front"].setFocus()
    QApplication.processEvents()
    QTest.keyClick(editor.views["top"], Qt.Key_D)
    assert editor.box == box


@pytest.mark.parametrize("name", ["front", "side"])
def test_quarter_turn_collapsed_faces_keep_center_drag_and_fit_accessible(editor, name):
    box = Box3D([0, 0, 0], [4, 2, 2], math.pi / 2)
    load(editor, box)
    view = editor.views[name]
    center = view.handle_positions()[(0, 0)]
    assert view._hit(center) == (0, 0)
    edge = view.handle_positions()[(0, 1)]
    assert view._hit(edge) == (0, 1)
    QTest.mousePress(view, Qt.LeftButton, pos=center.toPoint())
    QTest.mouseMove(view, center.toPoint() + QPoint(15, -5))
    QTest.mouseRelease(view, Qt.LeftButton, pos=center.toPoint() + QPoint(15, -5))
    assert editor.box.center != box.center
    assert editor.box.size == box.size
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    actual = Box3D([0, 0, 0], [2, 1, 1], math.pi / 2)
    editor.set_data(box_corners(actual), box, reset=True)
    center = view.handle_positions()[(0, 0)]
    QTest.mouseDClick(view, Qt.LeftButton, pos=center.toPoint())
    np.testing.assert_allclose(editor.box.size, actual.size)


def test_drag_completion_refreshes_neighborhood_projection_and_preserves_history(editor):
    box, points = load(editor, points=np.array([[0., 0., 0.], [20., 0., 0.]]))
    view = editor.views["top"]
    center = view.handle_positions()[(0, 0)].toPoint()
    distance = round(20 * view._scale())
    QTest.mousePress(view, Qt.LeftButton, pos=center)
    QTest.mouseMove(view, center + QPoint(distance, 0))
    # Motion retains the original coordinate mapping and does not rescan points.
    np.testing.assert_array_equal(editor.display_points, [[0, 0, 0]])
    QTest.mouseRelease(view, Qt.LeftButton, pos=center + QPoint(distance, 0))
    moved = editor.box
    editor.set_data(points, moved)
    np.testing.assert_array_equal(editor.display_points, [[20, 0, 0]])
    after = view.handle_positions()[(0, 0)]
    assert 0 < after.x() < view.width() and 30 < after.y() < view.height()
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    QTest.qWait(120)
    assert editor.box == box
    np.testing.assert_array_equal(editor.display_points, [[0, 0, 0]])
    QTest.keyClick(view, Qt.Key_Y, Qt.ControlModifier)
    QTest.qWait(120)
    assert editor.box == moved
    np.testing.assert_array_equal(editor.display_points, [[20, 0, 0]])
    QTest.keyClick(view, Qt.Key_T)
    QTest.qWait(120)
    assert editor.box == box
    np.testing.assert_array_equal(editor.display_points, [[0, 0, 0]])


def test_enlarged_box_refreshes_points_and_keeps_corners_reachable(editor):
    load(editor, points=np.array([[0., 0., 0.], [15., 0., 0.]]))
    view = editor.views["top"]
    start = view.handle_positions()[(1, 1)].toPoint()
    end = start + QPoint(round(20 * view._scale()), -round(12 * view._scale()))
    QTest.mousePress(view, Qt.LeftButton, pos=start)
    QTest.mouseMove(view, end)
    QTest.mouseRelease(view, Qt.LeftButton, pos=end)
    assert [15., 0., 0.] in editor.display_points.tolist()
    for point in view.handle_positions().values():
        assert 0 < point.x() < view.width()
        assert 30 < point.y() < view.height()


def test_target_switch_reuses_finite_frame_points_and_still_resets_history(editor):
    points = np.array([[0., 0., 0.], [1., 1., 1.], [np.nan, 2., 3.]])
    load(editor, points=points)
    filtered = editor._points
    view = editor.views["top"]
    view.setFocus()
    QTest.keyClick(view, Qt.Key_D)
    next_box = Box3D((1., 1., 1.), (2., 2., 2.))
    editor.set_data(points, next_box, reset=True, reuse_points=True)
    assert editor._points is filtered
    QTest.keyClick(view, Qt.Key_Z, Qt.ControlModifier)
    assert editor.box == next_box
    replacement = points.copy()
    replacement[0] = [5., 5., 5.]
    editor.set_data(replacement, next_box)
    assert editor._points is not filtered
    np.testing.assert_array_equal(editor._points, [[5., 5., 5.], [1., 1., 1.]])


def test_explicit_scene_refresh_refilters_in_place_point_edits(editor):
    points = np.array([[0., 0., 0.], [1., 1., 1.]])
    box, _ = load(editor, points=points)
    points[0] = [np.nan, 0., 0.]
    editor.set_data(points, box, reset=True)
    np.testing.assert_array_equal(editor._points, [[1., 1., 1.]])


def test_repainting_three_views_shares_membership_until_box_or_sample_changes(editor, monkeypatch):
    from ui.widgets import box_editor
    real_membership = box_editor.points_in_box
    checked = []

    def membership(points, box, *args, **kwargs):
        checked.append(box)
        return real_membership(points, box, *args, **kwargs)

    monkeypatch.setattr(box_editor, "points_in_box", membership)
    box, _ = load(editor)
    for _ in range(2):
        for view in editor.views.values():
            assert not view.grab().isNull()
    assert len(checked) == 1
    editor._change(Box3D((.2, 0, 0), box.size))
    for view in editor.views.values():
        view.grab()
    assert len(checked) == 2
    editor._refresh_context()
    for view in editor.views.values():
        view.grab()
    assert len(checked) == 3
