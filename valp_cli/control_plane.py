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
_WINDOWS_REPLACE_RETRY_SECONDS = 0.01
_WINDOWS_REPLACE_TIMEOUT_SECONDS = 1.0
INSTALLATION_STATUS = {
    "uninitialized",
    "bootstrapping",
    "discovering_leader_candidates",
    "awaiting_leader_selection",
    "awaiting_leader_start",
    "activating_leader",
    "active",
    "restarting_leader",
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
    "awaiting_leader_selection": {"awaiting_leader_start", "blocked"},
    "awaiting_leader_start": {"activating_leader", "blocked"},
    "activating_leader": {"active", "blocked"},
    "active": {"restarting_leader", "reconciling_capabilities", "migrating", "rotating_leader", "degraded", "blocked", "retired"},
    "restarting_leader": {"active", "blocked"},
    "reconciling_capabilities": {"active", "degraded", "blocked"},
    "rotating_leader": {"active", "blocked"},
    "migrating": {"active", "rollback_required", "blocked"},
    "rollback_required": {"active", "blocked"},
    "degraded": {"reconciling_capabilities", "rotating_leader", "migrating", "blocked", "retired"},
    "blocked": {"activating_leader", "active", "retired"},
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
    "command.leader.start",
    "command.leader.recover_start",
    "event.leader.activated",
    "result.leader.activation_failed",
}

