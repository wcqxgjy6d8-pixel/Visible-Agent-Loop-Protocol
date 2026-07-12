from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath
from typing import Any

from .delegation import validate_delegation_policy
from .risk import classify_approval_risks
from .submission import dependency_order_errors, validate_submission_dependencies


PASS = "pass"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"

VISIBLE_ATTENTION_REQUIRED_PROFILES = {
    "software-code",
    "apple-app",
    "web-frontend",
    "research",
    "document-artifact",
    "agent-runtime",
    "ops-release",
    "prototype",
}

@dataclass
class AuditItem:
    id: str
    title: str
    status: str
    message: str
    evidence: list[str]


@dataclass
class AuditReport:
    task_dir: str
    status: str
    pass_count: int
    warn_count: int
    fail_count: int
    skip_count: int
    items: list[AuditItem]


class TaskAudit:
    def __init__(self, task_dir: Path, strict: bool = False) -> None:
        self.task_dir = task_dir.resolve()
        self.strict = strict
        self.jsonl_errors: dict[str, list[str]] = {}
        self.state = self._load_json("state.json")
        self.routing = self._load_json("routing.json")
        self.feedback = self._load_json("routing-feedback.json")
        self.automation_policy = self._load_json("automation-policy.json")
        self.context_pack = self._load_json("context-pack.json")
        self.learning_feedback = self._load_json("learning-feedback.json")
        self.evidence_status = self._load_json("evidence-status.json")
        self.skill_recommendations = self._load_json("skill-recommendations.json")
        self.agent_recommendations = self._load_json("agent-recommendations.json")
        self.submission_dependencies = self._load_json("submission-dependencies.json")
        self.delegation_policy = self._load_json("delegation-policy.json")
        self.historical_audit_boundary = self._load_json("historical-audit-boundary.json")
        self.attention_map = self._load_json("attention-map.json")
        self.context_selection = self._load_json("context-selection.json")
        self.mask_list = self._load_json("mask-list.json")
        self.evidence_board = self._load_json("evidence-board.json")
        self.correction_cycle = self._load_json("correction-cycle.json")
        self.receipts = self._load_jsonl("dispatch-receipts.jsonl")
        self.approval_requests = self._load_jsonl("approvals/requested.jsonl")
        self.approval_decisions = self._load_jsonl("approvals/user-decisions.jsonl")
        self.task_text = self._read_text("task.md")
        self.final_synthesis_text = self._read_first_existing(
            ["final-synthesis.md", "evidence/final-synthesis.md"]
        )

    def run(self) -> AuditReport:
        items = [
            self.check_profile_and_routing(),
            self.check_runtime_adapter_and_state_mapping(),
            self.check_local_overlay(),
            self.check_selected_agents_and_context(),
            self.check_provider_matrix(),
            self.check_runtime_preflight(),
            self.check_routing_confidence(),
            self.check_automation_policy(),
            self.check_delegation_policy(),
            self.check_visible_attention(),
            self.check_context_pack(),
            self.check_skill_recommendations(),
            self.check_squad_routing(),
            self.check_dispatch_receipts(),
            self.check_submission_dependencies(),
            self.check_expected_evidence(),
            self.check_correction_cycle(),
            self.check_agent_recommendations(),
            self.check_claim_evidence(),
            self.check_verification(),
            self.check_review_findings(),
            self.check_approvals(),
            self.check_final_synthesis(),
            self.check_routing_feedback(),
            self.check_learning_feedback(),
        ]

        if self.strict:
            items = [
                AuditItem(i.id, i.title, FAIL if i.status == WARN else i.status, i.message, i.evidence)
                for i in items
            ]

        pass_count = sum(1 for i in items if i.status == PASS)
        warn_count = sum(1 for i in items if i.status == WARN)
        fail_count = sum(1 for i in items if i.status == FAIL)
        skip_count = sum(1 for i in items if i.status == SKIP)
        status = FAIL if fail_count else WARN if warn_count else PASS

        return AuditReport(
            task_dir=str(self.task_dir),
            status=status,
            pass_count=pass_count,
            warn_count=warn_count,
            fail_count=fail_count,
            skip_count=skip_count,
            items=items,
        )

    def check_profile_and_routing(self) -> AuditItem:
        evidence = self._existing(["routing.json", "state.json"])
        if not self.routing:
            return self._fail("profile_routing", "Profile and routing are recorded", "Missing routing.json", evidence)
        profile = self.routing.get("profile") or self.state.get("profile")
        if not profile:
            return self._fail("profile_routing", "Profile and routing are recorded", "routing.json/state.json has no profile", evidence)
        return self._pass("profile_routing", "Profile and routing are recorded", f"Profile recorded: {profile}", evidence)

    def check_runtime_adapter_and_state_mapping(self) -> AuditItem:
        evidence = self._existing(["routing.json", "state.json"])
        runtime = self.routing.get("runtime_adapter") or self.state.get("runtime_adapter")
        mapping = self.routing.get("runtime_task_state_mapping") or self.state.get("runtime_task_state_mapping")
        if not runtime:
            return self._fail("runtime_adapter", "Runtime adapter and task state mapping are recorded", "Missing runtime_adapter", evidence)
        if not mapping:
            return self._fail("runtime_adapter", "Runtime adapter and task state mapping are recorded", "Missing runtime_task_state_mapping", evidence)
        suspension = self.state.get("suspension") or {}
        if self.state.get("status") == "suspended" or suspension.get("status") == "waiting":
            return self._fail(
                "runtime_adapter",
                "Runtime adapter and task state mapping are recorded",
                "Task is suspended and cannot satisfy completion audit",
                evidence,
            )
        return self._pass("runtime_adapter", "Runtime adapter and task state mapping are recorded", "Runtime adapter and state mapping found", evidence)

    def check_local_overlay(self) -> AuditItem:
        evidence = self._existing(["routing.json", "state.json"])
        overlay = self.routing.get("local_overlay") or self.state.get("local_overlay") or {}
        if not overlay:
            return self._skip("local_overlay", "Local overlay inputs are recorded when used", "No local overlay declared", evidence)
        used = overlay.get("used")
        if used is False:
            return self._skip("local_overlay", "Local overlay inputs are recorded when used", "Local overlay explicitly not used", evidence)
        if overlay.get("ref") or overlay.get("note"):
            return self._pass("local_overlay", "Local overlay inputs are recorded when used", "Local overlay use is recorded", evidence)
        return self._fail("local_overlay", "Local overlay inputs are recorded when used", "Local overlay appears used but has no ref or note", evidence)

    def check_selected_agents_and_context(self) -> AuditItem:
        evidence = self._existing(["routing.json", "state.json"])
        agents = self._selected_agents()
        policies = self.routing.get("selected_agent_context_policies") or self.state.get("context_policies") or {}
        if not agents:
            return self._fail("selected_agents_context", "Selected agents and context policies are recorded", "No selected_agents recorded", evidence)
        missing = [agent for agent in agents if agent not in policies]
        if missing:
            return self._fail(
                "selected_agents_context",
                "Selected agents and context policies are recorded",
                f"Selected agents recorded, but context policy missing for: {', '.join(missing)}",
                evidence,
            )
        required = {"soft_warning_pct", "hard_compression_pct", "emergency_stop_pct"}
        incomplete = [
            agent
            for agent in agents
            if not required.issubset(set((policies.get(agent) or {}).keys()))
        ]
        if incomplete:
            return self._fail(
                "selected_agents_context",
                "Selected agents and context policies are recorded",
                "Context policy missing threshold fields for: " + ", ".join(incomplete),
                evidence,
            )
        compressed_needed = []
        for agent in agents:
            policy = policies.get(agent) or {}
            current = policy.get("current_context_pct")
            hard = policy.get("hard_compression_pct")
            if policy.get("compression_required"):
                compressed_needed.append(f"{agent}=compression_required")
                continue
            if isinstance(current, (int, float)) and isinstance(hard, (int, float)) and current >= hard:
                compressed_needed.append(f"{agent}=context {current}% >= hard {hard}%")
        if compressed_needed:
            return self._fail(
                "selected_agents_context",
                "Selected agents and context policies are recorded",
                "Compression required before dispatch: " + ", ".join(compressed_needed),
                evidence,
            )
        return self._pass("selected_agents_context", "Selected agents and context policies are recorded", "Selected agents and context policies found", evidence)

    def check_provider_matrix(self) -> AuditItem:
        evidence = self._existing(["routing.json", "state.json"])
        matrix = self.routing.get("provider_matrix") or self.state.get("provider_matrix") or {}
        if not matrix:
            return self._fail("provider_matrix", "Provider matrix fields needed for the task are recorded", "Missing provider_matrix", evidence)
        providers = matrix.get("providers")
        if isinstance(providers, dict) and providers:
            return self._pass("provider_matrix", "Provider matrix fields needed for the task are recorded", "Provider matrix has provider records", evidence)
        if isinstance(providers, list) and providers:
            return self._pass("provider_matrix", "Provider matrix fields needed for the task are recorded", "Provider matrix has provider records", evidence)
        if matrix.get("status") == "scanned":
            return self._warn("provider_matrix", "Provider matrix fields needed for the task are recorded", "Provider matrix is referenced as scanned but no provider details are local", evidence)
        return self._fail("provider_matrix", "Provider matrix fields needed for the task are recorded", "provider_matrix has no provider records", evidence)

    def check_runtime_preflight(self) -> AuditItem:
        evidence = self._existing(["routing.json", "runtime-preflight.json", "state.json"])
        runtime = self.routing.get("runtime_adapter") or self.state.get("runtime_adapter") or {}
        if runtime.get("class") == "manual":
            return self._skip("runtime_preflight", "Runtime preflight is recorded for Full Mode adapters", "Manual Mode has no runtime preflight", evidence)
        matrix = self.routing.get("provider_matrix") or {}
        preflight = matrix.get("runtime_preflight") or runtime.get("preflight") or self._load_json("runtime-preflight.json")
        if not preflight:
            return self._fail("runtime_preflight", "Runtime preflight is recorded for Full Mode adapters", "Missing runtime preflight record", evidence)
        agents = preflight.get("agents") or {}
        failed = [name for name, record in agents.items() if isinstance(record, dict) and record.get("status") == "fail"]
        if preflight.get("status") == "fail":
            return self._fail("runtime_preflight", "Runtime preflight is recorded for Full Mode adapters", "Runtime preflight status is fail", evidence)
        if failed:
            return self._fail("runtime_preflight", "Runtime preflight is recorded for Full Mode adapters", "Runtime preflight failed for: " + ", ".join(failed), evidence)
        if preflight.get("status") == "warn" or any(isinstance(record, dict) and record.get("status") == "warn" for record in agents.values()):
            return self._warn("runtime_preflight", "Runtime preflight is recorded for Full Mode adapters", "Runtime preflight has warnings", evidence)
        return self._pass("runtime_preflight", "Runtime preflight is recorded for Full Mode adapters", "Runtime preflight recorded with no failures", evidence)

    def check_routing_confidence(self) -> AuditItem:
        evidence = self._existing(["routing.json", "state.json"])
        missing_recorded = "capabilities_missing" in self.routing or "capabilities_missing" in self.state
        confidence = self.routing.get("routing_confidence") or self.state.get("routing_confidence")
        rejected_present = "rejected_candidates" in self.routing
        scores_present = "candidate_scores" in self.routing
        if missing_recorded and confidence and (rejected_present or scores_present):
            return self._pass(
                "routing_confidence",
                "Routing confidence, missing capabilities, and relevant rejected candidates are recorded",
                "Routing confidence and candidate evidence found",
                evidence,
            )
        details = []
        if not confidence:
            details.append("routing_confidence")
        if not missing_recorded:
            details.append("capabilities_missing")
        if not rejected_present and not scores_present:
            details.append("rejected_candidates or candidate_scores")
        return self._warn(
            "routing_confidence",
            "Routing confidence, missing capabilities, and relevant rejected candidates are recorded",
            "Missing advisory routing fields: " + ", ".join(details),
            evidence,
        )

    def check_automation_policy(self) -> AuditItem:
        evidence = self._existing(["automation-policy.json", "routing.json", "state.json"])
        runtime = self.routing.get("runtime_adapter") or self.state.get("runtime_adapter") or {}
        declared = self.routing.get("automation_policy") or self.state.get("automation_policy") or self.automation_policy
        required = bool(declared) or runtime.get("class") != "manual"
        if not required:
            return self._skip("automation_policy", "Automation policy records automatic progress and stop conditions", "Manual task without automation policy requirement", evidence)
        if not self.automation_policy:
            return self._fail("automation_policy", "Automation policy records automatic progress and stop conditions", "Missing automation-policy.json", evidence)
        if self.automation_policy.get("schema_version") != "valp-automation-policy.v1":
            return self._fail("automation_policy", "Automation policy records automatic progress and stop conditions", "automation-policy.json has wrong or missing schema_version", evidence)
        missing = [
            key
            for key in ["mode", "risk_classification", "selected_action", "audit_grade", "stop_conditions"]
            if not self.automation_policy.get(key)
        ]
        if missing:
            return self._fail("automation_policy", "Automation policy records automatic progress and stop conditions", "Missing automation policy fields: " + ", ".join(missing), evidence)
        if (
            self.automation_policy.get("approval_required")
            and self.automation_policy.get("selected_action") != "block_for_approval"
            and not self._approval_ledger_result()["approved"]
        ):
            return self._fail("automation_policy", "Automation policy records automatic progress and stop conditions", "Approval-required automation must select block_for_approval until approval evidence exists", evidence)
        if not isinstance(self.automation_policy.get("stop_conditions"), list) or not self.automation_policy.get("stop_conditions"):
            return self._fail("automation_policy", "Automation policy records automatic progress and stop conditions", "Automation policy needs stop_conditions", evidence)
        return self._pass(
            "automation_policy",
            "Automation policy records automatic progress and stop conditions",
            f"Automation action: {self.automation_policy.get('selected_action')}; audit grade: {self.automation_policy.get('audit_grade')}",
            evidence,
        )

    def check_delegation_policy(self) -> AuditItem:
        evidence = self._existing(["delegation-policy.json", "routing.json", "state.json"])
        expected_marker = {"status": "recorded", "ref": "delegation-policy.json"}
        routing_marker = self.routing.get("delegation_policy") or {}
        state_marker = self.state.get("delegation_policy") or {}
        required = bool(routing_marker) or bool(state_marker) or bool(self.delegation_policy)
        if not required:
            return self._skip(
                "delegation_policy",
                "Delegated live self-modification is forbidden",
                "Legacy task without a declared delegation policy",
                evidence,
            )
        if routing_marker != expected_marker or state_marker != expected_marker:
            return self._fail(
                "delegation_policy",
                "Delegated live self-modification is forbidden",
                "Delegation policy markers are missing or inconsistent",
                evidence,
            )
        runtime = self.routing.get("runtime_adapter") or self.state.get("runtime_adapter") or {}
        errors = validate_delegation_policy(
            self.delegation_policy,
            str(self.state.get("task_id") or self.routing.get("task_id") or ""),
            manual_mode=runtime.get("class") == "manual",
        )
        if errors:
            return self._fail(
                "delegation_policy",
                "Delegated live self-modification is forbidden",
                "; ".join(errors),
                evidence,
            )
        violations = self.delegation_policy.get("violations") or []
        if violations:
            agents = sorted({str(item.get("agent") or "unknown") for item in violations if isinstance(item, dict)})
            return self._fail(
                "delegation_policy",
                "Delegated live self-modification is forbidden",
                "Recorded live self-modification invalidates affected evidence and blocks the task: "
                + ", ".join(agents),
                evidence,
            )
        return self._pass(
            "delegation_policy",
            "Delegated live self-modification is forbidden",
            "Exact protected-surface policy recorded with no violations",
            evidence,
        )

    def check_visible_attention(self) -> AuditItem:
        required_refs = [
            "attention-map.json",
            "context-selection.json",
            "context-pack.json",
            "mask-list.json",
            "evidence-board.json",
            "visible-routing.md",
        ]
        evidence = self._existing(required_refs + ["routing.json", "state.json"])
        profile = str(self.routing.get("profile") or self.state.get("profile") or "")
        agents = self._selected_agents()
        non_trivial = len(agents) > 1 or profile in VISIBLE_ATTENTION_REQUIRED_PROFILES
        if not non_trivial and not (self.routing.get("visible_attention") or self.state.get("visible_attention")):
            return self._skip("visible_attention", "Visible attention routing evidence is recorded", "Simple task without visible attention requirement", evidence)
        missing = [ref for ref in required_refs if not (self.task_dir / ref).exists()]
        if missing:
            return self._fail(
                "visible_attention",
                "Visible attention routing evidence is recorded",
                "Missing visible attention evidence: " + ", ".join(missing),
                evidence,
            )
        if self.attention_map.get("schema_version") != "valp-visible-attention-map.v1":
            return self._fail("visible_attention", "Visible attention routing evidence is recorded", "attention-map.json has wrong or missing schema_version", evidence)
        if not self.attention_map.get("loop_layer"):
            return self._fail("visible_attention", "Visible attention routing evidence is recorded", "attention-map.json has no loop_layer", evidence)
        heads = self.attention_map.get("heads")
        if not isinstance(heads, dict) or not heads:
            return self._fail("visible_attention", "Visible attention routing evidence is recorded", "attention-map.json has no attention heads", evidence)
        if not self.context_selection.get("selected"):
            return self._fail("visible_attention", "Visible attention routing evidence is recorded", "context-selection.json has no selected context", evidence)
        if not self.mask_list.get("masked"):
            return self._fail("visible_attention", "Visible attention routing evidence is recorded", "mask-list.json has no masked inputs", evidence)
        if not self.evidence_board.get("claims"):
            return self._fail("visible_attention", "Visible attention routing evidence is recorded", "evidence-board.json has no claims", evidence)
        return self._pass(
            "visible_attention",
            "Visible attention routing evidence is recorded",
            f"Loop layer: {self.attention_map.get('loop_layer')}",
            evidence,
        )

    def check_context_pack(self) -> AuditItem:
        evidence = self._existing(["context-pack.json", "context-selection.json", "routing.json", "state.json"])
        required = self._agent_recommendations_required() or bool(self.routing.get("context_pack") or self.state.get("context_pack") or self.context_pack)
        if not required:
            return self._skip("context_pack", "Context pack records compact visible worker context", "Simple task without context pack requirement", evidence)
        if not self.context_pack:
            return self._fail("context_pack", "Context pack records compact visible worker context", "Missing context-pack.json", evidence)
        if self.context_pack.get("schema_version") != "valp-context-pack.v1":
            return self._fail("context_pack", "Context pack records compact visible worker context", "context-pack.json has wrong or missing schema_version", evidence)
        items = self.context_pack.get("items")
        if not isinstance(items, list) or not items:
            return self._fail("context_pack", "Context pack records compact visible worker context", "context-pack.json has no items", evidence)
        sections = {str(item.get("section")) for item in items if isinstance(item, dict)}
        missing_sections = [section for section in ["task_scope", "verification", "permission_boundary"] if section not in sections]
        if missing_sections:
            return self._fail("context_pack", "Context pack records compact visible worker context", "Context pack missing sections: " + ", ".join(missing_sections), evidence)
        unsafe_refs: list[str] = []
        missing_refs: list[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            for ref in item.get("evidence_refs") or []:
                ref = str(ref)
                if not self._is_safe_task_ref(ref):
                    unsafe_refs.append(ref)
                elif not self._ref_exists(ref) and not self._workspace_ref_exists(ref):
                    missing_refs.append(ref)
        if unsafe_refs:
            return self._fail("context_pack", "Context pack records compact visible worker context", "Context pack refs must be safe paths: " + ", ".join(unsafe_refs[:5]), evidence)
        if missing_refs:
            return self._fail("context_pack", "Context pack records compact visible worker context", "Context pack refs are missing: " + ", ".join(missing_refs[:5]), evidence)
        payload_budgets = self.routing.get("dispatch_payload_budgets") or self.state.get("dispatch_payload_budgets") or {}
        mismatches: list[dict[str, Any]] = []
        for agent, budget in payload_budgets.items():
            if not isinstance(budget, dict):
                return self._fail("context_pack", "Context pack records compact visible worker context", f"Invalid dispatch payload budget for {agent}", evidence)
            dispatch_ref = str(budget.get("dispatch_ref") or "")
            if not dispatch_ref or not self._is_safe_task_ref(dispatch_ref) or not self._ref_exists(dispatch_ref):
                return self._fail("context_pack", "Context pack records compact visible worker context", f"Missing safe dispatch ref for {agent}", evidence)
            dispatch_path = self.task_dir / dispatch_ref
            try:
                dispatch_bytes = dispatch_path.read_bytes()
                dispatch_text = dispatch_bytes.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                max_chars = int(budget["max_chars"])
                max_reference_tokens = int(budget["max_reference_tokens"])
                recorded_chars = int(budget["actual_chars"])
                recorded_reference_tokens = int(budget["actual_reference_tokens"])
            except (KeyError, TypeError, ValueError, UnicodeDecodeError, OSError):
                return self._fail(
                    "context_pack",
                    "Context pack records compact visible worker context",
                    f"Invalid dispatch payload budget or bytes for {agent}",
                    evidence,
                )
            actual_chars = len(dispatch_text)
            actual_reference_tokens = (actual_chars + 3) // 4
            if (
                actual_chars > max_chars
                or actual_reference_tokens > max_reference_tokens
                or actual_chars != recorded_chars
                or actual_reference_tokens != recorded_reference_tokens
            ):
                mismatches.append(
                    {
                        "agent": str(agent),
                        "artifact_ref": dispatch_ref,
                        "byte_digest": "sha256:" + hashlib.sha256(dispatch_bytes).hexdigest(),
                        "recorded_budget": budget,
                        "observed": {
                            "actual_chars": actual_chars,
                            "actual_reference_tokens": actual_reference_tokens,
                        },
                    }
                )
        boundary_exists = (self.task_dir / "historical-audit-boundary.json").exists()
        if mismatches:
            boundary_errors = self._historical_budget_boundary_errors(mismatches)
            if boundary_errors:
                first = mismatches[0]
                return self._fail(
                    "context_pack",
                    "Context pack records compact visible worker context",
                    "Dispatch payload budget mismatch for "
                    f"{first['agent']}: chars={first['observed']['actual_chars']}, "
                    f"reference_tokens={first['observed']['actual_reference_tokens']}; "
                    + "; ".join(boundary_errors[:3]),
                    evidence,
                )
            return self._warn(
                "context_pack",
                "Context pack records compact visible worker context",
                "Hash-pinned historical dispatch budget nonconformity preserved for: "
                + ", ".join(item["agent"] for item in mismatches),
                self._existing([
                    "context-pack.json",
                    "context-selection.json",
                    "routing.json",
                    "state.json",
                    "historical-audit-boundary.json",
                ]),
            )
        if boundary_exists:
            return self._fail(
                "context_pack",
                "Context pack records compact visible worker context",
                "Historical audit boundary is present without a current dispatch budget mismatch",
                self._existing(["historical-audit-boundary.json"]),
            )
        return self._pass("context_pack", "Context pack records compact visible worker context", f"Context pack sections: {len(items)}", evidence)

    def check_skill_recommendations(self) -> AuditItem:
        evidence = self._existing(["routing.json", "skill-recommendations.json", "state.json"])
        record = self.routing.get("skill_recommendations") or self.state.get("skill_recommendations") or {}
        if not record:
            return self._warn("skill_recommendations", "Skill recommendation backend result is recorded", "No skill recommendation record found", evidence)
        status = record.get("status")
        if status == "not_run":
            return self._fail("skill_recommendations", "Skill recommendation backend result is recorded", "Skill router was not executed", evidence)
        if status == "unavailable":
            return self._warn("skill_recommendations", "Skill recommendation backend result is recorded", "No skill recommendation backend was available", evidence)
        if status == "failed":
            return self._warn("skill_recommendations", "Skill recommendation backend result is recorded", "Skill recommendation backend failed; continue only with explicit missing-recommendation evidence", evidence)
        ref = record.get("ref") or "skill-recommendations.json"
        if status in {"complete", "no_matches"}:
            data = self.skill_recommendations if ref == "skill-recommendations.json" else self._load_json(str(ref))
            if not data:
                return self._fail("skill_recommendations", "Skill recommendation backend result is recorded", f"Missing skill recommendation evidence: {ref}", evidence)
            if data.get("status") != status:
                return self._fail("skill_recommendations", "Skill recommendation backend result is recorded", "Routing status does not match skill recommendation evidence", evidence)
            return self._pass("skill_recommendations", "Skill recommendation backend result is recorded", f"Skill recommendation status: {status}", evidence)
        return self._warn("skill_recommendations", "Skill recommendation backend result is recorded", f"Unknown skill recommendation status: {status}", evidence)

    def check_squad_routing(self) -> AuditItem:
        evidence = self._existing(["routing.json", "state.json"])
        squad = self.routing.get("squad_routing") or self.state.get("squad_routing") or {}
        if not squad or squad.get("used") is False:
            return self._skip("squad_routing", "Squad routing evidence is recorded when a squad is used", "No squad routing used", evidence)
        required = ["leader", "members", "leader_decision"]
        missing = [key for key in required if not squad.get(key)]
        if missing:
            return self._fail("squad_routing", "Squad routing evidence is recorded when a squad is used", "Missing squad fields: " + ", ".join(missing), evidence)
        return self._pass("squad_routing", "Squad routing evidence is recorded when a squad is used", "Squad routing evidence found", evidence)

    def check_dispatch_receipts(self) -> AuditItem:
        evidence = self._existing(["dispatch-receipts.jsonl"])
        agents = self._selected_agents()
        receipt_errors = self.jsonl_errors.get("dispatch-receipts.jsonl") or []
        if receipt_errors:
            return self._fail(
                "dispatch_receipts",
                "Dispatch receipts satisfy the required gates",
                "Invalid dispatch receipt ledger: " + "; ".join(receipt_errors[:5]),
                evidence,
            )
        if not self.receipts:
            return self._fail("dispatch_receipts", "Dispatch receipts satisfy the required gates", "Missing or empty dispatch-receipts.jsonl", evidence)
        runtime = self.routing.get("runtime_adapter") or self.state.get("runtime_adapter") or {}
        manual_mode = runtime.get("class") == "manual"
        completed_events = {"manual_result_attested", "dispatch_completed"} if manual_mode else {"dispatch_completed"}
        latest = self._latest_receipts_by_agent()
        missing = [agent for agent in agents if agent not in latest]
        incomplete = [
            f"{agent}={latest[agent].get('event')}"
            for agent in agents
            if agent in latest and latest[agent].get("event") not in completed_events
        ]
        failed = []
        if missing:
            failed.append("missing receipt for: " + ", ".join(missing))
        if incomplete:
            failed.append("latest receipt is not dispatch_completed for: " + ", ".join(incomplete))
        if not manual_mode:
            missing_submission_proof = [
                agent
                for agent in agents
                if agent in latest
                and latest[agent].get("event") == "dispatch_completed"
                and not self._has_runtime_submission_proof(agent)
            ]
            if missing_submission_proof:
                failed.append(
                    "missing runtime submission proof before completion for: "
                    + ", ".join(missing_submission_proof)
                )
        if failed:
            return self._fail("dispatch_receipts", "Dispatch receipts satisfy the required gates", "; ".join(failed), evidence)
        if manual_mode:
            return self._pass("dispatch_receipts", "Dispatch receipts satisfy the required gates", "latest receipt is manual_result_attested or dispatch_completed for selected agents", evidence)
        return self._pass("dispatch_receipts", "Dispatch receipts satisfy the required gates", "latest receipt is dispatch_completed for selected agents and runtime submission proof exists", evidence)

    def check_submission_dependencies(self) -> AuditItem:
        evidence = self._existing(["submission-dependencies.json", "dispatch-receipts.jsonl", "routing.json", "state.json"])
        expected_marker = {"status": "recorded", "ref": "submission-dependencies.json"}
        routing_marker = self.routing.get("submission_dependencies") or {}
        state_marker = self.state.get("submission_dependencies") or {}
        required = bool(routing_marker) or bool(state_marker) or bool(self.submission_dependencies)
        if not required:
            return self._skip(
                "submission_dependencies",
                "Role submission dependencies are ordered",
                "Legacy task without declared submission dependencies",
                evidence,
            )
        if routing_marker != expected_marker or state_marker != expected_marker:
            return self._fail(
                "submission_dependencies",
                "Role submission dependencies are ordered",
                "Submission dependency markers are missing or inconsistent",
                evidence,
            )
        task_id = str(self.state.get("task_id") or self.routing.get("task_id") or "")
        role_assignments = self.routing.get("role_assignments") or self.state.get("role_assignments") or {}
        errors = validate_submission_dependencies(
            self.submission_dependencies,
            task_id,
            role_assignments,
        )
        runtime = self.routing.get("runtime_adapter") or self.state.get("runtime_adapter") or {}
        if not errors:
            errors = dependency_order_errors(
                self.submission_dependencies,
                self.receipts,
                self.task_dir,
                self.evidence_status,
                manual_mode=runtime.get("class") == "manual",
            )
        if errors:
            return self._fail(
                "submission_dependencies",
                "Role submission dependencies are ordered",
                "; ".join(errors),
                evidence,
            )
        return self._pass(
            "submission_dependencies",
            "Role submission dependencies are ordered",
            f"Validated dependencies: {len(self.submission_dependencies.get('dependencies') or [])}",
            evidence,
        )

    def check_expected_evidence(self) -> AuditItem:
        refs = self._expected_evidence_refs()
        evidence = self._existing(["task.md", "dispatch-receipts.jsonl"])
        if not refs:
            return self._fail("expected_evidence", "Expected evidence exists", "No expected evidence refs found", evidence)
        unsafe = [ref for ref in refs if not self._is_safe_task_ref(ref)]
        if unsafe:
            return self._fail(
                "expected_evidence",
                "Expected evidence exists",
                "Expected evidence refs must be task-relative safe paths: " + ", ".join(unsafe),
                evidence,
            )
        missing = [ref for ref in refs if not (self.task_dir / ref).exists()]
        invalid = [
            f"{ref}={status}"
            for ref in refs
            if (status := self._evidence_status(ref)) in {"invalid", "superseded", "rejected", "blocked"}
        ]
        if missing:
            return self._fail("expected_evidence", "Expected evidence exists", "Missing expected evidence: " + ", ".join(missing), evidence)
        if invalid:
            return self._fail("expected_evidence", "Expected evidence exists", "Expected evidence is not valid: " + ", ".join(invalid), evidence)
        return self._pass("expected_evidence", "Expected evidence exists", "All expected evidence refs exist", refs)

    def check_correction_cycle(self) -> AuditItem:
        evidence = self._existing(["correction-cycle.json", "dispatch-receipts.jsonl", "evidence-status.json"])
        signals = self._correction_required_signals()
        declared = (
            self.state.get("correction_cycle")
            or self.routing.get("correction_cycle")
            or (self.state.get("gates") or {}).get("correction")
        )
        if not signals and not declared and not self.correction_cycle:
            return self._skip(
                "correction_cycle",
                "Correction cycle is recorded when work is rejected, retried, blocked, or superseded",
                "No correction cycle signals found",
                evidence,
            )
        if not self.correction_cycle:
            return self._fail(
                "correction_cycle",
                "Correction cycle is recorded when work is rejected, retried, blocked, or superseded",
                "Missing correction-cycle.json for: " + ", ".join(signals or ["declared correction cycle"]),
                evidence,
            )
        if self.correction_cycle.get("schema_version") != "valp-correction-cycle.v1":
            return self._fail(
                "correction_cycle",
                "Correction cycle is recorded when work is rejected, retried, blocked, or superseded",
                "correction-cycle.json has wrong or missing schema_version",
                evidence,
            )
        rounds = self.correction_cycle.get("rounds")
        if not isinstance(rounds, list):
            return self._fail(
                "correction_cycle",
                "Correction cycle is recorded when work is rejected, retried, blocked, or superseded",
                "correction-cycle.json rounds must be a list",
                evidence,
            )
        outcome = str(self.correction_cycle.get("final_outcome") or "").lower()
        if signals and not rounds:
            return self._fail(
                "correction_cycle",
                "Correction cycle is recorded when work is rejected, retried, blocked, or superseded",
                "Correction cycle has no rounds for: " + ", ".join(signals),
                evidence,
            )
        if signals and outcome != "fixed":
            return self._fail(
                "correction_cycle",
                "Correction cycle is recorded when work is rejected, retried, blocked, or superseded",
                f"Correction cycle final_outcome must be fixed before Done; got {outcome or 'missing'}",
                evidence,
            )
        unsafe_refs = self._unsafe_correction_refs(rounds, self.correction_cycle.get("final_evidence_refs") or [])
        if unsafe_refs:
            return self._fail(
                "correction_cycle",
                "Correction cycle is recorded when work is rejected, retried, blocked, or superseded",
                "Correction evidence refs must be task-relative safe paths: " + ", ".join(unsafe_refs),
                evidence,
            )
        missing_refs = self._missing_correction_refs(rounds, self.correction_cycle.get("final_evidence_refs") or [])
        if outcome == "fixed" and missing_refs:
            return self._fail(
                "correction_cycle",
                "Correction cycle is recorded when work is rejected, retried, blocked, or superseded",
                "Correction evidence refs are missing: " + ", ".join(missing_refs),
                evidence,
            )
        if signals:
            return self._pass(
                "correction_cycle",
                "Correction cycle is recorded when work is rejected, retried, blocked, or superseded",
                "Correction cycle recorded and fixed: " + ", ".join(signals),
                evidence,
            )
        return self._pass(
            "correction_cycle",
            "Correction cycle is recorded when work is rejected, retried, blocked, or superseded",
            f"Correction cycle declared with final_outcome={outcome or 'missing'}",
            evidence,
        )

    def check_agent_recommendations(self) -> AuditItem:
        evidence = self._existing(["agent-recommendations.json", "routing.json", "state.json"])
        if not self._agent_recommendations_required():
            if not self.agent_recommendations:
                return self._skip(
                    "agent_recommendations",
                    "Agent recommendations are recorded and resolved",
                    "Simple task without agent recommendation resolution requirement",
                    evidence,
                )
        if not self.agent_recommendations:
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                "Missing agent-recommendations.json for non-trivial routed task",
                evidence,
            )
        if self.agent_recommendations.get("schema_version") != "valp-agent-recommendations.v1":
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                "agent-recommendations.json has wrong or missing schema_version",
                evidence,
            )
        status = str(self.agent_recommendations.get("status") or "").lower()
        entries = self.agent_recommendations.get("entries")
        if not isinstance(entries, list):
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                "agent-recommendations.json entries must be a list",
                evidence,
            )
        if status in {"pending", "blocked", "escalated"}:
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                f"Agent recommendations are not resolved: {status}",
                evidence,
            )
        if status == "not_required":
            if entries:
                return self._fail(
                    "agent_recommendations",
                    "Agent recommendations are recorded and resolved",
                    "status not_required must not include recommendation entries",
                    evidence,
                )
            if not self.agent_recommendations.get("summary") and not self.agent_recommendations.get("notes"):
                return self._fail(
                    "agent_recommendations",
                    "Agent recommendations are recorded and resolved",
                    "status not_required needs a summary or notes explaining why",
                    evidence,
                )
            return self._pass(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                "No agent recommendations required; reason recorded",
                evidence,
            )
        if status != "resolved":
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                f"Unknown or missing recommendation status: {status or 'missing'}",
                evidence,
            )
        complexity_policy = self.agent_recommendations.get("complexity_policy") or {}
        if not isinstance(complexity_policy, dict) or not complexity_policy.get("current_scope") or not complexity_policy.get("stop_conditions"):
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                "Resolved recommendation record needs complexity_policy.current_scope and stop_conditions",
                evidence,
            )
        if not entries:
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                "Resolved recommendation record has no entries",
                evidence,
            )
        allowed_decisions = {"accepted", "merged", "scoped_followup", "bounded_no_action", "escalated"}
        unresolved: list[str] = []
        unsafe_refs: list[str] = []
        missing_refs: list[str] = []
        missing_fields: list[str] = []
        for index, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                missing_fields.append(f"entry-{index}:not_object")
                continue
            entry_id = str(entry.get("id") or f"entry-{index}")
            required_fields = [
                "agent",
                "source_ref",
                "recommendation",
                "coordinator_decision",
                "rationale",
                "scope_boundary",
                "complexity_impact",
            ]
            missing = [field for field in required_fields if not entry.get(field)]
            if missing:
                missing_fields.append(f"{entry_id}:{','.join(missing)}")
            decision = str(entry.get("coordinator_decision") or "").lower()
            if decision not in allowed_decisions:
                missing_fields.append(f"{entry_id}:invalid_decision")
            decision_status = str(entry.get("decision_status") or "resolved").lower()
            if decision_status in {"pending", "blocked", "escalated"}:
                unresolved.append(f"{entry_id}={decision_status}")
            refs = [str(entry.get("source_ref") or "")]
            refs.extend(str(ref) for ref in entry.get("follow_up_refs") or [])
            refs.extend(str(ref) for ref in entry.get("follow_up_dispatch_refs") or [])
            for ref in refs:
                if not ref:
                    continue
                if not self._is_safe_task_ref(ref):
                    unsafe_refs.append(f"{entry_id}:{ref}")
                elif not (self.task_dir / ref).exists():
                    missing_refs.append(f"{entry_id}:{ref}")
        if missing_fields:
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                "Recommendation entries are incomplete: " + "; ".join(missing_fields[:5]),
                evidence,
            )
        if unresolved:
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                "Recommendation entries are unresolved: " + ", ".join(unresolved),
                evidence,
            )
        if unsafe_refs:
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                "Recommendation refs must be task-relative safe paths: " + ", ".join(unsafe_refs[:5]),
                evidence,
            )
        if missing_refs:
            return self._fail(
                "agent_recommendations",
                "Agent recommendations are recorded and resolved",
                "Recommendation refs are missing: " + ", ".join(missing_refs[:5]),
                evidence,
            )
        return self._pass(
            "agent_recommendations",
            "Agent recommendations are recorded and resolved",
            f"Resolved recommendation entries: {len(entries)}",
            evidence,
        )

    def check_claim_evidence(self) -> AuditItem:
        evidence = self._existing(["agents", "evidence", "dispatch-receipts.jsonl"])
        unsupported = self._unsupported_runtime_claims()
        if unsupported:
            return self._fail(
                "claim_evidence",
                "Runtime/build/test claims cite concrete evidence",
                "Unsupported runtime claims: " + "; ".join(unsupported[:5]),
                evidence,
            )
        return self._pass("claim_evidence", "Runtime/build/test claims cite concrete evidence", "No unsupported runtime claims found", evidence)

    def check_verification(self) -> AuditItem:
        evidence = self._existing(["evidence/verification.md", "gates/verification.json", "state.json"])
        gate = (self.state.get("gates") or {}).get("verification")
        has_evidence = (self.task_dir / "evidence/verification.md").exists() or (self.task_dir / "gates/verification.json").exists()
        if gate == "not_required":
            return self._pass("verification", "Verification passed or has a scoped blocker", f"Verification gate: {gate}", evidence)
        if gate == "passed":
            if has_evidence:
                return self._pass("verification", "Verification passed or has a scoped blocker", f"Verification gate: {gate}", evidence)
            return self._fail("verification", "Verification passed or has a scoped blocker", "Verification gate is passed but no verification evidence exists", evidence)
        if gate in {"scoped_blocker", "blocked"}:
            if has_evidence:
                return self._pass("verification", "Verification passed or has a scoped blocker", f"Verification gate: {gate}", evidence)
            return self._fail("verification", "Verification passed or has a scoped blocker", f"Verification gate is {gate} but no blocker evidence exists", evidence)
        if has_evidence:
            return self._pass("verification", "Verification passed or has a scoped blocker", "Verification evidence exists", evidence)
        return self._fail("verification", "Verification passed or has a scoped blocker", "Missing verification evidence or gate", evidence)

    def check_review_findings(self) -> AuditItem:
        evidence = self._existing(["findings/findings.json", "agents/claude/review.md", "gates/review.json", "state.json"])
        findings_path = self.task_dir / "findings/findings.json"
        if findings_path.exists():
            findings = self._load_json("findings/findings.json")
            unresolved = self._unresolved_high_findings(findings)
            if unresolved:
                return self._fail("review_findings", "Review findings have no unresolved critical/high blockers", "Unresolved critical/high findings: " + ", ".join(unresolved), evidence)
            return self._pass("review_findings", "Review findings have no unresolved critical/high blockers", "No unresolved critical/high findings", evidence)
        gate = (self.state.get("gates") or {}).get("review")
        if gate in {"passed", "not_required"}:
            return self._pass("review_findings", "Review findings have no unresolved critical/high blockers", f"Review gate: {gate}", evidence)
        if self._review_evidence_exists():
            return self._pass("review_findings", "Review findings have no unresolved critical/high blockers", "Review evidence exists and no blocking findings file is present", evidence)
        return self._warn("review_findings", "Review findings have no unresolved critical/high blockers", "No findings file or review gate found", evidence)

    def check_approvals(self) -> AuditItem:
        evidence = self._existing(["state.json", "approvals/requested.jsonl", "approvals/user-decisions.jsonl"])
        approval_errors = []
        for ref in ["approvals/requested.jsonl", "approvals/user-decisions.jsonl"]:
            approval_errors.extend(self.jsonl_errors.get(ref) or [])
        if approval_errors:
            return self._fail(
                "approvals",
                "Approvals are resolved",
                "Invalid approval ledger: " + "; ".join(approval_errors[:5]),
                evidence,
            )
        gate = (self.state.get("gates") or {}).get("approval")
        approval_required = self.state.get("approval_required") or []
        ledger = self._approval_ledger_result()
        if ledger["unresolved"]:
            return self._fail(
                "approvals",
                "Approvals are resolved",
                "Unresolved approval requests: " + ", ".join(ledger["unresolved"]),
                evidence,
            )
        if approval_required:
            return self._fail("approvals", "Approvals are resolved", "Unresolved approval_required entries exist", evidence)
        approval_risks = classify_approval_risks(self._approval_relevant_text())
        if approval_risks:
            risk_names = ", ".join(risk["kind"] for risk in approval_risks)
            if gate != "passed":
                return self._fail(
                    "approvals",
                    "Approvals are resolved",
                    f"High-risk approval required but gate is {gate or 'missing'}: {risk_names}",
                    evidence,
                )
            if not ledger["approved"]:
                return self._fail(
                    "approvals",
                    "Approvals are resolved",
                    f"High-risk approval gate passed without approval decision evidence: {risk_names}",
                    evidence,
                )
        if gate in {"passed", "not_required"}:
            if ledger["requests"]:
                return self._pass("approvals", "Approvals are resolved", f"Approval gate: {gate}; approval ledger resolved", evidence)
            return self._pass("approvals", "Approvals are resolved", f"Approval gate: {gate}", evidence)
        if gate:
            return self._warn("approvals", "Approvals are resolved", f"Approval gate is not resolved: {gate}", evidence)
        return self._warn("approvals", "Approvals are resolved", "No approval gate recorded", evidence)

    def check_final_synthesis(self) -> AuditItem:
        evidence = self._existing(["final-synthesis.md", "evidence/final-synthesis.md"])
        if not self.final_synthesis_text:
            return self._fail("final_synthesis", "Final synthesis records decisions, disagreements, evidence gaps, and result", "Missing final synthesis", evidence)
        required_terms = ["result", "decision", "disagreement", "evidence gap"]
        missing = [term for term in required_terms if term not in self.final_synthesis_text.lower()]
        if missing:
            return self._warn(
                "final_synthesis",
                "Final synthesis records decisions, disagreements, evidence gaps, and result",
                "Final synthesis exists but does not mention: " + ", ".join(missing),
                evidence,
            )
        return self._pass("final_synthesis", "Final synthesis records decisions, disagreements, evidence gaps, and result", "Final synthesis covers required sections", evidence)

    def check_routing_feedback(self) -> AuditItem:
        evidence = self._existing(["routing-feedback.json", "routing.json", "state.json"])
        feedback_ref = self.routing.get("routing_feedback_ref")
        feedback_state = self.state.get("routing_feedback") or {}
        supports_feedback = bool(feedback_ref or feedback_state or self.feedback)
        if not supports_feedback:
            return self._skip("routing_feedback", "Feedback record is written for non-trivial tasks when supported", "No routing feedback support declared", evidence)
        ref = feedback_ref or feedback_state.get("ref") or "routing-feedback.json"
        if (self.task_dir / ref).exists() or self.feedback:
            if self.feedback.get("schema_version") != "valp-routing-feedback.v1":
                return self._fail("routing_feedback", "Feedback record is written for non-trivial tasks when supported", "routing-feedback.json has wrong or missing schema_version", evidence)
            missing = [key for key in ["task_id", "profile", "result", "updated_at"] if not self.feedback.get(key)]
            if missing:
                return self._fail("routing_feedback", "Feedback record is written for non-trivial tasks when supported", "Routing feedback missing fields: " + ", ".join(missing), evidence)
            if self._agent_recommendations_required():
                quality_missing = [
                    key
                    for key in ["selected_agents", "expected_evidence", "actual_evidence", "lessons", "next_routing_hints", "privacy_notes"]
                    if not self.feedback.get(key)
                ]
                if quality_missing:
                    return self._fail("routing_feedback", "Feedback record is written for non-trivial tasks when supported", "Routing feedback missing learning fields: " + ", ".join(quality_missing), evidence)
            if self.feedback.get("result") == "done":
                if self.feedback.get("verification_result") != "passed" or self.feedback.get("review_result") != "passed":
                    return self._fail(
                        "routing_feedback",
                        "Feedback record is written for non-trivial tasks when supported",
                        "Done routing feedback requires passed verification_result and review_result",
                        evidence,
                    )
                actual_evidence = self.feedback.get("actual_evidence") or []
                if not isinstance(actual_evidence, list) or not actual_evidence:
                    return self._fail(
                        "routing_feedback",
                        "Feedback record is written for non-trivial tasks when supported",
                        "Done routing feedback requires actual_evidence refs",
                        evidence,
                    )
                unsafe_refs = [str(ref) for ref in actual_evidence if not self._is_safe_task_ref(str(ref))]
                if unsafe_refs:
                    return self._fail(
                        "routing_feedback",
                        "Feedback record is written for non-trivial tasks when supported",
                        "Routing feedback evidence refs must be task-relative safe paths: " + ", ".join(unsafe_refs[:5]),
                        evidence,
                    )
                missing_refs = [str(ref) for ref in actual_evidence if not self._task_local_ref_exists(str(ref))]
                if missing_refs:
                    return self._fail(
                        "routing_feedback",
                        "Feedback record is written for non-trivial tasks when supported",
                        "Routing feedback evidence refs are missing: " + ", ".join(missing_refs[:5]),
                        evidence,
                    )
            return self._pass("routing_feedback", "Feedback record is written for non-trivial tasks when supported", "Routing feedback exists with required fields", evidence)
        return self._fail("routing_feedback", "Feedback record is written for non-trivial tasks when supported", f"Missing routing feedback ref: {ref}", evidence)

    def check_learning_feedback(self) -> AuditItem:
        evidence = self._existing(["learning-feedback.json", "routing-feedback.json", "routing.json", "state.json"])
        declared = self.routing.get("learning_feedback_ref") or (self.state.get("learning_feedback") or {}).get("ref") or self.learning_feedback
        required = self._agent_recommendations_required() and bool(declared or self.feedback)
        if not required:
            return self._skip("learning_feedback", "Learning feedback records evidence-backed future improvements", "No learning feedback requirement for this task", evidence)
        ref = self.routing.get("learning_feedback_ref") or (self.state.get("learning_feedback") or {}).get("ref") or "learning-feedback.json"
        if not self.learning_feedback:
            return self._fail("learning_feedback", "Learning feedback records evidence-backed future improvements", f"Missing learning feedback ref: {ref}", evidence)
        if self.learning_feedback.get("schema_version") != "valp-learning-feedback.v1":
            return self._fail("learning_feedback", "Learning feedback records evidence-backed future improvements", "learning-feedback.json has wrong or missing schema_version", evidence)
        learning_items = self.learning_feedback.get("learning_items")
        proposed_updates = self.learning_feedback.get("proposed_updates")
        if not isinstance(learning_items, list) or not learning_items:
            return self._fail("learning_feedback", "Learning feedback records evidence-backed future improvements", "learning-feedback.json has no learning_items", evidence)
        if not isinstance(proposed_updates, list):
            return self._fail("learning_feedback", "Learning feedback records evidence-backed future improvements", "learning-feedback.json proposed_updates must be a list", evidence)
        unsafe_refs: list[str] = []
        missing_refs: list[str] = []
        for item in [*learning_items, *proposed_updates]:
            if not isinstance(item, dict):
                continue
            for ref in item.get("evidence_refs") or []:
                ref = str(ref)
                if not self._is_safe_task_ref(ref):
                    unsafe_refs.append(ref)
                elif not self._ref_exists(ref):
                    missing_refs.append(ref)
        if unsafe_refs:
            return self._fail("learning_feedback", "Learning feedback records evidence-backed future improvements", "Learning feedback refs must be safe paths: " + ", ".join(unsafe_refs[:5]), evidence)
        if missing_refs:
            return self._fail("learning_feedback", "Learning feedback records evidence-backed future improvements", "Learning feedback refs are missing: " + ", ".join(missing_refs[:5]), evidence)
        if not self.learning_feedback.get("privacy_notes"):
            return self._fail("learning_feedback", "Learning feedback records evidence-backed future improvements", "Learning feedback needs privacy_notes", evidence)
        return self._pass("learning_feedback", "Learning feedback records evidence-backed future improvements", f"Learning items: {len(learning_items)}", evidence)

    def _historical_budget_boundary_errors(self, mismatches: list[dict[str, Any]]) -> list[str]:
        boundary = self.historical_audit_boundary
        errors: list[str] = []
        expected_top_level = {
            "schema_version",
            "task_id",
            "recorded_at",
            "decision_task_id",
            "decision_ref",
            "auditor_boundary",
            "accepted_legacy_artifacts",
        }
        if not boundary:
            return ["missing historical-audit-boundary.json"]
        if set(boundary) != expected_top_level:
            errors.append("historical audit boundary has unexpected or missing top-level fields")
        task_id = str(self.state.get("task_id") or self.routing.get("task_id") or "")
        if boundary.get("schema_version") != "valp-historical-audit-boundary.v1":
            errors.append("historical audit boundary has the wrong schema_version")
        if boundary.get("task_id") != task_id:
            errors.append("historical audit boundary task_id does not match")
        if self.state.get("status") != "done":
            errors.append("historical audit boundary is restricted to terminal done tasks")
        decision_task_id = str(boundary.get("decision_task_id") or "")
        decision_ref = str(boundary.get("decision_ref") or "")
        if not decision_task_id or decision_task_id == task_id:
            errors.append("historical audit boundary requires a separate reconciliation task")
        expected_decision_prefix = f".herdr-loop/tasks/{decision_task_id}/"
        if (
            not decision_ref.startswith(expected_decision_prefix)
            or not self._is_safe_task_ref(decision_ref)
            or not self._workspace_ref_exists(decision_ref)
        ):
            errors.append("historical audit boundary decision_ref is missing or unsafe")
        auditor_boundary = boundary.get("auditor_boundary") or {}
        if set(auditor_boundary) != {
            "historical_cli_version",
            "historical_source_revision",
            "rule_introduced_revision",
        }:
            errors.append("historical auditor boundary fields are incomplete")
        historical_revision = str(auditor_boundary.get("historical_source_revision") or "")
        introduced_revision = str(auditor_boundary.get("rule_introduced_revision") or "")
        if not re.fullmatch(r"[0-9a-f]{40}", historical_revision):
            errors.append("historical source revision is invalid")
        if not re.fullmatch(r"[0-9a-f]{40}", introduced_revision):
            errors.append("rule introduction revision is invalid")
        if historical_revision == introduced_revision:
            errors.append("historical and rule introduction revisions must differ")
        if not str(auditor_boundary.get("historical_cli_version") or ""):
            errors.append("historical CLI version is missing")
        if not str(boundary.get("recorded_at") or ""):
            errors.append("historical audit boundary recorded_at is missing")

        accepted = boundary.get("accepted_legacy_artifacts")
        if not isinstance(accepted, list):
            return [*errors, "accepted legacy artifacts must be an array"]
        keys = [
            (
                str(item.get("rule_id") or ""),
                str(item.get("agent") or ""),
                str(item.get("artifact_ref") or ""),
            )
            for item in accepted
            if isinstance(item, dict)
        ]
        if len(keys) != len(accepted) or len(keys) != len(set(keys)):
            errors.append("accepted legacy artifacts must be unique objects")
        if len(accepted) != len(mismatches):
            errors.append("historical audit boundary must cover every and only current mismatch")

        expected_entry_fields = {
            "rule_id",
            "agent",
            "artifact_ref",
            "byte_digest",
            "recorded_budget",
            "observed",
            "disposition",
            "reason",
        }
        accepted_by_agent = {
            str(item.get("agent")): item
            for item in accepted
            if isinstance(item, dict)
        }
        for mismatch in mismatches:
            agent = mismatch["agent"]
            entry = accepted_by_agent.get(agent)
            if not entry:
                errors.append(f"missing historical acceptance for {agent}")
                continue
            if set(entry) != expected_entry_fields:
                errors.append(f"historical acceptance fields are invalid for {agent}")
            if entry.get("rule_id") != "context_pack.dispatch_payload_budget":
                errors.append(f"historical acceptance rule is invalid for {agent}")
            if entry.get("artifact_ref") != mismatch["artifact_ref"]:
                errors.append(f"historical artifact ref does not match for {agent}")
            if not self._is_safe_task_ref(str(entry.get("artifact_ref") or "")):
                errors.append(f"historical artifact ref is unsafe for {agent}")
            if entry.get("byte_digest") != mismatch["byte_digest"]:
                errors.append(f"historical byte digest does not match for {agent}")
            if entry.get("recorded_budget") != mismatch["recorded_budget"]:
                errors.append(f"historical recorded budget does not match for {agent}")
            if entry.get("observed") != mismatch["observed"]:
                errors.append(f"historical observed measurement does not match for {agent}")
            if entry.get("disposition") != "accept_historical_artifact":
                errors.append(f"historical disposition is invalid for {agent}")
            if not str(entry.get("reason") or ""):
                errors.append(f"historical acceptance reason is missing for {agent}")
        return errors

    def _selected_agents(self) -> list[str]:
        agents = self.routing.get("selected_agents") or self.state.get("selected_agents") or []
        return [str(a) for a in agents]

    def _agent_recommendations_required(self) -> bool:
        profile = str(self.routing.get("profile") or self.state.get("profile") or "")
        agents = self._selected_agents()
        if len(agents) > 1:
            return True
        return profile in VISIBLE_ATTENTION_REQUIRED_PROFILES

    def _expected_evidence_refs(self) -> list[str]:
        refs: set[str] = set()
        latest = self._latest_receipts_by_agent()
        for receipt in latest.values():
            for ref in receipt.get("expected_refs") or []:
                refs.add(str(ref))
        in_section = False
        for raw_line in self.task_text.splitlines():
            line = raw_line.strip()
            if line.lower().startswith("## expected evidence"):
                in_section = True
                continue
            if in_section and line.startswith("## "):
                break
            if in_section:
                match = re.search(r"`([^`]+)`", line)
                if match:
                    refs.add(match.group(1))
        return sorted(refs)

    def _evidence_status(self, ref: str) -> str:
        if not self.evidence_status:
            return "valid"
        evidence = self.evidence_status.get("evidence") or self.evidence_status.get("items") or {}
        if isinstance(evidence, dict):
            item = evidence.get(ref)
            if isinstance(item, dict):
                return str(item.get("status") or "valid").lower()
            if isinstance(item, str):
                return item.lower()
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict) and item.get("ref") == ref:
                    return str(item.get("status") or "valid").lower()
        return "valid"

    def _all_evidence_statuses(self) -> dict[str, str]:
        statuses: dict[str, str] = {}
        evidence = self.evidence_status.get("evidence") or self.evidence_status.get("items") or {}
        if isinstance(evidence, dict):
            for ref, item in evidence.items():
                if isinstance(item, dict):
                    statuses[str(ref)] = str(item.get("status") or "valid").lower()
                elif isinstance(item, str):
                    statuses[str(ref)] = item.lower()
        elif isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict) and item.get("ref"):
                    statuses[str(item["ref"])] = str(item.get("status") or "valid").lower()
        return statuses

    def _correction_required_signals(self) -> list[str]:
        signals: list[str] = []
        if any(receipt.get("event") == "dispatch_blocked" for receipt in self.receipts):
            signals.append("dispatch_blocked")
        invalid_statuses = {
            ref: status
            for ref, status in self._all_evidence_statuses().items()
            if status in {"invalid", "superseded", "rejected", "blocked"}
        }
        if invalid_statuses:
            rendered = ", ".join(f"{ref}={status}" for ref, status in sorted(invalid_statuses.items()))
            signals.append("evidence_status:" + rendered)
        return signals

    def _correction_refs(self, rounds: Any, final_refs: Any) -> list[str]:
        refs: list[str] = []
        if isinstance(rounds, list):
            for round_record in rounds:
                if not isinstance(round_record, dict):
                    continue
                for field in ["evidence_refs", "rejected_refs"]:
                    values = round_record.get(field) or []
                    if isinstance(values, list):
                        refs.extend(str(value) for value in values)
        if isinstance(final_refs, list):
            refs.extend(str(value) for value in final_refs)
        return refs

    def _unsafe_correction_refs(self, rounds: Any, final_refs: Any) -> list[str]:
        return [ref for ref in self._correction_refs(rounds, final_refs) if not self._is_safe_task_ref(ref)]

    def _missing_correction_refs(self, rounds: Any, final_refs: Any) -> list[str]:
        refs = [
            ref
            for ref in self._correction_refs(rounds, final_refs)
            if self._is_safe_task_ref(ref) and not (self.task_dir / ref).exists()
        ]
        return sorted(set(refs))

    def _unsupported_runtime_claims(self) -> list[str]:
        claim_patterns = [
            r"\b(build|compile|compiled|test|tests|lint|runtime|ui|browser|screenshot|launch|launched)\b.{0,80}\b(pass|passed|ok|succeed|succeeded|verified|fixed)\b",
            r"\bverified\b",
            r"\bverification passed\b",
        ]
        unsupported: list[str] = []
        for path in self._claim_evidence_paths():
            if path.name in {"dispatch.md", "context-compression.md"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            lowered = text.lower()
            claim_found = any(
                re.search(pattern, line, re.IGNORECASE)
                for line in lowered.splitlines()
                for pattern in claim_patterns
            )
            if not claim_found:
                continue
            if self._has_concrete_claim_evidence(text) or self._claim_has_status_support(path):
                continue
            unsupported.append(f"{path.relative_to(self.task_dir)}")
        return unsupported

    def _claim_evidence_paths(self) -> list[Path]:
        paths: list[Path] = []
        for root in [self.task_dir / "agents", self.task_dir / "evidence"]:
            if root.exists():
                paths.extend(sorted(root.rglob("*.md")))
        for relative in ["final-synthesis.md", "evidence/final-synthesis.md"]:
            path = self.task_dir / relative
            if path.exists() and path not in paths:
                paths.append(path)
        return paths

    def _has_concrete_claim_evidence(self, text: str) -> bool:
        if self._references_existing_evidence_path(text):
            return True
        has_any_command_block = False
        has_any_result_block = False
        for block in re.findall(r"```[a-zA-Z0-9_-]*\n(.*?)```", text, flags=re.DOTALL):
            lowered = block.lower()
            has_command = bool(
                re.search(
                    r"(^|\n)\s*(\$ )?(python|python3|pytest|npm|pnpm|yarn|node|swift|xcodebuild|cargo|go test|make|bash|sh|bin/|valp)\b",
                    lowered,
                )
                or "command" in lowered
                or "exit code" in lowered
                or "exit_code" in lowered
            )
            has_result = bool(
                re.search(r"\b(pass|passed|ok|success|succeeded|verified)\b", lowered)
                or re.search(r"\bexit[_ ]code\s*[:=]?\s*0\b", lowered)
            )
            has_any_command_block = has_any_command_block or has_command
            has_any_result_block = has_any_result_block or has_result
            if has_command and has_result:
                return True
        return has_any_command_block and has_any_result_block

    def _references_existing_evidence_path(self, text: str) -> bool:
        candidates = set(re.findall(r"`([^`]+)`", text))
        candidates.update(re.findall(r"(?<![\w/.-])((?:agents|evidence|gates|logs|screenshots)/[^\s),;:]+)", text))
        for candidate in candidates:
            ref = candidate.strip().strip(".,;:)")
            if not self._is_safe_task_ref(ref):
                continue
            try:
                if (self.task_dir / ref).exists():
                    return True
            except OSError:
                continue
        return False

    def _is_safe_task_ref(self, ref: str) -> bool:
        if not isinstance(ref, str):
            return False
        ref = ref.strip()
        if not ref or ref.startswith("/") or "\\" in ref or "\n" in ref or "\r" in ref:
            return False
        path = PurePosixPath(ref)
        if path.is_absolute():
            return False
        return ".." not in path.parts

    def _ref_exists(self, ref: str) -> bool:
        if not self._is_safe_task_ref(ref):
            return False
        task_relative = self.task_dir / ref
        try:
            if task_relative.exists():
                return True
        except OSError:
            return False
        if ref.startswith(".herdr-loop/tasks/") and self.task_dir.parent.name == "tasks" and self.task_dir.parent.parent.name == ".herdr-loop":
            workspace_root = self.task_dir.parent.parent.parent
            try:
                return (workspace_root / ref).exists()
            except OSError:
                return False
        return False

    def _workspace_ref_exists(self, ref: str) -> bool:
        if not self._is_safe_task_ref(ref):
            return False
        if self.task_dir.parent.name != "tasks" or self.task_dir.parent.parent.name != ".herdr-loop":
            return False
        workspace_root = self.task_dir.parent.parent.parent.resolve()
        try:
            candidate = (workspace_root / ref).resolve()
            candidate.relative_to(workspace_root)
        except (OSError, ValueError):
            return False
        return candidate.exists()

    def _task_local_ref_exists(self, ref: str) -> bool:
        if not self._is_safe_task_ref(ref):
            return False
        try:
            candidate = (self.task_dir / ref).resolve()
            candidate.relative_to(self.task_dir.resolve())
        except (OSError, ValueError):
            return False
        return candidate.exists()

    def _claim_has_status_support(self, path: Path) -> bool:
        records = self.evidence_status.get("evidence") or self.evidence_status.get("items") or {}
        if not isinstance(records, dict):
            return False
        ref = path.relative_to(self.task_dir).as_posix()
        record = records.get(ref)
        if not isinstance(record, dict) or record.get("status") != "valid":
            return False
        supporting_refs = record.get("supporting_refs") or []
        if not isinstance(supporting_refs, list):
            return False
        for supporting_ref in supporting_refs:
            supporting_ref = str(supporting_ref)
            if supporting_ref == ref or not self._task_local_ref_exists(supporting_ref):
                continue
            supporting_path = self.task_dir / supporting_ref
            try:
                supporting_text = supporting_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if self._has_concrete_claim_evidence(supporting_text):
                return True
        return False

    def _approval_ledger_result(self) -> dict[str, list[str]]:
        decisions_by_key: dict[str, list[dict[str, Any]]] = {}
        approved: list[str] = []
        for decision in self.approval_decisions:
            key = self._approval_record_key(decision)
            status = self._approval_status(decision)
            if key:
                decisions_by_key.setdefault(key, []).append(decision)
            if status in {"approved", "approve", "allowed", "allow"}:
                approved.append(key or "approval")

        unresolved: list[str] = []
        for index, request in enumerate(self.approval_requests, 1):
            key = self._approval_record_key(request) or f"request-{index}"
            status = self._approval_status(request)
            if status in {"approved", "approve", "rejected", "reject", "denied", "deny", "cancelled", "canceled", "superseded"}:
                continue
            matching_decisions = decisions_by_key.get(key, [])
            if any(
                self._approval_status(decision)
                in {"approved", "approve", "rejected", "reject", "denied", "deny", "cancelled", "canceled", "superseded"}
                for decision in matching_decisions
            ):
                continue
            unresolved.append(key)
        return {
            "requests": [self._approval_record_key(request) or "approval" for request in self.approval_requests],
            "unresolved": unresolved,
            "approved": approved,
        }

    def _approval_record_key(self, record: dict[str, Any]) -> str:
        for field in ["request_id", "approval_id", "id"]:
            value = record.get(field)
            if value:
                return str(value)
        kind = record.get("kind") or record.get("risk") or record.get("action")
        scope = record.get("scope") or record.get("target")
        if kind and scope:
            return f"{kind}:{scope}"
        if kind:
            return str(kind)
        return ""

    def _approval_status(self, record: dict[str, Any]) -> str:
        for field in ["status", "decision", "result", "outcome"]:
            value = record.get(field)
            if value:
                return str(value).lower()
        return "pending"

    def _approval_relevant_text(self) -> str:
        goal = self._section_text("goal")
        approval_risks = self._section_text("approval risks")
        if approval_risks and not re.search(r"\b(no|none|not required|not_required|no approval-gated)\b", approval_risks, re.IGNORECASE):
            return f"{goal}\n{approval_risks}"
        return goal

    def _section_text(self, heading: str) -> str:
        lines = self.task_text.splitlines()
        collecting = False
        collected: list[str] = []
        wanted = heading.strip().lower()
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## "):
                current = stripped[3:].strip().lower()
                if collecting:
                    break
                collecting = current == wanted
                continue
            if collecting:
                collected.append(line)
        return "\n".join(collected).strip()

    def _latest_receipts_by_agent(self) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for receipt in self.receipts:
            agent = receipt.get("agent")
            if agent:
                latest[str(agent)] = receipt
        return latest

    def _has_runtime_submission_proof(self, agent: str) -> bool:
        for receipt in self.receipts:
            if receipt.get("agent") != agent or receipt.get("event") != "dispatch_submitted":
                continue
            proof = receipt.get("proof")
            if not isinstance(proof, dict) or not proof:
                continue
            proof_text = json.dumps(proof, sort_keys=True).lower()
            forbidden = [
                "dry_run",
                "dry-run",
                "simulation",
                "simulated",
                "subagent",
                "sub-agent",
                "manual_attestation",
            ]
            if any(term in proof_text for term in forbidden):
                continue
            return True
        return False

    def _review_evidence_exists(self) -> bool:
        if (self.task_dir / "agents/claude/review.md").exists():
            return True
        for agent in self._selected_agents():
            agent_dir = self.task_dir / "agents" / agent
            if (agent_dir / "review.md").exists() or (agent_dir / "visible-review.md").exists():
                return True
        return False

    def _unresolved_high_findings(self, findings: Any) -> list[str]:
        if isinstance(findings, dict):
            candidates = findings.get("findings") or findings.get("items") or []
        elif isinstance(findings, list):
            candidates = findings
        else:
            candidates = []
        unresolved = []
        for idx, finding in enumerate(candidates):
            if not isinstance(finding, dict):
                continue
            severity = str(finding.get("severity", "")).lower()
            status = str(finding.get("status", "open")).lower()
            if severity in {"critical", "high"} and status in {"open", "blocked"}:
                unresolved.append(str(finding.get("title") or finding.get("id") or f"finding-{idx + 1}"))
        return unresolved

    def _load_json(self, relative: str) -> dict[str, Any]:
        path = self.task_dir / relative
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _load_jsonl(self, relative: str) -> list[dict[str, Any]]:
        path = self.task_dir / relative
        if not path.exists():
            return []
        records = []
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                self.jsonl_errors.setdefault(relative, []).append(f"{relative}:{lineno}: {exc.msg}")
                continue
            if isinstance(data, dict):
                records.append(data)
            else:
                self.jsonl_errors.setdefault(relative, []).append(f"{relative}:{lineno}: expected object")
        return records

    def _read_text(self, relative: str) -> str:
        path = self.task_dir / relative
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def _read_first_existing(self, relatives: list[str]) -> str:
        for relative in relatives:
            text = self._read_text(relative)
            if text:
                return text
        return ""

    def _existing(self, relatives: list[str]) -> list[str]:
        return [relative for relative in relatives if (self.task_dir / relative).exists()]

    def _item(self, status: str, item_id: str, title: str, message: str, evidence: list[str]) -> AuditItem:
        return AuditItem(item_id, title, status, message, evidence)

    def _pass(self, item_id: str, title: str, message: str, evidence: list[str]) -> AuditItem:
        return self._item(PASS, item_id, title, message, evidence)

    def _warn(self, item_id: str, title: str, message: str, evidence: list[str]) -> AuditItem:
        return self._item(WARN, item_id, title, message, evidence)

    def _fail(self, item_id: str, title: str, message: str, evidence: list[str]) -> AuditItem:
        return self._item(FAIL, item_id, title, message, evidence)

    def _skip(self, item_id: str, title: str, message: str, evidence: list[str]) -> AuditItem:
        return self._item(SKIP, item_id, title, message, evidence)


def resolve_task_dir(path: Path, task_id: str | None = None) -> Path:
    path = path.resolve()
    if task_id:
        candidate = path / ".herdr-loop" / "tasks" / task_id
        if candidate.exists():
            return candidate
        direct = path / task_id
        if direct.exists():
            return direct
        raise SystemExit(f"Task not found: {task_id} under {path}")

    if (path / "routing.json").exists() or (path / "state.json").exists():
        return path

    tasks_dir = path / ".herdr-loop" / "tasks"
    if tasks_dir.exists():
        tasks = [p for p in tasks_dir.iterdir() if p.is_dir()]
        if len(tasks) == 1:
            return tasks[0]
        if not tasks:
            raise SystemExit(f"No tasks found under {tasks_dir}")
        raise SystemExit("Multiple tasks found. Use --task <task-id>.")

    raise SystemExit(f"Not a VALP task folder or workspace: {path}")


def report_to_dict(report: AuditReport) -> dict[str, Any]:
    data = asdict(report)
    return data


def print_text_report(report: AuditReport) -> None:
    print(f"VALP audit: {report.status.upper()}")
    print(f"Task: {report.task_dir}")
    print(
        f"Summary: pass={report.pass_count} warn={report.warn_count} "
        f"fail={report.fail_count} skip={report.skip_count}"
    )
    print()
    for item in report.items:
        print(f"[{item.status.upper()}] {item.id}: {item.title}")
        print(f"  {item.message}")
        if item.evidence:
            print(f"  evidence: {', '.join(item.evidence)}")
