# Quickstart

Prerequisites for the repository smoke check:

```text
Git clone of this repository
Bash shell for scripts/verify-examples.sh
Python 3.11 or another supported Python 3
Python jsonschema package for schema validation
```

VALP has three practical entry paths:

- understand the protocol without installing a runtime;
- try Full Mode automation with HERDR, the current reference runtime.
- enable Auto Visible Mode when a local policy or runtime should decide that a
  user request belongs in VALP.

Use the first path if you are evaluating VALP as an open protocol. Use the
second path when you want automated dispatch receipts and runtime-backed status
checks. Use the third path after you already understand the gates and want
intelligent automatic task intake.

## Path A: Understand VALP Without A Runtime

Clone the repository and audit the minimal example:

```bash
git clone https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol.git
cd Visible-Agent-Loop-Protocol
python -m pip install -r requirements-dev.txt
bin/valp audit examples/minimal-task
```

Expected result:

```text
VALP audit: PASS
Summary: pass=13 warn=0 fail=0 skip=7
```

To verify all bundled examples and CLI tests in one command:

```bash
python -m pip install -r requirements-dev.txt
scripts/verify-examples.sh
```

This is the same smoke check used by the repository GitHub Actions workflow on
Linux, macOS, and Windows runners.

For editable local CLI development:

```bash
python -m pip install -e ".[dev]"
valp audit examples/minimal-task
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

The smoke check proves the public examples and reference CLI pass their audit
gates. It does not prove Full Mode support on every operating system; Full Mode
still requires a compatible runtime adapter on the user's local or remote host.

If you run `bin/valp publish ...` without a compatible runtime, the CLI can
still create a routed task folder using a generic Manual Mode operator. That
task is not done yet. It will fail `valp audit` until manual result evidence and
a final synthesis are added.

## Path B: Try Full Mode With HERDR

Full Mode requires a compatible runtime. HERDR is the current reference runtime
documented by this repository. Other runtimes can implement VALP by exporting
the adapter evidence in [runtime-adapters.md](runtime-adapters.md).

### 0. Run The First-Install Health Gate

Do this before real dispatch, especially when VALP is installed through an App
or another installer that manages paths for the user:

```text
install check
  -> valp doctor
  -> runtime preflight
  -> publish/dispatch dry run
  -> user opt-in for real submit or Auto Visible Mode
```

The App or installer should resolve the actual install root instead of assuming
a fixed Desktop checkout path. A broken symlink, stale wrapper, missing Python
dependency, or missing runtime should be shown as a doctor/preflight result, not
as an agent task failure.

A dry-run task is only an environment check. It may write routing and dispatch
files, but it should still fail audit until a real dispatch produces expected
evidence and final synthesis.

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

Each generated `dispatch.md` is meant to be a concise worker prompt. It should
carry the short task brief, role, boundaries, expected evidence, visible
attention slice, and refs to the full task files. Do not judge dispatch quality
by whether it pasted the whole conversation; the full context belongs in
task-local evidence such as `task.md`, `routing.json`, and
`skill-recommendations.json`.

This is the start of the loop, not the end. The task should fail audit until the
selected agents or manual operator produce the expected evidence and the receipt
ledger is advanced to a completion state.

That first failure is expected. A newly published task has dispatch files, but
not completed receipts, expected evidence, or final synthesis yet. Typical
output looks like:

```text
VALP audit: FAIL
Summary: pass=8 warn=2 fail=5 skip=4
[FAIL] dispatch_receipts: latest receipt is not dispatch_completed
[FAIL] expected_evidence: Missing expected evidence
[FAIL] final_synthesis: Missing final synthesis
```

The exact counts can vary by runtime adapter and task profile. Treat this as a
normal "work has not finished" state, not as a broken installation.

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

You can diagnose the workspace at any time:

```bash
bin/valp doctor --workspace /path/to/Visible-Agent-Loop-Protocol
bin/valp doctor --workspace /path/to/Visible-Agent-Loop-Protocol --report ./valp-doctor-report.md
```

Doctor checks local git tracking status, local residue, example audits, JSON
syntax, and reference adapter probes for the protocol checkout. It does not
replace task audit and does not mutate files by default.

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
For Full Mode and Remote Mode, the same agent also needs a prior
`dispatch_submitted` receipt with runtime submission proof. A dry-run command or
local sub-agent result is useful as analysis evidence, but it is not HERDR/live
dispatch proof.

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
correction cycle is fixed if work was rejected or superseded
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

## Path C: Enable Auto Visible Mode

Auto Visible Mode is for users who want to state a task naturally and let local
policy decide whether VALP should run.

Start conservatively:

```text
1. Keep the new install default as manual.
2. Add a project or local overlay trigger policy.
3. Let matching requests publish and route visibly.
4. Dispatch only when runtime preflight and approval gates allow it.
5. Require a final report and `valp audit` before Done.
```

Example local overlay fragment:

```json
{
  "trigger_policy": {
    "default_mode": "manual",
    "auto_visible_mode": "policy_auto",
    "signals": [
      "task mentions VALP",
      "task asks for multi-agent collaboration",
      "task asks for visible evidence or audit"
    ],
    "default_action": "publish_and_route",
    "high_risk_action": "block_for_approval"
  }
}
```

Auto Visible Mode should write:

```text
.herdr-loop/tasks/<task-id>/trigger-policy.json
```

That file records why VALP started, which rule matched, risk classification,
whether approval is required, and where the user can inspect routing, skill
recommendations, dispatch receipts, final report, and audit evidence.

Do not use Auto Visible Mode as a hidden autopilot. High-risk actions still
require explicit user approval before execution.
