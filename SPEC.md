# Ultra Review for Codex CLI — build specification (v0.2.0)

Goal: a local deep code review for Codex CLI that matches or beats Claude Code's
`/code-review ultra` on recall, verification quality and honesty of the report.
It has two entry points that share one protocol, one set of prompts and one gate:

1. **Driver** (`python3 -m ultrareview …`, launcher `bin/ultrareview`): a deterministic
   orchestrator that runs every role as its own `codex exec` process. Fresh context per
   role is guaranteed by construction. This is the primary, strongest mode.
2. **Skill** (`$ultrareview` inside interactive Codex): the same phases run through
   native `spawn_agent` sub-agents. The skill calls the driver's `prep`, `gate` and
   `render` subcommands so everything deterministic stays deterministic.

Python 3.9+, standard library only. No third-party packages at runtime or in tests.

## Verified runtime facts (Codex CLI 0.154.0, 2026-09-11) — design against these

- `codex exec [PROMPT]` flags that exist: `-m/--model`, `-c key=value`,
  `-s read-only|workspace-write|danger-full-access`, `-C <dir>`, `--skip-git-repo-check`,
  `--ephemeral`, `--json`, `-o/--output-last-message <FILE>`, `--output-schema <FILE>`,
  `--add-dir`, `--worktree` (experimental, do not rely on it). Reasoning effort is set with
  `-c model_reasoning_effort=<none|minimal|low|medium|high|xhigh|max>`.
- stdin must be closed (`stdin=subprocess.DEVNULL`) or exec waits on it.
- `--json` prints JSONL events on stdout. Observed shapes:
  - `{"type":"thread.started","thread_id":"…"}`
  - `{"type":"turn.started"}`
  - `{"type":"item.started"|"item.completed","item":{"id":"item_1","type":"command_execution","command":"/bin/zsh -lc 'cat calc.py'","aggregated_output":"…","exit_code":0,"status":"completed"}}`
  - `{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":"…"}}`
  - `{"type":"turn.completed","usage":{"input_tokens":…,"cached_input_tokens":…,"output_tokens":…,"reasoning_output_tokens":…}}`
  Unknown event/item types must be tolerated and preserved.
- `--output-schema` is enforced: the final agent message is exactly the JSON that matches
  the schema, and `-o` writes that JSON to the file. Schemas must be strict-style:
  every object lists `required` = all properties and `additionalProperties: false`.
- Sub-agents inside interactive Codex: `spawn_agent(task_name, message, model?,
  reasoning_effort?, fork_turns?)`. **`fork_turns` defaults to `all`** (inherits the whole
  parent conversation). Independence requires `fork_turns: "none"`. There is no
  `agent_type` and no `close_agent` in this runtime; `wait_agent(timeout_ms)` defaults to
  30 000 ms, max 3 600 000 ms.
- Codex hooks exist: `~/.codex/hooks.json` and `<repo>/.codex/hooks.json` (project hooks
  load only for trusted projects). Events: SessionStart, SessionEnd, PreToolUse, PostToolUse,
  PermissionRequest, PreCompact, PostCompact, UserPromptSubmit, SubagentStart, SubagentStop,
  Stop, Interrupt. A hook reads JSON on stdin (`session_id, cwd, hook_event_name, tool_name,
  tool_input, tool_response`) and denies with exit code 2 (reason on stderr).
- Inside a Codex sandbox, child processes have no network, so a nested `codex exec` cannot
  be used from the skill. The skill must use `spawn_agent`.
- The sandbox in `read-only` denies creating any directory anywhere; a role that must build
  or run tests needs `workspace-write` in a disposable worktree copy.

## Scopes

`--scope branch` (default) | `changes` | `commit <sha>` | `repo`.

- branch: diff `merge-base(HEAD, base)…HEAD` plus uncommitted (index + working tree) plus
  untracked non-ignored files. Base: `--base <ref>`, else `refs/remotes/origin/HEAD`, else
  local `main`, else `master`; say which one was used. No merge-base → stop with a clear
  message that suggests `--scope repo` (never silently widen).
- changes: index + working tree + untracked. When a path is both staged and unstaged, the
  index version is also a target (a bug can hide in the index while the net diff is empty).
- commit: `git diff <sha>^..<sha>`.
- repo: every tracked non-excluded file, optionally narrowed with `--paths <glob>…`.
  Completeness is judged against the narrowed set and the narrowing is printed.

