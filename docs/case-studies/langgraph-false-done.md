# LangGraph: Runtime Success Was Not Done

A real LangGraph API run returned `success` with run ID
`019f7e44-b84c-7061-80bc-14ebd75c10c3`. Its output claimed that the report had
been generated. The two expected refs did not exist:

```text
agents/langgraph_worker/evidence.md
evidence/verification.md
```

VALP did not translate runtime success into task completion. Receipt event 4
recorded `dispatch_blocked` with `missing_expected_evidence`, and the first task
audit remained preserved at `pass=16 warn=1 fail=9 skip=2`.

The repair reused worker thread
`019f7e44-b84b-7893-bd81-d2b0e6d28a22`. Repair run
`019f7e45-7266-75c0-9301-d73f77714f34` created both expected refs. The adapter,
not a human, then appended `dispatch_completed` after checking that the files
were non-empty.

A separate LangGraph reviewer run
`019f7e45-aaf0-7f61-8d4c-856a446b5b35` verified the report marker and digest.
The final sanitized task audit is:

```text
PASS: pass=26 warn=0 fail=0 skip=2
```

The complete task, runtime snapshots, receipt ledger, conformance report, and
reproduction script live in `examples/langgraph-false-done/`. The wait timeout
used by the adapter is an observation window: expiry returns `waiting`, retains
the original run identity, and does not cancel the worker.

This proves one local LangGraph API adapter/runtime pair. It does not claim a
production LangSmith deployment, deterministic coordinator auto-continuation,
or model-quality performance.
