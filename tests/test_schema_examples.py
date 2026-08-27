from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from tests.schema_helpers import schema_validator


ROOT = Path(__file__).resolve().parents[1]

EXAMPLE_SCHEMA_BY_NAME = {
    "attention-map.json": "attention-map.schema.json",
    "assignment-declaration.json": "assignment-declaration.schema.json",
    "assignment-validation.json": "assignment-validation.schema.json",
    "automation-policy.json": "automation-policy.schema.json",
    "agent-recommendations.json": "agent-recommendations.schema.json",
    "agent-sessions.json": "agent-sessions.schema.json",
    "capabilities.json": "capabilities.schema.json",
    "capability-passport.json": "capability-passport.schema.json",
    "qwen-capability-passport.json": "capabilities.schema.json",
    "context-pack.json": "context-pack.schema.json",
    "context-selection.json": "context-selection.schema.json",
    "correction-cycle.json": "correction-cycle.schema.json",
    "delegation-policy.json": "delegation-policy.schema.json",
    "evidence-board.json": "evidence-board.schema.json",
    "evidence-catalog-entry.json": "evidence-catalog-entry.schema.json",
    "evidence-catalog-fixtures.json": "evidence-catalog-fixtures.schema.json",
    "evidence-status.json": "evidence-status.schema.json",
    "exception-wake.json": "exception-wake.schema.json",
    "local-overlay.json": "local-overlay.schema.json",
    "mask-list.json": "mask-list.schema.json",
    "routing-feedback.json": "routing-feedback.schema.json",
    "learning-feedback.json": "learning-feedback.schema.json",
    "historical-audit-boundary.json": "historical-audit-boundary.schema.json",
    "routing.json": "routing.schema.json",
    "skill-recommendations.json": "skill-recommendations.schema.json",
    "source-provenance.json": "source-provenance.schema.json",
    "iteration-budget.json": "iteration-budget.schema.json",
    "state.json": "state.schema.json",
    "submission-dependencies.json": "submission-dependencies.schema.json",
    "trigger-policy.json": "trigger-policy.schema.json",
    "wait-policy.json": "wait-policy.schema.json",
    "wake-result.json": "wake-result.schema.json",
    "continuation-envelope.json": "continuation-envelope.schema.json",
    "continuation-capability.json": "continuation-capability.schema.json",
    "continuation-event.json": "continuation-event.schema.json",
    "continuation-invocation-receipt.json": "continuation-invocation-receipt.schema.json",
    "pricing-snapshots.json": "pricing-snapshots.schema.json",
    "cost-budget.json": "cost-budget.schema.json",
    "cost-report.json": "cost-report.schema.json",
    "repair-plan.json": "repair-plan.schema.json",
    "repair-receipt.json": "repair-receipt.schema.json",
    "doctor-proof-certificate.json": "doctor-proof-certificate.schema.json",
    "doctor-snapshot.json": "doctor-snapshot.schema.json",
    "recovery-plan.json": "recovery-plan.schema.json",
    "recovery-approval.json": "recovery-approval.schema.json",
    "recovery-intent.json": "recovery-intent.schema.json",
    "recovery-receipt.json": "recovery-receipt.schema.json",
    "leader-health-policy.json": "leader-health-policy.schema.json",
    "leader-health-record.json": "leader-health-record.schema.json",
    "section20-runtime-report.json": "section20-runtime-report.schema.json",
}


