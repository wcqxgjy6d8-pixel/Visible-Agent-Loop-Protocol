# First False-Done Audit

Command:

```text
bin/valp audit <workspace> --task VALP-NON-HERDR-E2E-001
```

Result:

```text
VALP audit: FAIL
Summary: pass=16 warn=1 fail=9 skip=2
[FAIL] dispatch_receipts: implementer:langgraph_worker=dispatch_blocked; reviewer receipt missing
[FAIL] expected_evidence: agents/langgraph_reviewer/review.md, agents/langgraph_worker/evidence.md, evidence/verification.md
```

The underlying LangGraph run `019f7e44-b84c-7061-80bc-14ebd75c10c3`
reported `success`. VALP event sequence 4 preserved the mismatch as
`dispatch_blocked` because the worker's expected evidence did not exist.

This record predates the repair run and must not be removed or rewritten.
