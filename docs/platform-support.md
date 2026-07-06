# Platform Support

VALP is cross-platform as a protocol. Full Mode depends on the runtime adapter
available on the user's machine or remote host.

The default recommended runtime is HERDR. Platform support below reflects HERDR
public documentation and repository metadata checked on 2026-07-06.

Sources:

- https://herdr.dev/docs/install/
- https://herdr.dev/docs/windows-beta/
- https://herdr.dev/
- https://github.com/ogulcancelik/herdr

## Recommended Matrix

| User system | Recommended VALP path | Expected mode |
|---|---|---|
| macOS Apple silicon | HERDR stable installer or Homebrew | Full Mode |
| macOS Intel | HERDR stable installer or Homebrew | Full Mode |
| Linux x86_64 | HERDR stable installer, manual binary, or package manager | Full Mode |
| Linux aarch64 | HERDR stable installer, manual binary, or package manager | Full Mode |
| Windows, stable workflow | SSH into Linux/macOS host and run HERDR there | Remote Mode with Full Mode guarantees on remote host |
| Windows, local workflow | Native HERDR Windows preview beta | Full Mode only where beta features satisfy adapter requirements |
| Windows, no HERDR | Manual Mode today; runner/queue adapter is planned | Manual Mode now, future adapter when implemented |
| Any system without compatible runtime | Manual folders, attestations, and evidence only | Manual Mode |

## macOS

Recommended:

```bash
curl -fsSL https://herdr.dev/install.sh | sh
herdr status
```

Homebrew users may prefer:

```bash
brew install herdr
herdr status
```

macOS users should generally be directed to Full Mode first.

## Linux

Recommended:

```bash
curl -fsSL https://herdr.dev/install.sh | sh
herdr status
```

Package-manager options can be used when the team already standardizes on
Homebrew-on-Linux, mise, or Nix.

Linux users should generally be directed to Full Mode first.

## Windows

There are three Windows paths.

Stable workflow:

```powershell
ssh you@linux-or-macos-host
herdr
```

This runs the runtime on the remote host. The remote host owns panes, agent
state, receipts, and task evidence.

Local beta workflow:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://herdr.dev/install.ps1 | iex"
herdr
```

Native Windows HERDR support is preview beta. Treat local Windows Full Mode as
available only when the specific workflow has been verified on that machine.

Important caveats from HERDR Windows beta documentation include:

- Windows uses ConPTY and Windows process/runtime behavior instead of Unix PTY.
- Native Windows beta is preview-only.
- Native Windows `herdr --remote` is not supported.
- Direct terminal attach is not supported in the Windows beta.
- Live server handoff is not supported in the Windows beta.
- Remote work from Windows should use SSH into the server and run HERDR there.

No-HERDR local workflow:

Windows Terminal, PowerShell, and CMD can be used as display shells. A terminal
can show multiple panes, but pane layout is not VALP runtime proof. Without
HERDR, a Windows automation path should use a runner or queue adapter that:

```text
starts one or more agent sessions
reads dispatch work from task-local files or JSONL queues
writes dispatch receipts
writes expected evidence
records timeout, blocked, and late-evidence states
lets valp audit decide completion
```

Do not treat UI keystroke automation into a terminal pane as Full Mode proof.
If no runner/queue adapter is installed, use Manual Mode.

## Manual Mode

Manual Mode is a normal way to learn or adopt the evidence discipline. It is not
the normal automated Full Mode experience.

Use it only when:

```text
HERDR cannot be installed
no VALP-compatible runtime exists
the user needs documentation, PR review evidence, or an audit template
the task is temporary and does not require automatic dispatch proof
```

Manual Mode does not provide:

```text
automatic agent scan
dispatch submission proof
status wait
runtime receipt ledger
automatic evidence gates
```

## Documentation Rule

Public VALP documentation should distinguish the automated Full Mode path from
the no-runtime Manual Mode path, and must not imply Manual Mode provides runtime
proof.

Repository CI and `scripts/verify-examples.sh` prove the reference CLI, schemas,
unit tests, and bundled examples pass their audit gates. The public workflow
runs that proof on Linux, macOS, and Windows runners. This does not prove native
Full Mode support for every platform. Platform Full Mode claims still require
runtime-adapter evidence on that specific local or remote host.
