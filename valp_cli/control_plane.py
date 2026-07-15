from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROTOCOL_VERSION = "0.3.0-draft"
IMPLEMENTATION_ID = "valp-reference-cli"
INSTALLATION_STATUS = {
    "uninitialized",
    "bootstrapping",
    "discovering_leader_candidates",
    "awaiting_leader_selection",
    "activating_leader",
    "active",
    "reconciling_capabilities",
    "rotating_leader",
    "migrating",
    "rollback_required",
    "degraded",
    "blocked",
    "retired",
}

LEGAL_TRANSITIONS = {
    "uninitialized": {"bootstrapping"},
    "bootstrapping": {"discovering_leader_candidates", "blocked"},
    "discovering_leader_candidates": {"awaiting_leader_selection", "blocked"},
    "awaiting_leader_selection": {"activating_leader", "blocked"},
    "activating_leader": {"active", "blocked"},
    "active": {"reconciling_capabilities", "migrating", "rotating_leader", "degraded", "blocked", "retired"},
    "reconciling_capabilities": {"active", "degraded", "blocked"},
    "rotating_leader": {"active", "blocked"},
    "migrating": {"active", "rollback_required", "blocked"},
    "rollback_required": {"active", "blocked"},
    "degraded": {"reconciling_capabilities", "rotating_leader", "migrating", "blocked", "retired"},
    "blocked": {"active", "retired"},
    "retired": set(),
}

BOOTSTRAP_READ_ONLY_KINDS = {
    "query.bootstrap.hello",
    "query.bootstrap.candidates",
    "command.bootstrap.discover_candidates",
    "result.bootstrap.discovery",
}

BOOTSTRAP_CORE_KINDS = BOOTSTRAP_READ_ONLY_KINDS | {
    "command.installation.init",
    "command.leader.select",
    "event.leader.activated",
}

REQUIRED_FILES = (
    "installation.json",
    "protocol-manifest.json",
    "state.json",
    "leader-selections.jsonl",
    "capability-observations.jsonl",
    "capability-registry.json",
    "messages.jsonl",
    "events.jsonl",
    "claims.jsonl",
    "evidence-manifest.json",
    "failures.jsonl",
    "reviews.jsonl",
)

CONTROL_LOCK_TIMEOUT_SECONDS = 30.0
CONTROL_LOCK_RETRY_SECONDS = 0.05


