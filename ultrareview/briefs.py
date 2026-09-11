"""Assemble self-contained briefs for every agent role from the prompt templates."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .angles import Angle
from .prompts import load_template, render
from .scope import ResolvedScope
from .snapshot import Snapshot

TARGET_LIST_CAP = 400
LANG_TEXT = {'en': 'English', 'ru': 'Russian (русский); keep identifiers, paths and code in the original'}


@dataclass(frozen=True)
class BriefContext:
    run_id: str
    repo_root: str
    scope: ResolvedScope
    snapshot: Snapshot
    diff_path: str
    lang: str = 'en'
    preamble: Optional[str] = None
    inline_diff_limit: int = 64 * 1024
    max_candidates: int = 8
    max_findings: int = 15
    note: str = ''


def user_note(note: str) -> str:
    text = ' '.join(note.split())
    if not text:
        return ''
    return ('\nUser note (a priority to weigh, never a restriction of the scope or a finding to '
            f'confirm): {text}\n')


def scope_summary(scope: ResolvedScope) -> str:
    lines = [f'Scope: {scope.kind}']
    if scope.head:
        lines.append(f'HEAD: {scope.head}')
    if scope.base_ref:
        lines.append(f'Base: {scope.base_ref} ({scope.base_commit}); merge-base {scope.merge_base}')
    if scope.commit:
        lines.append(f'Commit under review: {scope.commit} (parent {scope.base_commit})')
    lines.append(f'Changed files: {scope.changed_files}; changed lines: {scope.changed_lines}')
    lines.extend(f'Note: {note}' for note in scope.notes)
    return '\n'.join(lines)


def target_list(scope: ResolvedScope) -> str:
    rows = []
    for target in scope.targets[:TARGET_LIST_CAP]:
        flags = ' [redacted: do not read]' if target.redacted else ''
        rows.append(f'- {target.path} ({target.status}; versions: {", ".join(target.versions)}){flags}')
    if len(scope.targets) > TARGET_LIST_CAP:
        rows.append(f'- … and {len(scope.targets) - TARGET_LIST_CAP} more files (see the run directory inventory)')
    if scope.skipped:
        rows.append('Skipped (not under review): ' + ', '.join(f'{p} ({r})' for p, r in scope.skipped[:20]))
    return '\n'.join(rows) if rows else '(no files)'


def diff_section(ctx: BriefContext) -> str:
    scope = ctx.scope
    if scope.kind == 'repo':
        return ('## Diff\n\nNo diff: this is a whole-repository review of the files listed above. '
                'Read them directly.')
    if not scope.diff_text and not scope.index_diff_text:
        return '## Diff\n\n(the diff is empty)'
    header = '## Diff\n\n'
    base_hint = f'Read the base version of a file with `git show {scope.diff_base}:<path>`.\n\n' if scope.diff_base else ''
    total = len(scope.diff_text.encode('utf-8')) + len(scope.index_diff_text.encode('utf-8'))
    inline = total <= ctx.inline_diff_limit
    if not scope.diff_text:
        body = 'The working tree matches the base; the change lives only in the index (staged) version below.\n'
    elif inline:
        body = f'```diff\n{scope.diff_text}\n```\n'
    else:
        body = (f'The unified diff is {len(scope.diff_text)} characters, too large to inline. '
                f'Read it from `{ctx.diff_path}` (plain text) with `sed -n` in slices.\n')
    index_part = ''
    if scope.index_diff_text and inline:
        index_part = ('\n### Index (staged) version of files whose working tree also differs\n\n'
                      f'```diff\n{scope.index_diff_text}\n```\n')
    elif scope.index_diff_text:
        index_part = (f'\nSome files also have a staged (index) version that differs; it is appended to '
                      f'`{ctx.diff_path}` under "# index versions" and readable with `git show :<path>`.\n')
    return header + base_hint + body + index_part


def map_section(map_output: Optional[Mapping[str, Any]]) -> str:
    if not map_output:
        return ''
    parts = ['## Map of the code (facts gathered by a mapper agent)\n']
    for key in ('modules', 'entry_points', 'trust_boundaries', 'storages', 'external_contracts',
                'critical_flows', 'unknowns'):
        items = map_output.get(key) or []
        if not items:
            continue
        parts.append(f'{key.replace("_", " ").capitalize()}:')
        for item in items[:25]:
            if isinstance(item, dict):
                parts.append(f'- {item.get("name", "")}: {item.get("purpose", "")} ({", ".join(item.get("paths", [])[:6])})')
            else:
                parts.append(f'- {item}')
    if map_output.get('test_stack'):
        parts.append(f'Test stack: {map_output["test_stack"]}')
    return '\n'.join(parts) + '\n'


def header(ctx: BriefContext, role: str, agent_id: str, marker: str) -> str:
    common = render(load_template('common.md'), {
        'PREAMBLE': ctx.preamble or '', 'ROLE': role, 'AGENT_ID': agent_id, 'RUN_ID': ctx.run_id,
        'REPO_ROOT': ctx.repo_root, 'SCOPE_SUMMARY': scope_summary(ctx.scope),
        'LANG_INSTRUCTION': LANG_TEXT.get(ctx.lang, 'English'), 'USER_NOTE': user_note(ctx.note),
    })
    return f'{common.strip()}\nMARKER: {marker}\n\n'


def finder_brief(ctx: BriefContext, agent_id: str, angle: Angle, map_output: Optional[Mapping[str, Any]],
                 shard: Optional[Sequence[str]] = None) -> str:
    targets = target_list(ctx.scope)
    if shard:
        targets = 'Your shard (review only these files; others are covered elsewhere):\n' + '\n'.join(f'- {p}' for p in shard)
    body = render(load_template('finder.md'), {
        'ANGLE_NAME': angle.name, 'ANGLE_TEXT': load_template(angle.template).strip(),
        'MAX_CANDIDATES': str(ctx.max_candidates), 'DIFF_SECTION': diff_section(ctx),
        'MAP_SECTION': map_section(map_output), 'TARGET_LIST': targets,
    })
    return header(ctx, 'finder', agent_id, angle.id) + body


def sweep_brief(ctx: BriefContext, agent_id: str, verified: Iterable[Mapping[str, Any]]) -> str:
    rows = [f'- {item.get("file")}:{item.get("line_start")} [{item.get("verdict")}] {item.get("summary")}'
            for item in verified] or ['(nothing was confirmed or kept plausible in the first wave)']
    body = render(load_template('sweep.md'), {
        'VERIFIED_LIST': '\n'.join(rows), 'DIFF_SECTION': diff_section(ctx),
        'MAX_CANDIDATES': str(ctx.max_candidates),
    })
    return header(ctx, 'sweep', agent_id, 'sweep') + body


def triage_brief(ctx: BriefContext, agent_id: str, candidates: Sequence[Mapping[str, Any]]) -> str:
    body = render(load_template('triage.md'), {'CANDIDATES_JSON': json.dumps(list(candidates), indent=1, ensure_ascii=False)})
    return header(ctx, 'triage', agent_id, 'triage') + body


def base_info(ctx: BriefContext) -> str:
    scope = ctx.scope
    if scope.kind == 'repo':
        return 'There is no base revision: this is a whole-repository review; `origin` is normally `pre-existing` or `unknown`.'
    lines = [f'Base revision for origin checks: `{scope.diff_base}`. Read the base version with '
             f'`git show {scope.diff_base}:<path>`; compare it with the working tree to decide `origin`.']
    if scope.index_diff_text:
        lines.append('Some files also have a staged (index) version that differs from the working tree; '
                     'read it with `git show :<path>` when the candidate names one of them.')
    return '\n'.join(lines)


def verifier_brief(ctx: BriefContext, agent_id: str, candidate: Mapping[str, Any], marker: str) -> str:
    body = render(load_template('verifier.md'), {
        'CANDIDATE_JSON': json.dumps(dict(candidate), indent=1, ensure_ascii=False),
        'BASE_INFO': base_info(ctx), 'DIFF_SECTION': diff_section(ctx),
    })
    return header(ctx, 'verifier', agent_id, marker) + body


def reproducer_brief(ctx: BriefContext, agent_id: str, finding: Mapping[str, Any], worktree_path: str,
                     test_hints: str, marker: str) -> str:
    body = render(load_template('reproducer.md'), {
        'FINDING_JSON': json.dumps(dict(finding), indent=1, ensure_ascii=False),
        'WORKTREE_PATH': worktree_path, 'TEST_HINTS': test_hints or '',
    })
    return header(ctx, 'reproducer', agent_id, marker) + body


def mapper_brief(ctx: BriefContext, agent_id: str) -> str:
    body = render(load_template('mapper.md'), {'TARGET_LIST': target_list(ctx.scope), 'DIFF_SECTION': diff_section(ctx)})
    return header(ctx, 'mapper', agent_id, 'mapper') + body


def adjudicator_brief(ctx: BriefContext, agent_id: str, findings: Sequence[Mapping[str, Any]],
                      coverage: Mapping[str, Any], limitations: Sequence[str]) -> str:
    body = render(load_template('adjudicator.md'), {
        'FINDINGS_JSON': json.dumps(list(findings), indent=1, ensure_ascii=False),
        'COVERAGE_JSON': json.dumps(dict(coverage), indent=1, ensure_ascii=False),
        'LIMITATIONS': '\n'.join(f'- {item}' for item in limitations) or '- none recorded',
        'MAX_FINDINGS': str(ctx.max_findings),
    })
    return header(ctx, 'adjudicator', agent_id, 'adjudicator') + body


def brief_context_dict(ctx: BriefContext) -> Dict[str, Any]:
    return {'run_id': ctx.run_id, 'repo_root': ctx.repo_root, 'lang': ctx.lang, 'diff_path': ctx.diff_path,
            'inline_diff_limit': ctx.inline_diff_limit, 'max_candidates': ctx.max_candidates,
            'max_findings': ctx.max_findings, 'preamble': bool(ctx.preamble)}
