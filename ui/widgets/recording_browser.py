"""Indexed stream navigation, two-view time association and calibration."""

from collections import OrderedDict
from dataclasses import dataclass, field, replace
import json
import os
import re
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QIODevice, QSaveFile, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QMessageBox, QWidget

from core.calibration import (
    calibration_from_dict,
    calibration_to_dict,
    from_camera_info,
    project_points,
)
from core.calibration_display import projection_colors
from core.calibration_files import read_calibration_bundle, CALIBRATION_FILE_FILTER
from core.color.colormaps import compute_colors, list_colormaps
from core.fusion import project_box_segments, sample_image_colors
from core.io.recording_types import FrameData, FrameRef, RecordingSource, StreamInfo
from core.model.entities import COLOR_MODES, ScalarField
from core.object_tracking import Box3D
from core.recording_sync import TimestampMatcher
from ui.recording_tasks import RecordingTask
from ui.widgets.data_view import render_projection_image
from ui.widgets.recording_prefetch import PreparedFramesMixin
from ui.widgets.recording_export import RecordingExportController
from ui.widgets.calibration_editor import CalibrationImportDialog, IntrinsicsDialog, ExtrinsicsDialog
from ui.widgets.source_overview import KIND_LABELS, time_text
from ui.widgets.recording_chrome import RecordingChromeMixin
from ui.widgets.object_tracking import ObjectTrackingController


def _find_key(combo, value):
    # PySide stores Python tuples as opaque QVariant objects; findData compares
    # identity rather than equal path/channel tuples reconstructed on restore.
    value = tuple(value) if isinstance(value, list) else value
    return next((i for i in range(combo.count()) if combo.itemData(i) == value), -1)


@dataclass
class BrowserStream:
    source: RecordingSource
    stream: StreamInfo
    frames: list[FrameRef]
    cache_identity: object = field(init=False)

    def __post_init__(self):
        self.cache_identity = self.source.cache_token()

    @property
    def key(self):
        return self.source.path, self.stream.id

    @property
    def label(self):
        path = Path(self.source.path)
        group = path.parent.parent.name if path.parent.name.lower() in ("bag", "rosbag", "rosbag2") else path.parent.name
        source = group if path.name.lower() in ("bag", "rosbag", "rosbag2") else f"{group}/{path.name}"
        return f"{self.stream.name} · {source}"


def filter_frames(frames, selection, start_ns):
    mode = selection.get("mode", "all")
    if mode == "frames":
        return frames[
            max(0, int(selection.get("first", 0))) : int(selection["last"]) + 1
        ]
    if mode == "time":
        first = start_ns + round(selection.get("start_seconds", 0) * 1e9)
        last = start_ns + round(selection.get("end_seconds", 0) * 1e9)
        return [
            frame
            for frame in frames
            if frame.record_time_ns is not None
            and first <= frame.record_time_ns <= last
        ]
    return frames


def _frame_bytes(frame):
    if frame.kind == "image":
        return frame.value.nbytes
    if frame.kind == "pointcloud":
        cloud = frame.value
        return cloud.xyz.nbytes + sum(
            field.data.nbytes for field in cloud.fields.values()
        )
    return len(str(frame.value)) * 4


