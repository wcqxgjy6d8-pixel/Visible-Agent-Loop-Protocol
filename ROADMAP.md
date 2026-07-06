# Roadmap

## 0.1 Protocol Draft

- [shipped] Generic lifecycle.
- [shipped] Auto Visible Mode semantics for automatic visible task intake.
- [shipped] Full/Remote/Manual runtime modes.
- [shipped] Capability routing.
- [shipped] Context policy scanning.
- [shipped] Dispatch receipts.
- [shipped] Evidence layout.
- [shipped] Skill recommendation abstraction.
- [shipped] Profile adapters.
- [shipped] Runtime adapter contract.
- [shipped] Runtime work item state mapping.
- [shipped] Provider matrix.
- [shipped] Optional squad routing.
- [shipped] Local overlays for runtime/operator-specific configuration.
- [shipped] Intelligent routing scores and confidence bands.
- [shipped] Routing feedback records for learning from task outcomes.
- [shipped] Cross-platform Full Mode guidance for macOS, Linux, Windows SSH,
  Windows beta, and Manual Mode.
- [shipped] Quickstart, FAQ, comparison document, issue templates, code of
  conduct, and a complete Full Mode evidence example.

## 0.2 Reference CLI

- [shipped] `valp publish` for local task creation and default auto-route.
- [shipped] `valp scan` for local capability and overlay snapshots.
- [shipped] `valp route` for candidate scoring, selected agents, dispatch
  files, and `dispatch_written` receipts.
- [shipped] `valp dispatch` for HERDR reference-adapter submit command
  generation and optional `--submit`.
- [shipped] `valp preflight` for runtime pane/CLI readiness checks before
  dispatch.
- [shipped] `valp audit` for task evidence folders.
- [shipped] Map Done Criteria to executable audit items.
- [shipped] Validate dispatch receipts.
- [shipped] Validate context policy presence.
- [shipped] Validate approval gates.
- [shipped] Validate runtime preflight gates.
- [shipped] Validate skill recommendation evidence and dispatch surfacing.
- [shipped] Validate invalid/superseded evidence status.
- [shipped] Validate runtime/build/test claims have concrete evidence.
- [shipped] Produce text and JSON task audit reports.
- [shipped] `valp doctor` workspace health diagnostics and optional Markdown
  reports.
- [planned] CLI helper for writing task-local `trigger-policy.json` from
  project/local overlay policy.
- [planned] `valp init` workspace scaffold.
- [planned] Standalone schema validation command.

## 0.2.x Credibility Calibration

- [shipped] Public status and evidence matrix that separates proved repository
  checks from live runtime claims.
- [shipped] Documentation correction that HERDR is a public reference runtime,
  not a closed-source protocol dependency.
- [shipped] Sanitized real-world Manual Mode task case study with task folder,
  receipts, evidence, review, final synthesis, and audit output.
- [shipped] Public coverage matrix for tested CLI/schema/example behavior versus
  missing live-runtime E2E areas.
- [planned] First GitHub release or tag for evaluators who need a stable
  checkpoint.
- [planned] Public Full Mode walkthrough using a live runtime, clearly separated
  from synthetic fixtures.

## 0.3 Runtime Adapters

- [shipped] HERDR reference-adapter command generation in `valp dispatch`.
- [planned] First-class `valp dispatch --adapter <name>` interface.
- [planned] No-HERDR Windows runner/queue adapter prototype.
- [planned] Daemon queue adapter.
- [planned] Managed-agent platform adapter.
- [planned] Manual adapter helper commands.
- [shipped] Remote SSH adapter notes.
- [shipped] Windows preview caveats.
- [shipped] Linux/macOS stable runtime notes.

## 0.4 Recommendation Backends

- [shipped] Generic recommendation contract.
- [shipped] Optional task-skill-router adapter in the reference CLI.
- [shipped] Missing capability reporting.
- [planned] Recommendation audit log beyond task-local JSON evidence.

## 0.5 Routing Intelligence

- [shipped] Candidate scoring in routing evidence.
- [planned] Candidate scoring CLI output.
- [planned] Local overlay validation.
- [planned] Routing feedback aggregation.
- [planned] Confidence-based discovery task generation.
- [planned] Re-routing when tools, context, or evidence gates change.

## 0.6 Profile Packs

- [planned] software-code
- [planned] research
- [planned] web-frontend
- [planned] apple-app
- [planned] document-artifact
- [planned] agent-runtime
- [planned] ops-release
- [planned] prototype
