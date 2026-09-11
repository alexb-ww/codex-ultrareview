"""The runtime handed to every phase: builds agent specs and runs them in parallel."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from .briefs import BriefContext
from .config import RunConfig
from .replay import AgentsNeeded
from .runner import AgentResult, AgentSpec, run_agent
from .schemas import schema_for

Emitter = Callable[[str], None]
AgentRunner = Callable[[AgentSpec, int], AgentResult]


def stderr_emitter(message: str) -> None:
    sys.stderr.write(message.rstrip('\n') + '\n')
    sys.stderr.flush()


@dataclass(frozen=True)
class Runtime:
    config: RunConfig
    ctx: BriefContext
    emit: Emitter = stderr_emitter
    run_agent_fn: AgentRunner = run_agent
    schema_overrides: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def spec(self, agent_id: str, role: str, brief: str, *, sandbox: str = 'read-only',
             cwd: Optional[Path] = None, marker: str = '') -> AgentSpec:
        schema = self.schema_overrides.get(role) or schema_for(role)
        return AgentSpec(agent_id=agent_id, role=role, brief=brief, schema=schema,
                         cwd=cwd or self.config.repo, run_dir=self.config.run_dir, sandbox=sandbox,
                         effort=self.config.effort.for_role(role), model=self.config.effort.model,
                         timeout=self.config.agent_timeout, keep_session=self.config.keep_sessions,
                         codex_bin=self.config.codex_bin, marker=marker)

    def run(self, specs: Sequence[AgentSpec], jobs: Optional[int] = None) -> Tuple[AgentResult, ...]:
        if not specs:
            return ()
        workers = max(1, min(jobs or self.config.jobs, len(specs)))
        self.emit(f'  launching {len(specs)} agent(s) with {workers} worker(s): '
                  + ', '.join(spec.agent_id for spec in specs))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(self.run_agent_fn, spec, self.config.retries) for spec in specs]
            results = tuple(future.result() for future in futures)
        pending = [spec for spec, result in zip(specs, results) if result.status == 'pending']
        if pending:
            raise AgentsNeeded(pending)
        for result in results:
            state = result.status if result.completed else f'{result.status}: {result.error}'
            self.emit(f'  {result.agent_id}: {state} ({result.duration_s}s, {len(result.commands)} command(s))')
        return results
