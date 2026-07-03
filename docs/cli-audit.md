# VALP Audit CLI

`valp audit` is the first reference CLI command for VALP 0.2.

It turns the `SPEC.md` Done Criteria checklist into an executable quality gate
for a task evidence folder.

## What It Audits

`valp audit` reads a VALP task folder and checks:

```text
task.md
state.json
routing.json
dispatch-receipts.jsonl
routing-feedback.json
agents/<agent>/...
evidence/...
findings/...
approvals/...
```

It does not run agents, mutate project source, submit dispatches, or call a
runtime. It only audits recorded evidence.

## Usage

Audit a task folder directly:

```bash
bin/valp audit examples/full-mode-task
```

Audit a workspace task:

```bash
bin/valp audit /path/to/workspace --task TASK-001
```

JSON output:

```bash
bin/valp audit examples/full-mode-task --json
```

Strict mode treats warnings as failures:

```bash
bin/valp audit examples/full-mode-task --strict
```

The module entrypoint is also supported:

```bash
python3 -m valp_cli audit examples/full-mode-task
```

## Statuses

| Status | Meaning |
|---|---|
| `pass` | Evidence satisfies the audit item |
| `warn` | Evidence is usable but incomplete or advisory |
| `fail` | Required evidence or gate is missing |
| `skip` | Item is not applicable, such as squad routing when no squad is used |

The command exits with status code `1` when the overall audit status is `fail`.
Warnings do not fail the command unless `--strict` is used.

## Audit Items

The command maps the Done Criteria into these audit items:

| Audit item | Done criteria covered |
|---|---|
| `profile_routing` | profile and routing are recorded |
| `runtime_adapter` | runtime adapter and task state mapping are recorded |
| `local_overlay` | local overlay inputs are recorded when used |
| `selected_agents_context` | selected agents and context policies are recorded |
| `provider_matrix` | provider matrix fields needed for the task are recorded |
| `routing_confidence` | routing confidence, missing capabilities, and relevant rejected candidates are recorded |
| `squad_routing` | squad routing evidence is recorded when a squad is used |
| `dispatch_receipts` | dispatch receipts satisfy the required gates |
| `expected_evidence` | expected evidence exists |
| `verification` | verification passed or has a scoped blocker |
| `review_findings` | review findings have no unresolved critical/high blockers |
| `approvals` | approvals are resolved |
| `final_synthesis` | final synthesis records decisions, disagreements, evidence gaps, and result |
| `routing_feedback` | feedback record is written for non-trivial tasks when supported |

## Example Output

```text
VALP audit: PASS
Task: /path/to/Visible-Agent-Loop-Protocol/examples/full-mode-task
Summary: pass=13 warn=0 fail=0 skip=1
```

The example has one skip because it does not use squad routing.

## Current Scope

This is a reference audit command, not a full runtime.

It intentionally does not:

- install HERDR;
- submit dispatches;
- infer hidden agent decisions;
- call external services;
- validate every JSON schema field deeply.

Future CLI work can add schema validation, workspace-wide audits, SARIF output,
and runtime adapter checks.
