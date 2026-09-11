"""Command-line interface for ultrareview."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Optional, Sequence, Tuple

from . import __version__
from .config import EFFORTS, LANGUAGES, PROFILES, REPRO_MODES, ROLES, DEFAULT_PREAMBLE, EffortConfig, RunConfig, default_run_dir
from .errors import ConfigError, UltraReviewError
from .pipeline import prepare, plan_text, run_review, run_step
from .replay import record_output
from .runner import SANDBOXES
from .scope import Limits, SCOPE_KINDS, ScopeSpec

ROLE_FLAGS = ('finder', 'verifier', 'reproducer', 'adjudicator', 'mapper', 'triage', 'sweep')


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='ultrareview', description='Deep multi-agent code review for Codex CLI.')
    parser.add_argument('--version', action='version', version=f'ultrareview {__version__}')
    sub = parser.add_subparsers(dest='command')
    run = sub.add_parser('run', help='run a review (default command)')
    add_run_arguments(run)
    plan = sub.add_parser('plan', help='resolve the scope and print the plan without launching agents')
    add_run_arguments(plan)
    step = sub.add_parser('step', help='skill mode: replay recorded agent outputs and print the next batch to run')
    add_run_arguments(step)
    record = sub.add_parser('record', help='skill mode: store one agent\'s final JSON (from --json-file or stdin)')
    record.add_argument('--run-dir', required=True)
    record.add_argument('--agent-id', required=True)
    record.add_argument('--json-file', help='file holding the agent\'s final message; default: stdin')
    record.add_argument('--events-file', help='optional JSONL of {"command","exit_code"} the coordinator captured')
    return parser


def add_run_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--repo', default='.', help='path inside the repository (default: current directory)')
    parser.add_argument('--scope', choices=SCOPE_KINDS, default='branch')
    parser.add_argument('--base', help='base ref for --scope branch (default: origin/HEAD, main, master)')
    parser.add_argument('--commit', help='commit id for --scope commit')
    parser.add_argument('--paths', nargs='*', default=(), help='fnmatch globs narrowing --scope repo')
    parser.add_argument('--profile', choices=PROFILES, default='deep')
    parser.add_argument('--run-dir', help='where to write briefs, logs and the report (outside the repo)')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--agent-timeout', type=int, default=1200, help='seconds per agent')
    parser.add_argument('--votes', type=int, default=1)
    parser.add_argument('--repro', choices=REPRO_MODES, default='auto')
    parser.add_argument('--repro-sandbox', choices=SANDBOXES, default='workspace-write',
                        help='sandbox for reproducer agents; danger-full-access lets them use build caches outside the copy')
    parser.add_argument('--max-repro', type=int, default=6)
    parser.add_argument('--max-findings', type=int, default=15)
    parser.add_argument('--max-files', type=int, default=500)
    parser.add_argument('--max-lines', type=int, default=8000)
    parser.add_argument('--lang', choices=LANGUAGES, default='en')
    parser.add_argument('--note', default='', help='free-text note from the user; a priority for the agents, not a scope change')
    parser.add_argument('--no-preamble', action='store_true')
    parser.add_argument('--keep-sessions', action='store_true')
    parser.add_argument('--keep-worktree', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--model')
    parser.add_argument('--effort', choices=EFFORTS)
    for role in ROLE_FLAGS:
        parser.add_argument(f'--effort-{role}', choices=EFFORTS, dest=f'effort_{role}')
    parser.add_argument('--codex-bin', default='codex')
    parser.add_argument('--retries', type=int, default=1)
    parser.add_argument('--quiet', action='store_true', help='print only the final report')


def config_from_args(args: argparse.Namespace) -> RunConfig:
    repo = Path(args.repo).expanduser().resolve()
    per_role: Tuple[Tuple[str, str], ...] = tuple((role, getattr(args, f'effort_{role}')) for role in ROLE_FLAGS
                                                   if getattr(args, f'effort_{role}', None))
    scope = ScopeSpec(kind=args.scope, base=args.base, commit=args.commit, paths=tuple(args.paths or ()))
    run_dir = Path(args.run_dir).expanduser() if args.run_dir else default_run_dir(repo)
    return RunConfig(repo=repo, scope=scope, run_dir=run_dir, profile=args.profile, jobs=args.jobs,
                     agent_timeout=args.agent_timeout, votes=args.votes, repro=args.repro, max_repro=args.max_repro,
                     repro_sandbox=args.repro_sandbox, note=args.note or '',
                     max_findings=args.max_findings, lang=args.lang,
                     preamble=None if args.no_preamble else DEFAULT_PREAMBLE, keep_sessions=args.keep_sessions,
                     keep_worktree=args.keep_worktree, dry_run=args.dry_run or args.command == 'plan',
                     limits=Limits(max_files=args.max_files, max_lines=args.max_lines),
                     effort=EffortConfig(model=args.model, default=args.effort, per_role=per_role),
                     codex_bin=args.codex_bin, retries=args.retries).validate()


def _record(args: argparse.Namespace) -> int:
    try:
        text = Path(args.json_file).read_text(encoding='utf-8') if args.json_file else sys.stdin.read()
        events = Path(args.events_file).read_text(encoding='utf-8') if args.events_file else ''
    except OSError as exc:
        sys.stderr.write(f'ultrareview: {exc}\n')
        return 1
    ok, message = record_output(Path(args.run_dir).expanduser().resolve(), args.agent_id, text, events)
    print(message)
    return 0 if ok else 3


def _emitter(quiet: bool):
    def emit(message: str) -> None:
        if not quiet:
            sys.stderr.write(message.rstrip('\n') + '\n')
            sys.stderr.flush()
    return emit


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or (raw[0].startswith('-') and raw[0] not in ('--version', '-h', '--help')):
        raw = ['run', *raw]
    parser = build_parser()
    args = parser.parse_args(raw)
    if args.command == 'record':
        return _record(args)
    try:
        config = config_from_args(args)
        if args.command == 'plan':
            print(plan_text(prepare(config, _emitter(True))))
            return 0
        if args.command == 'step':
            if not args.run_dir:
                raise ConfigError('step requires --run-dir (a writable directory outside the repository, e.g. under $TMPDIR)')
            outcome = run_step(config, emit=_emitter(args.quiet))
        else:
            outcome = run_review(config, emit=_emitter(args.quiet))
    except UltraReviewError as exc:
        sys.stderr.write(f'ultrareview: {exc}\n')
        return 1
    except KeyboardInterrupt:
        sys.stderr.write('ultrareview: interrupted\n')
        return 130
    print(outcome.markdown)
    if outcome.report_path:
        sys.stderr.write(f'report: {outcome.report_path}\njson: {outcome.json_path}\n')
    return outcome.exit_code


assert set(ROLE_FLAGS) == set(ROLES)
