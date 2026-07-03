# Quickstart

VALP's normal path is Full Mode: install a compatible runtime, verify it can see
agents, then run tasks with dispatch receipts and evidence gates.

This document avoids promising a VALP CLI that does not exist yet. It shows the
minimum operational path for using the protocol with a Full Mode runtime.

## 1. Pick Your Platform Path

| System | Recommended path | Expected mode |
|---|---|---|
| macOS | HERDR stable installer or Homebrew | Full Mode |
| Linux | HERDR stable installer or package manager | Full Mode |
| Windows stable workflow | SSH into a Linux/macOS HERDR host | Remote Mode with Full Mode guarantees on the remote host |
| Windows local workflow | HERDR Windows preview beta | Beta; verify adapter requirements |
| No runtime | Manual files only | Manual Mode, degraded |

## 2. Install Runtime

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

## 3. Verify Full Mode Capability

Before publishing real work, verify the runtime can provide:

```text
agent list
agent status/read
agent send or insert
pane/message submit
submission proof
status wait
task evidence store
receipt ledger
```

If any required proof is missing, record the gap and either use a lower mode or
fix the adapter.

## 4. Create Task Evidence Folder

Canonical reference layout:

```text
<workspace>/.herdr-loop/tasks/<task-id>/
  task.md
  state.json
  routing.json
  dispatch-receipts.jsonl
  agents/
    codex/
      dispatch.md
      evidence.md
    claude/
      dispatch.md
      review.md
```

The `.herdr-loop` path is the reference runtime-compatible default. Other
runtimes may use another internal store if they export the same evidence
contract.

## 5. Publish Task

Minimum task description:

```markdown
# Task

ID: TASK-001
Profile: software-code
Goal:
Expected evidence:
Approval risks:
```

## 6. Scan And Route

Record:

```text
runtime adapter
provider matrix
context policies
skills and MCP availability
permission boundaries
selected agents
routing reasons
missing capabilities
```

Do not route by habit.

## 7. Dispatch And Require Receipts

Valid dispatch receipt states:

```text
dispatch_written
dispatch_inserted
dispatch_submitted
dispatch_completed
dispatch_blocked
```

Text in an input box is only `dispatch_inserted`. It is not delivery.

If expected evidence is declared, the gate requires `dispatch_completed`.

## 8. Verify, Review, Record

A task is done only when:

```text
runtime adapter and routing are recorded
selected agent context policies are recorded
dispatch receipts satisfy gates
expected evidence exists
verification passed or has a scoped blocker
review has no unresolved critical/high findings
approval gates are resolved
final synthesis is recorded
```

## 9. If Runtime Is Not Available

Use Manual Mode only as a degraded fallback. Manual Mode can preserve task
folders and evidence notes, but it cannot prove automatic dispatch submission,
agent status transitions, or runtime-backed receipts.
