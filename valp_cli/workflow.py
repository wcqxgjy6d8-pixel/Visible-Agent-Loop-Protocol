from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

PROFILE_DEFAULT_AGENTS = {
    "generic-analysis": ["hermes", "codex", "claude"],
    "software-code": ["hermes", "codex", "claude"],
    "apple-app": ["hermes", "codex", "claude", "agy"],
    "web-frontend": ["hermes", "codex", "claude"],
    "research": ["hermes", "codex", "claude"],
    "document-artifact": ["hermes", "codex", "claude"],
    "agent-runtime": ["hermes", "codex", "claude"],
    "ops-release": ["hermes", "codex", "claude"],
    "prototype": ["hermes", "agy", "codex"],
}

DEFAULT_CONTEXT_POLICIES = {
    "hermes": {"soft_warning_pct": 50, "hard_compression_pct": 60, "emergency_stop_pct": 80},
    "codex": {"soft_warning_pct": 55, "hard_compression_pct": 65, "emergency_stop_pct": 80},
    "claude": {"soft_warning_pct": 60, "hard_compression_pct": 70, "emergency_stop_pct": 80},
    "agy": {"soft_warning_pct": 60, "hard_compression_pct": 70, "emergency_stop_pct": 80},
}

RUNTIME_TASK_STATE_MAPPING = {
    "queued": "accepted_by_runtime_not_delivery",
    "dispatched": "dispatch_submitted_only_with_proof",
    "running": "executing",
    "completed": "dispatch_completed_only_with_expected_evidence",
    "failed": "failed_or_blocked_with_reason",
    "cancelled": "cancelled",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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


def local_capabilities_path() -> Path:
    return Path.home() / ".herdr" / "agent-capabilities.json"


def local_overlay_path() -> Path:
    return Path.home() / ".herdr" / "valp-local-overlay.json"


def classify_profile(prompt: str) -> str:
    lowered = prompt.lower()
    for profile, keywords in PROFILE_RULES:
        if any(keyword in lowered for keyword in keywords):
            return profile
    return "generic-analysis"


def load_local_capabilities() -> dict[str, Any]:
    data = read_json(local_capabilities_path())
    if data:
        return data
    return {
        "schema_version": "valp-agent-capabilities.v1",
        "updated_at": now_iso(),
        "source": "fallback minimal local scan",
        "agents": {
            "codex": {
                "active": True,
                "role": ["implementation", "verification", "tool_execution"],
                "skills": [],
                "mcp_servers": [],
                "strengths": ["edits files", "runs commands", "verifies with real tools"],
                "must_not_do": ["must not bypass approval gates"],
            }
        },
    }


def load_local_overlay() -> dict[str, Any]:
    return read_json(local_overlay_path())


def scan_workspace(root: Path, task_id: str | None = None) -> dict[str, Any]:
    root = workspace_root(root)
    capabilities = load_local_capabilities()
    overlay = load_local_overlay()
    capabilities["last_valp_scan_at"] = now_iso()
    capabilities["local_overlay_ref"] = str(local_overlay_path()) if overlay else None
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


def publish_task(root: Path, task_id: str, prompt: str, profile: str | None = None, route: bool = True) -> Path:
    root = workspace_root(root)
    directory = task_dir(root, task_id)
    directory.mkdir(parents=True, exist_ok=True)
    selected_profile = profile or classify_profile(prompt)
    task_md = f"""# Task

ID: {task_id}
Profile: {selected_profile}
Mode: Full Mode

## Goal

{prompt}

## Expected Evidence

Generated during routing.

## Approval Risks

Generated during routing.
"""
    (directory / "task.md").write_text(task_md, encoding="utf-8")
    state = {
        "schema_version": "valp-visible-loop-state.v1",
        "task_id": task_id,
        "profile": selected_profile,
        "status": "published",
        "selected_agents": [],
        "capabilities_needed": PROFILE_CAPABILITIES.get(selected_profile, PROFILE_CAPABILITIES["generic-analysis"]),
        "capabilities_missing": [],
        "gates": {
            "dispatch_receipts": "needs_evidence",
            "expected_evidence": "needs_evidence",
            "verification": "needs_evidence",
            "review": "needs_evidence",
            "approval": "not_required",
        },
        "approval_required": [],
        "updated_at": now_iso(),
    }
    write_json(directory / "state.json", state)
    if route:
        scan_workspace(root, task_id)
        route_task(root, task_id)
    return directory


def route_task(root: Path, task_id: str) -> dict[str, Any]:
    root = workspace_root(root)
    directory = task_dir(root, task_id)
    state_path = directory / "state.json"
    state = read_json(state_path)
    if not state:
        raise SystemExit(f"Missing state.json for task {task_id}")
    prompt = (directory / "task.md").read_text(encoding="utf-8", errors="replace")
    profile = state.get("profile") or classify_profile(prompt)
    capabilities = scan_workspace(root, task_id)
    overlay = load_local_overlay()
    agents = capabilities.get("agents") or {}
    candidate_scores = score_candidates(profile, agents)
    selected_agents = select_agents(profile, agents, candidate_scores)
    context_policies = {
        agent: context_policy_for(agent, agents.get(agent, {}), overlay)
        for agent in selected_agents
    }
    expected_by_agent = expected_refs_for_agents(selected_agents)
    routing = {
        "schema_version": "valp-capability-routing.v1",
        "task_id": task_id,
        "profile": profile,
        "runtime_adapter": runtime_adapter_record(),
        "local_overlay": {
            "used": bool(overlay),
            "ref": ".herdr-loop/local-overlay.json" if overlay else None,
            "note": "Local capability profiles are routing hints, not fixed assignments.",
        },
        "capabilities_needed": PROFILE_CAPABILITIES.get(profile, PROFILE_CAPABILITIES["generic-analysis"]),
        "selected_agents": selected_agents,
        "agent_match_reasons": {
            agent: match_reasons_for(agent, profile, agents.get(agent, {}))
            for agent in selected_agents
        },
        "candidate_scores": candidate_scores,
        "routing_confidence": routing_confidence(candidate_scores, selected_agents),
        "rejected_candidates": rejected_candidates(candidate_scores, selected_agents),
        "selected_agent_context_policies": context_policies,
        "skill_recommendations": {
            "schema_version": "valp-skill-recommendations.v1",
            "status": "not_run",
            "reason": "Local VALP coordinator route did not run a skill recommender backend.",
        },
        "provider_matrix": provider_matrix_for(selected_agents, agents, overlay),
        "runtime_task_state_mapping": RUNTIME_TASK_STATE_MAPPING,
        "squad_routing": {"used": False},
        "routing_feedback_ref": "routing-feedback.json",
        "capabilities_missing": [],
    }
    write_json(directory / "routing.json", routing)
    write_dispatches(directory, task_id, profile, prompt, selected_agents, expected_by_agent, routing)
    append_dispatch_written_receipts(directory, selected_agents, expected_by_agent)
    state.update(
        {
            "profile": profile,
            "status": "dispatching",
            "runtime_adapter": routing["runtime_adapter"],
            "local_overlay": routing["local_overlay"],
            "runtime_task_state_mapping": RUNTIME_TASK_STATE_MAPPING,
            "provider_matrix": {"status": "scanned", "ref": "routing.json"},
            "squad_routing": {"used": False},
            "selected_agents": selected_agents,
            "capabilities_needed": routing["capabilities_needed"],
            "capabilities_missing": [],
            "context_policies": context_policies,
            "skill_recommendations": {"status": "not_run"},
            "routing_confidence": routing["routing_confidence"],
            "routing_feedback": {"status": "expected", "ref": "routing-feedback.json"},
            "updated_at": now_iso(),
        }
    )
    write_json(state_path, state)
    return routing


def score_candidates(profile: str, agents: dict[str, Any]) -> dict[str, dict[str, Any]]:
    defaults = set(PROFILE_DEFAULT_AGENTS.get(profile, PROFILE_DEFAULT_AGENTS["generic-analysis"]))
    scores: dict[str, dict[str, Any]] = {}
    for agent, info in agents.items():
        active = bool(info.get("active", True))
        runtime = info.get("runtime") or {}
        runtime_status = str(runtime.get("status", "unknown"))
        profile_fit = 0.9 if agent in defaults else 0.35
        tool_fit = 0.85 if info.get("mcp_servers") or runtime else 0.55
        skill_count = len(info.get("skills") or [])
        skill_fit = min(0.95, 0.45 + skill_count / 80)
        permission_fit = 0 if not active else 1
        context_fit = 0.85
        evidence_history = 0.6
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
        }
    return scores


