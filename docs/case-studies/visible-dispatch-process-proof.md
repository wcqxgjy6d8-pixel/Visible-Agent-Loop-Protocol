# Visible Dispatch Process Evidence

> Historical process evidence from 2026-07-06. The original local recording
> was removed from the publication tree because it exposed machine-specific
> paths, runtime identifiers, and provider details. The sanitized ledger excerpt
> below preserves only the provider-neutral receipt semantics.

This page records a sanitized process-evidence excerpt for VALP's dispatch path.

It is not a standalone public live Full Mode completion case study. A full case
study must publish a complete sanitized task folder, runtime submission proof,
and final audit closure without local operator data.

## What The Historical Run Showed

- `valp publish` creates task `VALP-INTEGRITY-AUDIT-20260706`.
- The legacy runtime prepares dispatches for its then-selected Agents.
- HERDR receives a visible worker brief for the `hermes` pane.
- VALP records missing expected evidence as blocked instead of done.
- Later panes show agent evidence being produced.

## Machine Evidence Shape

The source task recorded these machine-checkable events in its task-local
ledger. This is a sanitized excerpt, not the full private machine log:

```jsonl
{"agent":"hermes","event":"dispatch_inserted","expected_refs":["agents/hermes/self-review.md"]}
{"agent":"hermes","event":"dispatch_submitted","summary":"Dispatch submitted and proof observed"}
{"agent":"hermes","event":"dispatch_blocked","summary":"Dispatch submitted but expected evidence did not appear before timeout"}
{"agent":"agy","event":"dispatch_submitted","summary":"Dispatch submitted and proof observed"}
{"agent":"agy","event":"dispatch_completed","proof":{"completion_basis":"expected evidence appeared after prior runtime submission proof"}}
{"agent":"claude","event":"dispatch_completed","proof":{"completion_basis":"expected evidence exists after prior dispatch_submitted proof and visible steer"}}
```

The important behavior is not that every worker completed immediately. The
important behavior is that VALP distinguishes:

- dispatch text written;
- dispatch actually submitted to a runtime;
- runtime or worker activity;
- expected evidence missing;
- expected evidence later present;
- completion only after evidence exists.

## What This Answers

This excerpt demonstrates the receipt-state distinction without publishing the
operator's local recording. It is historical supporting evidence, not current
runtime or platform conformance proof.

The critique that HERDR is an external reference runtime is also valid as an
ecosystem risk. The protocol remains runtime-neutral, but the repository still
needs a first-class non-HERDR adapter before it can prove multi-runtime
automation.

The critique that VALP may overlap with CI and code review needs a narrower
answer. CI can prove tests or checks passed. Code review can judge a diff. VALP
tracks whether agent work was dispatched, submitted, evidenced, reviewed,
approved when needed, and synthesized before it is called done.

## What It Still Does Not Prove

- It is not a clean end-to-end Full Mode completion case study by itself.
- It does not prove production reliability.
- It does not prove a non-HERDR runtime adapter.
- It does not remove the need for the bundled audits and schemas.
- It should not be marketed as "production-ready" or as a hosted platform.

## Next Credibility Step

The next stronger artifact should be a sanitized full task folder for a live
Full Mode run, with:

- preflight output;
- the user-selected Leader and Leader-declared assignments;
- dispatch receipts with runtime submission proof;
- expected evidence files;
- review and verification evidence;
- final synthesis;
- `valp audit` output.
