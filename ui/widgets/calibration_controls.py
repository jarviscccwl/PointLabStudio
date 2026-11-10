"""Compact calibration management and projection display controls."""

import json
import math

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.calibration import CameraCalibration, calibration_to_dict
from ui.theme import get_theme


def _compact(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _bounded_number(value, current, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return current
    if isinstance(value, bool) or not math.isfinite(number):
        return current
    return max(minimum, min(maximum, number))


class CalibrationPanel(QWidget):
    """Expose controls; the recording browser owns import/save/reset actions.

    Refreshing readiness does not clear or emit the user's checkbox selections.
    The consumer must still check current calibration/frame applicability before
    processing. ``display_changed`` is emitted only for user-facing style edits;
    ``set_display_options`` restores bounded settings without signals.
    """

    display_changed = Signal()

    def __init__(self, theme="dark", parent=None):
        super().__init__(parent)
        self._calibration = None
        self._has_image = False
        self._has_cloud = False
        self._error = ""
        self._theme = theme
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        source_row = QHBoxLayout()
        source_row.setSpacing(8)
        source_row.addWidget(QLabel("相机参数"))
        self.camera = QComboBox()
        self.camera.addItem("选择包内相机参数", None)
        self.camera.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.camera.setMinimumContentsLength(12)
        self.camera.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.camera.setToolTip("选择记录中的 CameraInfo；包内参数不包含雷达到相机的外参。")
        self.calibration_button = QPushButton("导入标定…")
        self.calibration_button.setToolTip("导入 JSON、YAML、文本矩阵、TOML、NumPy 或 ZIP；可分别替换内参和外参。")
        self.edit_button = QPushButton("编辑内参…")
        self.edit_button.setToolTip("手动修改焦距、主点、分辨率和畸变系数，应用后刷新投影；保留当前外参。")
        self.edit_extrinsics_button = QPushButton("编辑外参…")
        self.edit_extrinsics_button.setToolTip("编辑雷达 → 相机的米制平移、角度或 4×4 矩阵；保留当前内参。")
        self.more_button = QPushButton("更多…")
        # Keep the public buttons as signal endpoints for existing browser wiring.
        self.save_button = QPushButton("保存标定…", self)
        self.save_button.setToolTip("保存当前内参及已明确提供的外参。")
        self.reset_button = QPushButton("清除标定", self)
        self.reset_button.setToolTip("清除当前标定和包内相机参数选择。")
        source_row.addWidget(self.camera, 1)
        layout.addLayout(source_row)
        source_actions = QHBoxLayout()
        for button in (self.calibration_button, self.edit_button, self.edit_extrinsics_button, self.more_button):
            source_actions.addWidget(button)
        source_actions.addStretch(1)
        layout.addLayout(source_actions)

        self.calibration_label = QLabel()
        self.calibration_label.setTextFormat(Qt.PlainText)
        self.calibration_label.setWordWrap(True)
        self.calibration_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.calibration_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.calibration_label)

        action_row = QGridLayout()
        self.undistort = QCheckBox("图像去畸变")
        self.overlay = QCheckBox("点云 → 图像")
        self.colorize = QCheckBox("图像 → 点云赋色")
        self.box_overlay = QCheckBox("在图像中显示 3D 框")
        self.box_overlay.setChecked(True)
        self.colorize.setToolTip("根据当前匹配图像给已有点云赋 RGB；视野外与被遮挡点保留基础颜色。")
        self.box_overlay.setToolTip("把当前点云帧的 3D 框同步显示在匹配图像上；实线为人工框，虚线为自动建议。")
        action_row.addWidget(self.overlay, 0, 0)
        action_row.addWidget(self.colorize, 0, 1)
        action_row.addWidget(self.box_overlay, 1, 0)
        action_row.addWidget(self.undistort, 1, 1)
        action_row.setColumnStretch(1, 1)
        self.details_button = QPushButton("参数详情…", self)
        self.details_button.setToolTip("以只读方式查看完整参数、外参方向和单位。")
        menu = QMenu(self.more_button)
        self._secondary_actions = []
        for button in (self.details_button, self.save_button, self.reset_button):
            action = menu.addAction(button.text())
            action.setToolTip(button.toolTip())
            action.triggered.connect(button.click)
            self._secondary_actions.append((action, button))
            button.hide()
        self.more_button.setMenu(menu)
        layout.addLayout(action_row)

        self.projection_options = QWidget()
        display_row = QHBoxLayout(self.projection_options)
        display_row.setContentsMargins(0, 0, 0, 0)
        display_row.setSpacing(8)
        self.projection_color = QComboBox()
        for label, value in (("深度（相机 Z）", "depth"), ("高度（雷达 Z）", "height"),
                             ("单色绿色", "solid")):
            self.projection_color.addItem(label, value)
        self.projection_color.setToolTip("深度 / 高度按当前可见投影点的范围分为 16 级颜色。")
        self.projection_opacity = QSpinBox()
        self.projection_opacity.setRange(10, 100)
        self.projection_opacity.setValue(70)
        self.projection_opacity.setSuffix(" %")
        self.projection_size = QSpinBox()
        self.projection_size.setRange(1, 8)
        self.projection_size.setValue(2)
        self.projection_size.setSuffix(" px")
        for label, widget in (("投影着色", self.projection_color),
                              ("不透明度", self.projection_opacity),
                              ("点大小", self.projection_size)):
            caption = QLabel(label)
            caption.setBuddy(widget)
            display_row.addWidget(caption)
            display_row.addWidget(widget)
        display_row.addStretch(1)
        layout.addWidget(self.projection_options)

        self.projection_hint = QLabel()
        self.projection_hint.setTextFormat(Qt.PlainText)
        self.projection_hint.setWordWrap(True)
        self.projection_hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.projection_hint)
        layout.addStretch(1)

        self.details_dialog = QDialog(self)
        self.details_dialog.setWindowTitle("标定参数 · 只读")
        self.details_dialog.resize(640, 540)
        details_layout = QVBoxLayout(self.details_dialog)
        self.parameter_help = QLabel(
            "K：焦距、主点与倾斜量单位为像素 px；D：无量纲畸变系数。\n"
            "T_camera_lidar：雷达 → 相机，p_camera = R × p_lidar + t；平移单位：米 m。\n"
            "相机光学坐标：X 向右、Y 向下、Z 向前。外参为 null 时未提供外参。"
        )
        self.parameter_help.setTextFormat(Qt.PlainText)
        self.parameter_help.setWordWrap(True)
        self.parameter_help.setTextInteractionFlags(Qt.TextSelectableByMouse)
        details_layout.addWidget(self.parameter_help)
        self.parameters = QPlainTextEdit()
        self.parameters.setReadOnly(True)
        self.parameters.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.parameters.setAccessibleName("只读标定参数 JSON")
        details_layout.addWidget(self.parameters, 1)
        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.button(QDialogButtonBox.Close).setText("关闭")
        close_buttons.rejected.connect(self.details_dialog.reject)
        details_layout.addWidget(close_buttons)

        self.details_button.clicked.connect(self.details_dialog.open)
        self.overlay.toggled.connect(self._update_applicability)
        self.projection_color.currentIndexChanged.connect(self.display_changed)
        self.projection_opacity.valueChanged.connect(self.display_changed)
        self.projection_size.valueChanged.connect(self.display_changed)
        self.set_calibration(None)

    def set_calibration(
        self,
        calibration: CameraCalibration | None,
        origin_label="",
        error="",
        has_image=False,
        has_cloud=False,
    ):
        """Show actual validated calibration and frame-specific readiness."""
        self._calibration = calibration
        self._has_image = bool(has_image)
        self._has_cloud = bool(has_cloud)
        self._error = str(error or "")
        if calibration is None:
            summary = "内参未载入 · 外参未载入"
            self.parameters.clear()
        else:
            extrinsics = "外参就绪（雷达 → 相机）" if calibration.T_camera_lidar is not None else "外参缺失"
            summary = (
                f"内参就绪 · {calibration.width} × {calibration.height} px · "
                f"{calibration.distortion_model} · {extrinsics}"
            )
            self.parameters.setPlainText(json.dumps(
                calibration_to_dict(calibration), ensure_ascii=False, indent=2, allow_nan=False
            ))
        if origin_label:
            summary += "\n来源：" + _compact(origin_label, 70)
        if self._error:
            summary += "\n" + _compact(self._error, 110)
        self.calibration_label.setText(summary)
        self.calibration_label.setToolTip("\n".join(
            text for text in (summary, str(origin_label or ""), self._error) if text
        ))
        ready = calibration is not None
        self.save_button.setEnabled(ready)
        self.details_button.setEnabled(ready)
        self.edit_extrinsics_button.setEnabled(ready)
        self.edit_extrinsics_button.setToolTip(
            "编辑雷达 → 相机的米制平移、角度或 4×4 矩阵；保留当前内参。" if ready
            else "请先填写或导入相机内参，再编辑雷视外参。"
        )
        self.reset_button.setEnabled(ready or bool(self._error) or self.camera.currentData() is not None)
        for action, button in self._secondary_actions:
            action.setEnabled(button.isEnabled())
        self.more_button.setEnabled(any(action.isEnabled() for action, _ in self._secondary_actions))
        self._update_applicability()

    def _update_applicability(self, *_):
        calibration = self._calibration
        has_intrinsics = calibration is not None
        has_extrinsics = has_intrinsics and calibration.T_camera_lidar is not None
        self.undistort.setEnabled(has_intrinsics and self._has_image)
        self.undistort.setToolTip(
            "使用当前内参对图像去畸变。" if self.undistort.isEnabled() else "需要相机内参和当前图像。"
        )
        projection_ready = bool(has_extrinsics and self._has_image and self._has_cloud)
        self.overlay.setEnabled(projection_ready)
        self.colorize.setEnabled(projection_ready)
        self.box_overlay.setEnabled(projection_ready)
        missing = []
        for available, label in ((has_intrinsics, "相机内参"), (has_extrinsics, "雷达 → 相机外参"),
                                 (self._has_image, "当前图像"), (self._has_cloud, "当前点云")):
            if not available:
                missing.append(label)
        hint = "投影待就绪：" + "、".join(missing) if missing else (
            "深度以相机 Z 计，高度以雷达 Z 计，单位均为米；颜色按当前可见点范围显示。"
            if self.overlay.isChecked() else "三维框随编辑实时更新；图像赋色仅作用于已有点云，不生成深度。"
        )
        self.overlay.setToolTip(hint)
        self.projection_hint.setText(hint)
        self.projection_options.setVisible(projection_ready and self.overlay.isChecked())
        self.projection_hint.setVisible(True)
        for control in (self.projection_color, self.projection_opacity, self.projection_size):
            control.setEnabled(projection_ready and self.overlay.isChecked())
        self.set_theme(self._theme)

    def display_options(self) -> dict:
        return {
            "color": self.projection_color.currentData(),
            "opacity": self.projection_opacity.value() / 100,
            "size": self.projection_size.value(),
        }

    def set_display_options(self, options):
        """Restore partial display preferences, bounding values without signals."""
        if not isinstance(options, dict):
            return
        current = self.display_options()
        color = options.get("color", current["color"])
        index = self.projection_color.findData(color)
        opacity = _bounded_number(options.get("opacity"), current["opacity"], 0.1, 1.0)
        size = _bounded_number(options.get("size"), current["size"], 1, 8)
        blockers = [QSignalBlocker(control) for control in (
            self.projection_color, self.projection_opacity, self.projection_size
        )]
        try:
            if index >= 0:
                self.projection_color.setCurrentIndex(index)
            self.projection_opacity.setValue(round(opacity * 100))
            self.projection_size.setValue(round(size))
        finally:
            for blocker in blockers:
                blocker.unblock()

    def set_theme(self, theme):
        self._theme = theme
        colors = get_theme(theme)
        status_color = colors["danger"] if self._error else (
            colors["success"] if self.overlay.isEnabled() else colors["text2"]
        )
        self.calibration_label.setStyleSheet(f"color: {status_color};")
        self.projection_hint.setStyleSheet(f"color: {colors['text2']}; font-size: 11px;")
        self.parameter_help.setStyleSheet(f"color: {colors['text2']};")
        self.details_dialog.setStyleSheet(f"QDialog {{ background: {colors['panel']}; }}")
        self.parameters.setStyleSheet(
            "QPlainTextEdit {"
            f"background: {colors['panel2']}; color: {colors['text']}; "
            f"border: 1px solid {colors['border_strong']}; border-radius: 6px; "
            f"selection-background-color: {colors['accent']}; font-family: Consolas;"
            "}"
        )
