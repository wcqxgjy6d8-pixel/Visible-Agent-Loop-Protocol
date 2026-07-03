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
task profile
approval gates
squad roster, if used
historical evidence, if available
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

## Routing Outputs

```json
{
  "profile": "software-code",
  "runtime_adapter": {
    "class": "daemon_queue",
    "name": "example-runtime",
    "full_mode_capable": true
  },
  "selected_agents": ["hermes", "codex", "claude"],
  "agent_match_reasons": {
    "codex": ["implementation", "verification"],
    "claude": ["read_only_review"],
    "hermes": ["coordination", "gates"]
  },
  "selected_agent_context_policies": {},
  "skill_recommendations": {
    "status": "not_run",
    "reason": "requires decomposed execution tasks"
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
- Do not treat squad leader judgment as worker completion evidence.
- Record missing capabilities instead of pretending they exist.
