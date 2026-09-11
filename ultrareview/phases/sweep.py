"""Phase 6 — sweep: one fresh finder hunts only for what the first wave missed."""
from __future__ import annotations

from dataclasses import replace
import time
from typing import Dict, List, Sequence, Tuple

from ..briefs import sweep_brief
from ..models import Candidate, Finding
from ..pool import Runtime
from ..state import ReviewState
from .find import collect_candidates
from .triage import LINE_WINDOW, deterministic_clusters
from .verify import verify_clusters


def verified_payload(findings: Sequence[Finding]) -> List[Dict[str, object]]:
    return [{'file': f.candidate.file, 'line_start': f.candidate.line_start, 'verdict': f.verdict,
             'summary': f.candidate.summary}
            for f in findings if f.verdict in ('CONFIRMED', 'PLAUSIBLE')]


def _touches(candidate: Candidate, finding: Finding) -> bool:
    for member in (finding.candidate,) + tuple(finding.members):
        if member.file == candidate.file and \
                candidate.line_start - LINE_WINDOW <= member.line_end and member.line_start - LINE_WINDOW <= candidate.line_end:
            return True
    return False


def attach_to_existing(candidates: Sequence[Candidate], findings: Sequence[Finding]) -> Tuple[Tuple[Finding, ...], Tuple[Candidate, ...]]:
    updated = list(findings)
    fresh: List[Candidate] = []
    for candidate in candidates:
        for index, finding in enumerate(updated):
            if _touches(candidate, finding):
                updated[index] = replace(finding, members=finding.members + (candidate,))
                break
        else:
            fresh.append(candidate)
    return tuple(updated), tuple(fresh)


def run_sweep(rt: Runtime, state: ReviewState) -> ReviewState:
    started = time.monotonic()
    rt.emit('phase sweep: one gap finder')
    result = rt.run([rt.spec('sweep', 'sweep', sweep_brief(rt.ctx, 'sweep', verified_payload(state.findings)), marker='sweep')])[0]
    new_state = state.with_results((result,))
    if not result.completed:
        return new_state.with_limitations(f'sweep {result.status}: {result.error}; no gap pass').with_timing('sweep', time.monotonic() - started)
    candidates = collect_candidates((result,), rt.config.max_candidates_per_angle, len(state.candidates))
    findings, fresh = attach_to_existing(candidates, state.findings)
    new_state = replace(new_state, candidates=state.candidates + candidates, findings=findings)
    rt.emit(f'  {len(candidates)} new candidate(s), {len(fresh)} not overlapping existing findings')
    if not fresh:
        return new_state.with_timing('sweep', time.monotonic() - started)
    clusters = deterministic_clusters(fresh, len(new_state.clusters))
    new_state = replace(new_state, clusters=new_state.clusters + clusters)
    verified, results, limitations = verify_clusters(rt, new_state, clusters, prefix='sw-')
    return (replace(new_state, findings=new_state.findings + verified).with_results(results)
            .with_limitations(*limitations).with_timing('sweep', time.monotonic() - started))
