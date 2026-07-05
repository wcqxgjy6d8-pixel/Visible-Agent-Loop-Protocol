# Visible Routing

Task: TASK-QUEUE-001
Profile: software-code
Loop layer: agentic_coding_loop
Design contract: not_applicable

## Attention Heads

- state_gate: codex (score 0.89, selected)
- implementation: codex (score 0.89, selected)
- ux_review: claude (score 0.84, selected)
- prototype: none (score n/a, not_selected)

## Selected Context

- `task.md`: active task brief
- `routing.json`: candidate scores and selected agents
- `skill-recommendations.json`: recommended installed skills

## Masked Inputs

- old chat memory without file-backed evidence: stale context is not valid routing or completion evidence
- hidden votes, hidden reviews, or hidden routing decisions: VALP requires visible decision input
- Agy prototype output as production proof: prototype evidence can inform implementation but cannot satisfy build/test/release gates
- release, signing, upload, deploy, auth, secrets, or destructive changes: high-risk operations require explicit user approval
- invalid, superseded, rejected, or blocked evidence: these evidence statuses do not satisfy done criteria

## Evidence Board

- routing decision is visible: recorded
- selected agents have visible dispatches: recorded
- runtime or build success: verified
