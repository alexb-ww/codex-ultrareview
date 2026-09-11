"""Finder angles: independent lenses that hunt for candidates in a change."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .errors import ConfigError


@dataclass(frozen=True)
class Angle:
    id: str
    name: str
    kind: str
    template: str


DIFF_ANGLES: Tuple[Angle, ...] = (
    Angle('a-line-scan', 'Line-by-line hunk scan', 'diff', 'angles/a-line-scan.md'),
    Angle('b-removed-behavior', 'Removed-behavior auditor', 'diff', 'angles/b-removed-behavior.md'),
    Angle('c-cross-file', 'Cross-file tracer', 'diff', 'angles/c-cross-file.md'),
    Angle('d-language-pitfalls', 'Language and framework pitfalls', 'diff', 'angles/d-language-pitfalls.md'),
    Angle('e-wrapper-proxy', 'Wrapper, proxy and adapter correctness', 'diff', 'angles/e-wrapper-proxy.md'),
)

DOMAIN_ANGLES: Tuple[Angle, ...] = (
    Angle('security', 'Security and authorization', 'domain', 'angles/security.md'),
    Angle('state-concurrency', 'State and concurrency', 'domain', 'angles/state-concurrency.md'),
    Angle('contracts-data', 'Contracts and data', 'domain', 'angles/contracts-data.md'),
    Angle('failure-resources', 'Failure handling and resources', 'domain', 'angles/failure-resources.md'),
    Angle('tests-integration', 'Tests and integration', 'domain', 'angles/tests-integration.md'),
)

ALL_ANGLES: Tuple[Angle, ...] = DIFF_ANGLES + DOMAIN_ANGLES

PROFILE_ANGLE_IDS = {
    'fast': ('a-line-scan', 'b-removed-behavior', 'c-cross-file', 'security', 'contracts-data'),
    'standard': ('a-line-scan', 'b-removed-behavior', 'c-cross-file', 'd-language-pitfalls',
                 'e-wrapper-proxy', 'security', 'state-concurrency', 'contracts-data', 'failure-resources'),
    'deep': tuple(angle.id for angle in ALL_ANGLES),
}

REPO_ANGLE_IDS = ('a-line-scan', 'security', 'state-concurrency', 'contracts-data',
                  'failure-resources', 'tests-integration')


def angle_by_id(angle_id: str) -> Angle:
    for angle in ALL_ANGLES:
        if angle.id == angle_id:
            return angle
    raise ConfigError(f'unknown angle {angle_id!r}')


def select_angles(profile: str, scope_kind: str) -> Tuple[Angle, ...]:
    if scope_kind == 'repo':
        ids = REPO_ANGLE_IDS
    else:
        try:
            ids = PROFILE_ANGLE_IDS[profile]
        except KeyError as exc:
            raise ConfigError(f'unknown profile {profile!r}') from exc
    return tuple(angle_by_id(angle_id) for angle_id in ids)
