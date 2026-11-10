"""CPU-only sequence composition and actual GIF/MP4 encoding checks."""
from __future__ import annotations

import gc
import math
import os
from pathlib import Path
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch
import weakref

import cv2
import numpy as np
from PIL import Image

from core import sequence_export
from core.sequence_export import SequenceWriter, compose_views


def solid(color, size=(96, 64)):
    width, height = size
    return np.full((height, width, 3), color, dtype=np.uint8)


class SequenceCompositionTests(unittest.TestCase):
    def test_paired_preserves_aspect_ratio_and_cloud_is_on_the_left(self):
        cloud = solid((255, 0, 0), (80, 40))
        image = solid((0, 255, 0), (40, 80))
        original = cloud.copy()
        result = compose_views(cloud, image, size=(160, 100))
        self.assertEqual(result.shape, (100, 160, 3))
        self.assertEqual(result.dtype, np.uint8)
        red = np.all(result == (255, 0, 0), axis=2)
        green = np.all(result == (0, 255, 0), axis=2)
        self.assertEqual(int(red.sum()), 80 * 40)
        self.assertEqual(int(green.sum()), 50 * 100)
        np.testing.assert_array_equal(result[30:70, :80], cloud)
        self.assertTrue(green[:, 95:145].all())
        np.testing.assert_array_equal(cloud, original)

    def test_single_views_fill_available_area_without_using_the_other_frame(self):
        cloud = solid((255, 0, 0))
        image = solid((0, 0, 255))
        for layout, expected in (("cloud", cloud), ("image", image)):
            with self.subTest(layout=layout):
                actual = compose_views(cloud, image, layout, (96, 64))
                np.testing.assert_array_equal(actual, expected)

    def test_missing_frame_is_a_fresh_neutral_placeholder(self):
        cloud, image = solid((255, 0, 0)), solid((0, 255, 0))
        compose_views(cloud, image, size=(192, 64))
        current = compose_views(cloud, None, size=(192, 64))
        np.testing.assert_array_equal(current[:, :96], cloud)
        missing = current[:, 96:]
        self.assertTrue(np.all(missing[..., 0] == missing[..., 1]))
        self.assertTrue(np.all(missing[..., 1] == missing[..., 2]))
        self.assertGreater(int(np.ptp(missing)), 0)
        self.assertFalse(np.any(np.all(missing == (0, 255, 0), axis=2)))

    def test_odd_width_and_small_placeholders_keep_requested_dimensions(self):
        for size in ((193, 65), (2, 1)):
            with self.subTest(size=size):
                result = compose_views(None, None, "paired", size)
                self.assertEqual(result.shape, (size[1], size[0], 3))

    def test_rejects_invalid_layout_size_and_non_rgb_input(self):
        for kwargs in (
            {"layout": "stacked"}, {"size": (0, 50)}, {"size": (1, 50)},
            {"size": (40.5, 50)}, {"size": (True, 50)},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                compose_views(None, None, **kwargs)
        for frame in (np.zeros((4, 5), np.uint8), np.zeros((4, 5, 4), np.uint8),
                      np.zeros((4, 5, 3), np.float32), np.zeros((0, 5, 3), np.uint8)):
            with self.subTest(shape=frame.shape), self.assertRaises(ValueError):
                compose_views(frame, None)


class SequenceWriterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pointlab-sequence-test-")
        self.directory = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def writer(self, suffix, fps=30, size=(96, 64)):
        writer = SequenceWriter(str(self.directory / ("融合结果" + suffix)), fps, size)
        self.addCleanup(writer.abort)
        return writer

    def test_gif_decodes_every_frame_in_order_including_identical_frames(self):
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                  (0, 0, 255), (255, 255, 0), (0, 255, 255)]
        writer = self.writer(".gif")
        for color in colors:
            writer.append(solid(color))
        writer.close()
        path = self.directory / "融合结果.gif"
        with Image.open(path) as animation:
            self.assertEqual(animation.size, (96, 64))
            self.assertEqual(animation.n_frames, len(colors))
            self.assertEqual(animation.info["loop"], 0)
            durations = []
            for index, color in enumerate(colors):
                animation.seek(index)
                durations.append(animation.info["duration"])
                np.testing.assert_array_equal(np.asarray(animation.convert("RGB")), solid(color))
            self.assertEqual(durations, [30, 40, 30, 30, 40, 30])
        self.assertEqual(list(self.directory.iterdir()), [path])

    def test_mp4_decodes_exact_frame_count_dimensions_colors_and_fps(self):
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255),
                  (0, 0, 255), (255, 255, 0)]
        writer = self.writer(".mp4", fps=20)
        for color in colors:
            writer.append(solid(color))
        writer.close()
        path = self.directory / "融合结果.mp4"
        capture = cv2.VideoCapture(str(path))
        try:
            self.assertTrue(capture.isOpened())
            self.assertAlmostEqual(capture.get(cv2.CAP_PROP_FPS), 20, places=2)
            decoded = []
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                decoded.append(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        finally:
            capture.release()
        self.assertEqual(len(decoded), len(colors))
        for frame, color in zip(decoded, colors):
            self.assertEqual(frame.shape, (64, 96, 3))
            np.testing.assert_allclose(frame, solid(color), atol=8)
        self.assertEqual(list(self.directory.iterdir()), [path])

    def test_reused_noncontiguous_input_buffer_does_not_change_written_frames(self):
        for suffix in (".gif", ".mp4"):
            with self.subTest(suffix=suffix):
                writer = self.writer(suffix)
                buffer = np.zeros((64, 192, 3), np.uint8)
                view = buffer[:, ::2, :]
                view[:] = (255, 0, 0)
                writer.append(view)
                view[:] = (0, 255, 0)
                writer.append(view)
                view[:] = 0
                writer.close()
                path = self.directory / ("融合结果" + suffix)
                if suffix == ".gif":
                    with Image.open(path) as animation:
                        first = np.array(animation.convert("RGB"))
                        animation.seek(1)
                        second = np.array(animation.convert("RGB"))
                else:
                    capture = cv2.VideoCapture(str(path))
                    try:
                        first_ok, first = capture.read()
                        second_ok, second = capture.read()
                        self.assertTrue(first_ok and second_ok)
                        first = cv2.cvtColor(first, cv2.COLOR_BGR2RGB)
                        second = cv2.cvtColor(second, cv2.COLOR_BGR2RGB)
                    finally:
                        capture.release()
                np.testing.assert_allclose(first, solid((255, 0, 0)), atol=8)
                np.testing.assert_allclose(second, solid((0, 255, 0)), atol=8)

    def test_completed_file_replaces_existing_only_after_successful_close(self):
        for suffix in (".gif", ".mp4"):
            with self.subTest(suffix=suffix):
                path = self.directory / ("融合结果" + suffix)
                path.write_bytes(b"existing result")
                writer = self.writer(suffix)
                writer.append(solid((255, 0, 0)))
                self.assertEqual(path.read_bytes(), b"existing result")
                writer.close()
                self.assertNotEqual(path.read_bytes(), b"existing result")
                writer.close()

    def test_abort_and_uncommitted_close_preserve_original_and_remove_temporary_files(self):
        for suffix in (".gif", ".mp4"):
            for cancel in (lambda writer: writer.abort(), lambda writer: writer.close(False)):
                with self.subTest(suffix=suffix, cancel=cancel):
                    path = self.directory / ("融合结果" + suffix)
                    path.write_bytes(b"existing result")
                    writer = self.writer(suffix)
                    writer.append(solid((255, 0, 0)))
                    cancel(writer)
                    writer.abort()
                    self.assertEqual(path.read_bytes(), b"existing result")
                    self.assertFalse(any(p.name.startswith(".") for p in self.directory.iterdir()))
                    with self.assertRaises(RuntimeError):
                        writer.append(solid((0, 255, 0)))
                    with self.assertRaises(RuntimeError):
                        writer.close()

    def test_empty_sequence_does_not_publish_and_cleans_up(self):
        for suffix in (".gif", ".mp4"):
            with self.subTest(suffix=suffix):
                writer = self.writer(suffix)
                with self.assertRaisesRegex(ValueError, "帧"):
                    writer.close()
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_invalid_append_aborts_and_preserves_existing_destination(self):
        path = self.directory / "融合结果.gif"
        path.write_bytes(b"existing result")
        writer = self.writer(".gif")
        writer.append(solid((255, 0, 0)))
        with self.assertRaisesRegex(ValueError, "尺寸"):
            writer.append(solid((0, 255, 0), size=(95, 64)))
        self.assertEqual(path.read_bytes(), b"existing result")
        self.assertEqual(list(self.directory.iterdir()), [path])
        with self.assertRaises(RuntimeError):
            writer.append(solid((0, 255, 0)))

    def test_encoder_failure_has_chinese_context_and_never_publishes_partial_frames(self):
        path = self.directory / "融合结果.gif"
        path.write_bytes(b"existing result")
        writer = self.writer(".gif")
        writer.append(solid((255, 0, 0)))
        # Inject only an encoder failure: real file writing, cleanup, and atomic
        # publication stay in use. Reproducing a codec failure otherwise depends
        # on the installed backend and available disk space.
        with patch("PIL.GifImagePlugin.getdata", side_effect=ValueError("encode failed")):
            with self.assertRaisesRegex(ValueError, "写入.*失败"):
                writer.append(solid((0, 255, 0)))
        self.assertEqual(path.read_bytes(), b"existing result")
        self.assertEqual(list(self.directory.iterdir()), [path])

    def test_invalid_parameters_fail_before_creating_any_output(self):
        cases = [(".avi", 30, (96, 64)), (".gif", 0, (96, 64)),
                 (".gif", math.nan, (96, 64)), (".gif", math.inf, (96, 64)),
                 (".gif", 101, (96, 64)), (".gif", 0.0001, (96, 64)),
                 (".gif", 30, (65536, 64)), (".gif", 30, (0, 64)),
                 (".gif", 30, (96.5, 64)), (".mp4", 30, (95, 64)),
                 (".mp4", 30, (96, 63))]
        for suffix, fps, size in cases:
            with self.subTest(suffix=suffix, fps=fps, size=size), self.assertRaises(ValueError):
                self.writer(suffix, fps, size)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_open_and_publish_failures_have_chinese_errors_and_leave_no_partial_result(self):
        with self.assertRaisesRegex(RuntimeError, "创建|打开|导出"):
            SequenceWriter(str(self.directory / "missing" / "test.gif"), 30, (96, 64))
        writer = self.writer(".gif")
        writer.append(solid((255, 0, 0)))
        destination = self.directory / "融合结果.gif"
        destination.mkdir()
        marker = destination / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "保存|发布|导出"):
            writer.close()
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
        self.assertEqual(list(self.directory.iterdir()), [destination])

    def test_gif_streams_without_retaining_input_frames_or_growing_with_sequence_length(self):
        writer = self.writer(".gif", size=(128, 96))
        rng = np.random.default_rng(42)
        for _ in range(4):
            writer.append(rng.integers(0, 256, (96, 128, 3), dtype=np.uint8))
        partial = next(self.directory.iterdir())
        initial_size = partial.stat().st_size
        gc.collect()
        tracemalloc.start()
        try:
            baseline, _ = tracemalloc.get_traced_memory()
            references = []
            for _ in range(160):
                frame = rng.integers(0, 256, (96, 128, 3), dtype=np.uint8)
                references.append(weakref.ref(frame))
                writer.append(frame)
            del frame
            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
            self.assertTrue(all(reference() is None for reference in references))
            self.assertLess(current - baseline, 1024 * 1024)
            self.assertLess(peak - baseline, 3 * 1024 * 1024)
        finally:
            tracemalloc.stop()
        self.assertGreater(partial.stat().st_size, initial_size)
        writer.close()
        with Image.open(self.directory / "融合结果.gif") as animation:
            self.assertEqual(animation.n_frames, 164)


class ExportFileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pointlab-export-files-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.staging = self.directory / ".staging"
        self.staging.mkdir()

    def test_selected_formats_use_the_chosen_name_and_one_based_frame_suffixes(self):
        cases = [
            ("旅行.gif", "gif", 1, 3, ["旅行.gif"]),
            ("旅行.mp4", "mp4", 1, 3, ["旅行.mp4"]),
            ("旅行.png", "png", 2, 2, ["旅行.png"]),
            ("旅行.ply", "ply", 2, 2, ["旅行.ply"]),
            ("旅行.pcd", "pcd", 2, 2, ["旅行.pcd"]),
            ("旅行.png", "png", 1, 3,
             ["旅行_000002.png", "旅行_000003.png", "旅行_000004.png"]),
            ("旅行.pcd", "pcd", 1, 2, ["旅行_000002.pcd", "旅行_000003.pcd"]),
            ("旅行.png", "png_ply", 2, 2, ["旅行.png", "旅行.ply"]),
            ("旅行.png", "png_ply", 1, 2,
             ["旅行_000002.png", "旅行_000002.ply", "旅行_000003.png", "旅行_000003.ply"]),
            ("旅行.v2", "gif", 0, 1, ["旅行.v2.gif"]),
            ("旅行", "png", 0, 0, ["旅行.png"]),
        ]
        for name, fmt, first, last, expected in cases:
            with self.subTest(name=name, format=fmt, first=first, last=last):
                paths = sequence_export.export_file_paths(self.directory / name, fmt, first, last)
                self.assertEqual(list(paths), [self.directory / value for value in expected])

    def test_publish_only_creates_requested_files_without_a_result_directory(self):
        paths = [self.directory / "chosen_000002.png", self.directory / "chosen_000003.png"]
        for index, path in enumerate(paths):
            (self.staging / path.name).write_bytes(bytes([index]))
        published = sequence_export.publish_export_files(self.staging, paths)
        self.assertTrue(published)
        self.assertEqual([path.read_bytes() for path in paths], [b"\x00", b"\x01"])
        self.assertEqual(set(self.directory.iterdir()), {self.staging, *paths})

    def test_existing_files_reject_publication_by_default_before_any_output_changes(self):
        paths = [self.directory / "chosen_000002.png", self.directory / "chosen_000003.png"]
        paths[1].write_bytes(b"original")
        for path in paths:
            (self.staging / path.name).write_bytes(b"new")
        with self.assertRaises(FileExistsError):
            sequence_export.publish_export_files(self.staging, paths)
        self.assertFalse(paths[0].exists())
        self.assertEqual(paths[1].read_bytes(), b"original")

    def test_explicit_overwrite_replaces_only_requested_files_after_success(self):
        path, unrelated = self.directory / "chosen.gif", self.directory / "keep.txt"
        path.write_bytes(b"original")
        unrelated.write_bytes(b"unrelated")
        (self.staging / path.name).write_bytes(b"complete")
        self.assertTrue(sequence_export.publish_export_files(self.staging, [path], overwrite=True))
        self.assertEqual(path.read_bytes(), b"complete")
        self.assertEqual(unrelated.read_bytes(), b"unrelated")

    def test_partial_publish_failure_restores_existing_files_and_removes_new_outputs(self):
        paths = [self.directory / "existing.png", self.directory / "new.png", self.directory / "blocked.png"]
        paths[0].write_bytes(b"original")
        for path in paths:
            (self.staging / path.name).write_bytes(b"complete")
        replace = os.replace

        def fail_final(source, destination):
            if Path(destination) == paths[2]:
                raise OSError("injected publication failure")
            return replace(source, destination)

        with patch("core.sequence_export.os.replace", side_effect=fail_final):
            with self.assertRaisesRegex(OSError, "publication failure"):
                sequence_export.publish_export_files(self.staging, paths, overwrite=True)
        self.assertEqual(paths[0].read_bytes(), b"original")
        self.assertFalse(paths[1].exists())
        self.assertFalse(paths[2].exists())

    def test_cancel_during_publication_restores_the_original_set(self):
        paths = [self.directory / "existing.png", self.directory / "new.png"]
        paths[0].write_bytes(b"original")
        for path in paths:
            (self.staging / path.name).write_bytes(b"complete")

        def cancelled():
            return paths[0].read_bytes() == b"complete"

        self.assertFalse(sequence_export.publish_export_files(
            self.staging, paths, overwrite=True, cancelled=cancelled))
        self.assertEqual(paths[0].read_bytes(), b"original")
        self.assertFalse(paths[1].exists())

    def test_missing_staged_file_fails_before_replacing_any_existing_output(self):
        paths = [self.directory / "existing.png", self.directory / "missing.png"]
        paths[0].write_bytes(b"original")
        (self.staging / paths[0].name).write_bytes(b"complete")
        with self.assertRaises(FileNotFoundError):
            sequence_export.publish_export_files(self.staging, paths, overwrite=True)
        self.assertEqual(paths[0].read_bytes(), b"original")
        self.assertFalse(paths[1].exists())

    def test_new_collision_during_publication_is_preserved_and_earlier_outputs_roll_back(self):
        paths = [self.directory / "first.png", self.directory / "late.png"]
        for path in paths:
            (self.staging / path.name).write_bytes(b"complete")

        def concurrent_writer():
            if paths[0].exists() and not paths[1].exists():
                paths[1].write_bytes(b"someone else's file")
            return False

        with self.assertRaises(FileExistsError):
            sequence_export.publish_export_files(self.staging, paths, cancelled=concurrent_writer)
        self.assertFalse(paths[0].exists())
        self.assertEqual(paths[1].read_bytes(), b"someone else's file")

    def test_cancellation_after_last_file_still_restores_existing_result(self):
        path = self.directory / "chosen.gif"
        path.write_bytes(b"original")
        (self.staging / path.name).write_bytes(b"complete")
        self.assertFalse(sequence_export.publish_export_files(
            self.staging, [path], overwrite=True,
            cancelled=lambda: path.read_bytes() == b"complete"))
        self.assertEqual(path.read_bytes(), b"original")

    def test_failed_restore_retains_the_original_backup_and_reports_its_location(self):
        paths = [self.directory / "existing.png", self.directory / "blocked.png"]
        paths[0].write_bytes(b"original")
        for path in paths:
            (self.staging / path.name).write_bytes(b"complete")
        replace = os.replace

        def fail_publish_and_restore(source, destination):
            if Path(destination) == paths[1] or Path(source).parent.name == '.previous':
                raise OSError("injected filesystem failure")
            return replace(source, destination)

        with patch("core.sequence_export.os.replace", side_effect=fail_publish_and_restore):
            with self.assertRaises(sequence_export.ExportRollbackError) as failure:
                sequence_export.publish_export_files(self.staging, paths, overwrite=True)
        self.assertEqual(failure.exception.recovery_directory, self.staging)
        self.assertIn(str(self.staging), str(failure.exception))
        self.assertEqual((self.staging / '.previous' / 'existing.png').read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
