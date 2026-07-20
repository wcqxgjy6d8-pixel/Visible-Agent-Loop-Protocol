# Visible Routing

Task: VALP-NON-HERDR-E2E-001
Profile: agent-runtime
Loop layer: developer_feedback_loop
Design contract: missing

## Attention Heads

- state_gate: langgraph_coordinator (score 0.8, selected)
- implementation: langgraph_worker (score 0.78, selected)
- ux_review: langgraph_reviewer (score 0.8, selected)
- prototype: none (score n/a, not_selected)

## Selected Context

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/task.md`: active task brief
- `AGENTS.md`: project operating rules
- `pyproject.toml`: Python build/test surface
- `.herdr-loop/local-overlay.json`: workspace-local routing overlay
- `.herdr-loop/agents/capabilities.json`: workspace agent capability scan

## Context Pack

- project: Load the active task and project operating rules from selected task-local refs; do not rely on hidden chat context.
- task_scope: Stay inside the task brief, expected evidence refs, visible routing, and permission boundary recorded for this task.
- verification: Completion claims require concrete files, command output, screenshots, receipts, reviews, or gate evidence.
- permission_boundary: Do not bypass approval gates or expand into release, auth, secrets, destructive, privacy, signing, migration, memory, or agent-configuration changes.
- routing_prior: Historical feedback is a routing prior only; current scan, tools, permissions, context, approvals, and expected evidence override it.

## Masked Inputs

- old chat memory without file-backed evidence: stale context is not valid routing or completion evidence
- hidden votes, hidden reviews, or hidden routing decisions: VALP requires visible decision input
- Agy prototype output as production proof: prototype evidence can inform implementation but cannot satisfy build/test/release gates
- release, signing, upload, deploy, auth, secrets, or destructive changes: high-risk operations require explicit user approval
- invalid, superseded, rejected, or blocked evidence: these evidence statuses do not satisfy done criteria

## Evidence Board

- routing decision is visible: recorded
- selected agents have visible dispatches: needs_dispatch_completion
- runtime or build success: not_yet_claimed
