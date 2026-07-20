from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from valp_cli.langgraph_adapter import LangGraphAdapterError, resume_langgraph_run, submit_langgraph_run


ROOT = Path(__file__).resolve().parents[1]


class LangGraphAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.task_id = "TASK-LANGGRAPH"
        self.task_dir = self.workspace / ".herdr-loop" / "tasks" / self.task_id
        (self.task_dir / "agents" / "langgraph_worker").mkdir(parents=True)
        (self.task_dir / "agents" / "langgraph_worker" / "dispatch.md").write_text(
            "# Dispatch\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def receipts(self) -> list[dict]:
        return [
            json.loads(line)
            for line in (self.task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
        ]

    def test_runtime_success_without_evidence_blocks_then_repair_completes(self) -> None:
        run_ids = iter(["run-false-done", "run-repair"])

        def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
            if method == "POST" and path == "/threads":
                return {"thread_id": "thread-1"}
            if method == "POST" and path.endswith("/runs"):
                run_id = next(run_ids)
                return {
                    "run_id": run_id,
                    "thread_id": "thread-1",
                    "assistant_id": "assistant-worker",
                    "status": "pending",
                }
            if method == "GET" and path.endswith("/join"):
                return {"claim": "report generated"}
            if method == "GET" and path.endswith("/state"):
                return {"checkpoint": {"thread_id": "thread-1", "checkpoint_id": "checkpoint-1"}}
            if method == "GET" and "/runs/" in path:
                return {"status": "success", "updated_at": "2026-07-20T00:00:00Z"}
            raise AssertionError((method, path, payload))

        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request):
            first = submit_langgraph_run(
                self.workspace,
                self.task_id,
                "langgraph_worker",
                "implementer",
                expected_refs=["evidence/report.md"],
            )
            self.assertEqual(first["status"], "blocked")
            self.assertEqual(first["run"]["runtime_status"], "success")
            self.assertEqual(first["run"]["missing_refs"], ["evidence/report.md"])

            (self.task_dir / "evidence").mkdir()
            (self.task_dir / "evidence" / "report.md").write_text("verified report\n", encoding="utf-8")
            second = submit_langgraph_run(
                self.workspace,
                self.task_id,
                "langgraph_worker",
                "implementer",
                expected_refs=["evidence/report.md"],
                thread_id="thread-1",
                input_data={"attempt": "repair"},
            )

        self.assertEqual(second["status"], "completed")
        receipts = self.receipts()
        self.assertEqual(
            [receipt["event"] for receipt in receipts],
            ["dispatch_submitted", "dispatch_blocked", "dispatch_submitted", "dispatch_completed"],
        )
        self.assertEqual(receipts[0]["proof"]["adapter_record"]["submission_id"], "run-false-done")
        self.assertEqual(receipts[2]["proof"]["adapter_record"]["submission_id"], "run-repair")
        self.assertEqual(receipts[1]["suspension_epoch"], 1)
        self.assertEqual(receipts[3]["suspension_epoch"], 2)

    def test_pause_window_expiry_keeps_job_alive_and_resume_uses_same_run(self) -> None:
        runtime_status = {"value": "pending"}

        def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
            if method == "POST" and path == "/threads":
                return {"thread_id": "thread-slow"}
            if method == "POST" and path.endswith("/runs"):
                return {
                    "run_id": "run-slow",
                    "thread_id": "thread-slow",
                    "assistant_id": "assistant-worker",
                    "status": "pending",
                }
            if method == "GET" and path.endswith("/join"):
                return {"claim": "report generated"}
            if method == "GET" and path.endswith("/state"):
                return {"checkpoint": {"thread_id": "thread-slow", "checkpoint_id": "checkpoint-slow"}}
            if method == "GET" and "/runs/" in path:
                return {"status": runtime_status["value"], "updated_at": "2026-07-20T00:00:00Z"}
            raise AssertionError((method, path, payload))

        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request):
            waiting = submit_langgraph_run(
                self.workspace,
                self.task_id,
                "langgraph_worker",
                "implementer",
                expected_refs=["evidence/report.md"],
                wait_seconds=0,
            )
            self.assertEqual(waiting["status"], "waiting")
            self.assertFalse(waiting["run"]["worker_cancelled"])
            self.assertEqual([item["event"] for item in self.receipts()], ["dispatch_submitted"])

            (self.task_dir / "evidence").mkdir()
            (self.task_dir / "evidence" / "report.md").write_text("late report\n", encoding="utf-8")
            runtime_status["value"] = "success"
            resumed = resume_langgraph_run(
                self.workspace,
                self.task_id,
                "run-slow",
                wait_seconds=0,
            )

        self.assertEqual(resumed["status"], "completed")
        self.assertEqual([item["event"] for item in self.receipts()], ["dispatch_submitted", "dispatch_completed"])
        self.assertEqual(resumed["receipt"]["proof"]["adapter_record"]["submission_id"], "run-slow")

    def test_runtime_error_records_join_failure_reason(self) -> None:
        def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
            if method == "POST" and path == "/threads":
                return {"thread_id": "thread-error"}
            if method == "POST" and path.endswith("/runs"):
                return {
                    "run_id": "run-error",
                    "thread_id": "thread-error",
                    "assistant_id": "assistant-worker",
                    "status": "pending",
                }
            if method == "GET" and path.endswith("/join"):
                return {"__error__": {"error": "RuntimeError", "message": "worker failed"}}
            if method == "GET" and path.endswith("/state"):
                return {"checkpoint": {"thread_id": "thread-error", "checkpoint_id": "checkpoint-error"}}
            if method == "GET" and "/runs/" in path:
                return {"status": "error", "updated_at": "2026-07-20T00:00:00Z"}
            raise AssertionError((method, path, payload))

        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request):
            result = submit_langgraph_run(
                self.workspace,
                self.task_id,
                "langgraph_worker",
                "implementer",
                expected_refs=["evidence/report.md"],
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["run"]["failure_reason"]["message"], "worker failed")
        self.assertEqual(self.receipts()[-1]["proof"]["failure_reason"]["error"], "RuntimeError")

    def test_invalid_wait_windows_are_rejected_before_submission(self) -> None:
        for wait_seconds in (-1, float("nan"), float("inf")):
            with self.subTest(wait_seconds=wait_seconds):
                with self.assertRaisesRegex(LangGraphAdapterError, "finite non-negative"):
                    submit_langgraph_run(
                        self.workspace,
                        self.task_id,
                        "langgraph_worker",
                        "implementer",
                        wait_seconds=wait_seconds,
                    )


