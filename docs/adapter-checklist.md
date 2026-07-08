# Runtime Adapter Checklist

Use this checklist when evaluating whether a runtime can implement VALP.

The question is not "can this runtime run an agent?" The question is whether it
can export enough evidence for another person, agent, or CI job to audit the
work.

## 1. Classify The Adapter

Choose the closest shape:

| Adapter class | Typical runtime | Mode |
|---|---|---|
| pane controller | terminal panes or browser-controlled panes | Full Mode if proof is exported |
| daemon queue | local job queue or runner daemon | Full Mode if state and evidence are exported |
| hosted/local platform | web board plus agent workers | Full Mode if audit data is accessible |
| remote SSH | runtime state lives on another host | Remote Mode |
| manual | human copies prompts and results | Manual Mode |

## 2. Required Full Mode Evidence

A Full Mode adapter must export:

- agent list;
- agent metadata and status;
- provider matrix;
- context policy;
- runtime preflight;
- dispatch submission proof;
- runtime task state mapping;
- expected evidence refs;
- receipt ledger;
- failure reason when blocked or failed;
- approval gate status.

If any of these are missing, the adapter may still be useful, but it should not
claim Full Mode yet.

## 3. Dispatch Proof

Check each dispatch step:

- `dispatch_written`: the task-local dispatch file or equivalent exists.
- `dispatch_inserted`: text was inserted into an input surface, if applicable.
- `dispatch_submitted`: the runtime proves the message/job was submitted.
- `dispatch_completed`: expected evidence exists after execution.
- `dispatch_blocked`: delivery or completion could not be proven.

Text inserted into a pane is not enough. Queue accepted is not enough. Hosted
job completed is not enough. The adapter must connect runtime state to expected
evidence.

## 4. Expected Evidence

For each selected agent, record:

- exact expected evidence paths;
- who owns each path;
- whether the evidence exists;
- whether evidence is valid, superseded, rejected, blocked, or invalid;
- whether build/test/runtime claims cite concrete logs or screenshots.

## 5. Preflight

Pane adapters should record:

- pane id;
- foreground cwd, when available;
- terminal size, when available;
- minimum terminal size expected by the agent;
- CLI availability and version probe, when available;
- known TUI/display caveats.

Queue, hosted, or remote adapters should record equivalent job/session facts:

- queue id or run id;
- worker id;
- session status;
- output refs;
- retry state or failure reason;
- expected refs.

Do not fake pane fields for non-pane runtimes.

## 6. Approval And Risk

The adapter must stop or require approval for:

- destructive changes;
- release, publish, upload, submit, or deploy;
- auth, secrets, signing, entitlements, privacy metadata;
- migrations;
- memory, agent config, or MCP config changes;
- private-data export.

Approval evidence must be task-local and visible. A permissive runtime default
does not override VALP approval gates.

## 7. Recommendation Resolution

If selected workers return recommendations:

- collect them in task-local evidence;
- record whether each meaningful recommendation was adopted, merged, bounded,
  converted to follow-up, rejected, or escalated;
- avoid unbounded redispatch loops;
- require user approval when a recommendation expands into high-risk work.

## 8. Minimal Smoke Test

Before claiming compatibility, run a small task through this sequence:

```text
publish task
scan capabilities
route selected agents
write concise dispatch
submit through runtime
record dispatch_submitted proof
wait for worker output
write expected evidence
record dispatch_completed
write final synthesis
run valp audit
```

The task is compatible only when the audit passes for the right reason.

## 9. What To Post For Feedback

In a runtime-adapter discussion, include:

- adapter class;
- runtime or platform name;
- which Full Mode fields are exported today;
- which fields are missing;
- one sample receipt ledger;
- one expected evidence map;
- known failure modes;
- whether the adapter should start as Full Mode, Remote Mode, or Manual Mode.
