from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from .control_plane import write_json
from .protocol_receipts import (
    ApprovalBinding,
    ProofBinding,
    ReceiptDraft,
    ReceiptMode,
    ReceiptProofKind,
    digest,
    propose_receipt_append,
    receipt_subject_digest,
)
from .receipt_store import ReceiptStore, ReceiptStoreError, UNKNOWN_OR_COMMITTED_OUTCOME
from .submission import TERMINAL_RECEIPT_EVENTS, unmet_dependencies_for_phases, work_item_identity


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
    installation_id, leader_epoch = _reference_identity(workspace, directory, task_id)
    _require_unmixed_v3_ledger(directory)
    identity = _work_item(directory, task_id, agent, role)
    refs = list(expected_refs if expected_refs is not None else identity["expected_refs"])
    dispatch_ref = f"agents/{agent}/dispatch.md"
    if not (directory / dispatch_ref).is_file():
        raise LangGraphAdapterError(f"Missing dispatch file: {dispatch_ref}")
    dependencies_path = directory / "submission-dependencies.json"
    if dependencies_path.is_file():
        try:
            dependencies = json.loads(dependencies_path.read_text(encoding="utf-8"))
            evidence_status_path = directory / "evidence-status.json"
            evidence_status = (
                json.loads(evidence_status_path.read_text(encoding="utf-8"))
                if evidence_status_path.is_file()
                else {}
            )
        except json.JSONDecodeError as error:
            raise LangGraphAdapterError(f"Invalid LangGraph dependency evidence: {error}") from error
        receipts = load_langgraph_v3_receipts(directory)
        unmet = unmet_dependencies_for_phases(
            dependencies,
            [(agent, role)],
            receipts,
            directory,
            evidence_status,
        )
        if unmet:
            raise LangGraphAdapterError("LangGraph submission dependencies are unmet: " + "; ".join(unmet))
    runtime_input = dict(input_data or {})
    runtime_input.setdefault("task_id", task_id)
    runtime_input.setdefault("agent", agent)
    runtime_input.setdefault("role", role)
    endpoint = _api_url(api_url)
    selected_graph = graph_id or agent
    intent_id = digest(
        {
            "task_id": task_id,
            "agent": agent,
            "role": role,
            "work_item_id": identity["work_item_id"],
            "dispatch_id": identity["dispatch_id"],
            "dispatch_generation": identity["dispatch_generation"],
            "graph_id": selected_graph,
            "requested_thread_id": thread_id,
            "input": runtime_input,
        }
    )
    intent_path = _intent_path(directory, intent_id)
    with _submission_intent_lock(intent_path):
        if intent_path.is_file():
            try:
                prior_intent = json.loads(intent_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as error:
                raise LangGraphAdapterError("LangGraph submission intent is malformed") from error
            if not isinstance(prior_intent, dict) or prior_intent.get("intent_id") != intent_id:
                raise LangGraphAdapterError("LangGraph submission intent identity conflicts")
            if prior_intent.get("status") == "prepared":
                raise LangGraphAdapterError(
                    "LangGraph provider outcome is unknown; reconciliation required before redispatch"
                )
            submission_record = prior_intent.get("submission")
            if prior_intent.get("status") != "accepted" or not isinstance(submission_record, dict):
                raise LangGraphAdapterError("LangGraph submission intent has an unsupported state")
            _append_receipt(directory, submission_record, "dispatch_submitted", proof=prior_intent["proof"])
            return _wait_for_run(
                directory,
                submission_record,
                wait_seconds=wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )

        _write_adoption_marker(directory, task_id)
        thread = {"thread_id": thread_id} if thread_id else _request(endpoint, "POST", "/threads", {})
        actual_thread_id = str((thread or {}).get("thread_id") or "")
        if not actual_thread_id:
            raise LangGraphAdapterError("LangGraph thread creation did not return thread_id")
        request_payload = {
            "assistant_id": selected_graph,
            "input": runtime_input,
            "metadata": {"valp_submission_intent_id": intent_id},
        }
        prepared_intent = {
            "schema_version": "valp-langgraph-submission-intent.v1",
            "intent_id": intent_id,
            "task_id": task_id,
            "work_item_id": identity["work_item_id"],
            "dispatch_id": identity["dispatch_id"],
            "dispatch_generation": identity["dispatch_generation"],
            "thread_id": actual_thread_id,
            "request_payload": request_payload,
            "request_payload_digest": digest(request_payload),
            "status": "prepared",
        }
        write_json(intent_path, prepared_intent)
    submitted = _request(
        endpoint,
        "POST",
        f"/threads/{actual_thread_id}/runs",
        request_payload,
    )
    run_id = str((submitted or {}).get("run_id") or "")
    if not run_id:
        raise LangGraphAdapterError("LangGraph run submission did not return run_id")
    run_ref = f"runtime/langgraph/{run_id}"
    run_directory = directory / run_ref
    suspension_epoch = _next_suspension_epoch(directory, installation_id, leader_epoch, task_id)
    submission_record = {
        "schema_version": "valp-langgraph-submission.v1",
        "task_id": task_id,
        "agent": agent,
        "role": role,
        "work_item_id": identity["work_item_id"],
        "dispatch_id": identity["dispatch_id"],
        "dispatch_generation": identity["dispatch_generation"],
        "attempt_id": f"langgraph:{run_id}",
        "installation_id": installation_id,
        "leader_epoch": leader_epoch,
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
        "request_payload": request_payload,
        "payload_digest": digest(request_payload),
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
    write_json(
        intent_path,
        {
            **prepared_intent,
            "status": "accepted",
            "run_id": run_id,
            "provider_response_digest": digest(submitted),
            "submission": submission_record,
            "proof": proof,
        },
    )
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
    installation_id, leader_epoch = _reference_identity(workspace, directory, task_id)
    _require_unmixed_v3_ledger(directory)
    submission_path = directory / "runtime" / "langgraph" / run_id / "submission.json"
    try:
        submission = json.loads(submission_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LangGraphAdapterError(f"Cannot load LangGraph submission {run_id}: {error}") from error
    if (
        submission.get("task_id") != task_id
        or submission.get("run_id") != run_id
        or submission.get("installation_id") != installation_id
        or submission.get("leader_epoch") != leader_epoch
        or submission.get("attempt_id") != f"langgraph:{run_id}"
    ):
        raise LangGraphAdapterError("LangGraph submission identity does not match resume request")
    _load_validated_v3_ledger(directory, installation_id, leader_epoch, task_id)
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


def _v3_ledger_path(directory: Path) -> Path:
    return directory / "runtime" / "langgraph" / "receipts.v3.jsonl"


def _intent_path(directory: Path, intent_id: str) -> Path:
    return directory / "runtime" / "langgraph" / "intents" / intent_id[7:] / "intent.json"


@contextmanager
def _submission_intent_lock(intent_path: Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    lock_path = intent_path.with_name("intent.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise LangGraphAdapterError(f"Cannot open LangGraph submission intent lock: {error}") from error
    with os.fdopen(descriptor, "r+b", closefd=True) as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            try:
                if os.name == "nt":  # pragma: no cover - Windows parity is outside MVP-E.
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (ImportError, AttributeError) as error:
                raise LangGraphAdapterError("No supported LangGraph submission intent lock") from error
            except OSError as error:
                if error.errno not in {
                    errno.EACCES,
                    errno.EAGAIN,
                    getattr(errno, "EDEADLK", errno.EAGAIN),
                }:
                    raise LangGraphAdapterError(
                        f"Cannot lock LangGraph submission intent: {error}"
                    ) from error
                if time.monotonic() >= deadline:
                    raise LangGraphAdapterError("LangGraph submission intent lock timed out") from error
                time.sleep(0.01)
        try:
            yield
        finally:
            try:
                handle.seek(0)
                if os.name == "nt":  # pragma: no cover - Windows parity is outside MVP-E.
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError as error:
                raise LangGraphAdapterError(
                    f"Cannot unlock LangGraph submission intent: {error}"
                ) from error


def _expected_adoption_marker(task_id: str) -> dict[str, str]:
    return {
        "schema_version": "valp-langgraph-receipt-adoption.v1",
        "task_id": task_id,
        "ledger_ref": "runtime/langgraph/receipts.v3.jsonl",
        "compatibility_ledger_ref": "dispatch-receipts.jsonl",
        "write_schema": "valp-dispatch-receipt.v3",
    }


def _validate_adoption_marker(directory: Path, task_id: str) -> None:
    path = directory / "runtime" / "langgraph" / "adoption.json"
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LangGraphAdapterError("LangGraph v3 adoption marker is malformed") from error
    if marker != _expected_adoption_marker(task_id):
        raise LangGraphAdapterError("LangGraph v3 adoption marker conflicts with the selected ledger")


def _write_adoption_marker(directory: Path, task_id: str) -> None:
    path = directory / "runtime" / "langgraph" / "adoption.json"
    marker = _expected_adoption_marker(task_id)
    if path.is_file():
        _validate_adoption_marker(directory, task_id)
        return
    write_json(path, marker)


def _reference_identity(workspace: Path, directory: Path, task_id: str) -> tuple[str, int]:
    control_root = workspace.resolve() / ".valp"
    try:
        installation = json.loads((control_root / "installation.json").read_text(encoding="utf-8"))
        state = json.loads((control_root / "state.json").read_text(encoding="utf-8"))
        policy = json.loads((directory / "automation-policy.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LangGraphAdapterError(f"LangGraph v3 identity or approval policy is unavailable: {error}") from error
    if not all(isinstance(item, dict) for item in (installation, state, policy)):
        raise LangGraphAdapterError("LangGraph v3 identity and approval policy records must be JSON objects")
    installation_id = installation.get("installation_id")
    leader_epoch = state.get("active_leader_epoch")
    if (
        installation.get("schema_version") != "valp-installation.v1"
        or state.get("schema_version") != "valp-executable-state.v1"
        or not isinstance(installation_id, str)
        or not installation_id
        or state.get("installation_id") != installation_id
        or type(leader_epoch) is not int
        or leader_epoch < 1
        or installation.get("active_leader_epoch") != leader_epoch
    ):
        raise LangGraphAdapterError("LangGraph v3 requires a consistent installation and active non-zero Leader epoch")
    if policy.get("schema_version") != "valp-automation-policy.v1" or policy.get("approval_required") is not False:
        raise LangGraphAdapterError("LangGraph v3 requires an explicit recorded no-approval policy for this bounded path")
    if not task_id:
        raise LangGraphAdapterError("LangGraph v3 requires a task identity")
    return installation_id, leader_epoch


def _require_unmixed_v3_ledger(directory: Path) -> None:
    adoption = directory / "runtime" / "langgraph" / "adoption.json"
    if adoption.is_file():
        _validate_adoption_marker(directory, directory.name)
    compatibility = directory / "dispatch-receipts.jsonl"
    authoritative = _v3_ledger_path(directory)
    if compatibility.exists() and compatibility.stat().st_size and authoritative.exists() and authoritative.stat().st_size:
        raise LangGraphAdapterError("LangGraph v3 mixed legacy/v2 and authoritative ledgers are forbidden")
    if compatibility.exists() and compatibility.stat().st_size:
        raise LangGraphAdapterError("LangGraph v3 adoption cannot write beside a non-empty legacy/v2 ledger")


def _receipt_store(
    directory: Path,
    installation_id: str,
    leader_epoch: int,
    task_id: str,
) -> ReceiptStore:
    return ReceiptStore(_v3_ledger_path(directory), installation_id, leader_epoch, task_id)


def _load_validated_v3_ledger(
    directory: Path,
    installation_id: str,
    leader_epoch: int,
    task_id: str,
):
    try:
        ledger = _receipt_store(directory, installation_id, leader_epoch, task_id).load()
        policy = json.loads((directory / "automation-policy.json").read_text(encoding="utf-8"))
    except (ReceiptStoreError, OSError, json.JSONDecodeError) as error:
        code = error.code if isinstance(error, ReceiptStoreError) else "invalid-policy"
        raise LangGraphAdapterError(f"Cannot validate LangGraph v3 receipt ledger: {code}") from error
    policy_digest = digest(policy)
    for receipt in ledger.receipts:
        subject = receipt_subject_digest(receipt.draft)
        if (
            receipt.draft.approval_binding.status != "not_required"
            or receipt.draft.approval_binding.policy_digest != policy_digest
        ):
            raise LangGraphAdapterError("LangGraph v3 approval policy digest mismatch")
        proof_refs: dict[ReceiptProofKind, str] = {}
        for binding in receipt.draft.proof_bindings:
            proof_path = (directory / binding.proof_ref).resolve()
            try:
                proof_path.relative_to(directory.resolve())
                proof_record = json.loads(proof_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise LangGraphAdapterError("LangGraph v3 proof digest cannot be verified") from error
            if (
                not isinstance(proof_record, dict)
                or digest(proof_record) != binding.proof_digest
                or binding.subject_digest != subject
                or proof_record.get("subject_digest") != subject
                or proof_record.get("receipt_id") != receipt.draft.receipt_id
            ):
                raise LangGraphAdapterError("LangGraph v3 proof digest or subject binding mismatch")
            expected_schema = {
                ReceiptProofKind.PROCESS_BOUND: "valp-langgraph-process-proof.v1",
                ReceiptProofKind.CONTENT_BOUND: "valp-langgraph-content-proof.v1",
            }.get(binding.proof_kind)
            if expected_schema is None or proof_record.get("schema_version") != expected_schema:
                raise LangGraphAdapterError("LangGraph v3 proof kind was relabeled or is unsupported")
            proof_refs[binding.proof_kind] = binding.proof_ref
            provider_ref = proof_record.get("provider_record_ref")
            try:
                provider_path = (directory / str(provider_ref)).resolve()
                provider_path.relative_to(directory.resolve())
                provider_record = json.loads(provider_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise LangGraphAdapterError("LangGraph provider proof record cannot be verified") from error
            if digest(provider_record) != proof_record.get("provider_record_digest"):
                raise LangGraphAdapterError("LangGraph provider proof digest mismatch")
            if binding.proof_kind == ReceiptProofKind.CONTENT_BOUND and (
                proof_record.get("acknowledged") is not True
                or proof_record.get("request_payload_digest") != receipt.draft.payload_digest
                or digest(proof_record.get("request_payload")) != receipt.draft.payload_digest
            ):
                raise LangGraphAdapterError("LangGraph exact request acknowledgement mismatch")
        if proof_refs.get(ReceiptProofKind.PROCESS_BOUND) == proof_refs.get(ReceiptProofKind.CONTENT_BOUND):
            raise LangGraphAdapterError("LangGraph process and content proof records must be distinct")
    return ledger


def load_langgraph_v3_receipts(directory: Path) -> list[dict[str, Any]]:
    """Strictly load the authoritative LangGraph ledger for audit/ordering consumers."""

    task_dir = directory.resolve()
    workspace = task_dir.parents[2]
    task_id = task_dir.name
    installation_id, leader_epoch = _reference_identity(workspace, task_dir, task_id)
    _require_unmixed_v3_ledger(task_dir)
    ledger = _load_validated_v3_ledger(task_dir, installation_id, leader_epoch, task_id)
    return [dict(receipt.canonical()) for receipt in ledger.receipts]


def _next_suspension_epoch(
    directory: Path,
    installation_id: str,
    leader_epoch: int,
    task_id: str,
) -> int:
    ledger = _load_validated_v3_ledger(directory, installation_id, leader_epoch, task_id)
    epochs = [
        int(receipt.draft.suspension_epoch)
        for receipt in ledger.receipts
        if receipt.draft.event in TERMINAL_RECEIPT_EVENTS
        and type(receipt.draft.suspension_epoch) is int
    ]
    return max(epochs, default=0) + 1


def _append_receipt(
    directory: Path,
    submission: dict[str, Any],
    event: str,
    *,
    proof: dict[str, Any],
) -> dict[str, Any]:
    receipt_source = f"{submission['task_id']}:{submission['run_id']}:{event}"
    receipt_id = "sha256:" + hashlib.sha256(receipt_source.encode("utf-8")).hexdigest()
    installation_id = str(submission.get("installation_id") or "")
    leader_epoch = submission.get("leader_epoch")
    task_id = str(submission.get("task_id") or "")
    if not installation_id or type(leader_epoch) is not int or not task_id:
        raise LangGraphAdapterError("LangGraph v3 submission identity is incomplete")
    store = _receipt_store(directory, installation_id, leader_epoch, task_id)
    ledger = _load_validated_v3_ledger(directory, installation_id, leader_epoch, task_id)

    prior = next((item for item in ledger.receipts if item.draft.receipt_id == receipt_id), None)
    if prior is not None:
        expected = {
            "task_id": task_id,
            "agent": submission["agent"],
            "role": submission["role"],
            "work_item_id": submission["work_item_id"],
            "attempt_id": submission["attempt_id"],
            "dispatch_id": submission["dispatch_id"],
            "dispatch_generation": int(submission["dispatch_generation"]),
            "event": event,
            "payload_digest": submission["payload_digest"],
        }
        if any(getattr(prior.draft, key) != value for key, value in expected.items()):
            raise LangGraphAdapterError("LangGraph v3 exact retry conflicts with the committed receipt")
        return dict(prior.canonical())

    policy = json.loads((directory / "automation-policy.json").read_text(encoding="utf-8"))
    base = ReceiptDraft(
        receipt_id=receipt_id,
        installation_id=installation_id,
        leader_epoch=leader_epoch,
        task_id=task_id,
        agent=str(submission["agent"]),
        role=str(submission["role"]),
        work_item_id=str(submission["work_item_id"]),
        attempt_id=str(submission["attempt_id"]),
        dispatch_id=str(submission["dispatch_id"]),
        dispatch_generation=int(submission["dispatch_generation"]),
        mode=ReceiptMode.FULL,
        event_sequence=ledger.revision + 1,
        expected_revision=ledger.revision,
        prior_receipt_digest=ledger.tail_digest,
        event=event,
        ts=str(submission.get("submitted_at") or _now_iso()),
        dispatch_ref=str(submission["dispatch_ref"]),
        payload_digest=str(submission["payload_digest"]),
        expected_refs=tuple(str(ref) for ref in submission.get("expected_refs") or []),
        proof_bindings=(),
        approval_binding=ApprovalBinding("not_required", digest(policy)),
        suspension_epoch=(int(submission["suspension_epoch"]) if event in TERMINAL_RECEIPT_EVENTS else None),
    )
    subject = receipt_subject_digest(base)
    provider_ref = (
        f"runtime/langgraph/{submission['run_id']}/submitted-run.json"
        if event == "dispatch_submitted"
        else f"runtime/langgraph/{submission['run_id']}/terminal-run.json"
    )
    try:
        provider_record = json.loads((directory / provider_ref).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LangGraphAdapterError("LangGraph provider proof record is unavailable") from error
    process_record = {
        "schema_version": "valp-langgraph-process-proof.v1",
        "receipt_id": receipt_id,
        "event": event,
        "attempt_id": submission["attempt_id"],
        "subject_digest": subject,
        "provider_record_ref": provider_ref,
        "provider_record_digest": digest(provider_record),
        "adapter_proof": proof,
    }
    content_record = {
        "schema_version": "valp-langgraph-content-proof.v1",
        "receipt_id": receipt_id,
        "event": event,
        "attempt_id": submission["attempt_id"],
        "subject_digest": subject,
        "acknowledged": True,
        "request_payload": submission["request_payload"],
        "request_payload_digest": submission["payload_digest"],
        "provider_record_ref": provider_ref,
        "provider_record_digest": digest(provider_record),
        "adapter_proof": proof,
    }
    process_ref = f"runtime/langgraph/{submission['run_id']}/receipt-proofs/{event}-process.json"
    content_ref = f"runtime/langgraph/{submission['run_id']}/receipt-proofs/{event}-content.json"
    write_json(directory / process_ref, process_record)
    write_json(directory / content_ref, content_record)
    draft = ReceiptDraft(
        **{
            **base.__dict__,
            "proof_bindings": (
                ProofBinding(ReceiptProofKind.PROCESS_BOUND, process_ref, digest(process_record), subject),
                ProofBinding(ReceiptProofKind.CONTENT_BOUND, content_ref, digest(content_record), subject),
            ),
        }
    )
    proposed = propose_receipt_append(ledger, draft)
    if proposed.accepted is None:
        code = proposed.rejected.error_code if proposed.rejected is not None else "VALP-E-STATE-CONFLICT"
        raise LangGraphAdapterError(f"LangGraph v3 receipt proposal rejected: {code}")
    try:
        committed = store.append(proposed.accepted)
    except ReceiptStoreError as error:
        if error.outcome != UNKNOWN_OR_COMMITTED_OUTCOME:
            raise LangGraphAdapterError(f"LangGraph v3 receipt append failed: {error.code}") from error
        try:
            reconciled = store.load()
        except ReceiptStoreError as reread_error:
            raise LangGraphAdapterError(
                f"LangGraph v3 durability reconciliation failed: {reread_error.code}"
            ) from reread_error
        matching = next((item for item in reconciled.receipts if item.draft.receipt_id == receipt_id), None)
        if matching is None or matching.canonical() != proposed.accepted.receipt.canonical():
            raise LangGraphAdapterError("LangGraph v3 durability outcome is unresolved or conflicting") from error
        return dict(matching.canonical())
    if committed.rejected is not None:
        raise LangGraphAdapterError(
            f"LangGraph v3 receipt commit rejected: {committed.rejected.error_code}"
        )
    receipt = committed.accepted.receipt if committed.accepted is not None else committed.no_op.prior_receipt
    return dict(receipt.canonical())


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
