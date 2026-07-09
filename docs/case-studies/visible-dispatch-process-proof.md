# Visible Dispatch Process Proof

This page records a short public process proof for VALP's live dispatch path.
It responds to a specific credibility gap: the repository should show more than
schemas, fixtures, and written rules.

It is still not a standalone public live Full Mode completion case study. The
clip proves publish and visible dispatch behavior, plus VALP's refusal to treat
missing evidence as done. A full case study must also publish the complete,
sanitized task folder and final audit closure.

<video controls src="../assets/valp-herdr-dispatch-proof-public-55s-20260706.mp4" width="100%">
  <a href="../assets/valp-herdr-dispatch-proof-public-55s-20260706.mp4">Watch the VALP/HERDR visible dispatch proof video.</a>
</video>

Direct video link:
[valp-herdr-dispatch-proof-public-55s-20260706.mp4](../assets/valp-herdr-dispatch-proof-public-55s-20260706.mp4)

## What The Clip Shows

- `valp publish` creates task `VALP-INTEGRITY-AUDIT-20260706`.
- `valp dispatch` prepares selected-agent dispatches.
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

The critique that VALP had more protocol prose than public running evidence was
valid. This video improves that proof path by showing a real VALP/HERDR
publish-and-dispatch run.

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
- routing and selected agents;
- dispatch receipts with runtime submission proof;
- expected evidence files;
- review and verification evidence;
- final synthesis;
- `valp audit` output.
