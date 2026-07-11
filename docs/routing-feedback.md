# Routing Feedback

VALP should improve from task outcomes, but it must not mistake old memory for
current capability.

Routing feedback records what actually happened after a task. Future routing can
use that record as a prior, while still running fresh scans for runtime status,
tools, skills, permissions, and context.

## When To Write Feedback

Write routing feedback for:

```text
multi-agent tasks
tasks with review/fix loops
tasks where routing confidence was medium or low
tasks with blocked dispatch or missing evidence
tasks that changed agent assumptions
high-risk tasks with approval gates
```

Small single-agent tasks may skip feedback if the runtime would add noise.

## Required Fields

```json
{
  "schema_version": "valp-routing-feedback.v1",
  "task_id": "TASK-001",
  "profile": "software-code",
  "selected_agents": ["codex", "claude"],
  "candidate_agents": ["codex", "claude", "agy"],
  "routing_confidence": {
    "overall": "high",
    "notes": ["codex had required tools and context budget"]
  },
  "expected_evidence": ["agents/codex/evidence.md"],
  "actual_evidence": ["agents/codex/evidence.md", "agents/claude/review.md"],
  "verification_result": "passed",
  "review_result": "passed",
  "approval_outcomes": [],
  "blockers": [],
  "result": "done",
  "worked": ["Codex implementation and Claude review stayed separate."],
  "did_not_work": [],
  "context_gaps": [],
  "lessons": ["Keep reviewer read-only for this profile."],
  "next_routing_hints": ["Codex remains a good implementer when tools are available."],
  "learning_feedback_ref": "learning-feedback.json",
  "rule_change_proposals": [],
  "privacy_notes": ["No secrets stored; evidence paths only."],
  "updated_at": "2026-07-03T00:00:00Z"
}
```

## Storage

Task-local record:

```text
<workspace>/.herdr-loop/tasks/<task-id>/routing-feedback.json
```

Optional workspace memory:

```text
<workspace>/.herdr-loop/routing-feedback.jsonl
```

The workspace memory is an index of prior outcomes. It should contain summaries
and evidence references, not raw private data or hidden conversations.

The index cannot establish trust by itself. Before an entry affects routing,
the reference CLI resolves it back to the task-local `routing-feedback.json`
and checks that the task identity matches. Positive `done` feedback is eligible
only when the task is `done`, completion gates passed, approval is resolved,
verification and review passed, and every `actual_evidence` ref exists inside
the task folder. An index-only or altered entry is ignored.

Task-local learning feedback:

```text
<workspace>/.herdr-loop/tasks/<task-id>/learning-feedback.json
```

`routing-feedback.json` records the outcome. `learning-feedback.json` records
evidence-backed observations and proposed updates. Proposed updates are not
automatically applied to protocol files, local overlays, skills, memory, or
runtime adapter configuration.

## Learning Rule

Feedback may adjust future local capability profiles, but it cannot override:

```text
current runtime status
current tool availability
permission boundaries
context policy
approval gates
receipt gates
expected evidence gates
```

## Failure Patterns To Preserve

Record these clearly because they change future routes:

| Pattern | Future routing effect |
|---|---|
| dispatch inserted but not submitted | require stronger adapter proof |
| runtime completed but evidence missing | keep VALP gate open |
| agent exceeded context threshold | compress before assigning similar work |
| reviewer found high-risk issue | add review earlier for similar profile |
| missing tool/MCP | route setup task before execution |
| repeated blocker | shrink scope or ask user before another loop |
| missing dispatch context | add evidence-backed summary to the next context pack |
| over-broad automatic action | tighten automation policy stop conditions |

## Privacy Rule

Feedback should be enough to improve future routing, not enough to recreate a
private transcript. Prefer evidence paths, short summaries, and non-sensitive
labels.
