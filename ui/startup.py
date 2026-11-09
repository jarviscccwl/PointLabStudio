"""Lightweight animated startup art, isolated from main-thread native imports."""
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

from PySide6.QtCore import QElapsedTimer, QEventLoop, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QSplashScreen

from core import __version__
from ui.theme.tokens import get_theme


RESOURCES = Path(__file__).resolve().parents[1] / "resources"


class StartupScreen(QSplashScreen):
    """Native text and a quiet light scan over the generated LiDAR artwork."""

    def __init__(self, theme="dark", *, auto_show=True):
        super().__init__()
        self.colors = get_theme(theme)
        self.progress = None
        self.displayed_progress = 0.0
        self.message = "正在启动工作台…"
        self._art = QPixmap(str(RESOURCES / "startup-art.png"))
        self._logo = QSvgRenderer(str(RESOURCES / "app.svg"), self)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_frame = 0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._animate)
        self.setWindowTitle("PointLab Studio · 正在启动")
        self._paint_frame()
        if auto_show:
            self.show()

    def set_progress(self, percent, message=None):
        self.progress = None if percent is None else max(0, min(100, int(percent)))
        if message is not None:
            self.message = str(message)
        if self.progress is not None:
            if self.progress < self.displayed_progress:
                self.displayed_progress = float(self.progress)
        self._paint_frame()

    def _animate(self):
        now = self._clock.elapsed()
        elapsed = min(0.1, (now - self._last_frame) / 1000)
        self._last_frame = now
        if self.progress is not None:
            self.displayed_progress += (self.progress - self.displayed_progress) * (
                1 - math.exp(-elapsed / 0.12))
            if self.progress - self.displayed_progress < 0.01:
                self.displayed_progress = float(self.progress)
        self._paint_frame()

    def _paint_frame(self):
        phase = self._clock.elapsed() / 1000
        ratio = self.devicePixelRatioF()
        canvas = QPixmap(round(780 * ratio), round(440 * ratio))
        canvas.setDevicePixelRatio(ratio)
        canvas.fill(QColor("#030d1b"))
        painter = QPainter(canvas)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing
                               | QPainter.SmoothPixmapTransform)
        if not self._art.isNull():
            # A slight overscan leaves room for a slow, subpixel camera drift.
            painter.drawPixmap(QRectF(-10 + math.sin(phase / 6) * 3,
                                      -6 + math.cos(phase / 7) * 2, 800, 452),
                               self._art, QRectF(self._art.rect()))
        glow = QRadialGradient(600, 240, 235)
        glow.setColorAt(0, QColor(31, 148, 232, round(5 + 3 * math.sin(phase / 2))))
        glow.setColorAt(1, QColor(31, 148, 232, 0))
        painter.fillRect(QRectF(0, 0, 780, 440), glow)
        sweep_x = 330 + (1 - math.cos(phase * math.tau / 9)) * 280
        sweep = QLinearGradient(sweep_x - 95, 0, sweep_x + 95, 0)
        sweep.setColorAt(0, QColor(71, 187, 255, 0))
        sweep.setColorAt(0.5, QColor(71, 187, 255, 13))
        sweep.setColorAt(1, QColor(71, 187, 255, 0))
        painter.fillRect(QRectF(0, 0, 780, 440), sweep)
        self._logo.render(painter, QRectF(44, 38, 34, 34))
        font = QFont()
        font.setFamilies(["Segoe UI Variable", "Segoe UI", "Ubuntu Sans", "Noto Sans", "DejaVu Sans"])
        font.setPixelSize(10)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 1.1)
        painter.setFont(font)
        painter.setPen(QColor("#98b3c9"))
        painter.drawText(90, 60, "POINT CLOUD WORKSTATION")
        title = QFont(font)
        title.setPixelSize(57)
        title.setWeight(QFont.DemiBold)
        title.setLetterSpacing(QFont.AbsoluteSpacing, -2)
        painter.setFont(title)
        painter.setPen(QColor("#eff7ff"))
        painter.drawText(41, 168, "PointLab")
        subtitle = QFont(font)
        subtitle.setPixelSize(26)
        subtitle.setWeight(QFont.Light)
        subtitle.setLetterSpacing(QFont.AbsoluteSpacing, 7)
        painter.setFont(subtitle)
        painter.drawText(46, 214, "STUDIO")
        caption = QFont(font)
        caption.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC"])
        caption.setLetterSpacing(QFont.AbsoluteSpacing, 0)
        caption.setPixelSize(12)
        painter.setFont(caption)
        painter.setPen(QColor("#9db5c9"))
        painter.drawText(46, 260, "点云浏览 · 处理 · 分析")
        painter.setPen(QColor("#d0e3f2"))
        painter.drawText(QRectF(44, 342, 540, 28), Qt.AlignLeft | Qt.AlignVCenter, self.message)
        painter.setFont(font)
        painter.setPen(QColor("#7d99b1"))
        painter.drawText(44, 415, f"v{__version__}")
        if self.progress is not None:
            painter.setPen(QColor("#d0e3f2"))
            painter.drawText(QRectF(610, 342, 126, 28), Qt.AlignRight | Qt.AlignVCenter,
                             f"{int(self.displayed_progress)}%")
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#183044"))
        painter.drawRoundedRect(QRectF(44, 385, 692, 3), 1.5, 1.5)
        if self.progress is None:
            painter.save()
            painter.setClipRect(QRectF(44, 385, 692, 3))
            # Keep activity visible throughout the cycle, including its turn.
            x = 44 + (692 - 208) * (1 - math.cos(phase * math.tau / 2.8)) / 2
            shimmer = QLinearGradient(x, 0, x + 208, 0)
            shimmer.setColorAt(0, QColor(53, 179, 247, 0))
            shimmer.setColorAt(0.5, QColor("#63ceff"))
            shimmer.setColorAt(1, QColor(53, 179, 247, 0))
            painter.fillRect(QRectF(x, 385, 208, 3), shimmer)
            painter.restore()
        elif self.displayed_progress > 0:
            painter.setBrush(QColor("#63ceff"))
            painter.drawRoundedRect(QRectF(44, 385, 692 * self.displayed_progress / 100, 3), 1.5, 1.5)
        painter.end()
        self.setPixmap(canvas)

    def showEvent(self, event):  # noqa: N802
        self._last_frame = self._clock.elapsed()
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def closeEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().closeEvent(event)


