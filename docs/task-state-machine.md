# Task State Machine

VALP separates two concepts:

`VALP task`
: The user-published unit of work with evidence, routing, receipts, review, and
done criteria.

`runtime work item`
: A runtime-owned unit of work, such as a queue item, pane submission,
issue-triggered run, chat-triggered run, scheduled run, or retry. Older VALP
drafts may call this an `execution task`.

The two must be mapped. They are not the same object.

## VALP State Machine

```text
new
  -> published
  -> scanning_capabilities
  -> scanning_context
  -> loading_local_overlay
  -> selecting_runtime_adapter
  -> classifying_task
  -> selecting_profile
  -> decomposing_tasks
  -> recommending_skills
  -> building_provider_matrix
  -> scoring_routes
  -> routing_capabilities
  -> routing_squad
  -> dispatching
  -> planned
  -> locked
  -> executing
  -> verifying
  -> reviewing
  -> resolving_agent_recommendations
  -> fixing
  -> approval_required
  -> recording
  -> done | blocked | failed | cancelled
```

## Common Runtime Queue State

Daemon and managed-agent runtimes often expose:

```text
queued
  -> dispatched
  -> running
  -> completed | failed | cancelled
```

This is a runtime lifecycle. It is not enough to prove VALP completion.

## Required Mapping

| Runtime state | VALP receipt/state | Required evidence |
|---|---|---|
| queued | no dispatch proof yet | queue item id, assignee, runtime id |
| dispatched | `dispatch_submitted`, only when proof exists | submission proof or runtime claim proof |
| running | `executing` | active run id or status event |
| completed | `dispatch_completed`, only when expected evidence exists | output refs and expected evidence refs |
| failed | `failed` or `blocked` | failure reason and logs |
| cancelled | `cancelled` | actor or policy that cancelled |

## Retry Semantics

Retries must record whether the failure was:

```text
runtime_offline
runtime_recovery
timeout
idle_watchdog
agent_error
approval_denied
context_policy_block
evidence_missing
manual_cancelled
```

Automatic retry is an adapter feature, not a VALP done signal. A retried task
still needs fresh receipts and expected evidence.

When a retry, rejection, blocked dispatch, invalid evidence, or superseded
evidence affects task completion, the task should also write
`correction-cycle.json`. The correction cycle records why work was rejected, who
owned the fix, which evidence was replaced, and whether the final outcome was
`fixed`, `blocked`, `escalated`, or `cancelled`. Only `fixed` can satisfy Done
Criteria.

## Recommendation Resolution

After selected agents produce evidence or review output, the coordinator must
resolve meaningful next-step suggestions before recording Done.

This is not an unlimited loop. The coordinator should record a task-local
complexity policy:

```text
max_recommendation_rounds
max_new_dispatches_without_user_approval
current_scope
stop_conditions
```

Each meaningful recommendation is adopted into the visible decision process as
one of:

```text
accepted
merged
scoped_followup
bounded_no_action
escalated
```

`accepted` and `merged` can create new dispatches, correction entries,
verification, review, or final-synthesis updates. `scoped_followup` records
valid work outside the current task. `bounded_no_action` is only for duplicate,
already-satisfied, non-actionable, or complexity-increasing recommendations.

The important invariant is: no selected-agent recommendation disappears into
the leader's private judgment.

## Session Resume

Some providers can resume prior sessions or threads. This is useful but risky.

VALP requires the adapter to record:

```text
resume_supported
resume_used
resume_id_or_redacted_ref
reason_for_resume
reason_for_fresh_session
```

Manual reruns after bad output should usually start fresh. Infrastructure
retries may resume when the provider supports it and context policy allows it.

## Completion Rule

A runtime can finish a runtime work item without finishing a VALP task.

VALP is done only when receipts, expected evidence, review, approval gates, and
agent-recommendation resolution, and final synthesis are recorded.

## Routing Feedback State

For non-trivial tasks, the final recording phase should also write routing
feedback when the runtime supports it. Feedback is not a completion shortcut; it
is a memory artifact for future routing.

```text
recording
  -> write final synthesis
  -> write routing feedback, if useful
  -> append workspace routing feedback index, if configured
  -> done | blocked | failed | cancelled
```
