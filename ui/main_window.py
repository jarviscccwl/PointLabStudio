"""主窗口：原创 Studio 布局。

结构：
  顶栏：品牌 + 打开/处理/工程/视图 + 撤销重做 + 视图控件（颜色/点大小/测量/选择/裁剪/重置/主题）
  ├─ 左侧「场景」卡片列表（勾选框切换显示、双击聚焦）
  ├─ 中央 3D 视口（细线坐标轴 + 地面网格 + 裁剪范围线框 + 测量/选点浮层）
  └─ 右侧「数据概览」（点数/空间范围/属性/场景统计/来源）
  底部状态栏（点数/任务进度/内存/FPS + 日志开关），日志为可收起抽屉

加载流程：文件夹后台扫描；当前帧优先、相邻帧预取，最近帧按内存预算缓存。
单帧读取始终在后台执行，大文件先预览，超过内存阈值时使用采样。
"""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
import time
from uuid import uuid4
from copy import deepcopy
from dataclasses import dataclass
from weakref import WeakKeyDictionary, WeakSet

import numpy as np
from PySide6.QtCore import QEventLoop, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QScrollArea, QSlider, QSplitter, QToolButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from core.algorithms import registration, segmentation
from core.applog import get_logger
from core.color.colormaps import COLOR_MODE_LABELS, list_colormaps
from core.i18n import tr
from core.plugin import load_plugins
from core.settings import load_settings, save_settings
from ui.project_session import ProjectSessionMixin
from ui.recording_session import RecordingSessionMixin, discovery_label
from core.io.recording import discover_source_inventory, is_data_source
from core.cancellation import OperationCancelled
from core.commands import Command, History
from core.commands.spec import get_spec
from core.commands.builtin import run_command
from core.geometry import mesh as mesh_ops
from core.io.auto_ingest import plan_ingest, tier_by_points
from core.io.measurements import write_measurements_csv
from core.io.readers import ReadOptions, read_cloud, read_stream_bin
from core.io.scanner import natural_key
from core.io.source_ops import move_sources, relocate_path, rename_source
from core.model.entities import COLOR_MODES, CloudEntity, MeshEntity, ScalarField
from core.pipeline import run_pipeline
from ui.theme import build_stylesheet
from ui.theme.icons import make_icon
from ui.viewport import PointCloudViewport
from ui.widgets import OverviewPanel, ScenePanel
from ui.widgets.clip_popup import ClipPopup
from ui.widgets.responsive_header import ResponsiveHeader
from ui.widgets.param_dialog import ParamDialog
from ui.widgets.transform_dialog import TransformDialog
from ui.widgets.pipeline_dialog import PipelineDialog
from ui.widgets.script_dialog import ScriptDialog
from ui.widgets.frame_browser import FrameBrowser
from ui.widgets.recording_browser import RecordingBrowser
from ui.widgets.smooth_progress import SmoothProgressDialog

COLOR_SHORTCUT_MODES = tuple(COLOR_MODES)

HELP_TEXT = """快捷键：
  ←/↑  上一个点云        →/↓  下一个点云
  R    重置视角          H    显示本帮助
  C    循环颜色模式      B    开关裁剪范围
  1 高度 · 2 强度 · 3 RGB · 4 标签 · 5 标量场 · 6 单色（视口或场景获得焦点时）
  Space 场景中显示/隐藏当前选中点云；采集包点云/图像预览中播放/暂停
  Home/End 采集包点云/图像预览中暂停并跳到首帧/末帧
  +/=  增大点大小        -/_  减小点大小
  W/S  上/下平移相机     A/D  左/右平移相机
  Q/E  后退/前进平移相机
测量：顶栏「测量」→ 点两个点测距；Esc 退出
选点：顶栏「选点」→ 单击查看坐标与属性；Esc 退出
文件：Ctrl+O 打开文件 · Ctrl+Shift+O 文件夹 · Ctrl+P 打开工作区 · Ctrl+S 保存工作区 · Ctrl+E 导出点云 · Ctrl+L 日志
采集包：打开文件或文件夹 → 查看内容概览 → 选择数据流浏览；支持 PCAP / MCAP / BAG
鼠标：左键拖拽旋转 · 右键/中键拖拽平移 · Alt + 拖拽/滚轮以 20% 速度微调相机
视角：自由视角绕中心旋转、滚轮缩放；固定视角锁定当前高度、原地水平转向、滚轮沿 XY 平面平滑前后移动
裁剪：按包围盒后关闭面板，拖动橙色框面中央的圆点调整边界；Esc 取消本次拖动
"""


class LoadThread(QThread):
    """后台全量加载线程（M/L 级）。"""

    progress = Signal(str, float)   # path, 0..1
    done = Signal(str, object)      # path, CloudEntity
    preview = Signal(str, object)
    failed = Signal(str, str)       # path, error

    def __init__(self, path: str, options: ReadOptions, parent=None):
        super().__init__(parent)
        self.path = path
        self.options = options

    def run(self):
        try:
            plan = plan_ingest(self.path, self.options)
            if self.isInterruptionRequested():
                return
            if plan.preview is not None:
                self.preview.emit(self.path, plan.preview)
            if plan.tier == "S" or plan.needs_full:
                entity = read_cloud(self.path, self.options,
                                    progress=lambda p: self.progress.emit(self.path, p),
                                    cancel=self.isInterruptionRequested)
            elif self.path.lower().endswith(".bin"):
                entity = read_stream_bin(self.path, self.options, budget=2_000_000,
                                         progress=lambda p: self.progress.emit(self.path, p),
                                         cancel=self.isInterruptionRequested)
            else:
                entity = plan.preview
            if entity is None:
                raise ValueError("没有可读取的点云数据")
            if self.isInterruptionRequested():
                return
            self.done.emit(self.path, entity)
        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(self.path, str(exc))


class ScanThread(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(object)

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self):
        try:
            inventory = discover_source_inventory(
                self.paths, cancel=self.isInterruptionRequested, progress=self.progress.emit,
            )
        except OperationCancelled:
            return
        except Exception as exc:
            self.failed.emit(f"数据源识别失败: {exc}")
            return
        if not self.isInterruptionRequested():
            for error in inventory.errors:
                self.failed.emit(f"收集失败: {error}")
            self.done.emit({"files": inventory.files,
                            "directories": inventory.directories,
                            "data_sources": inventory.sources})




class SourceOperationThread(QThread):
    done = Signal(object)
    failed = Signal(object)

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self.worker = worker

    def run(self):
        try:
            self.done.emit(self.worker())
        except Exception as exc:
            self.failed.emit(exc)


@dataclass(eq=False)
class SceneSnapshot:
    entities: list
    current: object
    tree: dict
    visibility: list
    explicit: set


@dataclass(eq=False)
class TrashRecord:
    before: SceneSnapshot
    after: SceneSnapshot
    sources: list
    moves: list


class FilterThread(QThread):
    """后台滤波线程：worker(progress, cancel) → 新的 CloudEntity。"""

    progress = Signal(float)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self._worker = worker

    def run(self):
        try:
            entity = self._worker(lambda f: self.progress.emit(float(f)),
                                  self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.done.emit(entity)
        except OperationCancelled:
            self.cancelled.emit()
        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))


class EpsThread(QThread):
    """后台估算 DBSCAN 邻域半径，避免大点云在 UI 线程建 KDTree。"""

    done = Signal(float)
    failed = Signal(str)

    def __init__(self, xyz, min_samples: int, parent=None):
        super().__init__(parent)
        self._xyz = xyz
        self._min_samples = min_samples

    def run(self):
        try:
            value = segmentation.suggest_eps(
                self._xyz, min_samples=self._min_samples,
                cancel=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.done.emit(float(value))
        except OperationCancelled:
            return
        except Exception as exc:  # noqa: BLE001
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc))


