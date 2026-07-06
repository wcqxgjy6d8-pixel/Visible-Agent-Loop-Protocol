# Visual Flow

VALP is easiest to inspect as a receipt-and-evidence timeline. The runtime may
be HERDR, a queue, a hosted platform, a remote host, or a manual handoff, but
the task is not done until the expected evidence and review gates exist.

```mermaid
sequenceDiagram
    participant User
    participant VALP as VALP CLI / Coordinator
    participant Runtime as Runtime Adapter
    participant Agent
    participant Evidence as Task Evidence Folder
    participant Audit as valp audit

    User->>VALP: publish task
    VALP->>Evidence: task.md, state.json
    VALP->>VALP: scan capabilities, context, skills
    VALP->>Runtime: preflight selected agents
    Runtime-->>VALP: pane/job/session readiness
    VALP->>Evidence: routing.json, visible-routing.md
    VALP->>Agent: visible dispatch
    VALP->>Evidence: dispatch_written receipt
    Runtime-->>VALP: submission proof
    VALP->>Evidence: dispatch_submitted receipt
    Agent->>Evidence: expected evidence files
    VALP->>Evidence: dispatch_completed receipt
    VALP->>Evidence: review, verification, approvals
    User->>Audit: run audit
    Audit->>Evidence: check receipts and gates
    Audit-->>User: PASS / WARN / FAIL
```

## Evidence Map

```text
.herdr-loop/tasks/<task-id>/
  task.md
  state.json
  routing.json
  visible-routing.md
  dispatch-receipts.jsonl
  agents/<agent>/dispatch.md
  agents/<agent>/<expected-output>.md
  evidence/verification.md
  final-synthesis.md
```

## Reading The Timeline

- `dispatch_written` means the task file exists and was surfaced.
- `dispatch_inserted` means text entered a runtime surface, but may not have
  been submitted.
- `dispatch_submitted` requires runtime submission proof.
- `dispatch_completed` requires expected evidence after submission proof.
- A runtime "completed" state is advisory until VALP evidence gates pass.
