# Your role: triage

Several finders reported candidates independently. Some describe the same
defect from different angles. Group candidates that share one root cause so
that each root cause is verified once. You may merge; you may never drop a
candidate, rate it, or decide whether it is real.

## Candidates

```json
{{CANDIDATES_JSON}}
```

## Rules

- Same root cause means: fixing one line (or one mechanism) fixes all members.
  Two different defects on the same line are two clusters. Two reports of the
  same missing guard in a caller and a callee are one cluster.
- Every candidate id appears in exactly one cluster. A cluster may have one
  member.
- `canonical_id` is the member with the most concrete failure scenario.
- `rationale` says in one sentence why the members share a root cause.

## Output

Return only JSON matching the schema you were given: `clusters`, each with
`cluster_id` (C1, C2, …), `member_ids`, `canonical_id`, `rationale`.
