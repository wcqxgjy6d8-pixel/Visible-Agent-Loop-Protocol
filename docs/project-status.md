# Project Status And Evidence

VALP is currently an early open protocol release plus a reference CLI. Treat it
as a portable evidence standard and coordination shape, not as a finished
multi-agent platform.

## Current Package

| Area | Current state |
|---|---|
| Protocol | `0.2.0` |
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
| Publish/scan/route/dispatch workflow shape | Covered for reference CLI behavior | `tests/test_valp_workflow.py` |
| Deterministic wake core | Covered locally for dependency barrier, identity rejection, revision CAS, duplicate wake, concurrent wake, and event-to-projection recovery | `valp_cli/workflow.py`, `tests/test_valp_workflow.py` |
| v0.3 installation core | Covered for bootstrap, explicit leader selection, epoch fencing, CAS, idempotency, replay, capability registry, content-addressed claims/reviews, task Done reducer, plugin boundary, and migration dry-run | `valp_cli/control_plane.py`, `valp_cli/task_control.py`, `valp_cli/plugins.py`, `valp_cli/conformance.py`, `tests/test_control_plane.py` |
| Local-process adapter | Covered for approved subprocess submission, lifecycle result, output evidence, and failure status | `valp_cli/process_adapter.py`, `schemas/process-adapter-run.schema.json`, `tests/test_control_plane.py` |
| LangGraph API adapter | Covered for real run/thread identity, submission proof, state/output/checkpoint refs, failure reason, replay identity, and non-terminal wait windows | `valp_cli/langgraph_adapter.py`, `tests/test_langgraph_adapter.py` |
| File-ledger queue concurrency | Covered on the current POSIX test host with synchronized cross-process submitters | `valp_cli/workflow.py`, `tests/test_valp_workflow.py`; real Windows subprocess proof remains open |
| Wait/wake closed artifacts | Covered for shared closed suspension projections, immutable policy snapshots, event/reason pairing, valid/invalid fixtures, identity-bound external wake evidence, generated-result audit, and projection mismatch failure | `schemas/suspension.schema.json`, `schemas/wait-policy.schema.json`, `schemas/exception-wake.schema.json`, `schemas/wait-event.schema.json`, `schemas/wake-result.schema.json`, `tests/test_schema_examples.py`, `tests/test_valp_audit.py`, `tests/test_valp_workflow.py` |
| Doctor diagnostics | Covered for current diagnostics | `tests/test_valp_doctor.py` |
| Bundled Manual Mode example | Covered by audit | `examples/minimal-task/` |
| Bundled synthetic Full Mode fixture | Covered by audit | `examples/full-mode-task/` |
| Bundled synthetic headless queue fixture | Covered by audit | `examples/headless-queue-task/` |
| Sanitized real Manual Mode documentation case study | Covered by audit | `examples/real-doc-calibration-task/` |
| Sanitized real non-HERDR LangGraph false-done case | Covered by audit and a live reproduction script | `examples/langgraph-false-done/`, `docs/case-studies/langgraph-false-done.md` |
| Visible HERDR publish-and-dispatch process | Covered as process proof, not CI | `docs/case-studies/visible-dispatch-process-proof.md` |
| Live HERDR dispatch E2E completion case study | Not covered in repository CI | Requires sanitized task folder plus runtime submission and final audit evidence |
| Live zero-model-turn deterministic wake and exactly-once coordinator continuation | Not covered in repository CI | Requires a wake-ID-bound continuation invocation receipt plus restart/restore evidence from a real adapter |
| Non-HERDR real adapter E2E | Covered for the local LangGraph API development runtime | Production hosting and deterministic coordinator auto-continuation remain open |
| Full state-machine transition suite | Partially covered | Installation transitions are implemented and tested; the task-level legal transition graph remains planned |
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
