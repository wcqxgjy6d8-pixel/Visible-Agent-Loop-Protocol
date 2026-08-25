# Troubleshooting

This page covers common first-run failures.

## `preflight` says no pane or session was reported

For HERDR/pane-controller runtimes:

1. Run `herdr status`.
2. Run `herdr pane list`.
3. Confirm the installed HERDR exposes `herdr workspace create`, `herdr agent
   start`, and `herdr pane move`.
4. Rerun the submitted dispatch so VALP can provision the task-owned worker.

For non-pane runtimes, do not fake pane fields. Implement an adapter record with
the equivalent queue id, worker id, hosted run id, output ref, and expected
evidence refs.

## `agent_start_failed` says no viable command was found in `PATH`

The HERDR daemon may have a narrower `PATH` than the coordinator. Current VALP
resolves bare launch commands to absolute executable paths before provisioning.
If this error persists, inspect the selected Agent's `runtime.launch_argv` and
confirm its first item is executable from the coordinator. Do not reuse an
unbound user pane as a workaround. VALP intentionally has no built-in table of
preferred Agent commands; first-install discovery or explicit adapter
configuration must supply the entrypoint.

## `task-owned session ... conflicts`

Inspect `agent-sessions.json`, `agent-session-receipts.jsonl`,
`runtime-preflight.json`, and `herdr pane list`. This error means a recorded pane
or worker identity is still present but no longer matches the accepted terminal,
workspace/tab, Agent, launch, or project context. Do not rename or reuse an
unrelated user pane to satisfy the check. Preserve the evidence and repair the
runtime/session state explicitly; VALP will not overwrite a live identity
conflict.

## `runtime dispatch failure` blocks a retry

Rerun the same `valp dispatch ... --submit` command after repairing the HERDR
session. The packaged adapter runs a fresh preflight and may retry the same
dependency-ready work item once. It does not reopen other blocked states.

An older in-progress task may show `runtime session provisioning failure`.
That is the same bounded retry state; current VALP records new provisioning
failures under `runtime dispatch failure`.

If that retry also fails, the budget records `runtime dispatch retry exhausted`.
Stop and inspect `runtime-preflight.json`, `timeline.jsonl`, and the target pane;
another automatic dispatch is intentionally blocked.

If a runtime keeps the outer Agent idle while child work runs, inspect the
runtime manually and request an adapter update that exports a structured,
dispatch-bound child-job identity. Do not convert visible counters, labels, or
pane text into submission proof.

## `dispatch` says `no ready phase` after a proven submission

First confirm that the exact work item has one concrete `dispatch_submitted`
receipt and no `dispatch_completed` or `dispatch_blocked` receipt. Run the
bounded public recovery:

```bash
valp dispatch <task-id> --workspace <root> \
  --agent <agent> --role <role> --runtime herdr \
  --recover-incomplete --retry-generation 1 --submit
```

If all expected refs arrived after the observer stopped, the command appends the
completion for the original submission without preflight or resubmission. If
all refs are still absent or invalid, prepare a fresh HERDR worker session; the
same command performs at most one bounded resubmission. A partial evidence set,
changed task or dispatch identity, changed control contract or slice, repeat or
second-generation retry, and failed recovery transport all fail closed before
another submission. Do not delete or edit the original receipt to make the
frontier ready.

## `dispatch_blocked`: expected evidence did not appear

This means VALP could not prove completion.

Check:

- the dispatch was actually submitted, not only inserted;
- the agent wrote the exact expected evidence path;
- the latest receipt for that agent is not `dispatch_blocked`;
- `evidence-status.json` does not mark the evidence invalid, superseded,
  rejected, or blocked.

If late evidence appears after a timeout, append a newer `dispatch_completed`
receipt that points to the recovered evidence.

Then apply the explicit recovery transition using that receipt's ledger line:

```bash
valp resume <task-id> --workspace <root> \
  --event receipt --ref dispatch-receipts.jsonl#<line>
```

The command fails closed unless the late completion matches the timed-out work
item and binds to its original concrete submission proof. It does not rewrite
the accepted timeout wake.

## `task-skill-router` not found

Skill recommendation is optional evidence. If no local recommender is installed,
record `status: unavailable` and continue with explicit capability routing.

If a recommender exists but fails, record `status: failed`; the reference audit
reports this as a warning. Do not let a failed recommender grant permissions or
hide missing skills.

## Manual Mode audit fails on receipts

Manual Mode should use manual labels:

```text
manual_dispatch_written
manual_delivery_attested
manual_result_attested
manual_blocked
```

`manual_result_attested` can satisfy a Manual Mode evidence trail when expected
evidence exists. It is not Full Mode `dispatch_submitted` proof.

## Runtime says completed, but `valp audit` fails

This is expected when the runtime completed a job but VALP evidence is missing.

VALP completion requires:

- receipt gates;
- expected evidence;
- verification or scoped blocker;
- review gate;
- approval resolution;
- final synthesis.
