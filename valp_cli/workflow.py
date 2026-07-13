from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .delegation import build_delegation_policy, validate_delegation_policy
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

UI_ATTENTION_PROFILES = {"apple-app", "web-frontend", "prototype"}

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

DEFAULT_MIN_TERMINAL_SIZE = {"width": 60, "height": 20}
AGENT_MIN_TERMINAL_SIZE = {
    "agy": {"width": 70, "height": 24},
}
RUNTIME_CHOICES = {"auto", "manual", "herdr", "queue"}
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
    receipts = read_json_lines_strict(directory / "dispatch-receipts.jsonl")
    errors = deterministic_receipt_ledger_errors(receipts, task_id)
    if errors:
        raise SystemExit("Invalid dispatch receipt ledger: " + "; ".join(errors[:5]))
    return receipts


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
        "stdout": completed.stdout[:stdout_limit],
        "stderr": completed.stderr[:stderr_limit],
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
    runtime_class = str(runtime.get("class") or "").lower()
    runtime_name = str(runtime.get("name") or "").lower()
    if runtime_class == "manual" or runtime_name == "manual":
        return "manual"
    if runtime_class == "daemon_queue":
        return "queue"
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


def load_local_overlay(root: Path | None = None) -> dict[str, Any]:
    return read_json(local_overlay_path(root))


