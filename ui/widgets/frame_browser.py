"""当前帧与相邻帧对比，参考窗格按需创建并共享点云数组。"""

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QSizePolicy, QSplitter,
    QToolButton, QVBoxLayout, QWidget,
)

from ui.viewport import PointCloudViewport
from ui.theme import get_theme
from ui.theme.icons import make_icon


class FrameBrowser(QWidget):
    previous_requested = Signal()
    next_requested = Signal()
    comparison_changed = Signal()

    def __init__(self, primary, parent=None):
        super().__init__(parent)
        self.primary = primary
        self.secondary = None
        self._reference_source = None
        self._reference = None
        self._syncing_camera = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        bar = QHBoxLayout()
        self.previous = QToolButton()
        self.previous.setText("‹")
        self.previous.setToolTip("上一帧（← / ↑）")
        self.previous.setAccessibleName("上一帧")
        self.next = QToolButton()
        self.next.setText("›")
        self.next.setToolTip("下一帧（→ / ↓）")
        self.next.setAccessibleName("下一帧")
        self.position = QLabel("0 / 0")
        self.position.setObjectName("metric")
        self.compare = QToolButton()
        self.compare.setText("双窗格")
        self.compare.setCheckable(True)
        self.compare.setToolTip("左侧当前帧，右侧相邻帧；处理与裁剪作用于左侧")
        self.direction = QComboBox()
        self.direction.addItem("对比下一帧", 1)
        self.direction.addItem("对比上一帧", -1)
        self.linked = QCheckBox("视角联动")
        self.linked.setChecked(True)
        for widget in (self.previous, self.next, self.compare, self.linked):
            widget.setFocusPolicy(Qt.NoFocus)
        for widget in (self.previous, self.position, self.next):
            bar.addWidget(widget)
        bar.addStretch(1)
        for widget in (self.compare, self.direction, self.linked):
            bar.addWidget(widget)
        self.direction.hide()
        self.linked.hide()
        root.addLayout(bar)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.primary_pane, self.primary_title = self._pane(primary)
        self.splitter.addWidget(self.primary_pane)
        self.box_splitter = QSplitter(Qt.Vertical)
        self.box_splitter.setChildrenCollapsible(False)
        self.box_splitter.addWidget(self.splitter)
        root.addWidget(self.box_splitter, 1)
        self.previous.clicked.connect(self.previous_requested)
        self.next.clicked.connect(self.next_requested)
        self.compare.toggled.connect(self._toggle_comparison)
        self.direction.currentIndexChanged.connect(self.comparison_changed)
        self.linked.toggled.connect(lambda checked: self._sync_camera(self.primary) if checked else None)
        primary.camera_changed.connect(lambda: self._sync_camera(primary))
        primary.navigation_changed.connect(lambda _: self._sync_camera(primary))
        self.set_current(None, -1, 0)
        self.set_theme(primary.theme)

    def attach_box_panel(self, panel):
        self.box_splitter.addWidget(panel)
        self.box_splitter.setStretchFactor(0, 3)
        self.box_splitter.setStretchFactor(1, 2)
        self.box_splitter.setSizes([max(220, self.height() - 300), 260])

    def _pane(self, viewport):
        pane = QWidget()
        pane.setMinimumWidth(120)
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        label = QLabel()
        label.setObjectName("metric")
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        layout.addWidget(label)
        layout.addWidget(viewport, 1)
        return pane, label

    def _toggle_comparison(self, enabled):
        if enabled and self.secondary is None:
            self.secondary = PointCloudViewport(theme=self.primary.theme, parent=self)
            self.secondary_pane, self.secondary_title = self._pane(self.secondary)
            self.splitter.addWidget(self.secondary_pane)
            self.secondary.camera_changed.connect(lambda: self._sync_camera(self.secondary))
            self.secondary.navigation_changed.connect(lambda _: self._sync_camera(self.secondary))
            self.secondary.key_next_file.connect(self.next_requested)
            self.secondary.key_prev_file.connect(self.previous_requested)
            self.secondary.key_reset_view.connect(self.secondary.reset_view)
            self.secondary.set_grid_visible(self.primary._grid_visible)
        if self.secondary is not None:
            self.secondary_pane.setVisible(enabled)
            if not enabled:
                self.secondary.set_entities([])
                self._reference_source = self._reference = None
        self.direction.setVisible(enabled)
        self.linked.setVisible(enabled)
        self.primary_title.setVisible(enabled)
        if enabled:
            self.splitter.setSizes([1, 1])
            self._sync_camera(self.primary)
        self.comparison_changed.emit()

    def _sync_camera(self, source):
        if self._syncing_camera or not self.compare.isChecked() or not self.linked.isChecked():
            return
        target = self.secondary if source is self.primary else self.primary
        if target is None:
            return
        self._syncing_camera = True
        try:
            target.set_camera_state(source.camera_state())
        finally:
            self._syncing_camera = False

    @staticmethod
    def _caption(entity):
        if entity is None:
            return ""
        if entity.xyz is not None:
            return entity.name
        status = "加载失败" if entity.load_state == "error" else "正在加载…"
        return f"{entity.name} · {status}"

    def set_current(self, entity, index, count):
        self.position.setText(f"{index + 1 if index >= 0 else 0} / {count}")
        self.previous.setEnabled(index > 0)
        self.next.setEnabled(0 <= index < count - 1)
        text = "当前 · " + self._caption(entity) if entity is not None else "当前"
        self.primary_title.setText(text)
        self.primary_title.setToolTip(text)
        self.primary_title.setVisible(self.compare.isChecked())
        if entity is not None and entity.xyz is None:
            self.primary.empty_label.setText(self._caption(entity))
        elif not any(e.visible and e.xyz is not None for e in self.primary.entities):
            self.primary.empty_label.setText("当前没有可见点云")

    def set_reference(self, entity):
        if not self.compare.isChecked() or self.secondary is None:
            return
        changed = self._reference_source is not entity
        had_data = self._reference is not None and self._reference.xyz is not None
        if entity is None:
            self._reference_source = self._reference = None
            self.secondary.set_entities([])
            self.secondary.empty_label.setText("没有相邻帧")
            self.secondary_title.setText("参考 · 没有相邻帧")
            return
        if changed:
            self._reference = replace(entity, visible=True)
            self._reference_source = entity
        else:
            self._reference.__dict__.update(entity.__dict__)
            self._reference.visible = True
        self.secondary.set_entities([self._reference])
        text = "参考 · " + self._caption(entity)
        self.secondary_title.setText(text)
        self.secondary_title.setToolTip(text)
        if entity.xyz is None:
            self.secondary.empty_label.setText(self._caption(entity))
        elif (changed or not had_data) and not self.linked.isChecked():
            self.secondary.reset_view()

    def set_theme(self, theme):
        color = get_theme(theme)["text"]
        for button, name in ((self.previous, "chevron-left"), (self.next, "chevron-right"),
                             (self.compare, "columns")):
            button.setIcon(make_icon(name, color))
            button.setIconSize(QSize(18, 18))
            button.setMinimumSize(32, 30)
        self.previous.setText("")
        self.next.setText("")
        self.compare.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        if self.secondary is not None:
            self.secondary.set_theme(theme)

    def stop(self):
        if self.secondary is not None:
            self.secondary.stop()
