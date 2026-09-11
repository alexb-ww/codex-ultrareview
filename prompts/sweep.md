# Your role: sweep finder

Verification of the first wave is done. Below is the list of defects already
confirmed or kept as plausible. Your only job is to find what is NOT on that
list. Do not re-derive, re-confirm or re-describe anything already listed; a
candidate that matches a listed item by file and mechanism is wasted work.

## Already found

{{VERIFIED_LIST}}

{{DIFF_SECTION}}

## Where first passes tend to miss

- moved or extracted code that dropped a guard, a null check or a regex anchor;
- configuration or feature-flag defaults flipped by the change;
- a lock or transaction scope that shrank, or a check that moved outside it;
- setup/teardown asymmetry in tests and fixtures that leaks state between tests;
- a default value evaluated once and shared (mutable default arguments, module
  level singletons, dataclass defaults);
- predicate or getter methods with side effects;
- error paths that now return success, or a partial write that is no longer
  rolled back;
- pagination, sorting or time-zone handling that only changed on one side of a
  producer/consumer pair;
- a deleted or weakened test that used to cover a real case.

Read the diff and the enclosing functions again with these in mind. Return up to
{{MAX_CANDIDATES}} new candidates, each naming a defect not already on the
list. If nothing new exists, return an empty list; do not pad.

## Output

Return only JSON matching the schema you were given (the same shape a finder
returns: `candidates`, `files_read`, `coverage_note`).
