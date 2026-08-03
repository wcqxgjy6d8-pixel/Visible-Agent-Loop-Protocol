# RFC 0002: VALP Layered Architecture

Status: Draft; based on frozen RFC-0001 layered-architecture semantics

Target: VALP 0.3 design line

Created: 2026-07-29

Source basis: frozen artifact digests verified against the frozen RFC-0001
artifact manifest

## 1. Abstract

This RFC publishes the approved layered-architecture semantics derived from the
RFC-0001 design line. It separates protocol truth from system effects into five
layers, defines the minimum machine contracts for a vertical implementation
slice, and provides full traceability from the nineteen agent-reviewed design
decisions (D01-D19) to their RFC destinations.

The five-layer model is:

```text
00 Human Intent And Authority Boundary
01 Reference System
02 Protocol Kernel
03 Adapter Boundary
04 External Runtime And Ecosystem
```

This document is normative documentation. It does not contain implementation
proof, runtime evidence, or provider-specific configuration. Implementation
and runtime proof belong in separate acceptance artifacts.

## 2. Status And Normative Language

This RFC is a draft. It does not change the stable protocol, public schemas, or
reference CLI until it is accepted and incorporated through reviewed changes.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY are normative.

## 3. Frozen Source Basis

The semantics in this document are derived from four frozen artifacts whose
SHA-256 digests and byte sizes have been verified against the artifact manifest:

| Artifact | SHA-256 digest | Size |
|---|---|---|
| `AGENT-DECISION-LEDGER.md` | `6ef30a36a78065490275a230451eea799ecd596d007550fc4abadda0d2c69159` | 17693 bytes |
| `RFC-0001-LAYERED-ARCHITECTURE.md` | `75aa3739c48be35daa74638c1afc13c88b167c9d5b350c4d4855cba11b96f30a` | 31556 bytes |
| `CORE-CONTRACTS.md` | `5a7a3e45c129bbd2ea4f4ba2425269c337cb239610486ea7ec6c07164a82ee4a` | 29946 bytes |
| `ACCEPTANCE-PLAN.md` | `15c7a8be062f2d31e9e9ca082d6933ff3d9a99e4cac464da44250f417d71168b` | 15919 bytes |

Input artifact digests:

- Architecture preview:
  `f5565273d646981cce3dfe2225b04f3b2774b5333bf55db9cdc55b74582ccef8`
- Leader synthesis report:
  `97115cada60f2c76efc4fbce0a58715ad458b7adfec899f13e0eda0b3337294d`

## 4. Design Decision Traceability

Every accepted design decision maps to one or more RFC-0001 sections. No
decision may disappear during implementation; each future change must reference
one or more decision IDs, or record why it does not affect them.

| ID | Resolution | Decision summary | RFC-0001 destination |
|---|---|---|---|
| D01 | accepted | Preserve five layers, pure Kernel, receipts, four dimensions, Adapter boundary, and fail-closed behavior | Sections 5-10 |
| D02 | merged | Layer 00 contains separate Intent and Authority lanes | Section 5 |
| D03 | merged | Keep five top-level layers; split Reference System into Control, Execution, State, Experience, and Lifecycle subdomains | Section 6 |
| D04 | accepted | Keep the human-readable Operator Workflow and add a separate Protocol State Machine view | Section 11 |
| D05 | accepted | Add explicit Cancel, Interrupt, and Redirect semantics with authority and fencing | Section 13 |
| D06 | accepted | Separate Task and Work Item results; add hard/soft/optional dependencies and partial/degraded outcomes | Section 14 |
| D07 | accepted | Replay never reruns LLM/tool/runtime work; new execution is a new Attempt | Section 12 |
| D08 | merged | Add complementary process-bound, content-bound, manual-attested, and transport-only proof kinds | Section 9 |
| D09 | accepted | Define identity-bound, revocable, conflict-safe Manual attestation without runtime-proof equivalence | Section 9.4 and CORE-CONTRACTS |
| D10 | accepted | Version protocol, schema, system, blueprint, and Adapter ABI independently | Section 16 |
| D11 | merged | Add provenance, confidence, freshness, fault class, supersession, conflict, and review state to evidence | Section 15 |
| D12 | accepted | Split delivery into MVP, asynchronous/recovery, learning/SLO, and parity stages | Section 18 |
| D13 | accepted | Add latency, replay, ledger, lock, cache, context, and cost budgets | Section 17 |
| D14 | accepted | Composite Adapters preserve a proof provenance chain and cannot hide a weak segment | Section 9 |
| D15 | accepted | Add progressive disclosure and RFC/SPEC/Schema/Test traceability | Sections 11 and 19 |
| D16 | scoped_followup | Study bounded authorization leases without weakening mandatory approval gates | Sections 5.4 and 21 |
| D17 | merged | Keep one-time explicit Leader selection; make daily reopen/recovery low-friction | Section 5 |
| D18 | bounded_no_action | Reject automatic Leader inference from focus, label, cwd, or product name | Section 5 |
| D19 | bounded_no_action | Reject more top-level layers and universal cryptographic-proof requirements | Sections 4 and 9 |

