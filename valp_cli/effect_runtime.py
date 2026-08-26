"""Execute accepted pure-Kernel effects through supported runtime Adapters."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .kernel_store import KernelEffectStatus, KernelStore, KernelStoreError
from .langgraph_adapter import LangGraphAdapterError, cancel_langgraph_run
from .protocol_receipts import digest
from .runtime_adapters import (
    RuntimeAdapterError,
    load_queue_lifecycle,
    load_runtime_v3_receipts,
    record_queue_cancellation_proof,
    record_queue_cancellation_request,
)


class EffectRuntimeError(RuntimeError):
    pass


def _task_directory(workspace: Path, task_id: str) -> Path:
    if not task_id or "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
        raise EffectRuntimeError("invalid task_id")
    directory = workspace.resolve() / ".herdr-loop" / "tasks" / task_id
    if not directory.is_dir():
        raise EffectRuntimeError(f"missing VALP task directory: {directory}")
    return directory


def _obligation_target(obligation: str) -> dict[str, Any]:
    prefix = "adapter_cancel:"
    try:
        target = json.loads(obligation[len(prefix):]) if obligation.startswith(prefix) else None
    except json.JSONDecodeError as error:
        raise EffectRuntimeError("Kernel cancellation obligation is malformed") from error
    if not isinstance(target, dict):
        raise EffectRuntimeError("Kernel cancellation obligation is malformed")
    return target


def _matching_langgraph_run(directory: Path, obligation: str) -> str | None:
    target = _obligation_target(obligation)
    matches = []
    for path in sorted((directory / "runtime" / "langgraph").glob("*/submission.json")):
        try:
            submission = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise EffectRuntimeError("LangGraph submission discovery failed") from error
        identity = {
            key: submission.get(key)
            for key in (
                "task_id", "work_item_id", "attempt_id", "dispatch_id",
                "dispatch_generation",
            )
        }
        if identity == target:
            matches.append(str(submission.get("run_id") or ""))
    if len(matches) > 1:
        raise EffectRuntimeError("Kernel cancellation obligation matches multiple LangGraph runs")
    return matches[0] if matches else None


def _matching_queue_submission(
    directory: Path, obligation: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    target = _obligation_target(obligation)
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    try:
        receipts = load_runtime_v3_receipts(directory, "queue")
    except RuntimeAdapterError:
        return None
    for path in sorted((directory / "runtime" / "queue" / "items").glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise EffectRuntimeError("Queue item discovery failed") from error
        identity = {
            key: item.get(key)
            for key in (
                "task_id", "work_item_id", "attempt_id", "dispatch_id",
                "dispatch_generation",
            )
        }
        if identity != target:
            continue
        submissions = [
            receipt for receipt in receipts
            if receipt.get("event") == "dispatch_submitted"
            and receipt.get("attempt_id") == item.get("attempt_id")
        ]
        if len(submissions) != 1:
            raise EffectRuntimeError("Queue cancellation target lacks one exact submission receipt")
        matches.append((submissions[0], item))
    if len(matches) > 1:
        raise EffectRuntimeError("Kernel cancellation obligation matches multiple Queue items")
    return matches[0] if matches else None


def _fulfill_queue_effect(
    store: KernelStore,
    directory: Path,
    obligation: str,
    submission: dict[str, Any],
    item: dict[str, Any],
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    lifecycle = load_queue_lifecycle(directory, str(item["queue_id"]))
    if not lifecycle or lifecycle[-1].get("event") != "cancelled":
        raise EffectRuntimeError("Queue cancellation has no acknowledged terminal proof")
    try:
        proof_ref, observation = record_queue_cancellation_proof(
            directory, str(item["task_id"]), submission, lifecycle[-1]
        )
        proof_path = directory / proof_ref
        record = store.record_effect(
            obligation,
            status=KernelEffectStatus.FULFILLED,
            proof_ref=proof_ref,
            proof_digest=digest(proof_path.read_bytes()),
        )
    except (KernelStoreError, RuntimeAdapterError, OSError) as error:
        raise EffectRuntimeError(str(error)) from error
    return record.canonical(), proof_ref, observation


def _execute_kernel_effect_locked(
    workspace: Path,
    task_id: str,
    obligation: str,
    *,
    approve: bool,
    directory: Path,
) -> dict[str, Any]:
    store = KernelStore(directory / "runtime" / "kernel")
    try:
        reconciliation = store.reconcile_effects()
    except KernelStoreError as error:
        raise EffectRuntimeError(str(error)) from error
    pending = {item for item in reconciliation.pending}
    fulfilled = {item.obligation: item for item in reconciliation.fulfilled}
    blocked = {item.obligation: item for item in reconciliation.blocked}
    if obligation in fulfilled:
        return {
            "status": "fulfilled",
            "variant": "no_op",
            "record": fulfilled[obligation].canonical(),
            "reconciliation": reconciliation.canonical(),
        }
    if obligation in blocked:
        raise EffectRuntimeError("Kernel effect is already blocked")
    if obligation not in pending:
        raise EffectRuntimeError("Kernel effect is not an accepted pending obligation")

    run_id = _matching_langgraph_run(directory, obligation)
    queue_match = None if run_id is not None else _matching_queue_submission(directory, obligation)
    if run_id is None and queue_match is None:
        raise EffectRuntimeError("no supported runtime Adapter matches the cancellation obligation")
    if queue_match is not None:
        submission, item = queue_match
        lifecycle = load_queue_lifecycle(directory, str(item["queue_id"]))
        state = lifecycle[-1]["state"] if lifecycle else "queued"
        if not approve:
            return {
                "status": "dry_run", "variant": "pending", "adapter_id": "queue",
                "queue_id": item["queue_id"], "queue_state": state,
                "obligation": obligation, "reconciliation": reconciliation.canonical(),
            }
        if state == "queued":
            try:
                cancelled = record_queue_cancellation_request(
                    directory, task_id, submission,
                    authority="kernel-effect-executor",
                    reason="accepted Kernel cancellation obligation",
                    expected_revision=0,
                )
                record, proof_ref, observation = _fulfill_queue_effect(
                    store, directory, obligation, submission, item
                )
                final = store.reconcile_effects()
            except (RuntimeAdapterError, KernelStoreError) as error:
                raise EffectRuntimeError(str(error)) from error
            return {
                "status": "fulfilled", "variant": "accepted", "adapter_id": "queue",
                "queue_id": item["queue_id"], "lifecycle": cancelled,
                "proof_ref": proof_ref, "observation": observation, "record": record,
                "reconciliation": final.canonical(),
            }
        if state == "claimed":
            try:
                request = record_queue_cancellation_request(
                    directory, task_id, submission,
                    authority="kernel-effect-executor",
                    reason="accepted Kernel cancellation obligation",
                    expected_revision=len(lifecycle),
                )
            except RuntimeAdapterError as error:
                raise EffectRuntimeError(str(error)) from error
            return {
                "status": "pending", "variant": "awaiting_worker_ack",
                "adapter_id": "queue", "queue_id": item["queue_id"],
                "lifecycle": request, "reconciliation": reconciliation.canonical(),
            }
        if state == "cancellation_requested":
            return {
                "status": "pending", "variant": "awaiting_worker_ack",
                "adapter_id": "queue", "queue_id": item["queue_id"],
                "lifecycle": lifecycle[-1], "reconciliation": reconciliation.canonical(),
            }
        if state == "cancelled":
            record, proof_ref, observation = _fulfill_queue_effect(
                store, directory, obligation, submission, item
            )
            final = store.reconcile_effects()
            return {
                "status": "fulfilled", "variant": "accepted", "adapter_id": "queue",
                "queue_id": item["queue_id"], "proof_ref": proof_ref,
                "observation": observation, "record": record,
                "reconciliation": final.canonical(),
            }
        raise EffectRuntimeError("Queue Attempt already reached a conflicting terminal state")
    assert run_id is not None
    if not approve:
        return {
            "status": "dry_run",
            "variant": "pending",
            "adapter_id": "langgraph",
            "run_id": run_id,
            "obligation": obligation,
            "reconciliation": reconciliation.canonical(),
        }
    try:
        result = cancel_langgraph_run(
            workspace, task_id, run_id, obligation=obligation
        )
        proof_path = directory / result["proof_ref"]
        record = store.record_effect(
            obligation,
            status=KernelEffectStatus.FULFILLED,
            proof_ref=result["proof_ref"],
            proof_digest=digest(proof_path.read_bytes()),
        )
        final = store.reconcile_effects()
    except (KernelStoreError, LangGraphAdapterError, OSError) as error:
        raise EffectRuntimeError(str(error)) from error
    return {
        "status": "fulfilled",
        "variant": "accepted",
        "adapter_id": "langgraph",
        "run_id": run_id,
        "record": record.canonical(),
        "adapter_result": result,
        "reconciliation": final.canonical(),
    }


def execute_kernel_effect(
    workspace: Path,
    task_id: str,
    obligation: str,
    *,
    approve: bool,
) -> dict[str, Any]:
    directory = _task_directory(workspace, task_id)
    from .workflow import task_state_lock

    with task_state_lock(directory):
        return _execute_kernel_effect_locked(
            workspace,
            task_id,
            obligation,
            approve=approve,
            directory=directory,
        )


__all__ = ["EffectRuntimeError", "execute_kernel_effect"]
