from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tests.schema_helpers import schema_validator
from valp_cli.doctor import DoctorCheck, DoctorReport
from valp_cli.protocol_receipts import digest
from valp_cli.remediation import (
    RemediationError,
    build_doctor_snapshot,
    build_repair_plan,
    build_recovery_approval,
    build_recovery_plan,
    collect_doctor_with_snapshot,
    execute_repair_plan,
    execute_recovery_plan,
    workspace_fingerprint,
    validate_plan_digest,
    verify_repair_receipt,
    write_json_atomic,
)


ROOT = Path(__file__).resolve().parents[1]
RECOVERY_EXECUTED_AT = "2026-08-26T10:00:02Z"


def report_with(
    *checks: DoctorCheck,
    passports: list[dict] | None = None,
) -> DoctorReport:
    pass_count = sum(check.status == "pass" for check in checks)
    warn_count = sum(check.status == "warn" for check in checks)
    fail_count = sum(check.status == "fail" for check in checks)
    return DoctorReport(
        workspace="example-workspace",
        generated_at="2026-08-26T10:00:00Z",
        status="fail" if fail_count else "warn" if warn_count else "pass",
        pass_count=pass_count,
        warn_count=warn_count,
        fail_count=fail_count,
        checks=list(checks),
        capability_passports=passports or [],
    )


def runtime_failure() -> DoctorCheck:
    return DoctorCheck(
        id="runtime_herdr",
        title="HERDR reference runtime is available",
        status="fail",
        message="runtime probe failed",
        evidence=["submission transport unavailable"],
        suggestion="Inspect runtime support.",
    )


def recovery_plan(root: Path) -> dict:
    return build_recovery_plan(
        report_with(runtime_failure()),
        root,
        resource_id="mcp-a",
        resource_version="g1",
        rollback_token="rollback-fixture-token",
        generated_at="2026-08-26T10:00:00Z",
    )


def recovery_approval(plan: dict, *, expires_at: str = "2026-08-26T10:10:00Z") -> dict:
    return build_recovery_approval(
        plan,
        approval_id="approval-fixture-1",
        approval_ref="approvals/fixture.json",
        approver_identity="user:fixture",
        approved_at="2026-08-26T00:00:01Z",
        expires_at=expires_at,
    )


