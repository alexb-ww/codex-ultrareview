"""Thin, safe wrapper around git for read-only inventory work.

Every call scrubs inherited GIT_* redirection variables, disables fsmonitor and,
for diff-like commands, neutralises repository-configured clean/process filters
and external diff/textconv drivers so that reading a diff cannot execute code
configured inside the repository under review.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Optional, Tuple

from .errors import GitError

SCRUBBED_ENV = (
    'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR',
    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE',
)
DIFF_COMMANDS = ('diff', 'show', 'diff-index', 'diff-tree', 'diff-files', 'log')
FILTER_PATTERN = r'^filter\..*\.(clean|smudge|process|required)$'
DEFAULT_TIMEOUT = 120


def git_env() -> dict:
    env = {key: value for key, value in os.environ.items() if key not in SCRUBBED_ENV}
    return {**env, 'GIT_OPTIONAL_LOCKS': '0', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_NO_LAZY_FETCH': '1', 'LC_ALL': 'C'}


def decode_nul(data: bytes) -> Tuple[str, ...]:
    return tuple(os.fsdecode(item) for item in data.split(b'\0') if item)


def _run(argv: Tuple[str, ...], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(argv), capture_output=True, env=git_env(),
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise GitError('git executable not found on PATH') from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f'git timed out after {timeout}s: {" ".join(argv[:6])}') from exc
    except OSError as exc:
        raise GitError(f'git could not be started: {exc}') from exc


def filter_neutralisers(repo: Path, timeout: int = DEFAULT_TIMEOUT) -> Tuple[str, ...]:
    """Return -c flags that blank every configured content filter driver."""
    result = _run(('git', '-C', str(repo), 'config', '--null', '--name-only',
                   '--get-regexp', FILTER_PATTERN), timeout)
    if result.returncode not in (0, 1):
        raise GitError('cannot enumerate git content filters safely')
    drivers = sorted({key.rsplit('.', 1)[0] for key in decode_nul(result.stdout)})
    flags: Tuple[str, ...] = ()
    for driver in drivers:
        for setting, value in (('clean', ''), ('smudge', ''), ('process', ''), ('required', 'false')):
            flags += ('-c', f'{driver}.{setting}={value}')
    return flags


def run_git(repo: Path, *args: str, allow_fail: bool = False,
            timeout: int = DEFAULT_TIMEOUT, ok_codes: Tuple[int, ...] = (0,)) -> bytes:
    """Run git inside ``repo`` and return stdout bytes.

    Diff-like commands additionally get ``--no-ext-diff --no-textconv`` and the
    filter neutralisers. Exit codes outside ``ok_codes`` raise a GitError carrying
    stderr unless ``allow_fail`` is set, in which case empty bytes are returned.
    """
    prefix: Tuple[str, ...] = ('git', '-c', 'core.fsmonitor=false', '-C', str(repo))
    body: Tuple[str, ...] = tuple(args)
    if body and body[0] in DIFF_COMMANDS:
        prefix += filter_neutralisers(repo, timeout)
        body = (body[0], '--no-ext-diff', '--no-textconv') + body[1:]
    result = _run(prefix + body, timeout)
    if result.returncode not in ok_codes:
        if allow_fail:
            return b''
        detail = result.stderr.decode('utf-8', 'replace').strip()
        raise GitError(detail or f'git {args[0] if args else ""} failed with exit {result.returncode}')
    return result.stdout


def git_text(repo: Path, *args: str, allow_fail: bool = False,
             timeout: int = DEFAULT_TIMEOUT) -> str:
    return run_git(repo, *args, allow_fail=allow_fail, timeout=timeout).decode('utf-8', 'replace').strip()


def repo_root(path: Path) -> Path:
    """Resolve the repository top level for ``path`` (must be inside a work tree)."""
    top = git_text(path.resolve(), 'rev-parse', '--show-toplevel')
    if not top:
        raise GitError(f'{path} is not inside a git work tree')
    return Path(top).resolve()


def rev_parse(repo: Path, ref: str) -> Optional[str]:
    """Return the commit id for ``ref`` or None when it does not resolve."""
    if not ref or ref.startswith('-') or '\0' in ref:
        return None
    text = git_text(repo, 'rev-parse', '--verify', '--quiet', '--end-of-options',
                    f'{ref}^{{commit}}', allow_fail=True)
    return text or None
