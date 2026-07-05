# Local Overlays

VALP is the open protocol. A local overlay is the machine, workspace, or
operator-specific layer that tells a runtime how this environment usually works.

The overlay exists so VALP can stay generic while real users keep useful local
facts such as agent names, runtime preferences, skill paths, task folder habits,
and context limits.

## Layering

```text
VALP spec
  -> runtime adapter
  -> local overlay
  -> workspace/project instructions
  -> task evidence
```

The more specific layer can add detail, but it cannot weaken protocol gates.

## What Belongs In A Local Overlay

```text
workspace defaults
runtime adapter preferences
local agent names and aliases
agent capability profiles
skill library paths
context policy overrides
project folder conventions
approval preferences
routing feedback refs
```

## What Must Not Belong In A Local Overlay

```text
secrets
API keys
raw private data
hidden agent conversations
unverified claims of completion
rules that bypass approval gates
rules that treat dispatch insertion as delivery
rules that make agent roles fixed forever
```

## Capability Profiles Are Hints

A local overlay may say that an agent is often good at implementation, review,
research, design, or coordination. That is a prior, not an assignment.

Every task still needs a fresh scan of:

```text
runtime status
available tools and MCP servers
installed skills
context policy
permission boundaries
task profile
expected evidence
approval risk
recent routing feedback
```

## Conflict Rules

| Conflict | Winner |
|---|---|
| local preference vs protocol hard gate | protocol hard gate |
| old memory vs current runtime scan | current runtime scan |
| agent strength vs project permission boundary | project permission boundary |
| skill recommendation vs approval requirement | approval requirement |
| historical success vs current missing tool | current missing tool |

## Example Overlay

```json
{
  "schema_version": "valp-local-overlay.v1",
  "updated_at": "2026-07-03T00:00:00Z",
  "workspace_defaults": {
    "evidence_root": ".herdr-loop/tasks"
  },
  "runtime_preferences": {
    "preferred_full_mode_runtime": "herdr",
    "manual_mode_allowed": "learning_and_audit"
  },
  "agent_capability_profiles": {
    "codex": {
      "routing_hint_only": true,
      "likely_roles": ["implementation", "verification"],
      "must_not_do": ["bypass approval gates"],
      "context_policy": {
        "hard_compression_pct": 65,
        "emergency_stop_pct": 80
      }
    },
    "claude": {
      "routing_hint_only": true,
      "likely_roles": ["read_only_review", "architecture_review"],
      "must_not_do": ["claim runtime facts without evidence"]
    }
  }
}
```

## Implementation Rule

The routing record should say whether a local overlay was used and where it
came from. If the overlay affects a decision, the routing record must explain
that it was used as a hint and name the current evidence that made the route
valid.
