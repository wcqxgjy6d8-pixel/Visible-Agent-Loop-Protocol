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
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable


PROTOCOL_VERSION = "0.3.0"
DRAFT_PROTOCOL_VERSION = "0.3.0-draft"
IMPLEMENTATION_ID = "valp-reference-cli"
SUPPORTED_MIGRATION_PATHS = [
    "0.2.0->0.3.0",
    "0.3.0-draft->0.3.0",
]
DRAFT_MIGRATION_REQUIRED_FIELDS = {
    "source_schema_versions",
    "target_schema_versions",
    "ordered_transforms",
    "legacy_fields",
    "expected_state_revision",
    "expected_registry_revision",
    "expected_leader_epoch",
    "required_capabilities",
    "affected_plugins",
    "affected_tasks",
    "approval_requirements",
    "rollback_strategy",
    "validation_cases",
}
MIGRATION_PLAN_BASE_REQUIRED_FIELDS = {
    "schema_version",
    "migration_id",
    "installation_id",
    "source_protocol_version",
    "target_protocol_version",
    "source_root",
    "target_root",
    "preserve_original_bytes",
    "task_file_count",
    "files",
    "preconditions",
    "created_at",
    "plan_digest",
}
MIGRATION_PLAN_ALLOWED_FIELDS = MIGRATION_PLAN_BASE_REQUIRED_FIELDS | DRAFT_MIGRATION_REQUIRED_FIELDS
DRAFT_MIGRATION_EXCLUDED_ROOTS = {
    ".control-plane.lock",
    "migration-plan.json",
    "migration-receipt.json",
    "migration-snapshots",
    "transition-journal.json",
}
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
    "awaiting_leader_selection": {
        "discovering_leader_candidates",
        "awaiting_leader_start",
        "blocked",
    },
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

TRANSITION_CONTRACTS = {
    "installation_initialized": ("command.installation.init", "bootstrap-controller"),
    "bootstrap_discovery_started": ("command.bootstrap.discover_candidates", "bootstrap-controller"),
    "leader_candidate_discovery_completed": ("result.bootstrap.discovery", "bootstrap-controller"),
    "leader_selection_approved": ("command.leader.select", "human"),
    "leader_start_requested": ("command.leader.start", "bootstrap-controller"),
    "leader_start_recovery_approved": ("command.leader.recover_start", "human"),
    "leader_restart_requested": ("command.leader.restart", "human"),
    "leader_restart_rolled_back": ("result.leader.restart_rolled_back", "runtime-adapter"),
    "leader_activation_failed": ("result.leader.activation_failed", "runtime-adapter"),
    "leader_health_failed": ("result.leader.health_failed", "runtime-adapter"),
    "leader_activated": ("event.leader.activated", "bootstrap-controller"),
    "leader_rotation_approved": ("command.leader.rotate", "human"),
    "emergency_leader_rotation_approved": ("command.leader.rotate_emergency", "human"),
    "leader_rotation_completed": ("event.leader.rotation_completed", "bootstrap-controller"),
    "capability_reconciliation_started": ("command.capabilities.reconcile", "installation-leader"),
    "capability_reconciliation_completed": ("result.capabilities.reconcile", "installation-leader"),
    "migration_apply_approved": ("command.protocol.migrate", "human"),
    "migration_activated": ("event.protocol.migration.activated", "bootstrap-controller"),
    "migration_rolled_back": ("event.protocol.migration.rolled_back", "bootstrap-controller"),
    "migration_unrecoverable": ("event.protocol.migration.blocked", "bootstrap-controller"),
}

TRANSITION_TARGETS = {
    "installation_initialized": "bootstrapping",
    "bootstrap_discovery_started": "discovering_leader_candidates",
    "leader_candidate_discovery_completed": "awaiting_leader_selection",
    "leader_selection_approved": "awaiting_leader_start",
    "leader_start_requested": "activating_leader",
    "leader_start_recovery_approved": "activating_leader",
    "leader_restart_requested": "restarting_leader",
    "leader_restart_rolled_back": "active",
    "leader_activation_failed": "blocked",
    "leader_health_failed": "degraded",
    "leader_activated": "active",
    "leader_rotation_approved": "rotating_leader",
    "emergency_leader_rotation_approved": "rotating_leader",
    "leader_rotation_completed": "active",
    "capability_reconciliation_started": "reconciling_capabilities",
    "capability_reconciliation_completed": "active",
    "migration_apply_approved": "migrating",
    "migration_activated": "active",
    "migration_rolled_back": "active",
    "migration_unrecoverable": "blocked",
}

