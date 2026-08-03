# Project Status And Evidence

VALP is currently an early open protocol release plus a reference CLI. Treat it
as a portable evidence standard and coordination shape, not as a finished
multi-agent platform.

## Current Package

| Area | Current state |
|---|---|
| Protocol | Stable `0.2.0`; `0.3.0-draft` normative documentation target |
| Repository license | MIT |
| Reference CLI | `bin/valp` with task workflow, v0.3 installation, leader, capability, migration, plugin, hello, conformance, audit, and doctor commands |
| Reference runtime | HERDR for the documented Full Mode path |
| Other runtime adapters | Local-process and LangGraph API adapters are implemented; the LangGraph proof uses the local development runtime, not production hosting |
| Public examples | Three synthetic fixtures, two sanitized real task case studies, and one visible dispatch process video |
| Public release | Stable evaluation release `v0.2.0` |

## v0.3 Draft Implementation

[RFC 0001: VALP v0.3 Installation Control Plane](rfcs/0001-v0.3-installation-control-plane.md)
is partially implemented as an executable `0.3.0-draft` core. The current stable
release remains `0.2.0`; RFC 0001 remains incomplete and is not stable as a whole.
The implementation guide is [docs/v0.3-implementation.md](v0.3-implementation.md).

The shipped draft core covers control-root bootstrap, explicit leader selection,
leader epochs, message/event ledgers, replayable state, capability layers,
plugin manifest boundary checks, migration dry-run/apply guards, and isolated
conformance fixtures.

## RFC 0002 Local Integration And Kernel Slice 1 Status

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
structural Checkpoint Root machine contract. The structural contract is not
trusted checkpoint replay: authenticated Checkpoint Root suffix replay remains
deferred to Stage 2. A separate pure reducer covers canonical v3 receipt write
proposals and digest-bound legacy/v2 migration projections, with valid and
adversarial fixtures. A file-backed Reference System store now adds cooperative
inter-process locking, canonical-prefix replay, CAS, atomic replacement, and
file plus directory synchronization. Current evidence covers process-crash
recovery on the tested macOS/APFS host, not sudden power loss, hostile writers,
or Windows parity. The LangGraph Adapter is the only runtime path that acquires
canonical v3 proof and uses the durable v3 store end to end. HERDR, Queue,
Manual Mode, and workflow observation/recovery remain legacy/v2
compatibility-only paths. The complete Protocol Kernel and complete third layer
remain unfinished. No broader Adapter conformance, platform parity, release
support, or runtime-wide Done conformance is claimed.

## Verified In This Repository

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
| v0.3 installation core | Covered for bootstrap, explicit leader selection, epoch fencing, CAS, idempotency, replay, capability registry, content-addressed claims/reviews, task Done reducer, plugin boundary, and migration dry-run | `valp_cli/control_plane.py`, `valp_cli/task_control.py`, `valp_cli/plugins.py`, `valp_cli/conformance.py`, `tests/test_control_plane.py` |
| Protocol 0.3 layered architecture | Public RFC 0002 package integrated; broader architecture remains a normative target | `SPEC.md` Section 21, `docs/index.md`, this page, and `docs/rfcs/0002-layered-architecture.md` |
| Pure Protocol Kernel Slice 1 | Closed Layer 02 Task transition graph implemented; this is not the complete Protocol Kernel or complete third layer | `valp_cli/protocol_kernel.py`, `schemas/protocol-kernel.schema.json`, `tests/test_protocol_kernel.py` |
| Pure v3 receipt-write, migration projection, and durable Reference System store | Covered for canonical append proposals, fail-closed legacy/v2 projection, proof-kind negative cases, fixtures, cooperative locking/CAS, and process-crash recovery on the tested macOS/APFS host; LangGraph is the only adopted runtime path | `valp_cli/protocol_receipts.py`, `valp_cli/receipt_store.py`, `schemas/receipts.schema.json`, `tests/test_protocol_receipts.py`, `tests/test_receipt_store.py`, `tests/fixtures/receipt-v3/` |
| Remaining layered core machine contracts and conformance | Not implemented by these bounded slices | Requires remaining Adapter adoption/conformance, broader platform durability proof, independent review, and strict audit |
| Local-process adapter | Covered for approved subprocess submission, lifecycle result, output evidence, and failure status | `valp_cli/process_adapter.py`, `schemas/process-adapter-run.schema.json`, `tests/test_control_plane.py` |
| LangGraph API adapter | Covered for real run/thread and Attempt identity, canonical v3 ReceiptStore writes, strict resume/audit reads, dependency ordering, exact retry, stale CAS, proof mismatch, mixed-ledger rejection, post-commit reconciliation, false-Done blocking, and non-terminal wait windows | `valp_cli/langgraph_adapter.py`, `valp_cli/audit.py`, `valp_cli/submission.py`, `tests/test_langgraph_adapter.py` |
| File-ledger queue concurrency | Covered on the current POSIX test host with synchronized cross-process submitters | `valp_cli/workflow.py`, `tests/test_valp_workflow.py`; real Windows subprocess proof remains open |
| Wait/wake closed artifacts | Covered for shared closed suspension projections, immutable policy snapshots, event/reason pairing, valid/invalid fixtures, identity-bound external wake evidence, generated-result audit, and projection mismatch failure | `schemas/suspension.schema.json`, `schemas/wait-policy.schema.json`, `schemas/exception-wake.schema.json`, `schemas/wait-event.schema.json`, `schemas/wake-result.schema.json`, `tests/test_schema_examples.py`, `tests/test_valp_audit.py`, `tests/test_valp_workflow.py` |
| Doctor diagnostics and capability passports | Covered for per-surface/session passports, four evidence layers, model/provider/session freshness, Skills, MCP, permissions, context, history binding, and role gates | `schemas/capability-passport.schema.json`, `tests/test_valp_doctor.py` |
| Bundled Manual Mode example | Covered by audit | `examples/minimal-task/` |
| Bundled synthetic Full Mode fixture | Covered by audit | `examples/full-mode-task/` |
| Bundled synthetic headless queue fixture | Covered by audit | `examples/headless-queue-task/` |
| Sanitized real Manual Mode documentation case study | Covered by audit | `examples/real-doc-calibration-task/` |
| Sanitized real non-HERDR LangGraph false-done case | Covered by audit and a live reproduction script | `examples/langgraph-false-done/`, `docs/case-studies/langgraph-false-done.md` |
| Visible HERDR publish-and-dispatch process | Covered as process proof, not CI | `docs/case-studies/visible-dispatch-process-proof.md` |
| Live HERDR dispatch E2E completion case study | Not covered in repository CI | Requires sanitized task folder plus runtime submission and final audit evidence |
| Live zero-model-turn deterministic wake and exactly-once coordinator continuation | Not covered in repository CI | Requires a wake-ID-bound continuation invocation receipt plus restart/restore evidence from a real adapter |
| Non-HERDR real adapter E2E | Covered for the local LangGraph API development runtime | Production hosting and deterministic coordinator auto-continuation remain open |
| Full state-machine transition suite | Partially covered | Installation and closed Layer 02 Task transitions are implemented and tested; Work Item and Attempt graphs remain planned |
| Context compression runtime integration | Partially covered | Semantics are documented; live adapter enforcement is not yet covered |
| Auto Visible watcher E2E | Not covered | Trigger policy semantics exist; watcher implementation is runtime-specific |
| App-managed first install E2E | Not covered in repository CI | Protocol now defines doctor-first health gate; App installer implementation must prove it |

