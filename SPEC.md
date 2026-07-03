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
  -> SELECT RUNTIME ADAPTER
  -> CLASSIFY TASK
  -> SELECT PROFILE
  -> DECOMPOSE EXECUTION TASKS
  -> RECOMMEND SKILLS, IF BACKEND EXISTS
  -> BUILD PROVIDER MATRIX
  -> ROUTE AGENTS
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
  -> selecting_runtime_adapter
  -> classifying_task
  -> selecting_profile
  -> decomposing_tasks
  -> recommending_skills
  -> building_provider_matrix
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

## 6. Capability Evidence

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
```

Only command output, receipts, expected evidence, and review records prove that
work is done.

## 7. Runtime Modes

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

## 8. Dispatch Receipts

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

## 9. Context Compression

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

## 10. Provider Matrix

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

## 11. Skill Recommendation

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

## 12. Squad Routing

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

## 13. Evidence Store

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

## 14. Approval Gates

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

## 15. Done Criteria

A task is done only when:

- profile and routing are recorded;
- runtime adapter and task state mapping are recorded;
- selected agents and context policies are recorded;
- provider matrix fields needed for the task are recorded;
- squad routing evidence is recorded when a squad is used;
- dispatch receipts satisfy the required gates;
- expected evidence exists;
- verification passed or has a scoped blocker;
- review findings have no unresolved critical/high blockers;
- approvals are resolved;
- final synthesis records decisions, disagreements, evidence gaps, and result.
