# Community

VALP is looking for skeptical implementation feedback, not hype. The useful
question is whether the protocol makes agent work more auditable in real
workflows.

## Start Here

For a no-runtime first pass:

```bash
git clone https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol.git
cd Visible-Agent-Loop-Protocol
python -m pip install -r requirements-dev.txt
bin/valp audit examples/minimal-task
```

For repository verification:

```bash
scripts/verify-examples.sh
```

Current discussion entry points:

- [RFC: Phase 0 public evaluation](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/discussions/8)
- [Runtime adapter checklist feedback](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/discussions/9)

Good first GitHub-native tasks:

- [Run the adapter checklist against one runtime](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/issues/10)
- [Add one false-done case to the failure gallery](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/issues/11)
- [Improve the Pages demo for Agent done is not done](https://github.com/wcqxgjy6d8-pixel/Visible-Agent-Loop-Protocol/issues/12)

## Where To Post

Use GitHub Discussions for open-ended feedback:

- whether VALP is useful reliability engineering or unnecessary ceremony;
- RFCs for protocol semantics, schema, evidence, adapter, or governance
  changes;
- runtime adapter ideas before they are ready for an issue;
- task folder or audit experiments that need interpretation;
- workflow stories where a runtime said "completed" before evidence existed.

Use GitHub Issues for concrete work:

- broken docs;
- schema/example inconsistencies;
- CLI or audit behavior that can be reproduced;
- a scoped docs or fixture improvement.

## Best Feedback

The most useful community feedback is concrete:

- a real agent workflow where "completed" did not prove the work was done;
- a runtime adapter that can or cannot export VALP receipts and evidence;
- an example task folder that passes or fails audit for a clear reason;
- schema or documentation wording that produces the wrong mental model;
- an RFC with a small evidence-changing proposal and a testable artifact;
- first-install friction found by running `valp doctor`, preflight, or the
  quickstart.

## Contribution Paths

Good first contributions usually fit one of these shapes:

- improve quickstart or install wording;
- add a small Manual Mode example;
- add or refine a runtime adapter capability checklist;
- add a failure gallery entry with receipts, expected evidence, and audit
  behavior;
- improve `valp doctor` or audit messages;
- tighten schema/example consistency;
- document a comparison against a real agent workflow.

For broad ideas, use GitHub Discussions. Use the RFC template when the proposal
changes protocol semantics, evidence contracts, schemas, adapter requirements,
or governance. For concrete bugs or scoped changes, open an issue. For code or
doc changes, open a pull request and include the verification command you ran.

## Ground Rules

- Keep the protocol runtime-neutral.
- Do not turn HERDR, one terminal app, one model provider, or one local machine
  into a protocol requirement.
- Do not weaken receipt, review, evidence, or approval gates to make an example
  pass.
- Do not claim production readiness or platform support beyond the evidence in
  this repository.