class ControlPlaneError(RuntimeError):
    def __init__(self, code: str, message: str, *, state_effect: str = "no_state_change") -> None:
        super().__init__(message)
        self.code = code
        self.state_effect = state_effect


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def digest_without(value: dict[str, Any], field: str) -> str:
    payload = dict(value)
    payload.pop(field, None)
    return digest_value(payload)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", f"Cannot read {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", f"Expected an object in {path.name}")
    return value


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", f"Cannot read {path.name}: {exc}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", f"Malformed {path.name}:{line_number}") from exc
        if not isinstance(value, dict):
            raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", f"Non-object record in {path.name}:{line_number}")
        records.append(value)
    return records


def installation_root(workspace: Path, root: Path | None = None) -> Path:
    return (root or (workspace.resolve() / ".valp")).expanduser().resolve()


def safe_control_ref(ref: str) -> str:
    candidate = Path(ref)
    if not ref or candidate.is_absolute() or "\\" in ref or ":" in ref:
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Control evidence refs must be relative POSIX paths")
    parts = ref.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Control evidence refs contain an unsafe segment")
    return ref


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def _safe_nonce() -> str:
    return base64.urlsafe_b64encode(os.urandom(18)).decode("ascii").rstrip("=")


def _empty_state(installation_id: str) -> dict[str, Any]:
    return {
        "schema_version": "valp-executable-state.v1",
        "installation_id": installation_id,
        "revision": 0,
        "status": "uninitialized",
        "active_leader": None,
        "active_leader_epoch": 0,
        "registry_revision": 0,
        "active_blockers": [],
        "last_event_id": None,
        "last_event_digest": None,
        "updated_at": utc_now(),
        "projection_digest": "",
    }


def _state_digest(state: dict[str, Any]) -> str:
    return digest_without(state, "projection_digest")


class InstallationCore:
    """Small deterministic file-backed implementation of the v0.3 core contract."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    @property
    def installation_path(self) -> Path:
        return self.root / "installation.json"

    @property
    def state_path(self) -> Path:
        return self.root / "state.json"

    def _path(self, name: str) -> Path:
        return self.root / name

    @contextmanager
    def _lock(self):
        """Serialize ledger append and projection commits across CLI processes."""
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".control-plane.lock"
        with lock_path.open("a+b") as handle:
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            deadline = time.monotonic() + CONTROL_LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ControlPlaneError("VALP-E-STATE-CONFLICT", "Timed out acquiring control-plane lock")
                    time.sleep(CONTROL_LOCK_RETRY_SECONDS)
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _require_initialized(self) -> None:
        if not self.installation_path.exists() or not self.state_path.exists():
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Installation is not initialized")

    def _installation(self) -> dict[str, Any]:
        self._require_initialized()
        return read_json(self.installation_path)

    def state(self) -> dict[str, Any]:
        self._require_initialized()
        state = read_json(self.state_path)
        if state.get("projection_digest") != _state_digest(state):
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "State projection digest mismatch", state_effect="blocked")
        return state

    def _manifest(self) -> dict[str, Any]:
        return read_json(self._path("protocol-manifest.json"))

    def init(self, *, implementation_id: str = IMPLEMENTATION_ID) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        if self.installation_path.exists():
            return self.status()
        installation_id = _new_id("inst")
        installation = {
            "schema_version": "valp-installation.v1",
            "installation_id": installation_id,
            "control_root": str(self.root),
            "active_protocol_version": PROTOCOL_VERSION,
            "active_leader_epoch": 0,
            "installation_status": "uninitialized",
            "created_at": utc_now(),
            "implementation_id": implementation_id,
        }
        manifest = {
            "schema_version": "valp-protocol-manifest.v1",
            "active_protocol_version": PROTOCOL_VERSION,
            "supported_protocol_read_versions": [PROTOCOL_VERSION, "0.2.0"],
            "supported_protocol_write_versions": [PROTOCOL_VERSION],
            "supported_schema_versions": {
                "installation": ["valp-installation.v1"],
                "state": ["valp-executable-state.v1"],
                "message": ["valp-message.v1"],
                "event": ["valp-event.v1"],
            },
            "required_core_message_kinds": sorted(BOOTSTRAP_READ_ONLY_KINDS | {"command.leader.select", "command.leader.rotate", "command.capabilities.reconcile"}),
            "enabled_extension_namespaces": [],
            "digest_algorithms": ["sha256"],
            "migration_paths": ["0.2.0->0.3.0-draft"],
            "implementation_id": implementation_id,
            "manifest_digest": "",
        }
        manifest["manifest_digest"] = digest_without(manifest, "manifest_digest")
        state = _empty_state(installation_id)
        state["projection_digest"] = _state_digest(state)
        write_json(self.installation_path, installation)
        write_json(self._path("protocol-manifest.json"), manifest)
        write_json(self.state_path, state)
        registry = {
            "schema_version": "valp-capability-registry.v1",
            "installation_id": installation_id,
            "registry_revision": 0,
            "last_observation_sequence": 0,
            "generated_at": utc_now(),
            "active_leader_epoch": 0,
            "entries": {},
            "projection_digest": "",
        }
        registry["projection_digest"] = digest_without(registry, "projection_digest")
        write_json(self._path("capability-registry.json"), registry)
        write_json(self._path("evidence-manifest.json"), {
            "schema_version": "valp-evidence-manifest.v1",
            "installation_id": installation_id,
            "items": [],
        })
        for name in REQUIRED_FILES:
            path = self._path(name)
            if path.suffix == ".jsonl":
                path.touch()
            elif not path.exists():
                write_json(path, {})
        (self.root / "plugins").mkdir(exist_ok=True)
        self._transition(
            event_kind="installation_initialized",
            message_kind="command.installation.init",
            principal_id="bootstrap-controller",
            principal_kind="bootstrap-controller",
            epoch=0,
            expected_revision=0,
            payload={"control_root": str(self.root), "protocol_version": PROTOCOL_VERSION},
            target_status="bootstrapping",
            idempotency_key="installation-init",
        )
        return self.status()

    def _find_message(self, idempotency_key: str) -> dict[str, Any] | None:
        for message in read_jsonl(self._path("messages.jsonl")):
            if message.get("idempotency_key") == idempotency_key:
                return message
        return None

    def _failure(self, error: ControlPlaneError, *, message_id: str | None = None, phase: str = "control_plane") -> None:
        installation = self._installation()
        state = read_json(self.state_path)
        record = {
            "schema_version": "valp-failure.v1",
            "failure_id": _new_id("failure"),
            "error_code": error.code,
            "error_schema_version": "valp-failure.v1",
            "installation_id": installation["installation_id"],
            "phase": phase,
            "accepted_or_rejected_message_id": message_id,
            "state_revision": state.get("revision", 0),
            "leader_epoch": state.get("active_leader_epoch", 0),
            "retriable": error.code == "VALP-E-STATE-CONFLICT",
            "retry_class": "state_conflict" if error.code == "VALP-E-STATE-CONFLICT" else "none",
            "safe_summary": str(error),
            "diagnostic_ref": None,
            "affected_refs": [],
            "deterministic_state_effect": error.state_effect,
            "created_at": utc_now(),
        }
        record["failure_digest"] = digest_without(record, "failure_digest")
        append_jsonl(self._path("failures.jsonl"), record)

    def _validate_epoch(self, state: dict[str, Any], kind: str, epoch: int) -> None:
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise ControlPlaneError("VALP-E-LEADER-EPOCH", "Leader epoch must be an integer")
        if state["active_leader_epoch"] == 0:
            if epoch != 0 or kind not in BOOTSTRAP_CORE_KINDS:
                raise ControlPlaneError("VALP-E-LEADER-EPOCH", "Only bootstrap read-only messages may use epoch 0")
        elif epoch != state["active_leader_epoch"]:
            raise ControlPlaneError("VALP-E-LEADER-EPOCH", "Message uses a fenced leader epoch")

    def _transition(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        with self._lock():
            return self._transition_unlocked(**kwargs)

    def _transition_unlocked(
        self,
        *,
        event_kind: str,
        message_kind: str,
        principal_id: str,
        principal_kind: str,
        epoch: int,
        expected_revision: int,
        payload: dict[str, Any],
        target_status: str,
        idempotency_key: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_initialized()
        existing = self._find_message(idempotency_key)
        if existing is not None:
            if existing.get("content_digest") != digest_value({
                "kind": message_kind,
                "principal_id": principal_id,
                "epoch": epoch,
                "expected_revision": expected_revision,
                "payload": payload,
            }):
                error = ControlPlaneError("VALP-E-IDEMPOTENCY-CONFLICT", "Idempotency key was reused with different content")
                self._failure(error, message_id=existing.get("message_id"))
                raise error
            return existing.get("result") or {"message_id": existing.get("message_id"), "revision": existing.get("result_revision")}
        state = self.state()
        try:
            if expected_revision != state["revision"]:
                raise ControlPlaneError("VALP-E-STATE-CONFLICT", f"Expected revision {expected_revision}, current is {state['revision']}")
            self._validate_epoch(state, message_kind, epoch)
            if target_status not in INSTALLATION_STATUS or target_status not in LEGAL_TRANSITIONS.get(state["status"], set()):
                raise ControlPlaneError("VALP-E-STATE-TRANSITION", f"Illegal installation transition {state['status']} -> {target_status}")
        except ControlPlaneError as error:
            self._failure(error)
            raise

        message_id = _new_id("msg")
        event_id = _new_id("event")
        next_state = dict(state)
        next_state["revision"] = state["revision"] + 1
        next_state["status"] = target_status
        next_state["updated_at"] = utc_now()
        next_state["last_event_id"] = event_id
        next_state["active_leader_epoch"] = state["active_leader_epoch"]
        if "active_leader" in payload:
            next_state["active_leader"] = payload["active_leader"]
        if "active_leader_epoch" in payload:
            next_state["active_leader_epoch"] = payload["active_leader_epoch"]
        if "registry_revision" in payload:
            next_state["registry_revision"] = payload["registry_revision"]
        if target_status == "blocked":
            next_state["active_blockers"] = list(payload.get("active_blockers") or [event_kind])
        elif target_status in {"active", "awaiting_leader_selection", "discovering_leader_candidates"}:
            next_state["active_blockers"] = []
        next_state["projection_digest"] = _state_digest(next_state)
        content_digest = digest_value({
            "kind": message_kind,
            "principal_id": principal_id,
            "epoch": epoch,
            "expected_revision": expected_revision,
            "payload": payload,
        })
        message = {
            "schema_version": "valp-message.v1",
            "message_id": message_id,
            "idempotency_key": idempotency_key,
            "installation_id": state["installation_id"],
            "sender_principal_id": principal_id,
            "sender_kind": principal_kind,
            "leader_epoch": epoch,
            "expected_state_revision": expected_revision,
            "kind": message_kind,
            "payload_schema": "valp-control-payload.v1",
            "payload": payload,
            "content_digest": content_digest,
            "sent_at": utc_now(),
            "accepted": True,
            "installation_sequence": len(read_jsonl(self._path("messages.jsonl"))) + 1,
            "event_id": event_id,
        }
        event = {
            "schema_version": "valp-event.v1",
            "event_id": event_id,
            "installation_sequence": len(read_jsonl(self._path("events.jsonl"))) + 1,
            "installation_id": state["installation_id"],
            "leader_epoch": epoch,
            "task_id": task_id,
            "event_kind": event_kind,
            "accepted_message_id": message_id,
            "prior_revision": state["revision"],
            "new_revision": next_state["revision"],
            "occurred_at": next_state["updated_at"],
            "actor_principal_id": principal_id,
            "payload_schema": "valp-control-payload.v1",
            "payload": dict(payload, state_projection=next_state),
            "prior_event_digest": state.get("last_event_digest"),
        }
        event["event_digest"] = digest_without(event, "event_digest")
        message["result"] = {"message_id": message_id, "event_id": event_id, "revision": next_state["revision"], "status": target_status}
        message["result_revision"] = next_state["revision"]
        message["message_digest"] = digest_without(message, "message_digest")
        append_jsonl(self._path("messages.jsonl"), message)
        append_jsonl(self._path("events.jsonl"), event)
        next_state["last_event_digest"] = event["event_digest"]
        next_state["projection_digest"] = _state_digest(next_state)
        write_json(self.state_path, next_state)
        installation = self._installation()
        installation["installation_status"] = next_state["status"]
        installation["active_leader_epoch"] = next_state["active_leader_epoch"]
        write_json(self.installation_path, installation)
        return message["result"]

    def discover_candidates(self) -> dict[str, Any]:
        state = self.state()
        if state["status"] == "bootstrapping":
            self._transition(
                event_kind="bootstrap_discovery_started",
                message_kind="command.bootstrap.discover_candidates",
                principal_id="bootstrap-controller",
                principal_kind="bootstrap-controller",
                epoch=0,
                expected_revision=state["revision"],
                payload={"read_only": True},
                target_status="discovering_leader_candidates",
                idempotency_key="bootstrap-discovery-start",
            )
            state = self.state()
        if state["status"] != "discovering_leader_candidates":
            raise ControlPlaneError("VALP-E-STATE-TRANSITION", "Candidate discovery requires discovering_leader_candidates")
        candidates = [
            {"principal_id": "manual-user", "principal_kind": "human", "capabilities": ["coordination", "approval", "review"], "presence": "available"},
            {"principal_id": "valp-reference-cli", "principal_kind": "runtime-controller", "capabilities": ["coordination", "state", "audit"], "presence": "local"},
        ]
        write_json(self._path("leader-candidates.json"), {
            "schema_version": "valp-leader-candidates.v1",
            "installation_id": state["installation_id"],
            "epoch": 0,
            "candidates": candidates,
            "generated_at": utc_now(),
        })
        result = self._transition(
            event_kind="leader_candidate_discovery_completed",
            message_kind="result.bootstrap.discovery",
            principal_id="bootstrap-controller",
            principal_kind="bootstrap-controller",
            epoch=0,
            expected_revision=state["revision"],
            payload={"candidate_count": len(candidates), "candidate_ref": "leader-candidates.json"},
            target_status="awaiting_leader_selection",
            idempotency_key="bootstrap-discovery-complete",
        )
        return dict(result, candidates=candidates)

    def select_leader(self, principal_id: str) -> dict[str, Any]:
        state = self.state()
        if state["status"] != "awaiting_leader_selection":
            raise ControlPlaneError("VALP-E-STATE-TRANSITION", "Leader selection requires awaiting_leader_selection")
        candidates = read_json(self._path("leader-candidates.json")).get("candidates") or []
        if principal_id not in {candidate.get("principal_id") for candidate in candidates}:
            raise ControlPlaneError("VALP-E-PERMISSION-DENIED", "Leader must be selected from observed candidates")
        selection = {
            "schema_version": "valp-leader-selection.v1",
            "selection_id": _new_id("selection"),
            "installation_id": state["installation_id"],
            "principal_id": principal_id,
            "principal_kind": next(candidate["principal_kind"] for candidate in candidates if candidate["principal_id"] == principal_id),
            "selected_by": "user",
            "selection_reason": "explicit user selection",
            "approved_at": utc_now(),
            "previous_leader_epoch": 0,
            "new_leader_epoch": 1,
        }
        append_jsonl(self._path("leader-selections.jsonl"), selection)
        activating = self._transition(
            event_kind="leader_selection_approved",
            message_kind="command.leader.select",
            principal_id="user",
            principal_kind="human",
            epoch=0,
            expected_revision=state["revision"],
            payload={"selected_principal_id": principal_id, "selection_id": selection["selection_id"]},
            target_status="activating_leader",
            idempotency_key="leader-selection-" + principal_id,
        )
        state = self.state()
        activated = self._transition(
            event_kind="leader_activated",
            message_kind="event.leader.activated",
            principal_id="bootstrap-controller",
            principal_kind="bootstrap-controller",
            epoch=0,
            expected_revision=state["revision"],
            payload={"active_leader": {"principal_id": principal_id, "principal_kind": selection["principal_kind"]}, "active_leader_epoch": 1},
            target_status="active",
            idempotency_key="leader-activate-1-" + principal_id,
        )
        return {"selection": selection, "activation": activated}

    def rotate_leader(self, principal_id: str) -> dict[str, Any]:
        state = self.state()
        if state["status"] not in {"active", "degraded"}:
            raise ControlPlaneError("VALP-E-STATE-TRANSITION", "Leader rotation requires active or degraded installation")
        if principal_id == (state.get("active_leader") or {}).get("principal_id"):
            raise ControlPlaneError("VALP-E-PERMISSION-DENIED", "Replacement leader must be different")
        candidates = read_json(self._path("leader-candidates.json")).get("candidates") if self._path("leader-candidates.json").exists() else []
        if principal_id not in {candidate.get("principal_id") for candidate in candidates}:
            raise ControlPlaneError("VALP-E-PERMISSION-DENIED", "Replacement leader must have current discovery evidence")
        old_epoch = state["active_leader_epoch"]
        rotating = self._transition(
            event_kind="leader_rotation_approved",
            message_kind="command.leader.rotate",
            principal_id="user",
            principal_kind="human",
            epoch=old_epoch,
            expected_revision=state["revision"],
            payload={"replacement_principal_id": principal_id, "old_epoch": old_epoch},
            target_status="rotating_leader",
            idempotency_key=f"leader-rotate-{old_epoch}-{principal_id}",
        )
        state = self.state()
        new_epoch = old_epoch + 1
        completed = self._transition(
            event_kind="leader_rotation_completed",
            message_kind="event.leader.rotation.completed",
            principal_id="bootstrap-controller",
            principal_kind="bootstrap-controller",
            epoch=old_epoch,
            expected_revision=state["revision"],
            payload={"active_leader": {"principal_id": principal_id, "principal_kind": "runtime-controller"}, "active_leader_epoch": new_epoch, "old_epoch": old_epoch},
            target_status="active",
            idempotency_key=f"leader-rotation-complete-{new_epoch}-{principal_id}",
        )
        return {"rotation": rotating, "completed": completed, "new_epoch": new_epoch}

    def reconcile_capabilities(self, observations: Iterable[dict[str, Any]]) -> dict[str, Any]:
        state = self.state()
        if state["status"] not in {"active", "degraded"}:
            raise ControlPlaneError("VALP-E-STATE-TRANSITION", "Capability reconciliation requires active or degraded installation")
        observations = list(observations)
        if not observations:
            raise ControlPlaneError("VALP-E-CAPABILITY-STALE", "At least one capability observation is required")
        start = self._transition(
            event_kind="capability_reconciliation_started",
            message_kind="command.capabilities.reconcile",
            principal_id=(state.get("active_leader") or {}).get("principal_id", "unknown"),
            principal_kind="installation-leader",
            epoch=state["active_leader_epoch"],
            expected_revision=state["revision"],
            payload={"observation_count": len(observations), "registry_revision": state["registry_revision"]},
            target_status="reconciling_capabilities",
            idempotency_key=f"capability-reconcile-start-{state['revision']}",
        )
        registry = read_json(self._path("capability-registry.json"))
        sequence = int(registry.get("last_observation_sequence") or 0)
        entries = dict(registry.get("entries") or {})
        for observation in observations:
            if observation.get("layer") not in {"official_claim", "local_presence", "live_callable", "task_verified"}:
                raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Unknown capability evidence layer")
            sequence += 1
            record = dict(observation)
            record.update({
                "schema_version": "valp-capability-observation.v1",
                "installation_id": state["installation_id"],
                "observation_id": record.get("observation_id") or _new_id("observation"),
                "registry_sequence": sequence,
                "leader_epoch": state["active_leader_epoch"],
                "observed_at": record.get("observed_at") or utc_now(),
                "source_principal_id": record.get("source_principal_id") or (state.get("active_leader") or {}).get("principal_id", "unknown"),
                "source_kind": record.get("source_kind") or "reference-probe",
                "evidence_refs": list(record.get("evidence_refs") or []),
            })
            record["content_digest"] = digest_without(record, "content_digest")
            append_jsonl(self._path("capability-observations.jsonl"), record)
            subject = str(record.get("subject_id") or "unknown")
            capability = str(record.get("capability_id") or "unknown")
            entry = dict(entries.get(subject + "::" + capability) or {"subject_id": subject, "capability_id": capability, "layers": {}})
            layers = dict(entry.get("layers") or {})
            layers[record["layer"]] = record
            entry["layers"] = layers
            entry["effective_status"] = "pass" if layers.get("live_callable", {}).get("status") == "pass" else record.get("status", "unknown")
            entries[subject + "::" + capability] = entry
        registry.update({
            "registry_revision": int(registry.get("registry_revision") or 0) + 1,
            "last_observation_sequence": sequence,
            "generated_at": utc_now(),
            "active_leader_epoch": state["active_leader_epoch"],
            "entries": entries,
        })
        registry["projection_digest"] = digest_without(registry, "projection_digest")
        write_json(self._path("capability-registry.json"), registry)
        state = self.state()
        finish = self._transition(
            event_kind="capability_reconciliation_completed",
            message_kind="result.capabilities.reconcile",
            principal_id=(state.get("active_leader") or {}).get("principal_id", "unknown"),
            principal_kind="installation-leader",
            epoch=state["active_leader_epoch"],
            expected_revision=state["revision"],
            payload={"registry_revision": registry["registry_revision"], "projection_digest": registry["projection_digest"]},
            target_status="active",
            idempotency_key=f"capability-reconcile-complete-{registry['registry_revision']}",
        )
        return {"start": start, "finish": finish, "registry": registry}

    def add_evidence(
        self,
        content_ref: str,
        *,
        evidence_kind: str,
        producer_principal_id: str,
        collection_method: str = "control-root-file",
        media_type: str = "application/octet-stream",
        redaction_state: str = "not_redacted",
    ) -> dict[str, Any]:
        self._require_initialized()
        ref = safe_control_ref(content_ref)
        path = self.root / ref
        if not path.is_file():
            raise ControlPlaneError("VALP-E-EVIDENCE-MISSING", f"Evidence file does not exist: {ref}")
        content = path.read_bytes()
        manifest = read_json(self._path("evidence-manifest.json"))
        items = list(manifest.get("items") or [])
        item = {
            "evidence_id": _new_id("evidence"),
            "evidence_kind": evidence_kind,
            "content_ref": ref,
            "content_digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            "media_type": media_type,
            "byte_length": len(content),
            "created_at": utc_now(),
            "producer_principal_id": producer_principal_id,
            "collection_method": collection_method,
            "redaction_state": redaction_state,
            "validity_state": "valid",
            "supporting_claim_ids": [],
        }
        items.append(item)
        manifest["items"] = items
        write_json(self._path("evidence-manifest.json"), manifest)
        return item

    def _evidence_by_ref(self, refs: Iterable[str]) -> list[dict[str, Any]]:
        manifest = read_json(self._path("evidence-manifest.json"))
        by_ref = {item.get("content_ref"): item for item in manifest.get("items") or []}
        result: list[dict[str, Any]] = []
        for ref in refs:
            safe_control_ref(ref)
            item = by_ref.get(ref)
            if not item or item.get("validity_state") != "valid":
                raise ControlPlaneError("VALP-E-EVIDENCE-MISSING", f"Evidence is absent or invalid: {ref}")
            path = self.root / ref
            if not path.is_file() or "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest() != item.get("content_digest"):
                raise ControlPlaneError("VALP-E-EVIDENCE-MISSING", f"Evidence digest mismatch: {ref}")
            result.append(item)
        return result

    def declare_claim(
        self,
        *,
        subject_ref: str,
        claim_kind: str,
        predicate: str,
        asserted_value: Any,
        scope: str,
        claimant_principal_id: str,
        evidence_refs: Iterable[str],
        required_evidence_kinds: Iterable[str] = (),
        task_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_initialized()
        ref = safe_control_ref(subject_ref)
        subject_path = self.root / ref
        if not subject_path.is_file():
            raise ControlPlaneError("VALP-E-EVIDENCE-MISSING", f"Claim subject does not exist: {ref}")
        subject_digest = "sha256:" + hashlib.sha256(subject_path.read_bytes()).hexdigest()
        refs = list(evidence_refs)
        evidence = self._evidence_by_ref(refs) if refs else []
        claim = {
            "schema_version": "valp-claim.v1",
            "claim_id": _new_id("claim"),
            "installation_id": self._installation()["installation_id"],
            "task_id": task_id,
            "claimant_principal_id": claimant_principal_id,
            "claim_kind": claim_kind,
            "subject_ref": ref,
            "subject_digest": subject_digest,
            "predicate": predicate,
            "asserted_value": asserted_value,
            "scope": scope,
            "created_at": utc_now(),
            "required_evidence_kinds": list(required_evidence_kinds),
            "evidence_refs": refs,
            "status": "supported" if evidence else "declared",
            "verifier_principal_id": None,
            "review_ref": None,
            "supersedes_claim_id": None,
        }
        claim["claim_digest"] = digest_without(claim, "claim_digest")
        append_jsonl(self._path("claims.jsonl"), claim)
        if evidence:
            manifest = read_json(self._path("evidence-manifest.json"))
            for item in manifest.get("items") or []:
                if item.get("content_ref") in refs and claim["claim_id"] not in item.get("supporting_claim_ids", []):
                    item.setdefault("supporting_claim_ids", []).append(claim["claim_id"])
            write_json(self._path("evidence-manifest.json"), manifest)
        return claim

    def record_review(
        self,
        *,
        claim_id: str,
        reviewer_principal_id: str,
        verdict: str,
        criteria_schema: str = "valp-claim-review.v1",
        criteria_version: str = "1",
        findings: Iterable[dict[str, Any]] = (),
        confidence_limits: Iterable[str] = (),
    ) -> dict[str, Any]:
        if verdict not in {"pass", "fail", "abstain", "blocked"}:
            raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Unknown review verdict")
        claims = [record for record in read_jsonl(self._path("claims.jsonl")) if record.get("claim_id") == claim_id]
        if not claims:
            raise ControlPlaneError("VALP-E-EVIDENCE-MISSING", f"Unknown claim: {claim_id}")
        claim = claims[-1]
        if claim.get("claimant_principal_id") == reviewer_principal_id and claim.get("claim_kind") in {"done", "high_risk", "migration", "plugin_enablement", "stable_release"}:
            raise ControlPlaneError("VALP-E-REVIEW-BLOCKED", "Claimant cannot independently review this claim")
        evidence = self._evidence_by_ref(claim.get("evidence_refs") or []) if claim.get("evidence_refs") else []
        if verdict == "pass" and not evidence:
            raise ControlPlaneError("VALP-E-EVIDENCE-MISSING", "A passing review requires evidence")
        review = {
            "schema_version": "valp-review.v1",
            "review_id": _new_id("review"),
            "reviewer_principal_id": reviewer_principal_id,
            "claim_ids": [claim_id],
            "reviewed_subject_digests": [claim["subject_digest"]],
            "criteria_schema": criteria_schema,
            "criteria_version": criteria_version,
            "required_evidence_refs": list(claim.get("evidence_refs") or []),
            "independence_requirement": "different-principal-for-gate-bearing-claim",
            "risk_class": claim.get("claim_kind", "general"),
            "requested_at": claim.get("created_at"),
            "findings": list(findings),
            "verdict": verdict,
            "confidence_limits": list(confidence_limits),
            "completed_at": utc_now(),
        }
        review["review_digest"] = digest_without(review, "review_digest")
        append_jsonl(self._path("reviews.jsonl"), review)
        verified_claim = dict(claim)
        verified_claim["claim_id"] = _new_id("claim")
        verified_claim["status"] = "verified" if verdict == "pass" else ("rejected" if verdict == "fail" else "blocked")
        verified_claim["verifier_principal_id"] = reviewer_principal_id
        verified_claim["review_ref"] = review["review_id"]
        verified_claim["supersedes_claim_id"] = claim["claim_id"]
        verified_claim["created_at"] = review["completed_at"]
        verified_claim["claim_digest"] = digest_without(verified_claim, "claim_digest")
        append_jsonl(self._path("claims.jsonl"), verified_claim)
        return {"review": review, "claim": verified_claim}

    def hello(self, nonce: str | None = None) -> dict[str, Any]:
        request_nonce = nonce or _safe_nonce()
        if not request_nonce or any(character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for character in request_nonce):
            raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Hello nonce must be canonical base64url")
        manifest = self._manifest()
        return {
            "hello_schema": "valp-hello.v1",
            "kind": "hello.response",
            "nonce": request_nonce,
            "installation_id": self._installation()["installation_id"],
            "implementation_id": manifest["implementation_id"],
            "supported_protocol_read_versions": manifest["supported_protocol_read_versions"],
            "supported_protocol_write_versions": manifest["supported_protocol_write_versions"],
            "manifest_ref": "protocol-manifest.json",
            "manifest_digest": manifest["manifest_digest"],
        }

    def replay(self) -> dict[str, Any]:
        self._require_initialized()
        messages = read_jsonl(self._path("messages.jsonl"))
        message_by_id = {}
        for sequence, message in enumerate(messages, 1):
            if message.get("installation_sequence") != sequence:
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Accepted message sequence has a gap", state_effect="blocked")
            if message.get("message_digest") != digest_without(message, "message_digest"):
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Message digest mismatch", state_effect="blocked")
            message_by_id[message.get("message_id")] = message
        events = read_jsonl(self._path("events.jsonl"))
        previous_digest: str | None = None
        current: dict[str, Any] | None = None
        for sequence, event in enumerate(events, 1):
            if event.get("installation_sequence") != sequence:
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Event sequence has a gap", state_effect="blocked")
            accepted_message = message_by_id.get(event.get("accepted_message_id"))
            if not accepted_message or accepted_message.get("event_id") != event.get("event_id"):
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Event is missing its accepted message", state_effect="blocked")
            if event.get("prior_event_digest") != previous_digest:
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Event chain digest mismatch", state_effect="blocked")
            if event.get("event_digest") != digest_without(event, "event_digest"):
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Event digest mismatch", state_effect="blocked")
            projection = (event.get("payload") or {}).get("state_projection")
            if not isinstance(projection, dict):
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Event has no state projection", state_effect="blocked")
            current = projection
            previous_digest = event["event_digest"]
        if current is None:
            current = _empty_state(self._installation()["installation_id"])
            current["projection_digest"] = _state_digest(current)
        elif current.get("last_event_digest") != previous_digest:
            current["last_event_digest"] = previous_digest
            current["projection_digest"] = _state_digest(current)
        persisted = read_json(self.state_path)
        if persisted.get("projection_digest") != _state_digest(persisted):
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Persisted state digest mismatch", state_effect="blocked")
        if persisted.get("revision") != current.get("revision") or persisted.get("status") != current.get("status"):
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "State projection differs from event replay", state_effect="blocked")
        return current

    def status(self) -> dict[str, Any]:
        state = self.replay()
        return {
            "installation": self._installation(),
            "state": state,
            "root": str(self.root),
            "hello": self.hello(),
        }

    def migrate_plan(self, workspace: Path, target_version: str = PROTOCOL_VERSION) -> dict[str, Any]:
        if target_version != PROTOCOL_VERSION:
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", f"Unsupported target version {target_version}")
        self._require_initialized()
        legacy_root = workspace.resolve() / ".herdr-loop"
        task_files: list[dict[str, Any]] = []
        if legacy_root.exists():
            for path in sorted(legacy_root.rglob("*")):
                if path.is_file():
                    task_files.append({"source_ref": str(path.relative_to(workspace.resolve())), "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
        plan = {
            "schema_version": "valp-migration-plan.v1",
            "migration_id": _new_id("migration"),
            "installation_id": self._installation()["installation_id"],
            "source_protocol_version": "0.2.0",
            "target_protocol_version": target_version,
            "source_root": str(legacy_root),
            "target_root": str(self.root / "legacy"),
            "preserve_original_bytes": True,
            "task_file_count": len(task_files),
            "files": task_files,
            "preconditions": ["source files remain unchanged", "target root is writable", "explicit approval is present"],
            "created_at": utc_now(),
        }
        plan["plan_digest"] = digest_without(plan, "plan_digest")
        write_json(self._path("migration-plan.json"), plan)
        return plan

    def migrate_apply(self, workspace: Path, *, approve: bool = False) -> dict[str, Any]:
        if not approve:
            raise ControlPlaneError("VALP-E-APPROVAL-REQUIRED", "Migration apply requires explicit approval")
        plan = read_json(self._path("migration-plan.json"))
        if plan.get("plan_digest") != digest_without(plan, "plan_digest"):
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration plan digest mismatch")
        state = self.state()
        if state["status"] not in {"active", "degraded"}:
            raise ControlPlaneError("VALP-E-STATE-TRANSITION", "Migration requires an active or degraded installation")
        self._transition(
            event_kind="migration_apply_approved",
            message_kind="command.protocol.migrate",
            principal_id="user",
            principal_kind="human",
            epoch=state["active_leader_epoch"],
            expected_revision=state["revision"],
            payload={"migration_id": plan["migration_id"], "plan_digest": plan["plan_digest"]},
            target_status="migrating",
            idempotency_key="migration-apply-" + plan["migration_id"],
        )
        source = workspace.resolve() / ".herdr-loop"
        target = self.root / "legacy"
        try:
            if source.exists():
                if target.exists():
                    raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration target already exists; refusing overwrite")
                expected_files = {item["source_ref"]: item["digest"] for item in plan.get("files") or []}
                for source_ref, expected_digest in expected_files.items():
                    source_path = workspace.resolve() / source_ref
                    if not source_path.is_file() or "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_digest:
                        raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", f"Legacy source changed: {source_ref}")
                shutil.copytree(source, target)
            receipt = {
                "schema_version": "valp-migration-receipt.v1",
                "migration_id": plan["migration_id"],
                "installation_id": self._installation()["installation_id"],
                "status": "applied",
                "plan_digest": plan["plan_digest"],
                "source_protocol_version": plan["source_protocol_version"],
                "target_protocol_version": plan["target_protocol_version"],
                "source_root": plan["source_root"],
                "target_root": str(target),
                "preserved_file_count": plan["task_file_count"],
                "created_at": utc_now(),
            }
            receipt["receipt_digest"] = digest_without(receipt, "receipt_digest")
            write_json(self._path("migration-receipt.json"), receipt)
            state = self.state()
            self._transition(
                event_kind="migration_activated",
                message_kind="event.protocol.migration.activated",
                principal_id="bootstrap-controller",
                principal_kind="bootstrap-controller",
                epoch=state["active_leader_epoch"],
                expected_revision=state["revision"],
                payload={"migration_id": plan["migration_id"], "receipt_digest": receipt["receipt_digest"]},
                target_status="active",
                idempotency_key="migration-complete-" + plan["migration_id"],
            )
            return receipt
        except Exception as exc:
            receipt = {
                "schema_version": "valp-migration-receipt.v1",
                "migration_id": plan["migration_id"],
                "installation_id": self._installation()["installation_id"],
                "status": "blocked",
                "plan_digest": plan["plan_digest"],
                "source_protocol_version": plan["source_protocol_version"],
                "target_protocol_version": plan["target_protocol_version"],
                "error": str(exc),
                "created_at": utc_now(),
            }
            receipt["receipt_digest"] = digest_without(receipt, "receipt_digest")
            write_json(self._path("migration-receipt.json"), receipt)
            try:
                blocked_state = self.state()
                self._transition(
                    event_kind="migration_unrecoverable",
                    message_kind="event.protocol.migration.blocked",
                    principal_id="bootstrap-controller",
                    principal_kind="bootstrap-controller",
                    epoch=blocked_state["active_leader_epoch"],
                    expected_revision=blocked_state["revision"],
                    payload={"migration_id": plan["migration_id"], "error": str(exc), "active_blockers": ["migration_unrecoverable"]},
                    target_status="blocked",
                    idempotency_key="migration-blocked-" + plan["migration_id"],
                )
            except ControlPlaneError:
                pass
            raise


def load_observations(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("observations")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Observation file must contain an object list")
    return value
