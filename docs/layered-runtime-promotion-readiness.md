# Layered Runtime Promotion Readiness

This matrix separates implemented source contracts from the evidence required
to promote them. A green row does not imply that later rows are green.

| Gate | Current result | Required evidence |
|---|---|---|
| Layered machine contracts | Pass on the current source tree | Pure Kernel replay and control, durable Kernel/receipt/effect stores, ABI 1.0, adopted LangGraph/HERDR/Queue/Manual paths, false-Done prevention, multi-frontier wait/wake, and continuation preparation are covered by the canonical repository suite |
| LangGraph cancellation effect | Pass in source tests | An accepted Kernel obligation routes to one exact run only after approval, requires a terminal `interrupted` observation, writes ABI cancellation proof, fulfills the effect ledger, and does not resend on exact retry; production hosting remains unproven |
| Queue lifecycle and cancellation effect | Pass on current macOS source tree | Claim/cancel share one CAS frontier; terminal observation binds the persisted claim; claimed cancellation remains pending until exact worker acknowledgement; proof bytes fulfill the Kernel effect once and fail closed under reconciliation |
| Current macOS verification | Pass on Python 3.9.6 and 3.12.13 | `scripts/verify-examples.sh`: 662 tests, 11/11 conformance checks, and all bundled audits pass; `git diff --check` and `python3 -m compileall -q valp_cli tests` pass |
| Same-commit Linux and Windows verification | Pending | Required CI must run against the exact candidate commit; prior commits and a local macOS run do not transfer |
| Live HERDR Full Mode E2E | Pending | A sanitized task must show atomic submission, a strictly later identity-bound `idle` or `blocked` terminal observation, expected Evidence, receipt completion or blocking, and final strict audit |
| Local Queue worker E2E | Pass in a fresh subprocess regression | A separate local worker process claims the accepted transaction, writes expected Evidence and a claim-bound terminal observation, and the observer closes `dispatch_completed`; production daemon hosting and same-commit Windows proof remain pending |
| Local live provider continuation | Pass in a fresh subprocess crash-recovery regression | One external provider process durably consumes the exact wake; an injected post-consumption crash leaves the invocation intent; restart uses provider `status` without a second `submit`, validates the immutable receipt, and reaches exactly one `resume_consumed`. Production provider and HERDR runtime-control proof remain pending |
| Independent review and task-local strict audit | Pass for `VALP-PROTOCOL-LAYER-CLOSURE-20260811` | Formal Claude review, six resolved recommendations, correction-cycle evidence, and task-local audit at 28 pass / 1 explained runtime-preflight warning / 0 fail / 4 skip |
| Install, publish, merge, and release | Not authorized | Each action remains a separate approval and provenance gate |

The implementation is therefore locally machine-contract complete for the
declared dependency-ready runtime slice. It is not yet production-, platform-,
or promotion-complete.