def scan_workspace(root: Path, task_id: str | None = None, runtime: str | None = None) -> dict[str, Any]:
    root = workspace_root(root)
    capabilities_path = local_capabilities_path(root)
    overlay_path = local_overlay_path(root)
    capabilities = load_local_capabilities(root)
    overlay = load_local_overlay(root)
    capabilities["runtime_preflight"] = collect_runtime_preflight(list((capabilities.get("agents") or {}).keys()), runtime=runtime)
    capabilities["last_valp_scan_at"] = now_iso()
    capabilities["capabilities_source_ref"] = str(capabilities_path) if read_json(capabilities_path) else None
    capabilities["local_overlay_ref"] = str(overlay_path) if overlay else None
    write_json(root / ".herdr-loop" / "agents" / "capabilities.json", capabilities)
    if overlay:
        write_json(root / ".herdr-loop" / "local-overlay.json", overlay)
    if task_id:
        state_path = task_dir(root, task_id) / "state.json"
        state = read_json(state_path)
        if state:
            state["status"] = "scanning_capabilities"
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
    path = Path(ref)
    return bool(ref) and not path.is_absolute() and "\\" not in ref and ".." not in path.parts


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
) -> dict[str, Any]:
    selected_refs = [str(item.get("path")) for item in (context_selection.get("selected") or []) if item.get("path")]
    items: list[dict[str, Any]] = [
        {
            "section": "project",
            "summary": "Load the active task and project operating rules from selected task-local refs; do not rely on hidden chat context.",
            "evidence_refs": [ref for ref in selected_refs if ref.endswith("task.md") or ref == "AGENTS.md"][:4],
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
            "claim": "selected agents have visible dispatches",
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
) -> dict[str, Any]:
    heads: dict[str, Any] = {}
    for head, role in ATTENTION_HEAD_ROLES.items():
        selected = role_assignments.get(role)
        score = candidate_scores.get(selected or "", {}).get("overall")
        heads[head] = {
            "selected": selected,
            "candidate": f"role:{role}",
            "score": score,
            "status": "selected" if selected in selected_agents else "not_selected",
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
    feedback_history: list[dict[str, Any]],
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
    )
    mask_list = mask_list_for(profile, loop_layer, design_contract)
    evidence_board = evidence_board_for(profile, loop_layer, selected_agents, design_contract)
    heads = attention_heads_for(loop_layer, profile, selected_agents, candidate_scores, design_contract, role_assignments)
    attention_map = {
        "schema_version": "valp-visible-attention-map.v1",
        "task_id": task_id,
        "profile": profile,
        "loop_layer": loop_layer,
        "generated_at": now_iso(),
        "heads": heads,
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
    route: bool = True,
    runtime: str | None = None,
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
Mode: Selected during routing

## Goal

{prompt}

## Expected Evidence

Generated during routing.

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
    if route:
        scan_workspace(root, task_id, runtime=runtime)
        route_task(root, task_id, runtime=runtime)
    return directory


def route_task(root: Path, task_id: str, runtime: str | None = None) -> dict[str, Any]:
    root = workspace_root(root)
    normalize_runtime(runtime)
    directory = task_dir(root, task_id)
    state_path = directory / "state.json"
    state = read_json(state_path)
    if not state:
        raise SystemExit(f"Missing state.json for task {task_id}")
    prompt = (directory / "task.md").read_text(encoding="utf-8", errors="replace")
    profile = state.get("profile") or classify_profile(prompt)
    approval_risks = (state.get("risk") or {}).get("matches") or classify_approval_risks(extract_goal_text(prompt))
    if approval_risks and (state.get("gates") or {}).get("approval") in {None, "not_required"}:
        state.setdefault("gates", {})["approval"] = "needs_approval"
    if approval_risks and not state.get("approval_required"):
        state["approval_required"] = approval_risks
    state["risk"] = {
        "approval_required": bool(approval_risks),
        "matches": approval_risks,
    }
    capabilities = scan_workspace(root, task_id, runtime=runtime)
    overlay = load_local_overlay(root)
    agents = capabilities.get("agents") or {}
    feedback_history = load_routing_feedback_history(root)
    candidate_scores = score_candidates(profile, agents, feedback_history)
    selected_agents = select_agents(profile, agents, candidate_scores)
    role_assignments = role_assignments_for(profile, selected_agents, agents, candidate_scores)
    preflight = collect_runtime_preflight(selected_agents, runtime=runtime)
    runtime_adapter = runtime_adapter_record(preflight, runtime=runtime)
    automation_policy = automation_policy_for(task_id, runtime_adapter, approval_risks)
    write_json(directory / "automation-policy.json", automation_policy)
    skill_recommendations = run_skill_recommendations(root, task_id, profile, prompt)
    skill_recommendations = add_per_agent_skill_recommendations(skill_recommendations, selected_agents)
    write_json(directory / "skill-recommendations.json", skill_recommendations)
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
        feedback_history,
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
        "role_requirements": PROFILE_ROLE_REQUIREMENTS.get(profile, PROFILE_ROLE_REQUIREMENTS["generic-analysis"]),
        "role_assignments": role_assignments,
        "submission_dependencies": {
            "status": "recorded",
            "ref": "submission-dependencies.json",
        },
        "delegation_policy": {
            "status": "recorded",
            "ref": "delegation-policy.json",
        },
        "coordinator_selection": {
            "selected_agent": role_assignments.get("coordinator"),
            "selection_rule": "Selected from current capability evidence, local overlay hints, runtime availability, context policy, and task profile. The open protocol does not name a universal leader.",
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
        },
        "visible_attention": {
            "schema_version": "valp-visible-attention.v1",
            "status": "recorded",
            "loop_layer": visible_attention["loop_layer"],
            "design_contract": visible_attention["design_contract"],
            **visible_attention["refs"],
        },
        "provider_matrix": provider_matrix_for(selected_agents, agents, overlay, preflight),
        "runtime_task_state_mapping": RUNTIME_TASK_STATE_MAPPING,
        "squad_routing": {"used": False},
        "routing_feedback_ref": "routing-feedback.json",
        "learning_feedback_ref": "learning-feedback.json",
        "capabilities_missing": [],
    }
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
    append_dispatch_written_receipts(directory, selected_agents, expected_by_agent)
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
            "submission_dependencies": routing["submission_dependencies"],
            "delegation_policy": routing["delegation_policy"],
            "capabilities_needed": routing["capabilities_needed"],
            "capabilities_missing": [],
            "context_policies": context_policies,
            "automation_policy": {
                "status": "recorded",
                "ref": "automation-policy.json",
                "selected_action": automation_policy.get("selected_action"),
                "audit_grade": automation_policy.get("audit_grade"),
            },
            "context_pack": {"status": "recorded", "ref": "context-pack.json"},
            "dispatch_payload_budgets": dispatch_payload_budgets,
            "skill_recommendations": {
                "status": skill_recommendations.get("status"),
                "backend": skill_recommendations.get("backend"),
                "ref": "skill-recommendations.json",
            },
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
    write_json(state_path, state)
    return routing


def score_candidates(profile: str, agents: dict[str, Any], feedback_history: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    required_roles = PROFILE_ROLE_REQUIREMENTS.get(profile, PROFILE_ROLE_REQUIREMENTS["generic-analysis"])
    history = feedback_history or []
    scores: dict[str, dict[str, Any]] = {}
    for agent, info in agents.items():
        active = bool(info.get("active", True))
        runtime = info.get("runtime") or {}
        runtime_status = str(runtime.get("status", "unknown"))
        role_fit = {role: role_fit_score(info, role) for role in required_roles}
        profile_fit = max(role_fit.values()) if role_fit else 0.45
        tool_fit = 0.85 if info.get("mcp_servers") or runtime else 0.55
        skill_count = len(info.get("skills") or [])
        skill_fit = min(0.95, 0.45 + skill_count / 80)
        permission_fit = 0 if not active else 1
        context_fit = 0.85
        feedback_prior = feedback_prior_for_agent(agent, profile, history)
        evidence_history = feedback_prior["score"]
        availability = 1 if runtime_status == "idle" else 0.75 if runtime_status in {"working", "focused"} else 0.65
        if not active:
            availability = 0
        risk_fit = 0.9
        values = [profile_fit, tool_fit, skill_fit, permission_fit, context_fit, evidence_history, availability, risk_fit]
        overall = round(sum(values) / len(values), 2)
        confidence = "high" if overall >= 0.75 else "medium" if overall >= 0.55 else "low"
        scores[agent] = {
            "profile_fit": round(profile_fit, 2),
            "tool_fit": round(tool_fit, 2),
            "skill_fit": round(skill_fit, 2),
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
        }
    return scores


def select_agents(profile: str, agents: dict[str, Any], scores: dict[str, dict[str, Any]]) -> list[str]:
    required_roles = PROFILE_ROLE_REQUIREMENTS.get(profile, PROFILE_ROLE_REQUIREMENTS["generic-analysis"])
    selected: list[str] = []
    for role in required_roles:
        ranked = sorted(
            scores,
            key=lambda name: (scores[name].get("role_fit", {}).get(role, 0), scores[name].get("overall", 0)),
            reverse=True,
        )
        for agent in ranked:
            role_score = scores[agent].get("role_fit", {}).get(role, 0)
            if scores[agent].get("overall", 0) < 0.5 and selected:
                continue
            if role_score < 0.35 and len(agents) > 1:
                continue
            if agent not in selected:
                selected.append(agent)
            break
    if selected:
        return selected
    ranked = sorted(scores, key=lambda name: scores[name].get("overall", 0), reverse=True)
    return ranked[:1]


def role_assignments_for(
    profile: str,
    selected_agents: list[str],
    agents: dict[str, Any],
    scores: dict[str, dict[str, Any]],
) -> dict[str, str]:
    assignments: dict[str, str] = {}
    required_roles = PROFILE_ROLE_REQUIREMENTS.get(profile, PROFILE_ROLE_REQUIREMENTS["generic-analysis"])
    for role in required_roles:
        ranked = sorted(
            selected_agents,
            key=lambda name: (scores.get(name, {}).get("role_fit", {}).get(role, 0), scores.get(name, {}).get("overall", 0)),
            reverse=True,
        )
        if ranked:
            assignments[role] = ranked[0]
    return assignments


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
        reasons.append(f"candidate selected for {profile}")
    return reasons


def routing_confidence(scores: dict[str, dict[str, Any]], selected_agents: list[str]) -> dict[str, Any]:
    if not selected_agents:
        return {"overall": "low", "reason": "No selected agents."}
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
                    "reason": "Not selected because selected role candidates had stronger current capability evidence.",
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
            "class": "daemon_queue",
            "name": "VALP headless queue",
            "full_mode_capable": True,
            "state_mapping_ref": "docs/task-state-machine.md",
            "preflight": preflight,
        }
    if runtime_kind == "herdr":
        return {
            "class": "pane_controller",
            "name": "HERDR",
            "full_mode_capable": bool(shutil.which("herdr")) and preflight.get("status") != "fail",
            "state_mapping_ref": "docs/task-state-machine.md",
            "preflight": preflight,
        }
    return {
        "class": "manual",
        "name": "manual",
        "full_mode_capable": False,
        "state_mapping_ref": "docs/task-state-machine.md",
        "preflight": preflight,
    }


def collect_runtime_preflight(agent_names: list[str] | None = None, runtime: str | None = None) -> dict[str, Any]:
    runtime_kind = resolve_runtime(runtime)
    if runtime_kind == "manual":
        return collect_manual_preflight(agent_names)
    if runtime_kind == "queue":
        return collect_queue_preflight(agent_names)
    return collect_herdr_preflight(agent_names)


def collect_manual_preflight(agent_names: list[str] | None = None) -> dict[str, Any]:
    agents = {
        agent: {
            "status": "not_applicable",
            "session_status": "manual",
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


def collect_herdr_preflight(agent_names: list[str] | None = None) -> dict[str, Any]:
    herdr = shutil.which("herdr")
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

    pane_result = run_command([herdr, "pane", "list"], timeout=5.0)
    pane_json = parse_json_stdout(pane_result)
    panes = (((pane_json.get("result") or {}).get("panes")) or []) if pane_json else []
    preflight["checks"]["pane_list"] = {
        "status": "pass" if pane_result.get("ok") else "fail",
        "count": len(panes),
    }

    panes_by_agent = {
        str(pane.get("agent")): pane
        for pane in panes
        if pane.get("agent")
    }
    for agent in agent_names or sorted(panes_by_agent):
        pane = panes_by_agent.get(agent)
        agent_record = {
            "status": "warn",
            "agent_status": None,
            "pane_id": None,
            "terminal_size": None,
            "min_terminal_size": AGENT_MIN_TERMINAL_SIZE.get(agent, DEFAULT_MIN_TERMINAL_SIZE),
            "terminal_size_status": "unknown",
            "cli": cli_preflight_for_agent(agent),
            "notes": [],
        }
        if not pane:
            agent_record["status"] = "warn"
            agent_record["notes"].append("No current pane was reported for this agent.")
            preflight["agents"][agent] = agent_record
            continue

        pane_id = str(pane.get("pane_id"))
        agent_record["pane_id"] = pane_id
        agent_record["agent_status"] = pane.get("agent_status")
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
        if terminal_status == "fail" or cli_status == "fail":
            agent_record["status"] = "fail"
        elif terminal_status == "unknown" or cli_status == "warn":
            agent_record["status"] = "warn"
        else:
            agent_record["status"] = "pass"
        preflight["agents"][agent] = agent_record

    if any(isinstance(check, dict) and check.get("status") == "fail" for check in preflight["checks"].values()):
        preflight["status"] = "fail"
    elif any(record.get("status") == "fail" for record in preflight["agents"].values()):
        preflight["status"] = "fail"
    elif any(record.get("status") == "warn" for record in preflight["agents"].values()):
        preflight["status"] = "warn"
    return preflight


def pane_rect_from_layout(layout_json: dict[str, Any], pane_id: str) -> dict[str, Any]:
    layout = (layout_json.get("result") or {}).get("layout") or {}
    for pane in layout.get("panes") or []:
        if pane.get("pane_id") == pane_id and isinstance(pane.get("rect"), dict):
            return pane["rect"]
    return {}


def cli_preflight_for_agent(agent: str) -> dict[str, Any]:
    command_by_agent = {
        "agy": ["agy", "--version"],
        "claude": ["claude", "--version"],
        "codex": ["codex", "--version"],
        "hermes": ["hermes", "--version"],
    }
    command = command_by_agent.get(agent)
    if not command:
        return {"status": "warn", "message": "No CLI version probe is defined for this agent."}
    if not shutil.which(command[0]):
        return {"status": "warn", "command": command, "message": "CLI command was not found on PATH."}
    result = run_command(command, timeout=5.0)
    return {
        "status": "pass" if result.get("ok") else "fail",
        "command": command,
        "exit_code": result.get("exit_code"),
        "version_output": (result.get("stdout") or result.get("stderr") or "").strip()[:500],
    }


def provider_matrix_for(selected_agents: list[str], agents: dict[str, Any], overlay: dict[str, Any], preflight: dict[str, Any]) -> dict[str, Any]:
    overlay_profiles = overlay.get("agent_capability_profiles") or {}
    providers = {}
    for agent in selected_agents:
        info = agents.get(agent, {})
        overlay_profile = overlay_profiles.get(agent) or {}
        agent_preflight = (preflight.get("agents") or {}).get(agent, {})
        cli_record = agent_preflight.get("cli") or {}
        runtime_report = cli_record.get("version_output") or agent_preflight.get("worker_id") or agent_preflight.get("queue_id") or "unknown"
        cli_available = cli_record.get("status") in {"pass", "warn"} if cli_record else "unknown"
        providers[agent] = {
            "provider_name": agent,
            "provider_version_or_runtime_report": runtime_report,
            "cli_available": cli_available,
            "mcp_support": "supported" if info.get("mcp_servers") else "unknown",
            "skill_discovery_path": overlay_profile.get("skill_library_paths") or "unknown",
            "session_resume_support": "unknown",
            "approval_behavior": overlay_profile.get("approval_behavior") or "unknown",
            "model_selection": "runtime_default",
            "max_concurrency": 1,
            "context_policy": context_policy_for(agent, info, overlay),
            "known_limitations": info.get("must_not_do") or [],
            "runtime_preflight": agent_preflight,
            "last_verified_at": now_iso(),
        }
    return {"generated_at": now_iso(), "runtime_preflight": preflight, "providers": providers}


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
    core_task_refs = "\n".join(
        f"- `{relative_ref(directory / ref, root)}`"
        for ref in ["task.md", "context-pack.json", "skill-recommendations.json"]
    )
    payload_records: dict[str, dict[str, Any]] = {}
    for agent in selected_agents:
        agent_dir = directory / "agents" / agent
        agent_dir.mkdir(parents=True, exist_ok=True)
        expected = "\n".join(f"- `{ref}`" for ref in expected_by_agent.get(agent, []))
        exact_evidence = "\n".join(f"- `{relative_ref(directory / ref, root)}`" for ref in expected_by_agent.get(agent, []))
        reasons = bounded_text("; ".join(routing["agent_match_reasons"].get(agent, [])), 160)
        skills = format_skill_recommendations_for_dispatch(agent, skill_recommendations)
        compact_skill_lines = [
            line
            for line in skills.splitlines()
            if "Full recommendation records remain" in line
            or "Recommendations filtered for" in line
            or line.startswith("- Work item ")
        ]
        compact_skills = "\n".join(compact_skill_lines) or "- Full recommendation records: `skill-recommendations.json`."
        minimal_skills = "- Load provider-reachable matches from `skill-recommendations.json` only when relevant."
        attention_slice = attention_slice_for_agent(agent, visible_attention)
        budget = dispatch_budget_for_agent(agent, routing.get("role_assignments") or {})

        def render_dispatch(brief: str, attention: str, skill_text: str, actual_chars: int) -> str:
            return f"""# Dispatch: {agent}

Task: {task_id}
Profile: {profile}
Payload budget: role={budget['role']} max_chars={budget['max_chars']} max_reference_tokens={budget['max_reference_tokens']} actual_chars={actual_chars} estimator=ceil(chars/4)

## Project Root

```bash
cd "{root}"
```

## Role

Primary role: `{budget['role']}`. Capability match: {reasons or 'current routing evidence'}.

## Task Brief

{brief}

## Task References

The coordinator/leader owns dispatch precision; load these refs as needed:

{core_task_refs}
- Gate contracts: `submission-dependencies.json`, `delegation-policy.json`
- More refs: `automation-policy.json`, `routing.json`, `visible-routing.md`, `context-selection.json`, `mask-list.json`, `evidence-board.json`

## Payload Budget

- Expand only through task-local refs; do not request hidden chat history.

## Visible Attention Slice

{attention}

## Permission Boundary

- Honor approval gates; cite evidence for runtime facts.
- Do not write skills, plugins, memory, MCP configuration, or agent configuration while delegated.
- Scoped repository edits need permission and must not be live-loaded.
- Write expected evidence only unless source edits are permitted.

## Expected Evidence

{exact_evidence or expected}

## Recommended Skills

{skill_text}

## Evidence Claim Rule

- Cite task-local proof for build, test, and runtime claims.

## Required Response

Write expected evidence with blockers, confidence, and `## Recommendations`.
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
                "- See `visible-routing.md` and `context-pack.json` for the task-local role slice.",
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
            raise SystemExit(f"Dispatch payload exceeds role budget for {agent}")

        actual_chars = len(dispatch)
        payload_records[agent] = {
            **budget,
            "actual_chars": actual_chars,
            "actual_reference_tokens": (actual_chars + 3) // 4,
            "dispatch_ref": f"agents/{agent}/dispatch.md",
        }
        (agent_dir / "dispatch.md").write_text(dispatch, encoding="utf-8")
    return payload_records


def format_skill_recommendations_for_dispatch(agent: str, skill_recommendations: dict[str, Any]) -> str:
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
        "- Full recommendation records remain in `skill-recommendations.json`; dispatch carries short labels only.",
    ]
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


def append_dispatch_written_receipts(directory: Path, selected_agents: list[str], expected_by_agent: dict[str, list[str]]) -> None:
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
    queue_id = f"{task_id}-{target}-{role}"
    worker_id = f"worker-{target}-{role}"
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


def suspend_task(root: Path, task_id: str, timeout_seconds: float = 300.0) -> dict[str, Any]:
    root = workspace_root(root)
    directory = task_dir(root, task_id)
    state_path = directory / "state.json"
    if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
        raise SystemExit("Wait timeout must be a finite non-negative number")
    with task_state_lock(directory):
        state = recover_wait_projection(directory, read_json_strict(state_path))
        if state.get("status") == "suspended":
            return state.get("suspension") or {}
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
            "allowed_resume_events": sorted(SUSPENSION_RESUME_EVENTS),
        }
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
        event_details = {
            "resume_event": resume_event,
            "resume_ref": resume_ref,
            "wake_reason": wake_reason,
            "wake_id": wake_id,
            "result_ref": result_ref,
            "qualifying_receipt_id": qualifying_receipt_id,
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
) -> dict[str, Any]:
    if not math.isfinite(poll_interval_seconds) or poll_interval_seconds < 0:
        raise SystemExit("Poll interval must be a finite non-negative number")
    root = workspace_root(root)
    directory = task_dir(root, task_id)
    suspension = suspend_task(root, task_id, timeout_seconds=timeout_seconds)
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

        deadline_text = str(current.get("deadline_at") or "").replace("Z", "+00:00")
        deadline = datetime.fromisoformat(deadline_text)
        if datetime.now(timezone.utc) >= deadline:
            return resume_suspended_task(root, task_id, "timeout")

        time.sleep(max(0.01, poll_interval_seconds))


def dispatch_task(
    root: Path,
    task_id: str,
    agent: str = "all",
    submit: bool = False,
    runtime: str | None = None,
    role: str | None = None,
) -> list[str]:
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
        raise SystemExit("Agent is not selected for this task: " + ", ".join(unknown_targets))
    if not targets:
        raise SystemExit("No selected agent is assigned to the requested role")

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

    if submit and submission_dependencies:
        dependency_errors = unmet_dependencies_for_phases(
            submission_dependencies,
            phases,
            load_dispatch_receipts(directory, task_id),
            directory,
            read_json(directory / "evidence-status.json"),
            manual_mode=manual_mode,
        )
        if dependency_errors:
            raise SystemExit("Dispatch blocked by unmet prerequisites: " + ", ".join(dependency_errors))
    if submit and read_json(directory / "automation-policy.json").get("selected_action") == "block_for_approval":
        raise SystemExit("Dispatch is blocked until approval evidence and automation policy are reconciled")

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

    preflight = collect_runtime_preflight(targets, runtime=runtime_kind)
    failed = [
        name
        for name, record in (preflight.get("agents") or {}).items()
        if record.get("status") == "fail"
    ]
    if preflight.get("status") == "fail" or failed:
        write_json(directory / "runtime-preflight.json", preflight)
        target_summary = ", ".join(failed) if failed else "runtime checks"
        raise SystemExit("Runtime preflight failed for: " + target_summary)
    write_json(directory / "runtime-preflight.json", preflight)
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
        if runtime_kind != "herdr":
            raise SystemExit(f"Runtime {runtime_kind} is not supported by this reference dispatch helper.")
        command = ["herdr-loop", "--project-root", str(root), "submit-dispatch", task_id, target]
        for ref in expected:
            command.extend(["--expect", ref])
        commands.append(" ".join(command))
        if submit:
            subprocess.run(command, check=True)
    return commands
