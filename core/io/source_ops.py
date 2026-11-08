"""Physical source moves used by the explorer, without changing scene state."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import sys
import tempfile

from PySide6.QtCore import QFile

from core.io.scanner import SUPPORTED_EXTS


class SourceMoveError(OSError):
    """A failed batch with completed operation-direction path mappings.

    Trash operations never roll back earlier successes. An empty Trash target
    means the OS trashed that source but did not report a recoverable location.
    """

    def __init__(self, message: str, remaining_moves: list[tuple[str, str]]):
        super().__init__(message)
        self.remaining_moves = remaining_moves


def _absolute(path: str) -> str:
    if not isinstance(path, str) or not path.strip() or "\0" in path:
        raise ValueError(f"Invalid source path: {path!r}")
    return os.path.abspath(os.path.expanduser(path))


def _validate_name(name: str) -> None:
    if (
        not isinstance(name, str)
        or not name.strip()
        or name in {".", ".."}
        or name.endswith((".", " "))
        or any(character in '/\\<>:"|?*' or ord(character) < 32 or ord(character) == 127
               for character in name)
    ):
        raise ValueError(f"Invalid source name: {name!r}")


def _rename_no_replace(source: str, target: str) -> None:
    """Atomically rename a directory entry, refusing any existing target."""
    if os.name == "nt":
        os.rename(source, target)  # Windows rename already refuses replacement.
        return

    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        # <sys/stdio.h>: RENAME_EXCL = 0x00000004 (macOS 10.12+).
        rename = libc.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        arguments = (os.fsencode(source), os.fsencode(target), 0x00000004)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        # Linux UAPI: AT_FDCWD = -100, RENAME_NOREPLACE = (1 << 0).
        rename = libc.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint]
        arguments = (-100, os.fsencode(source), -100, os.fsencode(target), 1)
    else:
        raise OSError(errno.ENOTSUP, "Atomic non-replacing rename is unavailable")
    rename.restype = ctypes.c_int
    if rename(*arguments) != 0:
        number = ctypes.get_errno()
        raise OSError(number, f"{os.strerror(number)}: {source!r} -> {target!r}")


def rename_source(path: str, name: str) -> str:
    """Rename a source in place and return its absolute new path."""
    _validate_name(name)
    source = _absolute(path)
    if not os.path.lexists(source):
        raise FileNotFoundError(f"Source does not exist: {source}")
    if not os.path.isdir(source):
        extension = os.path.splitext(name)[1]
        if not extension:
            name += os.path.splitext(source)[1]
        elif extension.lower() not in SUPPORTED_EXTS:
            raise ValueError(f"Unsupported point-cloud extension: {extension}")
    target = os.path.join(os.path.dirname(source), name)
    if target == source:
        return target
    if os.path.lexists(target):
        raise FileExistsError(f"Target already exists: {target}")
    _rename_no_replace(source, target)
    return target


def _is_within(path: str, directory: str) -> bool:
    try:
        return os.path.commonpath([path, directory]) == directory
    except ValueError:  # Different drives on Windows.
        return False


def _move_without_overwrite(source: str, target: str) -> None:
    if os.path.lexists(target):
        raise FileExistsError(f"Target already exists: {target}")
    try:
        _rename_no_replace(source, target)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        _copy_across_volume(source, target)


def _copy_across_volume(source: str, target: str) -> None:
    """Publish a complete staged copy before removing any original source data."""
    published = False
    try:
        # mkdtemp reserves a private directory on the target volume. Its payload
        # is never visible at the final path until the non-replacing rename.
        with tempfile.TemporaryDirectory(prefix=".pointlab-move-", dir=os.path.dirname(target)) as staging:
            payload = os.path.join(staging, "source")
            if os.path.islink(source):
                os.symlink(os.readlink(source), payload, target_is_directory=os.path.isdir(source))
            elif os.path.isdir(source):
                shutil.copytree(source, payload, symlinks=True)
            else:
                shutil.copy2(source, payload, follow_symlinks=False)
            _rename_no_replace(payload, target)
            published = True

        if os.path.isdir(source) and not os.path.islink(source):
            shutil.rmtree(source)
        else:
            os.unlink(source)
    except Exception as error:
        if published:
            # rmtree may have removed only part of the original directory. The
            # new destination is complete; never roll it back over that source.
            raise SourceMoveError(
                f"Move published a complete destination at {target!r}, but cleanup failed: "
                f"{error}. Remaining original data is preserved at {source!r}.",
                [(source, target)],
            ) from error
        raise


def move_sources(paths: list[str], destination: str) -> list[tuple[str, str]]:
    """Move selected sources into a folder, returning top-level path mappings.

    Repeated selections and selected descendants are moved once. Sources already
    in the destination are left in place and omitted from the result.
    """
    destination = _absolute(destination)
    if not os.path.exists(destination):
        raise FileNotFoundError(f"Destination does not exist: {destination}")
    if not os.path.isdir(destination):
        raise NotADirectoryError(f"Destination is not a directory: {destination}")

    sources = list(dict.fromkeys(_absolute(path) for path in paths))
    for source in sources:
        if not os.path.lexists(source):
            raise FileNotFoundError(f"Source does not exist: {source}")
    directories = [source for source in sources if os.path.isdir(source)]
    sources = [source for source in sources if not any(
        directory != source and _is_within(source, directory) for directory in directories
    )]

    plan = []
    targets = set()
    for source in sources:
        if os.path.isdir(source) and not os.path.islink(source) and _is_within(
            os.path.realpath(destination), os.path.realpath(source)
        ):
            raise ValueError(f"Cannot move a directory into itself: {source} -> {destination}")
        if os.path.samefile(os.path.dirname(source), destination):
            continue
        target = os.path.join(destination, os.path.basename(source))
        key = os.path.normcase(target)
        if key in targets or os.path.lexists(target):
            raise FileExistsError(f"Target already exists or is selected twice: {target}")
        targets.add(key)
        plan.append((source, target))

    completed = []
    try:
        for source, target in plan:
            _move_without_overwrite(source, target)
            completed.append((source, target))
    except Exception as error:
        rollback_errors = []
        remaining_moves = []
        committed_moves = getattr(error, "remaining_moves", [])
        for old, new in reversed(completed):
            try:
                _move_without_overwrite(new, old)
            except Exception as rollback_error:
                rollback_errors.append(f"{new!r} -> {old!r}: {rollback_error}")
                # A cross-volume rollback can restore the complete old path and
                # then fail cleaning its duplicate. References should stay old.
                if (new, old) not in getattr(rollback_error, "remaining_moves", []):
                    remaining_moves.append((old, new))
        if rollback_errors:
            raise SourceMoveError(
                f"Move failed: {error}. Rollback failed; data remains at the reported paths: "
                + "; ".join(rollback_errors),
                list(reversed(remaining_moves)) + committed_moves,
            ) from error
        raise
    return plan


def minimal_source_paths(paths: list[str]) -> list[str]:
    """Validate every selection and return unique, top-level absolute sources.

    A selected physical directory includes its descendants. A directory symlink
    is a separate entry, since moving it to Trash does not move its target.
    This is also a read-only preflight for a Trash confirmation dialog.
    """
    sources = list(dict.fromkeys(_absolute(path) for path in paths))
    for source in sources:
        if not os.path.lexists(source):
            raise FileNotFoundError(f"Source does not exist: {source}")
    directories = [source for source in sources
                   if os.path.isdir(source) and not os.path.islink(source)]
    return [source for source in sources if not any(
        directory != source and _is_within(source, directory) for directory in directories
    )]


def trash_sources(paths: list[str]) -> list[tuple[str, str]]:
    """Move sources to the system Trash and return (original, Trash path) pairs.

    All selections are validated before mutation. Only the native Qt Trash API
    is used; there is no permanent-delete fallback. Runtime failures preserve
    earlier successes in SourceMoveError.remaining_moves without rolling back.
    An unavailable Trash location is reported as an error, since app undo then
    cannot safely restore the source; a missing OS location is recorded as "".
    """
    sources = minimal_source_paths(paths)
    completed = []
    try:
        for source in sources:
            file = QFile(source)
            if not file.moveToTrash():
                raise OSError(f"Cannot move {source!r} to Trash: {file.errorString()}")
            trashed = file.fileName()
            trashed = _absolute(trashed) if trashed else ""
            completed.append((source, trashed))
            if not trashed or not os.path.lexists(trashed):
                raise OSError(
                    f"Moved {source!r} to Trash, but its restore location is unavailable "
                    f"({trashed!r}). Restore it using the system Trash."
                )
    except Exception as error:
        raise SourceMoveError(f"Trash operation failed: {error}", completed) from error
    return completed


def restore_trashed(moves: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Restore (original, Trash path) records and return (Trash path, original).

    Preflight the whole batch, then atomically rename without replacement. If
    originals were recreated, parents disappeared, or the volume changed, keep
    the Trash contents intact. No copy/unlink fallback or rollback is attempted.
    Runtime failures expose only completed restores in remaining_moves, in the
    same (Trash path, original) direction as the successful return value.
    """
    plan = list(dict.fromkeys(
        (_absolute(trashed), _absolute(original)) for original, trashed in moves
    ))
    for trashed, original in plan:
        if not os.path.lexists(trashed):
            raise FileNotFoundError(f"Source is no longer in Trash: {trashed}")
        if os.path.lexists(original):
            raise FileExistsError(f"Restore target already exists: {original}")
        parent = os.path.dirname(original)
        if not os.path.exists(parent):
            raise FileNotFoundError(f"Restore parent no longer exists: {parent}")
        if not os.path.isdir(parent):
            raise NotADirectoryError(f"Restore parent is not a directory: {parent}")

    completed = []
    try:
        for trashed, original in plan:
            _rename_no_replace(trashed, original)
            completed.append((trashed, original))
    except Exception as error:
        raise SourceMoveError(f"Trash restore failed: {error}", completed) from error
    return completed


def relocate_path(path: str, old: str, new: str) -> str:
    """Remap a source path or descendant after a successful physical move."""
    if path == old:
        return new
    prefix = old.rstrip(os.sep) + os.sep
    if path.startswith(prefix):
        return os.path.join(new, path[len(prefix):])
    return path
