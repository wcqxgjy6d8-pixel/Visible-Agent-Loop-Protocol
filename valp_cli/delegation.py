from __future__ import annotations

from typing import Any


PROTECTED_SURFACES = ["skills", "plugins", "memory", "mcp_config", "agent_config"]


def build_delegation_policy(task_id: str, manual_mode: bool) -> dict[str, Any]:
    return {
        "schema_version": "valp-delegation-policy.v1",
        "task_id": task_id,
        "reference_enforcement": "dispatch_and_audit",
        "runtime_enforcement": "manual_attestation" if manual_mode else "adapter_required",
        "live_self_modification": {
            "mode": "forbidden",
            "protected_surfaces": PROTECTED_SURFACES,
            "repository_changes": "allowed_when_task_scoped_and_not_live_loaded",
            "violation_effect": "invalidate_evidence_and_block",
        },
        "violations": [],
    }


def validate_delegation_policy(
    document: dict[str, Any],
    task_id: str,
    manual_mode: bool,
) -> list[str]:
    if not document:
        return ["missing delegation-policy.json"]
    expected = build_delegation_policy(task_id, manual_mode)
    errors: list[str] = []
    if set(document) != set(expected):
        errors.append("delegation policy has unexpected or missing top-level fields")
    for key in ["schema_version", "task_id", "reference_enforcement", "runtime_enforcement"]:
        if document.get(key) != expected[key]:
            errors.append(f"delegation policy {key} is missing or weakened")
    live_policy = document.get("live_self_modification")
    if live_policy != expected["live_self_modification"]:
        errors.append("delegation policy does not protect the exact required live surfaces")
    violations = document.get("violations")
    if not isinstance(violations, list):
        errors.append("delegation policy violations must be an array")
    return errors
