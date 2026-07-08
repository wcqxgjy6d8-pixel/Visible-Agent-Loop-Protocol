# Visible Agent Loop Protocol

Agent says done. VALP asks for proof.

VALP is an open protocol for visible, evidence-backed multi-agent automation.

![VALP audit demo: PASS to FAIL to PASS](assets/valp-audit-demo.svg)

Start here:

- [Repository README](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/blob/main/README.md)
- [中文注解](zh-CN/README.md)
- [Protocol specification](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/blob/main/SPEC.md)
- [Quickstart](quickstart.md)
- [When Agent "Done" Is Not Done](when-agent-done-is-not-done.md)
- [Minimal audit demo](minimal-audit-demo.md)
- [Failure gallery](failure-gallery.md)
- [Correction cycle evidence](correction-cycle.md)
- [Runtime adapter checklist](adapter-checklist.md)
- [Runtime adapters](runtime-adapters.md)
- [Community](community.md)
- [Support](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/blob/main/SUPPORT.md)

The core idea is narrow: a runtime saying `completed` is not enough. VALP
completion requires dispatch receipts, expected evidence, verification/review,
approval gates when needed, and a final synthesis that points to proof.

VALP is currently `0.2.0`. It is an open protocol release plus a reference CLI,
not a hosted production platform.

First useful actions:

- Run `bin/valp audit examples/minimal-task` to inspect the evidence shape.
- Read [When Agent "Done" Is Not Done](when-agent-done-is-not-done.md) for the
  shortest public explanation.
- Run the [minimal audit demo](minimal-audit-demo.md) to see PASS -> FAIL ->
  PASS when expected evidence is removed and restored.
- Read the [failure gallery](failure-gallery.md) to see what VALP catches.
- Use the [adapter checklist](adapter-checklist.md) before claiming runtime
  compatibility.
- Share a real false-done failure case in GitHub Discussions.
- Request or prototype a runtime adapter only after the receipt/evidence gates
  are clear.

Active discussions:

- [RFC: Phase 0 public evaluation](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/discussions/8)
- [Runtime adapter checklist feedback](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/discussions/9)

Good first tasks:

- [Run the adapter checklist against one runtime](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/issues/10)
- [Add one false-done case to the failure gallery](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/issues/11)
- [Improve the Pages demo](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/issues/12)
