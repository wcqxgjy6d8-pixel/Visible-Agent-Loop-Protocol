# Visible Agent Loop Protocol Specification

Version: 0.2.0-draft

## 1. Purpose

Visible Agent Loop defines a visible, auditable, evidence-backed workflow for
multi-agent collaboration.

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

`receipt`
: A machine-readable record of dispatch state.

`evidence`
: Files, logs, screenshots, command outputs, reviews, findings, and synthesis
used to prove progress or completion.

`provider-filtered skill recommendation`
: A skill recommendation result generated for one target agent, using that
agent's reachable provider or skill library filter. Provider-filtered results
are preferred in dispatch prompts because task-level aggregate results can
contain skills owned by other providers.

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
  -> FIX
  -> REVIEW AGAIN
  -> APPROVAL GATE, IF NEEDED
RECORD
  -> RECORD
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
  -> dispatching
  -> planned
  -> locked
  -> executing
  -> verifying
  -> reviewing
  -> fixing
  -> approval_required
  -> recording
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
runtime work item
recommended skill name
installed or missing status
confidence/mode/decision
skill path or install hint
instruction that recommendations are aids, not permission grants
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
<task>/mask-list.json
<task>/evidence-board.json
<task>/visible-routing.md
```

The four JSON artifacts must all carry `schema_version`, `profile`, and
`loop_layer`. `attention-map.json` additionally carries `task_id` and attention
heads. `context-selection.json` carries selected and not-selected context.
`mask-list.json` carries excluded inputs and reasons. `evidence-board.json`
carries claims and required evidence.

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

The evidence board turns claims into evidence requirements before execution.
For example, a UI claim should require a build/test log and a real screenshot,
not just code inspection.

Dispatch prompts should include the recipient's visible attention slice:

```text
loop layer
the recipient's attention head
selected context paths
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

Feedback may update future capability profiles, but future tasks must still run
a fresh capability, provider, context, and permission scan. Historical success
is a useful prior, not proof that the agent can do the current task.

Feedback should be stored in a task-local evidence file and optionally copied to
a workspace-level routing memory:

```text
<workspace>/.herdr-loop/tasks/<task-id>/routing-feedback.json
<workspace>/.herdr-loop/routing-feedback.jsonl
```

Do not store secrets, raw private data, or full hidden conversations in routing
feedback. Record evidence paths and short summaries instead.

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
- runtime adapter and task state mapping are recorded;
- local overlay inputs are recorded when used;
- selected agents and context policies are recorded;
- provider matrix fields needed for the task are recorded;
- runtime preflight is recorded for Full Mode adapters and has no failing
  selected agent checks;
- routing confidence, missing capabilities, and rejected high-relevance
  candidates are recorded when they affect the decision;
- skill recommendation backend result is recorded when a backend is available,
  and relevant recommendations are surfaced in dispatch prompts;
- squad routing evidence is recorded when a squad is used;
- dispatch receipts satisfy the required gates;
- expected evidence exists and is not marked invalid, superseded, rejected, or
  blocked;
- runtime/build/test/lint/UI claims cite concrete evidence;
- verification passed or has a scoped blocker with concrete verification
  evidence unless verification is explicitly not required;
- review findings have no unresolved critical/high blockers;
- approvals are resolved, including any task-local approval request and user
  decision ledger;
- final synthesis records decisions, disagreements, evidence gaps, and result.
- feedback record is written for non-trivial tasks when the runtime supports it.

The reference CLI command `valp audit` maps these bullets into executable audit
items. The CLI is not required by the protocol, but it is the reference quality
gate for checking whether a recorded task evidence folder satisfies the Done
Criteria.
