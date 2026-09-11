"""Shared helpers for the ultrareview test-suite: disposable git repositories."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'ultrareview'
KIT = SKILL / 'kit'
for entry in (str(KIT), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

GIT_IDENTITY = ('-c', 'user.name=ur-test', '-c', 'user.email=ur-test@example.invalid',
                '-c', 'commit.gpgsign=false', '-c', 'init.defaultBranch=main')


class TempRepo:
    """A throw-away git repository resolved to its real path."""

    def __init__(self, name: str = 'repo') -> None:
        self.parent = Path(tempfile.mkdtemp(prefix='ur-test-')).resolve()
        self.path = self.parent / name
        self.path.mkdir()
        self.git('init', '-q', '-b', 'main')

    def git(self, *args: str, check: bool = True, cwd: Optional[Path] = None) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        result = subprocess.run(['git', *GIT_IDENTITY, *args], cwd=str(cwd or self.path),
                                capture_output=True, text=True, env=env)
        if check and result.returncode != 0:
            raise AssertionError(f'git {" ".join(args)} failed: {result.stderr}')
        return result.stdout.strip()

    def write(self, rel: str, text: str) -> Path:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8')
        return target

    def write_bytes(self, rel: str, data: bytes) -> Path:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return target

    def commit(self, message: str = 'change') -> str:
        self.git('add', '-A')
        self.git('commit', '-q', '--allow-empty', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def cleanup(self) -> None:
        shutil.rmtree(self.parent, ignore_errors=True)


def seeded_repo() -> TempRepo:
    """A repository with two commits on main: app.py, util.py, README.md."""
    repo = TempRepo()
    repo.write('src/app.py', 'def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n')
    repo.write('src/util.py', 'def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n')
    repo.write('README.md', '# demo\n')
    repo.commit('init')
    repo.write('src/util.py', 'def clamp(x, lo, hi):\n    if lo > hi:\n        raise ValueError(lo)\n    return max(lo, min(x, hi))\n')
    repo.commit('guard')
    return repo
