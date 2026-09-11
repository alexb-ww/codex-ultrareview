"""Phase 7 — adjudicate: an independent audit of the finished review."""
from __future__ import annotations

from dataclasses import replace
import time
from typing import Dict, List, Sequence, Tuple

from ..briefs import adjudicator_brief
from ..models import Candidate, Decision, Finding, SEVERITY_RANK, VERDICT_RANK
from ..pool import Runtime
from ..runner import AgentResult
from ..state import ReviewState
from .find import angle_coverage
from .triage import deterministic_clusters
from .verify import verify_clusters


def findings_payload(findings: Sequence[Finding]) -> List[Dict[str, object]]:
    rows = []
    for finding in findings:
        if finding.verdict not in ('CONFIRMED', 'PLAUSIBLE'):
            continue
        verification = finding.verifications[0] if finding.verifications else None
        repro = finding.reproduction
        rows.append({
            'id': finding.id, 'file': finding.candidate.file, 'line_start': finding.candidate.line_start,
            'line_end': finding.candidate.line_end, 'summary': finding.candidate.summary,
            'failure_scenario': finding.candidate.failure_scenario, 'verdict': finding.verdict,
            'severity': finding.severity, 'origin': finding.origin, 'verification': finding.verification_label,
            'angles': sorted({m.angle for m in (finding.candidate,) + tuple(finding.members)}),
            'verifier_reasoning': verification.reasoning if verification else '',
            'counterevidence_checked': list(verification.counterevidence_checked) if verification else [],
            'quotes': [{'path': q.path, 'line': q.line, 'text': q.text} for q in (verification.quotes if verification else ())],
            'reproduction': ({'result': repro.result, 'command': repro.command, 'exit_code': repro.exit_code,
                              'output_excerpt': repro.output_excerpt[:600]} if repro else None),
            'evidence_issues': list(finding.evidence_issues),
        })
    return rows


def coverage_payload(rt: Runtime, state: ReviewState) -> Dict[str, object]:
    finders = [r for r in state.results if r.role in ('finder', 'sweep')]
    return {'scope': rt.ctx.scope.kind, 'target_files': rt.ctx.scope.changed_files,
            'targets': list(rt.ctx.scope.target_paths[:200]),
            'skipped': [{'path': p, 'reason': r} for p, r in rt.ctx.scope.skipped[:50]],
            'angles': angle_coverage(finders)}


def apply_decisions(findings: Sequence[Finding], output: Dict[str, object], adjudicator_id: str) -> Tuple[Tuple[Finding, ...], Tuple[str, ...]]:
    decisions: Dict[str, Decision] = {}
    unknown: List[str] = []
    ids = {f.id for f in findings}
    for item in output.get('decisions', []) if isinstance(output.get('decisions'), list) else []:
        if not isinstance(item, dict):
            continue
        fid = str(item.get('finding_id', ''))
        if fid not in ids:
            unknown.append(fid)
            continue
        decisions[fid] = Decision(adjudicator_id=adjudicator_id, action=str(item.get('action', 'accept')),
                                  severity=str(item.get('severity', 'P3')), reason=str(item.get('reason', '')),
                                  merged_into=str(item.get('merged_into', '')))
    updated = tuple(replace(f, decision=decisions.get(f.id, f.decision)) for f in findings)
    notes = (f'adjudicator referenced unknown finding ids: {", ".join(unknown)}',) if unknown else ()
    return updated, notes


def apply_cap(findings: Sequence[Finding], cap: int, adjudicator_id: str) -> Tuple[Finding, ...]:
    accepted = [f for f in findings if f.accepted]
    ordered = sorted(accepted, key=lambda f: (SEVERITY_RANK.get(f.severity, 9), VERDICT_RANK.get(f.verdict, 9), f.id))
    over = {f.id for f in ordered[cap:]}
    if not over:
        return tuple(findings)
    return tuple(replace(f, decision=Decision(adjudicator_id, 'downgrade', 'P3', 'cap: beyond the maximum number of reported findings'))
                 if f.id in over else f for f in findings)


def suspicion_candidates(output: Dict[str, object], start: int) -> Tuple[Candidate, ...]:
    raw = output.get('new_suspicions') if isinstance(output.get('new_suspicions'), list) else []
    return tuple(Candidate(id=f'C{start + i}', angle='adjudicator', finder_id='adjudicator', file=str(s.get('file', '')),
                           line_start=int(s.get('line_start', 0)), line_end=int(s.get('line_end', 0)),
                           summary=str(s.get('summary', '')), failure_scenario=str(s.get('failure_scenario', '')),
                           root_cause='', category='adjudicator')
                 for i, s in enumerate(raw, start=1) if isinstance(s, dict))


def _verify_suspicions(rt: Runtime, state: ReviewState, result: AgentResult) -> ReviewState:
    candidates = suspicion_candidates(result.output or {}, len(state.candidates))
    if not candidates:
        return state
    rt.emit(f'  {len(candidates)} new suspicion(s) from the adjudicator go through fresh verifiers')
    clusters = deterministic_clusters(candidates, len(state.clusters))
    new_state = replace(state, candidates=state.candidates + candidates, clusters=state.clusters + clusters)
    findings, results, limitations = verify_clusters(rt, new_state, clusters, prefix='adj-')
    return replace(new_state, findings=new_state.findings + findings).with_results(results).with_limitations(*limitations)


def run_adjudicate(rt: Runtime, state: ReviewState) -> ReviewState:
    started = time.monotonic()
    rt.emit('phase adjudicate: independent audit')
    brief = adjudicator_brief(rt.ctx, 'adjudicator', findings_payload(state.findings), coverage_payload(rt, state),
                              state.limitations)
    result = rt.run([rt.spec('adjudicator', 'adjudicator', brief, marker='adjudicator')])[0]
    new_state = state.with_results((result,))
    if not result.completed or result.output is None:
        new_state = new_state.with_limitations(f'adjudicator {result.status}: {result.error}; findings were not audited')
    else:
        findings, notes = apply_decisions(state.findings, result.output, result.agent_id)
        gaps = tuple(str(g) for g in result.output.get('coverage_gaps', []) if str(g).strip())
        new_state = replace(new_state, findings=findings, coverage_gaps=state.coverage_gaps + gaps,
                            adjudicator_summary=str(result.output.get('summary', ''))).with_limitations(*notes)
        new_state = _verify_suspicions(rt, new_state, result)
    capped = apply_cap(new_state.findings, rt.config.max_findings, 'adjudicator')
    return replace(new_state, findings=capped).with_timing('adjudicate', time.monotonic() - started)