## 5. Five-Layer Architecture

### 5.1 Layer 00: Human Intent And Authority Boundary

Layer 00 owns decisions that Agents and systems cannot silently take from the
user. It contains two separate lanes (D02, D17, D18).

**Intent lane.** Owns goal, non-goal, scope, acceptance criteria, declared
evidence expectations, and user-requested interruption or redirection. The
Reference System may record, validate, display, and version intent but MUST NOT
become the owner of intent.

**Authority lane.** Owns explicit Installation Leader selection, approval for
high-risk operations, data/privacy/external-export boundaries, explicit scope
expansion, and Leader replacement or rotation approval. Authority MUST NOT be
inferred from focus, product name, pane label, cwd, window position, or private
reasoning (D18).

**Leader experience.** The first installation requires Doctor observation of
addressable candidates, explicit user selection of one Leader, and system
recording of principal, session binding, and epoch. Daily work SHOULD reopen or
recover the valid binding without re-asking (D17). Convenience MUST NOT bypass
identity or epoch proof.

**Approval ergonomics.** Mandatory approval gates remain scoped to the exact
high-risk action. Bounded authorization leases are deferred (D16) and MUST NOT
replace a required approval until a separate policy and security review
produces that contract.

### 5.2 Layer 01: Reference System

The Reference System makes the protocol operable. It owns effects, storage,
scheduling, observation, and user surfaces, but not protocol truth. It is split
into five subdomains (D03):

1. **Control**: Doctor and capability passports, task intake and intent
   recording, Leader lifecycle and binding, capability/context/provider/
   permission scans, assignment validation orchestration, routing scores as
   visible advice.

2. **Execution**: Work Item creation, dependency frontier evaluation, dispatch
   scheduling, bounded retry and correction rounds, wait/wake coordination,
   cancel/interrupt/redirect operations (D05).

3. **State**: Append-only event and receipt ledgers, locks and sequence
   allocation, revision compare-and-swap, state projection, replay, handoff and
   restart recovery, idempotency records.

4. **Experience and observation**: CLI, application, and API surfaces, visible
   routing and attention, Evidence Board, status/blocker/next-action
   presentation, four-dimensional events and reports.

5. **Lifecycle**: Installation, migration, update staging, rollback,
   compatibility negotiation, preservation of local auth, memory, skills,
   configuration, and evidence.

**System boundary rule.** Every gate-bearing state change MUST be proposed to
the Kernel as an Event plus Evidence and accepted as a Result before the System
presents it as protocol truth. The System MAY read an authorized policy file,
but it MUST parse and canonicalize its content into immutable,
content-addressed Evidence. The Kernel validates that Evidence and computes the
gate result; the System MUST NOT replace that computation with an asserted
pass.

### 5.3 Layer 02: Protocol Kernel

The Kernel owns truth conditions. It is pure (D01):

```text
reduce(State, Event, EvidenceSet) -> Result
```

For equal canonical inputs and protocol version, the output MUST be equal. The
Kernel MUST NOT read files, obtain wall-clock time, call a runtime/model/tool/
network, allocate an external identity, inspect a UI, or perform a side effect.
Time, runtime status, content digests, and identity observations enter as typed
Events and Evidence created by the System or Adapter.

**Core entities.** Task, State, WorkItem, Attempt, Event, Evidence, Receipt,
Claim, and Result. Each has a distinct identity; one MUST NOT substitute for
another.

**Result variants.** A Result contains exactly one of:

- `accepted`: accepted next state plus emitted obligations and audit facts;
- `no_op`: unchanged state, bound by ID and digest to a prior accepted Result;
- `rejected`: deterministic failure with a closed error code and unchanged
  state.

