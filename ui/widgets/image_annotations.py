"""A separate image-space annotation layer; editing never touches the pixmap."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem


class ImageAnnotationLayer(QGraphicsItem):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self._rect = QRectF()
        self._items = []
        self.setZValue(2)

    def set_boxes(self, boxes, rect):
        if rect != self._rect:
            self.prepareGeometryChange()
            self._rect = QRectF(rect)
        items = []
        for box in boxes:
            path = QPainterPath()
            segments = box["segments"]
            for a, b in segments:
                path.moveTo(QPointF(*map(float, a)))
                path.lineTo(QPointF(*map(float, b)))
            if len(segments):
                items.append((path, box))
        self._items = items
        self.update()

    def boundingRect(self):
        return self._rect

    def paint(self, painter, option, widget=None):
        painter.setClipRect(self._rect)
        painter.setRenderHint(QPainter.Antialiasing)
        scale = max(.01, abs(painter.worldTransform().m11()))
        for path, box in self._items:
            color = QColor("#ffd166" if box.get("selected") else "#45d6ad")
            pen = QPen(color, 2.2 if box.get("selected") else 1.5)
            pen.setCosmetic(True)
            if not box.get("manual", True):
                pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawPath(path)
            label = ""
            if any(box.get(key) for key in ("category", "id", "name")):
                label = " · ".join(str(value) for value in
                                   (box.get("category") or "未分类", box.get("id"), box.get("name")) if value)
            if label:
                rect = path.boundingRect()
                metrics = painter.fontMetrics()
                available = max(0, min(240, int(self._rect.width() * scale) - 12))
                label = metrics.elidedText(label, Qt.ElideRight, available)
                if not label or self._rect.height() * scale < metrics.height() + 4:
                    continue
                background = QRectF(0, 0, metrics.horizontalAdvance(label) + 12, metrics.height() + 4)
                x = max(self._rect.left(), min(rect.left(), self._rect.right() - background.width() / scale))
                y = max(self._rect.top(), min(rect.top() - (background.height() + 4) / scale,
                                             self._rect.bottom() - background.height() / scale))
                painter.save()
                painter.translate(x, y)
                painter.scale(1 / scale, 1 / scale)
                painter.fillRect(background, QColor(18, 22, 28, 210))
                painter.setPen(color)
                painter.drawText(background.adjusted(6, 0, -6, 0), Qt.AlignVCenter, label)
                painter.restore()
