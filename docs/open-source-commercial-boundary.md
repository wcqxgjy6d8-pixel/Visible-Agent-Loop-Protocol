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

## How Skills Enter A VALP Team

VALP does not include a proprietary library of 223 skills. Skills come from
the Agent/runtime environment that an operator has installed or connected.
Doctor scans each Agent's reachable skills and records capability evidence. The
user-selected Leader then decomposes the task and declares assignments. During
routing, a skill recommender matches relevant skills to each work item. VALP
validates the assignment and gives each declared Worker a provider-specific
skill slice. The Worker loads the control contract first, then uses a reachable
skill or records why it skipped it. The resulting dispatch receipt and Worker
evidence remain auditable.

The previously observed number "223" was a machine-local discovery count from
2026-08-19 across several installed skill libraries. It was not a count of
skills bundled by VALP, and it did not mean every Agent could call every skill.
See [Skill discovery, routing, and Worker use](skill-recommendation.md) for the
full chain and its evidence boundaries.

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

Commercial value is therefore in making a customer's existing Agent and skill
estate operational: capability inventory, task-to-skill routing, private
adapters, controlled Worker dispatch, evidence collection, deployment,
monitoring, policy customization, and support. The public core supplies the
contracts and reference implementation; it does not sell a raw skill count.

The short commercial message is: **VALP does not ask an enterprise to replace
its existing Agents and skills. It inventories what is really reachable,
lets the Leader assign work, gives each Worker only the skills it can use, and
turns the whole run into evidence that can be reviewed and operated.**

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
