# FAQ

## Is VALP a product?

No. VALP is an open protocol for visible, evidence-backed multi-agent
collaboration. A product or runtime can implement VALP.

## Do I need HERDR?

For the documented Full Mode path in this repository, install HERDR. HERDR is
the current reference runtime, not the protocol itself. Other runtimes can
implement VALP, but this repository documents HERDR as the reference
implementation today.

## Is HERDR required by the protocol?

No. Full Mode requires a VALP-compatible runtime. HERDR is the current reference
target, but the protocol allows daemon queues, hosted platforms, remote
runtimes, or other adapters when they export the required evidence.

## Is HERDR closed source?

No. As checked on 2026-07-06, HERDR has a public source repository at
<https://github.com/ogulcancelik/herdr>. The repository contains source and
project files, and its license text says AGPL-3.0-or-later for open-source use
plus a commercial license option.

This does not make HERDR a VALP requirement. It only means the current reference
runtime is public rather than closed.

## What happens if I do not install a runtime?

You can use Manual Mode. It can write task folders, visible dispatch records,
manual attestations, and evidence notes. It cannot prove automatic dispatch
submission, wait for agent status, or generate runtime-backed receipts.

## Is Manual Mode a normal user experience?

Manual Mode is a normal learning and adoption path, but not a Full Mode
automation path. Use it for documentation, PR reviews, temporary audit trails,
or environments where a compatible runtime cannot be installed.

## Does VALP require a specific terminal?

No. Ghostty, iTerm, Apple Terminal, Windows Terminal, Linux terminal emulators,
and SSH sessions are display shells. VALP requires runtime capabilities, not a
specific terminal emulator.

## What is Full Mode?

Full Mode is the intended automated workflow. It requires a compatible runtime
that can scan agents, dispatch visibly, prove submission, wait for status, write
receipts, and store evidence.

## What is Auto Visible Mode?

Auto Visible Mode is opt-in automatic task intake. A project policy or runtime
watcher may decide that a user request should publish a VALP task, then show the
trigger reason, routing, skill recommendations, dispatches, evidence gates, and
final report path. It does not bypass approval gates or completion evidence.

## Does Auto Visible Mode mean VALP runs silently in the background?

No. The trigger can be automatic, but the decision and evidence must be visible.
High-risk work still requires explicit approval before execution.

## What is Remote Mode?

Remote Mode means the compatible runtime runs on a remote machine. The remote
runtime owns the agent state, submission proof, receipts, and evidence store.
Local terminal state is not proof of remote completion.

## What should Windows users do?

For stable automation, SSH into a Linux/macOS host and run HERDR there. Native
Windows HERDR support is preview beta and should be treated as beta until the
specific workflow is verified.

Windows users who do not want HERDR can use Manual Mode today. Windows Terminal
can display multiple panes, but it is not a VALP runtime by itself. A no-HERDR
automated Windows path needs a runner or queue adapter that writes receipts,
expected evidence, and auditable state.

## Does a runtime's "completed" state mean VALP is done?

No. Runtime completion is only one signal. VALP completion requires receipts,
expected evidence, verification, review, approval resolution, and final
synthesis.

## What is a dispatch receipt?

A machine-readable record of dispatch state. Valid states are:

```text
dispatch_written
dispatch_inserted
dispatch_submitted
dispatch_completed
dispatch_blocked
```

## Why is inserted text not delivery?

Because text can appear in an input box without being submitted. VALP treats
that as `dispatch_inserted`, not `dispatch_submitted`.

## What is a provider matrix?

A current capability record for agent backends: CLI availability, MCP support,
skill discovery path, session resume support, approval behavior, model
selection, context policy, and known limitations.

## What is a local overlay?

A local overlay is machine or workspace-specific configuration: agent names,
likely strengths, skill paths, runtime preferences, context limits, and folder
conventions. It is allowed to guide routing, but it cannot override protocol
gates. Agent profiles are hints, not fixed assignments.

## Can VALP learn which agent is best?

Yes, through routing feedback. After meaningful tasks, the runtime can record
which agents were selected, what evidence they produced, what failed, and what
should change next time. That history improves future routing, but every task
still runs fresh capability, context, provider, and permission scans.

## Can a skill router decide the agent?

No. Skill recommendation is evidence, not authority. It cannot bypass role
boundaries, context policy, approval gates, receipt gates, or verification.

## What is squad routing?

An optional routing pattern where a leader agent chooses a member agent for a
task. The leader's decision is routing evidence, not completion evidence.

## Is VALP tied to one project?

No. Profiles adapt VALP to domains such as software-code, research,
web-frontend, apple-app, documents, prototypes, and operations.

## Are the public examples real task case studies?

Mostly no, with one exception. The minimal, Full Mode, and headless queue
examples are synthetic fixtures. `examples/real-doc-calibration-task/` is a
sanitized real Manual Mode documentation case study.

There is still no public live Full Mode case study with runtime submission
proof.

## What test coverage exists today?

The repository currently tests audit behavior, workflow creation/routing,
doctor diagnostics, schema validation, and bundled examples. It does not yet
provide a live-runtime E2E suite for every adapter class, full state-machine
transition, context compression path, or Auto Visible runtime watcher.

## Is this ready for production?

This repository is an initial open protocol draft and reference CLI. Use it as a
protocol and evidence structure. Production use depends on a compatible runtime,
adapter quality, live E2E verification, and local operational controls.
