# Capability Routing

Capability routing answers:

```text
Which agents should participate, why, and under which limits?
```

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

## Capability Evidence Layers

| Layer | Meaning | Strength |
|---|---|---|
| declared capability | what an agent claims or is configured to do | weak |
| installed skills | workflows available to the agent | medium |
| current tools/MCP | tools available now | medium |
| runtime status | online/offline/focused/working/idle | medium |
| provider matrix | provider-specific MCP, skill, resume, approval, model, and limitation evidence | strong when current |
| permission boundary | what the agent must not do | strong |
| context policy | whether the agent can safely receive more work | strong |
| skill recommendation | which skill fits a decomposed task | advisory |
| squad routing | leader and member routing evidence | advisory until member dispatch receipts exist |
| verification history | prior pass/fail evidence | strong when current |
| local overlay profile | operator/workspace hints about likely strengths | advisory |
| routing feedback | prior outcome records for similar tasks | strong only as a prior |

Capability profiles are routing hints, not assignments. A current scan can
override a remembered strength, and a permission boundary can override every
preference.

## Intelligent Routing Steps

```text
decompose the user request
map each execution task to required capabilities and evidence
load local overlay profiles, if present
scan runtime, tools, MCP, skills, and context policy
score candidate agents
select coordinator/implementer/reviewer/prototype roles from current evidence
build a visible attention map from candidate scores and selected context
record selected agents and rejected high-relevance candidates
route discovery/review first when confidence is low
```

VALP does not define a universal leader. The coordinator is whichever local
agent or human the runtime selects from current capability evidence. Local
overlays can provide hints, but they cannot force a fixed leader across tasks.

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

Scores explain the route; they do not prove completion.

## Confidence Bands

| Band | Meaning | Default action |
|---|---|---|
| high | agent has required tools, permission, context, and evidence history | dispatch normal work |
| medium | agent can likely work, but risk or evidence is incomplete | dispatch smaller scoped task or require review |
| low | important capability or permission is missing/unknown | route discovery, ask for setup, or stop |

If the best implementer is medium confidence and the task is high risk, VALP
should require review before mutation or ask for explicit approval.

## Routing Outputs

```json
{
  "profile": "software-code",
  "runtime_adapter": {
    "class": "daemon_queue",
    "name": "example-runtime",
    "full_mode_capable": true
  },
  "role_requirements": ["coordinator", "implementer", "reviewer"],
  "role_assignments": {
    "coordinator": "local-coordinator",
    "implementer": "build-agent",
    "reviewer": "review-agent"
  },
  "coordinator_selection": {
    "selected_agent": "local-coordinator",
    "selection_rule": "Selected from current capability evidence, not from a protocol-wide leader default."
  },
  "selected_agents": ["local-coordinator", "build-agent", "review-agent"],
  "local_overlay": {
    "used": true,
    "ref": ".herdr/valp-local-overlay.json",
    "note": "Agent profiles used as routing hints only."
  },
  "agent_match_reasons": {
    "local-coordinator": ["coordination", "gates"],
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
    "reason": "requires decomposed execution tasks"
  },
  "visible_attention": {
    "status": "recorded",
    "loop_layer": "agentic_coding_loop",
    "attention_map": "attention-map.json",
    "context_selection": "context-selection.json",
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

- Do not assign agents by habit.
- Do not use hidden agent judgment as routing input.
- Do not route more work to an agent beyond its hard context threshold.
- Do not allow skill recommendation to bypass role boundaries.
- Do not allow provider matrix claims to bypass proof or approval gates.
- Do not hide attention/routing decisions; record selected context and masked inputs.
- Do not let local overlay profiles become fixed assignments.
- Do not let historical feedback replace current scans.
- Do not treat squad leader judgment as worker completion evidence.
- Record missing capabilities instead of pretending they exist.
