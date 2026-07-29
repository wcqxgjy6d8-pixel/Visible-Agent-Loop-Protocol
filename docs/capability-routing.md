# Capability Routing

Capability routing answers:

```text
Do the user-selected Leader's declared assignments fit current capability
evidence and protocol limits?
```

VALP does not answer the first question by choosing Agents. Doctor commissions
the facts, the user chooses the Leader, and that Leader declares the team.
Routing is the validation and evidence step after that declaration.

## Scan Inputs

```text
agent declared role
runtime status
available MCP/tools
installed skills
provider matrix
permission boundaries
context policy
skill recommendation backend availability
project AGENTS.md
local overlay capability profiles
task profile
approval gates
squad roster, if used
historical evidence, if available
routing feedback, if available
```

## Doctor Capability Passports

| Layer | Meaning | Strength |
|---|---|---|
| `official_claim` | sourced vendor/project claims about the Agent surface | descriptive, not live proof |
| `local_presence` | installation, version, and local discovery evidence | proves presence, not task fitness |
| `live_callable` | current runtime/session/tool reachability | current when the probe is valid |
| `task_verified` | prior task evidence bound to the same surface, model, provider, reasoning mode, and session | strong only while the complete binding matches |

Doctor writes one passport per addressable Agent surface/session. The passport
also records reachable Skills and MCP, permissions, context state, known
limitations, and role eligibility. Missing evidence stays `unknown`.

Capability profiles and scores are hints available to the Leader. They are not
assignments and never authorize VALP to fill a role. A current scan can
invalidate old evidence, and a permission boundary overrides every preference.

Model-aware routing adds the actual runtime model to the route identity. Keep
the agent surface, provider, reasoning mode, permissions, context, and task
evidence alongside separate declared and observed model records. A model
mismatch invalidates model-bound history; stale or unknown observation
downgrades it. Unknown model identity is never strong implementation or final
review evidence, and runtime_default alone cannot satisfy a model-aware matrix.
No configured declaration is required when a current, high-confidence live
observation is bound to a known session; an explicit declaration mismatch still
blocks.

Dynamic model-aware routing evaluates the runtime probe before scoring. The
probe must be `observed`, freshness must compute to `current`, and its
non-sensitive session identity must be known before the candidate is eligible
for implementer or final-review work. An explicit request for an agent does not
bypass this gate.

## Intelligent Routing Steps

```text
run Doctor and commission current capability passports
let the user explicitly select the Leader
publish the task without routing it
let the Leader decompose the task and author assignment-declaration.json
scan current runtime, tools, MCP, skills, model/session, permissions, and context
score the Leader-declared assignments as advisory evidence
validate every declared role and required independence boundary
write assignment-validation.json
dispatch only when validation passes
```

VALP does not define or select a universal Leader. The user selects the Leader.
The Leader is the coordinator authority but is not automatically a routed
worker. If the Leader declares a runtime `coordinator` assignment, that Agent
must be the same Leader. Local overlays can provide hints, but neither an
overlay nor the runtime may override the user's selection.

Recommended score fields:

```json
{
  "codex": {
    "profile_fit": 0.9,
    "tool_fit": 0.95,
    "skill_fit": 0.8,
    "permission_fit": 1,
    "context_fit": 0.7,
    "evidence_history": 0.8,
    "availability": 0.9,
    "risk_fit": 0.85,
    "overall": 0.86
  }
}
```

Scores help the Leader and explain validation. They do not select an Agent and
do not prove completion.

## Confidence Bands

| Band | Meaning | Default action |
|---|---|---|
| high | declared Agent has required tools, permission, context, and current evidence | validate the declaration for normal work |
| medium | declared Agent may work, but risk or evidence is incomplete | Leader narrows the assignment or adds review |
| low | important capability or permission is missing/unknown | block validation and return the gap to the Leader |

