# Visible Agent Loop Protocol Specification

Version: 0.3.0

Status: normative stable specification. Public repository promotion and release
distribution are separate delivery gates; this document is the source of truth
for the `0.3.0` protocol contract.

## 1. Purpose

Visible Agent Loop defines a visible, auditable, evidence-backed control
protocol for autonomous and multi-agent work.

The protocol starts from a first-principles question: when an intelligent
system claims a task is done, what evidence makes that claim trustworthy?
VALP answers by making intent, routing, execution, evidence, correction,
approval, and learning visible.

VALP is a protocol for commissioning capability facts and controlling an
evidence-backed task loop. It does not choose an Agent on the user's behalf.
The user selects the installation Leader; that Leader decomposes tasks and
declares worker and reviewer assignments. VALP validates those declarations,
records the decision and evidence, controls dispatch gates, audits the result,
and returns control to the user.

Repository hosting and contribution operations, including branch creation,
push, pull request, review service, and merge, are outside the general task
lifecycle. A repository-specific profile or maintainer workflow may use those
operations after VALP returns the audited result, but the protocol must not
assume that a user's work is hosted on GitHub or any other forge.

The protocol is generic. It can be used for software engineering, research,
frontend work, Apple apps, documents, operations, prototypes, and future task
profiles.

## 2. Terms

`agent`
: A local or remote AI worker with a role, tool access, context limits, and
permission boundaries, backed by a model provider, runtime provider, or manual
operator.

`runtime`
: A system that can list agents, inspect status, send visible dispatches,
submit agent sessions/messages, wait for state, and read output.

`runtime adapter`
: The compatibility layer that maps a concrete runtime, such as an agent-session
controller, daemon queue, hosted agent platform, or manual workflow, into VALP
receipts and evidence.

`agent session`
: A visible or addressable interaction channel for one agent, such as a
terminal pane, hosted-agent thread, queue worker, or manually copied dispatch.

`bootstrap surface`
: The user-controlled CLI, App, installer, or manual surface that initializes
an installation, runs Doctor, records the user's Leader selection, and requests
Leader startup at epoch `0`. A bootstrap surface is not the Installation Leader
and its private reasoning, current session, focus, label, or working directory
cannot establish Leader identity.

`Installation Leader session`
: The single installation-owned Agent session activated for the current Leader
epoch after explicit user selection and successful runtime provisioning. Its
authority comes from the selection, epoch, exact runtime binding, health proof,
and activation receipt together, not from an Agent product name or UI surface.

`worker`
: Any Agent session created or assigned by the Installation Leader for task
execution, research, prototyping, verification, review, or delegated
coordination. A worker remains task- or project-owned and cannot inherit
installation authority or the Leader epoch, including when it uses the same
Agent product or provider as the Leader.

`pane`
: A terminal split or equivalent visible UI surface used by a pane-controller
runtime adapter. A pane is one possible agent session type, not a protocol
requirement.

`task`
: A user-published unit of work with a task id and evidence folder.

`runtime work item`
: A runtime-owned unit of agent work, such as a queue item, issue-triggered run,
chat-triggered run, scheduled run, hosted-agent thread, or pane-submitted prompt.
A runtime work item is not the same as a VALP task; it must be mapped into VALP
evidence. Older documents may call this an `execution task`.

`profile`
: A domain adapter that defines gates and evidence for a task type, such as
`software-code`, `research`, `apple-app`, or `web-frontend`.

`local overlay`
: Operator or workspace-specific configuration layered on top of the open
protocol. It may describe local agents, paths, runtimes, habits, and defaults,
but it cannot override non-negotiable gates.

`capability profile`
: A remembered or configured description of an agent's likely strengths,
boundaries, tools, and context policy. It is a routing hint, not a fixed role or
assignment.

`capability passport`
: An installation-wide Doctor record for one Agent surface and addressable
session. It separates official claims, local presence, live callability, and
task-verified history; records declared and observed model identity; and lists
reachable Skills, MCP/tools, permissions, context, limitations, and role
eligibility. A passport is evidence for a Leader's decision, not an assignment.

`Leader`
: The Agent or human explicitly selected by the user to own task decomposition,
assignment declarations, visible decisions, gates, and final synthesis. VALP
MUST record and validate the user's selection, but MUST NOT select, replace, or
rotate the Leader on its own. Selecting an Agent records user intent but does
not activate it; Agent leadership starts only after the exact installation-owned
Leader session is provisioned, bound, health-checked, and activated for a
non-zero epoch.

`coordinator`
: The control surface that maintains visible state, receipts, gates, handoffs,
and final synthesis for one task. It may be the user-selected Leader or a
runtime service acting under that Leader's declared assignments. A coordinator
is not a second authority that may select Agents independently.

`assignment declaration`
: The Leader-authored, task-local mapping from runtime work items and roles to
specific Agent surfaces or humans, including the reason, expected evidence,
and any accepted capability limitations. VALP validates and records the mapping
but does not author it.

`routing confidence`
: A recorded estimate of how strongly current capability evidence supports a
Leader-declared assignment and how serious any rejected or accepted alternative
risk is. It can block or downgrade an assignment, but cannot choose a different
Agent.

`feedback record`
: A post-task record of what actually happened: Leader-declared Agents, evidence
quality, review outcomes, blockers, completion result, and lessons for future
routing. Feedback records improve future routing but do not replace current
capability scans.

`control loop`
: The full closed loop from intent to routing, execution, evidence, review,
correction, approval, synthesis, audit, and learning. A runtime may automate
parts of the loop, but the protocol boundary is the evidence trail that proves
which parts ran and which gates stopped.

`automation policy`
: A task-local record of which phases may continue automatically, which phases
must stop, the risk classification, the approval requirement, and the audit
grade needed for the task. Automation policy allows low-risk progress; it does
not grant permission for high-risk work.

`context pack`
: A compact, task-local package of selected project rules, operator
preferences, known pitfalls, task scope, verification expectations, and
permission boundaries. It is derived from visible refs and is included in
dispatch prompts by reference, not by copying private transcripts.

`learning feedback`
: A post-task record of evidence-backed observations and proposed updates for
future routing, context selection, automation policy, docs, schemas, adapters,
or local overlays. Learning feedback is a prior for future tasks, not proof
that the same route is valid now.

`audit grade`
: The evidence standard claimed for a task or example. Common grades are
`demo`, `local`, `runtime`, and `public-proof`. A higher grade requires stronger
external proof; a lower grade must not be marketed as deployment reliability.

`evidence-based prior`
: A routing or automation hint derived from previous task evidence. It may
increase or decrease confidence, but current runtime status, tool availability,
permission boundaries, context policy, approval gates, and expected evidence
always win.

`provider matrix`
: A current capability table for agent backends, including CLI availability,
MCP support, skill discovery path, session resume support, approval behavior,
context policy, and known limitations.

`squad`
: A named group of agents and optional humans, optionally with a selected leader
agent that routes work to members. Squad routing is optional and must remain
visible.

`dispatch`
: A visible assignment sent to an agent.

`dispatch payload budget`
: A machine-readable per-role ceiling for the complete worker dispatch. It
records maximum characters, a deterministic reference-token estimate, and the
task-local references used for progressive disclosure. It is not permission to
omit role boundaries, expected evidence, or gate requirements.

`task iteration budget`
: A task-local machine-readable ceiling for aggregate dispatch reference
  tokens, dispatch submissions, reroutes, and fix-review rounds. The runtime
  records observed usage and stops before a new dispatch would exceed a limit
  or a safety gate. It is a control-plane budget, not a provider tokenizer
  claim.

`task cost budget`
: A task-local optional ceiling for a named estimate basis. It is not a price
  quote, invoice, authorization to spend, or evidence of an actual charge.

`pricing snapshot`
: A versioned provider-neutral input-rate record. Official list price,
  relay/account price, and subscription marginal cost are separate categories
  and MUST NOT be substituted for one another.

`billing event`
: An append-only task-local record of a charge supported by billing evidence.
  A cost report MUST NOT present an actual billed amount unless such evidence
  exists. Missing billing evidence means `actual_billed: null`, not zero.

`provider-reachable skill slice`
: A compact per-agent recommendation artifact containing only installed skills
  reachable by that provider and short task labels. The complete skill router
  report remains coordinator-only; a worker must not receive another
  provider's private or unreachable skill paths.

`submission dependency`
: A machine-readable role-to-role gate that requires recorded prerequisite
  evidence and a qualifying completion receipt before a dependent dispatch may
  be submitted. Receipt ledger line order, not timestamps, determines whether
  the dependency was satisfied.

`work item identity`
: The task-scoped tuple of `work_item_id`, agent, role, dispatch id, and
  dispatch generation that identifies one delegated phase. Agent identity by
  itself is not a work item identity and cannot qualify a deterministic wake.

`dependency-ready barrier`
: The default successful wait condition. It is satisfied only when every work
  item needed by the next coordinator step has a matching completion receipt
  and valid expected evidence. Unrelated parallel work does not join the
  barrier.

`exception short circuit`
: A failure, cancellation, deadline, or explicit user-input event that wakes
  the coordinator for visible handling before the success barrier is ready. It
  never marks a pending work item complete or satisfies a completion gate.

`accepted wake`
: The single revision-CAS transition committed for one suspension epoch. It is
  identified by a stable wake id, accepted event sequence, result ref, and the
  exact event or receipt that caused it.

`delegated self-modification`
: A delegated principal changing live-loaded skills, plugins, memory, MCP
configuration, or agent configuration for itself or another current task
participant. Repository source, protocol, policy, schema, test, and
documentation edits are not delegated self-modification when the task
explicitly permits them and the changed files are not live-loaded by the
current task.

`historical audit boundary`
: A hash-pinned compatibility decision that lets a later auditor evaluate an
immutable terminal task created before a named audit rule existed. It records
the exact rule, artifact bytes, recorded facts, observed facts, source
revisions, and external reconciliation evidence. It does not make the old
artifact conform to the later rule.

`suspended waiting`
: A non-terminal coordinator state entered after dispatch submission while
  workers run. The runtime waits without invoking another coordinator model turn
  and returns only after one accepted wake is committed for the current
  suspension epoch. A task status rewrite without that accepted wake is not a
  resume event.

`receipt`
: A machine-readable record of dispatch state.

`evidence`
: Files, logs, screenshots, command outputs, reviews, findings, and synthesis
used to prove progress or completion.

`correction cycle`
: A task-local record of rejected, blocked, invalid, or superseded work and the
follow-up round that fixed, blocked, escalated, or cancelled it. A correction
cycle is evidence of self-correction; it is not the self-correction engine.

`provider-filtered skill recommendation`
: A skill recommendation result generated for one target agent, using that
agent's reachable provider or skill library filter. Provider-filtered results
are preferred in dispatch prompts because task-level aggregate results can
contain skills owned by other providers.

`agent recommendation`
: A task-local recommendation, next step, follow-up risk, or proposed action
reported by a dispatched agent after doing its assigned work. Agent
recommendations are not commands. The coordinator must record whether each
meaningful recommendation is adopted into the task plan, merged into existing
work, converted into a follow-up, explicitly bounded, or escalated. Adoption is
not blind execution; it is visible disposition plus scope control.

`trigger policy`
: A local, workspace, or runtime rule that decides whether a user request,
issue, queue item, scheduled run, or other signal should publish a VALP task.
Trigger policy is intake evidence. It cannot weaken dispatch, evidence, review,
or approval gates.

`Auto Visible Mode`
: An opt-in intake behavior where a coordinator or runtime automatically decides
that a request should enter VALP, publishes the task, and immediately surfaces
the trigger reason and evidence gates. It may surface routing, skill
recommendations, and dispatches only after a user-selected Leader supplies a
valid assignment declaration. It is automatic entry, not silent execution.

## 3. Lifecycle

VALP defines a small set of phases, with finer sub-steps inside each phase. A
runtime may persist the phase, the sub-state, or both, but it must always export
enough evidence to satisfy the Done Criteria.

High-level phases:

```text
COMMISSION INSTALLATION
  -> USER SELECTS LEADER
INTAKE
  -> REFRESH
  -> LEADER ASSIGNS
  -> VALIDATE
  -> DISPATCH
  -> EXECUTE
  -> REVIEW
  -> RECORD
  -> AUDIT
  -> RETURN TO USER AS DONE / BLOCKED / FAILED / CANCELLED
```

Expanded sub-steps:

```text
COMMISSION INSTALLATION
  -> RUN DOCTOR ACROSS KNOWN AGENT SURFACES AND SESSIONS
  -> WRITE CAPABILITY PASSPORTS
  -> USER SELECTS LEADER
  -> START AND BIND INSTALLATION-OWNED LEADER SESSION
  -> ACTIVATE LEADER EPOCH
INTAKE
  -> PUBLISH
  -> SELECT AUTOMATION POLICY
REFRESH
  -> REFRESH VOLATILE MODEL, SESSION, RUNTIME, PERMISSION, AND CONTEXT FACTS
  -> LOAD CAPABILITY PASSPORTS AND LOCAL OVERLAY
  -> SELECT RUNTIME ADAPTER
LEADER ASSIGNS
  -> CLASSIFY TASK
  -> SELECT PROFILE
  -> DECOMPOSE INTO RUNTIME WORK ITEMS
  -> DECLARE WORKER AND REVIEWER ASSIGNMENTS
VALIDATE
  -> RECOMMEND SKILLS, IF BACKEND EXISTS
  -> BUILD PROVIDER MATRIX
  -> PREFLIGHT DECLARED RUNTIME AND AGENT SESSIONS
  -> VALIDATE ASSIGNMENTS AGAINST CAPABILITY PASSPORTS AND HARD GATES
  -> RECORD ADVISORY SCORES, RISKS, AND ALTERNATIVES
  -> VALIDATE SQUAD DECLARATION, IF USED
  -> BUILD CONTEXT PACK
DISPATCH
  -> WRITE VISIBLE DISPATCH
  -> SUBMIT DISPATCH
  -> MAP RUNTIME TASK STATES
  -> SUSPEND COORDINATOR WHILE WORKERS RUN, IF SUPPORTED
EXECUTE
  -> ANALYZE
  -> EXECUTE / RESEARCH / PROTOTYPE
  -> VERIFY
REVIEW
  -> REVIEW
  -> COLLECT AGENT RECOMMENDATIONS
  -> RESOLVE / MERGE / REDISPATCH
  -> FIX
  -> REVIEW AGAIN
  -> APPROVAL GATE, IF NEEDED
RECORD
  -> RECORD
  -> WRITE LEARNING FEEDBACK
  -> STRICT AUDIT
  -> RETURN RESULT TO USER AS DONE / BLOCKED / FAILED / CANCELLED
```

## 4. State Machine

Doctor commissioning, user Leader selection, and exact Leader session activation
are installation authority preconditions, not task-state transitions. The task
state begins when the task is published. Implementations may collapse adjacent
sub-states into phase-level records if they preserve the required evidence and
state mapping. For example, a small CLI may keep `published` while waiting for
the Leader's declaration or record `dispatching` while separately writing
validation, provider, preflight, and visible-attention evidence.

```text
new
  -> published
  -> selecting_automation_policy
  -> scanning_capabilities
  -> scanning_context
  -> loading_local_overlay
  -> selecting_runtime_adapter
  -> classifying_task
  -> selecting_profile
  -> decomposing_tasks
  -> Leader authors assignment-declaration.json
  -> recommending_skills
  -> building_provider_matrix
  -> preflighting_runtime
  -> scoring_routes (advisory evidence only)
  -> routing_capabilities (validate declared assignments)
  -> routing_squad (validate declared squad, if used)
  -> building_context_pack
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
  -> writing_learning_feedback
  -> strict audit and return to user
  -> done | blocked | failed | cancelled
```

The compatibility state names `scoring_routes`, `routing_capabilities`, and
`routing_squad` do not grant Agent-selection authority. Scores are advisory;
the latter states validate the Leader's declaration and either preserve it or
block it. VALP cannot fill, remove, or replace an assignment.

### Task Source Provenance

Every newly published task MUST record the implementation source identity used
to create it in `state.json.source_provenance`. The record MUST contain a
`task_start` observation and a `last_observed` observation. `valp publish`
creates both observations from the same source identity; every task-scoped
`valp scan` MUST preserve `task_start` and atomically replace `last_observed`
with a fresh observation.

Each observation MUST record:

```text
status: resolved_clean | resolved_dirty | unavailable
implementation_id
invoked_entrypoint
resolved_entrypoint
source_root
observed_at
vcs.kind: git | none
vcs.commit: exact commit id or null
vcs.tree: exact committed tree id or null
vcs.worktree_status: clean | dirty | unavailable
```

Paths are implementation observations, not protocol defaults. Implementations
MUST resolve their actual entrypoint and source root and MUST NOT hard-code one
operator's checkout path. A Git-backed source MUST record both the exact commit
and committed tree. A dirty worktree MUST remain `resolved_dirty`; the commit
and tree identify its base revision but MUST NOT be represented as the exact
identity of uncommitted bytes. A non-Git or otherwise unresolvable installation
MUST use `unavailable` with null commit/tree values rather than inventing a
revision.

Historical task states created before this contract MAY omit
`source_provenance`. A later task-scoped scan MAY add the object with
`task_start: null` and a fresh `last_observed` observation. Readers MUST
preserve such tasks as historical evidence; they MUST NOT backfill a claimed
task-start identity from a later observation.

### 4.1 Runtime Work Item State Mapping

Runtimes may expose their own queue state machine. A VALP adapter may map states
such as:

```text
queued
  -> dispatched
  -> waiting
  -> running
  -> completed | failed | cancelled
```

These runtime states are useful, but they are not sufficient by themselves.
VALP completion still requires receipt and evidence gates:

| Runtime state | VALP meaning |
|---|---|
| `queued` | Runtime accepted work, but delivery is not proven yet |
| `dispatched` | Runtime claims work was claimed or sent; maps to `dispatch_submitted` only when submission proof exists |
| `waiting` | Coordinator is suspended while submitted work runs; no completion or evidence gate is satisfied |
| `running` | Agent execution is active; maps to VALP `executing` |
| `completed` | Execution ended; maps to `dispatch_completed` only when expected evidence exists |
| `failed` | Execution failed; record failure reason and evidence gap |
| `cancelled` | User, runtime, or policy cancelled the execution |

If a runtime marks work `completed` without expected evidence, VALP must record
the adapter state but keep the evidence gate open.

#### 4.1.1 Suspended Waiting And Deterministic Resume

Suspended waiting is an optional runtime capability and a normative state when
claimed. It exists to stop coordinator model turns while dispatched workers are
still running. A runtime process may block on file notifications, queue events,
socket events, or bounded polling; it must not invoke the coordinator model to
ask whether work is finished.

An authored `wait-policy.json` is prepared control data, not evidence that a
task entered deterministic suspension. Its presence alone MUST NOT claim
deterministic wait/wake conformance or require a historical event ledger. The
claim becomes auditable when version-2 `state.json` records a suspension, when a
committed wait event exists, or when malformed wait-event input shows that the
event path was used. Once any of those signals exists, missing or invalid
`wait-events.jsonl` remains a failure and MUST NOT be fabricated, deleted, or
hidden to change the audit result.

Before entering `suspended`, the deterministic core MUST validate a closed
`wait-policy.json`, its referenced `submission-dependencies.json`, delivery
proof for every required work item, and the current task revision. The policy
MUST use `dependency_ready` as its normal success barrier and
`exception_short_circuit` as its exception behavior. The dependency artifact
remains the only dependency graph; the wait policy selects work items from it
and MUST NOT create a second graph.

Each required work item MUST bind all of:

```text
task_id
work_item_id
agent
role
dispatch_id
dispatch_generation
expected_refs
```

The task's versioned `state.json` records a closed `suspension` projection with
at least:

```text
status: waiting | resumed
suspension_id
suspension_epoch
state_revision_at_entry
checkpoint_ref (optional opaque task-local ref)
wait_policy_ref: immutable content-addressed policy snapshot
wait_policy_id
strict_identity
event_sequence_at_entry
receipt_event_sequence_at_entry
receipt_cursor_at_entry
evidence_refs_present_at_entry
required_work_items
required_work_item_ids
pending_work_item_ids
completed_work_item_ids
failed_work_item_ids
entered_at
deadline_at
execution_deadline
waiting_for_agents
receipt_count_at_entry
allowed_resume_events:
  receipt | timeout | runtime_failure | cancellation | user_input
accepted_wake when resumed:
  wake_id
  wake_event_id
  wake_reason
  resume_event
  resume_ref
  accepted_sequence
  resulting_state_revision
  result_ref
```

A runtime adapter MAY record `checkpoint_ref` as an opaque task-local reference
only when the ref is safe, existing, and non-empty. Replay and audit MUST reject
an unsafe, missing, or empty referenced artifact. Presence proves only those
reference properties; it does not prove that the artifact is restorable
coordinator state, that an adapter can restore coordinator execution, or that a
continuation was invoked exactly once. An adapter MUST omit `checkpoint_ref`
when no such artifact exists; the field is not a universal prerequisite for
deterministic wait.

