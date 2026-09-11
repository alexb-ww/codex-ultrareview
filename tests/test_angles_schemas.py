from __future__ import annotations

import json
import unittest

from ultrareview import angles, schemas
from ultrareview.errors import ConfigError


class AngleTests(unittest.TestCase):
    def test_profiles_select_expected_counts(self) -> None:
        self.assertEqual(len(angles.select_angles('fast', 'branch')), 5)
        self.assertEqual(len(angles.select_angles('standard', 'changes')), 9)
        self.assertEqual(len(angles.select_angles('deep', 'commit')), 10)

    def test_repo_scope_uses_domain_angles_plus_line_scan(self) -> None:
        ids = [a.id for a in angles.select_angles('deep', 'repo')]
        self.assertEqual(ids[0], 'a-line-scan')
        self.assertIn('tests-integration', ids)
        self.assertNotIn('b-removed-behavior', ids)

    def test_unknown_profile_or_angle(self) -> None:
        with self.assertRaises(ConfigError):
            angles.select_angles('ultra', 'branch')
        with self.assertRaises(ConfigError):
            angles.angle_by_id('nope')

    def test_angle_ids_unique(self) -> None:
        ids = [a.id for a in angles.ALL_ANGLES]
        self.assertEqual(len(ids), len(set(ids)))


class SchemaTests(unittest.TestCase):
    def test_every_role_schema_is_strict_and_json_serialisable(self) -> None:
        for role in schemas.SCHEMA_BUILDERS:
            schema = schemas.schema_for(role)
            self.assertTrue(schemas.is_strict(schema), role)
            json.dumps(schema)

    def test_non_strict_detected(self) -> None:
        loose = {'type': 'object', 'properties': {'a': schemas.string()}, 'required': [], 'additionalProperties': False}
        self.assertFalse(schemas.is_strict(loose))
        open_obj = {'type': 'object', 'properties': {'a': schemas.string()}, 'required': ['a']}
        self.assertFalse(schemas.is_strict(open_obj))

    def test_unknown_role(self) -> None:
        with self.assertRaises(KeyError):
            schemas.schema_for('poet')

    def test_verdict_enum_matches_contract(self) -> None:
        verdict = schemas.verifier_schema()['properties']['verdict']
        self.assertEqual(verdict['enum'], ['CONFIRMED', 'PLAUSIBLE', 'REFUTED'])


if __name__ == '__main__':
    unittest.main()
