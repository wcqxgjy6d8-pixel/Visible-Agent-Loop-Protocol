# Squad Routing

Squad routing lets a leader agent choose which member should handle a task.

It is optional. Direct agent routing remains valid.

## When To Use A Squad

Use a squad when:

```text
the task spans multiple specialties
the best worker is not obvious at intake
the routing target should stay stable while members change
the leader has clear routing instructions and member roles
```

Do not use a squad when a single qualified agent is already known.

## Required Records

When a squad is used, record:

```text
squad id or name
leader agent
members
member roles
routing instructions
leader dispatch
leader decision
delegated member dispatches
no_action or escalation, if no member is delegated
```

## Leader Rules

The leader agent must:

- read the task and routing context;
- decide whether to delegate, do nothing, or escalate;
- state the reason briefly;
- create precise, concise visible delegation when assigning work to a member;
- cite task-local refs instead of pasting full context into member dispatches;
- resolve member recommendations with an explicit scope and complexity decision;
- stop after routing unless explicitly assigned implementation or review work.

The leader must not silently become the implementer unless the routing record
says so.

## Delegation Receipts

Delegation to a member is a new dispatch. It requires its own receipt:

```text
dispatch_written
dispatch_submitted
dispatch_completed or dispatch_blocked
```

Leader judgment is not member completion.

## Loop Control

Agent-to-agent routing can accidentally create loops.

The default rule:

```text
Do not mention or re-dispatch another agent for thanks, acknowledgements,
sign-offs, or "no action needed" messages.
```

Use delegation only for a concrete new subtask, escalation, or explicit user
request.

## Gates Still Apply

Squad routing cannot bypass:

```text
provider matrix checks
context compression
approval gates
receipt gates
expected evidence
review/fix/review
agent recommendation resolution
```

Squad routing is a routing mechanism, not a completion mechanism.
