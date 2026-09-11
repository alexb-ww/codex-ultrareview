from __future__ import annotations

from pathlib import Path
import unittest

from tests.helpers import TempRepo
from ultrareview.config import (EffortConfig, RunConfig, default_run_dir,
                                ensure_run_dir_outside_repo)
from ultrareview.errors import ConfigError
from ultrareview.scope import Limits, ScopeSpec


def config(**overrides) -> RunConfig:
    base = dict(repo=Path('/tmp/repo'), scope=ScopeSpec(kind='changes'), run_dir=Path('/tmp/run'))
    return RunConfig(**{**base, **overrides})


class EffortConfigTests(unittest.TestCase):
    def test_role_lookup_falls_back_to_default(self) -> None:
        effort = EffortConfig(default='high', per_role=(('verifier', 'xhigh'),))
        self.assertEqual(effort.for_role('verifier'), 'xhigh')
        self.assertEqual(effort.for_role('finder'), 'high')
        self.assertIsNone(EffortConfig().for_role('finder'))

    def test_invalid_values_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            EffortConfig(default='ultra').validate()
        with self.assertRaises(ConfigError):
            EffortConfig(per_role=(('painter', 'high'),)).validate()
        with self.assertRaises(ConfigError):
            EffortConfig(model='-m').validate()


class RunConfigTests(unittest.TestCase):
    def test_defaults_validate(self) -> None:
        self.assertEqual(config().validate().profile, 'deep')

    def test_invalid_profile_repro_lang(self) -> None:
        for overrides in ({'profile': 'ultra'}, {'repro': 'maybe'}, {'lang': 'de'}, {'jobs': 0},
                          {'agent_timeout': 5}, {'votes': 0}, {'max_findings': 0},
                          {'limits': Limits(max_files=0)}, {'codex_bin': ' '}):
            with self.assertRaises(ConfigError, msg=str(overrides)):
                config(**overrides).validate()

    def test_default_run_dir_is_under_cache_and_named_after_repo(self) -> None:
        path = default_run_dir(Path('/some/where/My Repo'), now=0)
        self.assertEqual(path.parent, Path.home() / '.cache' / 'ultrareview' / 'runs')
        self.assertTrue(path.name.startswith('19700101T000000Z-'))
        self.assertTrue(path.name.endswith('My-Repo'))

    def test_run_dir_inside_repo_rejected(self) -> None:
        repo = TempRepo()
        try:
            with self.assertRaises(ConfigError):
                ensure_run_dir_outside_repo(repo.path / 'reviews', repo.path)
            self.assertEqual(ensure_run_dir_outside_repo(repo.parent / 'run', repo.path), repo.parent / 'run')
        finally:
            repo.cleanup()


if __name__ == '__main__':
    unittest.main()
