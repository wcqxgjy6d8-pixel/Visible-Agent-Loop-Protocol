# Profiles

Profiles adapt the generic protocol to a domain.

The protocol kernel remains the same. Profiles define gates, likely agents,
evidence types, and approval risks.

Likely roles are defaults for routing, not fixed assignments. The runtime must
still scan local overlays, tools, skills, context policy, permission boundaries,
and expected evidence before selecting agents.

## Core Profiles

```text
generic-analysis
software-code
web-frontend
apple-app
research
document-artifact
agent-runtime
ops-release
prototype
```

## Profile Rules

- Profiles are adapters, not protocol centers.
- A project can select a profile, but cannot override non-negotiable gates.
- Profiles can add evidence requirements.
- Profiles can add approval requirements.
- Profiles cannot skip receipts when dispatch proof is required.

## Example: software-code

Likely roles:

```text
coordinator -> state/gates/final record
implementer -> edits and command verification
reviewer -> read-only review
```

Evidence:

```text
diff summary
test output
lint/build output
review findings
final synthesis
```

## Example: apple-app

Likely roles:

```text
coordinator -> release gates and approval tracking
implementer -> Swift/Xcode verification
reviewer -> architecture, SwiftUI, UX, App Review risk
prototype -> isolated alternatives
```

Extra approval gates:

```text
signing
bundle ID
entitlements
App Store upload
TestFlight
App Review submission
privacy metadata
```

## Example: research

Likely roles:

```text
researcher -> source discovery
reviewer -> source quality/risk
coordinator -> synthesis and evidence tracking
```

Evidence:

```text
source list
retrieval date
quotes within copyright limits
summary
uncertainty
final synthesis
```
