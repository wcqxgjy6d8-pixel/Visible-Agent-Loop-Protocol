# Optional Evidence Catalog

The evidence catalog is an optional, local recall extension for VALP task
evidence. It does not change protocol gates, dispatch semantics, or the
authoritative task files. The SQLite database is a rebuildable projection.

## Design Boundary

- Storage is local SQLite at `.herdr-loop/evidence-catalog.db` by default.
- Text recall uses SQLite FTS5 BM25 ranking plus exact metadata filters. This is
  the catalog's "hybrid" search: lexical ranking and structured evidence
  fields, with no vector service or model call.
- Each entry is bound to a SHA-256 content digest and, when declared, the
  producing agent and dispatch ID from `submission-dependencies.json`.
- Search defaults to `valid`. `stale` and `invalid` evidence require explicit
  status filters.
- Context assembly reads the source again and verifies its digest before adding
  cited text. Index drift is omitted instead of silently recalled.
- `verify` is read-only. `sweep` updates the rebuildable catalog status.
  `invalidate` preserves the root record as `invalid` and dependent records as
  `stale`; it never deletes source evidence.

The catalog has no network path and adds no dependency beyond Python's
standard `sqlite3` module. FTS5 must be enabled in the local SQLite build.

## Indexing

Index one task:

```bash
valp catalog index TASK-001 --workspace .
```

Index every task that has `evidence-status.json`:

```bash
valp catalog index --all --workspace .
```

Only refs explicitly registered in `evidence-status.json` are candidates. The
source must resolve inside the task and under one of these surfaces:

- `evidence/**`
- `agents/<agent>/**`, except `dispatch.md`

Control contracts, control slices, prompts, task state, dispatch text, raw
transcripts, and unregistered workspace files are not indexed. Absolute paths,
`..`, Windows-style separators, and symlinks that escape the task are rejected.
Binary files receive a digest and metadata entry but no FTS text.

## Recall

Search valid evidence:

```bash
valp catalog search "deployment reliability" --workspace .
```

Combine BM25 text ranking with exact filters:

```bash
valp catalog search "verification" \
  --status valid \
  --type verification \
  --agent codex \
  --task TASK-001 \
  --workspace . \
  --json
```

Use `--status stale` or `--status invalid` only when reviewing superseded or
drifted evidence. Repeat `--status` to include more than one state. An exact
digest filter is available through `--digest sha256:<64-lowercase-hex>`.

Build bounded cited context without an LLM call:

```bash
valp catalog context "verification" --limit 5 --max-chars 4000 --workspace .
```

Each included block begins with an `[E<n>]` citation carrying its task-local
ref and digest. If the source is missing, non-text, or no longer matches the
catalog digest, the command records an omission and excludes that block.

## Verification And Invalidation

```bash
valp catalog show catalog:<sha256>
valp catalog verify catalog:<sha256>
valp catalog sweep --workspace .
valp catalog invalidate catalog:<sha256> --reason "source revoked"
```

`sweep` checks both the current source digest and the task's current
`evidence-status.json`. Missing or changed content becomes `stale`; a current
source status of `superseded`, `invalid`, `rejected`, or `blocked` becomes
`invalid`.

## Anonymous Synthetic Fixtures

Anonymous fixtures are explicit synthetic inputs for tests and reusable public
examples. Index a fixture manifest that is inside the workspace:

```bash
valp catalog fixtures examples/evidence-catalog/evidence-catalog-fixtures.json --workspace .
valp catalog fixture "deterministic verification" --workspace .
valp catalog context "deterministic verification" --anonymous --workspace .
```

The manifest conforms to
`schemas/evidence-catalog-fixtures.schema.json`. Fixture sources must resolve
inside the manifest directory. Dependency IDs must exist and form an acyclic
graph. Anonymous output always clears task ID, source ref, agent, dispatch, and
tool-call provenance. Fixture metadata rejects identifying field names.

"Anonymous" here means the catalog output omits origin identifiers. It is not a
PII detector, secret scrubber, or cryptographic anonymity system. Only use
synthetic or already-public fixture text. The bundled fixtures under
`tests/fixtures/evidence-catalog/` contain invented content only.

## Schemas

- `schemas/evidence-catalog-entry.schema.json`
- `schemas/evidence-catalog-fixtures.schema.json`
- `examples/evidence-catalog/evidence-catalog-entry.json`
- `examples/evidence-catalog/evidence-catalog-fixtures.json`

The catalog entry schema enforces the digest, status, provenance shape, and the
anonymous-output nullability contract.
