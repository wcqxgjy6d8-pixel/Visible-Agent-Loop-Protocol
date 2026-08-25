"""Provisional provider-neutral continuation kernel.

VALP owns immutable correlation, event ordering, and fail-closed storage. A
provider adapter must supply real invocation identity and duplicate-suppression
evidence before provider-consumption events can be committed.
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
import uuid

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows.
    fcntl = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - exercised on POSIX.
    msvcrt = None


SUCCESS_EVENTS = (
    "resume_pending",
    "resume_received",
    "digest_verified",
    "resume_accepted",
    "continuation_started",
    "resume_consumed",
)
TERMINAL_EVENTS = {"resume_consumed", "continuation_failed", "continuation_superseded"}
RUNTIME_CHANNEL = {
    "kind": "runtime_control",
    "user_input_allowed": False,
    "raw_worker_output_allowed": False,
}
DIGEST_ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_DIRECTORY_SYNC_UNSUPPORTED = {
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
}
ENVELOPE_FIELDS = {
    "schema_version", "task_id", "suspension_id", "suspension_epoch",
    "wake_id", "wake_event_id", "wake_reason", "accepted_state_revision",
    "control_contract_ref", "control_contract_digest", "payload_ref",
    "payload_digest", "continuation_generation", "target", "channel",
}
RECEIPT_FIELDS = {
    "schema_version", "task_id", "suspension_id", "suspension_epoch",
    "wake_id", "continuation_generation", "idempotency_key",
    "payload_digest", "adapter", "provider", "durable_boundary_ref",
    "identity_evidence_ref", "duplicate_suppression_ref", "started_at",
    "consumed_at", "result",
}


class ContinuationError(RuntimeError):
    """A fail-closed continuation rejection."""


class HerdrUnixSocketRpcClient:
    """Small synchronous JSON-RPC client for HERDR's local Unix socket."""

    max_response_bytes = 1024 * 1024

    def __init__(self, socket_path: Path | str, *, timeout: float = 30.0):
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ContinuationError("HERDR RPC timeout must be a positive number")
        self.socket_path = str(socket_path)
        self.timeout = float(timeout)

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(method, str) or not method or not isinstance(params, dict):
            raise ContinuationError("HERDR RPC method and params are invalid")
        request_id = f"valp:{uuid.uuid4().hex}"
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            encoded = (
                json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError) as exc:
            raise ContinuationError("HERDR RPC params are not JSON serializable") from exc
        socket_family = getattr(socket, "AF_UNIX", None)
        if socket_family is None:
            raise ContinuationError(
                "HERDR RPC transport failed: Unix-domain sockets are unavailable"
            )
        try:
            with socket.socket(socket_family, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout)
                connection.connect(self.socket_path)
                connection.sendall(encoded)
                response_bytes = bytearray()
                while b"\n" not in response_bytes:
                    chunk = connection.recv(
                        min(65536, self.max_response_bytes - len(response_bytes))
                    )
                    if not chunk:
                        raise ContinuationError("HERDR RPC closed before a response")
                    response_bytes.extend(chunk)
                    if len(response_bytes) >= self.max_response_bytes:
                        raise ContinuationError("HERDR RPC response exceeds size limit")
        except socket.timeout as exc:
            raise ContinuationError("HERDR RPC timed out") from exc
        except OSError as exc:
            raise ContinuationError(f"HERDR RPC transport failed: {exc}") from exc
        line = bytes(response_bytes).split(b"\n", 1)[0]
        try:
            response = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContinuationError("HERDR RPC response is not valid JSON") from exc
        if not isinstance(response, dict) or response.get("jsonrpc") not in {None, "2.0"}:
            raise ContinuationError("HERDR RPC response is malformed")
        if response.get("id") != request_id:
            raise ContinuationError("HERDR RPC response id does not match request")
        if "result" in response and "error" in response:
            raise ContinuationError("HERDR RPC response has both result and error")
        if "error" in response:
            error = response["error"]
            if not isinstance(error, dict) or not isinstance(error.get("message"), str):
                raise ContinuationError("HERDR RPC error is malformed")
        elif "result" not in response:
            raise ContinuationError("HERDR RPC response has no result or error")
        return response


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def content_addressed_evidence_ref(namespace: str, value: Any) -> str:
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", namespace) is None:
        raise ContinuationError("content-addressed evidence namespace is invalid")
    return f"evidence/{namespace}/{digest(value).removeprefix('sha256:')}.json"


def persist_content_addressed_evidence(
    directory: Path,
    namespace: str,
    value: Any,
) -> str:
    ref = content_addressed_evidence_ref(namespace, value)
    _write_once(directory / ref, value)
    return ref


def file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def invocation_key(envelope: dict[str, Any]) -> tuple[Any, ...]:
    return (
        envelope.get("task_id"), envelope.get("suspension_id"),
        envelope.get("suspension_epoch"), envelope.get("wake_id"),
        envelope.get("continuation_generation"),
    )


def idempotency_key(envelope: dict[str, Any]) -> str:
    return digest(list(invocation_key(envelope)))


def _safe_ref(ref: str) -> bool:
    candidate = Path(ref)
    return bool(
        ref and not candidate.is_absolute() and "\\" not in ref and ":" not in ref
        and all(part not in {"", ".", ".."} for part in ref.split("/"))
    )


def _is_digest_id(value: Any) -> bool:
    return isinstance(value, str) and DIGEST_ID_PATTERN.fullmatch(value) is not None


def _require_digest_id(field: str, value: Any) -> str:
    if not _is_digest_id(value):
        raise ContinuationError(f"continuation {field} is not a digest-shaped identifier")
    return str(value)


