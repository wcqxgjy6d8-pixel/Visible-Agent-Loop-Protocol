# Skill Recommendation

Skill recommendation is a routing aid.

It is not a command, not a permission grant, and not a completion proof.

## Extracted Pattern

The protocol extracts the useful pattern from local skill routers:

```text
understand request
  -> decompose into execution tasks
  -> scan installed skills
  -> rank likely skills for each task
  -> surface missing useful skills
  -> write recommendation evidence
```

## Minimal Contract

```json
{
  "schema_version": "valp-skill-recommendations.v1",
  "status": "complete",
  "execution_tasks": [
    {
      "task": "inspect failing tests and identify root cause",
      "routing": {
        "priority": "P1",
        "decision": "auto-load",
        "reason": "Strong installed workflow match"
      },
      "matches": [
        {
          "skill": "systematic-debugging",
          "installed": true,
          "confidence": 0.92,
          "mode": "auto-load",
          "source_agent_or_library": "hermes"
        }
      ],
      "missing_skills": []
    }
  ]
}
```

## Priority Semantics

| Priority | Decision | Meaning |
|---|---|---|
| P0 | recommend | high-risk; report and ask approval before sensitive side effects |
| P1 | auto-load / auto-run | strong installed workflow match |
| P2 | optional-load / guidance-only | use only if it materially improves execution |
| P3 | bypass | no useful skill needed |

## Important Boundaries

- Do not run a recommender on a large raw prompt as authoritative routing.
- Do not let recommendation bypass approval gates.
- Do not let recommendation bypass agent role boundaries.
- Do not let recommendation bypass context compression gates.
- Do not treat recommendation as proof of completion.

## Optional Backends

Any local backend may implement the minimal contract.

`task-skill-router` is one possible backend adapter. The protocol does not
depend on it.

## Relationship To Local Overlays

A local overlay may record which agents have which skill libraries. A recommender
may rank those skills after task decomposition. Neither layer is allowed to turn
a suggested skill into a permission grant.

The routing record should show:

```text
which recommender ran
which execution tasks were scored
which installed skills matched
which agent library owns the skill
whether the selected agent is allowed to use it
```

If the skill is missing, surface it as a capability gap or future improvement.
Do not pretend another agent has the skill unless the current scan proves it.
