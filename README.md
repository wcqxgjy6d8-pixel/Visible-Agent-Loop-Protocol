# Visible Agent Loop Protocol

Open protocol for visible, evidence-backed, multi-agent automation.

[![Verify VALP Examples](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/actions/workflows/verify.yml/badge.svg)](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/actions/workflows/verify.yml)

The protocol is designed for terminal-based AI coding agents, review agents,
research agents, prototype agents, and coordinator agents. It is not tied to a
single project, operating system, terminal emulator, or model provider.

## Why VALP?

Agent work often fails in ways that ordinary chat transcripts hide:

- an agent says "done" without evidence;
- text is inserted into an input box but never submitted;
- a runtime marks a job completed before expected files exist;
- a reviewer gives a hidden opinion that the user cannot audit;
- a local preference silently turns into a fixed leader assignment.

VALP turns those failure points into a protocol: visible dispatches, receipt
states, expected evidence, review gates, approval gates, and final synthesis.
It is closer to a control system than a chat convention.

## Entry Paths

Choose the path that matches why you are here:

| Goal | Start here | Runtime required? |
|---|---|---|
| Understand the protocol | Read [SPEC.md](SPEC.md) and audit `examples/minimal-task/` | No |
| Try automated multi-agent work | Install HERDR, the current reference runtime | Yes |
| Inspect a headless runtime shape | Audit `examples/headless-queue-task/` | No |
| Implement a new runtime | Read [docs/runtime-adapters.md](docs/runtime-adapters.md) | Depends on your adapter |

No-runtime first look:

```bash
git clone https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol.git
cd Visible-Agent-Loop-Protocol
bin/valp audit examples/minimal-task
```

Expected result:

```text
VALP audit: PASS
Summary: pass=13 warn=0 fail=0 skip=5
```

Proof check for this repository:

```bash
scripts/verify-examples.sh
```

That script requires Bash and Python. It validates JSON examples and schemas,
runs the unit tests, then audits the bundled examples. The same check runs in
GitHub Actions on Linux, macOS, and Windows runners for push and pull request.

Reference-runtime trial:

```bash
bin/valp publish TASK-001 --workspace /path/to/workspace --prompt "Fix the bug and verify it"
bin/valp dispatch TASK-001 --workspace /path/to/workspace
```

`publish` only creates and routes the task. It is not a completion signal. A
new task will not pass `valp audit` until dispatch receipts, expected evidence,
verification/review status, and final synthesis are recorded.

## Architecture

```text
user request
  -> VALP task folder
  -> reference CLI or compatible runtime adapter
  -> agent sessions, queues, hosted runs, or manual handoffs
  -> dispatch receipts
  -> expected evidence
  -> verification/review/approval gates
  -> final synthesis
  -> valp audit
```

HERDR is the current reference runtime for the automated path. It is not the
protocol itself.

## Runtime Vs Terminal

A terminal app is not enough to provide VALP Full Mode.

Terminal apps such as Windows Terminal, Ghostty, iTerm, Apple Terminal, and
Linux terminal emulators can display multiple agent sessions. Some terminals can
also open split panes from the command line. That helps visibility, but it is
not the same as a runtime adapter.

Full Mode still requires a control layer that can prove:

- which agent received a dispatch;
- whether the dispatch was submitted, not only inserted as text;
- which expected evidence appeared;
- how timeouts, blocked work, and late evidence were recorded;
- whether approval, review, and final synthesis gates passed.

HERDR currently provides that control layer as the reference runtime. A
no-HERDR Windows path can still be VALP-compatible, but it should use a
runner/queue adapter that writes receipts and evidence. It should not rely on
fragile keystroke automation into terminal panes as Full Mode proof.

## Fast Start

VALP's default automated path is Full Mode with HERDR, the current reference
runtime. The protocol supports other compatible runtimes, and this repository
also includes a synthetic headless queue example for adapter authors.

Recommended first path:

```text
1. Install HERDR, the reference VALP runtime.
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
bin/valp doctor --workspace /path/to/Visible-Agent-Loop-Protocol
bin/valp preflight --runtime herdr --agent agy
bin/valp dispatch TASK-001 --workspace /path/to/workspace
bin/valp audit examples/full-mode-task
```

`valp publish` creates the task, scans local capabilities when available, routes
selected agents, writes dispatch files, and records `dispatch_written` receipts.
The current reference scan reads VALP-local capability files first, then
HERDR-compatible files as a compatibility fallback. If no local capability file
is available, it falls back to a generic Manual Mode operator record rather than
assuming a specific AI agent is installed.

