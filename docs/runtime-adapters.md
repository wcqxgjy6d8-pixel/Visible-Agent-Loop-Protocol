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

Externally checked on 2026-07-28:

- `https://github.com/ogulcancelik/herdr` is a public repository.
- The repository contains source and project files, including Rust sources,
  `Cargo.toml`, tests, docs, website files, and workers.
- The immutable `v0.7.5` tag and Homebrew stable artifact are
  `AGPL-3.0-or-later` with a commercial license option.
- Upstream `master` was relicensed to `Apache-2.0` by commit `cd5ea1be0e69` on
  2026-07-22, after `v0.7.5`. That change does not retroactively relicense the
  tagged or Homebrew artifact.

The existence of a public HERDR repository does not remove the adapter gap:
VALP still needs an independently operated hosted or agent-provider adapter
before it can claim broad automated Full Mode interoperability.

## Packaged HERDR Submission

The reference CLI packages its HERDR submission bridge in `valp_cli`; it does
not shell out to a repository-external `herdr-loop` helper. Capability probing
selects one of three explicit modes:

| Mode | Required HERDR commands | Result |
|---|---|---|
| `agent_prompt` | `herdr agent get`; `herdr agent prompt` with `--wait --until working --timeout` | Full Mode only after identity-bound `state_change_seq` advancement |
| `pane_send_text_enter` | `herdr pane send-text`, `herdr pane send-keys`, `herdr agent wait` | transport-only `dispatch_inserted`; `Manual-degraded`; never `dispatch_submitted` |
| `unavailable` | neither path | preflight and dispatch fail closed with remediation |

The atomic path first reads a structured Agent baseline, then invokes `herdr
agent prompt <target> <payload> --wait --until working --timeout <ms>`. A valid
`agent_prompted` response repeats the same terminal, name, Agent, and pane
identity and reports an integer `state_change_seq` strictly greater than the
baseline. A generic response `id`, fabricated `submission_id`, observed status,
visible label, counter, or task text is not independent invocation proof.

The compatibility path may perform one bounded Enter retry, but that does not
promote it. Successful insertion, Enter, status observation, or visible output
remains transport evidence and the operation stops as `Manual-degraded`.

The probe reads command help instead of inferring features from a version
string. A successful atomic invocation writes a native identity-bound
`valp-dispatch-receipt.v2` submission receipt. Evidence waiting can append a
completion receipt only after every declared expected ref is nonempty and the
completion cites the exact submission receipt. Text insertion alone is never a
submitted or completed receipt.

A dispatch dry run renders the adapter command only. It does not provision an
owned session, require a live model/session identity, create a runtime blocker,
or consume the one bounded runtime retry. Actual `--submit` still provisions
and rechecks every owned-session and model gate before delivery.

Before a submitted dispatch, the packaged adapter creates a non-focused
task-owned HERDR workspace, uses `herdr agent start` at the project root inside
that workspace, and moves the Agent to an isolated task tab before recording
the binding. It reuses only that exact recorded session. It writes the current
projection to `agent-sessions.json` and appends each provision/reuse decision
to `agent-session-receipts.jsonl`. The binding and receipt include the owned
workspace scope and `focused_at_provisioning: false` from the post-isolation
runtime response. A missing or focused result blocks delivery. A same-label
user pane is never adopted. A missing bound pane
may produce the next generation; a present pane with changed runtime identity
fails closed.

The packaged bridge resolves a bare worker command such as `agent-cli`
through the coordinator's `PATH` before calling `herdr agent start`, then stores
that absolute entrypoint in the binding and receipt. HERDR's daemon process may
have a narrower `PATH`; relying on it would make provisioning environment-
dependent. An unresolved entrypoint blocks before a session is recorded.

The installation-Leader recovery path is deliberately narrower than ordinary
session provisioning. After an explicitly approved failed first start, the
HERDR adapter may address only the exact named session with `pane get`, its
exact workspace with `workspace get`, and the same pane with `pane
process-info`. It must re-prove the deterministic installation workspace and
pane labels, Agent, cwd, non-focus, complete foreground argv, process
generation, and bounded health. It must not call runtime create, start, move,
focus, close, delete, send, or broad list operations. Any identity or launch
mismatch leaves the installation blocked.

