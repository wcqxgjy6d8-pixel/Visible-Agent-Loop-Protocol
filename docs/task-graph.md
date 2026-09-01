# Task Graph And Ontology Boundary

VALP has two different graph concepts. They must not be presented as one
feature.

## Ontology

Ontology is the internal semantic and routing projection. It helps the
Leader decompose a task, score declared routing candidates, and project a
Worker-local context slice. It may contain `Agent`, `Capability`, `Model`,
`Policy`, `Evidence`, and routing-prior relationships.

Ontology is not completion proof, is not a user-facing result by itself, and
does not replace the task-local receipt ledger or `valp audit`.

Neo4j is deferred beyond the `v0.3.0` release and is not implemented,
required, or included in this release. A future version may use Neo4j as
an optional cross-task Ontology projection. That projection may read existing
task ledgers and evidence, but it must not select a Leader or Worker, write the
canonical Task Graph, create proof, change protocol state, or become an audit
input or authority.

## Task Graph

Task Graph is a deterministic, projection-only Layer 01 user-facing view of
one task. It connects the task, WorkItems, Agents, receipts, expected evidence,
and a minimal audit summary. Every graph node keeps safe task-relative refs so
a user can follow a displayed claim back to an artifact or ledger record.

The graph is never an authority: it cannot create evidence, turn a missing
artifact into proof, replace the task ledger, or change `valp audit`. Its
canonical JSON has no generation timestamp, absolute task path, copied audit
items, or runtime-local identifier. Given identical task artifacts and audit
summary, the canonical JSON bytes are identical. HTML and SVG are renderings of
that canonical projection, not independent sources of truth.

Generate it with:

```bash
valp graph --task TASK-001 --workspace . --format all
```

The default output directory is:

```text
.herdr-loop/tasks/TASK-001/task-graph/
  task-graph.json
  task-graph.html
  task-graph.svg
```

Refresh the projection after a state, receipt, evidence, or review change:

```bash
valp audit --task TASK-001 --workspace . --emit-task-graph
```

This is a deterministic refresh path, not a claim of a live database or a
real-time browser subscription. A graph can show `blocked`, `failed`, or
missing evidence; it never makes `valp audit` pass.

`state.json` is the authoritative workflow-state projection for the graph's
`status`. The graph never promotes a phase such as `dispatching` to `done`
just because audit and receipt evidence look complete. Each projection also
contains `task_state_transition_digest` (and the same field in `summary`), a
SHA-256 over the task id, revision, status, gates, and active blockers. Consumers
can use it to detect that a graph and a state snapshot came from different
transitions.

The projection also exposes a stable `summary` object for field operators:
`current_status`, `current_blockers`, `missing_evidence`, correction
`round/max_rounds`, automation action, approval gate, cost recording,
continuation state, and a deterministic `next_action`. These are read-model
signals assembled from task-local artifacts; they do not grant approval,
advance a loop, or replace an audit result.

`task-graph.schema.json` owns the public projection shape. It allows only the
six displayed node kinds, defined edge types, and safe task-relative refs.
Implementations must reject or omit absolute paths and traversal refs instead
of resolving them outside the task.

## User reading order

The user-facing view should answer, in order:

1. What was the task goal?
2. Which WorkItems were created?
3. Which Agent handled each WorkItem?
4. Which receipts and artifacts were produced?
5. Did review, approval, and `valp audit` close?

The internal Ontology view remains an advanced routing/debug view. It should not
be the default completion screen.
