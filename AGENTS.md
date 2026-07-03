# Agent Instructions

This repository documents an open protocol.

## Scope

- Keep the protocol generic.
- Do not hard-code one user's machine, Desktop path, terminal emulator, project,
  or agent setup.
- Treat HERDR as the reference runtime, not the protocol itself.
- Treat task-skill-router as one optional skill recommendation backend, not the
  protocol itself.

## Editing Rules

- Update `SPEC.md` first when changing protocol semantics.
- Update relevant `docs/` pages after spec changes.
- Update examples and schemas when machine-readable fields change.
- Keep Full Mode, Remote Mode, and Manual Mode clearly separated.
- Keep local overlays separate from protocol semantics.
- Keep capability profiles as routing hints, not fixed assignments.
- Do not weaken receipt semantics.
- Do not weaken approval gates.
- Do not claim platform support beyond current runtime documentation.

## Verification

Before considering protocol edits complete:

```bash
python3 -m json.tool examples/context-policy.json >/dev/null
python3 -m json.tool examples/routing.json >/dev/null
python3 -m json.tool schemas/capabilities.schema.json >/dev/null
python3 -m json.tool schemas/local-overlay.schema.json >/dev/null
python3 -m json.tool schemas/routing-feedback.schema.json >/dev/null
python3 -m json.tool schemas/state.schema.json >/dev/null
python3 -m json.tool schemas/routing.schema.json >/dev/null
python3 -m json.tool schemas/receipts.schema.json >/dev/null
python3 -m json.tool schemas/evidence-status.schema.json >/dev/null
python3 -m json.tool schemas/skill-recommendations.schema.json >/dev/null
bin/valp audit examples/full-mode-task >/dev/null
python3 -m unittest tests/test_valp_audit.py tests/test_valp_workflow.py
```
