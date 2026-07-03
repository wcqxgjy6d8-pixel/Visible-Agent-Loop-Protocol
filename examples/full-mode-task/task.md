# Task

ID: TASK-EXAMPLE-001
Profile: software-code
Mode: Full Mode

## Goal

Fix a synthetic failing unit test in a sample project and verify the result.

## Expected Evidence

- `agents/codex/evidence.md`
- `agents/claude/review.md`
- `evidence/verification.md`
- `evidence/final-synthesis.md`

## Approval Risks

None. This sample task does not delete files, change secrets, publish, deploy,
or touch production data.

## Done Criteria

- Runtime adapter and provider matrix are recorded.
- Dispatch receipts include `dispatch_completed` for assigned agents.
- Verification evidence exists.
- Review has no unresolved critical/high findings.
- Final synthesis records the result.
