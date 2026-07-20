# Dispatch: langgraph_worker

Task: VALP-NON-HERDR-E2E-001
Profile: agent-runtime
Payload budget: role=implementer max_chars=2800 max_reference_tokens=700 actual_chars=2729 estimator=ceil(chars/4)

## Project Root

```bash
cd "/workspace/Visible-Agent-Loop-Protocol"
```

## Role

Primary role: `implementer`. Capability match: implementation; verification; implementation; artifact generation.

## Worker Control Contract

- Load `control-contract.json` and `control-slices/langgraph_worker.json` before planning or execution.
- Required digest: `sha256:b4538e48f631e2e6c05fb8db41ba1f9094cd90eb31a2545913295bce1bd1c26c`.
- Missing or mismatched control evidence blocks execution.

## Task Brief

First claim completion without evidence. On the repair run, write the exact expected refs in `task.md`.

## Task References

The coordinator/leader owns dispatch precision; load these refs as needed:

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/task.md`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/context-pack.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/iteration-budget.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/skill-slices/langgraph_worker.json`
- Gate contracts: `submission-dependencies.json`, `delegation-policy.json`
- More refs: `automation-policy.json`, `routing.json`, `visible-routing.md`, `context-selection.json`, `mask-list.json`, `evidence-board.json`

## Payload Budget

- Expand only through task-local refs; do not request hidden chat history.

## Visible Attention Slice

- Loop layer: `developer_feedback_loop`
- Your attention head(s): implementation
- Design contract: `missing`
- Role context from `context-pack.json`:
- project: Load the active task and project operating rules from selected task-local refs; do not rely on hidden chat context.
- task_scope: Stay inside the task brief, expected evidence refs, visible routing, and permission boundary recorded for this task.
- Full selection and masks: `visible-routing.md`


## Permission Boundary

- Honor approval gates; cite evidence for runtime facts.
- Do not write skills, plugins, memory, MCP configuration, or agent configuration while delegated.
- Scoped repository edits need permission and must not be live-loaded.
- Write expected evidence only unless source edits are permitted.

## Expected Evidence

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/agents/langgraph_worker/evidence.md`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/evidence/verification.md`

## Recommended Skills

- Use only the provider-reachable skill slice for this dispatch.

## Evidence Claim Rule

- Cite task-local proof for build, test, and runtime claims.

## Required Response

Write expected evidence with blockers, confidence, and `## Recommendations`.
