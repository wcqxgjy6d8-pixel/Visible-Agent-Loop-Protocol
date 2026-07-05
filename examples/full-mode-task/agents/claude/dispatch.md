# Dispatch: claude

Task: TASK-EXAMPLE-001
Profile: software-code

Role: read-only reviewer

## Project Root

Before inspecting or writing evidence, run:

```bash
cd "/workspace/project"
```

Write evidence exactly to:

- `.herdr-loop/tasks/TASK-EXAMPLE-001/agents/claude/review.md`

## Request

Review the implementation evidence and verification output. Report
critical/high findings first. Do not edit source.

## Visible Attention Slice

- Loop layer: `agentic_coding_loop`
- Your attention head(s): ux_review
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

## Expected Evidence

- `agents/claude/review.md`

## Recommended Skills

- Skill recommendations are routing aids, not permission grants.
- These recommendations were filtered for `claude` with the recommender's provider filter.
- No installed skill matched strongly enough for this dispatch.

## Evidence Claim Rule

- Any build, test, lint, runtime, UI, or verification claim must cite a concrete command log, screenshot, receipt, or evidence file path.
- Do not write "verified", "tests passed", "build passed", or equivalent claims unless the evidence path exists or the blocker is explicit.
