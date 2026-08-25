#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from valp_cli.workflow import (
    collect_runtime_preflight,
    publish_task,
    read_json,
    route_task,
)


CAPABILITIES = {
    "schema_version": "valp-agent-capabilities.v1",
    "updated_at": "2026-07-11T00:00:00Z",
    "source": "dispatch benchmark fixture",
    "agents": {
        "codex": {
            "active": True,
            "role": ["coordination", "implementation", "verification"],
            "skills": [],
            "mcp_servers": [],
            "strengths": ["edits files", "runs tests"],
            "must_not_do": ["must not bypass approval gates"],
        },
        "claude": {
            "active": True,
            "role": ["review", "code_review", "risk_review"],
            "skills": [],
            "mcp_servers": [],
            "strengths": ["reviews source and evidence"],
            "must_not_do": ["must not edit source"],
        },
    },
}

EXPECTED_FILES = {
    "full-mode/codex",
    "full-mode/claude",
    "headless-queue/codex",
    "headless-queue/claude",
}


def assignment_declaration(task_id: str) -> dict[str, object]:
    return {
        "schema_version": "valp-assignment-declaration.v1",
        "declaration_id": f"dispatch-benchmark-{task_id}",
        "task_id": task_id,
        "declared_at": "2026-07-23T10:00:00Z",
        "leader": {
            "agent_id": "codex",
            "selected_by": "user",
            "selection_ref": f"dispatch-benchmark-user-selection:{task_id}",
        },
        "assignments": {
            "coordinator": "codex",
            "implementer": "codex",
            "reviewer": "claude",
        },
        "reasons": {
            "coordinator": "The benchmark fixture explicitly assigns visible coordination.",
            "implementer": "The benchmark fixture explicitly assigns implementation.",
            "reviewer": "The benchmark fixture explicitly assigns independent review.",
        },
    }


def model_aware_queue_preflight(
    agent_names: list[str] | None = None,
    runtime: str | None = None,
    launch_argv_by_agent: dict[str, list[str]] | None = None,
    version_command_by_agent: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    preflight = collect_runtime_preflight(
        agent_names,
        runtime=runtime,
        launch_argv_by_agent=launch_argv_by_agent,
        version_command_by_agent=version_command_by_agent,
    )
    observed_at = datetime.now(timezone.utc).isoformat()
    for agent, record in (preflight.get("agents") or {}).items():
        record["model_probe"] = {
            "schema_version": "valp-model-probe.v1",
            "status": "observed",
            "source": "dispatch benchmark fixture",
            "observed_at": observed_at,
            "ttl_seconds": 3600,
            "model": {
                "model_id": f"benchmark-model-{agent}",
                "provider": "benchmark-provider",
                "reasoning_mode": "standard",
                "confidence": "high",
            },
            "session_identity": {
                "status": "known",
                "token": "sha256:"
                + hashlib.sha256(
                    f"dispatch-benchmark:{agent}".encode("utf-8")
                ).hexdigest(),
                "source": "dispatch benchmark fixture",
                "generation": "1",
            },
        }
    return preflight


def main() -> int:
    baseline = read_json(ROOT / "benchmarks" / "dispatch-size-baseline.json")
    generated: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="valp-dispatch-benchmark-") as tmp:
        workspace = Path(tmp)
        with patch("valp_cli.workflow.load_local_capabilities", return_value=CAPABILITIES):
            with patch("valp_cli.workflow.skill_router_command", return_value=None):
                with patch(
                    "valp_cli.workflow.collect_runtime_preflight",
                    side_effect=model_aware_queue_preflight,
                ):
                    for label, task_id in [
                        ("full-mode", "TASK-EXAMPLE-001"),
                        ("headless-queue", "TASK-QUEUE-001"),
                    ]:
                        directory = publish_task(
                            workspace,
                            task_id,
                            "Fix the synthetic failing test, verify it, and review receipt semantics.",
                            runtime="queue",
                        )
                        route_task(
                            workspace,
                            task_id,
                            runtime="queue",
                            assignment_declaration=assignment_declaration(task_id),
                        )
                        routing = read_json(directory / "routing.json")
                        for agent, budget in (
                            routing.get("dispatch_payload_budgets") or {}
                        ).items():
                            generated[f"{label}/{agent}"] = int(budget["actual_chars"])

    old_total = int(baseline.get("total_chars") or 0)
    new_total = sum(generated.values())
    reduction = old_total - new_total
    report = {
        "schema_version": "valp-dispatch-size-benchmark.v1",
        "old_total_chars": old_total,
        "new_total_chars": new_total,
        "reduction_chars": reduction,
        "reduction_percent": round((reduction / old_total) * 100, 2) if old_total else 0,
        "old_files": baseline.get("files") or {},
        "new_files": generated,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    complete = set(generated) == EXPECTED_FILES and all(
        generated[path] > 0 for path in EXPECTED_FILES
    )
    return 0 if complete and old_total > 0 and reduction > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
