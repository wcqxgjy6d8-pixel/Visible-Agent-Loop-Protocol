#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from valp_cli.workflow import publish_task, read_json


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


def main() -> int:
    baseline = read_json(ROOT / "benchmarks" / "dispatch-size-baseline.json")
    generated: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="valp-dispatch-benchmark-") as tmp:
        workspace = Path(tmp)
        with patch("valp_cli.workflow.load_local_capabilities", return_value=CAPABILITIES):
            with patch("valp_cli.workflow.skill_router_command", return_value=None):
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
                    routing = read_json(directory / "routing.json")
                    for agent, budget in (routing.get("dispatch_payload_budgets") or {}).items():
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
    return 0 if old_total > 0 and reduction > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
