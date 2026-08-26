from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .doctor import DoctorCheck, DoctorReport, collect_doctor_report
from .protocol_receipts import canonical_json, digest


READY = "ready"
APPROVAL_REQUIRED = "approval_required"
BLOCKED = "blocked"
NOT_REQUIRED = "not_required"

AUTO_RECHECK_CODES = frozenset(
    {
        "AGENT_NOT_CALLABLE",
        "MODEL_BINDING_MISMATCH",
        "MODEL_OBSERVATION_STALE",
        "RUNTIME_PROBE_FAILED",
        "SESSION_IDENTITY_UNKNOWN",
    }
)
PROTECTED_CODES = frozenset(
    {
        "EXAMPLE_AUDIT_FAILED",
        "GIT_REPOSITORY_UNAVAILABLE",
        "INSTALLATION_INCOMPLETE",
        "SOURCE_SYNTAX_INVALID",
        "TASK_AUDIT_FAILED",
        "WORKTREE_DIRTY",
        "WORKTREE_TRACKING_DIVERGED",
    }
)
ALLOWED_REFERENCE_ACTIONS = frozenset({"doctor.recheck"})
RECOVERY_ACTION = "mcp.process.restart"
RECOVERY_LEVELS = ("L0", "L1", "L2", "L3", "L4")
RECOVERY_KILL_SWITCH = ".recovery-disabled"


class RemediationError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise RemediationError(f"invalid remediation timestamp: {value}") from error
    if parsed.tzinfo is None:
        raise RemediationError(f"remediation timestamp must include a timezone: {value}")
    return parsed


