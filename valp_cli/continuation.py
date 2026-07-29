"""Provisional provider-neutral continuation kernel.

VALP owns immutable correlation, event ordering, and fail-closed storage. A
provider adapter must supply real invocation identity and duplicate-suppression
evidence before provider-consumption events can be committed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

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


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


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
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != encoded:
                raise ContinuationError(f"immutable artifact conflict: {path}")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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

    def consume(self, envelope: dict[str, Any], invoke: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        self.claim(envelope)
        with self._locked():
            self._persisted(envelope)
            prior = next((event for event in self._events() if event.get("event") == "resume_consumed" and self._matches(event, envelope)), None)
            receipt_path = self._artifact_dir(envelope) / "invocation-receipt.json"
            if prior:
                if not receipt_path.is_file():
                    raise ContinuationError("committed invocation receipt is missing")
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                if digest(receipt) != prior.get("invocation_receipt_digest"):
                    raise ContinuationError("committed invocation receipt changed")
                return receipt
            capability = self._capability(envelope)
            marker = self._artifact_dir(envelope) / "invocation.inflight"
            try:
                fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(idempotency_key(envelope) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError as exc:
                raise ContinuationError("continuation invocation is already in flight or indeterminate") from exc
        try:
            receipt = invoke()
        except Exception as exc:
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
            self._persisted(envelope)
            _write_once(receipt_path, validated)
            receipt_ref = receipt_path.relative_to(self.directory).as_posix()
            receipt_digest = digest(validated)
            invocation_id = validated["provider"]["invocation_id"]
            self._append("continuation_started", envelope, invocation_id=invocation_id, invocation_receipt_ref=receipt_ref, invocation_receipt_digest=receipt_digest)
            self._append("resume_consumed", envelope, invocation_id=invocation_id, invocation_receipt_ref=receipt_ref, invocation_receipt_digest=receipt_digest)
            marker.unlink(missing_ok=True)
        return validated

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
