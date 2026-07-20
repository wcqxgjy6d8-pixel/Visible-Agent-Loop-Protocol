from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class TaskState(TypedDict, total=False):
    task_id: str
    agent: str
    role: str
    attempt: str
    claim: str
    output_refs: list[str]
    verification: str
    control_contract_ref: str
    control_contract_digest: str
    control_contract_status: str


def _task_dir(state: TaskState) -> Path:
    workspace = os.environ.get("VALP_DEMO_WORKSPACE")
    task_id = state.get("task_id", "")
    if not workspace:
        raise RuntimeError("VALP_DEMO_WORKSPACE is required")
    if not task_id or "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
        raise RuntimeError("invalid task_id")
    directory = Path(workspace).resolve() / ".herdr-loop" / "tasks" / task_id
    if not directory.is_dir():
        raise RuntimeError(f"task directory does not exist: {task_id}")
    return directory


def _write(directory: Path, ref: str, content: str) -> None:
    path = directory / ref
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _control_ack(directory: Path, state: TaskState) -> dict[str, str]:
    agent = state.get("agent", "")
    role = state.get("role", "")
    contract_ref = "control-contract.json"
    slice_ref = f"control-slices/{agent}.json"
    contract_path = directory / contract_ref
    slice_path = directory / slice_ref
    if not contract_path.is_file() or not slice_path.is_file():
        raise RuntimeError("missing worker control contract or control slice")
    contract_bytes = contract_path.read_bytes()
    digest = "sha256:" + hashlib.sha256(contract_bytes).hexdigest()
    contract = json.loads(contract_bytes)
    control_slice = json.loads(slice_path.read_text(encoding="utf-8"))
    expected_work_item = f"{role}:{agent}"
    if contract.get("task_id") != state.get("task_id"):
        raise RuntimeError("control contract task identity mismatch")
    if contract.get("priority_class") != "highest_runtime_control":
        raise RuntimeError("control contract priority mismatch")
    if (contract.get("worker_ack") or {}).get("status") != "honored":
        raise RuntimeError("control contract acknowledgement policy mismatch")
    if control_slice.get("task_id") != state.get("task_id") or control_slice.get("agent") != agent:
        raise RuntimeError("control slice identity mismatch")
    if control_slice.get("control_contract_ref") != contract_ref or control_slice.get("control_contract_digest") != digest:
        raise RuntimeError("control slice digest mismatch")
    if expected_work_item not in (control_slice.get("work_item_ids") or []):
        raise RuntimeError("control slice work-item mismatch")
    return {
        "control_contract_ref": contract_ref,
        "control_contract_digest": digest,
        "control_contract_status": "honored",
    }


def coordinate(state: TaskState) -> TaskState:
    directory = _task_dir(state)
    control = _control_ack(directory, state)
    ref = "agents/langgraph_coordinator/self-review.md"
    _write(
        directory,
        ref,
        "# Coordinator Self-Review\n\n"
        "The task intentionally preserves a runtime-success/evidence-missing failure before repair.\n\n"
        f"control_contract_ref: {control['control_contract_ref']}\n\n"
        f"control_contract_digest: {control['control_contract_digest']}\n\n"
        "control_contract_status: honored\n\n"
        "## Recommendations\n\n- not_required\n",
    )
    return {"claim": "coordination evidence recorded", "output_refs": [ref], **control}


def implement(state: TaskState) -> TaskState:
    directory = _task_dir(state)
    control = _control_ack(directory, state)
    if state.get("attempt") != "repair":
        return {
            "claim": "Report generated successfully.",
            "output_refs": [
                "agents/langgraph_worker/evidence.md",
                "evidence/verification.md",
            ],
            **control,
        }
    report_ref = "evidence/verification.md"
    evidence_ref = "agents/langgraph_worker/evidence.md"
    report = (
        "# Runtime Report\n\n"
        "status: generated\n"
        "task: VALP-NON-HERDR-E2E-001\n"
        "marker: LANGGRAPH-REPAIR-PASS\n"
    )
    _write(directory, report_ref, report)
    digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
    _write(
        directory,
        evidence_ref,
        "# Worker Evidence\n\n"
        f"Report: `{report_ref}`\n\n"
        f"SHA-256: `{digest}`\n\n"
        "The first runtime run returned success without these files. This repair run uses the same thread identity.\n\n"
        f"control_contract_ref: {control['control_contract_ref']}\n\n"
        f"control_contract_digest: {control['control_contract_digest']}\n\n"
        "control_contract_status: honored\n\n"
        "## Recommendations\n\n- Keep missing expected evidence fail-closed.\n",
    )
    return {"claim": "Report generated successfully.", "output_refs": [evidence_ref, report_ref], **control}


def review(state: TaskState) -> TaskState:
    directory = _task_dir(state)
    control = _control_ack(directory, state)
    report_ref = "evidence/verification.md"
    evidence_ref = "agents/langgraph_worker/evidence.md"
    report_path = directory / report_ref
    evidence_path = directory / evidence_ref
    if not report_path.is_file() or "LANGGRAPH-REPAIR-PASS" not in report_path.read_text(encoding="utf-8"):
        raise RuntimeError("independent review could not verify the repaired report")
    if not evidence_path.is_file():
        raise RuntimeError("independent review could not verify worker evidence")
    review_ref = "agents/langgraph_reviewer/review.md"
    report_digest = hashlib.sha256(report_path.read_bytes()).hexdigest()
    _write(
        directory,
        review_ref,
        "# Independent Review\n\n"
        "Verdict: PASS\n\n"
        f"Verified: `{report_ref}`\n\n"
        f"SHA-256: `{report_digest}`\n\n"
        "The review ran in a separate LangGraph thread/run after the repair completion receipt.\n\n"
        f"control_contract_ref: {control['control_contract_ref']}\n\n"
        f"control_contract_digest: {control['control_contract_digest']}\n\n"
        "control_contract_status: honored\n\n"
        "## Recommendations\n\n- not_required\n",
    )
    return {"verification": "pass", "output_refs": [review_ref], **control}


def _graph(node_name: str, node):
    builder = StateGraph(TaskState)
    builder.add_node(node_name, node)
    builder.add_edge(START, node_name)
    builder.add_edge(node_name, END)
    return builder.compile()


langgraph_coordinator = _graph("coordinate", coordinate)
langgraph_worker = _graph("implement", implement)
langgraph_reviewer = _graph("review", review)
