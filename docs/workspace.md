# Workspace And Evidence Layout

The protocol uses a workspace root chosen by the user or project.

It must not hard-code Desktop, macOS paths, or one user's folder names.

## Recommended Layout

```text
<workspace>/
  AGENTS.md
  agents/
    build-agent/
      worklog.md
    review-agent/
      worklog.md
    coordinator-agent/
      worklog.md
    prototype-agent/
      worklog.md
  .herdr-loop/
    index.json
    local-overlay.json
    routing-feedback.jsonl
    agents/
      capabilities.json
      context-policy.json
    locks/
    tasks/
      <task-id>/
        task.md
        trigger-policy.json
        state.json
        routing.json
        routing-feedback.json
        skill-recommendations.json
        attention-map.json
        context-selection.json
        mask-list.json
        evidence-board.json
        visible-routing.md
        runtime-preflight.json
        evidence-status.json
        correction-cycle.json
        timeline.jsonl
        dispatch-receipts.jsonl
        final-synthesis.md
        artifacts/
          manifest.json
        gates/
          context.json
          routing.json
          execution.json
          verification.json
          review.json
          approval.json
        findings/
          findings.json
        approvals/
          requested.jsonl
          user-decisions.jsonl
        agents/
          <agent>/
            dispatch.md
            self-review.md
            visible-review.md
            visible-vote.md
            context-compression.md
            artifacts/
```

## Agent Worklogs

Every participating agent should have two record types:

```text
agents/<agent>/worklog.md
```

Long-term human-readable work history across tasks.

```text
.herdr-loop/tasks/<task-id>/agents/<agent>/
```

Canonical per-task evidence.

## Evidence Rule

Markdown is the human-readable surface. JSON/JSONL is the canonical machine
state.

If a claim is not backed by files, command output, receipts, reviews, or
artifacts, it is not protocol evidence.

`.herdr-loop` is the reference runtime-compatible default folder name. A
different implementation may use another internal path if it can export the same
VALP evidence contract.
