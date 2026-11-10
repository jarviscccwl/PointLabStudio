"""3D 视口：pygfx（wgpu GPU 渲染）嵌入 Qt。

- 任意规模点云同一个视口展示（Auto-Ingest 分级在 core 层完成）
- 键盘控制：R/C/+/-/WASD/QE/←→
- 背景渐变（深/浅主题）、坐标轴、地面网格、GPU 裁剪盒
"""

from __future__ import annotations

import time
import logging
from typing import Optional

import numpy as np
from PySide6.QtCore import QEvent, QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from rendercanvas.qt import RenderCanvas

import pygfx as gfx

from core.color.colormaps import compute_colors
from core.geometry import lod
from ui.viewport.annotation_overlay import AnnotationOverlay, annotation_corners

# 转盘相机手感：旋转灵敏度（弧度/像素）、滚轮缩放步长。
ORBIT_SPEED = 0.0045
ZOOM_STEP = 0.90


def _points_in_polygon(sx, sy, poly) -> np.ndarray:
    """向量化射线法：屏幕坐标点是否落在多边形内（含近似边界）。"""
    inside = np.zeros(len(sx), dtype=bool)
    if len(poly) < 3 or not len(sx):
        return inside
    finite = np.isfinite(sx) & np.isfinite(sy)
    x = np.where(finite, sx, -1e9).astype(np.float64)
    y = np.where(finite, sy, -1e9).astype(np.float64)
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = float(poly[i][0]), float(poly[i][1])
        xj, yj = float(poly[j][0]), float(poly[j][1])
        if yi != yj:
            cross = ((yi > y) != (yj > y)) & (x < (xj - xi) * (y - yi) / (yj - yi) + xi)
            inside ^= cross
        j = i
    return inside & finite


