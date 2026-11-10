"""Serial frame capture on Qt, bounded encoding on a separate worker."""
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from queue import Empty, Full, Queue
import shutil
import sys
import tempfile

from PIL import Image
from PySide6.QtCore import QObject, QProcess, QSignalBlocker, QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                              QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                              QMessageBox, QSpinBox, QVBoxLayout, QWidget)

from core.io.exporters import export_entity
from core.io.recording_types import FrameData
from core.sequence_export import (ExportRollbackError, SequenceWriter, compose_views,
                                  export_file_paths, publish_export_files)
from ui.widgets.smooth_progress import SmoothProgressDialog


@dataclass(frozen=True)
class ExportOptions:
    first: int
    last: int
    format: str = 'mp4'
    layout: str = 'paired'
    fps: float = 10.0
    size: tuple = (1280, 720)

    def validate(self, count):
        if not 0 <= self.first <= self.last < count:
            raise ValueError('请选择有效的起止帧区间')
        if self.format not in ('mp4', 'gif', 'png', 'ply', 'pcd', 'png_ply'):
            raise ValueError('不支持的导出格式')
        if self.layout not in ('paired', 'cloud', 'image'):
            raise ValueError('不支持的画面布局')
        if not 0.1 <= self.fps <= 120:
            raise ValueError('播放速度应在 0.1～120 帧/秒之间')
        if self.format == 'gif' and self.fps > 100:
            raise ValueError('GIF 最多支持 100 帧/秒')
        if len(self.size) != 2 or any(not isinstance(v, int) or v < 2 or v > 8192 for v in self.size):
            raise ValueError('输出尺寸无效')
        if self.format == 'mp4' and any(v % 2 for v in self.size):
            raise ValueError('MP4 输出宽高必须为偶数')


