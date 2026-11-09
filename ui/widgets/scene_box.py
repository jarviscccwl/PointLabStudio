"""The shared 3D box editor for ordinary scene point selection."""
import numpy as np
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget
from core.object_tracking import Box3D, fit_box, points_in_box
from ui.widgets.box_editor import BoxEditor


class SceneBoxController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.viewport = window.viewport
        self.active = self.armed = False
        self.box = None
        self._signature = None
        self.panel = QWidget()
        self.panel.setObjectName("sceneBoxPanel")
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        row = QHBoxLayout()
        row.addWidget(QLabel("3D 框选"))
        row.addStretch(1)
        new = QPushButton("重新框选")
        extract = QPushButton("提取框内点")
        delete = QPushButton("删除框内点")
        close = QPushButton("收起")
        for widget in (new, extract, delete, close):
            row.addWidget(widget)
        layout.addLayout(row)
        self.editor = BoxEditor()
        self.editor.set_theme(window.theme)
        layout.addWidget(self.editor, 1)
        self.status = QLabel("Ctrl 拖动建立三维框 · 拖中心/边/角调整 · W/A/S/D 移动 · Q/E 旋转 · Shift 精调")
        self.status.setWordWrap(True)
        self.status.setObjectName("dim")
        layout.addWidget(self.status)
        self.editor.box_edited.connect(self.apply)
        new.clicked.connect(self.start)
        close.clicked.connect(self.close)
        extract.clicked.connect(lambda: getattr(window, "_extract_selection", lambda: None)())
        delete.clicked.connect(lambda: getattr(window, "_delete_selection", lambda: None)())
        window.frame_browser.attach_box_panel(self.panel)
        self.panel.hide()

    def _entities(self):
        return [e for e in self.window.entities if e.visible and e.xyz is not None and len(e.xyz)]

    def _identity(self):
        return tuple((id(e), id(e.xyz), len(e.xyz)) for e in self._entities())

    def start(self):
        if not self._entities():
            return False
        self.active = self.armed = True
        self._signature = self._identity()
        self.editor.setEnabled(False)
        self.viewport.set_interaction_mode("box")
        return True

    def initialize(self, selection):
        if not self.armed or self._signature != self._identity():
            return False
        parts = [entity.xyz[indices] for entity, indices in selection
                 if entity in self._entities() and len(indices)]
        if not parts:
            return False
        try:
            box = fit_box(np.concatenate(parts))
        except ValueError:
            return False
        self.armed = False
        self.viewport.set_interaction_mode(None)
        entities = self._entities()
        self._points = entities[0].xyz if len(entities) == 1 else np.concatenate([e.xyz for e in entities])
        self.editor.set_data(self._points, box, reset=True)
        self.panel.show()
        self.apply(box)
        return True

    def apply(self, box):
        if not self.active or self.armed or not isinstance(box, Box3D):
            return False
        if self._signature != self._identity():
            self.close()
            return False
        self.box = box
        selected = []
        for entity in self._entities():
            indices = np.flatnonzero(points_in_box(entity.xyz, box))
            if len(indices):
                selected.append((entity, indices))
        self.viewport.set_annotation_boxes([{"id": "selection", "name": "三维范围", **box.to_dict(),
                                             "selected": True, "manual": True}])
        self.viewport._apply_selection_highlight(selected)
        self.window._on_viewport_selection(selected)
        self.editor.set_data(self._points, box)
        count = sum(len(indices) for _, indices in selected)
        self.status.setText(f"框内 {count:,} 点 · 拖中心/边/角调整 · 滚轮缩放 · W/A/S/D 移动 · Q/E 旋转 · Shift 精调 · 双击贴合")
        return True

    def refresh_entities(self):
        if self.active and self._signature != self._identity():
            self.close()
        elif self.active and not self.armed and self.box is not None:
            # The scene can refresh in place, and set_entities clears overlays.
            self.editor.set_data(self._points, self.box, reset=True)
            self.apply(self.box)

    def close(self):
        self.active = self.armed = False
        self.box = None
        self.editor.clear()
        self.panel.hide()
        self.viewport.set_annotation_boxes([])
        self.viewport.set_interaction_mode(None)
        self.window._clear_point_selection()
