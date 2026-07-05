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

Because the ledger is append-only, validators must evaluate the latest receipt
for each selected agent. A historical `dispatch_completed` does not satisfy the
gate if a later receipt for the same agent is `dispatch_blocked`,
`dispatch_inserted`, or only `dispatch_submitted`. If late evidence appears
after a timeout, the runtime must append a newer `dispatch_completed` receipt
that points to the recovered evidence.

If an evidence file exists but is marked `invalid`, `superseded`, `rejected`, or
`blocked` in `evidence-status.json`, it does not satisfy `dispatch_completed` or
the expected evidence gate.

## Receipt Ledger

Receipts are appended to:

```text
.herdr-loop/tasks/<task-id>/dispatch-receipts.jsonl
```

Example:

```json
{
  "ts": "2026-07-03T00:00:00Z",
  "agent": "claude",
  "event": "dispatch_completed",
  "exit_code": 0,
  "dispatch_ref": "agents/claude/dispatch.md",
  "expected_refs": ["agents/claude/visible-review.md"],
  "summary": "Expected dispatch evidence exists"
}
```

## Manual Mode

Manual Mode may record:

```text
dispatch_written
manual_delivery_attested
manual_result_attested
```

These are not equivalent to Full Mode runtime receipts.
