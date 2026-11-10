"""A normal playback slider with an optional, independent inclusive frame range."""
from __future__ import annotations

import operator

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider


class FrameRangeSlider(QSlider):
    """Keep all QSlider playback behavior while adding separate range handles.

    ``rangeChanged`` retains its native meaning (slider minimum and maximum).
    ``selectionChanged`` reports zero-based inclusive first/last frame indices.
    Tab visits playback, first, last, then the next widget; Shift+Tab reverses
    that path. Escape returns arrow-key control to the playback position.
    """

    selectionChanged = Signal(int, int)

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._selection_enabled = False
        self._has_selection = False
        self._first, self._last = self.minimum(), self.maximum()
        self._active_handle = None
        self._hover_handle = None
        self._drag_handle = None
        self._drag_offset = 0
        self._theme = None
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("帧时间轴")
        self._refresh_description()

    def setSelectionEnabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._selection_enabled == enabled:
            return
        self._selection_enabled = enabled
        self._active_handle = self._hover_handle = self._drag_handle = None
        self.unsetCursor()
        self._refresh_description()
        self.updateGeometry()
        self.update()

    def selectionEnabled(self) -> bool:
        return self._selection_enabled

    def setSelection(self, first: int, last: int) -> None:
        try:
            first, last = operator.index(first), operator.index(last)
        except TypeError as exc:
            raise ValueError("帧区间的起点和终点必须是整数。") from exc
        first, last = sorted((first, last))
        self._has_selection = True
        self._store_selection(self._clamp(first), self._clamp(last))

    def selection(self) -> tuple[int, int]:
        return self._first, self._last

    def set_theme(self, theme: str) -> None:
        from ui.theme import get_theme

        self._theme = get_theme(theme)
        self.update()

    def sizeHint(self) -> QSize:
        return self._with_selection_space(super().sizeHint())

    def minimumSizeHint(self) -> QSize:
        return self._with_selection_space(super().minimumSizeHint())

    def _with_selection_space(self, size: QSize) -> QSize:
        if self._selection_enabled:
            if self.orientation() == Qt.Horizontal:
                size.setHeight(max(size.height(), 48))
            else:
                size.setWidth(max(size.width(), 48))
        return size

    def _clamp(self, value: int) -> int:
        return max(self.minimum(), min(self.maximum(), value))

    def _bounds_changed(self, minimum: int, maximum: int) -> None:
        self._drag_handle = None
        if self._has_selection:
            self._store_selection(self._clamp(self._first), self._clamp(self._last))
        else:
            self._store_selection(minimum, maximum)

    def sliderChange(self, change) -> None:
        super().sliderChange(change)
        # State must remain valid even when the owner uses QSignalBlocker
        # during a source switch. A rangeChanged connection would be skipped.
        if change == QSlider.SliderRangeChange and hasattr(self, "_first"):
            self._bounds_changed(self.minimum(), self.maximum())

    def _store_selection(self, first: int, last: int) -> None:
        if (first, last) == (self._first, self._last):
            return
        self._first, self._last = first, last
        self._refresh_description()
        self.update()
        self.selectionChanged.emit(first, last)

    def _refresh_description(self) -> None:
        if self._selection_enabled:
            description = (
                f"已选择第 {self._first + 1} 至 {self._last + 1} 帧。"
                "Tab 切换起止手柄，方向键调整，Esc 返回定位。"
            )
            if self._active_handle is not None:
                label = "起点" if self._active_handle == "first" else "终点"
                description = f"正在调整{label}。{description}"
            self.setToolTip("拖动下方起止手柄选择范围；Tab 切换，方向键调整，Esc 返回定位")
        else:
            description = "拖动定位帧，方向键逐帧调整。"
            self.setToolTip("拖动定位帧；方向键逐帧调整")
        self.setAccessibleDescription(description)

    def _native_handle(self, value: int) -> QRect:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        option.sliderPosition = value
        option.sliderValue = value
        return self.style().subControlRect(
            QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self,
        )

    def _axis(self, position: QPoint) -> int:
        return position.x() if self.orientation() == Qt.Horizontal else position.y()

    def _value_axis(self, value: int) -> int:
        return self._axis(self._native_handle(value).center())

    def _pixel_value(self, position: int) -> int:
        first = self._value_axis(self.minimum())
        last = self._value_axis(self.maximum())
        start, finish = sorted((first, last))
        if finish == start:
            return self.minimum()
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), max(0, min(finish - start, position - start)),
            finish - start, first > last,
        )

    def _selection_handle_rects(self) -> dict[str, QRect]:
        horizontal = self.orientation() == Qt.Horizontal
        length = self.width() if horizontal else self.height()
        cross_length = self.height() if horizontal else self.width()
        extent = min(18, max(1, (length - 2) // 2))
        positions = {"first": self._value_axis(self._first),
                     "last": self._value_axis(self._last)}
        starts = {name: max(0, min(length - extent, value - extent // 2))
                  for name, value in positions.items()}
        # A one-frame selection still has two distinct, reachable drag targets.
        if abs(starts["last"] - starts["first"]) < extent + 2:
            group_length = extent * 2 + 2
            middle = (positions["first"] + positions["last"]) // 2
            low = max(0, min(length - group_length, middle - group_length // 2))
            normal = self._value_axis(self.minimum()) <= self._value_axis(self.maximum())
            low_name, high_name = ("first", "last") if normal else ("last", "first")
            starts[low_name], starts[high_name] = low, low + extent + 2
        cross_start = max(0, cross_length - 18)
        if horizontal:
            return {name: QRect(start, cross_start, extent, 16) for name, start in starts.items()}
        return {name: QRect(cross_start, start, 16, extent) for name, start in starts.items()}

    def _hit_handle(self, position: QPoint) -> str | None:
        if not self._selection_enabled or not self.isEnabled():
            return None
        for name, rect in self._selection_handle_rects().items():
            if rect.adjusted(-1, -1, 1, 1).contains(position):
                return name
        return None

    def _move_handle(self, which: str, value: int) -> None:
        if which == "first":
            self._store_selection(min(self._last, self._clamp(value)), self._last)
        else:
            self._store_selection(self._first, max(self._first, self._clamp(value)))

    def mousePressEvent(self, event) -> None:
        handle = self._hit_handle(event.position().toPoint())
        if event.button() == Qt.LeftButton and handle is not None:
            self.setFocus(Qt.MouseFocusReason)
            self._active_handle = self._drag_handle = handle
            value = self._first if handle == "first" else self._last
            self._drag_offset = self._axis(event.position().toPoint()) - self._value_axis(value)
            self._refresh_description()
            self.update()
            event.accept()
            return
        self._active_handle = None
        self._refresh_description()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_handle is not None:
            value = self._pixel_value(self._axis(event.position().toPoint()) - self._drag_offset)
            self._move_handle(self._drag_handle, value)
            event.accept()
            return
        handle = self._hit_handle(event.position().toPoint())
        if handle != self._hover_handle:
            self._hover_handle = handle
            if handle is not None:
                self.setCursor(Qt.SizeHorCursor if self.orientation() == Qt.Horizontal
                               else Qt.SizeVerCursor)
                label, value = ("起点", self._first) if handle == "first" else ("终点", self._last)
                self.setToolTip(f"{label}：第 {value + 1} 帧；拖动或用 Tab 和方向键调整")
            else:
                self.unsetCursor()
                self._refresh_description()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_handle is not None:
            value = self._pixel_value(self._axis(event.position().toPoint()) - self._drag_offset)
            self._move_handle(self._drag_handle, value)
            self._drag_handle = None
            self.update()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_handle = None
        if self._drag_handle is None:
            self.unsetCursor()
        self.update()
        super().leaveEvent(event)

    def focusNextPrevChild(self, next: bool) -> bool:
        if self._selection_enabled and self.isEnabled() and self.hasFocus():
            order = (None, "first", "last")
            index = order.index(self._active_handle) + (1 if next else -1)
            if 0 <= index < len(order):
                self._active_handle = order[index]
                self._refresh_description()
                self.update()
                return True
            self._active_handle = None
        return super().focusNextPrevChild(next)

    def focusInEvent(self, event) -> None:
        if self._selection_enabled and event.reason() == Qt.BacktabFocusReason:
            self._active_handle = "last"
        else:
            self._active_handle = None
        self._refresh_description()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self._active_handle = self._drag_handle = None
        self._refresh_description()
        self.update()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._selection_enabled and self._active_handle is not None and self.isEnabled():
            key = event.key()
            if key == Qt.Key_Escape:
                self._active_handle = None
                self._refresh_description()
                self.update()
                event.accept()
                return
            current = self._first if self._active_handle == "first" else self._last
            step = self.pageStep() if event.modifiers() & Qt.ShiftModifier else self.singleStep()
            normal = self._value_axis(self.minimum()) <= self._value_axis(self.maximum())
            directions = {Qt.Key_Left: -1 if normal else 1,
                          Qt.Key_Right: 1 if normal else -1,
                          Qt.Key_Up: 1, Qt.Key_Down: -1,
                          Qt.Key_PageUp: 1, Qt.Key_PageDown: -1}
            if key in directions:
                if key in (Qt.Key_PageUp, Qt.Key_PageDown):
                    step = self.pageStep()
                direction = directions[key] * (-1 if self.invertedControls() else 1)
                value = current + direction * step
            elif key == Qt.Key_Home:
                value = self.minimum()
            elif key == Qt.Key_End:
                value = self.maximum()
            else:
                super().keyPressEvent(event)
                return
            self._move_handle(self._active_handle, value)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self._selection_enabled:
            return
        group = QPalette.Active if self.isEnabled() else QPalette.Disabled
        palette = self.palette()
        accent = palette.color(group, QPalette.Highlight)
        text = palette.color(group, QPalette.Text)
        surface = palette.color(group, QPalette.Window)
        selected_text = palette.color(group, QPalette.HighlightedText)
        if self._theme is not None and self.isEnabled():
            accent = QColor(self._theme["accent"])
            text = QColor(self._theme["text"])
            surface = QColor(self._theme["panel"])
            selected_text = QColor(self._theme["accent_text"])
        rectangles = self._selection_handle_rects()
        first, last = sorted((self._value_axis(self._first), self._value_axis(self._last)))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        try:
            tint = QColor(accent)
            tint.setAlpha(80 if self.isEnabled() else 40)
            painter.setPen(Qt.NoPen)
            painter.setBrush(tint)
            if self.orientation() == Qt.Horizontal:
                lane = rectangles["first"].center().y()
                band = QRectF(first, lane - 2, max(2, last - first), 4)
            else:
                lane = rectangles["first"].center().x()
                band = QRectF(lane - 2, first, 4, max(2, last - first))
            painter.drawRoundedRect(band, 2, 2)
            for name, rect in rectangles.items():
                active = self.hasFocus() and self._active_handle == name
                width = 2 if active or self._hover_handle == name else 1
                painter.setPen(QPen(accent, width))
                painter.setBrush(accent if active else surface)
                painter.drawRoundedRect(QRectF(rect).adjusted(1, 1, -1, -1), 3, 3)
                painter.setPen(selected_text if active else text)
                painter.drawText(rect, Qt.AlignCenter, "起" if name == "first" else "止")
        finally:
            painter.end()
