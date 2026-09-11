from __future__ import annotations

import unittest

from tests.helpers import seeded_repo
from ultrareview import briefs
from ultrareview.angles import angle_by_id
from ultrareview.prompts import forbidden_words, placeholders
from ultrareview.scope import ScopeSpec, resolve_scope
from ultrareview.snapshot import take_snapshot


class BriefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()
        self.repo.git('checkout', '-q', '-b', 'feature')
        self.repo.write('src/app.py', 'def add(a, b):\n    return a - b\n')
        self.repo.commit('bug')
        self.repo.write('.env', 'TOKEN=abc\n')
        self.scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        self.snapshot = take_snapshot(self.repo.path, self.scope)
        self.ctx = briefs.BriefContext(run_id='run-1', repo_root=str(self.repo.path), scope=self.scope,
                                       snapshot=self.snapshot, diff_path='/run/diff.patch', preamble='PREAMBLE-TEXT')

    def tearDown(self) -> None:
        self.repo.cleanup()

    def assert_clean(self, text: str) -> None:
        self.assertEqual(placeholders(text), ())
        self.assertEqual(forbidden_words(text), ())

    def test_finder_brief_contains_markers_diff_and_angle(self) -> None:
        text = briefs.finder_brief(self.ctx, 'finder-a', angle_by_id('a-line-scan'), None)
        self.assert_clean(text)
        self.assertIn('ROLE: finder', text)
        self.assertIn('AGENT_ID: finder-a', text)
        self.assertIn('MARKER: a-line-scan', text)
        self.assertIn('PREAMBLE-TEXT', text)
        self.assertIn('return a - b', text)
        self.assertIn('Line-by-line', text)
        self.assertIn('[redacted: do not read]', text)
        self.assertNotIn('TOKEN=abc', text)

    def test_user_note_is_added_as_priority_not_scope(self) -> None:
        noted = briefs.BriefContext(**{**self.ctx.__dict__, 'note': '  check the  auth   changes '})
        text = briefs.finder_brief(noted, 'finder-a', angle_by_id('a-line-scan'), None)
        self.assert_clean(text)
        self.assertIn('User note (a priority to weigh', text)
        self.assertIn('check the auth changes', text)
        self.assertNotIn('User note', briefs.finder_brief(self.ctx, 'finder-a', angle_by_id('a-line-scan'), None))

    def test_large_diff_is_referenced_not_inlined(self) -> None:
        small = briefs.BriefContext(**{**self.ctx.__dict__, 'inline_diff_limit': 10})
        text = briefs.finder_brief(small, 'finder-a', angle_by_id('c-cross-file'), None)
        self.assertIn('/run/diff.patch', text)
        self.assertNotIn('```diff', text)

    def test_shard_replaces_target_list(self) -> None:
        text = briefs.finder_brief(self.ctx, 'finder-a', angle_by_id('security'), None, shard=['src/app.py'])
        self.assertIn('Your shard', text)

    def test_map_section_rendering(self) -> None:
        section = briefs.map_section({'modules': [{'name': 'core', 'paths': ['src'], 'purpose': 'math'}],
                                      'entry_points': ['cli'], 'test_stack': 'unittest'})
        self.assertIn('core: math (src)', section)
        self.assertIn('Test stack: unittest', section)
        self.assertEqual(briefs.map_section(None), '')

    def test_other_roles_render(self) -> None:
        cand = {'id': 'C1', 'file': 'src/app.py', 'line_start': 2, 'line_end': 2, 'summary': 's',
                'failure_scenario': 'f', 'verdict': 'CONFIRMED'}
        for text in (briefs.verifier_brief(self.ctx, 'v1', cand, 'C1'),
                     briefs.sweep_brief(self.ctx, 's1', [cand]),
                     briefs.sweep_brief(self.ctx, 's2', []),
                     briefs.triage_brief(self.ctx, 't1', [cand]),
                     briefs.mapper_brief(self.ctx, 'm1'),
                     briefs.reproducer_brief(self.ctx, 'r1', cand, '/tmp/wt', 'run pytest', 'UR-1'),
                     briefs.adjudicator_brief(self.ctx, 'adj', [cand], {'reviewed': 1}, ['limit one'])):
            self.assert_clean(text)
        self.assertIn('git show', briefs.base_info(self.ctx))

    def test_repo_scope_diff_section(self) -> None:
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='repo'))
        ctx = briefs.BriefContext(run_id='r', repo_root=str(self.repo.path), scope=scope,
                                  snapshot=take_snapshot(self.repo.path, scope), diff_path='/x')
        self.assertIn('whole-repository', briefs.diff_section(ctx))
        self.assertIn('no base revision', briefs.base_info(ctx))


if __name__ == '__main__':
    unittest.main()
