"""View gestures and color shortcuts without requiring a GPU device."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QWheelEvent
from ui.viewport.viewport import PointCloudViewport


class ViewControlTests(unittest.TestCase):
    def test_alt_slows_orbit_and_pan_without_jumping_on_release(self):
        for mode in ("orbit", "pan"):
            view = SimpleNamespace(_clip_drag=None, _sel_dragging=False,
                                   _drag=(mode, 0, 0), orbit_camera=Mock(), _pan_pixels=Mock())
            move = view.orbit_camera if mode == "orbit" else view._pan_pixels
            for position, modifiers, expected in (
                    ((10, 20), Qt.AltModifier, (2, 4)),
                    ((20, 40), Qt.NoModifier, (10, 20))):
                event = QMouseEvent(QEvent.MouseMove, QPointF(*position), QPointF(*position),
                                    Qt.NoButton, Qt.LeftButton, modifiers)
                PointCloudViewport.mouseMoveEvent(view, event)
                move.assert_called_with(*expected)

    def test_alt_slows_wheel_and_touchpad(self):
        for pixel, angle, normal in ((QPoint(), QPoint(0, 120), 1),
                                     (QPoint(0, -25), QPoint(), -0.5)):
            for modifiers, scale in ((Qt.NoModifier, 1), (Qt.AltModifier, 0.2)):
                view = SimpleNamespace(_clip_drag=None, zoom_camera=Mock())
                event = QWheelEvent(QPointF(), QPointF(), pixel, angle, Qt.NoButton,
                                    modifiers, Qt.NoScrollPhase, False)
                PointCloudViewport.wheelEvent(view, event)
                view.zoom_camera.assert_called_once_with(normal * scale)


if __name__ == "__main__":
    unittest.main()