class RecordingBrowser(PreparedFramesMixin, RecordingChromeMixin, QWidget):
    stream_selected = Signal(object)
    cloud_to_scene = Signal(object)
    text_as_cloud = Signal(str)
    files_dropped = Signal(list)
    fps_updated = Signal(float)
    status_changed = Signal(str)
    frame_changed = Signal(object)
    display_changed = Signal()
    source_options_requested = Signal(str)
    source_reload_requested = Signal(str)

    def __init__(self, theme="dark", viewport_factory=None, parent=None):
        super().__init__(parent)
        self._init_prepared_frames()
        self.entries = []
        self._worker = None
        self._pending = None
        self._reload_topics = set()
        self._generation = 0
        self._stopping = False
        self._read_started_at = None
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._matcher_cache = {}
        self._page_offsets = {}
        self._page_positions = {}
        self.calibration = None
        self._calibration_origin = ""
        self._calibration_key = None
        self._pair = None
        self.color_mode = "height"
        self.colormap = "default"
        self.color_field = ""
        self.color_range = None
        self.point_size = 2.0
        self._source_frames = []
        self._image_colors = None
        self._display_calibration = None
        self._annotation_boxes = []
        self._open_box_on_frame = False
        self._building = False
        self._exporting = False
        self._last_frame_error = ""
        self.current_frames = []
        self._build_chrome(theme, viewport_factory)
        self._timeline_cache = {}
        self._displayed_indices = [None, None]
        for view in (self.left, self.right):
            view.compact_header()
            view.color_requested.connect(self.set_color_mode)
            view.files_dropped.connect(self.files_dropped)
            view.cycle_color_requested.connect(self._cycle_color)
            view.point_size_requested.connect(self._change_point_size)
            view.page_requested.connect(lambda delta, view=view: self._change_page(view, delta))
            view.cloud_import_requested.connect(lambda view=view: self._import_text_cloud(view))
            view.next_requested.connect(self._advance)
            view.previous_requested.connect(lambda: self.position.setValue(self.position.value() - 1))
            view.play_requested.connect(self.play.click)
            view.first_requested.connect(self.first.click)
            view.last_requested.connect(self.last.click)
            view.fps_updated.connect(self.fps_updated)
            view.render_problem.connect(self._render_problem)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._advance)
        self.previous.clicked.connect(
            lambda: self.position.setValue(self.position.value() - 1)
        )
        self.next.clicked.connect(self._advance)
        self.first.clicked.connect(lambda: self._seek_edge(False))
        self.last.clicked.connect(lambda: self._seek_edge(True))
        self.position.valueChanged.connect(self._position_changed)
        self.slider.valueChanged.connect(
            lambda value: self.position.setValue(value + 1)
        )
        self.slider.sliderPressed.connect(lambda: self.play.setChecked(False))
        self.primary.currentIndexChanged.connect(self._primary_changed)
        self.secondary.currentIndexChanged.connect(self._configuration_changed)
        self.compare.toggled.connect(self._configuration_changed)
        self.basis.currentIndexChanged.connect(self._basis_changed)
        self.tolerance.valueChanged.connect(self._request_frame)
        self.offset.valueChanged.connect(self._request_frame)
        self.camera.currentIndexChanged.connect(self._camera_changed)
        self.overlay.toggled.connect(self._request_frame)
        self.undistort.toggled.connect(self._request_frame)
        self.play.toggled.connect(self._play_changed)
        self.rate.valueChanged.connect(self._schedule_next)
        self.calibration_button.clicked.connect(self._import_calibration)
        self.exporter = RecordingExportController(self)
        self.export_button.clicked.connect(self.exporter.open_dialog)
        self.range_button.toggled.connect(self._range_toggled)
        self.slider.selectionChanged.connect(self._range_changed)
        self.range_first.valueChanged.connect(lambda value: self.slider.setSelection(
            value - 1, max(value - 1, self.range_last.value() - 1)))
        self.range_last.valueChanged.connect(lambda value: self.slider.setSelection(
            min(value - 1, self.range_first.value() - 1), value - 1))
        self.scene_button.triggered.connect(self._add_cloud)
        self.swap_button.clicked.connect(self.swap_views)
        self.jump_button.clicked.connect(self._ask_time_jump)
        self.export_primary_action.triggered.connect(lambda: self.export_current(0))
        self.export_secondary_action.triggered.connect(lambda: self.export_current(1))
        self.options_action.triggered.connect(lambda: self._source_action(self.source_options_requested))
        self.reload_action.triggered.connect(lambda: self._source_action(self.source_reload_requested))
        self.reload_topic_action.triggered.connect(lambda: self.reload_topic())
        self.reload_comparison_action.triggered.connect(lambda: self.reload_topic(self.secondary.currentData()))
        self.calibration_panel.save_button.clicked.connect(self._save_calibration)
        self.calibration_panel.edit_button.clicked.connect(self._edit_intrinsics)
        self.calibration_panel.edit_extrinsics_button.clicked.connect(self._edit_extrinsics)
        self.calibration_panel.reset_button.clicked.connect(self._reset_calibration)
        self.calibration_panel.display_changed.connect(self._request_frame)
        self.calibration_panel.colorize.toggled.connect(self._request_frame)
        self.calibration_panel.box_overlay.toggled.connect(lambda: self._update_image_boxes())
        self.cloud_display.mode_changed.connect(self.set_color_mode)
        self.cloud_display.colormap_changed.connect(self.set_colormap)
        self.cloud_display.field_changed.connect(self.set_color_field)
        self.cloud_display.range_changed.connect(self.set_color_range)
        self.cloud_display.size_changed.connect(self.set_point_size)
        for view in (self.left, self.right):
            for sequence, callback in (
                ("Left", self.previous.click),
                ("Right", self.next.click),
            ):
                shortcut = QShortcut(QKeySequence(sequence), view.image)
                shortcut.setContext(Qt.WidgetWithChildrenShortcut)
                shortcut.activated.connect(callback)
        self.tracking = ObjectTrackingController(self)
        self.tracking.cloud_extracted.connect(self.cloud_to_scene)
        self.tracking.boxes_changed.connect(self._update_image_boxes)
        self.tracking_button.clicked.connect(self.show_box_editor)
        self.frame_changed.connect(self.tracking.on_frame_changed)
        self.tracking.busy_changed.connect(self._tracking_busy_changed)
        self.tracking.idle.connect(self._tracking_finished)
        self._primary_changed()

    def _tracking_busy_changed(self, busy):
        if busy:
            self.play.setChecked(False)
            self.timer.stop()
        for widget in (self.toolbar, self.transport, self.settings_panel, self.left, self.right):
            widget.setEnabled(not busy)

    def attach_box_panel(self, panel):
        self.box_splitter.addWidget(panel)
        self.box_splitter.setStretchFactor(0, 3)
        self.box_splitter.setStretchFactor(1, 2)
        panel.attach_preview_layout(self.box_splitter, self.panes)

    def show_box_editor(self):
        if self.left.cloud is not None:
            self.tracking.show()
        elif self.right.cloud is not None and self.compare.isChecked():
            self._open_box_on_frame = True
            self.swap_views()

    def _tracking_finished(self):
        if not self._stopping and self._pending is not None and self._worker is None:
            self._start_read()

    def _source_action(self, signal):
        entry = self.entry(self.primary.currentData())
        if entry:
            self.play.setChecked(False)
            signal.emit(entry.source.path)

    def _render_problem(self, message):
        self.play.setChecked(False)
        self.status.setText(message)
        self.status.show()
        self.status_changed.emit(message)

    def reload_topic(self, key=None):
        """Discard a topic's decoded frames only after the active reader exits."""
        key = self.primary.currentData() if key is None else tuple(key)
        if self._stopping or self.entry(key) is None:
            return False
        self.play.setChecked(False)
        self._reload_topics.add(key)
        # _request_frame invalidates the old generation and cancels its reader.
        # All cache mutations and canvas replacement happen later, serially.
        self._request_frame()
        return True

    def _apply_topic_reloads(self):
        if self._reload_topics:
            self._clear_prepared()
        for key in self._reload_topics:
            for cached in list(self._cache):
                if cached[:2] == key:
                    self._cache_bytes -= _frame_bytes(self._cache.pop(cached))
            if key == self.camera.currentData():
                self._calibration_key = None
            for view, combo in ((self.left, self.primary), (self.right, self.secondary)):
                if combo.currentData() == key:
                    self.tracking._viewports.discard(view.cloud_view)
                    view.rebuild_cloud_view()
        self._reload_topics.clear()

    def entry(self, key):
        return next((entry for entry in self.entries if entry.key == key), None)

    def set_entries(self, entries, *, state=None):
        self._clear_prepared()
        primary, secondary, camera = (
            self.primary.currentData(),
            self.secondary.currentData(),
            self.camera.currentData(),
        )
        self.entries = list(entries)
        self.tracking.set_entries(self.entries)
        self._matcher_cache.clear()
        self._timeline_cache.clear()
        self._building = True
        for combo in (self.primary, self.secondary, self.camera):
            combo.clear()
        self.camera.addItem("选择包内相机参数", None)
        for entry in self.entries:
            label = f"{KIND_LABELS.get(entry.stream.kind, '消息')} · {entry.label}"
            self.primary.addItem(label, entry.key)
            self.secondary.addItem(label, entry.key)
            for combo in (self.primary, self.secondary):
                combo.setItemData(combo.count() - 1, f"{entry.stream.name}\n{entry.source.path}\n{entry.stream.message_type}", Qt.ToolTipRole)
            if entry.stream.kind == "camera_info":
                self.camera.addItem(entry.label, entry.key)
        for combo, value in (
            (self.primary, primary),
            (self.secondary, secondary),
            (self.camera, camera),
        ):
            index = _find_key(combo, value)
            if index >= 0:
                combo.setCurrentIndex(index)
        if secondary is None and self.secondary.count() > 1:
            self.secondary.setCurrentIndex(1)
        self._building = False
        if state is None:
            self._primary_changed()
        else:
            self.restore_state(state)

    def select_stream(self, key):
        index = _find_key(self.primary, key)
        if index >= 0:
            if self.primary.currentIndex() == index:
                self._request_frame()
            else:
                self.primary.setCurrentIndex(index)

    def _primary_changed(self, *_args):
        if self._building:
            return
        self.play.setChecked(False)
        entry = self.entry(self.primary.currentData())
        count = len(entry.frames) if entry else 0
        self.position.blockSignals(True)
        self.position.setRange(1, max(count, 1))
        self.position.setValue(1)
        self.position.blockSignals(False)
        self.slider.setRange(0, max(count - 1, 0))
        self.slider.setSelection(0, max(count - 1, 0))
        self.range_button.setChecked(False)
        self._range_changed(*self.slider.selection())
        self.total.setText(f"/ {count:,}")
        for widget in (self.position, self.slider, self.play, self.export_button):
            widget.setEnabled(count > 0)
        if entry and (not self.compare.isChecked() or self.secondary.currentData() == entry.key):
            self._choose_comparison(entry)
        if entry:
            self.stream_selected.emit(entry.key)
        self._update_transport()
        self._configuration_changed()

    def _choose_comparison(self, primary):
        candidates = [entry for entry in self.entries if entry.key != primary.key]
        if not candidates:
            return
        wanted = {"pointcloud": "image", "image": "pointcloud"}.get(primary.stream.kind)
        def rank(entry):
            same_capture = (Path(entry.source.path).parent == Path(primary.source.path).parent
                            or Path(entry.source.path) == Path(primary.source.path).parent
                            or Path(primary.source.path) == Path(entry.source.path).parent)
            return (entry.stream.kind != wanted if wanted else 0,
                    not same_capture, entry.stream.kind in ("camera_info", "message"))
        chosen = min(candidates, key=rank)
        self.secondary.blockSignals(True)
        self.secondary.setCurrentIndex(_find_key(self.secondary, chosen.key))
        self.secondary.blockSignals(False)

    def _update_context_controls(self):
        comparing = self.compare.isChecked()
        self.comparison_row.setVisible(comparing)
        self.right.setVisible(comparing)
        self.secondary.setVisible(comparing)
        self.secondary.setEnabled(comparing)
        self.swap_button.setVisible(comparing)
        self.swap_button.setEnabled(bool(self.current_frames) and len(self.entries) > 1)
        self.compare.setEnabled(len(self.entries) > 1)
        self.options_action.setEnabled(bool(self.entries))
        self.reload_action.setEnabled(bool(self.entries))
        self.reload_topic_action.setEnabled(self.entry(self.primary.currentData()) is not None)
        self.reload_comparison_action.setEnabled(comparing and self.entry(self.secondary.currentData()) is not None)
        for combo in (self.primary, self.secondary):
            index = combo.currentIndex()
            combo.setToolTip(combo.itemData(index, Qt.ToolTipRole) or "选择数据流")
        tracking = getattr(self, "tracking", None)
        if tracking is not None and tracking.dialog is not None:
            tracking.dialog.schedule_layout()

    def _basis_changed(self, *_args):
        self._update_transport()
        self._request_frame()

    def _time_bounds(self):
        entry = self.entry(self.primary.currentData())
        if entry is None:
            return None
        basis = self.basis.currentData()
        key = (entry.key, basis)
        if key not in self._timeline_cache:
            stamps = [ref.record_time_ns if basis == "record" else ref.timestamp_ns for ref in entry.frames]
            stamps = [stamp for stamp in stamps if stamp is not None]
            self._timeline_cache[key] = (min(stamps), max(stamps)) if stamps else None
        return self._timeline_cache[key]

    def _update_transport(self):
        entry = self.entry(self.primary.currentData())
        count = len(entry.frames) if entry else 0
        multiple = count > 1
        for widget in (self.slider, self.first, self.previous, self.next, self.last,
                       self.play, self.position, self.total, self.rate):
            widget.setVisible(multiple)
        self.play.setEnabled(multiple)
        self.previous.setEnabled(multiple and self.position.value() > 1)
        self.next.setEnabled(multiple and self.position.value() < count)
        self.first.setEnabled(multiple and self.position.value() > 1)
        self.last.setEnabled(multiple and self.position.value() < count)
        bounds = self._time_bounds()
        self.jump_button.setEnabled(multiple and bounds is not None and bounds[0] != bounds[1])
        self.jump_button.setVisible(multiple)
        self.range_button.setVisible(multiple)
        self._update_range_row()
        if bounds is not None and entry and entry.frames:
            ref = entry.frames[min(self.position.value() - 1, count - 1)]
            stamp = ref.record_time_ns if self.basis.currentData() == "record" else ref.timestamp_ns
            if stamp is None:
                self.time_label.setText("当前帧无所选时间")
            else:
                self.time_label.setText(f"{(stamp - bounds[0]) / 1e9:.3f} / {(bounds[1] - bounds[0]) / 1e9:.3f} 秒")
                self.time_label.setToolTip(f"{'记录 / 抓包时间' if self.basis.currentData() == 'record' else '设备 / 消息头时间'}\n{time_text(stamp)}\n{stamp} ns")
        else:
            self.time_label.setText("无时间戳 · 按帧浏览" if multiple else "单个文件 / 消息")

    def _range_toggled(self, checked):
        self.slider.setSelectionEnabled(checked)
        self._range_changed(*self.slider.selection())

    def _range_changed(self, first, last):
        for spin, value in ((self.range_first, first + 1), (self.range_last, last + 1)):
            blocker = QSignalBlocker(spin)
            spin.setRange(1, self.slider.maximum() + 1)
            spin.setValue(value)
            blocker.unblock()
        self.range_count.setText(f"共 {last - first + 1:,} 帧")
        self._update_range_row()

    def jump_to_time(self, seconds):
        bounds = self._time_bounds()
        entry = self.entry(self.primary.currentData())
        if bounds is None or entry is None:
            return False
        target = bounds[0] + round(max(0, float(seconds)) * 1e9)
        match = self._matcher(entry, self.basis.currentData()).closest(target, None)
        if match is None:
            return False
        position = next(i for i, ref in enumerate(entry.frames) if ref.index == match[0])
        self.play.setChecked(False)
        self.position.setValue(position + 1)
        return True

    def _ask_time_jump(self):
        bounds = self._time_bounds()
        if bounds is None:
            return
        seconds, accepted = QInputDialog.getDouble(self, "按时间定位", "相对于当前数据流起点（秒）", 0, 0, (bounds[1] - bounds[0]) / 1e9, 3)
        if accepted:
            self.jump_to_time(seconds)

    def swap_views(self):
        if not self.compare.isChecked() or not self.current_frames:
            return
        secondary = self.entry(self.secondary.currentData())
        if secondary is None:
            return
        index = self._displayed_indices[1]
        position = next((i + 1 for i, ref in enumerate(secondary.frames) if ref.index == index), 1)
        state = self.state()
        state.update(primary=state["secondary"], secondary=state["primary"], position=position,
                     offset_ms=-state["offset_ms"])
        self.restore_state(state)

    def _configuration_changed(self, *_args):
        if self._building:
            return
        pair = (
            self.primary.currentData(),
            self.secondary.currentData() if self.compare.isChecked() else None,
        )
        if pair != self._pair:
            self._pair = pair
            self.calibration = None
            self._calibration_origin = ""
            self._calibration_key = None
            self.overlay.setChecked(False)
            self.undistort.setChecked(False)
            self.calibration_panel.colorize.setChecked(False)
            images = [self.entry(key) for key in pair]
            image = next(
                (entry for entry in images if entry and entry.stream.kind == "image"),
                None,
            )
            cameras = [
                entry
                for entry in self.entries
                if entry.stream.kind == "camera_info"
                and image
                and entry.source.path == image.source.path
            ]
            self.camera.blockSignals(True)
            self.camera.setCurrentIndex(
                _find_key(self.camera, cameras[0].key) if len(cameras) == 1 else 0
            )
            self.camera.blockSignals(False)
        self._update_context_controls()
        self._request_frame()

    def _camera_changed(self, *_args):
        if self._building:
            return
        self.calibration = None
        self._calibration_origin = ""
        self._calibration_key = None
        self._request_frame()

    def _position_changed(self, value):
        if value != self._last_requested_position:
            self._navigation_direction = 1 if value > self._last_requested_position else -1
        self._last_requested_position = value
        self.slider.blockSignals(True)
        self.slider.setValue(value - 1)
        self.slider.blockSignals(False)
        self._update_transport()
        self._request_frame()

    def _play_changed(self, checked):
        self.play.setText("暂停" if checked else "播放")
        if not checked:
            self.timer.stop()
        elif self.tracking.busy or self.position.maximum() <= 1:
            self.play.setChecked(False)
        elif self.position.value() >= self.position.maximum():
            self.position.setValue(1)
        elif self._worker is not None or self._pending is not None:
            return  # Let the already requested frame finish; don't decode it again.
        elif self.current_frames and isinstance(self.current_frames[0], FrameData):
            self._read_started_at = time.monotonic()
            self._schedule_next()
        else:
            self._request_frame()

    def _schedule_next(self, *_args):
        if (self._building or self._stopping or not self.play.isChecked()
                or (self._worker is not None and not getattr(self._worker, "background", False))
                or self._pending is not None or self.tracking.busy):
            return
        if (self.position.value() >= self.position.maximum() or not self.current_frames
                or not isinstance(self.current_frames[0], FrameData)):
            self.play.setChecked(False)
            return
        elapsed = 0 if self._read_started_at is None else time.monotonic() - self._read_started_at
        # Reading/presentation is part of the frame interval. Never skip frames
        # to catch up, and yield to the event loop even when the read was slow.
        self.timer.start(max(1, round(1000 / self.rate.value() - elapsed * 1000)))

    def _seek_edge(self, last):
        self.play.setChecked(False)
        self.position.setValue(self.position.maximum() if last else self.position.minimum())

    def _advance(self):
        if self.position.value() >= self.position.maximum():
            self.play.setChecked(False)
        else:
            self.position.setValue(self.position.value() + 1)

    def _request_frame(self, *_args):
        if self._building or self._stopping:
            return
        self._last_frame_error = ""
        self._prefetch_timer.stop()
        self.tracking.invalidate_frame()
        self.tracking_button.setEnabled(False)
        self.timer.stop()
        self.current_frames = []
        self._source_frames = []
        self._image_colors = None
        self._display_calibration = None
        self._update_image_boxes([])
        self._displayed_indices = [None, None]
        self.export_button.setEnabled(False)
        self.scene_button.setEnabled(False)
        self.export_primary_action.setEnabled(False)
        self.export_secondary_action.setEnabled(False)
        self.swap_button.setEnabled(False)
        for view in (self.left, self.right):
            view.previous_page.setEnabled(False)
            view.next_page.setEnabled(False)
            view.as_cloud.setEnabled(False)
        self._generation += 1
        self._pending = self._generation
        self._read_started_at = time.monotonic()
        if self._worker is not None:
            self._worker.requestInterruption()
        if self.tracking.worker is None and self._use_prepared(self._generation):
            return
        if self.tracking.worker is not None:
            self.tracking.cancel()
        elif self._worker is not None:
            self._worker.requestInterruption()
        else:
            self._start_read()

    def _read_cached(self, entry, index, cancel, offset=0):
        token = entry.source.cache_token()
        if token != entry.cache_identity:
            raise ValueError(
                "源文件已更改，请使用「更多 → 重新索引整个数据源」更新帧数和时间索引。"
            )
        source_refs = entry.source.index.get(entry.stream.id, [])
        if index < len(source_refs) and isinstance(source_refs[index].locator, str):
            stat = os.stat(source_refs[index].locator)
            token = token, stat.st_size, stat.st_mtime_ns
        key = (*entry.key, token, index, offset)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        frame = (
            entry.source.read_page(offset, cancel=cancel)
            if offset and hasattr(entry.source, "read_page")
            else entry.source.read_frame(entry.stream.id, index, cancel=cancel)
        )
        self._cache[key] = frame
        self._cache_bytes += _frame_bytes(frame)
        while len(self._cache) > 12 or self._cache_bytes > 256 * 1024**2:
            _, removed = self._cache.popitem(last=False)
            self._cache_bytes -= _frame_bytes(removed)
        return frame

    def reset_source_pages(self, path):
        for key in list(self._page_offsets):
            if key[0] == path:
                self._page_offsets.pop(key, None)
                self._page_positions.pop(key, None)
        self._calibration_key = None

    def _change_page(self, view, delta):
        if view.frame is None or self._worker is not None:
            return
        entry = self.entry(
            self.primary.currentData()
            if view is self.left
            else self.secondary.currentData()
        )
        if entry is None or not hasattr(entry.source, "read_page"):
            return
        history = self._page_offsets.setdefault(entry.key, [0])
        position = self._page_positions.get(entry.key, 0)
        if delta > 0:
            offset = view.frame.metadata.get("next_offset")
            if offset is None:
                return
            if position + 1 == len(history):
                history.append(offset)
            position += 1
        else:
            position = max(0, position - 1)
        self._page_positions[entry.key] = position
        self._request_frame()

    def _import_text_cloud(self, view):
        entry = self.entry(
            self.primary.currentData()
            if view is self.left
            else self.secondary.currentData()
        )
        if entry and os.path.isfile(entry.source.path):
            self.text_as_cloud.emit(entry.source.path)

    def _cycle_color(self):
        self.set_color_mode(
            COLOR_MODES[(COLOR_MODES.index(self.color_mode) + 1) % len(COLOR_MODES)]
        )

    def _change_point_size(self, delta):
        self.set_point_size(max(1, min(20, self.point_size + delta)))

    def _matcher(self, entry, basis):
        # Offset is cheap to apply to the query; retain one matcher per stream
        # and time basis instead of a full copy for every spinner value.
        key = (entry.key, basis)
        if key not in self._matcher_cache:
            self._matcher_cache[key] = TimestampMatcher(entry.frames, basis)
        return self._matcher_cache[key]

    def _start_read(self, prefetch_position=None):
        if self.tracking.worker is not None:
            return
        background = prefetch_position is not None
        if background:
            generation = self._generation
        else:
            self._apply_topic_reloads()
            generation, self._pending = self._pending, None
            if self._use_prepared(generation):
                return
        entry = self.entry(self.primary.currentData())
        if self._stopping or entry is None or not entry.frames:
            self.current_frames = []
            self.left.clear("当前选择范围内没有消息")
            self.right.clear()
            self.status.clear()
            self.status.hide()
            self.calibration_panel.set_calibration(self.calibration, self._calibration_origin)
            self.frame_changed.emit(None)
            return
        position = prefetch_position if background else self.position.value() - 1
        prepared_key = self._prepared_key(position)
        ref = entry.frames[position]
        if not background:
            self._read_started_at = time.monotonic()
        secondary = (
            self.entry(self.secondary.currentData())
            if self.compare.isChecked()
            else None
        )
        basis = self.basis.currentData()
        timestamp = ref.record_time_ns if basis == "record" else ref.timestamp_ns
        match = None
        match_status = ""
        clock_name = "记录 / 抓包时间" if basis == "record" else "设备 / 消息头时间"
        if secondary is not None and timestamp is None:
            match_status = f"未匹配：主视图缺少{clock_name}，可切换时间基准。"
        elif secondary is not None:
            nearest = self._matcher(secondary, basis).closest(
                timestamp - round(self.offset.value() * 1e6),
                None,
            )
            if not secondary.frames:
                match_status = "未匹配：对照数据流在当前导入范围内没有帧。"
            elif nearest is None:
                match_status = f"未匹配：对照数据流缺少{clock_name}，可切换时间基准。"
            elif abs(nearest[1]) > round(self.tolerance.value() * 1e6):
                match_status = (f"未匹配：最近帧差 {nearest[1] / 1e6:+.3f} ms，"
                                f"超出容差 {self.tolerance.value():.3f} ms。")
            else:
                match = nearest
        camera_entry = self.entry(self.camera.currentData())
        calibration = self.calibration
        calibration_key = self._calibration_key
        overlay, undistort = self.overlay.isChecked(), self.undistort.isChecked()
        colorize = self.calibration_panel.colorize.isChecked()
        projection_style = self.calibration_panel.display_options()
        page_offsets = {
            key: offsets[self._page_positions.get(key, 0)]
            for key, offsets in self._page_offsets.items()
        }
        camera_index = None
        if camera_entry and camera_entry.frames:
            if len(camera_entry.frames) == 1:
                camera_index = camera_entry.frames[0].index
            else:
                image_time = timestamp
                if secondary and secondary.stream.kind == "image" and match:
                    secondary_ref = next(
                        frame for frame in secondary.frames if frame.index == match[0]
                    )
                    image_time = (
                        secondary_ref.record_time_ns
                        if basis == "record"
                        else secondary_ref.timestamp_ns
                    )
                if image_time is not None:
                    camera_match = self._matcher(camera_entry, basis).closest(
                        image_time, round(self.tolerance.value() * 1e6)
                    )
                    camera_index = camera_match[0] if camera_match else None

        def read(cancel, _progress):
            for source_entry in (entry, secondary, camera_entry):
                if (
                    source_entry is not None
                    and source_entry.source.cache_token() != source_entry.cache_identity
                ):
                    raise ValueError(
                        "源文件已更改，请使用「更多 → 重新索引整个数据源」更新帧数和时间索引。"
                    )
            frames = []
            requests = [(entry, ref.index), (secondary, match[0] if match else None)]
            for source_entry, index in requests:
                if source_entry is None or index is None:
                    frames.append(None)
                    continue
                try:
                    frames.append(
                        self._read_cached(
                            source_entry,
                            index,
                            cancel,
                            page_offsets.get(source_entry.key, 0),
                        )
                    )
                except Exception as exc:
                    if cancel():
                        return None
                    frames.append(str(exc))
            current_calibration = calibration
            error = ""
            key = (*camera_entry.key, camera_index) if camera_entry else None
            if camera_entry and camera_index is None:
                current_calibration = None
                error = "当前图像没有匹配的相机参数，请调整时间来源或容差。"
            elif camera_entry and key != calibration_key:
                try:
                    camera_frame = self._read_cached(camera_entry, camera_index, cancel)
                    current_calibration = from_camera_info(camera_frame.value)
                except Exception as exc:
                    current_calibration = None
                    error = f"相机参数无法用于投影或去畸变  {exc}"
            pixels = colors = image_colors = None
            prepared_image = None
            projection_calibration = None
            try:
                image_index = next(
                    (
                        i
                        for i, frame in enumerate(frames)
                        if isinstance(frame, FrameData) and frame.kind == "image"
                    ),
                    None,
                )
                cloud = next(
                    (
                        frame.value
                        for frame in frames
                        if isinstance(frame, FrameData) and frame.kind == "pointcloud"
                    ),
                    None,
                )
                if image_index is not None and current_calibration is not None:
                    image_frame = frames[image_index]
                    image = image_frame.value
                    projection_calibration = current_calibration
                    if undistort:
                        import cv2

                        width, height = image.shape[1], image.shape[0]
                        matrix = current_calibration.K.copy()
                        matrix[0] *= width / current_calibration.width
                        matrix[1] *= height / current_calibration.height
                        if current_calibration.distortion_model == "equidistant":
                            image = cv2.fisheye.undistortImage(
                                image,
                                matrix,
                                current_calibration.D,
                                Knew=matrix,
                                new_size=(width, height),
                            )
                        else:
                            image = cv2.undistort(image, matrix, current_calibration.D)
                        frames[image_index] = replace(image_frame, value=image)
                        projection_calibration = replace(
                            current_calibration,
                            D=np.zeros(0),
                            distortion_model="plumb_bob",
                        )
                    if overlay and cloud is not None and projection_calibration.T_camera_lidar is not None:
                        points = cloud.xyz
                        if len(points) > 50_000:
                            points = points[
                                np.linspace(0, len(points) - 1, 50_000).astype(int)
                            ]
                        pixels, visible = project_points(
                            points,
                            projection_calibration,
                            (image.shape[1], image.shape[0]),
                        )
                        colors = projection_colors(points[visible], projection_calibration,
                                                   projection_style["color"])
                    if colorize and cloud is not None and projection_calibration.T_camera_lidar is not None:
                        image_colors = sample_image_colors(cloud.xyz, image, projection_calibration)
                    if not cancel():
                        prepared_image = render_projection_image(
                            image, pixels, colors, projection_style["size"], projection_style["opacity"])
            except Exception as exc:
                error = f"标定显示失败  {exc}"
            return (generation, frames, match, current_calibration, key, error, pixels, colors,
                    projection_style, match_status, image_colors, projection_calibration, prepared_image)

        if not background:
            self.previous.setEnabled(position > 0)
            self.next.setEnabled(position + 1 < len(entry.frames))
            self.status.setText("正在读取当前帧…")
            self.status.show()
        self._worker = RecordingTask(read, self)
        self._worker.background = background
        self._worker.request_generation = generation
        self._worker.done.connect(lambda result: self._remember_prepared(prepared_key, result))
        if background:
            self._worker.failed.connect(lambda _message: self._mark_prefetch_failed(prepared_key))
        if not background:
            self._worker.done.connect(self._show_result)
            self._worker.failed.connect(self._show_error)
        self._worker.finished.connect(self._read_finished)
        if background:
            self.tracking.refresh_reader_actions()
        self._worker.start()

    def _show_result(self, result):
        if result is None or result[0] != self._generation or self._stopping:
            return
        (_, frames, match, calibration, key, error, pixels, colors, projection_style,
         match_status, image_colors, projection_calibration, *prepared_images) = result
        self._last_frame_error = error
        self.calibration = calibration
        self._calibration_key = key
        self._source_frames = frames
        self._image_colors = image_colors
        self._display_calibration = projection_calibration
        frames = [self._cloud_display_frame(frame) for frame in frames]
        self.current_frames = frames
        primary_entry = self.entry(self.primary.currentData())
        self._displayed_indices = [
            primary_entry.frames[self.position.value() - 1].index
            if primary_entry and isinstance(frames[0], FrameData) else None,
            match[0] if match and isinstance(frames[1], FrameData) else None,
        ]
        for view, frame, combo in (
            (self.left, frames[0], self.primary),
            (self.right, frames[1], self.secondary),
        ):
            entry = self.entry(combo.currentData())
            if isinstance(frame, FrameData):
                view.show_frame(
                    frame, entry.label, pixels if frame.kind == "image" else None,
                    colors=colors, point_size=projection_style["size"], opacity=projection_style["opacity"],
                    source_key=entry.key,
                    prepared_image=prepared_images[0] if prepared_images and frame.kind == "image" else None,
                )
                view.set_page_state(
                    self._page_positions.get(entry.key, 0) + 1,
                    os.path.isfile(entry.source.path)
                    and os.path.splitext(entry.source.path)[1].lower()
                    in (".txt", ".csv", ".tsv"),
                )
                view.as_cloud.setEnabled(True)
            else:
                view.clear(
                    frame
                    if isinstance(frame, str)
                    else match_status or "请选择对照数据流"
                )
        primary = frames[0]
        messages = []
        if self.compare.isChecked():
            if isinstance(frames[0], str):
                messages.append("主视图读取失败，请查看视图内提示。")
            elif isinstance(frames[1], str):
                messages.append("对照帧读取失败，请查看视图内提示。")
            else:
                messages.append(f"右侧时间差 {match[1] / 1e6:+.3f} ms" if match else match_status or "未匹配")
        if pixels is not None:
            messages.append(f"投影预览 {len(pixels):,} 点（最多采样 50,000 点）")
        if image_colors is not None and self.calibration_panel.colorize.isChecked():
            messages.append(f"图像赋色 {len(image_colors[1]):,} 点")
        if error:
            messages.append(error)
        self.status.setText(" · ".join(messages))
        self.status.setVisible(bool(messages))
        self.status_changed.emit(self.status.text())
        self.calibration_panel.set_calibration(
            calibration,
            self.camera.currentText() if self.camera.currentData() else self._calibration_origin,
            error,
            has_image=any(isinstance(frame, FrameData) and frame.kind == "image" for frame in frames),
            has_cloud=any(isinstance(frame, FrameData) and frame.kind == "pointcloud" for frame in frames),
        )
        self.scene_button.setEnabled(
            any(
                isinstance(frame, FrameData) and frame.kind == "pointcloud"
                for frame in frames
            )
        )
        self.export_button.setEnabled(isinstance(primary, FrameData))
        self.export_primary_action.setEnabled(isinstance(primary, FrameData))
        self.export_secondary_action.setEnabled(isinstance(frames[1], FrameData))
        self.swap_button.setEnabled(self.compare.isChecked() and isinstance(frames[1], FrameData))
        self.tracking_button.setEnabled(any(view.cloud is not None for view in (self.left, self.right)))
        self._sync_cloud_display()
        self.frame_changed.emit(primary if isinstance(primary, FrameData) else None)
        if self._open_box_on_frame:
            self._open_box_on_frame = False
            if self.left.cloud is not None:
                self.tracking.show()

    def _show_error(self, message, generation=None):
        if generation is None:
            generation = getattr(self.sender(), "request_generation", self._generation)
        if self._stopping or generation != self._generation:
            return
        self._last_frame_error = message
        self.status.setText(message)
        self.status.show()
        self.play.setChecked(False)
        self.current_frames = []
        self._source_frames = []
        self._image_colors = None
        self._display_calibration = None
        self.left.clear(message)
        self.right.clear(message)
        self._sync_cloud_display()
        self.export_button.setEnabled(False)
        self.scene_button.setEnabled(False)
        self.frame_changed.emit(None)

    def _read_finished(self):
        if self._worker is None or (self.sender() is not None and self.sender() is not self._worker):
            return
        worker, self._worker = self._worker, None
        worker.deleteLater()
        if self._pending is not None and not self._stopping:
            self._start_read()
        elif not self._stopping:
            if (not getattr(worker, 'background', False)
                    and getattr(worker, 'request_generation', None) == self._generation):
                self.tracking.on_frame_changed(self.current_frames[0] if self.current_frames else None)
            else:
                self.tracking.refresh_reader_actions()
            self._schedule_next()
            self._schedule_prefetch()

    def _import_calibration(self):
        self.play.setChecked(False)
        path, _ = QFileDialog.getOpenFileName(
            self, "导入相机内参 / 雷达到相机外参", "", CALIBRATION_FILE_FILTER
        )
        if path:
            try:
                dialog = CalibrationImportDialog(
                    read_calibration_bundle(path), self.calibration,
                    theme=self.calibration_panel._theme, parent=self,
                    base_origin=self._calibration_origin or self.camera.currentText(),
                )
                if dialog.exec() == QDialog.Accepted:
                    self._apply_calibration(dialog.calibration, dialog.origin)
                dialog.deleteLater()
            except Exception as exc:
                QMessageBox.warning(self, "无法读取标定", str(exc))

    def _edit_intrinsics(self):
        self.play.setChecked(False)
        image = next((frame.value for frame in self._source_frames
                      if isinstance(frame, FrameData) and frame.kind == "image"), None)
        dialog = IntrinsicsDialog(
            self.calibration, image_size=(image.shape[1], image.shape[0]) if image is not None else None,
            theme=self.calibration_panel._theme, parent=self,
        )
        if dialog.exec() == QDialog.Accepted:
            origin = self._calibration_origin or (
                self.camera.currentText() if self.camera.currentData() else "手动输入")
            if not origin.startswith("手动修正内参"):
                origin = f"手动修正内参；原始来源：{origin}"
            self._apply_calibration(dialog.calibration, origin)
        dialog.deleteLater()

    def _edit_extrinsics(self):
        if self.calibration is None:
            return
        self.play.setChecked(False)
        dialog = ExtrinsicsDialog(self.calibration, theme=self.calibration_panel._theme, parent=self)
        if dialog.exec() == QDialog.Accepted:
            origin = self._calibration_origin or self.camera.currentText() or "手动输入"
            if not origin.startswith("手动修正外参"):
                origin = f"手动修正外参；原始来源：{origin}"
            self._apply_calibration(dialog.calibration, origin)
        dialog.deleteLater()

    def _apply_calibration(self, calibration, origin):
        """Freeze corrected parameters and invalidate every in-flight projection."""
        self.play.setChecked(False)
        self.calibration = calibration
        self._calibration_origin = origin
        blocker = QSignalBlocker(self.camera)
        self.camera.setCurrentIndex(0)
        blocker.unblock()
        self._calibration_key = None
        self.calibration_panel.set_calibration(
            calibration, origin,
            has_image=any(isinstance(frame, FrameData) and frame.kind == "image" for frame in self._source_frames),
            has_cloud=any(isinstance(frame, FrameData) and frame.kind == "pointcloud" for frame in self._source_frames),
        )
        self._request_frame()

    def _save_calibration(self):
        if self.calibration is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存标定", "calibration.json", "JSON (*.json)")
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += ".json"
        output = QSaveFile(path)
        try:
            parameters = calibration_to_dict(self.calibration)
            parameters["calibration_source"] = self._calibration_origin or (
                self.camera.currentText() if self.camera.currentData() else "")
            data = json.dumps(parameters, ensure_ascii=False,
                              indent=2, allow_nan=False).encode("utf-8")
            if not output.open(QIODevice.WriteOnly):
                raise OSError(output.errorString())
            if output.write(data) != len(data) or not output.commit():
                raise OSError(output.errorString())
            self.status.setText(f"已保存标定 {path}")
            self.status.show()
        except Exception as exc:
            output.cancelWriting()
            QMessageBox.warning(self, "无法保存标定", str(exc))

    def _reset_calibration(self):
        for widget in (self.camera, self.overlay, self.undistort, self.calibration_panel.colorize):
            widget.blockSignals(True)
        self.camera.setCurrentIndex(0)
        self.overlay.setChecked(False)
        self.undistort.setChecked(False)
        self.calibration_panel.colorize.setChecked(False)
        for widget in (self.camera, self.overlay, self.undistort, self.calibration_panel.colorize):
            widget.blockSignals(False)
        self.calibration = None
        self._calibration_origin = ""
        self._calibration_key = None
        self.calibration_panel.set_calibration(None)
        self._request_frame()

    def set_color_mode(self, mode):
        self.color_mode = mode if mode in COLOR_MODES else "height"
        if not self._building:
            blocker = QSignalBlocker(self.calibration_panel.colorize)
            self.calibration_panel.colorize.setChecked(False)
            blocker.unblock()
        self._refresh_cloud_display()

    def set_colormap(self, name):
        self.colormap = name if name in list_colormaps() else "default"
        self._refresh_cloud_display()

    def set_color_field(self, name):
        self.color_field = str(name or "")
        self.set_color_mode("field" if name else "height")

    def set_color_range(self, value):
        if value is None:
            self.color_range = None
        else:
            values = np.asarray(value, dtype=float)
            if values.shape != (2,) or not np.isfinite(values).all() or values[0] >= values[1]:
                return
            self.color_range = tuple(map(float, values))
        self._refresh_cloud_display()

    def _cloud_display_frame(self, frame):
        if not isinstance(frame, FrameData) or frame.kind != "pointcloud":
            return frame
        cloud = replace(frame.value, fields=dict(frame.value.fields), color_mode=self.color_mode,
                        colormap=self.colormap, color_field=self.color_field,
                        color_range=self.color_range, point_size=self.point_size)
        if cloud.color_mode == "field" and cloud.color_field not in cloud.fields:
            cloud.color_field = next((name for name, field in cloud.fields.items()
                                      if field.data.ndim == 1), "")
        if self.calibration_panel.colorize.isChecked() and self._image_colors is not None:
            samples, indices = self._image_colors
            rgb = compute_colors(cloud)
            if rgb is None:
                base = (.22, .25, .30) if self.left.theme == "light" else (.85, .85, .88)
                rgb = np.tile(np.array(base, np.float32), (len(cloud.xyz), 1))
            else:
                rgb = rgb.copy()
            rgb[indices] = samples
            cloud.fields["rgb"] = ScalarField("rgb", rgb, "color")
            cloud.color_mode = "rgb"
        return replace(frame, value=cloud)

    def _sync_cloud_display(self):
        cloud = next((view.cloud for view in (self.left, self.right) if view.cloud is not None), None)
        self.cloud_display.sync(cloud, self.color_mode, self.colormap, self.color_field,
                                self.color_range, self.point_size,
                                self.calibration_panel.colorize.isChecked() and self._image_colors is not None)

    def _refresh_cloud_display(self):
        if self._building:
            return
        for side, view in enumerate((self.left, self.right)):
            if side < len(self._source_frames):
                frame = self._cloud_display_frame(self._source_frames[side])
                if isinstance(frame, FrameData) and frame.kind == "pointcloud":
                    # Preserve the active preview object so an armed box or
                    # selected points still refer to the same cloud after color edits.
                    if view.cloud is not None and view.cloud.xyz is frame.value.xyz:
                        for name in ("fields", "color_mode", "colormap", "color_field", "color_range", "point_size"):
                            setattr(view.cloud, name, getattr(frame.value, name))
                        frame = replace(frame, value=view.cloud)
                    self.current_frames[side] = frame
                    view.frame = frame
                    view.cloud = frame.value
                    view.cloud_view.set_entities([view.cloud])
        self._sync_cloud_display()
        # set_entities resets the viewport's annotation layer. Restore its
        # current boxes without disarming an in-progress selection or rereading.
        if hasattr(self, "tracking"):
            self.tracking._refresh()
        self.display_changed.emit()

    def _update_image_boxes(self, boxes=None):
        if boxes is not None:
            self._annotation_boxes = boxes
        calibration = self._display_calibration
        # The annotated cloud may be on the right after swapping panes. Editing
        # still uses the primary cloud; its existing boxes remain visible here.
        if (self._source_frames and self.right.cloud is not None
                and self._displayed_indices[1] is not None):
            entry = self.entry(self.secondary.currentData())
            index = self._displayed_indices[1]
            records = []
            if entry is not None and self.tracking._source_matches(entry, notify=False):
                stream = self.tracking._streams.get(self.tracking._key(entry), {})
                for track in stream.get("tracks", {}).values():
                    observation = track.observations.get(index)
                    if observation is not None:
                        records.append({"id": track.id[:8], "name": track.name, "category": track.category,
                                        **observation.box.to_dict(), "manual": observation.manual,
                                        "selected": track.id == self.tracking.selected_id})
            self._annotation_boxes = records
            if hasattr(self.right.cloud_view, "set_annotation_boxes"):
                self.right.cloud_view.set_annotation_boxes(records)
        for view in (self.left, self.right):
            projected = []
            if (view.frame is not None and view.frame.kind == "image" and calibration is not None
                    and calibration.T_camera_lidar is not None
                    and self.calibration_panel.box_overlay.isChecked()):
                image = view.frame.value
                for box in self._annotation_boxes:
                    segments = project_box_segments(Box3D.from_dict(box), calibration,
                                                     (image.shape[1], image.shape[0]))
                    if len(segments):
                        projected.append({**box, "segments": segments})
            view.image.set_projected_boxes(projected)

    def set_point_size(self, size):
        self.point_size = size
        for view in (self.left, self.right):
            if view.cloud is not None:
                view.cloud.point_size = size
                view.cloud_view.set_point_size()
        self._sync_cloud_display()
        self.display_changed.emit()

    def _add_cloud(self):
        side = next(
            (
                side
                for side, frame in enumerate(self.current_frames)
                if isinstance(frame, FrameData) and frame.kind == "pointcloud"
            ),
            None,
        )
        if side is not None and self._displayed_indices[side] is not None:
            from copy import deepcopy

            cloud = deepcopy(self.current_frames[side].value)
            cloud.path = None
            cloud.name = f"{cloud.name} · 帧 {self._displayed_indices[side] + 1}"
            self.cloud_to_scene.emit(cloud)

    def export_current(self, side=0):
        if side not in (0, 1) or len(self.current_frames) <= side or not isinstance(self.current_frames[side], FrameData):
            return
        frame = self.current_frames[side]
        suffix = {
            "image": ".png",
            "pointcloud": ".ply",
            "text": ".txt",
            "table": ".csv",
        }.get(frame.kind, ".json")
        entry = self.entry((self.primary if side == 0 else self.secondary).currentData())
        original_index = self._displayed_indices[side]
        if entry is None or original_index is None:
            return
        stamp = (
            frame.timestamp_ns
            if frame.timestamp_ns is not None
            else frame.record_time_ns
        )
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", entry.stream.name.strip("/"))
        name = name.strip(" .")[:120] or "frame"
        default = f"{name}_{original_index:06d}_{stamp if stamp is not None else 'unknown'}{suffix}"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出当前显示帧 / 文本预览", default, f"当前格式 (*{suffix})"
        )
        if not path:
            return
        if not os.path.splitext(path)[1]:
            path += suffix
        try:
            if frame.kind == "pointcloud":
                from core.io.exporters import export_entity

                export_entity(self.export_cloud_entity(side), path)
            elif frame.kind == "image":
                from ui.widgets.data_view import image_to_qimage

                view = self.left if side == 0 else self.right
                if not image_to_qimage(view.image.snapshot_rgb()).save(path):
                    raise ValueError("图像保存失败")
            elif frame.kind == "table":
                import csv

                with open(path, "w", encoding="utf-8-sig", newline="") as output:
                    writer = csv.writer(output)
                    writer.writerow(frame.value["columns"])
                    writer.writerows(frame.value["rows"])
            else:
                with open(path, "w", encoding="utf-8") as output:
                    if isinstance(frame.value, str):
                        output.write(frame.value)
                    else:
                        json.dump(
                            {
                                "timestamp_ns": frame.timestamp_ns,
                                "record_time_ns": frame.record_time_ns,
                                "frame_id": frame.frame_id,
                                "data": frame.value,
                            },
                            output,
                            ensure_ascii=False,
                            indent=2,
                        )
            self.status.setText(f"已导出 {path}")
            self.status.show()
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def export_cloud_entity(self, side):
        """Keep sensor colors outside the camera; display colormaps are not data."""
        frame = self._source_frames[side]
        if not isinstance(frame, FrameData) or frame.kind != "pointcloud":
            raise ValueError("当前帧没有可导出的点云")
        cloud = frame.value
        if not self.calibration_panel.colorize.isChecked() or self._image_colors is None:
            return cloud
        fields = dict(cloud.fields)
        rgb = fields.get("rgb")
        colors = (np.array(rgb.data, dtype=np.float32, copy=True) if rgb is not None else
                  np.full((len(cloud.xyz), 3), .5, np.float32))
        samples, indices = self._image_colors
        colors[indices] = samples
        valid = np.zeros(len(cloud.xyz), np.uint8)
        valid[indices] = 1
        fields["rgb"] = ScalarField("rgb", colors, "color")
        fields["image_color_valid"] = ScalarField("image_color_valid", valid)
        return replace(cloud, fields=fields, color_mode="rgb")

    def state(self):
        return {
            "primary": self.primary.currentData(),
            "secondary": self.secondary.currentData(),
            "position": self.position.value(),
            "compare": self.compare.isChecked(),
            "basis": self.basis.currentData(),
            "tolerance_ms": self.tolerance.value(),
            "offset_ms": self.offset.value(),
            "camera": self.camera.currentData(),
            "calibration_origin": self._calibration_origin,
            "calibration": (
                calibration_to_dict(self.calibration)
                if self.calibration is not None and self.camera.currentData() is None
                else None
            ),
            "overlay": self.overlay.isChecked(),
            "undistort": self.undistort.isChecked(),
            "color_mode": self.color_mode,
            "colormap": self.colormap,
            "color_field": self.color_field,
            "color_range": self.color_range,
            "image_colorize": self.calibration_panel.colorize.isChecked(),
            "box_overlay": self.calibration_panel.box_overlay.isChecked(),
            "point_size": self.point_size,
            "projection_style": self.calibration_panel.display_options(),
            "frame_selection": list(self.slider.selection()),
            "selection_enabled": self.slider.selectionEnabled(),
        }

    def restore_state(self, state):
        self._building = True
        self.play.setChecked(False)
        for combo, key in (
            (self.primary, "primary"),
            (self.secondary, "secondary"),
            (self.camera, "camera"),
        ):
            value = state.get(key)
            index = _find_key(combo, value)
            if index >= 0:
                combo.setCurrentIndex(index)
        entry = self.entry(self.primary.currentData())
        count = len(entry.frames) if entry else 0
        self.position.setRange(1, max(count, 1))
        self.slider.setRange(0, max(count - 1, 0))
        selection = state.get("frame_selection", [0, max(count - 1, 0)])
        self.slider.setSelection(*selection)
        self.range_button.setChecked(bool(state.get("selection_enabled", False)))
        self._range_changed(*self.slider.selection())
        self.total.setText(f"/ {count:,}")
        for widget in (self.position, self.slider, self.play):
            widget.setEnabled(count > 0)
        self.basis.setCurrentIndex(
            max(0, self.basis.findData(state.get("basis", "record")))
        )
        self.compare.setChecked(bool(state.get("compare")))
        self._pair = (
            self.primary.currentData(),
            self.secondary.currentData() if self.compare.isChecked() else None,
        )
        self.right.setVisible(self.compare.isChecked())
        self.secondary.setEnabled(self.compare.isChecked())
        self.tolerance.setValue(state.get("tolerance_ms", 50))
        self.offset.setValue(state.get("offset_ms", 0))
        self.position.setValue(state.get("position", 1))
        self.calibration = (
            calibration_from_dict(state["calibration"])
            if state.get("calibration")
            else None
        )
        self._calibration_origin = str(state.get("calibration_origin", "")) if self.calibration else ""
        self._calibration_key = None
        self.overlay.setChecked(bool(state.get("overlay")))
        self.undistort.setChecked(bool(state.get("undistort")))
        self.set_color_mode(state.get("color_mode", "height"))
        self.set_colormap(state.get("colormap", "default"))
        self.color_field = str(state.get("color_field", ""))
        self.set_color_range(state.get("color_range"))
        self.calibration_panel.colorize.setChecked(bool(state.get("image_colorize")))
        self.calibration_panel.box_overlay.setChecked(bool(state.get("box_overlay", True)))
        self.set_point_size(state.get("point_size", 2.0))
        self.calibration_panel.set_display_options(state.get("projection_style", {}))
        self._building = False
        self._update_context_controls()
        self._update_transport()
        if entry:
            self.stream_selected.emit(entry.key)
        self._request_frame()

    def set_theme(self, theme):
        self._style_chrome(theme)
        for view in (self.left, self.right):
            view.set_theme(theme)
        if self.tracking.dialog is not None:
            self.tracking.dialog.editor.set_theme(theme)
        if self.calibration_panel.colorize.isChecked() and self.color_mode == "none":
            self._refresh_cloud_display()

    def cancel(self):
        self.exporter.cancel()
        self._prefetch_timer.stop()
        self._stopping = True
        self._pending = None
        self._reload_topics.clear()
        self.timer.stop()
        self.tracking.cancel()
        if self._worker is not None:
            self._worker.requestInterruption()

    def clear(self):
        self._clear_prepared()
        self.tracking.clear()
        self._cache.clear()
        self._cache_bytes = 0
        self._page_offsets.clear()
        self._page_positions.clear()
        self.calibration = None
        self._calibration_origin = ""
        self._calibration_key = None
        self.set_entries([])

    def stop(self):
        self.settings_panel.close()
        self.tracking.stop()
        self.cancel()
        self.left.stop()
        self.right.stop()

    def background_threads(self):
        return [worker for worker in (self._worker, self.exporter.worker) if worker is not None]
