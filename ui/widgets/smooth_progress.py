"""Qt progress widgets with animated paint and unchanged real-value semantics."""
import math

from PySide6.QtCore import QElapsedTimer, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QProgressBar, QProgressDialog, QStyle, QStyleOptionProgressBar


class SmoothProgressBar(QProgressBar):
    """Ease only the painted fill. value(), text and signals remain truthful."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.displayed_value = float(self.minimum())
        self.busy_phase = 0.0
        self._last_value = self.value()
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._advance)
        self.valueChanged.connect(self._value_changed)

    def _start(self):
        if self.isVisible() and not self._timer.isActive():
            self._clock.start()
            self._timer.start()

    def _value_changed(self, value):
        previous = self._last_value
        self._last_value = value
        if not self.isVisible() or value < previous:
            self.displayed_value = float(max(self.minimum(), value))
        self.displayed_value = min(self.displayed_value, max(self.minimum(), value))
        if self.minimum() == self.maximum() == 0 or self.displayed_value < value:
            self._start()
        else:
            self._timer.stop()
        self.update()

    def _sync_range(self):
        self._last_value = self.value()
        self.displayed_value = float(max(self.minimum(), self.value()))
        if self.minimum() == self.maximum() == 0:
            self._start()
        else:
            self._timer.stop()
        self.update()

    def setRange(self, minimum, maximum):  # noqa: N802
        super().setRange(minimum, maximum)
        self._sync_range()

    def setMinimum(self, minimum):  # noqa: N802
        super().setMinimum(minimum)
        self._sync_range()

    def setMaximum(self, maximum):  # noqa: N802
        super().setMaximum(maximum)
        self._sync_range()

    def reset(self):
        super().reset()
        self._sync_range()

    def _advance(self):
        elapsed = min(self._clock.restart() / 1000, 0.1)
        if self.minimum() == self.maximum() == 0:
            self.busy_phase = (self.busy_phase + elapsed / 2.8) % 1
        else:
            target = max(self.minimum(), self.value())
            distance = target - self.displayed_value
            if distance < 0 or abs(distance) < max(0.001, (self.maximum() - self.minimum()) / 10000):
                self.displayed_value = float(target)
                self._timer.stop()
            else:
                self.displayed_value += distance * (1 - math.exp(-elapsed / 0.075))
        self.update()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._sync_range()

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().closeEvent(event)

    def paintEvent(self, event):  # noqa: N802
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)
        painter = QPainter(self)
        if self.minimum() == self.maximum() == 0:
            self.style().drawControl(QStyle.CE_ProgressBarGroove, option, painter, self)
            area = QRectF(self.style().subElementRect(QStyle.SE_ProgressBarContents, option, self))
            painter.setRenderHint(QPainter.Antialiasing)
            clip = QPainterPath()
            clip.addRoundedRect(area, min(4, area.height() / 2), min(4, area.height() / 2))
            painter.setClipPath(clip)
            width = area.width() * 0.38
            travel = (1 - math.cos(math.tau * self.busy_phase)) / 2
            x = area.left() + (area.width() - width) * travel
            gradient = QLinearGradient(x, 0, x + width, 0)
            transparent = QColor(self.palette().highlight().color())
            transparent.setAlpha(0)
            gradient.setColorAt(0, transparent)
            gradient.setColorAt(0.5, self.palette().highlight().color())
            gradient.setColorAt(1, transparent)
            painter.fillRect(QRectF(x, area.top(), width, area.height()), gradient)
        else:
            span = self.maximum() - self.minimum()
            fraction = (self.displayed_value - self.minimum()) / max(1, span)
            option.minimum = 0
            option.maximum = 10000
            option.progress = round(min(1, max(0, fraction)) * 10000)
            if self.value() < self.maximum():
                option.progress = min(option.progress, 9999)
            # initStyleOption supplied text computed from the actual value.
            self.style().drawControl(QStyle.CE_ProgressBar, option, painter, self)
        painter.end()


class SmoothProgressDialog(QProgressDialog):
    """Keep QProgressDialog's native cancellation, reset and auto-close behavior."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_bar = SmoothProgressBar(self)
        self.progress_bar.setRange(self.minimum(), self.maximum())
        self.progress_bar.setValue(self.value())
        self.setBar(self.progress_bar)

    def setValue(self, value):  # noqa: N802
        super().setValue(value)
        # Qt resets the bar internally without emitting valueChanged when an
        # auto-reset dialog completes, including when auto-close is disabled.
        if self.progress_bar.value() < self.progress_bar.minimum():
            self.progress_bar._sync_range()

    def setRange(self, minimum, maximum):  # noqa: N802
        super().setRange(minimum, maximum)
        self.progress_bar._sync_range()

    def setMinimum(self, minimum):  # noqa: N802
        super().setMinimum(minimum)
        self.progress_bar._sync_range()

    def setMaximum(self, maximum):  # noqa: N802
        super().setMaximum(maximum)
        self.progress_bar._sync_range()

    def reset(self):
        super().reset()
        self.progress_bar._sync_range()