The three variants are mutually exclusive. Only `accepted` increments revision
or emits side-effect obligations.

### 5.4 Layer 03: Adapter Boundary

An Adapter translates a runtime into typed observations and proof. It MUST NOT
invent protocol state or Done semantics.

**Common port.** An Adapter declares support for probe, submit, observe,
cancel, resume, and prove. Unsupported operations are explicit capability
results, not guessed values.

**Proof grades (D08).** Proof grades are closed proof kinds, not a total
ordering:

| Grade | Meaning |
|---|---|
| `process_bound` | Invocation or terminal observation is bound to a process/run/thread/job identity and causal submission |
| `content_bound` | Observation is bound to an exact payload or output digest, identity tuple, sequence, and acknowledgement |
| `manual_attested` | A named human action attests exact content and scope |
| `transport_only` | Text, pane content, notification, or prepared data exists without causal invocation proof |

`process_bound` and `content_bound` prove different facts and are combined when
a transition needs both. `transport_only` MUST NOT satisfy Full or Remote
submission. Manual attestation MUST NOT be relabeled as runtime submission.

**Manual attestation (D09).** A Manual attestation is valid only when it binds
a named principal and task-local authority ref, Manual Mode and one closed
Manual receipt action, exact identity and digests, an attestation statement,
issue-time evidence, validity policy, and safe evidence refs. Revocation is
append-only. Same-identity attestations with different content digests conflict
and fail closed until authorized adjudication.

**Composite Adapters (D14).** A Work Item may cross multiple transport or
runtime segments. Each segment MUST append its own provenance record. The final
claim requires every proof kind and segment declared by its policy. A missing
kind or a transport-only segment limits the claim even when another segment is
strong.

### 5.5 Layer 04: External Runtime And Ecosystem

Layer 04 contains replaceable execution capabilities: Agent surfaces, models,
Providers, reasoning modes, tools, MCP servers, skills, repositories, local/
remote/hosted/process/queue/graph runtimes, and operating contexts.

Names and roles are priors. Current capability requires fresh observation of
identity, model/provider/session, callability, permissions, context, tools, and
task evidence.

## 6. Two Required Flow Views

Every normative transition MUST identify Event type, actor and authority,
current-state guard, identity tuple, required evidence, accepted next state,
emitted obligations and audit facts, and deterministic failure code (D04, D15).

**Operator Workflow.** The human-readable flow:

```text
Doctor and Leader authority
  -> Publish
  -> Scan and decompose
  -> Declare and validate assignments
  -> Visible dispatch and submission proof
  -> Execute / Wait / Wake
  -> Verify / Review / Fix
  -> Approval / Audit / Terminal result
```

**Protocol State Machine.** Includes duplicate, conflict, timeout, cancellation,
user interruption, retry, partial, degraded, recovery, and migration paths.

## 7. State Ownership

Three separate state domains:

**Task state** (closed): `published`, `routing_validation`, `dispatching`,
`executing`, `verifying`, `reviewing`, `fixing`, `approval_required`,
`recording`, `done`, `blocked`, `failed`, `cancelled`.

**Work Item state** (closed): `pending`, `eligible`, `submitted`, `running`,
`completed`, `partial`, `degraded`, `blocked`, `failed`, `cancelled`, `skipped`.
Requirement is exactly `required`, `optional`, or `soft`. Only `soft` Work
Items may appear in the degradation floor.

**Attempt state** (closed): `created`, `submitted`, `running`, `completed`,
`failed`, `cancelled`, `fenced`.

**Claim result** (closed): `pass`, `fail`, `unknown`, `partial`, `degraded`,
`not_applicable`.

Unknown values in any closed vocabulary fail with
`VALP-E-UNKNOWN-ENUM-VALUE`.

## 8. Attempt And Replay Semantics

Replay applies accepted Events to rebuild state. Replay MUST NOT call an LLM,
tool, Agent, Adapter, or runtime (D07). Any operation that can produce a
different external output is a new Attempt. Retrying or redispatching creates a
new Attempt ID even when the Work Item and payload are unchanged.

The ledger preserves prior Attempt IDs, payload and control-contract digests,
receipts and evidence, supersession or correction relationships, and the
accepted Result for each Event. An identical duplicate Event is a no-op bound
by ID and digest to the prior accepted Result. A duplicate identity with
different content fails closed.

## 9. Cancel, Interrupt, And Redirect

