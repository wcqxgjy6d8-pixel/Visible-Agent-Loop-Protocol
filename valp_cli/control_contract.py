from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA_VERSION = "valp-worker-control-contract.v1"
SLICE_SCHEMA_VERSION = "valp-control-slice.v1"
PRIORITY_CLASS = "highest_runtime_control"
CONTROL_CONTRACT_REF = "control-contract.json"
LOAD_BEFORE = ["planning", "skills", "tool_execution", "immediate_response"]


def control_contract_file_bytes(contract: dict[str, Any]) -> bytes:
    return (json.dumps(contract, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def control_contract_digest(
    contract: dict[str, Any],
    file_bytes: bytes | None = None,
) -> str:
    payload = file_bytes if file_bytes is not None else control_contract_file_bytes(contract)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def build_control_contract(task_id: str, created_at: str) -> dict[str, Any]:
    if not task_id.strip() or not created_at.strip():
        raise ValueError("task_id and created_at are required")
    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "priority_class": PRIORITY_CLASS,
        "created_at": created_at,
        "authority": {
            "higher_than_contract": ["system", "developer", "explicit_user", "approval_gates"],
            "lower_than_contract": [
                "task_scope",
                "project_instructions",
                "skills",
                "tool_provider_defaults",
                "worker_output",
                "immediate_response",
            ],
        },
        "load_before": list(LOAD_BEFORE),
        "failure_policy": {
            "missing_or_invalid": "block",
            "conflicting_duplicate": "fail_closed",
            "unsupported_continuation": "downgrade_mode",
        },
        "channel": {
            "kind": "runtime_control",
            "user_input_allowed": False,
            "raw_worker_output_allowed": False,
        },
        "delivery": {
            "safe_point_required": True,
            "busy_leader": "queue_and_coalesce",
            "interrupt_inflight_action": False,
        },
        "idempotency": {
            "identical_receipt": "no_op",
            "conflicting_receipt": "fail_closed",
            "race_precedence": "first_accepted_by_revision_cas",
        },
        "continuation": {
            "acknowledgement_chain": [
                "resume_pending",
                "resume_received",
                "digest_verified",
                "resume_accepted",
                "continuation_started",
                "resume_consumed",
            ],
            "transport_only_event": "leader_resume_sent",
            "full_mode_requires": "resume_consumed",
            "manual_submission_mode": "manual",
        },
        "worker_ack": {"required": True, "status": "honored"},
    }


def _exact_keys(value: Any, expected: set[str], label: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} must be an object"]
    actual = set(value)
    if actual == expected:
        return []
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    details = []
    if missing:
        details.append("missing " + ", ".join(missing))
    if extra:
        details.append("unexpected " + ", ".join(extra))
    return [f"{label} fields are invalid: " + "; ".join(details)]


def validate_control_contract(contract: dict[str, Any], task_id: str | None = None) -> list[str]:
    expected = build_control_contract(
        task_id or str(contract.get("task_id") or "_"),
        str(contract.get("created_at") or "_"),
    )
    errors = _exact_keys(contract, set(expected), "control contract")
    if errors:
        return errors
    if contract.get("schema_version") != SCHEMA_VERSION:
        errors.append("control contract schema_version is invalid")
    if task_id is not None and contract.get("task_id") != task_id:
        errors.append("control contract task_id does not match")
    if not isinstance(contract.get("task_id"), str) or not contract["task_id"].strip():
        errors.append("control contract task_id must be non-empty")
    if not isinstance(contract.get("created_at"), str) or not contract["created_at"].strip():
        errors.append("control contract created_at must be non-empty")
    for key in expected.keys() - {"schema_version", "task_id", "created_at"}:
        if contract.get(key) != expected[key]:
            errors.append(f"control contract {key} is invalid")
    return errors


def build_control_slice(
    task_id: str,
    agent: str,
    work_item_ids: list[str],
    contract_digest: str,
) -> dict[str, Any]:
    unique_work_items = list(dict.fromkeys(str(item) for item in work_item_ids if str(item).strip()))
    if not task_id.strip() or not agent.strip() or not unique_work_items:
        raise ValueError("task_id, agent, and work_item_ids are required")
    return {
        "schema_version": SLICE_SCHEMA_VERSION,
        "task_id": task_id,
        "agent": agent,
        "work_item_ids": unique_work_items,
        "control_contract_ref": CONTROL_CONTRACT_REF,
        "control_contract_digest": contract_digest,
        "priority_class": PRIORITY_CLASS,
        "load_before": list(LOAD_BEFORE),
        "missing_or_invalid": "block",
    }


def validate_control_slice(
    control_slice: dict[str, Any],
    task_id: str,
    agent: str,
    work_item_ids: list[str],
    contract_digest: str,
) -> list[str]:
    try:
        expected = build_control_slice(task_id, agent, work_item_ids, contract_digest)
    except ValueError as exc:
        return [str(exc)]
    errors = _exact_keys(control_slice, set(expected), "control slice")
    if errors:
        return errors
    for key, expected_value in expected.items():
        if control_slice.get(key) != expected_value:
            errors.append(f"control slice {key} does not match")
    return errors
