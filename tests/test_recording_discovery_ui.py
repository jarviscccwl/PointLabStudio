"""Qt-only integration checks for background import and a compact inventory.

These deliberately use no viewport or GPU; run serially with the UI suite.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QWidget

from core.cancellation import check_cancel
from core.io.recording_types import RecordingSource, SourceInfo, StreamInfo
from ui.recording_session import RecordingSessionMixin
from ui.widgets.source_overview import SourceOverviewDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(5)
    raise AssertionError("Import operation did not finish")


def dispose(widget, app):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


class InventorySource(RecordingSource):
    def __init__(self, path, count=12):
        super().__init__(str(path))
        self.info = SourceInfo(self.path, "MCAP", [
            StreamInfo("camera", "/front/image", "image", count=count),
        ])
        self.closed = False

    def inspect(self, cancel=None):
        check_cancel(cancel)
        return self.info

    def close(self):
        self.closed = True


class ImportHarness(RecordingSessionMixin, QWidget):
    def __init__(self):
        super().__init__()
        self._closing = False
        self._recording_importing = False
        self._recording_queue = []
        self._recording_jobs = set()
        self._recording_sources = {}
        self._recording_dialog = None
        self._recording_progress = None
        self._project_path = None
        self.status_task = QLabel(self)
        self.recording_browser = QWidget(self)
        self.recording_browser.play = QCheckBox(self)
        self.recording_browser.tracking = SimpleNamespace(worker=None)
        self.recording_browser._worker = None
        self.recording_browser.entries = []
        self.recording_browser.current_frames = []
        self.recording_browser.background_threads = lambda: []
        self.indexed = None
        self.messages = []

    def log(self, message):
        self.messages.append(message)

    def _index_recording_sources(self, sources, options, restore=None, refresh_state=None):
        self.indexed = (sources, options)
        self._finish_recording_import()


def test_discovered_paths_go_straight_to_inspection_without_a_second_walk(app, tmp_path, monkeypatch):
    source = InventorySource(tmp_path / "capture.mcap")
    window = ImportHarness()

    def redundant_walk(*_args, **_kwargs):
        raise AssertionError("The discovered inventory must not be walked again")

    monkeypatch.setattr("ui.recording_session.discover_sources", redundant_walk)
    monkeypatch.setattr("ui.recording_session.open_source", lambda _path, **_kwargs: source)
    try:
        window._open_recording_sources([source.path], prompt=False, discovered=True)
        wait_for(app, lambda: not window._recording_importing and not window._recording_jobs)
        assert window.indexed[0] == [source]
        assert window.indexed[1]["sources"] == {source.path: ["camera"]}
    finally:
        dispose(window, app)


def test_cancelled_inspection_does_not_publish_sources_or_leave_workers(app, tmp_path, monkeypatch):
    source = InventorySource(tmp_path / "capture.mcap")
    started = threading.Event()
    window = ImportHarness()

    def inspect(cancel=None):
        started.set()
        while not cancel():
            time.sleep(0.001)
        check_cancel(cancel)

    source.inspect = inspect
    monkeypatch.setattr("ui.recording_session.open_source", lambda _path, **_kwargs: source)
    try:
        window._open_recording_sources([source.path], prompt=False, discovered=True)
        wait_for(app, started.is_set)
        for worker in window._recording_threads():
            worker.requestInterruption()
        wait_for(app, lambda: not window._recording_importing and not window._recording_jobs)
        assert window.indexed is None
        assert source.closed
        assert "取消" in window.status_task.text()
    finally:
        for worker in window._recording_threads():
            worker.requestInterruption()
        wait_for(app, lambda: not window._recording_jobs)
        dispose(window, app)


def test_import_waits_for_export_to_finish_before_opening_sources(app, tmp_path, monkeypatch):
    source = InventorySource(tmp_path / "capture.mcap")
    window = ImportHarness()
    window.recording_browser._exporting = True
    monkeypatch.setattr("ui.recording_session.open_source", lambda _path, **_kwargs: source)
    try:
        window._open_recording_sources([source.path], prompt=False, discovered=True)
        assert len(window._recording_queue) == 1
        assert not window._recording_jobs
        assert window.indexed is None
        window.recording_browser._exporting = False
        wait_for(app, lambda: window.indexed is not None and not window._recording_jobs)
        assert not window._recording_queue
    finally:
        window._closing = True
        dispose(window, app)


def test_large_inventory_uses_file_level_rows_and_expands_search_without_selection_changes(app, tmp_path):
    sources = [InventorySource(tmp_path / f"capture{index}.mcap") for index in range(12)]
    dialog = SourceOverviewDialog(sources)
    try:
        before = dialog.selection()
        assert all(not dialog.tree.topLevelItem(i).isExpanded() for i in range(12))
        dialog.tree.topLevelItem(0).setExpanded(True)
        dialog.search.setText("capture10")
        root = dialog.tree.topLevelItem(10)
        assert not root.isHidden()
        assert root.isExpanded()
        dialog.search.clear()
        assert dialog.tree.topLevelItem(0).isExpanded()
        assert not root.isExpanded()
        assert dialog.selection() == before
    finally:
        dispose(dialog, app)


def test_kind_summary_distinguishes_known_counts_from_pending_counts(app, tmp_path):
    dialog = SourceOverviewDialog([
        InventorySource(tmp_path / "known.mcap", 12),
        InventorySource(tmp_path / "unknown.mcap", None),
    ])
    try:
        assert "图像" in dialog.kind_summary.text()
        assert "12" in dialog.kind_summary.text()
        assert "1 个待统计" in dialog.kind_summary.text()
        before = dialog.selection()
        dialog.resize(900, 580)
        dialog.show()
        dialog.details_toggle.click()
        app.processEvents()
        assert dialog.minimumSizeHint().width() <= 960
        assert dialog.minimumSizeHint().height() <= 620
        assert dialog.selection() == before
    finally:
        dispose(dialog, app)
