"""Image, table and structured-message views for recording frames."""

import json

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ui.theme import get_theme
from ui.widgets.image_annotations import ImageAnnotationLayer


class ElidedLabel(QLabel):
    """Keep long topic names readable without forcing a wider window."""
    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text):
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._elide()

    def text(self):
        return self._full_text

    def clear(self):
        self.setText("")

    def _elide(self):
        super().setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideRight,
                                                     max(0, self.contentsRect().width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()


def image_to_qimage(array):
    data = np.ascontiguousarray(array)
    if data.dtype != np.uint8:
        valid = np.isfinite(data)
        scaled = np.zeros(data.shape, dtype=np.uint8)
        if valid.any():
            low, high = np.percentile(data[valid], [1, 99])
            scaled[valid] = np.clip(
                (data[valid] - low) * 255 / max(high - low, 1e-12), 0, 255
            )
        data = scaled
    if data.ndim == 2:
        format_ = QImage.Format_Grayscale8
    elif data.ndim == 3 and data.shape[2] == 3:
        format_ = QImage.Format_RGB888
    elif data.ndim == 3 and data.shape[2] == 4:
        format_ = QImage.Format_RGBA8888
    else:
        raise ValueError(f"无法显示图像形状 {data.shape}")
    return QImage(
        data.data, data.shape[1], data.shape[0], data.strides[0], format_
    ).copy()


class ImageView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#17191d"))
        self._pixmap = self.scene().addPixmap(QPixmap())
        self._box_layer = ImageAnnotationLayer(self._pixmap)
        self.projected_boxes = []
        self._auto_fit = True

    def set_projected_boxes(self, boxes):
        self.projected_boxes = list(boxes)
        self._box_layer.set_boxes(self.projected_boxes, self._pixmap.boundingRect())

    def set_image(self, array, pixels=None, colors=None, point_size=2, opacity=.7):
        self.set_projected_boxes([])
        image = image_to_qimage(array)
        if pixels is not None and len(pixels):
            painter = QPainter(image)
            if colors is None:
                colors = np.repeat(np.array([[40, 230, 140]], np.uint8), len(pixels), axis=0)
            palette, groups = np.unique(colors, axis=0, return_inverse=True)
            for index, rgb in enumerate(palette):
                painter.setPen(QPen(QColor(*map(int, rgb), round(opacity * 255)), point_size))
                painter.drawPoints(QPolygonF([QPointF(float(x), float(y))
                                              for x, y in pixels[groups == index]]))
            painter.end()
        previous = self._pixmap.pixmap().size()
        self._pixmap.setPixmap(QPixmap.fromImage(image))
        self.scene().setSceneRect(self._pixmap.boundingRect())
        if self._auto_fit or previous != image.size():
            self.fit_image()

    def fit_image(self):
        self._auto_fit = True
        if not self._pixmap.pixmap().isNull():
            self.fitInView(self._pixmap, Qt.KeepAspectRatio)

    def original_size(self):
        self._auto_fit = False
        self.resetTransform()

    def wheelEvent(self, event):
        delta = event.pixelDelta().y() / 50 if not event.pixelDelta().isNull() else event.angleDelta().y() / 120
        if not delta:
            event.ignore()
            return
        self._auto_fit = False
        current = self.transform().m11()
        target = min(64.0, max(0.01, current * 1.2 ** max(-10, min(10, delta))))
        factor = target / current
        self.scale(factor, factor)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._auto_fit:
            self.fit_image()


class TableModel(QAbstractTableModel):
    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.columns = value.get("columns", [])
        self.rows = value.get("rows", [])

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and index.isValid():
            row = self.rows[index.row()]
            return str(row[index.column()]) if index.column() < len(row) else ""
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole:
            return (
                str(self.columns[section])
                if orientation == Qt.Horizontal
                else str(section + 1)
            )
        return None

    def sort(self, column, order=Qt.AscendingOrder):
        def key(row):
            text = str(row[column]) if column < len(row) else ""
            try:
                return 0, float(text)
            except ValueError:
                return 1, text.casefold()

        self.layoutAboutToBeChanged.emit()
        self.rows = sorted(self.rows, key=key, reverse=order == Qt.DescendingOrder)
        self.layoutChanged.emit()


class DataView(QWidget):
    display_requested = Signal()
    fusion_requested = Signal()
    color_requested = Signal(str)
    files_dropped = Signal(list)
    cycle_color_requested = Signal()
    point_size_requested = Signal(float)
    page_requested = Signal(int)
    cloud_import_requested = Signal()
    next_requested = Signal()
    previous_requested = Signal()
    play_requested = Signal()
    first_requested = Signal()
    last_requested = Signal()
    fps_updated = Signal(float)
    render_problem = Signal(str)

    def __init__(self, theme="dark", viewport_factory=None, parent=None):
        super().__init__(parent)
        self.theme = theme
        self.viewport_factory = viewport_factory
        self.cloud_view = None
        self.cloud = None
        self.frame = None
        self._source_key = None
        self._table_value = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.toolbar = toolbar
        self.caption = ElidedLabel("选择数据流")
        toolbar.addWidget(self.caption, 1)
        self.fit = QPushButton("适应")
        self.fit.setToolTip("适应窗口，恢复完整视野")
        self.one_to_one = QPushButton("1:1")
        self.display_button = QPushButton("颜色")
        self.display_button.setToolTip("调整此点云的着色方式、色图和字段")
        self.display_button.clicked.connect(self.display_requested)
        self.fusion_button = QPushButton("融合")
        self.fusion_button.setToolTip("标定、点云投影、图像赋色和三维框联动")
        self.fusion_button.clicked.connect(self.fusion_requested)
        toolbar.addWidget(self.display_button)
        toolbar.addWidget(self.fusion_button)
        toolbar.addWidget(self.fit)
        toolbar.addWidget(self.one_to_one)
        layout.addLayout(toolbar)
        self.summary = ElidedLabel()
        self.summary.setObjectName("dim")
        layout.addWidget(self.summary)
        self.stack = QStackedWidget()
        self.empty = QLabel("选择左侧的数据流开始查看")
        self.empty.setWordWrap(True)
        self.empty.setTextFormat(Qt.PlainText)
        self.empty.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.empty.setAlignment(Qt.AlignCenter)
        self.image = ImageView()
        self._preview_shortcuts(self.image)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setHorizontalScrollMode(QTableView.ScrollPerPixel)
        self.table.setVerticalScrollMode(QTableView.ScrollPerPixel)
        self.table.setSortingEnabled(True)
        for widget in (self.empty, self.image, self.text, self.table):
            self.stack.addWidget(widget)
        layout.addWidget(self.stack, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(
            "搜索本页文本 / 筛选本页表格，按 Enter 查找下一处"
        )
        self.search.returnPressed.connect(self._search)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._filter_table)
        self.search.textChanged.connect(lambda: self._filter_timer.start())
        layout.addWidget(self.search)
        self.page_bar = QWidget()
        pages = QHBoxLayout(self.page_bar)
        pages.setContentsMargins(0, 0, 0, 0)
        self.previous_page = QPushButton("上一页")
        self.next_page = QPushButton("下一页")
        self.page_label = QLabel()
        for widget in (self.previous_page, self.page_label, self.next_page):
            pages.addWidget(widget)
        pages.addStretch(1)
        self.as_cloud = QPushButton("作为点云导入…")
        pages.addWidget(self.as_cloud)
        self.previous_page.clicked.connect(lambda: self.page_requested.emit(-1))
        self.next_page.clicked.connect(lambda: self.page_requested.emit(1))
        self.as_cloud.clicked.connect(self.cloud_import_requested)
        layout.addWidget(self.page_bar)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(132)
        self.details_toggle = QPushButton("信息")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setMaximumWidth(52)
        self.details_toggle.toggled.connect(self.details.setVisible)
        self.details_toggle.toggled.connect(
            lambda checked: self.details_toggle.setText(
                "收起" if checked else "信息"
            )
        )
        toolbar.addWidget(self.details_toggle)
        layout.addWidget(self.details)
        self.details.hide()
        self.fit.clicked.connect(self._fit)
        self.one_to_one.clicked.connect(self.image.original_size)
        self.set_theme(theme)
        self.clear()

    def compact_header(self):
        """The recording workspace has source selectors; use one metadata row."""
        self.caption.hide()
        self.layout().removeWidget(self.summary)
        self.toolbar.insertWidget(0, self.summary, 1)
        self.fit.setMaximumWidth(58)
        self.one_to_one.setMaximumWidth(48)

    def clear(self, message="选择左侧的数据流开始查看"):
        self._filter_timer.stop()
        self.frame = None
        self.cloud = None
        self.image.set_projected_boxes([])
        if self.cloud_view is not None:
            self.cloud_view.set_entities([])
        self.empty.setText(message)
        self.stack.setCurrentWidget(self.empty)
        self.details.clear()
        self.caption.setText("暂无可显示内容")
        self.summary.clear()
        self.details_toggle.hide()
        self.details.hide()
        self.fit.hide()
        self.display_button.hide()
        self.fusion_button.hide()
        self.one_to_one.hide()
        self.search.hide()
        self.page_bar.hide()

    def set_page_state(self, number=1, numeric_file=False):
        metadata = self.frame.metadata if self.frame is not None else {}
        has_next = metadata.get("next_offset") is not None
        self.previous_page.setEnabled(number > 1)
        self.next_page.setEnabled(has_next)
        self.page_label.setText(f"第 {number} 页 · 搜索与导出作用于当前页")
        for widget in (self.previous_page, self.next_page, self.page_label):
            widget.setVisible(number > 1 or has_next)
        self.as_cloud.setVisible(numeric_file)
        self.page_bar.setVisible(number > 1 or has_next or numeric_file)

    def show_frame(self, frame, title="", pixels=None, colors=None, point_size=2, opacity=.7,
                   source_key=None):
        if source_key is not None and source_key != self._source_key:
            self._cloud_shown = False
            self._reload_camera_state = None
            self.image._auto_fit = True
        self._source_key = source_key
        self.frame = frame
        self.cloud = None
        if frame.kind != "pointcloud" and self.cloud_view is not None:
            self.cloud_view.set_entities([])
        self.caption.setText(title)
        self.fit.setVisible(frame.kind in ("image", "pointcloud"))
        self.one_to_one.setVisible(frame.kind == "image")
        self.display_button.setVisible(frame.kind == "pointcloud")
        self.fusion_button.setVisible(frame.kind == "image")
        self.search.setVisible(frame.kind not in ("image", "pointcloud"))
        if frame.kind == "image":
            self.image.set_image(frame.value, pixels, colors, point_size, opacity)
            self.stack.setCurrentWidget(self.image)
        elif frame.kind == "pointcloud":
            if self.cloud_view is None:
                if self.viewport_factory is None:
                    from ui.viewport import PointCloudViewport

                    self.viewport_factory = PointCloudViewport
                self.cloud_view = self.viewport_factory(theme=self.theme, parent=self)
                self.cloud_view.files_dropped.connect(self.files_dropped)
                self.cloud_view.key_cycle_color.connect(self.cycle_color_requested)
                self.cloud_view.key_reset_view.connect(self._fit)
                self.cloud_view.key_point_size.connect(self.point_size_requested)
                self.cloud_view.key_next_file.connect(self.next_requested)
                self.cloud_view.key_prev_file.connect(self.previous_requested)
                self.cloud_view.fps_updated.connect(self.fps_updated)
                if hasattr(self.cloud_view, "render_problem"):
                    self.cloud_view.render_problem.connect(self.render_problem)
                self.stack.addWidget(self.cloud_view)
                self._preview_shortcuts(self.cloud_view)
                from core.model.entities import COLOR_MODES

                for number, mode in enumerate(COLOR_MODES, 1):
                    shortcut = QShortcut(QKeySequence(str(number)), self.cloud_view)
                    shortcut.setContext(Qt.WidgetWithChildrenShortcut)
                    shortcut.activated.connect(
                        lambda mode=mode: self.color_requested.emit(mode)
                    )
            self.cloud = frame.value
            initial = not getattr(self, "_cloud_shown", False)
            self.cloud_view.set_entities([self.cloud])
            self.stack.setCurrentWidget(self.cloud_view)
            if initial:
                state = getattr(self, "_reload_camera_state", None)
                if state is not None and hasattr(self.cloud_view, "set_camera_state"):
                    self.cloud_view.set_camera_state(state)
                    self._reload_camera_state = None
                else:
                    self.cloud_view.focus_entity(self.cloud)
                self._cloud_shown = True
        elif frame.kind == "table":
            self._table_value = frame.value
            self.stack.setCurrentWidget(self.table)
            self._filter_table()
        else:
            text = (
                frame.value
                if isinstance(frame.value, str)
                else json.dumps(frame.value, ensure_ascii=False, indent=2, default=str)
            )
            self.text.setPlainText(text)
            self.stack.setCurrentWidget(self.text)
        metadata = dict(frame.metadata)
        metadata["记录时间（ns）"] = frame.record_time_ns
        metadata["设备 / 消息头时间（ns）"] = frame.timestamp_ns
        if frame.frame_id:
            metadata["坐标系"] = frame.frame_id
        self.details.setPlainText(json.dumps(metadata, ensure_ascii=False, indent=2, default=str)[:24000])
        summary = []
        if frame.kind == "image":
            summary.append(f"图像  {frame.value.shape[1]} × {frame.value.shape[0]}")
            encoding = metadata.get("encoding") or metadata.get("original_format") or metadata.get("original_mode")
            if encoding:
                summary.append(str(encoding))
        elif frame.kind == "pointcloud":
            summary.append(f"点云  {len(frame.value.xyz):,} 点")
            if metadata.get("partial"):
                summary.append("不完整帧 · 详情见信息")
        elif frame.kind == "table":
            summary.append(f"本页 {len(frame.value.get('rows', [])):,} 行 · {len(frame.value.get('columns', []))} 列")
        else:
            summary.append({"imu": "IMU", "camera_info": "相机参数", "message": "结构化消息", "text": "文本"}.get(frame.kind, "数据"))
        if frame.frame_id:
            summary.append(f"坐标系 {frame.frame_id}")
        if metadata.get("decode_error"):
            summary.append("解析受限 · 详情见信息")
        self.summary.setText(" · ".join(summary))
        self.details_toggle.setVisible(bool(metadata))
        self.details.setVisible(bool(metadata) and self.details_toggle.isChecked())
        self.set_page_state()

    def _preview_shortcuts(self, widget):
        for sequence, signal in (("Space", self.play_requested),
                                 ("Home", self.first_requested), ("End", self.last_requested)):
            shortcut = QShortcut(QKeySequence(sequence), widget)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(signal.emit)

    def rebuild_cloud_view(self):
        """Release a failed canvas; the next decoded frame creates a fresh one."""
        view = self.cloud_view
        if view is None:
            return
        self._reload_camera_state = view.camera_state() if hasattr(view, "camera_state") else None
        self.stack.removeWidget(view)
        view.stop()
        view.deleteLater()
        self.cloud_view = None
        self._cloud_shown = False

    def _fit(self):
        if self.stack.currentWidget() is self.cloud_view:
            self.cloud_view.reset_view()
        else:
            self.image.fit_image()

    def _search(self):
        if self.stack.currentWidget() is self.table:
            self._filter_table()
            return
        if self.stack.currentWidget() is self.text and not self.text.find(
            self.search.text()
        ):
            cursor = self.text.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.text.setTextCursor(cursor)
            self.text.find(self.search.text())

    def _filter_table(self):
        self._filter_timer.stop()
        if self.stack.currentWidget() is self.table and self._table_value is not None:
            text = self.search.text().casefold()
            value = dict(self._table_value)
            value["rows"] = [
                row
                for row in value["rows"]
                if not text or any(text in str(cell).casefold() for cell in row)
            ]
            old = self.table.model()
            self.table.setModel(TableModel(value, self.table))
            if old is not None:
                old.deleteLater()

    def stop(self):
        self._filter_timer.stop()
        if self.cloud_view is not None:
            self.cloud_view.stop()

    def set_theme(self, theme):
        self.theme = theme
        self.image.setBackgroundBrush(QColor(get_theme(theme)["bg"]))
        if self.cloud_view is not None:
            self.cloud_view.set_theme(theme)