class ExportDialog(QDialog):
    def __init__(self, browser):
        super().__init__(browser)
        self.setWindowTitle('导出画面与点云')
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        title = QLabel('选择范围，保存当前融合效果')
        title.setStyleSheet('font-size: 16px; font-weight: 600;')
        layout.addWidget(title)
        form = QFormLayout()
        form.setVerticalSpacing(14)
        layout.addLayout(form)
        self.scope = QComboBox()
        self.scope.addItem('时间轴选区', 'selection')
        self.scope.addItem('当前帧', 'current')
        self.scope.addItem('全部帧', 'all')
        form.addRow('导出范围', self.scope)
        count = len(browser.entry(browser.primary.currentData()).frames)
        self.first, self.last = QSpinBox(), QSpinBox()
        for spin in (self.first, self.last):
            spin.setRange(1, count)
            spin.setKeyboardTracking(False)
        first, last = browser.slider.selection()
        self.first.setValue(first + 1)
        self.last.setValue(last + 1)
        span = QWidget()
        row = QHBoxLayout(span)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.first)
        row.addWidget(QLabel('至'))
        row.addWidget(self.last)
        self.count_label = QLabel()
        row.addWidget(self.count_label)
        form.addRow('起止帧（含首尾）', span)
        self.format = QComboBox()
        for text, value in [('MP4 动画', 'mp4'), ('GIF 动画', 'gif'), ('PNG 逐帧画面', 'png'),
                            ('PLY 点云', 'ply'), ('PCD 点云', 'pcd'), ('PNG 画面 ＋ PLY 点云', 'png_ply')]:
            self.format.addItem(text, value)
        form.addRow('文件格式', self.format)
        self.layout_choice = QComboBox()
        self.layout_choice.addItem('点云 ＋ 图像同步双窗格', 'paired')
        self.layout_choice.addItem('投影图像', 'image')
        self.layout_choice.addItem('点云画面', 'cloud')
        form.addRow('画面内容', self.layout_choice)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(.1, 120)
        self.fps.setDecimals(1)
        self.fps.setSuffix(' 帧/秒')
        self.fps.setValue(browser.rate.value())
        form.addRow('动画速度', self.fps)
        self.size = QComboBox()
        self.size.addItem('1280 × 720', (1280, 720))
        self.size.addItem('1920 × 1080', (1920, 1080))
        self.size.addItem('960 × 540', (960, 540))
        form.addRow('画面尺寸', self.size)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('命名并保存…')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.first.valueChanged.connect(lambda value: self.last.setMinimum(value))
        self.last.valueChanged.connect(lambda value: self.first.setMaximum(value))
        self.first.valueChanged.connect(self._update)
        self.last.valueChanged.connect(self._update)
        self.format.currentIndexChanged.connect(self._update)
        self.layout_choice.currentIndexChanged.connect(self._update)
        def scope_changed():
            key = self.scope.currentData()
            a, b = ((browser.position.value(),) * 2 if key == 'current' else
                    (1, count) if key == 'all' else (first + 1, last + 1))
            self.first.setMaximum(count)
            self.last.setMinimum(1)
            self.last.setValue(b)
            self.first.setValue(a)
            self._update()
        self.scope.currentIndexChanged.connect(scope_changed)
        def edited_scope():
            blocker = QSignalBlocker(self.scope)
            self.scope.setCurrentIndex(0)
            blocker.unblock()
        self.first.editingFinished.connect(edited_scope)
        self.last.editingFinished.connect(edited_scope)
        if not browser.slider.selectionEnabled():
            self.scope.setCurrentIndex(1)
        kinds = {entry.stream.kind for entry in
                 (browser.entry(browser.primary.currentData()),
                  browser.entry(browser.secondary.currentData()) if browser.compare.isChecked() else None)
                 if entry is not None and entry.frames}
        has_cloud, has_image = 'pointcloud' in kinds, 'image' in kinds
        for index, available in enumerate((has_cloud and has_image, has_image, has_cloud)):
            self.layout_choice.model().item(index).setEnabled(available)
        if not (has_cloud and has_image):
            self.layout_choice.setCurrentIndex(1 if has_image else 2)
        if not has_cloud:
            for index in (3, 4, 5):
                self.format.model().item(index).setEnabled(False)
        self._update()

    def _update(self, *_):
        self.count_label.setText(f'共 {self.last.value() - self.first.value() + 1:,} 帧')
        fmt, layout = self.format.currentData(), self.layout_choice.currentData()
        visual = fmt not in ('ply', 'pcd')
        animation = fmt in ('mp4', 'gif')
        self.layout_choice.setEnabled(visual)
        self.size.setEnabled(visual and (animation or layout == 'paired'))
        self.fps.setEnabled(animation)
        self.fps.setMaximum(100 if fmt == 'gif' else 120)
        self.hint.setText(
            '动画保持当前视角，以设定速度逐帧播放；无匹配图像时显示空白占位。' if animation else
            '点云保存完整坐标、字段与 RGB；图像赋色会写入 RGB，显示用高度/强度色带不烘焙。' if not visual else
            '画面包含当前投影点和 3D 框。单图 PNG 保留原始分辨率；双窗格使用所选尺寸。')
        count = self.last.value() - self.first.value() + 1
        if animation or (count == 1 and fmt != 'png_ply'):
            self.summary.setText(f'保存为一个 {fmt.upper()} 文件，可自定义文件名。')
        elif fmt == 'png_ply':
            self.summary.setText(f'保存 {count} 组 PNG 画面与 PLY 点云，使用所填名称'
                                 + ('并追加帧号。' if count > 1 else '。'))
        else:
            self.summary.setText(f'保存 {count} 个 {fmt.upper()} 文件，使用所填名称并追加帧号。')

    def options(self):
        return ExportOptions(self.first.value() - 1, self.last.value() - 1,
                             self.format.currentData(), self.layout_choice.currentData(),
                             self.fps.value(), self.size.currentData())


