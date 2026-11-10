"""Editable source-coordinate targets and cancellable adjacent-frame suggestions."""

from copy import deepcopy
from contextlib import nullcontext
import json
import math
import os
from pathlib import Path
import re
from uuid import uuid4

import numpy as np
from PySide6.QtCore import QIODevice, QObject, QSaveFile, Qt, Signal
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from core.io.recording_types import FrameData
from core.box_extraction import extract_box, extract_track, track_output_paths
from core.model.entities import CloudEntity
from core.object_tracking import Box3D, ObjectTrack, Observation, box_corners, fit_box, track_step
from ui.recording_tasks import RecordingTask


_MATCH_REASONS = {
    "cancelled": "已取消",
    "insufficient_previous_points": "上一帧框内有效点数不足",
    "insufficient_current_points": "当前帧附近有效点数不足",
    "poor_match": "几何匹配质量不足，可能遮挡或丢失目标",
    "displacement_exceeded": "目标位移超过设定上限",
}


def _path_key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _signature(source, token):
    """Persist content identity without the absolute paths in reader cache tokens."""
    if isinstance(token, (tuple, list)) and len(token) == 3 and isinstance(token[0], str):
        if isinstance(token[2], (tuple, list)):
            token = {"parser": token[1], "files": [list(item[1:]) for item in token[2]]}
        else:
            token = list(token[1:])
    # PCAP inherits the size/mtime token. Include its parser and decoding settings.
    if type(source).__module__ == "core.io.pcap_source":
        from core.io.pcap_source import PARSER_VERSION
        token = {"files": token, "parser": PARSER_VERSION,
                 "drop_noise": source.drop_noise, "min_range_m": source.min_range_m}
    return json.loads(json.dumps(token))


from ui.widgets.box_panel import ObjectTrackingDialog


