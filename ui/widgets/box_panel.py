"""Compact embedded three-view editor and optional annotation controls."""
from PySide6.QtCore import QEvent, QPoint, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QMenu, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)
from ui.widgets.box_editor import BoxEditor
from ui.widgets.data_view import ElidedLabel
from ui.widgets.smooth_progress import SmoothProgressBar


class _TrackingStatus(ElidedLabel):
    def __init__(self, details_target):
        self._details_target = details_target
        super().__init__()

    def setText(self, text):
        super().setText(str(text).replace("\n", " · "))
        self.setToolTip(str(text))
        self._details_target.setToolTip(f"选择有框帧查看并修正；帧号为原始帧号。\n{text}")


class ObjectTrackingDialog(QDialog):
    def __init__(self, controller):
        embedded = hasattr(controller.browser, "attach_box_panel")
        super().__init__(controller.browser, Qt.Widget if embedded else Qt.Tool)
        self._controller = controller
        self._compact_sizes = None
        self._host_splitter = None
        self._preview_panes = None
        self._layout_pending = False
        self._fitting = False
        self._last_panel_height = 280
        self._user_sized = False
        self._target_rows = ()
        self._result_rows = ()
        self._category_target_id = None
        self.setObjectName("objectTrackingDialog")
        self.setWindowTitle("3D 框选")
        if not embedded:
            self.resize(900, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(3)
        header = QHBoxLayout()
        self.targets = QComboBox()
        self.targets.setMinimumWidth(110)
        self.targets.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.targets.setMinimumContentsLength(8)
        self.targets.setToolTip("目标名称和固定编号；类别对该目标的所有有框帧生效")
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.setInsertPolicy(QComboBox.NoInsert)
        self.category.addItems(["未分类", "汽车", "卡车", "公交车", "行人", "自行车", "摩托车",
                                "树木", "路灯", "交通标志", "其他"])
        self.category.setMinimumWidth(100)
        self.category.setMaximumWidth(150)
        self.category.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.category.setMinimumContentsLength(6)
        self.category.lineEdit().setMaxLength(64)
        self.category.setAccessibleName("目标类别")
        self.category.setToolTip("选择或输入类别，按 Enter 保存；同一目标的关联帧共用此标签")
        self.new_button = QPushButton("新建框")
        self.reframe_button = QPushButton("重框")
        self.focus_button = QPushButton("聚焦")
        self.focus_button.setToolTip("将主视图移到当前框，不改变点云")
        self.parameters_button = QToolButton()
        self.parameters_button.setText("参数")
        self.parameters_button.setCheckable(True)
        self.associate_button = QToolButton()
        self.associate_button.setText("关联设置")
        self.associate_button.setToolTip("调整关联方向、帧数和每帧最大位移")
        self.associate_button.setCheckable(True)
        close = QToolButton()
        close.setText("×")
        close.setToolTip("收起 3D 框编辑器")
        header.addWidget(QLabel("3D 框选"))
        header.addWidget(self.targets, 1)
        header.addWidget(QLabel("类别"))
        header.addWidget(self.category)
        for widget in (self.new_button, self.reframe_button, self.focus_button,
                       self.parameters_button, self.associate_button, close):
            header.addWidget(widget)
        layout.addLayout(header)
        self.editor = BoxEditor()
        self.editor.set_theme(getattr(getattr(controller.browser, "left", None), "theme", "dark"))
        layout.addWidget(self.editor, 1)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("trackingScroll")
        self.scroll.viewport().setObjectName("trackingScrollViewport")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setMinimumHeight(72)
        self.scroll.setMaximumHeight(150)
        body = QWidget()
        body.setObjectName("trackingContent")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(5)
        self.scroll.setWidget(body)
        layout.addWidget(self.scroll)
        self.scroll.hide()
        self._settings_window = QDialog(self, Qt.Tool)
        self._settings_window.setWindowTitle("3D 框设置")
        settings_layout = QVBoxLayout(self._settings_window)
        settings_layout.setContentsMargins(8, 8, 8, 8)
        self._settings_window.finished.connect(lambda: self._toggle_tab(self.tabs.currentIndex(), False))
        self.tabs = QStackedWidget()
        self.tabs.hide()
        body_layout.addWidget(self.tabs)

        edit = QWidget()
        edit_row = QHBoxLayout(edit)
        edit_row.setContentsMargins(0, 3, 0, 0)
        coordinates = QFormLayout()
        self.center = self._vector(coordinates, "中心 (m)", -1e7, 1e7)
        self.size = self._vector(coordinates, "尺寸 (m)", .001, 1e7)
        self.rotation = self._vector(coordinates, "角度 (°)", -180, 180)
        self.roll, self.pitch, self.yaw = self.rotation
        edit_row.addLayout(coordinates, 1)
        actions = QVBoxLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText("目标名称")
        self.apply_button = QPushButton("应用数值")
        self.apply_button.setToolTip("保存为人工框，自动关联不会覆盖")
        self.delete_frame_button = QPushButton("删除当前帧框")
        for widget in (self.name, self.apply_button, self.delete_frame_button):
            actions.addWidget(widget)
        edit_row.addLayout(actions)
        self.tabs.addWidget(edit)

        tracking = QWidget()
        tracking_row = QHBoxLayout(tracking)
        tracking_row.setContentsMargins(0, 3, 0, 0)
        form = QFormLayout()
        self.direction = QComboBox()
        for label, value in (("后续帧", "forward"), ("之前帧", "backward"), ("前后双向", "both")):
            self.direction.addItem(label, value)
        self.direction.setCurrentIndex(self.direction.findData("both"))
        self.count = QSpinBox()
        self.count.setRange(1, 100)
        self.count.setValue(5)
        self.max_distance = QDoubleSpinBox()
        self.max_distance.setRange(.001, 1000.)
        self.max_distance.setDecimals(3)
        self.max_distance.setValue(2.)
        self.max_distance.setSuffix(" m")
        row = QHBoxLayout()
        row.addWidget(self.direction)
        row.addWidget(QLabel("每方向"))
        row.addWidget(self.count)
        row.addWidget(QLabel("帧"))
        form.addRow("范围", row)
        form.addRow("每帧最大位移", self.max_distance)
        tracking_row.addLayout(form, 1)
        actions = QVBoxLayout()
        self.export_button = QPushButton("导出标注")
        self.delete_target_button = QPushButton("删除目标")
        for widget in (self.export_button, self.delete_target_button):
            actions.addWidget(widget)
        tracking_row.addLayout(actions)
        self.tabs.addWidget(tracking)
        footer = QHBoxLayout()
        self.previous_button = QPushButton("‹")
        self.next_button = QPushButton("›")
        self.frame_label = QLabel()
        self.frame_label.setObjectName("metric")
        hint = QLabel("拖中心/边/角调整 · 滚轮缩放 · W/A/S/D 移动 · Q/E 旋转 · Shift 精调 · 双击贴合")
        hint.setWordWrap(True)
        hint.setObjectName("dim")
        for widget in (self.previous_button, self.frame_label, self.next_button):
            widget.setParent(self)
            widget.hide()  # Frame navigation lives in the shared transport below.
        footer.addWidget(hint, 1)
        body_layout.addLayout(footer)

        # Actions and feedback stay outside the scroll area, including at small
        # window sizes. Expanding settings is distinct from starting a job.
        association = QHBoxLayout()
        self.track_button = QPushButton()
        self.track_button.setObjectName("primary")
        self.track_button.setToolTip("从当前帧的 3D 框开始关联；人工框会保留，自动建议需要逐帧检查")
        self.extract_button = QToolButton()
        self.extract_button.setText("提取框内点")
        self.extract_button.setToolTip("点击提取当前帧到场景；右侧箭头可为所有有框帧命名并分别保存点云文件")
        self.extract_button.setPopupMode(QToolButton.MenuButtonPopup)
        extract_menu = QMenu(self.extract_button)
        self.batch_extract_action = extract_menu.addAction("导出有框帧点云…")
        self.batch_extract_action.setToolTip("自定义名称，保存 PCD 或 PLY；多帧追加原始帧号，不创建文件夹")
        self.extract_button.setMenu(extract_menu)
        self.cancel_button = QPushButton("取消")
        self.return_button = QPushButton("返回有框帧")
        self.return_button.setToolTip("返回当前目标最近的有框帧，再继续调整或关联")
        self.result_previous = QToolButton()
        self.result_previous.setText("上一框")
        self.result_previous.setToolTip("查看上一个有框帧 · Ctrl+PageUp")
        self.result_next = QToolButton()
        self.result_next.setText("下一框")
        self.result_next.setToolTip("查看下一个有框帧 · Ctrl+PageDown")
        self.result_frames = QComboBox()
        self.result_frames.setMinimumContentsLength(12)
        self.result_frames.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.result_frames.setPlaceholderText("选择有框帧")
        self.result_frames.setToolTip("当前目标的有框帧；选择一帧查看并修正，帧号为原始帧号")
        self.result_frames.setAccessibleName("有框帧")
        association.addWidget(self.track_button)
        association.addWidget(self.extract_button)
        association.addWidget(self.cancel_button)
        association.addWidget(self.return_button)
        association.addStretch(1)
        for widget in (self.result_previous, self.result_frames, self.result_next):
            association.addWidget(widget)
        layout.addLayout(association)
        self.progress = SmoothProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = _TrackingStatus(self.result_frames)
        self.status.setTextFormat(Qt.PlainText)
        self.status.setObjectName("dim")
        layout.addWidget(self.status)
        self.targets.currentIndexChanged.connect(lambda: controller.select_target(self.targets.currentData()))
        self.name.editingFinished.connect(controller.rename_target)
        self.category.activated.connect(lambda _: controller.set_target_category())
        self.category.lineEdit().editingFinished.connect(controller.set_target_category)
        self.new_button.clicked.connect(lambda: controller.set_mode("box"))
        self.reframe_button.clicked.connect(lambda: controller.set_mode("box", reframe=True))
        self.apply_button.clicked.connect(controller.apply_manual_edit)
        self.editor.box_edited.connect(controller.apply_box_edit)
        self.focus_button.clicked.connect(controller.focus_target)
        self.previous_button.clicked.connect(lambda: controller.navigate(-1))
        self.next_button.clicked.connect(lambda: controller.navigate(1))
        self.track_button.clicked.connect(lambda: controller.start_tracking(
            self.direction.currentData(), self.count.value(), self.max_distance.value()))
        self.extract_button.clicked.connect(controller.extract_current)
        self.batch_extract_action.triggered.connect(lambda: controller.extract_associated())
        self.direction.currentIndexChanged.connect(self.update_tracking_action)
        self.count.valueChanged.connect(self.update_tracking_action)
        self.result_previous.clicked.connect(lambda: controller.navigate_result(-1))
        self.result_next.clicked.connect(lambda: controller.navigate_result(1))
        self.return_button.clicked.connect(lambda: controller.navigate_result(0))
        self.result_frames.activated.connect(
            lambda index: controller.show_result(self.result_frames.itemData(index)))
        self.cancel_button.clicked.connect(controller.cancel)
        self.delete_frame_button.clicked.connect(controller.delete_frame)
        self.delete_target_button.clicked.connect(controller.delete_target)
        self.export_button.clicked.connect(lambda: controller.export_json())
        self.parameters_button.clicked.connect(lambda checked: self._toggle_tab(0, checked))
        self.associate_button.clicked.connect(lambda checked: self._toggle_tab(1, checked))
        close.clicked.connect(self.close)
        self.finished.connect(controller._dialog_finished)
        for sequence, parent, callback in (
                ("PgUp", self.editor, lambda: controller.navigate(-1)),
                ("PgDown", self.editor, lambda: controller.navigate(1)),
                ("Ctrl+PgUp", self, lambda: controller.navigate_result(-1)),
                ("Ctrl+PgDown", self, lambda: controller.navigate_result(1))):
            shortcut = QShortcut(QKeySequence(sequence), parent)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.setAutoRepeat(False)
            shortcut.activated.connect(callback)
        self.update_tracking_action()

    @staticmethod
    def _sync_rows(combo, rows, selected, previous):
        """Retain popup/selection state when only box geometry or labels change."""
        rows = tuple(rows)
        blocked = combo.blockSignals(True)
        try:
            if rows != previous:
                if tuple(value for _, value in rows) != tuple(value for _, value in previous):
                    combo.clear()
                    for label, value in rows:
                        combo.addItem(label, value)
                else:
                    for index, ((label, _), (old_label, _)) in enumerate(zip(rows, previous)):
                        if label != old_label:
                            combo.setItemText(index, label)
            index = next((index for index, (_, value) in enumerate(rows) if value == selected), -1)
            if combo.currentIndex() != index:
                combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(blocked)
        return rows

    def set_category(self, category, target_id=None):
        edit = self.category.lineEdit()
        if (target_id == self._category_target_id and edit.hasFocus() and edit.isModified()):
            return
        self._category_target_id = target_id
        text = category or "未分类"
        blocked = self.category.blockSignals(True)
        try:
            if self.category.findText(text) < 0:
                self.category.addItem(text)
            if self.category.currentText() != text:
                self.category.setCurrentText(text)
        finally:
            self.category.blockSignals(blocked)

    def set_targets(self, rows, selected):
        self._target_rows = self._sync_rows(self.targets, rows, selected, self._target_rows)

    def set_results(self, rows, selected):
        self._result_rows = self._sync_rows(self.result_frames, rows, selected, self._result_rows)

    def update_tracking_action(self):
        if self._controller.busy:
            self.track_button.setText(f"正在{self._controller.job_label} {self.progress.value() // 10}%")
            return
        direction = self.direction.currentData()
        scope = {"both": "前后各", "forward": "后续", "backward": "之前"}[direction]
        self.track_button.setText(f"关联{scope} {self.count.value()} 帧")

    def _toggle_tab(self, index, checked):
        was_expanded = not self.tabs.isHidden()
        self.tabs.setCurrentIndex(index)
        self.tabs.setVisible(checked)
        self.parameters_button.setChecked(checked and index == 0)
        self.associate_button.setChecked(checked and index == 1)
        if checked and not was_expanded and self._host_splitter is not None:
            self._compact_sizes = self._host_splitter.sizes()
        if checked:
            self._fit_host_layout(force=True)
            if self._settings_window.isVisible():
                self._settings_window.raise_()
                self._settings_window.activateWindow()
            QTimer.singleShot(0, self, lambda: self.scroll.ensureWidgetVisible(self.tabs.currentWidget()))
        else:
            self._settings_window.hide()
            self._place_settings(False)
            self.scroll.hide()
            self._fit_host_layout()
            if self._compact_sizes and self._host_splitter is not None:
                self._host_splitter.setSizes(self._compact_sizes)
                self._compact_sizes = None
            self.scroll.verticalScrollBar().setValue(0)

    def attach_preview_layout(self, splitter, panes):
        self._host_splitter, self._preview_panes = splitter, panes
        self._preview_minimum = panes.minimumHeight()
        splitter.installEventFilter(self)
        splitter.splitterMoved.connect(self._remember_panel_size)

    def _remember_panel_size(self, *_):
        if self.isVisible() and not self._fitting:
            self._user_sized = True
            self._last_panel_height = self.height()

    def _minimum_preview_height(self):
        """Reserve 48 usable canvas pixels, including native view decorations."""
        minimum = 48
        for view in (self._controller.browser.left, self._controller.browser.right):
            if not view.isVisibleTo(self._preview_panes):
                continue
            layout = view.layout()
            items = [layout.itemAt(index) for index in range(layout.count())
                     if not layout.itemAt(index).isEmpty()]
            margins = layout.contentsMargins()
            chrome = margins.top() + margins.bottom() + max(0, len(items) - 1) * layout.spacing()
            chrome += sum(item.minimumSize().height() for item in items if item.widget() is not view.stack)
            content = view.stack.currentWidget()
            canvas = content.viewport() if hasattr(content, "viewport") else content
            inset = max(0, content.height() - canvas.height())
            if hasattr(content, "frameWidth"):
                inset = max(inset, 2 * content.frameWidth())
            minimum = max(minimum, 48 + chrome + inset)
        return minimum

    def _place_settings(self, floating):
        if floating:
            if self.scroll.parentWidget() is not self._settings_window:
                self.layout().removeWidget(self.scroll)
                self._settings_window.layout().addWidget(self.scroll)
            self.scroll.setMaximumHeight(16777215)
            self.scroll.show()
            if not self._settings_window.isVisible():
                self._settings_window.resize(min(620, max(440, self.width())), 220)
                window = self.window()
                origin = window.mapToGlobal(QPoint(0, 0))
                screen = window.screen().availableGeometry()
                right = origin.x() + window.width() + 12
                left = origin.x() - self._settings_window.width() - 12
                if right + self._settings_window.width() <= screen.right():
                    position = QPoint(right, max(screen.top(), origin.y()))
                elif left >= screen.left():
                    position = QPoint(left, max(screen.top(), origin.y()))
                else:
                    position = QPoint(max(screen.left(), screen.right() - self._settings_window.width()), screen.top())
                self._settings_window.move(position)
                self._settings_window.show()
        else:
            self._settings_window.hide()
            if self.scroll.parentWidget() is self._settings_window:
                self._settings_window.layout().removeWidget(self.scroll)
                self.layout().insertWidget(2, self.scroll)
            self.scroll.setMaximumHeight(150)

    def _fit_host_layout(self, *, force=False):
        self._layout_pending = False
        if self._fitting or not self.isVisible():
            return
        splitter, panes = self._host_splitter, self._preview_panes
        if splitter is None:
            self.scroll.setVisible(not self.tabs.isHidden())
            return
        self._fitting = True
        try:
            total = max(0, splitter.height() - splitter.handleWidth())
            expanded = not self.tabs.isHidden()
            # The core editor has a guaranteed place in the layout. Optional
            # settings use their existing scroll widget in a tool window if an
            # inline row would leave no usable cloud/image preview.
            self.scroll.hide()
            core = self.layout().minimumSize().height() - self.editor.minimumHeight()
            preview_minimum = self._minimum_preview_height()
            browser = self._controller.browser
            if hasattr(browser, "fit_compact_controls"):
                if self.status.isHidden():
                    core += self.status.minimumSizeHint().height() + self.layout().spacing()
                tight = browser.fit_compact_controls(core + 108 + preview_minimum + splitter.handleWidth())
                self.status.setVisible(not tight)
                self.layout().activate()
                core = self.layout().minimumSize().height() - self.editor.minimumHeight()
                total = max(0, splitter.height() - splitter.handleWidth())
            # The 48-pixel floor is for genuinely short windows. Opening the
            # editor normally reserves a useful 170-pixel cloud/image canvas.
            preview_preferred = max(self._preview_minimum, preview_minimum + 122)
            inline = expanded and total >= core + 108 + self.scroll.minimumHeight() + 5 + preview_minimum
            self._place_settings(expanded and not inline)
            self.scroll.setVisible(expanded)
            if not expanded:
                self.scroll.hide()
            settings_height = min(150, self.scroll.sizeHint().height()) + 5 if inline else 0
            compact = total < core + 160 + settings_height + preview_preferred
            self.editor.set_compact(compact)
            panes.setMinimumHeight(preview_minimum if compact or inline else max(preview_minimum, self._preview_minimum))
            self.layout().activate()
            minimum = self.layout().minimumSize().height()
            maximum = max(minimum, total - panes.minimumHeight())
            wanted = (core + self.editor.minimumHeight() + settings_height) if inline else self._last_panel_height
            auto_balance = not self._user_sized and not inline
            if auto_balance:
                wanted = min(wanted, max(minimum, total - preview_preferred))
            wanted = max(minimum, min(wanted, maximum))
            if (force or (auto_balance and abs(self.height() - wanted) > 1)
                    or self.height() < minimum or panes.height() < panes.minimumHeight()):
                splitter.setSizes([max(panes.minimumHeight(), total - wanted), wanted])
        finally:
            self._fitting = False

    def schedule_layout(self):
        if not self._layout_pending:
            self._layout_pending = True
            QTimer.singleShot(0, self, self._fit_host_layout)

    def eventFilter(self, watched, event):
        if watched is self._host_splitter and event.type() == QEvent.Resize:
            self.schedule_layout()
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self, lambda: self._fit_host_layout(force=True))

    def hideEvent(self, event):
        self._last_panel_height = self.height()
        self._toggle_tab(self.tabs.currentIndex(), False)
        if self._preview_panes is not None:
            self._preview_panes.setMinimumHeight(self._preview_minimum)
        if hasattr(self._controller.browser, "set_compact_controls"):
            self._controller.browser.set_compact_controls(False)
        self.status.show()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._controller._dialog_finished()
        super().closeEvent(event)

    @staticmethod
    def _vector(form, label, minimum, maximum):
        row = QHBoxLayout()
        values = []
        for axis in "XYZ":
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setDecimals(3)
            spin.setKeyboardTracking(False)
            spin.setPrefix(f"{axis} ")
            spin.setMinimumWidth(64)
            spin.setMaximumWidth(110)
            row.addWidget(spin)
            values.append(spin)
        form.addRow(label, row)
        return values
