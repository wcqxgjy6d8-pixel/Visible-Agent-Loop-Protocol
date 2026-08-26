# Skill Discovery, Routing, And Worker Use

Skill discovery and recommendation are routing aids. They are not a bundled
skill marketplace and they are not completion proof.

## Where Skills Come From

VALP does not ship a fixed library of hundreds of skills. A runtime or local
operator environment may expose skills from its own installed libraries. The
Doctor capability scan records the skills that are reachable for each Agent,
along with the source of that observation. Depending on the runtime, these may
come from provider-owned directories, a local overlay, or another adapter
discovery API.

For example, one local scan on 2026-08-19 found 223 skill records across five
local libraries. That number described the machine at scan time; it was not a
VALP package count and is not a promise that every Agent could use every one.
The public repository contains the discovery contract and sanitized examples,
not those private local libraries.

## End-To-End Flow

The actual control path is:

```text
skill libraries / runtime adapters
  -> Doctor scans reachable skills per Agent
  -> capability passport records names, source, and limitations
  -> user selects the Leader
  -> Leader decomposes the task into work items
  -> Leader declares role-to-Agent assignments
  -> VALP validates tools, permissions, model/session identity, context, and skill fit
  -> task-skill-router (or another backend) ranks relevant skills per work item
  -> VALP writes full recommendation evidence and per-Agent skill slices
  -> Leader dispatches the exact validated work items
  -> each Worker loads the control contract, then uses or declines its reachable skills
  -> Worker returns evidence, including blockers, confidence, and recommendations
  -> VALP audits receipts, evidence, review, approvals, and final synthesis
```

The Leader remains the authority for decomposition and assignment. VALP does
not silently replace a Worker because a different skill looks better. If a
required skill or runtime capability is missing, the route records the gap and
blocks or narrows the work according to the declared policy.

## What "Call" Means

There are three separate operations that should not be conflated:

1. **Discover:** Doctor or an adapter observes that a named skill is reachable
   for an Agent. This is capability evidence, not execution.
2. **Recommend:** the skill router matches a skill to a concrete work item and
   records confidence, mode, source, and install/missing status. A recommendation
   is guidance, not permission.
3. **Use:** the dispatched Worker reads the task-local control contract and
   dispatch slice, then loads or invokes the skill through its own provider
   environment. The Worker must say whether it used or skipped the recommendation
   and write the required evidence. VALP does not execute arbitrary third-party
   skill code inside the protocol core.

The coordinator keeps the complete `skill-recommendations.json`. Each Worker
receives only its provider-reachable `skill-slices/<agent>.json` plus a compact
`Recommended Skills` section in its dispatch. This prevents a Claude-only or
Hermes-only skill path from being handed to Codex by accident.

## Concrete Example

For a request such as "repair an API timeout and prove the fix":

1. Doctor records that the Codex Worker can reach the repository test and
   debugging skills, while a Claude Worker can reach the review skill.
2. The user-selected Leader declares an implementer work item for Codex and a
   reviewer work item for Claude. The Leader, not the router, owns that split.
3. The router recommends the relevant debugging/test skill for the first item
   and the review skill for the second, with confidence and source evidence.
4. VALP writes separate skill slices and dispatches each exact work item.
5. Codex loads the control contract, uses the reachable debugging/test skill,
   runs the fix and tests, and writes evidence. Claude loads its contract,
   reviews the exact artifact, and writes findings.
6. VALP checks the dispatch receipts, expected evidence, verification, review,
   and final synthesis. A runtime saying "fixed" alone is not enough.

Doctor never executes a skill just because it discovered it. The Leader never
gets permission to use a skill merely because the router recommended it. The
Worker-side provider is where the skill actually runs, subject to its existing
permissions and the task's control contract.

It is not a command, not a permission grant, and not a completion proof.

## Extracted Pattern

The protocol extracts the useful pattern from local skill routers:

```text
understand request
  -> decompose into runtime work items
  -> scan reachable skills per Agent
  -> rank likely skills for each task
  -> surface missing useful skills
  -> write recommendation evidence
  -> filter relevant installed skills into each Agent's dispatch
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

`auto-load` and `auto-run` in this table describe how a Leader-declared Agent
may use a skill after assignment validation. They do not mean the whole VALP
task should auto-trigger.
Task triggering is controlled separately by Auto Visible Mode trigger policy.

## Optional Backends

Any local backend may implement the minimal contract.

`task-skill-router` is one possible backend adapter. The protocol does not
depend on it.

## Full Mode Behavior

When `task-skill-router` or another backend is available, the reference CLI runs
it after task decomposition during routing and writes:

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
short runtime work-item label
skill name
installed/missing status
confidence
mode/decision
path or install hint
ref to skill-recommendations.json for full records
```

The target agent must treat this as execution guidance. If the skill exists in
that agent's reachable library and fits the assigned role, it should load or use
the skill. If it does not use the skill, it should state why in its evidence.

Do not paste a long raw task or full recommendation record into every dispatch.
The complete recommendation output belongs in `skill-recommendations.json`.
Dispatches should carry compact labels such as `Work item 1` plus enough text
for the worker to recognize the assigned item.

Dispatch generation should filter recommendations by the target agent's
reachable skill libraries when that information is known. For example, a Codex
dispatch should not ask Codex to load a Hermes-only skill path unless the runtime
explicitly marks that path as shared.

This is where discovered skills become operational. Without the per-Agent slice
and Worker-side use/skip evidence, multi-agent automation degrades into ordinary
prompt delegation.

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
whether the Leader-declared Agent is allowed to use it
```

If the skill is missing, surface it as a capability gap or future improvement.
Do not pretend another agent has the skill unless the current scan proves it.
