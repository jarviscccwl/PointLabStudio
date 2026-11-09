"""QSS 样式表构建：把 Design Tokens 渲染成 Qt 样式。

深色主题以 1px 描边分层（方案 §5.3），强调色只用于选中态与主操作。
"""

from __future__ import annotations

from pathlib import Path
from string import Template

from ui.theme.tokens import FONTS, RADII, get_theme

_CHECK_ICON = (Path(__file__).resolve().parents[2] / "resources" / "check.svg").as_posix()
_CHEVRON_ICON = (Path(__file__).resolve().parents[2] / "resources" / "chevron-down.svg").as_posix()

_QSS = Template("""
* {
    font-family: ${ui_font};
    font-size: 13px;
    color: ${text};
}

QMainWindow, QDialog {
    background: ${bg};
}
QScrollArea#trackingScroll, QWidget#trackingScrollViewport, QWidget#trackingContent {
    background: ${bg};
    border: none;
}
QDialog#objectTrackingDialog QGroupBox {
    background: ${panel};
}

/* ---------- Dock 与面板 ---------- */
QDockWidget {
    color: ${text2};
    titlebar-close-icon: none;
    titlebar-normal-icon: none;
}
QDockWidget::title {
    background: ${bg};
    padding: 6px 10px;
    border: none;
    text-align: left;
}
QWidget#dockContent {
    background: ${panel};
    border: 1px solid ${border};
    border-radius: ${card}px;
}

/* ---------- 工具栏 ---------- */
QToolBar {
    background: ${panel};
    border: none;
    border-bottom: 1px solid ${border};
    padding: 6px;
    spacing: 4px;
}
QToolBar::separator {
    background: ${border};
    width: 1px;
    margin: 4px 6px;
}
QToolButton {
    background: transparent;
    border: none;
    border-radius: ${control}px;
    padding: 5px 9px;
    color: ${text};
}
QToolButton:hover { background: ${panel2}; }
QToolButton:pressed { background: ${panel3}; }
QToolButton:checked { background: ${selection}; color: ${text}; }
QToolButton:disabled { color: ${disabled}; }
QToolButton:focus { outline: 1px solid ${accent}; }

/* ---------- 按钮 ---------- */
QPushButton {
    background: ${panel2};
    border: 1px solid ${border};
    border-radius: ${control}px;
    padding: 5px 14px;
    color: ${text};
}
QPushButton:hover { background: ${panel3}; border-color: ${border_strong}; }
QPushButton:pressed { background: ${selection}; }
QPushButton:focus { border-color: ${accent}; }
QPushButton:disabled { color: ${disabled}; background: ${panel2}; }
QPushButton#primary {
    background: ${accent};
    border: none;
    color: ${accent_text};
    font-weight: 600;
}
QPushButton#primary:hover { background: ${accent_hover}; }
QPushButton#primary:pressed { background: ${accent}; }
QPushButton#primary:disabled { background: ${panel2}; color: ${disabled}; }

/* ---------- 输入控件 ---------- */
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
    background: ${panel2};
    border: 1px solid ${border};
    border-radius: ${input}px;
    padding: 4px 8px;
    color: ${text};
    selection-background-color: ${accent};
    selection-color: ${accent_text};
    placeholder-text-color: ${text3};
}
QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
    border-color: ${border_strong};
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
    border-color: ${accent};
}
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {
    color: ${disabled};
}
QComboBox::drop-down { border: none; width: 22px; }
QComboBox::down-arrow {
    image: url(${chevron_icon});
    width: 12px;
    height: 8px;
    margin-right: 6px;
}
QComboBox QAbstractItemView {
    background: ${panel};
    border: 1px solid ${border_strong};
    border-radius: ${input}px;
    selection-background-color: ${selection};
    selection-color: ${text};
    outline: none;
    padding: 4px;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background: transparent;
    border: none;
    width: 18px;
}

/* ---------- 树与列表 ---------- */
QTreeWidget, QListWidget {
    background: ${panel};
    border: 1px solid ${border};
    border-radius: ${card}px;
    outline: none;
    padding: 4px;
}
QTreeWidget::item, QListWidget::item {
    padding: 5px 6px;
    border-radius: ${control}px;
    color: ${text};
}
QTreeWidget::item:hover, QListWidget::item:hover { background: ${panel2}; }
QTreeWidget::item:selected, QListWidget::item:selected {
    background: ${selection};
    color: ${text};
}
QHeaderView { background: ${panel}; }
QHeaderView::section {
    background: ${panel};
    color: ${text2};
    border: none;
    border-bottom: 1px solid ${border};
    padding: 5px 8px;
    font-size: 12px;
}
QTableView {
    background: ${panel};
    alternate-background-color: ${panel2};
    color: ${text};
    border: 1px solid ${border};
    border-radius: ${input}px;
    gridline-color: ${border};
    selection-background-color: ${selection};
    selection-color: ${text};
}
QTableView::item { padding: 4px 8px; }
QTableView::item:selected { background: ${selection}; color: ${text}; }
QTableCornerButton::section { background: ${panel}; border: none; }

/* ---------- 标签与分组 ---------- */
QLabel { background: transparent; color: ${text}; }
QLabel#hint, QLabel#dim { color: ${text2}; font-size: 11px; }
QLabel#title { font-size: 15px; font-weight: 600; letter-spacing: -0.3px; }
QLabel#bigTitle { font-size: 22px; font-weight: 600; color: ${text}; letter-spacing: -0.3px; }
QGroupBox {
    border: 1px solid ${border};
    border-radius: ${input}px;
    margin-top: 10px;
    padding-top: 8px;
    color: ${text2};
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}

/* ---------- 检查框 ---------- */
QCheckBox { color: ${text}; spacing: 6px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid ${border_strong};
    border-radius: 5px;
    background: ${panel2};
}
QCheckBox::indicator:hover { border-color: ${accent}; }
QCheckBox::indicator:checked {
    background: ${accent}; border-color: ${accent}; image: url(${check_icon});
}
QCheckBox:disabled { color: ${disabled}; }
QCheckBox::indicator:disabled { border-color: ${border}; }

/* ---------- 滑条 ---------- */
QSlider::groove:horizontal {
    height: 4px;
    background: ${panel3};
    border-radius: 2px;
}
QSlider::sub-page:horizontal { background: ${accent}; border-radius: 2px; }
QSlider::handle:horizontal {
    background: ${text};
    width: 14px; height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: ${accent_hover}; }
QSlider::handle:horizontal:pressed { background: ${accent}; }

/* ---------- 日志/文本 ---------- */
QPlainTextEdit, QTextEdit {
    background: ${panel};
    border: 1px solid ${border};
    border-radius: ${card}px;
    color: ${text2};
    selection-background-color: ${selection};
    font-family: ${mono_font};
    font-size: 12px;
}

/* ---------- 菜单 ---------- */
QMenu {
    background: ${panel};
    border: 1px solid ${border_strong};
    border-radius: ${input}px;
    padding: 5px;
}
QMenu::item {
    padding: 5px 24px 5px 12px;
    border-radius: ${control}px;
    color: ${text};
}
QMenu::item:selected { background: ${selection}; }
QMenu::item:disabled { color: ${disabled}; }
QMenu::separator { height: 1px; background: ${border}; margin: 4px 8px; }

/* ---------- 状态栏 ---------- */
QStatusBar {
    background: ${panel};
    border-top: 1px solid ${border};
    color: ${text2};
    font-size: 11px;
}
QStatusBar::item { border: none; }

/* ---------- 进度条 ---------- */
QProgressBar {
    background: ${panel2};
    selection-background-color: ${accent};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk { background: ${accent}; border-radius: 4px; }

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: ${panel3};
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: ${text3}; }
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: ${panel3};
    border-radius: 4px;
    min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: ${text3}; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---------- 分割条 ---------- */
QSplitter::handle { background: ${border}; }
QSplitter::handle:horizontal { width: 1px; }
QSplitter::handle:vertical { height: 1px; }
QSplitter#workspacePanes::handle { background: transparent; }
QSplitter#workspacePanes::handle:hover { background: ${border_strong}; }

/* ---------- 选项卡 ---------- */
QTabWidget::pane {
    border: 1px solid ${border};
    border-radius: ${card}px;
    background: ${panel};
}
QTabBar::tab {
    background: transparent;
    color: ${text2};
    padding: 6px 14px;
    border: none;
    border-bottom: 2px solid transparent;
}
QTabBar::tab:selected { color: ${text}; border-bottom: 2px solid ${accent}; }
QTabBar::tab:hover { color: ${text}; }

/* ---------- 提示 ---------- */
QToolTip {
    background: ${panel3};
    color: ${text};
    border: 1px solid ${border_strong};
    border-radius: ${control}px;
    padding: 4px 8px;
    font-size: 12px;
}

/* ---------- Studio 应用壳 ---------- */
QWidget#appHeader { background: ${panel}; border: none; border-bottom: 1px solid ${border}; }
QLabel#appLogo { color: ${accent}; font-size: 17px; }
QLabel#appTitle { font-size: 15px; font-weight: 600; letter-spacing: -0.3px; color: ${text}; }
QLabel#appSub { font-size: 11px; color: ${text3}; margin-left: 2px; }
QToolButton#headerBtn {
    background: transparent;
    border: none;
    border-radius: 9px;
    padding: 5px 9px;
    color: ${text};
    font-size: 12px;
}
QToolButton#headerBtn:hover { background: ${panel2}; }
QToolButton#headerBtn:pressed { background: ${panel3}; }
QToolButton#headerBtn:checked { background: ${selection}; }
QWidget#toolGroup {
    background: ${panel2};
    border: 1px solid ${border};
    border-radius: 12px;
}
QLabel#headerSep { color: ${border_strong}; font-size: 12px; padding: 0 2px; }
QComboBox#headerCombo {
    background: ${panel2};
    border: 1px solid ${border};
    border-radius: 8px;
    padding: 4px 8px;
    color: ${text};
    font-size: 12px;
}
QComboBox#headerCombo:hover { border-color: ${border_strong}; }
QComboBox#headerCombo:focus { border-color: ${accent}; }

/* ---------- 场景面板 ---------- */
QWidget#scenePanel { background: ${panel}; border: 1px solid ${border}; border-radius: ${card}px; }
QTreeWidget#sceneList { background: transparent; border: none; outline: none; }
QTreeWidget#sceneList::item { padding: 2px 3px; border: none; }
QTreeWidget#sceneList::item:hover { background: ${panel2}; }
QTreeWidget#sceneList::item:selected { background: ${selection}; color: ${text}; }
QTreeWidget#sceneList::branch { background: ${panel}; border-image: none; image: none; }
QTreeWidget#sceneList::branch:selected { background: ${panel}; }
QTreeWidget#sceneList::branch:has-children:closed { image: url(${chevron_right_icon}); }
QTreeWidget#sceneList::branch:has-children:open { image: url(${chevron_icon}); }
QTreeWidget#sceneList::indicator {
    width: 14px; height: 14px; border: 1px solid ${border_strong};
    border-radius: 3px; background: ${panel2};
}
QTreeWidget#sceneList::indicator:checked {
    background: ${accent}; border-color: ${accent}; image: url(${check_icon});
}
QTreeWidget#sceneList QHeaderView::section {
    background: ${panel}; color: ${text2}; padding: 5px 3px;
    border: none; border-bottom: 1px solid ${border}; font-size: 11px;
}
QLabel#sectionTitle { font-size: 12px; font-weight: 600; color: ${text2}; letter-spacing: 0.2px; }
QLabel#dim { color: ${text2}; font-size: 11px; }
QListWidget#sceneList { background: transparent; border: none; padding: 0; }
QWidget#sceneCard { background: ${panel2}; border-radius: 12px; }
QWidget#sceneCard:hover { background: ${panel3}; }
QWidget#sceneCard[selected="true"] { background: ${selection}; }
QLabel#cardTitle { font-size: 13px; font-weight: 600; color: ${text}; }
QLabel#cardSub { font-size: 11px; color: ${text2}; }
QToolButton#eyeBtn {
    border: none;
    background: transparent;
    color: ${text3};
    font-size: 14px;
    border-radius: 12px;
}
QToolButton#eyeBtn:hover { background: ${panel3}; color: ${text2}; }
QToolButton#eyeBtn[visible="true"] { color: ${accent}; }

/* 场景卡片勾选框 */
QCheckBox#sceneCheck { spacing: 0; }
QCheckBox#sceneCheck::indicator {
    width: 17px;
    height: 17px;
    border: 1px solid ${border_strong};
    border-radius: 5px;
    background: ${panel2};
}
QCheckBox#sceneCheck::indicator:hover { border-color: ${accent}; }
QCheckBox#sceneCheck::indicator:checked {
    background: ${accent};
    border-color: ${accent};
    image: url(${check_icon});
}

/* ---------- 检查器 / 数据概览 ---------- */
QWidget#inspector { background: ${panel}; border: 1px solid ${border}; border-radius: ${card}px; }
QScrollArea#overviewScroll { background: transparent; border: none; }
QScrollArea#overviewScroll > QWidget > QWidget { background: transparent; }
QWidget#sectionCard { background: ${panel2}; border-radius: 10px; }
QWidget#overviewCard { background: ${panel2}; border: 1px solid ${border}; border-radius: 12px; }
QLabel#cardHead {
    font-size: 11px;
    font-weight: 600;
    color: ${text2};
    letter-spacing: 0.4px;
}
QLabel#entityName {
    font-size: 16px;
    font-weight: 600;
    color: ${text};
}
QLabel#bigNumber {
    font-size: 26px;
    font-weight: 600;
    color: ${text};
    letter-spacing: -0.5px;
}
QLabel#rowCaption { font-size: 11px; color: ${text2}; }
QLabel#valueText { font-size: 13px; color: ${text}; }
QLabel#badgePill {
    background: ${panel3};
    color: ${text};
    border-radius: 6px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 600;
}
QLabel#badgePillDim {
    background: transparent;
    color: ${text2};
    border: 1px solid ${border_strong};
    border-radius: 6px;
    padding: 3px 9px;
    font-size: 11px;
}
QWidget#infoRow { background: transparent; }

/* ---------- 裁剪范围弹层 ---------- */
QWidget#clipPopup {
    background: ${panel};
    border: 1px solid ${border_strong};
    border-radius: 12px;
}

/* ---------- 视口框选/测量浮层 ---------- */
QWidget#rubber {
    border: 1px solid ${accent};
    background: rgba(10, 132, 255, 0.14);
}
QLabel#overlayPill {
    background: rgba(18, 18, 22, 0.92);
    color: #F5F5F7;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 9px;
    padding: 5px 11px;
    font-size: 12px;
}

/* ---------- 底部状态栏与日志抽屉 ---------- */
QWidget#bottomBar { background: ${panel}; border-top: 1px solid ${border}; }
QLabel#metric { color: ${text2}; font-size: 11px; }
QPlainTextEdit#logDrawer {
    border-left: 1px solid ${border};
    border-right: 1px solid ${border};
    border-bottom: none;
    border-radius: 0;
}
""")


