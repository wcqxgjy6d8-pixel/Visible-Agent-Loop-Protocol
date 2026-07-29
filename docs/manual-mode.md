# Manual Mode

Manual Mode is the VALP path for teams that want the evidence discipline before
they install or build a compatible runtime.

Manual Mode is valid for learning, design review, external review workflows,
and temporary audit trails. It is not Full Mode: it cannot
prove automatic dispatch submission, runtime status waits, pane submit proof, or
runtime-backed receipts.

Auto Visible Mode can be layered over Manual Mode only for intake. For example,
a project rule may automatically create a task folder, but it cannot choose a
Leader or author assignments. After the user selects a Leader and the Leader's
declaration is validated, a human still has to copy work, attest
delivery/results, and provide evidence. That task must not claim Full Mode
proof.

## When To Use It

Use Manual Mode when:

- you want to understand VALP without installing HERDR;
- a project wants review evidence before building a runtime adapter;
- a human coordinator will copy dispatches and paste results;
- a runtime is unavailable but the team still wants structured task records.

Do not use Manual Mode to claim:

- automatic dispatch submission;
- runtime-proven agent status;
- pane-level submit proof;
- automatic completion;
- Full Mode receipt equivalence.

## Minimal Manual Workflow

1. Create a task folder.
2. Write `task.md` with goal, scope, expected evidence, and approval risks.
3. Run Doctor and let the user select the Leader.
4. Let the Leader write `assignment-declaration.json` with role reasons.
5. Validate it and record `assignment-validation.json` plus `routing.json`.
6. Write `dispatch.md` files visibly.
7. Copy dispatches manually to the recipient Agent or reviewer.
8. Record manual receipt labels in `dispatch-receipts.jsonl`.
9. Paste results into evidence files and write review findings.
10. Resolve approval gates when needed.
11. Write `final-synthesis.md` and run `valp audit`.

## Manual Receipt Labels

Manual Mode should use labels that do not pretend to be runtime proof:

```text
manual_dispatch_written
manual_delivery_attested
manual_result_attested
manual_blocked
```

Meaning:

| Label | Meaning |
|---|---|
| `manual_dispatch_written` | A dispatch file was written |
| `manual_delivery_attested` | A human attests the dispatch was copied or sent |
| `manual_result_attested` | A human attests the expected evidence was received |
| `manual_blocked` | Delivery or result evidence could not be produced |

These labels are useful audit records but do not equal Full Mode runtime
receipts such as `dispatch_submitted`.

## Example

See:

```text
examples/minimal-task/
```

Run:

```bash
bin/valp audit examples/minimal-task
```

The example is intentionally small. It shows how a task can preserve evidence
without claiming Full Mode runtime proof.
