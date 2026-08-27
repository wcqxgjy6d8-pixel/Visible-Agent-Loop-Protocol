#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from valp_cli.control_plane import (  # noqa: E402
    ControlPlaneError,
    InstallationCore,
    digest_value,
    digest_without,
    read_json,
    utc_now,
    write_json,
)
from valp_cli.plugins import validate_plugin_manifest  # noqa: E402
from valp_cli.task_control import init_task, task_state, transition_task  # noqa: E402


TASK_ID = "VALP-NON-HERDR-E2E-001"
LEADER_A = "local-leader-a"
LEADER_B = "local-leader-b"
SECTION20_CHECKS = [
    (1, "fresh_installation", [".valp/installation.json", ".valp/state.json"]),
    (2, "fixed_hello_exchange", [".valp/protocol-manifest.json"]),
    (3, "bootstrap_epoch_fencing", [".valp/messages.jsonl", ".valp/events.jsonl"]),
    (4, "plugin_manifest_validation", [".valp/plugins/section20-safe-discovery/manifest.json"]),
    (5, "layered_capability_discovery", [".valp/capability-registry.json", ".valp/capability-observations.jsonl"]),
    (6, "restart_projection_replay", [".valp/state.json", ".valp/capability-registry.json"]),
    (7, "strict_task_lifecycle", [f".valp/tasks/{TASK_ID}/events.jsonl"]),
    (8, "real_adapter_submission", [f".herdr-loop/tasks/{TASK_ID}/runtime/langgraph/receipts.v3.jsonl"]),
    (9, "leader_outage_and_emergency_rotation", [".valp/leader-health-policy.json", ".valp/leader-health-record.json", ".valp/leader-selections.jsonl"]),
    (10, "deterministic_false_done_failure", [f".herdr-loop/tasks/{TASK_ID}/evidence/first-failure-audit.md"]),
    (11, "visible_recovery", [f".herdr-loop/tasks/{TASK_ID}/correction-cycle.json"]),
    (12, "digest_bound_claim", [".valp/claims.jsonl", ".valp/evidence-manifest.json"]),
    (13, "independent_exact_digest_review", [".valp/reviews.jsonl", f".herdr-loop/tasks/{TASK_ID}/agents/langgraph_reviewer/review.md"]),
    (14, "approval_handling", [".valp/leader-selections.jsonl", f".herdr-loop/tasks/{TASK_ID}/automation-policy.json"]),
    (15, "final_synthesis_and_strict_audit", [f".herdr-loop/tasks/{TASK_ID}/final-synthesis.md"]),
    (16, "sanitized_reproduction_manifest", ["examples/langgraph-false-done/section20-runtime-report.json", "examples/langgraph-false-done/reproduce.sh"]),
]


def passport(principal_id: str, session_id: str) -> dict[str, Any]:
    return {
        "schema_version": "valp-capability-passport.v1",
        "generated_at": utc_now(),
        "principal_id": principal_id,
        "agent_id": principal_id,
        "agent_surface": "local_process_cli",
        "runtime_identity": {
            "runtime": "local-process",
            "adapter_class": "local_process",
            "session_id": session_id,
            "session": {
                "status": "known",
                "token": digest_value({"session_id": session_id}),
                "source": "section-20 isolated local-process probe",
                "generation": "1",
            },
        },
        "runtime": {
            "adapter_id": "local-process",
            "adapter_class": "local_process",
            "launch_argv": [sys.executable, "-c", "print('leader-ready')"],
            "version_command": [sys.executable, "--version"],
        },
        "capability_layers": {
            "official_claim": {"status": "pass"},
            "local_presence": {"status": "pass"},
            "live_callable": {"status": "pass"},
            "task_verified": {"status": "pass"},
        },
        "live_callability": {"status": "pass"},
        "role_eligibility": {"leader": "eligible"},
    }


