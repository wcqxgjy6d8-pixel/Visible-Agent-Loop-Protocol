# Runtime Requirements

The protocol requires a runtime layer for Full Mode.

## Reference Runtime

HERDR is the reference runtime. It is not the protocol itself.

As of 2026-07-06, public HERDR documentation and repository metadata describe:

- stable Linux/macOS support;
- Windows preview beta support;
- remote attachment through SSH;
- operation from terminal environments;
- a public source repository at `https://github.com/ogulcancelik/herdr`.

Verify current support before making release claims:

```text
https://herdr.dev/
https://github.com/ogulcancelik/herdr
```

## Terminal Emulator Policy

The protocol must not require a specific terminal emulator.

Allowed display shells include, but are not limited to:

```text
Ghostty
iTerm
Apple Terminal
Windows Terminal
Linux terminal emulators
remote SSH terminal sessions
```

The important requirement is runtime control, not the terminal UI.

## Full Mode Runtime Interface

A VALP-compatible runtime must provide:

```text
agent list
agent metadata/status
provider matrix
context policy
agent read
agent send or insert
agent session/message submit
submission proof
runtime task state mapping
status wait
task evidence persistence
dispatch receipt ledger
```

## Auto Visible Intake

A runtime may also implement Auto Visible Mode by watching project policy,
issues, queues, schedules, file events, or platform APIs. This is an intake
layer, not a weaker execution mode.

The runtime must write trigger evidence before dispatch:

```text
trigger source
matched rule
risk classification
selected action
approval requirement
task id and visible refs
```

Watcher support is optional. Full Mode proof still comes from runtime
preflight, dispatch receipts, expected evidence, review, approval resolution,
and final synthesis.

## Runtime Adapters

A runtime adapter maps a concrete execution system into VALP evidence.

Supported adapter classes include:

- pane controller;
- daemon queue;
- hosted/local managed-agent platform;
- remote SSH runtime;
- manual workflow.

Daemon queues and managed-agent platforms may satisfy Full Mode when they export
submission proof, state transitions, output refs, expected evidence refs, and
failure reasons. Their internal `completed` state is not enough by itself; VALP
completion still requires expected evidence.

See [runtime-adapters.md](runtime-adapters.md) and
[task-state-machine.md](task-state-machine.md).

## Remote Mode

Remote Mode is valid when a local machine connects to a machine running a
VALP-compatible runtime that exports the required remote evidence. Remote
guarantees are conditional on adapter evidence for submission, state, receipts,
and expected evidence; SSH connectivity alone is not proof.

The remote runtime owns agent state, agent session state, submission proof, and
receipts. The local client must not pretend local terminal state proves remote
dispatch completion.

## Manual Mode

Manual Mode exists for learning, documentation, and environments without a
VALP-compatible runtime.

Manual Mode can:

- create task folders;
- write dispatch files;
- let a human copy dispatches;
- let a human paste reviews back;
- store evidence.

Manual Mode cannot:

- prove automatic dispatch submission;
- prove agent state transitions;
- claim Full Mode completion.

Manual Mode tasks should mark dispatch delivery as manually attested.
