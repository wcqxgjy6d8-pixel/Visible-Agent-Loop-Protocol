# Dispatch: claude

Task: TASK-EXAMPLE-001

Role: read-only reviewer

## Request

Review the implementation evidence and verification output. Report
critical/high findings first. Do not edit source.

## Expected Evidence

- `agents/claude/review.md`

## Recommended Skills

- Skill recommendations are routing aids, not permission grants.
- Task `inspect the requested code change and identify implementation risks` -> skill `systematic-debugging` (auto-load, confidence 0.42, mode auto-load, path `$CODEX_HOME/skills/systematic-debugging/SKILL.md`).

## Evidence Claim Rule

- Any build, test, lint, runtime, UI, or verification claim must cite a concrete command log, screenshot, receipt, or evidence file path.
- Do not write "verified", "tests passed", "build passed", or equivalent claims unless the evidence path exists or the blocker is explicit.
