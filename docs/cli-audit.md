# VALP Reference CLI

The reference CLI provides the first local VALP coordinator workflow:

```text
publish -> scan -> route -> dispatch -> audit
```

It is intentionally small. It creates task evidence, reads local capability
profiles when present, writes routing and dispatch files, prints Manual Mode
copy instructions, HERDR reference-adapter submit commands, or headless queue
reference records, and audits
completion evidence.

The CLI is not the whole protocol. It provides small reference helpers for
manual, HERDR pane-controller, and synthetic headless queue adapter shapes.
Production runtimes should implement the adapter evidence contract in
`docs/runtime-adapters.md`.

Print the reference CLI version:

```bash
bin/valp --version
```

## Publish / Scan / Route

Publish a task and auto-route it:

```bash
bin/valp publish TASK-001 --workspace /path/to/workspace --prompt "Fix the bug and verify it" --runtime auto
```

`--runtime auto` selects a runtime adapter. It is not the same as Auto Visible
Mode. Auto Visible Mode is a trigger policy that decides whether a user request
or runtime signal should publish a VALP task before routing starts.

Publish without routing:

```bash
bin/valp publish TASK-001 --workspace /path/to/workspace --prompt "..." --no-route
```

Run scan and route explicitly:

```bash
bin/valp scan --workspace /path/to/workspace --task TASK-001
bin/valp route TASK-001 --workspace /path/to/workspace --runtime auto
```

The local scan reads:

```text
$VALP_CAPABILITIES_FILE
<workspace>/.valp/agents/capabilities.json
~/.valp/agent-capabilities.json
~/.herdr/agent-capabilities.json

$VALP_LOCAL_OVERLAY_FILE
<workspace>/.valp/local-overlay.json
~/.valp/local-overlay.json
~/.herdr/valp-local-overlay.json
```

The `~/.herdr` files are compatibility fallbacks for the HERDR reference
runtime. They are not protocol defaults.

and writes:

```text
<workspace>/.herdr-loop/agents/capabilities.json
<workspace>/.herdr-loop/local-overlay.json
```

Routing writes:

```text
<workspace>/.herdr-loop/tasks/<task-id>/routing.json
<workspace>/.herdr-loop/tasks/<task-id>/trigger-policy.json, when Auto Visible Mode is used
<workspace>/.herdr-loop/tasks/<task-id>/skill-recommendations.json
<workspace>/.herdr-loop/tasks/<task-id>/agents/<agent>/dispatch.md
<workspace>/.herdr-loop/tasks/<task-id>/dispatch-receipts.jsonl
```

At this point the receipt state is `dispatch_written`; the work is not complete.

## Preflight

Check runtime readiness before dispatch:

```bash
bin/valp preflight --runtime herdr --agent agy
bin/valp preflight --runtime queue --agent codex --agent claude --json
```

Pane-based adapters should record:

```text
pane id
agent status
terminal size
minimum terminal size
CLI version probe
restart/update-needed status
```

Queue or hosted adapters should record job/session facts such as queue id,
worker id, session status, output refs, and expected refs. They should not fake
pane or terminal-size fields.

`valp dispatch --submit` writes `runtime-preflight.json` and stops when a
selected agent has a failing preflight check.

## Dispatch

Print dispatch instructions:

```bash
bin/valp dispatch TASK-001 --workspace /path/to/workspace
```

For Manual Mode tasks this prints copy instructions and expected evidence refs.
For HERDR-routed tasks it prints HERDR reference-adapter submit commands. For
queue-routed tasks it prints queue enqueue instructions.

Submit through the selected reference adapter:

```bash
bin/valp dispatch TASK-001 --workspace /path/to/workspace --runtime herdr --submit
bin/valp dispatch TASK-001 --workspace /path/to/workspace --runtime queue --submit
```

`dispatch --runtime herdr --submit` calls `herdr-loop submit-dispatch` for each
routed agent. It should only be used when the local HERDR panes/runtime are
ready.

`dispatch --runtime queue --submit` writes task-local queue submission records
and `dispatch_submitted` receipts. It does not mark the task complete; a queue
worker or operator must still produce expected evidence and `dispatch_completed`
receipts.

Manual Mode tasks cannot use `--submit`; copy dispatches manually and record
manual attestation receipts when evidence exists.

For runtimes beyond these reference helpers, do not post-process printed
commands as protocol truth. Implement an adapter that exports equivalent
dispatch receipts, state mapping, expected evidence refs, and final synthesis
evidence.

## Audit

`valp audit` turns the `SPEC.md` Done Criteria checklist into an executable
quality gate for a task evidence folder.

When a task was started by Auto Visible Mode, the trigger record is part of the
human explanation for why the task exists. It does not replace runtime
preflight, dispatch receipts, expected evidence, approval resolution, review, or
final synthesis.

## What It Audits

`valp audit` reads a VALP task folder and checks:

```text
task.md
state.json
routing.json
attention-map.json
context-selection.json
mask-list.json
evidence-board.json
visible-routing.md
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

Audit the minimal no-runtime example:

```bash
bin/valp audit examples/minimal-task
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

## Doctor

`valp doctor` diagnoses a VALP protocol checkout without changing files by
default:

```bash
bin/valp doctor --workspace .
bin/valp doctor --workspace . --json
bin/valp doctor --workspace . --report ./valp-doctor-report.md
bin/valp doctor --workspace . --report desktop
```

Doctor checks local git tracking status and cleanliness, ignored residue, the
`bin/valp` entrypoint, Python availability, JSON/JSONL syntax, bundled example
audits, and reference adapter probes. `--task <task-id>` also runs an audit for
one task folder.

Doctor is diagnostic. It does not submit dispatches, rewrite receipts, delete
task evidence, fetch from the network, or replace `valp audit`. Markdown reports
are written only when `--report` is passed, and the target file is overwritten
if it already exists.

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
| `runtime_preflight` | Full Mode runtime preflight is recorded and selected agents have no failing checks |
| `routing_confidence` | routing confidence, missing capabilities, and relevant rejected candidates are recorded |
| `skill_recommendations` | skill recommendation backend result is recorded when available |
| `squad_routing` | squad routing evidence is recorded when a squad is used |
| `dispatch_receipts` | dispatch receipts satisfy the required gates |
| `expected_evidence` | expected evidence exists and is not invalid/superseded/rejected/blocked |
| `claim_evidence` | runtime/build/test/lint/UI claims cite command logs, screenshots, receipts, or evidence paths |
| `verification` | verification passed or has a scoped blocker |
| `review_findings` | review findings have no unresolved critical/high blockers |
| `approvals` | approvals are resolved |
| `final_synthesis` | final synthesis records decisions, disagreements, evidence gaps, and result |
| `routing_feedback` | feedback record is written for non-trivial tasks when supported |

## Example Output

```text
VALP audit: PASS
Task: /path/to/Visible-Agent-Loop-Protocol/examples/full-mode-task
Summary: pass=17 warn=0 fail=0 skip=1
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

Future CLI work can add deeper schema validation, workspace-wide audits, SARIF
output, and more concrete runtime adapter submitters.
