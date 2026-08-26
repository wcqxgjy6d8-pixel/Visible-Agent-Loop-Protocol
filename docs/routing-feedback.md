# Routing Feedback

VALP should improve from task outcomes, but it must not mistake old memory for
current capability.

Routing feedback records what actually happened after a task. A future Leader
can use that record as a prior, while still running fresh scans for runtime
status, tools, skills, permissions, model identity, and context. Feedback may
inform a Leader; it cannot assign an Agent.

This is the reference CLI's executable feedback path: validated task-local
records may be indexed in `routing-feedback.jsonl`, resolved back to their
source, and then used as a bounded routing prior. The prior never replaces a
current scan or the Leader's visible assignment decision.

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

`selected_agents` is retained as a compatibility field. In new records it is
the unique projection of Agents named in the Leader's validated role
assignments, not a VALP-authored selection. Leader status alone does not put an
Agent in this field.

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

The two records have deliberately different authority:

| Record | Role | May directly change routing state? |
|---|---|---|
| `routing-feedback.json` (and its validated workspace index) | Historical task outcome used as a bounded prior by the reference CLI | Only as input to candidate scoring after task-local gate and evidence checks; it cannot assign an Agent or bypass fresh scans |
| `learning-feedback.json` | Evidence-backed observation, proposal, and disposition for compound engineering | No; registry/passport/protocol/schema/overlay/skill/memory/adapter changes require the relevant review, approval, and change-control path |

If a learning item is accepted, record the disposition and create the
separately scoped change task where required. Do not treat writing the learning
record, dispatch submission, or a runtime completion marker as proof that the
proposed change has been applied.

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

The current runtime, provider, tool, permission, and context state is scanned
again for each task. Historical feedback is retained for reuse only when its
evidence and bindings remain valid; changed or missing current capabilities
invalidate the relevant positive prior.

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
