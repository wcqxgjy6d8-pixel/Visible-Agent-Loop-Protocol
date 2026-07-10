# Visible Agent Loop Protocol Specification

Version: 0.2.0-draft

## 1. Purpose

Visible Agent Loop defines a visible, auditable, evidence-backed control
protocol for autonomous and multi-agent work.

The protocol starts from a first-principles question: when an intelligent
system claims a task is done, what evidence makes that claim trustworthy?
VALP answers by making intent, routing, execution, evidence, correction,
approval, and learning visible.

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

`coordinator`
: The selected agent or human responsible for visible state, receipts, gates,
handoffs, and final synthesis for one task. The open protocol does not name a
universal coordinator; the runtime or local overlay must select one from current
capability evidence.

`routing confidence`
: A recorded estimate of how strong the routing evidence is for each selected
agent and each rejected candidate. It helps decide whether to proceed, ask for
review, use a squad, or route a discovery task first.

`feedback record`
: A post-task record of what actually happened: selected agents, evidence
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
: The constraint that a dispatch is a concise, role-specific assignment with
task-local references for long context, not a pasted transcript or a full
context dump.

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
the trigger reason, routing, skill recommendations, dispatches, evidence gates,
and final report path. It is automatic entry, not silent execution.

## 3. Lifecycle

VALP defines a small set of phases, with finer sub-steps inside each phase. A
runtime may persist the phase, the sub-state, or both, but it must always export
enough evidence to satisfy the Done Criteria.

High-level phases:

```text
INTAKE
  -> SCAN
  -> ROUTE
  -> DISPATCH
  -> EXECUTE
  -> REVIEW
  -> RECORD
  -> DONE / BLOCKED / FAILED / CANCELLED
```

Expanded sub-steps:

```text
INTAKE
  -> PUBLISH
  -> SELECT AUTOMATION POLICY
SCAN
  -> SCAN CAPABILITIES, CONTEXT POLICIES, AND LOCAL OVERLAY
  -> SELECT RUNTIME ADAPTER
ROUTE
  -> CLASSIFY TASK
  -> SELECT PROFILE
  -> DECOMPOSE INTO RUNTIME WORK ITEMS
  -> RECOMMEND SKILLS, IF BACKEND EXISTS
  -> BUILD PROVIDER MATRIX
  -> PREFLIGHT RUNTIME AND AGENT SESSIONS
  -> SCORE AND ROUTE AGENTS
  -> ROUTE SQUAD, IF USED
  -> BUILD CONTEXT PACK
DISPATCH
  -> WRITE VISIBLE DISPATCH
  -> SUBMIT DISPATCH
  -> MAP RUNTIME TASK STATES
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
  -> DONE / BLOCKED / FAILED / CANCELLED
```

## 4. State Machine

The state machine below is the full reference vocabulary. Implementations may
collapse adjacent sub-states into phase-level records if they preserve the
required evidence and state mapping. For example, a small CLI may record
`dispatching` while separately writing routing, provider, preflight, and visible
attention evidence.

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
  -> recommending_skills
  -> building_provider_matrix
  -> preflighting_runtime
  -> scoring_routes
  -> routing_capabilities
  -> routing_squad
  -> building_context_pack
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
  -> writing_learning_feedback
  -> done | blocked | failed | cancelled
```

### 4.1 Runtime Work Item State Mapping

Runtimes may expose their own queue state machine. A VALP adapter may map states
such as:

```text
queued
  -> dispatched
  -> running
  -> completed | failed | cancelled
