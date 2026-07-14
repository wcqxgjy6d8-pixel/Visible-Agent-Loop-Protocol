from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from .control_plane import ControlPlaneError, InstallationCore, _new_id, digest_without, safe_control_ref, utc_now, write_json


def build_process_plan(root: Path, task_id: str, command: Sequence[str], *, timeout_seconds: float = 30.0) -> dict[str, Any]:
    if not command:
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Process adapter command cannot be empty")
    if timeout_seconds <= 0:
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Process adapter timeout must be positive")
    core = InstallationCore(root)
    state = core.state()
    if state["status"] not in {"active", "degraded"}:
        raise ControlPlaneError("VALP-E-STATE-CONFLICT", "Process adapter requires an active installation")
    command_digest = "sha256:" + hashlib.sha256((json.dumps(list(command), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest()
    return {
        "schema_version": "valp-process-adapter-plan.v1",
        "task_id": task_id,
        "installation_id": state["installation_id"],
        "runtime": "local-process",
        "adapter_class": "local_process",
        "worker_id": f"process-worker:{task_id}",
        "command": list(command),
        "command_digest": command_digest,
        "timeout_seconds": timeout_seconds,
        "created_at": utc_now(),
        "requires_explicit_approval": True,
    }


def run_process(root: Path, task_id: str, command: Sequence[str], *, timeout_seconds: float = 30.0, approve: bool = False) -> dict[str, Any]:
    plan = build_process_plan(root, task_id, command, timeout_seconds=timeout_seconds)
    run_id = _new_id("process-run")
    run_dir = root / "runs" / task_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    plan["run_id"] = run_id
    write_json(run_dir / "plan.json", plan)
    if not approve:
        return {"status": "dry_run", "plan": plan, "run_ref": f"runs/{task_id}/{run_id}"}
    submission = {
        "schema_version": "valp-process-submission.v1",
        "submission_id": _new_id("submission"),
        "run_id": run_id,
        "task_id": task_id,
        "installation_id": plan["installation_id"],
        "worker_id": plan["worker_id"],
        "command_digest": plan["command_digest"],
        "submitted_at": utc_now(),
        "proof_grade": "local-process",
    }
    write_json(run_dir / "submission.json", submission)
    started_at = utc_now()
    try:
        result = subprocess.run(list(command), cwd=str(root), capture_output=True, text=True, timeout=timeout_seconds, check=False)
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
        (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
        output_refs = [f"runs/{task_id}/{run_id}/stdout.txt", f"runs/{task_id}/{run_id}/stderr.txt"]
        core = InstallationCore(root)
        evidence = [
            core.add_evidence(ref, evidence_kind="runtime-output", producer_principal_id=plan["worker_id"], media_type="text/plain")
            for ref in output_refs
        ]
        status = "completed" if result.returncode == 0 else "failed"
        record = {
            "schema_version": "valp-process-adapter-run.v1",
            "run_id": run_id,
            "task_id": task_id,
            "installation_id": plan["installation_id"],
            "runtime": "local-process",
            "adapter_class": "local_process",
            "worker_id": plan["worker_id"],
            "submission_id": submission["submission_id"],
            "status": status,
            "started_at": started_at,
            "completed_at": utc_now(),
            "exit_code": result.returncode,
            "stdout_ref": output_refs[0],
            "stderr_ref": output_refs[1],
            "evidence_ids": [item["evidence_id"] for item in evidence],
            "expected_evidence_refs": output_refs,
            "failure_code": None if result.returncode == 0 else "VALP-E-RUNTIME-FAILED",
        }
    except subprocess.TimeoutExpired as error:
        (run_dir / "stdout.txt").write_text(str(error.stdout or ""), encoding="utf-8")
        (run_dir / "stderr.txt").write_text(str(error.stderr or ""), encoding="utf-8")
        record = {
            "schema_version": "valp-process-adapter-run.v1",
            "run_id": run_id,
            "task_id": task_id,
            "installation_id": plan["installation_id"],
            "runtime": "local-process",
            "adapter_class": "local_process",
            "worker_id": plan["worker_id"],
            "submission_id": submission["submission_id"],
            "status": "failed",
            "started_at": started_at,
            "completed_at": utc_now(),
            "exit_code": None,
            "stdout_ref": f"runs/{task_id}/{run_id}/stdout.txt",
            "stderr_ref": f"runs/{task_id}/{run_id}/stderr.txt",
            "evidence_ids": [],
            "expected_evidence_refs": [f"runs/{task_id}/{run_id}/stdout.txt", f"runs/{task_id}/{run_id}/stderr.txt"],
            "failure_code": "VALP-E-RUNTIME-TIMEOUT",
        }
    record["run_digest"] = digest_without(record, "run_digest")
    write_json(run_dir / "run.json", record)
    return {"status": record["status"], "run": record, "run_ref": f"runs/{task_id}/{run_id}"}
