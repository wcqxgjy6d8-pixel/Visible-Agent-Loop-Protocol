# Dispatch: langgraph_coordinator

Task: VALP-NON-HERDR-E2E-001
Profile: agent-runtime
Payload budget: role=coordinator max_chars=3000 max_reference_tokens=750 actual_chars=2949 estimator=ceil(chars/4)

## Project Root

```bash
cd "/workspace/Visible-Agent-Loop-Protocol"
```

## Role

Primary role: `coordinator`. Capability match: coordination; state; visible_synthesis; coordination; state tracking.

## Worker Control Contract

- Load `control-contract.json` and `control-slices/langgraph_coordinator.json` before planning or execution.
- Required digest: `sha256:b4538e48f631e2e6c05fb8db41ba1f9094cd90eb31a2545913295bce1bd1c26c`.
- Missing or mismatched control blocks.

## Task Brief

Coordinate the task-local LangGraph false-done, repair, review, and audit sequence in `task.md`.

## Task References

Load only these task-local refs:

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/task.md`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/context-pack.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/iteration-budget.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/skill-slices/langgraph_coordinator.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/skill-recommendations.json`
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

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/agents/langgraph_coordinator/self-review.md`

## Recommended Skills

- Full recommendation records remain in `skill-recommendations.json`; coordinator-only context.
- Work item 1 `Use a real non-HERDR LangGraph API runtime to produce a reproducible false-done: runtime success while evidence/repor...` -> `product-runtime-eval` (auto-load, confidence 0.669).
- Work item 1 `Use a real non-HERDR LangGraph API runtime to produce a reproducible false-done: runtime success while evidence/repor...` -> `product-operating-layer` (auto-load, confidence 0.542).
- Work item 1 `Use a real non-HERDR LangGraph API runtime to produce a reproducible false-done: runtime success while evidence/repor...` -> `herdr-coordinator-self-review` (auto-load, confidence 0.258).

## Evidence Claim Rule

- Cite task-local proof for build, test, and runtime claims.

## Required Response

Write expected evidence with blockers, confidence, and `## Recommendations`.