class ValpRemediationTests(unittest.TestCase):
    def test_recovery_rejects_expired_approval_and_kill_switch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = recovery_plan(root)
            expired = recovery_approval(plan, expires_at="2026-08-26T10:00:02Z")
            with self.assertRaisesRegex(RemediationError, "expired"):
                execute_recovery_plan(plan, root, approval=expired, rollback_token="rollback-fixture-token", recovery_dir=root / "recovery", dry_run=True, executed_at="2026-08-26T10:00:02Z")
            recovery_dir = root / "recovery"
            recovery_dir.mkdir()
            (recovery_dir / ".recovery-disabled").touch()
            with self.assertRaisesRegex(RemediationError, "kill-switch"):
                execute_recovery_plan(plan, root, approval=recovery_approval(plan), rollback_token="rollback-fixture-token", recovery_dir=recovery_dir, dry_run=True, executed_at=RECOVERY_EXECUTED_AT)

    def test_dry_run_does_not_consume_plan_and_failed_verification_rolls_back(self) -> None:
        class Provider:
            def restart(self, **_kwargs: object) -> dict:
                return {"status": "restarted"}
            def rollback(self, **_kwargs: object) -> dict:
                return {"status": "full"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = recovery_plan(root)
            approval = recovery_approval(plan)
            recovery_dir = root / "recovery"
            dry = execute_recovery_plan(plan, root, approval=approval, rollback_token="rollback-fixture-token", recovery_dir=recovery_dir, dry_run=True, executed_at=RECOVERY_EXECUTED_AT)
            self.assertFalse((recovery_dir / f"{plan['plan_id']}.intent.json").exists())
            self.assertFalse((recovery_dir / f"{plan['plan_id']}.receipt.json").exists())
            receipt = execute_recovery_plan(plan, root, approval=approval, rollback_token="rollback-fixture-token", recovery_dir=recovery_dir, provider=Provider(), doctor_collector=lambda _root: report_with(runtime_failure()), executed_at=RECOVERY_EXECUTED_AT)
        self.assertEqual(dry["status"], "dry_run")
        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(receipt["rollback"]["status"], "full")

    def test_replay_revalidates_receipt_and_crash_intent_fails_closed(self) -> None:
        class Provider:
            def restart(self, **_kwargs: object) -> dict:
                return {"status": "restarted"}
            def rollback(self, **_kwargs: object) -> dict:
                return {"status": "full"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = recovery_plan(root)
            approval = recovery_approval(plan)
            recovery_dir = root / "recovery"
            receipt = execute_recovery_plan(plan, root, approval=approval, rollback_token="rollback-fixture-token", recovery_dir=recovery_dir, provider=Provider(), doctor_collector=lambda _root: report_with(), executed_at=RECOVERY_EXECUTED_AT)
            receipt["receipt_digest"] = "sha256:" + "0" * 64
            (recovery_dir / f"{plan['plan_id']}.receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            with self.assertRaisesRegex(RemediationError, "receipt digest"):
                execute_recovery_plan(plan, root, approval=approval, rollback_token="rollback-fixture-token", recovery_dir=recovery_dir, provider=Provider(), executed_at=RECOVERY_EXECUTED_AT)
            (recovery_dir / f"{plan['plan_id']}.receipt.json").unlink()
            with self.assertRaisesRegex(RemediationError, "external effect is unknown"):
                execute_recovery_plan(plan, root, approval=approval, rollback_token="rollback-fixture-token", recovery_dir=recovery_dir, provider=Provider(), executed_at=RECOVERY_EXECUTED_AT)

    def test_executable_fingerprint_ignores_mtime_metadata(self) -> None:
        executables = workspace_fingerprint(ROOT)["facts"]["executables"]
        for entry in executables.values():
            if entry["status"] == "present":
                self.assertIn("digest", entry)
                self.assertNotIn("mtime_ns", entry)

    def test_recovery_plan_separates_source_dependency_and_requires_exact_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = recovery_plan(root)
            approval = recovery_approval(plan)
            self.assertNotEqual(plan["source_identity"]["digest"], plan["dependency_evidence"]["digest"])
            self.assertEqual(plan["action"]["kind"], "mcp.process.restart")
            self.assertEqual(plan["status"], "approval_required")
            receipt = execute_recovery_plan(
                plan, root, approval=approval, recovery_dir=root / "recovery", dry_run=True,
                rollback_token="rollback-fixture-token", executed_at="2026-08-26T10:00:02Z",
            )
        self.assertEqual(receipt["status"], "dry_run")
        self.assertEqual(receipt["effect"]["status"], "not_called")
        self.assertFalse((root / "recovery").exists())
        self.assertEqual(list(schema_validator(ROOT / "schemas" / "recovery-plan.schema.json").iter_errors(plan)), [])

    def test_observation_digest_is_stable_across_collection_times(self) -> None:
        first = report_with()
        second = report_with()
        first.generated_at = "2026-08-26T10:00:00Z"
        second.generated_at = "2026-08-26T10:01:00Z"
        from valp_cli.remediation import observation_digest
        self.assertEqual(observation_digest(first), observation_digest(second))

    def test_recovery_provider_is_one_shot_and_requires_independent_verification(self) -> None:
        class Provider:
            def __init__(self) -> None:
                self.calls = 0
            def restart(self, *, resource_id: str, resource_version: str, rollback_token: str) -> dict:
                self.calls += 1
                return {"status": "restarted", "resource_id": resource_id, "resource_version": resource_version}
            def rollback(self, *, resource_id: str, resource_version: str, rollback_token: str) -> dict:
                return {"status": "full"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = recovery_plan(root)
            approval = recovery_approval(plan)
            provider = Provider()
            first = execute_recovery_plan(plan, root, approval=approval, provider=provider, rollback_token="rollback-fixture-token", recovery_dir=root / "recovery", doctor_collector=lambda _root: report_with(), executed_at=RECOVERY_EXECUTED_AT)
            replay = execute_recovery_plan(plan, root, approval=approval, provider=provider, rollback_token="rollback-fixture-token", recovery_dir=root / "recovery", doctor_collector=lambda _root: report_with(), executed_at=RECOVERY_EXECUTED_AT)
        self.assertEqual(first["status"], "fixed")
        self.assertEqual(replay["receipt_digest"], first["receipt_digest"])
        self.assertEqual(provider.calls, 1)

    def test_recovery_rejects_changed_action_or_leftover_intent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = recovery_plan(root)
            approval = recovery_approval(plan)
            recovery_dir = root / "recovery"
            recovery_dir.mkdir()
            (recovery_dir / f"{plan['plan_id']}.intent.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RemediationError, "external effect is unknown"):
                execute_recovery_plan(plan, root, approval=approval, recovery_dir=recovery_dir, rollback_token="rollback-fixture-token", dry_run=True, executed_at=RECOVERY_EXECUTED_AT)
            approval["action_digest"] = "sha256:" + "0" * 64
            approval["approval_digest"] = digest({key: value for key, value in approval.items() if key != "approval_digest"})
            with self.assertRaisesRegex(RemediationError, "exact-digest"):
                execute_recovery_plan(plan, root, approval=approval, recovery_dir=root / "other", rollback_token="rollback-fixture-token", dry_run=True, executed_at=RECOVERY_EXECUTED_AT)
    def test_transient_runtime_failure_builds_ready_digest_bound_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_repair_plan(
                report_with(runtime_failure()),
                Path(tmp),
                generated_at="2026-08-26T10:00:00Z",
            )

        self.assertEqual(plan["status"], "ready")
        self.assertEqual(plan["risk_classification"], "low")
        self.assertEqual([item["kind"] for item in plan["actions"]], ["doctor.recheck"])
        self.assertEqual(plan["actions"][0]["mutation_surface"], "none")
        self.assertEqual(plan["actions"][0]["max_attempts"], 1)
        validate_plan_digest(plan)
        errors = list(
            schema_validator(ROOT / "schemas" / "repair-plan.schema.json").iter_errors(plan)
        )
        self.assertEqual(errors, [])

    def test_protected_worktree_finding_stops_at_approval_gate(self) -> None:
        check = DoctorCheck(
            id="git_worktree_clean",
            title="Git working tree is clean",
            status="fail",
            message="tracked changes exist",
            evidence=[" M source.py"],
            suggestion="Review local changes.",
        )
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_repair_plan(
                report_with(check),
                Path(tmp),
                generated_at="2026-08-26T10:00:00Z",
            )

        self.assertEqual(plan["status"], "approval_required")
        self.assertEqual(plan["risk_classification"], "protected")
        self.assertEqual(plan["actions"], [])
        self.assertTrue(plan["approval"]["required"])
        self.assertTrue(plan["approval"]["deferred_mutation_required"])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RemediationError, "not executable"):
                execute_repair_plan(plan, Path(tmp))

    def test_executor_issues_receipt_and_proof_only_after_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_repair_plan(
                report_with(runtime_failure()),
                root,
                generated_at="2026-08-26T10:00:00Z",
            )
            receipt, certificate = execute_repair_plan(
                plan,
                root,
                doctor_collector=lambda _root: report_with(),
                executed_at="2026-08-26T10:00:10Z",
            )

        self.assertEqual(receipt["status"], "fixed")
        self.assertEqual(receipt["verification"]["status"], "pass")
        self.assertEqual(receipt["verification"]["unresolved"], [])
        self.assertIsNotNone(certificate)
        self.assertEqual(receipt["proof_certificate_digest"], certificate["certificate_digest"])
        receipt_errors = list(
            schema_validator(ROOT / "schemas" / "repair-receipt.schema.json").iter_errors(receipt)
        )
        certificate_errors = list(
            schema_validator(
                ROOT / "schemas" / "doctor-proof-certificate.schema.json"
            ).iter_errors(certificate)
        )
        self.assertEqual(receipt_errors, [])
        self.assertEqual(certificate_errors, [])

    def test_persistent_failure_remains_blocked_without_false_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_repair_plan(
                report_with(runtime_failure()),
                root,
                generated_at="2026-08-26T10:00:00Z",
            )
            receipt, certificate = execute_repair_plan(
                plan,
                root,
                doctor_collector=lambda _root: report_with(runtime_failure()),
                executed_at="2026-08-26T10:00:10Z",
            )

        self.assertEqual(receipt["status"], "blocked")
        self.assertEqual(receipt["verification"]["status"], "fail")
        self.assertEqual(receipt["verification"]["resolved"], [])
        self.assertIsNone(certificate)
        self.assertIsNone(receipt["proof_certificate_digest"])

    def test_executor_rejects_workspace_drift_after_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_repair_plan(
                report_with(runtime_failure()),
                root,
                generated_at="2026-08-26T10:00:00Z",
            )
            (root / "SPEC.md").write_text("changed\n", encoding="utf-8")

            with self.assertRaisesRegex(RemediationError, "workspace fingerprint changed"):
                execute_repair_plan(
                    plan,
                    root,
                    doctor_collector=lambda _root: report_with(),
                )

    def test_executor_rejects_tampered_plan_and_unknown_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_repair_plan(
                report_with(runtime_failure()),
                root,
                generated_at="2026-08-26T10:00:00Z",
            )
            plan["selected_strategy"] = "tampered"
            with self.assertRaisesRegex(RemediationError, "plan digest"):
                execute_repair_plan(plan, root)

            plan = build_repair_plan(
                report_with(runtime_failure()),
                root,
                generated_at="2026-08-26T10:00:00Z",
            )
            plan["actions"][0]["kind"] = "shell.exec"
            plan["plan_digest"] = digest(
                {key: value for key, value in plan.items() if key != "plan_digest"}
            )
            with self.assertRaisesRegex(RemediationError, "unsupported repair action"):
                execute_repair_plan(plan, root)

    def test_executor_records_failed_receipt_when_recheck_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_repair_plan(
                report_with(runtime_failure()),
                root,
                generated_at="2026-08-26T10:00:00Z",
            )

            def failed_collector(_root: Path) -> DoctorReport:
                raise TimeoutError("fixture timeout")

            receipt, certificate = execute_repair_plan(
                plan,
                root,
                doctor_collector=failed_collector,
                executed_at="2026-08-26T10:00:10Z",
            )

        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["action_results"][0]["status"], "failed")
        self.assertEqual(receipt["action_results"][0]["result"], "Doctor recheck failed: TimeoutError")
        self.assertIsNone(certificate)

    def test_executor_blocks_counterfactual_workspace_divergence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_repair_plan(
                report_with(runtime_failure()),
                root,
                generated_at="2026-08-26T10:00:00Z",
            )

            def diverging_collector(_root: Path) -> DoctorReport:
                (root / "SPEC.md").write_text("changed during recheck\n", encoding="utf-8")
                return report_with()

            receipt, certificate = execute_repair_plan(
                plan,
                root,
                doctor_collector=diverging_collector,
                executed_at="2026-08-26T10:00:10Z",
            )

        self.assertEqual(receipt["status"], "blocked")
        self.assertIn(
            {"code": "COUNTERFACTUAL_DIVERGENCE", "subject": "workspace:dependency-fingerprint"},
            receipt["verification"]["unresolved"],
        )
        self.assertIsNone(certificate)

    def test_receipt_verification_detects_regression(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_repair_plan(
                report_with(runtime_failure()),
                root,
                generated_at="2026-08-26T10:00:00Z",
            )
            receipt, _certificate = execute_repair_plan(
                plan,
                root,
                doctor_collector=lambda _root: report_with(),
                executed_at="2026-08-26T10:00:10Z",
            )
            result = verify_repair_receipt(
                receipt,
                root,
                doctor_collector=lambda _root: report_with(runtime_failure()),
                verified_at="2026-08-26T10:01:00Z",
            )

        self.assertEqual(result["status"], "regressed")
        self.assertEqual(result["regressed"][0]["code"], "RUNTIME_PROBE_FAILED")

    def test_blocked_receipt_cannot_verify_as_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = build_repair_plan(
                report_with(runtime_failure()),
                root,
                generated_at="2026-08-26T10:00:00Z",
            )
            receipt, _certificate = execute_repair_plan(
                plan,
                root,
                doctor_collector=lambda _root: report_with(runtime_failure()),
                executed_at="2026-08-26T10:00:10Z",
            )
            result = verify_repair_receipt(
                receipt,
                root,
                doctor_collector=lambda _root: report_with(),
                verified_at="2026-08-26T10:01:00Z",
            )

        self.assertEqual(result["status"], "not_proven")
        self.assertIsNone(result["current_state_digest"])

    def test_atomic_writer_emits_canonical_json_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "artifact.json"
            write_json_atomic(path, {"b": 2, "a": 1})

            self.assertEqual(path.read_text(encoding="utf-8"), '{"a":1,"b":2}\n')
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1, "b": 2})

    def test_recovery_examples_have_valid_canonical_digests(self) -> None:
        plan = json.loads((ROOT / "examples" / "repair-plan.json").read_text(encoding="utf-8"))
        certificate = json.loads(
            (ROOT / "examples" / "doctor-proof-certificate.json").read_text(encoding="utf-8")
        )
        receipt = json.loads(
            (ROOT / "examples" / "repair-receipt.json").read_text(encoding="utf-8")
        )
        snapshot = json.loads(
            (ROOT / "examples" / "doctor-snapshot.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            plan["plan_digest"],
            digest({key: value for key, value in plan.items() if key != "plan_digest"}),
        )
        self.assertEqual(
            certificate["certificate_digest"],
            digest(
                {
                    key: value
                    for key, value in certificate.items()
                    if key != "certificate_digest"
                }
            ),
        )
        self.assertEqual(
            receipt["receipt_digest"],
            digest({key: value for key, value in receipt.items() if key != "receipt_digest"}),
        )
        self.assertEqual(snapshot["report_digest"], digest(snapshot["report"]))
        self.assertEqual(
            snapshot["snapshot_digest"],
            digest({key: value for key, value in snapshot.items() if key != "snapshot_digest"}),
        )
        for name, field in (
            ("recovery-plan.json", "plan_digest"),
            ("recovery-approval.json", "approval_digest"),
            ("recovery-intent.json", "intent_digest"),
            ("recovery-receipt.json", "receipt_digest"),
        ):
            value = json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))
            self.assertEqual(value[field], digest({key: item for key, item in value.items() if key != field}))

    def test_doctor_snapshot_matches_schema_and_reuses_without_collecting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "cache" / "doctor-snapshot.json"
            calls = 0

            def collector(_root: Path) -> DoctorReport:
                nonlocal calls
                calls += 1
                return report_with()

            first, first_cache = collect_doctor_with_snapshot(
                root,
                snapshot_path,
                collector=collector,
                evaluated_at="2026-08-26T10:00:00Z",
            )
            second, second_cache = collect_doctor_with_snapshot(
                root,
                snapshot_path,
                collector=collector,
                evaluated_at="2026-08-26T10:01:00Z",
            )
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

        self.assertEqual(calls, 1)
        self.assertEqual(first_cache["status"], "refreshed")
        self.assertEqual(second_cache["status"], "reused")
        self.assertEqual(first.generated_at, second.generated_at)
        errors = list(
            schema_validator(ROOT / "schemas" / "doctor-snapshot.schema.json").iter_errors(
                snapshot
            )
        )
        self.assertEqual(errors, [])

    def test_doctor_snapshot_refreshes_after_dependency_change_or_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "doctor-snapshot.json"
            calls = 0

            def collector(_root: Path) -> DoctorReport:
                nonlocal calls
                calls += 1
                return report_with()

            collect_doctor_with_snapshot(
                root,
                snapshot_path,
                collector=collector,
                evaluated_at="2026-08-26T10:00:00Z",
            )
            (root / "SPEC.md").write_text("dependency changed\n", encoding="utf-8")
            _changed_report, changed_cache = collect_doctor_with_snapshot(
                root,
                snapshot_path,
                collector=collector,
                evaluated_at="2026-08-26T10:01:00Z",
            )
            _expired_report, expired_cache = collect_doctor_with_snapshot(
                root,
                snapshot_path,
                collector=collector,
                evaluated_at="2026-08-26T10:07:00Z",
            )

        self.assertEqual(calls, 3)
        self.assertEqual(changed_cache["status"], "refreshed")
        self.assertIn("fingerprint changed", changed_cache["reason"])
        self.assertEqual(expired_cache["status"], "refreshed")
        self.assertIn("expired", expired_cache["reason"])

    def test_doctor_snapshot_rejects_invalid_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RemediationError, "between 1 and 3600"):
                build_doctor_snapshot(report_with(), Path(tmp), ttl_seconds=0)

    def test_failed_or_transient_snapshot_is_never_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "doctor-snapshot.json"
            calls = 0

            def collector(_root: Path) -> DoctorReport:
                nonlocal calls
                calls += 1
                return report_with(runtime_failure())

            collect_doctor_with_snapshot(
                root,
                snapshot_path,
                collector=collector,
                evaluated_at="2026-08-26T10:00:00Z",
            )
            _report, cache = collect_doctor_with_snapshot(
                root,
                snapshot_path,
                collector=collector,
                evaluated_at="2026-08-26T10:01:00Z",
            )

        self.assertEqual(calls, 2)
        self.assertEqual(cache["status"], "refreshed")
        self.assertIn("not reusable", cache["reason"])

    def test_snapshot_expiry_is_bounded_by_model_observation_ttl(self) -> None:
        passport = {
            "principal_id": "agent-example",
            "model_identity": {
                "model_probe": {
                    "observed_at": "2026-08-26T10:00:00Z",
                    "ttl_seconds": 60,
                    "session_identity": {"status": "known"},
                },
                "observed_model": {"freshness": "current"},
                "mismatch": {"status": "match"},
            },
            "local_installation": {"status": "installed", "source": "fixture"},
            "live_callability": {"status": "pass", "runtime": "fixture"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = build_doctor_snapshot(
                report_with(passports=[passport]),
                Path(tmp),
                issued_at="2026-08-26T10:00:00Z",
                ttl_seconds=300,
            )

        self.assertEqual(snapshot["effective_ttl_seconds"], 60)
        self.assertEqual(snapshot["expires_at"], "2026-08-26T10:01:00Z")


if __name__ == "__main__":
    unittest.main()
