# Final Synthesis

## Result

The real non-HERDR LangGraph API tracer bullet completed. The first worker run
reported runtime `success` while both expected evidence refs were absent, so
VALP appended `dispatch_blocked`. A second run on the same thread created the
evidence, and a separate reviewer thread verified it. The final audit passed:
`pass=26 warn=0 fail=0 skip=2`.

## Decisions

- Use the LangGraph API `run_id` as the concrete runtime submission ID.
- Keep timeout as a non-terminal coordinator pause; never cancel or mark the
  worker blocked merely because a local wait window expires.
- Emit `dispatch_completed` only from the adapter after every expected ref is
  present and non-empty.
- Preserve the original blocked receipt and runtime snapshots after repair.
- Keep this task to one adapter and leave `SPEC.md` unchanged.

## Disagreements

The runtime and protocol intentionally disagreed after the first worker run:
LangGraph said `success`; VALP said blocked because the report evidence did not
exist. The evidence gate won.

## Evidence Gaps

- This proves the local LangGraph API development runtime, not LangSmith or a
  production deployment.
- The graph is deterministic and exposes no underlying LLM provider/model
  identity; no model-quality claim is made.
- Control-contract generation came from the installed unmerged source and is
  recorded as source drift in `evidence/protocol-source.md`.
- Deterministic coordinator auto-continuation is not claimed; adapter wait
  expiry returns a run-bound resume command.

## Evidence

- First false-done run: `019f7e44-b84c-7061-80bc-14ebd75c10c3`
- Repair run: `019f7e45-7266-75c0-9301-d73f77714f34`
- Shared worker thread: `019f7e44-b84b-7893-bd81-d2b0e6d28a22`
- Independent review run: `019f7e45-aaf0-7f61-8d4c-856a446b5b35`
- Receipt ledger: `dispatch-receipts.jsonl`
- First failing audit: `evidence/first-failure-audit.md`
- Adapter fit: `evidence/adapter-fit.md`
- Worker/review evidence: `agents/langgraph_worker/evidence.md`,
  `evidence/verification.md`, `agents/langgraph_reviewer/review.md`

Final audit command:

```text
bin/valp audit examples/langgraph-false-done/task
```

Result: `PASS`, with no warnings or failures. The two skips are
`deterministic_wake` (not claimed by this adapter case) and `squad_routing`
(not used).
