# Intelligent Routing

VALP routing validates a control decision made by the user-selected Leader. It
should explain why each declared assignment passed or failed and what evidence
would change the validation result.

The goal is not to create an Agent selector. The goal is to give the Leader
current facts and make assignment validation adaptive, auditable, and easy to
correct.

## Authority Order

```text
Doctor commissions capability passports
  -> user selects Leader
  -> Leader decomposes work and declares assignments
  -> VALP validates the declaration
  -> runtime dispatches exactly the validated declaration
```

Doctor and VALP may report gaps, scores, and safer modes. They may not choose a
Leader, fill a missing role, or replace a blocked Agent.

## Dynamic Model Gate

For model-aware Full or Remote Mode routing, Doctor and runtime preflight emit
one closed model probe per addressable Agent session. Validation combines the
current model/session evidence with capability fit; it does not treat the Agent
product name or a static configured default as the active model.

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

When the gate fails, validation records the missing capability and blocks the
declaration. Discovery, prototype, or Manual Mode may be suggested to the
Leader, but VALP cannot activate a fallback Agent. A Leader declaration does
not override the evidence gate.

Before submit, dispatch preflight probes again. It compares the current
model/session/freshness binding with the route-time fingerprint. Any change or
new ineligibility blocks delivery and records task-local evidence; the dispatch
cannot inherit a stale routing decision.

## Token-Efficient Routing

The reference CLI runs the current MCP/tool scan and task-skill-router evidence
before validating the Leader's declaration. It then checks that the declared
bounded team covers required roles and writes `iteration-budget.json` with
limits for aggregate dispatch reference tokens, dispatch count, reroutes, and
fix-review rounds.
The reference correction policy permits at most three fix-review rounds; a
third round is the final bounded correction and must not create a fourth round.
Observed usage comes from accepted dispatch receipts and recorded dispatch
measurements. Legacy and v2 representations of the same accepted delivery count
once, using the v2 work-item identity as the authoritative logical dispatch. A
new submission is stopped before it would exceed a limit or a safety gate such
as approval, runtime preflight, missing evidence, critical review, or context
compression.

The complete `skill-recommendations.json` report is coordinator-only context.
Each Leader-declared provider gets `skill-slices/<agent>.json`, a compact
artifact with only installed, provider-reachable matches and short task labels. This prevents
another provider's skill records from entering a worker dispatch, while keeping
the full recommendation evidence available to the coordinator and audit.

## Routing Flow

```text
evaluate trigger policy, when Auto Visible Mode is enabled
  -> publish only
  -> Doctor commissions current capability passports
  -> user selects Leader
  -> Leader understands the task and decomposes runtime work items
  -> Leader writes assignment-declaration.json
  -> VALP loads local overlay and scans runtime/tools/skills/model/context
  -> VALP checks approval risks and scores declared assignments
  -> VALP validates assignment-declaration.json
  -> build visible attention map for Leader-declared Agents
  -> record selected context, masked inputs, and evidence board
  -> write concise dispatch payloads with refs for long context
  -> route the validated declaration
  -> require receipts and evidence
```

If Auto Visible Mode is enabled, the trigger decision happens before Leader
selection and assignment validation. The trigger may publish a draft task or
refresh capability facts. It must stop until a valid user-selected Leader
declaration exists. A trigger is neither a permission grant nor Agent-selection
authority.

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

The score is not the protocol and does not select an Agent. It is a compact way
to make the validation evidence visible to the Leader and user.

## Confidence Bands

| Confidence | Default behavior |
|---|---|
| high | validate normal work with expected evidence |
| medium | ask the Leader to narrow scope, add review, or seek approval |
| low | block and report missing capability evidence |

## Coordinator Responsibility

The user-selected Leader owns the quality of task assignment. The Leader's
declaration must name itself as coordinator and give a reason for every role.
The dispatch should be short, role-specific, and evidence-oriented.
It should point workers to `task.md`, routing records, visible attention records,
and skill recommendation records instead of pasting full chat history or broad
recommendation output into every worker prompt.

## Low Confidence Rules

- Missing required tool: block the declared execution role and record the gap.
- Context near hard threshold: compress before dispatch.
- Permission boundary conflict: reject the candidate.
- High-risk work with medium implementer confidence: require review or approval
  before mutation.
- Similar recent failure: require fresh evidence or a narrower discovery task.

## Re-Routing Triggers

Require a new Leader declaration and validation when:

```text
an agent goes offline or becomes overloaded
context threshold is reached
a required tool or MCP server is missing
dispatch is blocked or unproven
expected evidence is missing after runtime completion
review finds critical/high blockers
user changes task scope or risk tolerance
```

Re-routing should preserve the previous declaration and validation evidence. Do
not overwrite the old routing record without recording why the Leader changed
the declaration.

## Output Requirements

Routing records should include:

```text
assignment authority
Leader declaration ref and user-selection ref
assignment validation ref and status
Leader-declared Agents (`selected_agents` compatibility field)
candidate scores
routing confidence
rejected high-relevance candidates
missing capabilities
local overlay ref, if used
skill recommendation ref, if used
provider matrix ref
context policy snapshot
role requirements and role assignments
user-selected Leader evidence
visible attention refs
loop layer
expected evidence refs
trigger policy ref, when used
```

This keeps the system honest: when an assignment is wrong, the Leader can see
whether the failure came from stale capability data, missing tools, model drift,
context pressure, permissions, or an evidence gap. VALP still does not choose
the correction.

## Visible Attention

Routing must not become a black box. For non-trivial tasks, successful route
validation should write:

```text
attention-map.json
context-selection.json
context-pack.json
mask-list.json
evidence-board.json
visible-routing.md
```

`visible-routing.md` is the human-readable summary that should be printed in the
runtime frontend. It explains the Leader authority and declaration, which
attention heads and context were selected for the declared Agents, what compact
context pack is given to workers, which inputs were masked, and what evidence is
required next.