class ObjectTrackingController(QObject):
    changed = Signal()
    boxes_changed = Signal(object)
    notice = Signal(str)
    busy_changed = Signal(bool)
    idle = Signal()
    panel_visibility_changed = Signal(bool)
    cloud_extracted = Signal(object)

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.dialog = None
        self.worker = None
        self.job_label = "关联"
        self._committed_extraction = None
        self.selected_id = None
        self._streams = {}
        self._entries = {}
        self._generation = 0
        self._armed = None
        self._selection_dialog_hidden = False
        self._ready = False
        self._viewports = set()
        self._message = ""
        self._editor_identity = None
        self._frame_positions = None
        self._navigation_focus = None

    @property
    def busy(self):
        return self.worker is not None

    @property
    def has_annotations(self):
        return any(stream["tracks"] for stream in self._streams.values())

    def _entry(self):
        return self.browser.entry(self.browser.primary.currentData())

    def _frame_loading(self):
        worker = self.browser._worker
        generation = getattr(self.browser, "_generation", None)
        return (self.browser._pending is not None
                or (worker is not None and not getattr(worker, "background", False)
                    and getattr(worker, "request_generation", generation) == generation))

    def refresh_reader_actions(self):
        """Source-reading jobs share the reader; current-frame edits need not wait."""
        if self.dialog is None:
            return
        current, track = self._current(), self._track()
        readable = (not self.busy and self._armed is None and self.browser._worker is None
                    and self.browser._pending is None and current is not None and track is not None
                    and self._source_matches(current[0], notify=False))
        self.dialog.track_button.setEnabled(readable and current[2] in track.observations)
        self.dialog.batch_extract_action.setEnabled(readable and bool(track.observations))

    @staticmethod
    def _key(entry):
        return _path_key(entry.source.path), entry.stream.id

    def _current(self):
        entry = self._entry()
        frames = self.browser.current_frames
        indices = self.browser._displayed_indices
        if not self._ready or entry is None or entry.stream.kind != "pointcloud" or not frames:
            return None
        frame = frames[0]
        position = self.browser.position.value() - 1
        if (not isinstance(frame, FrameData) or frame.kind != "pointcloud"
                or indices[0] is None or not 0 <= position < len(entry.frames)
                or indices[0] != entry.frames[position].index):
            return None
        return entry, frame, indices[0]

    def _tracks(self):
        entry = self._entry()
        stream = self._streams.get(self._key(entry)) if entry else None
        return stream["tracks"] if stream else {}

    def _track(self):
        return self._tracks().get(self.selected_id)

    def _source_matches(self, entry, *, notify=True):
        if entry is None:
            return False
        try:
            token = entry.source.cache_token()
        except OSError:
            message = "源文件暂时不可用，请恢复连接并重新载入后编辑或关联。"
        else:
            if token == entry.cache_identity:
                return True
            message = "源文件已更改，请重新载入点云后编辑或关联。"
            if token is None:
                message = "源文件暂时不可用，请恢复连接并重新载入后编辑或关联。"
        if notify:
            self._disarm()
            self._status(message)
        return False

    @staticmethod
    def _entry_signature(entry):
        token = entry.source.cache_token()
        # File readers return None when stat fails. In-memory sources also use
        # None, with a matching None cache identity established on creation.
        if token is None and entry.cache_identity is not None:
            raise OSError("源文件不可用")
        return _signature(entry.source, token)

    def _status(self, text):
        self._message = text
        if self.dialog is not None:
            self.dialog.status.setText(text)
        self.notice.emit(text)

    def _viewport(self):
        viewport = self.browser.left.cloud_view
        if viewport is not None and all(hasattr(viewport, attribute) for attribute in (
                "selection_changed", "set_annotation_boxes", "set_interaction_mode")):
            return viewport
        return None

    def on_frame_changed(self, frame):
        self._disarm()
        self._ready = isinstance(frame, FrameData) and frame.kind == "pointcloud"
        viewport = self._viewport()
        if viewport is not None and viewport not in self._viewports:
            viewport.selection_changed.connect(self._selection_changed)
            if hasattr(viewport, "box_creation_requested"):
                viewport.box_input_enabled = True
                viewport.box_creation_requested.connect(self._start_box_from_view)
                viewport.annotation_selected.connect(self._select_box_from_view)
            if hasattr(viewport, "mode_changed"):
                viewport.mode_changed.connect(lambda mode, view=viewport: self._mode_changed(view, mode))
            self._viewports.add(viewport)
        self._refresh()
        self._restore_navigation_focus()

    def _start_box_from_view(self):
        self.show()
        self.set_mode("box")

    def _select_box_from_view(self, target_id):
        self.select_target(target_id)
        self.show()

    def _mode_changed(self, viewport, mode):
        if self._armed is not None and self._armed[3] is viewport and mode != "box":
            self._armed = None
            self._restore_selection_dialog()
            self._refresh_dialog()

    def invalidate_frame(self):
        if self.busy:
            self.cancel()
        self._ready = False
        self._disarm()
        viewport = self._viewport()
        boxes = []
        if viewport is not None:
            viewport.set_annotation_boxes(boxes)
        self.boxes_changed.emit(boxes)
        self._refresh_dialog()

    def _disarm(self):
        if self._armed is not None:
            viewport = self._armed[3]
            self._armed = None
            viewport.set_interaction_mode(None)
            if hasattr(viewport, "clear_selection_runtime"):
                viewport.clear_selection_runtime()
        self._restore_selection_dialog()

    def _restore_selection_dialog(self):
        if self._selection_dialog_hidden:
            self._selection_dialog_hidden = False
            self.show()

    def _dialog_finished(self):
        self._selection_dialog_hidden = False
        self.cancel()
        self.panel_visibility_changed.emit(False)

    def set_entries(self, entries):
        self.cancel()
        self._frame_positions = None
        self._entries = {self._key(entry): entry for entry in entries}
        sources = {key[0]: entry for key, entry in self._entries.items()}
        signatures = {}
        unavailable = []
        for path, entry in sources.items():
            try:
                signatures[path] = self._entry_signature(entry)
            except OSError:
                unavailable.append(os.path.basename(entry.source.path))
        invalidated = False
        for key, stream in list(self._streams.items()):
            if key[0] in signatures and stream["signature"] != signatures[key[0]]:
                invalidated |= bool(stream["tracks"])
                self._streams.pop(key)
        if invalidated:
            self._status("源文件或解析设置已更改，已清除该源的目标标注，请重新框选。")
            self.changed.emit()
        if unavailable:
            self._status(f"源文件暂时不可用（{'、'.join(unavailable)}），恢复连接并重新载入后可编辑或关联。")
        self._refresh()

    def source_state(self, path):
        path = _path_key(path)
        return {"version": 1, "streams": [
            {"stream_id": stream_id, "signature": deepcopy(stream["signature"]),
             "tracks": [track.to_dict() for track in stream["tracks"].values()]}
            for (source_path, stream_id), stream in self._streams.items()
            if source_path == path and stream["tracks"]]}

    def restore_source(self, path, payload):
        self.cancel()
        path = _path_key(path)
        restored = {}
        invalidated = False
        entry = next((entry for key, entry in self._entries.items() if key[0] == path), None)
        signature_available = False
        current_signature = None
        if entry is not None:
            try:
                current_signature = self._entry_signature(entry)
                signature_available = True
            except OSError:
                self._status("源文件暂时不可用，请恢复连接并重新载入后编辑或关联。")
        try:
            if not isinstance(payload, dict) or payload.get("version") != 1:
                raise ValueError("不支持的标注版本")
            for item in payload.get("streams", []):
                stream_id = item["stream_id"]
                if not isinstance(stream_id, str) or not stream_id or (path, stream_id) in restored:
                    raise ValueError("目标流 ID 无效或重复")
                signature = item["signature"]
                if signature_available and current_signature != signature:
                    invalidated = True
                    continue
                tracks = {}
                for value in item.get("tracks", []):
                    track = ObjectTrack.from_dict(value)
                    if track.id in tracks:
                        raise ValueError("目标 ID 重复")
                    tracks[track.id] = track
                restored[(path, stream_id)] = {"signature": deepcopy(signature), "tracks": tracks}
        except (ValueError, KeyError, TypeError) as error:
            self._status(f"无法恢复目标标注  {error}")
            return
        self._streams = {key: value for key, value in self._streams.items() if key[0] != path}
        self._streams.update(restored)
        if invalidated:
            self._status("源文件或解析设置已更改，已跳过该源的目标标注。")
        self._refresh()

    def remove_source(self, path):
        self.cancel()
        self._frame_positions = None
        path = _path_key(path)
        had_annotations = any(key[0] == path and value["tracks"] for key, value in self._streams.items())
        self._streams = {key: value for key, value in self._streams.items() if key[0] != path}
        self._entries = {key: value for key, value in self._entries.items() if key[0] != path}
        self._refresh()
        if had_annotations:
            self.changed.emit()

    def clear(self):
        self.cancel()
        self._frame_positions = None
        had_annotations = self.has_annotations
        self._streams.clear()
        self.selected_id = None
        self._refresh()
        if had_annotations:
            self.changed.emit()

    def select_target(self, target_id):
        if target_id is not None and target_id not in self._tracks():
            target_id = next((key for key in self._tracks() if key[:8] == target_id), None)
        self.selected_id = target_id if target_id in self._tracks() else None
        self._refresh()

    def navigate(self, delta):
        current = self._current()
        if (current is None or self.busy or self._frame_loading()
                or not self._source_matches(current[0])):
            return False
        position = self.browser.position
        target = max(position.minimum(), min(position.maximum(), position.value() + delta))
        if target == position.value():
            return False
        self._navigate_to(target)
        return True

    def _navigate_to(self, position):
        view = next((view for view in self.dialog.editor.views.values() if view.hasFocus()), None) if self.dialog else None
        entry = self._entry()
        pending = {"view": view, "key": self._key(entry), "index": entry.frames[position - 1].index,
                   "target": self.selected_id, "requesting": True} if view is not None else None
        self._navigation_focus = pending
        self.browser.play.setChecked(False)
        self.browser.position.setValue(position)
        if pending is not None:
            # Disabling the editor while loading moves focus elsewhere. Restore
            # only that automatic move; a user's subsequent click takes priority.
            pending["fallback"] = QApplication.focusWidget()
            pending["requesting"] = False
            self._restore_navigation_focus()

    def _restore_navigation_focus(self):
        pending = self._navigation_focus
        if pending is None or pending["requesting"] or self._frame_loading():
            return
        current = self._current()
        if current is None:
            return
        self._navigation_focus = None
        if (self._key(current[0]) == pending["key"] and current[2] == pending["index"]
                and self.selected_id == pending["target"] and self.dialog.isVisible()
                and pending["view"].isEnabled() and self.dialog.window().isActiveWindow()
                and QApplication.focusWidget() is pending["fallback"]):
            pending["view"].setFocus(Qt.ShortcutFocusReason)

    def _result_positions(self):
        entry, track = self._entry(), self._track()
        if entry is None or track is None:
            return []
        # The import range may be filtered. Only navigate available frames and
        # keep original frame IDs separate from the playback slider positions.
        cached = self._frame_positions
        if (cached is None or cached[0] is not entry or cached[1] is not entry.frames
                or cached[2] != len(entry.frames)):
            # Browser range changes replace entries/frames and call set_entries.
            # Keep this source-index lookup across geometry-only box edits.
            positions = {ref.index: position for position, ref in enumerate(entry.frames, 1)}
            self._frame_positions = entry, entry.frames, len(entry.frames), positions
        else:
            positions = cached[3]
        return sorted((positions[index], index) for index in track.observations if index in positions)

    def show_result(self, index):
        current = self._current()
        if (current is None or self.busy or self._armed is not None
                or self._frame_loading()
                or not self._source_matches(current[0])):
            return False
        position = next((position for position, frame in self._result_positions() if frame == index), None)
        if position is None:
            return False
        self._navigate_to(position)
        return True

    def navigate_result(self, direction):
        position = self.browser.position.value()
        results = self._result_positions()
        if direction < 0:
            results = [item for item in results if item[0] < position]
        elif direction > 0:
            results = [item for item in results if item[0] > position]
        if not results:
            return False
        return self.show_result(min(results, key=lambda item: abs(item[0] - position))[1])

    def focus_target(self):
        current, track, viewport = self._current(), self._track(), self._viewport()
        if (current is None or track is None or viewport is None or self.busy
                or self._frame_loading()
                or not self._source_matches(current[0]) or not hasattr(viewport, "focus_entity")):
            return False
        observation = track.observations.get(current[2])
        if observation is None:
            return False
        viewport.focus_entity(CloudEntity("目标范围", xyz=box_corners(observation.box).astype(np.float32)))
        return True

    def set_mode(self, mode, *, reframe=False):
        if mode != "box":
            self._disarm()
            return False
        current = self._current()
        viewport = self._viewport()
        if self.busy or self._frame_loading() or current is None or viewport is None:
            self._status("请等待主视图点云读取完成后框选。")
            return False
        if reframe and self._track() is None:
            self._status("请先选择需要重框的目标。")
            return False
        if not self._source_matches(current[0]):
            return False
        self._disarm()
        entry, frame, index = current
        self._armed = (self._key(entry), index, frame.value, viewport, self.selected_id if reframe else None)
        viewport.set_interaction_mode("box")
        self._status("在主视图拖动矩形，框选至少 6 个有效点。")
        if self.dialog is not None and self.dialog.isVisible():
            # Hiding an embedded editor would resize the preview underneath
            # an in-progress Ctrl-drag and shift the projected target.
            if self.dialog.isWindow():
                self._selection_dialog_hidden = True
                self.dialog.hide()
            if hasattr(viewport, "setFocus"):
                viewport.setFocus(Qt.OtherFocusReason)
        self._refresh_dialog()
        return True

    def _selection_changed(self, selection):
        current = self._current()
        if self._armed is None or current is None or self.busy:
            return
        key, index, cloud, viewport, target_id = self._armed
        if self.sender() is not None and self.sender() is not viewport:
            return
        entry, frame, current_index = current
        if self._key(entry) != key or current_index != index or frame.value is not cloud or viewport is not self._viewport():
            self._disarm()
            return
        if not self._source_matches(entry):
            return
        indices = [np.asarray(indices, dtype=np.int64) for entity, indices in selection if entity is cloud]
        if not indices:
            return
        indices = np.unique(np.concatenate(indices))
        indices = indices[(indices >= 0) & (indices < len(cloud.xyz))]
        points = np.asarray(cloud.xyz)[indices]
        points = points[np.isfinite(points).all(axis=1)]
        if len(points) < 6:
            self._status("有效点少于 6 个，请重新框选。")
            return
        existing = self._tracks().get(target_id)
        previous = existing.observations.get(index) if existing else None
        if previous:
            box = fit_box(points, previous.box.yaw, roll=previous.box.roll, pitch=previous.box.pitch)
        else:
            box = fit_box(points)
        if not self._source_matches(entry):
            return
        stream = self._streams.setdefault(key, {
            "signature": _signature(entry.source, entry.cache_identity), "tracks": {}})
        tracks = stream["tracks"]
        if target_id is None:
            target_id = uuid4().hex
            number = 1
            names = {track.name for track in tracks.values()}
            while f"目标 {number}" in names:
                number += 1
            tracks[target_id] = ObjectTrack(target_id, f"目标 {number}")
        if target_id not in tracks:
            self._disarm()
            return
        tracks[target_id].observations[index] = Observation(index, box, True)
        self.selected_id = target_id
        self._disarm()
        self._status("人工框已保存，可选择类别、调整三视图或关联相邻帧。")
        self.changed.emit()
        self._refresh()

    def rename_target(self):
        track = self._track()
        if self.busy or self.dialog is None or track is None:
            return
        if not self._source_matches(self._entry()):
            return
        name = self.dialog.name.text().strip()
        if name and name != track.name:
            track.name = name
            self.changed.emit()
        self._refresh()

    def set_target_category(self):
        track = self._track()
        if self.busy or self.dialog is None or track is None:
            return
        if not self._source_matches(self._entry()):
            return
        category = self.dialog.category.currentText().strip()
        category = "" if category == "未分类" else category
        self.dialog.category.lineEdit().setModified(False)
        if category != track.category:
            track.category = category
            self.changed.emit()
            self._status(f"类别“{category or '未分类'}”已保存，适用于此目标的全部有框帧。")
        self._refresh()

    def apply_manual_edit(self):
        current, track = self._current(), self._track()
        if (self.busy or self.dialog is None or current is None or track is None
                or current[2] not in track.observations):
            return
        if not self._source_matches(current[0]):
            return
        dialog = self.dialog
        try:
            box = Box3D(tuple(spin.value() for spin in dialog.center),
                        tuple(spin.value() for spin in dialog.size), math.radians(dialog.yaw.value()),
                        math.radians(dialog.roll.value()), math.radians(dialog.pitch.value()))
        except ValueError as error:
            self._status(str(error))
            return
        track.observations[current[2]] = Observation(current[2], box, True)
        name = dialog.name.text().strip()
        if name:
            track.name = name
        self.changed.emit()
        self._status("当前帧已保存为人工修正。")
        self._refresh()

    def apply_box_edit(self, box):
        current, track = self._current(), self._track()
        if (not isinstance(box, Box3D) or self.busy or current is None or track is None
                or self._armed is not None
                or self._frame_loading()
                or current[2] not in track.observations or not self._source_matches(current[0])):
            return False
        track.observations[current[2]] = Observation(current[2], box, True)
        self.changed.emit()
        self._message = ""
        self._refresh()
        return True

    def delete_frame(self):
        current, track = self._current(), self._track()
        if (not self.busy and current and track and current[2] in track.observations
                and self._source_matches(current[0])):
            del track.observations[current[2]]
            self.changed.emit()
            self._refresh()

    def delete_target(self):
        if (not self.busy and self.selected_id in self._tracks()
                and self._source_matches(self._entry())):
            del self._tracks()[self.selected_id]
            self.selected_id = None
            self.changed.emit()
            self._refresh()

    def extract_current(self):
        current, track = self._current(), self._track()
        if (self.busy or self._armed is not None or self._frame_loading()
                or current is None or track is None
                or current[2] not in track.observations):
            self._status("请先在已读取的当前帧建立 3D 框，再提取框内点。")
            return False
        entry, frame, index = current
        if not self._source_matches(entry):
            return False
        self._disarm()
        self._generation += 1
        generation, key = self._generation, self._key(entry)
        source, token = entry.source, deepcopy(entry.cache_identity)
        box = deepcopy(track.observations[index].box)
        name = f"{track.name} · 帧 {index + 1} · 框内点"
        info = (f"3D 框提取 · {source.path} · {entry.stream.name} · 原始帧 {index + 1}\n"
                f"timestamp_ns={frame.timestamp_ns}; record_time_ns={frame.record_time_ns}\n"
                f"box={json.dumps(box.to_dict(), ensure_ascii=False)}")

        def extract(cancel, progress):
            if source.cache_token() != token:
                raise ValueError("源文件已更改，请重新载入点云后提取。")
            cloud = extract_box(frame.value, box, name=name, info=info,
                                cancel=cancel, progress=progress)
            return generation, key, source, token, cloud

        self.job_label = "提取"
        self.worker = RecordingTask(extract, self)
        self.worker.done.connect(self._apply_extraction)
        self.worker.failed.connect(self._tracking_error)
        self.worker.progress.connect(self._tracking_progress)
        self.worker.finished.connect(self._finished)
        if self.dialog is not None:
            self.dialog.progress.setValue(0)
        self.busy_changed.emit(True)
        self._status(f"正在提取原始帧 {index + 1} 的框内点…")
        self._refresh_dialog()
        self.worker.start()
        return True

    def _apply_extraction(self, result):
        generation, key, source, token, cloud = result
        entry = self._entries.get(key)
        if (generation != self._generation or entry is None or entry.source is not source
                or entry.cache_identity != token or not self._source_matches(entry)):
            return
        if cloud is None:
            self._status("当前框内没有有效点，请调整框的位置或尺寸后重试。")
            return
        self._status(f"已提取 {cloud.point_count:,} 点并加入场景，可导出或继续处理；可撤销本次添加。")
        self.cloud_extracted.emit(cloud)

    def extract_associated(self, path=None, *, overwrite=False):
        current, track = self._current(), self._track()
        if (self.busy or self._armed is not None or self.browser._worker is not None
                or self.browser._pending is not None or current is None or track is None
                or not track.observations or not self._source_matches(current[0])):
            self._status("请先选择已读取的点云目标，再批量提取有框帧。")
            return False
        self.browser.play.setChecked(False)
        snapshot = deepcopy(track)
        entry = current[0]
        if path is None:
            hold = self.browser.pause_prefetch() if hasattr(self.browser, "pause_prefetch") else nullcontext()
            with hold:
                name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", snapshot.name).strip(". ") or "目标"
                path, selected_filter = QFileDialog.getSaveFileName(
                    self.dialog or self.browser, "保存有框帧点云 · 多帧自动追加原始帧号",
                    f"{name}.pcd", "PCD 点云 (*.pcd);;PLY 点云 (*.ply)",
                    options=QFileDialog.DontConfirmOverwrite)
                if path:
                    suffix = ".ply" if "*.ply" in selected_filter else ".pcd"
                    path = str(Path(path).with_suffix(suffix))
                    paths = track_output_paths(path, snapshot.observations.values())
                    existing = [p for p in paths if p.exists()]
                    if existing:
                        answer = QMessageBox.question(self.dialog or self.browser, "覆盖已有点云？",
                            f"将覆盖 {len(existing)} 个同名文件：\n" + "\n".join(p.name for p in existing[:5]),
                            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                        if answer != QMessageBox.Yes:
                            return False
                        overwrite = True
        if not path:
            return False
        # File dialogs run an event loop; revalidate their captured target.
        if (self.busy or self.browser._worker is not None or self.browser._pending is not None
                or self._entry() is not entry or not self._source_matches(entry)):
            return False
        self._generation += 1
        self._disarm()
        token = deepcopy(entry.cache_identity)
        self._committed_extraction = None

        def extract(cancel, progress):
            files = extract_track(entry.source, entry.stream.id, snapshot.observations.values(),
                                  path, snapshot.name, token=token, cancel=cancel, progress=progress,
                                  overwrite=overwrite)
            # Cancellation after the atomic publish cannot undo a completed export.
            # Retain its location for _finished even if done is suppressed by QThread.
            self._committed_extraction = files, len(snapshot.observations)
            return files

        self.job_label = "批量提取"
        self.worker = RecordingTask(extract, self)
        self.worker.failed.connect(self._tracking_error)
        self.worker.progress.connect(self._tracking_progress)
        self.worker.finished.connect(self._finished)
        if self.dialog is not None:
            self.dialog.progress.setValue(0)
        self.busy_changed.emit(True)
        self._status(f"正在逐帧提取「{snapshot.name}」的 {len(snapshot.observations)} 个有框帧…")
        self._refresh_dialog()
        self.worker.start()
        return True

    def start_tracking(self, direction="both", count=5, max_distance=2.0):
        current, track = self._current(), self._track()
        if (self.busy or self.browser._worker is not None or self.browser._pending is not None
                or current is None or track is None or current[2] not in track.observations):
            self._status("请先在已读取的主点云当前帧建立目标框，并等待其他读取结束。")
            return False
        if direction not in ("forward", "backward", "both") or isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
            self._status("关联方向或帧数无效，每方向允许 1–100 帧。")
            return False
        if not math.isfinite(max_distance) or max_distance <= 0:
            self._status("最大每帧位移必须是正数。")
            return False
        entry, frame, index = current
        source = entry.source
        if not self._source_matches(entry):
            return False
        token = deepcopy(entry.cache_identity)
        self.job_label = "关联"
        self._disarm()
        self._generation += 1
        generation = self._generation
        key = self._key(entry)
        snapshot = deepcopy(track)
        refs = tuple(entry.frames)
        position = next(i for i, ref in enumerate(refs) if ref.index == index)
        seed_xyz = frame.value.xyz
        directions = (1, -1) if direction == "both" else ((1,) if direction == "forward" else (-1,))
        total = sum(min(count, len(refs) - position - 1 if step == 1 else position)
                    for step in directions)

        def associate(cancel, progress):
            batch, messages = {}, []
            completed = 0
            for step in directions:
                previous_xyz = seed_xyz
                previous_ref = refs[position]
                previous_box = snapshot.observations[index].box
                reason = "已到帧边界"
                stop_index = index
                for offset in range(1, count + 1):
                    if cancel():
                        return None
                    next_position = position + step * offset
                    if not 0 <= next_position < len(refs):
                        reason = "已到帧边界"
                        break
                    ref = refs[next_position]
                    stop_index = ref.index
                    if ref.index != previous_ref.index + step:
                        reason = "原始帧号不连续"
                        break
                    before_time = previous_ref.record_time_ns if previous_ref.record_time_ns is not None else previous_ref.timestamp_ns
                    next_time = ref.record_time_ns if ref.record_time_ns is not None else ref.timestamp_ns
                    if before_time is not None and next_time is not None and abs(next_time - before_time) > 1_000_000_000:
                        reason = "时间间隔超过 1 秒"
                        break
                    if source.cache_token() != token:
                        raise ValueError("源文件已更改，关联结果已丢弃。")
                    next_frame = source.read_frame(entry.stream.id, ref.index, cancel=cancel)
                    if cancel():
                        return None
                    if not isinstance(next_frame, FrameData) or next_frame.kind != "pointcloud":
                        reason = "相邻帧没有点云"
                        break
                    anchor = snapshot.observations.get(ref.index)
                    if anchor is not None and anchor.manual:
                        previous_box = anchor.box
                    else:
                        match = track_step(previous_xyz, next_frame.value.xyz, previous_box,
                                           max_distance=max_distance, cancel=cancel)
                        if match.box is None:
                            reason = _MATCH_REASONS.get(match.reason, "目标几何匹配失败")
                            break
                        batch[ref.index] = Observation(ref.index, match.box, False, match.score)
                        previous_box = match.box
                    previous_xyz, previous_ref = next_frame.value.xyz, ref
                    completed += 1
                    progress(completed / max(1, total))
                    reason = "已达到请求帧数"
                messages.append(f"{'后续' if step == 1 else '之前'}：原始帧 {stop_index + 1}，{reason}")
            if cancel():
                return None
            return generation, key, source, token, snapshot.id, batch, "。".join(messages)

        self.worker = RecordingTask(associate, self)
        self.worker.done.connect(self._apply_batch)
        self.worker.failed.connect(self._tracking_error)
        self.worker.progress.connect(self._tracking_progress)
        self.worker.finished.connect(self._finished)
        if self.dialog is not None:
            self.dialog.progress.setValue(0)
        self.busy_changed.emit(True)
        self._status(f"正在从原始帧 {index + 1} 关联「{track.name}」，可随时取消。")
        self._refresh_dialog()
        self.worker.start()
        return True

    def _apply_batch(self, result):
        if result is None:
            return
        generation, key, source, token, target_id, batch, message = result
        entry = self._entries.get(key)
        if (generation != self._generation or entry is None or entry.source is not source
                or entry.cache_identity != token or not self._source_matches(entry)):
            return
        stream = self._streams.get(key)
        track = stream["tracks"].get(target_id) if stream else None
        if track is None:
            return
        changed = False
        for index, observation in batch.items():
            existing = track.observations.get(index)
            if existing is None or not existing.manual:
                track.observations[index] = observation
                changed = True
        if changed:
            self.changed.emit()
        guidance = ("用“上一框 / 下一框”逐帧检查；可在三视图拖动修正。" if batch else
                    "未新增自动框，已有人工框会保留。若匹配停止，请在目标清晰的帧重框后再关联。")
        self._status(f"已生成 {len(batch)} 个自动建议。{message}。{guidance}")
        self._refresh()

    def _tracking_progress(self, fraction):
        if (self.worker is None or self.sender() is not self.worker
                or self.worker.isInterruptionRequested() or self.dialog is None):
            return
        self.dialog.progress.setValue(round(max(0., min(1., fraction)) * 1000))
        self.dialog.update_tracking_action()

    def _tracking_error(self, message):
        worker = self.worker
        if worker is not None and not worker.isInterruptionRequested():
            self._status(f"{self.job_label}停止  {message}")

    def _finished(self):
        worker = self.worker
        if worker is None or self.sender() is not None and self.sender() is not worker:
            return
        self.worker = None
        if self._committed_extraction is not None:
            files, total = self._committed_extraction
            self._status(f"已保存 {len(files)} 个点云文件，跳过 {total - len(files)} 个空框。"
                         + (f"首个文件：{files[0]}" if files else "请检查框的位置和尺寸。"))
            self._committed_extraction = None
        elif self._message == "正在取消，等待当前读取结束。":
            self._status(f"已取消{self.job_label}。")
        worker.deleteLater()
        self.busy_changed.emit(False)
        self._refresh()
        self.idle.emit()

    def cancel(self):
        self._generation += 1
        self._navigation_focus = None
        self._disarm()
        if self.worker is not None:
            self.worker.requestInterruption()
            self._status("正在取消，等待当前读取结束。")

    def stop(self):
        self._selection_dialog_hidden = False
        self.cancel()
        if self.worker is not None:
            self.worker.wait()
            if self.job_label != "批量提取":
                self.worker.done.disconnect()
            self.worker.failed.disconnect(self._tracking_error)
            self.worker.finished.disconnect(self._finished)
            self._finished()
        self.invalidate_frame()
        if self.dialog is not None:
            self.dialog.close()

    def show(self):
        self._selection_dialog_hidden = False
        if hasattr(self.browser, "play"):
            self.browser.play.setChecked(False)
        if self.dialog is None:
            self.dialog = ObjectTrackingDialog(self)
            if hasattr(self.browser, "attach_box_panel"):
                self.browser.attach_box_panel(self.dialog)
        self._refresh()
        self.dialog.show()
        self.panel_visibility_changed.emit(True)
        self.dialog.raise_()
        if self.dialog.isWindow():
            self.dialog.activateWindow()

    def _refresh(self):
        tracks = self._tracks()
        if self.selected_id not in tracks:
            self.selected_id = next(iter(tracks), None)
        viewport = self._viewport()
        current = self._current()
        boxes = []
        if current:
            for track in tracks.values():
                observation = track.observations.get(current[2])
                if observation is not None:
                    boxes.append({"id": track.id[:8], "name": track.name, "category": track.category,
                                  **observation.box.to_dict(), "selected": track.id == self.selected_id,
                                  "manual": observation.manual})
        if viewport is not None:
            viewport.set_annotation_boxes(boxes)
        self.boxes_changed.emit(boxes)
        self._refresh_dialog()

    def _refresh_dialog(self):
        dialog = self.dialog
        if dialog is None:
            return
        tracks = self._tracks()
        dialog.set_targets([(f"{track.name} · {track.id[:8]}", track.id)
                            for track in tracks.values()], self.selected_id)
        track = self._track()
        current = self._current()
        observation = track.observations.get(current[2]) if track and current else None
        dialog.name.setText(track.name if track else "")
        dialog.set_category(track.category if track else "", track.id if track else None)
        if observation:
            for spin, value in zip(dialog.center + dialog.size, observation.box.center + observation.box.size):
                spin.setValue(value)
            dialog.yaw.setValue(math.degrees(observation.box.yaw))
            dialog.roll.setValue(math.degrees(observation.box.roll))
            dialog.pitch.setValue(math.degrees(observation.box.pitch))
            status = "人工框" if observation.manual else f"自动建议 · 几何质量 {observation.score:.3f}" if observation.score is not None else "自动建议"
            detail = f"当前原始帧 {current[2] + 1} · {status} · 高亮为当前帧框内点云"
        else:
            detail = "当前帧没有目标框，可返回有框帧继续关联，或在本帧重框。" if current and track else (
                "先在主视图新建 3D 框，再关联前后帧。" if current else "请在主视图选择并读取点云。")
        source_current = self._source_matches(self._entry(), notify=False)
        if not source_current and self._entry() is not None:
            detail = "源文件不可用或已更改，请恢复连接并重新载入点云。"
        dialog.status.setText(f"{detail}\n{self._message}" if self._message else detail)
        editable = (current is not None and source_current and not self.busy and self._armed is None
                    and not self._frame_loading())
        identity = (self._key(current[0]), current[2], self.selected_id) if current and observation else None
        if observation and current:
            dialog.editor.set_data(current[1].value.xyz, observation.box,
                                   reset=identity != self._editor_identity, reuse_points=True)
        else:
            dialog.editor.clear()
        self._editor_identity = identity
        dialog.editor.setEnabled(editable and observation is not None)
        dialog.frame_label.setText(f"原始帧 {current[2] + 1}" if current else "等待点云")
        dialog.previous_button.setEnabled(editable and self.browser.position.value() > 1)
        dialog.next_button.setEnabled(editable and self.browser.position.value() < self.browser.position.maximum())
        dialog.focus_button.setEnabled(editable and observation is not None and hasattr(self._viewport(), "focus_entity"))
        dialog.new_button.setEnabled(editable)
        dialog.reframe_button.setEnabled(editable and track is not None)
        dialog.apply_button.setEnabled(editable and observation is not None)
        dialog.extract_button.setEnabled(editable and track is not None and bool(track.observations))
        self.refresh_reader_actions()
        dialog.update_tracking_action()
        dialog.progress.setVisible(self.busy)
        dialog.cancel_button.setVisible(self.busy or self._armed is not None)
        results = self._result_positions()
        dialog.set_results([(f"原始帧 {index + 1} · {'人工' if track.observations[index].manual else '自动'}", index)
                            for _, index in results], current[2] if current else None)
        dialog.result_frames.setEnabled(editable and bool(results))
        position = self.browser.position.value()
        dialog.result_previous.setEnabled(editable and any(pos < position for pos, _ in results))
        dialog.result_next.setEnabled(editable and any(pos > position for pos, _ in results))
        dialog.return_button.setVisible(bool(results) and current is not None and observation is None)
        dialog.return_button.setEnabled(editable)
        dialog.delete_frame_button.setEnabled(editable and observation is not None)
        dialog.delete_target_button.setEnabled(source_current and not self.busy and track is not None)
        dialog.targets.setEnabled(not self.busy)
        dialog.name.setEnabled(source_current and not self.busy and track is not None)
        dialog.category.setEnabled(source_current and not self.busy and track is not None)
        for widget in dialog.center + dialog.size + dialog.rotation + [dialog.direction, dialog.count, dialog.max_distance]:
            widget.setEnabled(source_current and not self.busy)
        dialog.cancel_button.setEnabled(self.busy or self._armed is not None)
        dialog.export_button.setEnabled(self.has_annotations)

    def export_json(self, path=None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self.dialog or self.browser, "导出目标标注", "targets.json", "JSON (*.json)")
        if not path:
            return False
        sources = sorted({key[0] for key in self._streams})
        payload = {"version": 1, "coordinate_system": "source point cloud coordinates",
                   "units": {"position": "metres", "size": "metres", "yaw": "radians",
                             "roll": "radians", "pitch": "radians"},
                   "sources": [{"path": source, **self.source_state(source)} for source in sources]}
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        output = QSaveFile(os.fspath(path))
        if not output.open(QIODevice.WriteOnly):
            self._status(f"无法导出标注  {output.errorString()}")
            return False
        if output.write(data) != len(data) or not output.commit():
            output.cancelWriting()
            self._status(f"无法导出标注  {output.errorString()}")
            return False
        self._status("目标标注 JSON 已导出。")
        return True
