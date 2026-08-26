from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import contextlib
import io
import json
from pathlib import Path
import errno
import tempfile
import time
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from valp_cli.kernel_store import (
    DURABILITY_PRECOMMIT_ERROR,
    DURABILITY_UNKNOWN_ERROR,
    IDEMPOTENCY_CONFLICT_ERROR,
    KERNEL_STORE_CORRUPT_ERROR,
    KernelStore,
    KernelStoreError,
    KernelEffectStatus,
)
from valp_cli.effect_runtime import execute_kernel_effect
from valp_cli.cli import main
from valp_cli.protocol_receipts import digest
from valp_cli.runtime_adapters import (
    record_queue_acceptance,
    record_queue_cancellation_acknowledgement,
    record_queue_claim,
)
from valp_cli.protocol_kernel import (
    Attempt,
    AttemptStatus,
    CancellationScope,
    CheckpointAuthentication,
    CheckpointRoot,
    CheckpointTrustPolicy,
    ControlReason,
    Evidence,
    Event,
    EventKind,
    GenesisRoot,
    Identity,
    IdentityKind,
    PROTOCOL_VERSION,
    ReplayEntry,
    ResultVariant,
    State,
    TaskStatus,
    WorkItem,
    WorkItemRequirement,
    WorkItemStatus,
    reduce,
    replay_prefix_digest,
)


