# Runtime Preflight

Runtime preflight checks whether a Leader-declared Agent can actually receive
work before VALP sends a dispatch.

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
submission capability and selected transport mode
project/task-owned session provisioning capability
task-owned runtime workspace scope
owned-session binding ref, generation, and identity token
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

and refuses to submit when a Leader-declared Agent has a failing preflight
check. It reports the blocker to the Leader; it does not choose a replacement.
For HERDR, an installed CLI is insufficient by itself: preflight also requires
atomic `agent prompt` or the complete `pane send-text` + `pane send-keys` +
`agent wait` fallback, plus `workspace create`, `agent start`, and `pane move`
for isolated project/task-owned worker provisioning. Capability is detected
from command help, not assumed from the HERDR version.

For a submitted HERDR dispatch, preflight resolves the worker through
`agent-sessions.json`. It does not select a pane by Agent label. The fresh pane,
terminal, workspace/tab, Agent, and cwd facts must match the recorded binding.
The adapter queries the recorded workspace directly instead of depending on a
potentially truncated global pane list. The route-time pane scan remains
capability discovery only.

Before HERDR provisioning, the coordinator also resolves a bare launch command
to an absolute executable path. This is a runtime-boundary check: the HERDR
daemon is not assumed to inherit the coordinator's `PATH`. The command comes
from the selected Agent's observed `runtime.launch_argv` capability or explicit
adapter configuration; VALP does not guess it from the Agent name.
When `runtime.version_command` is declared, preflight runs that exact argv. It
does not append `--version` or infer another probe convention.

After a new owned Agent starts, preflight may repeat read-only structured
runtime metadata observation for a fixed bounded window. Unstructured pane,
footer, transcript, and dispatch text are not model evidence. It records the
attempt count in `owned_session_model_readiness`. Timeout leaves dispatch
blocked; an observed ineligible model is not retried as readiness.

## Evidence Rule

Preflight proves runtime readiness only. It does not prove the task was completed
and does not replace dispatch receipts or expected evidence.
