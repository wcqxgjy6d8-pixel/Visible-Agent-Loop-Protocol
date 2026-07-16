# Provider Matrix

The provider matrix records what each agent backend can actually do now.

It is scanned before routing. It is not assumed from memory.

## Minimum Fields

```json
{
  "provider_name": "codex",
  "provider_version_or_runtime_report": "unknown",
  "cli_available": true,
  "mcp_support": "supported|unsupported|unknown",
  "skill_discovery_path": "$CODEX_HOME/skills",
  "session_resume_support": "supported|unsupported|unknown",
  "approval_behavior": "manual|auto|policy|unknown",
  "model_selection": "observed_model|runtime_observed|unknown",
  "max_concurrency": 1,
  "context_policy": {},
  "runtime_preflight": {},
  "known_limitations": [],
  "last_verified_at": "2026-07-03T00:00:00Z"
}
```

Use `unknown` instead of guessing.

## Model-Aware Identity

For model-aware routing, each provider record also carries the routing identity:
agent_surface, declared_model, observed_model, provider, reasoning_mode,
permissions, context, task_evidence, model_mismatch, and
model_evidence_status.

declared_model and observed_model are separate objects. Both include model_id,
provider, reasoning_mode, source, timestamp, confidence, and freshness.
Runtime-observed identity wins for the current route; a declaration is not
runtime proof.

If the identities differ, model-bound capability history is invalidated.
Dynamic stale, unsupported, session-unbound, or unknown observations invalidate
model-bound history; legacy low-confidence records remain downgraded. Unknown
stays explicit and cannot qualify as strong evidence for high-risk
implementation or final review.

New dynamic matrices set `model_awareness.dynamic_discovery_required` to true.
Each selected provider then records a closed `valp-model-probe.v1` result from
adapter-visible metadata:

```json
{
  "status": "observed|unsupported|unavailable|error",
  "source": "runtime adapter metadata",
  "observed_at": "2026-07-15T12:00:00Z",
  "ttl_seconds": 3600,
  "model": {
    "model_id": "unknown",
    "provider": "unknown",
    "reasoning_mode": "unknown",
    "confidence": "unknown"
  },
  "session_identity": {
    "status": "unknown",
    "token": "unknown",
    "source": "runtime adapter metadata",
    "generation": "unknown"
  }
}
```

The TTL defaults to 3600 seconds and is bounded to 60 through 86400 seconds.
`freshness` is recomputed from `observed_at` whenever routing or audit runs. A
stored `freshness: current` label cannot keep an expired observation current.

When an adapter has no structured model field, it may parse a bounded visible
status footer with a surface-specific allowlist. The reference HERDR adapter
uses this fallback for supported panes, keeps only normalized model fields, and
discards raw screen text. It does not scan transcript content or provider
configuration. Missing or changed footer formats fail closed as `unsupported`.

Session tokens are non-sensitive adapter identities or digests of allowlisted
runtime fields. A model, provider, reasoning-mode, TTL-state, or session-token
change invalidates the prior `history_binding`. Unbound historical feedback
remains visible but contributes no positive routing score.

`unsupported` is a valid probe result, not an eligible model identity. It says
the adapter cannot expose the active model through safe metadata. The route
must preserve that result, block implementer/final-review assignment, and use
an explicit discovery, prototype, or Manual Mode fallback.

Submission runs a second safe probe for high-risk roles. Its binding fingerprint
must match the route-time provider matrix and remain eligible after TTL
reevaluation. A mismatch writes `model-identity-dispatch-block.json` and stops
before runtime delivery.

The value runtime_default is a legacy selection hint, not model-aware evidence.
The reference CLI emits observed_model, runtime_observed, or unknown and rejects
runtime_default when a matrix declares model-aware evidence.

## Evidence Sources

Provider matrix values may come from:

```text
runtime status output
agent CLI version commands
installed skills scan
MCP config scan
official provider documentation
workspace operator configuration
recent successful run evidence
local overlay capability profile, marked as hint only
routing feedback records, marked as historical priors
```

Dynamic model discovery is narrower than provider configuration discovery. It
must not read credentials, secret values, or raw user-level provider settings.
When safe runtime metadata is absent, record `unsupported` or `unavailable`
instead of guessing.

Marketing claims are not enough. Current local availability matters.
Local overlay and feedback data can explain likely fit, but they do not prove
current availability.

## Fields That Affect Routing

`cli_available`
: If false, the agent cannot receive execution work on that runtime.

`mcp_support`
: Determines whether tasks requiring external tools can be assigned.

`skill_discovery_path`
: Determines whether recommended skills will actually reach the provider.

`session_resume_support`
: Determines whether infrastructure retries can resume safely.

`approval_behavior`
: Determines whether high-risk or tool-heavy work can run headlessly.

`context_policy`
: Determines whether the agent can safely receive more work.

`runtime_preflight`
: Determines whether the runtime agent session, CLI, display size for pane
adapters, and update state are
ready enough for dispatch.

`known_limitations`
: Must be shown in routing output when they affect the task.

## Example Matrix

```json
{
  "generated_at": "2026-07-03T00:00:00Z",
  "runtime_id": "local-daemon-1",
  "providers": [
    {
      "provider_name": "claude-code",
      "cli_available": true,
      "mcp_support": "supported",
      "skill_discovery_path": ".claude/skills/",
      "session_resume_support": "supported",
      "approval_behavior": "provider_policy",
      "known_limitations": []
    },
    {
      "provider_name": "hermes",
      "cli_available": true,
      "mcp_support": "supported",
      "skill_discovery_path": ".agent_context/skills/",
      "session_resume_support": "supported",
      "approval_behavior": "provider_policy",
      "known_limitations": ["skill discovery path must be verified for this runtime"]
    }
  ]
}
```

## Routing Rule

The matrix is evidence, not authority. It cannot bypass approval gates,
context compression, receipt gates, or profile-specific verification.
