from __future__ import annotations

import re


HIGH_RISK_APPROVAL_PATTERNS: dict[str, list[str]] = {
    "delete": ["delete", "remove files", "remove directory", "rm -rf"],
    "auth": ["auth", "authentication", "credential", "credentials", "token"],
    "secrets": ["secret", "secrets", "api key", "apikey", "password"],
    "memory": ["memory"],
    "agent_config": ["agent_config", "agent config", "agent configuration"],
    "mcp_config": ["mcp_config", "mcp config", "mcp configuration"],
    "signing": ["signing", "code signing", "certificate", "provisioning profile"],
    "entitlements": ["entitlement", "entitlements"],
    "data_migration": ["data_migration", "data migration", "migrate data", "database migration"],
    "destructive_reset": ["destructive_reset", "destructive reset", "reset --hard", "factory reset"],
    "publish": ["publish"],
    "release": ["release"],
    "upload": ["upload"],
    "submit": ["submit"],
    "deploy": ["deploy", "deployment"],
    "pricing": ["pricing", "price change", "change price"],
    "metadata": ["metadata"],
    "privacy": ["privacy", "private data"],
    "external_private_data": ["external_private_data", "external private data", "export private data"],
}


def classify_approval_risks(text: str) -> list[dict[str, str]]:
    lowered = text.lower()
    matches: list[dict[str, str]] = []
    for kind, patterns in HIGH_RISK_APPROVAL_PATTERNS.items():
        for pattern in patterns:
            if _matches_phrase(lowered, pattern):
                matches.append({"kind": kind, "matched": pattern})
                break
    return matches


def _matches_phrase(text: str, phrase: str) -> bool:
    normalized = re.sub(r"[_-]+", " ", phrase.lower()).strip()
    if not normalized:
        return False
    if normalized in {"rm rf", "reset hard"}:
        return phrase.lower() in text
    parts = [re.escape(part) for part in normalized.split()]
    separator = r"[\s_-]+"
    pattern = rf"(?<![a-z0-9]){separator.join(parts)}(?![a-z0-9])"
    return re.search(pattern, text) is not None