For each strict epoch, the core MUST write the validated authored policy to an
immutable content-addressed `wait-policies/<sha256>.json` snapshot before it
commits the suspension. `wait_policy_ref` MUST name that snapshot. A later
epoch MAY use an updated root `wait-policy.json`, but replay and audit of prior
epochs MUST load their recorded snapshots rather than reinterpret history
against the mutable root policy.

A reference dispatch helper MAY generate the root `wait-policy.json` for the
exact work items it is about to submit, provided every item is copied from the
validated `submission-dependencies.json` and the policy is written before
delivery. This is policy authoring, not delivery or completion proof.

After concrete identity-bound delivery proof exists, a runtime adapter bridge
MAY observe task-local expected evidence while the coordinator is suspended.
It may emit `dispatch_completed` only when every expected ref for that work
item is safe, present, non-empty, and valid, and only when none of those refs
was already present at suspension entry. The completion receipt MUST bind the
current suspension epoch and work-item identity and MUST cite the originating
`dispatch_submitted` receipt. Pre-existing evidence MUST NOT be converted into
a new completion receipt.

`resume_event` records the accepted input. `dependency_ready` is a derived
`wake_reason` for a qualifying receipt after the final required work item
passes the barrier; it is not an input event.

Successful resume is a barrier, not an any-terminal race:

- a completion receipt qualifies only when task, work item, agent, role,
  dispatch id, dispatch generation, and expected refs match the policy;
- every required work item MUST have a qualifying `dispatch_completed`
  receipt and valid expected evidence before `dependency_ready` is accepted;
- unrelated work items may continue and MUST NOT block `dependency_ready`;
- a heartbeat or runtime `completed` label without receipt and evidence does
  not satisfy the barrier;
- Full and Remote Mode MUST reject `manual_result_attested` as a completion
  signal. Manual Mode MUST record that deterministic automatic wake is
  degraded rather than claim Full Mode conformance.

Exceptions short-circuit only into coordinator handling:

- `dispatch_blocked` or `manual_blocked`: the matching work item failed;
- `timeout`: the recorded deadline is reached;
- `runtime_failure`: the runtime exports a failure state or evidence ref;
- `cancellation`: a user, runtime, or policy cancellation is recorded;
- `user_input`: new explicit user input is recorded by the runtime adapter.

`runtime_failure`, `cancellation`, and `user_input` MUST reference a closed,
task-local `valp-exception-wake.v1` JSON artifact. The artifact MUST bind the
current `task_id`, `suspension_id`, `suspension_epoch`, and event, and MUST
record a non-empty reason plus a principal with a type and identifier. A
runtime failure principal MUST be `runtime`; a user-input principal MUST be
`user`; a cancellation principal MAY be `user`, `runtime`, or `policy`.
Runtime failure MUST also cite at least one separate, existing, non-empty
task-local supporting evidence ref. The artifact records attribution; it does
not by itself prove authentication and does not prescribe adapter transport.

The deterministic core MUST hash the exact artifact bytes, record the source
ref, SHA-256 digest, principal, reason, and supporting refs in the accepted
wake, committed wait event, and immutable wake result, and reject stale,
cross-task, cross-suspension, cross-epoch, event-mismatched, unsafe, malformed,
or changed evidence. The core assigns the authoritative accepted sequence;
producer-supplied sequencing is not trusted. An identical external wake uses
the source digest in its idempotency key, while changed bytes or conflicting
attribution fail closed.

An exception wake MUST preserve the immediately preceding committed
`pending_work_item_ids`, `completed_work_item_ids`, and
`failed_work_item_ids`. A blocked-work-item wake MAY append only its matching
pending work item to `failed_work_item_ids`; it MUST NOT remove that item from
pending or add it to completed. An exception wake MUST NOT be represented as
`dependency_ready` and MUST NOT satisfy Done Criteria.

Replay and audit MUST validate these transitions against the immediately
preceding projection, including when state, event projection, and result have
been edited together. The three work-item arrays in the immutable wake result
MUST exactly match the accepted suspension projection.

For strict deterministic suspension, every committed projection MUST retain the
exact ordered required-work-item table, IDs, wait-policy ID, and immutable
policy snapshot ref recorded for its epoch. Internal consistency among edited
projections is not sufficient.

Replay and audit of every receipt-driven completion or wake MUST revalidate the
recorded ledger ref, receipt ID, terminal event, suspension epoch, and exact
work-item identity. Editing a referenced receipt after acceptance MUST fail
closed even when state, event projection, and result remain internally aligned.

`user_input` is an explicit human override and may resume before `deadline_at`.
The deadline governs automatic `timeout` resume only; it must not delay a user
who intentionally re-enters the control loop.

Every accepted runtime event MUST carry a monotonic accepted sequence. Receipt
and deadline races are resolved by the first event accepted by the core, not by
wall-clock timestamp. The state transition MUST use revision compare-and-swap.
The authoritative event MUST be durably committed before its state projection,
or the implementation MUST provide equivalent atomicity. Deterministically
repairing a missing projection from committed events is event-to-projection
recovery, including after a process crash; it is not coordinator restart or
continuation proof. A projection without a committed event MUST fail closed.

An adapter that cannot durably sync newly created ledger or replacement-file
directory metadata on a platform MUST expose that limitation. Process-crash
recovery or atomic rename alone MUST NOT be described as proven sudden-power-loss
durability.

Only one wake transition may be accepted for a suspension epoch. Repeating the
same wake idempotency key MUST return the already-recorded byte-equivalent wake
result without a second state revision, wake event, or duplicate wake transition.
A conflicting duplicate, stale epoch, stale dispatch generation, or cross-task,
cross-role, or cross-work-item event MUST fail closed. `valp wait` MUST return
only after the matching `accepted_wake` is committed; it MUST NOT treat an
unrelated rewrite of `state.status` as successful resume.

Coordinator-model-free waiting requires one blocking runtime wait or event
subscription. While `state.status` is `suspended`, the runtime MUST NOT invoke
the coordinator model for status polling, progress messages, or periodic checks.
Local file, socket, queue, or receipt polling is allowed because it does not
invoke a model. User-facing status should report that a local wait was used,
that coordinator-model polling was not observed, and the accepted wake reason
and receipt evidence. It MUST NOT estimate provider billing or require the user
to derive a cost. The coordinator model is invoked again only after an accepted
wake event or an explicit user turn.

The reference core's exactly-once scope ends at the accepted wake transition.
An asynchronous adapter MAY claim exactly-once coordinator continuation only
when it exports a wake-ID-bound continuation invocation receipt and
restart/restore evidence showing that duplicate invocations are suppressed
across failure recovery. Otherwise the adapter MUST downgrade the continuation
capability claim. `checkpoint_ref` alone is not continuation invocation or
restorability evidence.

A successful `dependency_ready` wake normally resumes into `executing`. A
runtime failure, blocked work item, or timeout normally resumes into `blocked`
for visible handling; cancellation resumes into `cancelled`; explicit user
input resumes into `executing`. None of these transitions bypass expected
evidence, review, recommendation resolution, approval, final synthesis, or
audit. A later suspension creates a new epoch and cannot be awakened by events
from an older epoch.

### 4.2 Trigger Policy And Auto Visible Mode

VALP can start from an explicit command, from project policy, or from a runtime
watcher. The trigger decision must remain visible.

Recommended trigger levels:

| Trigger mode | Source | Default for new installs | Requirement |
|---|---|---:|---|
| `manual` | user explicitly publishes or asks to use VALP | yes | publish only after explicit user intent |
| `policy_auto` | project instructions, local overlay, or chat policy matches the task | no | record the matched policy and show the task id before dispatch |
| `watcher` | issue label, queue item, schedule, file event, or runtime API | no | opt-in runtime policy plus trigger source evidence |
| `disabled` | VALP is not used for this request | allowed | record only if a runtime evaluated and declined |

Auto Visible Mode is layered over Full Mode, Remote Mode, or Manual Mode. It
decides whether to publish a task and how much of the loop can proceed
automatically; it does not change what counts as completion.

An Auto Visible task must record trigger evidence, normally:

```text
<task>/trigger-policy.json
```

The trigger evidence should include:

```text
trigger_mode
trigger_source
matched_signal
rule_ref
risk_classification
selected_action
approval_required
visible_refs
```

When `trigger_mode` is `watcher`, the trigger evidence MUST additionally
include `task_id`, `source_event_id`, `matched_signal`, `rule_ref`, and a
digest-shaped `deduplication_identity`. These fields bind the published task to
the exact source event and matched rule that the runtime deduplicates.

Allowed selected actions:

```text
no_valp
publish_only
validate_declared_route
validate_declared_route_and_dispatch
continue_until_gate
block_for_approval
```

Auto Visible Mode may automatically publish a low-risk task and refresh Doctor
facts. It cannot select a Leader or author assignments. After a user-selected
Leader has written a valid declaration, it may automatically continue through
validation, skill recommendation, preflight, visible dispatch, verification,
review, audit, and report generation when the configured runtime can prove each
step. It must stop at `block_for_approval` before high-risk work.

High-risk trigger signals include destructive changes, release or upload,
auth/secrets, memory or agent configuration, migrations, signing, privacy, and
private-data export. A local overlay or runtime may add stricter rules, but it
must not remove the protocol approval requirements.

No background watcher is required by the protocol. A runtime that implements a
watcher must export the source event, rule, task id, and approval state into
VALP evidence before dispatching work. A watcher that cannot export this proof
is not an Auto Visible Mode implementation.

A watcher MUST derive and persist one deduplication identity from its source
event and matched rule before accepting a duplicate. An identical repeated
source event returns the original task result without another publication; a
reused identity with changed source content fails closed. A high-risk event MAY
publish its visible task record, but its selected action MUST be
`block_for_approval` until the separate approval gate is satisfied.

### 4.3 Automation Policy

Automation policy is the control surface for "full automation". It records which
parts of the loop may continue automatically, which parts must stop, and why.
The goal is not silent execution. The goal is automatic progress until the next
evidence, approval, context, runtime, or scope gate.

Recommended task evidence:

```text
<task>/automation-policy.json
```

The policy should include:

```text
mode
risk_classification
selected_action
approval_required
allowed_automatic_phases
blocked_automatic_phases
audit_grade
basis refs
stop_conditions
```

The `audit_grade` states the proof level being claimed:

| Grade | Meaning |
|---|---|
| `demo` | Static or synthetic example; useful for understanding protocol shape |
| `local` | Local task evidence exists, but no Full Mode runtime proof is claimed |
| `runtime` | Adapter-exported dispatch submission, runtime state, expected evidence, and audit proof exist |
| `public-proof` | Sanitized, shareable task folder or case study proves the claim without private context |

Automation policy must be conservative:

- low-risk work may continue through publish and capability refresh without an
  assignment; validation, dispatch, evidence collection, verification, review,
  synthesis, audit, and learning may continue only after a user-selected Leader
  has written a valid assignment declaration and each step writes evidence;
- high-risk work must stop before side effects and record approval evidence;
- runtime completion without expected evidence is a stop condition, not Done;
- missing context, stale memory, failed preflight, unresolved review findings,
  unresolved agent recommendations, or unresolved approvals must stop the loop;
- a local overlay may make automation stricter, but it cannot make approval,
  receipt, evidence, or context gates weaker.

This is the protocol boundary for autonomous work: the system can move quickly
when the control loop is healthy, and it must stop visibly when the loop cannot
prove safety or completion.

### 4.4 Delegated No-Self-Modification Policy

Every newly routed delegated task must write `delegation-policy.json` before
submission. The policy must forbid delegated self-modification across exactly
these live surfaces:

```text
skills
plugins
memory
mcp_config
agent_config
```

The boundary is based on live effect, not path or Git tracking. A tracked file
that is live-loaded by a current agent is protected. A repository protocol,
policy, schema, source, test, or documentation change is allowed only when the
task scope explicitly permits that repository change and the changed artifact
is not loaded into the current task as live agent behavior or configuration.

The reference CLI can require and surface the policy at dispatch and make a
recorded violation audit-fatal. It cannot claim that arbitrary filesystem or
shell writes were prevented unless the runtime adapter provides sandbox or
write-monitor evidence. Full and Remote Mode adapters must hard-block protected
writes or export equivalent enforcement evidence. Manual Mode may only record
an operator attestation; it must not claim runtime enforcement.

A protected write performed during delegation is a violation even if the
acting agent later reverts it. The task must record the earliest possible
mutation point, invalidate evidence produced at or after that point, move the
task to `blocked`, and require operator resolution, a fresh capability and
permission scan, rerouting, and redispatch. Approval recorded after the write
cannot retroactively validate affected evidence. A separately scoped
configuration task requires prior explicit user approval and is not an
exception that a delegated principal may grant itself.

That prior approval must name the task id, protected category, target principal
or artifact, permitted operation, and expiry or one-shot boundary. A
category-only, role-wide, repository-wide, or general task approval is
insufficient. The reference delegation policy remains fail-closed unless the
separately scoped configuration path can prove that exact approval before any
mutation.

### 4.5 First-Install Health Gate

A first install must commission the available Agent surfaces before real
dispatch. The bootstrap controller, whether exposed through a CLI or an
optional App, should run an explicit health and capability gate in this order:

```text
install check
  -> valp doctor
  -> enumerate known Agent surfaces and addressable sessions
  -> write installation capability passports
  -> user selects the Leader
  -> provision and bind one fresh installation-owned Leader session
  -> activate the Leader epoch
  -> runtime preflight, when Full Mode is requested
  -> Leader-declared assignment validation and dispatch dry run
  -> visible user decision before any submit or Auto Visible policy
  -> optional live smoke test
```

Doctor commissioning MUST record, for every discovered Agent surface and
addressable session:

```text
surface and runtime identity
official capability claims and provenance
local installation and version
live callability
declared model
observed model, provider or relay, and reasoning mode
session fingerprint and model-observation TTL/freshness
reachable Skills
reachable MCP servers and tools
filesystem, network, shell, and mutation permissions
context policy and current context state
known limitations
Leader, implementer, reviewer, and researcher role eligibility
task-verified history bound to the exact model and session identity
```

Doctor MUST preserve unknown, unsupported, stale, unavailable, and mismatched
facts explicitly. It MUST NOT infer an observed model from a configured default,
provider label, product name, or another Agent surface. Unknown, stale, or
mismatched model identity cannot qualify an Agent for high-risk implementation
or final review.

A configured or declared model is optional. When no model was declared, a
current, high-confidence runtime observation bound to a known session is the
authoritative current identity and may qualify the Agent. When a declaration
does exist, any model, provider, or reasoning mismatch invalidates bound history
and blocks high-risk roles.

The first install gate must not assume a fixed checkout path such as a Desktop
folder. It should record the actual install root, CLI path, runtime path, and
doctor/preflight report refs. A symlink or App bundle wrapper is valid only when
`valp doctor` can still identify the protocol checkout and `bin/valp` entrypoint.

The dry run may create a task folder and write routing, dispatch files, visible
attention evidence, assignment validation, and `dispatch_written` receipts. It
must not append
`dispatch_submitted` or `dispatch_completed` receipts unless a runtime actually
submitted work and the expected evidence appeared. A new dry-run task is allowed
to fail `valp audit`; that means work has not completed, not that installation
failed.

New installs must default to Manual trigger mode. `policy_auto`, `watcher`, and
real `--submit` behavior are opt-in after the user can inspect doctor,
preflight, the user-selected Leader, Leader-declared assignments, validation
findings, expected evidence, and approval risks.

## 5. Runtime Adapters

VALP is runtime-neutral. A runtime adapter translates concrete platform behavior
into protocol evidence.

Adapter classes:

| Adapter class | Example shape | Full Mode possible? |
|---|---|---|
| pane controller | visible terminal panes and submit proof | yes |
| daemon queue | local daemon claims queued tasks and reports status | yes, if receipts and evidence are exported |
| local process worker | approved local subprocess with submission, lifecycle, output, and evidence proof | yes, for the declared host/profile |
| hosted/local platform | Web board plus local runtime workers | yes, if agent output and proof are auditable |
| remote SSH | runtime runs on a remote host | yes, with remote proof caveats |
| manual | human copies dispatches and results | no; Manual Mode only |

Every Full Mode adapter must export:

```text
agent list
agent metadata/status
provider matrix
context policy
runtime preflight
dispatch submission proof
runtime task state mapping
expected evidence refs
receipt ledger
failure reason
approval gate status
delegation-policy enforcement evidence or an explicit limitation
```

An adapter may use its own internal state names, but it must publish the mapping
to VALP receipt states.

For pane-based runtimes, preflight must record enough display/runtime facts to
avoid invisible failures:

```text
pane id
agent status
foreground cwd, when available
terminal size, when available
minimum terminal size expected by the agent
CLI availability/version probe, when available
restart/update-needed status, when available
known TUI/display caveats
```

If terminal dimensions cannot be read, the adapter must record `unknown` instead
of pretending the pane is safe. If dimensions are below the agent minimum, Full
Mode dispatch must stop or require an explicit operator repair before sending
the task.

For headless, daemon, hosted, or queue-based runtimes, pane fields are not
required. The adapter must instead record the equivalent session or job facts
needed to prove delivery and completion, such as queue id, worker id, hosted run
id, output reference, artifact path, retry state, and expected evidence refs.
Expected evidence refs must be task-relative safe paths: non-empty POSIX-style
relative paths with no absolute prefix, backslash separators, or `..` path
segments. Evidence outside the VALP task folder cannot satisfy completion.

Reference tools may expose adapter selection flags such as `auto`, `manual`,
`herdr`, or `queue`, but protocol semantics come from the recorded adapter class
and evidence. A queue adapter must not fake pane fields, and a pane adapter must
still fail preflight when terminal or display checks fail.

### Installation Leader Session Lifecycle

The normal first-install control path is:

```text
bootstrap surface
  -> Doctor commissions one passport per addressable Agent session
  -> user selects one observed Leader principal
  -> selection remains inactive
  -> leader start provisions a fresh installation-owned runtime attachment
  -> authority and attachment health proof are recorded
  -> Leader epoch becomes active
  -> Leader may create task/project-owned Workers
```

The bootstrap surface MAY use the same Agent product as the selected Leader,
but it MUST remain a separate epoch-`0` control surface. An implementation MUST
NOT adopt the bootstrap session, the user's currently focused session, a pane
matched only by Agent name, an existing session matched by label or cwd, or a
previous worker session as the Installation Leader.

Leader selection and activation are separate state changes. Selection MUST
persist the selected principal, its observed passport reference and digest,
and user-approval evidence while leaving `active_leader` unset and
`active_leader_epoch` at `0`. `leader start` MUST resolve the selected
principal's validated runtime adapter and `runtime.launch_argv` or equivalent
launch contract from current Doctor evidence. The protocol and reference CLI
MUST NOT synthesize a product-specific command or flags.

Before a Leader is selected, the bootstrap surface MAY refresh candidate
discovery from `awaiting_leader_selection`. The refresh MUST return to
`discovering_leader_candidates`, commission fresh Doctor passports, replace
the candidate projection, and append distinct discovery events. It MUST NOT
reuse stale candidates, rewrite prior events, select a Leader, or activate an
epoch as a side effect.

An initial Agent Leader start MUST create a fresh, non-focused,
installation-owned runtime attachment. The installation Leader authority is
the selected principal plus the installation id and fenced epoch; it MUST NOT
be identified by a terminal, window, pane, tab, cwd, or other presentation
surface. The durable `leader-session-binding.json` projection and append-only
`leader-session-receipts.jsonl` ledger record the current attachment and MUST
bind at least:

```text
installation id
selected principal id and passport digest
Leader epoch
adapter id and adapter class
runtime scope and exact session identity
session generation and non-secret identity token
installation-owned cwd or equivalent isolated context
launch argv digest or equivalent launch-contract digest
health status and health evidence
focused_at_provisioning: false, when focus exists
provisioned, activated, replaced, and stopped timestamps as applicable
```

The first non-zero Leader epoch is allocated only after provisioning and health
checks succeed. Missing, ambiguous, stale, focused, user-owned, task-owned, or
conflicting identity evidence MUST leave the selection inactive and fail
closed. A pane name, Agent label, visible text, Enter submission, process age,
or cwd alone is not an authority or activation receipt.

`leader open` MUST be callable from any caller workspace. When the current
runtime attachment is present, it MAY focus or attach that runtime without
changing the installation Leader epoch. When the attachment is gone, the
adapter MUST provision a fresh attachment and the core MUST fence the old
attachment before recording the replacement. A replacement attachment changes
runtime generation and may change pane, tab, workspace, terminal, cwd, or
process identity; it MUST NOT change the selected principal merely because the
caller used another window.

