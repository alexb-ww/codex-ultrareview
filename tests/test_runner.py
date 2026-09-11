from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import ROOT
from ultrareview import runner, schemas
from ultrareview.errors import RunnerError

FAKE_CODEX = ROOT / 'tests' / 'fake_codex' / 'codex'
PROBE_EVENTS = [
    {"type": "thread.started", "thread_id": "01a090b9-15c9-7511-bc68-9c2540759837"},
    {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "I will read calc.py."}},
    {"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "/bin/zsh -lc 'cat calc.py'", "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": "/bin/zsh -lc 'cat calc.py'", "aggregated_output": "def add(a, b):\n    return a - b\n", "exit_code": 0, "status": "completed"}},
    {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": "{\"findings\": []}"}},
    {"type": "something.new", "payload": 1},
    {"type": "turn.completed", "usage": {"input_tokens": 30116, "cached_input_tokens": 14848, "cache_write_input_tokens": 0, "output_tokens": 123, "reasoning_output_tokens": 0}},
]


def brief_for(role: str, marker: str = 'm1', agent_id: str = 'agent-1') -> str:
    return f'ROLE: {role}\nAGENT_ID: {agent_id}\nMARKER: {marker}\n\nDo the thing.\n'


class ParseEventsTests(unittest.TestCase):
    def test_parses_probe_stream(self) -> None:
        parsed = runner.parse_events(json.dumps(e) for e in PROBE_EVENTS)
        self.assertEqual(parsed.thread_id, '01a090b9-15c9-7511-bc68-9c2540759837')
        self.assertEqual(len(parsed.commands), 1)
        self.assertEqual(parsed.commands[0].command, "/bin/zsh -lc 'cat calc.py'")
        self.assertEqual(parsed.commands[0].exit_code, 0)
        self.assertIn('return a - b', parsed.commands[0].output_excerpt)
        self.assertEqual(parsed.usage['input_tokens'], 30116)
        self.assertEqual(parsed.unknown_events, 1)
        self.assertEqual(parsed.messages[-1], '{"findings": []}')

    def test_tolerates_garbage_lines(self) -> None:
        parsed = runner.parse_events(['not json', '', '{"type": "turn.started"}'])
        self.assertEqual(parsed.unknown_events, 1)
        self.assertEqual(parsed.commands, ())


class ValidateOutputTests(unittest.TestCase):
    def test_detects_missing_and_wrong_enum(self) -> None:
        schema = schemas.verifier_schema()
        good = {'verdict': 'CONFIRMED', 'severity': 'P1', 'origin': 'introduced', 'trigger': 't', 'reasoning': 'r',
                'counterevidence_checked': [], 'quotes': [{'path': 'a', 'line': 1, 'text': 'x'}],
                'what_would_confirm': '', 'fix': 'f', 'regression_test': 'g', 'commands_run': []}
        self.assertEqual(runner.validate_output(schema, good), ())
        bad = dict(good, verdict='MAYBE')
        self.assertTrue(any('verdict' in e for e in runner.validate_output(schema, bad)))
        missing = {k: v for k, v in good.items() if k != 'fix'}
        self.assertTrue(any('fix' in e for e in runner.validate_output(schema, missing)))
        self.assertTrue(runner.validate_output(schema, dict(good, quotes=[{'path': 'a', 'line': 'one', 'text': 'x'}])))


