# Runtime Adapters

VALP is a protocol. A runtime adapter is the bridge from a concrete execution
system into VALP receipts and evidence.

If you are evaluating a new runtime, start with the
[runtime adapter checklist](adapter-checklist.md), then use this page for the
detailed contract.

The adapter exists so the protocol can work across pane-based tools, daemon
queues, hosted dashboards, remote SSH hosts, and manual workflows without
pretending they provide the same guarantees.

HERDR is the current reference adapter target in this repository. It is useful
for proving the documented Full Mode path, but it is not the VALP protocol
itself. The reference CLI also includes a synthetic `queue` adapter shape for
testing headless evidence without terminal panes and a real local-process draft
adapter for an approved addressable worker.

## Reference Runtime Status

HERDR should be described as the current reference runtime, not as a protocol
dependency and not as a closed-source black box.

Externally checked on 2026-07-06:

- `https://github.com/ogulcancelik/herdr` is a public repository.
- The repository contains source and project files, including Rust sources,
  `Cargo.toml`, tests, docs, website files, and workers.
- Its license text says AGPL-3.0-or-later for open-source use plus a commercial
  license option.

The existence of a public HERDR repository does not remove the adapter gap:
VALP still needs an independently operated hosted or agent-provider adapter
before it can claim broad automated Full Mode interoperability.

Terminals are display surfaces, not automatically runtime adapters. A terminal
that can open panes still needs an adapter layer that can submit dispatches,
read or collect outputs, and write receipts/evidence.

## Adapter Classes

| Adapter class | Shape | Mode |
|---|---|---|
| pane controller | terminal panes, visible input, submit proof | Full Mode when proof is exported |
| daemon queue | local daemon claims queued work and reports lifecycle events | Full Mode when state and evidence are exported |
| local process worker | approved local subprocess with submission, lifecycle, output, and evidence refs | Full Mode for the declared host/profile |
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

## Auto Visible Trigger Adapters

Some runtimes can start VALP from a policy rule, issue label, queue item,
schedule, file event, or platform API. That trigger layer is allowed, but it is
not completion evidence.

An Auto Visible trigger adapter must export:

```text
trigger id or source event
matched rule or policy reference
deduplication key, when a watcher is used
risk classification
selected action
approval requirement and approval ref, when needed
created VALP task id
visible refs for routing, skills, receipts, report, and audit
```

If the trigger selects a high-risk action, the adapter may publish and route the
task, but it must stop before execution and record `block_for_approval`.

Trigger adapters should write:

```text
<task>/trigger-policy.json
<task>/automation-policy.json
```

Watcher support is optional. A runtime that cannot export trigger evidence is
not implementing Auto Visible Mode, even if it starts agents automatically.

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

## Cross-Adapter Suspended-Wait Contract

This contract applies to pane, daemon, hosted, queue, and remote adapters. When
any adapter claims deterministic suspended waiting, it must block outside the
coordinator model and export a versioned wait policy, identity-bound receipts,
a revisioned suspension projection, an append-only accepted event ledger, and
an immutable wake result. Success requires the `dependency_ready` barrier;
blocked work, runtime failure, cancellation, timeout, and user input are
exception short circuits into visible handling, not completion proof.

An adapter bridge may watch expected evidence after proven delivery and emit a
completion receipt only for evidence that was absent at suspension entry. The
receipt must bind the current work item and epoch and cite the originating
submission receipt. This watcher is a local runtime process, not a coordinator
model turn. Runtime status should say that a local wait was used, that
coordinator-model polling was not observed, and which wake reason and receipt
were accepted. Repeated Agent prompts or model-based status polling do not
satisfy this contract. Provider billing is outside this status contract.

For a submission-only call, a zero evidence-wait window means the adapter
returns after concrete delivery proof. It must not emit `dispatch_blocked`
merely because expected evidence is not instantaneous. The phase wait policy
retains the expected refs so the separate local wait bridge can observe them.

The reference core proves one accepted wake transition per suspension epoch,
idempotent wake-result replay, and event-to-projection recovery from a committed
wait event. It does not prove exactly-once coordinator process continuation. An
adapter may make that stronger claim only with a wake-ID-bound continuation
invocation receipt and restart/restore evidence showing duplicate invocation is
suppressed across recovery. Otherwise it must downgrade the continuation
capability claim. An optional `checkpoint_ref` is only an opaque safe, existing,
non-empty task-local ref and is not restorability or invocation evidence.

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
| waiting | maps to `suspended`; runtime waits without coordinator model turns |
| running | maps to `executing` |
| completed | maps to `dispatch_completed` only after expected evidence exists |
| failed | maps to `failed` or `blocked` with reason |
| cancelled | maps to `cancelled` |

Queue success is not enough. VALP still requires evidence.

Daemon adapters use the shared cross-adapter suspended-wait contract above. A
queue wakeup is not completion proof.

The reference file-backed core flushes ledger records and replacement files and,
on POSIX filesystems that support it, synchronizes parent-directory metadata.
Unexpected directory-sync failures propagate instead of being silently ignored.
The current Windows reference path retains atomic replacement and process-crash
event-to-projection recovery, but does not prove sudden-power-loss directory
durability; adapters that need that guarantee must provide and evidence a
platform-specific equivalent.
Reference file-ledger locks use nonblocking acquisition with a 30-second,
contention-only retry deadline on POSIX and Windows. Unexpected lock errors and
deadline exhaustion fail visibly. Advisory-lock behavior on network filesystems
remains an adapter/filesystem capability that must be tested rather than assumed.

Recommended queue evidence:

```text
queue item id
worker id
provider/backend id
dispatch payload ref
status transition log
wait policy, suspension epoch, revision, accepted event, and wake result, if used
output or artifact ref
expected evidence refs
failure reason, if any
approval state, if needed
```

Reference CLI smoke path:

```bash
bin/valp publish TASK-QUEUE --workspace /path/to/workspace --prompt "..." --runtime queue
bin/valp preflight --runtime queue --agent codex --json
bin/valp dispatch TASK-QUEUE --workspace /path/to/workspace --runtime queue
```

The reference queue path writes queue-shaped records only. It does not replace a
real queue worker, and it does not turn `dispatch_submitted` into completion.
Completion still requires `dispatch_completed` receipts and expected evidence.

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

Remote Mode is valid when the runtime runs on another machine and exports the
required evidence contract. Remote guarantees are conditional on adapter
evidence from that host; SSH connectivity or local terminal state is not proof.

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
