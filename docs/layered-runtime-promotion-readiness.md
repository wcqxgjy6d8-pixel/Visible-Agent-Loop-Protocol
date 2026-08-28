# Layered Runtime Promotion Readiness

This matrix separates implemented source contracts from the evidence required
to promote them. A green row does not imply that later rows are green.

| Gate | Current result | Required evidence |
|---|---|---|
| Layered machine contracts | Pass on the current source tree | Pure Kernel replay and control, durable Kernel/receipt/effect stores, ABI 1.0, adopted LangGraph/HERDR/Queue/Manual paths, false-Done prevention, multi-frontier wait/wake, and continuation preparation are covered by the canonical repository suite |
| LangGraph cancellation effect | Pass in source tests | An accepted Kernel obligation routes to one exact run only after approval, requires a terminal `interrupted` observation, writes ABI cancellation proof, fulfills the effect ledger, and does not resend on exact retry; production hosting remains unproven |
| Queue lifecycle and cancellation effect | Pass in repository tests | Claim/cancel share one CAS frontier; terminal observation binds the persisted claim; claimed cancellation remains pending until exact worker acknowledgement; proof bytes fulfill the Kernel effect once and fail closed under reconciliation |
| v0.3.0 release verification | Pass | `scripts/verify-examples.sh`, profile-scoped smoke checks, bundled audits, wheel smoke, `git diff --check`, and same-commit Linux/macOS/Windows CI passed for the released commit |
| Same-commit Linux and Windows verification | Pass for v0.3.0 | Required CI passed against the exact reviewed merge commit; this does not prove every future runtime or deployment |
| Live HERDR Full Mode E2E | Pending | A sanitized task must show atomic submission, a strictly later identity-bound `idle` or `blocked` terminal observation, expected Evidence, receipt completion or blocking, and final strict audit |
| Local Queue worker E2E | Pass in a fresh subprocess regression | A separate local worker process claims the accepted transaction, writes expected Evidence and a claim-bound terminal observation, and the observer closes `dispatch_completed`; production daemon hosting and same-commit Windows proof remain pending |
| Local live provider continuation | Pass for subprocess crash recovery and the live HERDR normal path | One external provider process durably consumes the exact wake; an injected post-consumption crash leaves the invocation intent; restart uses provider `status` without a second `submit`, validates the immutable receipt, and reaches exactly one `resume_consumed`. A current-host HERDR 0.8 / protocol 19 `coordinator.continue` run also produced a CodexPlusPlus invocation receipt for `VALP-HERDR-AUTO-CONTINUATION-20260821`, validated the six-event ledger through `resume_consumed`, and replayed the completed ledger without a second HERDR call. Source tests prove HERDR inflight recovery by replaying the exact idempotency key because HERDR 0.8 exposes no separate status method. Live HERDR crash injection, production provider hosting, cross-platform runtime proof, and soak remain pending |
| Independent public review and strict audit | Pass for v0.3.0 | PR #35 exposed reviewable source, resolved findings, and strict audit evidence |
| Install, publish, merge, and release | Complete for v0.3.0 | PR #35 merged to `main`, tag `v0.3.0` and GitHub Release were published, and post-release smoke passed |

The implementation is therefore machine-contract complete for the declared
dependency-ready runtime slice and released as `v0.3.0`. It is not a claim of
production-, hosted-runtime-, or universal platform completeness.
