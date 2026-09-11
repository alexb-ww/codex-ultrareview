#!/usr/bin/env python3
"""Copy the skill (skills/ultrareview) into ~/.agents/skills/ultrareview without the plugin system.

The skill directory holds SKILL.md, agents/, scripts/, references/ and kit/ (the driver,
prompts, hooks and the CLI launcher). Nothing is downloaded and
config.toml is never touched. An existing installation is kept unless --force is
given, in which case it is moved aside with a timestamp suffix. --with-hooks writes
kit/hooks/hooks.json to ~/.codex/hooks.json when that file does not exist yet;
otherwise the snippet to merge is printed.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
import time
from typing import Tuple

SOURCE = Path(__file__).resolve().parent
SKILL_SOURCE = SOURCE / 'skills' / 'ultrareview'
SKILL_PARTS = ('SKILL.md', 'agents', 'scripts', 'references', 'kit')
REQUIRED = SKILL_PARTS + ('kit/ultrareview', 'kit/prompts', 'kit/hooks', 'kit/bin', 'kit/VERSION')
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', '.git')


@dataclass(frozen=True)
class InstallResult:
    skill_dir: Path
    notes: Tuple[str, ...]


def _check_source(source: Path) -> None:
    missing = [part for part in REQUIRED if not (source / 'skills' / 'ultrareview' / part).exists()]
    if missing:
        raise FileNotFoundError(f'run the installer from the complete package; missing: {", ".join(missing)}')


def _move_aside(path: Path) -> Path:
    backup = path.with_name(f'{path.name}.bak-{time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())}')
    path.rename(backup)
    return backup


def install(home: Path, source: Path = SOURCE, with_hooks: bool = False, dry_run: bool = False,
            force: bool = False) -> InstallResult:
    _check_source(source)
    home = home.expanduser().resolve()
    skill_dir = home / '.agents' / 'skills' / 'ultrareview'
    notes: Tuple[str, ...] = ()
    if skill_dir.exists() or skill_dir.is_symlink():
        if not force:
            raise FileExistsError(f'{skill_dir} already exists; re-run with --force to replace it (a backup is kept)')
        if not dry_run:
            notes += (f'previous installation moved to {_move_aside(skill_dir)}',)
    if not dry_run:
        skill_dir.mkdir(parents=True)
        for part in SKILL_PARTS:
            src = source / 'skills' / 'ultrareview' / part
            if src.is_dir():
                shutil.copytree(src, skill_dir / part, ignore=IGNORE)
            else:
                shutil.copy2(src, skill_dir / part)
        (skill_dir / 'kit' / 'bin' / 'ultrareview').chmod(0o755)
    notes += _hooks(home, source, with_hooks, dry_run)
    return InstallResult(skill_dir=skill_dir, notes=notes)


def _hooks(home: Path, source: Path, with_hooks: bool, dry_run: bool) -> Tuple[str, ...]:
    if not with_hooks:
        return ()
    target = home / '.codex' / 'hooks.json'
    snippet = (source / 'skills' / 'ultrareview' / 'kit' / 'hooks' / 'hooks.json').read_text(encoding='utf-8')
    if target.exists():
        return (f'{target} already exists; merge this snippet by hand:\n{snippet}',)
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(snippet, encoding='utf-8')
    return (f'hooks written to {target} (PreToolUse guard; active only while a review marker exists)',)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--with-hooks', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    try:
        result = install(Path.home(), SOURCE, args.with_hooks, args.dry_run, args.force)
    except (OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    verb = 'Would install' if args.dry_run else 'Installed'
    print(f'{verb} skill to {result.skill_dir}')
    for note in result.notes:
        print(f'- {note}')
    print('CLI launcher: ' + str(result.skill_dir / 'kit' / 'bin' / 'ultrareview')
          + '  (symlink it into ~/.local/bin as `ultrareview`)')
    print('Inside Codex: $ultrareview scope=branch profile=deep   (restart Codex once so the skill is listed)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
