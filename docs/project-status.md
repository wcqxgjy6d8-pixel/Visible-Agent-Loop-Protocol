# Project Status And Evidence

VALP is currently an early open protocol release plus a reference CLI. Treat it
as a portable evidence standard and coordination shape, not as a finished
multi-agent platform.

## Current Package

| Area | Current state |
|---|---|
| Protocol | `0.2.0` |
| Repository license | MIT |
| Reference CLI | `bin/valp` with `publish`, `scan`, `route`, `dispatch`, `preflight`, `audit`, and `doctor` |
| Reference runtime | HERDR for the documented Full Mode path |
| Other runtime adapters | Contract documented; first-class non-HERDR adapters are planned |
| Public examples | Three bundled fixtures plus one sanitized real Manual Mode documentation case study |
| Public release | Stable evaluation release `v0.2.0` |

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
| Publish/scan/route/dispatch workflow shape | Covered for reference CLI behavior | `tests/test_valp_workflow.py` |
| Doctor diagnostics | Covered for current diagnostics | `tests/test_valp_doctor.py` |
| Bundled Manual Mode example | Covered by audit | `examples/minimal-task/` |
| Bundled synthetic Full Mode fixture | Covered by audit | `examples/full-mode-task/` |
| Bundled synthetic headless queue fixture | Covered by audit | `examples/headless-queue-task/` |
| Sanitized real Manual Mode documentation case study | Covered by audit | `examples/real-doc-calibration-task/` |
| Live HERDR dispatch E2E | Not covered in repository CI | Requires installed runtime and live agent sessions |
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
| No public live Full Mode case study | Manual Mode case studies prove task evidence, not live runtime dispatch | Planned before stronger Full Mode promotion |
| Non-HERDR adapters are not first-class | Runtime-neutral protocol claims need at least one credible non-HERDR implementation | Adapter contract and synthetic queue fixture exist; implementation planned |
| Live Full Mode E2E coverage is limited | CLI tests cannot prove a real runtime can submit, wait, collect, and audit | Keep Full Mode claims tied to adapter proof |
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

1. Add a public live Full Mode case study with runtime submission proof.
2. Add the first first-class non-HERDR adapter path.
3. Grow RFCs, failure cases, and adapter feedback around the `v0.2.0` release.