class LangGraphPublishedCaseTests(unittest.TestCase):
    def test_conformance_report_is_complete_and_all_evidence_refs_exist(self) -> None:
        case_dir = ROOT / "examples" / "langgraph-false-done"
        report = json.loads((case_dir / "conformance.json").read_text(encoding="utf-8"))
        conformance = report["conformance"]
        checks = conformance["checks"]

        self.assertEqual(report["schema_version"], "valp-adapter-conformance-report.v1")
        self.assertEqual(report["case_id"], "VALP-NON-HERDR-E2E-001")
        self.assertFalse(report["adapter"]["herdr_used"])
        self.assertFalse(conformance["normative"])
        self.assertEqual(conformance["checks_total"], len(checks))
        self.assertEqual(conformance["checks_passed"], len(checks))
        self.assertEqual(
            {check["id"] for check in checks},
            {
                "run_thread_id",
                "submission_proof",
                "runtime_state",
                "output_evidence_refs",
                "failure_reason",
                "restart_replay_identity",
            },
        )
        for check in checks:
            with self.subTest(check=check["id"]):
                self.assertEqual(check["status"], "pass")
                evidence_ref = Path(check["evidence_ref"])
                self.assertFalse(evidence_ref.is_absolute())
                self.assertNotIn("..", evidence_ref.parts)
                self.assertTrue((case_dir / evidence_ref).is_file(), check["evidence_ref"])

        acceptance = report["acceptance"]
        self.assertTrue(acceptance["first_failure_preserved"])
        self.assertFalse(acceptance["dispatch_completed_manually_fabricated"])
        self.assertTrue(acceptance["independent_review"])
        self.assertTrue(acceptance["final_synthesis"])
        self.assertEqual(acceptance["audit"]["fail_count"], 0)
        self.assertLessEqual(acceptance["reproduction_budget_seconds"], 600)
        self.assertTrue((ROOT / acceptance["reproduction_command"]).is_file())

    def test_reproduction_audits_the_new_task_not_the_static_fixture(self) -> None:
        script = (ROOT / "examples" / "langgraph-false-done" / "reproduce.sh").read_text(encoding="utf-8")

        self.assertIn('"$repo_root/bin/valp" audit "$task_dir"', script)
        self.assertNotIn('"$repo_root/bin/valp" audit "$case_dir/task"', script)
        self.assertIn("Expected the first task audit to fail before repair", script)


if __name__ == "__main__":
    unittest.main()
