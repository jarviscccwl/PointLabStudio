"""Source-coordinate object boxes, independent of the viewport's point selection."""
from __future__ import annotations

import math

import numpy as np
import pygfx as gfx
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from core.object_tracking import Box3D, box_corners, box_rotation, points_in_box


_EDGES = np.array([
    [0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6],
    [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7],
], dtype=np.int32)


def _box_geometry(box):
    geometry = Box3D.from_dict(box)
    return np.asarray(geometry.center), np.asarray(geometry.size), geometry.yaw


def annotation_corners(box):
    """Return source-space corners in the shared Rz Ry Rx convention."""
    return box_corners(Box3D.from_dict(box))


def points_in_annotation_box(xyz, box):
    """Membership in the full oriented box, including its boundary."""
    geometry = Box3D.from_dict(box)
    tolerance = max(max(geometry.size), 1.0) * 1e-7
    return points_in_box(xyz, geometry, margin=tolerance)


class AnnotationOverlay:
    """Own annotation scene objects and non-interactive, camera-projected labels."""

    MAX_HIGHLIGHT_POINTS = 100_000
    MAX_SAMPLE_POINTS = 500_000
    CHUNK_POINTS = 50_000

    def __init__(self, viewport):
        self.viewport = viewport
        self.boxes = []
        self.wires = []
        self.labels = []
        self.highlight = None
        self._style_key = None
        self._highlight_key = None
        self._reset_sample()

    def clear(self):
        self._clear_objects()
        self._reset_sample()

    def _reset_sample(self):
        self._sample_key = None
        self._sample_arrays = ()
        self._sample_points = np.empty((0, 3))
        self._local_points = None
        self._local_angles = None
        self._local_origin = None
        self._local_axes = None
        self._highlight_key = None

    def _clear_objects(self):
        for obj in self.wires + ([self.highlight] if self.highlight is not None else []):
            self.viewport.scene.remove(obj)
        for label in self.labels:
            label.hide()
            label.deleteLater()
        self.boxes = []
        self.wires = []
        self.labels = []
        self.highlight = None
        self._style_key = None
        self._highlight_key = None
        self.viewport._needs_draw = True

    def set_boxes(self, boxes):
        """Copy valid dictionaries. Malformed observations are omitted safely."""
        valid = []
        for box in boxes:
            try:
                center, size, yaw = _box_geometry(box)
                valid.append({"id": str(box["id"]), "name": str(box.get("name", "")),
                              "center": center.tolist(), "size": size.tolist(), "yaw": yaw,
                              "roll": float(box.get("roll", 0.0)),
                              "pitch": float(box.get("pitch", 0.0)),
                              "selected": bool(box.get("selected", False)),
                              "manual": bool(box.get("manual", True))})
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        if not valid:
            self.clear()
            return
        previous = self.boxes
        reuse = [box["id"] for box in valid] == [box["id"] for box in previous]
        if not reuse:
            self._clear_objects()
        self.boxes = valid
        for index, box in enumerate(self.boxes):
            if reuse:
                line, label = self.wires[index], self.labels[index]
                if self._geometry_key(box) != self._geometry_key(previous[index]):
                    line.geometry.positions.data[:] = annotation_corners(box)[_EDGES].reshape(-1, 3)
                    line.geometry.positions.update_range()
                if box["selected"] != previous[index]["selected"]:
                    line.material.thickness = 3.5 if box["selected"] else 1.8
            else:
                positions = annotation_corners(box)[_EDGES].reshape(-1, 3).astype(np.float32)
                line = gfx.Line(gfx.Geometry(positions=positions), gfx.LineSegmentMaterial(
                    thickness=3.5 if box["selected"] else 1.8,
                    color=self._color(box), depth_test=False))
                line.render_order = 12
                self.viewport.scene.add(line)
                self.wires.append(line)
                label = QLabel(self.viewport)
                label.setTextFormat(Qt.PlainText)
                label.setAttribute(Qt.WA_TransparentForMouseEvents)
                label.hide()
                self.labels.append(label)
            state = "人工" if box["manual"] else "自动建议"
            name = f" · {box['name']}" if box["name"] else ""
            text = f"{box['id']}{name} · {state}"
            if label.text() != text:
                label.setText(text)
                label.adjustSize()
        self.refresh_highlight(reuse_points=True)
        if self._style_key != self._current_style_key():
            self.update_theme()
        self.update_labels()
        self.viewport._needs_draw = True

    @staticmethod
    def _geometry_key(box):
        return (tuple(box["center"]), tuple(box["size"]), box["yaw"], box["roll"], box["pitch"])

    def _current_style_key(self):
        return self.viewport.theme, tuple((box["manual"], box["selected"]) for box in self.boxes)

    def _color(self, box):
        if self.viewport.theme == "light":
            colors = {(True, False): "#007A60", (True, True): "#005C48",
                      (False, False): "#9A5600", (False, True): "#824500"}
        else:
            colors = {(True, False): "#40D7AE", (True, True): "#79FFD4",
                      (False, False): "#F6B45E", (False, True): "#FFCC80"}
        return colors[box["manual"], box["selected"]]

    def update_theme(self):
        self._style_key = self._current_style_key()
        light = self.viewport.theme == "light"
        background = "#F9FAFC" if light else "#151B25"
        for box, wire, label in zip(self.boxes, self.wires, self.labels):
            color = self._color(box)
            wire.material.color = color
            label.setStyleSheet(
                f"QLabel {{ color: {color}; background: {background}; "
                f"border: 1px solid {color}; border-radius: 4px; padding: 3px 5px; }}")
            label.adjustSize()
        if self.highlight is not None:
            self.highlight.material.color = "#006BC5" if light else "#61D8FF"
        self.viewport._needs_draw = True

    def refresh_highlight(self, *, reuse_points=False):
        """Reuse frame sampling for box edits; explicit cloud refresh invalidates it.

        A translation or resize only changes bounds in the cached local axes.
        Rotation changes rebuild one bounded coordinate array, never the cloud.
        """
        if not reuse_points:
            self._reset_sample()
        selected = [box for box in self.boxes if box["selected"]]
        if not selected:
            if self.highlight is not None:
                self.viewport.scene.remove(self.highlight)
                self.highlight = None
            self._highlight_key = None
            return
        entities = [e for e in self.viewport.entities
                    if e.visible and e.xyz is not None and len(e.xyz)]
        sample_key = tuple((id(e), id(e.xyz), len(e.xyz)) for e in entities)
        if sample_key != self._sample_key:
            self._reset_sample()
            self._sample_key = sample_key
            # Hold source arrays as well as their identity so replacements cannot
            # accidentally reuse a freed array's identity.
            self._sample_arrays = tuple(e.xyz for e in entities)
            total = sum(len(points) for points in self._sample_arrays)
            stride = max(1, math.ceil(total / self.MAX_SAMPLE_POINTS))
            parts, offset = [], 0
            for points in self._sample_arrays:
                sample = points[(-offset) % stride::stride]
                offset += len(points)
                parts.append(sample[np.isfinite(sample).all(axis=1)])
            if parts:
                self._sample_points = np.concatenate(parts)
        highlight_key = tuple(self._geometry_key(box) for box in selected)
        if highlight_key == self._highlight_key:
            return
        inside = np.zeros(len(self._sample_points), dtype=bool)
        for record in selected:
            box = Box3D.from_dict(record)
            angles = box.yaw, box.roll, box.pitch
            if angles != self._local_angles:
                self._local_axes = box_rotation(box)
                self._local_origin = np.asarray(box.center)
                self._local_points = (self._sample_points - self._local_origin) @ self._local_axes
                self._local_angles = angles
            center = (np.asarray(box.center) - self._local_origin) @ self._local_axes
            bounds = np.asarray(box.size) / 2 + max(max(box.size), 1.) * 1e-7 + 1e-9
            for start in range(0, len(inside), self.CHUNK_POINTS):
                local = self._local_points[start:start + self.CHUNK_POINTS]
                inside[start:start + self.CHUNK_POINTS] |= (np.abs(local - center) <= bounds).all(axis=1)
        indices = np.flatnonzero(inside)[:self.MAX_HIGHLIGHT_POINTS]
        positions = np.asarray(self._sample_points[indices], dtype=np.float32)
        self._highlight_key = highlight_key
        if self.highlight is not None and len(self.highlight.geometry.positions.data) == len(positions):
            self.highlight.geometry.positions.data[:] = positions
            self.highlight.geometry.positions.update_range()
            self.highlight.material.clipping_planes = self.viewport._clip_planes()
        else:
            if self.highlight is not None:
                self.viewport.scene.remove(self.highlight)
                self.highlight = None
            if len(positions):
                color = "#006BC5" if self.viewport.theme == "light" else "#61D8FF"
                self.highlight = gfx.Points(
                    gfx.Geometry(positions=positions),
                    gfx.PointsMaterial(size=4, size_space="screen", color=color,
                                       depth_test=False, clipping_planes=self.viewport._clip_planes()))
                self.highlight.render_order = 11
                self.viewport.scene.add(self.highlight)
        self.viewport._needs_draw = True

    def update_clip_planes(self):
        if self.highlight is not None:
            self.highlight.material.clipping_planes = self.viewport._clip_planes()

    def update_labels(self):
        if not self.labels:
            return
        view = np.asarray(self.viewport.camera.view_matrix, dtype=np.float64)
        projection = np.asarray(self.viewport.camera.projection_matrix, dtype=np.float64)
        transform = projection @ view
        width, height = self.viewport.width(), self.viewport.height()
        for box, label in zip(self.boxes, self.labels):
            anchor = annotation_corners(box)[4]
            clip = transform @ np.append(anchor, 1)
            if clip[3] <= 1e-9:
                label.hide()
                continue
            ndc = clip[:3] / clip[3]
            visible = bool(np.isfinite(ndc).all() and abs(ndc[0]) <= 1
                           and abs(ndc[1]) <= 1 and 0 <= ndc[2] <= 1)
            label.setVisible(visible)
            if visible:
                x = int((ndc[0] * 0.5 + 0.5) * width) + 6
                y = int((0.5 - ndc[1] * 0.5) * height) - label.height() - 5
                label.move(max(0, min(x, width - label.width())),
                           max(0, min(y, height - label.height())))
                label.raise_()
