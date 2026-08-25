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
Summary: pass=13 warn=0 fail=0
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
python -m pip install --upgrade pip setuptools
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
still create an unrouted task folder. It does not invent a generic operator or
select an Agent. The user must select a Leader, and that Leader must declare
Manual Mode assignments before `valp route --assignments` can create dispatch
evidence.

## Path B: Try Full Mode With HERDR

Full Mode requires a compatible runtime. HERDR is the current reference runtime
documented by this repository. Other runtimes can implement VALP by exporting
the adapter evidence in [runtime-adapters.md](runtime-adapters.md).

Before installing, note the version boundary: the immutable HERDR `v0.7.5` tag
and Homebrew stable artifact are `AGPL-3.0-or-later` with a commercial license
option. Upstream `master` was relicensed to `Apache-2.0` by commit
`cd5ea1be0e69` on 2026-07-22, after that release. Verify the license of the
exact artifact you install.

The complete first-time path is:

```text
1. Run `valp doctor --workspace <install-root> --json`.
2. Explicitly choose the Leader; run `valp leader select <principal>`, `valp
   leader start`, `valp leader show`, and `valp leader open`.
3. Run `valp publish TASK-001 --workspace <workspace> --prompt "..."`.
4. Run `valp route TASK-001 --workspace <workspace> --assignments <declaration>`.
5. Inspect `valp dispatch TASK-001 --workspace <workspace>` as a dry run.
6. After explicit user approval, run `valp dispatch TASK-001 --workspace
   <workspace> --submit`.
7. Confirm the terminal shows the installation-owned Leader pane and a fresh
   task-owned Worker pane.
8. Require identity-bound `dispatch_submitted`, then expected evidence and
   `dispatch_completed`.
9. Run independent review and resolve recommendations.
10. Run `valp audit <workspace> --task TASK-001`; `fail_count` must be 0.
```

### 0. Run The First-Install Health Gate

Do this before real dispatch, especially when VALP is installed through an App
or another installer that manages paths for the user:

```text
install check
  -> valp doctor capability passports
  -> user selects Leader
  -> runtime preflight
  -> publish / Leader declaration / route / dispatch dry run
  -> user opt-in for real submit or Auto Visible Mode
```

The App or installer should resolve the actual install root instead of assuming
a fixed Desktop checkout path. A broken symlink, stale wrapper, missing Python
dependency, or missing runtime should be shown as a doctor/preflight result, not
as an agent task failure.

On publish, inspect `state.json.source_provenance`: `task_start` records the
actual invoked entrypoint, resolved source root, and Git commit/tree when
available. A later `valp scan --task ...` refreshes `last_observed` without
rewriting `task_start`. `resolved_dirty` is a warning that the recorded commit
and tree describe only the base revision, not the uncommitted source bytes.

A dry-run task is only an environment check. `publish` itself writes no routing
or dispatch files. After the Leader declaration passes validation, the dry run
may write them, but it should still fail audit until a real dispatch produces
expected evidence and final synthesis.

### 1. Pick Your Platform Path

| System | Recommended path | Expected mode | Caveat |
|---|---|---|---|
| macOS | HERDR stable installer or Homebrew | Full Mode | Reference runtime path |
| Linux | HERDR stable installer or package manager | Full Mode | Reference runtime path |
| Windows stable workflow | SSH into a Linux/macOS HERDR host | Remote Mode | Remote guarantees are conditional on adapter evidence exported by that host; no live continuation E2E is claimed here |
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

For HERDR, Full Mode proof means a structured `herdr agent get` baseline
followed by `herdr agent prompt <target> <payload> --wait --until working
--timeout <ms>`. The `agent_prompted` response must preserve the routed Agent
identity and advance integer `state_change_seq`. Older pane insertion, Enter,
and status observation is transport only: record `dispatch_inserted`, stop as
`Manual-degraded`, and do not record `dispatch_submitted`.

### 4. Doctor, Select, And Start The Leader

Commission capability passports before assignment:

