"""Focused dialogs for independent calibration import and intrinsic editing."""
import math
from pathlib import PurePosixPath

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout,
    QLabel, QLineEdit, QVBoxLayout, QPlainTextEdit, QStackedWidget, QWidget,
    QScrollArea,
)

from core.calibration import CameraCalibration, extrinsics_from_pose, pose_from_extrinsics
from core.calibration_files import combine_calibration, interpret_matrix, parse_numeric_matrix
from ui.theme import get_theme


def _label(text):
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(True)
    return widget


def _scrolling_form(dialog, spacing):
    outer = QVBoxLayout(dialog)
    outer.setContentsMargins(20, 18, 20, 18)
    outer.setSpacing(spacing)
    scroll = QScrollArea()
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setFocusPolicy(Qt.NoFocus)
    contents = QWidget()
    form = QVBoxLayout(contents)
    form.setContentsMargins(0, 0, 0, 0)
    form.setSpacing(spacing)
    scroll.setWidget(contents)
    contents.setAutoFillBackground(False)
    scroll.viewport().setAutoFillBackground(False)
    outer.addWidget(scroll, 1)
    return outer, form


def _style(dialog, theme):
    colors = get_theme(theme)
    dialog.setStyleSheet(
        f"QDialog {{ background: {colors['panel']}; color: {colors['text']}; }}"
        f"QLabel {{ color: {colors['text']}; }}"
        "QLineEdit, QPlainTextEdit, QComboBox {"
        f"background: {colors['panel2']}; color: {colors['text']}; "
        f"border: 1px solid {colors['border_strong']}; border-radius: 5px; padding: 5px;"
        "}"
        f"QComboBox QAbstractItemView {{ background: {colors['panel2']}; color: {colors['text']}; }}"
    )
    dialog.error.setStyleSheet(f"color: {colors['danger']};")


