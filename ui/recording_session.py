"""Main-window integration for inspecting, indexing and restoring recordings."""

import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QMessageBox, QProgressDialog

from core.io.recording import discover_sources, open_source
from ui.recording_tasks import RecordingTask
from ui.widgets.recording_browser import BrowserStream, filter_frames
from ui.widgets.source_overview import SourceOverviewDialog


class RecordingSessionMixin:
    def _init_recording_session(self):
        self._recording_jobs = set()
        self._recording_queue = []
        self._recording_importing = False
        self._recording_sources = {}
        self._recording_ranges = {}
        self._recording_dialog = None
        self._recording_progress = None
        self.recording_browser.stream_selected.connect(
            self._recording_selected_in_browser
        )
        self.recording_browser.cloud_to_scene.connect(self._add_recording_cloud)
        self.recording_browser.frame_changed.connect(self._recording_frame_changed)
        self.recording_browser.files_dropped.connect(self.open_paths)
        self.recording_browser.text_as_cloud.connect(self._import_numeric_text)
        self.recording_browser.fps_updated.connect(self._recording_fps)
        self.scene_panel.recording_selected.connect(self._select_recording)
        self.scene_panel.recording_remove_requested.connect(self._remove_recording)

    def _recording_threads(self):
        threads = list(self._recording_jobs)
        if self.recording_browser._worker is not None:
            threads.append(self.recording_browser._worker)
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

    def _open_recording_sources(self, paths, prompt=True, restore=None):
        if self._closing:
            return
        self._recording_queue.append((list(paths), prompt, restore))
        self._start_next_recording_import()

    def _start_next_recording_import(self):
        if self._closing or self._recording_importing or not self._recording_queue:
            return
        browser = self.recording_browser
        browser.play.setChecked(False)
        if browser._worker is not None:
            browser._worker.requestInterruption()
            browser._pending = None
            QTimer.singleShot(50, self._start_next_recording_import)
            return
        paths, prompt, restore = self._recording_queue.pop(0)
        self._recording_importing = True
        browser.setEnabled(False)
        self.status_task.setText("正在识别数据源并读取概览…")
        inspection = QProgressDialog("正在识别数据源并读取概览…", "取消", 0, 0, self)
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
                self.status_task.setText("没有可读取的数据源，请查看日志")
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
                accepted = self._recording_dialog.exec() == QDialog.Accepted
                options = self._recording_dialog.selection()
                self._recording_dialog.deleteLater()
                self._recording_dialog = None
                if not accepted:
                    for source in sources:
                        source.close()
                    self._finish_recording_import()
                    return
            else:
                options = {
                    "sources": {
                        source.path: [stream.id for stream in source.info.streams]
                        for source in sources
                    },
                    "range": {"mode": "all"},
                }
            self._index_recording_sources(sources, options, restore)

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

    def _index_recording_sources(self, sources, options, restore=None):
        chosen = options["sources"]
        selected = [source for source in sources if chosen.get(source.path)]
        for source in sources:
            if source not in selected:
                source.close()
        accepted = []
        self.status_task.setText("正在建立帧索引，首次打开大包可能需要几分钟…")
        self._recording_progress = QProgressDialog(
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
            self.recording_browser.set_entries(entries)
            self._refresh_recording_scene()
            self._rebuild_recent_menu()
            if restore:
                try:
                    self.recording_browser.restore_state(restore.get("browser", {}))
                except (ValueError, TypeError, KeyError) as exc:
                    self.log("部分浏览设置恢复失败  " + str(exc))
                if self._project_path:
                    self._mark_project_saved(self._project_path)
            elif completed:
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
                f"已加入 {len(completed)} 个数据源 · {len(entries)} 个数据流"
            )
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
        self.content_stack.setCurrentWidget(self.recording_browser)
        self.overview_scroll.hide()
        self.btn_process.setEnabled(False)
        self.btn_clip.setEnabled(False)
        self.act_compare.setEnabled(False)
        for widget in (
            self.btn_measure,
            self.btn_select,
            self.btn_box_select,
            self.btn_navigation,
        ):
            widget.setEnabled(False)
        self.act_grid.setEnabled(False)
        for action in self._cmap_group:
            action.setEnabled(False)
        self._update_status()

    def _show_cloud_browser(self):
        self.recording_browser.play.setChecked(False)
        self.content_stack.setCurrentWidget(self.frame_browser)
        self.overview_scroll.show()
        self.btn_process.setEnabled(True)
        self.btn_clip.setEnabled(True)
        self.act_compare.setEnabled(True)
        for widget in (
            self.btn_measure,
            self.btn_select,
            self.btn_box_select,
            self.btn_navigation,
        ):
            widget.setEnabled(True)
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
        self.cmb_color.setEnabled(enabled)
        self.cmb_color.setCurrentIndex(
            self.cmb_color.findData(self.recording_browser.color_mode)
        )
        self.sld_size.setEnabled(enabled)
        self.sld_size.setValue(round(self.recording_browser.point_size * 2))
        self.lbl_size.setText(f"{self.recording_browser.point_size:.1f}")
        self._sync_guard = False
        self._update_status()

    def _recording_fps(self, fps):
        if self.content_stack.currentWidget() is self.recording_browser:
            self.status_fps.setText(f"{fps:.0f} FPS")

    def _add_recording_cloud(self, cloud):
        if self._closing:
            return
        self._show_cloud_browser()
        self._add_entity(cloud)
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
        progress = QProgressDialog("正在按所选列读取点云…", "取消", 0, 0, self)
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
        if self._recording_importing or self.recording_browser._worker is not None:
            self._flash_status("当前帧正在读取，请稍后移出数据源。")
            return
        source = self._recording_sources.pop(path, None)
        if source is not None:
            source.close()
        self._recording_ranges.pop(path, None)
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
        for field in ("primary", "secondary", "camera"):
            if state.get(field):
                state[field] = [portable(state[field][0]), state[field][1]]
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
                }
                for path in self._recording_sources
            ],
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
        self._open_recording_sources(
            [source["path"] for source in state["sources"]], prompt=False, restore=state
        )

    def _clear_recordings(self):
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
