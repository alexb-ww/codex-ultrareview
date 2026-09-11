from __future__ import annotations

import unittest

from tests.helpers import TempRepo, seeded_repo
from ultrareview.errors import ScopeError
from ultrareview.scope import (Limits, ScopeSpec, check_limits, detect_default_base,
                               resolve_scope)


def target_map(scope):
    return {t.path: t for t in scope.targets}


class BranchScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()
        self.repo.git('checkout', '-q', '-b', 'feature')
        self.repo.write('src/app.py', 'def add(a, b):\n    return a - b\n\n\ndef sub(a, b):\n    return a - b\n')
        self.repo.commit('bug')
        self.repo.git('checkout', '-q', 'main')
        self.repo.write('README.md', '# demo\nmain moved on\n')
        self.repo.commit('main advances')
        self.repo.git('checkout', '-q', 'feature')

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_branch_diff_uses_merge_base_not_base_tip(self) -> None:
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        paths = target_map(scope)
        self.assertIn('src/app.py', paths)
        self.assertNotIn('README.md', paths, 'changes made on main after branching are not ours')
        self.assertEqual(scope.merge_base, self.repo.git('merge-base', 'HEAD', 'main'))
        self.assertEqual(scope.base_source, 'flag')
        self.assertIn('return a - b', scope.diff_text)
        self.assertEqual(scope.changed_files, 1)
        self.assertGreaterEqual(scope.changed_lines, 2)

    def test_branch_includes_uncommitted_and_untracked(self) -> None:
        self.repo.write('src/util.py', 'def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n')
        self.repo.write('src/new.py', 'VALUE = 1\n')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        paths = target_map(scope)
        self.assertEqual(paths['src/util.py'].status, 'modified')
        self.assertEqual(paths['src/new.py'].status, 'untracked')
        self.assertIn('VALUE = 1', scope.diff_text)

    def test_default_base_prefers_origin_head_then_main(self) -> None:
        self.assertEqual(detect_default_base(self.repo.path), ('main', 'main'))
        self.repo.git('update-ref', 'refs/remotes/origin/main', 'main')
        self.repo.git('symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main')
        self.assertEqual(detect_default_base(self.repo.path), ('origin/main', 'origin/HEAD'))

    def test_default_base_falls_back_to_master(self) -> None:
        self.repo.git('branch', '-m', 'main', 'master')
        self.assertEqual(detect_default_base(self.repo.path), ('master', 'master'))

    def test_missing_merge_base_is_an_error_not_repo_fallback(self) -> None:
        self.repo.git('checkout', '-q', '--orphan', 'orphan')
        self.repo.write('alone.txt', 'x\n')
        self.repo.commit('orphan root')
        with self.assertRaises(ScopeError) as ctx:
            resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        self.assertIn('--scope repo', str(ctx.exception))

    def test_unknown_base_is_an_error(self) -> None:
        with self.assertRaises(ScopeError):
            resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='does-not-exist'))

    def test_empty_branch_diff_is_reported_empty(self) -> None:
        self.repo.git('checkout', '-q', 'main')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        self.assertTrue(scope.is_empty)
        self.assertEqual(scope.targets, ())

    def test_deleted_file_is_a_base_only_target(self) -> None:
        self.repo.git('rm', '-q', 'src/util.py')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        target = target_map(scope)['src/util.py']
        self.assertEqual(target.status, 'deleted')
        self.assertEqual(target.versions, ('base',))


class ChangesScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_clean_tree_is_empty(self) -> None:
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='changes'))
        self.assertTrue(scope.is_empty)

    def test_staged_then_reverted_worktree_keeps_index_version(self) -> None:
        self.repo.write('src/app.py', 'def add(a, b):\n    return a * b\n')
        self.repo.git('add', 'src/app.py')
        self.repo.write('src/app.py', self.repo.git('show', 'HEAD:src/app.py') + '\n')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='changes'))
        target = target_map(scope)['src/app.py']
        self.assertIn('index', target.versions)
        self.assertIn('return a * b', scope.index_diff_text)

    def test_untracked_binary_is_a_target_without_inline_diff(self) -> None:
        self.repo.write_bytes('blob.bin', b'\x00\x01\x02binary')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='changes'))
        self.assertEqual(target_map(scope)['blob.bin'].status, 'untracked')
        self.assertNotIn('\x00', scope.diff_text)


class CommitScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_commit_scope_reviews_that_commit_only(self) -> None:
        head = self.repo.git('rev-parse', 'HEAD')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='commit', commit=head))
        self.assertEqual(list(target_map(scope)), ['src/util.py'])
        self.assertIn('raise ValueError', scope.diff_text)
        self.assertEqual(scope.base_commit, self.repo.git('rev-parse', 'HEAD^'))

    def test_root_commit_is_supported(self) -> None:
        root = self.repo.git('rev-list', '--max-parents=0', 'HEAD')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='commit', commit=root))
        self.assertIn('README.md', target_map(scope))
        self.assertIn('src/app.py', target_map(scope))


class RepoScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()
        self.repo.write('src/views/drawer/vendor/menu.ts', 'export const x = 1;\n')
        self.repo.write('src/locales/en/auth.json', '{"a": "b"}\n')
        self.repo.write('vendor/modules.txt', '# go vendor\n')
        self.repo.write('vendor/pkg/lib.go', 'package pkg\n')
        self.repo.write('build/out.js', 'x\n')
        self.repo.commit('layout')

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_repo_scope_keeps_source_under_deep_vendor_dir(self) -> None:
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='repo'))
        paths = target_map(scope)
        self.assertIn('src/views/drawer/vendor/menu.ts', paths)
        self.assertIn('src/locales/en/auth.json', paths)
        self.assertNotIn('vendor/pkg/lib.go', paths)
        self.assertNotIn('build/out.js', paths)
        skipped = dict(scope.skipped)
        self.assertIn('vendor/pkg/lib.go', skipped)

    def test_repo_scope_paths_glob_narrows(self) -> None:
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='repo', paths=('src/locales/**',)))
        self.assertEqual(list(target_map(scope)), ['src/locales/en/auth.json'])
        self.assertTrue(any('src/locales/**' in note for note in scope.notes))

    def test_repo_scope_includes_untracked_source(self) -> None:
        self.repo.write('src/fresh.py', 'x = 1\n')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='repo'))
        self.assertEqual(target_map(scope)['src/fresh.py'].status, 'untracked')


class LimitTests(unittest.TestCase):
    def test_limits_report_largest_files(self) -> None:
        repo = seeded_repo()
        try:
            repo.git('checkout', '-q', '-b', 'big')
            repo.write('big.txt', '\n'.join(str(i) for i in range(300)) + '\n')
            repo.write('small.txt', 'one\n')
            repo.commit('big')
            scope = resolve_scope(repo.path, ScopeSpec(kind='branch', base='main'))
            violation = check_limits(scope, Limits(max_files=500, max_lines=100))
            self.assertIsNotNone(violation)
            assert violation is not None
            self.assertEqual(violation.top_files[0][0], 'big.txt')
            self.assertIsNone(check_limits(scope, Limits(max_files=500, max_lines=100000)))
            self.assertIsNotNone(check_limits(scope, Limits(max_files=1, max_lines=100000)))
        finally:
            repo.cleanup()


class ScopeSpecValidationTests(unittest.TestCase):
    def test_invalid_kind_rejected(self) -> None:
        with self.assertRaises(ScopeError):
            ScopeSpec(kind='everything').validate()

    def test_commit_kind_requires_commit(self) -> None:
        with self.assertRaises(ScopeError):
            ScopeSpec(kind='commit').validate()
        ScopeSpec(kind='commit', commit='abc').validate()

    def test_unborn_repository_blocks_diff_scopes(self) -> None:
        repo = TempRepo()
        try:
            repo.write('a.txt', 'x\n')
            with self.assertRaises(ScopeError):
                resolve_scope(repo.path, ScopeSpec(kind='branch', base='main'))
            scope = resolve_scope(repo.path, ScopeSpec(kind='changes'))
            self.assertIn('a.txt', target_map(scope))
        finally:
            repo.cleanup()


if __name__ == '__main__':
    unittest.main()
