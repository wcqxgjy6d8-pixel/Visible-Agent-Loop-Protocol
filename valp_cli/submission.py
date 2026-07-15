from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any


INVALID_EVIDENCE_STATUSES = {"invalid", "superseded", "rejected", "blocked"}
ORDERING_SOURCE = "dispatch-receipts.jsonl#line-order"
ROLE_ORDER = ["coordinator", "implementer", "researcher", "prototype", "reviewer", "other"]
TERMINAL_RECEIPT_EVENTS = {
    "dispatch_completed",
    "dispatch_blocked",
    "manual_result_attested",
    "manual_blocked",
}
NON_RUNTIME_PROOF_TERMS = {
    "dry_run",
    "dry-run",
    "simulation",
    "simulated",
    "subagent",
    "sub-agent",
    "manual_attestation",
}


def _proof_contains_concrete_signal(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for raw_key, signal in value.items():
        key = str(raw_key).lower()
        meaningful = isinstance(signal, str) and bool(signal.strip())
        identity_key = (
            key in {"id", "ref"}
            or key.endswith("_id")
            or key.endswith("_ref")
        )
        if identity_key and meaningful:
            return True
        if isinstance(signal, dict) and _proof_contains_concrete_signal(signal):
            return True
        if isinstance(signal, list) and any(
            _proof_contains_concrete_signal(item) for item in signal if isinstance(item, dict)
        ):
            return True
    return False


def deterministic_receipt_ledger_errors(
    receipts: list[dict[str, Any]],
    task_id: str = "",
) -> list[str]:
    errors: list[str] = []
    seen_receipts: dict[str, dict[str, Any]] = {}
    previous_sequence = 0
    for line_number, receipt in enumerate(receipts, 1):
        if receipt.get("schema_version") != "valp-dispatch-receipt.v2":
            continue
        receipt_id = receipt.get("receipt_id")
        if not isinstance(receipt_id, str) or not receipt_id:
            errors.append(f"line {line_number} has an invalid receipt_id")
        elif receipt_id in seen_receipts:
            if receipt != seen_receipts[receipt_id]:
                errors.append(f"line {line_number} has a conflicting duplicate receipt_id {receipt_id}")
            continue
        else:
            seen_receipts[receipt_id] = receipt
        if task_id and receipt.get("task_id") != task_id:
            errors.append(f"line {line_number} belongs to a different task")
        sequence = receipt.get("event_sequence")
        if type(sequence) is not int or sequence < 1:
            errors.append(f"line {line_number} has an invalid event_sequence")
        elif sequence <= previous_sequence:
            errors.append(f"line {line_number} event_sequence is not strictly increasing")
        else:
            previous_sequence = sequence
        generation = receipt.get("dispatch_generation")
        if type(generation) is not int or generation < 1:
            errors.append(f"line {line_number} has an invalid dispatch_generation")
        suspension_epoch = receipt.get("suspension_epoch")
        if suspension_epoch is not None and (
            type(suspension_epoch) is not int or suspension_epoch < 1
        ):
            errors.append(f"line {line_number} has an invalid suspension_epoch")
        if receipt.get("event") in TERMINAL_RECEIPT_EVENTS and type(suspension_epoch) is not int:
            errors.append(f"line {line_number} terminal receipt is missing a valid suspension_epoch")
    return errors


def has_concrete_runtime_submission_proof(receipt: dict[str, Any]) -> bool:
    if receipt.get("event") != "dispatch_submitted":
        return False
    proof = receipt.get("proof")
    if not isinstance(proof, dict) or not proof:
        return False
    proof_text = json.dumps(proof, sort_keys=True).lower()
    return (
        not any(term in proof_text for term in NON_RUNTIME_PROOF_TERMS)
        and _proof_contains_concrete_signal(proof)
    )


def role_expected_refs(agent: str, role: str) -> list[str]:
    refs = {
        "coordinator": [f"agents/{agent}/self-review.md"],
        "implementer": [f"agents/{agent}/evidence.md", "evidence/verification.md"],
        "researcher": [f"agents/{agent}/evidence.md"],
        "prototype": [f"agents/{agent}/prototype.md"],
        "reviewer": [f"agents/{agent}/review.md"],
    }
    return refs.get(role, [f"agents/{agent}/evidence.md"])


def roles_for_agent(role_assignments: dict[str, str], agent: str) -> list[str]:
    assigned = {role for role, selected in role_assignments.items() if selected == agent}
    return [role for role in ROLE_ORDER if role in assigned] or ["other"]


def work_item_identity(task_id: str, agent: str, role: str) -> dict[str, Any]:
    return {
        "work_item_id": f"{role}:{agent}",
        "agent": agent,
        "role": role,
        "dispatch_id": f"{task_id}:{role}:1",
        "dispatch_generation": 1,
        "expected_refs": role_expected_refs(agent, role),
    }


def dependency_work_items(task_id: str, role_assignments: dict[str, str]) -> list[dict[str, Any]]:
    return [
        work_item_identity(task_id, str(role_assignments[role]), role)
        for role in ROLE_ORDER
        if role in role_assignments
    ]


def dependency_edges(role_assignments: dict[str, str]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    coordinator = role_assignments.get("coordinator")
    producers: list[tuple[str, str]] = []
    for role in ["implementer", "researcher", "prototype"]:
        agent = role_assignments.get(role)
        if not agent:
            continue
        producers.append((role, agent))
        if coordinator:
            edges.append(
                {
                    "prerequisite_role": "coordinator",
                    "prerequisite_agent": coordinator,
                    "dependent_role": role,
                    "dependent_agent": agent,
                    "prerequisite_refs": role_expected_refs(coordinator, "coordinator"),
                }
            )

    reviewer = role_assignments.get("reviewer")
    if reviewer:
        reviewer_sources = producers or ([('coordinator', coordinator)] if coordinator else [])
        for role, agent in reviewer_sources:
            edges.append(
                {
                    "prerequisite_role": role,
                    "prerequisite_agent": agent,
                    "dependent_role": "reviewer",
                    "dependent_agent": reviewer,
                    "prerequisite_refs": role_expected_refs(agent, role),
                }
            )
    return edges


def build_submission_dependencies(
    task_id: str,
    role_assignments: dict[str, str],
) -> dict[str, Any]:
    work_items = dependency_work_items(task_id, role_assignments)
    work_item_by_role = {str(item["role"]): item for item in work_items}
    dependencies: list[dict[str, Any]] = []
    for edge in dependency_edges(role_assignments):
        prerequisite_item = work_item_by_role[str(edge["prerequisite_role"])]
        dependent_item = work_item_by_role[str(edge["dependent_role"])]
        dependency = {
            "id": f"{edge['prerequisite_role']}-before-{edge['dependent_role']}",
            **edge,
            "prerequisite_work_item_id": prerequisite_item["work_item_id"],
            "prerequisite_dispatch_generation": prerequisite_item["dispatch_generation"],
            "dependent_work_item_id": dependent_item["work_item_id"],
            "dependent_dispatch_generation": dependent_item["dispatch_generation"],
            "dependent_refs": role_expected_refs(
                str(edge["dependent_agent"]),
                str(edge["dependent_role"]),
            ),
        }
        dependencies.append(dependency)
    return {
        "schema_version": "valp-submission-dependencies.v2",
        "task_id": task_id,
        "ordering_source": ORDERING_SOURCE,
        "work_items": work_items,
        "dependencies": dependencies,
    }


def build_legacy_submission_dependencies(
    task_id: str,
    role_assignments: dict[str, str],
) -> dict[str, Any]:
    dependencies: list[dict[str, Any]] = []
    for edge in dependency_edges(role_assignments):
        dependencies.append(
            {
                "id": f"{edge['prerequisite_role']}-before-{edge['dependent_role']}",
                **edge,
                "dependent_refs": role_expected_refs(
                    str(edge["dependent_agent"]),
                    str(edge["dependent_role"]),
                ),
            }
        )
    return {
        "schema_version": "valp-submission-dependencies.v1",
        "task_id": task_id,
        "ordering_source": ORDERING_SOURCE,
        "dependencies": dependencies,
    }


def validate_submission_dependencies(
    document: dict[str, Any],
    task_id: str,
    role_assignments: dict[str, str],
) -> list[str]:
    if not document:
        return ["missing submission-dependencies.json"]
    if document.get("schema_version") == "valp-submission-dependencies.v1":
        expected = build_legacy_submission_dependencies(task_id, role_assignments)
    else:
        expected = build_submission_dependencies(task_id, role_assignments)
    errors: list[str] = []
    if set(document) != set(expected):
        errors.append("submission dependency artifact has unexpected or missing top-level fields")
    for key in ["schema_version", "task_id", "ordering_source"]:
        if document.get(key) != expected[key]:
            errors.append(f"submission dependency {key} does not match the routed task")
    if expected["schema_version"] == "valp-submission-dependencies.v2":
        work_items = document.get("work_items")
        if not isinstance(work_items, list):
            errors.append("submission dependency work item list is missing or invalid")
        elif work_items != expected["work_items"]:
            errors.append(
                "submission dependency work items do not match current role assignments and required refs"
            )
    dependencies = document.get("dependencies")
    if not isinstance(dependencies, list):
        errors.append("submission dependency list is missing or invalid")
        return errors
    ids = [str(item.get("id")) for item in dependencies if isinstance(item, dict)]
    if len(ids) != len(set(ids)):
        errors.append("submission dependency ids must be unique")
    if dependencies != expected["dependencies"]:
        errors.append("submission dependencies do not match current role assignments and required refs")
    return errors


def unmet_dependencies_for_phases(
    document: dict[str, Any],
    phases: list[tuple[str, str]],
    receipts: list[dict[str, Any]],
    task_dir: Path,
    evidence_status: dict[str, Any],
    manual_mode: bool = False,
) -> list[str]:
    requested = set(phases)
    errors: list[str] = []
    for dependency in document.get("dependencies") or []:
        if not isinstance(dependency, dict):
            errors.append("invalid dependency entry")
            continue
        dependent = (
            str(dependency.get("dependent_agent") or ""),
            str(dependency.get("dependent_role") or ""),
        )
        if dependent not in requested:
            continue
        refs = [str(ref) for ref in dependency.get("prerequisite_refs") or []]
        strict_identity = (
            document.get("schema_version") == "valp-submission-dependencies.v2"
            and not manual_mode
        )
        prerequisite_identity = _dependency_work_item(
            document,
            str(dependency.get("prerequisite_work_item_id") or ""),
        )
        latest = _latest_terminal_receipt(
            receipts,
            str(dependency.get("prerequisite_agent") or ""),
            refs,
            task_id=str(document.get("task_id") or ""),
            identity=prerequisite_identity,
            strict_identity=strict_identity,
        )
        if not _receipt_matches(
            latest,
            "dispatch_completed",
            refs,
            task_id=str(document.get("task_id") or ""),
            identity=prerequisite_identity,
            strict_identity=strict_identity,
        ):
            errors.append(str(dependency.get("id") or "unknown") + " completion receipt")
            continue
        missing = [ref for ref in refs if not _valid_evidence_ref(task_dir, ref, evidence_status)]
        errors.extend(f"{dependency.get('id', 'unknown')} evidence {ref}" for ref in missing)
    return errors


def dependency_order_errors(
    document: dict[str, Any],
    receipts: list[dict[str, Any]],
    task_dir: Path,
    evidence_status: dict[str, Any],
    manual_mode: bool,
) -> list[str]:
    prerequisite_event = "manual_result_attested" if manual_mode else "dispatch_completed"
    dependent_event = "manual_delivery_attested" if manual_mode else "dispatch_submitted"
    strict_identity = (
        document.get("schema_version") == "valp-submission-dependencies.v2"
        and not manual_mode
    )
    errors: list[str] = []
    for dependency in document.get("dependencies") or []:
        if not isinstance(dependency, dict):
            errors.append("invalid dependency entry")
            continue
        prerequisite_refs = [str(ref) for ref in dependency.get("prerequisite_refs") or []]
        dependent_refs = [str(ref) for ref in dependency.get("dependent_refs") or []]
        prerequisite_identity = _dependency_work_item(
            document,
            str(dependency.get("prerequisite_work_item_id") or ""),
        )
        dependent_identity = _dependency_work_item(
            document,
            str(dependency.get("dependent_work_item_id") or ""),
        )
        for index, receipt in enumerate(receipts):
            # Legacy HERDR records remain in the append-only ledger for
            # history. Full Mode v2 ordering is evaluated against the
            # identity-bound records produced by the translation layer.
            if strict_identity and receipt.get("schema_version") != "valp-dispatch-receipt.v2":
                continue
            if receipt.get("agent") != dependency.get("dependent_agent"):
                continue
            if not _receipt_matches(receipt, dependent_event, dependent_refs):
                continue
            if not _receipt_matches(
                receipt,
                dependent_event,
                dependent_refs,
                task_id=str(document.get("task_id") or ""),
                identity=dependent_identity,
                strict_identity=strict_identity,
            ):
                errors.append(
                    f"{dependency.get('id', 'unknown')} dependent receipt identity mismatch "
                    f"at line {index + 1}"
                )
                continue
            latest = _latest_terminal_receipt(
                receipts[:index],
                str(dependency.get("prerequisite_agent") or ""),
                prerequisite_refs,
                task_id=str(document.get("task_id") or ""),
                identity=prerequisite_identity,
                strict_identity=strict_identity,
            )
            valid_refs = all(
                _valid_evidence_ref(task_dir, ref, evidence_status)
                for ref in prerequisite_refs
            )
            if not _receipt_matches(
                latest,
                prerequisite_event,
                prerequisite_refs,
                task_id=str(document.get("task_id") or ""),
                identity=prerequisite_identity,
                strict_identity=strict_identity,
            ) or not valid_refs:
                errors.append(
                    f"{dependency.get('id', 'unknown')} was not satisfied before receipt line {index + 1}"
                )
    return errors


def _latest_terminal_receipt(
    receipts: list[dict[str, Any]],
    agent: str,
    required_refs: list[str],
    task_id: str = "",
    identity: dict[str, Any] | None = None,
    strict_identity: bool = False,
) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for receipt in receipts:
        if (
            receipt.get("agent") == agent
            and receipt.get("event") in TERMINAL_RECEIPT_EVENTS
            and _receipt_matches(
                receipt,
                str(receipt.get("event") or ""),
                required_refs,
                task_id=task_id,
                identity=identity,
                strict_identity=strict_identity,
            )
        ):
            latest = receipt
    return latest


def _receipt_matches(
    receipt: dict[str, Any],
    event: str,
    required_refs: list[str],
    task_id: str = "",
    identity: dict[str, Any] | None = None,
    strict_identity: bool = False,
) -> bool:
    if receipt.get("event") != event:
        return False
    receipt_refs = {str(ref) for ref in receipt.get("expected_refs") or []}
    if not set(str(ref) for ref in required_refs).issubset(receipt_refs):
        return False
    if not strict_identity:
        if identity and str(receipt.get("event") or "").startswith("manual_"):
            return (
                receipt.get("agent") == identity.get("agent")
                and receipt.get("role") == identity.get("role")
            )
        return True
    if receipt.get("schema_version") != "valp-dispatch-receipt.v2" or not identity:
        return False
    expected = {
        "task_id": task_id,
        "agent": identity.get("agent"),
        "role": identity.get("role"),
        "work_item_id": identity.get("work_item_id"),
        "dispatch_id": identity.get("dispatch_id"),
        "dispatch_generation": identity.get("dispatch_generation"),
    }
    return all(receipt.get(key) == value for key, value in expected.items())


def _dependency_work_item(
    document: dict[str, Any],
    work_item_id: str,
) -> dict[str, Any] | None:
    for item in document.get("work_items") or []:
        if isinstance(item, dict) and item.get("work_item_id") == work_item_id:
            return item
    return None


def _valid_evidence_ref(
    task_dir: Path,
    ref: str,
    evidence_status: dict[str, Any],
) -> bool:
    if not _safe_task_ref(ref):
        return False
    try:
        candidate = (task_dir / ref).resolve()
        candidate.relative_to(task_dir.resolve())
    except (OSError, ValueError):
        return False
    if not candidate.is_file() or candidate.stat().st_size == 0:
        return False
    records = evidence_status.get("evidence") or evidence_status.get("items") or {}
    if isinstance(records, dict):
        record = records.get(ref) or {}
        status = record.get("status") if isinstance(record, dict) else record
        if str(status or "valid").lower() in INVALID_EVIDENCE_STATUSES:
            return False
    return True


def _safe_task_ref(ref: str) -> bool:
    if not ref or ref.startswith("/") or "\\" in ref or "\n" in ref or "\r" in ref:
        return False
    path = PurePosixPath(ref)
    return not path.is_absolute() and ".." not in path.parts
