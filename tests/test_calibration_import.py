"""Calibration formats, independent replacement and archive boundaries."""
import json
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

import numpy as np

from core.calibration import calibration_from_dict, load_calibration
from core.calibration_files import read_calibration_bundle, combine_calibration
import core.calibration_files as calibration_files


OPENCV_INTRINSICS = """%YAML:1.0
---
camera_model: pinhole
image_width: 1920
image_height: 1080
distortion_model: radtan5
camera_matrix: !!opencv-matrix
  rows: 3
  cols: 3
  dt: d
  data: [1024.16049656, 0, 987.703296114, 0, 1026.05450715, 524.252914977, 0, 0, 1]
distortion_coefficients: !!opencv-matrix
  rows: 1
  cols: 5
  dt: d
  data: [-0.352860985495, 0.133510686619, -0.00147697112857, -0.000266857664536, -0.024786608746]
"""


class CalibrationImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = calibration_from_dict({
            "K": [[100, 2, 50], [0, 110, 40], [0, 0, 1]], "D": [],
            "width": 100, "height": 80, "T_camera_lidar": np.eye(4).tolist(),
        })

    def test_opencv_yaml_keeps_all_five_coefficients_and_full_precision(self):
        path = self.root / "相机.yaml"
        path.write_text(OPENCV_INTRINSICS, encoding="utf-8-sig")
        result = load_calibration(str(path))
        self.assertEqual(result.K[0, 0], 1024.16049656)
        self.assertEqual(result.D[-1], -0.024786608746)
        self.assertEqual(result.distortion_model, "plumb_bob")
        self.assertEqual((result.width, result.height), (1920, 1080))

    def test_consistent_forward_inverse_aliases_are_accepted(self):
        matrix = np.eye(4)
        matrix[:3, 3] = [.12, -.15, .03]
        data = {"T_camera_lidar": matrix.tolist(), "T_lidar_camera": np.linalg.inv(matrix).tolist(),
                "extrinsic_matrix": matrix.tolist(), "translation_unit": "metre",
                "transform_convention": "p_camera_optical = R_camera_lidar * p_lidar + t_camera_lidar"}
        result = calibration_from_dict(data, self.base)
        np.testing.assert_allclose(result.T_camera_lidar, matrix)
        data["T_lidar_camera"][0][3] += .1
        with self.assertRaisesRegex(ValueError, "conflict|inconsistent"):
            calibration_from_dict(data, self.base)

    def test_named_short_transforms_support_inverse_and_units(self):
        matrix = np.eye(4)
        matrix[0, 3] = -125
        result = calibration_from_dict({"T_L_C": matrix, "length_unit": "mm"}, self.base)
        self.assertAlmostEqual(result.T_camera_lidar[0, 3], .125)
        with self.assertRaises(ValueError):
            calibration_from_dict({"T_C_L": matrix, "translation_unit": "mm",
                                   "translation_units": "m"}, self.base)

    def test_unrelated_yaml_is_not_accepted_as_current_calibration(self):
        with self.assertRaisesRegex(ValueError, "calibration|Calibration"):
            calibration_from_dict({"targetRows": 5, "spacingMeters": .035}, self.base)

    def test_archive_identifies_separate_parameters_and_reports_candidate_status(self):
        path = self.root / "标定.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("相机/camera_intrinsics.yaml", OPENCV_INTRINSICS)
            archive.writestr("相机/circlegrid.yaml", "targetRows: 5\nspacingMeters: 0.035")
            archive.writestr("雷视/candidate.json", json.dumps({"T_C_L": np.eye(4).tolist(),
                              "status": "candidate", "validation_status": "failed",
                              "recommended_for_deployment": False}))
            archive.writestr("../escape.json", "{}")
        bundle = read_calibration_bundle(str(path))
        self.assertEqual(len(bundle.documents), 2)
        self.assertEqual(len(bundle.ignored), 2)
        intrinsics, extrinsics = bundle.documents
        self.assertTrue(intrinsics.has_intrinsics)
        self.assertFalse(intrinsics.has_extrinsics)
        self.assertIn("failed", extrinsics.status)
        result = combine_calibration(intrinsics, extrinsics)
        self.assertEqual(result.width, 1920)
        np.testing.assert_array_equal(result.T_camera_lidar, np.eye(4))
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_extrinsics_selection_does_not_replace_embedded_intrinsics(self):
        path = self.root / "both.json"
        matrix = np.eye(4)
        matrix[1, 3] = .7
        path.write_text(json.dumps({"K": np.eye(3).tolist(), "D": [0]*5,
                                   "T_camera_lidar": matrix.tolist()}), encoding="utf-8")
        document = read_calibration_bundle(str(path)).documents[0]
        result = combine_calibration(None, document, self.base)
        np.testing.assert_array_equal(result.K, self.base.K)
        self.assertEqual(len(result.D), 0)
        self.assertEqual(result.T_camera_lidar[1, 3], .7)
        np.testing.assert_array_equal(self.base.T_camera_lidar, np.eye(4))

    def test_intrinsics_selection_preserves_existing_extrinsics(self):
        path = self.root / "camera.yaml"
        path.write_text(OPENCV_INTRINSICS, encoding="utf-8")
        document = read_calibration_bundle(str(path)).documents[0]
        result = combine_calibration(document, None, self.base)
        self.assertEqual(result.K[0, 0], 1024.16049656)
        np.testing.assert_array_equal(result.T_camera_lidar, self.base.T_camera_lidar)

    def test_archive_rejects_oversized_calibration(self):
        path = self.root / "oversized.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("camera.yaml", "x" * (1024**2 + 1))
        with self.assertRaisesRegex(ValueError, "large|大|限"):
            read_calibration_bundle(str(path))

    def test_opencv_loader_does_not_enable_python_tags(self):
        path = self.root / "unsafe.yaml"
        path.write_text("%YAML:1.0\n---\nK: !!python/object/apply:builtins.print ['unsafe']", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_calibration(str(path), self.base)

    def test_partial_intrinsics_patches_are_importable(self):
        for data in ({"D": [.1, .2, 0, 0, .3]}, {"image_width": 200, "image_height": 160}):
            with self.subTest(data=data):
                path = self.root / "patch.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                document = read_calibration_bundle(str(path)).documents[0]
                self.assertTrue(document.has_intrinsics)
                result = combine_calibration(document, None, self.base)
                np.testing.assert_array_equal(result.K, self.base.K)
                if "D" in data:
                    np.testing.assert_array_equal(result.D, data["D"])
                else:
                    self.assertEqual((result.width, result.height), (200, 160))

    def test_invalid_explicit_direction_is_not_ignored_with_redundant_matrices(self):
        with self.assertRaisesRegex(ValueError, "direction"):
            calibration_from_dict({"T_C_L": np.eye(4), "T_L_C": np.eye(4),
                                   "transform_direction": "unknown"}, self.base)

    def test_bare_text_matrix_requires_explicit_type_direction_and_units(self):
        path = self.root / "matrix.csv"
        path.write_text("1,0,0,-125\n0,1,0,0\n0,0,1,0\n0,0,0,1\n", encoding="utf-8")
        document = read_calibration_bundle(str(path)).documents[0]
        self.assertTrue(document.requires_interpretation)
        with self.assertRaises(ValueError):
            combine_calibration(None, document, self.base)
        for kind, direction, units in ((None, "camera_to_lidar", "mm"),
                                       ("extrinsics", None, "mm"),
                                       ("extrinsics", "camera_to_lidar", None)):
            with self.subTest(kind=kind, direction=direction, units=units), self.assertRaises(ValueError):
                calibration_files.interpret_matrix(document, kind, direction, units)
        typed = calibration_files.interpret_matrix(document, "extrinsics", "camera_to_lidar", "mm")
        result = combine_calibration(None, typed, self.base)
        self.assertAlmostEqual(result.T_camera_lidar[0, 3], .125)
        np.testing.assert_array_equal(result.K, self.base.K)

    def test_intrinsic_matrix_requires_image_size_and_preserves_existing_extrinsics(self):
        path = self.root / "matrix.txt"
        path.write_text("# camera matrix\n100 2 50\n0 110 40\n0 0 1\n", encoding="utf-8")
        document = read_calibration_bundle(str(path)).documents[0]
        with self.assertRaises(ValueError):
            calibration_files.interpret_matrix(document, "intrinsics")
        typed = calibration_files.interpret_matrix(document, "intrinsics", image_size=(100, 80))
        result = combine_calibration(typed, None, self.base)
        np.testing.assert_array_equal(result.K, [[100, 2, 50], [0, 110, 40], [0, 0, 1]])
        np.testing.assert_array_equal(result.T_camera_lidar, self.base.T_camera_lidar)

    def test_toml_direct_and_caliscope_camera_sections_keep_precision(self):
        direct = self.root / "calibration.toml"
        direct.write_text('T_L_C = [[1,0,0,-125],[0,1,0,0],[0,0,1,0],[0,0,0,1]]\n'
                          'translation_units = "mm"\n', encoding="utf-8")
        result = combine_calibration(None, read_calibration_bundle(str(direct)).documents[0], self.base)
        self.assertAlmostEqual(result.T_camera_lidar[0, 3], .125)
        cameras = self.root / "cameras.toml"
        cameras.write_text('[cameras.2]\nsize = [100,80]\n'
                           'matrix = [[100.12345678912345,0,50],[0,110,40],[0,0,1]]\n'
                           'distortions = [0.1,0.2,0,0,0.3]\nfisheye = false\n', encoding="utf-8")
        result = combine_calibration(read_calibration_bundle(str(cameras)).documents[0], None, self.base)
        self.assertEqual(result.K[0, 0], 100.12345678912345)
        self.assertEqual(result.D[-1], .3)

    def test_numpy_named_arrays_and_bare_array_are_loaded_without_pickle(self):
        named = self.root / "named.npz"
        np.savez(named, T_lidar_camera=np.array([[1,0,0,-125],[0,1,0,0],[0,0,1,0],[0,0,0,1]]),
                 translation_units=np.array("mm"))
        result = combine_calibration(None, read_calibration_bundle(str(named)).documents[0], self.base)
        self.assertAlmostEqual(result.T_camera_lidar[0, 3], .125)
        bare = self.root / "bare.npy"
        np.save(bare, np.eye(4))
        self.assertTrue(read_calibration_bundle(str(bare)).documents[0].requires_interpretation)
        unsafe = self.root / "unsafe.npy"
        np.save(unsafe, np.array([{"T_camera_lidar": np.eye(4)}], dtype=object))
        with self.assertRaisesRegex(ValueError, "对象|pickle"):
            read_calibration_bundle(str(unsafe))

    def test_mixed_initial_text_imports_only_explicitly_named_transform(self):
        path = self.root / "initial_extrinsics.txt"
        text = ('[cameras.2]\nsize = [100,80]\n'
                'matrix = [[90,0,50],[0,90,40],[0,0,1]]\ndistortions = [0,0,0,0,0]\n'
                '# T_camera_lidar: p_camera = R * p_lidar + t\n'
                'rotation_matrix: [1,0,0,0,1,0,0,0,1]\ntranslation_m: [0.1,0.2,0.3]\n'
                'transform_4x4:\n1 0 0 0.1\n0 1 0 0.2\n0 0 1 0.3\n0 0 0 1\n')
        path.write_text(text, encoding="utf-8")
        document = read_calibration_bundle(str(path)).documents[0]
        result = combine_calibration(None, document, self.base)
        np.testing.assert_array_equal(result.K, self.base.K)
        np.testing.assert_allclose(result.T_camera_lidar[:3, 3], [.1, .2, .3])
        path.write_text(text.replace('# T_camera_lidar: p_camera = R * p_lidar + t', ''), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "方向|direction"):
            read_calibration_bundle(str(path))

    def test_archive_supports_new_formats_without_extracting(self):
        path = self.root / "matrices.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("bare.csv", "1,0,0,0\n0,1,0,0\n0,0,1,0\n0,0,0,1")
            archive.writestr("named.toml", 'T_C_L = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]')
        bundle = read_calibration_bundle(str(path))
        self.assertEqual(len(bundle.documents), 2)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_direct_loader_supports_named_numpy_and_rejects_unresolved_matrix(self):
        path = self.root / "named.npz"
        np.savez(path, T_C_L=np.eye(4))
        result = load_calibration(str(path), self.base)
        np.testing.assert_array_equal(result.T_camera_lidar, np.eye(4))
        path = self.root / "bare.npy"
        np.save(path, np.eye(4))
        with self.assertRaisesRegex(ValueError, "选择|方向|导入窗口"):
            load_calibration(str(path), self.base)

    def test_generic_transform_can_be_resolved_without_guessing_direction(self):
        path = self.root / "generic.json"
        path.write_text(json.dumps({"T": [[1,0,0,-125],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}),
                        encoding="utf-8")
        document = read_calibration_bundle(str(path)).documents[0]
        self.assertTrue(document.requires_interpretation)
        typed = calibration_files.interpret_matrix(document, "extrinsics", "camera_to_lidar", "mm")
        result = combine_calibration(None, typed, self.base)
        self.assertAlmostEqual(result.T_camera_lidar[0, 3], .125)

    def test_intrinsic_only_selection_ignores_unselected_ambiguous_transform(self):
        path = self.root / "generic.json"
        path.write_text(json.dumps({"K": [[150,0,50],[0,160,40],[0,0,1]],
                                   "T": np.eye(4).tolist()}), encoding="utf-8")
        document = read_calibration_bundle(str(path)).documents[0]
        result = combine_calibration(document, None, self.base)
        self.assertEqual(result.K[0, 0], 150)
        np.testing.assert_array_equal(result.T_camera_lidar, self.base.T_camera_lidar)

    def test_three_by_four_transform_requires_explicit_extrinsic_choice(self):
        path = self.root / "matrix.txt"
        path.write_text("1 0 0 10\n0 1 0 20\n0 0 1 30", encoding="utf-8")
        document = read_calibration_bundle(str(path)).documents[0]
        with self.assertRaises(ValueError):
            calibration_files.interpret_matrix(document, "intrinsics", image_size=(100, 80))
        typed = calibration_files.interpret_matrix(document, "extrinsics", "lidar_to_camera", "cm")
        result = combine_calibration(None, typed, self.base)
        np.testing.assert_allclose(result.T_camera_lidar, [[1,0,0,.1], [0,1,0,.2],
                                                         [0,0,1,.3], [0,0,0,1]])

    def test_numpy_header_and_compressed_payload_are_bounded_before_array_allocation(self):
        source = BytesIO()
        np.lib.format.write_array_header_1_0(source, {"descr": "<f8", "fortran_order": False,
                                                    "shape": (10**9, 10**9)})
        path = self.root / "oversized.npy"
        path.write_bytes(source.getvalue())
        with self.assertRaisesRegex(ValueError, "1 MB|限制"):
            read_calibration_bundle(str(path))
        path = self.root / "compressed.npz"
        np.savez_compressed(path, T_camera_lidar=np.zeros((400, 400)))
        self.assertLess(path.stat().st_size, 1024**2)
        with self.assertRaisesRegex(ValueError, "限制"):
            read_calibration_bundle(str(path))


if __name__ == "__main__":
    unittest.main()
