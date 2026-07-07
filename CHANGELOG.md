# Changelog

## Unreleased

- Adds a project status and evidence matrix that separates current repository
  proof from unproven live-runtime, adapter, case-study, and production claims.
- Clarifies that HERDR is the public reference runtime and not a VALP protocol
  dependency, while keeping first-class non-HERDR adapters on the roadmap.
- Adds a sanitized real Manual Mode documentation calibration case study and
  includes it in the repository smoke check.
- Hardens `valp audit` against weak completion evidence: approval ledgers are
  checked, high-risk goals trigger approval requirements, verification gates
  need concrete evidence, runtime/build/test claims need real evidence, bundled
  examples are schema-validated in CI, and Manual Mode receipt events are now
  covered by the receipt schema.
- Adds `valp doctor` for non-mutating workspace health checks, JSON output, and
  optional Markdown reports.
- Adds `correction-cycle.json` plus audit/schema coverage so rejected, retried,
  blocked, invalid, or superseded work must record a fixed correction trail
  before Done.
- Adds Chinese explanatory notes for Chinese-speaking evaluators while keeping
  `SPEC.md` and schemas as the normative protocol source.
- Adds standard Python development metadata, a `requirements-dev.txt` file, and
  editable-install instructions for the reference CLI.
- Adds `docs/visual-flow.md` with a Mermaid task timeline and task evidence map.
- Clarifies visible-attention audit scope and requires `loop_layer` to be
  documented consistently across visible-attention JSON artifacts.
- Updates repository agent instructions so `scripts/verify-examples.sh` is the
  canonical protocol-edit verification command.
- Defines Auto Visible Mode as opt-in automatic, visible task intake with
  trigger evidence, skill recommendation visibility, approval boundaries, and
  final report expectations.
- Future adapter work may add first-class non-HERDR `valp dispatch --adapter`
  support.

## 0.2.0-draft

- Adds local coordinator commands: `valp publish`, `valp scan`, `valp route`,
  and `valp dispatch`.
- `valp publish` now creates a task, scans local capability/overlay files, routes
  selected agents, writes dispatch files, and records `dispatch_written`
  receipts by default.
- Adds the first reference CLI command: `valp audit`.
- Maps `SPEC.md` Done Criteria into executable PASS/WARN/FAIL/SKIP audit items.
- Adds text and JSON audit output for VALP task evidence folders.
- Adds unit tests for passing and failing audit cases.
- Adds runtime preflight checks for pane size, agent pane readiness, CLI probes,
  and restart/update-needed status where the adapter exposes them.
- Runs available task-skill-router backends during routing and writes
  `skill-recommendations.json`.
- Injects relevant recommended skills into each agent dispatch prompt.
- Adds `evidence-status.json` semantics so invalid, superseded, rejected, or
  blocked evidence cannot satisfy Done.
- Adds audit checks for runtime preflight, skill recommendation execution, and
  unsupported runtime/build/test verification claims.
- Updates the protocol draft to clarify runtime work items, agent sessions,
  Manual Mode, schema/versioning policy, and the HERDR reference-adapter
  boundary.
- Adds a minimal no-runtime Manual Mode example.
- Adds troubleshooting guidance for preflight, blocked dispatches, skill
  recommendation failures, and Manual Mode receipts.
- Treats failed skill recommendation backends as audit warnings instead of hard
  failures because recommendations are evidence, not authority.

## 0.1.0-draft

- Initial open protocol draft.
- Defines Full Mode, Remote Mode, and Manual Mode.
- Defines VALP-compatible runtime interface.
- Adds capability routing with context policy scanning.
- Adds dispatch receipt states.
- Adds evidence folder layout.
- Adds abstract skill recommendation contract.
- Adds default context compression thresholds.
- Adds profile adapter concept.
- Adds runtime adapter contract for pane, daemon queue, hosted/local platform,
  remote, and manual workflows.
- Adds runtime work item state mapping and clarifies that runtime completion is
  not VALP completion without expected evidence.
- Adds provider matrix as routing evidence.
- Adds optional squad routing rules.
- Adds local overlay concept so machine-specific agent profiles stay separate
  from protocol semantics.
- Adds intelligent routing confidence, candidate scoring, and rejected-candidate
  evidence.
- Adds routing feedback records so future routing can learn from outcomes
  without replacing current scans.
- Adds platform support guidance for macOS, Linux, Windows SSH, Windows beta,
  and Manual Mode.
- Adds quickstart, FAQ, comparison document, code of conduct, issue templates,
  and a complete Full Mode task example.
