# Dispatch: langgraph_worker

Task: VALP-NON-HERDR-E2E-001
Profile: agent-runtime
Payload budget: recorded in `routing.json`.

## VALP Control Contract (Load First)

Load `control-contract.json` first; slice `control-slices/langgraph_worker.json`; mismatch blocks.

{"schema_version":"valp-control-slice.v1","task_id":"VALP-NON-HERDR-E2E-001","agent":"langgraph_worker","work_item_ids":["implementer:langgraph_worker"],"control_contract_ref":"control-contract.json","control_contract_digest":"sha256:b4538e48f631e2e6c05fb8db41ba1f9094cd90eb31a2545913295bce1bd1c26c","priority_class":"highest_runtime_control","load_before":["planning","skills","tool_execution","immediate_response"],"missing_or_invalid":"block"}

## Project Root

```bash
cd "/workspace/Visible-Agent-Loop-Protocol"
```

## Role

`implementer`: implementation and verification.

## Task Brief

First claim completion without evidence. On the repair run, write the exact expected refs in `task.md`.

## Task References

The coordinator/leader owns dispatch precision; load these refs as needed:

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/task.md`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/context-pack.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/iteration-budget.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/skill-slices/langgraph_worker.json`
- Gate contracts: `submission-dependencies.json`, `delegation-policy.json`

## Payload Budget

- Expand only through task-local refs; do not request hidden chat history.

## Visible Attention Slice

- Implementation slice: `visible-routing.md`, `context-pack.json`.


## Permission Boundary

- Honor approval gates; write only expected evidence and cite runtime proof.
- Do not write skills, plugins, memory, MCP configuration, or agent configuration while delegated.

## Expected Evidence

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/agents/langgraph_worker/evidence.md`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/evidence/verification.md`

## Recommended Skills

- Use only the provider-reachable skill slice for this dispatch.

## Required Response

Include blockers, confidence, `## Recommendations`, and:

```text
control_contract_ref: control-contract.json
control_contract_digest: sha256:b4538e48f631e2e6c05fb8db41ba1f9094cd90eb31a2545913295bce1bd1c26c
control_contract_status: honored
```
