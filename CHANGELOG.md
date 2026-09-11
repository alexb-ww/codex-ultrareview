# Changelog

## 0.2.0 — 2026-09-11

New implementation, replaces the 0.1.0 prototype.

- Driver mode: every role is its own `codex exec --json --output-schema -o` process, so
  fresh context is guaranteed and every command an agent runs is in its event log.
- Phases: map (large changes) → find (10 angles: 5 diff-specific, 5 domain) → triage
  (deterministic + agent, merge-only) → verify (CONFIRMED / PLAUSIBLE / REFUTED, recall
  biased) → reproduce (disposable worktree copy, workspace-write) → sweep (gaps only) →
  reproduce → adjudicate (cannot upgrade; suspicions re-verified) → gate → report.
- Gate authenticates quotes against the snapshot (worktree, index, base or commit
  version) and commands against the agent's own ledger; unauthenticated reproductions
  become "unverified-evidence"; drift makes the run partial.
- Scope: branch (default, base auto-detected: origin/HEAD, main, master), changes
  (index and working tree kept apart when both differ), commit, repo with globs; limits
  500 files / 8 000 lines refused before any token is spent; empty diff exits 0.
- Exclusions fixed: changed paths are never skipped; dependency dirs are skipped in repo
  scope only at the root or with a marker; no basename heuristics such as `auth.json`.
- Skill mode: `step` replays recorded outputs and prints the next batch; the coordinator
  spawns sub-agents with `fork_turns: "none"`; commands are not authenticated there and
  the report says so.
- Skill helpers: `record` stores a sub-agent's final JSON after validating it against
  that agent's schema; `--note` passes the user's free text to every brief as a
  priority; `--repro-sandbox danger-full-access` lets reproducers use system build
  caches (needed for Go under the no-private-caches rule).
- Reproduction copies are built from the working-tree patch captured at snapshot time
  (`state.patch`), so edits made during the review do not leak into reproduction.
- Hooks: optional PreToolUse guard denying edits while a review marker exists.
- Corpus and scorer: `corpus/build.py`, `scripts/eval_corpus.py`.
- Closed after an independent Codex review of the build: content filters are blanked on
  every git call (`status` included) and pathspecs are literal; every file read walks the
  path with `O_NOFOLLOW` so a symlinked directory cannot redirect a read; the worktree
  patch goes to `git apply` over stdin; `.env*` means every name starting with `.env`;
  the staged (index) diff is redacted like the main diff and counted against the limits;
  `changes` scope records HEAD as its base so deleted code stays quotable; the gate checks
  the evidence of every verdict (a refutation whose evidence fails is set aside instead of
  burying the bug), binds a reproduction to the logged exit code, accepts no finding with
  evidence problems and never reports `complete` with them; command claims must equal a
  logged command or one of its `&&`/`;` segments, quotes must equal the line; the sweep
  attaches a candidate only to a surviving finding of the same category; the installed
  hook path is correct and `step` maintains the guard marker; when a sandbox refuses
  `git worktree add`, reproduction copies fall back to a plain `git archive` copy.
- Distribution: the repository is a Codex plugin marketplace (`.agents/plugins/marketplace.json`,
  `.codex-plugin/plugin.json`, skill under `skills/ultrareview`); `codex plugin marketplace add
  alexb-ww/codex-ultrareview` + `codex plugin add ultrareview@codex-ultrareview` installs it and the
  skill is invoked as `$ultrareview:ultrareview`. The plan and the run passport show the model and
  effort that actually apply (flag, config.toml or default); the skill accepts `model=` / `effort=`.
- Tests: 150+ unit and integration tests against a fake `codex` binary; coverage 93 % of
  the package.
