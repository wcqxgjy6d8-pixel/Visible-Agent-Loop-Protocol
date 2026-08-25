# Compound Learning Loop

VALP is not tied to a user's local agents. The protocol is a control loop for
autonomous work.

The first-principles question is:

```text
When an intelligent system claims a task is done, what evidence makes that claim trustworthy?
```

VALP answers with visible intent, routing, execution, evidence, correction,
approval, synthesis, audit, and learning.

## Operating Principles

First-principles evidence:
: A completion claim must point to receipts, files, logs, screenshots, reviews,
approval ledgers, or other concrete evidence. Natural-language confidence is
not enough.

Control-system loop:
: The task has a target, sensors, actuators, feedback, error correction, and
stop conditions. Dispatches are actuators. Receipts and evidence are sensors.
`valp audit` is the controller check.

Accounting ledger:
: Critical states are append-only or task-local records. Dispatch sent,
dispatch submitted, expected evidence, review, approval, synthesis, and
learning all need auditable refs.

Anti-hallucination boundary:
: LLM output is useful reasoning, but it is not completion proof until it
touches external evidence.

Compound engineering:
: Every non-trivial task should improve future tasks. The improvement must be
stored as evidence-backed feedback, not hidden memory.

## Core Artifacts

`automation-policy.json`
: Records what can proceed automatically, what must stop, the risk
classification, approval behavior, stop conditions, and audit grade.

`context-pack.json`
: Gives workers compact, evidence-backed task context without copying private
transcripts or stale memory.

`routing-feedback.json`
: Records the validated assignment outcome: Leader-declared Agents, expected evidence, actual
evidence, blockers, result, lessons, and next routing hints.

`routing-feedback.json` is the routing-facing history consumed by the reference
CLI. Its workspace index is only an index: before a record can influence a new
route, the CLI resolves the task-local record and checks task identity,
completion gates, review, approval, verification, and referenced evidence.
The resulting history is a prior for candidate scoring, never an assignment or
proof of current capability.

`learning-feedback.json`
: Records what the system learned and which protocol, schema, audit, docs,
local overlay, skill, runtime adapter, or memory updates are proposed.

Learning feedback is a separate, evidence-backed proposal record. It does not
write to the registry, capability passport, protocol, schema, overlay, skill,
memory, or runtime adapter by itself. An accepted proposal must pass the
relevant review, approval, and change-control path; a live skill, plugin,
memory, MCP, or agent-configuration change becomes a separately scoped task
with explicit user approval and fresh delegation evidence.

## Learning Rule

Learning feedback can explain or propose improvements to future routing, but it
is not authority. The reference CLI's directly consumable routing prior comes
from validated `routing-feedback` history. Every new task still performs fresh
capability, provider, context, and permission scans; current observations win
over historical feedback.

Workspace feedback indexes are also not authority. A positive history entry
must resolve to matching task-local feedback, passed completion gates, and
existing evidence before it can influence routing.

```text
old success + missing current tool -> do not route
old success + approval risk -> stop for approval
old failure + repaired current capability -> route with lower confidence
old context gap + similar task -> include the missing context in context-pack
```

This keeps VALP intelligent without letting stale memory override current
evidence.

## Automation Rule

Full automation means:

```text
continue automatically while evidence proves the loop is healthy
stop automatically when evidence, approval, context, runtime, or scope gates fail
```

It does not mean silent high-risk execution. The safer automation is the one
that knows exactly when to stop.
