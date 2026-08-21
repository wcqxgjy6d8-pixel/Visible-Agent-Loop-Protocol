# Visible Dispatch Process Evidence

> The original local recording from 2026-07-06 was removed from the publication
> tree because it exposed machine-specific paths, runtime identifiers, and
> provider details. The replacement public asset is a Chinese animated GIF;
> this page keeps the machine-checkable audit evidence in text and commands.

This page records a sanitized process-evidence excerpt for VALP's dispatch path.

![VALP 0.3 open-core evidence flow](../assets/valp-v03-open-core-overview.gif)

The GIF explains the public control path at a glance. It is not live-runtime
proof; the commands and fixture outputs below are the machine-checkable
evidence.

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

## Artifact Boundary

This artifact proves the visible dispatch receipt distinction. It is not used
as evidence for broader claims. End-to-end Full Mode completion, production
reliability, non-HERDR runtime automation, and hosted platform operation each
require a separate evidence package with the corresponding task folder,
receipts, expected evidence, review, final synthesis, and `fail_count=0` audit.

## Complete Full Mode Evidence Package

A complete public Full Mode case study should publish a sanitized task folder
or equivalent artifact set containing:

- preflight output;
- the user-selected Leader and Leader-declared assignments;
- dispatch receipts with runtime submission proof;
- expected evidence files;
- review and verification evidence;
- final synthesis;
- `valp audit` output.

If the claim includes automatic coordinator continuation, the package must also
include the provider-consumed continuation ledger through `resume_consumed`. A
cross-restart claim additionally requires an injected post-consumption,
pre-receipt crash followed by restart reconciliation and duplicate-suppression
evidence.
