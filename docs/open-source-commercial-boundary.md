# Open Core And Commercial Boundary

VALP separates the public protocol from optional commercial delivery. This
repository is the open-core boundary, not a hosted enterprise product.

## Public In This Repository

The following are MIT-licensed public materials:

- the VALP 0.3 protocol specification and accepted RFCs;
- the provider-neutral reference CLI, schemas, conformance runner, and audit
  rules;
- portable runtime adapter contracts and starter templates;
- sanitized examples, failure cases, and documentation;
- tests that validate the public contracts.

These materials are intended for inspection, self-hosted experimentation,
adapter development, and independent review. They do not include customer
data, credentials, local control roots, active Leader state, private prompts,
or deployment-specific configuration.

## Optional Commercial Delivery

Commercial work may be provided around the public core without changing the
MIT license of this repository. It is expected to live in separate private
repositories, deployment environments, or service agreements, and may include:

- enterprise installation, migration, and upgrade execution;
- private runtime adapters and integrations for a customer's systems;
- hosted control-plane operation, monitoring, support, and incident response;
- security review, policy customization, audit export, and compliance work;
- training, architecture work, and implementation services with an agreed
  support level.

The public repository does not claim that these services already exist or that
the reference CLI is a production-hosted enterprise platform. A commercial
offering must define its own scope, data handling, support terms, and license
terms before delivery.

## Boundary Rules

Do not commit customer workspaces, `.valp` or `.herdr-loop` control roots,
provider credentials, private prompts, local paths, runtime logs, or deployment
secrets to this repository. Keep enterprise additions behind adapters or in a
separate private delivery layer. Improvements to the protocol contracts that
are generally useful should be proposed publicly and remain reviewable under
the repository license.

