import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
import numpy as np
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from core.calibration import calibration_from_dict, calibration_to_dict
from core.calibration_files import CalibrationBundle, CalibrationDocument
from ui.widgets.calibration_editor import IntrinsicsDialog, CalibrationImportDialog
import ui.widgets.calibration_editor as calibration_editor


class CalibrationEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.base = calibration_from_dict({
            "K": [[1024.16049656, 0.01234567890123456, 987.703296114],
                  [0, 1026.05450715, 524.252914977], [0, 0, 1]],
            "D": [-.352860985495, .133510686619, -.00147697112857,
                  -.000266857664536, -.024786608746],
            "width": 1920, "height": 1080, "T_camera_lidar": np.eye(4).tolist(),
        })

    def editor(self):
        dialog = IntrinsicsDialog(self.base)
        self.addCleanup(dialog.close)
        return dialog

    def test_open_and_apply_without_changes_preserves_full_precision(self):
        dialog = self.editor()
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(calibration_to_dict(dialog.calibration), calibration_to_dict(self.base))

    def test_edit_applies_once_and_preserves_extrinsics_without_mutation(self):
        dialog = self.editor()
        dialog.fields["fx"].setText("1100.1234567891234")
        self.assertEqual(self.base.K[0, 0], 1024.16049656)
        dialog.accept()
        self.assertEqual(dialog.calibration.K[0, 0], 1100.1234567891234)
        np.testing.assert_array_equal(dialog.calibration.T_camera_lidar, self.base.T_camera_lidar)
        self.assertIsNot(dialog.calibration.T_camera_lidar, self.base.T_camera_lidar)

    def test_cancel_and_restore_do_not_change_original(self):
        dialog = self.editor()
        dialog.fields["cx"].setText("3")
        dialog.restore_values()
        self.assertEqual(float(dialog.fields["cx"].text()), self.base.K[0, 2])
        dialog.reject()
        self.assertIsNone(dialog.calibration)

    def test_invalid_values_stay_in_dialog_and_explain_recovery(self):
        for field, value in (("fx", "0"), ("fy", "nan"), ("width", "100.5"), ("cx", "abc")):
            with self.subTest(field=field):
                dialog = self.editor()
                dialog.fields[field].setText(value)
                dialog.accept()
                self.assertNotEqual(dialog.result(), QDialog.Accepted)
                self.assertIsNone(dialog.calibration)
                self.assertTrue(dialog.error.text())

    def test_model_switch_keeps_values_when_switching_back(self):
        dialog = self.editor()
        original = dialog.model.currentIndex()
        dialog.model.setCurrentIndex(dialog.model.findData("equidistant:4"))
        self.assertEqual(list(dialog.coefficients), ["k1", "k2", "k3", "k4"])
        dialog.model.setCurrentIndex(original)
        self.assertEqual(float(dialog.coefficients["p1"].text()), self.base.D[2])
        dialog.accept()
        np.testing.assert_array_equal(dialog.calibration.D, self.base.D)

    def test_empty_editor_does_not_invent_focal_lengths(self):
        dialog = IntrinsicsDialog(None, image_size=(1920, 1080))
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.fields["width"].text(), "1920")
        self.assertEqual(dialog.fields["fx"].text(), "")
        dialog.accept()
        self.assertIsNone(dialog.calibration)

    def test_multiple_extrinsics_require_a_choice_and_keep_status_visible(self):
        intrinsic = CalibrationDocument("camera.yaml", calibration_to_dict(self.base))
        intrinsic.data.pop("T_camera_lidar")
        first = CalibrationDocument("candidate.json", {"T_C_L": np.eye(4).tolist(),
                                                     "validation_status": "failed"})
        second = CalibrationDocument("chosen.json", {"T_C_L": np.eye(4).tolist()})
        dialog = CalibrationImportDialog(CalibrationBundle([intrinsic, first, second]), self.base)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.apply_button.isEnabled())
        dialog.extrinsics.setCurrentIndex(dialog.extrinsics.findData(1))
        self.assertIn("failed", dialog.preview.text())
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.accept()
        self.assertIsNotNone(dialog.calibration)
        self.assertIn("candidate.json", dialog.origin)

    def test_extrinsic_file_with_embedded_K_defaults_to_preserving_manual_intrinsics(self):
        data = calibration_to_dict(self.base)
        data["K"][0][0] = 800
        document = CalibrationDocument("jy_extrinsics.yaml", data)
        dialog = CalibrationImportDialog(CalibrationBundle([document]), self.base)
        self.addCleanup(dialog.close)
        self.assertIsNone(dialog.intrinsics.currentData())
        self.assertEqual(dialog.extrinsics.currentData(), 0)
        dialog.accept()
        self.assertEqual(dialog.calibration.K[0, 0], self.base.K[0, 0])

    def test_reinterpreting_another_matrix_preserves_explicit_keep_extrinsics(self):
        replacement = np.eye(4)
        replacement[0, 3] = 2.5
        extrinsics = CalibrationDocument("replacement.json", {"T_C_L": replacement.tolist()})
        intrinsics = CalibrationDocument("camera.csv", {}, np.array(
            [[150, 0, 50], [0, 160, 40], [0, 0, 1]], dtype=float))
        dialog = CalibrationImportDialog(CalibrationBundle([extrinsics, intrinsics]), self.base)
        self.addCleanup(dialog.close)
        self.assertEqual(dialog.extrinsics.currentData(), 0)
        dialog.extrinsics.setCurrentIndex(dialog.extrinsics.findData(None))
        controls = dialog.matrix_controls[1]
        controls["type"].setCurrentIndex(controls["type"].findData("intrinsics"))
        self.assertIsNone(dialog.extrinsics.currentData())
        dialog.intrinsics.setCurrentIndex(dialog.intrinsics.findData(1))
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.calibration.K[0, 0], 150)
        np.testing.assert_array_equal(dialog.calibration.T_camera_lidar, self.base.T_camera_lidar)

    def test_explicit_keep_survives_temporarily_removing_only_candidate(self):
        replacement = np.eye(4)
        replacement[0, 3] = 2.5
        for kind, matrix in (("intrinsics", np.array([[150, 0, 50], [0, 160, 40], [0, 0, 1]], dtype=float)),
                             ("extrinsics", replacement)):
            with self.subTest(component=kind):
                document = CalibrationDocument("matrix.csv", {}, matrix)
                dialog = CalibrationImportDialog(CalibrationBundle([document]), self.base)
                self.addCleanup(dialog.close)
                controls = dialog.matrix_controls[0]
                controls["type"].setCurrentIndex(controls["type"].findData(kind))
                controls["direction"].setCurrentIndex(controls["direction"].findData("lidar_to_camera"))
                controls["units"].setCurrentIndex(controls["units"].findData("m"))
                choice = getattr(dialog, kind)
                self.assertEqual(choice.currentData(), 0)
                choice.setCurrentIndex(choice.findData(None))
                controls["type"].setCurrentIndex(controls["type"].findData(None))
                controls["type"].setCurrentIndex(controls["type"].findData(kind))
                self.assertIsNone(choice.currentData())
                self.assertFalse(dialog.apply_button.isEnabled())
                choice.setCurrentIndex(choice.findData(0))
                dialog.accept()
                self.assertEqual(dialog.result(), QDialog.Accepted)
                if kind == "intrinsics":
                    self.assertEqual(dialog.calibration.K[0, 0], 150)
                else:
                    self.assertEqual(dialog.calibration.T_camera_lidar[0, 3], 2.5)

    def test_eight_coefficient_editor_keeps_apply_visible_in_short_window(self):
        dialog = self.editor()
        dialog.model.setCurrentIndex(dialog.model.findData("rational_polynomial:8"))
        dialog.resize(720, 420)
        dialog.show()
        self.app.processEvents()
        self.assertLessEqual(dialog.height(), 420)
        buttons = dialog.findChild(QDialogButtonBox)
        apply_button = buttons.button(QDialogButtonBox.Ok)
        bottom_right = apply_button.mapTo(dialog, QPoint(apply_button.width() - 1,
                                                         apply_button.height() - 1))
        self.assertTrue(dialog.rect().contains(bottom_right))
        self.assertTrue(apply_button.isVisible())

    def test_coefficient_fields_show_sign_and_entire_supplied_precision(self):
        dialog = self.editor()
        dialog.show()
        self.app.processEvents()
        for edit in dialog.coefficients.values():
            self.assertEqual(edit.cursorPosition(), 0)
            self.assertGreater(edit.width() - 24, edit.fontMetrics().horizontalAdvance(edit.text()))

    def test_extrinsics_no_edit_and_mode_switch_preserve_exact_matrix_and_intrinsics(self):
        self.base.T_camera_lidar = np.array([
            [.047197566, -.998884377, .001546525, .086788878],
            [.019444205, -.000629215, -.999810746, -.157110130],
            [.998696307, .047218704, .019392815, -.106791921], [0, 0, 0, 1]])
        dialog = calibration_editor.ExtrinsicsDialog(self.base)
        self.addCleanup(dialog.close)
        dialog.mode.setCurrentIndex(dialog.mode.findData("matrix"))
        dialog.mode.setCurrentIndex(dialog.mode.findData("pose"))
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(calibration_to_dict(dialog.calibration), calibration_to_dict(self.base))

    def test_extrinsics_pose_edits_apply_in_metres_degrees_without_mutating_base(self):
        original = calibration_to_dict(self.base)
        dialog = calibration_editor.ExtrinsicsDialog(self.base)
        self.addCleanup(dialog.close)
        dialog.fields["tx"].setText("0.123456789123456")
        dialog.fields["yaw"].setText("90")
        dialog.accept()
        np.testing.assert_allclose(dialog.calibration.T_camera_lidar,
                                   [[0,-1,0,.123456789123456], [1,0,0,0],
                                    [0,0,1,0], [0,0,0,1]], atol=1e-14)
        self.assertEqual(dialog.calibration.T_camera_lidar[0, 3], .123456789123456)
        self.assertEqual(calibration_to_dict(self.base), original)
        np.testing.assert_array_equal(dialog.calibration.K, self.base.K)
        np.testing.assert_array_equal(dialog.calibration.D, self.base.D)

    def test_extrinsics_invalid_matrix_and_cancel_never_change_base(self):
        original = calibration_to_dict(self.base)
        dialog = calibration_editor.ExtrinsicsDialog(self.base)
        self.addCleanup(dialog.close)
        dialog.mode.setCurrentIndex(dialog.mode.findData("matrix"))
        dialog.matrix.setPlainText("-1 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1")
        dialog.accept()
        self.assertIsNone(dialog.calibration)
        self.assertTrue(dialog.error.text())
        dialog.mode.setCurrentIndex(dialog.mode.findData("pose"))
        self.assertEqual(dialog.mode.currentData(), "matrix")
        dialog.reject()
        self.assertIsNone(dialog.calibration)
        self.assertEqual(calibration_to_dict(self.base), original)

    def test_missing_extrinsics_do_not_silently_apply_identity(self):
        self.base.T_camera_lidar = None
        dialog = calibration_editor.ExtrinsicsDialog(self.base)
        self.addCleanup(dialog.close)
        dialog.accept()
        self.assertIsNone(dialog.calibration)
        dialog.mode.setCurrentIndex(dialog.mode.findData("matrix"))
        dialog.matrix.setPlainText("1 0 0 0\n0 1 0 0\n0 0 1 0\n0 0 0 1")
        dialog.accept()
        np.testing.assert_array_equal(dialog.calibration.T_camera_lidar, np.eye(4))

    def test_raw_import_requires_type_direction_and_unit_before_applying(self):
        document = CalibrationDocument("matrix.csv", {}, np.array(
            [[1,0,0,-125], [0,1,0,0], [0,0,1,0], [0,0,0,1]], dtype=float))
        dialog = CalibrationImportDialog(CalibrationBundle([document]), self.base)
        self.addCleanup(dialog.close)
        self.assertFalse(dialog.apply_button.isEnabled())
        options = dialog.matrix_controls[0]
        options["type"].setCurrentIndex(options["type"].findData("extrinsics"))
        self.assertFalse(dialog.apply_button.isEnabled())
        options["direction"].setCurrentIndex(options["direction"].findData("camera_to_lidar"))
        self.assertFalse(dialog.apply_button.isEnabled())
        options["units"].setCurrentIndex(options["units"].findData("mm"))
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.accept()
        self.assertAlmostEqual(dialog.calibration.T_camera_lidar[0, 3], .125)
        np.testing.assert_array_equal(dialog.calibration.K, self.base.K)

    def test_import_intrinsics_does_not_require_options_for_unselected_external_transform(self):
        document = CalibrationDocument("generic.json", {
            "K": [[150,0,50], [0,160,40], [0,0,1]], "T": np.eye(4).tolist()})
        dialog = CalibrationImportDialog(CalibrationBundle([document]), self.base)
        self.addCleanup(dialog.close)
        dialog.intrinsics.setCurrentIndex(dialog.intrinsics.findData(0))
        dialog.extrinsics.setCurrentIndex(dialog.extrinsics.findData(None))
        self.assertTrue(dialog.apply_button.isEnabled())
        dialog.accept()
        self.assertEqual(dialog.calibration.K[0, 0], 150)
        np.testing.assert_array_equal(dialog.calibration.T_camera_lidar, self.base.T_camera_lidar)


if __name__ == "__main__":
    unittest.main()
