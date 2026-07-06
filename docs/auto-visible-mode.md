# Auto Visible Mode

Auto Visible Mode is VALP's opt-in automatic intake path. It lets a coordinator
or runtime decide that a user task should enter the visible multi-agent loop
without requiring the user to type the exact `valp publish` command each time.

It is not silent execution. The first visible artifact is the trigger decision:
why VALP started, which policy matched, which task id was created, what risk was
detected, and what evidence will be required.

## Trigger Modes

| Mode | What starts it | Typical user |
|---|---|---|
| `manual` | user explicitly runs `valp publish` or asks to use VALP | first-time user |
| `policy_auto` | project instructions or local overlay match the request | experienced user |
| `watcher` | issue label, queue item, schedule, file event, or runtime API | advanced runtime |
| `disabled` | VALP should not run for this request | any user |

New installs should default to `manual`. `policy_auto` and `watcher` are opt-in
because they can start work without another explicit command.

For App-managed installs, the App should not enable `policy_auto` or `watcher`
on first launch. It should first run the install health gate: doctor, runtime
preflight when Full Mode is requested, and a publish/dispatch dry run whose
result is shown to the user before real `--submit` is enabled.

## Flow

```text
user task or runtime signal
  -> evaluate trigger policy
  -> classify risk
  -> publish VALP task when policy allows
  -> record trigger-policy.json
  -> scan runtime, tools, skills, and context
  -> run skill recommendation when available
  -> write visible routing and dispatches
  -> execute only within approval boundaries
  -> verify, review, and record final report
  -> audit the task evidence
```

The task may proceed automatically only as far as the evidence and approval
gates allow. If the trigger policy chooses `publish_only` or
`block_for_approval`, the runtime must stop there.

## Trigger Evidence

Auto Visible Mode should write:

```text
.herdr-loop/tasks/<task-id>/trigger-policy.json
```

Minimum fields:

```json
{
  "schema_version": "valp-trigger-policy.v1",
  "trigger_mode": "policy_auto",
  "trigger_source": "project_policy",
  "matched_signal": "task mentions VALP multi-agent visible collaboration",
  "rule_ref": "AGENTS.md#valp",
  "risk_classification": "low",
  "selected_action": "publish_route_and_dispatch",
  "approval_required": false,
  "visible_refs": {
    "task": ".herdr-loop/tasks/TASK-001/task.md",
    "routing": ".herdr-loop/tasks/TASK-001/visible-routing.md",
    "skills": ".herdr-loop/tasks/TASK-001/skill-recommendations.json",
    "report": ".herdr-loop/tasks/TASK-001/final-synthesis.md"
  }
}
```

## Approval Boundary

Auto Visible Mode must never treat automatic trigger as automatic permission.

The following require explicit user approval before execution:

```text
delete
auth
secrets
memory
agent_config
mcp_config
signing
entitlements
data_migration
destructive_reset
publish
release
upload
submit
deploy
pricing
metadata
privacy
external_private_data
```

A runtime may still publish and route the task, but it must stop before the
high-risk action and record `block_for_approval`.

## Final Report

The final report should be readable by the user and include:

```text
task id
trigger mode and matched rule
risk classification and approval status
unexecuted high-risk scope when `block_for_approval` is selected
selected agents and why they were selected
skill recommendations used or skipped
dispatch receipt status
expected evidence paths
verification commands or artifacts
review findings and fixes
known gaps or blockers
audit result
```

This makes automatic work inspectable: the user can see why VALP ran, what it
did, which agents participated, and what evidence proves the result.

## Non-Goals

Auto Visible Mode does not:

- run hidden agent votes;
- bypass approval gates;
- treat inserted text as delivery;
- mark runtime completion as VALP completion without expected evidence;
- require a background watcher;
- require HERDR specifically.
