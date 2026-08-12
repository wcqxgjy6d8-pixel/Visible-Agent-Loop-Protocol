# VALP Doctor

`valp doctor` diagnoses a VALP protocol checkout and commissions capability
passports for every discovered Agent surface and addressable session. It does
not mutate Agent configuration or task assignments.

It is a health check, not a repair command and not a replacement for
`valp audit`.

For first installs, Doctor should run before the user selects a Leader or any
real Agent dispatch occurs. An App or installer can use it as the first visible
environment and capability check after resolving the actual install root and
CLI path.

## Usage

```bash
bin/valp doctor --workspace /path/to/Visible-Agent-Loop-Protocol
bin/valp doctor --workspace /path/to/Visible-Agent-Loop-Protocol --json
bin/valp doctor --workspace /path/to/Visible-Agent-Loop-Protocol --report ./valp-doctor-report.md
bin/valp doctor --workspace /path/to/Visible-Agent-Loop-Protocol --report desktop
bin/valp doctor --workspace /path/to/Visible-Agent-Loop-Protocol --task TASK-001
```

`--report desktop` is an explicit convenience alias. The CLI does not write to a
user's desktop unless that flag is provided.

`--report <path>` creates parent directories when needed and overwrites the
target file if it already exists. Doctor reports may include absolute local
paths, ignored file names, runtime command paths, and git SHAs.

## Checks

Doctor runs checkout and runtime checks:

```text
git HEAD and local upstream tracking ref
git working tree cleanliness
ignored local residue such as caches or local runtime evidence
bin/valp and Python availability
VALP CLI version import
examples/ and schemas/ JSON syntax
examples/ JSONL syntax
bundled task example audits
manual, queue, and HERDR reference adapter probes
optional task audit when --task is provided
```

Doctor also writes one capability passport for every Agent surface/session it
can discover from the local capability registry and runtime adapter. Each
passport keeps four evidence layers separate:

```text
official_claim  -> vendor or project claims with provenance
local_presence  -> installed surface, version, and local source
live_callable   -> current runtime/session probe
task_verified   -> passed task history bound to this exact model and session
```

The commissioned set is the union of registry entries, local overlay profiles,
and runtime-discovered surfaces. Doctor must not silently omit an overlay-only
or live runtime surface merely because the static registry is stale. Facts that
the discovering source did not prove remain `unknown`.

Each passport records:

```text
Agent surface and runtime/session identity
declared model, observed model, provider or relay, and reasoning mode
model observation time, TTL, freshness, mismatch, and session generation
reachable Skills
reachable MCP servers and tools
filesystem, network, shell, and mutation permissions
context policy and current context state
known limitations
Leader, implementer, reviewer, and researcher eligibility
task-verified history bound to the observed model and session
```

Unknown is evidence. Doctor must preserve `unknown`, `unsupported`,
`unavailable`, `stale`, `mismatch`, and session-unbound states instead of
guessing from an Agent product name or configured default. An unknown, stale,
mismatched, or session-unbound model cannot qualify an Agent as a high-risk
implementer or final reviewer.

A configured default is not required. If no model was declared, a fresh,
high-confidence live observation bound to a known session can be authoritative.
If a declaration exists and differs from the observed model, provider, or
reasoning mode, the mismatch still blocks high-risk roles.

Capability passports are inputs to human and Leader judgment. Doctor does not
rank Agents, choose the Leader, write an assignment declaration, or substitute
another Agent when validation blocks one.

First-install flows should combine Doctor with exact Leader startup and runtime
preflight. A CLI is sufficient; an App is optional:

```text
resolve install root and CLI path
  -> run doctor on the protocol checkout
  -> inspect capability passports
  -> user selects the Leader
  -> start and verify the installation-owned Leader session
  -> run runtime preflight for Full Mode
  -> publish the task
  -> Leader writes assignment-declaration.json
  -> validate the declaration and run a dispatch dry run
  -> ask the user before real --submit, policy_auto, or watcher mode
```

Doctor success means the protocol checkout and reference checks are healthy and
that discovered capability facts were recorded. It does not mean the scan found
every possible Agent, that every passport is eligible for every role, or that a
live runtime task has completed.

## Status

| Status | Meaning |
|---|---|
| `pass` | The check is healthy |
| `warn` | The workspace is usable, but there is residue, missing optional runtime support, or another advisory issue |
| `fail` | The workspace has a broken required check, such as dirty git state, bad JSON, or failing example audit |

Warnings do not prove the protocol is broken. For example, HERDR can be missing
on a machine that only uses Manual Mode or a queue adapter.

Missing optional HERDR remains a warning. If HERDR is installed but its
preflight exposes no supported submission transport, the HERDR check fails:
doctor and `valp preflight --runtime herdr` then agree that Full Mode submission
is unavailable.

## Boundaries

Doctor must not:

- run `git reset`;
- delete task evidence;
- rewrite receipts;
- create `dispatch_completed` events;
- bypass approval gates;
- choose a Leader or task Agent;
- write or repair `assignment-declaration.json`;
- infer an observed model from a configured default or another Agent surface;
- submit, publish, deploy, release, upload, or fetch from the network;
- treat a runtime's internal "completed" state as VALP completion.

Use `valp audit` for task evidence gates. Use code review and verification
evidence for semantic correctness. Doctor reports workspace health and
capability passports; the user and user-selected Leader retain assignment
authority.
