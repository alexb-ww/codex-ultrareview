from __future__ import annotations

import os
import unittest
from unittest import mock

from tests.helpers import TempRepo, seeded_repo
from ultrareview import gitio
from ultrareview.errors import GitError


class GitIoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_repo_root_resolves_real_path(self) -> None:
        nested = self.repo.path / 'src'
        self.assertEqual(gitio.repo_root(nested), self.repo.path)

    def test_repo_root_outside_git_raises(self) -> None:
        with self.assertRaises(GitError):
            gitio.repo_root(self.repo.parent)

    def test_rev_parse_rejects_option_like_refs(self) -> None:
        self.assertIsNone(gitio.rev_parse(self.repo.path, '--output=/tmp/x'))
        self.assertIsNone(gitio.rev_parse(self.repo.path, 'no-such-branch'))
        self.assertEqual(gitio.rev_parse(self.repo.path, 'HEAD'), self.repo.git('rev-parse', 'HEAD'))

    def test_inherited_git_environment_is_scrubbed(self) -> None:
        with mock.patch.dict(os.environ, {'GIT_DIR': '/does-not-exist', 'GIT_INDEX_FILE': '/missing'}):
            self.assertEqual(gitio.git_text(self.repo.path, 'rev-parse', '--show-toplevel'),
                             str(self.repo.path))

    def test_clean_filter_does_not_run_during_diff(self) -> None:
        marker = self.repo.parent / 'filter-ran'
        self.repo.write('.gitattributes', 'src/app.py filter=probe\n')
        self.repo.commit('attributes')
        self.repo.git('config', 'filter.probe.clean', f'touch "{marker}"; cat')
        self.repo.git('config', 'filter.probe.required', 'true')
        self.repo.write('src/app.py', 'changed\n')
        out = gitio.run_git(self.repo.path, 'diff', '--name-only', '-z').decode()
        self.assertIn('src/app.py', out)
        self.assertFalse(marker.exists(), 'diff must not execute a configured clean filter')

    def test_process_filter_does_not_run_during_show(self) -> None:
        marker = self.repo.parent / 'process-ran'
        self.repo.write('.gitattributes', 'src/app.py filter=probe\n')
        self.repo.commit('attributes')
        self.repo.git('config', 'filter.probe.process', f'touch "{marker}"; exit 1')
        self.repo.git('config', 'filter.probe.required', 'true')
        text = gitio.git_text(self.repo.path, 'show', 'HEAD:src/app.py')
        self.assertIn('def add', text)
        self.assertFalse(marker.exists())

    def test_allow_fail_returns_empty_bytes(self) -> None:
        self.assertEqual(gitio.run_git(self.repo.path, 'rev-parse', '--verify', 'nope', allow_fail=True), b'')
        with self.assertRaises(GitError):
            gitio.run_git(self.repo.path, 'rev-parse', '--verify', 'nope')

    def test_decode_nul_handles_unicode_and_spaces(self) -> None:
        raw = 'a b.py\0dir/ü.txt\0'.encode('utf-8')
        self.assertEqual(gitio.decode_nul(raw), ('a b.py', 'dir/ü.txt'))


class TempRepoSmokeTests(unittest.TestCase):
    def test_temp_repo_is_resolved(self) -> None:
        repo = TempRepo()
        try:
            self.assertEqual(repo.path, repo.path.resolve())
        finally:
            repo.cleanup()


if __name__ == '__main__':
    unittest.main()
