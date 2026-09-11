"""Phase 3 — triage: group candidates by root cause; merge, never drop."""
from __future__ import annotations

from dataclasses import replace
import time
from typing import Dict, List, Sequence, Tuple

from ..briefs import triage_brief
from ..models import Candidate, Cluster
from ..pool import Runtime
from ..state import ReviewState

LINE_WINDOW = 5


def _overlaps(a: Candidate, b: Candidate) -> bool:
    if a.file != b.file:
        return False
    return a.line_start - LINE_WINDOW <= b.line_end and b.line_start - LINE_WINDOW <= a.line_end


def _pick_canonical(members: Sequence[Candidate]) -> Candidate:
    return max(members, key=lambda c: (len(c.failure_scenario), len(c.quotes), -int(c.id[1:]) if c.id[1:].isdigit() else 0))


def deterministic_clusters(candidates: Sequence[Candidate], start: int = 0) -> Tuple[Cluster, ...]:
    groups: List[List[Candidate]] = []
    for candidate in sorted(candidates, key=lambda c: (c.file, c.line_start, c.id)):
        for group in groups:
            if any(_overlaps(candidate, member) for member in group):
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    return tuple(Cluster(id=f'K{start + index}', member_ids=tuple(c.id for c in group),
                         canonical_id=_pick_canonical(group).id,
                         rationale='same file and overlapping lines' if len(group) > 1 else 'single report')
                 for index, group in enumerate(groups, start=1))


def candidate_payload(candidate: Candidate) -> Dict[str, object]:
    return {'id': candidate.id, 'angle': candidate.angle, 'file': candidate.file,
            'line_start': candidate.line_start, 'line_end': candidate.line_end, 'summary': candidate.summary,
            'failure_scenario': candidate.failure_scenario, 'root_cause': candidate.root_cause,
            'category': candidate.category,
            'quotes': [{'path': q.path, 'line': q.line, 'text': q.text} for q in candidate.quotes]}


def _valid_partition(clusters: Sequence[Dict[str, object]], ids: Sequence[str]) -> bool:
    seen: List[str] = []
    for cluster in clusters:
        members = cluster.get('member_ids')
        if not isinstance(members, list) or not members:
            return False
        if cluster.get('canonical_id') not in members:
            return False
        seen.extend(str(m) for m in members)
    return sorted(seen) == sorted(ids)


def clusters_from_agent(output: Dict[str, object], candidates: Sequence[Candidate], start: int) -> Tuple[Cluster, ...]:
    raw = output.get('clusters')
    ids = [c.id for c in candidates]
    if not isinstance(raw, list) or not _valid_partition(raw, ids):
        raise ValueError('triage output is not a partition of the candidates')
    return tuple(Cluster(id=f'K{start + index}', member_ids=tuple(str(m) for m in cluster['member_ids']),
                         canonical_id=str(cluster['canonical_id']), rationale=str(cluster.get('rationale', '')))
                 for index, cluster in enumerate(raw, start=1))


def triage_candidates(rt: Runtime, candidates: Sequence[Candidate], start: int,
                      agent_id: str = 'triage') -> Tuple[Tuple[Cluster, ...], Tuple[str, ...], tuple]:
    fallback = deterministic_clusters(candidates, start)
    if len(candidates) < 2:
        return fallback, (), ()
    brief = triage_brief(rt.ctx, agent_id, [candidate_payload(c) for c in candidates])
    result = rt.run([rt.spec(agent_id, 'triage', brief, marker='triage')])[0]
    if not result.completed or result.output is None:
        return fallback, (f'triage {result.status}: {result.error}; deterministic clustering used',), (result,)
    try:
        return clusters_from_agent(result.output, candidates, start), (), (result,)
    except ValueError as exc:
        return fallback, (f'triage output rejected ({exc}); deterministic clustering used',), (result,)


def run_triage(rt: Runtime, state: ReviewState) -> ReviewState:
    started = time.monotonic()
    rt.emit(f'phase triage: {len(state.candidates)} candidate(s)')
    clusters, limitations, results = triage_candidates(rt, state.candidates, len(state.clusters))
    rt.emit(f'  {len(clusters)} cluster(s)')
    return (replace(state, clusters=state.clusters + clusters).with_results(tuple(results))
            .with_limitations(*limitations).with_timing('triage', time.monotonic() - started))
