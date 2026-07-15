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
  -> suspended
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
  -> waiting
  -> running
  -> completed | failed | cancelled
```

This is a runtime lifecycle. It is not enough to prove VALP completion.

## Required Mapping

| Runtime state | VALP receipt/state | Required evidence |
|---|---|---|
| queued | no dispatch proof yet | queue item id, assignee, runtime id |
| dispatched | `dispatch_submitted`, only when proof exists | submission proof or runtime claim proof |
| waiting | `suspended` | wait policy, work-item delivery proof, epoch, revision, event/receipt cursors |
| running | `executing` | active run id or status event |
| completed | `dispatch_completed`, only when expected evidence exists | output refs and expected evidence refs |
| failed | `failed` or `blocked` | failure reason and logs |
| cancelled | `cancelled` | actor or policy that cancelled |

## Suspended Waiting

After identity-bound delivery proof exists, `valp wait` may place a state-v2
task in `suspended` and block in the runtime process. This stops coordinator
model turns while workers run; it does not pause workers and does not satisfy a
receipt or evidence gate.

The runtime resumes only for:

```text
all next-step work items completed with valid expected evidence
matching blocked work item
recorded deadline reached
runtime failure with task-local evidence
cancellation
explicit user input
```

The default success barrier is `dependency_ready`. A first completion does not
wake when another required work item remains pending. A blocked work item,
runtime failure, cancellation, timeout, or user input short-circuits into
coordinator handling but does not satisfy missing completion evidence.

The reference CLI validates the authored `wait-policy.json`, snapshots each
strict epoch under `wait-policies/<sha256>.json`, and records revisioned
`state.json.suspension`, authoritative `wait-events.jsonl`, and an immutable
`wake-results/<wake-id>.json`. Historical replay uses the policy snapshot named
by that epoch. Human-readable timeline events mirror the core ledger but are
not the replay authority. Receipt and timeout wakeups are detected by `valp
wait`. Runtime failure, cancellation, and user input each require a closed
`valp-exception-wake.v1` source artifact bound to the current task, suspension
id, and epoch. External events use:

The reference dispatch helper writes a phase-specific root wait policy before
submission. During suspension, the reference wait bridge may observe expected
evidence that was absent at entry and emit an identity-bound completion receipt
citing the original submission proof. It never promotes pre-existing evidence.
When dispatch uses a zero evidence-wait window, the adapter returns after
submission proof without marking missing immediate evidence as blocked; the
wait bridge owns that later completion transition.

The coordinator model is not a watcher. A runtime keeps one blocking
`valp wait` process or event subscription alive; periodic Lead-Agent status
turns are non-conforming because they consume model tokens.

```bash
valp resume TASK-ID --workspace /path/to/workspace \
  --event user_input --ref evidence/wake-requests/user-input.json
valp resume TASK-ID --workspace /path/to/workspace \
  --event cancellation --ref evidence/wake-requests/cancellation.json
valp resume TASK-ID --workspace /path/to/workspace \
  --event runtime_failure --ref evidence/wake-requests/runtime-failure.json
```

The artifact records a principal and reason. Runtime failure also cites a
separate non-empty task-local log or equivalent supporting evidence. The core
hashes the exact artifact bytes into state, event, and result records; changing
the artifact after acceptance fails replay and audit.

An adapter may record `checkpoint_ref` only as an opaque, safe, existing,
non-empty task-local ref. The field does not prove coordinator restorability or
exactly-once continuation invocation. Adapters without such an artifact omit
the field; it is not required for ordinary deterministic wait.

Explicit `user_input` may resume before the recorded deadline. The deadline
controls automatic timeout handling, not deliberate human re-entry.

Only one revision-CAS wake is accepted per suspension epoch. An identical
duplicate returns the recorded result without another event or revision;
stale/cross-task/cross-role/cross-work-item/generation events fail closed.
Repairing a missing projection from a committed wait event is
event-to-projection recovery, including after a process crash. It is not proof
of coordinator restart or continuation restore. A projection without a
committed event is invalid.

`dependency_ready` or user input resumes into `executing`. A blocked work item,
timeout, or runtime failure resumes into `blocked`. Cancellation resumes into
`cancelled`. None of these transitions bypass review, recommendation, approval,
synthesis, or audit gates.

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
