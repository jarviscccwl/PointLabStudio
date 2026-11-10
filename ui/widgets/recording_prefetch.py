"""Bounded prepared frames, sharing the browser's single source-reader lane."""
from collections import OrderedDict
from contextlib import contextmanager
import json
import os

from PySide6.QtCore import QTimer
from core.calibration import calibration_to_dict
from core.io.recording_types import FrameData


class PreparedFramesMixin:
    def _init_prepared_frames(self):
        self._prepared = OrderedDict()
        self._prefetch_failed = OrderedDict()
        self._prepared_bytes = 0
        self._prepared_hits = 0
        self._prefetch_holds = 0
        self._navigation_direction = 1
        self._last_requested_position = 1
        self._prefetch_timer = QTimer(self)
        self._prefetch_timer.setSingleShot(True)
        self._prefetch_timer.timeout.connect(self._start_prefetch)

    def _clear_prepared(self):
        self._prefetch_timer.stop()
        self._prepared.clear()
        self._prefetch_failed.clear()
        self._prepared_bytes = 0

    @contextmanager
    def pause_prefetch(self):
        """Hold new neighbor reads while a modal source action is configured."""
        self._prefetch_holds += 1
        self._prefetch_timer.stop()
        try:
            yield
        finally:
            self._prefetch_holds -= 1
            if not self._prefetch_holds:
                self._schedule_prefetch()

    def _prepared_key(self, position):
        primary = self.entry(self.primary.currentData())
        if primary is None or not 0 <= position < len(primary.frames):
            return None
        basis = self.basis.currentData()
        ref = primary.frames[position]
        stamp = ref.record_time_ns if basis == "record" else ref.timestamp_ns
        selected = [(primary, ref.index)]
        secondary = self.entry(self.secondary.currentData()) if self.compare.isChecked() else None
        if secondary is not None and stamp is not None:
            match = self._matcher(secondary, basis).closest(
                stamp - round(self.offset.value() * 1e6), round(self.tolerance.value() * 1e6))
            selected.append((secondary, match[0] if match else None))
        camera = self.entry(self.camera.currentData())
        sources = []
        for entry, index in selected + ([(camera, None)] if camera is not None else []):
            try:
                token = entry.source.cache_token()
            except OSError:
                return None
            if token != entry.cache_identity:
                return None
            locator_token = None
            refs = entry.source.index.get(entry.stream.id, [])
            if index is not None and index < len(refs) and isinstance(refs[index].locator, str):
                try:
                    stat = os.stat(refs[index].locator)
                    locator_token = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    return None
            sources.append((entry.key, token, index, locator_token))
        calibration = (calibration_to_dict(self.calibration)
                       if camera is None and self.calibration is not None else None)
        return json.dumps({
            "sources": sources, "position": position, "secondary": self.secondary.currentData(),
            "compare": self.compare.isChecked(), "camera": self.camera.currentData(), "basis": basis,
            "tolerance": self.tolerance.value(), "offset": self.offset.value(), "calibration": calibration,
            "overlay": self.overlay.isChecked(), "undistort": self.undistort.isChecked(),
            "colorize": self.calibration_panel.colorize.isChecked(),
            "style": self.calibration_panel.display_options(),
            "pages": sorted((str(key), self._page_positions.get(key, 0)) for key in self._page_offsets),
        }, sort_keys=True, default=str)

    def _remember_prepared(self, key, result):
        if not key or result is None or result[5] or any(isinstance(f, str) for f in result[1]):
            if key and result is not None:
                self._mark_prefetch_failed(key)
            return
        from ui.widgets.recording_browser import _frame_bytes
        size = sum(_frame_bytes(f) for f in result[1] if isinstance(f, FrameData))
        for item in (result[6], result[7]):
            size += getattr(item, "nbytes", 0)
        if result[10] is not None:
            size += sum(getattr(item, "nbytes", 0) for item in result[10])
        if len(result) > 12 and result[12] is not None:
            size += result[12].sizeInBytes()
        old = self._prepared.pop(key, None)
        if old is not None:
            self._prepared_bytes -= old[1]
        if size > 192 * 1024**2:
            self._mark_prefetch_failed(key)
            return
        self._prepared[key] = (result, size)
        self._prepared_bytes += size
        while len(self._prepared) > 8 or self._prepared_bytes > 192 * 1024**2:
            _, (_, removed) = self._prepared.popitem(last=False)
            self._prepared_bytes -= removed

    def _mark_prefetch_failed(self, key):
        if key is not None:
            self._prefetch_failed[key] = True
            while len(self._prefetch_failed) > 32:
                self._prefetch_failed.popitem(last=False)

    def _use_prepared(self, generation):
        key = self._prepared_key(self.position.value() - 1)
        cached = self._prepared.get(key)
        if cached is None or self._reload_topics:
            return False
        self._prepared.move_to_end(key)
        self._prepared_hits += 1
        self._pending = None
        self._show_result((generation, *cached[0][1:]))
        self._schedule_next()
        self._schedule_prefetch()
        return True

    def _schedule_prefetch(self):
        if (not self._stopping and not self._prefetch_holds and self.isVisible()
                and self.isEnabled() and not getattr(self, "_exporting", False)):
            self._prefetch_timer.start(60)

    def _start_prefetch(self, force=False):
        if (self._stopping or self._building or self._prefetch_holds
                or self._worker is not None or self._pending is not None
                or not self.isEnabled()
                or self.tracking.busy or getattr(self, "_exporting", False)
                or (not force and not self.isVisible())):
            return
        entry = self.entry(self.primary.currentData())
        if entry is None or entry.stream.kind not in ("pointcloud", "image") or not self.current_frames:
            return
        current = self.position.value() - 1
        direction = self._navigation_direction
        for delta in (direction, -direction, 2 * direction):
            position = current + delta
            if 0 <= position < len(entry.frames):
                key = self._prepared_key(position)
                if key is not None and key not in self._prepared and key not in self._prefetch_failed:
                    self._start_read(prefetch_position=position)
                    return
