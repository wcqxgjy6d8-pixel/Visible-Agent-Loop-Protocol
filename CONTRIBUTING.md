# Contributing

Keep the protocol boring, auditable, and implementation-neutral.

## Rules

- Do not hard-code one user's machine, terminal emulator, or project.
- Do not make HERDR CLI behavior part of the abstract protocol unless it is
  described as a reference runtime behavior.
- Keep runtime adapters separate from protocol rules.
- Add schema changes with examples.
- Add tests or self-check fixtures for receipt/gate behavior.
- Treat recommendations as evidence, not authority.

## Change Checklist

- Does the change preserve Full Mode and Manual Mode distinction?
- Does the change avoid hidden decision input?
- Does the change keep receipt semantics clear?
- Does the change preserve cross-platform wording?
- Does the change include evidence paths?

