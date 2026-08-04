from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from valp_cli.protocol_receipts import (
    ApprovalBinding,
    EMPTY_RECEIPT_LEDGER_DIGEST,
    IDEMPOTENCY_CONFLICT_ERROR,
    MIGRATION_UNSUPPORTED_ERROR,
    ProofBinding,
    ReceiptDraft,
    ReceiptLedger,
    ReceiptMode,
    ReceiptProofKind,
    ReceiptWriteVariant,
    STATE_CONFLICT_ERROR,
    digest,
    migrate_receipt,
    propose_receipt_append,
    receipt_subject_digest,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "receipt-v3"
GOOD_DIGEST = "sha256:" + "1" * 64


class ProtocolReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = ReceiptLedger(
            installation_id="installation-1",
            leader_epoch=3,
            task_id="TASK-RECEIPT-V3",
        )

    def validator(self) -> Draft202012Validator:
        schema = json.loads((ROOT / "schemas" / "receipts.schema.json").read_text())
        return Draft202012Validator(schema)

    def draft(
        self,
        *,
        receipt_id: str = "receipt-submit-1",
        event: str = "dispatch_submitted",
        mode: ReceiptMode | str = ReceiptMode.FULL,
        sequence: int = 1,
        revision: int = 0,
        prior_digest: str = EMPTY_RECEIPT_LEDGER_DIGEST,
        payload_digest: str = GOOD_DIGEST,
        suspension_epoch: int | None = None,
        attempt_id: str = "attempt-1",
        dispatch_id: str = "TASK-RECEIPT-V3:implementer:1",
        agent: str = "codex",
        role: str = "implementer",
        expected_refs: tuple[str, ...] = ("agents/codex/evidence.md",),
    ) -> ReceiptDraft:
        draft = ReceiptDraft(
            receipt_id=receipt_id,
            installation_id="installation-1",
            leader_epoch=3,
            task_id="TASK-RECEIPT-V3",
            agent=agent,
            role=role,
            work_item_id="implementer:codex",
            attempt_id=attempt_id,
            dispatch_id=dispatch_id,
            dispatch_generation=1,
            mode=mode,
            event_sequence=sequence,
            expected_revision=revision,
            prior_receipt_digest=prior_digest,
            event=event,
            ts="2026-08-03T08:00:00Z",
            dispatch_ref="agents/codex/dispatch.md",
            payload_digest=payload_digest,
            expected_refs=expected_refs,
            proof_bindings=(),
            approval_binding=ApprovalBinding("not_required", GOOD_DIGEST),
            suspension_epoch=suspension_epoch,
        )
        subject = receipt_subject_digest(draft)
        kinds = (
            (ReceiptProofKind.MANUAL_ATTESTED,)
            if mode == ReceiptMode.MANUAL
            else (ReceiptProofKind.PROCESS_BOUND, ReceiptProofKind.CONTENT_BOUND)
        )
        return replace(
            draft,
            proof_bindings=tuple(
                ProofBinding(
                    kind,
                    f"evidence/{kind.value}.json",
                    "sha256:" + str(index) * 64,
                    subject,
                )
                for index, kind in enumerate(kinds, 2)
            ),
        )

    def test_v3_receipt_schema_is_closed_and_runtime_aligned(self) -> None:
        draft = self.draft()
        result = propose_receipt_append(self.ledger, draft)
        self.assertEqual(result.variant, ReceiptWriteVariant.ACCEPTED)
        self.validator().validate(result.accepted.receipt.canonical())
        reordered = propose_receipt_append(
            self.ledger,
            replace(draft, proof_bindings=tuple(reversed(draft.proof_bindings))),
        )
        self.assertEqual(
            reordered.accepted.receipt.canonical(),
            result.accepted.receipt.canonical(),
        )
        invalid = dict(result.accepted.receipt.canonical())
        invalid["future_safety_field"] = True
        self.assertFalse(self.validator().is_valid(invalid))

    def test_write_accepts_once_and_identical_duplicate_is_noop(self) -> None:
        draft = self.draft()
        accepted = propose_receipt_append(self.ledger, draft)
        duplicate = propose_receipt_append(accepted.accepted.ledger, draft)
        self.assertEqual(accepted.variant, ReceiptWriteVariant.ACCEPTED)
        self.assertEqual(accepted.accepted.ledger.revision, 1)
        self.assertEqual(len(accepted.accepted.obligations), 1)
        self.assertEqual(duplicate.variant, ReceiptWriteVariant.NO_OP)
        self.assertIs(duplicate.no_op.ledger, accepted.accepted.ledger)
        self.assertEqual(duplicate.no_op.prior_receipt, accepted.accepted.receipt)

    def test_same_id_with_changed_content_is_conflict(self) -> None:
        accepted = propose_receipt_append(self.ledger, self.draft())
        changed = self.draft(payload_digest="sha256:" + "2" * 64)
        result = propose_receipt_append(accepted.accepted.ledger, changed)
        self.assertEqual(result.variant, ReceiptWriteVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, IDEMPOTENCY_CONFLICT_ERROR)
        self.assertIs(result.rejected.ledger, accepted.accepted.ledger)

    def test_revision_cas_selects_one_sequence_winner(self) -> None:
        accepted = propose_receipt_append(self.ledger, self.draft())
        stale = self.draft(
            receipt_id="receipt-race-2",
            attempt_id="attempt-2",
            dispatch_id="TASK-RECEIPT-V3:implementer:2",
        )
        result = propose_receipt_append(accepted.accepted.ledger, stale)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)
        self.assertEqual(result.rejected.ledger.revision, 1)

    def test_sequence_rejects_bool_gap_and_prior_digest_mismatch(self) -> None:
        for changed in (
            self.draft(sequence=True),
            self.draft(sequence=2),
            self.draft(prior_digest=GOOD_DIGEST),
        ):
            with self.subTest(draft=changed):
                result = propose_receipt_append(self.ledger, changed)
                self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_different_id_for_same_logical_receipt_is_conflict(self) -> None:
        accepted = propose_receipt_append(self.ledger, self.draft())
        duplicate_key = self.draft(
            receipt_id="receipt-alias",
            sequence=2,
            revision=1,
            prior_digest=accepted.accepted.receipt.receipt_digest,
        )
        result = propose_receipt_append(accepted.accepted.ledger, duplicate_key)
        self.assertEqual(result.rejected.error_code, IDEMPOTENCY_CONFLICT_ERROR)

    def test_completion_requires_matching_prior_submission(self) -> None:
        completion = self.draft(
            receipt_id="receipt-complete-2",
            event="dispatch_completed",
            suspension_epoch=1,
        )
        self.assertEqual(
            propose_receipt_append(self.ledger, completion).rejected.error_code,
            STATE_CONFLICT_ERROR,
        )
        submitted = propose_receipt_append(self.ledger, self.draft())
        completion = self.draft(
            receipt_id="receipt-complete-2",
            event="dispatch_completed",
            sequence=2,
            revision=1,
            prior_digest=submitted.accepted.receipt.receipt_digest,
            suspension_epoch=1,
        )
        completed = propose_receipt_append(submitted.accepted.ledger, completion)
        self.assertEqual(completed.variant, ReceiptWriteVariant.ACCEPTED)
        self.validator().validate(completed.accepted.receipt.canonical())

    def test_completion_cannot_borrow_submission_identity_or_payload(self) -> None:
        submitted = propose_receipt_append(self.ledger, self.draft())
        base = dict(
            receipt_id="receipt-complete-2",
            event="dispatch_completed",
            sequence=2,
            revision=1,
            prior_digest=submitted.accepted.receipt.receipt_digest,
            suspension_epoch=1,
        )
        variants = (
            self.draft(**base, agent="mallory"),
            self.draft(**base, role="reviewer"),
            self.draft(**base, payload_digest="sha256:" + "2" * 64),
            self.draft(**base, expected_refs=("agents/codex/other.md",)),
        )
        for completion in variants:
            with self.subTest(completion=completion):
                result = propose_receipt_append(submitted.accepted.ledger, completion)
                self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_subject_digest_binds_sequence_revision_and_suspension_epoch(self) -> None:
        first = self.draft(event="dispatch_completed", suspension_epoch=1)
        changed_sequence = self.draft(
            event="dispatch_completed", sequence=2, revision=1, suspension_epoch=1
        )
        changed_epoch = self.draft(event="dispatch_completed", suspension_epoch=2)
        self.assertNotEqual(receipt_subject_digest(first), receipt_subject_digest(changed_sequence))
        self.assertNotEqual(receipt_subject_digest(first), receipt_subject_digest(changed_epoch))

    def test_schema_enforces_mode_event_proof_and_terminal_epoch(self) -> None:
        valid = propose_receipt_append(self.ledger, self.draft()).accepted.receipt.canonical()
        manual_event = dict(valid)
        manual_event["event"] = "manual_delivery_attested"
        transport_only = json.loads(json.dumps(valid))
        transport_only["proof_bindings"] = [
            {
                "proof_kind": "transport_only",
                "proof_ref": "evidence/transport.json",
                "proof_digest": GOOD_DIGEST,
                "subject_digest": GOOD_DIGEST,
            }
        ]
        terminal_without_epoch = dict(valid)
        terminal_without_epoch["event"] = "dispatch_completed"
        for invalid in (manual_event, transport_only, terminal_without_epoch):
            with self.subTest(invalid=invalid):
                self.assertFalse(self.validator().is_valid(invalid))

    def test_manual_attestation_cannot_be_relabelled_as_full_submission(self) -> None:
        manual = self.draft(
            receipt_id="receipt-manual-1",
            event="manual_delivery_attested",
            mode=ReceiptMode.MANUAL,
        )
        self.assertEqual(
            propose_receipt_append(self.ledger, manual).variant,
            ReceiptWriteVariant.ACCEPTED,
        )
        relabelled = replace(manual, mode=ReceiptMode.FULL)
        self.assertEqual(
            propose_receipt_append(self.ledger, relabelled).rejected.error_code,
            STATE_CONFLICT_ERROR,
        )

    def test_process_and_content_proofs_require_distinct_refs(self) -> None:
        draft = self.draft()
        process, content = draft.proof_bindings
        relabelled = replace(
            draft,
            proof_bindings=(process, replace(content, proof_ref=process.proof_ref)),
        )

        result = propose_receipt_append(self.ledger, relabelled)
        self.assertEqual(result.variant, ReceiptWriteVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_process_and_content_proofs_require_distinct_digests(self) -> None:
        draft = self.draft()
        process, content = draft.proof_bindings
        relabelled = replace(
            draft,
            proof_bindings=(process, replace(content, proof_digest=process.proof_digest)),
        )

        result = propose_receipt_append(self.ledger, relabelled)
        self.assertEqual(result.variant, ReceiptWriteVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_unknown_schema_semantics_fail_migration_closed(self) -> None:
        unknown_mode = self.draft(mode="future")
        self.assertEqual(
            propose_receipt_append(self.ledger, unknown_mode).rejected.error_code,
            MIGRATION_UNSUPPORTED_ERROR,
        )
        source = (FIXTURES / "invalid" / "unsupported-v4.json").read_bytes()
        migrated = migrate_receipt(
            source, self.ledger, self.draft(), GOOD_DIGEST, "migration-1"
        )
        self.assertEqual(migrated.write_result.rejected.error_code, MIGRATION_UNSUPPORTED_ERROR)

    def test_legacy_and_v2_migration_preserve_exact_source_bytes(self) -> None:
        for name in ("legacy.json", "v2.json"):
            with self.subTest(name=name):
                source = (FIXTURES / "valid" / name).read_bytes()
                draft = self.draft()
                if name == "legacy.json":
                    draft = self.draft(event="dispatch_written")
                else:
                    draft = self.draft(receipt_id="receipt-v2-source")
                result = migrate_receipt(
                    source, self.ledger, draft, GOOD_DIGEST, "migration-1"
                )
                self.assertEqual(result.source_bytes, source)
                self.assertEqual(result.source_digest, digest(source))
                self.assertEqual(result.write_result.variant, ReceiptWriteVariant.ACCEPTED)
                receipt = result.write_result.accepted.receipt
                self.assertEqual(receipt.draft.migration_binding.source_receipt_digest, digest(source))
                self.validator().validate(receipt.canonical())

    def test_malformed_migration_is_rejected_without_changing_source(self) -> None:
        source = (FIXTURES / "invalid" / "malformed.jsonl").read_bytes()
        result = migrate_receipt(
            source, self.ledger, self.draft(), GOOD_DIGEST, "migration-1"
        )
        self.assertEqual(result.source_bytes, source)
        self.assertEqual(result.write_result.rejected.error_code, MIGRATION_UNSUPPORTED_ERROR)
        self.assertIs(result.write_result.rejected.ledger, self.ledger)

    def test_changed_migration_source_conflicts_with_same_receipt_id(self) -> None:
        source = (FIXTURES / "valid" / "legacy.json").read_bytes()
        draft = self.draft(event="dispatch_written")
        first = migrate_receipt(
            source, self.ledger, draft, GOOD_DIGEST, "migration-1"
        )
        changed_source = source + b"\n"
        second = migrate_receipt(
            changed_source,
            first.write_result.accepted.ledger,
            draft,
            GOOD_DIGEST,
            "migration-1",
        )
        self.assertEqual(second.write_result.rejected.error_code, IDEMPOTENCY_CONFLICT_ERROR)

    def test_migration_retry_is_noop_and_changed_reconciliation_conflicts(self) -> None:
        boundary = json.loads(
            (FIXTURES / "migration" / "duplicate-boundary.json").read_text(
                encoding="utf-8"
            )
        )
        source = (FIXTURES / "valid" / "legacy.json").read_bytes()
        draft = self.draft(event="dispatch_written")
        first = migrate_receipt(source, self.ledger, draft, GOOD_DIGEST, "migration-1")
        repeated = migrate_receipt(
            source, first.write_result.accepted.ledger, draft, GOOD_DIGEST, "migration-1"
        )
        changed = migrate_receipt(
            source,
            first.write_result.accepted.ledger,
            draft,
            "sha256:" + "2" * 64,
            "migration-1",
        )
        self.assertEqual(
            repeated.write_result.variant.value,
            boundary["same_migration_id_same_source_and_bindings"],
        )
        self.assertEqual(
            changed.write_result.rejected.error_code,
            boundary["same_migration_id_changed_source_or_bindings"],
        )
        self.assertEqual(boundary["source_bytes"], "preserved")
        self.assertEqual(first.source_bytes, source)
        self.assertEqual(repeated.source_bytes, source)
        self.assertEqual(changed.source_bytes, source)
        self.assertEqual(boundary["runtime_adoption"], "not_claimed")
        self.assertFalse(hasattr(first, "runtime_adoption"))

    def test_migration_rejects_invalid_or_conflicting_source_semantics(self) -> None:
        cases = (
            ("legacy-unknown-event.json", self.draft(event="dispatch_written")),
            ("v2-missing-proof.json", self.draft()),
            (
                "v2-terminal-missing-epoch.json",
                self.draft(event="dispatch_completed", suspension_epoch=1),
            ),
            ("v2.json", self.draft(receipt_id="receipt-v2-source", agent="mallory")),
            ("v2.json", self.draft(receipt_id="receipt-v2-source", role="reviewer")),
            (
                "v2.json",
                self.draft(receipt_id="receipt-v2-source", event="dispatch_written"),
            ),
            ("v2.json", self.draft(receipt_id="different-source-identity")),
        )
        for name, draft in cases:
            with self.subTest(name=name, draft=draft):
                source = (FIXTURES / ("valid" if name == "v2.json" else "invalid") / name).read_bytes()
                result = migrate_receipt(
                    source, self.ledger, draft, GOOD_DIGEST, "migration-1"
                )
                self.assertEqual(
                    result.write_result.rejected.error_code,
                    MIGRATION_UNSUPPORTED_ERROR,
                )
                self.assertEqual(result.source_bytes, source)

    def test_explicit_legacy_and_v2_migration_fixtures_bind_existing_fields(self) -> None:
        for fixture_name in ("legacy-to-v3.json", "v2-to-v3.json"):
            with self.subTest(fixture=fixture_name):
                fixture = json.loads(
                    (FIXTURES / "migration" / fixture_name).read_text(encoding="utf-8")
                )
                source_path = FIXTURES / fixture["source_ref"]
                source_bytes = source_path.read_bytes()
                source = json.loads(source_bytes)
                draft = self.draft(**fixture["draft"])

                result = migrate_receipt(
                    source_bytes,
                    self.ledger,
                    draft,
                    GOOD_DIGEST,
                    fixture["migration_id"],
                )

                self.assertEqual(result.write_result.variant, ReceiptWriteVariant.ACCEPTED)
                self.assertEqual(result.source_bytes, source_bytes)
                receipt = result.write_result.accepted.receipt
                self.assertEqual(
                    receipt.draft.migration_binding.source_schema_version,
                    fixture["source_schema_version"],
                )
                self.assertEqual(
                    receipt.draft.migration_binding.source_receipt_digest,
                    digest(source_bytes),
                )
                projected = receipt.canonical()
                for field in fixture["preserved_fields"]:
                    self.assertEqual(projected[field], source[field], field)

    def test_migration_rejects_unknown_legacy_safety_fields_and_boolean_v2_ints(self) -> None:
        cases = (
            "legacy-unknown-safety-field.json",
            "v2-bool-sequence.json",
            "v2-bool-generation.json",
        )
        for name in cases:
            with self.subTest(name=name):
                source = (FIXTURES / "invalid" / name).read_bytes()
                event = "dispatch_written" if name.startswith("legacy") else "dispatch_submitted"
                result = migrate_receipt(
                    source,
                    self.ledger,
                    self.draft(event=event),
                    GOOD_DIGEST,
                    "migration-invalid",
                )
                self.assertEqual(
                    result.write_result.rejected.error_code,
                    MIGRATION_UNSUPPORTED_ERROR,
                )
                self.assertEqual(result.source_bytes, source)

    def test_v2_matching_identity_weak_proof_cannot_be_laundered_into_v3(self) -> None:
        cases = (
            "v2-weak-proof-dry-run.json",
            "v2-weak-proof-simulation.json",
            "v2-weak-proof-empty-adapter-record.json",
            "v2-weak-proof-accepted-bool.json",
        )
        for name in cases:
            with self.subTest(name=name):
                source = (FIXTURES / "invalid" / name).read_bytes()
                source_receipt = json.loads(source)
                self.assertEqual(source_receipt["receipt_id"], self.draft().receipt_id)

                result = migrate_receipt(
                    source,
                    self.ledger,
                    self.draft(),
                    GOOD_DIGEST,
                    f"migration-{name}",
                )

                self.assertEqual(
                    result.write_result.rejected.error_code,
                    MIGRATION_UNSUPPORTED_ERROR,
                )
                self.assertEqual(result.source_bytes, source)
                self.assertIs(result.write_result.rejected.ledger, self.ledger)

    def test_v2_submission_proof_is_closed_typed_and_content_bound(self) -> None:
        positive_source = (FIXTURES / "valid" / "v2.json").read_bytes()
        positive = migrate_receipt(
            positive_source,
            self.ledger,
            self.draft(receipt_id="receipt-v2-source"),
            GOOD_DIGEST,
            "migration-v2-positive",
        )
        self.assertEqual(positive.write_result.variant, ReceiptWriteVariant.ACCEPTED)

        cases = (
            "v2-proof-bypass-dry-run-camel.json",
            "v2-proof-bypass-sim-id.json",
            "v2-proof-bypass-generic-id.json",
            "v2-proof-payload-mismatch.json",
            "v2-proof-ack-false.json",
            "v2-proof-missing-identity.json",
            "v2-proof-extra-field.json",
        )
        for name in cases:
            with self.subTest(name=name):
                source = (FIXTURES / "invalid" / name).read_bytes()
                source_receipt = json.loads(source)
                self.assertEqual(source_receipt["receipt_id"], self.draft().receipt_id)

                result = migrate_receipt(
                    source,
                    self.ledger,
                    self.draft(),
                    GOOD_DIGEST,
                    f"migration-{name}",
                )

                self.assertEqual(
                    result.write_result.rejected.error_code,
                    MIGRATION_UNSUPPORTED_ERROR,
                )
                self.assertEqual(result.source_bytes, source)
                self.assertIs(result.write_result.rejected.ledger, self.ledger)

    def test_known_semantics_duplicate_precedes_structural_conflict(self) -> None:
        draft = self.draft()
        accepted = propose_receipt_append(self.ledger, draft)
        bad_subject = replace(
            draft,
            proof_bindings=tuple(
                replace(binding, subject_digest=GOOD_DIGEST)
                for binding in draft.proof_bindings
            ),
        )

        duplicate = propose_receipt_append(accepted.accepted.ledger, bad_subject)
        new_identity = propose_receipt_append(
            self.ledger,
            replace(bad_subject, receipt_id="receipt-new-invalid-subject"),
        )

        self.assertEqual(duplicate.rejected.error_code, IDEMPOTENCY_CONFLICT_ERROR)
        self.assertEqual(new_identity.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_proof_approval_and_digest_tampering_fail_closed(self) -> None:
        draft = self.draft()
        subject = receipt_subject_digest(draft)
        granted = replace(
            draft,
            approval_binding=ApprovalBinding(
                "granted",
                GOOD_DIGEST,
                approval_id="approval-1",
                approval_ref="evidence/approval.json",
                approval_digest=GOOD_DIGEST,
                action_digest=subject,
            ),
        )
        cases = (
            replace(
                draft,
                proof_bindings=tuple(
                    replace(binding, subject_digest=GOOD_DIGEST)
                    for binding in draft.proof_bindings
                ),
            ),
            replace(
                draft,
                proof_bindings=(
                    replace(draft.proof_bindings[0], proof_digest="sha256:" + "a" * 63),
                    *draft.proof_bindings[1:],
                ),
            ),
            replace(draft, payload_digest="sha256:" + "A" * 64),
            replace(
                granted,
                approval_binding=replace(
                    granted.approval_binding,
                    action_digest="sha256:" + "2" * 64,
                ),
            ),
        )
        for changed in cases:
            with self.subTest(changed=changed):
                result = propose_receipt_append(self.ledger, changed)
                self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_all_integer_safety_fields_reject_booleans(self) -> None:
        cases = (
            replace(self.draft(), leader_epoch=True),
            replace(self.draft(), dispatch_generation=True),
            replace(self.draft(), event_sequence=True),
            replace(self.draft(), expected_revision=True),
            replace(self.draft(), retry_generation=True),
            replace(
                self.draft(event="dispatch_completed", suspension_epoch=1),
                suspension_epoch=True,
            ),
        )
        for changed in cases:
            with self.subTest(changed=changed):
                result = propose_receipt_append(self.ledger, changed)
                self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

        forged_ledger = replace(self.ledger, revision=True)
        result = propose_receipt_append(forged_ledger, self.draft())
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)


if __name__ == "__main__":
    unittest.main()
