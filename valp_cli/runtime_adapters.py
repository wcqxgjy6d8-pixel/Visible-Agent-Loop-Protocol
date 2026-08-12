"""Canonical ABI 1.0 and receipt-v3 adoption for reference runtimes."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import tempfile
import time
from typing import Any, Iterable

from .adapter_abi import (
    ABI_VERSION,
    AdapterCapability,
    AdapterManifest,
    AdapterObservation,
    AdapterOperation,
    AdapterRequest,
    AdapterStatus,
    CapabilityStatus,
    ProofKind,
    ProvenanceSegment,
    validate_observation,
)
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


class RuntimeAdapterError(RuntimeError):
    pass


_ADAPTER_CLASSES = {
    "herdr": "visible_agent_runtime",
    "queue": "durable_file_queue",
    "manual": "manual_operator",
}

_UNSUPPORTED = {
    "herdr": {
        AdapterOperation.CANCEL: "HERDR cancellation is not implemented by this Adapter",
    },
    "queue": {
        AdapterOperation.RESUME: "Queue resume requires a real worker observation",
    },
    "manual": {
        AdapterOperation.SUBMIT: "Manual Mode has no automatic submission operation",
        AdapterOperation.CANCEL: "Manual Mode records revocation instead of runtime cancellation",
        AdapterOperation.RESUME: "Manual Mode has no automatic resume operation",
    },
}


def runtime_adapter_manifest(adapter_id: str) -> AdapterManifest:
    if adapter_id not in _ADAPTER_CLASSES:
        raise RuntimeAdapterError(f"Unsupported runtime Adapter: {adapter_id}")
    unsupported = _UNSUPPORTED[adapter_id]
    return AdapterManifest(
        adapter_id,
        _ADAPTER_CLASSES[adapter_id],
        ABI_VERSION,
        tuple(
            AdapterCapability(operation, CapabilityStatus.UNSUPPORTED, unsupported[operation])
            if operation in unsupported
            else AdapterCapability(operation, CapabilityStatus.SUPPORTED)
            for operation in AdapterOperation
        ),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _runtime_root(directory: Path, adapter_id: str) -> Path:
    return directory / "runtime" / adapter_id


def _ledger_path(directory: Path, adapter_id: str) -> Path:
    return _runtime_root(directory, adapter_id) / "receipts.v3.jsonl"


def _safe_ref(value: object) -> bool:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")) or "\\" in value:
        return False
    path = PurePosixPath(value)
    return all(part not in {"", ".", ".."} for part in path.parts)


def _expected_adoption_marker(adapter_id: str, task_id: str) -> dict[str, Any]:
    return {
        "schema_version": "valp-runtime-receipt-adoption.v1",
        "task_id": task_id,
        "adapter_id": adapter_id,
        "abi_version": ABI_VERSION,
        "ledger_ref": f"runtime/{adapter_id}/receipts.v3.jsonl",
        "compatibility_ledger_ref": "dispatch-receipts.jsonl",
        "write_schema": "valp-dispatch-receipt.v3",
    }


def _adopt(directory: Path, adapter_id: str, task_id: str) -> None:
    marker_path = _runtime_root(directory, adapter_id) / "adoption.json"
    marker = _expected_adoption_marker(adapter_id, task_id)
    compatibility = directory / "dispatch-receipts.jsonl"
    if compatibility.is_file() and compatibility.stat().st_size:
        raise RuntimeAdapterError(
            f"{adapter_id} v3 adoption rejects mixed non-empty legacy and authoritative ledgers"
        )
    if marker_path.is_file():
        try:
            existing = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeAdapterError(f"{adapter_id} adoption marker is malformed") from error
        if existing != marker:
            raise RuntimeAdapterError(f"{adapter_id} adoption marker conflicts")
    else:
        write_json(marker_path, marker)
    write_json(
        _runtime_root(directory, adapter_id) / "abi-adoption.json",
        {
            "schema_version": "valp-adapter-abi-adoption.v1",
            "adapter_id": adapter_id,
            "abi_version": ABI_VERSION,
            "manifest": runtime_adapter_manifest(adapter_id).canonical(),
        },
    )


def _reference_identity(directory: Path, task_id: str) -> tuple[str, int, str]:
    try:
        workspace = directory.resolve().parents[2]
        installation = json.loads(
            (workspace / ".valp" / "installation.json").read_text(encoding="utf-8")
        )
        state = json.loads((workspace / ".valp" / "state.json").read_text(encoding="utf-8"))
        policy = json.loads((directory / "automation-policy.json").read_text(encoding="utf-8"))
    except (IndexError, OSError, json.JSONDecodeError) as error:
        raise RuntimeAdapterError("runtime v3 identity or approval policy is unavailable") from error
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
        or policy.get("schema_version") != "valp-automation-policy.v1"
        or policy.get("approval_required") is not False
        or not task_id
    ):
        raise RuntimeAdapterError("runtime v3 requires consistent installation, Leader, and policy identity")
    return installation_id, leader_epoch, digest(policy)


def load_runtime_v3_receipts(directory: Path, adapter_id: str) -> list[dict[str, Any]]:
    marker_path = _runtime_root(directory, adapter_id) / "adoption.json"
    if not marker_path.is_file():
        raise RuntimeAdapterError(f"{adapter_id} v3 adoption marker is missing")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeAdapterError(f"{adapter_id} adoption marker is malformed") from error
    expected = _expected_adoption_marker(adapter_id, directory.name)
    if marker != expected:
        raise RuntimeAdapterError(f"{adapter_id} adoption marker conflicts")
    compatibility = directory / "dispatch-receipts.jsonl"
    if compatibility.is_file() and compatibility.stat().st_size:
        raise RuntimeAdapterError(f"{adapter_id} adopted runtime has mixed legacy ledger state")
    installation_id, leader_epoch, _ = _reference_identity(directory, directory.name)
    try:
        ledger = ReceiptStore(
            _ledger_path(directory, adapter_id), installation_id, leader_epoch, directory.name
        ).load()
    except ReceiptStoreError as error:
        raise RuntimeAdapterError(f"{adapter_id} v3 ledger is invalid: {error.code}") from error
    return [dict(item.canonical()) for item in ledger.receipts]


def _attempt_id(adapter_id: str, value: dict[str, Any]) -> str:
    return f"{adapter_id}:{digest(value)}"


def _proof_record(
    *,
    adapter_id: str,
    event: str,
    receipt_id: str,
    attempt_id: str,
    subject_digest: str,
    proof_kind: ReceiptProofKind,
    proof: dict[str, Any],
) -> dict[str, Any]:
    if proof_kind == ReceiptProofKind.MANUAL_ATTESTED:
        return {
            **proof,
            "receipt_id": receipt_id,
            "subject_digest": subject_digest,
            "proof_kind": proof_kind.value,
            "acknowledged": True,
            "source_proof_digest": digest(proof),
        }
    return {
        "schema_version": f"valp-{adapter_id}-{proof_kind.value}-proof.v1",
        "receipt_id": receipt_id,
        "event": event,
        "attempt_id": attempt_id,
        "subject_digest": subject_digest,
        "proof_kind": proof_kind.value,
        "acknowledged": True,
        "source_proof_digest": digest(proof),
        "proof": proof,
    }


def _observation(
    *,
    adapter_id: str,
    receipt: dict[str, Any],
    status: AdapterStatus,
    runtime_identity: str,
    operation: AdapterOperation,
    evidence_refs: Iterable[str] = (),
) -> AdapterObservation:
    request = AdapterRequest(
        request_id=digest({
            "adapter_id": adapter_id,
            "operation": operation.value,
            "task_id": receipt["task_id"],
            "attempt_id": receipt["attempt_id"],
            "payload_digest": receipt["payload_digest"],
        }),
        operation=operation,
        installation_id=receipt["installation_id"],
        leader_epoch=receipt["leader_epoch"],
        task_id=receipt["task_id"],
        work_item_id=receipt["work_item_id"],
        attempt_id=receipt["attempt_id"],
        dispatch_id=receipt["dispatch_id"],
        dispatch_generation=receipt["dispatch_generation"],
        payload_digest=receipt["payload_digest"],
        expected_evidence_refs=tuple(receipt["expected_refs"]),
    )
    segments = []
    prior_identity = request.request_id
    prior_digest = request.payload_digest
    for sequence, binding in enumerate(receipt["proof_bindings"]):
        kind = ProofKind(binding["proof_kind"])
        output_identity = receipt["attempt_id"] if sequence == 0 else receipt["receipt_id"]
        segment = ProvenanceSegment(
            segment_id=f"{receipt['receipt_id']}:{kind.value}",
            sequence=sequence,
            adapter_id=adapter_id,
            abi_version=ABI_VERSION,
            input_identity=prior_identity,
            input_digest=prior_digest,
            output_identity=output_identity,
            output_digest=binding["proof_digest"],
            proof_kind=kind,
            evidence_refs=(binding["proof_ref"],),
            acknowledged=True,
        )
        segments.append(segment)
        prior_identity = output_identity
        prior_digest = binding["proof_digest"]
    observation = AdapterObservation(
        observation_id=digest({
            "request_id": request.request_id,
            "status": status.value,
            "receipt_id": receipt["receipt_id"],
        }),
        request=request,
        sequence=receipt["ledger_revision"],
        status=status,
        runtime_identity=runtime_identity,
        provenance=tuple(segments),
        evidence_refs=tuple(evidence_refs),
    )
    validate_observation(runtime_adapter_manifest(adapter_id), observation)
    return observation


def _append(
    directory: Path,
    adapter_id: str,
    task_id: str,
    *,
    agent: str,
    role: str,
    work_item_id: str,
    attempt_id: str,
    dispatch_id: str,
    dispatch_generation: int,
    dispatch_ref: str,
    expected_refs: list[str],
    payload_digest: str,
    event: str,
    mode: ReceiptMode,
    proof_items: list[tuple[ReceiptProofKind, dict[str, Any]]],
    runtime_identity: str,
    status: AdapterStatus,
    operation: AdapterOperation,
    suspension_epoch: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _adopt(directory, adapter_id, task_id)
    installation_id, leader_epoch, policy_digest = _reference_identity(directory, task_id)
    store = ReceiptStore(_ledger_path(directory, adapter_id), installation_id, leader_epoch, task_id)
    try:
        ledger = store.load()
    except ReceiptStoreError as error:
        raise RuntimeAdapterError(f"{adapter_id} v3 ledger load failed: {error.code}") from error
    logical = [
        item for item in ledger.receipts
        if item.draft.work_item_id == work_item_id
        and item.draft.dispatch_id == dispatch_id
        and item.draft.dispatch_generation == dispatch_generation
        and item.draft.event == event
    ]
    if logical:
        exact = [item for item in logical if item.draft.attempt_id == attempt_id]
        if not exact and adapter_id == "manual":
            logical = []
        elif len(exact) != 1:
            raise RuntimeAdapterError(f"{adapter_id} exact identity conflicts with committed receipt")
        else:
            logical = exact
    if logical:
        receipt = dict(logical[0].canonical())
        observation_path = (
            _runtime_root(directory, adapter_id) / "attempts" / digest(attempt_id)[7:]
            / f"abi-{operation.value}-{event}.json"
        )
        try:
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeAdapterError(f"{adapter_id} committed receipt lacks ABI observation") from error
        expected_proof_digests = {
            item[0].value: digest(item[1]) for item in proof_items
        }
        actual_proof_digests = {}
        for binding in receipt["proof_bindings"]:
            actual = json.loads((directory / binding["proof_ref"]).read_text(encoding="utf-8"))
            actual_proof_digests[binding["proof_kind"]] = actual.get("source_proof_digest")
        if actual_proof_digests != expected_proof_digests:
            raise RuntimeAdapterError(f"{adapter_id} exact retry proof conflicts")
        return receipt, observation

    receipt_id = digest({
        "adapter_id": adapter_id,
        "task_id": task_id,
        "work_item_id": work_item_id,
        "attempt_id": attempt_id,
        "dispatch_id": dispatch_id,
        "dispatch_generation": dispatch_generation,
        "event": event,
    })
    base = ReceiptDraft(
        receipt_id=receipt_id,
        installation_id=installation_id,
        leader_epoch=leader_epoch,
        task_id=task_id,
        agent=agent,
        role=role,
        work_item_id=work_item_id,
        attempt_id=attempt_id,
        dispatch_id=dispatch_id,
        dispatch_generation=dispatch_generation,
        mode=mode,
        event_sequence=ledger.revision + 1,
        expected_revision=ledger.revision,
        prior_receipt_digest=ledger.tail_digest,
        event=event,
        ts=_now_iso(),
        dispatch_ref=dispatch_ref,
        payload_digest=payload_digest,
        expected_refs=tuple(expected_refs),
        proof_bindings=(),
        approval_binding=ApprovalBinding("not_required", policy_digest),
        suspension_epoch=suspension_epoch,
    )
    subject = receipt_subject_digest(base)
    proof_bindings = []
    attempt_root = _runtime_root(directory, adapter_id) / "attempts" / digest(attempt_id)[7:]
    for proof_kind, proof in proof_items:
        record = _proof_record(
            adapter_id=adapter_id,
            event=event,
            receipt_id=receipt_id,
            attempt_id=attempt_id,
            subject_digest=subject,
            proof_kind=proof_kind,
            proof=proof,
        )
        proof_ref = str(
            (attempt_root / "receipt-proofs" / f"{event}-{proof_kind.value}.json")
            .relative_to(directory)
        )
        write_json(directory / proof_ref, record)
        proof_bindings.append(
            ProofBinding(proof_kind, proof_ref, digest(record), subject)
        )
    draft = ReceiptDraft(**{**base.__dict__, "proof_bindings": tuple(proof_bindings)})
    proposal = propose_receipt_append(ledger, draft)
    if proposal.accepted is None:
        code = proposal.rejected.error_code if proposal.rejected else "VALP-E-STATE-CONFLICT"
        raise RuntimeAdapterError(f"{adapter_id} v3 receipt rejected: {code}")
    try:
        result = store.append(proposal.accepted)
    except ReceiptStoreError as error:
        if error.outcome != UNKNOWN_OR_COMMITTED_OUTCOME:
            raise RuntimeAdapterError(f"{adapter_id} v3 receipt append failed: {error.code}") from error
        try:
            reconciled = store.load()
        except ReceiptStoreError as reread_error:
            raise RuntimeAdapterError(
                f"{adapter_id} durability reconciliation failed: {reread_error.code}"
            ) from reread_error
        match = next(
            (item for item in reconciled.receipts if item.draft.receipt_id == receipt_id), None
        )
        if match is None or match.canonical() != proposal.accepted.receipt.canonical():
            raise RuntimeAdapterError(f"{adapter_id} durability outcome remains unresolved") from error
        receipt = dict(match.canonical())
    else:
        if result.rejected is not None:
            raise RuntimeAdapterError(
                f"{adapter_id} v3 receipt commit rejected: {result.rejected.error_code}"
            )
        committed = result.accepted.receipt if result.accepted else result.no_op.prior_receipt
        receipt = dict(committed.canonical())
    observation = _observation(
        adapter_id=adapter_id,
        receipt=receipt,
        status=status,
        runtime_identity=runtime_identity,
        operation=operation,
        evidence_refs=expected_refs if status == AdapterStatus.COMPLETED else (),
    ).canonical()
    write_json(attempt_root / f"abi-{operation.value}-{event}.json", observation)
    return receipt, dict(observation)


def record_herdr_submission(
    directory: Path,
    task_id: str,
    *,
    agent: str,
    role: str,
    work_item_id: str,
    dispatch_id: str,
    dispatch_generation: int,
    dispatch_ref: str,
    expected_refs: list[str],
    proof: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = proof.get("submission_proof") or {}
    identity = state.get("identity") or {}
    if (
        proof.get("runtime") != "HERDR"
        or proof.get("proof_class") != "agent_invocation"
        or proof.get("transport_mode") != "agent_prompt"
        or state.get("kind") != "identity_bound_state_change"
        or type(state.get("baseline_state_change_seq")) is not int
        or type(state.get("state_change_seq")) is not int
        or state["state_change_seq"] <= state["baseline_state_change_seq"]
        or not all(str(identity.get(key) or "") for key in ("terminal_id", "agent", "pane_id"))
        or str(proof.get("pane_id") or "") != str(identity.get("pane_id") or "")
        or str(proof.get("agent_ref") or "") != agent
        or not str(proof.get("payload_digest") or "").startswith("sha256:")
    ):
        raise RuntimeAdapterError("HERDR atomic submission proof is incomplete")
    attempt = _attempt_id("herdr", {
        "identity": identity,
        "baseline_state_change_seq": state["baseline_state_change_seq"],
        "state_change_seq": state["state_change_seq"],
        "payload_digest": proof["payload_digest"],
    })
    process = {
        "runtime_identity": identity,
        "baseline_state_change_seq": state["baseline_state_change_seq"],
        "state_change_seq": state["state_change_seq"],
        "runtime_response_digest": digest(proof.get("runtime_response") or {}),
    }
    content = {
        "payload_digest": proof["payload_digest"],
        "acknowledged_state_change_seq": state["state_change_seq"],
        "agent": agent,
        "dispatch_ref": dispatch_ref,
    }
    return _append(
        directory, "herdr", task_id,
        agent=agent, role=role, work_item_id=work_item_id, attempt_id=attempt,
        dispatch_id=dispatch_id, dispatch_generation=dispatch_generation,
        dispatch_ref=dispatch_ref, expected_refs=expected_refs,
        payload_digest=proof["payload_digest"], event="dispatch_submitted",
        mode=ReceiptMode.FULL,
        proof_items=[
            (ReceiptProofKind.PROCESS_BOUND, process),
            (ReceiptProofKind.CONTENT_BOUND, content),
        ],
        runtime_identity=f"{identity['terminal_id']}:{identity['pane_id']}:{state['state_change_seq']}",
        status=AdapterStatus.ACCEPTED,
        operation=AdapterOperation.SUBMIT,
    )


def record_herdr_transport(
    directory: Path,
    task_id: str,
    *,
    agent: str,
    role: str,
    work_item_id: str,
    dispatch_id: str,
    dispatch_generation: int,
    dispatch_ref: str,
    expected_refs: list[str],
    proof: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        proof.get("runtime") != "HERDR"
        or proof.get("proof_class") != "transport_only"
        or proof.get("transport_mode") != "pane_send_text_enter"
        or proof.get("manual_degraded") is not True
        or not str(proof.get("pane_id") or "")
        or not str(proof.get("payload_digest") or "").startswith("sha256:")
    ):
        raise RuntimeAdapterError("HERDR transport-only proof is incomplete")
    attempt = _attempt_id("herdr-transport", {
        "task_id": task_id,
        "work_item_id": work_item_id,
        "dispatch_id": dispatch_id,
        "dispatch_generation": dispatch_generation,
        "pane_id": proof["pane_id"],
        "payload_digest": proof["payload_digest"],
    })
    return _append(
        directory, "herdr", task_id,
        agent=agent, role=role, work_item_id=work_item_id, attempt_id=attempt,
        dispatch_id=dispatch_id, dispatch_generation=dispatch_generation,
        dispatch_ref=dispatch_ref, expected_refs=expected_refs,
        payload_digest=proof["payload_digest"], event="dispatch_inserted",
        mode=ReceiptMode.MANUAL,
        proof_items=[(ReceiptProofKind.TRANSPORT_ONLY, proof)],
        runtime_identity=f"pane:{proof['pane_id']}", status=AdapterStatus.WAITING,
        operation=AdapterOperation.OBSERVE,
    )


def record_herdr_completion(
    directory: Path,
    task_id: str,
    submission: dict[str, Any],
    existing_refs: list[str],
    terminal_proof: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        submission.get("schema_version") != "valp-dispatch-receipt.v3"
        or submission.get("event") != "dispatch_submitted"
        or submission.get("task_id") != task_id
        or set(existing_refs) != set(submission.get("expected_refs") or [])
    ):
        raise RuntimeAdapterError("HERDR completion requires its exact submitted receipt and evidence set")
    attempt_hash = digest(str(submission["attempt_id"]))[7:]
    submit_observation_path = (
        _runtime_root(directory, "herdr") / "attempts" / attempt_hash
        / "abi-submit-dispatch_submitted.json"
    )
    try:
        submitted_observation = json.loads(submit_observation_path.read_text(encoding="utf-8"))
        submitted_runtime_identity = str(submitted_observation["runtime_identity"])
        process_binding = next(
            item for item in submission["proof_bindings"]
            if item["proof_kind"] == "process_bound"
        )
        submission_process_record = json.loads(
            (directory / process_binding["proof_ref"]).read_text(encoding="utf-8")
        )
        submission_process = submission_process_record["proof"]
        submitted_identity = submission_process["runtime_identity"]
    except (OSError, KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeAdapterError("HERDR completion lacks its submitted ABI observation") from error
    status = terminal_proof.get("status") if isinstance(terminal_proof, dict) else None
    if (
        not isinstance(terminal_proof, dict)
        or terminal_proof.get("schema_version") != "valp-herdr-terminal-observation.v1"
        or terminal_proof.get("runtime") != "HERDR"
        or terminal_proof.get("proof_class") != "agent_terminal_observation"
        or terminal_proof.get("task_id") != task_id
        or terminal_proof.get("agent") != submission.get("agent")
        or terminal_proof.get("terminal_id") != submitted_identity.get("terminal_id")
        or terminal_proof.get("pane_id") != submitted_identity.get("pane_id")
        or terminal_proof.get("submission_state_change_seq") != submission_process.get("state_change_seq")
        or type(terminal_proof.get("state_change_seq")) is not int
        or terminal_proof["state_change_seq"] <= int(submission_process["state_change_seq"])
        or status not in {"completed", "blocked"}
        or terminal_proof.get("acknowledged") is not True
        or (status == "blocked") != (
            isinstance(terminal_proof.get("failure_code"), str)
            and bool(terminal_proof["failure_code"].strip())
        )
        or (status == "completed" and "failure_code" in terminal_proof)
    ):
        raise RuntimeAdapterError("HERDR terminal observation is not bound to the submitted Attempt")
    evidence = []
    if status == "completed":
        for ref in submission["expected_refs"]:
            path = directory / ref
            if not path.is_file() or not path.stat().st_size:
                raise RuntimeAdapterError(f"HERDR completion evidence is missing: {ref}")
            evidence.append({"ref": ref, "content_digest": digest(path.read_bytes())})
    receipts = load_runtime_v3_receipts(directory, "herdr")
    epochs = [
        int(item["suspension_epoch"])
        for item in receipts
        if item.get("event") in {"dispatch_completed", "dispatch_blocked"}
        and type(item.get("suspension_epoch")) is int
    ]
    suspension_epoch = max(epochs, default=0) + 1
    runtime_identity = (
        f"{terminal_proof['terminal_id']}:{terminal_proof['pane_id']}:"
        f"{terminal_proof['state_change_seq']}"
    )
    process = {
        "runtime_identity": runtime_identity,
        "submission_runtime_identity": submitted_runtime_identity,
        "submission_receipt_id": submission["receipt_id"],
        "terminal_observer": "valp.packaged-herdr.agent-state",
        "terminal_observation_digest": digest(terminal_proof),
        "terminal_observation": terminal_proof,
        "suspension_epoch": suspension_epoch,
    }
    content = {
        "submission_receipt_id": submission["receipt_id"],
        "terminal_status": status,
        "expected_evidence": evidence,
        "acknowledged": True,
    }
    if status == "blocked":
        content["failure_code"] = terminal_proof["failure_code"]
    event = "dispatch_completed" if status == "completed" else "dispatch_blocked"
    return _append(
        directory, "herdr", task_id,
        agent=str(submission["agent"]), role=str(submission["role"]),
        work_item_id=str(submission["work_item_id"]),
        attempt_id=str(submission["attempt_id"]), dispatch_id=str(submission["dispatch_id"]),
        dispatch_generation=int(submission["dispatch_generation"]),
        dispatch_ref=str(submission["dispatch_ref"]),
        expected_refs=list(submission["expected_refs"]),
        payload_digest=str(submission["payload_digest"]), event=event,
        mode=ReceiptMode.FULL,
        proof_items=[
            (ReceiptProofKind.PROCESS_BOUND, process),
            (ReceiptProofKind.CONTENT_BOUND, content),
        ],
        runtime_identity=runtime_identity,
        status=AdapterStatus.COMPLETED if status == "completed" else AdapterStatus.BLOCKED,
        operation=AdapterOperation.OBSERVE, suspension_epoch=suspension_epoch,
    )


def record_queue_acceptance(
    directory: Path,
    task_id: str,
    *,
    agent: str,
    role: str,
    work_item_id: str,
    dispatch_id: str,
    dispatch_generation: int,
    dispatch_ref: str,
    expected_refs: list[str],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    dispatch_path = directory / dispatch_ref
    try:
        payload = dispatch_path.read_bytes()
    except OSError as error:
        raise RuntimeAdapterError(f"Queue dispatch payload is unavailable: {dispatch_ref}") from error
    payload_digest = digest(payload)
    queue_id = digest({
        "task_id": task_id,
        "work_item_id": work_item_id,
        "dispatch_id": dispatch_id,
        "dispatch_generation": dispatch_generation,
    })
    transaction_id = digest({"queue_id": queue_id, "payload_digest": payload_digest})
    attempt = _attempt_id("queue", {
        "queue_id": queue_id,
        "transaction_id": transaction_id,
        "payload_digest": payload_digest,
    })
    queue_ref = f"runtime/queue/items/{queue_id[7:]}.json"
    queue_path = directory / queue_ref
    queue = {
        "schema_version": "valp-queue-dispatch.v2",
        "task_id": task_id,
        "agent": agent,
        "role": role,
        "work_item_id": work_item_id,
        "attempt_id": attempt,
        "dispatch_id": dispatch_id,
        "dispatch_generation": dispatch_generation,
        "queue_id": queue_id,
        "enqueue_transaction_id": transaction_id,
        "status": "queued",
        "dispatch_ref": dispatch_ref,
        "payload_digest": payload_digest,
        "expected_refs": expected_refs,
        "created_at": _now_iso(),
        "claim_limit": "queue acceptance only; worker delivery and completion are unproven",
    }
    if queue_path.is_file():
        try:
            prior = json.loads(queue_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeAdapterError("Queue item is malformed") from error
        if {key: value for key, value in prior.items() if key != "created_at"} != {
            key: value for key, value in queue.items() if key != "created_at"
        }:
            raise RuntimeAdapterError("Queue exact retry conflicts with existing item")
        queue = prior
    else:
        write_json(queue_path, queue)
    receipt, observation = _append(
        directory, "queue", task_id,
        agent=agent, role=role, work_item_id=work_item_id, attempt_id=attempt,
        dispatch_id=dispatch_id, dispatch_generation=dispatch_generation,
        dispatch_ref=dispatch_ref, expected_refs=expected_refs,
        payload_digest=payload_digest, event="dispatch_submitted", mode=ReceiptMode.FULL,
        proof_items=[
            (ReceiptProofKind.PROCESS_BOUND, {
                "queue_id": queue_id,
                "enqueue_transaction_id": transaction_id,
                "queue_ref": queue_ref,
                "queue_record_digest": digest(queue),
                "claim_limit": queue["claim_limit"],
            }),
            (ReceiptProofKind.CONTENT_BOUND, {
                "queue_id": queue_id,
                "dispatch_ref": dispatch_ref,
                "payload_digest": payload_digest,
                "acknowledged": True,
            }),
        ],
        runtime_identity=f"queue:{queue_id}", status=AdapterStatus.ACCEPTED,
        operation=AdapterOperation.SUBMIT,
    )
    return queue, receipt, observation


def _queue_item_for_submission(directory: Path, submission: dict[str, Any]) -> dict[str, Any]:
    items_root = _runtime_root(directory, "queue") / "items"
    matches = []
    for path in sorted(items_root.glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeAdapterError("Queue item is malformed") from error
        if item.get("attempt_id") == submission.get("attempt_id"):
            matches.append(item)
    if len(matches) != 1:
        raise RuntimeAdapterError("Queue terminal observation requires one exact accepted queue item")
    item = matches[0]
    for key in (
        "task_id", "agent", "role", "work_item_id", "attempt_id", "dispatch_id",
        "dispatch_generation", "dispatch_ref", "payload_digest", "expected_refs",
    ):
        if item.get(key) != submission.get(key):
            raise RuntimeAdapterError("Queue item identity conflicts with its submitted receipt")
    return item


_QUEUE_IDENTITY_KEYS = (
    "task_id", "work_item_id", "attempt_id", "dispatch_id", "dispatch_generation",
    "queue_id", "enqueue_transaction_id",
)


def _queue_lifecycle_path(directory: Path) -> Path:
    return _runtime_root(directory, "queue") / "lifecycle.v1.jsonl"


@contextmanager
def _queue_lifecycle_lock(directory: Path) -> Iterable[None]:
    lock_path = _queue_lifecycle_path(directory).with_name("lifecycle.v1.jsonl.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = lock_path.open("a+b")
    except OSError as error:
        raise RuntimeAdapterError("Queue lifecycle lock is unavailable") from error
    with handle:
        if handle.tell() == 0:
            try:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            except OSError as error:
                raise RuntimeAdapterError("Queue lifecycle lock initialization failed") from error
        deadline = time.monotonic() + 30.0
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":  # pragma: no cover - Windows only.
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in {
                    errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", errno.EAGAIN)
                }:
                    raise RuntimeAdapterError("Queue lifecycle lock failed") from error
                if time.monotonic() >= deadline:
                    raise RuntimeAdapterError("Queue lifecycle lock timed out") from error
                time.sleep(0.01)
        try:
            yield
        finally:
            try:
                handle.seek(0)
                if os.name == "nt":  # pragma: no cover - Windows only.
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError as error:
                raise RuntimeAdapterError("Queue lifecycle unlock failed") from error


def _canonical_line(value: dict[str, Any]) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8")


def _load_queue_lifecycle_unlocked(directory: Path) -> list[dict[str, Any]]:
    path = _queue_lifecycle_path(directory)
    if not path.exists():
        return []
    try:
        payload = path.read_bytes()
    except OSError as error:
        raise RuntimeAdapterError("Queue lifecycle ledger is unavailable") from error
    if payload and (not payload.endswith(b"\n") or b"\r" in payload):
        raise RuntimeAdapterError("Queue lifecycle ledger is noncanonical")
    entries: list[dict[str, Any]] = []
    prior_digest: str | None = None
    queue_revisions: dict[str, int] = {}
    queue_states: dict[str, str] = {}
    for ledger_revision, line in enumerate(payload.splitlines(keepends=True), 1):
        try:
            entry = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeAdapterError("Queue lifecycle ledger is malformed") from error
        if not isinstance(entry, dict) or line != _canonical_line(entry):
            raise RuntimeAdapterError("Queue lifecycle ledger is noncanonical")
        queue_id = entry.get("queue_id")
        expected_queue_revision = queue_revisions.get(str(queue_id), 0) + 1
        if (
            entry.get("schema_version") != "valp-queue-lifecycle-entry.v1"
            or entry.get("ledger_revision") != ledger_revision
            or entry.get("queue_revision") != expected_queue_revision
            or entry.get("previous_entry_digest") != prior_digest
            or not isinstance(entry.get("entry_digest"), str)
            or entry.get("entry_digest") != digest({
                key: value for key, value in entry.items() if key != "entry_digest"
            })
        ):
            raise RuntimeAdapterError("Queue lifecycle digest chain is invalid")
        prior_state = queue_states.get(str(queue_id), "queued")
        event = entry.get("event")
        state = entry.get("state")
        valid_transition = (
            (prior_state == "queued" and event == "claimed" and state == "claimed")
            or (prior_state == "queued" and event == "cancelled" and state == "cancelled")
            or (
                prior_state == "claimed"
                and event == "cancellation_requested"
                and state == "cancellation_requested"
            )
            or (
                prior_state == "cancellation_requested"
                and event == "cancelled"
                and state == "cancelled"
            )
            or (
                prior_state == "claimed"
                and event in {"completed", "blocked"}
                and state == event
            )
        )
        if not valid_transition:
            raise RuntimeAdapterError("Queue lifecycle transition is invalid")
        queue_revisions[str(queue_id)] = expected_queue_revision
        queue_states[str(queue_id)] = str(state)
        prior_digest = entry["entry_digest"]
        entries.append(entry)
    return entries


def load_queue_lifecycle(directory: Path, queue_id: str | None = None) -> list[dict[str, Any]]:
    with _queue_lifecycle_lock(directory):
        entries = _load_queue_lifecycle_unlocked(directory)
    if queue_id is None:
        return entries
    return [entry for entry in entries if entry.get("queue_id") == queue_id]


def _write_queue_lifecycle_unlocked(directory: Path, entries: list[dict[str, Any]]) -> None:
    path = _queue_lifecycle_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".lifecycle.v1.jsonl.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(b"".join(_canonical_line(entry) for entry in entries))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = ""
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except OSError as error:
        raise RuntimeAdapterError("Queue lifecycle durable append failed") from error
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _queue_lifecycle_entry(
    directory: Path,
    submission: dict[str, Any],
    *,
    event: str,
    state: str,
    expected_revision: int,
    details: dict[str, Any],
) -> dict[str, Any]:
    queue = _queue_item_for_submission(directory, submission)
    identity = {key: queue[key] for key in _QUEUE_IDENTITY_KEYS}
    idempotency_key = digest({
        "identity": identity,
        "event": event,
        "expected_revision": expected_revision,
        "details": details,
    })
    with _queue_lifecycle_lock(directory):
        entries = _load_queue_lifecycle_unlocked(directory)
        queue_entries = [item for item in entries if item.get("queue_id") == queue["queue_id"]]
        exact = [item for item in queue_entries if item.get("idempotency_key") == idempotency_key]
        if exact:
            if len(exact) != 1:
                raise RuntimeAdapterError("Queue lifecycle idempotency record is ambiguous")
            return exact[0]
        logical = [
            item for item in queue_entries
            if item.get("event") == event
            and item.get("expected_revision") == expected_revision
        ]
        if logical:
            raise RuntimeAdapterError("Queue lifecycle exact retry conflicts")
        current_revision = len(queue_entries)
        current_state = queue_entries[-1]["state"] if queue_entries else "queued"
        valid = (
            (current_state == "queued" and event == "claimed" and state == "claimed")
            or (current_state == "queued" and event == "cancelled" and state == "cancelled")
            or (
                current_state == "claimed"
                and event == "cancellation_requested"
                and state == "cancellation_requested"
            )
            or (
                current_state == "cancellation_requested"
                and event == "cancelled"
                and state == "cancelled"
            )
            or (
                current_state == "claimed"
                and event in {"completed", "blocked"}
                and state == event
            )
        )
        if expected_revision != current_revision or not valid:
            raise RuntimeAdapterError("Queue lifecycle CAS or transition conflict")
        entry = {
            "schema_version": "valp-queue-lifecycle-entry.v1",
            **identity,
            "ledger_revision": len(entries) + 1,
            "queue_revision": current_revision + 1,
            "expected_revision": expected_revision,
            "previous_entry_digest": entries[-1]["entry_digest"] if entries else None,
            "event": event,
            "state": state,
            "idempotency_key": idempotency_key,
            **details,
        }
        entry["event_id"] = digest({
            "queue_id": queue["queue_id"],
            "queue_revision": entry["queue_revision"],
            "idempotency_key": idempotency_key,
        })
        entry["entry_digest"] = digest(entry)
        _write_queue_lifecycle_unlocked(directory, [*entries, entry])
        return entry


def record_queue_claim(
    directory: Path,
    task_id: str,
    submission: dict[str, Any],
    *,
    worker_id: str,
    run_id: str,
    claim_token: str,
    expected_revision: int,
) -> dict[str, Any]:
    if submission.get("task_id") != task_id or not all(
        isinstance(value, str) and value.strip()
        for value in (worker_id, run_id, claim_token)
    ):
        raise RuntimeAdapterError("Queue claim identity is invalid")
    return _queue_lifecycle_entry(
        directory, submission, event="claimed", state="claimed",
        expected_revision=expected_revision,
        details={"worker_id": worker_id, "run_id": run_id, "claim_token": claim_token},
    )


def record_queue_cancellation_request(
    directory: Path,
    task_id: str,
    submission: dict[str, Any],
    *,
    authority: str,
    reason: str,
    expected_revision: int,
) -> dict[str, Any]:
    if submission.get("task_id") != task_id or not all(
        isinstance(value, str) and value.strip() for value in (authority, reason)
    ):
        raise RuntimeAdapterError("Queue cancellation authority or reason is invalid")
    queue = _queue_item_for_submission(directory, submission)
    current = load_queue_lifecycle(directory, queue["queue_id"])
    state = current[-1]["state"] if current else "queued"
    event = (
        "cancelled"
        if state == "queued" or (
            state == "cancelled"
            and expected_revision == 0
            and current[-1].get("authority") == authority
            and current[-1].get("reason") == reason
        )
        else "cancellation_requested"
    )
    return _queue_lifecycle_entry(
        directory, submission, event=event, state=event,
        expected_revision=expected_revision,
        details={"authority": authority, "reason": reason},
    )


def _queue_cancellation_observation(
    submission: dict[str, Any], entry: dict[str, Any], proof_ref: str
) -> dict[str, Any]:
    request = AdapterRequest(
        request_id=digest({
            "adapter_id": "queue", "operation": "cancel",
            "task_id": submission["task_id"], "attempt_id": submission["attempt_id"],
            "payload_digest": submission["payload_digest"],
        }),
        operation=AdapterOperation.CANCEL,
        installation_id=str(submission["installation_id"]),
        leader_epoch=int(submission["leader_epoch"]),
        task_id=str(submission["task_id"]),
        work_item_id=str(submission["work_item_id"]),
        attempt_id=str(submission["attempt_id"]),
        dispatch_id=str(submission["dispatch_id"]),
        dispatch_generation=int(submission["dispatch_generation"]),
        payload_digest=str(submission["payload_digest"]),
        expected_evidence_refs=tuple(submission["expected_refs"]),
    )
    segment = ProvenanceSegment(
        segment_id=f"{entry['event_id']}:process_bound",
        sequence=0, adapter_id="queue", abi_version=ABI_VERSION,
        input_identity=request.request_id, input_digest=request.payload_digest,
        output_identity=str(entry["event_id"]), output_digest=str(entry["entry_digest"]),
        proof_kind=ProofKind.PROCESS_BOUND, evidence_refs=(proof_ref,), acknowledged=True,
    )
    observation = AdapterObservation(
        observation_id=digest({"request_id": request.request_id, "event_id": entry["event_id"]}),
        request=request, sequence=int(entry["ledger_revision"]),
        status=AdapterStatus.CANCELLED,
        runtime_identity=(
            f"{entry['worker_id']}:{entry['run_id']}"
            if "worker_id" in entry else f"queue:{entry['queue_id']}"
        ),
        provenance=(segment,), evidence_refs=(proof_ref,),
    )
    validate_observation(runtime_adapter_manifest("queue"), observation)
    return dict(observation.canonical())


def record_queue_cancellation_proof(
    directory: Path,
    task_id: str,
    submission: dict[str, Any],
    entry: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    queue = _queue_item_for_submission(directory, submission)
    if entry.get("event") != "cancelled" or entry.get("queue_id") != queue.get("queue_id"):
        raise RuntimeAdapterError("Queue cancellation proof requires an exact cancelled entry")
    worker_acknowledged = all(
        isinstance(entry.get(key), str) and bool(entry[key].strip())
        for key in ("worker_id", "run_id", "claim_token", "claim_event_id")
    ) and entry.get("acknowledged") is True
    queued_unclaimed = (
        entry.get("queue_revision") == 1
        and isinstance(entry.get("authority"), str)
        and isinstance(entry.get("reason"), str)
    )
    if not worker_acknowledged and not queued_unclaimed:
        raise RuntimeAdapterError("Queue cancellation lacks runtime acknowledgement")
    proof_ref = f"runtime/queue/cancellation-proofs/{entry['event_id'][7:]}.json"
    proof = {
        "schema_version": "valp-queue-cancellation-proof.v1",
        "task_id": task_id,
        "work_item_id": submission["work_item_id"],
        "attempt_id": submission["attempt_id"],
        "dispatch_id": submission["dispatch_id"],
        "dispatch_generation": submission["dispatch_generation"],
        "queue_id": queue["queue_id"],
        "enqueue_transaction_id": queue["enqueue_transaction_id"],
        "cancellation_kind": (
            "worker_acknowledged" if worker_acknowledged else "queued_unclaimed"
        ),
        "cancellation_event_id": entry["event_id"],
        "lifecycle_entry_digest": entry["entry_digest"],
        "acknowledged": True,
    }
    for key in (
        "worker_id", "run_id", "claim_token", "claim_event_id", "authority", "reason"
    ):
        if key in entry:
            proof[key] = entry[key]
    proof_path = directory / proof_ref
    if proof_path.is_file():
        try:
            if json.loads(proof_path.read_text(encoding="utf-8")) != proof:
                raise RuntimeAdapterError("Queue cancellation proof conflicts")
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeAdapterError("Queue cancellation proof is malformed") from error
    else:
        write_json(proof_path, proof)
    return proof_ref, _queue_cancellation_observation(submission, entry, proof_ref)


def record_queue_cancellation_acknowledgement(
    directory: Path,
    task_id: str,
    submission: dict[str, Any],
    *,
    worker_id: str,
    run_id: str,
    claim_token: str,
    claim_event_id: str,
    expected_revision: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    queue = _queue_item_for_submission(directory, submission)
    lifecycle = load_queue_lifecycle(directory, queue["queue_id"])
    claim = next((item for item in lifecycle if item.get("event") == "claimed"), None)
    if claim is None or any(
        claim.get(key) != value for key, value in {
            "worker_id": worker_id, "run_id": run_id,
            "claim_token": claim_token, "event_id": claim_event_id,
        }.items()
    ):
        raise RuntimeAdapterError("Queue cancellation acknowledgement conflicts with claim")
    entry = _queue_lifecycle_entry(
        directory, submission, event="cancelled", state="cancelled",
        expected_revision=expected_revision,
        details={
            "worker_id": worker_id, "run_id": run_id, "claim_token": claim_token,
            "claim_event_id": claim_event_id, "acknowledged": True,
        },
    )
    _, observation = record_queue_cancellation_proof(
        directory, task_id, submission, entry
    )
    return entry, observation


def record_queue_terminal_observation(
    directory: Path,
    task_id: str,
    submission: dict[str, Any],
    observation_ref: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        submission.get("schema_version") != "valp-dispatch-receipt.v3"
        or submission.get("event") != "dispatch_submitted"
        or submission.get("task_id") != task_id
        or submission.get("mode") != "full"
        or not _safe_ref(observation_ref)
    ):
        raise RuntimeAdapterError("Queue terminal observation requires its exact submitted receipt")
    queue = _queue_item_for_submission(directory, submission)
    try:
        worker = json.loads((directory / observation_ref).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeAdapterError("Queue worker observation is unavailable or malformed") from error
    if not isinstance(worker, dict):
        raise RuntimeAdapterError("Queue worker observation must be an object")
    status = worker.get("status")
    if (
        worker.get("schema_version") != "valp-queue-worker-observation.v2"
        or worker.get("task_id") != task_id
        or worker.get("queue_id") != queue.get("queue_id")
        or worker.get("enqueue_transaction_id") != queue.get("enqueue_transaction_id")
        or not all(
            isinstance(worker.get(key), str) and worker[key].strip()
            for key in ("worker_id", "run_id", "claim_token", "claim_event_id")
        )
        or type(worker.get("observation_sequence")) is not int
        or worker["observation_sequence"] < 1
        or status not in {"completed", "blocked"}
        or worker.get("acknowledged") is not True
        or (status == "blocked") != (
            isinstance(worker.get("failure_code"), str) and bool(worker["failure_code"].strip())
        )
        or (status == "completed" and "failure_code" in worker)
    ):
        raise RuntimeAdapterError("Queue worker observation identity or terminal claim is invalid")

    lifecycle = load_queue_lifecycle(directory, str(queue["queue_id"]))
    claims = [item for item in lifecycle if item.get("event") == "claimed"]
    if len(claims) != 1 or any(
        claims[0].get(key) != worker.get(key)
        for key in ("worker_id", "run_id", "claim_token")
    ) or claims[0].get("event_id") != worker.get("claim_event_id"):
        raise RuntimeAdapterError("Queue terminal observation does not bind the exact persisted claim")

    runtime_identity = (
        f"{worker['worker_id']}:{worker['run_id']}:{worker['observation_sequence']}"
    )
    for receipt in load_runtime_v3_receipts(directory, "queue"):
        if receipt.get("event") not in {"dispatch_completed", "dispatch_blocked"}:
            continue
        for binding in receipt.get("proof_bindings") or []:
            if binding.get("proof_kind") != "process_bound":
                continue
            try:
                proof_record = json.loads((directory / binding["proof_ref"]).read_text(encoding="utf-8"))
            except (OSError, KeyError, json.JSONDecodeError) as error:
                raise RuntimeAdapterError("Queue terminal process proof is unavailable") from error
            proof = proof_record.get("proof") or {}
            if proof.get("runtime_identity") == runtime_identity and receipt.get("attempt_id") != submission.get("attempt_id"):
                raise RuntimeAdapterError("Queue worker/run observation identity was replayed across Attempts")

    evidence = []
    if status == "completed":
        for ref in submission.get("expected_refs") or []:
            path = directory / ref
            if not path.is_file() or not path.stat().st_size:
                raise RuntimeAdapterError(f"Queue completion evidence is missing: {ref}")
            evidence.append({"ref": ref, "content_digest": digest(path.read_bytes())})
    receipts = load_runtime_v3_receipts(directory, "queue")
    epochs = [
        int(item["suspension_epoch"])
        for item in receipts
        if item.get("event") in {"dispatch_completed", "dispatch_blocked"}
        and type(item.get("suspension_epoch")) is int
    ]
    suspension_epoch = max(epochs, default=0) + 1
    event = "dispatch_completed" if status == "completed" else "dispatch_blocked"
    terminal_entry = _queue_lifecycle_entry(
        directory, submission, event=status, state=status,
        expected_revision=int(claims[0]["queue_revision"]),
        details={
            "worker_id": worker["worker_id"], "run_id": worker["run_id"],
            "claim_token": worker["claim_token"],
            "claim_event_id": worker["claim_event_id"],
            "observation_sequence": worker["observation_sequence"],
            "worker_observation_ref": observation_ref,
            "worker_observation_digest": digest(worker),
        },
    )
    process = {
        "runtime_identity": runtime_identity,
        "worker_id": worker["worker_id"],
        "run_id": worker["run_id"],
        "observation_sequence": worker["observation_sequence"],
        "queue_id": queue["queue_id"],
        "enqueue_transaction_id": queue["enqueue_transaction_id"],
        "submission_receipt_id": submission["receipt_id"],
        "worker_observation_ref": observation_ref,
        "worker_observation_digest": digest(worker),
        "claim_token": worker["claim_token"],
        "claim_event_id": worker["claim_event_id"],
        "terminal_lifecycle_event_id": terminal_entry["event_id"],
        "terminal_lifecycle_entry_digest": terminal_entry["entry_digest"],
    }
    content = {
        "submission_receipt_id": submission["receipt_id"],
        "terminal_status": status,
        "expected_evidence": evidence,
        "acknowledged": True,
    }
    if status == "blocked":
        content["failure_code"] = worker["failure_code"]
    return _append(
        directory, "queue", task_id,
        agent=str(submission["agent"]), role=str(submission["role"]),
        work_item_id=str(submission["work_item_id"]),
        attempt_id=str(submission["attempt_id"]), dispatch_id=str(submission["dispatch_id"]),
        dispatch_generation=int(submission["dispatch_generation"]),
        dispatch_ref=str(submission["dispatch_ref"]),
        expected_refs=list(submission["expected_refs"]),
        payload_digest=str(submission["payload_digest"]), event=event,
        mode=ReceiptMode.FULL,
        proof_items=[
            (ReceiptProofKind.PROCESS_BOUND, process),
            (ReceiptProofKind.CONTENT_BOUND, content),
        ],
        runtime_identity=runtime_identity,
        status=AdapterStatus.COMPLETED if status == "completed" else AdapterStatus.BLOCKED,
        operation=AdapterOperation.OBSERVE,
        suspension_epoch=suspension_epoch,
    )


def _manual_authority_digest(
    directory: Path,
    task_id: str,
    authority: str,
    authority_ref: str,
    action: str,
) -> str:
    if not _safe_ref(authority_ref):
        raise RuntimeAdapterError("Manual authority ref is unsafe")
    try:
        declaration = json.loads((directory / authority_ref).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeAdapterError("Manual authority declaration is unavailable or malformed") from error
    if not isinstance(declaration, dict):
        raise RuntimeAdapterError("Manual authority declaration must be an object")
    allowed = declaration.get("allowed_actions")
    allowed_vocabulary = {
        "manual_delivery_attested", "manual_result_attested", "manual_blocked",
        "revoke", "adjudicate",
    }
    if (
        declaration.get("schema_version") != "valp-manual-authority.v1"
        or declaration.get("task_id") != task_id
        or declaration.get("authority") != authority
        or not isinstance(allowed, list)
        or action not in allowed
        or len(allowed) != len(set(allowed))
        or not set(allowed).issubset(allowed_vocabulary)
        or not all(
            isinstance(declaration.get(field), str) and declaration[field].strip()
            for field in ("issued_by", "statement")
        )
    ):
        raise RuntimeAdapterError("Manual authority declaration does not permit the exact action")
    return digest(declaration)


def record_manual_attestation(
    directory: Path,
    task_id: str,
    *,
    agent: str,
    role: str,
    work_item_id: str,
    dispatch_id: str,
    dispatch_generation: int,
    dispatch_ref: str,
    expected_refs: list[str],
    event: str,
    authority: str,
    authority_ref: str,
    statement: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if event not in {"manual_delivery_attested", "manual_result_attested", "manual_blocked"}:
        raise RuntimeAdapterError("Unsupported Manual attestation event")
    if not all(isinstance(value, str) and value.strip() for value in (authority, authority_ref, statement)):
        raise RuntimeAdapterError("Manual attestation requires authority, ref, and statement")
    authority_digest = _manual_authority_digest(
        directory, task_id, authority, authority_ref, event
    )
    refs = expected_refs if event == "manual_result_attested" else []
    evidence = []
    for ref in refs:
        path = directory / ref
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeAdapterError(f"Manual result evidence is missing: {ref}")
        evidence.append({"ref": ref, "content_digest": digest(path.read_bytes())})
    try:
        payload_digest = digest((directory / dispatch_ref).read_bytes())
    except OSError as error:
        raise RuntimeAdapterError("Manual dispatch payload is unavailable") from error
    attempt = _attempt_id("manual", {
        "task_id": task_id,
        "work_item_id": work_item_id,
        "dispatch_id": dispatch_id,
        "dispatch_generation": dispatch_generation,
        "authority": authority,
        "event": event,
    })
    attestation = {
        "schema_version": "valp-manual-attestation.v1",
        "authority": authority,
        "authority_ref": authority_ref,
        "authority_digest": authority_digest,
        "action": event,
        "statement": statement,
        "task_id": task_id,
        "work_item_id": work_item_id,
        "attempt_id": attempt,
        "dispatch_id": dispatch_id,
        "dispatch_generation": dispatch_generation,
        "payload_digest": payload_digest,
        "evidence": evidence,
        "validity": "active",
    }
    status = {
        "manual_delivery_attested": AdapterStatus.ACCEPTED,
        "manual_result_attested": AdapterStatus.COMPLETED,
        "manual_blocked": AdapterStatus.BLOCKED,
    }[event]
    suspension_epoch = 1 if event in {"manual_result_attested", "manual_blocked"} else None
    return _append(
        directory, "manual", task_id,
        agent=agent, role=role, work_item_id=work_item_id, attempt_id=attempt,
        dispatch_id=dispatch_id, dispatch_generation=dispatch_generation,
        dispatch_ref=dispatch_ref, expected_refs=expected_refs,
        payload_digest=payload_digest, event=event, mode=ReceiptMode.MANUAL,
        proof_items=[(ReceiptProofKind.MANUAL_ATTESTED, attestation)],
        runtime_identity=f"manual:{authority}", status=status,
        operation=AdapterOperation.OBSERVE,
        suspension_epoch=suspension_epoch,
    )


def _manual_decision_path(directory: Path) -> Path:
    return _runtime_root(directory, "manual") / "attestation-decisions.jsonl"


def _manual_decision_root(task_id: str) -> str:
    return digest({
        "schema_version": "valp-manual-attestation-decision-root.v1",
        "task_id": task_id,
    })


def _manual_receipt_subject_key(receipt: dict[str, Any]) -> str:
    return digest({
        "task_id": receipt.get("task_id"),
        "work_item_id": receipt.get("work_item_id"),
        "dispatch_id": receipt.get("dispatch_id"),
        "dispatch_generation": receipt.get("dispatch_generation"),
        "action": receipt.get("event"),
    })


def _load_manual_decisions(directory: Path, task_id: str) -> list[dict[str, Any]]:
    path = _manual_decision_path(directory)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RuntimeAdapterError("Manual decision ledger is unavailable") from error
    records = []
    prior = _manual_decision_root(task_id)
    for sequence, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeAdapterError("Manual decision ledger is malformed") from error
        if not isinstance(record, dict):
            raise RuntimeAdapterError("Manual decision ledger record must be an object")
        without_digest = {key: value for key, value in record.items() if key != "decision_digest"}
        if (
            not isinstance(record, dict)
            or record.get("schema_version") != "valp-manual-attestation-decision.v1"
            or record.get("task_id") != task_id
            or record.get("decision_sequence") != sequence
            or record.get("prior_decision_digest") != prior
            or record.get("decision_digest") != digest(without_digest)
        ):
            raise RuntimeAdapterError("Manual decision ledger hash chain is invalid")
        authority_digest = _manual_authority_digest(
            directory,
            task_id,
            str(record.get("authority") or ""),
            str(record.get("authority_ref") or ""),
            str(record.get("action") or ""),
        )
        if record.get("authority_digest") != authority_digest:
            raise RuntimeAdapterError("Manual decision authority declaration digest changed")
        records.append(record)
        prior = record["decision_digest"]
    return records


def _manual_receipts(directory: Path, task_id: str) -> list[dict[str, Any]]:
    installation_id, leader_epoch, _ = _reference_identity(directory, task_id)
    try:
        ledger = ReceiptStore(
            _ledger_path(directory, "manual"), installation_id, leader_epoch, task_id
        ).load()
    except ReceiptStoreError as error:
        raise RuntimeAdapterError(f"manual v3 ledger is invalid: {error.code}") from error
    return [dict(item.canonical()) for item in ledger.receipts]


def manual_effective_receipt_ids(directory: Path, task_id: str) -> set[str]:
    receipts = _manual_receipts(directory, task_id)
    manual = {
        str(item["receipt_id"]): item
        for item in receipts
        if item.get("event") in {
            "manual_dispatch_written", "manual_delivery_attested",
            "manual_result_attested", "manual_blocked",
        }
    }
    decisions = _load_manual_decisions(directory, task_id)
    revoked = {
        str(item["target_receipt_id"])
        for item in decisions
        if item.get("action") == "revoke"
    }
    effective = set(manual) - revoked
    groups: dict[str, set[str]] = {}
    for receipt_id, receipt in manual.items():
        groups.setdefault(_manual_receipt_subject_key(receipt), set()).add(receipt_id)
    adjudications = {
        str(item["subject_key_digest"]): item
        for item in decisions
        if item.get("action") == "adjudicate"
    }
    for subject_key, receipt_ids in groups.items():
        active = receipt_ids - revoked
        decision = adjudications.get(subject_key)
        if decision is not None:
            declared = set(decision.get("conflicting_receipt_ids") or [])
            selected = str(decision.get("target_receipt_id") or "")
            if declared != receipt_ids or selected not in active:
                raise RuntimeAdapterError("Manual adjudication no longer resolves the complete active subject")
            effective.difference_update(receipt_ids - {selected})
            continue
        proof_digests = set()
        for receipt_id in active:
            bindings = manual[receipt_id].get("proof_bindings") or []
            if len(bindings) != 1 or bindings[0].get("proof_kind") != "manual_attested":
                raise RuntimeAdapterError("Manual attestation proof binding is invalid")
            try:
                proof = json.loads((directory / bindings[0]["proof_ref"]).read_text(encoding="utf-8"))
            except (OSError, KeyError, json.JSONDecodeError) as error:
                raise RuntimeAdapterError("Manual attestation proof is unavailable") from error
            authority_digest = _manual_authority_digest(
                directory,
                task_id,
                str(proof.get("authority") or ""),
                str(proof.get("authority_ref") or ""),
                str(proof.get("action") or ""),
            )
            if proof.get("authority_digest") != authority_digest:
                raise RuntimeAdapterError("Manual attestation authority declaration digest changed")
            proof_digests.add(str(proof.get("source_proof_digest") or ""))
        if len(proof_digests) > 1:
            raise RuntimeAdapterError("conflicting active Manual attestations require adjudication")
    return effective


def manual_receipt_is_effective(directory: Path, task_id: str, receipt_id: str) -> bool:
    return receipt_id in manual_effective_receipt_ids(directory, task_id)


def record_manual_decision(
    directory: Path,
    task_id: str,
    *,
    action: str,
    target_receipt_id: str,
    authority: str,
    authority_ref: str,
    statement: str,
    conflicting_receipt_ids: list[str] | None = None,
) -> dict[str, Any]:
    if action not in {"revoke", "adjudicate"}:
        raise RuntimeAdapterError("Unsupported Manual attestation decision")
    if not all(isinstance(value, str) and value.strip() for value in (
        target_receipt_id, authority, authority_ref, statement
    )) or not _safe_ref(authority_ref):
        raise RuntimeAdapterError("Manual decision requires target, authority, safe ref, and statement")
    authority_digest = _manual_authority_digest(
        directory, task_id, authority, authority_ref, action
    )
    _adopt(directory, "manual", task_id)
    receipts = {str(item["receipt_id"]): item for item in _manual_receipts(directory, task_id)}
    target = receipts.get(target_receipt_id)
    if target is None or target.get("event") not in {
        "manual_dispatch_written", "manual_delivery_attested", "manual_result_attested", "manual_blocked"
    }:
        raise RuntimeAdapterError("Manual decision target is not a Manual receipt")
    subject_key = _manual_receipt_subject_key(target)
    conflicts = sorted(set(conflicting_receipt_ids or []))
    if action == "adjudicate":
        group = {
            receipt_id
            for receipt_id, receipt in receipts.items()
            if _manual_receipt_subject_key(receipt) == subject_key
        }
        if len(conflicts) < 2 or set(conflicts) != group or target_receipt_id not in group:
            raise RuntimeAdapterError("Manual adjudication requires the complete conflicting receipt set")
    elif conflicts:
        raise RuntimeAdapterError("Manual revocation cannot declare a conflict set")
    identity = {
        "task_id": task_id,
        "action": action,
        "target_receipt_id": target_receipt_id,
        "subject_key_digest": subject_key,
        "conflicting_receipt_ids": conflicts,
        "authority": authority,
        "authority_ref": authority_ref,
        "authority_digest": authority_digest,
        "statement": statement,
    }
    decision_id = digest(identity)
    from .workflow import append_json_line_durable, task_state_lock

    with task_state_lock(directory):
        prior = _load_manual_decisions(directory, task_id)
        existing = next((item for item in prior if item.get("decision_id") == decision_id), None)
        if existing is not None:
            return existing
        logical = [
            item for item in prior
            if item.get("action") == action
            and (
                item.get("target_receipt_id") == target_receipt_id
                if action == "revoke"
                else item.get("subject_key_digest") == subject_key
            )
        ]
        if logical:
            raise RuntimeAdapterError("Manual decision conflicts with an existing logical decision")
        record = {
            "schema_version": "valp-manual-attestation-decision.v1",
            "decision_id": decision_id,
            "decision_sequence": len(prior) + 1,
            "prior_decision_digest": (
                prior[-1]["decision_digest"] if prior else _manual_decision_root(task_id)
            ),
            "task_id": task_id,
            "action": action,
            "target_receipt_id": target_receipt_id,
            "subject_key_digest": subject_key,
            "authority": authority,
            "authority_ref": authority_ref,
            "authority_digest": authority_digest,
            "statement": statement,
            "ts": _now_iso(),
        }
        if action == "adjudicate":
            record["conflicting_receipt_ids"] = conflicts
        record["decision_digest"] = digest(record)
        append_json_line_durable(_manual_decision_path(directory), record)
        return record


__all__ = [
    "RuntimeAdapterError",
    "load_queue_lifecycle",
    "load_runtime_v3_receipts",
    "manual_effective_receipt_ids",
    "manual_receipt_is_effective",
    "record_herdr_completion",
    "record_herdr_submission",
    "record_herdr_transport",
    "record_manual_attestation",
    "record_manual_decision",
    "record_queue_acceptance",
    "record_queue_cancellation_acknowledgement",
    "record_queue_cancellation_proof",
    "record_queue_cancellation_request",
    "record_queue_claim",
    "record_queue_terminal_observation",
    "runtime_adapter_manifest",
]
