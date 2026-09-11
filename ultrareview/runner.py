"""Run one agent role as its own ``codex exec`` process and collect its evidence.

Each agent gets a brief on stdin, a strict output schema, its own event log
(``--json`` JSONL on stdout) and an output file (``-o``). The JSONL stream is
parsed into command records that later authenticate the agent's claims.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .errors import RunnerError

SANDBOXES = ('read-only', 'workspace-write', 'danger-full-access')
OUTPUT_EXCERPT_LIMIT = 4000
KILL_GRACE_SECONDS = 5


@dataclass(frozen=True)
class AgentSpec:
    agent_id: str
    role: str
    brief: str
    schema: Dict[str, Any]
    cwd: Path
    run_dir: Path
    sandbox: str = 'read-only'
    effort: Optional[str] = None
    model: Optional[str] = None
    timeout: int = 1200
    keep_session: bool = False
    codex_bin: str = 'codex'
    extra_config: Tuple[str, ...] = ()
    marker: str = ''


@dataclass(frozen=True)
class CommandRecord:
    command: str
    exit_code: Optional[int]
    output_excerpt: str = ''


@dataclass(frozen=True)
class ParsedEvents:
    thread_id: Optional[str] = None
    commands: Tuple[CommandRecord, ...] = ()
    usage: Dict[str, int] = field(default_factory=dict)
    messages: Tuple[str, ...] = ()
    unknown_events: int = 0


@dataclass(frozen=True)
class AgentResult:
    agent_id: str
    role: str
    status: str
    output: Optional[Dict[str, Any]]
    error: Optional[str]
    thread_id: Optional[str]
    commands: Tuple[CommandRecord, ...]
    usage: Dict[str, int]
    duration_s: float
    events_path: str
    output_path: str
    brief_path: str
    attempts: int
    marker: str = ''

    @property
    def completed(self) -> bool:
        return self.status == 'completed' and self.output is not None


def agent_paths(spec: AgentSpec) -> Dict[str, Path]:
    base = spec.run_dir / 'agents'
    return {'brief': base / f'{spec.agent_id}.brief.md', 'schema': base / f'{spec.agent_id}.schema.json',
            'events': base / f'{spec.agent_id}.events.jsonl', 'output': base / f'{spec.agent_id}.output.json',
            'stderr': base / f'{spec.agent_id}.stderr.log'}


def build_argv(spec: AgentSpec, schema_path: Path, output_path: Path) -> Tuple[str, ...]:
    if spec.sandbox not in SANDBOXES:
        raise RunnerError(f'unknown sandbox {spec.sandbox!r}')
    argv: List[str] = [spec.codex_bin, 'exec', '--skip-git-repo-check', '--json', '-s', spec.sandbox,
                       '-C', str(spec.cwd), '--output-schema', str(schema_path), '-o', str(output_path)]
    if not spec.keep_session:
        argv.append('--ephemeral')
    if spec.model:
        argv.extend(['-m', spec.model])
    if spec.effort:
        argv.extend(['-c', f'model_reasoning_effort={json.dumps(spec.effort)}'])
    for item in spec.extra_config:
        argv.extend(['-c', item])
    argv.append('-')
    return tuple(argv)


def _excerpt(text: Any) -> str:
    value = text if isinstance(text, str) else ''
    return value if len(value) <= OUTPUT_EXCERPT_LIMIT else value[:OUTPUT_EXCERPT_LIMIT] + '…'


def parse_events(lines: Iterable[str]) -> ParsedEvents:
    thread_id = None
    commands: Dict[str, CommandRecord] = {}
    order: List[str] = []
    usage: Dict[str, int] = {}
    messages: List[str] = []
    unknown = 0
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            unknown += 1
            continue
        kind = event.get('type')
        item = event.get('item') if isinstance(event.get('item'), dict) else {}
        if kind == 'thread.started':
            thread_id = event.get('thread_id') or thread_id
        elif kind == 'turn.completed' and isinstance(event.get('usage'), dict):
            usage = {k: usage.get(k, 0) + int(v) for k, v in event['usage'].items() if isinstance(v, int)}
        elif kind in ('item.started', 'item.completed', 'item.updated') and item.get('type') == 'command_execution':
            key = str(item.get('id') or len(order))
            if key not in commands:
                order.append(key)
            commands[key] = CommandRecord(command=str(item.get('command', '')), exit_code=item.get('exit_code'),
                                          output_excerpt=_excerpt(item.get('aggregated_output')))
        elif kind == 'item.completed' and item.get('type') == 'agent_message':
            messages.append(str(item.get('text', '')))
        elif kind not in ('turn.started', 'item.started', 'item.completed', 'item.updated', 'turn.completed', 'error'):
            unknown += 1
    return ParsedEvents(thread_id=thread_id, commands=tuple(commands[k] for k in order), usage=usage,
                        messages=tuple(messages), unknown_events=unknown)


def validate_output(schema: Dict[str, Any], value: Any, path: str = '$') -> Tuple[str, ...]:
    """A small structural validator: types, required keys, enums, arrays."""
    kind = schema.get('type')
    if kind == 'object':
        if not isinstance(value, dict):
            return (f'{path}: expected object',)
        errors: Tuple[str, ...] = tuple(f'{path}.{key}: missing' for key in schema.get('required', []) if key not in value)
        for key, child in schema.get('properties', {}).items():
            if key in value:
                errors += validate_output(child, value[key], f'{path}.{key}')
        return errors
    if kind == 'array':
        if not isinstance(value, list):
            return (f'{path}: expected array',)
        return tuple(err for i, item in enumerate(value) for err in validate_output(schema['items'], item, f'{path}[{i}]'))
    if kind == 'string':
        if not isinstance(value, str):
            return (f'{path}: expected string',)
        if 'enum' in schema and value not in schema['enum']:
            return (f'{path}: {value!r} not in {schema["enum"]}',)
        return ()
    if kind == 'integer':
        return () if isinstance(value, int) and not isinstance(value, bool) else (f'{path}: expected integer',)
    if kind == 'boolean':
        return () if isinstance(value, bool) else (f'{path}: expected boolean',)
    return ()


def _terminate(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        process.wait()


def _launch(argv: Tuple[str, ...], brief_path: Path, events_path: Path, stderr_path: Path,
            cwd: Path, timeout: int) -> Tuple[str, Optional[str], float]:
    started = time.monotonic()
    try:
        with brief_path.open('rb') as stdin, events_path.open('ab') as stdout, stderr_path.open('ab') as stderr:
            process = subprocess.Popen(list(argv), stdin=stdin, stdout=stdout, stderr=stderr,
                                       cwd=str(cwd), start_new_session=True)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                _terminate(process)
                return 'timeout', f'agent exceeded {timeout}s and was terminated', time.monotonic() - started
    except FileNotFoundError:
        raise RunnerError(f'codex binary {argv[0]!r} not found on PATH')
    except OSError as exc:
        raise RunnerError(f'could not start codex: {exc}')
    if code != 0:
        return 'failed', f'codex exited with status {code}', time.monotonic() - started
    return 'completed', None, time.monotonic() - started


def _read_output(output_path: Path, schema: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        text = output_path.read_text(encoding='utf-8')
    except OSError:
        return None, 'agent produced no output file'
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f'agent output is not valid JSON: {exc}'
    problems = validate_output(schema, value)
    if problems:
        return None, 'agent output does not match schema: ' + '; '.join(problems[:5])
    return value, None


def run_agent(spec: AgentSpec, retries: int = 1) -> AgentResult:
    paths = agent_paths(spec)
    paths['brief'].parent.mkdir(parents=True, exist_ok=True)
    paths['brief'].write_text(spec.brief, encoding='utf-8')
    paths['schema'].write_text(json.dumps(spec.schema, indent=2), encoding='utf-8')
    argv = build_argv(spec, paths['schema'], paths['output'])
    attempts = 0
    status, error, duration, output = 'failed', 'not started', 0.0, None
    while attempts <= retries:
        attempts += 1
        for stale in ('output',):
            if paths[stale].exists():
                paths[stale].unlink()
        status, error, elapsed = _launch(argv, paths['brief'], paths['events'], paths['stderr'], spec.cwd, spec.timeout)
        duration += elapsed
        if status == 'completed':
            output, error = _read_output(paths['output'], spec.schema)
            if output is not None:
                break
            status = 'failed'
        if status == 'timeout':
            break
    events = parse_events(_read_lines(paths['events']))
    result = AgentResult(agent_id=spec.agent_id, role=spec.role, status=status, output=output, error=error,
                         thread_id=events.thread_id, commands=events.commands, usage=events.usage,
                         duration_s=round(duration, 2), events_path=str(paths['events']),
                         output_path=str(paths['output']), brief_path=str(paths['brief']),
                         attempts=attempts, marker=spec.marker)
    return replace(result, error=_with_stderr_hint(result.error, paths['stderr']))


def _with_stderr_hint(error: Optional[str], stderr_path: Path) -> Optional[str]:
    if error is None:
        return None
    try:
        tail = stderr_path.read_text(encoding='utf-8', errors='replace').strip().splitlines()[-3:]
    except OSError:
        tail = []
    hint = ' | '.join(line.strip() for line in tail if line.strip())
    return f'{error} ({hint})' if hint else error


def _read_lines(path: Path) -> Tuple[str, ...]:
    try:
        return tuple(path.read_text(encoding='utf-8', errors='replace').splitlines())
    except OSError:
        return ()


def result_to_dict(result: AgentResult) -> Dict[str, Any]:
    return {'agent_id': result.agent_id, 'role': result.role, 'status': result.status, 'error': result.error,
            'thread_id': result.thread_id, 'usage': dict(result.usage), 'duration_s': result.duration_s,
            'attempts': result.attempts, 'marker': result.marker, 'events_path': result.events_path,
            'output_path': result.output_path, 'brief_path': result.brief_path,
            'commands': [{'command': c.command, 'exit_code': c.exit_code} for c in result.commands]}
