# Manual Mode

Manual Mode is a degraded mode for environments without HERDR or a
VALP-compatible runtime.

It exists so the protocol can still create useful evidence folders, but it
cannot provide Full Mode guarantees.

## Allowed

- Create workspace folders.
- Write `task.md`, `state.json`, `routing.json`.
- Write visible dispatch files.
- Let a human manually copy dispatches.
- Let a human paste results into evidence files.
- Record manual attestations.

## Not Allowed

Manual Mode must not claim:

- automatic dispatch submission;
- runtime-proven agent status;
- pane-level submit proof;
- automatic completion;
- Full Mode receipt equivalence.

## Manual Receipt Labels

Manual Mode should use separate receipt labels, such as:

```text
manual_dispatch_written
manual_delivery_attested
manual_result_attested
manual_blocked
```

These labels are useful audit records but do not equal Full Mode receipts.