def _without_digest(value: dict[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def _file_fingerprint(path: Path, root: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    if not path.is_file():
        return {"ref": relative, "status": "absent"}
    payload = path.read_bytes()
    return {
        "ref": relative,
        "status": "present",
        "digest": digest(payload),
        "bytes": len(payload),
    }


def workspace_fingerprint(root: Path) -> dict[str, Any]:
    workspace = root.resolve()
    tracked_inputs = (
        "SPEC.md",
        "bin/valp",
        "valp_cli/doctor.py",
        "valp_cli/remediation.py",
        ".valp/agents/capabilities.json",
        ".valp/local-overlay.json",
        ".herdr-loop/agents/capabilities.json",
        ".herdr-loop/local-overlay.json",
    )
    executables: dict[str, Any] = {}
    for name in ("git", "herdr", "python3"):
        resolved = shutil.which(name)
        if not resolved:
            executables[name] = {"status": "absent"}
            continue
        path = Path(resolved).resolve()
        try:
            stat = path.stat()
            # Timestamps are mutable metadata, not executable identity.
            executables[name] = {
                "status": "present",
                "path": str(path),
                "digest": digest(path.read_bytes()),
                "bytes": stat.st_size,
            }
        except OSError:
            executables[name] = {"status": "unreadable", "path": str(path)}
    facts = {
        "files": [_file_fingerprint(workspace / item, workspace) for item in tracked_inputs],
        "executables": executables,
    }
    return {"digest": digest(facts), "facts": facts}


def source_identity(root: Path) -> dict[str, Any]:
    """Content identity for the recovery implementation, excluding local dependencies."""
    workspace = root.resolve()
    refs = ("SPEC.md", "bin/valp", "valp_cli/doctor.py", "valp_cli/remediation.py")
    facts = {"files": [_file_fingerprint(workspace / ref, workspace) for ref in refs]}
    return {"digest": digest(facts), "facts": facts}


def dependency_evidence(root: Path) -> dict[str, Any]:
    """Observed local dependencies; this is deliberately not source identity."""
    combined = workspace_fingerprint(root)
    source = source_identity(root)
    facts = dict(combined["facts"])
    source_refs = {item["ref"] for item in source["facts"]["files"]}
    facts["files"] = [item for item in facts["files"] if item["ref"] not in source_refs]
    return {"digest": digest(facts), "facts": facts}


def doctor_observation(report: DoctorReport) -> dict[str, Any]:
    value = asdict(report)
    value.pop("generated_at", None)
    value["checks"] = [
        {
            "id": item["id"],
            "status": item["status"],
            "message": item["message"],
            "evidence": item["evidence"],
        }
        for item in value.get("checks") or []
    ]
    # Collection time is transport metadata, not an observation claim.  Model
    # probe times remain in the report for TTL evaluation, but do not make an
    # otherwise identical health observation get a different digest.
    def stable(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: stable(value) for key, value in item.items()
                    if key not in {"generated_at", "observed_at", "freshness_evaluated_at"}}
        if isinstance(item, list):
            return [stable(entry) for entry in item]
        return item

    return stable(value)


def observation_digest(report: DoctorReport) -> str:
    return digest(doctor_observation(report))


def _bounded_report_expiry(
    report: DoctorReport,
    issued_time: datetime,
    ttl_seconds: int,
) -> tuple[datetime, list[str]]:
    expires = (issued_time + timedelta(seconds=ttl_seconds)).replace(microsecond=0)
    reasons: list[str] = []
    for passport in report.capability_passports:
        identity = passport.get("model_identity") if isinstance(passport.get("model_identity"), dict) else {}
        probe = identity.get("model_probe") if isinstance(identity.get("model_probe"), dict) else {}
        observed_at = probe.get("observed_at")
        probe_ttl = probe.get("ttl_seconds")
        if not observed_at or not isinstance(probe_ttl, int) or isinstance(probe_ttl, bool):
            continue
        try:
            probe_expires = _parse_time(str(observed_at)) + timedelta(seconds=max(0, probe_ttl))
        except RemediationError:
            reasons.append("Doctor report contains an invalid model observation timestamp")
            continue
        expires = min(expires, probe_expires.replace(microsecond=0))
    return expires, reasons


def build_doctor_snapshot(
    report: DoctorReport,
    root: Path,
    *,
    issued_at: str | None = None,
    ttl_seconds: int = 300,
) -> dict[str, Any]:
    if ttl_seconds < 1 or ttl_seconds > 3600:
        raise RemediationError("Doctor snapshot TTL must be between 1 and 3600 seconds")
    issued = issued_at or now_iso()
    issued_time = _parse_time(issued)
    expires, reasons = _bounded_report_expiry(report, issued_time, ttl_seconds)
    if report.fail_count:
        reasons.append("Doctor report contains failed checks")
    if any(item["code"] in AUTO_RECHECK_CODES for item in diagnostics_from_report(report)):
        reasons.append("Doctor report contains transient or freshness diagnostics")
    effective_ttl = max(0, int((expires - issued_time).total_seconds()))
    if effective_ttl == 0:
        reasons.append("a bound model observation is already expired")
    reasons = sorted(set(reasons))
    report_value = asdict(report)
    snapshot: dict[str, Any] = {
        "schema_version": "valp-doctor-snapshot.v1",
        "issued_at": issued,
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "requested_ttl_seconds": ttl_seconds,
        "effective_ttl_seconds": effective_ttl,
        "reuse_eligible": not reasons,
        "ineligible_reasons": reasons,
        "workspace": str(root.resolve()),
        "dependency_fingerprint": workspace_fingerprint(root)["digest"],
        "report_digest": digest(report_value),
        "report": report_value,
    }
    snapshot["snapshot_digest"] = digest(snapshot)
    return snapshot


def doctor_report_from_dict(value: dict[str, Any]) -> DoctorReport:
    try:
        checks = [
            DoctorCheck(
                id=str(item["id"]),
                title=str(item["title"]),
                status=str(item["status"]),
                message=str(item["message"]),
                evidence=[str(evidence) for evidence in item.get("evidence") or []],
                suggestion=str(item["suggestion"]) if item.get("suggestion") is not None else None,
            )
            for item in value.get("checks") or []
            if isinstance(item, dict)
        ]
        return DoctorReport(
            workspace=str(value["workspace"]),
            generated_at=str(value["generated_at"]),
            status=str(value["status"]),
            pass_count=int(value["pass_count"]),
            warn_count=int(value["warn_count"]),
            fail_count=int(value["fail_count"]),
            checks=checks,
            capability_passports=[
                dict(item) for item in value.get("capability_passports") or [] if isinstance(item, dict)
            ],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise RemediationError("Doctor snapshot contains an invalid embedded report") from error


def validate_doctor_snapshot(
    snapshot: dict[str, Any],
    root: Path,
    *,
    evaluated_at: str | None = None,
    max_ttl_seconds: int | None = None,
) -> DoctorReport:
    expected_snapshot_digest = digest(_without_digest(snapshot, "snapshot_digest"))
    if snapshot.get("snapshot_digest") != expected_snapshot_digest:
        raise RemediationError("Doctor snapshot digest does not match canonical content")
    if snapshot.get("workspace") != str(root.resolve()):
        raise RemediationError("Doctor snapshot belongs to a different workspace")
    if snapshot.get("reuse_eligible") is not True:
        reasons = ", ".join(str(item) for item in snapshot.get("ineligible_reasons") or [])
        raise RemediationError(f"Doctor snapshot is not reusable: {reasons or 'unspecified reason'}")
    if max_ttl_seconds is not None and int(snapshot.get("requested_ttl_seconds") or 0) > max_ttl_seconds:
        raise RemediationError("Doctor snapshot TTL exceeds the current reuse policy")
    now = _parse_time(evaluated_at or now_iso())
    if now >= _parse_time(str(snapshot.get("expires_at") or "1970-01-01T00:00:00Z")):
        raise RemediationError("Doctor snapshot expired")
    current_fingerprint = workspace_fingerprint(root)["digest"]
    if snapshot.get("dependency_fingerprint") != current_fingerprint:
        raise RemediationError("Doctor snapshot dependency fingerprint changed")
    report_value = snapshot.get("report")
    if not isinstance(report_value, dict) or snapshot.get("report_digest") != digest(report_value):
        raise RemediationError("Doctor snapshot report digest does not match embedded report")
    return doctor_report_from_dict(report_value)


def collect_doctor_with_snapshot(
    root: Path,
    snapshot_path: Path,
    *,
    ttl_seconds: int = 300,
    collector: Callable[[Path], DoctorReport] = collect_doctor_report,
    evaluated_at: str | None = None,
) -> tuple[DoctorReport, dict[str, Any]]:
    evaluated = evaluated_at or now_iso()
    reason = "snapshot absent"
    if snapshot_path.exists():
        try:
            snapshot = read_json_object(snapshot_path)
            report = validate_doctor_snapshot(
                snapshot,
                root,
                evaluated_at=evaluated,
                max_ttl_seconds=ttl_seconds,
            )
            return report, {
                "status": "reused",
                "snapshot_path": str(snapshot_path.resolve()),
                "snapshot_digest": snapshot["snapshot_digest"],
                "expires_at": snapshot["expires_at"],
            }
        except RemediationError as error:
            reason = str(error)
    report = collector(root.resolve())
    snapshot = build_doctor_snapshot(
        report,
        root,
        issued_at=evaluated,
        ttl_seconds=ttl_seconds,
    )
    write_json_atomic(snapshot_path, snapshot)
    return report, {
        "status": "refreshed",
        "reason": reason,
        "snapshot_path": str(snapshot_path.resolve()),
        "snapshot_digest": snapshot["snapshot_digest"],
        "expires_at": snapshot["expires_at"],
    }


def _check_diagnostic_code(check_id: str) -> str:
    if check_id == "git_repository":
        return "GIT_REPOSITORY_UNAVAILABLE"
    if check_id == "git_tracking":
        return "WORKTREE_TRACKING_DIVERGED"
    if check_id == "git_worktree_clean":
        return "WORKTREE_DIRTY"
    if check_id in {"valp_entrypoint", "python"}:
        return "INSTALLATION_INCOMPLETE"
    if check_id == "json_syntax":
        return "SOURCE_SYNTAX_INVALID"
    if check_id.startswith("audit_"):
        return "EXAMPLE_AUDIT_FAILED"
    if check_id == "task_audit":
        return "TASK_AUDIT_FAILED"
    if check_id.startswith("runtime_"):
        return "RUNTIME_PROBE_FAILED"
    return "DOCTOR_CHECK_DEGRADED"


def _repairability(code: str) -> str:
    if code in AUTO_RECHECK_CODES:
        return "bounded_recheck"
    if code in PROTECTED_CODES:
        return "protected_operator_action"
    return "operator_review"


def diagnostics_from_report(report: DoctorReport) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for check in report.checks:
        if check.status == "pass" or check.id == "ignored_residue":
            continue
        code = _check_diagnostic_code(check.id)
        diagnostics.append(
            {
                "diagnostic_id": f"check:{check.id}",
                "code": code,
                "severity": check.status,
                "subject": f"doctor-check:{check.id}",
                "observed_status": check.status,
                "repairability": _repairability(code),
                "evidence": list(check.evidence),
            }
        )

    for passport in report.capability_passports:
        principal = str(passport.get("principal_id") or passport.get("agent_id") or "unknown")
        subject = f"agent:{principal}"
        local = passport.get("local_installation") if isinstance(passport.get("local_installation"), dict) else {}
        live = passport.get("live_callability") if isinstance(passport.get("live_callability"), dict) else {}
        identity = passport.get("model_identity") if isinstance(passport.get("model_identity"), dict) else {}
        observed = identity.get("observed_model") if isinstance(identity.get("observed_model"), dict) else {}
        mismatch = identity.get("mismatch") if isinstance(identity.get("mismatch"), dict) else {}
        probe = identity.get("model_probe") if isinstance(identity.get("model_probe"), dict) else {}
        session = probe.get("session_identity") if isinstance(probe.get("session_identity"), dict) else {}

        installation_status = str(local.get("status") or "unknown")
        if installation_status in {"missing", "absent", "unknown"}:
            diagnostics.append(
                {
                    "diagnostic_id": f"{subject}:installation",
                    "code": "INSTALLATION_INCOMPLETE",
                    "severity": "warn",
                    "subject": subject,
                    "observed_status": installation_status,
                    "repairability": "protected_operator_action",
                    "evidence": [str(local.get("source") or "installation source unavailable")],
                }
            )
        live_status = str(live.get("status") or "unknown")
        if live_status in {"fail", "unknown"}:
            diagnostics.append(
                {
                    "diagnostic_id": f"{subject}:live-callability",
                    "code": "AGENT_NOT_CALLABLE",
                    "severity": "fail" if live_status == "fail" else "warn",
                    "subject": subject,
                    "observed_status": live_status,
                    "repairability": "bounded_recheck",
                    "evidence": [str(live.get("runtime") or "runtime unavailable")],
                }
            )
        if observed.get("freshness") == "stale":
            diagnostics.append(
                {
                    "diagnostic_id": f"{subject}:model-freshness",
                    "code": "MODEL_OBSERVATION_STALE",
                    "severity": "warn",
                    "subject": subject,
                    "observed_status": "stale",
                    "repairability": "bounded_recheck",
                    "evidence": [str(identity.get("freshness_evaluated_at") or report.generated_at)],
                }
            )
        if mismatch.get("status") == "mismatch":
            diagnostics.append(
                {
                    "diagnostic_id": f"{subject}:model-binding",
                    "code": "MODEL_BINDING_MISMATCH",
                    "severity": "fail",
                    "subject": subject,
                    "observed_status": "mismatch",
                    "repairability": "bounded_recheck",
                    "evidence": [str(mismatch.get("details") or "model binding differs")],
                }
            )
        if str(session.get("status") or "unknown") != "known":
            diagnostics.append(
                {
                    "diagnostic_id": f"{subject}:session-identity",
                    "code": "SESSION_IDENTITY_UNKNOWN",
                    "severity": "warn",
                    "subject": subject,
                    "observed_status": str(session.get("status") or "unknown"),
                    "repairability": "bounded_recheck",
                    "evidence": [str(session.get("source") or "session identity unavailable")],
                }
            )
    return sorted(diagnostics, key=lambda item: (item["code"], item["subject"], item["diagnostic_id"]))


def ranked_hypotheses(diagnostics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hypotheses: list[dict[str, Any]] = []
    for diagnostic in diagnostics:
        code = diagnostic["code"]
        subject = diagnostic["subject"]
        evidence = list(diagnostic.get("evidence") or [])
        if code in AUTO_RECHECK_CODES:
            candidates = (
                ("TRANSIENT_OR_STALE_OBSERVATION", 0.60, "doctor.recheck"),
                ("PERSISTENT_BINDING_OR_RUNTIME_DRIFT", 0.30, "operator.inspect-config"),
                ("UNSUPPORTED_RUNTIME_CAPABILITY", 0.10, "operator.inspect-adapter"),
            )
        else:
            candidates = (
                ("PERSISTENT_LOCAL_STATE", 0.70, "operator.inspect-evidence"),
                ("STALE_OR_INCOMPLETE_OBSERVATION", 0.20, "doctor.recheck"),
                ("UNSUPPORTED_ENVIRONMENT", 0.10, "operator.inspect-environment"),
            )
        for index, (cause, confidence, next_probe) in enumerate(candidates, 1):
            hypotheses.append(
                {
                    "hypothesis_id": f"{diagnostic['diagnostic_id']}:h{index}",
                    "diagnostic_id": diagnostic["diagnostic_id"],
                    "cause_code": cause,
                    "confidence": confidence,
                    "subject": subject,
                    "evidence": evidence,
                    "next_probe": next_probe,
                }
            )
    return hypotheses


def build_repair_plan(
    report: DoctorReport,
    root: Path,
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    generated = generated_at or now_iso()
    diagnostics = diagnostics_from_report(report)
    hypotheses = ranked_hypotheses(diagnostics)
    recheck_diagnostics = [item for item in diagnostics if item["code"] in AUTO_RECHECK_CODES]
    protected_diagnostics = [item for item in diagnostics if item["code"] in PROTECTED_CODES]
    fingerprint = workspace_fingerprint(root)
    actions: list[dict[str, Any]] = []
    if recheck_diagnostics:
        actions.append(
            {
                "action_id": "action-1",
                "kind": "doctor.recheck",
                "risk": "low",
                "target": "current-workspace",
                "mutation_surface": "none",
                "preconditions": ["workspace fingerprint matches plan", "action remains read-only"],
                "expected_changed": [item["diagnostic_id"] for item in recheck_diagnostics],
                "must_remain_unchanged": [
                    "agent_config",
                    "auth",
                    "mcp_config",
                    "task_evidence",
                ],
                "postconditions": [
                    f"diagnostic_resolved:{item['code']}:{item['subject']}"
                    for item in recheck_diagnostics
                ],
                "timeout_seconds": 30,
                "max_attempts": 1,
                "reversible": True,
                "verification_strength": "deterministic",
            }
        )

    if not diagnostics:
        status = NOT_REQUIRED
        strategy = "no_action"
        risk = "none"
    elif actions:
        status = READY
        strategy = "bounded_recheck_then_stop"
        risk = "low"
    elif protected_diagnostics:
        status = APPROVAL_REQUIRED
        strategy = "separate_configuration_or_source_task"
        risk = "protected"
    else:
        status = BLOCKED
        strategy = "operator_review"
        risk = "medium"

    plan: dict[str, Any] = {
        "schema_version": "valp-repair-plan.v1",
        "generated_at": generated,
        "workspace": str(root.resolve()),
        "workspace_fingerprint": fingerprint["digest"],
        "pre_state_digest": observation_digest(report),
        "status": status,
        "risk_classification": risk,
        "selected_strategy": strategy,
        "diagnostics": diagnostics,
        "hypotheses": hypotheses,
        "actions": actions,
        "approval": {
            "required": status == APPROVAL_REQUIRED,
            "status": "required" if status == APPROVAL_REQUIRED else "not_required",
            "deferred_mutation_required": bool(protected_diagnostics),
            "protected_categories": sorted(
                {item["code"] for item in protected_diagnostics}
            ),
        },
        "stop_conditions": [
            "workspace fingerprint changed",
            "action is not in the closed reference vocabulary",
            "postcondition remains unresolved",
            "protected mutation is required",
            "observed state diverges outside the counterfactual contract",
        ],
    }
    plan["plan_digest"] = digest(plan)
    plan["plan_id"] = f"repair-{plan['plan_digest'][7:23]}"
    plan["plan_digest"] = digest(_without_digest(plan, "plan_digest"))
    return plan


def validate_plan_digest(plan: dict[str, Any]) -> None:
    expected = digest(_without_digest(plan, "plan_digest"))
    if plan.get("plan_digest") != expected:
        raise RemediationError("repair plan digest does not match canonical content")


def _diagnostic_keys(diagnostics: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(str(item.get("code")), str(item.get("subject"))) for item in diagnostics}


def make_proof_certificate(
    *,
    root: Path,
    plan: dict[str, Any],
    report: DoctorReport,
    resolved: list[dict[str, str]],
    issued_at: str,
    ttl_seconds: int = 300,
) -> dict[str, Any] | None:
    issued_time = _parse_time(issued_at)
    expires_at, _reasons = _bounded_report_expiry(report, issued_time, ttl_seconds)
    if expires_at <= issued_time:
        return None
    certificate: dict[str, Any] = {
        "schema_version": "valp-doctor-proof-certificate.v1",
        "certificate_id": "pending",
        "issued_at": issued_at,
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "subject": "doctor-remediation",
        "subject_digest": observation_digest(report),
        "dependency_fingerprint": workspace_fingerprint(root)["digest"],
        "verifier": {"id": "valp.doctor", "version": "reference-v1"},
        "binding": {"workspace": str(root.resolve()), "plan_digest": plan["plan_digest"]},
        "claims": resolved,
        "evidence_refs": [f"repair-plan:{plan['plan_id']}"],
        "status": "valid",
    }
    certificate["certificate_digest"] = digest(certificate)
    certificate["certificate_id"] = f"proof-{certificate['certificate_digest'][7:23]}"
    certificate["certificate_digest"] = digest(
        _without_digest(certificate, "certificate_digest")
    )
    return certificate


def execute_repair_plan(
    plan: dict[str, Any],
    root: Path,
    *,
    doctor_collector: Callable[[Path], DoctorReport] = collect_doctor_report,
    executed_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    validate_plan_digest(plan)
    if plan.get("status") != READY:
        raise RemediationError(f"repair plan is not executable: {plan.get('status')}")
    if (plan.get("approval") or {}).get("required"):
        raise RemediationError("reference executor cannot apply approval-gated repair plans")
    current_fingerprint = workspace_fingerprint(root)["digest"]
    if plan.get("workspace_fingerprint") != current_fingerprint:
        raise RemediationError("workspace fingerprint changed after the repair plan was created")

    actions = plan.get("actions") or []
    for action in actions:
        if action.get("kind") not in ALLOWED_REFERENCE_ACTIONS:
            raise RemediationError(f"unsupported repair action: {action.get('kind')}")
        if action.get("risk") != "low" or action.get("mutation_surface") != "none":
            raise RemediationError("reference executor accepts only low-risk read-only actions")
        if int(action.get("max_attempts") or 0) != 1:
            raise RemediationError("reference executor requires exactly one bounded attempt")

    timestamp = executed_at or now_iso()
    try:
        post_report = doctor_collector(root.resolve())
    except Exception as error:
        targeted_failures = [
            {"code": code, "subject": subject}
            for code, subject in sorted(
                {
                    (str(item.get("code")), str(item.get("subject")))
                    for item in plan.get("diagnostics") or []
                    if item.get("code") in AUTO_RECHECK_CODES
                }
            )
        ]
        receipt = {
            "schema_version": "valp-repair-receipt.v1",
            "receipt_id": "pending",
            "executed_at": timestamp,
            "plan_id": plan["plan_id"],
            "plan_digest": plan["plan_digest"],
            "workspace": str(root.resolve()),
            "pre_state_digest": plan["pre_state_digest"],
            "post_state_digest": plan["pre_state_digest"],
            "status": "failed",
            "action_results": [
                {
                    "action_id": str(action.get("action_id")),
                    "kind": str(action.get("kind")),
                    "status": "failed",
                    "attempts": 1,
                    "result": f"Doctor recheck failed: {type(error).__name__}",
                }
                for action in actions
            ],
            "verification": {
                "status": "fail",
                "verifier": "valp.doctor",
                "resolved": [],
                "unresolved": targeted_failures,
            },
            "approval_binding": {"status": "not_required"},
            "rollback": {"status": "not_required", "reason": "reference actions are read-only"},
            "proof_certificate_digest": None,
        }
        receipt["receipt_digest"] = digest(receipt)
        receipt["receipt_id"] = f"repair-receipt-{receipt['receipt_digest'][7:23]}"
        receipt["receipt_digest"] = digest(_without_digest(receipt, "receipt_digest"))
        return receipt, None
    post_diagnostics = diagnostics_from_report(post_report)
    targeted = {
        (item["code"], item["subject"])
        for item in plan.get("diagnostics") or []
        if item.get("code") in AUTO_RECHECK_CODES
    }
    remaining = _diagnostic_keys(post_diagnostics)
    resolved = [
        {"code": code, "subject": subject}
        for code, subject in sorted(targeted - remaining)
    ]
    unresolved = [
        {"code": code, "subject": subject}
        for code, subject in sorted(targeted & remaining)
    ]
    post_workspace_fingerprint = workspace_fingerprint(root)["digest"]
    if post_workspace_fingerprint != plan["workspace_fingerprint"]:
        unresolved.append(
            {
                "code": "COUNTERFACTUAL_DIVERGENCE",
                "subject": "workspace:dependency-fingerprint",
            }
        )
    outcome = "fixed" if targeted and not unresolved else "blocked"
    action_results = [
        {
            "action_id": str(action.get("action_id")),
            "kind": str(action.get("kind")),
            "status": outcome,
            "attempts": 1,
            "result": "all targeted diagnostics resolved" if outcome == "fixed" else "one or more diagnostics remain",
        }
        for action in actions
    ]
    certificate = (
        make_proof_certificate(
            root=root,
            plan=plan,
            report=post_report,
            resolved=resolved,
            issued_at=timestamp,
        )
        if outcome == "fixed"
        else None
    )
    receipt: dict[str, Any] = {
        "schema_version": "valp-repair-receipt.v1",
        "receipt_id": "pending",
        "executed_at": timestamp,
        "plan_id": plan["plan_id"],
        "plan_digest": plan["plan_digest"],
        "workspace": str(root.resolve()),
        "pre_state_digest": plan["pre_state_digest"],
        "post_state_digest": observation_digest(post_report),
        "status": outcome,
        "action_results": action_results,
        "verification": {
            "status": "pass" if outcome == "fixed" else "fail",
            "verifier": "valp.doctor",
            "resolved": resolved,
            "unresolved": unresolved,
        },
        "approval_binding": {"status": "not_required"},
        "rollback": {"status": "not_required", "reason": "reference actions are read-only"},
        "proof_certificate_digest": certificate["certificate_digest"] if certificate else None,
    }
    receipt["receipt_digest"] = digest(receipt)
    receipt["receipt_id"] = f"repair-receipt-{receipt['receipt_digest'][7:23]}"
    receipt["receipt_digest"] = digest(_without_digest(receipt, "receipt_digest"))
    return receipt, certificate


def verify_repair_receipt(
    receipt: dict[str, Any],
    root: Path,
    *,
    doctor_collector: Callable[[Path], DoctorReport] = collect_doctor_report,
    verified_at: str | None = None,
) -> dict[str, Any]:
    expected = digest(_without_digest(receipt, "receipt_digest"))
    if receipt.get("receipt_digest") != expected:
        raise RemediationError("repair receipt digest does not match canonical content")
    if receipt.get("status") != "fixed" or (receipt.get("verification") or {}).get("status") != "pass":
        return {
            "schema_version": "valp-repair-verification.v1",
            "verified_at": verified_at or now_iso(),
            "receipt_id": receipt.get("receipt_id"),
            "receipt_digest": receipt.get("receipt_digest"),
            "status": "not_proven",
            "current_state_digest": None,
            "regressed": [],
        }
    report = doctor_collector(root.resolve())
    current = _diagnostic_keys(diagnostics_from_report(report))
    proved = {
        (str(item.get("code")), str(item.get("subject")))
        for item in (receipt.get("verification") or {}).get("resolved") or []
    }
    regressed = [
        {"code": code, "subject": subject}
        for code, subject in sorted(proved & current)
    ]
    return {
        "schema_version": "valp-repair-verification.v1",
        "verified_at": verified_at or now_iso(),
        "receipt_id": receipt.get("receipt_id"),
        "receipt_digest": receipt.get("receipt_digest"),
        "status": "valid" if not regressed else "regressed",
        "current_state_digest": observation_digest(report),
        "regressed": regressed,
    }


class McpProcessProvider(Protocol):
    """The only mutation boundary exposed by the reference Recovery Kernel."""

    def restart(
        self, *, resource_id: str, resource_version: str, rollback_token: str
    ) -> dict[str, Any]: ...

    def rollback(
        self, *, resource_id: str, resource_version: str, rollback_token: str
    ) -> dict[str, Any]: ...


def recovery_resource_identity(resource_id: str, resource_version: str) -> dict[str, Any]:
    if not resource_id or not resource_version:
        raise RemediationError("recovery resource identity requires id and version")
    value = {
        "schema_version": "valp-recovery-resource.v1",
        "kind": "mcp.process",
        "resource_id": resource_id,
        "resource_version": resource_version,
    }
    value["resource_digest"] = digest(value)
    return value


def _recovery_action(resource: dict[str, Any], rollback_token: str) -> dict[str, Any]:
    if not rollback_token:
        raise RemediationError("recovery requires a provider rollback token")
    action = {
        "action_id": "action-1",
        "kind": RECOVERY_ACTION,
        "resource": resource,
        "timeout_seconds": 30,
        "max_attempts": 1,
        "rollback": {
            "kind": "provider.rollback",
            "token_digest": digest(rollback_token),
        },
        "verification": "independent-doctor-observation",
    }
    action["action_digest"] = digest(action)
    return action


def build_recovery_plan(
    report: DoctorReport,
    root: Path,
    *,
    resource_id: str,
    resource_version: str,
    rollback_token: str,
    generated_at: str | None = None,
    proof_ttl_seconds: int = 300,
) -> dict[str, Any]:
    """Create an approval-gated, single-action Recovery Kernel plan.

    The planned action is L2 authority. Planning itself is VALP-owned L1 work;
    this artifact records the action level and deliberately produces
    ``approval_required`` without restarting a provider.
    """
    if not 1 <= proof_ttl_seconds <= 3600:
        raise RemediationError("recovery proof TTL must be between 1 and 3600 seconds")
    resource = recovery_resource_identity(resource_id, resource_version)
    action = _recovery_action(resource, rollback_token)
    plan: dict[str, Any] = {
        "schema_version": "valp-recovery-plan.v1",
        "generated_at": generated_at or now_iso(),
        "workspace": str(root.resolve()),
        "source_identity": source_identity(root),
        "dependency_evidence": dependency_evidence(root),
        "pre_observation_digest": observation_digest(report),
        "diagnostics": diagnostics_from_report(report),
        "authority": {"mcp.process": "L2"},
        "action": action,
        "proof_ttl_seconds": proof_ttl_seconds,
        "status": APPROVAL_REQUIRED,
    }
    plan["plan_digest"] = digest(plan)
    plan["plan_id"] = f"recovery-{plan['plan_digest'][7:23]}"
    plan["plan_digest"] = digest(_without_digest(plan, "plan_digest"))
    return plan


def build_recovery_approval(
    plan: dict[str, Any],
    *,
    approval_id: str,
    approval_ref: str,
    approver_identity: str,
    approved_at: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    """Build caller-supplied L2 approval evidence; executors never mint it."""
    validate_recovery_plan(plan)
    if not approval_id or not approval_ref or not approver_identity:
        raise RemediationError("recovery approval requires identity and evidence reference")
    approved = approved_at or now_iso()
    expiry = expires_at or (
        _parse_time(approved) + timedelta(seconds=300)
    ).isoformat().replace("+00:00", "Z")
    if _parse_time(expiry) <= _parse_time(approved):
        raise RemediationError("recovery approval expiry must be after approval time")
    approval = {
        "schema_version": "valp-recovery-approval.v1",
        "approval_id": approval_id,
        "approval_ref": approval_ref,
        "approver_identity": approver_identity,
        "approved_at": approved,
        "expires_at": expiry,
        "plan_digest": plan["plan_digest"],
        "action_digest": plan["action"]["action_digest"],
        "authority": {"mcp.process": "L2"},
    }
    approval["approval_digest"] = digest(approval)
    return approval


def validate_recovery_plan(plan: dict[str, Any]) -> None:
    expected = digest(_without_digest(plan, "plan_digest"))
    if plan.get("schema_version") != "valp-recovery-plan.v1" or plan.get("plan_digest") != expected:
        raise RemediationError("recovery plan digest does not match canonical content")
    action = plan.get("action") or {}
    if action.get("kind") != RECOVERY_ACTION or int(action.get("max_attempts") or 0) != 1:
        raise RemediationError("recovery plan has an unsupported action")
    if action.get("action_digest") != digest(_without_digest(action, "action_digest")):
        raise RemediationError("recovery action digest does not match canonical content")
    rollback = action.get("rollback") or {}
    if rollback.get("kind") != "provider.rollback" or not rollback.get("token_digest"):
        raise RemediationError("recovery plan requires a provider rollback contract")


def _exclusive_recovery_lock(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".recovery.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RemediationError("recovery lock is already held; state is unknown") from error
    os.write(fd, str(os.getpid()).encode("ascii"))
    os.close(fd)
    return lock


def _recovery_disabled(directory: Path) -> bool:
    return (directory / RECOVERY_KILL_SWITCH).exists()


def _fsync_directory(directory: Path) -> None:
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError as error:
        raise RemediationError(f"cannot fsync recovery directory {directory}: {error}") from error
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_immutable(path: Path, value: dict[str, Any]) -> None:
    payload = canonical_json(value)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(path.parent)
    except FileExistsError:
        existing = read_json_object(path)
        if existing != value:
            raise RemediationError("immutable recovery artifact conflicts with existing content")


def _validate_recovery_approval(plan: dict[str, Any], approval: dict[str, Any], timestamp: str) -> None:
    expected = digest(_without_digest(approval, "approval_digest"))
    if (
        approval.get("schema_version") != "valp-recovery-approval.v1"
        or approval.get("approval_digest") != expected
        or not all(approval.get(field) for field in ("approval_id", "approval_ref", "approver_identity", "approved_at", "expires_at"))
        or approval.get("plan_digest") != plan["plan_digest"]
        or approval.get("action_digest") != plan["action"]["action_digest"]
        or (approval.get("authority") or {}).get("mcp.process") != "L2"
    ):
        raise RemediationError("recovery requires an exact-digest L2 approval")
    if _parse_time(timestamp) < _parse_time(str(approval["approved_at"])):
        raise RemediationError("recovery cannot execute before approval time")
    if _parse_time(timestamp) >= _parse_time(str(approval["expires_at"])):
        raise RemediationError("recovery approval expired")


def validate_recovery_receipt(
    receipt: dict[str, Any], plan: dict[str, Any], approval: dict[str, Any]
) -> None:
    if receipt.get("receipt_digest") != digest(_without_digest(receipt, "receipt_digest")):
        raise RemediationError("recovery receipt digest does not match canonical content")
    required = {
        "schema_version", "receipt_id", "receipt_digest", "executed_at", "plan_id",
        "plan_digest", "action_digest", "authority", "approval_binding", "status",
        "effect", "verification", "rollback", "proof_expires_at",
    }
    if (
        receipt.get("schema_version") != "valp-recovery-receipt.v1"
        or not required.issubset(receipt)
        or set(receipt) != required
        or (receipt.get("authority") or {}).get("mcp.process") != "L2"
        or receipt.get("status") not in {"fixed", "blocked", "unknown"}
        or not isinstance(receipt.get("effect"), dict)
        or not isinstance(receipt.get("verification"), dict)
        or (receipt.get("rollback") or {}).get("status") not in {"not_required", "full", "partial", "failed", "unknown"}
    ):
        raise RemediationError("recovery receipt does not satisfy the strict receipt contract")
    binding = receipt.get("approval_binding") or {}
    if (
        receipt.get("plan_id") != plan.get("plan_id")
        or receipt.get("plan_digest") != plan.get("plan_digest")
        or receipt.get("action_digest") != (plan.get("action") or {}).get("action_digest")
        or binding.get("approval_digest") != approval.get("approval_digest")
        or binding.get("approval_id") != approval.get("approval_id")
        or binding.get("approval_ref") != approval.get("approval_ref")
    ):
        raise RemediationError("recovery receipt binding does not match plan and approval")


def execute_recovery_plan(
    plan: dict[str, Any],
    root: Path,
    *,
    approval: dict[str, Any],
    provider: McpProcessProvider | None = None,
    rollback_token: str,
    doctor_collector: Callable[[Path], DoctorReport] = collect_doctor_report,
    recovery_dir: Path,
    dry_run: bool = False,
    executed_at: str | None = None,
) -> dict[str, Any]:
    """Consume a plan once through an injected provider and persist its receipt.

    The intent is written before the provider call. A restart after a crash that
    finds that intent cannot guess whether the external process changed state,
    so it fails closed as ``unknown`` rather than issuing a second restart.
    """
    validate_recovery_plan(plan)
    if plan.get("workspace") != str(root.resolve()):
        raise RemediationError("recovery plan belongs to a different workspace")
    if plan.get("source_identity", {}).get("digest") != source_identity(root)["digest"]:
        raise RemediationError("recovery source identity changed")
    if plan.get("dependency_evidence", {}).get("digest") != dependency_evidence(root)["digest"]:
        raise RemediationError("recovery dependency evidence changed")
    directory = recovery_dir.resolve()
    timestamp = executed_at or now_iso()
    _validate_recovery_approval(plan, approval, timestamp)
    if plan["action"]["rollback"]["token_digest"] != digest(rollback_token):
        raise RemediationError("recovery rollback token does not match the plan contract")
    if _recovery_disabled(directory):
        raise RemediationError("recovery is disabled by the recovery-dir kill-switch")
    receipt_path = directory / f"{plan['plan_id']}.receipt.json"
    if receipt_path.exists():
        receipt = read_json_object(receipt_path)
        validate_recovery_receipt(receipt, plan, approval)
        return receipt
    lock = _exclusive_recovery_lock(directory)
    try:
        if _recovery_disabled(directory):
            raise RemediationError("recovery is disabled by the recovery-dir kill-switch")
        if receipt_path.exists():
            receipt = read_json_object(receipt_path)
            validate_recovery_receipt(receipt, plan, approval)
            return receipt
        intent_path = directory / f"{plan['plan_id']}.intent.json"
        if intent_path.exists():
            raise RemediationError("recovery intent already exists; external effect is unknown")
        if dry_run:
            # Dry-runs are observations only: they consume no intent or receipt.
            return {
                "schema_version": "valp-recovery-dry-run.v1",
                "plan_id": plan["plan_id"],
                "plan_digest": plan["plan_digest"],
                "action_digest": plan["action"]["action_digest"],
                "approval_digest": approval["approval_digest"],
                "status": "dry_run",
                "effect": {"status": "not_called"},
            }
        intent = {
            "schema_version": "valp-recovery-intent.v1",
            "plan_digest": plan["plan_digest"],
            "action_digest": plan["action"]["action_digest"],
            "approval_digest": approval["approval_digest"],
            "created_at": timestamp,
            "state": "accepted",
        }
        intent["intent_digest"] = digest(intent)
        _write_immutable(intent_path, intent)
        if provider is None:
            effect, status, verification = {"status": "unknown", "reason": "provider unavailable"}, "unknown", {"status": "not_run"}
        else:
            try:
                effect = provider.restart(resource_id=plan["action"]["resource"]["resource_id"], resource_version=plan["action"]["resource"]["resource_version"], rollback_token=rollback_token)
            except Exception as error:
                effect, status, verification = {"status": "unknown", "error": type(error).__name__}, "unknown", {"status": "not_run"}
            else:
                try:
                    post = doctor_collector(root.resolve())
                    verification = {"status": "pass" if not diagnostics_from_report(post) else "fail", "post_observation_digest": observation_digest(post)}
                    status = "fixed" if verification["status"] == "pass" else "blocked"
                except Exception as error:
                    status = "blocked"
                    verification = {"status": "fail", "error": type(error).__name__}
        rollback: dict[str, Any] = {"status": "not_required"}
        if status == "blocked":
            try:
                rollback_effect = provider.rollback(resource_id=plan["action"]["resource"]["resource_id"], resource_version=plan["action"]["resource"]["resource_version"], rollback_token=rollback_token)
                rollback = {"status": str(rollback_effect.get("status") or "partial"), "effect": rollback_effect}
            except Exception as error:
                rollback = {"status": "failed", "error": type(error).__name__}
        receipt = {"schema_version": "valp-recovery-receipt.v1", "executed_at": timestamp, "plan_id": plan["plan_id"], "plan_digest": plan["plan_digest"], "action_digest": plan["action"]["action_digest"], "authority": {"mcp.process": "L2"}, "approval_binding": {"approval_id": approval["approval_id"], "approval_ref": approval["approval_ref"], "approval_digest": approval["approval_digest"]}, "status": status, "effect": effect, "verification": verification, "rollback": rollback, "proof_expires_at": ( _parse_time(timestamp) + timedelta(seconds=plan["proof_ttl_seconds"]) ).isoformat().replace("+00:00", "Z")}
        receipt["receipt_digest"] = digest(receipt)
        receipt["receipt_id"] = f"recovery-receipt-{receipt['receipt_digest'][7:23]}"
        receipt["receipt_digest"] = digest(_without_digest(receipt, "receipt_digest"))
        _write_immutable(receipt_path, receipt)
        return receipt
    finally:
        lock.unlink(missing_ok=True)


def write_json_atomic(path: Path, value: dict[str, Any]) -> Path:
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    payload = canonical_json(value)
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RemediationError(f"cannot read remediation artifact {path}: {error}") from error
    if not isinstance(value, dict):
        raise RemediationError(f"remediation artifact must be a JSON object: {path}")
    return value
