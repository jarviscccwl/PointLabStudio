"""PointLabStudio 桌面应用入口。"""

from __future__ import annotations

import argparse
import sys


def build_parser():
    parser = argparse.ArgumentParser(description="PointLabStudio — 点云综合处理平台")
    parser.add_argument("path", nargs="?", default=None, help="点云文件或点云目录")
    parser.add_argument("--background", choices=["dark", "light"], default=None,
                        help="背景主题（默认沿用上次设置）")
    return parser


def fit_window_to_screen(window):
    """把启动窗口收进当前屏幕可用区域，兼容小屏与旧显示器配置。"""
    from PySide6.QtWidgets import QApplication

    screen = window.screen() or QApplication.primaryScreen()
    if screen is None:
        return
    available = screen.availableGeometry()
    geometry = window.frameGeometry()
    width = min(geometry.width(), available.width())
    height = min(geometry.height(), available.height())
    left = max(available.left(), min(geometry.left(), available.right() - width + 1))
    top = max(available.top(), min(geometry.top(), available.bottom() - height + 1))
    window.setMinimumSize(min(window.minimumWidth(), width),
                          min(window.minimumHeight(), height))
    window.setGeometry(left, top, width, height)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) in (2, 4) and arguments[0] == "--splash-child":
        from ui.startup import _run_splash_child

        handle = (int(arguments[3])
                  if len(arguments) == 4 and arguments[2] == "--splash-input" else None)
        sys.exit(_run_splash_child(arguments[1], input_handle=handle))
    args = build_parser().parse_args(arguments)

    from core.applog import get_logger, setup_logging
    from core.i18n import set_language
    from core.recovery import install_excepthook, is_unclean, mark_running
    from core.settings import load_settings

    setup_logging()
    install_excepthook()
    logger = get_logger("main")
    settings = load_settings()
    if is_unclean():
        logger.warning("检测到上次未正常退出（残留 session.running）")
    mark_running()
    try:
        set_language(settings.language)
    except ValueError:
        set_language("zh")
    theme = args.background or settings.theme

    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from pathlib import Path

    app = QApplication(sys.argv[:1] if argv is not None else sys.argv)
    app.setApplicationName("PointLabStudio")
    from core import __version__
    app.setApplicationVersion(__version__)
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[1] / "resources" / "app.svg")))
    app.setOrganizationName("PointLabStudio")
    app.setStyle("Fusion")
    from ui.startup import StartupProcess

    splash = StartupProcess(theme)
    try:
        splash.set_progress(None, "正在加载点云与渲染组件…")
        from ui.main_window import MainWindow

        splash.set_progress(None, "正在创建工作台…")
        window = MainWindow(theme=theme)
        fit_window_to_screen(window)
        splash.set_progress(None, "正在准备视图…")
        window.show()
        app.processEvents(QEventLoop.ExcludeUserInputEvents)
        splash.finish(window)
    finally:
        splash.close()
    if args.path:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: window.open_paths([args.path]))
    code = app.exec()
    from core.recovery import clear_running
    clear_running()
    sys.exit(code)


if __name__ == "__main__":
    main()
