# Manual Mode

Manual Mode is the VALP path for teams that want the evidence discipline before
they install or build a compatible runtime.

Manual Mode is valid for learning, design review, GitHub pull requests, issue
tracker workflows, and temporary audit trails. It is not Full Mode: it cannot
prove automatic dispatch submission, runtime status waits, pane submit proof, or
runtime-backed receipts.

Auto Visible Mode can be layered over Manual Mode only for intake. For example,
a project rule may automatically create a task folder and dispatch files, but a
human still has to copy work, attest delivery/results, and provide evidence.
That task must not claim Full Mode proof.

## When To Use It

Use Manual Mode when:

- you want to understand VALP without installing HERDR;
- a project wants PR evidence before building a runtime adapter;
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
3. Write `routing.json` with selected human or agent roles and the reason.
4. Write `dispatch.md` files or PR comments visibly.
5. Copy dispatches manually to the recipient agent or reviewer.
6. Record manual receipt labels in `dispatch-receipts.jsonl`.
7. Paste results into evidence files.
8. Write review findings.
9. Resolve approval gates when needed.
10. Write `final-synthesis.md`.
11. Run `valp audit` or a manual checklist.

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
| `manual_dispatch_written` | A dispatch file or PR comment was written |
| `manual_delivery_attested` | A human attests the dispatch was copied or sent |
| `manual_result_attested` | A human attests the expected evidence was received |
| `manual_blocked` | Delivery or result evidence could not be produced |

These labels are useful audit records but do not equal Full Mode runtime
receipts such as `dispatch_submitted`.

## GitHub PR Audit Workflow

A small team can adopt VALP manually inside a pull request:

```text
PR description
  -> task goal and expected evidence
review comment
  -> visible dispatch to reviewer
commit or artifact
  -> evidence file
review comment
  -> findings
final PR comment
  -> final synthesis and approval status
```

Recommended task folder:

```text
.herdr-loop/tasks/PR-123/
  task.md
  state.json
  routing.json
  dispatch-receipts.jsonl
  agents/
    reviewer/
      dispatch.md
      review.md
  final-synthesis.md
```

The PR itself can link to these files. If the task folder lives inside a git
repository, add `.herdr-loop/` to `.gitignore` unless the evidence is sanitized
and intentionally committed as an example.

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