REQUIRED_FILES = (
    "installation.json",
    "protocol-manifest.json",
    "state.json",
    "leader-selections.jsonl",
    "leader-session-receipts.jsonl",
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
        _replace_file(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _replace_file(source: str, target: Path) -> None:
    deadline = time.monotonic() + _WINDOWS_REPLACE_TIMEOUT_SECONDS
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32} or time.monotonic() >= deadline:
                raise
            time.sleep(_WINDOWS_REPLACE_RETRY_SECONDS)


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


def leader_installation_root(workspace: Path, root: Path | None = None) -> Path:
    """Resolve the installation Leader from any caller workspace.

    A workspace-local control root remains authoritative when it already
    exists. Otherwise an initialized user-level root is reused so opening the
    Leader from another terminal does not create a second installation.
    """
    if root is not None:
        return installation_root(workspace, root)
    workspace_root = installation_root(workspace)
    if (workspace_root / "installation.json").is_file():
        return workspace_root
    configured_root = os.environ.get("VALP_CONTROL_ROOT", "").strip()
    if configured_root:
        return Path(configured_root).expanduser().resolve()
    user_root = (Path.home() / ".valp").expanduser().resolve()
    if (user_root / "installation.json").is_file():
        return user_root
    return workspace_root


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
        "selected_leader": None,
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


def _authority_view(state: dict[str, Any]) -> dict[str, Any]:
    """Hide legacy attachment hints from the installation authority view."""
    active = state.get("active_leader")
    if not isinstance(active, dict) or "session_id" not in active:
        return state
    normalized = dict(state)
    normalized["active_leader"] = {
        key: value for key, value in active.items() if key != "session_id"
    }
    return normalized


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
        return _authority_view(state)

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
            "required_core_message_kinds": sorted(BOOTSTRAP_READ_ONLY_KINDS | {"command.leader.select", "command.leader.start", "command.leader.recover_start", "command.leader.restart", "command.leader.rotate", "command.capabilities.reconcile"}),
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
        if "selected_leader" in payload:
            next_state["selected_leader"] = payload["selected_leader"]
        if "active_leader_epoch" in payload:
            next_state["active_leader_epoch"] = payload["active_leader_epoch"]
        if "registry_revision" in payload:
            next_state["registry_revision"] = payload["registry_revision"]
        if target_status == "blocked":
            next_state["active_blockers"] = list(payload.get("active_blockers") or [event_kind])
        elif target_status in {"active", "awaiting_leader_selection", "awaiting_leader_start", "discovering_leader_candidates"}:
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

    def discover_candidates(self, passports: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
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
        candidates: list[dict[str, Any]] = []
        passport_directory = self._path("capability-passports")
        passport_directory.mkdir(parents=True, exist_ok=True)
        for passport in passports or []:
            if not isinstance(passport, dict):
                continue
            principal_id = str(passport.get("principal_id") or "").strip()
            runtime = passport.get("runtime") if isinstance(passport.get("runtime"), dict) else {}
            launch_argv = runtime.get("launch_argv")
            roles = passport.get("role_eligibility") if isinstance(passport.get("role_eligibility"), dict) else {}
            live = passport.get("live_callability") if isinstance(passport.get("live_callability"), dict) else {}
            session = ((passport.get("runtime_identity") or {}).get("session") or {}) if isinstance(passport.get("runtime_identity"), dict) else {}
            if (
                not principal_id
                or roles.get("leader") != "eligible"
                or live.get("status") not in {"pass", "warn"}
                or session.get("status") != "known"
                or not isinstance(launch_argv, list)
                or not launch_argv
                or not all(isinstance(item, str) and item.strip() for item in launch_argv)
                or not str(runtime.get("adapter_id") or "").strip()
                or not str(runtime.get("adapter_class") or "").strip()
            ):
                continue
            passport_digest = digest_value(passport)
            passport_ref = f"capability-passports/{passport_digest.removeprefix('sha256:')}.json"
            write_json(self._path(passport_ref), passport)
            capabilities = sorted(
                role
                for role, eligibility in roles.items()
                if eligibility == "eligible"
            ) or ["leader"]
            candidates.append({
                "principal_id": principal_id,
                "principal_kind": "agent",
                "agent_id": str(passport.get("agent_id") or principal_id),
                "agent_surface": str(passport.get("agent_surface") or "unknown"),
                "capabilities": capabilities,
                "presence": str(live.get("status") or "unknown"),
                "passport_ref": passport_ref,
                "passport_digest": passport_digest,
                "observed_session": {
                    "session_id": str((passport.get("runtime_identity") or {}).get("session_id") or "unknown"),
                    "token": str(session.get("token") or "unknown"),
                    "generation": str(session.get("generation") or "unknown"),
                },
                "runtime": {
                    "adapter_id": str(runtime["adapter_id"]),
                    "adapter_class": str(runtime["adapter_class"]),
                    "launch_argv": [str(item) for item in launch_argv],
                    "version_command": [
                        str(item)
                        for item in runtime.get("version_command") or []
                        if isinstance(item, str) and item.strip()
                    ],
                },
            })
        candidates.sort(key=lambda candidate: candidate["principal_id"])
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
        candidate = next(candidate for candidate in candidates if candidate["principal_id"] == principal_id)
        selection = {
            "schema_version": "valp-leader-selection.v1",
            "selection_id": _new_id("selection"),
            "installation_id": state["installation_id"],
            "principal_id": principal_id,
            "principal_kind": candidate["principal_kind"],
            "selected_by": "user",
            "selection_reason": "explicit user selection",
            "approved_at": utc_now(),
            "previous_leader_epoch": 0,
            "proposed_leader_epoch": 1,
            "passport_ref": candidate["passport_ref"],
            "passport_digest": candidate["passport_digest"],
        }
        append_jsonl(self._path("leader-selections.jsonl"), selection)
        recorded = self._transition(
            event_kind="leader_selection_approved",
            message_kind="command.leader.select",
            principal_id="user",
            principal_kind="human",
            epoch=0,
            expected_revision=state["revision"],
            payload={
                "selected_principal_id": principal_id,
                "selection_id": selection["selection_id"],
                "selected_leader": {
                    "principal_id": principal_id,
                    "principal_kind": candidate["principal_kind"],
                    "agent_id": candidate["agent_id"],
                    "agent_surface": candidate["agent_surface"],
                    "selection_id": selection["selection_id"],
                    "passport_ref": candidate["passport_ref"],
                    "passport_digest": candidate["passport_digest"],
                    "runtime": candidate["runtime"],
                },
            },
            target_status="awaiting_leader_start",
            idempotency_key="leader-selection-" + principal_id,
        )
        return {"selection": selection, "recording": recorded}

    def prepare_leader_start(self) -> dict[str, Any]:
        state = self.state()
        if state["status"] != "awaiting_leader_start":
            raise ControlPlaneError(
                "VALP-E-STATE-TRANSITION",
                "Leader start requires an inactive selected Leader",
            )
        selected = state.get("selected_leader")
        if not isinstance(selected, dict) or not selected.get("principal_id"):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader start requires persisted selection evidence",
            )
        proposed_epoch = state["active_leader_epoch"] + 1
        started = self._transition(
            event_kind="leader_start_requested",
            message_kind="command.leader.start",
            principal_id="bootstrap-controller",
            principal_kind="bootstrap-controller",
            epoch=0,
            expected_revision=state["revision"],
            payload={
                "selected_principal_id": selected["principal_id"],
                "selection_id": selected["selection_id"],
                "proposed_leader_epoch": proposed_epoch,
            },
            target_status="activating_leader",
            idempotency_key=f"leader-start-{selected['selection_id']}-{proposed_epoch}",
        )
        return {
            "selected_leader": selected,
            "proposed_leader_epoch": proposed_epoch,
            "start": started,
        }

    def prepare_leader_start_recovery(
        self,
        session_id: str,
        *,
        approve: bool = False,
    ) -> dict[str, Any]:
        if not approve:
            raise ControlPlaneError(
                "VALP-E-APPROVAL-REQUIRED",
                "Leader start recovery requires explicit user approval",
            )
        approved_session_id = str(session_id or "").strip()
        if not approved_session_id or approved_session_id != session_id:
            raise ControlPlaneError(
                "VALP-E-MESSAGE-SCHEMA",
                "Leader start recovery requires one exact runtime session id",
            )
        state = self.state()
        binding_path = self._path("leader-session-binding.json")
        selected = state.get("selected_leader")
        if (
            state["status"] != "blocked"
            or state["active_leader_epoch"] != 0
            or state.get("active_leader") is not None
            or binding_path.exists()
            or state.get("active_blockers") != ["leader_activation_failed"]
            or not isinstance(selected, dict)
        ):
            raise ControlPlaneError(
                "VALP-E-STATE-TRANSITION",
                "Leader start recovery requires the exact blocked first-start state",
            )

        selections = read_jsonl(self._path("leader-selections.jsonl"))
        selection = selections[-1] if selections else None
        if (
            not isinstance(selection, dict)
            or selection.get("installation_id") != state["installation_id"]
            or selection.get("selection_id") != selected.get("selection_id")
            or selection.get("principal_id") != selected.get("principal_id")
            or selection.get("passport_ref") != selected.get("passport_ref")
            or selection.get("passport_digest") != selected.get("passport_digest")
            or selection.get("previous_leader_epoch") != 0
            or selection.get("proposed_leader_epoch") != 1
        ):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader start recovery selection evidence changed or is incomplete",
                state_effect="blocked",
            )
        passport_ref = safe_control_ref(str(selected.get("passport_ref") or ""))
        passport = read_json(self._path(passport_ref))
        if digest_value(passport) != selected.get("passport_digest"):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader start recovery passport digest mismatch",
                state_effect="blocked",
            )

        events = read_jsonl(self._path("events.jsonl"))
        blocking_event = events[-1] if events else None
        if (
            not isinstance(blocking_event, dict)
            or blocking_event.get("event_id") != state.get("last_event_id")
            or blocking_event.get("event_digest") != state.get("last_event_digest")
            or blocking_event.get("event_digest") != digest_without(blocking_event, "event_digest")
            or blocking_event.get("event_kind") != "leader_activation_failed"
            or blocking_event.get("leader_epoch") != 0
        ):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader start recovery blocking event is missing or invalid",
                state_effect="blocked",
            )

        receipts = read_jsonl(self._path("leader-session-receipts.jsonl"))
        failed_receipt = receipts[-1] if receipts else None
        if (
            not isinstance(failed_receipt, dict)
            or failed_receipt.get("receipt_digest")
            != digest_without(failed_receipt, "receipt_digest")
            or failed_receipt.get("schema_version")
            != "valp-leader-session-receipt.v1"
            or failed_receipt.get("receipt_type")
            != "leader_session_start_failed"
            or failed_receipt.get("installation_id") != state["installation_id"]
            or failed_receipt.get("principal_id") != selected.get("principal_id")
            or failed_receipt.get("leader_epoch") != 1
            or failed_receipt.get("generation") != 1
            or failed_receipt.get("operation") != "start"
            or failed_receipt.get("adapter_id")
            != (selected.get("runtime") or {}).get("adapter_id")
            or failed_receipt.get("blocking_event_id")
            != blocking_event.get("event_id")
        ):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader start recovery requires the latest exact blocking failed-start receipt",
                state_effect="blocked",
            )
        event_payload = blocking_event.get("payload") or {}
        if (
            event_payload.get("operation") != "start"
            or event_payload.get("selected_principal_id") != selected.get("principal_id")
            or event_payload.get("proposed_leader_epoch") != 1
            or event_payload.get("proposed_generation") != 1
        ):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader start recovery blocking event does not match the failed receipt",
                state_effect="blocked",
            )

        approval_payload = {
            "approved_session_id": approved_session_id,
            "selected_principal_id": selected["principal_id"],
            "selection_id": selected["selection_id"],
            "passport_digest": selected["passport_digest"],
            "failed_receipt_id": failed_receipt["receipt_id"],
            "failed_receipt_digest": failed_receipt["receipt_digest"],
            "blocking_event_id": blocking_event["event_id"],
            "proposed_leader_epoch": 1,
            "proposed_generation": 1,
        }
        approved = self._transition(
            event_kind="leader_start_recovery_approved",
            message_kind="command.leader.recover_start",
            principal_id="user",
            principal_kind="human",
            epoch=0,
            expected_revision=state["revision"],
            payload=approval_payload,
            target_status="activating_leader",
            idempotency_key=(
                "leader-start-recovery-"
                + failed_receipt["receipt_digest"].removeprefix("sha256:")
                + "-"
                + hashlib.sha256(approved_session_id.encode("utf-8")).hexdigest()
            ),
        )
        recovery_approval = {
            "approval_event_id": approved["event_id"],
            "approved_session_id": approved_session_id,
            "failed_receipt_id": failed_receipt["receipt_id"],
            "failed_receipt_digest": failed_receipt["receipt_digest"],
        }
        return {
            "selected_leader": selected,
            "proposed_leader_epoch": 1,
            "generation": 1,
            "recovery_approval": recovery_approval,
            "recovery": approved,
        }

    def prepare_leader_restart(self) -> dict[str, Any]:
        state = self.state()
        if state["status"] != "active" or state["active_leader_epoch"] < 1:
            raise ControlPlaneError(
                "VALP-E-STATE-TRANSITION",
                "Leader restart requires one active Leader authority and runtime attachment",
            )
        selected = state.get("selected_leader")
        binding_path = self._path("leader-session-binding.json")
        binding = read_json(binding_path) if binding_path.exists() else None
        if (
            not isinstance(selected, dict)
            or not isinstance(binding, dict)
            or binding.get("binding_digest") != digest_without(binding, "binding_digest")
            or binding.get("status") != "active"
            or binding.get("leader_epoch") != state["active_leader_epoch"]
            or binding.get("principal_id") != selected.get("principal_id")
        ):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader restart requires the exact healthy active binding",
                state_effect="blocked",
            )
        proposed_epoch = state["active_leader_epoch"] + 1
        generation = int(binding["generation"]) + 1
        started = self._transition(
            event_kind="leader_restart_requested",
            message_kind="command.leader.restart",
            principal_id="user",
            principal_kind="human",
            epoch=state["active_leader_epoch"],
            expected_revision=state["revision"],
            payload={
                "selected_principal_id": selected["principal_id"],
                "selection_id": selected["selection_id"],
                "prior_binding_digest": binding["binding_digest"],
                "proposed_leader_epoch": proposed_epoch,
                "proposed_generation": generation,
            },
            target_status="restarting_leader",
            idempotency_key=f"leader-restart-{binding['binding_digest']}-{proposed_epoch}",
        )
        return {
            "selected_leader": selected,
            "prior_binding": binding,
            "proposed_leader_epoch": proposed_epoch,
            "generation": generation,
            "restart": started,
        }

    def fail_leader_activation(
        self,
        operation: str,
        *,
        adapter_id: str,
        failure_class: str,
    ) -> None:
        state = self.state()
        expected_status = {
            "start": "activating_leader",
            "recover-start": "activating_leader",
            "restart": "restarting_leader",
            "rotate": "rotating_leader",
        }.get(operation)
        if expected_status is None or state["status"] != expected_status:
            raise ControlPlaneError(
                "VALP-E-STATE-TRANSITION",
                "Leader activation failure requires a prepared start, recovery, restart, or rotation",
            )
        selected = state.get("selected_leader")
        if not isinstance(selected, dict) or not selected.get("principal_id"):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader activation failure is missing selected Leader evidence",
                state_effect="blocked",
            )
        prior_binding = None
        if state["active_leader_epoch"] >= 1:
            binding_path = self._path("leader-session-binding.json")
            prior_binding = read_json(binding_path) if binding_path.exists() else None
        recovery_approval = None
        if operation == "recover-start":
            events = read_jsonl(self._path("events.jsonl"))
            approval_event = events[-1] if events else None
            approval_payload = (
                approval_event.get("payload")
                if isinstance(approval_event, dict)
                and isinstance(approval_event.get("payload"), dict)
                else {}
            )
            if (
                not isinstance(approval_event, dict)
                or approval_event.get("event_id") != state.get("last_event_id")
                or approval_event.get("event_digest") != state.get("last_event_digest")
                or approval_event.get("event_kind")
                != "leader_start_recovery_approved"
            ):
                raise ControlPlaneError(
                    "VALP-E-REGISTRY-CONSISTENCY",
                    "Leader recovery failure is missing its approval event",
                    state_effect="blocked",
                )
            recovery_approval = {
                "approval_event_id": approval_event["event_id"],
                "approved_session_id": approval_payload.get("approved_session_id"),
                "failed_receipt_id": approval_payload.get("failed_receipt_id"),
                "failed_receipt_digest": approval_payload.get(
                    "failed_receipt_digest"
                ),
            }
        generation = int((prior_binding or {}).get("generation") or 0) + 1
        proposed_epoch = state["active_leader_epoch"] + 1
        failure = ControlPlaneError(
            "VALP-E-LEADER-UNREACHABLE",
            "The selected runtime adapter could not provision the Leader session",
            state_effect="blocked",
        )
        blocked = self._transition(
            event_kind="leader_activation_failed",
            message_kind="result.leader.activation_failed",
            principal_id="reference-runtime-adapter",
            principal_kind="runtime-adapter",
            epoch=state["active_leader_epoch"],
            expected_revision=state["revision"],
            payload={
                "selected_principal_id": selected["principal_id"],
                "operation": operation,
                "adapter_id": adapter_id,
                "failure_code": failure.code,
                "failure_class": failure_class,
                "proposed_leader_epoch": proposed_epoch,
                "proposed_generation": generation,
                "active_blockers": ["leader_activation_failed"],
                **(
                    {"recovery": recovery_approval}
                    if recovery_approval is not None
                    else {}
                ),
            },
            target_status="blocked",
            idempotency_key=(
                f"leader-activation-failed-{operation}-{selected['selection_id']}-"
                f"{proposed_epoch}-{generation}"
            ),
        )
        receipt = {
            "schema_version": "valp-leader-session-receipt.v1",
            "receipt_id": _new_id("leader-receipt"),
            "receipt_type": "leader_session_start_failed",
            "installation_id": state["installation_id"],
            "principal_id": selected["principal_id"],
            "leader_epoch": proposed_epoch,
            "generation": generation,
            "operation": operation,
            "adapter_id": adapter_id,
            "failure_code": failure.code,
            "failure_class": failure_class,
            "failure_message": str(failure),
            "blocking_event_id": blocked["event_id"],
            "recorded_at": utc_now(),
            **(
                {"recovery": recovery_approval}
                if recovery_approval is not None
                else {}
            ),
        }
        receipt["receipt_digest"] = digest_without(receipt, "receipt_digest")
        append_jsonl(self._path("leader-session-receipts.jsonl"), receipt)
        self._failure(
            failure,
            message_id=blocked["message_id"],
            phase="leader_activation",
        )
        raise failure

    def activate_leader(self, provisioned: dict[str, Any]) -> dict[str, Any]:
        state = self.state()
        first_start = state["status"] == "activating_leader" and state["active_leader_epoch"] == 0
        restarting = state["status"] == "restarting_leader" and state["active_leader_epoch"] >= 1
        rotating = state["status"] == "rotating_leader" and state["active_leader_epoch"] >= 1
        events = read_jsonl(self._path("events.jsonl"))
        prepared_event = events[-1] if events else None
        if (
            not isinstance(prepared_event, dict)
            or prepared_event.get("event_id") != state.get("last_event_id")
            or prepared_event.get("event_digest") != state.get("last_event_digest")
            or prepared_event.get("event_digest")
            != digest_without(prepared_event, "event_digest")
        ):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader activation prepared event is missing or invalid",
                state_effect="blocked",
            )
        recovering = (
            first_start
            and prepared_event.get("event_kind")
            == "leader_start_recovery_approved"
        )
        operation = (
            "recover-start"
            if recovering
            else "start"
            if first_start
            else "restart"
            if restarting
            else "rotate"
        )
        if not first_start and not restarting and not rotating:
            raise ControlPlaneError(
                "VALP-E-STATE-TRANSITION",
                "Leader activation requires a prepared start or fenced restart",
            )
        selected = state.get("selected_leader")
        if not isinstance(selected, dict):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader activation is missing selected Leader evidence",
            )
        installation = self._installation()
        expected_launch = ((selected.get("runtime") or {}).get("launch_argv") or [])
        runtime_identity = provisioned.get("runtime_identity") if isinstance(provisioned.get("runtime_identity"), dict) else {}
        health = provisioned.get("health") if isinstance(provisioned.get("health"), dict) else {}
        ownership = provisioned.get("ownership") if isinstance(provisioned.get("ownership"), dict) else {}
        runtime_scope = provisioned.get("runtime_scope") if isinstance(provisioned.get("runtime_scope"), dict) else {}
        context = provisioned.get("context") if isinstance(provisioned.get("context"), dict) else {}
        launch = provisioned.get("launch") if isinstance(provisioned.get("launch"), dict) else {}
        recovery = provisioned.get("recovery") if isinstance(provisioned.get("recovery"), dict) else None
        generation = provisioned.get("generation")
        prior_binding = None
        if restarting or rotating:
            prior_binding = read_json(self._path("leader-session-binding.json"))
            if prior_binding.get("binding_digest") != digest_without(prior_binding, "binding_digest"):
                raise ControlPlaneError(
                    "VALP-E-REGISTRY-CONSISTENCY",
                    "Prior Leader binding digest mismatch",
                    state_effect="blocked",
                )
        leader_epoch = state["active_leader_epoch"] + 1
        expected_generation = 1 if first_start else int((prior_binding or {}).get("generation") or 0) + 1
        validation_errors: list[str] = []
        if provisioned.get("principal_id") != selected.get("principal_id"):
            validation_errors.append("principal identity mismatch")
        if provisioned.get("adapter_id") != (selected.get("runtime") or {}).get("adapter_id"):
            validation_errors.append("adapter id mismatch")
        if provisioned.get("adapter_class") != (selected.get("runtime") or {}).get("adapter_class"):
            validation_errors.append("adapter class mismatch")
        if ownership != {
            "scope": "installation",
            "installation_id": installation["installation_id"],
        }:
            validation_errors.append("ownership is not installation-scoped")
        if runtime_scope.get("ownership") != "installation":
            validation_errors.append("runtime scope is not installation-owned")
        if launch.get("argv") != expected_launch:
            validation_errors.append("launch argv differs from selected passport")
        if provisioned.get("focused_at_provisioning") is not False:
            validation_errors.append("Leader session is focused or focus is unproven")
        if type(generation) is not int or generation != expected_generation:
            validation_errors.append(f"Leader session generation must be {expected_generation}")
        if health.get("status") != "pass" or not health.get("observed_at"):
            validation_errors.append("Leader health proof did not pass")
        if not str(context.get("cwd") or "").strip():
            validation_errors.append("Leader context is missing")
        if not str(runtime_identity.get("session_id") or "").strip():
            validation_errors.append("Leader runtime session id is missing")
        if not str(runtime_identity.get("token") or "").startswith("sha256:"):
            validation_errors.append("Leader runtime identity token is missing")
        expected_recovery = None
        if recovering:
            prepared_payload = prepared_event.get("payload") or {}
            expected_recovery = {
                "approval_event_id": prepared_event["event_id"],
                "approved_session_id": prepared_payload.get("approved_session_id"),
                "failed_receipt_id": prepared_payload.get("failed_receipt_id"),
                "failed_receipt_digest": prepared_payload.get("failed_receipt_digest"),
            }
            if recovery != expected_recovery:
                validation_errors.append("Leader recovery approval evidence mismatch")
            if runtime_identity.get("session_id") != expected_recovery["approved_session_id"]:
                validation_errors.append("Leader recovery returned a different runtime session")
        elif recovery is not None:
            validation_errors.append("Unexpected Leader recovery evidence")
        if validation_errors:
            self.fail_leader_activation(
                operation,
                adapter_id=str(provisioned.get("adapter_id") or "unknown"),
                failure_class="LeaderSessionBindingValidationError",
            )
            raise AssertionError("Invalid Leader session binding must fail closed")

        launch_argv_digest = digest_value(expected_launch)
        binding = {
            "schema_version": "valp-leader-session-binding.v1",
            "installation_id": installation["installation_id"],
            "principal_id": selected["principal_id"],
            "principal_kind": selected["principal_kind"],
            "agent_id": str(provisioned.get("agent_id") or selected["principal_id"]),
            "selection_id": selected["selection_id"],
            "passport_ref": selected["passport_ref"],
            "passport_digest": selected["passport_digest"],
            "leader_epoch": leader_epoch,
            "adapter_id": provisioned["adapter_id"],
            "adapter_class": provisioned["adapter_class"],
            "generation": generation,
            "ownership": ownership,
            "context": context,
            "launch": {"argv": expected_launch, "argv_digest": launch_argv_digest},
            "focused_at_provisioning": False,
            "runtime_scope": runtime_scope,
            "runtime_identity": runtime_identity,
            "health": health,
            "status": "active",
            "provisioned_at": provisioned["provisioned_at"],
            "activated_at": utc_now(),
            "replaces_binding_digest": (prior_binding or {}).get("binding_digest"),
            "binding_digest": "",
        }
        if expected_recovery is not None:
            binding["recovery"] = expected_recovery
        binding["binding_digest"] = digest_without(binding, "binding_digest")
        write_json(self._path("leader-session-binding.json"), binding)
        history_ref = (
            f"leader-session-bindings/epoch-{leader_epoch}-generation-{generation}-"
            f"{binding['binding_digest'].removeprefix('sha256:')[:16]}.json"
        )
        write_json(self._path(history_ref), binding)

        def append_receipt(receipt_type: str, **extra: Any) -> dict[str, Any]:
            receipt = {
                "schema_version": "valp-leader-session-receipt.v1",
                "receipt_id": _new_id("leader-receipt"),
                "receipt_type": receipt_type,
                "installation_id": installation["installation_id"],
                "principal_id": selected["principal_id"],
                "leader_epoch": leader_epoch,
                "generation": generation,
                "binding_ref": "leader-session-binding.json",
                "binding_digest": binding["binding_digest"],
                "runtime_session_id": runtime_identity["session_id"],
                "recorded_at": utc_now(),
                **extra,
            }
            receipt["receipt_digest"] = digest_without(receipt, "receipt_digest")
            append_jsonl(self._path("leader-session-receipts.jsonl"), receipt)
            return receipt

        provisioned_receipt = append_receipt(
            "leader_session_provisioned",
            health_status=health["status"],
            binding_history_ref=history_ref,
            **({"recovery": expected_recovery} if expected_recovery is not None else {}),
        )
        replaced_receipt = None
        if prior_binding is not None:
            replaced_receipt = append_receipt(
                "leader_session_replaced",
                replaced_binding_digest=prior_binding["binding_digest"],
                replaced_epoch=prior_binding["leader_epoch"],
                replaced_generation=prior_binding["generation"],
            )
        activated = self._transition(
            event_kind="leader_activated",
            message_kind="event.leader.activated",
            principal_id="bootstrap-controller",
            principal_kind="bootstrap-controller",
            epoch=0 if first_start else state["active_leader_epoch"],
            expected_revision=state["revision"],
            payload={
                "active_leader": {
                    "principal_id": selected["principal_id"],
                    "principal_kind": selected["principal_kind"],
                    "binding_ref": "leader-session-binding.json",
                    "binding_digest": binding["binding_digest"],
                    "generation": generation,
                },
                "active_leader_epoch": leader_epoch,
                **({"recovery": expected_recovery} if expected_recovery is not None else {}),
            },
            target_status="active",
            idempotency_key=f"leader-activate-{leader_epoch}-{selected['selection_id']}",
        )
        activated_receipt = append_receipt(
            "leader_session_activated",
            activation_event_id=activated["event_id"],
            **({"recovery": expected_recovery} if expected_recovery is not None else {}),
        )
        return {
            "binding": binding,
            "provisioned_receipt": provisioned_receipt,
            "replaced_receipt": replaced_receipt,
            "activation": activated,
            "activated_receipt": activated_receipt,
        }

    def rotate_leader(self, principal_id: str) -> dict[str, Any]:
        state = self.state()
        if state["status"] != "active":
            raise ControlPlaneError("VALP-E-STATE-TRANSITION", "Leader rotation requires an active installation")
        if principal_id == (state.get("active_leader") or {}).get("principal_id"):
            raise ControlPlaneError("VALP-E-PERMISSION-DENIED", "Replacement leader must be different")
        candidates = read_json(self._path("leader-candidates.json")).get("candidates") if self._path("leader-candidates.json").exists() else []
        if principal_id not in {candidate.get("principal_id") for candidate in candidates}:
            raise ControlPlaneError("VALP-E-PERMISSION-DENIED", "Replacement leader must have current discovery evidence")
        candidate = next(candidate for candidate in candidates if candidate["principal_id"] == principal_id)
        prior_binding = read_json(self._path("leader-session-binding.json"))
        if (
            prior_binding.get("binding_digest") != digest_without(prior_binding, "binding_digest")
            or prior_binding.get("leader_epoch") != state["active_leader_epoch"]
        ):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Leader rotation requires the exact active binding",
                state_effect="blocked",
            )
        old_epoch = state["active_leader_epoch"]
        new_epoch = old_epoch + 1
        selection = {
            "schema_version": "valp-leader-selection.v1",
            "selection_id": _new_id("selection"),
            "installation_id": state["installation_id"],
            "principal_id": principal_id,
            "principal_kind": candidate["principal_kind"],
            "selected_by": "user",
            "selection_reason": "explicit user-approved Leader rotation",
            "approved_at": utc_now(),
            "previous_leader_epoch": old_epoch,
            "proposed_leader_epoch": new_epoch,
            "passport_ref": candidate["passport_ref"],
            "passport_digest": candidate["passport_digest"],
        }
        append_jsonl(self._path("leader-selections.jsonl"), selection)
        selected_leader = {
            "principal_id": principal_id,
            "principal_kind": candidate["principal_kind"],
            "agent_id": candidate["agent_id"],
            "agent_surface": candidate["agent_surface"],
            "selection_id": selection["selection_id"],
            "passport_ref": candidate["passport_ref"],
            "passport_digest": candidate["passport_digest"],
            "runtime": candidate["runtime"],
        }
        rotating = self._transition(
            event_kind="leader_rotation_approved",
            message_kind="command.leader.rotate",
            principal_id="user",
            principal_kind="human",
            epoch=old_epoch,
            expected_revision=state["revision"],
            payload={
                "replacement_principal_id": principal_id,
                "old_epoch": old_epoch,
                "selected_leader": selected_leader,
                "selection_id": selection["selection_id"],
                "prior_binding_digest": prior_binding["binding_digest"],
                "proposed_leader_epoch": new_epoch,
            },
            target_status="rotating_leader",
            idempotency_key=f"leader-rotate-{old_epoch}-{principal_id}",
        )
        return {
            "selection": selection,
            "selected_leader": selected_leader,
            "prior_binding": prior_binding,
            "rotation": rotating,
            "proposed_leader_epoch": new_epoch,
            "generation": int(prior_binding["generation"]) + 1,
        }

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
        state = _authority_view(self.replay())
        leader_session = None
        binding_path = self._path("leader-session-binding.json")
        if binding_path.exists():
            leader_session = read_json(binding_path)
            if leader_session.get("binding_digest") != digest_without(
                leader_session,
                "binding_digest",
            ):
                raise ControlPlaneError(
                    "VALP-E-REGISTRY-CONSISTENCY",
                    "Leader session binding digest mismatch",
                    state_effect="blocked",
                )
            active = state.get("active_leader") if isinstance(state.get("active_leader"), dict) else {}
            if state.get("status") == "active" and (
                active.get("binding_digest") != leader_session.get("binding_digest")
                or state.get("active_leader_epoch") != leader_session.get("leader_epoch")
            ):
                raise ControlPlaneError(
                    "VALP-E-REGISTRY-CONSISTENCY",
                    "Active Leader authority conflicts with its runtime attachment record",
                    state_effect="blocked",
                )
        return {
            "installation": self._installation(),
            "state": state,
            "leader_session": leader_session,
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
