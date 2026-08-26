# Cost Governance

VALP cost governance is task-local and provider-neutral. It is a reproducible
accounting projection, not a provider integration or a claim about current
public pricing.

`pricing-snapshots.json` carries immutable token-rate snapshots. Every snapshot
contains separate `official_list`, `relay_account`, and
`subscription_marginal` rates. `usage-events.jsonl` and
`billing-events.jsonl` are append-only evidence logs with unique event ids.

`cost-budget.json` may declare planned token use and an official-list projected
ceiling. `bin/valp cost report <task>` calculates accumulated and projected
estimates per agent and in total. Actual billed amounts are shown only from
billing events; no billing event produces `actual_billed: null`.

Each usage event may bind its spend to `dispatch_id`, `work_item_id`, a prior
usage event through `retry_of`, and a spawning Agent through `parent_agent`.
The deterministic report preserves these links in `usage_attribution`, so retry
and sub-Agent cost remain visible instead of being folded into an unexplained
per-Agent total.

`bin/valp audit` fails when a cost report differs from its snapshots/events,
contains an actual charge without billing evidence, duplicates event ids, or
exceeds a declared projected official-list budget.
