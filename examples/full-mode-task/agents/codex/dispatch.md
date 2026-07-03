# Dispatch: codex

Task: TASK-EXAMPLE-001

Role: implementation and verification

## Request

Fix the synthetic failing unit test in the sample project. Keep the change
minimal. Run the relevant test command and write verification evidence.

## Boundaries

- Do not delete files.
- Do not change secrets, auth, release settings, or deployment settings.
- Do not publish or upload anything.

## Expected Evidence

- `agents/codex/evidence.md`
- `evidence/verification.md`

## Recommended Skills

- Skill recommendations are routing aids, not permission grants.
- Task `inspect the requested code change and identify implementation risks` -> skill `systematic-debugging` (auto-load, confidence 0.42, mode auto-load, path `$CODEX_HOME/skills/systematic-debugging/SKILL.md`).
- Task `run build, lint, or tests and write verification evidence` -> skill `verification-before-completion` (auto-load, confidence 0.39, mode auto-load, path `$CODEX_HOME/skills/verification-before-completion/SKILL.md`).

## Evidence Claim Rule

- Any build, test, lint, runtime, UI, or verification claim must cite a concrete command log, screenshot, receipt, or evidence file path.
- Do not write "verified", "tests passed", "build passed", or equivalent claims unless the evidence path exists or the blocker is explicit.