If the declared implementer is medium confidence and the task is high risk,
VALP should require review before mutation or block the declaration. It must not
choose a different implementer.

If a declared implementer or final reviewer has an unknown, stale,
unsupported, unavailable, mismatched, or session-unbound model identity,
validation records `active_model_identity:<role>:<agent>` and blocks. It may
expose `discovery`, `prototype`, and `manual` as suggestions, but cannot remove,
replace, or silently reassign the Leader's declaration.

## Routing Outputs

```json
{
  "profile": "software-code",
  "runtime_adapter": {
    "class": "daemon_queue",
    "name": "example-runtime",
    "full_mode_capable": true
  },
  "role_requirements": ["implementer", "reviewer"],
  "role_assignments": {
    "implementer": "build-agent",
    "reviewer": "review-agent"
  },
  "assignment_authority": "leader_declared",
  "assignment_declaration": {
    "status": "recorded",
    "ref": "assignment-declaration.json",
    "leader_agent": "local-coordinator",
    "selected_by": "user",
    "selection_ref": "user-message:leader-selection"
  },
  "assignment_validation": {
    "status": "pass",
    "ref": "assignment-validation.json"
  },
  "coordinator_selection": {
    "selected_agent": "local-coordinator",
    "selection_rule": "Explicit user-selected Leader; VALP validated but did not select the Agent."
  },
  "selected_agents": ["build-agent", "review-agent"],
  "local_overlay": {
    "used": true,
    "ref": ".herdr/valp-local-overlay.json",
    "note": "Agent profiles used as routing hints only."
  },
  "agent_match_reasons": {
    "build-agent": ["implementation", "verification"],
    "review-agent": ["read_only_review"]
  },
  "candidate_scores": {
    "local-coordinator": {"overall": 0.84, "confidence": "high"},
    "build-agent": {"overall": 0.86, "confidence": "high"},
    "review-agent": {"overall": 0.78, "confidence": "medium"},
    "prototype-agent": {"overall": 0.42, "confidence": "low"}
  },
  "rejected_candidates": [
    {
      "agent": "prototype-agent",
      "reason": "prototype profile does not match source-edit evidence gate",
      "confidence": "low"
    }
  ],
  "selected_agent_context_policies": {},
  "skill_recommendations": {
    "status": "not_run",
    "reason": "requires decomposed runtime work items"
  },
  "visible_attention": {
    "status": "recorded",
    "loop_layer": "agentic_coding_loop",
    "attention_map": "attention-map.json",
    "context_selection": "context-selection.json",
    "context_pack": "context-pack.json",
    "mask_list": "mask-list.json",
    "evidence_board": "evidence-board.json",
    "visible_routing": "visible-routing.md"
  },
  "provider_matrix": {
    "status": "scanned",
    "missing": []
  },
  "squad_routing": {
    "used": false
  },
  "capabilities_missing": []
}
```

## Routing Rules

- Only the user selects the Leader.
- Only the selected Leader declares task assignments.
- VALP may validate or block a declaration; it may not select or replace an Agent.
- Do not use hidden agent judgment as routing input.
- Do not route more work to an agent beyond its hard context threshold.
- Do not allow skill recommendation to bypass role boundaries.
- Do not allow provider matrix claims to bypass proof or approval gates.
- Do not allow stored freshness or a declared model to replace a live dynamic
  probe when dynamic discovery is required.
- Do not assign implementer or final reviewer to an unknown, stale, or
  session-unbound active model.
- Do not hide attention/routing decisions; record selected context and masked inputs.
- Do not let local overlay profiles become fixed assignments.
- Do not let historical feedback replace current scans.
- Do not treat squad leader judgment as worker completion evidence.
- Record missing capabilities instead of pretending they exist.

`selected_agents` remains a compatibility field. In a conforming new routing
record it is the ordered unique set of Agents from the Leader's role
assignments. The Leader is included only when it also has an explicit runtime
role. The field must never be described as a list selected by VALP.
