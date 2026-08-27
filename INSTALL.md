# Installation Guide

Checked against HERDR public documentation, immutable release metadata, and
repository history on 2026-07-28.

Sources:

- https://herdr.dev/docs/install/
- https://herdr.dev/docs/windows-beta/
- https://github.com/ogulcancelik/herdr
- https://github.com/ogulcancelik/herdr/releases/tag/v0.7.5

License boundary: the immutable HERDR `v0.7.5` tag and Homebrew stable artifact
are `AGPL-3.0-or-later` with a commercial license option. Upstream `master` was
relicensed to `Apache-2.0` by commit `cd5ea1be0e69` on 2026-07-22, after that
release. Check the exact artifact you install; the current branch license does
not retroactively change a tagged release.

## Default Path: Full Mode

VALP is designed for automated multi-agent collaboration. The default user path
is Full Mode: install HERDR or another VALP-compatible runtime before running
multi-agent tasks.

For working on this repository's reference CLI locally:

```bash
python -m pip install --upgrade pip setuptools
python -m pip install -e ".[dev]"
valp audit examples/minimal-task
scripts/verify-examples.sh
```

This installs the `valp` console script from the local checkout and the
development dependency used by the repository smoke check. The VALP package
includes its HERDR bridge, so clean installs do not need a separate
`herdr-loop` command. HERDR itself remains an external reference runtime and is
not installed or replaced by VALP.

For the `0.3.0rc1` candidate, the exact-SHA source checkout or release source
archive is the complete protocol distribution. It contains `SPEC.md`, schemas,
docs, examples, tests, and the reference CLI. A Python wheel is a CLI-only
artifact and does not replace that protocol bundle. Installed wheel provenance
is recorded as `unavailable` rather than inventing a Git revision; the release
manifest must bind the wheel digest to the reviewed source SHA.

## First Run Health Gate

The first action after installing VALP should be diagnosis, not dispatch. This
is especially important for App-managed installs, where the App may install a
CLI wrapper, create symlinks, or manage a hidden checkout path.

Recommended first-run order:

```text
1. Resolve the actual VALP install root and `valp` executable path.
2. Run `valp doctor --workspace <install-root> --json` to commission
   capability passports for every discovered Agent surface/session.
3. Show the passports and let the user choose the Leader. Run `valp leader
   select <principal>`, `valp leader start`, `valp leader show`, and `valp
   leader open`.
4. Run `valp publish TASK-001 --workspace <workspace> --prompt "..."`.
5. Let the Leader author assignments, then run `valp route TASK-001 --workspace
   <workspace> --assignments <declaration>`.
6. Inspect `valp dispatch TASK-001 --workspace <workspace>` as a dry run.
7. After explicit user approval, run `valp dispatch TASK-001 --workspace
   <workspace> --submit`.
8. Confirm the terminal shows the installation-owned Leader pane and a fresh
   task-owned Worker pane.
9. Require identity-bound `dispatch_submitted`, then expected evidence and
   `dispatch_completed`.
10. Run independent review and resolve recommendations.
11. Run `valp audit <workspace> --task TASK-001`; `fail_count` must be 0.
```

An installer or App must not hard-code a Desktop checkout path. It should store
the actual install root it created and verify that `valp doctor` can find the
protocol checkout, Python runtime, examples, schemas, and reference adapters.

A dry run may create a task folder. It prints submit commands only after a
user-selected Leader declaration passes validation. It must not actually send
work to Agents and must not be reported as a completed task. Newly published
dry-run tasks normally fail `valp audit` because receipts, expected evidence,
and final synthesis do not exist yet.

Doctor does not choose the Leader or task Agents. Missing evidence stays
`unknown`; installers must not infer a model from the Agent product name. The
Leader declares task roles, and VALP may validate or block that declaration but
cannot replace an Agent.

For HERDR, preflight probes command help rather than assuming capabilities from
a version number. Full Mode requires a structured `herdr agent get` baseline
and `herdr agent prompt <target> <payload> --wait --until working --timeout
<ms>`. The `agent_prompted` response must preserve the routed Agent identity
and advance integer `state_change_seq`. Older `herdr pane send-text`, `herdr
pane send-keys`, and `herdr agent wait` fallback is transport only: VALP records
`dispatch_inserted`, stops as `Manual-degraded`, and does not record
`dispatch_submitted`. Use Manual Mode until the runtime is updated or another
compatible adapter is selected.

For the fastest stable setup:

```text
Linux/macOS -> official installer
Windows stable workflow -> SSH into a Linux/macOS HERDR host
Windows local workflow -> native Windows beta, clearly marked preview
Windows without HERDR -> Manual Mode today; runner/queue adapter when available
No runtime -> Manual Mode only
```

VALP Full Mode requires HERDR or a VALP-compatible runtime. It does not
require a specific terminal emulator.

Manual Mode is a valid learning and audit path for environments where a
compatible runtime is not installed. It does not provide automatic dispatch
proof, status waits, or runtime-backed receipt guarantees.

## Platform Quick Start

| System | Recommended install | Mode |
|---|---|---|
| macOS | `curl -fsSL https://herdr.dev/install.sh | sh` or Homebrew | Full Mode |
| Linux | `curl -fsSL https://herdr.dev/install.sh | sh` or package manager | Full Mode |
| Windows stable workflow | SSH to Linux/macOS host, run `herdr` there | Remote Mode |
| Windows local workflow | PowerShell preview installer | Windows beta |
| Windows without HERDR | No HERDR install | Manual Mode today; runner/queue adapter when implemented |
| No compatible runtime | No install path | Manual Mode; evidence only |

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

### Windows Without HERDR

Windows Terminal, PowerShell, and CMD can host visible agent sessions, but they
do not by themselves provide dispatch receipts, status waits, expected evidence
checks, or timeout handling.

For no-HERDR Windows use today, start with Manual Mode. For automated local
Windows work, a VALP-compatible runner or queue adapter should be used when one
is available. The runner should write receipts and evidence into the task
folder; the terminal should be treated as display only.

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

Use Manual Mode for learning, documentation, external review, or temporary audit
trails. Do not present it as the normal automated multi-agent experience.

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
  -> Manual Mode only; evidence trail without runtime proof
```
