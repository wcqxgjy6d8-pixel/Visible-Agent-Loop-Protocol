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
