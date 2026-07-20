from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .control_plane import write_json
from .submission import TERMINAL_RECEIPT_EVENTS, work_item_identity


TERMINAL_RUN_STATUSES = {"success", "error", "timeout", "interrupted"}
DEFAULT_API_URL = "http://127.0.0.1:8123"


class LangGraphAdapterError(RuntimeError):
    pass


def _api_url(value: str | None = None) -> str:
    return (value or os.environ.get("VALP_LANGGRAPH_API_URL") or DEFAULT_API_URL).rstrip("/")


def _request(
    api_url: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 10.0,
) -> Any:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        api_url + path,
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise LangGraphAdapterError(f"LangGraph API {method} {path} failed: HTTP {error.code}: {detail}") from error
    except (OSError, urllib.error.URLError) as error:
        raise LangGraphAdapterError(f"LangGraph API {method} {path} failed: {error}") from error
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise LangGraphAdapterError(f"LangGraph API {method} {path} returned invalid JSON") from error


def collect_langgraph_preflight(
    agent_names: list[str] | None = None,
    *,
    api_url: str | None = None,
) -> dict[str, Any]:
    endpoint = _api_url(api_url)
    report: dict[str, Any] = {
        "generated_at": _now_iso(),
        "runtime": "LangGraph API",
        "adapter_class": "hosted_local_platform",
        "status": "pass",
        "checks": {},
        "agents": {},
    }
    try:
        health = _request(endpoint, "GET", "/ok")
    except LangGraphAdapterError as error:
        report["status"] = "fail"
        report["checks"]["langgraph_api"] = {"status": "fail", "message": str(error)}
        return report
    report["checks"]["langgraph_api"] = {
        "status": "pass",
        "api_url": endpoint,
        "health": health,
    }
    for agent in agent_names or []:
        try:
            assistants = _request(
                endpoint,
                "POST",
                "/assistants/search",
                {"graph_id": agent, "limit": 10},
            )
        except LangGraphAdapterError as error:
            report["agents"][agent] = {
                "status": "fail",
                "graph_id": agent,
                "session_status": "unavailable",
                "message": str(error),
            }
            report["status"] = "fail"
            continue
        matches = assistants if isinstance(assistants, list) else []
        report["agents"][agent] = {
            "status": "pass" if matches else "fail",
            "graph_id": agent,
            "assistant_ids": [str(item.get("assistant_id")) for item in matches if item.get("assistant_id")],
            "session_status": "idle" if matches else "missing",
            "expected_refs": [],
            "notes": ["LangGraph uses thread/run identities instead of pane fields."],
        }
        if not matches:
            report["status"] = "fail"
    return report


