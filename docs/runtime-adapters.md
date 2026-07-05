# Runtime Adapters

VALP is a protocol. A runtime adapter is the bridge from a concrete execution
system into VALP receipts and evidence.

The adapter exists so the protocol can work across pane-based tools, daemon
queues, hosted dashboards, remote SSH hosts, and manual workflows without
pretending they provide the same guarantees.

HERDR is the current reference adapter target in this repository. It is useful
for proving the Full Mode path, but it is not the VALP protocol itself.

Terminals are display surfaces, not automatically runtime adapters. A terminal
that can open panes still needs an adapter layer that can submit dispatches,
read or collect outputs, and write receipts/evidence.

## Adapter Classes

| Adapter class | Shape | Mode |
|---|---|---|
| pane controller | terminal panes, visible input, submit proof | Full Mode when proof is exported |
| daemon queue | local daemon claims queued work and reports lifecycle events | Full Mode when state and evidence are exported |
| hosted/local platform | web board plus local agent workers | Full Mode when audit data is accessible |
| remote SSH | runtime owns state on another host | Remote Mode |
| manual | human copies prompts and results | Manual Mode |

## Agent Sessions

VALP uses `agent session` as the generic term for the place where an agent
receives work and produces output.

Examples:

| Session type | Runtime shape |
|---|---|
| terminal pane | pane-controller adapter |
| queue job | daemon queue adapter |
| hosted thread/run | hosted platform adapter |
| SSH-hosted pane or queue | remote adapter |
| copied prompt / PR comment | manual adapter |

A terminal pane is only one session type. Non-pane runtimes should export
equivalent job/session identifiers instead of fake pane fields.

## Full Mode Requirements

A Full Mode adapter must export:

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

The adapter may store this data in a database, JSONL ledger, local task folder,
or platform API. The storage is implementation-specific; the exported evidence
contract is not.

## Coordinator Patterns

VALP does not choose a universal leader.

Common patterns:

| Runtime shape | Coordinator pattern |
|---|---|
| pane controller | select a coordinator agent or human from current capability evidence |
| daemon queue | the daemon writes routing, dispatch receipts, gates, and final synthesis |
| hosted platform | the platform task controller writes state and evidence refs |
| manual | a human coordinator writes attestations and synthesis |
| squad | a selected leader writes visible member routing and handoffs |

The selected coordinator must be recorded in routing evidence with the reason
for selection. Local defaults are hints, not protocol semantics.

## Pane Controller Adapter

Pane controllers are useful when an agent is visibly running in a terminal or
browser-controlled pane.

Required proof:

```text
dispatch file written
runtime preflight passed
text inserted, if applicable
submit action proven
agent output read
expected evidence found
```

Text inserted into an input box remains only `dispatch_inserted`. It does not
prove delivery.

Pane controllers should also export pane dimensions when available. A visible
agent can fail at the UI layer when the pane is too small for its TUI. If a
selected agent's pane is below the adapter's minimum size, the adapter must stop
dispatch or record the dispatch as blocked until the pane is repaired.

Pane-specific checks are not required for non-pane adapters.

## Windows Terminal Without HERDR

Windows Terminal can be useful for showing multiple PowerShell or CMD sessions,
but terminal panes alone do not satisfy Full Mode. The missing part is the
control plane: reliable dispatch submission, output collection, receipt
writing, timeout handling, expected evidence checks, and final audit state.

A no-HERDR Windows adapter should prefer a runner/queue shape:

```text
valp task folder
  -> inbox/<agent>.jsonl or task-local queue
  -> valp-agent-runner.ps1 per agent/session
  -> agent CLI or manual operator
  -> evidence files
  -> dispatch-receipts.jsonl
  -> valp audit
```

This can be displayed inside Windows Terminal panes, but the panes are only the
UI. The runner/queue is the adapter. Keystroke automation tools can be useful
for experiments, but they should not be used as Full Mode proof unless they also
export reliable submission proof, output refs, receipts, and evidence gates.

## Daemon Queue Adapter

A daemon queue is a system where a local process polls for work, starts an
agent CLI, streams progress, and reports completion.

The adapter must map runtime queue states into VALP:

| Queue state | VALP mapping |
|---|---|
| queued | accepted by runtime, not delivered |
| dispatched | may map to `dispatch_submitted` only with submission proof |
| running | maps to `executing` |
| completed | maps to `dispatch_completed` only after expected evidence exists |
| failed | maps to `failed` or `blocked` with reason |
| cancelled | maps to `cancelled` |

Queue success is not enough. VALP still requires evidence.

Recommended queue evidence:

```text
queue item id
worker id
provider/backend id
dispatch payload ref
status transition log
output or artifact ref
expected evidence refs
failure reason, if any
approval state, if needed
```

## Hosted Or Local Platform Adapter

Managed agent platforms often have boards, issues, comments, task runs, skills,
and runtime workers. They can be good VALP runtimes when they expose enough
audit information.

Required export:

```text
issue or task id
agent assignment
runtime worker id
provider/backend id
state transitions
comments or output refs
tool logs, if available
evidence refs
failure reason
approval state
```

If the platform cannot export submission proof or expected evidence refs, it is
not a Full Mode adapter.

## Remote Adapter

Remote Mode is valid when the runtime runs on another machine.

The remote runtime owns:

```text
agent state
pane state
queue state
submission proof
receipts
evidence store
```

Local terminal state is not proof of remote delivery.

## Manual Adapter

Manual Mode can record:

```text
dispatch_written
manual_delivery_attested
manual_result_attested
```

Manual attestation is useful for continuity, but it is not Full Mode proof.

Manual adapters should prefer explicit manual labels:

```text
manual_dispatch_written
manual_delivery_attested
manual_result_attested
manual_blocked
```

These labels can satisfy Manual Mode continuity, but they do not prove runtime
delivery.

## Adapter Rule

An adapter must never upgrade an internal "completed" state into VALP
completion unless the VALP expected evidence gate is satisfied.
