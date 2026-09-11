from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from tests.helpers import ROOT, seeded_repo
from tests.test_pipeline import FAKE_CODEX, happy_scenario
from ultrareview import cli
from ultrareview.config import RunConfig
from ultrareview.pipeline import EXIT_AGENTS_NEEDED, run_step
from ultrareview.scope import ScopeSpec


class StepModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()
        self.repo.git('checkout', '-q', '-b', 'feature')
        self.repo.write('src/app.py', 'def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n')
        self.repo.commit('bug')
        self.tmp = Path(tempfile.mkdtemp(prefix='ur-step-')).resolve()
        self.scenario_path = self.tmp / 'scenario.json'
        self.scenario_path.write_text(json.dumps(happy_scenario()), encoding='utf-8')
        self.env = mock.patch.dict(os.environ, {'FAKE_CODEX_SCENARIO': str(self.scenario_path), 'FAKE_CODEX_LOG': ''})
        self.env.start()
        self.run_dir = self.tmp / 'steprun'

    def tearDown(self) -> None:
        self.env.stop()
        self.repo.cleanup()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, **overrides) -> RunConfig:
        base = dict(repo=self.repo.path, scope=ScopeSpec(kind='branch', base='main'), run_dir=self.run_dir,
                    profile='fast', jobs=2, agent_timeout=30, codex_bin=FAKE_CODEX, retries=0)
        return RunConfig(**{**base, **overrides})

    def coordinator_round(self) -> int:
        """Play the coordinator: run every pending agent through the fake codex and record its JSON."""
        pending = json.loads((self.run_dir / 'pending.json').read_text())
        for item in pending:
            with open(item['brief_path'], 'rb') as brief:
                subprocess.run([FAKE_CODEX, 'exec', '-C', item['cwd'], '--output-schema', item['schema_path'],
                                '-o', item['output_path'], '-'], stdin=brief, stdout=subprocess.DEVNULL, check=True)
        return len(pending)

    def test_step_loop_reaches_the_report(self) -> None:
        rounds = []
        outcome = None
        for _ in range(12):
            outcome = run_step(self.config(), emit=lambda _: None)
            if outcome.exit_code != EXIT_AGENTS_NEEDED:
                break
            rounds.append(self.coordinator_round())
        assert outcome is not None
        self.assertEqual(outcome.status, 'complete', outcome.markdown)
        self.assertEqual(rounds[0], 5, 'first batch is the five fast-profile finders')
        self.assertGreaterEqual(len(rounds), 5)
        report = json.loads((self.run_dir / 'report.json').read_text())
        accepted = {f['id']: f for f in report['findings']}
        self.assertIn('UR-1', accepted)
        self.assertEqual(accepted['UR-1']['verification'], 'reproduced')
        self.assertTrue(any('not authenticated' in w for w in report['gate']['warnings']))
        self.assertTrue((self.run_dir / 'config.json').exists())
        self.assertFalse(list((self.run_dir / 'worktrees').glob('*')) if (self.run_dir / 'worktrees').exists() else [])
        self.assertNotIn('worktrees', self.repo.git('worktree', 'list').replace(str(self.repo.path), ''))

    def test_later_steps_reuse_the_saved_configuration(self) -> None:
        first = run_step(self.config(), emit=lambda _: None)
        self.assertEqual(first.exit_code, EXIT_AGENTS_NEEDED)
        again = run_step(self.config(scope=ScopeSpec(kind='changes'), profile='deep'), emit=lambda _: None)
        self.assertEqual(again.exit_code, EXIT_AGENTS_NEEDED)
        pending = json.loads((self.run_dir / 'pending.json').read_text())
        self.assertEqual(len(pending), 5, 'profile and scope come from config.json, not from the new flags')

    def test_record_validates_and_stores_agent_output(self) -> None:
        first = run_step(self.config(), emit=lambda _: None)
        self.assertEqual(first.exit_code, EXIT_AGENTS_NEEDED)
        pending = json.loads((self.run_dir / 'pending.json').read_text())
        agent_id = pending[0]['agent_id']
        good = json.dumps({'candidates': [], 'files_read': [], 'coverage_note': 'n'})
        good_file = self.tmp / 'good.json'
        good_file.write_text('```json\n' + good + '\n```', encoding='utf-8')
        self.assertEqual(cli.main(['record', '--run-dir', str(self.run_dir), '--agent-id', agent_id,
                                   '--json-file', str(good_file)]), 0)
        self.assertEqual(json.loads(Path(pending[0]['output_path']).read_text())['coverage_note'], 'n')
        bad_file = self.tmp / 'bad.json'
        bad_file.write_text('not json at all', encoding='utf-8')
        self.assertEqual(cli.main(['record', '--run-dir', str(self.run_dir), '--agent-id', pending[1]['agent_id'],
                                   '--json-file', str(bad_file)]), 3)
        self.assertEqual(cli.main(['record', '--run-dir', str(self.run_dir), '--agent-id', 'nobody',
                                   '--json-file', str(good_file)]), 3)
        second = run_step(self.config(), emit=lambda _: None)
        self.assertEqual(second.exit_code, EXIT_AGENTS_NEEDED)
        still_pending = {p['agent_id'] for p in json.loads((self.run_dir / 'pending.json').read_text())}
        self.assertNotIn(agent_id, still_pending)
        self.assertNotIn(pending[1]['agent_id'], still_pending, 'an invalid answer counts as failed, not pending')

    def test_cli_step_requires_run_dir(self) -> None:
        self.assertEqual(cli.main(['step', '--repo', str(self.repo.path), '--codex-bin', FAKE_CODEX]), 1)
        code = cli.main(['step', '--repo', str(self.repo.path), '--codex-bin', FAKE_CODEX, '--run-dir', str(self.run_dir),
                         '--scope', 'branch', '--base', 'main', '--profile', 'fast', '--quiet'])
        self.assertEqual(code, EXIT_AGENTS_NEEDED)


if __name__ == '__main__':
    unittest.main()
