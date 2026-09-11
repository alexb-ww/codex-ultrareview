#!/usr/bin/env python3
"""Build an evaluation repository from a corpus case: base on main, defects on a branch.

Usage: python3 corpus/build.py <case> <destination> [--branch feature] [--dirty]
--dirty leaves the last defect file uncommitted so the working-tree path is exercised.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
IDENTITY = ('-c', 'user.name=corpus', '-c', 'user.email=corpus@example.invalid', '-c', 'commit.gpgsign=false')


def git(repo: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    result = subprocess.run(['git', *IDENTITY, *args], cwd=str(repo), capture_output=True, text=True, env=env)
    if result.returncode != 0:
        raise SystemExit(f'git {" ".join(args)} failed: {result.stderr}')
    return result.stdout.strip()


def sync_tree(source: Path, repo: Path) -> None:
    for child in repo.iterdir():
        if child.name == '.git':
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for path in source.rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts:
            target = repo / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def build(case: str, destination: Path, branch: str, dirty: bool) -> Path:
    case_dir = HERE / case
    if not (case_dir / 'base').is_dir() or not (case_dir / 'feature').is_dir():
        raise SystemExit(f'unknown case {case!r}; expected {case_dir}/base and /feature')
    destination = destination.resolve()
    if destination.exists():
        raise SystemExit(f'destination exists: {destination}')
    destination.mkdir(parents=True)
    git(destination, 'init', '-q', '-b', 'main')
    sync_tree(case_dir / 'base', destination)
    git(destination, 'add', '-A')
    git(destination, 'commit', '-q', '-m', 'base: correct implementation')
    git(destination, 'checkout', '-q', '-b', branch)
    sync_tree(case_dir / 'feature', destination)
    if dirty:
        git(destination, 'add', '-A')
        git(destination, 'reset', '-q', '--', 'svc/api.py')
        git(destination, 'commit', '-q', '-m', 'feature: request lifecycle refactor')
    else:
        git(destination, 'add', '-A')
        git(destination, 'commit', '-q', '-m', 'feature: request lifecycle refactor')
    shutil.copy2(case_dir / 'expected.json', destination.parent / f'{destination.name}.expected.json')
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case')
    parser.add_argument('destination')
    parser.add_argument('--branch', default='feature')
    parser.add_argument('--dirty', action='store_true')
    args = parser.parse_args()
    path = build(args.case, Path(args.destination), args.branch, args.dirty)
    print(path)
    return 0


if __name__ == '__main__':
    sys.exit(main())
