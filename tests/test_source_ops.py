import errno
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.io.source_ops import rename_source, relocate_path
from core.io import source_ops


def make_symlink(path, target, **kwargs):
    try:
        path.symlink_to(target, **kwargs)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            raise unittest.SkipTest("Windows account lacks symbolic-link privilege") from error
        raise


class SourceOperationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_rename_file_preserves_suffix_and_unicode_contents(self):
        source = self.root / "cloud.PLY"
        source.write_bytes(b"point-cloud-data")

        result = rename_source(str(source), "点云 新名字")

        self.assertEqual(result, str(self.root / "点云 新名字.PLY"))
        self.assertFalse(source.exists())
        self.assertEqual(Path(result).read_bytes(), b"point-cloud-data")

    def test_rename_directory_moves_its_contents_and_accepts_dots(self):
        source = self.root / "capture"
        source.mkdir()
        (source / "frame.ply").write_bytes(b"points")

        result = rename_source(str(source), "采集.v2")

        self.assertEqual(result, str(self.root / "采集.v2"))
        self.assertFalse(source.exists())
        self.assertEqual((Path(result) / "frame.ply").read_bytes(), b"points")

    def test_rename_rejects_invalid_names_without_mutation(self):
        names = ["", " ", ".", "..", "../escape", "nested/cloud", r"nested\cloud",
                 "bad\0name", "bad\nname", "bad:name", "bad?name", "bad*name",
                 'bad"name', "bad<name", "bad>name", "bad|name", "cloud.", "cloud "]
        for index, name in enumerate(names):
            with self.subTest(name=name):
                folder = self.root / str(index)
                folder.mkdir()
                source = folder / "cloud.ply"
                source.write_bytes(b"original")
                with self.assertRaises(ValueError):
                    rename_source(str(source), name)
                self.assertEqual(source.read_bytes(), b"original")
                self.assertEqual(list(folder.iterdir()), [source])

    def test_rename_rejects_unsupported_extension_without_mutation(self):
        source = self.root / "cloud.ply"
        source.write_bytes(b"original")

        with self.assertRaises(ValueError):
            rename_source(str(source), "renamed.exe")

        self.assertEqual(source.read_bytes(), b"original")

    def test_rename_accepts_an_explicit_supported_extension(self):
        source = self.root / "cloud.ply"
        source.write_bytes(b"original")

        result = rename_source(str(source), "renamed.PLY")

        self.assertEqual(result, str(self.root / "renamed.PLY"))
        self.assertEqual(Path(result).read_bytes(), b"original")

    def test_rename_collision_preserves_both_files(self):
        source = self.root / "cloud.ply"
        source.write_bytes(b"source data")
        target = self.root / "existing.ply"
        target.write_bytes(b"existing data")

        with self.assertRaises(FileExistsError):
            rename_source(str(source), "existing")

        self.assertEqual(source.read_bytes(), b"source data")
        self.assertEqual(target.read_bytes(), b"existing data")

    def test_rename_destination_created_after_final_lookup_is_never_overwritten(self):
        source = self.root / "source.ply"
        source.write_bytes(b"source points")
        target = self.root / "target.ply"
        real_lexists = os.path.lexists

        def create_destination_at_rename_boundary(path):
            exists = real_lexists(path)
            if path == str(target) and not exists:
                target.write_bytes(b"concurrent arrival")
            return exists

        with patch.object(source_ops.os.path, "lexists", side_effect=create_destination_at_rename_boundary):
            with self.assertRaises(FileExistsError):
                rename_source(str(source), "target")

        self.assertEqual(source.read_bytes(), b"source points")
        self.assertEqual(target.read_bytes(), b"concurrent arrival")

    def test_move_destination_created_after_final_lookup_is_never_overwritten(self):
        source = self.root / "source.ply"
        source.write_bytes(b"source points")
        destination = self.root / "destination"
        destination.mkdir()
        target = destination / "source.ply"
        real_lexists = os.path.lexists
        target_checks = 0

        def create_destination_at_move_boundary(path):
            nonlocal target_checks
            exists = real_lexists(path)
            if path == str(target):
                target_checks += 1
                if target_checks == 2:
                    target.write_bytes(b"concurrent arrival")
            return exists

        with patch.object(source_ops.os.path, "lexists", side_effect=create_destination_at_move_boundary):
            with self.assertRaises(FileExistsError):
                source_ops.move_sources([str(source)], str(destination))

        self.assertEqual(source.read_bytes(), b"source points")
        self.assertEqual(target.read_bytes(), b"concurrent arrival")

    def test_rename_collision_with_broken_symlink_preserves_link(self):
        source = self.root / "cloud.ply"
        source.write_bytes(b"source data")
        target = self.root / "existing.ply"
        make_symlink(target, "missing.ply")

        with self.assertRaises(FileExistsError):
            rename_source(str(source), "existing")

        self.assertEqual(source.read_bytes(), b"source data")
        self.assertTrue(target.is_symlink())
        self.assertEqual(os.readlink(target), "missing.ply")

    def test_rename_same_name_is_a_noop_with_absolute_result(self):
        source = self.root / "cloud.ply"
        source.write_bytes(b"original")

        result = rename_source(os.path.relpath(source), "cloud")

        self.assertEqual(result, str(source))
        self.assertEqual(source.read_bytes(), b"original")

    def test_rename_missing_source_does_not_create_target(self):
        with self.assertRaises(FileNotFoundError):
            rename_source(str(self.root / "missing.ply"), "renamed")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_move_files_and_directories_returns_actual_absolute_paths(self):
        destination = self.root / "归档"
        destination.mkdir()
        source = self.root / "点云.ply"
        source.write_bytes(b"cloud")
        folder = self.root / "采集"
        folder.mkdir()
        (folder / "frame.ply").write_bytes(b"frame")

        result = source_ops.move_sources(
            [os.path.relpath(source), str(folder)], os.path.relpath(destination)
        )

        self.assertEqual(result, [
            (str(source), str(destination / "点云.ply")),
            (str(folder), str(destination / "采集")),
        ])
        self.assertFalse(source.exists())
        self.assertFalse(folder.exists())
        self.assertEqual((destination / "点云.ply").read_bytes(), b"cloud")
        self.assertEqual((destination / "采集" / "frame.ply").read_bytes(), b"frame")

    def test_move_existing_target_preflights_entire_batch(self):
        first = self.root / "first.ply"
        first.write_bytes(b"first data")
        second = self.root / "second.ply"
        second.write_bytes(b"second data")
        destination = self.root / "destination"
        destination.mkdir()
        collision = destination / "second.ply"
        collision.write_bytes(b"existing data")

        with self.assertRaises(FileExistsError):
            source_ops.move_sources([str(first), str(second)], str(destination))

        self.assertEqual(first.read_bytes(), b"first data")
        self.assertEqual(second.read_bytes(), b"second data")
        self.assertEqual(collision.read_bytes(), b"existing data")
        self.assertFalse((destination / "first.ply").exists())

    def test_move_same_basename_preflights_batch_before_any_mutation(self):
        sources = []
        for name, contents in [("one", b"first"), ("two", b"second")]:
            folder = self.root / name
            folder.mkdir()
            source = folder / "frame.ply"
            source.write_bytes(contents)
            sources.append(str(source))
        destination = self.root / "destination"
        destination.mkdir()

        with self.assertRaises(FileExistsError):
            source_ops.move_sources(sources, str(destination))

        self.assertEqual(Path(sources[0]).read_bytes(), b"first")
        self.assertEqual(Path(sources[1]).read_bytes(), b"second")
        self.assertEqual(list(destination.iterdir()), [])

    def test_move_missing_or_nondirectory_destination_preserves_source(self):
        source = self.root / "frame.ply"
        source.write_bytes(b"points")
        regular_file = self.root / "regular-file"
        regular_file.write_bytes(b"other")

        for destination, exception in [
            (self.root / "missing", FileNotFoundError),
            (regular_file, NotADirectoryError),
        ]:
            with self.subTest(destination=destination):
                with self.assertRaises(exception):
                    source_ops.move_sources([str(source)], str(destination))
                self.assertEqual(source.read_bytes(), b"points")
                self.assertEqual(regular_file.read_bytes(), b"other")

    def test_move_missing_source_preflights_entire_batch(self):
        source = self.root / "frame.ply"
        source.write_bytes(b"points")
        destination = self.root / "destination"
        destination.mkdir()

        with self.assertRaises(FileNotFoundError):
            source_ops.move_sources([str(source), str(self.root / "missing.ply")], str(destination))

        self.assertEqual(source.read_bytes(), b"points")
        self.assertEqual(list(destination.iterdir()), [])

    def test_move_directory_into_self_or_descendant_is_rejected(self):
        folder = self.root / "source"
        child = folder / "nested"
        child.mkdir(parents=True)
        (child / "frame.ply").write_bytes(b"points")

        for destination in [folder, child]:
            with self.subTest(destination=destination):
                with self.assertRaises(ValueError):
                    source_ops.move_sources([str(folder)], str(destination))
                self.assertEqual((child / "frame.ply").read_bytes(), b"points")

    def test_move_directory_into_symlinked_descendant_is_rejected(self):
        folder = self.root / "source"
        child = folder / "nested"
        child.mkdir(parents=True)
        alias = self.root / "alias"
        make_symlink(alias, child, target_is_directory=True)

        with self.assertRaises(ValueError):
            source_ops.move_sources([str(folder)], str(alias))

        self.assertTrue(child.is_dir())
        self.assertTrue(alias.is_symlink())

    def test_move_collapses_duplicate_and_selected_descendant_paths(self):
        folder = self.root / "source"
        child = folder / "nested"
        child.mkdir(parents=True)
        frame = child / "frame.ply"
        frame.write_bytes(b"points")
        destination = self.root / "destination"
        destination.mkdir()

        result = source_ops.move_sources([
            str(frame), str(folder), str(child), str(folder / "."), str(frame)
        ], str(destination))

        self.assertEqual(result, [(str(folder), str(destination / "source"))])
        self.assertFalse(folder.exists())
        self.assertEqual((destination / "source" / "nested" / "frame.ply").read_bytes(), b"points")

    def test_move_same_parent_is_a_noop_and_has_no_mapping(self):
        source = self.root / "frame.ply"
        source.write_bytes(b"points")

        result = source_ops.move_sources([str(source), str(source)], str(self.root))

        self.assertEqual(result, [])
        self.assertEqual(source.read_bytes(), b"points")

    def test_move_to_same_parent_via_symlink_is_a_noop(self):
        folder = self.root / "source"
        folder.mkdir()
        source = folder / "frame.ply"
        source.write_bytes(b"points")
        alias = self.root / "alias"
        make_symlink(alias, folder, target_is_directory=True)

        result = source_ops.move_sources([str(source)], str(alias))

        self.assertEqual(result, [])
        self.assertEqual(source.read_bytes(), b"points")

    def test_move_empty_selection_is_a_noop(self):
        self.assertEqual(source_ops.move_sources([], str(self.root)), [])

    def test_move_io_failure_rolls_back_already_moved_files(self):
        first = self.root / "first.ply"
        first.write_bytes(b"first data")
        second = self.root / "second.ply"
        second.write_bytes(b"second data")
        destination = self.root / "destination"
        destination.mkdir()
        real_move = source_ops._rename_no_replace

        def fail_second(source, target):
            if source == str(second):
                raise OSError("simulated second move failure")
            return real_move(source, target)

        with patch.object(source_ops, "_rename_no_replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "simulated second move failure"):
                source_ops.move_sources([str(first), str(second)], str(destination))

        self.assertEqual(first.read_bytes(), b"first data")
        self.assertEqual(second.read_bytes(), b"second data")
        self.assertEqual(list(destination.iterdir()), [])

    def test_move_rollback_failure_reports_paths_and_preserves_remaining_data(self):
        first = self.root / "first.ply"
        first.write_bytes(b"first data")
        second = self.root / "second.ply"
        second.write_bytes(b"second data")
        destination = self.root / "destination"
        destination.mkdir()
        moved_first = destination / "first.ply"
        real_move = source_ops._rename_no_replace

        def fail_second_and_rollback(source, target):
            if source == str(second):
                raise OSError("simulated second move failure")
            if source == str(moved_first):
                raise OSError("simulated rollback failure")
            return real_move(source, target)

        with patch.object(source_ops, "_rename_no_replace", side_effect=fail_second_and_rollback):
            with self.assertRaises(OSError) as caught:
                source_ops.move_sources([str(first), str(second)], str(destination))

        self.assertIn("simulated second move failure", str(caught.exception))
        self.assertIn("simulated rollback failure", str(caught.exception))
        self.assertIn(repr(str(moved_first)), str(caught.exception))
        self.assertIn(repr(str(first)), str(caught.exception))
        self.assertEqual(caught.exception.remaining_moves, [(str(first), str(moved_first))])
        self.assertFalse(first.exists())
        self.assertEqual(moved_first.read_bytes(), b"first data")
        self.assertEqual(second.read_bytes(), b"second data")

    def test_move_rollback_error_maps_only_entries_not_restored(self):
        sources = [self.root / name for name in ["first.ply", "second.ply", "third.ply"]]
        for index, source in enumerate(sources):
            source.write_bytes(f"points {index}".encode())
        destination = self.root / "destination"
        destination.mkdir()
        real_move = source_ops._rename_no_replace

        def fail_third_and_first_rollback(source, target):
            if source == str(sources[2]):
                raise OSError("third move failed")
            if source == str(destination / "first.ply"):
                raise OSError("first rollback failed")
            return real_move(source, target)

        with patch.object(source_ops, "_rename_no_replace", side_effect=fail_third_and_first_rollback):
            with self.assertRaises(OSError) as caught:
                source_ops.move_sources([str(path) for path in sources], str(destination))

        self.assertEqual(caught.exception.remaining_moves, [
            (str(sources[0]), str(destination / "first.ply"))
        ])
        self.assertEqual((destination / "first.ply").read_bytes(), b"points 0")
        self.assertFalse(sources[0].exists())
        self.assertEqual(sources[1].read_bytes(), b"points 1")
        self.assertEqual(sources[2].read_bytes(), b"points 2")
        self.assertFalse((destination / "second.ply").exists())

    def test_move_collision_created_after_preflight_rolls_back_prior_move(self):
        first = self.root / "first.ply"
        first.write_bytes(b"first data")
        second = self.root / "second.ply"
        second.write_bytes(b"second data")
        destination = self.root / "destination"
        destination.mkdir()
        collision = destination / "second.ply"
        real_move = source_ops._rename_no_replace

        def create_collision_after_first_move(source, target):
            result = real_move(source, target)
            if source == str(first):
                collision.write_bytes(b"new arrival")
            return result

        with patch.object(source_ops, "_rename_no_replace", side_effect=create_collision_after_first_move):
            with self.assertRaises(FileExistsError):
                source_ops.move_sources([str(first), str(second)], str(destination))

        self.assertEqual(first.read_bytes(), b"first data")
        self.assertEqual(second.read_bytes(), b"second data")
        self.assertEqual(collision.read_bytes(), b"new arrival")
        self.assertFalse((destination / "first.ply").exists())

    def test_rename_symlink_changes_link_name_without_touching_target(self):
        real = self.root / "real.ply"
        real.write_bytes(b"real points")
        link = self.root / "link.ply"
        make_symlink(link, "real.ply")

        result = rename_source(str(link), "renamed")

        self.assertEqual(result, str(self.root / "renamed.ply"))
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(Path(result).is_symlink())
        self.assertEqual(os.readlink(result), "real.ply")
        self.assertEqual(real.read_bytes(), b"real points")

    def test_rename_directory_symlink_preserves_target_and_link_kind(self):
        real = self.root / "real"
        real.mkdir()
        (real / "frame.ply").write_bytes(b"real points")
        link = self.root / "link"
        make_symlink(link, "real", target_is_directory=True)

        result = rename_source(str(link), "renamed.v2")

        self.assertEqual(result, str(self.root / "renamed.v2"))
        self.assertTrue(Path(result).is_symlink())
        self.assertEqual(os.readlink(result), "real")
        self.assertEqual((real / "frame.ply").read_bytes(), b"real points")

    def test_move_broken_symlink_moves_link_itself(self):
        link = self.root / "link.ply"
        make_symlink(link, "missing.ply")
        destination = self.root / "destination"
        destination.mkdir()

        result = source_ops.move_sources([str(link)], str(destination))

        self.assertEqual(result, [(str(link), str(destination / "link.ply"))])
        self.assertFalse(os.path.lexists(link))
        self.assertTrue((destination / "link.ply").is_symlink())
        self.assertEqual(os.readlink(destination / "link.ply"), "missing.ply")

    def test_move_selected_directory_symlink_does_not_move_target_children(self):
        real = self.root / "real"
        real.mkdir()
        frame = real / "frame.ply"
        frame.write_bytes(b"real points")
        link = self.root / "link"
        make_symlink(link, real, target_is_directory=True)
        destination = self.root / "destination"
        destination.mkdir()

        result = source_ops.move_sources([str(link / "frame.ply"), str(link)], str(destination))

        self.assertEqual(result, [(str(link), str(destination / "link"))])
        self.assertTrue((destination / "link").is_symlink())
        self.assertEqual(os.readlink(destination / "link"), str(real))
        self.assertEqual(frame.read_bytes(), b"real points")

    def test_cross_volume_move_copies_real_contents_and_preserves_symlink(self):
        source = self.root / "frame.ply"
        source.write_bytes(b"point data")
        link = self.root / "link.ply"
        make_symlink(link, source)
        destination = self.root / "destination"
        destination.mkdir()

        real_rename = source_ops._rename_no_replace

        def cross_volume_boundary(old, new):
            if old in {str(source), str(link)}:
                raise OSError(errno.EXDEV, "cross volume")
            return real_rename(old, new)

        with patch.object(source_ops, "_rename_no_replace", side_effect=cross_volume_boundary):
            result = source_ops.move_sources([str(source), str(link)], str(destination))

        self.assertEqual(result, [
            (str(source), str(destination / "frame.ply")),
            (str(link), str(destination / "link.ply")),
        ])
        self.assertFalse(os.path.lexists(source))
        self.assertFalse(os.path.lexists(link))
        self.assertEqual((destination / "frame.ply").read_bytes(), b"point data")
        self.assertTrue((destination / "link.ply").is_symlink())
        self.assertEqual(os.readlink(destination / "link.ply"), str(source))

    def test_cross_volume_directory_move_preserves_nested_data_and_symlink(self):
        source = self.root / "source"
        nested = source / "nested"
        nested.mkdir(parents=True)
        (nested / "frame.ply").write_bytes(b"nested points")
        external = self.root / "external.ply"
        external.write_bytes(b"external points")
        make_symlink((source / "link.ply"), external)
        destination = self.root / "destination"
        destination.mkdir()
        real_rename = source_ops._rename_no_replace

        def cross_volume_boundary(old, new):
            if old == str(source):
                raise OSError(errno.EXDEV, "cross volume")
            return real_rename(old, new)

        with patch.object(source_ops, "_rename_no_replace", side_effect=cross_volume_boundary):
            result = source_ops.move_sources([str(source)], str(destination))

        self.assertEqual(result, [(str(source), str(destination / "source"))])
        self.assertFalse(source.exists())
        self.assertEqual((destination / "source" / "nested" / "frame.ply").read_bytes(), b"nested points")
        self.assertTrue((destination / "source" / "link.ply").is_symlink())
        self.assertEqual(os.readlink(destination / "source" / "link.ply"), str(external))
        self.assertEqual(external.read_bytes(), b"external points")
        self.assertEqual(list(destination.iterdir()), [destination / "source"])

    def test_cross_volume_copy_failure_preserves_source_and_removes_only_staging(self):
        source = self.root / "source.ply"
        source.write_bytes(b"source points")
        destination = self.root / "destination"
        destination.mkdir()

        def fail_copy_after_partial_write(old, new, **kwargs):
            Path(new).write_bytes(b"partial copy")
            raise OSError("copy failed midway")

        with patch.object(source_ops, "_rename_no_replace", side_effect=OSError(errno.EXDEV, "cross volume")):
            with patch.object(source_ops.shutil, "copy2", side_effect=fail_copy_after_partial_write):
                with self.assertRaisesRegex(OSError, "copy failed midway"):
                    source_ops.move_sources([str(source)], str(destination))

        self.assertEqual(source.read_bytes(), b"source points")
        self.assertEqual(list(destination.iterdir()), [])

    def test_cross_volume_publish_collision_preserves_source_and_concurrent_arrival(self):
        source = self.root / "source.ply"
        source.write_bytes(b"source points")
        destination = self.root / "destination"
        destination.mkdir()
        target = destination / "source.ply"
        real_rename = source_ops._rename_no_replace

        def concurrent_publish_boundary(old, new):
            if old == str(source):
                raise OSError(errno.EXDEV, "cross volume")
            target.write_bytes(b"concurrent arrival")
            return real_rename(old, new)

        with patch.object(source_ops, "_rename_no_replace", side_effect=concurrent_publish_boundary):
            with self.assertRaises(FileExistsError):
                source_ops.move_sources([str(source)], str(destination))

        self.assertEqual(source.read_bytes(), b"source points")
        self.assertEqual(target.read_bytes(), b"concurrent arrival")
        self.assertEqual(list(destination.iterdir()), [target])

    def test_cross_volume_partial_source_cleanup_keeps_complete_destination_and_mapping(self):
        first = self.root / "first.ply"
        first.write_bytes(b"first points")
        source = self.root / "source"
        source.mkdir()
        (source / "one.ply").write_bytes(b"first frame")
        (source / "two.ply").write_bytes(b"second frame")
        last = self.root / "last.ply"
        last.write_bytes(b"last points")
        destination = self.root / "destination"
        destination.mkdir()
        target = destination / "source"
        real_rename = source_ops._rename_no_replace
        real_rmtree = source_ops.shutil.rmtree

        def cross_volume_boundary(old, new):
            if old == str(source):
                raise OSError(errno.EXDEV, "cross volume")
            return real_rename(old, new)

        def partially_remove_source(path, *args, **kwargs):
            if path == str(source):
                (source / "one.ply").unlink()
                raise OSError("source cleanup failed midway")
            return real_rmtree(path, *args, **kwargs)

        with patch.object(source_ops, "_rename_no_replace", side_effect=cross_volume_boundary):
            with patch.object(source_ops.shutil, "rmtree", side_effect=partially_remove_source):
                with self.assertRaises(source_ops.SourceMoveError) as caught:
                    source_ops.move_sources([str(first), str(source), str(last)], str(destination))

        self.assertEqual(caught.exception.remaining_moves, [(str(source), str(target))])
        self.assertIn("source cleanup failed midway", str(caught.exception))
        self.assertIn(repr(str(source)), str(caught.exception))
        self.assertIn(repr(str(target)), str(caught.exception))
        self.assertFalse((source / "one.ply").exists())
        self.assertEqual((source / "two.ply").read_bytes(), b"second frame")
        self.assertEqual((target / "one.ply").read_bytes(), b"first frame")
        self.assertEqual((target / "two.ply").read_bytes(), b"second frame")
        self.assertEqual(first.read_bytes(), b"first points")
        self.assertEqual(last.read_bytes(), b"last points")
        self.assertEqual(list(destination.iterdir()), [target])

    def test_cross_volume_rollback_cleanup_failure_keeps_references_at_complete_original(self):
        source = self.root / "source"
        source.mkdir()
        (source / "one.ply").write_bytes(b"first frame")
        (source / "two.ply").write_bytes(b"second frame")
        second = self.root / "second.ply"
        second.write_bytes(b"second points")
        destination = self.root / "destination"
        destination.mkdir()
        moved = destination / "source"
        real_rename = source_ops._rename_no_replace
        real_rmtree = source_ops.shutil.rmtree

        def fail_second_move_and_cross_volume_rollback(old, new):
            if old == str(second):
                raise OSError("second move failed")
            if old == str(moved):
                raise OSError(errno.EXDEV, "cross volume rollback")
            return real_rename(old, new)

        def partially_remove_rollback_source(path, *args, **kwargs):
            if path == str(moved):
                (moved / "one.ply").unlink()
                raise OSError("rollback cleanup failed midway")
            return real_rmtree(path, *args, **kwargs)

        with patch.object(source_ops, "_rename_no_replace", side_effect=fail_second_move_and_cross_volume_rollback):
            with patch.object(source_ops.shutil, "rmtree", side_effect=partially_remove_rollback_source):
                with self.assertRaises(source_ops.SourceMoveError) as caught:
                    source_ops.move_sources([str(source), str(second)], str(destination))

        self.assertEqual(caught.exception.remaining_moves, [])
        self.assertIn("rollback cleanup failed midway", str(caught.exception))
        self.assertEqual((source / "one.ply").read_bytes(), b"first frame")
        self.assertEqual((source / "two.ply").read_bytes(), b"second frame")
        self.assertFalse((moved / "one.ply").exists())
        self.assertEqual((moved / "two.ply").read_bytes(), b"second frame")
        self.assertEqual(second.read_bytes(), b"second points")

    def test_blank_source_path_is_rejected_instead_of_renaming_working_directory(self):
        working = self.root / "working"
        working.mkdir()

        with patch.object(source_ops.os, "getcwd", return_value=str(working)):
            with self.assertRaises(ValueError):
                rename_source("", "renamed")

        self.assertTrue(working.is_dir())

    def test_blank_move_paths_are_rejected_instead_of_using_working_directory(self):
        working = self.root / "working"
        working.mkdir()
        destination = self.root / "destination"
        destination.mkdir()
        source = self.root / "frame.ply"
        source.write_bytes(b"points")

        with patch.object(source_ops.os, "getcwd", return_value=str(working)):
            with self.assertRaises(ValueError):
                source_ops.move_sources([""], str(destination))
            with self.assertRaises(ValueError):
                source_ops.move_sources([str(source)], "")

        self.assertTrue(working.is_dir())
        self.assertEqual(source.read_bytes(), b"points")
        self.assertEqual(list(destination.iterdir()), [])

    def test_relocate_replaces_only_exact_or_descendant_paths(self):
        cases = [
            ("/captures/run", "/captures/run", "/archive/day", "/archive/day"),
            ("/captures/run/a.ply", "/captures/run", "/archive/day", "/archive/day/a.ply"),
            ("/captures/run/a.ply", "/captures/run/a.ply", "/archive/a.ply", "/archive/a.ply"),
            ("/captures/run2/a.ply", "/captures/run", "/archive/day", "/captures/run2/a.ply"),
            ("/other/a.ply", "/captures/run", "/archive/day", "/other/a.ply"),
        ]
        for path, old, new, expected in cases:
            path, old, new, expected = map(os.path.normpath, (path, old, new, expected))
            with self.subTest(path=path, old=old):
                self.assertEqual(relocate_path(path, old, new), expected)


class SourceTrashTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pointlab-trash-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.trash = self.root / "fixture-trash"
        self.trash.mkdir()

    def fixture_qfile(self, *, failing=(), missing_report=False, absent_report=False):
        """Replace only the OS Trash boundary; exercise real fixture renames."""
        trash = self.trash

        class FixtureFile:
            def __init__(self, filename):
                self.filename = filename

            def moveToTrash(self):
                if self.filename in failing:
                    return False
                target = trash / Path(self.filename).name
                index = 1
                while os.path.lexists(target):
                    target = trash / f"{Path(self.filename).name}.{index}"
                    index += 1
                os.rename(self.filename, target)
                self.filename = "" if missing_report else str(target)
                if absent_report:
                    self.filename = str(trash / "unavailable-location")
                return True

            def fileName(self):
                return self.filename

            def errorString(self):
                return "fixture Trash unavailable"

        return FixtureFile

    def test_minimal_sources_validate_all_paths_and_collapse_parent_selections(self):
        folder = self.root / "capture"
        frame = folder / "nested" / "frame.ply"
        frame.parent.mkdir(parents=True)
        frame.write_bytes(b"points")

        result = source_ops.minimal_source_paths([
            os.path.relpath(frame), str(folder), str(frame.parent), str(folder / ".")
        ])

        self.assertEqual(result, [str(folder)])
        self.assertEqual(frame.read_bytes(), b"points")
        with self.assertRaises(FileNotFoundError):
            source_ops.minimal_source_paths([str(folder), str(folder / "missing.ply")])

    def test_empty_trash_and_restore_selections_do_nothing(self):
        self.assertEqual(source_ops.minimal_source_paths([]), [])
        self.assertEqual(source_ops.trash_sources([]), [])
        self.assertEqual(source_ops.restore_trashed([]), [])
        self.assertEqual(list(self.trash.iterdir()), [])

    def test_trash_preserves_file_and_folder_contents_at_reported_locations(self):
        first = self.root / "点云.ply"
        first.write_bytes(b"first cloud")
        folder = self.root / "capture"
        child = folder / "frame.ply"
        folder.mkdir()
        child.write_bytes(b"nested cloud")
        # The OS may choose a different Trash name when one already exists.
        (self.trash / "点云.ply").write_bytes(b"previously trashed cloud")

        with patch.object(source_ops, "QFile", self.fixture_qfile(), create=True):
            moves = source_ops.trash_sources([
                os.path.relpath(first), str(child), str(folder), str(first)
            ])

        self.assertEqual(moves, [
            (str(first), str(self.trash / "点云.ply.1")),
            (str(folder), str(self.trash / "capture")),
        ])
        self.assertFalse(first.exists())
        self.assertFalse(folder.exists())
        self.assertEqual((self.trash / "点云.ply.1").read_bytes(), b"first cloud")
        self.assertEqual((self.trash / "capture" / "frame.ply").read_bytes(), b"nested cloud")
        self.assertEqual((self.trash / "点云.ply").read_bytes(), b"previously trashed cloud")

    def test_invalid_or_missing_trash_path_preflights_the_whole_batch(self):
        first = self.root / "first.ply"
        first.write_bytes(b"first cloud")
        for invalid, exception in [
            ("", ValueError), (" ", ValueError), (None, ValueError),
            ("bad\0path", ValueError), (str(self.root / "missing.ply"), FileNotFoundError),
        ]:
            with self.subTest(invalid=invalid):
                with patch.object(source_ops, "QFile", self.fixture_qfile(), create=True):
                    with self.assertRaises(exception):
                        source_ops.trash_sources([str(first), invalid])
                self.assertEqual(first.read_bytes(), b"first cloud")
                self.assertEqual(list(self.trash.iterdir()), [])

    def test_trash_symbolic_links_preserves_their_targets(self):
        target = self.root / "target.ply"
        target.write_bytes(b"target points")
        link = self.root / "alias.ply"
        make_symlink(link, target)
        broken = self.root / "broken.ply"
        make_symlink(broken, "missing.ply")

        with patch.object(source_ops, "QFile", self.fixture_qfile(), create=True):
            result = source_ops.trash_sources([str(link), str(broken)])

        self.assertEqual(result, [
            (str(link), str(self.trash / "alias.ply")),
            (str(broken), str(self.trash / "broken.ply")),
        ])
        self.assertEqual(target.read_bytes(), b"target points")
        self.assertEqual(os.readlink(self.trash / "alias.ply"), str(target))
        self.assertEqual(os.readlink(self.trash / "broken.ply"), "missing.ply")

    def test_directory_symlink_is_not_treated_as_containing_its_target_children(self):
        target = self.root / "target"
        target.mkdir()
        child = target / "frame.ply"
        child.write_bytes(b"target points")
        link = self.root / "alias"
        make_symlink(link, target, target_is_directory=True)

        result = source_ops.minimal_source_paths([str(link), str(child)])

        self.assertEqual(result, [str(link), str(child)])
        self.assertEqual(child.read_bytes(), b"target points")

    def test_trash_failure_reports_prior_successes_without_rollback_or_unlink(self):
        sources = [self.root / name for name in ["first.ply", "second.ply", "third.ply"]]
        for index, source in enumerate(sources):
            source.write_bytes(f"points {index}".encode())

        with patch.object(source_ops, "QFile", self.fixture_qfile(failing=[str(sources[1])]), create=True):
            with self.assertRaises(source_ops.SourceMoveError) as caught:
                source_ops.trash_sources([str(source) for source in sources])

        self.assertEqual(caught.exception.remaining_moves, [
            (str(sources[0]), str(self.trash / "first.ply"))
        ])
        self.assertIn("fixture Trash unavailable", str(caught.exception))
        self.assertEqual((self.trash / "first.ply").read_bytes(), b"points 0")
        self.assertFalse(sources[0].exists())
        self.assertEqual(sources[1].read_bytes(), b"points 1")
        self.assertEqual(sources[2].read_bytes(), b"points 2")

    def test_missing_trash_location_reports_completed_move_and_unavailable_undo(self):
        source = self.root / "frame.ply"
        source.write_bytes(b"points")

        with patch.object(source_ops, "QFile", self.fixture_qfile(missing_report=True), create=True):
            with self.assertRaises(source_ops.SourceMoveError) as caught:
                source_ops.trash_sources([str(source)])

        self.assertEqual(caught.exception.remaining_moves, [(str(source), "")])
        self.assertIn("restore", str(caught.exception).lower())
        self.assertFalse(source.exists())
        self.assertEqual((self.trash / "frame.ply").read_bytes(), b"points")

    def test_unavailable_reported_trash_location_is_not_silently_accepted(self):
        source = self.root / "frame.ply"
        source.write_bytes(b"points")

        with patch.object(source_ops, "QFile", self.fixture_qfile(absent_report=True), create=True):
            with self.assertRaises(source_ops.SourceMoveError) as caught:
                source_ops.trash_sources([str(source)])

        self.assertEqual(caught.exception.remaining_moves, [
            (str(source), str(self.trash / "unavailable-location"))
        ])
        self.assertIn("restore", str(caught.exception).lower())
        self.assertFalse(source.exists())
        self.assertEqual((self.trash / "frame.ply").read_bytes(), b"points")

    def test_restore_preserves_files_directories_and_broken_symlinks(self):
        trashed_file = self.trash / "renamed-by-os.ply"
        trashed_file.write_bytes(b"cloud")
        trashed_folder = self.trash / "capture"
        trashed_folder.mkdir()
        (trashed_folder / "frame.ply").write_bytes(b"nested cloud")
        trashed_link = self.trash / "broken.ply"
        make_symlink(trashed_link, "missing.ply")
        original_file = self.root / "点云.ply"
        original_folder = self.root / "capture"
        original_link = self.root / "broken.ply"

        result = source_ops.restore_trashed([
            (os.path.relpath(original_file), str(trashed_file)),
            (str(original_folder), str(trashed_folder)),
            (str(original_link), str(trashed_link)),
            (str(original_file), str(trashed_file)),
        ])

        self.assertEqual(result, [
            (str(trashed_file), str(original_file)),
            (str(trashed_folder), str(original_folder)),
            (str(trashed_link), str(original_link)),
        ])
        self.assertEqual(original_file.read_bytes(), b"cloud")
        self.assertEqual((original_folder / "frame.ply").read_bytes(), b"nested cloud")
        self.assertEqual(os.readlink(original_link), "missing.ply")
        self.assertEqual(list(self.trash.iterdir()), [])

    def test_restore_collision_preflights_entire_batch_and_preserves_both_versions(self):
        first = self.trash / "first.ply"
        first.write_bytes(b"first cloud")
        second = self.trash / "second.ply"
        second.write_bytes(b"second cloud")
        collision = self.root / "second.ply"
        collision.write_bytes(b"new capture")

        with self.assertRaises(FileExistsError):
            source_ops.restore_trashed([
                (str(self.root / "first.ply"), str(first)),
                (str(collision), str(second)),
            ])

        self.assertEqual(first.read_bytes(), b"first cloud")
        self.assertEqual(second.read_bytes(), b"second cloud")
        self.assertEqual(collision.read_bytes(), b"new capture")
        self.assertFalse((self.root / "first.ply").exists())

    def test_restore_broken_link_collision_is_never_overwritten(self):
        source = self.trash / "frame.ply"
        source.write_bytes(b"points")
        original = self.root / "frame.ply"
        make_symlink(original, "missing.ply")

        with self.assertRaises(FileExistsError):
            source_ops.restore_trashed([(str(original), str(source))])

        self.assertEqual(source.read_bytes(), b"points")
        self.assertEqual(os.readlink(original), "missing.ply")

    def test_restore_validates_all_mappings_before_mutation(self):
        source = self.trash / "frame.ply"
        source.write_bytes(b"points")
        original = self.root / "frame.ply"
        for invalid, exception in [
            (("", str(source)), ValueError),
            ((str(self.root / "other.ply"), ""), ValueError),
            ((str(self.root / "other.ply"), "bad\0path"), ValueError),
            ((str(self.root / "other.ply"), str(self.trash / "missing.ply")), FileNotFoundError),
            ((str(self.root / "missing-parent" / "other.ply"), str(source)), FileNotFoundError),
        ]:
            with self.subTest(invalid=invalid):
                with self.assertRaises(exception):
                    source_ops.restore_trashed([(str(original), str(source)), invalid])
                self.assertEqual(source.read_bytes(), b"points")
                self.assertFalse(original.exists())

    def test_restore_failure_reports_successes_and_leaves_unrestored_entries_in_trash(self):
        first = self.trash / "first.ply"
        first.write_bytes(b"first cloud")
        second = self.trash / "second.ply"
        second.write_bytes(b"second cloud")
        original_first = self.root / "first.ply"
        original_second = self.root / "second.ply"
        rename = source_ops._rename_no_replace

        def fail_second(source, target):
            if source == str(second):
                raise OSError("fixture restore failure")
            rename(source, target)

        with patch.object(source_ops, "_rename_no_replace", side_effect=fail_second):
            with self.assertRaises(source_ops.SourceMoveError) as caught:
                source_ops.restore_trashed([
                    (str(original_first), str(first)), (str(original_second), str(second))
                ])

        self.assertEqual(caught.exception.remaining_moves, [(str(first), str(original_first))])
        self.assertEqual(original_first.read_bytes(), b"first cloud")
        self.assertFalse(first.exists())
        self.assertEqual(second.read_bytes(), b"second cloud")
        self.assertFalse(original_second.exists())

    def test_restore_racing_collision_is_atomic_and_preserves_trashed_source(self):
        source = self.trash / "frame.ply"
        source.write_bytes(b"trashed cloud")
        original = self.root / "frame.ply"
        rename = source_ops._rename_no_replace

        def create_collision_at_rename_boundary(old, new):
            original.write_bytes(b"concurrent capture")
            rename(old, new)

        with patch.object(source_ops, "_rename_no_replace", side_effect=create_collision_at_rename_boundary):
            with self.assertRaises(source_ops.SourceMoveError) as caught:
                source_ops.restore_trashed([(str(original), str(source))])

        self.assertEqual(caught.exception.remaining_moves, [])
        self.assertEqual(source.read_bytes(), b"trashed cloud")
        self.assertEqual(original.read_bytes(), b"concurrent capture")

    def test_restore_across_volumes_refuses_copy_unlink_fallback(self):
        source = self.trash / "frame.ply"
        source.write_bytes(b"trashed cloud")
        original = self.root / "frame.ply"

        with patch.object(source_ops, "_rename_no_replace", side_effect=OSError(errno.EXDEV, "cross volume")):
            with self.assertRaises(source_ops.SourceMoveError) as caught:
                source_ops.restore_trashed([(str(original), str(source))])

        self.assertEqual(caught.exception.remaining_moves, [])
        self.assertEqual(source.read_bytes(), b"trashed cloud")
        self.assertFalse(original.exists())

    @unittest.skipUnless(
        os.environ.get("POINTLAB_TEST_SYSTEM_TRASH") == "1",
        "Explicit opt-in required for one synthetic system Trash round trip",
    )
    def test_native_trash_round_trip_restores_only_the_fixture(self):
        source = self.root / "点云-Trash-integration.ply"
        source.write_bytes(b"pointlab synthetic Trash fixture")
        moves = []
        try:
            moves = source_ops.trash_sources([str(source)])
            self.assertEqual(len(moves), 1)
            self.assertEqual(moves[0][0], str(source))
            self.assertTrue(os.path.isabs(moves[0][1]))
            self.assertFalse(source.exists())
            self.assertEqual(Path(moves[0][1]).read_bytes(), b"pointlab synthetic Trash fixture")
        finally:
            # Never delete from the real Trash. Restore precisely this fixture.
            for original, trashed in moves:
                if os.path.lexists(trashed) and not os.path.lexists(original):
                    source_ops.restore_trashed([(original, trashed)])
        self.assertEqual(source.read_bytes(), b"pointlab synthetic Trash fixture")
        self.assertFalse(os.path.lexists(moves[0][1]))


if __name__ == "__main__":
    unittest.main()