class IntrinsicsDialog(QDialog):
    """Keep a draft isolated from the browser until a validated apply."""
    MODELS = (
        ("标准透视 · 5 系数（radtan5 / plumb_bob）", "plumb_bob:5"),
        ("标准透视 · 4 系数（radtan / plumb_bob）", "plumb_bob:4"),
        ("无畸变 · 针孔模型", "plumb_bob:0"),
        ("有理模型 · 8 系数", "rational_polynomial:8"),
        ("鱼眼 · 4 系数（equidistant）", "equidistant:4"),
    )

    def __init__(self, calibration=None, image_size=None, theme="dark", parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑相机内参")
        self.resize(720, 580)
        self._base = calibration
        self._image_size = image_size
        self.calibration = None
        self._drafts = {}
        self._model_key = None
        self.coefficients = {}
        outer, layout = _scrolling_form(self, 10)
        layout.addWidget(_label("修改焦距、主点或畸变后点击「应用并刷新」。当前外参保持不变。"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(5)
        self.fields = {}
        for i, (key, caption) in enumerate((
            ("width", "图像宽度 / px"), ("height", "图像高度 / px"),
            ("fx", "水平焦距 fx / px"), ("fy", "垂直焦距 fy / px"),
            ("cx", "主点 cx / px"), ("cy", "主点 cy / px"),
            ("skew", "倾斜量 skew / px"),
        )):
            edit = QLineEdit()
            edit.setAccessibleName(caption)
            label = _label(caption)
            label.setBuddy(edit)
            row, column = divmod(i, 2)
            grid.addWidget(label, row * 2, column)
            grid.addWidget(edit, row * 2 + 1, column)
            self.fields[key] = edit
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.model = QComboBox()
        self.model.setAccessibleName("畸变模型")
        for label, key in self.MODELS:
            self.model.addItem(label, key)
        model_row = QFormLayout()
        model_row.addRow("畸变模型", self.model)
        layout.addLayout(model_row)
        self.coefficient_grid = QGridLayout()
        self.coefficient_grid.setHorizontalSpacing(12)
        self.coefficient_grid.setVerticalSpacing(5)
        for column in range(3):
            self.coefficient_grid.setColumnStretch(column, 1)
        layout.addLayout(self.coefficient_grid)
        self.coefficient_hint = _label("畸变系数无量纲。分辨率应对应标定时的图像；仅缩放显示时软件会换算内参。")
        layout.addWidget(self.coefficient_hint)
        self.error = _label("")
        outer.addWidget(self.error)
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("应用并刷新")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        reset = buttons.addButton("恢复打开时的值", QDialogButtonBox.ResetRole)
        reset.clicked.connect(self.restore_values)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.model.currentIndexChanged.connect(self._change_model)
        self.restore_values()
        _style(self, theme)

    def restore_values(self):
        base = self._base
        if base is not None:
            values = {"width": base.width, "height": base.height, "fx": base.K[0, 0],
                      "fy": base.K[1, 1], "cx": base.K[0, 2], "cy": base.K[1, 2],
                      "skew": base.K[0, 1]}
            key = f"{base.distortion_model}:{len(base.D)}"
            coefficients = [repr(float(value)) for value in base.D]
        else:
            width, height = self._image_size or ("", "")
            values = {"width": width, "height": height, "skew": 0}
            key, coefficients = "plumb_bob:5", ["0"] * 5
        for name, edit in self.fields.items():
            edit.setText(str(values.get(name, "")))
            edit.setCursorPosition(0)
        self._drafts = {key: coefficients}
        self._model_key = None
        self.model.blockSignals(True)
        self.model.setCurrentIndex(self.model.findData(key))
        self.model.blockSignals(False)
        self._change_model()
        self.error.clear()

    def _change_model(self):
        if self._model_key is not None:
            self._drafts[self._model_key] = [edit.text() for edit in self.coefficients.values()]
        while self.coefficient_grid.count():
            item = self.coefficient_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        key = self.model.currentData()
        model, count = key.split(":")
        names = (["k1", "k2", "k3", "k4"] if model == "equidistant" else
                 ["k1", "k2", "p1", "p2", "k3", "k4", "k5", "k6"])[:int(count)]
        values = self._drafts.get(key, ["0"] * int(count))
        self.coefficients = {}
        for i, name in enumerate(names):
            edit = QLineEdit(values[i])
            edit.setCursorPosition(0)
            edit.setMinimumWidth(edit.fontMetrics().horizontalAdvance(values[i]) + 28)
            edit.setAccessibleName(f"畸变系数 {name}")
            label = _label(name)
            label.setBuddy(edit)
            row, column = divmod(i, 3)
            self.coefficient_grid.addWidget(label, row * 2, column)
            self.coefficient_grid.addWidget(edit, row * 2 + 1, column)
            self.coefficients[name] = edit
        self._model_key = key

    def accept(self):
        try:
            values = {}
            for name, edit in self.fields.items():
                try:
                    value = int(edit.text().strip()) if name in ("width", "height") else float(edit.text())
                except ValueError:
                    raise ValueError(f"请填写有效的 {edit.accessibleName()}。") from None
                if not math.isfinite(value) or (name in ("fx", "fy", "width", "height") and value <= 0):
                    raise ValueError(f"{edit.accessibleName()} 必须是有限数值；焦距和图像尺寸须大于 0。")
                values[name] = value
            coefficients = []
            for name, edit in self.coefficients.items():
                try:
                    value = float(edit.text())
                except ValueError:
                    raise ValueError(f"请填写有效的畸变系数 {name}。") from None
                if not math.isfinite(value):
                    raise ValueError(f"畸变系数 {name} 必须是有限数值。")
                coefficients.append(value)
            self.calibration = CameraCalibration(
                [[values["fx"], values["skew"], values["cx"]],
                 [0, values["fy"], values["cy"]], [0, 0, 1]],
                coefficients, values["width"], values["height"],
                self.model.currentData().split(":")[0],
                self._base.T_camera_lidar if self._base is not None else None,
            )
        except (ValueError, OverflowError) as error:
            self.error.setText(str(error))
            return
        super().accept()


class ExtrinsicsDialog(QDialog):
    """Validate a separate lidar → camera draft; apply never mutates the base."""

    def __init__(self, calibration, theme="dark", parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑雷达 → 相机外参")
        self.resize(710, 490)
        self._base = calibration
        self.calibration = None
        self._active_mode = "pose"
        self._source_matrix = None
        self._pose_snapshot = {}
        outer, layout = _scrolling_form(self, 12)
        layout.addWidget(_label(
            "方向：雷达 → 相机光学坐标，p_camera = R × p_lidar + t。\n"
            "相机坐标：X 向右、Y 向下、Z 向前；平移沿相机轴，单位为米。"))
        self.mode = QComboBox()
        self.mode.addItem("平移与旋转角", "pose")
        self.mode.addItem("4 × 4 矩阵", "matrix")
        self.mode.setAccessibleName("外参编辑方式")
        form = QFormLayout()
        form.addRow("编辑方式", self.mode)
        layout.addLayout(form)
        self.stack = QStackedWidget()
        pose_widget = QWidget()
        pose_layout = QVBoxLayout(pose_widget)
        pose_layout.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        self.fields = {}
        for index, (name, caption) in enumerate((
            ("tx", "平移 X / m"), ("ty", "平移 Y / m"), ("tz", "平移 Z / m"),
            ("roll", "绕 X 轴 · 横滚 / °"), ("pitch", "绕 Y 轴 · 俯仰 / °"),
            ("yaw", "绕 Z 轴 · 偏航 / °"),
        )):
            row, column = divmod(index, 3)
            edit = QLineEdit()
            edit.setAccessibleName(caption)
            caption_widget = _label(caption)
            caption_widget.setBuddy(edit)
            grid.addWidget(caption_widget, row * 2, column)
            grid.addWidget(edit, row * 2 + 1, column)
            grid.setColumnStretch(column, 1)
            self.fields[name] = edit
        pose_layout.addLayout(grid)
        pose_layout.addWidget(_label(
            "旋转约定：右手系，依次绕固定 X、Y、Z 轴旋转；角度单位为度。\n"
            "R = Rz(偏航) × Ry(俯仰) × Rx(横滚)。俯仰 ±90° 时角度不唯一，显示偏航为 0 的等价解。"))
        pose_layout.addStretch(1)
        self.stack.addWidget(pose_widget)
        matrix_widget = QWidget()
        matrix_layout = QVBoxLayout(matrix_widget)
        matrix_layout.setContentsMargins(0, 0, 0, 0)
        self.matrix = QPlainTextEdit()
        self.matrix.setAccessibleName("雷达 → 相机 4×4 外参矩阵，平移单位米")
        self.matrix.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.matrix.setPlaceholderText("粘贴 4 行矩阵，以空格或逗号分隔；最后一行为 0 0 0 1")
        matrix_layout.addWidget(self.matrix, 1)
        matrix_layout.addWidget(_label("左上 3×3 必须为正交旋转矩阵且行列式为 +1；最后一列前三项为米。"))
        self.stack.addWidget(matrix_widget)
        layout.addWidget(self.stack, 1)
        self.error = _label("")
        outer.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("应用并刷新")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.addButton("恢复打开时的值", QDialogButtonBox.ResetRole).clicked.connect(self.restore_values)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.mode.currentIndexChanged.connect(self._change_mode)
        self.restore_values()
        _style(self, theme)

    def _display_matrix(self, matrix):
        self._source_matrix = None if matrix is None else matrix.copy()
        if matrix is None:
            values = [""] * 6
            self.matrix.clear()
        else:
            translation, rotation = pose_from_extrinsics(matrix)
            values = [repr(float(value)) for value in (*translation, *rotation)]
            self.matrix.setPlainText("\n".join(" ".join(repr(float(value)) for value in row)
                                                 for row in matrix))
        for (name, edit), value in zip(self.fields.items(), values):
            edit.setText(value)
            edit.setCursorPosition(0)
        self._pose_snapshot = {name: edit.text() for name, edit in self.fields.items()}

    def restore_values(self):
        self._display_matrix(self._base.T_camera_lidar if self._base is not None else None)
        self.error.clear()

    def _read_draft(self):
        if self._active_mode == "matrix":
            matrix = parse_numeric_matrix(self.matrix.toPlainText())
            if matrix.shape != (4, 4):
                raise ValueError("请输入完整的 4×4 外参矩阵。")
            try:
                pose_from_extrinsics(matrix)
            except ValueError as error:
                raise ValueError("外参无效：最后一行须为 0 0 0 1；旋转须正交且行列式为 +1。") from error
            return matrix
        values = []
        for name, edit in self.fields.items():
            try:
                value = float(edit.text())
                if not math.isfinite(value):
                    raise ValueError
            except ValueError:
                raise ValueError(f"请填写有限数值：{edit.accessibleName()}。") from None
            values.append(value)
        if self._source_matrix is not None and all(
                self.fields[name].text() == self._pose_snapshot[name] for name in ("roll", "pitch", "yaw")):
            matrix = self._source_matrix.copy()
            matrix[:3, 3] = values[:3]
            return matrix
        return extrinsics_from_pose(values[:3], values[3:])

    def _change_mode(self):
        target = self.mode.currentData()
        if target == self._active_mode:
            return
        try:
            empty = (all(not edit.text().strip() for edit in self.fields.values())
                     if self._active_mode == "pose" else not self.matrix.toPlainText().strip())
            matrix = None if empty else self._read_draft()
        except ValueError as error:
            self.error.setText(str(error))
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(self.mode.findData(self._active_mode))
            self.mode.blockSignals(False)
            return
        self._display_matrix(matrix)
        self._active_mode = target
        self.stack.setCurrentIndex(0 if target == "pose" else 1)
        self.error.clear()

    def accept(self):
        try:
            if self._base is None:
                raise ValueError("请先填写或导入相机内参，再编辑雷视外参。")
            matrix = self._read_draft()
            self.calibration = CameraCalibration(
                self._base.K, self._base.D, self._base.width, self._base.height,
                self._base.distortion_model, matrix,
            )
        except (ValueError, OverflowError) as error:
            self.error.setText(str(error))
            return
        super().accept()


class CalibrationImportDialog(QDialog):
    def __init__(self, bundle, base=None, theme="dark", parent=None, base_origin=""):
        super().__init__(parent)
        self.setWindowTitle("导入标定 · 选择替换参数")
        self.resize(680, 430)
        self.bundle, self.base, self.base_origin = bundle, base, base_origin
        self.calibration, self.origin = None, ""
        outer, layout = _scrolling_form(self, 14)
        layout.addWidget(_label("内参和外参可分别替换。导入外参时，文件附带的内参不会覆盖未选择替换的内参。"))
        self.matrix_controls = {}
        self._keep_components = set()
        unresolved = [(i, doc) for i, doc in enumerate(bundle.documents) if doc.requires_interpretation]
        if unresolved:
            self.resize(720, 650)
            layout.addWidget(_label("矩阵缺少说明：请选择用途；外参还须指定原始方向与平移单位。相机内参须对应标定图像尺寸。"))
            container = QWidget()
            matrix_layout = QVBoxLayout(container)
            matrix_layout.setContentsMargins(6, 6, 6, 6)
            for index, document in unresolved:
                grid = QGridLayout()
                caption = _label(document.name)
                caption.setToolTip(document.name)
                grid.addWidget(caption, 0, 0, 1, 3)
                kind, direction, units = QComboBox(), QComboBox(), QComboBox()
                kind.addItem("请选择用途 / 暂不导入", None)
                kind.addItem("相机内参 K", "intrinsics")
                kind.addItem("雷视外参", "extrinsics")
                direction.addItem("请选择原始方向", None)
                direction.addItem("雷达 → 相机", "lidar_to_camera")
                direction.addItem("相机 → 雷达", "camera_to_lidar")
                units.addItem("请选择平移单位", None)
                for caption, value in (("米（m）", "m"), ("厘米（cm）", "cm"), ("毫米（mm）", "mm")):
                    units.addItem(caption, value)
                if document.raw_matrix is None:
                    kind.setCurrentIndex(kind.findData("extrinsics"))
                    kind.setEnabled(False)
                    declared = next((document.data[key] for key in
                                     ("translation_units", "translation_unit", "length_unit")
                                     if key in document.data), None)
                    declared = ({"metre": "m", "meter": "m"}.get(declared, declared)
                                if isinstance(declared, str) else None)
                    if units.findData(declared) >= 0:
                        units.setCurrentIndex(units.findData(declared))
                for column, (caption, control) in enumerate((("矩阵类型", kind),
                                                            ("原始方向", direction), ("平移单位", units))):
                    label = _label(caption)
                    label.setBuddy(control)
                    control.setAccessibleName(f"{document.name} · {caption}")
                    grid.addWidget(label, 1, column)
                    grid.addWidget(control, 2, column)
                    grid.setColumnStretch(column, 1)
                width, height = QLineEdit(), QLineEdit()
                width.setPlaceholderText("标定图像宽度 / px")
                height.setPlaceholderText("标定图像高度 / px")
                width.setAccessibleName(f"{document.name} · 图像宽度")
                height.setAccessibleName(f"{document.name} · 图像高度")
                if base is not None:
                    width.setText(str(base.width))
                    height.setText(str(base.height))
                grid.addWidget(width, 3, 0)
                grid.addWidget(height, 3, 1)
                self.matrix_controls[index] = {"type": kind, "direction": direction, "units": units,
                                               "width": width, "height": height}
                matrix_layout.addLayout(grid)
            layout.addWidget(container)
        self.intrinsics, self.extrinsics = QComboBox(), QComboBox()
        form = QFormLayout()
        form.addRow("相机内参", self.intrinsics)
        form.addRow("雷视外参", self.extrinsics)
        layout.addLayout(form)
        for combo in (self.intrinsics, self.extrinsics):
            combo.setMinimumContentsLength(20)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preview = _label("")
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.preview)
        self.error = _label("")
        outer.addWidget(self.error)
        if bundle.ignored:
            ignored = _label(f"已略过 {len(bundle.ignored)} 个非标定或不可读取文件（悬停查看）。")
            ignored.setToolTip("\n".join(bundle.ignored))
            layout.addWidget(ignored)
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("导入并刷新")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        _style(self, theme)
        for kind, combo in (("intrinsics", self.intrinsics), ("extrinsics", self.extrinsics)):
            remember_choice = lambda _index, component=kind: self._remember_keep_choice(component)
            combo.currentIndexChanged.connect(remember_choice)
            combo.activated.connect(remember_choice)
            combo.currentIndexChanged.connect(self._preview)
        for controls in self.matrix_controls.values():
            controls["type"].currentIndexChanged.connect(self._matrix_type_changed)
            for key in ("direction", "units"):
                controls[key].currentIndexChanged.connect(self._preview)
            for key in ("width", "height"):
                controls[key].textChanged.connect(self._preview)
        self._matrix_type_changed()

    def _remember_keep_choice(self, kind):
        # Rebuilding the candidate lists blocks signals; only an explicit choice
        # changes this intent, even if its candidate disappears temporarily.
        if getattr(self, kind).currentData() is None:
            self._keep_components.add(kind)
        else:
            self._keep_components.discard(kind)

    def _matrix_type_changed(self, *_):
        for controls in self.matrix_controls.values():
            kind = controls["type"].currentData()
            for key in ("direction", "units"):
                controls[key].setEnabled(kind == "extrinsics")
            for key in ("width", "height"):
                controls[key].setVisible(kind == "intrinsics")
        self._populate_choices()

    def _has_kind(self, index, kind):
        document = self.bundle.documents[index]
        if document.raw_matrix is not None:
            return self.matrix_controls[index]["type"].currentData() == kind
        return getattr(document, f"has_{kind}")

    def _populate_choices(self):
        for combo, kind in ((self.intrinsics, "intrinsics"), (self.extrinsics, "extrinsics")):
            previous = combo.currentData()
            candidates = [i for i in range(len(self.bundle.documents)) if self._has_kind(i, kind)]
            preferred = ([i for i in candidates if not self._has_kind(i, "extrinsics")]
                         if kind == "intrinsics" else candidates) or candidates
            combo.blockSignals(True)
            combo.clear()
            if len(preferred) > 1:
                combo.addItem("发现多组参数，请选择…", "choose")
            combo.addItem("保留当前内参" if kind == "intrinsics" and self.base else (
                          "暂不导入内参" if kind == "intrinsics" else "保留当前外参 / 暂不导入"), None)
            for i in candidates:
                name = self.bundle.documents[i].name
                combo.addItem(name, i)
                combo.setItemData(combo.count() - 1, name, Qt.ToolTipRole)
            keep_current = (kind == "intrinsics" and self.base is not None
                            and all(self._has_kind(i, "extrinsics") for i in candidates))
            if kind in self._keep_components:
                combo.setCurrentIndex(combo.findData(None))
            elif isinstance(previous, int) and previous in candidates:
                combo.setCurrentIndex(combo.findData(previous))
            elif keep_current:
                combo.setCurrentIndex(combo.findData(None))
            elif len(preferred) == 1:
                combo.setCurrentIndex(combo.findData(preferred[0]))
            combo.blockSignals(False)
        self._preview()

    def _selection(self):
        selected = []
        for combo in (self.intrinsics, self.extrinsics):
            index = combo.currentData()
            if index == "choose":
                raise ValueError("请选择所需参数，或明确选择保留当前参数。")
            document = self.bundle.documents[index] if index is not None else None
            if (document is not None and document.requires_interpretation
                    and (document.raw_matrix is not None or combo is self.extrinsics)):
                controls = self.matrix_controls[index]
                kind = controls["type"].currentData()
                size = None
                if kind == "intrinsics":
                    try:
                        size = (int(controls["width"].text()), int(controls["height"].text()))
                    except ValueError:
                        raise ValueError("请填写该内参对应的标定图像宽度和高度（正整数像素）。") from None
                document = interpret_matrix(document, kind, controls["direction"].currentData(),
                                            controls["units"].currentData(), image_size=size)
            selected.append(document)
        return selected

    def _preview(self, *_):
        self.error.clear()
        self.apply_button.setEnabled(False)
        self.preview.clear()
        try:
            intrinsics, extrinsics = self._selection()
            calibration = combine_calibration(intrinsics, extrinsics, self.base)
            origin = []
            details = [f"应用后：{calibration.width} × {calibration.height} px · {calibration.distortion_model}",
                       f"fx {calibration.K[0, 0]:.9g} · fy {calibration.K[1, 1]:.9g} · "
                       f"cx {calibration.K[0, 2]:.9g} · cy {calibration.K[1, 2]:.9g}"]
            for label, doc in (("内参", intrinsics), ("外参", extrinsics)):
                if doc is not None:
                    details.append(f"{label}：{doc.name}\n{doc.status}")
                    origin.append(f"{label} {PurePosixPath(doc.name).name}（{doc.status}）")
                else:
                    present = self.base is not None and (label == "内参" or self.base.T_camera_lidar is not None)
                    details.append(f"{label}：保留当前值" if present else f"{label}：未提供")
                    origin.append(f"{label}保留当前值" if present else f"{label}未提供")
            if calibration.T_camera_lidar is not None:
                details.append("外参方向：雷达 → 相机光学坐标；平移单位：米。")
            self.preview.setText("\n".join(details))
            if self.base_origin and (intrinsics is None or extrinsics is None):
                origin.append(f"继承自 {self.base_origin}")
            self._candidate, self._origin = calibration, "；".join(origin)
            self.apply_button.setEnabled(True)
        except ValueError as error:
            self.error.setText(str(error))

    def accept(self):
        self._preview()
        if self.apply_button.isEnabled():
            self.calibration, self.origin = self._candidate, self._origin
            super().accept()