```bash
bin/valp doctor --workspace /path/to/install-root --json
bin/valp leader select <observed-principal-id> --workspace /path/to/install-root
bin/valp leader start --workspace /path/to/install-root
bin/valp leader show --workspace /path/to/install-root
bin/valp leader open --workspace /path/to/workspace
```

Doctor records one passport per addressable Agent surface/session. Inspect the
observed model and provider, session freshness, Skills, MCP, permissions,
context, limitations, and role eligibility. A product name is not model
evidence.

The user explicitly chooses the Leader. `leader start` creates the exact
installation-owned session; `show` verifies its binding and health; `open`
opens that Leader from the caller workspace. None of these commands adopts an
arbitrary existing pane.

### 5. Publish And Route A Task

With the reference CLI:

```bash
bin/valp publish TASK-001 --workspace /path/to/workspace --prompt "Fix the bug and verify it"
```

`publish` creates the task and stops before routing. It writes:

```text
.herdr-loop/tasks/TASK-001/task.md
.herdr-loop/tasks/TASK-001/state.json
```

The CLI prints `routed: false`. This is intentional: VALP does not select the
Leader or any Agent.

The Leader decomposes the task and writes a declaration like
[examples/assignment-declaration.json](../examples/assignment-declaration.json).
Validate it with:

```bash
bin/valp scan --workspace /path/to/workspace --task TASK-001
bin/valp route TASK-001 --workspace /path/to/workspace \
  --assignments /path/to/assignment-declaration.json
```

Successful validation writes:

```text
.herdr-loop/tasks/TASK-001/assignment-declaration.json
.herdr-loop/tasks/TASK-001/assignment-validation.json
.herdr-loop/tasks/TASK-001/routing.json
.herdr-loop/tasks/TASK-001/automation-policy.json
.herdr-loop/tasks/TASK-001/skill-recommendations.json
.herdr-loop/tasks/TASK-001/attention-map.json
.herdr-loop/tasks/TASK-001/context-selection.json
.herdr-loop/tasks/TASK-001/context-pack.json
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
task-local evidence such as `task.md`, `routing.json`, `context-pack.json`, and
`skill-recommendations.json`.

This is the start of the loop, not the end. `selected_agents` in these files
means the unique Agents declared by the Leader. The task should fail audit until
those Agents or a manual operator produce expected evidence and the receipt
ledger reaches a completion state.

That first failure is expected. A newly published task has dispatch files, but
not completed receipts, expected evidence, or final synthesis yet. Typical
output looks like:

```text
VALP audit: FAIL
Summary: pass=8 warn=2 fail=5
[FAIL] dispatch_receipts: latest receipt is not dispatch_completed
[FAIL] expected_evidence: Missing expected evidence
[FAIL] final_synthesis: Missing final synthesis
```

The exact counts can vary by runtime adapter and task profile. Treat this as a
normal "work has not finished" state, not as a broken installation.

### 6. What Route Validation Records

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
user-selected Leader and selection ref
Leader-declared role assignments and reasons
assignment validation status and blockers
candidate confidence
missing capabilities
```

Local capability profiles and candidate scores are hints for the Leader, not
VALP selection authority. If validation blocks, VALP reports the gap and stops;
the Leader must author the next declaration.

### 7. Preflight

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

### 8. Dispatch And Require Receipts

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

To see the detected HERDR packaged-adapter plan:

```bash
bin/valp dispatch TASK-001 --workspace /path/to/workspace
```

For Manual Mode tasks, the same command prints manual copy instructions instead
of a HERDR adapter plan. For HERDR tasks, the plan names the detected
`agent_prompt` or `pane_send_text_enter` transport. Only `agent_prompt` with the
identity-bound sequence proof above is Full Mode; `pane_send_text_enter` remains
`Manual-degraded` transport evidence.

To actually submit through the local HERDR adapter:

```bash
bin/valp dispatch TASK-001 --workspace /path/to/workspace --submit
```

