# Troubleshooting

This page covers common first-run failures.

## `preflight` says no pane or session was reported

For HERDR/pane-controller runtimes:

1. Run `herdr status`.
2. Run `herdr pane list`.
3. Start or attach the agent session.
4. Rerun `bin/valp preflight --agent <agent>`.

For non-pane runtimes, do not fake pane fields. Implement an adapter record with
the equivalent queue id, worker id, hosted run id, output ref, and expected
evidence refs.

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
