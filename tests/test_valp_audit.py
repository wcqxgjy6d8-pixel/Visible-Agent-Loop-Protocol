from pathlib import Path
import json
import shutil
import tempfile
import unittest

from valp_cli.audit import FAIL, PASS, WARN, TaskAudit


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "full-mode-task"
QUEUE_EXAMPLE = ROOT / "examples" / "headless-queue-task"


class ValpAuditTests(unittest.TestCase):
    def test_full_mode_example_passes(self) -> None:
        report = TaskAudit(EXAMPLE).run()
        self.assertEqual(report.status, PASS)
        self.assertEqual(report.fail_count, 0)

    def test_headless_queue_preflight_without_terminal_size_passes_audit(self) -> None:
        report = TaskAudit(QUEUE_EXAMPLE).run()
        self.assertEqual(report.status, PASS)
        self.assertEqual(report.fail_count, 0)
        preflight_text = (QUEUE_EXAMPLE / "runtime-preflight.json").read_text(encoding="utf-8")
        self.assertNotIn("terminal_size_status", preflight_text)
        self.assertNotIn("pane_id", preflight_text)

    def test_pane_runtime_terminal_size_fail_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            routing = json.loads((task / "routing.json").read_text(encoding="utf-8"))
            preflight = routing["provider_matrix"]["runtime_preflight"]
            preflight["status"] = "fail"
            preflight["agents"]["codex"]["status"] = "fail"
            preflight["agents"]["codex"]["terminal_size_status"] = "fail"
            routing["runtime_adapter"]["preflight"]["status"] = "fail"
            routing["runtime_adapter"]["preflight"]["agents"]["codex"]["status"] = "fail"
            routing["runtime_adapter"]["preflight"]["agents"]["codex"]["terminal_size_status"] = "fail"
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "runtime_preflight" and item.status == FAIL for item in report.items))

    def test_missing_expected_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "agents" / "codex" / "evidence.md").unlink()

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "expected_evidence" and item.status == FAIL for item in report.items))

    def test_later_blocked_receipt_supersedes_completed_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            blocked = {
                "ts": "2026-07-03T00:07:00Z",
                "agent": "claude",
                "event": "dispatch_blocked",
                "dispatch_ref": "agents/claude/dispatch.md",
                "expected_refs": ["agents/claude/review.md"],
                "summary": "Later retry failed to prove expected evidence",
            }
            with (task / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(blocked) + "\n")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "dispatch_receipts" and item.status == FAIL for item in report.items))

    def test_invalid_expected_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            status = {
                "schema_version": "valp-evidence-status.v1",
                "evidence": {
                    "agents/codex/evidence.md": {
                        "status": "superseded",
                        "reason": "A later retry replaced this evidence.",
                    }
                },
            }
            (task / "evidence-status.json").write_text(json.dumps(status), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "expected_evidence" and item.status == FAIL for item in report.items))

    def test_unsupported_runtime_claim_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "agents" / "codex" / "evidence.md").write_text(
                "# Codex Evidence\n\nBuild passed and tests passed.\n",
                encoding="utf-8",
            )

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "claim_evidence" and item.status == FAIL for item in report.items))

    def test_skill_router_not_run_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            routing = json.loads((task / "routing.json").read_text(encoding="utf-8"))
            routing["skill_recommendations"] = {"status": "not_run"}
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "skill_recommendations" and item.status == FAIL for item in report.items))

    def test_skill_router_failed_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            routing = json.loads((task / "routing.json").read_text(encoding="utf-8"))
            routing["skill_recommendations"] = {"status": "failed"}
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, WARN)
            self.assertTrue(any(item.id == "skill_recommendations" and item.status == WARN for item in report.items))

    def test_missing_visible_attention_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            for name in [
                "attention-map.json",
                "context-selection.json",
                "mask-list.json",
                "evidence-board.json",
                "visible-routing.md",
            ]:
                (task / name).unlink()

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "visible_attention" and item.status == FAIL for item in report.items))

    def test_manual_mode_accepts_manual_result_attested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "manual-task"
            (task / "agents" / "manual-reviewer").mkdir(parents=True)
            (task / "task.md").write_text("# Task\n\n## Goal\n\nManual review.\n", encoding="utf-8")
            state = {
                "schema_version": "valp-visible-loop-state.v1",
                "task_id": "MANUAL-001",
                "profile": "generic-analysis",
                "status": "done",
                "runtime_adapter": {"class": "manual", "name": "manual"},
                "runtime_task_state_mapping": {"completed": "manual_result_attested_with_expected_evidence"},
                "provider_matrix": {"status": "scanned", "ref": "routing.json"},
                "selected_agents": ["manual-reviewer"],
                "context_policies": {
                    "manual-reviewer": {
                        "soft_warning_pct": 60,
                        "hard_compression_pct": 70,
                        "emergency_stop_pct": 80,
                    }
                },
                "skill_recommendations": {
                    "status": "no_matches",
                    "backend": "manual",
                    "ref": "skill-recommendations.json",
                },
                "gates": {
                    "verification": "not_required",
                    "review": "passed",
                    "approval": "not_required",
                },
                "approval_required": [],
            }
            routing = {
                "schema_version": "valp-capability-routing.v1",
                "task_id": "MANUAL-001",
                "profile": "generic-analysis",
                "runtime_adapter": {"class": "manual", "name": "manual"},
                "runtime_task_state_mapping": {"completed": "manual_result_attested_with_expected_evidence"},
                "selected_agents": ["manual-reviewer"],
                "selected_agent_context_policies": state["context_policies"],
                "capabilities_missing": [],
                "routing_confidence": {"overall": "medium"},
                "candidate_scores": {
                    "manual-reviewer": {
                        "overall": 0.7,
                        "confidence": "medium",
                    }
                },
                "provider_matrix": {
                    "providers": {
                        "manual-reviewer": {
                            "provider_name": "manual-reviewer",
                            "provider_version_or_runtime_report": "manual",
                            "cli_available": False,
                            "mcp_support": "unknown",
                            "skill_discovery_path": "none",
                            "session_resume_support": "not_applicable",
                            "approval_behavior": "manual_attestation",
                            "model_selection": "manual",
                            "max_concurrency": 1,
                            "context_policy": state["context_policies"]["manual-reviewer"],
                            "runtime_preflight": {"status": "not_applicable"},
                            "known_limitations": ["no runtime proof"],
                        }
                    }
                },
                "skill_recommendations": {
                    "status": "no_matches",
                    "backend": "manual",
                    "ref": "skill-recommendations.json",
                },
            }
            receipt = {
                "ts": "2026-07-05T00:00:00Z",
                "agent": "manual-reviewer",
                "event": "manual_result_attested",
                "dispatch_ref": "agents/manual-reviewer/dispatch.md",
                "expected_refs": ["agents/manual-reviewer/review.md"],
                "summary": "Human attested that the manual review evidence exists.",
            }
            recommendations = {
                "schema_version": "valp-skill-recommendations.v1",
                "status": "no_matches",
                "backend": "manual",
                "results": [],
                "missing_skills": [],
            }
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")
            (task / "skill-recommendations.json").write_text(json.dumps(recommendations), encoding="utf-8")
            (task / "dispatch-receipts.jsonl").write_text(json.dumps(receipt) + "\n", encoding="utf-8")
            (task / "agents" / "manual-reviewer" / "review.md").write_text(
                "# Review\n\nManual review evidence recorded in `agents/manual-reviewer/review.md`.\n",
                encoding="utf-8",
            )
            (task / "final-synthesis.md").write_text(
                "# Final Synthesis\n\nResult: done.\nDecision: accept.\nDisagreement: none.\nEvidence gap: no runtime proof in Manual Mode.\n",
                encoding="utf-8",
            )

            report = TaskAudit(task).run()
            self.assertEqual(report.status, PASS)
            self.assertTrue(any(item.id == "dispatch_receipts" and item.status == PASS for item in report.items))


if __name__ == "__main__":
    unittest.main()
