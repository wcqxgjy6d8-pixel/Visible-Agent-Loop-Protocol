from __future__ import annotations

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
    dependencies: list[dict[str, Any]] = []
    for edge in dependency_edges(role_assignments):
        dependency = {
            "id": f"{edge['prerequisite_role']}-before-{edge['dependent_role']}",
            **edge,
            "dependent_refs": role_expected_refs(
                str(edge["dependent_agent"]),
                str(edge["dependent_role"]),
            ),
        }
        dependencies.append(dependency)
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
    expected = build_submission_dependencies(task_id, role_assignments)
    errors: list[str] = []
    if set(document) != set(expected):
        errors.append("submission dependency artifact has unexpected or missing top-level fields")
    for key in ["schema_version", "task_id", "ordering_source"]:
        if document.get(key) != expected[key]:
            errors.append(f"submission dependency {key} does not match the routed task")
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
        latest = _latest_terminal_receipt(
            receipts,
            str(dependency.get("prerequisite_agent") or ""),
            refs,
        )
        if not _receipt_matches(latest, "dispatch_completed", refs):
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
    errors: list[str] = []
    for dependency in document.get("dependencies") or []:
        if not isinstance(dependency, dict):
            errors.append("invalid dependency entry")
            continue
        prerequisite_refs = [str(ref) for ref in dependency.get("prerequisite_refs") or []]
        dependent_refs = [str(ref) for ref in dependency.get("dependent_refs") or []]
        for index, receipt in enumerate(receipts):
            if receipt.get("agent") != dependency.get("dependent_agent"):
                continue
            if not _receipt_matches(receipt, dependent_event, dependent_refs):
                continue
            latest = _latest_terminal_receipt(
                receipts[:index],
                str(dependency.get("prerequisite_agent") or ""),
                prerequisite_refs,
            )
            valid_refs = all(
                _valid_evidence_ref(task_dir, ref, evidence_status)
                for ref in prerequisite_refs
            )
            if not _receipt_matches(latest, prerequisite_event, prerequisite_refs) or not valid_refs:
                errors.append(
                    f"{dependency.get('id', 'unknown')} was not satisfied before receipt line {index + 1}"
                )
    return errors


def _latest_terminal_receipt(
    receipts: list[dict[str, Any]],
    agent: str,
    required_refs: list[str],
) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for receipt in receipts:
        receipt_refs = {str(ref) for ref in receipt.get("expected_refs") or []}
        if (
            receipt.get("agent") == agent
            and receipt.get("event") in TERMINAL_RECEIPT_EVENTS
            and set(required_refs).issubset(receipt_refs)
        ):
            latest = receipt
    return latest


def _receipt_matches(receipt: dict[str, Any], event: str, required_refs: list[str]) -> bool:
    if receipt.get("event") != event:
        return False
    receipt_refs = {str(ref) for ref in receipt.get("expected_refs") or []}
    return set(str(ref) for ref in required_refs).issubset(receipt_refs)


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
