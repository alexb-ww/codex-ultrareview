from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ROOT

sys.path.insert(0, str(ROOT))
import install  # noqa: E402


class InstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = Path(tempfile.mkdtemp(prefix='ur-home-')).resolve()

    def tearDown(self) -> None:
        shutil.rmtree(self.home, ignore_errors=True)

    def test_dry_run_writes_nothing(self) -> None:
        result = install.install(self.home, ROOT, dry_run=True)
        self.assertFalse(result.skill_dir.exists())

    def test_install_copies_skill_and_kit_and_shim_runs(self) -> None:
        result = install.install(self.home, ROOT)
        skill = result.skill_dir
        self.assertTrue((skill / 'SKILL.md').is_file())
        self.assertTrue((skill / 'agents' / 'openai.yaml').is_file())
        self.assertTrue((skill / 'kit' / 'ultrareview' / 'cli.py').is_file())
        self.assertTrue((skill / 'kit' / 'prompts' / 'finder.md').is_file())
        self.assertTrue((skill / 'kit' / 'hooks' / 'ur_hook_guard.py').is_file())
        proc = subprocess.run([sys.executable, str(skill / 'scripts' / 'ultrareview.py'), '--version'],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn('ultrareview', proc.stdout)
        self.assertFalse((self.home / '.codex' / 'config.toml').exists())

    def test_reinstall_needs_force_and_keeps_backup(self) -> None:
        install.install(self.home, ROOT)
        with self.assertRaises(FileExistsError):
            install.install(self.home, ROOT)
        result = install.install(self.home, ROOT, force=True)
        backups = list(result.skill_dir.parent.glob('ultrareview.bak-*'))
        self.assertEqual(len(backups), 1)

    def test_hooks_written_once_then_printed(self) -> None:
        result = install.install(self.home, ROOT, with_hooks=True)
        hooks = self.home / '.codex' / 'hooks.json'
        self.assertTrue(hooks.exists())
        self.assertIn('PreToolUse', json.loads(hooks.read_text())['hooks'])
        self.assertTrue(any('hooks written' in n for n in result.notes))
        again = install.install(self.home, ROOT, with_hooks=True, force=True)
        self.assertTrue(any('merge this snippet' in n for n in again.notes))

    def test_incomplete_source_rejected(self) -> None:
        partial = self.home / 'partial'
        (partial / 'ultrareview').mkdir(parents=True)
        with self.assertRaises(FileNotFoundError):
            install.install(self.home, partial)


class HookGuardTests(unittest.TestCase):
    def run_guard(self, payload: dict, marker: Path) -> subprocess.CompletedProcess:
        env = {'ULTRAREVIEW_ACTIVE': str(marker), 'PATH': '/usr/bin:/bin'}
        return subprocess.run([sys.executable, str(ROOT / 'kit' / 'hooks' / 'ur_hook_guard.py')], input=json.dumps(payload),
                              capture_output=True, text=True, env=env)

    def test_guard_is_inert_without_marker_and_denies_with_it(self) -> None:
        tmp = Path(tempfile.mkdtemp()).resolve()
        try:
            marker = tmp / 'REVIEW_ACTIVE'
            self.assertEqual(self.run_guard({'tool_name': 'apply_patch', 'tool_input': {}}, marker).returncode, 0)
            marker.write_text('1')
            self.assertEqual(self.run_guard({'tool_name': 'apply_patch', 'tool_input': {}}, marker).returncode, 2)
            self.assertEqual(self.run_guard({'tool_name': 'shell', 'tool_input': {'command': 'git commit -m x'}}, marker).returncode, 2)
            self.assertEqual(self.run_guard({'tool_name': 'shell', 'tool_input': {'command': ['sed', '-i', 's/a/b/', 'f']}}, marker).returncode, 2)
            self.assertEqual(self.run_guard({'tool_name': 'shell', 'tool_input': {'command': 'sed -n 1,5p f.py'}}, marker).returncode, 0)
            self.assertEqual(self.run_guard({'tool_name': 'shell', 'tool_input': {'command': 'git diff main'}}, marker).returncode, 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
