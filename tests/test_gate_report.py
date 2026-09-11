from __future__ import annotations

import unittest

from tests.helpers import seeded_repo
from ultrareview import gate as gate_mod
from ultrareview.ledger import build_ledger
from ultrareview.models import Candidate, Cluster, Decision, Finding, Quote, Reproduction, Verification
from ultrareview.phases.adjudicate import apply_cap, apply_decisions, suspicion_candidates
from ultrareview.phases.find import shard_paths
from ultrareview.phases.repro import select_for_repro, test_hints
from ultrareview.phases.sweep import attach_to_existing
from ultrareview.phases.triage import clusters_from_agent, deterministic_clusters
from ultrareview.runner import AgentResult, CommandRecord
from ultrareview.scope import ScopeSpec, resolve_scope
from ultrareview.snapshot import SnapshotReader, detect_drift, take_snapshot
from ultrareview.state import ReviewState


def cand(cid: str, file: str = 'src/app.py', line: int = 2, angle: str = 'a-line-scan') -> Candidate:
    return Candidate(id=cid, angle=angle, finder_id=f'finder-{angle}', file=file, line_start=line, line_end=line,
                     summary=f'{cid} summary', failure_scenario=f'{cid} scenario long enough', root_cause='rc',
                     category='correctness')


def verification(vid: str, verdict: str = 'CONFIRMED', quotes=(), commands=(), severity: str = 'P1') -> Verification:
    return Verification(verifier_id=vid, verdict=verdict, severity=severity, origin='introduced', trigger='t',
                        reasoning='r', counterevidence_checked=(), quotes=tuple(quotes), what_would_confirm='',
                        fix='f', regression_test='g', commands_run=tuple(commands))


def result(agent_id: str, role: str, *commands: str, completed: bool = True) -> AgentResult:
    return AgentResult(agent_id=agent_id, role=role, status='completed' if completed else 'failed',
                       output={} if completed else None, error=None if completed else 'boom', thread_id='t',
                       commands=tuple(CommandRecord(f"/bin/zsh -lc '{c}'", 0) for c in commands), usage={},
                       duration_s=1.0, events_path='', output_path='', brief_path='', attempts=1)


def finding(fid: str, candidate: Candidate, *verifications: Verification, **overrides) -> Finding:
    base = dict(id=fid, cluster=Cluster(f'K-{fid}', (candidate.id,), candidate.id), candidate=candidate,
                members=(candidate,), verifications=tuple(verifications))
    return Finding(**{**base, **overrides})


class TriageTests(unittest.TestCase):
    def test_deterministic_clusters_group_overlapping_lines(self) -> None:
        clusters = deterministic_clusters([cand('C1', line=2), cand('C2', line=4, angle='security'),
                                           cand('C3', file='src/util.py', line=1), cand('C4', line=40)])
        members = sorted(sorted(c.member_ids) for c in clusters)
        self.assertEqual(members, [['C1', 'C2'], ['C3'], ['C4']])
        self.assertTrue(all(c.canonical_id in c.member_ids for c in clusters))

    def test_agent_partition_validation(self) -> None:
        cands = [cand('C1'), cand('C2')]
        good = {'clusters': [{'cluster_id': 'x', 'member_ids': ['C1', 'C2'], 'canonical_id': 'C2', 'rationale': 'same'}]}
        self.assertEqual(clusters_from_agent(good, cands, 0)[0].canonical_id, 'C2')
        for bad in ({'clusters': [{'cluster_id': 'x', 'member_ids': ['C1'], 'canonical_id': 'C1', 'rationale': ''}]},
                    {'clusters': [{'cluster_id': 'x', 'member_ids': ['C1', 'C2'], 'canonical_id': 'C9', 'rationale': ''}]},
                    {'clusters': 'nope'}):
            with self.assertRaises(ValueError):
                clusters_from_agent(bad, cands, 0)


