# VALP, HERDR, Hermes MoA, And Multica

This document defines scope boundaries. It is not a ranking.

## Summary

| Name | Layer | What it is |
|---|---|---|
| VALP | Protocol | Open rules for visible, evidence-backed multi-agent collaboration |
| HERDR | Runtime | Reference runtime for Full Mode automation |
| Hermes MoA | Model provider pattern | Model-level reference fan-out plus one acting aggregator model |
| Multica | Platform | Managed agents platform with web UI, daemon, issue/task model, skills, and squads |

## VALP

VALP defines:

```text
task lifecycle
runtime adapter contract
provider matrix
context policy
visible dispatch
dispatch receipts
evidence gates
review/fix/review
approval gates
final synthesis
```

VALP is not a hosted product and does not require a specific model provider,
terminal emulator, database, or operating system.

## HERDR

HERDR is the reference runtime for Full Mode.

In VALP terms, HERDR is expected to provide runtime capabilities such as:

```text
agent list
agent status/read
agent send/insert
pane or message submit
submission proof
status wait
receipt ledger
evidence store
```

HERDR is not the protocol itself.

## Hermes MoA And Delegation

Hermes Mixture of Agents (MoA) is a model-level aggregation pattern, not a VALP
task-completion protocol. MoA can improve one acting model's reasoning by
running reference models first and giving their advice to an aggregator. The
aggregator remains the acting model that produces the assistant response and
tool calls.

In VALP terms:

```text
MoA improves one acting model's reasoning.
VALP governs multi-agent task execution.
```

MoA traces can be useful evidence because they may show what reference models
saw, what they advised, and what the aggregator received. They are not
completion proof by themselves. VALP completion still requires dispatch
receipts, expected evidence, verification, review, approval resolution when
needed, final synthesis, and audit.

Hermes delegation can be a VALP runtime adapter only when the adapter exports
the required evidence contract:

```text
dispatch submission proof
expected evidence refs
runtime task state mapping
failure state and failure reason
receipt ledger or equivalent audit records
review and approval gate status
```

Hidden model consensus, private advisor traces, or subagent summaries are not
enough to claim VALP Full Mode completion.

## Multica

Multica is a managed agents platform. Its public repository describes a system
with web UI, Go backend, PostgreSQL, local daemon, issues, comments, task queue,
skills, runtimes, squads, and autopilots.

In VALP terms, a Multica-like system is a hosted/local platform runtime shape.
It can be VALP-compatible only if it exports the required adapter evidence:

```text
runtime task state mapping
dispatch submission proof
provider matrix
context policy
expected evidence refs
receipt ledger or equivalent audit records
approval state
failure reason
```

## Key Difference

VALP task completion is stricter than a runtime task ending.

```text
runtime completed != VALP done
```

VALP done requires expected evidence, verification, review, approval resolution,
and final synthesis.

## License Difference

VALP is MIT licensed.

Multica's repository license is a modified Apache 2.0 license with commercial
restrictions, according to the license text checked on 2026-07-03.

## Practical Use

Use HERDR, the current reference runtime, when you want the documented Full Mode
automation path. Other runtimes can be VALP-compatible if they implement the
adapter evidence contract.

Use VALP when you need a portable protocol and evidence standard.

Use a managed agents platform when you want a complete product experience with
boards, issues, comments, and hosted/self-hosted platform workflows.
