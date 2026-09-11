"""Read regular files beneath a trusted root without following any symlink.

Every path component is opened relative to a directory descriptor with
``O_NOFOLLOW``, so a symlinked ancestor (``src -> /somewhere/else``) or a
symlinked leaf can never redirect a read outside the reviewed repository.
Failures are reported as ``SafeReadError``; callers record the path as skipped
instead of falling back to a less careful reader.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Tuple

DEFAULT_LIMIT = 16 * 1024 * 1024


class SafeReadError(Exception):
    """The path is unsafe, not a regular file, too large or unreadable."""


def _split(relative: str) -> Tuple[str, ...]:
    if not isinstance(relative, str) or not relative or '\0' in relative or '\\' in relative:
        raise SafeReadError('unsafe relative path')
    parts = tuple(relative.split('/'))
    if any(part in ('', '.', '..') for part in parts):
        raise SafeReadError('unsafe relative path')
    return parts


def read_regular_beneath(root: Path, relative: str, limit: int = DEFAULT_LIMIT) -> bytes:
    """Return the bytes of ``root/relative`` or raise SafeReadError."""
    parts = _split(relative)
    if not hasattr(os, 'O_NOFOLLOW') or not hasattr(os, 'O_DIRECTORY'):
        raise SafeReadError('no descriptor-relative reader on this platform')
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        parent = os.open(str(root), dir_flags)
    except OSError as exc:
        raise SafeReadError(f'cannot open root: {exc.__class__.__name__}') from exc
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, dir_flags, dir_fd=parent)
            except OSError as exc:
                raise SafeReadError(f'{exc.__class__.__name__} at {part}') from exc
            os.close(parent)
            parent = child
        leaf_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, 'O_NONBLOCK', 0)
        try:
            leaf = os.open(parts[-1], leaf_flags, dir_fd=parent)
        except OSError as exc:
            raise SafeReadError(exc.__class__.__name__) from exc
        with os.fdopen(leaf, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise SafeReadError('not a regular file')
            if info.st_size > limit:
                raise SafeReadError('larger than the content limit')
            data = handle.read(limit + 1)
            if len(data) > limit:
                raise SafeReadError('grew past the content limit while reading')
            return data
    finally:
        os.close(parent)


def stat_beneath(root: Path, relative: str) -> os.stat_result:
    """lstat of the leaf after walking the ancestors without following symlinks."""
    parts = _split(relative)
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        parent = os.open(str(root), dir_flags)
    except OSError as exc:
        raise SafeReadError(f'cannot open root: {exc.__class__.__name__}') from exc
    try:
        for part in parts[:-1]:
            try:
                child = os.open(part, dir_flags, dir_fd=parent)
            except OSError as exc:
                raise SafeReadError(f'{exc.__class__.__name__} at {part}') from exc
            os.close(parent)
            parent = child
        try:
            return os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        except OSError as exc:
            raise SafeReadError(exc.__class__.__name__) from exc
    finally:
        os.close(parent)
