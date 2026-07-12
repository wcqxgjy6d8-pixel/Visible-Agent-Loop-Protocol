from __future__ import annotations

import re


HIGH_RISK_APPROVAL_PATTERNS: dict[str, list[str]] = {
    "delete": ["delete", "remove files", "remove directory", "rm -rf"],
    "auth": ["auth", "authentication", "credential", "credentials", "token"],
    "secrets": ["secret", "secrets", "api key", "apikey", "password"],
    "skill_config": [
        "install skill",
        "install a skill",
        "modify skill",
        "modify a skill",
        "patch skill",
        "patch a skill",
        "enable skill",
        "disable skill",
    ],
    "plugin_config": [
        "install plugin",
        "install a plugin",
        "modify plugin",
        "modify a plugin",
        "patch plugin",
        "patch a plugin",
        "enable plugin",
        "disable plugin",
    ],
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
            if _matches_actionable_phrase(lowered, pattern, kind):
                matches.append({"kind": kind, "matched": pattern})
                break
    return matches


def _matches_actionable_phrase(text: str, phrase: str, kind: str) -> bool:
    return any(
        _is_actionable_match(text, match.start(), match.end(), kind)
        for match in _iter_phrase_matches(text, phrase)
    )


def _iter_phrase_matches(text: str, phrase: str):
    normalized = re.sub(r"[_-]+", " ", phrase.lower()).strip()
    if not normalized:
        return []
    if normalized in {"rm rf", "reset hard"}:
        return re.finditer(re.escape(phrase.lower()), text)
    parts = [re.escape(part) for part in normalized.split()]
    separator = r"[\s_-]+"
    pattern = rf"(?<![a-z0-9]){separator.join(parts)}(?![a-z0-9])"
    return re.finditer(pattern, text)


def _is_actionable_match(text: str, start: int, end: int, kind: str) -> bool:
    if _inside_inline_code(text, start):
        return False
    if start >= 2 and text[start - 2 : start] == "--":
        return False

    before = text[max(0, start - 48) : start]
    action_context = _local_action_context(text, start, end)
    if kind == "auth" and text[start:end] == "token" and _is_model_token_context(action_context):
        return False
    negation_pattern = (
        r"\b(do not|don't|dont|never|without|no|not|avoid|skip|refuse to|must not|should not|will not)\b"
        r"[\s\S]{0,40}$"
    )
    if re.search(negation_pattern, before):
        return False
    if re.search(
        r"\b(dry[- ]run|smoke test|simulation|simulated|mock run|print only|documentation only|docs only)\b",
        action_context,
    ):
        return False
    if kind in {"publish", "submit"} and _is_valp_control_word_context(action_context):
        return False
    return True


def _is_model_token_context(window: str) -> bool:
    if re.search(r"\b(auth|authentication|credential|access|refresh|bearer|api[- ]?key)\b", window):
        return False
    return bool(
        re.search(r"\b(llm|model|prompt|context|input|output|billing)\s+token\b", window)
        or re.search(r"\bzero[- ]token\b", window)
        or re.search(r"\btoken[- ](efficient|efficiency|saving|budgeted)\b", window)
        or re.search(
            r"\btoken\s+(consumption|budgets?|counts?|usage|costs?|limits?|window|efficiency|saving|spend)\b",
            window,
        )
    )


def _local_action_context(text: str, start: int, end: int) -> str:
    delimiter = re.compile(
        r"[,;.!?\n]|\b(?:then|next|after|before|afterward|afterwards|after that)\b",
        re.IGNORECASE,
    )
    left = 0
    for match in delimiter.finditer(text[:start]):
        left = match.end()
    right = len(text)
    next_delimiter = delimiter.search(text[end:])
    if next_delimiter:
        right = end + next_delimiter.start()
    return text[left:right]


def _inside_inline_code(text: str, offset: int) -> bool:
    line_start = text.rfind("\n", 0, offset) + 1
    return text[line_start:offset].count("`") % 2 == 1


def _is_valp_control_word_context(window: str) -> bool:
    return bool(
        re.search(r"\b(bin/)?valp\s+(publish|dispatch|audit|doctor|preflight)\b", window)
        or re.search(r"\bpublish(?:ed|ing)?\s+(a\s+)?(valp\s+)?task\b", window)
        or re.search(r"\btask\s+publish(?:ed|ing)?\b", window)
    )
