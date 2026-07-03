# Installation Guide

Checked against HERDR public documentation on 2026-07-03.

Sources:

- https://herdr.dev/docs/install/
- https://herdr.dev/docs/windows-beta/

## Default Path: Full Mode

VALP is designed for automated multi-agent collaboration. The default user path
is Full Mode: install HERDR or another VALP-compatible runtime before running
multi-agent tasks.

For the fastest stable setup:

```text
Linux/macOS -> official installer
Windows stable workflow -> SSH into a Linux/macOS HERDR host
Windows local workflow -> native Windows beta, clearly marked preview
No runtime -> Manual Mode only
```

VALP Full Mode requires HERDR or a VALP-compatible runtime. It does not
require a specific terminal emulator.

Manual Mode is only a fallback for environments where a compatible runtime
cannot be installed. It does not provide automatic dispatch proof, status waits,
or runtime-backed receipt guarantees.

## Platform Quick Start

| System | Recommended install | Mode |
|---|---|---|
| macOS | `curl -fsSL https://herdr.dev/install.sh | sh` or Homebrew | Full Mode |
| Linux | `curl -fsSL https://herdr.dev/install.sh | sh` or package manager | Full Mode |
| Windows stable workflow | SSH to Linux/macOS host, run `herdr` there | Remote Mode |
| Windows local workflow | PowerShell preview installer | Windows beta |
| No compatible runtime | No install path | Manual Mode, degraded |

See [docs/platform-support.md](docs/platform-support.md) for detailed platform
notes.

## Linux And macOS

Recommended one-command path:

```bash
curl -fsSL https://herdr.dev/install.sh | sh
```

Then verify:

```bash
herdr
herdr status
```

Update direct installs with:

```bash
herdr update
```

This is the best default for most users because it follows HERDR's own stable
Linux/macOS release channel.

## Homebrew

For users who already manage tools through Homebrew:

```bash
brew install herdr
```

Update through Homebrew:

```bash
brew upgrade herdr
```

Do not mix Homebrew updates with `herdr update`; package-manager installs should
be updated by the package manager.

## mise

For users who already use mise:

```bash
mise use -g herdr
```

If the local mise registry is stale, update mise and retry. HERDR documentation
also mentions a temporary GitHub fallback for older mise versions.

## Nix

For reproducible environments:

```bash
nix profile install github:ogulcancelik/herdr/v0.x.y
```

Replace `v0.x.y` with the desired release tag. Pinning a release tag is better
for teams than tracking `master`.

## Windows

Native Windows support is preview beta.

Stable recommendation for Windows users:

```powershell
ssh you@linux-or-macos-host
herdr
```

This runs HERDR on the remote host where the runtime owns panes, agents,
receipts, and task state.

Native Windows beta:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://herdr.dev/install.ps1 | iex"
```

Use this only when beta limitations are acceptable. The Windows beta uses
Windows ConPTY behavior instead of the Unix PTY model. Some features are beta,
partial, or unsupported.

Important Windows beta caveats from HERDR documentation:

- Windows beta builds use the preview channel.
- Native Windows `herdr --remote` is not part of the beta.
- For remote work from Windows, SSH into the server and run `herdr` there.
- Live handoff is not supported on Windows beta.
- Restart running sessions after updates.

For users who need stable automation from a Windows machine today, use SSH into
a Linux/macOS HERDR host and run the runtime there.

## Terminal Emulator

Do not require a specific terminal app.

Acceptable display shells include:

```text
Ghostty
iTerm
Apple Terminal
Windows Terminal
Linux terminal emulators
remote SSH sessions
```

The required layer is runtime control:

```text
agent list
agent status/read
agent send/insert
pane/message submit
submission proof
status wait
receipt ledger
evidence store
```

## Manual Mode

If HERDR or a VALP-compatible runtime is not installed, the user can still use
Manual Mode:

```text
write task folders
write dispatch files
copy dispatches manually
paste results manually
store evidence manually
```

Manual Mode is not Full Mode. It cannot claim automatic dispatch submission,
agent status proof, or runtime receipt equivalence.

Use Manual Mode for learning, documentation, or temporary audit trails. Do not
present it as the normal automated multi-agent experience.

## Quick Decision Tree

```text
Are you on Linux/macOS and want the fastest setup?
  -> curl installer

Already use Homebrew/mise/Nix?
  -> use your package manager

On Windows and want stable behavior?
  -> SSH to a Linux/macOS HERDR host

On Windows and want local testing?
  -> install Windows beta, mark limitations

No VALP-compatible runtime and cannot install one?
  -> Manual Mode only; degraded workflow
```
