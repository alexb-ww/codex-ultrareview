"""Execution ledger: what each agent actually ran, used to authenticate claims."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Dict, Iterable, Optional, Tuple

from .runner import AgentResult

SHELL_WRAPPER = re.compile(r'^(?:/bin/|/usr/bin/)?(?:zsh|bash|sh)\s+-l?c\s+(.*)$', re.DOTALL)
MIN_MATCH_LENGTH = 3


@dataclass(frozen=True)
class LedgerEntry:
    agent_id: str
    role: str
    command: str
    normalised: str
    exit_code: Optional[int]


@dataclass(frozen=True)
class Ledger:
    entries: Tuple[LedgerEntry, ...] = ()

    def for_agent(self, agent_id: str) -> Tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self.entries if entry.agent_id == agent_id)


def _strip_quotes(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ('"', "'"):
        return stripped[1:-1]
    return stripped


def _unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\'", "'").replace('\\\\', '\\')


def normalise_command(command: str) -> str:
    text = command.strip()
    match = SHELL_WRAPPER.match(text)
    if match:
        text = _unescape(_strip_quotes(match.group(1)))
    return re.sub(r'\s+', ' ', text).strip()


def loose_form(command: str) -> str:
    """Quote- and backslash-insensitive form for comparing differently quoted commands."""
    return re.sub(r'\s+', ' ', re.sub(r'["\'\\]', '', normalise_command(command))).strip()


def build_ledger(results: Iterable[AgentResult]) -> Ledger:
    entries = tuple(LedgerEntry(agent_id=result.agent_id, role=result.role, command=record.command,
                                normalised=normalise_command(record.command), exit_code=record.exit_code)
                    for result in results for record in result.commands)
    return Ledger(entries=entries)


SEGMENT_SPLIT = re.compile(r'\s*(?:&&|\|\||;)\s*')
CD_PREFIX = re.compile(r'^cd\s+\S+\s*$')


def command_segments(command: str) -> Tuple[str, ...]:
    """The whole normalised command plus each top-level `&&`/`;`/`||` segment.

    A claim must equal the whole command or one of its segments; text that merely
    appears inside an argument (for example printed by ``printf``) never matches.
    """
    body = normalise_command(command)
    segments = tuple(seg for seg in SEGMENT_SPLIT.split(body) if seg and not CD_PREFIX.match(seg))
    return (body,) + segments


def _matching_entries(ledger: Ledger, agent_id: str, claimed: str) -> Tuple[LedgerEntry, ...]:
    wanted = normalise_command(claimed)
    if len(wanted) < MIN_MATCH_LENGTH:
        return ()
    wanted_loose = loose_form(claimed)
    matches = []
    for entry in ledger.for_agent(agent_id):
        segments = command_segments(entry.command)
        if wanted in segments or wanted_loose in {loose_form(seg) for seg in segments}:
            matches.append(entry)
    return tuple(matches)


def command_ran(ledger: Ledger, agent_id: str, claimed: str) -> bool:
    return bool(_matching_entries(ledger, agent_id, claimed))


def exit_codes_for(ledger: Ledger, agent_id: str, claimed: str) -> Tuple[int, ...]:
    """Exit codes the log recorded for the command a claim refers to (may be several runs)."""
    return tuple(sorted({entry.exit_code for entry in _matching_entries(ledger, agent_id, claimed)
                         if isinstance(entry.exit_code, int)}))


def unauthenticated_commands(ledger: Ledger, agent_id: str, claimed: Iterable[str]) -> Tuple[str, ...]:
    return tuple(command for command in claimed if not command_ran(ledger, agent_id, command))


def _squash(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()


LONG_FRAGMENT = 20


def quote_matches(lines: Tuple[str, ...], line: int, text: str) -> bool:
    """True when ``text`` is the whitespace-normalised content of ``line``.

    A blank or short actual line never matches; a long fragment (20+ characters)
    of the actual line is accepted, since verifiers sometimes quote a statement
    without its trailing comment.
    """
    if not isinstance(line, int) or isinstance(line, bool) or line < 1 or line > len(lines):
        return False
    actual = _squash(lines[line - 1])
    wanted = _squash(text)
    if not wanted or not actual:
        return False
    if actual == wanted:
        return True
    return len(wanted) >= LONG_FRAGMENT and wanted in actual


def write_ledger(ledger: Ledger, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        for entry in ledger.entries:
            handle.write(json.dumps({'agent_id': entry.agent_id, 'role': entry.role, 'command': entry.command,
                                     'exit_code': entry.exit_code}, ensure_ascii=False) + '\n')


def ledger_summary(ledger: Ledger) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for entry in ledger.entries:
        summary[entry.agent_id] = summary.get(entry.agent_id, 0) + 1
    return summary
