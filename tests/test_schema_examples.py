from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

from tests.schema_helpers import schema_validator


ROOT = Path(__file__).resolve().parents[1]

EXAMPLE_SCHEMA_BY_NAME = {
    "attention-map.json": "attention-map.schema.json",
    "automation-policy.json": "automation-policy.schema.json",
    "agent-recommendations.json": "agent-recommendations.schema.json",
    "context-pack.json": "context-pack.schema.json",
    "context-selection.json": "context-selection.schema.json",
    "correction-cycle.json": "correction-cycle.schema.json",
    "delegation-policy.json": "delegation-policy.schema.json",
    "evidence-board.json": "evidence-board.schema.json",
    "evidence-status.json": "evidence-status.schema.json",
    "exception-wake.json": "exception-wake.schema.json",
    "local-overlay.json": "local-overlay.schema.json",
    "mask-list.json": "mask-list.schema.json",
    "routing-feedback.json": "routing-feedback.schema.json",
    "learning-feedback.json": "learning-feedback.schema.json",
    "historical-audit-boundary.json": "historical-audit-boundary.schema.json",
    "routing.json": "routing.schema.json",
    "skill-recommendations.json": "skill-recommendations.schema.json",
    "state.json": "state.schema.json",
    "submission-dependencies.json": "submission-dependencies.schema.json",
    "trigger-policy.json": "trigger-policy.schema.json",
    "wait-policy.json": "wait-policy.schema.json",
    "wake-result.json": "wake-result.schema.json",
}


class SchemaExampleTests(unittest.TestCase):
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
            data = json.loads(path.read_text(encoding="utf-8"))
            for error in validators[schema_name].iter_errors(data):
                errors.append(f"{path.relative_to(ROOT)} {error.json_path}: {error.message}")
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
            "wake-result-extra-field.json": "wake-result.schema.json",
        }
        for fixture_name, schema_name in schema_by_fixture.items():
            with self.subTest(fixture=fixture_name):
                fixture = json.loads((fixture_dir / fixture_name).read_text(encoding="utf-8"))
                errors = list(schema_validator(ROOT / "schemas" / schema_name).iter_errors(fixture))
                self.assertTrue(errors, f"{fixture_name} unexpectedly matched {schema_name}")

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
        self.assertIn("final qualifying dependency-ready barrier receipt", quickstart)
        self.assertIn("exception short circuit", quickstart)

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

    def test_public_status_marks_rfc_incomplete_and_names_the_local_wake_subset(self) -> None:
        for relative_path in ("README.md", "docs/index.md", "docs/project-status.md"):
            with self.subTest(path=relative_path):
                document = (ROOT / relative_path).read_text(encoding="utf-8")
                normalized = " ".join(document.split())
                self.assertIn("RFC 0001 remains incomplete", normalized)
                self.assertIn("deterministic-wake subset is locally implemented", normalized)
                self.assertIn("release remains `0.2.0`", normalized)
                self.assertNotIn("stable `0.3.0` release", normalized)

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
