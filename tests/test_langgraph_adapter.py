from __future__ import annotations

import json
import multiprocessing
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from valp_cli.audit import PASS, TaskAudit
from valp_cli.adapter_abi import AdapterOperation, CapabilityStatus
from valp_cli.langgraph_adapter import (
    LangGraphAdapterError,
    cancel_langgraph_run,
    langgraph_adapter_manifest,
    resume_langgraph_run,
    submit_langgraph_run,
)
from valp_cli.protocol_receipts import ProofBinding, propose_receipt_append, receipt_subject_digest
from valp_cli.receipt_store import DURABILITY_UNKNOWN_ERROR, ReceiptStore, ReceiptStoreError
from valp_cli.submission import build_submission_dependencies


ROOT = Path(__file__).resolve().parents[1]


def _concurrent_submit_worker(workspace, task_id, start, results, run_posts) -> None:
    def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
        if method == "POST" and path == "/threads":
            time.sleep(0.25)
            return {"thread_id": "thread-concurrent"}
        if method == "POST" and path.endswith("/runs"):
            with run_posts.get_lock():
                run_posts.value += 1
                run_number = run_posts.value
            return {
                "run_id": f"run-concurrent-{run_number}",
                "thread_id": "thread-concurrent",
                "assistant_id": "assistant-worker",
                "status": "pending",
            }
        if method == "GET" and "/runs/" in path:
            return {"status": "pending"}
        raise AssertionError((method, path, payload))

    start.wait()
    try:
        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request):
            result = submit_langgraph_run(
                Path(workspace),
                task_id,
                "langgraph_worker",
                "implementer",
                wait_seconds=0,
            )
        results.put(("ok", result["status"]))
    except Exception as error:  # noqa: BLE001 - process boundary records the public error.
        results.put(("error", str(error)))


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
        control_root = self.workspace / ".valp"
        control_root.mkdir()
        (control_root / "installation.json").write_text(
            json.dumps(
                {
                    "schema_version": "valp-installation.v1",
                    "installation_id": "inst-langgraph-test",
                    "active_leader_epoch": 1,
                }
            ),
            encoding="utf-8",
        )
        (control_root / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": "valp-executable-state.v1",
                    "installation_id": "inst-langgraph-test",
                    "active_leader_epoch": 1,
                }
            ),
            encoding="utf-8",
        )
        (self.task_dir / "automation-policy.json").write_text(
            json.dumps(
                {
                    "schema_version": "valp-automation-policy.v1",
                    "approval_required": False,
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def receipts(self) -> list[dict]:
        return [
            json.loads(line)
            for line in (self.task_dir / "runtime" / "langgraph" / "receipts.v3.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
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
        abi_schema = json.loads((ROOT / "schemas" / "adapter-abi.schema.json").read_text())
        abi_adoption = json.loads(
            (self.task_dir / "runtime" / "langgraph" / "abi-adoption.json").read_text()
        )
        submit_observation = json.loads(
            (self.task_dir / "runtime" / "langgraph" / "run-repair" / "abi-submit.json").read_text()
        )
        terminal_observation = json.loads(
            (self.task_dir / "runtime" / "langgraph" / "run-repair" / "abi-observe.json").read_text()
        )
        self.assertEqual(abi_adoption["abi_version"], "1.0")
        self.assertEqual(
            [item["proof_kind"] for item in submit_observation["provenance"]],
            ["process_bound", "content_bound"],
        )
        self.assertEqual(terminal_observation["status"], "completed")
        self.assertEqual(terminal_observation["evidence_refs"], ["evidence/report.md"])
        Draft202012Validator(abi_schema).validate(submit_observation)
        Draft202012Validator(abi_schema).validate(terminal_observation)
        receipts = self.receipts()
        self.assertFalse((self.task_dir / "dispatch-receipts.jsonl").exists())
        self.assertTrue(all(receipt["schema_version"] == "valp-dispatch-receipt.v3" for receipt in receipts))
        self.assertEqual(
            [receipt["event"] for receipt in receipts],
            ["dispatch_submitted", "dispatch_blocked", "dispatch_submitted", "dispatch_completed"],
        )
        first_proof = json.loads(
            (self.task_dir / receipts[0]["proof_bindings"][0]["proof_ref"]).read_text(encoding="utf-8")
        )
        repair_proof = json.loads(
            (self.task_dir / receipts[2]["proof_bindings"][0]["proof_ref"]).read_text(encoding="utf-8")
        )
        self.assertEqual(first_proof["adapter_proof"]["adapter_record"]["submission_id"], "run-false-done")
        self.assertEqual(repair_proof["adapter_proof"]["adapter_record"]["submission_id"], "run-repair")
        self.assertEqual(receipts[0]["attempt_id"], "langgraph:run-false-done")
        self.assertEqual(receipts[2]["attempt_id"], "langgraph:run-repair")
        submitted_bindings = {item["proof_kind"]: item for item in receipts[0]["proof_bindings"]}
        self.assertNotEqual(
            submitted_bindings["process_bound"]["proof_ref"],
            submitted_bindings["content_bound"]["proof_ref"],
        )
        content_ack = json.loads(
            (self.task_dir / submitted_bindings["content_bound"]["proof_ref"]).read_text(encoding="utf-8")
        )
        self.assertIs(content_ack["acknowledged"], True)
        self.assertEqual(content_ack["request_payload_digest"], receipts[0]["payload_digest"])
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
            replayed = resume_langgraph_run(
                self.workspace,
                self.task_id,
                "run-slow",
                wait_seconds=0,
            )

        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(replayed["receipt"], resumed["receipt"])
        self.assertEqual([item["event"] for item in self.receipts()], ["dispatch_submitted", "dispatch_completed"])
        proof_ref = resumed["receipt"]["proof_bindings"][0]["proof_ref"]
        proof = json.loads((self.task_dir / proof_ref).read_text(encoding="utf-8"))
        self.assertEqual(proof["adapter_proof"]["adapter_record"]["submission_id"], "run-slow")

    def test_langgraph_cancel_executes_provider_operation_and_writes_identity_bound_proof(self) -> None:
        post_count = {"value": 0}
        cancelled = {"value": False}

        def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
            if method == "POST" and path == "/threads":
                return {"thread_id": "thread-cancel"}
            if method == "POST" and path.endswith("/runs"):
                return {
                    "run_id": "run-cancel",
                    "thread_id": "thread-cancel",
                    "assistant_id": "assistant-worker",
                    "status": "pending",
                }
            if method == "GET" and path.endswith("/runs/run-cancel"):
                return {
                    "status": "interrupted" if cancelled["value"] else "pending",
                    "updated_at": "2026-08-05T00:00:00Z",
                }
            if method == "POST" and path.endswith("/runs/run-cancel/cancel"):
                post_count["value"] += 1
                cancelled["value"] = True
                return {"status": "interrupted", "run_id": "run-cancel"}
            raise AssertionError((method, path, payload))

        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request):
            waiting = submit_langgraph_run(
                self.workspace,
                self.task_id,
                "langgraph_worker",
                "implementer",
                wait_seconds=0,
            )
            submission = json.loads(
                (self.task_dir / "runtime/langgraph/run-cancel/submission.json").read_text()
            )
            obligation = "adapter_cancel:" + json.dumps({
                key: submission[key]
                for key in (
                    "attempt_id", "dispatch_generation", "dispatch_id", "task_id",
                    "work_item_id",
                )
            }, sort_keys=True, separators=(",", ":"))
            first = cancel_langgraph_run(
                self.workspace, self.task_id, "run-cancel", obligation=obligation
            )
            second = cancel_langgraph_run(
                self.workspace, self.task_id, "run-cancel", obligation=obligation
            )

        self.assertEqual(post_count["value"], 1)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "cancelled")
        self.assertEqual(first["proof"]["obligation"], obligation)
        self.assertEqual(first["proof"]["terminal_status"], "interrupted")
        self.assertTrue(first["proof"]["acknowledged"])
        self.assertEqual(first["observation"]["request"]["operation"], "cancel")
        self.assertEqual(first["observation"]["status"], "cancelled")
        Draft202012Validator(json.loads(
            (ROOT / "schemas/adapter-cancellation-proof.schema.json").read_text()
        )).validate(first["proof"])
        self.assertEqual(
            langgraph_adapter_manifest().capability(AdapterOperation.CANCEL).status,
            CapabilityStatus.SUPPORTED,
        )
        self.assertTrue((self.task_dir / first["proof_ref"]).is_file())

    def test_langgraph_cancel_rejects_wrong_obligation_without_calling_provider(self) -> None:
        def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
            if method == "POST" and path == "/threads":
                return {"thread_id": "thread-cancel-bad"}
            if method == "POST" and path.endswith("/runs"):
                return {
                    "run_id": "run-cancel-bad",
                    "thread_id": "thread-cancel-bad",
                    "assistant_id": "assistant-worker",
                    "status": "pending",
                }
            if method == "GET" and path.endswith("/runs/run-cancel-bad"):
                return {"status": "pending"}
            raise AssertionError((method, path, payload))

        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request):
            submit_langgraph_run(
                self.workspace,
                self.task_id,
                "langgraph_worker",
                "implementer",
                wait_seconds=0,
            )
            with self.assertRaisesRegex(LangGraphAdapterError, "obligation"):
                cancel_langgraph_run(
                    self.workspace,
                    self.task_id,
                    "run-cancel-bad",
                    obligation='adapter_cancel:{"attempt_id":"forged"}',
                )
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
        blocked = self.receipts()[-1]
        proof = json.loads(
            (self.task_dir / blocked["proof_bindings"][0]["proof_ref"]).read_text(encoding="utf-8")
        )
        self.assertEqual(proof["adapter_proof"]["failure_reason"]["error"], "RuntimeError")

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

    def test_nonempty_compatibility_ledger_blocks_before_runtime_dispatch(self) -> None:
        (self.task_dir / "dispatch-receipts.jsonl").write_text(
            json.dumps({"schema_version": "valp-dispatch-receipt.v2"}) + "\n",
            encoding="utf-8",
        )

        with patch("valp_cli.langgraph_adapter._request") as request:
            with self.assertRaisesRegex(LangGraphAdapterError, "legacy/v2 ledger"):
                submit_langgraph_run(
                    self.workspace,
                    self.task_id,
                    "langgraph_worker",
                    "implementer",
                )

        request.assert_not_called()

    def test_post_commit_uncertainty_reconciles_without_double_dispatch(self) -> None:
        run_posts = 0

        def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
            nonlocal run_posts
            if method == "POST" and path == "/threads":
                return {"thread_id": "thread-uncertain"}
            if method == "POST" and path.endswith("/runs"):
                run_posts += 1
                return {
                    "run_id": "run-uncertain",
                    "thread_id": "thread-uncertain",
                    "assistant_id": "assistant-worker",
                    "status": "pending",
                }
            if method == "GET" and path.endswith("/join"):
                return {"claim": "done"}
            if method == "GET" and path.endswith("/state"):
                return {"checkpoint": {"checkpoint_id": "checkpoint-uncertain"}}
            if method == "GET" and "/runs/" in path:
                return {"status": "success", "updated_at": "2026-07-20T00:00:00Z"}
            raise AssertionError((method, path, payload))

        original_append = ReceiptStore.append
        calls = 0

        def append_then_report_unknown(store, accepted):
            nonlocal calls
            result = original_append(store, accepted)
            calls += 1
            if calls == 1:
                raise ReceiptStoreError(
                    DURABILITY_UNKNOWN_ERROR,
                    "injected post-commit uncertainty",
                    outcome="unknown_or_committed",
                )
            return result

        (self.task_dir / "evidence").mkdir()
        (self.task_dir / "evidence" / "report.md").write_text("verified\n", encoding="utf-8")
        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request), patch.object(
            ReceiptStore,
            "append",
            new=append_then_report_unknown,
        ):
            result = submit_langgraph_run(
                self.workspace,
                self.task_id,
                "langgraph_worker",
                "implementer",
                expected_refs=["evidence/report.md"],
            )
            replayed = submit_langgraph_run(
                self.workspace,
                self.task_id,
                "langgraph_worker",
                "implementer",
                expected_refs=["evidence/report.md"],
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(replayed["receipt"], result["receipt"])
        self.assertEqual(run_posts, 1)
        self.assertEqual([item["event"] for item in self.receipts()], ["dispatch_submitted", "dispatch_completed"])

    def test_concurrent_first_submissions_create_one_provider_run(self) -> None:
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        run_posts = context.Value("i", 0)

        processes = [
            context.Process(
                target=_concurrent_submit_worker,
                args=(str(self.workspace), self.task_id, start, results, run_posts),
            )
            for _ in range(2)
        ]
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(10)

        self.assertTrue(all(not process.is_alive() for process in processes))
        self.assertTrue(all(process.exitcode == 0 for process in processes))
        outcomes = [results.get(timeout=1) for _ in processes]
        self.assertEqual(run_posts.value, 1)
        self.assertIn(("ok", "waiting"), outcomes)
        self.assertTrue(
            all(
                outcome == ("ok", "waiting")
                or (outcome[0] == "error" and "reconciliation required" in outcome[1])
                for outcome in outcomes
            ),
            outcomes,
        )

    def test_resume_rejects_tampered_receipt_proof_before_runtime_observation(self) -> None:
        def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
            if method == "POST" and path == "/threads":
                return {"thread_id": "thread-proof"}
            if method == "POST" and path.endswith("/runs"):
                return {
                    "run_id": "run-proof",
                    "thread_id": "thread-proof",
                    "assistant_id": "assistant-worker",
                    "status": "pending",
                }
            if method == "GET" and "/runs/" in path:
                return {"status": "pending"}
            raise AssertionError((method, path, payload))

        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request):
            waiting = submit_langgraph_run(
                self.workspace,
                self.task_id,
                "langgraph_worker",
                "implementer",
                wait_seconds=0,
            )
        self.assertEqual(waiting["status"], "waiting")
        submitted = self.receipts()[0]
        proof_path = self.task_dir / submitted["proof_bindings"][0]["proof_ref"]
        proof_path.write_text('{"tampered":true}\n', encoding="utf-8")

        with patch("valp_cli.langgraph_adapter._request") as request:
            with self.assertRaisesRegex(LangGraphAdapterError, "proof digest"):
                resume_langgraph_run(
                    self.workspace,
                    self.task_id,
                    "run-proof",
                    wait_seconds=0,
                )

        request.assert_not_called()

    def test_task_audit_consumes_authoritative_langgraph_v3_ledger(self) -> None:
        identity = {
            "work_item_id": "implementer:langgraph_worker",
            "agent": "langgraph_worker",
            "role": "implementer",
            "dispatch_id": f"{self.task_id}:implementer:1",
            "dispatch_generation": 1,
            "expected_refs": ["evidence/report.md"],
        }
        (self.task_dir / "state.json").write_text(
            json.dumps(
                {
                    "schema_version": "valp-visible-loop-state.v2",
                    "task_id": self.task_id,
                    "selected_agents": ["langgraph_worker"],
                }
            ),
            encoding="utf-8",
        )
        (self.task_dir / "routing.json").write_text(
            json.dumps(
                {
                    "task_id": self.task_id,
                    "selected_agents": ["langgraph_worker"],
                    "runtime_adapter": {"class": "hosted_local_platform", "name": "LangGraph API"},
                }
            ),
            encoding="utf-8",
        )
        (self.task_dir / "submission-dependencies.json").write_text(
            json.dumps(
                {
                    "schema_version": "valp-submission-dependencies.v2",
                    "task_id": self.task_id,
                    "work_items": [identity],
                    "dependencies": [],
                }
            ),
            encoding="utf-8",
        )
        (self.task_dir / "evidence").mkdir()
        (self.task_dir / "evidence" / "report.md").write_text("verified\n", encoding="utf-8")

        def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
            if method == "POST" and path == "/threads":
                return {"thread_id": "thread-audit"}
            if method == "POST" and path.endswith("/runs"):
                return {
                    "run_id": "run-audit",
                    "thread_id": "thread-audit",
                    "assistant_id": "assistant-worker",
                    "status": "pending",
                }
            if method == "GET" and path.endswith("/join"):
                return {"claim": "done"}
            if method == "GET" and path.endswith("/state"):
                return {"checkpoint": {"checkpoint_id": "checkpoint-audit"}}
            if method == "GET" and "/runs/" in path:
                return {"status": "success", "updated_at": "2026-07-20T00:00:00Z"}
            raise AssertionError((method, path, payload))

        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request):
            submit_langgraph_run(
                self.workspace,
                self.task_id,
                "langgraph_worker",
                "implementer",
            )

        item = TaskAudit(self.task_dir).check_dispatch_receipts()
        self.assertEqual(item.status, PASS, item.message)
        self.assertIn("v3", item.message)

    def test_stale_receipt_cas_fails_closed_without_redispatch(self) -> None:
        run_posts = 0

        def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
            nonlocal run_posts
            if method == "POST" and path == "/threads":
                return {"thread_id": "thread-race"}
            if method == "POST" and path.endswith("/runs"):
                run_posts += 1
                return {
                    "run_id": "run-race",
                    "thread_id": "thread-race",
                    "assistant_id": "assistant-worker",
                    "status": "pending",
                }
            raise AssertionError((method, path, payload))

        original_append = ReceiptStore.append
        injected = False

        def append_after_competing_write(store, accepted):
            nonlocal injected
            if not injected:
                injected = True
                base = replace(
                    accepted.receipt.draft,
                    receipt_id="receipt-competing",
                    event="dispatch_written",
                    proof_bindings=(),
                )
                subject = receipt_subject_digest(base)
                competing = replace(
                    base,
                    proof_bindings=tuple(
                        ProofBinding(binding.proof_kind, binding.proof_ref, binding.proof_digest, subject)
                        for binding in accepted.receipt.draft.proof_bindings
                    ),
                )
                proposal = propose_receipt_append(store.load(), competing)
                self.assertIsNotNone(proposal.accepted)
                original_append(store, proposal.accepted)
            return original_append(store, accepted)

        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request), patch.object(
            ReceiptStore,
            "append",
            new=append_after_competing_write,
        ):
            with self.assertRaisesRegex(LangGraphAdapterError, "STATE-CONFLICT"):
                submit_langgraph_run(
                    self.workspace,
                    self.task_id,
                    "langgraph_worker",
                    "implementer",
                    wait_seconds=0,
                )

        self.assertEqual(run_posts, 1)

    def test_dependency_gate_blocks_reviewer_before_runtime_dispatch(self) -> None:
        (self.task_dir / "agents" / "review_worker").mkdir()
        (self.task_dir / "agents" / "review_worker" / "dispatch.md").write_text(
            "# Review dispatch\n",
            encoding="utf-8",
        )
        dependencies = build_submission_dependencies(
            self.task_id,
            {"implementer": "langgraph_worker", "reviewer": "review_worker"},
        )
        (self.task_dir / "submission-dependencies.json").write_text(
            json.dumps(dependencies),
            encoding="utf-8",
        )

        with patch("valp_cli.langgraph_adapter._request") as request:
            with self.assertRaisesRegex(LangGraphAdapterError, "implementer-before-reviewer"):
                submit_langgraph_run(
                    self.workspace,
                    self.task_id,
                    "review_worker",
                    "reviewer",
                )

        request.assert_not_called()

    def test_prepared_intent_blocks_retry_after_unknown_provider_acceptance(self) -> None:
        def api_request(api_url, method, path, payload=None, timeout_seconds=10.0):
            if method == "POST" and path == "/threads":
                return {"thread_id": "thread-crash-window"}
            if method == "POST" and path.endswith("/runs"):
                return {
                    "run_id": "run-crash-window",
                    "thread_id": "thread-crash-window",
                    "assistant_id": "assistant-worker",
                    "status": "pending",
                }
            raise AssertionError((method, path, payload))

        from valp_cli import langgraph_adapter as adapter_module

        original_write_json = adapter_module.write_json

        def crash_before_acceptance_persistence(path, value):
            if path.name == "intent.json" and value.get("status") == "accepted":
                raise OSError("injected crash after provider acceptance")
            return original_write_json(path, value)

        with patch("valp_cli.langgraph_adapter._request", side_effect=api_request), patch(
            "valp_cli.langgraph_adapter.write_json",
            side_effect=crash_before_acceptance_persistence,
        ):
            with self.assertRaisesRegex(OSError, "injected crash"):
                submit_langgraph_run(
                    self.workspace,
                    self.task_id,
                    "langgraph_worker",
                    "implementer",
                    wait_seconds=0,
                )

        with patch("valp_cli.langgraph_adapter._request") as request:
            with self.assertRaisesRegex(LangGraphAdapterError, "reconciliation required"):
                submit_langgraph_run(
                    self.workspace,
                    self.task_id,
                    "langgraph_worker",
                    "implementer",
                    wait_seconds=0,
                )
        request.assert_not_called()

    def test_adopted_task_cannot_audit_v2_when_v3_ledger_is_missing(self) -> None:
        (self.task_dir / "state.json").write_text(
            json.dumps({"schema_version": "valp-visible-loop-state.v2", "task_id": self.task_id}),
            encoding="utf-8",
        )
        (self.task_dir / "routing.json").write_text(
            json.dumps(
                {
                    "task_id": self.task_id,
                    "runtime_adapter": {"class": "hosted_local_platform", "name": "LangGraph API"},
                }
            ),
            encoding="utf-8",
        )
        adoption = self.task_dir / "runtime" / "langgraph" / "adoption.json"
        adoption.parent.mkdir(parents=True)
        adoption.write_text(
            json.dumps(
                {
                    "schema_version": "valp-langgraph-receipt-adoption.v1",
                    "task_id": self.task_id,
                    "ledger_ref": "runtime/langgraph/receipts.v3.jsonl",
                    "compatibility_ledger_ref": "dispatch-receipts.jsonl",
                    "write_schema": "valp-dispatch-receipt.v3",
                }
            ),
            encoding="utf-8",
        )
        (self.task_dir / "dispatch-receipts.jsonl").write_text(
            json.dumps({"event": "dispatch_completed", "agent": "langgraph_worker"}) + "\n",
            encoding="utf-8",
        )

        item = TaskAudit(self.task_dir).check_dispatch_receipts()
        self.assertEqual(item.status, "fail")
        self.assertIn("v3", item.message)

    def test_adoption_marker_prevents_v2_audit_fallback_after_routing_drift(self) -> None:
        (self.task_dir / "state.json").write_text(
            json.dumps({"schema_version": "valp-visible-loop-state.v2", "task_id": self.task_id}),
            encoding="utf-8",
        )
        (self.task_dir / "routing.json").write_text(
            json.dumps(
                {
                    "task_id": self.task_id,
                    "runtime_adapter": {"class": "manual", "name": "manual"},
                }
            ),
            encoding="utf-8",
        )
        adoption = self.task_dir / "runtime" / "langgraph" / "adoption.json"
        adoption.parent.mkdir(parents=True)
        adoption.write_text(
            json.dumps(
                {
                    "schema_version": "valp-langgraph-receipt-adoption.v1",
                    "task_id": self.task_id,
                    "ledger_ref": "runtime/langgraph/receipts.v3.jsonl",
                    "compatibility_ledger_ref": "dispatch-receipts.jsonl",
                    "write_schema": "valp-dispatch-receipt.v3",
                }
            ),
            encoding="utf-8",
        )
        (self.task_dir / "dispatch-receipts.jsonl").write_text(
            json.dumps(
                {
                    "ts": "2026-08-03T00:00:00Z",
                    "agent": "langgraph_worker",
                    "event": "dispatch_completed",
                    "dispatch_ref": "agents/langgraph_worker/dispatch.md",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        item = TaskAudit(self.task_dir).check_dispatch_receipts()
        self.assertEqual(item.status, "fail")
        self.assertIn("v3", item.message)


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
        self.assertIn('install init --workspace "$demo_workspace"', script)
        self.assertIn('installation_e2e.py" bootstrap', script)
        self.assertIn('installation_e2e.py" restart-and-rotate', script)
        self.assertIn('"$task_dir/runtime/langgraph/receipts.v3.jsonl"', script)
        self.assertNotIn("written = [", script)


if __name__ == "__main__":
    unittest.main()
