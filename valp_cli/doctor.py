from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .audit import WARN as AUDIT_WARN, TaskAudit, resolve_task_dir
from .model_identity import model_identity_for
from .workflow import collect_runtime_preflight, load_local_capabilities, load_local_overlay


PASS = "pass"
WARN = "warn"
FAIL = "fail"


@dataclass
class DoctorCheck:
    id: str
    title: str
    status: str
    message: str
    evidence: list[str]
    suggestion: str | None = None


@dataclass
class DoctorReport:
    workspace: str
    generated_at: str
    status: str
    pass_count: int
    warn_count: int
    fail_count: int
    checks: list[DoctorCheck]
    capability_passports: list[dict[str, Any]] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_command(command: list[str], cwd: Path, timeout: float = 10.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {"ok": False, "exit_code": 127, "stdout": "", "stderr": "command not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "command timed out"}
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def make_check(
    check_id: str,
    title: str,
    status: str,
    message: str,
    evidence: list[str] | None = None,
    suggestion: str | None = None,
) -> DoctorCheck:
    return DoctorCheck(check_id, title, status, message, evidence or [], suggestion)


def collect_doctor_report(root: Path, task_id: str | None = None) -> DoctorReport:
    workspace = root.resolve()
    generated_at = now_iso()
    checks: list[DoctorCheck] = []
    checks.extend(git_checks(workspace))
    checks.extend(install_checks(workspace))
    checks.extend(syntax_checks(workspace))
    checks.extend(example_audit_checks(workspace))
    checks.extend(runtime_checks())
    if task_id:
        checks.append(task_audit_check(workspace, task_id))
    capability_passports = commission_capability_passports(workspace, evaluated_at=generated_at)

    pass_count = sum(1 for check in checks if check.status == PASS)
    warn_count = sum(1 for check in checks if check.status == WARN)
    fail_count = sum(1 for check in checks if check.status == FAIL)
    status = FAIL if fail_count else WARN if warn_count else PASS
    return DoctorReport(
        workspace=str(workspace),
        generated_at=generated_at,
        status=status,
        pass_count=pass_count,
        warn_count=warn_count,
        fail_count=fail_count,
        checks=checks,
        capability_passports=capability_passports,
    )


def commission_capability_passports(root: Path, *, evaluated_at: str) -> list[dict[str, Any]]:
    capabilities = load_local_capabilities(root)
    agents = capabilities.get("agents") or {}
    overlay = load_local_overlay(root)
    overlay_profiles = overlay.get("agent_capability_profiles") or {}
    agent_ids = sorted(str(agent_id) for agent_id in agents)
    preflight = collect_runtime_preflight(agent_ids, runtime="auto")
    runtime_agents = preflight.get("agents") or {}
    passports: list[dict[str, Any]] = []

    for agent_id in agent_ids:
        info = agents.get(agent_id) or {}
        agent_preflight = runtime_agents.get(agent_id) or {}
        sessions = agent_preflight.get("sessions")
        if isinstance(sessions, list) and sessions:
            session_preflights = []
            for session in sessions:
                if not isinstance(session, dict):
                    continue
                merged = {key: value for key, value in agent_preflight.items() if key != "sessions"}
                merged.update(session)
                session_preflights.append(merged)
        else:
            session_preflights = [agent_preflight]
        for session_preflight in session_preflights:
            passports.append(
                build_capability_passport(
                    agent_id,
                    info,
                    overlay_profiles.get(agent_id) or {},
                    capability_source=str(capabilities.get("source") or "unknown"),
                    runtime_preflight=preflight,
                    agent_preflight=session_preflight,
                    evaluated_at=evaluated_at,
                )
            )
    return passports


def build_capability_passport(
    agent_id: str,
    info: dict[str, Any],
    overlay_profile: dict[str, Any],
    *,
    capability_source: str,
    runtime_preflight: dict[str, Any],
    agent_preflight: dict[str, Any],
    evaluated_at: str,
) -> dict[str, Any]:
    model_identity = model_identity_for(
        agent_id,
        info,
        overlay_profile,
        runtime_probe=agent_preflight.get("model_probe"),
        evaluated_at=evaluated_at,
    )
    declared_roles = {str(role).strip().lower() for role in info.get("role") or []}
    live_status = str(agent_preflight.get("status") or "unknown")

    def role_status(claims: set[str], *, model_role: str | None = None) -> str:
        if not declared_roles.intersection(claims):
            return "not_declared"
        if not info.get("active", True) or live_status not in {"pass", "warn"}:
            return "blocked"
        if model_role and model_identity["role_eligibility"].get(model_role) != "eligible":
            return "blocked"
        return "eligible"

    discovery = (
        agent_preflight.get("capability_discovery")
        if isinstance(agent_preflight.get("capability_discovery"), dict)
        else {}
    )
    discovery_source = str(discovery.get("source") or capability_source)
    discovered_permissions = discovery.get("permissions")
    permissions = (
        discovered_permissions
        if isinstance(discovered_permissions, dict)
        else info.get("permissions") if isinstance(info.get("permissions"), dict) else {}
    )
    official_claims = [
        dict(claim)
        for claim in info.get("official_capability_claims") or []
        if isinstance(claim, dict)
    ]
    installation = dict(info.get("installation") or {})
    cli_record = agent_preflight.get("cli") if isinstance(agent_preflight.get("cli"), dict) else {}
    if not installation:
        installation = {
            "status": "present" if info.get("active", True) else "inactive",
            "version": str(cli_record.get("version_output") or "unknown"),
            "source": capability_source,
        }
    history_records: list[dict[str, Any]] = []
    current_binding = model_identity["history_binding"]
    binding_fields = ("agent_surface", "model_id", "provider", "reasoning_mode", "session_token")
    for raw_record in info.get("task_verified_history") or []:
        if not isinstance(raw_record, dict):
            continue
        record = dict(raw_record)
        binding = record.get("binding") if isinstance(record.get("binding"), dict) else {}
        if not all(field in binding for field in binding_fields):
            binding_status = "unbound"
        elif all(binding.get(field) == current_binding.get(field) for field in binding_fields):
            binding_status = "current"
        else:
            binding_status = "mismatch"
        record["binding_status"] = binding_status
        history_records.append(record)
    qualifying_history = [
        record
        for record in history_records
        if record["binding_status"] == "current"
        and str(record.get("outcome") or "").lower() in {"pass", "passed", "verified", "accepted"}
    ]
    return {
        "schema_version": "valp-capability-passport.v1",
        "generated_at": evaluated_at,
        "agent_id": agent_id,
        "agent_surface": model_identity["agent_surface"],
        "runtime_identity": {
            "runtime": runtime_preflight.get("runtime") or "unknown",
            "adapter_class": runtime_preflight.get("adapter_class") or "unknown",
            "session_id": str(agent_preflight.get("session_id") or "unknown"),
            "session": model_identity["model_probe"]["session_identity"],
        },
        "capability_layers": {
            "official_claim": {"status": "present" if official_claims else "unknown"},
            "local_presence": {
                "status": "present"
                if str(installation.get("status") or "").lower() in {"installed", "present", "active"}
                else "inactive" if str(installation.get("status") or "").lower() == "inactive" else "unknown"
            },
            "live_callable": {
                "status": "present" if live_status == "pass" else "degraded" if live_status == "warn" else "unknown"
            },
            "task_verified": {"status": "present" if qualifying_history else "unknown"},
        },
        "official_capability_claims": official_claims,
        "local_installation": installation,
        "live_callability": {
            "status": live_status,
            "runtime": str(runtime_preflight.get("runtime") or "unknown"),
            "evidence": dict(agent_preflight),
        },
        "task_verified_history": {
            "records": history_records,
            "qualifying_record_count": len(qualifying_history),
            "binding": current_binding,
        },
        "model_identity": model_identity,
        "skills": {
            "reachable": [str(skill) for skill in discovery.get("skills", info.get("skills") or [])],
            "source": discovery_source if "skills" in discovery else str(info.get("skills_source") or capability_source),
        },
        "mcp": {
            "servers": [str(server) for server in discovery.get("mcp_servers", info.get("mcp_servers") or [])],
            "tools": [str(tool) for tool in discovery.get("mcp_tools", info.get("mcp_tools") or [])],
            "source": discovery_source
            if "mcp_servers" in discovery or "mcp_tools" in discovery
            else str(info.get("mcp_source") or capability_source),
        },
        "permissions": {
            "filesystem": [str(item) for item in permissions.get("filesystem") or []],
            "network": [str(item) for item in permissions.get("network") or []],
            "shell": [str(item) for item in permissions.get("shell") or []],
            "mutation": [str(item) for item in permissions.get("mutation") or []],
        },
        "context": {
            "policy": dict(discovery.get("context_policy", info.get("context_policy") or {})),
            "current": dict(discovery.get("current_context", info.get("current_context") or {})),
        },
        "known_limitations": [
            str(item)
            for item in discovery.get("known_limitations", info.get("must_not_do") or [])
        ],
        "role_eligibility": {
            "leader": role_status({"leader", "coordination", "coordinator", "state"}),
            "implementer": role_status(
                {"implementation", "implementer", "verification"},
                model_role="implementer",
            ),
            "reviewer": role_status(
                {"review", "reviewer", "code_review", "risk_review"},
                model_role="final_reviewer",
            ),
            "researcher": role_status({"research", "researcher"}),
        },
    }


def git_checks(root: Path) -> list[DoctorCheck]:
    inside = run_command(["git", "rev-parse", "--is-inside-work-tree"], root)
    if not inside.get("ok") or str(inside.get("stdout", "")).strip() != "true":
        return [
            make_check(
                "git_repository",
                "Workspace is a git repository",
                WARN,
                "Workspace is not inside a git work tree; git tracking checks were skipped.",
                suggestion="Run doctor from a git-backed VALP workspace for sync checks.",
            )
        ]

    checks: list[DoctorCheck] = []
    head = run_command(["git", "rev-parse", "HEAD"], root)
    upstream = run_command(["git", "rev-parse", "@{u}"], root)
    if head.get("ok") and upstream.get("ok"):
        head_sha = str(head.get("stdout", "")).strip()
        upstream_sha = str(upstream.get("stdout", "")).strip()
        if head_sha == upstream_sha:
            checks.append(make_check("git_tracking", "Local HEAD matches upstream tracking ref", PASS, f"HEAD == upstream tracking ref ({head_sha[:7]}).", [head_sha]))
        else:
            counts = run_command(["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"], root)
            detail = str(counts.get("stdout", "")).strip() or "ahead/behind count unavailable"
            checks.append(
                make_check(
                    "git_tracking",
                    "Local HEAD matches upstream tracking ref",
                    FAIL,
                    f"HEAD differs from the local upstream tracking ref ({detail}).",
                    [head_sha, upstream_sha],
                    "Fetch/pull/push or reconcile the branch before claiming release-ready sync.",
                )
            )
    else:
        checks.append(
            make_check(
                "git_tracking",
                "Local HEAD matches upstream tracking ref",
                WARN,
                "No upstream branch is configured.",
                suggestion="Set an upstream branch if local tracking status matters for this workspace.",
            )
        )

    status = run_command(["git", "status", "--porcelain"], root)
    lines = [line for line in str(status.get("stdout", "")).splitlines() if line.strip()]
    if lines:
        checks.append(
            make_check(
                "git_worktree_clean",
                "Git working tree is clean",
                FAIL,
                f"Working tree has {len(lines)} changed or untracked item(s).",
                lines[:20],
                "Commit, stash, or intentionally remove local changes before release or reproducibility checks.",
            )
        )
    else:
        checks.append(make_check("git_worktree_clean", "Git working tree is clean", PASS, "No tracked or untracked changes found."))

    ignored = run_command(["git", "status", "--ignored", "--porcelain"], root)
    ignored_lines = [
        line
        for line in str(ignored.get("stdout", "")).splitlines()
        if line.startswith("!! ")
    ]
    if ignored_lines:
        checks.append(
            make_check(
                "ignored_residue",
                "Ignored local residue is absent",
                WARN,
                f"Found {len(ignored_lines)} ignored local item(s).",
                ignored_lines[:20],
                "Remove caches or local runtime evidence when you need a pristine checkout.",
            )
        )
    else:
        checks.append(make_check("ignored_residue", "Ignored local residue is absent", PASS, "No ignored residue reported by git."))
    return checks


def install_checks(root: Path) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    entrypoint = root / "bin" / "valp"
    if entrypoint.exists() and entrypoint.is_file():
        executable = "yes" if entrypoint.stat().st_mode & 0o111 else "no"
        status = PASS if executable == "yes" else WARN
        checks.append(
            make_check(
                "valp_entrypoint",
                "bin/valp entrypoint exists",
                status,
                f"bin/valp exists; executable={executable}.",
                ["bin/valp"],
                None if status == PASS else "Run chmod +x bin/valp if you need direct shell execution.",
            )
        )
    else:
        checks.append(make_check("valp_entrypoint", "bin/valp entrypoint exists", FAIL, "bin/valp was not found.", ["bin/valp"]))

    checks.append(
        make_check(
            "python",
            "Python runtime is available",
            PASS,
            f"Python executable: {sys.executable}",
            [sys.version.split()[0]],
        )
    )
    checks.append(make_check("valp_version", "VALP CLI version is importable", PASS, f"valp {__version__}", [__version__]))
    return checks


def syntax_checks(root: Path) -> list[DoctorCheck]:
    json_paths = sorted([*root.joinpath("examples").rglob("*.json"), *root.joinpath("schemas").rglob("*.json")])
    jsonl_paths = sorted(root.joinpath("examples").rglob("*.jsonl"))
    failures: list[str] = []
    for path in json_paths:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            failures.append(f"{path.relative_to(root)}: {exc}")
    for path in jsonl_paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            failures.append(f"{path.relative_to(root)}: {exc}")
            continue
        for lineno, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                failures.append(f"{path.relative_to(root)}:{lineno}: {exc}")
    if failures:
        return [
            make_check(
                "json_syntax",
                "Examples and schemas parse as JSON/JSONL",
                FAIL,
                f"Found {len(failures)} JSON/JSONL parse error(s).",
                failures[:20],
            )
        ]
    return [
        make_check(
            "json_syntax",
            "Examples and schemas parse as JSON/JSONL",
            PASS,
            f"Parsed {len(json_paths)} JSON and {len(jsonl_paths)} JSONL file(s).",
        )
    ]


def example_audit_checks(root: Path) -> list[DoctorCheck]:
    examples = [
        "examples/minimal-task",
        "examples/full-mode-task",
        "examples/headless-queue-task",
    ]
    checks: list[DoctorCheck] = []
    for example in examples:
        path = root / example
        if not path.exists():
            checks.append(make_check(f"audit_{Path(example).name}", f"Audit {example}", FAIL, "Example folder is missing.", [example]))
            continue
        report = TaskAudit(path).run()
        status = audit_status_to_doctor_status(report.status)
        checks.append(
            make_check(
                f"audit_{Path(example).name}",
                f"Audit {example}",
                status,
                f"Audit status {report.status}; pass={report.pass_count} warn={report.warn_count} fail={report.fail_count} skip={report.skip_count}.",
                [example],
                None if status == PASS else "Run bin/valp audit on this example and inspect warnings or failing evidence gates.",
            )
        )
    return checks


def runtime_checks() -> list[DoctorCheck]:
    checks = [
        make_check("runtime_manual", "Manual runtime adapter is available", PASS, "Manual Mode is always available."),
    ]
    queue = collect_runtime_preflight(["doctor"], runtime="queue")
    checks.append(
        make_check(
            "runtime_queue",
            "Headless queue reference probe works",
            PASS if queue.get("status") == PASS else FAIL,
            f"Reference queue-shaped preflight status: {queue.get('status')}.",
            ["adapter_class=" + str(queue.get("adapter_class"))],
        )
    )
    herdr_path = shutil.which("herdr")
    if not herdr_path:
        checks.append(
            make_check(
                "runtime_herdr",
                "HERDR reference runtime is available",
                WARN,
                "herdr command was not found on PATH.",
                suggestion="Install HERDR only if you need the pane-controller Full Mode reference runtime.",
            )
        )
        return checks

    herdr = collect_runtime_preflight(runtime="herdr")
    status = herdr.get("status")
    submission = (herdr.get("checks") or {}).get("submission_transport") or {}
    doctor_status = PASS if status == PASS else FAIL if status == FAIL else WARN
    submission_mode = submission.get("mode") or "unknown"
    checks.append(
        make_check(
            "runtime_herdr",
            "HERDR reference runtime is available",
            doctor_status,
            (
                f"HERDR preflight status: {status}; submission mode: {submission_mode}; "
                f"command={herdr_path}."
            ),
            ["adapter_class=" + str(herdr.get("adapter_class"))],
            None if status == PASS else "Run bin/valp preflight --runtime herdr for detailed pane/runtime diagnostics.",
        )
    )
    return checks


def task_audit_check(root: Path, task_id: str) -> DoctorCheck:
    try:
        task_path = resolve_task_dir(root, task_id)
        report = TaskAudit(task_path).run()
    except SystemExit as exc:
        return make_check("task_audit", f"Audit task {task_id}", FAIL, str(exc), [task_id])
    status = audit_status_to_doctor_status(report.status)
    return make_check(
        "task_audit",
        f"Audit task {task_id}",
        status,
        f"Audit status {report.status}; pass={report.pass_count} warn={report.warn_count} fail={report.fail_count} skip={report.skip_count}.",
        [str(task_path)],
        None if status == PASS else "Inspect task audit warnings or failures before claiming Done.",
    )


def audit_status_to_doctor_status(status: str) -> str:
    lowered = str(status).lower()
    if lowered == PASS:
        return PASS
    if lowered == AUDIT_WARN:
        return WARN
    return FAIL


def report_to_dict(report: DoctorReport) -> dict[str, Any]:
    return asdict(report)


def render_text_summary(report: DoctorReport) -> str:
    lines = [
        f"VALP doctor: {report.status.upper()}",
        f"Workspace: {report.workspace}",
        f"Summary: pass={report.pass_count} warn={report.warn_count} fail={report.fail_count}",
        f"Capability passports: {len(report.capability_passports)}",
        "",
    ]
    for check in report.checks:
        lines.append(f"[{check.status.upper()}] {check.id}: {check.title}")
        lines.append(f"  {check.message}")
        if check.suggestion:
            lines.append(f"  suggestion: {check.suggestion}")
    return "\n".join(lines)


def render_markdown_report(report: DoctorReport) -> str:
    lines = [
        "# VALP Doctor Report",
        "",
        f"Generated: {report.generated_at}",
        f"Workspace: `{report.workspace}`",
        f"Status: **{report.status.upper()}**",
        "",
        "## Summary",
        "",
        f"- Pass: {report.pass_count}",
        f"- Warn: {report.warn_count}",
        f"- Fail: {report.fail_count}",
        "",
        "## Checks",
        "",
    ]
    for check in report.checks:
        lines.append(f"### {check.status.upper()} `{check.id}`")
        lines.append("")
        lines.append(check.title)
        lines.append("")
        lines.append(check.message)
        if check.evidence:
            lines.append("")
            lines.append("Evidence:")
            for item in check.evidence:
                lines.append(f"- `{item}`")
        if check.suggestion:
            lines.append("")
            lines.append(f"Suggested action: {check.suggestion}")
        lines.append("")
    lines.extend(["## Capability Passports", ""])
    if not report.capability_passports:
        lines.extend(["No Agent surfaces were discovered.", ""])
    for passport in report.capability_passports:
        agent_id = str(passport.get("agent_id") or "unknown")
        surface = str(passport.get("agent_surface") or "unknown")
        identity = passport.get("model_identity") if isinstance(passport.get("model_identity"), dict) else {}
        declared = identity.get("declared_model") if isinstance(identity.get("declared_model"), dict) else {}
        observed = identity.get("observed_model") if isinstance(identity.get("observed_model"), dict) else {}
        probe = identity.get("model_probe") if isinstance(identity.get("model_probe"), dict) else {}
        session = probe.get("session_identity") if isinstance(probe.get("session_identity"), dict) else {}
        runtime_identity = passport.get("runtime_identity") if isinstance(passport.get("runtime_identity"), dict) else {}
        layers = passport.get("capability_layers") if isinstance(passport.get("capability_layers"), dict) else {}
        skills = passport.get("skills") if isinstance(passport.get("skills"), dict) else {}
        mcp = passport.get("mcp") if isinstance(passport.get("mcp"), dict) else {}
        permissions = passport.get("permissions") if isinstance(passport.get("permissions"), dict) else {}
        context = passport.get("context") if isinstance(passport.get("context"), dict) else {}
        limitations = [str(item) for item in passport.get("known_limitations") or []]
        roles = passport.get("role_eligibility") if isinstance(passport.get("role_eligibility"), dict) else {}
        layer_summary = ", ".join(
            f"{name}={str((record or {}).get('status') or 'unknown')}"
            for name, record in layers.items()
            if isinstance(record, dict)
        ) or "unknown"
        permission_summary = "; ".join(
            f"{name}={','.join(str(item) for item in values) or 'none'}"
            for name, values in permissions.items()
            if isinstance(values, list)
        ) or "unknown"
        lines.extend(
            [
                f"### `{agent_id}` on `{surface}`",
                "",
                f"- Runtime/session: `{runtime_identity.get('runtime') or 'unknown'}` / `{runtime_identity.get('session_id') or 'unknown'}`",
                f"- Capability layers: `{layer_summary}`",
                f"- Evidence status: `{identity.get('evidence_status') or 'unknown'}`",
                f"- Declared model: `{declared.get('model_id') or 'unknown'}` via `{declared.get('provider') or 'unknown'}`; reasoning `{declared.get('reasoning_mode') or 'unknown'}`",
                f"- Observed model: `{observed.get('model_id') or 'unknown'}` via `{observed.get('provider') or 'unknown'}`; reasoning `{observed.get('reasoning_mode') or 'unknown'}`; freshness `{observed.get('freshness') or 'unknown'}`",
                f"- Model session: `{session.get('status') or 'unknown'}`; generation `{session.get('generation') or 'unknown'}`; TTL `{probe.get('ttl_seconds') or 'unknown'}` seconds",
                f"- Skills: `{', '.join(str(item) for item in skills.get('reachable') or []) or 'none observed'}`",
                f"- MCP servers: `{', '.join(str(item) for item in mcp.get('servers') or []) or 'none observed'}`",
                f"- MCP tools: `{', '.join(str(item) for item in mcp.get('tools') or []) or 'none observed'}`",
                f"- Permissions: `{permission_summary}`",
                f"- Context: `{json.dumps(context.get('current') or {}, sort_keys=True)}`",
                f"- Known limitations: `{', '.join(limitations) or 'none observed'}`",
                f"- Leader: `{roles.get('leader') or 'unknown'}`",
                f"- Implementer: `{roles.get('implementer') or 'unknown'}`",
                f"- Reviewer: `{roles.get('reviewer') or 'unknown'}`",
                f"- Researcher: `{roles.get('researcher') or 'unknown'}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def resolve_report_path(raw: str, home: Path | None = None, generated_at: str | None = None) -> Path:
    if raw != "desktop":
        return Path(raw).expanduser()
    safe_ts = (generated_at or now_iso()).replace(":", "").replace("-", "").replace("Z", "Z")
    return (home or Path.home()) / "Desktop" / f"valp-doctor-report-{safe_ts}.md"


def write_markdown_report(report: DoctorReport, raw_path: str) -> Path:
    path = resolve_report_path(raw_path, generated_at=report.generated_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_report(report), encoding="utf-8")
    return path
