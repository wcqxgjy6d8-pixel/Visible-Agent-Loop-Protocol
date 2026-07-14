from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from valp_cli.control_plane import ControlPlaneError, InstallationCore, digest_without
from valp_cli.plugins import validate_plugin_manifest
from valp_cli.task_control import init_task, task_state, transition_task
from valp_cli.process_adapter import run_process


ROOT = Path(__file__).resolve().parents[1]


class ControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="valp-control-plane-test-")
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / ".valp"
        self.core = InstallationCore(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _bootstrap(self) -> None:
        self.core.init()
        self.core.discover_candidates()
        self.core.select_leader("manual-user")

    def test_bootstrap_requires_explicit_leader_selection(self) -> None:
        self.core.init()
        self.core.discover_candidates()
        self.assertEqual(self.core.state()["status"], "awaiting_leader_selection")
        with self.assertRaises(ControlPlaneError) as context:
            self.core.select_leader("missing-principal")
        self.assertEqual(context.exception.code, "VALP-E-PERMISSION-DENIED")

    def test_epoch_zero_is_fenced_after_activation(self) -> None:
        self._bootstrap()
        state = self.core.state()
        with self.assertRaises(ControlPlaneError) as context:
            self.core._transition(
                event_kind="test.fenced",
                message_kind="command.test.fenced",
                principal_id="bootstrap-controller",
                principal_kind="bootstrap-controller",
                epoch=0,
                expected_revision=state["revision"],
                payload={},
                target_status="degraded",
                idempotency_key="test-fenced",
            )
        self.assertEqual(context.exception.code, "VALP-E-LEADER-EPOCH")
        self.assertEqual(self.core.state()["status"], "active")

    def test_stale_revision_is_fail_closed(self) -> None:
        self._bootstrap()
        state = self.core.state()
        with self.assertRaises(ControlPlaneError) as context:
            self.core._transition(
                event_kind="test.cas",
                message_kind="command.test.cas",
                principal_id="manual-user",
                principal_kind="human",
                epoch=state["active_leader_epoch"],
                expected_revision=state["revision"] - 1,
                payload={},
                target_status="degraded",
                idempotency_key="test-cas",
            )
        self.assertEqual(context.exception.code, "VALP-E-STATE-CONFLICT")
        self.assertEqual(self.core.state()["revision"], state["revision"])
        failures = [json.loads(line) for line in (self.root / "failures.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual(failures[-1]["error_code"], "VALP-E-STATE-CONFLICT")

    def test_idempotency_replay_and_conflict(self) -> None:
        self._bootstrap()
        state = self.core.state()
        arguments = dict(
            event_kind="test.degraded",
            message_kind="command.test.degraded",
            principal_id="manual-user",
            principal_kind="human",
            epoch=state["active_leader_epoch"],
            expected_revision=state["revision"],
            payload={"reason": "test"},
            target_status="degraded",
            idempotency_key="test-idempotent",
        )
        first = self.core._transition(**arguments)
        second = self.core._transition(**arguments)
        self.assertEqual(first, second)
        self.assertEqual(self.core.state()["revision"], state["revision"] + 1)
        arguments["payload"] = {"reason": "different"}
        with self.assertRaises(ControlPlaneError) as context:
            self.core._transition(**arguments)
        self.assertEqual(context.exception.code, "VALP-E-IDEMPOTENCY-CONFLICT")

    def test_capability_registry_keeps_layers_separate(self) -> None:
        self._bootstrap()
        result = self.core.reconcile_capabilities([
            {"subject_id": "manual-user", "capability_id": "coordination", "layer": "official_claim", "status": "present"},
            {"subject_id": "manual-user", "capability_id": "coordination", "layer": "local_presence", "status": "present"},
            {"subject_id": "manual-user", "capability_id": "coordination", "layer": "live_callable", "status": "pass"},
        ])
        entry = result["registry"]["entries"]["manual-user::coordination"]
        self.assertEqual(set(entry["layers"]), {"official_claim", "local_presence", "live_callable"})
        self.assertEqual(entry["effective_status"], "pass")

    def test_event_replay_detects_tampering(self) -> None:
        self._bootstrap()
        events = self.root / "events.jsonl"
        original = events.read_text(encoding="utf-8")
        tampered = original.replace('"event_kind": "leader_activated"', '"event_kind": "tampered"', 1)
        events.write_text(tampered, encoding="utf-8")
        with self.assertRaises(ControlPlaneError) as context:
            self.core.replay()
        self.assertEqual(context.exception.code, "VALP-E-REGISTRY-CONSISTENCY")

    def test_generated_core_artifacts_match_schemas(self) -> None:
        self._bootstrap()
        self.core.reconcile_capabilities([
            {"subject_id": "manual-user", "capability_id": "coordination", "layer": "live_callable", "status": "pass"},
        ])
        mappings = {
            "installation.json": "installation.schema.json",
            "protocol-manifest.json": "protocol-manifest.schema.json",
            "state.json": "executable-state.schema.json",
            "leader-candidates.json": "leader-candidates.schema.json",
            "capability-registry.json": "capability-registry.schema.json",
            "evidence-manifest.json": "evidence-manifest.schema.json",
        }
        for artifact, schema_name in mappings.items():
            with self.subTest(artifact=artifact):
                schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
                value = json.loads((self.root / artifact).read_text(encoding="utf-8"))
                self.assertEqual(list(Draft202012Validator(schema).iter_errors(value)), [])
        for artifact, schema_name in (("messages.jsonl", "message.schema.json"), ("events.jsonl", "event.schema.json")):
            schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
            validator = Draft202012Validator(schema)
            for line in (self.root / artifact).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.assertEqual(list(validator.iter_errors(json.loads(line))), [])

    def test_plugin_cannot_write_authoritative_ledgers(self) -> None:
        manifest = {
            "schema_version": "valp-plugin-manifest.v1",
            "plugin_id": "safe-discovery",
            "implementation_id": "test",
            "plugin_kind": "discovery",
            "protocol_read_versions": ["0.3.0-draft"],
            "protocol_write_versions": ["0.3.0-draft"],
            "entrypoint": "test:run",
            "permissions": ["capability.observe"],
            "provided_capabilities": ["coordination"],
            "required_capabilities": [],
            "resource_limits": {"timeout_seconds": 1},
            "isolation": "process",
            "manifest_digest": "",
        }
        manifest["manifest_digest"] = digest_without(manifest, "manifest_digest")
        validate_plugin_manifest(manifest)
        manifest["permissions"] = ["state.write"]
        manifest["manifest_digest"] = digest_without(manifest, "manifest_digest")
        with self.assertRaises(ControlPlaneError) as context:
            validate_plugin_manifest(manifest)
        self.assertEqual(context.exception.code, "VALP-E-PLUGIN-BOUNDARY")

    def test_evidence_claim_and_independent_review_preserve_history(self) -> None:
        self._bootstrap()
        subject = self.root / "artifacts" / "result.txt"
        subject.parent.mkdir(parents=True)
        subject.write_text("verified output\n", encoding="utf-8")
        evidence = self.core.add_evidence("artifacts/result.txt", evidence_kind="test-output", producer_principal_id="worker")
        claim = self.core.declare_claim(
            subject_ref="artifacts/result.txt",
            claim_kind="done",
            predicate="artifact is verified",
            asserted_value=True,
            scope="installation-test",
            claimant_principal_id="worker",
            evidence_refs=["artifacts/result.txt"],
        )
        self.assertEqual(claim["status"], "supported")
        reviewed = self.core.record_review(claim_id=claim["claim_id"], reviewer_principal_id="reviewer", verdict="pass")
        self.assertEqual(reviewed["claim"]["status"], "verified")
        claim_records = [json.loads(line) for line in (self.root / "claims.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual([record["status"] for record in claim_records], ["supported", "verified"])
        self.assertEqual(reviewed["review"]["reviewed_subject_digests"], [evidence["content_digest"]])
        for schema_name, value in (
            ("claim.schema.json", claim_records[0]),
            ("claim.schema.json", claim_records[1]),
            ("review.schema.json", reviewed["review"]),
            ("evidence-manifest.schema.json", json.loads((self.root / "evidence-manifest.json").read_text(encoding="utf-8"))),
        ):
            schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(value)), [])

    def test_passing_review_requires_evidence_and_independence(self) -> None:
        self._bootstrap()
        subject = self.root / "result.txt"
        subject.write_text("output\n", encoding="utf-8")
        claim = self.core.declare_claim(
            subject_ref="result.txt",
            claim_kind="done",
            predicate="artifact is verified",
            asserted_value=True,
            scope="test",
            claimant_principal_id="worker",
            evidence_refs=[],
        )
        with self.assertRaises(ControlPlaneError) as missing:
            self.core.record_review(claim_id=claim["claim_id"], reviewer_principal_id="reviewer", verdict="pass")
        self.assertEqual(missing.exception.code, "VALP-E-EVIDENCE-MISSING")

        self.core.add_evidence("result.txt", evidence_kind="test-output", producer_principal_id="worker")
        supported = self.core.declare_claim(
            subject_ref="result.txt",
            claim_kind="done",
            predicate="artifact is verified",
            asserted_value=True,
            scope="test",
            claimant_principal_id="worker",
            evidence_refs=["result.txt"],
        )
        with self.assertRaises(ControlPlaneError) as independent:
            self.core.record_review(claim_id=supported["claim_id"], reviewer_principal_id="worker", verdict="pass")
        self.assertEqual(independent.exception.code, "VALP-E-REVIEW-BLOCKED")

    def test_task_reducer_blocks_runtime_completed_to_done(self) -> None:
        self._bootstrap()
        init_task(self.root, "TASK-001")
        current = task_state(self.root, "TASK-001")
        with self.assertRaises(ControlPlaneError) as direct_done:
            transition_task(self.root, "TASK-001", "done", expected_revision=current["revision"])
        self.assertEqual(direct_done.exception.code, "VALP-E-STATE-TRANSITION")

        published = transition_task(self.root, "TASK-001", "published", expected_revision=current["revision"])
        self.assertEqual(published["status"], "published")
        with self.assertRaises(ControlPlaneError) as missing_gates:
            transition_task(self.root, "TASK-001", "scanning_capabilities", expected_revision=published["revision"])
            transition_task(self.root, "TASK-001", "done", expected_revision=published["revision"] + 1)
        self.assertIn(missing_gates.exception.code, {"VALP-E-STATE-TRANSITION", "VALP-E-EVIDENCE-MISSING"})

    def test_task_done_requires_all_recorded_gates(self) -> None:
        self._bootstrap()
        init_task(self.root, "TASK-002")
        current = task_state(self.root, "TASK-002")
        # Drive the legal path with explicit gate records; runtime completion alone is not a Done gate.
        status = "new"
        revision = current["revision"]
        for target in ["published", "scanning_capabilities", "scanning_context", "loading_local_overlay", "selecting_runtime_adapter", "classifying_task", "selecting_profile", "decomposing_tasks", "recommending_skills", "building_provider_matrix", "scoring_routes", "routing_capabilities", "dispatching", "executing", "verifying", "reviewing", "recording"]:
            state = transition_task(self.root, "TASK-002", target, expected_revision=revision)
            status, revision = state["status"], state["revision"]
        with self.assertRaises(ControlPlaneError) as missing:
            transition_task(self.root, "TASK-002", "done", expected_revision=revision, gates={"receipts": True})
        self.assertEqual(missing.exception.code, "VALP-E-EVIDENCE-MISSING")
        gates = {name: True for name in ("receipts", "expected_evidence", "verification", "review", "approvals", "final_synthesis", "audit")}
        done = transition_task(self.root, "TASK-002", "done", expected_revision=revision, gates=gates)
        self.assertEqual(done["status"], "done")
        schema = json.loads((ROOT / "schemas" / "task-state.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(done)), [])

    def test_local_process_adapter_has_real_submission_and_output_evidence(self) -> None:
        self._bootstrap()
        command = [sys.executable, "-c", "print('process-adapter-ok')"]
        dry_run = run_process(self.root, "PROCESS-001", command)
        self.assertEqual(dry_run["status"], "dry_run")
        result = run_process(self.root, "PROCESS-001", command, approve=True)
        self.assertEqual(result["status"], "completed")
        run_record = result["run"]
        self.assertEqual(run_record["runtime"], "local-process")
        self.assertEqual(run_record["exit_code"], 0)
        self.assertEqual((self.root / run_record["stdout_ref"]).read_text(encoding="utf-8").strip(), "process-adapter-ok")
        schema = json.loads((ROOT / "schemas" / "process-adapter-run.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(run_record)), [])
