"""Cancellable background jobs used by the recording browser."""

from PySide6.QtCore import QThread, Signal

from core.cancellation import OperationCancelled


class RecordingTask(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(float)
    progress_detail = Signal(object)

    def __init__(self, function, parent=None, *, detailed=False):
        super().__init__(parent)
        self.function = function
        self.detailed = detailed

    def run(self):
        try:
            args = (self.isInterruptionRequested, self.progress.emit)
            if self.detailed:
                args += (self.progress_detail.emit,)
            result = self.function(*args)
            if not self.isInterruptionRequested():
                self.done.emit(result)
        except OperationCancelled:
            pass
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))
