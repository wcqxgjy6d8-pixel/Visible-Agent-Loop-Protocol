# Dispatch Receipts And Evidence Gates

Dispatch success must be proven.

Text appearing in an input box is not delivery.

## Receipt States

```text
dispatch_written
dispatch_inserted
dispatch_submitted
dispatch_completed
dispatch_blocked
```

## State Meanings

`dispatch_written`
: Dispatch file exists and was surfaced visibly.

`dispatch_inserted`
: Text was inserted into an agent pane or input box. This is not delivery.

`dispatch_submitted`
: Runtime proved the message was submitted.

`dispatch_completed`
: Expected evidence appeared.

`dispatch_blocked`
: Submission or completion could not be proven.

`dispatch_completed` for controlling-agent self-work
: If the controlling agent is also the implementer, it must not paste its own
dispatch prompt into its live context. The adapter may append
`dispatch_completed` for that agent only after the expected task-local evidence
exists, and the receipt should state that self-work was tracked through compact
evidence files.

## Gate Rule

If expected evidence is declared, the gate requires:

```text
dispatch_completed
```

`dispatch_submitted` is not enough when evidence is expected.

For Full Mode and Remote Mode, `dispatch_completed` also needs runtime
submission provenance. Each selected agent must have a prior
`dispatch_submitted` receipt with concrete adapter proof, for example a
submission id, queue id, hosted run id, pane/session submit proof, or equivalent
runtime record. A dry-run dispatch, a local sub-agent result, a simulated
review, or a manually appended `dispatch_completed` receipt is not HERDR/live
runtime proof.

Deterministic Full/Remote suspension accepts only an identity-matching
`dispatch_submitted` receipt with concrete adapter proof. A missing or empty
proof object and `manual_delivery_attested` do not satisfy the suspension entry
guard. Proof identity/ref values must be non-empty strings, directly or inside
a typed adapter record; booleans and counters are not delivery identities.

Because the ledger is append-only, legacy validators evaluate the latest
receipt for each selected agent. Deterministic wait validators evaluate the
latest accepted receipt for the exact task, work item, role, dispatch id, and
dispatch generation. A different role or retry generation cannot supersede or
satisfy that gate. If late evidence appears after a timeout, the runtime must
append a newer identity-bound `dispatch_completed` receipt and use an explicit
recovery transition.

File-backed adapters must allocate the next receipt sequence and durably append
the receipt while holding one inter-process task lock. The reference queue
adapter uses the same lock as the state/wake reducer so concurrent submitters
cannot reuse a sequence.

The reference queue adapter also gives each logical task/dispatch/generation
submission a stable receipt ID. Retrying after a file or directory flush error
reuses an identical queue record and receipt instead of appending a second
sequence. A queue JSON file alone is prepared data, not delivery proof; a worker
must require its matching `dispatch_submitted` receipt or reconcile the pair.

If an evidence file exists but is marked `invalid`, `superseded`, `rejected`, or
`blocked` in `evidence-status.json`, it does not satisfy `dispatch_completed` or
the expected evidence gate.

Expected evidence refs must be task-relative safe paths. They must be non-empty
POSIX-style relative paths and must not be absolute paths, contain backslash
separators, or include `..` path segments. Evidence outside the task folder
cannot satisfy `dispatch_completed`.

## Receipt Ledger

Receipts are appended to:

```text
.herdr-loop/tasks/<task-id>/dispatch-receipts.jsonl
```

Every non-empty JSONL line must parse as a JSON object. A corrupted receipt
ledger is an audit failure, even if earlier valid lines look complete.

Legacy/non-deterministic receipt example (useful for older and Manual Mode task
folders, but not deterministic Full/Remote submission proof):

```json
{
  "ts": "2026-07-03T00:00:00Z",
  "agent": "claude",
  "event": "dispatch_completed",
  "exit_code": 0,
  "dispatch_ref": "agents/claude/dispatch.md",
  "expected_refs": ["agents/claude/visible-review.md"],
  "proof": {
    "runtime_state": "completed",
    "evidence_found": true
  },
  "summary": "Expected dispatch evidence exists"
}
```

Deterministic Full/Remote receipt v2 starts with concrete adapter-issued
submission proof while binding the complete work-item identity:

```json
{
  "schema_version": "valp-dispatch-receipt.v2",
  "receipt_id": "receipt-submit-41",
  "task_id": "TASK-001",
  "event_sequence": 41,
  "ts": "2026-07-13T10:28:45Z",
  "agent": "codex",
  "role": "implementer",
  "work_item_id": "implementer:codex",
  "dispatch_id": "TASK-001:implementer:1",
  "dispatch_generation": 1,
  "event": "dispatch_submitted",
  "dispatch_ref": "agents/codex/dispatch.md",
  "expected_refs": ["agents/codex/evidence.md", "evidence/verification.md"],
  "proof": {
    "adapter_record": {
      "adapter": "herdr",
      "submission_id": "herdr-submit-7f3a"
    }
  }
}
```

The matching terminal completion keeps the same identity and records the
suspension epoch:

```json
{
  "schema_version": "valp-dispatch-receipt.v2",
  "receipt_id": "receipt-complete-42",
  "task_id": "TASK-001",
  "event_sequence": 42,
  "ts": "2026-07-13T10:28:55Z",
  "agent": "codex",
  "role": "implementer",
  "work_item_id": "implementer:codex",
  "dispatch_id": "TASK-001:implementer:1",
  "dispatch_generation": 1,
  "suspension_epoch": 1,
  "event": "dispatch_completed",
  "dispatch_ref": "agents/codex/dispatch.md",
  "expected_refs": ["agents/codex/evidence.md", "evidence/verification.md"]
}
```

Terminal v2 receipts require `suspension_epoch`. Full and Remote Mode reject
manual terminal receipts as wake evidence. Timestamps remain descriptive;
accepted core sequence and revision CAS decide races.

## Manual Mode

Manual Mode may record:

```text
manual_dispatch_written
manual_delivery_attested
manual_result_attested
manual_blocked
```

These are useful audit records, but they are not equivalent to Full Mode runtime
receipts. A Manual Mode task can be complete as a manual evidence trail, but it
must not label manual attestation as `dispatch_submitted`.
