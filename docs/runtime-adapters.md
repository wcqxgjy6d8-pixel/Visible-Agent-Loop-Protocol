# Runtime Adapters

VALP is a protocol. A runtime adapter is the bridge from a concrete execution
system into VALP receipts and evidence.

The adapter exists so the protocol can work across pane-based tools, daemon
queues, hosted dashboards, remote SSH hosts, and manual workflows without
pretending they provide the same guarantees.

## Adapter Classes

| Adapter class | Shape | Mode |
|---|---|---|
| pane controller | terminal panes, visible input, submit proof | Full Mode when proof is exported |
| daemon queue | local daemon claims queued work and reports lifecycle events | Full Mode when state and evidence are exported |
| hosted/local platform | web board plus local agent workers | Full Mode when audit data is accessible |
| remote SSH | runtime owns state on another host | Remote Mode |
| manual | human copies prompts and results | Manual Mode |

## Full Mode Requirements

A Full Mode adapter must export:

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

The adapter may store this data in a database, JSONL ledger, local task folder,
or platform API. The storage is implementation-specific; the exported evidence
contract is not.

## Pane Controller Adapter

Pane controllers are useful when an agent is visibly running in a terminal or
browser-controlled pane.

Required proof:

```text
dispatch file written
text inserted, if applicable
submit action proven
agent output read
expected evidence found
```

Text inserted into an input box remains only `dispatch_inserted`. It does not
prove delivery.

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

## Adapter Rule

An adapter must never upgrade an internal "completed" state into VALP
completion unless the VALP expected evidence gate is satisfied.
