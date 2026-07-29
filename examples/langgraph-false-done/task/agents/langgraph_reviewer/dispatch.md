# Dispatch: langgraph_reviewer

Task: VALP-NON-HERDR-E2E-001
Profile: agent-runtime
Payload budget: recorded in `routing.json`.

## VALP Control Contract (Load First)

Load `control-contract.json` first; slice `control-slices/langgraph_reviewer.json`; mismatch blocks.

{"schema_version":"valp-control-slice.v1","task_id":"VALP-NON-HERDR-E2E-001","agent":"langgraph_reviewer","work_item_ids":["reviewer:langgraph_reviewer"],"control_contract_ref":"control-contract.json","control_contract_digest":"sha256:b4538e48f631e2e6c05fb8db41ba1f9094cd90eb31a2545913295bce1bd1c26c","priority_class":"highest_runtime_control","load_before":["planning","skills","tool_execution","immediate_response"],"missing_or_invalid":"block"}

## Project Root

```bash
cd "/workspace/Visible-Agent-Loop-Protocol"
```

## Role

`reviewer`: independent evidence review.

## Task Brief

Independently verify the repaired expected refs and record a digest-backed verdict.

## Task References

The coordinator/leader owns dispatch precision; load these refs as needed:

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/task.md`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/context-pack.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/iteration-budget.json`
- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/skill-slices/langgraph_reviewer.json`
- Gate contracts: `submission-dependencies.json`, `delegation-policy.json`

## Payload Budget

- Expand only through task-local refs; do not request hidden chat history.

## Visible Attention Slice

- Attention head(s): task-local role slice. See `visible-routing.md` and `context-pack.json`.

## Permission Boundary

- Honor approval gates; write only expected evidence and cite runtime proof.
- Do not write skills, plugins, memory, MCP configuration, or agent configuration while delegated.

## Expected Evidence

- `.herdr-loop/tasks/VALP-NON-HERDR-E2E-001/agents/langgraph_reviewer/review.md`

## Recommended Skills

- Use only the provider-reachable skill slice for this dispatch.

## Required Response

Include blockers, confidence, `## Recommendations`, and:

```text
control_contract_ref: control-contract.json
control_contract_digest: sha256:b4538e48f631e2e6c05fb8db41ba1f9094cd90eb31a2545913295bce1bd1c26c
control_contract_status: honored
```
