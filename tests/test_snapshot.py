from __future__ import annotations

import json
import unittest

from tests.helpers import seeded_repo
from ultrareview.scope import ScopeSpec, resolve_scope
from ultrareview.snapshot import (SnapshotReader, detect_drift, snapshot_from_dict,
                                  snapshot_to_dict, take_snapshot)


def records(snapshot):
    return {(r.path, r.version): r for r in snapshot.files}


class SnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = seeded_repo()
        self.repo.git('checkout', '-q', '-b', 'feature')
        self.repo.write('src/app.py', 'def add(a, b):\n    return a - b\n')
        self.repo.commit('bug')
        self.repo.git('rm', '-q', 'README.md')
        self.repo.write_bytes('img.bin', b'\x00\x01\x02')
        self.repo.write('.env', 'SECRET=1\n')
        self.scope = resolve_scope(self.repo.path, ScopeSpec(kind='branch', base='main'))
        self.snapshot = take_snapshot(self.repo.path, self.scope)

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_records_cover_every_target_version(self) -> None:
        recs = records(self.snapshot)
        self.assertEqual(recs[('src/app.py', 'worktree')].kind, 'file')
        self.assertEqual(recs[('src/app.py', 'worktree')].lines, 2)
        self.assertEqual(recs[('src/app.py', 'base')].lines, 6)
        self.assertEqual(recs[('README.md', 'base')].kind, 'file')
        self.assertNotIn(('README.md', 'worktree'), recs)
        self.assertEqual(recs[('img.bin', 'worktree')].kind, 'binary')
        self.assertTrue(recs[('.env', 'worktree')].redacted)
        self.assertIsNotNone(recs[('src/app.py', 'worktree')].sha256)

    def test_snapshot_id_is_stable_and_serialisable(self) -> None:
        again = take_snapshot(self.repo.path, self.scope)
        self.assertEqual(again.snapshot_id, self.snapshot.snapshot_id)
        payload = json.loads(json.dumps(snapshot_to_dict(self.snapshot)))
        restored = snapshot_from_dict(payload)
        self.assertEqual(restored, self.snapshot)

    def test_drift_detects_content_change_with_same_status(self) -> None:
        self.assertTrue(detect_drift(self.snapshot, self.repo.path).unchanged)
        self.repo.write('src/app.py', 'def add(a, b):\n    return a + b\n')
        drift = detect_drift(self.snapshot, self.repo.path)
        self.assertFalse(drift.unchanged)
        self.assertIn('src/app.py', drift.changed_paths)

    def test_drift_detects_new_commit_and_new_file(self) -> None:
        self.repo.write('src/extra.py', 'x = 1\n')
        drift = detect_drift(self.snapshot, self.repo.path)
        self.assertFalse(drift.unchanged)
        self.assertTrue(drift.status_changed)
        self.repo.commit('extra')
        drift = detect_drift(self.snapshot, self.repo.path)
        self.assertTrue(drift.head_changed)

    def test_reader_returns_lines_for_each_version(self) -> None:
        reader = SnapshotReader(self.repo.path, self.snapshot)
        self.assertEqual(reader.read_lines('src/app.py', 'worktree')[1], '    return a - b')
        self.assertEqual(reader.read_lines('src/app.py', 'base')[1], '    return a + b')
        self.assertEqual(reader.read_lines('README.md', 'base'), ('# demo',))
        self.assertEqual(reader.read_lines('README.md', 'worktree'), ())
        self.assertEqual(reader.read_lines('.env', 'worktree'), (), 'redacted content is never read back')

    def test_index_version_is_recorded_when_staged_differs(self) -> None:
        self.repo.write('src/app.py', 'def add(a, b):\n    return a * b\n')
        self.repo.git('add', 'src/app.py')
        self.repo.write('src/app.py', 'def add(a, b):\n    return a / b\n')
        scope = resolve_scope(self.repo.path, ScopeSpec(kind='changes'))
        snapshot = take_snapshot(self.repo.path, scope)
        recs = records(snapshot)
        self.assertIn(('src/app.py', 'index'), recs)
        reader = SnapshotReader(self.repo.path, snapshot)
        self.assertEqual(reader.read_lines('src/app.py', 'index')[1], '    return a * b')
        self.assertEqual(reader.read_lines('src/app.py', 'worktree')[1], '    return a / b')


if __name__ == '__main__':
    unittest.main()