If runtime provisioning fails after `leader start`, `leader restart`, or
`leader rotate` has entered its prepared state, the core MUST append a
provider-neutral `leader_session_start_failed` receipt and transition the
installation to `blocked` through `leader_activation_failed`. The failure
receipt MUST identify the requested operation, selected principal, proposed
epoch and generation, adapter, protocol error code, and blocking event. It MUST
NOT invent a binding digest or runtime session identity that the adapter did
not prove. The active epoch MUST remain unchanged, and no partially created
session may acquire Leader authority.

A blocked first start MAY recover the exact partially provisioned session only
through an explicit user-approved `leader recover-start` operation. Recovery is
not an ordinary start, restart, rotation, or broad runtime scan. Before entering
`activating_leader` again, the core MUST prove all of the following:

```text
active Leader is unset and active Leader epoch is 0
no Leader session binding exists
the selected principal and passport digest remain unchanged
the latest valid Leader-session receipt is the blocking failed start
the failed receipt names operation start, epoch 1, and the pending generation
the receipt blocking event matches the current blocked state
the user explicitly approves one exact runtime session id
```

The recovery adapter MUST NOT create, move, focus, close, or replace a runtime
session. It MUST address only the user-named session and re-prove its exact
runtime identity, deterministic installation-owned workspace identity, selected
Agent identity, cwd or equivalent context, non-focused state, complete launch
argv or launch-contract identity, live process generation, and bounded health
observations. Product name, pane label, cwd, process age, or visible text alone
is insufficient. A launch mismatch, ambiguous workspace, changed selection,
stale failure receipt, missing process, or any unrelated session MUST leave the
installation blocked.

Successful recovery MUST append `leader_start_recovery_approved`, preserve the
original failure receipt, bind the recovered session to the original pending
generation, append normal provisioned and activated receipts with recovery
evidence, and only then activate epoch `1`. Failed recovery MUST append a new
failure event and receipt without overwriting the original attempt. Recovery
MUST NOT be used after a Leader epoch has ever been activated; those cases use
fenced restart or rotation semantics.

Only one Leader authority may be active for an installation, and the reference
runtime SHOULD expose at most one live attachment for that authority. Calling
`leader start` while the authority is active MUST behave like `leader open`: it
must open the current attachment or provision a fenced replacement, rather than
failing solely because the caller is a different window. Restarting the same
selected principal remains available as an explicit fenced `leader restart`
operation; changing the selected principal requires explicit user-approved
Leader rotation. Both explicit operations preserve prior bindings and receipts,
provision a fresh generation, and activate the next epoch only after new health
proof succeeds. A failed replacement MUST NOT silently restore authority to an
ambiguous or partially started session.

Every Agent session subsequently launched, assigned, or coordinated by the
Leader is a Worker unless the user performs the explicit fenced Leader
replacement operation above. This remains true for another session of the same
Agent product: a Codex Leader launching Codex creates a Codex Worker, not a
second Leader. Workers MUST use task- or project-owned bindings, MUST NOT carry
the Leader epoch or installation-owned credentials, and MUST NOT promote
themselves or another worker through an assignment declaration, delegation,
squad role, coordinator role, runtime label, or private reasoning.

No UI product is privileged by the protocol. A CLI-only installation MUST be
fully supported when its Doctor and runtime adapter provide the required
evidence. An App MAY act only as an explicit bootstrap surface or as the exact
user-selected and bound Leader session; its presence MUST NOT create a hidden
coordinator, implicit Leader, or extra evidence seat.

### Project/Task-Owned Agent Sessions

After the user-selected Leader's role assignment passes validation, a Full Mode
adapter that controls reusable Agent sessions MUST provision or reuse an
adapter-owned session for every declared worker before delivery. It MUST NOT
bind a work item to an unrelated session that the user opened for another task.

Session ownership is provider-neutral. The task-local `agent-sessions.json`
projection MUST bind each declared worker to:

```text
owner scope: project or task
stable project identity
task id, when task-owned
declared Agent id
launch argv or equivalent worker entrypoint
optional version probe argv from the same capability record
isolated project working context
adapter-provisioned runtime scope and ownership
adapter-issued session, worker, or run identity
binding generation and non-secret identity token
dispatch eligibility
```

The base session schemas MUST describe these facts without requiring a pane,
terminal, workspace, tab, local filesystem path, provider name, model name, or
known Agent command. Adapter-specific identifiers MAY appear as additional
runtime-scope or runtime-identity fields, and an adapter MAY apply stricter
validation only when that adapter is explicitly selected. A reference adapter
implementation or example MUST NOT become a default requirement for other
Agents or runtimes. Launch behavior comes from observed capability evidence or
explicit adapter configuration; the protocol MUST NOT infer it from a built-in
Agent-name table.

The projection MUST name a stable adapter id, the routed runtime record MUST
name the same id, and every session receipt in the same ledger MUST repeat that
exact id. A state-v2 Full Mode task MUST carry the routing and state session
markers; deleting both markers MUST fail audit rather than downgrade the task to
legacy behavior. Base audit validates the common
ownership, context, launch-or-runtime reference, scope, identity, generation,
and binding chain. Adapter-specific audit rules apply only when that adapter is
explicitly named; HERDR pane requirements, for example, MUST NOT be applied to
a thread, queue, hosted run, or another non-pane adapter.

For a local process or pane-controller adapter, a bare executable in the launch
argv MUST be resolved by the coordinator to a concrete absolute executable path
before it crosses the runtime boundary. The adapter MUST NOT assume that its
daemon or terminal server inherits the coordinator's `PATH`; failure to resolve
the entrypoint blocks provisioning. If a capability declares a version probe,
preflight MUST execute that exact argv. It MUST NOT synthesize `--version` or
another flag from an Agent name or launch entrypoint.

When a pane controller can create workspaces, tabs, threads, or equivalent
containers, it MUST provision a non-focused task-owned runtime scope before it
starts the worker. It MUST place the worker in an isolated tab or equivalent
surface inside that scope before recording the binding. Creating a worker by
splitting the user's active project-unrelated surface is not task isolation.
The final provisioning response MUST explicitly report that the isolated worker
pane is not focused. The binding and provisioning receipt MUST persist this as
`focused_at_provisioning: false`; a missing or focused result blocks delivery.
This is creation-time evidence and does not forbid a user or coordinator from
opening the task-owned pane later.

The adapter MUST append provisioning decisions to
`agent-session-receipts.jsonl`. A first binding is valid only when it is created
from a successful adapter provisioning response. Agent labels, pane names,
working directories, model footers, terminal focus, and user declarations are
not ownership proof and MUST NOT be used to claim a pre-existing unbound
session.

When a task declares a task-scoped runtime capability record, routing and state
MUST repeat the same content-addressed reference and digest before dispatch may
use it. The referenced record MAY override only runtime launch and version-probe
commands for the declared task; it MUST NOT silently replace role, permission,
approval, or routing evidence. An unreferenced file, a one-sided marker, or a
digest mismatch has no launch authority and MUST fail closed when a marker is
present. This task-scoped launch evidence takes precedence over a mutable global
capability suggestion only while creating a new binding. It MUST NOT rewrite or
replace an accepted live binding.

A recorded binding may be reused only when fresh runtime preflight observes the
same adapter-issued identity and isolated project context. If the bound session
is absent, the adapter MAY provision the next binding generation and record a
new receipt. Changing the launch argv for that next generation requires an
explicit operator action scoped to one declared worker; the previous binding
and receipt remain immutable. The adapter MUST confirm that the old runtime
identity is absent before it accepts the new launch contract. A structured
runtime `not found` result for the exact recorded scope MAY prove this absence;
timeouts, permission errors, transport failures, and unstructured lookup
failures MUST NOT. If the recorded pane, worker, thread, or run identifier is present
with a different identity, owner, Agent, launch entrypoint, or context, the
adapter MUST fail closed. It MUST NOT silently replace or overwrite the
conflicting binding.

When the recorded runtime identity is still present and matches, the accepted
binding's launch contract remains authoritative for reuse. A later capability
scan or registry change MUST NOT rewrite that contract, force a replacement,
or block same-binding delivery merely because its current launch suggestion is
different. An explicit launch-replacement request while the old identity is
present MUST fail closed; replacement is available only after absence is
proved. Binding validation errors MUST identify the conflicting field without
printing the launch arguments or other sensitive values.

When the runtime exposes a binding-scoped lookup, fresh preflight SHOULD query
the recorded workspace, worker pool, thread, or run scope directly. It SHOULD
NOT depend on an unbounded global session listing that may be truncated or mix
unrelated user sessions into the observation surface.

Before the first owned binding exists, route-time observations from unbound
sessions are discovery hints only. They MUST NOT qualify or disqualify a
declared worker's runtime model/session identity. Static assignment validation
MAY pass with that gate explicitly deferred. Immediately after provisioning
and before delivery, the adapter MUST perform fresh model/session eligibility
checks against the owned binding; a stale, unknown, mismatched, or ineligible
result blocks dispatch.

A newly started owned session MAY need a bounded readiness observation window
before it reports model and session metadata. The adapter MAY repeat read-only
preflight during that fixed window. An unobserved identity after the deadline
is a readiness blocker; an observed but ineligible identity is a model mismatch
and MUST NOT be treated as readiness.

Some interactive runtimes create their native session only after the first
prompt. Others report a native session at startup but do not emit their
structured model observation until the first completed turn. A Full Mode
adapter MAY submit one bounded non-task bootstrap probe before formal dispatch
only when fresh structured evidence reports one of these exact states:

- the owned target is addressable, detected as the declared Agent,
  interactive, settled, not prompt-eligible, and
  `session_identity_unknown`; or
- the owned target is addressable, detected as the declared Agent,
  interactive, settled, prompt-eligible, and ready with a known native session,
  while the runtime model probe is explicitly `unsupported` because no current
  model observation exists.

An unavailable, stale, malformed, or already-observed model probe MUST NOT use
the second state. The probe MUST begin
with the worker's validated task-local control slice, MUST request the exact
response `BOOTSTRAP_READY`, and MUST be bound to the current owned binding
generation and runtime identity. A relative `control_contract_ref` in that
slice MUST resolve from the task directory, and the bootstrap input MUST give
the Worker an unambiguous address for that exact directory before asking it to
load the contract. Any other not-ready reason, an existing formal delivery
receipt, a missing or mismatched control contract/slice, or a repeated
unverified probe MUST fail closed without sending input. A formal delivery
receipt from a prior task-owned binding generation or prior phase MAY remain in
the immutable task ledger; it MUST NOT block a new binding generation. A formal
receipt for the current Agent binding generation MUST block the probe, and a
same-Agent formal receipt whose binding generation is absent or ambiguous MUST
also fail closed.

The exact response may be proven from terminal output only as response evidence.
An adapter MAY declare a closed set of renderer envelopes for that response,
but normalization MUST accept only a whole rendered line that maps through one
declared envelope to the exact requested response. For example, an adapter may
declare the bare response, an exact Codex list-marker envelope, or an exact
Claude action-marker envelope; the marker and spacing are part of the declared
envelope rather than punctuation that may be stripped generically. It MUST NOT use substring
search, extract a token from prompt text, strip arbitrary punctuation or
formatting, accept additional text on the matched line, or accept a match
without the concrete matched line. The same normalization MUST reject an
equivalent line already present in the bounded pre-probe snapshot. The accepted
envelope and raw matched line MUST be recorded as response-only proof; they
remain invalid session or model evidence. Pane text, titles, argv, labels, and
launch attestations remain invalid session or model evidence. After the probe
settles, native session identity MUST come from the runtime's structured session report
and model/provider/reasoning MUST come from the structured runtime model
observation for that same native session. When the runtime exposes that binding
as a SHA-256 token and generation, the token MUST equal the complete SHA-256 of
the structured native session identifier and the generation MUST equal the
runtime's declared prefix derivation; comparing a derived generation directly
to the raw identifier is invalid. Model, provider, and reasoning values MUST be
concrete; placeholders such as `unknown`, `unsupported`, or `unavailable` are
not proof. The final state-change sequence MUST advance
beyond the pre-probe sequence. When the native session was already known before
the probe, structured readiness after the probe and the structured model
observation MUST both bind to that exact same pre-probe native session.
Only then may the adapter append one `agent_session_bootstrap_verified` receipt,
mark the binding `bootstrap_ready`, and reuse that same session for one formal
dispatch. The bootstrap probe itself MUST keep `formal_dispatch_count: 0` and
MUST NOT create `dispatch_submitted`, `dispatch_completed`, or equivalent Worker
delivery evidence.

An Agent launch interposer that probes identity, reports metadata, captures
startup output, or supervises a child process MUST preserve the child runtime's
interactive terminal contract. For a TUI worker this includes a real child PTY,
terminal size propagation, signal forwarding, and raw input semantics so keys
such as Enter are not rewritten by an outer canonical line discipline. Any
persisted transcript MUST apply the task's redaction policy before bytes reach
durable storage; keeping a raw transcript and redacting it afterward is not
equivalent.

The provisioning adapter MUST provide the accepted binding generation to a
task-owned launch interposer through an adapter-controlled input. The interposer
MUST prefer that value over a static initial-generation default and MUST fail
closed when the adapter value is malformed. A replacement session MUST NOT
continue reporting the generation of the launcher's original config file.

Structured model/session metadata is not Agent lifecycle proof. A pane
controller that uses an interposed launcher MUST also expose the declared Agent
id and an adapter-visible `idle`, `working`, or terminal state bound to the
same task-owned session. `working` MUST follow actual dispatch input reaching
the child runtime; text insertion, an Enter transport call, model metadata, or
a launcher process remaining alive MUST NOT synthesize it. Immediately before
delivery, a task-owned pane whose Agent state is absent or `unknown` MUST fail
preflight instead of waiting for a predictable working-state timeout.

The bounded post-provision readiness window MUST wait for both an observed,
session-bound model identity and an adapter-visible `idle` or `working` Agent
state. Model metadata arriving before the launcher's lifecycle report MUST NOT
short-circuit that window or turn a transient startup state into a terminal
preflight failure. The final delivery preflight remains fail-closed.

When a task-owned launcher publishes a structured lifecycle observation, the
observation MUST include a nonempty source, session id, monotonic sequence, and
binding generation. A valid observation bound to the accepted pane and
generation takes precedence over generic pane-title or screen inference for
that preflight snapshot. A nonempty session id alone is insufficient: its task
and Agent identity MUST match the accepted binding. A missing reporter permits
the adapter's normal runtime observation; a partial, malformed,
stale-generation, cross-task, cross-Agent, or otherwise mismatched reporter
record MUST fail closed and MUST NOT fall back to the generic state. Model
identity and lifecycle state remain separate signals even when a runtime
transports both as structured pane metadata.

Late structured identity metadata MAY reconcile an exhausted readiness timeout
for the same dependency-ready work item. This reconciliation is allowed only
when the exact task-owned binding and its provisioning receipt still match, the
recorded model blocker is `owned_session_model_readiness_timeout`, and no
`dispatch_submitted` receipt exists for that work item. The adapter MUST
re-observe the accepted binding and MUST reopen delivery only after the model
probe is `observed`, the session identity is `known`, and role eligibility
passes. It MUST reuse the binding without consuming another reroute or creating
a replacement session. Any different exhausted failure, prior submission,
missing receipt, binding conflict, or observed ineligible model remains blocked.

Dispatch-time preflight MUST resolve the worker through the accepted binding,
not through an Agent-label lookup. Concrete submission proof MUST cite the
binding ref, generation, and identity token. A transport receipt for an
unbound session cannot satisfy Full Mode delivery, deterministic waiting, or
completion provenance. The cited generation and token MUST match that
generation's historical provisioning receipt; they MUST NOT be compared only
with the newest projection after a valid session replacement.

A dry run that only renders dispatch commands MUST NOT provision a runtime
session, require a live session/model identity, create a runtime dispatch
blocker, or consume a retry. These gates remain mandatory immediately before
actual delivery.

Unstructured pane, transcript, conversation, or dispatch text MUST NOT establish
submission state. A runtime that keeps an outer Agent status idle while child
work runs must expose an adapter-issued structured child-job event or identifier
bound to the accepted session and current dispatch. Visible labels or counters
may help an operator diagnose the runtime, but they cannot produce a
`dispatch_submitted` receipt.

Session context MUST start at the declared project root or the adapter's
equivalent isolated project context. Task dispatches may cite task-local context
artifacts, but provisioning MUST NOT copy unrelated user transcripts, hidden
coordinator reasoning, secrets, or another task's state into the worker
session. Manual Mode records manual attestations instead of inventing session
ownership. Remote and non-pane adapters implement the same ownership contract
with their native thread, job, worker, or session identity.

### Provider-Neutral Coordinator Continuation

A local or synthetic identifier, pane transport, CLI stdout, or user-message
channel cannot establish provider/session invocation identity. An adapter MUST
remain Manual/degraded until real provider/session identity and durable
duplicate-suppression evidence exist.

An accepted wake MAY create an immutable `valp-continuation-envelope.v1` on a
typed `runtime_control` channel. The envelope binds task, suspension epoch,
wake identity, control-contract digest, payload digest, coordinator target, and
an adapter-owned durable boundary. User input and raw worker output are never
continuation payloads. Wake acceptance and invocation acceptance are separate
revision-CAS boundaries.

The `suspension_id`, `wake_id`, and `wake_event_id` fields MUST be
content-addressed identifiers in the form `sha256:` followed by 64 lowercase
hexadecimal characters. Implementations MUST validate these identifiers before
using any of them in artifact paths or ledger lookups. The active suspension
epoch MUST come from the authoritative task state projection; a persisted
continuation envelope MUST match that epoch exactly. Persisted envelopes MUST
NOT establish, raise, or otherwise redefine the active epoch.

An adapter MUST append the ordered acknowledgement chain
`resume_pending`, `resume_received`, `digest_verified`, `resume_accepted`,
`continuation_started`, and `resume_consumed`. The last event proves exactly one
correlated provider invocation consumed one envelope; it does not mean that the
VALP task is done or approved. Identical redelivery MUST return the original
receipt byte-for-byte. A changed payload, target, provider, or control digest
under the same idempotency key MUST fail closed and remain in the append-only
ledger. Adapters without real provider invocation identity or durable duplicate
suppression MUST remain Manual/degraded.

Every acknowledgement MUST correlate to the exact persisted envelope, payload,
control-contract bytes, suspension epoch, full invocation key, target tuple, and
capability declaration. `resume_received` and later events MUST fail closed when
`resume_pending` is absent, stale, changed, or unpersisted. Conflicts and stale
epochs MUST produce durable rejection evidence.

`continuation_started` and `resume_consumed` require an immutable, complete
provider invocation receipt. A bare invocation identifier, VALP's own event
ledger, or an unverified local marker is not provider duplicate-suppression
proof. Strict audit MUST recompute event identifiers and correlate the envelope,
payload, control contract, receipt, and capability tuple. A runtime MUST recover
persisted pending envelopes after restart. Where exclusive locking is not
implemented, the adapter MUST fail closed rather than append without a lock.

Before external invocation, the runtime MUST durably persist an invocation
intent keyed by the exact continuation idempotency key. If restart finds that
intent without a committed invocation receipt, it MUST use the provider-owned
status/reconciliation operation and duplicate-suppression boundary; it MUST NOT
resubmit the continuation. A reconciled complete receipt is validated and
committed through the same `continuation_started -> resume_consumed` transition.
If provider status is pending, missing, malformed, or cannot prove the exact
identity, the runtime remains indeterminate and preserves the intent. If restart
finds a complete immutable receipt but either terminal event is missing, it MUST
validate the receipt again and append only the missing correlated event or
events. This recovery path never manufactures provider identity and never uses
the VALP intent marker itself as duplicate-suppression proof.

## 6. Local Overlays

VALP separates open protocol semantics from local execution facts.

The open protocol defines lifecycle, evidence, receipts, approval gates, context
policy, adapter contracts, and done criteria. A local overlay may define:

```text
workspace defaults
local agent names
runtime adapter preferences
agent capability profiles
skill library paths
project folder conventions
operator approval preferences
context policy overrides
historical feedback refs
```

Local overlays are useful because real users have different machines, agents,
terminals, and runtimes. They must remain subordinate to protocol gates:

- A local overlay cannot declare hidden dispatch valid.
- A local overlay cannot treat insertion as submission.
- A local overlay cannot skip expected evidence.
- A local overlay cannot point expected evidence outside the task folder.
- A local overlay cannot bypass approval gates.
- A local overlay cannot turn a capability profile into a fixed assignment.
- A local overlay cannot suppress context compression thresholds unless stricter
  policy replaces them.

Reference local scans should prefer protocol-neutral locations before
runtime-specific compatibility paths:

```text
<workspace>/.valp/agents/capabilities.json
~/.valp/agent-capabilities.json
~/.herdr/agent-capabilities.json

<workspace>/.valp/local-overlay.json
~/.valp/local-overlay.json
~/.herdr/valp-local-overlay.json
```

Environment variables may explicitly select another file, but local overlays
remain hints and cannot weaken receipt, evidence, approval, or preflight gates.

The preferred layering is:

```text
VALP spec
  -> runtime adapter
  -> local overlay
  -> workspace/project AGENTS.md or equivalent
  -> task routing/evidence
```

