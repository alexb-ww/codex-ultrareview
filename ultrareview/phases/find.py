"""Phase 2 — find: independent finder angles, each a fresh agent."""
from __future__ import annotations

from dataclasses import replace
import time
from typing import Dict, List, Optional, Sequence, Tuple

from ..angles import Angle, select_angles
from ..briefs import finder_brief
from ..models import Candidate, candidate_from_dict
from ..pool import Runtime
from ..runner import AgentResult, AgentSpec
from ..state import ReviewState

SHARD_SIZE = 40


def shard_paths(paths: Sequence[str], size: int = SHARD_SIZE) -> Tuple[Tuple[str, ...], ...]:
    if not paths:
        return ()
    return tuple(tuple(paths[i:i + size]) for i in range(0, len(paths), size))


def finder_specs(rt: Runtime, state: ReviewState) -> Tuple[AgentSpec, ...]:
    scope = rt.ctx.scope
    angles = select_angles(rt.config.profile, scope.kind)
    shards: Tuple[Optional[Tuple[str, ...]], ...] = (None,)
    if scope.kind == 'repo' and scope.changed_files > rt.config.shard_threshold_files:
        shards = shard_paths(scope.target_paths)
    specs: List[AgentSpec] = []
    for angle in angles:
        for index, shard in enumerate(shards):
            suffix = f'-s{index + 1}' if shard is not None else ''
            agent_id = f'finder-{angle.id}{suffix}'
            brief = finder_brief(rt.ctx, agent_id, angle, state.map_output, shard)
            specs.append(rt.spec(agent_id, 'finder', brief, marker=angle.id))
    return tuple(specs)


def parse_candidates(result: AgentResult, angle_id: str, cap: int, start: int) -> Tuple[Candidate, ...]:
    if not result.completed or result.output is None:
        return ()
    raw = [item for item in result.output.get('candidates', []) if isinstance(item, dict)]
    return tuple(candidate_from_dict(item, f'C{start + offset}', angle_id, result.agent_id)
                 for offset, item in enumerate(raw[:cap], start=1))


def collect_candidates(results: Sequence[AgentResult], cap: int, start: int = 0) -> Tuple[Candidate, ...]:
    collected: List[Candidate] = []
    for result in results:
        collected.extend(parse_candidates(result, result.marker, cap, start + len(collected)))
    return tuple(collected)


def finder_limitations(results: Sequence[AgentResult], cap: int) -> Tuple[str, ...]:
    notes = []
    for result in results:
        if not result.completed:
            notes.append(f'finder {result.agent_id} {result.status}: {result.error}; its angle is uncovered')
        elif len(result.output.get('candidates', [])) > cap:
            notes.append(f'finder {result.agent_id} returned more than {cap} candidates; extra ones were dropped')
    return tuple(notes)


def angle_coverage(results: Sequence[AgentResult]) -> Dict[str, Dict[str, object]]:
    coverage: Dict[str, Dict[str, object]] = {}
    for result in results:
        files = result.output.get('files_read', []) if result.completed and result.output else []
        note = result.output.get('coverage_note', '') if result.completed and result.output else result.error
        coverage[result.agent_id] = {'angle': result.marker, 'status': result.status,
                                     'files_read': list(files), 'note': note}
    return coverage


def run_find(rt: Runtime, state: ReviewState) -> ReviewState:
    started = time.monotonic()
    specs = finder_specs(rt, state)
    rt.emit(f'phase find: {len(specs)} finder(s)')
    results = rt.run(specs)
    candidates = collect_candidates(results, rt.config.max_candidates_per_angle, len(state.candidates))
    rt.emit(f'  {len(candidates)} candidate(s) collected')
    new_state = replace(state, candidates=state.candidates + candidates)
    return (new_state.with_results(results)
            .with_limitations(*finder_limitations(results, rt.config.max_candidates_per_angle))
            .with_timing('find', time.monotonic() - started))
