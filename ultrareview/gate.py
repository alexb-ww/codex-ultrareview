"""Deterministic gate: evidence authentication, drift and the final status."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence, Tuple

from .exclusions import is_safe_relative, is_secret_like
from .ledger import Ledger, command_ran, quote_matches
from .models import Finding, Quote, Reproduction
from .snapshot import Drift, SnapshotReader
from .state import ReviewState

VERSION_ORDER = ('worktree', 'index', 'commit', 'base')


@dataclass(frozen=True)
class GateResult:
    status: str
    errors: Tuple[str, ...]
    warnings: Tuple[str, ...]
    findings: Tuple[Finding, ...]
    drift: Optional[Drift]


def _worktree_lines(reader: SnapshotReader, path: str) -> Tuple[str, ...]:
    """Lines of a file that is not part of the snapshot (context outside the diff)."""
    if not is_safe_relative(path) or is_secret_like(path):
        return ()
    full = reader.repo.joinpath(*path.split('/'))
    try:
        if full.is_symlink() or not full.is_file() or full.stat().st_size > 4 * 1024 * 1024:
            return ()
        text = full.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return ()
    lines = text.split('\n')
    return tuple(line.rstrip('\r') for line in (lines[:-1] if lines and lines[-1] == '' else lines))


def quote_ok(reader: SnapshotReader, quote: Quote) -> bool:
    versions = (quote.version,) + tuple(v for v in VERSION_ORDER if v != quote.version)
    recorded = False
    for version in versions:
        if reader.record(quote.path, version) is not None:
            recorded = True
        lines = reader.read_lines(quote.path, version)
        if lines and quote_matches(lines, quote.line, quote.text):
            return True
    if recorded:
        return False
    return quote_matches(_worktree_lines(reader, quote.path), quote.line, quote.text)


def _verification_issues(finding: Finding, ledger: Ledger, reader: SnapshotReader,
                         authenticate_commands: bool) -> Tuple[str, ...]:
    issues = []
    for verification in finding.verifications:
        if verification.verdict == 'REFUTED':
            continue
        for command in verification.commands_run if authenticate_commands else ():
            if not command_ran(ledger, verification.verifier_id, command):
                issues.append(f'{verification.verifier_id} claims a command it did not run: {command}')
        for quote in verification.quotes:
            if not quote_ok(reader, quote):
                issues.append(f'{verification.verifier_id} quote does not match {quote.path}:{quote.line}: {quote.text[:80]}')
        if verification.verdict == 'CONFIRMED' and not verification.quotes:
            issues.append(f'{verification.verifier_id} says CONFIRMED without quoting a line')
    return tuple(issues)


def _reproduction_check(reproduction: Optional[Reproduction], ledger: Ledger,
                        authenticate_commands: bool) -> Tuple[Optional[Reproduction], Tuple[str, ...]]:
    if reproduction is None or reproduction.result != 'reproduced' or not authenticate_commands:
        return reproduction, ()
    if reproduction.command and command_ran(ledger, reproduction.reproducer_id, reproduction.command):
        return reproduction, ()
    note = f'{reproduction.reproducer_id} reports a reproduction command that is not in its execution log: {reproduction.command or "(empty)"}'
    return replace(reproduction, result='unverified-evidence'), (note,)


def apply_gate(findings: Sequence[Finding], ledger: Ledger, reader: SnapshotReader,
               authenticate_commands: bool = True) -> Tuple[Finding, ...]:
    gated = []
    for finding in findings:
        reproduction, repro_issues = _reproduction_check(finding.reproduction, ledger, authenticate_commands)
        issues = _verification_issues(finding, ledger, reader, authenticate_commands) + repro_issues
        gated.append(replace(finding, reproduction=reproduction, evidence_issues=finding.evidence_issues + issues))
    return tuple(gated)


def compute_status(state: ReviewState, drift: Optional[Drift], findings: Sequence[Finding]) -> str:
    if not state.results:
        return 'blocked'
    if drift is not None and not drift.unchanged:
        return 'partial'
    if state.failed_results():
        return 'partial'
    if any(f.verdict == 'UNVERIFIED' for f in findings):
        return 'partial'
    return 'complete'


def run_gate(state: ReviewState, ledger: Ledger, reader: SnapshotReader, drift: Optional[Drift],
             authenticate_commands: bool = True) -> GateResult:
    findings = apply_gate(state.findings, ledger, reader, authenticate_commands)
    errors: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    if not authenticate_commands:
        warnings += ('commands claimed by agents were not authenticated against an execution log (skill mode); '
                     'quotes were checked against the snapshot',)
    if drift is not None and not drift.unchanged:
        changed = ', '.join(drift.changed_paths[:10]) or 'git state'
        errors += (f'repository changed during the review ({changed}); findings need re-validation',)
    for finding in findings:
        for issue in finding.evidence_issues:
            warnings += (f'{finding.id}: {issue}',)
    for result in state.failed_results():
        warnings += (f'agent {result.agent_id} {result.status}: {result.error}',)
    ids = [f.id for f in findings]
    if len(ids) != len(set(ids)):
        errors += ('duplicate finding ids',)
    return GateResult(status=compute_status(state, drift, findings), errors=errors, warnings=warnings,
                      findings=findings, drift=drift)
