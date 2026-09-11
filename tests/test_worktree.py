from __future__ import annotations

import unittest
from unittest import mock

from tests.helpers import seeded_repo
from ultrareview.errors import GitError
from ultrareview.scope import ScopeSpec, resolve_scope
from ultrareview.worktree import capture_state_patch, create_worktree_copy, remove_worktree_copy


class WorktreeCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()
        self.repo.git('checkout', '-q', '-b', 'feature')
        self.repo.write('src/app.py', 'def add(a, b):\n    return a - b\n')
        self.repo.commit('bug')
        self.repo.write('src/util.py', 'def clamp(x, lo, hi):\n    return min(x, hi)\n')
        self.repo.write('src/new_untracked.py', 'NEW = True\n')
        self.repo.write('.env', 'SECRET=1\n')

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_copy_matches_reviewed_state(self) -> None:
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        copy = create_worktree_copy(self.repo.path, scope, self.repo.parent / 'wt')
        try:
            self.assertTrue(copy.applied_diff)
            self.assertEqual((copy.path / 'src/app.py').read_text(), 'def add(a, b):\n    return a - b\n')
            self.assertEqual((copy.path / 'src/util.py').read_text(), 'def clamp(x, lo, hi):\n    return min(x, hi)\n')
            self.assertEqual((copy.path / 'src/new_untracked.py').read_text(), 'NEW = True\n')
            self.assertFalse((copy.path / '.env').exists(), 'redacted files are not copied')
            self.assertIn('src/new_untracked.py', copy.copied_untracked)
            self.assertEqual(len(self.repo.git('status', '--porcelain').splitlines()), 3,
                             'the original checkout is untouched')
        finally:
            remove_worktree_copy(copy)
        self.assertFalse(copy.path.exists())
        self.assertNotIn('wt', self.repo.git('worktree', 'list'))

    def test_captured_patch_wins_over_later_edits(self) -> None:
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        patch_path = self.repo.parent / 'state.patch'
        self.assertTrue(capture_state_patch(self.repo.path, scope, patch_path))
        self.repo.write('src/util.py', 'def clamp(x, lo, hi):\n    return EDITED_LATER\n')
        copy = create_worktree_copy(self.repo.path, scope, self.repo.parent / 'wt-patch', patch_path=patch_path)
        try:
            self.assertEqual((copy.path / 'src/util.py').read_text(), 'def clamp(x, lo, hi):\n    return min(x, hi)\n')
        finally:
            remove_worktree_copy(copy)
        head = self.repo.git('rev-parse', 'HEAD')
        commit_scope = resolve_scope(self.repo.path, ScopeSpec(kind='commit', commit=head))
        self.assertFalse(capture_state_patch(self.repo.path, commit_scope, self.repo.parent / 'empty.patch'))

    def test_plain_copy_fallback_when_worktree_add_is_refused(self) -> None:
        from ultrareview import worktree as wt
        from ultrareview.errors import GitError as _GitError
        real = wt.run_git

        def refusing(repo, *args, **kwargs):
            if args[:2] == ('worktree', 'add'):
                raise _GitError("fatal: could not create leading directories of '.git/worktrees/x': Operation not permitted")
            return real(repo, *args, **kwargs)

        scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        with mock.patch.object(wt, 'run_git', refusing):
            copy = create_worktree_copy(self.repo.path, scope, self.repo.parent / 'wt-plain')
        try:
            self.assertEqual(copy.kind, 'plain')
            self.assertFalse((copy.path / '.git').exists())
            self.assertTrue(copy.applied_diff)
            self.assertEqual((copy.path / 'src/util.py').read_text(), 'def clamp(x, lo, hi):\n    return min(x, hi)\n')
            self.assertEqual((copy.path / 'src/new_untracked.py').read_text(), 'NEW = True\n')
            self.assertTrue(any('plain copy' in n for n in copy.notes))
            reused = create_worktree_copy(self.repo.path, scope, copy.path, reuse=True)
            self.assertEqual(reused.kind, 'plain')
        finally:
            remove_worktree_copy(copy)
        self.assertFalse(copy.path.exists())

    def test_commit_scope_copy_is_that_commit(self) -> None:
        head = self.repo.git('rev-parse', 'HEAD')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='commit', commit=head))
        copy = create_worktree_copy(self.repo.path, scope, self.repo.parent / 'wt-commit')
        try:
            self.assertFalse(copy.applied_diff)
            self.assertIn('return a - b', (copy.path / 'src/app.py').read_text())
            self.assertIn('raise ValueError', (copy.path / 'src/util.py').read_text())
        finally:
            remove_worktree_copy(copy)

    def test_existing_destination_rejected(self) -> None:
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='changes'))
        (self.repo.parent / 'taken').mkdir()
        with self.assertRaises(GitError):
            create_worktree_copy(self.repo.path, scope, self.repo.parent / 'taken')


if __name__ == '__main__':
    unittest.main()