Limits (defaults, configurable): 500 changed files, 8 000 changed lines. When exceeded,
refuse before spending any tokens and print the ten largest files. Empty diff → say so and
exit 0 with a report that states it. Test/fixture files are in scope (a deleted test is a
finding candidate for the removed-behavior angle).

## Snapshot and exclusions

Inventory the target files with sha256, size, line count and a `version` tag
(`worktree|index|base`). Detect drift at the end by re-hashing; drift → status `partial`
and the changed paths are listed. Write everything to a run directory **outside the repo**
(`--run-dir`, default `~/.cache/ultrareview/runs/<utc-stamp>-<repo-name>/`).

Exclusion rules (learned from real repos):
- In `changes`, `branch` and `commit` scopes a changed path is **never** excluded. It may
  be marked `redacted` (secret-like: `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.jks`,
  `*.tfvars`, `.netrc`, `.npmrc`, `.pypirc`, `id_rsa*`, `service-account*.json`) — then the
  prompts must not inline its contents and agents are told not to read it.
- `node_modules`, `.venv`, `venv`, `__pycache__`, `.next`, `.gradle`, `Pods`, `dist`,
  `build`, `vendor` are skipped in `repo` scope **only when the directory is at the
  repository root or contains a dependency marker** (`package.json`, `modules.txt`,
  `.package-lock.json`, `go.sum`). A feature folder named `vendor/` or `build/` deep in
  `src/` is source code. Basename heuristics like `auth.json` are never used.
- Binary files (NUL byte in the first 8 KiB) and files over 16 MiB are inventoried without
  content and listed under `skipped` with a reason.
- Never follow symlinks; never read a symlink target; submodule gitlinks are `skipped`.
- Every `git diff` call passes `--no-ext-diff --no-textconv` and neutralises configured
  clean/smudge/process filters (`-c filter.<name>.clean= …`) plus `-c core.fsmonitor=false`,
  so reading the diff cannot run repository-configured commands.

## Phases (driver)

Every agent is a fresh `codex exec` with `--json`, `-o`, `--output-schema`, `--ephemeral`
unless `--keep-sessions`, and stdin closed. Each gets a self-contained brief: the scope,
the unified diff (inline when ≤ 64 KiB, otherwise the path of the diff file plus the file
list), the snapshot summary, safety rules, its role text and the output schema. Concurrency
is `--jobs` (default 4). Per-agent timeout `--agent-timeout` (default 1 200 s); on timeout
the process group is killed and the agent is recorded as `failed`.

0. **Preflight**: codex on PATH and version; git repo; scope; limits; snapshot; run dir;
   print the plan (scope, files, lines, angle list, budgets, estimated agent count) and
   continue unless `--dry-run`.
1. **Map** (only when changed files > 12 or scope=repo): one mapper returns modules, entry
   points, trust boundaries, storages, external contracts, critical flows, test stack and
   unknowns. It must not propose bugs. Its JSON is summarised into finder briefs.
2. **Find**: N independent finder angles, each a fresh agent, each returning up to 8
   candidates. Finders are told: pass every candidate with a nameable failure scenario;
   silently dropping half-believed candidates bypasses verification and is the dominant
   cause of misses; do not let one angle's conclusions suppress another's.
   Angle sets:
   - diff angles (scopes branch/changes/commit): A line-by-line hunk scan plus the
     enclosing function; B removed-behavior auditor (every deleted/replaced line: which
     invariant did it enforce, where is it re-established); C cross-file tracer (callers
     and callees of every changed symbol); D language/framework pitfall specialist;
     E wrapper/proxy/adapter correctness.
   - domain angles (all scopes): security & authorization (source→sink, tenant
     isolation, secrets, webhooks); state & concurrency (schedules of operations,
     idempotency, check-then-act, lost updates); contracts & data (producer/consumer,
     nullability, migrations, old client vs new server, pagination, money, time zones);
     failure & resources (partial failure, swallowed errors, retries, timeouts, leaks);
     tests & integration (tests that don't test, mocks that mask, CI config vs build,
     feature flags, deleted tests).
   - `--profile fast`: A, B, C + security + contracts. `standard`: all five diff angles +
     security, state, contracts, failure. `deep` (default): all ten. `repo` scope uses the
     five domain angles plus A, sharded by module from the map when files > 60.
3. **Triage**: deterministic clustering (same file, overlapping line windows ±5) followed
   by one fresh triage agent that merges candidates with the same root cause across
   clusters. Triage may merge, never drop. Every cluster keeps its member ids and angles.