class MainWindow(RecordingSessionMixin, ProjectSessionMixin, QMainWindow):
    def __init__(self, start_path: str | None = None, theme: str = "dark"):
        super().__init__()
        self.setWindowTitle(tr("PointLab Studio — 点云综合处理平台"))
        self.setMinimumSize(980, 620)
        self.resize(1280, 820)
        self.setAcceptDrops(True)

        self.settings = load_settings()
        geometry = self.settings.window_geometry
        if geometry:
            try:
                from PySide6.QtCore import QByteArray
                self.restoreGeometry(QByteArray.fromBase64(geometry.encode()))
            except Exception:  # noqa: BLE001
                pass
        if theme == "default" and self.settings.theme in ("dark", "light"):
            theme = self.settings.theme
        self.theme = theme
        self._logger = get_logger("ui")
        QApplication.instance().setStyleSheet(build_stylesheet(theme))
        self.entities: list = []
        self.meshes: list[MeshEntity] = []
        self.options = ReadOptions()
        self._threads: dict = {}
        self._load_queue: list = []
        self._scan_threads = []
        self._frame_cache = {}
        self._result_sources = WeakKeyDictionary()
        self._known_source_entities = WeakSet()
        self._scene_snapshots = WeakSet()
        self._trash_records = WeakSet()
        self._source_operation = None
        self._deferred_imports = []
        self._pending_regroups = []
        self._camera_initialized = False
        self._restoring_project = False
        self._auto_mesh = None
        self._explicit_loads = set()
        self._close_deadline: float | None = None
        self._eps_thread: EpsThread | None = None
        self._eps_cache: dict[str, float] = {}
        self._pending_display: dict = {}
        self.history = History()
        self._filter_thread: FilterThread | None = None
        self._closing = False
        self._sync_guard = False
        self._selection: list = []  # [(entity, indices), ...] 框选/套索结果
        self._scene_box_tool = None
        self._icon_buttons = []

        self._build_ui()
        self._build_shortcuts()
        self._connect_signals()
        self._init_recording_session()
        self._init_project_session()

        self.log("PointLab Studio 已启动。拖入采集包、点云、图片或文件夹即可查看。")
        if start_path:
            self.open_paths([start_path])

    # ================= UI 构建 =================
    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 视口先创建：顶栏控件（重置视角/网格）需要引用它
        self.viewport = PointCloudViewport(theme=self.theme, parent=self)

        root.addWidget(self._build_header())

        body_host = QWidget()
        body = QHBoxLayout(body_host)
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(10)

        self.scene_panel = ScenePanel()
        self.scene_panel.set_theme(self.theme)
        self.scene_panel.setMinimumWidth(250)

        self.overview = OverviewPanel()
        self.overview_scroll = QScrollArea()
        self.overview_scroll.setObjectName("overviewScroll")
        self.overview_scroll.setWidgetResizable(True)
        self.overview_scroll.setFrameShape(QScrollArea.NoFrame)
        self.overview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.overview_scroll.setWidget(self.overview)
        self.overview_scroll.setMinimumWidth(240)

        panes = QSplitter(Qt.Horizontal)
        panes.setObjectName("workspacePanes")
        panes.setChildrenCollapsible(False)
        panes.setHandleWidth(6)
        panes.addWidget(self.scene_panel)
        self.frame_browser = FrameBrowser(self.viewport)
        self.recording_browser = RecordingBrowser(self.theme, PointCloudViewport, self)
        # One global box tool serves scenes and recordings. Standalone browsers
        # retain their own entry for embedding outside MainWindow.
        self.recording_browser.tracking_button.hide()
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.frame_browser)
        self.content_stack.addWidget(self.recording_browser)
        panes.addWidget(self.content_stack)
        panes.addWidget(self.overview_scroll)
        panes.setStretchFactor(0, 0)
        panes.setStretchFactor(1, 1)
        panes.setStretchFactor(2, 0)
        panes.setSizes([320, 760, 292])
        body.addWidget(panes)
        root.addWidget(body_host, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logDrawer")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setMaximumHeight(180)
        self.log_view.hide()
        root.addWidget(self.log_view)

        root.addWidget(self._build_bottom_bar())
        self.setCentralWidget(central)

        self.clip_popup = ClipPopup(self)
        self._clip_committed = {"enabled": False, "min": None, "max": None}
        self._applying_clip = False
        self._clip_commit_timer = QTimer(self)
        self._clip_commit_timer.setSingleShot(True)
        self._clip_commit_timer.timeout.connect(self._commit_clip_history)
        self._apply_icons()

    def _build_header(self) -> QWidget:
        header = ResponsiveHeader()
        lay = header.row

        logo = QLabel("◉")
        logo.setObjectName("appLogo")
        title = QLabel("PointLab Studio")
        title.setObjectName("appTitle")
        sub = QLabel("点云综合处理平台")
        sub.setObjectName("appSub")
        lay.addWidget(logo)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addStretch(1)

        self.btn_open = QToolButton()
        self.btn_open.setObjectName("headerBtn")
        self.btn_open.setText("打开")
        open_menu = QMenu(self.btn_open)
        open_menu.addAction("打开文件…", self._open_files_dialog)
        open_menu.addAction("打开文件夹…", self._open_folder_dialog)
        open_menu.addAction("打开采集包 (PCAP / MCAP / BAG)…", self._start_lidar_import)
        self._recent_menu = open_menu.addMenu("最近文件")
        self._rebuild_recent_menu()
        self.btn_open.setMenu(open_menu)
        self.btn_open.setPopupMode(QToolButton.InstantPopup)
        self._register_icon(self.btn_open, "folder-plus")

        self.btn_project = QToolButton()
        self.btn_project.setObjectName("headerBtn")
        self.btn_project.setText("工作区")
        self.btn_project.setToolTip("保存文件列表、视角与处理结果，方便下次继续；只看点云无需保存")
        proj_menu = QMenu(self.btn_project)
        proj_menu.addAction("工作区是什么？", self._explain_project)
        proj_menu.addSeparator()
        proj_menu.addAction("清空当前工作区", self._new_project)
        proj_menu.addAction("打开工作区…", self._open_project_dialog)
        proj_menu.addAction("保存工作区…", self._save_project_dialog)
        proj_menu.addAction("工作区另存为…", self._save_project_as_dialog)
        proj_menu.addSeparator()
        proj_menu.addAction("导出当前点云…", self._export_current_dialog)
        self.act_export_clipped = proj_menu.addAction("导出裁剪内点云…", self._export_clipped_dialog)
        self.act_export_clipped.setEnabled(False)
        self.mesh_menu = proj_menu.addMenu("网格结果")
        self.mesh_menu.aboutToShow.connect(self._populate_mesh_menu)
        proj_menu.addSeparator()
        proj_menu.addAction("退出", self.close)
        self.btn_project.setMenu(proj_menu)
        self.btn_project.setPopupMode(QToolButton.InstantPopup)
        self._register_icon(self.btn_project, "folder")

        self.btn_process = QToolButton()
        self.btn_process.setObjectName("headerBtn")
        self.btn_process.setText("处理")
        process_menu = QMenu(self.btn_process)
        process_menu.addAction("体素降采样…", self._process_voxel)
        process_menu.addAction("随机采样…", self._process_random)
        process_menu.addSeparator()
        process_menu.addAction("统计滤波 (SOR)…", self._process_sor)
        process_menu.addAction("半径滤波 (ROR)…", self._process_ror)
        process_menu.addSeparator()
        process_menu.addAction("盒裁切…", self._process_crop)
        process_menu.addSeparator()
        process_menu.addAction("平面分割…", self._process_plane)
        process_menu.addAction("地面提取…", self._process_ground)
        process_menu.addAction("DBSCAN 聚类…", self._process_dbscan)
        process_menu.addSeparator()
        self.act_extract_selection = process_menu.addAction(
            "提取选中点…", self._extract_selection)
        self.act_delete_selection = process_menu.addAction(
            "删除选中点…", self._delete_selection)
        self.act_extract_selection.setEnabled(False)
        self.act_delete_selection.setEnabled(False)
        process_menu.addSeparator()
        process_menu.addAction("导出测量 CSV…", self._export_measurements)
        process_menu.addAction("清除测量", self._clear_measurement)
        process_menu.addSeparator()
        process_menu.addSeparator()
        process_menu.addAction("ICP 配准（点到点）…", self._process_icp)
        process_menu.addAction("ICP 精配准（点到面）…", self._process_icp_plane)
        process_menu.addAction("全局配准（FPFH + RANSAC）…", self._process_global_registration)
        process_menu.addAction("变换矩阵…", self._open_transform_dialog)
        process_menu.addSeparator()
        process_menu.addAction("估计法线…", self._process_normals)
        process_menu.addAction("到参考点云距离 (C2C)…", self._process_c2c)
        process_menu.addAction("到网格距离 (C2M)…", self._process_c2m)
        process_menu.addAction("Poisson 网格重建…", self._process_mesh)
        self._plugin_menu = process_menu.addMenu("插件")
        self._plugin_menu.setEnabled(False)
        process_menu.addSeparator()
        process_menu.addAction("处理链…", self._open_pipeline_dialog)
        process_menu.addAction("脚本控制台…", self._open_script_dialog)
        self.btn_process.setMenu(process_menu)
        self.btn_process.setPopupMode(QToolButton.InstantPopup)
        self._register_icon(self.btn_process, "sliders")

        self.btn_undo = QToolButton()
        self.btn_undo.setObjectName("headerBtn")
        self.btn_undo.setToolTip("撤销 (Ctrl+Z)")
        self.btn_undo.clicked.connect(self._undo)
        self._register_icon(self.btn_undo, "undo")

        self.btn_redo = QToolButton()
        self.btn_redo.setObjectName("headerBtn")
        self.btn_redo.setToolTip("重做 (Ctrl+Shift+Z)")
        self.btn_redo.clicked.connect(self._redo)
        self._register_icon(self.btn_redo, "redo")

        # ---- 常用视图控件：颜色模式 / 点大小 / 裁剪范围 / 重置视角 ----
        self.cmb_color = QComboBox()
        self.cmb_color.setObjectName("headerCombo")
        self.cmb_color.setMinimumWidth(84)
        color_keys = {mode: index for index, mode in enumerate(COLOR_SHORTCUT_MODES, 1)}
        for mode in COLOR_SHORTCUT_MODES:
            self.cmb_color.addItem(f"{color_keys[mode]}  {COLOR_MODE_LABELS[mode]}", mode)
        self.cmb_color.setToolTip("颜色模式：1 高度 · 2 强度 · 3 RGB · 4 标签 · 5 标量场 · 6 单色；C 循环")

        self.sld_size = QSlider(Qt.Horizontal)
        self.sld_size.setRange(1, 20)
        self.sld_size.setFixedWidth(80)
        self.sld_size.setToolTip("点大小")
        self.lbl_size = QLabel("2.0")
        self.lbl_size.setObjectName("metric")
        self.lbl_size.setFixedWidth(30)
        self.lbl_size.setAlignment(Qt.AlignCenter)

        self.btn_reset = QToolButton()
        self.btn_reset.setObjectName("headerBtn")
        self.btn_reset.setToolTip("重置视角 (R)")
        self.btn_reset.clicked.connect(self._reset_active_view)
        self._register_icon(self.btn_reset, "refresh")

        self.btn_navigation = QToolButton()
        self.btn_navigation.setObjectName("headerBtn")
        self.btn_navigation.setMinimumHeight(32)
        self.btn_navigation.setText("自由视角")
        self.btn_navigation.setCheckable(True)
        self.btn_navigation.setToolTip("固定视角：锁定当前 Z 高度，拖动原地水平转向，滚轮沿 XY 平面平滑前后移动；自由视角：绕中心旋转和缩放")
        self.btn_navigation.toggled.connect(self.viewport.set_fixed_view)
        self.viewport.navigation_changed.connect(self._sync_navigation_button)

        self.btn_clip = QToolButton()
        self.btn_clip.setObjectName("headerBtn")
        self.btn_clip.setToolTip("裁剪范围 (B)：显示/隐藏裁剪线框")
        self.btn_clip.setCheckable(True)
        self.btn_clip.clicked.connect(self._show_clip_popup)
        self._register_icon(self.btn_clip, "crop")

        self.btn_measure = QToolButton()
        self.btn_measure.setObjectName("headerBtn")
        self.btn_measure.setCheckable(True)
        self.btn_measure.setToolTip("测量：连续点击加点，显示折线长度与面积（Esc 清除）")
        self.btn_measure.toggled.connect(self._on_measure_toggled)
        self._register_icon(self.btn_measure, "ruler")

        self.btn_select = QToolButton()
        self.btn_select.setObjectName("headerBtn")
        self.btn_select.setCheckable(True)
        self.btn_select.setToolTip("选点：单击点云查看坐标与属性（Esc 退出）")
        self.btn_select.toggled.connect(self._on_select_toggled)
        self._register_icon(self.btn_select, "target")

        self.btn_box_select = QToolButton()
        self.btn_box_select.setObjectName("headerBtn")
        self.btn_box_select.setCheckable(True)
        self.btn_box_select.setToolTip("3D 框选：Ctrl 拖动建框，三个视图调整，并关联前后帧")
        self.btn_box_select.setText("3D 框选")
        self.btn_box_select.toggled.connect(self._on_box_toggled)
        self._register_icon(self.btn_box_select, "select")

        # ---- 低频操作收进「视图」菜单 ----
        self.btn_view = QToolButton()
        self.btn_view.setObjectName("headerBtn")
        self.btn_view.setText("视图")
        view_menu = QMenu(self.btn_view)
        view_menu.addAction("重置视角", self._reset_active_view)
        self.act_grid = view_menu.addAction("显示地面网格")
        self.act_grid.setCheckable(True)
        self.act_grid.setChecked(True)
        self.act_compare = view_menu.addAction("双窗格对比")
        self.act_compare.setCheckable(True)

        cmap_menu = view_menu.addMenu("色彩映射")
        self._cmap_group = []
        for name in list_colormaps():
            act = cmap_menu.addAction(name)
            act.setCheckable(True)
            act.setData(name)
            act.triggered.connect(lambda checked=False, n=name: self._on_cmap_action(n))
            self._cmap_group.append(act)
        view_menu.addSeparator()
        view_menu.addAction("显示日志 (Ctrl+L)", lambda: self.btn_log.toggle())
        view_menu.addAction("切换主题", self._toggle_theme)
        view_menu.addAction("快捷键帮助", lambda: self.log(HELP_TEXT))
        view_menu.addAction("关于 PointLab Studio", self._show_about)
        self.btn_view.setMenu(view_menu)
        self.btn_view.setPopupMode(QToolButton.InstantPopup)
        self._register_icon(self.btn_view, "eye")

        self.btn_theme = QToolButton()
        self.btn_theme.setObjectName("headerBtn")
        self.btn_theme.setToolTip("切换主题（当前：深色）" if self.theme == "dark" else "切换主题（当前：浅色）")
        self.btn_theme.clicked.connect(self._toggle_theme)
        self._register_icon(self.btn_theme, "sun")

        def vsep():
            sep = QLabel("│")
            sep.setObjectName("headerSep")
            return sep

        # 视图控件聚合为一个胶囊容器（Ins 风格分组）
        tool_group = QWidget()
        tool_group.setObjectName("toolGroup")
        tool_layout = QHBoxLayout(tool_group)
        tool_layout.setContentsMargins(5, 3, 5, 3)
        tool_layout.setSpacing(3)
        for widget in (self.cmb_color, self.sld_size, self.lbl_size,
                       self.btn_measure, self.btn_select, self.btn_box_select,
                       self.btn_clip, self.btn_navigation, self.btn_reset):
            tool_layout.addWidget(widget)

        lay.addWidget(self.btn_open)
        lay.addWidget(self.btn_project)
        lay.addWidget(self.btn_process)
        lay.addWidget(self.btn_view)
        lay.addWidget(vsep())
        lay.addWidget(self.btn_undo)
        lay.addWidget(self.btn_redo)
        lay.addWidget(vsep())
        lay.addWidget(tool_group)
        tools_separator = vsep()
        lay.addWidget(tools_separator)
        lay.addWidget(self.btn_theme)
        self._apply_icons()
        header.set_tools(tool_group, tools_separator)
        return header

    # ---------------- 图标（主题色自适应） ----------------
    def _register_icon(self, btn, name: str):
        btn.setIconSize(QSize(18, 18))
        btn.setMinimumSize(36, 32)
        if btn.text():
            btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        else:
            btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._icon_buttons.append((btn, name))

    def _apply_icons(self):
        color = "#F5F5F7" if self.theme == "dark" else "#1D1D1F"
        for btn, name in self._icon_buttons:
            actual = name
            if btn is self.btn_theme:
                actual = "sun" if self.theme == "dark" else "moon"
            icon = make_icon(actual, color)
            if icon is not None:
                btn.setIcon(icon)

    def _build_bottom_bar(self) -> QWidget:
        from ui.widgets.data_view import ElidedLabel

        bar = QWidget()
        bar.setObjectName("bottomBar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 6, 12, 6)
        lay.setSpacing(16)

        self.status_points = QLabel("0 点")
        self.status_points.setObjectName("metric")
        self.status_mode = ElidedLabel()
        self.status_mode.setObjectName("metric")
        self.status_mode.setStyleSheet("")
        self.status_task = ElidedLabel()
        self.status_task.setObjectName("metric")
        self.status_mem = QLabel("0 MB")
        self.status_mem.setObjectName("metric")
        self.status_fps = QLabel("— FPS")
        self.status_fps.setObjectName("metric")

        lay.addWidget(self.status_points)
        lay.addWidget(self.status_mode, 1)
        lay.addWidget(self.status_task, 1)
        self.btn_cancel_task = QToolButton()
        self.btn_cancel_task.setText("取消任务")
        self.btn_cancel_task.setToolTip("请求取消当前处理；部分算法需等待当前计算阶段结束")
        self.btn_cancel_task.clicked.connect(self._cancel_filter)
        self.btn_cancel_task.hide()
        lay.addWidget(self.btn_cancel_task)
        lay.addWidget(self.status_mem)
        lay.addWidget(self.status_fps)

        self.btn_log = QToolButton()
        self.btn_log.setObjectName("headerBtn")
        self.btn_log.setText("日志")
        self.btn_log.setCheckable(True)
        self.btn_log.setToolTip("展开 / 收起日志")
        self.btn_log.toggled.connect(self._toggle_log)
        self._register_icon(self.btn_log, "list")
        lay.addWidget(self.btn_log)

        from core import __version__
        version = QLabel(f"v{__version__}")
        version.setObjectName("metric")
        lay.addWidget(version)
        return bar

    # ---------------- 裁剪范围弹层 ----------------
    def _sync_navigation_button(self, enabled):
        self.btn_navigation.blockSignals(True)
        self.btn_navigation.setChecked(enabled)
        self.btn_navigation.setText("固定视角" if enabled else "自由视角")
        self.btn_navigation.blockSignals(False)

    def _begin_clip_history(self):
        self.clip_popup._timer.stop()
        self._clip_commit_timer.stop()
        self._commit_clip_history()
        self.clip_popup.hide()

    def _sync_dragged_clip(self, mn, mx):
        self.clip_popup.set_values(mn, mx)
        self.clip_popup.lbl_warning.hide()

    def _clip_state(self) -> dict:
        if self.clip_popup.is_enabled() and self.viewport._clip_box is not None:
            mn, mx = self.viewport._clip_box
            return {"enabled": True, "min": np.array(mn, copy=True), "max": np.array(mx, copy=True)}
        return {"enabled": False, "min": None, "max": None}

    @staticmethod
    def _clip_states_equal(a, b) -> bool:
        if a is None or b is None:
            return a is b
        if bool(a.get("enabled")) != bool(b.get("enabled")):
            return False
        if not a.get("enabled"):
            return True
        return (np.array_equal(a["min"], b["min"]) and np.array_equal(a["max"], b["max"]))

    def _apply_clip_state(self, state: dict):
        """恢复裁剪状态（不写历史，供撤销/重做使用）。"""
        self._applying_clip = True
        try:
            self.btn_clip.blockSignals(True)
            if not state or not state.get("enabled"):
                self.clip_popup.set_enabled(False)
                self.btn_clip.setChecked(False)
                self.viewport.clear_clip_box()
            else:
                self.clip_popup.set_values(state["min"], state["max"])
                self.clip_popup.set_enabled(True)
                self.btn_clip.setChecked(True)
                self.viewport.set_clip_box(state["min"], state["max"])
            self.btn_clip.blockSignals(False)
        finally:
            self._applying_clip = False
        self._clip_committed = self._clip_state()

    def _schedule_clip_commit(self, *args):
        if getattr(self, "_applying_clip", False):
            return
        self._clip_commit_timer.start(350)

    def _commit_clip_history(self):
        current = self._clip_state()
        before = getattr(self, "_clip_committed", {"enabled": False, "min": None, "max": None})
        if self._clip_states_equal(before, current):
            self._clip_committed = current
            return

        def undo():
            self._apply_clip_state(before)

        def redo():
            self._apply_clip_state(current)

        self.history.push("裁剪范围", undo, redo)
        self._clip_committed = current
        self._update_history_buttons()

    def _show_clip_popup(self):
        self.btn_clip.setChecked(self.clip_popup.is_enabled())
        if self.clip_popup.isVisible():
            self.clip_popup.hide()
            return
        self.clip_popup.show_for(self.btn_clip)

    def _build_shortcuts(self):
        shortcuts = {
            "Ctrl+O": self._open_files_dialog,
            "Ctrl+Shift+O": self._open_folder_dialog,
            "Ctrl+P": self._open_project_dialog,
            "Ctrl+S": self._save_project_dialog,
            "Ctrl+E": self._export_current_dialog,
            "Ctrl+L": lambda: self.btn_log.toggle(),
            "Ctrl+Z": self._undo,
            "Ctrl+Shift+Z": self._redo,
            "Ctrl+Y": self._redo,
            "Esc": self._escape_tools,
            "Ctrl+Q": self.close,
        }
        for seq, slot in shortcuts.items():
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(slot)
        for widget in (self.frame_browser.splitter, self.scene_panel.list):
            sc = QShortcut(QKeySequence("Space"), widget)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.setAutoRepeat(False)
            sc.activated.connect(self._toggle_selected_visibility)
        for key, mode in enumerate(COLOR_SHORTCUT_MODES, 1):
            index = self.cmb_color.findData(mode)
            for widget in (self.viewport, self.scene_panel):
                sc = QShortcut(QKeySequence(str(key)), widget)
                sc.setContext(Qt.WidgetWithChildrenShortcut)
                sc.activated.connect(lambda i=index: self.cmb_color.setCurrentIndex(i))

    def _connect_signals(self):
        # 场景面板
        self.scene_panel.entity_selected.connect(self._on_selection_changed)
        self.scene_panel.visibility_changed.connect(self._on_visibility_changed)
        self.scene_panel.focus_requested.connect(self._focus_entity)
        self.scene_panel.remove_requested.connect(self._remove_entity)
        self.scene_panel.remove_selection_requested.connect(self._request_remove_selection)
        self.scene_panel.rename_requested.connect(self._rename_source)
        self.scene_panel.reveal_source_requested.connect(self._reveal_source)
        self.scene_panel.move_requested.connect(self._request_move_sources)
        self.scene_panel.visibility_selection_requested.connect(self._on_visibility_selection)
        self.scene_panel.files_open_requested.connect(self.open_paths)
        self.scene_panel.reload_requested.connect(self._reload_entity)
        self.scene_panel.open_requested.connect(self._open_files_dialog)
        self.scene_panel.open_folder_requested.connect(self._open_folder_dialog)

        # 顶栏视图控件
        self.cmb_color.currentIndexChanged.connect(self._on_header_color)
        self.cmb_color.activated.connect(self._on_header_color_activated)
        self.sld_size.valueChanged.connect(self._on_header_size)
        self.act_grid.toggled.connect(self.viewport.set_grid_visible)
        self.act_grid.toggled.connect(lambda checked: self.frame_browser.secondary.set_grid_visible(checked)
                                     if self.frame_browser.secondary is not None else None)
        self.act_compare.toggled.connect(self.frame_browser.compare.setChecked)
        self.frame_browser.compare.toggled.connect(self.act_compare.setChecked)
        self.frame_browser.previous_requested.connect(self._prev_entity)
        self.frame_browser.next_requested.connect(self._next_entity)
        self.frame_browser.comparison_changed.connect(self._update_frame_browser)

        # 裁剪范围弹层
        self.clip_popup.enabled_changed.connect(self._on_clip_enabled)
        self.clip_popup.values_changed.connect(lambda mn, mx: self.viewport.set_clip_box(mn, mx))
        self.clip_popup.values_changed.connect(self._schedule_clip_commit)
        self.clip_popup.enabled_changed.connect(self._schedule_clip_commit)
        self.clip_popup.fill_requested.connect(self._clip_fill_from_scene)
        self.clip_popup.clear_requested.connect(self._clip_clear)
        self.viewport.clip_drag_started.connect(self._begin_clip_history)
        self.viewport.clip_dragged.connect(self._sync_dragged_clip)
        self.viewport.clip_drag_finished.connect(self._commit_clip_history)
        self.viewport.clip_statistics_changed.connect(self._on_clip_statistics)

        # 视口键盘
        self.viewport.key_next_file.connect(self._next_entity)
        self.viewport.key_prev_file.connect(self._prev_entity)
        self.viewport.key_reset_view.connect(self.viewport.reset_view)
        self.viewport.key_cycle_color.connect(self._cycle_color)
        self.viewport.key_toggle_clipbox.connect(self._toggle_clipbox)
        self.viewport.key_show_help.connect(lambda: self.log(HELP_TEXT))
        self.viewport.key_point_size.connect(self._adjust_point_size)
        self.viewport.fps_updated.connect(self._on_fps)
        self.viewport.files_dropped.connect(self.open_paths)
        self.viewport.measured.connect(lambda d: self.log(f"测量折线累计: {d:.3f} m"))
        self.viewport.mode_changed.connect(self._on_mode_changed)
        self.viewport.selection_changed.connect(self._on_viewport_selection)
        self.viewport.box_creation_requested.connect(self._start_scene_box)
        self.viewport.box_input_enabled = True
        self.viewport.render_problem.connect(lambda message: (self.log(message), self._flash_status(message, 8000)))
        self.overview.colorbar.field_changed.connect(self._on_colorbar_field)
        self.overview.colorbar.range_changed.connect(self._on_colorbar_range)
        self.overview.colorbar.range_reset.connect(self._on_colorbar_reset)
        self._populate_plugin_menu()

    # ---------------- 选中与视图控件同步 ----------------
    def _on_selection_changed(self, entity):
        if self._restoring_project:
            return
        if entity is not None:
            self._show_cloud_browser()
        if entity is not None:
            for other in self.entities:
                other.visible = other is entity or other.visibility_pinned
            if self._auto_mesh is not None:
                self._auto_mesh.visible = False
                self.viewport._refresh_meshes()
                self._auto_mesh = None
            if entity in self._frame_cache:
                self._frame_cache[entity] = time.monotonic()
        self._show_entity(entity)
        self._refresh_scene()
        if entity is not None and entity.xyz is not None and not self._camera_initialized:
            self.viewport.focus_entity(entity)
            self._camera_initialized = True
        self._schedule_frame_loads()

    def _focus_entity(self, entity):
        if entity.xyz is None:
            self._focus_when_loaded = entity
            self._schedule_frame_loads()
        else:
            self.viewport.focus_entity(entity)

    def _sync_view_controls(self, entity):
        if (hasattr(self, "recording_browser") and
                self.content_stack.currentWidget() is self.recording_browser):
            frames = self.recording_browser.current_frames
            self._recording_frame_changed(frames[0] if frames else None)
            return
        self._sync_guard = True
        enabled = entity is not None
        self.cmb_color.setEnabled(enabled)
        self.sld_size.setEnabled(enabled)
        if entity is None:
            self.cmb_color.setCurrentIndex(0)
            self.sld_size.setValue(4)
            self.lbl_size.setText("2.0")
        else:
            idx = self.cmb_color.findData(entity.color_mode)
            self.cmb_color.setCurrentIndex(max(0, idx))
            self.sld_size.setValue(int(entity.point_size * 2))
            self.lbl_size.setText(f"{entity.point_size:.1f}")
        for act in getattr(self, "_cmap_group", []):
            act.setChecked(bool(entity is not None and act.data() == entity.colormap))
        self._sync_guard = False

    def _show_entity(self, entity):
        self.overview.set_entity(entity)
        self._sync_view_controls(entity)

    def _on_header_color(self, _index):
        if getattr(self, "_sync_guard", False):
            return
        if self.content_stack.currentWidget() is self.recording_browser:
            self.recording_browser.set_color_mode(self.cmb_color.currentData())
            return
        entity = self.scene_panel.current_entity()
        if entity is None:
            return
        mode = self.cmb_color.currentData()
        if mode == "field" and not entity.color_field:
            candidates = [name for name, field in entity.fields.items()
                          if name != "rgb" and np.ndim(field.data) == 1]
            if not candidates:
                self.log("该点云没有可用标量场，无法按字段着色")
                self._sync_view_controls(entity)
                return
            entity.color_field = candidates[0]
        entity.color_mode = mode
        self.overview.set_entity(entity)
        self._refresh_scene()

    def _on_header_color_activated(self, index):
        # Selecting RGB again is meaningful while the effective display is
        # image RGB: return to the source RGB field, even if the index is equal.
        if (self.content_stack.currentWidget() is self.recording_browser
                and self.recording_browser.calibration_panel.colorize.isChecked()):
            self._on_header_color(index)

    def _on_colorbar_field(self, name: str):
        if self.content_stack.currentWidget() is self.recording_browser:
            self.recording_browser.set_color_field("" if name == "height" else name)
            return
        entity = self.scene_panel.current_entity()
        if entity is None:
            return
        if name == "height":
            entity.color_field = ""
            entity.color_mode = "height"
        else:
            entity.color_field = name
            entity.color_mode = "field"
        self._sync_view_controls(entity)
        self.overview.set_entity(entity)
        self._refresh_scene()

    def _on_colorbar_range(self, lo: float, hi: float):
        if self.content_stack.currentWidget() is self.recording_browser:
            self.recording_browser.set_color_range((lo, hi))
            return
        entity = self.scene_panel.current_entity()
        if entity is None:
            return
        entity.color_range = (float(lo), float(hi))
        self._refresh_scene()

    def _on_colorbar_reset(self):
        if self.content_stack.currentWidget() is self.recording_browser:
            self.recording_browser.set_color_range(None)
            return
        entity = self.scene_panel.current_entity()
        if entity is None:
            return
        entity.color_range = None
        self._refresh_scene()

    def _on_cmap_action(self, name: str):
        if self.content_stack.currentWidget() is self.recording_browser:
            self.recording_browser.set_colormap(name)
            return
        entity = self.scene_panel.current_entity()
        if entity is None:
            return
        entity.colormap = name
        for act in self._cmap_group:
            act.setChecked(act.data() == name)
        self.overview.set_entity(entity)
        self._refresh_scene()

    def _on_header_size(self, value: int):
        if getattr(self, "_sync_guard", False):
            return
        if self.content_stack.currentWidget() is self.recording_browser:
            self.recording_browser.set_point_size(value / 2.0)
            self.lbl_size.setText(f"{value / 2.0:.1f}")
            return
        entity = self.scene_panel.current_entity()
        if entity is None:
            return
        entity.point_size = value / 2.0
        self.lbl_size.setText(f"{entity.point_size:.1f}")
        self.overview.v_pointsize.setText(f"{entity.point_size:.1f}  px")
        self.viewport.set_point_size()
        self.frame_browser.set_reference(self._reference_entity())

    def _toggle_selected_visibility(self):
        selection = self.scene_panel._selection_payload()
        if not selection["entities"]:
            self.log("请先在左侧场景中选择一片点云")
            return
        action = "show" if any(not e.visible for e in selection["entities"]) else "hide"
        self._on_visibility_selection(selection, action)

    def _on_visibility_selection(self, selection, action):
        targets = [e for e in dict.fromkeys(selection.get("entities", [])) if e in self.entities]
        if not targets:
            return
        before = [(e, e.visible, e.visibility_pinned) for e in targets]
        after = []
        for entity, visible, pinned in before:
            if action == "show":
                visible = True
            elif action == "hide":
                visible, pinned = False, False
            elif action == "pin":
                visible, pinned = True, True
            elif action == "unpin":
                pinned = False
            else:
                return
            after.append((entity, visible, pinned))

        def apply(states):
            for entity, visible, pinned in states:
                entity.visible, entity.visibility_pinned = visible, pinned
            self._refresh_scene()
        if before == after:
            return
        label = {"show": "临时显示", "hide": "隐藏", "pin": "固定叠加", "unpin": "取消固定叠加"}[action]
        self.history.push(f"{label}（{len(targets)} 片点云）",
                          lambda: apply(before), lambda: apply(after))
        apply(after)
        self._update_history_buttons()

    def _on_visibility_changed(self, entity):
        visible = entity.visible
        pinned_before = entity.visibility_pinned

        def apply(value, pinned):
            entity.visible = value
            entity.visibility_pinned = pinned
            self._refresh_scene()
            self._schedule_frame_loads()

        self.history.push(f"显示/隐藏 {entity.name}",
                          lambda: apply(not visible, pinned_before), lambda: apply(visible, visible))
        self._update_history_buttons()
        apply(visible, visible)

    # ---------------- P4：测量与选点 ----------------
    def _on_measure_toggled(self, checked: bool):
        if getattr(self, "_sync_guard", False):
            return
        self._set_tool("measure" if checked else None)

    def _on_select_toggled(self, checked: bool):
        if getattr(self, "_sync_guard", False):
            return
        self._set_tool("select" if checked else None)

    def _on_box_toggled(self, checked: bool):
        if getattr(self, "_sync_guard", False):
            return
        if self.content_stack.currentWidget() is self.recording_browser:
            control = self.recording_browser.tracking
            if checked:
                self.recording_browser.show_box_editor()
                if control._track() is None:
                    control.set_mode("box")
            elif control.dialog is not None:
                control.dialog.close()
        elif checked:
            self._start_scene_box()
        else:
            self._set_tool(None)

    def _start_scene_box(self):
        if self._scene_box_tool is None:
            from ui.widgets.scene_box import SceneBoxController
            self._scene_box_tool = SceneBoxController(self)
        if not self._scene_box_tool.start():
            self._sync_guard = True
            self.btn_box_select.setChecked(False)
            self._sync_guard = False
            self._flash_status("请先打开点云")

    def _set_tool(self, mode):
        if self._scene_box_tool is not None and self._scene_box_tool.active:
            self._scene_box_tool.close()
        if mode == "box":
            self._start_scene_box()
            return
        if mode is not None and not any(
                e.visible and e.xyz is not None and len(e.xyz) for e in self.entities):
            self._sync_guard = True
            for btn in (self.btn_measure, self.btn_select, self.btn_box_select):
                btn.setChecked(False)
            self._sync_guard = False
            self.log("请先打开点云，再使用测量/选点工具")
            self._flash_status("请先打开点云")
            return
        self._sync_guard = True
        self.btn_measure.setChecked(mode == "measure")
        self.btn_select.setChecked(mode == "select")
        self.btn_box_select.setChecked(mode == "box")
        self._sync_guard = False
        if mode != "box":
            self._clear_point_selection()
        self.viewport.set_interaction_mode(mode)
        if mode == "measure":
            self.log("测量模式：连续点击点云加点，Esc 清除")
        elif mode == "select":
            self.log("选点模式：单击一个点查看坐标与属性（Esc 退出）")
        elif mode == "box":
            self.log("框选模式：拖拽矩形选择点（Esc 退出）")

    def _escape_tools(self):
        if self.content_stack.currentWidget() is self.recording_browser:
            control = self.recording_browser.tracking
            control.set_mode(None)
            return
        if self._scene_box_tool is not None and self._scene_box_tool.active:
            self._scene_box_tool.close()
            return
        self.viewport.cancel_clip_drag()
        if getattr(self.viewport, "_mode", None) is not None:
            self._set_tool(None)

    def _on_mode_changed(self, mode: str):
        if self._scene_box_tool is not None and self._scene_box_tool.active:
            mode = "box"
        self._sync_guard = True
        self.btn_measure.setChecked(mode == "measure")
        self.btn_select.setChecked(mode == "select")
        self.btn_box_select.setChecked(mode == "box")
        self._sync_guard = False
        if mode != "box":
            self._clear_point_selection()
        self.status_mode.setText(self._mode_status_text())

    # ---------------- E18：框选 ----------------
    def _on_viewport_selection(self, selection):
        if self._scene_box_tool is not None and self._scene_box_tool.armed:
            self._scene_box_tool.initialize(selection)
            return
        self._selection = list(selection or [])
        total = int(sum(len(idx) for _, idx in self._selection))
        if total:
            self.status_mode.setText(
                f"● 已选 {total:,} 点（{len(self._selection)} 片） · Esc 清除")
            self._flash_status(f"已选 {total:,} 个点")
        for act in (getattr(self, "act_extract_selection", None),
                    getattr(self, "act_delete_selection", None)):
            if act is not None:
                act.setEnabled(total > 0)

    def _clear_point_selection(self):
        if getattr(self, "_selection", None):
            self._selection = []
        if hasattr(self, "viewport"):
            self.viewport.clear_selection_runtime()
        for act in (getattr(self, "act_extract_selection", None),
                    getattr(self, "act_delete_selection", None)):
            if act is not None:
                act.setEnabled(False)

    def _selection_snapshot(self):
        selection = [s for s in getattr(self, "_selection", [])
                     if s[1] is not None and len(s[1])]
        if not selection:
            self.log("还没有选中点：先用框选/套索工具选择，或按 Esc 退出当前工具")
            self._flash_status("没有选中点")
            return []
        return selection

    def _extract_selection(self):
        selection = self._selection_snapshot()
        if not selection:
            return
        made = []
        for source, indices in selection:
            indices = np.asarray(indices)
            xyz = np.asarray(source.xyz[indices], dtype=np.float32)
            entity = self._make_result(source, f"框选 {len(indices):,} 点",
                                       xyz, source.subset_fields(indices),
                                       f"框选提取，来自 {source.name}")
            self._add_entity(entity)
            self._push_add_history(entity)
            made.append(entity)
        count = int(sum(len(i) for _, i in selection))
        self.log(f"提取选中点：{count:,} 点 → {len(made)} 个新点云")
        self._update_status()
        self._clear_point_selection()

    def _delete_selection(self):
        selection = self._selection_snapshot()
        if not selection:
            return
        made = []
        removed = 0
        for source, indices in selection:
            keep = np.ones(len(source.xyz), dtype=bool)
            keep[np.asarray(indices)] = False
            removed += int(len(indices))
            if not keep.any():
                self.log(f"{source.name} 的所有点都被选中，跳过删除（结果为空）")
                continue
            xyz = np.asarray(source.xyz[keep], dtype=np.float32)
            entity = self._make_result(source, f"删除选中 {int((~keep).sum()):,} 点",
                                       xyz, source.subset_fields(keep),
                                       f"删除选中点，来自 {source.name}")
            self._add_entity(entity)
            self._push_add_history(entity)
            made.append(entity)
        self.log(f"删除选中点：{removed:,} 点 → {len(made)} 个剩余点云")
        self._update_status()
        self._clear_point_selection()

    # ---------------- E19：测量扩展 ----------------
    def _clear_measurement(self):
        self.viewport.clear_measurement()
        self.log("已清除测量折线")

    def _write_measurements(self, path: str) -> bool:
        summary = self.viewport.measurement_summary()
        if len(summary["points"]) == 0:
            self.log("没有可导出的测量结果：先切到测量模式并点击点云")
            self._flash_status("没有测量结果")
            return False
        ok = write_measurements_csv(summary, path)
        if ok:
            self.log(f"测量结果已导出: {path}（{len(summary['points'])} 点，"
                     f"折线 {summary['length']:.3f} m，面积 {summary['area']:.3f} m²）")
            self._flash_status("测量 CSV 已导出")
        else:
            self.log(f"测量导出失败: {path}")
            self._flash_status("导出失败")
        return ok

    def _export_measurements(self):
        summary = self.viewport.measurement_summary()
        if len(summary["points"]) == 0:
            self.log("没有可导出的测量结果：先切到测量模式并点击点云")
            self._flash_status("没有测量结果")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出测量 CSV", "measurements.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        self._write_measurements(path)



    # ================= Auto-Ingest 加载 =================
    def open_paths(self, paths: list):
        paths = [os.path.abspath(p) for p in paths if p]
        workspaces = [p for p in paths if p.lower().endswith(".pls")]
        if workspaces:
            if len(paths) != 1:
                self._flash_status("请单独打开一个工作区，再添加其他点云文件。", 6000)
                return
            self._open_project_path(workspaces[0])
            return
        packages = [p for p in paths if os.path.isfile(p) and is_data_source(p)]
        if packages:
            paths = [p for p in paths if p not in packages]
            prompt = any(os.path.splitext(p)[1].lower() in (".pcap", ".pcapng", ".mcap", ".bag", ".db3")
                         for p in packages)
            QTimer.singleShot(0, lambda: self._open_recording_sources(packages, prompt=prompt))
        if not paths:
            return
        if self._source_operation is not None:
            self._deferred_imports.extend(paths)
            self.log("路径操作进行中，已排队追加打开文件。")
            return
        self.scene_panel.add_roots([p for p in paths if os.path.isdir(p)])
        for source in paths:
            if os.path.exists(source):
                self.settings.add_recent(source)
        self._rebuild_recent_menu()
        if any(os.path.isdir(p) for p in paths):
            thread = ScanThread(paths, self)
            self._scan_threads.append(thread)
            progress = SmoothProgressDialog("正在发现文件…", "取消", 0, 0, self)
            progress.setWindowTitle("发现文件夹内容")
            progress.setMinimumDuration(0)
            progress.setMinimumWidth(400)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            thread.discovery_progress = progress
            thread.result_received = False

            def update_discovery(update):
                if self._closing or progress.wasCanceled():
                    return
                label = discovery_label(update)
                progress.setLabelText(label)
                self.status_task.setText(label.replace("\n", " · "))

            def cancel_discovery():
                if not thread.result_received and thread.isRunning():
                    thread.requestInterruption()
                    self.status_task.setText("正在取消文件夹发现…")

            progress.canceled.connect(cancel_discovery)
            thread.progress.connect(update_discovery)
            thread.done.connect(lambda result, t=thread: self._register_scan(result, t))
            thread.failed.connect(self.log)
            thread.finished.connect(lambda t=thread: self._finish_scan(t))
            self.log("正在扫描文件夹…")
            progress.show()
            thread.start()
        else:
            self._register_files(paths)

    def _finish_scan(self, thread):
        if thread in self._scan_threads:
            self._scan_threads.remove(thread)
        progress = getattr(thread, "discovery_progress", None)
        if progress is not None:
            progress.close()
            progress.deleteLater()
            if not thread.result_received and not self._closing:
                self.status_task.setText(
                    "文件夹发现已取消" if thread.isInterruptionRequested()
                    else "文件夹发现未完成，请查看日志"
                )
        thread.deleteLater()
        if not self._scan_threads:
            pending, self._pending_regroups = self._pending_regroups, []
            for paths, destination in pending:
                self._regroup_sources(paths, destination)

    def _register_scan(self, result, thread=None):
        if thread is not None:
            thread.result_received = True
            thread.discovery_progress.close()
        if not self._closing:
            self.scene_panel.add_directories(result["directories"])
            self._register_files(result["files"])
            if result.get("data_sources"):
                sources = result["data_sources"]
                prompt = any(os.path.splitext(path)[1].lower() in (".pcap", ".pcapng", ".mcap", ".bag", ".db3")
                             or os.path.isfile(os.path.join(path, "metadata.yaml")) for path in sources)
                self._open_recording_sources(sources, prompt=prompt, discovered=True)

    def _register_files(self, files):
        if self._closing:
            return
        known = {os.path.normcase(e.path) for e in self.entities if e.path}
        added = []
        self.scene_panel.list.setUpdatesEnabled(False)
        try:
            for path in sorted(set(files), key=natural_key):
                path = os.path.abspath(path)
                key = os.path.normcase(path)
                if key in known:
                    continue
                known.add(key)
                entity = CloudEntity(os.path.basename(path), path=path, visible=False,
                                     load_state="unloaded", loaded=0.0)
                self._apply_pending(entity)
                self.entities.append(entity)
                self._known_source_entities.add(entity)
                self._frame_cache[entity] = time.monotonic()
                added.append(entity)
            if added:
                self.scene_panel.set_entities(self.entities)
        finally:
            self.scene_panel.list.setUpdatesEnabled(True)
        if added:
            self.log(f"已列出 {len(added)} 帧，按浏览位置加载。")
            if not self._restoring_project:
                self.scene_panel.select_entity(added[0])
        elif not files:
            self.log("没有找到支持的点云文件。")
        self._refresh_scene()
        if not self._restoring_project:
            self._schedule_frame_loads()

    MAX_LOAD_THREADS = 2
    FRAME_CACHE_COUNT = 8
    FRAME_CACHE_BYTES = 512 * 1024 * 1024

    def _reference_entity(self):
        if not self.frame_browser.compare.isChecked():
            return None
        current = self.scene_panel.current_entity()
        if current not in self.entities:
            return None
        index = self.entities.index(current) + self.frame_browser.direction.currentData()
        return self.entities[index] if 0 <= index < len(self.entities) else None

    def _update_frame_browser(self):
        current = self.scene_panel.current_entity()
        index = self.entities.index(current) if current in self.entities else -1
        self.frame_browser.set_current(current, index, len(self.entities))
        self.frame_browser.set_reference(self._reference_entity())
        self._schedule_frame_loads()

    def _wanted_frames(self):
        current = self.scene_panel.current_entity()
        wanted = [current, self._reference_entity()]
        wanted.extend(self._explicit_loads)
        wanted.extend(e for e in self.entities if e.visible)
        if current in self.entities:
            index = self.entities.index(current)
            wanted.extend(self.entities[i] for i in (index + 1, index - 1)
                          if 0 <= i < len(self.entities))
        return list(dict.fromkeys(e for e in wanted if e is not None))

    def _schedule_frame_loads(self):
        if self._closing or self._restoring_project or self._source_operation is not None:
            return
        wanted = self._wanted_frames()
        for thread in self._threads.values():
            if thread.entity not in wanted:
                thread.requestInterruption()
        self._load_queue = [e.path for e in wanted if e.path and e.load_state == "unloaded"]
        self._pump_load_queue()

    def _evict_frame_cache(self):
        if self._filter_thread is not None:
            return
        protected = set(self._wanted_frames())
        live = set(self.entities)
        candidates = [e for e in self._frame_cache if e.xyz is not None and e in live]
        sizes = {e: e.xyz.nbytes + sum(f.data.nbytes for f in e.fields.values()) for e in candidates}
        total = sum(sizes.values())
        count = len(candidates)
        for entity in sorted(candidates, key=self._frame_cache.get):
            if count <= self.FRAME_CACHE_COUNT and total <= self.FRAME_CACHE_BYTES:
                break
            if entity in protected or entity.path in self._threads:
                continue
            total -= sizes[entity]
            count -= 1
            entity.xyz, entity.fields = None, {}
            entity.load_state, entity.loaded = "unloaded", 0.0
            self.scene_panel.update_entity(entity)

    def _start_full_load(self, path: str):
        if self._closing or path in self._threads:
            return
        entity = next((e for e in self.entities if e.path == path), None)
        if entity is None or entity.load_state != "unloaded":
            return
        thread = LoadThread(path, self.options, self)
        thread.entity = entity
        entity.load_state = "loading"
        self.scene_panel.update_entity(entity)
        thread.progress.connect(self._on_load_progress)
        thread.preview.connect(self._on_preview_loaded)
        thread.done.connect(self._on_full_loaded)
        thread.failed.connect(self._on_load_failed)
        thread.finished.connect(lambda p=path: self._on_thread_finished(p))
        self._threads[path] = thread
        thread.start()

    def _pump_load_queue(self):
        if self._closing:
            self._load_queue.clear()
            return
        if self._source_operation is not None:
            return
        while self._load_queue and len(self._threads) < self.MAX_LOAD_THREADS:
            path = self._load_queue.pop(0)
            if path in self._threads:
                continue
            self._start_full_load(path)

    def _add_entity(self, entity: CloudEntity):
        self.entities.append(entity)
        self._known_source_entities.add(entity)
        self._apply_pending(entity)
        self.scene_panel.add_entity(entity)
        if not self._restoring_project:
            self.scene_panel.select_entity(entity)
            if not self._camera_initialized and entity.xyz is not None:
                self.viewport.reset_view()
                self._camera_initialized = True

    def _accept_loaded(self, path, fresh, *, complete):
        thread = self._threads.get(path)
        if self._closing or thread is None or thread.isInterruptionRequested():
            return
        entity = thread.entity
        if entity not in self.entities:
            return
        for name in ("xyz", "fields", "info", "debug", "dim_hint", "tier", "loaded"):
            setattr(entity, name, getattr(fresh, name))
        entity.load_state = "ready" if complete else "loading"
        self._frame_cache[entity] = time.monotonic()
        self.scene_panel.update_entity(entity)
        if entity is self.scene_panel.current_entity():
            self._show_entity(entity)
        if entity.visible or entity is self._reference_entity():
            self._refresh_scene()
        if entity is self.scene_panel.current_entity() and (
                not self._camera_initialized or getattr(self, "_focus_when_loaded", None) is entity):
            self.viewport.focus_entity(entity)
            self._camera_initialized = True
            self._focus_when_loaded = None

    def _on_preview_loaded(self, path, entity):
        self._accept_loaded(path, entity, complete=False)

    def _on_full_loaded(self, path: str, entity: CloudEntity):
        self._accept_loaded(path, entity, complete=True)

    def _on_load_failed(self, path: str, error: str):
        thread = self._threads.get(path)
        if thread is not None and thread.entity in self.entities:
            thread.entity.load_state = "error"
            self.scene_panel.update_entity(thread.entity)
            if thread.entity is self.scene_panel.current_entity():
                self._show_entity(thread.entity)
            self._update_frame_browser()
        self.log(f"后台加载失败: {os.path.basename(path)} — {error}")
        self._flash_status(f"后台加载失败：{os.path.basename(path)}，详情见日志", 6000)

    def _on_thread_finished(self, path: str):
        """线程真正结束后再释放（避免 QThread 在运行中被 GC 销毁）。"""
        thread = self._threads.pop(path, None)
        if thread is not None:
            if thread.entity.load_state == "loading":
                thread.entity.load_state = "unloaded"
            thread.deleteLater()
        self._evict_frame_cache()
        self._schedule_frame_loads()
        self._update_status()

    def _on_load_progress(self, path: str, fraction: float):
        entity = next((e for e in self.entities if e.path == path), None)
        if entity is not None:
            self.overview.set_loading_progress(entity, fraction)

    def _apply_pending(self, entity: CloudEntity):
        state = self._pending_display.pop(entity.path, None)
        if state:
            entity.visible = state.get("visible", True)
            entity.visibility_pinned = state.get("visibility_pinned", entity.visible)
            mode = state.get("color_mode", "height")
            entity.color_mode = mode if mode in COLOR_MODES else "height"
            entity.colormap = state.get("colormap", "default")
            entity.color_field = state.get("color_field", "")
            color_range = state.get("color_range")
            entity.color_range = tuple(color_range) if color_range is not None else None
            entity.point_size = state.get("point_size", 2.0)

    # ================= 场景操作 =================
    def _remove_entity(self, entity: CloudEntity):
        self._remove_selection({"entities": [entity], "folders": []})

    def _scene_snapshot(self):
        snapshot = SceneSnapshot(
            list(self.entities), self.scene_panel.current_entity(),
            deepcopy(self.scene_panel.tree_state()),
            [(e, e.visible, e.visibility_pinned) for e in self.entities],
            set(self._explicit_loads),
        )
        self._scene_snapshots.add(snapshot)
        return snapshot

    def _restore_scene_snapshot(self, snapshot):
        live = set(snapshot.entities)
        for thread in self._threads.values():
            if thread.entity not in live:
                thread.requestInterruption()
        self._load_queue = [p for p in self._load_queue if any(e.path == p for e in live)]
        self._frame_cache = {e: stamp for e, stamp in self._frame_cache.items() if e in live}
        self.entities[:] = snapshot.entities
        self._known_source_entities.update(self.entities)
        self._explicit_loads = set(snapshot.explicit)
        self._restoring_project = True
        try:
            self.scene_panel.restore_tree_state(snapshot.tree)
            self.scene_panel.set_entities(self.entities)
            for entity, visible, pinned in snapshot.visibility:
                entity.visible, entity.visibility_pinned = visible, pinned
                if entity.path:
                    self._frame_cache[entity] = time.monotonic()
            if snapshot.current in self.entities:
                self.scene_panel.select_entity(snapshot.current)
            self.scene_panel.restore_tree_state(snapshot.tree)
        finally:
            self._restoring_project = False
        self._show_entity(self.scene_panel.current_entity())
        self._refresh_scene()

    def _selection_source_paths(self, selection):
        from core.io.source_ops import minimal_source_paths
        paths = list(selection.get("source_paths", []))
        paths.extend(e.path for e in selection.get("entities", []) if e.path)
        paths.extend(self.scene_panel.source_path(folder) for folder in selection.get("folders", []))
        return minimal_source_paths(paths)

    def _choose_delete_mode(self, selection):
        try:
            paths = self._selection_source_paths(selection)
        except (OSError, ValueError):
            paths = []
        box = QMessageBox(self)
        box.setWindowTitle("删除所选项目")
        box.setText(f"已选择 {len(selection.get('entities', []))} 片点云、{len(selection.get('folders', []))} 个文件夹。")
        box.setInformativeText("仅移出工作区会保留硬盘原文件。移到回收站会同步删除原位置的文件；选中文件夹时包含其中的全部内容。")
        box.setDetailedText("\n".join(paths) or "所选内容没有磁盘源文件。")
        scene = box.addButton("仅移出工作区", QMessageBox.AcceptRole)
        disk = box.addButton("移到回收站", QMessageBox.DestructiveRole)
        disk.setEnabled(bool(paths))
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(scene)
        box.exec()
        return "scene" if box.clickedButton() is scene else "disk" if box.clickedButton() is disk else None

    def _request_remove_selection(self, selection):
        if self._source_operation is not None or self._scan_threads:
            self._flash_status("正在扫描或操作文件，请稍后再删除。", 6000)
            return
        mode = self._choose_delete_mode(selection)
        if mode == "scene":
            self._remove_selection(selection)
        elif mode == "disk":
            self._trash_selection(selection)

    def _trash_selection(self, selection):
        from core.io.source_ops import trash_sources
        if not self._source_edit_allowed():
            return False
        try:
            paths = self._selection_source_paths(selection)
        except (OSError, ValueError) as exc:
            self._flash_status(str(exc), 6000)
            return False
        if not paths:
            return self._remove_selection(selection)

        def completed(moves):
            self._record_trashed_selection(moves, selection)
        def failed(error):
            moves = getattr(error, "remaining_moves", [])
            if moves:
                self._record_trashed_selection(moves, selection)
            self.log("移到回收站未全部完成：" + str(error))
            self._flash_status("部分文件未删除，请查看日志。", 7000)
        return self._start_source_operation(lambda: trash_sources(paths), "移到回收站",
                                            on_success=completed, on_failure=failed)

    def _record_trashed_selection(self, moves, selection):
        def affected(path):
            return bool(path) and any(path == old or path.startswith(old.rstrip(os.sep) + os.sep)
                                      for old, _trash in moves)
        targets = [e for e in self.entities if affected(e.path) or
                   (not e.path and e in selection.get("entities", []))]
        folders = [folder for folder in selection.get("folders", [])
                   if affected(self.scene_panel.source_path(folder))]
        result = self._remove_selection({"entities": targets, "folders": folders},
                                        record_history=False, expand_folders=False)
        if result is None:
            return
        before, after = result
        record = TrashRecord(before, after, [old for old, _trash in moves], list(moves))
        self._trash_records.add(record)
        self.history.push(f"移到回收站（{len(moves)} 项）",
                          lambda: self._restore_trash_history(record),
                          lambda: self._redo_trash_history(record))
        self.log(f"已将 {len(moves)} 项原文件移到回收站；撤销会先恢复原文件。")
        self._update_history_buttons()

    def _run_history_source_operation(self, worker, label, on_success, on_failure):
        """Wait in a Qt event loop so history advances only after a safe disk operation."""
        loop = QEventLoop(self)
        succeeded = False
        def completed(changes):
            nonlocal succeeded
            on_success(changes)
            succeeded = True
        if not self._start_source_operation(worker, label, on_success=completed, on_failure=on_failure):
            return False
        self._source_operation.finished.connect(loop.quit)
        loop.exec()
        return succeeded

    def _restore_trash_history(self, record):
        from core.io.source_ops import restore_trashed
        if any(not trash for _old, trash in record.moves):
            self.log("系统未提供可恢复路径，请在系统回收站中恢复原文件。")
            self._flash_status("请先从系统回收站恢复原文件。", 7000)
            return False
        def completed(_changes):
            record.moves[:] = []
            self._apply_removal_history(record.before, record.after, restore=True)
        def failed(error):
            restored = {original for _trash, original in getattr(error, "remaining_moves", [])}
            record.moves[:] = [(original, trash) for original, trash in record.moves
                               if original not in restored]
            self.log("恢复原文件失败，未覆盖现有文件，可处理冲突后重试撤销：" + str(error))
            self._flash_status("恢复未完成；请处理同名冲突后重试撤销。", 7000)
        return self._run_history_source_operation(lambda: restore_trashed(record.moves),
                                                 "恢复原文件", completed, failed)

    def _redo_trash_history(self, record):
        from core.io.source_ops import trash_sources
        def completed(moves):
            record.moves[:] = moves
            self._apply_removal_history(record.before, record.after, restore=False)
        def failed(error):
            moves = list(getattr(error, "remaining_moves", []))
            record.moves[:] = moves
            if moves:
                self._record_trashed_selection(moves, {"entities": [], "folders": []})
            self.log("重新移到回收站失败：" + str(error))
            self._flash_status("重做未完成，请查看日志。", 7000)
        return self._run_history_source_operation(lambda: trash_sources(record.sources),
                                                 "重新移到回收站", completed, failed)

    def _remove_selection(self, selection, *, record_history=True, expand_folders=True):
        if record_history and (self._source_operation is not None or self._scan_threads):
            self._flash_status("正在扫描或移动文件，请稍后再移除。", 6000)
            return
        folders = selection.get("folders", [])
        requested = set(selection.get("entities", []))
        targets = {
            e for e in self.entities
            if e in requested or (expand_folders and e.path and any(
                self.scene_panel.display_path(e.path) == p or
                self.scene_panel.display_path(e.path).startswith(p.rstrip(os.sep) + os.sep) for p in folders
            ))
        }
        before = self._scene_snapshot()
        roots = [p for p in before.tree.get("roots", []) if not any(
            self.scene_panel.display_path(p) == f or
            self.scene_panel.display_path(p).startswith(f.rstrip(os.sep) + os.sep) for f in folders
        )]
        if not targets and not folders:
            return
        remaining = [e for e in self.entities if e not in targets]
        current = before.current
        if current not in remaining:
            index = self.entities.index(current) if current in self.entities else 0
            current = remaining[min(index, len(remaining) - 1)] if remaining else None
        tree = deepcopy(before.tree)
        tree["roots"] = roots
        for key in ("directories", "expanded"):
            tree[key] = [p for p in tree.get(key, []) if not any(
                (self.scene_panel.display_path(p) if key == "directories" else p) == f or
                (self.scene_panel.display_path(p) if key == "directories" else p).startswith(f.rstrip(os.sep) + os.sep)
                for f in folders
            )]
        target_paths = {e.path for e in targets if e.path}
        remaining_paths = [e.path for e in remaining if e.path]
        tree["placements"] = {source: group for source, group in tree.get("placements", {}).items()
                              if source not in target_paths and (any(
                                  p == source or p.startswith(source.rstrip(os.sep) + os.sep) for p in remaining_paths)
                                  or not any(group == f or group.startswith(f.rstrip(os.sep) + os.sep) for f in folders))}
        after = SceneSnapshot(
            remaining, current, tree,
            [(e, e.visible or e is current, e.visibility_pinned) for e in remaining],
            before.explicit - targets,
        )
        self._scene_snapshots.add(after)
        self._restore_scene_snapshot(after)
        label = f"删除所选（{len(targets)} 片点云）"
        if record_history:
            self.history.push(label, lambda: self._apply_removal_history(before, after, restore=True),
                              lambda: self._apply_removal_history(before, after, restore=False))
        self.log(f"已从场景移除 {len(targets)} 片点云；磁盘文件未删除，可整批撤销。")
        self._update_history_buttons()
        return before, after

    def _apply_removal_history(self, before, after, *, restore):
        """Replay only this removed batch; later append-imports stay in the scene."""
        targets = set(before.entities) - set(after.entities)
        live = self._scene_snapshot()
        entities = list(live.entities)
        if restore:
            for index, entity in enumerate(before.entities):
                if entity not in targets or entity in entities:
                    continue
                anchor = next((e for e in before.entities[index + 1:] if e in entities), None)
                entities.insert(entities.index(anchor) if anchor is not None else len(entities), entity)
        else:
            entities = [e for e in entities if e not in targets]
        tree = deepcopy(live.tree)
        for key in ("roots", "directories", "expanded"):
            removed = set(before.tree.get(key, [])) - set(after.tree.get(key, []))
            tree[key] = (list(dict.fromkeys([*tree.get(key, []), *removed])) if restore
                         else [p for p in tree.get(key, []) if p not in removed])
        placements = tree.setdefault("placements", {})
        removed_placements = {p: group for p, group in before.tree.get("placements", {}).items()
                              if p not in after.tree.get("placements", {})}
        if restore:
            placements.update(removed_placements)
        else:
            for source in removed_placements:
                placements.pop(source, None)
        current = live.current if live.current in entities else None
        if current is None:
            current = before.current if restore and before.current in entities else (
                entities[0] if entities else None)
        visibility = {e: (visible, pinned) for e, visible, pinned in live.visibility}
        if restore:
            visibility.update({e: (visible, pinned) for e, visible, pinned in before.visibility
                               if e in targets})
        snapshot = SceneSnapshot(
            entities, current, tree,
            [(e, *visibility.get(e, (e.visible, e.visibility_pinned))) for e in entities],
            (live.explicit | (before.explicit & targets)) if restore else live.explicit - targets,
        )
        self._restore_scene_snapshot(snapshot)

    def _source_edit_allowed(self):
        if (self._source_operation is not None or self._threads or self._scan_threads
                or self._filter_thread is not None or self._restoring_project):
            self._flash_status("文件正在加载、处理或移动，请稍后再重命名／移动。", 6000)
            self.log("路径操作未执行：请等待当前加载／处理任务结束。")
            return False
        return True

    def _start_source_operation(self, worker, label, imports=(), *, on_success=None, on_failure=None):
        if not self._source_edit_allowed():
            return False
        if self._clip_commit_timer.isActive():
            self._clip_commit_timer.stop()
            self._commit_clip_history()
        thread = SourceOperationThread(worker, self)
        self._source_operation = thread
        thread.done.connect(on_success or (lambda changes: self._on_source_changes(changes, list(imports), label)))
        thread.failed.connect(on_failure or self._on_source_failure)
        thread.finished.connect(self._on_source_finished)
        self.centralWidget().setEnabled(False)
        self.status_task.setText(label + "…")
        self.log(label + "…")
        thread.start()
        return True

    def _rename_source(self, path, name):
        def worker():
            renamed = rename_source(path, name)
            return [(path, renamed)] if renamed != path else []
        self._start_source_operation(worker, "重命名")

    def _reveal_source(self, path):
        if not path or not os.path.lexists(path):
            self._flash_status("源文件不存在，无法打开所在文件夹。", 6000)
            self.log(f"无法定位源文件：{path or '没有源文件'}")
            return False
        path = os.path.abspath(path)
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            opened = QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
            if not opened:
                raise OSError("无法启动文件管理器")
        except (OSError, RuntimeError) as exc:
            self.log(f"打开文件所在文件夹失败：{exc}")
            self._flash_status("无法打开文件所在文件夹，请查看日志。", 6000)
            return False
        return True

    def _move_sources(self, paths, destination):
        paths = list(dict.fromkeys(os.path.abspath(p) for p in paths))
        imports = self._external_sources(paths)
        def completed(changes):
            self.scene_panel.clear_placements([old for old, _new in changes])
            self._on_source_changes(changes, imports, "移动文件")
        return self._start_source_operation(lambda: move_sources(paths, destination), "移动文件", imports,
                                            on_success=completed)

    def _external_sources(self, paths):
        known = {e.path for e in self.entities if e.path}
        known.update(self.scene_panel.tree_state().get("directories", []))
        return [p for p in paths if os.path.abspath(p) not in known]

    def _choose_move_mode(self, paths, destination):
        box = QMessageBox(self)
        box.setWindowTitle("移动到文件夹")
        box.setText(f"将 {len(paths)} 项移到「{os.path.basename(destination)}」？")
        box.setInformativeText("仅调整工作区分组会保留原文件路径；同时移动硬盘原文件会改变实际位置。同名文件不会被覆盖。")
        box.setDetailedText("目标分组：" + destination + "\n\n" + "\n".join(paths))
        scene = box.addButton("仅调整工作区中的分组", QMessageBox.AcceptRole)
        disk = box.addButton("同时移动硬盘原文件", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(scene)
        box.exec()
        return "scene" if box.clickedButton() is scene else "disk" if box.clickedButton() is disk else None

    def _request_move_sources(self, paths, destination):
        if any(is_data_source(path) for path in paths):
            self.open_paths(paths)
            return
        if self._source_operation is not None or self._scan_threads:
            self._flash_status("正在扫描或操作文件，请稍后再移动。", 6000)
            return
        mode = self._choose_move_mode(paths, destination)
        if mode == "disk":
            self._move_sources(paths, self.scene_panel.source_path(destination))
        elif mode == "scene":
            external = self._external_sources(paths)
            if external:
                self.open_paths(external)
            if self._scan_threads:
                self._pending_regroups.append((paths, destination))
            else:
                self._regroup_sources(paths, destination)

    def _regroup_sources(self, paths, destination):
        before = self._scene_snapshot()
        try:
            self.scene_panel.regroup_paths(paths, destination)
        except (ValueError, OSError) as exc:
            self.log("调整分组失败：" + str(exc))
            self._flash_status("分组未移动：" + str(exc), 6000)
            return
        after = self._scene_snapshot()
        if before.tree == after.tree:
            return
        self.history.push("调整工作区分组", lambda: self._apply_group_history(before, after, restore=True),
                          lambda: self._apply_group_history(before, after, restore=False))
        self._update_history_buttons()
        self._flash_status("已调整工作区分组，硬盘原路径未改变。", 5000)

    def _apply_group_history(self, before, after, *, restore):
        tree = self.scene_panel.tree_state()
        previous, target = (after.tree, before.tree) if restore else (before.tree, after.tree)
        for key in ("roots", "directories", "expanded"):
            removed = set(previous.get(key, [])) - set(target.get(key, []))
            added = set(target.get(key, [])) - set(previous.get(key, []))
            tree[key] = list(dict.fromkeys([p for p in tree.get(key, []) if p not in removed] + sorted(added)))
        placements = tree.setdefault("placements", {})
        for path in previous.get("placements", {}).keys() | target.get("placements", {}).keys():
            if previous.get("placements", {}).get(path) == target.get("placements", {}).get(path):
                continue
            if path in target.get("placements", {}):
                placements[path] = target["placements"][path]
            else:
                placements.pop(path, None)
        self.scene_panel.restore_tree_state(tree)

    def _apply_source_changes(self, changes):
        def mapped(path):
            for old, new in changes:
                path = relocate_path(path, old, new)
            return path
        for entity in self._known_source_entities:
            if entity.path:
                old = entity.path
                entity.path = mapped(old)
                if os.path.basename(entity.path) != os.path.basename(old):
                    entity.name = os.path.basename(entity.path)
        for snapshot in self._scene_snapshots:
            for old, new in changes:
                placements = snapshot.tree.get("placements", {})
                ancestors = [p for p in placements if old == p or old.startswith(p.rstrip(os.sep) + os.sep)]
                parent = max(ancestors, key=len) if ancestors else None
                logical_old = relocate_path(old, parent, placements[parent]) if parent else old
                logical_new = (os.path.join(os.path.dirname(logical_old), os.path.basename(new))
                               if logical_old != old else new)
                def logical_mapped(path):
                    return relocate_path(relocate_path(path, logical_old, logical_new), old, new)
                for key in ("roots", "directories"):
                    snapshot.tree[key] = [relocate_path(p, old, new) for p in snapshot.tree.get(key, [])]
                snapshot.tree["expanded"] = [logical_mapped(p) for p in snapshot.tree.get("expanded", [])]
                snapshot.tree["placements"] = {relocate_path(p, old, new): logical_mapped(group)
                                               for p, group in placements.items()}
        for record in self._trash_records:
            record.sources[:] = [mapped(path) for path in record.sources]
            record.moves[:] = [(mapped(original), trash) for original, trash in record.moves]
        self.settings.recent_files = list(dict.fromkeys(mapped(p) for p in self.settings.recent_files))
        self.settings.last_dir = mapped(self.settings.last_dir)
        self._load_queue = [mapped(p) for p in self._load_queue]
        self._pending_display = {mapped(p): state for p, state in self._pending_display.items()}
        self._deferred_imports = [mapped(p) for p in self._deferred_imports]
        self._restoring_project = True
        try:
            for old, new in changes:
                self.scene_panel.remap_paths(old, new)
        finally:
            self._restoring_project = False
        self._rebuild_recent_menu()
        save_settings(self.settings)
        self._show_entity(self.scene_panel.current_entity())
        self._refresh_scene()

    def _on_source_changes(self, changes, imports, label):
        self._apply_source_changes(changes)
        for path in imports:
            for old, new in changes:
                path = relocate_path(path, old, new)
            self._deferred_imports.append(path)
        self.log(f"{label}完成（{len(changes)} 项）；如需保留工作区，请重新保存。")
        self._flash_status(label + "完成", 4000)

    def _on_source_failure(self, error):
        remaining = getattr(error, "remaining_moves", [])
        if remaining:
            self._apply_source_changes(remaining)
            self._deferred_imports.extend(new for _old, new in remaining)
        message = str(error)
        if isinstance(error, FileExistsError):
            message = "目标存在同名文件或文件夹，未覆盖原文件：" + message
        self.log("路径操作失败：" + message)
        self._flash_status("操作失败，请查看日志：" + message, 8000)

    def _on_source_finished(self):
        thread = self._source_operation
        self._source_operation = None
        if thread is not None:
            thread.deleteLater()
        self.status_task.clear()
        self.centralWidget().setEnabled(True)
        imports, self._deferred_imports = self._deferred_imports, []
        if not self._closing:
            if imports:
                self.open_paths(imports)
            self._schedule_frame_loads()

    def _reload_entity(self, entity: CloudEntity):
        """按源文件重新读取并原地替换数据（后台线程，保留显示状态）。"""
        if self._source_operation is not None:
            self._flash_status("文件移动中，请稍后再重新加载。", 6000)
            return
        if not entity.path or not os.path.exists(entity.path):
            self.log("无法重新加载：该点云没有源文件")
            self._flash_status("无法重新加载：没有源文件")
            return
        if entity.xyz is None:
            entity.load_state = "unloaded"
            self.scene_panel.select_entity(entity)
            self._schedule_frame_loads()
            return
        if self._filter_thread is not None:
            self.log("已有处理任务在进行，请稍候")
            return

        def worker(progress, cancel):
            return read_cloud(entity.path, self.options,
                              progress=lambda f: progress(f), cancel=cancel)

        thread = FilterThread(worker, self)
        thread.done.connect(lambda fresh: self._apply_reload(entity, fresh))
        thread.failed.connect(self._on_filter_failed)
        thread.cancelled.connect(lambda: self.log("重新加载已取消"))
        thread.finished.connect(self._on_filter_finished)
        self._filter_thread = thread
        self.log(f"重新加载: {entity.name}…")
        thread.start()

    def _apply_reload(self, entity: CloudEntity, fresh: CloudEntity):
        if self._closing or entity not in self.entities:
            return
        attributes = ("xyz", "fields", "info", "debug", "loaded", "dim_hint", "tier", "load_state")
        before = {name: getattr(entity, name) for name in attributes}
        after = {name: getattr(fresh, name) for name in attributes}
        after["tier"] = tier_by_points(fresh.point_count)

        def apply(state):
            for name, value in state.items():
                setattr(entity, name, value)
            if self.scene_panel.current_entity() is entity:
                self._show_entity(entity)
            self._refresh_scene()

        apply(after)
        self.history.push(f"重新加载 {entity.name}", lambda: apply(before), lambda: apply(after))
        self._update_history_buttons()
        self.log(f"已重新加载: {entity.name}（{entity.point_count:,} 点）")

    def _next_entity(self):
        if not self.entities:
            return
        cur = self.scene_panel.current_entity()
        idx = self.entities.index(cur) if cur in self.entities else -1
        nxt = self.entities[min(idx + 1, len(self.entities) - 1)]
        self.scene_panel.select_entity(nxt)

    def _prev_entity(self):
        if not self.entities:
            return
        cur = self.scene_panel.current_entity()
        idx = self.entities.index(cur) if cur in self.entities else 0
        prv = self.entities[max(0, idx - 1)]
        self.scene_panel.select_entity(prv)

    def _cycle_color(self):
        if self.content_stack.currentWidget() is self.recording_browser:
            index = COLOR_MODES.index(self.recording_browser.color_mode)
            self.cmb_color.setCurrentIndex((index + 1) % len(COLOR_MODES))
            return
        targets = self.entities if not self.scene_panel.current_entity() else [self.scene_panel.current_entity()]
        for e in targets:
            i = COLOR_MODES.index(e.color_mode) if e.color_mode in COLOR_MODES else 0
            e.color_mode = COLOR_MODES[(i + 1) % len(COLOR_MODES)]
        self._show_entity(self.scene_panel.current_entity())
        if targets:
            self.log(f"颜色模式: {targets[0].color_mode}")
        self._refresh_scene()

    def _adjust_point_size(self, delta: float):
        for e in self.entities:
            e.point_size = min(max(e.point_size + delta, 0.5), 10.0)
        self._show_entity(self.scene_panel.current_entity())
        if self.entities:
            self.lbl_size.setText(f"{self.entities[0].point_size:.1f}")
            self.log(f"点大小: {self.entities[0].point_size:.1f}")
        self.viewport.set_point_size()

    # ================= 裁剪范围 =================
    def _toggle_clipbox(self):
        if self.clip_popup.is_enabled():
            self._clip_clear()
        else:
            self._clip_fill_from_scene()
        self._clip_commit_timer.stop()
        self._commit_clip_history()

    def _on_clip_enabled(self, checked: bool):
        self.btn_clip.blockSignals(True)
        self.btn_clip.setChecked(bool(checked))
        self.btn_clip.blockSignals(False)

        if not checked:
            self.viewport.clear_clip_box()
            self.log("裁剪范围已关闭")
            return

        mn, mx = self.clip_popup.values()
        if all(abs(v) < 1e-9 for v in list(mn) + list(mx)):
            self._clip_fill_from_scene()
        else:
            self.viewport.set_clip_box(mn, mx)

    def _clip_fill_from_scene(self):
        bounds = [e.bounds() for e in self.entities if e.visible and e.xyz is not None and len(e.xyz)]
        if not bounds:
            self.log("场景中没有可见点云，无法按包围盒填充")
            self.clip_popup.set_enabled(False)
            self.btn_clip.setChecked(False)
            self._flash_status("请先打开并显示一片点云")
            return
        mn = np.min([b[0] for b in bounds], axis=0)
        mx = np.max([b[1] for b in bounds], axis=0)
        padding = np.maximum(np.maximum(np.abs(mn), np.abs(mx)) * 1e-6, 0.001)
        flat = mx - mn < padding
        mn, mx = np.where(flat, mn - padding, mn), np.where(flat, mx + padding, mx)
        self.clip_popup.set_values(mn, mx)
        if not self.clip_popup.is_enabled():
            self.clip_popup.set_enabled(True)
            self.btn_clip.blockSignals(True)
            self.btn_clip.setChecked(True)
            self.btn_clip.blockSignals(False)
        self.viewport.set_clip_box(mn, mx)
        self.log("裁剪范围已按包围盒填充")
        self._schedule_clip_commit()

    def _clip_clear(self):
        self.clip_popup.set_enabled(False)
        self.btn_clip.blockSignals(True)
        self.btn_clip.setChecked(False)
        self.btn_clip.blockSignals(False)
        self.viewport.clear_clip_box()
        self.log("裁剪范围已关闭")
        self._schedule_clip_commit()

    # ================= 处理命令（P2：滤波 + 撤销/重做） =================
    def _current_entity(self):
        if self.content_stack.currentWidget() is self.recording_browser:
            self._flash_status("请点击“当前点云加入场景”后使用点云处理工具。")
            return None
        entity = self.scene_panel.current_entity()
        if entity is None:
            self.log("请先在左侧场景中选择一片点云")
            self._flash_status("请先在左侧选择一片点云")
        elif entity.load_state != "ready" or entity.xyz is None:
            self._schedule_frame_loads()
            self._flash_status("当前帧尚未加载完成；失败时可右键重新加载", 4000)
            return None
        return entity

    def _load_reference(self, entity):
        if entity.load_state == "ready" and entity.xyz is not None:
            return True
        if entity.load_state == "error":
            entity.load_state = "unloaded"
        progress = SmoothProgressDialog(f"正在加载参考点云：{entity.name}", "取消", 0, 0, self)
        progress.setWindowTitle("加载参考点云")
        progress.setMinimumDuration(0)
        timer = QTimer(progress)
        timer.setInterval(50)

        def check():
            if entity.load_state == "ready":
                progress.accept()
            elif entity.load_state == "error" or self._closing:
                progress.reject()

        self._explicit_loads.add(entity)
        self._schedule_frame_loads()
        timer.timeout.connect(check)
        timer.start()
        try:
            return progress.exec() == QDialog.Accepted and entity.xyz is not None
        finally:
            timer.stop()
            self._explicit_loads.discard(entity)
            self._schedule_frame_loads()

    def _make_result(self, source: CloudEntity, title: str, xyz, fields, info: str) -> CloudEntity:
        entity = CloudEntity(name=f"{source.name} · {title}", path=None, xyz=xyz, fields=fields)
        entity.tier = tier_by_points(len(xyz))
        entity.color_mode = source.color_mode
        entity.color_field = source.color_field
        entity.color_range = source.color_range
        entity.colormap = source.colormap
        entity.point_size = source.point_size
        if source.is_preview:
            # 派生结果来自采样预览：必须继续标记为预览，避免用户误以为是全量结果
            entity.loaded = source.loaded
            entity.tier = source.tier
            info = f"{info}；基于 {int(source.loaded * 100)}% 预览".strip("；")
        entity.info = info
        self._result_sources[entity] = source
        return entity

    def _command_worker(self, source: CloudEntity, command: Command, title,
                        min_points: int = 0, min_message: str = ""):
        """后台执行内置命令，并统一整理结果实体的名称、分级与来源关联。"""
        def worker(progress, cancel):
            entity = run_command(source, command, progress, cancel)
            if min_points and entity.point_count < min_points:
                raise ValueError(min_message.format(n=entity.point_count)
                                 if min_message else f"仅保留 {entity.point_count} 点，请放宽参数")
            name = title(entity) if callable(title) else title
            entity.name = f"{source.name} · {name}"
            entity.tier = source.tier if source.is_preview else tier_by_points(entity.point_count)
            self._result_sources[entity] = source
            return entity
        return worker

    def _run_filter(self, title: str, worker, command: Command | None = None):
        if self._source_operation is not None:
            self._flash_status("文件移动中，请稍后再处理。", 6000)
            return
        if self._filter_thread is not None:
            self.log("已有处理任务在进行，请稍候")
            return
        thread = FilterThread(worker, self)
        self.status_task.setText(f"{title}…")
        self.btn_cancel_task.setEnabled(True)
        self.btn_cancel_task.show()
        thread.progress.connect(lambda f: self.status_task.setText(f"{title} {f:.0%}"))
        thread.done.connect(lambda e: self._on_filter_done(title, e, command))
        thread.failed.connect(self._on_filter_failed)
        thread.finished.connect(self._on_filter_finished)
        self._filter_thread = thread
        self.log(f"开始{title}…")
        thread.start()

    def _on_filter_done(self, title: str, entity, command: Command | None = None):
        if self._closing:
            return
        if isinstance(entity, MeshEntity):
            visibility = [(e, e.visible) for e in self.entities]
            previous_mesh = self._auto_mesh

            def show_result():
                for cloud in self.entities:
                    if not cloud.visibility_pinned:
                        cloud.visible = False
                if previous_mesh is not None:
                    previous_mesh.visible = False
                self._auto_mesh = entity
                entity.visible = True
                self._add_mesh(entity)
                self._refresh_scene()

            def undo_result():
                self._remove_mesh(entity)
                self._auto_mesh = previous_mesh
                if previous_mesh is not None:
                    previous_mesh.visible = True
                    self.viewport._refresh_meshes()
                for cloud, visible in visibility:
                    if cloud in self.entities:
                        cloud.visible = visible
                self._refresh_scene()

            show_result()
            self.history.push(f"添加 {entity.name}", undo_result, show_result)
            self._update_history_buttons()
            self.log(f"{title}完成: {entity.name}（{entity.vertex_count:,} 顶点 / {entity.triangle_count:,} 三角面）已加入场景")
            if entity.info:
                self.log(f"  {entity.info}")
            self._update_status()
            return
        self._add_entity(entity)
        self._push_add_history(entity, command)
        self.log(f"{title}完成: {entity.name}（{entity.point_count:,} 点）")
        if entity.info:
            self.log(f"  {entity.info}")
        self._update_status()

    def _on_filter_failed(self, error: str):
        self.log(f"处理失败: {error}")
        self._flash_status(f"处理失败：{error}", 6000)
        self._update_status()

    def _on_filter_finished(self):
        if self._filter_thread is not None:
            self._filter_thread.deleteLater()
        self._filter_thread = None
        self._evict_frame_cache()
        self.btn_cancel_task.hide()
        self.status_task.clear()
        self._update_status()

    def _cancel_filter(self):
        if self._filter_thread is not None:
            self._filter_thread.requestInterruption()
            self.btn_cancel_task.setEnabled(False)
            self.status_task.setText("正在取消，请稍候…")

    # ---------------- 撤销 / 重做 ----------------
    def _push_add_history(self, entity: CloudEntity, command: Command | None = None):
        def undo():
            self._remove_entity_now(entity)
            source = self._result_sources.get(entity)
            if source in self.entities:
                self.scene_panel.select_entity(source)

        def redo():
            self._add_entity(entity)

        self.history.push(f"添加 {entity.name}", undo, redo, command)
        self._update_history_buttons()

    def _update_history_buttons(self):
        can_undo = self.history.can_undo
        can_redo = self.history.can_redo
        self.btn_undo.setEnabled(can_undo)
        self.btn_redo.setEnabled(can_redo)
        self.btn_undo.setToolTip("撤销 (Ctrl+Z)" if can_undo else "撤销 (Ctrl+Z) — 暂无可撤销操作")
        self.btn_redo.setToolTip("重做 (Ctrl+Shift+Z)" if can_redo else "重做 (Ctrl+Shift+Z) — 暂无可重做操作")

    def _undo(self):
        if self._source_operation is not None:
            self._flash_status("正在操作原文件，请完成后再撤销。", 5000)
            return
        if self._clip_commit_timer.isActive():
            self._clip_commit_timer.stop()
            self._commit_clip_history()
        label = self.history.undo()
        if label is None:
            self.log("没有可撤销的操作")
            self._flash_status("没有可撤销的操作")
            return
        self.log(f"撤销: {label}")
        self._flash_status(f"已撤销：{label}")
        self._update_history_buttons()

    def _redo(self):
        if self._source_operation is not None:
            self._flash_status("正在操作原文件，请完成后再重做。", 5000)
            return
        label = self.history.redo()
        if label is None:
            self.log("没有可重做的操作")
            self._flash_status("没有可重做的操作")
            return
        self.log(f"重做: {label}")
        self._flash_status(f"已重做：{label}")
        self._update_history_buttons()

    # ---------------- 状态栏临时提示 ----------------
    def _mode_status_text(self) -> str:
        mode = getattr(self.viewport, "_mode", None)
        return {"measure": "● 测量中：连续点击加点，显示长度与面积（Esc 退出）",
                "select": "● 选点中：单击查看坐标与属性（Esc 退出）",
                "box": "● 框选中：拖拽矩形选择点（Esc 退出）"}.get(mode, "")

    def _flash_status(self, text: str, msec: int = 2600):
        self.status_mode.setText(text)
        QTimer.singleShot(msec, self, lambda: self.status_mode.setText(self._mode_status_text()))

    def _remove_entity_now(self, entity: CloudEntity):
        thread = self._threads.get(entity.path)
        if thread is not None:
            thread.requestInterruption()
        if entity in self.entities:
            self.entities.remove(entity)
        self._frame_cache.pop(entity, None)
        self.scene_panel.remove_entity(entity)
        self._show_entity(self.scene_panel.current_entity())
        self._refresh_scene()

    # ---------------- 具体处理命令 ----------------
    def _process_voxel(self):
        source = self._current_entity()
        if source is None:
            return
        default = max(source.extent(), 1e-6) / 300.0
        dlg = ParamDialog(self, "体素降采样",
                          [{"key": "voxel", "label": "体素大小 (m)", "default": default,
                            "min": 1e-4, "max": 1e5, "decimals": 4}],
                          hint="按空间体素取质心，合并邻近点；适合快速降采样与配准前处理。")
        if dlg.exec() != QDialog.Accepted:
            return
        voxel = float(dlg.values()["voxel"])
        command = Command("voxel", {"voxel": voxel})
        self._run_filter("体素降采样",
                         self._command_worker(source, command, f"体素{voxel:g}m"), command)

    def _process_random(self):
        source = self._current_entity()
        if source is None:
            return
        default = min(500_000, max(100, source.point_count // 2))
        dlg = ParamDialog(self, "随机采样",
                          [{"key": "count", "label": "目标点数", "default": default,
                            "kind": "int", "min": 100, "max": 100_000_000}],
                          hint="均匀随机采样到指定点数（覆盖整体形状）。")
        if dlg.exec() != QDialog.Accepted:
            return
        count = int(dlg.values()["count"])
        command = Command("random", {"count": count})
        worker = self._command_worker(source, command, f"随机{count:,}")

        def guarded(progress, cancel):
            if count >= source.point_count:
                raise ValueError(f"目标点数（{count:,}）不少于源点数")
            return worker(progress, cancel)

        self._run_filter("随机采样", guarded, command)

    def _process_sor(self):
        source = self._current_entity()
        if source is None:
            return
        dlg = ParamDialog(self, "统计滤波 (SOR)",
                          [{"key": "k", "label": "邻域点数 k", "default": 20,
                            "kind": "int", "min": 3, "max": 200},
                           {"key": "std", "label": "标准差倍数", "default": 2.0,
                            "min": 0.1, "max": 10.0, "decimals": 2}],
                          hint="统计每个点到 k 近邻的平均距离，剔除明显离群的点。")
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.values()
        k = int(params["k"])
        std = float(params["std"])
        command = Command("sor", {"k": k, "std": std})
        self._run_filter("统计滤波",
                         self._command_worker(source, command, f"统计滤波k{k}",
                                              min_points=10,
                                              min_message="仅保留 {n} 点，请放宽参数"),
                         command)

    def _process_ror(self):
        source = self._current_entity()
        if source is None:
            return
        default_r = max(source.extent(), 1e-6) / 500.0
        dlg = ParamDialog(self, "半径滤波 (ROR)",
                          [{"key": "radius", "label": "半径 (m)", "default": default_r,
                            "min": 1e-4, "max": 1e5, "decimals": 3},
                           {"key": "min_pts", "label": "最少邻居数", "default": 5,
                            "kind": "int", "min": 1, "max": 200}],
                          hint="剔除半径内邻居数不足的点（去孤立噪点）。")
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.values()
        radius = float(params["radius"])
        min_pts = int(params["min_pts"])
        command = Command("ror", {"radius": radius, "min_pts": min_pts})
        self._run_filter("半径滤波",
                         self._command_worker(source, command, f"半径滤波r{radius:g}",
                                              min_points=10,
                                              min_message="仅保留 {n} 点，请放宽参数"),
                         command)

    def _process_crop(self):
        source = self._current_entity()
        if source is None:
            return
        mn, mx = source.bounds()
        if self.viewport._clip_box is not None:
            mn, mx = self.viewport._clip_box
        fields = [
            {"key": "xmin", "label": "X min", "default": float(mn[0]), "decimals": 2},
            {"key": "xmax", "label": "X max", "default": float(mx[0]), "decimals": 2},
            {"key": "ymin", "label": "Y min", "default": float(mn[1]), "decimals": 2},
            {"key": "ymax", "label": "Y max", "default": float(mx[1]), "decimals": 2},
            {"key": "zmin", "label": "Z min", "default": float(mn[2]), "decimals": 2},
            {"key": "zmax", "label": "Z max", "default": float(mx[2]), "decimals": 2},
        ]
        dlg = ParamDialog(self, "盒裁切", fields, hint="保留盒内的点，生成新点云。")
        if dlg.exec() != QDialog.Accepted:
            return
        v = dlg.values()
        mn2 = [v["xmin"], v["ymin"], v["zmin"]]
        mx2 = [v["xmax"], v["ymax"], v["zmax"]]
        command = Command("crop", {"xmin": mn2[0], "ymin": mn2[1], "zmin": mn2[2],
                                   "xmax": mx2[0], "ymax": mx2[1], "zmax": mx2[2]})
        self._run_filter("盒裁切",
                         self._command_worker(source, command, "盒裁切",
                                              min_points=10,
                                              min_message="盒内点数过少，请调整范围"),
                         command)

    # ---------------- P3：分割与配准 ----------------
    def _transformed_fields(self, source, transform):
        fields = source.subset_fields(slice(None))
        if "normal" in fields:
            normals = np.asarray(fields["normal"].data, dtype=np.float64)
            normals = normals @ np.linalg.inv(np.asarray(transform)[:3, :3])
            normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
            fields["normal"] = ScalarField("normal", normals.astype(np.float32), kind="normal")
        return fields

    def _process_plane(self):
        source = self._current_entity()
        if source is None:
            return
        default_dist = max(source.extent() / 300.0, 0.02)
        dlg = ParamDialog(self, "平面分割",
                          [{"key": "dist", "label": "距离阈值 (m)", "default": default_dist,
                            "min": 1e-4, "max": 1e5, "decimals": 3},
                           {"key": "iters", "label": "RANSAC 迭代", "default": 300,
                            "kind": "int", "min": 20, "max": 3000}],
                          hint="RANSAC 拟合最大的平面，输出平面内点云；平面方程会写入日志。")
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.values()
        dist, iters = float(params["dist"]), int(params["iters"])
        command = Command("plane", {"dist": dist, "iters": iters})
        self._run_filter("平面分割",
                         self._command_worker(source, command,
                                              lambda e: f"平面{e.point_count:,}",
                                              min_points=10,
                                              min_message="平面内点过少，请增大距离阈值"),
                         command)

    def _process_ground(self):
        source = self._current_entity()
        if source is None:
            return
        default_dist = max(source.extent() / 400.0, 0.05)
        dlg = ParamDialog(self, "地面提取",
                          [{"key": "dist", "label": "距离阈值 (m)", "default": default_dist,
                            "min": 1e-4, "max": 1e5, "decimals": 3},
                           {"key": "iters", "label": "RANSAC 迭代", "default": 300,
                            "kind": "int", "min": 20, "max": 3000}],
                          hint="拟合接近水平的最大平面作为地面，提取地面点云。")
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.values()
        dist, iters = float(params["dist"]), int(params["iters"])
        command = Command("ground", {"dist": dist, "iters": iters})
        self._run_filter("地面提取",
                         self._command_worker(source, command, "地面",
                                              min_points=10,
                                              min_message="地面点过少，请增大距离阈值"),
                         command)

    def _process_dbscan(self):
        source = self._current_entity()
        if source is None:
            return
        if self._eps_thread is not None:
            self.log("正在估算邻域半径，请稍候")
            return
        min_pts_default = 10
        key = f"{id(source.xyz)}:{source.point_count}:{min_pts_default}"
        cached = self._eps_cache.get(key)
        if cached is not None:
            self._open_dbscan_dialog(source, cached)
            return
        if source.point_count <= 200_000:
            eps = segmentation.suggest_eps(source.xyz, min_samples=min_pts_default)
            self._eps_cache[key] = eps
            self._open_dbscan_dialog(source, eps)
            return

        progress = SmoothProgressDialog("正在估算邻域尺度（后台）…", "取消", 0, 0, self)
        progress.setWindowTitle("DBSCAN 聚类")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        thread = EpsThread(source.xyz, min_pts_default, self)
        self._eps_thread = thread
        outcome = {"cancelled": False, "eps": None, "error": None}

        def on_finished():
            progress.hide()
            progress.deleteLater()
            thread.deleteLater()
            if self._eps_thread is thread:
                self._eps_thread = None
            if self._closing or outcome["cancelled"] or source not in self.entities:
                return
            if outcome["error"]:
                self.log(f"邻域半径估算失败: {outcome['error']}")
            eps = outcome["eps"] if outcome["eps"] is not None else 0.5
            self._eps_cache[key] = eps
            self._open_dbscan_dialog(source, eps)

        def on_cancel():
            outcome["cancelled"] = True
            thread.requestInterruption()
            self.log("已取消邻域半径估算")

        thread.done.connect(lambda eps: outcome.update(eps=eps))
        thread.failed.connect(lambda error: outcome.update(error=error))
        thread.finished.connect(on_finished)
        progress.canceled.connect(on_cancel)
        self.log("正在估算 DBSCAN 邻域半径…")
        thread.start()
        progress.show()

    def _open_dbscan_dialog(self, source: CloudEntity, eps_default: float):
        min_pts_default = 10
        dlg = ParamDialog(self, "DBSCAN 聚类",
                          [{"key": "eps", "label": "邻域半径 (m)", "default": round(eps_default, 3),
                            "min": 1e-4, "max": 1e5, "decimals": 3},
                           {"key": "min_pts", "label": "最少点数", "default": min_pts_default,
                            "kind": "int", "min": 2, "max": 1000}],
                          hint="按空间密度聚类；半径已按点间距自动估计，可手动调整。")
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.values()
        eps, min_pts = float(params["eps"]), int(params["min_pts"])
        command = Command("dbscan", {"eps": eps, "min_pts": min_pts})

        def title(entity):
            labels = entity.field("labels")
            clusters = 0 if labels is None else int(labels.max()) + 1
            return f"聚类{clusters}"

        self._run_filter("DBSCAN 聚类",
                         self._command_worker(source, command, title), command)

    def _process_icp(self):
        self._run_icp_dialog(plane=False)

    def _process_icp_plane(self):
        self._run_icp_dialog(plane=True)

    def _run_icp_dialog(self, plane: bool):
        source = self._current_entity()
        if source is None:
            return
        others = [e for e in self.entities if e is not source]
        if not others:
            self.log("ICP 配准需要至少两片点云：请先加载目标点云")
            return
        options = [(f"{i + 1}. {e.name}（{e.point_count:,} 点）", i) for i, e in enumerate(others)]
        title = "ICP 精配准（点到面）" if plane else "ICP 配准（点到点）"
        dlg = ParamDialog(
            self, title,
            [{"key": "target", "label": "目标点云", "kind": "choice", "options": options},
             {"key": "iters", "label": "最大迭代", "default": 60 if plane else 30,
              "kind": "int", "min": 5, "max": 500}],
            hint=("点到面 ICP：沿目标法线最小化距离，平面/墙面场景收敛更快。"
                  if plane else
                  "把当前选中的点云（源）对齐到目标点云，生成配准后的新点云。"))
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.values()
        target = others[int(params["target"])]
        iters = int(params["iters"])
        if not self._load_reference(target):
            return

        def worker(progress, cancel):
            normals = None
            field = target.field("normal")
            if field is not None and np.asarray(field.data).shape == (target.point_count, 3):
                normals = np.asarray(field.data, dtype=np.float64)
            if plane:
                transform, rmse = registration.icp_point_to_plane(
                    source.xyz, target.xyz, target_normals=normals,
                    max_iterations=iters, progress=progress, cancel=cancel)
            else:
                transform, rmse = registration.icp(
                    source.xyz, target.xyz, max_iterations=iters,
                    progress=progress, cancel=cancel)
            xyz = registration.apply_transform(source.xyz, transform)
            kind = "ICP精配准" if plane else "ICP配准"
            return self._make_result(
                source, kind, xyz, self._transformed_fields(source, transform),
                f"{kind} → {target.name}｜RMSE={rmse:.6f}" + chr(10)
                + np.array2string(transform, precision=4, suppress_small=True))

        self._run_filter(title, worker)

    def _process_global_registration(self):
        source = self._current_entity()
        if source is None:
            return
        others = [e for e in self.entities if e is not source]
        if not others:
            self.log("全局配准需要至少两片点云：请先加载目标点云")
            return
        options = [(f"{i + 1}. {e.name}（{e.point_count:,} 点）", i) for i, e in enumerate(others)]
        diag = max(source.extent(), 1e-3)
        dlg = ParamDialog(
            self, "全局配准（FPFH + RANSAC）",
            [{"key": "target", "label": "目标点云", "kind": "choice", "options": options},
             {"key": "voxel", "label": "降采样体素 (m)", "default": max(diag / 200.0, 1e-4),
              "min": 1e-5, "max": 1e4, "decimals": 5},
             {"key": "iters", "label": "精配准迭代", "default": 60,
              "kind": "int", "min": 0, "max": 500}],
            hint="无初始位姿时的粗配准；完成后自动用点到面 ICP 精修。点云较大时较慢。")
        if dlg.exec() != QDialog.Accepted:
            return
        params = dlg.values()
        target = others[int(params["target"])]
        voxel = float(params["voxel"])
        iters = int(params["iters"])
        if not self._load_reference(target):
            return

        def worker(progress, cancel):
            transform, fitness, fit_rmse = registration.global_registration(
                source.xyz, target.xyz, voxel_size=voxel, progress=progress, cancel=cancel)
            if fitness <= 0 or not np.isfinite(fit_rmse):
                raise ValueError("未找到可靠匹配，请检查重叠区域或调整体素大小")
            note = (f"FPFH+RANSAC → {target.name}｜fitness={fitness:.3f}｜"
                    f"inlier RMSE={fit_rmse:.4f}")
            if iters > 0:
                refined, rmse = registration.icp_point_to_plane(
                    source.xyz, target.xyz, max_iterations=iters,
                    max_corr_distance=voxel * 1.5, initial_transform=transform,
                    progress=progress, cancel=cancel)
                if np.isfinite(rmse):
                    transform = refined
                    note += f"｜精修点面 RMSE={rmse:.6f}"
            xyz = registration.apply_transform(source.xyz, transform)
            return self._make_result(
                source, "全局配准", xyz, self._transformed_fields(source, transform),
                note + chr(10) + np.array2string(transform, precision=4, suppress_small=True))

        self._run_filter("全局配准", worker)

    def _open_transform_dialog(self):
        source = self._current_entity()
        if source is None:
            return
        dlg = TransformDialog(self, title=f"变换矩阵 · {source.name}")
        if dlg.exec() != QDialog.Accepted:
            return
        matrix = dlg.matrix()
        try:
            fields = self._transformed_fields(source, matrix)
        except np.linalg.LinAlgError:
            self._flash_status("变换矩阵不可逆，无法变换法线", 5000)
            return
        xyz = registration.apply_transform(source.xyz, matrix)
        entity = self._make_result(
            source, "变换", xyz, fields,
            "应用 4×4 变换" + chr(10)
            + np.array2string(matrix, precision=5, suppress_small=True))
        self._add_entity(entity)
        self._push_add_history(entity)
        self.log(f"已应用变换：{source.name} → {entity.name}")

    # ---------------- E25：法线/距离/网格 ----------------
    def _process_normals(self):
        source = self._current_entity()
        if source is None:
            return
        dlg = ParamDialog(self, "估计法线", [
            {"key": "k", "label": "近邻数 k", "kind": "int", "default": 20, "min": 3, "max": 200},
        ], hint="k 近邻 PCA 法线，按背离质心统一朝向；结果写入 normal 字段。")
        if dlg.exec() != QDialog.Accepted:
            return
        k = int(dlg.values()["k"])

        def worker(progress, cancel):
            normals = mesh_ops.estimate_normals(source.xyz, k=k, progress=progress, cancel=cancel)
            fields = source.subset_fields(slice(None))
            fields["normal"] = ScalarField("normal", normals, kind="normal")
            return self._make_result(source, "法线", source.xyz.copy(), fields,
                                     f"法线 k={k}｜{len(normals):,} 点")

        self._run_filter("估计法线", worker)

    def _process_c2c(self):
        source = self._current_entity()
        if source is None:
            return
        others = [e for e in self.entities if e is not source]
        if not others:
            self.log("C2C 需要至少两片点云：请先加载参考点云")
            return
        options = [(f"{i + 1}. {e.name}（{e.point_count:,} 点）", i) for i, e in enumerate(others)]
        dlg = ParamDialog(self, "点到点云距离 (C2C)", [
            {"key": "target", "label": "参考点云", "kind": "choice", "options": options},
        ], hint="计算每个点到参考点云的最近距离，写入 c2c 字段并按该字段着色。")
        if dlg.exec() != QDialog.Accepted:
            return
        target = others[int(dlg.values()["target"])]
        if not self._load_reference(target):
            return

        def worker(progress, cancel):
            dist = mesh_ops.c2c_distance(source.xyz, target.xyz)
            fields = source.subset_fields(slice(None))
            fields["c2c"] = ScalarField("c2c", dist, kind="scalar")
            entity = self._make_result(
                source, "C2C距离", source.xyz.copy(), fields,
                f"C2C → {target.name}｜均值 {dist.mean():.4f}｜最大 {dist.max():.4f}")
            entity.color_mode = "field"
            entity.color_field = "c2c"
            return entity

        self._run_filter("C2C 距离", worker)

    def _choose_reference_mesh(self):
        """返回 (描述, verts, tris)；优先用场景内的网格实体，否则从文件读取。"""
        options = [(f"场景网格 {i + 1}. {m.name}（{m.triangle_count:,} 面）", i)
                   for i, m in enumerate(getattr(self, "meshes", []))]
        options.append(("从文件选择…", -1))
        dlg = ParamDialog(self, "选择参考网格", [
            {"key": "target", "label": "参考网格", "kind": "choice", "options": options},
        ], hint="可直接使用场景中的重建网格，或读取磁盘上的 PLY/OBJ/STL。")
        if dlg.exec() != QDialog.Accepted:
            return None
        index = int(dlg.values()["target"])
        if index >= 0:
            mesh = self.meshes[index]
            return mesh.name, mesh.vertices, mesh.triangles
        path, _ = QFileDialog.getOpenFileName(
            self, "选择参考网格", self.settings.last_dir,
            "网格 (*.ply *.obj *.stl);;所有文件 (*)")
        if not path:
            return None
        verts, tris = mesh_ops.read_mesh(path)
        return os.path.basename(path), verts, tris

    def _process_c2m(self):
        source = self._current_entity()
        if source is None:
            return
        ref = self._choose_reference_mesh()
        if ref is None:
            return
        ref_name, ref_verts, ref_tris = ref

        def worker(progress, cancel):
            dist = mesh_ops.c2m_distance(source.xyz, ref_verts, ref_tris)
            fields = source.subset_fields(slice(None))
            fields["c2m"] = ScalarField("c2m", dist, kind="scalar")
            entity = self._make_result(
                source, "C2M距离", source.xyz.copy(), fields,
                f"C2M ← {ref_name}｜均值 {dist.mean():.4f}｜最大 {dist.max():.4f}")
            entity.color_mode = "field"
            entity.color_field = "c2m"
            return entity

        self._run_filter("C2M 距离", worker)

    def _process_mesh(self):
        source = self._current_entity()
        if source is None:
            return
        dlg = ParamDialog(self, "Poisson 网格重建", [
            {"key": "depth", "label": "八叉树深度", "kind": "int", "default": 8, "min": 5, "max": 12},
            {"key": "export", "label": "输出", "kind": "choice",
             "options": [("仅加入场景", 0), ("加入场景并导出 PLY…", 1)]},
        ], hint="从点云重建三角网格并加入场景，可直接用于渲染和 C2M 参照。点云较大时耗时较长。")
        if dlg.exec() != QDialog.Accepted:
            return
        depth = int(dlg.values()["depth"])
        out = ""
        if int(dlg.values()["export"]) == 1:
            out, _ = QFileDialog.getSaveFileName(self, "导出网格", "", "PLY 网格 (*.ply)")
            if not out:
                return
            if not out.lower().endswith(".ply"):
                out += ".ply"
        normals = source.field("normal")
        normals = np.asarray(normals) if normals is not None else None

        def worker(progress, cancel):
            verts, tris = mesh_ops.poisson_mesh(source.xyz, normals=normals, depth=depth)
            if out:
                mesh_ops.write_mesh_ply(out, verts, tris)
            return MeshEntity(
                name=f"{source.name} 网格",
                vertices=np.asarray(verts, dtype=np.float32),
                triangles=np.asarray(tris, dtype=np.int32),
                path=out or None,
                info=f"Poisson depth={depth}｜{len(verts):,} 顶点 / {len(tris):,} 面"
                     + (f"｜{out}" if out else ""))

        self._run_filter("网格重建", worker)

    def _populate_plugin_menu(self):
        loaded, errors = load_plugins()
        for message in errors:
            if hasattr(self, "log_view"):
                self.log(f"插件加载失败 · {message}")
        self._plugin_menu.clear()
        if not loaded:
            self._plugin_menu.setEnabled(False)
            return
        self._plugin_menu.setEnabled(True)
        for info in loaded:
            sub = self._plugin_menu.addMenu(f"{info.name} {info.version}")
            for op in info.commands:
                try:
                    spec = get_spec(op)
                except KeyError:
                    continue
                sub.addAction(spec.label, lambda _=False, o=op: self._run_plugin_command(o))

    def _run_plugin_command(self, op: str):
        source = self._current_entity()
        if source is None:
            return
        spec = get_spec(op)
        dlg = ParamDialog(self, spec.label, spec.dialog_fields(), hint=spec.hint or "")
        if dlg.exec() != QDialog.Accepted:
            return
        command = Command(op, dlg.values())

        def worker(progress, cancel):
            return run_command(source, command, progress, cancel)

        self._run_filter(spec.label, worker, command=command)

    # ---------------- P5：处理链与脚本 ----------------
    def _open_pipeline_dialog(self):
        source = self._current_entity()
        if source is None:
            return
        dialog = PipelineDialog(self, source)
        if dialog.exec() != QDialog.Accepted:
            return
        steps = dialog.steps()
        if not steps:
            self.log("处理链为空，请先添加步骤")
            return

        def worker(progress, cancel):
            result = run_pipeline(source, steps, progress=progress, cancel=cancel)
            result.name = f"{source.name} · 处理链"
            return result

        self._run_filter("处理链", worker)

    def _open_script_dialog(self):
        ScriptDialog(self, self).exec()

    # ================= 主题 / 状态 =================
    def _rebuild_recent_menu(self):
        menu = getattr(self, "_recent_menu", None)
        if menu is None:
            return
        menu.clear()
        entries = [p for p in self.settings.recent_files if os.path.exists(p)]
        menu.setEnabled(bool(entries))
        for path in entries[: self.settings.max_recent]:
            label = os.path.basename(path) or path
            if os.path.isdir(path):
                label += "/"
            act = menu.addAction(label)
            act.setToolTip(path)
            act.triggered.connect(lambda checked=False, p=path: self._open_recent(p))
        if entries:
            menu.addSeparator()
            menu.addAction("清除最近文件", self._clear_recent)

    def _open_recent(self, path: str):
        if not os.path.exists(path):
            self.settings.remove_recent(path)
            self._rebuild_recent_menu()
            self.log(f"最近文件已不存在: {path}")
            return
        self.open_paths([path])

    def _clear_recent(self):
        self.settings.recent_files.clear()
        self._rebuild_recent_menu()
        save_settings(self.settings)

    def _show_about(self):
        from core import __version__
        QMessageBox.about(
            self, tr("关于 PointLab Studio"),
            f"<b>PointLab Studio {__version__}</b><br>点云浏览、处理、测量与配准工作站。<br><br>"
            "渲染 pygfx/wgpu · 算法 numpy/scipy/Open3D<br>"
            f"设置文件：{self.settings_path_hint()}")

    def settings_path_hint(self) -> str:
        from core.settings import settings_path
        return str(settings_path())

    def _set_theme(self, theme: str):
        self.theme = theme
        QApplication.instance().setStyleSheet(build_stylesheet(theme))
        self.viewport.set_theme(theme)
        self.frame_browser.set_theme(theme)
        self.recording_browser.set_theme(theme)
        if self._scene_box_tool is not None:
            self._scene_box_tool.editor.set_theme(theme)
        self.scene_panel.set_theme(theme)
        self.btn_theme.setToolTip("切换主题（当前：深色）" if theme == "dark" else "切换主题（当前：浅色）")
        self._apply_icons()
        self.settings.theme = theme
        save_settings(self.settings)
        self.log(f"主题: {'浅色' if theme == 'light' else '深色'}")

    def _toggle_theme(self):
        self._set_theme("light" if self.theme == "dark" else "dark")

    def _toggle_log(self, checked: bool):
        self.log_view.setVisible(checked)

    def _refresh_scene(self):
        for entity in self.entities:
            self.scene_panel.update_entity(entity)
        self.viewport.set_entities([e for e in self.entities if e.visible and e.xyz is not None])
        if self._scene_box_tool is not None:
            self._scene_box_tool.refresh_entities()
        self._update_frame_browser()
        self._update_status()

    def _on_fps(self, fps: float):
        if self.content_stack.currentWidget() is self.recording_browser:
            return
        self.status_fps.setText(f"{fps:.0f} FPS")

    def _update_status(self):
        if self.content_stack.currentWidget() is self.recording_browser:
            browser = self.recording_browser
            total = sum(view.cloud.point_count for view in (browser.left, browser.right) if view.cloud is not None)
            images = [view.frame.value for view in (browser.left, browser.right)
                      if view.frame is not None and view.frame.kind == "image"]
            details = [f"当前 {total:,} 点"] if total else []
            details.extend(f"图像 {image.shape[1]} × {image.shape[0]}" for image in images)
            self.status_points.setText("采集浏览")
            self.status_points.setToolTip(" · ".join(details) or "在左侧场景选择数据流")
            self.status_mem.setText(f"帧缓存 {browser._cache_bytes / 1024**2:,.0f} MB")
            return
        total = sum(e.point_count for e in self.entities)
        preview = sum(1 for e in self.entities if e.xyz is not None and e.is_preview)
        mem = sum(e.xyz.nbytes for e in self.entities if e.xyz is not None)
        for e in self.entities:
            mem += sum(f.data.nbytes for f in e.fields.values())
        mem += sum(m.vertices.nbytes + m.triangles.nbytes for m in self.meshes)

        self.status_points.setText(f"已加载 {total:,} 点" + (f"（{preview} 片预览）" if preview else ""))
        self.status_points.setToolTip(self.status_points.text())
        self.status_mem.setText(f"{mem / 1024**2:,.0f} MB")
        try:
            self.overview.set_scene_stats(len(self.entities), total, mem)
        except Exception:
            pass
        self._show_clip_statistics()
        if self.meshes:
            self.overview.v_scene.setText(self.overview.v_scene.text() + f" · {len(self.meshes)} 个网格")

    def _on_clip_statistics(self, statistics):
        self.act_export_clipped.setEnabled(self.viewport._clip_box is not None)
        self._clip_statistics = statistics
        self.overview.set_clip_statistics(statistics)
        self.scene_panel.set_clip_statistics(statistics)
        self._update_status()

    def _show_clip_statistics(self):
        statistics = getattr(self, "_clip_statistics", {})
        if self.viewport._clip_box is None or not statistics:
            return
        visible = [e for e in self.entities if e.visible]
        count = sum(statistics.get(id(e), (0, None))[0] for e in visible)
        original = sum(e.point_count for e in visible)
        self.status_points.setText(f"裁剪后 {count:,} 点 / 原始 {original:,} 点")
        self.overview.v_scene.setText(f"{len(visible)} 片可见 · 裁剪后 {count:,} 点 / 原始 {original:,} 点")

    # ================= 文件 =================
    def _open_files_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "打开文件", self.settings.last_dir,
            "支持的数据 (*.pcap *.pcapng *.mcap *.bag *.db3 *.bin *.pcd *.ply *.xyz *.xyzn *.xyzrgb *.pts *.las *.laz *.e57 *.npy *.npz *.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp *.gif *.txt *.csv *.tsv *.log *.json *.yaml *.yml);;采集包 (*.pcap *.pcapng *.mcap *.bag *.db3);;点云 (*.bin *.pcd *.ply *.xyz *.xyzn *.xyzrgb *.pts *.las *.laz *.e57 *.npy *.npz);;图像 (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp *.gif);;文本与表格 (*.txt *.csv *.tsv *.log *.json *.yaml *.yml);;所有文件 (*)")
        if files:
            self.open_paths(files)

    def _open_folder_dialog(self):
        folder = QFileDialog.getExistingDirectory(self, "打开文件夹 / ROS2 录制目录", self.settings.last_dir)
        if folder:
            self.open_paths([folder])

    # ================= 雷达包导入（pcap / mcap） =================
    def _start_lidar_import(self, preset=None):
        """弹出导入对话框；preset 为已选好的包路径（拖放/最近文件）。"""
        if self._closing:
            return
        if self._source_operation is not None:
            self._flash_status("文件操作进行中，请稍后再导入雷达包。")
            return
        path = preset if isinstance(preset, str) else None
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "打开采集包", self.settings.last_dir,
                "采集包 (*.pcap *.pcapng *.mcap *.bag *.db3);;所有文件 (*)")
        if not path:
            return
        path = os.path.abspath(path)
        self.settings.last_dir = os.path.dirname(path)
        self._open_recording_sources([path])

    def _project_operation_blocked(self):
        blocked = bool(self._source_operation is not None or self._threads or self._scan_threads
                       or self._filter_thread is not None or self._eps_thread is not None
                       or self._recording_importing
                       or any(thread.isRunning() for thread in self._recording_threads()))
        if blocked:
            self._flash_status("正在加载、处理或操作原文件，请完成后再操作工作区。", 6000)
        return blocked

    def _clear_project_scene(self):
        if self._project_operation_blocked():
            return False
        self._restoring_project = True
        try:
            self._clear_recordings()
            self._clip_commit_timer.stop()
            self._set_tool(None)
            self.viewport.clear_measurement()
            self.viewport.clear_selection()
            self._selection = []
            self.entities.clear()
            for mesh in list(self.meshes):
                self._remove_mesh(mesh)
            self._auto_mesh = None
            self._load_queue.clear()
            self._pending_display.clear()
            self._deferred_imports.clear()
            self._pending_regroups.clear()
            self._explicit_loads.clear()
            self._frame_cache.clear()
            self._known_source_entities = WeakSet()
            self._scene_snapshots = WeakSet()
            self._trash_records = WeakSet()
            self._result_sources = WeakKeyDictionary()
            self.history.clear()
            self.scene_panel.set_entities([])
            self.scene_panel.restore_tree_state({"roots": [], "directories": [], "expanded": [], "placements": {}})
            self._apply_clip_state({"enabled": False})
            self._camera_initialized = False
            self._show_entity(None)
        finally:
            self._restoring_project = False
        self._refresh_scene()
        if self.frame_browser.secondary is not None:
            self.frame_browser.secondary.set_entities([])
        self._update_history_buttons()
        return True

    @staticmethod
    def _write_asset(entity: CloudEntity, path: str) -> None:
        """派生点云落盘为 npz（xyz + 全部标量场），避免工程只存路径而丢失结果。"""
        arrays = {"xyz": np.asarray(entity.xyz, dtype=np.float64)}
        for name, field in entity.fields.items():
            arrays["field__" + name] = np.asarray(field.data)
            arrays["kind__" + name] = np.array(str(field.kind))
        np.savez_compressed(path, **arrays)

    @staticmethod
    def _store_project_asset(directory, kind, writer):
        """Publish immutable, content-addressed assets before updating the manifest."""
        os.makedirs(directory, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".saving-", suffix=".npz", dir=directory)
        os.close(descriptor)
        def digest(path):
            result = hashlib.sha256()
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    result.update(chunk)
            return result.hexdigest()
        try:
            writer(temporary)
            checksum = digest(temporary)
            name = f"{kind}-{checksum}.npz"
            target = os.path.join(directory, name)
            try:
                os.link(temporary, target)
            except FileExistsError:
                if digest(target) != checksum:
                    name = f"{kind}-{checksum}-{uuid4().hex}.npz"
                    os.link(temporary, os.path.join(directory, name))
            return f"{os.path.basename(directory)}/{name}"
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _read_asset(path: str):
        with np.load(path, allow_pickle=False) as data:
            xyz = np.asarray(data["xyz"], dtype=np.float32)
            fields = {}
            for key in data.files:
                if not key.startswith("field__"):
                    continue
                name = key[len("field__"):]
                kind_key = "kind__" + name
                kind = str(data[kind_key]) if kind_key in data.files else "scalar"
                fields[name] = ScalarField(name, np.asarray(data[key]), kind=kind)
        return xyz, fields

    def save_project(self, path: str) -> bool:
        """保存工程到 .pls（非对话框入口，供测试/脚本复用）。

        v2 格式：源文件写相对路径，派生点云写入 `<工程名>.assets/*.npz`，
        同时保存相机、裁剪范围与每片点云的显示状态。
        """
        if self._source_operation is not None:
            self._flash_status("文件移动中，工作区尚未保存，请稍后再试。", 6000)
            return False
        if not path.lower().endswith(".pls"):
            path += ".pls"
        base = os.path.dirname(os.path.abspath(path))
        try:
            files, derived, meshes = [], [], []
            asset_dir = os.path.splitext(os.path.abspath(path))[0] + ".assets"
            for index, e in enumerate(self.entities):
                entry = {
                    "name": e.name,
                    "order": index,
                    "visible": bool(e.visible),
                    "visibility_pinned": e.visibility_pinned,
                    "color_mode": e.color_mode,
                    "colormap": e.colormap,
                    "color_field": e.color_field,
                    "color_range": e.color_range,
                    "point_size": float(e.point_size),
                    "loaded": float(e.loaded),
                    "tier": e.tier,
                    "info": e.info,
                }
                if e.path:
                    try:
                        source_path = os.path.relpath(os.path.abspath(e.path), base)
                    except ValueError:
                        source_path = os.path.abspath(e.path)
                    entry["path"] = source_path.replace(os.sep, "/")
                    files.append(entry)
                    continue
                if e.xyz is None or not len(e.xyz):
                    continue
                entry["asset"] = self._store_project_asset(asset_dir, "cloud",
                                                          lambda target, cloud=e: self._write_asset(cloud, target))
                derived.append(entry)

            for index, mesh in enumerate(self.meshes):
                asset = self._store_project_asset(asset_dir, "mesh", lambda target, m=mesh:
                    np.savez_compressed(target, vertices=m.vertices, triangles=m.triangles))
                meshes.append({"name": mesh.name, "asset": asset,
                               "visible": mesh.visible, "color": list(mesh.color),
                               "opacity": mesh.opacity, "info": mesh.info})

            clip = self._clip_state()
            tree_state = self.scene_panel.tree_state()
            portable_tree = {}
            for key in ("roots", "directories", "expanded"):
                portable_tree[key] = []
                for folder in tree_state.get(key, []):
                    try:
                        folder = os.path.relpath(folder, base)
                    except ValueError:
                        pass
                    portable_tree[key].append(folder.replace(os.sep, "/"))
            def portable_path(source):
                try:
                    return os.path.relpath(source, base).replace(os.sep, "/")
                except ValueError:
                    return source
            portable_tree["placements"] = {portable_path(source): portable_path(group)
                                           for source, group in tree_state.get("placements", {}).items()}
            data = {
                "version": 2,
                "scene_tree": portable_tree,
                "theme": self.theme,
                "camera": self.viewport.camera_state(),
                "clip": {
                    "enabled": bool(clip.get("enabled")),
                    "min": [float(v) for v in clip["min"]] if clip.get("enabled") else None,
                    "max": [float(v) for v in clip["max"]] if clip.get("enabled") else None,
                },
                "files": files,
                "recordings": self._recording_state(base),
                "derived": derived,
                "meshes": meshes,
                "browser": {
                    "current": self.entities.index(self.scene_panel.current_entity())
                               if self.scene_panel.current_entity() in self.entities else -1,
                    "compare": self.frame_browser.compare.isChecked(),
                    "direction": self.frame_browser.direction.currentData(),
                    "linked": self.frame_browser.linked.isChecked(),
                },
            }
            temporary = path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
            self.log(f"工作区已保存: {path}（{len(files)} 片源点云，{len(derived)} 片派生点云，{len(meshes)} 个网格）")
            self._flash_status("工作区已保存")
            self._mark_project_saved(path)
            return True
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return False

    def _add_mesh(self, mesh):
        if mesh not in self.meshes:
            self.meshes.append(mesh)
            self.viewport.add_mesh_entity(mesh)
        self._update_status()

    def _remove_mesh(self, mesh):
        if mesh in self.meshes:
            self.meshes.remove(mesh)
            self.viewport.remove_mesh_entity(mesh)
        self._update_status()

    def _populate_mesh_menu(self):
        self.mesh_menu.clear()
        if not self.meshes:
            self.mesh_menu.addAction("暂无网格结果").setEnabled(False)
        for mesh in self.meshes:
            menu = self.mesh_menu.addMenu(f"{mesh.name} · {mesh.triangle_count:,} 面")
            visible = menu.addAction("显示")
            visible.setCheckable(True)
            visible.setChecked(mesh.visible)
            visible.toggled.connect(lambda checked, m=mesh: self._set_mesh_visible(m, checked))
            menu.addAction("导出 PLY…", lambda m=mesh: self._export_mesh(m))
            menu.addAction("移除", lambda m=mesh: self._remove_mesh_with_history(m))

    def _set_mesh_visible(self, mesh, visible):
        if self._auto_mesh is mesh:
            self._auto_mesh = None
        mesh.visible = visible
        self.viewport._refresh_meshes()
        self.viewport._invalidate()

    def _remove_mesh_with_history(self, mesh):
        self._remove_mesh(mesh)
        self.history.push(f"移除 {mesh.name}", lambda: self._add_mesh(mesh),
                          lambda: self._remove_mesh(mesh))
        self._update_history_buttons()

    def _export_mesh(self, mesh):
        path, _ = QFileDialog.getSaveFileName(self, "导出网格", mesh.name + ".ply", "PLY 网格 (*.ply)")
        if path:
            if not path.lower().endswith(".ply"):
                path += ".ply"
            try:
                mesh_ops.write_mesh_ply(path, mesh.vertices, mesh.triangles)
                self._flash_status("网格已导出")
            except Exception as exc:
                QMessageBox.warning(self, "导出失败", str(exc))

    def _export_clipped_dialog(self):
        self._export_current_dialog(clipped=True)

    def _export_current_dialog(self, *, clipped=False):
        if self.content_stack.currentWidget() is self.recording_browser:
            if not clipped:
                self.recording_browser.export_current()
            return
        entity = self._current_entity()
        if entity is None:
            return
        if clipped:
            if self.viewport._clip_box is None:
                self._flash_status("请先启用裁剪范围")
                return
            mn, mx = self.viewport._clip_box
            keep = np.all((entity.xyz >= mn) & (entity.xyz <= mx), axis=1)
            if not keep.any():
                self._flash_status("当前点云在裁剪框内没有点")
                return
            entity = self._make_result(entity, "裁剪", entity.xyz[keep],
                                       entity.subset_fields(keep), "裁剪内点云")
        default = os.path.splitext(entity.name)[0] + ".ply"
        path, selected_filter = QFileDialog.getSaveFileName(
            self, "导出裁剪内点云" if clipped else "导出当前点云（完整数据）", default,
            "PLY 点云 (*.ply);;PCD 点云 (*.pcd);;LAS 点云 (*.las);;PTS 点云 (*.pts);;XYZ 文本 (*.xyz)")
        if path:
            if not os.path.splitext(path)[1]:
                path += selected_filter.split("*", 1)[1].split(")", 1)[0]
            self.export_entity(entity, path)

    def export_entity(self, entity: CloudEntity, path: str) -> bool:
        """导出点云（非对话框入口，供测试/脚本复用）。"""
        try:
            from core.io.exporters import export_entity as write_entity

            write_entity(entity, path)
            self.log(f"已导出: {path}（{entity.point_count:,} 点）")
            self._flash_status(f"已导出 {os.path.basename(path)}")
            return True
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(exc))
            return False

    def open_project(self, path: str) -> bool:
        """从 .pls 打开工程（先登记显示状态，再按 Auto-Ingest 加载）。"""
        if self._source_operation is not None:
            self._flash_status("文件移动中，请稍后再打开工作区。", 6000)
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            base = os.path.dirname(os.path.abspath(path))
            if data.get("theme"):
                self._set_theme(data["theme"])
            self._restoring_project = True
            tree_state = data.get("scene_tree")
            restored_tree = None
            if isinstance(tree_state, dict):
                current_tree = self.scene_panel.tree_state()
                restored_tree = {}
                for key in ("roots", "directories", "expanded"):
                    restored_tree[key] = list(dict.fromkeys(
                        current_tree.get(key, []) + [
                            os.path.abspath(os.path.join(base, folder))
                            for folder in tree_state.get(key, []) if isinstance(folder, str)
                        ]
                    ))
                restored_tree["placements"] = dict(current_tree.get("placements", {}))
                restored_tree["placements"].update({
                    os.path.abspath(os.path.join(base, source)): os.path.abspath(os.path.join(base, group))
                    for source, group in tree_state.get("placements", {}).items()
                    if isinstance(source, str) and isinstance(group, str)
                })
                self.scene_panel.restore_tree_state(restored_tree)
            initial_count = len(self.entities)
            restored_order = {}

            camera = data.get("camera")
            clip = data.get("clip")
            if isinstance(clip, dict):
                state = {"enabled": bool(clip.get("enabled")), "min": None, "max": None}
                if state["enabled"] and clip.get("min") and clip.get("max"):
                    state["min"] = np.array(clip["min"], dtype=np.float64)
                    state["max"] = np.array(clip["max"], dtype=np.float64)
                self._apply_clip_state(state)

            entries = []
            missing = []
            for entry in data.get("files", []):
                source = entry.get("path")
                if not source:
                    continue
                source = os.path.abspath(os.path.join(base, source))
                if not os.path.isfile(source):
                    missing.append(source)
                    continue
                entries.append(dict(entry, path=source))
            for entry in entries:
                self._pending_display[entry["path"]] = entry
                existing = next((e for e in self.entities if e.path == entry["path"]), None)
                if existing is not None:
                    self._apply_pending(existing)
            self.open_paths([e["path"] for e in entries])
            for index, entry in enumerate(entries):
                entity = next((e for e in self.entities if e.path == entry["path"]), None)
                if entity is not None:
                    restored_order[entity] = entry.get("order", index)

            derived_failed = 0
            for entry in data.get("derived", []):
                asset = entry.get("asset")
                if not asset:
                    continue
                asset_path = os.path.abspath(os.path.join(base, asset))
                try:
                    xyz, fields = self._read_asset(asset_path)
                except Exception as exc:  # noqa: BLE001
                    derived_failed += 1
                    self.log(f"派生点云读取失败: {asset} — {exc}")
                    continue
                entity = CloudEntity(name=entry.get("name") or os.path.basename(asset_path),
                                     path=None, xyz=xyz, fields=fields)
                entity.visible = bool(entry.get("visible", True))
                entity.visibility_pinned = bool(entry.get("visibility_pinned", entity.visible))
                mode = entry.get("color_mode", "height")
                entity.color_mode = mode if mode in COLOR_MODES else "height"
                entity.colormap = entry.get("colormap", "default")
                entity.color_field = entry.get("color_field", "")
                color_range = entry.get("color_range")
                entity.color_range = tuple(color_range) if color_range is not None else None
                entity.point_size = float(entry.get("point_size", 2.0))
                entity.info = entry.get("info", "工作区内嵌派生点云")
                entity.loaded = float(entry.get("loaded", 1.0))
                entity.tier = entry.get("tier", tier_by_points(entity.point_count))
                self._add_entity(entity)
                restored_order[entity] = entry.get("order", len(entries) + len(restored_order))

            for entry in data.get("meshes", []):
                try:
                    asset_path = os.path.abspath(os.path.join(base, entry["asset"]))
                    with np.load(asset_path, allow_pickle=False) as asset:
                        mesh = MeshEntity(entry.get("name", "网格"),
                                          np.asarray(asset["vertices"], dtype=np.float32),
                                          np.asarray(asset["triangles"], dtype=np.int32),
                                          visible=bool(entry.get("visible", True)),
                                          color=tuple(entry.get("color", (0.56, 0.66, 0.82))),
                                          opacity=float(entry.get("opacity", 1)),
                                          info=entry.get("info", ""))
                    self._add_mesh(mesh)
                except Exception as exc:
                    derived_failed += 1
                    self.log(f"网格恢复失败: {entry.get('name', '网格')} — {exc}")

            browser = data.get("browser", {})
            self.entities[initial_count:] = sorted(self.entities[initial_count:],
                                                   key=lambda e: restored_order.get(e, len(self.entities)))
            self.scene_panel.set_entities(self.entities)
            current_order = int(browser.get("current", 0))
            current = next((e for e, order in restored_order.items() if order == current_order), None)
            if current is None and self.entities:
                current = self.entities[min(initial_count, len(self.entities) - 1)]
            if current is not None:
                self.scene_panel.select_entity(current)
            if restored_tree is not None:
                self.scene_panel.restore_tree_state(restored_tree)
            self._restoring_project = False
            self.frame_browser.direction.setCurrentIndex(0 if browser.get("direction", 1) == 1 else 1)
            self.frame_browser.linked.setChecked(browser.get("linked", True))
            self.frame_browser.compare.setChecked(browser.get("compare", False))
            self._refresh_scene()
            self._show_entity(self.scene_panel.current_entity())
            # 首片点云加载会重置视角，工程相机必须在场景就绪后恢复。
            if isinstance(camera, dict):
                self.viewport.set_camera_state(camera)
                self._camera_initialized = True
                self.frame_browser._sync_camera(self.viewport)
            if missing:
                self.log("工作区源文件缺失：\n" + "\n".join(missing))
                self._flash_status(f"工作区缺少 {len(missing)} 个源文件，请查看日志", 6000)
                return False
            self.log(f"工作区已打开: {path}")
            if derived_failed:
                self.log(f"有 {derived_failed} 个派生数据未能恢复")
                self._flash_status("部分工作区数据未能恢复，请查看日志", 6000)
                return False
            self._mark_project_saved(path)
            self._restore_recordings(data.get("recordings", {}), base)
            return True
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开失败", str(exc))
            return False
        finally:
            self._restoring_project = False

    # ================= 拖放 / 日志 / 关闭 =================
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.open_paths([u.toLocalFile() for u in event.mimeData().urls()])

    def closeEvent(self, event):
        """关闭前停止渲染循环并回收后台线程，避免 wgpu 回调/线程销毁崩溃。"""
        if not self._closing and not self._confirm_unsaved_project():
            event.ignore()
            return
        self._closing = True
        self.recording_browser.cancel()
        if self._recording_dialog is not None:
            self._recording_dialog.reject()
        try:
            self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
            self.settings.theme = self.theme
            save_settings(self.settings)
        except Exception:  # noqa: BLE001
            pass
        threads = list(self._threads.values()) + self._scan_threads + self._recording_threads()
        if self._filter_thread is not None:
            threads.append(self._filter_thread)
        if self._eps_thread is not None:
            threads.append(self._eps_thread)
        if self._source_operation is not None:
            threads.append(self._source_operation)
        for thread in threads:
            thread.requestInterruption()
        if any(thread.isRunning() for thread in threads):
            if self._close_deadline is None:
                self._close_deadline = time.monotonic() + 3.0
            if time.monotonic() < self._close_deadline:
                event.ignore()
                self.status_task.setText("正在等待后台任务结束后关闭…")
                self.setEnabled(False)
                QTimer.singleShot(100, self.close)
                return
            # 原生算法可能无法在一次计算中途取消，不能销毁仍运行的 QThread。
            event.ignore()
            self.status_task.setText("正在等待当前计算结束后关闭…")
            QTimer.singleShot(200, self.close)
            return
        self._close_deadline = None
        try:
            self.viewport.stop()
            self.frame_browser.stop()
            self.recording_browser.stop()
            for source in self._recording_sources.values():
                source.close()
        except Exception:
            pass
        self._threads.clear()
        self._load_queue.clear()
        super().closeEvent(event)

    def log(self, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.log_view.appendPlainText(line)
        self._logger.info(msg)
