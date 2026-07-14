# Twelve-Layer N/I/P Audit Matrix

Status: non-normative audit guidance

This matrix is a maintenance tool, not a protocol architecture and not a
runtime implementation plan. `SPEC.md` and the schemas are the current
normative sources. The v0.3 installation control plane RFC is proposed work and
does not raise a current score.

The matrix separates three questions that a single letter grade hides:

| Axis | 0 | 1 | 2 | 3 |
|---|---|---|---|---|
| N: Normative | absent | concept or prose | explicit guard or MUST | closed schema plus conformance cases |
| I: Implementation | absent | partial CLI or artifact support | deterministic core | complete multi-adapter implementation |
| P: Proof | none | unit or synthetic fixture | one real runtime end to end | cross-runtime plus fault injection |

Scores are deliberately conservative. They describe the whole layer, not the
best implemented feature inside it. A narrow deterministic-wake slice can be
I2 without making the entire state, failure, or idempotency layer I2.

## Current Matrix

| # | Layer | N | I | P | Current evidence and remaining gap |
|---:|---|---:|---:|---:|---|
| 1 | Boundary | 2 | 1 | 1 | `SPEC.md` defines provider-neutral inputs, outputs, non-goals, and adapter responsibility. Reference CLI and adapter docs exist. There is no multi-adapter conformance proof. |
| 2 | Roles | 2 | 1 | 1 | Coordinator, implementer, reviewer, and task-scoped delegation gates are explicit. Submission dependencies and role budgets are implemented. Enforcement still depends partly on adapters, and co-located role isolation lacks real runtime proof. |
| 3 | Messages | 2 | 1 | 1 | Receipt v2, wait policy, wait events, and wake result have closed safety shapes. The wider v0.2 artifact family is still distributed rather than one universal envelope. Proof is schema and unit level. |
| 4 | State | 2 | 1 | 1 | v2 now closes the task status vocabulary and deterministic suspension projection. The narrow reference wait reducer is an I2 tracer bullet, but the full legal transition graph and cross-runtime proof remain incomplete, so the whole layer remains I1. |
| 5 | Evidence | 2 | 2 | 1 | Expected refs, evidence status, audit, and identity-bound wake completion are enforced locally. Content-addressed evidence and verifier independence are not complete across adapters. |
| 6 | Failure | 2 | 1 | 1 | Blocked work, timeout, runtime failure, cancellation, and user input have distinct wake outcomes. Delivery failure, execution deadlines, lease expiry, and late-completion recovery are not yet one complete reducer. |
| 7 | Zero trust | 2 | 1 | 1 | Claimant evidence, independent review, approval gates, immutable wake results, and fail-closed identity checks exist. Reviewer independence and digest review are not proven across runtimes. |
| 8 | Evolution | 1 | 0 | 0 | Current schema versioning rules exist. Fixed `valp.hello`, leader rotation, migration, and retired-read-only semantics are proposed in RFC 0001, not shipped implementation. |
| 9 | Discovery | 1 | 1 | 1 | Per-task capability scan, provider matrix, local overlay, and routing evidence exist. Continuous discovery is adapter work; there is no protocol-owned daemon or live freshness reconciliation proof. |
| 10 | Idempotency | 2 | 1 | 1 | Deterministic wake now has task-scoped idempotency, revision CAS, one accepted wake, and replay. The rest of the protocol does not yet share one implemented idempotency core. |
| 11 | Observability | 2 | 1 | 1 | Receipts, timeline, state revision, wait event sequence, pending/completed/failed sets, and wake result refs are visible. Heartbeat and lease transport remain adapter-specific and unproven. |
| 12 | Termination | 1 | 1 | 1 | Done, blocked, failed, cancelled, correction, and approval outcomes are visible. Full crash/cancel/late-evidence/in-flight disposition and installation retirement remain incomplete. |

No row currently earns P2. The repository has unit tests and synthetic fixtures,
but this matrix does not treat them as a real HERDR end-to-end run or as a real
non-HERDR adapter proof.

## Corrected Findings

Several findings from earlier twelve-layer reviews remain useful questions but
must be stated precisely:

- Bootstrap discovery is not absent from the proposed design. RFC 0001 defines
  a fixed `valp.hello` surface; implementation and interoperability proof remain
  future work.
- Emergency leader rotation is addressed normatively in RFC 0001; it is not a
  current reference-core feature.
- Retirement is not deletion in RFC 0001. `retired-read-only` is proposed, but
  current installation migration and retirement proof are incomplete.
- Idempotency is not absent from the RFC. Same-key/same-digest replay and
  conflict behavior are proposed. Before the deterministic-wake repair, the
  current wait implementation still had I0 for that slice.
- A rich state vocabulary is not the same as a closed transition system.
  `state.status` remains broad outside the versioned deterministic suspension
  projection.
- Strong evidence vocabulary is not proof of content-addressed storage,
  independent verification, or cross-adapter enforcement.

## Deterministic-Wake Tracer Bullet

The first cross-layer conformance slice is:

```text
submitted work items
  -> versioned wait policy
  -> dependency-ready barrier
  -> identity-bound receipt and valid evidence
  -> one revision-CAS wake
  -> immutable wake result
  -> deterministic event-to-projection recovery
```

This reducer path is an I2 tracer bullet. It does not raise the whole State
layer above I1. It primarily exercises layers 4, 5, 6, 10, 11, and 12.
Boundary, role, message, evolution, and discovery contracts constrain the slice
without making VALP a scheduler, daemon, queue host, session manager, or
capability registry.

The success and exception paths are different:

```text
success:   all next-step dependencies complete -> dependency_ready
exception: blocked/failure/cancel/timeout/input -> coordinator handling
```

An exception wake preserves missing completion evidence. It does not convert a
failed or pending work item into success.

## Evidence Anchors

- `SPEC.md` sections 4.1.1 and 10.2: suspension and dependency semantics.
- `schemas/wait-policy.schema.json`: selected dependency barrier.
- `schemas/receipts.schema.json`: legacy receipt and deterministic receipt v2.
- `schemas/state.schema.json`: legacy state v1 and revisioned state v2.
- `schemas/wait-event.schema.json`: replay ledger event contract.
- `schemas/wake-result.schema.json`: immutable accepted-wake result.
- `valp_cli/workflow.py`: reference atomic write, lock, reducer, CAS, and replay.
- `valp_cli/audit.py`: projection, event, barrier, and result consistency gate.
- `tests/test_valp_workflow.py`: barrier, identity, duplicate, concurrency, and
  recovery cases.
- `tests/test_valp_audit.py`: deterministic-wake and provenance gates.
- `docs/rfcs/0001-v0.3-installation-control-plane.md`: proposed target only.

## Update Rule

Raise N only when normative text and safety schemas close the named gap. Raise I
only when the reference core or adapters implement it. Raise P only from
reproducible evidence at the corresponding level. Never raise one axis because
another axis improved.
