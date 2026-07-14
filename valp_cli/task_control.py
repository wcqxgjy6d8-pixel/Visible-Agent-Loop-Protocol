from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .control_plane import ControlPlaneError, InstallationCore, _new_id, _state_digest, append_jsonl, read_json, safe_control_ref, utc_now, write_json


TASK_STATUSES = {
    "new", "published", "scanning_capabilities", "scanning_context", "loading_local_overlay",
    "selecting_runtime_adapter", "classifying_task", "selecting_profile", "decomposing_tasks",
    "recommending_skills", "building_provider_matrix", "scoring_routes", "routing_capabilities",
    "routing_squad", "dispatching", "suspended", "planned", "locked", "executing", "verifying",
    "reviewing", "resolving_agent_recommendations", "fixing", "approval_required", "recording",
    "done", "blocked", "failed", "cancelled",
}

TASK_TRANSITIONS = {
    "new": {"published", "blocked"},
    "published": {"scanning_capabilities", "blocked"},
    "scanning_capabilities": {"scanning_context", "blocked"},
    "scanning_context": {"loading_local_overlay", "blocked"},
    "loading_local_overlay": {"selecting_runtime_adapter", "blocked"},
    "selecting_runtime_adapter": {"classifying_task", "blocked"},
    "classifying_task": {"selecting_profile", "blocked"},
    "selecting_profile": {"decomposing_tasks", "blocked"},
    "decomposing_tasks": {"recommending_skills", "building_provider_matrix", "blocked"},
    "recommending_skills": {"building_provider_matrix", "blocked"},
    "building_provider_matrix": {"preflighting_runtime", "scoring_routes", "blocked"},
    "preflighting_runtime": {"scoring_routes", "blocked"},
    "scoring_routes": {"routing_capabilities", "blocked"},
    "routing_capabilities": {"routing_squad", "dispatching", "planned", "blocked"},
    "routing_squad": {"dispatching", "planned", "blocked"},
    "dispatching": {"suspended", "executing", "verifying", "blocked", "cancelled"},
    "suspended": {"executing", "verifying", "blocked", "cancelled"},
    "planned": {"locked", "dispatching", "blocked"},
    "locked": {"executing", "blocked", "cancelled"},
    "executing": {"verifying", "blocked", "cancelled"},
    "verifying": {"reviewing", "fixing", "approval_required", "recording", "blocked"},
    "reviewing": {"resolving_agent_recommendations", "fixing", "approval_required", "recording", "blocked"},
    "resolving_agent_recommendations": {"fixing", "approval_required", "recording", "blocked"},
    "fixing": {"dispatching", "executing", "verifying", "blocked", "cancelled"},
    "approval_required": {"recording", "blocked", "cancelled"},
    "recording": {"done", "blocked"},
    "done": {"blocked"},
    "blocked": {"published", "scanning_capabilities", "dispatching", "fixing", "executing", "verifying", "cancelled"},
    "failed": {"published", "blocked", "cancelled"},
    "cancelled": set(),
}

DONE_GATES = ("receipts", "expected_evidence", "verification", "review", "approvals", "final_synthesis", "audit")


def _task_dir(root: Path, task_id: str) -> Path:
    if not task_id or "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Task id must be a single safe path segment")
    return root / "tasks" / task_id


def _task_digest(state: dict[str, Any]) -> str:
    return _state_digest(state)


def _initial_task_state(installation_id: str, task_id: str) -> dict[str, Any]:
    state = {
        "schema_version": "valp-task-state.v1",
        "installation_id": installation_id,
        "task_id": task_id,
        "revision": 0,
        "status": "new",
        "active_blockers": [],
        "gates": {},
        "last_event_id": None,
        "last_event_digest": None,
        "protocol_version": "0.3.0-draft",
        "updated_at": utc_now(),
        "projection_digest": "",
    }
    state["projection_digest"] = _task_digest(state)
    return state