4. **Verify**: one fresh verifier per cluster (`--votes` 1 by default; when >1, the finding
   survives unless a majority says REFUTED). Verdicts:
   - CONFIRMED — the verifier names the inputs/state that trigger it and the wrong
     output/crash, and quotes the exact line(s).
   - PLAUSIBLE — the mechanism is real, the trigger is uncertain (timing, environment,
     configuration); the verifier states what would confirm it.
   - REFUTED — only when constructible from the code: factually wrong (quote the actual
     line), provably impossible (type/constant/invariant shown), already handled in this
     diff (cite the guard), or pure style with no observable effect.
   PLAUSIBLE is the default for realistic runtime states: races, nil on a rare-but-reachable
   path, falsy-zero, off-by-one on an unexcluded boundary, retry storms, an allowlist that
   lost an anchor. "Could not run it" is never REFUTED. The verifier also reports
   `origin` (`introduced|pre-existing|unknown`) for diff scopes by reading the base version,
   `severity` P0–P3 with the damage and its conditions, and `quotes[]` (path, line, text).
5. **Reproduce** (`--repro auto|off|all`, default auto = CONFIRMED and PLAUSIBLE up to
   `--max-repro` 6): a fresh reproducer runs in a disposable worktree copy that matches the
   snapshot (`git worktree add --detach`, then apply the working-tree diff and copy
   untracked files), `-s workspace-write`, network stays off. It writes a minimal test or
   script that calls the real project code, runs it, and returns `reproduced|not_reproduced|
   blocked` with the command, exit code and a short output excerpt. It must never touch the
   original checkout, commit, install dependencies or create private caches. The worktree
   is removed afterwards unless `--keep-worktree`. A reproduced finding is marked
   `verification: reproduced`; blocked runs keep the verifier verdict and record why.
6. **Sweep**: one fresh finder receives the verified list and hunts ONLY for defects not on
   it, focused on second-tier footguns (moved/extracted code that dropped a guard or anchor,
   config defaults flipped, lock scope shrunk, setup/teardown asymmetry in tests, dataclass
   default evaluated once, predicate methods with side effects). Up to 8 new candidates,
   empty is fine. New candidates go through triage → verify (→ repro).
7. **Adjudicate**: one fresh adjudicator sees findings, verdicts, reproductions, coverage
   and limitations. It may accept, downgrade or reject with a quoted reason, merges
   duplicates, checks scope/origin, orders by severity, caps the final list at
   `--max-findings` 15, and lists coverage gaps. It cannot upgrade a verdict or add a
   finding without a new verifier run (new suspicions go back to phase 4, once).
8. **Gate** (deterministic):
   - structure: required fields, enums, ids unique, discoverer ≠ verifier ≠ adjudicator.
   - **ledger authentication**: every claimed command in evidence must match a
     `command_execution` item in that agent's own event stream (normalised: strip the
     `/bin/zsh -lc` wrapper and quotes); every quoted line must exist in the snapshot at
     that line with that text (whitespace-normalised). Failures are listed per finding
     and the finding is downgraded to `unverified-evidence`, never silently accepted.
   - drift check; status computation (`complete|partial|blocked`).
9. **Render**: `report.md`, `report.json`, `ledger.jsonl`, `agents/<id>.events.jsonl`,
   `agents/<id>.brief.md`, `agents/<id>.output.json`, `run.json` (config, timings, usage
   totals per phase, exit reasons). Terminal output: findings first (P0→P3, verdict badge,
   file:line, one-line summary, failure scenario, evidence, fix, regression test), then
   unresolved/plausible-without-repro, rejected with reasons, coverage table, limitations,
   run passport (scope, HEAD/base/merge-base, model/effort if known, agents run/failed,
   tokens, wall time). Exit codes: 0 completed, 1 launch/scope failure, 3 completed but
   partial or gate errors.

`--lang en|ru` (default en) tells agents which language to write summaries in; prompts
themselves are English. `--no-preamble` disables the environment preamble; the default
preamble is the verbatim paragraph:

«Не задавай GOCACHE, GOMODCACHE, GOLANGCI_LINT_CACHE и другие кэши в /tmp, используй
системные значения по умолчанию. Не запускай go test -race и make l2/l3/l4, только обычный
go test по нужным пакетам. Не создавай worktree, DerivedData и симуляторы, используй
существующие. Результаты сборок удаляй сразу после анализа.»

Prompt wording: use review vocabulary ("check", "bugs", "reproduce with a test", "confirm",
"refute"); never "attack", "adversarial", "exploit", "break" — the provider's classifier
rejects such briefs.

