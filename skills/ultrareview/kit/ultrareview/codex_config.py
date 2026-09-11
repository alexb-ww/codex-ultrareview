"""Read the model and reasoning effort Codex applies when the driver does not override them.

Only the top-level ``model`` and ``model_reasoning_effort`` keys of ``config.toml`` are
read (profiles and per-project overrides are out of scope); the values are shown in the
plan and the run passport so a report always says which model reviewed the code.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Dict, Optional, Tuple

TOP_LEVEL_KEYS = ('model', 'model_reasoning_effort', 'model_provider')
_ASSIGNMENT = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$')


def codex_home() -> Path:
    override = os.environ.get('CODEX_HOME')
    return Path(override).expanduser() if override else Path.home() / '.codex'


def _unquote(raw: str) -> str:
    value = raw.strip()
    if value and value[0] in ('"', "'"):
        closing = value.find(value[0], 1)
        return value[1:closing] if closing > 0 else value[1:]
    return value.split('#', 1)[0].strip()


def read_top_level(config_path: Optional[Path] = None) -> Dict[str, str]:
    """Top-level string keys of config.toml, stopping at the first table header."""
    path = config_path or (codex_home() / 'config.toml')
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return {}
    values: Dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('['):
            break
        match = _ASSIGNMENT.match(line)
        if match and match.group(1) in TOP_LEVEL_KEYS:
            values[match.group(1)] = _unquote(match.group(2))
    return values


def effective_model(model_override: Optional[str], effort_override: Optional[str],
                    config_path: Optional[Path] = None) -> Tuple[str, str]:
    """(model, effort) as they will apply: explicit overrides win, then config.toml, then unknown."""
    values = read_top_level(config_path)
    model = model_override or values.get('model') or 'unknown (codex default)'
    effort = effort_override or values.get('model_reasoning_effort') or 'unknown (codex default)'
    source_model = 'flag' if model_override else ('config.toml' if values.get('model') else 'default')
    source_effort = 'flag' if effort_override else ('config.toml' if values.get('model_reasoning_effort') else 'default')
    return f'{model} ({source_model})', f'{effort} ({source_effort})'
