# VALP Reference CLI

The reference CLI provides the first local VALP coordinator workflow:

```text
doctor -> user selects Leader -> publish -> Leader declares assignments
  -> route validates -> dispatch -> audit
```

It is intentionally small. It creates task evidence, reads local capability
profiles when present, writes routing and dispatch files, prints Manual Mode
copy instructions, a HERDR packaged-adapter transport plan, or headless queue
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

## Doctor / Publish / Scan / Route

Commission current capability passports:

```bash
bin/valp doctor --workspace /path/to/workspace --json
```

Doctor produces one passport per addressable Agent surface/session. The JSON
includes four capability evidence layers, declared and observed model/provider,
reasoning mode, session identity and TTL, Skills, MCP, permissions, context,
limitations, and role eligibility. Unknown evidence remains explicit.

The user selects the Leader from those facts. Publish then creates the task and
waits for that Leader's declaration:

```bash
bin/valp publish TASK-001 --workspace /path/to/workspace --prompt "Fix the bug and verify it" --runtime auto
```

`--runtime auto` selects a runtime adapter, not an Agent. It is not the same as
Auto Visible Mode. Auto Visible Mode is a trigger policy that may publish a
task, but cannot choose the Leader or task Agents.

The Leader writes a declaration following
`schemas/assignment-declaration.schema.json`, then VALP validates it:

```bash
bin/valp scan --workspace /path/to/workspace --task TASK-001
bin/valp route TASK-001 --workspace /path/to/workspace --runtime auto \
  --assignments /path/to/assignment-declaration.json
```

