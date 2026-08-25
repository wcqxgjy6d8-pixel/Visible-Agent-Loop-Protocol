from __future__ import annotations

import json
import contextlib
import concurrent.futures
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from jsonschema import Draft202012Validator

from valp_cli.protocol_receipts import digest
from valp_cli.cli import main
from valp_cli.workflow import observe_expected_evidence_completions, wait_receipt_event_error
from valp_cli.kernel_runtime import (
    accept_kernel_wake,
    record_kernel_completion,
    start_kernel_suspension,
)
from valp_cli.kernel_store import KernelStore
from valp_cli.protocol_kernel import SuspensionStatus, TaskStatus, WorkItemStatus
from valp_cli.continuation import (
    ContinuationStore,
    capability_declaration,
    prepare_wake_continuation,
)
from valp_cli.runtime_adapters import (
    RuntimeAdapterError,
    load_queue_lifecycle,
    load_runtime_v3_receipts,
    manual_effective_receipt_ids,
    manual_receipt_is_effective,
    record_herdr_completion,
    record_herdr_submission,
    record_herdr_transport,
    record_manual_attestation,
    record_manual_decision,
    record_queue_acceptance,
    record_queue_cancellation_acknowledgement,
    record_queue_cancellation_proof,
    record_queue_cancellation_request,
    record_queue_claim,
    record_queue_terminal_observation,
    runtime_adapter_manifest,
)


