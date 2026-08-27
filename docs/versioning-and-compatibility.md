# Versioning And Compatibility

VALP uses protocol versions, Git tags, release channels, and development
branches for different purposes. They must not be treated as interchangeable.

## User-facing channels

| Channel | Meaning | Use it for a new install? |
|---|---|---|
| `main` | Current merged public source | Yes, when it points to the current stable release |
| `vX.Y.Z` | Immutable stable release and reproducible source | Yes |
| `vX.Y.Z-draft` | Historical or testing pre-release | No, unless explicitly testing that draft |
| `X.Y.ZrcN` package | Release-candidate CLI for exact-SHA evaluation | No, unless explicitly evaluating the candidate |
| `codex/*`, feature branches | Review or development candidates | No |

`v0.3.0` is the intended protocol version line and `0.3.0` is the current
reference CLI candidate. Evaluate it from a candidate branch at its exact SHA.
Stable status also requires RFC 0001 Sections 20 and 21, profile-specific
conformance, required checks, external review, merge, tag, release metadata,
and post-release smoke. Before they close, the candidate is not the default
public installation; `v0.2.0` remains legacy reproduction and migration history.

The `v0.2.0` release remains available for legacy reproduction and migration
only. Once the release gate closes, the immutable `v0.3.0` tag becomes the
preferred reproducible install reference.

## Legacy versions

Existing stable tags and releases are immutable historical records. VALP does
not delete or rewrite `v0.2.0`, `v0.2.0-draft`, or earlier draft history merely
because a newer protocol exists. Legacy artifacts remain useful for reproducing
old runs and reviewing the evolution of the public contract.

Legacy status does not imply an active maintenance or security-support promise.
Any supported maintenance line must be named explicitly in its release notes.

## Compatibility and migration

VALP `0.3.0` reads declared `0.2.0` compatibility inputs and writes the `0.3`
format. A user upgrading from `0.2.0` should use the documented migration
plan, preserve a checkpoint, validate the digest-bound plan, and keep the old
installation recoverable until activation and replay verification pass.

Do not mix a `main` checkout, an old release tag, and a candidate branch in one
installation. Select one release or candidate explicitly and record its exact
commit SHA in the evidence for that installation.

## Release rule

The public default changes only after:

```text
candidate branch -> required checks -> external review -> merge to main
  -> immutable vX.Y.Z tag -> GitHub release -> post-release smoke tests
```

Until that sequence completes, a candidate is reviewable source, not the
recommended public installation.

The release claim is scoped to the evidence package that ships with the
release. Protocol and reference-CLI stability can be released independently
from hosted runtime, platform, or production reliability claims. Any claim that
mentions Full Mode, cross-restart continuation, hosted operation, or platform
support must cite the matching task evidence and post-release smoke result.

The release-preparation path is:

```text
PR -> required checks -> external review -> merge -> tag -> Release
  -> post-release smoke tests
```
