---
name: ultrareview
description: Deep multi-agent code review with independent verification, reproduction in a disposable copy and an evidence gate. Use only when the user explicitly asks for $ultrareview or an "ultra review". Review-only; never edits project files.
---

# Ultra Review (skill mode)

This skill runs the same protocol as the `ultrareview` command-line driver, with one
difference: the agents are your native sub-agents. Everything deterministic (scope,
snapshot, briefs, clustering, gate, report) is done by the bundled program; your job is
to dispatch agents with a clean context and record their answers verbatim.

## Arguments after `$ultrareview`

`scope=branch|changes|commit|repo` (default branch), `base=<ref>`, `commit=<sha>`,
`paths=<glob,glob>` (repo scope), `profile=fast|standard|deep` (default deep),
`repro=auto|off|all`, `votes=<n>`, `lang=en|ru`. Anything else the user writes is a note:
relate the findings to it when you present them; do not put it into agent messages.

## Procedure

1. Resolve the skill directory from the path of this SKILL.md; the program is
   `<skill-dir>/scripts/ultrareview.py`. Choose a run directory outside the repository:
   `RUN="$TMPDIR/ultrareview/$(date -u +%Y%m%dT%H%M%SZ)"`.
2. Run the step command, mapping the arguments to flags:
   `python3 "<skill-dir>/scripts/ultrareview.py" step --repo . --run-dir "$RUN" --scope branch --base main --profile deep`
   (drop flags the user did not set; the program picks the base branch itself).
   Print its plan lines to the user once.
3. Exit code 4 means agents are needed. The output lists them and `$RUN/pending.json`
   holds the manifest (agent id, role, brief path, schema path, output path, sandbox,
   cwd). For every listed agent call `spawn_agent` with:
   - `task_name`: the agent id with dashes replaced by underscores;
   - `fork_turns`: `"none"` — mandatory; the agent must not inherit this conversation;
   - `message`, exactly: `You are agent <id> in an Ultra Review. Read the brief file
     <brief_path> completely and follow it; it names the repository, the files and the
     rules. Your final message must be ONLY a JSON object matching the JSON schema in
     <schema_path>: no prose, no code fences.`
   Do not add hints, prior findings or context of your own: independence is the point.
   Keep at most four agents active at a time. Reproducer agents work inside the worktree
   copy named in their brief; that copy is the only place anyone may write.
4. Wait with `wait_agent` (timeout_ms 600000; repeat while agents are running; a wait
   timeout is not completion). When an agent finishes, store its final message through
   the program, never by hand: write the message to a temp file with a quoted heredoc
   (`cat > "$RUN/tmp-<id>.json" <<'JSON' … JSON`) and run
   `python3 "<skill-dir>/scripts/ultrareview.py" record --run-dir "$RUN" --agent-id <id> --json-file "$RUN/tmp-<id>.json"`.
   It validates the JSON against that agent's schema. If it reports invalid JSON or a
   schema mismatch, ask that agent once with `send_input` to resend only the JSON object
   and record again; if it still fails, leave the stored answer as is: the program counts
   the agent as failed and continues. Close finished agents if a close tool exists.
5. Run the identical step command again. Repeat 3–5 until the exit code is not 4.
   Exit 0 or 3: the report is printed and saved at `$RUN/report.md` and
   `$RUN/report.json`; present it (findings first, then unresolved, rejected, coverage,
   limitations, run passport), in the user's language, keeping paths and code as is.
   Exit 1: show the program's message; it is a scope or launch problem, not a finding.

## Rules

- Never edit, create, stage, commit, checkout or reset files in the repository; never
  install dependencies; never post results anywhere. Only the run directory and the
  worktree copies the program creates are writable.
- Do not run `codex exec`, do not review the code yourself instead of dispatching, do
  not merge or reword agent JSON, and do not paste one agent's output into another's
  message. The program feeds later agents exactly what they need.
- If `spawn_agent` is not available, say `MULTI_AGENT_UNAVAILABLE`, run one careful pass
  yourself, and label the result as a single-agent review with no independent
  verification.
- In this mode the commands agents claim to have run are not authenticated against an
  execution log; the report states that. For a fully authenticated review, the user can
  run `ultrareview run` from a terminal (see `references/protocol.md`).
