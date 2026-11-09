"""Main-window integration for inspecting, indexing and restoring recordings."""

import os
from copy import deepcopy

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QMessageBox
from ui.widgets.smooth_progress import SmoothProgressDialog

from core.io.recording import discover_sources, open_source
from core.recording_pairing import suggest_pair
from ui.recording_tasks import RecordingTask
from ui.widgets.recording_browser import BrowserStream, filter_frames
from ui.widgets.source_overview import SourceOverviewDialog


class RecordingSessionMixin:
    def _init_recording_session(self):
        self._recording_jobs = set()
        self._recording_queue = []
        self._recording_importing = False
        self._recording_sources = {}
        self._recording_unavailable = {}
        self._recording_unavailable_browser = {}
        self._recording_ranges = {}
        self._recording_dialog = None
        self._recording_progress = None
        self.recording_browser.tracking.changed.connect(self._update_project_title)
        self.recording_browser.tracking.notice.connect(self.log)
        self.recording_browser.tracking.panel_visibility_changed.connect(self._sync_box_tool_state)
        self.recording_browser.stream_selected.connect(
            self._recording_selected_in_browser
        )
        self.recording_browser.cloud_to_scene.connect(self._add_recording_cloud)
        self.recording_browser.frame_changed.connect(self._recording_frame_changed)
        self.recording_browser.display_changed.connect(self._recording_display_changed)
        self.recording_browser.files_dropped.connect(self.open_paths)
        self.recording_browser.text_as_cloud.connect(self._import_numeric_text)
        self.recording_browser.fps_updated.connect(self._recording_fps)
        self.scene_panel.recording_selected.connect(self._select_recording)
        self.scene_panel.recording_remove_requested.connect(self._remove_recording)
        self.recording_browser.source_options_requested.connect(self._configure_recording_source)
        self.recording_browser.source_reload_requested.connect(self._reload_recording_source)
        self.scene_panel.recording_options_requested.connect(self._configure_recording_source)
        self.scene_panel.recording_reload_requested.connect(self._reload_recording_source)
        self.scene_panel.recording_topic_reload_requested.connect(self._reload_recording_topic)

    def _sync_box_tool_state(self, visible):
        if self.content_stack.currentWidget() is self.recording_browser:
            self._sync_guard = True
            self.btn_box_select.setChecked(visible)
            self._sync_guard = False

    def _reload_recording_topic(self, key):
        if self._recording_importing:
            return
        browser = self.recording_browser
        if browser.entry(key) is None:
            return
        self._show_recording_browser()
        active = [browser.primary.currentData()]
        if browser.compare.isChecked():
            active.append(browser.secondary.currentData())
        if key not in active:
            browser.select_stream(key)
        browser.reload_topic(key)

    def _configure_recording_source(self, path):
        if path in self._recording_sources:
            self._open_recording_sources([path], prompt=True, refresh=True)

    def _reload_recording_source(self, path):
        if path in self._recording_sources:
            self._open_recording_sources([path], prompt=False, refresh=True)

    def _recording_threads(self):
        threads = list(self._recording_jobs)
        if self.recording_browser._worker is not None:
            threads.append(self.recording_browser._worker)
        if self.recording_browser.tracking.worker is not None:
            threads.append(self.recording_browser.tracking.worker)
        if (
            self._recording_dialog is not None
            and self._recording_dialog._scan is not None
        ):
            threads.append(self._recording_dialog._scan)
        return threads

    def _start_recording_job(self, function, done, failed, finished=None):
        thread = RecordingTask(function, self)
        self._recording_jobs.add(thread)
        thread.done.connect(done)
        thread.failed.connect(failed)

        def cleanup():
            self._recording_jobs.discard(thread)
            thread.deleteLater()
            if finished is not None:
                finished()

        thread.finished.connect(cleanup)
        thread.start()
        return thread

    def _open_recording_sources(self, paths, prompt=True, restore=None, *, refresh=False):
        if self._closing:
            return
        self._recording_queue.append((list(paths), prompt, restore, refresh))
        self._start_next_recording_import()

    def _start_next_recording_import(self):
        if self._closing or self._recording_importing or not self._recording_queue:
            return
        browser = self.recording_browser
        browser.play.setChecked(False)
        if browser.tracking.worker is not None:
            browser.tracking.cancel()
            QTimer.singleShot(50, self._start_next_recording_import)
            return
        if browser._worker is not None:
            browser._worker.requestInterruption()
            browser._pending = None
            QTimer.singleShot(50, self._start_next_recording_import)
            return
        paths, prompt, restore, refresh = self._recording_queue.pop(0)
        refresh_state = self._recording_state() if refresh else None
        if refresh:
            paths = [path for path in paths if path in self._recording_sources]
            if not paths:
                QTimer.singleShot(0, self._start_next_recording_import)
                return
            entry = browser.entry(browser.primary.currentData())
            refresh_state["original_index"] = (
                entry.frames[browser.position.value() - 1].index if entry and entry.frames else None
            )
        self._recording_importing = True
        browser.setEnabled(False)
        self.status_task.setText("正在识别数据源并读取概览…")
        inspection = SmoothProgressDialog("正在识别数据源并读取概览…", "取消", 0, 0, self)
        inspection.setWindowTitle("读取数据包信息")
        inspection.setMinimumDuration(0)
        inspection.show()
        dispatched = False
        inspected_sources = []

        def inspect(cancel, progress):
            sources, errors = inspected_sources, []
            candidates = discover_sources(paths, cancel)
            for index, path in enumerate(candidates):
                if cancel():
                    break
                source = None
                try:
                    source = open_source(path)
                    source.info = source.inspect(cancel=cancel)
                    sources.append(source)
                except Exception as exc:
                    if source is not None:
                        source.close()
                    errors.append(f"{path}\n{exc}")
                progress((index + 1) / max(len(candidates), 1))
            if cancel():
                for source in sources:
                    source.close()
            return sources, errors

        def ready(result):
            nonlocal dispatched
            dispatched = True
            inspection.close()
            sources, errors = result
            for error in errors:
                self.log("数据源读取失败  " + error)
            if self._closing:
                for source in sources:
                    source.close()
                return
            if not sources:
                self.status_task.setText(
                    "数据源暂时不可用，工作区来源与标注已保留" if restore
                    else "没有可读取的数据源，请查看日志"
                )
                if restore and self._project_path:
                    self._mark_project_saved(self._project_path)
                self._finish_recording_import()
                return
            if errors:
                self.log(f"已跳过 {len(errors)} 个无法识别的数据源，其余可以继续浏览。")
            if restore is not None:
                saved = {
                    os.path.normcase(entry["path"]): entry
                    for entry in restore.get("sources", [])
                }
                chosen = {
                    source.path: saved.get(os.path.normcase(source.path), {}).get(
                        "streams", []
                    )
                    for source in sources
                }
                options = {"sources": chosen, "range": {"mode": "all"}}
            elif prompt:
                self._recording_dialog = SourceOverviewDialog(sources, self)
                if refresh_state:
                    saved = {entry["path"]: entry for entry in refresh_state["sources"]}
                    self._recording_dialog.set_selection({
                        "sources": {source.path: saved.get(source.path, {}).get("streams", [])
                                    for source in sources},
                        "range": saved[paths[0]]["range"],
                    })
                accepted = self._recording_dialog.exec() == QDialog.Accepted
                options = self._recording_dialog.selection()
                self._recording_dialog.deleteLater()
                self._recording_dialog = None
                if not accepted:
                    for source in sources:
                        source.close()
                    self.status_task.setText("已取消选择数据包内容")
                    self._finish_recording_import()
                    return
            elif refresh_state:
                saved = {entry["path"]: entry for entry in refresh_state["sources"]}
                options = {
                    "sources": {source.path: saved.get(source.path, {}).get("streams", [])
                                for source in sources},
                    "range": saved[paths[0]]["range"],
                }
            else:
                options = {
                    "sources": {
                        source.path: [stream.id for stream in source.info.streams]
                        for source in sources
                    },
                    "range": {"mode": "all"},
                }
            self._index_recording_sources(sources, options, restore, refresh_state)

        def failed(message):
            nonlocal dispatched
            dispatched = True
            self.log("识别数据源失败  " + message)
            self.status_task.setText(message)
            self._finish_recording_import()

        def finished():
            inspection.close()
            inspection.deleteLater()
            if not dispatched:
                for source in inspected_sources:
                    source.close()
                self.status_task.setText("读取数据包信息已取消")
                self._finish_recording_import()

        worker = self._start_recording_job(inspect, ready, failed, finished)
        inspection.canceled.connect(worker.requestInterruption)

    def _index_recording_sources(self, sources, options, restore=None, refresh_state=None):
        chosen = options["sources"]
        selected = [source for source in sources if chosen.get(source.path)]
        for source in sources:
            if source not in selected:
                source.close()
        accepted = []
        self.status_task.setText("正在建立帧索引，首次打开大包可能需要几分钟…")
        self._recording_progress = SmoothProgressDialog(
            "正在建立可浏览的帧索引…", "取消", 0, 100, self
        )
        self._recording_progress.setWindowTitle("准备数据浏览")
        self._recording_progress.setMinimumDuration(0)
        self._recording_progress.setAutoClose(False)
        self._recording_progress.setAutoReset(False)
        self._recording_progress.show()

        def index_sources(cancel, progress):
            errors = []
            for index, source in enumerate(selected):
                if cancel():
                    break
                try:
                    available = {stream.id for stream in source.info.streams}
                    ids = [
                        stream_id
                        for stream_id in chosen[source.path]
                        if stream_id in available
                    ]
                    if not ids:
                        raise ValueError("保存的数据流在此文件中已不存在")
                    source.build_index(
                        ids,
                        cancel=cancel,
                        progress=lambda value, i=index: progress(
                            (i + value) / len(selected)
                        ),
                    )
                    accepted.append((source, ids))
                except Exception as exc:
                    if cancel():
                        break
                    errors.append(f"{source.path}\n{exc}")
            return accepted, errors

        def indexed(result):
            completed, errors = result
            for error in errors:
                self.log("建立索引失败  " + error)
            if self._closing:
                return
            if refresh_state and not completed:
                self.status_task.setText("重新读取未完成，已保留原有数据源；详情见日志。")
                return
            all_times = [
                frame.record_time_ns
                for source, ids in completed
                for stream_id in ids
                for frame in source.index.get(stream_id, [])
                if frame.record_time_ns is not None
            ]
            origin = min(all_times, default=0)
            entries = list(self.recording_browser.entries)
            saved = (
                {
                    os.path.normcase(entry["path"]): entry
                    for entry in restore.get("sources", [])
                }
                if restore
                else {}
            )
            for source, ids in completed:
                bounds = dict(
                    saved.get(os.path.normcase(source.path), {}).get(
                        "range", options["range"]
                    )
                )
                bounds.setdefault("origin_ns", origin)
                previous = self._recording_sources.get(source.path)
                entries = [
                    entry for entry in entries if entry.source.path != source.path
                ]
                if previous is not None:
                    previous.close()
                self._recording_sources[source.path] = source
                self._recording_unavailable.pop(os.path.normcase(source.path), None)
                self._recording_ranges[source.path] = bounds
                self.recording_browser.reset_source_pages(source.path)
                for stream in source.info.streams:
                    if stream.id in ids:
                        indexed_frames = source.index.get(stream.id, [])
                        frames = (
                            indexed_frames
                            if stream.kind == "camera_info" and len(indexed_frames) == 1
                            else filter_frames(
                                indexed_frames, bounds, bounds["origin_ns"]
                            )
                        )
                        entries.append(BrowserStream(source, stream, frames))
                self.settings.add_recent(source.path)
            self.recording_browser._cache.clear()
            self.recording_browser._cache_bytes = 0
            browser_state = None
            if refresh_state:
                browser_state = dict(refresh_state["browser"])
                for field in ("primary", "secondary", "camera"):
                    if browser_state.get(field):
                        browser_state[field] = tuple(browser_state[field])
                keys = {entry.key for entry in entries}
                primary_key = browser_state.get("primary")
                primary = next((entry for entry in entries if entry.key == primary_key), None)
                original = refresh_state["original_index"]
                if primary and primary.frames and original is not None:
                    browser_state["position"] = min(
                        range(len(primary.frames)), key=lambda i: abs(primary.frames[i].index - original)
                    ) + 1
                else:
                    browser_state["position"] = 1
                if primary_key not in keys or browser_state.get("secondary") not in keys:
                    browser_state.update(compare=False, calibration=None, overlay=False,
                                         undistort=False, camera=None)
            pair = None
            if completed and not refresh_state and not restore:
                browser = self.recording_browser
                new_paths = {source.path for source, _ids in completed}
                pair = suggest_pair(
                    [entry for entry in entries if entry.source.path in new_paths],
                    basis=browser.basis.currentData(),
                    tolerance_ns=round(browser.tolerance.value() * 1e6),
                    offset_ns=round(browser.offset.value() * 1e6),
                )
                if pair:
                    cameras = [entry for entry in entries
                               if entry.source.path == pair.secondary[0]
                               and entry.stream.kind == "camera_info"]
                    browser_state = browser.state()
                    browser_state.update(primary=pair.primary, secondary=pair.secondary,
                                         compare=True, position=pair.position,
                                         calibration=None, overlay=False, undistort=False,
                                         camera=cameras[0].key if len(cameras) == 1 else None)
            self.recording_browser.set_entries(entries, state=browser_state)
            self._refresh_recording_scene()
            self._rebuild_recent_menu()
            if restore:
                try:
                    for item in restore.get("sources", []):
                        if "annotations" in item:
                            self.recording_browser.tracking.restore_source(
                                item["path"], item["annotations"]
                            )
                    self.recording_browser.restore_state(restore.get("browser", {}))
                except (ValueError, TypeError, KeyError) as exc:
                    self.log("部分浏览设置恢复失败  " + str(exc))
                if self._project_path:
                    self._mark_project_saved(self._project_path)
            elif completed and not refresh_state and pair is None:
                self.recording_browser.compare.setChecked(False)
                source, ids = completed[0]
                preferred = next(
                    (
                        stream.id
                        for stream in source.info.streams
                        if stream.id in ids and stream.kind in ("pointcloud", "image")
                    ),
                    ids[0],
                )
                self.recording_browser.select_stream((source.path, preferred))
            if entries:
                self._show_recording_browser()
            self.status_task.setText(
                f"{'已更新' if refresh_state else '已加入'} {len(completed)} 个数据源 · {len(entries)} 个数据流"
                + (" · 已定位首对匹配帧" if pair and pair.matches else "")
            )
            if pair:
                self.log("已开启点云/图像同步查看。" + (
                    f"从当前范围第 {pair.position} 帧开始；更早的点云仍可浏览。"
                    if pair.matches else "当前时间基准和容差下没有匹配帧，可在浏览设置中检查。"
                ))
            if errors:
                self.log(f"{len(errors)} 个数据源索引失败，详情见上方日志。")

        def failed(message):
            self.log("建立索引失败  " + message)
            self.status_task.setText(message)

        def finished():
            for source in selected:
                if self._recording_sources.get(source.path) is not source:
                    source.close()
            if self._recording_progress is not None:
                self._recording_progress.close()
                self._recording_progress.deleteLater()
                self._recording_progress = None
            self._finish_recording_import()

        thread = self._start_recording_job(index_sources, indexed, failed, finished)
        thread.progress.connect(
            lambda value: (
                self._recording_progress.setValue(round(value * 100))
                if self._recording_progress is not None
                else None
            )
        )
        self._recording_progress.canceled.connect(thread.requestInterruption)

    def _finish_recording_import(self):
        self._recording_importing = False
        self.recording_browser.setEnabled(True)
        if not self._closing:
            browser = self.recording_browser
            if browser.entries and not browser.current_frames and browser._worker is None:
                browser._request_frame()
            QTimer.singleShot(0, self._start_next_recording_import)

    def _refresh_recording_scene(self):
        entries = self.recording_browser.entries
        self.scene_panel.set_recordings(
            [
                (
                    source.info,
                    [entry.stream.id for entry in entries if entry.source is source],
                )
                for source in self._recording_sources.values()
            ]
        )

    def _show_recording_browser(self):
        if self._scene_box_tool is not None and self._scene_box_tool.active:
            self._scene_box_tool.close()
        self.content_stack.setCurrentWidget(self.recording_browser)
        panel = self.recording_browser.tracking.dialog
        self._sync_box_tool_state(panel is not None and not panel.isHidden())
        self.overview_scroll.hide()
        self.btn_process.setEnabled(False)
        self.btn_process.hide()
        self.btn_clip.setEnabled(False)
        self.btn_clip.hide()
        self.act_compare.setEnabled(False)
        for widget in (
            self.btn_measure,
            self.btn_select,
            self.btn_navigation,
        ):
            widget.setEnabled(False)
            widget.hide()
        self.act_grid.setEnabled(False)
        self.btn_box_select.setEnabled(True)
        self._recording_display_changed()
        self._update_status()

    def _show_cloud_browser(self):
        self.recording_browser.play.setChecked(False)
        self.content_stack.setCurrentWidget(self.frame_browser)
        self._on_mode_changed(self.viewport._mode)
        self.overview_scroll.show()
        self.btn_process.setEnabled(True)
        self.btn_process.show()
        self.btn_clip.setEnabled(True)
        self.btn_clip.show()
        self.act_compare.setEnabled(True)
        for widget in (
            self.btn_measure,
            self.btn_select,
            self.btn_box_select,
            self.btn_navigation,
        ):
            widget.setEnabled(True)
            widget.show()
        self.act_grid.setEnabled(True)
        for action in self._cmap_group:
            action.setEnabled(True)
        self._update_status()

    def _select_recording(self, key):
        if self._recording_importing:
            return
        path, stream_id = key
        if stream_id is None:
            entry = next(
                (
                    entry
                    for entry in self.recording_browser.entries
                    if entry.source.path == path
                ),
                None,
            )
            if entry is None:
                return
            stream_id = entry.stream.id
        self._show_recording_browser()
        self.recording_browser.select_stream((path, stream_id))

    def _recording_selected_in_browser(self, key):
        self.scene_panel.select_recording(*key)

    def _recording_frame_changed(self, _frame):
        if self.content_stack.currentWidget() is not self.recording_browser:
            return
        enabled = any(
            view.cloud is not None
            for view in (self.recording_browser.left, self.recording_browser.right)
        )
        self._sync_guard = True
        self.btn_box_select.setEnabled(enabled)
        panel = self.recording_browser.tracking.dialog
        self.btn_box_select.setChecked(panel is not None and panel.isVisible())
        self.cmb_color.setEnabled(enabled)
        for action in self._cmap_group:
            action.setEnabled(enabled)
            action.setChecked(action.data() == self.recording_browser.colormap)
        effective_mode = ("rgb" if self.recording_browser.calibration_panel.colorize.isChecked()
                          and self.recording_browser._image_colors is not None
                          else self.recording_browser.color_mode)
        self.cmb_color.setCurrentIndex(self.cmb_color.findData(effective_mode))
        self.sld_size.setEnabled(enabled)
        self.sld_size.setValue(round(self.recording_browser.point_size * 2))
        self.lbl_size.setText(f"{self.recording_browser.point_size:.1f}")
        self._sync_guard = False
        self._update_status()

    def _recording_display_changed(self):
        frames = self.recording_browser.current_frames
        self._recording_frame_changed(frames[0] if frames else None)

    def _recording_fps(self, fps):
        if self.content_stack.currentWidget() is self.recording_browser:
            self.status_fps.setText(f"{fps:.0f} FPS")

    def _add_recording_cloud(self, cloud):
        if self._closing:
            return
        self._show_cloud_browser()
        self._add_entity(cloud)
        self._push_add_history(cloud)
        self._refresh_scene()

    def _import_numeric_text(self, path):
        from core.io.text_cloud import read_text_cloud
        from ui.widgets.text_cloud_import import TextCloudImportDialog

        try:
            dialog = TextCloudImportDialog(path, self)
        except Exception as exc:
            QMessageBox.warning(self, "无法预览数值文件", str(exc))
            return
        if dialog.exec() != QDialog.Accepted:
            dialog.deleteLater()
            return
        options = dialog.options()
        dialog.deleteLater()
        progress = SmoothProgressDialog("正在按所选列读取点云…", "取消", 0, 0, self)
        progress.setWindowTitle("导入数值点云")
        progress.setMinimumDuration(0)
        progress.show()

        def read(cancel, _progress):
            cloud = read_text_cloud(path, options, cancel=cancel)
            # Column mapping produces a derived cloud. Persist its arrays in
            # workspace assets rather than reloading the source with default XYZ.
            cloud.path = None
            return cloud

        def failed(message):
            if not self._closing:
                QMessageBox.warning(self, "点云导入失败", message)

        def finished():
            progress.close()
            progress.deleteLater()

        worker = self._start_recording_job(
            read, self._add_recording_cloud, failed, finished
        )
        progress.canceled.connect(worker.requestInterruption)

    def _remove_recording(self, path):
        if (self._recording_importing or self.recording_browser._worker is not None
                or self.recording_browser.tracking.worker is not None):
            self._flash_status("当前帧正在读取，请稍后移出数据源。")
            return
        source = self._recording_sources.pop(path, None)
        if source is not None:
            source.close()
        self._recording_ranges.pop(path, None)
        self._recording_unavailable.pop(os.path.normcase(path), None)
        self.recording_browser.tracking.remove_source(path)
        self.recording_browser.reset_source_pages(path)
        entries = [
            entry
            for entry in self.recording_browser.entries
            if entry.source.path != path
        ]
        self.recording_browser._cache.clear()
        self.recording_browser._cache_bytes = 0
        self.recording_browser.set_entries(entries)
        self._refresh_recording_scene()
        if not entries:
            self._show_cloud_browser()

    def _recording_state(self, base=None):
        def portable(path):
            if base is not None:
                try:
                    return os.path.relpath(path, base).replace(os.sep, "/")
                except ValueError:
                    pass
            return path

        state = self.recording_browser.state()
        if not self.recording_browser.entries and self._recording_unavailable:
            state = deepcopy(self._recording_unavailable_browser)
        for field in ("primary", "secondary", "camera"):
            if state.get(field):
                state[field] = [portable(state[field][0]), state[field][1]]
        unavailable = []
        for item in self._recording_unavailable.values():
            retained = deepcopy(item)
            retained["path"] = portable(item["path"])
            annotations = self.recording_browser.tracking.source_state(item["path"])
            if annotations["streams"]:
                retained["annotations"] = annotations
            unavailable.append(retained)
        return {
            "sources": [
                {
                    "path": portable(path),
                    "streams": [
                        entry.stream.id
                        for entry in self.recording_browser.entries
                        if entry.source.path == path
                    ],
                    "range": self._recording_ranges.get(path, {"mode": "all"}),
                    "annotations": self.recording_browser.tracking.source_state(path),
                }
                for path in self._recording_sources
            ] + unavailable,
            "browser": state,
        }

    def _restore_recordings(self, state, base):
        if not isinstance(state, dict) or not state.get("sources"):
            return
        import copy

        state = copy.deepcopy(state)
        for source in state["sources"]:
            source["path"] = os.path.abspath(os.path.join(base, source["path"]))
        for field in ("primary", "secondary", "camera"):
            value = state.get("browser", {}).get(field)
            if value:
                value[0] = os.path.abspath(os.path.join(base, value[0]))
        # Keep references and annotations even when the drive is disconnected.
        # Successful indexing removes a source from this pending inventory.
        self._recording_unavailable = {
            os.path.normcase(item["path"]): deepcopy(item) for item in state["sources"]
        }
        self._recording_unavailable_browser = deepcopy(state.get("browser", {}))
        for item in state["sources"]:
            if "annotations" in item:
                self.recording_browser.tracking.restore_source(item["path"], item["annotations"])
        self._open_recording_sources(
            [source["path"] for source in state["sources"]], prompt=False, restore=state
        )

    def _clear_recordings(self):
        self._recording_unavailable.clear()
        self._recording_unavailable_browser.clear()
        self.recording_browser.clear()
        for source in self._recording_sources.values():
            source.close()
        self._recording_sources.clear()
        self._recording_ranges.clear()
        self._recording_queue.clear()
        self._refresh_recording_scene()
        self._show_cloud_browser()

    def _reset_active_view(self):
        if self.content_stack.currentWidget() is self.recording_browser:
            self.recording_browser.left._fit()
            self.recording_browser.right._fit()
        else:
            self.viewport.reset_view()
