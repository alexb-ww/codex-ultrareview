"""Immutable review state passed from phase to phase, plus JSON persistence."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .models import Candidate, Cluster, Finding, finding_to_dict, to_dict
from .runner import AgentResult, result_to_dict


@dataclass(frozen=True)
class ReviewState:
    map_output: Optional[Dict[str, Any]] = None
    candidates: Tuple[Candidate, ...] = ()
    clusters: Tuple[Cluster, ...] = ()
    findings: Tuple[Finding, ...] = ()
    results: Tuple[AgentResult, ...] = ()
    limitations: Tuple[str, ...] = ()
    coverage_gaps: Tuple[str, ...] = ()
    adjudicator_summary: str = ''
    phase_timings: Tuple[Tuple[str, float], ...] = field(default_factory=tuple)
    worktree_notes: Tuple[str, ...] = ()

    def with_results(self, results: Tuple[AgentResult, ...]) -> 'ReviewState':
        return replace(self, results=self.results + tuple(results))

    def with_limitations(self, *items: str) -> 'ReviewState':
        return replace(self, limitations=self.limitations + tuple(item for item in items if item))

    def with_timing(self, phase: str, seconds: float) -> 'ReviewState':
        return replace(self, phase_timings=self.phase_timings + ((phase, round(seconds, 2)),))

    def candidates_by_id(self) -> Dict[str, Candidate]:
        return {candidate.id: candidate for candidate in self.candidates}

    def findings_by_id(self) -> Dict[str, Finding]:
        return {finding.id: finding for finding in self.findings}

    def failed_results(self) -> Tuple[AgentResult, ...]:
        return tuple(result for result in self.results if not result.completed)

    def usage_totals(self) -> Dict[str, int]:
        totals: Dict[str, int] = {}
        for result in self.results:
            for key, value in result.usage.items():
                totals[key] = totals.get(key, 0) + value
        return totals


def state_to_dict(state: ReviewState) -> Dict[str, Any]:
    return {
        'map_output': state.map_output,
        'candidates': [to_dict(c) for c in state.candidates],
        'clusters': [to_dict(c) for c in state.clusters],
        'findings': [finding_to_dict(f) for f in state.findings],
        'results': [result_to_dict(r) for r in state.results],
        'limitations': list(state.limitations),
        'coverage_gaps': list(state.coverage_gaps),
        'adjudicator_summary': state.adjudicator_summary,
        'phase_timings': [list(item) for item in state.phase_timings],
        'worktree_notes': list(state.worktree_notes),
        'usage_totals': state.usage_totals(),
    }


def save_state(state: ReviewState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state_to_dict(state), indent=2, ensure_ascii=False), encoding='utf-8')
