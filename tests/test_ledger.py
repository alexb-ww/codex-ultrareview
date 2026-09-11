from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from ultrareview import ledger
from ultrareview.runner import AgentResult, CommandRecord


def result(agent_id: str, *commands: str) -> AgentResult:
    return AgentResult(agent_id=agent_id, role='verifier', status='completed', output={}, error=None,
                       thread_id='t', commands=tuple(CommandRecord(c, 0) for c in commands), usage={},
                       duration_s=1.0, events_path='', output_path='', brief_path='', attempts=1)


class NormaliseTests(unittest.TestCase):
    def test_strips_shell_wrapper_and_quotes(self) -> None:
        self.assertEqual(ledger.normalise_command("/bin/zsh -lc 'cat  calc.py'"), 'cat calc.py')
        self.assertEqual(ledger.normalise_command('/bin/bash -lc "pytest -q tests"'), 'pytest -q tests')
        self.assertEqual(ledger.normalise_command('sh -c "go test ./..."'), 'go test ./...')
        self.assertEqual(ledger.normalise_command('  python3 -m unittest  '), 'python3 -m unittest')


class CommandRanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = ledger.build_ledger([result('v1', "/bin/zsh -lc 'cd /repo && pytest -q tests/test_x.py'"),
                                           result('v2', "/bin/zsh -lc 'cat a.py'")])

    def test_exact_and_substring_matches(self) -> None:
        self.assertTrue(ledger.command_ran(self.ledger, 'v1', 'pytest -q tests/test_x.py'))
        self.assertTrue(ledger.command_ran(self.ledger, 'v1', 'cd /repo && pytest -q tests/test_x.py'))
        self.assertTrue(ledger.command_ran(self.ledger, 'v2', "/bin/zsh -lc 'cat a.py'"))

    def test_other_agents_commands_do_not_count(self) -> None:
        self.assertFalse(ledger.command_ran(self.ledger, 'v2', 'pytest -q tests/test_x.py'))
        self.assertFalse(ledger.command_ran(self.ledger, 'v1', 'go test ./...'))
        self.assertFalse(ledger.command_ran(self.ledger, 'v1', 'ls'))

    def test_unauthenticated_list(self) -> None:
        missing = ledger.unauthenticated_commands(self.ledger, 'v1', ['pytest -q tests/test_x.py', 'make test'])
        self.assertEqual(missing, ('make test',))

    def test_summary_and_write(self) -> None:
        tmp = Path(tempfile.mkdtemp()).resolve()
        try:
            path = tmp / 'nested' / 'ledger.jsonl'
            ledger.write_ledger(self.ledger, path)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertEqual(ledger.ledger_summary(self.ledger), {'v1': 1, 'v2': 1})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class QuoteTests(unittest.TestCase):
    LINES = ('def add(a, b):', '    return a - b', '', 'x = 1  # trailing')

    def test_whitespace_insensitive_match(self) -> None:
        self.assertTrue(ledger.quote_matches(self.LINES, 2, 'return a - b'))
        self.assertTrue(ledger.quote_matches(self.LINES, 2, '    return   a - b   '))
        self.assertTrue(ledger.quote_matches(self.LINES, 4, 'x = 1  # trailing'))

    def test_wrong_line_or_text_rejected(self) -> None:
        self.assertFalse(ledger.quote_matches(self.LINES, 1, 'return a - b'))
        self.assertFalse(ledger.quote_matches(self.LINES, 9, 'return a - b'))
        self.assertFalse(ledger.quote_matches(self.LINES, 0, 'def add(a, b):'))
        self.assertFalse(ledger.quote_matches(self.LINES, 3, ''))
        self.assertFalse(ledger.quote_matches(self.LINES, 2, 'return a + b'))


if __name__ == '__main__':
    unittest.main()
