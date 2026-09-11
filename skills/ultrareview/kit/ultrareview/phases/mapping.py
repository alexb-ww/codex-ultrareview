"""Phase 1 — map: a factual map of the code under review (no bug hypotheses)."""
from __future__ import annotations

from dataclasses import replace
import time

from ..briefs import mapper_brief
from ..pool import Runtime
from ..state import ReviewState


def needs_map(rt: Runtime) -> bool:
    scope = rt.ctx.scope
    return scope.kind == 'repo' or scope.changed_files > rt.config.map_threshold_files


def run_map(rt: Runtime, state: ReviewState) -> ReviewState:
    if not needs_map(rt):
        rt.emit('phase map: skipped (small change)')
        return state
    started = time.monotonic()
    rt.emit('phase map: running mapper')
    result = rt.run([rt.spec('mapper', 'mapper', mapper_brief(rt.ctx, 'mapper'))])[0]
    new_state = state.with_results((result,)).with_timing('map', time.monotonic() - started)
    if not result.completed:
        return new_state.with_limitations(f'mapper {result.status}: {result.error}; finders ran without a map')
    return replace(new_state, map_output=result.output)