## Reference Runtime Boundary

HERDR is the current reference runtime, not the VALP protocol.

Current externally checked facts on 2026-07-06:

- `https://github.com/ogulcancelik/herdr` is public.
- The repository contains source and project files, including Rust sources,
  `Cargo.toml`, tests, and docs, and GitHub shows published releases.
- The repository license text says AGPL-3.0-or-later for open-source use plus
  a commercial license option.

VALP should not claim that HERDR is required by the protocol. It should also
not imply that another runtime is already first-class until that adapter exists
and exports the required receipts and evidence.

## Known Gaps

| Gap | Why it matters | Current handling |
|---|---|---|
| No production-hosted non-HERDR completion proof | The LangGraph case proves a real local API runtime, not LangSmith or another production deployment | Keep hosting and production reliability claims out of scope until separately evidenced |
| Non-HERDR adapter breadth is limited | One LangGraph adapter proves the boundary but not portability across multiple providers | Keep conformance claims scoped to the tested adapter/runtime pair |
| Live Full Mode E2E coverage is limited | CLI tests cannot prove a real runtime can submit, wait, collect, and audit | Keep Full Mode claims tied to adapter proof |
| Deterministic wake proof is local | File-lock/CAS and event-to-projection recovery tests prove the reference core, not a real HERDR or non-HERDR continuation | Do not claim P2 or cross-runtime conformance until both live paths exist |
| Windows directory durability is unproven | The reference core flushes files but has no evidenced Windows parent-directory sync equivalent | Do not claim sudden-power-loss durability on Windows; require adapter-specific proof |
| Windows lock contention lacks native subprocess proof | The retry/deadline policy is platform-neutral, but this local run exercises real cross-process locking only on POSIX | Keep native Windows contention conformance open until run on a Windows host |
| Task-ref grammar | Shared POSIX-style relative-ref grammar is enforced across runtime and artifact schemas | Covered for the reference CLI and current artifact family; adapter-specific path handling remains outside the protocol core |
| Declared Python range lacks endpoint CI | Package metadata declares Python 3.9-3.12 | Public verification now exercises Python 3.9, 3.11, and 3.12 on Linux, macOS, and Windows |
| App installer behavior is not a protocol runtime | First-launch UX can accidentally hide path, preflight, and submit boundaries | First-install health gate is specified; App must expose doctor/preflight/dry-run results |
| Windows local Full Mode is conditional | Native Windows runtime support is beta-dependent | Recommend SSH remote for stable Windows workflow |
| Stable release is early | Users need clear limits around runtime proof and adapter coverage | Use the v0.3 draft core for installation-control-plane evaluation; keep stable/live-runtime claims tied to adapter proof |
| Protocol Kernel beyond Slice 1 is incomplete | The closed Task graph can be mistaken for the complete third layer | Keep migration, additional Adapter, platform, and runtime-wide support claims blocked until matching implementation and conformance evidence exists |
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
3. Grow RFCs, failure cases, and adapter feedback around the `v0.2.0` release.
