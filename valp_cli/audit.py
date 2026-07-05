from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


PASS = "pass"
WARN = "warn"
FAIL = "fail"
SKIP = "skip"


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
        self.state = self._load_json("state.json")
        self.routing = self._load_json("routing.json")
        self.feedback = self._load_json("routing-feedback.json")
        self.evidence_status = self._load_json("evidence-status.json")
        self.skill_recommendations = self._load_json("skill-recommendations.json")
        self.attention_map = self._load_json("attention-map.json")
        self.context_selection = self._load_json("context-selection.json")
        self.mask_list = self._load_json("mask-list.json")
        self.evidence_board = self._load_json("evidence-board.json")
        self.receipts = self._load_jsonl("dispatch-receipts.jsonl")
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
            self.check_visible_attention(),
            self.check_skill_recommendations(),
            self.check_squad_routing(),
            self.check_dispatch_receipts(),
            self.check_expected_evidence(),
            self.check_claim_evidence(),
            self.check_verification(),
            self.check_review_findings(),
            self.check_approvals(),
            self.check_final_synthesis(),
            self.check_routing_feedback(),
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

    def check_visible_attention(self) -> AuditItem:
        required_refs = [
            "attention-map.json",
            "context-selection.json",
            "mask-list.json",
            "evidence-board.json",
            "visible-routing.md",
        ]
        evidence = self._existing(required_refs + ["routing.json", "state.json"])
        profile = str(self.routing.get("profile") or self.state.get("profile") or "")
        agents = self._selected_agents()
        non_trivial = len(agents) > 1 or profile in {"software-code", "apple-app", "web-frontend", "agent-runtime", "ops-release", "prototype"}
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

    def check_expected_evidence(self) -> AuditItem:
        refs = self._expected_evidence_refs()
        evidence = self._existing(["task.md", "dispatch-receipts.jsonl"])
        if not refs:
            return self._warn("expected_evidence", "Expected evidence exists", "No expected evidence refs found", evidence)
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
        if gate in {"passed", "not_required", "scoped_blocker", "blocked"}:
            return self._pass("verification", "Verification passed or has a scoped blocker", f"Verification gate: {gate}", evidence)
        if (self.task_dir / "evidence/verification.md").exists() or (self.task_dir / "gates/verification.json").exists():
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
        gate = (self.state.get("gates") or {}).get("approval")
        approval_required = self.state.get("approval_required") or []
        if gate in {"passed", "not_required"} and not approval_required:
            return self._pass("approvals", "Approvals are resolved", f"Approval gate: {gate}", evidence)
        if approval_required:
            return self._fail("approvals", "Approvals are resolved", "Unresolved approval_required entries exist", evidence)
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
            return self._pass("routing_feedback", "Feedback record is written for non-trivial tasks when supported", "Routing feedback exists", evidence)
        return self._fail("routing_feedback", "Feedback record is written for non-trivial tasks when supported", f"Missing routing feedback ref: {ref}", evidence)

    def _selected_agents(self) -> list[str]:
        agents = self.routing.get("selected_agents") or self.state.get("selected_agents") or []
        return [str(a) for a in agents]

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

    def _unsupported_runtime_claims(self) -> list[str]:
        claim_patterns = [
            r"\b(build|compile|compiled|test|tests|lint|runtime|ui|browser|screenshot|launch|launched)\b.{0,80}\b(pass|passed|ok|succeed|succeeded|verified|fixed)\b",
            r"\bverified\b",
            r"\bverification passed\b",
        ]
        evidence_markers = [
            "```",
            "`",
            ".log",
            ".json",
            ".png",
            ".txt",
            "command",
            "exit code",
            "exit_code",
            "stdout",
            "stderr",
            "evidence/",
            "agents/",
            "$ ",
        ]
        unsupported: list[str] = []
        roots = [self.task_dir / "agents", self.task_dir / "evidence"]
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*.md"):
                if path.name in {"dispatch.md", "context-compression.md", "final-synthesis.md"}:
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
                if any(marker in lowered for marker in evidence_markers):
                    continue
                unsupported.append(f"{path.relative_to(self.task_dir)}")
        return unsupported

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
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                records.append(data)
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
