#!/usr/bin/env python3
"""Codex PreToolUse hook: deny file edits while an Ultra Review session is active.

Install via ``install.py --with-hooks`` or merge ``hooks/hooks.json`` by hand.
The hook is a no-op unless a marker file named by ``ULTRAREVIEW_ACTIVE`` (or the
default ``$TMPDIR/ultrareview/REVIEW_ACTIVE``) exists; the skill creates the marker
at the start of a review and removes it at the end. Denial: exit code 2 with the
reason on stderr, as the Codex hooks contract specifies.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

WRITE_PATTERNS = (
    r'\bgit\s+(commit|push|checkout|reset|stash|clean|rebase|merge|cherry-pick|am|apply)\b',
    r'\brm\s+-', r'\bmv\s+', r'\bcp\s+', r'\btouch\s+', r'\bsed\s+-i', r'>\s*[^&\s]', r'\btee\s+',
    r'\bnpm\s+(install|ci|update)\b', r'\bpip\d?\s+install\b', r'\bgo\s+(get|mod\s+tidy)\b',
    r'\bbrew\s+install\b', r'\bapt(-get)?\s+install\b', r'\bchmod\s+', r'\bmkdir\s+',
)
EDIT_TOOLS = ('apply_patch', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit')


def marker_path() -> str:
    return os.environ.get('ULTRAREVIEW_ACTIVE') or os.path.join(tempfile.gettempdir(), 'ultrareview', 'REVIEW_ACTIVE')


def command_text(tool_input: object) -> str:
    if isinstance(tool_input, dict):
        value = tool_input.get('command') or tool_input.get('cmd') or ''
        if isinstance(value, list):
            return ' '.join(str(part) for part in value)
        return str(value)
    return str(tool_input or '')


def deny(reason: str) -> int:
    sys.stderr.write(f'ultrareview guard: {reason}\n')
    return 2


def main() -> int:
    if not os.path.exists(marker_path()):
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    tool = str(payload.get('tool_name', ''))
    if tool in EDIT_TOOLS:
        return deny(f'{tool} is disabled during an Ultra Review session (review-only)')
    command = command_text(payload.get('tool_input'))
    for pattern in WRITE_PATTERNS:
        if re.search(pattern, command):
            return deny(f'command looks like a write and is disabled during the review: {command[:120]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
