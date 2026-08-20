# Project Status And Evidence

VALP source currently defines a stable `0.3.0` protocol and reference CLI.
External repository publication, tags, and release artifacts remain separate
delivery gates. Treat VALP as a portable evidence standard and coordination
shape, not as a finished multi-agent platform.

## Current Package

| Area | Current state |
|---|---|
| Protocol | Stable `0.3.0` protocol and reference CLI |
| Repository license | MIT |
| Reference CLI | `bin/valp` with task workflow, v0.3 installation, leader, capability, migration, plugin, hello, conformance, audit, and doctor commands |
| Reference runtime | HERDR for the documented Full Mode path |
| Other runtime adapters | Local-process and LangGraph API adapters are implemented; LangGraph includes approved, identity-bound cancellation effect execution, but its proof uses the local development runtime rather than production hosting |
| Public examples | Three synthetic fixtures, two sanitized real task case studies, and one sanitized visible-dispatch process ledger |
| Public release | `v0.3.0` is the current usage line from the pushed candidate SHA; merge, immutable tag, and external release metadata remain pending publication gates; `v0.2.0` is legacy |

## v0.3.0 Protocol And Reference CLI

[RFC 0001: VALP v0.3 Installation Control Plane](rfcs/0001-v0.3-installation-control-plane.md)
is accepted and implemented as the executable `0.3.0` protocol and reference
CLI. Runtime, adapter, platform, and production claims remain limited to the
evidence in this matrix.
The implementation guide is [docs/v0.3-implementation.md](v0.3-implementation.md).

The shipped `0.3.0` core covers control-root bootstrap, Doctor-backed Leader
candidate discovery, selection/start separation, exact installation-owned
Leader session binding, restart/rotation epoch fencing, message/event ledgers,
replayable state, capability layers, plugin manifest boundary checks, migration
dry-run/apply guards, and isolated conformance fixtures.

## RFC 0002 Local Integration And Kernel Status