TRANSITION_PAYLOAD_STATE_FIELDS = {
    "leader_selection_approved": {"selected_leader"},
    "leader_activated": {"active_leader", "active_leader_epoch"},
    "leader_rotation_approved": {"selected_leader"},
    "emergency_leader_rotation_approved": {"selected_leader"},
    "leader_rotation_completed": {"active_leader", "active_leader_epoch"},
    "capability_reconciliation_completed": {"registry_revision"},
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


def validate_transition_contract(
    message: dict[str, Any],
    event: dict[str, Any],
    state_projection: dict[str, Any],
    prior_state: dict[str, Any],
    *,
    allow_legacy_replay: bool = False,
) -> None:
    event_kind = str(event.get("event_kind") or "")
    contract = TRANSITION_CONTRACTS.get(event_kind)
    sender_kind = message.get("sender_kind")
    if contract is None or contract != (message.get("kind"), sender_kind):
        raise ControlPlaneError(
            "VALP-E-REGISTRY-CONSISTENCY",
            "Transition event, message, and principal kind are not a declared contract",
        )
    expected_principal = {
        "bootstrap-controller": "bootstrap-controller",
        "human": "user",
        "runtime-adapter": "reference-runtime-adapter",
    }.get(str(sender_kind))
    if sender_kind == "installation-leader":
        active_leader = state_projection.get("active_leader")
        expected_principal = (
            active_leader.get("principal_id")
            if isinstance(active_leader, dict)
            else None
        )
    if (
        not expected_principal
        or message.get("sender_principal_id") != expected_principal
        or event.get("actor_principal_id") != expected_principal
    ):
        raise ControlPlaneError(
            "VALP-E-REGISTRY-CONSISTENCY",
            "Transition principal does not hold the declared authority",
        )
    target_status = TRANSITION_TARGETS.get(event_kind)
    prior_status = prior_state.get("status")
    payload = message.get("payload")
    legal_transition = target_status in LEGAL_TRANSITIONS.get(str(prior_status), set())
    if (
        allow_legacy_replay
        and event_kind == "leader_restart_requested"
        and prior_status == "blocked"
        and target_status == "restarting_leader"
    ):
        legal_transition = True
    if (
        not isinstance(payload, dict)
        or state_projection.get("status") != target_status
        or not legal_transition
        or event.get("prior_revision") != prior_state.get("revision")
        or state_projection.get("revision") != prior_state.get("revision", -1) + 1
        or state_projection.get("last_event_id") != event.get("event_id")
        or state_projection.get("last_event_digest") != event.get("prior_event_digest")
        or state_projection.get("updated_at") != event.get("occurred_at")
    ):
        raise ControlPlaneError(
            "VALP-E-REGISTRY-CONSISTENCY",
            "Transition source, target, revision, or event binding violates its contract",
        )
    payload_state_fields = set(TRANSITION_PAYLOAD_STATE_FIELDS.get(event_kind, set()))
    if (
        allow_legacy_replay
        and event_kind == "leader_restart_requested"
        and "selected_leader" in payload
    ):
        payload_state_fields.add("selected_leader")
    protected_state_fields = {
        "active_leader",
        "selected_leader",
        "active_leader_epoch",
        "registry_revision",
    }
    asserted_state_fields = (protected_state_fields & set(payload)) - payload_state_fields
    if any(
        payload.get(field) != prior_state.get(field)
        or state_projection.get(field) != prior_state.get(field)
        for field in asserted_state_fields
    ):
        raise ControlPlaneError(
            "VALP-E-REGISTRY-CONSISTENCY",
            "Transition payload attempts an undeclared authority-state mutation",
        )
    if any(
        field not in payload or state_projection.get(field) != payload.get(field)
        for field in payload_state_fields
    ):
        raise ControlPlaneError(
            "VALP-E-REGISTRY-CONSISTENCY",
            "Transition payload does not bind its declared state mutation",
        )
    expected_blockers = prior_state.get("active_blockers")
    if target_status == "blocked":
        expected_blockers = list(payload.get("active_blockers") or [event_kind])
    elif target_status in {
        "active",
        "awaiting_leader_selection",
        "awaiting_leader_start",
        "discovering_leader_candidates",
    }:
        expected_blockers = []
    if state_projection.get("active_blockers") != expected_blockers:
        raise ControlPlaneError(
            "VALP-E-REGISTRY-CONSISTENCY",
            "Transition blocker projection violates its contract",
        )
    allowed_changes = {
        "revision",
        "status",
        "updated_at",
        "last_event_id",
        "last_event_digest",
        "projection_digest",
        "active_blockers",
    } | payload_state_fields
    changed_fields = {
        field
        for field in set(prior_state) | set(state_projection)
        if prior_state.get(field) != state_projection.get(field)
    }
    if changed_fields - allowed_changes:
        raise ControlPlaneError(
            "VALP-E-REGISTRY-CONSISTENCY",
            "Transition projection changes undeclared state fields",
        )


def validate_migration_plan_contract(plan: dict[str, Any]) -> None:
    missing = (
        sorted(DRAFT_MIGRATION_REQUIRED_FIELDS - set(plan))
        if plan.get("source_protocol_version") == DRAFT_PROTOCOL_VERSION
        else []
    )
    if missing:
        raise ControlPlaneError(
            "VALP-E-MIGRATION-UNSUPPORTED",
            "Draft migration plan is missing required fields: " + ", ".join(missing),
        )
    integer_fields = ("expected_state_revision", "expected_registry_revision", "expected_leader_epoch")
    string_list_fields = (
        "ordered_transforms",
        "legacy_fields",
        "required_capabilities",
        "affected_plugins",
        "approval_requirements",
        "validation_cases",
    )
    if (
        any(
            not isinstance(plan.get(field), int)
            or isinstance(plan.get(field), bool)
            or plan.get(field) < 0
            for field in integer_fields
            if field in plan
        )
        or any(
            not isinstance(plan.get(field), list)
            or any(not isinstance(item, str) or not item for item in plan.get(field))
            for field in string_list_fields
            if field in plan
        )
        or any(
            not isinstance(plan.get(field), dict)
            or any(
                not isinstance(key, str)
                or not key
                or (value is not None and not isinstance(value, str))
                for key, value in plan.get(field, {}).items()
            )
            for field in ("source_schema_versions", "target_schema_versions")
            if field in plan
        )
        or ("affected_tasks" in plan and not isinstance(plan.get("affected_tasks"), list))
        or any(
            not isinstance(item, dict)
            or set(item) != {"task_id", "protocol_version", "status", "revision", "handling"}
            or any(not isinstance(item.get(field), str) or not item.get(field) for field in ("task_id", "protocol_version", "status"))
            or not isinstance(item.get("revision"), int)
            or isinstance(item.get("revision"), bool)
            or item.get("revision") < 0
            or item.get("handling") != "legacy-read-only"
            for item in plan.get("affected_tasks", [])
        )
        or (
            "rollback_strategy" in plan
            and (
                not isinstance(plan.get("rollback_strategy"), str)
                or not plan.get("rollback_strategy")
            )
        )
    ):
        raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Draft migration plan contract is malformed")


def validate_migration_plan_artifact(plan: dict[str, Any]) -> None:
    missing = sorted(MIGRATION_PLAN_BASE_REQUIRED_FIELDS - set(plan))
    extra = sorted(set(plan) - MIGRATION_PLAN_ALLOWED_FIELDS)
    string_fields = (
        "migration_id",
        "installation_id",
        "source_protocol_version",
        "target_protocol_version",
        "source_root",
        "target_root",
        "created_at",
    )
    files = plan.get("files")
    preconditions = plan.get("preconditions")
    digest = plan.get("plan_digest")
    migration_id = plan.get("migration_id")
    if (
        missing
        or extra
        or plan.get("schema_version") != "valp-migration-plan.v1"
        or any(not isinstance(plan.get(field), str) or not plan.get(field) for field in string_fields)
        or not isinstance(migration_id, str)
        or migration_id[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
            for character in migration_id
        )
        or plan.get("preserve_original_bytes") is not True
        or not isinstance(plan.get("task_file_count"), int)
        or isinstance(plan.get("task_file_count"), bool)
        or plan.get("task_file_count", -1) < 0
        or not isinstance(files, list)
        or plan.get("task_file_count") != len(files or [])
        or any(
            not isinstance(item, dict)
            or set(item) != {"source_ref", "digest", "bytes"}
            or not isinstance(item.get("source_ref"), str)
            or not item.get("source_ref")
            or not isinstance(item.get("digest"), str)
            or len(item.get("digest")) != 71
            or not item.get("digest").startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in item.get("digest")[7:])
            or not isinstance(item.get("bytes"), int)
            or isinstance(item.get("bytes"), bool)
            or item.get("bytes") < 0
            for item in (files or [])
        )
        or not isinstance(preconditions, list)
        or not preconditions
        or any(not isinstance(item, str) or not item for item in (preconditions or []))
        or not isinstance(digest, str)
        or len(digest) != 71
        or not digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        detail = ""
        if missing:
            detail = "; missing: " + ", ".join(missing)
        if extra:
            detail += "; unsupported: " + ", ".join(extra)
        raise ControlPlaneError(
            "VALP-E-MIGRATION-UNSUPPORTED",
            "Migration plan artifact is malformed" + detail,
        )
    validate_migration_plan_contract(plan)


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


def write_bytes_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_file(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_file(source: str, target: Path) -> None:
    deadline = time.monotonic() + _WINDOWS_REPLACE_TIMEOUT_SECONDS
    while True:
        try:
            os.replace(source, target)
            _sync_directory(target.parent)
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


def append_jsonl_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_bytes() if path.exists() else b""
    if existing and not existing.endswith(b"\n"):
        raise ControlPlaneError(
            "VALP-E-REGISTRY-CONSISTENCY",
            f"Cannot append to truncated {path.name}",
        )
    payload = existing + (
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    write_bytes_atomic(path, payload)


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
    if (
        not ref
        or PurePosixPath(ref).is_absolute()
        or PureWindowsPath(ref).is_absolute()
        or "\\" in ref
        or ":" in ref
    ):
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
            "required_core_message_kinds": sorted(BOOTSTRAP_READ_ONLY_KINDS | {"command.leader.select", "command.leader.start", "command.leader.recover_start", "command.leader.restart", "command.leader.rotate", "command.leader.rotate_emergency", "command.capabilities.reconcile", "result.leader.health_failed"}),
            "enabled_extension_namespaces": [],
            "digest_algorithms": ["sha256"],
            "migration_paths": SUPPORTED_MIGRATION_PATHS,
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
        self._recover_transition_unlocked()
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
            "payload": dict(payload, state_projection=dict(next_state)),
            "prior_event_digest": state.get("last_event_digest"),
        }
        event["event_digest"] = digest_without(event, "event_digest")
        message["result"] = {"message_id": message_id, "event_id": event_id, "revision": next_state["revision"], "status": target_status}
        message["result_revision"] = next_state["revision"]
        message["message_digest"] = digest_without(message, "message_digest")
        next_state["last_event_digest"] = event["event_digest"]
        next_state["projection_digest"] = _state_digest(next_state)
        installation = self._installation()
        installation["installation_status"] = next_state["status"]
        installation["active_leader_epoch"] = next_state["active_leader_epoch"]
        journal = {
            "schema_version": "valp-transition-journal.v1",
            "message": message,
            "event": event,
            "next_state": next_state,
            "installation": installation,
        }
        journal["journal_digest"] = digest_without(journal, "journal_digest")
        write_json(self._path("transition-journal.json"), journal)
        self._commit_transition_journal_unlocked(journal)
        return message["result"]

    def _recover_transition_unlocked(self) -> None:
        path = self._path("transition-journal.json")
        if path.is_file():
            self._commit_transition_journal_unlocked(read_json(path))

    def _commit_transition_journal_unlocked(self, journal: dict[str, Any]) -> None:
        if (
            journal.get("schema_version") != "valp-transition-journal.v1"
            or journal.get("journal_digest") != digest_without(journal, "journal_digest")
        ):
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition journal digest mismatch")
        message = journal.get("message")
        event = journal.get("event")
        next_state = journal.get("next_state")
        installation = journal.get("installation")
        if not all(isinstance(item, dict) for item in (message, event, next_state, installation)):
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition journal payload is malformed")
        prior_revision = event.get("prior_revision")
        new_revision = event.get("new_revision")
        result = message.get("result")
        event_payload = event.get("payload")
        embedded_projection = event_payload.get("state_projection") if isinstance(event_payload, dict) else None
        expected_embedded_projection = dict(next_state)
        expected_embedded_projection["last_event_digest"] = event.get("prior_event_digest")
        expected_embedded_projection["projection_digest"] = _state_digest(expected_embedded_projection)
        message_payload = message.get("payload")
        expected_event_payload = (
            dict(message_payload, state_projection=expected_embedded_projection)
            if isinstance(message_payload, dict)
            else None
        )
        expected_content_digest = digest_value({
            "kind": message.get("kind"),
            "principal_id": message.get("sender_principal_id"),
            "epoch": message.get("leader_epoch"),
            "expected_revision": message.get("expected_state_revision"),
            "payload": message_payload,
        })
        if (
            message.get("message_digest") != digest_without(message, "message_digest")
            or message.get("content_digest") != expected_content_digest
            or event.get("event_digest") != digest_without(event, "event_digest")
            or next_state.get("projection_digest") != _state_digest(next_state)
            or message.get("event_id") != event.get("event_id")
            or event.get("accepted_message_id") != message.get("message_id")
            or event.get("new_revision") != next_state.get("revision")
            or message.get("sender_principal_id") != event.get("actor_principal_id")
            or message.get("leader_epoch") != event.get("leader_epoch")
            or not isinstance(result, dict)
            or result.get("message_id") != message.get("message_id")
            or result.get("event_id") != event.get("event_id")
            or message.get("result_revision") != next_state.get("revision")
            or result.get("revision") != next_state.get("revision")
            or result.get("status") != next_state.get("status")
            or not isinstance(prior_revision, int)
            or isinstance(prior_revision, bool)
            or not isinstance(new_revision, int)
            or isinstance(new_revision, bool)
            or new_revision != prior_revision + 1
            or message.get("expected_state_revision") != prior_revision
            or message.get("installation_id") != event.get("installation_id")
            or next_state.get("installation_id") != event.get("installation_id")
            or installation.get("installation_id") != event.get("installation_id")
            or installation.get("installation_status") != next_state.get("status")
            or installation.get("active_leader_epoch") != next_state.get("active_leader_epoch")
            or event.get("occurred_at") != next_state.get("updated_at")
            or event_payload != expected_event_payload
        ):
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition journal records are inconsistent")

        messages = read_jsonl(self._path("messages.jsonl"))
        matching_messages = [item for item in messages if item.get("message_id") == message["message_id"]]
        if matching_messages and matching_messages != [message]:
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition message conflicts with journal")
        if matching_messages and messages[-1] != message:
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition journal message is not the ledger tail")
        expected_message_sequence = len(messages) if matching_messages else len(messages) + 1
        if message.get("installation_sequence") != expected_message_sequence:
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition message sequence conflicts with ledger")

        events = read_jsonl(self._path("events.jsonl"))
        matching_events = [item for item in events if item.get("event_id") == event["event_id"]]
        if matching_events and matching_events != [event]:
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition event conflicts with journal")
        if matching_events and events[-1] != event:
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition journal event is not the ledger tail")
        expected_event_sequence = len(events) if matching_events else len(events) + 1
        if matching_events:
            expected_prior_event_digest = events[-2].get("event_digest") if len(events) > 1 else None
        else:
            expected_prior_event_digest = events[-1].get("event_digest") if events else None
        if (
            event.get("installation_sequence") != expected_event_sequence
            or event.get("prior_event_digest") != expected_prior_event_digest
        ):
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition event chain conflicts with ledger")

        persisted_state = read_json(self.state_path)
        if persisted_state.get("revision") == event.get("prior_revision"):
            prior_state = persisted_state
        elif persisted_state == next_state and matching_events:
            if len(events) > 1:
                prior_event = events[-2]
                prior_payload = prior_event.get("payload")
                prior_projection = (
                    prior_payload.get("state_projection")
                    if isinstance(prior_payload, dict)
                    else None
                )
                if not isinstance(prior_projection, dict):
                    raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Prior transition projection is missing")
                prior_state = dict(prior_projection)
                prior_state["last_event_digest"] = prior_event.get("event_digest")
                prior_state["projection_digest"] = _state_digest(prior_state)
            else:
                prior_state = _empty_state(str(event.get("installation_id") or ""))
        else:
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition state conflicts with journal")
        validate_transition_contract(message, event, expected_embedded_projection, prior_state)
        if not matching_messages:
            append_jsonl_atomic(self._path("messages.jsonl"), message)
        if not matching_events:
            append_jsonl_atomic(self._path("events.jsonl"), event)

        if persisted_state != next_state:
            write_json(self.state_path, next_state)

        persisted_installation = self._installation()
        if persisted_installation != installation:
            if persisted_installation.get("installation_id") != installation.get("installation_id"):
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Transition installation conflicts with journal")
            write_json(self.installation_path, installation)

        journal_path = self._path("transition-journal.json")
        journal_path.unlink(missing_ok=True)
        _sync_directory(journal_path.parent)

    def discover_candidates(self, passports: Iterable[dict[str, Any]] | None = None) -> dict[str, Any]:
        state = self.state()
        if state["status"] in {"bootstrapping", "awaiting_leader_selection"}:
            refresh = state["status"] == "awaiting_leader_selection"
            self._transition(
                event_kind="bootstrap_discovery_started",
                message_kind="command.bootstrap.discover_candidates",
                principal_id="bootstrap-controller",
                principal_kind="bootstrap-controller",
                epoch=0,
                expected_revision=state["revision"],
                payload={"read_only": True},
                target_status="discovering_leader_candidates",
                idempotency_key=(
                    f"bootstrap-discovery-refresh-{state['revision']}"
                    if refresh
                    else "bootstrap-discovery-start"
                ),
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
            idempotency_key=(
                "bootstrap-discovery-complete"
                if state["revision"] == 2
                else f"bootstrap-discovery-complete-{state['revision']}"
            ),
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
                + f"-r{state['revision']}"
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
            idempotency_key=(
                f"leader-restart-{binding['binding_digest']}-{proposed_epoch}"
                f"-r{state['revision']}"
            ),
        )
        return {
            "selected_leader": selected,
            "prior_binding": binding,
            "proposed_leader_epoch": proposed_epoch,
            "generation": generation,
            "restart": started,
        }

    def restore_active_leader_after_failed_restart(self) -> dict[str, Any]:
        """Restore the prior healthy binding after fail-closed restart provisioning."""
        state = self.state()
        binding = read_json(self._path("leader-session-binding.json"))
        events = read_jsonl(self._path("events.jsonl"))
        latest = events[-1] if events else {}
        payload = latest.get("payload") if isinstance(latest.get("payload"), dict) else {}
        if (
            state["status"] != "blocked"
            or state.get("active_blockers") != ["leader_activation_failed"]
            or latest.get("event_kind") != "leader_activation_failed"
            or payload.get("operation") != "restart"
            or not isinstance(binding, dict)
            or binding.get("status") != "active"
            or binding.get("binding_digest") != digest_without(binding, "binding_digest")
            or binding.get("leader_epoch") != state.get("active_leader_epoch")
        ):
            raise ControlPlaneError(
                "VALP-E-STATE-TRANSITION",
                "Failed Leader restart cannot restore an unproven prior binding",
            )
        return self._transition(
            event_kind="leader_restart_rolled_back",
            message_kind="result.leader.restart_rolled_back",
            principal_id="reference-runtime-adapter",
            principal_kind="runtime-adapter",
            epoch=state["active_leader_epoch"],
            expected_revision=state["revision"],
            payload={"binding_digest": binding["binding_digest"]},
            target_status="active",
            idempotency_key=f"leader-restart-rollback-{latest['event_id']}",
        )

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
                f"{proposed_epoch}-{generation}-r{state['revision']}"
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
            event_kind=(
                "leader_rotation_completed"
                if rotating
                else "leader_activated"
            ),
            message_kind=(
                "event.leader.rotation_completed"
                if rotating
                else "event.leader.activated"
            ),
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
        if state["status"] not in {"active", "degraded"}:
            raise ControlPlaneError("VALP-E-STATE-TRANSITION", "Leader rotation requires an active or degraded installation")
        emergency = state["status"] == "degraded"
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
            event_kind=(
                "emergency_leader_rotation_approved"
                if emergency
                else "leader_rotation_approved"
            ),
            message_kind=(
                "command.leader.rotate_emergency"
                if emergency
                else "command.leader.rotate"
            ),
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
            "emergency": emergency,
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
        if self._path("transition-journal.json").is_file():
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Pending transition journal requires recovery before replay",
                state_effect="blocked",
            )
        messages = read_jsonl(self._path("messages.jsonl"))
        message_by_id = {}
        for sequence, message in enumerate(messages, 1):
            if message.get("installation_sequence") != sequence:
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Accepted message sequence has a gap", state_effect="blocked")
            if message.get("message_digest") != digest_without(message, "message_digest"):
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Message digest mismatch", state_effect="blocked")
            if message.get("message_id") in message_by_id:
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Duplicate accepted message id", state_effect="blocked")
            message_by_id[message.get("message_id")] = message
        events = read_jsonl(self._path("events.jsonl"))
        referenced_messages: set[str] = set()
        event_ids: set[str] = set()
        previous_digest: str | None = None
        previous_revision = 0
        current = _empty_state(self._installation()["installation_id"])
        for sequence, event in enumerate(events, 1):
            if event.get("installation_sequence") != sequence:
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Event sequence has a gap", state_effect="blocked")
            accepted_message = message_by_id.get(event.get("accepted_message_id"))
            if not accepted_message or accepted_message.get("event_id") != event.get("event_id"):
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Event is missing its accepted message", state_effect="blocked")
            if event.get("event_id") in event_ids or event.get("accepted_message_id") in referenced_messages:
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Message and event identity is not one-to-one", state_effect="blocked")
            event_ids.add(event.get("event_id"))
            referenced_messages.add(event.get("accepted_message_id"))
            if event.get("prior_event_digest") != previous_digest:
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Event chain digest mismatch", state_effect="blocked")
            if event.get("event_digest") != digest_without(event, "event_digest"):
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Event digest mismatch", state_effect="blocked")
            event_payload = event.get("payload")
            projection = event_payload.get("state_projection") if isinstance(event_payload, dict) else None
            if not isinstance(projection, dict):
                raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Event has no state projection", state_effect="blocked")
            validate_transition_contract(
                accepted_message,
                event,
                projection,
                current,
                allow_legacy_replay=True,
            )
            message_payload = accepted_message.get("payload")
            expected_event_payload = (
                dict(message_payload, state_projection=projection)
                if isinstance(message_payload, dict)
                else None
            )
            expected_content_digest = digest_value({
                "kind": accepted_message.get("kind"),
                "principal_id": accepted_message.get("sender_principal_id"),
                "epoch": accepted_message.get("leader_epoch"),
                "expected_revision": accepted_message.get("expected_state_revision"),
                "payload": message_payload,
            })
            result = accepted_message.get("result")
            if (
                projection.get("projection_digest") != _state_digest(projection)
                or event.get("prior_revision") != previous_revision
                or event.get("new_revision") != previous_revision + 1
                or projection.get("revision") != event.get("new_revision")
                or projection.get("last_event_id") != event.get("event_id")
                or projection.get("last_event_digest") != event.get("prior_event_digest")
                or projection.get("updated_at") != event.get("occurred_at")
                or projection.get("installation_id") != event.get("installation_id")
                or accepted_message.get("expected_state_revision") != event.get("prior_revision")
                or accepted_message.get("sender_principal_id") != event.get("actor_principal_id")
                or accepted_message.get("leader_epoch") != event.get("leader_epoch")
                or accepted_message.get("content_digest") != expected_content_digest
                or event.get("payload") != expected_event_payload
                or not isinstance(result, dict)
                or result.get("message_id") != accepted_message.get("message_id")
                or result.get("event_id") != event.get("event_id")
                or result.get("revision") != event.get("new_revision")
                or result.get("status") != projection.get("status")
            ):
                raise ControlPlaneError(
                    "VALP-E-REGISTRY-CONSISTENCY",
                    "Event projection or accepted message conflicts with replay",
                    state_effect="blocked",
                )
            current = dict(projection)
            current["last_event_digest"] = event["event_digest"]
            current["projection_digest"] = _state_digest(current)
            previous_digest = event["event_digest"]
            previous_revision = event["new_revision"]
        if referenced_messages != set(message_by_id):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Accepted message ledger contains an orphan record",
                state_effect="blocked",
            )
        if not events:
            current["projection_digest"] = _state_digest(current)
        persisted = read_json(self.state_path)
        if persisted.get("projection_digest") != _state_digest(persisted):
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Persisted state digest mismatch", state_effect="blocked")
        if persisted != current:
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
        with self._lock():
            self._recover_transition_unlocked()
            return self._migrate_plan_unlocked(workspace, target_version=target_version)

    def _draft_control_files(self) -> list[tuple[str, Path]]:
        files: list[tuple[str, Path]] = []
        for path in sorted(self.root.rglob("*")):
            source_ref = path.relative_to(self.root).as_posix()
            if not source_ref or source_ref.split("/", 1)[0] in DRAFT_MIGRATION_EXCLUDED_ROOTS:
                continue
            if path.is_symlink():
                raise ControlPlaneError(
                    "VALP-E-MIGRATION-UNSUPPORTED",
                    f"Draft migration does not follow symlinks: {source_ref}",
                )
            if path.is_file():
                files.append((source_ref, path))
        return files

    def _draft_migration_bindings(self) -> tuple[list[dict[str, Any]], list[str]]:
        affected_tasks = []
        task_root = self.root / "tasks"
        if task_root.is_dir():
            from .task_control import task_state as load_task_state

            for state_path in sorted(task_root.glob("*/task-state.json")):
                task_state = load_task_state(self.root, state_path.parent.name)
                if task_state.get("protocol_version") != DRAFT_PROTOCOL_VERSION:
                    continue
                if task_state.get("status") not in {"done", "failed", "cancelled"}:
                    raise ControlPlaneError(
                        "VALP-E-MIGRATION-UNSUPPORTED",
                        f"Draft task is not quiescent: {task_state.get('task_id')}",
                    )
                affected_tasks.append({
                    "task_id": task_state.get("task_id"),
                    "protocol_version": task_state.get("protocol_version"),
                    "status": task_state.get("status"),
                    "revision": task_state.get("revision"),
                    "handling": "legacy-read-only",
                })
        plugins_root = self.root / "plugins"
        affected_plugins = (
            sorted(path.name for path in plugins_root.iterdir())
            if plugins_root.is_dir()
            else []
        )
        return affected_tasks, affected_plugins

    def _pending_migration_activation(self, plan: dict[str, Any]) -> bool:
        journal_path = self._path("transition-journal.json")
        if not journal_path.is_file():
            return False
        journal = read_json(journal_path)
        event = journal.get("event")
        payload = event.get("payload") if isinstance(event, dict) else None
        if (
            journal.get("journal_digest") != digest_without(journal, "journal_digest")
            or not isinstance(event, dict)
            or event.get("event_kind") != "migration_activated"
            or not isinstance(payload, dict)
            or payload.get("migration_id") != plan.get("migration_id")
        ):
            raise ControlPlaneError(
                "VALP-E-REGISTRY-CONSISTENCY",
                "Pending transition journal does not match migration activation",
            )
        return True

    def stage_migration_plan(self, plan_path: Path) -> dict[str, Any]:
        with self._lock():
            self._recover_transition_unlocked()
            if self.state()["status"] == "migrating":
                raise ControlPlaneError(
                    "VALP-E-STATE-TRANSITION",
                    "Cannot replace the plan for an interrupted migration",
                )
            plan = read_json(plan_path.expanduser().resolve())
            validate_migration_plan_artifact(plan)
            if (
                plan.get("plan_digest") != digest_without(plan, "plan_digest")
                or plan.get("installation_id") != self._installation().get("installation_id")
                or plan.get("target_protocol_version") != PROTOCOL_VERSION
                or plan.get("source_protocol_version") not in {"0.2.0", DRAFT_PROTOCOL_VERSION}
            ):
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "External migration plan is unsupported")
            write_json(self._path("migration-plan.json"), plan)
            return plan

    def _migrate_plan_unlocked(self, workspace: Path, target_version: str = PROTOCOL_VERSION) -> dict[str, Any]:
        if target_version != PROTOCOL_VERSION:
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", f"Unsupported target version {target_version}")
        self._require_initialized()
        installation = self._installation()
        source_version = str(installation.get("active_protocol_version") or "")
        if source_version == DRAFT_PROTOCOL_VERSION:
            migration_id = _new_id("migration")
            source_root = self.root
            target_root = self.root / "migration-snapshots" / migration_id
            files = []
            for source_ref, path in self._draft_control_files():
                files.append({
                    "source_ref": source_ref,
                    "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
                    "bytes": path.stat().st_size,
                })
            affected_tasks, affected_plugins = self._draft_migration_bindings()
            state = self.state()
            plan = {
                "schema_version": "valp-migration-plan.v1",
                "migration_id": migration_id,
                "installation_id": installation["installation_id"],
                "source_protocol_version": source_version,
                "target_protocol_version": target_version,
                "source_root": str(source_root),
                "target_root": str(target_root),
                "preserve_original_bytes": True,
                "task_file_count": len(files),
                "files": files,
                "source_schema_versions": {
                    "installation": installation.get("schema_version"),
                    "protocol_manifest": self._manifest().get("schema_version"),
                },
                "target_schema_versions": {
                    "installation": installation.get("schema_version"),
                    "protocol_manifest": self._manifest().get("schema_version"),
                },
                "ordered_transforms": [
                    "checkpoint complete control root",
                    "promote installation protocol declaration",
                    "promote manifest read and write declarations",
                    "validate manifest digest and control replay",
                    "activate stable installation",
                ],
                "legacy_fields": ["terminal draft tasks remain immutable legacy-read-only"],
                "expected_state_revision": state["revision"],
                "expected_registry_revision": state["registry_revision"],
                "expected_leader_epoch": state["active_leader_epoch"],
                "required_capabilities": ["exclusive_control_lock", "atomic_file_replace", "sha256"],
                "affected_plugins": affected_plugins,
                "affected_tasks": affected_tasks,
                "approval_requirements": ["explicit_user_approval"],
                "rollback_strategy": "restore exact installation and manifest bytes from the complete checkpoint",
                "validation_cases": [
                    "manifest digest",
                    "installation replay",
                    "Leader identity and epoch preservation",
                    "terminal draft tasks remain read-only",
                ],
                "preconditions": [
                    "checkpointed control-root files remain unchanged",
                    "all affected draft tasks are terminal",
                    "cooperative writers are fenced by the installation lock",
                    "migration snapshot target is absent or is the exact interrupted checkpoint",
                    "explicit approval is present",
                ],
                "created_at": utc_now(),
            }
            validate_migration_plan_contract(plan)
            plan["plan_digest"] = digest_without(plan, "plan_digest")
            write_json(self._path("migration-plan.json"), plan)
            return plan

        legacy_root = workspace.resolve() / ".herdr-loop"
        task_files: list[dict[str, Any]] = []
        if legacy_root.is_symlink():
            raise ControlPlaneError(
                "VALP-E-MIGRATION-UNSUPPORTED",
                "Legacy migration source root must not be a symlink",
            )
        if legacy_root.exists():
            for path in sorted(legacy_root.rglob("*")):
                if path.is_symlink():
                    raise ControlPlaneError(
                        "VALP-E-MIGRATION-UNSUPPORTED",
                        f"Legacy migration does not follow symlinks: {path.relative_to(legacy_root)}",
                    )
                if path.is_file():
                    task_files.append({"source_ref": path.relative_to(workspace.resolve()).as_posix(), "digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size})
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
        if (
            plan.get("installation_id") != self._installation().get("installation_id")
            or plan.get("target_protocol_version") != PROTOCOL_VERSION
            or plan.get("source_protocol_version") not in {"0.2.0", DRAFT_PROTOCOL_VERSION}
        ):
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration plan identity or version is unsupported")
        validate_migration_plan_artifact(plan)
        if plan.get("source_protocol_version") == DRAFT_PROTOCOL_VERSION:
            with self._lock():
                return self._migrate_draft_apply_unlocked(plan)
        with self._lock():
            return self._migrate_legacy_apply_unlocked(workspace, plan)

    def _migrate_legacy_apply_unlocked(self, workspace: Path, plan: dict[str, Any]) -> dict[str, Any]:
        self._recover_transition_unlocked()
        source = workspace.resolve() / ".herdr-loop"
        target = self.root / "legacy"
        if source.is_symlink():
            raise ControlPlaneError(
                "VALP-E-MIGRATION-UNSUPPORTED",
                "Legacy migration source root must not be a symlink",
            )
        if Path(str(plan.get("source_root") or "")).resolve() != source.resolve():
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration source root mismatch")
        if Path(str(plan.get("target_root") or "")).resolve() != target.resolve():
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration target root mismatch")
        files = plan.get("files") or []
        prefix = source.relative_to(workspace.resolve()).as_posix() + "/"
        target_inventory: dict[str, dict[str, Any]] = {}
        for item in files:
            source_ref = safe_control_ref(str(item.get("source_ref") or ""))
            if not source_ref.startswith(prefix):
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Legacy source ref is outside its root")
            target_ref = source_ref[len(prefix):]
            if not target_ref or target_ref in target_inventory:
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Legacy migration inventory is inconsistent")
            target_inventory[target_ref] = item
            source_path = workspace.resolve() / source_ref
            expected_digest = str(item.get("digest") or "")
            if (
                not source_path.is_file()
                or source_path.stat().st_size != item.get("bytes")
                or "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest() != expected_digest
            ):
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", f"Migration source changed: {source_ref}")

        actual_source_refs = set()
        if source.is_dir():
            for path in source.rglob("*"):
                source_ref = path.relative_to(source).as_posix()
                if path.is_symlink():
                    raise ControlPlaneError(
                        "VALP-E-MIGRATION-UNSUPPORTED",
                        f"Legacy migration does not follow symlinks: {source_ref}",
                    )
                if path.is_file():
                    actual_source_refs.add(source_ref)
        if actual_source_refs != set(target_inventory):
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Legacy migration source inventory changed")

        def validate_target(base: Path) -> None:
            if not base.is_dir() or base.is_symlink():
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Legacy migration target is missing")
            actual_refs = set()
            for path in base.rglob("*"):
                target_ref = path.relative_to(base).as_posix()
                if path.is_symlink():
                    raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", f"Legacy target contains a symlink: {target_ref}")
                if path.is_file():
                    actual_refs.add(target_ref)
            if actual_refs != set(target_inventory):
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Legacy migration target inventory conflicts with plan")
            for target_ref, item in target_inventory.items():
                payload = (base / target_ref).read_bytes()
                if (
                    len(payload) != item.get("bytes")
                    or "sha256:" + hashlib.sha256(payload).hexdigest() != item.get("digest")
                ):
                    raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", f"Legacy migration target changed: {target_ref}")

        receipt_path = self._path("migration-receipt.json")
        existing_receipt = read_json(receipt_path) if receipt_path.is_file() else None
        if (
            existing_receipt
            and existing_receipt.get("migration_id") == plan.get("migration_id")
            and existing_receipt.get("plan_digest") == plan.get("plan_digest")
            and existing_receipt.get("status") == "applied"
        ):
            if existing_receipt.get("receipt_digest") != digest_without(existing_receipt, "receipt_digest"):
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration receipt digest mismatch")
            if (
                existing_receipt.get("installation_id") != plan.get("installation_id")
                or existing_receipt.get("source_protocol_version") != plan.get("source_protocol_version")
                or existing_receipt.get("target_protocol_version") != plan.get("target_protocol_version")
                or Path(str(existing_receipt.get("source_root") or "")).resolve()
                != Path(str(plan.get("source_root") or "")).resolve()
                or Path(str(existing_receipt.get("target_root") or "")).resolve() != target.resolve()
                or existing_receipt.get("preserved_file_count") != plan.get("task_file_count")
            ):
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration receipt conflicts with plan")
            validate_target(target)
            state = self.state()
            if state["status"] == "migrating":
                self._transition_unlocked(
                    event_kind="migration_activated",
                    message_kind="event.protocol.migration.activated",
                    principal_id="bootstrap-controller",
                    principal_kind="bootstrap-controller",
                    epoch=state["active_leader_epoch"],
                    expected_revision=state["revision"],
                    payload={"migration_id": plan["migration_id"], "receipt_digest": existing_receipt["receipt_digest"]},
                    target_status="active",
                    idempotency_key="migration-complete-" + plan["migration_id"],
                )
            elif state["status"] != "active":
                raise ControlPlaneError("VALP-E-STATE-TRANSITION", "Applied migration receipt conflicts with installation state")
            return existing_receipt

        staging_parent = self.root / "migration-snapshots"
        if staging_parent.is_symlink():
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Legacy migration staging root is unsafe")
        staging_parent.mkdir(parents=True, exist_ok=True)
        staged = staging_parent / f"legacy-{plan['migration_id']}.staging"
        if staged.parent.resolve() != staging_parent.resolve():
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Legacy migration staging path escapes its root")

        state = self.state()
        resuming = state["status"] == "migrating"
        if state["status"] in {"active", "degraded"}:
            if target.exists() or staged.exists() or staged.is_symlink():
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration target already exists; refusing overwrite")
            self._transition_unlocked(
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
        elif state["status"] != "migrating":
            raise ControlPlaneError("VALP-E-STATE-TRANSITION", "Migration requires an active or matching interrupted installation")

        try:
            if target.exists():
                validate_target(target)
            else:
                if staged.is_symlink():
                    raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Legacy migration staging path is unsafe")
                if staged.exists():
                    if not resuming:
                        raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Legacy migration staging target already exists")
                    shutil.rmtree(staged)
                if source.is_dir():
                    shutil.copytree(source, staged)
                else:
                    staged.mkdir(parents=True)
                validate_target(staged)
                os.replace(staged, target)
                _sync_directory(target.parent)
            validate_target(target)
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
            write_json(receipt_path, receipt)
            state = self.state()
            self._transition_unlocked(
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
            if self._pending_migration_activation(plan):
                raise
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
            write_json(receipt_path, receipt)
            try:
                blocked_state = self.state()
                self._transition_unlocked(
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

    def _migrate_draft_apply_unlocked(self, plan: dict[str, Any]) -> dict[str, Any]:
        self._recover_transition_unlocked()
        current_plan = read_json(self._path("migration-plan.json"))
        if current_plan != plan or plan.get("plan_digest") != digest_without(plan, "plan_digest"):
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration plan changed before apply")

        snapshot_root = self.root / "migration-snapshots"
        target = snapshot_root / str(plan["migration_id"])
        if snapshot_root.is_symlink() or target.is_symlink():
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration snapshot path is unsafe")
        if (
            snapshot_root.parent.resolve() != self.root.resolve()
            or target.parent.resolve() != snapshot_root.resolve()
        ):
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration snapshot target escapes its root")
        if Path(str(plan.get("source_root") or "")).resolve() != self.root.resolve():
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration source root mismatch")
        if Path(str(plan.get("target_root") or "")).resolve() != target.resolve():
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration target root mismatch")

        files = plan.get("files") or []
        if not isinstance(files, list) or plan.get("task_file_count") != len(files):
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration file inventory is inconsistent")
        inventory = {safe_control_ref(str(item.get("source_ref") or "")): item for item in files}
        required_refs = {"installation.json", "protocol-manifest.json", "state.json"}
        if not required_refs.issubset(inventory) or len(inventory) != len(files):
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Draft migration inventory is unsupported")
        current_refs = {source_ref for source_ref, _ in self._draft_control_files()}
        if current_refs != set(inventory):
            raise ControlPlaneError(
                "VALP-E-MIGRATION-UNSUPPORTED",
                "Draft migration control-root inventory changed",
            )
        affected_tasks, affected_plugins = self._draft_migration_bindings()
        if (
            plan.get("affected_tasks") != affected_tasks
            or plan.get("affected_plugins") != affected_plugins
        ):
            raise ControlPlaneError(
                "VALP-E-MIGRATION-UNSUPPORTED",
                "Draft migration task or plugin bindings changed",
            )

        def checked_bytes(base: Path, *, exact_inventory: bool = False) -> dict[str, bytes]:
            if base.is_symlink():
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration checkpoint root is a symlink")
            if exact_inventory:
                actual_refs = set()
                for path in sorted(base.rglob("*")):
                    source_ref = path.relative_to(base).as_posix()
                    if path.is_symlink():
                        raise ControlPlaneError(
                            "VALP-E-MIGRATION-UNSUPPORTED",
                            f"Migration snapshot contains a symlink: {source_ref}",
                        )
                    if path.is_file():
                        actual_refs.add(source_ref)
                if actual_refs != set(inventory):
                    raise ControlPlaneError(
                        "VALP-E-MIGRATION-UNSUPPORTED",
                        "Migration snapshot inventory conflicts with plan",
                    )
            result: dict[str, bytes] = {}
            for source_ref, item in inventory.items():
                path = base / source_ref
                if not path.is_file():
                    raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", f"Migration source changed: {source_ref}")
                payload = path.read_bytes()
                expected_digest = str(item.get("digest") or "")
                if len(payload) != item.get("bytes") or "sha256:" + hashlib.sha256(payload).hexdigest() != expected_digest:
                    raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", f"Migration source changed: {source_ref}")
                result[source_ref] = payload
            return result

        def validate_checkpoint(state: dict[str, Any]) -> dict[str, bytes]:
            snapshot_bytes = checked_bytes(target, exact_inventory=True)
            mutable_refs = {
                "installation.json",
                "protocol-manifest.json",
                "state.json",
                "messages.jsonl",
                "events.jsonl",
            }
            for source_ref, payload in snapshot_bytes.items():
                if source_ref in mutable_refs:
                    continue
                current_path = self.root / source_ref
                if current_path.is_symlink() or not current_path.is_file() or current_path.read_bytes() != payload:
                    raise ControlPlaneError(
                        "VALP-E-MIGRATION-UNSUPPORTED",
                        f"Checkpointed migration source changed: {source_ref}",
                    )
            try:
                checkpoint_state = json.loads(snapshot_bytes["state.json"])
            except (KeyError, json.JSONDecodeError) as exc:
                raise ControlPlaneError(
                    "VALP-E-MIGRATION-UNSUPPORTED",
                    "Migration checkpoint state is malformed",
                ) from exc
            for field in (
                "installation_id",
                "registry_revision",
                "active_leader_epoch",
                "selected_leader",
                "active_leader",
            ):
                if state.get(field) != checkpoint_state.get(field):
                    raise ControlPlaneError(
                        "VALP-E-MIGRATION-UNSUPPORTED",
                        f"Migration changed checkpointed authority field: {field}",
                    )
            if (
                state.get("active_leader_epoch") != plan.get("expected_leader_epoch")
                or state.get("registry_revision") != plan.get("expected_registry_revision")
            ):
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration authority checkpoint changed")
            self.replay()
            return snapshot_bytes

        def validate_stable_files(snapshot_bytes: dict[str, bytes]) -> None:
            try:
                original_installation = json.loads(snapshot_bytes["installation.json"])
                original_manifest = json.loads(snapshot_bytes["protocol-manifest.json"])
                installation = self._installation()
                manifest = self._manifest()
            except (ControlPlaneError, KeyError, json.JSONDecodeError) as exc:
                raise ControlPlaneError(
                    "VALP-E-MIGRATION-UNSUPPORTED",
                    "Stable migration documents are malformed",
                ) from exc
            expected_installation = dict(original_installation)
            expected_installation["active_protocol_version"] = PROTOCOL_VERSION
            expected_manifest = dict(original_manifest)
            expected_manifest["active_protocol_version"] = PROTOCOL_VERSION
            expected_manifest["supported_protocol_read_versions"] = [PROTOCOL_VERSION, "0.2.0"]
            expected_manifest["supported_protocol_write_versions"] = [PROTOCOL_VERSION]
            expected_manifest["migration_paths"] = SUPPORTED_MIGRATION_PATHS
            expected_manifest["manifest_digest"] = digest_without(expected_manifest, "manifest_digest")
            if installation != expected_installation or manifest != expected_manifest:
                raise ControlPlaneError(
                    "VALP-E-MIGRATION-UNSUPPORTED",
                    "Stable migration documents differ from the checkpointed transform",
                )

        state = self.state()
        if state["status"] not in {"active", "migrating"}:
            raise ControlPlaneError(
                "VALP-E-STATE-TRANSITION",
                "Draft migration requires an active installation or a matching interrupted migration",
            )
        receipt_path = self._path("migration-receipt.json")
        existing_receipt = read_json(receipt_path) if receipt_path.is_file() else None
        matching_applied_receipt = bool(
            existing_receipt
            and existing_receipt.get("migration_id") == plan.get("migration_id")
            and existing_receipt.get("plan_digest") == plan.get("plan_digest")
            and existing_receipt.get("status") == "applied"
        )
        if matching_applied_receipt:
            if existing_receipt.get("receipt_digest") != digest_without(existing_receipt, "receipt_digest"):
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration receipt digest mismatch")
            if (
                existing_receipt.get("installation_id") != plan.get("installation_id")
                or existing_receipt.get("source_protocol_version") != plan.get("source_protocol_version")
                or existing_receipt.get("target_protocol_version") != plan.get("target_protocol_version")
                or Path(str(existing_receipt.get("source_root") or "")).resolve()
                != Path(str(plan.get("source_root") or "")).resolve()
                or Path(str(existing_receipt.get("target_root") or "")).resolve()
                != target.resolve()
                or existing_receipt.get("preserved_file_count") != plan.get("task_file_count")
            ):
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration receipt conflicts with plan")
            snapshot_bytes = validate_checkpoint(state)
            validate_stable_files(snapshot_bytes)
            if state["status"] == "migrating":
                self._transition_unlocked(
                    event_kind="migration_activated",
                    message_kind="event.protocol.migration.activated",
                    principal_id="bootstrap-controller",
                    principal_kind="bootstrap-controller",
                    epoch=state["active_leader_epoch"],
                    expected_revision=state["revision"],
                    payload={"migration_id": plan["migration_id"], "receipt_digest": existing_receipt["receipt_digest"]},
                    target_status="active",
                    idempotency_key="migration-complete-" + plan["migration_id"],
                )
            return existing_receipt

        if state["status"] == "active" and (
            state["revision"] != plan.get("expected_state_revision")
            or state["registry_revision"] != plan.get("expected_registry_revision")
            or state["active_leader_epoch"] != plan.get("expected_leader_epoch")
        ):
            raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration checkpoint revision changed")

        if state["status"] == "active":
            original_bytes = checked_bytes(self.root)
            if target.exists():
                if checked_bytes(target, exact_inventory=True) != original_bytes:
                    raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Migration snapshot conflicts with source")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                staged_target = Path(tempfile.mkdtemp(prefix=f".{plan['migration_id']}.", dir=target.parent))
                for source_ref, payload in original_bytes.items():
                    write_bytes_atomic(staged_target / source_ref, payload)
                checked_bytes(staged_target, exact_inventory=True)
                os.replace(staged_target, target)
                _sync_directory(target.parent)
            self._transition_unlocked(
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
        else:
            original_bytes = validate_checkpoint(state)

        try:
            original_installation = json.loads(original_bytes["installation.json"])
            original_manifest = json.loads(original_bytes["protocol-manifest.json"])
            if (
                not isinstance(original_installation, dict)
                or not isinstance(original_manifest, dict)
                or original_installation.get("installation_id") != plan["installation_id"]
                or original_installation.get("active_protocol_version") != DRAFT_PROTOCOL_VERSION
                or original_manifest.get("active_protocol_version") != DRAFT_PROTOCOL_VERSION
                or original_manifest.get("manifest_digest") != digest_without(original_manifest, "manifest_digest")
            ):
                raise ControlPlaneError("VALP-E-MIGRATION-UNSUPPORTED", "Draft migration snapshot is inconsistent")

            installation = dict(original_installation)
            installation["active_protocol_version"] = PROTOCOL_VERSION
            write_json(self.installation_path, installation)
            manifest = dict(original_manifest)
            manifest["active_protocol_version"] = PROTOCOL_VERSION
            manifest["supported_protocol_read_versions"] = [PROTOCOL_VERSION, "0.2.0"]
            manifest["supported_protocol_write_versions"] = [PROTOCOL_VERSION]
            manifest["migration_paths"] = SUPPORTED_MIGRATION_PATHS
            manifest["manifest_digest"] = digest_without(manifest, "manifest_digest")
            write_json(self._path("protocol-manifest.json"), manifest)

            receipt = {
                "schema_version": "valp-migration-receipt.v1",
                "migration_id": plan["migration_id"],
                "installation_id": plan["installation_id"],
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
            write_json(receipt_path, receipt)
            active_state = self.state()
            self._transition_unlocked(
                event_kind="migration_activated",
                message_kind="event.protocol.migration.activated",
                principal_id="bootstrap-controller",
                principal_kind="bootstrap-controller",
                epoch=active_state["active_leader_epoch"],
                expected_revision=active_state["revision"],
                payload={"migration_id": plan["migration_id"], "receipt_digest": receipt["receipt_digest"]},
                target_status="active",
                idempotency_key="migration-complete-" + plan["migration_id"],
            )
            return receipt
        except Exception as exc:
            if self._pending_migration_activation(plan):
                raise
            rolled_back = False
            try:
                write_bytes_atomic(self.installation_path, original_bytes["installation.json"])
                write_bytes_atomic(self._path("protocol-manifest.json"), original_bytes["protocol-manifest.json"])
                rollback_state = self.state()
                if rollback_state["status"] == "migrating":
                    self._transition_unlocked(
                        event_kind="migration_rolled_back",
                        message_kind="event.protocol.migration.rolled_back",
                        principal_id="bootstrap-controller",
                        principal_kind="bootstrap-controller",
                        epoch=rollback_state["active_leader_epoch"],
                        expected_revision=rollback_state["revision"],
                        payload={"migration_id": plan["migration_id"], "error": str(exc)},
                        target_status="active",
                        idempotency_key="migration-rollback-" + plan["migration_id"],
                    )
                rolled_back = True
            except Exception:
                rolled_back = False
            rollback_receipt = {
                "schema_version": "valp-migration-receipt.v1",
                "migration_id": plan["migration_id"],
                "installation_id": plan["installation_id"],
                "status": "rolled_back" if rolled_back else "blocked",
                "plan_digest": plan["plan_digest"],
                "source_protocol_version": plan["source_protocol_version"],
                "target_protocol_version": plan["target_protocol_version"],
                "error": str(exc),
                "created_at": utc_now(),
            }
            rollback_receipt["receipt_digest"] = digest_without(rollback_receipt, "receipt_digest")
            write_json(receipt_path, rollback_receipt)
            if not rolled_back:
                try:
                    blocked_state = self.state()
                    self._transition_unlocked(
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
