# Quickstart

VALP has two practical entry paths:

- understand the protocol without installing a runtime;
- try Full Mode automation with HERDR, the current reference runtime.

Use the first path if you are evaluating VALP as an open protocol. Use the
second path when you want automated dispatch receipts and runtime-backed status
checks.

## Path A: Understand VALP Without A Runtime

Clone the repository and audit the minimal example:

```bash
bin/valp audit examples/minimal-task
```

This shows the smallest useful VALP evidence shape:

```text
task.md
state.json
routing.json
dispatch-receipts.jsonl
skill-recommendations.json
agents/manual-reviewer/review.md
final-synthesis.md
```

Manual or no-runtime examples can teach the evidence discipline, but they do not
prove automatic dispatch submission, agent status waits, or runtime-backed
completion.

If you run `bin/valp publish ...` without a compatible runtime, the CLI can
still create a routed task folder using a generic Manual Mode operator. That
task is not done yet. It will fail `valp audit` until manual result evidence and
a final synthesis are added.

## Path B: Try Full Mode With HERDR

Full Mode requires a compatible runtime. HERDR is the current reference runtime
documented by this repository. Other runtimes can implement VALP by exporting
the adapter evidence in [runtime-adapters.md](runtime-adapters.md).

### 1. Pick Your Platform Path

| System | Recommended path | Expected mode | Caveat |
|---|---|---|---|
| macOS | HERDR stable installer or Homebrew | Full Mode | Reference runtime path |
| Linux | HERDR stable installer or package manager | Full Mode | Reference runtime path |
| Windows stable workflow | SSH into a Linux/macOS HERDR host | Remote Mode | Full Mode guarantees live on the remote host |
| Windows local workflow | HERDR Windows preview beta | Conditional Full Mode | Verify beta limitations before claiming Full Mode |
| Windows without HERDR | Manual Mode today; runner adapter planned | Manual / future adapter | Windows Terminal panes are display, not runtime proof |
| No runtime | Manual files only | Manual Mode | No runtime proof |

### 2. Install Runtime

macOS/Linux recommended:

```bash
curl -fsSL https://herdr.dev/install.sh | sh
herdr status
```

Homebrew users:

```bash
brew install herdr
herdr status
```

Windows stable workflow:

```powershell
ssh you@linux-or-macos-host
herdr status
```

Windows local beta:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://herdr.dev/install.ps1 | iex"
herdr status
```

Windows without HERDR:

Use Manual Mode today. A future no-HERDR Windows adapter should run agent
sessions through a runner or queue that writes VALP receipts and evidence. Do
not treat Windows Terminal split panes or keystroke automation as Full Mode
proof by themselves.

### 3. Verify Full Mode Capability

Before publishing real work, verify the runtime can provide:

```text
agent list
agent status/read
agent send or insert
agent session/message submit
submission proof
status wait
task evidence store
receipt ledger
```

If any required proof is missing, record the gap and either use Manual Mode or
fix the adapter.

### 4. Publish A Task

With the reference CLI:

```bash
bin/valp publish TASK-001 --workspace /path/to/workspace --prompt "Fix the bug and verify it"
```

`publish` runs local `scan` and `route` by default. It writes:

```text
.herdr-loop/tasks/TASK-001/task.md
.herdr-loop/tasks/TASK-001/state.json
.herdr-loop/tasks/TASK-001/routing.json
.herdr-loop/tasks/TASK-001/skill-recommendations.json
.herdr-loop/tasks/TASK-001/attention-map.json
.herdr-loop/tasks/TASK-001/context-selection.json
.herdr-loop/tasks/TASK-001/mask-list.json
.herdr-loop/tasks/TASK-001/evidence-board.json
.herdr-loop/tasks/TASK-001/visible-routing.md
.herdr-loop/tasks/TASK-001/dispatch-receipts.jsonl
.herdr-loop/tasks/TASK-001/agents/<agent>/dispatch.md
```

This is the start of the loop, not the end. The task should fail audit until the
selected agents or manual operator produce the expected evidence and the receipt
ledger is advanced to a completion state.

### 5. Scan And Route

Record:

```text
runtime adapter
provider matrix
local overlay ref, if used
context policies
skills and MCP availability
visible attention map, selected context, masks, and evidence board
skill recommendations surfaced into dispatch prompts
permission boundaries
selected agents
routing reasons
candidate confidence
rejected high-relevance candidates, if relevant
missing capabilities
```

Do not route by habit. Local capability profiles are hints, not fixed
assignments.

To rerun routing explicitly:

```bash
bin/valp scan --workspace /path/to/workspace --task TASK-001
bin/valp route TASK-001 --workspace /path/to/workspace
```

### 6. Preflight

Before sending work, check the runtime:

```bash
bin/valp preflight --agent codex --agent claude
```

For pane-controller runtimes, this should record pane id, status, terminal size,
minimum size, CLI probe result, and restart/update-needed status when available.
For headless runtimes, the adapter should record equivalent job/session facts
instead of pane dimensions.

### 7. Dispatch And Require Receipts

Valid Full Mode dispatch receipt states:

```text
dispatch_written
dispatch_inserted
dispatch_submitted
dispatch_completed
dispatch_blocked
```

Text in an input box is only `dispatch_inserted`. It is not delivery.

If expected evidence is declared, the gate requires `dispatch_completed`.

To see the HERDR reference-adapter submit commands:

```bash
bin/valp dispatch TASK-001 --workspace /path/to/workspace
```

For Manual Mode tasks, the same command prints manual copy instructions instead
of HERDR submit commands.

To actually submit through the local HERDR adapter:

```bash
bin/valp dispatch TASK-001 --workspace /path/to/workspace --submit
```

### 8. Verify, Review, Record

A task is done only when:

```text
runtime adapter and routing are recorded
selected agent context policies are recorded
provider matrix and runtime preflight are recorded
skill recommendations are recorded when available
dispatch receipts satisfy gates
expected evidence exists
runtime/build/test claims cite concrete evidence
verification passed or has a scoped blocker
review has no unresolved critical/high findings
approval gates are resolved
final synthesis is recorded
routing feedback is recorded for non-trivial tasks, if supported
```

Run the reference audit command against a task folder:

```bash
bin/valp audit examples/full-mode-task
```

For machine-readable output:

```bash
bin/valp audit examples/full-mode-task --json
```

## For Runtime Implementers

Start with:

- [runtime-adapters.md](runtime-adapters.md)
- [schema-versioning.md](schema-versioning.md)
- [task-state-machine.md](task-state-machine.md)
- [dispatch-receipts.md](dispatch-receipts.md)
- [provider-matrix.md](provider-matrix.md)
- [troubleshooting.md](troubleshooting.md)

The minimum adapter question is not "can the runtime run an agent?" It is:

```text
Can the runtime export visible dispatches, submission proof, state mapping,
expected evidence refs, receipts, approval status, and final synthesis evidence?
```