With no `--agent` or `--role`, this submits only the current dependency-ready
frontier. A later call after the committed wake advances the next frontier;
already submitted or completed work items are not sent again. Explicitly
requesting an unready agent or role remains a hard error.

When the reference dispatch helper submits a specific role or agent, it writes
the closed `.herdr-loop/tasks/TASK-001/wait-policy.json` for those exact work
items before delivery. Other adapters may author the file directly from
`submission-dependencies.json`; `examples/wait-policy.json` shows the shape.
`valp wait` rejects a missing policy or work items without concrete delivery
proof. Manual Mode may wait without this file, but its audit result is
explicitly degraded.

After delivery proof exists, suspend coordinator model turns while workers run:

```bash
bin/valp dispatch TASK-001 --workspace /path/to/workspace --wait-seconds 0 --submit
bin/valp wait TASK-001 --workspace /path/to/workspace \
  --timeout 300 --execution-timeout 3600
```

The zero evidence-wait window makes the packaged HERDR call submission-only: it returns
after runtime delivery proof and does not wait for expected evidence. The generated
wait policy still carries the exact expected refs, and `valp wait` owns later
evidence observation and the completion receipt.

Keep this command or runtime subscription pending while convenient. Do not ask
a Lead Agent to poll status every few seconds: every model turn spends tokens.
The `valp wait` process performs local receipt/evidence observation without
model calls. Its `--timeout` is only the current observation window: expiry
returns `waiting`, leaves the worker running, and preserves the suspension so a
later receipt can wake the coordinator. The first wait that creates a
suspension also requires `--execution-timeout`; this records the protocol
deadline once. Later wait calls reattach to the same suspension and reuse that
deadline, so they need only a new observation `--timeout`.

The runtime process resumes only for the final qualifying dependency-ready
barrier receipt or an exception short circuit: a matching blocked work item,
an independently established execution deadline, runtime failure,
cancellation, or explicit user input. Intermediate completions, unrelated
terminal receipts, and an elapsed CLI observation window do not resume the
task. Another runtime or user-facing surface can wake it explicitly with:

```bash
bin/valp resume TASK-001 --workspace /path/to/workspace --event user_input --ref evidence/wake-requests/user-input.json
```

The `--ref` file must be a closed task-local `valp-exception-wake.v1` artifact
bound to the current task, suspension id, epoch, event, principal, and reason;
see `examples/exception-wake.json` for the shape.

If the protocol execution deadline already produced an accepted timeout wake,
a later identity-bound completion uses the receipt ledger instead:

```bash
bin/valp resume TASK-001 --workspace /path/to/workspace \
  --event receipt --ref dispatch-receipts.jsonl#<line>
```

This recovery preserves the timeout wake and fails closed unless the completion
matches the timed-out work item, valid evidence, and original concrete runtime
submission.

Suspension is non-terminal. It does not satisfy evidence, review, approval,
recommendation-resolution, synthesis, or audit gates.

### 9. Verify, Review, Record

A task is done only when:

```text
runtime adapter and routing are recorded
user-selected Leader declaration and VALP validation agree
Leader-declared Agent context policies are recorded
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
3. Let matching requests publish only or refresh capability facts.
4. Require an explicit user-selected Leader and Leader-authored declaration.
5. Validate, then dispatch only when runtime preflight and approval gates allow it.
6. Require a final report and `valp audit` before Done.
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
    "default_action": "publish_only",
    "high_risk_action": "block_for_approval"
  }
}
```

Auto Visible Mode should write:

```text
.herdr-loop/tasks/<task-id>/trigger-policy.json
.herdr-loop/tasks/<task-id>/automation-policy.json
```

Those files record why VALP started, which rule matched, risk classification,
how far automation may proceed, whether approval is required, and where the user
can inspect routing, skill recommendations, dispatch receipts, final report, and
audit evidence.

Do not use Auto Visible Mode as a hidden Agent selector or autopilot. It cannot
choose the Leader, author assignments, or replace a blocked Agent. High-risk
actions still require explicit user approval before execution.
