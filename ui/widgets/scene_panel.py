"""Compact source tree; selection, cloud visibility and disk operations stay separate."""
from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import QEvent, QItemSelectionModel, QMimeData, QPoint, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDrag
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
    QPushButton, QRubberBand, QStyle, QStyleOptionViewItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QHBoxLayout, QWidget,
)

from ui.theme import get_theme
from ui.theme.icons import make_icon

ENTITY_ROLE = Qt.UserRole
PATH_ROLE = Qt.UserRole + 1
SOURCE_PATH_ROLE = Qt.UserRole + 2
ORDER_ROLE = Qt.UserRole + 3
RECORDING_ROLE = Qt.UserRole + 4
GROUP_ROLE = Qt.UserRole + 5
SEARCH_ROLE = Qt.UserRole + 6
KIND_ROLE = Qt.UserRole + 7
ROW_HEIGHT = 30


def _absolute(path):
    return os.path.abspath(os.path.expanduser(os.fspath(path)))


def _within(path, parent):
    try:
        return os.path.commonpath([path, parent]) == parent
    except ValueError:
        return False


def _minimal_paths(paths):
    result = []
    for path in sorted(set(paths), key=lambda p: (len(Path(p).parts), p.casefold())):
        if not any(_within(path, parent) for parent in result):
            result.append(path)
    return result


def _natural(text):
    return [(1, int(part)) if part.isdigit() else (0, part.casefold())
            for part in re.split(r"(\d+)", text)]


def _filter_visible(item):
    """Collapsed rows still belong to an action; filtered-out rows do not."""
    while item is not None:
        if item.isHidden():
            return False
        item = item.parent()
    return True


class SceneItem(QTreeWidgetItem):
    def __lt__(self, other):
        return (-(self.data(0, ORDER_ROLE) or 0), not bool(self.data(0, PATH_ROLE)),
                _natural(self.text(0))) < (
            -(other.data(0, ORDER_ROLE) or 0), not bool(other.data(0, PATH_ROLE)),
            _natural(other.text(0)))


