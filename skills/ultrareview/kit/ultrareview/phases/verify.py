"""Phase 4 — verify: one fresh verifier per cluster (or ``votes`` of them)."""
from __future__ import annotations

from dataclasses import replace
import time
from typing import Dict, List, Sequence, Tuple

from ..briefs import verifier_brief
from ..models import Candidate, Cluster, Finding, verification_from_dict
from ..pool import Runtime
from ..runner import AgentResult, AgentSpec
from ..state import ReviewState
from .triage import candidate_payload


def verifier_specs(rt: Runtime, clusters: Sequence[Cluster], by_id: Dict[str, Candidate],
                   votes: int, prefix: str = '') -> Tuple[AgentSpec, ...]:
    specs: List[AgentSpec] = []
    for cluster in clusters:
        canonical = by_id[cluster.canonical_id]
        others = [candidate_payload(by_id[m]) for m in cluster.member_ids if m != cluster.canonical_id and m in by_id]
        payload = {**candidate_payload(canonical), 'cluster_id': cluster.id,
                   'other_reports_of_the_same_cause': others}
        for vote in range(1, votes + 1):
            agent_id = f'verifier-{prefix}{cluster.id}-v{vote}'
            brief = verifier_brief(rt.ctx, agent_id, payload, cluster.id)
            specs.append(rt.spec(agent_id, 'verifier', brief, marker=cluster.id))
    return tuple(specs)


def build_findings(clusters: Sequence[Cluster], by_id: Dict[str, Candidate], results: Sequence[AgentResult],
                   start_index: int) -> Tuple[Finding, ...]:
    by_cluster: Dict[str, List[AgentResult]] = {}
    for result in results:
        by_cluster.setdefault(result.marker, []).append(result)
    findings = []
    for offset, cluster in enumerate(clusters, start=1):
        verifications = tuple(verification_from_dict(r.output, r.agent_id)
                              for r in by_cluster.get(cluster.id, []) if r.completed and r.output)
        members = tuple(by_id[m] for m in cluster.member_ids if m in by_id)
        findings.append(Finding(id=f'UR-{start_index + offset}', cluster=cluster, candidate=by_id[cluster.canonical_id],
                                members=members, verifications=verifications))
    return tuple(findings)


def verifier_limitations(results: Sequence[AgentResult]) -> Tuple[str, ...]:
    return tuple(f'verifier {r.agent_id} {r.status}: {r.error}; the candidate stays unverified'
                 for r in results if not r.completed)


def verify_clusters(rt: Runtime, state: ReviewState, clusters: Sequence[Cluster],
                    prefix: str = '') -> Tuple[Tuple[Finding, ...], Tuple[AgentResult, ...], Tuple[str, ...]]:
    by_id = state.candidates_by_id()
    specs = verifier_specs(rt, clusters, by_id, rt.config.votes, prefix)
    results = rt.run(specs)
    findings = build_findings(clusters, by_id, results, len(state.findings))
    return findings, results, verifier_limitations(results)


def run_verify(rt: Runtime, state: ReviewState) -> ReviewState:
    started = time.monotonic()
    pending = [c for c in state.clusters if c.id not in {f.cluster.id for f in state.findings}]
    rt.emit(f'phase verify: {len(pending)} cluster(s) × {rt.config.votes} vote(s)')
    findings, results, limitations = verify_clusters(rt, state, pending)
    kept = sum(1 for f in findings if f.verdict in ('CONFIRMED', 'PLAUSIBLE'))
    rt.emit(f'  {kept} kept, {sum(1 for f in findings if f.verdict == "REFUTED")} refuted, '
            f'{sum(1 for f in findings if f.verdict == "UNVERIFIED")} unverified')
    return (replace(state, findings=state.findings + findings).with_results(results)
            .with_limitations(*limitations).with_timing('verify', time.monotonic() - started))