```

These runtime states are useful, but they are not sufficient by themselves.
VALP completion still requires receipt and evidence gates:

| Runtime state | VALP meaning |
|---|---|
| `queued` | Runtime accepted work, but delivery is not proven yet |
| `dispatched` | Runtime claims work was claimed or sent; maps to `dispatch_submitted` only when submission proof exists |
| `running` | Agent execution is active; maps to VALP `executing` |
| `completed` | Execution ended; maps to `dispatch_completed` only when expected evidence exists |
| `failed` | Execution failed; record failure reason and evidence gap |
| `cancelled` | User, runtime, or policy cancelled the execution |

If a runtime marks work `completed` without expected evidence, VALP must record
the adapter state but keep the evidence gate open.

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

Allowed selected actions:

```text
no_valp
publish_only
publish_and_route
publish_route_and_dispatch
continue_until_gate
block_for_approval
```

Auto Visible Mode may automatically continue through low-risk publish, scan,
route, skill recommendation, preflight, visible dispatch, verification, review,
and report generation when the configured runtime can prove each step. It must
stop at `block_for_approval` before high-risk work.

High-risk trigger signals include destructive changes, release or upload,
auth/secrets, memory or agent configuration, migrations, signing, privacy, and
private-data export. A local overlay or runtime may add stricter rules, but it
must not remove the protocol approval requirements.

No background watcher is required by the protocol. A runtime that implements a
watcher must export the source event, rule, task id, and approval state into
VALP evidence before dispatching work. A watcher that cannot export this proof
is not an Auto Visible Mode implementation.

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

- low-risk work may continue through publish, scan, route, dispatch, evidence
  collection, verification, review, synthesis, audit, and learning when each
  step writes evidence;
- high-risk work must stop before side effects and record approval evidence;
- runtime completion without expected evidence is a stop condition, not Done;
- missing context, stale memory, failed preflight, unresolved review findings,
  unresolved agent recommendations, or unresolved approvals must stop the loop;
- a local overlay may make automation stricter, but it cannot make approval,
  receipt, evidence, or context gates weaker.

This is the protocol boundary for autonomous work: the system can move quickly
when the control loop is healthy, and it must stop visibly when the loop cannot
prove safety or completion.

### 4.4 First-Install Health Gate

A first install, including an App-managed install, must prove environment health
before real dispatch. The installer or App should run an explicit health gate in
this order:

```text
install check
  -> valp doctor
  -> runtime preflight, when Full Mode is requested
  -> publish/dispatch dry run
  -> visible user decision before any submit or Auto Visible policy
  -> optional live smoke test
```

The first install gate must not assume a fixed checkout path such as a Desktop
folder. It should record the actual install root, CLI path, runtime path, and
doctor/preflight report refs. A symlink or App bundle wrapper is valid only when
`valp doctor` can still identify the protocol checkout and `bin/valp` entrypoint.

The dry run may create a task folder and write routing, dispatch files, visible
attention evidence, and `dispatch_written` receipts. It must not append
`dispatch_submitted` or `dispatch_completed` receipts unless a runtime actually
submitted work and the expected evidence appeared. A new dry-run task is allowed
to fail `valp audit`; that means work has not completed, not that installation
failed.

New installs must default to Manual trigger mode. `policy_auto`, `watcher`, and
real `--submit` behavior are opt-in after the user can inspect doctor,
preflight, dry-run routing, selected agents, expected evidence, and approval
risks.

## 5. Runtime Adapters

VALP is runtime-neutral. A runtime adapter translates concrete platform behavior
into protocol evidence.

Adapter classes:

| Adapter class | Example shape | Full Mode possible? |
|---|---|---|
| pane controller | visible terminal panes and submit proof | yes |
| daemon queue | local daemon claims queued tasks and reports status | yes, if receipts and evidence are exported |
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

No agent is assumed to be fully known from memory. Capability routing combines
several evidence layers:

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

Only command output, receipts, expected evidence, and review records prove that
work is done.

Capability profiles are not assignments. They answer "what is this agent often
good at?" Routing answers "what should this task use now, given current tools,
context, permissions, evidence, and risk?"

## 8. Intelligent Routing

VALP routing should be explainable and adaptive. A routing decision must record
both selected and meaningfully rejected candidates when the choice affects risk
or quality.

Minimum routing decision steps:

```text
decompose the task into runtime work items
identify required capabilities and evidence gates
load local overlay profiles, if present
scan current runtime/tool/skill/context state
run runtime preflight for selected or high-relevance agents
rank candidate agents for each runtime work item
select a coordinator from current capability evidence
write precise, concise dispatch payloads with refs for long context
record confidence and risk for selected agents
record missing capability or uncertainty
route discovery/review before implementation when confidence is low
```

Coordinator or leader selection is a routing output, not a protocol constant.
VALP must not hard-code one vendor, product, local agent name, or runtime
session type as the universal leader. A local overlay may express preferences,
but the final
selection must be justified by current capability, tool, context, permission,
availability, profile, and evidence scans. If a user has a stronger coordinator
agent, the open protocol should let that agent own state and gates.

Whoever is selected as coordinator or leader owns dispatch precision. The
coordinator must break work into short, role-specific assignments and cite
task-local files for detail. It must not shift context-management work onto
workers by pasting the full conversation, full task history, or broad skill
router output into every dispatch.

Coordinator selection patterns:

- Pane-controller runtime: choose a coordinator agent or human from current
  capability evidence, then record that choice and reason in routing evidence.
- Daemon or hosted runtime: the runtime process may act as coordinator if it
  writes visible dispatches, receipts, gates, and final synthesis evidence.
- Manual Mode: a human coordinator may copy dispatches and write attestations,
  but must not label those attestations as Full Mode submission proof.
- Squad routing: a selected squad leader may coordinate sub-agents only when the
  leader decision, member list, and handoffs are visible evidence.

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

Scores are advisory. The routing output must still explain the decision in
plain language and list hard blockers separately.

Low confidence rules:

- If no agent has the needed tools, mark `capabilities_missing` and stop or ask
  for setup.
- If only one agent can act but confidence is low, route a small discovery task
  first.
- If implementation confidence is medium but risk is high, require review before
  mutation.
- If context policy is near the hard threshold, compress before dispatch.
- If pane or CLI preflight fails, repair the runtime before dispatch or route a
  different adapter.
- If a prior feedback record says an agent failed this task type recently,
  require fresh evidence before routing similar work again.

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

Daemon or platform runtimes may satisfy Full Mode when their adapter exports
equivalent submission proof, state transitions, output references, and evidence
locations. Queue completion alone is not enough.

Manual Mode may write task folders and evidence files, but it cannot claim
automatic dispatch proof, runtime-backed status waits, or Full Mode receipt
equivalence.

## 10. Dispatch Receipts

Valid receipt states:

```text
dispatch_written
dispatch_inserted
dispatch_submitted
dispatch_completed
dispatch_blocked
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
- `dispatch_submitted` means submission was attempted and proven by runtime
state or equivalent proof.
- `dispatch_completed` means expected evidence exists.
- `dispatch_blocked` means submission or completion could not be proven.
- `manual_result_attested` means a human coordinator attests that expected
  evidence exists in a Manual Mode task.