def select_agents(profile: str, agents: dict[str, Any], scores: dict[str, dict[str, Any]]) -> list[str]:
    defaults = PROFILE_DEFAULT_AGENTS.get(profile, PROFILE_DEFAULT_AGENTS["generic-analysis"])
    selected = [
        agent
        for agent in defaults
        if agent in agents and scores.get(agent, {}).get("overall", 0) >= 0.5
    ]
    if selected:
        return selected
    ranked = sorted(scores, key=lambda name: scores[name].get("overall", 0), reverse=True)
    return ranked[:1]


def context_policy_for(agent: str, info: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    overlay_profiles = overlay.get("agent_capability_profiles") or {}
    overlay_policy = (overlay_profiles.get(agent) or {}).get("context_policy")
    if overlay_policy:
        return overlay_policy
    if info.get("context_policy"):
        return info["context_policy"]
    return DEFAULT_CONTEXT_POLICIES.get(agent, {"soft_warning_pct": 60, "hard_compression_pct": 70, "emergency_stop_pct": 80})


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
                    "reason": "Not selected because default profile route had stronger fit.",
                }
            )
    return rejected


def runtime_adapter_record() -> dict[str, Any]:
    herdr = shutil.which("herdr")
    return {
        "class": "pane_controller" if herdr else "manual",
        "name": "HERDR" if herdr else "manual",
        "full_mode_capable": bool(herdr),
        "state_mapping_ref": "docs/task-state-machine.md",
    }