def submit_langgraph_run(
    workspace: Path,
    task_id: str,
    agent: str,
    role: str,
    *,
    graph_id: str | None = None,
    input_data: dict[str, Any] | None = None,
    expected_refs: list[str] | None = None,
    thread_id: str | None = None,
    wait_seconds: float = 30.0,
    poll_interval_seconds: float = 0.1,
    api_url: str | None = None,
) -> dict[str, Any]:
    _validate_wait_window(wait_seconds, poll_interval_seconds)
    directory = _task_directory(workspace, task_id)
    identity = _work_item(directory, task_id, agent, role)
    refs = list(expected_refs if expected_refs is not None else identity["expected_refs"])
    dispatch_ref = f"agents/{agent}/dispatch.md"
    if not (directory / dispatch_ref).is_file():
        raise LangGraphAdapterError(f"Missing dispatch file: {dispatch_ref}")
    endpoint = _api_url(api_url)
    selected_graph = graph_id or agent
    thread = {"thread_id": thread_id} if thread_id else _request(endpoint, "POST", "/threads", {})
    actual_thread_id = str((thread or {}).get("thread_id") or "")
    if not actual_thread_id:
        raise LangGraphAdapterError("LangGraph thread creation did not return thread_id")
    runtime_input = dict(input_data or {})
    runtime_input.setdefault("task_id", task_id)
    runtime_input.setdefault("agent", agent)
    runtime_input.setdefault("role", role)
    submitted = _request(
        endpoint,
        "POST",
        f"/threads/{actual_thread_id}/runs",
        {"assistant_id": selected_graph, "input": runtime_input},
    )
    run_id = str((submitted or {}).get("run_id") or "")
    if not run_id:
        raise LangGraphAdapterError("LangGraph run submission did not return run_id")
    run_ref = f"runtime/langgraph/{run_id}"
    run_directory = directory / run_ref
    suspension_epoch = _next_suspension_epoch(directory)
    submission_record = {
        "schema_version": "valp-langgraph-submission.v1",
        "task_id": task_id,
        "agent": agent,
        "role": role,
        "work_item_id": identity["work_item_id"],
        "dispatch_id": identity["dispatch_id"],
        "dispatch_generation": identity["dispatch_generation"],
        "dispatch_ref": dispatch_ref,
        "expected_refs": refs,
        "runtime": "LangGraph API",
        "api_url": endpoint,
        "graph_id": selected_graph,
        "assistant_id": str((submitted or {}).get("assistant_id") or ""),
        "thread_id": actual_thread_id,
        "run_id": run_id,
        "submission_id": run_id,
        "suspension_epoch": suspension_epoch,
        "submitted_at": str((submitted or {}).get("created_at") or _now_iso()),
        "initial_status": str((submitted or {}).get("status") or "pending"),
        "input": runtime_input,
    }
    write_json(run_directory / "submission.json", submission_record)
    write_json(run_directory / "submitted-run.json", submitted)
    proof = {
        "adapter_record": {
            "runtime": "LangGraph API",
            "submission_id": run_id,
            "thread_id": actual_thread_id,
            "assistant_id": submission_record["assistant_id"],
            "graph_id": selected_graph,
            "submission_ref": f"{run_ref}/submission.json",
        }
    }
    _append_receipt(directory, submission_record, "dispatch_submitted", proof=proof)
    return _wait_for_run(
        directory,
        submission_record,
        wait_seconds=wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def resume_langgraph_run(
    workspace: Path,
    task_id: str,
    run_id: str,
    *,
    wait_seconds: float = 30.0,
    poll_interval_seconds: float = 0.1,
) -> dict[str, Any]:
    _validate_wait_window(wait_seconds, poll_interval_seconds)
    directory = _task_directory(workspace, task_id)
    submission_path = directory / "runtime" / "langgraph" / run_id / "submission.json"
    try:
        submission = json.loads(submission_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LangGraphAdapterError(f"Cannot load LangGraph submission {run_id}: {error}") from error
    if submission.get("task_id") != task_id or submission.get("run_id") != run_id:
        raise LangGraphAdapterError("LangGraph submission identity does not match resume request")
    return _wait_for_run(
        directory,
        submission,
        wait_seconds=wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def _wait_for_run(
    directory: Path,
    submission: dict[str, Any],
    *,
    wait_seconds: float,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    endpoint = str(submission["api_url"])
    thread_id = str(submission["thread_id"])
    run_id = str(submission["run_id"])
    deadline = time.monotonic() + wait_seconds
    current: dict[str, Any] = {}
    while True:
        value = _request(endpoint, "GET", f"/threads/{thread_id}/runs/{run_id}")
        current = value if isinstance(value, dict) else {}
        if current.get("status") in TERMINAL_RUN_STATUSES:
            break
        if time.monotonic() >= deadline:
            waiting = {
                "schema_version": "valp-langgraph-run.v1",
                "task_id": submission["task_id"],
                "run_id": run_id,
                "thread_id": thread_id,
                "status": "waiting",
                "runtime_status": str(current.get("status") or "unknown"),
                "wait_timeout_seconds": wait_seconds,
                "worker_cancelled": False,
                "resume_command": f"valp adapter langgraph resume {submission['task_id']} --run-id {run_id}",
                "observed_at": _now_iso(),
            }
            write_json(directory / "runtime" / "langgraph" / run_id / "run.json", waiting)
            return {"status": "waiting", "run": waiting, "run_ref": f"runtime/langgraph/{run_id}"}
        time.sleep(max(0.01, poll_interval_seconds))

    join_value = _request(endpoint, "GET", f"/threads/{thread_id}/runs/{run_id}/join")
    state_value = _request(endpoint, "GET", f"/threads/{thread_id}/state")
    run_ref = f"runtime/langgraph/{run_id}"
    run_directory = directory / run_ref
    write_json(run_directory / "terminal-run.json", current)
    write_json(run_directory / "output.json", join_value)
    write_json(run_directory / "state.json", state_value)
    refs = [str(ref) for ref in submission.get("expected_refs") or []]
    missing_refs = [ref for ref in refs if not _valid_evidence(directory / ref)]
    runtime_status = str(current.get("status") or "unknown")
    error_value = join_value.get("__error__") if isinstance(join_value, dict) else None
    checkpoint = state_value.get("checkpoint") if isinstance(state_value, dict) else {}
    checkpoint_id = str((checkpoint or {}).get("checkpoint_id") or "")
    terminal_record: dict[str, Any] = {
        "schema_version": "valp-langgraph-run.v1",
        "task_id": submission["task_id"],
        "agent": submission["agent"],
        "role": submission["role"],
        "run_id": run_id,
        "submission_id": run_id,
        "thread_id": thread_id,
        "graph_id": submission["graph_id"],
        "assistant_id": submission["assistant_id"],
        "status": "completed" if runtime_status == "success" and not missing_refs else "blocked",
        "runtime_status": runtime_status,
        "checkpoint_id": checkpoint_id,
        "expected_refs": refs,
        "missing_refs": missing_refs,
        "failure_reason": error_value or ({"error": "missing_expected_evidence", "refs": missing_refs} if missing_refs else None),
        "worker_cancelled": False,
        "completed_at": str(current.get("updated_at") or _now_iso()),
        "output_ref": f"{run_ref}/output.json",
        "state_ref": f"{run_ref}/state.json",
    }
    write_json(run_directory / "run.json", terminal_record)
    common_proof = {
        "adapter_record": {
            "runtime": "LangGraph API",
            "submission_id": run_id,
            "thread_id": thread_id,
            "assistant_id": submission["assistant_id"],
            "graph_id": submission["graph_id"],
            "run_ref": f"{run_ref}/run.json",
        },
        "runtime_state": runtime_status,
        "checkpoint_id": checkpoint_id,
        "output_ref": f"{run_ref}/output.json",
        "state_ref": f"{run_ref}/state.json",
    }
    if terminal_record["status"] == "completed":
        common_proof["evidence"] = [_evidence_record(directory, ref) for ref in refs]
        receipt = _append_receipt(directory, submission, "dispatch_completed", proof=common_proof)
    else:
        common_proof["failure_reason"] = terminal_record["failure_reason"]
        common_proof["missing_refs"] = missing_refs
        receipt = _append_receipt(directory, submission, "dispatch_blocked", proof=common_proof)
    return {
        "status": terminal_record["status"],
        "run": terminal_record,
        "run_ref": run_ref,
        "receipt": receipt,
    }


def _task_directory(workspace: Path, task_id: str) -> Path:
    if not task_id or "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
        raise LangGraphAdapterError("Invalid task_id")
    directory = workspace.resolve() / ".herdr-loop" / "tasks" / task_id
    if not directory.is_dir():
        raise LangGraphAdapterError(f"Missing VALP task directory: {directory}")
    return directory


def _validate_wait_window(wait_seconds: float, poll_interval_seconds: float) -> None:
    if not math.isfinite(wait_seconds) or wait_seconds < 0:
        raise LangGraphAdapterError("LangGraph wait_seconds must be a finite non-negative number")
    if not math.isfinite(poll_interval_seconds) or poll_interval_seconds < 0:
        raise LangGraphAdapterError("LangGraph poll_interval_seconds must be a finite non-negative number")


def _work_item(directory: Path, task_id: str, agent: str, role: str) -> dict[str, Any]:
    path = directory / "submission-dependencies.json"
    if path.is_file():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise LangGraphAdapterError(f"Invalid submission-dependencies.json: {error}") from error
        matches = [
            item
            for item in document.get("work_items") or []
            if item.get("agent") == agent and item.get("role") == role
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise LangGraphAdapterError(f"Ambiguous work item for {agent}/{role}")
    return work_item_identity(task_id, agent, role)


def _load_receipts(directory: Path) -> list[dict[str, Any]]:
    path = directory / "dispatch-receipts.jsonl"
    if not path.exists():
        return []
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise LangGraphAdapterError(f"Invalid dispatch-receipts.jsonl line {line_number}: {error}") from error
        if isinstance(value, dict):
            records.append(value)
    return records


def _next_suspension_epoch(directory: Path) -> int:
    epochs = [
        int(receipt["suspension_epoch"])
        for receipt in _load_receipts(directory)
        if receipt.get("event") in TERMINAL_RECEIPT_EVENTS
        and type(receipt.get("suspension_epoch")) is int
    ]
    return max(epochs, default=0) + 1


def _append_receipt(
    directory: Path,
    submission: dict[str, Any],
    event: str,
    *,
    proof: dict[str, Any],
) -> dict[str, Any]:
    receipts = _load_receipts(directory)
    receipt_source = f"{submission['task_id']}:{submission['run_id']}:{event}"
    receipt_id = "sha256:" + hashlib.sha256(receipt_source.encode("utf-8")).hexdigest()
    for receipt in receipts:
        if receipt.get("receipt_id") == receipt_id:
            return receipt
    sequence = max(
        [
            int(receipt["event_sequence"])
            for receipt in receipts
            if receipt.get("schema_version") == "valp-dispatch-receipt.v2"
            and type(receipt.get("event_sequence")) is int
        ],
        default=0,
    ) + 1
    receipt: dict[str, Any] = {
        "schema_version": "valp-dispatch-receipt.v2",
        "receipt_id": receipt_id,
        "task_id": submission["task_id"],
        "event_sequence": sequence,
        "ts": _now_iso(),
        "agent": submission["agent"],
        "role": submission["role"],
        "work_item_id": submission["work_item_id"],
        "dispatch_id": submission["dispatch_id"],
        "dispatch_generation": int(submission["dispatch_generation"]),
        "event": event,
        "dispatch_ref": submission["dispatch_ref"],
        "expected_refs": list(submission.get("expected_refs") or []),
        "proof": proof,
        "summary": _receipt_summary(event, proof),
    }
    if event in TERMINAL_RECEIPT_EVENTS:
        receipt["suspension_epoch"] = int(submission["suspension_epoch"])
        receipt["exit_code"] = 0 if event == "dispatch_completed" else 1
    path = directory / "dispatch-receipts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return receipt


def _receipt_summary(event: str, proof: dict[str, Any]) -> str:
    if event == "dispatch_submitted":
        return "LangGraph API accepted the run and returned a concrete run ID."
    if event == "dispatch_completed":
        return "LangGraph runtime succeeded and every expected evidence ref exists."
    reason = proof.get("failure_reason")
    return f"LangGraph run did not satisfy the VALP evidence gate: {reason}"


def _valid_evidence(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _evidence_record(directory: Path, ref: str) -> dict[str, Any]:
    path = directory / ref
    payload = path.read_bytes()
    return {
        "ref": ref,
        "content_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