class BuildArgvTests(unittest.TestCase):
    def spec(self, **overrides) -> runner.AgentSpec:
        base = dict(agent_id='a1', role='finder', brief='x', schema=schemas.finder_schema(), cwd=Path('/repo'),
                    run_dir=Path('/run'))
        return runner.AgentSpec(**{**base, **overrides})

    def test_default_argv(self) -> None:
        argv = runner.build_argv(self.spec(), Path('/run/s.json'), Path('/run/o.json'))
        self.assertEqual(argv[:3], ('codex', 'exec', '--skip-git-repo-check'))
        self.assertIn('--json', argv)
        self.assertIn('--ephemeral', argv)
        self.assertEqual(argv[argv.index('-s') + 1], 'read-only')
        self.assertEqual(argv[-1], '-')
        self.assertNotIn('-m', argv)

    def test_effort_model_and_keep_session(self) -> None:
        argv = runner.build_argv(self.spec(effort='xhigh', model='gpt-6-astra', keep_session=True,
                                           sandbox='workspace-write'), Path('/s'), Path('/o'))
        self.assertNotIn('--ephemeral', argv)
        self.assertIn('model_reasoning_effort="xhigh"', argv)
        self.assertEqual(argv[argv.index('-m') + 1], 'gpt-6-astra')
        self.assertEqual(argv[argv.index('-s') + 1], 'workspace-write')

    def test_unknown_sandbox_rejected(self) -> None:
        with self.assertRaises(RunnerError):
            runner.build_argv(self.spec(sandbox='none'), Path('/s'), Path('/o'))


class RunAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix='ur-runner-')).resolve()
        self.run_dir = self.tmp / 'run'
        self.scenario_path = self.tmp / 'scenario.json'
        self.log_path = self.tmp / 'log.jsonl'
        self.env = mock.patch.dict(os.environ, {'FAKE_CODEX_SCENARIO': str(self.scenario_path),
                                                'FAKE_CODEX_LOG': str(self.log_path)})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scenario(self, payload: dict) -> None:
        self.scenario_path.write_text(json.dumps(payload), encoding='utf-8')

    def spec(self, role: str = 'finder', **overrides) -> runner.AgentSpec:
        base = dict(agent_id=f'{role}-1', role=role, brief=brief_for(role), schema=schemas.schema_for(role),
                    cwd=self.tmp, run_dir=self.run_dir, codex_bin=str(FAKE_CODEX), timeout=20)
        return runner.AgentSpec(**{**base, **overrides})

    def test_happy_path_collects_output_and_commands(self) -> None:
        output = {'candidates': [], 'files_read': ['a.py'], 'coverage_note': 'ok'}
        self.scenario({'finder:m1': {'output': output, 'commands': [{'command': 'cat a.py', 'exit_code': 0, 'output': 'x'}]}})
        result = runner.run_agent(self.spec(), retries=1)
        self.assertTrue(result.completed)
        self.assertEqual(result.output, output)
        self.assertEqual(result.commands[0].command, "/bin/zsh -lc \"cat a.py\"")
        self.assertEqual(result.usage['input_tokens'], 1000)
        self.assertEqual(result.attempts, 1)
        self.assertTrue(Path(result.events_path).exists())
        logged = json.loads(self.log_path.read_text().splitlines()[0])
        self.assertIn('--ephemeral', logged['flags'])
        self.assertEqual(logged['sandbox'], 'read-only')

    def test_invalid_json_is_retried_then_failed(self) -> None:
        self.scenario({'finder': {'mode': 'invalid_json'}})
        result = runner.run_agent(self.spec(), retries=1)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.attempts, 2)
        self.assertIn('not valid JSON', result.error or '')

    def test_schema_mismatch_fails(self) -> None:
        self.scenario({'finder': {'output': {'candidates': 'nope'}}})
        result = runner.run_agent(self.spec(), retries=0)
        self.assertEqual(result.status, 'failed')
        self.assertIn('schema', result.error or '')

    def test_crash_reports_stderr_hint(self) -> None:
        self.scenario({'finder': {'mode': 'crash'}})
        result = runner.run_agent(self.spec(), retries=0)
        self.assertEqual(result.status, 'failed')
        self.assertIn('crashed on purpose', result.error or '')

    def test_timeout_terminates_process(self) -> None:
        self.scenario({'finder': {'sleep': 5}})
        result = runner.run_agent(self.spec(timeout=1), retries=1)
        self.assertEqual(result.status, 'timeout')
        self.assertEqual(result.attempts, 1)
        self.assertLess(result.duration_s, 4)

    def test_missing_binary_raises(self) -> None:
        with self.assertRaises(RunnerError):
            runner.run_agent(self.spec(codex_bin=str(self.tmp / 'no-such-codex')), retries=0)


if __name__ == '__main__':
    unittest.main()
