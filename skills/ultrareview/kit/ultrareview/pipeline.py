"""The driver: preflight, phases, gate and report, in that order."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import subprocess
import time
from typing import Callable, Optional

from . import __version__
from .angles import select_angles
from .briefs import BriefContext, brief_context_dict
from .codex_config import effective_model
from .config import RunConfig, ensure_run_dir_outside_repo
from .errors import LimitExceeded, RunnerError, ScopeError
from .gate import GateResult, run_gate
from .gitio import repo_root
from .ledger import build_ledger, write_ledger
from .phases.adjudicate import run_adjudicate
from .phases.find import finder_specs, run_find
from .phases.mapping import needs_map, run_map
from .phases.repro import run_repro
from .phases.sweep import run_sweep
from .phases.triage import run_triage
from .phases.verify import run_verify
from .pool import AgentRunner, Emitter, Runtime, stderr_emitter
from .report import ReportBundle, exit_code_for, render_markdown, report_json
from .runner import run_agent
from .scope import ResolvedScope, check_limits, resolve_scope
from .replay import AgentsNeeded, pending_from_specs, pending_instructions, replay_agent, write_pending
from .snapshot import Snapshot, SnapshotReader, detect_drift, snapshot_from_dict, snapshot_to_dict, take_snapshot
from .state import ReviewState, save_state
from .worktree import capture_state_patch

CONFIG_FILE = 'config.json'
EXIT_AGENTS_NEEDED = 4


@dataclass(frozen=True)
class Prepared:
    config: RunConfig
    scope: ResolvedScope
    snapshot: Snapshot
    ctx: BriefContext
    run_id: str
    codex_version: str


@dataclass(frozen=True)
class RunOutcome:
    exit_code: int
    status: str
    markdown: str
    report_path: Optional[Path] = None
    json_path: Optional[Path] = None


def codex_version(codex_bin: str) -> str:
    try:
        result = subprocess.run([codex_bin, '--version'], capture_output=True, text=True, timeout=30,
                                stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise RunnerError(f'codex binary {codex_bin!r} not found on PATH') from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f'could not run {codex_bin} --version: {exc}') from exc
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0] if text else 'unknown'


def _load_or_take_snapshot(run_dir: Path, repo: Path, scope: ResolvedScope, reuse: bool) -> Snapshot:
    path = run_dir / 'snapshot.json'
    if reuse and path.exists():
        return snapshot_from_dict(json.loads(path.read_text(encoding='utf-8')))
    snapshot = take_snapshot(repo, scope)
    path.write_text(json.dumps(snapshot_to_dict(snapshot), indent=1), encoding='utf-8')
    return snapshot


def prepare(config: RunConfig, emit: Emitter = stderr_emitter, reuse_snapshot: bool = False,
            require_codex: bool = True) -> Prepared:
    config = config.validate()
    repo = repo_root(config.repo)
    run_dir = ensure_run_dir_outside_repo(config.run_dir, repo)
    config = replace(config, repo=repo, run_dir=run_dir)
    try:
        version = codex_version(config.codex_bin)
    except RunnerError:
        if require_codex:
            raise
        version = 'unknown'
    scope = resolve_scope(repo, config.scope)
    violation = check_limits(scope, config.limits)
    if violation is not None:
        raise LimitExceeded(violation.describe())
    run_dir.mkdir(parents=True, exist_ok=True)
    snapshot = _load_or_take_snapshot(run_dir, repo, scope, reuse_snapshot)
    state_patch = run_dir / 'state.patch'
    if not (reuse_snapshot and state_patch.exists()):
        capture_state_patch(repo, scope, state_patch)
    diff_path = run_dir / 'diff.patch'
    diff_path.write_text(scope.diff_text + (('\n\n# index versions\n' + scope.index_diff_text) if scope.index_diff_text else ''),
                         encoding='utf-8')
    run_id = run_dir.name
    ctx = BriefContext(run_id=run_id, repo_root=str(repo), scope=scope, snapshot=snapshot, diff_path=str(diff_path),
                       lang=config.lang, preamble=config.preamble, inline_diff_limit=config.inline_diff_limit,
                       max_candidates=config.max_candidates_per_angle, max_findings=config.max_findings,
                       note=config.note)
    return Prepared(config=config, scope=scope, snapshot=snapshot, ctx=ctx, run_id=run_id, codex_version=version)


def plan_text(prepared: Prepared) -> str:
    scope = prepared.scope
    config = prepared.config
    angles = select_angles(config.profile, scope.kind)
    finders = len(finder_specs(Runtime(config, prepared.ctx, emit=lambda _: None), ReviewState()))
    lines = [
        f'ultrareview {__version__} · codex {prepared.codex_version}',
        f'repo: {config.repo}', f'run dir: {config.run_dir}',
        f'scope: {scope.kind}; base {scope.base_ref or "-"} via {scope.base_source or "-"}; '
        f'{scope.changed_files} file(s), {scope.changed_lines} changed line(s)',
        f'profile {config.profile}: {finders} finder(s) over {len(angles)} angle(s) '
        f'({", ".join(a.id for a in angles)}); mapper: {"yes" if needs_map(Runtime(config, prepared.ctx, emit=lambda _: None)) else "no"}',
        f'verify: {config.votes} vote(s) per cluster; repro: {config.repro} (max {config.max_repro}); '
        f'sweep: yes; adjudicate: yes; jobs {config.jobs}; agent timeout {config.agent_timeout}s',
        'model: {}; effort: {}{}'.format(*effective_model(config.effort.model, config.effort.default),
                                         f'; per role {dict(config.effort.per_role)}' if config.effort.per_role else ''),
    ]
    lines.extend(f'note: {note}' for note in scope.notes)
    if scope.skipped:
        lines.append(f'skipped: {len(scope.skipped)} path(s) (see report)')
    return '\n'.join(lines)


def _empty_outcome(prepared: Prepared, emit: Emitter) -> RunOutcome:
    text = (f'# Ultra Review — nothing to review\n\nThe {prepared.scope.kind} scope is empty '
            f'({"; ".join(prepared.scope.notes) or "no changed files"}). Stage or commit local edits, or pass a different base.\n')
    emit(text)
    return RunOutcome(exit_code=0, status='empty', markdown=text)


def _write_outputs(prepared: Prepared, bundle: ReportBundle, state: ReviewState, gate: GateResult) -> RunOutcome:
    run_dir = prepared.config.run_dir
    markdown = render_markdown(bundle)
    report_path = run_dir / 'report.md'
    json_path = run_dir / 'report.json'
    report_path.write_text(markdown, encoding='utf-8')
    json_path.write_text(json.dumps(report_json(bundle), indent=2, ensure_ascii=False), encoding='utf-8')
    save_state(replace(state, findings=gate.findings), run_dir / 'state.json')
    (run_dir / 'run.json').write_text(json.dumps({
        'run_id': prepared.run_id, 'version': __version__, 'codex_version': prepared.codex_version,
        'status': gate.status, 'wall_seconds': bundle.wall_seconds, 'context': brief_context_dict(prepared.ctx),
        'config': {'profile': prepared.config.profile, 'scope': prepared.config.scope.__dict__,
                   'jobs': prepared.config.jobs, 'votes': prepared.config.votes, 'repro': prepared.config.repro,
                   'agent_timeout': prepared.config.agent_timeout, 'model': prepared.config.effort.model,
                   'effort': prepared.config.effort.default, 'per_role': list(prepared.config.effort.per_role)},
    }, indent=2), encoding='utf-8')
    return RunOutcome(exit_code=exit_code_for(gate), status=gate.status, markdown=markdown,
                      report_path=report_path, json_path=json_path)


def run_phases(rt: Runtime, state: ReviewState) -> ReviewState:
    state = run_map(rt, state)
    state = run_find(rt, state)
    state = run_triage(rt, state)
    state = run_verify(rt, state)
    state = run_repro(rt, state)
    state = run_sweep(rt, state)
    state = run_repro(rt, state)
    return run_adjudicate(rt, state)


def run_review(config: RunConfig, emit: Emitter = stderr_emitter, run_agent_fn: AgentRunner = run_agent) -> RunOutcome:
    started = time.monotonic()
    prepared = prepare(config, emit)
    emit(plan_text(prepared))
    if prepared.scope.is_empty:
        return _empty_outcome(prepared, emit)
    if prepared.config.dry_run:
        emit('dry run: no agents launched')
        return RunOutcome(exit_code=0, status='dry-run', markdown=plan_text(prepared))
    rt = Runtime(prepared.config, prepared.ctx, emit=emit, run_agent_fn=run_agent_fn)
    state = run_phases(rt, ReviewState())
    drift = detect_drift(prepared.snapshot, prepared.config.repo)
    ledger = build_ledger(state.results)
    write_ledger(ledger, prepared.config.run_dir / 'ledger.jsonl')
    reader = SnapshotReader(prepared.config.repo, prepared.snapshot)
    gate = run_gate(state, ledger, reader, drift)
    bundle = ReportBundle(version=__version__, run_id=prepared.run_id, run_dir=str(prepared.config.run_dir),
                          config=prepared.config, scope=prepared.scope, snapshot=prepared.snapshot, state=state,
                          gate=gate, ledger=ledger, wall_seconds=time.monotonic() - started,
                          codex_version=prepared.codex_version)
    return _write_outputs(prepared, bundle, state, gate)


def _config_payload(config: RunConfig) -> dict:
    return {'scope': config.scope.__dict__, 'profile': config.profile, 'votes': config.votes, 'repro': config.repro,
            'max_repro': config.max_repro, 'max_findings': config.max_findings, 'lang': config.lang,
            'preamble': config.preamble, 'limits': config.limits.__dict__, 'jobs': config.jobs,
            'agent_timeout': config.agent_timeout, 'max_candidates_per_angle': config.max_candidates_per_angle,
            'note': config.note}


def _restore_config(config: RunConfig, payload: dict) -> RunConfig:
    from .scope import Limits, ScopeSpec
    scope = payload.get('scope') or {}
    return replace(config, scope=ScopeSpec(kind=scope.get('kind', 'branch'), base=scope.get('base'),
                                           commit=scope.get('commit'), paths=tuple(scope.get('paths', ()))),
                   profile=payload.get('profile', config.profile), votes=payload.get('votes', config.votes),
                   repro=payload.get('repro', config.repro), max_repro=payload.get('max_repro', config.max_repro),
                   max_findings=payload.get('max_findings', config.max_findings), lang=payload.get('lang', config.lang),
                   preamble=payload.get('preamble', config.preamble),
                   limits=Limits(**payload.get('limits', config.limits.__dict__)),
                   max_candidates_per_angle=payload.get('max_candidates_per_angle', config.max_candidates_per_angle),
                   note=str(payload.get('note', config.note) or ''))


def _remove_step_worktrees(repo: Path, run_dir: Path) -> tuple:
    from .gitio import run_git
    notes = ()
    base = run_dir / 'worktrees'
    if not base.exists():
        return notes
    for copy in sorted(base.iterdir()):
        try:
            run_git(repo, 'worktree', 'remove', '--force', str(copy))
        except Exception as exc:  # noqa: BLE001 - cleanup must not hide the report
            notes += (f'could not remove worktree copy {copy}: {exc}',)
    run_git(repo, 'worktree', 'prune', allow_fail=True)
    return notes


def guard_marker_path() -> Path:
    """Where the optional PreToolUse guard looks for an active review (see hooks/)."""
    import os
    import tempfile
    override = os.environ.get('ULTRAREVIEW_ACTIVE')
    return Path(override) if override else Path(tempfile.gettempdir()) / 'ultrareview' / 'REVIEW_ACTIVE'


def set_guard_marker(run_dir: Path, active: bool) -> None:
    marker = guard_marker_path()
    try:
        if active:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(str(run_dir) + '\n', encoding='utf-8')
        elif marker.exists():
            marker.unlink()
    except OSError:
        return


def run_step(config: RunConfig, emit: Emitter = stderr_emitter) -> RunOutcome:
    """One coordinator-driven step: replay recorded outputs, stop at the first missing batch."""
    started = time.monotonic()
    run_dir = config.run_dir.expanduser().resolve()
    config_path = run_dir / CONFIG_FILE
    if config_path.exists():
        config = _restore_config(config, json.loads(config_path.read_text(encoding='utf-8')))
    config = replace(config, replay=True, run_dir=run_dir)
    prepared = prepare(config, emit, reuse_snapshot=True, require_codex=False)
    if not config_path.exists():
        config_path.write_text(json.dumps(_config_payload(prepared.config), indent=2, ensure_ascii=False), encoding='utf-8')
        emit(plan_text(prepared))
    if prepared.scope.is_empty:
        return _empty_outcome(prepared, emit)
    rt = Runtime(prepared.config, prepared.ctx, emit=emit, run_agent_fn=replay_agent)
    try:
        state = run_phases(rt, ReviewState())
    except AgentsNeeded as need:
        pending = pending_from_specs(need.specs)
        write_pending(run_dir, pending)
        set_guard_marker(run_dir, active=True)
        text = pending_instructions(pending, run_dir)
        return RunOutcome(exit_code=EXIT_AGENTS_NEEDED, status='agents-needed', markdown=text)
    set_guard_marker(run_dir, active=False)
    notes = () if prepared.config.keep_worktree else _remove_step_worktrees(prepared.config.repo, run_dir)
    state = replace(state, worktree_notes=state.worktree_notes + notes)
    drift = detect_drift(prepared.snapshot, prepared.config.repo)
    ledger = build_ledger(state.results)
    write_ledger(ledger, run_dir / 'ledger.jsonl')
    reader = SnapshotReader(prepared.config.repo, prepared.snapshot)
    gate = run_gate(state, ledger, reader, drift, authenticate_commands=False)
    bundle = ReportBundle(version=__version__, run_id=prepared.run_id, run_dir=str(run_dir), config=prepared.config,
                          scope=prepared.scope, snapshot=prepared.snapshot, state=state, gate=gate, ledger=ledger,
                          wall_seconds=time.monotonic() - started, codex_version=prepared.codex_version)
    return _write_outputs(prepared, bundle, state, gate)
