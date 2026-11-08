"""Lightweight startup artwork; deliberately independent of the GPU renderer."""
import math
from pathlib import Path

from PySide6.QtCore import QEventLoop, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QRadialGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QSplashScreen

from core import __version__
from ui.theme.tokens import get_theme


class StartupScreen(QSplashScreen):
    def __init__(self, theme="dark"):
        super().__init__()
        self.colors = get_theme(theme)
        self.setWindowTitle("PointLab Studio · 正在启动")
        self.set_progress(0)

    def set_progress(self, percent):
        self.progress = percent
        colors = self.colors
        ratio = QApplication.primaryScreen().devicePixelRatio()
        canvas = QPixmap(round(780 * ratio), round(440 * ratio))
        canvas.setDevicePixelRatio(ratio)
        canvas.fill(QColor(colors["bg"]))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        glow = QRadialGradient(586, 205, 190)
        accent = QColor(colors["accent"])
        accent.setAlpha(24)
        glow.setColorAt(0, accent)
        accent.setAlpha(0)
        glow.setColorAt(1, accent)
        painter.fillRect(QRectF(370, 20, 410, 340), glow)
        painter.setPen(Qt.NoPen)
        for ring in range(26):
            latitude = (ring + 0.5) * math.pi / 26
            for index in range(54):
                angle = index * math.tau / 54 + ring * 0.08
                radius = 126 * math.sin(latitude)
                x = 586 + math.cos(angle) * radius
                y = 200 + math.cos(latitude) * 110 + math.sin(angle) * radius * 0.22
                color = QColor(colors["accent"])
                color.setAlphaF(0.15 + 0.7 * (math.sin(angle) + 1) / 2)
                painter.setBrush(color)
                painter.drawEllipse(QPointF(x, y), 1.1, 1.1)
        QSvgRenderer(str(Path(__file__).resolve().parents[1] / "resources" / "app.svg")).render(
            painter, QRectF(44, 38, 34, 34))
        painter.setPen(QColor(colors["text2"]))
        font = QFont()
        font.setFamilies(["Segoe UI Variable", "Segoe UI", "Ubuntu Sans", "Noto Sans", "DejaVu Sans"])
        font.setPixelSize(11)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        painter.setFont(font)
        painter.drawText(90, 60, "POINT CLOUD WORKSTATION")
        painter.setPen(QColor(colors["text"]))
        title = QFont(font)
        title.setPixelSize(58)
        title.setWeight(QFont.DemiBold)
        title.setLetterSpacing(QFont.AbsoluteSpacing, -2)
        painter.setFont(title)
        painter.drawText(41, 168, "PointLab")
        subtitle = QFont(font)
        subtitle.setPixelSize(27)
        subtitle.setWeight(QFont.Light)
        subtitle.setLetterSpacing(QFont.AbsoluteSpacing, 7)
        painter.setFont(subtitle)
        painter.drawText(46, 215, "STUDIO")
        painter.setPen(QColor(colors["text2"]))
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0)
        font.setPixelSize(14)
        caption = QFont(font)
        caption.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC"])
        painter.setFont(caption)
        painter.drawText(46, 266, "点云浏览 · 处理 · 分析")
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(44, 363, f"v{__version__}")
        painter.setPen(QColor(colors["text"]))
        font.setPixelSize(15)
        painter.setFont(font)
        painter.drawText(QRectF(610, 343, 126, 26), Qt.AlignRight | Qt.AlignVCenter,
                         f"{percent}%")
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colors["panel3"]))
        painter.drawRoundedRect(QRectF(44, 387, 692, 4), 2, 2)
        painter.setBrush(QColor(colors["accent"]))
        painter.drawRoundedRect(QRectF(44, 387, 692 * percent / 100, 4), 2, 2)
        painter.end()
        self.setPixmap(canvas)
        self.show()
        QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