def init_task(root: Path, task_id: str) -> dict[str, Any]:
    core = InstallationCore(root)
    installation = core._installation()
    directory = _task_dir(root, task_id)
    state_path = directory / "task-state.json"
    if state_path.exists():
        return read_json(state_path)
    directory.mkdir(parents=True, exist_ok=True)
    state = _initial_task_state(installation["installation_id"], task_id)
    event = {
        "schema_version": "valp-task-event.v1",
        "event_id": _new_id("task-event"),
        "task_id": task_id,
        "installation_id": installation["installation_id"],
        "event_kind": "task_initialized",
        "prior_revision": 0,
        "new_revision": 0,
        "payload": {"state_projection": dict(state)},
        "event_digest": "",
    }
    event["event_digest"] = "sha256:" + hashlib.sha256((json.dumps({key: value for key, value in event.items() if key != "event_digest"}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest()
    state["last_event_id"] = event["event_id"]
    state["last_event_digest"] = event["event_digest"]
    state["projection_digest"] = _task_digest(state)
    append_jsonl(directory / "events.jsonl", event)
    write_json(state_path, state)
    return state


def task_state(root: Path, task_id: str) -> dict[str, Any]:
    directory = _task_dir(root, task_id)
    state = read_json(directory / "task-state.json")
    if state.get("projection_digest") != _task_digest(state):
        raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Task projection digest mismatch", state_effect="blocked")
    replay_task(root, task_id, persisted=state)
    return state


def replay_task(root: Path, task_id: str, *, persisted: dict[str, Any] | None = None) -> dict[str, Any]:
    directory = _task_dir(root, task_id)
    events = []
    event_path = directory / "events.jsonl"
    if event_path.exists():
        for line in event_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    previous_digest = None
    current = None
    for event in events:
        expected = "sha256:" + hashlib.sha256((json.dumps({key: value for key, value in event.items() if key != "event_digest"}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest()
        if event.get("prior_event_digest") != previous_digest and event.get("event_kind") != "task_initialized":
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Task event chain mismatch", state_effect="blocked")
        if event.get("event_digest") != expected:
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Task event digest mismatch", state_effect="blocked")
        projection = (event.get("payload") or {}).get("state_projection")
        if not isinstance(projection, dict):
            raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Task event has no projection", state_effect="blocked")
        current = dict(projection)
        current["last_event_id"] = event.get("event_id")
        current["last_event_digest"] = event.get("event_digest")
        current["projection_digest"] = _task_digest(current)
        previous_digest = event.get("event_digest")
    if current is None:
        raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Task has no initialization event", state_effect="blocked")
    saved = persisted or read_json(directory / "task-state.json")
    if saved.get("revision") != current.get("revision") or saved.get("status") != current.get("status") or saved.get("projection_digest") != current.get("projection_digest"):
        raise ControlPlaneError("VALP-E-REGISTRY-CONSISTENCY", "Task projection differs from event replay", state_effect="blocked")
    return current


def transition_task(root: Path, task_id: str, target_status: str, *, expected_revision: int | None = None, gates: dict[str, Any] | None = None, actor: str = "installation-leader") -> dict[str, Any]:
    core = InstallationCore(root)
    installation_state = core.state()
    if installation_state["status"] not in {"active", "degraded"}:
        raise ControlPlaneError("VALP-E-STATE-CONFLICT", "Task transitions require an active installation")
    directory = _task_dir(root, task_id)
    current = task_state(root, task_id)
    if target_status not in TASK_STATUSES or target_status not in TASK_TRANSITIONS.get(current["status"], set()):
        raise ControlPlaneError("VALP-E-STATE-TRANSITION", f"Illegal task transition {current['status']} -> {target_status}")
    if expected_revision is not None and expected_revision != current["revision"]:
        raise ControlPlaneError("VALP-E-STATE-CONFLICT", f"Expected task revision {expected_revision}, current is {current['revision']}")
    next_gates = dict(current.get("gates") or {})
    next_gates.update(gates or {})
    if target_status == "done":
        missing = [gate for gate in DONE_GATES if next_gates.get(gate) is not True]
        if missing:
            raise ControlPlaneError("VALP-E-EVIDENCE-MISSING", "Done gates unresolved: " + ", ".join(missing))
    next_state = dict(current)
    next_state.update({
        "revision": current["revision"] + 1,
        "status": target_status,
        "gates": next_gates,
        "active_blockers": [] if target_status != "blocked" else list(next_state.get("active_blockers") or [target_status]),
        "updated_at": utc_now(),
        "last_event_id": _new_id("task-event"),
    })
    next_state["projection_digest"] = _task_digest(next_state)
    event = {
        "schema_version": "valp-task-event.v1",
        "event_id": next_state["last_event_id"],
        "task_id": task_id,
        "installation_id": current["installation_id"],
        "leader_epoch": installation_state["active_leader_epoch"],
        "event_kind": f"task_{target_status}",
        "actor_principal_id": actor,
        "prior_revision": current["revision"],
        "new_revision": next_state["revision"],
        "payload": {"state_projection": dict(next_state)},
        "prior_event_digest": current.get("last_event_digest"),
    }
    event["event_digest"] = "sha256:" + hashlib.sha256((json.dumps({key: value for key, value in event.items() if key != "event_digest"}, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest()
    next_state["last_event_digest"] = event["event_digest"]
    next_state["projection_digest"] = _task_digest(next_state)
    with core._lock():
        current_again = task_state(root, task_id)
        if current_again["revision"] != current["revision"]:
            raise ControlPlaneError("VALP-E-STATE-CONFLICT", "Task changed while transition was prepared")
        append_jsonl(directory / "events.jsonl", event)
        write_json(directory / "task-state.json", next_state)
    return next_state
