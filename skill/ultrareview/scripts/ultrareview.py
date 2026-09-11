#!/usr/bin/env python3
"""Relocatable shim: runs the ultrareview CLI from an installed skill or from the source tree.

The installer copies the package, prompts and hooks into ``<skill>/kit``; in the source
tree the package lives three levels up. Whichever exists first is used.
"""
from __future__ import annotations

from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
CANDIDATES = (HERE.parent / 'kit', HERE.parents[2])


def locate_root() -> Path:
    for root in CANDIDATES:
        if (root / 'ultrareview' / '__init__.py').is_file() and (root / 'prompts').is_dir():
            return root
    raise SystemExit('ultrareview: package not found next to this skill (expected <skill>/kit or the source tree)')


def main() -> int:
    root = locate_root()
    sys.path.insert(0, str(root))
    from ultrareview.cli import main as cli_main
    return cli_main()


if __name__ == '__main__':
    sys.exit(main())