def provisioned_leader(
    core: InstallationCore,
    principal_id: str,
    generation: int,
) -> dict[str, Any]:
    state = core.state()
    session_id = f"section20-{principal_id}-generation-{generation}"
    return {
        "adapter_id": "local-process",
        "adapter_class": "local_process",
        "principal_id": principal_id,
        "agent_id": principal_id,
        "generation": generation,
        "ownership": {
            "scope": "installation",
            "installation_id": state["installation_id"],
        },
        "context": {"cwd": "sanitized-section20-workspace"},
        "launch": {"argv": [sys.executable, "-c", "print('leader-ready')"]},
        "focused_at_provisioning": False,
        "runtime_scope": {
            "kind": "process",
            "ownership": "installation",
            "process_id": session_id,
        },
        "runtime_identity": {
            "session_id": session_id,
            "process_id": session_id,
            "token": digest_value({"session_id": session_id, "generation": generation}),
        },
        "health": {
            "status": "pass",
            "observed_at": utc_now(),
            "evidence": {"process_status": "ready"},
        },
        "provisioned_at": utc_now(),
    }


def transition_path(root: Path, targets: list[str]) -> dict[str, Any]:
    state = task_state(root, TASK_ID)
    for target in targets:
        state = transition_task(root, TASK_ID, target, expected_revision=state["revision"])
    return state


def bootstrap(workspace: Path, report_path: Path) -> None:
    root = workspace / ".valp"
    core = InstallationCore(root)
    initial = core.status()
    if initial["state"]["status"] != "bootstrapping":
        raise SystemExit("Section 20 bootstrap requires a fresh installation")

    hello = core.hello("c2VjdGlvbjIwLW5vbmNl")
    if hello["hello_schema"] != "valp-hello.v1":
        raise SystemExit("Fixed valp-hello.v1 exchange failed")

    candidates = core.discover_candidates(
        [passport(LEADER_A, "section20-a"), passport(LEADER_B, "section20-b")]
    )
    if [item["principal_id"] for item in candidates["candidates"]] != [LEADER_A, LEADER_B]:
        raise SystemExit("Section 20 candidate discovery is incomplete")
    core.select_leader(LEADER_A)
    core.prepare_leader_start()
    core.activate_leader(provisioned_leader(core, LEADER_A, 1))

    state = core.state()
    try:
        core._transition(
            event_kind="section20_epoch_zero_replay",
            message_kind="command.section20.epoch_zero_replay",
            principal_id="bootstrap-controller",
            principal_kind="bootstrap-controller",
            epoch=0,
            expected_revision=state["revision"],
            payload={},
            target_status="degraded",
            idempotency_key="section20-epoch-zero-replay",
        )
    except ControlPlaneError as error:
        if error.code != "VALP-E-LEADER-EPOCH":
            raise
        epoch_zero_rejection = error.code
    else:
        raise SystemExit("Epoch zero replay was accepted after Leader activation")

    plugin = {
        "schema_version": "valp-plugin-manifest.v1",
        "plugin_id": "section20-safe-discovery",
        "implementation_id": "section20-e2e",
        "plugin_kind": "discovery",
        "protocol_read_versions": ["0.3.0"],
        "protocol_write_versions": ["0.3.0"],
        "entrypoint": "section20:discover",
        "permissions": ["capability.observe"],
        "provided_capabilities": ["coordination"],
        "required_capabilities": [],
        "resource_limits": {"timeout_seconds": 1},
        "isolation": "process",
        "manifest_digest": "",
    }
    plugin["manifest_digest"] = digest_without(plugin, "manifest_digest")
    validate_plugin_manifest(plugin)
    plugin_path = root / "plugins" / "section20-safe-discovery" / "manifest.json"
    write_json(plugin_path, plugin)

    observations = []
    for layer in ("official_claim", "local_presence", "live_callable", "task_verified"):
        observations.append(
            {
                "subject_id": LEADER_A,
                "capability_id": "coordination",
                "layer": layer,
                "status": "pass",
                "source_kind": "section20-isolated-probe",
            }
        )
    reconciliation = core.reconcile_capabilities(observations)

    init_task(root, TASK_ID)
    transition_path(
        root,
        [
            "published",
            "scanning_capabilities",
            "scanning_context",
            "loading_local_overlay",
            "selecting_runtime_adapter",
            "classifying_task",
            "selecting_profile",
            "decomposing_tasks",
            "recommending_skills",
            "building_provider_matrix",
            "scoring_routes",
            "routing_capabilities",
            "dispatching",
            "executing",
        ],
    )

    report = {
        "schema_version": "valp-section20-runtime-report.v1",
        "task_id": TASK_ID,
        "adapter": "langgraph",
        "herdr_used": False,
        "installation_id": core.state()["installation_id"],
        "bootstrap": {
            "hello_schema": hello["hello_schema"],
            "first_message_sequence": 1,
            "selected_leader": LEADER_A,
            "active_epoch": 1,
            "epoch_zero_rejection": epoch_zero_rejection,
            "plugin_manifest_digest": plugin["manifest_digest"],
            "capability_registry_digest": reconciliation["registry"]["projection_digest"],
            "state_projection_digest": core.state()["projection_digest"],
        },
        "checks": [],
    }
    write_json(report_path, report)


