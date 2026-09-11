from __future__ import annotations

import unittest

from ultrareview import models


def candidate(cid: str = 'F1-1') -> models.Candidate:
    return models.Candidate(id=cid, angle='a-line-scan', finder_id='finder-a', file='src/app.py', line_start=2,
                            line_end=2, summary='add subtracts', failure_scenario='add(2,3) == -1',
                            root_cause='operator', category='correctness',
                            quotes=(models.Quote('src/app.py', 2, '    return a - b'),))


def verification(verdict: str, severity: str = 'P2', origin: str = 'introduced', vid: str = 'v1') -> models.Verification:
    return models.Verification(verifier_id=vid, verdict=verdict, severity=severity, origin=origin, trigger='t',
                               reasoning='r', counterevidence_checked=(), quotes=(), what_would_confirm='',
                               fix='f', regression_test='g')


def finding(*verifications: models.Verification, **overrides) -> models.Finding:
    cand = candidate()
    base = dict(id='UR-1', cluster=models.Cluster('C1', (cand.id,), cand.id), candidate=cand, members=(cand,),
                verifications=tuple(verifications))
    return models.Finding(**{**base, **overrides})


class FindingAggregationTests(unittest.TestCase):
    def test_single_confirmed_vote(self) -> None:
        f = finding(verification('CONFIRMED', 'P1'))
        self.assertEqual(f.verdict, 'CONFIRMED')
        self.assertEqual(f.severity, 'P1')
        self.assertEqual(f.verification_label, 'source-verified')
        self.assertTrue(f.accepted)

    def test_majority_refuted_refutes(self) -> None:
        f = finding(verification('REFUTED'), verification('REFUTED', vid='v2'), verification('PLAUSIBLE', vid='v3'))
        self.assertEqual(f.verdict, 'REFUTED')
        self.assertFalse(f.accepted)

    def test_single_non_refuted_vote_carries(self) -> None:
        f = finding(verification('REFUTED'), verification('PLAUSIBLE', vid='v2'))
        self.assertEqual(f.verdict, 'PLAUSIBLE')
        self.assertEqual(f.verification_label, 'plausible')
        self.assertTrue(f.accepted)

    def test_unverified_when_no_votes(self) -> None:
        f = finding()
        self.assertEqual(f.verdict, 'UNVERIFIED')
        self.assertFalse(f.accepted)

    def test_reproduction_wins_label(self) -> None:
        repro = models.Reproduction('r1', 'reproduced', 'pytest', '/w', 1, 'FAILED', 'shows it', 't.py', '')
        f = finding(verification('CONFIRMED'), reproduction=repro)
        self.assertEqual(f.verification_label, 'reproduced')

    def test_decision_reject_and_severity_override(self) -> None:
        rejected = finding(verification('CONFIRMED'), decision=models.Decision('adj', 'reject', 'P2', 'guarded'))
        self.assertFalse(rejected.accepted)
        downgraded = finding(verification('CONFIRMED', 'P0'), decision=models.Decision('adj', 'downgrade', 'P2', 'rare'))
        self.assertEqual(downgraded.severity, 'P2')
        self.assertTrue(downgraded.accepted)

    def test_evidence_issues_block_unless_reproduced(self) -> None:
        f = finding(verification('CONFIRMED'), evidence_issues=('quote mismatch',))
        self.assertFalse(f.accepted)

    def test_origin_aggregation(self) -> None:
        self.assertEqual(finding(verification('CONFIRMED', origin='pre-existing')).origin, 'pre-existing')
        mixed = finding(verification('CONFIRMED', origin='introduced'), verification('PLAUSIBLE', origin='unknown', vid='v2'))
        self.assertEqual(mixed.origin, 'unknown')
        self.assertEqual(finding().origin, 'unknown')


class FromDictTests(unittest.TestCase):
    def test_candidate_and_verification_parsing(self) -> None:
        cand = models.candidate_from_dict({'file': 'a.py', 'line_start': 1, 'line_end': 2, 'summary': 's',
                                           'failure_scenario': 'f', 'root_cause': 'r', 'category': 'security',
                                           'quotes': [{'path': 'a.py', 'line': 1, 'text': 'x'}],
                                           'commands_run': ['cat a.py']}, 'C-1', 'security', 'finder-s')
        self.assertEqual(cand.quotes[0].version, 'worktree')
        self.assertEqual(cand.commands_run, ('cat a.py',))
        ver = models.verification_from_dict({'verdict': 'PLAUSIBLE', 'severity': 'P2', 'origin': 'unknown',
                                             'quotes': [], 'commands_run': []}, 'v9')
        self.assertEqual(ver.verifier_id, 'v9')
        payload = models.finding_to_dict(finding(ver))
        self.assertEqual(payload['verdict'], 'PLAUSIBLE')
        self.assertTrue(payload['accepted'])


if __name__ == '__main__':
    unittest.main()