When layers disagree, the safer and more specific evidence wins. A current
runtime scan beats old memory. A project permission boundary beats a general
agent strength. A protocol hard gate beats every local preference.

## 7. Capability Evidence

No Agent is assumed to be fully known from memory, product branding, or a
configured default model. Doctor commissions installation-wide capability
passports before task routing. Task intake refreshes volatile facts for every
declared Agent surface and session. Capability evidence keeps four layers
separate:

```text
official_claim -> local_presence -> live_callable -> task_verified
```

The layers are cumulative evidence, not four names for one boolean. A strong
official claim does not prove local presence; installation does not prove live
callability; a successful ping does not prove task performance. Capability
passports combine these layers with:

```text
official or declared agent capability
installed skills
current MCP/tool availability
runtime status
permission boundaries
context policy and compression budget
skill recommendation evidence
provider matrix evidence
historical verification/review quality
local overlay capability profile
recent feedback records
```

Doctor SHOULD discover every Agent surface and addressable session available to
the installation, including multiple surfaces backed by the same vendor or
model family. It MUST keep those surfaces separate. Discovery adapters publish
their provenance and unsupported fields; Doctor must not invent facts that an
adapter cannot observe safely.

Doctor MUST commission passports from the union of static capability registry
entries, local overlay profiles, and surfaces returned by the active runtime
adapter. No one source may silently remove a surface reported by another. A
surface known only to the overlay or runtime remains `unknown` for installation,
role, model, permission, and other facts that were not directly observed.

Only command output, receipts, expected evidence, and review records prove that
work is done. The Leader uses capability passports to decide assignments. VALP
uses them to validate those assignments and block hard-gate violations.

Capability profiles are not assignments. They answer "what is this agent often
good at?" The Leader answers "which Agent should do this work?" VALP answers
"is that declared assignment supported by current evidence and protocol gates?"

### 7.1 Model-Aware Routing Identity

A capability route MUST distinguish the agent surface from the model that
actually served the current runtime turn. Its routing identity is the tuple:

agent surface
actual runtime model
provider
reasoning mode
permissions
context policy and current context
task evidence

Provider matrix records MUST carry declared_model and observed_model separately;
declared_model may explicitly be unknown.
Each model record MUST include a model identifier (or the explicit value
unknown), source, timestamp, confidence, and freshness. observed_model is
runtime evidence; a declaration, configured default, or provider label cannot
substitute for it.

The record MUST include mismatch handling. A declaration/observation mismatch
invalidates model-bound capability history for that route. A stale, low
confidence, or unknown observation downgrades capability evidence. Unknown
model identity remains explicit and MUST NOT qualify as strong evidence for
high-risk implementation or final review.

model_selection: runtime_default alone is not model-aware evidence. A
model-aware provider matrix MUST instead record the model identity object and
an evidence status of strong, degraded, unknown, or invalid. The reference CLI
MUST reject a matrix that claims strong model-aware evidence while the observed
identity is unknown, stale, low-confidence, or mismatched.

When the runtime model changes, the adapter MUST either invalidate the affected
history or mark it downgraded until a fresh observation and task evidence
requalify the route. A Leader session and a Worker session remain separate
Agent surfaces even when they use the same product and provider family; one
session's model observation MUST NOT qualify the other session's evidence.

#### 7.1.1 Dynamic Model Observation

A model-aware Full or Remote Mode route MUST evaluate a task-local runtime probe
for every candidate considered for implementation or final review. The probe
MUST use adapter-visible runtime metadata. It MUST NOT read credentials, secret
values, raw user-level provider configuration, or another surface's private
session state to infer a model.

Unstructured pane, transcript, conversation, status-footer, or dispatch text
MUST NOT qualify as model evidence. If the agent surface does not expose a
structured active-model field through its adapter contract, the probe produces
`unsupported` rather than a guessed identity. A task-owned launch contract MAY
be recorded as `launch_attested` evidence when its provisioning receipt is
immutable, identity-bound, and has one unambiguous model selection, but it is
not a runtime observation and MUST NOT change the probe from `unsupported`.
Product-name inference, inferred launch defaults, ambiguous flags, or an
unbound launch contract remain unsupported.

Every structured metadata key accepted by a model probe MUST be defined by its
adapter as the active LLM identity. A generic deployment name, product name, or
unscoped `model_name` field is not sufficient evidence unless that adapter
contract gives it the active-LLM meaning.

New task evidence that adopts this contract records
`model_awareness.dynamic_discovery_required: true`. The marker makes dynamic
probe, TTL, session-binding, history-binding, and role-gate checks audit-fatal.
Its absence on immutable historical tasks does not claim dynamic conformance.

The closed probe result is `valp-model-probe.v1` and records:

```text
status: observed | unsupported | unavailable | error
source
observed_at
ttl_seconds
model: model_id, provider, reasoning_mode, confidence
session_identity: status, token, source, generation
```

Session tokens and generation values exposed by a probe or durable binding MUST
be adapter-scoped opaque identifiers. Raw process IDs, process-group IDs, thread
IDs, or similar host-local runtime identifiers may contribute to an opaque
digest, but MUST NOT be emitted verbatim in protocol artifacts.

`unsupported` means the adapter was reached but does not expose the active model
through safe metadata. `unavailable` means the target runtime session was not
present or could not be addressed. Neither status may be replaced with a stored
declaration or guessed provider default. A static registry observation may be
retained as historical evidence, but it is not a live probe.

Freshness MUST be computed when routing or auditing, not copied from a stored
`freshness` label. The reference TTL defaults to 3600 seconds and MUST remain
between 60 and 86400 seconds. The evaluated identity records the effective TTL,
observation age, evaluation time, and computed state `current`, `stale`, or
`unknown`. An observation becomes stale as soon as its age exceeds the effective
TTL. Invalid timestamps, future timestamps outside a small clock-skew allowance,
and non-observed probes produce `unknown` freshness.

The session identity token MUST be non-secret and scoped to the adapter-visible
agent session. An adapter SHOULD derive it from a runtime-issued session id or
generation. It MAY record a deterministic digest of an allowlisted tuple such as
runtime id, pane or worker id, terminal or hosted-run id, and adapter generation;
it MUST NOT expose the raw tuple when it may contain private runtime state. A pane
id alone is not sufficient when the adapter can reuse the pane across agent
restarts. A local pane adapter MAY include a foreground process-generation value
in the digested tuple, but MUST NOT persist that raw value. If the adapter cannot
produce a session-change identity, it records an unknown session identity and the
observation cannot qualify a high-risk role.

Each model-bound capability-history record MUST carry a binding over:

```text
agent surface
model id
provider
reasoning mode
computed TTL state
session identity token
```

The route MUST invalidate model-bound history when any bound value changes, when
freshness changes from current to stale or unknown, when a declaration/observation
mismatch occurs, or when a current binding cannot be compared with the binding
that qualified the history. Invalidated history contributes no positive routing
score until fresh task evidence requalifies the new binding.

For implementation and final-review roles, a declared Agent is eligible only when the
active model is known, the probe status is `observed`, computed freshness is
`current`, and the session identity is known. An ineligible Agent MUST NOT be
dispatched even when it is the only available Agent. VALP MUST record the missing
capability and block. It may offer discovery, prototype, or Manual Mode as
advisory alternatives, but the Leader or user chooses the next declaration.
Coordinator-only state work may
continue when its own permission and evidence gates allow it.

Immediately before a high-risk dispatch is submitted, the adapter MUST probe
again and compare the fresh history-binding fingerprint with the binding used by
assignment validation. A model, provider, reasoning-mode, freshness-state, or session change,
or a newly ineligible identity, MUST block before delivery and write visible
task-local block evidence. Dispatch-time preflight cannot reuse the route-time
`current` label without reevaluation.

## 8. Leader Assignment And VALP Validation

The user-selected Leader is the assignment authority. VALP provides capability
facts, advisory fit scores, hard-gate validation, visible dispatch records, and
audit evidence. It MUST NOT authoritatively select the Leader, worker, reviewer,
researcher, or squad members.

Minimum assignment and validation steps:

```text
user selects the Leader
VALP starts and binds the exact installation-owned Leader session
Leader reads installation capability passports
Leader decomposes the task into runtime work items
Leader identifies required capabilities and evidence gates
Leader declares Agent and role assignments
VALP refreshes current model/session/runtime/permission/context facts
VALP validates every declared assignment against hard gates
VALP records advisory scores, risks, alternatives, and missing capabilities
Leader accepts, changes, narrows, or blocks the declaration
VALP writes precise visible dispatch payloads and receipts
```

The task-local authority records are:

```text
<task>/assignment-declaration.json
<task>/assignment-validation.json
```

`assignment-declaration.json` MUST use `valp-assignment-declaration.v1` and
record a declaration ID, task ID, declaration time, the user-selected Leader and
selection evidence, role-to-Agent assignments, and a reason for every assigned
role. The Leader is the coordinator authority and does not have to be routed as
a worker. If the declaration includes a runtime `coordinator` assignment, it
MUST match the user-selected Leader. VALP MUST reject an incomplete or
mismatched declaration before capability scan or dispatch.

The compatibility field `selected_agents` MUST be the unique projection of the
Leader's role assignments. The Leader is not included merely for being Leader;
it appears only when explicitly assigned a runtime role. VALP MUST NOT add an
Agent to this projection.

`assignment-validation.json` MUST use `valp-assignment-validation.v1` and record
the declaration ref, `leader_declared` authority, validation time, exact
validated assignments, status, and visible blockers. `pass` requires an empty
blocker list; `blocked` requires at least one concrete blocker. A blocked
validation MUST NOT write dispatch receipts.

Each Doctor passport MUST use `valp-capability-passport.v1`. A Doctor report may
carry passports inline or an installation may store them separately, but every
passport remains bound to one Agent surface and addressable session. The schema
does not grant assignment authority.

Leader selection is explicit user input, not a routing output. VALP must not
hard-code one vendor, product, local Agent name, model, or runtime session type
as the universal Leader. A local overlay may record preferences and Doctor may
report eligibility, but neither can select a Leader. Rotation requires a new
explicit user selection and a recorded epoch change.

VALP may reject or block a declared assignment when a protocol hard gate fails,
for example missing permission, unavailable runtime, unknown or stale model
identity for a high-risk role, missing reviewer independence, or insufficient
expected evidence. A validation failure MUST identify the exact missing fact or
gate. VALP may present alternatives as advisory facts, but it MUST NOT silently
substitute another Agent. The Leader or user owns the revised declaration.

The user-selected Leader and any explicitly declared runtime coordinator
surface own dispatch precision. They must break work into short, role-specific assignments and cite
task-local files for detail. It must not shift context-management work onto
workers by pasting the full conversation, full task history, or broad skill
router output into every dispatch.

Coordinator patterns:

- Pane-controller runtime: the user-selected Leader may act through a visible
  coordinator surface; the runtime records the selection and session identity.
- Daemon or hosted runtime: the runtime process may maintain coordinator state
  only under the Leader's recorded assignment declaration and only if it writes
  visible dispatches, receipts, gates, and final synthesis evidence.
- Manual Mode: a human coordinator may copy dispatches and write attestations,
  but must not label those attestations as Full Mode submission proof.
- Squad routing: the Leader may declare a squad leader and members only when the
  declaration, member list, and handoffs are visible evidence.

Recommended scoring factors:

| Factor | Meaning |
|---|---|
| profile_fit | match between task profile and agent capability profile |
| tool_fit | current tools/MCP/runtime support needed for the task |
| skill_fit | installed skill or recommendation match |
| permission_fit | whether the agent is allowed to do the work |
| context_fit | whether context budget is below hard threshold |
| evidence_history | recent verification/review quality for similar work |
| availability | runtime status, queue pressure, or pane readiness |
| risk_fit | whether the agent should handle high-risk or read-only work |

Scores are advisory and MUST NOT become hidden assignment authority. The
validation output must explain why the Leader's declaration is supported,
degraded, or blocked in plain language and list hard blockers separately.

Low confidence rules:

- If no declared Agent has the needed tools, mark `capabilities_missing` and
  return the block to the Leader or user.
- If only one Agent appears viable but confidence is low, recommend a small
  discovery task; the Leader decides whether to declare it.
- If implementation confidence is medium but risk is high, require independent
  review before mutation.
- If context policy is near the hard threshold, compress before dispatch.
- If pane or CLI preflight fails, block dispatch until the Leader changes the
  declaration, chooses Manual Mode, or the runtime is repaired.
- If a prior feedback record says an agent failed this task type recently,
  require fresh evidence before validating a similar assignment again.

## 9. Runtime Modes

Full Mode requires a VALP-compatible runtime. HERDR is one reference runtime,
not the protocol's required coordinator or leader. Manual Mode is a valid
learning and audit workflow, but it must not claim Full Mode automation
guarantees.

Full Mode must support:

```text
agent list
agent status
agent read
agent send/insert
agent session/message submit
submission proof
wait for status
task evidence writing
dispatch receipt ledger
```

When Full or Remote Mode claims asynchronous suspended waiting, it MUST also
export a versioned wait policy, identity-bound deterministic receipts, an
append-only accepted event sequence, a revisioned state projection, and the
immutable accepted wake result. A runtime that cannot export those artifacts
MUST degrade the capability claim instead of describing agent polling or a
manual attestation as deterministic automatic wake.

Daemon or platform runtimes may satisfy Full Mode when their adapter exports
equivalent submission proof, state transitions, output references, and evidence
locations. Queue completion alone is not enough.

Manual Mode may write task folders and evidence files, but it cannot claim
automatic dispatch proof, runtime-backed status waits, or Full Mode receipt
equivalence. It may use a foreground/manual wait, but MUST record that
deterministic automatic wake is unsupported or degraded.

## 10. Dispatch Receipts

Valid receipt states:

```text
dispatch_written
dispatch_inserted
dispatch_submitted
dispatch_completed
dispatch_blocked
dispatch_superseded
```

Manual Mode may also record manual-only receipt labels:

```text
manual_dispatch_written
manual_delivery_attested
manual_result_attested
manual_blocked
```

Manual labels are useful audit records, but they are not Full Mode runtime
proof. `manual_result_attested` may satisfy a Manual Mode evidence trail only
when expected evidence exists; it must not be reported as `dispatch_submitted`.

Rules:

- `dispatch_written` means the dispatch file exists and was surfaced.
- `dispatch_inserted` means text entered an input box. It is not delivery.
- `dispatch_submitted` means a Full or Remote Adapter proved both causal
  invocation and acknowledgement of the exact submitted payload. A transport
  observation alone is not submission.
- `dispatch_completed` means a prior valid submission, a process-bound terminal
  observation, and content-bound expected evidence all exist for the same
  Attempt.
- `dispatch_blocked` means submission or completion could not be proven; it
  MUST NOT claim an external runtime stopped unless matching runtime proof
  exists.
- `manual_result_attested` means a human coordinator attests that expected
  evidence exists in a Manual Mode task. The attestation is identity-, scope-,
  revision-, and digest-bound and is never runtime proof.

Legacy receipts identify an agent-level event and remain readable for existing
tasks. Protocol `0.2` deterministic receipts use
`valp-dispatch-receipt.v2`. Protocol `0.3` writers use
`valp-dispatch-receipt.v3`, preserve the fields below, and additionally bind an
Attempt ID, mode, payload digest, and the proof records required by Section 21:

```text
receipt_id
task_id
event_sequence
agent
role
work_item_id
attempt_id
dispatch_id
dispatch_generation
expected_refs
suspension_epoch for terminal events
```

These fields form one safety identity. A missing or mismatched field cannot be
repaired from agent name, timestamp, file presence, or coordinator intent.

For `valp-dispatch-receipt.v3`, `mode` is closed to `full`, `remote`, and
`manual`; `proof_kind` is closed to `process_bound`, `content_bound`,
`manual_attested`, and `transport_only`. Every v3 receipt additionally binds
the installation ID, Leader epoch, exact payload digest, proof ref and digest,
ledger revision, prior receipt digest, and its own canonical receipt digest.
The first v3 receipt binds the canonical empty receipt-ledger digest. Later
receipts increment both ledger revision and event sequence by exactly one and
bind the immediately preceding canonical receipt digest. Boolean values are
not integer revisions, sequences, epochs, or generations.

If expected evidence is declared, gates require `dispatch_completed`.

For Full Mode and Remote Mode, `dispatch_completed` is not valid by itself. The
receipt ledger MUST contain a prior `dispatch_submitted` receipt for the same
Task, Work Item, Attempt, dispatch ID, and generation. Submission requires a
process-bound invocation proof and a content-bound payload acknowledgement.
Completion additionally requires process-bound terminal observation and
content-bound expected evidence. Remote Mode carries the remote proof issuer,
host identity, observation sequence, and evidence location. A dry-run command,
terminal insertion, local sub-agent result, simulation, manual attestation,
manually fabricated completion receipt, or copied review file cannot be
upgraded into Full or Remote completion.

A concrete proof identity or ref MUST be a non-empty adapter-issued string,
either directly or inside a typed structured adapter record. Boolean flags,
numeric counters, and non-empty containers without such an identity or ref do
not prove delivery.

The HERDR reference adapter uses the atomic Agent interface introduced in HERDR
v0.7.5 for Full Mode submission proof. Before delivery it MUST read the exact
routed Agent and record a settled, identity-bound baseline containing the live
Agent name, Agent kind, pane id, terminal id, and integer `state_change_seq`.
It MUST then invoke the same target with `herdr agent prompt <target> <payload>
--wait --until working` and a bounded timeout. The successful response MUST have
result type `agent_prompted`, report `agent_status: working`, repeat the same
identity fields, and report a
`state_change_seq` strictly greater than the baseline. A missing field, changed
identity, non-settled baseline, non-advancing sequence, timeout, or
`agent_prompt_stalled` response MUST fail closed without a
`dispatch_submitted` receipt.

HERDR's top-level response `id` is request correlation, not an execution
identity. A `submission_id`, generic response id, status string, revision,
counter, pane id, or visible text MUST NOT be invented, extracted, or accepted
as a substitute for the identity-bound `state_change_seq` advancement above.

Completion observation MUST use the same structured Agent identity. The
reference adapter polls `herdr agent get` because HERDR terminal states are
`idle` and `blocked`, while `agent wait` accepts only one target status. A
strictly later `idle` state maps to `completed`; a strictly later `blocked`
state maps to `blocked`. `working`, `unknown`, identity drift, and replayed or
non-advancing sequences do not produce a terminal receipt.

Older HERDR compatibility paths that insert text, send Enter, and observe
`agent wait` may prove only transport and observation. Without an independent
Agent invocation receipt, the adapter MAY append `dispatch_inserted` with its
transport evidence and continue only as explicitly recorded Manual-degraded
operation. It MUST NOT append `dispatch_submitted`, claim Full Mode, or use a
later `working`, `idle`, `done`, or `blocked` observation to upgrade that
transport receipt.

For a controlling agent that is executing its own assigned work, the runtime
must not paste the controlling agent's dispatch back into its own live context.
Instead, the controlling agent writes compact task-local evidence and the
adapter records a `dispatch_completed` receipt only after the expected evidence
files exist. This preserves receipt semantics without self-prompt pollution, but
it is controller-local evidence unless an adapter also records runtime
submission proof. Controller-local evidence must not be described as HERDR live
agent dispatch.

Receipt ledgers are append-only. Legacy gates evaluate the latest receipt for
each Leader-declared Agent. Deterministic gates MUST evaluate the latest accepted
receipt for the exact task, work item, role, dispatch id, and dispatch
generation. A later matching `dispatch_blocked` supersedes an earlier
`dispatch_completed` until a newer matching `dispatch_completed` records the
recovered evidence. A receipt from another role, work item, generation,
suspension epoch, or task is unrelated evidence and cannot supersede or satisfy
the gate.

`dispatch_superseded` is a ledger-management event, not a Worker outcome. It
MAY supersede one earlier `dispatch_submitted` receipt only when its proof names
`kind: invalid_session_binding`, the exact earlier submission receipt ID, and a
later replacement `dispatch_submitted` receipt for the identical task, Agent,
role, work item, dispatch ID, generation, dispatch ref, and expected refs. The
replacement MUST have valid task-owned session-binding provenance; the
superseded submission MUST fail that same provenance check. A supersession
cannot target a completion, cannot change the original bytes, and is ignored
when deciding the latest Worker outcome.

Receipt timestamps are descriptive. Deterministic wake ordering uses the
accepted event sequence and revision CAS. Duplicate receipt or event ids MUST
be replayed idempotently or rejected on content conflict; they MUST NOT create a
second wake.

When multiple adapter processes share a file-backed receipt ledger, sequence
allocation and durable append MUST occur in one inter-process locked
transaction. Reading the current maximum and appending under separate lock
epochs is not deterministic ordering.