class KernelStoreTests(unittest.TestCase):
    def make_history(self):
        installation = Identity(IdentityKind.INSTALLATION, "kernel-store-installation")
        task = Identity(IdentityKind.TASK, "kernel-store-task")
        genesis = GenesisRoot(State(
            PROTOCOL_VERSION, installation, 3, task, 0, TaskStatus.PUBLISHED,
        ))
        current = genesis.state
        entries = []
        for index, kind in enumerate((
            EventKind.ROUTING_VALIDATION_STARTED,
            EventKind.ROUTING_VALIDATION_PASSED,
            EventKind.DISPATCH_ACCEPTED,
        )):
            event = Event(
                Identity(IdentityKind.EVENT, f"kernel-store-event-{index}"),
                installation, 3, task, kind, current.revision,
            )
            result = reduce(current, event, ())
            self.assertEqual(result.variant, ResultVariant.ACCEPTED)
            entries.append(ReplayEntry(event, (), result))
            current = result.accepted.state
        return genesis, tuple(entries)

    def make_checkpoint(self, entries, count=2):
        prefix = entries[:count]
        checkpoint_result = prefix[-1].result
        state = checkpoint_result.accepted.state
        evidence_id = Identity(IdentityKind.EVIDENCE, "kernel-store-checkpoint-authority")
        policy = CheckpointTrustPolicy((evidence_id,))
        root = CheckpointRoot(
            state=state,
            accepted_entry_count=count,
            prefix_digest=replay_prefix_digest(prefix),
            tail_event_id=state.accepted_events[-1].event_id,
            tail_result_id=state.accepted_events[-1].result_id,
            tail_result_digest=state.accepted_events[-1].result_digest,
            checkpoint_result_id=checkpoint_result.accepted.result_id,
            trust_policy_digest=policy.digest,
        )
        digest = CheckpointAuthentication.statement_digest_for(
            root, checkpoint_result, policy)
        authentication = CheckpointAuthentication(
            checkpoint_result,
            (Evidence(evidence_id, digest),),
            policy,
        )
        return root, authentication

    def make_cancellation_history(self, attempt_value: str = "effect-attempt"):
        installation = Identity(IdentityKind.INSTALLATION, "effect-installation")
        task = Identity(IdentityKind.TASK, "effect-task")
        work = Identity(IdentityKind.WORK_ITEM, "effect-work")
        attempt = Identity(IdentityKind.ATTEMPT, attempt_value)
        dispatch = Identity(IdentityKind.DISPATCH, "effect-dispatch")
        authority = Identity(IdentityKind.EVIDENCE, "effect-authority")
        genesis = GenesisRoot(State(
            PROTOCOL_VERSION,
            installation,
            2,
            task,
            0,
            TaskStatus.PUBLISHED,
            work_items=(WorkItem(
                task,
                work,
                WorkItemRequirement.REQUIRED,
                WorkItemStatus.RUNNING,
                current_attempt=Attempt(
                    task, work, attempt, dispatch, 1, AttemptStatus.RUNNING,
                ),
            ),),
        ))
        event = Event(
            Identity(IdentityKind.EVENT, "effect-cancel"),
            installation,
            2,
            task,
            EventKind.ATTEMPT_CANCELLED,
            0,
            work_item_id=work,
            attempt_id=attempt,
            dispatch_id=dispatch,
            dispatch_generation=1,
            authority_principal_id=Identity(IdentityKind.PRINCIPAL, "effect-user"),
            authority_evidence_id=authority,
            control_reason=ControlReason.USER_REQUESTED,
            cancellation_scope=CancellationScope.ATTEMPT,
        )
        evidence = (Evidence(authority, "sha256:" + "b" * 64),)
        result = reduce(genesis.state, event, evidence)
        self.assertEqual(result.variant, ResultVariant.ACCEPTED)
        return genesis, ReplayEntry(event, evidence, result)

    def test_initialize_append_and_restart_recover_canonical_history(self) -> None:
        genesis, entries = self.make_history()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "kernel"
            store = KernelStore(root)
            store.initialize(genesis)
            for entry in entries:
                store.append(entry)

            recovered = KernelStore(root).recover()

            self.assertFalse(recovered.used_checkpoint)
            self.assertEqual(recovered.entries, entries)
            self.assertEqual(recovered.replay.state, entries[-1].result.accepted.state)
            self.assertEqual(recovered.replay.obligations, ())
            self.assertEqual(
                (root / "genesis.json").read_bytes(),
                (json.dumps(genesis.canonical(), sort_keys=True, separators=(",", ":")) + "\n").encode(),
            )

    def test_effect_reconciliation_is_durable_idempotent_and_restart_safe(self) -> None:
        genesis, entry = self.make_cancellation_history()
        with tempfile.TemporaryDirectory() as temporary:
            store = KernelStore(Path(temporary) / "kernel")
            store.initialize(genesis)
            store.append(entry)
            obligation = entry.result.accepted.obligations[0]

            pending = store.reconcile_effects()
            self.assertEqual(pending.pending, (obligation,))
            self.assertEqual(pending.fulfilled, ())

            record = store.record_effect(
                obligation,
                status=KernelEffectStatus.FULFILLED,
                proof_ref="runtime/herdr/cancel-proof.json",
                proof_digest="sha256:" + "c" * 64,
            )
            before = store.effects_path.read_bytes()
            retry = store.record_effect(
                obligation,
                status=KernelEffectStatus.FULFILLED,
                proof_ref="runtime/herdr/cancel-proof.json",
                proof_digest="sha256:" + "c" * 64,
            )
            self.assertEqual(retry, record)
            self.assertEqual(store.effects_path.read_bytes(), before)

            restarted = KernelStore(store.root).reconcile_effects()
            self.assertEqual(restarted.pending, ())
            self.assertEqual(restarted.fulfilled, (record,))
            self.assertEqual(restarted.blocked, ())
            effect_schema = json.loads(
                (Path(__file__).parents[1] / "schemas" / "kernel-effects.schema.json").read_text()
            )
            validator = Draft202012Validator(effect_schema)
            validator.validate(record.canonical())
            validator.validate(restarted.canonical())

    def test_effect_reconciliation_rejects_conflict_or_unaccepted_effect(self) -> None:
        genesis, entry = self.make_cancellation_history()
        with tempfile.TemporaryDirectory() as temporary:
            store = KernelStore(Path(temporary) / "kernel")
            store.initialize(genesis)
            store.append(entry)
            obligation = entry.result.accepted.obligations[0]
            store.record_effect(
                obligation,
                status=KernelEffectStatus.BLOCKED,
                proof_ref="runtime/herdr/cancel-blocked.json",
                proof_digest="sha256:" + "d" * 64,
            )
            before = store.effects_path.read_bytes()

            with self.assertRaises(KernelStoreError) as conflict:
                store.record_effect(
                    obligation,
                    status=KernelEffectStatus.FULFILLED,
                    proof_ref="runtime/herdr/cancel-proof.json",
                    proof_digest="sha256:" + "e" * 64,
                )
            self.assertEqual(conflict.exception.code, IDEMPOTENCY_CONFLICT_ERROR)
            with self.assertRaises(KernelStoreError):
                store.record_effect(
                    "adapter_cancel:{}",
                    status=KernelEffectStatus.FULFILLED,
                    proof_ref="runtime/herdr/unknown.json",
                    proof_digest="sha256:" + "f" * 64,
                )
            self.assertEqual(store.effects_path.read_bytes(), before)

    def test_effect_ledger_tamper_fails_closed_and_post_replace_uncertainty_reconciles(self) -> None:
        genesis, entry = self.make_cancellation_history()
        with tempfile.TemporaryDirectory() as temporary:
            store = KernelStore(Path(temporary) / "kernel")
            store.initialize(genesis)
            store.append(entry)
            obligation = entry.result.accepted.obligations[0]
            with patch(
                "valp_cli.kernel_store._sync_directory",
                side_effect=OSError(errno.EIO, "injected directory sync uncertainty"),
            ):
                record = store.record_effect(
                    obligation,
                    status=KernelEffectStatus.FULFILLED,
                    proof_ref="runtime/herdr/cancel-proof.json",
                    proof_digest="sha256:" + "1" * 64,
                )
            self.assertEqual(store.reconcile_effects().fulfilled, (record,))

            parsed = json.loads(store.effects_path.read_text())
            parsed["proof_digest"] = "sha256:" + "2" * 64
            store.effects_path.write_text(json.dumps(parsed) + "\n", encoding="utf-8")
            before = store.effects_path.read_bytes()
            with self.assertRaises(KernelStoreError) as raised:
                store.reconcile_effects()
            self.assertEqual(raised.exception.code, KERNEL_STORE_CORRUPT_ERROR)
            self.assertEqual(store.effects_path.read_bytes(), before)

    def test_kernel_effect_cli_requires_real_proof_and_reports_pending_gate(self) -> None:
        genesis, entry = self.make_cancellation_history()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            task = workspace / ".herdr-loop" / "tasks" / "TASK-EFFECT"
            store = KernelStore(task / "runtime" / "kernel")
            store.initialize(genesis)
            store.append(entry)
            obligation = entry.result.accepted.obligations[0]

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "kernel", "effects", "status", "TASK-EFFECT",
                    "--workspace", str(workspace), "--json",
                ])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())["pending"], [obligation])

            proof_ref = "runtime/herdr/cancel-proof.json"
            proof_path = task / proof_ref
            proof_path.parent.mkdir(parents=True, exist_ok=True)
            proof_path.write_text('{"cancelled":true}\n', encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "kernel", "effects", "record", "TASK-EFFECT",
                    "--workspace", str(workspace), "--obligation", obligation,
                    "--status", "fulfilled", "--proof-ref", proof_ref, "--json",
                ])
            self.assertEqual(code, 0)
            self.assertEqual(
                json.loads(output.getvalue())["reconciliation"]["pending"], []
            )
            proof_path.write_text('{"cancelled":false}\n', encoding="utf-8")
            with self.assertRaises(KernelStoreError) as tampered:
                store.reconcile_effects()
            self.assertEqual(tampered.exception.code, KERNEL_STORE_CORRUPT_ERROR)

    def test_kernel_effect_execute_is_approval_gated_and_closes_real_adapter_proof(self) -> None:
        genesis, entry = self.make_cancellation_history()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            task = workspace / ".herdr-loop" / "tasks" / "TASK-EFFECT-EXECUTE"
            store = KernelStore(task / "runtime" / "kernel")
            store.initialize(genesis)
            store.append(entry)
            obligation = entry.result.accepted.obligations[0]
            identity = json.loads(obligation.removeprefix("adapter_cancel:"))
            submission_path = task / "runtime/langgraph/run-effect/submission.json"
            submission_path.parent.mkdir(parents=True)
            submission_path.write_text(json.dumps({
                **identity,
                "run_id": "run-effect",
            }), encoding="utf-8")
            calls = {"value": 0}

            def cancel(workspace_arg, task_id, run_id, *, obligation):
                calls["value"] += 1
                self.assertEqual(run_id, "run-effect")
                proof_ref = "runtime/langgraph/run-effect/cancellation-proof.json"
                proof_path = task / proof_ref
                proof_path.write_text(json.dumps({
                    "schema_version": "valp-adapter-cancellation-proof.v1",
                    "obligation": obligation,
                    "acknowledged": True,
                }) + "\n", encoding="utf-8")
                return {
                    "status": "cancelled",
                    "proof_ref": proof_ref,
                    "proof": json.loads(proof_path.read_text()),
                    "observation": {"status": "cancelled"},
                }

            with patch("valp_cli.effect_runtime.cancel_langgraph_run", side_effect=cancel):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    dry_code = main([
                        "kernel", "effects", "execute", "TASK-EFFECT-EXECUTE",
                        "--workspace", str(workspace), "--obligation", obligation,
                        "--json",
                    ])
                self.assertEqual(dry_code, 0)
                self.assertEqual(json.loads(output.getvalue())["status"], "dry_run")
                self.assertEqual(calls["value"], 0)
                self.assertEqual(store.reconcile_effects().pending, (obligation,))

                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    execute_code = main([
                        "kernel", "effects", "execute", "TASK-EFFECT-EXECUTE",
                        "--workspace", str(workspace), "--obligation", obligation,
                        "--approve", "--json",
                    ])
                executed = json.loads(output.getvalue())
                self.assertEqual(execute_code, 0)
                self.assertEqual(executed["status"], "fulfilled")
                self.assertEqual(calls["value"], 1)
                self.assertEqual(store.reconcile_effects().pending, ())

                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    retry_code = main([
                        "kernel", "effects", "execute", "TASK-EFFECT-EXECUTE",
                        "--workspace", str(workspace), "--obligation", obligation,
                        "--approve", "--json",
                    ])
                self.assertEqual(retry_code, 0)
                self.assertEqual(json.loads(output.getvalue())["variant"], "no_op")
                self.assertEqual(calls["value"], 1)

    def test_queue_kernel_effect_waits_for_exact_worker_ack_before_fulfillment(self) -> None:
        payload = b"Implement and verify.\n"
        queue_id = digest({
            "task_id": "effect-task", "work_item_id": "effect-work",
            "dispatch_id": "effect-dispatch", "dispatch_generation": 1,
        })
        transaction_id = digest({"queue_id": queue_id, "payload_digest": digest(payload)})
        attempt_digest = digest({
            'queue_id': queue_id,
            'transaction_id': transaction_id,
            'payload_digest': digest(payload),
        })
        attempt_id = f"queue:{attempt_digest}"
        genesis, entry = self.make_cancellation_history(attempt_id)
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            task = workspace / ".herdr-loop" / "tasks" / "effect-task"
            (task / "agents/codex").mkdir(parents=True)
            (task / "agents/codex/dispatch.md").write_bytes(payload)
            (workspace / ".valp").mkdir()
            (workspace / ".valp/installation.json").write_text(json.dumps({
                "schema_version": "valp-installation.v1",
                "installation_id": "queue-effect-installation",
                "active_leader_epoch": 2,
            }), encoding="utf-8")
            (workspace / ".valp/state.json").write_text(json.dumps({
                "schema_version": "valp-executable-state.v1",
                "installation_id": "queue-effect-installation",
                "active_leader_epoch": 2,
            }), encoding="utf-8")
            (task / "automation-policy.json").write_text(json.dumps({
                "schema_version": "valp-automation-policy.v1",
                "approval_required": False,
            }), encoding="utf-8")
            store = KernelStore(task / "runtime/kernel")
            store.initialize(genesis)
            store.append(entry)
            obligation = entry.result.accepted.obligations[0]
            queue, submission, _ = record_queue_acceptance(
                task, "effect-task", agent="codex", role="implementer",
                work_item_id="effect-work", dispatch_id="effect-dispatch",
                dispatch_generation=1, dispatch_ref="agents/codex/dispatch.md",
                expected_refs=["agents/codex/evidence.md"],
            )
            self.assertEqual(submission["attempt_id"], attempt_id)
            claim = record_queue_claim(
                task, "effect-task", submission,
                worker_id="worker-effect", run_id="run-effect",
                claim_token="claim-effect", expected_revision=0,
            )

            dry = execute_kernel_effect(
                workspace, "effect-task", obligation, approve=False
            )
            self.assertEqual((dry["adapter_id"], dry["queue_state"]), ("queue", "claimed"))
            pending = execute_kernel_effect(
                workspace, "effect-task", obligation, approve=True
            )
            self.assertEqual(pending["variant"], "awaiting_worker_ack")
            self.assertEqual(store.reconcile_effects().pending, (obligation,))

            record_queue_cancellation_acknowledgement(
                task, "effect-task", submission,
                worker_id="worker-effect", run_id="run-effect",
                claim_token="claim-effect", claim_event_id=claim["event_id"],
                expected_revision=2,
            )
            fulfilled = execute_kernel_effect(
                workspace, "effect-task", obligation, approve=True
            )
            self.assertEqual((fulfilled["status"], fulfilled["adapter_id"]), ("fulfilled", "queue"))
            self.assertEqual(store.reconcile_effects().pending, ())
            retry = execute_kernel_effect(
                workspace, "effect-task", obligation, approve=True
            )
            self.assertEqual(retry["variant"], "no_op")
            self.assertEqual(len([
                item for item in (task / "runtime/queue/lifecycle.v1.jsonl").read_text().splitlines()
                if json.loads(item)["event"] == "cancelled"
            ]), 1)
    def test_concurrent_kernel_effect_execution_calls_provider_once(self) -> None:
        genesis, entry = self.make_cancellation_history()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            task_id = "TASK-EFFECT-CONCURRENT"
            task = workspace / ".herdr-loop" / "tasks" / task_id
            store = KernelStore(task / "runtime" / "kernel")
            store.initialize(genesis)
            store.append(entry)
            obligation = entry.result.accepted.obligations[0]
            identity = json.loads(obligation.removeprefix("adapter_cancel:"))
            submission_path = task / "runtime/langgraph/run-concurrent/submission.json"
            submission_path.parent.mkdir(parents=True)
            submission_path.write_text(json.dumps({
                **identity,
                "run_id": "run-concurrent",
            }), encoding="utf-8")
            calls = {"value": 0}

            def cancel(workspace_arg, task_id_arg, run_id, *, obligation):
                calls["value"] += 1
                time.sleep(0.05)
                proof_ref = "runtime/langgraph/run-concurrent/cancellation-proof.json"
                proof_path = task / proof_ref
                proof_path.write_text('{"acknowledged":true}\n', encoding="utf-8")
                return {
                    "status": "cancelled",
                    "proof_ref": proof_ref,
                    "proof": {"acknowledged": True},
                    "observation": {"status": "cancelled"},
                }

            with patch("valp_cli.effect_runtime.cancel_langgraph_run", side_effect=cancel):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(
                        lambda _: execute_kernel_effect(
                            workspace, task_id, obligation, approve=True
                        ),
                        range(2),
                    ))

            self.assertEqual(calls["value"], 1)
            self.assertEqual(
                {result["variant"] for result in results}, {"accepted", "no_op"}
            )
            self.assertEqual(store.reconcile_effects().pending, ())

    def test_kernel_control_cli_requires_approval_and_exact_retry_is_noop(self) -> None:
        genesis, entries = self.make_history()
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            task_id = genesis.state.task_id.value
            task = workspace / ".herdr-loop" / "tasks" / task_id
            store = KernelStore(task / "runtime" / "kernel")
            store.initialize(genesis)
            for entry in entries:
                store.append(entry)
            state = store.recover().replay.state
            authority_id = Identity(IdentityKind.EVIDENCE, "control-authority")
            event = Event(
                Identity(IdentityKind.EVENT, "control-interrupt"),
                state.installation_id,
                state.leader_epoch,
                state.task_id,
                EventKind.INTERRUPT_REQUESTED,
                state.revision,
                authority_principal_id=Identity(IdentityKind.PRINCIPAL, "control-user"),
                authority_evidence_id=authority_id,
                control_reason=ControlReason.USER_REQUESTED,
                interrupt_id=Identity(IdentityKind.INTERRUPT, "control-interrupt-1"),
                intent_version=0,
            )
            event_ref = "control/interrupt.json"
            authority_ref = "control/authority.txt"
            event_path = task / event_ref
            authority_path = task / authority_ref
            event_path.parent.mkdir(parents=True, exist_ok=True)
            event_path.write_text(
                json.dumps(event.canonical(), sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            authority_path.write_text("approved by user\n", encoding="utf-8")
            command = [
                "kernel", "control", task_id, "--workspace", str(workspace),
                "--event-ref", event_ref, "--authority-ref", authority_ref, "--json",
            ]

            with self.assertRaises(SystemExit):
                main(command)
            self.assertEqual(store.recover().replay.state.revision, state.revision)

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(command + ["--approve"])
            self.assertEqual(code, 0)
            accepted = json.loads(output.getvalue())
            self.assertEqual(accepted["variant"], "accepted")
            self.assertEqual(accepted["state"]["control"]["status"], "interrupted")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(command + ["--approve"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["variant"], "no_op")

    def test_authenticated_checkpoint_recovers_exact_suffix_and_full_state(self) -> None:
        genesis, entries = self.make_history()
        root, authentication = self.make_checkpoint(entries)
        with tempfile.TemporaryDirectory() as temporary:
            store = KernelStore(Path(temporary) / "kernel")
            store.initialize(genesis)
            for entry in entries:
                store.append(entry)
            store.persist_checkpoint(root, authentication)

            recovered = KernelStore(store.root).recover()

            self.assertTrue(recovered.used_checkpoint)
            self.assertEqual(recovered.replay.state, entries[-1].result.accepted.state)
            self.assertEqual(
                recovered.replay.applied_result_digests,
                (entries[-1].result.accepted.result_digest,),
            )
            self.assertEqual(recovered.replay.obligations, ())
            kernel_schema = json.loads(
                (Path(__file__).parents[1] / "schemas" / "protocol-kernel.schema.json").read_text()
            )
            checkpoint_schema = json.loads(
                (Path(__file__).parents[1] / "schemas" / "kernel-store.schema.json").read_text()
            )
            registry = Registry().with_resource(
                kernel_schema["$id"], Resource.from_contents(kernel_schema)
            )
            Draft202012Validator(checkpoint_schema, registry=registry).validate(
                json.loads(store.checkpoint_path.read_text())
            )

    def test_exact_append_retry_is_noop_but_changed_event_identity_conflicts(self) -> None:
        genesis, entries = self.make_history()
        with tempfile.TemporaryDirectory() as temporary:
            store = KernelStore(Path(temporary) / "kernel")
            store.initialize(genesis)
            store.append(entries[0])
            before = store.path.read_bytes()

            retry = store.append(entries[0])
            self.assertEqual(retry.entries, (entries[0],))
            self.assertEqual(store.path.read_bytes(), before)

            changed_event = replace(entries[0].event, expected_revision=1)
            changed = ReplayEntry(changed_event, (), entries[0].result)
            with self.assertRaises(KernelStoreError) as raised:
                store.append(changed)
            self.assertEqual(raised.exception.code, IDEMPOTENCY_CONFLICT_ERROR)
            self.assertEqual(store.path.read_bytes(), before)

    def test_stale_or_tampered_entry_and_checkpoint_fail_without_mutation(self) -> None:
        genesis, entries = self.make_history()
        root, authentication = self.make_checkpoint(entries)
        with tempfile.TemporaryDirectory() as temporary:
            store = KernelStore(Path(temporary) / "kernel")
            store.initialize(genesis)
            store.append(entries[0])
            before = store.path.read_bytes()

            with self.assertRaises(KernelStoreError):
                store.append(entries[2])
            self.assertEqual(store.path.read_bytes(), before)

            with self.assertRaises(KernelStoreError) as raised:
                store.persist_checkpoint(root, authentication)
            self.assertEqual(raised.exception.code, KERNEL_STORE_CORRUPT_ERROR)
            self.assertFalse(store.checkpoint_path.exists())

    def test_noncanonical_or_tampered_journal_fails_closed(self) -> None:
        genesis, entries = self.make_history()
        with tempfile.TemporaryDirectory() as temporary:
            store = KernelStore(Path(temporary) / "kernel")
            store.initialize(genesis)
            store.append(entries[0])
            parsed = json.loads(store.path.read_text())
            parsed["result"]["accepted_events"] = []
            store.path.write_text(json.dumps(parsed) + "\n")
            before = store.path.read_bytes()

            with self.assertRaises(KernelStoreError) as raised:
                store.recover()

            self.assertEqual(raised.exception.code, KERNEL_STORE_CORRUPT_ERROR)
            self.assertEqual(store.path.read_bytes(), before)

    def test_precommit_failure_preserves_prior_journal_bytes(self) -> None:
        genesis, entries = self.make_history()
        for target in ("os.fsync", "_replace_file"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary:
                store = KernelStore(Path(temporary) / "kernel")
                store.initialize(genesis)
                store.append(entries[0])
                before = store.path.read_bytes()
                with patch(
                    f"valp_cli.kernel_store.{target}",
                    side_effect=OSError(errno.EIO, "injected precommit failure"),
                ):
                    with self.assertRaises(KernelStoreError) as raised:
                        store.append(entries[1])
                self.assertEqual(raised.exception.code, DURABILITY_PRECOMMIT_ERROR)
                self.assertEqual(store.path.read_bytes(), before)

    def test_post_replace_uncertainty_reconciles_by_strict_reread(self) -> None:
        genesis, entries = self.make_history()
        with tempfile.TemporaryDirectory() as temporary:
            store = KernelStore(Path(temporary) / "kernel")
            store.initialize(genesis)
            with patch(
                "valp_cli.kernel_store._sync_directory",
                side_effect=OSError(errno.EIO, "injected directory sync failure"),
            ):
                with self.assertRaises(KernelStoreError) as raised:
                    store.append(entries[0])

            self.assertEqual(raised.exception.code, DURABILITY_UNKNOWN_ERROR)
            reconciled = KernelStore(store.root).append(entries[0])
            self.assertEqual(reconciled.entries, (entries[0],))
            self.assertEqual(reconciled.replay.state, entries[0].result.accepted.state)


if __name__ == "__main__":
    unittest.main()
