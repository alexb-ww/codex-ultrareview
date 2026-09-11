from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from ultrareview import codex_config


class CodexConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix='ur-codexhome-')).resolve()
        self.config = self.home / 'config.toml'

    def tearDown(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)

    def test_reads_top_level_keys_and_stops_at_tables(self) -> None:
        self.config.write_text('model = "gpt-6-astra"\nmodel_reasoning_effort = "max"  # note\n'
                               '[projects."/x"]\nmodel = "other"\n', encoding='utf-8')
        values = codex_config.read_top_level(self.config)
        self.assertEqual(values, {'model': 'gpt-6-astra', 'model_reasoning_effort': 'max'})

    def test_missing_file_and_env_home(self) -> None:
        self.assertEqual(codex_config.read_top_level(self.home / 'nope.toml'), {})
        with mock.patch.dict(os.environ, {'CODEX_HOME': str(self.home)}):
            self.assertEqual(codex_config.codex_home(), self.home)

    def test_effective_model_precedence(self) -> None:
        self.config.write_text("model = 'gpt-6-astra'\n", encoding='utf-8')
        model, effort = codex_config.effective_model(None, None, self.config)
        self.assertEqual(model, 'gpt-6-astra (config.toml)')
        self.assertTrue(effort.startswith('unknown'))
        model, effort = codex_config.effective_model('gpt-5.4', 'high', self.config)
        self.assertEqual((model, effort), ('gpt-5.4 (flag)', 'high (flag)'))


if __name__ == '__main__':
    unittest.main()
