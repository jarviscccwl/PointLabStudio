"""One compact editor for the cloud display preferences of both recording panes."""
import numpy as np
from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from core.color.colormaps import COLOR_MODE_LABELS, list_colormaps
from core.model.entities import COLOR_MODES


class CloudDisplayPanel(QWidget):
    mode_changed = Signal(str)
    colormap_changed = Signal(str)
    field_changed = Signal(str)
    range_changed = Signal(object)
    size_changed = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        form = QFormLayout()
        form.setSpacing(12)
        self.mode = QComboBox()
        for number, mode in enumerate(COLOR_MODES, 1):
            self.mode.addItem(f"{number}  {COLOR_MODE_LABELS[mode]}", mode)
        self.colormap = QComboBox()
        self.colormap.addItems(list_colormaps())
        self.field = QComboBox()
        self.field_label = QLabel("标量字段")
        self.size = QDoubleSpinBox()
        self.size.setRange(1, 20)
        self.size.setSingleStep(.5)
        self.size.setSuffix(" px")
        self.size.setKeyboardTracking(False)
        self.mode_label = QLabel("着色方式")
        form.addRow(self.mode_label, self.mode)
        form.addRow("色彩映射", self.colormap)
        form.addRow(self.field_label, self.field)
        form.addRow("点大小", self.size)
        layout.addLayout(form)
        self.custom_range = QCheckBox("固定颜色范围")
        self.custom_range.setToolTip("关闭时根据当前点云的 2–98 百分位范围着色。")
        layout.addWidget(self.custom_range)
        self.range_row = QWidget()
        row = QHBoxLayout(self.range_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.minimum, self.maximum = QDoubleSpinBox(), QDoubleSpinBox()
        for caption, spin in (("最小值", self.minimum), ("最大值", self.maximum)):
            spin.setRange(-1e12, 1e12)
            spin.setDecimals(3)
            spin.setKeyboardTracking(False)
            row.addWidget(QLabel(caption))
            row.addWidget(spin)
        self.maximum.setValue(1)
        self.apply_range = QPushButton("应用")
        row.addWidget(self.apply_range)
        layout.addWidget(self.range_row)
        self.hint = QLabel()
        self.hint.setObjectName("dim")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        layout.addStretch(1)
        self.mode.activated.connect(lambda: self.mode_changed.emit(self.mode.currentData()))
        self.colormap.activated.connect(lambda: self.colormap_changed.emit(self.colormap.currentText()))
        self.field.activated.connect(lambda: self.field_changed.emit(self.field.currentText()))
        self.size.valueChanged.connect(self.size_changed)
        self.custom_range.toggled.connect(self._toggle_range)
        self.apply_range.clicked.connect(self._apply_range)
        self.sync(None, "height", "default", "", None, 2)

    def _toggle_range(self, checked):
        self.range_row.setVisible(checked)
        if not checked:
            self.range_changed.emit(None)
        else:
            cloud, mode, field = self._cloud, self._mode, self._field
            if cloud is not None:
                data = cloud.xyz[:, 2] if mode == "height" else cloud.field(field if mode == "field" else "intensity")
                if data is not None:
                    finite = np.asarray(data)[np.isfinite(data)]
                    if len(finite):
                        low, high = np.percentile(finite, [2, 98])
                        self.minimum.setValue(float(low))
                        self.maximum.setValue(float(high if high > low else low + 1))
            self._apply_range()

    def _apply_range(self):
        low, high = self.minimum.value(), self.maximum.value()
        if low >= high:
            self.hint.setText("最小值需要小于最大值。")
            return
        self.range_changed.emit((low, high))

    def sync(self, cloud, mode, colormap, field, color_range, size, colorized=False):
        self._cloud, self._mode, self._field = cloud, mode, field
        controls = (self.mode, self.colormap, self.field, self.custom_range,
                    self.minimum, self.maximum, self.size)
        blockers = [QSignalBlocker(widget) for widget in controls]
        try:
            self.setEnabled(cloud is not None)
            self.mode.setCurrentIndex(max(0, self.mode.findData(mode)))
            self.mode_label.setText("基础着色" if colorized else "着色方式")
            self.colormap.setCurrentText(colormap)
            fields = [name for name, value in cloud.fields.items() if value.data.ndim == 1] if cloud else []
            if fields != [self.field.itemText(i) for i in range(self.field.count())]:
                self.field.clear()
                self.field.addItems(fields)
            self.field.setCurrentIndex(max(0, self.field.findText(field)))
            for widget in (self.field, self.field_label):
                widget.setVisible(mode == "field")
            scalar = mode in ("height", "intensity", "field")
            self.colormap.setEnabled(scalar)
            self.custom_range.setEnabled(scalar)
            self.custom_range.setChecked(color_range is not None)
            self.range_row.setVisible(scalar and color_range is not None)
            # Range statistics are only calculated when explicitly requested;
            # hidden settings must not scan half a million points every frame.
            values = color_range
            if values is not None:
                self.minimum.setValue(float(values[0]))
                self.maximum.setValue(float(values[1]) if values[1] > values[0] else float(values[0]) + 1)
            self.size.setValue(size)
            message = "两侧点云共用显示设置，切帧后保持。"
            if cloud is None:
                message = "在任一窗格选择点云后可调整显示。"
            elif colorized:
                message = "当前使用图像赋色。选择上方着色方式可返回点云原有颜色。"
            elif mode == "rgb" and "rgb" not in cloud.fields:
                message = "当前点云没有 RGB 字段，暂按高度显示。可在“融合”中启用图像赋色。"
            elif mode == "intensity" and "intensity" not in cloud.fields:
                message = "当前点云没有强度字段，暂按高度显示。"
            self.hint.setText(message)
        finally:
            for blocker in blockers:
                blocker.unblock()
