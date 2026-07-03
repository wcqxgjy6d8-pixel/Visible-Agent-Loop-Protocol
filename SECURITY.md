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

