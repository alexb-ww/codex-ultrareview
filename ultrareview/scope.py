"""Review scope resolution: which files and which diff are under review."""
from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

from .errors import ScopeError
from .exclusions import classify_path
from .gitio import decode_nul, git_text, rev_parse, run_git

SCOPE_KINDS = ('branch', 'changes', 'commit', 'repo')
DIFF_SCOPES = ('branch', 'changes', 'commit')
EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'
MAX_INLINE_UNTRACKED = 256 * 1024
DEFAULT_BASE_CANDIDATES = ('main', 'master')


@dataclass(frozen=True)
class ScopeSpec:
    kind: str = 'branch'
    base: Optional[str] = None
    commit: Optional[str] = None
    paths: Tuple[str, ...] = ()

    def validate(self) -> 'ScopeSpec':
        if self.kind not in SCOPE_KINDS:
            raise ScopeError(f'unknown scope {self.kind!r}; expected one of {", ".join(SCOPE_KINDS)}')
        if self.kind == 'commit' and not self.commit:
            raise ScopeError('scope commit requires a commit id')
        for value in (self.base, self.commit):
            if value is not None and (value.startswith('-') or '\0' in value or not value.strip()):
                raise ScopeError(f'unsafe git reference {value!r}')
        return self


@dataclass(frozen=True)
class Limits:
    max_files: int = 500
    max_lines: int = 8000


@dataclass(frozen=True)
class LimitViolation:
    files: int
    lines: int
    max_files: int
    max_lines: int
    top_files: Tuple[Tuple[str, int], ...]

    def describe(self) -> str:
        head = (f'diff has {self.files} files and {self.lines} changed lines; '
                f'limits are {self.max_files} files / {self.max_lines} lines')
        biggest = ', '.join(f'{path} ({lines})' for path, lines in self.top_files)
        return f'{head}. Largest files: {biggest}' if biggest else head


@dataclass(frozen=True)
class Target:
    path: str
    status: str
    versions: Tuple[str, ...]
    redacted: bool = False
    lines_changed: int = 0


@dataclass(frozen=True)
class ResolvedScope:
    kind: str
    head: Optional[str]
    base_ref: Optional[str]
    base_commit: Optional[str]
    merge_base: Optional[str]
    base_source: Optional[str]
    commit: Optional[str]
    targets: Tuple[Target, ...]
    skipped: Tuple[Tuple[str, str], ...] = ()
    diff_text: str = ''
    index_diff_text: str = ''
    changed_files: int = 0
    changed_lines: int = 0
    notes: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        return not self.targets

    @property
    def target_paths(self) -> Tuple[str, ...]:
        return tuple(t.path for t in self.targets)

    @property
    def diff_base(self) -> Optional[str]:
        """The revision the reviewed state is compared against."""
        return self.merge_base or self.base_commit


