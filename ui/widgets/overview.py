"""右侧「数据概览」：分块展示基础数据（当前点云 / 空间范围 / 属性 / 场景统计）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from core.color.colormaps import rgb_has_color
from core.model.entities import CloudEntity
from ui.widgets.colorbar import ColorbarPanel


def _effective_color_mode(entity: CloudEntity) -> str:
    mode = entity.color_mode
    if mode != "auto":
        return {"auto": "自动", "height": "高度", "intensity": "强度",
                "rgb": "RGB", "label": "聚类标签", "none": "无"}.get(mode, mode)
    rgb = entity.field("rgb")
    if rgb is not None and rgb_has_color(rgb):
        return "自动 · RGB"
    if entity.field("intensity") is not None:
        return "自动 · 强度"
    return "自动 · 高度"


def _card(title: str):
    card = QWidget()
    card.setObjectName("overviewCard")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(12, 10, 12, 10)
    lay.setSpacing(7)
    head = QLabel(title)
    head.setObjectName("cardHead")
    lay.addWidget(head)
    return card, lay


def _row(label: str, value_text: str = "—", value_font: str = "valueText"):
    row = QWidget()
    row.setObjectName("infoRow")
    lay = QVBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    caption = QLabel(label)
    caption.setObjectName("rowCaption")
    value = QLabel(value_text)
    value.setObjectName(value_font)
    value.setWordWrap(True)
    value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    value.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(caption)
    lay.addWidget(value)
    return row, value


class OverviewPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("inspector")
        self._entity = None
        self._clip_statistics = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 12, 10, 12)
        root.setSpacing(10)

        title = QLabel("数据概览")
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        # ---- 当前点云 ----
        card, lay = _card("当前点云")
        self.lbl_name = QLabel("—")
        self.lbl_name.setObjectName("entityName")
        self.lbl_name.setWordWrap(True)
        self.lbl_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        lay.addWidget(self.lbl_name)

        self.lbl_points = QLabel("—")
        self.lbl_points.setObjectName("bigNumber")
        lay.addWidget(self.lbl_points)

        badge_row = QWidget()
        badge_row.setObjectName("infoRow")
        badges = QHBoxLayout(badge_row)
        badges.setContentsMargins(0, 0, 0, 0)
        badges.setSpacing(6)
        self.lbl_state = QLabel("—")
        self.lbl_state.setObjectName("badgePillDim")
        self.lbl_state.setWordWrap(True)
        badges.addWidget(self.lbl_state, 1)
        lay.addWidget(badge_row)
        root.addWidget(card)

        # ---- 空间范围 ----
        card, lay = _card("空间范围")
        self.lbl_range_x, self.v_x = _row("X 范围")
        self.lbl_range_y, self.v_y = _row("Y 范围")
        self.lbl_range_z, self.v_z = _row("Z 范围")
        self.lbl_size, self.v_size = _row("尺寸 (X × Y × Z)")
        self.lbl_diag, self.v_diag = _row("对角线")
        for row, _v in ((self.lbl_range_x, self.v_x), (self.lbl_range_y, self.v_y),
                        (self.lbl_range_z, self.v_z), (self.lbl_size, self.v_size),
                        (self.lbl_diag, self.v_diag)):
            lay.addWidget(row)
        root.addWidget(card)

        # ---- 属性 ----
        card, lay = _card("属性")
        self.lbl_fields, self.v_fields = _row("标量场")
        self.lbl_colormode, self.v_colormode = _row("颜色模式")
        self.lbl_colormap, self.v_colormap = _row("色彩映射")
        self.lbl_pointsize, self.v_pointsize = _row("点大小")
        for row in (self.lbl_fields, self.lbl_colormode, self.lbl_colormap, self.lbl_pointsize):
            lay.addWidget(row)
        root.addWidget(card)

        # ---- 场景 ----
        card, lay = _card("场景")
        self.lbl_scene, self.v_scene = _row("点云数量 / 总点数")
        self.lbl_mem, self.v_mem = _row("数据内存")
        lay.addWidget(self.lbl_scene)
        lay.addWidget(self.lbl_mem)
        root.addWidget(card)

        # ---- 来源 ----
        card, lay = _card("来源")
        self.lbl_path, self.v_path = _row("文件路径")
        lay.addWidget(self.lbl_path)
        root.addWidget(card)

        # ---- 颜色条 / 直方图 ----
        self.colorbar = ColorbarPanel(self)
        root.addWidget(self.colorbar)

        root.addStretch(1)

    # ---------------- 数据接口 ----------------
    def set_clip_statistics(self, statistics):
        self._clip_statistics = statistics
        self.colorbar.title.setText("颜色条 · 原始数据" if statistics else "颜色条")
        self.set_entity(self._entity, update_colorbar=False)

    def set_entity(self, entity: CloudEntity | None, *, update_colorbar=True):
        self._entity = entity
        if update_colorbar:
            self.colorbar.set_entity(entity)
        if entity is None:
            self.lbl_name.setText("未选择点云")
            self.lbl_points.setText("—")
            self.lbl_state.setText("在左侧选择一片点云")
            for value in (self.v_x, self.v_y, self.v_z, self.v_size, self.v_diag,
                          self.v_fields, self.v_colormode, self.v_colormap,
                          self.v_pointsize, self.v_path):
                value.setText("—")
            return

        self.lbl_name.setText(entity.name)
        self.lbl_points.setText(f"{entity.point_count:,}")
        if entity.is_preview:
            state = "预览加载中" if entity.load_state == "loading" else f"预览数据 · {entity.loaded:.0%}"
        else:
            state = "已就绪"
        self.lbl_state.setText(state)
        if entity.xyz is None:
            self.lbl_points.setText("—")
            self.lbl_state.setText({"unloaded": "待浏览", "loading": "正在加载…",
                                   "error": "加载失败，请右键重新加载"}.get(entity.load_state, "空点云"))

        clipped = self._clip_statistics.get(id(entity))
        b = entity.bounds() if clipped is None else clipped[1]
        if clipped is not None:
            count, b = clipped
            self.lbl_points.setText(f"{count:,}")
            self.lbl_state.setText(f"裁剪后 · 原始 {entity.point_count:,} 点" +
                                   (" · 预览数据" if entity.is_preview else ""))
        if b is not None:
            mn, mx = b
            self.v_x.setText(f"{mn[0]:.2f}  →  {mx[0]:.2f}  m")
            self.v_y.setText(f"{mn[1]:.2f}  →  {mx[1]:.2f}  m")
            self.v_z.setText(f"{mn[2]:.2f}  →  {mx[2]:.2f}  m")
            d = mx - mn
            self.v_size.setText(f"{d[0]:.2f} × {d[1]:.2f} × {d[2]:.2f}  m")
            self.v_diag.setText(f"{float(sum(d ** 2) ** 0.5):.2f}  m")
        else:
            for value in (self.v_x, self.v_y, self.v_z, self.v_size, self.v_diag):
                value.setText("—")

        fields = list(entity.fields.keys())
        self.v_fields.setText("、".join(fields) if fields else "无（仅 XYZ）")
        self.v_colormode.setText(_effective_color_mode(entity))
        self.v_colormap.setText(entity.colormap)
        self.v_pointsize.setText(f"{entity.point_size:.1f}  px")
        self.v_path.setText(entity.path or "—（派生数据）")

    def set_loading_progress(self, entity: CloudEntity, fraction: float):
        if self._entity is entity:
            self.lbl_state.setText(f"正在加载 {int(fraction * 100)}%")

    def set_scene_stats(self, count: int, points: int, mem_bytes: int):
        self.v_scene.setText(f"{count} 片 · {points:,} 点")
        self.v_mem.setText(f"{mem_bytes / 1024 ** 2:,.1f} MB")
