# Dispatch: langgraph_reviewer

Task: VALP-NON-HERDR-E2E-001
Profile: agent-runtime
Payload budget: role=reviewer max_chars=2400 max_reference_tokens=600 actual_chars=2275 estimator=ceil(chars/4)

## Project Root

```bash
cd "/workspace/Visible-Agent-Loop-Protocol"
```

## Role

Primary role: `reviewer`. Capability match: review; code_review; risk_review; review; independent verification.

## Worker Control Contract

- Load `control-contract.json` and `control-slices/langgraph_reviewer.json` before planning or execution.
- Required digest: `sha256:b4538e48f631e2e6c05fb8db41ba1f9094cd90eb31a2545913295bce1bd1c26c`.
- Missing or mismatched control evidence blocks execution.

## Task Brief

Independently verify the repaired expected refs and record a digest-backed verdict.

## Task References

The coordinator/leader owns dispatch precision; load these refs as needed:

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/task.md`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/context-pack.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/iteration-budget.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/skill-slices/langgraph_reviewer.json`
- Gate contracts: `submission-dependencies.json`, `delegation-policy.json`
- More refs: `automation-policy.json`, `routing.json`, `visible-routing.md`, `context-selection.json`, `mask-list.json`, `evidence-board.json`

## Payload Budget

- Expand only through task-local refs; do not request hidden chat history.

## Visible Attention Slice

- Attention head(s): task-local role slice. See `visible-routing.md` and `context-pack.json`.

## Permission Boundary

- Honor approval gates; cite evidence for runtime facts.
- Do not write skills, plugins, memory, MCP configuration, or agent configuration while delegated.
- Scoped repository edits need permission and must not be live-loaded.
- Write expected evidence only unless source edits are permitted.

## Expected Evidence

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/agents/langgraph_reviewer/review.md`

## Recommended Skills

- Use only the provider-reachable skill slice for this dispatch.

## Evidence Claim Rule

- Cite task-local proof for build, test, and runtime claims.

## Required Response

Write expected evidence with blockers, confidence, and `## Recommendations`.
