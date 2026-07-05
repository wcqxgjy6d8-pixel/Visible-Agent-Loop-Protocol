# Dispatch: codex

Task: TASK-QUEUE-001
Profile: software-code

Role: implementation and verification

## Project Root

Before inspecting or writing evidence, run:

```bash
cd "/workspace/project"
```

Write evidence exactly to:

- `.herdr-loop/tasks/TASK-QUEUE-001/agents/codex/self-review.md`
- `.herdr-loop/tasks/TASK-QUEUE-001/agents/codex/evidence.md`
- `.herdr-loop/tasks/TASK-QUEUE-001/evidence/verification.md`

## Request

Fix the synthetic failing unit test in the sample project. Keep the change
minimal. Run the relevant test command and write verification evidence.

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
- These recommendations were filtered for `codex` with the recommender's provider filter.
- Task `run build, lint, or tests and write verification evidence` -> skill `verification-before-completion` (auto-load, confidence 0.39, mode auto-load, path `$CODEX_HOME/skills/verification-before-completion/SKILL.md`).

## Evidence Claim Rule

- Any build, test, lint, runtime, UI, or verification claim must cite a concrete command log, screenshot, receipt, or evidence file path.
- Do not write "verified", "tests passed", "build passed", or equivalent claims unless the evidence path exists or the blocker is explicit.