A retry of the same task, dispatch ID, dispatch generation, work item, and
submission event MUST use a stable logical submission identity. If an identical
receipt already exists, the retry MUST replay it without allocating a new
sequence. Same-generation content conflicts MUST fail closed. For adapters that
export queue and receipt files separately, a queue file without its matching
receipt is prepared data, not submission proof, and MUST be reconciled before a
worker treats it as delivered.

An incomplete-submission recovery is an explicit reconciliation request under
the same task, work item, role, dispatch ID, and dispatch generation. It is
permitted only when exactly one matching concrete `dispatch_submitted` receipt
exists, no conflicting terminal receipt exists, and the current worker control
contract and agent slice preserve that identity. After those checks, the
runtime MUST choose exactly one outcome. If every expected evidence ref is
valid, it MUST append or idempotently replay an identity-bound
`dispatch_completed` receipt for the originating submission without runtime
preflight, worker submission, or a retry `dispatch_submitted` receipt. If every
expected evidence ref remains absent or invalid, it MAY perform the bounded
transport retry: that receipt MUST record a positive `retry_generation`, bind
the originating submission receipt and current worker control-contract digest
in its proof, and use a stable receipt ID for that retry generation. A partial
evidence set MUST fail closed.

The reference recovery is bounded to one retry generation. Replay MUST be
idempotent or fail closed, changed identity MUST fail closed, and a failed
recovery transport MUST NOT feed the ordinary automatic dispatch retry or
permit another explicit recovery submission. The append-only ledger and the
originating submission receipt remain unchanged.

## 10.1 Correction Cycle Evidence

Runtimes may implement self-correcting loops, automatic retries, repair queues,
or human review/fix rounds. VALP does not prescribe that implementation. VALP
does require the evidence trail when work is rejected, retried, blocked, marked
invalid, or superseded.

When a task records any of these signals, the task should write:

```text
<task>/correction-cycle.json
```

Trigger signals include:

```text
dispatch_blocked
expected_evidence_missing
evidence_rejected
evidence_superseded
evidence_invalid
review_blocker
verification_failed
runtime_timeout
runtime_failure
approval_block
context_policy_block
manual_retry
```

The correction cycle records:

```text
task id
maximum allowed rounds
round number
trigger
owner
reason
rejected or superseded refs
required actions
replacement evidence refs
receipt refs
final outcome
```

The final outcome is one of:

```text
fixed
blocked
escalated
cancelled
not_required
```

For a task to satisfy Done Criteria after a correction signal, the correction
cycle's final outcome must be `fixed`, replacement evidence must exist, and the
normal receipt, review, verification, approval, and final synthesis gates must
still pass. A blocked or escalated correction cycle is useful state, but it is
not completion proof.

This keeps the protocol boundary narrow: a runtime may use rules, tests, model
review, queues, or human operators to correct work. VALP only requires enough
machine-readable evidence to audit what was rejected, what changed, and why the
task is now acceptable or still blocked.

## 10.2 Submission Dependencies

New routed tasks with coordinator, implementer, and reviewer roles must write a
closed `submission-dependencies.json` artifact. At minimum it declares these
role gates when the roles exist:

```text
coordinator self-review completed -> implementer dispatch may be submitted
implementer evidence and verification completed -> reviewer dispatch may be submitted
```

Each dependency records a stable id, prerequisite role and agent, dependent
role and agent, prerequisite evidence refs, and dependent evidence refs. The
artifact must match the current role assignments and cannot omit or weaken a
generated edge. Co-located roles still require role-scoped receipts whose
`expected_refs` distinguish the phases; agent identity alone is not enough.

Version 2 also records the task's work-item table. Each work item binds role,
agent, dispatch id, dispatch generation, and expected refs; dependency edges
refer to the prerequisite and dependent work-item ids and generations. Version
1 remains readable for historical submission ordering but does not contain
enough identity to qualify a new deterministic suspension.

`wait-policy.json` MUST reference this artifact and select the work items needed
by the next coordinator step. It MUST NOT restate or replace the dependency
edges. Its work-item records add the dispatch identity needed to evaluate one
suspension epoch. This keeps submission order and wait readiness on one
dependency truth.

For Full and Remote Mode, every prerequisite ref must exist and remain valid,
and a matching `dispatch_completed` receipt must appear earlier in
`dispatch-receipts.jsonl` than the dependent role's `dispatch_submitted`
receipt. For Manual Mode, the equivalent ordering is
`manual_result_attested` before `manual_delivery_attested`. Physical JSONL line
order is authoritative. Timestamps are descriptive and must not be used to
repair reversed ledger order.

If the declared prerequisite generation was superseded by a correction, a
later Full or Remote Mode `dispatch_completed` receipt MAY satisfy the same
dependency only when all of these conditions hold: the correction cycle and a
binding round both have outcome `fixed`; the receipt keeps the same task,
agent, role, and `work_item_id`; its dispatch generation is greater than the
declared generation; the fixed round records `evidence_superseded`, names every
declared ref as rejected, keeps those refs marked `superseded`, and binds the
append-only receipt ledger; its expected refs are replacement evidence named by
both the fixed round and the correction cycle's final evidence; and every
replacement ref exists and remains valid. The latest qualifying correction
generation is used. Its completion receipt still MUST physically precede the
dependent submission. Earlier receipts and superseded evidence remain immutable
history; the auditor does not relabel or rewrite them. Missing, active, blocked,
invalid, cross-work-item, stale, or reverse-ordered correction evidence fails
closed.

Reference dispatch tools must validate dependency gates before preflight, queue
writes, subprocess submission, or any other delivery side effect. An explicit
agent or role request that includes an uncleared target fails as a whole.
Default all-agent automatic progression instead derives the current dependency
frontier: it submits only work items whose prerequisites are complete, excludes
already completed or currently submitted work items, and leaves later work for
the next post-wake call. One frontier is submitted atomically; a call must not
partially advance through multiple ordered frontiers. Manual Mode can print
dependency-aware instructions, but only the later ordered attestations prove
that a human followed them.

When a Full or Remote Mode adapter is invoked with a zero evidence-wait window,
the operation is submission-only. The adapter must return after concrete
delivery proof without treating absent immediate evidence as
`dispatch_blocked`. VALP binds that delivery proof to the routed work item and
`valp wait` owns later expected-evidence observation, completion receipt
creation, and deterministic wake. A zero evidence-wait window is not a zero
submission-proof window.

## 11. Context Compression

Context compression is part of capability scanning, not a late-stage cleanup.
Each agent has a `context_policy`.

Default hard compression thresholds:

| Agent role | Soft warning | Hard compression | Emergency stop |
|---|---:|---:|---:|
| coordinator | 50% | 60% | 80% |
| implementer | 55% | 65% | 80% |
| reviewer | 60% | 70% | 80% |
| prototype | 60% | 70% | 80% |
| other | 60% | 70% | 80% |

Default checkpoint behavior:

```text
checkpoint_interval_minutes: 45
checkpoint_after_phase: true
checkpoint_after_fix_review_rounds: 2
compression_target_pct_min: 15
compression_target_pct_max: 25
```

User or project policy may override defaults.

Assignment validation must treat these thresholds as a pre-dispatch gate. If current context
is at or above `hard_compression_pct`, or if the runtime marks
`compression_required`, no new implementation/review/prototype dispatch should
be sent until the agent writes a compression handoff and the task state is
revalidated.

### 11.1 Dispatch Payload Budget

Dispatch generation is a coordinator/leader responsibility. The selected leader
must send each worker a precise, concise assignment and use task-local file refs
for context expansion. This applies to direct routing, squad routing, hosted
runtimes, pane runtimes, queues, and Manual Mode.

The complete canonical dispatch must satisfy the selected role budget. The
reference profile is:

| Primary role | Max characters | Max reference tokens |
|---|---:|---:|
| coordinator | 3000 | 750 |
| implementer | 2800 | 700 |
| reviewer | 2400 | 600 |
| prototype or researcher | 2400 | 600 |
| other | 2200 | 550 |

Characters are Unicode code points. Reference tokens use the deterministic
estimate `ceil(character_count / 4)`. This estimate is for portable budgeting,
not a claim about any provider tokenizer. An adapter with an exact tokenizer
must enforce the lower of its provider limit and the recorded VALP role budget.
The generated task context pack records the role budgets and estimator; each
dispatch records its actual character count and reference-token estimate.

Budget enforcement must be deterministic. If essential content would exceed a
ceiling, the coordinator shortens prose and skill labels first, then replaces
detail with task-local refs. It must not truncate a permission boundary,
expected evidence path, receipt requirement, or approval rule. A dispatch that
still exceeds either ceiling is not submitted and must be recorded as blocked.

### 11.2 Leader-Declared Team And Iteration Budget

After reading current MCP/tool scans and task-relevant skill recommendations,
the Leader declares the smallest team they judge sufficient for the required
roles. VALP validates the declaration and bounds its execution; it does not add,
remove, or substitute Agents. A task records `iteration-budget.json` with these
ceilings and observed counters:

```text
max_dispatch_reference_tokens
max_dispatches
max_reroutes
max_fix_review_rounds
usage.dispatch_reference_tokens
usage.dispatches
usage.reroutes
usage.fix_review_rounds
```

The reference implementation counts accepted submission receipts, not merely
dispatch files, and derives reference-token usage from the recorded dispatch
payload measurement. Before a new submitted phase it projects the additional
usage and stops when a ceiling would be exceeded. Approval, runtime preflight,
missing-evidence, critical review, and context-compression gates stop the loop
even when budget remains. A revised Leader declaration preserves the previous
validation evidence and consumes one reroute budget unit.

When a runtime preserves a legacy receipt and appends an identity-bound v2
translation for the same accepted delivery, the pair counts as one logical
dispatch. The v2 work-item identity is authoritative; compatibility records
must not consume the task budget twice.

The full `skill-recommendations.json` report is coordinator-only context for
this purpose. Each selected provider receives a task-local
`skill-slices/<agent>.json` containing only provider-reachable installed matches.
The slice is routing evidence, not permission to load or modify a skill.

A current or newly generated dispatch must never use a historical audit
boundary to bypass this rule. A terminal historical task may use
`historical-audit-boundary.json` only when the dispatch was accepted under a
named source revision where the named audit rule did not exist and a later,
separate reconciliation task records the compatibility decision. Each accepted
legacy artifact must bind all of the following without wildcards:

```text
rule id
agent
safe task-local artifact ref
exact raw-byte SHA-256 digest
exact recorded role, limits, estimator, and measurements
exact observed measurements under the current auditor
historical auditor source revision
rule-introduction source revision
external reconciliation task id and evidence ref
```

The auditor must recompute the byte digest and every recorded and observed
value. A missing decision ref, duplicate acceptance, changed byte, changed
measurement, unsafe path, unknown rule, or partial multi-artifact declaration
fails closed. The digest establishes byte identity only. A valid boundary may
let the later audit continue only with `WARN` and must disclose the preserved
nonconformity; it must never convert that item to `PASS`. The boundary must not
claim that the dispatch met the later rule when submitted, raise a limit,
rewrite a receipt, or authorize the same exception for a current task.

A dispatch should include:

```text
short task brief
role and capability match
permission boundary
expected evidence refs
visible attention slice
recommended skills as short work-item labels
refs to full task, routing, context selection, masks, evidence board, and skill recommendations
```

A dispatch should not include:

```text
full chat transcript
full task history when a brief plus task.md ref is enough
repeated long skill recommendation task text
stale memory without file-backed evidence
hidden coordinator reasoning
```

The full `task.md` and `skill-recommendations.json` remain task evidence. They
are not required to be copied into every worker prompt. Skill recommendations in
dispatch should use short work-item labels and point to the full recommendation
record when more detail is needed.

Progressive disclosure uses the smallest role-specific starting set:

```text
all roles: task.md, automation-policy.json, visible-routing.md
coordinator: state, receipts, evidence board, recommendation resolution
implementer: source/build refs and expected implementation evidence
reviewer: changed refs, verification evidence, findings/review output
prototype or researcher: task scope, selected context, expected role evidence
```

Workers load `routing.json`, `context-selection.json`, `context-pack.json`,
`mask-list.json`, `evidence-board.json`, or `skill-recommendations.json` only
when the assignment requires that detail. The files remain visible task
evidence even when their names are not duplicated into every dispatch.

Plain text or Markdown is the canonical worker dispatch format. HTML or other
rich formats may render reports, dashboards, or evidence summaries, but they
must not replace the concise canonical dispatch unless the runtime exports the
same readable assignment and receipt evidence.

## 12. Provider Matrix

Every routed agent should have a current provider capability record before work
is assigned.

Minimum fields:

```text
provider_name
provider_version_or_runtime_report
cli_available
mcp_support
skill_discovery_path
session_resume_support
approval_behavior
model_selection
max_concurrency
context_policy
runtime_preflight
known_limitations
last_verified_at

Model-aware records add:

agent_surface
declared_model {model_id, provider, reasoning_mode, source, timestamp, confidence, freshness}
observed_model {model_id, provider, reasoning_mode, source, timestamp, confidence, freshness}
model_mismatch {status, handling, details}
model_evidence_status
permissions
context
task_evidence

model_evidence_status: strong is valid only when the observed model is
identified with current, high-confidence, session-bound evidence and either the
declaration matches the observation or no model was declared. A provider record
using only runtime_default MUST be unknown or rejected, never strong.

Dynamic model-aware records also add:

model_probe {schema_version, status, source, observed_at, ttl_seconds, model, session_identity}
freshness_evaluated_at
observation_age_seconds
observation_ttl_seconds
history_binding
history_invalidation_reasons
role_eligibility {implementer, final_reviewer}
```

The provider matrix is evidence, not marketing. It must be generated from
current runtime status, installed tools, official documentation, or explicit
operator configuration. Missing values must be recorded as unknown instead of
guessed.

Provider matrix scanning should use real local/runtime probes where possible:

```text
runtime status command
pane list and pane layout
agent CLI version command
installed skill library paths
MCP/tool availability
context policy and current context signal
recent task-local feedback
```

Runtime model probes are metadata probes, not provider configuration scans. If
the adapter does not expose active model and session identity safely, record
`unsupported` or `unavailable`; do not open private configuration to fill the
gap.

For TUI agents that are sensitive to small panes, such as prototype or design
agents, `runtime_preflight` should include the pane size and the minimum size
used by the adapter.

## 13. Skill Recommendation

Skill recommendation is abstract. The protocol does not require a specific
router implementation.

The useful extracted pattern is:

```text
understand request
  -> decompose into runtime work items
  -> rank installed skills against each task
  -> surface missing useful skills
  -> record recommendation evidence
```

Recommendation output is evidence, not authority. It cannot bypass role
boundaries, approval gates, receipt gates, or context gates.

When a recommendation backend is available, Full Mode routing should execute it
after decomposition and before dispatch. The result must be written to task
evidence, normally:

```text
<task>/skill-recommendations.json
```

If the backend supports target-agent filtering, the routing layer should also
write provider-filtered results under:

```text
<task>/skill-recommendations.json#per_agent
```

Dispatch prompts must prefer `per_agent.<agent>` over task-level aggregate
recommendations. The aggregate result is useful for capability scanning, but it
can surface irrelevant provider-specific skills when broad task text includes
overloaded words such as "Apple", "agent", or "review".

Dispatch prompts must surface relevant installed skills to the target agent.
They should include:

```text
short runtime work-item label
recommended skill name
installed or missing status
confidence/mode/decision
skill path or install hint
instruction that recommendations are aids, not permission grants
ref to the full skill-recommendations.json record
```

An agent should use or load a recommended skill only when it matches the agent's
role and materially improves the runtime work item. If a useful skill is
missing, the task should record the gap instead of silently proceeding as if the
skill were available.

## 14. Visible Attention For Declared Assignments

Visible attention makes the Leader's declaration and VALP's context selection
inspectable. It borrows the useful systems idea from attention mechanisms:
focus each Leader-declared Agent on the relevant skills, context, and evidence
instead of making every participant read everything. Unlike a hidden optimizer,
VALP must surface the assignment, validation, context selection, and masking
decisions before dispatch.

Visible attention happens after capability/skill scans and assignment
validation, before Agent dispatch.

Required task evidence:

```text
<task>/attention-map.json
<task>/context-selection.json
<task>/context-pack.json
<task>/mask-list.json
<task>/evidence-board.json
<task>/visible-routing.md
```

The five JSON artifacts must all carry `schema_version`, `profile`, and
`loop_layer`. `attention-map.json` additionally carries `task_id`, the
user-selected `leader_agent`, and attention heads. The Leader identity is
recorded independently of routed worker assignments. `context-selection.json`
carries selected and not-selected context.
`context-pack.json` carries the compact context given to workers. `mask-list.json`
carries excluded inputs and reasons. `evidence-board.json` carries claims and
required evidence.

The attention map records:

```text
loop_layer
attention heads such as implementation, ux_review, prototype, state_gate
user-selected Leader for state_gate, without implying a worker assignment
Leader-declared Agent or source for each worker head
advisory score or validation status for the declaration
references to selected context, masks, and evidence board
```

The loop layer should be one of:

```text
agentic_coding_loop       minutes-scale agent build/test/fix loop
developer_feedback_loop   hours-scale human/product/design steering loop
external_feedback_loop    days-or-longer user/beta/production feedback loop
```

The context-selection record lists what the task selected to read and what it
excluded by default. The mask-list records inputs that must not influence the
decision, such as stale chat memory, hidden votes, prototype-as-production-proof,
unapproved release operations, or invalid/superseded evidence.

The context pack is the dispatch-facing compression artifact. It should include
only task-relevant summaries backed by visible refs:

```text
project rules
operator preferences, when a local overlay exposes them
known pitfalls from prior evidence-backed feedback
task scope and out-of-scope boundaries
verification expectations
permission boundaries
routing priors and their evidence refs
```

The context pack must not include secrets, raw private transcripts, hidden
votes, unverified memory, or broad personal preferences that are not relevant to
the current task. It is a compact working packet, not a memory dump.

The evidence board turns claims into evidence requirements before execution.
For example, a UI claim should require a build/test log and a real screenshot,
not just code inspection.

Dispatch prompts should include the recipient's visible attention slice:

```text
loop layer
the recipient's attention head
selected context paths
context pack ref
masked inputs
design contract status, when relevant
```

Audit should fail non-trivial routed tasks when visible attention evidence is
missing or malformed. This keeps automation visible without forcing the user to
inspect every low-level command.

A non-trivial routed task is any task with more than one Leader-declared Agent,
or any task in a profile that normally needs external evidence, source review,
artifact review, release gates, runtime repair, implementation, verification, or
prototype evidence. Current examples include `software-code`, `apple-app`,
`web-frontend`, `research`, `document-artifact`, `agent-runtime`, `ops-release`,
and `prototype`. A single-agent, no-runtime learning task may skip visible
attention when it records that it is simple Manual Mode.

## 15. Leader-Declared Squads

Squads are optional. The user-selected Leader may declare a squad when one
coordinating Agent should delegate to visible members instead of assigning one
worker directly.

Rules:

- squad existence, squad leader, members, and assignment reason must be visible;
- leader dispatch is a dispatch, not hidden reasoning;
- leader output must include either a concrete delegation, `no_action`, or
  escalation;
- member delegation must create its own dispatch receipt;
- leader judgment cannot bypass provider matrix, context policy, approval gates,
  or expected evidence;
- agent-to-agent mentions or delegations must avoid accidental loops.

Squad validation is assignment evidence. It is not completion evidence.

## 16. Feedback And Learning

VALP should learn from outcomes without becoming stale memory.

After each non-trivial task, write a feedback record when the runtime supports
it. The record should include:

```text
task id
profile
Leader-declared Agents
advisory candidate facts considered by the Leader, if meaningful
assignment validation confidence
expected evidence
actual evidence
verification result
review result
approval outcomes
blockers and failure reasons
what should change next time
```

Feedback may update future capability profiles, context-pack generation,
automation policy defaults, adapter warnings, docs, schemas, or audit checks,
but future tasks must still run a fresh capability, provider, context, and
permission scan. Historical success is a useful prior, not proof that an Agent
can do the current task and never authority for VALP to assign that Agent.

Feedback should be stored in a task-local evidence file and optionally copied to
a workspace-level routing memory:

```text
<workspace>/.herdr-loop/tasks/<task-id>/routing-feedback.json
<workspace>/.herdr-loop/routing-feedback.jsonl
```

The workspace-level file is an index, not an independent source of truth. A
reference implementation must not change routing scores from an index entry
unless the corresponding task-local `routing-feedback.json` exists and matches
the indexed task identity. Positive `done` feedback additionally requires the
task state and dispatch, expected-evidence, verification, review, and approval
gates to be resolved, plus existing task-local `actual_evidence` refs. Unbacked,
malformed, stale-copy, or path-unsafe index entries must be ignored.

Do not store secrets, raw private data, or full hidden conversations in routing
feedback. Record evidence paths and short summaries instead.

Non-trivial tasks should also write a learning feedback record when the runtime
supports it:

```text
<task>/learning-feedback.json
```

Learning feedback records the compound-engineering part of the loop:

```text
learning item kind
observation
evidence refs
confidence
next effect
proposed update target layer
proposal
disposition
approval requirement
```

Allowed target layers are:

```text
protocol
schema
audit
docs
local_overlay
skill
runtime_adapter
memory
none
```

Learning feedback does not directly patch the target layer. It records a
proposal and its disposition. Updates to protocol, schemas, audit gates, local
overlays, skills, memory, runtime adapters, or agent configuration must follow
the relevant approval, review, and change-control path. This keeps compound
learning inspectable instead of turning old memory into hidden authority.
During a delegated task, learning feedback cannot authorize or perform live
self-modification. Any accepted live skill, plugin, memory, MCP, or agent
configuration change must become a separately scoped task with prior explicit
user approval and fresh delegation evidence.

For routing, learning feedback becomes an evidence-based prior. A current scan
still wins over old feedback:

```text
old success + missing current tool -> do not route
old success + current approval risk -> stop for approval
old failure + current repaired capability -> route only with lower confidence
old context gap + similar new task -> include the missing context in context-pack
```

## 16.1 Agent Recommendation Resolution

VALP loops are evidence-driven, not fixed-count. A small task may need one
dispatch round. A larger or higher-risk task may need repeated dispatch,
review, fix, review, and recommendation-resolution rounds until the evidence
gates pass or the task is blocked. The coordinator should set a task-local
iteration budget so the loop improves quality without expanding the task
indefinitely.

The coordinator or leader agent must not silently ignore meaningful suggestions
from dispatched agents. Every meaningful recommendation must be adopted into the
visible decision process, but the coordinator controls how far it is executed in
the current task. When a Leader-declared Agent produces next steps, follow-up
risks, implementation suggestions, review suggestions, or explicit "no further
action" guidance, the task should write:

```text
<task>/agent-recommendations.json
```

The record should include:

```text
task id
agent
source evidence ref
recommendation or no-action statement
coordinator adoption decision
rationale
scope boundary
complexity impact
follow-up dispatch or evidence refs, when accepted or merged
deferred owner or escalation ref, when deferred or escalated
```

Allowed coordinator adoption decisions are:

```text
accepted
merged
scoped_followup
bounded_no_action
escalated
```

`accepted` or `merged` recommendations that change the current task must create
normal evidence: a new dispatch, correction-cycle entry, verification record,
review record, approval request, or final synthesis entry. `scoped_followup`
means the recommendation is valid but belongs outside the current task boundary;
it must name the follow-up owner or record why it is intentionally parked.
`bounded_no_action` is allowed only for duplicate, already-satisfied,
non-actionable, or complexity-increasing recommendations; it must state the
reason and cite the evidence that made no action acceptable. High-risk
recommendations still require approval before execution.

The coordinator should also record a complexity policy, normally:

```text
max_recommendation_rounds
max_new_dispatches_without_user_approval
current_scope
stop_conditions
```

If applying a recommendation would materially broaden scope, increase risk, or
start another project, the coordinator should stop, defer it to a follow-up, or
ask for user approval rather than keep looping.

For non-trivial routed tasks, Done Criteria require either a resolved
`agent-recommendations.json` record or an explicit `not_required` record that
explains why no Leader-declared Agent produced meaningful follow-up
recommendations.
This keeps the loop from collapsing into "leader dispatches once, then ignores
everyone and finishes alone."

## 17. Schema And Protocol Versioning

The protocol, blueprint/RFC, Reference System, schema, and Adapter ABI versions
are related but evolve independently.
The protocol version and JSON schema versions are related but independent.

Protocol version describes the human-readable VALP contract: lifecycle,
receipts, adapter duties, evidence gates, approval gates, and Done Criteria.
Schema version describes one machine-readable artifact shape, such as routing,
state, receipts, or visible attention evidence.

The Protocol `0.3` compatibility target is:

| Surface | Accepted line | Compatibility rule |
|---|---|---|
| Blueprint | `Blueprint-0001/1.x` | `1.0` defines Protocol `0.3`; a Blueprint is a frozen design-source identifier, distinct from the public RFC series; editorial clarifications may increment the Blueprint minor version without changing protocol semantics |
| Protocol | `>=0.3.0,<0.4.0` | Reference System `0.3.x` reads and writes this line |
| Legacy protocol input | `0.2.0-draft` | Reference System `0.3.x` may read it only through a declared compatibility and migration path; it never writes new `0.2` task state |
| State schema | read `valp-visible-loop-state.v1`, `.v2`, and `.v3`; write `.v3` | migration creates a new projection and preserves original bytes |
| Receipt schema | read legacy, `valp-dispatch-receipt.v2`, and `.v3`; write `.v3` | new Attempt and proof semantics require v3 or digest-bound reconciliation evidence |
| New core schemas | `v1` | Task, State, Work Item, Attempt, Event, Result, Replay Entry, Checkpoint Root, Proof, Evidence Descriptor, Dimension Policy Evidence, Dimension Gate Result, Dependency Edge, Manual Attestation, Cancellation Event, and Claim Result begin at v1 |
| Reference System | `>=0.3.0,<0.4.0` | writes Protocol `0.3` artifacts only |
| Adapter ABI | `>=1.0,<2.0` | minor additions are capability-negotiated; a major mismatch blocks |

Every System and Adapter handshake MUST publish exact `protocol_read`,
`protocol_write`, `schema_read`, `schema_write`, and `adapter_abi` ranges. A
writer emits one exact schema version and MUST NOT rewrite an append-only ledger
in place.

Unknown optional fields MAY be preserved by a compatible reader. An unknown
required field, closed enum value, authority rule, proof requirement, receipt
meaning, or Done condition is safety-relevant and MUST fail with
`VALP-E-MIGRATION-UNSUPPORTED`. A readable legacy receipt cannot satisfy a new
Attempt, proof-kind, cancellation, or partial-result gate merely because it can
be parsed; it requires content-digest-bound reconciliation that preserves the
original receipt.
Rules:

- A schema version may remain `v1` while the protocol draft moves from
  `0.1.0-draft` to `0.2.0-draft`, as long as that artifact shape stays
  backward-compatible.
- Additive fields should be accepted by older readers when possible.
- Readers should preserve or ignore unknown fields instead of failing, unless
  the unknown field changes a safety gate.
- Breaking artifact changes require a new schema version.
- A task folder should record the schema version for each machine-readable
  artifact it writes.

## 18. Evidence Store

Canonical task evidence lives under:

```text
<workspace>/.herdr-loop/tasks/<task-id>/
```

Long-term agent worklogs may live under:

```text
<workspace>/agents/<agent>/worklog.md
```

The workspace path is configurable. A Desktop project folder is a valid local
convention, not a protocol requirement.

The `.herdr-loop` folder name is the reference runtime-compatible default. Other
implementations may use a different internal folder if they export the same
VALP evidence contract.

Evidence may have an explicit validity state:

```text
valid
superseded
invalid
rejected
blocked
```

The reference task-local file is:

```text
<task>/evidence-status.json
```

Only `valid` evidence can satisfy expected evidence gates. A file that exists but
is marked `superseded`, `invalid`, `rejected`, or `blocked` does not count as
completion evidence.

Agents must not make runtime/build/test/lint/UI verification claims without
concrete evidence. Claims such as "build passed", "tests passed", "UI verified",
or equivalent must cite a command log, screenshot, receipt, or evidence path.
When a claim document cannot embed the full command output, its `valid`
`evidence-status.json` entry may record task-local `supporting_refs`. At least
one supporting ref must contain concrete command/result, screenshot, receipt,
or equivalent verification evidence; a self-reference or missing ref does not
support the claim.

## 19. Approval Gates

The following require explicit user approval:

```text
delete
auth
secrets
skill_config
plugin_config
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

No approval is inferred from silence.

Approval is not retroactive. In particular, a delegated principal that writes a
protected live surface before approval has violated the delegation policy; a
later approval or revert cannot restore evidence produced after the earliest
uncertain mutation point.

Task publishing or routing should classify the task goal and explicit
`Approval Risks` section for the high-risk categories above. When a match is
found, task state must record the risk, set `approval_required`, and stop the
approval gate from passing until explicit approval evidence exists.

The approval mechanism is adapter-specific. A pane-controller adapter may use a
visible prompt, operator confirmation, or policy file. A daemon or hosted
adapter may require an approval record or allowlist before starting high-risk
work. Manual Mode may use a human-written attestation. In all cases, the task
evidence must record what was requested, who or what approved it, when it was
approved, and which scope the approval covered.

## 20. Done Criteria

A task is done only when:

- the user-selected Leader and selection evidence are recorded;
- profile, Leader-declared assignments, and VALP validation are recorded;
- automation policy is recorded when automation or a runtime adapter is used;
- runtime adapter and task state mapping are recorded;
- local overlay inputs are recorded when used;
- declared Agents and context policies are recorded;
- provider matrix fields needed for the task are recorded;
- dynamic model probes, computed freshness, session identity, history binding,
  and high-risk role eligibility are recorded when model-aware routing is
  required;
- runtime preflight is recorded for Full Mode adapters and has no failing
  Leader-declared Agent checks;
- assignment confidence, missing capabilities, validation blockers, and
  high-relevance alternatives are recorded when they affect the decision;
- context pack is recorded for non-trivial routed tasks and uses visible refs;
- skill recommendation backend result is recorded when a backend is available,
  and relevant recommendations are surfaced in dispatch prompts;
- squad routing evidence is recorded when a squad is used;
- submission dependencies are recorded and every dependent delivery follows
  its prerequisite completion in receipt-ledger line order;
- delegation policy is recorded for newly routed delegated tasks and has no
  unresolved protected-write violation;
- dispatch receipts satisfy the required gates;
- expected evidence exists and is not marked invalid, superseded, rejected, or
  blocked;
- correction cycle evidence is recorded and fixed when work was rejected,
  retried, blocked, invalid, or superseded;
- recommendations and next-step suggestions from Leader-declared Agents are
  recorded and resolved for non-trivial routed tasks;
- runtime/build/test/lint/UI claims cite concrete evidence;
- verification passed or has a scoped blocker with concrete verification
  evidence unless verification is explicitly not required;
- review findings have no unresolved critical/high blockers;
- approvals are resolved, including any task-local approval request and user
  decision ledger;
- final synthesis records decisions, disagreements, evidence gaps, and result;
- feedback and learning records are written for non-trivial tasks when the
  runtime supports them;
- strict audit passes for the claimed grade, or a scoped blocker is returned;
- the audited result is returned to the user. Repository hosting, push, pull
  request, and merge are separate user-controlled workflows outside this Done
  claim.

The reference CLI command `valp audit` maps these bullets into executable audit
items. The CLI is not required by the protocol, but it is the reference quality
gate for checking whether a recorded task evidence folder satisfies the Done
Criteria.

## 21. Layered Architecture And Kernel Boundary

This section defines the normative Protocol `0.3` architecture target. It
preserves the authority, receipt, evidence, review, approval, and audit gates in
Sections 1-20 while assigning each behavior to one owning layer. Documentation
of the target does not prove that a schema, reducer, Adapter, runtime, or
platform implements it.

### 21.1 Five layers

VALP has exactly five top-level layers:

```text
00 Human Intent And Authority Boundary
01 Reference System
02 Protocol Kernel
03 Adapter Boundary
04 External Runtime And Ecosystem
```

Layer 00 has separate Intent and Authority lanes. Intent owns the goal,
non-goals, scope, acceptance criteria, declared evidence expectations, and
explicit interruption or redirection. Authority owns explicit Installation
Leader selection, high-risk approvals, privacy and export boundaries, explicit
scope expansion, and governed Leader replacement. The System MAY record and
validate those decisions but MUST NOT take ownership of them. Authority MUST
NOT be inferred from focus, product name, pane label, current directory,
window position, or private reasoning.

The first installation records an explicit user-selected Leader principal,
session binding, and epoch. Daily reopen or recovery SHOULD reuse a still-valid
binding without repeating selection. Convenience MUST NOT bypass identity or
epoch proof. Mandatory approval gates remain bound to the exact action,
identity, digest, and policy version. Bounded authorization leases are deferred
and MUST NOT replace a required approval.

Layer 01, the Reference System, is the second of the five layers, following
Layer 00. It owns effects and operation through five subdomains:

- Control: Doctor, capability passports, intake, Leader lifecycle, scans,
  assignment validation, and visible routing advice.
- Execution: Work Item creation, dependency frontiers, dispatch, bounded retry,
  wait/wake, cancellation, interruption, and redirection.
- State: append-only ledgers, locks, sequence allocation, revision CAS,
  projection, replay, handoff, restart recovery, and idempotency records.
- Experience and observation: CLI, application, and API surfaces; visible
  attention; Evidence Board; status, blockers, and four-dimensional reports.
- Lifecycle: installation, migration, update staging, rollback, compatibility
  negotiation, and preservation of local user state.

The System MAY read files, obtain time, call runtimes, and perform approved side
effects. Every gate-bearing state change MUST be proposed to the Kernel as an
Event plus Evidence and accepted as a Result before the System presents it as
protocol truth.

The Reference System's task-state projection is not the Kernel `State` in
Section 21.2. Its `status` field records operational progress such as intake,
capability/context scans, routing, preflight, suspension, and recommendation
resolution. Those phases describe System work and MUST NOT be substituted for
Kernel truth conditions. The current `valp-task-state.v1` projection has 30
closed operational values; a future projection vocabulary change requires its
own schema version and migration path.

Layer 02, the Protocol Kernel, owns truth conditions. Layer 03 translates
replaceable runtimes into typed observations and proof. Layer 04 contains the
replaceable Agents, models, Providers, tools, skills, repositories, runtimes,
platforms, and operating contexts. HERDR is one reference Adapter and is not
the protocol kernel.

### 21.2 Pure Kernel and canonical Result

The normative Kernel model is:

```text
reduce(State, Event, EvidenceSet) -> Result
```

Equal canonical inputs under the same protocol version MUST produce equal
output. The Kernel MUST NOT read files, obtain wall-clock time, call a runtime,
model, tool, or network, allocate external identity, inspect a UI, or perform a
side effect. Time, identity, runtime status, content digests, and other
observations enter only as typed Events and Evidence.

For the `0.3.0-draft` Kernel machine contracts, canonical JSON bytes are UTF-8
JSON with object keys sorted lexicographically, no insignificant whitespace,
protocol arrays kept in their declared order, set-like Evidence collections
sorted by each Evidence canonical byte representation, non-ASCII characters
emitted as UTF-8 rather than ASCII escapes, non-finite numbers forbidden, and
one trailing LF byte. A digest is `sha256:` plus 64 lowercase hexadecimal
characters over those exact bytes. The canonical empty replay prefix is:

```json
{"entries":[],"schema_version":"valp-kernel-replay-prefix.v1"}
```

including its trailing LF byte, with digest
`sha256:fa1f226ad4960367691ffda3176c5f45a463c102a791799d33dcf2bbfa08b54d`.

The core entities are `Task`, `State`, `WorkItem`, `Attempt`, `Event`,
`Evidence`, `Receipt`, `Claim`, `Result`, `ReplayEntry`, and `CheckpointRoot`.
Installation ID, Leader epoch, Task ID, Work Item ID, Attempt ID, dispatch ID
and generation, suspension epoch, Event ID, and receipt sequence are distinct
identities and MUST NOT substitute for one another.

A Result contains exactly one mutually exclusive variant:

- `accepted`: the next State plus emitted obligations and audit facts;
- `no_op`: unchanged State, bound by ID and digest to the prior accepted Result
  for the same canonical Event and Evidence input;
- `rejected`: unchanged State plus a deterministic closed error code.

Only `accepted` increments the State revision or emits side-effect obligations.
An identical duplicate is `no_op`; a same-identity duplicate with different
canonical content is `rejected` and fails closed.

Kernel Task status is closed to these 13 truth values:

```text
published routing_validation dispatching executing verifying reviewing fixing
approval_required recording done blocked failed cancelled
```

This Kernel vocabulary is intentionally distinct from the Layer 01 operational
projection. In particular, `new`, capability/context scans, adapter selection,
planning, locking, suspension, and recommendation resolution are not Kernel
Task statuses. A System MAY display or persist those phases, but it MUST
propose a typed Event and Evidence to change Kernel State and MUST NOT infer a
Kernel Result from projection text alone.

The following is the complete legal Layer 02 Task transition graph for this
protocol version. Each row is an exact typed Event kind and its only legal
source and target. An Event with a listed kind at any other source status is a
`VALP-E-STATE-CONFLICT` and leaves State unchanged.

| Event kind | Source status | Target status |
| --- | --- | --- |
| `routing_validation_started` | `published` | `routing_validation` |
| `routing_validation_passed` | `routing_validation` | `dispatching` |
| `dispatch_accepted` | `dispatching` | `executing` |
| `work_completed` | `executing` | `verifying` |
| `verification_passed` | `verifying` | `reviewing` |
| `verification_failed` | `verifying` | `fixing` |
| `review_passed` | `reviewing` | `recording` |
| `review_rejected` | `reviewing` | `fixing` |
| `approval_required_raised` | `reviewing` | `approval_required` |
| `fix_dispatch_requested` | `fixing` | `dispatching` |
| `approval_granted` | `approval_required` | `recording` |
| `approval_denied` | `approval_required` | `fixing` |
| `recording_completed` | `recording` | `done` |
| `task_blocked` | `executing`, `verifying`, `reviewing`, or `fixing` | `blocked` |
| `blocked_recovery_to_fixing` | `blocked` | `fixing` |
| `task_failed` | any non-terminal Task status | `failed` |
| `task_cancelled` | any non-terminal Task status | `cancelled` |

`done`, `failed`, and `cancelled` are the closed terminal set and have no
outgoing edges. `blocked` is recoverable only through the explicit typed
`blocked_recovery_to_fixing` Event; no System projection, wake, or
user-interface text may silently rewrite it. A fix re-enters the normal
dispatching, executing, and verifying spine only through their separately
named and validated Events. `approval_required` reaches `recording` only by
the typed `approval_granted` Event; it has no direct path to `done`.

The Kernel State starts only as `published` at revision `0`. For every valid
State, `revision` MUST equal the number of `accepted_events`; revision `0` MUST
have an empty accepted-event ledger and status `published`, while a non-zero
revision MUST have a non-empty accepted-event ledger. The graph does not model
Layer 01 `operationalPhase`, receipt writes, or external effects. Every
accepted edge remains subject to identity binding,
expected-revision CAS, Evidence validation, canonical Result construction, and
idempotency rules in this section.

Work Item status is closed to:

```text
pending eligible submitted running completed partial degraded blocked failed
cancelled skipped
```

Work Item `requirement` is exactly `required`, `optional`, or `soft`. Attempt
status is exactly `created`, `submitted`, `running`, `completed`, `failed`,
`cancelled`, or `fenced`. Claim result is exactly `pass`, `fail`, `unknown`,
`partial`, `degraded`, or `not_applicable`. The Pure Kernel error-code
vocabulary is closed to:

```text
VALP-E-UNKNOWN-ENUM-VALUE
VALP-E-STATE-CONFLICT
VALP-E-IDEMPOTENCY-CONFLICT
```

Unknown closed-vocabulary values fail with `VALP-E-UNKNOWN-ENUM-VALUE`.

#### 21.2.1 Work Item and Attempt Stage 2 slice

The Stage 2 Kernel State additionally carries an ordered, unique Work Item
table. Each Work Item binds its Task ID, Work Item ID, requirement, declared
dependency edges, status, and zero or one current Attempt. A dependency edge
binds the depended-on Work Item ID and its requirement (`required`, `optional`,
or `soft`). An Attempt binds the same Task and Work Item IDs plus a distinct
Attempt ID, dispatch ID, and non-negative dispatch generation. These
identities are never interchangeable.

The complete closed Work Item graph is:

| Event kind | Source status | Target status | Constraint |
| --- | --- | --- | --- |
| `work_item_eligible` | `pending` | `eligible` | dependency frontier is satisfied |
| `attempt_created` | `eligible` or `blocked` | `submitted` | creates a current Attempt; retry from blocked has a new Attempt ID and higher generation |
| `attempt_completed` | `running` | `completed` | current Attempt is completed |
| `attempt_failed` | `submitted` or `running` | `failed` | current Attempt is failed |
| `attempt_cancelled` | `submitted` or `running` | `cancelled` | current Attempt is cancelled |
| `work_item_partial` | `running` | `partial` | does not rewrite current Attempt |
| `work_item_degraded` | `running` | `degraded` | does not rewrite current Attempt |
| `work_item_blocked` | `pending`, `eligible`, `submitted`, or `running` | `blocked` | does not rewrite current Attempt |
| `work_item_failed` | `pending`, `eligible`, `submitted`, `running`, or `blocked` | `failed` | does not rewrite current Attempt |
| `work_item_cancelled` | any non-terminal Work Item status | `cancelled` | does not rewrite current Attempt |
| `work_item_skipped` | `pending` or `eligible` | `skipped` | the Work Item is optional or soft and is not the target of any required dependency edge |

`completed`, `partial`, `degraded`, `failed`, `cancelled`, and `skipped` are
terminal Work Item statuses. `fenced` is a terminal Attempt status with no
outgoing edge. Retrying or redispatching never reuses an Attempt identity or
generation.

The complete closed Attempt graph is:

| Event kind | Source Attempt status | Target Attempt status |
| --- | --- | --- |
| `attempt_created` | no current Attempt, or a blocked Work Item's current Attempt | `created` |
| `attempt_submitted` | `created` | `submitted` |
| `attempt_running` | `submitted` | `running` |
| `attempt_completed` | `running` | `completed` |
| `attempt_failed` | `created`, `submitted`, or `running` | `failed` |
| `attempt_cancelled` | `created`, `submitted`, or `running` | `cancelled` |
| `attempt_fenced` | `created`, `submitted`, or `running` | `fenced` |

`completed`, `failed`, `cancelled`, and `fenced` are the closed terminal
Attempt set. No Attempt event can change a terminal Attempt. Work Item events
do not change a current Attempt; only the exact tuple-bound Attempt events do.

Every attempt-scoped Event MUST carry and exactly match the State Task ID, Work
Item ID, Attempt ID, dispatch ID, and dispatch generation. A current Attempt
is superseded only by accepted retry. An Event bound to a different, stale,
cancelled, fenced, or superseded Attempt/generation is
`VALP-E-STATE-CONFLICT`, leaves State unchanged, and MUST NOT be an idempotent
duplicate. An authorized Work Item or Attempt cancellation fences the exact
current Attempt tuple. Late output may enter as immutable Evidence but cannot
change the Work Item or Attempt truth.

Dependency eligibility is computed by the Kernel from the declared Work Item
table, not asserted by the System. A `required` dependency must be
`completed`; `partial`, `degraded`, `blocked`, `failed`, `cancelled`, and
`skipped` required dependencies prevent eligibility. An unmet `optional`
dependency may permit eligibility. An unmet `soft` dependency may permit
eligibility and records a deterministic audit fact. Unknown, duplicate, or
cross-Task dependency identities fail closed. These rules do not model Layer
01 scheduling, waiting, wake-up, or runtime effects.

#### 21.2.2 Suspension and dependency-ready wake Stage 3 slice

Kernel Task status remains closed to the 13 values in Section 21.2. Waiting is
not a Task status. While a Task is `executing`, the Kernel MAY carry exactly one
current `Suspension` machine record whose status is closed to `waiting` or
`resumed`. The record binds the Task ID, a distinct Suspension ID, a
non-negative suspension epoch, a distinct Wait Policy ID and canonical policy
digest, an ordered non-empty set of required Work Item IDs, and, after wake, the
accepted Wake ID and wake reason. Suspension ID, suspension epoch, Leader
epoch, Wait Policy ID, Wake ID, Event ID, Work Item ID, and Task ID are not
interchangeable.

The complete closed Suspension graph for this slice is:

| Event kind | Source | Target | Constraint |
| --- | --- | --- | --- |
| `suspension_started` | no current Suspension, or current `resumed` Suspension | `waiting` | Task is `executing`; the first epoch is `0`; a later epoch is exactly the prior epoch plus one and uses a new Suspension ID; the Wait Policy and every required Work Item are exactly bound |
| `wake_accepted` | current `waiting` Suspension | `resumed` | exact current Suspension ID, epoch, Wait Policy ID and digest; new Wake ID; wake reason is `dependency_ready`; every bound required Work Item is complete |

The wake-reason vocabulary in this slice is closed to `dependency_ready`.
`suspension_started` MUST bind a non-empty, duplicate-free ordered set of Work
Item identities present in the same Task State. `wake_accepted` MUST carry the
same ordered set. The Kernel computes readiness from the current Work Item
table: every bound Work Item MUST have status `completed`. An Adapter,
projection, receipt, pane, callback, or user-interface label cannot assert
dependency readiness. A missing, unknown, duplicate, cross-Task, incomplete,
partial, degraded, blocked, failed, cancelled, or skipped required Work Item
causes `VALP-E-STATE-CONFLICT` and leaves State unchanged.

Both Events remain subject to installation, Leader epoch, Task identity, and
`expected_revision` CAS. A stale, cross-Task, cross-installation,
cross-Leader-epoch, cross-suspension, cross-suspension-epoch, policy-mismatched,
or already-consumed wake is rejected before it can change truth. An identical
duplicate of an already accepted Event remains the canonical idempotent
`no_op`; reuse of its Event identity with changed canonical content is
`VALP-E-IDEMPOTENCY-CONFLICT`. A new suspension cycle after `resumed` MUST use
the next epoch and a new Suspension identity.

Accepted suspension and wake Events change only the Suspension machine record,
increment State revision, and append normal accepted-event history. They do not
change Task or Work Item status and emit no side-effect obligations. Replay
MUST reconstruct the exact Suspension record and accepted Wake binding with
byte-equal canonical Results and MUST return zero obligations. For backward
compatibility, a State without a Suspension omits the `suspension` member, and
all pre-Stage-3 Task-only canonical bytes and digests remain unchanged.

A `waiting` Suspension blocks ordinary Task progress out of `executing`; the
Task cannot accept `work_completed` until the exact Suspension is `resumed`.
After resume, the first accepted Task transition out of `executing` clears the
current Suspension record. Explicit `task_blocked`, `task_failed`, and
`task_cancelled` Events MAY terminate an outstanding wait and also clear the
current Suspension; their existing authority and fencing rules still apply.
The Kernel MUST NOT produce a State that combines a current Suspension with a
non-`executing` Task status.

#### 21.2.3 Authority-bound cancellation, Interrupt, and Redirect

Cancellation, Interrupt, and Redirect are pure Kernel control Events. Each
MUST bind a `principal` identity, one `authority_evidence_id` present in the
canonical EvidenceSet, and one closed reason: `user_requested`,
`runtime_failed`, `policy_enforced`, or `superseded_by_redirect`. An authority
label, UI action, runtime status, or non-bound Evidence file is insufficient.

Cancellation scope is closed to `task`, `work_item`, and `attempt` and MUST
match the Event kind. `task_cancelled` targets the current Task;
`work_item_cancelled` targets one exact Work Item; `attempt_cancelled` targets
the exact current Work Item, Attempt, dispatch ID, and generation. When a
current Suspension exists, Task cancellation additionally binds its exact
suspension epoch. An accepted cancellation changes Kernel truth immediately,
fences late output, and emits one deterministic `adapter_cancel` obligation for
each submitted or running Attempt in the cancelled scope. A created but not
submitted Attempt needs no Adapter effect. Replay validates the obligations but
returns none. The Reference System reconciles each accepted obligation against
a durable effect record; missing proof remains pending and replay never retries
the Adapter directly.

An effect executor MUST accept only an obligation that reconciliation reports
as pending. Before an external cancellation it MUST require explicit approval,
resolve exactly one adopted Adapter submission whose Task, Work Item, Attempt,
dispatch ID, and generation equal the obligation, and require that Adapter to
advertise `cancel` support. Fulfillment requires an identity-bound terminal
runtime observation and a task-local cancellation proof whose digest is stored
in the Kernel effect ledger. Task-scoped reconciliation MUST re-read the proof
ref and require its current bytes to match that digest; missing, empty, escaped,
or changed proof fails closed. A dry run performs no Adapter operation. An exact
retry of a fulfilled effect returns the prior record and MUST NOT resend
cancellation. Unsupported or ambiguous Adapter routing remains pending or is
recorded blocked with proof; it MUST NOT be relabelled fulfilled.

The optional Kernel `control` record is absent for pre-control states and
therefore preserves their canonical bytes. Once present it binds a non-negative
`intent_version`, status `active` or `interrupted`, and the active Interrupt ID
when interrupted. `interrupt_requested` is accepted only for a non-terminal
Task at the current intent version, records a new Interrupt identity, and
blocks every ordinary progress Event. While interrupted, only
`interrupt_resumed`, `redirect_authorized`, `task_cancelled`, or `task_failed`
may advance State. `interrupt_resumed` MUST bind the exact active Interrupt ID,
current intent version, and fresh authority Evidence; it returns control status
to `active` without satisfying evidence, dependency, review, or approval gates.

`redirect_authorized` MUST bind a new Redirect identity, the exact current
intent version, the next version (`current + 1`), and an ordered duplicate-free
set of known Work Item IDs invalidated by the change. It cancels those
non-terminal Work Items, cancels their current non-terminal Attempts, emits the
same deterministic Adapter cancellation obligations where execution had been
submitted, clears any Suspension and Interrupt, records the new active intent
version, and moves every non-terminal Task to `fixing`. Historical Work Items,
Attempts, Evidence, receipts, and accepted Events remain immutable. A Redirect
cannot relax Done policy or revive cancelled work without fresh Work Items,
Attempts, Evidence, and evaluation under the new intent version.

### 21.3 Attempts, replay, and control changes

Replay consumes an ordered sequence of canonical entries:

```text
ReplayEntry(Event, EvidenceSet, accepted Result)
CheckpointTrustPolicy(trusted Evidence identities)
CheckpointAuthentication(accepted checkpoint Result, EvidenceSet, trust policy)
replay(GenesisRoot | CheckpointRoot, ReplayEntry[], CheckpointAuthentication?) -> Replay
```

Each `ReplayEntry` MUST preserve the exact canonical Event, canonical
EvidenceSet, and accepted Result recorded for one accepted transition. Replay
MUST process entries in accepted ledger order. For every entry it MUST call the
pure `reduce(current State, Event, EvidenceSet)` function and require the
recomputed Result to be `accepted` and its complete canonical representation to
be byte-for-byte equal to the recorded accepted Result. The comparison includes
the next State, identities, revisions, digests, obligations, and audit facts.
Replay then advances to that next State. A missing input, reordered entry,
non-accepted Result, or any canonical mismatch fails closed; replay MUST NOT
trust a recorded Result merely because its digest is internally consistent.

Replay validates recorded obligations but MUST return no obligations and MUST
NOT submit, deliver, or otherwise re-emit them. Effect recovery is a separate
Reference System reconciliation of accepted obligations against durable
receipts. Replay itself MUST NOT call an Agent, LLM, tool, Adapter, runtime, or
effect handler. The returned `Replay` envelope contains the rebuilt State, the
ordered applied Result digests, and an exactly empty obligations collection; it
is not a fourth `Result` variant.

A replay root is legal only when it is exactly one of:

- `GenesisRoot`: the canonical Task State for the applicable protocol version,
  with valid installation, Leader epoch, and Task identities, Task status
  `published`, revision `0`, zero accepted entries, the canonical empty-prefix
  digest, and no prior Event or Result identity;
- `CheckpointRoot`: a canonical, content-addressed record that binds the exact
  State and State digest, protocol version, installation ID, Leader epoch, Task
  ID, revision, accepted-entry count, digest of the exact accepted
  `ReplayEntry` prefix, and the tail Event ID, Result ID, and Result digest. It
  MUST also bind a prior Kernel-accepted checkpoint Result under the declared
  checkpoint trust policy. That accepted Result and its supporting evidence
  MUST be independently verifiable from an immutable ledger before suffix
  replay begins.

A bare State, opaque checkpoint reference, self-asserted digest, or cached
projection is not a `CheckpointRoot`. An implementation without the complete
Checkpoint Root machine contract and verification path MUST accept only a
`GenesisRoot`.

The structural `CheckpointRoot` binds the State and State digest, identity
tuple, revision, accepted-entry count, prefix digest, tail accepted
Event/Result identities and Result digest, a checkpoint Result identity, and a
trust-policy digest. Structural validation is not checkpoint authorization. A
`CheckpointRoot` MUST be accompanied by a `CheckpointAuthentication` supplied
as an input independent from the untrusted root. A `GenesisRoot` MUST NOT be
accompanied by checkpoint authentication.

`CheckpointTrustPolicy` is a canonical, immutable Kernel input containing a
non-empty, unique, canonically ordered set of trusted Evidence identities. Its
canonical digest MUST equal the root `trust_policy_digest`. The System or
Adapter establishes why those Evidence identities are trusted before invoking
the Kernel; the Kernel MUST NOT infer trust from an Agent name, Provider,
runtime, file path, timestamp, or from identities embedded only in the root.
Changing the trusted Evidence set changes the policy digest and requires a new
authentication input.

The authenticated checkpoint Result MUST be the exact accepted Result at the
prefix tail. It MUST be structurally valid and accepted; its canonical next
State MUST equal the root State, its recorded `result_digest` MUST equal the
root `tail_result_digest`, and its Result identity MUST equal both
`checkpoint_result_id` and `tail_result_id`. The tail State history record MUST
bind that same Result identity and digest plus the root `tail_event_id`.

Checkpoint authentication signs a canonical statement with this logical form:

```text
CheckpointStatement(CheckpointRoot, accepted checkpoint Result,
                    trust-policy digest)