Reuse and post-provision preflight query `herdr pane list --workspace` with the
task-owned workspace id from the accepted binding. This avoids an unbounded
global pane scan and still requires the exact pane, terminal, tab, Agent, and
cwd match.

If that exact live pane still matches, its recorded binding and launch argv are
the reuse authority. A later capability scan may suggest a different launcher,
but it cannot mutate the live binding or force a new generation. The adapter
reuses the accepted launch contract unless the operator explicitly requests a
replacement; an explicit replacement remains blocked while the old pane is
present. Metadata conflicts name only the mismatched fields.

An operator-approved launch integration change uses
`valp dispatch --agent <agent> --role <role> --replace-owned-session-launch
--submit`. This is accepted only for one explicitly targeted task-owned
binding after its old pane is absent and before any delivery receipt exists.
The next generation records the new launch argv while preserving all earlier
bindings, receipts, and failed-attempt evidence. A present old pane or an
implicit capability change still fails closed.

Freshly launched workers may publish structured active-model metadata after the
session itself is addressable. The bridge performs a bounded read-only
readiness observation before the dispatch-time model gate. Unstructured pane or
footer text is not model evidence. A timeout may be rechecked once for the same
owned binding; an observed wrong or ineligible model remains fail-closed.

If that readiness recheck exhausts before structured metadata arrives, the same
public dispatch command may reconcile late metadata without spending another
reroute. This narrow path requires the exact task-owned binding and matching
provisioning receipt, a recorded `owned_session_model_readiness_timeout`, and no
`dispatch_submitted` receipt for the work item. It reuses the bound session and
continues only when fresh preflight reports an `observed` model, a `known`
session identity, and eligible role. It never creates a replacement session or
reopens another exhausted runtime failure.

If session provisioning, preflight, or delivery fails before the bridge can
write concrete submission proof, it blocks the iteration budget with `runtime
dispatch failure`. The same public dispatch command may reopen that exact
blocker once for the same dependency-ready work item. Provisioning and
preflight are repeated before delivery. A second failure records `runtime
dispatch retry exhausted` and remains blocked; it is not an automatic retry
loop. Approval, dependency, observed model mismatch, evidence, and other budget
blockers are never reopened by this path. An unknown model from a fresh owned
session is a bounded readiness blocker, not an observed mismatch. Tasks written
by the earlier reference build may carry `runtime session provisioning failure`
or `runtime preflight failure`; the adapter treats those values as the same
one-time retry state.

After concrete submission proof exists, a missing worker result is a different
failure class. Automatic frontier routing must not silently submit that work
again. The operator may use `valp dispatch` with one explicit agent, role,
`--recover-incomplete`, and `--retry-generation 1`. The packaged HERDR adapter
validates the complete work-item and control-contract identity before choosing
one outcome. When all expected refs have arrived, it appends the completion for
the original submission without preflight or transport. When all refs remain
absent or invalid, it performs a fresh preflight and appends a distinct retry
receipt only after successful resubmission. Partial evidence, a second recovery
attempt, and a failed recovery transport stop fail-closed; neither the ordinary
runtime retry nor another explicit recovery may loop. The originating receipt
is never rewritten.

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
| copied prompt or review artifact | manual adapter |

A terminal pane is only one session type. Non-pane runtimes should export
equivalent job/session identifiers instead of fake pane fields.

The public `agent-sessions` schemas therefore keep context, launch, runtime
scope, and runtime identity adapter-neutral. Pane, terminal, workspace, and tab
fields belong to the HERDR reference-adapter record; queue, hosted, remote, and
future adapters use their own native identifiers. The coordinator reads an
Agent launch entrypoint from current capability evidence or explicit adapter
configuration. It does not maintain a preferred list of Agent commands.

