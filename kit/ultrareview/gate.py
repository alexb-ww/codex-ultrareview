"""Deterministic gate: evidence authentication, drift and the final status."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional, Sequence, Tuple

from .exclusions import is_safe_relative, is_secret_like
from .ledger import Ledger, command_ran, exit_codes_for, quote_matches
from .models import Finding, Quote, Reproduction, Verification
from .safe_read import SafeReadError, read_regular_beneath
from .snapshot import Drift, SnapshotReader
from .state import ReviewState

VERSION_ORDER = ('worktree', 'index', 'commit', 'base')
CONTEXT_FILE_LIMIT = 4 * 1024 * 1024
QUOTE_REQUIRED = ('CONFIRMED', 'REFUTED')


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
    try:
        data = read_regular_beneath(reader.repo, path, CONTEXT_FILE_LIMIT)
    except SafeReadError:
        return ()
    lines = data.decode('utf-8', 'replace').split('\n')
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


def verification_issues(verification: Verification, ledger: Ledger, reader: SnapshotReader,
                        authenticate_commands: bool) -> Tuple[str, ...]:
    """Evidence problems of one verification; every verdict is checked, REFUTED included."""
    issues = []
    who = verification.verifier_id
    for command in verification.commands_run if authenticate_commands else ():
        if not command_ran(ledger, who, command):
            issues.append(f'{who} claims a command it did not run: {command}')
    for quote in verification.quotes:
        if not quote_ok(reader, quote):
            issues.append(f'{who} quote does not match {quote.path}:{quote.line}: {quote.text[:80]}')
    if verification.verdict in QUOTE_REQUIRED and not verification.quotes:
        issues.append(f'{who} says {verification.verdict} without quoting a line')
    return tuple(issues)


def _reproduction_issues(reproduction: Reproduction, ledger: Ledger, authenticate_commands: bool) -> Tuple[str, ...]:
    if not authenticate_commands:
        return ()
    who = reproduction.reproducer_id
    issues = [f'{who} claims a command it did not run: {command}'
              for command in reproduction.commands_run if not command_ran(ledger, who, command)]
    if reproduction.result != 'reproduced':
        return tuple(issues)
    if not reproduction.command or not command_ran(ledger, who, reproduction.command):
        issues.append(f'{who} reports a reproduction command that is not in its execution log: {reproduction.command or "(empty)"}')
        return tuple(issues)
    codes = exit_codes_for(ledger, who, reproduction.command)
    if codes and reproduction.exit_code not in codes:
        issues.append(f'{who} reports exit code {reproduction.exit_code} but the log shows {sorted(codes)} for that command')
    return tuple(issues)


def gate_finding(finding: Finding, ledger: Ledger, reader: SnapshotReader, authenticate_commands: bool) -> Finding:
    """Attach evidence issues; drop refutations whose evidence fails so they cannot bury a real bug."""
    issues: Tuple[str, ...] = ()
    kept = []
    for verification in finding.verifications:
        problems = verification_issues(verification, ledger, reader, authenticate_commands)
        issues += problems
        if problems and verification.verdict == 'REFUTED':
            issues += (f'{verification.verifier_id}: refutation set aside because its evidence did not authenticate',)
            continue
        kept.append(verification)
    reproduction = finding.reproduction
    if reproduction is not None:
        repro_issues = _reproduction_issues(reproduction, ledger, authenticate_commands)
        issues += repro_issues
        if repro_issues and reproduction.result == 'reproduced':
            reproduction = replace(reproduction, result='unverified-evidence')
    return replace(finding, verifications=tuple(kept), reproduction=reproduction,
                   evidence_issues=finding.evidence_issues + issues)


def apply_gate(findings: Sequence[Finding], ledger: Ledger, reader: SnapshotReader,
               authenticate_commands: bool = True) -> Tuple[Finding, ...]:
    return tuple(gate_finding(finding, ledger, reader, authenticate_commands) for finding in findings)


def compute_status(state: ReviewState, drift: Optional[Drift], findings: Sequence[Finding]) -> str:
    if not state.results:
        return 'blocked'
    if drift is not None and not drift.unchanged:
        return 'partial'
    if state.failed_results():
        return 'partial'
    if any(f.verdict == 'UNVERIFIED' or f.evidence_issues for f in findings):
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