If expected evidence is declared, gates require `dispatch_completed`.

For Full Mode and Remote Mode, `dispatch_completed` is not valid by itself. The
receipt ledger must also contain a prior `dispatch_submitted` receipt for that
selected agent with concrete runtime submission proof, such as a runtime
submission id, queue id, hosted run id, pane/session submission proof, or
equivalent adapter proof. A dry-run command, local sub-agent result,
simulation, manually fabricated completion receipt, or copied review file cannot
be upgraded into Full Mode completion.

For a controlling agent that is executing its own assigned work, the runtime
must not paste the controlling agent's dispatch back into its own live context.
Instead, the controlling agent writes compact task-local evidence and the
adapter records a `dispatch_completed` receipt only after the expected evidence
files exist. This preserves receipt semantics without self-prompt pollution, but
it is controller-local evidence unless an adapter also records runtime
submission proof. Controller-local evidence must not be described as HERDR live
agent dispatch.

Receipt ledgers are append-only, so gates must evaluate the latest receipt for
each selected agent, not merely search for any historical success. A later
`dispatch_blocked` supersedes an earlier `dispatch_completed` until a newer
`dispatch_completed` receipt records the recovered evidence. This prevents old
success receipts from hiding a failed retry, a missed pane submission, or an
agent that timed out before producing required evidence.

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

Routing must treat these thresholds as a pre-dispatch gate. If current context
is at or above `hard_compression_pct`, or if the runtime marks
`compression_required`, no new implementation/review/prototype dispatch should
be sent until the agent writes a compression handoff and the task state is
revalidated.

### 11.1 Dispatch Payload Budget

Dispatch generation is a coordinator/leader responsibility. The selected leader
must send each worker a precise, concise assignment and use task-local file refs
for context expansion. This applies to direct routing, squad routing, hosted
runtimes, pane runtimes, queues, and Manual Mode.

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

## 14. Visible Attention Routing

Visible attention routing makes the routing decision inspectable. It borrows the
useful systems idea from attention mechanisms: select the relevant agents,
skills, context, and evidence for this task instead of making every participant
read everything. Unlike a hidden optimizer, VALP must surface the selection and
masking decisions before dispatch.

