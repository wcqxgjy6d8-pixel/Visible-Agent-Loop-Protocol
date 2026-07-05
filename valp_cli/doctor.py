from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .audit import WARN as AUDIT_WARN, TaskAudit, resolve_task_dir
from .workflow import collect_runtime_preflight


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
    checks: list[DoctorCheck] = []
    checks.extend(git_checks(workspace))
    checks.extend(install_checks(workspace))
    checks.extend(syntax_checks(workspace))
    checks.extend(example_audit_checks(workspace))
    checks.extend(runtime_checks())
    if task_id:
        checks.append(task_audit_check(workspace, task_id))

    pass_count = sum(1 for check in checks if check.status == PASS)
    warn_count = sum(1 for check in checks if check.status == WARN)
    fail_count = sum(1 for check in checks if check.status == FAIL)
    status = FAIL if fail_count else WARN if warn_count else PASS
    return DoctorReport(
        workspace=str(workspace),
        generated_at=now_iso(),
        status=status,
        pass_count=pass_count,
        warn_count=warn_count,
        fail_count=fail_count,
        checks=checks,
    )


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
    checks.append(
        make_check(
            "runtime_herdr",
            "HERDR reference runtime is available",
            PASS if status == PASS else WARN,
            f"HERDR preflight status: {status}; command={herdr_path}.",
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
