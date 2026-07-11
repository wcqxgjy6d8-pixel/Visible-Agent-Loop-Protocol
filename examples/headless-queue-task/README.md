# Headless Queue Full Mode Example

This example shows what a completed VALP Full Mode task evidence folder can
look like when the runtime is a daemon queue rather than a terminal-pane
controller.

It is synthetic. It does not correspond to a real project.

```text
examples/headless-queue-task/
  task.md
  state.json
  routing.json
  routing-feedback.json
  dispatch-receipts.jsonl
  timeline.jsonl
  agents/
    codex/
      dispatch.md
      evidence.md
    claude/
      dispatch.md
      review.md
  evidence/
    verification.md
    final-synthesis.md
```

The key point is not the exact folder name. The key point is that routing,
dispatch, receipts, evidence, review, feedback, and final synthesis are all
visible.

The final state also retains a prior `suspension` record. The coordinator
stopped model turns after delivery proof, the queue kept running, and a newer
terminal receipt resumed the loop. That wakeup did not replace the remaining
evidence, review, feedback, synthesis, or audit gates.

This example intentionally has no pane id, terminal size, or
`terminal_size_status` fields. The queue adapter proves readiness with queue
and worker facts instead.

Audit this example with:

```bash
bin/valp audit examples/headless-queue-task
```