Visible attention happens after capability/skill scans and before agent
dispatch.

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
`loop_layer`. `attention-map.json` additionally carries `task_id` and attention
heads. `context-selection.json` carries selected and not-selected context.
`context-pack.json` carries the compact context given to workers. `mask-list.json`
carries excluded inputs and reasons. `evidence-board.json` carries claims and
required evidence.

The attention map records:

```text
loop_layer
attention heads such as implementation, ux_review, prototype, state_gate
selected agent or source for each head
score or status for the selection
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

A non-trivial routed task is any task with more than one selected agent, or any
task in a profile that normally needs external evidence, source review,
artifact review, release gates, runtime repair, implementation, verification, or
prototype evidence. Current examples include `software-code`, `apple-app`,
`web-frontend`, `research`, `document-artifact`, `agent-runtime`, `ops-release`,
and `prototype`. A single-agent, no-runtime learning task may skip visible
attention when it records that it is simple Manual Mode.

## 15. Squad Routing

Squads are optional. A squad may be used when the task should be routed by a
leader agent instead of assigned directly to one worker.

Rules:

- squad existence, leader, members, and routing reason must be visible;
- leader dispatch is a dispatch, not hidden reasoning;
- leader output must include either a concrete delegation, `no_action`, or
  escalation;
- member delegation must create its own dispatch receipt;
- leader judgment cannot bypass provider matrix, context policy, approval gates,
  or expected evidence;
- agent-to-agent mentions or delegations must avoid accidental loops.

Squad routing is routing evidence. It is not completion evidence.

## 16. Feedback And Learning

VALP should learn from outcomes without becoming stale memory.

After each non-trivial task, write a feedback record when the runtime supports
it. The record should include:

```text
task id
profile
selected agents
candidate agents considered, if meaningful
routing confidence
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
permission scan. Historical success is a useful prior, not proof that the agent
can do the current task.

Feedback should be stored in a task-local evidence file and optionally copied to
a workspace-level routing memory:

```text
<workspace>/.herdr-loop/tasks/<task-id>/routing-feedback.json
<workspace>/.herdr-loop/routing-feedback.jsonl
```

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
the current task. When a selected agent produces next steps, follow-up risks,
implementation suggestions, review suggestions, or explicit "no further action"
guidance, the task should write:

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
explains why no selected agent produced meaningful follow-up recommendations.
This keeps the loop from collapsing into "leader dispatches once, then ignores
everyone and finishes alone."

## 17. Schema And Protocol Versioning

The protocol version and JSON schema versions are related but independent.

Protocol version describes the human-readable VALP contract: lifecycle,
receipts, adapter duties, evidence gates, approval gates, and Done Criteria.
Schema version describes one machine-readable artifact shape, such as routing,
state, receipts, or visible attention evidence.

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

## 19. Approval Gates

The following require explicit user approval:

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

No approval is inferred from silence.

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

- profile and routing are recorded;
- automation policy is recorded when automation or a runtime adapter is used;
- runtime adapter and task state mapping are recorded;
- local overlay inputs are recorded when used;
- selected agents and context policies are recorded;
- provider matrix fields needed for the task are recorded;
- runtime preflight is recorded for Full Mode adapters and has no failing
  selected agent checks;
- routing confidence, missing capabilities, and rejected high-relevance
  candidates are recorded when they affect the decision;
- context pack is recorded for non-trivial routed tasks and uses visible refs;
- skill recommendation backend result is recorded when a backend is available,
  and relevant recommendations are surfaced in dispatch prompts;
- squad routing evidence is recorded when a squad is used;
- dispatch receipts satisfy the required gates;
- expected evidence exists and is not marked invalid, superseded, rejected, or
  blocked;
- correction cycle evidence is recorded and fixed when work was rejected,
  retried, blocked, invalid, or superseded;
- agent recommendations and next-step suggestions from selected agents are
  recorded and resolved for non-trivial routed tasks;
- runtime/build/test/lint/UI claims cite concrete evidence;
- verification passed or has a scoped blocker with concrete verification
  evidence unless verification is explicitly not required;
- review findings have no unresolved critical/high blockers;
- approvals are resolved, including any task-local approval request and user
  decision ledger;
- final synthesis records decisions, disagreements, evidence gaps, and result.
- feedback and learning records are written for non-trivial tasks when the
  runtime supports them.

The reference CLI command `valp audit` maps these bullets into executable audit
items. The CLI is not required by the protocol, but it is the reference quality
gate for checking whether a recorded task evidence folder satisfies the Done
Criteria.
