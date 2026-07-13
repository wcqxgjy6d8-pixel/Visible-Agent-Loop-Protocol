# Schema Versioning

VALP has two version layers:

| Layer | Example | Meaning |
|---|---|---|
| protocol version | `0.2.0` | Human-readable contract: lifecycle, receipts, adapters, gates, Done Criteria |
| schema version | `valp-capability-routing.v1` | Machine-readable artifact shape for one JSON/JSONL file |

Schema versions are independent from protocol versions. A protocol release can
move from `0.1.0-draft` to `0.2.0` while a schema remains `v1`, as long as
the artifact shape stays backward-compatible.

## Compatibility Rules

- Additive fields should be accepted when possible.
- Readers should preserve or ignore unknown fields instead of failing, unless
  the unknown field changes a safety gate.
- Breaking artifact shape changes require a new schema version.
- Task folders should record the schema version for each machine-readable
  artifact they write.
- Adapters should not silently coerce an unknown schema into a known one when it
  affects receipts, approval gates, evidence validity, or Done Criteria.

## Practical Guidance

For external runtime implementers:

1. Read the `schema_version` field first.
2. Validate required fields for the artifact you consume.
3. Preserve unknown fields when rewriting task evidence.
4. Treat unknown safety-gate fields as warnings or blockers, not as success.
5. Record adapter-specific extensions under clearly named extension fields.

## Deterministic Wait Compatibility

`valp-visible-loop-state.v1` suspensions and unversioned legacy receipts remain
readable for existing tasks. They are legacy-read-only for deterministic wake:
an adapter must not infer work-item, role, generation, epoch, or accepted
sequence fields from agent name or timestamps.

New deterministic waits use state v2 plus `valp-dispatch-receipt.v2`,
`valp-wait-policy.v1`, `valp-wait-event.v1`, and `valp-wake-result.v1`.
State v2 and wait-event projections share the closed
`schemas/suspension.schema.json` contract. Each strict epoch records a
content-addressed `wait-policies/<sha256>.json` snapshot of its validated root
policy, and historical replay resolves that immutable ref. `checkpoint_ref`
is an optional opaque-ref field and is present only when it names a safe,
existing, non-empty task-local artifact. Its presence does not prove
coordinator restorability or exactly-once continuation invocation.
External runtime-failure, cancellation, and user-input wakes also use a closed
`valp-exception-wake.v1` source artifact whose exact bytes are digest-bound to
the accepted wake. Migration must be explicit. Rewriting a v1 artifact in
place does not create the missing historical identity or replay evidence.
