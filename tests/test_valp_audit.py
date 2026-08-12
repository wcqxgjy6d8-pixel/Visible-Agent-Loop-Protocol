from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from jsonschema import Draft202012Validator

from tests.schema_helpers import schema_validator

from valp_cli.audit import FAIL, PASS, SKIP, WARN, TaskAudit
from valp_cli.submission import build_submission_dependencies
from valp_cli.workflow import (
    resume_suspended_task,
    suspend_task,
    write_herdr_invalid_session_binding_supersession,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "full-mode-task"
QUEUE_EXAMPLE = ROOT / "examples" / "headless-queue-task"
REAL_DOC_EXAMPLE = ROOT / "examples" / "real-doc-calibration-task"


class ValpAuditTests(unittest.TestCase):
    def _owned_session_supersession_fixture(self, task: Path) -> tuple[dict, dict, dict]:
        task_id = "TASK-OWNED-SESSION-SUPERSESSION"
        marker = {
            "status": "ready",
            "ref": "agent-sessions.json",
            "receipts_ref": "agent-session-receipts.jsonl",
        }
        projection = json.loads(
            (ROOT / "examples" / "agent-sessions.json").read_text(encoding="utf-8")
        )
        projection["task_id"] = task_id
        binding = projection["bindings"]["example-agent"]
        binding["ownership"]["task_id"] = task_id
        (task / "agent-sessions.json").write_text(json.dumps(projection), encoding="utf-8")
        session_receipt = json.loads(
            (ROOT / "examples" / "agent-session-receipts.jsonl").read_text(encoding="utf-8")
        )
        session_receipt["task_id"] = task_id
        session_receipt["ownership"]["task_id"] = task_id
        (task / "agent-session-receipts.jsonl").write_text(
            json.dumps(session_receipt) + "\n", encoding="utf-8"
        )
        runtime = {"id": "herdr", "class": "pane_controller", "name": "HERDR"}
        for name in ("routing.json", "state.json"):
            (task / name).write_text(json.dumps({
                "task_id": task_id,
                "runtime_adapter": runtime,
                "agent_sessions": marker,
            }), encoding="utf-8")
        identity = {
            "task_id": task_id,
            "agent": "example-agent",
            "role": "reviewer",
            "work_item_id": "reviewer:example-agent",
            "dispatch_id": f"{task_id}:reviewer:1",
            "dispatch_generation": 1,
            "dispatch_ref": "agents/example-agent/dispatch.md",
            "expected_refs": ["agents/example-agent/review.md"],
        }
        original = {
            "schema_version": "valp-dispatch-receipt.v2",
            "receipt_id": "submission-invalid",
            "event_sequence": 1,
            "ts": "2026-08-12T00:00:00Z",
            "event": "dispatch_submitted",
            **identity,
            "proof": {
                "runtime": "HERDR",
                "proof_class": "agent_invocation",
                "submission_id": "invalid",
                "session_binding": {
                    "ref": "agent-sessions.json",
                    "generation": 1,
                    "identity_token": "sha256:" + "0" * 64,
                    "ownership": binding["ownership"],
                },
            },
        }
        replacement = json.loads(json.dumps(original))
        replacement.update({
            "receipt_id": "submission-valid",
            "event_sequence": 2,
            "retry_generation": 1,
        })
        replacement["proof"]["submission_id"] = "valid"
        replacement["proof"]["session_binding"]["identity_token"] = binding["runtime_identity"]["token"]
        (task / "dispatch-receipts.jsonl").write_text(
            json.dumps(original) + "\n" + json.dumps(replacement) + "\n",
            encoding="utf-8",
        )
        return original, replacement, binding

    def test_owned_session_audit_accepts_append_only_invalid_binding_supersession(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            original, replacement, _binding = self._owned_session_supersession_fixture(task)
            supersession = write_herdr_invalid_session_binding_supersession(
                task,
                original["task_id"],
                original["receipt_id"],
                replacement["receipt_id"],
            )
            self.assertEqual(supersession["event"], "dispatch_superseded")
            self.assertEqual(TaskAudit(task).check_agent_sessions().status, PASS)

    def test_owned_session_supersession_rejects_valid_original_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            original, replacement, binding = self._owned_session_supersession_fixture(task)
            original["proof"]["session_binding"]["identity_token"] = binding["runtime_identity"]["token"]
            (task / "dispatch-receipts.jsonl").write_text(
                json.dumps(original) + "\n" + json.dumps(replacement) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "invalid original"):
                write_herdr_invalid_session_binding_supersession(
                    task,
                    original["task_id"],
                    original["receipt_id"],
                    replacement["receipt_id"],
                )

    def test_owned_session_supersession_rejects_original_without_runtime_submission_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            original, replacement, _binding = self._owned_session_supersession_fixture(task)
            original["proof"] = {
                "runtime": "HERDR",
                "session_binding": original["proof"]["session_binding"],
            }
            (task / "dispatch-receipts.jsonl").write_text(
                json.dumps(original) + "\n" + json.dumps(replacement) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "concrete submission proof"):
                write_herdr_invalid_session_binding_supersession(
                    task,
                    original["task_id"],
                    original["receipt_id"],
                    replacement["receipt_id"],
                )

    def test_owned_session_supersession_rejects_completion_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            original, replacement, _binding = self._owned_session_supersession_fixture(task)
            completion = {
                **replacement,
                "receipt_id": "completion-valid",
                "event_sequence": 3,
                "event": "dispatch_completed",
                "suspension_epoch": 1,
            }
            (task / "dispatch-receipts.jsonl").write_text(
                "\n".join(json.dumps(item) for item in (original, replacement, completion)) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "exact existing submission receipts"):
                write_herdr_invalid_session_binding_supersession(
                    task,
                    original["task_id"],
                    completion["receipt_id"],
                    replacement["receipt_id"],
                )

    def test_owned_session_supersession_rejects_identity_mismatch(self) -> None:
        identity_changes = {
            "task_id": "ANOTHER-TASK",
            "agent": "another-agent",
            "role": "implementer",
            "work_item_id": "reviewer:another-agent",
            "dispatch_id": "another-dispatch",
            "dispatch_generation": 2,
            "dispatch_ref": "agents/example-agent/other.md",
            "expected_refs": ["agents/example-agent/other-review.md"],
        }
        for field, changed_value in identity_changes.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                task = Path(tmp) / "task"
                task.mkdir()
                original, replacement, _binding = self._owned_session_supersession_fixture(task)
                replacement[field] = changed_value
                (task / "dispatch-receipts.jsonl").write_text(
                    json.dumps(original) + "\n" + json.dumps(replacement) + "\n",
                    encoding="utf-8",
                )

                expected_error = (
                    "belongs to a different task"
                    if field == "task_id"
                    else "exact work-item identity"
                )
                with self.assertRaisesRegex(SystemExit, expected_error):
                    write_herdr_invalid_session_binding_supersession(
                        task,
                        original["task_id"],
                        original["receipt_id"],
                        replacement["receipt_id"],
                    )

    def test_owned_session_supersession_rejects_reversed_submission_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            original, replacement, _binding = self._owned_session_supersession_fixture(task)
            original["event_sequence"] = 2
            replacement["event_sequence"] = 1
            (task / "dispatch-receipts.jsonl").write_text(
                json.dumps(replacement) + "\n" + json.dumps(original) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "exact work-item identity"):
                write_herdr_invalid_session_binding_supersession(
                    task,
                    original["task_id"],
                    original["receipt_id"],
                    replacement["receipt_id"],
                )

    def test_owned_session_audit_rejects_supersession_before_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            original, replacement, _binding = self._owned_session_supersession_fixture(task)
            supersession = write_herdr_invalid_session_binding_supersession(
                task,
                original["task_id"],
                original["receipt_id"],
                replacement["receipt_id"],
            )
            replacement["event_sequence"] = supersession["event_sequence"] + 1
            (task / "dispatch-receipts.jsonl").write_text(
                "\n".join(json.dumps(item) for item in (original, supersession, replacement)) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(TaskAudit(task).check_agent_sessions().status, FAIL)

    def test_owned_session_audit_rejects_malformed_supersession_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            original, replacement, _binding = self._owned_session_supersession_fixture(task)
            supersession = {
                **original,
                "receipt_id": "malformed-supersession",
                "event_sequence": 3,
                "event": "dispatch_superseded",
                "proof": {"kind": "invalid_session_binding"},
            }
            (task / "dispatch-receipts.jsonl").write_text(
                "\n".join(json.dumps(item) for item in (original, replacement, supersession)) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(TaskAudit(task).check_agent_sessions().status, FAIL)

    def test_owned_session_audit_rejects_supersession_of_non_runtime_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            original, replacement, _binding = self._owned_session_supersession_fixture(task)
            original["proof"] = {
                "runtime": "HERDR",
                "session_binding": original["proof"]["session_binding"],
            }
            supersession = {
                "schema_version": "valp-dispatch-receipt.v2",
                "receipt_id": "supersession-for-non-runtime-submission",
                "event_sequence": 3,
                "ts": "2026-08-12T00:00:02Z",
                "event": "dispatch_superseded",
                **{
                    field: original[field]
                    for field in (
                        "task_id", "agent", "role", "work_item_id", "dispatch_id",
                        "dispatch_generation", "dispatch_ref", "expected_refs",
                    )
                },
                "proof": {
                    "kind": "invalid_session_binding",
                    "superseded_submission_receipt_id": original["receipt_id"],
                    "replacement_submission_receipt_id": replacement["receipt_id"],
                },
            }
            (task / "dispatch-receipts.jsonl").write_text(
                "\n".join(json.dumps(item) for item in (original, replacement, supersession)) + "\n",
                encoding="utf-8",
            )

            self.assertEqual(TaskAudit(task).check_agent_sessions().status, FAIL)

    def test_supersession_receipt_schema_requires_exact_proof_shape(self) -> None:
        validator = schema_validator(ROOT / "schemas" / "receipts.schema.json")
        receipt = {
            "schema_version": "valp-dispatch-receipt.v2",
            "receipt_id": "supersession-schema-test",
            "task_id": "TASK-SUPERSESSION-SCHEMA",
            "event_sequence": 3,
            "ts": "2026-08-12T00:00:02Z",
            "agent": "example-agent",
            "role": "reviewer",
            "work_item_id": "reviewer:example-agent",
            "dispatch_id": "TASK-SUPERSESSION-SCHEMA:reviewer:1",
            "dispatch_generation": 1,
            "event": "dispatch_superseded",
            "dispatch_ref": "agents/example-agent/dispatch.md",
            "expected_refs": ["agents/example-agent/review.md"],
            "proof": {"kind": "invalid_session_binding"},
        }

        self.assertTrue(list(validator.iter_errors(receipt)))

    def test_owned_agent_session_audit_binds_submission_to_provisioning_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            task_id = "TASK-OWNED-SESSION-AUDIT"
            marker = {
                "status": "ready",
                "ref": "agent-sessions.json",
                "receipts_ref": "agent-session-receipts.jsonl",
            }
            binding = json.loads(
                (ROOT / "examples" / "agent-sessions.json").read_text(encoding="utf-8")
            )
            binding["task_id"] = task_id
            binding["bindings"]["example-agent"]["ownership"]["task_id"] = task_id
            (task / "agent-sessions.json").write_text(json.dumps(binding), encoding="utf-8")
            session_receipt = json.loads(
                (ROOT / "examples" / "agent-session-receipts.jsonl").read_text(encoding="utf-8")
            )
            session_receipt["task_id"] = task_id
            session_receipt["ownership"]["task_id"] = task_id
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(session_receipt) + "\n",
                encoding="utf-8",
            )
            (task / "routing.json").write_text(
                json.dumps({
                    "task_id": task_id,
                    "runtime_adapter": {
                        "id": "herdr",
                        "class": "pane_controller",
                        "name": "HERDR",
                    },
                    "agent_sessions": marker,
                }),
                encoding="utf-8",
            )
            (task / "state.json").write_text(
                json.dumps({
                    "task_id": task_id,
                    "runtime_adapter": {
                        "id": "herdr",
                        "class": "pane_controller",
                        "name": "HERDR",
                    },
                    "agent_sessions": marker,
                }),
                encoding="utf-8",
            )
            owned = binding["bindings"]["example-agent"]
            submission = {
                "agent": "example-agent",
                "event": "dispatch_submitted",
                "proof": {
                    "runtime": "HERDR",
                    "session_binding": {
                        "ref": "agent-sessions.json",
                        "generation": owned["generation"],
                        "identity_token": "sha256:" + ("f" * 64),
                        "ownership": owned["ownership"],
                    },
                },
            }
            (task / "dispatch-receipts.jsonl").write_text(
                json.dumps(submission) + "\n",
                encoding="utf-8",
            )

            failed = TaskAudit(task).check_agent_sessions()
            self.assertEqual(failed.status, FAIL)
            self.assertIn("not bound", failed.message)

            submission["proof"]["session_binding"]["identity_token"] = owned["runtime_identity"]["token"]
            session_receipt["event"] = "agent_session_reused"
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(session_receipt) + "\n",
                encoding="utf-8",
            )
            failed = TaskAudit(task).check_agent_sessions()
            self.assertEqual(failed.status, FAIL)
            self.assertIn("generations are incomplete", failed.message)

            session_receipt["event"] = "agent_session_provisioned"
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(session_receipt) + "\n",
                encoding="utf-8",
            )
            (task / "dispatch-receipts.jsonl").write_text(
                json.dumps(submission) + "\n",
                encoding="utf-8",
            )
            passed = TaskAudit(task).check_agent_sessions()
            self.assertEqual(passed.status, PASS)

            binding["bindings"]["example-agent"]["focused_at_provisioning"] = True
            session_receipt["focused_at_provisioning"] = True
            (task / "agent-sessions.json").write_text(
                json.dumps(binding),
                encoding="utf-8",
            )
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(session_receipt) + "\n",
                encoding="utf-8",
            )
            failed = TaskAudit(task).check_agent_sessions()
            self.assertEqual(failed.status, FAIL)
            self.assertIn("non-focused provisioning", failed.message)

            binding["bindings"]["example-agent"]["focused_at_provisioning"] = False
            session_receipt["focused_at_provisioning"] = False
            binding["bindings"]["example-agent"]["launch"]["argv"][0] = "example-agent"
            session_receipt["launch"]["argv"][0] = "example-agent"
            (task / "agent-sessions.json").write_text(
                json.dumps(binding),
                encoding="utf-8",
            )
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(session_receipt) + "\n",
                encoding="utf-8",
            )
            failed = TaskAudit(task).check_agent_sessions()
            self.assertEqual(failed.status, FAIL)
            self.assertIn("absolute launch executable", failed.message)

    def test_owned_agent_session_audit_accepts_verified_bootstrap_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            task_id = "TASK-BOOTSTRAP-SESSION-AUDIT"
            marker = {
                "status": "ready",
                "ref": "agent-sessions.json",
                "receipts_ref": "agent-session-receipts.jsonl",
            }
            projection = json.loads(
                (ROOT / "examples" / "agent-sessions.json").read_text(encoding="utf-8")
            )
            binding = projection["bindings"]["example-agent"]
            projection["task_id"] = task_id
            binding["ownership"]["task_id"] = task_id
            binding["lifecycle"] = "bootstrap_ready"
            binding["bootstrap_verification"] = {
                "status": "verified",
                "evidence_ref": "evidence/bootstrap-probe-result.json",
                "generation": 1,
                "pane_id": binding["runtime_identity"]["pane_id"],
                "native_session_id": "session-native",
                "expected_response": "BOOTSTRAP_READY",
                "actual_response": "BOOTSTRAP_READY",
                "native_turn_error": None,
                "session_identity_status": "known",
                "model_probe_status": "observed",
            }
            provisioning = json.loads(
                (ROOT / "examples" / "agent-session-receipts.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            provisioning["task_id"] = task_id
            provisioning["ownership"]["task_id"] = task_id
            bootstrap = {
                "schema_version": "valp-agent-session-receipt.v1",
                "adapter": "herdr",
                "task_id": task_id,
                "event_sequence": 2,
                "ts": "2026-08-07T09:15:59Z",
                "agent": "example-agent",
                "event": "agent_session_bootstrap_verified",
                "binding_ref": "agent-sessions.json",
                "generation": 1,
                "identity_token": binding["runtime_identity"]["token"],
                "evidence_ref": "evidence/bootstrap-probe-result.json",
                "native_session_id": "session-native",
            }
            submission = {
                "agent": "example-agent",
                "event": "dispatch_submitted",
                "proof": {
                    "runtime": "HERDR",
                    "session_binding": {
                        "ref": "agent-sessions.json",
                        "generation": 1,
                        "identity_token": binding["runtime_identity"]["token"],
                        "ownership": binding["ownership"],
                    },
                },
            }
            runtime = {"id": "herdr", "class": "pane_controller", "name": "HERDR"}
            (task / "agent-sessions.json").write_text(json.dumps(projection), encoding="utf-8")
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(provisioning) + "\n" + json.dumps(bootstrap) + "\n",
                encoding="utf-8",
            )
            (task / "dispatch-receipts.jsonl").write_text(
                json.dumps(submission) + "\n", encoding="utf-8"
            )
            for name in ("routing.json", "state.json"):
                (task / name).write_text(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "runtime_adapter": runtime,
                            "agent_sessions": marker,
                        }
                    ),
                    encoding="utf-8",
                )

            item = TaskAudit(task).check_agent_sessions()

            self.assertEqual(item.status, PASS, item.message)

            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(provisioning) + "\n",
                encoding="utf-8",
            )
            missing_bootstrap_receipt = TaskAudit(task).check_agent_sessions()

            self.assertEqual(missing_bootstrap_receipt.status, FAIL)
            self.assertIn(
                "verified bootstrap lifecycle has no matching receipt",
                missing_bootstrap_receipt.message,
            )

            mismatched_bootstrap = {**bootstrap, "evidence_ref": "evidence/other.json"}
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(provisioning)
                + "\n"
                + json.dumps(mismatched_bootstrap)
                + "\n",
                encoding="utf-8",
            )
            mismatched_bootstrap_receipt = TaskAudit(task).check_agent_sessions()

            self.assertEqual(mismatched_bootstrap_receipt.status, FAIL)
            self.assertIn(
                "verified bootstrap lifecycle has no matching receipt",
                mismatched_bootstrap_receipt.message,
            )

    def test_owned_session_audit_binds_historical_submission_to_its_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            task_id = "TASK-OWNED-SESSION-GENERATIONS"
            marker = {
                "status": "ready",
                "ref": "agent-sessions.json",
                "receipts_ref": "agent-session-receipts.jsonl",
            }
            projection = json.loads(
                (ROOT / "examples" / "agent-sessions.json").read_text(encoding="utf-8")
            )
            projection["task_id"] = task_id
            generation_one = projection["bindings"]["example-agent"]
            generation_one["ownership"]["task_id"] = task_id
            receipt_one = json.loads(
                (ROOT / "examples" / "agent-session-receipts.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            receipt_one["task_id"] = task_id
            receipt_one["ownership"]["task_id"] = task_id

            generation_two = json.loads(json.dumps(generation_one))
            generation_two["generation"] = 2
            generation_two["runtime_scope"]["workspace_id"] = "workspace-owned-2"
            generation_two["runtime_scope"]["label"] = "valp-task-001-example-agent-g2"
            identity_fields = {
                "pane_id": "pane-owned-2",
                "terminal_id": "terminal-owned-2",
                "workspace_id": "workspace-owned-2",
                "tab_id": "tab-owned-2",
            }
            generation_two["runtime_identity"] = {
                **identity_fields,
                "token": "sha256:" + hashlib.sha256(
                    json.dumps(
                        identity_fields,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
            }
            projection["bindings"]["example-agent"] = generation_two
            receipt_two = json.loads(json.dumps(receipt_one))
            receipt_two.update({
                "event_sequence": 2,
                "generation": 2,
                "identity_token": generation_two["runtime_identity"]["token"],
                "ownership": generation_two["ownership"],
                "context": generation_two["context"],
                "launch": generation_two["launch"],
                "runtime_scope": generation_two["runtime_scope"],
                "runtime_identity": generation_two["runtime_identity"],
            })
            submission = {
                "agent": "example-agent",
                "event": "dispatch_submitted",
                "proof": {
                    "runtime": "HERDR",
                    "session_binding": {
                        "ref": "agent-sessions.json",
                        "generation": 1,
                        "identity_token": receipt_one["identity_token"],
                        "ownership": receipt_one["ownership"],
                    },
                },
            }
            runtime = {"id": "herdr", "class": "pane_controller", "name": "HERDR"}
            (task / "agent-sessions.json").write_text(json.dumps(projection), encoding="utf-8")
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(receipt_one) + "\n" + json.dumps(receipt_two) + "\n",
                encoding="utf-8",
            )
            (task / "dispatch-receipts.jsonl").write_text(
                json.dumps(submission) + "\n",
                encoding="utf-8",
            )
            (task / "routing.json").write_text(
                json.dumps({
                    "task_id": task_id,
                    "runtime_adapter": runtime,
                    "agent_sessions": marker,
                }),
                encoding="utf-8",
            )
            (task / "state.json").write_text(
                json.dumps({
                    "schema_version": "valp-visible-loop-state.v2",
                    "task_id": task_id,
                    "runtime_adapter": runtime,
                    "agent_sessions": marker,
                }),
                encoding="utf-8",
            )

            item = TaskAudit(task).check_agent_sessions()
            self.assertEqual(item.status, PASS, item.message)

    def test_state_v2_pane_controller_cannot_drop_owned_session_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            runtime = {"id": "herdr", "class": "pane_controller", "name": "HERDR"}
            (task / "routing.json").write_text(
                json.dumps({"task_id": "TASK-MARKER", "runtime_adapter": runtime}),
                encoding="utf-8",
            )
            (task / "state.json").write_text(
                json.dumps({
                    "schema_version": "valp-visible-loop-state.v2",
                    "task_id": "TASK-MARKER",
                    "runtime_adapter": runtime,
                }),
                encoding="utf-8",
            )

            item = TaskAudit(task).check_agent_sessions()
            self.assertEqual(item.status, FAIL)
            self.assertIn("missing required owned-session markers", item.message)

    def test_owned_agent_session_audit_accepts_adapter_neutral_thread_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            task_id = "TASK-THREAD-SESSION-AUDIT"
            marker = {
                "status": "ready",
                "ref": "agent-sessions.json",
                "receipts_ref": "agent-session-receipts.jsonl",
            }
            runtime_identity_fields = {
                "thread_id": "thread-1",
                "worker_id": "worker-1",
            }
            identity_token = "sha256:" + hashlib.sha256(
                json.dumps(
                    runtime_identity_fields,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            ownership = {
                "scope": "task",
                "task_id": task_id,
                "project_identity": "sha256:" + ("0" * 64),
            }
            context = {"project_ref": "project://example"}
            launch = {"runtime_ref": "agent://example-agent"}
            runtime_scope = {
                "kind": "thread",
                "ownership": "task",
                "thread_id": "thread-1",
            }
            runtime_identity = {
                **runtime_identity_fields,
                "token": identity_token,
            }
            binding = {
                "agent": "example-agent",
                "session_name": "thread-session-1",
                "generation": 1,
                "ownership": ownership,
                "context": context,
                "launch": launch,
                "runtime_scope": runtime_scope,
                "runtime_identity": runtime_identity,
                "lifecycle": "provisioned",
                "dispatch_eligible": True,
            }
            projection = {
                "schema_version": "valp-agent-sessions.v1",
                "task_id": task_id,
                "adapter": "example-thread-runtime",
                "status": "ready",
                "bindings": {"example-agent": binding},
                "updated_at": "2026-07-26T12:00:00Z",
            }
            session_receipt = {
                "schema_version": "valp-agent-session-receipt.v1",
                "adapter": "example-thread-runtime",
                "task_id": task_id,
                "event_sequence": 1,
                "ts": "2026-07-26T12:00:00Z",
                "agent": "example-agent",
                "event": "agent_session_provisioned",
                "binding_ref": "agent-sessions.json",
                "generation": 1,
                "identity_token": identity_token,
                "ownership": ownership,
                "context": context,
                "launch": launch,
                "runtime_scope": runtime_scope,
                "runtime_identity": runtime_identity,
            }
            submission = {
                "agent": "example-agent",
                "event": "dispatch_submitted",
                "proof": {
                    "runtime": "example-thread-runtime",
                    "session_binding": {
                        "ref": "agent-sessions.json",
                        "generation": 1,
                        "identity_token": identity_token,
                        "ownership": ownership,
                    },
                },
            }
            (task / "agent-sessions.json").write_text(
                json.dumps(projection),
                encoding="utf-8",
            )
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(session_receipt) + "\n",
                encoding="utf-8",
            )
            (task / "dispatch-receipts.jsonl").write_text(
                json.dumps(submission) + "\n",
                encoding="utf-8",
            )
            (task / "routing.json").write_text(
                json.dumps({
                    "task_id": task_id,
                    "runtime_adapter": {
                        "id": "example-thread-runtime",
                        "class": "hosted_local_platform",
                        "name": "Example Thread Runtime",
                    },
                    "agent_sessions": marker,
                }),
                encoding="utf-8",
            )
            (task / "state.json").write_text(
                json.dumps({
                    "task_id": task_id,
                    "runtime_adapter": {
                        "id": "example-thread-runtime",
                        "class": "hosted_local_platform",
                        "name": "Example Thread Runtime",
                    },
                    "agent_sessions": marker,
                }),
                encoding="utf-8",
            )

            passed = TaskAudit(task).check_agent_sessions()
            self.assertEqual(passed.status, PASS)

            projection["adapter"] = "another-adapter"
            session_receipt["adapter"] = "another-adapter"
            (task / "agent-sessions.json").write_text(
                json.dumps(projection),
                encoding="utf-8",
            )
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(session_receipt) + "\n",
                encoding="utf-8",
            )
            failed = TaskAudit(task).check_agent_sessions()
            self.assertEqual(failed.status, FAIL)
            self.assertIn("routed runtime adapter", failed.message)

            projection["adapter"] = "example-thread-runtime"
            session_receipt["adapter"] = "example-thread-runtime"
            (task / "agent-sessions.json").write_text(
                json.dumps(projection),
                encoding="utf-8",
            )

            session_receipt["adapter"] = "another-adapter"
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(session_receipt) + "\n",
                encoding="utf-8",
            )
            failed = TaskAudit(task).check_agent_sessions()
            self.assertEqual(failed.status, FAIL)
            self.assertIn("adapter", failed.message.lower())

            projection["bindings"] = {}
            (task / "agent-sessions.json").write_text(
                json.dumps(projection),
                encoding="utf-8",
            )
            (task / "agent-session-receipts.jsonl").write_text("", encoding="utf-8")
            failed = TaskAudit(task).check_agent_sessions()
            self.assertEqual(failed.status, FAIL)
            self.assertIn("no Agent session bindings", failed.message)

    def test_v2_state_audit_rejects_unknown_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / ".herdr-loop" / "tasks" / "TASK-STATE-STATUS"
            task.mkdir(parents=True)
            (task / "state.json").write_text(
                json.dumps({
                    "schema_version": "valp-visible-loop-state.v2",
                    "task_id": "TASK-STATE-STATUS",
                    "profile": "agent-runtime",
                    "status": "invented_state",
                    "revision": 0,
                    "selected_agents": [],
                }),
                encoding="utf-8",
            )
            item = TaskAudit(task).check_state_status_vocabulary()
            self.assertEqual(item.status, FAIL)
            self.assertIn("Unknown state-v2 status", item.message)

    def test_full_mode_example_passes(self) -> None:
        report = TaskAudit(EXAMPLE).run()
        self.assertEqual(report.status, PASS)
        self.assertEqual(report.fail_count, 0)

    def test_assignment_authority_passes_for_user_selected_leader_declaration(self) -> None:
        item = TaskAudit(EXAMPLE).check_assignment_authority()

        self.assertEqual(item.status, PASS)
        self.assertIn("user-selected Leader leader-surface", item.message)
        state = json.loads((EXAMPLE / "state.json").read_text(encoding="utf-8"))
        self.assertNotIn("leader-surface", state["selected_agents"])

    def test_assignment_authority_rejects_valp_authored_or_drifted_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            declaration_path = task / "assignment-declaration.json"
            declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
            declaration["leader"]["selected_by"] = "valp"
            declaration["assignments"]["reviewer"] = "codex"
            declaration_path.write_text(json.dumps(declaration), encoding="utf-8")

            item = TaskAudit(task).check_assignment_authority()

            self.assertEqual(item.status, FAIL)
            self.assertIn("user-selection evidence", item.message)
            self.assertIn("differ", item.message)

    def test_assignment_authority_rejects_missing_validation_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "assignment-validation.json").unlink()

            item = TaskAudit(task).check_assignment_authority()

            self.assertEqual(item.status, FAIL)
            self.assertIn("assignment-validation.json", item.message)

    def test_assignment_authority_skips_legacy_task_without_new_contract_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            (task / "state.json").write_text(
                json.dumps({
                    "schema_version": "valp-visible-loop-state.v1",
                    "task_id": "LEGACY-TASK",
                    "profile": "generic-analysis",
                    "status": "done",
                    "selected_agents": ["manual-operator"],
                }),
                encoding="utf-8",
            )
            (task / "routing.json").write_text(
                json.dumps({
                    "schema_version": "legacy-routing.v0",
                    "task_id": "LEGACY-TASK",
                    "profile": "generic-analysis",
                    "selected_agents": ["manual-operator"],
                }),
                encoding="utf-8",
            )

            item = TaskAudit(task).check_assignment_authority()

            self.assertEqual(item.status, SKIP)
            self.assertIn("Legacy task", item.message)

    def test_headless_queue_preflight_without_terminal_size_passes_audit(self) -> None:
        report = TaskAudit(QUEUE_EXAMPLE).run()
        self.assertEqual(report.status, PASS)
        self.assertEqual(report.fail_count, 0)
        preflight_text = (QUEUE_EXAMPLE / "runtime-preflight.json").read_text(encoding="utf-8")
        self.assertNotIn("terminal_size_status", preflight_text)
        self.assertNotIn("pane_id", preflight_text)

    def test_suspended_task_cannot_audit_as_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            state["status"] = "suspended"
            state["suspension"] = {
                "status": "waiting",
                "entered_at": "2026-07-11T00:00:00Z",
                "deadline_at": "2026-07-11T00:05:00Z",
                "waiting_for_agents": ["codex"],
                "receipt_count_at_entry": 2,
                "allowed_resume_events": [
                    "receipt",
                    "timeout",
                    "runtime_failure",
                    "cancellation",
                    "user_input",
                ],
            }
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")

            report = TaskAudit(task).run()

            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "runtime_adapter" and item.status == FAIL for item in report.items))

    def test_deterministic_wake_audit_fails_projection_without_committed_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            task_id = "TASK-WAKE-AUDIT-MISSING-EVENT"
            dependencies = build_submission_dependencies(task_id, {"implementer": "codex"})
            work_item = dependencies["work_items"][0]
            (task / "state.json").write_text(json.dumps({
                "schema_version": "valp-visible-loop-state.v2",
                "task_id": task_id,
                "profile": "agent-runtime",
                "status": "suspended",
                "revision": 1,
                "selected_agents": ["codex"],
                "role_assignments": {"implementer": "codex"},
                "suspension": {
                    "status": "waiting",
                    "suspension_id": "sha256:" + ("a" * 64),
                    "suspension_epoch": 1,
                },
            }), encoding="utf-8")
            (task / "wait-policy.json").write_text(json.dumps({
                "schema_version": "valp-wait-policy.v1",
                "task_id": task_id,
                "wait_policy_id": "next-step",
                "mode": "dependency_ready",
                "exception_policy": "exception_short_circuit",
                "dependency_ref": "submission-dependencies.json",
                "required_work_items": [work_item],
                "exception_events": [
                    "dispatch_blocked",
                    "runtime_failure",
                    "cancellation",
                    "timeout",
                    "user_input",
                ],
            }), encoding="utf-8")
            (task / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )

            item = TaskAudit(task).check_deterministic_wake()

            self.assertEqual(item.status, FAIL)
            self.assertIn("wait-events.jsonl", item.message)

    def test_deterministic_wake_audit_skips_authored_but_unused_wait_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            task_id = "TASK-WAKE-AUDIT-UNUSED-POLICY"
            dependencies = build_submission_dependencies(task_id, {"implementer": "codex"})
            (task / "state.json").write_text(json.dumps({
                "schema_version": "valp-visible-loop-state.v2",
                "task_id": task_id,
                "profile": "agent-runtime",
                "status": "executing",
                "revision": 0,
                "selected_agents": ["codex"],
                "role_assignments": {"implementer": "codex"},
            }), encoding="utf-8")
            (task / "wait-policy.json").write_text(json.dumps({
                "schema_version": "valp-wait-policy.v1",
                "task_id": task_id,
                "wait_policy_id": "next-step",
                "mode": "dependency_ready",
                "exception_policy": "exception_short_circuit",
                "dependency_ref": "submission-dependencies.json",
                "required_work_items": dependencies["work_items"],
                "exception_events": [
                    "dispatch_blocked",
                    "runtime_failure",
                    "cancellation",
                    "timeout",
                    "user_input",
                ],
            }), encoding="utf-8")

            item = TaskAudit(task).check_deterministic_wake()

            self.assertEqual(item.status, SKIP)
            self.assertIn("wait-policy.json", item.evidence)

    def test_manual_mode_wait_reaches_the_explicit_degraded_audit_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-MANUAL-DEGRADED-WAIT"
            task = root / ".herdr-loop" / "tasks" / task_id
            task.mkdir(parents=True)
            (task / "state.json").write_text(json.dumps({
                "schema_version": "valp-visible-loop-state.v2",
                "task_id": task_id,
                "profile": "agent-runtime",
                "status": "executing",
                "revision": 0,
                "runtime_adapter": {"class": "manual"},
                "selected_agents": ["manual-operator"],
                "role_assignments": {"reviewer": "manual-operator"},
            }), encoding="utf-8")
            (task / "dispatch-receipts.jsonl").write_text(json.dumps({
                "ts": "2026-07-13T10:28:00Z",
                "agent": "manual-operator",
                "role": "reviewer",
                "event": "manual_delivery_attested",
                "dispatch_ref": "agents/manual-operator/dispatch.md",
                "expected_refs": ["agents/manual-operator/review.md"],
            }) + "\n", encoding="utf-8")
            suspend_task(root, task_id, timeout_seconds=60)

            item = TaskAudit(task).check_deterministic_wake()

            self.assertEqual(item.status, WARN)
            self.assertIn("degraded", item.message)

    def test_manual_v2_accepts_legacy_attestations_before_degraded_wait_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-MANUAL-V2-LEGACY-ATTESTATION"
            task = root / ".herdr-loop" / "tasks" / task_id
            task.mkdir(parents=True)
            role_assignments = {"reviewer": "manual-operator"}
            dependencies = build_submission_dependencies(task_id, role_assignments)
            item = dependencies["work_items"][0]
            (task / "state.json").write_text(json.dumps({
                "schema_version": "valp-visible-loop-state.v2",
                "task_id": task_id,
                "profile": "agent-runtime",
                "status": "executing",
                "revision": 0,
                "runtime_adapter": {"class": "manual"},
                "selected_agents": ["manual-operator"],
                "role_assignments": role_assignments,
            }), encoding="utf-8")
            (task / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            for ref in item["expected_refs"]:
                path = task / ref
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("attested\n", encoding="utf-8")
            receipts_path = task / "dispatch-receipts.jsonl"
            legacy_base = {
                "ts": "2026-07-13T10:28:00Z",
                "agent": "manual-operator",
                "role": "reviewer",
                "dispatch_ref": "agents/manual-operator/dispatch.md",
                "expected_refs": item["expected_refs"],
            }
            receipts_path.write_text(
                json.dumps({**legacy_base, "event": "manual_delivery_attested"}) + "\n",
                encoding="utf-8",
            )
            suspend_task(root, task_id, timeout_seconds=60)
            with receipts_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({
                        **legacy_base,
                        "ts": "2026-07-13T10:29:00Z",
                        "event": "manual_result_attested",
                    })
                    + "\n"
                )
            resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#2",
            )

            audit = TaskAudit(task)
            dispatch_item = audit.check_dispatch_receipts()
            wait_item = audit.check_deterministic_wake()

            self.assertEqual((dispatch_item.status, wait_item.status), (PASS, WARN))
            self.assertIn("degraded", wait_item.message)

    def test_manual_v2_rejects_shared_agent_role_receipt_lending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-MANUAL-V2-ROLE-LENDING"
            task = root / ".herdr-loop" / "tasks" / task_id
            task.mkdir(parents=True)
            role_assignments = {
                "implementer": "manual-operator",
                "reviewer": "manual-operator",
            }
            dependencies = build_submission_dependencies(task_id, role_assignments)
            marker = {"status": "recorded", "ref": "submission-dependencies.json"}
            state = {
                "schema_version": "valp-visible-loop-state.v2",
                "task_id": task_id,
                "profile": "agent-runtime",
                "status": "executing",
                "revision": 0,
                "runtime_adapter": {"class": "manual"},
                "selected_agents": ["manual-operator"],
                "role_assignments": role_assignments,
                "submission_dependencies": marker,
            }
            routing = {
                "task_id": task_id,
                "runtime_adapter": {"class": "manual"},
                "selected_agents": ["manual-operator"],
                "role_assignments": role_assignments,
                "submission_dependencies": marker,
            }
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")
            (task / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            all_refs = [
                ref
                for item in dependencies["work_items"]
                for ref in item["expected_refs"]
            ]
            for ref in all_refs:
                path = task / ref
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("attested\n", encoding="utf-8")
            base = {
                "agent": "manual-operator",
                "role": "implementer",
                "dispatch_ref": "agents/manual-operator/dispatch.md",
                "expected_refs": all_refs,
            }
            receipts = [
                {**base, "ts": "2026-07-13T10:28:00Z", "event": "manual_result_attested"},
                {**base, "ts": "2026-07-13T10:29:00Z", "event": "manual_delivery_attested"},
                {**base, "ts": "2026-07-13T10:30:00Z", "event": "manual_result_attested"},
            ]
            (task / "dispatch-receipts.jsonl").write_text(
                "".join(json.dumps(receipt) + "\n" for receipt in receipts),
                encoding="utf-8",
            )

            audit = TaskAudit(task)

            self.assertEqual(audit.check_dispatch_receipts().status, FAIL)
            self.assertEqual(audit.check_submission_dependencies().status, FAIL)

    def test_manual_v2_accepts_separate_shared_agent_role_attestations_as_degraded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-MANUAL-V2-SEPARATE-ROLES"
            task = root / ".herdr-loop" / "tasks" / task_id
            task.mkdir(parents=True)
            role_assignments = {
                "implementer": "manual-operator",
                "reviewer": "manual-operator",
            }
            dependencies = build_submission_dependencies(task_id, role_assignments)
            work_items = {item["role"]: item for item in dependencies["work_items"]}
            marker = {"status": "recorded", "ref": "submission-dependencies.json"}
            state = {
                "schema_version": "valp-visible-loop-state.v2",
                "task_id": task_id,
                "profile": "agent-runtime",
                "status": "executing",
                "revision": 0,
                "runtime_adapter": {"class": "manual"},
                "selected_agents": ["manual-operator"],
                "role_assignments": role_assignments,
                "submission_dependencies": marker,
            }
            routing = {
                "task_id": task_id,
                "runtime_adapter": {"class": "manual"},
                "selected_agents": ["manual-operator"],
                "role_assignments": role_assignments,
                "submission_dependencies": marker,
            }
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")
            (task / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            for item in work_items.values():
                for ref in item["expected_refs"]:
                    path = task / ref
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("attested\n", encoding="utf-8")
            receipts_path = task / "dispatch-receipts.jsonl"
            receipts_path.write_text(
                json.dumps({
                    "ts": "2026-07-13T10:28:00Z",
                    "agent": "manual-operator",
                    "role": "implementer",
                    "event": "manual_delivery_attested",
                    "dispatch_ref": "agents/manual-operator/dispatch.md",
                    "expected_refs": work_items["implementer"]["expected_refs"],
                })
                + "\n",
                encoding="utf-8",
            )
            suspend_task(root, task_id, timeout_seconds=60)
            implementer_result = {
                "ts": "2026-07-13T10:29:00Z",
                "agent": "manual-operator",
                "role": "implementer",
                "event": "manual_result_attested",
                "dispatch_ref": "agents/manual-operator/dispatch.md",
                "expected_refs": work_items["implementer"]["expected_refs"],
            }
            with receipts_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(implementer_result) + "\n")
            resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#2",
            )
            reviewer_receipts = [
                {
                    "ts": "2026-07-13T10:30:00Z",
                    "agent": "manual-operator",
                    "role": "reviewer",
                    "event": "manual_delivery_attested",
                    "dispatch_ref": "agents/manual-operator/dispatch.md",
                    "expected_refs": work_items["reviewer"]["expected_refs"],
                },
                {
                    "ts": "2026-07-13T10:31:00Z",
                    "agent": "manual-operator",
                    "role": "reviewer",
                    "event": "manual_result_attested",
                    "dispatch_ref": "agents/manual-operator/dispatch.md",
                    "expected_refs": work_items["reviewer"]["expected_refs"],
                },
            ]
            with receipts_path.open("a", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(receipt) + "\n" for receipt in reviewer_receipts))

            audit = TaskAudit(task)

            self.assertEqual(audit.check_dispatch_receipts().status, PASS)
            self.assertEqual(audit.check_submission_dependencies().status, PASS)
            wait_item = audit.check_deterministic_wake()
            self.assertEqual(wait_item.status, WARN)
            self.assertIn("degraded", wait_item.message)

    def test_deterministic_wake_audit_accepts_generated_barrier_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAKE-AUDIT-PASS"
            task = root / ".herdr-loop" / "tasks" / task_id
            task.mkdir(parents=True)
            dependencies = build_submission_dependencies(task_id, {"implementer": "codex"})
            work_item = dependencies["work_items"][0]
            (task / "state.json").write_text(json.dumps({
                "schema_version": "valp-visible-loop-state.v2",
                "task_id": task_id,
                "profile": "agent-runtime",
                "status": "executing",
                "revision": 0,
                "runtime_adapter": {"class": "daemon_queue"},
                "selected_agents": ["codex"],
                "role_assignments": {"implementer": "codex"},
            }), encoding="utf-8")
            (task / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            (task / "wait-policy.json").write_text(json.dumps({
                "schema_version": "valp-wait-policy.v1",
                "task_id": task_id,
                "wait_policy_id": "next-step",
                "mode": "dependency_ready",
                "exception_policy": "exception_short_circuit",
                "dependency_ref": "submission-dependencies.json",
                "required_work_items": [work_item],
                "exception_events": [
                    "dispatch_blocked",
                    "runtime_failure",
                    "cancellation",
                    "timeout",
                    "user_input",
                ],
            }), encoding="utf-8")
            for ref in work_item["expected_refs"]:
                evidence_path = task / ref
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text("verified\n", encoding="utf-8")
            delivery = {
                "schema_version": "valp-dispatch-receipt.v2",
                "receipt_id": "receipt-1",
                "task_id": task_id,
                "event_sequence": 1,
                "ts": "2026-07-13T10:28:15Z",
                "agent": "codex",
                "role": "implementer",
                "work_item_id": work_item["work_item_id"],
                "dispatch_id": work_item["dispatch_id"],
                "dispatch_generation": 1,
                "event": "dispatch_submitted",
                "dispatch_ref": "agents/codex/dispatch.md",
                "expected_refs": work_item["expected_refs"],
                "proof": {
                    "runtime": "test queue adapter",
                    "submission_id": "submission-1",
                },
            }
            receipts_path = task / "dispatch-receipts.jsonl"
            receipts_path.write_text(json.dumps(delivery) + "\n", encoding="utf-8")
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            completion = {
                **delivery,
                "receipt_id": "receipt-2",
                "event_sequence": 2,
                "ts": "2026-07-13T10:28:55Z",
                "event": "dispatch_completed",
                "suspension_epoch": suspension["suspension_epoch"],
            }
            with receipts_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(completion) + "\n")
            resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#2",
            )

            item = TaskAudit(task).check_deterministic_wake()

            self.assertEqual(item.status, PASS)
            self.assertIn("dependency_ready", item.message)
            completed_state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            completed_state["status"] = "done"
            (task / "state.json").write_text(json.dumps(completed_state), encoding="utf-8")
            lifecycle_item = TaskAudit(task).check_deterministic_wake()
            self.assertEqual(lifecycle_item.status, PASS)
            schema_by_path = {
                task / "state.json": ROOT / "schemas/state.schema.json",
                task / "wait-policy.json": ROOT / "schemas/wait-policy.schema.json",
            }
            for artifact_path, schema_path in schema_by_path.items():
                validator = schema_validator(schema_path)
                errors = list(validator.iter_errors(json.loads(artifact_path.read_text(encoding="utf-8"))))
                self.assertEqual(errors, [], artifact_path.name)
            receipt_validator = Draft202012Validator(
                json.loads((ROOT / "schemas/receipts.schema.json").read_text(encoding="utf-8"))
            )
            for line in receipts_path.read_text(encoding="utf-8").splitlines():
                self.assertEqual(list(receipt_validator.iter_errors(json.loads(line))), [])
            event_validator = schema_validator(ROOT / "schemas/wait-event.schema.json")
            for line in (task / "wait-events.jsonl").read_text(encoding="utf-8").splitlines():
                self.assertEqual(list(event_validator.iter_errors(json.loads(line))), [])
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            result_path = task / state["suspension"]["accepted_wake"]["result_ref"]
            result_validator = Draft202012Validator(
                json.loads((ROOT / "schemas/wake-result.schema.json").read_text(encoding="utf-8"))
            )
            self.assertEqual(
                list(result_validator.iter_errors(json.loads(result_path.read_text(encoding="utf-8")))),
                [],
            )
            original_result = result_path.read_text(encoding="utf-8")
            result_with_extra_field = json.loads(original_result)
            result_with_extra_field["unexpected"] = True
            result_path.write_text(json.dumps(result_with_extra_field), encoding="utf-8")
            closed_result = TaskAudit(task).check_deterministic_wake()
            self.assertEqual(closed_result.status, FAIL)
            self.assertIn("fields", closed_result.message.lower())
            result_path.write_text(original_result, encoding="utf-8")

            original_state = (task / "state.json").read_text(encoding="utf-8")
            original_events = (task / "wait-events.jsonl").read_text(encoding="utf-8")
            forged_event_id = "sha256:" + ("e" * 64)
            forged_wake_id = "sha256:" + ("f" * 64)
            forged_state = json.loads(original_state)
            forged_state["suspension"]["accepted_wake"]["wake_id"] = forged_wake_id
            forged_state["suspension"]["accepted_wake"]["wake_event_id"] = forged_event_id
            event_lines = [json.loads(line) for line in original_events.splitlines()]
            event_lines[-1]["event_id"] = forged_event_id
            event_lines[-1]["wake_id"] = forged_wake_id
            event_lines[-1]["projection"]["suspension"] = forged_state["suspension"]
            forged_result = json.loads(original_result)
            forged_result["wake_id"] = forged_wake_id
            forged_result["wake_event_id"] = forged_event_id
            (task / "state.json").write_text(json.dumps(forged_state), encoding="utf-8")
            (task / "wait-events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in event_lines),
                encoding="utf-8",
            )
            result_path.write_text(json.dumps(forged_result), encoding="utf-8")
            forged_ids = TaskAudit(task).check_deterministic_wake()
            self.assertEqual(forged_ids.status, FAIL)
            self.assertIn("derived", forged_ids.message.lower())
            (task / "state.json").write_text(original_state, encoding="utf-8")
            (task / "wait-events.jsonl").write_text(original_events, encoding="utf-8")
            result_path.write_text(original_result, encoding="utf-8")
            receipt_lines = [json.loads(line) for line in receipts_path.read_text(encoding="utf-8").splitlines()]
            receipt_lines[-1]["role"] = "reviewer"
            receipts_path.write_text(
                "".join(json.dumps(receipt) + "\n" for receipt in receipt_lines),
                encoding="utf-8",
            )
            tampered = TaskAudit(task).check_deterministic_wake()
            self.assertEqual(tampered.status, FAIL)
            self.assertIn("receipt", tampered.message.lower())

    def test_state_v2_cannot_delete_delegation_policy_and_claim_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            routing = json.loads((task / "routing.json").read_text(encoding="utf-8"))
            state["schema_version"] = "valp-visible-loop-state.v2"
            state["revision"] = 0
            state.pop("delegation_policy", None)
            routing.pop("delegation_policy", None)
            (task / "delegation-policy.json").unlink(missing_ok=True)
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")

            item = TaskAudit(task).check_delegation_policy()

            self.assertEqual(item.status, FAIL)
            self.assertIn("missing", item.message.lower())

    def test_recorded_dispatch_payload_budget_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            routing = json.loads((task / "routing.json").read_text(encoding="utf-8"))
            routing["dispatch_payload_budgets"] = {
                "codex": {
                    "role": "implementer",
                    "max_chars": 2800,
                    "max_reference_tokens": 700,
                    "token_estimator": "ceil(chars/4)",
                    "actual_chars": 3887,
                    "actual_reference_tokens": 972,
                    "dispatch_ref": "agents/codex/dispatch.md",
                }
            }
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")

            report = TaskAudit(task).run()

            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "context_pack" and item.status == FAIL for item in report.items))

    def test_dispatch_payload_counts_use_canonical_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            dispatch_ref = "agents/codex/dispatch.md"
            dispatch_path = task / dispatch_ref
            canonical_text = dispatch_path.read_text(encoding="utf-8")
            dispatch_path.write_bytes(canonical_text.replace("\n", "\r\n").encode("utf-8"))
            routing = json.loads((task / "routing.json").read_text(encoding="utf-8"))
            routing["dispatch_payload_budgets"] = {
                "codex": {
                    "role": "implementer",
                    "max_chars": len(canonical_text),
                    "max_reference_tokens": (len(canonical_text) + 3) // 4,
                    "token_estimator": "ceil(chars/4)",
                    "actual_chars": len(canonical_text),
                    "actual_reference_tokens": (len(canonical_text) + 3) // 4,
                    "dispatch_ref": dispatch_ref,
                }
            }
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")

            report = TaskAudit(task).run()
            context_item = next(item for item in report.items if item.id == "context_pack")
            self.assertEqual(context_item.status, PASS)

    def test_historical_dispatch_boundary_rejects_unverifiable_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-HISTORICAL"
            decision_task_id = "TASK-RECONCILIATION"
            task = root / ".herdr-loop" / "tasks" / task_id
            shutil.copytree(EXAMPLE, task)
            decision_ref = f".herdr-loop/tasks/{decision_task_id}/evidence/decision.md"
            decision_path = root / decision_ref
            decision_path.parent.mkdir(parents=True)
            decision_path.write_text("Independent reconciliation decision.\n", encoding="utf-8")

            dispatch_ref = "agents/codex/dispatch.md"
            dispatch_path = task / dispatch_ref
            original_text = dispatch_path.read_text(encoding="utf-8")
            recorded_budget = {
                "role": "implementer",
                "max_chars": len(original_text),
                "max_reference_tokens": (len(original_text) + 3) // 4,
                "token_estimator": "ceil(chars/4)",
                "actual_chars": len(original_text),
                "actual_reference_tokens": (len(original_text) + 3) // 4,
                "dispatch_ref": dispatch_ref,
            }
            dispatch_path.write_text(dispatch_path.read_text(encoding="utf-8") + ("x" * 500), encoding="utf-8")
            dispatch_text = dispatch_path.read_text(encoding="utf-8")
            routing = json.loads((task / "routing.json").read_text(encoding="utf-8"))
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            routing["task_id"] = task_id
            routing["dispatch_payload_budgets"] = {"codex": recorded_budget}
            state["task_id"] = task_id
            state["status"] = "done"
            boundary = {
                "schema_version": "valp-historical-audit-boundary.v1",
                "task_id": task_id,
                "recorded_at": "2026-07-12T00:00:00Z",
                "decision_task_id": decision_task_id,
                "decision_ref": decision_ref,
                "auditor_boundary": {
                    "historical_cli_version": "0.2.0",
                    "historical_source_revision": "1" * 40,
                    "rule_introduced_revision": "2" * 40,
                },
                "accepted_legacy_artifacts": [
                    {
                        "rule_id": "context_pack.dispatch_payload_budget",
                        "agent": "codex",
                        "artifact_ref": dispatch_ref,
                        "byte_digest": "sha256:" + hashlib.sha256(dispatch_path.read_bytes()).hexdigest(),
                        "recorded_budget": recorded_budget,
                        "observed": {
                            "actual_chars": len(dispatch_text),
                            "actual_reference_tokens": (len(dispatch_text) + 3) // 4,
                        },
                        "disposition": "accept_historical_artifact",
                        "reason": "The immutable dispatch predates this audit rule.",
                    }
                ],
            }
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task / "historical-audit-boundary.json").write_text(json.dumps(boundary), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertGreater(report.fail_count, 0)
            self.assertTrue(any(item.id == "context_pack" and item.status == FAIL for item in report.items))

    def test_verified_historical_dispatch_boundary_remains_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-HISTORICAL-VERIFIED"
            decision_task_id = "TASK-RECONCILIATION-VERIFIED"
            task = root / ".herdr-loop" / "tasks" / task_id
            shutil.copytree(EXAMPLE, task)
            historical_revision = subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD^"],
                text=True,
            ).strip()
            introduced_revision = subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            decision_ref = f".herdr-loop/tasks/{decision_task_id}/evidence/decision.md"
            decision_task = root / ".herdr-loop" / "tasks" / decision_task_id
            decision_path = root / decision_ref
            decision_path.parent.mkdir(parents=True)
            (decision_task / "task.md").write_text(
                "# Task\n\nReconcile historical dispatch evidence.\n",
                encoding="utf-8",
            )
            (decision_task / "state.json").write_text(json.dumps({
                "schema_version": "valp-visible-loop-state.v1",
                "task_id": decision_task_id,
                "profile": "agent-runtime",
                "status": "done",
                "selected_agents": ["hermes"],
            }), encoding="utf-8")
            decision_path.write_text(
                "\n".join([
                    f"source_task_id: {task_id}",
                    "rule_id: context_pack.dispatch_payload_budget",
                    f"historical_source_revision: {historical_revision}",
                    f"rule_introduced_revision: {introduced_revision}",
                    "decision: accept_historical_artifact",
                ]) + "\n",
                encoding="utf-8",
            )

            dispatch_ref = "agents/codex/dispatch.md"
            dispatch_path = task / dispatch_ref
            original_text = dispatch_path.read_text(encoding="utf-8")
            recorded_budget = {
                "role": "implementer",
                "max_chars": len(original_text),
                "max_reference_tokens": (len(original_text) + 3) // 4,
                "token_estimator": "ceil(chars/4)",
                "actual_chars": len(original_text),
                "actual_reference_tokens": (len(original_text) + 3) // 4,
                "dispatch_ref": dispatch_ref,
            }
            dispatch_path.write_text(original_text + ("x" * 500), encoding="utf-8")
            dispatch_text = dispatch_path.read_text(encoding="utf-8")
            routing = json.loads((task / "routing.json").read_text(encoding="utf-8"))
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            routing["task_id"] = task_id
            routing["dispatch_payload_budgets"] = {"codex": recorded_budget}
            state["task_id"] = task_id
            state["status"] = "done"
            declaration = json.loads((task / "assignment-declaration.json").read_text(encoding="utf-8"))
            declaration["task_id"] = task_id
            validation = json.loads((task / "assignment-validation.json").read_text(encoding="utf-8"))
            validation["task_id"] = task_id
            (task / "assignment-declaration.json").write_text(json.dumps(declaration), encoding="utf-8")
            (task / "assignment-validation.json").write_text(json.dumps(validation), encoding="utf-8")
            iteration_budget = json.loads((task / "iteration-budget.json").read_text(encoding="utf-8"))
            iteration_budget["task_id"] = task_id
            (task / "iteration-budget.json").write_text(json.dumps(iteration_budget), encoding="utf-8")
            for slice_path in (task / "skill-slices").glob("*.json"):
                skill_slice = json.loads(slice_path.read_text(encoding="utf-8"))
                skill_slice["task_id"] = task_id
                slice_path.write_text(json.dumps(skill_slice), encoding="utf-8")
            boundary = {
                "schema_version": "valp-historical-audit-boundary.v1",
                "task_id": task_id,
                "recorded_at": "2026-07-13T00:00:00Z",
                "decision_task_id": decision_task_id,
                "decision_ref": decision_ref,
                "auditor_boundary": {
                    "historical_cli_version": "0.2.0",
                    "historical_source_revision": historical_revision,
                    "rule_introduced_revision": introduced_revision,
                },
                "accepted_legacy_artifacts": [
                    {
                        "rule_id": "context_pack.dispatch_payload_budget",
                        "agent": "codex",
                        "artifact_ref": dispatch_ref,
                        "byte_digest": "sha256:" + hashlib.sha256(dispatch_path.read_bytes()).hexdigest(),
                        "recorded_budget": recorded_budget,
                        "observed": {
                            "actual_chars": len(dispatch_text),
                            "actual_reference_tokens": (len(dispatch_text) + 3) // 4,
                        },
                        "disposition": "accept_historical_artifact",
                        "reason": "Verified immutable dispatch predates this audit rule.",
                    }
                ],
            }
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task / "historical-audit-boundary.json").write_text(json.dumps(boundary), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, WARN)
            self.assertEqual(report.fail_count, 0)
            self.assertTrue(any(item.id == "context_pack" and item.status == WARN for item in report.items))

            dispatch_path.write_text(dispatch_text + "tamper", encoding="utf-8")
            tampered = TaskAudit(task).run()
            self.assertEqual(tampered.status, FAIL)

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

    def test_done_routing_feedback_requires_passed_verification_and_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            feedback = json.loads((task / "routing-feedback.json").read_text(encoding="utf-8"))
            feedback["review_result"] = "failed"
            (task / "routing-feedback.json").write_text(json.dumps(feedback), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(
                any(
                    item.id == "routing_feedback"
                    and item.status == FAIL
                    and "passed verification_result and review_result" in item.message
                    for item in report.items
                )
            )

    def test_done_routing_feedback_requires_existing_actual_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            feedback = json.loads((task / "routing-feedback.json").read_text(encoding="utf-8"))
            feedback["actual_evidence"].append("evidence/missing-proof.md")
            (task / "routing-feedback.json").write_text(json.dumps(feedback), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(
                any(
                    item.id == "routing_feedback"
                    and item.status == FAIL
                    and "refs are missing" in item.message
                    for item in report.items
                )
            )

    def test_done_routing_feedback_rejects_cross_task_actual_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = workspace / ".herdr-loop" / "tasks" / "current"
            other_evidence = workspace / ".herdr-loop" / "tasks" / "other" / "evidence.md"
            shutil.copytree(EXAMPLE, task)
            other_evidence.parent.mkdir(parents=True)
            other_evidence.write_text("other task proof\n", encoding="utf-8")
            feedback = json.loads((task / "routing-feedback.json").read_text(encoding="utf-8"))
            feedback["actual_evidence"].append(".herdr-loop/tasks/other/evidence.md")
            (task / "routing-feedback.json").write_text(json.dumps(feedback), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(
                any(
                    item.id == "routing_feedback"
                    and item.status == FAIL
                    and "refs are missing" in item.message
                    for item in report.items
                )
            )

    def test_long_fenced_code_in_review_does_not_crash_claim_evidence_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            review = task / "agents" / "claude" / "review.md"
            review.write_text(
                review.read_text(encoding="utf-8")
                + "\n```python\n"
                + ("long_non_path_code = True  # padding\n" * 30)
                + "```\n",
                encoding="utf-8",
            )

            report = TaskAudit(task).run()
            self.assertEqual(report.status, PASS)

    def test_context_pack_can_resolve_contained_workspace_context_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            task = workspace / ".herdr-loop" / "tasks" / "current"
            shutil.copytree(EXAMPLE, task)
            (workspace / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
            context_pack = json.loads((task / "context-pack.json").read_text(encoding="utf-8"))
            context_pack["items"][0]["evidence_refs"] = ["AGENTS.md"]
            (task / "context-pack.json").write_text(json.dumps(context_pack), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, PASS)

    def test_structured_supporting_ref_can_back_review_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            review = task / "agents" / "claude" / "review.md"
            review.write_text("Tests passed according to coordinator verification evidence.\n", encoding="utf-8")
            verification = task / "evidence" / "verification.md"
            verification.write_text(
                "```text\n$ python3 -m unittest tests/test_valp_audit.py\nRan 1 test\nOK\nexit_code: 0\n```\n",
                encoding="utf-8",
            )
            evidence_status = json.loads((task / "evidence-status.json").read_text(encoding="utf-8"))
            evidence_status["evidence"]["agents/claude/review.md"]["supporting_refs"] = [
                "evidence/verification.md"
            ]
            (task / "evidence-status.json").write_text(json.dumps(evidence_status), encoding="utf-8")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, PASS)

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

    def test_v2_dispatch_audit_requires_submission_proof_for_each_work_item_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            task.mkdir()
            task_id = "TASK-SAME-AGENT-MULTI-ROLE"
            role_assignments = {"implementer": "codex", "reviewer": "codex"}
            dependencies = build_submission_dependencies(task_id, role_assignments)
            items = {item["role"]: item for item in dependencies["work_items"]}
            (task / "state.json").write_text(json.dumps({
                "schema_version": "valp-visible-loop-state.v2",
                "task_id": task_id,
                "profile": "agent-runtime",
                "status": "executing",
                "revision": 0,
                "runtime_adapter": {"class": "daemon_queue"},
                "selected_agents": ["codex"],
                "role_assignments": role_assignments,
            }), encoding="utf-8")
            (task / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )

            def receipt(role: str, event: str, sequence: int) -> dict[str, object]:
                item = items[role]
                record: dict[str, object] = {
                    "schema_version": "valp-dispatch-receipt.v2",
                    "receipt_id": f"receipt-{sequence}",
                    "task_id": task_id,
                    "event_sequence": sequence,
                    "ts": f"2026-07-13T10:28:{sequence:02d}Z",
                    "agent": item["agent"],
                    "role": item["role"],
                    "work_item_id": item["work_item_id"],
                    "dispatch_id": item["dispatch_id"],
                    "dispatch_generation": item["dispatch_generation"],
                    "event": event,
                    "dispatch_ref": f"agents/{item['agent']}/dispatch.md",
                    "expected_refs": item["expected_refs"],
                }
                if event == "dispatch_submitted":
                    record["proof"] = {
                        "runtime": "test queue adapter",
                        "submission_id": f"submission-{sequence}",
                    }
                else:
                    record["suspension_epoch"] = 1
                return record

            receipts = [
                receipt("implementer", "dispatch_submitted", 1),
                receipt("implementer", "dispatch_completed", 2),
                receipt("reviewer", "dispatch_completed", 3),
            ]
            (task / "dispatch-receipts.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in receipts),
                encoding="utf-8",
            )

            item = TaskAudit(task).check_dispatch_receipts()

            self.assertEqual(item.status, FAIL)
            self.assertIn("reviewer:codex", item.message)

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

    def test_coordinated_negated_approval_terms_do_not_require_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "task.md").write_text(
                "# Task\n\n## Goal\n\nMake no GitHub, config, or release changes.\n",
                encoding="utf-8",
            )
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            state["gates"]["approval"] = "not_required"
            state["approval_required"] = []
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")

            item = TaskAudit(task).check_approvals()

            self.assertEqual(item.status, PASS)

    def test_approval_audit_detects_live_action_after_non_actionable_clause(self) -> None:
        for goal, expected_kind in (
            ("Documentation only first, then deploy production.", "deploy"),
            ("Print only the plan, but submit the app tomorrow.", "submit"),
        ):
            with self.subTest(goal=goal):
                with tempfile.TemporaryDirectory() as tmp:
                    task = Path(tmp) / "task"
                    shutil.copytree(EXAMPLE, task)
                    (task / "task.md").write_text(
                        f"# Task\n\n## Goal\n\n{goal}\n",
                        encoding="utf-8",
                    )
                    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
                    state["gates"]["approval"] = "not_required"
                    state["approval_required"] = []
                    (task / "state.json").write_text(json.dumps(state), encoding="utf-8")

                    item = TaskAudit(task).check_approvals()

                    self.assertEqual(item.status, FAIL)
                    self.assertIn(expected_kind, item.message)

    def test_approval_audit_detects_explicitly_executed_literals(self) -> None:
        cases = (
            ("Run `deploy production` now.", "deploy"),
            ("Execute 'rm -rf build/' now.", "delete"),
            ('Execute "submit the app" now.', "submit"),
            ("Execute this command:\n```sh\nrm -rf build/\n```", "delete"),
        )
        for goal, expected_kind in cases:
            with self.subTest(goal=goal):
                with tempfile.TemporaryDirectory() as tmp:
                    task = Path(tmp) / "task"
                    shutil.copytree(EXAMPLE, task)
                    (task / "task.md").write_text(
                        f"# Task\n\n## Goal\n\n{goal}\n",
                        encoding="utf-8",
                    )
                    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
                    state["gates"]["approval"] = "not_required"
                    state["approval_required"] = []
                    (task / "state.json").write_text(json.dumps(state), encoding="utf-8")

                    item = TaskAudit(task).check_approvals()

                    self.assertEqual(item.status, FAIL)
                    self.assertIn(expected_kind, item.message)

    def test_approval_audit_detects_independent_coordinated_actions(self) -> None:
        cases = (
            ("Make no release changes and deploy production.", "deploy"),
            ("Print only the summary and submit the final report.", "submit"),
            ("Make no credential rotations and upload the package.", "upload"),
            ("Print only the checksum and release the archive.", "release"),
        )
        for goal, expected_kind in cases:
            with self.subTest(goal=goal):
                with tempfile.TemporaryDirectory() as tmp:
                    task = Path(tmp) / "task"
                    shutil.copytree(EXAMPLE, task)
                    (task / "task.md").write_text(
                        f"# Task\n\n## Goal\n\n{goal}\n",
                        encoding="utf-8",
                    )
                    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
                    state["gates"]["approval"] = "not_required"
                    state["approval_required"] = []
                    (task / "state.json").write_text(json.dumps(state), encoding="utf-8")

                    item = TaskAudit(task).check_approvals()

                    self.assertEqual(item.status, FAIL)
                    self.assertIn(expected_kind, item.message)

    def test_approval_audit_ignores_printed_high_risk_command_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "task.md").write_text(
                "# Task\n\n## Goal\n\nPrint only the release and submit commands.\n",
                encoding="utf-8",
            )
            state = json.loads((task / "state.json").read_text(encoding="utf-8"))
            state["gates"]["approval"] = "not_required"
            state["approval_required"] = []
            (task / "state.json").write_text(json.dumps(state), encoding="utf-8")

            item = TaskAudit(task).check_approvals()

            self.assertEqual(item.status, PASS)

    def test_approval_audit_scopes_comma_coordinate_negation_to_the_list(self) -> None:
        cases = (
            ("Make no release, deploy, or upload changes.", PASS),
            ("Do not deploy, submit, or release the app.", PASS),
            ("Make no credential or upload changes.", PASS),
            ("Print only release and submit commands.", PASS),
            ("Make no release changes, then deploy production.", FAIL),
        )
        for goal, expected_status in cases:
            with self.subTest(goal=goal):
                with tempfile.TemporaryDirectory() as tmp:
                    task = Path(tmp) / "task"
                    shutil.copytree(EXAMPLE, task)
                    (task / "task.md").write_text(
                        f"# Task\n\n## Goal\n\n{goal}\n",
                        encoding="utf-8",
                    )
                    state = json.loads((task / "state.json").read_text(encoding="utf-8"))
                    state["gates"]["approval"] = "not_required"
                    state["approval_required"] = []
                    (task / "state.json").write_text(json.dumps(state), encoding="utf-8")

                    item = TaskAudit(task).check_approvals()

                    self.assertEqual(item.status, expected_status)

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
                "context-pack.json",
                "mask-list.json",
                "evidence-board.json",
                "visible-routing.md",
            ]:
                (task / name).unlink()

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "visible_attention" and item.status == FAIL for item in report.items))

    def test_missing_context_pack_fails_for_non_trivial_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "context-pack.json").unlink()

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "context_pack" and item.status == FAIL for item in report.items))

    def test_missing_learning_feedback_fails_for_non_trivial_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "learning-feedback.json").unlink()

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "learning_feedback" and item.status == FAIL for item in report.items))

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
