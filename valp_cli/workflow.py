from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .control_contract import (
    CONTROL_CONTRACT_REF,
    build_control_contract,
    build_control_slice,
    control_contract_digest,
    validate_control_contract,
    validate_control_slice,
)
from .cost_governance import enforce_cost_budget
from .delegation import build_delegation_policy, validate_delegation_policy
from .herdr_adapter import (
    DONE_SESSION_REPROVISION_REQUIRED,
    HERDR_PANE_LIST_STDOUT_LIMIT,
    HerdrSubmissionError,
    binding_has_verified_bootstrap_lifecycle,
    describe_herdr_submission,
    detect_herdr_session_provisioning_capability,
    detect_herdr_submission_capability,
    opaque_process_generation,
    observe_herdr_terminal,
    provision_herdr_agent_session,
    submit_herdr_dispatch,
)
from .model_identity import (
    bounded_observation_ttl,
    model_awareness_for,
    model_evidence_score,
    model_identity_for,
    model_selection_for,
    observation_freshness,
)
from .risk import classify_approval_risks
from .submission import (
    INVALID_EVIDENCE_STATUSES,
    build_submission_dependencies,
    deterministic_receipt_ledger_errors,
    has_concrete_runtime_submission_proof,
    role_expected_refs,
    roles_for_agent,
    unmet_dependencies_for_phases,
    validate_submission_dependencies,
    work_item_identity,
)


PROFILE_RULES = [
    ("apple-app", ["swift", "swiftui", "xcode", "app store", "testflight", "macos", "ios", "entitlement"]),
    ("web-frontend", ["frontend", "website", "react", "next", "css", "playwright", "browser", "responsive"]),
    ("software-code", ["bug", "fix", "refactor", "test", "code", "build", "lint", "compile"]),
    ("research", ["research", "search", "look up", "url", "web", "source", "compare"]),
    ("document-artifact", ["pdf", "docx", "slides", "presentation", "spreadsheet", "csv", "xlsx"]),
    ("agent-runtime", ["agent", "mcp", "skill", "connector", "herdr", "codex", "claude", "harness", "loop", "valp"]),
    ("ops-release", ["deploy", "release", "publish", "upload", "submit", "rollback", "ci"]),
    ("prototype", ["prototype", "mock", "spike", "alternative", "experiment"]),
]

PROFILE_CAPABILITIES = {
    "generic-analysis": ["visible_synthesis", "risk_review", "coordination"],
    "software-code": ["implementation", "verification", "code_review"],
    "apple-app": ["implementation", "verification", "xcode", "swiftui", "app_review", "frontend_ux", "alternatives"],
    "web-frontend": ["implementation", "playwright", "browser", "ux_review"],
    "research": ["web_search", "source_review", "visible_synthesis"],
    "document-artifact": ["document", "pdf", "presentation", "spreadsheet"],
    "agent-runtime": ["mcp", "skills", "connector", "state", "verification"],
    "ops-release": ["release_gate", "approval_gate", "verification"],
    "prototype": ["prototype", "alternatives", "mock"],
}

PROFILE_ROLE_REQUIREMENTS = {
    "generic-analysis": ["coordinator", "reviewer"],
    "software-code": ["coordinator", "implementer", "reviewer"],
    "apple-app": ["coordinator", "implementer", "reviewer", "prototype"],
    "web-frontend": ["coordinator", "implementer", "reviewer"],
    "research": ["coordinator", "researcher", "reviewer"],
    "document-artifact": ["coordinator", "implementer", "reviewer"],
    "agent-runtime": ["coordinator", "implementer", "reviewer"],
    "ops-release": ["coordinator", "implementer", "reviewer"],
    "prototype": ["coordinator", "prototype", "implementer"],
}

RUNTIME_DISPATCH_RETRYABLE_STOP_REASONS = {
    "runtime dispatch failure",
    # Compatibility for tasks blocked before provisioning failures were folded
    # into the common runtime dispatch failure state.
    "runtime session provisioning failure",
    "runtime preflight failure",
    "owned session model readiness pending",
}

UI_ATTENTION_PROFILES = {"apple-app", "web-frontend", "prototype"}
TASK_RUNTIME_CAPABILITIES_REF = "runtime/task-capabilities.json"

ATTENTION_HEAD_ROLES = {
    "state_gate": "coordinator",
    "implementation": "implementer",
    "ux_review": "reviewer",
    "prototype": "prototype",
}

DEFAULT_CONTEXT_POLICIES = {
    "coordinator": {"soft_warning_pct": 50, "hard_compression_pct": 60, "emergency_stop_pct": 80},
    "implementer": {"soft_warning_pct": 55, "hard_compression_pct": 65, "emergency_stop_pct": 80},
    "reviewer": {"soft_warning_pct": 60, "hard_compression_pct": 70, "emergency_stop_pct": 80},
    "prototype": {"soft_warning_pct": 60, "hard_compression_pct": 70, "emergency_stop_pct": 80},
    "other": {"soft_warning_pct": 60, "hard_compression_pct": 70, "emergency_stop_pct": 80},
}

ROLE_MATCH_TERMS = {
    "coordinator": ["coordination", "coordinator", "state", "gate", "approval", "routing", "synthesis", "final record"],
    "implementer": ["implementation", "implementer", "verification", "tool_execution", "edit", "build", "test", "code"],
    "reviewer": ["review", "reviewer", "risk_review", "code_review", "source_review", "ux_review", "read-only", "read_only"],
    "prototype": ["prototype", "alternatives", "mock", "spike", "experiment"],
    "researcher": ["research", "web_search", "source", "retrieval", "compare"],
}

RUNTIME_TASK_STATE_MAPPING = {
    "queued": "accepted_by_runtime_not_delivery",
    "dispatched": "dispatch_submitted_only_with_proof",
    "running": "executing",
    "completed": "dispatch_completed_only_with_expected_evidence",
    "failed": "failed_or_blocked_with_reason",
    "cancelled": "cancelled",
}

TASK_STATE_STATUSES = frozenset(
    {
        "new",
        "published",
        "scanning_capabilities",
        "scanning_context",
        "loading_local_overlay",
        "selecting_runtime_adapter",
        "classifying_task",
        "selecting_profile",
        "decomposing_tasks",
        "recommending_skills",
        "building_provider_matrix",
        "scoring_routes",
        "routing_capabilities",
        "routing_squad",
        "dispatching",
        "suspended",
        "planned",
        "locked",
        "executing",
        "verifying",
        "reviewing",
        "resolving_agent_recommendations",
        "fixing",
        "approval_required",
        "recording",
        "done",
        "blocked",
        "failed",
        "cancelled",
    }
)

DEFAULT_MIN_TERMINAL_SIZE = {"width": 60, "height": 20}
AGENT_MIN_TERMINAL_SIZE = {
    "agy": {"width": 70, "height": 24},
}
RUNTIME_CHOICES = {"auto", "manual", "herdr", "langgraph", "queue"}
DISPATCH_BRIEF_CHAR_LIMIT = 480
SKILL_TASK_LABEL_CHAR_LIMIT = 120
DISPATCH_ROLE_BUDGETS = {
    "coordinator": {"max_chars": 3000, "max_reference_tokens": 750},
    "implementer": {"max_chars": 2800, "max_reference_tokens": 700},
    "reviewer": {"max_chars": 2400, "max_reference_tokens": 600},
    "prototype": {"max_chars": 2400, "max_reference_tokens": 600},
    "researcher": {"max_chars": 2400, "max_reference_tokens": 600},
    "other": {"max_chars": 2200, "max_reference_tokens": 550},
}
ITERATION_BUDGET_STOP_CONDITIONS = [
    "dispatch reference-token budget exhausted",
    "dispatch-count budget exhausted",
    "reroute budget exhausted",
    "fix-review-round budget exhausted",
    "approval gate unresolved",
    "runtime preflight failure",
    "missing expected evidence",
    "critical or high review blocker",
    "context compression required",
]
SUSPENSION_RESUME_EVENTS = {"receipt", "timeout", "runtime_failure", "cancellation", "user_input"}
EXTERNAL_RESUME_EVENTS = {"runtime_failure", "cancellation", "user_input"}
DELIVERY_RECEIPT_EVENTS = {"dispatch_submitted", "manual_delivery_attested"}
TERMINAL_WORKER_RECEIPT_EVENTS = {
    "dispatch_completed",
    "dispatch_blocked",
    "manual_result_attested",
    "manual_blocked",
}
WAKE_REASONS_BY_RESUME_EVENT = {
    "receipt": {"dependency_ready", "dispatch_blocked", "manual_blocked"},
    "timeout": {"timeout"},
    "runtime_failure": {"runtime_failure"},
    "cancellation": {"cancellation"},
    "user_input": {"user_input"},
}
TASK_STATUS_BY_WAKE_REASON = {
    "dependency_ready": "executing",
    "user_input": "executing",
    "dispatch_blocked": "blocked",
    "manual_blocked": "blocked",
    "timeout": "blocked",
    "runtime_failure": "blocked",
    "cancellation": "cancelled",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


DIRECTORY_FSYNC_UNSUPPORTED_ERRNOS = {
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
}
FILE_LOCK_CONTENTION_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
    getattr(errno, "EDEADLK", errno.EAGAIN),
}
TASK_LOCK_TIMEOUT_SECONDS = 30.0
TASK_LOCK_RETRY_SECONDS = 0.05


def fsync_directory(directory: Path) -> bool:
    if os.name == "nt":
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError as exc:
        if exc.errno in DIRECTORY_FSYNC_UNSUPPORTED_ERRNOS:
            return False
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno in DIRECTORY_FSYNC_UNSUPPORTED_ERRNOS:
                return False
            raise
    finally:
        os.close(descriptor)
    return True


def retry_file_lock(
    attempt: Callable[[], None],
    timeout_seconds: float = TASK_LOCK_TIMEOUT_SECONDS,
    retry_seconds: float = TASK_LOCK_RETRY_SECONDS,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            attempt()
            return
        except OSError as exc:
            if exc.errno not in FILE_LOCK_CONTENTION_ERRNOS:
                raise
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out acquiring task state lock after {timeout_seconds:g} seconds"
                ) from exc
            time.sleep(retry_seconds)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode & 0o777 if path.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temporary_path, existing_mode)
        os.replace(temporary_path, path)
        fsync_directory(path.parent)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def read_json_lines_strict(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSONL record at {path.name}:{line_number}: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise SystemExit(f"Invalid JSONL record at {path.name}:{line_number}: expected object")
        records.append(record)
    return records


def load_dispatch_receipts(directory: Path, task_id: str) -> list[dict[str, Any]]:
    adopted = [
        adapter_id
        for adapter_id in ("herdr", "queue", "manual", "langgraph")
        if (directory / "runtime" / adapter_id / "adoption.json").is_file()
    ]
    if len(adopted) > 1:
        raise SystemExit("Invalid dispatch receipt ledger: multiple runtime adoption markers")
    if adopted:
        try:
            if adopted[0] == "langgraph":
                from .langgraph_adapter import load_langgraph_v3_receipts

                return load_langgraph_v3_receipts(directory)
            from .runtime_adapters import load_runtime_v3_receipts, manual_effective_receipt_ids

            receipts = load_runtime_v3_receipts(directory, adopted[0])
            if adopted[0] == "manual":
                manual_effective_receipt_ids(directory, task_id)
            return receipts
        except Exception as error:
            raise SystemExit(f"Invalid adopted {adopted[0]} v3 receipt ledger: {error}") from error
    receipts = read_json_lines_strict(directory / "dispatch-receipts.jsonl")
    errors = deterministic_receipt_ledger_errors(receipts, task_id)
    if errors:
        raise SystemExit("Invalid dispatch receipt ledger: " + "; ".join(errors[:5]))
    return receipts


def runtime_receipt_is_effective(
    directory: Path,
    task_id: str,
    receipt: dict[str, Any],
) -> bool:
    if (
        receipt.get("event") not in {
            "manual_dispatch_written", "manual_delivery_attested",
            "manual_result_attested", "manual_blocked",
        }
        or not (directory / "runtime" / "manual" / "adoption.json").is_file()
    ):
        return True
    from .runtime_adapters import manual_receipt_is_effective

    try:
        return manual_receipt_is_effective(
            directory, task_id, str(receipt.get("receipt_id") or "")
        )
    except Exception as error:
        raise SystemExit(f"Invalid adopted manual v3 effective state: {error}") from error


def runtime_v3_identity_available(directory: Path) -> bool:
    try:
        workspace = directory.resolve().parents[2]
        installation = read_json(workspace / ".valp" / "installation.json")
        state = read_json(workspace / ".valp" / "state.json")
        policy = read_json(directory / "automation-policy.json")
    except (IndexError, OSError):
        return False
    installation_id = installation.get("installation_id")
    epoch = state.get("active_leader_epoch")
    return bool(
        installation.get("schema_version") == "valp-installation.v1"
        and state.get("schema_version") == "valp-executable-state.v1"
        and isinstance(installation_id, str)
        and installation_id
        and state.get("installation_id") == installation_id
        and type(epoch) is int
        and epoch >= 1
        and installation.get("active_leader_epoch") == epoch
        and policy.get("schema_version") == "valp-automation-policy.v1"
        and policy.get("approval_required") is False
    )


def read_json_strict(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"Missing {path.name}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON at {path.name}: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid JSON at {path.name}: expected object")
    return data


def append_json_line_durable(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    if created:
        fsync_directory(path.parent)


def _herdr_suspension_epoch(directory: Path) -> int:
    state = read_json(directory / "state.json")
    suspension = state.get("suspension") if isinstance(state, dict) else None
    if isinstance(suspension, dict) and type(suspension.get("suspension_epoch")) is int:
        return max(1, int(suspension["suspension_epoch"]))
    epochs = [
        int(event.get("suspension_epoch"))
        for event in read_json_lines_strict(directory / "wait-events.jsonl")
        if type(event.get("suspension_epoch")) is int
    ]
    return max(epochs, default=0) + 1


def _legacy_receipt_source_digest(
    task_id: str,
    line_number: int,
    receipt: dict[str, Any],
) -> str:
    source = {
        "task_id": task_id,
        "line_number": line_number,
        "receipt": receipt,
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _legacy_translation_receipt_id(
    task_id: str,
    item: dict[str, Any],
    legacy: dict[str, Any],
) -> str:
    source = {"task_id": task_id, "item": item, "legacy": legacy}
    return "sha256:" + hashlib.sha256(
        json.dumps(source, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def translate_legacy_herdr_receipts(
    directory: Path,
    task_id: str,
    phase: tuple[str, str] | None = None,
) -> int:
    """Translate legacy HERDR receipts into identity-bound v2 records.

    The adapter owns pane delivery proof, while VALP owns task/work-item
    identity. Legacy receipts remain preserved for audit history; translated
    records are appended exactly once and are the only records accepted by the
    v2 dependency matcher.
    """
    receipts_path = directory / "dispatch-receipts.jsonl"
    if not receipts_path.exists():
        return 0
    dependencies = read_json(directory / "submission-dependencies.json")
    work_items = [item for item in dependencies.get("work_items") or [] if isinstance(item, dict)]
    if not work_items:
        return 0
    receipts = read_json_lines_strict(receipts_path)
    existing_ids = {
        str(record.get("receipt_id"))
        for record in receipts
        if record.get("schema_version") == "valp-dispatch-receipt.v2" and record.get("receipt_id")
    }
    consumed_legacy_sources = {
        str((record.get("proof") or {}).get("legacy_source_digest"))
        for record in receipts
        if record.get("schema_version") == "valp-dispatch-receipt.v2"
        and isinstance(record.get("proof"), dict)
        and (record.get("proof") or {}).get("legacy_source_digest")
    }
    event_sequence = max(
        [
            int(record.get("event_sequence"))
            for record in receipts
            if record.get("schema_version") == "valp-dispatch-receipt.v2"
            and type(record.get("event_sequence")) is int
        ],
        default=0,
    )
    translated = 0
    suspension_epoch = _herdr_suspension_epoch(directory)
    for line_number, legacy in enumerate(receipts, 1):
        if legacy.get("schema_version") == "valp-dispatch-receipt.v2":
            continue
        if legacy.get("event") not in {"dispatch_submitted", "dispatch_completed", "dispatch_blocked"}:
            continue
        source_digest = _legacy_receipt_source_digest(task_id, line_number, legacy)
        if source_digest in consumed_legacy_sources:
            continue
        if any(
            _legacy_translation_receipt_id(task_id, item, legacy) in existing_ids
            for item in work_items
        ):
            continue
        agent = str(legacy.get("agent") or "")
        expected_refs = {str(ref) for ref in legacy.get("expected_refs") or []}
        matches = [
            item
            for item in work_items
            if str(item.get("agent") or "") == agent
            and (
                phase is None
                or (
                    str(item.get("agent") or "") == phase[0]
                    and str(item.get("role") or "") == phase[1]
                )
            )
            and expected_refs.issubset({str(ref) for ref in item.get("expected_refs") or []})
        ]
        if len(matches) != 1:
            continue
        item = matches[0]
        digest_source = json.dumps(
            {
                "task_id": task_id,
                "item": item,
                "legacy_source_digest": source_digest,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        receipt_id = "sha256:" + hashlib.sha256(digest_source).hexdigest()
        if receipt_id in existing_ids:
            continue
        event_sequence += 1
        runtime = legacy.get("runtime") if isinstance(legacy.get("runtime"), dict) else {}
        proof = dict(legacy.get("proof") or {}) if isinstance(legacy.get("proof"), dict) else {}
        proof["legacy_source_digest"] = source_digest
        if legacy.get("event") == "dispatch_submitted":
            for key in ("pane_id", "terminal_id", "workspace_id", "tab_id"):
                value = runtime.get(key)
                if isinstance(value, str) and value.strip():
                    proof.setdefault(key, value)
            proof.setdefault("adapter", "HERDR")
        translated_record = {
            "schema_version": "valp-dispatch-receipt.v2",
            "receipt_id": receipt_id,
            "task_id": task_id,
            "event_sequence": event_sequence,
            "ts": str(legacy.get("ts") or now_iso()),
            "agent": agent,
            "role": str(item.get("role") or "other"),
            "work_item_id": str(item.get("work_item_id") or ""),
            "dispatch_id": str(item.get("dispatch_id") or ""),
            "dispatch_generation": int(item.get("dispatch_generation") or 1),
            "event": str(legacy.get("event")),
            "exit_code": int(legacy.get("exit_code") or 0),
            "summary": str(legacy.get("summary") or ""),
            "dispatch_ref": str(legacy.get("dispatch_ref") or f"agents/{agent}/dispatch.md"),
            "expected_refs": [str(ref) for ref in item.get("expected_refs") or []],
            "runtime": runtime,
        }
        if proof:
            translated_record["proof"] = proof
        if translated_record["event"] in {"dispatch_completed", "dispatch_blocked"}:
            translated_record["suspension_epoch"] = suspension_epoch
        append_json_line_durable(receipts_path, translated_record)
        existing_ids.add(receipt_id)
        consumed_legacy_sources.add(source_digest)
        translated += 1
    return translated


@contextmanager
def task_state_lock(directory: Path) -> Iterator[None]:
    lock_path = directory / ".valp-state.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            retry_file_lock(
                lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            )
        else:
            import fcntl

            retry_file_lock(
                lambda: fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            )
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_timeline_event(directory: Path, event: str, summary: str, **details: Any) -> None:
    record = {"ts": now_iso(), "event": event, "summary": summary, **details}
    with (directory / "timeline.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_command(
    command: list[str],
    timeout: float = 8.0,
    input_text: str | None = None,
    stdout_limit: int = 4000,
    stderr_limit: int = 4000,
) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {
            "command": command,
            "ok": False,
            "exit_code": 127,
            "stdout": "",
            "stderr": "command not found",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": command,
            "ok": False,
            "exit_code": None,
            "stdout": (exc.stdout or "")[:2000] if isinstance(exc.stdout, str) else "",
            "stderr": "command timed out",
        }
    return {
        "command": command,
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": (completed.stdout or "")[:stdout_limit],
        "stderr": (completed.stderr or "")[:stderr_limit],
    }


def observe_source_provenance(invoked_entrypoint: Path | None = None) -> dict[str, Any]:
    source_root = Path(__file__).resolve().parents[1]
    if invoked_entrypoint is None:
        invoked_path = source_root / "bin" / "valp"
    else:
        expanded = Path(os.path.expanduser(str(invoked_entrypoint)))
        if expanded.is_absolute():
            invoked_path = expanded
        else:
            discovered = shutil.which(str(expanded))
            invoked_path = Path(discovered) if discovered else Path.cwd() / expanded
    invoked_path = Path(os.path.abspath(str(invoked_path)))
    resolved_entrypoint = invoked_path.resolve()

    top_level_result = run_command(
        ["git", "-C", str(source_root), "rev-parse", "--show-toplevel"]
    )
    commit_result = run_command(["git", "-C", str(source_root), "rev-parse", "--verify", "HEAD"])
    tree_result = run_command(["git", "-C", str(source_root), "rev-parse", "--verify", "HEAD^{tree}"])
    status_result = run_command(
        ["git", "-C", str(source_root), "status", "--porcelain=v1", "-uall"],
        stdout_limit=1,
    )
    observed_top_level = str(top_level_result.get("stdout") or "").strip()
    git_resolved = bool(
        top_level_result["ok"]
        and observed_top_level
        and Path(observed_top_level).resolve() == source_root
        and commit_result["ok"]
        and tree_result["ok"]
        and status_result["ok"]
    )
    worktree_status = (
        "dirty"
        if git_resolved and bool(str(status_result.get("stdout") or ""))
        else "clean" if git_resolved else "unavailable"
    )
    return {
        "status": f"resolved_{worktree_status}" if git_resolved else "unavailable",
        "implementation_id": "valp-reference-cli",
        "invoked_entrypoint": str(invoked_path),
        "resolved_entrypoint": str(resolved_entrypoint),
        "source_root": str(source_root),
        "observed_at": now_iso(),
        "vcs": {
            "kind": "git" if git_resolved else "none",
            "commit": str(commit_result.get("stdout") or "").strip() if git_resolved else None,
            "tree": str(tree_result.get("stdout") or "").strip() if git_resolved else None,
            "worktree_status": worktree_status,
        },
    }


def new_source_provenance(invoked_entrypoint: Path | None = None) -> dict[str, Any]:
    observation = observe_source_provenance(invoked_entrypoint)
    return {
        "schema_version": "valp-source-provenance.v1",
        "task_start": observation,
        "last_observed": observation,
    }


def refresh_source_provenance(
    state: dict[str, Any],
    invoked_entrypoint: Path | None = None,
) -> dict[str, Any]:
    current = state.get("source_provenance")
    task_start = current.get("task_start") if isinstance(current, dict) else None
    return {
        "schema_version": "valp-source-provenance.v1",
        "task_start": task_start,
        "last_observed": observe_source_provenance(invoked_entrypoint),
    }


def parse_json_stdout(result: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def workspace_root(path: Path) -> Path:
    root = path.resolve()
    (root / ".herdr-loop" / "tasks").mkdir(parents=True, exist_ok=True)
    (root / ".herdr-loop" / "agents").mkdir(parents=True, exist_ok=True)
    return root


def task_dir(root: Path, task_id: str) -> Path:
    return root / ".herdr-loop" / "tasks" / task_id


def first_existing_or_default(paths: list[Path]) -> Path:
    for path in paths:
        if path.exists():
            return path
    return paths[0]


def local_capabilities_path(root: Path | None = None) -> Path:
    configured = os.environ.get("VALP_CAPABILITIES_FILE")
    if configured:
        return Path(configured).expanduser()
    candidates: list[Path] = []
    if root:
        candidates.append(root.resolve() / ".valp" / "agents" / "capabilities.json")
    candidates.extend(
        [
            Path.home() / ".valp" / "agent-capabilities.json",
            Path.home() / ".herdr" / "agent-capabilities.json",
        ]
    )
    return first_existing_or_default(candidates)


def local_overlay_path(root: Path | None = None) -> Path:
    configured = os.environ.get("VALP_LOCAL_OVERLAY_FILE")
    if configured:
        return Path(configured).expanduser()
    candidates: list[Path] = []
    if root:
        candidates.append(root.resolve() / ".valp" / "local-overlay.json")
    candidates.extend(
        [
            Path.home() / ".valp" / "local-overlay.json",
            Path.home() / ".herdr" / "valp-local-overlay.json",
        ]
    )
    return first_existing_or_default(candidates)


def normalize_runtime(runtime: str | None = None) -> str:
    selected = (runtime or "auto").strip().lower()
    if selected not in RUNTIME_CHOICES:
        raise SystemExit(f"Unsupported runtime: {runtime}. Expected one of: {', '.join(sorted(RUNTIME_CHOICES))}")
    return selected


def auto_runtime() -> str:
    return "herdr" if shutil.which("herdr") else "manual"


def resolve_runtime(runtime: str | None = None) -> str:
    selected = normalize_runtime(runtime)
    return auto_runtime() if selected == "auto" else selected


def runtime_from_adapter_record(runtime: dict[str, Any]) -> str:
    adapter_id = str(runtime.get("id") or "").lower()
    if adapter_id in {"manual", "queue", "langgraph", "herdr"}:
        return adapter_id
    runtime_class = str(runtime.get("class") or "").lower()
    runtime_name = str(runtime.get("name") or "").lower()
    if runtime_class == "manual" or runtime_name == "manual":
        return "manual"
    if runtime_class == "daemon_queue":
        return "queue"
    if runtime_class == "hosted_local_platform" and "langgraph" in runtime_name:
        return "langgraph"
    if runtime_class == "pane_controller":
        return "herdr"
    return "auto"


def classify_profile(prompt: str) -> str:
    lowered = prompt.lower()
    scored: list[tuple[int, str]] = []
    for profile, keywords in PROFILE_RULES:
        score = sum(1 for keyword in keywords if keyword in lowered)
        if score:
            scored.append((score, profile))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]
    return "generic-analysis"


def load_local_capabilities(root: Path | None = None) -> dict[str, Any]:
    data = read_json(local_capabilities_path(root))
    if data:
        return data
    return {
        "schema_version": "valp-agent-capabilities.v1",
        "updated_at": now_iso(),
        "source": "generic manual local scan",
        "agents": {
            "manual-operator": {
                "active": True,
                "role": ["coordination", "review", "manual_evidence"],
                "skills": [],
                "mcp_servers": [],
                "strengths": ["writes manual evidence", "records receipts", "keeps local assumptions out of protocol semantics"],
                "must_not_do": [
                    "must not bypass approval gates",
                    "must not claim runtime dispatch proof",
                    "must not imply a specific AI agent is installed",
                ],
            }
        },
    }


def load_dispatch_capabilities(
    root: Path,
    directory: Path,
    routing: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    capabilities = load_local_capabilities(root)
    routing_marker = routing.get("task_runtime_capabilities")
    state_marker = state.get("task_runtime_capabilities")
    if routing_marker is None and state_marker is None:
        return capabilities
    if (
        not isinstance(routing_marker, dict)
        or not isinstance(state_marker, dict)
        or routing_marker != state_marker
    ):
        raise SystemExit("Task runtime capability marker is missing or inconsistent")
    digest = str(routing_marker.get("digest") or "")
    if (
        routing_marker.get("status") != "recorded"
        or routing_marker.get("ref") != TASK_RUNTIME_CAPABILITIES_REF
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
    ):
        raise SystemExit("Task runtime capability marker is invalid")

    capability_path = directory / TASK_RUNTIME_CAPABILITIES_REF
    if not capability_path.is_file():
        raise SystemExit("Task runtime capability record is missing")
    raw = capability_path.read_bytes()
    observed_digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if observed_digest != digest:
        raise SystemExit("Task runtime capability digest mismatch")
    task_capabilities = read_json_strict(capability_path)
    task_agents = task_capabilities.get("agents")
    if (
        task_capabilities.get("schema_version") != "valp-agent-capabilities.v1"
        or not isinstance(task_agents, dict)
    ):
        raise SystemExit("Task runtime capability record is invalid")

    merged = json.loads(json.dumps(capabilities))
    merged_agents = merged.get("agents")
    if not isinstance(merged_agents, dict):
        raise SystemExit("Current capability evidence has no Agent registry")
    override_count = 0
    for agent, task_info in task_agents.items():
        if agent not in merged_agents or not isinstance(merged_agents.get(agent), dict):
            raise SystemExit(
                f"Task runtime capability Agent is absent from current capability evidence: {agent}"
            )
        if not isinstance(task_info, dict):
            raise SystemExit(f"Task runtime capability Agent record is invalid: {agent}")
        task_runtime = task_info.get("runtime")
        if not isinstance(task_runtime, dict):
            raise SystemExit(f"Task runtime capability runtime record is invalid: {agent}")
        merged_runtime = merged_agents[agent].get("runtime")
        if not isinstance(merged_runtime, dict):
            merged_runtime = {}
            merged_agents[agent]["runtime"] = merged_runtime
        for field in ("launch_argv", "version_command"):
            if field not in task_runtime:
                continue
            argv = task_runtime.get(field)
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) and item.strip() for item in argv)
            ):
                raise SystemExit(
                    f"Task runtime capability {field} is invalid for Agent {agent}"
                )
            merged_runtime[field] = [str(item) for item in argv]
            override_count += 1
    if override_count == 0:
        raise SystemExit("Task runtime capability record contains no runtime command overrides")
    return merged


def load_local_overlay(root: Path | None = None) -> dict[str, Any]:
    return read_json(local_overlay_path(root))


def capability_runtime_argv_by_agent(
    agents: dict[str, Any],
    field: str,
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if not isinstance(agents, dict):
        return result
    for agent, info in agents.items():
        runtime = info.get("runtime") if isinstance(info, dict) else None
        argv = runtime.get(field) if isinstance(runtime, dict) else None
        if (
            isinstance(argv, list)
            and argv
            and all(isinstance(item, str) and item.strip() for item in argv)
        ):
            result[str(agent)] = [str(item) for item in argv]
    return result


def scan_workspace(
    root: Path,
    task_id: str | None = None,
    runtime: str | None = None,
    invoked_entrypoint: Path | None = None,
) -> dict[str, Any]:
    root = workspace_root(root)
    capabilities_path = local_capabilities_path(root)
    overlay_path = local_overlay_path(root)
    capabilities = load_local_capabilities(root)
    overlay = load_local_overlay(root)
    agents = capabilities.get("agents") or {}
    launch_argv_by_agent = capability_runtime_argv_by_agent(agents, "launch_argv")
    version_command_by_agent = capability_runtime_argv_by_agent(agents, "version_command")
    capabilities["runtime_preflight"] = collect_runtime_preflight(
        list(agents.keys()),
        runtime=runtime,
        launch_argv_by_agent=launch_argv_by_agent,
        version_command_by_agent=version_command_by_agent,
    )
    capabilities["last_valp_scan_at"] = now_iso()
    capabilities["capabilities_source_ref"] = str(capabilities_path) if read_json(capabilities_path) else None
    capabilities["local_overlay_ref"] = str(overlay_path) if overlay else None
    write_json(root / ".herdr-loop" / "agents" / "capabilities.json", capabilities)
    if overlay:
        write_json(root / ".herdr-loop" / "local-overlay.json", overlay)
    if task_id:
        directory = task_dir(root, task_id)
        state_path = directory / "state.json"
        state = read_json(state_path)
        if state:
            routing_path = directory / "routing.json"
            routing = read_json(routing_path)
            current_status = str(state.get("status") or "")
            routed_task = bool(
                state.get("selected_agents")
                and routing
            )
            if current_status in {"new", "published"}:
                state["status"] = "scanning_capabilities"
            elif current_status == "scanning_capabilities" and routed_task:
                state["status"] = "dispatching"
            if routed_task:
                selected_agents = [
                    str(agent)
                    for agent in routing.get("selected_agents") or []
                    if str(agent).strip()
                ]
                session_projection = read_json(directory / "agent-sessions.json")
                session_bindings = targeted_session_bindings(
                    session_projection,
                    selected_agents,
                )
                existing_matrix = routing.get("provider_matrix")
                if (
                    selected_agents
                    and set(session_bindings) == set(selected_agents)
                    and isinstance(existing_matrix, dict)
                ):
                    task_preflight = collect_runtime_preflight(
                        selected_agents,
                        runtime=runtime,
                        session_bindings=session_bindings,
                        launch_argv_by_agent=launch_argv_by_agent,
                        version_command_by_agent=version_command_by_agent,
                    )
                    evaluated_at = now_iso()
                    refreshed_matrix = provider_matrix_for(
                        selected_agents,
                        agents,
                        overlay,
                        task_preflight,
                        evaluated_at=evaluated_at,
                        dynamic_discovery_required=bool(
                            (existing_matrix.get("model_awareness") or {}).get(
                                "dynamic_discovery_required",
                                resolve_runtime(runtime) != "manual",
                            )
                        ),
                    )
                    historical_preflight = (
                        existing_matrix.get("runtime_preflight")
                        or (routing.get("runtime_adapter") or {}).get("preflight")
                        or read_json(directory / "runtime-preflight.json")
                    )
                    if historical_preflight:
                        refreshed_matrix["runtime_preflight"] = historical_preflight
                    routing["provider_matrix"] = refreshed_matrix
                    write_json(routing_path, routing)
            state["source_provenance"] = refresh_source_provenance(
                state,
                invoked_entrypoint,
            )
            state["capabilities_ref"] = ".herdr-loop/agents/capabilities.json"
            state["local_overlay"] = {
                "used": bool(overlay),
                "ref": ".herdr-loop/local-overlay.json" if overlay else None,
            }
            state["updated_at"] = now_iso()
            write_json(state_path, state)
    return capabilities


def extract_goal_text(task_text: str) -> str:
    lines = task_text.splitlines()
    collecting = False
    collected: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == "## goal":
            collecting = True
            continue
        if collecting and stripped.startswith("## "):
            break
        if collecting:
            collected.append(line)
    goal = "\n".join(collected).strip()
    return goal or task_text.strip()


def compact_text(value: str) -> str:
    return " ".join(str(value or "").split())


def bounded_text(value: str, limit: int) -> str:
    text = compact_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def task_brief_for_dispatch(task_text: str) -> str:
    goal = extract_goal_text(task_text)
    return bounded_text(goal, DISPATCH_BRIEF_CHAR_LIMIT)


def dispatch_budget_for_agent(agent: str, role_assignments: dict[str, str]) -> dict[str, Any]:
    assigned_roles = {role for role, selected in role_assignments.items() if selected == agent}
    role = next(
        (candidate for candidate in ["implementer", "reviewer", "prototype", "researcher", "coordinator"] if candidate in assigned_roles),
        "other",
    )
    return {
        "role": role,
        **DISPATCH_ROLE_BUDGETS[role],
        "token_estimator": "ceil(chars/4)",
    }


def iteration_budget_for(task_id: str, role_assignments: dict[str, str]) -> dict[str, Any]:
    roles = list(dict.fromkeys(str(role) for role in role_assignments if str(role)))
    initial_tokens = sum(
        int(DISPATCH_ROLE_BUDGETS.get(role, DISPATCH_ROLE_BUDGETS["other"])["max_reference_tokens"])
        for role in roles
    )
    reviewer_tokens = int(DISPATCH_ROLE_BUDGETS["reviewer"]["max_reference_tokens"])
    max_fix_review_rounds = 3
    return {
        "schema_version": "valp-iteration-budget.v1",
        "task_id": task_id,
        "strategy": "leader_declared_bounded_team",
        "max_dispatch_reference_tokens": max(initial_tokens * 2 + reviewer_tokens * max_fix_review_rounds, 1),
        "max_dispatches": max(len(roles) + 2, 3),
        "max_reroutes": 1,
        "max_fix_review_rounds": max_fix_review_rounds,
        "usage": {
            "dispatch_reference_tokens": 0,
            "dispatches": 0,
            "reroutes": 0,
            "fix_review_rounds": 0,
        },
        "status": "active",
        "stop_reason": None,
        "stop_conditions": ITERATION_BUDGET_STOP_CONDITIONS,
        "updated_at": now_iso(),
    }


def _dispatch_usage_from_receipts(
    directory: Path,
    routing: dict[str, Any],
    task_id: str,
) -> dict[str, int]:
    budgets = routing.get("dispatch_payload_budgets") or {}
    records = load_dispatch_receipts(directory, task_id)
    submitted = [
        (line_number, record)
        for line_number, record in enumerate(records, 1)
        if record.get("event") in {"dispatch_submitted", "manual_delivery_attested"}
    ]
    v2_records = [
        record
        for _, record in submitted
        if record.get("schema_version") == "valp-dispatch-receipt.v2"
    ]
    translated_source_digests = {
        str((record.get("proof") or {}).get("legacy_source_digest"))
        for record in v2_records
        if isinstance(record.get("proof"), dict)
        and (record.get("proof") or {}).get("legacy_source_digest")
    }
    legacy_v2_records = [
        record
        for record in v2_records
        if not (
            isinstance(record.get("proof"), dict)
            and (record.get("proof") or {}).get("legacy_source_digest")
        )
    ]
    seen: set[tuple[Any, ...]] = set()
    dispatches = 0
    reference_tokens = 0
    for line_number, record in submitted:
        expected_refs = tuple(sorted(str(ref) for ref in record.get("expected_refs") or []))
        if record.get("schema_version") == "valp-dispatch-receipt.v2":
            identity = (
                "v2",
                record.get("task_id"),
                record.get("agent"),
                record.get("role"),
                record.get("work_item_id"),
                record.get("dispatch_id"),
                record.get("dispatch_generation"),
                record.get("event"),
            )
        else:
            source_digest = _legacy_receipt_source_digest(
                task_id,
                line_number,
                record,
            )
            if source_digest in translated_source_digests:
                continue
            if any(
                twin.get("agent") == record.get("agent")
                and twin.get("event") == record.get("event")
                and (
                    tuple(sorted(str(ref) for ref in twin.get("expected_refs") or [])) == expected_refs
                    or (
                        not expected_refs
                        and twin.get("ts") == record.get("ts")
                        and twin.get("dispatch_ref") == record.get("dispatch_ref")
                    )
                )
                for twin in legacy_v2_records
            ):
                continue
            identity = ("legacy", source_digest)
        if identity in seen:
            continue
        seen.add(identity)
        dispatches += 1
        agent = str(record.get("agent") or "")
        payload = budgets.get(agent) or {}
        reference_tokens += int(payload.get("actual_reference_tokens") or 0)
    return {
        "dispatch_reference_tokens": reference_tokens,
        "dispatches": dispatches,
    }


def refresh_iteration_budget(
    directory: Path,
    routing: dict[str, Any],
    budget: dict[str, Any],
    reroutes: int | None = None,
) -> dict[str, Any]:
    usage = dict(budget.get("usage") or {})
    observed = _dispatch_usage_from_receipts(
        directory,
        routing,
        str(budget.get("task_id") or directory.name),
    )
    usage.update(observed)
    if reroutes is not None:
        usage["reroutes"] = max(int(usage.get("reroutes") or 0), reroutes)
    correction = read_json(directory / "correction-cycle.json")
    rounds = correction.get("rounds") if isinstance(correction, dict) else []
    usage["fix_review_rounds"] = max(int(usage.get("fix_review_rounds") or 0), len(rounds) if isinstance(rounds, list) else 0)
    budget["usage"] = {
        "dispatch_reference_tokens": int(usage.get("dispatch_reference_tokens") or 0),
        "dispatches": int(usage.get("dispatches") or 0),
        "reroutes": int(usage.get("reroutes") or 0),
        "fix_review_rounds": int(usage.get("fix_review_rounds") or 0),
    }
    if budget.get("status") not in {"blocked", "completed"}:
        budget["status"] = "active"
        budget["stop_reason"] = None
        limits = {
            "dispatch_reference_tokens": int(budget.get("max_dispatch_reference_tokens") or 0),
            "dispatches": int(budget.get("max_dispatches") or 0),
            "reroutes": int(budget.get("max_reroutes") or 0),
            "fix_review_rounds": int(budget.get("max_fix_review_rounds") or 0),
        }
        for key, limit in limits.items():
            if budget["usage"][key] > limit:
                budget["status"] = "exhausted"
                budget["stop_reason"] = f"{key} budget exhausted"
                break
    budget["updated_at"] = now_iso()
    write_json(directory / "iteration-budget.json", budget)
    return budget


def record_reroute_evidence(
    directory: Path,
    task_id: str,
    previous_routing: dict[str, Any],
    reroute_number: int,
) -> None:
    payload = json.dumps(previous_routing, ensure_ascii=False, sort_keys=True).encode("utf-8")
    append_json_line_durable(
        directory / "routing-history.jsonl",
        {
            "schema_version": "valp-routing-history.v1",
            "task_id": task_id,
            "event": "reroute_started",
            "reroute_number": reroute_number,
            "previous_routing_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "previous_selected_agents": previous_routing.get("selected_agents") or [],
            "previous_role_assignments": previous_routing.get("role_assignments") or {},
            "recorded_at": now_iso(),
        },
    )


def inflight_reroute_for_recovery(
    directory: Path,
    routing: dict[str, Any],
    budget: dict[str, Any],
    preflight: dict[str, Any],
) -> int | None:
    if budget.get("status") != "blocked" or budget.get("stop_reason") != "runtime preflight failure":
        return None
    if preflight.get("status") != "pass":
        return None
    history = read_json_lines(directory / "routing-history.jsonl")
    if not history or history[-1].get("event") != "reroute_started":
        return None
    latest = history[-1]
    reroute_number = int(latest.get("reroute_number") or 0)
    if reroute_number != int((budget.get("usage") or {}).get("reroutes") or 0):
        return None
    payload = json.dumps(routing, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    return reroute_number if latest.get("previous_routing_digest") == digest else None


def record_reroute_resume_evidence(directory: Path, task_id: str, reroute_number: int) -> None:
    append_json_line_durable(
        directory / "routing-history.jsonl",
        {
            "schema_version": "valp-routing-history.v1",
            "task_id": task_id,
            "event": "reroute_resumed",
            "reroute_number": reroute_number,
            "reason": "fresh runtime preflight resolved the recorded transient blocker",
            "recorded_at": now_iso(),
        },
    )


def _iteration_safety_errors(
    state: dict[str, Any],
    phases: list[tuple[str, str]],
) -> list[str]:
    errors: list[str] = []
    phase_roles = {role for _agent, role in phases}
    if state.get("status") in {"blocked", "cancelled", "done", "failed"}:
        errors.append(f"task status is {state.get('status')}")
    gates = state.get("gates") or {}
    if gates.get("approval") in {"needs_approval", "blocked", "failed"}:
        errors.append("approval gate unresolved")
    if gates.get("review") in {"blocked", "failed"} and not phase_roles.intersection(
        {"implementer", "reviewer"}
    ):
        errors.append("critical or high review blocker")
    if gates.get("verification") in {"blocked", "failed"}:
        errors.append("verification gate failed")
    if gates.get("expected_evidence") == "blocked" and not phases:
        errors.append("missing expected evidence")
    if state.get("context_compression_required") or state.get("compression_required"):
        errors.append("context compression required")
    return errors


def enforce_iteration_budget(
    directory: Path,
    routing: dict[str, Any],
    state: dict[str, Any],
    phases: list[tuple[str, str]],
) -> dict[str, Any]:
    budget = read_json(directory / "iteration-budget.json")
    if not budget:
        return {}
    phase_roles = {role for _agent, role in phases}
    stop_reasons = {
        reason.strip()
        for reason in str(budget.get("stop_reason") or "").split(";")
        if reason.strip()
    }
    resolvable_reasons = {"missing expected evidence"}
    if phase_roles.intersection({"implementer", "reviewer"}):
        resolvable_reasons.add("critical or high review blocker")
    if (
        budget.get("status") == "blocked"
        and state.get("status") == "dispatching"
        and stop_reasons
        and stop_reasons.issubset(resolvable_reasons)
    ):
        budget["status"] = "active"
        budget["stop_reason"] = None
    budget = refresh_iteration_budget(directory, routing, budget)
    safety_errors = _iteration_safety_errors(state, phases)
    if safety_errors:
        budget["status"] = "blocked"
        budget["stop_reason"] = "; ".join(safety_errors)
        write_json(directory / "iteration-budget.json", budget)
        raise SystemExit("Dispatch blocked by iteration safety gate: " + "; ".join(safety_errors))
    if budget.get("status") != "active":
        raise SystemExit(f"Dispatch blocked by iteration budget: {budget.get('stop_reason') or budget.get('status')}")
    payload_budgets = routing.get("dispatch_payload_budgets") or {}
    projected_dispatches = int((budget.get("usage") or {}).get("dispatches") or 0) + len(phases)
    projected_tokens = int((budget.get("usage") or {}).get("dispatch_reference_tokens") or 0) + sum(
        int((payload_budgets.get(agent) or {}).get("actual_reference_tokens") or 0)
        for agent, _role in phases
    )
    if projected_dispatches > int(budget.get("max_dispatches") or 0):
        budget["status"] = "blocked"
        budget["stop_reason"] = "dispatch-count budget exhausted"
        write_json(directory / "iteration-budget.json", budget)
        raise SystemExit("Dispatch blocked by iteration budget: dispatch count would exceed the configured maximum")
    if projected_tokens > int(budget.get("max_dispatch_reference_tokens") or 0):
        budget["status"] = "blocked"
        budget["stop_reason"] = "dispatch reference-token budget exhausted"
        write_json(directory / "iteration-budget.json", budget)
        raise SystemExit("Dispatch blocked by iteration budget: reference-token usage would exceed the configured maximum")
    return budget


def late_owned_session_model_readiness_recovery_pending(
    directory: Path,
    state: dict[str, Any],
    phases: list[tuple[str, str]] | None,
) -> bool:
    if not phases:
        return False
    task_id = str(state.get("task_id") or directory.name)
    model_block = read_json(directory / "model-identity-dispatch-block.json")
    if (
        model_block.get("status") != "blocked"
        or model_block.get("task_id") != task_id
        or model_block.get("reason") != "owned_session_model_readiness_timeout"
    ):
        return False

    target_work_items = {f"{role}:{agent}" for agent, role in phases}
    target_agents = {agent for agent, _role in phases}
    try:
        dispatch_receipts = load_dispatch_receipts(directory, task_id)
        session_receipts = read_json_lines_strict(directory / "agent-session-receipts.jsonl")
        timeline_events = read_json_lines_strict(directory / "timeline.jsonl")
    except SystemExit:
        return False
    for event in timeline_events:
        if event.get("event") != "dispatch_submit_failed":
            continue
        work_item_id = str(event.get("work_item_id") or "")
        if work_item_id in target_work_items:
            return False
        if not work_item_id and str(event.get("agent") or "") in target_agents:
            return False
    for receipt in dispatch_receipts:
        if receipt.get("event") not in DELIVERY_RECEIPT_EVENTS:
            continue
        work_item_id = str(receipt.get("work_item_id") or "")
        if work_item_id in target_work_items:
            return False
        if not work_item_id and str(receipt.get("agent") or "") in target_agents:
            return False

    projection = read_json(directory / "agent-sessions.json")
    if (
        projection.get("schema_version") != "valp-agent-sessions.v1"
        or projection.get("task_id") != task_id
        or projection.get("adapter") != "herdr"
        or projection.get("status") != "ready"
    ):
        return False
    bindings = projection.get("bindings")
    if not isinstance(bindings, dict):
        return False

    for agent in target_agents:
        binding = bindings.get(agent)
        if not isinstance(binding, dict):
            return False
        ownership = binding.get("ownership") or {}
        identity = binding.get("runtime_identity") or {}
        generation = binding.get("generation")
        if (
            binding.get("agent") != agent
            or binding.get("dispatch_eligible") is not True
            or binding.get("focused_at_provisioning") is not False
            or ownership.get("scope") != "task"
            or ownership.get("task_id") != task_id
            or type(generation) is not int
            or generation < 1
            or not str(identity.get("pane_id") or "")
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(identity.get("token") or ""))
        ):
            return False
        matching_receipts = [
            receipt
            for receipt in session_receipts
            if receipt.get("schema_version") == "valp-agent-session-receipt.v1"
            and receipt.get("adapter") == "herdr"
            and receipt.get("task_id") == task_id
            and receipt.get("agent") == agent
            and receipt.get("event") == "agent_session_provisioned"
            and receipt.get("binding_ref") == "agent-sessions.json"
            and receipt.get("generation") == generation
            and receipt.get("identity_token") == identity.get("token")
            and receipt.get("ownership") == ownership
            and receipt.get("context") == binding.get("context")
            and receipt.get("launch") == binding.get("launch")
            and receipt.get("runtime_scope") == binding.get("runtime_scope")
            and receipt.get("runtime_identity") == identity
            and receipt.get("focused_at_provisioning") is False
        ]
        if len(matching_receipts) != 1:
            return False
    return True


def runtime_dispatch_retry_pending(
    directory: Path,
    state: dict[str, Any],
    runtime_kind: str,
    phases: list[tuple[str, str]] | None = None,
) -> bool:
    budget = read_json(directory / "iteration-budget.json")
    if not (
        runtime_kind == "herdr"
        and state.get("status") in {"dispatching", "executing"}
        and budget.get("status") == "blocked"
    ):
        return False
    reason = budget.get("stop_reason")
    if reason in RUNTIME_DISPATCH_RETRYABLE_STOP_REASONS:
        return True
    if reason == "runtime dispatch retry exhausted":
        return late_owned_session_model_readiness_recovery_pending(
            directory,
            state,
            phases,
        )
    if reason != "dynamic model identity changed after routing":
        return False
    preflight = read_json(directory / "runtime-preflight.json")
    model_block = read_json(directory / "model-identity-dispatch-block.json")
    records = list((preflight.get("agents") or {}).values())
    return bool(
        model_block.get("status") == "blocked"
        and model_block.get("errors")
        and records
        and all(
            ((record.get("session_binding") or {}).get("status") == "bound")
            and ((record.get("model_probe") or {}).get("status") in {"unsupported", "unavailable"})
            for record in records
            if isinstance(record, dict)
        )
    )


def owned_session_launch_replacement_pending(
    directory: Path,
    state: dict[str, Any],
    phases: list[tuple[str, str]],
) -> bool:
    if len(phases) != 1 or state.get("status") != "dispatching":
        return False
    budget = read_json(directory / "iteration-budget.json")
    block = read_json(directory / "agent-session-block.json")
    if (
        budget.get("status") != "blocked"
        or budget.get("stop_reason") != "runtime dispatch retry exhausted"
        or block.get("status") != "blocked"
        or block.get("reason") != "HERDR task-owned session binding metadata conflicts"
    ):
        return False
    agent, role = phases[0]
    work_item_id = f"{role}:{agent}"
    try:
        receipts = load_dispatch_receipts(directory, str(state.get("task_id") or directory.name))
        timeline = read_json_lines_strict(directory / "timeline.jsonl")
    except SystemExit:
        return False
    if any(
        event.get("event") == "dispatch_submit_failed"
        and (
            event.get("work_item_id") == work_item_id
            or (
                not event.get("work_item_id")
                and event.get("agent") == agent
            )
        )
        for event in timeline
    ):
        return False
    return not any(
        receipt.get("event") in DELIVERY_RECEIPT_EVENTS
        and (
            receipt.get("work_item_id") == work_item_id
            or (
                not receipt.get("work_item_id")
                and receipt.get("agent") == agent
            )
        )
        for receipt in receipts
    )


def resume_runtime_dispatch_retry(
    directory: Path,
    routing: dict[str, Any],
    phases: list[tuple[str, str]],
    *,
    expected_stop_reason: str | None = None,
) -> dict[str, Any]:
    with task_state_lock(directory):
        state = read_json(directory / "state.json")
        budget = read_json(directory / "iteration-budget.json")
        retry_state_matches = (
            budget.get("status") == "blocked"
            and budget.get("stop_reason") == expected_stop_reason
        ) if expected_stop_reason else runtime_dispatch_retry_pending(directory, state, "herdr")
        if not retry_state_matches:
            raise SystemExit("Runtime dispatch retry state changed before recovery")
        budget["status"] = "active"
        budget["stop_reason"] = None
        write_json(directory / "iteration-budget.json", budget)
        budget = enforce_iteration_budget(directory, routing, state, phases)
        append_timeline_event(
            directory,
            "runtime_dispatch_retry_started",
            "Fresh runtime preflight passed; retrying the same dependency-ready work item once",
            work_item_ids=[f"{role}:{agent}" for agent, role in phases],
        )
        return budget


def record_verified_bootstrap_lifecycle(
    directory: Path,
    agent: str,
    evidence_ref: str,
) -> dict[str, Any]:
    relative_ref = Path(evidence_ref)
    if relative_ref.is_absolute() or ".." in relative_ref.parts:
        raise HerdrSubmissionError("Bootstrap verification evidence must be task-local")
    evidence_path = (directory / relative_ref).resolve()
    try:
        evidence_path.relative_to(directory.resolve())
    except ValueError as exc:
        raise HerdrSubmissionError("Bootstrap verification evidence must be task-local") from exc

    with task_state_lock(directory):
        projection = read_json_strict(directory / "agent-sessions.json")
        bindings = projection.get("bindings")
        binding = bindings.get(agent) if isinstance(bindings, dict) else None
        task_id = str(projection.get("task_id") or "").strip()
        if (
            projection.get("schema_version") != "valp-agent-sessions.v1"
            or projection.get("adapter") != "herdr"
            or projection.get("status") != "ready"
            or not task_id
            or not isinstance(binding, dict)
            or binding.get("agent") != agent
            or binding.get("lifecycle") != "provisioned"
            or binding.get("dispatch_eligible") is not True
            or ((binding.get("ownership") or {}).get("task_id") != task_id)
        ):
            raise HerdrSubmissionError("Bootstrap verification has no eligible task-owned session")

        evidence = read_json_strict(evidence_path)
        target = evidence.get("target") if isinstance(evidence.get("target"), dict) else {}
        native_turn = (
            evidence.get("native_turn")
            if isinstance(evidence.get("native_turn"), dict)
            else {}
        )
        runtime_after = (
            evidence.get("runtime_after")
            if isinstance(evidence.get("runtime_after"), dict)
            else {}
        )
        structured_observation = (
            evidence.get("structured_observation")
            if isinstance(evidence.get("structured_observation"), dict)
            else {}
        )
        response_proof = (
            evidence.get("response_proof")
            if isinstance(evidence.get("response_proof"), dict)
            else {}
        )
        runtime_before = (
            evidence.get("runtime_before")
            if isinstance(evidence.get("runtime_before"), dict)
            else {}
        )
        pane_id = str(((binding.get("runtime_identity") or {}).get("pane_id")) or "")
        native_session_id = str(target.get("native_session_id") or "").strip()
        task_complete_timestamps = structured_observation.get("task_complete_timestamps")
        normalized_response_proof = _normalize_herdr_bootstrap_response(
            response_proof.get("raw_matched_line")
        )
        renderer_response_proof = (
            normalized_response_proof is not None
            and normalized_response_proof[0] == native_turn.get("actual_response")
            and normalized_response_proof[1] == response_proof.get("renderer_envelope")
        )
        idle_native_completion = (
            runtime_after.get("agent_status") == "idle"
            and isinstance(task_complete_timestamps, list)
            and bool(task_complete_timestamps)
            and all(
                isinstance(timestamp, str) and bool(timestamp.strip())
                for timestamp in task_complete_timestamps
            )
            and native_turn.get("completed_turn_count") == len(task_complete_timestamps)
            and native_turn.get("aborted_turn_count") == 0
            and structured_observation.get("session_id") == native_session_id
            and all(
                _concrete_bootstrap_model_value(value)
                for value in (
                    native_turn.get("model"),
                    native_turn.get("provider"),
                    native_turn.get("reasoning_mode"),
                    structured_observation.get("model_id"),
                    structured_observation.get("provider"),
                    structured_observation.get("reasoning_mode"),
                )
            )
            and structured_observation.get("model_id") == native_turn.get("model")
            and structured_observation.get("provider") == native_turn.get("provider")
            and structured_observation.get("reasoning_mode")
            == native_turn.get("reasoning_mode")
        )
        atomic_idle_completion = (
            runtime_after.get("agent_status") == "idle"
            and response_proof.get("authority") == "response_only_not_identity_or_model"
            and renderer_response_proof
            and type(runtime_before.get("state_change_seq")) is int
            and type(runtime_after.get("state_change_seq")) is int
            and runtime_after["state_change_seq"] > runtime_before["state_change_seq"]
            and native_turn.get("completed_turn_count") == 1
            and native_turn.get("aborted_turn_count") == 0
            and structured_observation.get("session_id") == native_session_id
            and all(
                _concrete_bootstrap_model_value(value)
                for value in (
                    native_turn.get("model"),
                    native_turn.get("provider"),
                    native_turn.get("reasoning_mode"),
                    structured_observation.get("model_id"),
                    structured_observation.get("provider"),
                    structured_observation.get("reasoning_mode"),
                )
            )
            and structured_observation.get("model_id") == native_turn.get("model")
            and structured_observation.get("provider") == native_turn.get("provider")
            and structured_observation.get("reasoning_mode")
            == native_turn.get("reasoning_mode")
        )
        valid = all(
            (
                evidence.get("schema_version") == "valp-bootstrap-probe-result.v1",
                evidence.get("task_id") == task_id,
                evidence.get("classification") == "non_task_bootstrap_probe",
                evidence.get("accepted") is True,
                evidence.get("formal_dispatch_count") == 0,
                target.get("agent") == agent,
                target.get("generation") == binding.get("generation"),
                target.get("pane_id") == pane_id,
                bool(native_session_id),
                native_turn.get("expected_response") == "BOOTSTRAP_READY",
                native_turn.get("actual_response") == "BOOTSTRAP_READY",
                "error" in native_turn and native_turn.get("error") is None,
                runtime_after.get("agent_status") == "done"
                or idle_native_completion
                or atomic_idle_completion,
                runtime_after.get("readiness_status") == "ready",
                runtime_after.get("prompt_eligible") is True,
                runtime_after.get("session_identity_status") == "known",
                runtime_after.get("model_probe_status") == "observed",
            )
        )
        if not valid:
            raise HerdrSubmissionError(
                "Bootstrap verification evidence does not match the exact task-owned session"
            )

        verification = {
            "status": "verified",
            "evidence_ref": evidence_ref,
            "generation": binding["generation"],
            "pane_id": pane_id,
            "native_session_id": native_session_id,
            "expected_response": "BOOTSTRAP_READY",
            "actual_response": "BOOTSTRAP_READY",
            "native_turn_error": None,
            "session_identity_status": "known",
            "model_probe_status": "observed",
        }
        binding["lifecycle"] = "bootstrap_ready"
        binding["bootstrap_verification"] = verification
        projection["updated_at"] = now_iso()
        write_json(directory / "agent-sessions.json", projection)

        receipts_path = directory / "agent-session-receipts.jsonl"
        receipts = read_json_lines_strict(receipts_path)
        sequence = max(
            (
                int(record.get("event_sequence"))
                for record in receipts
                if type(record.get("event_sequence")) is int
            ),
            default=0,
        )
        append_json_line_durable(
            receipts_path,
            {
                "schema_version": "valp-agent-session-receipt.v1",
                "adapter": "herdr",
                "task_id": task_id,
                "event_sequence": sequence + 1,
                "ts": now_iso(),
                "agent": agent,
                "event": "agent_session_bootstrap_verified",
                "binding_ref": "agent-sessions.json",
                "generation": binding["generation"],
                "identity_token": (binding.get("runtime_identity") or {}).get("token"),
                "evidence_ref": evidence_ref,
                "native_session_id": native_session_id,
            },
        )
        return projection


def _bootstrap_readiness(
    herdr: str,
    pane_id: str,
    run_command_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    result = run_command_fn(
        [herdr, "agent", "readiness", pane_id],
        timeout=5.0,
    )
    response = parse_json_stdout(result)
    payload = response.get("result") if isinstance(response, dict) else None
    readiness = payload.get("readiness") if isinstance(payload, dict) else None
    if not (
        result.get("ok") is True
        and isinstance(payload, dict)
        and payload.get("type") == "agent_readiness"
        and isinstance(readiness, dict)
        and readiness.get("schema_version") == "valp-named-agent-readiness.v1"
        and type(readiness.get("state_change_seq")) is int
    ):
        raise HerdrSubmissionError("HERDR bootstrap readiness response is unavailable or malformed")
    return dict(readiness)


def _bootstrap_agent_info(
    result: dict[str, Any],
    *,
    expected_type: str,
    action: str,
) -> dict[str, Any]:
    response = parse_json_stdout(result)
    payload = response.get("result") if isinstance(response, dict) else None
    agent_info = payload.get("agent") if isinstance(payload, dict) else None
    if not (
        result.get("ok") is True
        and isinstance(payload, dict)
        and payload.get("type") == expected_type
        and isinstance(agent_info, dict)
        and all(
            isinstance(agent_info.get(field), str) and bool(agent_info[field].strip())
            for field in ("terminal_id", "name", "agent", "pane_id", "agent_status")
        )
        and type(agent_info.get("state_change_seq")) is int
    ):
        raise HerdrSubmissionError(f"{action} response is unavailable or malformed")
    return dict(agent_info)


HERDR_BOOTSTRAP_RESPONSE_REGEX = r"^(?:BOOTSTRAP_READY|• BOOTSTRAP_READY|⏺ BOOTSTRAP_READY)$"
HERDR_BOOTSTRAP_RESPONSE_ENVELOPES = {
    "BOOTSTRAP_READY": "bare",
    "• BOOTSTRAP_READY": "codex_list_marker",
    "⏺ BOOTSTRAP_READY": "claude_action_marker",
}
HERDR_BOOTSTRAP_MODEL_PLACEHOLDERS = {
    "none",
    "null",
    "unavailable",
    "unknown",
    "unsupported",
}


def _normalize_herdr_bootstrap_response(line: object) -> tuple[str, str] | None:
    if not isinstance(line, str):
        return None
    envelope = HERDR_BOOTSTRAP_RESPONSE_ENVELOPES.get(line)
    if envelope is None:
        return None
    return "BOOTSTRAP_READY", envelope


def _concrete_bootstrap_model_value(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.strip().lower() not in HERDR_BOOTSTRAP_MODEL_PLACEHOLDERS
    )


def bootstrap_task_owned_herdr_session(
    directory: Path,
    task_id: str,
    agent: str,
    binding: dict[str, Any],
    *,
    herdr: str = "herdr",
    run_command_fn: Callable[..., dict[str, Any]] = run_command,
    timeout_seconds: float = 30.0,
    poll_interval_seconds: float = 0.25,
) -> dict[str, Any]:
    """Bootstrap native session/model evidence without producing formal dispatch evidence."""

    if agent not in {"codex", "claude", "hermes"}:
        raise HerdrSubmissionError("HERDR bootstrap probe is unsupported for this Agent")
    if timeout_seconds < 0 or poll_interval_seconds < 0:
        raise HerdrSubmissionError("HERDR bootstrap probe timing must be non-negative")

    projection = read_json_strict(directory / "agent-sessions.json")
    bindings = projection.get("bindings")
    projected = bindings.get(agent) if isinstance(bindings, dict) else None
    if (
        projection.get("schema_version") != "valp-agent-sessions.v1"
        or projection.get("task_id") != task_id
        or projection.get("adapter") != "herdr"
        or projection.get("status") != "ready"
        or not isinstance(projected, dict)
        or projected != binding
    ):
        raise HerdrSubmissionError("HERDR bootstrap binding projection is missing or changed")
    if binding_has_verified_bootstrap_lifecycle(projected):
        evidence_ref = str((projected.get("bootstrap_verification") or {}).get("evidence_ref") or "")
        if not evidence_ref or not (directory / evidence_ref).is_file():
            raise HerdrSubmissionError("Verified HERDR bootstrap evidence is missing")
        return projection

    identity = projected.get("runtime_identity")
    ownership = projected.get("ownership")
    runtime_scope = projected.get("runtime_scope")
    generation = projected.get("generation")
    pane_id = str((identity or {}).get("pane_id") or "").strip()
    if (
        projected.get("agent") != agent
        or projected.get("lifecycle") != "provisioned"
        or projected.get("dispatch_eligible") is not True
        or type(generation) is not int
        or generation < 1
        or not isinstance(ownership, dict)
        or ownership.get("scope") != "task"
        or ownership.get("task_id") != task_id
        or not isinstance(identity, dict)
        or not pane_id
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(identity.get("token") or ""))
        or (
            isinstance(runtime_scope, dict)
            and runtime_scope.get("ownership") != "task"
        )
    ):
        raise HerdrSubmissionError("HERDR bootstrap has no exact eligible task-owned binding")

    contract_path = directory / CONTROL_CONTRACT_REF
    contract = read_json_strict(contract_path)
    contract_errors = validate_control_contract(contract, task_id)
    digest = control_contract_digest(contract, contract_path.read_bytes())
    slice_path = directory / "control-slices" / f"{agent}.json"
    control_slice = read_json_strict(slice_path)
    work_item_ids = control_slice.get("work_item_ids")
    if not isinstance(work_item_ids, list):
        work_item_ids = []
    slice_errors = validate_control_slice(
        control_slice,
        task_id,
        agent,
        work_item_ids,
        digest,
    )
    if contract_errors or slice_errors:
        raise HerdrSubmissionError(
            "HERDR bootstrap control contract or slice is invalid: "
            + "; ".join(contract_errors + slice_errors)
        )

    receipts = read_json_lines_strict(directory / "dispatch-receipts.jsonl")
    current_generation_receipts = []
    for receipt in receipts:
        if receipt.get("event") not in DELIVERY_RECEIPT_EVENTS:
            continue
        if receipt.get("agent") != agent:
            if not isinstance(receipt.get("agent"), str):
                raise HerdrSubmissionError(
                    "HERDR bootstrap cannot classify an unbound formal delivery receipt"
                )
            continue
        proof = receipt.get("proof") if isinstance(receipt.get("proof"), dict) else {}
        session_binding = (
            proof.get("session_binding")
            if isinstance(proof.get("session_binding"), dict)
            else {}
        )
        receipt_generation = session_binding.get("generation")
        if type(receipt_generation) is not int:
            raise HerdrSubmissionError(
                "HERDR bootstrap cannot classify a formal delivery receipt without binding generation"
            )
        if receipt_generation == generation:
            current_generation_receipts.append(receipt)
    if current_generation_receipts:
        raise HerdrSubmissionError(
            "HERDR bootstrap cannot follow a formal delivery receipt for the current binding generation"
        )
    formal_dispatch_count = len(current_generation_receipts)

    evidence_ref = f"evidence/bootstrap-probe-{agent}-g{generation}.json"
    evidence_path = directory / evidence_ref
    if evidence_path.exists():
        raise HerdrSubmissionError("HERDR bootstrap probe was already attempted without verification")

    readiness_before = _bootstrap_readiness(herdr, pane_id, run_command_fn)
    session_before = readiness_before.get("session_identity")
    session_before_identity = (
        session_before.get("identity")
        if isinstance(session_before, dict)
        and isinstance(session_before.get("identity"), dict)
        else {}
    )
    native_session_before = str(session_before_identity.get("value") or "").strip()
    common_ready = (
        readiness_before.get("addressable") is True
        and readiness_before.get("detected_agent") == agent
        and readiness_before.get("agent_status") in {"idle", "done"}
        and readiness_before.get("interactive_ready") is True
    )
    codex_bootstrap_ready = (
        agent == "codex"
        and common_ready
        and readiness_before.get("ready") is False
        and readiness_before.get("reason_code") == "session_identity_unknown"
        and readiness_before.get("prompt_eligible") is False
        and isinstance(session_before, dict)
        and session_before.get("status") == "unknown"
        and not session_before.get("identity")
    )
    hermes_bootstrap_ready = (
        agent == "hermes"
        and common_ready
        and readiness_before.get("ready") is False
        and readiness_before.get("reason_code") == "session_identity_unknown"
        and readiness_before.get("prompt_eligible") is False
        and isinstance(session_before, dict)
        and session_before.get("status") == "unknown"
        and not session_before.get("identity")
    )
    model_observation_bootstrap_ready = (
        agent == "claude"
        and common_ready
        and readiness_before.get("ready") is True
        and readiness_before.get("reason_code") == "ready"
        and readiness_before.get("prompt_eligible") is True
        and isinstance(session_before, dict)
        and session_before.get("status") == "known"
        and session_before_identity.get("source") == "herdr:claude"
        and session_before_identity.get("agent") == "claude"
        and session_before_identity.get("kind") == "id"
        and bool(native_session_before)
    )
    if not (
        codex_bootstrap_ready
        or hermes_bootstrap_ready
        or model_observation_bootstrap_ready
    ):
        raise HerdrSubmissionError("HERDR bootstrap readiness is not the exact session_identity_unknown state")

    if agent in {"claude", "hermes"}:
        model_before = herdr_model_probe_with_runner(
            herdr,
            pane_id,
            run_command_fn,
        )
        if (
            model_before.get("status") != "unsupported"
            or any(
                field in model_before
                for field in ("model", "session_identity", "observed_at")
            )
        ):
            raise HerdrSubmissionError(
                "HERDR model bootstrap requires an unobserved pre-turn model state"
            )

    baseline_result = run_command_fn(
        [herdr, "agent", "get", pane_id],
        timeout=5.0,
    )
    baseline = _bootstrap_agent_info(
        baseline_result,
        expected_type="agent_info",
        action="HERDR bootstrap baseline",
    )
    bound_terminal_id = str(identity.get("terminal_id") or "").strip()
    if (
        baseline.get("pane_id") != pane_id
        or baseline.get("agent") != agent
        or baseline.get("agent_status") not in {"idle", "done"}
        or baseline.get("state_change_seq") != readiness_before.get("state_change_seq")
        or (bound_terminal_id and baseline.get("terminal_id") != bound_terminal_id)
    ):
        raise HerdrSubmissionError("HERDR bootstrap baseline does not match the bound settled session")

    prior_output = run_command_fn(
        [herdr, "pane", "read", pane_id, "--source", "recent-unwrapped", "--lines", "200"],
        timeout=5.0,
        stdout_limit=32768,
    )
    if prior_output.get("ok") is not True:
        raise HerdrSubmissionError("HERDR bootstrap could not inspect pre-existing pane output")
    if any(
        _normalize_herdr_bootstrap_response(line) is not None
        for line in str(prior_output.get("stdout") or "").splitlines()
    ):
        raise HerdrSubmissionError("HERDR bootstrap rejects a pre-existing BOOTSTRAP_READY response")

    compact_slice = json.dumps(
        control_slice,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    prompt = (
        "[VALP CONTROL SLICE]\n"
        + compact_slice
        + "\n[/VALP CONTROL SLICE]\n"
        "This is a one-time non-task bootstrap probe. Do not perform task work.\n"
        "Resolve control_contract_ref relative to this task directory: "
        + str(directory.resolve())
        + "\n"
        "Load and honor the control slice, then respond with exactly BOOTSTRAP_READY and nothing else.\n"
    )
    write_json(
        evidence_path,
        {
            "schema_version": "valp-bootstrap-probe-result.v1",
            "task_id": task_id,
            "target": {"agent": agent, "pane_id": pane_id, "generation": generation},
            "classification": "non_task_bootstrap_probe",
            "formal_dispatch_count": formal_dispatch_count,
            "accepted": False,
            "status": "attempt_recorded",
            "control_contract": {"ref": CONTROL_CONTRACT_REF, "digest": digest},
            "control_slice_ref": f"control-slices/{agent}.json",
        },
    )

    timeout_ms = max(1, int(timeout_seconds * 1000))
    prompt_result = run_command_fn(
        [
            herdr,
            "agent",
            "prompt",
            pane_id,
            prompt,
            "--wait",
            "--until",
            "done",
            "--until",
            "idle",
            "--timeout",
            str(timeout_ms),
        ],
        timeout=max(5.0, timeout_seconds + 1.0),
    )
    prompted = _bootstrap_agent_info(
        prompt_result,
        expected_type="agent_prompted",
        action="atomic HERDR bootstrap prompt",
    )
    if (
        any(prompted.get(field) != baseline.get(field) for field in ("terminal_id", "name", "agent", "pane_id"))
        or prompted.get("agent_status") not in {"idle", "done"}
        or prompted.get("state_change_seq") <= baseline.get("state_change_seq")
    ):
        raise HerdrSubmissionError("Atomic HERDR bootstrap prompt did not settle on the bound advanced session")

    response_result = run_command_fn(
        [
            herdr,
            "pane",
            "wait-output",
            pane_id,
            "--regex",
            HERDR_BOOTSTRAP_RESPONSE_REGEX,
            "--source",
            "recent-unwrapped",
            "--lines",
            "200",
            "--timeout",
            str(timeout_ms),
        ],
        timeout=max(5.0, timeout_seconds + 1.0),
        stdout_limit=32768,
    )
    response = parse_json_stdout(response_result)
    response_payload = response.get("result") if isinstance(response, dict) else None
    matched_line = response_payload.get("matched_line") if isinstance(response_payload, dict) else None
    normalized_response = _normalize_herdr_bootstrap_response(matched_line)
    if not (
        response_result.get("ok") is True
        and isinstance(response_payload, dict)
        and response_payload.get("type") == "output_matched"
        and normalized_response is not None
        and response_payload.get("pane_id") in {None, pane_id}
    ):
        raise HerdrSubmissionError("HERDR bootstrap exact response was not proven")
    normalized_line, renderer_envelope = normalized_response

    deadline = time.monotonic() + timeout_seconds
    readiness_after: dict[str, Any] | None = None
    model_probe: dict[str, Any] | None = None
    native_session_id = ""
    while True:
        candidate_readiness = _bootstrap_readiness(herdr, pane_id, run_command_fn)
        candidate_session = candidate_readiness.get("session_identity")
        candidate_identity = (
            candidate_session.get("identity")
            if isinstance(candidate_session, dict)
            and isinstance(candidate_session.get("identity"), dict)
            else {}
        )
        candidate_native_id = str(candidate_identity.get("value") or "").strip()
        candidate_probe = herdr_model_probe_with_runner(
            herdr,
            pane_id,
            run_command_fn,
        )
        probe_session = candidate_probe.get("session_identity")
        probe_freshness, _probe_age = observation_freshness(
            candidate_probe.get("observed_at"),
            evaluated_at=now_iso(),
            ttl_seconds=bounded_observation_ttl(candidate_probe.get("ttl_seconds")),
        )
        native_session_digest = hashlib.sha256(
            candidate_native_id.encode("utf-8")
        ).hexdigest()
        same_session = (
            isinstance(probe_session, dict)
            and probe_session.get("status") == "known"
            and probe_session.get("token") == f"sha256:{native_session_digest}"
            and probe_session.get("generation")
            == f"session:{native_session_digest[:16]}"
        )
        same_native_session = (
            not native_session_before or candidate_native_id == native_session_before
        )
        if (
            candidate_readiness.get("ready") is True
            and candidate_readiness.get("reason_code") == "ready"
            and candidate_readiness.get("addressable") is True
            and candidate_readiness.get("detected_agent") == agent
            and candidate_readiness.get("agent_status") in {"idle", "done"}
            and candidate_readiness.get("interactive_ready") is True
            and candidate_readiness.get("prompt_eligible") is True
            and isinstance(candidate_session, dict)
            and candidate_session.get("status") == "known"
            and candidate_native_id
            and candidate_readiness.get("state_change_seq") > baseline.get("state_change_seq")
            and candidate_probe.get("status") == "observed"
            and probe_freshness == "current"
            and same_session
            and same_native_session
        ):
            readiness_after = candidate_readiness
            model_probe = candidate_probe
            native_session_id = candidate_native_id
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise HerdrSubmissionError("HERDR bootstrap did not produce matching native session and model evidence")
        time.sleep(min(poll_interval_seconds, remaining))

    model = model_probe.get("model") if isinstance(model_probe.get("model"), dict) else {}
    if not all(
        _concrete_bootstrap_model_value(model.get(field))
        for field in ("model_id", "provider", "reasoning_mode")
    ):
        raise HerdrSubmissionError("HERDR bootstrap structured model observation is incomplete")
    evidence = {
        "schema_version": "valp-bootstrap-probe-result.v1",
        "task_id": task_id,
        "target": {
            "agent": agent,
            "pane_id": pane_id,
            "generation": generation,
            "native_session_id": native_session_id,
        },
        "classification": "non_task_bootstrap_probe",
        "control_contract": {"ref": CONTROL_CONTRACT_REF, "digest": digest},
        "control_slice_ref": f"control-slices/{agent}.json",
        "native_turn": {
            "expected_response": "BOOTSTRAP_READY",
            "actual_response": normalized_line,
            "error": None,
            "model": model["model_id"],
            "provider": model["provider"],
            "reasoning_mode": model["reasoning_mode"],
            "completed_turn_count": 1,
            "aborted_turn_count": 0,
        },
        "response_proof": {
            "source": "HERDR pane wait-output renderer-aware anchored exact-line match",
            "authority": "response_only_not_identity_or_model",
            "raw_matched_line": matched_line,
            "renderer_envelope": renderer_envelope,
        },
        "runtime_before": readiness_before,
        "runtime_after": {
            "agent_status": readiness_after["agent_status"],
            "readiness_status": readiness_after["reason_code"],
            "prompt_eligible": readiness_after["prompt_eligible"],
            "session_identity_status": readiness_after["session_identity"]["status"],
            "state_change_seq": readiness_after["state_change_seq"],
            "model_probe_status": model_probe["status"],
        },
        "structured_observation": {
            "session_id": native_session_id,
            "model_id": model["model_id"],
            "provider": model["provider"],
            "reasoning_mode": model["reasoning_mode"],
            "observed_at": model_probe.get("observed_at"),
            "task_complete_timestamps": [],
        },
        "formal_dispatch_count": formal_dispatch_count,
        "accepted": True,
    }
    write_json(evidence_path, evidence)
    return record_verified_bootstrap_lifecycle(directory, agent, evidence_ref)


def await_owned_session_model_preflight(
    agent_names: list[str],
    runtime_kind: str,
    session_bindings: dict[str, Any],
    initial_preflight: dict[str, Any],
    *,
    version_command_by_agent: dict[str, list[str]] | None = None,
    max_attempts: int | None = None,
    interval_seconds: float = 0.25,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    def with_task_owned_launch_observations(preflight_record: dict[str, Any]) -> dict[str, Any]:
        records = preflight_record.get("agents") or {}
        for agent in agent_names:
            record = records.get(agent) if isinstance(records, dict) else None
            binding = session_bindings.get(agent)
            if not isinstance(record, dict) or not isinstance(binding, dict):
                continue
            attestation = launch_attestation_from_task_owned_binding(
                agent,
                binding,
                record.get("model_probe"),
            )
            if attestation is not None:
                record["launch_attestation"] = attestation
        return preflight_record

    def pending_agents(preflight_record: dict[str, Any]) -> list[str]:
        pending: list[str] = []
        for agent in agent_names:
            agent_record = ((preflight_record.get("agents") or {}).get(agent) or {})
            probe = agent_record.get("model_probe") or {}
            agent_status = str(agent_record.get("agent_status") or "unknown").strip().lower()
            accepted_status = agent_status in {"idle", "working"} or (
                agent_status == "done"
                and binding_has_verified_bootstrap_lifecycle(session_bindings.get(agent))
            )
            if (
                probe.get("status") != "observed"
                or (probe.get("session_identity") or {}).get("status") != "known"
                or not accepted_status
            ):
                pending.append(agent)
        return pending

    preflight = with_task_owned_launch_observations(initial_preflight)
    attempts = 1
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        pending = pending_agents(preflight)
        if not pending:
            break
        if max_attempts is not None and attempts >= max_attempts:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval_seconds, remaining))
        preflight = collect_runtime_preflight(
            agent_names,
            runtime=runtime_kind,
            session_bindings=session_bindings,
            version_command_by_agent=version_command_by_agent,
        )
        preflight = with_task_owned_launch_observations(preflight)
        attempts += 1
    pending = pending_agents(preflight)
    preflight.setdefault("checks", {})["owned_session_model_readiness"] = {
        "status": "pass" if not pending else "warn",
        "attempts": attempts,
        "pending_agents": pending,
    }
    return preflight


def targeted_session_bindings(
    session_projection: dict[str, Any],
    targets: list[str],
) -> dict[str, Any]:
    bindings = session_projection.get("bindings") or {}
    return {
        agent: bindings[agent]
        for agent in targets
        if isinstance(bindings.get(agent), dict)
    }


def merge_task_owned_runtime_preflight(
    previous: dict[str, Any],
    current: dict[str, Any],
    selected_agents: list[str],
    task_id: str,
) -> dict[str, Any]:
    previous_agents = previous.get("agents") if isinstance(previous.get("agents"), dict) else {}
    current_agents = current.get("agents") if isinstance(current.get("agents"), dict) else {}
    merged_agents: dict[str, Any] = {}

    for agent in selected_agents:
        current_record = current_agents.get(agent)
        if isinstance(current_record, dict):
            merged_agents[agent] = current_record
            continue
        previous_record = previous_agents.get(agent)
        if not isinstance(previous_record, dict):
            continue
        binding = previous_record.get("session_binding")
        ownership = binding.get("ownership") if isinstance(binding, dict) else {}
        if (
            isinstance(binding, dict)
            and binding.get("status") == "bound"
            and isinstance(ownership, dict)
            and ownership.get("task_id") == task_id
        ):
            merged_agents[agent] = previous_record

    pending_agents = []
    for agent in selected_agents:
        record = merged_agents.get(agent) or {}
        probe = record.get("model_probe") or {}
        session = probe.get("session_identity") or {}
        if probe.get("status") != "observed" or session.get("status") != "known":
            pending_agents.append(agent)

    checks = dict(current.get("checks") or {})
    checks["owned_session_model_readiness"] = {
        "status": "pass" if not pending_agents else "warn",
        "pending_agents": pending_agents,
    }
    statuses = [
        str(record.get("status") or "warn")
        for record in merged_agents.values()
        if isinstance(record, dict)
    ]
    if current.get("status") == "fail" or "fail" in statuses:
        status = "fail"
    elif pending_agents or current.get("status") == "warn" or "warn" in statuses:
        status = "warn"
    else:
        status = "pass"

    return {
        **current,
        "status": status,
        "checks": checks,
        "agents": merged_agents,
    }


def provider_reachable_match(agent: str, match: dict[str, Any]) -> bool:
    if not match.get("installed"):
        return False
    path = str(match.get("path") or "")
    if not skill_visible_to_agent(agent, path):
        return False
    reachability = match.get("provider_reachability")
    if isinstance(reachability, dict):
        reachable_agent = str(reachability.get("agent") or "any")
        if reachable_agent not in {"any", agent} or reachability.get("reachable") is False:
            return False
    return True


def compact_skill_slice(
    task_id: str,
    agent: str,
    skill_recommendations: dict[str, Any],
) -> dict[str, Any]:
    per_agent = skill_recommendations.get("per_agent") or {}
    source = per_agent.get(agent) if isinstance(per_agent, dict) else None
    if not isinstance(source, dict):
        source = skill_recommendations
    recommendations: list[dict[str, Any]] = []
    omitted = 0
    for result in source.get("results") or []:
        task = bounded_text(str(result.get("task") or ""), 120)
        decision = str((result.get("routing") or {}).get("decision") or "unknown")
        for match in result.get("matches") or []:
            if not provider_reachable_match(agent, match):
                omitted += 1
                continue
            recommendations.append({
                "skill": str(match.get("skill") or "unknown"),
                "task": task,
                "confidence": float(match.get("confidence") or 0),
                "provider": str(match.get("provider") or "unknown"),
                "path": str(match.get("path") or "unknown"),
                "decision": decision,
            })
    recommendations = recommendations[:8]
    missing = [
        str(item.get("skill") or "unknown")
        for item in source.get("missing_skills") or []
        if isinstance(item, dict)
    ][:4]
    return {
        "schema_version": "valp-skill-recommendation-slice.v1",
        "task_id": task_id,
        "agent": agent,
        "status": str(source.get("status") or skill_recommendations.get("status") or "not_run"),
        "source_ref": "skill-recommendations.json",
        "provider_reachable": bool(recommendations) or str(source.get("status")) == "no_matches",
        "recommendations": recommendations,
        "missing_skills": missing,
        "omitted_match_count": omitted,
        "generated_at": now_iso(),
    }


def write_skill_slices(
    directory: Path,
    task_id: str,
    selected_agents: list[str],
    skill_recommendations: dict[str, Any],
) -> dict[str, str]:
    refs: dict[str, str] = {}
    for agent in selected_agents:
        relative = f"skill-slices/{agent}.json"
        write_json(directory / relative, compact_skill_slice(task_id, agent, skill_recommendations))
        refs[agent] = relative
    return refs


def ensure_control_contract(directory: Path, task_id: str) -> tuple[dict[str, Any], str]:
    path = directory / CONTROL_CONTRACT_REF
    if path.exists():
        contract = read_json_strict(path)
        errors = validate_control_contract(contract, task_id)
        if errors:
            raise SystemExit("Invalid worker control contract: " + "; ".join(errors))
        return contract, control_contract_digest(contract, path.read_bytes())
    contract = build_control_contract(task_id, now_iso())
    write_json(path, contract)
    return contract, control_contract_digest(contract, path.read_bytes())


def work_item_ids_by_agent(submission_dependencies: dict[str, Any]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for item in submission_dependencies.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        agent = str(item.get("agent") or "")
        work_item_id = str(item.get("work_item_id") or "")
        if agent and work_item_id:
            grouped.setdefault(agent, []).append(work_item_id)
    return {agent: list(dict.fromkeys(work_items)) for agent, work_items in grouped.items()}


def write_control_slices(
    directory: Path,
    task_id: str,
    selected_agents: list[str],
    work_items: dict[str, list[str]],
    contract_digest: str,
) -> dict[str, str]:
    refs: dict[str, str] = {}
    for agent in selected_agents:
        relative = f"control-slices/{agent}.json"
        write_json(
            directory / relative,
            build_control_slice(task_id, agent, work_items.get(agent) or [], contract_digest),
        )
        refs[agent] = relative
    return refs


def skill_task_label(task: str, index: int) -> str:
    label = bounded_text(task, SKILL_TASK_LABEL_CHAR_LIMIT)
    return label or f"work-item-{index}"


def decompose_execution_tasks(prompt: str, profile: str) -> list[str]:
    cleaned = extract_goal_text(prompt)
    candidates: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if re.match(r"^[-*]\s+", line):
            candidates.append(line[1:].strip())
            continue
        numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if numbered:
            candidates.append(numbered.group(1).strip())
    if not candidates and cleaned:
        candidates = [cleaned]
    if not candidates:
        candidates = [cleaned]

    profile_tasks = {
        "software-code": [
            "inspect the requested code change and identify implementation risks",
            "implement the scoped code change",
            "run build, lint, or tests and write verification evidence",
        ],
        "apple-app": [
            "inspect Swift or Apple app implementation risks",
            "implement the scoped Apple app change when approved",
            "run build and UI verification evidence for the Apple app",
        ],
        "web-frontend": [
            "inspect frontend UI and responsive behavior",
            "implement the scoped frontend change",
            "run browser or Playwright verification evidence",
        ],
        "research": [
            "research sources and capture citations",
            "compare evidence and write synthesis",
        ],
        "agent-runtime": [
            "inspect agent runtime, routing, or connector behavior",
            "verify runtime preflight, dispatch, receipts, and evidence",
        ],
    }
    candidates.extend(profile_tasks.get(profile, ["analyze the task and write evidence"]))

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        compact = " ".join(item.split())
        key = compact.lower()
        if compact and key not in seen:
            seen.add(key)
            deduped.append(compact)
    return deduped[:8]


def skill_router_command() -> list[str] | None:
    configured = os.environ.get("VALP_SKILL_ROUTER")
    if configured:
        return configured.split()
    found = shutil.which("task-skill-router")
    if found:
        return [found]
    local = Path.home() / ".local" / "bin" / "task-skill-router"
    if local.exists():
        return [str(local)]
    return None


def run_skill_recommendations(root: Path, task_id: str, profile: str, prompt: str) -> dict[str, Any]:
    tasks = decompose_execution_tasks(prompt, profile)
    command = skill_router_command()
    base = {
        "schema_version": "valp-skill-recommendations.v1",
        "task_id": task_id,
        "profile": profile,
        "execution_tasks": tasks,
        "generated_at": now_iso(),
    }
    if not command:
        return {
            **base,
            "status": "unavailable",
            "backend": "task-skill-router",
            "reason": "task-skill-router command was not found on PATH.",
            "results": [],
            "missing_skills": [],
        }

    result = run_command(
        skill_router_batch_command(command),
        timeout=30.0,
        input_text="\n".join(tasks) + "\n",
        stdout_limit=250000,
        stderr_limit=8000,
    )
    parsed = parse_json_stdout(result)
    if not parsed:
        return {
            **base,
            "status": "failed",
            "backend": "task-skill-router",
            "command": result.get("command"),
            "exit_code": result.get("exit_code"),
            "reason": "task-skill-router did not return parseable JSON.",
            "stderr": result.get("stderr", ""),
            "results": [],
            "missing_skills": [],
        }

    status = "complete" if parsed.get("results") else "no_matches"
    return {
        **base,
        "status": status,
        "backend": "task-skill-router",
        "command": result.get("command"),
        "exit_code": result.get("exit_code"),
        "routing": parsed.get("routing") or {},
        "results": parsed.get("results") or [],
        "missing_skills": parsed.get("missing_skills") or [],
        "raw": parsed,
    }


def skill_router_batch_command(command: list[str], agent: str | None = None) -> list[str]:
    if agent:
        return command + ["--agent", agent, "--batch"]
    return command + ["--batch"]


def add_per_agent_skill_recommendations(
    skill_recommendations: dict[str, Any],
    selected_agents: list[str],
) -> dict[str, Any]:
    if skill_recommendations.get("status") not in {"complete", "no_matches"}:
        return skill_recommendations
    command = skill_router_command()
    tasks = skill_recommendations.get("execution_tasks") or []
    if not command or not tasks:
        return skill_recommendations

    per_agent: dict[str, Any] = {}
    for agent in selected_agents:
        result = run_command(
            skill_router_batch_command(command, agent=agent),
            timeout=30.0,
            input_text="\n".join(str(task) for task in tasks) + "\n",
            stdout_limit=250000,
            stderr_limit=8000,
        )
        parsed = parse_json_stdout(result)
        if not parsed:
            per_agent[agent] = {
                "status": "failed",
                "backend": "task-skill-router",
                "agent": agent,
                "command": result.get("command"),
                "exit_code": result.get("exit_code"),
                "reason": "task-skill-router did not return parseable JSON for this agent.",
                "stderr": result.get("stderr", ""),
                "results": [],
                "missing_skills": [],
            }
            continue
        per_agent[agent] = {
            "status": "complete" if parsed.get("results") else "no_matches",
            "backend": "task-skill-router",
            "agent": agent,
            "command": result.get("command"),
            "exit_code": result.get("exit_code"),
            "routing": parsed.get("routing") or {},
            "results": parsed.get("results") or [],
            "missing_skills": parsed.get("missing_skills") or [],
            "raw": parsed,
        }

    statuses = {record.get("status") for record in per_agent.values()}
    skill_recommendations["per_agent"] = per_agent
    skill_recommendations["agent_filtering"] = {
        "status": "complete" if statuses <= {"complete", "no_matches"} else "partial",
        "backend": "task-skill-router",
        "agents": selected_agents,
        "note": "Per-agent recommendations are generated with task-skill-router --agent and should be preferred in dispatch prompts.",
    }
    return skill_recommendations


def classify_loop_layer(prompt: str, profile: str) -> str:
    lowered = prompt.lower()
    external_terms = [
        "external feedback",
        "user feedback",
        "alpha",
        "beta",
        "a/b",
        "ab test",
        "analytics",
        "production feedback",
        "真实用户",
        "外部反馈",
        "用户反馈",
        "上线反馈",
    ]
    developer_terms = [
        "ui",
        "ux",
        "design",
        "product",
        "flow",
        "visual",
        "prototype",
        "spec",
        "用户流程",
        "视觉",
        "交互",
        "产品",
    ]
    if any(term in lowered for term in external_terms):
        return "external_feedback_loop"
    if profile in UI_ATTENTION_PROFILES or any(term in lowered for term in developer_terms):
        return "developer_feedback_loop"
    return "agentic_coding_loop"


def relative_ref(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def find_design_contract(root: Path) -> dict[str, Any]:
    for relative in ["DESIGN.md", ".stitch/DESIGN.md"]:
        candidate = root / relative
        if candidate.exists():
            return {
                "status": "present",
                "path": relative,
                "recommended_lint": f"npx @google/design.md lint {relative}",
            }
    return {
        "status": "missing",
        "path": None,
        "reason": "No DESIGN.md or .stitch/DESIGN.md found in the workspace.",
    }


def context_selection_for(root: Path, directory: Path, profile: str, loop_layer: str) -> dict[str, Any]:
    selected: list[dict[str, Any]] = []
    not_selected: list[dict[str, Any]] = []

    def add_if_exists(relative: str, reason: str) -> None:
        path = root / relative
        if path.exists():
            selected.append({"path": relative, "reason": reason})

    selected.append({"path": relative_ref(directory / "task.md", root), "reason": "active task brief"})
    add_if_exists("AGENTS.md", "project operating rules")
    add_if_exists("DESIGN.md", "project visual contract")
    add_if_exists(".stitch/DESIGN.md", "project visual contract")
    add_if_exists("Package.swift", "SwiftPM build surface")
    add_if_exists("package.json", "frontend or JavaScript build surface")
    add_if_exists("pyproject.toml", "Python build/test surface")
    add_if_exists(".herdr-loop/local-overlay.json", "workspace-local routing overlay")
    add_if_exists(".herdr-loop/agents/capabilities.json", "workspace agent capability scan")

    tasks_root = root / ".herdr-loop" / "tasks"
    if tasks_root.exists():
        for task_path in sorted((p for p in tasks_root.iterdir() if p.is_dir()), key=lambda p: p.name)[-8:]:
            if task_path.resolve() == directory.resolve():
                continue
            not_selected.append(
                {
                    "path": relative_ref(task_path, root),
                    "reason": "prior task context is excluded unless explicitly cited by the active task",
                }
            )

    return {
        "schema_version": "valp-context-selection.v1",
        "generated_at": now_iso(),
        "profile": profile,
        "loop_layer": loop_layer,
        "selected": selected,
        "not_selected": not_selected,
    }


def safe_history_task_id(task_id: str) -> bool:
    return bool(task_id) and task_id not in {".", ".."} and "/" not in task_id and "\\" not in task_id


def safe_task_evidence_ref(ref: str) -> bool:
    if not isinstance(ref, str) or not ref or ref.startswith(("/", "\\")):
        return False
    if "\\" in ref or ":" in ref:
        return False
    parts = ref.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def task_evidence_exists(directory: Path, ref: str) -> bool:
    if not safe_task_evidence_ref(ref):
        return False
    try:
        candidate = (directory / ref).resolve()
        candidate.relative_to(directory.resolve())
    except (OSError, ValueError):
        return False
    return candidate.exists()


def trusted_routing_feedback(root: Path, indexed: dict[str, Any]) -> dict[str, Any]:
    task_id = str(indexed.get("task_id") or "")
    if indexed.get("schema_version") != "valp-routing-feedback.v1" or not safe_history_task_id(task_id):
        return {}

    tasks_root = root / ".herdr-loop" / "tasks"
    directory = tasks_root / task_id
    try:
        directory.resolve().relative_to(tasks_root.resolve())
    except (OSError, ValueError):
        return {}
    task_feedback = read_json(directory / "routing-feedback.json")
    state = read_json(directory / "state.json")
    if not task_feedback or not state:
        return {}

    identity_fields = ["schema_version", "task_id", "profile", "result", "selected_agents"]
    if any(indexed.get(field) != task_feedback.get(field) for field in identity_fields):
        return {}
    if state.get("task_id") != task_id:
        return {}

    result = str(task_feedback.get("result") or "").lower()
    if result == "done":
        gates = state.get("gates") or {}
        required_gates = {
            "dispatch_receipts": "passed",
            "expected_evidence": "passed",
            "verification": "passed",
            "review": "passed",
        }
        if state.get("status") != "done" or any(gates.get(name) != status for name, status in required_gates.items()):
            return {}
        if gates.get("approval") not in {"passed", "not_required"}:
            return {}
        if task_feedback.get("verification_result") != "passed" or task_feedback.get("review_result") != "passed":
            return {}
        actual_evidence = task_feedback.get("actual_evidence") or []
        if not isinstance(actual_evidence, list) or not actual_evidence:
            return {}
        for raw_ref in actual_evidence:
            if not task_evidence_exists(directory, str(raw_ref)):
                return {}

    trusted = dict(task_feedback)
    trusted["_history_source_ref"] = f".herdr-loop/tasks/{task_id}/routing-feedback.json"
    return trusted


def load_routing_feedback_history(root: Path, limit: int = 40) -> list[dict[str, Any]]:
    feedback_path = root / ".herdr-loop" / "routing-feedback.jsonl"
    if not feedback_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in feedback_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            trusted = trusted_routing_feedback(root, data)
            if trusted:
                records.append(trusted)
    return records[-limit:]


def feedback_prior_for_agent(agent: str, profile: str, feedback_history: list[dict[str, Any]]) -> dict[str, Any]:
    score = 0.6
    notes: list[str] = []
    refs: list[str] = []
    relevant = [
        record
        for record in feedback_history
        if agent in [str(item) for item in (record.get("selected_agents") or [])]
    ]
    for record in relevant[-8:]:
        same_profile = record.get("profile") == profile
        weight = 0.08 if same_profile else 0.03
        result = str(record.get("result") or "").lower()
        source_ref = str(record.get("_history_source_ref") or "")
        if source_ref:
            refs.append(source_ref)
        if result == "done":
            score += weight
            notes.append(f"{agent} has evidence-backed prior done feedback" + (" for this profile" if same_profile else ""))
        elif result in {"failed", "blocked", "partial"}:
            score -= weight * 1.5
            notes.append(f"{agent} has prior {result} feedback" + (" for this profile" if same_profile else ""))
        if record.get("context_gaps"):
            score -= 0.02
            notes.append(f"{agent} had prior context gaps")
    return {
        "score": round(max(0.2, min(0.9, score)), 2),
        "notes": list(dict.fromkeys(notes[-5:])),
        "refs": list(dict.fromkeys(refs[-5:])),
    }


def context_pack_for(
    root: Path,
    directory: Path,
    task_id: str,
    profile: str,
    loop_layer: str,
    selected_agents: list[str],
    role_assignments: dict[str, str],
    context_selection: dict[str, Any],
    feedback_history: list[dict[str, Any]],
    control_contract_record: dict[str, str],
) -> dict[str, Any]:
    selected_refs = [str(item.get("path")) for item in (context_selection.get("selected") or []) if item.get("path")]
    items: list[dict[str, Any]] = [
        {
            "section": "project",
            "summary": "Load and validate the immutable highest-runtime-control contract first, then load the active task and project rules; block on missing or mismatched control evidence.",
            "evidence_refs": [
                control_contract_record["ref"],
                *[ref for ref in selected_refs if ref.endswith("task.md") or ref == "AGENTS.md"][:3],
            ],
            "recipient_agents": selected_agents,
        },
        {
            "section": "task_scope",
            "summary": "Stay inside the task brief, expected evidence refs, visible routing, and permission boundary recorded for this task.",
            "evidence_refs": [
                relative_ref(directory / "task.md", root),
                relative_ref(directory / "visible-routing.md", root),
            ],
            "recipient_agents": selected_agents,
        },
        {
            "section": "verification",
            "summary": "Completion claims require concrete files, command output, screenshots, receipts, reviews, or gate evidence.",
            "evidence_refs": [
                relative_ref(directory / "evidence-board.json", root),
                relative_ref(directory / "dispatch-receipts.jsonl", root),
            ],
            "recipient_agents": selected_agents,
        },
        {
            "section": "permission_boundary",
            "summary": "Do not bypass approval gates or expand into release, auth, secrets, destructive, privacy, signing, migration, memory, or agent-configuration changes.",
            "evidence_refs": [relative_ref(directory / "automation-policy.json", root), relative_ref(directory / "state.json", root)],
            "recipient_agents": selected_agents,
        },
    ]
    recent_context_gaps: list[str] = []
    recent_refs: list[str] = []
    for record in feedback_history[-10:]:
        gaps = [str(item) for item in (record.get("context_gaps") or [])]
        if gaps:
            recent_context_gaps.extend(gaps)
            task_id_ref = record.get("task_id")
            if task_id_ref:
                recent_refs.append(f".herdr-loop/tasks/{task_id_ref}/routing-feedback.json")
    if recent_context_gaps:
        items.append(
            {
                "section": "known_pitfalls",
                "summary": "Prior feedback reported context gaps: " + "; ".join(list(dict.fromkeys(recent_context_gaps))[:3]),
                "evidence_refs": list(dict.fromkeys(recent_refs))[:5],
                "recipient_agents": selected_agents,
            }
        )
    items.append(
        {
            "section": "routing_prior",
            "summary": "Historical feedback is a routing prior only; current scan, tools, permissions, context, approvals, and expected evidence override it.",
            "evidence_refs": list(dict.fromkeys(recent_refs))[:5],
            "recipient_agents": selected_agents,
        }
    )
    return {
        "schema_version": "valp-context-pack.v1",
        "task_id": task_id,
        "profile": profile,
        "loop_layer": loop_layer,
        "generated_at": now_iso(),
        "budget": {"target_tokens": 500, "target_chars": 2400},
        "dispatch_role_budgets": {
            agent: dispatch_budget_for_agent(agent, role_assignments)
            for agent in selected_agents
        },
        "sources": [
            {"ref": ref, "reason": "selected visible context"}
            for ref in selected_refs[:10]
        ],
        "items": items,
        "excluded": [
            {"item": "raw private transcript", "reason": "not task-local evidence"},
            {"item": "stale memory without evidence refs", "reason": "cannot override current scan"},
        ],
        "privacy_notes": ["Context pack stores summaries and refs, not secrets or hidden conversations."],
    }


def automation_policy_for(
    task_id: str,
    runtime_adapter: dict[str, Any],
    approval_risks: list[dict[str, Any]],
    trigger_policy_ref: str | None = None,
) -> dict[str, Any]:
    runtime_class = str(runtime_adapter.get("class") or "")
    mode = "manual" if runtime_class == "manual" else "runtime_auto"
    risk_classification = "high" if approval_risks else "low"
    approval_required = bool(approval_risks)
    selected_action = "block_for_approval" if approval_required else "continue_until_gate"
    allowed = ["publish", "scan", "route", "build_context_pack"]
    if not approval_required:
        allowed.extend(["dispatch", "collect_evidence", "verify", "review", "synthesize", "audit", "write_learning_feedback"])
    blocked = []
    if approval_required:
        blocked.extend(["dispatch_side_effects", "release", "auth", "secrets", "destructive_changes", "memory_or_agent_config"])
    audit_grade = "local" if runtime_class == "manual" else "runtime"
    basis: list[dict[str, Any]] = [
        {
            "kind": "runtime",
            "ref": "routing.json#runtime_adapter",
            "summary": f"Runtime adapter class is {runtime_class or 'unknown'}.",
        },
        {
            "kind": "risk",
            "ref": "state.json#risk",
            "summary": "Approval risks detected." if approval_risks else "No approval-gated risks detected.",
        },
    ]
    if trigger_policy_ref:
        basis.append({"kind": "trigger", "ref": trigger_policy_ref, "summary": "Trigger policy selected task intake."})
    return {
        "schema_version": "valp-automation-policy.v1",
        "task_id": task_id,
        "mode": mode,
        "trigger_policy_ref": trigger_policy_ref,
        "risk_classification": risk_classification,
        "selected_action": selected_action,
        "approval_required": approval_required,
        "approval_refs": ["approvals/requested.jsonl"] if approval_required else [],
        "iteration_budget_ref": "iteration-budget.json",
        "allowed_automatic_phases": allowed,
        "blocked_automatic_phases": blocked,
        "audit_grade": audit_grade,
        "basis": basis,
        "stop_conditions": [
            "runtime preflight failure",
            "missing expected evidence",
            "unresolved approval request",
            "unresolved critical/high review finding",
            "unresolved agent recommendation",
            "context compression required",
            "dispatch reference-token budget exhausted",
            "dispatch-count budget exhausted",
            "reroute budget exhausted",
            "fix-review-round budget exhausted",
        ],
        "notes": [
            "Automation may continue only while each phase writes auditable evidence.",
            "Automation policy does not grant high-risk approval.",
        ],
    }


def mask_list_for(profile: str, loop_layer: str, design_contract: dict[str, Any]) -> dict[str, Any]:
    masked = [
        {
            "item": "old chat memory without file-backed evidence",
            "reason": "stale context is not valid routing or completion evidence",
        },
        {
            "item": "hidden votes, hidden reviews, or hidden routing decisions",
            "reason": "VALP requires visible decision input",
        },
        {
            "item": "Agy prototype output as production proof",
            "reason": "prototype evidence can inform implementation but cannot satisfy build/test/release gates",
        },
        {
            "item": "release, signing, upload, deploy, auth, secrets, or destructive changes",
            "reason": "high-risk operations require explicit user approval",
        },
        {
            "item": "invalid, superseded, rejected, or blocked evidence",
            "reason": "these evidence statuses do not satisfy done criteria",
        },
    ]
    if profile in UI_ATTENTION_PROFILES and design_contract.get("status") == "missing":
        masked.append(
            {
                "item": "silent full visual-identity invention",
                "reason": "UI work without DESIGN.md must rely on existing project context or create a separate design-contract task",
            }
        )
    if loop_layer == "external_feedback_loop":
        masked.append(
            {
                "item": "agent-only product judgment as user feedback",
                "reason": "external feedback must come from users, analytics, beta testing, or explicitly supplied market evidence",
            }
        )
    return {
        "schema_version": "valp-mask-list.v1",
        "generated_at": now_iso(),
        "profile": profile,
        "loop_layer": loop_layer,
        "masked": masked,
    }


def evidence_board_for(profile: str, loop_layer: str, selected_agents: list[str], design_contract: dict[str, Any]) -> dict[str, Any]:
    claims: list[dict[str, Any]] = [
        {
            "claim": "routing decision is visible",
            "status": "recorded",
            "required_evidence": ["attention-map.json", "visible-routing.md"],
        },
        {
            "claim": "Leader-declared Agents have visible dispatches",
            "status": "needs_dispatch_completion",
            "required_evidence": ["agents/<agent>/dispatch.md", "dispatch-receipts.jsonl"],
        },
        {
            "claim": "runtime or build success",
            "status": "not_yet_claimed",
            "required_evidence": ["command log", "gate JSON", "task evidence path"],
        },
    ]
    if profile in UI_ATTENTION_PROFILES:
        claims.append(
            {
                "claim": "UI behavior matches the requested interaction",
                "status": "needs_preview_evidence",
                "required_evidence": ["real app/browser screenshot", "build/test log", "review evidence"],
            }
        )
        claims.append(
            {
                "claim": "design contract was followed",
                "status": "needs_design_review" if design_contract.get("status") == "present" else "design_contract_missing",
                "required_evidence": ["DESIGN.md lint when present", "Claude UX review", "screenshot comparison"],
            }
        )
    if loop_layer == "external_feedback_loop":
        claims.append(
            {
                "claim": "external feedback was incorporated",
                "status": "needs_external_source",
                "required_evidence": ["user feedback record", "analytics extract", "beta/test report", "A/B result"],
            }
        )
    return {
        "schema_version": "valp-evidence-board.v1",
        "generated_at": now_iso(),
        "profile": profile,
        "loop_layer": loop_layer,
        "selected_agents": selected_agents,
        "claims": claims,
    }


def attention_heads_for(
    loop_layer: str,
    profile: str,
    selected_agents: list[str],
    candidate_scores: dict[str, dict[str, Any]],
    design_contract: dict[str, Any],
    role_assignments: dict[str, str],
    leader_agent: str,
) -> dict[str, Any]:
    heads: dict[str, Any] = {}
    for head, role in ATTENTION_HEAD_ROLES.items():
        selected = leader_agent if role == "coordinator" else role_assignments.get(role)
        score = candidate_scores.get(selected or "", {}).get("overall")
        heads[head] = {
            "selected": selected,
            "candidate": f"role:{role}",
            "score": score,
            "status": (
                "user_selected_leader"
                if role == "coordinator" and selected
                else "leader_declared" if selected in selected_agents else "not_declared"
            ),
        }
    if loop_layer == "external_feedback_loop":
        heads["external_feedback"] = {
            "selected": "human_or_external_source",
            "candidate": "user_feedback_or_runtime_data",
            "score": None,
            "status": "required_source",
        }
    if profile in UI_ATTENTION_PROFILES:
        heads["design_contract"] = {
            "selected": design_contract.get("path"),
            "candidate": "DESIGN.md or .stitch/DESIGN.md",
            "score": 1.0 if design_contract.get("status") == "present" else 0.0,
            "status": design_contract.get("status"),
        }
    return heads


def write_visible_attention(
    root: Path,
    directory: Path,
    task_id: str,
    profile: str,
    prompt: str,
    selected_agents: list[str],
    candidate_scores: dict[str, dict[str, Any]],
    skill_recommendations: dict[str, Any],
    role_assignments: dict[str, str],
    leader_agent: str,
    feedback_history: list[dict[str, Any]],
    control_contract_record: dict[str, str],
) -> dict[str, Any]:
    loop_layer = classify_loop_layer(prompt, profile)
    design_contract = find_design_contract(root)
    context_selection = context_selection_for(root, directory, profile, loop_layer)
    context_pack = context_pack_for(
        root,
        directory,
        task_id,
        profile,
        loop_layer,
        selected_agents,
        role_assignments,
        context_selection,
        feedback_history,
        control_contract_record,
    )
    mask_list = mask_list_for(profile, loop_layer, design_contract)
    evidence_board = evidence_board_for(profile, loop_layer, selected_agents, design_contract)
    heads = attention_heads_for(
        loop_layer,
        profile,
        selected_agents,
        candidate_scores,
        design_contract,
        role_assignments,
        leader_agent,
    )
    attention_map = {
        "schema_version": "valp-visible-attention-map.v1",
        "task_id": task_id,
        "profile": profile,
        "loop_layer": loop_layer,
        "generated_at": now_iso(),
        "heads": heads,
        "leader_agent": leader_agent,
        "selected_agents": selected_agents,
        "role_assignments": role_assignments,
        "candidate_scores_ref": "routing.json#candidate_scores",
        "skill_recommendations": {
            "status": skill_recommendations.get("status"),
            "ref": "skill-recommendations.json",
        },
        "context_selection_ref": "context-selection.json",
        "context_pack_ref": "context-pack.json",
        "mask_list_ref": "mask-list.json",
        "evidence_board_ref": "evidence-board.json",
        "visible_summary_ref": "visible-routing.md",
    }
    visible_routing = format_visible_routing(attention_map, context_selection, context_pack, mask_list, evidence_board, design_contract)
    write_json(directory / "attention-map.json", attention_map)
    write_json(directory / "context-selection.json", context_selection)
    write_json(directory / "context-pack.json", context_pack)
    write_json(directory / "mask-list.json", mask_list)
    write_json(directory / "evidence-board.json", evidence_board)
    (directory / "visible-routing.md").write_text(visible_routing, encoding="utf-8")
    return {
        "loop_layer": loop_layer,
        "design_contract": design_contract,
        "refs": {
            "attention_map": "attention-map.json",
            "context_selection": "context-selection.json",
            "context_pack": "context-pack.json",
            "mask_list": "mask-list.json",
            "evidence_board": "evidence-board.json",
            "visible_routing": "visible-routing.md",
        },
        "attention_map": attention_map,
        "context_selection": context_selection,
        "context_pack": context_pack,
        "mask_list": mask_list,
    }


def format_visible_routing(
    attention_map: dict[str, Any],
    context_selection: dict[str, Any],
    context_pack: dict[str, Any],
    mask_list: dict[str, Any],
    evidence_board: dict[str, Any],
    design_contract: dict[str, Any],
) -> str:
    head_lines = []
    for head, record in (attention_map.get("heads") or {}).items():
        selected = record.get("selected") or "none"
        score = record.get("score")
        score_text = "n/a" if score is None else str(score)
        head_lines.append(f"- {head}: {selected} (score {score_text}, {record.get('status')})")
    context_lines = [
        f"- `{item.get('path')}`: {item.get('reason')}"
        for item in (context_selection.get("selected") or [])[:10]
    ]
    context_pack_lines = [
        f"- {item.get('section')}: {item.get('summary')}"
        for item in (context_pack.get("items") or [])[:8]
    ]
    mask_lines = [
        f"- {item.get('item')}: {item.get('reason')}"
        for item in (mask_list.get("masked") or [])[:8]
    ]
    claim_lines = [
        f"- {item.get('claim')}: {item.get('status')}"
        for item in (evidence_board.get("claims") or [])[:8]
    ]
    return """# Visible Routing

Task: {task_id}
Profile: {profile}
Loop layer: {loop_layer}
Design contract: {design_status}{design_path}

## Attention Heads

{heads}

## Selected Context

{context}

## Context Pack

{context_pack}

## Masked Inputs

{masks}

## Evidence Board

{claims}
""".format(
        task_id=attention_map.get("task_id"),
        profile=attention_map.get("profile"),
        loop_layer=attention_map.get("loop_layer"),
        design_status=design_contract.get("status"),
        design_path=f" ({design_contract.get('path')})" if design_contract.get("path") else "",
        heads="\n".join(head_lines) or "- none",
        context="\n".join(context_lines) or "- none",
        context_pack="\n".join(context_pack_lines) or "- none",
        masks="\n".join(mask_lines) or "- none",
        claims="\n".join(claim_lines) or "- none",
    )


def attention_slice_for_agent(agent: str, visible_attention: dict[str, Any]) -> str:
    attention_map = visible_attention.get("attention_map") or {}
    context_pack = visible_attention.get("context_pack") or {}
    heads = attention_map.get("heads") or {}
    matching_heads = [
        head
        for head, record in heads.items()
        if record.get("selected") == agent or record.get("candidate") == agent
    ]
    context_pack_lines = [
        f"- {item.get('section')}: {item.get('summary')}"
        for item in (context_pack.get("items") or [])
        if agent in (item.get("recipient_agents") or []) or not item.get("recipient_agents")
    ][:2]
    design = visible_attention.get("design_contract") or {}
    return """- Loop layer: `{loop_layer}`
- Your attention head(s): {heads}
- Design contract: `{design_status}`{design_path}
- Role context from `context-pack.json`:
{context_pack}
- Full selection and masks: `visible-routing.md`
""".format(
        loop_layer=visible_attention.get("loop_layer", "unknown"),
        heads=", ".join(matching_heads) if matching_heads else "none",
        design_status=design.get("status", "unknown"),
        design_path=f" (`{design.get('path')}`)" if design.get("path") else "",
        context_pack="\n".join(context_pack_lines) or "  - none",
    )


def publish_task(
    root: Path,
    task_id: str,
    prompt: str,
    profile: str | None = None,
    runtime: str | None = None,
    invoked_entrypoint: Path | None = None,
) -> Path:
    root = workspace_root(root)
    normalize_runtime(runtime)
    directory = task_dir(root, task_id)
    directory.mkdir(parents=True, exist_ok=True)
    selected_profile = profile or classify_profile(prompt)
    approval_risks = classify_approval_risks(prompt)
    approval_risk_text = "\n".join(
        f"- `{risk['kind']}` matched `{risk['matched']}`"
        for risk in approval_risks
    ) or "- No approval-gated risks detected."
    task_md = f"""# Task

ID: {task_id}
Profile: {selected_profile}
Mode: Awaiting Leader assignment

## Goal

{prompt}

## Expected Evidence

Declared by the user-selected Leader before VALP validation.

## Approval Risks

{approval_risk_text}
"""
    (directory / "task.md").write_text(task_md, encoding="utf-8")
    approval_gate = "needs_approval" if approval_risks else "not_required"
    state = {
        "schema_version": "valp-visible-loop-state.v2",
        "task_id": task_id,
        "profile": selected_profile,
        "status": "published",
        "revision": 0,
        "risk": {
            "approval_required": bool(approval_risks),
            "matches": approval_risks,
        },
        "selected_agents": [],
        "source_provenance": new_source_provenance(invoked_entrypoint),
        "capabilities_needed": PROFILE_CAPABILITIES.get(selected_profile, PROFILE_CAPABILITIES["generic-analysis"]),
        "capabilities_missing": [],
        "gates": {
            "dispatch_receipts": "needs_evidence",
            "expected_evidence": "needs_evidence",
            "verification": "needs_evidence",
            "review": "needs_evidence",
            "approval": approval_gate,
        },
        "approval_required": approval_risks,
        "updated_at": now_iso(),
    }
    write_json(directory / "state.json", state)
    return directory


def route_task(
    root: Path,
    task_id: str,
    runtime: str | None = None,
    assignment_declaration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = workspace_root(root)
    normalize_runtime(runtime)
    directory = task_dir(root, task_id)
    state_path = directory / "state.json"
    state = read_json(state_path)
    if not state:
        raise SystemExit(f"Missing state.json for task {task_id}")
    declaration = assignment_declaration or read_json(directory / "assignment-declaration.json")
    if not declaration:
        raise SystemExit(
            "Routing requires a Leader-authored assignment declaration; "
            "VALP does not select Agents"
        )
    if declaration.get("schema_version") != "valp-assignment-declaration.v1":
        raise SystemExit("Assignment declaration must use valp-assignment-declaration.v1")
    allowed_declaration_fields = {
        "schema_version",
        "declaration_id",
        "task_id",
        "declared_at",
        "leader",
        "assignments",
        "reasons",
    }
    extra_declaration_fields = sorted(set(declaration) - allowed_declaration_fields)
    if extra_declaration_fields:
        raise SystemExit(
            "Invalid Leader assignment declaration: unexpected_fields:"
            + ",".join(extra_declaration_fields)
        )
    if declaration.get("task_id") != task_id:
        raise SystemExit("Assignment declaration task_id does not match the routed task")
    leader = declaration.get("leader") if isinstance(declaration.get("leader"), dict) else {}
    if set(leader) != {"agent_id", "selected_by", "selection_ref"}:
        raise SystemExit("Invalid Leader assignment declaration: invalid_leader_fields")
    if leader.get("selected_by") != "user" or not leader.get("agent_id") or not leader.get("selection_ref"):
        raise SystemExit("Assignment declaration requires explicit user-selected Leader evidence")
    declared_assignments = declaration.get("assignments")
    if not isinstance(declared_assignments, dict) or not declared_assignments:
        raise SystemExit("Assignment declaration has no role assignments")
    role_assignments = {
        str(role): str(agent)
        for role, agent in declared_assignments.items()
        if str(role).strip() and str(agent).strip()
    }
    declaration_errors: list[str] = []
    if not str(declaration.get("declaration_id") or "").strip():
        declaration_errors.append("missing_declaration_id")
    declared_at = str(declaration.get("declared_at") or "").strip()
    try:
        declared_timestamp = datetime.fromisoformat(declared_at.replace("Z", "+00:00"))
    except ValueError:
        declared_timestamp = None
    if declared_timestamp is None or declared_timestamp.tzinfo is None:
        declaration_errors.append("invalid_declared_at")
    reasons = declaration.get("reasons")
    if not isinstance(reasons, dict):
        reasons = {}
    if role_assignments != declared_assignments:
        declaration_errors.append("invalid_role_or_agent_value")
    if set(reasons) != set(role_assignments):
        declaration_errors.append("assignment_reason_roles_mismatch")
    for role in role_assignments:
        if not str(reasons.get(role) or "").strip():
            declaration_errors.append(f"missing_assignment_reason:{role}")
    if declaration_errors:
        raise SystemExit("Invalid Leader assignment declaration: " + ", ".join(declaration_errors))
    write_json(directory / "assignment-declaration.json", declaration)
    prompt = (directory / "task.md").read_text(encoding="utf-8", errors="replace")
    profile = state.get("profile") or classify_profile(prompt)
    approval_risks = classify_approval_risks(extract_goal_text(prompt))
    if approval_risks and (state.get("gates") or {}).get("approval") in {None, "not_required"}:
        state.setdefault("gates", {})["approval"] = "needs_approval"
    elif not approval_risks:
        state.setdefault("gates", {})["approval"] = "not_required"
    state["approval_required"] = approval_risks
    state["risk"] = {
        "approval_required": bool(approval_risks),
        "matches": approval_risks,
    }
    capabilities = scan_workspace(root, task_id, runtime=runtime)
    overlay = load_local_overlay(root)
    agents = capabilities.get("agents") or {}
    feedback_history = load_routing_feedback_history(root)
    # Skill/MCP evidence is collected before advisory scoring. Per-agent
    # slices are written only for the Leader-declared Agents after validation.
    skill_recommendations = run_skill_recommendations(root, task_id, profile, prompt)
    routing_evaluated_at = now_iso()
    runtime_kind = resolve_runtime(runtime)
    dynamic_model_discovery_required = runtime_kind != "manual"
    runtime_checks = (capabilities.get("runtime_preflight") or {}).get("checks") or {}
    herdr_owned_session_gate_deferred = bool(
        runtime_kind == "herdr"
        and (runtime_checks.get("session_provisioning") or {}).get("status") == "pass"
    )
    enforce_route_model_role_gate = bool(
        dynamic_model_discovery_required and not herdr_owned_session_gate_deferred
    )
    candidate_scores = score_candidates(
        profile,
        agents,
        feedback_history,
        skill_recommendations,
        runtime_preflight=capabilities.get("runtime_preflight") or {},
        enforce_model_role_gate=enforce_route_model_role_gate,
        evaluated_at=routing_evaluated_at,
    )
    selected_agents = list(dict.fromkeys(role_assignments.values()))
    required_roles = required_roles_for(profile, agents)
    assignment_blockers: list[str] = []
    missing_roles = [role for role in required_roles if role not in role_assignments]
    assignment_blockers.extend(f"missing_required_role:{role}" for role in missing_roles)
    if (
        role_assignments.get("coordinator")
        and role_assignments.get("coordinator") != str(leader.get("agent_id"))
    ):
        assignment_blockers.append("coordinator_must_match_user_selected_leader")
    for role, agent in role_assignments.items():
        info = agents.get(agent)
        if not isinstance(info, dict):
            assignment_blockers.append(f"unknown_agent:{role}:{agent}")
        elif not bool(info.get("active", True)):
            assignment_blockers.append(f"inactive_agent:{role}:{agent}")
        elif role in {"coordinator", "implementer", "reviewer", "prototype", "researcher"} and role_fit_score(
            info, role
        ) <= 0.0:
            assignment_blockers.append(f"role_ineligible:{role}:{agent}")
    blocked_model_roles = [
        role
        for role in ("implementer", "reviewer")
        if enforce_route_model_role_gate
        and role in role_assignments
        and role in (candidate_scores.get(role_assignments[role], {}).get("model_role_gate", {}).get("blocked_roles") or [])
    ]
    assignment_blockers.extend(
        f"active_model_identity:{role}:{role_assignments[role]}"
        for role in blocked_model_roles
    )
    model_role_gate = {
        "enforced": dynamic_model_discovery_required,
        "status": "blocked" if blocked_model_roles else "pass",
        "blocked_roles": blocked_model_roles,
        "fallback_modes": ["discovery", "prototype", "manual"] if blocked_model_roles else [],
        "reason": (
            "No candidate has an observed, current model identity bound to a known runtime session."
            if blocked_model_roles
            else (
                "Model identity eligibility is deferred to task-owned session preflight before HERDR delivery."
                if herdr_owned_session_gate_deferred
                else "High-risk model identity requirements are satisfied or not applicable."
            )
        ),
    }
    capabilities_missing = list(assignment_blockers)
    assignment_validation = {
        "schema_version": "valp-assignment-validation.v1",
        "task_id": task_id,
        "declaration_ref": "assignment-declaration.json",
        "authority": "leader_declared",
        "status": "blocked" if assignment_blockers else "pass",
        "validated_at": routing_evaluated_at,
        "blockers": assignment_blockers,
        "validated_assignments": role_assignments,
    }
    write_json(directory / "assignment-validation.json", assignment_validation)
    if assignment_blockers:
        state["status"] = "blocked"
        state["role_assignments"] = role_assignments
        state["selected_agents"] = selected_agents
        state["capabilities_missing"] = capabilities_missing
        state["assignment_authority"] = "leader_declared"
        state["assignment_declaration"] = {
            "status": "recorded",
            "ref": "assignment-declaration.json",
            "leader_agent": leader.get("agent_id"),
            "selected_by": leader.get("selected_by"),
            "selection_ref": leader.get("selection_ref"),
        }
        state["assignment_validation"] = {
            "status": "blocked",
            "ref": "assignment-validation.json",
        }
        state["updated_at"] = now_iso()
        write_json(state_path, state)
        raise SystemExit("Assignment validation blocked: " + ", ".join(assignment_blockers))
    preflight = runtime_preflight_for_agents(
        capabilities.get("runtime_preflight") or {},
        selected_agents,
    )
    runtime_adapter = runtime_adapter_record(preflight, runtime=runtime)
    automation_policy = automation_policy_for(task_id, runtime_adapter, approval_risks)
    write_json(directory / "automation-policy.json", automation_policy)
    skill_recommendations = add_per_agent_skill_recommendations(skill_recommendations, selected_agents)
    write_json(directory / "skill-recommendations.json", skill_recommendations)
    existing_routing = read_json(directory / "routing.json")
    _control_contract, contract_digest = ensure_control_contract(directory, task_id)
    control_contract_record = {
        "status": "recorded",
        "ref": CONTROL_CONTRACT_REF,
        "digest": contract_digest,
        "priority_class": "highest_runtime_control",
    }
    previous_control_contract = existing_routing.get("control_contract") or {}
    if previous_control_contract and previous_control_contract != control_contract_record:
        raise SystemExit("Routing blocked: immutable worker control contract marker changed")
    iteration_budget = read_json(directory / "iteration-budget.json") or iteration_budget_for(task_id, role_assignments)
    reroute_count = int((iteration_budget.get("usage") or {}).get("reroutes") or 0)
    if existing_routing.get("selected_agents"):
        inflight_reroute = inflight_reroute_for_recovery(
            directory,
            existing_routing,
            iteration_budget,
            preflight,
        )
        if inflight_reroute is not None:
            reroute_count = inflight_reroute
            iteration_budget["status"] = "active"
            iteration_budget["stop_reason"] = None
            record_reroute_resume_evidence(directory, task_id, reroute_count)
        else:
            reroute_count += 1
            if reroute_count > int(iteration_budget.get("max_reroutes") or 0):
                iteration_budget["status"] = "blocked"
                iteration_budget["stop_reason"] = "reroute budget exhausted"
                write_json(directory / "iteration-budget.json", iteration_budget)
                raise SystemExit("Routing blocked by iteration budget: reroute budget exhausted")
            if (
                iteration_budget.get("stop_reason")
                == "runtime dispatch retry exhausted"
                and existing_routing.get("role_assignments") != role_assignments
            ):
                iteration_budget["status"] = "active"
                iteration_budget["stop_reason"] = None
            record_reroute_evidence(directory, task_id, existing_routing, reroute_count)
        refresh_iteration_budget(directory, existing_routing, iteration_budget, reroute_count)
        if iteration_budget.get("status") != "active":
            raise SystemExit(f"Routing blocked by iteration budget: {iteration_budget.get('stop_reason') or iteration_budget.get('status')}")
    iteration_budget.setdefault("usage", {})["reroutes"] = reroute_count
    iteration_budget["task_id"] = task_id
    iteration_budget["strategy"] = "leader_declared_bounded_team"
    visible_attention = write_visible_attention(
        root,
        directory,
        task_id,
        profile,
        prompt,
        selected_agents,
        candidate_scores,
        skill_recommendations,
        role_assignments,
        str(leader.get("agent_id")),
        feedback_history,
        control_contract_record,
    )
    context_policies = {
        agent: context_policy_for(agent, agents.get(agent, {}), overlay)
        for agent in selected_agents
    }
    expected_by_agent = expected_refs_for_agents(selected_agents, role_assignments)
    submission_dependencies = build_submission_dependencies(task_id, role_assignments)
    existing_delegation_policy = read_json(directory / "delegation-policy.json")
    recorded_delegation_violations = existing_delegation_policy.get("violations", [])
    delegation_policy = build_delegation_policy(
        task_id,
        manual_mode=runtime_adapter.get("class") == "manual",
    )
    delegation_policy["violations"] = recorded_delegation_violations
    write_json(directory / "submission-dependencies.json", submission_dependencies)
    write_json(directory / "delegation-policy.json", delegation_policy)
    control_slice_refs = write_control_slices(
        directory,
        task_id,
        selected_agents,
        work_item_ids_by_agent(submission_dependencies),
        contract_digest,
    )
    routing = {
        "schema_version": "valp-capability-routing.v1",
        "task_id": task_id,
        "profile": profile,
        "runtime_adapter": runtime_adapter,
        "risk": state["risk"],
        "local_overlay": {
            "used": bool(overlay),
            "ref": ".herdr-loop/local-overlay.json" if overlay else None,
            "note": "Local capability profiles are routing hints, not fixed assignments.",
        },
        "capabilities_needed": PROFILE_CAPABILITIES.get(profile, PROFILE_CAPABILITIES["generic-analysis"]),
        "role_requirements": required_roles,
        "role_assignments": role_assignments,
        "assignment_authority": "leader_declared",
        "assignment_declaration": {
            "status": "recorded",
            "ref": "assignment-declaration.json",
            "leader_agent": leader.get("agent_id"),
            "selected_by": leader.get("selected_by"),
            "selection_ref": leader.get("selection_ref"),
        },
        "assignment_validation": {
            "status": "pass",
            "ref": "assignment-validation.json",
        },
        "model_role_gate": model_role_gate,
        "team_selection": {
            "strategy": "leader_declared_valp_validated",
            "required_roles": required_roles,
            "selected_agents": selected_agents,
        },
        "submission_dependencies": {
            "status": "recorded",
            "ref": "submission-dependencies.json",
        },
        "delegation_policy": {
            "status": "recorded",
            "ref": "delegation-policy.json",
        },
        "iteration_budget": {
            "status": "recorded",
            "ref": "iteration-budget.json",
        },
        "control_contract": control_contract_record,
        "control_slices": control_slice_refs,
        "coordinator_selection": {
            "selected_agent": leader.get("agent_id"),
            "selection_rule": "Explicit user-selected Leader from assignment-declaration.json; VALP validated but did not select the Agent.",
        },
        "selected_agents": selected_agents,
        "agent_match_reasons": {
            agent: match_reasons_for(agent, profile, agents.get(agent, {}))
            for agent in selected_agents
        },
        "candidate_scores": candidate_scores,
        "routing_confidence": routing_confidence(candidate_scores, selected_agents),
        "rejected_candidates": rejected_candidates(candidate_scores, selected_agents),
        "selected_agent_context_policies": context_policies,
        "automation_policy": {
            "schema_version": "valp-automation-policy.v1",
            "status": "recorded",
            "ref": "automation-policy.json",
            "selected_action": automation_policy.get("selected_action"),
            "audit_grade": automation_policy.get("audit_grade"),
        },
        "context_pack": {
            "schema_version": "valp-context-pack.v1",
            "status": "recorded",
            "ref": "context-pack.json",
        },
        "skill_recommendations": {
            "schema_version": "valp-skill-recommendations.v1",
            "status": skill_recommendations.get("status"),
            "backend": skill_recommendations.get("backend"),
            "ref": "skill-recommendations.json",
            "routing": skill_recommendations.get("routing") or {},
            "missing_skills": skill_recommendations.get("missing_skills") or [],
            "coordinator_only": True,
        },
        "visible_attention": {
            "schema_version": "valp-visible-attention.v1",
            "status": "recorded",
            "loop_layer": visible_attention["loop_layer"],
            "design_contract": visible_attention["design_contract"],
            **visible_attention["refs"],
        },
        "provider_matrix": provider_matrix_for(
            selected_agents,
            agents,
            overlay,
            preflight,
            evaluated_at=routing_evaluated_at,
            dynamic_discovery_required=dynamic_model_discovery_required,
        ),
        "runtime_task_state_mapping": RUNTIME_TASK_STATE_MAPPING,
        "squad_routing": {"used": False},
        "routing_feedback_ref": "routing-feedback.json",
        "learning_feedback_ref": "learning-feedback.json",
        "capabilities_missing": capabilities_missing,
    }
    skill_slice_refs = write_skill_slices(directory, task_id, selected_agents, skill_recommendations)
    routing["skill_recommendation_slices"] = skill_slice_refs
    write_json(directory / "routing.json", routing)
    dispatch_payload_budgets = write_dispatches(
        root,
        directory,
        task_id,
        profile,
        prompt,
        selected_agents,
        expected_by_agent,
        routing,
        skill_recommendations,
        visible_attention,
    )
    routing["dispatch_payload_budgets"] = dispatch_payload_budgets
    write_json(directory / "routing.json", routing)
    refresh_iteration_budget(directory, routing, iteration_budget, reroute_count)
    append_dispatch_written_receipts(
        directory,
        selected_agents,
        expected_by_agent,
        runtime_adapter_id=str((routing.get("runtime_adapter") or {}).get("id") or ""),
    )
    state.update(
        {
            "profile": profile,
            "status": "dispatching",
            "loop_layer": visible_attention["loop_layer"],
            "runtime_adapter": routing["runtime_adapter"],
            "local_overlay": routing["local_overlay"],
            "runtime_task_state_mapping": RUNTIME_TASK_STATE_MAPPING,
            "provider_matrix": {"status": "scanned", "ref": "routing.json"},
            "squad_routing": {"used": False},
            "selected_agents": selected_agents,
            "role_assignments": role_assignments,
            "assignment_authority": "leader_declared",
            "assignment_declaration": routing["assignment_declaration"],
            "assignment_validation": routing["assignment_validation"],
            "submission_dependencies": routing["submission_dependencies"],
            "delegation_policy": routing["delegation_policy"],
            "capabilities_needed": routing["capabilities_needed"],
            "capabilities_missing": capabilities_missing,
            "model_role_gate": model_role_gate,
            "context_policies": context_policies,
            "automation_policy": {
                "status": "recorded",
                "ref": "automation-policy.json",
                "selected_action": automation_policy.get("selected_action"),
                "audit_grade": automation_policy.get("audit_grade"),
            },
            "context_pack": {"status": "recorded", "ref": "context-pack.json"},
            "control_contract": control_contract_record,
            "control_slices": control_slice_refs,
            "dispatch_payload_budgets": dispatch_payload_budgets,
            "iteration_budget": {
                "status": "recorded",
                "ref": "iteration-budget.json",
            },
            "skill_recommendations": {
                "status": skill_recommendations.get("status"),
                "backend": skill_recommendations.get("backend"),
                "ref": "skill-recommendations.json",
                "coordinator_only": True,
            },
            "skill_recommendation_slices": skill_slice_refs,
            "visible_attention": {
                "status": "recorded",
                "loop_layer": visible_attention["loop_layer"],
                **visible_attention["refs"],
            },
            "routing_confidence": routing["routing_confidence"],
            "routing_feedback": {"status": "expected", "ref": "routing-feedback.json"},
            "learning_feedback": {"status": "expected", "ref": "learning-feedback.json"},
            "updated_at": now_iso(),
        }
    )
    if delegation_policy.get("violations"):
        state["status"] = "blocked"
        state.setdefault("gates", {})["expected_evidence"] = "blocked"
        state["delegation_violation"] = {
            "status": "unresolved",
            "count": len(delegation_policy["violations"]),
            "ref": "delegation-policy.json#violations",
        }
    if blocked_model_roles:
        state["status"] = "blocked"
        state.setdefault("gates", {})["expected_evidence"] = "blocked"
    write_json(state_path, state)
    return routing


def score_candidates(
    profile: str,
    agents: dict[str, Any],
    feedback_history: list[dict[str, Any]] | None = None,
    skill_recommendations: dict[str, Any] | None = None,
    runtime_preflight: dict[str, Any] | None = None,
    enforce_model_role_gate: bool = False,
    evaluated_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    required_roles = PROFILE_ROLE_REQUIREMENTS.get(profile, PROFILE_ROLE_REQUIREMENTS["generic-analysis"])
    history = feedback_history or []
    preflight_agents = (runtime_preflight or {}).get("agents") or {}
    scores: dict[str, dict[str, Any]] = {}
    for agent, info in agents.items():
        active = bool(info.get("active", True))
        runtime = info.get("runtime") or {}
        runtime_status = str(runtime.get("status", "unknown"))
        role_fit = {role: role_fit_score(info, role) for role in required_roles}
        profile_fit = max(role_fit.values()) if role_fit else 0.45
        tool_fit = 0.85 if info.get("mcp_servers") or runtime else 0.55
        skill_count = len(info.get("skills") or [])
        recommended_count = 0
        if skill_recommendations:
            for result in skill_recommendations.get("results") or []:
                recommended_count += sum(
                    1
                    for match in result.get("matches") or []
                    if provider_reachable_match(agent, match)
                )
        skill_fit = min(0.95, 0.45 + skill_count / 80 + min(0.25, recommended_count * 0.04))
        permission_fit = 0 if not active else 1
        context_fit = 0.85
        feedback_prior = feedback_prior_for_agent(agent, profile, history)
        agent_preflight = preflight_agents.get(agent) if isinstance(preflight_agents, dict) else {}
        runtime_probe = agent_preflight.get("model_probe") if isinstance(agent_preflight, dict) else None
        model_identity = model_identity_for(
            agent,
            info,
            {},
            runtime_probe=runtime_probe,
            evaluated_at=evaluated_at,
        )
        blocked_roles: list[str] = []
        if enforce_model_role_gate:
            for role, eligibility_key in (("implementer", "implementer"), ("reviewer", "final_reviewer")):
                if role in role_fit and model_identity["role_eligibility"][eligibility_key] != "eligible":
                    role_fit[role] = 0.0
                    blocked_roles.append(role)
        model_score = model_evidence_score(model_identity)
        evidence_history = round(min(feedback_prior["score"], model_score), 2)
        availability = 1 if runtime_status == "idle" else 0.75 if runtime_status in {"working", "focused"} else 0.65
        if not active:
            availability = 0
        risk_fit = 0.9
        values = [profile_fit, tool_fit, skill_fit, permission_fit, context_fit, evidence_history, availability, risk_fit]
        overall = round(sum(values) / len(values), 2)
        confidence = "high" if overall >= 0.75 else "medium" if overall >= 0.55 else "low"
        if model_identity["evidence_status"] == "unknown":
            confidence = "low"
        elif model_identity["evidence_status"] != "strong" and confidence == "high":
            confidence = "medium"
        scores[agent] = {
            "profile_fit": round(profile_fit, 2),
            "tool_fit": round(tool_fit, 2),
            "skill_fit": round(skill_fit, 2),
            "skill_evidence": {
                "source_ref": "skill-recommendations.json" if skill_recommendations else None,
                "provider_reachable_match_count": recommended_count,
            },
            "permission_fit": round(permission_fit, 2),
            "context_fit": round(context_fit, 2),
            "evidence_history": round(evidence_history, 2),
            "availability": round(availability, 2),
            "risk_fit": round(risk_fit, 2),
            "overall": overall,
            "confidence": confidence,
            "role_fit": role_fit,
            "routing_basis": "capability_roles",
            "evidence_history_notes": feedback_prior["notes"],
            "evidence_history_refs": feedback_prior["refs"],
            "model_evidence": {
                "status": model_identity["evidence_status"],
                "history_status": model_identity["history_status"],
                "observed_model": model_identity["observed_model"]["model_id"],
                "computed_freshness": model_identity["observed_model"]["freshness"],
                "probe_status": model_identity["model_probe"]["status"],
                "session_status": model_identity["model_probe"]["session_identity"]["status"],
                "history_invalidation_reasons": model_identity["history_invalidation_reasons"],
            },
            "model_role_gate": {
                "enforced": enforce_model_role_gate,
                "status": "blocked" if blocked_roles else "eligible",
                "blocked_roles": blocked_roles,
                "fallback_roles": ["discovery", "prototype", "manual"] if blocked_roles else [],
                "role_eligibility": model_identity["role_eligibility"],
            },
        }
    return scores


def required_roles_for(
    profile: str,
    agents: dict[str, Any],
) -> list[str]:
    return [
        role
        for role in PROFILE_ROLE_REQUIREMENTS.get(profile, PROFILE_ROLE_REQUIREMENTS["generic-analysis"])
        if role != "coordinator"
    ]


def agent_capability_text(info: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ["role", "strengths"]:
        raw = info.get(key) or []
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(str(item) for item in raw)
    return " ".join(values).lower()


def role_fit_score(info: dict[str, Any], role: str) -> float:
    text = agent_capability_text(info)
    negative_text = " ".join(str(item) for item in (info.get("must_not_do") or [])).lower()
    normalized_boundary = re.sub(r"[-_]+", " ", f"{text} {negative_text}")
    if role == "implementer" and re.search(r"\bread only\b", normalized_boundary):
        return 0.0
    terms = ROLE_MATCH_TERMS.get(role, [])
    matches = sum(1 for term in terms if term_matches_capability(term, text) and not term_matches_capability(term, negative_text))
    if matches:
        return round(min(0.95, 0.35 + matches * 0.15), 2)
    return 0.25


def term_matches_capability(term: str, text: str) -> bool:
    normalized_text = re.sub(r"[-_]+", " ", text.lower())
    normalized_term = re.sub(r"[-_]+", " ", term.lower()).strip()
    if " " in normalized_term:
        return re.search(rf"(?<!\w){re.escape(normalized_term)}(?!\w)", normalized_text) is not None
    return normalized_term in set(re.findall(r"[a-z0-9]+", normalized_text))


def inferred_primary_role(info: dict[str, Any]) -> str:
    candidates = {
        role: role_fit_score(info, role)
        for role in ["coordinator", "implementer", "reviewer", "prototype", "researcher"]
    }
    role, score = max(candidates.items(), key=lambda item: item[1])
    return role if score >= 0.35 else "other"


def context_policy_for(agent: str, info: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    overlay_profiles = overlay.get("agent_capability_profiles") or {}
    overlay_policy = (overlay_profiles.get(agent) or {}).get("context_policy")
    if overlay_policy:
        return overlay_policy
    if info.get("context_policy"):
        return info["context_policy"]
    return DEFAULT_CONTEXT_POLICIES.get(inferred_primary_role(info), DEFAULT_CONTEXT_POLICIES["other"])


def match_reasons_for(agent: str, profile: str, info: dict[str, Any]) -> list[str]:
    reasons = []
    roles = info.get("role") or []
    strengths = info.get("strengths") or []
    if roles:
        reasons.extend(roles[:3])
    if strengths:
        reasons.extend(strengths[:2])
    if not reasons:
        reasons.append(f"candidate capability facts for {profile}")
    return reasons


def routing_confidence(scores: dict[str, dict[str, Any]], selected_agents: list[str]) -> dict[str, Any]:
    if not selected_agents:
        return {"overall": "low", "reason": "No Leader-declared Agents."}
    average = sum(scores[agent]["overall"] for agent in selected_agents) / len(selected_agents)
    band = "high" if average >= 0.75 else "medium" if average >= 0.55 else "low"
    return {"overall": band, "score": round(average, 2), "reason": "Computed from local capability scan and overlay hints."}


def rejected_candidates(scores: dict[str, dict[str, Any]], selected_agents: list[str]) -> list[dict[str, Any]]:
    selected = set(selected_agents)
    rejected = []
    for agent, score in sorted(scores.items(), key=lambda item: item[1].get("overall", 0), reverse=True):
        if agent in selected:
            continue
        if score.get("overall", 0) >= 0.45:
            rejected.append(
                {
                    "agent": agent,
                    "confidence": score.get("confidence", "unknown"),
                    "score": score.get("overall"),
                    "reason": "The Leader declaration did not assign this Agent; the score is advisory only.",
                }
            )
    return rejected


def runtime_adapter_record(preflight: dict[str, Any] | None = None, runtime: str | None = None) -> dict[str, Any]:
    preflight = preflight or {}
    runtime_kind = resolve_runtime(runtime)
    adapter_class = str(preflight.get("adapter_class") or "")
    if adapter_class == "manual":
        runtime_kind = "manual"
    elif adapter_class == "daemon_queue":
        runtime_kind = "queue"
    elif adapter_class == "pane_controller":
        runtime_kind = "herdr"

    if runtime_kind == "queue":
        return {
            "id": "queue",
            "class": "daemon_queue",
            "name": "VALP headless queue",
            "full_mode_capable": True,
            "state_mapping_ref": "docs/task-state-machine.md",
            "preflight": preflight,
        }
    if runtime_kind == "langgraph":
        return {
            "id": "langgraph",
            "class": "hosted_local_platform",
            "name": "LangGraph API",
            "full_mode_capable": preflight.get("status") != "fail",
            "state_mapping_ref": "docs/task-state-machine.md",
            "preflight": preflight,
        }
    if runtime_kind == "herdr":
        return {
            "id": "herdr",
            "class": "pane_controller",
            "name": "HERDR",
            "full_mode_capable": bool(shutil.which("herdr")) and preflight.get("status") != "fail",
            "state_mapping_ref": "docs/task-state-machine.md",
            "preflight": preflight,
        }
    return {
        "id": "manual",
        "class": "manual",
        "name": "manual",
        "full_mode_capable": False,
        "state_mapping_ref": "docs/task-state-machine.md",
        "preflight": preflight,
    }


def herdr_launch_argv_for(agent: str, capabilities: dict[str, Any]) -> list[str]:
    info = ((capabilities.get("agents") or {}).get(agent) or {})
    runtime = info.get("runtime") if isinstance(info.get("runtime"), dict) else {}
    configured = runtime.get("launch_argv")
    if (
        isinstance(configured, list)
        and configured
        and all(isinstance(item, str) and item.strip() for item in configured)
    ):
        argv = [str(item) for item in configured]
    else:
        argv = []
    if not argv:
        return []
    entrypoint = argv[0]
    separators = [separator for separator in (os.sep, os.altsep) if separator]
    has_path_component = any(separator in entrypoint for separator in separators)
    if has_path_component and not Path(entrypoint).is_absolute():
        raise HerdrSubmissionError(
            f"HERDR Agent {agent} launch executable {entrypoint!r} is not absolute; "
            "configure an absolute runtime.launch_argv or use a bare command"
        )
    resolved = shutil.which(entrypoint)
    if not resolved:
        raise HerdrSubmissionError(
            f"HERDR cannot resolve Agent {agent} launch executable {entrypoint!r} "
            "to an executable absolute path; configure an absolute runtime.launch_argv"
        )
    resolved_path = Path(resolved)
    if not resolved_path.is_absolute():
        resolved_path = resolved_path.resolve()
    argv[0] = str(resolved_path)
    return argv


def ensure_herdr_agent_sessions(
    root: Path,
    directory: Path,
    task_id: str,
    agent_names: list[str],
    capabilities: dict[str, Any],
    *,
    allow_launch_argv_change: bool = False,
    allow_done_session_reprovision: bool = False,
) -> dict[str, Any]:
    herdr = shutil.which("herdr")
    if not herdr:
        raise HerdrSubmissionError("HERDR session provisioning is unavailable: herdr command not found")
    projection_path = directory / "agent-sessions.json"
    receipts_path = directory / "agent-session-receipts.jsonl"
    with task_state_lock(directory):
        projection_exists = projection_path.exists()
        if projection_exists:
            projection = read_json_strict(projection_path)
            if (
                projection.get("schema_version") != "valp-agent-sessions.v1"
                or projection.get("task_id") != task_id
                or projection.get("adapter") != "herdr"
                or projection.get("status") not in {"provisioning", "ready"}
                or not isinstance(projection.get("bindings"), dict)
            ):
                raise HerdrSubmissionError("HERDR task-owned session projection conflicts")
        else:
            projection = {
                "schema_version": "valp-agent-sessions.v1",
                "task_id": task_id,
                "adapter": "herdr",
                "status": "provisioning",
                "bindings": {},
                "updated_at": now_iso(),
            }
        receipts = read_json_lines_strict(receipts_path)
        receipt_errors: list[str] = []
        bindings = projection.get("bindings") or {}
        if receipts and not projection_exists:
            receipt_errors.append("receipts exist without an Agent session projection")
        if [record.get("event_sequence") for record in receipts] != list(
            range(1, len(receipts) + 1)
        ):
            receipt_errors.append("event_sequence is not contiguous")
        for index, record in enumerate(receipts, 1):
            if record.get("schema_version") != "valp-agent-session-receipt.v1":
                receipt_errors.append(f"line {index} has an invalid schema_version")
            if record.get("adapter") != "herdr":
                receipt_errors.append(f"line {index} belongs to another adapter")
            if record.get("task_id") != task_id:
                receipt_errors.append(f"line {index} belongs to another task")
            if record.get("event") not in {
                "agent_session_provisioned",
                "agent_session_reused",
                "agent_session_bootstrap_verified",
            }:
                receipt_errors.append(f"line {index} has an invalid event")
            if record.get("binding_ref") != "agent-sessions.json":
                receipt_errors.append(f"line {index} has an invalid binding ref")
            if type(record.get("generation")) is not int or int(record["generation"]) < 1:
                receipt_errors.append(f"line {index} has an invalid generation")
            if not re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(record.get("identity_token") or ""),
            ):
                receipt_errors.append(f"line {index} has an invalid identity token")
            if (
                record.get("event") != "agent_session_bootstrap_verified"
                and record.get("focused_at_provisioning") is not False
            ):
                receipt_errors.append(
                    f"line {index} does not prove non-focused provisioning"
                )
        unknown_receipt_agents = sorted(
            {
                str(record.get("agent") or "")
                for record in receipts
                if str(record.get("agent") or "") not in bindings
            }
        )
        if unknown_receipt_agents:
            receipt_errors.append(
                "receipts have no projected binding for: " + ", ".join(unknown_receipt_agents)
            )
        for agent, binding in bindings.items():
            if not isinstance(binding, dict):
                receipt_errors.append(f"{agent}: projected binding is not an object")
                continue
            if binding.get("focused_at_provisioning") is not False:
                receipt_errors.append(
                    f"{agent}: projected binding does not prove non-focused provisioning"
                )
            generation = binding.get("generation")
            identity = binding.get("runtime_identity") or {}
            provisioned = [
                record
                for record in receipts
                if record.get("agent") == agent
                and record.get("event") == "agent_session_provisioned"
            ]
            provisioned_generations = sorted(
                record.get("generation")
                for record in provisioned
                if type(record.get("generation")) is int
            )
            if type(generation) is not int or provisioned_generations != list(
                range(1, int(generation or 0) + 1)
            ):
                receipt_errors.append(
                    f"{agent}: provisioning generations do not match the current binding"
                )
                continue
            current_provisioning_receipt = next(
                (
                    record
                    for record in provisioned
                    if record.get("generation") == generation
                ),
                None,
            )
            if not current_provisioning_receipt or any(
                (
                    current_provisioning_receipt.get("adapter") != "herdr",
                    current_provisioning_receipt.get("identity_token") != identity.get("token"),
                    current_provisioning_receipt.get("ownership") != binding.get("ownership"),
                    current_provisioning_receipt.get("context") != binding.get("context"),
                    current_provisioning_receipt.get("launch") != binding.get("launch"),
                    current_provisioning_receipt.get("runtime_scope") != binding.get("runtime_scope"),
                    current_provisioning_receipt.get("runtime_identity") != identity,
                )
            ):
                receipt_errors.append(
                    f"{agent}: current binding has no matching provisioning receipt"
                )
            elif binding.get("provisioned_at") and (
                current_provisioning_receipt.get("ts") != binding.get("provisioned_at")
            ):
                receipt_errors.append(
                    f"{agent}: provisioning timestamp does not match the immutable receipt"
                )
            if binding.get("lifecycle") == "bootstrap_ready":
                verification = binding.get("bootstrap_verification") or {}
                bootstrap_receipts = [
                    record
                    for record in receipts
                    if record.get("agent") == agent
                    and record.get("event") == "agent_session_bootstrap_verified"
                    and record.get("generation") == generation
                ]
                if (
                    not binding_has_verified_bootstrap_lifecycle(binding)
                    or len(bootstrap_receipts) != 1
                    or bootstrap_receipts[0].get("identity_token") != identity.get("token")
                    or bootstrap_receipts[0].get("evidence_ref")
                    != verification.get("evidence_ref")
                    or bootstrap_receipts[0].get("native_session_id")
                    != verification.get("native_session_id")
                ):
                    receipt_errors.append(
                        f"{agent}: verified bootstrap lifecycle has no matching receipt"
                    )
        if receipt_errors:
            raise HerdrSubmissionError(
                "HERDR task-owned session receipt ledger conflicts: "
                + "; ".join(receipt_errors[:5])
            )
        sequence = max(
            [
                int(record.get("event_sequence"))
                for record in receipts
                if type(record.get("event_sequence")) is int
            ],
            default=0,
        )
        for agent in agent_names:
            launch_argv = herdr_launch_argv_for(agent, capabilities)
            if not launch_argv:
                raise HerdrSubmissionError(
                    f"HERDR has no task-owned session launch entrypoint for Agent {agent}"
                )
            binding = provision_herdr_agent_session(
                herdr,
                task_id=task_id,
                agent=agent,
                project_root=root,
                launch_argv=launch_argv,
                existing_binding=(projection.get("bindings") or {}).get(agent),
                run_command=run_command,
                allow_launch_argv_change=allow_launch_argv_change,
                allow_done_session_reprovision=allow_done_session_reprovision,
            )
            projection["bindings"][agent] = binding
            projection["status"] = "provisioning"
            projection["updated_at"] = now_iso()
            write_json(projection_path, projection)
            if binding["lifecycle"] == "bootstrap_ready":
                continue
            if binding["lifecycle"] == "provisioned" and any(
                record.get("agent") == agent
                and record.get("event") == "agent_session_provisioned"
                and record.get("generation") == binding.get("generation")
                and record.get("identity_token")
                == (binding.get("runtime_identity") or {}).get("token")
                for record in receipts
            ):
                continue
            sequence += 1
            append_json_line_durable(
                receipts_path,
                {
                    "schema_version": "valp-agent-session-receipt.v1",
                    "adapter": "herdr",
                    "task_id": task_id,
                    "event_sequence": sequence,
                    "ts": (
                        binding["provisioned_at"]
                        if binding["lifecycle"] == "provisioned"
                        else now_iso()
                    ),
                    "agent": agent,
                    "event": f"agent_session_{binding['lifecycle']}",
                    "binding_ref": "agent-sessions.json",
                    "generation": binding["generation"],
                    "identity_token": binding["runtime_identity"]["token"],
                    "ownership": binding["ownership"],
                    "context": binding["context"],
                    "launch": binding["launch"],
                    "focused_at_provisioning": binding["focused_at_provisioning"],
                    "runtime_scope": binding["runtime_scope"],
                    "runtime_identity": binding["runtime_identity"],
                },
            )
        projection["status"] = "ready"
        projection["updated_at"] = now_iso()
        write_json(projection_path, projection)
    return projection


def collect_runtime_preflight(
    agent_names: list[str] | None = None,
    runtime: str | None = None,
    session_bindings: dict[str, Any] | None = None,
    launch_argv_by_agent: dict[str, list[str]] | None = None,
    version_command_by_agent: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    runtime_kind = resolve_runtime(runtime)
    if runtime_kind == "manual":
        return collect_manual_preflight(agent_names)
    if runtime_kind == "queue":
        return collect_queue_preflight(agent_names)
    if runtime_kind == "langgraph":
        from .langgraph_adapter import collect_langgraph_preflight

        return collect_langgraph_preflight(agent_names)
    return collect_herdr_preflight(
        agent_names,
        session_bindings=session_bindings,
        launch_argv_by_agent=launch_argv_by_agent,
        version_command_by_agent=version_command_by_agent,
    )


def runtime_preflight_for_agents(
    preflight: dict[str, Any],
    agent_names: list[str],
) -> dict[str, Any]:
    selected = set(agent_names)
    filtered = json.loads(json.dumps(preflight)) if isinstance(preflight, dict) else {}
    all_agents = filtered.get("agents") or {}
    filtered["agents"] = {
        agent: record
        for agent, record in all_agents.items()
        if agent in selected
    }
    adapter_class = str(filtered.get("adapter_class") or "")
    if adapter_class == "manual":
        filtered["status"] = "not_applicable"
        return filtered
    checks = filtered.get("checks") or {}
    records = filtered["agents"].values()
    if any(isinstance(check, dict) and check.get("status") == "fail" for check in checks.values()):
        filtered["status"] = "fail"
    elif any(isinstance(record, dict) and record.get("status") == "fail" for record in records):
        filtered["status"] = "fail"
    elif any(isinstance(check, dict) and check.get("status") == "warn" for check in checks.values()):
        filtered["status"] = "warn"
    elif any(isinstance(record, dict) and record.get("status") == "warn" for record in records):
        filtered["status"] = "warn"
    else:
        filtered["status"] = "pass"
    return filtered


def model_probe_from_runtime_metadata(
    agent: str,
    metadata: dict[str, Any] | None,
    *,
    source: str,
    observed_at: str | None = None,
    ttl_seconds: int = 3600,
) -> dict[str, Any]:
    metadata = metadata if isinstance(metadata, dict) else {}
    def first_value(containers: list[dict[str, Any]], *keys: str) -> str:
        for container in containers:
            for key in keys:
                value = container.get(key)
                if isinstance(value, (str, int, float)) and str(value).strip():
                    return str(value).strip()
        return "unknown"

    runtime_metadata = metadata.get("runtime") if isinstance(metadata.get("runtime"), dict) else {}
    identity_metadata = metadata.get("model_identity") if isinstance(metadata.get("model_identity"), dict) else {}
    model_metadata = metadata.get("model") if isinstance(metadata.get("model"), dict) else {}
    token_metadata = metadata.get("tokens") if isinstance(metadata.get("tokens"), dict) else {}
    model_id = first_value(
        [metadata, runtime_metadata, identity_metadata, token_metadata],
        "active_model_id",
        "model_id",
        "active_model",
        "model_name",
    )
    if model_id == "unknown":
        model_id = first_value([model_metadata], "model_id", "id", "name")
    if model_id == "unknown" and isinstance(metadata.get("model"), str):
        model_id = str(metadata["model"]).strip() or "unknown"
    provider = first_value(
        [metadata, runtime_metadata, identity_metadata, model_metadata, token_metadata],
        "model_provider",
        "provider",
        "provider_name",
    )
    reasoning_mode = first_value(
        [metadata, runtime_metadata, identity_metadata, model_metadata, token_metadata],
        "reasoning_mode",
        "reasoning",
        "effort",
    )
    generation = first_value(
        [metadata],
        "adapter_generation",
        "session_generation",
        "generation",
    )

    session_parts = {"agent": agent}
    for key in (
        "session_change_token",
        "session_token",
        "session_id",
        "terminal_id",
        "worker_id",
        "hosted_run_id",
        "adapter_generation",
        "session_generation",
        "generation",
    ):
        value = metadata.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            session_parts[key] = str(value).strip()
    session_known = len(session_parts) > 1
    session_token = "unknown"
    if session_known:
        digest = hashlib.sha256(
            json.dumps(session_parts, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        session_token = f"sha256:{digest}"

    probe_status = "observed" if model_id != "unknown" else "unsupported"
    timestamp = observed_at or now_iso()
    return {
        "schema_version": "valp-model-probe.v1",
        "status": probe_status,
        "source": source,
        "observed_at": timestamp,
        "ttl_seconds": bounded_observation_ttl(ttl_seconds),
        "model": {
            "model_id": model_id,
            "provider": provider,
            "reasoning_mode": reasoning_mode,
            "confidence": "high" if probe_status == "observed" else "unknown",
        },
        "session_identity": {
            "status": "known" if session_known else "unknown",
            "token": session_token,
            "source": source,
            "generation": generation,
        },
    }


def launch_attestation_from_task_owned_binding(
    agent: str,
    binding: dict[str, Any] | None,
    adapter_probe: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Record launch intent without promoting it to runtime model observation."""
    probe = adapter_probe if isinstance(adapter_probe, dict) else {}
    if probe.get("status") != "unsupported" or not isinstance(binding, dict):
        return None
    ownership = binding.get("ownership") if isinstance(binding.get("ownership"), dict) else {}
    identity = binding.get("runtime_identity") if isinstance(binding.get("runtime_identity"), dict) else {}
    launch = binding.get("launch") if isinstance(binding.get("launch"), dict) else {}
    argv = launch.get("argv") if isinstance(launch.get("argv"), list) else []
    if (
        binding.get("agent") != agent
        or binding.get("dispatch_eligible") is not True
        or ownership.get("scope") != "task"
        or not str(ownership.get("task_id") or "").strip()
        or not all(str(identity.get(key) or "").strip() for key in ("pane_id", "terminal_id", "workspace_id", "tab_id"))
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(identity.get("token") or ""))
        or not argv
        or not all(isinstance(value, str) and value.strip() for value in argv)
    ):
        return None
    provisioned_at = str(binding.get("provisioned_at") or "").strip()
    freshness, age_seconds = observation_freshness(
        provisioned_at,
        evaluated_at=now_iso(),
        ttl_seconds=3600,
    )
    if freshness == "unknown":
        return None

    model_ids: list[str] = []
    reasoning_modes: list[str] = []
    for index, argument in enumerate(argv):
        if argument in {"--model", "--model-id", "-m"} and index + 1 < len(argv):
            model_ids.append(argv[index + 1])
        elif argument.startswith("--model=") or argument.startswith("--model-id="):
            model_ids.append(argument.split("=", 1)[1])
        elif argument in {"--reasoning", "--reasoning-effort"} and index + 1 < len(argv):
            reasoning_modes.append(argv[index + 1])
        elif argument.startswith("--reasoning=") or argument.startswith("--reasoning-effort="):
            reasoning_modes.append(argument.split("=", 1)[1])
        elif argument == "-c" and index + 1 < len(argv):
            config = argv[index + 1]
            if config.startswith("model_reasoning_effort="):
                reasoning_modes.append(config.split("=", 1)[1].strip("\"'"))

    unique_models = list(dict.fromkeys(value.strip() for value in model_ids if value.strip()))
    unique_reasoning = list(dict.fromkeys(value.strip() for value in reasoning_modes if value.strip()))
    if len(unique_models) != 1 or len(unique_reasoning) > 1:
        return None

    return {
        "schema_version": "valp-launch-attestation.v1",
        "status": "launch_attested",
        "source": "task-owned provisioning launch receipt",
        "attested_at": provisioned_at,
        "ttl_seconds": 3600,
        "freshness": freshness,
        "age_seconds": age_seconds,
        "model": {
            "model_id": unique_models[0],
            "provider": "unknown",
            "reasoning_mode": unique_reasoning[0] if unique_reasoning else "unknown",
            "confidence": "low",
        },
        "session_identity": {
            "status": "known",
            "token": identity["token"],
            "source": "task-owned provisioning receipt",
            "generation": str(binding.get("generation") or "unknown"),
        },
    }


def task_owned_runtime_binding_errors(
    agent: str,
    binding: dict[str, Any] | None,
    readiness: dict[str, Any] | None,
    probe: dict[str, Any] | None,
) -> list[str]:
    """Require current public runtime evidence to agree with a task binding."""
    if not isinstance(binding, dict):
        return ["task-owned binding is missing"]
    readiness = readiness if isinstance(readiness, dict) else {}
    probe = probe if isinstance(probe, dict) else {}
    launch = binding.get("launch") if isinstance(binding.get("launch"), dict) else {}
    argv = launch.get("argv") if isinstance(launch.get("argv"), list) else []
    bootstrap = binding.get("bootstrap_verification")
    bootstrap = bootstrap if isinstance(bootstrap, dict) else {}
    # Older generic bindings did not establish a model-bearing bootstrap contract.
    if not argv or not bootstrap:
        return []
    errors: list[str] = []
    readiness_identity = readiness.get("session_identity")
    readiness_identity = readiness_identity if isinstance(readiness_identity, dict) else {}
    native_identity = readiness_identity.get("identity")
    native_identity = native_identity if isinstance(native_identity, dict) else {}
    readiness_status = readiness.get("agent_status")
    accepted_readiness = readiness_status == "idle" or (
        readiness_status == "done"
        and binding_has_verified_bootstrap_lifecycle(binding)
    )
    if readiness.get("ready") is not True or not accepted_readiness:
        errors.append("public readiness is not ready and idle")
    if readiness_identity.get("status") != "known" or not str(native_identity.get("value") or "").strip():
        errors.append("native session identity is unknown")
    elif str(bootstrap.get("native_session_id") or "").strip() != str(native_identity.get("value") or "").strip():
        errors.append("native session identity does not match the task-owned binding")

    model = probe.get("model") if isinstance(probe.get("model"), dict) else {}
    session = probe.get("session_identity") if isinstance(probe.get("session_identity"), dict) else {}
    freshness, _ = observation_freshness(
        probe.get("observed_at"),
        evaluated_at=now_iso(),
        ttl_seconds=bounded_observation_ttl(probe.get("ttl_seconds")),
    )
    if probe.get("status") != "observed" or session.get("status") != "known" or freshness != "current":
        errors.append("current structured model observation is unavailable or stale")

    expected_model = None
    expected_reasoning = None
    for index, argument in enumerate(argv):
        if argument in {"--model", "--model-id", "-m"} and index + 1 < len(argv):
            expected_model = argv[index + 1]
        elif argument.startswith("--model=") or argument.startswith("--model-id="):
            expected_model = argument.split("=", 1)[1]
        elif argument == "-c" and index + 1 < len(argv):
            config = argv[index + 1]
            if config.startswith("model_reasoning_effort="):
                expected_reasoning = config.split("=", 1)[1].strip("\"'")
    if expected_model and str(model.get("model_id") or "").strip() != expected_model:
        errors.append("current structured model observation does not match the task-owned binding")
    if expected_reasoning and str(model.get("reasoning_mode") or "").strip() != expected_reasoning:
        errors.append("current structured model observation does not match the task-owned binding")
    return list(dict.fromkeys(errors))


def collect_manual_preflight(agent_names: list[str] | None = None) -> dict[str, Any]:
    agents = {
        agent: {
            "status": "not_applicable",
            "session_status": "manual",
            "model_probe": model_probe_from_runtime_metadata(
                agent,
                {},
                source="Manual adapter does not expose active model metadata",
            ),
            "notes": ["Manual Mode has no runtime dispatch proof."],
        }
        for agent in agent_names or []
    }
    return {
        "generated_at": now_iso(),
        "runtime": "manual",
        "adapter_class": "manual",
        "status": "not_applicable",
        "checks": {
            "manual_mode": {
                "status": "not_applicable",
                "message": "Manual Mode records dispatch files and manual attestations only.",
            }
        },
        "agents": agents,
    }


def collect_queue_preflight(agent_names: list[str] | None = None) -> dict[str, Any]:
    agents = {}
    for agent in agent_names or []:
        agents[agent] = {
            "status": "pass",
            "queue_id": f"queue-{agent}",
            "worker_id": f"worker-{agent}",
            "session_status": "idle",
            "output_ref": f"agents/{agent}/evidence.md",
            "expected_refs": [f"agents/{agent}/evidence.md"],
            "model_probe": model_probe_from_runtime_metadata(
                agent,
                {},
                source="Queue adapter does not expose active model metadata",
            ),
            "notes": ["Headless queue adapters use queue/session facts instead of pane or terminal-size facts."],
        }
    return {
        "generated_at": now_iso(),
        "runtime": "VALP headless queue",
        "adapter_class": "daemon_queue",
        "status": "pass",
        "checks": {
            "queue_available": {"status": "pass"},
            "worker_available": {"status": "pass"},
        },
        "agents": agents,
    }


def task_owned_agent_state_observation(
    pane: dict[str, Any],
    binding: dict[str, Any] | None,
) -> dict[str, Any]:
    native_state = str(pane.get("agent_status") or "unknown").strip().lower()
    if not isinstance(binding, dict):
        return {
            "status": "unbound",
            "source": "herdr_runtime",
            "state": native_state,
            "native_state": native_state,
        }
    tokens = pane.get("tokens") if isinstance(pane.get("tokens"), dict) else {}
    reporter_keys = {
        "valp_agent_state",
        "valp_agent_state_source",
        "valp_agent_state_sequence",
        "valp_agent_state_generation",
        "valp_agent_session_id",
    }
    if not reporter_keys.intersection(tokens):
        return {
            "status": "unreported",
            "source": "herdr_runtime",
            "state": native_state,
            "native_state": native_state,
        }

    reporter_state = str(tokens.get("valp_agent_state") or "").strip().lower()
    reporter_source = str(tokens.get("valp_agent_state_source") or "").strip()
    reporter_session_id = str(tokens.get("valp_agent_session_id") or "").strip()
    sequence_text = str(tokens.get("valp_agent_state_sequence") or "").strip()
    generation_text = str(tokens.get("valp_agent_state_generation") or "").strip()
    ownership = binding.get("ownership") if isinstance(binding.get("ownership"), dict) else {}
    bound_task_id = str(ownership.get("task_id") or "").strip()
    bound_agent = str(binding.get("agent") or "").strip()
    conflicts: list[str] = []
    if reporter_state not in {"idle", "working", "blocked"}:
        conflicts.append("state")
    if not reporter_source:
        conflicts.append("source")
    if (
        not reporter_session_id
        or not bound_task_id
        or not bound_agent
        or not reporter_session_id.startswith(f"{bound_task_id}:{bound_agent}:")
    ):
        conflicts.append("session_id")
    try:
        sequence = int(sequence_text)
    except ValueError:
        sequence = 0
    if sequence < 1:
        conflicts.append("sequence")
    try:
        generation = int(generation_text)
    except ValueError:
        generation = 0
    if generation != binding.get("generation"):
        conflicts.append("generation")
    if conflicts:
        return {
            "status": "conflict",
            "source": "task_owned_reporter",
            "state": "unknown",
            "native_state": native_state,
            "conflicts": conflicts,
        }
    return {
        "status": "bound",
        "source": "task_owned_reporter",
        "state": reporter_state,
        "native_state": native_state,
        "reporter_source": reporter_source,
        "sequence": sequence,
        "session_id": reporter_session_id,
        "generation": generation,
    }


def activated_herdr_executable() -> str | None:
    invoked = Path(sys.argv[0]).expanduser()
    if not invoked.is_absolute():
        resolved_invoked = shutil.which(str(invoked))
        if resolved_invoked:
            invoked = Path(resolved_invoked)
    sibling = invoked.parent / "herdr"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return shutil.which("herdr")


def collect_herdr_preflight(
    agent_names: list[str] | None = None,
    session_bindings: dict[str, Any] | None = None,
    launch_argv_by_agent: dict[str, list[str]] | None = None,
    version_command_by_agent: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    herdr = activated_herdr_executable()
    preflight: dict[str, Any] = {
        "generated_at": now_iso(),
        "runtime": "HERDR",
        "adapter_class": "pane_controller",
        "status": "pass" if herdr else "fail",
        "checks": {},
        "agents": {},
    }
    if not herdr:
        preflight["checks"]["herdr_cli"] = {"status": "fail", "message": "herdr command not found; HERDR pane-controller runtime is unavailable."}
        return preflight

    submission_capability = detect_herdr_submission_capability(herdr, run_command)
    preflight["checks"]["submission_transport"] = submission_capability
    preflight["checks"]["session_provisioning"] = detect_herdr_session_provisioning_capability(
        herdr,
        run_command,
    )

    status_result = run_command([herdr, "status", "--json"], timeout=5.0)
    status_json = parse_json_stdout(status_result)
    restart_needed = bool(((status_json.get("server") or {}).get("restart_needed")) or ((status_json.get("update") or {}).get("restart_needed")))
    preflight["checks"]["herdr_status"] = {
        "status": "fail" if not status_result.get("ok") or restart_needed else "pass",
        "exit_code": status_result.get("exit_code"),
        "restart_needed": restart_needed,
        "client_version": (status_json.get("client") or {}).get("version"),
        "server_version": (status_json.get("server") or {}).get("version"),
    }

    if session_bindings is None:
        pane_commands = [[herdr, "pane", "list"]]
        queried_workspaces: list[str] = []
    else:
        queried_workspaces = sorted(
            {
                str(((binding or {}).get("runtime_identity") or {}).get("workspace_id") or "").strip()
                for binding in session_bindings.values()
                if isinstance(binding, dict)
                and str(((binding.get("runtime_identity") or {}).get("workspace_id") or "")).strip()
            }
        )
        pane_commands = [
            [herdr, "pane", "list", "--workspace", workspace_id]
            for workspace_id in queried_workspaces
        ]
    pane_results = [
        run_command(
            command,
            timeout=5.0,
            stdout_limit=HERDR_PANE_LIST_STDOUT_LIMIT,
        )
        for command in pane_commands
    ]
    panes: list[dict[str, Any]] = []
    seen_pane_ids: set[str] = set()
    for pane_result in pane_results:
        pane_json = parse_json_stdout(pane_result)
        for pane in (((pane_json.get("result") or {}).get("panes")) or []) if pane_json else []:
            if not isinstance(pane, dict):
                continue
            pane_id = str(pane.get("pane_id") or "")
            if pane_id and pane_id not in seen_pane_ids:
                panes.append(pane)
                seen_pane_ids.add(pane_id)
    preflight["checks"]["pane_list"] = {
        "status": "pass" if pane_results and all(result.get("ok") for result in pane_results) else "fail",
        "count": len(panes),
        "workspace_ids": queried_workspaces,
    }

    panes_by_agent: dict[str, dict[str, Any]] = {}
    session_panes_by_agent: dict[str, list[dict[str, Any]]] = {}
    binding_errors: dict[str, str] = {}
    if session_bindings is None:
        for pane in panes:
            agent = str(pane.get("agent") or "").strip()
            if not agent:
                continue
            session_panes_by_agent.setdefault(agent, []).append(pane)
        panes_by_agent = {
            agent: session_panes[0]
            for agent, session_panes in session_panes_by_agent.items()
        }
    else:
        for agent in agent_names or []:
            binding = session_bindings.get(agent) if isinstance(session_bindings, dict) else None
            identity = binding.get("runtime_identity") if isinstance(binding, dict) else None
            pane_id = str((identity or {}).get("pane_id") or "")
            pane = next(
                (
                    item
                    for item in panes
                    if isinstance(item, dict) and str(item.get("pane_id") or "") == pane_id
                ),
                None,
            )
            if not isinstance(binding, dict) or not pane_id or pane is None:
                binding_errors[agent] = "Task-owned session binding is absent from the runtime."
                continue
            observed_identity = {
                key: str(pane.get(key) or "")
                for key in ("pane_id", "terminal_id", "workspace_id", "tab_id")
            }
            recorded_identity = {
                key: str((identity or {}).get(key) or "")
                for key in ("pane_id", "terminal_id", "workspace_id", "tab_id")
            }
            reported_agent = str(pane.get("agent") or "")
            reported_cwd = str(pane.get("cwd") or "")
            if (
                observed_identity != recorded_identity
                or reported_agent != agent
                or not reported_cwd
                or Path(reported_cwd).resolve()
                != Path(str((binding.get("context") or {}).get("cwd") or "")).resolve()
            ):
                binding_errors[agent] = "Task-owned session binding conflicts with fresh runtime identity."
                continue
            panes_by_agent[agent] = pane
    def build_agent_record(
        agent: str,
        pane: dict[str, Any] | None,
        binding: dict[str, Any] | None,
        binding_error: str | None,
    ) -> dict[str, Any]:
        binding = session_bindings.get(agent) if isinstance(session_bindings, dict) else None
        launch_argv = (
            ((binding or {}).get("launch") or {}).get("argv")
            or (launch_argv_by_agent or {}).get(agent)
        )
        adapter_model_probe = (
            herdr_model_probe(herdr, str((pane or {}).get("pane_id") or ""))
            if pane
            else {
                "schema_version": "valp-model-probe.v1",
                "status": "unavailable",
                "source": "HERDR agent.model_probe public runtime API",
                "observed_at": None,
                "ttl_seconds": 3600,
                "model": None,
                "session_identity": None,
            }
        )
        adapter_readiness = (
            herdr_named_agent_readiness(herdr, str((pane or {}).get("pane_id") or ""))
            if pane
            else {
                "schema_version": "valp-named-agent-readiness.v1",
                "ready": False,
                "reason_code": "agent_not_addressable",
                "addressable": False,
                "detected_agent": None,
                "agent_status": None,
                "interactive_ready": False,
                "prompt_eligible": False,
                "session_identity": {"status": "unknown", "identity": None},
                "state_change_seq": 0,
            }
        )
        agent_record = {
            "status": "warn",
            "session_id": str((pane or {}).get("pane_id") or "unknown"),
            "agent_status": None,
            "pane_id": None,
            "terminal_size": None,
            "min_terminal_size": AGENT_MIN_TERMINAL_SIZE.get(agent, DEFAULT_MIN_TERMINAL_SIZE),
            "terminal_size_status": "unknown",
            "cli": cli_preflight_for_agent(
                agent,
                launch_argv=launch_argv,
                version_command=(version_command_by_agent or {}).get(agent),
            ),
            "model_probe": adapter_model_probe,
            "readiness": adapter_readiness,
            "notes": [],
        }
        launch_attestation = launch_attestation_from_task_owned_binding(
            agent,
            binding,
            adapter_model_probe,
        )
        if launch_attestation is not None:
            agent_record["launch_attestation"] = launch_attestation
        if session_bindings is not None:
            agent_record["session_binding"] = {
                "status": "bound" if binding_error is None else "conflict",
                "ref": "agent-sessions.json",
                "generation": (binding or {}).get("generation"),
                "identity_token": ((binding or {}).get("runtime_identity") or {}).get("token"),
                "ownership": (binding or {}).get("ownership"),
            }
        if binding_error is not None:
            agent_record["status"] = "fail"
            agent_record["notes"].append(binding_error)
            return agent_record
        if adapter_readiness.get("ready") is not True:
            agent_record["status"] = "fail"
            agent_record["notes"].append(
                "HERDR named-Agent readiness is not ready: "
                + str(adapter_readiness.get("reason_code") or "unavailable")
            )
            return agent_record
        if not pane:
            agent_record["status"] = "warn"
            agent_record["model_probe"]["status"] = "unavailable"
            agent_record["notes"].append("No current pane was reported for this agent.")
            return agent_record

        pane_id = str(pane.get("pane_id"))
        agent_record["pane_id"] = pane_id
        state_observation = task_owned_agent_state_observation(pane, binding)
        agent_record["agent_status"] = state_observation["state"]
        agent_record["agent_status_observation"] = state_observation
        if session_bindings is not None:
            binding_errors = task_owned_runtime_binding_errors(
                agent,
                binding,
                adapter_readiness,
                adapter_model_probe,
            )
            if binding_errors:
                agent_record["status"] = "fail"
                agent_record["notes"].extend(binding_errors)
                return agent_record
        layout_result = run_command([herdr, "pane", "layout", "--pane", pane_id], timeout=5.0)
        layout_json = parse_json_stdout(layout_result)
        rect = pane_rect_from_layout(layout_json, pane_id)
        if rect:
            size = {"width": int(rect.get("width", 0)), "height": int(rect.get("height", 0))}
            agent_record["terminal_size"] = size
            minimum = agent_record["min_terminal_size"]
            ok_size = size["width"] >= minimum["width"] and size["height"] >= minimum["height"]
            agent_record["terminal_size_status"] = "pass" if ok_size else "fail"
            if not ok_size:
                agent_record["notes"].append("Pane is smaller than the minimum terminal size for reliable TUI rendering.")
        else:
            agent_record["notes"].append("Pane size could not be read from runtime layout output.")

        cli_status = (agent_record.get("cli") or {}).get("status")
        terminal_status = agent_record.get("terminal_size_status")
        agent_status = str(agent_record.get("agent_status") or "unknown").strip().lower()
        if state_observation.get("status") == "conflict":
            agent_record["status"] = "fail"
            agent_record["notes"].append(
                "Task-owned structured Agent state conflicts with the accepted binding: "
                + ", ".join(state_observation.get("conflicts") or [])
            )
        elif session_bindings is not None and not (
            agent_status in {"idle", "working"}
            or (
                agent_status == "done"
                and binding_has_verified_bootstrap_lifecycle(binding)
            )
        ):
            agent_record["status"] = "fail"
            agent_record["notes"].append(
                "Task-owned session has no structured idle/working Agent state."
            )
        elif terminal_status == "fail" or cli_status == "fail":
            agent_record["status"] = "fail"
        elif terminal_status == "unknown" or cli_status == "warn":
            agent_record["status"] = "warn"
        else:
            agent_record["status"] = "pass"
        return agent_record

    if session_bindings is None:
        agents_to_report = sorted(set(agent_names or []) | set(panes_by_agent))
    else:
        agents_to_report = list(agent_names or [])
    for agent in agents_to_report:
        binding = session_bindings.get(agent) if isinstance(session_bindings, dict) else None
        if session_bindings is None:
            session_records = [
                build_agent_record(agent, pane, None, None)
                for pane in session_panes_by_agent.get(agent, [])
            ]
            if session_records:
                status_rank = {"pass": 0, "warn": 1, "fail": 2}
                representative = min(
                    session_records,
                    key=lambda record: (
                        status_rank.get(str(record.get("status") or "warn"), 1),
                        (record.get("readiness") or {}).get("ready") is not True,
                        (record.get("model_probe") or {}).get("status") != "observed",
                    ),
                )
                agent_record = dict(representative)
                agent_record["sessions"] = session_records
            else:
                agent_record = build_agent_record(agent, None, None, None)
        else:
            agent_record = build_agent_record(
                agent,
                panes_by_agent.get(agent),
                binding,
                binding_errors.get(agent),
            )
        preflight["agents"][agent] = agent_record

    if any(isinstance(check, dict) and check.get("status") == "fail" for check in preflight["checks"].values()):
        preflight["status"] = "fail"
    elif any(record.get("status") == "fail" for record in preflight["agents"].values()):
        preflight["status"] = "fail"
    elif any(record.get("status") == "warn" for record in preflight["agents"].values()):
        preflight["status"] = "warn"
    return preflight


def herdr_model_probe(
    herdr: str,
    pane_id: str,
) -> dict[str, Any]:
    return herdr_model_probe_with_runner(herdr, pane_id, run_command)


def herdr_model_probe_with_runner(
    herdr: str,
    pane_id: str,
    run_command_fn: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    result = run_command_fn(
        [herdr, "agent", "model-probe", pane_id, "--json"],
        timeout=5.0,
    )
    response = parse_json_stdout(result)
    payload = response.get("result") if isinstance(response, dict) else None
    probe = payload.get("probe") if isinstance(payload, dict) else None
    if (
        result.get("ok") is True
        and isinstance(payload, dict)
        and payload.get("type") == "agent_model_probe"
        and isinstance(probe, dict)
        and probe.get("schema_version") == "valp-model-probe.v1"
        and probe.get("status") in {"observed", "unsupported"}
    ):
        return dict(probe)
    return {
        "schema_version": "valp-model-probe.v1",
        "status": "unavailable",
        "source": "HERDR agent.model_probe public runtime API",
        "observed_at": None,
        "ttl_seconds": 3600,
        "model": None,
        "session_identity": None,
    }


def herdr_named_agent_readiness(herdr: str, pane_id: str) -> dict[str, Any]:
    unavailable = {
        "schema_version": "valp-named-agent-readiness.v1",
        "ready": False,
        "reason_code": "unavailable",
        "addressable": False,
        "detected_agent": None,
        "agent_status": None,
        "interactive_ready": False,
        "prompt_eligible": False,
        "session_identity": {"status": "unknown", "identity": None},
        "state_change_seq": 0,
    }
    result = run_command([herdr, "agent", "readiness", pane_id], timeout=5.0)
    response = parse_json_stdout(result)
    payload = response.get("result") if isinstance(response, dict) else None
    readiness = payload.get("readiness") if isinstance(payload, dict) else None
    if not (
        result.get("ok") is True
        and isinstance(payload, dict)
        and payload.get("type") == "agent_readiness"
        and isinstance(readiness, dict)
        and readiness.get("schema_version") == "valp-named-agent-readiness.v1"
        and readiness.get("ready") is True
        and readiness.get("reason_code") == "ready"
        and readiness.get("addressable") is True
        and isinstance(readiness.get("detected_agent"), str)
        and isinstance(readiness.get("agent_status"), str)
        and readiness.get("interactive_ready") is True
        and readiness.get("prompt_eligible") is True
        and isinstance(readiness.get("session_identity"), dict)
        and readiness["session_identity"].get("status") == "known"
        and isinstance(readiness.get("state_change_seq"), int)
        and not isinstance(readiness.get("state_change_seq"), bool)
    ):
        return unavailable
    return dict(readiness)


def pane_rect_from_layout(layout_json: dict[str, Any], pane_id: str) -> dict[str, Any]:
    layout = (layout_json.get("result") or {}).get("layout") or {}
    for pane in layout.get("panes") or []:
        if pane.get("pane_id") == pane_id and isinstance(pane.get("rect"), dict):
            return pane["rect"]
    return {}


def cli_preflight_for_agent(
    agent: str,
    launch_argv: list[str] | None = None,
    version_command: list[str] | None = None,
) -> dict[str, Any]:
    if (
        isinstance(version_command, list)
        and version_command
        and all(isinstance(item, str) and item.strip() for item in version_command)
    ):
        command = [str(item) for item in version_command]
    else:
        command = None
    if not command:
        return {
            "status": "warn",
            "message": (
                f"Agent {agent} has no capability-declared runtime.version_command; "
                "no CLI version probe was attempted."
            ),
        }
    if not shutil.which(command[0]):
        return {"status": "warn", "command": command, "message": "CLI command was not found on PATH."}
    result = run_command(command, timeout=5.0)
    return {
        "status": "pass" if result.get("ok") else "fail",
        "command": command,
        "exit_code": result.get("exit_code"),
        "version_output": (result.get("stdout") or result.get("stderr") or "").strip()[:500],
    }


def provider_matrix_for(
    selected_agents: list[str],
    agents: dict[str, Any],
    overlay: dict[str, Any],
    preflight: dict[str, Any],
    *,
    evaluated_at: str | None = None,
    dynamic_discovery_required: bool = True,
) -> dict[str, Any]:
    overlay_profiles = overlay.get("agent_capability_profiles") or {}
    providers = {}
    for agent in selected_agents:
        info = agents.get(agent, {})
        overlay_profile = overlay_profiles.get(agent) or {}
        agent_preflight = (preflight.get("agents") or {}).get(agent, {})
        cli_record = agent_preflight.get("cli") or {}
        runtime_report = cli_record.get("version_output") or agent_preflight.get("worker_id") or agent_preflight.get("queue_id") or "unknown"
        cli_available = cli_record.get("status") in {"pass", "warn"} if cli_record else "unknown"
        model_identity = model_identity_for(
            agent,
            info,
            overlay_profile,
            runtime_probe=agent_preflight.get("model_probe"),
            evaluated_at=evaluated_at,
        )
        providers[agent] = {
            "provider_name": agent,
            "provider_version_or_runtime_report": runtime_report,
            "cli_available": cli_available,
            "mcp_support": "supported" if info.get("mcp_servers") else "unknown",
            "skill_discovery_path": overlay_profile.get("skill_library_paths") or "unknown",
            "session_resume_support": "unknown",
            "approval_behavior": overlay_profile.get("approval_behavior") or "unknown",
            "model_selection": model_selection_for(model_identity),
            "agent_surface": model_identity["agent_surface"],
            "model_identity": model_identity,
            "permissions": model_identity["permissions"],
            "context": model_identity["context"],
            "task_evidence": model_identity["task_evidence"],
            "max_concurrency": 1,
            "context_policy": context_policy_for(agent, info, overlay),
            "known_limitations": info.get("must_not_do") or [],
            "runtime_preflight": agent_preflight,
            "last_verified_at": now_iso(),
        }
    return {
        "generated_at": now_iso(),
        "runtime_preflight": preflight,
        "model_awareness": model_awareness_for(
            providers,
            dynamic_discovery_required=dynamic_discovery_required,
        ),
        "providers": providers,
    }


def dynamic_model_dispatch_errors(
    routing: dict[str, Any],
    agents: dict[str, Any],
    overlay: dict[str, Any],
    preflight: dict[str, Any],
    phases: list[tuple[str, str]],
    *,
    evaluated_at: str | None = None,
    allow_session_rebinding: bool = False,
) -> list[str]:
    routed_matrix = routing.get("provider_matrix") or {}
    awareness = routed_matrix.get("model_awareness") or {}
    if awareness.get("dynamic_discovery_required") is not True:
        return []
    routed_providers = routed_matrix.get("providers") or {}
    overlay_profiles = overlay.get("agent_capability_profiles") or {}
    preflight_agents = preflight.get("agents") or {}
    errors: list[str] = []
    for agent, role in phases:
        if role not in {"implementer", "reviewer"}:
            continue
        routed_record = routed_providers.get(agent) if isinstance(routed_providers, dict) else None
        routed_identity = routed_record.get("model_identity") if isinstance(routed_record, dict) else None
        agent_preflight = preflight_agents.get(agent) if isinstance(preflight_agents, dict) else None
        if not isinstance(routed_identity, dict) or not isinstance(agent_preflight, dict):
            errors.append(f"{role}:{agent} is missing routed or fresh model identity evidence")
            continue
        fresh_identity = model_identity_for(
            agent,
            agents.get(agent) or {},
            overlay_profiles.get(agent) or {},
            runtime_probe=agent_preflight.get("model_probe"),
            evaluated_at=evaluated_at,
        )
        eligibility_key = "implementer" if role == "implementer" else "final_reviewer"
        if fresh_identity["role_eligibility"][eligibility_key] != "eligible":
            errors.append(f"{role}:{agent} active model identity is not eligible at dispatch preflight")
        if not allow_session_rebinding:
            routed_fingerprint = (routed_identity.get("history_binding") or {}).get("fingerprint")
            fresh_fingerprint = (fresh_identity.get("history_binding") or {}).get("fingerprint")
            if not routed_fingerprint or routed_fingerprint != fresh_fingerprint:
                errors.append(f"{role}:{agent} model/session/freshness binding changed after routing")
    return list(dict.fromkeys(errors))


def expected_refs_for_agents(selected_agents: list[str], role_assignments: dict[str, str] | None = None) -> dict[str, list[str]]:
    role_assignments = role_assignments or {}
    refs = {}
    for agent in selected_agents:
        agent_refs: list[str] = []
        for role in roles_for_agent(role_assignments, agent):
            agent_refs.extend(role_expected_refs(agent, role))
        refs[agent] = list(dict.fromkeys(agent_refs))
    return refs


def write_dispatches(
    root: Path,
    directory: Path,
    task_id: str,
    profile: str,
    prompt: str,
    selected_agents: list[str],
    expected_by_agent: dict[str, list[str]],
    routing: dict[str, Any],
    skill_recommendations: dict[str, Any],
    visible_attention: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    task_brief = task_brief_for_dispatch(prompt)
    payload_records: dict[str, dict[str, Any]] = {}
    for agent in selected_agents:
        agent_dir = directory / "agents" / agent
        agent_dir.mkdir(parents=True, exist_ok=True)
        assigned_roles = roles_for_agent(routing.get("role_assignments") or {}, agent)
        is_coordinator = "coordinator" in assigned_roles
        slice_ref = (routing.get("skill_recommendation_slices") or {}).get(agent)
        control_slice_ref = (routing.get("control_slices") or {}).get(agent)
        contract_record = routing.get("control_contract") or {}
        task_directory_ref = relative_ref(directory, root)
        task_contract_ref = relative_ref(directory / CONTROL_CONTRACT_REF, root)
        task_control_slice_ref = relative_ref(directory / str(control_slice_ref or ""), root)
        control_slice = read_json(directory / str(control_slice_ref or "")) if control_slice_ref else {}
        control_errors = validate_control_slice(
            control_slice,
            task_id,
            agent,
            [
                str(item.get("work_item_id"))
                for item in (read_json(directory / "submission-dependencies.json").get("work_items") or [])
                if isinstance(item, dict) and item.get("agent") == agent
            ],
            str(contract_record.get("digest") or ""),
        )
        if control_errors:
            raise SystemExit(f"Invalid control slice for {agent}: " + "; ".join(control_errors))
        compact_control_slice = json.dumps(control_slice, ensure_ascii=False, separators=(",", ":"))
        task_refs = ["task.md", "context-pack.json"]
        if is_coordinator:
            task_refs.append("skill-recommendations.json")
        elif slice_ref:
            task_refs.append(slice_ref)
        core_task_refs = "\n".join(
            f"- `{relative_ref(directory / ref, root)}`"
            for ref in task_refs
        )
        compact_task_refs = "\n".join(f"- `{ref}`" for ref in task_refs)
        expected = "\n".join(f"- `{ref}`" for ref in expected_by_agent.get(agent, []))
        exact_evidence = "\n".join(f"- `{relative_ref(directory / ref, root)}`" for ref in expected_by_agent.get(agent, []))
        skill_source = skill_recommendations if is_coordinator else compact_skill_slice(task_id, agent, skill_recommendations)
        skills = format_skill_recommendations_for_dispatch(agent, skill_source, coordinator=is_coordinator)
        if slice_ref and str(slice_ref) not in skills:
            skills += f"\n- Provider slice: `{slice_ref}`."
        compact_skill_lines = [
            line
            for line in skills.splitlines()
            if "Full recommendation records remain" in line
            or "Recommendations filtered for" in line
            or "Provider slice:" in line
            or line.startswith("- Work item ")
        ]
        compact_skills = "\n".join(compact_skill_lines) or (
            "- Full recommendation records: `skill-recommendations.json`."
            if is_coordinator
            else "- Use only the provider-reachable skill slice for this dispatch."
        )
        minimal_skills = (
            f"- Provider slice: `{slice_ref}`."
            if slice_ref
            else "- No provider skill slice was generated."
        )
        attention_slice = attention_slice_for_agent(agent, visible_attention)
        budget = dispatch_budget_for_agent(agent, routing.get("role_assignments") or {})
        permission_boundary = (
            "- Read-only review: do not edit files, source, configuration, or runtime; "
            "the coordinator records the response.\n- Cite task-local evidence and honor gates."
            if budget["role"] == "reviewer"
            else "- Honor gates; write only expected evidence unless source edits are permitted. "
            "Cite runtime proof."
        )

        def render_dispatch(brief: str, attention: str, skill_text: str, actual_chars: int) -> str:
            return f"""# Dispatch: {agent}

Task: {task_id} | Profile: {profile}
Payload budget: {actual_chars}/{budget['max_chars']} chars.

## VALP Control Contract (Load First)

Load task-local `{task_contract_ref}` first; slice `{task_control_slice_ref}`; mismatch blocks.

{compact_control_slice}

## Project Root

`cd "{root}"`

## Role

Role: `{budget['role']}`. Evidence: `routing.json`.

## Task Brief

{brief}

## Task References

The coordinator/leader owns dispatch precision. Load:

{core_task_refs}
- Task directory: `{task_directory_ref}`; budget/gates: `iteration-budget.json`, `submission-dependencies.json`, `delegation-policy.json`

## Payload Budget

- Task-local refs only.

## Visible Attention Slice

{attention}

## Permission Boundary

{permission_boundary}
- Do not write skills, plugins, memory, MCP configuration, or agent configuration while delegated.

## Expected Evidence

{exact_evidence or expected}

## Recommended Skills

{skill_text}

## Required Response

Include blockers, confidence, `## Recommendations`, and:

```text
control_contract_ref: control-contract.json
control_contract_digest: {contract_record.get('digest')}
control_contract_status: honored
```
"""

        def render_compact_dispatch(actual_chars: int) -> str:
            return f"""# Dispatch: {agent}

Task: {task_id} | Profile: {profile}
Payload budget: {actual_chars}/{budget['max_chars']} chars.

## VALP Control Contract (Load First)

Task dir: `{task_directory_ref}`. In it, load `control-contract.json`, then `{control_slice_ref}`; mismatch blocks.

{compact_control_slice}

## Project Root

`cd "{root}"`

## Role

`{budget['role']}`; see task-dir `routing.json`.

## Task Brief

See task-dir `task.md`.

## Task References

All refs below are task-dir relative:

{compact_task_refs}
- `visible-routing.md`
- `iteration-budget.json`, `submission-dependencies.json`, `delegation-policy.json`

## Visible Attention Slice

- See task-dir `visible-routing.md` and `context-pack.json`.

## Permission Boundary

{permission_boundary}
- Do not write skills, plugins, memory, MCP configuration, or agent configuration while delegated.

## Expected Evidence

{expected or '- See task.md.'}

## Recommended Skills

{minimal_skills}

## Required Response

Include blockers, confidence, `## Recommendations`, and:

```text
control_contract_ref: control-contract.json
control_contract_digest: {contract_record.get('digest')}
control_contract_status: honored
```
"""

        variants = [
            (task_brief, attention_slice, skills),
            (bounded_text(task_brief, 320), attention_slice, compact_skills),
            (
                bounded_text(task_brief, 240),
                f"- Attention head(s): task-local role slice. See `visible-routing.md` and `context-pack.json`.",
                compact_skills,
            ),
            (
                bounded_text(task_brief, 160),
                "- See `context-pack.json` and `visible-routing.md`.",
                minimal_skills,
            ),
            (
                "See task refs.",
                "- See refs.",
                minimal_skills,
            ),
        ]
        dispatch = ""
        for brief, attention, skill_text in variants:
            actual_chars = 0
            for _ in range(4):
                candidate = render_dispatch(brief, attention, skill_text, actual_chars)
                next_chars = len(candidate)
                if next_chars == actual_chars:
                    break
                actual_chars = next_chars
            candidate = render_dispatch(brief, attention, skill_text, actual_chars)
            reference_tokens = (len(candidate) + 3) // 4
            if len(candidate) <= budget["max_chars"] and reference_tokens <= budget["max_reference_tokens"]:
                dispatch = candidate
                break
        if not dispatch:
            actual_chars = 0
            for _ in range(4):
                candidate = render_compact_dispatch(actual_chars)
                next_chars = len(candidate)
                if next_chars == actual_chars:
                    break
                actual_chars = next_chars
            candidate = render_compact_dispatch(actual_chars)
            reference_tokens = (len(candidate) + 3) // 4
            if len(candidate) <= budget["max_chars"] and reference_tokens <= budget["max_reference_tokens"]:
                dispatch = candidate
        if not dispatch:
            raise SystemExit(f"Dispatch payload exceeds role budget for {agent}")

        actual_chars = len(dispatch)
        payload_records[agent] = {
            **budget,
            "actual_chars": actual_chars,
            "actual_reference_tokens": (actual_chars + 3) // 4,
            "dispatch_ref": f"agents/{agent}/dispatch.md",
            "skill_slice_ref": slice_ref,
        }
        (agent_dir / "dispatch.md").write_text(dispatch, encoding="utf-8")
    return payload_records


def format_skill_recommendations_for_dispatch(
    agent: str,
    skill_recommendations: dict[str, Any],
    coordinator: bool = False,
) -> str:
    if skill_recommendations.get("schema_version") == "valp-skill-recommendation-slice.v1":
        if skill_recommendations.get("status") not in {"complete", "no_matches"}:
            return f"- Provider-reachable skill slice status: `{skill_recommendations.get('status', 'unknown')}`."
        lines = [
            "- Provider-reachable skill recommendations are routing aids, not permission grants.",
            "- This dispatch receives only the compact provider-reachable slice; the full report is coordinator-only.",
        ]
        for item in (skill_recommendations.get("recommendations") or [])[:3]:
            lines.append(
                "- Skill `{}` for `{}` (provider {}, confidence {}, {}).".format(
                    item.get("skill", "unknown"),
                    item.get("task", "work item"),
                    item.get("provider", "unknown"),
                    item.get("confidence", "unknown"),
                    item.get("decision", "unknown"),
                )
            )
        if not skill_recommendations.get("recommendations"):
            lines.append("- No provider-reachable installed skill matched strongly enough for this dispatch.")
        return "\n".join(lines)
    source = skill_recommendations
    per_agent = skill_recommendations.get("per_agent") or {}
    if isinstance(per_agent, dict) and agent in per_agent:
        candidate = per_agent.get(agent) or {}
        if candidate.get("status") in {"complete", "no_matches"}:
            source = candidate
    if source.get("status") not in {"complete", "no_matches"}:
        return f"- Skill router status: `{source.get('status', 'unknown')}`. Proceed without assuming hidden skill recommendations."
    lines = [
        "- Skill recommendations are routing aids, not permission grants.",
    ]
    if coordinator:
        lines.append("- Full recommendation records remain in `skill-recommendations.json`; coordinator-only context.")
    if source is not skill_recommendations:
        lines.append(f"- Recommendations filtered for `{agent}` by provider.")
    count = 0
    for result_index, result in enumerate(source.get("results") or [], start=1):
        task = str(result.get("task") or "").strip()
        label = skill_task_label(task, result_index)
        routing = result.get("routing") or {}
        for match in result.get("matches") or []:
            if not match.get("installed"):
                continue
            if not skill_visible_to_agent(agent, str(match.get("path") or "")):
                continue
            count += 1
            lines.append(
                "- Work item {} `{}` -> `{}` ({}, confidence {}).".format(
                    result_index,
                    label,
                    match.get("skill", "unknown"),
                    routing.get("decision", "unknown"),
                    match.get("confidence", "unknown"),
                )
            )
            if count >= 3:
                break
        if count >= 3:
            break
    if count == 0:
        lines.append("- No installed skill matched strongly enough for this dispatch.")
    missing = source.get("missing_skills") or []
    for missing_skill in missing[:1]:
        lines.append(
            "- Missing useful skill `{}`: {}".format(
                missing_skill.get("skill", "unknown"),
                missing_skill.get("install_hint", "no install hint"),
            )
        )
    return "\n".join(lines)


def skill_visible_to_agent(agent: str, path: str) -> bool:
    if not path or path == "unknown":
        return True
    normalized = path.replace("\\", "/")
    shared = ["/.agents/skills/"]
    agent_paths = {
        "codex": ["/.codex/skills/", *shared],
        "claude": ["/.claude/skills/", *shared],
        "hermes": ["/.hermes/skills/"],
        "agy": ["/.gemini/", "/.antigravity/", *shared],
    }
    allowed = agent_paths.get(agent)
    if not allowed:
        return True
    return any(marker in normalized for marker in allowed)


def append_dispatch_written_receipts(
    directory: Path,
    selected_agents: list[str],
    expected_by_agent: dict[str, list[str]],
    *,
    runtime_adapter_id: str = "",
) -> None:
    if runtime_adapter_id in {"herdr", "queue", "manual", "langgraph"} and runtime_v3_identity_available(directory):
        return
    receipts_path = directory / "dispatch-receipts.jsonl"
    existing = receipts_path.read_text(encoding="utf-8").splitlines() if receipts_path.exists() else []
    existing_keys = set()
    for line in existing:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        existing_keys.add((record.get("agent"), record.get("event")))
    with receipts_path.open("a", encoding="utf-8") as handle:
        for agent in selected_agents:
            key = (agent, "dispatch_written")
            if key in existing_keys:
                continue
            record = {
                "ts": now_iso(),
                "agent": agent,
                "event": "dispatch_written",
                "dispatch_ref": f"agents/{agent}/dispatch.md",
                "expected_refs": expected_by_agent.get(agent, []),
                "summary": "VALP coordinator wrote visible dispatch.",
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_queue_submission(
    directory: Path,
    task_id: str,
    target: str,
    role: str,
    expected: list[str],
) -> dict[str, Any]:
    dependency_document = read_json(directory / "submission-dependencies.json")
    identity = next(
        (
            item
            for item in dependency_document.get("work_items") or []
            if isinstance(item, dict)
            and item.get("agent") == target
            and item.get("role") == role
        ),
        work_item_identity(task_id, target, role),
    )
    if runtime_v3_identity_available(directory):
        from .runtime_adapters import RuntimeAdapterError, record_queue_acceptance

        try:
            queue_record, _, _ = record_queue_acceptance(
                directory,
                task_id,
                agent=target,
                role=role,
                work_item_id=str(identity["work_item_id"]),
                dispatch_id=str(identity["dispatch_id"]),
                dispatch_generation=int(identity["dispatch_generation"]),
                dispatch_ref=f"agents/{target}/dispatch.md",
                expected_refs=expected,
            )
        except RuntimeAdapterError as error:
            raise SystemExit(f"Queue v3 submission failed: {error}") from error
        return queue_record
    queue_id = f"{task_id}-{target}-{role}"
    worker_id = f"worker-{target}-{role}"
    queue_record = {
        "schema_version": "valp-queue-dispatch.v1",
        "task_id": task_id,
        "agent": target,
        "role": role,
        "work_item_id": identity["work_item_id"],
        "dispatch_id": identity["dispatch_id"],
        "dispatch_generation": identity["dispatch_generation"],
        "queue_id": queue_id,
        "worker_id": worker_id,
        "status": "queued",
        "dispatch_ref": f"agents/{target}/dispatch.md",
        "expected_refs": expected,
        "created_at": now_iso(),
        "note": "Synthetic reference queue submission. Completion still requires dispatch_completed plus expected evidence.",
    }
    queue_ref = f"queue/{target}-{role}.json"
    queue_path = directory / queue_ref
    proof = {
        "runtime": "VALP headless queue",
        "queue_id": queue_id,
        "worker_id": worker_id,
        "queue_record": queue_ref,
    }
    with task_state_lock(directory):
        existing_queue_record = read_json(queue_path)
        if queue_path.exists():
            expected_queue_fields = set(queue_record)
            if (
                set(existing_queue_record) != expected_queue_fields
                or any(
                    existing_queue_record.get(key) != value
                    for key, value in queue_record.items()
                    if key != "created_at"
                )
                or not isinstance(existing_queue_record.get("created_at"), str)
                or not str(existing_queue_record["created_at"]).strip()
            ):
                raise SystemExit("Existing queue submission conflicts with the routed work item")
            queue_record = existing_queue_record
        else:
            write_json(queue_path, queue_record)
        existing_receipts = load_dispatch_receipts(directory, task_id)
        logical_receipts = [
            receipt
            for receipt in existing_receipts
            if receipt.get("schema_version") == "valp-dispatch-receipt.v2"
            and receipt.get("task_id") == task_id
            and receipt.get("agent") == target
            and receipt.get("role") == role
            and receipt.get("work_item_id") == identity["work_item_id"]
            and receipt.get("dispatch_id") == identity["dispatch_id"]
            and receipt.get("dispatch_generation") == identity["dispatch_generation"]
            and receipt.get("event") == "dispatch_submitted"
            and has_concrete_runtime_submission_proof(receipt)
        ]
        if logical_receipts:
            conflicting = [
                receipt
                for receipt in logical_receipts
                if receipt.get("dispatch_ref") != f"agents/{target}/dispatch.md"
                or receipt.get("expected_refs") != expected
                or receipt.get("proof") != proof
            ]
            if conflicting or len({str(receipt.get("receipt_id")) for receipt in logical_receipts}) != 1:
                raise SystemExit("Existing queue submission receipt conflicts with the routed work item")
            return queue_record
        event_sequence = max(
            [
                int(record["event_sequence"])
                for record in existing_receipts
                if record.get("schema_version") == "valp-dispatch-receipt.v2"
                and type(record.get("event_sequence")) is int
            ],
            default=0,
        ) + 1
        append_json_line_durable(
            directory / "dispatch-receipts.jsonl",
            {
                "schema_version": "valp-dispatch-receipt.v2",
                "receipt_id": (
                    f"{task_id}:{identity['work_item_id']}:{identity['dispatch_id']}:"
                    f"{identity['dispatch_generation']}:dispatch_submitted"
                ),
                "task_id": task_id,
                "event_sequence": event_sequence,
                "ts": now_iso(),
                "agent": target,
                "role": role,
                "work_item_id": identity["work_item_id"],
                "dispatch_id": identity["dispatch_id"],
                "dispatch_generation": identity["dispatch_generation"],
                "event": "dispatch_submitted",
                "dispatch_ref": f"agents/{target}/dispatch.md",
                "expected_refs": expected,
                "proof": proof,
                "summary": (
                    "Headless queue adapter accepted the dispatch. "
                    "Completion still requires expected evidence."
                ),
            },
        )
    return queue_record


def write_herdr_transport_receipt(
    directory: Path,
    task_id: str,
    target: str,
    role: str,
    expected: list[str],
    proof: dict[str, Any],
) -> dict[str, Any]:
    if (
        proof.get("runtime") != "HERDR"
        or proof.get("proof_class") != "transport_only"
        or proof.get("transport_mode") != "pane_send_text_enter"
    ):
        raise SystemExit("HERDR transport receipt requires explicit transport-only proof")
    dependency_document = read_json(directory / "submission-dependencies.json")
    identity = next(
        (
            item
            for item in dependency_document.get("work_items") or []
            if isinstance(item, dict)
            and item.get("agent") == target
            and item.get("role") == role
        ),
        work_item_identity(task_id, target, role),
    )
    if runtime_v3_identity_available(directory):
        from .runtime_adapters import RuntimeAdapterError, record_herdr_transport

        try:
            receipt, _ = record_herdr_transport(
                directory,
                task_id,
                agent=target,
                role=role,
                work_item_id=str(identity["work_item_id"]),
                dispatch_id=str(identity["dispatch_id"]),
                dispatch_generation=int(identity["dispatch_generation"]),
                dispatch_ref=f"agents/{target}/dispatch.md",
                expected_refs=expected,
                proof=proof,
            )
        except RuntimeAdapterError as error:
            raise SystemExit(f"HERDR transport v3 receipt failed: {error}") from error
        return receipt
    with task_state_lock(directory):
        existing_receipts = load_dispatch_receipts(directory, task_id)
        logical_receipts = [
            receipt
            for receipt in existing_receipts
            if receipt.get("schema_version") == "valp-dispatch-receipt.v2"
            and receipt.get("task_id") == task_id
            and receipt.get("agent") == target
            and receipt.get("role") == role
            and receipt.get("work_item_id") == identity["work_item_id"]
            and receipt.get("dispatch_id") == identity["dispatch_id"]
            and receipt.get("dispatch_generation") == identity["dispatch_generation"]
            and receipt.get("event") == "dispatch_inserted"
        ]
        if logical_receipts:
            if len(logical_receipts) != 1 or any(
                receipt.get("dispatch_ref") != f"agents/{target}/dispatch.md"
                or receipt.get("expected_refs") != expected
                or receipt.get("proof") != proof
                for receipt in logical_receipts
            ):
                raise SystemExit("Existing HERDR transport receipt conflicts with the routed work item")
            return logical_receipts[0]
        event_sequence = max(
            [
                int(record["event_sequence"])
                for record in existing_receipts
                if record.get("schema_version") == "valp-dispatch-receipt.v2"
                and type(record.get("event_sequence")) is int
            ],
            default=0,
        ) + 1
        receipt = {
            "schema_version": "valp-dispatch-receipt.v2",
            "receipt_id": (
                f"{task_id}:{identity['work_item_id']}:{identity['dispatch_id']}:"
                f"{identity['dispatch_generation']}:dispatch_inserted"
            ),
            "task_id": task_id,
            "event_sequence": event_sequence,
            "ts": now_iso(),
            "agent": target,
            "role": role,
            "work_item_id": identity["work_item_id"],
            "dispatch_id": identity["dispatch_id"],
            "dispatch_generation": identity["dispatch_generation"],
            "event": "dispatch_inserted",
            "dispatch_ref": f"agents/{target}/dispatch.md",
            "expected_refs": expected,
            "proof": proof,
            "summary": (
                "The packaged HERDR adapter inserted the dispatch and sent Enter. "
                "This is transport proof only; operation is Manual-degraded."
            ),
        }
        append_json_line_durable(directory / "dispatch-receipts.jsonl", receipt)
        return receipt


def consume_verified_bootstrap_lifecycle(
    directory: Path,
    task_id: str,
    target: str,
    proof: dict[str, Any],
    submission_receipt_id: str,
) -> None:
    proof_binding = proof.get("session_binding") or {}
    if proof_binding.get("ref") != "agent-sessions.json":
        return
    projection_path = directory / "agent-sessions.json"
    receipts_path = directory / "agent-session-receipts.jsonl"
    if not projection_path.is_file():
        return
    with task_state_lock(directory):
        projection = read_json_strict(projection_path)
        binding = ((projection.get("bindings") or {}).get(target) or {})
        identity = binding.get("runtime_identity") or {}
        if not binding_has_verified_bootstrap_lifecycle(binding):
            return
        if any(
            (
                projection.get("schema_version") != "valp-agent-sessions.v1",
                projection.get("task_id") != task_id,
                projection.get("adapter") != "herdr",
                proof_binding.get("generation") != binding.get("generation"),
                proof_binding.get("identity_token") != identity.get("token"),
                proof_binding.get("ownership") != binding.get("ownership"),
            )
        ):
            raise SystemExit(
                "HERDR submission proof cannot consume a different bootstrap lifecycle"
            )
        binding["lifecycle"] = "reused"
        binding["bootstrap_verification"][
            "consumed_by_dispatch_receipt_id"
        ] = submission_receipt_id
        projection["updated_at"] = now_iso()
        write_json(projection_path, projection)
        receipts = read_json_lines_strict(receipts_path)
        sequence = max(
            (
                int(record.get("event_sequence"))
                for record in receipts
                if type(record.get("event_sequence")) is int
            ),
            default=0,
        )
        append_json_line_durable(
            receipts_path,
            {
                "schema_version": "valp-agent-session-receipt.v1",
                "adapter": "herdr",
                "task_id": task_id,
                "event_sequence": sequence + 1,
                "ts": now_iso(),
                "agent": target,
                "event": "agent_session_reused",
                "binding_ref": "agent-sessions.json",
                "generation": binding["generation"],
                "identity_token": identity["token"],
                "ownership": binding["ownership"],
                "context": binding["context"],
                "launch": binding["launch"],
                "focused_at_provisioning": binding["focused_at_provisioning"],
                "runtime_scope": binding["runtime_scope"],
                "runtime_identity": identity,
                "submission_receipt_id": submission_receipt_id,
            },
        )


def write_herdr_submission_receipt(
    directory: Path,
    task_id: str,
    target: str,
    role: str,
    expected: list[str],
    proof: dict[str, Any],
    recovery: dict[str, Any] | None = None,
    expected_evidence_baseline: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    if proof.get("runtime") != "HERDR" or proof.get("proof_class") != "agent_invocation":
        raise SystemExit(
            "HERDR dispatch_submitted requires independent Agent invocation proof"
        )
    if expected_evidence_baseline is not None:
        if set(expected_evidence_baseline) != set(expected):
            raise SystemExit("HERDR expected evidence baseline does not match the dispatch")
        proof = {**proof, "expected_evidence_baseline": expected_evidence_baseline}
    dependency_document = read_json(directory / "submission-dependencies.json")
    identity = next(
        (
            item
            for item in dependency_document.get("work_items") or []
            if isinstance(item, dict)
            and item.get("agent") == target
            and item.get("role") == role
        ),
        work_item_identity(task_id, target, role),
    )
    retry_generation = recovery.get("retry_generation") if recovery else None
    if recovery:
        if "recovery" in proof:
            raise SystemExit("HERDR submission proof conflicts with incomplete recovery metadata")
        proof = {
            **proof,
            "recovery": {
                "kind": "incomplete_submission",
                "retry_generation": retry_generation,
                "originating_submission_receipt_id": recovery[
                    "originating_submission_receipt_id"
                ],
                "control_contract_digest": recovery["control_contract_digest"],
            },
        }
    if runtime_v3_identity_available(directory):
        if recovery is not None:
            raise SystemExit("HERDR v3 recovery requires exact Attempt resume, not v2 retry generation")
        from .runtime_adapters import RuntimeAdapterError, record_herdr_submission

        try:
            receipt, _ = record_herdr_submission(
                directory,
                task_id,
                agent=target,
                role=role,
                work_item_id=str(identity["work_item_id"]),
                dispatch_id=str(identity["dispatch_id"]),
                dispatch_generation=int(identity["dispatch_generation"]),
                dispatch_ref=f"agents/{target}/dispatch.md",
                expected_refs=expected,
                proof=proof,
            )
        except RuntimeAdapterError as error:
            raise SystemExit(f"HERDR v3 submission receipt failed: {error}") from error
        consume_verified_bootstrap_lifecycle(
            directory,
            task_id,
            target,
            proof,
            str(receipt["receipt_id"]),
        )
        return receipt
    receipt: dict[str, Any]
    with task_state_lock(directory):
        existing_receipts = load_dispatch_receipts(directory, task_id)
        logical_receipts = [
            receipt
            for receipt in existing_receipts
            if receipt.get("schema_version") == "valp-dispatch-receipt.v2"
            and receipt.get("task_id") == task_id
            and receipt.get("agent") == target
            and receipt.get("role") == role
            and receipt.get("work_item_id") == identity["work_item_id"]
            and receipt.get("dispatch_id") == identity["dispatch_id"]
            and receipt.get("dispatch_generation") == identity["dispatch_generation"]
            and receipt.get("retry_generation") == retry_generation
            and receipt.get("event") == "dispatch_submitted"
        ]
        if logical_receipts:
            matching = [
                receipt
                for receipt in logical_receipts
                if has_concrete_runtime_submission_proof(receipt)
                and (receipt.get("proof") or {}).get("runtime") == "HERDR"
            ]
            if len(logical_receipts) != 1 or len(matching) != 1 or any(
                receipt.get("dispatch_ref") != f"agents/{target}/dispatch.md"
                or receipt.get("expected_refs") != expected
                or receipt.get("proof") != proof
                for receipt in matching
            ) or len({str(receipt.get("receipt_id")) for receipt in matching}) != 1:
                raise SystemExit("Existing HERDR submission receipt conflicts with the routed work item")
            receipt = matching[0]
        else:
            event_sequence = max(
                [
                    int(record["event_sequence"])
                    for record in existing_receipts
                    if record.get("schema_version") == "valp-dispatch-receipt.v2"
                    and type(record.get("event_sequence")) is int
                ],
                default=0,
            ) + 1
            receipt_id = (
                f"{task_id}:{identity['work_item_id']}:{identity['dispatch_id']}:"
                f"{identity['dispatch_generation']}:dispatch_submitted"
            )
            if retry_generation is not None:
                receipt_id = (
                    f"{task_id}:{identity['work_item_id']}:{identity['dispatch_id']}:"
                    f"{identity['dispatch_generation']}:retry:{retry_generation}:dispatch_submitted"
                )
            receipt = {
                "schema_version": "valp-dispatch-receipt.v2",
                "receipt_id": receipt_id,
                "task_id": task_id,
                "event_sequence": event_sequence,
                "ts": now_iso(),
                "agent": target,
                "role": role,
                "work_item_id": identity["work_item_id"],
                "dispatch_id": identity["dispatch_id"],
                "dispatch_generation": identity["dispatch_generation"],
                "event": "dispatch_submitted",
                "dispatch_ref": f"agents/{target}/dispatch.md",
                "expected_refs": expected,
                "proof": proof,
                "summary": (
                    "The packaged HERDR adapter submitted the dispatch with runtime proof. "
                    "Completion still requires expected evidence."
                ),
            }
            if retry_generation is not None:
                receipt["retry_generation"] = retry_generation
            append_json_line_durable(directory / "dispatch-receipts.jsonl", receipt)
    consume_verified_bootstrap_lifecycle(
        directory,
        task_id,
        target,
        proof,
        str(receipt["receipt_id"]),
    )
    return receipt


def write_herdr_invalid_session_binding_supersession(
    directory: Path,
    task_id: str,
    superseded_submission_receipt_id: str,
    replacement_submission_receipt_id: str,
) -> dict[str, Any]:
    """Append a narrow provenance repair without changing either submission."""
    with task_state_lock(directory):
        receipts = load_dispatch_receipts(directory, task_id)
        submitted = {
            str(receipt.get("receipt_id") or ""): receipt
            for receipt in receipts
            if receipt.get("event") == "dispatch_submitted"
        }
        original = submitted.get(superseded_submission_receipt_id)
        replacement = submitted.get(replacement_submission_receipt_id)
        if not original or not replacement:
            raise SystemExit("HERDR supersession requires exact existing submission receipts")
        identity_fields = (
            "task_id", "agent", "role", "work_item_id", "dispatch_id",
            "dispatch_generation", "dispatch_ref", "expected_refs",
        )
        if (
            original.get("event_sequence", 0) >= replacement.get("event_sequence", 0)
            or any(original.get(field) != replacement.get(field) for field in identity_fields)
            or not has_concrete_runtime_submission_proof(original)
            or not has_concrete_runtime_submission_proof(replacement)
            or (original.get("proof") or {}).get("runtime") != "HERDR"
            or (replacement.get("proof") or {}).get("runtime") != "HERDR"
            or (original.get("proof") or {}).get("proof_class")
            not in {"agent_invocation", "reconciled_identity_bound_submission"}
            or (replacement.get("proof") or {}).get("proof_class") != "agent_invocation"
        ):
            raise SystemExit(
                "HERDR supersession requires concrete submission proof and exact work-item identity"
            )
        projection = read_json_strict(directory / "agent-sessions.json")
        session_receipts = read_json_lines_strict(
            directory / "agent-session-receipts.jsonl"
        )
        adapter = projection.get("adapter")

        def provisioned(receipt: dict[str, Any]) -> bool:
            proof_binding = ((receipt.get("proof") or {}).get("session_binding") or {})
            return bool(
                proof_binding.get("ref") == "agent-sessions.json"
                and any(
                    item.get("adapter") == adapter
                    and item.get("task_id") == task_id
                    and item.get("agent") == receipt.get("agent")
                    and item.get("event") == "agent_session_provisioned"
                    and item.get("binding_ref") == proof_binding.get("ref")
                    and item.get("generation") == proof_binding.get("generation")
                    and item.get("identity_token") == proof_binding.get("identity_token")
                    and item.get("ownership") == proof_binding.get("ownership")
                    for item in session_receipts
                )
            )

        if provisioned(original) or not provisioned(replacement):
            raise SystemExit(
                "HERDR supersession requires an invalid original and valid replacement binding"
            )
        proof = {
            "kind": "invalid_session_binding",
            "superseded_submission_receipt_id": superseded_submission_receipt_id,
            "replacement_submission_receipt_id": replacement_submission_receipt_id,
        }
        receipt_id = "sha256:" + hashlib.sha256(
            json.dumps({"task_id": task_id, **proof}, sort_keys=True).encode("utf-8")
        ).hexdigest()
        existing = [receipt for receipt in receipts if receipt.get("receipt_id") == receipt_id]
        if existing:
            return existing[0]
        event_sequence = max(
            (int(receipt.get("event_sequence") or 0) for receipt in receipts),
            default=0,
        ) + 1
        supersession = {
            "schema_version": "valp-dispatch-receipt.v2",
            "receipt_id": receipt_id,
            "task_id": task_id,
            "event_sequence": event_sequence,
            "ts": now_iso(),
            "agent": original["agent"],
            "role": original["role"],
            "work_item_id": original["work_item_id"],
            "dispatch_id": original["dispatch_id"],
            "dispatch_generation": original["dispatch_generation"],
            "event": "dispatch_superseded",
            "dispatch_ref": original["dispatch_ref"],
            "expected_refs": original["expected_refs"],
            "proof": proof,
            "summary": (
                "Append-only repair: invalid task-owned session binding "
                "superseded by a later valid submission."
            ),
        }
        append_json_line_durable(directory / "dispatch-receipts.jsonl", supersession)
        return supersession


def expected_evidence_snapshot(directory: Path, expected: list[str]) -> dict[str, str | None]:
    """Capture the exact pre-submission evidence state for a dispatch."""
    snapshot: dict[str, str | None] = {}
    for ref in expected:
        if not safe_task_evidence_ref(ref):
            snapshot[ref] = None
            continue
        path = (directory / ref).resolve()
        try:
            path.relative_to(directory.resolve())
        except ValueError:
            snapshot[ref] = None
            continue
        snapshot[ref] = (
            "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else None
        )
    return snapshot


def herdr_expected_ref_status(
    directory: Path,
    expected: list[str],
    baseline: dict[str, str | None] | None = None,
) -> tuple[bool, list[str], list[str]]:
    existing: list[str] = []
    missing: list[str] = []
    for ref in expected:
        if not safe_task_evidence_ref(ref):
            missing.append(ref)
            continue
        path = (directory / ref).resolve()
        try:
            path.relative_to(directory.resolve())
        except ValueError:
            missing.append(ref)
            continue
        current_digest = (
            "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            if path.is_file()
            else None
        )
        if (
            path.is_file()
            and path.stat().st_size > 0
            and work_item_evidence_is_valid(directory, {"expected_refs": [ref]})
            and (baseline is None or current_digest != baseline.get(ref))
        ):
            existing.append(ref)
        else:
            missing.append(ref)
    return not missing, existing, missing


def wait_for_herdr_expected_refs(
    directory: Path,
    expected: list[str],
    wait_seconds: float | None,
    baseline: dict[str, str | None] | None = None,
) -> tuple[bool, list[str], list[str]]:
    timeout_seconds = 60.0 if wait_seconds is None else wait_seconds
    deadline = time.monotonic() + max(timeout_seconds, 0.0)
    while True:
        completed, existing, missing = herdr_expected_ref_status(directory, expected, baseline)
        if completed:
            return completed, existing, missing
        if time.monotonic() >= deadline:
            return False, existing, missing
        time.sleep(0.5)


def write_herdr_completion_receipt(
    directory: Path,
    task_id: str,
    submission: dict[str, Any],
    existing_refs: list[str],
    terminal_proof: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (directory / "runtime" / "herdr" / "adoption.json").is_file():
        from .runtime_adapters import RuntimeAdapterError, record_herdr_completion

        try:
            if terminal_proof is None:
                raise RuntimeAdapterError(
                    "adopted HERDR completion requires an identity-bound terminal observation"
                )
            receipt, _ = record_herdr_completion(
                directory, task_id, submission, existing_refs, terminal_proof
            )
        except RuntimeAdapterError as error:
            raise SystemExit(f"HERDR v3 completion receipt failed: {error}") from error
        return receipt
    suspension_epoch = _herdr_suspension_epoch(directory)
    identity = {
        "task_id": task_id,
        "suspension_epoch": suspension_epoch,
        "work_item_id": submission.get("work_item_id"),
        "submission_receipt_id": submission.get("receipt_id"),
        "expected_refs": existing_refs,
    }
    receipt_id = "sha256:" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    with task_state_lock(directory):
        receipts = load_dispatch_receipts(directory, task_id)
        existing = [receipt for receipt in receipts if receipt.get("receipt_id") == receipt_id]
        if existing:
            return existing[0]
        event_sequence = max(
            [
                int(receipt["event_sequence"])
                for receipt in receipts
                if receipt.get("schema_version") == "valp-dispatch-receipt.v2"
                and type(receipt.get("event_sequence")) is int
            ],
            default=0,
        ) + 1
        completion = {
            "schema_version": "valp-dispatch-receipt.v2",
            "receipt_id": receipt_id,
            "task_id": task_id,
            "event_sequence": event_sequence,
            "ts": now_iso(),
            "agent": str(submission.get("agent") or ""),
            "role": str(submission.get("role") or "other"),
            "work_item_id": str(submission.get("work_item_id") or ""),
            "dispatch_id": str(submission.get("dispatch_id") or ""),
            "dispatch_generation": int(submission.get("dispatch_generation") or 1),
            "suspension_epoch": suspension_epoch,
            "event": "dispatch_completed",
            "exit_code": 0,
            "summary": "Expected dispatch evidence exists",
            "dispatch_ref": str(submission.get("dispatch_ref") or ""),
            "expected_refs": existing_refs,
            "proof": {
                "observer": "valp.packaged-herdr.expected-evidence",
                "submission_receipt_id": str(submission.get("receipt_id") or ""),
                "existing_refs": existing_refs,
            },
            "runtime": {
                "name": "HERDR",
                "transport_mode": str((submission.get("proof") or {}).get("transport_mode") or "unknown"),
                "pane_id": str((submission.get("proof") or {}).get("pane_id") or ""),
            },
        }
        if submission.get("retry_generation") is not None:
            completion["retry_generation"] = submission["retry_generation"]
        append_json_line_durable(directory / "dispatch-receipts.jsonl", completion)
        return completion


def wait_event_id(
    task_id: str,
    event: str,
    suspension_id: str,
    event_sequence: int,
    resulting_revision: int,
) -> str:
    value = f"{task_id}:{event}:{suspension_id}:{event_sequence}:{resulting_revision}"
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def deterministic_wake_id(
    task_id: str,
    suspension_epoch: int,
    resume_event: str,
    resume_ref: str | None,
    external_event: dict[str, Any] | None = None,
) -> str:
    wake_source = (
        str(external_event.get("source_digest") or "")
        if external_event is not None
        else resume_ref or ""
    )
    wake_key = f"{task_id}:{suspension_epoch}:{resume_event}:{wake_source}"
    return "sha256:" + hashlib.sha256(wake_key.encode("utf-8")).hexdigest()


def wake_reason_pair_error(resume_event: str, wake_reason: str) -> str | None:
    if wake_reason not in WAKE_REASONS_BY_RESUME_EVENT.get(resume_event, set()):
        return f"Illegal resume_event/wake_reason combination: {resume_event}/{wake_reason}"
    return None


def wake_status_pair_error(wake_reason: str, task_status: str) -> str | None:
    expected_status = TASK_STATUS_BY_WAKE_REASON.get(wake_reason)
    if expected_status is None or task_status != expected_status:
        return f"Illegal wake_reason/resulting task status combination: {wake_reason}/{task_status}"
    return None


WORK_ITEM_STATE_FIELDS = (
    "completed_work_item_ids",
    "pending_work_item_ids",
    "failed_work_item_ids",
)
DETERMINISTIC_SUSPENSION_REQUIRED_FIELDS = {
    "status",
    "suspension_id",
    "suspension_epoch",
    "state_revision_at_entry",
    "wait_policy_ref",
    "wait_policy_id",
    "strict_identity",
    "event_sequence_at_entry",
    "receipt_event_sequence_at_entry",
    "receipt_cursor_at_entry",
    "required_work_items",
    "required_work_item_ids",
    "pending_work_item_ids",
    "completed_work_item_ids",
    "failed_work_item_ids",
    "entered_at",
    "deadline_at",
    "execution_deadline",
    "waiting_for_agents",
    "receipt_count_at_entry",
    "allowed_resume_events",
}
DETERMINISTIC_SUSPENSION_OPTIONAL_FIELDS = {
    "checkpoint_ref",
    "evidence_refs_present_at_entry",
    "receipt_cursor",
    "resume_event",
    "resumed_at",
    "resume_ref",
    "accepted_wake",
}


def deterministic_suspension_shape_error(suspension: dict[str, Any]) -> str | None:
    fields = set(suspension)
    missing = DETERMINISTIC_SUSPENSION_REQUIRED_FIELDS - fields
    unknown = fields - (
        DETERMINISTIC_SUSPENSION_REQUIRED_FIELDS | DETERMINISTIC_SUSPENSION_OPTIONAL_FIELDS
    )
    if missing:
        return "Deterministic suspension is missing closed fields: " + ", ".join(sorted(missing))
    if unknown:
        return "Deterministic suspension has unknown control fields: " + ", ".join(sorted(unknown))
    return None


def suspension_checkpoint_error(
    directory: Path,
    suspension: dict[str, Any],
) -> str | None:
    checkpoint_ref = suspension.get("checkpoint_ref")
    if checkpoint_ref is None:
        return None
    if not isinstance(checkpoint_ref, str) or not safe_task_evidence_ref(checkpoint_ref):
        return "Suspension checkpoint_ref must be a safe task-local ref"
    checkpoint_path = (directory / checkpoint_ref).resolve()
    try:
        checkpoint_path.relative_to(directory.resolve())
    except ValueError:
        return "Suspension checkpoint_ref escapes the task directory"
    if not checkpoint_path.is_file() or checkpoint_path.stat().st_size == 0:
        return "Suspension checkpoint_ref does not name a durable non-empty checkpoint"
    return None


def wait_work_item_policy_error(
    suspension: dict[str, Any],
    policy: dict[str, Any],
    policy_ref: str = "wait-policy.json",
) -> str | None:
    if not suspension.get("strict_identity"):
        return None
    expected_items = policy.get("required_work_items")
    if not isinstance(expected_items, list) or not expected_items:
        return "Strict suspension has no valid wait policy work-item table"
    expected_ids = [
        item.get("work_item_id")
        for item in expected_items
        if isinstance(item, dict)
    ]
    if (
        suspension.get("wait_policy_ref") != policy_ref
        or suspension.get("wait_policy_id") != policy.get("wait_policy_id")
        or suspension.get("required_work_items") != expected_items
        or suspension.get("required_work_item_ids") != expected_ids
    ):
        return "Committed suspension work-item barrier does not match wait policy"
    return None


def wait_work_item_transition_error(
    previous_suspension: dict[str, Any] | None,
    event: dict[str, Any],
) -> str | None:
    projection = event.get("projection")
    if not isinstance(projection, dict) or not isinstance(projection.get("suspension"), dict):
        return "Wait event projection is missing"
    current = projection["suspension"]
    shape_error = deterministic_suspension_shape_error(current)
    if shape_error:
        return shape_error
    required = current.get("required_work_item_ids")
    work_items = current.get("required_work_items")
    if (
        not isinstance(required, list)
        or not required
        or any(not isinstance(value, str) or not value for value in required)
        or len(required) != len(set(required))
        or not isinstance(work_items, list)
        or any(not isinstance(item, dict) for item in work_items)
        or [item.get("work_item_id") for item in work_items] != required
    ):
        return "Wait event work-item identity set is invalid"
    values: dict[str, list[str]] = {}
    for field in WORK_ITEM_STATE_FIELDS:
        raw = current.get(field)
        if (
            not isinstance(raw, list)
            or any(not isinstance(value, str) or not value for value in raw)
            or len(raw) != len(set(raw))
            or any(value not in required for value in raw)
        ):
            return f"Wait event {field} is invalid"
        values[field] = raw
    completed = values["completed_work_item_ids"]
    pending = values["pending_work_item_ids"]
    failed = values["failed_work_item_ids"]
    if set(completed).intersection(pending) or set(completed).union(pending) != set(required):
        return "Wait event completed and pending work-item sets do not partition required work"
    if set(completed).intersection(failed):
        return "Wait event cannot mark a completed work item as failed"

    event_name = str(event.get("event") or "")
    if event_name == "coordinator_suspended":
        if completed or failed or pending != required:
            return "New suspension work-item sets do not match the required barrier"
        return None
    if not isinstance(previous_suspension, dict):
        return "Wait event work-item transition has no preceding projection"
    if (
        current.get("suspension_id") != previous_suspension.get("suspension_id")
        or current.get("suspension_epoch") != previous_suspension.get("suspension_epoch")
        or current.get("required_work_items") != previous_suspension.get("required_work_items")
        or required != previous_suspension.get("required_work_item_ids")
    ):
        return "Wait event changed suspension work-item identity"
    previous_values = {
        field: previous_suspension.get(field)
        for field in WORK_ITEM_STATE_FIELDS
    }
    if any(not isinstance(value, list) for value in previous_values.values()):
        return "Preceding wait projection has invalid work-item sets"

    previous_completed = previous_values["completed_work_item_ids"]
    previous_pending = previous_values["pending_work_item_ids"]
    previous_failed = previous_values["failed_work_item_ids"]
    if event_name == "work_item_completed":
        work_item_id = event.get("work_item_id")
        if not isinstance(work_item_id, str) or work_item_id not in previous_pending:
            return "Completed work-item event does not identify pending work"
        if (
            completed != [*previous_completed, work_item_id]
            or pending != [value for value in previous_pending if value != work_item_id]
            or failed != previous_failed
        ):
            return "Completed work-item event has an invalid set transition"
        return None
    if event_name != "coordinator_resumed":
        return "Wait event has an unsupported work-item transition"

    wake_reason = str(event.get("wake_reason") or "")
    if wake_reason == "dependency_ready":
        if (
            len(previous_pending) != 1
            or completed != [*previous_completed, previous_pending[0]]
            or pending
            or failed != previous_failed
        ):
            return "dependency_ready wake has an invalid work-item set transition"
        return None
    if wake_reason in {"dispatch_blocked", "manual_blocked"}:
        added_failed = failed[len(previous_failed):]
        if (
            completed != previous_completed
            or pending != previous_pending
            or failed[:len(previous_failed)] != previous_failed
            or len(added_failed) != 1
            or added_failed[0] not in previous_pending
        ):
            return "Blocked work-item wake has an invalid set transition"
        return None
    if any(values[field] != previous_values[field] for field in WORK_ITEM_STATE_FIELDS):
        return "Exception wake changed committed work-item sets"
    return None


def wait_receipt_event_error(
    event: dict[str, Any],
    previous_suspension: dict[str, Any] | None,
    receipts: list[dict[str, Any]],
    task_id: str,
    directory: Path | None = None,
) -> str | None:
    event_name = str(event.get("event") or "")
    wake_reason = str(event.get("wake_reason") or "")
    if event_name == "work_item_completed":
        receipt_ref = event.get("receipt_ref")
        expected_events = {"dispatch_completed", "manual_result_attested"}
        expected_work_item_id = event.get("work_item_id")
    elif event_name == "coordinator_resumed" and event.get("resume_event") == "receipt":
        receipt_ref = event.get("resume_ref")
        if wake_reason == "dependency_ready":
            expected_events = {"dispatch_completed", "manual_result_attested"}
        elif wake_reason in {"dispatch_blocked", "manual_blocked"}:
            expected_events = {wake_reason}
        else:
            return "Receipt-driven wake has an unsupported wake reason"
        expected_work_item_id = None
    else:
        return None
    if not isinstance(receipt_ref, str) or not receipt_ref.startswith("dispatch-receipts.jsonl#"):
        return "Wait event has an invalid receipt ref"
    try:
        receipt_index = int(receipt_ref.rsplit("#", 1)[1])
    except (ValueError, IndexError):
        return "Wait event has an invalid receipt ref"
    if receipt_index < 1 or receipt_index > len(receipts):
        return "Wait event receipt ref does not exist"
    receipt = receipts[receipt_index - 1]
    if directory is not None and not runtime_receipt_is_effective(directory, task_id, receipt):
        return "Wait event receipt was revoked or not selected by Manual adjudication"
    qualifying_receipt_id = str(receipt.get("receipt_id") or receipt_ref)
    if event.get("qualifying_receipt_id") != qualifying_receipt_id:
        return "Wait event qualifying receipt ID does not match its ledger ref"
    if receipt.get("event") not in expected_events:
        return "Wait event receipt no longer supports its terminal event"
    projection = event.get("projection") or {}
    suspension = projection.get("suspension") or {}
    required_items = suspension.get("required_work_items") or []
    strict_identity = bool(suspension.get("strict_identity"))
    if strict_identity:
        matching_items = [
            item
            for item in required_items
            if isinstance(item, dict) and item.get("work_item_id") == receipt.get("work_item_id")
        ]
    else:
        matching_items = [
            item
            for item in required_items
            if isinstance(item, dict) and item.get("agent") == receipt.get("agent")
        ]
    if len(matching_items) != 1:
        return "Wait event receipt does not identify one required work item"
    item = matching_items[0]
    if expected_work_item_id is not None and item.get("work_item_id") != expected_work_item_id:
        return "Completed wait event work item does not match its receipt"
    if not receipt_matches_work_item(
        receipt,
        item,
        task_id,
        strict_identity,
        suspension_epoch=int(suspension.get("suspension_epoch") or 0),
        event_sequence_at_entry=int(suspension.get("receipt_event_sequence_at_entry") or 0),
    ):
        return "Wait event receipt identity does not match its suspension"
    work_item_id = str(item.get("work_item_id") or "")
    if event_name == "coordinator_resumed" and isinstance(previous_suspension, dict):
        if wake_reason == "dependency_ready" and work_item_id not in (
            previous_suspension.get("pending_work_item_ids") or []
        ):
            return "dependency_ready receipt does not complete the preceding pending work item"
        if wake_reason in {"dispatch_blocked", "manual_blocked"}:
            previous_failed = previous_suspension.get("failed_work_item_ids") or []
            current_failed = suspension.get("failed_work_item_ids") or []
            if current_failed[len(previous_failed):] != [work_item_id]:
                return "Blocked wake receipt does not match the newly failed work item"
    return None


def validated_wait_events(directory: Path, task_id: str) -> list[dict[str, Any]]:
    events = read_json_lines_strict(directory / "wait-events.jsonl")
    previous_sequence = 0
    previous_revision: int | None = None
    previous_suspension: dict[str, Any] | None = None
    policies: dict[str, dict[str, Any]] = {}
    receipts: list[dict[str, Any]] | None = None
    for event in events:
        sequence = event.get("event_sequence")
        before = event.get("state_revision_before")
        after = event.get("state_revision_after")
        if event.get("schema_version") != "valp-wait-event.v1" or event.get("task_id") != task_id:
            raise SystemExit("Invalid wait event identity")
        if type(sequence) is not int or sequence != previous_sequence + 1:
            raise SystemExit("Wait event sequence is not contiguous")
        if type(before) is not int or type(after) is not int or after != before + 1:
            raise SystemExit("Wait event revision transition is invalid")
        expected_event_id = wait_event_id(
            task_id,
            str(event.get("event") or ""),
            str(event.get("suspension_id") or ""),
            sequence,
            after,
        )
        if event.get("event_id") != expected_event_id:
            raise SystemExit("Wait event_id does not match its deterministic derivation")
        if previous_revision is not None and before != previous_revision:
            raise SystemExit("Wait event revision history is not contiguous")
        projection = event.get("projection")
        if not isinstance(projection, dict) or not isinstance(projection.get("suspension"), dict):
            raise SystemExit("Wait event projection is missing")
        checkpoint_error = suspension_checkpoint_error(directory, projection["suspension"])
        if checkpoint_error:
            raise SystemExit(checkpoint_error)
        if event.get("event") == "coordinator_resumed":
            pair_error = wake_reason_pair_error(
                str(event.get("resume_event") or ""),
                str(event.get("wake_reason") or ""),
            )
            if pair_error:
                raise SystemExit(pair_error)
            status_error = wake_status_pair_error(
                str(event.get("wake_reason") or ""),
                str(projection.get("status") or ""),
            )
            if status_error:
                raise SystemExit(status_error)
        if projection["suspension"].get("strict_identity"):
            policy_ref = str(projection["suspension"].get("wait_policy_ref") or "")
            if policy_ref not in policies:
                policies[policy_ref] = load_wait_policy(
                    directory,
                    task_id,
                    policy_ref=policy_ref,
                    validate_dependency_ref=True,
                )
            policy_error = wait_work_item_policy_error(
                projection["suspension"],
                policies[policy_ref],
                policy_ref,
            )
            if policy_error:
                raise SystemExit(policy_error)
        transition_error = wait_work_item_transition_error(previous_suspension, event)
        if transition_error:
            raise SystemExit(transition_error)
        if event.get("event") == "work_item_completed" or (
            event.get("event") == "coordinator_resumed" and event.get("resume_event") == "receipt"
        ):
            if receipts is None:
                receipts = load_dispatch_receipts(directory, task_id)
            receipt_error = wait_receipt_event_error(
                event,
                previous_suspension,
                receipts,
                task_id,
                directory,
            )
            if receipt_error:
                raise SystemExit(receipt_error)
        if event.get("event") == "coordinator_resumed":
            projected_suspension = projection["suspension"]
            accepted_wake = projected_suspension.get("accepted_wake") or {}
            resume_event = str(event.get("resume_event") or "")
            if resume_event in EXTERNAL_RESUME_EVENTS:
                external_event = event.get("external_event")
                if not isinstance(external_event, dict) or accepted_wake.get("external_event") != external_event:
                    raise SystemExit("Committed exception wake metadata is inconsistent")
                try:
                    current_external_event = load_exception_wake_evidence(
                        directory,
                        task_id,
                        projected_suspension,
                        resume_event,
                        str(event.get("resume_ref") or ""),
                    )
                except SystemExit as exc:
                    raise SystemExit("Committed exception wake source evidence is invalid: " + str(exc)) from exc
                if current_external_event != external_event:
                    raise SystemExit("Committed exception wake source evidence changed")
            elif event.get("external_event") is not None:
                raise SystemExit("Non-external wake contains exception wake metadata")
            expected_wake_id = deterministic_wake_id(
                task_id,
                int(event.get("suspension_epoch") or 0),
                resume_event,
                event.get("resume_ref"),
                event.get("external_event"),
            )
            expected_result_ref = f"wake-results/{expected_wake_id.removeprefix('sha256:')}.json"
            if (
                event.get("wake_id") != expected_wake_id
                or accepted_wake.get("wake_id") != expected_wake_id
                or accepted_wake.get("wake_event_id") != expected_event_id
                or event.get("result_ref") != expected_result_ref
                or accepted_wake.get("result_ref") != expected_result_ref
            ):
                raise SystemExit("Committed wake identity does not match its deterministic derivation")
            wake_result = read_json_strict(directory / expected_result_ref)
            status_error = wake_status_pair_error(
                str(event.get("wake_reason") or ""),
                str(wake_result.get("resulting_task_status") or ""),
            )
            if status_error:
                raise SystemExit(status_error)
            if any(
                wake_result.get(field) != projected_suspension.get(field)
                for field in WORK_ITEM_STATE_FIELDS
            ):
                raise SystemExit("Wake result work-item sets do not match the accepted suspension")
        previous_sequence = sequence
        previous_revision = after
        previous_suspension = projection["suspension"]
    return events


def exact_state_revision(state: dict[str, Any]) -> int:
    revision = state.get("revision")
    if type(revision) is not int or revision < 0:
        raise SystemExit("Task state revision must be an exact non-negative integer")
    return revision


def recover_wait_projection(directory: Path, state: dict[str, Any]) -> dict[str, Any]:
    task_id = str(state.get("task_id") or "")
    events = validated_wait_events(directory, task_id)
    kernel_binding_path = directory / "runtime" / "kernel" / "workflow-binding.json"
    if kernel_binding_path.is_file():
        try:
            from .kernel_store import KernelStore

            kernel_state = KernelStore(directory / "runtime" / "kernel").recover().replay.state
            kernel_binding = read_json_strict(kernel_binding_path)
        except Exception as error:
            raise SystemExit(f"Kernel wait recovery failed: {error}") from error
        if kernel_state.suspension is None:
            raise SystemExit("Kernel wait binding exists without Kernel suspension truth")
        if kernel_state.suspension.status.value != kernel_binding.get("status"):
            raise SystemExit("Kernel wait binding status conflicts with recovered Kernel State")
        if not events and kernel_binding.get("status") == "waiting":
            workflow_suspension = kernel_binding.get("workflow_suspension")
            if not isinstance(workflow_suspension, dict):
                raise SystemExit("Kernel wait binding lacks workflow suspension projection")
            recovered = dict(state)
            recovered["status"] = "suspended"
            recovered["suspension"] = workflow_suspension
            recovered["updated_at"] = now_iso()
            commit_wait_state(
                directory,
                recovered,
                "coordinator_suspended",
                "Recovered coordinator suspension from durable Kernel truth",
                kernel_recovered=True,
            )
            return recovered
        if kernel_binding.get("status") == "resumed" and (
            not events or events[-1]["projection"]["suspension"].get("status") == "waiting"
        ):
            projection = kernel_binding.get("workflow_wake_projection")
            if not isinstance(projection, dict) or not isinstance(projection.get("suspension"), dict):
                raise SystemExit("Kernel resumed without a recoverable workflow wake projection")
            recovered = dict(state)
            recovered["status"] = projection["status"]
            recovered["suspension"] = projection["suspension"]
            recovered["updated_at"] = projection["updated_at"]
            accepted = projection["suspension"].get("accepted_wake") or {}
            commit_wait_state(
                directory,
                recovered,
                "coordinator_resumed",
                "Recovered coordinator wake from durable Kernel truth",
                resume_event=projection["suspension"].get("resume_event"),
                resume_ref=projection["suspension"].get("resume_ref"),
                wake_reason=accepted.get("wake_reason"),
                wake_id=accepted.get("wake_id"),
                result_ref=projection.get("result_ref"),
                qualifying_receipt_id=None,
                kernel_recovered=True,
            )
            return recovered
    if not events:
        return state
    latest = events[-1]
    projection = latest["projection"]
    projected_suspension = projection["suspension"]
    current_suspension = state.get("suspension") or {}
    state_revision = exact_state_revision(state)
    event_revision = int(latest["state_revision_after"])
    same_suspension = (
        current_suspension.get("suspension_id") == projected_suspension.get("suspension_id")
    )
    waiting_projection_drifted = (
        projected_suspension.get("status") == "waiting"
        and same_suspension
        and (
            state.get("status") != projection.get("status")
            or current_suspension != projected_suspension
        )
    )
    missing_committed_projection = state_revision < event_revision
    if state_revision > event_revision and current_suspension.get("status") == "waiting":
        raise SystemExit("State revision advanced without a committed wake event")
    if missing_committed_projection or waiting_projection_drifted:
        recovered = dict(state)
        recovered["status"] = projection["status"]
        recovered["suspension"] = projected_suspension
        recovered["updated_at"] = projection["updated_at"]
        recovered["revision"] = event_revision
        write_json(directory / "state.json", recovered)
        append_timeline_event(
            directory,
            "wait_projection_recovered",
            "Recovered task wait projection from committed wait event",
            event_id=latest.get("event_id"),
            event_sequence=latest.get("event_sequence"),
        )
        return recovered
    return state


def commit_wait_state(
    directory: Path,
    state: dict[str, Any],
    event: str,
    summary: str,
    **details: Any,
) -> dict[str, Any]:
    task_id = str(state.get("task_id") or "")
    suspension = state.get("suspension") or {}
    events = validated_wait_events(directory, task_id)
    event_sequence = len(events) + 1
    before = exact_state_revision(state)
    if events and int(events[-1]["state_revision_after"]) != before:
        raise SystemExit("State revision does not match committed wait history")
    after = before + 1
    state["revision"] = after
    event_id = wait_event_id(
        task_id,
        event,
        str(suspension.get("suspension_id") or ""),
        event_sequence,
        after,
    )
    record = {
        "schema_version": "valp-wait-event.v1",
        "task_id": task_id,
        "event_id": event_id,
        "event_sequence": event_sequence,
        "event": event,
        "recorded_at": now_iso(),
        "state_revision_before": before,
        "state_revision_after": after,
        "suspension_id": suspension.get("suspension_id"),
        "suspension_epoch": suspension.get("suspension_epoch"),
        "projection": {
            "status": state.get("status"),
            "suspension": suspension,
            "updated_at": state.get("updated_at"),
        },
        **details,
    }
    append_json_line_durable(directory / "wait-events.jsonl", record)
    write_json(directory / "state.json", state)
    append_timeline_event(
        directory,
        event,
        summary,
        event_id=event_id,
        event_sequence=event_sequence,
        state_revision=after,
        **details,
    )
    return record


WAIT_WORK_ITEM_FIELDS = {
    "work_item_id",
    "agent",
    "role",
    "dispatch_id",
    "dispatch_generation",
    "expected_refs",
}
EXCEPTION_WAKE_FIELDS = {
    "schema_version",
    "task_id",
    "suspension_id",
    "suspension_epoch",
    "event",
    "principal",
    "reason",
    "recorded_at",
    "supporting_refs",
}
EXCEPTION_WAKE_PRINCIPAL_FIELDS = {"type", "id"}
EXCEPTION_WAKE_PRINCIPAL_TYPES = {
    "runtime_failure": {"runtime"},
    "cancellation": {"user", "runtime", "policy"},
    "user_input": {"user"},
}
WAIT_EXCEPTION_EVENTS = {
    "dispatch_blocked",
    "manual_blocked",
    "runtime_failure",
    "cancellation",
    "timeout",
    "user_input",
}
REQUIRED_WAIT_EXCEPTION_EVENTS = {
    "dispatch_blocked",
    "runtime_failure",
    "cancellation",
    "timeout",
    "user_input",
}


def load_wait_policy(
    directory: Path,
    task_id: str,
    role_assignments: dict[str, Any] | None = None,
    *,
    policy_ref: str = "wait-policy.json",
    validate_dependency_ref: bool = True,
) -> dict[str, Any]:
    if policy_ref == "wait-policy.json":
        policy_path = directory / policy_ref
    elif re.fullmatch(r"wait-policies/[0-9a-f]{64}\.json", policy_ref):
        policy_path = directory / policy_ref
    else:
        raise SystemExit("Wait policy snapshot ref is invalid")
    if not policy_path.exists():
        return {}
    if policy_ref != "wait-policy.json":
        expected_digest = policy_path.stem
        actual_digest = hashlib.sha256(policy_path.read_bytes()).hexdigest()
        if actual_digest != expected_digest:
            raise SystemExit("Wait policy snapshot digest does not match its ref")
    policy = read_json_strict(policy_path)
    if set(policy) != {
        "schema_version",
        "task_id",
        "wait_policy_id",
        "mode",
        "exception_policy",
        "dependency_ref",
        "required_work_items",
        "exception_events",
    }:
        raise SystemExit("wait-policy.json has unexpected or missing fields")
    if policy.get("schema_version") != "valp-wait-policy.v1" or policy.get("task_id") != task_id:
        raise SystemExit("wait-policy.json identity is invalid")
    if policy.get("mode") != "dependency_ready":
        raise SystemExit("Deterministic wait requires dependency_ready mode")
    if policy.get("exception_policy") != "exception_short_circuit":
        raise SystemExit("Deterministic wait requires exception_short_circuit")
    exception_events = policy.get("exception_events")
    if (
        not isinstance(exception_events, list)
        or len(exception_events) != len(set(str(event) for event in exception_events))
        or any(not isinstance(event, str) or event not in WAIT_EXCEPTION_EVENTS for event in exception_events)
        or not REQUIRED_WAIT_EXCEPTION_EVENTS.issubset(set(exception_events))
    ):
        raise SystemExit("wait-policy.json exception_events are invalid")
    dependency_ref = str(policy.get("dependency_ref") or "")
    if dependency_ref != "submission-dependencies.json":
        raise SystemExit("wait-policy.json must reference submission-dependencies.json")
    dependencies: dict[str, Any] = {}
    if validate_dependency_ref:
        dependencies = read_json_strict(directory / dependency_ref)
        if dependencies.get("task_id") != task_id:
            raise SystemExit("Wait policy dependency task_id does not match")
        if dependencies.get("schema_version") != "valp-submission-dependencies.v2":
            raise SystemExit("Deterministic wait requires submission dependency work item identities")
        if role_assignments is not None:
            dependency_errors = validate_submission_dependencies(
                dependencies,
                task_id,
                {str(role): str(agent) for role, agent in role_assignments.items()},
            )
            if dependency_errors:
                raise SystemExit(
                    "Submission dependency work items do not match routed role assignments: "
                    + "; ".join(dependency_errors)
                )
    work_items = policy.get("required_work_items")
    if not isinstance(work_items, list) or not work_items:
        raise SystemExit("wait-policy.json requires at least one work item")
    ids: list[str] = []
    for item in work_items:
        if not isinstance(item, dict) or set(item) != WAIT_WORK_ITEM_FIELDS:
            raise SystemExit("wait-policy.json contains an invalid work item")
        work_item_id = str(item.get("work_item_id") or "")
        if not work_item_id or not str(item.get("agent") or "") or not str(item.get("role") or ""):
            raise SystemExit("wait-policy.json work item identity is incomplete")
        if not str(item.get("dispatch_id") or ""):
            raise SystemExit("wait-policy.json dispatch_id is missing")
        if type(item.get("dispatch_generation")) is not int or int(item["dispatch_generation"]) < 1:
            raise SystemExit("wait-policy.json dispatch_generation is invalid")
        expected_refs = item.get("expected_refs")
        if not isinstance(expected_refs, list) or not expected_refs:
            raise SystemExit("wait-policy.json expected_refs are missing")
        if any(not safe_task_evidence_ref(str(ref)) for ref in expected_refs):
            raise SystemExit("wait-policy.json contains an unsafe expected ref")
        ids.append(work_item_id)
    if len(ids) != len(set(ids)):
        raise SystemExit("wait-policy.json work_item_id values must be unique")
    if validate_dependency_ref:
        dependency_work_items = dependencies.get("work_items")
        if not isinstance(dependency_work_items, list):
            raise SystemExit("submission dependency work items are missing")
        for item in work_items:
            if item not in dependency_work_items:
                raise SystemExit(
                    "wait-policy.json references an unknown dependency work item: "
                    + str(item.get("work_item_id"))
                )
    return policy


def write_wait_policy_for_phases(
    directory: Path,
    task_id: str,
    phases: list[tuple[str, str]],
    submission_dependencies: dict[str, Any],
) -> dict[str, Any]:
    work_items = [
        item
        for item in submission_dependencies.get("work_items") or []
        if isinstance(item, dict)
    ]
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    for agent, role in phases:
        matches = [
            item
            for item in work_items
            if item.get("agent") == agent and item.get("role") == role
        ]
        if len(matches) != 1:
            raise SystemExit(f"Cannot build wait policy for ambiguous work item {role}:{agent}")
        item = dict(matches[0])
        work_item_id = str(item.get("work_item_id") or "")
        if work_item_id not in selected_ids:
            selected.append(item)
            selected_ids.add(work_item_id)
    if not selected:
        raise SystemExit("Cannot build wait policy without submitted work items")
    identity_digest = hashlib.sha256(
        json.dumps(selected, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    policy = {
        "schema_version": "valp-wait-policy.v1",
        "task_id": task_id,
        "wait_policy_id": f"submitted-phase-{identity_digest}",
        "mode": "dependency_ready",
        "exception_policy": "exception_short_circuit",
        "dependency_ref": "submission-dependencies.json",
        "required_work_items": selected,
        "exception_events": [
            "dispatch_blocked",
            "manual_blocked",
            "runtime_failure",
            "cancellation",
            "timeout",
            "user_input",
        ],
    }
    write_json(directory / "wait-policy.json", policy)
    load_wait_policy(directory, task_id, validate_dependency_ref=True)
    return policy


def snapshot_wait_policy(directory: Path, policy: dict[str, Any]) -> str:
    serialized = json.dumps(policy, indent=2, ensure_ascii=False) + "\n"
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    policy_ref = f"wait-policies/{digest}.json"
    snapshot_path = directory / policy_ref
    if snapshot_path.exists():
        if snapshot_path.read_text(encoding="utf-8") != serialized:
            raise SystemExit("Wait policy snapshot conflicts with its content digest")
    else:
        atomic_write_text(snapshot_path, serialized)
    return policy_ref


def load_exception_wake_evidence(
    directory: Path,
    task_id: str,
    suspension: dict[str, Any],
    resume_event: str,
    resume_ref: str | None,
) -> dict[str, Any]:
    if not resume_ref or not safe_task_evidence_ref(resume_ref):
        raise SystemExit("External resume requires a safe task-local exception wake evidence ref")
    source_path = (directory / resume_ref).resolve()
    try:
        source_path.relative_to(directory.resolve())
    except ValueError:
        raise SystemExit("Exception wake evidence ref escapes the task directory")
    if not source_path.is_file():
        raise SystemExit("Exception wake evidence ref does not exist")
    raw = source_path.read_bytes()
    if not raw:
        raise SystemExit("Exception wake evidence is empty")
    try:
        evidence = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit("Exception wake evidence must be valid UTF-8 JSON") from exc
    if not isinstance(evidence, dict) or set(evidence) != EXCEPTION_WAKE_FIELDS:
        raise SystemExit("Exception wake evidence has unexpected or missing fields")
    if evidence.get("schema_version") != "valp-exception-wake.v1":
        raise SystemExit("Exception wake evidence has an unsupported schema version")
    expected_identity = {
        "task_id": task_id,
        "suspension_id": suspension.get("suspension_id"),
        "suspension_epoch": suspension.get("suspension_epoch"),
        "event": resume_event,
    }
    if any(evidence.get(key) != value for key, value in expected_identity.items()):
        raise SystemExit("Exception wake evidence does not match the current suspension identity")
    principal = evidence.get("principal")
    if not isinstance(principal, dict) or set(principal) != EXCEPTION_WAKE_PRINCIPAL_FIELDS:
        raise SystemExit("Exception wake evidence principal is invalid")
    principal_type = str(principal.get("type") or "")
    principal_id = str(principal.get("id") or "").strip()
    if principal_type not in EXCEPTION_WAKE_PRINCIPAL_TYPES[resume_event] or not principal_id:
        raise SystemExit("Exception wake evidence principal does not match the event")
    reason = evidence.get("reason")
    recorded_at = evidence.get("recorded_at")
    if not isinstance(reason, str) or not reason.strip():
        raise SystemExit("Exception wake evidence reason is missing")
    if not isinstance(recorded_at, str) or not recorded_at.strip():
        raise SystemExit("Exception wake evidence recorded_at is missing")
    supporting_refs = evidence.get("supporting_refs")
    if (
        not isinstance(supporting_refs, list)
        or len(supporting_refs) != len(set(str(ref) for ref in supporting_refs))
        or any(not isinstance(ref, str) or not safe_task_evidence_ref(ref) for ref in supporting_refs)
    ):
        raise SystemExit("Exception wake evidence supporting_refs are invalid")
    if resume_event == "runtime_failure" and not supporting_refs:
        raise SystemExit("Runtime failure wake requires supporting evidence")
    for supporting_ref in supporting_refs:
        if supporting_ref == resume_ref:
            raise SystemExit("Exception wake evidence cannot cite itself as supporting evidence")
        supporting_path = (directory / supporting_ref).resolve()
        try:
            supporting_path.relative_to(directory.resolve())
        except ValueError:
            raise SystemExit("Exception wake supporting evidence escapes the task directory")
        if not supporting_path.is_file() or supporting_path.stat().st_size == 0:
            raise SystemExit("Exception wake supporting evidence is missing or empty")
    if suspension.get("strict_identity"):
        policy = load_wait_policy(
            directory,
            task_id,
            policy_ref=str(suspension.get("wait_policy_ref") or ""),
            validate_dependency_ref=True,
        )
        if resume_event not in (policy.get("exception_events") or []):
            raise SystemExit("Exception wake event is not allowed by the current wait policy")
    elif resume_event not in (suspension.get("allowed_resume_events") or []):
        raise SystemExit("Exception wake event is not allowed by the current suspension")
    return {
        "source_ref": resume_ref,
        "source_digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "principal": {"type": principal_type, "id": principal_id},
        "reason": reason,
        "recorded_at": recorded_at,
        "supporting_refs": list(supporting_refs),
    }


def receipt_matches_work_item(
    receipt: dict[str, Any],
    item: dict[str, Any],
    task_id: str,
    strict_identity: bool,
    suspension_epoch: int | None = None,
    event_sequence_at_entry: int | None = None,
) -> bool:
    if strict_identity:
        if receipt.get("schema_version") != "valp-dispatch-receipt.v2":
            return False
        expected = {
            "task_id": task_id,
            "agent": item.get("agent"),
            "role": item.get("role"),
            "work_item_id": item.get("work_item_id"),
            "dispatch_id": item.get("dispatch_id"),
            "dispatch_generation": item.get("dispatch_generation"),
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            return False
        if (
            not str(receipt.get("receipt_id") or "")
            or type(receipt.get("event_sequence")) is not int
            or type(receipt.get("dispatch_generation")) is not int
        ):
            return False
        if receipt.get("event") in TERMINAL_WORKER_RECEIPT_EVENTS:
            if (
                suspension_epoch is None
                or type(receipt.get("suspension_epoch")) is not int
                or receipt.get("suspension_epoch") != suspension_epoch
            ):
                return False
            if event_sequence_at_entry is None or int(receipt["event_sequence"]) <= event_sequence_at_entry:
                return False
    elif str(receipt.get("agent")) != str(item.get("agent")):
        return False
    receipt_refs = {str(ref) for ref in receipt.get("expected_refs") or []}
    return set(str(ref) for ref in item.get("expected_refs") or []).issubset(receipt_refs)


def work_item_evidence_is_valid(directory: Path, item: dict[str, Any]) -> bool:
    evidence_status = read_json(directory / "evidence-status.json")
    records = evidence_status.get("evidence") or evidence_status.get("items") or {}
    for raw_ref in item.get("expected_refs") or []:
        ref = str(raw_ref)
        if not safe_task_evidence_ref(ref) or not task_evidence_exists(directory, ref):
            return False
        status = "valid"
        if isinstance(records, dict):
            record = records.get(ref)
            if isinstance(record, dict):
                status = str(record.get("status") or "valid").lower()
            elif isinstance(record, str):
                status = record.lower()
        elif isinstance(records, list):
            for record in records:
                if isinstance(record, dict) and record.get("ref") == ref:
                    status = str(record.get("status") or "valid").lower()
                    break
        if status in INVALID_EVIDENCE_STATUSES:
            return False
    return True


def observe_expected_evidence_completions(
    directory: Path,
    task_id: str,
    suspension: dict[str, Any],
) -> int:
    if not suspension.get("strict_identity"):
        return 0
    pending_ids = {str(value) for value in suspension.get("pending_work_item_ids") or []}
    present_at_entry = {
        str(ref) for ref in suspension.get("evidence_refs_present_at_entry") or []
    }
    adopted = [
        adapter_id
        for adapter_id in ("herdr", "queue", "manual", "langgraph")
        if (directory / "runtime" / adapter_id / "adoption.json").is_file()
    ]
    if adopted:
        # Adopted runtimes require their explicit terminal observer. File
        # presence cannot manufacture Full or Manual terminal proof.
        return 0
    receipts = load_dispatch_receipts(directory, task_id)
    existing_ids = {
        str(receipt.get("receipt_id"))
        for receipt in receipts
        if receipt.get("schema_version") == "valp-dispatch-receipt.v2"
        and receipt.get("receipt_id")
    }
    event_sequence = max(
        [
            int(receipt["event_sequence"])
            for receipt in receipts
            if receipt.get("schema_version") == "valp-dispatch-receipt.v2"
            and type(receipt.get("event_sequence")) is int
        ],
        default=0,
    )
    appended = 0
    for item in suspension.get("required_work_items") or []:
        if not isinstance(item, dict) or str(item.get("work_item_id")) not in pending_ids:
            continue
        expected_refs = [str(ref) for ref in item.get("expected_refs") or []]
        if not expected_refs or present_at_entry.intersection(expected_refs):
            continue
        if not work_item_evidence_is_valid(directory, item):
            continue
        submissions = [
            receipt
            for receipt in receipts
            if receipt.get("event") == "dispatch_submitted"
            and receipt_matches_work_item(
                receipt,
                item,
                task_id,
                strict_identity=True,
            )
            and has_concrete_runtime_submission_proof(receipt)
        ]
        if not submissions:
            continue
        submission = max(submissions, key=lambda receipt: int(receipt["event_sequence"]))
        identity = {
            "task_id": task_id,
            "suspension_epoch": suspension.get("suspension_epoch"),
            "work_item": item,
            "submission_receipt_id": submission.get("receipt_id"),
            "expected_refs": expected_refs,
        }
        receipt_id = "sha256:" + hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if receipt_id in existing_ids:
            continue
        event_sequence += 1
        completion = {
            "schema_version": "valp-dispatch-receipt.v2",
            "receipt_id": receipt_id,
            "task_id": task_id,
            "event_sequence": event_sequence,
            "ts": now_iso(),
            "agent": str(item.get("agent") or ""),
            "role": str(item.get("role") or "other"),
            "work_item_id": str(item.get("work_item_id") or ""),
            "dispatch_id": str(item.get("dispatch_id") or ""),
            "dispatch_generation": int(item.get("dispatch_generation") or 1),
            "suspension_epoch": int(suspension.get("suspension_epoch") or 0),
            "event": "dispatch_completed",
            "exit_code": 0,
            "summary": "Expected evidence appeared while the coordinator model was suspended",
            "dispatch_ref": str(
                submission.get("dispatch_ref")
                or f"agents/{item.get('agent')}/dispatch.md"
            ),
            "expected_refs": expected_refs,
            "proof": {
                "observer": "valp.wait.expected-evidence",
                "submission_receipt_id": str(submission.get("receipt_id") or ""),
                "existing_refs": expected_refs,
            },
            "runtime": dict(submission.get("runtime") or {}),
        }
        append_json_line_durable(directory / "dispatch-receipts.jsonl", completion)
        receipts.append(completion)
        existing_ids.add(receipt_id)
        appended += 1
    if appended:
        append_timeline_event(
            directory,
            "expected_evidence_observed",
            f"Observed expected evidence for {appended} suspended work item(s)",
            observer="valp.wait.expected-evidence",
        )
    return appended


def suspend_task(root: Path, task_id: str, timeout_seconds: float | None = None) -> dict[str, Any]:
    root = workspace_root(root)
    directory = task_dir(root, task_id)
    state_path = directory / "state.json"
    with task_state_lock(directory):
        state = recover_wait_projection(directory, read_json_strict(state_path))
        if state.get("status") == "suspended":
            return state.get("suspension") or {}
        if timeout_seconds is None:
            raise SystemExit(
                "Creating a suspension requires an explicit protocol execution timeout"
            )
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise SystemExit("Execution timeout must be a finite non-negative number")
        if state.get("schema_version") != "valp-visible-loop-state.v2":
            raise SystemExit("Legacy v1 task state is read-only; deterministic wait requires state v2")
        if state.get("status") in {"done", "failed", "cancelled"}:
            raise SystemExit(f"Cannot suspend task in terminal state: {state.get('status')}")

        receipts = load_dispatch_receipts(directory, task_id)
        policy = load_wait_policy(
            directory,
            task_id,
            state.get("role_assignments") or {},
        )
        runtime_class = str((state.get("runtime_adapter") or {}).get("class") or "")
        if not policy and runtime_class != "manual":
            raise SystemExit("Full and Remote Mode deterministic wait requires wait-policy.json")
        strict_identity = bool(policy)
        if policy:
            policy_ref = snapshot_wait_policy(directory, policy)
            required_work_items = [dict(item) for item in policy["required_work_items"]]
            for item in required_work_items:
                delivered = any(
                    record.get("event") == "dispatch_submitted"
                    and receipt_matches_work_item(record, item, task_id, strict_identity=True)
                    and has_concrete_runtime_submission_proof(record)
                    for record in receipts
                )
                if not delivered:
                    raise SystemExit(
                        "Cannot suspend before concrete adapter delivery proof for work item "
                        + str(item.get("work_item_id"))
                    )
        else:
            policy_ref = None
            selected_agents = [str(agent) for agent in (state.get("selected_agents") or [])]
            latest_receipts: dict[str, dict[str, Any]] = {}
            for record in receipts:
                latest_receipts[str(record.get("agent"))] = record
            required_work_items = [
                {
                    "work_item_id": f"legacy:{agent}",
                    "agent": agent,
                    "role": str((latest_receipts.get(agent) or {}).get("role") or "other"),
                    "dispatch_id": str((latest_receipts.get(agent) or {}).get("dispatch_id") or f"legacy:{agent}"),
                    "dispatch_generation": int((latest_receipts.get(agent) or {}).get("dispatch_generation") or 1),
                    "expected_refs": [str(ref) for ref in (latest_receipts.get(agent) or {}).get("expected_refs") or []],
                }
                for agent in selected_agents
                if (latest_receipts.get(agent) or {}).get("event") in DELIVERY_RECEIPT_EVENTS
            ]
        if not required_work_items:
            raise SystemExit("Cannot suspend before a selected worker has delivery proof")
        waiting_for_agents = list(dict.fromkeys(str(item["agent"]) for item in required_work_items))

        entered_at = datetime.now(timezone.utc).replace(microsecond=0)
        deadline_at = entered_at + timedelta(seconds=max(0.0, timeout_seconds))
        committed_events = validated_wait_events(directory, task_id)
        suspension_epoch = max(
            [
                int(event.get("suspension_epoch") or 0)
                for event in committed_events
                if type(event.get("suspension_epoch")) is int
            ],
            default=0,
        ) + 1
        state_revision_at_entry = exact_state_revision(state)
        suspension_seed = f"{task_id}:{suspension_epoch}:{state_revision_at_entry}:{len(receipts)}"
        suspension_id = "sha256:" + hashlib.sha256(suspension_seed.encode("utf-8")).hexdigest()
        suspension = {
            "status": "waiting",
            "suspension_id": suspension_id,
            "suspension_epoch": suspension_epoch,
            "state_revision_at_entry": state_revision_at_entry,
            "wait_policy_ref": policy_ref,
            "wait_policy_id": policy.get("wait_policy_id") if policy else "legacy-agent-wait",
            "strict_identity": strict_identity,
            "event_sequence_at_entry": len(committed_events),
            "receipt_event_sequence_at_entry": max(
                [int(record.get("event_sequence")) for record in receipts if isinstance(record.get("event_sequence"), int)],
                default=0,
            ),
            "receipt_cursor_at_entry": len(receipts),
            "required_work_items": required_work_items,
            "required_work_item_ids": [str(item["work_item_id"]) for item in required_work_items],
            "pending_work_item_ids": [str(item["work_item_id"]) for item in required_work_items],
            "completed_work_item_ids": [],
            "failed_work_item_ids": [],
            "entered_at": entered_at.isoformat().replace("+00:00", "Z"),
            "deadline_at": deadline_at.isoformat().replace("+00:00", "Z"),
            "execution_deadline": deadline_at.isoformat().replace("+00:00", "Z"),
            "waiting_for_agents": waiting_for_agents,
            "receipt_count_at_entry": len(receipts),
            "evidence_refs_present_at_entry": sorted(
                {
                    str(ref)
                    for item in required_work_items
                    for ref in item.get("expected_refs") or []
                    if task_evidence_exists(directory, str(ref))
                }
            ),
            "allowed_resume_events": sorted(SUSPENSION_RESUME_EVENTS),
        }
        if any(
            (directory / "runtime" / adapter_id / "adoption.json").is_file()
            for adapter_id in ("herdr", "queue", "langgraph")
        ):
            from .kernel_runtime import KernelRuntimeError, start_kernel_suspension

            try:
                start_kernel_suspension(directory, task_id, suspension, receipts)
            except KernelRuntimeError as error:
                raise SystemExit(f"Kernel suspension persistence failed: {error}") from error
        state["status"] = "suspended"
        state["suspension"] = suspension
        state["updated_at"] = now_iso()
        commit_wait_state(
            directory,
            state,
            "coordinator_suspended",
            "Coordinator model turns suspended while workers run",
        )
        return suspension


def recover_timed_out_task_from_receipt(
    directory: Path,
    task_id: str,
    state: dict[str, Any],
    suspension: dict[str, Any],
    resume_ref: str | None,
) -> dict[str, Any]:
    accepted = suspension.get("accepted_wake") or {}
    if (
        suspension.get("status") != "resumed"
        or suspension.get("resume_event") != "timeout"
        or accepted.get("resume_event") != "timeout"
        or accepted.get("wake_reason") != "timeout"
    ):
        raise SystemExit("Late completion recovery requires an accepted timeout wake")
    if not resume_ref or not resume_ref.startswith("dispatch-receipts.jsonl#"):
        raise SystemExit("Late completion recovery requires a dispatch receipt ref")
    try:
        receipt_index = int(resume_ref.rsplit("#", 1)[1])
    except (ValueError, IndexError):
        raise SystemExit("Invalid dispatch receipt ref")

    receipts = load_dispatch_receipts(directory, task_id)
    if receipt_index < 1 or receipt_index > len(receipts):
        raise SystemExit("Dispatch receipt ref does not exist")
    if receipt_index <= int(suspension.get("receipt_count_at_entry") or 0):
        raise SystemExit("Late completion receipt predates the timed-out suspension")
    accepted_receipt_cursor = suspension.get("receipt_cursor")
    if (
        type(accepted_receipt_cursor) is int
        and receipt_index <= accepted_receipt_cursor
    ):
        raise SystemExit(
            f"Conflicting wake for completed suspension epoch {suspension.get('suspension_epoch')}"
        )
    receipt = receipts[receipt_index - 1]
    if receipt.get("event") != "dispatch_completed":
        raise SystemExit("Late completion recovery requires dispatch_completed")

    required_work_items = suspension.get("required_work_items") or []
    matching_items = [
        item
        for item in required_work_items
        if isinstance(item, dict)
        and receipt_matches_work_item(
            receipt,
            item,
            task_id,
            strict_identity=True,
            suspension_epoch=int(suspension.get("suspension_epoch") or 0),
            event_sequence_at_entry=int(suspension.get("receipt_event_sequence_at_entry") or 0),
        )
    ]
    if len(matching_items) != 1:
        raise SystemExit("Late completion receipt does not match one timed-out work item identity")
    item = matching_items[0]
    work_item_id = str(item.get("work_item_id") or "")
    if work_item_id not in {str(value) for value in suspension.get("pending_work_item_ids") or []}:
        raise SystemExit("Late completion receipt does not recover a pending timed-out work item")
    if not work_item_evidence_is_valid(directory, item):
        raise SystemExit("Late completion receipt expected evidence is missing or invalid")

    proof = receipt.get("proof") if isinstance(receipt.get("proof"), dict) else {}
    submission_receipt_id = str(proof.get("submission_receipt_id") or "")
    submissions = [
        candidate
        for candidate in receipts
        if candidate.get("receipt_id") == submission_receipt_id
        and candidate.get("event") == "dispatch_submitted"
        and receipt_matches_work_item(candidate, item, task_id, strict_identity=True)
        and has_concrete_runtime_submission_proof(candidate)
    ]
    if not submission_receipt_id or len(submissions) != 1:
        raise SystemExit("Late completion receipt is not bound to one concrete submission receipt")

    recovery_identity = {
        "suspension_id": suspension.get("suspension_id"),
        "completion_receipt_id": receipt.get("receipt_id"),
        "completion_receipt_ref": resume_ref,
    }
    recovery_events = [
        event
        for event in read_json_lines(directory / "timeline.jsonl")
        if event.get("event") == "late_completion_recovered"
        and event.get("suspension_id") == suspension.get("suspension_id")
    ]
    conflicts = [
        event
        for event in recovery_events
        if any(event.get(key) != value for key, value in recovery_identity.items())
    ]
    if conflicts:
        raise SystemExit("Timed-out suspension already has a conflicting late completion recovery")
    if not recovery_events:
        append_timeline_event(
            directory,
            "late_completion_recovered",
            "Recovered current task progress from an identity-bound late completion receipt",
            **recovery_identity,
            submission_receipt_id=submission_receipt_id,
            preserved_timeout_result_ref=accepted.get("result_ref"),
        )

    if state.get("status") not in {"blocked", "dispatching"}:
        raise SystemExit(f"Late completion recovery cannot advance task status {state.get('status')}")
    if state.get("status") == "blocked":
        state["status"] = "dispatching"
        state["updated_at"] = now_iso()
        write_json(directory / "state.json", state)

    budget = read_json(directory / "iteration-budget.json")
    if budget:
        stop_reasons = {
            reason.strip()
            for reason in str(budget.get("stop_reason") or "").split(";")
            if reason.strip()
        }
        if budget.get("status") == "blocked" and stop_reasons == {"task status is blocked"}:
            budget["status"] = "active"
            budget["stop_reason"] = None
        routing = read_json(directory / "routing.json")
        if routing:
            budget = refresh_iteration_budget(directory, routing, budget)

    return {
        "schema_version": "valp-timeout-recovery.v1",
        "task_id": task_id,
        "recovery_event": "late_completion",
        "resume_event": "receipt",
        "resume_ref": resume_ref,
        "completion_receipt_id": receipt.get("receipt_id"),
        "submission_receipt_id": submission_receipt_id,
        "preserved_timeout_result_ref": accepted.get("result_ref"),
        "resulting_task_status": state.get("status"),
        "iteration_budget_status": budget.get("status") if budget else "not_recorded",
    }


def resume_suspended_task(
    root: Path,
    task_id: str,
    resume_event: str,
    resume_ref: str | None = None,
) -> dict[str, Any]:
    if resume_event not in SUSPENSION_RESUME_EVENTS:
        raise SystemExit(f"Unsupported resume event: {resume_event}")
    root = workspace_root(root)
    directory = task_dir(root, task_id)
    state_path = directory / "state.json"
    with task_state_lock(directory):
        state = recover_wait_projection(directory, read_json_strict(state_path))
        suspension = state.get("suspension") or {}
        wake_reason = resume_event
        qualifying_receipt_id: str | None = None
        external_event = (
            load_exception_wake_evidence(
                directory,
                task_id,
                suspension,
                resume_event,
                resume_ref,
            )
            if resume_event in EXTERNAL_RESUME_EVENTS
            else None
        )
        if state.get("status") != "suspended" or suspension.get("status") != "waiting":
            accepted = suspension.get("accepted_wake") or {}
            if accepted:
                if resume_event == "receipt" and accepted.get("resume_event") == "timeout":
                    return recover_timed_out_task_from_receipt(
                        directory,
                        task_id,
                        state,
                        suspension,
                        resume_ref,
                    )
                if (
                    accepted.get("resume_event") == resume_event
                    and accepted.get("resume_ref") == resume_ref
                    and (
                        external_event is None
                        or accepted.get("external_event") == external_event
                    )
                ):
                    return suspension
                raise SystemExit(f"Conflicting wake for completed suspension epoch {suspension.get('suspension_epoch')}")
            raise SystemExit(f"Task {task_id} is not suspended")
        if resume_event == "timeout":
            deadline_text = str(suspension.get("deadline_at") or "")
            try:
                deadline = datetime.fromisoformat(deadline_text.replace("Z", "+00:00"))
            except ValueError:
                raise SystemExit("Suspension deadline is missing or invalid")
            if deadline.tzinfo is None:
                raise SystemExit("Suspension deadline must include a timezone")
            if datetime.now(timezone.utc) < deadline.astimezone(timezone.utc):
                raise SystemExit("Cannot resume from timeout before the recorded deadline")
            suspension["receipt_cursor"] = len(
                load_dispatch_receipts(directory, task_id)
            )
        if resume_event == "receipt":
            if not resume_ref or not resume_ref.startswith("dispatch-receipts.jsonl#"):
                raise SystemExit("receipt resume requires a dispatch receipt ref")
            try:
                receipt_index = int(resume_ref.rsplit("#", 1)[1])
            except (ValueError, IndexError):
                raise SystemExit("Invalid dispatch receipt ref")
            receipts = load_dispatch_receipts(directory, task_id)
            if receipt_index < 1 or receipt_index > len(receipts):
                raise SystemExit("Dispatch receipt ref does not exist")
            if receipt_index <= int(suspension.get("receipt_count_at_entry") or 0):
                raise SystemExit("Dispatch receipt predates suspension")
            receipt = receipts[receipt_index - 1]
            if not runtime_receipt_is_effective(directory, task_id, receipt):
                raise SystemExit(
                    "Dispatch receipt was revoked or not selected by Manual adjudication"
                )
            if receipt.get("event") not in TERMINAL_WORKER_RECEIPT_EVENTS:
                raise SystemExit("Dispatch receipt is not a terminal worker receipt")
            strict_identity = bool(suspension.get("strict_identity"))
            required_work_items = suspension.get("required_work_items") or []
            matching_items = [
                item
                for item in required_work_items
                if isinstance(item, dict)
                and receipt_matches_work_item(
                    receipt,
                    item,
                    task_id,
                    strict_identity,
                    suspension_epoch=int(suspension.get("suspension_epoch") or 0),
                    event_sequence_at_entry=int(suspension.get("receipt_event_sequence_at_entry") or 0),
                )
            ]
            if len(matching_items) != 1:
                raise SystemExit("Dispatch receipt does not match one required work item identity")
            item = matching_items[0]
            work_item_id = str(item["work_item_id"])
            receipt_event = str(receipt.get("event") or "")
            qualifying_receipt_id = str(receipt.get("receipt_id") or resume_ref)
            runtime_class = str((state.get("runtime_adapter") or {}).get("class") or "")
            if strict_identity and receipt_event in {"manual_result_attested", "manual_blocked"} and runtime_class != "manual":
                raise SystemExit("Manual receipt cannot wake a Full or Remote Mode suspension")
            if any(
                event.get("qualifying_receipt_id") == qualifying_receipt_id
                for event in validated_wait_events(directory, task_id)
            ):
                return suspension
            if receipt_event in {"dispatch_completed", "manual_result_attested"}:
                if not work_item_evidence_is_valid(directory, item):
                    raise SystemExit("Completion receipt expected evidence is missing or invalid")
                if (
                    receipt_event == "dispatch_completed"
                    and (directory / "runtime" / "kernel" / "genesis.json").is_file()
                ):
                    from .kernel_runtime import KernelRuntimeError, record_kernel_completion

                    try:
                        record_kernel_completion(directory, task_id, receipt)
                    except KernelRuntimeError as error:
                        raise SystemExit(f"Kernel Work Item completion failed: {error}") from error
                completed = list(suspension.get("completed_work_item_ids") or [])
                pending = list(suspension.get("pending_work_item_ids") or [])
                if pending == [work_item_id]:
                    invalid_work_items = [
                        str(required_item.get("work_item_id") or "unknown")
                        for required_item in required_work_items
                        if isinstance(required_item, dict)
                        and not work_item_evidence_is_valid(directory, required_item)
                    ]
                    if invalid_work_items:
                        raise SystemExit(
                            "dependency_ready required work item evidence is missing or invalid: "
                            + ", ".join(invalid_work_items)
                        )
                if work_item_id not in completed:
                    completed.append(work_item_id)
                pending = [value for value in pending if value != work_item_id]
                suspension["completed_work_item_ids"] = completed
                suspension["pending_work_item_ids"] = pending
                suspension["receipt_cursor"] = max(
                    int(suspension.get("receipt_cursor") or suspension.get("receipt_cursor_at_entry") or 0),
                    receipt_index,
                )
                state["suspension"] = suspension
                state["updated_at"] = now_iso()
                if pending:
                    commit_wait_state(
                        directory,
                        state,
                        "work_item_completed",
                        "Required work item completed; dependency barrier remains pending",
                        work_item_id=work_item_id,
                        qualifying_receipt_id=qualifying_receipt_id,
                        receipt_ref=resume_ref,
                    )
                    return suspension
                wake_reason = "dependency_ready"
            else:
                failed = list(suspension.get("failed_work_item_ids") or [])
                if work_item_id not in failed:
                    failed.append(work_item_id)
                suspension["failed_work_item_ids"] = failed
                wake_reason = receipt_event

        pair_error = wake_reason_pair_error(resume_event, wake_reason)
        if pair_error:
            raise SystemExit(pair_error)
        resumed_at = now_iso()
        events = validated_wait_events(directory, task_id)
        accepted_sequence = len(events) + 1
        resulting_revision = exact_state_revision(state) + 1
        event_id = wait_event_id(
            task_id,
            "coordinator_resumed",
            str(suspension.get("suspension_id") or ""),
            accepted_sequence,
            resulting_revision,
        )
        wake_id = deterministic_wake_id(
            task_id,
            int(suspension.get("suspension_epoch") or 0),
            resume_event,
            resume_ref,
            external_event,
        )
        result_ref = f"wake-results/{wake_id.removeprefix('sha256:')}.json"
        accepted_wake = {
            "wake_id": wake_id,
            "wake_event_id": event_id,
            "wake_reason": wake_reason,
            "resume_event": resume_event,
            "resume_ref": resume_ref,
            "accepted_sequence": accepted_sequence,
            "resulting_state_revision": resulting_revision,
            "result_ref": result_ref,
        }
        if external_event is not None:
            accepted_wake["external_event"] = external_event
        suspension.update({
            "status": "resumed",
            "resume_event": resume_event,
            "resumed_at": resumed_at,
            "accepted_wake": accepted_wake,
        })
        if resume_ref:
            suspension["resume_ref"] = resume_ref
        state["status"] = TASK_STATUS_BY_WAKE_REASON[wake_reason]
        state["suspension"] = suspension
        state["updated_at"] = resumed_at
        wake_result = {
            "schema_version": "valp-wake-result.v1",
            "task_id": task_id,
            "suspension_id": suspension.get("suspension_id"),
            "suspension_epoch": suspension.get("suspension_epoch"),
            **suspension["accepted_wake"],
            "resulting_task_status": state["status"],
            "completed_work_item_ids": list(suspension.get("completed_work_item_ids") or []),
            "pending_work_item_ids": list(suspension.get("pending_work_item_ids") or []),
            "failed_work_item_ids": list(suspension.get("failed_work_item_ids") or []),
            "recorded_at": resumed_at,
        }
        result_path = directory / result_ref
        if result_path.exists():
            existing_result = read_json_strict(result_path)
            existing_recorded_at = existing_result.get("recorded_at")
            if not isinstance(existing_recorded_at, str) or not existing_recorded_at:
                raise SystemExit("Pre-existing wake result conflicts with the pending commit")
            resumed_at = existing_recorded_at
            suspension["resumed_at"] = resumed_at
            state["updated_at"] = resumed_at
            wake_result["recorded_at"] = resumed_at
            expected_bytes = (
                json.dumps(wake_result, indent=2, ensure_ascii=False) + "\n"
            ).encode("utf-8")
            if result_path.read_bytes() != expected_bytes:
                raise SystemExit("Pre-existing wake result conflicts with the pending commit")
        else:
            write_json(result_path, wake_result)
        if (
            wake_reason == "dependency_ready"
            and (directory / "runtime" / "kernel" / "genesis.json").is_file()
        ):
            from .kernel_runtime import KernelRuntimeError, accept_kernel_wake

            try:
                accept_kernel_wake(
                    directory,
                    task_id,
                    wake_id,
                    workflow_projection={
                        "status": state["status"],
                        "suspension": suspension,
                        "updated_at": state["updated_at"],
                        "result_ref": result_ref,
                    },
                )
            except KernelRuntimeError as error:
                raise SystemExit(f"Kernel dependency wake failed: {error}") from error
        continuation_status = "not_available"
        if wake_reason == "dependency_ready":
            from .continuation import ContinuationError, prepare_wake_continuation

            try:
                continuation = prepare_wake_continuation(
                    directory, task_id, suspension, wake_result
                )
            except ContinuationError as error:
                raise SystemExit(f"Coordinator continuation preparation failed: {error}") from error
            continuation_status = str(continuation.get("status") or "unknown")
        event_details = {
            "resume_event": resume_event,
            "resume_ref": resume_ref,
            "wake_reason": wake_reason,
            "wake_id": wake_id,
            "result_ref": result_ref,
            "qualifying_receipt_id": qualifying_receipt_id,
            "continuation_status": continuation_status,
        }
        if external_event is not None:
            event_details["external_event"] = external_event
        commit_wait_state(
            directory,
            state,
            "coordinator_resumed",
            f"Coordinator resumed from {resume_event}",
            **event_details,
        )
        return suspension


def wait_for_task(
    root: Path,
    task_id: str,
    timeout_seconds: float = 300.0,
    poll_interval_seconds: float = 0.25,
    execution_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise SystemExit("Wait timeout must be a finite non-negative number")
    if not math.isfinite(poll_interval_seconds) or poll_interval_seconds < 0:
        raise SystemExit("Poll interval must be a finite non-negative number")
    if execution_timeout_seconds is not None and (
        not math.isfinite(execution_timeout_seconds) or execution_timeout_seconds < 0
    ):
        raise SystemExit("Execution timeout must be a finite non-negative number")
    root = workspace_root(root)
    directory = task_dir(root, task_id)
    suspension = suspend_task(
        root,
        task_id,
        timeout_seconds=execution_timeout_seconds,
    )
    observation_deadline = time.monotonic() + timeout_seconds
    scan_cursor = int(
        suspension.get("receipt_cursor")
        or suspension.get("receipt_count_at_entry")
        or 0
    )
    while True:
        with task_state_lock(directory):
            state = recover_wait_projection(directory, read_json_strict(directory / "state.json"))
            current = state.get("suspension") or suspension
            if state.get("status") != "suspended" or current.get("status") != "waiting":
                if current.get("accepted_wake"):
                    return current
                raise SystemExit(f"Task {task_id} left suspended state without an accepted wake")
            observe_expected_evidence_completions(directory, task_id, current)

        receipts = load_dispatch_receipts(directory, task_id)
        receipt_count = max(
            scan_cursor,
            int(current.get("receipt_cursor") or current.get("receipt_count_at_entry") or 0),
        )
        waiting_for_agents = {str(agent) for agent in (current.get("waiting_for_agents") or [])}
        for receipt_index, record in enumerate(receipts[receipt_count:], start=receipt_count + 1):
            scan_cursor = receipt_index
            if str(record.get("agent")) not in waiting_for_agents:
                continue
            if record.get("event") not in TERMINAL_WORKER_RECEIPT_EVENTS:
                continue
            if not runtime_receipt_is_effective(directory, task_id, record):
                continue
            strict_identity = bool(current.get("strict_identity"))
            if not any(
                isinstance(item, dict)
                and receipt_matches_work_item(
                    record,
                    item,
                    task_id,
                    strict_identity,
                    suspension_epoch=int(current.get("suspension_epoch") or 0),
                    event_sequence_at_entry=int(
                        current.get("receipt_event_sequence_at_entry") or 0
                    ),
                )
                for item in current.get("required_work_items") or []
            ):
                continue
            reduced = resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref=f"dispatch-receipts.jsonl#{receipt_index}",
            )
            if reduced.get("status") == "resumed":
                return reduced
            current = reduced

        execution_deadline_text = str(
            current.get("execution_deadline") or current.get("deadline_at") or ""
        )
        try:
            execution_deadline = datetime.fromisoformat(
                execution_deadline_text.replace("Z", "+00:00")
            )
        except ValueError:
            raise SystemExit("Suspension execution deadline is missing or invalid")
        if execution_deadline.tzinfo is None:
            raise SystemExit("Suspension execution deadline must include a timezone")
        if datetime.now(timezone.utc) >= execution_deadline.astimezone(timezone.utc):
            return resume_suspended_task(
                root,
                task_id,
                "timeout",
                resume_ref="state.json#execution_deadline",
            )

        if time.monotonic() >= observation_deadline:
            return {
                **current,
                "wait_window": {
                    "status": "elapsed",
                    "timeout_seconds": timeout_seconds,
                    "worker_cancelled": False,
                },
            }

        time.sleep(max(0.01, poll_interval_seconds))


def latest_phase_delivery_event(
    submission_dependencies: dict[str, Any],
    phase: tuple[str, str],
    receipts: list[dict[str, Any]],
    task_id: str,
) -> str | None:
    target, role = phase
    work_item = next(
        (
            item
            for item in submission_dependencies.get("work_items") or []
            if isinstance(item, dict)
            and item.get("agent") == target
            and item.get("role") == role
        ),
        None,
    )
    if not work_item:
        return None
    latest: str | None = None
    for receipt in receipts:
        if (
            receipt.get("schema_version") == "valp-dispatch-receipt.v2"
            and receipt.get("task_id") == task_id
            and receipt.get("agent") == work_item.get("agent")
            and receipt.get("role") == work_item.get("role")
            and receipt.get("work_item_id") == work_item.get("work_item_id")
            and receipt.get("dispatch_id") == work_item.get("dispatch_id")
            and receipt.get("dispatch_generation") == work_item.get("dispatch_generation")
            and receipt.get("event") in DELIVERY_RECEIPT_EVENTS | TERMINAL_WORKER_RECEIPT_EVENTS
        ):
            if (
                receipt.get("event") == "dispatch_submitted"
                and not has_concrete_runtime_submission_proof(receipt)
            ):
                continue
            latest = str(receipt.get("event"))
    return latest


def dependency_ready_frontier(
    submission_dependencies: dict[str, Any],
    phases: list[tuple[str, str]],
    receipts: list[dict[str, Any]],
    directory: Path,
    task_id: str,
) -> tuple[list[tuple[str, str]], list[str]]:
    ready: list[tuple[str, str]] = []
    blocked: list[str] = []
    receipts = [
        receipt
        for receipt in receipts
        if runtime_receipt_is_effective(directory, task_id, receipt)
    ]
    evidence_status = read_json(directory / "evidence-status.json")
    for phase in phases:
        latest_event = latest_phase_delivery_event(
            submission_dependencies,
            phase,
            receipts,
            task_id,
        )
        if latest_event in DELIVERY_RECEIPT_EVENTS | {"dispatch_completed", "manual_result_attested"}:
            continue
        if latest_event in {"dispatch_blocked", "manual_blocked"}:
            blocked.append(f"{phase[1]}:{phase[0]} is blocked")
            continue
        dependency_errors = unmet_dependencies_for_phases(
            submission_dependencies,
            [phase],
            receipts,
            directory,
            evidence_status,
            manual_mode=False,
            correction_cycle=read_json(directory / "correction-cycle.json"),
        )
        if dependency_errors:
            blocked.extend(dependency_errors)
        else:
            ready.append(phase)
    return ready, list(dict.fromkeys(blocked))


def incomplete_submission_recovery_context(
    directory: Path,
    task_id: str,
    routing: dict[str, Any],
    submission_dependencies: dict[str, Any],
    receipts: list[dict[str, Any]],
    phase: tuple[str, str],
    retry_generation: int,
    runtime_kind: str,
) -> dict[str, Any]:
    target, role = phase
    if runtime_kind != "herdr":
        raise SystemExit("Incomplete submission recovery is supported only by the HERDR adapter")
    if retry_generation != 1:
        raise SystemExit("Incomplete submission recovery accepts only retry generation 1")

    work_items = [
        item
        for item in submission_dependencies.get("work_items") or []
        if isinstance(item, dict)
        and item.get("agent") == target
        and item.get("role") == role
    ]
    if len(work_items) != 1:
        raise SystemExit("Incomplete submission recovery requires one exact routed work item")
    work_item = work_items[0]

    contract_path = directory / CONTROL_CONTRACT_REF
    contract = read_json_strict(contract_path)
    contract_errors = validate_control_contract(contract, task_id)
    if contract_errors:
        raise SystemExit("Invalid worker control contract: " + "; ".join(contract_errors))
    digest = control_contract_digest(contract, contract_path.read_bytes())
    contract_record = routing.get("control_contract") or {}
    if (
        contract_record.get("status") != "recorded"
        or contract_record.get("ref") != CONTROL_CONTRACT_REF
        or contract_record.get("digest") != digest
    ):
        raise SystemExit("Incomplete submission recovery control-contract identity mismatch")

    slice_ref = str((routing.get("control_slices") or {}).get(target) or "")
    if slice_ref != f"control-slices/{target}.json":
        raise SystemExit("Incomplete submission recovery control slice is missing or inconsistent")
    agent_work_item_ids = [
        str(item.get("work_item_id") or "")
        for item in submission_dependencies.get("work_items") or []
        if isinstance(item, dict) and item.get("agent") == target
    ]
    control_slice = read_json_strict(directory / slice_ref)
    slice_errors = validate_control_slice(
        control_slice,
        task_id,
        target,
        agent_work_item_ids,
        digest,
    )
    if slice_errors or work_item.get("work_item_id") not in control_slice.get("work_item_ids", []):
        details = slice_errors or ["selected work item is absent"]
        raise SystemExit("Incomplete submission recovery control slice mismatch: " + "; ".join(details))

    expected = [str(ref) for ref in work_item.get("expected_refs") or []]
    identity_receipts = [
        receipt
        for receipt in receipts
        if receipt.get("schema_version") == "valp-dispatch-receipt.v2"
        and receipt.get("task_id") == task_id
        and receipt.get("agent") == work_item.get("agent")
        and receipt.get("role") == work_item.get("role")
        and receipt.get("work_item_id") == work_item.get("work_item_id")
        and receipt.get("dispatch_id") == work_item.get("dispatch_id")
        and receipt.get("dispatch_generation") == work_item.get("dispatch_generation")
        and receipt.get("dispatch_ref") == f"agents/{target}/dispatch.md"
        and receipt.get("expected_refs") == expected
    ]
    terminal = [
        receipt
        for receipt in identity_receipts
        if receipt.get("event") in TERMINAL_WORKER_RECEIPT_EVENTS
    ]
    if terminal:
        raise SystemExit("Incomplete submission recovery rejects an already terminal work item")
    prior_retries = [
        receipt for receipt in identity_receipts if receipt.get("retry_generation") is not None
    ]
    originating = [
        receipt
        for receipt in identity_receipts
        if receipt.get("event") == "dispatch_submitted"
        and receipt.get("retry_generation") is None
        and has_concrete_runtime_submission_proof(receipt)
        and (receipt.get("proof") or {}).get("runtime") == "HERDR"
    ]
    if len(originating) != 1:
        raise SystemExit(
            "Incomplete submission recovery requires exactly one concrete identity-bound HERDR submission"
        )
    completion_submission = originating[0]
    if prior_retries:
        expected_recovery_proof = {
            "kind": "incomplete_submission",
            "retry_generation": retry_generation,
            "originating_submission_receipt_id": originating[0]["receipt_id"],
            "control_contract_digest": digest,
        }
        retry_submissions = [
            receipt
            for receipt in prior_retries
            if receipt.get("event") == "dispatch_submitted"
            and receipt.get("retry_generation") == retry_generation
            and has_concrete_runtime_submission_proof(receipt)
            and (receipt.get("proof") or {}).get("runtime") == "HERDR"
            and (receipt.get("proof") or {}).get("recovery") == expected_recovery_proof
        ]
        if len(prior_retries) != 1 or len(retry_submissions) != 1:
            raise SystemExit(
                "Incomplete submission recovery found conflicting retry receipt identity"
            )
        completion_submission = retry_submissions[0]

    baseline_value = (completion_submission.get("proof") or {}).get(
        "expected_evidence_baseline"
    )
    baseline: dict[str, str | None] | None = None
    if baseline_value is not None:
        if (
            not isinstance(baseline_value, dict)
            or set(baseline_value) != set(expected)
            or any(value is not None and not isinstance(value, str) for value in baseline_value.values())
        ):
            raise SystemExit("Incomplete submission recovery expected-evidence baseline is invalid")
        baseline = {str(ref): value for ref, value in baseline_value.items()}

    complete, existing_refs, _missing_refs = herdr_expected_ref_status(
        directory,
        expected,
        baseline,
    )
    if complete:
        return {
            "action": "reconcile_completion",
            "originating_submission": completion_submission,
            "existing_refs": existing_refs,
            "work_item_id": work_item["work_item_id"],
        }
    if prior_retries:
        raise SystemExit("Incomplete submission recovery was already attempted for this work item")
    if existing_refs:
        raise SystemExit(
            "Incomplete submission recovery requires all expected evidence to be valid or all to remain absent or invalid"
        )
    return {
        "action": "retry_submission",
        "retry_generation": retry_generation,
        "originating_submission_receipt_id": originating[0]["receipt_id"],
        "control_contract_digest": digest,
        "work_item_id": work_item["work_item_id"],
    }


def dispatch_task(
    root: Path,
    task_id: str,
    agent: str = "all",
    submit: bool = False,
    runtime: str | None = None,
    role: str | None = None,
    wait_seconds: float | None = None,
    proof_seconds: float | None = None,
    recover_incomplete: bool = False,
    retry_generation: int | None = None,
    replace_owned_session_launch: bool = False,
    reprovision_done_session: bool = False,
) -> list[str]:
    for label, value in (
        ("Evidence wait", wait_seconds),
        ("Submission proof timeout", proof_seconds),
    ):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise SystemExit(f"{label} must be a finite non-negative number")
    if recover_incomplete:
        if not submit:
            raise SystemExit("Incomplete submission recovery requires --submit")
        if agent == "all" or role is None:
            raise SystemExit(
                "Incomplete submission recovery requires one explicit --agent and --role"
            )
        if retry_generation is None:
            raise SystemExit("Incomplete submission recovery requires --retry-generation")
    elif retry_generation is not None:
        raise SystemExit("--retry-generation requires --recover-incomplete")
    if replace_owned_session_launch and (
        not submit or agent == "all" or role is None or recover_incomplete
    ):
        raise SystemExit(
            "--replace-owned-session-launch requires --submit, one explicit --agent and --role, "
            "and cannot be combined with --recover-incomplete"
        )
    if reprovision_done_session and (
        not submit or agent == "all" or role is None or recover_incomplete or replace_owned_session_launch
    ):
        raise SystemExit(
            "--reprovision-done-session requires --submit, one explicit --agent and --role, "
            "and cannot be combined with --recover-incomplete or --replace-owned-session-launch"
        )
    root = workspace_root(root)
    directory = task_dir(root, task_id)
    routing = read_json(directory / "routing.json")
    if not routing:
        raise SystemExit(f"Missing routing.json for task {task_id}")
    selected_agents = routing.get("selected_agents") or []
    role_assignments = routing.get("role_assignments") or {}
    if role and role not in {"coordinator", "implementer", "reviewer", "prototype", "researcher", "other"}:
        raise SystemExit(f"Unsupported dispatch role: {role}")
    if agent == "all" and role:
        targets = [target for target in selected_agents if role in roles_for_agent(role_assignments, target)]
    else:
        targets = selected_agents if agent == "all" else [agent]
    unknown_targets = [target for target in targets if target not in selected_agents]
    if unknown_targets:
        raise SystemExit("Agent is not declared for this task: " + ", ".join(unknown_targets))
    if not targets:
        raise SystemExit("No Leader-declared Agent is assigned to the requested role")

    phases: list[tuple[str, str]] = []
    for target in targets:
        assigned_roles = roles_for_agent(role_assignments, target)
        if role:
            if role not in assigned_roles:
                raise SystemExit(f"Agent {target} is not assigned role {role}")
            phases.append((target, role))
        else:
            phases.extend((target, assigned_role) for assigned_role in assigned_roles)

    runtime_record = routing.get("runtime_adapter") or {}
    requested_runtime = normalize_runtime(runtime)
    runtime_kind = runtime_from_adapter_record(runtime_record) if requested_runtime == "auto" else requested_runtime
    manual_mode = runtime_kind == "manual"
    state = read_json(directory / "state.json")
    if not state:
        raise SystemExit(f"Missing state.json for task {task_id}")
    if reprovision_done_session:
        prior_delivery = [
            receipt for receipt in load_dispatch_receipts(directory, task_id)
            if receipt.get("event") in DELIVERY_RECEIPT_EVENTS
            and receipt.get("agent") == agent
            and receipt.get("role") == role
        ]
        if prior_delivery:
            raise SystemExit("Fenced done-session reprovision is blocked after delivery receipt")

    expected_submission_marker = {"status": "recorded", "ref": "submission-dependencies.json"}
    routing_submission_marker = routing.get("submission_dependencies") or {}
    state_submission_marker = state.get("submission_dependencies") or {}
    submission_path = directory / "submission-dependencies.json"
    submission_dependencies: dict[str, Any] = {}
    if submit and (
        routing_submission_marker != expected_submission_marker
        or state_submission_marker != expected_submission_marker
        or not submission_path.is_file()
    ):
        raise SystemExit("Submission dependency policy is missing or inconsistent")
    if routing_submission_marker or state_submission_marker or submission_path.exists():
        if (
            routing_submission_marker != expected_submission_marker
            or state_submission_marker != expected_submission_marker
        ):
            raise SystemExit("Submission dependency marker is missing or inconsistent")
        submission_dependencies = read_json(submission_path)
        dependency_errors = validate_submission_dependencies(
            submission_dependencies,
            task_id,
            role_assignments,
        )
        if dependency_errors:
            raise SystemExit("Invalid submission dependencies: " + "; ".join(dependency_errors))

    expected_delegation_marker = {"status": "recorded", "ref": "delegation-policy.json"}
    routing_delegation_marker = routing.get("delegation_policy") or {}
    state_delegation_marker = state.get("delegation_policy") or {}
    delegation_path = directory / "delegation-policy.json"
    if submit and (
        routing_delegation_marker != expected_delegation_marker
        or state_delegation_marker != expected_delegation_marker
        or not delegation_path.is_file()
    ):
        raise SystemExit("Delegation policy is missing or inconsistent")
    if routing_delegation_marker or state_delegation_marker or delegation_path.exists():
        if (
            routing_delegation_marker != expected_delegation_marker
            or state_delegation_marker != expected_delegation_marker
        ):
            raise SystemExit("Delegation policy marker is missing or inconsistent")
        delegation_policy = read_json(delegation_path)
        delegation_errors = validate_delegation_policy(
            delegation_policy,
            task_id,
            manual_mode=manual_mode,
        )
        if delegation_errors:
            raise SystemExit("Invalid delegation policy: " + "; ".join(delegation_errors))
        if delegation_policy.get("violations"):
            raise SystemExit("Delegated dispatch is blocked by a recorded live self-modification violation")

    recovery_context: dict[str, Any] | None = None
    if submission_dependencies and (submit or not manual_mode):
        translated = 0
        if not manual_mode:
            translated = translate_legacy_herdr_receipts(directory, task_id)
        if translated:
            append_timeline_event(
                directory,
                "runtime_receipts_translated",
                f"Translated {translated} existing legacy runtime receipt(s) before dependency evaluation",
            )
        receipts = load_dispatch_receipts(directory, task_id)
        if manual_mode:
            receipts = [
                receipt
                for receipt in receipts
                if runtime_receipt_is_effective(directory, task_id, receipt)
            ]
        automatic_frontier = (
            agent == "all"
            and role is None
            and submission_dependencies.get("schema_version") == "valp-submission-dependencies.v2"
            and not manual_mode
        )
        if automatic_frontier:
            phases, dependency_errors = dependency_ready_frontier(
                submission_dependencies,
                phases,
                receipts,
                directory,
                task_id,
            )
            if not phases:
                detail = ", ".join(dependency_errors) or "no pending dependency-ready work items"
                raise SystemExit("Dispatch has no ready phase: " + detail)
            targets = list(dict.fromkeys(target for target, _ in phases))
        else:
            dependency_errors = unmet_dependencies_for_phases(
                submission_dependencies,
                phases,
                receipts,
                directory,
                read_json(directory / "evidence-status.json"),
                manual_mode=manual_mode,
                correction_cycle=read_json(directory / "correction-cycle.json"),
            )
            if dependency_errors:
                raise SystemExit("Dispatch blocked by unmet prerequisites: " + ", ".join(dependency_errors))
            if submit and not recover_incomplete:
                existing_events = [
                    (phase, latest_phase_delivery_event(
                        submission_dependencies,
                        phase,
                        receipts,
                        task_id,
                    ))
                    for phase in phases
                ]
                incomplete = [
                    f"{phase[1]}:{phase[0]}"
                    for phase, event in existing_events
                    if event in DELIVERY_RECEIPT_EVENTS
                ]
                if incomplete:
                    raise SystemExit(
                        "Resubmitting an incomplete work item requires --recover-incomplete: "
                        + ", ".join(incomplete)
                    )
                terminal = [
                    f"{phase[1]}:{phase[0]}"
                    for phase, event in existing_events
                    if event in TERMINAL_WORKER_RECEIPT_EVENTS
                ]
                if terminal:
                    raise SystemExit(
                        "Dispatch phase already has a terminal receipt: " + ", ".join(terminal)
                    )
        if recover_incomplete:
            if len(phases) != 1:
                raise SystemExit("Incomplete submission recovery requires one exact dispatch phase")
            recovery_context = incomplete_submission_recovery_context(
                directory,
                task_id,
                routing,
                submission_dependencies,
                receipts,
                phases[0],
                int(retry_generation or 0),
                runtime_kind,
            )
    if submit and read_json(directory / "automation-policy.json").get("selected_action") == "block_for_approval":
        raise SystemExit("Dispatch is blocked until approval evidence and automation policy are reconciled")
    if recovery_context and recovery_context["action"] == "reconcile_completion":
        completion_receipt = write_herdr_completion_receipt(
            directory,
            task_id,
            recovery_context["originating_submission"],
            recovery_context["existing_refs"],
        )
        append_timeline_event(
            directory,
            "dispatch_completed",
            f"Reconciled late expected evidence for {phases[0][0]}",
            agent=phases[0][0],
            evidence_refs=recovery_context["existing_refs"],
            submission_receipt_id=recovery_context["originating_submission"]["receipt_id"],
            completion_receipt_id=completion_receipt["receipt_id"],
        )
        return [f"Reconciled late expected evidence for {phases[0][1]}:{phases[0][0]}"]
    launch_replacement_retry = bool(
        submit
        and replace_owned_session_launch
        and runtime_kind == "herdr"
        and owned_session_launch_replacement_pending(directory, state, phases)
    )
    done_session_reprovision_retry = bool(
        submit
        and reprovision_done_session
        and runtime_kind == "herdr"
        and len(phases) == 1
        and state.get("status") == "dispatching"
        and read_json(directory / "iteration-budget.json").get("stop_reason")
        == "runtime dispatch retry exhausted"
    )
    runtime_retry = bool(
        submit
        and (
            runtime_dispatch_retry_pending(directory, state, runtime_kind, phases)
            or launch_replacement_retry
            or done_session_reprovision_retry
        )
    )
    runtime_retry_reason = (
        read_json(directory / "iteration-budget.json").get("stop_reason")
        if runtime_retry
        else None
    )
    if submit and not runtime_retry:
        enforce_iteration_budget(directory, routing, state, phases)
    if submit:
        enforce_cost_budget(directory, task_id)

    recorded_budgets = routing.get("dispatch_payload_budgets") or {}
    for target in targets:
        dispatch_ref = directory / "agents" / target / "dispatch.md"
        if not dispatch_ref.exists():
            raise SystemExit(f"Missing dispatch for agent {target}: {dispatch_ref}")
        budget = recorded_budgets.get(target) or dispatch_budget_for_agent(target, role_assignments)
        dispatch_text = dispatch_ref.read_text(encoding="utf-8")
        actual_chars = len(dispatch_text)
        actual_reference_tokens = (actual_chars + 3) // 4
        if (
            actual_chars > int(budget["max_chars"])
            or actual_reference_tokens > int(budget["max_reference_tokens"])
        ):
            raise SystemExit(
                f"Dispatch for {target} exceeds role budget: "
                f"chars={actual_chars}/{budget['max_chars']} "
                f"reference_tokens={actual_reference_tokens}/{budget['max_reference_tokens']}"
            )
    if manual_mode and submit:
        raise SystemExit("Manual Mode cannot use --submit. Copy dispatches manually and record ordered manual attestations.")

    capabilities = load_dispatch_capabilities(root, directory, routing, state)
    capability_agents = capabilities.get("agents") or {}
    launch_argv_by_agent = capability_runtime_argv_by_agent(
        capability_agents,
        "launch_argv",
    )
    version_command_by_agent = capability_runtime_argv_by_agent(
        capability_agents,
        "version_command",
    )
    session_projection: dict[str, Any] = {}
    session_bindings: dict[str, Any] | None = None
    if submit and runtime_kind == "herdr":
        try:
            session_projection = ensure_herdr_agent_sessions(
                root,
                directory,
                task_id,
                targets,
                capabilities,
                allow_launch_argv_change=replace_owned_session_launch,
                allow_done_session_reprovision=reprovision_done_session,
            )
            session_bindings = targeted_session_bindings(session_projection, targets)
        except HerdrSubmissionError as exc:
            if str(exc) == DONE_SESSION_REPROVISION_REQUIRED:
                append_timeline_event(
                    directory,
                    "agent_session_reprovision_required",
                    str(exc),
                    work_item_ids=[f"{target_role}:{target}" for target, target_role in phases],
                    attempt="precondition",
                )
                raise SystemExit(f"HERDR task-owned session provisioning failed: {exc}") from exc
            write_json(
                directory / "agent-session-block.json",
                {
                    "schema_version": "valp-agent-session-block.v1",
                    "task_id": task_id,
                    "status": "blocked",
                    "reason": str(exc),
                    "recorded_at": now_iso(),
                },
            )
            budget = read_json(directory / "iteration-budget.json")
            if budget:
                budget["status"] = "blocked"
                budget["stop_reason"] = (
                    "runtime dispatch retry exhausted"
                    if runtime_retry
                    else "runtime dispatch failure"
                )
                write_json(directory / "iteration-budget.json", budget)
            append_timeline_event(
                directory,
                "agent_session_provision_failed",
                str(exc),
                work_item_ids=[f"{target_role}:{target}" for target, target_role in phases],
                attempt="retry" if runtime_retry else "initial",
            )
            raise SystemExit(f"HERDR task-owned session provisioning failed: {exc}") from exc
    if submit:
        preflight = collect_runtime_preflight(
            targets,
            runtime=runtime_kind,
            session_bindings=session_bindings,
            launch_argv_by_agent=launch_argv_by_agent,
            version_command_by_agent=version_command_by_agent,
        )
    else:
        preflight = (
            (routing.get("runtime_adapter") or {}).get("preflight")
            or read_json(directory / "runtime-preflight.json")
        )
    bootstrap_failed = False
    bootstrap_succeeded = False
    if submit and runtime_kind == "herdr" and session_bindings:
        for target in targets:
            binding = session_bindings.get(target)
            agent_preflight = ((preflight.get("agents") or {}).get(target) or {})
            readiness = agent_preflight.get("readiness") or {}
            model_probe = agent_preflight.get("model_probe") or {}
            needs_codex_session_bootstrap = (
                target == "codex" and readiness.get("ready") is not True
            )
            needs_model_observation_bootstrap = (
                target in {"claude", "hermes"}
                and model_probe.get("status") == "unsupported"
                and (
                    target == "hermes"
                    or readiness.get("ready") is True
                )
            )
            if (
                not isinstance(binding, dict)
                or binding.get("lifecycle") != "provisioned"
                or not (
                    needs_codex_session_bootstrap
                    or needs_model_observation_bootstrap
                )
            ):
                continue
            try:
                session_projection = bootstrap_task_owned_herdr_session(
                    directory,
                    task_id,
                    target,
                    binding,
                    herdr=activated_herdr_executable() or "herdr",
                    timeout_seconds=max(
                        60.0,
                        min(float(wait_seconds or 30.0), 300.0),
                    ),
                )
                session_bindings = targeted_session_bindings(session_projection, targets)
                bootstrap_succeeded = True
            except HerdrSubmissionError as exc:
                bootstrap_failed = True
                agent_record = (preflight.get("agents") or {}).get(target)
                if isinstance(agent_record, dict):
                    agent_record["status"] = "fail"
                    agent_record["bootstrap_probe"] = {
                        "status": "blocked",
                        "reason": str(exc),
                    }
                preflight["status"] = "fail"
    if bootstrap_succeeded:
        preflight = collect_runtime_preflight(
            targets,
            runtime=runtime_kind,
            session_bindings=session_bindings,
            launch_argv_by_agent=launch_argv_by_agent,
            version_command_by_agent=version_command_by_agent,
        )
    if not bootstrap_failed and any(
        isinstance(binding, dict) and binding.get("lifecycle") == "provisioned"
        for binding in (session_bindings or {}).values()
    ):
        preflight = await_owned_session_model_preflight(
            targets,
            runtime_kind,
            session_bindings or {},
            preflight,
            version_command_by_agent=version_command_by_agent,
        )
    if session_projection:
        previous_preflight = (
            (routing.get("runtime_adapter") or {}).get("preflight")
            or read_json(directory / "runtime-preflight.json")
        )
        preflight = merge_task_owned_runtime_preflight(
            previous_preflight,
            preflight,
            selected_agents,
            task_id,
        )
    failed = [
        name
        for name, record in (preflight.get("agents") or {}).items()
        if record.get("status") == "fail"
    ]
    if submit and (preflight.get("status") == "fail" or failed):
        write_json(directory / "runtime-preflight.json", preflight)
        budget = read_json(directory / "iteration-budget.json")
        if budget:
            budget["status"] = "blocked"
            budget["stop_reason"] = (
                "runtime dispatch retry exhausted"
                if runtime_retry
                else "runtime dispatch failure"
            )
            write_json(directory / "iteration-budget.json", budget)
        target_summary = ", ".join(failed) if failed else "runtime checks"
        append_timeline_event(
            directory,
            "runtime_preflight_failed",
            f"Runtime preflight failed for: {target_summary}",
            work_item_ids=[f"{target_role}:{target}" for target, target_role in phases],
            attempt="retry" if runtime_retry else "initial",
        )
        raise SystemExit("Runtime preflight failed for: " + target_summary)
    if submit:
        write_json(directory / "runtime-preflight.json", preflight)
    model_dispatch_errors = (
        dynamic_model_dispatch_errors(
            routing,
            (capabilities.get("agents") or {}),
            load_local_overlay(root),
            preflight,
            phases,
            allow_session_rebinding=bool(session_bindings),
        )
        if submit
        else []
    )
    if model_dispatch_errors:
        readiness_pending: list[str] = []
        for target, _target_role in phases:
            agent_preflight = ((preflight.get("agents") or {}).get(target) or {})
            probe = agent_preflight.get("model_probe") or {}
            if (
                probe.get("status") != "observed"
                or (probe.get("session_identity") or {}).get("status") != "known"
            ):
                readiness_pending.append(target)
        block_reason = (
            "owned_session_model_readiness_timeout"
            if readiness_pending
            else "dynamic_model_identity_mismatch"
        )
        write_json(
            directory / "model-identity-dispatch-block.json",
            {
                "schema_version": "valp-model-identity-dispatch-block.v1",
                "task_id": task_id,
                "status": "blocked",
                "reason": block_reason,
                "errors": model_dispatch_errors,
                "runtime_preflight_ref": "runtime-preflight.json",
                "recorded_at": now_iso(),
            },
        )
        budget = read_json(directory / "iteration-budget.json")
        if budget:
            budget["status"] = "blocked"
            budget["stop_reason"] = (
                "runtime dispatch retry exhausted"
                if runtime_retry
                else (
                    "owned session model readiness pending"
                    if readiness_pending
                    else "dynamic model identity changed after routing"
                )
            )
            write_json(directory / "iteration-budget.json", budget)
        raise SystemExit("Dispatch blocked by dynamic model identity gate: " + "; ".join(model_dispatch_errors))
    if session_projection:
        session_marker = {
            "status": "ready",
            "ref": "agent-sessions.json",
            "receipts_ref": "agent-session-receipts.jsonl",
        }
        routing["agent_sessions"] = session_marker
        routing.setdefault("runtime_adapter", {})["preflight"] = preflight
        routing["provider_matrix"] = provider_matrix_for(
            selected_agents,
            capabilities.get("agents") or {},
            load_local_overlay(root),
            preflight,
            evaluated_at=now_iso(),
            dynamic_discovery_required=True,
        )
        write_json(directory / "routing.json", routing)
        state["agent_sessions"] = session_marker
        state["runtime_adapter"] = routing["runtime_adapter"]
        state["provider_matrix"] = {"status": "scanned", "ref": "routing.json"}
        state["updated_at"] = now_iso()
        write_json(directory / "state.json", state)
    if runtime_retry:
        resume_runtime_dispatch_retry(
            directory,
            routing,
            phases,
            expected_stop_reason=runtime_retry_reason,
        )
    if submit and runtime_kind in {"herdr", "langgraph", "queue"}:
        write_wait_policy_for_phases(
            directory,
            task_id,
            phases,
            submission_dependencies,
        )
    commands = []
    for target, target_role in phases:
        expected = role_expected_refs(target, target_role)
        if manual_mode:
            expected_text = ", ".join(expected) if expected else "task-local evidence"
            commands.append(
                f"Manual Mode: phase={target_role}; copy agents/{target}/dispatch.md to {target}; "
                f"expected evidence: {expected_text}; attest ordering from submission-dependencies.json"
            )
            continue
        if runtime_kind == "queue":
            expected_text = ", ".join(expected) if expected else "task-local evidence"
            commands.append(
                f"VALP Queue Mode: phase={target_role}; enqueue agents/{target}/dispatch.md for {target}; "
                f"expected evidence: {expected_text}"
            )
            if submit:
                write_queue_submission(directory, task_id, target, target_role, expected)
            continue
        if runtime_kind == "langgraph":
            from .langgraph_adapter import submit_langgraph_run

            command = [
                "valp",
                "adapter",
                "langgraph",
                "run",
                task_id,
                "--workspace",
                str(root),
                "--agent",
                target,
                "--role",
                target_role,
                "--graph-id",
                target,
            ]
            if wait_seconds is not None:
                command.extend(["--wait-seconds", str(wait_seconds)])
            commands.append(shlex.join(command))
            if submit:
                result = submit_langgraph_run(
                    root,
                    task_id,
                    target,
                    target_role,
                    graph_id=target,
                    input_data={"attempt": "initial"},
                    expected_refs=expected,
                    wait_seconds=30.0 if wait_seconds is None else wait_seconds,
                )
                if result["status"] == "blocked":
                    raise SystemExit(f"LangGraph run blocked by VALP evidence gate: {result['run_ref']}")
            continue
        if runtime_kind != "herdr":
            raise SystemExit(f"Runtime {runtime_kind} is not supported by this reference dispatch helper.")
        capability = (preflight.get("checks") or {}).get("submission_transport") or {}
        dispatch_ref = f"agents/{target}/dispatch.md"
        command_description = describe_herdr_submission(capability, target, dispatch_ref)
        submission_only = wait_seconds == 0
        commands.append(command_description)
        if submit:
            herdr = shutil.which("herdr")
            if not herdr:
                raise SystemExit("HERDR submission became unavailable after preflight: herdr command not found")
            pane_id = str(((preflight.get("agents") or {}).get(target) or {}).get("pane_id") or "")
            if not pane_id:
                raise SystemExit(f"HERDR submission has no addressable pane for agent {target}")
            baseline = expected_evidence_snapshot(directory, expected)
            try:
                proof = submit_herdr_dispatch(
                    herdr,
                    capability,
                    task_id=task_id,
                    target=target,
                    pane_id=pane_id,
                    dispatch_path=directory / dispatch_ref,
                    run_command=run_command,
                    proof_seconds=proof_seconds,
                    session_binding=(session_bindings or {}).get(target),
                )
                if proof.get("proof_class") == "transport_only":
                    transport_receipt = write_herdr_transport_receipt(
                        directory,
                        task_id,
                        target,
                        target_role,
                        expected,
                        proof,
                    )
                    append_timeline_event(
                        directory,
                        "dispatch_inserted",
                        "HERDR transport inserted the dispatch without independent Agent invocation proof",
                        agent=target,
                        role=target_role,
                        receipt_id=transport_receipt["receipt_id"],
                        mode="manual_degraded",
                    )
                    raise HerdrSubmissionError(
                        "HERDR pane transport is Manual-degraded and cannot produce dispatch_submitted"
                    )
                submission_receipt = write_herdr_submission_receipt(
                    directory,
                    task_id,
                    target,
                    target_role,
                    expected,
                    proof,
                    recovery=recovery_context,
                    expected_evidence_baseline=baseline,
                )
                if recovery_context:
                    append_timeline_event(
                        directory,
                        "incomplete_submission_retried",
                        "Resubmitted one identity-bound incomplete work item",
                        work_item_id=recovery_context["work_item_id"],
                        retry_generation=recovery_context["retry_generation"],
                        originating_submission_receipt_id=recovery_context[
                            "originating_submission_receipt_id"
                        ],
                        recovery_submission_receipt_id=submission_receipt["receipt_id"],
                    )
                if expected and not submission_only:
                    completed, existing_refs, missing_refs = wait_for_herdr_expected_refs(
                        directory,
                        expected,
                        wait_seconds,
                        baseline,
                    )
                    if completed:
                        terminal_proof = None
                        if (directory / "runtime" / "herdr" / "adoption.json").is_file():
                            terminal_proof = observe_herdr_terminal(
                                herdr,
                                task_id=task_id,
                                target=target,
                                pane_id=pane_id,
                                submission_proof=proof,
                                run_command=run_command,
                                timeout_seconds=max(proof_seconds, 5.0),
                            )
                        write_herdr_completion_receipt(
                            directory,
                            task_id,
                            submission_receipt,
                            existing_refs,
                            terminal_proof,
                        )
                        append_timeline_event(
                            directory,
                            "dispatch_completed",
                            f"Expected evidence completed for {target}",
                            agent=target,
                            evidence_refs=existing_refs,
                        )
                    else:
                        append_timeline_event(
                            directory,
                            "dispatch_waiting",
                            f"Evidence observation window elapsed for {target}; worker remains active",
                            agent=target,
                            missing_refs=missing_refs,
                        )
            except HerdrSubmissionError as exc:
                budget = read_json(directory / "iteration-budget.json")
                if budget:
                    budget["status"] = "blocked"
                    if recovery_context:
                        budget["stop_reason"] = "incomplete submission recovery failed"
                    else:
                        budget["stop_reason"] = (
                            "runtime dispatch retry exhausted"
                            if runtime_retry
                            else "runtime dispatch failure"
                        )
                    write_json(directory / "iteration-budget.json", budget)
                append_timeline_event(
                    directory,
                    "dispatch_submit_failed",
                    str(exc),
                    agent=target,
                    role=target_role,
                    work_item_id=f"{target_role}:{target}",
                    attempt="retry" if runtime_retry else "initial",
                )
                raise SystemExit(str(exc)) from exc
    if submit:
        budget = read_json(directory / "iteration-budget.json")
        if budget:
            refresh_iteration_budget(directory, routing, budget)
    return commands