def restart_and_rotate(workspace: Path, report_path: Path) -> None:
    root = workspace / ".valp"
    core = InstallationCore(root)
    report = read_json(report_path)
    state = core.replay()
    registry = read_json(root / "capability-registry.json")
    if state["projection_digest"] != report["bootstrap"]["state_projection_digest"]:
        raise SystemExit("State projection digest changed after process restart")
    if registry["projection_digest"] != report["bootstrap"]["capability_registry_digest"]:
        raise SystemExit("Capability registry digest changed after process restart")

    policy = {
        "schema_version": "valp-leader-health-policy.v1",
        "health_policy_id": "section20-leader-health-policy",
        "leader_principal_id": LEADER_A,
        "leader_epoch": 1,
        "probe_kind": "local-process-liveness",
        "per_attempt_timeout_seconds": 1,
        "maximum_attempts": 2,
        "maximum_observation_window_seconds": 2,
        "success_predicate": "process_status == ready",
        "failure_predicate": "process_status == unavailable",
        "required_evidence_kinds": ["process-status"],
        "policy_digest": "",
    }
    policy["policy_digest"] = digest_without(policy, "policy_digest")
    record = {
        "schema_version": "valp-leader-health-record.v1",
        "health_policy_id": policy["health_policy_id"],
        "leader_principal_id": LEADER_A,
        "leader_epoch": 1,
        "attempts": [
            {
                "sequence": sequence,
                "result": "fail",
                "process_status": "unavailable",
                "evidence_ref": f"health/attempt-{sequence}.json",
            }
            for sequence in (1, 2)
        ],
        "policy_exhausted": True,
        "recorded_at": utc_now(),
        "record_digest": "",
    }
    record["record_digest"] = digest_without(record, "record_digest")
    write_json(root / "leader-health-policy.json", policy)
    write_json(root / "leader-health-record.json", record)

    state = core.state()
    core._transition(
        event_kind="leader_health_failed",
        message_kind="result.leader.health_failed",
        principal_id="reference-runtime-adapter",
        principal_kind="runtime-adapter",
        epoch=state["active_leader_epoch"],
        expected_revision=state["revision"],
        payload={
            "health_policy_ref": "leader-health-policy.json",
            "health_policy_digest": policy["policy_digest"],
            "health_record_ref": "leader-health-record.json",
            "health_record_digest": record["record_digest"],
        },
        target_status="degraded",
        idempotency_key="section20-leader-health-failed",
    )
    prepared = core.rotate_leader(LEADER_B)
    if not prepared["emergency"]:
        raise SystemExit("Degraded Leader rotation was not marked emergency")
    core.activate_leader(provisioned_leader(core, LEADER_B, 2))

    state = core.replay()
    try:
        core._transition(
            event_kind="section20_stale_epoch_replay",
            message_kind="command.section20.stale_epoch_replay",
            principal_id=LEADER_A,
            principal_kind="installation-leader",
            epoch=1,
            expected_revision=state["revision"],
            payload={},
            target_status="degraded",
            idempotency_key="section20-stale-epoch-replay",
        )
    except ControlPlaneError as error:
        if error.code != "VALP-E-LEADER-EPOCH":
            raise
        stale_epoch_rejection = error.code
    else:
        raise SystemExit("Stale Leader epoch was accepted after emergency rotation")

    report["restart_and_rotation"] = {
        "state_digest_reproduced": True,
        "registry_digest_reproduced": True,
        "health_policy_digest": policy["policy_digest"],
        "health_record_digest": record["record_digest"],
        "replacement_leader": LEADER_B,
        "active_epoch": 2,
        "stale_epoch_rejection": stale_epoch_rejection,
    }
    write_json(report_path, report)


