"""Regression tests for the defects found by the independent Codex review of 0.2.0."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import ROOT, seeded_repo
from tests.test_gate_report import cand, finding, result, verification
from tests.test_pipeline import FAKE_CODEX, happy_scenario
from ultrareview import briefs, gate as gate_mod, ledger as ledger_mod
from ultrareview.angles import angle_by_id
from ultrareview.config import RunConfig
from ultrareview.exclusions import is_secret_like
from ultrareview.models import Quote, Reproduction
from ultrareview.phases.sweep import attach_to_existing
from ultrareview.pipeline import EXIT_AGENTS_NEEDED, guard_marker_path, run_step
from ultrareview.runner import AgentResult, CommandRecord
from ultrareview.scope import Limits, ScopeSpec, check_limits, resolve_scope
from ultrareview.snapshot import SnapshotReader, detect_drift, take_snapshot
from ultrareview.state import ReviewState
from ultrareview.worktree import create_worktree_copy, remove_worktree_copy
import install


class GitFilterTests(unittest.TestCase):
    def test_snapshot_status_does_not_run_clean_filter(self) -> None:
        repo = seeded_repo()
        try:
            marker = repo.parent / 'filter-ran'
            repo.write('.gitattributes', 'src/app.py filter=probe\n')
            repo.commit('attributes')
            repo.git('config', 'filter.probe.clean', f'touch "{marker}"; cat')
            repo.git('config', 'filter.probe.required', 'true')
            repo.write('src/app.py', 'def add(a, b):\n    return a - b\n')
            scope = resolve_scope(repo.path, ScopeSpec(kind='changes'))
            snapshot = take_snapshot(repo.path, scope)
            detect_drift(snapshot, repo.path)
            self.assertFalse(marker.exists(), 'status/diff during snapshot must not execute a clean filter')
        finally:
            repo.cleanup()


class RedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()

    def tearDown(self) -> None:
        self.repo.cleanup()

    def brief_for(self, scope) -> str:
        snapshot = take_snapshot(self.repo.path, scope)
        ctx = briefs.BriefContext(run_id='r', repo_root=str(self.repo.path), scope=scope, snapshot=snapshot, diff_path='/x')
        return briefs.finder_brief(ctx, 'finder-a', angle_by_id('a-line-scan'), None)

    def test_staged_secret_is_not_inlined(self) -> None:
        self.repo.write('.env', 'BASE=1\n')
        self.repo.commit('env')
        self.repo.write('.env', 'STAGED_FAKE_SECRET=2\n')
        self.repo.git('add', '.env')
        self.repo.write('.env', 'WORKTREE_FAKE_SECRET=3\n')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='changes'))
        text = self.brief_for(scope)
        self.assertNotIn('STAGED_FAKE_SECRET', text)
        self.assertNotIn('WORKTREE_FAKE_SECRET', text)
        self.assertIn('redacted', text)

    def test_wildcard_filename_does_not_reintroduce_redacted_file(self) -> None:
        self.repo.write('config/*', 'star\n')
        self.repo.write('config/.env', 'PATHSPEC_FAKE_SECRET=1\n')
        self.repo.commit('files')
        self.repo.write('config/*', 'star changed\n')
        self.repo.write('config/.env', 'PATHSPEC_FAKE_SECRET=2\n')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='changes'))
        self.assertNotIn('PATHSPEC_FAKE_SECRET', scope.diff_text)
        self.assertIn('star changed', scope.diff_text)

    def test_envrc_is_secret_like(self) -> None:
        for name in ('.envrc', '.env.local', 'deploy/.env', '.environment'):
            self.assertTrue(is_secret_like(name), name)
        self.assertFalse(is_secret_like('environment.py'))


class SymlinkTests(unittest.TestCase):
    def test_snapshot_does_not_read_through_directory_symlink(self) -> None:
        repo = seeded_repo()
        try:
            outside = repo.parent / 'outside' / 'src'
            outside.mkdir(parents=True)
            (outside / 'app.py').write_text('OUTSIDE_FAKE_SECRET = 1\n')
            shutil.rmtree(repo.path / 'src')
            (repo.path / 'src').symlink_to(repo.parent / 'outside' / 'src', target_is_directory=True)
            scope = resolve_scope(repo.path, ScopeSpec(kind='repo'))
            snapshot = take_snapshot(repo.path, scope)
            record = {(r.path, r.version): r for r in snapshot.files}.get(('src/app.py', 'worktree'))
            self.assertIsNotNone(record)
            self.assertNotEqual(record.kind, 'file')
            reader = SnapshotReader(repo.path, snapshot)
            self.assertEqual(reader.read_lines('src/app.py', 'worktree'), ())
            self.assertFalse(gate_mod.quote_ok(reader, Quote('src/app.py', 1, 'OUTSIDE_FAKE_SECRET = 1')))
        finally:
            repo.cleanup()

    def test_patch_is_applied_through_stdin(self) -> None:
        repo = seeded_repo()
        try:
            victim = repo.parent / 'victim.txt'
            victim.write_text('untouched\n')
            (repo.path / '.ultrareview-state.patch').symlink_to(victim)
            repo.commit('tracked symlink with the old temp-file name')
            repo.write('src/app.py', 'def add(a, b):\n    return a - b\n')
            scope = resolve_scope(repo.path, ScopeSpec(kind='changes'))
            copy = create_worktree_copy(repo.path, scope, repo.parent / 'wt')
            try:
                self.assertTrue(copy.applied_diff)
                self.assertEqual(victim.read_text(), 'untouched\n')
            finally:
                remove_worktree_copy(copy)
        finally:
            repo.cleanup()


class StrictEvidenceTests(unittest.TestCase):
    def test_printed_text_is_not_an_executed_command(self) -> None:
        ledger = ledger_mod.build_ledger([result('v1', 'verifier', "printf '%s\\n' 'python3 repro.py'",
                                                'cd /repo && pytest -q tests/test_x.py')])
        self.assertFalse(ledger_mod.command_ran(ledger, 'v1', 'python3 repro.py'))
        self.assertTrue(ledger_mod.command_ran(ledger, 'v1', 'pytest -q tests/test_x.py'))
        self.assertTrue(ledger_mod.command_ran(ledger, 'v1', "printf '%s\\n' 'python3 repro.py'"))
        self.assertEqual(ledger_mod.exit_codes_for(ledger, 'v1', 'pytest -q tests/test_x.py'), (0,))

    def test_blank_or_short_lines_never_match(self) -> None:
        lines = ('def f():', '', '    x = 1', '    return compute_total(items, discount) - shipping_fee  # noqa')
        self.assertFalse(ledger_mod.quote_matches(lines, 2, 'return fabricated_failure'))
        self.assertFalse(ledger_mod.quote_matches(lines, 3, 'x'))
        self.assertTrue(ledger_mod.quote_matches(lines, 4, 'return compute_total(items, discount) - shipping_fee'))
        self.assertFalse(ledger_mod.quote_matches(lines, 4, 'return compute_total(items, discount) + shipping_fee'))


class GateStrictnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()
        self.repo.git('checkout', '-q', '-b', 'feature')
        self.repo.write('src/app.py', 'def add(a, b):\n    return a - b\n')
        self.repo.commit('bug')
        self.scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        self.snapshot = take_snapshot(self.repo.path, self.scope)
        self.reader = SnapshotReader(self.repo.path, self.snapshot)

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_fabricated_refutation_cannot_bury_a_bug(self) -> None:
        ledger = ledger_mod.build_ledger([])
        refuted = finding('UR-1', cand('C1'), verification('v1', 'REFUTED', quotes=[Quote('src/app.py', 2, 'if guarded: return')]))
        gate = gate_mod.run_gate(ReviewState(findings=(refuted,), results=(result('f', 'finder'),)), ledger, self.reader, None)
        gated = gate.findings[0]
        self.assertEqual(gated.verdict, 'UNVERIFIED')
        self.assertTrue(any('set aside' in i for i in gated.evidence_issues))
        self.assertEqual(gate.status, 'partial')

    def test_evidence_issue_blocks_acceptance_even_with_reproduction(self) -> None:
        ledger = ledger_mod.build_ledger([result('r1', 'reproducer', 'pytest -q')])
        bad = finding('UR-1', cand('C1'), verification('v1', quotes=[Quote('src/app.py', 2, 'return a * b')]),
                      reproduction=Reproduction('r1', 'reproduced', 'pytest -q', '.', 1, 'F', 'e', 't', ''))
        gate = gate_mod.run_gate(ReviewState(findings=(bad,), results=(result('f', 'finder'),)), ledger, self.reader, None)
        self.assertFalse(gate.findings[0].accepted)
        self.assertEqual(gate.status, 'partial')

    def test_reproduction_exit_code_must_match_the_log(self) -> None:
        ledger = ledger_mod.build_ledger([result('r1', 'reproducer', 'pytest -q')])
        good_quote = Quote('src/app.py', 2, 'return a - b')
        mismatch = finding('UR-1', cand('C1'), verification('v1', quotes=[good_quote]),
                           reproduction=Reproduction('r1', 'reproduced', 'pytest -q', '.', 1, 'F', 'e', 't', '',
                                                     commands_run=('pytest -q',)))
        gated = gate_mod.apply_gate((mismatch,), ledger, self.reader)[0]
        self.assertEqual(gated.reproduction.result, 'unverified-evidence')
        self.assertTrue(any('exit code 1' in i for i in gated.evidence_issues))
        ok = finding('UR-2', cand('C2', line=1), verification('v2', quotes=[Quote('src/app.py', 1, 'def add(a, b):')]),
                     reproduction=Reproduction('r1', 'reproduced', 'pytest -q', '.', 0, 'F', 'e', 't', ''))
        self.assertEqual(gate_mod.apply_gate((ok,), ledger, self.reader)[0].evidence_issues, ())


class SweepAndScopeTests(unittest.TestCase):
    def test_sweep_does_not_attach_to_refuted_or_different_category(self) -> None:
        refuted = finding('UR-1', cand('C1', line=1), verification('v1', 'REFUTED'))
        kept = finding('UR-2', cand('C2', line=40), verification('v2', 'CONFIRMED'))
        other = replace(cand('C9', line=42, angle='security'), category='security')
        near_refuted = cand('C8', line=2)
        updated, fresh = attach_to_existing([near_refuted, other], [refuted, kept])
        self.assertEqual({c.id for c in fresh}, {'C8', 'C9'})
        self.assertEqual(len(updated[1].members), 1)

    def test_changes_scope_has_a_base_and_deleted_code_is_readable(self) -> None:
        repo = seeded_repo()
        try:
            repo.git('rm', '-q', 'src/util.py')
            scope = resolve_scope(repo.path, ScopeSpec(kind='changes'))
            self.assertEqual(scope.base_commit, repo.git('rev-parse', 'HEAD'))
            self.assertEqual(scope.diff_base, scope.base_commit)
            snapshot = take_snapshot(repo.path, scope)
            reader = SnapshotReader(repo.path, snapshot)
            self.assertTrue(gate_mod.quote_ok(reader, Quote('src/util.py', 1, 'def clamp(x, lo, hi):', version='base')))
            self.assertIn('git show', briefs.base_info(briefs.BriefContext('r', str(repo.path), scope, snapshot, '/x')))
        finally:
            repo.cleanup()

    def test_index_only_change_reaches_the_brief_and_the_limits(self) -> None:
        repo = seeded_repo()
        try:
            repo.write('src/app.py', 'x = 1\n' * 50)
            repo.git('add', 'src/app.py')
            repo.write('src/app.py', repo.git('show', 'HEAD:src/app.py') + '\n')
            scope = resolve_scope(repo.path, ScopeSpec(kind='changes'))
            self.assertGreaterEqual(scope.changed_lines, 50)
            self.assertIsNotNone(check_limits(scope, Limits(max_files=10, max_lines=10)))
            snapshot = take_snapshot(repo.path, scope)
            ctx = briefs.BriefContext('r', str(repo.path), scope, snapshot, '/x')
            text = briefs.diff_section(ctx)
            self.assertNotIn('(the diff is empty)', text)
            self.assertIn('x = 1', text)
        finally:
            repo.cleanup()


class InstalledHookTests(unittest.TestCase):
    def test_installed_hook_command_points_at_an_existing_script(self) -> None:
        home = Path(tempfile.mkdtemp(prefix='ur-home-')).resolve()
        try:
            install.install(home, ROOT, with_hooks=True)
            payload = json.loads((home / '.codex' / 'hooks.json').read_text())
            command = payload['hooks']['PreToolUse'][0]['hooks'][0]['command']
            script = command.split('"')[1].replace('$HOME', str(home))
            self.assertTrue(Path(script).is_file(), script)
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_step_creates_and_clears_the_guard_marker(self) -> None:
        repo = seeded_repo()
        repo.git('checkout', '-q', '-b', 'feature')
        repo.write('src/app.py', 'def add(a, b):\n    return a - b\n')
        repo.commit('bug')
        tmp = Path(tempfile.mkdtemp(prefix='ur-marker-')).resolve()
        scenario = tmp / 'scenario.json'
        scenario.write_text(json.dumps(happy_scenario()))
        marker = tmp / 'REVIEW_ACTIVE'
        env = {'FAKE_CODEX_SCENARIO': str(scenario), 'FAKE_CODEX_LOG': '', 'ULTRAREVIEW_ACTIVE': str(marker)}
        try:
            with mock.patch.dict(os.environ, env):
                self.assertEqual(guard_marker_path(), marker)
                config = RunConfig(repo=repo.path, scope=ScopeSpec(kind='branch', base='main'), run_dir=tmp / 'run',
                                   profile='fast', agent_timeout=30, codex_bin=FAKE_CODEX, retries=0, repro='off')
                outcome = run_step(config, emit=lambda _: None)
                self.assertEqual(outcome.exit_code, EXIT_AGENTS_NEEDED)
                self.assertTrue(marker.exists())
                self.assertIn(str(tmp / 'run'), marker.read_text())
        finally:
            repo.cleanup()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
