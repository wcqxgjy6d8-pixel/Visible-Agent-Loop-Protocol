# Visible Agent Loop Protocol

Open protocol for visible, evidence-backed, multi-agent automation.

The protocol is designed for terminal-based AI coding agents, review agents,
research agents, prototype agents, and coordinator agents. It is not tied to a
single project, operating system, terminal emulator, or model provider.

## Fast Start

VALP's default path is Full Mode automation. Install HERDR or another
VALP-compatible runtime first; without a runtime, VALP is only a manual audit
workflow.

Recommended first path:

```text
1. Install HERDR or a VALP-compatible runtime.
2. Verify the runtime can list agents and report status.
3. Create or choose a workspace.
4. Publish a task.
5. Let the runtime scan agents, dispatch visibly, wait for status, and write
   receipts/evidence.
```

Linux/macOS recommended HERDR install:

```bash
curl -fsSL https://herdr.dev/install.sh | sh
herdr status
```

See [INSTALL.md](INSTALL.md) for Homebrew, Windows, SSH remote, and fallback
paths.

New users should start with [docs/quickstart.md](docs/quickstart.md).

## Reference CLI

VALP 0.2 starts with a local coordinator workflow plus an executable quality
gate:

```bash
bin/valp publish TASK-001 --workspace /path/to/workspace --prompt "Fix the bug and verify it"
bin/valp preflight --agent agy
bin/valp dispatch TASK-001 --workspace /path/to/workspace
bin/valp audit examples/full-mode-task
```

`valp publish` creates the task, scans local capabilities, routes selected
agents, writes dispatch files, and records `dispatch_written` receipts.

`valp preflight` checks runtime readiness such as agent panes, terminal size,
CLI version probes, and restart/update signals when the adapter can expose them.

`valp dispatch` prints HERDR adapter submit commands by default. Use
`--submit` to call `herdr-loop submit-dispatch`.

`valp audit` scans a task evidence folder and checks the Done Criteria from
`SPEC.md`, including runtime preflight, skill recommendation evidence, invalid
evidence status, and unsupported runtime/build/test claims.

See [docs/cli-audit.md](docs/cli-audit.md).

## Platform Paths

| User system | Recommended path | Mode |
|---|---|---|
| macOS | HERDR stable installer or Homebrew | Full Mode |
| Linux | HERDR stable installer, manual binary, or package manager | Full Mode |
| Windows stable workflow | SSH to Linux/macOS host running HERDR | Remote Mode with Full Mode guarantees on remote host |
| Windows local workflow | HERDR Windows preview beta | Verify beta limitations before claiming Full Mode |
| No compatible runtime | Manual files and evidence only | Manual Mode, degraded |

See [docs/platform-support.md](docs/platform-support.md) for platform-specific
notes.

## What Full Mode Provides

Full Mode is the intended VALP experience for automated multi-agent work:

- automatic agent and runtime scan;
- provider matrix and context policy scan;
- visible dispatch;
- submission proof;
- status wait;
- receipt ledger;
- evidence gates;
- review/fix/review loop;
- approval gates for high-risk actions;
- final synthesis record.

Manual Mode is a degraded fallback for environments where a compatible runtime
cannot be installed. It can preserve task folders and evidence notes, but it
does not provide automatic dispatch proof, status waits, or runtime-backed
receipt guarantees.

## Core Idea

Visible Agent Loop is a control system, not a chat convention:

```text
publish task
  -> scan runtime, tools, skills, context budgets
  -> load local overlay, if present
  -> select runtime adapter
  -> classify task profile
  -> build provider matrix
  -> preflight runtime and panes
  -> score and route agents by evidence
  -> run skill recommendation, if available
  -> route squad if needed
  -> dispatch visibly
  -> require receipts
  -> map runtime task states
  -> verify with real artifacts
  -> review/fix/review
  -> record final synthesis
```

No agent is assumed to be known from memory. Agent selection is based on current
runtime evidence: declared role, installed skills, available MCP/tools, runtime
status, pane/CLI preflight, permission boundary, context policy, optional skill
recommendation evidence, local overlay hints, prior verification records, and
routing feedback.
Local capability profiles are hints, not fixed assignments. Every task reruns
capability routing.

Managed-agent platforms, daemon queues, and terminal-pane systems can all be
VALP-compatible if they export the required runtime adapter evidence. A runtime
task marked "completed" is not enough by itself; VALP completion still requires
receipts and expected evidence.

## Modes

| Mode | Runtime requirement | Guarantees |
|---|---|---|
| Full Mode | HERDR or VALP-compatible runtime | agent scan, visible dispatch, submission proof, status waits, receipt ledger, evidence gates |
| Remote Mode | SSH to a VALP-compatible runtime | same as Full Mode, with remote runtime caveats |
| Manual Mode | no runtime automation | task folders and evidence files only; no automatic dispatch proof |

Terminal apps such as Ghostty, iTerm, Apple Terminal, Windows Terminal, or a
Linux terminal are display shells. The protocol requires runtime capabilities,
not a specific terminal emulator.

## Runtime Compatibility

HERDR is the reference runtime. As of 2026-07-03, the public HERDR site describes
stable Linux/macOS support and Windows preview beta support. Runtime support can
change; check the current runtime documentation before publishing platform
claims.

Reference: https://herdr.dev/

See [INSTALL.md](INSTALL.md) for the recommended installation paths.

The protocol itself only requires a VALP-compatible runtime interface:

```text
agent list
agent status/read
agent send/insert
pane submit
submission proof
status wait
task evidence store
receipt ledger
```

## Repository Layout

```text
Visible-Agent-Loop-Protocol/
  README.md
  SPEC.md
  INSTALL.md
  ROADMAP.md
  bin/
    valp
  valp_cli/
    audit.py
  LICENSE
  CHANGELOG.md
  CONTRIBUTING.md
  SECURITY.md
  PRIVACY.md
  docs/
    runtime.md
    cli-audit.md
    runtime-preflight.md
    platform-support.md
    quickstart.md
    faq.md
    comparison.md
    runtime-adapters.md
    task-state-machine.md
    local-overlays.md
    intelligent-routing.md
    provider-matrix.md
    squad-routing.md
    workspace.md
    capability-routing.md
    context-compression.md
    dispatch-receipts.md
    skill-recommendation.md
    routing-feedback.md
    profiles.md
    manual-mode.md
  schemas/
    capabilities.schema.json
    local-overlay.schema.json
    routing-feedback.schema.json
    state.schema.json
    routing.schema.json
    receipts.schema.json
    evidence-status.schema.json
    skill-recommendations.schema.json
    attention-map.schema.json
    context-selection.schema.json
    mask-list.schema.json
    evidence-board.schema.json
  examples/
    task-folder-tree.md
    context-policy.json
    routing.json
    dispatch.md
    full-mode-task/
```

## Non-Negotiables

- No hidden agent judgment as decision input.
- No fake success.
- Text inserted into an input box is not delivery.
- Dispatch completion requires receipts and expected evidence.
- High-risk actions require explicit user approval.
- Long context is a reliability risk and must be scanned before dispatch.
- Skill recommendation is evidence, not authority.
- Local overlays are hints, not protocol overrides.
- Agent profiles are routing hints, not fixed assignments.
- Provider capability is scanned, not assumed.
- Routing feedback improves future routing but never replaces current scans.
- Runtime queue completion is not VALP completion unless evidence gates pass.
- Squad routing is visible routing evidence, not hidden agent judgment.
- Profiles adapt the protocol to domains; projects are inputs, not protocol
  centers.

## Status

Initial open protocol draft.