def provider_matrix_for(selected_agents: list[str], agents: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    overlay_profiles = overlay.get("agent_capability_profiles") or {}
    providers = {}
    for agent in selected_agents:
        info = agents.get(agent, {})
        overlay_profile = overlay_profiles.get(agent) or {}
        providers[agent] = {
            "provider_name": agent,
            "cli_available": True,
            "mcp_support": "supported" if info.get("mcp_servers") else "unknown",
            "skill_discovery_path": overlay_profile.get("skill_library_paths") or "unknown",
            "session_resume_support": "unknown",
            "approval_behavior": overlay_profile.get("approval_behavior") or "unknown",
            "context_policy": context_policy_for(agent, info, overlay),
            "known_limitations": info.get("must_not_do") or [],
            "last_verified_at": now_iso(),
        }
    return {"generated_at": now_iso(), "providers": providers}


def expected_refs_for_agents(selected_agents: list[str]) -> dict[str, list[str]]:
    refs = {}
    for agent in selected_agents:
        if agent == "codex":
            refs[agent] = ["agents/codex/evidence.md", "evidence/verification.md"]
        elif agent == "claude":
            refs[agent] = ["agents/claude/review.md"]
        elif agent == "hermes":
            refs[agent] = ["agents/hermes/self-review.md"]
        elif agent == "agy":
            refs[agent] = ["agents/agy/prototype.md"]
        else:
            refs[agent] = [f"agents/{agent}/evidence.md"]
    return refs


def write_dispatches(
    directory: Path,
    task_id: str,
    profile: str,
    prompt: str,
    selected_agents: list[str],
    expected_by_agent: dict[str, list[str]],
    routing: dict[str, Any],
) -> None:
    for agent in selected_agents:
        agent_dir = directory / "agents" / agent
        agent_dir.mkdir(parents=True, exist_ok=True)
        expected = "\n".join(f"- `{ref}`" for ref in expected_by_agent.get(agent, []))
        reasons = "\n".join(f"- {reason}" for reason in routing["agent_match_reasons"].get(agent, []))
        dispatch = f"""# Dispatch: {agent}

Task: {task_id}
Profile: {profile}

## Role

Use your routed capability profile for this task. Local profiles are hints, not fixed assignments.

## Capability Match

{reasons}

## User Request

{prompt}

## Permission Boundary

- Do not bypass approval gates.
- Do not claim runtime facts without evidence.
- Write only the expected evidence for your role unless the task explicitly permits source edits.

## Expected Evidence

{expected}

## Required Response

Write concise evidence to the expected path. Include blockers, confidence limits, and any handoff needed for the next agent.
"""
        (agent_dir / "dispatch.md").write_text(dispatch, encoding="utf-8")


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


def dispatch_task(root: Path, task_id: str, agent: str = "all", submit: bool = False) -> list[str]:
    root = workspace_root(root)
    directory = task_dir(root, task_id)
    routing = read_json(directory / "routing.json")
    if not routing:
        raise SystemExit(f"Missing routing.json for task {task_id}")
    selected_agents = routing.get("selected_agents") or []
    targets = selected_agents if agent == "all" else [agent]
    commands = []
    for target in targets:
        dispatch_ref = directory / "agents" / target / "dispatch.md"
        if not dispatch_ref.exists():
            raise SystemExit(f"Missing dispatch for agent {target}: {dispatch_ref}")
        expected = expected_refs_for_agents([target]).get(target, [])
        command = ["herdr-loop", "--project-root", str(root), "submit-dispatch", task_id, target]
        for ref in expected:
            command.extend(["--expect", ref])
        commands.append(" ".join(command))
        if submit:
            subprocess.run(command, check=True)
    return commands
