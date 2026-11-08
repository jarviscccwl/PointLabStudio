"""Explicit column selection before converting a numeric text file to a cloud."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from core.io.text_cloud import preview_text_cloud


class TextCloudImportDialog(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = str(path)
        self._preview = None
        self.setWindowTitle("文本转换为点云")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(Path(path).name))
        hint = QLabel("预览最多读取 64 KiB、显示 30 行。确认坐标列后导入全部数值行，其他列保留为标量场。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        controls = QHBoxLayout()
        form = QFormLayout()
        self.delimiter = QComboBox()
        for label, value in [("自动识别", "auto"), ("空白", None), ("逗号", ","),
                             ("制表符", "\t"), ("分号", ";")]:
            self.delimiter.addItem(label, value)
        form.addRow("分隔符", self.delimiter)
        self.header = QComboBox()
        for label, value in [("自动识别", None), ("首个数据行为列名", True), ("无表头", False)]:
            self.header.addItem(label, value)
        form.addRow("表头", self.header)
        controls.addLayout(form)
        axes_form = QFormLayout()
        self.axis_combos = {}
        for axis in ("x", "y", "z"):
            combo = QComboBox()
            combo.setObjectName(f"text_cloud_{axis}")
            combo.currentIndexChanged.connect(self._update_validity)
            self.axis_combos[axis] = combo
            axes_form.addRow(axis.upper() + " 坐标列", combo)
        controls.addLayout(axes_form)
        layout.addLayout(controls)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("导入点云")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.delimiter.currentIndexChanged.connect(self._refresh)
        self.header.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def _refresh(self, *_):
        self._preview = None
        try:
            sample = preview_text_cloud(self.path, delimiter=self.delimiter.currentData(),
                                        header=self.header.currentData())
        except (OSError, ValueError) as exc:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            self.status.setText(str(exc))
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            return
        self._preview = sample
        self.table.setColumnCount(len(sample.columns))
        self.table.setRowCount(len(sample.rows))
        self.table.setHorizontalHeaderLabels(sample.columns)
        for row, cells in enumerate(sample.rows):
            for column, value in enumerate(cells[:len(sample.columns)]):
                self.table.setItem(row, column, QTableWidgetItem(value[:200]))
        for axis, combo in self.axis_combos.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("请选择…", None)
            for index, name in enumerate(sample.columns):
                combo.addItem(f"{index + 1} · {name}", index)
                if sample.has_header and name.casefold() == axis:
                    combo.setCurrentIndex(index + 1)
            combo.blockSignals(False)
        self._update_validity()

    def _update_validity(self, *_):
        indices = [combo.currentData() for combo in self.axis_combos.values()]
        valid = self._preview is not None and None not in indices and len(set(indices)) == 3
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(valid)
        if self._preview is not None:
            self.status.setText("坐标映射已确认。" if valid else "请为 X、Y、Z 选择三个不同的列。无表头文件需要明确指定坐标列。")

    def options(self):
        if self._preview is None:
            raise ValueError("当前文件没有可用的列映射预览。")
        indices = {axis: combo.currentData() for axis, combo in self.axis_combos.items()}
        if None in indices.values() or len(set(indices.values())) != 3:
            raise ValueError("请先确认三个不同的坐标列。")
        return dict(delimiter=self._preview.delimiter, skip_rows=self._preview.skip_rows,
                    columns=list(self._preview.columns), **indices)