The declaration must bind the task, explicit user-selected Leader evidence,
every runtime role assignment, and a reason for each assignment. A runtime
`coordinator` assignment is optional; when present, it must name the Leader.
VALP checks current capability, role, active model/session, permission, context,
and independence boundaries. It may pass or block the declaration; it cannot
choose a missing role or substitute another Agent.

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
<workspace>/.herdr-loop/tasks/<task-id>/assignment-declaration.json
<workspace>/.herdr-loop/tasks/<task-id>/assignment-validation.json
<workspace>/.herdr-loop/tasks/<task-id>/routing.json
<workspace>/.herdr-loop/tasks/<task-id>/trigger-policy.json, when Auto Visible Mode is used
<workspace>/.herdr-loop/tasks/<task-id>/automation-policy.json
<workspace>/.herdr-loop/tasks/<task-id>/context-pack.json
<workspace>/.herdr-loop/tasks/<task-id>/skill-recommendations.json
<workspace>/.herdr-loop/tasks/<task-id>/agents/<agent>/dispatch.md
<workspace>/.herdr-loop/tasks/<task-id>/dispatch-receipts.jsonl
```

At this point the receipt state is `dispatch_written`; the work is not complete.
`selected_agents` in these artifacts is the compatibility projection of unique
Leader-assigned runtime Agents, not a VALP selection result. The Leader is not
included unless it also has an explicit runtime role assignment.

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

`valp dispatch --submit` writes `runtime-preflight.json`. For HERDR it also
creates or reuses the task-owned worker recorded by `agent-sessions.json` and
`agent-session-receipts.jsonl`. It stops when provisioning, identity binding,
or a Leader-declared Agent preflight fails.

## Dispatch

Print dispatch instructions:

```bash
bin/valp dispatch TASK-001 --workspace /path/to/workspace
```

For Manual Mode tasks this prints copy instructions and expected evidence refs.
For HERDR-routed tasks it prints the packaged reference-adapter transport plan. For
queue-routed tasks it prints queue enqueue instructions.

The generated `agents/<agent>/dispatch.md` files are concise assignments. They
should contain the task brief, role, boundaries, expected evidence, visible
attention slice, short skill labels, and refs to the full task artifacts. Long
context remains in task-local files and should not be expanded into every worker
prompt.

Submit through the selected reference adapter:

```bash
bin/valp dispatch TASK-001 --workspace /path/to/workspace --runtime herdr --submit
bin/valp dispatch TASK-001 --workspace /path/to/workspace --runtime queue --submit
```

`dispatch --runtime herdr --submit` uses the adapter packaged in `valp_cli` for
each routed agent; no separate `herdr-loop` executable is required. Preflight
selects atomic `herdr agent prompt` when available, otherwise the complete pane
insertion + Enter + working-state fallback. It fails closed before delivery
when neither path is present. The fallback accepts only an identity-bound
structured `working` response. Pane text, labels, counters, and the dispatched
prompt cannot establish submission.

`dispatch --runtime queue --submit` writes task-local queue submission records
and `dispatch_submitted` receipts. It does not mark the task complete; a queue
worker or operator must still produce expected evidence and `dispatch_completed`
receipts.

Without `--submit`, dispatch only renders the selected adapter command. A HERDR
dry run does not provision an owned session, require live model/session
identity, create a runtime blocker, or consume a runtime retry.

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

Audit treats corrupted JSONL ledgers as failures. It also requires expected
evidence refs to be task-relative safe paths; refs that point outside the task
folder are not completion evidence.

## What It Audits

`valp audit` reads a VALP task folder and checks:

```text
task.md
state.json
assignment-declaration.json
assignment-validation.json
routing.json
automation-policy.json
attention-map.json
context-selection.json
context-pack.json
mask-list.json
evidence-board.json
visible-routing.md
dispatch-receipts.jsonl
routing-feedback.json
learning-feedback.json
agents/<agent>/...
evidence/...
agent-recommendations.json
findings/...
approvals/...
```

It does not run agents, mutate project source, submit dispatches, or call a
runtime. It only audits recorded evidence.

For approval checks, `valp audit` reads both `state.json` and task-local
approval ledgers such as `approvals/requested.jsonl` and
`approvals/user-decisions.jsonl`. A stale `approval: not_required` state does
not override an unresolved approval request.

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
audits, and reference adapter probes. It also commissions capability passports;
the full records are returned by `--json` and included in Markdown reports.
`--task <task-id>` also runs an audit for one task folder.

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
| `assignment_authority` | the user-selected Leader declaration, VALP validation, routing, and state agree |
| `runtime_adapter` | runtime adapter and task state mapping are recorded |
| `deterministic_wake` | a v2 suspension or wait-event evidence is replayable; an authored-only wait policy is not a claim |
| `local_overlay` | local overlay inputs are recorded when used |
| `selected_agents_context` | Leader-declared Agents and context policies are recorded |
| `provider_matrix` | provider matrix fields needed for the task are recorded |
| `runtime_preflight` | Full Mode runtime preflight is recorded and Leader-declared Agents have no failing checks |
| `routing_confidence` | routing confidence, missing capabilities, and relevant rejected candidates are recorded |
| `automation_policy` | automation policy records allowed automatic phases, stop conditions, approval behavior, and audit grade |
| `context_pack` | context pack records compact visible worker context with safe evidence refs |
| `skill_recommendations` | skill recommendation backend result is recorded when available |
| `squad_routing` | squad routing evidence is recorded when a squad is used |
| `dispatch_receipts` | dispatch receipts satisfy the required gates; Full/Remote Mode completions require prior runtime submission proof |
| `submission_dependencies` | prerequisite completion physically precedes dependent submission; a later correction generation qualifies only through fixed, identity-bound, valid replacement evidence |
| `expected_evidence` | expected evidence refs exist, are task-relative safe paths, and are not invalid/superseded/rejected/blocked |
| `correction_cycle` | correction cycle evidence is recorded and fixed when work was rejected, retried, blocked, invalid, or superseded |
| `agent_recommendations` | recommendations from Leader-declared Agents are resolved with coordinator scope control |
| `claim_evidence` | runtime/build/test/lint/UI claims, including final synthesis claims, cite command logs, screenshots, receipts, or evidence paths |
| `verification` | verification passed or has a scoped blocker with concrete verification evidence unless verification is explicitly not required |
| `review_findings` | review findings have no unresolved critical/high blockers |
| `approvals` | approvals are resolved, including task-local approval ledgers |
| `final_synthesis` | final synthesis records decisions, disagreements, evidence gaps, and result |
| `routing_feedback` | feedback record is written for non-trivial tasks when supported |
| `learning_feedback` | evidence-backed learning observations and proposed updates are recorded |

## Example Output

```text
VALP audit: PASS
Task: /path/to/Visible-Agent-Loop-Protocol/examples/full-mode-task
Summary: pass=22 warn=0 fail=0
```

Not-applicable gates are still reported as `skip`; their total may change as
the audit adds or refines gates, so public examples do not freeze that count.

## Current Scope

This is a reference audit command, not a full runtime.

It intentionally does not:

- install HERDR;
- submit dispatches;
- infer hidden agent decisions;
- select a Leader or task Agent;
- call external services;
- validate every JSON schema field deeply.

Future CLI work can add deeper schema validation, workspace-wide audits, SARIF
output, and more concrete runtime adapter submitters.