A task may bind runtime-only launch evidence through matching
`task_runtime_capabilities` markers in routing and state. Both markers name the
same task-relative record and SHA-256 digest. Only `launch_argv` and
`version_command` are overlaid onto current capability evidence; task role,
permission, approval, and routing fields do not come from this runtime record.
The file alone is not authority. A missing, mismatched, or stale marker fails
closed, and the overlay cannot replace an accepted live session binding.
For an explicit replacement, an adapter-native structured `not found` result
for the exact recorded scope counts as absence. Generic command failure,
timeout, permission denial, or unstructured output does not.

Every receipt repeats the projection's exact adapter id, and that id must match
the routed runtime adapter record in routing and state. The common audit checks
the provider-neutral binding chain; pane, absolute executable, workspace, and
non-focused provisioning checks run only for the explicitly selected HERDR
reference adapter.

Session ownership is separate from session addressability. A pane id proves
where input can be sent; the task-local binding plus adapter provisioning
receipt proves why VALP is allowed to use it for this project/task. Full Mode
submission proof cites the binding generation and non-secret identity token.

Task-owned launch wrappers are part of the runtime adapter boundary. A wrapper
that runs an identity probe or reports model metadata must not replace a TUI
with a pipe or a canonical input proxy. It must preserve a child PTY, resize and
signal behavior, and raw key bytes. Startup transcripts are written only after
task-scoped redaction; raw credential-bearing output is never the durable
artifact.

For HERDR replacements, the adapter passes the binding generation to the
wrapper as `VALP_AGENT_BINDING_GENERATION`. The wrapper uses it instead of a
static generation-1 config value and publishes the resulting generation in both
model and lifecycle metadata.

Model metadata and Agent state are separate signals. For HERDR, an interposed
launcher reports the declared Agent id plus `idle` after the child PTY is ready,
then `working` only after dispatch input reaches that child. A task-owned
binding with observed provider/model/session metadata but `agent_status:
unknown` fails dispatch preflight. The adapter does not reinterpret pane text,
Enter insertion, or a live wrapper PID as working proof.

The task-owned state report repeats its state, source, session id, monotonic
sequence, and binding generation as structured pane metadata. When all fields
match the accepted binding, this report takes precedence over generic HERDR
screen/title inference such as a transient startup `done`. A partial report or
generation mismatch is a binding conflict and fails preflight; it does not
silently fall back to the generic state. The session id must identify the same
task and Agent as the binding, so an arbitrary nonempty or cross-task id cannot
claim this precedence.

During bounded post-provision readiness, the packaged adapter waits until both
the model/session probe is observed and the same task-owned Agent reports
`idle` or `working`. This closes the startup race where metadata is visible one
snapshot before the child TUI lifecycle report. The later delivery preflight
does not accept a terminal, missing, or unknown Agent state.

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
task only when a valid Leader declaration already exists. Otherwise it may only
publish or refresh non-mutating capability facts. It must stop before execution
and record `block_for_approval`.

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
capability passport inputs per addressable Agent session
provider matrix
context policy
runtime preflight
user-selected Leader evidence
Leader assignment declaration
VALP assignment validation
dispatch submission proof
project/task-owned session bindings and provisioning receipts
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

When timeout wins a wake race, the accepted suspension projection freezes the
receipt cursor it observed. A completion receipt already inside that boundary
is a losing event from the same race and must be rejected as a conflicting
wake. Only a newer identity-bound completion beyond that cursor is eligible for
the explicit late-completion recovery path.

Continuation envelope identifiers (`suspension_id`, `wake_id`, and
`wake_event_id`) must be content-addressed `sha256:` values with 64 lowercase
hexadecimal characters. Adapters must validate them before constructing or
looking up artifact paths. The active suspension epoch comes only from the
authoritative task state projection; an envelope is accepted only when its
epoch matches that projection exactly, and persisted envelopes cannot raise or
otherwise redefine the active epoch.

## Coordinator Patterns

VALP does not choose a universal or task Leader. The user does.

Common patterns:

