# Roadmap

## 0.1 Protocol Draft

- Generic lifecycle.
- Full/Remote/Manual runtime modes.
- Capability routing.
- Context policy scanning.
- Dispatch receipts.
- Evidence layout.
- Skill recommendation abstraction.
- Profile adapters.
- Runtime adapter contract.
- Execution task state mapping.
- Provider matrix.
- Optional squad routing.
- Local overlays for runtime/operator-specific configuration.
- Intelligent routing scores and confidence bands.
- Routing feedback records for learning from task outcomes.
- Cross-platform Full Mode guidance for macOS, Linux, Windows SSH, Windows
  beta, and Manual Mode fallback.
- Quickstart, FAQ, comparison document, issue templates, code of conduct, and
  a complete Full Mode evidence example.

## 0.2 Reference CLI

- `valp publish` for local task creation and default auto-route.
- `valp scan` for local capability and overlay snapshots.
- `valp route` for candidate scoring, selected agents, dispatch files, and
  `dispatch_written` receipts.
- `valp dispatch` for HERDR adapter submit command generation and optional
  `--submit`.
- `valp preflight` for runtime pane/CLI readiness checks before dispatch.
- `valp audit` for task evidence folders.
- Map Done Criteria to executable audit items.
- Validate dispatch receipts.
- Validate context policy presence.
- Validate approval gates.
- Validate runtime preflight gates.
- Validate skill recommendation evidence and dispatch surfacing.
- Validate invalid/superseded evidence status.
- Validate runtime/build/test claims have concrete evidence.
- Produce text and JSON task audit reports.
- Generate workspace scaffold.
- Validate schemas.

## 0.3 Runtime Adapters

- HERDR adapter.
- Daemon queue adapter.
- Managed-agent platform adapter.
- Manual adapter.
- Remote SSH adapter notes.
- Windows preview caveats.
- Linux/macOS stable runtime notes.

## 0.4 Recommendation Backends

- Generic recommendation contract.
- Optional task-skill-router adapter.
- Missing capability reporting.
- Recommendation audit log.

## 0.5 Routing Intelligence

- Candidate scoring CLI output.
- Local overlay validation.
- Routing feedback aggregation.
- Confidence-based discovery task generation.
- Re-routing when tools, context, or evidence gates change.

## 0.6 Profile Packs

- software-code
- research
- web-frontend
- apple-app
- document-artifact
- agent-runtime
- ops-release
- prototype
