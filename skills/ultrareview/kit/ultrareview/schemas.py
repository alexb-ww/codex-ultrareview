"""Strict JSON schemas for every agent role's final message.

All schemas follow the strict structured-output rules: every object lists all of
its properties as required and forbids additional properties, enums are plain
string enums, and no unsupported keywords (minItems, maxItems, format) are used.
Caps such as "at most 8 candidates" are enforced by the driver after parsing.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

VERDICTS = ('CONFIRMED', 'PLAUSIBLE', 'REFUTED')
SEVERITIES = ('P0', 'P1', 'P2', 'P3')
ORIGINS = ('introduced', 'pre-existing', 'unknown')
REPRO_RESULTS = ('reproduced', 'not_reproduced', 'blocked')
DECISIONS = ('accept', 'downgrade', 'reject')


def string() -> Dict[str, Any]:
    return {'type': 'string'}


def integer() -> Dict[str, Any]:
    return {'type': 'integer'}


def boolean() -> Dict[str, Any]:
    return {'type': 'boolean'}


def enum(values: Tuple[str, ...]) -> Dict[str, Any]:
    return {'type': 'string', 'enum': list(values)}


def array(items: Dict[str, Any]) -> Dict[str, Any]:
    return {'type': 'array', 'items': items}


def obj(properties: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {'type': 'object', 'properties': dict(properties),
            'required': list(properties), 'additionalProperties': False}


def quote_schema() -> Dict[str, Any]:
    return obj({'path': string(), 'line': integer(), 'text': string()})


def candidate_schema() -> Dict[str, Any]:
    return obj({
        'file': string(),
        'line_start': integer(),
        'line_end': integer(),
        'summary': string(),
        'failure_scenario': string(),
        'root_cause': string(),
        'category': string(),
        'quotes': array(quote_schema()),
        'commands_run': array(string()),
    })


def finder_schema() -> Dict[str, Any]:
    return obj({
        'candidates': array(candidate_schema()),
        'files_read': array(string()),
        'coverage_note': string(),
    })


def mapper_schema() -> Dict[str, Any]:
    module = obj({'name': string(), 'paths': array(string()), 'purpose': string()})
    return obj({
        'modules': array(module),
        'entry_points': array(string()),
        'trust_boundaries': array(string()),
        'storages': array(string()),
        'external_contracts': array(string()),
        'critical_flows': array(string()),
        'test_stack': string(),
        'unknowns': array(string()),
    })


def triage_schema() -> Dict[str, Any]:
    cluster = obj({'cluster_id': string(), 'member_ids': array(string()),
                   'canonical_id': string(), 'rationale': string()})
    return obj({'clusters': array(cluster)})


def verifier_schema() -> Dict[str, Any]:
    return obj({
        'verdict': enum(VERDICTS),
        'severity': enum(SEVERITIES),
        'origin': enum(ORIGINS),
        'trigger': string(),
        'reasoning': string(),
        'counterevidence_checked': array(string()),
        'quotes': array(quote_schema()),
        'what_would_confirm': string(),
        'fix': string(),
        'regression_test': string(),
        'commands_run': array(string()),
    })


def reproducer_schema() -> Dict[str, Any]:
    return obj({
        'result': enum(REPRO_RESULTS),
        'command': string(),
        'cwd': string(),
        'exit_code': integer(),
        'output_excerpt': string(),
        'explanation': string(),
        'test_file': string(),
        'blocked_reason': string(),
        'commands_run': array(string()),
    })


def adjudicator_schema() -> Dict[str, Any]:
    decision = obj({'finding_id': string(), 'action': enum(DECISIONS), 'severity': enum(SEVERITIES),
                    'reason': string(), 'merged_into': string()})
    suspicion = obj({'file': string(), 'line_start': integer(), 'line_end': integer(),
                     'summary': string(), 'failure_scenario': string()})
    return obj({
        'decisions': array(decision),
        'coverage_gaps': array(string()),
        'new_suspicions': array(suspicion),
        'summary': string(),
    })


SCHEMA_BUILDERS = {
    'mapper': mapper_schema,
    'finder': finder_schema,
    'sweep': finder_schema,
    'triage': triage_schema,
    'verifier': verifier_schema,
    'reproducer': reproducer_schema,
    'adjudicator': adjudicator_schema,
}


def schema_for(role: str) -> Dict[str, Any]:
    try:
        return SCHEMA_BUILDERS[role]()
    except KeyError as exc:
        raise KeyError(f'no schema for role {role!r}') from exc


def is_strict(schema: Dict[str, Any]) -> bool:
    """True when every object in the schema lists all properties as required."""
    if schema.get('type') == 'object':
        props = schema.get('properties', {})
        if schema.get('additionalProperties') is not False or set(schema.get('required', [])) != set(props):
            return False
        return all(is_strict(child) for child in props.values())
    if schema.get('type') == 'array':
        return is_strict(schema['items'])
    return True
