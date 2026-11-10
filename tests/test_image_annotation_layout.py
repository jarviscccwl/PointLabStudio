"""Projected labels remain legible inside the image at its edges."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication
from ui.widgets.data_view import ImageView


def test_right_edge_label_moves_inside_the_image_without_changing_the_source():
    app = QApplication.instance() or QApplication([])
    source = np.zeros((100, 200, 3), np.uint8)
    view = ImageView()
    view.set_image(source)
    view.set_projected_boxes([{"id": "stable-id", "category": "树木", "name": "路边树",
                              "segments": np.array([[[195, 50], [199, 70]]])}])
    snapshot = view.snapshot_rgb()
    # Label must shift left; drawing at x=195 clips almost all of its text.
    assert np.count_nonzero(snapshot[20:50, 30:185]) > 100
    assert not source.any()
    view.close()
    view.deleteLater()
    app.processEvents()
