# The Ultra Review protocol (shared by the CLI driver and the skill)

Phases, each agent a fresh context:

0. **Preflight** — resolve the scope (branch vs base incl. uncommitted, changes, one commit,
   or the repository narrowed by globs), refuse diffs over the limits before spending
   tokens, inventory the target files with hashes, write the diff and the snapshot to the
   run directory.
1. **Map** (large changes and repo scope only) — facts: modules, entry points, trust
   boundaries, storages, contracts, critical flows, test stack. No bug hypotheses.
2. **Find** — independent angles, up to 8 candidates each. Diff angles: line-by-line hunk
   scan, removed-behavior auditor, cross-file tracer, language pitfalls, wrapper/proxy
   correctness. Domain angles: security, state & concurrency, contracts & data, failure &
   resources, tests & integration. Recall over precision: every nameable failure scenario
   goes to verification.
3. **Triage** — deterministic clustering by file and line window, then one agent merges
   same-root-cause reports across clusters. Merge only, never drop.
4. **Verify** — one fresh verifier per cluster: CONFIRMED (inputs + wrong output, quoted
   lines), PLAUSIBLE (mechanism real, trigger uncertain, says what would confirm it) or
   REFUTED (only when constructible from the code: quote, type/invariant, guard in this
   diff, or pure style). Also origin (introduced / pre-existing / unknown) and severity
   P0–P3 with the damage and its conditions.
5. **Reproduce** — for CONFIRMED and PLAUSIBLE findings (up to the cap), a fresh agent in a
   disposable worktree copy that matches the reviewed state runs the real code and
   returns reproduced / not_reproduced / blocked with command, exit code and output.
6. **Sweep** — one fresh finder receives the verified list and hunts only for what is
   missing (dropped guards after moves, flipped defaults, lock scope, fixture asymmetry).
   New candidates go through triage, verification and reproduction.
7. **Adjudicate** — a fresh agent audits findings, verdicts, reproductions and coverage:
   accept, downgrade or reject with quoted reasons; duplicates; origin; ordering; cap. It
   cannot upgrade; its new suspicions get fresh verifiers.
8. **Gate** — deterministic: every quoted line must exist in the snapshot at that line;
   in CLI mode every claimed command must appear in that agent's own execution log
   (`codex exec --json` events); a reproduction whose command is not in the log becomes
   "unverified-evidence"; drift since the snapshot makes the run partial.
9. **Report** — findings by severity with verdict badge, failure scenario, trigger,
   refutations tried, quotes, reproduction, fix and regression test; then unresolved,
   rejected with reasons, coverage table, limitations and the run passport.

Status: `complete` only when no agent failed, nothing drifted and every cluster got a
verdict; otherwise `partial`. Empty scope exits 0 without launching agents.

Exit codes: 0 completed, 1 launch or scope error, 3 completed but partial or with gate
errors, 4 (skill mode) agents needed before the next step.

CLI examples (from a terminal, with the real `codex` on PATH):

```
ultrareview run                              # branch vs default base, profile deep
ultrareview run --scope changes --profile fast
ultrareview run --scope commit --commit HEAD~1 --repro all
ultrareview run --scope repo --paths 'src/auth/**' --jobs 6
ultrareview plan --scope branch --base develop  # no agents, just the plan
```
