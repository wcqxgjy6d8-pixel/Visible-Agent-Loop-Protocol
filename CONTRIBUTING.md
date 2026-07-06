# Contributing

Keep the protocol boring, auditable, and implementation-neutral.

## Good First Contributions

Good first issues should improve evidence quality without changing the whole
protocol surface. Useful starter contributions include:

- clearer quickstart wording;
- small Manual Mode examples;
- runtime adapter capability notes;
- schema/example consistency checks;
- `valp doctor` and audit message improvements;
- comparisons against real agent workflows.

If you are unsure where to start, open a GitHub Discussion with the workflow you
want to support and the evidence your runtime can actually export.

## Rules

- Do not hard-code one user's machine, terminal emulator, or project.
- Do not make HERDR CLI behavior part of the abstract protocol unless it is
  described as a reference runtime behavior.
- Keep runtime adapters separate from protocol rules.
- Add schema changes with examples.
- Add tests or self-check fixtures for receipt/gate behavior.
- Treat recommendations as evidence, not authority.

## Pull Requests

Before opening a pull request:

1. State whether the change is protocol semantics, docs, schemas, examples, or
   reference CLI behavior.
2. Link the evidence path or example folder that proves the behavior.
3. Explain any effect on Full Mode, Remote Mode, Manual Mode, receipts, review
   gates, approval gates, or runtime adapters.
4. Run the repository smoke check when possible:

```bash
scripts/verify-examples.sh
```

Docs-only changes can explain why the smoke check was not run, but protocol,
schema, CLI, and example changes should include verification output.

## Change Checklist

- Does the change preserve Full Mode and Manual Mode distinction?
- Does the change avoid hidden decision input?
- Does the change keep receipt semantics clear?
- Does the change preserve cross-platform wording?
- Does the change include evidence paths?
- Does the change avoid claiming a CLI/runtime feature that is not implemented?
- Does the change update quickstart, FAQ, or examples when user-facing behavior changes?
