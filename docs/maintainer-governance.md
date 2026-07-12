# Maintainer Governance

VALP is an evidence protocol, so the repository itself should preserve an
auditable change trail. The default maintainer workflow is:

```text
branch -> pull request -> review -> required checks -> merge
```

Direct pushes to `main` should not be used for normal protocol, documentation,
schema, example, or reference CLI changes. They hide the public review point
that future users and contributors need when evaluating the project.

## Required PR Trail

Every non-trivial change should have:

- a pull request with a short summary;
- a change type;
- linked evidence paths, examples, or task folders;
- verification output or a scoped explanation for why verification is not
  applicable;
- review before merge;
- required checks passing before merge.

Small typo fixes may still use the PR path. The extra friction is intentional:
the project is about visible work and verifiable claims.

## Branch Protection Baseline

`main` should require:

- pull requests before merge;
- at least one approving review;
- the repository smoke check on Linux, macOS, and Windows;
- administrator enforcement, so maintainers cannot silently bypass the same
  rules external contributors follow.

If branch protection is ever weakened for an emergency, the follow-up should be
public and should record why the exception happened, what changed, and which
checks validated the result.

## Task-Local Merge Authority

Branch protection, approving review, and passing checks are necessary merge
conditions, but they do not grant task authority. When a VALP task excludes
merge, release, or publication, completion of that task does not erase the
exclusion. A later merge requires either a new task-local approval record that
names the reviewed commit and merge action, or a separate maintainer task with
that scope.

A post-task action must be appended as later governance evidence. Maintainers
must not edit the original task, receipts, or approval ledger to imply that the
action was previously authorized.

### PR #17 Post-Task Reconciliation

VALP-030 reached its final audit at `2026-07-12T10:36:55Z`. Its task evidence
authorized branch, commit, push, and PR creation while explicitly excluding
merge. PR #17 was later merged by `jxb412` at `2026-07-12T11:49:06Z`, a gap of
`1:12:11`, as merge commit
`607c8db3b8440348747a3c4c869ab45004b21743`.

The PR had an approving review bound to its final head and all required checks
had passed. No VALP-030 task-local merge approval exists, and the merge-scoped
request in that task was superseded. The public GitHub controls therefore
passed, while the task-local authorization ledger remained incomplete. This is
recorded as a post-task maintainer governance gap; it does not retroactively
change VALP-030's scope.

## Retrospective Direct-Push Audit

Early public hardening work was committed directly to `main`. Those commits
have Git history and CI results, but they do not have the stronger PR review
trail this project should model.

The affected public-hardening window includes these direct-to-main commits:

```text
502a3cb Add cross-platform first-run evidence checks
ee8872b Make workflow tests runtime neutral
4a91427 Add runtime-neutral adapter support
729715a Add VALP doctor diagnostics
f30f911 Define Auto Visible Mode
a404e9c Clarify VALP completion proof boundaries
f54c273 Harden VALP audit evidence gates
14174d6 Calibrate VALP public evidence boundaries
73cf0c8 Prepare VALP audit fixes for promotion
fe21c3c Harden VALP audit evidence boundaries
0b7b53d Update GitHub Actions runner versions
d05305a Add first-install health gate
694ed68 Tighten dry-run approval risk matching
a606ba9 Add GitHub community onboarding
31e6d9a Add GitHub support routing
75fb80d Add Dependabot configuration
2e9ad3f Add GitHub Pages docs landing
b0f86c4 Fix GitHub Pages landing links
a158ab9 Add correction cycle evidence contract
f3c5854 Add Chinese explanatory notes
b5b5cce Add VALP social preview artwork
c8e48f6 Show hero artwork on Pages homepage
6a243b0 Require visible agent recommendation resolution
5856a28 Add dispatch payload budget
dae2096 Prepare public onboarding for v0.2.0
5711a01 Add GitHub-native cold start entry points
19bb8dd Improve README hero demo
c2f587b Put audit demo first on Pages
a611aa7 Add visible dispatch process proof
```

This page does not turn those commits into historical pull requests. GitHub
cannot retroactively add PR review discussions to commits that already landed on
`main`. It records the governance gap and sets the rule for future changes.

## Current Evidence

The direct-push commits above still have these weaker forms of evidence:

- Git commit history;
- GitHub Actions smoke-check runs for pushes to `main`;
- repository-local verification commands recorded in task evidence and rollout
  notes where applicable.

Those are useful, but they are not a substitute for a PR trail. Future
maintainer changes should not rely on direct push plus after-the-fact CI as the
normal review mechanism.