class _SelectionRubber(QWidget):
    """框选/套索过程中的透明橡皮筋层（只负责画，不吃鼠标）。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._shape = None  # ("box", (x0,y0,x1,y1)) / ("lasso", [(x,y), ...])
        self.hide()

    def set_shape(self, shape):
        self._shape = shape
        self.setGeometry(self.parent().rect())
        self.raise_()
        self.show()
        self.update()

    def clear_shape(self):
        self._shape = None
        self.hide()

    def paintEvent(self, event):  # noqa: N802
        if not self._shape:
            return
        kind, data = self._shape
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        color = QColor("#005FCC") if self.parent().theme == "light" else QColor(96, 176, 255, 235)
        pen = QPen(color, 1.4)
        if kind == "box":
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        if kind == "box":
            x0, y0, x1, y1 = data
            painter.drawRect(int(min(x0, x1)), int(min(y0, y1)),
                             int(abs(x1 - x0)), int(abs(y1 - y0)))
        else:
            painter.drawPolyline(QPolygonF([QPointF(x, y) for x, y in data]))
        painter.end()


class PointCloudViewport(RenderCanvas):
    # 键盘语义信号（由主窗口处理）
    key_next_file = Signal()
    key_prev_file = Signal()
    key_reset_view = Signal()
    key_cycle_color = Signal()
    key_toggle_clipbox = Signal()
    key_show_help = Signal()
    key_point_size = Signal(float)   # delta
    fps_updated = Signal(float)
    render_problem = Signal(str)
    files_dropped = Signal(list)
    measured = Signal(float)          # 两点距离（米）
    mode_changed = Signal(str)        # "" / "measure" / "select" / "box" / "lasso"
    selection_changed = Signal(object)  # [(entity, np.ndarray indices), ...]；[] 表示清空
    box_creation_requested = Signal()
    annotation_selected = Signal(str)
    navigation_changed = Signal(bool)
    camera_changed = Signal()
    clip_drag_started = Signal()
    clip_dragged = Signal(object, object)
    clip_drag_finished = Signal()
    clip_statistics_changed = Signal(object)

    def __init__(self, theme: str = "dark", parent=None):
        super().__init__(parent=parent, max_fps=120)
        self.setFocusPolicy(Qt.StrongFocus)

        self.theme = theme
        # Render at the screen's physical resolution, with pygfx's anti-aliasing.
        # Its default supersamples low/medium-DPI screens by 2 in each axis,
        # multiplying fill and intermediate image traffic during camera motion.
        self.renderer = gfx.WgpuRenderer(self, pixel_scale=1)
        self.scene = gfx.Scene()

        self.camera = gfx.PerspectiveCamera(50, 16 / 9)
        self.camera.local.position = (0, -8, 4)
        self.camera.look_at((0, 0, 0))

        # CloudCompare 风格转盘相机：左键旋转（绕 Z 轴 yaw + 俯仰）、右键/中键平移、滚轮缩放、
        # 双击把旋转中心设为鼠标下的点。自己维护球坐标，避免 OrbitController 绕原点旋转。
        self.controller = None
        self._fixed_view = False
        self._walk_remaining = 0.0
        self._walk_velocity = 0.0
        self._pivot = np.zeros(3, dtype=np.float64)
        self._yaw = np.deg2rad(-90.0)
        self._pitch = np.deg2rad(24.0)
        self._distance = 9.0
        self._drag = None  # ("orbit"|"pan", last_x, last_y)
        # 平滑缩放：滚轮只改目标距离，渲染循环里按帧阻尼逼近，避免一格一跳。
        self._zoom_target = self._distance
        self._zoom_velocity = 0.0
        self._zoom_animating = False
        self._zoom_lod_pending = False
        self._last_frame_time = time.monotonic()
        self._apply_camera()

        self.entities: list = []
        self.mesh_entities: list = []
        self._mesh_objs: list = []
        self._points_objs: list = []
        self._point_entities: list = []
        self._render_cache: dict = {}  # id(entity) -> (签名, 渲染对象, entity)
        self._proj_cache: dict = {}  # (id(xyz), n) -> (相机键, sx, sy, depth, ok)
        self._clip_box: Optional[tuple] = None  # (min3, max3)
        self._overlays: list = []
        self._grid_visible = True

        self._axis_obj = None
        self._axis_group = None
        self._clip_lines = None
        self._clip_handles = None
        self._clip_drag = None
        self._clip_drag_perspective = 0.0
        self._clip_stats_job = None
        self._clip_stats_timer = QTimer(self)
        self._clip_stats_timer.setInterval(1)
        self._clip_stats_timer.timeout.connect(self._step_clip_statistics)
        self._grid_obj = None
        self._grid_theme = None
        self._grid_base_size = 1.0
        self._grid_divisions = None
        self._background = None
        self._apply_background()

        self._fps_frames = 0
        self._fps_time = time.monotonic()
        self._running = True
        self._needs_draw = True
        self._render_failures = 0
        self._stall_recoveries = 0
        self._last_recovery_at = 0.0
        self._lod_state: dict[int, int] = {}
        self._lod_metrics: dict = {}
        self._lod_counts: dict[int, int] = {}  # 脏标记：只在场景/相机变化后实际提交 GPU 帧

        # P4：交互模式（测量 / 选点）
        self._mode = None
        self.box_input_enabled = False
        self._measure_pts = []
        self._measure_objs = []
        self._selected_point = None
        self._selection_marker = None
        self.measure_label = QLabel(self)
        self.measure_label.setObjectName("overlayPill")
        self.measure_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.measure_label.hide()
        self.selection_label = QLabel(self)
        self.selection_label.setObjectName("overlayPill")
        self.selection_label.setTextFormat(Qt.PlainText)
        self.selection_label.setWordWrap(True)
        self.selection_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.selection_label.hide()

        # E18：框选/套索
        self._sel_mode = None            # None / "box" / "lasso"
        self._sel_dragging = False
        self._sel_start = None
        self._sel_points = []
        self._sel_highlight = None       # 选中点的高亮 gfx.Points
        self._sel_rubber = _SelectionRubber(self)
        self._annotation_overlay = AnnotationOverlay(self)

        # RenderCanvas 的内层 QWidget 消费鼠标事件，不会传到外层工具处理器。
        self._subwidget.installEventFilter(self)

        # 空状态引导（Apple 式空态）
        self.empty_label = None
        self._ensure_empty_label()

        self.request_draw(self._animate)
        self._render_watchdog = QTimer(self)
        self._render_watchdog.setInterval(250)
        self._render_watchdog.timeout.connect(self._check_render_health)
        self._render_watchdog.start()

    # ---------------- 空状态标签 ----------------
    def _ensure_empty_label(self):
        """创建/校验空状态标签（跨主题切换后 Qt 对象可能失效，需重建）。"""
        try:
            import shiboken6
            if self.empty_label is not None and shiboken6.isValid(self.empty_label):
                return self.empty_label
        except Exception:
            pass

        label = QLabel("拖入点云文件或文件夹", self)
        label.setObjectName("bigTitle")
        label.setAlignment(Qt.AlignCenter)
        label.setAttribute(Qt.WA_TransparentForMouseEvents)
        label.setAttribute(Qt.WA_StyledBackground, False)
        label.hide()
        self.empty_label = label
        return label

    def _set_empty_visible(self, visible: bool):
        label = self._ensure_empty_label()
        label.setVisible(visible)
        self._center_empty_label()

    # ---------------- 悬浮层（自绘工具条等） ----------------
    def add_overlay(self, widget):
        self._overlays.append(widget)
        self._layout_overlays()

    def _layout_overlays(self):
        for w in getattr(self, "_overlays", []):
            try:
                w.adjustSize()
                w.move((self.width() - w.width()) // 2, self.height() - w.height() - 20)
            except Exception:
                pass

    def set_grid_visible(self, visible: bool):
        self._grid_visible = bool(visible)
        if self._grid_obj is not None:
            try:
                self._grid_obj.visible = self._grid_visible
                self._needs_draw = True
            except Exception:
                pass

    # ---------------- 渲染循环 ----------------
    def snapshot_rgb(self):
        """Capture a fresh rendered view on the GUI thread, preserving the camera."""
        if not self._running or min(self.get_physical_size()) <= 0:
            raise ValueError("点云视图尚未就绪，请打开预览后再导出")
        self._annotation_overlay.update_labels()
        self.renderer.render(self.scene, self.camera)
        pixels = np.asarray(self.renderer.snapshot())
        if pixels.ndim != 3 or pixels.shape[2] < 3:
            raise RuntimeError("无法获取当前点云画面")
        return np.ascontiguousarray(pixels[..., :3])

    def stop(self):
        """停止渲染循环（关闭前调用，避免画布销毁后 wgpu 回调仍触发）。"""
        if not self._running:
            return
        self._running = False
        self._render_watchdog.stop()
        self._clip_stats_timer.stop()
        self._clip_stats_job = None
        self._annotation_overlay.clear()
        # rendercanvas 2.7.2 close() does not cancel pending bitmap downloads.
        # Drain its downloader before deleting Qt, including zero-size canvases
        # where force_draw() would skip presentation. Screen contexts have none.
        try:
            context = getattr(self._subwidget, "_canvas_context", None)
            downloader = getattr(context, "_downloader", None)
            if downloader is not None:
                downloader._clear_pending_download()
        finally:
            self.close()

    def _animate(self):
        if not self._running:
            return
        now = time.monotonic()
        dt = min(max(now - self._last_frame_time, 0.0), 0.1)
        self._last_frame_time = now
        try:
            self._step_zoom_animation(dt)
            self._step_walk_animation(dt)
            if self._needs_draw:
                self._needs_draw = False
                self._annotation_overlay.update_labels()
                self.renderer.render(self.scene, self.camera)
                self._render_failures = 0
                self._fps_frames += 1
            if now - self._fps_time >= 0.5:
                if self._fps_frames:
                    self.fps_updated.emit(self._fps_frames / (now - self._fps_time))
                self._fps_frames = 0
                self._fps_time = now
        except Exception as error:
            # A dirty frame must survive a failed overlay/render operation.
            # In particular, do not let an exception skip scheduling the next draw.
            self._needs_draw = True
            self._render_failures += 1
            if self._render_failures == 1:
                logging.getLogger(__name__).exception("Point-cloud draw failed; retrying")
            if self._render_failures == 3:
                self.render_problem.emit(f"点云显示暂时异常，请重新加载该话题。{error}")
        finally:
            if self._running:
                self.request_draw()

    def _check_render_health(self):
        if (not self._running or not self.isVisible() or self.window().isMinimized()
                or min(self.get_physical_size()) <= 0):
            return
        now = time.monotonic()
        if now - self._last_frame_time < 1.0:
            # A forced frame alone is not evidence that the scheduler resumed.
            if self._last_frame_time > self._last_recovery_at + .05:
                self._stall_recoveries = 0
            return
        self._last_recovery_at = now
        if self.recover_rendering():
            # A delayed Qt callback can follow a slow load or window transition.
            # Count failed presentations, not successful forced redraws.
            self._stall_recoveries = 0
            return
        self._stall_recoveries += 1
        if self._stall_recoveries == 3:
            self.render_problem.emit("点云显示未能恢复，播放已暂停。请在话题菜单中重新加载。")

    def recover_rendering(self):
        """Recover a stalled presentation scheduler through the public canvas API.

        rendercanvas 2.7.2 can leave its scheduler waiting after a presentation
        initialization exception. Another request_draw alone cannot release it;
        a synchronous presentation completes the pending scheduler event.
        """
        if not self._running:
            return False
        self._needs_draw = True
        self.request_draw(self._animate)
        if self.isVisible() and min(self.get_physical_size()) > 0:
            try:
                previous = getattr(self._subwidget, "_last_image", None)
                self.force_draw()
                # Qt bitmap presentation creates a new retained image tuple.
                # rendercanvas catches present errors internally, so returning
                # from force_draw alone does not establish that a frame arrived.
                presented = getattr(self._subwidget, "_last_image", None)
                return (not self._needs_draw and presented is not None
                        and presented is not previous)
            except Exception:
                logging.getLogger(__name__).exception("Point-cloud display recovery failed")
                return False
        return True

    def _invalidate(self, *_args):
        """标记需要重绘；空闲时不再空转 GPU。"""
        self._needs_draw = True

    # ---------------- 场景搭建 ----------------
    def _theme_colors(self):
        if self.theme == "light":
            return "#EDEDF0", "#D8D8DD", "#B9B9C0"
        return "#101016", "#050507", "#5A5A66"

    def _tool_color(self):
        return "#A65300" if self.theme == "light" else (1.0, 0.72, 0.25, 1.0)

    def _apply_background(self):
        top, bottom, _ = self._theme_colors()
        if self._background is not None:
            try:
                self.scene.remove(self._background)
            except Exception:
                pass
        bg = None
        try:
            bg = gfx.Background(None, gfx.BackgroundMaterial(top, bottom))
        except Exception:
            try:
                bg = gfx.Background(gfx.BackgroundMaterial(None, (top, top, bottom, bottom)),
                                    gfx.plane_geometry(1, 1))
            except Exception:
                try:
                    bg = gfx.Background(gfx.BackgroundMaterial(None, (top, top, bottom, bottom)),
                                        gfx.plane_geometry(1, 1))
                except Exception:
                    bg = None
        self._background = bg
        if bg is not None:
            self.scene.add(bg)
            try:
                self.scene.background = bg
            except Exception:
                pass

    def _ensure_helpers(self):
        """坐标轴 + 地面网格：z-up 场景，网格放平贴在数据底部。"""
        extent = self._scene_extent()
        size = max(extent, 1.0)
        bounds = self._scene_bounds()

        # 坐标轴：3 条细线 + 原点小圆点（简洁，替代粗箭头）
        axis_size = size * 0.045
        if self._axis_group is None:
            try:
                group = gfx.Group()
                axis_colors = {"x": (0.95, 0.35, 0.30, 1.0),
                               "y": (0.45, 0.85, 0.40, 1.0),
                               "z": (0.35, 0.60, 1.0, 1.0)}
                for key, end in (("x", (1, 0, 0)), ("y", (0, 1, 0)), ("z", (0, 0, 1))):
                    line = gfx.Line(
                        gfx.Geometry(positions=np.array([[0, 0, 0], end], dtype=np.float32)),
                        gfx.LineMaterial(thickness=2, color=axis_colors[key]),
                    )
                    group.add(line)
                dot = gfx.Mesh(
                    gfx.sphere_geometry(radius=0.012, width_segments=10, height_segments=6),
                    gfx.MeshBasicMaterial(color=(0.75, 0.78, 0.82, 1.0)),
                )
                group.add(dot)
                self._axis_group = group
                self.scene.add(group)
            except Exception:
                self._axis_group = None
        if self._axis_group is not None:
            try:
                self._axis_group.local.scale = (axis_size, axis_size, axis_size)
            except Exception:
                pass

        # 地面网格：pygfx GridHelper 默认躺在 XZ 平面（y-up 约定），
        # 需绕 X 轴旋转 90° 才与 z-up 数据的地面平行。
        if hasattr(gfx, "GridHelper"):
            grid_size = size * 1.6
            divisions = max(10, min(80, int(grid_size / 10)))
            if self._grid_obj is not None and (
                    self._grid_theme != self.theme or self._grid_divisions != divisions):
                try:
                    self.scene.remove(self._grid_obj)
                except Exception:
                    pass
                self._grid_obj = None
            if self._grid_obj is None:
                try:
                    c1 = "#C8C8D0" if self.theme == "light" else "#23232C"
                    c2 = "#DEDEE4" if self.theme == "light" else "#3A3A44"
                    self._grid_obj = gfx.GridHelper(size=grid_size, divisions=divisions,
                                                    thickness=1, color1=c1, color2=c2)
                    # 绕 X 轴 +90°（四元数顺序 x,y,z,w）：XZ 平面 → XY 地面
                    self._grid_obj.local.rotation = (0.70710678, 0.0, 0.0, 0.70710678)
                    self._grid_obj.visible = self._grid_visible
                    self._grid_theme = self.theme
                    self._grid_base_size = grid_size
                    self._grid_divisions = divisions
                    self.scene.add(self._grid_obj)
                except Exception:
                    self._grid_obj = None

            if self._grid_obj is not None:
                try:
                    scale = grid_size / self._grid_base_size
                    self._grid_obj.local.scale = (scale, scale, scale)
                except Exception:
                    pass
                if bounds is not None:
                    mn, mx = bounds
                    cx, cy = (mn[0] + mx[0]) / 2.0, (mn[1] + mx[1]) / 2.0
                    ground_z = float(mn[2]) - size * 0.002
                    try:
                        self._grid_obj.local.position = (cx, cy, ground_z)
                    except Exception:
                        pass

    def _scene_extent(self) -> float:
        total = 0.0
        for e in self.entities:
            if e.visible and e.xyz is not None and len(e.xyz):
                metrics = self._lod_metrics.get(id(e))
                extent = metrics[2] if metrics is not None and metrics[0] is e.xyz else e.extent()
                total = max(total, extent)
        return total

    def _scene_bounds(self):
        """可见点云的合并包围盒 ((mn), (mx))；无数据返回 None。"""
        mins, maxs = [], []
        for e in self.entities:
            if e.visible and e.xyz is not None and len(e.xyz):
                metrics = self._lod_metrics.get(id(e))
                b = metrics[3] if metrics is not None and metrics[0] is e.xyz else e.bounds()
                if b is not None:
                    mins.append(b[0])
                    maxs.append(b[1])
        if not mins:
            return None
        return np.min(mins, axis=0), np.max(maxs, axis=0)

    # ---------------- 数据刷新 ----------------
    def set_entities(self, entities: list):
        # Screen selection owns copied points, separate from the cloud render
        # cache and annotation highlight. They belong only to the old frame.
        self.clear_selection_runtime()
        self._annotation_overlay.clear()
        self.entities = list(entities)
        self._proj_cache.clear()
        self.refresh()

    def set_annotation_boxes(self, boxes: list[dict]):
        """Show source-coordinate boxes for this frame, separate from screen selection.

        Each dictionary contains id, name, category, center XYZ, size length/width/height,
        yaw in radians, selected and manual. Reapply after set_entities().
        """
        if self._running:
            self._annotation_overlay.set_boxes(boxes)

    def set_theme(self, theme: str):
        self.theme = theme
        self._apply_background()
        self._ensure_helpers()
        self._update_clip_lines()
        if self._measure_pts:
            self._draw_measurement_polyline()
        self._annotation_overlay.update_theme()
        self.refresh()

    def set_clip_box(self, mn, mx):
        if mn is not None and mx is not None:
            mn_a = np.asarray(mn, dtype=np.float32).ravel()
            mx_a = np.asarray(mx, dtype=np.float32).ravel()
            if np.any(mn_a >= mx_a):
                return False
        self._clip_box = (np.asarray(mn, dtype=np.float32), np.asarray(mx, dtype=np.float32)) \
            if mn is not None and mx is not None else None
        self._update_clip_lines()
        self._apply_clip_planes()
        self.clear_selection()
        self._needs_draw = True
        if self._clip_drag is None:
            self._schedule_clip_statistics()
        return True

    def _clip_planes(self):
        if self._clip_box is None:
            return []
        mn, mx = self._clip_box
        return [(1, 0, 0, float(mn[0])), (-1, 0, 0, float(-mx[0])),
                (0, 1, 0, float(mn[1])), (0, -1, 0, float(-mx[1])),
                (0, 0, 1, float(mn[2])), (0, 0, -1, float(-mx[2]))]

    def _apply_clip_planes(self):
        planes = self._clip_planes()
        for obj in self._points_objs:
            obj.material.clipping_planes = planes
        self._annotation_overlay.update_clip_planes()

    def _schedule_clip_statistics(self):
        self._clip_stats_timer.stop()
        self._clip_stats_job = None
        if self._clip_box is None:
            self.clip_statistics_changed.emit({})
            return
        bounds = tuple(a.copy() for a in self._clip_box)
        entities = [(id(e), e.xyz) for e in self.entities if e.xyz is not None]

        def scan():
            result = {}
            for key, xyz in entities:
                count, low, high = 0, None, None
                for start in range(0, len(xyz), 100_000):
                    chunk = xyz[start:start + 100_000]
                    selected = chunk[np.all((chunk >= bounds[0]) & (chunk <= bounds[1]), axis=1)]
                    count += len(selected)
                    if len(selected):
                        lo, hi = selected.min(axis=0), selected.max(axis=0)
                        low = lo if low is None else np.minimum(low, lo)
                        high = hi if high is None else np.maximum(high, hi)
                    yield
                result[key] = (count, None if low is None else (low, high))
            self.clip_statistics_changed.emit(result)

        self._clip_stats_job = scan()
        self._clip_stats_timer.start()

    def _step_clip_statistics(self):
        deadline = time.monotonic() + 0.006
        while self._clip_stats_job is not None and time.monotonic() < deadline:
            try:
                next(self._clip_stats_job)
            except StopIteration:
                self._clip_stats_job = None
                self._clip_stats_timer.stop()

    def clear_clip_box(self):
        self._finish_clip_drag()
        self.set_clip_box(None, None)

    def _clip_face_centers(self):
        mn, mx = self._clip_box
        centers = np.tile((mn.astype(float) + mx) / 2, (6, 1))
        for axis in range(3):
            centers[axis * 2, axis] = mn[axis]
            centers[axis * 2 + 1, axis] = mx[axis]
        return centers

    def _clip_handle_screen(self):
        points = self._clip_face_centers()
        matrix = np.asarray(self.camera.projection_matrix) @ np.asarray(self.camera.view_matrix)
        clip = np.column_stack((points, np.ones(6))) @ matrix.T
        valid = (clip[:, 3] > 1e-9)
        ndc = clip[:, :3] / np.where(valid, clip[:, 3], 1)[:, None]
        valid &= (ndc[:, 2] >= 0) & (ndc[:, 2] <= 1)
        screen = np.column_stack(((ndc[:, 0] + 1) * self.width() / 2,
                                  (1 - ndc[:, 1]) * self.height() / 2))
        return points, screen, valid

    def _begin_clip_drag(self, x, y):
        if self._clip_box is None:
            return False
        points, screen, valid = self._clip_handle_screen()
        distances = np.linalg.norm(screen - (x, y), axis=1)
        distances[~valid] = np.inf
        index = int(np.argmin(distances))
        if distances[index] > 12:
            return False
        axis = index // 2
        point = points[index]
        matrix = np.asarray(self.camera.projection_matrix) @ np.asarray(self.camera.view_matrix)
        clip = matrix @ np.append(point, 1)
        # 投影的轴向导数；视线正对该轴时无法可靠拖动，需先旋转视图。
        derivative = (matrix[:2, axis] * clip[3] - clip[:2] * matrix[3, axis]) / clip[3] ** 2
        direction = derivative * (self.width() / 2, -self.height() / 2)
        if np.linalg.norm(direction) * max(self._scene_extent(), 1) < 2:
            return False
        self._sync_zoom()
        self._clip_stats_timer.stop()
        self._clip_stats_job = None
        self._clip_drag_perspective = float(matrix[3, axis] / clip[3])
        self._clip_drag = (index, np.array([x, y]), direction,
                           tuple(a.copy() for a in self._clip_box))
        self._drag = None
        self.clip_drag_started.emit()
        self.setCursor(Qt.SizeAllCursor)
        return True

    def _move_clip_drag(self, x, y):
        index, start, direction, bounds = self._clip_drag
        axis, side = divmod(index, 2)
        delta = float(np.dot(np.array([x, y]) - start, direction) / np.dot(direction, direction))
        # 反解透视投影，而不是把屏幕位移当作固定世界距离。
        denominator = 1.0 - delta * self._clip_drag_perspective
        if denominator <= 1e-6:
            return
        delta /= denominator
        mn, mx = (a.copy() for a in bounds)
        value = bounds[side][axis] + delta
        if side == 0:
            mn[axis] = min(value, np.nextafter(mx[axis], np.float32(-np.inf)))
        else:
            mx[axis] = max(value, np.nextafter(mn[axis], np.float32(np.inf)))
        if self.set_clip_box(mn, mx):
            self.clip_dragged.emit(mn, mx)

    def _finish_clip_drag(self):
        if self._clip_drag is not None:
            self._clip_drag = None
            self.setCursor(Qt.ArrowCursor)
            self._schedule_clip_statistics()
            self.clip_drag_finished.emit()

    def cancel_clip_drag(self):
        if self._clip_drag is not None:
            mn, mx = self._clip_drag[3]
            self.set_clip_box(mn, mx)
            self.clip_dragged.emit(mn, mx)
            self._finish_clip_drag()

    def _update_clip_lines(self):
        """裁剪范围线框可视化（12 条棱边），实时反映输入的范围。"""
        if self._clip_box is None and self._clip_handles is not None:
            self.scene.remove(self._clip_handles)
            self._clip_handles = None
        if self._clip_box is None and self._clip_lines is not None:
            try:
                self.scene.remove(self._clip_lines)
            except Exception:
                pass
            self._clip_lines = None
        if self._clip_box is None:
            return
        try:
            mn, mx = self._clip_box
            x0, y0, z0 = float(mn[0]), float(mn[1]), float(mn[2])
            x1, y1, z1 = float(mx[0]), float(mx[1]), float(mx[2])
            corners = np.array([
                [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
            ], dtype=np.float32)
            edges = [(0, 1), (1, 2), (2, 3), (3, 0),
                     (4, 5), (5, 6), (6, 7), (7, 4),
                     (0, 4), (1, 5), (2, 6), (3, 7)]
            positions = np.array([corners[a] for a, b in edges] + [corners[b] for a, b in edges],
                                 dtype=np.float32).reshape(-1, 3)
            # 每条棱由两个端点组成，重排为线段序列
            seg = []
            for a, b in edges:
                seg.append(corners[a])
                seg.append(corners[b])
            positions = np.array(seg, dtype=np.float32)
            if self._clip_lines is not None and self._clip_handles is not None:
                self._clip_lines.material.color = self._tool_color()
                self._clip_handles.material.color = self._tool_color()
                self._clip_lines.geometry.positions.data[:] = positions
                self._clip_lines.geometry.positions.update_full()
                self._clip_handles.geometry.positions.data[:] = self._clip_face_centers()
                self._clip_handles.geometry.positions.update_full()
                return
            line = gfx.Line(
                gfx.Geometry(positions=positions),
                gfx.LineSegmentMaterial(thickness=2, color=self._tool_color()),
            )
            self._clip_lines = line
            self.scene.add(line)
            self._clip_handles = gfx.Points(
                gfx.Geometry(positions=self._clip_face_centers().astype(np.float32)),
                gfx.PointsMaterial(size=12, size_space="screen",
                                   color=self._tool_color(), depth_test=False))
            self._clip_handles.render_order = 20
            self.scene.add(self._clip_handles)
        except Exception:
            self._clip_lines = None

    def _entity_signature(self, entity):
        fields = tuple((name, id(field.data)) for name, field in entity.fields.items())
        return (id(entity.xyz), entity.color_mode, entity.colormap,
                entity.color_field, entity.color_range, fields,
                self.theme if entity.color_mode == "none" else None,
                self._lod_budget_for(entity))

    def _lod_budget_for(self, entity) -> int:
        metrics = self._lod_metrics.get(id(entity))
        if metrics is None or metrics[0] is not entity.xyz:
            bounds = entity.bounds()
            mn, mx = bounds if bounds is not None else (np.zeros(3), np.zeros(3))
            metrics = (entity.xyz, (mn + mx) / 2, float(np.linalg.norm(mx - mn)), bounds)
            self._lod_metrics[id(entity)] = metrics
        _, center, extent = metrics[:3]
        distance = float(np.linalg.norm(np.asarray(self.camera.world.position) - center))
        return lod.lod_budget(entity.point_count, extent, distance)

    def _build_points(self, entity):
        """创建可复用的点云缓冲；裁剪由材质的世界坐标平面完成。"""
        xyz = entity.xyz
        colors = compute_colors(entity)
        budget = self._lod_budget_for(entity)
        self._lod_state[id(entity)] = budget
        sample = lod.select_lod_indices(len(xyz), budget)
        if sample is not None:
            xyz = xyz[sample]
            colors = colors[sample] if colors is not None else None
        self._lod_counts[id(entity)] = int(len(xyz))
        rgba = np.empty((len(xyz), 4), dtype=np.float32)
        if colors is not None:
            rgba[:, :3] = colors
        else:
            rgba[:, :3] = (0.22, 0.25, 0.30) if self.theme == "light" else (0.85, 0.85, 0.88)
        rgba[:, 3] = 1.0
        try:
            geo = gfx.Geometry(positions=xyz, colors=rgba)
            mat = gfx.PointsMaterial(
                size=entity.point_size,
                size_space="screen",
                color_mode="vertex",
                opacity=1.0,
                clipping_planes=self._clip_planes(),
            )
            return gfx.Points(geo, mat)
        except Exception as exc:  # noqa: BLE001
            print(f"[viewport] 创建渲染对象失败: {entity.name} — {exc}")
            return None

    def set_point_size(self):
        """点大小只改材质，不重建几何/不重算颜色。"""
        for obj, entity in zip(self._points_objs, self._point_entities):
            try:
                obj.material.size = entity.point_size
            except Exception:  # noqa: BLE001
                pass
        self._needs_draw = True

    def _update_lod(self):
        """相机远近变化后，若采样预算改变则重建渲染缓冲。"""
        changed = False
        for entity in self.entities:
            if not entity.visible or entity.point_count == 0:
                continue
            budget = self._lod_budget_for(entity)
            if self._lod_state.get(id(entity)) != budget:
                self._lod_state[id(entity)] = budget
                changed = True
        if changed:
            self.refresh(lod_only=True)

    def refresh(self, *, lod_only=False):
        """增量同步实体：数据、颜色与 LOD 不变时复用已有 GPU 对象。"""
        self._needs_draw = True
        if not lod_only:
            self.clear_selection()
            self._lod_metrics.clear()

        live: dict = {}
        visible_any = False
        for entity in self.entities:
            if not entity.visible or entity.xyz is None or len(entity.xyz) == 0:
                continue
            visible_any = True

            signature = self._entity_signature(entity)
            cached = self._render_cache.get(id(entity))
            if cached is not None and cached[0] == signature:
                obj = cached[1]
                try:
                    obj.material.size = entity.point_size
                except Exception:  # noqa: BLE001
                    pass
            else:
                if cached is not None:
                    try:
                        self.scene.remove(cached[1])
                    except Exception:  # noqa: BLE001
                        pass
                obj = self._build_points(entity)
                if obj is None:
                    continue
                self.scene.add(obj)
            live[id(entity)] = (signature, obj, entity)

        for key, (_, obj, _entity) in list(self._render_cache.items()):
            if key not in live:
                try:
                    self.scene.remove(obj)
                except Exception:  # noqa: BLE001
                    pass
        self._render_cache = live
        self._points_objs = [item[1] for item in live.values()]
        self._point_entities = [item[2] for item in live.values()]
        self._lod_state = {key: self._lod_state[key] for key in live if key in self._lod_state}
        self._lod_metrics = {key: self._lod_metrics[key] for key in live
                             if key in self._lod_metrics}

        if lod_only:
            return

        self._refresh_meshes()
        if self._mesh_objs:
            visible_any = True

        self._ensure_helpers()
        self._set_empty_visible(not visible_any)
        self._center_empty_label()

        self._schedule_clip_statistics()

        self._annotation_overlay.refresh_highlight()

    # ---------------- 网格实体 ----------------
    def add_mesh_entity(self, mesh):
        """把三角网格加入场景（与点云共用相机/裁剪前的世界坐标）。"""
        self.mesh_entities.append(mesh)
        self._refresh_meshes()
        self._invalidate()

    def remove_mesh_entity(self, mesh):
        if mesh in self.mesh_entities:
            self.mesh_entities.remove(mesh)
        self._refresh_meshes()
        self._invalidate()

    def _refresh_meshes(self):
        for obj in self._mesh_objs:
            try:
                self.scene.remove(obj)
            except Exception:  # noqa: BLE001
                pass
        self._mesh_objs = []
        for item in self.mesh_entities:
            if not item.visible or item.vertices is None or len(item.vertices) == 0:
                continue
            try:
                mat = gfx.MeshBasicMaterial(
                    color=tuple(item.color), opacity=float(item.opacity),
                    side="both", depth_write=True)
                obj = gfx.Mesh(
                    gfx.Geometry(positions=np.asarray(item.vertices, dtype=np.float32),
                                 indices=np.asarray(item.triangles, dtype=np.int32)),
                    mat)
                obj.render_order = 6
                self.scene.add(obj)
                self._mesh_objs.append(obj)
            except Exception:  # noqa: BLE001
                continue

    # ---------------- 相机 ----------------
    def reset_view(self):
        bounds = [e.bounds() for e in self.entities if e.visible and e.xyz is not None and len(e.xyz)]
        bounds += [m.bounds() for m in self.mesh_entities if m.visible and m.vertices is not None]
        if not bounds:
            return
        mn = np.min([b[0] for b in bounds], axis=0)
        mx = np.max([b[1] for b in bounds], axis=0)
        center = (mn + mx) / 2.0
        extent = float(np.linalg.norm(mx - mn)) or 1.0

        front = np.array([0.0, -1.0, 0.45], dtype=np.float64)
        front /= np.linalg.norm(front)
        dist = extent * 1.15
        pos = center + front * dist

        self._pivot = np.asarray(center, dtype=np.float64)
        self._distance = float(dist)
        self._yaw = np.deg2rad(-90.0)
        self._pitch = np.deg2rad(24.2)
        self._sync_zoom()
        self._apply_camera()

    def _apply_camera(self):
        """沿球面旋转，用连续的上方向穿过俯仰极点。"""
        ce = float(np.cos(self._pitch))
        offset = np.array([np.cos(self._yaw) * ce, np.sin(self._yaw) * ce,
                           np.sin(self._pitch)], dtype=np.float64)
        self.camera.local.position = self._pivot + offset * self._distance
        # 球面的俯仰切向量始终垂直于视线，避免 look_at 在极点翻转。
        self.camera.world.reference_up = (
            -np.cos(self._yaw) * np.sin(self._pitch),
            -np.sin(self._yaw) * np.sin(self._pitch), ce)
        self.camera.look_at(self._pivot)
        self._needs_draw = True
        self.camera_changed.emit()

    def orbit_camera(self, dx: float, dy: float):
        """左键拖拽：水平绕世界 Z 轴、垂直连续旋转完整一周。"""
        position = np.array(self.camera.local.position, copy=True)
        speed = ORBIT_SPEED
        mods = QApplication.keyboardModifiers() if QApplication.instance() else Qt.NoModifier
        if mods & Qt.ShiftModifier:
            speed *= 0.25  # 按住 Shift 精调
        self._yaw = float((self._yaw - float(dx) * speed + np.pi) % (2.0 * np.pi) - np.pi)
        if not self._fixed_view:
            # 抓取式转盘：向下拖动应让场景顶面转入视野（相机抬高、pitch 增大）
            self._pitch = float((self._pitch + float(dy) * speed + np.pi)
                                % (2.0 * np.pi) - np.pi)
        self._apply_camera()
        if self._fixed_view:
            self._pivot += position - np.asarray(self.camera.local.position)
            self._apply_camera()

    def set_fixed_view(self, enabled: bool):
        self._sync_zoom()
        self._fixed_view = bool(enabled)
        self.navigation_changed.emit(self._fixed_view)

    def _walk_direction(self):
        sign = 1.0 if np.cos(self._pitch) >= 0 else -1.0
        return -sign * np.array([np.cos(self._yaw), np.sin(self._yaw), 0.0])

    def _step_walk_animation(self, dt):
        if not self._fixed_view or (self._walk_remaining == 0 and self._walk_velocity == 0):
            return
        error, velocity = self._walk_remaining, self._walk_velocity
        omega = 18.0
        decay = float(np.exp(-omega * dt))
        impulse = (velocity + omega * error) * dt
        remaining = (error + impulse) * decay
        self._walk_velocity = (velocity - omega * impulse) * decay
        tolerance = max(self._pan_step() * 1e-5, 1e-9)
        if abs(remaining) < tolerance and abs(self._walk_velocity) < tolerance:
            remaining = 0.0
            self._walk_velocity = 0.0
        self._walk_remaining = remaining
        self._pivot += self._walk_direction() * (error - remaining)
        self._apply_camera()
        if remaining == 0:
            self._update_lod()

    def _pan_pixels(self, dx: float, dy: float):
        view = np.asarray(self.camera.view_matrix, dtype=np.float64)
        rot = np.linalg.inv(view)[:3, :3]
        right = rot[:, 0]
        up = rot[:, 1]
        step = max(self._distance, 1e-6) * 0.0016
        delta = (-float(dx) * right + float(dy) * up) * step
        if self._fixed_view:
            delta[2] = 0
        self._pivot = self._pivot + delta
        self._apply_camera()

    def zoom_camera(self, notches: float):
        """滚轮缩放：只更新目标距离，真正的距离由渲染循环阻尼逼近。"""
        if not np.isfinite(notches) or abs(notches) < 1e-9:
            return
        if self._fixed_view:
            self._walk_remaining += float(np.clip(notches, -100, 100)) * self._pan_step() * 5
            return
        base = self._zoom_target if self._zoom_animating else self._distance
        target_log = np.log(base) + np.log(ZOOM_STEP) * float(notches)
        target_log = np.clip(target_log, np.log(self._distance) - np.log(4),
                             np.log(self._distance) + np.log(4))
        self._zoom_target = float(np.exp(np.clip(target_log, np.log(1e-3), np.log(1e12))))
        self._zoom_animating = abs(self._zoom_target - self._distance) > 1e-9
        self._zoom_lod_pending = self._zoom_animating
        self._needs_draw = True

    def _step_zoom_animation(self, dt: float):
        """在对数距离上使用临界阻尼弹簧，连续滚动时保持速度连续。"""
        if not self._zoom_animating:
            return
        target = self._zoom_target
        dt = max(float(dt), 0.0)
        omega = 24.0
        error = float(np.log(self._distance / target))
        velocity = self._zoom_velocity
        decay = float(np.exp(-omega * dt))
        impulse = (velocity + omega * error) * dt
        error = (error + impulse) * decay
        self._zoom_velocity = (velocity - omega * impulse) * decay
        distance = target * float(np.exp(error))
        if abs(error) < 1e-5 and abs(self._zoom_velocity) < 1e-4:
            distance = target
            self._zoom_animating = False
            self._zoom_velocity = 0.0
        self._distance = float(distance)
        self._apply_camera()
        if not self._zoom_animating and self._zoom_lod_pending:
            self._zoom_lod_pending = False
            self._update_lod()

    def _sync_zoom(self):
        """相机距离被直接改写后，让缩放动画从当前值重新开始。"""
        self._zoom_target = float(self._distance)
        self._zoom_velocity = 0.0
        self._zoom_animating = False
        self._zoom_lod_pending = False
        self._walk_remaining = 0.0
        self._walk_velocity = 0.0

    def _set_pivot(self, point):
        point = np.asarray(point, dtype=np.float64)
        pos = np.asarray(self.camera.local.position, dtype=np.float64)
        offset = pos - point
        length = float(np.linalg.norm(offset))
        if length < 1e-6:
            return
        self._distance = length
        pitch = float(np.arcsin(np.clip(offset[2] / length, -1.0, 1.0)))
        yaw = float(np.arctan2(offset[1], offset[0]))
        if np.cos(self._pitch) < 0:
            pitch = np.pi - pitch
            yaw += np.pi
        self._pitch = pitch
        self._yaw = yaw
        self._pivot = point
        self._sync_zoom()
        self._apply_camera()

    def focus_entity(self, entity):
        """聚焦到单个实体（工程树右键/双击）。"""
        b = entity.bounds()
        if b is None:
            return
        mn, mx = b
        center = (mn + mx) / 2.0
        extent = float(np.linalg.norm(mx - mn)) or 1.0

        front = np.array([0.0, -1.0, 0.45], dtype=np.float64)
        front /= np.linalg.norm(front)
        pos = center + front * extent * 1.15

        self._pivot = np.asarray(center, dtype=np.float64)
        self._distance = float(extent * 1.15)
        self._yaw = np.deg2rad(-90.0)
        self._pitch = np.deg2rad(24.2)
        self._sync_zoom()
        self._apply_camera()

    def camera_state(self) -> dict:
        """可序列化相机状态（工程保存用）。"""
        return {
            "position": [float(v) for v in self.camera.local.position],
            "rotation": [float(v) for v in self.camera.local.rotation],
            "pivot": [float(v) for v in self._pivot],
            "yaw": float(self._yaw),
            "pitch": float(self._pitch),
            "fov": float(self.camera.fov),
            "fixed_view": self._fixed_view,
        }

    def set_camera_state(self, state: dict) -> bool:
        try:
            self.camera.local.position = tuple(float(v) for v in state["position"])
            self.camera.local.rotation = tuple(float(v) for v in state["rotation"])
            if "fov" in state:
                self.camera.fov = float(state["fov"])
            pos = np.asarray(self.camera.local.position, dtype=np.float64)
            pivot = state.get("pivot")
            if pivot is None:
                center = self._scene_center()
                pivot = center if center is not None else pos
            self._pivot = np.asarray(pivot, dtype=np.float64)
            offset = pos - self._pivot
            self._distance = float(max(np.linalg.norm(offset), 1e-6))
            self._pitch = float(state.get("pitch", np.arcsin(
                np.clip(offset[2] / self._distance, -1.0, 1.0))))
            self._yaw = float(state.get("yaw", np.arctan2(offset[1], offset[0])))
            self._sync_zoom()
            self.set_fixed_view(state.get("fixed_view", False))
            self._needs_draw = True
            return True
        except Exception:  # noqa: BLE001
            return False

    def _pan_step(self) -> float:
        return max(self._scene_extent(), 1e-6) * 0.01

    def pan_camera(self, dx=0.0, dy=0.0, dz=0.0):
        """沿相机局部坐标平移（WASD/QE，沿用原脚本习惯）。"""
        try:
            pos = np.asarray(self.camera.local.position, dtype=np.float64)
            target = np.asarray(self._pivot, dtype=np.float64)
            forward = target - pos
            if np.linalg.norm(forward) < 1e-6:
                forward = np.array([0.0, -1.0, 0.0])
            forward /= np.linalg.norm(forward)
            rotation = np.asarray(self.camera.world.matrix)[:3, :3]
            right = rotation[:, 0]
            upv = rotation[:, 1]

            step = self._pan_step()
            delta = (dx * right + dy * upv + dz * forward) * step
            if self._fixed_view:
                delta = (dx * right + dy * upv + dz * self._walk_direction()) * step
                delta[2] = 0
            self._pivot = target + delta
            self.camera.local.position = pos + delta
            self._needs_draw = True
        except Exception:
            pass

    # ---------------- P4：测量与选点 ----------------
    def set_interaction_mode(self, mode):
        mode = mode if mode in ("measure", "select", "box") else None
        if mode == self._mode:
            return
        # 用控制器的 enabled 开关暂停相机交互（remove_event_handler 需按事件类型移除，
        # 直接传 controller 是无效的，会导致拖拽时相机仍在旋转）
        if self.controller is not None:
            try:
                self.controller.enabled = mode is None
            except Exception:
                pass
        self._mode = mode
        self._sel_mode = "box" if mode == "box" else None
        if self._sel_mode is None:
            self._cancel_selection_drag()
        self.setCursor(Qt.CrossCursor if mode else Qt.ArrowCursor)
        if mode != "measure":
            self._clear_measurement()
        if mode != "select":
            self.clear_selection()
        if mode in ("box", "lasso"):
            label = "框选" if mode == "box" else "套索"
            self._show_point_info(f"{label}模式：拖拽框选/圈选点云（Esc 退出）")
        elif mode == "measure":
            self.measure_label.setText("测量模式：连续点击点云加点（Esc 清除）")
            self.measure_label.adjustSize()
            self.measure_label.move(max(0, (self.width() - self.measure_label.width()) // 2),
                                    self.height() - 92)
            self.measure_label.show()
        elif mode == "select":
            self._show_point_info("选点模式：单击一个点查看信息（Esc 退出）")
        self.mode_changed.emit(mode or "")

    def _cancel_selection_drag(self):
        self._sel_dragging = False
        self._sel_start = None
        self._sel_points = []
        if getattr(self, "_sel_rubber", None) is not None:
            self._sel_rubber.clear_shape()

    def _finish_selection(self, x1: float, y1: float):
        """按当前框/套索计算选中点，返回 [(entity, indices), ...]。"""
        mode = self._sel_mode
        if mode is None:
            return []
        if mode == "box":
            x0, y0 = self._sel_start
            lo_x, hi_x = min(x0, x1), max(x0, x1)
            lo_y, hi_y = min(y0, y1), max(y0, y1)
        else:
            poly = np.asarray(self._sel_points + [(x1, y1)], dtype=np.float64)
            if len(poly) < 3:
                return []
            inside = _points_in_polygon

        result = []
        for entity in list(self.entities):
            if not getattr(entity, "visible", True) or entity.xyz is None or not len(entity.xyz):
                continue
            sx, sy, _depth, ok = self._project_points(entity.xyz)
            if mode == "box":
                hit = ok & (sx >= lo_x) & (sx <= hi_x) & (sy >= lo_y) & (sy <= hi_y)
            else:
                hit = ok & inside(sx, sy, poly)
            indices = np.nonzero(hit)[0]
            if len(indices):
                result.append((entity, indices))
        return result

    def _apply_selection_highlight(self, selection):
        """把选中点画成亮橙色覆盖层（不影响原实体）。"""
        self._clear_selection_highlight()
        if not selection:
            return
        parts = []
        for entity, indices in selection:
            parts.append(np.asarray(entity.xyz[indices], dtype=np.float32))
        xyz = np.concatenate(parts, axis=0)
        if not len(xyz):
            return
        size = max((getattr(e, "point_size", 2.0) for e, _ in selection), default=2.0) * 1.6
        obj = gfx.Points(
            gfx.Geometry(positions=xyz),
            gfx.PointsMaterial(size=size, size_space="screen",
                               color=self._tool_color() if self.theme == "light" else (1.0, 0.56, 0.10, 1.0),
                               depth_test=False),
        )
        obj.render_order = 15
        self.scene.add(obj)
        self._sel_highlight = obj
        self._needs_draw = True

    def _clear_selection_highlight(self):
        if self._sel_highlight is not None:
            try:
                self.scene.remove(self._sel_highlight)
            except Exception:
                pass
            self._sel_highlight = None
            self._needs_draw = True

    def clear_selection_runtime(self):
        """清除框选高亮与橡皮筋（选择数据由主窗口持有）。"""
        self._cancel_selection_drag()
        self._clear_selection_highlight()
        try:
            self.selection_label.hide()
        except Exception:
            pass


    def _projection_key(self):
        view = np.asarray(self.camera.view_matrix, dtype=np.float64)
        proj = np.asarray(self.camera.projection_matrix, dtype=np.float64)
        clip = None if self._clip_box is None else tuple(
            float(v) for arr in self._clip_box for v in np.asarray(arr, dtype=np.float64).ravel())
        return (tuple(view.ravel()), tuple(proj.ravel()),
                int(self.width()), int(self.height()), clip)

    def _project_points(self, xyz):
        """世界坐标 → 屏幕坐标 + NDC 深度（缓存到相机参数变化为止）。"""
        key = self._projection_key()
        cache_key = (id(xyz), len(xyz))
        cached = self._proj_cache.get(cache_key)
        if cached is not None and cached[0] == key:
            return cached[1], cached[2], cached[3], cached[4]

        view = np.asarray(self.camera.view_matrix, dtype=np.float64)
        proj = np.asarray(self.camera.projection_matrix, dtype=np.float64)
        m = proj @ view
        w_px = max(int(self.width()), 1)
        h_px = max(int(self.height()), 1)
        n = len(xyz)
        sx = np.empty(n, dtype=np.float32)
        sy = np.empty(n, dtype=np.float32)
        depth = np.empty(n, dtype=np.float32)
        ok = np.zeros(n, dtype=bool)
        chunk = 2_000_000
        for start in range(0, n, chunk):
            stop = min(start + chunk, n)
            p = np.asarray(xyz[start:stop], dtype=np.float64)
            clip = p @ m[:3, :3].T + m[:3, 3]
            cw = p @ m[3, :3] + m[3, 3]
            valid = cw > 1e-9
            safe_w = np.where(valid, cw, 1.0)
            d = clip[:, 2] / safe_w
            valid &= (d >= 0.0) & (d <= 1.0)
            if self._clip_box is not None:
                mn, mx = self._clip_box
                valid &= np.all((p >= mn) & (p <= mx), axis=1)
            ndc_x = clip[:, 0] / safe_w
            ndc_y = clip[:, 1] / safe_w
            sx[start:stop] = np.where(valid, (ndc_x * 0.5 + 0.5) * w_px, np.inf)
            sy[start:stop] = np.where(valid, (1.0 - (ndc_y * 0.5 + 0.5)) * h_px, np.inf)
            depth[start:stop] = d.astype(np.float32)
            ok[start:stop] = valid

        if len(self._proj_cache) >= 4:
            self._proj_cache.pop(next(iter(self._proj_cache)))
        self._proj_cache[cache_key] = (key, sx, sy, depth, ok)
        return sx, sy, depth, ok

    def _pick_point(self, x, y, radius=18.0):
        """拾取点击半径内最靠前的可见点（2px 邻域深度测试，近似遮挡）。"""
        r2 = radius * radius
        near_r2 = 2.5 * 2.5
        layers = []
        front = np.inf
        for entity in self.entities:
            if not entity.visible or entity.xyz is None or len(entity.xyz) == 0:
                continue
            sx, sy, depth, ok = self._project_points(entity.xyz)
            d2 = (sx - x) ** 2 + (sy - y) ** 2
            d2[~ok] = np.inf
            reachable = d2 <= r2
            if not reachable.any():
                continue
            near = reachable & (d2 <= near_r2)
            if near.any():
                front = min(front, float(depth[near].min()))
            layers.append((d2, depth, ok, entity))

        if not layers:
            return None
        best = None
        for d2, depth, ok, entity in layers:
            visible = ok & np.isfinite(d2) & (depth <= front + 1e-6)
            if not visible.any():
                continue
            d2v = np.where(visible, d2, np.inf)
            idx = int(np.argmin(d2v))
            if not np.isfinite(d2v[idx]):
                continue
            if best is None or d2v[idx] < best[0]:
                best = (float(d2v[idx]), entity, idx, entity.xyz[idx].copy())
        return best

    def _on_measure_click(self, x, y):
        picked = self._pick_point(x, y)
        if picked is None:
            self.measure_label.setText("未拾取到点，请点击点云（Esc 清除）")
            self.measure_label.adjustSize()
            self.measure_label.move(int(x) + 14, int(y) + 10)
            self.measure_label.show()
            return
        _, _entity, _idx, pos = picked
        pos = np.asarray(pos, dtype=np.float32)
        if self._measure_pts and np.linalg.norm(self._measure_pts[-1] - pos) < 1e-6:
            return  # 同一位置重复点击忽略
        self._measure_pts.append(pos)
        self._draw_measurement_polyline()
        summary = self.measurement_summary()
        text = f"{len(self._measure_pts)} 点 · 折线 {summary['length']:.3f} m"
        if summary["area"] > 0:
            text += f" · 面积 {summary['area']:.3f} m²"
        self.measure_label.setText(text)
        self.measure_label.adjustSize()
        self.measure_label.move(int(x) + 14, int(y) + 10)
        self.measure_label.show()
        self.measured.emit(summary["length"])

    def measurement_summary(self) -> dict:
        """当前测量折线的点、分段、累计长度与多边形面积（3D Newell）。"""
        pts = [np.asarray(p, dtype=np.float64) for p in self._measure_pts]
        if not pts:
            return {"points": np.zeros((0, 3)), "length": 0.0, "area": 0.0, "segments": []}
        segments = [float(np.linalg.norm(pts[i + 1] - pts[i])) for i in range(len(pts) - 1)]
        area = 0.0
        if len(pts) >= 3:
            normal = np.zeros(3, dtype=np.float64)
            for i in range(len(pts)):
                normal += np.cross(pts[i], pts[(i + 1) % len(pts)])
            area = float(np.linalg.norm(normal) / 2.0)
        return {"points": np.asarray(pts), "length": float(sum(segments)),
                "area": area, "segments": segments}

    def clear_measurement(self):
        self._clear_measurement()

    def _draw_measurement_polyline(self):
        self._needs_draw = True
        self._clear_measurement_objs()
        pts = np.asarray(self._measure_pts, dtype=np.float32)
        if len(pts) >= 2:
            # ≥3 点时把折线首尾相连，形成面积边界；填充半透明橙色面，直观标出被测区域。
            outline = np.vstack([pts, pts[:1]]) if len(pts) >= 3 else pts
            line = gfx.Line(
                gfx.Geometry(positions=outline),
                gfx.LineMaterial(thickness=3, color=self._tool_color()),
            )
            self.scene.add(line)
            self._measure_objs.append(line)
        if len(pts) >= 3:
            centroid = pts.astype(np.float64).mean(axis=0)
            triangles = np.array(
                [[0, i, i + 1] for i in range(1, len(pts) - 1)], dtype=np.int32)
            fill = gfx.Mesh(
                gfx.Geometry(positions=pts, indices=triangles),
                gfx.MeshBasicMaterial(color=(1.0, 0.62, 0.18), opacity=0.22,
                                      side="both", depth_write=False),
            )
            fill.render_order = 18
            self.scene.add(fill)
            self._measure_objs.append(fill)
            self._measure_objs.append(self._point_marker(centroid))
        for p in pts:
            self._measure_objs.append(self._point_marker(p))


    def _point_marker(self, position):
        marker = gfx.Points(
            gfx.Geometry(positions=np.asarray(position, dtype=np.float32).reshape(1, 3)),
            gfx.PointsMaterial(size=10, size_space="screen", color=self._tool_color(),
                               depth_test=False),
        )
        marker.render_order = 20
        self.scene.add(marker)
        return marker

    def _clear_measurement_objs(self):
        for obj in self._measure_objs:
            try:
                self.scene.remove(obj)
            except Exception:
                pass
        self._measure_objs = []
        self._needs_draw = True

    def _clear_measurement(self):
        self._clear_measurement_objs()
        self._measure_pts = []
        try:
            self.measure_label.hide()
        except Exception:
            pass

    def _show_point_info(self, text):
        self.selection_label.setText(text)
        self.selection_label.setMaximumWidth(max(100, self.width() - 24))
        self.selection_label.adjustSize()
        self.selection_label.move(12, 12)
        self.selection_label.show()
        self.selection_label.raise_()

    def _on_point_click(self, x, y):
        self.clear_selection()
        picked = self._pick_point(x, y)
        if picked is None:
            self._show_point_info("未拾取到点，请点击点云（Esc 退出）")
            return
        _, entity, index, position = picked
        self._selected_point = (entity, index)
        self._selection_marker = self._point_marker(position)
        lines = [f"{entity.name} · 点索引 {index}（从 0 开始）",
                 f"X: {position[0]:.6f} m",
                 f"Y: {position[1]:.6f} m",
                 f"Z: {position[2]:.6f} m"]
        names = {"intensity": "强度", "rgb": "RGB", "labels": "聚类标签"}
        for name, field in entity.fields.items():
            value = np.asarray(field.data[index])
            text = np.array2string(value, precision=6, separator=", ", threshold=12)
            lines.append(f"{names.get(name, name)}: {text}")
        if entity.is_preview:
            lines.append("当前为预览点，索引属于预览数据")
        lines.append("单击其他点切换 · Esc 退出")
        self._show_point_info("\n".join(lines))

    def clear_selection(self):
        self._selected_point = None
        if self._selection_marker is not None:
            self.scene.remove(self._selection_marker)
            self._selection_marker = None
        try:
            self.selection_label.hide()
        except Exception:
            pass

    # ---------------- 事件 ----------------
    def eventFilter(self, watched, event):
        if watched is self._subwidget:
            kind = event.type()
            if kind in (QEvent.MouseButtonPress, QEvent.MouseMove,
                        QEvent.MouseButtonRelease, QEvent.Wheel):
                self._needs_draw = True
            if kind == QEvent.MouseButtonPress:
                self.setFocus()
            if kind in (QEvent.MouseButtonPress, QEvent.MouseMove,
                        QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick):
                position = watched.mapTo(self, event.position())
                forwarded = QMouseEvent(kind, position, event.globalPosition(),
                                        event.button(), event.buttons(), event.modifiers())
                handlers = {QEvent.MouseButtonPress: self.mousePressEvent,
                            QEvent.MouseMove: self.mouseMoveEvent,
                            QEvent.MouseButtonRelease: self.mouseReleaseEvent,
                            QEvent.MouseButtonDblClick: self.mouseDoubleClickEvent}
                handlers[kind](forwarded)
                return True
            if kind == QEvent.Wheel:
                self.wheelEvent(event)
                return True
            if kind == QEvent.KeyPress and not (event.modifiers() & (
                    Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
                if event.key() in (
                        Qt.Key_Escape, Qt.Key_Delete, Qt.Key_Backspace,
                        Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down,
                        Qt.Key_R, Qt.Key_H, Qt.Key_C, Qt.Key_B,
                        Qt.Key_Plus, Qt.Key_Equal, Qt.Key_Minus, Qt.Key_Underscore,
                        Qt.Key_W, Qt.Key_S, Qt.Key_A, Qt.Key_D, Qt.Key_Q, Qt.Key_E):
                    self.keyPressEvent(event)
                    return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            w, h = self.width(), self.height()
            self.camera.aspect = (w / h) if h else 16 / 9
            if hasattr(self.camera, "set_view_size"):
                self.camera.set_view_size(w, h)
        except Exception:
            pass
        self._center_empty_label()
        self._layout_overlays()
        try:
            self._sel_rubber.setGeometry(self.rect())
        except Exception:
            pass
        self._needs_draw = True

    def _center_empty_label(self):
        label = getattr(self, "empty_label", None)
        if label is not None:
            try:
                import shiboken6
                if shiboken6.isValid(label):
                    label.resize(self.width(), 60)
                    label.move(0, self.height() // 2 - 30)
            except Exception:
                pass

    def mousePressEvent(self, event):
        self.setFocus()
        if (self.box_input_enabled and event.button() == Qt.LeftButton
                and event.modifiers() & Qt.ControlModifier):
            self.box_creation_requested.emit()
        if self._sel_mode is not None and event.button() == Qt.LeftButton:
            pos = event.position()
            self._sel_dragging = True
            self._sel_start = (float(pos.x()), float(pos.y()))
            self._sel_points = [self._sel_start]
            self._sel_rubber.set_shape(("box", (pos.x(), pos.y(), pos.x(), pos.y())))
            return
        if self._mode == "measure" and event.button() == Qt.LeftButton:
            pos = event.position()
            self._on_measure_click(pos.x(), pos.y())
            return
        if self._mode == "select" and event.button() == Qt.LeftButton:
            pos = event.position()
            self._on_point_click(pos.x(), pos.y())
            return
        pos = event.position()
        if event.button() == Qt.LeftButton:
            target = self._annotation_at(pos.x(), pos.y()) if self.box_input_enabled else None
            if target is not None:
                self.annotation_selected.emit(target)
                return
            if self._begin_clip_drag(float(pos.x()), float(pos.y())):
                return
            self._drag = ("orbit", float(pos.x()), float(pos.y()))
            return
        if event.button() in (Qt.RightButton, Qt.MiddleButton):
            self._drag = ("pan", float(pos.x()), float(pos.y()))
            return
        super().mousePressEvent(event)

    def _annotation_at(self, x, y):
        """Pick a visible cuboid face; prefer the nearest overlapping box."""
        candidates = []
        faces = ((0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4),
                 (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))
        for box in self._annotation_overlay.boxes:
            sx, sy, depth, ok = self._project_points(annotation_corners(box))
            for face in faces:
                indices = np.asarray(face)
                if not ok[indices].all():
                    continue
                polygon = np.column_stack((sx[indices], sy[indices]))
                if _points_in_polygon(np.array([x]), np.array([y]), polygon)[0]:
                    candidates.append((float(depth[indices].mean()), box["id"]))
                    break
        return min(candidates)[1] if candidates else None

    def mouseMoveEvent(self, event):
        pos = event.position()
        x, y = float(pos.x()), float(pos.y())
        if self._clip_drag is not None:
            self._move_clip_drag(x, y)
            return
        if self._sel_dragging:
            x0, y0 = self._sel_start
            self._sel_rubber.set_shape(("box", (x0, y0, x, y)))
            return
        if self._drag is not None:
            mode, lx, ly = self._drag
            dx, dy = x - lx, y - ly
            self._drag = (mode, x, y)
            if event.modifiers() & Qt.AltModifier:
                dx, dy = dx * 0.2, dy * 0.2
            if mode == "orbit":
                self.orbit_camera(dx, dy)
            else:
                self._pan_pixels(dx, dy)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._clip_drag is not None and event.button() == Qt.LeftButton:
            self._move_clip_drag(event.position().x(), event.position().y())
            self._finish_clip_drag()
            self._update_lod()
            return
        if self._sel_dragging:
            self._sel_dragging = False
            pos = event.position()
            selection = self._finish_selection(float(pos.x()), float(pos.y()))
            self._apply_selection_highlight(selection)
            self._sel_rubber.clear_shape()
            count = int(sum(len(idx) for _, idx in selection))
            if count:
                self._show_point_info(
                    f"已选 {count:,} 个点（{len(selection)} 片点云）· Esc 退出")
                self.selection_changed.emit(selection)
            else:
                self._show_point_info("框选范围内没有点 · 重新拖拽或 Esc 退出")
                self.selection_changed.emit([])
            super().mouseReleaseEvent(event)
            self._update_lod()
            return
        self._drag = None
        super().mouseReleaseEvent(event)
        self._update_lod()

    def mouseDoubleClickEvent(self, event):
        if self._mode is not None or self._fixed_view or self._clip_drag is not None:
            super().mouseDoubleClickEvent(event)
            return
        pos = event.position()
        picked = self._pick_point(pos.x(), pos.y())
        if picked is not None:
            _, entity, index, position = picked
            self._set_pivot(position)
            self._show_point_info(
                f"旋转中心已设为 {entity.name} 的点 {index}｜"
                f"X {position[0]:.3f}  Y {position[1]:.3f}  Z {position[2]:.3f}")
        else:
            center = self._scene_center()
            if center is not None:
                self._set_pivot(center)
        self._update_lod()

    def wheelEvent(self, event):
        if self._clip_drag is not None:
            event.accept()
            return
        pixels = event.pixelDelta().y()
        notches = pixels / 50.0 if pixels else event.angleDelta().y() / 120.0
        if event.modifiers() & Qt.AltModifier:
            notches *= 0.2
        self.zoom_camera(notches)
        event.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        self.files_dropped.emit([u.toLocalFile() for u in event.mimeData().urls()])

    def keyPressEvent(self, event):
        key = event.key()
        if self._clip_drag is not None:
            if key == Qt.Key_Escape:
                self.cancel_clip_drag()
            event.accept()
            return
        if key == Qt.Key_Escape:
            if self._mode is not None:
                self.set_interaction_mode(None)
            else:
                self.clear_selection()
                self._clear_measurement()
            return
        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            return
        if key == Qt.Key_Right or key == Qt.Key_Down:
            self.key_next_file.emit()
        elif key == Qt.Key_Left or key == Qt.Key_Up:
            self.key_prev_file.emit()
        elif key == Qt.Key_R:
            self.key_reset_view.emit()
        elif key == Qt.Key_H:
            self.key_show_help.emit()
        elif key == Qt.Key_C:
            self.key_cycle_color.emit()
        elif key == Qt.Key_B:
            self.key_toggle_clipbox.emit()
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self.key_point_size.emit(+0.5)
        elif key in (Qt.Key_Minus, Qt.Key_Underscore):
            self.key_point_size.emit(-0.5)
        elif key == Qt.Key_W:
            self.pan_camera(dy=+1.0)
        elif key == Qt.Key_S:
            self.pan_camera(dy=-1.0)
        elif key == Qt.Key_A:
            self.pan_camera(dx=-1.0)
        elif key == Qt.Key_D:
            self.pan_camera(dx=+1.0)
        elif key == Qt.Key_Q:
            self.pan_camera(dz=-1.0)
        elif key == Qt.Key_E:
            self.pan_camera(dz=+1.0)
        else:
            super().keyPressEvent(event)
