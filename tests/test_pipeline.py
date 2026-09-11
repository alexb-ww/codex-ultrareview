from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import ROOT, TempRepo, seeded_repo
from ultrareview import cli
from ultrareview.config import EffortConfig, RunConfig
from ultrareview.errors import LimitExceeded
from ultrareview.pipeline import run_review
from ultrareview.scope import Limits, ScopeSpec

FAKE_CODEX = str(ROOT / 'tests' / 'fake_codex' / 'codex')
APP_LINE = '    return a - b'


def candidate(file: str, line: int, summary: str, quote: str = '', commands=()) -> dict:
    return {'file': file, 'line_start': line, 'line_end': line, 'summary': summary,
            'failure_scenario': f'{summary}: wrong result', 'root_cause': 'operator', 'category': 'correctness',
            'quotes': [{'path': file, 'line': line, 'text': quote}] if quote else [], 'commands_run': list(commands)}


def verifier(verdict: str, severity: str = 'P1', quotes=(), commands=(), origin: str = 'introduced') -> dict:
    return {'verdict': verdict, 'severity': severity, 'origin': origin, 'trigger': 'add(2, 3)',
            'reasoning': f'{verdict} because the operator is wrong', 'counterevidence_checked': ['no guard upstream'],
            'quotes': [{'path': p, 'line': l, 'text': t} for p, l, t in quotes], 'what_would_confirm': 'run it',
            'fix': 'use +', 'regression_test': 'add(2,3) == 5', 'commands_run': list(commands)}


def happy_scenario() -> dict:
    repro_cmd = 'python3 -c "import sys; sys.path.insert(0, \'src\'); import app; print(app.add(2, 3))"'
    return {
        'finder:a-line-scan': {'output': {'candidates': [candidate('src/app.py', 2, 'add subtracts', APP_LINE, ['cat src/app.py'])],
                                          'files_read': ['src/app.py'], 'coverage_note': 'read app.py'},
                               'commands': [{'command': 'cat src/app.py', 'exit_code': 0, 'output': 'x'}]},
        'finder:b-removed-behavior': {'output': {'candidates': [candidate('src/app.py', 2, 'plus became minus', APP_LINE)],
                                                 'files_read': ['src/app.py'], 'coverage_note': 'compared base'}},
        'finder:c-cross-file': {'output': {'candidates': [candidate('src/util.py', 1, 'clamp callers unchecked')],
                                           'files_read': ['src/util.py'], 'coverage_note': 'traced callers'}},
        'triage': {'output': {'clusters': [
            {'cluster_id': 'X', 'member_ids': ['C1', 'C2'], 'canonical_id': 'C1', 'rationale': 'same operator'},
            {'cluster_id': 'Y', 'member_ids': ['C3'], 'canonical_id': 'C3', 'rationale': 'single'}]}},
        'verifier:K1': {'output': verifier('CONFIRMED', 'P1', [('src/app.py', 2, 'return a - b')], ['sed -n 1,5p src/app.py']),
                        'commands': [{'command': 'sed -n 1,5p src/app.py', 'exit_code': 0, 'output': APP_LINE}]},
        'verifier:K2': {'output': verifier('REFUTED', 'P3')},
        'reproducer:UR-1': {'output': {'result': 'reproduced', 'command': repro_cmd, 'cwd': '.', 'exit_code': 0,
                                       'output_excerpt': '-1', 'explanation': 'prints -1, expected 5',
                                       'test_file': 'test_repro.py', 'blocked_reason': '', 'commands_run': [repro_cmd]},
                            'commands': [{'command': repro_cmd, 'exit_code': 0, 'output': '-1\n'}]},
        'sweep': {'output': {'candidates': [candidate('src/util.py', 12, 'late footgun')], 'files_read': ['src/util.py'],
                             'coverage_note': 'gap pass'}},
        'verifier:K3': {'output': verifier('PLAUSIBLE', 'P2', origin='unknown')},
        'adjudicator': {'output': {'decisions': [
            {'finding_id': 'UR-1', 'action': 'accept', 'severity': 'P1', 'reason': 'clear', 'merged_into': ''},
            {'finding_id': 'UR-3', 'action': 'downgrade', 'severity': 'P3', 'reason': 'rare', 'merged_into': ''}],
            'coverage_gaps': ['no end-to-end flow checked'], 'new_suspicions': [], 'summary': 'One real bug.'}},
    }


class PipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()
        self.repo.git('checkout', '-q', '-b', 'feature')
        self.repo.write('src/app.py', 'def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n')
        self.repo.commit('bug')
        self.tmp = Path(tempfile.mkdtemp(prefix='ur-pipe-')).resolve()
        self.scenario_path = self.tmp / 'scenario.json'
        self.log_path = self.tmp / 'log.jsonl'
        self.env = mock.patch.dict(os.environ, {'FAKE_CODEX_SCENARIO': str(self.scenario_path),
                                                'FAKE_CODEX_LOG': str(self.log_path)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.repo.cleanup()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, **overrides) -> RunConfig:
        base = dict(repo=self.repo.path, scope=ScopeSpec(kind='branch', base='main'), run_dir=self.tmp / 'run',
                    profile='fast', jobs=2, agent_timeout=30, codex_bin=FAKE_CODEX, retries=0,
                    effort=EffortConfig(default='high', per_role=(('verifier', 'xhigh'),)))
        return RunConfig(**{**base, **overrides})

    def write_scenario(self, scenario: dict) -> None:
        self.scenario_path.write_text(json.dumps(scenario), encoding='utf-8')

    def invocations(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def test_happy_path_end_to_end(self) -> None:
        self.write_scenario(happy_scenario())
        outcome = run_review(self.config(), emit=lambda _: None)
        self.assertEqual(outcome.status, 'complete', outcome.markdown)
        self.assertEqual(outcome.exit_code, 0)
        report = json.loads(Path(outcome.json_path).read_text())
        accepted = {f['id']: f for f in report['findings']}
        self.assertEqual(set(accepted), {'UR-1', 'UR-3'})
        self.assertEqual(accepted['UR-1']['verification'], 'reproduced')
        self.assertEqual(accepted['UR-1']['severity'], 'P1')
        self.assertEqual(accepted['UR-3']['severity'], 'P3')
        self.assertEqual([f['id'] for f in report['rejected']], ['UR-2'])
        self.assertEqual(len(accepted['UR-1']['members']), 2, 'the duplicate report was merged')
        self.assertIn('REPRODUCED', outcome.markdown)
        self.assertIn('One real bug.', outcome.markdown)
        self.assertIn('no end-to-end flow checked', outcome.markdown)
        run_dir = self.tmp / 'run'
        self.assertTrue((run_dir / 'ledger.jsonl').exists())
        self.assertTrue((run_dir / 'agents' / 'finder-a-line-scan.events.jsonl').exists())
        self.assertFalse(list((run_dir / 'worktrees').glob('*')) if (run_dir / 'worktrees').exists() else [])
        roles = [inv['role'] for inv in self.invocations()]
        self.assertEqual(roles.count('finder'), 5)
        self.assertEqual(roles.count('verifier'), 3)
        self.assertEqual(roles.count('reproducer'), 2, 'UR-1 after verify, UR-3 after the sweep')
        self.assertEqual([inv['marker'] for inv in self.invocations() if inv['role'] == 'reproducer'], ['UR-1', 'UR-3'])
        repro = next(inv for inv in self.invocations() if inv['role'] == 'reproducer')
        self.assertEqual(repro['sandbox'], 'workspace-write')
        self.assertIn('worktrees/UR-1', repro['cwd'])
        verifier_inv = next(inv for inv in self.invocations() if inv['role'] == 'verifier')
        self.assertIn('model_reasoning_effort="xhigh"', verifier_inv['config'])
        self.assertNotIn('mapper', roles)
        self.assertEqual(len(self.repo.git('status', '--porcelain').splitlines()), 0, 'repo untouched')

    def test_unauthenticated_evidence_is_downgraded(self) -> None:
        scenario = happy_scenario()
        scenario['verifier:K1'] = {'output': verifier('CONFIRMED', 'P1', [('src/app.py', 2, 'return a * b')], ['make test'])}
        scenario['reproducer:UR-1']['commands'] = []
        self.write_scenario(scenario)
        outcome = run_review(self.config(), emit=lambda _: None)
        report = json.loads(Path(outcome.json_path).read_text())
        ids = {f['id'] for f in report['findings']}
        self.assertNotIn('UR-1', ids)
        unresolved = {f['id']: f for f in report['unresolved']}
        self.assertIn('UR-1', unresolved)
        issues = ' '.join(unresolved['UR-1']['evidence_issues'])
        self.assertIn('did not run', issues)
        self.assertIn('quote does not match', issues)
        self.assertEqual(unresolved['UR-1']['reproduction']['result'], 'unverified-evidence')

    def test_failed_agent_makes_partial(self) -> None:
        scenario = happy_scenario()
        scenario['finder:security'] = {'mode': 'crash'}
        self.write_scenario(scenario)
        outcome = run_review(self.config(), emit=lambda _: None)
        self.assertEqual(outcome.status, 'partial')
        self.assertEqual(outcome.exit_code, 3)
        self.assertIn('finder-security', outcome.markdown)

    def test_drift_during_review_is_reported(self) -> None:
        scenario = happy_scenario()
        scenario['sweep']['write_file'] = {'path': 'src/app.py', 'text': 'def add(a, b):\n    return a + b\n'}
        self.write_scenario(scenario)
        outcome = run_review(self.config(), emit=lambda _: None)
        self.assertEqual(outcome.status, 'partial')
        self.assertIn('changed during the review', outcome.markdown)

    def test_empty_scope_exits_zero_without_agents(self) -> None:
        self.repo.git('checkout', '-q', 'main')
        self.write_scenario({})
        outcome = run_review(self.config(), emit=lambda _: None)
        self.assertEqual(outcome.status, 'empty')
        self.assertEqual(outcome.exit_code, 0)
        self.assertFalse(self.log_path.exists())

    def test_limits_refuse_before_agents(self) -> None:
        self.write_scenario({})
        with self.assertRaises(LimitExceeded):
            run_review(self.config(limits=Limits(max_files=500, max_lines=1)), emit=lambda _: None)
        self.assertFalse(self.log_path.exists())

    def test_dry_run_prints_plan_only(self) -> None:
        self.write_scenario({})
        outcome = run_review(self.config(dry_run=True), emit=lambda _: None)
        self.assertEqual(outcome.status, 'dry-run')
        self.assertIn('finder(s)', outcome.markdown)
        self.assertFalse(self.log_path.exists())

    def test_repo_scope_with_map_and_repro_off(self) -> None:
        scenario = happy_scenario()
        scenario['mapper'] = {'output': {'modules': [{'name': 'core', 'paths': ['src'], 'purpose': 'math'}],
                                         'entry_points': [], 'trust_boundaries': [], 'storages': [],
                                         'external_contracts': [], 'critical_flows': [], 'test_stack': 'unittest',
                                         'unknowns': []}}
        self.write_scenario(scenario)
        outcome = run_review(self.config(scope=ScopeSpec(kind='repo'), repro='off'), emit=lambda _: None)
        roles = [inv['role'] for inv in self.invocations()]
        self.assertIn('mapper', roles)
        self.assertNotIn('reproducer', roles)
        self.assertIn(outcome.status, ('complete', 'partial'))


class CliTests(unittest.TestCase):
    def test_plan_command_and_bad_flags(self) -> None:
        repo = seeded_repo()
        try:
            with mock.patch.dict(os.environ, {'FAKE_CODEX_SCENARIO': ''}):
                code = cli.main(['plan', '--repo', str(repo.path), '--scope', 'changes', '--codex-bin', FAKE_CODEX,
                                 '--run-dir', str(repo.parent / 'run')])
            self.assertEqual(code, 0)
            self.assertEqual(cli.main(['run', '--repo', str(repo.path), '--jobs', '0', '--codex-bin', FAKE_CODEX]), 1)
            self.assertEqual(cli.main(['run', '--repo', str(repo.path), '--scope', 'branch', '--base', 'nope',
                                       '--codex-bin', FAKE_CODEX, '--run-dir', str(repo.parent / 'run2')]), 1)
        finally:
            repo.cleanup()

    def test_default_command_is_run(self) -> None:
        repo = TempRepo()
        try:
            code = cli.main(['--repo', str(repo.path), '--scope', 'changes', '--codex-bin', FAKE_CODEX,
                             '--run-dir', str(repo.parent / 'run'), '--dry-run'])
            self.assertEqual(code, 0)
        finally:
            repo.cleanup()


if __name__ == '__main__':
    unittest.main()
