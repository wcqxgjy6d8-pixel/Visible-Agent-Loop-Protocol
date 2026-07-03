from __future__ import annotations

import argparse
import json
import re
import sys
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
            self.check_routing_confidence(),
            self.check_squad_routing(),
            self.check_dispatch_receipts(),
            self.check_expected_evidence(),
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
            return self._warn(
                "selected_agents_context",
                "Selected agents and context policies are recorded",
                f"Selected agents recorded, but context policy missing for: {', '.join(missing)}",
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
        completed_agents = {
            r.get("agent")
            for r in self.receipts
            if r.get("event") == "dispatch_completed" and r.get("agent")
        }
        missing = [agent for agent in agents if agent not in completed_agents]
        if missing:
            return self._fail("dispatch_receipts", "Dispatch receipts satisfy the required gates", "Missing dispatch_completed for: " + ", ".join(missing), evidence)
        return self._pass("dispatch_receipts", "Dispatch receipts satisfy the required gates", "dispatch_completed found for selected agents", evidence)

    def check_expected_evidence(self) -> AuditItem:
        refs = self._expected_evidence_refs()
        evidence = self._existing(["task.md", "dispatch-receipts.jsonl"])
        if not refs:
            return self._warn("expected_evidence", "Expected evidence exists", "No expected evidence refs found", evidence)
        missing = [ref for ref in refs if not (self.task_dir / ref).exists()]
        if missing:
            return self._fail("expected_evidence", "Expected evidence exists", "Missing expected evidence: " + ", ".join(missing), evidence)
        return self._pass("expected_evidence", "Expected evidence exists", "All expected evidence refs exist", refs)

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
        for receipt in self.receipts:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="valp", description="VALP reference CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="Audit a VALP task evidence folder")
    audit.add_argument("path", nargs="?", default=".", help="Task folder or workspace root")
    audit.add_argument("--task", dest="task_id", help="Task id under <workspace>/.herdr-loop/tasks/")
    audit.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    audit.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "audit":
        task_dir = resolve_task_dir(Path(args.path), args.task_id)
        report = TaskAudit(task_dir, strict=args.strict).run()
        if args.json:
            print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
        else:
            print_text_report(report)
        return 1 if report.status == FAIL else 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
