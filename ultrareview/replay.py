"""Replay runner for skill mode: agents are run by a coordinator, outputs come from disk.

In interactive Codex the driver cannot launch ``codex exec`` (no network inside the
sandbox), so the skill's coordinator spawns native sub-agents itself. Each ``step``
re-runs the deterministic pipeline: every agent whose output file already exists is
replayed from disk; the first batch with missing outputs stops the pipeline with
``AgentsNeeded`` after writing the briefs and schemas the coordinator must dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .errors import UltraReviewError
from .runner import AgentResult, AgentSpec, CommandRecord, agent_paths, validate_output

PENDING_FILE = 'pending.json'


class AgentsNeeded(UltraReviewError):
    """Raised when the coordinator must run the listed agents before the next step."""

    def __init__(self, specs: Sequence[AgentSpec]) -> None:
        super().__init__(f'{len(specs)} agent(s) need to be run')
        self.specs: Tuple[AgentSpec, ...] = tuple(specs)


@dataclass(frozen=True)
class PendingAgent:
    agent_id: str
    role: str
    marker: str
    brief_path: str
    schema_path: str
    output_path: str
    sandbox: str
    cwd: str


def pending_from_specs(specs: Sequence[AgentSpec]) -> Tuple[PendingAgent, ...]:
    items = []
    for spec in specs:
        paths = agent_paths(spec)
        items.append(PendingAgent(agent_id=spec.agent_id, role=spec.role, marker=spec.marker,
                                  brief_path=str(paths['brief']), schema_path=str(paths['schema']),
                                  output_path=str(paths['output']), sandbox=spec.sandbox, cwd=str(spec.cwd)))
    return tuple(items)


def write_pending(run_dir: Path, pending: Sequence[PendingAgent]) -> Path:
    path = run_dir / PENDING_FILE
    path.write_text(json.dumps([p.__dict__ for p in pending], indent=2), encoding='utf-8')
    return path


def pending_instructions(pending: Sequence[PendingAgent], run_dir: Path) -> str:
    lines = [f'AGENTS NEEDED: {len(pending)} — run each as a fresh sub-agent (fork_turns: "none"), then call step again.',
             f'Manifest: {run_dir / PENDING_FILE}', '']
    for item in pending:
        lines.append(f'- {item.agent_id} (role {item.role}, sandbox {item.sandbox}, cwd {item.cwd})')
        lines.append(f'    brief:  {item.brief_path}')
        lines.append(f'    schema: {item.schema_path}')
        lines.append(f'    write the agent\'s final JSON verbatim to: {item.output_path}')
    return '\n'.join(lines)


def _events_commands(events_path: Path) -> Tuple[CommandRecord, ...]:
    """Optional: a coordinator may drop a JSONL of {"command","exit_code"} next to the output."""
    try:
        lines = events_path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return ()
    records: List[CommandRecord] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get('command'):
            records.append(CommandRecord(str(item['command']), item.get('exit_code'), str(item.get('output', ''))[:400]))
    return tuple(records)


def _result(spec: AgentSpec, status: str, output: Any, error: Any, paths: Dict[str, Path]) -> AgentResult:
    return AgentResult(agent_id=spec.agent_id, role=spec.role, status=status, output=output, error=error,
                       thread_id=None, commands=_events_commands(paths['run_events']), usage={}, duration_s=0.0,
                       events_path=str(paths['run_events']), output_path=str(paths['output']),
                       brief_path=str(paths['brief']), attempts=1, marker=spec.marker)


def record_output(run_dir: Path, agent_id: str, text: str, events_text: str = '') -> Tuple[bool, str]:
    """Store a coordinator-collected final message; validate it against the saved schema first.

    Returns (ok, message). Invalid JSON is still stored so the next step records the agent
    as failed instead of waiting forever; the message says what was wrong.
    """
    agents_dir = run_dir / 'agents'
    schema_path = agents_dir / f'{agent_id}.schema.json'
    if not schema_path.is_file():
        return False, f'unknown agent id {agent_id!r}: no schema in {agents_dir}'
    output_path = agents_dir / f'{agent_id}.output.json'
    body = text.strip()
    if body.startswith('```'):
        body = body.strip('`')
        body = body[body.find('{'):] if '{' in body else body
    try:
        value = json.loads(body)
        problems = validate_output(json.loads(schema_path.read_text(encoding='utf-8')), value)
    except json.JSONDecodeError as exc:
        output_path.write_text(body, encoding='utf-8')
        return False, f'stored, but not valid JSON ({exc}); the agent will count as failed'
    output_path.write_text(json.dumps(value, indent=1, ensure_ascii=False), encoding='utf-8')
    if events_text.strip():
        (agents_dir / f'{agent_id}.commands.jsonl').write_text(events_text, encoding='utf-8')
    if problems:
        return False, 'stored, but it does not match the schema: ' + '; '.join(problems[:5])
    return True, f'recorded {output_path}'


def replay_agent(spec: AgentSpec, retries: int = 0) -> AgentResult:
    paths = agent_paths(spec)
    paths['run_events'] = paths['brief'].with_name(f'{spec.agent_id}.commands.jsonl')
    paths['brief'].parent.mkdir(parents=True, exist_ok=True)
    paths['brief'].write_text(spec.brief, encoding='utf-8')
    paths['schema'].write_text(json.dumps(spec.schema, indent=2), encoding='utf-8')
    if not paths['output'].exists():
        return _result(spec, 'pending', None, 'output not recorded yet', paths)
    try:
        value = json.loads(paths['output'].read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return _result(spec, 'failed', None, f'recorded output is not valid JSON: {exc}', paths)
    problems = validate_output(spec.schema, value)
    if problems:
        return _result(spec, 'failed', None, 'recorded output does not match schema: ' + '; '.join(problems[:5]), paths)
    return _result(spec, 'completed', value, None, paths)
