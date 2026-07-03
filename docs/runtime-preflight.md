# Runtime Preflight

Runtime preflight checks whether a selected agent can actually receive and show
work before VALP sends a dispatch.

It exists because pane-based agents can be technically running while their UI is
too small, detached, stale, or unable to render useful output.

## Required Checks

Full Mode adapters should record:

```text
runtime status
restart/update-needed status
agent pane id
agent status
terminal width and height, when available
minimum terminal size expected by that agent
CLI availability or version probe, when available
known runtime limitations
```

If a value cannot be read, record `unknown`.

## Pane Size

Terminal TUI agents may fail or show a blank screen when panes are too small.
The adapter should compare current pane size with an agent-specific minimum:

```json
{
  "agent": "agy",
  "terminal_size": {"width": 70, "height": 46},
  "min_terminal_size": {"width": 70, "height": 24},
  "terminal_size_status": "pass"
}
```

If `terminal_size_status` is `fail`, Full Mode dispatch should stop until the
pane is resized, zoomed, moved, or replaced.

## CLI Surface

The reference CLI exposes:

```bash
bin/valp preflight --agent agy
bin/valp preflight --agent codex --agent claude --json
```

`valp dispatch --submit` also writes:

```text
<task>/runtime-preflight.json
```

and refuses to submit when a selected agent has a failing preflight check.

## Evidence Rule

Preflight proves runtime readiness only. It does not prove the task was completed
and does not replace dispatch receipts or expected evidence.
