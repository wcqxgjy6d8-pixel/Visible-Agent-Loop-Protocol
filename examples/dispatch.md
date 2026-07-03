# Dispatch: claude

Task:
TASK-001

Profile:
software-code

Role:
read-only reviewer

Capability match:
architecture review, risk review, correctness review

Permission boundary:
Do not edit source. Do not claim runtime facts without command evidence.

Context policy:
soft_warning_pct=60; hard_compression_pct=70; emergency_stop_pct=80;
checkpoint_interval_minutes=45; compression_target_pct=15-25

Context to inspect:
task.md, routing.json, codex verification evidence, changed files

Question:
Review the implementation for correctness, architecture risk, and missing
verification. Report critical/high findings first.

Expected output:
agents/claude/visible-review.md

