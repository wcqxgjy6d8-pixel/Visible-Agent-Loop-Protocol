# Failure Gallery

VALP is easiest to understand through failures.

This gallery lists failure shapes that normal chat transcripts often hide and
the VALP evidence that catches them. The examples are intentionally small so a
reader can map each failure to receipts, expected evidence, review gates, or
audit output.

## Failure 1: Runtime Says Completed, Evidence Is Missing

Symptom:

```text
runtime status = completed
expected evidence file does not exist
```

Why it matters:

A runtime lifecycle event proves only that the runtime reached its internal
state. It does not prove the assigned work produced the expected artifacts.

VALP catch:

- `dispatch_completed` must cite `expected_refs`.
- `valp audit` fails when expected evidence paths are missing.
- `final-synthesis.md` must reference concrete evidence instead of repeating
  "done".

Try it:

- Run [minimal-audit-demo.md](minimal-audit-demo.md).

## Failure 2: Text Was Inserted But Not Submitted

Symptom:

```text
dispatch text appears in an agent input box
no submit action is proven
no worker output or expected evidence appears
```

Why it matters:

Pane automation can make a task look delegated while the agent never actually
received it.

VALP catch:

- `dispatch_inserted` is not delivery.
- Full Mode needs `dispatch_submitted` with adapter proof.
- Completion still needs expected evidence after submission.

Relevant docs:

- [Dispatch receipts](dispatch-receipts.md)
- [Runtime adapters](runtime-adapters.md)

## Failure 3: Worker Recommendation Is Ignored

Symptom:

```text
reviewer or worker reports a next step
coordinator finalizes without accepting, rejecting, merging, or bounding it
```

Why it matters:

Multi-agent work becomes cosmetic if the coordinator silently discards the
useful parts of worker output.

VALP catch:

- Selected-agent recommendations must be resolved in
  `agent-recommendations.json`.
- The coordinator controls scope: adoption means explicit disposition, not
  unlimited expansion.
- `valp audit` fails non-trivial tasks when required recommendation resolution
  is missing or pending.

Relevant docs:

- [Squad routing](squad-routing.md)
- [CLI audit](cli-audit.md)

## Failure 4: Long Context Pollutes Worker Dispatch

Symptom:

```text
coordinator pastes the full conversation or long skill-router output into every worker prompt
worker spends context budget on stale or irrelevant material
```

Why it matters:

Large dispatch payloads increase drift risk and make the worker responsible for
the leader's context hygiene.

VALP catch:

- Dispatches should use a concise task brief plus task-local references.
- Long context belongs in `task.md`, `routing.json`, and
  `skill-recommendations.json`.
- The leader owns dispatch precision.

Relevant docs:

- [Context compression](context-compression.md)
- [Skill recommendation](skill-recommendation.md)

## Failure 5: Manual Work Is Marketed As Full Mode

Symptom:

```text
human copied prompts and results
repo copy claims automated runtime dispatch proof
```

Why it matters:

Manual Mode can be useful evidence, but it is not runtime-backed submission or
status proof.

VALP catch:

- Manual Mode uses manual receipt labels.
- Full Mode requires adapter submission proof and runtime preflight.
- Project status must distinguish repository proof from live-runtime proof.

Relevant docs:

- [Manual Mode](manual-mode.md)
- [Project status](project-status.md)

## Add A Failure Case

Good failure reports include:

- what claimed completion;
- which receipt or status was present;
- which expected evidence was missing or invalid;
- what `valp audit` did;
- whether the fix needed routing, adapter, docs, schema, or workflow changes.

Use [GitHub Discussions](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/discussions)
for open-ended failure stories and GitHub Issues for reproducible bugs.
