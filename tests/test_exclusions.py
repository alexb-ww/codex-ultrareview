from __future__ import annotations

import unittest

from tests.helpers import TempRepo
from ultrareview.exclusions import classify_path, is_secret_like


class SecretLikeTests(unittest.TestCase):
    def test_env_and_key_material_are_secret_like(self) -> None:
        for path in ('.env', '.env.staging', 'deploy/.env', 'certs/server.pem', 'id_rsa',
                     'keys/id_ed25519', 'infra/prod.tfvars', 'service-account-prod.json',
                     '.npmrc', 'home/.netrc', 'store.p12', 'app.jks'):
            self.assertTrue(is_secret_like(path), path)

    def test_ordinary_names_are_not_secret_like(self) -> None:
        for path in ('src/locales/en/auth.json', 'src/auth/handler.go', 'env.py',
                     'docs/environment.md', 'src/keys.ts', 'credentials_form.tsx'):
            self.assertFalse(is_secret_like(path), path)


class ClassifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = TempRepo()
        self.repo.write('vendor/modules.txt', '')
        self.repo.write('vendor/x/lib.go', 'package x\n')
        self.repo.write('src/feature/vendor/actions.ts', 'export {}\n')
        self.repo.write('tools/build/gen.py', 'x = 1\n')
        self.repo.write('web/node_modules/pkg/index.js', 'x\n')
        self.repo.write('web/node_modules/pkg/package.json', '{}\n')
        self.repo.write('src/locales/en/auth.json', '{}\n')

    def tearDown(self) -> None:
        self.repo.cleanup()

    def test_diff_scopes_never_skip_changed_paths(self) -> None:
        for path in ('vendor/x/lib.go', 'web/node_modules/pkg/index.js', '.env'):
            result = classify_path(path, 'changes', self.repo.path)
            self.assertEqual(result.status, 'target', path)
        self.assertTrue(classify_path('.env', 'branch', self.repo.path).redacted)
        self.assertFalse(classify_path('vendor/x/lib.go', 'branch', self.repo.path).redacted)

    def test_repo_scope_skips_root_vendor_with_marker(self) -> None:
        result = classify_path('vendor/x/lib.go', 'repo', self.repo.path)
        self.assertEqual(result.status, 'skipped')
        self.assertIn('dependency', result.reason or '')

    def test_repo_scope_keeps_deep_feature_vendor_dir(self) -> None:
        self.assertEqual(classify_path('src/feature/vendor/actions.ts', 'repo', self.repo.path).status, 'target')
        self.assertEqual(classify_path('tools/build/gen.py', 'repo', self.repo.path).status, 'target')

    def test_repo_scope_skips_nested_node_modules_with_marker(self) -> None:
        result = classify_path('web/node_modules/pkg/index.js', 'repo', self.repo.path)
        self.assertEqual(result.status, 'skipped')

    def test_repo_scope_keeps_auth_json_locale(self) -> None:
        result = classify_path('src/locales/en/auth.json', 'repo', self.repo.path)
        self.assertEqual(result.status, 'target')
        self.assertFalse(result.redacted)

    def test_unsafe_paths_are_skipped_everywhere(self) -> None:
        for path in ('../escape.py', '/abs/path.py', 'a\0b'):
            self.assertEqual(classify_path(path, 'changes', self.repo.path).status, 'skipped')


if __name__ == '__main__':
    unittest.main()
