# Dispatch: codex

Task: TASK-EXAMPLE-001
Profile: software-code

Role: implementation and verification

## Project Root

Before inspecting or writing evidence, run:

```bash
cd "/workspace/project"
```

Write evidence exactly to:

- `.herdr-loop/tasks/TASK-EXAMPLE-001/agents/codex/self-review.md`
- `.herdr-loop/tasks/TASK-EXAMPLE-001/agents/codex/evidence.md`
- `.herdr-loop/tasks/TASK-EXAMPLE-001/evidence/verification.md`

## Task Brief

Fix the synthetic failing unit test in the sample project. Keep the change
minimal. Run the relevant test command and write verification evidence.

## Task References

The coordinator/leader is responsible for sending a precise, concise dispatch.
Use this brief first. Load full context only from task-local refs when your role
requires it:

- `.herdr-loop/tasks/TASK-EXAMPLE-001/task.md`
- `.herdr-loop/tasks/TASK-EXAMPLE-001/routing.json`
- `.herdr-loop/tasks/TASK-EXAMPLE-001/visible-routing.md`
- `.herdr-loop/tasks/TASK-EXAMPLE-001/context-selection.json`
- `.herdr-loop/tasks/TASK-EXAMPLE-001/mask-list.json`
- `.herdr-loop/tasks/TASK-EXAMPLE-001/evidence-board.json`
- `.herdr-loop/tasks/TASK-EXAMPLE-001/skill-recommendations.json`

## Payload Budget

- Treat this dispatch as the working prompt, not as a dump of all coordinator context.
- Do not ask the coordinator to paste hidden chat context; use the referenced task files and evidence refs.
- Keep your output scoped to expected evidence plus actionable recommendations.

## Visible Attention Slice

- Loop layer: `agentic_coding_loop`
- Your attention head(s): state_gate, implementation
- Design contract: `not_applicable`
- Context selected for this round:
  - `task.md`: active task brief
  - `routing.json`: candidate scores and selected agents
  - `skill-recommendations.json`: recommended installed skills
- Inputs masked out:
  - old chat memory without file-backed evidence: stale context is not valid routing or completion evidence
  - hidden votes, hidden reviews, or hidden routing decisions: VALP requires visible decision input
  - Agy prototype output as production proof: prototype evidence can inform implementation but cannot satisfy build/test/release gates
  - release, signing, upload, deploy, auth, secrets, or destructive changes: high-risk operations require explicit user approval
  - invalid, superseded, rejected, or blocked evidence: these evidence statuses do not satisfy done criteria

## Boundaries

- Do not delete files.
- Do not change secrets, auth, release settings, or deployment settings.
- Do not publish or upload anything.

## Expected Evidence

- `agents/codex/self-review.md`
- `agents/codex/evidence.md`
- `evidence/verification.md`

## Recommended Skills

- Skill recommendations are routing aids, not permission grants.
- Full recommendation records remain in `skill-recommendations.json`; dispatch only carries short labels.
- These recommendations were filtered for `codex` with the recommender's provider filter.
- Work item 1 `run build, lint, or tests and write verification evidence` -> skill `verification-before-completion` (auto-load, confidence 0.39, mode auto-load, path `$CODEX_HOME/skills/verification-before-completion/SKILL.md`).

## Evidence Claim Rule

- Any build, test, lint, runtime, UI, or verification claim must cite a concrete command log, screenshot, receipt, or evidence file path.
- Do not write "verified", "tests passed", "build passed", or equivalent claims unless the evidence path exists or the blocker is explicit.

## Required Response

Write concise evidence to the expected path. Include blockers, confidence limits, and any handoff needed for the next agent.

Also include a `## Recommendations` section. List concrete next steps, risks, or follow-up suggestions, or state `No further action recommended.` The coordinator must resolve these recommendations before Done.