```

The statement uses schema version `valp-kernel-checkpoint-statement.v1` and the
same canonical JSON rules as other Kernel entities. Its digest is the Evidence
`content_digest`. The authentication EvidenceSet MUST contain exactly one
structurally valid Evidence record for every trusted Evidence identity, no
missing or additional identity, and every record MUST carry the exact statement
digest. Evidence collections are canonical sets; duplicate identities,
malformed digests, a different statement digest, or a policy digest mismatch
fail closed. This is an identity-bound digest verification contract, not a
signature algorithm, key store, freshness clock, or runtime lookup.

Before applying any suffix entry, replay MUST validate the root State and State
digest; exact protocol version, installation ID, Leader epoch, Task ID,
revision, accepted-entry count, prefix digest, embedded accepted history, and
tail bindings; the accepted checkpoint Result; the independently supplied
trust policy; and the complete authentication EvidenceSet. Missing,
self-asserted, malformed, stale, conflicting, or digest-mismatched
authentication fails deterministically before the first suffix Event is passed
to `reduce`.

For genesis replay, State revision MUST equal the number of accepted entries in
the validated prefix. For checkpoint replay, State revision, accepted-entry
count, prefix digest, embedded history when present, and tail binding MUST all
describe the same exact prefix. Every suffix entry MUST extend that prefix by
exactly one revision and one history record with no gap, duplicate Event or
Result identity, reordering, or identity/version/epoch change. Impossible
combinations, including revision `0` with non-empty accepted history or a
non-zero revision with no authenticated matching prefix, fail closed before any
entry is applied.

After checkpoint authentication succeeds, suffix replay uses the root State as
the current State and applies the same per-entry reducer re-execution and full
canonical Result comparison as Genesis replay. The returned applied Result
digests contain only suffix Results, in order. Accepted obligations recorded in
the checkpoint Result or any suffix Result remain data under validation;
`Replay.obligations` MUST be exactly empty, so replay never repeats a prior
external effect.

#### 21.3.1 Reference System durable Kernel journal and checkpoint recovery

The file-backed Reference System persists one Task's Kernel truth in three
separate canonical artifacts under one task-scoped store: an immutable
`GenesisRoot`, an ordered `ReplayEntry` journal, and at most one authenticated
checkpoint envelope containing a `CheckpointRoot` plus its independently
supplied `CheckpointAuthentication`. These artifacts share one stable
inter-process lock. A checkpoint is an acceleration index over the journal; it
does not replace, truncate, or authorize journal history.

Appending a `ReplayEntry` MUST lock the store, strictly decode the immutable
Genesis Root and complete canonical journal, replay the current prefix through
the public Kernel `replay` function, require the candidate to extend that exact
State by one accepted revision, and require its recorded Result to be
byte-equal to reducer output. An exact existing Event/Result entry is a no-op.
A same Event identity with changed content, stale expected revision, malformed
canonical bytes, identity/epoch mismatch, replay gap, or invalid Result fails
closed without changing bytes. The complete old prefix plus candidate is
written by same-directory temporary file, file flush and sync, atomic replace,
and directory metadata sync where supported. Pre-replace failure preserves the
prior bytes; a reported post-replace failure is `unknown_or_committed` and MUST
be reconciled by strict reread before retry.

Persisting a checkpoint MUST occur under the same lock after strict full
Genesis replay. The envelope's root prefix digest and accepted-entry count MUST
equal the exact journal prefix through the checkpoint State; its tail bindings,
accepted checkpoint Result, trust policy, and authentication EvidenceSet MUST
pass the Section 21.3 checkpoint replay contract before replacement. A
checkpoint over an unknown, future, stale, or non-prefix State is rejected.
The canonical envelope is atomically replaced and never inferred from a bare
State or cached projection.

Recovery MUST first validate Genesis and the complete journal. When no
checkpoint exists it returns full Genesis replay. When a checkpoint exists it
MUST revalidate the envelope against the exact journal prefix, then replay only
the remaining suffix from the authenticated root. The recovered State MUST be
byte-equal to full Genesis replay, and recovery returns zero obligations.
Malformed, noncanonical, symlinked, mixed-task, mixed-installation,
mixed-Leader-epoch, or digest-mismatched store artifacts fail closed and are not
repaired, skipped, truncated, or overwritten by recovery.

Kernel journal/checkpoint persistence and receipt-effect fulfillment are
different boundaries. Accepted v3 receipt append obligations continue to be
reconciled by the Section 21.4.1 `ReceiptStore`: exact durable receipt is
fulfilled/no-op, absence is pending, changed identity/content is conflict, and
post-replace uncertainty requires strict reread. Kernel replay never emits or
repeats those effects.

For an adopted runtime, the Reference System persists the complete declared v2
Work Item dependency graph in the Kernel Genesis Root, not only the first wait
frontier. Each later dependency-ready frontier advances its exact declared Work
Item through eligibility and the submitted/running Attempt lifecycle before a
new Suspension is accepted. The workflow suspension epoch and zero-based Kernel
suspension epoch are bound explicitly. Restart recovery MUST reject an unknown
Work Item, unmet dependency, changed Attempt/dispatch identity, skipped epoch,
or workflow projection that conflicts with recovered Kernel truth.

Any operation that can produce a different external output is a new Attempt
with a new Attempt ID, even when the Work Item and payload are unchanged.

An authorized cancellation fences the exact Task, Work Item, or Attempt
identity and generation. Late output remains immutable evidence but cannot
silently revive the cancelled scope. Runtime cancellation is a System or
Adapter obligation and remains unknown until matching proof exists.

Explicit user interruption and Redirect follow the identity-, authority-, and
intent-bound machine contracts in Section 21.2.3. They never satisfy missing
Evidence or silently rewrite historical work.

### 21.4 Receipt writes and migration

The Protocol receipt-write contract is pure:

```text
propose_receipt_append(ReceiptLedger, ReceiptDraft) -> ReceiptWriteResult
```

It validates canonical input and returns exactly one of `accepted`, `no_op`,
or `rejected`. It does not read a file, allocate time or identity, obtain
runtime proof, or append bytes. An accepted result contains the exact canonical
`valp-dispatch-receipt.v3` record and one append obligation. The Reference
System or Adapter performs the durable locked append and records whether that
obligation was fulfilled.

Each v3 receipt binds `receipt_id`, installation ID, Leader epoch, Task ID,
Work Item ID, Attempt ID, dispatch ID and generation, mode, event sequence,
ledger revision, expected evidence refs, payload digest, proof kind/ref/digest,
prior receipt digest, and canonical receipt digest. Full and Remote receipts
MUST use process- or content-bound proof as required by the event. Manual
events MUST use `manual_attested`; `transport_only` can record visibility but
never satisfies submission or completion. Every receipt carries an
`approval_binding` whose status is either `not_required` or `granted`. The
granted form requires the paired approval ref and digest, but their presence
never grants approval by itself; approval cannot be inferred from another
receipt.

Canonical v3 encoding sorts `proof_bindings` by their canonical object encoding;
input array order is not a separate proof meaning or idempotency identity.

The first accepted receipt has event sequence and ledger revision `1` and
binds the canonical empty receipt-ledger digest. Every later accepted receipt
increments both values by exactly one and binds the prior canonical receipt
digest. Two candidates against the same prior revision race by revision CAS;
the first accepted candidate wins and the stale candidate is rejected. An
identical receipt-ID duplicate is `no_op` and does not emit an append
obligation. The same receipt ID with different canonical content is rejected
with `VALP-E-IDEMPOTENCY-CONFLICT`. Invalid identity, sequence, digest, proof,
or prior-ledger binding is `VALP-E-STATE-CONFLICT`. Unknown receipt schema,
event, mode, proof kind, required safety field, authority meaning, or proof
meaning is `VALP-E-MIGRATION-UNSUPPORTED`.

Legacy and v2 receipts remain immutable read-only history. Migration MUST
preserve their exact original bytes and source digest and create a separate v3
projection. Each projection carries an explicit `migration_id`. A migrator may
accept explicit reconciliation bindings for the
missing installation, Leader epoch, Task, Work Item, Attempt, dispatch, mode,
payload, and proof identities. It MUST NOT infer them from agent name,
timestamp, line number, file presence, or coordinator intent. Missing,
ambiguous, malformed, or unsupported safety semantics fail closed. Repeating
the same source bytes and bindings is a no-op; reusing one `migration_id`
for different source bytes or bindings is an idempotency conflict.

The bounded v2 submission projection recognizes only a closed Adapter proof
record with a concrete submission ID, literal acknowledgement, exact payload
digest, receipt and dispatch identity tuple, event sequence, dispatch ref, and
expected evidence refs, all equal to both the immutable source and v3 draft.
Aliases, missing or additional proof fields, simulation or dry-run evidence,
generic identifiers, and conflicting bindings are unsupported. v2 terminal
receipts remain read-only until a separately versioned terminal reconciliation
contract exists; this slice does not project them into v3 terminal proof.

#### 21.4.1 Reference System durable receipt append

The Reference System consumes only an `accepted` receipt-write Result whose
single append obligation and canonical v3 receipt digest agree. Persistence is
an effect boundary and MUST NOT be implemented inside the Protocol reducer.

For a file-backed receipt ledger, one durable append transaction MUST:

1. acquire an exclusive inter-process lock on a stable lock file distinct from
   the replaceable ledger file;
2. strictly decode every non-empty ledger line as one canonical v3 receipt and
   validate the complete revision, sequence, identity, and prior-digest chain;
3. replay the accepted draft through `propose_receipt_append` against that exact
   durable prefix and require the recomputed accepted receipt to be byte-equal
   to the obligation receipt;
4. treat an exact existing receipt as `no_op`, reject the same receipt or
   migration identity with changed content, and reject stale revision/CAS input
   without changing ledger bytes;
5. serialize the complete old prefix plus one canonical LF-terminated record to
   a same-directory temporary file, flush and `fsync` that file, atomically
   replace the ledger, and sync directory metadata where the platform supports
   it; and
6. release the lock only after the durability boundary completes or its exact
   failure state is reported.

Directly appending a line is insufficient for the MVP-D crash boundary because
a process may stop after writing only part of a JSON object. Atomic replacement
MUST leave readers with either the complete prior ledger or the complete next
ledger after a process crash on a supported filesystem. Malformed, truncated,
noncanonical, mixed-schema, identity-conflicting, or digest-invalid persisted
input fails closed and MUST NOT be truncated, skipped, repaired, or rewritten
by the append operation.

Failure before atomic replacement MUST preserve prior bytes. Failure reported
after replacement, including directory-sync failure, MUST be classified as an
unknown-or-committed durability outcome rather than a clean rejection; callers
MUST reconcile by strict reread before retrying. Lock timeout and unavailable
locking are explicit failures. An exact retry after a committed append returns
the prior canonical receipt without a second write or revision.

The Reference System MUST expose whether directory metadata sync is supported.
Atomic replacement plus process-crash recovery MUST NOT be described as proven
sudden-power-loss durability on platforms or filesystems where directory sync,
storage barriers, or atomic-replace guarantees are unavailable. MVP-D does not
wire this store into existing v2 runtime writers or execute a migration.

#### 21.4.2 Reference System LangGraph v3 adoption

The Reference System LangGraph Adapter is the first bounded runtime path to
adopt the durable v3 receipt contract. Its authoritative receipt ledger is
`runtime/langgraph/receipts.v3.jsonl`. The Adapter MUST use the Section 21.4.1
ReceiptStore boundary for every `dispatch_submitted`, `dispatch_completed`, and
`dispatch_blocked` write, and its resume, dependency-order, and audit consumers
MUST read that same canonical v3 ledger. Direct JSONL append is forbidden on
this path.

Adoption is declared by the task-local `runtime/langgraph/adoption.json`
marker. Once that marker exists, audit MUST require the authoritative v3 ledger
and MUST NOT fall back to compatibility receipts because the v3 ledger is empty,
missing, or invalid. Historical LangGraph tasks without the marker remain
legacy/v2 read-only compatibility cases and cannot receive new v3 writes.

Adoption is atomic per Adapter and task ledger. A LangGraph task with a non-empty
legacy/v2 `dispatch-receipts.jsonl` and a non-empty authoritative LangGraph v3
ledger is mixed-version state and MUST fail closed. The Adapter MUST NOT append
v3 records to a legacy/v2 ledger, translate old receipts in place, or infer a
migration. HERDR, Queue, Manual Mode, workflow observation/recovery writers, and
their legacy/v2 consumers remain compatibility-only and are not adopted by this
slice.

Before invoking LangGraph, the Adapter MUST load a real initialized Reference
System installation identity and active non-zero Leader epoch. Missing,
malformed, inconsistent, or bootstrap-only installation state fails closed.
The selected work-item record supplies Task, agent, role, Work Item, dispatch
ID, and dispatch generation. The exact canonical LangGraph request supplies the
payload digest. A successful LangGraph run ID becomes the Attempt ID and is
preserved by resume. No missing identity may be replaced by a placeholder,
timestamp, agent name, or newly generated local value.

Submission proof MUST bind the adapter-issued run/thread identities and exact
request payload acknowledgement as separate process-bound and content-bound
records with different refs and digests. The process record binds the exact
provider response; the content record binds the exact canonical request digest,
the provider response digest, and an explicit acknowledgement. Reusing one
locally generated record under both proof kinds is proof relabeling and fails
closed. Terminal proof MUST bind the same Attempt, the terminal run
observation, and the exact expected-evidence content. Proof refs and approval
policy refs MUST name persisted task-local records whose canonical digests match
their bindings. When no approval is required, the receipt still binds the
recorded no-approval policy digest; approval is never inferred from runtime
success.

Sequence and revision allocation MUST be proposed from the strictly loaded v3
prefix and committed through ReceiptStore CAS. An exact receipt retry is a
no-op. A stale proposal or proof/identity conflict fails closed without another
runtime invocation. If ReceiptStore reports `unknown_or_committed` after atomic
replacement, the Adapter MUST strictly reread and reconcile the exact receipt.
An exact committed receipt returns success without another write or LangGraph
submission; an absent or conflicting receipt returns an explicit durability
conflict. Uncertainty alone MUST NOT redispatch work.

Before `POST /runs`, the Adapter MUST durably persist a stable submission intent
for the Task, Work Item, dispatch generation, graph, requested thread, and exact
input. The intent ID MUST be sent to the provider as an idempotency/reconciliation
key. A provider response is persisted back into that same intent before receipt
construction. If the process restarts with an accepted intent, it reuses the
recorded run and Attempt. If it restarts with only a prepared intent, provider
acceptance is unknown: the Adapter MUST stop for explicit reconciliation and
MUST NOT issue a second run. This bounded path prefers an orphaned-but-visible
unknown outcome over double dispatch.

LangGraph resume MUST load the persisted submission record and the same v3
ledger, reuse the original run and Attempt identity, and append only the matching
terminal receipt. Audit and dependency gates MUST validate the canonical v3
chain and exact identity, proof, expected-evidence, ordering, and prior submitted
receipt. Legacy/v2 remains readable only on runtime paths not adopted here.

#### 21.4.3 Reference System HERDR v3 adoption

The packaged HERDR Adapter adopts Adapter ABI 1.0 and the durable canonical v3
receipt ledger at `runtime/herdr/receipts.v3.jsonl`. Adoption is task-local and
is declared by `runtime/herdr/adoption.json`. Once declared, HERDR writers,
observers, recovery, dependency gates, and audit MUST read that authoritative
ledger and MUST NOT fall back to `dispatch-receipts.jsonl`. A non-empty legacy
ledger and a HERDR adoption marker are a mixed-ledger conflict; the Adapter
fails closed rather than merging histories implicitly.

An atomic `herdr agent prompt` acceptance creates one Attempt identity from the
bound terminal, pane, Agent identity, baseline state-change sequence, accepted
state-change sequence, and exact payload digest. HERDR's top-level response ID
remains request correlation and MUST NOT be used as the Attempt identity. The
Adapter writes distinct process-bound and content-bound proof records, then a
canonical `dispatch_submitted` receipt and ABI `accepted` observation. Terminal
observation writes `dispatch_completed` or `dispatch_blocked` using the same
Attempt and an ABI `completed` or `blocked` observation. Completion additionally
binds every expected Evidence ref and its content digest. The terminal observer
MUST bind the same terminal, pane, and Agent identity, the exact submission
state-change sequence, a strictly later terminal state-change sequence, terminal
status, and acknowledgement. Expected Evidence appearing on disk without this
HERDR terminal state observation MUST NOT create a Full Mode terminal receipt.

Pane `send-text` plus Enter remains transport only. It may write a canonical v3
`dispatch_inserted` receipt and ABI observation containing a `transport_only`
segment in Manual-degraded mode, but it MUST NOT write `dispatch_submitted`,
claim Full Mode, or satisfy a delivery/completion gate. Exact retries return the
same committed receipt and observation bytes. A same-identity retry with changed
payload, proof, runtime identity, or expected refs is an idempotency conflict.

#### 21.4.4 Reference System Queue v3 adoption

The reference file-ledger Queue Adapter adopts Adapter ABI 1.0 and the durable
canonical v3 ledger at `runtime/queue/receipts.v3.jsonl`, declared by
`runtime/queue/adoption.json`. Queue acceptance creates a stable Attempt from
the exact Task, Work Item, dispatch generation, queue item, enqueue transaction,
and payload digest. It writes separate process-bound enqueue and content-bound
payload proof, a canonical `dispatch_submitted` receipt, and an ABI `accepted`
observation.

`queued` means only that the queue durably accepted the exact item. It does not
prove that a worker received, started, or completed it. Worker observation MUST
bind a real worker/run identity before it can write terminal process proof, and
`dispatch_completed` additionally requires all expected Evidence refs. A
synthetic worker label, queue file existence, or controller-local status MUST
NOT be used as delivery or completion proof. Unknown post-commit outcomes use
strict reread reconciliation and MUST NOT enqueue a second Attempt merely
because the caller did not receive success.

The reference Queue terminal observer consumes a task-local worker observation
record, never the queue item itself. That record MUST bind the queue ID, enqueue
transaction ID, worker ID, run ID, strictly positive worker observation
sequence, terminal status, and acknowledgement. `completed` additionally binds
the digest of every expected Evidence ref. `blocked` binds a non-empty failure
code and MUST NOT be projected as completion. The observer reuses the accepted
Attempt and dispatch identity; mismatched or replayed worker/run identities fail
closed. Exact retries return the same committed receipt and ABI observation.

Queue worker lifecycle is an append-only, digest-chained state machine at
`runtime/queue/lifecycle.v1.jsonl`. Every entry binds the Task, Work Item,
Attempt, dispatch generation, queue ID, enqueue transaction, prior revision,
prior entry digest, event ID, and resulting state. The reference implementation
serializes claim and cancel against one task-local lock and accepts either
`queued -> claimed` or `queued -> cancelled`, never both. A claim additionally
binds one worker ID, run ID, and claim token. Exact retries return the committed
entry; changed worker/run identity, claim token, authority, reason, or expected
revision is an idempotency or CAS conflict and leaves the ledger unchanged.

Cancellation is two-phase after claim. An authorized request transitions
`claimed -> cancellation_requested` but does not prove that execution stopped.
Only an acknowledgement from the exact claimed worker/run and claim token may
transition `cancellation_requested -> cancelled` and emit ABI `cancelled` proof.
A queued item may be cancelled directly because no worker claim exists. A queue
file mutation, controller-local label, request without worker acknowledgement,
or acknowledgement from another identity MUST NOT fulfill a runtime
cancellation obligation. Terminal completion or blocking MUST cite the exact
accepted claim entry and is rejected from `queued`, `cancelled`, or a conflicting
claim identity. Terminal and cancellation acknowledgement race on the same
revision frontier, so at most one terminal outcome is accepted.

#### 21.4.5 Reference System Manual v3 adoption

Manual Mode adopts Adapter ABI 1.0 and the canonical v3 ledger at
`runtime/manual/receipts.v3.jsonl`, declared by `runtime/manual/adoption.json`.
Every Manual receipt uses Manual mode, a stable Attempt identity, and exactly
`manual_attested` proof. The attestation record binds the named authority and
authority ref plus declaration digest, exact action, statement, subject identity, payload or Evidence
digest, ledger revision, prior receipt digest, timestamp, and validity state.
Delivery uses `manual_delivery_attested`; accepted result Evidence uses
`manual_result_attested`; a failed result uses `manual_blocked`.

Manual observations use ABI status `accepted`, `completed`, or `blocked` but
remain Manual claims. They MUST NOT contain process-bound or content-bound proof,
MUST NOT be relabeled as Full or Remote, and MUST NOT satisfy independent runtime
submission, approval, or review separation. Revocation is append-only. Two
active attestations for the same subject/action/revision with different content
digests fail closed until an authorized adjudication record resolves them.

The reference Manual runtime stores revocation and adjudication decisions in a
separate append-only, hash-chained decision ledger. A decision binds its exact
target receipt, subject key, named authority and task-local authority ref,
authority declaration digest, statement, sequence, prior decision digest,
timestamp, and decision digest. The referenced declaration MUST match the Task
and authority and explicitly allow the exact attestation or decision action.
Revocation makes the target ineffective without editing either the receipt or
its proof. Adjudication names the complete conflicting receipt set and exactly
one selected active receipt. A missing target, changed exact retry, malformed
chain, incomplete conflict set, revoked selection, or multiple decisions for the
same logical decision key fails closed. Consumers MUST consult effective Manual
state before accepting delivery, terminal wake, audit completion, or Done.

Receipt-write rejection leaves Kernel Task State unchanged. MVP-C does not add
`recording -> blocked`: a pure validation/CAS rejection is not an external
storage outage, and an outage cannot durably prove that transition through the
failed ledger. A future recording-recovery contract must separately define
failure Evidence, retry/fencing semantics, and whether work may be re-executed
before a versioned Task-graph change is allowed.

### 21.5 Adapter proof contract

An Adapter declares support for `probe`, `submit`, `observe`, `cancel`,
`resume`, and `prove`. Unsupported operations are explicit capability results.
An Adapter MUST NOT invent protocol State or Done semantics.

Proof kinds are closed and complementary:

| Kind | Proves |
|---|---|
| `process_bound` | causal invocation or terminal observation bound to a process, run, thread, or job identity |
| `content_bound` | exact payload or output digest, identity tuple, sequence, and acknowledgement |
| `manual_attested` | a named human attests exact content and scope under declared authority |
| `transport_only` | text, pane content, notification, or prepared data exists without causal invocation proof |

Full and Remote submission require process-bound invocation plus content-bound
payload acknowledgement. Completion additionally requires process-bound
terminal observation and content-bound expected evidence. Remote Mode uses the
same truth conditions and additionally binds the remote proof issuer, host,
observation sequence, and evidence location. `transport_only` never submits.

Manual Mode uses identity-, authority-, revision-, prior-receipt-, digest-,
statement-, time-, and validity-bound attestations. Revocation is append-only;
conflicting attestations fail closed until authorized adjudication. Manual
attestation is never relabeled as Full or Remote runtime proof and cannot
satisfy an independent approval or review role when role separation is
required.

Composite Adapters append provenance for every segment. Each record binds
input and output identity, Adapter identity and ABI, proof kind, evidence refs,
observation sequence, and failure or acknowledgement. A weak or missing
segment limits the final claim; strong downstream proof MUST NOT hide weak
upstream transport.

#### 21.5.1 Adapter ABI 1.x machine contract

The Adapter ABI version line is `1.x`; this slice implements exact version
`1.0`. An Adapter manifest binds a non-empty Adapter ID, Adapter class, exact
ABI version, and exactly one capability result for each closed operation:
`probe`, `submit`, `observe`, `cancel`, `resume`, and `prove`. Capability status
is closed to `supported` or `unsupported`. Unsupported operations require a
non-empty reason and MUST return an explicit unsupported observation; they are
never inferred from a missing method, exception text, or runtime name.

Every Adapter request binds a unique request ID, operation, installation ID,
Leader epoch, Task ID, Work Item ID, Attempt ID, dispatch ID and generation,
canonical payload digest, and ordered expected-evidence refs. Every observation
binds that exact request identity tuple, a non-negative observation sequence,
closed status, runtime identity when one exists, and zero or more provenance
segments. Observation status is closed to `accepted`, `waiting`, `completed`,
`blocked`, `cancelled`, or `unsupported`. An observation never directly changes
Kernel State.

Each provenance segment binds a unique segment ID, contiguous non-negative
segment sequence, Adapter ID and ABI version, exact input identity and digest,
exact output identity and digest, one closed proof kind, ordered safe evidence
refs, acknowledgement, and optional failure code. Adjacent segments MUST bind
the prior output identity and digest as the next input identity and digest.
Sequence gaps, duplicate identities, unsupported ABI versions, missing refs,
digest mismatch, or conflicting acknowledgement/failure semantics fail closed.

A Composite proof policy declares mode and required proof kinds. Full and Remote
submission require both `process_bound` and `content_bound`, every segment
acknowledged, and no `transport_only` segment. Completion uses the same pair and
requires the observation's complete expected-evidence set. Remote additionally
requires a declared remote issuer/host binding in its policy Evidence. Manual
requires `manual_attested` and rejects relabeling as Full or Remote. A
`transport_only` segment always limits the result to transport evidence even if
later segments are stronger. Proof assessment returns `pass` or a closed list
of missing/conflicting conditions; it does not invent receipts, Kernel Events,
or Done.

### 21.6 Dependencies, dimensions, and Done

Dependency kinds are closed to `hard`, `soft`, and `optional`. A hard failure
blocks the dependent Work Item, a soft failure permits a declared degraded
path, and an optional failure may be recorded and skipped when the frozen Done
policy permits it.

The top-level Task completion Claim MUST be `pass` before Task state becomes
`done`. A top-level `partial`, `degraded`, `fail`, or `unknown` result never
means Done. A required Work Item that is failed, blocked, cancelled, skipped,
partial, degraded, or unknown blocks Done. Optional skips and degraded soft
objectives may remain only when predeclared by the frozen Done policy and made
visible in final synthesis.

A Done policy that permits a degraded soft Work Item MUST bind that exact
`work_item_id` to one declared dimension and at least one supported floor rule.
Only `requirement: soft` Work Items may appear. Missing, duplicate, unknown,
required, or optional Work Item bindings; absent dimensions; empty rule sets;
unknown metrics; and unsupported operators fail Task validation. Relaxing Done
policy, optionality, dependencies, or floors after failure requires an
authorized Redirect, a new intent version, supersession or cancellation of
invalidated work, and fresh evaluation.

Quality, Experience, Cost, and Stability are independent. Quality is always a
required dimension. A policy source is a System input, not a Kernel dependency:

```text
System reads authorized policy source
  -> parses and canonicalizes the complete policy payload
  -> materializes immutable Task-, intent-, dimension-, and digest-bound Evidence
