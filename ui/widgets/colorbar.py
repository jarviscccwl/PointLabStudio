"""E20：颜色条 + 直方图 + 手动颜色范围（标量场可视化）。"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QRectF, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from core.color.colormaps import sample_colormap


def field_values(entity, name: str):
    """取实体上用于着色的标量值：'height' → Z 坐标，其余取字段。"""
    if entity is None or entity.xyz is None or not len(entity.xyz):
        return None
    if name == "height":
        return np.asarray(entity.xyz[:, 2], dtype=np.float64)
    field = entity.field(name)
    if field is None:
        return None
    data = np.asarray(field.data)
    if data.ndim != 1:
        return None
    return data.astype(np.float64, copy=False)


def robust_minmax(values):
    """2–98 百分位范围；退化数据回退到 min/max（全相等时扩一个单位）。"""
    values = np.asarray(values, dtype=np.float64)
    finite = values[np.isfinite(values)]
    if not len(finite):
        return 0.0, 1.0
    lo, hi = np.percentile(finite, [2.0, 98.0])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(finite.min()), float(finite.max())
    if hi <= lo:
        hi = lo + 1.0
    return float(lo), float(hi)


class _GradientCanvas(QWidget):
    """色带 + 半透明直方图（上方为色带，下方为分布）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(64)
        self._colors = None
        self._hist = None

    def set_data(self, colors, hist):
        self._colors = colors
        self._hist = hist
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        light = getattr(self.window(), "theme", "dark") == "light"
        painter.fillRect(rect, QColor("#F2F2F7") if light else QColor(20, 22, 26, 220))

        if self._colors is not None and len(self._colors):
            rgb = np.clip(np.asarray(self._colors) * 255.0, 0, 255).astype(np.uint8)
            img = QImage(rgb.tobytes(), len(rgb), 1, 3 * len(rgb), QImage.Format_RGB888)
            bar = QRectF(rect.left(), rect.top(), rect.width(), rect.height() * 0.42)
            painter.drawImage(bar, img)

        if self._hist is not None and len(self._hist):
            n = len(self._hist)
            peak = float(np.max(self._hist)) or 1.0
            base = rect.bottom() - 2
            h_avail = rect.height() * 0.52
            pen = QPen(QColor("#005FCC") if light else QColor(120, 170, 230, 150), 1)
            painter.setPen(pen)
            w = rect.width() / n
            for i, value in enumerate(self._hist):
                h = h_avail * float(value) / peak
                painter.drawLine(int(rect.left() + i * w), int(base),
                                 int(rect.left() + i * w), int(base - h))

        painter.setPen(QPen(QColor("#959DA9") if light else QColor(255, 255, 255, 40), 1))
        painter.drawRect(rect)
        painter.end()


class ColorbarPanel(QWidget):
    field_changed = Signal(str)
    range_changed = Signal(float, float)
    range_reset = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entity = None
        self._updating = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.title = QLabel("颜色条")
        self.title.setObjectName("sectionTitle")
        self.title.setToolTip("颜色映射与直方图基于原始点云，裁剪不会改变色标")
        root.addWidget(self.title)

        self.combo = QComboBox()
        self.combo.setObjectName("headerCombo")
        self.combo.setToolTip("选择用于着色的标量场")
        self.combo.currentIndexChanged.connect(self._on_field_changed)
        root.addWidget(self.combo)

        self.canvas = _GradientCanvas(self)
        root.addWidget(self.canvas)

        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.spin_min = QDoubleSpinBox()
        self.spin_max = QDoubleSpinBox()
        for spin in (self.spin_min, self.spin_max):
            spin.setMinimumWidth(60)
            spin.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            spin.setDecimals(3)
            spin.setRange(-1e12, 1e12)
            spin.setSingleStep(0.1)
            spin.setToolTip("手动颜色范围下界/上界")
            lay.addWidget(spin, 1)
        self.btn_auto = QPushButton("自动")
        self.btn_auto.setObjectName("headerBtn")
        self.btn_auto.setToolTip("恢复 2–98 百分位自动范围")
        lay.addWidget(self.btn_auto)
        root.addWidget(row)

        self.lbl_stats = QLabel("—")
        self.lbl_stats.setObjectName("metric")
        self.lbl_stats.setWordWrap(True)
        root.addWidget(self.lbl_stats)

        self.spin_min.editingFinished.connect(self._on_range_edited)
        self.spin_max.editingFinished.connect(self._on_range_edited)
        self.btn_auto.clicked.connect(self._on_auto_clicked)

    # ---------------- 外部接口 ----------------
    def set_entity(self, entity):
        self._entity = entity
        self._updating = True
        try:
            self.combo.clear()
            if entity is None:
                self.lbl_stats.setText("—")
                self.canvas.set_data(None, None)
                return
            names = ["height"] + [n for n in entity.fields if n != "rgb"
                                  and np.ndim(getattr(entity.fields[n], "data", None)) == 1]
            for name in names:
                self.combo.addItem("高度 (Z)" if name == "height" else name, name)
            current = entity.color_field or (names[1] if len(names) > 1 else "height")
            idx = self.combo.findData(current)
            self.combo.setCurrentIndex(max(0, idx))
            self.refresh()
        finally:
            self._updating = False

    def refresh(self):
        entity = self._entity
        if entity is None:
            return
        name = self.combo.currentData() or "height"
        values = field_values(entity, name)
        if values is None or not len(values):
            self.lbl_stats.setText("该字段没有可显示的数值")
            self.canvas.set_data(None, None)
            return

        self._updating = True
        try:
            if entity.color_range:
                lo, hi = float(entity.color_range[0]), float(entity.color_range[1])
            else:
                lo, hi = robust_minmax(values)
            self.spin_min.setValue(lo)
            self.spin_max.setValue(hi)
        finally:
            self._updating = False

        finite = values[np.isfinite(values)]
        if len(finite):
            self.lbl_stats.setText(
                f"min {finite.min():.3f} · max {finite.max():.3f} · "
                f"μ {finite.mean():.3f} · n {len(finite):,}")
            bins = min(64, max(8, int(np.sqrt(len(finite)))))
            hist, _ = np.histogram(finite, bins=bins, range=(lo, hi))
        else:
            self.lbl_stats.setText("全部为非有限值")
            hist = None
        colors = sample_colormap(entity.colormap, np.linspace(0.0, 1.0, 256))
        self.canvas.set_data(colors, hist)

    # ---------------- 事件 ----------------
    def _on_field_changed(self, _index):
        if self._updating or self._entity is None:
            return
        name = self.combo.currentData()
        if not name:
            return
        self._entity.color_field = "" if name == "height" else name
        self._entity.color_mode = "field" if name != "height" else "height"
        self.refresh()
        self.field_changed.emit(name)

    def _on_range_edited(self):
        if self._updating or self._entity is None:
            return
        lo, hi = float(self.spin_min.value()), float(self.spin_max.value())
        if hi <= lo:
            self.lbl_stats.setText("上界必须大于下界")
            return
        self._entity.color_range = (lo, hi)
        self.refresh()
        self.range_changed.emit(lo, hi)

    def _on_auto_clicked(self):
        if self._entity is None:
            return
        self._entity.color_range = None
        self.refresh()
        self.range_reset.emit()
