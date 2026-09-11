"""Immutable domain records shared by the phases, the gate and the report."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Optional, Tuple

VERDICT_RANK = {'CONFIRMED': 0, 'PLAUSIBLE': 1, 'REFUTED': 2}
SEVERITY_RANK = {'P0': 0, 'P1': 1, 'P2': 2, 'P3': 3}


@dataclass(frozen=True)
class Quote:
    path: str
    line: int
    text: str
    version: str = 'worktree'


@dataclass(frozen=True)
class Candidate:
    id: str
    angle: str
    finder_id: str
    file: str
    line_start: int
    line_end: int
    summary: str
    failure_scenario: str
    root_cause: str
    category: str
    quotes: Tuple[Quote, ...] = ()
    commands_run: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Cluster:
    id: str
    member_ids: Tuple[str, ...]
    canonical_id: str
    rationale: str = ''


@dataclass(frozen=True)
class Verification:
    verifier_id: str
    verdict: str
    severity: str
    origin: str
    trigger: str
    reasoning: str
    counterevidence_checked: Tuple[str, ...]
    quotes: Tuple[Quote, ...]
    what_would_confirm: str
    fix: str
    regression_test: str
    commands_run: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Reproduction:
    reproducer_id: str
    result: str
    command: str
    cwd: str
    exit_code: int
    output_excerpt: str
    explanation: str
    test_file: str
    blocked_reason: str
    commands_run: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Decision:
    adjudicator_id: str
    action: str
    severity: str
    reason: str
    merged_into: str = ''


@dataclass(frozen=True)
class Finding:
    id: str
    cluster: Cluster
    candidate: Candidate
    members: Tuple[Candidate, ...]
    verifications: Tuple[Verification, ...] = ()
    reproduction: Optional[Reproduction] = None
    decision: Optional[Decision] = None
    evidence_issues: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def verdict(self) -> str:
        """Recall-biased aggregate: a majority of REFUTED votes refutes, otherwise the best vote stands."""
        if not self.verifications:
            return 'UNVERIFIED'
        refuted = sum(v.verdict == 'REFUTED' for v in self.verifications)
        if refuted * 2 > len(self.verifications):
            return 'REFUTED'
        kept = [v for v in self.verifications if v.verdict != 'REFUTED']
        return min((v.verdict for v in kept), key=lambda x: VERDICT_RANK.get(x, 9)) if kept else 'REFUTED'

    @property
    def severity(self) -> str:
        if self.decision and self.decision.action in ('accept', 'downgrade'):
            return self.decision.severity
        votes = [v.severity for v in self.verifications if v.verdict != 'REFUTED']
        return min(votes, key=lambda s: SEVERITY_RANK.get(s, 9)) if votes else 'P3'

    @property
    def origin(self) -> str:
        votes = [v.origin for v in self.verifications if v.verdict != 'REFUTED']
        if not votes:
            return 'unknown'
        if all(v == 'introduced' for v in votes):
            return 'introduced'
        if all(v == 'pre-existing' for v in votes):
            return 'pre-existing'
        return 'unknown'

    @property
    def verification_label(self) -> str:
        if self.reproduction and self.reproduction.result == 'reproduced':
            return 'reproduced'
        verdict = self.verdict
        if verdict == 'CONFIRMED':
            return 'source-verified'
        if verdict == 'PLAUSIBLE':
            return 'plausible'
        return verdict.lower()

    @property
    def accepted(self) -> bool:
        reproduced = self.reproduction is not None and self.reproduction.result == 'reproduced'
        if self.evidence_issues and not reproduced:
            return False
        if self.decision is not None and self.decision.action == 'reject':
            return False
        return self.verdict in ('CONFIRMED', 'PLAUSIBLE')

    def with_verification(self, verification: Verification) -> 'Finding':
        return replace(self, verifications=self.verifications + (verification,))


def quote_from_dict(item: Dict[str, Any], default_version: str = 'worktree') -> Quote:
    return Quote(path=str(item.get('path', '')), line=int(item.get('line', 0)), text=str(item.get('text', '')),
                 version=str(item.get('version', default_version)))


def candidate_from_dict(item: Dict[str, Any], candidate_id: str, angle: str, finder_id: str) -> Candidate:
    return Candidate(id=candidate_id, angle=angle, finder_id=finder_id, file=str(item.get('file', '')),
                     line_start=int(item.get('line_start', 0)), line_end=int(item.get('line_end', 0)),
                     summary=str(item.get('summary', '')), failure_scenario=str(item.get('failure_scenario', '')),
                     root_cause=str(item.get('root_cause', '')), category=str(item.get('category', 'correctness')),
                     quotes=tuple(quote_from_dict(q) for q in item.get('quotes', []) if isinstance(q, dict)),
                     commands_run=tuple(str(c) for c in item.get('commands_run', [])))


def verification_from_dict(item: Dict[str, Any], verifier_id: str) -> Verification:
    return Verification(verifier_id=verifier_id, verdict=str(item['verdict']), severity=str(item['severity']),
                        origin=str(item['origin']), trigger=str(item.get('trigger', '')),
                        reasoning=str(item.get('reasoning', '')),
                        counterevidence_checked=tuple(str(x) for x in item.get('counterevidence_checked', [])),
                        quotes=tuple(quote_from_dict(q) for q in item.get('quotes', []) if isinstance(q, dict)),
                        what_would_confirm=str(item.get('what_would_confirm', '')), fix=str(item.get('fix', '')),
                        regression_test=str(item.get('regression_test', '')),
                        commands_run=tuple(str(c) for c in item.get('commands_run', [])))


def reproduction_from_dict(item: Dict[str, Any], reproducer_id: str) -> Reproduction:
    return Reproduction(reproducer_id=reproducer_id, result=str(item['result']), command=str(item.get('command', '')),
                        cwd=str(item.get('cwd', '')), exit_code=int(item.get('exit_code', 0)),
                        output_excerpt=str(item.get('output_excerpt', '')), explanation=str(item.get('explanation', '')),
                        test_file=str(item.get('test_file', '')), blocked_reason=str(item.get('blocked_reason', '')),
                        commands_run=tuple(str(c) for c in item.get('commands_run', [])))


def to_dict(record: Any) -> Dict[str, Any]:
    return asdict(record)


def finding_to_dict(finding: Finding) -> Dict[str, Any]:
    return {**asdict(finding), 'verdict': finding.verdict, 'severity': finding.severity, 'origin': finding.origin,
            'verification': finding.verification_label, 'accepted': finding.accepted}
