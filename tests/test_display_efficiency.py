"""Avoid repeated full-cloud scans while retaining correct display values."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from core.color.colormaps import sample_colormap
from core.model.entities import CloudEntity
from ui.viewport.viewport import PointCloudViewport


class DisplayEfficiencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = PointCloudViewport()
        self.cloud = CloudEntity("bounds", xyz=np.array([[1, 2, 3], [4, 8, 12]], np.float32))

    def tearDown(self):
        self.view.stop()
        self.view.close()
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_refresh_shares_one_full_bounds_scan_for_lod_and_grid(self):
        with patch.object(self.cloud, "bounds", wraps=self.cloud.bounds) as scan:
            self.view.set_entities([self.cloud])
            np.testing.assert_array_equal(self.view._scene_bounds(), [[1, 2, 3], [4, 8, 12]])
            self.assertEqual(scan.call_count, 1)

    def test_refresh_recomputes_bounds_after_inplace_edit_and_replacement(self):
        self.view.set_entities([self.cloud])
        self.cloud.xyz += [10, 20, 30]
        self.view.refresh()
        np.testing.assert_array_equal(self.view._scene_bounds(), [[11, 22, 33], [14, 28, 42]])
        self.cloud.xyz = np.array([[-8, -9, -10], [-1, -2, -3]], np.float32)
        # A new array must not use the previous data's cached bounds.
        np.testing.assert_array_equal(self.view._scene_bounds(), [[-8, -9, -10], [-1, -2, -3]])
        self.view.refresh()
        self.cloud.visible = False
        self.view.refresh()
        self.assertIsNone(self.view._scene_bounds())
        self.view.set_entities([])
        self.assertFalse(self.view._lod_metrics)


class ColorInterpolationTests(unittest.TestCase):
    def test_known_stops_midpoints_shape_and_float32_output(self):
        values = np.array([[-1, .25, .5], [.75, 1, 2]], np.float32)
        expected = [[[.08, .20, .60], [.09, .525, .675], [.10, .85, .75]],
                    [[.54, .885, .485], [.98, .92, .22], [.98, .92, .22]]]
        colors = sample_colormap("default", values)
        np.testing.assert_allclose(colors, expected, atol=1e-7)
        self.assertEqual(colors.shape, (2, 3, 3))
        self.assertEqual(colors.dtype, np.float32)
        np.testing.assert_array_equal(values, [[-1, .25, .5], [.75, 1, 2]])

    def test_noncontiguous_empty_nan_and_unknown_palette(self):
        values = np.array([0, 99, .5, 99, 1, 99], np.float32)[::2]
        np.testing.assert_allclose(sample_colormap("grayscale", values),
                                   [[0, 0, 0], [.5, .5, .5], [1, 1, 1]])
        self.assertEqual(sample_colormap("default", np.empty((0, 2))).shape, (0, 2, 3))
        self.assertTrue(np.isnan(sample_colormap("default", np.array([np.nan]))).all())
        np.testing.assert_allclose(sample_colormap("unknown", np.array(0)), [.08, .20, .60])


if __name__ == "__main__":
    unittest.main()
