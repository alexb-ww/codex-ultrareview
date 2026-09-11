"""Render the review as Markdown (terminal and file) and as a JSON payload."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from .codex_config import effective_model
from .config import RunConfig
from .gate import GateResult
from .ledger import Ledger, ledger_summary
from .models import Finding, SEVERITY_RANK, VERDICT_RANK, finding_to_dict
from .scope import ResolvedScope
from .snapshot import Snapshot
from .state import ReviewState

SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ReportBundle:
    version: str
    run_id: str
    run_dir: str
    config: RunConfig
    scope: ResolvedScope
    snapshot: Snapshot
    state: ReviewState
    gate: GateResult
    ledger: Ledger
    wall_seconds: float
    codex_version: str


def _sorted(findings: Sequence[Finding]) -> List[Finding]:
    return sorted(findings, key=lambda f: (SEVERITY_RANK.get(f.severity, 9), VERDICT_RANK.get(f.verdict, 9), f.id))


def partition(findings: Sequence[Finding]) -> Dict[str, List[Finding]]:
    accepted = [f for f in findings if f.accepted]
    rejected = [f for f in findings if f.verdict == 'REFUTED' or (f.decision and f.decision.action == 'reject')]
    unverified = [f for f in findings if f.verdict == 'UNVERIFIED']
    evidence = [f for f in findings if f.evidence_issues and not f.accepted and f not in rejected and f not in unverified]
    return {'accepted': _sorted(accepted), 'rejected': _sorted(rejected), 'unverified': unverified, 'evidence': evidence}


def _badge(finding: Finding) -> str:
    return {'reproduced': 'REPRODUCED', 'source-verified': 'SOURCE-VERIFIED', 'plausible': 'PLAUSIBLE'}.get(
        finding.verification_label, finding.verification_label.upper())


def _finding_block(finding: Finding) -> str:
    cand = finding.candidate
    verification = finding.verifications[0] if finding.verifications else None
    lines = [f'### {finding.id} · {finding.severity} · {_badge(finding)} · `{cand.file}:{cand.line_start}` — {cand.summary}', '']
    lines.append(f'- **Failure scenario:** {cand.failure_scenario}')
    if cand.root_cause:
        lines.append(f'- **Root cause:** {cand.root_cause}')
    if verification:
        if verification.trigger:
            lines.append(f'- **Trigger:** {verification.trigger}')
        lines.append(f'- **Verifier:** {verification.reasoning}')
        if verification.counterevidence_checked:
            lines.append('- **Refutations tried:** ' + '; '.join(verification.counterevidence_checked))
        if finding.verdict == 'PLAUSIBLE' and verification.what_would_confirm:
            lines.append(f'- **What would confirm it:** {verification.what_would_confirm}')
        for quote in verification.quotes[:6]:
            lines.append(f'  - `{quote.path}:{quote.line}` `{quote.text.strip()}`')
    repro = finding.reproduction
    if repro:
        if repro.result == 'reproduced':
            lines.append(f'- **Reproduction:** `{repro.command}` (exit {repro.exit_code}) — {repro.explanation}')
            if repro.output_excerpt:
                lines.append('  ```\n  ' + repro.output_excerpt.strip().replace('\n', '\n  ')[:1200] + '\n  ```')
            if repro.test_file:
                lines.append(f'  - test kept in the worktree copy as `{repro.test_file}`')
        else:
            lines.append(f'- **Reproduction:** {repro.result}' + (f' — {repro.blocked_reason}' if repro.blocked_reason else '')
                         + (f' — {repro.explanation}' if repro.explanation else ''))
    if verification and verification.fix:
        lines.append(f'- **Fix (not applied):** {verification.fix}')
    if verification and verification.regression_test:
        lines.append(f'- **Regression test:** {verification.regression_test}')
    angles = sorted({m.angle for m in (cand,) + tuple(finding.members)})
    lines.append(f'- **Origin:** {finding.origin} · **found by:** {", ".join(angles)}')
    if finding.decision and finding.decision.action == 'downgrade':
        lines.append(f'- **Adjudicator downgraded:** {finding.decision.reason}')
    if finding.evidence_issues:
        lines.append('- **Evidence problems:** ' + '; '.join(finding.evidence_issues))
    return '\n'.join(lines) + '\n'


def _coverage_table(bundle: ReportBundle) -> str:
    rows = ['| agent | angle | status | files read | note |', '|---|---|---|---|---|']
    for result in bundle.state.results:
        if result.role not in ('finder', 'sweep'):
            continue
        files = len(result.output.get('files_read', [])) if result.completed and result.output else 0
        note = (result.output.get('coverage_note', '') if result.completed and result.output else result.error) or ''
        rows.append(f'| {result.agent_id} | {result.marker} | {result.status} | {files} | {note[:120]} |')
    return '\n'.join(rows)


def _passport(bundle: ReportBundle) -> str:
    scope = bundle.scope
    state = bundle.state
    usage = state.usage_totals()
    completed = sum(1 for r in state.results if r.completed)
    lines = [
        f'- scope: {scope.kind}; files {scope.changed_files}; changed lines {scope.changed_lines}',
        f'- HEAD {scope.head or "-"}; base {scope.base_ref or "-"} ({scope.base_commit or "-"}); merge-base {scope.merge_base or "-"}'
        + (f'; commit {scope.commit}' if scope.commit else ''),
        f'- snapshot {bundle.snapshot.snapshot_id[:16]}; profile {bundle.config.profile}; votes {bundle.config.votes}; repro {bundle.config.repro}',
        '- codex {}; model {}; effort {}{}'.format(bundle.codex_version, *effective_model(bundle.config.effort.model, bundle.config.effort.default),
                                                 f'; per role {dict(bundle.config.effort.per_role)}' if bundle.config.effort.per_role else ''),
        f'- agents: {len(state.results)} run, {completed} completed, {len(state.failed_results())} failed; '
        f'commands logged: {sum(ledger_summary(bundle.ledger).values())}',
        f'- tokens: input {usage.get("input_tokens", 0)} (cached {usage.get("cached_input_tokens", 0)}), '
        f'output {usage.get("output_tokens", 0)}, reasoning {usage.get("reasoning_output_tokens", 0)}',
        f'- wall time {bundle.wall_seconds:.0f}s; phases: ' + ', '.join(f'{p} {s:.0f}s' for p, s in state.phase_timings),
        f'- run directory: {bundle.run_dir}',
    ]
    return '\n'.join(lines)


def render_markdown(bundle: ReportBundle) -> str:
    parts = partition(bundle.gate.findings)
    out = [f'# Ultra Review — {bundle.gate.status.upper()}', '']
    if bundle.state.adjudicator_summary:
        out += [bundle.state.adjudicator_summary.strip(), '']
    out.append(f'## Findings ({len(parts["accepted"])})')
    out.append('')
    if parts['accepted']:
        out.extend(_finding_block(f) for f in parts['accepted'])
    else:
        out.append('No confirmed or plausible defects in the reviewed scope. This is a statement about what was checked, not a proof of absence.\n')
    if parts['unverified'] or parts['evidence']:
        out.append('## Unresolved')
        out.append('')
        for f in parts['unverified']:
            out.append(f'- {f.id} `{f.candidate.file}:{f.candidate.line_start}` — {f.candidate.summary} (verifier did not complete)')
        for f in parts['evidence']:
            out.append(f'- {f.id} `{f.candidate.file}:{f.candidate.line_start}` — {f.candidate.summary} (evidence did not authenticate: '
                       + '; '.join(f.evidence_issues) + ')')
        out.append('')
    if parts['rejected']:
        out.append('## Rejected')
        out.append('')
        for f in parts['rejected']:
            reason = f.decision.reason if f.decision and f.decision.action == 'reject' else \
                (f.verifications[0].reasoning if f.verifications else '')
            out.append(f'- {f.id} `{f.candidate.file}:{f.candidate.line_start}` — {f.candidate.summary}: {reason[:300]}')
        out.append('')
    out += ['## Coverage', '', _coverage_table(bundle), '']
    if bundle.state.coverage_gaps:
        out += ['Coverage gaps named by the adjudicator:'] + [f'- {g}' for g in bundle.state.coverage_gaps] + ['']
    if bundle.scope.skipped:
        out += ['Skipped paths:'] + [f'- {p}: {r}' for p, r in bundle.scope.skipped[:30]] + ['']
    limitations = tuple(bundle.state.limitations) + tuple(bundle.gate.errors) + tuple(bundle.state.worktree_notes)
    if limitations or bundle.gate.warnings:
        out += ['## Limitations', ''] + [f'- {item}' for item in limitations] + [f'- {w}' for w in bundle.gate.warnings] + ['']
    out += ['## Run passport', '', _passport(bundle), '']
    return '\n'.join(out)


def report_json(bundle: ReportBundle) -> Dict[str, Any]:
    parts = partition(bundle.gate.findings)
    return {
        'schema_version': SCHEMA_VERSION,
        'tool': f'ultrareview {bundle.version}',
        'status': bundle.gate.status,
        'run_id': bundle.run_id,
        'run_dir': bundle.run_dir,
        'scope': {'kind': bundle.scope.kind, 'head': bundle.scope.head, 'base_ref': bundle.scope.base_ref,
                  'base_commit': bundle.scope.base_commit, 'merge_base': bundle.scope.merge_base,
                  'commit': bundle.scope.commit, 'changed_files': bundle.scope.changed_files,
                  'changed_lines': bundle.scope.changed_lines, 'targets': list(bundle.scope.target_paths),
                  'skipped': [{'path': p, 'reason': r} for p, r in bundle.scope.skipped]},
        'snapshot_id': bundle.snapshot.snapshot_id,
        'summary': bundle.state.adjudicator_summary,
        'findings': [finding_to_dict(f) for f in parts['accepted']],
        'unresolved': [finding_to_dict(f) for f in parts['unverified'] + parts['evidence']],
        'rejected': [finding_to_dict(f) for f in parts['rejected']],
        'coverage_gaps': list(bundle.state.coverage_gaps),
        'limitations': list(bundle.state.limitations) + list(bundle.state.worktree_notes),
        'gate': {'errors': list(bundle.gate.errors), 'warnings': list(bundle.gate.warnings),
                 'drift': None if bundle.gate.drift is None else bundle.gate.drift.__dict__},
        'agents': [{'id': r.agent_id, 'role': r.role, 'status': r.status, 'marker': r.marker,
                    'duration_s': r.duration_s, 'commands': len(r.commands), 'thread_id': r.thread_id}
                   for r in bundle.state.results],
        'usage': bundle.state.usage_totals(),
        'wall_seconds': round(bundle.wall_seconds, 1),
        'codex_version': bundle.codex_version,
        'config': {'profile': bundle.config.profile, 'votes': bundle.config.votes, 'repro': bundle.config.repro,
                   'jobs': bundle.config.jobs, 'model': bundle.config.effort.model,
                   'effort': bundle.config.effort.default, 'per_role_effort': list(bundle.config.effort.per_role)},
    }


def exit_code_for(gate: GateResult) -> int:
    return 0 if gate.status == 'complete' and not gate.errors else 3
