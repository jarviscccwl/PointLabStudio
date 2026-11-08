"""Cancellable background jobs used by the recording browser."""

from PySide6.QtCore import QThread, Signal

from core.cancellation import OperationCancelled


class RecordingTask(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(float)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            result = self.function(self.isInterruptionRequested, self.progress.emit)
            if not self.isInterruptionRequested():
                self.done.emit(result)
        except OperationCancelled:
            pass
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))
