from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from .control_plane import ControlPlaneError, InstallationCore, digest_without, write_json
from .plugins import validate_plugin_manifest
from .task_control import init_task, task_state, transition_task
from .process_adapter import run_process


def run_conformance(profile: str = "core-writer") -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(name: str, operation: Callable[[], None]) -> None:
        try:
            operation()
        except Exception as error:  # conformance output must record the exact failed check
            checks.append({"name": name, "status": "fail", "error": str(error)})
        else:
            checks.append({"name": name, "status": "pass"})

    with tempfile.TemporaryDirectory(prefix="valp-conformance-") as temporary:
        root = Path(temporary) / ".valp"
        workspace = Path(temporary)

        def setup() -> None:
            core = InstallationCore(root)
            core.init()
            core.discover_candidates()
            core.select_leader("manual-user")
            assert core.state()["status"] == "active"

        check("bootstrap-selection-epoch", setup)

        def hello() -> None:
            response = InstallationCore(root).hello("YWJjMTIz")
            assert response["nonce"] == "YWJjMTIz"
            assert response["manifest_digest"].startswith("sha256:")

        check("fixed-hello", hello)

        def fencing() -> None:
            core = InstallationCore(root)
            state = core.state()
            try:
                core._transition(
                    event_kind="fenced-test",
                    message_kind="command.test.fenced",
                    principal_id="stale-principal",
                    principal_kind="test",
                    epoch=0,
                    expected_revision=state["revision"],
                    payload={},
                    target_status="degraded",
                    idempotency_key="fenced-test",
                )
            except ControlPlaneError as error:
                assert error.code == "VALP-E-LEADER-EPOCH"
            else:
                raise AssertionError("stale epoch was accepted")

        check("bootstrap-epoch-fencing", fencing)

        def cas() -> None:
            core = InstallationCore(root)
            state = core.state()
            try:
                core._transition(
                    event_kind="cas-test",
                    message_kind="command.test.cas",
                    principal_id="manual-user",
                    principal_kind="human",
                    epoch=state["active_leader_epoch"],
                    expected_revision=state["revision"] - 1,
                    payload={},
                    target_status="degraded",
                    idempotency_key="cas-test",
                )
            except ControlPlaneError as error:
                assert error.code == "VALP-E-STATE-CONFLICT"
            else:
                raise AssertionError("stale revision was accepted")

        check("revision-cas", cas)

        def capabilities() -> None:
            core = InstallationCore(root)
            result = core.reconcile_capabilities([
                {"subject_id": "manual-user", "capability_id": "coordination", "layer": "local_presence", "status": "present"},
                {"subject_id": "manual-user", "capability_id": "coordination", "layer": "live_callable", "status": "pass"},
            ])
            assert result["registry"]["registry_revision"] == 1
            assert result["registry"]["entries"]["manual-user::coordination"]["effective_status"] == "pass"

        check("capability-layered-registry", capabilities)

        def claims_and_review() -> None:
            artifact = root / "evidence" / "done.txt"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("done\n", encoding="utf-8")
            core = InstallationCore(root)
            core.add_evidence("evidence/done.txt", evidence_kind="command-output", producer_principal_id="worker")
            claim = core.declare_claim(
                subject_ref="evidence/done.txt",
                claim_kind="done",
                predicate="artifact is complete",
                asserted_value=True,
                scope="conformance",
                claimant_principal_id="worker",
                evidence_refs=["evidence/done.txt"],
            )
            result = core.record_review(claim_id=claim["claim_id"], reviewer_principal_id="reviewer", verdict="pass")
            assert result["claim"]["status"] == "verified"

        check("content-addressed-claim-review", claims_and_review)

        def task_done_reducer() -> None:
            init_task(root, "TASK-CONFORMANCE")
            state = task_state(root, "TASK-CONFORMANCE")
            state = transition_task(root, "TASK-CONFORMANCE", "published", expected_revision=state["revision"])
            try:
                transition_task(root, "TASK-CONFORMANCE", "done", expected_revision=state["revision"])
            except ControlPlaneError as error:
                assert error.code == "VALP-E-STATE-TRANSITION"
            else:
                raise AssertionError("task reducer accepted a direct Done transition")

        check("task-done-gate-reducer", task_done_reducer)

        def process_adapter() -> None:
            result = run_process(root, "PROCESS-CONFORMANCE", [sys.executable, "-c", "print('adapter')"], approve=True)
            assert result["status"] == "completed"
            assert result["run"]["evidence_ids"]

        check("non-herdr-process-adapter", process_adapter)

        def replay() -> None:
            core = InstallationCore(root)
            state = core.replay()
            assert state["revision"] == core.state()["revision"]
            event_path = root / "events.jsonl"
            original = event_path.read_text(encoding="utf-8")
            event_path.write_text(original.replace('"event_kind": "capability_reconciliation_completed"', '"event_kind": "tampered"', 1), encoding="utf-8")
            try:
                core.replay()
            except ControlPlaneError as error:
                assert error.code == "VALP-E-REGISTRY-CONSISTENCY"
            else:
                raise AssertionError("tampered event replayed successfully")
            event_path.write_text(original, encoding="utf-8")

        check("event-replay-digest", replay)

        def plugin_boundary() -> None:
            manifest = {
                "schema_version": "valp-plugin-manifest.v1",
                "plugin_id": "safe-discovery",
                "implementation_id": "test-plugin",
                "plugin_kind": "discovery",
                "protocol_read_versions": ["0.3.0-draft"],
                "protocol_write_versions": ["0.3.0-draft"],
                "entrypoint": "test.plugin:run",
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
            try:
                validate_plugin_manifest(manifest)
            except ControlPlaneError as error:
                assert error.code == "VALP-E-PLUGIN-BOUNDARY"
            else:
                raise AssertionError("core ledger permission was accepted")

        check("plugin-boundary", plugin_boundary)

        def migration() -> None:
            (workspace / ".herdr-loop" / "tasks" / "legacy").mkdir(parents=True)
            (workspace / ".herdr-loop" / "tasks" / "legacy" / "state.json").write_text("{}\n", encoding="utf-8")
            plan = InstallationCore(root).migrate_plan(workspace)
            assert plan["task_file_count"] == 1
            assert plan["plan_digest"].startswith("sha256:")

        check("legacy-migration-dry-run", migration)

    passed = sum(item["status"] == "pass" for item in checks)
    failed = len(checks) - passed
    return {
        "schema_version": "valp-conformance-report.v1",
        "profile": profile,
        "implementation_id": "valp-reference-cli",
        "checks": checks,
        "pass_count": passed,
        "fail_count": failed,
        "status": "PASS" if failed == 0 else "FAIL",
    }
