"""Lightweight orthographic box editing in source coordinates, without a GPU.

Drawing uses a bounded point sample. Fitting always uses the complete finite
input, intersected with the current box (or a Ctrl-drag rectangle and depth).
"""
from __future__ import annotations

from dataclasses import replace
import math
import struct

import numpy as np
from PySide6.QtCore import QByteArray, QDataStream, QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget

from core.object_tracking import Box3D, box_corners, box_rotation, fit_box, points_in_box
from ui.theme.tokens import THEMES


_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6),
          (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
_HANDLES = ((-1, -1), (0, -1), (1, -1), (-1, 0), (0, 0),
            (1, 0), (-1, 1), (0, 1), (1, 1))


def _point_polygon(pixels):
    """Batch points through Qt's native stream codec, avoiding N Python wrappers."""
    # QPolygonF's Qt 6.0 wire format is a uint32 count and pairs of doubles.
    data = struct.pack(">I", len(pixels)) + np.asarray(pixels, dtype=">f8").tobytes()
    stream = QDataStream(QByteArray(data))
    stream.setVersion(QDataStream.Qt_6_0)
    polygon = QPolygonF()
    stream >> polygon
    return polygon


def _rotated(box, axis, angle):
    """Apply a source-axis rotation, then recover the shared Euler convention."""
    c, s = math.cos(angle), math.sin(angle)
    if axis == 0:
        delta = np.array([[1., 0., 0.], [0., c, -s], [0., s, c]])
    elif axis == 1:
        delta = np.array([[c, 0., s], [0., 1., 0.], [-s, 0., c]])
    else:
        delta = np.array([[c, -s, 0.], [s, c, 0.], [0., 0., 1.]])
    rotation = delta @ box_rotation(box)
    pitch = math.asin(float(np.clip(-rotation[2, 0], -1., 1.)))
    if abs(math.cos(pitch)) > 1e-8:
        roll = math.atan2(rotation[2, 1], rotation[2, 2])
        yaw = math.atan2(rotation[1, 0], rotation[0, 0])
    else:
        roll = 0.
        yaw = math.atan2(-rotation[0, 1], rotation[1, 1])
    return replace(box, yaw=yaw, roll=roll, pitch=pitch)


class BoxEditor(QWidget):
    """Three source-axis projections sharing one box and per-target history.

    Call ``set_data(..., reset=True)`` when a target changes while reusing the
    same points. A new points object or a different externally supplied box also
    resets history. Synchronous echoes of ``box_edited`` preserve it. Programmatic
    updates never emit the signal. ``box`` is the current immutable Box3D.
    ``reuse_points=True`` keeps the finite-point cache on target switches when
    callers know the frame data is unchanged. Scene refreshes keep the default.
    """

    box_edited = Signal(object)
    MAX_DISPLAY_POINTS = 12000
    MAX_HISTORY = 100

    def __init__(self, parent=None):
        super().__init__(parent)
        self.box = None
        self.display_points = np.empty((0, 3))
        self._points = np.empty((0, 3))
        self._source = None
        self._display_membership = None
        self._initial = None
        self._undo = []
        self._redo = []
        self._context_timer = QTimer(self)
        self._context_timer.setSingleShot(True)
        self._context_timer.setInterval(80)
        self._context_timer.timeout.connect(self._refresh_context)
        self.theme = "dark"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.views = {}
        for name, title, axes in (("top", "俯视 · X / Y", (0, 1)),
                                  ("front", "前视 · Y / Z", (1, 2)),
                                  ("side", "侧视 · X / Z", (0, 2))):
            view = _BoxView(self, title, axes)
            self.views[name] = view
            layout.addWidget(view, 1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(160)
        self.setEnabled(False)

    def set_data(self, points, box, *, reset=False, reuse_points=False):
        if box is None:
            self.clear()
            return
        if not isinstance(box, Box3D):
            raise TypeError("box must be a Box3D")
        different_points = points is not self._source
        changed = reset or different_points or box != self.box
        if different_points or (reset and not reuse_points):
            array = np.asarray(points)
            if (array.ndim != 2 or array.shape[1] != 3
                    or not np.issubdtype(array.dtype, np.number) or np.iscomplexobj(array)):
                raise ValueError("points must be a real N by 3 array")
            self._points = np.asarray(array[np.isfinite(array).all(axis=1)], dtype=float)
            self._source = points
        self.box = box
        if changed:
            self._initial = box
            self._undo.clear()
            self._redo.clear()
            for view in self.views.values():
                view._zoom = 1.
            self._refresh_context()
        self.setEnabled(True)
        self._refresh()

    def set_theme(self, theme):
        self.theme = theme if theme in THEMES else "dark"
        self._refresh()

    def set_compact(self, compact):
        """Allow a short embedded workspace without cropping the edit planes."""
        self.setMinimumHeight(108 if compact else 160)
        for view in self.views.values():
            view.setMinimumHeight(108 if compact else 150)

    def clear(self):
        self._context_timer.stop()
        self.box = self._initial = self._source = None
        self._points = self.display_points = np.empty((0, 3))
        self._display_membership = None
        self._undo.clear()
        self._redo.clear()
        for view in self.views.values():
            view.cancel_drag()
        self.setEnabled(False)
        self._refresh()

    def _refresh(self):
        for view in self.views.values():
            view.update()

    def display_membership(self):
        """Share the same bounded point test across all three view repaints."""
        cached = self._display_membership
        if cached is None or cached[0] is not self.display_points or cached[1] != self.box:
            inside = points_in_box(self.display_points, self.box)
            self._display_membership = self.display_points, self.box, inside
        return self._display_membership[2]

    def _refresh_context(self):
        """Refresh the neighborhood after an edit, with bounded temporary storage."""
        self._context_timer.stop()
        if self.box is None:
            return
        radius = max(float(np.linalg.norm(self.box.size)) * 2, 1.)
        selected = np.empty((0, 3))
        priorities = np.empty(0)
        rng = np.random.default_rng(0)
        for start in range(0, len(self._points), 32768):
            chunk = self._points[start:start + 32768]
            delta = chunk - self.box.center
            nearby = chunk[np.einsum("ij,ij->i", delta, delta) <= radius * radius]
            selected = np.concatenate([selected, nearby])
            priorities = np.concatenate([priorities, rng.random(len(nearby))])
            if len(selected) > self.MAX_DISPLAY_POINTS:
                indices = np.argpartition(priorities, self.MAX_DISPLAY_POINTS - 1)[:self.MAX_DISPLAY_POINTS]
                selected, priorities = selected[indices], priorities[indices]
        self.display_points = selected
        for view in self.views.values():
            view.reset_projection()
        self._refresh()

    def _change(self, box, *, remember=True):
        if not self.isEnabled() or self.box is None or box == self.box:
            return
        if remember:
            self._undo.append(self.box)
            self._undo = self._undo[-self.MAX_HISTORY:]
            self._redo.clear()
        self.box = box
        # Pointer gestures retain their initial projection until release. Other
        # edits keep the box reachable immediately and debounce the cloud scan.
        if not any(view._drag for view in self.views.values()):
            for view in self.views.values():
                view.reset_projection()
            self._context_timer.start()
        self._refresh()
        self.box_edited.emit(box)

    def _history(self, redo=False):
        source, destination = (self._redo, self._undo) if redo else (self._undo, self._redo)
        if source:
            destination.append(self.box)
            self._change(source.pop(), remember=False)

    def _support(self, box=None):
        return self._points[points_in_box(self._points, box or self.box)]

    def _fit(self, points, handles=None, axes=None):
        if not len(points):
            return
        box = self.box
        if handles is None or handles == (0, 0):
            self._change(fit_box(points, box.yaw, roll=box.roll, pitch=box.pitch))
            return
        local = (points - box.center) @ box_rotation(box)
        lower, upper = -np.asarray(box.size) / 2, np.asarray(box.size) / 2
        for axis, sign in zip(axes, handles):
            if sign < 0:
                lower[axis] = min(float(local[:, axis].min()), upper[axis] - .001)
            elif sign > 0:
                upper[axis] = max(float(local[:, axis].max()), lower[axis] + .001)
        center = np.asarray(box.center) + (lower + upper) / 2 @ box_rotation(box).T
        self._change(replace(box, center=center, size=upper - lower))


class _BoxView(QWidget):
    def __init__(self, editor, title, axes):
        super().__init__(editor)
        self.editor = editor
        self.title = title
        self.axes = axes
        self.normal = ({0, 1, 2} - set(axes)).pop()
        self._origin = np.zeros(3)
        self._extent = 1.
        self._zoom = 1.
        self._drag = None
        self.setMinimumSize(140, 150)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName(title + "三维框编辑")
        self.setToolTip("拖中心移动，拖边/角缩放，拖圆柄旋转 · 滚轮缩放视图\n"
                        "WASD 移动 · Q/E 旋转 · Shift 精调\n"
                        "双击边/角/中心贴合 · Ctrl 拖动区域贴合\n"
                        "G 翻转 · R/F 旋转并贴合 · T 重置\n"
                        "Ctrl+Z 撤销 · Ctrl+Shift+Z 重做\n"
                        "PageUp/PageDown 切帧 · Ctrl+PageUp/PageDown 切换有框帧")

    def reset_projection(self):
        self.cancel_drag()
        if self.editor.box is not None:
            self._origin = np.asarray(self.editor.box.center)
            self._extent = max(float(np.linalg.norm(self.editor.box.size)) * 1.6, .1)

    def cancel_drag(self):
        self._drag = None

    def _scale(self):
        return max(1., min(self.width() - 32, self.height() - 56)) / (self._extent * self._zoom)

    def wheelEvent(self, event):
        if not self.isEnabled() or self.editor.box is None:
            event.ignore()
            return
        # Changing the projection during a pointer gesture would move the handle
        # away from the cursor. Zoom affects only this view, never the box/history.
        if self._drag is None:
            steps = event.angleDelta().y() / 120
            if not steps:
                steps = event.pixelDelta().y() / 120
            self._zoom = float(np.clip(self._zoom * 1.2 ** -np.clip(steps, -10, 10), .15, 8.))
            self.update()
        event.accept()

    def _project(self, points):
        projected = (np.asarray(points) - self._origin)[..., list(self.axes)] * self._scale()
        return projected * [1, -1] + [self.width() / 2, (self.height() + 22) / 2]

    def handle_positions(self):
        """Pixel positions of center, face and corner handles for this plane."""
        box = self.editor.box
        if box is None:
            return {}
        rotation = box_rotation(box)
        positions = {}
        for signs in _HANDLES:
            local = np.zeros(3)
            local[list(self.axes)] = np.asarray(box.size)[list(self.axes)] * np.asarray(signs) / 2
            point = self._project(local @ rotation.T + box.center)
            positions[signs] = QPointF(float(point[0]), float(point[1]))
        return positions

    def _rotation_handle(self):
        handles = self.handle_positions()
        center, end = handles[(0, 0)], handles[(0, 1)]
        vector = end - center
        length = math.hypot(vector.x(), vector.y())
        if length < 1:
            vector, length = QPointF(0, -1), 1
        direction = vector / length
        # Keep the complete handle below the title and inside a compact view.
        # Box geometry still uses the same projection; only this screen-space
        # control shortens its stem when there is little room above the face.
        distance = 24.
        limits = self.rect().adjusted(8, 36, -8, -8)
        for value, component, low, high in (
                (end.x(), direction.x(), limits.left(), limits.right()),
                (end.y(), direction.y(), limits.top(), limits.bottom())):
            if component > 1e-9:
                distance = min(distance, (high - value) / component)
            elif component < -1e-9:
                distance = min(distance, (low - value) / component)
        return end + direction * max(0., distance)

    def _hit(self, position):
        rotation = self._rotation_handle()
        if math.hypot(position.x() - rotation.x(), position.y() - rotation.y()) <= 9:
            return "rotate"
        handles = self.handle_positions()
        # Prefer the center, then faces, when an edge-on projection puts handles
        # at the same pixel. Rounding absorbs trigonometric quarter-turn noise.
        closest = min(handles, key=lambda key: (
            round((handles[key] - position).manhattanLength(), 6),
            sum(sign != 0 for sign in key)))
        if math.hypot((handles[closest] - position).x(), (handles[closest] - position).y()) <= 10:
            return closest
        return None

    def paintEvent(self, event):
        palette = THEMES[self.editor.theme]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(palette["viewport_bottom"]))
        painter.setPen(QColor(palette["text2"]))
        painter.drawText(QRectF(10, 6, self.width() - 20, 20), Qt.AlignLeft, self.title)
        border = palette["accent"] if self.hasFocus() else palette["border_strong"]
        painter.setPen(QPen(QColor(border), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        box = self.editor.box
        if box is None:
            painter.setPen(QColor(palette["text2"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "选择或新建三维框")
            return
        painter.setClipRect(QRectF(2, 30, self.width() - 4, self.height() - 32))
        points = self.editor.display_points
        if len(points):
            inside = self.editor.display_membership()
            pixels = self._project(points)
            for mask, color in ((~inside, palette["text3"]), (inside, palette["success"])):
                painter.setPen(QPen(QColor(color), 2))
                painter.drawPoints(_point_polygon(pixels[mask]))
        corners = self._project(box_corners(box))
        painter.setPen(QPen(QColor(palette["accent"]), 1.5))
        for first, second in _EDGES:
            painter.drawLine(QPointF(*corners[first]), QPointF(*corners[second]))
        handles = self.handle_positions()
        painter.setBrush(QColor(palette["panel"]))
        for signs, point in handles.items():
            if signs == (0, 0):
                painter.drawEllipse(point, 5, 5)
                painter.drawLine(point + QPointF(-8, 0), point + QPointF(8, 0))
                painter.drawLine(point + QPointF(0, -8), point + QPointF(0, 8))
            else:
                painter.drawRect(QRectF(point.x() - 3, point.y() - 3, 6, 6))
        rotation = self._rotation_handle()
        painter.drawLine(handles[(0, 1)], rotation)
        painter.drawEllipse(rotation, 5, 5)
        if self._drag and self._drag["handle"] == "fit":
            painter.setPen(QPen(QColor(palette["accent"]), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRectF(self._drag["start"], self._drag["end"]).normalized())

    def mousePressEvent(self, event):
        if not self.isEnabled() or self.editor.box is None or event.button() != Qt.LeftButton:
            return
        self.setFocus(Qt.MouseFocusReason)
        handle = "fit" if event.modifiers() & Qt.ControlModifier else self._hit(event.position())
        if handle is not None:
            self.editor._context_timer.stop()
            self._drag = {"box": self.editor.box, "start": event.position(), "end": event.position(),
                          "handle": handle, "remembered": False}
            event.accept()

    def mouseMoveEvent(self, event):
        if not self.isEnabled() or self.editor.box is None:
            self.cancel_drag()
            return
        if self._drag is None:
            handle = self._hit(event.position())
            self.setCursor(Qt.OpenHandCursor if handle == (0, 0) else
                           Qt.CrossCursor if handle is not None else Qt.ArrowCursor)
            return
        drag = self._drag
        drag["end"] = event.position()
        if drag["handle"] == "fit":
            self.update()
            return
        box = drag["box"]
        difference = event.position() - drag["start"]
        fine = .1 if event.modifiers() & Qt.ShiftModifier else 1.
        delta = np.array([difference.x(), -difference.y()]) / self._scale() * fine
        handle = drag["handle"]
        if handle == "rotate":
            center = self._project(box.center)
            first = np.array([drag["start"].x(), drag["start"].y()]) - center
            last = np.array([event.position().x(), event.position().y()]) - center
            angle = math.atan2(-last[1], last[0]) - math.atan2(-first[1], first[0])
            direction = -1 if self.normal == 1 else 1
            edited = _rotated(box, self.normal, math.remainder(angle, 2 * math.pi) * fine * direction)
        elif handle == (0, 0):
            center = np.asarray(box.center).copy()
            center[list(self.axes)] += delta
            edited = replace(box, center=center)
        else:
            rotation = box_rotation(box)
            projection = rotation[np.ix_(self.axes, self.axes)]
            # A nearly edge-on local axis cannot be resized reliably in this view.
            local_delta = np.linalg.pinv(projection, rcond=.05) @ delta
            size = np.asarray(box.size).copy()
            center_delta = np.zeros(3)
            for axis, sign, amount in zip(self.axes, handle, local_delta):
                if sign:
                    size[axis] = max(.001, box.size[axis] + sign * amount)
                    center_delta[axis] = sign * (size[axis] - box.size[axis]) / 2
            edited = replace(box, center=np.asarray(box.center) + center_delta @ rotation.T, size=size)
        if edited != self.editor.box:
            remember = not drag["remembered"]
            drag["remembered"] = True
            self.editor._change(edited, remember=remember)

    def mouseReleaseEvent(self, event):
        if not self._drag:
            return
        drag, self._drag = self._drag, None
        if self.isEnabled() and self.editor.box is not None and drag["handle"] == "fit":
            rect = QRectF(drag["start"], event.position()).normalized()
            if rect.width() >= 4 and rect.height() >= 4:
                points = self.editor._points
                pixels = self._project(points)
                local = (points - self.editor.box.center) @ box_rotation(self.editor.box)
                keep = ((pixels[:, 0] >= rect.left()) & (pixels[:, 0] <= rect.right())
                        & (pixels[:, 1] >= rect.top()) & (pixels[:, 1] <= rect.bottom())
                        & (np.abs(local[:, self.normal]) <= self.editor.box.size[self.normal] / 2 + 1e-9))
                selected = points[keep]
                if len(selected):
                    fitted = fit_box(selected, self.editor.box.yaw,
                                     roll=self.editor.box.roll, pitch=self.editor.box.pitch)
                    rotation = box_rotation(self.editor.box)
                    center_delta = (np.asarray(fitted.center) - self.editor.box.center) @ rotation
                    center_delta[self.normal] = 0
                    size = np.asarray(self.editor.box.size).copy()
                    size[list(self.axes)] = np.asarray(fitted.size)[list(self.axes)]
                    self.editor._change(replace(self.editor.box, size=size,
                                               center=np.asarray(self.editor.box.center) + center_delta @ rotation.T))
        if self.isEnabled() and self.editor.box is not None:
            self.editor._refresh_context()
        self.update()

    def mouseDoubleClickEvent(self, event):
        if not self.isEnabled() or self.editor.box is None or event.button() != Qt.LeftButton:
            return
        self.cancel_drag()
        handle = self._hit(event.position())
        if isinstance(handle, tuple):
            self.editor._fit(self.editor._support(), handle, self.axes)

    def event(self, event):
        if (event.type() == QEvent.ShortcutOverride and self.isEnabled()
                and self.hasFocus() and self.editor.box is not None
                and event.modifiers() & Qt.ControlModifier
                and event.key() in (Qt.Key_Z, Qt.Key_Y)):
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if not self.isEnabled() or not self.hasFocus() or self.editor.box is None:
            return
        key, modifiers = event.key(), event.modifiers()
        if modifiers & Qt.ControlModifier:
            if key in (Qt.Key_Z, Qt.Key_Y):
                self.cancel_drag()
                self.editor._history(key == Qt.Key_Y or bool(modifiers & Qt.ShiftModifier))
                event.accept()
                return
            return super().keyPressEvent(event)
        box = self.editor.box
        fine = .1 if modifiers & Qt.ShiftModifier else 1.
        if key in (Qt.Key_W, Qt.Key_A, Qt.Key_S, Qt.Key_D):
            center = np.asarray(box.center).copy()
            axis = self.axes[0] if key in (Qt.Key_A, Qt.Key_D) else self.axes[1]
            direction = 1 if key in (Qt.Key_D, Qt.Key_W) else -1
            center[axis] += max(min(box.size) * .02, .001) * direction * fine
            self.editor._change(replace(box, center=center))
        elif key in (Qt.Key_Q, Qt.Key_E, Qt.Key_G, Qt.Key_R, Qt.Key_F):
            angle = {Qt.Key_Q: math.radians(5) * fine, Qt.Key_E: -math.radians(5) * fine,
                     Qt.Key_G: math.pi, Qt.Key_R: math.pi / 2, Qt.Key_F: -math.pi / 2}[key]
            edited = _rotated(box, self.normal, angle)
            if key in (Qt.Key_R, Qt.Key_F):
                support = self.editor._support()
                if len(support):
                    edited = fit_box(support, edited.yaw, roll=edited.roll, pitch=edited.pitch)
            self.editor._change(edited)
        elif key == Qt.Key_T:
            self.editor._change(self.editor._initial)
        else:
            return super().keyPressEvent(event)
        event.accept()

    def changeEvent(self, event):
        if not self.isEnabled():
            self.cancel_drag()
        super().changeEvent(event)

    def focusInEvent(self, event):
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        was_dragging = self._drag is not None
        self.cancel_drag()
        if was_dragging:
            self.editor._refresh_context()
        self.update()
        super().focusOutEvent(event)
