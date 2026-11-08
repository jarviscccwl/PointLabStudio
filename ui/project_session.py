"""Project lifecycle and inexpensive dirty-state tracking for the main window.

The host provides ``save_project`` / ``open_project`` serialization,
``_project_operation_blocked`` and ``_clear_project_scene``. Scene clearing is
kept in the host so this mixin never owns worker or renderer lifetimes.
"""
from __future__ import annotations

import json
import math
import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFileDialog, QMessageBox


def _json_value(value):
    """Normalize small metadata values; point arrays never pass through here."""
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, os.PathLike):
        return os.fspath(value)
    return value


def _data_identity(array):
    if array is None:
        return None
    return (id(array), tuple(array.shape), str(array.dtype))


def _require_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"工程 {field} 数据格式不正确。")


def _require_vector(value, length, field):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"工程 {field} 数据格式不正确。")
    for item in value:
        _require_number(item, field)


class ProjectSessionMixin:
    def _init_project_session(self):
        """Initialize after the scene panel and viewport have been constructed."""
        self._project_path: str | None = None
        self._project_base_title = self.windowTitle() or "PointLab Studio"
        self._project_baseline = self._project_signature()
        self._project_title_timer = QTimer(self)
        self._project_title_timer.setInterval(1000)
        self._project_title_timer.timeout.connect(self._update_project_title)
        self._project_title_timer.start()
        self._update_project_title()

    def _project_signature(self) -> str:
        entities = []
        for entity in self.entities:
            state = {key: getattr(entity, key) for key in (
                "name", "path", "visible", "visibility_pinned", "color_mode", "colormap",
                "color_field", "color_range", "point_size",
            )}
            if not entity.path:
                state["data"] = _data_identity(entity.xyz)
                state["fields"] = {
                    name: (field.kind, _data_identity(field.data))
                    for name, field in entity.fields.items()
                }
                state["info"] = entity.info
            entities.append(state)
        meshes = [
            {"name": mesh.name, "path": mesh.path, "visible": mesh.visible,
             "color": mesh.color, "opacity": mesh.opacity, "info": mesh.info,
             "vertices": _data_identity(mesh.vertices),
             "triangles": _data_identity(mesh.triangles)}
            for mesh in self.meshes
        ]
        tree = dict(self.scene_panel.tree_state())
        for key in ("roots", "directories", "expanded"):
            if key in tree:
                tree[key] = sorted(tree[key])
        state = {"entities": entities, "meshes": meshes, "tree": tree,
                 "camera": self.viewport.camera_state(), "clip": self._clip_state()}
        browser = getattr(self, "frame_browser", None)
        if browser is not None:
            state["browser"] = {
                "compare": browser.compare.isChecked(),
                "direction": browser.direction.currentData(),
                "linked": browser.linked.isChecked(),
            }
        return json.dumps(_json_value(state), sort_keys=True, ensure_ascii=False,
                          separators=(",", ":"))

    def _has_unsaved_project(self) -> bool:
        # Source-only browsing is disposable; derived data and saved workspaces are not.
        if not self._project_path and not self.meshes and all(e.path for e in self.entities):
            return False
        return self._project_signature() != self._project_baseline

    def _update_project_title(self):
        name = self._project_path or "点云浏览"
        dirty = " *" if self._has_unsaved_project() else ""
        title = f"{self._project_base_title} — {name}{dirty}"
        if self.windowTitle() != title:
            self.setWindowTitle(title)

    def _mark_project_saved(self, path: str):
        self._project_path = os.path.abspath(os.fspath(path))
        self._project_baseline = self._project_signature()
        self._update_project_title()

    def _confirm_unsaved_project(self) -> bool:
        if not self._has_unsaved_project():
            return True
        choice = QMessageBox.question(
            self, "保存工作区", "是否保存当前工作区，以便下次继续？\n\n"
            "工作区（.pls）记录文件列表、视角和处理结果，不会覆盖源点云。\n"
            "不保存将丢弃本次工作区更改；尚未导出的处理结果也会丢失。\n"
            "如需给其他软件使用，请先取消，再选择「导出当前点云」。",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if choice == QMessageBox.Save:
            return self._save_project_dialog()
        return choice == QMessageBox.Discard

    def _explain_project(self):
        QMessageBox.information(
            self, "工作区与点云文件", "只看点云：直接打开或拖入文件，浏览完关闭即可，不必保存工作区。\n\n"
            "下次继续：保存工作区（.pls），保留文件列表、分组、视角、颜色、裁剪和处理结果。"
            "它是 PointLab 的记录文件，不是新的点云格式。\n\n"
            "给其他软件使用：选择「导出当前点云」，保存为 PLY、PCD、LAS 等格式。\n\n"
            "移动工作区时：同时带上 .pls、同名 .assets 文件夹和源点云，保持相对位置。"
        )

    def _reset_project_session(self) -> bool:
        if self._project_operation_blocked() or self._clear_project_scene() is False:
            return False
        self._project_path = None
        self._project_baseline = self._project_signature()
        self._update_project_title()
        return True

    def _new_project(self) -> bool:
        if self._project_operation_blocked() or not self._confirm_unsaved_project():
            return False
        return self._reset_project_session()

    def _close_project(self) -> bool:
        return self._new_project()

    def _save_project_to(self, path: str) -> bool:
        path = os.path.abspath(os.fspath(path))
        if not path.lower().endswith(".pls"):
            path += ".pls"
        if not self.save_project(path):
            return False
        self._mark_project_saved(path)
        return True

    def _save_project_dialog(self) -> bool:
        if self._project_operation_blocked():
            return False
        if self._project_path:
            return self._save_project_to(self._project_path)
        return self._save_project_as_dialog()

    def _save_project_as_dialog(self) -> bool:
        if self._project_operation_blocked():
            return False
        path, _ = QFileDialog.getSaveFileName(
            self, "保存工作区（用于下次继续，不是点云导出）", self._project_path or "", "PointLab 工作区 (*.pls)"
        )
        return self._save_project_to(path) if path else False

    @staticmethod
    def _validate_project_file(path: str):
        """Reject unreadable and structurally invalid files before scene clearing."""
        with open(path, "r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or not any(
            key in data for key in ("files", "derived", "meshes")
        ):
            raise ValueError("文件不是有效的 PointLab 工程。")
        for key in ("files", "derived", "meshes"):
            entries = data.get(key, [])
            if not isinstance(entries, list) or any(not isinstance(e, dict) for e in entries):
                raise ValueError(f"工程 {key} 数据格式不正确。")
            for entry in entries:
                required = "path" if key == "files" else "asset"
                if not isinstance(entry.get(required), str) or not entry[required]:
                    raise ValueError(f"工程 {key}.{required} 数据格式不正确。")
                for field in ("path", "asset", "name"):
                    if field in entry and not isinstance(entry[field], str):
                        raise ValueError(f"工程 {key}.{field} 数据格式不正确。")
                for field in ("point_size", "opacity", "loaded", "order"):
                    if field in entry:
                        _require_number(entry[field], f"{key}.{field}")
                if entry.get("color_range") is not None:
                    _require_vector(entry["color_range"], 2, f"{key}.color_range")
                if "color" in entry:
                    _require_vector(entry["color"], 3, f"{key}.color")
        for key in ("scene_tree", "camera", "clip", "browser"):
            if key in data and data[key] is not None and not isinstance(data[key], dict):
                raise ValueError(f"工程 {key} 数据格式不正确。")
        browser = data.get("browser")
        if browser is None and "browser" in data:
            raise ValueError("工程 browser 数据格式不正确。")
        browser = browser or {}
        for key in ("current", "direction"):
            if key in browser and not isinstance(browser[key], int):
                raise ValueError(f"工程 browser.{key} 数据格式不正确。")
        camera = data.get("camera") or {}
        for key, length in (("position", 3), ("rotation", 4), ("pivot", 3)):
            if key in camera:
                _require_vector(camera[key], length, f"camera.{key}")
        for key in ("fov", "yaw", "pitch"):
            if key in camera:
                _require_number(camera[key], f"camera.{key}")
        clip = data.get("clip") or {}
        for key in ("min", "max"):
            if clip.get("enabled") or clip.get(key) is not None:
                _require_vector(clip.get(key), 3, f"clip.{key}")
        tree = data.get("scene_tree") or {}
        placements = tree.get("placements", {})
        if not isinstance(placements, dict) or any(
            not isinstance(source, str) or not source or not isinstance(group, str) or not group
            for source, group in placements.items()
        ):
            raise ValueError("工程 scene_tree.placements 数据格式不正确。")
        for key in ("roots", "directories", "expanded"):
            if key in tree and (not isinstance(tree[key], list)
                                or any(not isinstance(p, str) for p in tree[key])):
                raise ValueError(f"工程 scene_tree.{key} 数据格式不正确。")

    def _open_project_dialog(self) -> bool:
        if self._project_operation_blocked():
            return False
        path, _ = QFileDialog.getOpenFileName(
            self, "打开工作区", self._project_path or "", "PointLab 工作区 (*.pls)"
        )
        if not path:
            return False
        return self._open_project_path(path)

    def _open_project_path(self, path: str) -> bool:
        if self._project_operation_blocked():
            return False
        path = os.path.abspath(path)
        try:
            self._validate_project_file(path)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "打开失败", str(exc))
            return False
        if not self._confirm_unsaved_project() or not self._reset_project_session():
            return False
        if not self.open_project(path):
            self._update_project_title()
            return False
        self._mark_project_saved(path)
        return True