class _ExportWriter(QThread):
    advanced = Signal(int)

    def __init__(self, paths, options, parent, *, overwrite=False):
        super().__init__(parent)
        self.paths, self.options = tuple(paths), options
        self.target = self.paths[0]
        self.overwrite = overwrite
        self.queue = Queue(maxsize=2)
        self.error = ''
        self.success = False

    def run(self):
        staging = None
        writer = None
        try:
            staging = Path(tempfile.mkdtemp(prefix='.pointlab-export-', dir=self.target.parent))
            fmt = self.options.format
            if fmt in ('mp4', 'gif'):
                writer = SequenceWriter(str(staging / self.target.name), self.options.fps, self.options.size)
            count = 0
            per_frame = 2 if fmt == 'png_ply' else 1
            while not self.isInterruptionRequested():
                try:
                    item = self.queue.get(timeout=.05)
                except Empty:
                    continue
                if item is None:
                    if count != self.options.last - self.options.first + 1:
                        raise ValueError('导出帧数不完整')
                    break
                cloud_image, image, cloud, position = item
                if position != self.options.first + count + 1 or position > self.options.last + 1:
                    raise ValueError('导出帧顺序不正确')
                frame_paths = self.paths[count * per_frame:(count + 1) * per_frame]
                if fmt in ('mp4', 'gif', 'png', 'png_ply'):
                    if fmt in ('png', 'png_ply') and self.options.layout != 'paired':
                        rgb = image if self.options.layout == 'image' else cloud_image
                        if rgb is None:
                            rgb = compose_views(None, None, self.options.layout, self.options.size)
                    else:
                        rgb = compose_views(cloud_image, image, self.options.layout, self.options.size)
                    if writer is not None:
                        writer.append(rgb)
                    else:
                        with Image.fromarray(rgb) as picture:
                            picture.save(staging / frame_paths[0].name)
                if fmt in ('ply', 'pcd', 'png_ply'):
                    if cloud is None:
                        raise ValueError(f'第 {position} 帧没有匹配点云，无法导出点云文件')
                    export_entity(cloud, str(staging / frame_paths[-1].name))
                count += 1
                self.advanced.emit(count)
            else:
                return
            if self.isInterruptionRequested():
                return
            if writer is not None:
                writer.close()
                writer = None
            if self.isInterruptionRequested():
                return
            self.success = publish_export_files(staging, self.paths, overwrite=self.overwrite,
                                                cancelled=self.isInterruptionRequested)
        except ExportRollbackError as exc:
            self.error = str(exc)
            # Never discard the only backup if the filesystem refused restoration.
            staging = None
        except Exception as exc:
            self.error = str(exc)
        finally:
            if writer is not None:
                try:
                    writer.abort()
                except Exception as exc:
                    self.error += f'\n编码临时文件清理失败：{exc}'
            if staging is not None:
                try:
                    shutil.rmtree(staging)
                except OSError:
                    self.error += f'\n未能清理本次临时导出，可手动删除：{staging}'


