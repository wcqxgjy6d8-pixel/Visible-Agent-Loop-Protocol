from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator

from valp_cli.audit import FAIL, PASS, TaskAudit
from valp_cli.cli import main
from valp_cli.continuation import (
    ContinuationError,
    ContinuationStore,
    HerdrCoordinatorContinuationAdapter,
    HermesCliAdapter,
    HermesRuntimeControlAdapter,
    SafePointQueue,
    SubprocessRuntimeControlAdapter,
    build_envelope,
    capability_declaration,
    file_digest,
    idempotency_key,
)


class ContinuationStoreTests(unittest.TestCase):
    def setup_store(self, root: Path, epoch: int = 1) -> ContinuationStore:
        (root / "control-contract.json").write_text('{"task_id":"TASK-1"}\n', encoding="utf-8")
        (root / "state.json").write_text(
            json.dumps({"task_id": "TASK-1", "suspension": {"suspension_id": "sha256:" + "a" * 64, "suspension_epoch": epoch}}) + "\n",
            encoding="utf-8",
        )
        (root / "evidence").mkdir(exist_ok=True)
        (root / "evidence/provider-identity.json").write_text('{"invocation_id":"provider-turn-1"}\n', encoding="utf-8")
        (root / "evidence/provider-dedup.json").write_text('{"idempotency":"provider-owned"}\n', encoding="utf-8")
        return ContinuationStore(root, "TASK-1")

    def envelope(self, root: Path, payload: dict, *, epoch: int = 1, wake: str = "b", generation: int = 1, adapter: str = "test-adapter", provider: str = "fake-provider", coordinator: str = "coordinator") -> dict:
        return build_envelope(
            task_id="TASK-1", suspension_id="sha256:" + "a" * 64, suspension_epoch=epoch,
            wake_id="sha256:" + wake * 64, wake_event_id="sha256:" + "c" * 64,
            wake_reason="dependency_ready", accepted_state_revision=2,
            control_contract_ref="control-contract.json", control_contract_digest=file_digest(root / "control-contract.json"),
            payload=payload, coordinator_agent=coordinator, adapter_id=adapter, provider_id=provider,
            durable_boundary_ref="provider-session:session-1", continuation_generation=generation,
        )

    def capability(self) -> dict:
        return capability_declaration(
            "test-adapter", "1.0", "fake-provider", "coordinator",
            automatic_full=True, invocation_proof=True, duplicate_suppression=True,
            identity_evidence_ref="evidence/provider-identity.json",
            duplicate_suppression_evidence_ref="evidence/provider-dedup.json",
        )

    def receipt(self, envelope: dict, **changes: object) -> dict:
        value = {
            "schema_version": "valp-continuation-invocation-receipt.v1",
            "task_id": envelope["task_id"],
            "suspension_id": envelope["suspension_id"],
            "suspension_epoch": envelope["suspension_epoch"],
            "wake_id": envelope["wake_id"],
            "continuation_generation": envelope["continuation_generation"],
            "idempotency_key": idempotency_key(envelope),
            "payload_digest": envelope["payload_digest"],
            "adapter": {"id": "test-adapter", "version": "1.0"},
            "provider": {"id": "fake-provider", "invocation_id": "provider-turn-1", "turn_id": "turn-1"},
            "durable_boundary_ref": envelope["target"]["durable_boundary_ref"],
            "identity_evidence_ref": "evidence/provider-identity.json",
            "duplicate_suppression_ref": "evidence/provider-dedup.json",
            "started_at": "2026-07-21T00:00:00Z",
            "consumed_at": "2026-07-21T00:00:01Z",
            "result": "consumed",
        }
        value.update(changes)
        return value

    def prepare(self, root: Path, payload: dict) -> tuple[ContinuationStore, dict]:
        store = self.setup_store(root)
        envelope = self.envelope(root, payload)
        store.register_capability(self.capability())
        store.pending(envelope, payload)
        store.receive(envelope, payload)
        return store, envelope

    def test_six_event_chain_persists_complete_receipt_and_replays(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, envelope = self.prepare(root, {"wake": "ready"})
            calls: list[int] = []
            receipt = store.consume(envelope, lambda: calls.append(1) or self.receipt(envelope))
            duplicate = store.consume(envelope, lambda: calls.append(2) or self.receipt(envelope))
            self.assertEqual(receipt, duplicate)
            self.assertEqual(calls, [1])
            self.assertEqual([event["event"] for event in store.events()], list(("resume_pending", "resume_received", "digest_verified", "resume_accepted", "continuation_started", "resume_consumed")))
            persisted = root / "continuations" / ("b" * 64) / "invocation-receipt.json"
            self.assertEqual(json.loads(persisted.read_text()), receipt)

    def test_herdr_coordinator_continuation_invokes_once_and_completes_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.setup_store(root)
            payload = {"wake": "ready"}
            envelope = self.envelope(
                root,
                payload,
                adapter="herdr-coordinator-continuation",
                provider="herdr",
                coordinator="codex",
            )
            requests: list[dict[str, object]] = []

            def continue_coordinator(request: dict[str, object]) -> dict[str, object]:
                requests.append(request)
                return {
                    "schema_version": "valp-herdr-coordinator-continuation-response.v1",
                    "status": "consumed",
                    "receipt": self.receipt(
                        envelope,
                        adapter={"id": "herdr-coordinator-continuation", "version": "1"},
                        provider={
                            "id": "herdr",
                            "invocation_id": "herdr-invocation-1",
                            "turn_id": "herdr-turn-1",
                        },
                    ),
                }

            adapter = HerdrCoordinatorContinuationAdapter(
                runtime_session_id="herdr-session-1",
                provider_id="herdr",
                coordinator_agent="codex",
                continue_coordinator=continue_coordinator,
                identity_evidence_ref="evidence/provider-identity.json",
                duplicate_suppression_evidence_ref="evidence/provider-dedup.json",
            )
            store.register_capability(adapter.capability())
            store.pending(envelope, payload)
            store.receive(envelope, payload)

            first = store.consume_with_adapter(envelope, payload, adapter)
            replay = store.consume_with_adapter(envelope, payload, adapter)

            self.assertEqual(first, replay)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["method"], "coordinator.continue")
            self.assertEqual(requests[0]["session_id"], "herdr-session-1")
            self.assertEqual(requests[0]["channel"], {
                "kind": "runtime_control",
                "user_input_allowed": False,
                "raw_worker_output_allowed": False,
            })
            self.assertEqual(
                [event["event"] for event in store.events()],
                [
                    "resume_pending",
                    "resume_received",
                    "digest_verified",
                    "resume_accepted",
                    "continuation_started",
                    "resume_consumed",
                ],
            )

    def test_receive_cannot_bypass_persisted_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.setup_store(root)
            envelope = self.envelope(root, {"wake": "ready"})
            with self.assertRaisesRegex(ContinuationError, "persisted resume_pending"):
                store.receive(envelope, {"wake": "ready"})
            self.assertEqual(store.events()[-1]["event"], "continuation_rejected")
            self.assertFalse(any(event["event"] == "resume_received" for event in store.events()))

    def test_payload_and_target_conflicts_are_durably_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.setup_store(root)
            payload = {"wake": "ready"}
            envelope = self.envelope(root, payload)
            store.pending(envelope, payload)
            with self.assertRaisesRegex(ContinuationError, "payload"):
                store.receive(envelope, {"wake": "tampered"})
            conflict = dict(envelope)
            conflict["target"] = dict(envelope["target"], provider_id="other-provider")
            with self.assertRaisesRegex(ContinuationError, "conflicting continuation"):
                store.pending(conflict, payload)
            self.assertGreaterEqual(sum(event["event"] == "continuation_rejected" for event in store.events()), 2)

    def test_stale_epoch_is_durably_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.setup_store(root, epoch=2)
            stale = self.envelope(root, {"wake": "stale"}, epoch=1, wake="d")
            with self.assertRaisesRegex(ContinuationError, "stale suspension epoch"):
                store.pending(stale, {"wake": "stale"})
            self.assertEqual(store.events()[-1]["event"], "continuation_rejected")

    def test_digest_identifiers_reject_traversal_before_artifact_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.setup_store(root)
            payload = {"wake": "ready"}
            envelope = self.envelope(root, payload)
            envelope["wake_id"] = "sha256:" + ("b" * 63) + "/../../escape"
            with self.assertRaisesRegex(ContinuationError, "digest-shaped"):
                store.pending(envelope, payload)
            self.assertFalse((root / "escape").exists())
            self.assertFalse((root.parent / "escape").exists())

            for field in ("suspension_id", "wake_event_id"):
                malformed = self.envelope(root, payload, wake="d")
                malformed[field] = "local-id"
                with self.assertRaisesRegex(ContinuationError, "digest-shaped"):
                    store.pending(malformed, payload)

    def test_future_epoch_is_rejected_without_poisoning_state_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.setup_store(root, epoch=1)
            payload = {"wake": "future"}
            future = self.envelope(root, payload, epoch=2, wake="d")
            future_dir = root / "continuations" / ("d" * 64)
            future_dir.mkdir(parents=True)
            (future_dir / "envelope.json").write_text(json.dumps(future) + "\n", encoding="utf-8")
            (future_dir / "payload.json").write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ContinuationError, "future suspension epoch"):
                store.receive(future, payload)

            valid_payload = {"wake": "current"}
            valid = self.envelope(root, valid_payload, epoch=1, wake="e")
            store.pending(valid, valid_payload)
            self.assertTrue((root / "continuations" / ("e" * 64) / "envelope.json").is_file())

    def test_control_contract_digest_is_revalidated_after_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.setup_store(root)
            payload = {"wake": "ready"}
            envelope = self.envelope(root, payload)
            store.pending(envelope, payload)
            (root / "control-contract.json").write_text('{"task_id":"tampered"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ContinuationError, "control contract digest mismatch"):
                store.receive(envelope, payload)
            self.assertEqual(store.events()[-1]["event"], "continuation_rejected")

    def test_bare_id_and_fake_duplicate_proof_cannot_emit_consumption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, envelope = self.prepare(root, {"wake": "ready"})
            with self.assertRaisesRegex(ContinuationError, "complete provider invocation receipt"):
                store.consume(envelope, lambda: {"invocation_id": "bare"})
            self.assertFalse(any(event["event"] in {"continuation_started", "resume_consumed"} for event in store.events()))

    def test_provider_exception_records_indeterminate_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, envelope = self.prepare(root, {"wake": "ready"})
            with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                store.consume(envelope, lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")))
            self.assertEqual(store.events()[-1]["event"], "continuation_failed")
            self.assertIn("indeterminate", store.events()[-1]["reason"])
            with self.assertRaisesRegex(ContinuationError, "in flight or indeterminate"):
                store.consume(envelope, lambda: self.receipt(envelope))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, envelope = self.prepare(root, {"wake": "ready"})
            fake = self.receipt(envelope, duplicate_suppression_ref="continuations/events.jsonl")
            with self.assertRaisesRegex(ContinuationError, "correlation mismatch"):
                store.consume(envelope, lambda: fake)
            self.assertFalse(any(event["event"] in {"continuation_started", "resume_consumed"} for event in store.events()))

    def test_busy_queue_recovers_pending_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.setup_store(root)
            store.register_capability(self.capability())
            envelope = self.envelope(root, {"wake": "ready"})
            queue = SafePointQueue(store)
            queue.busy = True
            queue.enqueue(envelope, {"wake": "ready"})
            self.assertEqual(queue.safe_point(lambda _envelope, _payload: self.receipt(envelope)), [])
            recovered = SafePointQueue(ContinuationStore(root, "TASK-1"))
            result = recovered.safe_point(lambda restored, _payload: self.receipt(restored))
            self.assertEqual(result[0]["provider"]["invocation_id"], "provider-turn-1")

    def test_pending_continuation_survives_process_restart_and_replays_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.setup_store(root)
            store.register_capability(self.capability())
            payload = {"wake": "ready"}
            envelope = self.envelope(root, payload)
            store.pending(envelope, payload)
            script = """
import json
import sys
from pathlib import Path

from valp_cli.continuation import ContinuationStore, idempotency_key

root = Path(sys.argv[1])
wake_dir = root / "continuations" / ("b" * 64)
envelope = json.loads((wake_dir / "envelope.json").read_text(encoding="utf-8"))
payload = json.loads((wake_dir / "payload.json").read_text(encoding="utf-8"))
store = ContinuationStore(root, "TASK-1")
if sys.argv[2] == "receive":
    store.receive(envelope, payload)

def invoke():
    calls_path = root / "evidence" / "provider-calls.jsonl"
    with calls_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"idempotency_key": idempotency_key(envelope)}) + "\\n")
    return {
        "schema_version": "valp-continuation-invocation-receipt.v1",
        "task_id": envelope["task_id"],
        "suspension_id": envelope["suspension_id"],
        "suspension_epoch": envelope["suspension_epoch"],
        "wake_id": envelope["wake_id"],
        "continuation_generation": envelope["continuation_generation"],
        "idempotency_key": idempotency_key(envelope),
        "payload_digest": envelope["payload_digest"],
        "adapter": {"id": "test-adapter", "version": "1.0"},
        "provider": {
            "id": "fake-provider",
            "invocation_id": "provider-turn-1",
            "turn_id": "turn-1",
        },
        "durable_boundary_ref": envelope["target"]["durable_boundary_ref"],
        "identity_evidence_ref": "evidence/provider-identity.json",
        "duplicate_suppression_ref": "evidence/provider-dedup.json",
        "started_at": "2026-07-21T00:00:00Z",
        "consumed_at": "2026-07-21T00:00:01Z",
        "result": "consumed",
    }

print(json.dumps(store.consume(envelope, invoke), sort_keys=True))
"""

            first = subprocess.run(
                [sys.executable, "-c", script, str(root), "receive"],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )
            replay = subprocess.run(
                [sys.executable, "-c", script, str(root), "replay"],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(replay.returncode, 0, replay.stderr)
            self.assertEqual(json.loads(first.stdout), json.loads(replay.stdout))
            calls = (root / "evidence/provider-calls.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(calls), 1)
            restarted = ContinuationStore(root, "TASK-1")
            self.assertEqual(
                [event["event"] for event in restarted.events()],
                list(("resume_pending", "resume_received", "digest_verified", "resume_accepted", "continuation_started", "resume_consumed")),
            )

    def test_committed_receipt_recovers_missing_terminal_events_without_reinvoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, envelope = self.prepare(root, {"wake": "ready"})
            store.claim(envelope)
            original_append = store._append

            def crash_before_started(event: str, value: dict, **extra: object) -> dict:
                if event == "continuation_started":
                    raise OSError("injected process crash")
                return original_append(event, value, **extra)

            with patch.object(store, "_append", side_effect=crash_before_started):
                with self.assertRaisesRegex(OSError, "injected process crash"):
                    store.consume(envelope, lambda: self.receipt(envelope))
            receipt_path = root / "continuations" / ("b" * 64) / "invocation-receipt.json"
            self.assertTrue(receipt_path.is_file())

            restarted = ContinuationStore(root, "TASK-1")
            recovered = restarted.consume(
                envelope,
                lambda: self.fail("provider must not be reinvoked"),
            )
            self.assertEqual(recovered, self.receipt(envelope))
            self.assertEqual(
                [event["event"] for event in restarted.events()],
                list(("resume_pending", "resume_received", "digest_verified", "resume_accepted", "continuation_started", "resume_consumed")),
            )

    def test_external_subprocess_provider_is_invoked_once_and_reconciles_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            root = workspace / ".herdr-loop" / "tasks" / "TASK-1"
            root.mkdir(parents=True)
            store = self.setup_store(root)
            payload = {"wake": "ready"}
            envelope = self.envelope(
                root, payload, adapter="subprocess-runtime-control",
                provider="local-provider", coordinator="coordinator",
            )
            provider_root = root / "provider-state"
            script = root / "provider.py"
            script.write_text('''
import json
from pathlib import Path
import sys

provider_root = Path(sys.argv[1])
provider_root.mkdir(parents=True, exist_ok=True)
request = json.load(sys.stdin)
params = request["params"]
envelope = params["envelope"]
state_path = provider_root / "receipt.json"
calls_path = provider_root / "calls.jsonl"
with calls_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"method": request["method"], "key": params["idempotency_key"]}) + "\\n")
if request["method"] == "runtime_control.submit":
    receipt = {
        "schema_version": "valp-continuation-invocation-receipt.v1",
        "task_id": envelope["task_id"],
        "suspension_id": envelope["suspension_id"],
        "suspension_epoch": envelope["suspension_epoch"],
        "wake_id": envelope["wake_id"],
        "continuation_generation": envelope["continuation_generation"],
        "idempotency_key": params["idempotency_key"],
        "payload_digest": envelope["payload_digest"],
        "adapter": {"id": "subprocess-runtime-control", "version": "1"},
        "provider": {"id": "local-provider", "invocation_id": "local-invocation-1", "turn_id": "local-turn-1"},
        "durable_boundary_ref": envelope["target"]["durable_boundary_ref"],
        "identity_evidence_ref": "evidence/provider-identity.json",
        "duplicate_suppression_ref": "evidence/provider-dedup.json",
        "started_at": "2026-08-05T00:00:00Z",
        "consumed_at": "2026-08-05T00:00:01Z",
        "result": "consumed",
    }
    state_path.write_text(json.dumps(receipt), encoding="utf-8")
else:
    receipt = json.loads(state_path.read_text(encoding="utf-8"))
print(json.dumps({"result": {"status": "consumed", "receipt": receipt}}))
''', encoding="utf-8")
            adapter = SubprocessRuntimeControlAdapter(
                command=(sys.executable, str(script), str(provider_root)),
                provider_id="local-provider", coordinator_surface="coordinator",
                identity_evidence_ref="evidence/provider-identity.json",
                duplicate_suppression_evidence_ref="evidence/provider-dedup.json",
                timeout=5,
            )
            store.register_capability(adapter.capability())
            store.pending(envelope, payload)
            store.receive(envelope, payload)

            command = [
                "adapter", "continuation", "TASK-1",
                "--workspace", str(workspace),
                "--command-json", json.dumps(list(adapter.command)),
                "--provider-id", "local-provider",
                "--coordinator-surface", "coordinator",
                "--identity-evidence-ref", "evidence/provider-identity.json",
                "--duplicate-suppression-ref", "evidence/provider-dedup.json",
                "--json",
            ]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(command), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "dry_run")
            self.assertFalse((provider_root / "calls.jsonl").exists())

            def consumed_then_crash() -> dict:
                adapter.invoke(envelope, payload)
                raise OSError("crash after provider consumption")

            with self.assertRaisesRegex(OSError, "crash after provider consumption"):
                store.consume(
                    envelope,
                    consumed_then_crash,
                    reconcile=lambda: adapter.reconcile(envelope, payload),
                )
            intent_path = root / "continuations" / ("b" * 64) / "invocation.inflight"
            intent_schema = json.loads((
                Path(__file__).parents[1]
                / "schemas/continuation-invocation-intent.schema.json"
            ).read_text(encoding="utf-8"))
            Draft202012Validator(intent_schema).validate(
                json.loads(intent_path.read_text(encoding="utf-8"))
            )
            self.assertEqual(
                TaskAudit(root, strict=True).check_continuation_ledger().status,
                PASS,
            )
            receipt = ContinuationStore(root, "TASK-1").consume_with_adapter(
                envelope, payload, adapter
            )
            replay = ContinuationStore(root, "TASK-1").consume_with_adapter(
                envelope, payload, adapter
            )
            self.assertEqual(receipt, replay)
            self.assertEqual(
                TaskAudit(root, strict=True).check_continuation_ledger().status,
                PASS,
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([*command, "--approve"]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "consumed")
            calls = [
                json.loads(line)
                for line in (provider_root / "calls.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [item["method"] for item in calls],
                ["runtime_control.submit", "runtime_control.status"],
            )

    def test_unknown_locking_platform_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self.setup_store(Path(tmp))
            with patch("valp_cli.continuation.fcntl", None), patch("valp_cli.continuation.msvcrt", None):
                with self.assertRaisesRegex(ContinuationError, "no supported exclusive"):
                    store.events()

    def test_hermes_is_manual_and_cannot_invoke(self) -> None:
        adapter = HermesCliAdapter()
        self.assertEqual(adapter.capability()["mode"], "manual")
        with self.assertRaisesRegex(ContinuationError, "unsupported"):
            adapter.invoke({}, {})

    def test_hermes_runtime_control_waits_for_real_provider_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = self.setup_store(root)
            payload = {"wake": "ready"}
            envelope = self.envelope(
                root,
                payload,
                adapter="hermes-tui-runtime-control",
                provider="test-provider",
                coordinator="hermes",
            )
            envelope["target"]["durable_boundary_ref"] = "session-live-1"
            receipt = {
                "schema_version": "valp-continuation-invocation-receipt.v1",
                "task_id": envelope["task_id"],
                "suspension_id": envelope["suspension_id"],
                "suspension_epoch": envelope["suspension_epoch"],
                "wake_id": envelope["wake_id"],
                "continuation_generation": envelope["continuation_generation"],
                "idempotency_key": idempotency_key(envelope),
                "payload_digest": envelope["payload_digest"],
                "adapter": {"id": "hermes-tui-runtime-control", "version": "1"},
                "provider": {
                    "id": "test-provider",
                    "invocation_id": "chatcmpl-provider-1",
                    "turn_id": "session-live-1:runtime-control:turn-1",
                },
                "durable_boundary_ref": "session-live-1",
                "identity_evidence_ref": "evidence/provider-identity.json",
                "duplicate_suppression_ref": "evidence/provider-dedup.json",
                "started_at": "2026-07-21T00:00:00Z",
                "consumed_at": "2026-07-21T00:00:01Z",
                "result": "consumed",
            }
            calls: list[str] = []

            def rpc_call(method: str, _params: dict) -> dict:
                calls.append(method)
                if method == "runtime_control.submit":
                    return {"result": {"status": "pending"}}
                if calls.count("runtime_control.status") == 1:
                    return {"result": {"status": "pending"}}
                return {"result": {"status": "consumed", "receipt": receipt}}

            adapter = HermesRuntimeControlAdapter(
                runtime_session_id="ui-live-1",
                provider_id="test-provider",
                rpc_call=rpc_call,
                identity_evidence_ref="evidence/provider-identity.json",
                duplicate_suppression_evidence_ref="evidence/provider-dedup.json",
                poll_interval=0.01,
                timeout=1,
            )
            store.register_capability(adapter.capability())
            store.pending(envelope, payload)
            store.receive(envelope, payload)

            result = store.consume(
                envelope, lambda: adapter.invoke(envelope, payload)
            )

            self.assertEqual(result, receipt)
            self.assertEqual(
                calls,
                [
                    "runtime_control.submit",
                    "runtime_control.status",
                    "runtime_control.status",
                ],
            )
            self.assertEqual(store.events()[-1]["event"], "resume_consumed")

    def test_strict_audit_recomputes_event_ids_and_correlates_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, envelope = self.prepare(root, {"wake": "ready"})
            store.consume(envelope, lambda: self.receipt(envelope))
            self.assertEqual(TaskAudit(root, strict=True).check_continuation_ledger().status, PASS)

            event_path = root / "continuations/events.jsonl"
            events = [json.loads(line) for line in event_path.read_text().splitlines()]
            events[0]["event_id"] = "sha256:" + "0" * 64
            event_path.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
            self.assertEqual(TaskAudit(root, strict=True).check_continuation_ledger().status, FAIL)

    def test_strict_audit_rejects_fabricated_bypass_and_capability_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, envelope = self.prepare(root, {"wake": "ready"})
            store.consume(envelope, lambda: self.receipt(envelope))
            (root / "continuations" / ("b" * 64) / "envelope.json").unlink()
            self.assertEqual(TaskAudit(root, strict=True).check_continuation_ledger().status, FAIL)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store, envelope = self.prepare(root, {"wake": "ready"})
            store.consume(envelope, lambda: self.receipt(envelope))
            capability_path = root / "continuations/capability.json"
            capability = json.loads(capability_path.read_text())
            capability["provider_id"] = "fabricated-provider"
            capability_path.write_text(json.dumps(capability) + "\n", encoding="utf-8")
            self.assertEqual(TaskAudit(root, strict=True).check_continuation_ledger().status, FAIL)


if __name__ == "__main__":
    unittest.main()
