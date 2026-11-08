"""Choose recording streams and ranges after inspecting package metadata."""

import os
from collections import Counter
from datetime import datetime

from PySide6.QtCore import Qt, QSignalBlocker
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ui.recording_tasks import RecordingTask

KIND_LABELS = {
    "pointcloud": "点云",
    "image": "图像",
    "camera_info": "相机参数",
    "imu": "IMU",
    "text": "文本",
    "table": "表格",
    "message": "消息",
}


def time_text(timestamp_ns):
    if timestamp_ns is None:
        return "未知"
    try:
        return datetime.fromtimestamp(timestamp_ns / 1e9).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]
    except (ValueError, OverflowError, OSError):
        return f"{timestamp_ns} ns"


class SourceOverviewDialog(QDialog):
    def __init__(self, sources, parent=None):
        super().__init__(parent)
        self.sources = list(sources)
        self._scan = None
        self._reject_pending = False
        self.setWindowTitle("数据包概览 · 选择要查看的内容")
        self.resize(900, 620)
        self.setMinimumSize(820, 540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        title = QLabel("选择浏览内容")
        title.setObjectName("title")
        heading.addWidget(title)
        heading.addStretch(1)
        self.summary = QLabel()
        self.summary.setObjectName("dim")
        heading.addWidget(self.summary)
        layout.addLayout(heading)

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索文件、话题或消息格式")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("搜索数据源和话题")
        filters.addWidget(self.search, 1)
        self.kind_filter = QComboBox()
        self.kind_filter.setAccessibleName("筛选数据类型")
        self.kind_filter.addItem("全部类型", "")
        for kind, label in KIND_LABELS.items():
            self.kind_filter.addItem(label, kind)
        filters.addWidget(self.kind_filter)
        layout.addLayout(filters)

        selection_row = QHBoxLayout()
        self.select_visible_button = QPushButton("选择显示项")
        self.deselect_visible_button = QPushButton("取消显示项")
        for button in (self.select_visible_button, self.deselect_visible_button):
            button.setAutoDefault(False)
            button.setToolTip("仅更改当前筛选结果，保留隐藏项的选择")
            selection_row.addWidget(button)
        selection_row.addStretch(1)
        self.selection_summary = QLabel()
        self.selection_summary.setObjectName("dim")
        selection_row.addWidget(self.selection_summary)
        self.details_toggle = QPushButton("内容详情")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAutoDefault(False)
        self.details_toggle.setToolTip("查看选中话题的格式、时间和标定信息")
        selection_row.addWidget(self.details_toggle)
        layout.addLayout(selection_row)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        inventory = QWidget()
        inventory.setMinimumWidth(390)
        inventory_layout = QVBoxLayout(inventory)
        inventory_layout.setContentsMargins(0, 0, 0, 0)
        self.empty = QLabel("没有匹配的内容，请更换关键词或类型。")
        self.empty.setObjectName("hint")
        self.empty.hide()
        inventory_layout.addWidget(self.empty)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["数据源 / 话题", "类型", "数量"])
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.tree.header().setSectionResizeMode(2, QHeaderView.Fixed)
        self.tree.setColumnWidth(1, 74)
        self.tree.setColumnWidth(2, 90)
        self.tree.setUniformRowHeights(True)
        self.tree.setTextElideMode(Qt.ElideMiddle)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.currentItemChanged.connect(self._show_details)
        self.tree.itemChanged.connect(self._item_changed)
        inventory_layout.addWidget(self.tree, 1)
        self.splitter.addWidget(inventory)

        self.detail_panel = detail_panel = QWidget()
        detail_panel.setMinimumWidth(240)
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_title = QLabel("内容详情")
        detail_title.setObjectName("dim")
        detail_layout.addWidget(detail_title)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("所选数据源或话题的详情")
        self.details.setMinimumWidth(0)
        self.details.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        detail_layout.addWidget(self.details, 1)
        self.splitter.addWidget(detail_panel)
        self.splitter.setStretchFactor(0, 2)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([630, 330])
        detail_panel.hide()
        self.details_toggle.toggled.connect(detail_panel.setVisible)
        layout.addWidget(self.splitter, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("浏览范围"))
        self.mode = QComboBox()
        self.mode.addItem("全部浏览", "all")
        self.mode.addItem("指定帧范围", "frames")
        self.mode.addItem("指定时间段", "time")
        row.addWidget(self.mode)
        self.range_controls = QWidget()
        bounds = QHBoxLayout(self.range_controls)
        bounds.setContentsMargins(0, 0, 0, 0)
        self.first = QSpinBox()
        self.last = QSpinBox()
        for spin in (self.first, self.last):
            spin.setRange(1, 2_000_000_000)
            spin.setSuffix(" 帧")
            spin.setFixedWidth(142)
        self.first.setAccessibleName("起始帧")
        self.last.setAccessibleName("结束帧")
        self.last.setValue(100)
        self.start_seconds = QDoubleSpinBox()
        self.end_seconds = QDoubleSpinBox()
        for spin in (self.start_seconds, self.end_seconds):
            spin.setRange(0, 1_000_000_000)
            spin.setDecimals(3)
            spin.setSuffix(" 秒")
            spin.setFixedWidth(142)
            spin.setToolTip("相对于所选数据源中最早的记录时间")
        self.start_seconds.setAccessibleName("开始时间（秒）")
        self.end_seconds.setAccessibleName("结束时间（秒）")
        self.end_seconds.setValue(10)
        bounds.addWidget(self.first)
        bounds.addWidget(self.start_seconds)
        bounds.addWidget(QLabel("至"))
        bounds.addWidget(self.last)
        bounds.addWidget(self.end_seconds)
        row.addWidget(self.range_controls)
        row.addStretch(1)
        self.scan_button = QPushButton("统计准确帧数")
        self.scan_button.setAutoDefault(False)
        self.scan_button.setToolTip(
            "扫描已勾选的内容（包括隐藏项）并缓存索引；\n"
            "未识别的数据源会扫描整个文件。PCAP 首次扫描可能需要一些时间。"
        )
        self.scan_button.clicked.connect(self._start_scan)
        row.addWidget(self.scan_button)
        layout.addLayout(row)
        self.range_hint = QLabel()
        self.range_hint.setObjectName("hint")
        layout.addWidget(self.range_hint)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status.hide()
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("加入浏览")
        self.buttons.button(QDialogButtonBox.Ok).setObjectName("primary")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.mode.currentIndexChanged.connect(self._range_visibility)
        self.search.textChanged.connect(self._apply_filter)
        self.kind_filter.currentIndexChanged.connect(self._apply_filter)
        self.select_visible_button.clicked.connect(lambda: self._select_visible(True))
        self.deselect_visible_button.clicked.connect(lambda: self._select_visible(False))
        self._populate()
        self._range_visibility()

    def _populate(self, checked=None):
        current = self.tree.currentItem()
        current_key = current.data(0, Qt.UserRole) if current else None
        current_item = None
        duplicate_names = Counter(os.path.basename(source.path) for source in self.sources)
        with QSignalBlocker(self.tree):
            self.tree.clear()
            for source in self.sources:
                info = source.info
                name = os.path.basename(info.path)
                if duplicate_names[name] > 1:
                    name += f" · {os.path.basename(os.path.dirname(info.path))}"
                root = QTreeWidgetItem(self.tree, [name, info.format])
                root.setData(0, Qt.UserRole, (source, None))
                root.setToolTip(0, info.path)
                root.setFlags(root.flags() | Qt.ItemIsUserCheckable)
                root.setToolTip(1, "勾选文件可选择其全部数据流，包括筛选外的内容。")
                if current_key == (source, None):
                    current_item = root
                if not info.streams:
                    selected = checked is None or (
                        source.path in checked
                        and (checked[source.path] is None or bool(checked[source.path]))
                    )
                    root.setCheckState(0, Qt.Checked if selected else Qt.Unchecked)
                    root.setText(2, "尚未识别")
                    root.setToolTip(2, "勾选文件后点击「统计准确帧数」，扫描整个文件。")
                else:
                    root.setText(2, f"{len(info.streams)} 个流")
                for stream in info.streams:
                    count = "待统计" if stream.count is None else f"{stream.count:,}"
                    item = QTreeWidgetItem(
                        root, [stream.name, KIND_LABELS.get(stream.kind, "消息"), count]
                    )
                    item.setData(0, Qt.UserRole, (source, stream.id))
                    item.setData(0, Qt.UserRole + 1, " ".join((
                        source.path, info.format, stream.id, stream.name, stream.message_type
                    )).casefold())
                    item.setData(1, Qt.UserRole, stream.kind)
                    item.setToolTip(0, f"{stream.name}\n{stream.message_type}\n{source.path}")
                    item.setToolTip(1, stream.message_type)
                    item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                    selected = checked is None or (
                        source.path in checked
                        and (checked[source.path] is None or stream.id in checked[source.path])
                    )
                    item.setCheckState(0, Qt.Checked if selected else Qt.Unchecked)
                    if current_key == (source, stream.id):
                        current_item = item
                root.setExpanded(True)
            self._sync_roots()
        size = sum(source.info.size_bytes for source in self.sources)
        streams = sum(len(source.info.streams) for source in self.sources)
        self.summary.setText(
            f"{len(self.sources)} 个数据源 · {streams} 个数据流 · {size / 1024**2:,.1f} MiB"
        )
        self._apply_filter()
        if current_item is not None and not current_item.isHidden():
            self.tree.setCurrentItem(current_item)
        self._show_details(self.tree.currentItem())

    def _sync_roots(self):
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            if root.childCount():
                selected = sum(
                    root.child(i).checkState(0) == Qt.Checked
                    for i in range(root.childCount())
                )
                root.setCheckState(
                    0, Qt.Checked if selected == root.childCount()
                    else Qt.PartiallyChecked if selected else Qt.Unchecked
                )

    def _item_changed(self, item, column):
        if column != 0:
            return
        with QSignalBlocker(self.tree):
            if item.parent() is None and item.childCount():
                state = item.checkState(0)
                for index in range(item.childCount()):
                    item.child(index).setCheckState(0, state)
            self._sync_roots()
        self._update_selection_summary()

    def _apply_filter(self):
        query = self.search.text().strip().casefold()
        kind = self.kind_filter.currentData()
        first_visible = None
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            source, _ = root.data(0, Qt.UserRole)
            visible_children = 0
            for child_index in range(root.childCount()):
                item = root.child(child_index)
                visible = (not query or query in item.data(0, Qt.UserRole + 1)) and (
                    not kind or kind == item.data(1, Qt.UserRole)
                )
                item.setHidden(not visible)
                visible_children += visible
            visible = bool(visible_children) if root.childCount() else (
                not kind and (not query or query in f"{source.path} {source.info.format}".casefold())
            )
            root.setHidden(not visible)
            if visible and first_visible is None:
                first_visible = root
        current = self.tree.currentItem()
        if current is None or current.isHidden() or (
            current.parent() is not None and current.parent().isHidden()
        ):
            self.tree.setCurrentItem(first_visible)
        if first_visible is None:
            self.details.clear()
        self.empty.setVisible(first_visible is None)
        self._update_selection_summary()

    def _select_visible(self, selected):
        with QSignalBlocker(self.tree):
            for index in range(self.tree.topLevelItemCount()):
                root = self.tree.topLevelItem(index)
                if root.isHidden():
                    continue
                items = [root.child(i) for i in range(root.childCount())] or [root]
                for item in items:
                    if not item.isHidden():
                        item.setCheckState(0, Qt.Checked if selected else Qt.Unchecked)
            self._sync_roots()
        self._update_selection_summary()

    def _update_selection_summary(self):
        total = selected = visible = pending = visible_pending = 0
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            if not root.childCount():
                pending += root.checkState(0) == Qt.Checked
                visible_pending += not root.isHidden()
            for child_index in range(root.childCount()):
                item = root.child(child_index)
                total += 1
                selected += item.checkState(0) == Qt.Checked
                visible += not root.isHidden() and not item.isHidden()
        text = f"已选 {selected} / {total} 个数据流 · 显示 {visible}"
        if pending:
            text += f" · 待识别 {pending} 个文件"
        self.selection_summary.setText(text)
        for button in (self.select_visible_button, self.deselect_visible_button):
            button.setEnabled(self._scan is None and bool(visible or visible_pending))

    def _show_details(self, item, _previous=None):
        if item is None:
            self.details.clear()
            return
        source, stream_id = item.data(0, Qt.UserRole)
        info = source.info
        text = []
        if stream_id is not None:
            stream = next((s for s in info.streams if s.id == stream_id), None)
            if stream is None:
                return
            count = "待统计" if stream.count is None else f"{stream.count:,}"
            text.extend([
                "话题", stream.name, "",
                f"类型  {KIND_LABELS.get(stream.kind, '消息')}",
                f"数量  {count}", "",
                "消息格式", stream.message_type or "未知", "",
            ])
            if stream.start_ns is not None or stream.end_ns is not None:
                text.extend([
                    f"话题起始  {time_text(stream.start_ns)}",
                    f"话题结束  {time_text(stream.end_ns)}", "",
                ])
            if stream.metadata:
                text.extend(["话题信息", *self._metadata_lines(stream.metadata), ""])
        text.extend([
            "数据源", os.path.basename(info.path),
            f"{info.format} · {info.size_bytes / 1024**2:,.1f} MiB · {len(info.streams)} 个数据流",
            "", "记录时间",
            f"起始  {time_text(info.start_ns)}", f"结束  {time_text(info.end_ns)}",
        ])
        if info.start_ns is None and info.end_ns is None:
            text.append("时间尚未统计，或数据源没有记录时钟。")
        if not info.streams:
            text.extend(["", "尚未识别数据流", "勾选该文件后点击「统计准确帧数」，可扫描整个文件。"])
        if info.warnings:
            text.extend(["", "注意事项"])
            for warning in info.warnings:
                text.extend([f"• {warning}", ""])
        if info.metadata:
            text.extend(["", "文件信息", *self._metadata_lines(info.metadata)])
        text.extend(["", "完整路径", info.path])
        self.details.setPlainText("\n".join(text))
        self.details.setToolTip(info.path)

    @classmethod
    def _metadata_lines(cls, metadata, indent=""):
        labels = {
            "clock_domain": "记录时钟", "timestamp_domain": "时间戳时钟",
            "clock_relation": "时钟关系", "protocol": "协议",
            "source_ip": "来源 IP", "source_port": "来源端口",
            "calibration": "标定", "calibration_source": "标定来源",
            "inspection_sample_bytes": "快速检查字节数",
        }
        result = []
        for key, value in metadata.items():
            label = labels.get(key, str(key).replace("_", " "))
            if isinstance(value, dict):
                result.append(f"{indent}{label}")
                result.extend(cls._metadata_lines(value, indent + "  "))
            elif isinstance(value, (list, tuple)):
                result.append(f"{indent}{label}（{len(value)} 项）")
                for index, entry in enumerate(value):
                    result.extend(cls._metadata_lines({str(index + 1): entry}, indent + "  "))
            else:
                result.append(f"{indent}{label}  {value}")
        return result

    def _range_visibility(self):
        mode = self.mode.currentData()
        self.range_controls.setVisible(mode != "all")
        self.first.setVisible(mode == "frames")
        self.last.setVisible(mode == "frames")
        self.start_seconds.setVisible(mode == "time")
        self.end_seconds.setVisible(mode == "time")
        self.range_hint.setText({
            "all": "按需加载当前帧；无需预先填写帧数。",
            "frames": "帧号从 1 开始，范围包含起止帧，并分别应用于每个所选数据流。",
            "time": "时间相对于所选数据源中最早的记录时间；没有记录时钟的数据无法按时间筛选。",
        }[mode])

    def set_selection(self, selection_dict):
        """Restore available sources and streams using the selection() contract."""
        self._populate(selection_dict.get("sources", {}))
        bounds = selection_dict.get("range", {})
        mode = self.mode.findData(bounds.get("mode", "all"))
        self.mode.setCurrentIndex(max(0, mode))
        self.first.setValue(bounds.get("first", 0) + 1)
        self.last.setValue(bounds.get("last", 99) + 1)
        self.start_seconds.setValue(bounds.get("start_seconds", 0))
        self.end_seconds.setValue(bounds.get("end_seconds", 10))
        self._range_visibility()

    def _checked_sources(self, include_unknown=False):
        sources = {}
        for index in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(index)
            source, _ = root.data(0, Qt.UserRole)
            selected = [
                root.child(i).data(0, Qt.UserRole)[1]
                for i in range(root.childCount())
                if root.child(i).checkState(0) == Qt.Checked
            ]
            if selected:
                sources[source.path] = selected
            elif include_unknown and not root.childCount() and root.checkState(0) == Qt.Checked:
                sources[source.path] = None
        return sources

    def selection(self):
        return {
            "sources": self._checked_sources(),
            "range": {
                "mode": self.mode.currentData(),
                "first": self.first.value() - 1,
                "last": self.last.value() - 1,
                "start_seconds": self.start_seconds.value(),
                "end_seconds": self.end_seconds.value(),
            },
        }

    def accept(self):
        if self._scan is not None:
            return
        selected = self.selection()
        if not selected["sources"]:
            self._set_status("请至少选择一个数据流。未识别的文件可先统计准确帧数。")
            return
        bounds = selected["range"]
        if (bounds["mode"] == "frames" and bounds["last"] < bounds["first"]) or (
            bounds["mode"] == "time" and bounds["end_seconds"] < bounds["start_seconds"]
        ):
            self._set_status("结束位置应大于或等于开始位置。")
            return
        super().accept()

    def _start_scan(self):
        if self._scan is not None:
            self._scan.requestInterruption()
            return
        chosen = self._checked_sources(include_unknown=True)
        sources = [source for source in self.sources if source.path in chosen]
        if not sources:
            self._set_status("请先勾选需要统计的数据流或未识别的文件。")
            return

        def scan(cancel, progress):
            for index, source in enumerate(sources):
                if cancel():
                    return
                source.build_index(
                    chosen[source.path],
                    cancel=cancel,
                    progress=lambda value, i=index: progress(
                        (i + value) / len(sources)
                    ),
                )
            return chosen

        self._scan = RecordingTask(scan, self)
        self._scan.done.connect(lambda checked: self._populate(checked))
        self._scan.failed.connect(self._set_status)
        self._scan.progress.connect(
            lambda value: self.progress.setValue(round(value * 100))
        )
        self._scan.finished.connect(self._scan_finished)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self.tree.setEnabled(False)
        self.search.setEnabled(False)
        self.kind_filter.setEnabled(False)
        self.select_visible_button.setEnabled(False)
        self.deselect_visible_button.setEnabled(False)
        self.scan_button.setText("取消统计")
        self.progress.setValue(0)
        self.progress.show()
        self._set_status("正在建立索引…")
        self._scan.start()

    def _scan_finished(self):
        thread, self._scan = self._scan, None
        cancelled = thread.isInterruptionRequested()
        thread.deleteLater()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        self.tree.setEnabled(True)
        self.search.setEnabled(True)
        self.kind_filter.setEnabled(True)
        self._update_selection_summary()
        self.scan_button.setText("统计准确帧数")
        self.progress.hide()
        if cancelled:
            self._set_status("统计已取消，可以重新扫描。")
        elif self.status.text() == "正在建立索引…":
            self._set_status("统计完成。" if not self.selection()["sources"] else "索引已就绪。")
        if self._reject_pending:
            super().reject()

    def _set_status(self, message):
        self.status.setText(message)
        self.status.setVisible(bool(message))

    def reject(self):
        if self._scan is not None:
            self._reject_pending = True
            self._scan.requestInterruption()
            self._set_status("正在取消统计…")
            return
        super().reject()