def _write_once(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() != encoded:
            raise ContinuationError(f"immutable artifact conflict: {path}")
        return
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        created = False
        try:
            os.link(temporary, path)
            created = True
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ContinuationError(f"immutable artifact conflict: {path}")
        if created:
            _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sync_directory(directory: Path) -> bool:
    if os.name == "nt":  # Windows has no portable directory-fsync primitive.
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(directory, flags)
    try:
        try:
            os.fsync(descriptor)
            return True
        except OSError as exc:
            if exc.errno in _DIRECTORY_SYNC_UNSUPPORTED:
                return False
            raise
    finally:
        os.close(descriptor)


def build_envelope(
    *, task_id: str, suspension_id: str, suspension_epoch: int, wake_id: str,
    wake_event_id: str, wake_reason: str, accepted_state_revision: int,
    control_contract_ref: str, control_contract_digest: str, payload: Any,
    coordinator_agent: str, adapter_id: str, provider_id: str,
    durable_boundary_ref: str, continuation_generation: int = 1,
) -> dict[str, Any]:
    for field, value in (
        ("suspension_id", suspension_id),
        ("wake_id", wake_id),
        ("wake_event_id", wake_event_id),
    ):
        _require_digest_id(field, value)
    return {
        "schema_version": "valp-continuation-envelope.v1",
        "task_id": task_id,
        "suspension_id": suspension_id,
        "suspension_epoch": suspension_epoch,
        "wake_id": wake_id,
        "wake_event_id": wake_event_id,
        "wake_reason": wake_reason,
        "accepted_state_revision": accepted_state_revision,
        "control_contract_ref": control_contract_ref,
        "control_contract_digest": control_contract_digest,
        "payload_ref": f"continuations/{wake_id.removeprefix('sha256:')}/payload.json",
        "payload_digest": digest(payload),
        "continuation_generation": continuation_generation,
        "target": {
            "coordinator_agent": coordinator_agent,
            "adapter_id": adapter_id,
            "provider_id": provider_id,
            "durable_boundary_ref": durable_boundary_ref,
        },
        "channel": dict(RUNTIME_CHANNEL),
    }


class ContinuationStore:
    """Durable envelope/event store with separate wake and invocation CAS."""

    def __init__(self, directory: Path, task_id: str):
        self.directory = Path(directory).resolve()
        self.task_id = task_id
        self.root = self.directory / "continuations"
        self.events_path = self.root / "events.jsonl"
        self.lock_path = self.root / ".lock"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:  # pragma: no cover - Windows only.
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b"0")
                    lock.flush()
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            else:
                raise ContinuationError("no supported exclusive file locking implementation")
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:  # pragma: no cover - Windows only.
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    def _events(self) -> list[dict[str, Any]]:
        if not self.events_path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(self.events_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ContinuationError(f"malformed continuation ledger line {line_number}") from exc
            if not isinstance(record, dict):
                raise ContinuationError(f"invalid continuation ledger line {line_number}")
            records.append(record)
        return records

    def _append(self, event: str, envelope: dict[str, Any], **extra: Any) -> dict[str, Any]:
        records = self._events()
        new_ledger = not self.events_path.exists()
        record = {
            "schema_version": "valp-continuation-event.v1",
            "event_sequence": len(records) + 1,
            "event": event,
            "task_id": envelope.get("task_id"),
            "suspension_id": envelope.get("suspension_id"),
            "suspension_epoch": envelope.get("suspension_epoch"),
            "wake_id": envelope.get("wake_id"),
            "wake_event_id": envelope.get("wake_event_id"),
            "continuation_generation": envelope.get("continuation_generation"),
            "idempotency_key": idempotency_key(envelope),
            "envelope_digest": digest(envelope),
            "payload_digest": envelope.get("payload_digest"),
            "control_contract_digest": envelope.get("control_contract_digest"),
            "target": envelope.get("target"),
            "channel": "runtime_control",
        }
        record.update(extra)
        record["event_id"] = digest(record)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        if new_ledger:
            _sync_directory(self.events_path.parent)
        return record

    def _matches(self, event: dict[str, Any], envelope: dict[str, Any]) -> bool:
        return all(
            event.get(field) == value
            for field, value in {
                "task_id": envelope.get("task_id"),
                "suspension_id": envelope.get("suspension_id"),
                "suspension_epoch": envelope.get("suspension_epoch"),
                "wake_id": envelope.get("wake_id"),
                "wake_event_id": envelope.get("wake_event_id"),
                "continuation_generation": envelope.get("continuation_generation"),
                "idempotency_key": idempotency_key(envelope),
                "envelope_digest": digest(envelope),
                "payload_digest": envelope.get("payload_digest"),
                "control_contract_digest": envelope.get("control_contract_digest"),
                "target": envelope.get("target"),
            }.items()
        )

    def _artifact_dir(self, envelope: dict[str, Any]) -> Path:
        wake_id = _require_digest_id("wake_id", envelope.get("wake_id"))
        return self.root / wake_id.removeprefix("sha256:")

    def _active_epoch(self, suspension_id: str) -> int:
        state_path = self.directory / "state.json"
        if not state_path.is_file():
            raise ContinuationError("authoritative state.json suspension epoch is missing")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ContinuationError("state.json is malformed") from exc
        suspension = state.get("suspension")
        if isinstance(suspension, dict):
            state_suspension_id = suspension.get("suspension_id")
            value = suspension.get("suspension_epoch")
        else:
            state_suspension_id = state.get("suspension_id")
            value = state.get("suspension_epoch")
        if state_suspension_id != suspension_id:
            raise ContinuationError("active suspension identity mismatch")
        if type(value) is not int or value < 1:
            raise ContinuationError("authoritative state.json suspension epoch is invalid")
        return value

    def _validate_envelope(self, envelope: dict[str, Any]) -> None:
        if set(envelope) != ENVELOPE_FIELDS or envelope.get("schema_version") != "valp-continuation-envelope.v1":
            raise ContinuationError("continuation envelope fields are invalid")
        if envelope.get("task_id") != self.task_id:
            raise ContinuationError("cross-task continuation rejected")
        if envelope.get("channel") != RUNTIME_CHANNEL:
            raise ContinuationError("continuation channel is not typed runtime_control")
        for field in ("suspension_id", "wake_id", "wake_event_id"):
            _require_digest_id(field, envelope.get(field))
        if not isinstance(envelope.get("suspension_epoch"), int) or envelope["suspension_epoch"] < 1:
            raise ContinuationError("continuation suspension epoch is invalid")
        if not isinstance(envelope.get("continuation_generation"), int) or envelope["continuation_generation"] < 1:
            raise ContinuationError("continuation generation is invalid")
        target = envelope.get("target")
        if not isinstance(target, dict) or set(target) != {"coordinator_agent", "adapter_id", "provider_id", "durable_boundary_ref"} or any(not str(value) for value in target.values()):
            raise ContinuationError("continuation target tuple is invalid")
        control_ref = str(envelope.get("control_contract_ref") or "")
        if not _safe_ref(control_ref):
            raise ContinuationError("control contract ref is unsafe")
        if not _is_digest_id(envelope.get("control_contract_digest")):
            raise ContinuationError("control contract digest is invalid")
        if not _is_digest_id(envelope.get("payload_digest")):
            raise ContinuationError("payload digest is invalid")
        payload_ref = str(envelope.get("payload_ref") or "")
        expected_payload_ref = f"continuations/{envelope['wake_id'].removeprefix('sha256:')}/payload.json"
        if not _safe_ref(payload_ref) or payload_ref != expected_payload_ref:
            raise ContinuationError("payload ref is unsafe or mismatched")
        control_path = self.directory / control_ref
        if not control_path.is_file() or file_digest(control_path) != envelope.get("control_contract_digest"):
            raise ContinuationError("control contract digest mismatch")
        active_epoch = self._active_epoch(str(envelope["suspension_id"]))
        if envelope["suspension_epoch"] != active_epoch:
            if envelope["suspension_epoch"] < active_epoch:
                raise ContinuationError("stale suspension epoch")
            raise ContinuationError("future suspension epoch")

    def _reject_locked(self, envelope: dict[str, Any], reason: str) -> dict[str, Any]:
        return self._append("continuation_rejected", envelope, reason=reason)

    def _persisted(self, envelope: dict[str, Any], payload: Any | None = None) -> tuple[dict[str, Any], Any]:
        self._validate_envelope(envelope)
        artifact_dir = self._artifact_dir(envelope)
        envelope_path = artifact_dir / "envelope.json"
        payload_path = artifact_dir / "payload.json"
        if not envelope_path.is_file() or not payload_path.is_file():
            raise ContinuationError("persisted resume_pending envelope or payload is missing")
        try:
            persisted_envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
            persisted_payload = json.loads(payload_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ContinuationError("persisted continuation artifact is malformed") from exc
        if persisted_envelope != envelope or digest(persisted_envelope) != digest(envelope):
            raise ContinuationError("persisted continuation envelope conflict")
        if digest(persisted_payload) != envelope.get("payload_digest"):
            raise ContinuationError("persisted continuation payload digest mismatch")
        if payload is not None and persisted_payload != payload:
            raise ContinuationError("continuation payload differs from persisted payload")
        if not any(event.get("event") == "resume_pending" and self._matches(event, envelope) for event in self._events()):
            raise ContinuationError("persisted resume_pending event is missing")
        return persisted_envelope, persisted_payload

    def pending(self, envelope: dict[str, Any], payload: Any) -> dict[str, Any]:
        with self._locked():
            try:
                self._validate_envelope(envelope)
                if digest(payload) != envelope.get("payload_digest"):
                    raise ContinuationError("payload digest mismatch")
                for path in self.root.glob("*/envelope.json"):
                    prior = json.loads(path.read_text(encoding="utf-8"))
                    if invocation_key(prior) == invocation_key(envelope) and prior != envelope:
                        raise ContinuationError("conflicting continuation for invocation key")
                artifact_dir = self._artifact_dir(envelope)
                if (artifact_dir / "envelope.json").exists():
                    self._persisted(envelope, payload)
                    return envelope
                _write_once(artifact_dir / "envelope.json", envelope)
                _write_once(artifact_dir / "payload.json", payload)
                self._append("resume_pending", envelope)
                return envelope
            except ContinuationError as exc:
                if set(envelope).issuperset({"task_id", "suspension_id", "suspension_epoch", "wake_id", "continuation_generation", "payload_digest", "control_contract_digest", "target"}):
                    self._reject_locked(envelope, str(exc))
                raise

    def receive(self, envelope: dict[str, Any], payload: Any) -> list[dict[str, Any]]:
        with self._locked():
            try:
                self._persisted(envelope, payload)
            except ContinuationError as exc:
                self._reject_locked(envelope, str(exc))
                raise
            existing = self._events()
            for name in ("resume_received", "digest_verified"):
                if not any(event.get("event") == name and self._matches(event, envelope) for event in existing):
                    self._append(name, envelope)
                    existing = self._events()
            return existing

    def claim(self, envelope: dict[str, Any]) -> dict[str, Any]:
        with self._locked():
            try:
                self._persisted(envelope)
            except ContinuationError as exc:
                self._reject_locked(envelope, str(exc))
                raise
            existing = self._events()
            conflict = next((event for event in existing if event.get("event") == "resume_accepted" and event.get("idempotency_key") == idempotency_key(envelope) and not self._matches(event, envelope)), None)
            if conflict:
                self._reject_locked(envelope, "conflicting invocation CAS")
                raise ContinuationError("conflicting invocation CAS")
            prior = next((event for event in existing if event.get("event") == "resume_accepted" and self._matches(event, envelope)), None)
            if prior:
                return prior
            if not any(event.get("event") == "digest_verified" and self._matches(event, envelope) for event in existing):
                self._reject_locked(envelope, "invocation CAS requires correlated digest_verified")
                raise ContinuationError("invocation CAS requires correlated digest_verified")
            return self._append("resume_accepted", envelope)

    def register_capability(self, capability: dict[str, Any]) -> dict[str, Any]:
        proof = capability.get("proof") if isinstance(capability, dict) else None
        if (
            capability.get("schema_version") != "valp-continuation-capability.v1"
            or capability.get("mode") != "automatic_full"
            or not isinstance(proof, dict)
            or proof.get("invocation_receipt") is not True
            or proof.get("duplicate_suppression") is not True
        ):
            raise ContinuationError("automatic continuation capability proof is incomplete")
        for field in ("identity_evidence_ref", "duplicate_suppression_evidence_ref"):
            ref = str(proof.get(field) or "")
            if not _safe_ref(ref) or not (self.directory / ref).is_file():
                raise ContinuationError(f"capability {field} is missing or unsafe")
        with self._locked():
            _write_once(self.root / "capability.json", capability)
        return capability

    def _capability(self, envelope: dict[str, Any]) -> dict[str, Any]:
        path = self.root / "capability.json"
        if not path.is_file():
            raise ContinuationError("automatic continuation capability is not registered")
        try:
            capability = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ContinuationError("continuation capability is malformed") from exc
        target = envelope["target"]
        if (
            capability.get("mode") != "automatic_full"
            or capability.get("adapter_id") != target["adapter_id"]
            or capability.get("provider_id") != target["provider_id"]
            or capability.get("coordinator_surface") != target["coordinator_agent"]
        ):
            raise ContinuationError("continuation capability does not match target tuple")
        proof = capability.get("proof") or {}
        if proof.get("invocation_receipt") is not True or proof.get("duplicate_suppression") is not True:
            raise ContinuationError("continuation capability proof is incomplete")
        for field in ("identity_evidence_ref", "duplicate_suppression_evidence_ref"):
            ref = str(proof.get(field) or "")
            if not _safe_ref(ref) or not (self.directory / ref).is_file():
                raise ContinuationError(f"continuation capability {field} is invalid")
        return capability

    def _validate_receipt(self, receipt: Any, envelope: dict[str, Any], capability: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(receipt, dict) or set(receipt) != RECEIPT_FIELDS:
            raise ContinuationError("complete provider invocation receipt is required")
        expected = {
            "schema_version": "valp-continuation-invocation-receipt.v1",
            "task_id": envelope["task_id"],
            "suspension_id": envelope["suspension_id"],
            "suspension_epoch": envelope["suspension_epoch"],
            "wake_id": envelope["wake_id"],
            "continuation_generation": envelope["continuation_generation"],
            "idempotency_key": idempotency_key(envelope),
            "payload_digest": envelope["payload_digest"],
            "durable_boundary_ref": envelope["target"]["durable_boundary_ref"],
            "identity_evidence_ref": capability["proof"]["identity_evidence_ref"],
            "duplicate_suppression_ref": capability["proof"]["duplicate_suppression_evidence_ref"],
            "result": "consumed",
        }
        if any(receipt.get(field) != value for field, value in expected.items()):
            raise ContinuationError("provider invocation receipt correlation mismatch")
        adapter = receipt.get("adapter")
        provider = receipt.get("provider")
        if (
            not isinstance(adapter, dict)
            or set(adapter) != {"id", "version"}
            or adapter.get("id") != capability["adapter_id"]
            or adapter.get("version") != capability["adapter_version"]
            or not isinstance(provider, dict)
            or set(provider) != {"id", "invocation_id", "turn_id"}
            or provider.get("id") != capability["provider_id"]
            or not provider.get("invocation_id")
            or not provider.get("turn_id")
            or not receipt.get("started_at")
            or not receipt.get("consumed_at")
        ):
            raise ContinuationError("provider invocation receipt identity is incomplete")
        for field in ("identity_evidence_ref", "duplicate_suppression_ref"):
            ref = str(receipt[field])
            if not _safe_ref(ref) or not (self.directory / ref).is_file():
                raise ContinuationError(f"provider receipt {field} is missing or unsafe")
        return receipt

    def _finalize_receipt_locked(
        self,
        envelope: dict[str, Any],
        validated: dict[str, Any],
    ) -> dict[str, Any]:
        self._persisted(envelope)
        receipt_path = self._artifact_dir(envelope) / "invocation-receipt.json"
        _write_once(receipt_path, validated)
        receipt_ref = receipt_path.relative_to(self.directory).as_posix()
        receipt_digest = digest(validated)
        invocation_id = validated["provider"]["invocation_id"]
        events = self._events()
        expected = {
            "invocation_id": invocation_id,
            "invocation_receipt_ref": receipt_ref,
            "invocation_receipt_digest": receipt_digest,
        }
        started = next((
            event for event in events
            if event.get("event") == "continuation_started"
            and self._matches(event, envelope)
        ), None)
        if started is not None and any(started.get(key) != value for key, value in expected.items()):
            raise ContinuationError("committed continuation_started conflicts with receipt")
        if started is None:
            self._append("continuation_started", envelope, **expected)
        events = self._events()
        consumed = next((
            event for event in events
            if event.get("event") == "resume_consumed"
            and self._matches(event, envelope)
        ), None)
        if consumed is not None and any(consumed.get(key) != value for key, value in expected.items()):
            raise ContinuationError("committed resume_consumed conflicts with receipt")
        if consumed is None:
            self._append("resume_consumed", envelope, **expected)
        (self._artifact_dir(envelope) / "invocation.inflight").unlink(missing_ok=True)
        return validated

    def consume(
        self,
        envelope: dict[str, Any],
        invoke: Callable[[], dict[str, Any]],
        reconcile: Callable[[], dict[str, Any] | None] | None = None,
    ) -> dict[str, Any]:
        self.claim(envelope)
        reconcile_only = False
        with self._locked():
            self._persisted(envelope)
            prior = next((event for event in self._events() if event.get("event") == "resume_consumed" and self._matches(event, envelope)), None)
            receipt_path = self._artifact_dir(envelope) / "invocation-receipt.json"
            capability = self._capability(envelope)
            if prior:
                if not receipt_path.is_file():
                    raise ContinuationError("committed invocation receipt is missing")
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                validated = self._validate_receipt(receipt, envelope, capability)
                if digest(validated) != prior.get("invocation_receipt_digest"):
                    raise ContinuationError("committed invocation receipt changed")
                return validated
            if receipt_path.is_file():
                try:
                    persisted_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    raise ContinuationError("persisted invocation receipt is malformed") from exc
                validated = self._validate_receipt(
                    persisted_receipt, envelope, capability
                )
                return self._finalize_receipt_locked(envelope, validated)
            marker = self._artifact_dir(envelope) / "invocation.inflight"
            if marker.exists():
                if reconcile is None:
                    raise ContinuationError(
                        "continuation invocation is already in flight or indeterminate"
                    )
                reconcile_only = True
            else:
                intent = {
                    "schema_version": "valp-continuation-invocation-intent.v1",
                    "task_id": envelope["task_id"],
                    "wake_id": envelope["wake_id"],
                    "continuation_generation": envelope["continuation_generation"],
                    "idempotency_key": idempotency_key(envelope),
                    "envelope_digest": digest(envelope),
                    "target": envelope["target"],
                }
                _write_once(marker, intent)
        try:
            if reconcile_only:
                assert reconcile is not None
                receipt = reconcile()
                if receipt is None:
                    raise ContinuationError(
                        "provider reconciliation did not prove continuation consumption"
                    )
            else:
                receipt = invoke()
        except Exception as exc:
            if reconcile is None:
                with self._locked():
                    self._append(
                        "continuation_failed",
                        envelope,
                        reason=f"provider invocation outcome indeterminate: {type(exc).__name__}",
                    )
            raise
        try:
            validated = self._validate_receipt(receipt, envelope, capability)
        except ContinuationError as exc:
            with self._locked():
                self._reject_locked(envelope, str(exc))
            raise
        with self._locked():
            return self._finalize_receipt_locked(envelope, validated)

    def consume_with_adapter(
        self,
        envelope: dict[str, Any],
        payload: Any,
        adapter: Any,
    ) -> dict[str, Any]:
        reconcile = getattr(adapter, "reconcile", None)
        return self.consume(
            envelope,
            lambda: adapter.invoke(envelope, payload),
            (
                (lambda: reconcile(envelope, payload))
                if callable(reconcile) else None
            ),
        )

    def reject(self, envelope: dict[str, Any], reason: str) -> dict[str, Any]:
        with self._locked():
            self._validate_envelope(envelope)
            return self._reject_locked(envelope, str(reason))

    def fail(self, envelope: dict[str, Any], reason: str) -> dict[str, Any]:
        with self._locked():
            self._persisted(envelope)
            return self._append("continuation_failed", envelope, reason=str(reason))

    def supersede(self, envelope: dict[str, Any], replacement_generation: int, reason: str) -> dict[str, Any]:
        if replacement_generation <= int(envelope.get("continuation_generation") or 0):
            raise ContinuationError("supersession generation must advance")
        with self._locked():
            self._persisted(envelope)
            return self._append("continuation_superseded", envelope, replacement_generation=replacement_generation, reason=str(reason))

    def recover_pending(self) -> list[tuple[dict[str, Any], Any]]:
        recovered: list[tuple[dict[str, Any], Any]] = []
        with self._locked():
            events = self._events()
            for path in sorted(self.root.glob("*/envelope.json")):
                try:
                    envelope = json.loads(path.read_text(encoding="utf-8"))
                    _, payload = self._persisted(envelope)
                except (OSError, json.JSONDecodeError, ContinuationError):
                    continue
                terminal = any(event.get("event") in TERMINAL_EVENTS and self._matches(event, envelope) for event in events)
                if not terminal:
                    recovered.append((envelope, payload))
        return recovered

    accept_wake = pending
    invoke = consume

    def events(self) -> list[dict[str, Any]]:
        with self._locked():
            return self._events()


class HermesCliAdapter:
    """Manual/degraded marker for Hermes; no automatic transport is exposed."""

    adapter_id = "hermes-cli"

    def __init__(self, provider_id: str = "test-provider"):
        self.provider_id = str(provider_id)

    def invoke(self, envelope: dict[str, Any], payload: Any) -> dict[str, Any]:
        raise ContinuationError(
            "Hermes automatic continuation is unsupported: -z is oneshot and "
            "chat -q --resume is user-message transport, not runtime_control"
        )

    def capability(self) -> dict[str, Any]:
        return capability_declaration(
            self.adapter_id, "cli", self.provider_id, "hermes-coordinator",
            invocation_proof=False, duplicate_suppression=False,
        )


class HerdrCoordinatorContinuationAdapter:
    """Typed HERDR coordinator continuation with invocation receipts."""

    adapter_id = "herdr-coordinator-continuation"
    adapter_version = "1"

    def __init__(
        self,
        *,
        runtime_session_id: str,
        provider_id: str,
        coordinator_agent: str,
        continue_coordinator: Callable[[dict[str, Any]], dict[str, Any]],
        identity_evidence_ref: str,
        duplicate_suppression_evidence_ref: str,
    ):
        values = (
            runtime_session_id,
            provider_id,
            coordinator_agent,
            identity_evidence_ref,
            duplicate_suppression_evidence_ref,
        )
        if not all(isinstance(value, str) and value for value in values):
            raise ContinuationError("HERDR coordinator continuation identity is incomplete")
        if not callable(continue_coordinator):
            raise ContinuationError("HERDR coordinator continuation API is unavailable")
        self.runtime_session_id = runtime_session_id
        self.provider_id = provider_id
        self.coordinator_agent = coordinator_agent
        self.continue_coordinator = continue_coordinator
        self.identity_evidence_ref = identity_evidence_ref
        self.duplicate_suppression_evidence_ref = (
            duplicate_suppression_evidence_ref
        )

    def capability(self) -> dict[str, Any]:
        return capability_declaration(
            self.adapter_id,
            self.adapter_version,
            self.provider_id,
            self.coordinator_agent,
            automatic_full=True,
            invocation_proof=True,
            duplicate_suppression=True,
            identity_evidence_ref=self.identity_evidence_ref,
            duplicate_suppression_evidence_ref=(
                self.duplicate_suppression_evidence_ref
            ),
        )

    def invoke(self, envelope: dict[str, Any], payload: Any) -> dict[str, Any]:
        target = envelope.get("target") if isinstance(envelope, dict) else None
        if (
            not isinstance(target, dict)
            or target.get("adapter_id") != self.adapter_id
            or target.get("provider_id") != self.provider_id
            or target.get("coordinator_agent") != self.coordinator_agent
        ):
            raise ContinuationError("HERDR coordinator continuation target tuple mismatch")
        response = self.continue_coordinator({
            "schema_version": "valp-herdr-coordinator-continuation-request.v1",
            "method": "coordinator.continue",
            "session_id": self.runtime_session_id,
            "idempotency_key": idempotency_key(envelope),
            "channel": dict(RUNTIME_CHANNEL),
            "envelope": envelope,
            "payload": payload,
            "identity_evidence_ref": self.identity_evidence_ref,
            "duplicate_suppression_ref": (
                self.duplicate_suppression_evidence_ref
            ),
        })
        if (
            not isinstance(response, dict)
            or response.get("schema_version")
            != "valp-herdr-coordinator-continuation-response.v1"
            or response.get("status") != "consumed"
            or not isinstance(response.get("receipt"), dict)
        ):
            raise ContinuationError(
                "HERDR coordinator continuation response lacks a consumed receipt"
            )
        return response["receipt"]


class HermesRuntimeControlAdapter:
    """Typed Hermes TUI gateway adapter with provider-owned deduplication."""

    adapter_id = "hermes-tui-runtime-control"
    adapter_version = "1"

    def __init__(
        self,
        *,
        runtime_session_id: str,
        provider_id: str,
        rpc_call: Callable[[str, dict[str, Any]], dict[str, Any]],
        identity_evidence_ref: str,
        duplicate_suppression_evidence_ref: str,
        poll_interval: float = 0.1,
        timeout: float = 120.0,
    ):
        self.runtime_session_id = str(runtime_session_id)
        self.provider_id = str(provider_id)
        self.rpc_call = rpc_call
        self.identity_evidence_ref = str(identity_evidence_ref)
        self.duplicate_suppression_evidence_ref = str(
            duplicate_suppression_evidence_ref
        )
        self.poll_interval = max(0.01, float(poll_interval))
        self.timeout = max(self.poll_interval, float(timeout))

    @staticmethod
    def _rpc_result(response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise ContinuationError("Hermes runtime-control response is malformed")
        if response.get("error"):
            error = response["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise ContinuationError(f"Hermes runtime-control rejected: {message}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ContinuationError("Hermes runtime-control result is missing")
        return result

    def capability(self) -> dict[str, Any]:
        return capability_declaration(
            self.adapter_id,
            self.adapter_version,
            self.provider_id,
            "hermes",
            automatic_full=True,
            invocation_proof=True,
            duplicate_suppression=True,
            identity_evidence_ref=self.identity_evidence_ref,
            duplicate_suppression_evidence_ref=(
                self.duplicate_suppression_evidence_ref
            ),
        )

    def invoke(self, envelope: dict[str, Any], payload: Any) -> dict[str, Any]:
        target = envelope.get("target") if isinstance(envelope, dict) else None
        if (
            not isinstance(target, dict)
            or target.get("adapter_id") != self.adapter_id
            or target.get("provider_id") != self.provider_id
            or target.get("coordinator_agent") != "hermes"
        ):
            raise ContinuationError("Hermes runtime-control target tuple mismatch")
        submitted = self._rpc_result(
            self.rpc_call(
                "runtime_control.submit",
                {
                    "session_id": self.runtime_session_id,
                    "envelope": envelope,
                    "payload": payload,
                    "identity_evidence_ref": self.identity_evidence_ref,
                    "duplicate_suppression_ref": (
                        self.duplicate_suppression_evidence_ref
                    ),
                },
            )
        )
        if submitted.get("status") == "consumed":
            receipt = submitted.get("receipt")
            if not isinstance(receipt, dict):
                raise ContinuationError("Hermes consumed result has no receipt")
            return receipt
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            status = self._rpc_result(
                self.rpc_call("runtime_control.status", {"envelope": envelope})
            )
            if status.get("status") == "consumed":
                receipt = status.get("receipt")
                if not isinstance(receipt, dict):
                    raise ContinuationError("Hermes consumed status has no receipt")
                return receipt
            if status.get("status") in {"failed", "missing", "invalid"}:
                failure = status.get("failure")
                reason = (
                    failure.get("reason")
                    if isinstance(failure, dict)
                    else status.get("status")
                )
                raise ContinuationError(
                    f"Hermes runtime-control did not consume: {reason}"
                )
            time.sleep(self.poll_interval)
        raise ContinuationError("Hermes runtime-control receipt wait timed out")


class HerdrCoordinatorContinueAdapter:
    """Translate HERDR 0.8 coordinator.continue receipts into VALP receipts."""

    adapter_id = "herdr-coordinator-continue"
    adapter_version = "0.8"

    def __init__(
        self,
        *,
        coordinator_target: str,
        runtime_coordinator_id: str,
        runtime_session_id: str,
        provider_id: str,
        rpc_call: Callable[[str, dict[str, Any]], dict[str, Any]],
        identity_evidence_ref: str,
        duplicate_suppression_evidence_ref: str,
        timeout_ms: int | None = None,
        approval_granted: bool = False,
    ):
        if timeout_ms is not None and (type(timeout_ms) is not int or timeout_ms < 0):
            raise ContinuationError("HERDR coordinator timeout must be a non-negative integer")
        if type(approval_granted) is not bool:
            raise ContinuationError("HERDR continuation approval must be a boolean")
        self.coordinator_target = str(coordinator_target)
        self.runtime_coordinator_id = str(runtime_coordinator_id)
        self.runtime_session_id = str(runtime_session_id)
        self.provider_id = str(provider_id)
        self.rpc_call = rpc_call
        self.identity_evidence_ref = str(identity_evidence_ref)
        self.duplicate_suppression_evidence_ref = str(duplicate_suppression_evidence_ref)
        self.timeout_ms = timeout_ms
        self.approval_granted = approval_granted

    def capability(self) -> dict[str, Any]:
        return capability_declaration(
            self.adapter_id,
            self.adapter_version,
            self.provider_id,
            "codex",
            automatic_full=True,
            invocation_proof=True,
            duplicate_suppression=True,
            identity_evidence_ref=self.identity_evidence_ref,
            duplicate_suppression_evidence_ref=self.duplicate_suppression_evidence_ref,
        )

    def _runtime_receipt(self, response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise ContinuationError("HERDR coordinator.continue response is malformed")
        if response.get("error"):
            error = response["error"]
            if isinstance(error, dict):
                code = str(error.get("code") or "unknown_error")
                message = str(error.get("message") or "unspecified rejection")
            else:
                code = "malformed_error"
                message = str(error)
            raise ContinuationError(
                f"HERDR coordinator.continue rejected [{code}]: {message}"
            )
        result = response.get("result")
        if not isinstance(result, dict) or result.get("type") != "coordinator_continued":
            raise ContinuationError("HERDR coordinator.continue did not return coordinator_continued")
        receipt = result.get("receipt")
        required = {
            "invocation_id", "task_id", "resume_id", "coordinator_id", "provider",
            "session_id", "state_change_seq", "prompt_digest", "revision", "event_chain",
        }
        if not isinstance(receipt, dict) or not required.issubset(receipt):
            raise ContinuationError("HERDR coordinator.continue receipt is incomplete")
        return receipt

    def _request_params(
        self,
        envelope: dict[str, Any],
        payload: Any,
    ) -> dict[str, Any]:
        target = envelope.get("target") if isinstance(envelope, dict) else None
        if (
            not isinstance(target, dict)
            or target.get("adapter_id") != self.adapter_id
            or target.get("provider_id") != self.provider_id
            or target.get("coordinator_agent") != "codex"
            or target.get("durable_boundary_ref") != self.coordinator_target
        ):
            raise ContinuationError("HERDR coordinator.continue target tuple mismatch")
        params: dict[str, Any] = {
            "target": self.coordinator_target,
            "task_id": envelope.get("task_id"),
            "resume_id": envelope.get("wake_id"),
            "text": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "idempotency_key": idempotency_key(envelope),
            "approval_granted": self.approval_granted,
        }
        if self.timeout_ms is not None:
            params["timeout_ms"] = self.timeout_ms
        return params

    def invoke(self, envelope: dict[str, Any], payload: Any) -> dict[str, Any]:
        params = self._request_params(envelope, payload)
        runtime = self._runtime_receipt(self.rpc_call("coordinator.continue", params))
        if (
            runtime.get("task_id") != envelope.get("task_id")
            or runtime.get("resume_id") != envelope.get("wake_id")
            or runtime.get("coordinator_id") != self.runtime_coordinator_id
            or runtime.get("provider") != self.provider_id
            or runtime.get("session_id") != self.runtime_session_id
            or runtime.get("event_chain") != list(SUCCESS_EVENTS)
            or not str(runtime.get("invocation_id") or "").strip()
            or not _is_digest_id(runtime.get("prompt_digest"))
            or type(runtime.get("state_change_seq")) is not int
            or type(runtime.get("revision")) is not int
            or runtime["state_change_seq"] < 0
            or runtime["revision"] < 0
        ):
            raise ContinuationError("HERDR coordinator.continue receipt correlation mismatch")
        observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return {
            "schema_version": "valp-continuation-invocation-receipt.v1",
            "task_id": envelope["task_id"],
            "suspension_id": envelope["suspension_id"],
            "suspension_epoch": envelope["suspension_epoch"],
            "wake_id": envelope["wake_id"],
            "continuation_generation": envelope["continuation_generation"],
            "idempotency_key": idempotency_key(envelope),
            "payload_digest": envelope["payload_digest"],
            "adapter": {"id": self.adapter_id, "version": self.adapter_version},
            "provider": {
                "id": self.provider_id,
                "invocation_id": runtime["invocation_id"],
                "turn_id": f"{self.runtime_session_id}:state-change:{runtime['state_change_seq']}",
            },
            "durable_boundary_ref": self.coordinator_target,
            "identity_evidence_ref": self.identity_evidence_ref,
            "duplicate_suppression_ref": self.duplicate_suppression_evidence_ref,
            "started_at": observed_at,
            "consumed_at": observed_at,
            "result": "consumed",
        }

    def reconcile(self, envelope: dict[str, Any], payload: Any) -> dict[str, Any]:
        # HERDR 0.8 exposes idempotent coordinator.continue rather than a
        # separate status method. Replaying the exact key returns the durable
        # receipt or fails closed on conflicting content.
        return self.invoke(envelope, payload)


class SubprocessRuntimeControlAdapter:
    """Provider-neutral JSON-RPC continuation over an explicit local argv."""

    adapter_id = "subprocess-runtime-control"
    adapter_version = "1"

    def __init__(
        self,
        *,
        command: tuple[str, ...],
        provider_id: str,
        coordinator_surface: str,
        identity_evidence_ref: str,
        duplicate_suppression_evidence_ref: str,
        poll_interval: float = 0.1,
        timeout: float = 120.0,
    ):
        if (
            not isinstance(command, tuple)
            or not command
            or not all(isinstance(item, str) and item for item in command)
        ):
            raise ContinuationError("subprocess runtime-control command is invalid")
        if not all(
            isinstance(value, str) and value
            for value in (
                provider_id, coordinator_surface, identity_evidence_ref,
                duplicate_suppression_evidence_ref,
            )
        ):
            raise ContinuationError("subprocess runtime-control identity is incomplete")
        self.command = command
        self.provider_id = provider_id
        self.coordinator_surface = coordinator_surface
        self.identity_evidence_ref = identity_evidence_ref
        self.duplicate_suppression_evidence_ref = (
            duplicate_suppression_evidence_ref
        )
        self.poll_interval = max(0.01, float(poll_interval))
        self.timeout = max(self.poll_interval, float(timeout))

    def capability(self) -> dict[str, Any]:
        return capability_declaration(
            self.adapter_id,
            self.adapter_version,
            self.provider_id,
            self.coordinator_surface,
            automatic_full=True,
            invocation_proof=True,
            duplicate_suppression=True,
            identity_evidence_ref=self.identity_evidence_ref,
            duplicate_suppression_evidence_ref=(
                self.duplicate_suppression_evidence_ref
            ),
        )

    def _validate_target(self, envelope: dict[str, Any]) -> None:
        target = envelope.get("target") if isinstance(envelope, dict) else None
        if (
            not isinstance(target, dict)
            or target.get("adapter_id") != self.adapter_id
            or target.get("provider_id") != self.provider_id
            or target.get("coordinator_agent") != self.coordinator_surface
        ):
            raise ContinuationError("subprocess runtime-control target tuple mismatch")

    def _call(
        self,
        method: str,
        envelope: dict[str, Any],
        payload: Any,
    ) -> dict[str, Any]:
        request = {
            "jsonrpc": "2.0",
            "id": idempotency_key(envelope),
            "method": method,
            "params": {
                "envelope": envelope,
                "payload": payload,
                "idempotency_key": idempotency_key(envelope),
                "identity_evidence_ref": self.identity_evidence_ref,
                "duplicate_suppression_ref": (
                    self.duplicate_suppression_evidence_ref
                ),
            },
        }
        try:
            completed = subprocess.run(
                list(self.command),
                input=json.dumps(request, ensure_ascii=False),
                text=True,
                encoding="utf-8",
                errors="strict",
                capture_output=True,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ContinuationError(
                f"subprocess runtime-control invocation failed: {type(exc).__name__}"
            ) from exc
        if completed.returncode != 0:
            raise ContinuationError(
                f"subprocess runtime-control exited {completed.returncode}"
            )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ContinuationError(
                "subprocess runtime-control response is malformed"
            ) from exc
        return HermesRuntimeControlAdapter._rpc_result(response)

    @staticmethod
    def _receipt(result: dict[str, Any]) -> dict[str, Any] | None:
        if result.get("status") != "consumed":
            return None
        receipt = result.get("receipt")
        if not isinstance(receipt, dict):
            raise ContinuationError(
                "subprocess consumed result has no invocation receipt"
            )
        return receipt

    def invoke(self, envelope: dict[str, Any], payload: Any) -> dict[str, Any]:
        self._validate_target(envelope)
        result = self._call("runtime_control.submit", envelope, payload)
        receipt = self._receipt(result)
        if receipt is not None:
            return receipt
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            receipt = self.reconcile(envelope, payload)
            if receipt is not None:
                return receipt
            time.sleep(self.poll_interval)
        raise ContinuationError(
            "subprocess runtime-control receipt wait timed out"
        )

    def reconcile(
        self, envelope: dict[str, Any], payload: Any
    ) -> dict[str, Any] | None:
        self._validate_target(envelope)
        result = self._call("runtime_control.status", envelope, payload)
        if result.get("status") in {"failed", "missing", "invalid"}:
            raise ContinuationError(
                f"subprocess runtime-control reconciliation failed: {result.get('status')}"
            )
        return self._receipt(result)


class SafePointQueue:
    """Queue runtime-control envelopes and recover persisted pending work."""

    def __init__(self, store: ContinuationStore):
        self.store = store
        self.busy = False
        self._pending = store.recover_pending()

    def enqueue(self, envelope: dict[str, Any], payload: Any) -> None:
        self.store.pending(envelope, payload)
        if not any(invocation_key(item[0]) == invocation_key(envelope) for item in self._pending):
            self._pending.append((envelope, payload))

    def safe_point(self, invoke: Callable[[dict[str, Any], Any], dict[str, Any]]) -> list[dict[str, Any]]:
        if self.busy:
            return []
        results: list[dict[str, Any]] = []
        while self._pending:
            envelope, payload = self._pending.pop(0)
            self.store.receive(envelope, payload)
            results.append(self.store.consume(envelope, lambda e=envelope, p=payload: invoke(e, p)))
        return results


def capability_declaration(
    adapter_id: str, adapter_version: str, provider_id: str,
    coordinator_surface: str, *, automatic_full: bool = False,
    invocation_proof: bool = False, duplicate_suppression: bool = False,
    identity_evidence_ref: str = "", duplicate_suppression_evidence_ref: str = "",
) -> dict[str, Any]:
    full = bool(
        automatic_full and invocation_proof and duplicate_suppression
        and identity_evidence_ref and duplicate_suppression_evidence_ref
    )
    mode = "automatic_full" if full else ("degraded" if invocation_proof else "manual")
    return {
        "schema_version": "valp-continuation-capability.v1",
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "provider_id": provider_id,
        "coordinator_surface": coordinator_surface,
        "mode": mode,
        "proof": {
            "invocation_receipt": invocation_proof,
            "duplicate_suppression": duplicate_suppression,
            "identity_evidence_ref": identity_evidence_ref,
            "duplicate_suppression_evidence_ref": duplicate_suppression_evidence_ref,
        },
    }


def prepare_wake_continuation(
    directory: Path,
    task_id: str,
    suspension: dict[str, Any],
    wake_result: dict[str, Any],
) -> dict[str, Any]:
    """Persist one wake-bound continuation only for a proven automatic provider."""

    task_directory = Path(directory).resolve()
    capability_path = task_directory / "continuations" / "capability.json"
    binding_path = task_directory / "continuations" / "runtime-binding.json"
    if not capability_path.is_file() and not binding_path.is_file():
        return {"status": "not_available"}
    if not capability_path.is_file() or not binding_path.is_file():
        raise ContinuationError("continuation capability and runtime binding must be registered together")
    try:
        capability = json.loads(capability_path.read_text(encoding="utf-8"))
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ContinuationError("continuation capability or runtime binding is malformed") from error
    proof = capability.get("proof") if isinstance(capability, dict) else None
    if (
        capability.get("schema_version") != "valp-continuation-capability.v1"
        or capability.get("mode") != "automatic_full"
        or not isinstance(proof, dict)
        or proof.get("invocation_receipt") is not True
        or proof.get("duplicate_suppression") is not True
    ):
        raise ContinuationError("dependency wake cannot target an unproven automatic continuation")
    required_binding = {
        "schema_version", "adapter_id", "provider_id", "coordinator_agent",
        "durable_boundary_ref",
    }
    if (
        not isinstance(binding, dict)
        or set(binding) != required_binding
        or binding.get("schema_version") != "valp-continuation-runtime-binding.v1"
        or binding.get("adapter_id") != capability.get("adapter_id")
        or binding.get("provider_id") != capability.get("provider_id")
        or binding.get("coordinator_agent") != capability.get("coordinator_surface")
        or not all(str(binding.get(field) or "") for field in required_binding - {"schema_version"})
    ):
        raise ContinuationError("continuation runtime binding conflicts with capability identity")
    control_ref = "control-contract.json"
    control_path = task_directory / control_ref
    if not control_path.is_file():
        raise ContinuationError("continuation requires the immutable control contract")
    result_ref = str((suspension.get("accepted_wake") or {}).get("result_ref") or "")
    if not _safe_ref(result_ref) or not (task_directory / result_ref).is_file():
        raise ContinuationError("continuation wake result ref is missing or unsafe")
    payload = {
        "schema_version": "valp-continuation-payload.v1",
        "task_id": task_id,
        "wake_id": wake_result.get("wake_id"),
        "wake_result_ref": result_ref,
        "wake_result_digest": file_digest(task_directory / result_ref),
        "completed_work_item_ids": list(wake_result.get("completed_work_item_ids") or []),
        "pending_work_item_ids": list(wake_result.get("pending_work_item_ids") or []),
        "channel": dict(RUNTIME_CHANNEL),
    }
    accepted = suspension.get("accepted_wake") or {}
    envelope = build_envelope(
        task_id=task_id,
        suspension_id=str(suspension.get("suspension_id") or ""),
        suspension_epoch=int(suspension.get("suspension_epoch")),
        wake_id=str(accepted.get("wake_id") or ""),
        wake_event_id=str(accepted.get("wake_event_id") or ""),
        wake_reason=str(accepted.get("wake_reason") or ""),
        accepted_state_revision=int(accepted.get("resulting_state_revision")),
        control_contract_ref=control_ref,
        control_contract_digest=file_digest(control_path),
        payload=payload,
        coordinator_agent=str(binding["coordinator_agent"]),
        adapter_id=str(binding["adapter_id"]),
        provider_id=str(binding["provider_id"]),
        durable_boundary_ref=str(binding["durable_boundary_ref"]),
    )
    store = ContinuationStore(task_directory, task_id)
    store.pending(envelope, payload)
    return {"status": "pending", "envelope": envelope, "payload": payload}