class RuntimeAdapterAdoptionTests(unittest.TestCase):
    def validate_schema(self, name: str, value: dict[str, object]) -> None:
        schema = json.loads(
            (Path(__file__).parents[1] / "schemas" / name).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema).validate(value)

    def task(self, root: Path, task_id: str = "TASK-RUNTIME") -> Path:
        directory = root / ".herdr-loop" / "tasks" / task_id
        (directory / "agents" / "codex").mkdir(parents=True)
        (directory / "agents" / "codex" / "dispatch.md").write_text(
            "Implement and verify.\n", encoding="utf-8"
        )
        (root / ".valp").mkdir()
        (root / ".valp" / "installation.json").write_text(
            json.dumps({
                "schema_version": "valp-installation.v1",
                "installation_id": "installation-test",
                "active_leader_epoch": 3,
            }),
            encoding="utf-8",
        )
        (root / ".valp" / "state.json").write_text(
            json.dumps({
                "schema_version": "valp-executable-state.v1",
                "installation_id": "installation-test",
                "active_leader_epoch": 3,
            }),
            encoding="utf-8",
        )
        (directory / "automation-policy.json").write_text(
            json.dumps({
                "schema_version": "valp-automation-policy.v1",
                "approval_required": False,
            }),
            encoding="utf-8",
        )
        authorities = directory / "authorities"
        authorities.mkdir()
        for authority in ("operator-1", "operator-2", "lead-operator"):
            (authorities / f"{authority}.json").write_text(json.dumps({
                "schema_version": "valp-manual-authority.v1",
                "task_id": task_id,
                "authority": authority,
                "allowed_actions": [
                    "manual_delivery_attested", "manual_result_attested", "manual_blocked",
                    "revoke", "adjudicate",
                ],
                "issued_by": "test-fixture",
                "statement": "Test authority declaration.",
            }), encoding="utf-8")
        return directory

    def identity(self) -> dict[str, object]:
        return {
            "agent": "codex",
            "role": "implementer",
            "work_item_id": "work-codex",
            "dispatch_id": "dispatch-codex",
            "dispatch_generation": 1,
            "dispatch_ref": "agents/codex/dispatch.md",
            "expected_refs": ["agents/codex/evidence.md"],
        }

    def herdr_proof(self, payload_digest: str) -> dict[str, object]:
        return {
            "runtime": "HERDR",
            "adapter": "VALP packaged HERDR adapter",
            "transport_mode": "agent_prompt",
            "proof_class": "agent_invocation",
            "pane_id": "pane-1",
            "agent_ref": "codex",
            "runtime_target": "pane-1",
            "payload_digest": payload_digest,
            "runtime_response": {"id": "request-correlation-only"},
            "submission_proof": {
                "kind": "identity_bound_state_change",
                "baseline_state_change_seq": 8,
                "state_change_seq": 9,
                "identity": {
                    "terminal_id": "terminal-1",
                    "name": "Codex",
                    "agent": "codex",
                    "pane_id": "pane-1",
                },
            },
        }

    def herdr_terminal_proof(
        self,
        *,
        agent: str = "codex",
        terminal_id: str = "terminal-1",
        pane_id: str = "pane-1",
        submission_sequence: int = 9,
        state_change_sequence: int = 10,
        status: str = "completed",
    ) -> dict[str, object]:
        proof = {
            "schema_version": "valp-herdr-terminal-observation.v1",
            "runtime": "HERDR",
            "proof_class": "agent_terminal_observation",
            "task_id": "TASK-RUNTIME",
            "agent": agent,
            "terminal_id": terminal_id,
            "pane_id": pane_id,
            "submission_state_change_seq": submission_sequence,
            "state_change_seq": state_change_sequence,
            "status": status,
            "acknowledged": True,
        }
        if status == "blocked":
            proof["failure_code"] = "HERDR-E-WORKER-BLOCKED"
        return proof

    def test_manifests_are_closed_abi_1_0_capability_tables(self) -> None:
        for adapter_id in ("herdr", "queue", "manual"):
            manifest = runtime_adapter_manifest(adapter_id).canonical()
            self.assertEqual(manifest["abi_version"], "1.0")
            self.assertEqual(len(manifest["capabilities"]), 6)
        manual = runtime_adapter_manifest("manual")
        unsupported = {
            item.operation.value for item in manual.capabilities if item.status.value == "unsupported"
        }
        self.assertEqual(unsupported, {"submit", "cancel", "resume"})
        queue = runtime_adapter_manifest("queue")
        self.assertEqual(
            next(item for item in queue.capabilities if item.operation.value == "cancel").status.value,
            "supported",
        )

    def test_herdr_atomic_submission_uses_state_change_attempt_and_v3_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            payload = (directory / "agents/codex/dispatch.md").read_bytes()
            payload_digest = digest(payload)
            receipt, observation = record_herdr_submission(
                directory,
                "TASK-RUNTIME",
                **self.identity(),
                proof=self.herdr_proof(payload_digest),
            )

            self.assertEqual(receipt["schema_version"], "valp-dispatch-receipt.v3")
            self.assertEqual(receipt["event"], "dispatch_submitted")
            self.assertNotEqual(receipt["attempt_id"], "request-correlation-only")
            self.assertEqual(
                {item["proof_kind"] for item in receipt["proof_bindings"]},
                {"process_bound", "content_bound"},
            )
            for binding in receipt["proof_bindings"]:
                self.assertNotIn("\\", binding["proof_ref"])
                self.assertTrue((directory / binding["proof_ref"]).is_file())
            self.assertEqual(observation["status"], "accepted")
            self.assertEqual(len(observation["provenance"]), 2)
            self.assertTrue((directory / "runtime/herdr/adoption.json").is_file())
            self.validate_schema(
                "runtime-adoption.schema.json",
                json.loads((directory / "runtime/herdr/adoption.json").read_text()),
            )

    def test_herdr_exact_retry_is_byte_stable_and_conflict_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            payload_digest = digest((directory / "agents/codex/dispatch.md").read_bytes())
            arguments = {
                "directory": directory,
                "task_id": "TASK-RUNTIME",
                **self.identity(),
                "proof": self.herdr_proof(payload_digest),
            }
            first = record_herdr_submission(**arguments)
            first_bytes = (directory / "runtime/herdr/receipts.v3.jsonl").read_bytes()
            second = record_herdr_submission(**arguments)
            self.assertEqual(second, first)
            self.assertEqual(
                (directory / "runtime/herdr/receipts.v3.jsonl").read_bytes(), first_bytes
            )
            changed = self.herdr_proof(payload_digest)
            changed["pane_id"] = "pane-2"
            with self.assertRaises(RuntimeAdapterError):
                record_herdr_submission(**{**arguments, "proof": changed})

    def test_herdr_pane_fallback_is_transport_only_and_never_submitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            proof = {
                "runtime": "HERDR",
                "transport_mode": "pane_send_text_enter",
                "proof_class": "transport_only",
                "manual_degraded": True,
                "pane_id": "pane-1",
                "payload_digest": digest((directory / "agents/codex/dispatch.md").read_bytes()),
            }
            receipt, observation = record_herdr_transport(
                directory, "TASK-RUNTIME", **self.identity(), proof=proof
            )
            self.assertEqual(receipt["event"], "dispatch_inserted")
            self.assertEqual(receipt["mode"], "manual")
            self.assertEqual(
                {item["proof_kind"] for item in receipt["proof_bindings"]}, {"transport_only"}
            )
            self.assertEqual(observation["status"], "waiting")
            self.assertEqual(observation["provenance"][0]["proof_kind"], "transport_only")
            self.assertNotIn(
                "dispatch_submitted",
                {item["event"] for item in load_runtime_v3_receipts(directory, "herdr")},
            )

    def test_herdr_completion_reuses_attempt_and_binds_expected_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            payload_digest = digest((directory / "agents/codex/dispatch.md").read_bytes())
            submitted, _ = record_herdr_submission(
                directory,
                "TASK-RUNTIME",
                **self.identity(),
                proof=self.herdr_proof(payload_digest),
            )
            evidence = directory / "agents/codex/evidence.md"
            evidence.write_text("verified\n", encoding="utf-8")
            completed, observation = record_herdr_completion(
                directory, "TASK-RUNTIME", submitted, ["agents/codex/evidence.md"],
                self.herdr_terminal_proof(),
            )
            self.assertEqual(completed["event"], "dispatch_completed")
            self.assertEqual(completed["attempt_id"], submitted["attempt_id"])
            self.assertEqual(completed["suspension_epoch"], 1)
            self.assertEqual(observation["status"], "completed")
            self.assertEqual(observation["evidence_refs"], ["agents/codex/evidence.md"])
            self.validate_schema(
                "herdr-terminal-observation.schema.json", self.herdr_terminal_proof()
            )

    def test_herdr_evidence_without_terminal_state_cannot_create_full_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            payload_digest = digest((directory / "agents/codex/dispatch.md").read_bytes())
            submitted, _ = record_herdr_submission(
                directory, "TASK-RUNTIME", **self.identity(),
                proof=self.herdr_proof(payload_digest),
            )
            (directory / "agents/codex/evidence.md").write_text("appeared\n", encoding="utf-8")
            invalid = self.herdr_terminal_proof()
            invalid["state_change_seq"] = invalid["submission_state_change_seq"]
            with self.assertRaisesRegex(RuntimeAdapterError, "terminal observation"):
                record_herdr_completion(
                    directory, "TASK-RUNTIME", submitted,
                    ["agents/codex/evidence.md"], invalid,
                )
            self.assertNotIn("dispatch_completed", {
                item["event"] for item in load_runtime_v3_receipts(directory, "herdr")
            })

    def test_herdr_blocked_terminal_records_same_attempt_without_success_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            payload_digest = digest((directory / "agents/codex/dispatch.md").read_bytes())
            submitted, _ = record_herdr_submission(
                directory,
                "TASK-RUNTIME",
                **self.identity(),
                proof=self.herdr_proof(payload_digest),
            )
            blocked, observation = record_herdr_completion(
                directory,
                "TASK-RUNTIME",
                submitted,
                ["agents/codex/evidence.md"],
                self.herdr_terminal_proof(status="blocked"),
            )
            self.assertEqual(blocked["event"], "dispatch_blocked")
            self.assertEqual(blocked["attempt_id"], submitted["attempt_id"])
            self.assertEqual(observation["status"], "blocked")
            self.assertEqual(observation["evidence_refs"], [])
            self.assertNotIn("dispatch_completed", {
                item["event"] for item in load_runtime_v3_receipts(directory, "herdr")
            })

    def test_queue_acceptance_is_not_worker_delivery_or_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            queue, receipt, observation = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            self.assertEqual(queue["status"], "queued")
            self.assertNotIn("worker_id", queue)
            self.assertEqual(receipt["event"], "dispatch_submitted")
            self.assertEqual(observation["status"], "accepted")
            self.validate_schema("queue-dispatch.schema.json", queue)
            self.assertNotIn("dispatch_completed", {
                item["event"] for item in load_runtime_v3_receipts(directory, "queue")
            })

    def test_queue_claim_and_queued_cancel_share_one_cas_frontier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            queue, submitted, _ = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            barrier = threading.Barrier(2)

            def claim() -> tuple[str, object]:
                barrier.wait()
                try:
                    return "ok", record_queue_claim(
                        directory, "TASK-RUNTIME", submitted,
                        worker_id="worker-1", run_id="run-1",
                        claim_token="claim-1", expected_revision=0,
                    )
                except RuntimeAdapterError as error:
                    return "error", error

            def cancel() -> tuple[str, object]:
                barrier.wait()
                try:
                    return "ok", record_queue_cancellation_request(
                        directory, "TASK-RUNTIME", submitted,
                        authority="operator-1", reason="stop",
                        expected_revision=0,
                    )
                except RuntimeAdapterError as error:
                    return "error", error

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(claim), pool.submit(cancel)]
                outcomes = [future.result() for future in futures]
            self.assertEqual([item[0] for item in outcomes].count("ok"), 1)
            lifecycle = load_queue_lifecycle(directory, queue["queue_id"])
            self.assertEqual(len(lifecycle), 1)
            self.assertIn(lifecycle[0]["event"], {"claimed", "cancelled"})

    def test_queue_claim_exact_retry_and_conflicting_identity_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            queue, submitted, _ = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            first = record_queue_claim(
                directory, "TASK-RUNTIME", submitted,
                worker_id="worker-1", run_id="run-1",
                claim_token="claim-1", expected_revision=0,
            )
            self.validate_schema("queue-lifecycle.schema.json", first)
            self.assertEqual(record_queue_claim(
                directory, "TASK-RUNTIME", submitted,
                worker_id="worker-1", run_id="run-1",
                claim_token="claim-1", expected_revision=0,
            ), first)
            before = (directory / "runtime/queue/lifecycle.v1.jsonl").read_bytes()
            with self.assertRaises(RuntimeAdapterError):
                record_queue_claim(
                    directory, "TASK-RUNTIME", submitted,
                    worker_id="worker-2", run_id="run-2",
                    claim_token="claim-2", expected_revision=0,
                )
            self.assertEqual(
                (directory / "runtime/queue/lifecycle.v1.jsonl").read_bytes(), before
            )
            self.assertEqual(load_queue_lifecycle(directory, queue["queue_id"])[0]["event"], "claimed")

    def test_queue_terminal_observation_requires_exact_persisted_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            queue, submitted, _ = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            (directory / "agents/codex/evidence.md").write_text("verified\n", encoding="utf-8")
            observation_ref = "runtime/queue/worker-observations/unclaimed.json"
            observation_path = directory / observation_ref
            observation_path.parent.mkdir(parents=True)
            observation_path.write_text(json.dumps({
                "schema_version": "valp-queue-worker-observation.v2",
                "task_id": "TASK-RUNTIME", "queue_id": queue["queue_id"],
                "enqueue_transaction_id": queue["enqueue_transaction_id"],
                "worker_id": "worker-1", "run_id": "run-1",
                "claim_token": "claim-1", "claim_event_id": "missing",
                "observation_sequence": 1, "status": "completed", "acknowledged": True,
            }), encoding="utf-8")
            with self.assertRaises(RuntimeAdapterError):
                record_queue_terminal_observation(
                    directory, "TASK-RUNTIME", submitted, observation_ref
                )

    def test_queue_claimed_cancel_requires_exact_worker_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            queue, submitted, _ = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            claim = record_queue_claim(
                directory, "TASK-RUNTIME", submitted,
                worker_id="worker-1", run_id="run-1",
                claim_token="claim-1", expected_revision=0,
            )
            requested = record_queue_cancellation_request(
                directory, "TASK-RUNTIME", submitted,
                authority="operator-1", reason="stop", expected_revision=1,
            )
            self.assertEqual(requested["event"], "cancellation_requested")
            with self.assertRaises(RuntimeAdapterError):
                record_queue_cancellation_acknowledgement(
                    directory, "TASK-RUNTIME", submitted,
                    worker_id="worker-2", run_id="run-1",
                    claim_token="claim-1", claim_event_id=claim["event_id"],
                    expected_revision=2,
                )
            acknowledged, observation = record_queue_cancellation_acknowledgement(
                directory, "TASK-RUNTIME", submitted,
                worker_id="worker-1", run_id="run-1",
                claim_token="claim-1", claim_event_id=claim["event_id"],
                expected_revision=2,
            )
            self.assertEqual(acknowledged["event"], "cancelled")
            self.validate_schema("queue-lifecycle.schema.json", acknowledged)
            self.assertEqual(observation["status"], "cancelled")
            proof_ref = observation["evidence_refs"][0]
            proof = json.loads((directory / proof_ref).read_text(encoding="utf-8"))
            self.validate_schema("queue-cancellation-proof.schema.json", proof)
            self.assertEqual(load_queue_lifecycle(directory, queue["queue_id"])[-1]["event"], "cancelled")

    def test_queue_unclaimed_cancel_is_terminal_and_exact_retry_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            queue, submitted, _ = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            cancelled = record_queue_cancellation_request(
                directory, "TASK-RUNTIME", submitted,
                authority="operator-1", reason="withdraw before claim",
                expected_revision=0,
            )
            self.assertEqual(record_queue_cancellation_request(
                directory, "TASK-RUNTIME", submitted,
                authority="operator-1", reason="withdraw before claim",
                expected_revision=0,
            ), cancelled)
            proof_ref, observation = record_queue_cancellation_proof(
                directory, "TASK-RUNTIME", submitted, cancelled
            )
            self.assertEqual(cancelled["event"], "cancelled")
            self.assertEqual(observation["status"], "cancelled")
            self.assertEqual(len(load_queue_lifecycle(directory, queue["queue_id"])), 1)
            proof = json.loads((directory / proof_ref).read_text(encoding="utf-8"))
            self.validate_schema("queue-cancellation-proof.schema.json", proof)

    def test_queue_file_evidence_cannot_trigger_false_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            identity = self.identity()
            record_queue_acceptance(directory, "TASK-RUNTIME", **identity)
            (directory / "agents/codex/evidence.md").write_text("appeared\n", encoding="utf-8")
            appended = observe_expected_evidence_completions(
                directory,
                "TASK-RUNTIME",
                {
                    "strict_identity": True,
                    "pending_work_item_ids": [identity["work_item_id"]],
                    "evidence_refs_present_at_entry": [],
                    "required_work_items": [{
                        "agent": identity["agent"],
                        "role": identity["role"],
                        "work_item_id": identity["work_item_id"],
                        "dispatch_id": identity["dispatch_id"],
                        "dispatch_generation": identity["dispatch_generation"],
                        "expected_refs": identity["expected_refs"],
                    }],
                },
            )
            self.assertEqual(appended, 0)
            self.assertNotIn(
                "dispatch_completed",
                {item["event"] for item in load_runtime_v3_receipts(directory, "queue")},
            )

    def test_queue_terminal_observer_binds_real_worker_run_and_expected_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            queue, submitted, _ = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            claim = record_queue_claim(
                directory, "TASK-RUNTIME", submitted,
                worker_id="worker-7", run_id="run-1",
                claim_token="claim-7", expected_revision=0,
            )
            (directory / "agents/codex/evidence.md").write_text("verified\n", encoding="utf-8")
            observation_ref = "runtime/queue/worker-observations/run-1.json"
            worker_observation = {
                "schema_version": "valp-queue-worker-observation.v2",
                "task_id": "TASK-RUNTIME",
                "queue_id": queue["queue_id"],
                "enqueue_transaction_id": queue["enqueue_transaction_id"],
                "worker_id": "worker-7",
                "run_id": "run-1",
                "claim_token": "claim-7",
                "claim_event_id": claim["event_id"],
                "observation_sequence": 4,
                "status": "completed",
                "acknowledged": True,
            }
            path = directory / observation_ref
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(worker_observation), encoding="utf-8")

            completed, observation = record_queue_terminal_observation(
                directory, "TASK-RUNTIME", submitted, observation_ref
            )

            self.validate_schema("queue-worker-observation.schema.json", worker_observation)
            self.assertEqual(completed["event"], "dispatch_completed")
            self.assertEqual(completed["attempt_id"], submitted["attempt_id"])
            self.assertEqual(observation["runtime_identity"], "worker-7:run-1:4")
            self.assertEqual(observation["status"], "completed")
            before = (directory / "runtime/queue/receipts.v3.jsonl").read_bytes()
            self.assertEqual(
                record_queue_terminal_observation(
                    directory, "TASK-RUNTIME", submitted, observation_ref
                ),
                (completed, observation),
            )
            self.assertEqual((directory / "runtime/queue/receipts.v3.jsonl").read_bytes(), before)

    def test_queue_real_local_worker_process_claims_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            _, submitted, _ = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            worker = """
import json
from pathlib import Path
import sys
from valp_cli.runtime_adapters import load_runtime_v3_receipts, record_queue_claim

directory = Path(sys.argv[1])
submission = next(
    item for item in load_runtime_v3_receipts(directory, "queue")
    if item["event"] == "dispatch_submitted"
)
claim = record_queue_claim(
    directory, "TASK-RUNTIME", submission,
    worker_id="worker-subprocess", run_id="run-subprocess",
    claim_token="claim-subprocess", expected_revision=0,
)
(directory / "agents/codex/evidence.md").write_text("subprocess verified\\n", encoding="utf-8")
observation_ref = "runtime/queue/worker-observations/run-subprocess.json"
path = directory / observation_ref
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    "schema_version": "valp-queue-worker-observation.v2",
    "task_id": "TASK-RUNTIME",
    "queue_id": claim["queue_id"],
    "enqueue_transaction_id": claim["enqueue_transaction_id"],
    "worker_id": "worker-subprocess", "run_id": "run-subprocess",
    "claim_token": "claim-subprocess", "claim_event_id": claim["event_id"],
    "observation_sequence": 1, "status": "completed", "acknowledged": True,
}), encoding="utf-8")
print(json.dumps({"claim": claim, "observation_ref": observation_ref}))
"""
            completed = subprocess.run(
                [sys.executable, "-c", worker, str(directory)],
                cwd=Path(__file__).parents[1], text=True, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            worker_result = json.loads(completed.stdout)
            receipt, observation = record_queue_terminal_observation(
                directory, "TASK-RUNTIME", submitted, worker_result["observation_ref"]
            )
            self.assertEqual(worker_result["claim"]["event"], "claimed")
            self.assertEqual(receipt["event"], "dispatch_completed")
            self.assertEqual(observation["runtime_identity"], "worker-subprocess:run-subprocess:1")

    def test_queue_terminal_observer_rejects_synthetic_or_mismatched_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            queue, submitted, _ = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            observation_ref = "runtime/queue/worker-observations/run-bad.json"
            path = directory / observation_ref
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "schema_version": "valp-queue-worker-observation.v2",
                "task_id": "TASK-RUNTIME",
                "queue_id": queue["queue_id"],
                "enqueue_transaction_id": "sha256:" + "0" * 64,
                "worker_id": "worker-7",
                "run_id": "run-bad",
                "claim_token": "claim-bad",
                "claim_event_id": "missing",
                "observation_sequence": 1,
                "status": "blocked",
                "acknowledged": True,
                "failure_code": "WORKER-E-FAILED",
            }), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeAdapterError, "identity"):
                record_queue_terminal_observation(
                    directory, "TASK-RUNTIME", submitted, observation_ref
                )

    def test_manual_attestation_remains_manual_and_binds_authority_statement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            receipt, observation = record_manual_attestation(
                directory,
                "TASK-RUNTIME",
                **self.identity(),
                event="manual_delivery_attested",
                authority="operator-1",
                authority_ref="authorities/operator-1.json",
                statement="I delivered the exact dispatch to the named recipient.",
            )
            self.assertEqual(receipt["mode"], "manual")
            self.assertEqual(
                {item["proof_kind"] for item in receipt["proof_bindings"]}, {"manual_attested"}
            )
            proof_ref = receipt["proof_bindings"][0]["proof_ref"]
            attestation = json.loads((directory / proof_ref).read_text(encoding="utf-8"))
            self.assertEqual(attestation["authority"], "operator-1")
            self.assertEqual(attestation["statement"], "I delivered the exact dispatch to the named recipient.")
            self.assertEqual(observation["status"], "accepted")
            self.assertEqual(observation["provenance"][0]["proof_kind"], "manual_attested")
            self.validate_schema("manual-attestation.schema.json", attestation)
            self.validate_schema(
                "manual-authority.schema.json",
                json.loads((directory / "authorities/operator-1.json").read_text()),
            )

    def test_manual_attestation_rejects_missing_or_unauthorized_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            with self.assertRaisesRegex(RuntimeAdapterError, "authority declaration"):
                record_manual_attestation(
                    directory, "TASK-RUNTIME", **self.identity(),
                    event="manual_delivery_attested", authority="unknown",
                    authority_ref="authorities/unknown.json", statement="Delivered.",
                )
            declaration_path = directory / "authorities/operator-1.json"
            declaration = json.loads(declaration_path.read_text())
            declaration["allowed_actions"] = ["manual_delivery_attested"]
            declaration_path.write_text(json.dumps(declaration), encoding="utf-8")
            receipt, _ = record_manual_attestation(
                directory, "TASK-RUNTIME", **self.identity(),
                event="manual_delivery_attested", authority="operator-1",
                authority_ref="authorities/operator-1.json", statement="Delivered.",
            )
            with self.assertRaisesRegex(RuntimeAdapterError, "permit"):
                record_manual_decision(
                    directory, "TASK-RUNTIME", action="revoke",
                    target_receipt_id=receipt["receipt_id"], authority="operator-1",
                    authority_ref="authorities/operator-1.json", statement="Withdraw.",
                )

    def test_revoked_manual_receipt_cannot_support_a_wait_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            receipt, _ = record_manual_attestation(
                directory, "TASK-RUNTIME", **self.identity(),
                event="manual_delivery_attested", authority="operator-1",
                authority_ref="authorities/operator-1.json", statement="Delivered.",
            )
            record_manual_decision(
                directory, "TASK-RUNTIME", action="revoke",
                target_receipt_id=receipt["receipt_id"], authority="operator-1",
                authority_ref="authorities/operator-1.json", statement="Withdraw.",
            )
            self.assertFalse(
                manual_receipt_is_effective(
                    directory, "TASK-RUNTIME", receipt["receipt_id"]
                )
            )
            error = wait_receipt_event_error(
                {
                    "event": "work_item_completed",
                    "receipt_ref": "dispatch-receipts.jsonl#1",
                    "work_item_id": "work-codex",
                },
                None,
                [receipt],
                "TASK-RUNTIME",
                directory,
            )
            self.assertEqual(
                error,
                "Wait event receipt was revoked or not selected by Manual adjudication",
            )

    def test_manual_conflict_fails_closed_then_adjudication_and_revocation_are_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            (directory / "agents/codex/evidence.md").write_text("verified\n", encoding="utf-8")
            first, _ = record_manual_attestation(
                directory, "TASK-RUNTIME", **self.identity(),
                event="manual_result_attested", authority="operator-1",
                authority_ref="authorities/operator-1.json", statement="Result accepted by operator 1.",
            )
            second, _ = record_manual_attestation(
                directory, "TASK-RUNTIME", **self.identity(),
                event="manual_result_attested", authority="operator-2",
                authority_ref="authorities/operator-2.json", statement="Different result accepted by operator 2.",
            )
            with self.assertRaisesRegex(RuntimeAdapterError, "conflicting active Manual attestations"):
                manual_effective_receipt_ids(directory, "TASK-RUNTIME")

            decision = record_manual_decision(
                directory,
                "TASK-RUNTIME",
                action="adjudicate",
                target_receipt_id=first["receipt_id"],
                conflicting_receipt_ids=[first["receipt_id"], second["receipt_id"]],
                authority="lead-operator",
                authority_ref="authorities/lead-operator.json",
                statement="Select operator 1 attestation after review.",
            )
            self.validate_schema("manual-attestation-decision.schema.json", decision)
            self.assertEqual(manual_effective_receipt_ids(directory, "TASK-RUNTIME"), {first["receipt_id"]})
            self.assertTrue(manual_receipt_is_effective(directory, "TASK-RUNTIME", first["receipt_id"]))
            self.assertFalse(manual_receipt_is_effective(directory, "TASK-RUNTIME", second["receipt_id"]))

            before = (directory / "runtime/manual/attestation-decisions.jsonl").read_bytes()
            self.assertEqual(record_manual_decision(
                directory,
                "TASK-RUNTIME",
                action="adjudicate",
                target_receipt_id=first["receipt_id"],
                conflicting_receipt_ids=[first["receipt_id"], second["receipt_id"]],
                authority="lead-operator",
                authority_ref="authorities/lead-operator.json",
                statement="Select operator 1 attestation after review.",
            ), decision)
            self.assertEqual(
                (directory / "runtime/manual/attestation-decisions.jsonl").read_bytes(), before
            )

            revocation = record_manual_decision(
                directory,
                "TASK-RUNTIME",
                action="revoke",
                target_receipt_id=first["receipt_id"],
                authority="lead-operator",
                authority_ref="authorities/lead-operator.json",
                statement="The selected attestation is withdrawn.",
            )
            self.validate_schema("manual-attestation-decision.schema.json", revocation)
            with self.assertRaisesRegex(RuntimeAdapterError, "no longer resolves"):
                manual_receipt_is_effective(directory, "TASK-RUNTIME", first["receipt_id"])

    def test_adopted_runtime_rejects_nonempty_legacy_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            (directory / "dispatch-receipts.jsonl").write_text(
                json.dumps({"event": "dispatch_written"}) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RuntimeAdapterError, "mixed"):
                record_queue_acceptance(directory, "TASK-RUNTIME", **self.identity())

    def test_manual_cli_records_canonical_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = self.task(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "adapter", "manual", "attest", "TASK-RUNTIME",
                    "--workspace", str(root),
                    "--agent", "codex",
                    "--role", "implementer",
                    "--event", "manual_delivery_attested",
                    "--authority", "operator-1",
                    "--authority-ref", "authorities/operator-1.json",
                    "--statement", "I delivered the exact dispatch.",
                    "--json",
                ])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["receipt"]["event"], "manual_delivery_attested")
            self.assertEqual(result["observation"]["status"], "accepted")
            self.assertTrue((directory / "runtime/manual/receipts.v3.jsonl").is_file())

    def test_queue_observe_and_manual_revoke_cli_paths_are_operational(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = self.task(root)
            payload_digest = digest((directory / "agents/codex/dispatch.md").read_bytes())
            submitted, _ = record_herdr_submission(
                directory, "TASK-RUNTIME", **self.identity(),
                proof=self.herdr_proof(payload_digest),
            )
            (directory / "agents/codex/evidence.md").write_text("verified\n", encoding="utf-8")
            observation_ref = "runtime/herdr/terminal-observations/run-cli.json"
            observation_path = directory / observation_ref
            observation_path.parent.mkdir(parents=True)
            observation_path.write_text(
                json.dumps(self.herdr_terminal_proof()), encoding="utf-8"
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "adapter", "herdr", "observe", "TASK-RUNTIME",
                    "--workspace", str(root), "--agent", "codex",
                    "--role", "implementer", "--attempt-id", submitted["attempt_id"],
                    "--observation-ref", observation_ref, "--json",
                ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["receipt"]["event"], "dispatch_completed")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = self.task(root)
            queue, submitted, _ = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            claim = record_queue_claim(
                directory, "TASK-RUNTIME", submitted,
                worker_id="worker-cli", run_id="run-cli",
                claim_token="claim-cli", expected_revision=0,
            )
            (directory / "agents/codex/evidence.md").write_text("verified\n", encoding="utf-8")
            observation_ref = "runtime/queue/worker-observations/run-cli.json"
            observation_path = directory / observation_ref
            observation_path.parent.mkdir(parents=True)
            observation_path.write_text(json.dumps({
                "schema_version": "valp-queue-worker-observation.v2",
                "task_id": "TASK-RUNTIME",
                "queue_id": queue["queue_id"],
                "enqueue_transaction_id": queue["enqueue_transaction_id"],
                "worker_id": "worker-cli",
                "run_id": "run-cli",
                "claim_token": "claim-cli",
                "claim_event_id": claim["event_id"],
                "observation_sequence": 1,
                "status": "completed",
                "acknowledged": True,
            }), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "adapter", "queue", "observe", "TASK-RUNTIME",
                    "--workspace", str(root), "--agent", "codex",
                    "--role", "implementer", "--observation-ref", observation_ref,
                    "--json",
                ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["receipt"]["event"], "dispatch_completed")
            self.assertEqual(submitted["attempt_id"], json.loads(output.getvalue())["receipt"]["attempt_id"])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = self.task(root)
            receipt, _ = record_manual_attestation(
                directory, "TASK-RUNTIME", **self.identity(),
                event="manual_delivery_attested", authority="operator-1",
                authority_ref="authorities/operator-1.json", statement="Delivered.",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "adapter", "manual", "revoke", "TASK-RUNTIME",
                    "--workspace", str(root), "--receipt-id", receipt["receipt_id"],
                    "--authority", "operator-1",
                    "--authority-ref", "authorities/operator-1.json",
                    "--statement", "Withdraw delivery claim.", "--json",
                ])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["action"], "revoke")

    def test_queue_claim_cancel_ack_cli_paths_are_operational(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = self.task(root)
            _, submitted, _ = record_queue_acceptance(
                directory, "TASK-RUNTIME", **self.identity()
            )
            common = [
                "TASK-RUNTIME", "--workspace", str(root),
                "--agent", "codex", "--role", "implementer",
                "--attempt-id", submitted["attempt_id"], "--json",
            ]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([
                    "adapter", "queue", "claim", *common,
                    "--worker-id", "worker-cli", "--run-id", "run-cli",
                    "--claim-token", "claim-cli", "--expected-revision", "0",
                ]), 0)
            claim = json.loads(output.getvalue())["lifecycle"]
            self.assertEqual(claim["event"], "claimed")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([
                    "adapter", "queue", "cancel", *common,
                    "--authority", "operator-1", "--reason", "stop",
                    "--expected-revision", "1",
                ]), 0)
            self.assertEqual(
                json.loads(output.getvalue())["lifecycle"]["event"],
                "cancellation_requested",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([
                    "adapter", "queue", "ack-cancel", *common,
                    "--worker-id", "worker-cli", "--run-id", "run-cli",
                    "--claim-token", "claim-cli", "--claim-event-id", claim["event_id"],
                    "--expected-revision", "2",
                ]), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["lifecycle"]["event"], "cancelled")
            self.assertEqual(result["observation"]["status"], "cancelled")

    def test_adopted_runtime_wait_wake_is_durable_kernel_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            identity = self.identity()
            payload_digest = digest((directory / "agents/codex/dispatch.md").read_bytes())
            submitted, _ = record_herdr_submission(
                directory,
                "TASK-RUNTIME",
                **identity,
                proof=self.herdr_proof(payload_digest),
            )
            policy = {
                "schema_version": "valp-wait-policy.v1",
                "task_id": "TASK-RUNTIME",
                "wait_policy_id": "wait-policy-1",
                "required_work_items": [{
                    "work_item_id": identity["work_item_id"],
                }],
            }
            (directory / "wait-policy.json").write_text(json.dumps(policy), encoding="utf-8")
            suspension = {
                "status": "waiting",
                "suspension_id": "suspension-1",
                "suspension_epoch": 1,
                "wait_policy_ref": "wait-policy.json",
                "wait_policy_id": "wait-policy-1",
                "required_work_items": [{
                    "agent": identity["agent"],
                    "role": identity["role"],
                    "work_item_id": identity["work_item_id"],
                    "dispatch_id": identity["dispatch_id"],
                    "dispatch_generation": identity["dispatch_generation"],
                    "expected_refs": identity["expected_refs"],
                }],
            }
            binding = start_kernel_suspension(
                directory, "TASK-RUNTIME", suspension, [submitted]
            )
            self.assertEqual(binding["workflow_suspension_epoch"], 1)
            self.assertEqual(binding["kernel_suspension_epoch"], 0)
            self.validate_schema("kernel-workflow-binding.schema.json", binding)
            recovered = KernelStore(directory / "runtime/kernel").recover().replay.state
            self.assertEqual(recovered.status, TaskStatus.EXECUTING)
            self.assertEqual(recovered.suspension.status, SuspensionStatus.WAITING)

            (directory / "agents/codex/evidence.md").write_text("verified\n", encoding="utf-8")
            completed, _ = record_herdr_completion(
                directory, "TASK-RUNTIME", submitted, identity["expected_refs"],
                self.herdr_terminal_proof(),
            )
            record_kernel_completion(directory, "TASK-RUNTIME", completed)
            before_retry = (directory / "runtime/kernel/replay.jsonl").read_bytes()
            record_kernel_completion(directory, "TASK-RUNTIME", completed)
            self.assertEqual((directory / "runtime/kernel/replay.jsonl").read_bytes(), before_retry)
            accept_kernel_wake(directory, "TASK-RUNTIME", "wake-1")

            restarted = KernelStore(directory / "runtime/kernel").recover().replay.state
            self.assertEqual(restarted.suspension.status, SuspensionStatus.RESUMED)
            self.assertEqual(restarted.work_items[0].status, WorkItemStatus.COMPLETED)
            self.assertEqual(restarted.suspension.accepted_wake_id.value, "wake-1")

    def test_dependency_wake_prepares_one_identity_bound_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            (directory / "control-contract.json").write_text("{}\n", encoding="utf-8")
            (directory / "continuations").mkdir()
            (directory / "continuations/identity.json").write_text("{}\n", encoding="utf-8")
            (directory / "continuations/dedup.json").write_text("{}\n", encoding="utf-8")
            capability = capability_declaration(
                "provider-adapter", "1", "provider-1", "hermes",
                automatic_full=True,
                invocation_proof=True,
                duplicate_suppression=True,
                identity_evidence_ref="continuations/identity.json",
                duplicate_suppression_evidence_ref="continuations/dedup.json",
            )
            store = ContinuationStore(directory, "TASK-RUNTIME")
            store.register_capability(capability)
            (directory / "continuations/runtime-binding.json").write_text(
                json.dumps({
                    "schema_version": "valp-continuation-runtime-binding.v1",
                    "adapter_id": "provider-adapter",
                    "provider_id": "provider-1",
                    "coordinator_agent": "hermes",
                    "durable_boundary_ref": "provider-session:session-1",
                }),
                encoding="utf-8",
            )
            self.validate_schema(
                "continuation-runtime-binding.schema.json",
                json.loads((directory / "continuations/runtime-binding.json").read_text()),
            )
            suspension_id = "sha256:" + "a" * 64
            wake_id = "sha256:" + "b" * 64
            wake_event_id = "sha256:" + "c" * 64
            result_ref = f"wake-results/{wake_id[7:]}.json"
            (directory / "wake-results").mkdir()
            wake_result = {
                "wake_id": wake_id,
                "completed_work_item_ids": ["work-codex"],
                "pending_work_item_ids": [],
            }
            (directory / result_ref).write_text(json.dumps(wake_result), encoding="utf-8")
            suspension = {
                "suspension_id": suspension_id,
                "suspension_epoch": 1,
                "accepted_wake": {
                    "wake_id": wake_id,
                    "wake_event_id": wake_event_id,
                    "wake_reason": "dependency_ready",
                    "resulting_state_revision": 4,
                    "result_ref": result_ref,
                },
            }
            (directory / "state.json").write_text(
                json.dumps({
                    "schema_version": "valp-visible-loop-state.v2",
                    "task_id": "TASK-RUNTIME",
                    "status": "suspended",
                    "revision": 3,
                    "suspension": suspension,
                }),
                encoding="utf-8",
            )
            first = prepare_wake_continuation(
                directory, "TASK-RUNTIME", suspension, wake_result
            )
            before = (directory / "continuations/events.jsonl").read_bytes()
            second = prepare_wake_continuation(
                directory, "TASK-RUNTIME", suspension, wake_result
            )
            self.assertEqual(first, second)
            self.assertEqual((directory / "continuations/events.jsonl").read_bytes(), before)
            self.assertEqual([item["event"] for item in store.events()], ["resume_pending"])

    def test_kernel_runtime_registers_full_graph_and_starts_second_frontier_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.task(Path(tmp))
            (directory / "agents/reviewer").mkdir()
            (directory / "agents/reviewer/dispatch.md").write_text("Review.\n", encoding="utf-8")
            dependencies = {
                "schema_version": "valp-submission-dependencies.v2",
                "task_id": "TASK-RUNTIME",
                "work_items": [
                    {**self.identity()},
                    {
                        "agent": "reviewer", "role": "reviewer",
                        "work_item_id": "work-reviewer", "dispatch_id": "dispatch-reviewer",
                        "dispatch_generation": 1,
                        "dispatch_ref": "agents/reviewer/dispatch.md",
                        "expected_refs": ["agents/reviewer/review.md"],
                    },
                ],
                "dependencies": [{
                    "id": "implementation-before-review",
                    "prerequisite_work_item_id": "work-codex",
                    "dependent_work_item_id": "work-reviewer",
                }],
            }
            (directory / "submission-dependencies.json").write_text(
                json.dumps(dependencies), encoding="utf-8"
            )
            first_payload = digest((directory / "agents/codex/dispatch.md").read_bytes())
            first, _ = record_herdr_submission(
                directory, "TASK-RUNTIME", **self.identity(), proof=self.herdr_proof(first_payload)
            )
            (directory / "wait-policy.json").write_text(json.dumps({
                "schema_version": "valp-wait-policy.v1", "task_id": "TASK-RUNTIME",
                "wait_policy_id": "policy-1",
            }), encoding="utf-8")
            first_suspension = {
                "status": "waiting", "suspension_id": "suspension-1",
                "suspension_epoch": 1, "wait_policy_ref": "wait-policy.json",
                "wait_policy_id": "policy-1", "required_work_items": [self.identity()],
            }
            start_kernel_suspension(directory, "TASK-RUNTIME", first_suspension, [first])
            initial = KernelStore(directory / "runtime/kernel").recover().replay.state
            self.assertEqual(len(initial.work_items), 2)
            self.assertEqual(initial.work_items[1].status, WorkItemStatus.PENDING)
            (directory / "agents/codex/evidence.md").write_text("done\n", encoding="utf-8")
            completed, _ = record_herdr_completion(
                directory, "TASK-RUNTIME", first, self.identity()["expected_refs"],
                self.herdr_terminal_proof(),
            )
            record_kernel_completion(directory, "TASK-RUNTIME", completed)
            accept_kernel_wake(directory, "TASK-RUNTIME", "wake-1")

            reviewer_identity = dependencies["work_items"][1]
            reviewer_proof = self.herdr_proof(
                digest((directory / reviewer_identity["dispatch_ref"]).read_bytes())
            )
            reviewer_proof["agent_ref"] = "reviewer"
            reviewer_proof["pane_id"] = "pane-2"
            reviewer_proof["submission_proof"]["identity"].update({
                "agent": "reviewer", "pane_id": "pane-2",
            })
            reviewer_proof["submission_proof"].update({
                "baseline_state_change_seq": 10, "state_change_seq": 11,
            })
            second, _ = record_herdr_submission(
                directory, "TASK-RUNTIME", **reviewer_identity, proof=reviewer_proof
            )
            (directory / "wait-policy.json").write_text(json.dumps({
                "schema_version": "valp-wait-policy.v1", "task_id": "TASK-RUNTIME",
                "wait_policy_id": "policy-2",
            }), encoding="utf-8")
            second_suspension = {
                "status": "waiting", "suspension_id": "suspension-2",
                "suspension_epoch": 2, "wait_policy_ref": "wait-policy.json",
                "wait_policy_id": "policy-2", "required_work_items": [reviewer_identity],
            }
            binding = start_kernel_suspension(
                directory, "TASK-RUNTIME", second_suspension, [first, completed, second]
            )
            restarted = KernelStore(directory / "runtime/kernel").recover().replay.state
            reviewer = next(
                item for item in restarted.work_items if item.work_item_id.value == "work-reviewer"
            )
            self.assertEqual(reviewer.status, WorkItemStatus.RUNNING)
            self.assertEqual(reviewer.current_attempt.attempt_id.value, second["attempt_id"])
            self.assertEqual(binding["kernel_suspension_epoch"], 1)


if __name__ == "__main__":
    unittest.main()