[RFC 0002: VALP Layered Architecture](rfcs/0002-layered-architecture.md) and
[SPEC Section 21](../SPEC.md#21-layered-architecture-and-kernel-boundary)
document the accepted Protocol `0.3` ownership and proof model: five top-level
layers, a pure deterministic Kernel, distinct Attempt identity, complementary
proof kinds, cancellation fencing, bounded partial/degraded outcomes,
Kernel-computed dimension gates, and independent version lines.

The public RFC 0002 package comprises `SPEC.md`, `docs/index.md`, this
project status page, and
`docs/rfcs/0002-layered-architecture.md`. Pure Protocol Kernel Slice 1 implements
the closed Layer 02 Task graph for all 13 truth statuses and its canonical
identity, closed-enum, State, Result, idempotency, and rejection contracts. It
also implements ordered `ReplayEntry` reducer re-execution, complete canonical
Result equality, validated Genesis Root replay with negative tests, and a
structural Checkpoint Root machine contract. MVP-H adds independently
trust-policy-bound checkpoint authentication and deterministic suffix replay
through the same reducer, with exact Result equality and zero emitted
obligations. A later pure-Kernel slice adds an identity- and epoch-bound
`Suspension` machine with closed `waiting`/`resumed` states, Kernel-computed
`dependency_ready` wake from the Work Item table, exact policy/frontier/CAS
binding, idempotency, replay equality, and zero replay obligations. A
bounded control slice additionally enforces authority-bound Task, Work Item,
and Attempt cancellation, progression-freezing Interrupt/resume, and versioned
Redirect. Submitted or running cancellation emits a deterministic Adapter
obligation; a digest-chained effect ledger reconciles it as pending, fulfilled,
or blocked without re-emitting the effect during replay. A
task-scoped Layer 01 `KernelStore` now persists an immutable Genesis Root,
canonical ReplayEntry journal, and authenticated checkpoint envelope under one
stable lock; recovery validates full Genesis replay, exact checkpoint prefix,
and suffix equality. Adopted v3 runtime waits now bind the workflow projection
to that journal, advance exact Attempts from terminal receipts, and accept
Kernel `dependency_ready` only after every required Work Item completes. A
separate pure reducer covers canonical v3 receipt write
proposals and digest-bound legacy/v2 migration projections, with valid and
adversarial fixtures. A file-backed Reference System store now adds cooperative
inter-process locking, canonical-prefix replay, CAS, atomic replacement, and
file plus directory synchronization. Current evidence covers process-crash
recovery on the tested macOS/APFS host, not sudden power loss or hostile
writers. LangGraph, atomic HERDR, Queue, and Manual Mode now have explicit ABI
1.0/v3 adoption paths. HERDR completion requires a later identity-matched Agent
terminal state and cannot be inferred from Evidence files alone. Queue acceptance remains distinct from worker delivery;
terminal Queue receipts require an exact worker/run observation and expected
Evidence. Pane fallback remains transport-only. Manual attestation remains
Manual, with append-only revocation and fail-closed conflict adjudication.
Adopted Kernel waits preserve the complete declared Work Item graph across
multiple dependency frontiers. The continuation kernel receives dependency wakes through
an identity-bound `resume_pending` bridge only when a provider has registered
real invocation and duplicate-suppression proof. A local external subprocess
provider now proves approved invocation, provider-owned status reconciliation,
post-consumption crash recovery, and exactly one `resume_consumed`. Production
provider hosting, HERDR runtime-control, native runtime E2E on every platform,
release support, and production reliability remain separate evidence gates.

## Verified In This Repository

The [layered runtime promotion-readiness matrix](layered-runtime-promotion-readiness.md)
separates current source and macOS verification from pending same-commit CI,
live runtime, independent review, strict audit, and release gates.

These checks prove the repository artifacts, not live runtime deployment:

```bash
scripts/verify-examples.sh
python3 -m unittest tests/test_valp_audit.py tests/test_valp_workflow.py tests/test_valp_doctor.py tests/test_schema_examples.py
bin/valp audit examples/minimal-task
bin/valp audit examples/full-mode-task
bin/valp audit examples/headless-queue-task
bin/valp audit examples/real-doc-calibration-task
bin/valp audit examples/langgraph-false-done/task
```

The public GitHub workflow runs the smoke check on Linux, macOS, and Windows
runners. That proves the CLI, schema validation, tests, and bundled example
audits. It does not launch HERDR and does not prove live dispatch on every
platform.

## Coverage Matrix

| Area | Status | Evidence |
|---|---|---|
| JSON and JSONL syntax | Covered | `scripts/verify-examples.sh` |
| JSON schema validation for bundled examples | Covered | `scripts/verify-examples.sh`, `tests/test_schema_examples.py` |
| Audit gates and negative cases | Covered for current CLI rules | `tests/test_valp_audit.py` |
| Correction cycle evidence | Covered for schema, audit pass, and missing-record failure | `schemas/correction-cycle.schema.json`, `examples/full-mode-task/correction-cycle.json`, `tests/test_valp_audit.py` |
| Automation policy evidence | Covered for schema, examples, and audit gate | `schemas/automation-policy.schema.json`, `examples/full-mode-task/automation-policy.json`, `tests/test_valp_audit.py` |
| Context pack evidence | Covered for schema, CLI generation, examples, and audit gate | `schemas/context-pack.schema.json`, `valp_cli/workflow.py`, `examples/full-mode-task/context-pack.json` |
| Learning feedback evidence | Covered for schema, examples, and audit gate | `schemas/learning-feedback.schema.json`, `examples/full-mode-task/learning-feedback.json`, `tests/test_valp_audit.py` |
| Doctor/User/Leader authority chain | Covered for capability passports, explicit user-selected Leader evidence, Leader declarations, validation blockers, and publish-without-routing behavior | `tests/test_valp_doctor.py`, `tests/test_valp_workflow.py` |
| Assignment declaration and validation schemas | Covered for bundled examples and negative cases | `schemas/assignment-declaration.schema.json`, `schemas/assignment-validation.schema.json`, `tests/test_schema_examples.py` |
| Deterministic wake core | Covered locally for dependency barrier, identity rejection, revision CAS, duplicate wake, concurrent wake, and event-to-projection recovery | `valp_cli/workflow.py`, `tests/test_valp_workflow.py` |
| v0.3 installation core | Covered for bootstrap, Doctor-backed candidates, selection/start separation, exact Leader session binding, restart/rotation epoch fencing, CAS, idempotency, replay, capability registry, content-addressed claims/reviews, task Done reducer, plugin boundary, and migration dry-run | `valp_cli/control_plane.py`, `valp_cli/herdr_adapter.py`, `valp_cli/task_control.py`, `valp_cli/plugins.py`, `valp_cli/conformance.py`, `tests/test_control_plane.py`, `tests/test_herdr_adapter.py` |
| Protocol 0.3 layered architecture | Public RFC 0002 package integrated; broader architecture remains a normative target | `SPEC.md` Section 21, `docs/index.md`, this page, and `docs/rfcs/0002-layered-architecture.md` |
| Pure Protocol Kernel slices | Closed Layer 02 Task, Work Item, Attempt, authenticated checkpoint replay, multi-frontier dependency-ready Suspension, authority-bound cancellation, Interrupt/resume, and versioned Redirect graphs implemented | `valp_cli/protocol_kernel.py`, `valp_cli/kernel_runtime.py`, `schemas/protocol-kernel.schema.json`, `tests/test_protocol_kernel.py`, `tests/test_runtime_adapters.py` |
| Pure v3 receipt-write, migration projection, and durable Reference System store | Covered for canonical append proposals, fail-closed legacy/v2 projection, proof-kind negative cases, fixtures, cooperative locking/CAS, and process-crash recovery; LangGraph, HERDR, Queue, and Manual have explicit task-local adoption markers and unmixed ledgers | `valp_cli/protocol_receipts.py`, `valp_cli/receipt_store.py`, `valp_cli/runtime_adapters.py`, `tests/test_protocol_receipts.py`, `tests/test_receipt_store.py`, `tests/test_runtime_adapters.py` |
| Durable Kernel journal, checkpoint recovery, and effect reconciliation | Covered locally for immutable Genesis, canonical journal append, authenticated checkpoint persistence, suffix recovery, strict restart reread, precommit preservation, post-replace reconciliation, adopted-runtime wait/wake binding, and accepted cancellation obligations reconciled against digest-bound proof | `valp_cli/kernel_store.py`, `valp_cli/kernel_runtime.py`, `schemas/kernel-store.schema.json`, `schemas/kernel-effects.schema.json`, `schemas/kernel-workflow-binding.schema.json`, `tests/test_kernel_store.py`, `tests/test_runtime_adapters.py` |
| Adapter ABI 1.0 and Composite provenance | Common manifest, six-operation capability table, typed request/observation, closed proof kinds, contiguous segment provenance, identity-bound HERDR terminal observation, claim-bound Queue worker lifecycle and cancellation, Manual authority/revocation/adjudication, mode-specific proof assessment, and explicit LangGraph/HERDR/Queue/Manual adoption are implemented | `valp_cli/adapter_abi.py`, `valp_cli/runtime_adapters.py`, `schemas/adapter-abi.schema.json`, `schemas/runtime-adoption.schema.json`, `schemas/herdr-terminal-observation.schema.json`, `schemas/queue-lifecycle.schema.json`, `schemas/queue-worker-observation.schema.json`, `schemas/queue-cancellation-proof.schema.json`, `schemas/manual-authority.schema.json`, `schemas/manual-attestation-decision.schema.json`, `tests/test_adapter_abi.py`, `tests/test_runtime_adapters.py`, `tests/test_herdr_adapter.py` |
| Layered runtime machine contracts | Implemented for pure Kernel, local durable stores, ABI adoption, false-Done prevention, durable wait/wake, wake-to-continuation preparation, approved subprocess invocation, post-consumption crash reconciliation, and the typed HERDR coordinator-continuation source adapter | Live HERDR endpoint binding/provider consumption, independent review, strict audit, and same-commit platform CI results remain promotion gates |
| Local-process adapter | Covered for approved subprocess submission, lifecycle result, output evidence, and failure status | `valp_cli/process_adapter.py`, `schemas/process-adapter-run.schema.json`, `tests/test_control_plane.py` |
| LangGraph API adapter | Covered for real run/thread and Attempt identity, canonical v3 ReceiptStore writes, strict resume/audit reads, dependency ordering, exact retry, stale CAS, proof mismatch, mixed-ledger rejection, post-commit reconciliation, false-Done blocking, non-terminal wait windows, and approved cancellation with terminal `interrupted` proof and Kernel effect fulfillment | `valp_cli/langgraph_adapter.py`, `valp_cli/effect_runtime.py`, `valp_cli/audit.py`, `valp_cli/submission.py`, `schemas/adapter-cancellation-proof.schema.json`, `tests/test_langgraph_adapter.py`, `tests/test_kernel_store.py` |
| File-ledger Queue runtime | Durable acceptance is separated from worker execution; atomic claim/cancel CAS, append-only digest chaining, exact retry, conflicting-identity rejection, claim-bound terminal observation, two-phase worker cancellation acknowledgement, Kernel effect fulfillment, and a real local subprocess worker E2E are covered on the current macOS host | `valp_cli/runtime_adapters.py`, `valp_cli/effect_runtime.py`, `schemas/queue-dispatch.schema.json`, `schemas/queue-lifecycle.schema.json`, `schemas/queue-worker-observation.schema.json`, `schemas/queue-cancellation-proof.schema.json`, `tests/test_runtime_adapters.py`, `tests/test_kernel_store.py`; native Windows same-commit proof remains an external gate |
| Wait/wake closed artifacts | Covered for shared closed suspension projections, immutable policy snapshots, event/reason pairing, valid/invalid fixtures, identity-bound external wake evidence, generated-result audit, and projection mismatch failure | `schemas/suspension.schema.json`, `schemas/wait-policy.schema.json`, `schemas/exception-wake.schema.json`, `schemas/wait-event.schema.json`, `schemas/wake-result.schema.json`, `tests/test_schema_examples.py`, `tests/test_valp_audit.py`, `tests/test_valp_workflow.py` |
| Doctor diagnostics and capability passports | Covered for per-surface/session passports, four evidence layers, model/provider/session freshness, Skills, MCP, permissions, context, history binding, and role gates | `schemas/capability-passport.schema.json`, `tests/test_valp_doctor.py` |
| Bundled Manual Mode example | Covered by audit | `examples/minimal-task/` |
| Bundled synthetic Full Mode fixture | Covered by audit | `examples/full-mode-task/` |
| Bundled synthetic headless queue fixture | Covered by audit | `examples/headless-queue-task/` |
| Sanitized real Manual Mode documentation case study | Covered by audit | `examples/real-doc-calibration-task/` |
| Sanitized real non-HERDR LangGraph false-done case | Covered by audit and a live reproduction script | `examples/langgraph-false-done/`, `docs/case-studies/langgraph-false-done.md` |
| Visible HERDR publish-and-dispatch process | Covered as process proof, not CI | `docs/case-studies/visible-dispatch-process-proof.md` |
| Live HERDR dispatch E2E completion case study | Not covered in repository CI | Requires sanitized task folder plus runtime submission and final audit evidence |
| Live zero-model-turn deterministic wake and exactly-once coordinator continuation | Covered locally with an external subprocess provider; the HERDR-specific source adapter also proves typed request, complete receipt, the six-event ledger, and duplicate replay suppression | A real installed HERDR coordinator endpoint and provider consumption remain a separate live Full Mode gate |
| Non-HERDR real adapter E2E | Covered for the local LangGraph API development runtime | Production hosting and deterministic coordinator auto-continuation remain open |
| Full state-machine transition suite | Covered for the implemented dependency-ready runtime path | Installation, closed Layer 02 Task, Work Item/Attempt, dependency-ready Suspension graphs, authenticated Checkpoint Root suffix replay, local durable Kernel recovery, and Adapter adoption are implemented; provider-specific exception wake breadth remains adapter-dependent |
| Context compression runtime integration | Partially covered | Semantics are documented; live adapter enforcement is not yet covered |
| Auto Visible watcher E2E | Covered at source level for the HERDR adapter: exact duplicate publication is suppressed, trigger evidence is exported, and high-risk intake remains approval-blocked | No background watcher installation or live runtime activation is claimed |
| CLI-managed first install E2E | Source behavior covered; real installation activation remains a local proof gate | Protocol defines Doctor-first selection plus exact Leader start; each runtime must prove its own live session binding; an App is optional |

## Reference Runtime Boundary

HERDR is the current reference runtime, not the VALP protocol.

Current externally checked facts on 2026-07-28:

- `https://github.com/ogulcancelik/herdr` is public.
- The repository contains source and project files, including Rust sources,
  `Cargo.toml`, tests, and docs, and GitHub shows published releases.
- The immutable `v0.7.5` tag and Homebrew stable artifact are
  `AGPL-3.0-or-later` with a commercial license option.
- Upstream `master` was relicensed to `Apache-2.0` by commit `cd5ea1be0e69` on
  2026-07-22, after `v0.7.5`; the tagged artifact did not change retroactively.

The versioned HERDR Full Mode boundary uses a structured `herdr agent get`
baseline followed by `herdr agent prompt <target> <payload> --wait --until
working --timeout <ms>`. The `agent_prompted` response must preserve Agent
identity and advance integer `state_change_seq`. Older pane insertion, Enter,
and status observation is transport only: it records `dispatch_inserted`, stays
`Manual-degraded`, and cannot record `dispatch_submitted`.

VALP should not claim that HERDR is required by the protocol. It should also
not imply that another runtime is already first-class until that adapter exists
and exports the required receipts and evidence.

## Known Gaps

| Gap | Why it matters | Current handling |
|---|---|---|
| No production-hosted non-HERDR completion proof | The LangGraph case proves a real local API runtime, not LangSmith or another production deployment | Keep hosting and production reliability claims out of scope until separately evidenced |
| Non-HERDR adapter breadth is limited | One LangGraph adapter proves the boundary but not portability across multiple providers | Keep conformance claims scoped to the tested adapter/runtime pair |
| Live Full Mode E2E coverage is limited | CLI tests cannot prove a real runtime can submit, wait, collect, and audit | Keep Full Mode claims tied to adapter proof |
| Deterministic wake proof remains locally scoped | File-lock/CAS, event-to-projection recovery, and an external local subprocess provider prove the reference path, not HERDR or a production provider | Keep cross-runtime and production conformance pending until those live paths exist |
| Windows directory durability is unproven | The reference core flushes files but has no evidenced Windows parent-directory sync equivalent | Do not claim sudden-power-loss durability on Windows; require adapter-specific proof |
| Windows lock contention lacks native subprocess proof | The retry/deadline policy is platform-neutral, but this local run exercises real cross-process locking only on POSIX | Keep native Windows contention conformance open until run on a Windows host |
| Task-ref grammar | Shared POSIX-style relative-ref grammar is enforced across runtime and artifact schemas | Covered for the reference CLI and current artifact family; adapter-specific path handling remains outside the protocol core |
| Declared Python range lacks endpoint CI | Package metadata declares Python 3.9-3.12 | Public verification now exercises Python 3.9, 3.11, and 3.12 on Linux, macOS, and Windows |
| Provider-specific live cancellation breadth remains bounded | LangGraph and the reference Queue now have executable cancellation/proof paths; HERDR 0.7.4 still exposes no atomic cancel command | Keep HERDR cancellation unsupported until the runtime executes the operation and records identity-bound effect proof; retain Queue production-host and cross-platform proof as separate gates |
| Optional App installer behavior is not a protocol runtime | First-launch UX can accidentally hide path, Leader binding, preflight, and submit boundaries | First-install health gate is specified; any App must expose the same CLI-verifiable evidence |
| Windows local Full Mode is conditional | Native Windows runtime support is beta-dependent | Recommend SSH remote for stable Windows workflow |
| Stable release is early | Users need clear limits around runtime proof and adapter coverage | Use the v0.3.0 protocol and reference CLI with stable/live-runtime claims tied to adapter proof |
| Small public community | Social proof is low | Avoid community-size overclaims |

## Promotion Language

Use:

```text
VALP is an early open protocol and reference CLI for visible, evidence-backed
multi-agent work. It defines dispatch receipts, expected evidence, review gates,
approval gates, and audit checks. HERDR is the current reference runtime for
Full Mode; other runtimes can implement the adapter contract.
```

For early promotion, frame VALP as an evidence discipline or acceptance system,
not as a productivity claim. The safest public invitation is to ask users to run
the minimal audit, share a false-done failure case, or critique whether the
protocol is useful or ceremony.

Avoid:

```text
production-ready multi-agent platform
fully runtime-independent implementation
proven on real-world deployments
native Windows Full Mode without caveats
HERDR-free automation path already shipped
```

## Near-Term Credibility Work

1. Turn the visible dispatch process proof into a full sanitized live Full Mode
   completion case study with runtime submission proof and final audit output.
2. Add an independently operated hosted or agent-provider adapter path.
3. Grow RFCs, failure cases, and adapter feedback around the `v0.3.0` release.
