from __future__ import annotations

import json
import os
import re
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
) -> dict[str, Any]:
    loop_layer = classify_loop_layer(prompt, profile)
    design_contract = find_design_contract(root)
    context_selection = context_selection_for(root, directory, profile, loop_layer)
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
        "mask_list_ref": "mask-list.json",
        "evidence_board_ref": "evidence-board.json",
        "visible_summary_ref": "visible-routing.md",
    }
    visible_routing = format_visible_routing(attention_map, context_selection, mask_list, evidence_board, design_contract)
    write_json(directory / "attention-map.json", attention_map)
    write_json(directory / "context-selection.json", context_selection)
    write_json(directory / "mask-list.json", mask_list)
    write_json(directory / "evidence-board.json", evidence_board)
    (directory / "visible-routing.md").write_text(visible_routing, encoding="utf-8")
    return {
        "loop_layer": loop_layer,
        "design_contract": design_contract,
        "refs": {
            "attention_map": "attention-map.json",
            "context_selection": "context-selection.json",
            "mask_list": "mask-list.json",
            "evidence_board": "evidence-board.json",
            "visible_routing": "visible-routing.md",
        },
        "attention_map": attention_map,
        "context_selection": context_selection,
        "mask_list": mask_list,
    }


def format_visible_routing(
    attention_map: dict[str, Any],
    context_selection: dict[str, Any],
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
        masks="\n".join(mask_lines) or "- none",
        claims="\n".join(claim_lines) or "- none",
    )


