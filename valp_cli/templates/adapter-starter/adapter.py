#!/usr/bin/env python3
from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any, Protocol


TERMINAL_RUNTIME_STATES = {"success", "error", "failed", "cancelled"}


class RuntimeClient(Protocol):
    def submit(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def get_run(self, submission_id: str) -> dict[str, Any]: ...

    def collect(self, submission_id: str) -> dict[str, Any]: ...


def expected_evidence_missing(task_dir: Path, refs: list[str]) -> list[str]:
    missing: list[str] = []
    for ref in refs:
        path = task_dir / ref
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(ref)
    return missing


def observe(
    client: RuntimeClient,
    submission: dict[str, Any],
    task_dir: Path,
    expected_refs: list[str],
    *,
    wait_seconds: float = 30.0,
    poll_interval_seconds: float = 0.1,
) -> dict[str, Any]:
    if not math.isfinite(wait_seconds) or wait_seconds < 0:
        raise ValueError("wait_seconds must be a finite non-negative number")
    if not math.isfinite(poll_interval_seconds) or poll_interval_seconds < 0:
        raise ValueError("poll_interval_seconds must be a finite non-negative number")
    submission_id = str(submission.get("submission_id") or "")
    replay_identity = str(submission.get("replay_identity") or "")
    if not submission_id or not replay_identity:
        raise ValueError("submission must include submission_id and replay_identity")

    deadline = time.monotonic() + wait_seconds
    while True:
        run = client.get_run(submission_id)
        runtime_state = str(run.get("status") or "unknown")
        if runtime_state in TERMINAL_RUNTIME_STATES:
            break
        if time.monotonic() >= deadline:
            return {
                "status": "waiting",
                "submission_id": submission_id,
                "replay_identity": replay_identity,
                "runtime_state": runtime_state,
                "worker_cancelled": False,
            }
        time.sleep(max(0.01, poll_interval_seconds))

    collected = client.collect(submission_id)
    if runtime_state != "success":
        return {
            "status": "blocked",
            "submission_id": submission_id,
            "replay_identity": replay_identity,
            "runtime_state": runtime_state,
            "failure_reason": collected.get("failure_reason") or run.get("failure_reason") or "runtime_failed",
            "worker_cancelled": runtime_state == "cancelled",
            "output_refs": collected.get("output_refs") or [],
        }

    missing_refs = expected_evidence_missing(task_dir, expected_refs)
    if missing_refs:
        return {
            "status": "blocked",
            "submission_id": submission_id,
            "replay_identity": replay_identity,
            "runtime_state": runtime_state,
            "failure_reason": {"error": "missing_expected_evidence", "refs": missing_refs},
            "worker_cancelled": False,
            "output_refs": collected.get("output_refs") or [],
        }

    return {
        "status": "completed",
        "submission_id": submission_id,
        "replay_identity": replay_identity,
        "runtime_state": runtime_state,
        "worker_cancelled": False,
        "output_refs": collected.get("output_refs") or [],
        "evidence_refs": expected_refs,
    }