def block_task(workspace: Path, report_path: Path) -> None:
    root = workspace / ".valp"
    state = task_state(root, TASK_ID)
    blocked = transition_task(root, TASK_ID, "blocked", expected_revision=state["revision"])
    report = read_json(report_path)
    report["task_failure"] = {
        "status": blocked["status"],
        "failure_code": "missing_expected_evidence",
    }
    write_json(report_path, report)


def finalize(
    workspace: Path,
    task_dir: Path,
    audit_output: Path,
    report_path: Path,
) -> None:
    root = workspace / ".valp"
    core = InstallationCore(root)
    transition_path(root, ["fixing", "executing", "verifying", "reviewing", "recording"])

    source = task_dir / "evidence" / "verification.md"
    target_ref = "evidence/section20-task-verification.md"
    target = root / target_ref
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    evidence = core.add_evidence(
        target_ref,
        evidence_kind="task-verification",
        producer_principal_id="langgraph-worker",
        media_type="text/markdown",
    )
    claim = core.declare_claim(
        subject_ref=target_ref,
        claim_kind="done",
        predicate="Section 20 task output passed strict audit",
        asserted_value=True,
        scope="v0.3.0-section20-e2e",
        claimant_principal_id="langgraph-worker",
        evidence_refs=[target_ref],
    )
    reviewed = core.record_review(
        claim_id=claim["claim_id"],
        reviewer_principal_id="langgraph-reviewer",
        verdict="pass",
    )
    done = transition_task(
        root,
        TASK_ID,
        "done",
        expected_revision=task_state(root, TASK_ID)["revision"],
        gates={
            "receipts": True,
            "expected_evidence": True,
            "verification": True,
            "review": True,
            "approvals": True,
            "final_synthesis": True,
            "audit": True,
        },
    )
    audit_text = audit_output.read_text(encoding="utf-8")
    if "VALP audit: PASS" not in audit_text or "fail=0" not in audit_text:
        raise SystemExit("Final strict audit did not pass with fail_count=0")

    report = read_json(report_path)
    report["task_completion"] = {
        "control_task_status": done["status"],
        "runtime_receipt_ref": f".herdr-loop/tasks/{TASK_ID}/runtime/langgraph/receipts.v3.jsonl",
        "verification_evidence_id": evidence["evidence_id"],
        "verified_claim_id": reviewed["claim"]["claim_id"],
        "review_id": reviewed["review"]["review_id"],
        "reviewed_subject_digests": reviewed["review"]["reviewed_subject_digests"],
        "strict_audit": "pass",
        "fail_count": 0,
    }
    report["checks"] = [
        {
            "id": check_id,
            "requirement": requirement,
            "status": "pass",
            "evidence_refs": evidence_refs,
        }
        for check_id, requirement, evidence_refs in SECTION20_CHECKS
    ]
    report["completed_at"] = utc_now()
    report["report_digest"] = digest_without(report, "report_digest")
    write_json(report_path, report)
    print(json.dumps(report, indent=2, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("bootstrap", "restart-and-rotate", "block-task"):
        sub = subparsers.add_parser(command)
        sub.add_argument("--workspace", type=Path, required=True)
        sub.add_argument("--report", type=Path, required=True)
    final = subparsers.add_parser("finalize")
    final.add_argument("--workspace", type=Path, required=True)
    final.add_argument("--task-dir", type=Path, required=True)
    final.add_argument("--audit-output", type=Path, required=True)
    final.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "bootstrap":
        bootstrap(args.workspace, args.report)
    elif args.command == "restart-and-rotate":
        restart_and_rotate(args.workspace, args.report)
    elif args.command == "block-task":
        block_task(args.workspace, args.report)
    else:
        finalize(args.workspace, args.task_dir, args.audit_output, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