def attention_slice_for_agent(agent: str, visible_attention: dict[str, Any]) -> str:
    attention_map = visible_attention.get("attention_map") or {}
    context_selection = visible_attention.get("context_selection") or {}
    mask_list = visible_attention.get("mask_list") or {}
    heads = attention_map.get("heads") or {}
    matching_heads = [
        head
        for head, record in heads.items()
        if record.get("selected") == agent or record.get("candidate") == agent
    ]
    context_lines = [
        f"- `{item.get('path')}`: {item.get('reason')}"
        for item in (context_selection.get("selected") or [])[:8]
    ]
    mask_lines = [
        f"- {item.get('item')}: {item.get('reason')}"
        for item in (mask_list.get("masked") or [])[:6]
    ]
    design = visible_attention.get("design_contract") or {}
    return """- Loop layer: `{loop_layer}`
- Your attention head(s): {heads}
- Design contract: `{design_status}`{design_path}
- Context selected for this round:
{context}
- Inputs masked out:
{masks}
""".format(
        loop_layer=visible_attention.get("loop_layer", "unknown"),
        heads=", ".join(matching_heads) if matching_heads else "none",
        design_status=design.get("status", "unknown"),
        design_path=f" (`{design.get('path')}`)" if design.get("path") else "",
        context="\n".join(context_lines) or "  - none",
        masks="\n".join(mask_lines) or "  - none",
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
    task_md = f"""# Task

ID: {task_id}
Profile: {selected_profile}
Mode: Selected during routing

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
    capabilities = scan_workspace(root, task_id, runtime=runtime)
    overlay = load_local_overlay(root)
    agents = capabilities.get("agents") or {}
    candidate_scores = score_candidates(profile, agents)
    selected_agents = select_agents(profile, agents, candidate_scores)
    role_assignments = role_assignments_for(profile, selected_agents, agents, candidate_scores)
    preflight = collect_runtime_preflight(selected_agents, runtime=runtime)
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
    )
    context_policies = {
        agent: context_policy_for(agent, agents.get(agent, {}), overlay)
        for agent in selected_agents
    }
    expected_by_agent = expected_refs_for_agents(selected_agents, role_assignments)
    routing = {
        "schema_version": "valp-capability-routing.v1",
        "task_id": task_id,
        "profile": profile,
        "runtime_adapter": runtime_adapter_record(preflight, runtime=runtime),
        "local_overlay": {
            "used": bool(overlay),
            "ref": ".herdr-loop/local-overlay.json" if overlay else None,
            "note": "Local capability profiles are routing hints, not fixed assignments.",
        },
        "capabilities_needed": PROFILE_CAPABILITIES.get(profile, PROFILE_CAPABILITIES["generic-analysis"]),
        "role_requirements": PROFILE_ROLE_REQUIREMENTS.get(profile, PROFILE_ROLE_REQUIREMENTS["generic-analysis"]),
        "role_assignments": role_assignments,
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
        "capabilities_missing": [],
    }
    write_json(directory / "routing.json", routing)
    write_dispatches(root, directory, task_id, profile, prompt, selected_agents, expected_by_agent, routing, skill_recommendations, visible_attention)
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
            "capabilities_needed": routing["capabilities_needed"],
            "capabilities_missing": [],
            "context_policies": context_policies,
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
            "updated_at": now_iso(),
        }
    )
    write_json(state_path, state)
    return routing


def score_candidates(profile: str, agents: dict[str, Any]) -> dict[str, dict[str, Any]]:
    required_roles = PROFILE_ROLE_REQUIREMENTS.get(profile, PROFILE_ROLE_REQUIREMENTS["generic-analysis"])
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
            "role_fit": role_fit,
            "routing_basis": "capability_roles",
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
        agent_roles = {role for role, selected in role_assignments.items() if selected == agent}
        agent_refs: list[str] = []
        if "coordinator" in agent_roles:
            agent_refs.append(f"agents/{agent}/self-review.md")
        if "implementer" in agent_roles or "researcher" in agent_roles:
            agent_refs.append(f"agents/{agent}/evidence.md")
        if "implementer" in agent_roles:
            agent_refs.append("evidence/verification.md")
        if "reviewer" in agent_roles:
            agent_refs.append(f"agents/{agent}/review.md")
        if "prototype" in agent_roles:
            agent_refs.append(f"agents/{agent}/prototype.md")
        if not agent_refs:
            agent_refs.append(f"agents/{agent}/evidence.md")
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
) -> None:
    for agent in selected_agents:
        agent_dir = directory / "agents" / agent
        agent_dir.mkdir(parents=True, exist_ok=True)
        expected = "\n".join(f"- `{ref}`" for ref in expected_by_agent.get(agent, []))
        exact_evidence = "\n".join(f"- `{relative_ref(directory / ref, root)}`" for ref in expected_by_agent.get(agent, []))
        reasons = "\n".join(f"- {reason}" for reason in routing["agent_match_reasons"].get(agent, []))
        skills = format_skill_recommendations_for_dispatch(agent, skill_recommendations)
        attention_slice = attention_slice_for_agent(agent, visible_attention)
        dispatch = f"""# Dispatch: {agent}

Task: {task_id}
Profile: {profile}

## Project Root

Before inspecting or writing evidence, run:

```bash
cd "{root}"
```

Write evidence exactly to:

{exact_evidence}

## Role

Use your routed capability profile for this task. Local profiles are hints, not fixed assignments.

## Capability Match

{reasons}

## User Request

{prompt}

## Visible Attention Slice

{attention_slice}

## Permission Boundary

- Do not bypass approval gates.
- Do not claim runtime facts without evidence.
- Write only the expected evidence for your role unless the task explicitly permits source edits.

## Expected Evidence

{expected}

## Recommended Skills

{skills}

## Evidence Claim Rule

- Any build, test, lint, runtime, UI, or verification claim must cite a concrete command log, screenshot, receipt, or evidence file path.
- Do not write "verified", "tests passed", "build passed", or equivalent claims unless the evidence path exists or the blocker is explicit.

## Required Response

Write concise evidence to the expected path. Include blockers, confidence limits, and any handoff needed for the next agent.
"""
        (agent_dir / "dispatch.md").write_text(dispatch, encoding="utf-8")


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
        "- Load or invoke an installed skill only when it matches your role and materially improves this task.",
    ]
    if source is not skill_recommendations:
        lines.append(f"- These recommendations were filtered for `{agent}` with the recommender's provider filter.")
    count = 0
    for result in source.get("results") or []:
        task = str(result.get("task") or "").strip()
        routing = result.get("routing") or {}
        for match in result.get("matches") or []:
            if not match.get("installed"):
                continue
            if not skill_visible_to_agent(agent, str(match.get("path") or "")):
                continue
            count += 1
            lines.append(
                "- Task `{}` -> skill `{}` ({}, confidence {}, mode {}, path `{}`).".format(
                    task,
                    match.get("skill", "unknown"),
                    routing.get("decision", "unknown"),
                    match.get("confidence", "unknown"),
                    match.get("mode", "unknown"),
                    match.get("path", "unknown"),
                )
            )
            if count >= 5:
                break
        if count >= 5:
            break
    if count == 0:
        lines.append("- No installed skill matched strongly enough for this dispatch.")
    missing = source.get("missing_skills") or []
    for missing_skill in missing[:3]:
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


def append_receipt(directory: Path, record: dict[str, Any]) -> None:
    receipts_path = directory / "dispatch-receipts.jsonl"
    receipts_path.parent.mkdir(parents=True, exist_ok=True)
    with receipts_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_queue_submission(directory: Path, task_id: str, target: str, expected: list[str]) -> dict[str, Any]:
    queue_id = f"{task_id}-{target}"
    worker_id = f"worker-{target}"
    queue_record = {
        "schema_version": "valp-queue-dispatch.v1",
        "task_id": task_id,
        "agent": target,
        "queue_id": queue_id,
        "worker_id": worker_id,
        "status": "queued",
        "dispatch_ref": f"agents/{target}/dispatch.md",
        "expected_refs": expected,
        "created_at": now_iso(),
        "note": "Synthetic reference queue submission. Completion still requires dispatch_completed plus expected evidence.",
    }
    write_json(directory / "queue" / f"{target}.json", queue_record)
    append_receipt(
        directory,
        {
            "ts": now_iso(),
            "agent": target,
            "event": "dispatch_submitted",
            "dispatch_ref": f"agents/{target}/dispatch.md",
            "expected_refs": expected,
            "proof": {
                "runtime": "VALP headless queue",
                "queue_id": queue_id,
                "worker_id": worker_id,
                "queue_record": f"queue/{target}.json",
            },
            "summary": "Headless queue adapter accepted the dispatch. Completion still requires expected evidence.",
        },
    )
    return queue_record


def dispatch_task(root: Path, task_id: str, agent: str = "all", submit: bool = False, runtime: str | None = None) -> list[str]:
    root = workspace_root(root)
    directory = task_dir(root, task_id)
    routing = read_json(directory / "routing.json")
    if not routing:
        raise SystemExit(f"Missing routing.json for task {task_id}")
    selected_agents = routing.get("selected_agents") or []
    targets = selected_agents if agent == "all" else [agent]
    runtime_record = routing.get("runtime_adapter") or {}
    requested_runtime = normalize_runtime(runtime)
    runtime_kind = runtime_from_adapter_record(runtime_record) if requested_runtime == "auto" else requested_runtime
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
    role_assignments = routing.get("role_assignments") or {}
    for target in targets:
        dispatch_ref = directory / "agents" / target / "dispatch.md"
        if not dispatch_ref.exists():
            raise SystemExit(f"Missing dispatch for agent {target}: {dispatch_ref}")
        expected = expected_refs_for_agents([target], role_assignments).get(target, [])
        if runtime_kind == "manual":
            if submit:
                raise SystemExit("Manual Mode cannot use --submit. Copy dispatches manually and record manual_result_attested when evidence exists.")
            expected_text = ", ".join(expected) if expected else "task-local evidence"
            commands.append(f"Manual Mode: copy agents/{target}/dispatch.md to {target}; expected evidence: {expected_text}")
            continue
        if runtime_kind == "queue":
            expected_text = ", ".join(expected) if expected else "task-local evidence"
            commands.append(f"VALP Queue Mode: enqueue agents/{target}/dispatch.md for {target}; expected evidence: {expected_text}")
            if submit:
                write_queue_submission(directory, task_id, target, expected)
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
