# Intelligent Routing

VALP routing is a control decision. It should explain why each agent was
selected, why other plausible agents were not selected, and what evidence would
change the route.

The goal is not to create a hidden optimizer. The goal is to make routing
adaptive, auditable, and easy to correct.

## Routing Flow

```text
understand task
  -> decompose execution tasks
  -> identify evidence gates
  -> load local overlay, if present
  -> scan runtime/tools/skills/context
  -> check approval risks
  -> score candidates
  -> route selected agents
  -> record rejected high-relevance candidates
  -> require receipts and evidence
```

## Candidate Score

Recommended scoring factors:

| Factor | Question |
|---|---|
| profile_fit | Does the agent's capability profile match the task profile? |
| tool_fit | Are required tools, MCP servers, CLIs, and runtime access available now? |
| skill_fit | Are relevant installed skills present, or recommended after decomposition? |
| permission_fit | Is the agent allowed to do this work? |
| context_fit | Is the agent below hard compression threshold? |
| evidence_history | Has the agent produced good evidence for similar tasks? |
| availability | Is the agent online, idle, resumable, or overloaded? |
| risk_fit | Should this agent handle high-risk, mutating, or read-only work? |

The score is not the protocol. It is a compact way to make the routing decision
visible.

## Confidence Bands

| Confidence | Default behavior |
|---|---|
| high | dispatch normal work with expected evidence |
| medium | dispatch smaller scoped work, require review, or ask for approval before mutation |
| low | route discovery/setup work, use a squad leader, or stop with missing capabilities |

## Low Confidence Rules

- Missing required tool: do not assign execution work; record the gap.
- Context near hard threshold: compress before dispatch.
- Permission boundary conflict: reject the candidate.
- High-risk work with medium implementer confidence: require review or approval
  before mutation.
- Similar recent failure: require fresh evidence or a narrower discovery task.

## Re-Routing Triggers

Re-run routing when:

```text
an agent goes offline or becomes overloaded
context threshold is reached
a required tool or MCP server is missing
dispatch is blocked or unproven
expected evidence is missing after runtime completion
review finds critical/high blockers
user changes task scope or risk tolerance
```

Re-routing should preserve old evidence. Do not overwrite the old routing record
without recording why the route changed.

## Output Requirements

Routing records should include:

```text
selected agents
candidate scores
routing confidence
rejected high-relevance candidates
missing capabilities
local overlay ref, if used
skill recommendation ref, if used
provider matrix ref
context policy snapshot
expected evidence refs
```

This keeps the system honest: when a route is wrong, the next agent can see
whether the failure came from bad scoring, stale capability data, missing tools,
context pressure, or an evidence gap.
