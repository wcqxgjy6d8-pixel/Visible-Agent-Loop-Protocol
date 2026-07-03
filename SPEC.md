# Visible Agent Loop Protocol Specification

Version: 0.1.0-draft

## 1. Purpose

Visible Agent Loop defines a visible, auditable, evidence-backed workflow for
multi-agent collaboration.

The protocol is generic. It can be used for software engineering, research,
frontend work, Apple apps, documents, operations, prototypes, and future task
profiles.

## 2. Terms

`agent`
: A local or remote AI worker with a role, tool access, context limits, and
permission boundaries.

`runtime`
: A system that can list agents, inspect status, send visible dispatches,
submit panes/messages, wait for state, and read output.

`runtime adapter`
: The compatibility layer that maps a concrete runtime, such as a pane
controller, daemon queue, hosted agent platform, or manual workflow, into VALP
receipts and evidence.

`task`
: A user-published unit of work with a task id and evidence folder.

`execution task`
: A runtime-owned unit of agent work, such as a queue item, issue-triggered run,
chat-triggered run, scheduled run, or pane-submitted prompt. An execution task is
not the same as a VALP task; it must be mapped into VALP evidence.

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
: A named group of agents and optional humans, usually with a leader agent that
routes work to members. Squad routing is optional and must remain visible.

`dispatch`
: A visible assignment sent to an agent.

`receipt`
: A machine-readable record of dispatch state.

`evidence`
: Files, logs, screenshots, command outputs, reviews, findings, and synthesis
used to prove progress or completion.

## 3. Lifecycle

```text
INTAKE
  -> PUBLISH
  -> SCAN CAPABILITIES
  -> SCAN CONTEXT POLICIES
  -> LOAD LOCAL OVERLAY
  -> SELECT RUNTIME ADAPTER
  -> CLASSIFY TASK
  -> SELECT PROFILE
  -> DECOMPOSE EXECUTION TASKS
  -> RECOMMEND SKILLS, IF BACKEND EXISTS
  -> BUILD PROVIDER MATRIX
  -> SCORE AND ROUTE AGENTS
  -> ROUTE SQUAD, IF USED
  -> WRITE VISIBLE DISPATCH
  -> SUBMIT DISPATCH
  -> MAP RUNTIME TASK STATES
  -> ANALYZE
  -> EXECUTE / RESEARCH / PROTOTYPE
  -> VERIFY
  -> REVIEW
  -> FIX
  -> REVIEW AGAIN
  -> APPROVAL GATE, IF NEEDED
  -> RECORD
  -> DONE / BLOCKED / FAILED / CANCELLED
```

## 4. State Machine

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
  -> fixing
  -> approval_required
  -> recording
  -> done | blocked | failed | cancelled
```

### 4.1 Execution Task State Mapping

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
dispatch submission proof
runtime task state mapping
expected evidence refs
receipt ledger
failure reason
approval gate status
```

An adapter may use its own internal state names, but it must publish the mapping
to VALP receipt states.

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
- A local overlay cannot bypass approval gates.
- A local overlay cannot turn a capability profile into a fixed assignment.
- A local overlay cannot suppress context compression thresholds unless stricter
  policy replaces them.

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
decompose the task into execution tasks
identify required capabilities and evidence gates
load local overlay profiles, if present
scan current runtime/tool/skill/context state
rank candidate agents for each execution task
record confidence and risk for selected agents
record missing capability or uncertainty
route discovery/review before implementation when confidence is low
```

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
- If a prior feedback record says an agent failed this task type recently,
  require fresh evidence before routing similar work again.

## 9. Runtime Modes

Full Mode requires HERDR or a VALP-compatible runtime. Manual Mode is allowed
only as a degraded workflow.

Full Mode must support:

```text
agent list
agent status
agent read
agent send/insert
pane/message submit
submission proof
wait for status
task evidence writing
dispatch receipt ledger
```

Daemon or platform runtimes may satisfy Full Mode when their adapter exports
equivalent submission proof, state transitions, output references, and evidence
locations. Queue completion alone is not enough.

Manual Mode may write task folders and evidence files, but it cannot claim
automatic dispatch proof.

## 10. Dispatch Receipts

Valid receipt states:

```text
dispatch_written
dispatch_inserted
dispatch_submitted
dispatch_completed
dispatch_blocked
```

Rules:

- `dispatch_written` means the dispatch file exists and was surfaced.
- `dispatch_inserted` means text entered an input box. It is not delivery.
- `dispatch_submitted` means submission was attempted and proven by runtime
state or equivalent proof.
- `dispatch_completed` means expected evidence exists.
- `dispatch_blocked` means submission or completion could not be proven.

If expected evidence is declared, gates require `dispatch_completed`.

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
known_limitations
last_verified_at
```

The provider matrix is evidence, not marketing. It must be generated from
current runtime status, installed tools, official documentation, or explicit
operator configuration. Missing values must be recorded as unknown instead of
guessed.

## 13. Skill Recommendation

Skill recommendation is abstract. The protocol does not require a specific
router implementation.

The useful extracted pattern is:

```text
understand request
  -> decompose into execution tasks
  -> rank installed skills against each task
  -> surface missing useful skills
  -> record recommendation evidence
```

Recommendation output is evidence, not authority. It cannot bypass role
boundaries, approval gates, receipt gates, or context gates.

## 14. Squad Routing

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

## 15. Feedback And Learning

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

## 16. Evidence Store

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

## 17. Approval Gates

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

## 18. Done Criteria

A task is done only when:

- profile and routing are recorded;
- runtime adapter and task state mapping are recorded;
- local overlay inputs are recorded when used;
- selected agents and context policies are recorded;
- provider matrix fields needed for the task are recorded;
- routing confidence, missing capabilities, and rejected high-relevance
  candidates are recorded when they affect the decision;
- squad routing evidence is recorded when a squad is used;
- dispatch receipts satisfy the required gates;
- expected evidence exists;
- verification passed or has a scoped blocker;
- review findings have no unresolved critical/high blockers;
- approvals are resolved;
- final synthesis records decisions, disagreements, evidence gaps, and result.
- feedback record is written for non-trivial tasks when the runtime supports it.

The reference CLI command `valp audit` maps these bullets into executable audit
items. The CLI is not required by the protocol, but it is the reference quality
gate for checking whether a recorded task evidence folder satisfies the Done
Criteria.