**Cancel (D05).** A cancellation Event records authorized principal, scope
(Task, Work Item, or Attempt), reason, target identities and current
generation/epoch, supporting evidence or policy ref, and requested Adapter
cancellation obligations. Kernel acceptance fences later results from the
cancelled identity.

**Interrupt.** Explicit user input may suspend automatic progression and return
control to the Leader. It does not satisfy missing evidence or completion gates.

**Redirect.** Changing goal, scope, approach, or acceptance criteria creates a
versioned intent change. Work that no longer applies is cancelled, superseded,
or moved to a scoped follow-up; it is not erased.

## 10. Dependency And Partial-Result Semantics

Dependency edges are `hard` (failure blocks dependent), `soft` (failure permits
degraded path), or `optional` (failure may be skipped when Done criteria
permit) (D06).

The Task completion Claim MUST be `pass` before Task state can become Done.
`partial`, `degraded`, `fail`, or `unknown` on that top-level Claim never means
Done.

The default-deny Done matrix:

| Condition at closure | Done allowed? |
|---|---|
| required criterion is `fail`, `unknown`, or `partial` | no |
| hard dependency is not completed with valid evidence | no |
| required Work Item is failed, blocked, cancelled, skipped, partial, degraded, or unknown | no |
| required Quality gate is not `pass` | no |
| non-applicable result has a predeclared applicability rule and supporting evidence | yes |
| optional Work Item is skipped under a predeclared Done policy | yes, with visible synthesis |
| soft objective is degraded within a predeclared floor and no required gate depends on it | yes, with visible risk acceptance |

Done policy, dependency kinds, dimension floors, and optionality MUST be frozen
before execution. Relaxing one after failure requires an authorized Redirect, a
new intent version, cancellation or supersession of invalidated work, and fresh
evaluation.

### 10.1 Four-Dimensional Evaluation Boundary

Dimension policy storage belongs to the System. Dimension gate truth belongs to
the Kernel. The System reads an authorized policy source, canonicalizes it into
immutable digest-bound Dimension Policy Evidence, and proposes evaluation. The
Kernel validates policy ID/digest/scope/applicability and evaluates every
supported rule deterministically. Quality, Experience, Cost, and Stability are
evaluated independently.

An unsupported dimension-policy operator fails closed with
`VALP-E-DIMENSION-POLICY-UNSUPPORTED-OPERATOR`. An unsupported policy version
fails closed with `VALP-E-DIMENSION-POLICY-UNSUPPORTED-VERSION`. Both produce
`VALP-E-DIMENSION-GATE-UNVERIFIABLE` at the gate level.

Faithful canonicalization of the authorized policy source into Dimension Policy
Evidence is a System audit obligation. An external auditor MAY independently
parse and canonicalize the authorized source and compare the resulting
`policy_digest` and canonical payload against the Evidence.

## 11. Evidence Quality And Learning

Evidence descriptors include content digest and safe ref, provenance, observed
time, validity interval or freshness policy, confidence, observation method,
scope, fault class, review status, and supersession/conflict refs (D11).

Closed fault classes: `none`, `transient`, `capability`, `permission`,
`configuration`, `protocol`, `unknown`.

Closed confidence values: `strong`, `moderate`, `weak`, `unknown`.

Closed review status values: `unreviewed`, `accepted`, `rejected`,
`superseded`, `conflicted`.

Routing and learning feedback may use evidence as a prior. It MUST NOT delete
or rewrite the immutable historical ledger, become assignment authority, or
replace fresh task observation.

## 12. Version And Migration

The following versions evolve independently (D10):

| Surface | Accepted line | Compatibility rule |
|---|---|---|
| Blueprint | `RFC-0001/1.x` | `1.0` defines Protocol `0.3` |
| Protocol | `>=0.3.0,<0.4.0` | Semantic changes require version bump |
| Protocol 0.2 compatibility input | `0.2.0-draft` | Read-only through declared compatibility |
| State schema | read v1/v2; write v3 | Migration preserves original bytes |
| Receipt schema | read legacy/v2; write v3 | Old receipts remain historical |
| New core schemas | `v1` | Start at v1 |
| Reference System | `>=0.3.0,<0.4.0` | Writes Protocol `0.3` only |
| Adapter ABI | `>=1.0,<2.0` | Major mismatch blocks |

Unknown required fields, closed enum values, authority rules, proof
requirements, receipt meanings, or Done conditions are safety-relevant and MUST
fail with `VALP-E-MIGRATION-UNSUPPORTED`.

