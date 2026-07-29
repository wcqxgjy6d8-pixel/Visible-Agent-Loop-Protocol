# Security Policy

This protocol is designed for local-first multi-agent workflows.

## Sensitive Operations

The following require explicit user approval:

- deleting files or directories;
- changing auth, secrets, memory, agent config, or MCP config;
- changing signing, bundle IDs, entitlements, or app capabilities;
- running migrations or destructive resets;
- publishing, deploying, uploading, submitting, or releasing;
- changing pricing, privacy, or metadata;
- sending private data to a new external service.

## Auto Visible Mode

Auto Visible Mode is automatic intake, not automatic permission. A trigger
policy or watcher may publish a task and refresh non-mutating Doctor facts. It
cannot select a Leader, author Agent assignments, or replace a blocked Agent.
Only after a user-selected Leader has written a valid assignment declaration
may automation validate the route and continue. It must stop before high-risk
work unless explicit approval has been recorded.

For high-risk signals, the task evidence should record `block_for_approval` and
the exact scope that was not executed. Silence, background watcher state, or a
previous broad approval is not enough.

## Secrets

Capability registries must not store:

- tokens;
- API keys;
- OAuth credentials;
- private environment variables;
- command strings containing secrets;
- private runtime configuration.

## Reporting

Open an issue with:

- protocol section affected;
- risk scenario;
- expected safer behavior;
- proposed wording or schema change.
