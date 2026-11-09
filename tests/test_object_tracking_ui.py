"""Annotation control uses original indices and commits only current worker batches."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
import time

import numpy as np
import pytest
from PySide6.QtCore import QObject, QPersistentModelIndex, Qt, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QSpinBox, QWidget

from core.io.recording_types import FrameData, FrameRef, RecordingSource, StreamInfo
from core.model.entities import CloudEntity
from core.object_tracking import Box3D, Observation, ObjectTrack
from ui.widgets.recording_browser import BrowserStream


class Viewport(QObject):
    selection_changed = Signal(object)
    mode_changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.boxes = []
        self.mode = None

    def set_annotation_boxes(self, boxes):
        self.boxes = boxes

    def set_interaction_mode(self, mode):
        self.mode = mode
        self.mode_changed.emit(mode or "")


class MemorySource(RecordingSource):
    def __init__(self, indices=(10, 11, 12), token=None):
        super().__init__(os.path.abspath("tracking-memory.pcap"))
        self.token = token
        self.unavailable = False
        self.reads = []
        rng = np.random.default_rng(41)
        points = rng.uniform(-0.4, 0.4, (100, 3))
        self.frames = {i: FrameData("pointcloud", CloudEntity(str(i), xyz=points + [n * .1, 0, 0]),
                                    record_time_ns=n * 100_000_000)
                       for n, i in enumerate(indices)}
        self.index = {"cloud": [FrameRef(i, None, n * 100_000_000) for n, i in enumerate(indices)]}

    def cache_token(self):
        if self.unavailable:
            raise OSError("source disconnected")
        return self.token

    def read_frame(self, stream_id, index, cancel=None):
        self.reads.append(index)
        return self.frames[index]


class Browser(QWidget):
    def __init__(self, source):
        super().__init__()
        self.entries = [BrowserStream(source, StreamInfo("cloud", "cloud", "pointcloud"), source.index["cloud"])]
        self.primary = QComboBox(self)
        self.primary.addItem("cloud", self.entries[0].key)
        self.position = QSpinBox(self)
        self.position.setRange(1, len(self.entries[0].frames))
        self.position.setValue(1)
        self.current_frames = [source.frames[self.entries[0].frames[0].index]]
        self._displayed_indices = [self.entries[0].frames[0].index, None]
        self.left = SimpleNamespace(cloud_view=Viewport())
        self._worker = None
        self._pending = None
        self.play = QPushButton(self)
        self.play.setCheckable(True)

    def entry(self, key):
        return next((entry for entry in self.entries if entry.key == key), None)


@pytest.fixture
def controller():
    from ui.widgets.object_tracking import ObjectTrackingController
    app = QApplication.instance() or QApplication([])
    browser = Browser(MemorySource())
    control = ObjectTrackingController(browser)
    control.set_entries(browser.entries)
    control.on_frame_changed(browser.current_frames[0])
    yield control, browser, app
    control.stop()
    browser.close()
    browser.deleteLater()
    app.processEvents()


def select_target(control, browser):
    control.set_mode("box")
    cloud = browser.current_frames[0].value
    browser.left.cloud_view.selection_changed.emit([(cloud, np.arange(len(cloud.xyz)))])
    return control.source_state(browser.entries[0].source.path)["streams"][0]["tracks"][0]


def wait_idle(control, app):
    deadline = time.monotonic() + 10
    while control.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.005)
    assert not control.busy


def test_selection_creates_manual_original_index_and_reframe_keeps_id(controller):
    control, browser, _ = controller
    track = select_target(control, browser)
    assert track["observations"][0]["frame_index"] == 10
    assert track["observations"][0]["manual"] is True
    control.set_mode("box", reframe=True)
    cloud = browser.current_frames[0].value
    browser.left.cloud_view.selection_changed.emit([(cloud, np.arange(20))])
    restored = control.source_state(browser.entries[0].source.path)["streams"][0]["tracks"]
    assert len(restored) == 1
    assert restored[0]["id"] == track["id"]
    assert browser.left.cloud_view.boxes[0]["selected"]


def test_invalidate_rejects_stale_selection_and_preserves_annotations(controller):
    control, browser, _ = controller
    select_target(control, browser)
    control.set_mode("box", reframe=True)
    control.invalidate_frame()
    browser.left.cloud_view.selection_changed.emit([(browser.current_frames[0].value, np.arange(20))])
    assert control.has_annotations
    assert browser.left.cloud_view.boxes == []
    assert browser.left.cloud_view.mode is None


def test_range_reduction_and_unselected_stream_preserve_annotations(controller):
    control, browser, _ = controller
    select_target(control, browser)
    entry = browser.entries[0]
    control.set_entries([BrowserStream(entry.source, entry.stream, entry.frames[1:])])
    control.set_entries([])
    assert control.source_state(entry.source.path)["streams"][0]["tracks"][0]["observations"][0]["frame_index"] == 10
    control.remove_source(entry.source.path)
    assert not control.has_annotations


def test_restore_is_path_independent_and_changed_signature_invalidates(controller):
    control, browser, _ = controller
    source = browser.entries[0].source
    source.token = ("C:/old/source.mcap", 7, (("C:/old/source.mcap", 42, 99),))
    browser.entries[0].cache_identity = source.token
    control.set_entries(browser.entries)
    select_target(control, browser)
    payload = control.source_state(source.path)
    control.clear()
    source.token = ("D:/new/source.mcap", 7, (("D:/new/source.mcap", 42, 99),))
    control.restore_source(source.path, payload)
    assert control.has_annotations
    notices = []
    control.notice.connect(notices.append)
    source.token = ("D:/new/source.mcap", 7, (("D:/new/source.mcap", 43, 99),))
    control.set_entries(browser.entries)
    assert not control.has_annotations
    assert notices


def test_tracking_reads_adjacent_directly_and_preserves_manual_anchor(controller):
    control, browser, app = controller
    track = select_target(control, browser)
    payload = control.source_state(browser.entries[0].source.path)
    anchor = Observation(11, Box3D((.1, 0, 0), (.8, .8, .8)), True)
    payload["streams"][0]["tracks"][0]["observations"].append(anchor.to_dict())
    control.restore_source(browser.entries[0].source.path, payload)
    assert control.start_tracking("forward", 2, 2.)
    wait_idle(control, app)
    result = control.source_state(browser.entries[0].source.path)["streams"][0]["tracks"][0]
    observations = {item["frame_index"]: item for item in result["observations"]}
    assert observations[11] == anchor.to_dict()
    assert observations[12]["manual"] is False
    assert browser.entries[0].source.reads == [11, 12]


def test_cancel_discards_batch_and_refuses_conflicting_reader(controller):
    control, browser, app = controller
    select_target(control, browser)
    browser._pending = object()
    assert not control.start_tracking("forward", 2)
    browser._pending = None
    assert control.start_tracking("forward", 2)
    control.cancel()
    wait_idle(control, app)
    track = control.source_state(browser.entries[0].source.path)["streams"][0]["tracks"][0]
    assert len(track["observations"]) == 1


def test_dialog_manual_editor_and_deletions(controller):
    control, browser, _ = controller
    select_target(control, browser)
    control.show()
    assert control.dialog is not None
    assert control.dialog.count.value() == 5
    assert control.dialog.count.maximum() == 100
    control.dialog.center[0].setValue(2.)
    control.dialog.yaw.setValue(90.)
    control.apply_manual_edit()
    track = control.source_state(browser.entries[0].source.path)["streams"][0]["tracks"][0]
    assert track["observations"][0]["box"]["center"][0] == 2.
    assert np.isclose(track["observations"][0]["box"]["yaw"], np.pi / 2)
    control.delete_frame()
    assert not browser.left.cloud_view.boxes
    control.delete_target()
    assert not control.has_annotations


def test_insufficient_finite_selection_does_not_create_target(controller):
    control, browser, _ = controller
    cloud = browser.current_frames[0].value
    cloud.xyz[0] = np.nan
    control.set_mode("box")
    browser.left.cloud_view.selection_changed.emit([(cloud, np.arange(6))])
    assert not control.has_annotations
    assert browser.left.cloud_view.mode == "box"
    browser.left.cloud_view.selection_changed.emit([(cloud, np.arange(1, 8))])
    assert control.has_annotations


@pytest.mark.parametrize("gap", ["index", "time"])
def test_tracking_stops_before_nonadjacent_frame(controller, gap):
    control, browser, app = controller
    select_target(control, browser)
    entry = browser.entries[0]
    if gap == "index":
        entry.frames[1] = FrameRef(12, None, 100_000_000)
    else:
        entry.frames[1] = FrameRef(11, None, 1_000_000_001)
    assert control.start_tracking("forward", 2)
    wait_idle(control, app)
    assert entry.source.reads == []
    assert len(control.source_state(entry.source.path)["streams"][0]["tracks"][0]["observations"]) == 1


def test_both_directions_seed_current_frame_and_use_original_indices(controller):
    control, browser, app = controller
    entry = browser.entries[0]
    browser.position.setValue(2)
    browser.current_frames = [entry.source.frames[11]]
    browser._displayed_indices[0] = 11
    control.on_frame_changed(browser.current_frames[0])
    select_target(control, browser)
    assert control.start_tracking("both", 1)
    wait_idle(control, app)
    track = control.source_state(entry.source.path)["streams"][0]["tracks"][0]
    assert [item["frame_index"] for item in track["observations"]] == [10, 11, 12]
    assert entry.source.reads == [12, 10]
    assert track["observations"][1]["manual"]


def test_source_token_change_discards_completed_worker_batch(controller):
    control, browser, app = controller
    select_target(control, browser)
    source = browser.entries[0].source
    assert control.start_tracking("forward", 1)
    control.worker.wait()
    source.token = "changed before queued result"
    wait_idle(control, app)
    track = control.source_state(source.path)["streams"][0]["tracks"][0]
    assert len(track["observations"]) == 1


def test_export_is_atomic_versioned_json_in_source_coordinates(controller, tmp_path):
    import json
    control, browser, _ = controller
    select_target(control, browser)
    destination = tmp_path / "目标.json"
    assert control.export_json(destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["units"]["yaw"] == "radians"
    assert payload["sources"][0]["path"] == os.path.normcase(browser.entries[0].source.path)
    assert payload["sources"][0]["streams"][0]["tracks"][0]["observations"][0]["manual"]


def test_manual_edit_cannot_create_frame_from_stale_editor_values(controller):
    control, browser, _ = controller
    select_target(control, browser)
    control.show()
    browser.position.setValue(2)
    browser.current_frames = [browser.entries[0].source.frames[11]]
    browser._displayed_indices[0] = 11
    control.on_frame_changed(browser.current_frames[0])
    control.apply_manual_edit()
    track = control.source_state(browser.entries[0].source.path)["streams"][0]["tracks"][0]
    assert [item["frame_index"] for item in track["observations"]] == [10]


def test_frame_invalidation_cancels_tracking_and_discards_queued_result(controller):
    control, browser, app = controller
    select_target(control, browser)
    assert control.start_tracking("forward", 1)
    control.worker.wait()
    control.invalidate_frame()
    wait_idle(control, app)
    track = control.source_state(browser.entries[0].source.path)["streams"][0]["tracks"][0]
    assert len(track["observations"]) == 1


def test_escape_disarms_selection_and_later_selection_cannot_create_target(controller):
    control, browser, _ = controller
    control.set_mode("box")
    browser.left.cloud_view.set_interaction_mode(None)
    cloud = browser.current_frames[0].value
    browser.left.cloud_view.selection_changed.emit([(cloud, np.arange(20))])
    assert not control.has_annotations


def test_legacy_preview_without_annotation_api_remains_usable(controller):
    control, browser, _ = controller
    browser.left.cloud_view = QObject()
    control.on_frame_changed(browser.current_frames[0])
    control.invalidate_frame()
    control.on_frame_changed(browser.current_frames[0])
    assert not control.set_mode("box")


def test_failed_match_status_uses_chinese_reason(controller):
    control, browser, app = controller
    select_target(control, browser)
    browser.entries[0].source.frames[11].value.xyz = np.empty((0, 3))
    messages = []
    control.notice.connect(messages.append)
    assert control.start_tracking("forward", 1)
    wait_idle(control, app)
    assert "insufficient_current_points" not in messages[-1]
    assert "点数不足" in messages[-1]


def test_tracking_progress_is_visible_and_cancellation_drops_late_updates(controller):
    import threading
    control, browser, app = controller
    select_target(control, browser)
    control.show()
    source = browser.entries[0].source
    original_read = source.read_frame
    blocked = threading.Event()

    def read(stream_id, index, cancel=None):
        if index == 12:
            blocked.set()
            while not cancel():
                time.sleep(.002)
        return original_read(stream_id, index, cancel)

    source.read_frame = read
    try:
        assert control.start_tracking("forward", 2)
        deadline = time.monotonic() + 5
        while not blocked.is_set() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
        assert blocked.is_set()
        app.processEvents()
        assert control.dialog.progress.isVisible()
        assert control.dialog.progress.value() == 500
        control.cancel()
        wait_idle(control, app)
        assert not control.dialog.progress.isVisible()
        assert control.dialog.status.isVisible()
        assert "已取消" in control.dialog.status.text()
        assert sorted(control._track().observations) == [10]
    finally:
        control.cancel()
        wait_idle(control, app)


def test_match_failure_names_the_frame_and_keeps_recovery_message_visible(controller):
    control, browser, app = controller
    select_target(control, browser)
    control.show()
    browser.entries[0].source.frames[11].value.xyz = np.empty((0, 3))
    assert control.start_tracking("forward", 1)
    wait_idle(control, app)
    assert control.dialog.status.isVisible()
    message = control.dialog.status.text()
    assert "原始帧 12" in message
    assert "点数不足" in message
    assert "重框" in message
    assert sorted(control._track().observations) == [10]


def test_overlay_uses_compact_target_id(controller):
    control, browser, _ = controller
    track = select_target(control, browser)
    assert browser.left.cloud_view.boxes[0]["id"] == track["id"][:8]


def test_changed_source_invalidates_unselected_sibling_stream_annotations(controller):
    control, browser, _ = controller
    select_target(control, browser)
    entry = browser.entries[0]
    sibling = BrowserStream(entry.source, StreamInfo("other", "other", "pointcloud"), entry.frames)
    control.set_entries([sibling])
    assert control.has_annotations
    entry.source.token = "content changed"
    control.set_entries([sibling])
    assert not control.has_annotations


def test_restore_validates_unselected_stream_against_same_source(controller):
    control, browser, _ = controller
    select_target(control, browser)
    entry = browser.entries[0]
    payload = control.source_state(entry.source.path)
    control.clear()
    sibling = BrowserStream(entry.source, StreamInfo("other", "other", "pointcloud"), entry.frames)
    entry.source.token = "content changed"
    control.set_entries([sibling])
    control.restore_source(entry.source.path, payload)
    assert not control.has_annotations


def test_cancel_status_finishes_after_worker_becomes_idle(controller):
    control, browser, app = controller
    select_target(control, browser)
    messages = []
    control.notice.connect(messages.append)
    assert control.start_tracking("forward", 2)
    control.cancel()
    wait_idle(control, app)
    assert messages[-1] == "已取消关联。"


def test_replaced_source_cannot_arm_selection_on_displayed_old_frame(controller):
    control, browser, _ = controller
    browser.entries[0].source.token = "replaced source"
    messages = []
    control.notice.connect(messages.append)
    assert not control.set_mode("box")
    assert not control.has_annotations
    assert "重新载入" in messages[-1]


def test_source_replacement_during_selection_cannot_bind_old_points_to_new_signature(controller):
    control, browser, _ = controller
    assert control.set_mode("box")
    browser.entries[0].source.token = "replaced source"
    cloud = browser.current_frames[0].value
    control._selection_changed([(cloud, np.arange(20))])
    assert not control.has_annotations
    assert browser.left.cloud_view.mode is None


@pytest.mark.parametrize("operation", ["manual_edit", "delete_frame", "rename", "delete_target"])
def test_replaced_source_blocks_annotation_writes_from_old_frame(controller, operation):
    control, browser, _ = controller
    select_target(control, browser)
    control.show()
    source = browser.entries[0].source
    original = control.source_state(source.path)
    control.dialog.center[0].setValue(9.)
    control.dialog.name.setText("stale edit")
    source.token = "replaced source"
    callbacks = {"manual_edit": control.apply_manual_edit, "delete_frame": control.delete_frame,
                 "rename": control.rename_target, "delete_target": control.delete_target}
    callbacks[operation]()
    assert control.source_state(source.path) == original


def test_unavailable_source_does_not_block_other_sources_and_retains_annotations(controller):
    control, browser, _ = controller
    select_target(control, browser)
    entry = browser.entries[0]
    original = control.source_state(entry.source.path)
    available = MemorySource()
    available.path = os.path.abspath("available-tracking.mcap")
    available_entry = BrowserStream(available, entry.stream, available.index["cloud"])
    browser.entries.append(available_entry)
    entry.source.unavailable = True
    messages = []
    control.notice.connect(messages.append)
    control.set_entries(browser.entries)
    assert control.source_state(entry.source.path) == original
    assert messages and "不可用" in messages[-1]
    control.show()
    assert not control.dialog.new_button.isEnabled()
    assert not control.dialog.apply_button.isEnabled()
    assert not control.dialog.track_button.isEnabled()
    assert not control.dialog.delete_target_button.isEnabled()
    assert not control.start_tracking()
    browser.primary.addItem("available", available_entry.key)
    browser.primary.setCurrentIndex(1)
    browser.current_frames = [available.frames[10]]
    control.on_frame_changed(browser.current_frames[0])
    select_target(control, browser)
    assert control.source_state(available.path)["streams"][0]["tracks"]
    entry.source.unavailable = False
    control.set_entries(browser.entries)
    assert control.source_state(entry.source.path) == original
    entry.source.token = "changed while disconnected"
    control.set_entries(browser.entries)
    assert control.source_state(entry.source.path)["streams"] == []
    assert control.source_state(available.path)["streams"][0]["tracks"]


def test_unavailable_source_restore_retains_payload_until_validation_becomes_possible(controller):
    control, browser, _ = controller
    select_target(control, browser)
    source = browser.entries[0].source
    payload = control.source_state(source.path)
    control.clear()
    source.unavailable = True
    control.restore_source(source.path, payload)
    assert control.source_state(source.path) == payload
    source.unavailable = False
    source.token = "changed source"
    control.set_entries(browser.entries)
    assert not control.has_annotations


def test_late_batch_discards_unavailable_source_without_raising(controller):
    control, browser, _ = controller
    track = select_target(control, browser)
    entry = browser.entries[0]
    observation = Observation(11, Box3D((.1, 0, 0), (.8, .8, .8)), False, .9)
    result = (control._generation, control._key(entry), entry.source, entry.cache_identity,
              track["id"], {11: observation}, "finished")
    entry.source.unavailable = True
    control._apply_batch(result)
    annotations = control.source_state(entry.source.path)["streams"][0]["tracks"][0]["observations"]
    assert len(annotations) == 1


def test_missing_file_none_token_preserves_annotations_but_blocks_edits(controller):
    control, browser, _ = controller
    source = browser.entries[0].source
    source.token = (42, 99)
    browser.entries[0].cache_identity = source.token
    select_target(control, browser)
    original = control.source_state(source.path)
    source.token = None
    control.set_entries(browser.entries)
    assert control.source_state(source.path) == original
    assert not control.set_mode("box", reframe=True)
    assert not control.start_tracking()
    source.token = (42, 99)
    control.set_entries(browser.entries)
    assert control.source_state(source.path) == original


def test_source_replaced_during_box_fit_cannot_commit_old_points(controller, monkeypatch):
    from ui.widgets import object_tracking
    control, browser, _ = controller
    original_fit = object_tracking.fit_box

    def fit_after_replacement(points):
        browser.entries[0].source.token = "replaced during fitting"
        return original_fit(points)

    monkeypatch.setattr(object_tracking, "fit_box", fit_after_replacement)
    control.set_mode("box")
    cloud = browser.current_frames[0].value
    control._selection_changed([(cloud, np.arange(20))])
    assert not control.has_annotations


def test_three_view_editor_and_xyz_controls_are_available(controller):
    control, _, app = controller
    control.show()
    app.processEvents()
    assert set(control.dialog.editor.views) == {"top", "front", "side"}
    for editor in control.dialog.center + control.dialog.size:
        assert editor.minimumWidth() >= 64
        assert editor.maximumWidth() <= 110


def test_three_view_edit_commits_full_orientation_as_manual_anchor(controller):
    control, browser, _ = controller
    select_target(control, browser)
    control.show()
    box = Box3D((1, 2, 3), (2, 3, 4), .4, .2, -.3)
    control.dialog.editor.box_edited.emit(box)
    saved = control._track().observations[10]
    assert saved.box == box
    assert saved.manual
    assert control.dialog.editor.box == box
    payload = control.source_state(browser.entries[0].source.path)
    assert payload["streams"][0]["tracks"][0]["observations"][0]["box"]["roll"] == .2


def test_three_view_edit_is_disabled_and_rejected_during_frame_change(controller):
    control, browser, _ = controller
    select_target(control, browser)
    control.show()
    old = control._track().observations[10]
    control.invalidate_frame()
    assert not control.dialog.editor.isEnabled()
    control.dialog.editor.box_edited.emit(Box3D((99, 0, 0), (1, 1, 1)))
    assert control._track().observations[10] == old


@pytest.mark.parametrize("reframe", [False, True])
def test_selection_temporarily_hides_dialog_and_success_restores_it(controller, reframe):
    control, browser, _ = controller
    if reframe:
        select_target(control, browser)
    control.show()
    button = control.dialog.reframe_button if reframe else control.dialog.new_button
    button.click()
    assert not control.dialog.isVisible()
    cloud = browser.current_frames[0].value
    browser.left.cloud_view.selection_changed.emit([(cloud, np.arange(20))])
    assert control.dialog.isVisible()
    assert control.has_annotations


def test_escape_restores_dialog_after_cancelled_selection(controller):
    control, browser, _ = controller
    control.show()
    control.dialog.new_button.click()
    assert not control.dialog.isVisible()
    browser.left.cloud_view.set_interaction_mode(None)
    assert control.dialog.isVisible()
    assert not control.has_annotations


def test_explicitly_closed_dialog_does_not_reopen_after_selection_ends(controller):
    control, browser, _ = controller
    control.show()
    control.dialog.new_button.click()
    control.dialog.close()
    browser.left.cloud_view.set_interaction_mode(None)
    assert not control.dialog.isVisible()
    assert not control.has_annotations


def test_selection_started_without_visible_panel_does_not_open_it_on_success(controller):
    control, browser, _ = controller
    control.show()
    control.dialog.close()
    select_target(control, browser)
    assert not control.dialog.isVisible()


def test_visible_boxes_signal_matches_viewport_through_edit_delete_and_invalidation(controller):
    control, browser, _ = controller
    updates = []
    control.boxes_changed.connect(updates.append)
    select_target(control, browser)
    assert updates[-1] is browser.left.cloud_view.boxes
    assert updates[-1][0]["manual"]
    assert control.apply_box_edit(Box3D((2, 1, 0), (2, 3, 4)))
    assert updates[-1] is browser.left.cloud_view.boxes
    assert updates[-1][0]["center"] == [2, 1, 0]
    control.invalidate_frame()
    assert updates[-1] == browser.left.cloud_view.boxes == []
    control.on_frame_changed(browser.current_frames[0])
    assert len(updates[-1]) == 1
    control.delete_frame()
    assert updates[-1] == []
    select_target(control, browser)
    control.clear()
    assert updates[-1] == []


def test_visible_boxes_signal_clears_without_a_cloud_viewport(controller):
    control, browser, _ = controller
    updates = []
    control.boxes_changed.connect(updates.append)
    select_target(control, browser)
    browser.left.cloud_view = QObject()
    control.invalidate_frame()
    assert updates[-1] == []


def test_geometry_edits_and_renames_preserve_result_and_target_rows(controller):
    control, browser, _ = controller
    select_target(control, browser)
    control.show()
    dialog = control.dialog
    target_row = QPersistentModelIndex(dialog.targets.model().index(0, 0))
    result_row = QPersistentModelIndex(dialog.result_frames.model().index(0, 0))
    control._track().observations[10] = Observation(10, control._track().observations[10].box, False, .9)
    control._refresh()
    assert result_row.isValid()
    assert "自动" in result_row.data()
    assert control.apply_box_edit(Box3D((1, 0, 0), (2, 2, 2)))
    assert target_row.isValid() and result_row.isValid()
    assert "人工" in result_row.data()
    dialog.name.setText("renamed")
    control.rename_target()
    assert target_row.isValid() and "renamed" in target_row.data()
    assert result_row.isValid()
    control.delete_frame()
    assert not result_row.isValid()
    assert target_row.isValid()


def test_box_edit_reuses_frame_index_and_filtered_range_refreshes_navigation(controller):
    class CountedFrames(list):
        iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    control, browser, _ = controller
    entry = browser.entries[0]
    entry.frames = CountedFrames(entry.frames)
    select_target(control, browser)
    track = control._track()
    track.observations[12] = Observation(12, Box3D((.2, 0, 0), (1, 1, 1)))
    control.show()
    before = entry.frames.iterations
    for number in range(10):
        control.apply_box_edit(Box3D((number * .01, 0, 0), (1, 1, 1)))
    assert entry.frames.iterations == before
    entry.frames = CountedFrames(entry.frames[1:])
    browser.position.setRange(1, 2)
    browser.current_frames = [entry.source.frames[11]]
    browser._displayed_indices[0] = 11
    control.set_entries(browser.entries)
    control.on_frame_changed(browser.current_frames[0])
    assert control.dialog.result_frames.count() == 1
    assert control.dialog.result_frames.itemData(0) == 12
    assert control.navigate_result(1)
    assert browser.position.value() == 2


def test_editor_shortcuts_browse_frames_and_results_without_changing_box(controller):
    control, browser, app = controller
    select_target(control, browser)
    original = control._track().observations[10]
    control._track().observations[12] = Observation(12, original.box, False, .9)
    control.show()
    control.dialog.activateWindow()
    view = control.dialog.editor.views["top"]
    view.setFocus()
    app.processEvents()
    QTest.keyClick(view, Qt.Key_PageDown, Qt.ControlModifier)
    assert browser.position.value() == 3
    assert control._track().observations[10] == original
    browser.position.setValue(1)
    browser._pending = object()
    QTest.keyClick(view, Qt.Key_PageDown, Qt.ControlModifier)
    assert browser.position.value() == 1
    browser._pending = None
    QTest.keyClick(view, Qt.Key_PageDown)
    assert browser.position.value() == 2


def test_numeric_edit_and_rename_publish_one_coherent_update(controller):
    control, browser, _ = controller
    select_target(control, browser)
    control.show()
    updates, changes = [], []
    control.boxes_changed.connect(updates.append)
    control.changed.connect(lambda: changes.append(True))
    control.dialog.name.setText("corrected")
    control.dialog.center[0].setValue(3.)
    control.apply_manual_edit()
    assert len(updates) == len(changes) == 1
    assert updates[0][0]["name"] == "corrected"
    assert updates[0][0]["center"][0] == 3.


def test_review_navigation_does_not_steal_focus_after_user_moves_to_settings(controller):
    control, browser, app = controller
    select_target(control, browser)
    control._track().observations[12] = Observation(12, control._track().observations[10].box)
    control.show()
    control.dialog.activateWindow()
    view = control.dialog.editor.views["top"]
    view.setFocus()
    app.processEvents()
    browser.position.valueChanged.connect(lambda: control.invalidate_frame())
    assert control.navigate_result(1)
    focus = QApplication.focusWidget()
    settings = (control.dialog.associate_button if focus is control.dialog.parameters_button
                else control.dialog.parameters_button)
    settings.setFocus()
    browser.current_frames = [browser.entries[0].source.frames[12]]
    browser._displayed_indices[0] = 12
    control.on_frame_changed(browser.current_frames[0])
    assert settings.hasFocus()
    assert not view.hasFocus()
