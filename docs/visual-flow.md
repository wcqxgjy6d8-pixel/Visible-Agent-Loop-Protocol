# Visual Flow

VALP is easiest to inspect as an authority, receipt, and evidence timeline.
The runtime may be HERDR, a queue, a hosted platform, a remote host, or a
manual handoff, but the task is not done until the expected evidence,
independent review, recommendation, approval, synthesis, feedback, and audit
gates close.

```mermaid
sequenceDiagram
    participant User
    participant Doctor
    participant Leader
    participant VALP as VALP CLI / Validator
    participant Adapter as Runtime Adapter
    participant Worker as Leader-declared Worker
    participant Reviewer as Independent Reviewer
    participant Evidence as Task Evidence Folder
    participant Audit as valp audit
    participant Graph as Task Graph

    User->>Doctor: inspect installation
    Doctor-->>User: current capability passports per Agent session
    User->>VALP: explicitly select Installation Leader
    VALP->>Leader: activate exact session and fenced epoch
    User->>VALP: publish task
    VALP->>Evidence: task.md, state.json
    Leader->>Evidence: declare WorkItems and role-to-Agent assignments
    VALP->>VALP: validate capability, context, skill, permission, evidence contracts
    VALP->>Evidence: assignment-validation.json
    VALP->>Adapter: preflight and bind Worker sessions
    Adapter-->>VALP: identity-bound job/session readiness
    VALP->>Evidence: routing.json, visible-routing.md
    VALP->>Evidence: dispatch_written receipt
    VALP->>Adapter: visible dispatch payload
    Adapter->>Worker: submit dispatch
    Adapter-->>VALP: identity-bound submission proof
    VALP->>Evidence: dispatch_submitted receipt
    Worker->>Evidence: expected evidence files
    Adapter-->>VALP: terminal observation bound to same attempt
    VALP->>Evidence: dispatch_completed receipt
    VALP->>Evidence: verification results
    VALP->>Reviewer: exact-artifact review request
    Reviewer->>Evidence: review and recommendations
    alt accepted recommendation or failed verification
        Leader->>Worker: bounded fix / redispatch
        Worker->>Evidence: corrected evidence
        Reviewer->>Evidence: independent re-review
    end
    Leader->>Evidence: recommendation resolution
    User->>Evidence: approval when policy requires it
    Leader->>Evidence: final synthesis and learning feedback
    User->>Audit: run audit
    Audit->>Evidence: check authority, receipts, evidence, review, approval, synthesis
    alt audit has failures
        Audit-->>User: FAIL; Done stays closed
    else audit has no failures
        Audit-->>User: PASS / WARN; explain warnings and apply lifecycle gates
    end
    VALP-->>User: separately gated task state: Done / Blocked / Failed
    Audit-->>Graph: optional read-only single-task projection
    Graph-->>User: receipts + evidence + audit summary
```

The Worker is an external task/project-owned Agent session, not the protocol
kernel. The adapter proves transport and runtime state; it does not decide that
the task is Done. The Task Graph is downstream of the task ledger and audit. It
cannot write evidence or feed authority back into the protocol.

Neo4j is not part of the current candidate. A future version may use it as an
optional Ontology projection across tasks, Agents, or Skills, but never as
protocol truth or audit authority.

## Evidence Map

```text
.herdr-loop/tasks/<task-id>/
  task.md
  state.json
  assignment-declaration.json
  assignment-validation.json
  routing.json
  visible-routing.md
  dispatch-receipts.jsonl
  agents/<agent>/dispatch.md
  agents/<agent>/<expected-output>.md
  evidence/verification.md
  agent-recommendations.json
  final-synthesis.md
  learning-feedback.json
  task-graph/                # optional projection
```

## Reading The Timeline

- `dispatch_written` means the task file exists and was surfaced.
- `dispatch_inserted` means text entered a runtime surface, but may not have
  been submitted.
- `dispatch_submitted` requires runtime submission proof.
- `dispatch_completed` requires expected evidence after submission proof.
- A runtime "completed" state is advisory until VALP evidence gates pass.
- Audit reports `PASS` / `WARN` / `FAIL`; task lifecycle states remain a
  separate gated state-machine concern.
- A Task Graph displays existing truth; it cannot make an audit pass.
