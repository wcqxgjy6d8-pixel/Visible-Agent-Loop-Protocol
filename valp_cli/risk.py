from __future__ import annotations

import re


_LIVE_CONFIGURATION_MUTATION_VERBS = (
    "install",
    "modify",
    "patch",
    "enable",
    "disable",
    "update",
    "edit",
    "change",
    "upgrade",
    "configure",
    "reconfigure",
    "uninstall",
    "remove",
    "turn off",
    "deactivate",
    "activate",
)
_LIVE_SKILL_MUTATION = "live skill mutation"
_LIVE_PLUGIN_MUTATION = "live plugin mutation"
_ACTION_DELIMITER = re.compile(
    r"[,;.!?\n]|\b(?:but|however|yet|then|next|after|before|afterward|afterwards|after that)\b",
    re.IGNORECASE,
)
_COORDINATE_NEGATION_BOUNDARY = re.compile(
    r"[;.!?\n]|\b(?:but|however|yet|then|next|after|before|afterward|afterwards|after that)\b",
    re.IGNORECASE,
)


HIGH_RISK_APPROVAL_PATTERNS: dict[str, list[str]] = {
    "delete": ["delete", "remove files", "remove directory", "rm -rf"],
    "auth": ["auth", "authentication", "credential", "credentials", "token"],
    "secrets": ["secret", "secrets", "api key", "apikey", "password"],
    "skill_config": [_LIVE_SKILL_MUTATION],
    "plugin_config": [_LIVE_PLUGIN_MUTATION],
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
    if phrase in {_LIVE_SKILL_MUTATION, _LIVE_PLUGIN_MUTATION}:
        subject = "skill" if phrase == _LIVE_SKILL_MUTATION else "plugin"
        separator = r"[\t _-]+"
        verbs = "|".join(
            separator.join(re.escape(part) for part in verb.split())
            for verb in sorted(_LIVE_CONFIGURATION_MUTATION_VERBS, key=len, reverse=True)
        )
        determiner = (
            r"(?:a|an|the|my|our|your|this|that|these|those|all|any|every|each|some)"
        )
        modifier = r"[a-z0-9]+(?:[-_][a-z0-9]+)*"
        pattern = (
            rf"(?<![a-z0-9])(?:{verbs}){separator}"
            rf"(?:{determiner}{separator})?(?:{modifier}{separator}){{0,2}}"
            rf"{subject}s?(?![a-z0-9])"
        )
        return re.finditer(pattern, text)
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
    if _inside_inline_code(text, start) and not (
        _explicitly_executes_inline_literal(text, start)
        or _explicitly_executes_fenced_literal(text, start)
    ):
        return False
    if start >= 2 and text[start - 2 : start] == "--":
        return False

    before = _local_action_prefix(text, start)
    action_context = _local_action_context(text, start, end)
    sentence_prefix = _sentence_action_prefix(text, start)
    sentence_context = _sentence_action_context(text, start, end)
    if kind == "auth" and text[start:end] == "token" and _is_model_token_context(action_context):
        return False
    if kind == "deploy" and text[start:end] == "deployment" and re.match(
        r"[-\s]+grade\b", text[end:]
    ):
        return False
    if kind == "metadata" and not re.search(
        r"\b(?:change|delete|edit|modify|publish|remove|replace|set|submit|update|upload)\b",
        action_context,
    ):
        return False
    follows_completed_negated_predicate = _follows_completed_negated_predicate(
        text, start
    )
    if not follows_completed_negated_predicate and (
        _is_effectively_negated(before)
        or _is_coordinately_negated(
            _coordinate_action_prefix(text, start),
            _coordinate_action_suffix(text, end),
        )
    ):
        return False
    non_actionable_context = re.search(
        r"\b(documentation only|docs only|print only)\b", action_context
    )
    if non_actionable_context:
        if non_actionable_context.group(1) != "print only":
            return False
        if _is_printed_command_label(text, start, end):
            return False
        if not _follows_completed_print_only_predicate(text, start):
            return False
    if (
        re.search(r"^\s*(?:verify|check|test|audit|review|inspect)\b", action_context)
        and re.search(
            r"\b(?:wording|classification|classifier|risk|approval|term|phrase|parser|detection|behavior)\b",
            action_context,
        )
    ):
        return False
    if re.search(r"^\s*(?:document|describe|explain)\b", action_context):
        return False
    if (
        re.search(
            r"\b(dry[- ]run|smoke test|simulation|simulated|mock run)\b",
            sentence_context,
        )
        and not re.search(
            r"\b(?:but|however|yet|then|next|afterward|afterwards|after that)\b[^.!?]*$",
            sentence_prefix,
        )
        and not re.search(r"\band\s*$", sentence_prefix)
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
        or re.search(
            r"\bnon[- ]sensitive\b[^.;!?\n]{0,120}\b(?:session|adapter\s+generation)\b"
            r"[^.;!?\n]{0,80}\btoken\b",
            window,
        )
        or re.search(r"\bzero[- ]token\b", window)
        or re.search(r"\btoken[- ](efficient|efficiency|saving|budgeted)\b", window)
        or re.search(
            r"\btoken\s+(consumption|budgets?|counts?|usage|costs?|limits?|window|efficiency|saving|spend)\b",
            window,
        )
    )


def _local_action_context(text: str, start: int, end: int) -> str:
    left = 0
    for match in _ACTION_DELIMITER.finditer(text[:start]):
        left = match.end()
    right = len(text)
    next_delimiter = _ACTION_DELIMITER.search(text[end:])
    if next_delimiter:
        right = end + next_delimiter.start()
    return text[left:right]


def _local_action_prefix(text: str, start: int) -> str:
    left = 0
    for match in _ACTION_DELIMITER.finditer(text[:start]):
        left = match.end()
    return text[left:start]


def _coordinate_action_prefix(text: str, start: int) -> str:
    left = 0
    for match in _COORDINATE_NEGATION_BOUNDARY.finditer(text[:start]):
        left = match.end()
    return text[left:start]


def _coordinate_action_suffix(text: str, end: int) -> str:
    right = len(text)
    next_boundary = _COORDINATE_NEGATION_BOUNDARY.search(text[end:])
    if next_boundary:
        right = end + next_boundary.start()
    return text[end:right]


def _sentence_action_context(text: str, start: int, end: int) -> str:
    delimiter = re.compile(r"[.!?\n]")
    left = 0
    for match in delimiter.finditer(text[:start]):
        left = match.end()
    right = len(text)
    next_delimiter = delimiter.search(text[end:])
    if next_delimiter:
        right = end + next_delimiter.start()
    return text[left:right]


def _sentence_action_prefix(text: str, start: int) -> str:
    delimiter = re.compile(r"[.!?\n]")
    left = 0
    for match in delimiter.finditer(text[:start]):
        left = match.end()
    return text[left:start]


def _is_effectively_negated(prefix: str) -> bool:
    normalized = re.sub(r"\s+", " ", prefix.lower()).strip()
    if re.search(r"\bunder no circumstances\b", normalized):
        return True
    if re.search(r"\bnot true\b.{0,60}\b(?:should |must |will )?not$", normalized):
        return False
    if re.search(r"\b(?:do not|don't|dont) hesitate to$", normalized):
        return False
    if re.search(
        r"\b(?:do not|don't|dont|never|refuse to|must not|should not|will not)\s+"
        r"(?:publish|create|prepare|make|perform|run|execute|deploy|submit|upload|"
        r"change|modify|update|rotate|revoke|delete|remove)\s+"
        r"(?:a|an|the|this|that|any|our|your)?$",
        normalized,
    ):
        return True
    direct = re.search(
        r"\b(?:do not|don't|dont|never|without|no|not|avoid|skip|refuse to|must not|should not|will not)"
        r"(?:\s+(?:please|ever|directly|immediately))?$",
        normalized,
    )
    if direct:
        return True
    return bool(
        re.search(
            r"\b(?:do not|don't|dont|never|refuse to|must not|should not|will not)\b"
            r".{0,80}\b(?:or|and)$",
            normalized,
        )
    )


def _is_coordinately_negated(prefix: str, suffix: str) -> bool:
    normalized = re.sub(r"\s+", " ", prefix.lower()).strip()
    if not re.search(
        r"\b(?:do not|don't|dont|never|no|refuse to|must not|should not|will not)\b",
        normalized,
    ):
        return False
    if re.search(r"\b(?:and|or)$", normalized):
        return True
    if not re.search(r",\s*$", prefix):
        shared_phrase = prefix.rsplit(",", 1)[-1].strip().lower()
        independent_clause = re.search(
            r"\b(?:i|we|you|they|he|she|it|this|that|there)\s+"
            r"(?:will|shall|must|should|can|could|may|might|would|am|is|are|was|were|have|has|had)\b",
            shared_phrase,
        )
        if not (
            "," in prefix
            and shared_phrase
            and len(shared_phrase.split()) <= 5
            and not independent_clause
        ):
            return False
        return True
    normalized_suffix = re.sub(r"\s+", " ", suffix.lower()).strip()
    return bool(
        re.match(r"^(?:and|or)\b", normalized_suffix)
        or re.search(r"(?:^|,)\s*(?:and|or)\b", normalized_suffix)
    )


def _follows_completed_negated_predicate(text: str, start: int) -> bool:
    prefix = _coordinate_action_prefix(text, start)
    match = re.search(
        r"\b(?:make|perform)\s+no\s+(?P<object>[^;.!?\n]+?)\s+and\s*$",
        prefix,
    )
    return bool(match and not _ends_with_approval_risk(match.group("object")))


def _ends_with_approval_risk(text: str) -> bool:
    candidate = text.strip()
    return any(
        match.end() == len(candidate)
        for patterns in HIGH_RISK_APPROVAL_PATTERNS.values()
        for pattern in patterns
        for match in _iter_phrase_matches(candidate, pattern)
    )


def _follows_completed_print_only_predicate(text: str, start: int) -> bool:
    prefix = _coordinate_action_prefix(text, start)
    match = re.search(
        r"\bprint\s+only\s+(?P<object>[^;.!?\n]+?)\s+and\s*$",
        prefix,
    )
    if not match:
        return False
    object_text = match.group("object").strip()
    return bool(
        re.match(r"(?:the|a|an|this|that|these|those|my|our|your)\b", object_text)
        or not _ends_with_approval_risk(object_text)
    )


def _is_printed_command_label(text: str, start: int, end: int) -> bool:
    """Treat high-risk words as labels when print-only outputs a command list."""
    prefix = _coordinate_action_prefix(text, start)
    suffix = text[end:]
    if not re.search(r"\bprint\s+only\b", prefix, re.IGNORECASE):
        return False
    if not re.match(r"\s+(?:the\s+)?(?:commands?|command\s+names?)\b", suffix, re.IGNORECASE):
        return False
    return bool(re.search(r"\b(?:and|or)\s*$", prefix, re.IGNORECASE))


def _inside_inline_code(text: str, offset: int) -> bool:
    if text[:offset].count("```") % 2 == 1:
        return True
    line_start = text.rfind("\n", 0, offset) + 1
    line_prefix = text[line_start:offset].replace("```", "")
    return (
        line_prefix.count("`") % 2 == 1
        or line_prefix.count('"') % 2 == 1
        or len(_single_quote_offsets(line_prefix)) % 2 == 1
    )


def _explicitly_executes_inline_literal(text: str, offset: int) -> bool:
    line_start = text.rfind("\n", 0, offset) + 1
    line_prefix = text[line_start:offset]
    single_quotes = _single_quote_offsets(line_prefix)
    opening_quote = max(
        line_prefix.rfind("`"),
        line_prefix.rfind('"'),
        single_quotes[-1] if single_quotes else -1,
    )
    if opening_quote < 0:
        return False
    instruction = line_prefix[:opening_quote]
    return bool(
        re.match(
            r"^\s*(?:please\s+)?(?:run|execute)"
            r"(?:\s+(?:the\s+)?(?:following\s+)?(?:command|script|literal))?\s*$",
            instruction,
        )
    )


def _explicitly_executes_fenced_literal(text: str, offset: int) -> bool:
    if text[:offset].count("```") % 2 != 1:
        return False
    opening_fence = text.rfind("```", 0, offset)
    instruction = text[:opening_fence]
    return bool(
        re.search(
            r"(?:^|[.;!?\n])\s*(?:please\s+)?(?:run|execute)"
            r"(?:\s+(?:(?:this|the|a)\s+)?(?:following\s+)?"
            r"(?:command|script|snippet|code))?\s*:?\s*$",
            instruction,
        )
    )


def _single_quote_offsets(text: str) -> list[int]:
    return [
        index
        for index, character in enumerate(text)
        if character == "'"
        and not (
            index > 0
            and index + 1 < len(text)
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        )
    ]


def _is_valp_control_word_context(window: str) -> bool:
    return bool(
        re.search(r"\b(bin/)?valp\s+(publish|dispatch|audit|doctor|preflight)\b", window)
        or re.search(r"\bpublish(?:ed|ing)?\s+(a\s+)?(valp\s+)?task\b", window)
        or re.search(r"\btask\s+publish(?:ed|ing)?\b", window)
    )