class StartupProcess:
    """Own the splash process so native imports can stay on the main thread.

    Open3D's first import must precede wgpu rendering on the GUI thread. A
    separate, Qt-only event loop keeps the artwork moving during that import.
    """

    def __init__(self, theme="dark"):
        self._process = None
        self._input = None
        self._local = None
        self._theme = theme
        self._closed = False
        # The frozen desktop entry handles this switch before importing the
        # workbench, so it never starts another controller or a GPU renderer.
        command = ([sys.executable] if getattr(sys, "frozen", False)
                   else [sys.executable, "-B", "-m", "ui.startup"])
        command.extend(["--splash-child", theme])
        reader = None
        try:
            startup = None
            if os.name == "nt":
                import msvcrt

                # A windowed bootloader can discard standard console streams.
                # Pass a dedicated pipe handle which survives those imports.
                reader, writer = os.pipe()
                self._input = os.fdopen(writer, "w", encoding="utf-8")
                handle = msvcrt.get_osfhandle(reader)
                os.set_handle_inheritable(handle, True)
                startup = subprocess.STARTUPINFO()
                startup.lpAttributeList = {"handle_list": [handle]}
                command.extend(["--splash-input", str(handle)])
            self._process = subprocess.Popen(
                command,
                cwd=str(Path(__file__).resolve().parents[1]),
                stdin=subprocess.DEVNULL if self._input is not None else subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", startupinfo=startup,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if self._input is None:
                self._input = self._process.stdin
        except OSError:
            if self._input is not None:
                self._input.close()
                self._input = None
            self._local = StartupScreen(theme)
        finally:
            if reader is not None:
                os.close(reader)

    def set_progress(self, percent, message=None):
        if self._closed:
            return
        if self._local is not None:
            self._local.set_progress(percent, message)
            QApplication.processEvents(QEventLoop.ExcludeUserInputEvents)
            return
        try:
            self._input.write(json.dumps({"progress": percent, "message": message}) + "\n")
            self._input.flush()
        except (OSError, ValueError):
            self.close()
            self._closed = False
            self._local = StartupScreen(self._theme)
            self._local.set_progress(percent, message)

    def finish(self, window):
        if self._local is not None:
            self._local.finish(window)
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._local is not None:
            self._local.close()
        if self._input is not None:
            try:
                self._input.close()
            except (OSError, ValueError):
                pass
        if self._process is not None:
            try:
                self._process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    self._process.kill()
                    self._process.wait()


def _open_child_input(input_handle=None):
    """Own the inherited input pipe without requiring console streams.

    The controller passes a dedicated handle on Windows. A direct child launch
    can instead supply stdin; duplicate its OS handle when Python hides stdin.
    """
    if input_handle is not None:
        if os.name == "nt":
            import msvcrt

            descriptor = msvcrt.open_osfhandle(input_handle, os.O_RDONLY | os.O_BINARY)
        else:
            descriptor = input_handle
        return os.fdopen(descriptor, "r", encoding="utf-8")
    if sys.stdin is not None:
        return sys.stdin
    if os.name == "nt":
        import _winapi
        import msvcrt

        current = _winapi.GetCurrentProcess()
        inherited = _winapi.GetStdHandle(_winapi.STD_INPUT_HANDLE)
        if inherited in (None, 0, -1):
            raise OSError("The splash parent input pipe is unavailable")
        handle = _winapi.DuplicateHandle(
            current, inherited, current, 0, False, _winapi.DUPLICATE_SAME_ACCESS,
        )
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            _winapi.CloseHandle(handle)
            raise
    else:
        descriptor = os.dup(0)
    return os.fdopen(descriptor, "r", encoding="utf-8")


def _run_splash_child(theme, *, input_handle=None):
    """Only the reader thread touches the pipe. Every Qt operation stays here."""
    commands = queue.Queue()
    parent_closed = threading.Event()

    def read_commands():
        try:
            with _open_child_input(input_handle) as stream:
                for line in stream:
                    try:
                        commands.put(json.loads(line))
                    except ValueError:
                        continue
        except (OSError, ValueError):
            pass
        finally:
            # Pipe EOF also arrives if the parent crashes or is terminated.
            parent_closed.set()
            commands.put(None)

    threading.Thread(target=read_commands, name="splash-input", daemon=True).start()
    app = QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    splash = StartupScreen(theme, auto_show=False)

    def drain_commands():
        while not commands.empty():
            command = commands.get_nowait()
            if command is None:
                splash.close()
                app.quit()
                return
            if isinstance(command, dict) and "progress" in command:
                splash.set_progress(command["progress"], command.get("message"))
                print(json.dumps({"progress": splash.progress}), flush=True)

    # Avoid flashing a late splash when startup finished before this process
    # loaded Qt. There is no minimum animation/display duration.
    drain_commands()
    if parent_closed.is_set():
        return 0
    splash.show()
    timer = QTimer(splash)
    timer.timeout.connect(drain_commands)
    timer.start(20)
    QTimer.singleShot(0, lambda: print(json.dumps({"ready": True}), flush=True))
    return app.exec()


if __name__ == "__main__":
    if len(sys.argv) in (3, 5) and sys.argv[1] == "--splash-child":
        handle = (int(sys.argv[4])
                  if len(sys.argv) == 5 and sys.argv[3] == "--splash-input" else None)
        sys.exit(_run_splash_child(sys.argv[2], input_handle=handle))
