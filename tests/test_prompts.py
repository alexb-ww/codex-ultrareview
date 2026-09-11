from __future__ import annotations

import unittest

from ultrareview import PROMPTS_DIR, angles, prompts
from ultrareview.prompts import PromptError

ROLE_TEMPLATES = ('common.md', 'mapper.md', 'finder.md', 'triage.md', 'verifier.md',
                  'reproducer.md', 'sweep.md', 'adjudicator.md')


class TemplateTests(unittest.TestCase):
    def test_all_templates_exist_and_use_review_vocabulary(self) -> None:
        names = ROLE_TEMPLATES + tuple(angle.template for angle in angles.ALL_ANGLES)
        for name in names:
            text = prompts.load_template(name)
            self.assertTrue(text.strip(), name)
            self.assertEqual(prompts.forbidden_words(text), (), name)

    def test_angle_templates_have_no_placeholders(self) -> None:
        for angle in angles.ALL_ANGLES:
            self.assertEqual(prompts.placeholders(prompts.load_template(angle.template)), (), angle.id)

    def test_common_template_placeholders(self) -> None:
        keys = prompts.placeholders(prompts.load_template('common.md'))
        for expected in ('PREAMBLE', 'ROLE', 'AGENT_ID', 'RUN_ID', 'REPO_ROOT', 'SCOPE_SUMMARY', 'LANG_INSTRUCTION'):
            self.assertIn(expected, keys)

    def test_render_fills_every_placeholder(self) -> None:
        text = prompts.render('Hello {{ NAME }} and {{OTHER}}', {'NAME': 'a', 'OTHER': 'b'})
        self.assertEqual(text, 'Hello a and b')

    def test_render_rejects_missing_values(self) -> None:
        with self.assertRaises(PromptError):
            prompts.render('{{NAME}}', {})

    def test_missing_template_raises(self) -> None:
        with self.assertRaises(PromptError):
            prompts.load_template('does-not-exist.md', PROMPTS_DIR)

    def test_forbidden_words_detected(self) -> None:
        self.assertEqual(prompts.forbidden_words('an Adversarial pass that attacks'), ('attack', 'adversarial'))
        self.assertEqual(prompts.forbidden_words('check and refute the claim'), ())


if __name__ == '__main__':
    unittest.main()
