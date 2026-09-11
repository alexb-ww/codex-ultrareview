"""Disposable worktree copies that match the reviewed state, for reproduction runs."""
from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import shutil
import tarfile
from typing import Optional, Tuple

from .errors import GitError
from .gitio import run_git
from .safe_read import SafeReadError, read_regular_beneath
from .scope import ResolvedScope

MAX_UNTRACKED_COPY = 16 * 1024 * 1024


@dataclass(frozen=True)
class WorktreeCopy:
    path: Path
    repo: Path
    applied_diff: bool
    copied_untracked: Tuple[str, ...]
    notes: Tuple[str, ...]
    kind: str = 'worktree'


def _safe_member(member: tarfile.TarInfo) -> bool:
    name = member.name
    if not name or name.startswith('/') or '..' in name.split('/') or '\0' in name:
        return False
    return member.isfile() or member.isdir()


def _plain_copy(repo: Path, destination: Path, checkout: str) -> None:
    """Materialise ``checkout`` without git metadata (for sandboxes that protect .git)."""
    archive = run_git(repo, 'archive', '--format=tar', checkout)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as tar:
        members = [m for m in tar.getmembers() if _safe_member(m)]
        try:
            tar.extractall(destination, members=members, filter='data')
        except TypeError:
            tar.extractall(destination, members=members)


def capture_state_patch(repo: Path, scope: ResolvedScope, destination: Path) -> bool:
    """Save the working-tree diff against HEAD at snapshot time; returns True when non-empty."""
    if scope.kind == 'commit' or scope.head is None:
        destination.write_bytes(b'')
        return False
    patch = run_git(repo, 'diff', '--no-renames', '--binary', scope.head, '--', allow_fail=True)
    destination.write_bytes(patch)
    return bool(patch.strip())


def _apply_patch(repo: Path, copy: Path, base_rev: str, patch_path: Optional[Path]) -> Tuple[bool, Tuple[str, ...]]:
    if patch_path is not None and patch_path.is_file():
        patch = patch_path.read_bytes()
        source = f'captured patch {patch_path.name}'
    else:
        patch = run_git(repo, 'diff', '--no-renames', '--binary', base_rev, '--', allow_fail=True)
        source = 'live working-tree diff'
    if not patch.strip():
        return False, ()
    try:
        run_git(copy, 'apply', '--whitespace=nowarn', input_bytes=patch)
    except GitError as exc:
        return False, (f'could not apply the {source} to the copy: {exc}',)
    return True, ()


def _copy_untracked(repo: Path, copy: Path, scope: ResolvedScope) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    copied = []
    notes = []
    for target in scope.targets:
        if target.status != 'untracked' or target.redacted:
            continue
        try:
            data = read_regular_beneath(repo, target.path, MAX_UNTRACKED_COPY)
        except SafeReadError as exc:
            notes.append(f'untracked {target.path} not copied ({exc})')
            continue
        destination = copy / target.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        copied.append(target.path)
    return tuple(copied), tuple(notes)


def create_worktree_copy(repo: Path, scope: ResolvedScope, destination: Path, reuse: bool = False,
                         patch_path: Optional[Path] = None) -> WorktreeCopy:
    """Create a detached worktree at ``destination`` that mirrors the reviewed state.

    For branch/changes scopes the copy is HEAD plus the working-tree diff (the patch
    captured at snapshot time when ``patch_path`` is given, so later edits do not leak
    into reproduction) plus untracked target files. For commit scope it is that commit.
    With ``reuse`` an existing destination (from an earlier step) is returned as is.
    """
    if scope.head is None and scope.kind != 'changes':
        raise GitError('cannot create a worktree copy without a HEAD commit')
    destination = destination.resolve()
    if destination.exists():
        if reuse and destination.is_dir() and any(destination.iterdir()):
            kind = 'worktree' if (destination / '.git').exists() else 'plain'
            return WorktreeCopy(path=destination, repo=repo, applied_diff=False, copied_untracked=(),
                                notes=('reused the copy from an earlier step',), kind=kind)
        raise GitError(f'worktree destination already exists: {destination}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    checkout = scope.commit if scope.kind == 'commit' else scope.head
    if checkout is None:
        raise GitError('cannot create a worktree copy of an unborn repository')
    kind = 'worktree'
    notes: Tuple[str, ...] = ()
    try:
        run_git(repo, 'worktree', 'add', '--detach', '--quiet', str(destination), checkout)
    except GitError as exc:
        # Codex sandboxes protect .git, so `worktree add` fails there; a plain copy works.
        shutil.rmtree(destination, ignore_errors=True)
        _plain_copy(repo, destination, checkout)
        kind = 'plain'
        notes += (f'git worktree add was refused ({str(exc).splitlines()[-1][:120]}); a plain copy without .git was used',)
    applied = False
    copied: Tuple[str, ...] = ()
    if scope.kind != 'commit':
        applied, apply_notes = _apply_patch(repo, destination, checkout, patch_path)
        copied, copy_notes = _copy_untracked(repo, destination, scope)
        notes += apply_notes + copy_notes
    return WorktreeCopy(path=destination, repo=repo, applied_diff=applied, copied_untracked=copied, notes=notes, kind=kind)


def remove_worktree_copy(copy: WorktreeCopy) -> Tuple[str, ...]:
    notes: Tuple[str, ...] = ()
    if copy.kind == 'plain':
        shutil.rmtree(copy.path, ignore_errors=True)
        return notes
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
