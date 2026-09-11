"""Ultra Review for Codex CLI: a multi-agent, evidence-gated deep code review."""
from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
PROMPTS_DIR = PROJECT_ROOT / 'prompts'


def read_version() -> str:
    version_file = PROJECT_ROOT / 'VERSION'
    try:
        return version_file.read_text(encoding='utf-8').strip() or '0.0.0'
    except OSError:
        return '0.0.0'


__version__ = read_version()
