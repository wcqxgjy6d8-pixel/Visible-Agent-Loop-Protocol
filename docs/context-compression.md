# Context Compression

Long context is a reliability risk.

Context policy must be scanned before dispatch, not discovered after a task is
already deep into execution.

## Context Policy Fields

```json
{
  "soft_warning_pct": 55,
  "hard_compression_pct": 65,
  "emergency_stop_pct": 80,
  "checkpoint_interval_minutes": 45,
  "checkpoint_after_phase": true,
  "checkpoint_after_fix_review_rounds": 2,
  "compression_target_pct_min": 15,
  "compression_target_pct_max": 25,
  "fallback_triggers": [
    "user_interruption_resume",
    "wrong_project_or_tool_target_confusion",
    "two_fix_review_rounds",
    "before_new_roadmap_task_after_long_task"
  ]
}
```

## Default Thresholds

| Role | Soft warning | Hard compression | Emergency stop |
|---|---:|---:|---:|
| coordinator | 50% | 60% | 80% |
| implementer | 55% | 65% | 80% |
| reviewer | 60% | 70% | 80% |
| prototype | 60% | 70% | 80% |
| other | 60% | 70% | 80% |

## Meaning

Soft warning:
: Finish the current small operation and prepare compression notes.

Hard compression:
: Stop new implementation, routing, review, or voting. Write a handoff before
continuing.

Emergency stop:
: Do not run commands, edit files, send dispatches, or issue final verdicts
until compression is complete and state is revalidated.

## Dispatch Gate

Context policy is not passive metadata. Before dispatch, the router or runtime
adapter must check whether the selected agent is already at or above
`hard_compression_pct` or explicitly marked `compression_required`.

If so:

```text
do not send the dispatch
request/write context compression handoff
re-read task state and evidence
rerun routing if the task, project, or tools changed
```

## Dispatch Payload Budget

The coordinator or leader must keep worker dispatches short. A good dispatch is
not a transcript; it is a precise assignment with file refs.

The complete generated dispatch has strict role-specific ceilings:

| Primary role | Max characters | Max reference tokens |
|---|---:|---:|
| coordinator | 3000 | 750 |
| implementer | 2800 | 700 |
| reviewer | 2400 | 600 |
| prototype or researcher | 2400 | 600 |
| other | 2200 | 550 |

The portable reference-token estimator is `ceil(chars / 4)`. It is a
deterministic budget proxy, not a provider-tokenizer claim. Adapters with exact
tokenizers enforce the lower limit. `routing.json` records configured and
actual dispatch sizes; `context-pack.json` records each selected agent's role
budget.

Dispatch should contain:

- short task brief;
- role and permission boundary;
- expected evidence paths;
- visible attention slice;
- compact context pack summary;
- short skill recommendation labels;
- refs to `task.md`, `automation-policy.json`, `routing.json`,
  `context-selection.json`, `context-pack.json`, `mask-list.json`,
  `evidence-board.json`, and `skill-recommendations.json`.

Only `task.md`, `context-pack.json`, and `skill-recommendations.json` need to be
in the starting worker prompt. Other task-local refs can be named as on-demand
progressive disclosure. Coordinator, implementer, reviewer, prototype, and
researcher prompts should receive only their role-specific evidence and
attention slice.

Dispatch should not paste:

- the full conversation;
- full task history when `task.md` is available;
- long repeated skill-router task text;
- stale memory without evidence.
- raw private transcript or broad operator preference data that is not selected
  into `context-pack.json`.

HTML can render dashboards or reports, but the canonical worker dispatch should
remain readable plain text or Markdown unless the runtime exports the same
concise assignment and receipt evidence.

Measure the checked-in legacy baseline against the current generator with:

```bash
python3 scripts/benchmark-dispatch-size.py
```

The benchmark reports only Unicode character counts and percentage reduction.

## Required Handoff

```markdown
## Context Compression

Active project:
Active task:
My role:
Current phase:
Completed:
Not completed:
Evidence paths:
Known blockers:
Next safe action:
Do not use / stale context:
```

## Fallback Triggers

If exact context percentage is not available, force compression after:

- user interruption followed by resume;
- two visible implementation/review/fix loops;
- wrong project, tool target, tab, path, or phase confusion;
- starting a new roadmap task after a long task;
- inability to state project root, task id, phase, and next safe action without
  rereading files.