`valp preflight` checks adapter-specific runtime readiness such as agent
sessions, terminal size for pane adapters, queue/worker facts for headless
adapters, CLI version probes, and restart/update signals when the adapter can
expose them.

`valp dispatch` prints Manual Mode copy instructions for manual tasks, HERDR
adapter submit commands for pane-controller tasks, or queue enqueue
instructions for headless queue tasks. Use `--submit` only when the selected
runtime is ready.

`valp audit` scans a task evidence folder and checks the Done Criteria from
`SPEC.md`, including runtime preflight, skill recommendation evidence, invalid
evidence status, and unsupported runtime/build/test claims.

`valp doctor` diagnoses a VALP protocol checkout without mutating by default. It
checks local git tracking status, working tree cleanliness, ignored local
residue, JSON/JSONL syntax, bundled example audits, and reference adapter
probes. Use
`--report <path>` or `--report desktop` to write a Markdown report.

See [docs/cli-audit.md](docs/cli-audit.md).

## Proof It Works

The repository includes three self-verifying task examples:

| Example | What it proves | Expected audit |
|---|---|---|
| `examples/minimal-task/` | Manual Mode evidence can be audited without a runtime | `PASS`, `pass=13 warn=0 fail=0 skip=5` |
| `examples/full-mode-task/` | Synthetic Full Mode fixture satisfies runtime, receipt, review, and final synthesis audit gates | `PASS`, `pass=17 warn=0 fail=0 skip=1` |
| `examples/headless-queue-task/` | Full Mode queue fixture passes without pane or terminal-size fields | `PASS`, `pass=17 warn=0 fail=0 skip=1` |

Run the complete smoke check:

```bash
scripts/verify-examples.sh
```

This is repository evidence, not a platform-support claim. It proves the CLI,
schemas, unit tests, and bundled examples pass on the machine running the check.
The GitHub workflow runs this proof on Linux, macOS, and Windows runners. It
does not launch HERDR or prove live agent dispatch. Full Mode on a user machine
still depends on a compatible runtime adapter such as HERDR or another adapter
that exports VALP receipts and evidence.

## Platform Paths

| User system | Recommended path | Mode | Caveat |
|---|---|---|---|
| macOS | HERDR stable installer or Homebrew | Full Mode | Reference runtime path |
| Linux | HERDR stable installer, manual binary, or package manager | Full Mode | Reference runtime path |
| Windows stable workflow | SSH to Linux/macOS host running HERDR | Remote Mode | Full Mode guarantees live on the remote host |
| Windows local workflow | HERDR Windows preview beta | Conditional Full Mode | Verify beta limitations before claiming Full Mode |
| Windows without HERDR | Manual Mode today; runner/queue adapter implementation required for Full Mode | Manual / adapter-specific | Windows Terminal can display panes, but does not itself provide receipts |
| No compatible runtime | Manual files and evidence only | Manual Mode | Useful for learning and audit trails; no runtime proof |

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

Manual Mode is a valid way to learn or adopt the evidence discipline before a
runtime is installed. It can preserve task folders, manual dispatch records, and
evidence notes, but it must not claim automatic dispatch proof, status waits, or
runtime-backed receipt guarantees.

## Core Idea

Visible Agent Loop is a control system, not a chat convention:

```text
publish task
  -> scan runtime, tools, skills, context budgets
  -> load local overlay, if present
  -> select runtime adapter
  -> classify task profile
  -> build provider matrix
  -> preflight runtime and agent sessions
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
status, adapter preflight, permission boundary, context policy, optional skill
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
| Full Mode | HERDR reference runtime or compatible runtime | agent scan, visible dispatch, submission proof, status waits, receipt ledger, evidence gates |
| Remote Mode | SSH to a VALP-compatible runtime | same as Full Mode, with remote runtime caveats |
| Manual Mode | no runtime automation | task folders, manual attestations, and evidence files; no automatic dispatch proof |

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
agent session/message submit
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
    doctor.md
    runtime-preflight.md
    platform-support.md
    quickstart.md
    faq.md
    comparison.md
    runtime-adapters.md
    schema-versioning.md
    task-state-machine.md
    troubleshooting.md
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
    minimal-task/
    full-mode-task/
    headless-queue-task/
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

Open protocol draft with reference CLI version `0.2.0`. The protocol draft is
`0.2.0-draft`; HERDR remains the current reference runtime, not a protocol
requirement.
