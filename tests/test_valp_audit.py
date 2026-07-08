from pathlib import Path
import json
import shutil
import tempfile
import unittest
from jsonschema import Draft202012Validator

from valp_cli.audit import FAIL, PASS, WARN, TaskAudit


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "full-mode-task"
QUEUE_EXAMPLE = ROOT / "examples" / "headless-queue-task"
REAL_DOC_EXAMPLE = ROOT / "examples" / "real-doc-calibration-task"


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

    def test_real_documentation_calibration_case_study_passes(self) -> None:
        report = TaskAudit(REAL_DOC_EXAMPLE).run()
        self.assertEqual(report.status, PASS)
        self.assertEqual(report.fail_count, 0)

    def test_research_profile_requires_visible_attention_even_single_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(ROOT / "examples" / "minimal-task", task)
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            routing = json.loads((task / "routing.json").read_text(encoding="utf-8"))
            state["profile"] = "research"
            routing["profile"] = "research"
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "visible_attention" and item.status == FAIL for item in report.items))

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

    def test_missing_expected_evidence_refs_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(ROOT / "examples" / "minimal-task", task)
            receipts_path = task / "dispatch-receipts.jsonl"
            receipts = [
                json.loads(line)
                for line in receipts_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for receipt in receipts:
                receipt["expected_refs"] = []
            receipts_path.write_text(
                "".join(json.dumps(receipt) + "\n" for receipt in receipts),
                encoding="utf-8",
            )
            (task / "task.md").write_text(
                "# Task\n\n## Goal\n\nManual review.\n\n## Expected Evidence\n\nGenerated during routing.\n",
                encoding="utf-8",
            )

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(
                any(
                    item.id == "expected_evidence"
                    and item.status == FAIL
                    and "No expected evidence refs found" in item.message
                    for item in report.items
                )
            )

    def test_unsafe_expected_evidence_ref_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            receipts_path = task / "dispatch-receipts.jsonl"
            receipts = [
                json.loads(line)
                for line in receipts_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for receipt in receipts:
                if receipt.get("agent") == "codex":
                    receipt["expected_refs"] = ["../outside.md"]
            receipts_path.write_text(
                "".join(json.dumps(receipt) + "\n" for receipt in receipts),
                encoding="utf-8",
            )

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(
                any(
                    item.id == "expected_evidence"
                    and item.status == FAIL
                    and "task-relative safe paths" in item.message
                    for item in report.items
                )
            )

    def test_corrupt_dispatch_receipt_jsonl_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            with (task / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{bad json\n")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(
                any(
                    item.id == "dispatch_receipts"
                    and item.status == FAIL
                    and "Invalid dispatch receipt ledger" in item.message
                    for item in report.items
                )
            )

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

    def test_full_mode_completion_without_runtime_submission_proof_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            receipts_path = task / "dispatch-receipts.jsonl"
            receipts = [
                json.loads(line)
                for line in receipts_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            receipts = [receipt for receipt in receipts if receipt.get("event") != "dispatch_submitted"]
            receipts_path.write_text(
                "".join(json.dumps(receipt) + "\n" for receipt in receipts),
                encoding="utf-8",
            )

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(
                any(
                    item.id == "dispatch_receipts"
                    and item.status == FAIL
                    and "missing runtime submission proof" in item.message
                    for item in report.items
                )
            )

    def test_dry_run_submission_proof_does_not_satisfy_full_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            receipts_path = task / "dispatch-receipts.jsonl"
            receipts = []
            for line in receipts_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                receipt = json.loads(line)
                if receipt.get("event") == "dispatch_submitted":
                    receipt["proof"] = {"mode": "dry_run", "note": "printed command only"}
                receipts.append(receipt)
            receipts_path.write_text(
                "".join(json.dumps(receipt) + "\n" for receipt in receipts),
                encoding="utf-8",
            )

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

    def test_missing_correction_cycle_fails_when_evidence_was_superseded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "correction-cycle.json").unlink()

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "correction_cycle" and item.status == FAIL for item in report.items))

    def test_correction_cycle_passes_when_superseded_evidence_was_fixed(self) -> None:
        report = TaskAudit(EXAMPLE).run()
        self.assertEqual(report.status, PASS)
        self.assertTrue(any(item.id == "correction_cycle" and item.status == PASS for item in report.items))

    def test_missing_agent_recommendations_fails_for_non_trivial_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "agent-recommendations.json").unlink()

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(
                any(
                    item.id == "agent_recommendations"
                    and item.status == FAIL
                    and "Missing agent-recommendations.json" in item.message
                    for item in report.items
                )
            )

    def test_pending_agent_recommendation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            recommendations = json.loads((task / "agent-recommendations.json").read_text(encoding="utf-8"))
            recommendations["status"] = "pending"
            recommendations["entries"][0]["decision_status"] = "pending"
            (task / "agent-recommendations.json").write_text(json.dumps(recommendations), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "agent_recommendations" and item.status == FAIL for item in report.items))

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

    def test_backtick_marker_without_existing_evidence_does_not_support_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "agents" / "codex" / "evidence.md").write_text(
                "# Codex Evidence\n\nBuild passed and tests passed. See `foo`.\n",
                encoding="utf-8",
            )

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "claim_evidence" and item.status == FAIL for item in report.items))

    def test_existing_evidence_path_supports_runtime_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "agents" / "codex" / "evidence.md").write_text(
                "# Codex Evidence\n\nBuild passed and tests passed. See `evidence/verification.md`.\n",
                encoding="utf-8",
            )

            report = TaskAudit(task).run()
            self.assertNotEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "claim_evidence" and item.status == PASS for item in report.items))

    def test_final_synthesis_runtime_claim_without_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(ROOT / "examples" / "minimal-task", task)
            (task / "final-synthesis.md").write_text(
                "# Final Synthesis\n\n"
                "Result: done. Tests passed and build passed.\n"
                "Decision: accept.\n"
                "Disagreement: none.\n"
                "Evidence gap: none.\n",
                encoding="utf-8",
            )

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(
                any(
                    item.id == "claim_evidence"
                    and item.status == FAIL
                    and "final-synthesis.md" in item.message
                    for item in report.items
                )
            )

    def test_pending_approval_ledger_fails_even_when_state_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            approvals_dir = task / "approvals"
            approvals_dir.mkdir()
            request = {
                "request_id": "deploy-prod",
                "kind": "deploy",
                "scope": "production",
                "status": "pending",
            }
            (approvals_dir / "requested.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "approvals" and item.status == FAIL for item in report.items))

    def test_approved_approval_ledger_resolves_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            approvals_dir = task / "approvals"
            approvals_dir.mkdir()
            request = {
                "request_id": "deploy-prod",
                "kind": "deploy",
                "scope": "production",
                "status": "pending",
            }
            decision = {
                "request_id": "deploy-prod",
                "decision": "approved",
                "approved_by": "operator",
            }
            (approvals_dir / "requested.jsonl").write_text(json.dumps(request) + "\n", encoding="utf-8")
            (approvals_dir / "user-decisions.jsonl").write_text(json.dumps(decision) + "\n", encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertNotEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "approvals" and item.status == PASS for item in report.items))

    def test_high_risk_goal_without_approval_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "task.md").write_text(
                "# Task\n\n## Goal\n\nDeploy the release to production and rotate secrets.\n",
                encoding="utf-8",
            )
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            state["gates"]["approval"] = "not_required"
            state["approval_required"] = []
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "approvals" and item.status == FAIL for item in report.items))

    def test_verification_passed_requires_concrete_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            state["gates"]["verification"] = "passed"
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task / "evidence" / "verification.md").unlink()
            receipts = [
                json.loads(line)
                for line in (task / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            for receipt in receipts:
                refs = receipt.get("expected_refs") or []
                receipt["expected_refs"] = [ref for ref in refs if ref != "evidence/verification.md"]
            (task / "dispatch-receipts.jsonl").write_text(
                "".join(json.dumps(receipt) + "\n" for receipt in receipts),
                encoding="utf-8",
            )

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "verification" and item.status == FAIL for item in report.items))

    def test_manual_receipts_match_receipt_schema(self) -> None:
        schema = json.loads((ROOT / "schemas" / "receipts.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        receipt_path = ROOT / "examples" / "minimal-task" / "dispatch-receipts.jsonl"
        errors = []
        for line in receipt_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                errors.extend(validator.iter_errors(json.loads(line)))
        self.assertEqual(errors, [])

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
