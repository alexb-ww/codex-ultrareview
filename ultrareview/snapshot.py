"""Inventory of the reviewed files, drift detection and line reading for the gate."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Dict, Optional, Tuple

from .errors import GitError
from .gitio import run_git
from .scope import ResolvedScope

MAX_CONTENT = 16 * 1024 * 1024
BINARY_PROBE = 8192
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class FileRecord:
    path: str
    version: str
    kind: str
    sha256: Optional[str] = None
    size: int = 0
    lines: int = 0
    redacted: bool = False
    reason: Optional[str] = None


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    repo_root: str
    scope_kind: str
    head: Optional[str]
    base_rev: Optional[str]
    commit: Optional[str]
    index_sha: str
    status_sha: str
    files: Tuple[FileRecord, ...]
    targets: Tuple[str, ...]
    skipped: Tuple[Tuple[str, str], ...]
    taken_at: str


@dataclass(frozen=True)
class Drift:
    unchanged: bool
    changed_paths: Tuple[str, ...]
    head_changed: bool
    index_changed: bool
    status_changed: bool


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def count_lines(data: bytes) -> int:
    return data.count(b'\n') + (1 if data and not data.endswith(b'\n') else 0)


def _read_worktree(repo: Path, path: str) -> Tuple[Optional[bytes], str, Optional[str]]:
    full = repo.joinpath(*path.split('/'))
    try:
        info = full.lstat()
    except FileNotFoundError:
        return None, 'missing', None
    except OSError as exc:
        return None, 'unreadable', exc.__class__.__name__
    if stat.S_ISLNK(info.st_mode):
        return None, 'symlink', 'symlink target not followed'
    if not stat.S_ISREG(info.st_mode):
        return None, 'special', 'not a regular file'
    if info.st_size > MAX_CONTENT:
        return None, 'large', f'{info.st_size} bytes exceeds 16 MiB'
    try:
        fd = os.open(full, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as handle:
            data = handle.read(MAX_CONTENT + 1)
    except OSError as exc:
        return None, 'unreadable', exc.__class__.__name__
    if len(data) > MAX_CONTENT:
        return None, 'large', 'grew past 16 MiB while reading'
    return data, 'file', None


def _revision_for(version: str, snapshot_like: Dict[str, Optional[str]]) -> Optional[str]:
    if version == 'index':
        return ''
    if version == 'base':
        return snapshot_like.get('base_rev')
    if version == 'commit':
        return snapshot_like.get('commit')
    return None


def _read_revision(repo: Path, path: str, revision: Optional[str]) -> Tuple[Optional[bytes], str, Optional[str]]:
    if revision is None:
        return None, 'missing', 'no revision for this version'
    try:
        data = run_git(repo, 'show', f'{revision}:{path}')
    except GitError:
        return None, 'missing', None
    if len(data) > MAX_CONTENT:
        return None, 'large', f'{len(data)} bytes exceeds 16 MiB'
    return data, 'file', None


def read_content(repo: Path, path: str, version: str, refs: Dict[str, Optional[str]]) -> Tuple[Optional[bytes], str, Optional[str]]:
    if version == 'worktree':
        return _read_worktree(repo, path)
    return _read_revision(repo, path, _revision_for(version, refs))


def _record(repo: Path, path: str, version: str, redacted: bool, refs: Dict[str, Optional[str]]) -> FileRecord:
    data, kind, reason = read_content(repo, path, version, refs)
    if data is None:
        return FileRecord(path=path, version=version, kind=kind, redacted=redacted, reason=reason)
    binary = b'\0' in data[:BINARY_PROBE]
    return FileRecord(path=path, version=version, kind='binary' if binary else 'file',
                      sha256=digest(data), size=len(data), lines=0 if binary else count_lines(data),
                      redacted=redacted)


def _refs(scope: ResolvedScope) -> Dict[str, Optional[str]]:
    return {'base_rev': scope.diff_base, 'commit': scope.commit}


def _state_hashes(repo: Path) -> Tuple[str, str]:
    index = run_git(repo, 'ls-files', '--stage', '-z', allow_fail=True)
    status = run_git(repo, 'status', '--porcelain=v1', '-z', '--untracked-files=all', '--no-renames', allow_fail=True)
    return digest(index), digest(status)


def _identity(records: Tuple[FileRecord, ...], repo_root: str, scope_kind: str,
              head: Optional[str], base_rev: Optional[str], index_sha: str) -> str:
    payload = {'repo_root': repo_root, 'scope_kind': scope_kind, 'head': head, 'base_rev': base_rev,
               'index_sha': index_sha, 'files': [asdict(r) for r in records]}
    return digest(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def take_snapshot(repo: Path, scope: ResolvedScope) -> Snapshot:
    repo = repo.resolve()
    refs = _refs(scope)
    records = tuple(_record(repo, target.path, version, target.redacted, refs)
                    for target in scope.targets for version in target.versions)
    index_sha, status_sha = _state_hashes(repo)
    snapshot_id = _identity(records, str(repo), scope.kind, scope.head, scope.diff_base, index_sha)
    return Snapshot(snapshot_id=snapshot_id, repo_root=str(repo), scope_kind=scope.kind, head=scope.head,
                    base_rev=scope.diff_base, commit=scope.commit, index_sha=index_sha, status_sha=status_sha,
                    files=records, targets=scope.target_paths, skipped=scope.skipped,
                    taken_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))


def detect_drift(snapshot: Snapshot, repo: Path) -> Drift:
    repo = repo.resolve()
    refs = {'base_rev': snapshot.base_rev, 'commit': snapshot.commit}
    changed = tuple(sorted({rec.path for rec in snapshot.files
                            if _record(repo, rec.path, rec.version, rec.redacted, refs) != rec}))
    head = run_git(repo, 'rev-parse', '--verify', '--quiet', 'HEAD', allow_fail=True).decode().strip() or None
    index_sha, status_sha = _state_hashes(repo)
    head_changed = head != snapshot.head
    index_changed = index_sha != snapshot.index_sha
    status_changed = status_sha != snapshot.status_sha
    unchanged = not changed and not head_changed and not index_changed and not status_changed
    return Drift(unchanged=unchanged, changed_paths=changed, head_changed=head_changed,
                 index_changed=index_changed, status_changed=status_changed)


def snapshot_to_dict(snapshot: Snapshot) -> Dict[str, object]:
    return {'schema_version': SCHEMA_VERSION, **asdict(snapshot)}


def snapshot_from_dict(payload: Dict[str, object]) -> Snapshot:
    files = tuple(FileRecord(**item) for item in payload.get('files', []))  # type: ignore[arg-type]
    skipped = tuple((item[0], item[1]) for item in payload.get('skipped', []))  # type: ignore[index]
    return Snapshot(snapshot_id=str(payload['snapshot_id']), repo_root=str(payload['repo_root']),
                    scope_kind=str(payload['scope_kind']), head=payload.get('head'),  # type: ignore[arg-type]
                    base_rev=payload.get('base_rev'), commit=payload.get('commit'),  # type: ignore[arg-type]
                    index_sha=str(payload['index_sha']), status_sha=str(payload['status_sha']),
                    files=files, targets=tuple(payload.get('targets', ())),  # type: ignore[arg-type]
                    skipped=skipped, taken_at=str(payload['taken_at']))


class SnapshotReader:
    """Reads the lines of a recorded file version; redacted content is never returned."""

    def __init__(self, repo: Path, snapshot: Snapshot) -> None:
        self.repo = repo.resolve()
        self.snapshot = snapshot
        self._refs = {'base_rev': snapshot.base_rev, 'commit': snapshot.commit}
        self._records = {(r.path, r.version): r for r in snapshot.files}

    def record(self, path: str, version: str) -> Optional[FileRecord]:
        return self._records.get((path, version))

    def read_lines(self, path: str, version: str) -> Tuple[str, ...]:
        record = self.record(path, version)
        if record is None or record.kind != 'file' or record.redacted:
            return ()
        data, kind, _ = read_content(self.repo, path, version, self._refs)
        if data is None or kind != 'file':
            return ()
        text = data.decode('utf-8', 'replace')
        lines = text.split('\n')
        if lines and lines[-1] == '':
            lines = lines[:-1]
        return tuple(line.rstrip('\r') for line in lines)
