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
  "model_selection": "static|dynamic|runtime_default|unknown",
  "max_concurrency": 1,
  "context_policy": {},
  "known_limitations": [],
  "last_verified_at": "2026-07-03T00:00:00Z"
}
```

Use `unknown` instead of guessing.

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
```

Marketing claims are not enough. Current local availability matters.

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
