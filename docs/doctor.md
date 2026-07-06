# VALP Doctor

`valp doctor` diagnoses a VALP protocol checkout without mutating it by default.

It is a health check, not a repair command and not a replacement for
`valp audit`.

For first installs, Doctor should run before any real agent dispatch. An App or
installer can use it as the first visible environment check after resolving the
actual install root and CLI path.

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

Doctor checks:

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

First-install App flows should combine Doctor with runtime preflight:

```text
resolve install root and CLI path
  -> run doctor on the protocol checkout
  -> run runtime preflight for Full Mode
  -> run publish/dispatch dry run
  -> ask the user before real --submit, policy_auto, or watcher mode
```

Doctor success means the protocol checkout and reference checks are healthy. It
does not mean a live runtime task has completed.

## Status

| Status | Meaning |
|---|---|
| `pass` | The check is healthy |
| `warn` | The workspace is usable, but there is residue, missing optional runtime support, or another advisory issue |
| `fail` | The workspace has a broken required check, such as dirty git state, bad JSON, or failing example audit |

Warnings do not prove the protocol is broken. For example, HERDR can be missing
on a machine that only uses Manual Mode or a queue adapter.

## Boundaries

Doctor must not:

- run `git reset`;
- delete task evidence;
- rewrite receipts;
- create `dispatch_completed` events;
- bypass approval gates;
- submit, publish, deploy, release, upload, or fetch from the network;
- treat a runtime's internal "completed" state as VALP completion.

Use `valp audit` for task evidence gates. Use code review and verification
evidence for semantic correctness. Doctor only reports workspace health and
likely next actions.