class SceneTree(QTreeWidget):
    """Explorer gestures without Qt's automatic, premature model moves."""
    browse_requested = Signal(object)
    paths_dropped = Signal(object, object)
    recording_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setColumnCount(2)
        self.setHeaderLabels(["名称", "状态"])
        self.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.setColumnWidth(1, 94)
        self.setIconSize(QSize(16, 16))
        self.setIndentation(14)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.ElideMiddle)
        self.setFrameShape(QTreeWidget.NoFrame)
        self.setExpandsOnDoubleClick(False)
        self.setSortingEnabled(True)
        self.sortItems(0, Qt.AscendingOrder)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.verticalScrollBar().installEventFilter(self)
        self.setToolTip("点击文件浏览；勾选可叠加显示。拖动名称选择分组或移动原文件；在行尾或空白处拖动框选。右键删除可选择仅移出工作区或移到回收站。")
        self._press = None
        self._pressed_item = None
        self._check_press = False
        self._source_press = False
        self._band_active = False
        self._band = QRubberBand(QRubberBand.Rectangle, self.viewport())
        self._prior_selection = []
        self._press_modifiers = Qt.NoModifier
        self._skip_release = False
        self._internal_drag_paths = None
        self._drop_band = QRubberBand(QRubberBand.Rectangle, self.viewport())
        self.filter_active = False

    def _check_rect(self, item):
        option = QStyleOptionViewItem()
        option.initFrom(self)
        index = self.indexFromItem(item, 0)
        self.itemDelegate().initStyleOption(option, index)
        option.rect = self.visualRect(index)
        return self.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, option, self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setColumnHidden(1, self.width() < 270)

    def _source_hit(self, item, pos):
        if item is None:
            return False
        rect = self.visualRect(self.indexFromItem(item, 0))
        width = self.fontMetrics().horizontalAdvance(item.text(0)) + 12
        if item.data(0, ENTITY_ROLE) is not None:
            width += self._check_rect(item).width() + 4
        if not item.icon(0).isNull():
            width += self.iconSize().width() + 4
        return rect.left() <= pos.x() <= min(rect.right(), rect.left() + width)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.RightButton:
            if item is not None and not item.isSelected():
                self.setCurrentItem(item, 0, (QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows))
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.MouseFocusReason)
        self._press = event.position().toPoint()
        self._pressed_item = item
        self._press_modifiers = event.modifiers()
        self._prior_selection = self.selectedItems()
        self._band_active = False
        self._internal_drag_paths = None
        self._drop_band.hide()
        self._check_press = (item is not None and item.data(0, ENTITY_ROLE) is not None
                             and self._check_rect(item).contains(self._press))
        self._source_press = (not self._check_press and self._source_hit(item, self._press)
                              and not event.modifiers() & Qt.AltModifier)
        event.accept()

    def _visible_rows(self):
        item = self.itemAt(QPoint(1, 0))
        if item is None and self.topLevelItemCount():
            item = next((self.topLevelItem(index) for index in range(self.topLevelItemCount())
                         if not self.topLevelItem(index).isHidden()), None)
        while item is not None:
            rect = self.visualItemRect(item)
            if rect.top() > self.viewport().height():
                break
            if rect.bottom() >= 0 and _filter_visible(item):
                yield item, QRect(0, rect.y(), self.viewport().width(), rect.height())
            item = self.itemBelow(item)

    def mouseMoveEvent(self, event):
        if self._press is None or not event.buttons() & Qt.LeftButton or self._check_press:
            event.accept()
            return
        point = event.position().toPoint()
        if (point - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        if self._source_press:
            item = self._pressed_item
            if item is not None and not item.isSelected():
                self.setCurrentItem(item, 0, (QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows))
            if self._internal_drag_paths is None:
                self._internal_drag_paths = self._selected_source_paths()
            hovered = self.itemAt(point)
            if hovered is not None and hovered.data(0, PATH_ROLE):
                rect = self.visualItemRect(hovered)
                self._drop_band.setGeometry(QRect(0, rect.y(), self.viewport().width(), rect.height()))
                self._drop_band.show()
            else:
                self._drop_band.hide()
            event.accept()
            return
        self._band_active = True
        rect = QRect(self._press, point).normalized()
        self._band.setGeometry(rect)
        self._band.show()
        selected = [item for item, row in self._visible_rows() if row.intersects(rect)]
        if self._press_modifiers & (Qt.ControlModifier | Qt.MetaModifier):
            selected = list(dict.fromkeys([*self._prior_selection, *selected]))
        self.clearSelection()
        for item in selected:
            item.setSelected(True)
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton:
            event.accept()
            return
        if self._skip_release:
            self._skip_release = False
            self._press = None
            return
        if self._press is None:
            event.accept()
            return
        item = self._pressed_item
        if self._internal_drag_paths is not None:
            paths = self._internal_drag_paths
            target_item = self.itemAt(event.position().toPoint())
            target = target_item.data(0, PATH_ROLE) if target_item else None
            self._internal_drag_paths = None
            self._drop_band.hide()
            if paths:
                self.paths_dropped.emit(paths, target)
        elif self._band_active:
            self._band.hide()
        elif self._check_press:
            if item is self.itemAt(event.position().toPoint()):
                item.setCheckState(0, Qt.Unchecked if item.checkState(0) == Qt.Checked
                                   else Qt.Checked)
        elif item is not None:
            flags = (QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            if self._press_modifiers & (Qt.ControlModifier | Qt.MetaModifier):
                flags = QItemSelectionModel.Toggle | QItemSelectionModel.Rows
            self.setCurrentItem(item, 0, flags)
            if item.data(0, RECORDING_ROLE) is not None:
                self.recording_requested.emit(item.data(0, RECORDING_ROLE))
            elif item.data(0, PATH_ROLE):
                item.setExpanded(not item.isExpanded())
            elif flags == (QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows):
                self.browse_requested.emit(item.data(0, ENTITY_ROLE))
        elif not self._press_modifiers & (Qt.ControlModifier | Qt.MetaModifier):
            self.clearSelection()
        self._press = None
        self._band_active = False
        event.accept()

    def mouseDoubleClickEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.LeftButton and item is not None:
            self._skip_release = True
            if item.data(0, ENTITY_ROLE) is not None or item.data(0, RECORDING_ROLE) is not None:
                self.itemDoubleClicked.emit(item, 0)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            event.accept()
            return
        super().keyPressEvent(event)
        if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End):
            item = self.currentItem()
            if item is not None and item.data(0, ENTITY_ROLE) is not None:
                self.browse_requested.emit(item.data(0, ENTITY_ROLE))
            elif item is not None and item.data(0, RECORDING_ROLE) is not None:
                self.recording_requested.emit(item.data(0, RECORDING_ROLE))

    def eventFilter(self, watched, event):
        if watched is self.verticalScrollBar() and event.type() == QEvent.Wheel:
            self.wheelEvent(event)
            return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event):
        # Qt deltas already include Natural Scrolling. Undo that preference here.
        delta = event.pixelDelta().y()
        if not delta:
            delta = event.angleDelta().y() / 120 * self.verticalScrollBar().singleStep() * 3
        if event.inverted():
            delta = -delta
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - round(delta))
        event.accept()

    def _selected_source_paths(self):
        paths = []
        def collect(item):
            if not _filter_visible(item):
                return
            entity = item.data(0, ENTITY_ROLE)
            # A folder path would also move/delete unmatched or unlisted disk
            # contents. During search only explicit matching files are eligible.
            path = ((entity.path if entity else None) if self.filter_active else
                    item.data(0, SOURCE_PATH_ROLE) or (entity.path if entity else None))
            if path:
                paths.append(_absolute(path))
            for index in range(item.childCount()):
                collect(item.child(index))
        for item in self.selectedItems():
            collect(item)
        return _minimal_paths(paths)

    def startDrag(self, supported_actions):
        """Native drag compatibility; in-view gestures use explicit release handling."""
        paths = self._selected_source_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        # The receiving panel requests the operation; Qt never edits this model.
        drag.exec(supported_actions, Qt.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        paths = list(dict.fromkeys(_absolute(url.toLocalFile()) for url in event.mimeData().urls()
                                   if url.isLocalFile()))
        if not paths:
            event.ignore()
            return
        item = self.itemAt(event.position().toPoint())
        target = item.data(0, PATH_ROLE) if item else None
        self.paths_dropped.emit(paths, target)
        # A move action here would allow another Qt source view to delete its rows.
        event.setDropAction(Qt.CopyAction)
        event.accept()


class ScenePanel(QWidget):
    entity_selected = Signal(object)
    visibility_changed = Signal(object)
    focus_requested = Signal(object)
    remove_requested = Signal(object)  # Compatibility; batch removal uses the signal below.
    reload_requested = Signal(object)
    open_requested = Signal()
    open_folder_requested = Signal()
    visibility_selection_requested = Signal(object, str)
    remove_selection_requested = Signal(object)
    rename_requested = Signal(str, str)
    reveal_source_requested = Signal(str)
    move_requested = Signal(object, str)
    files_open_requested = Signal(object)
    recording_selected = Signal(object)
    recording_remove_requested = Signal(str)
    recording_options_requested = Signal(str)
    recording_reload_requested = Signal(str)
    recording_topic_reload_requested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("scenePanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._entities = []
        self._recordings = []
        self._recording_items = {}
        self._recording_roots = {}
        self._recording_groups = {}
        self._items = {}
        self._folders = {}
        self._roots = []
        self._directories = set()
        self._placements = {}
        self._folder_sources = {}
        self._group_roots = []
        self._clip_statistics = {}
        self._updating = False
        self._context_menu_widget = None
        self._filter_query = ""
        self._filter_expansion = None
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 12, 10, 12)
        root.setSpacing(8)
        head = QHBoxLayout()
        title = QLabel("场景")
        title.setObjectName("sectionTitle")
        self.lbl_count = QLabel("0")
        self.lbl_count.setObjectName("dim")
        self.lbl_count.setToolTip("匹配项 / 已加入场景的文件和话题总数")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.lbl_count)
        root.addLayout(head)
        self.search = QLineEdit()
        self.search.setObjectName("sceneSearch")
        self.search.setPlaceholderText("搜索文件、话题或数据源")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip("按名称或完整路径筛选；不会改变当前显示和选择。")
        root.addWidget(self.search)
        self.list = SceneTree(self)
        self.list.setObjectName("sceneList")
        self.list.browse_requested.connect(self.entity_selected.emit)
        self.list.itemChanged.connect(self._on_visibility)
        self.list.itemDoubleClicked.connect(self._on_double_click)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.paths_dropped.connect(self._on_drop)
        self.list.recording_requested.connect(self.recording_selected.emit)
        root.addWidget(self.list, 1)
        self.no_matches = QLabel("没有匹配的文件或话题\n清除搜索可显示全部内容")
        self.no_matches.setObjectName("sceneNoMatches")
        self.no_matches.setWordWrap(True)
        self.no_matches.setAlignment(Qt.AlignCenter)
        root.addWidget(self.no_matches, 1)
        self.empty = QWidget()
        layout = QVBoxLayout(self.empty)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("还没有数据")
        hint.setObjectName("sectionTitle")
        hint.setAlignment(Qt.AlignCenter)
        sub = QLabel("拖入采集包、点云、图片或文件夹\n先查看内容，再选择浏览")
        sub.setObjectName("dim")
        sub.setAlignment(Qt.AlignCenter)
        button = QPushButton("打开文件或文件夹…")
        button.setObjectName("primary")
        open_menu = QMenu(button)
        open_menu.addAction("打开文件…", self.open_requested.emit)
        open_menu.addAction("打开文件夹…", self.open_folder_requested.emit)
        button.setMenu(open_menu)
        layout.addWidget(hint)
        layout.addWidget(sub)
        layout.addWidget(button)
        root.addWidget(self.empty, 1)
        self.search.textChanged.connect(self._apply_filter)
        self.set_theme("dark")
        self._refresh_chrome()

    def set_theme(self, theme):
        colors = get_theme(theme)
        self.no_matches.setStyleSheet(f"color: {colors['text2']};")
        self._folder_icon = make_icon("folder", colors["warning"], 16)
        self._cloud_icon = make_icon("cloud", colors["accent"], 16)
        self._recording_icon = make_icon("columns", colors["accent"], 16)
        self._stream_icons = {
            kind: make_icon(icon, colors[color], 16)
            for kind, icon, color in [
                ("pointcloud", "cloud", "accent"), ("image", "eye", "success"),
                ("camera_info", "sliders", "success"), ("imu", "target", "warning"),
                ("text", "list", "text2"), ("table", "columns", "text2"),
                ("message", "list", "text2"),
            ]
        }
        self._status_brush = QBrush(QColor(colors["text2"]))
        for item in self._folders.values():
            item.setIcon(0, self._folder_icon)
        for item in self._items.values():
            item.setIcon(0, self._cloud_icon)
            item.setForeground(1, self._status_brush)
        for item in self._recording_groups.values():
            item.setIcon(0, self._folder_icon)
        for item in self._recording_roots.values():
            item.setIcon(0, self._recording_icon)
            item.setForeground(1, self._status_brush)
        for item in self._recording_items.values():
            item.setIcon(0, self._stream_icons.get(
                item.data(0, KIND_ROLE), self._stream_icons["message"]))
            item.setForeground(1, self._status_brush)

    def _display_roots(self):
        roots = list(self._roots)
        uncovered = [_absolute(e.path) for e in self._entities if e.path and
                     not any(_within(_absolute(e.path), root) for root in roots)]
        if uncovered:
            parents = list(dict.fromkeys(os.path.dirname(path) for path in uncovered))
            try:
                common = os.path.commonpath(parents)
            except ValueError:  # Windows sources can reside on different drives.
                common = ""
            # Avoid a machine-wide '/' or '/Users' group for unrelated imports.
            roots.extend([common] if len(Path(common).parts) > 2 else parents)
        uncovered_directories = [path for path in self._directories
                                 if not any(_within(path, root) for root in roots)]
        roots.extend(_minimal_paths(uncovered_directories))
        return _minimal_paths(roots)

    def add_roots(self, paths):
        roots = _minimal_paths([*self._roots, *(_absolute(p) for p in paths)])
        if roots != self._roots:
            self._roots = roots
            self._rebuild()

    def add_directories(self, paths):
        """Register worker-discovered folders, including empty source folders."""
        directories = {_absolute(path) for path in paths}
        if not directories.issubset(self._directories):
            self._directories.update(directories)
            self._rebuild()

    def roots(self):
        return self._display_roots()

    def tree_state(self):
        return {"roots": self.roots(), "directories": sorted(self._directories),
                "expanded": sorted(self._expanded_folders()),
                "placements": dict(self._placements)}

    def _expanded_folders(self):
        expansion = self._filter_expansion or {}
        return {path for path, item in self._folders.items()
                if expansion.get(("folder", path), item.isExpanded())}

    def _remap_filter_folders(self, mapped):
        if self._filter_expansion is not None:
            self._filter_expansion = {
                (("folder", mapped(key[1])) if key and key[0] == "folder" else key): expanded
                for key, expanded in self._filter_expansion.items()
            }

    def restore_tree_state(self, state):
        if not isinstance(state, dict):
            return
        self._roots = _minimal_paths([_absolute(p) for p in state.get("roots", [])
                                      if isinstance(p, str) and p])
        self._directories = {_absolute(p) for p in state.get("directories", [])
                             if isinstance(p, str) and p}
        placements = state.get("placements", {})
        if not isinstance(placements, dict):
            placements = {}
        self._placements = {_absolute(source): _absolute(logical)
                            for source, logical in placements.items()
                            if isinstance(source, str) and source and isinstance(logical, str) and logical}
        expanded = {_absolute(p) for p in state.get("expanded", []) if isinstance(p, str)}
        self._rebuild(expanded=expanded)

    def remap_paths(self, old, new):
        old, new = _absolute(old), _absolute(new)
        old_logical = self.display_path(old)
        new_logical = (os.path.join(os.path.dirname(old_logical), os.path.basename(new))
                       if old_logical != old else new)
        def replaced(path, previous, replacement):
            if path == previous:
                return replacement
            if _within(path, previous):
                return os.path.join(replacement, os.path.relpath(path, previous))
            return path
        def mapped(path):
            return replaced(path, old, new)
        def logical_mapped(path):
            return mapped(replaced(path, old_logical, new_logical))
        expanded = {logical_mapped(path) for path in self._expanded_folders()}
        selected_folders = [logical_mapped(item.data(0, PATH_ROLE))
                            for item in self._selected_items() if item.data(0, PATH_ROLE)]
        self._remap_filter_folders(logical_mapped)
        self._roots = _minimal_paths([mapped(path) for path in self._roots])
        self._directories = {mapped(path) for path in self._directories}
        self._placements = {mapped(source): logical_mapped(logical)
                            for source, logical in self._placements.items()}
        self._rebuild(expanded=expanded, selected_folders=selected_folders)

    def display_path(self, source):
        """Resolve a physical source through the most specific project placement."""
        source = _absolute(source)
        ancestors = [path for path in self._placements if _within(source, path)]
        if not ancestors:
            return source
        parent = max(ancestors, key=len)
        relative = os.path.relpath(source, parent)
        return self._placements[parent] if relative == "." else os.path.join(self._placements[parent], relative)

    def source_path(self, logical_folder):
        """Return the physical target represented by a visible folder row."""
        logical_folder = _absolute(logical_folder)
        return self._folder_sources.get(logical_folder, logical_folder)

    def clear_placements(self, paths):
        """Remove overrides on physically moved sources and their descendants."""
        paths = [_absolute(path) for path in paths]
        self._placements = {source: logical for source, logical in self._placements.items()
                            if not any(_within(source, path) for path in paths)}
        self._rebuild()

    def regroup_paths(self, paths, destination_logical_folder):
        """Relocate rows within the project, atomically and without touching disk."""
        destination = _absolute(destination_logical_folder)
        if destination not in self._folders:
            raise ValueError("目标文件夹不在场景中")
        paths = _minimal_paths([_absolute(path) for path in paths])
        # A folder's foreign physical descendants may appear separately in a drag
        # payload. They travel with their logical ancestor rather than flattening.
        logical_sources = {source: self.display_path(source) for source in paths}
        paths = [source for source, logical in logical_sources.items()
                 if not any(other != source and _within(logical, parent)
                            for other, parent in logical_sources.items())]
        moves = []
        for source in paths:
            logical = self.display_path(source)
            if logical in self._folders and _within(destination, logical):
                raise ValueError("不能将文件夹移动到自身或其子文件夹")
            target = os.path.join(destination, os.path.basename(logical))
            if target != logical:
                moves.append((source, logical, target))
        if not moves:
            return
        def relocated(logical):
            candidates = [(old, new) for _, old, new in moves if _within(logical, old)]
            if not candidates:
                return logical
            old, new = max(candidates, key=lambda move: len(move[0]))
            relative = os.path.relpath(logical, old)
            return new if relative == "." else os.path.join(new, relative)
        occupied = list(self._folders)
        occupied.extend(self.display_path(entity.path) for entity in self._entities if entity.path)
        relocated_paths = [relocated(logical) for logical in occupied]
        if len(set(relocated_paths)) != len(set(occupied)):
            raise ValueError("目标位置已有同名文件或文件夹")
        expanded = {relocated(path) for path in self._expanded_folders()}
        selected = [relocated(item.data(0, PATH_ROLE)) for item in self._selected_items()
                    if item.data(0, PATH_ROLE)]
        self._remap_filter_folders(relocated)
        placements = {source: relocated(logical) for source, logical in self._placements.items()}
        for source, _, target in moves:
            placements[source] = target
        self._placements = placements
        self._rebuild(expanded=expanded, selected_folders=selected)

    def set_entities(self, entities):
        self._entities = list(entities)
        self._rebuild()

    def set_recordings(self, recordings):
        self._recordings = list(recordings)
        self._rebuild()

    def recording_item(self, path, stream_id):
        return (self._recording_roots.get(path) if stream_id is None else
                self._recording_items.get((path, stream_id)))

    def select_recording(self, path, stream_id):
        item = self.recording_item(path, stream_id)
        if item is not None:
            parent = item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
            self.list.setCurrentItem(item)
            self.list.scrollToItem(item)

    def _insert_recordings(self):
        from ui.widgets.source_overview import KIND_LABELS
        self._recording_items = {}
        self._recording_roots = {}
        self._recording_groups = {}
        for info, selected in self._recordings:
            directory = os.path.dirname(info.path)
            parent = self._folders.get(directory)
            if parent is None:
                if directory not in self._recording_groups:
                    group = SceneItem(self.list, [os.path.basename(directory) or directory])
                    group.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    group.setIcon(0, self._folder_icon)
                    group.setData(0, GROUP_ROLE, directory)
                    group.setData(0, SEARCH_ROLE, directory.casefold())
                    group.setToolTip(0, directory)
                    group.setSizeHint(0, QSize(0, ROW_HEIGHT))
                    group.setExpanded(True)
                    self._recording_groups[directory] = group
                parent = self._recording_groups[directory]
            selected_ids = set(selected)
            selected_streams = [stream for stream in info.streams if stream.id in selected_ids]
            root = SceneItem(parent, [os.path.basename(info.path),
                                      f"{len(selected_streams)} 个数据流"])
            root.setData(0, RECORDING_ROLE, (info.path, None))
            root.setData(0, SEARCH_ROLE, info.path.casefold())
            root.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            root.setIcon(0, self._recording_icon)
            root.setForeground(1, self._status_brush)
            font = root.font(0)
            font.setBold(True)
            root.setFont(0, font)
            root.setToolTip(0, f"{info.path}\n{info.format} · {len(selected_streams)} 个已选话题"
                              "\n右键可查看数据包概览或重新读取")
            root.setToolTip(1, root.text(1))
            root.setSizeHint(0, QSize(0, ROW_HEIGHT))
            root.setExpanded(True)
            self._recording_roots[info.path] = root
            for stream in selected_streams:
                count = "待统计" if stream.count is None else f"{stream.count:,}"
                label = KIND_LABELS.get(stream.kind, "消息")
                # Keep the distinguishing end of long ROS topic names readable
                # even when a narrow sidebar hides the status column.
                name = ("/".join(stream.name.rstrip("/").split("/")[-2:])
                        if stream.name.startswith("/") else stream.name)
                item = SceneItem(root, [f"{label} · {name}", count])
                item.setData(0, RECORDING_ROLE, (info.path, stream.id))
                item.setData(0, SEARCH_ROLE, "\n".join((stream.name, stream.id, info.path)).casefold())
                item.setData(0, KIND_ROLE, stream.kind)
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setIcon(0, self._stream_icons.get(stream.kind, self._stream_icons["message"]))
                item.setForeground(1, self._status_brush)
                item.setSizeHint(0, QSize(0, ROW_HEIGHT))
                item.setToolTip(0, f"{stream.name}\n{label} · {count}\n{stream.message_type}\n{info.path}")
                item.setToolTip(1, f"{label} · {count}")
                self._recording_items[(info.path, stream.id)] = item

    def _tree_items(self, parent=None):
        parent = parent if parent is not None else self.list.invisibleRootItem()
        for index in range(parent.childCount()):
            item = parent.child(index)
            yield item
            yield from self._tree_items(item)

    @staticmethod
    def _item_key(item):
        if item is None:
            return None
        entity = item.data(0, ENTITY_ROLE)
        if entity is not None:
            return ("entity", id(entity))
        path = item.data(0, PATH_ROLE)
        if path:
            return ("folder", path)
        recording = item.data(0, RECORDING_ROLE)
        if recording is not None:
            return ("recording", *recording)
        group = item.data(0, GROUP_ROLE)
        return ("group", group) if group is not None else None

    def _selected_items(self):
        # QTreeWidget.selectedItems() omits hidden rows; a rebuild must retain
        # their selection so search remains presentation-only.
        return [item for item in self._tree_items() if item.isSelected()]

    def _rebuild(self, expanded=None, selected_folders=None):
        selected = {self._item_key(item) for item in self._selected_items()}
        current = self._item_key(self.list.currentItem())
        branch_expansion = {self._item_key(item): item.isExpanded() for item in self._tree_items()
                            if item.childCount()}
        if selected_folders is None:
            selected_folders = [i.data(0, PATH_ROLE) for i in self._selected_items()
                                if i.data(0, PATH_ROLE)]
        known = set(self._folders)
        strict_expansion = expanded is not None
        if expanded is None:
            expanded = self._expanded_folders()
        self._updating = True
        updates_enabled = self.list.updatesEnabled()
        self.list.setUpdatesEnabled(False)
        self.list.setSortingEnabled(False)
        self.list.clear()
        self._items, self._folders = {}, {}
        physical_roots = self._display_roots()
        # Keep discovered and inferred *physical* folders as durable empty targets.
        directories = set(self._directories) | set(physical_roots)
        for entity in self._entities:
            if entity.path:
                directories.add(os.path.dirname(_absolute(entity.path)))
        for path in list(directories):
            candidates = [root for root in physical_roots if _within(path, root)]
            if candidates:
                root = max(candidates, key=len)
                while path != root:
                    directories.add(path)
                    path = os.path.dirname(path)
        self._directories = directories
        self._folder_sources = {self.display_path(path): path for path in directories}
        self._group_roots = _minimal_paths([self.display_path(root) for root in physical_roots])
        for path in self._group_roots:
            self._folder_item(path)
        for source in sorted(directories):
            path = self.display_path(source)
            candidates = [root for root in self._group_roots if _within(path, root)]
            self._folder_item(path, max(candidates, key=len) if candidates else None)
        for order, entity in enumerate(self._entities, 1):
            self._insert_entity(entity, order)
        self._insert_recordings()
        for path, item in self._folders.items():
            item.setExpanded(path in expanded if strict_expansion or path in known else True)
            if strict_expansion and self._filter_expansion is not None:
                self._filter_expansion[("folder", path)] = path in expanded
            item.setSelected(path in selected_folders)
        for item in self._tree_items():
            key = self._item_key(item)
            if not item.data(0, PATH_ROLE):
                item.setSelected(key in selected)
                if key in branch_expansion:
                    item.setExpanded(branch_expansion[key])
            if key == current:
                self.list.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
        self.list.setSortingEnabled(True)
        self.list.setUpdatesEnabled(updates_enabled)
        self._updating = False
        self._refresh_chrome()

    def _folder_item(self, path, root=None):
        if path in self._folders:
            return self._folders[path]
        parent = self.list
        if root is not None and path != root:
            parent = self._folder_item(os.path.dirname(path), root)
        item = SceneItem(parent, [os.path.basename(path) or path])
        item.setData(0, PATH_ROLE, path)
        source = self.source_path(path)
        item.setData(0, SOURCE_PATH_ROLE, source)
        item.setData(0, SEARCH_ROLE, f"{path}\n{source}".casefold())
        item.setToolTip(0, source)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
        item.setIcon(0, self._folder_icon)
        item.setSizeHint(0, QSize(0, ROW_HEIGHT))
        item.setExpanded(True)
        self._folders[path] = item
        return item

    def _insert_entity(self, entity, order):
        parent = self.list
        if entity.path:
            path = self.display_path(entity.path)
            candidates = [root for root in self._group_roots if _within(path, root)]
            if candidates:
                parent = self._folder_item(os.path.dirname(path), max(candidates, key=len))
        item = SceneItem(parent, [entity.name, ""])
        item.setIcon(0, self._cloud_icon)
        item.setForeground(1, self._status_brush)
        item.setData(0, ENTITY_ROLE, entity)
        item.setData(0, ORDER_ROLE, order)
        ancestor = item.parent()
        while ancestor is not None:
            ancestor.setData(0, ORDER_ROLE, max(order, ancestor.data(0, ORDER_ROLE) or 0))
            ancestor = ancestor.parent()
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
        item.setSizeHint(0, QSize(0, ROW_HEIGHT))
        self._items[id(entity)] = item
        self._update_item(entity)

    def add_entity(self, entity):
        if id(entity) in self._items:
            self.update_entity(entity)
            return
        self._entities.append(entity)
        logical = self.display_path(entity.path) if entity.path else None
        if logical and not any(_within(logical, root) for root in self._group_roots):
            self._rebuild()
            return
        if entity.path and os.path.dirname(_absolute(entity.path)) not in self._directories:
            path = os.path.dirname(_absolute(entity.path))
            roots = [root for root in self._display_roots() if _within(path, root)]
            root = max(roots, key=len) if roots else path
            while True:
                self._directories.add(path)
                self._folder_sources[self.display_path(path)] = path
                if path == root:
                    break
                path = os.path.dirname(path)
        self._updating = True
        self._insert_entity(entity, len(self._entities))
        self._updating = False
        self._refresh_chrome()

    def _summary(self, entity):
        text = f"{entity.point_count:,} 点"
        if entity.xyz is None:
            text = {"unloaded": "待浏览", "loading": "正在加载…", "error": "加载失败 · 右键重试"}.get(entity.load_state, "空点云")
        if id(entity) in self._clip_statistics:
            text = f"裁剪后 {self._clip_statistics[id(entity)][0]:,} / {entity.point_count:,} 点"
        if entity.is_preview and entity.xyz is not None:
            text += f" · 预览 {int(entity.loaded * 100)}%"
        if entity.visibility_pinned:
            text += " · 叠加"
        return text

    def _update_item(self, entity):
        item = self._items[id(entity)]
        item.setText(0, entity.name)
        item.setData(0, SEARCH_ROLE, f"{entity.name}\n{entity.path or ''}".casefold())
        item.setText(1, self._summary(entity))
        item.setToolTip(0, f"{entity.path or entity.name}\n{self._summary(entity)}")
        item.setToolTip(1, self._summary(entity))
        item.setCheckState(0, Qt.Checked if entity.visible else Qt.Unchecked)

    def update_entity(self, entity):
        if id(entity) in self._items:
            self._updating = True
            self._update_item(entity)
            self._updating = False
            if self._filter_query:
                self._refresh_chrome()

    def set_clip_statistics(self, statistics):
        self._clip_statistics = statistics
        for entity in self._entities:
            self.update_entity(entity)

    def remove_entity(self, entity):
        item = self._items.pop(id(entity), None)
        if item is None:
            return
        self._entities = [e for e in self._entities if e is not entity]
        parent = item.parent()
        (parent or self.list.invisibleRootItem()).removeChild(item)
        # Physical folders remain useful drop targets even with no scene children.
        self._refresh_chrome()

    def current_entity(self):
        item = self.list.currentItem()
        return item.data(0, ENTITY_ROLE) if item is not None else None

    def select_entity(self, entity):
        item = self._items.get(id(entity))
        if item is None:
            return
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        previous = self.list.currentItem()
        self.list.setCurrentItem(item, 0, (QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows))
        self.list.scrollToItem(item)
        if previous is not item:
            self.entity_selected.emit(entity)

    def set_visible(self, entity, visible):
        if id(entity) in self._items and entity.visible != visible:
            entity.visible = bool(visible)
            self.update_entity(entity)
            self.visibility_changed.emit(entity)

    def card_count(self):
        return len(self._entities)

    def card_summary(self, index):
        if not 0 <= index < len(self._entities):
            return {}
        entity = self._entities[index]
        return {"name": entity.name, "points": self._summary(entity),
                "selected": self._items[id(entity)].isSelected()}

    def _refresh_chrome(self):
        self._apply_filter()

    def _apply_filter(self, _text=None):
        query = self.search.text().strip().casefold()
        total = len(self._entities) + len(self._recording_items)
        matched = total
        any_match = True
        if query:
            if self._filter_expansion is None:
                self._filter_expansion = {}
            for item in self._tree_items():
                if item.childCount():
                    self._filter_expansion.setdefault(self._item_key(item), item.isExpanded())
            matched = 0

            def filter_branch(item):
                nonlocal matched
                child_match = False
                for index in range(item.childCount()):
                    child_match = filter_branch(item.child(index)) or child_match
                own_match = query in (item.data(0, SEARCH_ROLE) or item.text(0).casefold())
                visible = own_match or child_match
                item.setHidden(not visible)
                if child_match:
                    item.setExpanded(True)
                recording = item.data(0, RECORDING_ROLE)
                if visible and (item.data(0, ENTITY_ROLE) is not None or
                                (recording is not None and recording[1] is not None)):
                    matched += 1
                return visible

            any_match = False
            for index in range(self.list.topLevelItemCount()):
                any_match = filter_branch(self.list.topLevelItem(index)) or any_match
        elif self._filter_query or self._filter_expansion is not None:
            expansion = self._filter_expansion or {}
            for item in self._tree_items():
                item.setHidden(False)
                key = self._item_key(item)
                if key in expansion:
                    item.setExpanded(expansion[key])
            self._filter_expansion = None
        self._filter_query = query
        self.list.filter_active = bool(query)
        self.lbl_count.setText(f"{matched} / {total}")
        has_items = bool(self._entities or self._roots or self._directories or self._recordings)
        self.list.setVisible(has_items and any_match)
        self.no_matches.setVisible(has_items and not any_match)
        self.empty.setVisible(not has_items)

    def _on_visibility(self, item, column):
        entity = item.data(0, ENTITY_ROLE)
        if not self._updating and column == 0 and entity is not None:
            visible = item.checkState(0) == Qt.Checked
            if entity.visible != visible:
                entity.visible = visible
                self.visibility_changed.emit(entity)

    def _on_double_click(self, item, _column):
        recording = item.data(0, RECORDING_ROLE)
        if recording is not None:
            self.recording_selected.emit(recording)
            return
        entity = item.data(0, ENTITY_ROLE)
        if entity is not None:
            self.focus_requested.emit(entity)

    def _selection_payload(self):
        selected = [item for item in self.list.selectedItems() if _filter_visible(item)]
        folders = _minimal_paths([item.data(0, PATH_ROLE) for item in selected
                                  if item.data(0, PATH_ROLE) and not self.list.filter_active])
        selected_ids = set()
        def collect(item):
            if not _filter_visible(item):
                return
            entity = item.data(0, ENTITY_ROLE)
            if entity is not None:
                selected_ids.add(id(entity))
            for index in range(item.childCount()):
                collect(item.child(index))
        for item in selected:
            collect(item)
        entities = [entity for entity in self._entities if id(entity) in selected_ids]
        return {"entities": entities, "folders": folders,
                "source_paths": self.list._selected_source_paths()}

    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        recording = item.data(0, RECORDING_ROLE)
        if recording is not None:
            menu = QMenu(self)
            self._context_menu_widget = menu
            menu.addAction("查看", lambda: self.recording_selected.emit(recording))
            if recording[1] is not None:
                menu.addAction("重新加载此话题", lambda: self.recording_topic_reload_requested.emit(recording))
            menu.addAction("数据包概览 / 重新选择…",
                           lambda: self.recording_options_requested.emit(recording[0]))
            menu.addAction("重新索引整个数据源", lambda: self.recording_reload_requested.emit(recording[0]))
            menu.addSeparator()
            menu.addAction("打开文件所在文件夹", lambda: self.reveal_source_requested.emit(recording[0]))
            menu.addAction("移出该数据源", lambda: self.recording_remove_requested.emit(recording[0]))
            menu.popup(self.list.viewport().mapToGlobal(pos))
            return
        if not item.isSelected():
            self.list.setCurrentItem(item, 0, (QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows))
        payload = self._selection_payload()
        single = sum(_filter_visible(selected) for selected in self.list.selectedItems()) == 1
        entity = item.data(0, ENTITY_ROLE)
        path = item.data(0, SOURCE_PATH_ROLE) or (entity.path if entity else None)
        menu = QMenu(self)
        self._context_menu_widget = menu
        if self.list.filter_active:
            menu.addAction("筛选中：仅操作匹配的文件").setEnabled(False)
            menu.addSeparator()
        if entity is not None and single:
            menu.addAction("聚焦视图", lambda: self.focus_requested.emit(entity))
            menu.addAction("重新加载", lambda: self.reload_requested.emit(entity))
            menu.addSeparator()
        if payload["entities"]:
            for label, action in [("临时显示", "show"), ("临时隐藏", "hide"),
                                  ("固定叠加显示", "pin"), ("取消固定叠加", "unpin")]:
                menu.addAction(label, lambda action=action:
                               self.visibility_selection_requested.emit(payload, action))
            menu.addSeparator()
        if path and single:
            rename = menu.addAction("重命名", lambda: self._rename(path))
            if item.data(0, PATH_ROLE) and self.list.filter_active:
                rename.setEnabled(False)
                rename.setToolTip("清除搜索后可重命名整个文件夹。")
        if single:
            reveal = menu.addAction("打开文件所在文件夹")
            reveal.setEnabled(bool(path))
            if path:
                reveal.triggered.connect(lambda: self.reveal_source_requested.emit(path))
            else:
                reveal.setToolTip("此处理结果没有独立源文件。")
        remove = menu.addAction("删除", lambda: self.remove_selection_requested.emit(payload))
        remove.setEnabled(bool(payload["entities"] or payload["folders"] or payload["source_paths"]))
        menu.popup(self.list.viewport().mapToGlobal(pos))

    def _rename(self, path):
        name, accepted = QInputDialog.getText(self, "重命名", "新名称：", text=os.path.basename(path))
        if accepted and name and name != os.path.basename(path):
            self.rename_requested.emit(path, name)

    def _on_drop(self, paths, target):
        if target:
            self.move_requested.emit(paths, target)
        else:
            self.files_open_requested.emit(paths)
