"""雷达包导入对话框：帧范围、每帧点数、输出目录。"""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)

FRAME_BYTES_HINT = 520 * 1200 * 16  # EM4 单帧约 62 万点 × 16B（xyz + intensity，float32）


class LidarImportDialog(QDialog):
    """收集导入参数；不负责解析，只返回 values()。"""

    def __init__(self, parent, path: str, topics: list, default_dir: str,
                 kind: str = "pcap"):
        super().__init__(parent)
        self.setWindowTitle("导入雷达包")
        self.setMinimumWidth(520)
        self._path = path
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        name = QLabel(os.path.basename(path))
        name.setStyleSheet("font-weight: 600;")
        layout.addWidget(name)
        kind_label = QLabel(
            "RoboSense EM4 · pcap/pcapng 抓包（非压缩 UDP 6699）" if kind == "pcap"
            else "ROS2 MCAP · sensor_msgs/PointCloud2"
        )
        kind_label.setObjectName("dim")
        layout.addWidget(kind_label)

        form = QFormLayout()
        form.setSpacing(8)

        self.topic = QComboBox()
        for topic in topics:
            self.topic.addItem(topic.label, topic.topic)
        if topics:
            form.addRow("话题", self.topic)

        self.start = QSpinBox()
        self.start.setRange(0, 10_000_000)
        self.start.setValue(0)
        self.start.setToolTip("从第几帧开始导入（0 表示第一帧）")
        form.addRow("起始帧", self.start)

        self.count = QSpinBox()
        self.count.setRange(1, 100_000)
        self.count.setValue(1)
        form.addRow("帧数", self.count)

        self.max_points = QSpinBox()
        self.max_points.setRange(0, 5_000_000)
        self.max_points.setSingleStep(50_000)
        self.max_points.setValue(0)
        self.max_points.setSpecialValueText("全部")
        self.max_points.setToolTip("每帧均匀抽稀到该点数；0 表示不抽稀")
        form.addRow("每帧最多点数", self.max_points)

        self.directory = QLineEdit(default_dir)
        browse = QPushButton("浏览…")
        browse.clicked.connect(self._browse)
        folder = QHBoxLayout()
        folder.setSpacing(6)
        folder.addWidget(self.directory, 1)
        folder.addWidget(browse)
        wrapper = QHBoxLayout()
        wrapper.addLayout(folder)
        form.addRow("输出目录", wrapper)

        layout.addLayout(form)

        self.open_after = QCheckBox("导入完成后自动打开帧目录")
        self.open_after.setChecked(True)
        layout.addWidget(self.open_after)

        self.estimate = QLabel()
        self.estimate.setObjectName("dim")
        self.estimate.setWordWrap(True)
        layout.addWidget(self.estimate)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("开始导入")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.count.valueChanged.connect(self._update_estimate)
        self.max_points.valueChanged.connect(self._update_estimate)
        self._update_estimate()

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.directory.text())
        if folder:
            self.directory.setText(folder)

    def _update_estimate(self):
        count = self.count.value()
        points = self.max_points.value() or 620_000
        megabytes = count * points * 16 / (1024 * 1024)
        text = f"预计约 {count} 帧 / {megabytes:.0f} MB"
        if megabytes > 2048:
            text += "；文件较大，建议减少帧数或设置每帧最多点数。"
        self.estimate.setText(text)

    def _accept(self):
        directory = self.directory.text().strip()
        if not directory:
            self.estimate.setText("请先选择输出目录。")
            return
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            self.estimate.setText(f"无法创建输出目录：{exc}")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "path": self._path,
            "topic": self.topic.currentData() if self.topic.count() else None,
            "start": self.start.value(),
            "count": self.count.value(),
            "max_points": self.max_points.value(),
            "output_dir": os.path.abspath(self.directory.text().strip()),
            "open_after": self.open_after.isChecked(),
        }