def detect_default_base(repo: Path) -> Tuple[Optional[str], str]:
    """Return (ref, source) for the default base branch, or (None, 'none')."""
    origin_head = git_text(repo, 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD', allow_fail=True)
    if origin_head and rev_parse(repo, origin_head):
        return origin_head, 'origin/HEAD'
    for candidate in DEFAULT_BASE_CANDIDATES:
        if rev_parse(repo, candidate):
            return candidate, candidate
    return None, 'none'


def _name_status(repo: Path, *revisions: str, cached: bool = False) -> Dict[str, str]:
    args = ['diff', '--no-renames', '--name-status', '-z']
    if cached:
        args.append('--cached')
    args.extend(revisions)
    args.append('--')
    fields = decode_nul(run_git(repo, *args))
    pairs = zip(fields[0::2], fields[1::2])
    return {path: status[:1] for status, path in pairs}


def _numstat(repo: Path, *revisions: str) -> Dict[str, int]:
    out = run_git(repo, 'diff', '--no-renames', '--numstat', '-z', *revisions, '--').decode('utf-8', 'replace')
    counts: Dict[str, int] = {}
    for record in out.split('\0'):
        parts = record.split('\t')
        if len(parts) != 3:
            continue
        added, deleted, path = parts
        counts[path] = (int(added) if added.isdigit() else 0) + (int(deleted) if deleted.isdigit() else 0)
    return counts


def _untracked(repo: Path) -> Tuple[str, ...]:
    return decode_nul(run_git(repo, 'ls-files', '--others', '--exclude-standard', '-z'))


def _is_directory_entry(repo: Path, path: str) -> bool:
    candidate = repo / path
    return candidate.is_dir() and not candidate.is_symlink()


def _untracked_bytes(repo: Path, path: str) -> Optional[bytes]:
    full = repo / path
    try:
        if full.is_symlink() or not full.is_file() or full.stat().st_size > MAX_INLINE_UNTRACKED:
            return None
        return full.read_bytes()
    except OSError:
        return None


def _untracked_lines(repo: Path, path: str) -> int:
    data = _untracked_bytes(repo, path)
    if data is None or b'\0' in data[:8192]:
        return 0
    return data.count(b'\n') + (1 if data and not data.endswith(b'\n') else 0)


def _untracked_diff(repo: Path, path: str) -> str:
    data = _untracked_bytes(repo, path)
    if data is None:
        return f'diff --git a/{path} b/{path}\nUntracked file {path} not inlined (missing, symlink or larger than 256 KiB)\n'
    if b'\0' in data[:8192]:
        return f'diff --git a/{path} b/{path}\nBinary untracked file {path} (content not inlined)\n'
    text = run_git(repo, 'diff', '--no-index', '--', '/dev/null', path, allow_fail=True, ok_codes=(0, 1))
    return text.decode('utf-8', 'replace')


def _status_word(code: str) -> str:
    return {'A': 'added', 'M': 'modified', 'D': 'deleted', 'T': 'modified'}.get(code, 'modified')


def _diff_scope_targets(repo: Path, kind: str, new_rev: str, base_rev: str,
                        untracked: Tuple[str, ...], index_paths: Iterable[str]) -> Tuple[Tuple[Target, ...], Tuple[Tuple[str, str], ...]]:
    statuses = _name_status(repo, base_rev, new_rev) if kind == 'commit' else _name_status(repo, base_rev)
    numbers = _numstat(repo, base_rev, new_rev) if kind == 'commit' else _numstat(repo, base_rev)
    index_set = set(index_paths)
    reviewed_version = 'commit' if kind == 'commit' else 'worktree'
    targets = []
    skipped = []
    for path, code in sorted(statuses.items()):
        cls = classify_path(path, kind, repo)
        if cls.status == 'skipped':
            skipped.append((path, cls.reason or 'skipped'))
            continue
        if code != 'D' and kind != 'commit' and _is_directory_entry(repo, path):
            skipped.append((path, 'submodule or directory entry'))
            continue
        versions: Tuple[str, ...] = ('base',) if code == 'D' else (reviewed_version,)
        if code == 'M':
            versions = (reviewed_version, 'base')
        if path in index_set:
            versions = versions + ('index',)
        targets.append(Target(path=path, status=_status_word(code), versions=versions,
                              redacted=cls.redacted, lines_changed=numbers.get(path, 0)))
    for path in sorted(index_set - set(statuses)):
        cls = classify_path(path, kind, repo)
        if cls.status == 'target':
            targets.append(Target(path=path, status='modified', versions=('worktree', 'index', 'base'),
                                  redacted=cls.redacted))
    for path in sorted(untracked):
        cls = classify_path(path, kind, repo)
        if cls.status == 'skipped':
            skipped.append((path, cls.reason or 'skipped'))
            continue
        targets.append(Target(path=path, status='untracked', versions=('worktree',), redacted=cls.redacted,
                              lines_changed=_untracked_lines(repo, path)))
    return tuple(targets), tuple(skipped)


def _compose_diff(repo: Path, kind: str, base_rev: str, new_rev: str,
                  targets: Tuple[Target, ...]) -> str:
    tracked = [t.path for t in targets if t.status != 'untracked' and not t.redacted]
    chunks = []
    if tracked:
        revisions = (base_rev, new_rev) if kind == 'commit' else (base_rev,)
        chunks.append(run_git(repo, 'diff', '--no-renames', *revisions, '--', *tracked).decode('utf-8', 'replace'))
    chunks.extend(_untracked_diff(repo, t.path) for t in targets if t.status == 'untracked' and not t.redacted)
    redacted = [t.path for t in targets if t.redacted]
    if redacted:
        chunks.append('\n'.join(f'# redacted: {path} changed; content withheld (secret-like path)' for path in redacted) + '\n')
    return ''.join(chunk for chunk in chunks if chunk)


def _index_overlap(repo: Path, head: Optional[str]) -> Tuple[Tuple[str, ...], str]:
    if head is None:
        return (), ''
    staged = set(_name_status(repo, head, cached=True))
    unstaged = set(_name_status(repo))
    overlap = tuple(sorted(staged & unstaged))
    if not overlap:
        return (), ''
    text = run_git(repo, 'diff', '--no-renames', '--cached', head, '--', *overlap).decode('utf-8', 'replace')
    return overlap, text


def _resolve_branch(repo: Path, spec: ScopeSpec, head: str) -> Tuple[str, str, str, str]:
    if spec.base:
        base_ref, source = spec.base, 'flag'
    else:
        base_ref, source = detect_default_base(repo)
        if base_ref is None:
            raise ScopeError('no default base branch found (origin/HEAD, main, master); pass --base <ref>')
    base_commit = rev_parse(repo, base_ref)
    if base_commit is None:
        raise ScopeError(f'base ref {base_ref!r} does not resolve to a commit in this repository')
    merge_base = git_text(repo, 'merge-base', head, base_commit, allow_fail=True)
    if not merge_base:
        raise ScopeError(f'HEAD and {base_ref} share no history; review the whole tree with --scope repo, '
                         f'or pass a different --base')
    return base_ref, source, base_commit, merge_base


def _resolve_diff_scope(repo: Path, spec: ScopeSpec) -> ResolvedScope:
    head = rev_parse(repo, 'HEAD')
    notes: Tuple[str, ...] = ()
    base_ref = source = base_commit = merge_base = commit = None
    if spec.kind == 'branch':
        if head is None:
            raise ScopeError('repository has no commits yet; use --scope changes')
        base_ref, source, base_commit, merge_base = _resolve_branch(repo, spec, head)
        base_rev, new_rev = merge_base, head
        notes += (f'base {base_ref} chosen via {source}; merge-base {merge_base[:12]}',)
    elif spec.kind == 'commit':
        commit = rev_parse(repo, spec.commit or '')
        if commit is None:
            raise ScopeError(f'commit {spec.commit!r} not found')
        base_commit = rev_parse(repo, f'{commit}^') or EMPTY_TREE
        base_rev, new_rev = base_commit, commit
        if base_commit == EMPTY_TREE:
            notes += ('root commit: compared against the empty tree',)
    else:
        base_rev, new_rev = (head or EMPTY_TREE), (head or EMPTY_TREE)
        if head is None:
            notes += ('unborn repository: every file counts as added',)
    untracked = () if spec.kind == 'commit' else _untracked(repo)
    index_paths, index_diff = ((), '') if spec.kind == 'commit' else _index_overlap(repo, head)
    targets, skipped = _diff_scope_targets(repo, spec.kind, new_rev, base_rev, untracked, index_paths)
    diff_text = _compose_diff(repo, spec.kind, base_rev, new_rev, targets)
    if index_paths:
        notes += (f'index and working tree both differ for: {", ".join(index_paths)}',)
    changed_lines = sum(t.lines_changed for t in targets)
    return ResolvedScope(kind=spec.kind, head=head, base_ref=base_ref, base_commit=base_commit,
                         merge_base=merge_base, base_source=source, commit=commit, targets=targets,
                         skipped=skipped, diff_text=diff_text, index_diff_text=index_diff,
                         changed_files=len(targets), changed_lines=changed_lines, notes=notes)


def _matches_globs(path: str, globs: Tuple[str, ...]) -> bool:
    return not globs or any(fnmatch.fnmatchcase(path, pattern) for pattern in globs)


def _resolve_repo_scope(repo: Path, spec: ScopeSpec) -> ResolvedScope:
    head = rev_parse(repo, 'HEAD')
    tracked = set(decode_nul(run_git(repo, 'ls-files', '--cached', '-z')))
    untracked = set(_untracked(repo))
    targets = []
    skipped = []
    for path in sorted(tracked | untracked):
        if not _matches_globs(path, spec.paths):
            continue
        cls = classify_path(path, 'repo', repo)
        if cls.status == 'skipped':
            skipped.append((path, cls.reason or 'skipped'))
            continue
        if _is_directory_entry(repo, path):
            skipped.append((path, 'submodule or directory entry'))
            continue
        status = 'untracked' if path in untracked and path not in tracked else 'tracked'
        targets.append(Target(path=path, status=status, versions=('worktree',), redacted=cls.redacted))
    notes: Tuple[str, ...] = ()
    if spec.paths:
        notes += (f'repository scope narrowed to {", ".join(spec.paths)}',)
    return ResolvedScope(kind='repo', head=head, base_ref=None, base_commit=None, merge_base=None,
                         base_source=None, commit=None, targets=tuple(targets), skipped=tuple(skipped),
                         changed_files=len(targets), notes=notes)


def resolve_scope(repo: Path, spec: ScopeSpec) -> ResolvedScope:
    spec.validate()
    repo = repo.resolve()
    if spec.kind == 'repo':
        return _resolve_repo_scope(repo, spec)
    return _resolve_diff_scope(repo, spec)


def check_limits(scope: ResolvedScope, limits: Limits) -> Optional[LimitViolation]:
    if scope.kind == 'repo':
        return None
    if scope.changed_files <= limits.max_files and scope.changed_lines <= limits.max_lines:
        return None
    ranked = sorted(scope.targets, key=lambda t: t.lines_changed, reverse=True)[:10]
    return LimitViolation(files=scope.changed_files, lines=scope.changed_lines,
                          max_files=limits.max_files, max_lines=limits.max_lines,
                          top_files=tuple((t.path, t.lines_changed) for t in ranked))
