# FAQ

## Is VALP a product?

No. VALP is an open protocol for visible, evidence-backed multi-agent
collaboration. A product or runtime can implement VALP.

## Do I need HERDR?

For the recommended Full Mode path, install HERDR or another VALP-compatible
runtime. HERDR is the reference runtime, not the protocol itself.

## Is HERDR required by the protocol?

No. Full Mode requires HERDR or a VALP-compatible runtime. HERDR is the default
recommended runtime because it is the current reference target.

## What happens if I do not install a runtime?

You can use Manual Mode, but it is degraded. Manual Mode can write task folders
and evidence notes. It cannot prove automatic dispatch submission, wait for
agent status, or generate runtime-backed receipts.

## Is Manual Mode a normal user experience?

No. Manual Mode is for learning, documentation, temporary audit trails, or
environments where a compatible runtime cannot be installed.

## Does VALP require a specific terminal?

No. Ghostty, iTerm, Apple Terminal, Windows Terminal, Linux terminal emulators,
and SSH sessions are display shells. VALP requires runtime capabilities, not a
specific terminal emulator.

## What is Full Mode?

Full Mode is the intended automated workflow. It requires a compatible runtime
that can scan agents, dispatch visibly, prove submission, wait for status, write
receipts, and store evidence.

## What is Remote Mode?

Remote Mode means the compatible runtime runs on a remote machine. The remote
runtime owns the agent state, submission proof, receipts, and evidence store.
Local terminal state is not proof of remote completion.

## What should Windows users do?

For stable automation, SSH into a Linux/macOS host and run HERDR there. Native
Windows HERDR support is preview beta and should be treated as beta until the
specific workflow is verified.

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

## Is this ready for production?

This repository is an initial open protocol draft. Use it as a protocol and
reference structure. Production use depends on a compatible runtime and
adapter quality.
