"""通用参数对话框：由字段描述生成表单（处理命令使用）。

字段：{"key", "label", "kind": "float"|"int"|"choice", "default", "min", "max",
        "decimals", "options": [(label, data), ...]}
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
    QSpinBox, QVBoxLayout,
)


class ParamDialog(QDialog):
    def __init__(self, parent, title: str, fields: list, hint: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(340)
        self._widgets = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("dim")
            hint_label.setWordWrap(True)
            layout.addWidget(hint_label)

        form = QFormLayout()
        form.setSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        for field in fields:
            kind = field.get("kind", "float")
            if kind == "choice":
                widget = QComboBox()
                for label, data in field.get("options", []):
                    widget.addItem(str(label), data)
                index = widget.findData(field.get("default"))
                if index >= 0:
                    widget.setCurrentIndex(index)
            elif kind == "int":
                widget = QSpinBox()
                widget.setRange(int(field.get("min", -10**9)), int(field.get("max", 10**9)))
                widget.setValue(int(field.get("default", 0)))
            else:
                widget = QDoubleSpinBox()
                widget.setDecimals(int(field.get("decimals", 3)))
                widget.setRange(float(field.get("min", -1e9)), float(field.get("max", 1e9)))
                widget.setValue(float(field.get("default", 0.0)))
            self._widgets[field["key"]] = widget
            widget.setAccessibleName(field["label"])
            form.addRow(field["label"], widget)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("确定")
        buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        result = {}
        for key, widget in self._widgets.items():
            if isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            else:
                result[key] = widget.value()
        return result
