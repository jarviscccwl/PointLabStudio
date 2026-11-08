"""内置矢量图标（Feather 风格线条图标，24×24，颜色随主题注入）。

用 QtSvg 在运行时把 SVG 渲染成 QIcon，避免外部图标资源依赖；
图标颜色由调用方传入（深色主题用浅色描边，浅色主题用深色描边）。
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

try:
    from PySide6.QtSvg import QSvgRenderer

    _HAS_SVG = True
except Exception:  # noqa: BLE001
    _HAS_SVG = False

_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
    "{body}</svg>"
)

ICON_BODIES = {
    "cloud": '<circle cx="6" cy="7" r="1.5"/><circle cx="16" cy="5" r="1.5"/>'
             '<circle cx="12" cy="12" r="1.5"/><circle cx="5" cy="17" r="1.5"/>'
             '<circle cx="19" cy="15" r="1.5"/><circle cx="13" cy="20" r="1.5"/>',
    "chevron-left": '<path d="m15 5-7 7 7 7"/>',
    "chevron-right": '<path d="m9 5 7 7-7 7"/>',
    "columns": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/>',
    # 文件/工程
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "folder-plus": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'
                   '<path d="M12 11v6M9 14h6"/>',
    # 处理 / 视图
    "sliders": '<path d="M4 7h16M4 12h16M4 17h16"/>'
               '<circle cx="9" cy="7" r="1.8"/><circle cx="15" cy="12" r="1.8"/><circle cx="7" cy="17" r="1.8"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    # 撤销 / 重做 / 重置
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 0 12h-3"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 0 0 0 12h3"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/>',
    # 工具
    "ruler": '<path d="M4 20 20 4"/><path d="M8 8l2 2M12 4l2 2M4 12l2 2M16 16l2 2"/>',
    "select": '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2'
              'M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/>',
    "crop": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
    # 选点：靶心 + 十字准星
    "target": '<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2.2"/>'
              '<path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>',
    # 主题 / 帮助 / 日志
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4'
           'M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "check": '<path d="M4 12.5 9 17.5 20 6.5"/>',
}


def make_icon(name: str, color: str, size: int = 18) -> QIcon | None:
    """按名称 + 颜色生成 QIcon（2 倍分辨率，支持 HiDPI）。"""
    body = ICON_BODIES.get(name)
    if body is None or not _HAS_SVG:
        return None
    svg = _TEMPLATE.format(color=color, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    scale = 2
    pixmap = QPixmap(QSize(size * scale, size * scale))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(scale)
    return QIcon(pixmap)
