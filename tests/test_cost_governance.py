from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from valp_cli.audit import FAIL, TaskAudit
from valp_cli.cost_governance import (
    CostGovernanceError,
    append_event,
    build_cost_report,
    enforce_cost_budget,
    estimate_usage,
)


class CostGovernanceTests(unittest.TestCase):
    def snapshot(self) -> dict[str, object]:
        return {
            "schema_version": "valp-pricing-snapshot.v1",
            "snapshot_id": "pricing-20260809",
            "provider": "provider-neutral",
            "model": "model-a",
            "currency": "USD",
            "effective_at": "2026-08-09T00:00:00Z",
            "rates_per_million_tokens": {
                "official_list": {"input": "2.00", "output": "8.00"},
                "relay_account": {"input": "1.50", "output": "6.00"},
                "subscription_marginal": {"input": "0.00", "output": "0.00"},
            },
        }

    def test_estimate_keeps_price_categories_distinct(self) -> None:
        estimate = estimate_usage(self.snapshot(), 1_000_000, 500_000)
        self.assertEqual(estimate["official_list"], "6.000000")
        self.assertEqual(estimate["relay_account"], "4.500000")
        self.assertEqual(estimate["subscription_marginal"], "0.000000")

    def test_report_is_deterministic_and_does_not_invent_actual_cost(self) -> None:
        usage = [{
            "schema_version": "valp-usage-event.v1", "event_id": "usage-1", "task_id": "TASK-COST",
            "agent": "codex", "snapshot_id": "pricing-20260809", "input_tokens": 1_000_000,
            "output_tokens": 500_000, "provider": "provider-neutral", "model": "model-a",
            "currency": "USD", "observed_at": "2026-08-09T00:01:00Z",
        }]
        report = build_cost_report("TASK-COST", [self.snapshot()], usage, [], [{
            "agent": "codex", "snapshot_id": "pricing-20260809", "input_tokens": 100_000, "output_tokens": 0,
        }])
        self.assertEqual(report["actual_billed"], None)
        self.assertEqual(report["actual_billed_status"], "not_available")
        self.assertEqual(report["accumulated_estimate"]["official_list"], "6.000000")
        self.assertEqual(report["projected_estimate"]["official_list"], "6.200000")
        self.assertEqual(report["per_agent"]["codex"]["accumulated_estimate"]["relay_account"], "4.500000")

    def test_identical_event_replay_is_idempotent_but_conflict_fails_closed(self) -> None:
        event = {
            "schema_version": "valp-usage-event.v1", "event_id": "usage-1", "task_id": "TASK-COST",
            "agent": "codex", "snapshot_id": "pricing-20260809", "input_tokens": 1, "output_tokens": 1,
            "provider": "provider-neutral", "model": "model-a", "currency": "USD",
            "observed_at": "2026-08-09T00:01:00Z",
        }
        replayed = build_cost_report("TASK-COST", [self.snapshot()], [event, dict(event)], [], [])
        self.assertEqual(replayed["accumulated_estimate"]["official_list"], "0.000010")
        conflicting = {**event, "output_tokens": 2}
        with self.assertRaisesRegex(CostGovernanceError, "conflicting usage event_id"):
            build_cost_report("TASK-COST", [self.snapshot()], [event, conflicting], [], [])

    def test_usage_must_match_snapshot_identity_and_effective_time(self) -> None:
        event = {
            "schema_version": "valp-usage-event.v1",
            "event_id": "usage-identity",
            "task_id": "TASK-COST",
            "agent": "codex",
            "snapshot_id": "pricing-20260809",
            "provider": "provider-neutral",
            "model": "wrong-model",
            "currency": "USD",
            "observed_at": "2026-08-09T00:01:00Z",
            "input_tokens": 1,
            "output_tokens": 1,
        }
        with self.assertRaisesRegex(CostGovernanceError, "model"):
            build_cost_report("TASK-COST", [self.snapshot()], [event], [], [])

    def test_billing_requires_evidence_reference(self) -> None:
        with self.assertRaisesRegex(CostGovernanceError, "billing_evidence_ref"):
            build_cost_report("TASK-COST", [self.snapshot()], [], [{
                "schema_version": "valp-billing-event.v1", "event_id": "bill-1", "task_id": "TASK-COST",
                "agent": "codex", "currency": "USD", "amount": "1.00",
            }], [])

    def test_append_event_replay_is_no_op_but_conflict_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage-events.jsonl"
            event = {"schema_version": "valp-usage-event.v1", "event_id": "usage-1"}
            append_event(path, event, "valp-usage-event.v1")
            append_event(path, dict(event), "valp-usage-event.v1")
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)
            with self.assertRaisesRegex(CostGovernanceError, "conflicting event_id"):
                append_event(path, {**event, "input_tokens": 1}, "valp-usage-event.v1")

    def test_audit_fails_actual_claim_without_billing_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            (task / "state.json").write_text(json.dumps({"task_id": "TASK-COST"}), encoding="utf-8")
            (task / "pricing-snapshots.json").write_text(json.dumps({"schema_version": "valp-pricing-snapshots.v1", "task_id": "TASK-COST", "snapshots": [self.snapshot()]}), encoding="utf-8")
            (task / "usage-events.jsonl").write_text(json.dumps({
                "schema_version": "valp-usage-event.v1", "event_id": "usage-1", "task_id": "TASK-COST",
                "agent": "codex", "snapshot_id": "pricing-20260809", "input_tokens": 1, "output_tokens": 1,
                "provider": "provider-neutral", "model": "model-a", "currency": "USD",
                "observed_at": "2026-08-09T00:01:00Z",
            }) + "\n", encoding="utf-8")
            (task / "cost-budget.json").write_text(json.dumps({
                "schema_version": "valp-cost-budget.v1", "task_id": "TASK-COST", "planned_usage": [],
            }), encoding="utf-8")
            (task / "cost-report.json").write_text(json.dumps({"actual_billed": "1.000000"}), encoding="utf-8")
            result = TaskAudit(task).check_cost_governance()
            self.assertEqual(result.status, FAIL)
            self.assertIn("billing evidence", result.message)

    def test_pre_dispatch_gate_blocks_projected_overrun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task = Path(directory)
            (task / "state.json").write_text(
                json.dumps({"task_id": "TASK-COST"}), encoding="utf-8"
            )
            (task / "pricing-snapshots.json").write_text(
                json.dumps({
                    "schema_version": "valp-pricing-snapshots.v1",
                    "task_id": "TASK-COST",
                    "snapshots": [self.snapshot()],
                }),
                encoding="utf-8",
            )
            (task / "usage-events.jsonl").write_text("", encoding="utf-8")
            (task / "cost-budget.json").write_text(
                json.dumps({
                    "schema_version": "valp-cost-budget.v1",
                    "task_id": "TASK-COST",
                    "max_projected_official_list": "0.10",
                    "planned_usage": [{
                        "agent": "codex",
                        "snapshot_id": "pricing-20260809",
                        "input_tokens": 100_000,
                        "output_tokens": 0,
                    }],
                }),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(CostGovernanceError, "hard cap"):
                enforce_cost_budget(task, "TASK-COST")

    def test_report_attributes_retry_and_subagent_usage(self) -> None:
        base = {
            "schema_version": "valp-usage-event.v1",
            "task_id": "TASK-COST",
            "snapshot_id": "pricing-20260809",
            "provider": "provider-neutral",
            "model": "model-a",
            "currency": "USD",
            "observed_at": "2026-08-09T00:01:00Z",
            "input_tokens": 100,
            "output_tokens": 0,
        }
        usage = [
            {**base, "event_id": "usage-parent", "agent": "codex", "dispatch_id": "dispatch-1", "work_item_id": "implementer:codex"},
            {**base, "event_id": "usage-retry", "agent": "codex", "dispatch_id": "dispatch-1", "work_item_id": "implementer:codex", "retry_of": "usage-parent"},
            {**base, "event_id": "usage-child", "agent": "qwen", "dispatch_id": "dispatch-2", "work_item_id": "researcher:qwen", "parent_agent": "codex"},
        ]

        report = build_cost_report("TASK-COST", [self.snapshot()], usage, [], [])

        self.assertEqual(len(report["usage_attribution"]), 3)
        self.assertEqual(report["usage_attribution"][1]["retry_of"], "usage-parent")
        self.assertEqual(report["usage_attribution"][2]["parent_agent"], "codex")
        self.assertIn("qwen", report["per_agent"])

    def test_bundled_cost_governance_example_passes_cost_audit(self) -> None:
        task = Path(__file__).parents[1] / "examples" / "cost-governance-task"
        audit = TaskAudit(task)
        audit.state = {"task_id": "TASK-COST-001"}

        result = audit.check_cost_governance()

        self.assertNotEqual(result.status, FAIL, result.message)