## Model and effort

Default: inherit the user's `config.toml` (pass neither `-m` nor an effort). Overrides:
`--model`, `--effort` (all roles), `--effort-finder/--effort-verifier/--effort-repro/
--effort-adjudicator`. Validate effort values against
`none, minimal, low, medium, high, xhigh, max`.

## Skill mode (`skill/ultrareview/`)

`SKILL.md` (English, ≤ 120 lines, explicit-invocation only via `agents/openai.yaml`
`policy.allow_implicit_invocation: false`): the coordinator runs
`python3 <skill>/scripts/ultrareview.py prep …` (a shim to the package) which writes the
run dir, briefs and schemas and prints the plan; then spawns one sub-agent per brief with
`fork_turns: "none"`, a long `wait_agent` timeout, and pastes each brief file path plus
"read only this brief and the files it names"; collects each agent's final JSON into the
run dir; runs `gate` and `render`; prints the rendered report. If `spawn_agent` is missing
it says so and runs a single-pass inline review labelled as such. It never edits files.

Optional hooks (`hooks/`): a PreToolUse guard that denies `apply_patch` and shell commands
matching write patterns while `<run-dir>/REVIEW_ACTIVE` exists, and a PostToolUse +
SubagentStart/SubagentStop ledger writer appending to `<run-dir>/hook-ledger.jsonl`.
`install.py --with-hooks` prints the merge snippet when `~/.codex/hooks.json` exists and
writes it only when it does not.

## Package layout

```
bin/ultrareview                 launcher (exec python3 -m ultrareview "$@")
ultrareview/
  __init__.py __main__.py cli.py config.py errors.py
  scope.py snapshot.py gitio.py worktree.py
  prompts.py angles.py schemas.py
  runner.py ledger.py
  phases/ __init__.py map_.py find.py triage.py verify.py repro.py sweep.py adjudicate.py
  gate.py report.py
prompts/ common.md mapper.md finder.md triage.md verifier.md reproducer.md sweep.md adjudicator.md
prompts/angles/ a-line-scan.md b-removed-behavior.md c-cross-file.md d-language-pitfalls.md
                e-wrapper-proxy.md security.md state-concurrency.md contracts-data.md
                failure-resources.md tests-integration.md
skill/ultrareview/ SKILL.md agents/openai.yaml scripts/ultrareview.py references/*.md
hooks/ hooks.json ur_hook_guard.py ur_hook_ledger.py
tests/ (unittest) + tests/fake_codex/codex (executable stub) + tests/fixtures/
corpus/ planted-bug mini repositories with expected.json, and scripts/eval_corpus.py
install.py README.md CHANGELOG.md VERSION
```

Files stay small (≤ 400 lines typical, 800 max), functions ≤ 50 lines, no mutation of
shared state (frozen dataclasses, new dicts), explicit error types, no bare `print` in
library code (the CLI layer prints), no hardcoded paths.

## Tests (write them first)

- unit: scope resolution on temp repos (default-branch detection, merge-base, changes with
  index/worktree overlap, commit scope, limits, empty diff), snapshot + drift, exclusion
  rules (deep `vendor/` source file in changes scope is a target; `locales/*/auth.json` is
  a target; root `vendor/` with `modules.txt` skipped in repo scope), git filter
  neutralisation (a configured clean filter must not run), prompt rendering (no unfilled
  placeholder, no forbidden vocabulary), schema strictness, runner JSONL parsing on
  fixtures (including unknown events), ledger authentication (claimed command that never
  ran fails; wrapper normalisation), gate rules, report rendering, CLI validation.
- integration: `tests/fake_codex/codex` is an executable Python stub on PATH that parses
  the exec flags, reads the prompt, detects the role from a marker line in the brief, emits
  realistic JSONL events (thread.started, command_execution, agent_message, turn.completed)
  and writes a schema-valid `-o` file from a scenario table. Scenarios: happy path with a
  planted bug (finder → triage → verify CONFIRMED → repro reproduced → sweep adds one →
  adjudicate accepts), a verifier REFUTED path, an agent timeout → partial, invalid JSON
  output → retried once then failed, drift during the run → partial.
- coverage target 80 % of `ultrareview/` (`python3 -m coverage` if available, otherwise
  `python3 -m trace --count --summary`).

## Non-goals for this version

No PR posting, no GitHub fetches, no dependency installation, no network calls of its own,
no modification of `config.toml`, no automatic fixes.