| Runtime shape | Coordinator pattern |
|---|---|
| pane controller | the user-selected Leader declares worker sessions and may optionally declare itself as a runtime coordinator |
| daemon queue | the user-selected Leader declares assignments; the daemon validates and records execution evidence |
| hosted platform | the user-selected Leader declares assignments; the platform controller writes validation, state, and evidence refs |
| manual | the user selects a human or Agent Leader, who writes declarations, attestations, and synthesis |
| squad | the user-selected Leader writes visible member assignments and handoffs |

The Leader selection reference and every assigned role reason must be recorded.
The Leader is not automatically a routed worker. If a runtime coordinator is
declared, it must match the user-selected Leader. Local defaults, Doctor scores,
and runtime availability are hints or validation evidence, not selection
authority.

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
Leader-declared Agent's pane is below the adapter's minimum size, the adapter
must stop dispatch or record the dispatch as blocked until the pane is repaired.
It must not select a substitute Agent.

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
bin/valp route TASK-QUEUE --workspace /path/to/workspace \
  --assignments /path/to/assignment-declaration.json --runtime queue
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

### Reference LangGraph v3 receipt path

The Reference System LangGraph Adapter is the first bounded Adapter path that
uses canonical v3 receipts end to end. Its authoritative task ledger is:

```text
runtime/langgraph/receipts.v3.jsonl
```

The Adapter obtains installation ID and the active non-zero Leader epoch from
the initialized Reference System control plane, uses the adapter-issued run ID
as the Attempt ID, digests the exact submitted request, persists typed process-
and content-bound proof records, and sends every accepted receipt write through
`ReceiptStore`. Resume and audit strictly load that same ledger and verify proof
and approval-policy digests before accepting the receipt chain.

`runtime/langgraph/adoption.json` marks a task as adopted. Adopted audit never
falls back to v2 when the v3 ledger is absent or invalid. Before run submission,
the Adapter persists a stable intent and sends its ID in provider metadata. A
prepared intent with no persisted provider outcome blocks redispatch until
explicit reconciliation; an accepted intent reuses the recorded run. Process
and content proof use distinct records: one binds the provider response, while
the other binds the exact request digest, provider-response digest, and explicit
acknowledgement.

This is an atomic per-task cutover. A non-empty legacy/v2
`dispatch-receipts.jsonl` cannot coexist with a non-empty LangGraph v3 ledger,
and a compatibility ledger blocks a new LangGraph v3 submission before runtime
invocation. Post-commit `unknown_or_committed` outcomes are reconciled by strict
reread; uncertainty never causes another LangGraph run submission. Dependency
prerequisites are checked from the v3 ledger before runtime invocation.

HERDR, Queue, Manual Mode, and workflow observation/recovery writers remain on
their existing legacy/v2 compatibility paths. No in-place migration is executed
and this adoption does not prove production hosting, sudden-power-loss
durability, hostile-writer safety, or Windows parity.

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

An adapter also must not select the Leader, author task assignments, or replace
a blocked Agent. It transports and records the user/Leader authority chain and
the VALP validation result.

## Coordinator Continuation

Provider-neutral continuation uses an immutable envelope on the typed
`runtime_control` channel. The reference file-backed implementation is exposed
provisionally by `valp_cli.continuation.ContinuationStore`; it separates wake persistence
(`pending`) from invocation CAS (`claim`) and provider consumption (`consume`).
Only a receipt carrying a real provider/session invocation ID plus durable
duplicate-suppression evidence can emit
`continuation_started` and `resume_consumed`. Hermes CLI is an adapter example;
pane insertion remains transport-only, an unbound CLI or App bootstrap surface
remains Manual, and no synthetic wake/output digest may be promoted to
`automatic_full`.

Hermes is currently Manual/degraded: `-z` is a oneshot path that bypasses
resume, and `hermes chat -q --resume` uses the user-message channel. Neither is
a typed `runtime_control` continuation API, so neither may produce the two
provider-consumption events.

The candidate store revalidates the exact persisted envelope, payload, control
contract, full invocation key, target tuple, capability proof, and immutable
provider receipt at each transition. Pending envelopes are recovered from disk
after restart. Unsupported file-locking platforms fail closed; they do not
append an unlocked ledger.
