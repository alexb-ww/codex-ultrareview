"""Run configuration: immutable settings validated once at the CLI boundary."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import time
from typing import Optional, Tuple

from .errors import ConfigError
from .scope import Limits, ScopeSpec

EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
PROFILES = ('fast', 'standard', 'deep')
ROLES = ('mapper', 'finder', 'triage', 'verifier', 'reproducer', 'sweep', 'adjudicator')
REPRO_MODES = ('auto', 'off', 'all')
LANGUAGES = ('en', 'ru')
DEFAULT_PREAMBLE = (
    'Не задавай GOCACHE, GOMODCACHE, GOLANGCI_LINT_CACHE и другие кэши в /tmp, используй '
    'системные значения по умолчанию. Не запускай go test -race и make l2/l3/l4, только обычный '
    'go test по нужным пакетам. Не создавай worktree, DerivedData и симуляторы, используй '
    'существующие. Результаты сборок удаляй сразу после анализа.'
)


@dataclass(frozen=True)
class EffortConfig:
    model: Optional[str] = None
    default: Optional[str] = None
    per_role: Tuple[Tuple[str, str], ...] = ()

    def for_role(self, role: str) -> Optional[str]:
        for name, effort in self.per_role:
            if name == role:
                return effort
        return self.default

    def validate(self) -> 'EffortConfig':
        for value in (self.default, *(effort for _, effort in self.per_role)):
            if value is not None and value not in EFFORTS:
                raise ConfigError(f'unknown reasoning effort {value!r}; expected one of {", ".join(EFFORTS)}')
        for role, _ in self.per_role:
            if role not in ROLES:
                raise ConfigError(f'unknown role {role!r}')
        if self.model is not None and (not self.model.strip() or self.model.startswith('-')):
            raise ConfigError(f'invalid model name {self.model!r}')
        return self


@dataclass(frozen=True)
class RunConfig:
    repo: Path
    scope: ScopeSpec
    run_dir: Path
    profile: str = 'deep'
    jobs: int = 4
    agent_timeout: int = 1200
    votes: int = 1
    repro: str = 'auto'
    repro_sandbox: str = 'workspace-write'
    max_repro: int = 6
    max_findings: int = 15
    max_candidates_per_angle: int = 8
    lang: str = 'en'
    preamble: Optional[str] = DEFAULT_PREAMBLE
    keep_sessions: bool = False
    keep_worktree: bool = False
    dry_run: bool = False
    limits: Limits = field(default_factory=Limits)
    effort: EffortConfig = field(default_factory=EffortConfig)
    codex_bin: str = 'codex'
    retries: int = 1
    inline_diff_limit: int = 64 * 1024
    map_threshold_files: int = 12
    shard_threshold_files: int = 60
    replay: bool = False

    def validate(self) -> 'RunConfig':
        self.scope.validate()
        self.effort.validate()
        if self.profile not in PROFILES:
            raise ConfigError(f'unknown profile {self.profile!r}; expected one of {", ".join(PROFILES)}')
        if self.repro not in REPRO_MODES:
            raise ConfigError(f'unknown repro mode {self.repro!r}; expected one of {", ".join(REPRO_MODES)}')
        if self.repro_sandbox not in ('workspace-write', 'danger-full-access'):
            raise ConfigError(f'--repro-sandbox must be workspace-write or danger-full-access, got {self.repro_sandbox!r}')
        if self.lang not in LANGUAGES:
            raise ConfigError(f'unknown language {self.lang!r}; expected one of {", ".join(LANGUAGES)}')
        for name, value, low in (('jobs', self.jobs, 1), ('agent-timeout', self.agent_timeout, 30),
                                 ('votes', self.votes, 1), ('max-repro', self.max_repro, 0),
                                 ('max-findings', self.max_findings, 1), ('retries', self.retries, 0)):
            if not isinstance(value, int) or value < low:
                raise ConfigError(f'--{name} must be an integer >= {low}, got {value!r}')
        if self.limits.max_files < 1 or self.limits.max_lines < 1:
            raise ConfigError('limits must be positive')
        if not self.codex_bin.strip():
            raise ConfigError('codex binary name must not be empty')
        return self


def default_run_dir(repo: Path, now: Optional[float] = None) -> Path:
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(now))
    name = re.sub(r'[^A-Za-z0-9_.-]+', '-', repo.resolve().name) or 'repo'
    return Path.home() / '.cache' / 'ultrareview' / 'runs' / f'{stamp}-{name}'


def ensure_run_dir_outside_repo(run_dir: Path, repo: Path) -> Path:
    resolved = run_dir.expanduser().resolve()
    repo_resolved = repo.resolve()
    if resolved == repo_resolved or repo_resolved in resolved.parents:
        raise ConfigError(f'run directory {resolved} must be outside the repository {repo_resolved}')
    return resolved
