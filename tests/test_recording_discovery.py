"""Large-folder inventory is one cancellable pass with truthful counts."""

import os

import pytest

from core.cancellation import OperationCancelled
from core.io import recording


def test_discovery_reports_counts_without_guessing_a_total(tmp_path):
    (tmp_path / "notes.txt").write_text("notes")
    (tmp_path / "ignored.raw").touch()
    updates = []

    sources = recording.discover_sources([str(tmp_path)], progress=updates.append)

    assert sources == [str(tmp_path / "notes.txt")]
    assert updates[0].files == 0
    assert updates[-1].files == 2
    assert updates[-1].directories == 1
    assert updates[-1].sources == 1
    assert updates[-1].done
    assert all(not update.done for update in updates[:-1])


def test_inventory_visits_overlapping_directories_once_and_groups_packages(tmp_path, monkeypatch):
    images, bag, empty = (tmp_path / name for name in ("images", "bag", "empty"))
    for directory in (images, bag, empty):
        directory.mkdir()
    for name in ("cloud10.pcd", "cloud2.pcd", "notes.csv", "ignored.raw"):
        (tmp_path / name).touch()
    for name in ("frame10.png", "frame2.png"):
        (images / name).touch()
    (bag / "metadata.yaml").write_text(
        "rosbag2_bagfile_information:\n  storage_identifier: sqlite3\n"
        "  relative_file_paths: [part1.db3, part2.db3]\n"
    )
    for name in ("part1.db3", "part2.db3"):
        (bag / name).touch()
    visited = []
    scandir = os.scandir

    def observe(path):
        visited.append(os.path.normcase(os.fspath(path)))
        return scandir(path)

    monkeypatch.setattr(recording.os, "scandir", observe)
    updates = []
    result = recording.discover_source_inventory(
        [str(images), str(tmp_path), str(bag / "part1.db3"), str(tmp_path / "cloud2.pcd")],
        progress=updates.append,
    )

    assert result.sources == [str(bag), str(images), str(tmp_path / "notes.csv")]
    assert result.files == [str(tmp_path / "cloud2.pcd"), str(tmp_path / "cloud10.pcd")]
    assert set(result.directories) == {str(tmp_path), str(images), str(bag), str(empty)}
    assert len(visited) == len(set(visited)) == 4
    assert result.errors == []
    assert updates[-1].files == 9
    assert updates[-1].sources == 3


def test_flat_directory_cancels_during_enumeration_before_a_complete_listing(tmp_path):
    for index in range(1024):
        (tmp_path / f"file{index}.txt").touch()
    updates = []

    def progress(update):
        updates.append(update)

    with pytest.raises(OperationCancelled):
        recording.discover_sources(
            [str(tmp_path)],
            cancel=lambda: bool(updates and updates[-1].files >= 128),
            progress=progress,
        )

    assert 128 <= updates[-1].files < 1024
    assert not updates[-1].done


def test_inventory_cancelled_before_start_does_not_publish_a_partial_result(tmp_path):
    updates = []
    with pytest.raises(OperationCancelled):
        recording.discover_source_inventory(
            [str(tmp_path)], cancel=lambda: True, progress=updates.append,
        )
    assert not updates


def test_inventory_keeps_other_roots_when_one_path_is_missing(tmp_path):
    (tmp_path / "cloud.pcd").touch()
    missing = tmp_path / "missing"

    result = recording.discover_source_inventory([str(missing), str(tmp_path)])

    assert result.files == [str(tmp_path / "cloud.pcd")]
    assert len(result.errors) == 1
    assert str(missing) in result.errors[0]


def test_image_sequence_inspection_checks_cancellation_between_files(tmp_path):
    for index in range(200):
        (tmp_path / f"frame{index}.png").touch()
    polls = 0

    def cancel():
        nonlocal polls
        polls += 1
        return polls >= 10

    source = recording.ImageSequenceSource(tmp_path)
    with pytest.raises(OperationCancelled):
        source.inspect(cancel=cancel)
    assert source.info is None


def test_opening_image_sequence_checks_cancel_before_directory_validation(tmp_path):
    (tmp_path / "frame.png").touch()
    with pytest.raises(OperationCancelled):
        recording.open_source(str(tmp_path), cancel=lambda: True)