## 13. Budgets And Performance

The Reference System records independent budgets for context and dispatch
payload, iteration and correction rounds, monetary cost, dispatch and
verification latency, execution deadline, ledger growth and replay cost, lock
contention and durable append, and cache validity and freshness (D13).

Cache may avoid repeated observation only while identity, scope, freshness, and
policy remain valid. Cache MUST NOT skip authority, approval, or required
evidence gates.

## 14. Delivery Stages

Delivery proceeds in four stages (D12):

**MVP.** Canonical core entities and identities, pure reducer, canonical State
and accepted/no-op/rejected Result variants, publish/validated assignment/
dispatch receipts/evidence evaluation, System-materialized dimension policy
Evidence and Kernel-computed required dimension gates, Done/Blocked/Failed/
Cancelled, golden/negative/property/replay tests, one process-bound Adapter
proof path.

**Stage 2: asynchronous and recovery.** Dependency frontier, wait/wake/resume,
Attempt fencing, partial/degraded outcomes, handoff and restart recovery,
Composite Adapter provenance.

**Stage 3: experience and learning.** Evidence Board and progressive disclosure,
human interruption and redirect, expanded four-dimensional policy/SLO catalog
and Budget Record contracts, evidence quality and fault attribution, routing
and learning priors.

**Vision.** Conformance plus real local/remote runtime evidence, governed
migration/update/rollback, organization-specific policies that cannot weaken
protocol truth.

## 15. Traceability

Every accepted implementation change references (D15):

```text
RFC section
  -> SPEC section
  -> Schema field or closed enum
  -> reducer behavior or Adapter obligation
  -> positive fixture
  -> negative fixture
  -> reliability/E2E evidence where applicable
```

Public documentation may simplify presentation but MUST preserve a visible path
to the complete capability index and normative contract.

## 16. Compatibility With Existing Protocol

This RFC preserves:

- explicit user-selected Leader authority;
- capability passports and layered evidence;
- Leader declaration plus VALP validation;
- worker control contracts;
- receipt distinctions and expected-evidence gates;
- deterministic wait/wake identity and CAS behavior;
- Full/Remote/Manual separation;
- visible recommendation resolution;
- review, approval, final synthesis, and strict audit.

It refines or adds:

- explicit five-layer ownership;
- Attempt identity and no-side-effect replay;
- cancellation fencing and intent redirect;
- Work Item partial/degraded outcomes;
- proof grades and Composite Adapter provenance;
- evidence quality and fault attribution;
- independent version lines and migration fixtures;
- wider performance and reliability budgets.

## 17. Resolved Architecture Decisions

The five architecture decisions are closed in this order:

1. Full and Remote require the composite receipt proofs in the Adapter
   Boundary; Manual uses separate attestation receipts and never runtime
   receipt equivalence.
2. Manual attestation uses the identity, authority, digest, revocation, and
   conflict rules in the Manual attestation contract.
3. Top-level `partial` or `degraded` never means Done. Only predeclared
   optional or soft subordinate residuals may remain under the Done matrix.
4. RFC 0001 version 1.x maps to Protocol 0.3, Reference System 0.3.x, v3
   state and receipt writes, v1 new core schemas, and Adapter ABI 1.x.
5. The final public Layer 00 name is `Human Intent And Authority Boundary`,
   with separate Intent and Authority lanes.

## 18. Deferred Follow-Up

D16 records a real usability risk: repeated approval prompts can train users to
approve without reading. A future policy RFC may study bounded authorization
leases, but it must define scope, identity and digest binding, expiry,
revocation, conflict handling, audit evidence, and operations that are never
leaseable. This follow-up is not required for the first Kernel slice and cannot
weaken current approval gates.

Interrupt and Redirect Event contracts are required before Stage 3
human-intervention work. Budget Record contracts are also deferred to Stage 3.
Neither area is implementation-authorized by RFC prose alone.

## 19. Acceptance Criteria

This RFC is ready for implementation only when:

- all five resolved decisions remain visibly closed;
- an independent Reviewer checks the exact RFC digest;
- core entities and transition rules have machine-contract drafts;
- State, status enums, all three Result variants, and the four-dimensional
  evaluation boundary have machine-contract drafts and negative tests;
- the acceptance plan contains RED tests for every new semantic claim;
- public and local-only material are separated;
- no approval, receipt, evidence, or audit gate is weakened.
