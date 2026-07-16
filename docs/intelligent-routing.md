# Intelligent Routing

VALP routing is a control decision. It should explain why each agent was
selected, why other plausible agents were not selected, and what evidence would
change the route.

The goal is not to create a hidden optimizer. The goal is to make routing
adaptive, auditable, and easy to correct.

## Dynamic Model Gate

For model-aware Full or Remote Mode routing, runtime preflight emits one closed
model probe per candidate before scoring. The scorer combines current
model/session evidence with capability fit; it does not treat the agent product
name or a static configured default as the active model.

High-risk role eligibility requires:

```text
probe status = observed
active model id != unknown
computed freshness = current
session identity = known
```

Freshness uses a bounded TTL of 60 to 86400 seconds, default 3600. The history
binding covers model, provider, reasoning mode, freshness state, session token,
and agent surface. Any change invalidates model-bound history. Missing prior
binding also invalidates historical score until fresh evidence requalifies the
new binding.

When the gate fails, routing removes implementer/final-review eligibility,
records the missing capability, and stops or uses a visible discovery,
prototype, or Manual Mode fallback. Requested-agent routing cannot override
the gate.

Before submit, dispatch preflight probes again. It compares the current
model/session/freshness binding with the route-time fingerprint. Any change or
new ineligibility blocks delivery and records task-local evidence; the dispatch
cannot inherit a stale routing decision.

## Token-Efficient Routing

The reference CLI runs the current MCP/tool scan and task-skill-router evidence
before scoring candidates. It then selects the minimum capable team that covers
the required roles and writes `iteration-budget.json` with limits for aggregate
dispatch reference tokens, dispatch count, reroutes, and fix-review rounds.
The reference correction policy permits at most three fix-review rounds; a
third round is the final bounded correction and must not create a fourth round.
Observed usage comes from accepted dispatch receipts and recorded dispatch
measurements. Legacy and v2 representations of the same accepted delivery count
once, using the v2 work-item identity as the authoritative logical dispatch. A
new submission is stopped before it would exceed a limit or a safety gate such
as approval, runtime preflight, missing evidence, critical review, or context
compression.

The complete `skill-recommendations.json` report is coordinator-only context.
Each selected provider gets `skill-slices/<agent>.json`, a compact artifact with
only installed, provider-reachable matches and short task labels. This prevents
another provider's skill records from entering a worker dispatch, while keeping
the full recommendation evidence available to the coordinator and audit.

## Routing Flow

```text
evaluate trigger policy, when Auto Visible Mode is enabled
  -> understand task
  -> decompose runtime work items
  -> identify evidence gates
  -> load local overlay, if present
  -> scan runtime/tools/skills/context
  -> check approval risks
  -> score candidates
  -> select coordinator and execution roles from current evidence
  -> build visible attention map
  -> record selected context, masked inputs, and evidence board
  -> write concise dispatch payloads with refs for long context
  -> route selected agents
  -> record rejected high-relevance candidates
  -> require receipts and evidence
```

If Auto Visible Mode is enabled, the trigger decision happens before routing.
The routing layer should read the trigger evidence but must not treat it as a
permission grant. Low-confidence or high-risk trigger decisions should publish a
draft task, route review/setup work, or stop for approval instead of dispatching
mutating work.

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

## Coordinator Responsibility

Whoever is selected as coordinator or leader owns the quality of task
assignment. The dispatch should be short, role-specific, and evidence-oriented.
It should point workers to `task.md`, routing records, visible attention records,
and skill recommendation records instead of pasting full chat history or broad
recommendation output into every worker prompt.

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
role requirements and role assignments
coordinator selection reason
visible attention refs
loop layer
expected evidence refs
trigger policy ref, when used
```

This keeps the system honest: when a route is wrong, the next agent can see
whether the failure came from bad scoring, stale capability data, missing tools,
context pressure, or an evidence gap.

## Visible Attention

Routing must not become a black box. For non-trivial tasks, publish should write:

```text
attention-map.json
context-selection.json
context-pack.json
mask-list.json
evidence-board.json
visible-routing.md
```

`visible-routing.md` is the human-readable summary that should be printed in the
runtime frontend. It explains which attention heads were selected, which context
was selected, what compact context pack is given to workers, which inputs were
masked, and what evidence is required next.