class RecordingExportController(QObject):
    finished = Signal()

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.worker = None
        self.last_error = ''
        self.last_output = None
        self._dialog = None
        self._timer = QTimer(self)
        self._timer.setInterval(12)
        self._timer.timeout.connect(self._pump)
        self._restore_pending = False
        browser.frame_changed.connect(self._restore_views)
        browser.last_export_action.triggered.connect(self.open_output)

    def open_output(self):
        if not self.last_output:
            return
        path = Path(self.last_output)
        if not path.is_file():
            return
        if os.name == 'nt':
            started, _pid = QProcess.startDetached('explorer.exe', ['/select,', str(path)])
            if started:
                return
        elif sys.platform == 'darwin':
            started, _pid = QProcess.startDetached('open', ['-R', str(path)])
            if started:
                return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    @property
    def busy(self):
        return self.worker is not None

    def open_dialog(self):
        browser = self.browser
        if self.busy or not browser.current_frames:
            return
        if not any(isinstance(frame, FrameData) and frame.kind in ('pointcloud', 'image')
                   for frame in browser.current_frames):
            browser.export_current()
            return
        browser.play.setChecked(False)
        dialog = ExportDialog(browser)
        try:
            if dialog.exec() != QDialog.Accepted:
                return
            options = dialog.options()
            extension = 'png' if options.format == 'png_ply' else options.format
            multiple = (options.format == 'png_ply' or
                        (options.format not in ('gif', 'mp4') and options.first != options.last))
            name = datetime.now().strftime('PointLab_%Y%m%d_%H%M%S')
            default = Path(self.last_output).parent if self.last_output else Path()
            default /= f'{name}_{options.first + 1}-{options.last + 1}.{extension}'
            target, _filter = QFileDialog.getSaveFileName(
                browser, '设置文件名前缀与保存位置' if multiple else '命名并保存导出文件',
                str(default), f'{extension.upper()} 文件 (*.{extension})',
                options=QFileDialog.DontConfirmOverwrite)
            if not target:
                return
            paths = export_file_paths(target, options.format, options.first, options.last)
            conflicts = [path for path in paths if path.exists() or path.is_symlink()]
            if conflicts:
                names = '\n'.join(path.name for path in conflicts[:8])
                if len(conflicts) > 8:
                    names += f'\n…另有 {len(conflicts) - 8} 个文件'
                result = QMessageBox.question(
                    browser, '确认替换已有文件',
                    f'以下 {len(conflicts)} 个文件已存在：\n{names}\n\n是否替换？',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if result != QMessageBox.Yes:
                    return
            self.start(target, options, overwrite=bool(conflicts))
        except Exception as exc:
            QMessageBox.warning(browser, '无法导出', str(exc))
        finally:
            dialog.deleteLater()

    def start(self, target, options, *, overwrite=False, show_progress=True):
        """Export to a chosen file name (or batch prefix), rejecting overwrites by default."""
        browser = self.browser
        if self.busy or browser.tracking.busy or browser._stopping:
            raise RuntimeError('请等待当前操作完成')
        entry = browser.entry(browser.primary.currentData())
        options.validate(len(entry.frames) if entry else 0)
        target = Path(os.path.abspath(target))
        if not target.parent.is_dir():
            raise ValueError('请选择已存在的保存文件夹')
        paths = export_file_paths(target, options.format, options.first, options.last)
        for path in paths:
            if path.is_dir() or ((path.exists() or path.is_symlink()) and not overwrite):
                raise FileExistsError(f'文件已存在：{path}')
        self.last_error = ''
        self._cancelled = False
        self._saved_position = browser.position.value()
        self._saved_cameras = [(view.cloud_view, view.cloud_view.camera_state())
                               for view in (browser.left, browser.right)
                               if view.cloud_view is not None and hasattr(view.cloud_view, 'camera_state')]
        self._saved_images = [(view.image, view.image.transform(), view.image._auto_fit,
                               view.image.horizontalScrollBar().value(), view.image.verticalScrollBar().value())
                              for view in (browser.left, browser.right)]
        self._locks = [(widget, widget.isEnabled()) for widget in
                       (browser.toolbar, browser.transport, browser.settings_panel, browser.box_splitter)]
        browser.play.setChecked(False)
        browser._exporting = True
        browser._prefetch_timer.stop()
        browser.timer.stop()
        for widget, _ in self._locks:
            widget.setEnabled(False)
        self.options = options
        self._next = options.first
        self._requested = None
        self._sent_end = False
        self.worker = _ExportWriter(paths, options, self, overwrite=overwrite)
        self.worker.finished.connect(self._finish)
        if show_progress:
            self._dialog = SmoothProgressDialog('准备导出…', '取消', 0, options.last - options.first + 1, browser)
            self._dialog.setWindowTitle('导出选中区间')
            self._dialog.setWindowModality(Qt.WindowModal)
            self._dialog.setAutoClose(False)
            self._dialog.setAutoReset(False)
            self._dialog.setMinimumDuration(0)
            self._dialog.canceled.connect(self.cancel)
            self.worker.advanced.connect(self._progress)
            self._dialog.show()
        self.worker.start()
        self._timer.start()

    def _progress(self, count):
        if self._dialog is not None:
            self._dialog.setLabelText(f'正在保存 {count:,} / {self.options.last - self.options.first + 1:,} 帧')
            self._dialog.setValue(count)

    def _pump(self):
        browser, worker = self.browser, self.worker
        if worker is None or self._cancelled or self._sent_end:
            return
        if browser._stopping:
            self.cancel()
            return
        if worker.queue.full() or browser._worker is not None or browser._pending is not None:
            return
        try:
            if self._next > self.options.last:
                worker.queue.put_nowait(None)
                self._sent_end = True
                return
            if self._requested != self._next:
                self._requested = self._next
                if browser.position.value() == self._next + 1:
                    browser._request_frame()
                else:
                    browser.position.setValue(self._next + 1)
                return
            if not browser.current_frames or browser._last_frame_error:
                raise ValueError(browser._last_frame_error or browser.status.text() or '帧读取失败')
            errors = [frame for frame in browser.current_frames if isinstance(frame, str)]
            if errors:
                raise ValueError('；'.join(errors))
            entry = browser.entry(browser.primary.currentData())
            if browser._displayed_indices[0] != entry.frames[self._next].index:
                raise ValueError('当前帧与导出位置不一致，请重新加载话题')
            cloud_rgb = image_rgb = cloud = None
            visual = self.options.format not in ('ply', 'pcd')
            for side, view in enumerate((browser.left, browser.right)):
                if not isinstance(view.frame, FrameData):
                    continue
                if view.frame.kind == 'pointcloud':
                    cloud = browser.export_cloud_entity(side)
                    if visual and self.options.layout != 'image':
                        cloud_rgb = view.cloud_view.snapshot_rgb()
                elif view.frame.kind == 'image' and visual and self.options.layout != 'cloud':
                    image_rgb = view.image.snapshot_rgb()
            worker.queue.put_nowait((cloud_rgb, image_rgb, cloud, self._next + 1))
            self._next += 1
        except Full:
            return
        except Exception as exc:
            self.last_error = str(exc)
            self.cancel()

    def cancel(self):
        if self.worker is None:
            return
        self._cancelled = True
        self._timer.stop()
        self.worker.requestInterruption()
        if self.browser._worker is not None:
            self.browser._worker.requestInterruption()

    def _finish(self):
        self._timer.stop()
        worker, self.worker = self.worker, None
        if worker is None:
            return
        self.last_error = self.last_error or worker.error
        if worker.success:
            self.last_output = str(worker.target)
            self.browser.last_export_action.setEnabled(True)
        worker.deleteLater()
        visible_progress = self._dialog is not None
        if self._dialog is not None:
            self._dialog.close()
            self._dialog.deleteLater()
            self._dialog = None
        browser = self.browser
        browser._exporting = False
        self._completion_message = (
            f'已导出 {self.options.last - self.options.first + 1:,} 帧 · 在导出菜单中定位文件'
            if worker.success else f'导出失败：{self.last_error}' if self.last_error else '已取消导出')
        for widget, enabled in self._locks:
            widget.setEnabled(enabled)
        if not browser._stopping:
            self._restore_pending = True
            blocker = QSignalBlocker(browser.position)
            browser.position.setValue(self._saved_position)
            blocker.unblock()
            browser._position_changed(self._saved_position)
            browser.status_changed.emit(self._completion_message)
            if self.last_error and visible_progress:
                QMessageBox.warning(browser, '导出未完成', self.last_error)
        self.finished.emit()

    def _restore_views(self, _frame):
        if not self._restore_pending:
            return
        self._restore_pending = False
        if self.browser.position.value() != self._saved_position:
            return
        for view, state in self._saved_cameras:
            view.set_camera_state(state)
        for image, transform, auto_fit, horizontal, vertical in self._saved_images:
            image.setTransform(transform)
            image._auto_fit = auto_fit
            image.horizontalScrollBar().setValue(horizontal)
            image.verticalScrollBar().setValue(vertical)
        self.browser.status.setText(self._completion_message)
        self.browser.status.setToolTip(self.last_error or self.last_output or self._completion_message)
        self.browser.status.show()