class SchemaExampleTests(unittest.TestCase):
    def test_watcher_trigger_requires_task_source_and_deduplication_identity(self) -> None:
        validator = schema_validator(ROOT / "schemas" / "trigger-policy.schema.json")
        trigger = {
            "schema_version": "valp-trigger-policy.v1",
            "task_id": "TASK-WATCH-1",
            "trigger_mode": "watcher",
            "trigger_source": "runtime_api",
            "source_event_id": "herdr-event-42",
            "matched_signal": "queue item is ready",
            "rule_ref": "runtime-policy.json#queue-ready",
            "risk_classification": "low",
            "selected_action": "publish_only",
            "approval_required": False,
            "deduplication_identity": "sha256:" + "a" * 64,
        }

        self.assertEqual(list(validator.iter_errors(trigger)), [])
        for required_field in (
            "task_id",
            "source_event_id",
            "matched_signal",
            "rule_ref",
            "deduplication_identity",
        ):
            with self.subTest(required_field=required_field):
                incomplete = dict(trigger)
                incomplete.pop(required_field)
                self.assertTrue(list(validator.iter_errors(incomplete)))

    def test_assignment_declaration_requires_user_selected_leader_evidence(self) -> None:
        declaration = json.loads(
            (ROOT / "examples" / "assignment-declaration.json").read_text(encoding="utf-8")
        )
        validator = schema_validator(ROOT / "schemas" / "assignment-declaration.schema.json")

        self.assertEqual(list(validator.iter_errors(declaration)), [])
        declaration["leader"]["selected_by"] = "valp"
        self.assertTrue(list(validator.iter_errors(declaration)))
        declaration["leader"]["selected_by"] = "user"
        declaration["leader"].pop("selection_ref")
        self.assertTrue(list(validator.iter_errors(declaration)))

    def test_blocked_assignment_validation_requires_visible_blockers(self) -> None:
        validation = json.loads(
            (ROOT / "examples" / "assignment-validation.json").read_text(encoding="utf-8")
        )
        validator = schema_validator(ROOT / "schemas" / "assignment-validation.schema.json")

        self.assertEqual(list(validator.iter_errors(validation)), [])
        validation["status"] = "blocked"
        self.assertTrue(list(validator.iter_errors(validation)))

    def test_capability_passport_rejects_unbound_strong_model_evidence(self) -> None:
        passport = json.loads(
            (ROOT / "examples" / "capability-passport.json").read_text(encoding="utf-8")
        )
        validator = schema_validator(ROOT / "schemas" / "capability-passport.schema.json")

        self.assertEqual(list(validator.iter_errors(passport)), [])
        passport["model_identity"]["model_probe"]["session_identity"]["status"] = "unknown"
        self.assertTrue(list(validator.iter_errors(passport)))

    def test_model_aware_provider_matrix_matches_schema(self) -> None:
        data = json.loads(
            (ROOT / "examples" / "model-aware-provider-matrix.json").read_text(encoding="utf-8")
        )
        errors = list(
            schema_validator(ROOT / "schemas" / "provider-matrix-model-aware.schema.json").iter_errors(data)
        )
        self.assertEqual(errors, [])

    def test_public_examples_do_not_embed_operator_provider_snapshots(self) -> None:
        forbidden = ("private-relay", "model-internal", "operator-snapshot")
        violations: list[str] = []
        for path in sorted((ROOT / "examples").rglob("*")):
            if not path.is_file() or path.suffix not in {".json", ".jsonl", ".md", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8")
            for value in forbidden:
                if value in text:
                    violations.append(f"{path.relative_to(ROOT)} contains {value}")

        self.assertEqual(violations, [])

    def test_public_examples_do_not_embed_absolute_operator_paths(self) -> None:
        absolute_operator_path = re.compile(
            r"(?:/Users/|/home/|(?<![A-Za-z0-9])[A-Za-z]:\\+)"
        )
        violations: list[str] = []
        for path in sorted((ROOT / "examples").rglob("*")):
            relative = path.relative_to(ROOT / "examples")
            if "task-graph" in relative.parts or any(part.startswith(".") for part in relative.parts):
                continue
            if not path.is_file() or path.suffix not in {".json", ".jsonl", ".md", ".txt", ".sh"}:
                continue
            if absolute_operator_path.search(path.read_text(encoding="utf-8")):
                violations.append(str(path.relative_to(ROOT)))

        self.assertEqual(violations, [])

    def test_bundled_json_examples_match_schemas(self) -> None:
        validators = {
            schema_name: schema_validator(ROOT / "schemas" / schema_name)
            for schema_name in set(EXAMPLE_SCHEMA_BY_NAME.values())
        }
        errors: list[str] = []
        for path in sorted((ROOT / "examples").rglob("*.json")):
            schema_name = EXAMPLE_SCHEMA_BY_NAME.get(path.name)
            if not schema_name:
                continue
            if path.name == "state.json" and "runtime" in path.relative_to(ROOT / "examples").parts:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for error in validators[schema_name].iter_errors(data):
                errors.append(f"{path.relative_to(ROOT)} {error.json_path}: {error.message}")
        self.assertEqual(errors, [])

    def test_bundled_skill_slices_match_schema(self) -> None:
        validator = schema_validator(ROOT / "schemas" / "skill-recommendation-slice.schema.json")
        errors: list[str] = []
        for path in sorted((ROOT / "examples").rglob("skill-slices/*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            errors.extend(
                f"{path.relative_to(ROOT)} {error.json_path}: {error.message}"
                for error in validator.iter_errors(data)
            )
        self.assertEqual(errors, [])

    def test_bundled_receipt_jsonl_examples_match_schema(self) -> None:
        validator = schema_validator(ROOT / "schemas" / "receipts.schema.json")
        errors: list[str] = []
        for path in sorted((ROOT / "examples").rglob("dispatch-receipts.jsonl")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                data = json.loads(line)
                for error in validator.iter_errors(data):
                    errors.append(f"{path.relative_to(ROOT)}:{lineno} {error.json_path}: {error.message}")
        self.assertEqual(errors, [])

    def test_cost_event_examples_match_schemas(self) -> None:
        usage_validator = schema_validator(ROOT / "schemas" / "usage-event.schema.json")
        billing_validator = schema_validator(ROOT / "schemas" / "billing-event.schema.json")
        errors: list[str] = []
        for path, validator in ((ROOT / "examples" / "cost-governance-task" / "usage-events.jsonl", usage_validator), (ROOT / "examples" / "cost-governance-task" / "billing-events.jsonl", billing_validator)):
            if not path.exists():
                continue
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    errors.extend(f"{path.relative_to(ROOT)}:{lineno} {error.message}" for error in validator.iter_errors(json.loads(line)))
        self.assertEqual(errors, [])

    def test_bundled_agent_session_receipts_match_schema(self) -> None:
        validator = schema_validator(ROOT / "schemas" / "agent-session-receipt.schema.json")
        errors: list[str] = []
        for path in sorted((ROOT / "examples").rglob("agent-session-receipts.jsonl")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                data = json.loads(line)
                for error in validator.iter_errors(data):
                    errors.append(f"{path.relative_to(ROOT)}:{lineno} {error.json_path}: {error.message}")
        self.assertEqual(errors, [])

    def test_agent_session_schemas_accept_windows_absolute_paths(self) -> None:
        sessions = json.loads(
            (ROOT / "examples" / "agent-sessions.json").read_text(encoding="utf-8")
        )
        receipt = json.loads(
            (ROOT / "examples" / "agent-session-receipts.jsonl").read_text(
                encoding="utf-8"
            )
        )
        binding = sessions["bindings"]["example-agent"]
        binding["context"]["cwd"] = "C:\\workspace\\project"
        binding["launch"]["argv"][0] = "C:\\Tools\\codex.exe"
        receipt["context"]["cwd"] = "C:\\workspace\\project"
        receipt["launch"]["argv"][0] = "C:\\Tools\\codex.exe"

        session_errors = list(
            schema_validator(ROOT / "schemas" / "agent-sessions.schema.json").iter_errors(
                sessions
            )
        )
        receipt_errors = list(
            schema_validator(
                ROOT / "schemas" / "agent-session-receipt.schema.json"
            ).iter_errors(receipt)
        )

        self.assertEqual(session_errors, [])
        self.assertEqual(receipt_errors, [])

    def test_agent_session_schemas_accept_non_pane_runtime_identity(self) -> None:
        sessions = json.loads(
            (ROOT / "examples" / "agent-sessions.json").read_text(encoding="utf-8")
        )
        receipt = json.loads(
            (ROOT / "examples" / "agent-session-receipts.jsonl").read_text(
                encoding="utf-8"
            )
        )
        binding = sessions["bindings"].pop("example-agent")
        sessions["adapter"] = "example-thread-runtime"
        receipt["adapter"] = "example-thread-runtime"
        binding["agent"] = "build-agent"
        binding["session_name"] = "thread-session-1"
        binding["context"] = {"project_ref": "project://example"}
        binding["launch"] = {"runtime_ref": "agent://build-agent"}
        binding.pop("focused_at_provisioning")
        binding["runtime_scope"] = {
            "kind": "thread",
            "ownership": "task",
            "thread_id": "thread-1",
        }
        binding["runtime_identity"] = {
            "thread_id": "thread-1",
            "token": "sha256:" + ("1" * 64),
        }
        sessions["bindings"] = {"build-agent": binding}

        receipt["agent"] = "build-agent"
        receipt["context"] = binding["context"]
        receipt["launch"] = binding["launch"]
        receipt.pop("focused_at_provisioning")
        receipt["runtime_scope"] = binding["runtime_scope"]
        receipt["runtime_identity"] = binding["runtime_identity"]
        receipt["identity_token"] = binding["runtime_identity"]["token"]

        session_errors = list(
            schema_validator(ROOT / "schemas" / "agent-sessions.schema.json").iter_errors(
                sessions
            )
        )
        receipt_errors = list(
            schema_validator(
                ROOT / "schemas" / "agent-session-receipt.schema.json"
            ).iter_errors(receipt)
        )

        self.assertEqual(session_errors, [])
        self.assertEqual(receipt_errors, [])

    def test_agent_session_schemas_reject_focused_provisioning(self) -> None:
        sessions = json.loads(
            (ROOT / "examples" / "agent-sessions.json").read_text(encoding="utf-8")
        )
        receipt = json.loads(
            (ROOT / "examples" / "agent-session-receipts.jsonl").read_text(
                encoding="utf-8"
            )
        )
        sessions["bindings"]["example-agent"]["focused_at_provisioning"] = True
        receipt["focused_at_provisioning"] = True

        session_errors = list(
            schema_validator(ROOT / "schemas" / "agent-sessions.schema.json").iter_errors(
                sessions
            )
        )
        receipt_errors = list(
            schema_validator(
                ROOT / "schemas" / "agent-session-receipt.schema.json"
            ).iter_errors(receipt)
        )

        self.assertTrue(session_errors)
        self.assertTrue(receipt_errors)

    def test_ready_agent_session_projection_requires_a_binding(self) -> None:
        sessions = json.loads(
            (ROOT / "examples" / "agent-sessions.json").read_text(encoding="utf-8")
        )
        sessions["bindings"] = {}

        errors = list(
            schema_validator(ROOT / "schemas" / "agent-sessions.schema.json").iter_errors(
                sessions
            )
        )

        self.assertTrue(errors)

    def test_bundled_wait_event_jsonl_examples_match_schema(self) -> None:
        validator = schema_validator(ROOT / "schemas" / "wait-event.schema.json")
        errors: list[str] = []
        for path in sorted((ROOT / "examples").rglob("wait-events.jsonl")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                data = json.loads(line)
                for error in validator.iter_errors(data):
                    errors.append(f"{path.relative_to(ROOT)}:{lineno} {error.json_path}: {error.message}")
        self.assertEqual(errors, [])

    def test_invalid_wait_wake_fixtures_are_rejected(self) -> None:
        fixture_dir = ROOT / "tests" / "fixtures" / "wait-wake" / "invalid"
        schema_by_fixture = {
            "wait-policy-any-terminal.json": "wait-policy.schema.json",
            "deterministic-receipt-missing-epoch.json": "receipts.schema.json",
            "deterministic-receipt-missing-proof.json": "receipts.schema.json",
            "exception-wake-user-input-runtime-principal.json": "exception-wake.schema.json",
            "exception-wake-drive-qualified-ref.json": "exception-wake.schema.json",
            "wake-result-extra-field.json": "wake-result.schema.json",
        }
        for fixture_name, schema_name in schema_by_fixture.items():
            with self.subTest(fixture=fixture_name):
                fixture = json.loads((fixture_dir / fixture_name).read_text(encoding="utf-8"))
                errors = list(schema_validator(ROOT / "schemas" / schema_name).iter_errors(fixture))
                self.assertTrue(errors, f"{fixture_name} unexpectedly matched {schema_name}")

    def test_v2_state_status_is_a_closed_transition_vocabulary(self) -> None:
        state = json.loads((ROOT / "examples" / "full-mode-task" / "state.json").read_text(encoding="utf-8"))
        state["schema_version"] = "valp-visible-loop-state.v2"
        state["revision"] = 0
        state["status"] = "invented_state"
        errors = list(schema_validator(ROOT / "schemas" / "state.schema.json").iter_errors(state))
        self.assertTrue(errors)

    def test_wait_wake_spec_and_quickstart_match_the_shipped_cli_boundary(self) -> None:
        spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
        section = spec.split("#### 4.1.1 Suspended Waiting And Deterministic Resume", 1)[1].split(
            "### 4.2 Trigger Policy And Auto Visible Mode",
            1,
        )[0]
        self.assertIn("checkpoint_ref (optional opaque task-local ref)", section)
        self.assertIn("MUST omit `checkpoint_ref`", section)
        self.assertIn(
            "receipt | timeout | runtime_failure | cancellation | user_input",
            section,
        )
        self.assertNotIn(
            "dependency_ready | timeout | runtime_failure | cancellation | user_input",
            section,
        )

        quickstart = (ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
        policy_position = quickstart.find("wait-policy.json")
        wait_position = quickstart.find("bin/valp wait TASK-001")
        self.assertGreaterEqual(policy_position, 0)
        self.assertLess(policy_position, wait_position)
        self.assertIn(
            "--event user_input --ref evidence/wake-requests/user-input.json",
            quickstart.replace("\\\n", " ").replace("\n", " "),
        )
        normalized_quickstart = " ".join(quickstart.split())
        self.assertIn("final qualifying dependency-ready barrier receipt", normalized_quickstart)
        self.assertIn("exception short circuit", normalized_quickstart)

    def test_spec_scopes_exactly_once_to_wake_transition_and_adapter_evidence(self) -> None:
        spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
        section = spec.split("#### 4.1.1 Suspended Waiting And Deterministic Resume", 1)[1].split(
            "### 4.2 Trigger Policy And Auto Visible Mode",
            1,
        )[0]
        normalized_section = " ".join(section.split())

        self.assertIn("duplicate wake transition", section)
        self.assertIn("wake-ID-bound continuation invocation receipt", section)
        self.assertIn("restart/restore evidence", section)
        self.assertIn("MUST downgrade", section)
        self.assertIn("event-to-projection recovery", normalized_section)
        self.assertNotIn("or coordinator continuation", section)
        self.assertNotIn("coordinator restart replay", section)

        checkpoint = section.split("A runtime adapter MAY record `checkpoint_ref`", 1)[1].split(
            "For each strict epoch",
            1,
        )[0]
        self.assertIn("opaque", checkpoint)
        self.assertIn("safe, existing, and non-empty", checkpoint)
        self.assertIn("does not prove", checkpoint)
        self.assertNotIn("durable continuation checkpoint", checkpoint)

    def test_nip_matrix_keeps_whole_state_layer_at_i1_and_labels_the_i2_slice(self) -> None:
        matrix = (ROOT / "docs" / "twelve-layer-nip-matrix.md").read_text(encoding="utf-8")

        self.assertIn("| 4 | State | 2 | 1 | 1 |", matrix)
        tracer = matrix.split("## Deterministic-Wake Tracer Bullet", 1)[1]
        normalized_tracer = " ".join(tracer.split())
        self.assertIn("I2 tracer bullet", tracer)
        self.assertIn("does not raise the whole State layer above I1", normalized_tracer)

    def test_public_status_marks_v030_as_released_without_broad_runtime_claims(self) -> None:
        for relative_path in (
            "README.md",
            "README.zh-CN.md",
            "docs/index.md",
            "docs/project-status.md",
            "docs/v0.3-implementation.md",
            "docs/versioning-and-compatibility.md",
            "docs/zh-CN/README.md",
        ):
            with self.subTest(path=relative_path):
                document = (ROOT / relative_path).read_text(encoding="utf-8")
                normalized = " ".join(document.split())
                self.assertIn("`0.3.0`", normalized)
                self.assertTrue(
                    "published" in normalized.casefold()
                    or "正式发布" in normalized
                    or "已发布" in normalized
                )
                self.assertNotIn("release gates remain open", normalized.casefold())
                self.assertTrue(
                    "production" in normalized.casefold() or "生产" in normalized
                )

    def test_remote_mode_public_claims_are_conditional_on_adapter_evidence(self) -> None:
        for relative_path in (
            "README.md",
            "docs/quickstart.md",
            "docs/platform-support.md",
            "docs/runtime.md",
            "docs/faq.md",
            "docs/runtime-adapters.md",
        ):
            with self.subTest(path=relative_path):
                document = (ROOT / relative_path).read_text(encoding="utf-8")
                normalized = " ".join(document.split())
                self.assertNotIn("Full Mode guarantees live on the remote host", normalized)
                self.assertNotIn("Full Mode guarantees on remote host", normalized)
                self.assertIn("conditional on adapter evidence", normalized)

    def test_runtime_docs_surface_the_cross_adapter_wait_contract(self) -> None:
        document = (ROOT / "docs" / "runtime-adapters.md").read_text(encoding="utf-8")
        heading = "## Cross-Adapter Suspended-Wait Contract"
        self.assertIn(heading, document)
        self.assertLess(document.index(heading), document.index("## Daemon Queue Adapter"))
        section = document.split(heading, 1)[1].split("## ", 1)[0]
        normalized = " ".join(section.split())
        for phrase in (
            "versioned wait policy",
            "identity-bound receipts",
            "dependency_ready",
            "immutable wake result",
            "event-to-projection recovery",
            "wake-ID-bound continuation invocation receipt",
            "restart/restore evidence",
            "downgrade",
        ):
            self.assertIn(phrase, normalized)

    def test_checkpoint_and_projection_docs_do_not_claim_coordinator_restore(self) -> None:
        task_state = (ROOT / "docs" / "task-state-machine.md").read_text(encoding="utf-8")
        schema_versions = (ROOT / "docs" / "schema-versioning.md").read_text(encoding="utf-8")
        combined = " ".join((task_state + "\n" + schema_versions).split())

        self.assertNotIn("durable continuation checkpoint", combined)
        self.assertIn("opaque", combined)
        self.assertIn("does not prove coordinator restorability", combined)
        self.assertIn("event-to-projection recovery", task_state)
        self.assertNotIn("coordinator restart replay", combined)

    def test_public_audit_examples_do_not_freeze_volatile_skip_totals(self) -> None:
        for relative_path in (
            "README.md",
            "README.zh-CN.md",
            "docs/quickstart.md",
            "docs/minimal-audit-demo.md",
            "docs/zh-CN/README.md",
            "docs/cli-audit.md",
            "docs/when-agent-done-is-not-done.md",
            "docs/assets/valp-audit-demo.svg",
        ):
            with self.subTest(path=relative_path):
                document = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertNotRegex(document, r"skip=\d+")

    def test_dispatch_receipt_docs_show_concrete_v2_submission_identity(self) -> None:
        document = (ROOT / "docs" / "dispatch-receipts.md").read_text(encoding="utf-8")
        self.assertIn("Legacy/non-deterministic receipt example", document)
        records = [
            json.loads(block)
            for block in re.findall(r"```json\n(.*?)\n```", document, re.DOTALL)
        ]
        submitted = next(
            record
            for record in records
            if record.get("schema_version") == "valp-dispatch-receipt.v2"
            and record.get("event") == "dispatch_submitted"
        )
        completed = next(
            record
            for record in records
            if record.get("schema_version") == "valp-dispatch-receipt.v2"
            and record.get("event") == "dispatch_completed"
        )
        self.assertTrue(submitted["proof"]["adapter_record"]["submission_id"])
        validator = schema_validator(ROOT / "schemas" / "receipts.schema.json")
        self.assertEqual(list(validator.iter_errors(submitted)), [])
        self.assertEqual(list(validator.iter_errors(completed)), [])
        for field in (
            "task_id",
            "agent",
            "role",
            "work_item_id",
            "dispatch_id",
            "dispatch_generation",
        ):
            self.assertEqual(submitted[field], completed[field])


if __name__ == "__main__":
    unittest.main()
