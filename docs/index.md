# Visible Agent Loop Protocol

VALP is an open protocol for visible, evidence-backed multi-agent automation.

![VALP social preview: Agent says done, VALP asks for proof](assets/social-preview.png)

Start here:

- [Repository README](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/blob/main/README.md)
- [中文注解](zh-CN/README.md)
- [Protocol specification](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/blob/main/SPEC.md)
- [Quickstart](quickstart.md)
- [Minimal audit demo](minimal-audit-demo.md)
- [Correction cycle evidence](correction-cycle.md)
- [Runtime adapters](runtime-adapters.md)
- [Community](community.md)
- [Support](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/blob/main/SUPPORT.md)

The core idea is narrow: a runtime saying `completed` is not enough. VALP
completion requires dispatch receipts, expected evidence, verification/review,
approval gates when needed, and a final synthesis that points to proof.

VALP is currently `0.2.0-draft`. It is a protocol draft plus a reference CLI,
not a hosted production platform.

First useful actions:

- Run `bin/valp audit examples/minimal-task` to inspect the evidence shape.
- Run the [minimal audit demo](minimal-audit-demo.md) to see PASS -> FAIL ->
  PASS when expected evidence is removed and restored.
- Share a real false-done failure case in GitHub Discussions.
- Request or prototype a runtime adapter only after the receipt/evidence gates
  are clear.