Kernel validates policy, scope, applicability, operators, and input Evidence
  -> computes the Dimension Gate Result deterministically
```

A System- or Adapter-authored `pass` is untrusted. Missing policy payload,
digest or scope mismatch, unsupported policy operator or version, missing rule
input, and invalid applicability fail closed. No aggregate score replaces any
required dimension. Faithful source canonicalization is a System audit
obligation; an external auditor may independently canonicalize the authorized
source and compare its canonical payload and policy digest. No raw
`source_digest` field is defined until byte-level source identity semantics are
specified.

### 21.7 Evidence, delivery, and traceability

Evidence descriptors bind content digest, safe ref, provenance, observed time,
freshness policy, confidence, scope, fault class, review status,
supersession, and conflict. Fault class is closed to `none`, `transient`,
`capability`, `permission`, `configuration`, `protocol`, and `unknown`.
Historical evidence remains immutable. Routing and learning may use accepted
evidence as a prior but MUST still require fresh task observation.

An Adapter that derives `dispatch_completed` from expected evidence MUST record
the content digest or absence of every expected ref before submission. It may
complete only when each required ref is newly created or has a different
post-submission digest, and its completion proof MUST cite that submission.
Pre-existing evidence, including evidence from an earlier attempt or
generation, is not completion proof for a newly submitted Work Item.

Reference System budgets independently cover context and payload, iteration,
cost, dispatch and verification latency, deadline, ledger growth and replay,
lock contention, durable append, and cache validity. Cache MUST NOT bypass
authority, approval, or evidence gates.

Delivery proceeds in bounded stages: canonical entities and pure reducer first;
asynchronous recovery and Composite Adapters second; experience, interruption,
and evidence-quality learning third; cross-platform and organizational parity
only after matching conformance and runtime evidence. Every implementation
change MUST preserve a visible path from RFC section to SPEC section, schema or
closed enum, reducer behavior or Adapter obligation, positive fixture, negative
fixture, and reliability evidence where applicable.

The public decision traceability and implementation boundary are documented in
[RFC 0002](docs/rfcs/0002-layered-architecture.md). Until the corresponding
schemas, reducer, migrations, Adapter conformance, negative tests, independent
review, and strict audit pass, these Protocol `0.3` semantics remain a
normative documentation target rather than a runtime-support claim.
