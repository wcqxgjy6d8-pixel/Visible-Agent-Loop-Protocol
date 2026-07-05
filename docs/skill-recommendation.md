# Skill Recommendation

Skill recommendation is a routing aid.

It is not a command, not a permission grant, and not a completion proof.

## Extracted Pattern

The protocol extracts the useful pattern from local skill routers:

```text
understand request
  -> decompose into runtime work items
  -> scan installed skills
  -> rank likely skills for each task
  -> surface missing useful skills
  -> write recommendation evidence
  -> surface relevant installed skills in each dispatch prompt
  -> record whether the agent used or skipped the recommendation
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
- Do not hide recommendations from the agent that is expected to use them.
- Do not pretend a missing skill is installed.

`auto-load` and `auto-run` in this table describe how a selected agent may use a
skill after routing. They do not mean the whole VALP task should auto-trigger.
Task triggering is controlled separately by Auto Visible Mode trigger policy.

## Optional Backends

Any local backend may implement the minimal contract.

`task-skill-router` is one possible backend adapter. The protocol does not
depend on it.

## Full Mode Behavior

When `task-skill-router` or another backend is available, the reference CLI runs
it during routing and writes:

```text
.herdr-loop/tasks/<task-id>/skill-recommendations.json
```

When the backend supports provider filtering, the reference CLI should also run
per-agent recommendations and write them under:

```json
{
  "per_agent": {
    "codex": {
      "status": "complete",
      "results": []
    }
  },
  "agent_filtering": {
    "status": "complete",
    "backend": "task-skill-router",
    "agents": ["codex", "claude"]
  }
}
```

Dispatch prompts must prefer `per_agent.<agent>` recommendations. The aggregate
result remains useful for task-level capability scanning, but broad prompts can
surface irrelevant skills owned by other providers. Provider filtering prevents
one agent from being asked to load another agent's private skill.

Each dispatch prompt should include a `Recommended Skills` section with:

```text
runtime work item
skill name
installed/missing status
confidence
mode/decision
path or install hint
```

The target agent must treat this as execution guidance. If the skill exists in
that agent's reachable library and fits the assigned role, it should load or use
the skill. If it does not use the skill, it should state why in its evidence.

Dispatch generation should filter recommendations by the target agent's
reachable skill libraries when that information is known. For example, a Codex
dispatch should not ask Codex to load a Hermes-only skill path unless the runtime
explicitly marks that path as shared.

This is where installed skills become operational. Without this step, multi-agent
automation degrades into ordinary prompt delegation.

## Relationship To Local Overlays

A local overlay may record which agents have which skill libraries. A recommender
may rank those skills after task decomposition. Neither layer is allowed to turn
a suggested skill into a permission grant.

The routing record should show:

```text
which recommender ran
which runtime work items were scored
which installed skills matched
which agent library owns the skill
whether the selected agent is allowed to use it
```

If the skill is missing, surface it as a capability gap or future improvement.
Do not pretend another agent has the skill unless the current scan proves it.
