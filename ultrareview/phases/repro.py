"""Phase 5 — reproduce: run the real code in a disposable worktree copy."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import time
from typing import Dict, List, Optional, Sequence, Tuple

from ..briefs import reproducer_brief
from ..errors import GitError
from ..models import Finding, Reproduction, reproduction_from_dict, SEVERITY_RANK, VERDICT_RANK
from ..pool import Runtime
from ..runner import AgentResult, AgentSpec
from ..state import ReviewState
from ..worktree import WorktreeCopy, create_worktree_copy, remove_worktree_copy

REPRO_JOBS = 2
HINT_FILES = (
    ('go.mod', 'Go module: run `go test ./<package>/...` for the affected package only; no -race, no private caches.'),
    ('package.json', 'Node project: use the existing test script from package.json (`npm test -- <pattern>` or the runner it names).'),
    ('pyproject.toml', 'Python project: use pytest or unittest as configured; run a single test file.'),
    ('pytest.ini', 'Python project: `python3 -m pytest <file> -q`.'),
    ('Package.swift', 'Swift package: `swift test --filter <TestName>` if the toolchain is present; otherwise report blocked.'),
    ('Cargo.toml', 'Rust crate: `cargo test <name>`.'),
    ('pom.xml', 'Maven project: `mvn -q -Dtest=<Test> test`.'),
    ('build.gradle', 'Gradle project: `./gradlew test --tests <Test>` if the wrapper is present.'),
)


def select_for_repro(findings: Sequence[Finding], mode: str, cap: int) -> Tuple[Finding, ...]:
    if mode == 'off':
        return ()
    eligible = [f for f in findings if f.verdict in ('CONFIRMED', 'PLAUSIBLE') and f.reproduction is None]
    ordered = sorted(eligible, key=lambda f: (SEVERITY_RANK.get(f.severity, 9), VERDICT_RANK.get(f.verdict, 9), f.id))
    return tuple(ordered) if mode == 'all' else tuple(ordered[:cap])


def test_hints(repo: Path, map_output: Optional[Dict[str, object]]) -> str:
    hints: List[str] = []
    if map_output and map_output.get('test_stack'):
        hints.append(f'Test stack per the map: {map_output["test_stack"]}')
    hints.extend(text for marker, text in HINT_FILES if (repo / marker).exists())
    return '\n'.join(f'- {h}' for h in hints) if hints else '- No test runner detected from manifests; a short script calling the real code is acceptable.'


def finding_payload(finding: Finding) -> Dict[str, object]:
    verification = finding.verifications[0] if finding.verifications else None
    return {
        'id': finding.id, 'file': finding.candidate.file, 'line_start': finding.candidate.line_start,
        'line_end': finding.candidate.line_end, 'summary': finding.candidate.summary,
        'failure_scenario': finding.candidate.failure_scenario, 'root_cause': finding.candidate.root_cause,
        'verdict': finding.verdict, 'severity': finding.severity,
        'trigger': verification.trigger if verification else '',
        'verifier_reasoning': verification.reasoning if verification else '',
        'suggested_regression_test': verification.regression_test if verification else '',
        'quotes': [{'path': q.path, 'line': q.line, 'text': q.text} for q in (verification.quotes if verification else finding.candidate.quotes)],
    }


def _prepare_copies(rt: Runtime, selected: Sequence[Finding]) -> Tuple[Dict[str, WorktreeCopy], Tuple[str, ...]]:
    copies: Dict[str, WorktreeCopy] = {}
    notes: List[str] = []
    base = rt.config.run_dir / 'worktrees'
    for finding in selected:
        patch_path = rt.config.run_dir / 'state.patch'
        try:
            copy = create_worktree_copy(rt.config.repo, rt.ctx.scope, base / finding.id, reuse=rt.config.replay,
                                        patch_path=patch_path if patch_path.is_file() else None)
        except GitError as exc:
            notes.append(f'reproduction of {finding.id} skipped: {exc}')
            continue
        copies[finding.id] = copy
        notes.extend(f'{finding.id}: {note}' for note in copy.notes)
    return copies, tuple(notes)


def _specs(rt: Runtime, selected: Sequence[Finding], copies: Dict[str, WorktreeCopy], hints: str) -> Tuple[AgentSpec, ...]:
    specs = []
    for finding in selected:
        copy = copies.get(finding.id)
        if copy is None:
            continue
        agent_id = f'repro-{finding.id}'
        brief = reproducer_brief(rt.ctx, agent_id, finding_payload(finding), str(copy.path), hints, finding.id)
        specs.append(rt.spec(agent_id, 'reproducer', brief, sandbox=rt.config.repro_sandbox, cwd=copy.path, marker=finding.id))
    return tuple(specs)


def _attach(findings: Sequence[Finding], results: Sequence[AgentResult]) -> Tuple[Tuple[Finding, ...], Tuple[str, ...]]:
    by_marker = {r.marker: r for r in results}
    limitations = []
    updated = []
    for finding in findings:
        result = by_marker.get(finding.id)
        if result is None:
            updated.append(finding)
            continue
        if not result.completed or result.output is None:
            limitations.append(f'reproducer {result.agent_id} {result.status}: {result.error}; verifier verdict kept')
            blocked = Reproduction(result.agent_id, 'blocked', '', '', 0, '', '', '', f'{result.status}: {result.error}')
            updated.append(replace(finding, reproduction=blocked))
            continue
        updated.append(replace(finding, reproduction=reproduction_from_dict(result.output, result.agent_id)))
    return tuple(updated), tuple(limitations)


def run_repro(rt: Runtime, state: ReviewState) -> ReviewState:
    started = time.monotonic()
    selected = select_for_repro(state.findings, rt.config.repro, rt.config.max_repro)
    if not selected:
        rt.emit('phase reproduce: nothing selected')
        return state
    rt.emit(f'phase reproduce: {len(selected)} finding(s) in disposable worktree copies')
    copies, notes = _prepare_copies(rt, selected)
    hints = test_hints(rt.config.repo, state.map_output)
    results = rt.run(_specs(rt, selected, copies, hints), jobs=min(rt.config.jobs, REPRO_JOBS))
    cleanup_notes: Tuple[str, ...] = ()
    if not rt.config.keep_worktree:
        for copy in copies.values():
            cleanup_notes += remove_worktree_copy(copy)
    else:
        cleanup_notes += tuple(f'kept worktree copy {copy.path}' for copy in copies.values())
    findings, limitations = _attach(state.findings, results)
    reproduced = sum(1 for f in findings if f.reproduction and f.reproduction.result == 'reproduced')
    rt.emit(f'  {reproduced} reproduced')
    return (replace(state, findings=findings, worktree_notes=state.worktree_notes + notes + cleanup_notes)
            .with_results(results).with_limitations(*limitations).with_timing('reproduce', time.monotonic() - started))