class SweepAndAdjudicateTests(unittest.TestCase):
    def test_attach_overlapping_candidates_to_existing_findings(self) -> None:
        existing = finding('UR-1', cand('C1', line=2), verification('v1'))
        updated, fresh = attach_to_existing([cand('C5', line=4), cand('C6', line=50)], [existing])
        self.assertEqual(len(updated[0].members), 2)
        self.assertEqual([c.id for c in fresh], ['C6'])

    def test_apply_decisions_and_cap(self) -> None:
        findings = tuple(finding(f'UR-{i}', cand(f'C{i}', line=i * 20), verification(f'v{i}', severity='P2')) for i in range(1, 4))
        output = {'decisions': [{'finding_id': 'UR-2', 'action': 'reject', 'severity': 'P3', 'reason': 'guarded', 'merged_into': ''},
                                {'finding_id': 'UR-9', 'action': 'accept', 'severity': 'P1', 'reason': '', 'merged_into': ''}]}
        decided, notes = apply_decisions(findings, output, 'adj')
        self.assertFalse(decided[1].accepted)
        self.assertTrue(notes and 'UR-9' in notes[0])
        capped = apply_cap(decided, 1, 'adj')
        downgraded = [f for f in capped if f.decision and f.decision.reason.startswith('cap')]
        self.assertEqual(len(downgraded), 1)

    def test_suspicion_candidates(self) -> None:
        cands = suspicion_candidates({'new_suspicions': [{'file': 'a.py', 'line_start': 1, 'line_end': 2,
                                                          'summary': 's', 'failure_scenario': 'f'}]}, 7)
        self.assertEqual(cands[0].id, 'C8')
        self.assertEqual(cands[0].angle, 'adjudicator')


