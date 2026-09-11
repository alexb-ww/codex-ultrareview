"""Disposable worktree copies that match the reviewed state, for reproduction runs."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Tuple

from .errors import GitError
from .gitio import run_git
from .scope import ResolvedScope

MAX_UNTRACKED_COPY = 16 * 1024 * 1024


@dataclass(frozen=True)
class WorktreeCopy:
    path: Path
    repo: Path
    applied_diff: bool
    copied_untracked: Tuple[str, ...]
    notes: Tuple[str, ...]


def _apply_working_tree_diff(repo: Path, copy: Path, base_rev: str) -> Tuple[bool, Tuple[str, ...]]:
    patch = run_git(repo, 'diff', '--no-renames', '--binary', base_rev, '--', allow_fail=True)
    if not patch.strip():
        return False, ()
    patch_path = copy / '.ultrareview-state.patch'
    patch_path.write_bytes(patch)
    try:
        run_git(copy, 'apply', '--whitespace=nowarn', str(patch_path))
    except GitError as exc:
        return False, (f'could not apply working-tree diff to the copy: {exc}',)
    finally:
        try:
            patch_path.unlink()
        except OSError:
            pass
    return True, ()


def _copy_untracked(repo: Path, copy: Path, scope: ResolvedScope) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    copied = []
    notes = []
    for target in scope.targets:
        if target.status != 'untracked' or target.redacted:
            continue
        source = repo / target.path
        if source.is_symlink() or not source.is_file():
            notes.append(f'untracked {target.path} not copied (symlink or not a regular file)')
            continue
        if source.stat().st_size > MAX_UNTRACKED_COPY:
            notes.append(f'untracked {target.path} not copied (larger than 16 MiB)')
            continue
        destination = copy / target.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(target.path)
    return tuple(copied), tuple(notes)


def create_worktree_copy(repo: Path, scope: ResolvedScope, destination: Path, reuse: bool = False) -> WorktreeCopy:
    """Create a detached worktree at ``destination`` that mirrors the reviewed state.

    For branch/changes scopes the copy is HEAD plus the working-tree diff plus
    untracked target files. For commit scope it is that commit. Repo scope mirrors
    HEAD plus the working tree the same way as changes. With ``reuse`` an existing
    destination (from an earlier step) is returned as is.
    """
    if scope.head is None and scope.kind != 'changes':
        raise GitError('cannot create a worktree copy without a HEAD commit')
    destination = destination.resolve()
    if destination.exists():
        if reuse and (destination / '.git').exists():
            return WorktreeCopy(path=destination, repo=repo, applied_diff=False, copied_untracked=(),
                                notes=('reused the worktree copy from an earlier step',))
        raise GitError(f'worktree destination already exists: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    checkout = scope.commit if scope.kind == 'commit' else scope.head
    if checkout is None:
        raise GitError('cannot create a worktree copy of an unborn repository')
    run_git(repo, 'worktree', 'add', '--detach', '--quiet', str(destination), checkout)
    notes: Tuple[str, ...] = ()
    applied = False
    copied: Tuple[str, ...] = ()
    if scope.kind != 'commit':
        applied, apply_notes = _apply_working_tree_diff(repo, destination, checkout)
        copied, copy_notes = _copy_untracked(repo, destination, scope)
        notes = apply_notes + copy_notes
    return WorktreeCopy(path=destination, repo=repo, applied_diff=applied, copied_untracked=copied, notes=notes)


def remove_worktree_copy(copy: WorktreeCopy) -> Tuple[str, ...]:
    notes: Tuple[str, ...] = ()
    try:
        run_git(copy.repo, 'worktree', 'remove', '--force', str(copy.path))
    except GitError as exc:
        notes += (f'git worktree remove failed: {exc}',)
        shutil.rmtree(copy.path, ignore_errors=True)
    try:
        run_git(copy.repo, 'worktree', 'prune', allow_fail=True)
    except GitError:
        pass
    return notes
