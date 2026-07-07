# Correction Cycle Evidence

VALP treats self-correction as a runtime capability and an evidence contract.

A runtime may use tests, queues, model review, human review, or another repair
loop to fix work. VALP does not prescribe that engine. It requires a
task-local record when work is rejected, retried, blocked, invalid, or
superseded.

## When To Write It

Write `correction-cycle.json` when a task records any of these signals:

- `dispatch_blocked`;
- expected evidence missing after a runtime says work completed;
- evidence marked `invalid`, `superseded`, `rejected`, or `blocked`;
- critical or high review finding that sends work back to fixing;
- verification failure;
- runtime timeout or runtime failure;
- approval or context policy block;
- manual retry after a failed result.

Tasks with no correction signal may omit the file.

## Required Shape

The file uses `schemas/correction-cycle.schema.json`.

Minimal fixed example:

```json
{
  "schema_version": "valp-correction-cycle.v1",
  "task_id": "TASK-001",
  "status": "fixed",
  "max_rounds": 3,
  "rounds": [
    {
      "round": 1,
      "trigger": "evidence_superseded",
      "owner": "codex",
      "status": "fixed",
      "started_at": "2026-07-03T00:03:00Z",
      "ended_at": "2026-07-03T00:04:00Z",
      "reason": "Draft notes did not prove command-backed work.",
      "rejected_refs": ["agents/codex/draft-evidence.md"],
      "required_actions": ["replace draft notes with implementation evidence"],
      "evidence_refs": ["agents/codex/evidence.md", "evidence/verification.md"],
      "receipt_refs": ["dispatch-receipts.jsonl"]
    }
  ],
  "final_outcome": "fixed",
  "final_evidence_refs": ["agents/codex/evidence.md", "evidence/verification.md"]
}
```

## Audit Rule

`valp audit` skips this gate when no correction signal exists.

When a correction signal exists, audit requires:

- `correction-cycle.json` exists;
- `schema_version` is `valp-correction-cycle.v1`;
- at least one round is recorded;
- `final_outcome` is `fixed`;
- referenced replacement evidence uses task-relative safe paths and exists.

`blocked`, `escalated`, or `cancelled` are useful final states for a real task,
but they do not satisfy Done Criteria. They mean the loop should stop, shrink
scope, route another review, or ask the user.

## Boundary

Correction cycle evidence is not a routing model, retry engine, or verifier.
Those belong to the runtime adapter. The protocol only makes the loop auditable:
what failed, who owned the fix, what changed, and which evidence now proves the
result.
