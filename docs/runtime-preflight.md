# Runtime Preflight

Runtime preflight checks whether a selected agent can actually receive work
before VALP sends a dispatch.

It exists because pane-based agents can be technically running while their UI is
too small, detached, stale, or unable to render useful output. Headless agents
can fail differently: the queue might be unavailable, no worker might be idle,
or output refs might be missing.

## Required Checks

Full Mode adapters should record adapter-specific readiness evidence.

Pane-controller adapters should record:

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

Headless, daemon queue, hosted, or remote adapters should record:

```text
runtime status
queue, job, run, or session id
worker id or hosted runner id
session status
dispatch payload ref
output or artifact ref
expected evidence refs
retry state or failure reason, when applicable
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

Non-pane adapters should not invent terminal-size fields. Their preflight
passes or fails on queue/session readiness and expected output/evidence refs.

## CLI Surface

The reference CLI exposes:

```bash
bin/valp preflight --runtime herdr --agent agy
bin/valp preflight --runtime queue --agent codex --agent claude --json
```

`valp dispatch --submit` also writes:

```text
<task>/runtime-preflight.json
```

and refuses to submit when a selected agent has a failing preflight check.

## Evidence Rule

Preflight proves runtime readiness only. It does not prove the task was completed
and does not replace dispatch receipts or expected evidence.
