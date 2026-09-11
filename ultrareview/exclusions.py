"""Path classification: what is a review target, what is redacted, what is skipped.

The rules are deliberately conservative about *skipping*: in diff scopes a changed
path is always a target. Only whole-repository scope skips dependency and build
directories, and only when the directory is at the repository root or carries a
dependency marker, because a feature folder named ``vendor/`` or ``build/`` deep
inside ``src/`` is source code.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional, Tuple

SECRET_BASENAMES = frozenset({'.netrc', '.npmrc', '.pypirc'})
SECRET_PREFIXES = ('id_rsa', 'id_ed25519', 'id_dsa', 'id_ecdsa')
SECRET_SUFFIXES = frozenset({'.pem', '.key', '.p12', '.pfx', '.jks', '.keystore', '.tfvars'})
ALWAYS_SKIP_DIRS = frozenset({'node_modules', '__pycache__', '.venv', 'venv', '.gradle', '.next', 'Pods'})
MARKED_SKIP_DIRS = frozenset({'dist', 'build', 'vendor'})
DEPENDENCY_MARKERS = ('package.json', 'modules.txt', '.package-lock.json', 'go.sum', 'pyvenv.cfg')
DIFF_SCOPES = ('branch', 'changes', 'commit')


@dataclass(frozen=True)
class Classification:
    status: str
    redacted: bool = False
    reason: Optional[str] = None


def is_safe_relative(path: str) -> bool:
    if not isinstance(path, str) or not path or '\0' in path:
        return False
    posix = PurePosixPath(path)
    if posix.is_absolute() or PureWindowsPath(path).drive or path == '.':
        return False
    return '..' not in posix.parts and '\\' not in path


def is_secret_like(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    if name in SECRET_BASENAMES:
        return True
    if name.startswith('.env'):
        return True
    if name.startswith(SECRET_PREFIXES):
        return True
    if name.startswith('service-account') and name.endswith('.json'):
        return True
    return PurePosixPath(name).suffix in SECRET_SUFFIXES


@lru_cache(maxsize=4096)
def _has_dependency_marker(directory: str) -> bool:
    base = Path(directory)
    return any((base / marker).exists() for marker in DEPENDENCY_MARKERS)


def _dependency_dir_reason(parts: Tuple[str, ...], repo_root: Path) -> Optional[str]:
    for index, part in enumerate(parts[:-1]):
        if part in ALWAYS_SKIP_DIRS:
            return f'dependency/cache directory {part}/'
        if part in MARKED_SKIP_DIRS:
            directory = repo_root.joinpath(*parts[:index + 1])
            if index == 0 or _has_dependency_marker(str(directory)):
                return f'dependency/build directory {"/".join(parts[:index + 1])}/'
    return None


def classify_path(path: str, scope_kind: str, repo_root: Path) -> Classification:
    """Classify ``path`` (POSIX, repo-relative) for the given scope kind."""
    if not is_safe_relative(path):
        return Classification(status='skipped', reason='unsafe path')
    redacted = is_secret_like(path)
    if scope_kind in DIFF_SCOPES:
        return Classification(status='target', redacted=redacted)
    reason = _dependency_dir_reason(PurePosixPath(path).parts, repo_root)
    if reason:
        return Classification(status='skipped', redacted=redacted, reason=reason)
    return Classification(status='target', redacted=redacted)
