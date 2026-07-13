# Project Status And Evidence

VALP is currently an early open protocol release plus a reference CLI. Treat it
as a portable evidence standard and coordination shape, not as a finished
multi-agent platform.

## Current Package

| Area | Current state |
|---|---|
| Protocol | `0.2.0` |
| Repository license | MIT |
| Reference CLI | `bin/valp` with `publish`, `scan`, `route`, `dispatch`, `wait`, `resume`, `preflight`, `audit`, and `doctor` |
| Reference runtime | HERDR for the documented Full Mode path |
| Other runtime adapters | Contract documented; first-class non-HERDR adapters are planned |
| Public examples | Three bundled fixtures, one sanitized real Manual Mode documentation case study, and one visible dispatch process video |
| Public release | Stable evaluation release `v0.2.0` |

## Proposed v0.3 RFC

[RFC 0001: VALP v0.3 Installation Control Plane](rfcs/0001-v0.3-installation-control-plane.md)
is a proposal for `0.3.0-draft`. The current release remains `0.2.0`. RFC 0001
remains incomplete and is not stable as a whole. Its deterministic-wake subset
is locally implemented and tested in the reference core, schemas, and audit;
the remaining installation-control-plane contracts do not change current
runtime-support or release claims.

## Verified In This Repository

These checks prove the repository artifacts, not live runtime deployment:

```bash
scripts/verify-examples.sh
python3 -m unittest tests/test_valp_audit.py tests/test_valp_workflow.py tests/test_valp_doctor.py tests/test_schema_examples.py
bin/valp audit examples/minimal-task
bin/valp audit examples/full-mode-task
bin/valp audit examples/headless-queue-task
bin/valp audit examples/real-doc-calibration-task
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
| File-ledger queue concurrency | Covered on the current POSIX test host with synchronized cross-process submitters | `valp_cli/workflow.py`, `tests/test_valp_workflow.py`; real Windows subprocess proof remains open |
| Wait/wake closed artifacts | Covered for shared closed suspension projections, immutable policy snapshots, event/reason pairing, valid/invalid fixtures, identity-bound external wake evidence, generated-result audit, and projection mismatch failure | `schemas/suspension.schema.json`, `schemas/wait-policy.schema.json`, `schemas/exception-wake.schema.json`, `schemas/wait-event.schema.json`, `schemas/wake-result.schema.json`, `tests/test_schema_examples.py`, `tests/test_valp_audit.py`, `tests/test_valp_workflow.py` |
| Doctor diagnostics | Covered for current diagnostics | `tests/test_valp_doctor.py` |
| Bundled Manual Mode example | Covered by audit | `examples/minimal-task/` |
| Bundled synthetic Full Mode fixture | Covered by audit | `examples/full-mode-task/` |
| Bundled synthetic headless queue fixture | Covered by audit | `examples/headless-queue-task/` |
| Sanitized real Manual Mode documentation case study | Covered by audit | `examples/real-doc-calibration-task/` |
| Visible HERDR publish-and-dispatch process | Covered as process proof, not CI | `docs/case-studies/visible-dispatch-process-proof.md` |
| Live HERDR dispatch E2E completion case study | Not covered in repository CI | Requires sanitized task folder plus runtime submission and final audit evidence |
| Live zero-model-turn deterministic wake and exactly-once coordinator continuation | Not covered in repository CI | Requires a wake-ID-bound continuation invocation receipt plus restart/restore evidence from a real adapter |
| Non-HERDR real adapter E2E | Not covered | First-class adapter implementation is planned |
| Full state-machine transition suite | Partially covered | State vocabulary is specified; full transition suite is planned |
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
| No standalone public live Full Mode completion case study | The visible dispatch video proves publish and runtime dispatch behavior, but not a complete sanitized Full Mode run by itself | Planned before stronger Full Mode promotion |
| Non-HERDR adapters are not first-class | Runtime-neutral protocol claims need at least one credible non-HERDR implementation | Adapter contract and synthetic queue fixture exist; implementation planned |
| Live Full Mode E2E coverage is limited | CLI tests cannot prove a real runtime can submit, wait, collect, and audit | Keep Full Mode claims tied to adapter proof |
| Deterministic wake proof is local | File-lock/CAS and event-to-projection recovery tests prove the reference core, not a real HERDR or non-HERDR continuation | Do not claim P2 or cross-runtime conformance until both live paths exist |
| Windows directory durability is unproven | The reference core flushes files but has no evidenced Windows parent-directory sync equivalent | Do not claim sudden-power-loss durability on Windows; require adapter-specific proof |
| Windows lock contention lacks native subprocess proof | The retry/deadline policy is platform-neutral, but this local run exercises real cross-process locking only on POSIX | Keep native Windows contention conformance open until run on a Windows host |
| Task-ref grammar is not yet platform-neutral | Drive-qualified refs can be interpreted differently by POSIX and Windows, and the shared pattern appears across many schemas | Apply one versioned repo-wide schema/runtime tightening; do not patch only wait/wake artifacts |
| Declared Python range lacks endpoint CI | Package metadata declares Python 3.9-3.12 while public CI currently exercises Python 3.11 | Add lightweight 3.9/3.12 compatibility jobs in a separately authorized config change |
| App installer behavior is not a protocol runtime | First-launch UX can accidentally hide path, preflight, and submit boundaries | First-install health gate is specified; App must expose doctor/preflight/dry-run results |
| Windows local Full Mode is conditional | Native Windows runtime support is beta-dependent | Recommend SSH remote for stable Windows workflow |
| Stable release is early | Users need clear limits around runtime proof and adapter coverage | Use `v0.2.0` for protocol and CLI evaluation; keep live-runtime claims tied to adapter proof |
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
2. Add the first first-class non-HERDR adapter path.
3. Grow RFCs, failure cases, and adapter feedback around the `v0.2.0` release.