class ReproSelectionTests(unittest.TestCase):
    def test_selection_respects_mode_and_cap(self) -> None:
        confirmed = finding('UR-1', cand('C1'), verification('v1', 'CONFIRMED', severity='P2'))
        plausible = finding('UR-2', cand('C2', line=30), verification('v2', 'PLAUSIBLE', severity='P1'))
        refuted = finding('UR-3', cand('C3', line=60), verification('v3', 'REFUTED'))
        done = finding('UR-4', cand('C4', line=90), verification('v4', 'CONFIRMED'),
                       reproduction=Reproduction('r', 'reproduced', 'c', '.', 0, '', '', '', ''))
        findings = (confirmed, plausible, refuted, done)
        self.assertEqual(select_for_repro(findings, 'off', 6), ())
        self.assertEqual([f.id for f in select_for_repro(findings, 'auto', 1)], ['UR-2'])
        self.assertEqual([f.id for f in select_for_repro(findings, 'all', 1)], ['UR-2', 'UR-1'])

    def test_hints_and_shards(self) -> None:
        repo = seeded_repo()
        try:
            repo.write('go.mod', 'module x\n')
            self.assertIn('go test', test_hints(repo.path, {'test_stack': 'go test'}))
            self.assertIn('No test runner', test_hints(repo.parent, None))
        finally:
            repo.cleanup()
        self.assertEqual(shard_paths(['a', 'b', 'c'], 2), (('a', 'b'), ('c',)))
        self.assertEqual(shard_paths([], 2), ())


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()
        self.repo.git('checkout', '-q', '-b', 'feature')
        self.repo.write('src/app.py', 'def add(a, b):\n    return a - b\n')
        self.repo.commit('bug')
        self.scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        self.snapshot = take_snapshot(self.repo.path, self.scope)
        self.reader = SnapshotReader(self.repo.path, self.snapshot)

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_quote_ok_falls_back_to_base_version(self) -> None:
        self.assertTrue(gate_mod.quote_ok(self.reader, Quote('src/app.py', 2, 'return a - b')))
        self.assertTrue(gate_mod.quote_ok(self.reader, Quote('src/app.py', 2, 'return a + b')), 'base version still has the plus')
        self.assertFalse(gate_mod.quote_ok(self.reader, Quote('src/app.py', 2, 'return a * b')))
        self.assertFalse(gate_mod.quote_ok(self.reader, Quote('src/nope.py', 1, 'x')))

    def test_quote_from_unchanged_context_file_is_read_from_the_worktree(self) -> None:
        self.assertNotIn('src/util.py', self.snapshot.targets)
        self.assertTrue(gate_mod.quote_ok(self.reader, Quote('src/util.py', 1, 'def clamp(x, lo, hi):')))
        self.assertFalse(gate_mod.quote_ok(self.reader, Quote('src/util.py', 1, 'def clamp(x):')))
        self.repo.write('.env', 'SECRET=1\n')
        self.assertFalse(gate_mod.quote_ok(self.reader, Quote('.env', 1, 'SECRET=1')), 'secret-like files are never read')
        self.assertFalse(gate_mod.quote_ok(self.reader, Quote('../outside.py', 1, 'x')))

    def test_gate_flags_bad_evidence_and_keeps_good(self) -> None:
        ledger = build_ledger([result('v1', 'verifier', 'sed -n 1,3p src/app.py'), result('r1', 'reproducer', 'pytest -q')])
        good = finding('UR-1', cand('C1'), verification('v1', quotes=[Quote('src/app.py', 2, 'return a - b')],
                                                         commands=['sed -n 1,3p src/app.py']),
                       reproduction=Reproduction('r1', 'reproduced', 'pytest -q', '.', 0, 'F', 'e', 't', ''))
        bad = finding('UR-2', cand('C2', line=1), verification('v9', quotes=[Quote('src/app.py', 1, 'def sub')],
                                                               commands=['make test']),
                      reproduction=Reproduction('r9', 'reproduced', 'make test', '.', 1, 'F', 'e', 't', ''))
        confirmed_without_quote = finding('UR-3', cand('C3', line=1), verification('v1', quotes=[]))
        state = ReviewState(findings=(good, bad, confirmed_without_quote), results=(result('v1', 'verifier'),))
        gate = gate_mod.run_gate(state, ledger, self.reader, detect_drift(self.snapshot, self.repo.path))
        self.assertEqual(gate.status, 'partial', 'evidence problems never yield a complete run')
        by_id = {f.id: f for f in gate.findings}
        self.assertEqual(by_id['UR-1'].evidence_issues, ())
        self.assertTrue(by_id['UR-1'].accepted)
        self.assertGreaterEqual(len(by_id['UR-2'].evidence_issues), 3)
        self.assertEqual(by_id['UR-2'].reproduction.result, 'unverified-evidence')
        self.assertFalse(by_id['UR-2'].accepted)
        self.assertIn('without quoting', by_id['UR-3'].evidence_issues[0])
        self.assertTrue(any('UR-2' in w for w in gate.warnings))

    def test_status_partial_on_failure_or_unverified(self) -> None:
        ledger = build_ledger([])
        unverified = finding('UR-1', cand('C1'))
        state = ReviewState(findings=(unverified,), results=(result('f1', 'finder'),))
        self.assertEqual(gate_mod.run_gate(state, ledger, self.reader, None).status, 'partial')
        state = ReviewState(findings=(), results=(result('f1', 'finder', completed=False),))
        self.assertEqual(gate_mod.run_gate(state, ledger, self.reader, None).status, 'partial')
        self.assertEqual(gate_mod.run_gate(ReviewState(), ledger, self.reader, None).status, 'blocked')

    def test_drift_becomes_gate_error(self) -> None:
        self.repo.write('src/app.py', 'def add(a, b):\n    return a + b\n')
        drift = detect_drift(self.snapshot, self.repo.path)
        gate = gate_mod.run_gate(ReviewState(results=(result('f1', 'finder'),)), build_ledger([]), self.reader, drift)
        self.assertEqual(gate.status, 'partial')
        self.assertTrue(gate.errors and 'changed during the review' in gate.errors[0])


if __name__ == '__main__':
    unittest.main()