def build_stylesheet(theme_name: str = "dark") -> str:
    t = get_theme(theme_name)
    stylesheet = _QSS.substitute(
        ui_font=FONTS["ui"],
        mono_font=FONTS["mono"],
        control=RADII["control"],
        input=RADII["input"],
        card=RADII["card"],
        dialog=RADII["dialog"],
        check_icon=_CHECK_ICON,
        chevron_icon=(_CHEVRON_ICON.replace("chevron-down.svg", "chevron-down-light.svg")
                      if theme_name == "light" else _CHEVRON_ICON),
        chevron_right_icon=_CHEVRON_ICON.replace("chevron-down.svg",
                            "chevron-right-light.svg" if theme_name == "light" else "chevron-right.svg"),
        **t,
    )
    if theme_name == "light":
        stylesheet += Template("""
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: ${panel};
    selection-background-color: ${accent};
    selection-color: ${accent_text};
    placeholder-text-color: ${text3};
}
QComboBox#headerCombo { background: ${panel}; }
QPlainTextEdit, QTextEdit {
    color: ${text};
    selection-background-color: ${accent};
    selection-color: ${accent_text};
}
QToolButton#headerBtn:disabled, QPushButton:disabled,
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    color: ${disabled};
}
QToolButton#headerBtn:checked { color: ${accent}; }
QLabel#warning { color: ${danger}; }
QCheckBox::indicator:checked { image: url(${check_icon}); }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #959DA9; }
QLabel#overlayPill {
    background: ${panel};
    color: ${text};
    border: 1px solid ${border_strong};
}
""").substitute(check_icon=_CHECK_ICON, **t)
    return stylesheet
