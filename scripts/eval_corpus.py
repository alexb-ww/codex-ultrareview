#!/usr/bin/env python3
"""Score a report.json against a corpus expected.json: recall of planted defects, precision.

Usage: python3 scripts/eval_corpus.py <report.json> <expected.json>
A finding matches a defect when it is in the same file and within 4 lines of the
planted line, or when its summary/scenario mentions one of the defect keywords.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Dict, List

LINE_WINDOW = 4


def load(path: str) -> Dict:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def finding_text(finding: Dict) -> str:
    candidate = finding.get('candidate', {})
    parts = [candidate.get('summary', ''), candidate.get('failure_scenario', ''), candidate.get('root_cause', '')]
    for verification in finding.get('verifications', []):
        parts.append(verification.get('reasoning', ''))
        parts.append(verification.get('trigger', ''))
    return ' '.join(parts).lower()


def matches(finding: Dict, defect: Dict) -> bool:
    candidate = finding.get('candidate', {})
    same_file = candidate.get('file') == defect['file']
    near = same_file and abs(int(candidate.get('line_start', 0)) - int(defect['line'])) <= LINE_WINDOW
    text = finding_text(finding)
    keyword = same_file and any(k.lower() in text for k in defect.get('keywords', []))
    return near or keyword


def score(report: Dict, expected: Dict) -> Dict:
    findings = report.get('findings', [])
    hits: Dict[str, List[str]] = {d['id']: [] for d in expected['defects']}
    matched_findings = set()
    for finding in findings:
        for defect in expected['defects']:
            if matches(finding, defect):
                hits[defect['id']].append(finding['id'])
                matched_findings.add(finding['id'])
    decoy_hits = [f['id'] for f in findings for d in expected.get('decoys', []) if matches(f, d)]
    unmatched = [f['id'] for f in findings if f['id'] not in matched_findings]
    found = sum(1 for ids in hits.values() if ids)
    total = len(expected['defects'])
    precision = (len(matched_findings) / len(findings)) if findings else None
    return {'recall': f'{found}/{total}', 'found': {k: v for k, v in hits.items() if v},
            'missed': [k for k, v in hits.items() if not v], 'unmatched_findings': unmatched,
            'decoy_hits': decoy_hits, 'precision': precision, 'status': report.get('status')}


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    result = score(load(sys.argv[1]), load(sys.argv[2]))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
