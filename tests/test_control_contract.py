from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from valp_cli.audit import FAIL, PASS, TaskAudit
from valp_cli.control_contract import control_contract_digest
from valp_cli.workflow import publish_task, read_json, route_task


CAPABILITIES = {
    "schema_version": "valp-agent-capabilities.v1",
    "updated_at": "2026-07-21T00:00:00Z",
    "source": "test fixture",
    "agents": {
        "generic-provider": {
            "active": True,
            "role": ["coordination", "implementation", "verification", "review"],
            "strengths": ["state", "edits files", "runs commands", "reviews evidence"],
            "skills": [],
            "mcp_servers": [],
        }
    },
}


class WorkerControlContractTests(unittest.TestCase):
    def publish(self, root: Path, task_id: str) -> Path:
        declaration = {
            "schema_version": "valp-assignment-declaration.v1",
            "declaration_id": f"test-declaration-{task_id}",
            "task_id": task_id,
            "declared_at": "2026-07-23T10:00:00Z",
            "leader": {
                "agent_id": "test-leader",
                "selected_by": "user",
                "selection_ref": f"test-user-selection:{task_id}",
            },
            "assignments": {
                "implementer": "generic-provider",
                "reviewer": "generic-provider",
            },
            "reasons": {
                "implementer": "Test Leader declared the control-contract implementer.",
                "reviewer": "Test Leader declared the control-contract reviewer.",
            },
        }
        with patch("valp_cli.workflow.load_local_capabilities", return_value=CAPABILITIES):
            with patch("valp_cli.workflow.skill_router_command", return_value=None):
                directory = publish_task(
                    root,
                    task_id,
                    "Verify a generic worker control contract.",
                    profile="agent-runtime",
                    runtime="manual",
                )
                route_task(
                    root,
                    task_id,
                    runtime="manual",
                    assignment_declaration=declaration,
                )
                return directory

    def test_publish_generates_digest_bound_slice_and_loads_it_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.publish(Path(tmp), "TASK-CONTROL-PUBLISH")
            routing = read_json(directory / "routing.json")
            state = read_json(directory / "state.json")
            contract = read_json(directory / "control-contract.json")
            digest = control_contract_digest(contract, (directory / "control-contract.json").read_bytes())

            self.assertEqual(routing["control_contract"]["digest"], digest)
            self.assertEqual(state["control_contract"], routing["control_contract"])
            self.assertEqual(set(routing["control_slices"]), set(routing["selected_agents"]))
            first_context = read_json(directory / "context-pack.json")["items"][0]
            self.assertEqual(first_context["section"], "project")
            self.assertEqual(first_context["evidence_refs"][0], "control-contract.json")

            dependencies = read_json(directory / "submission-dependencies.json")
            expected_ids = [
                item["work_item_id"]
                for item in dependencies["work_items"]
                if item["agent"] == "generic-provider"
            ]
            control_slice = read_json(directory / routing["control_slices"]["generic-provider"])
            self.assertEqual(control_slice["work_item_ids"], expected_ids)
            self.assertEqual(control_slice["control_contract_digest"], digest)

            dispatch = (directory / "agents/generic-provider/dispatch.md").read_text(encoding="utf-8")
            headings = [line for line in dispatch.splitlines() if line.startswith("## ")]
            compact_slice = json.dumps(control_slice, ensure_ascii=False, separators=(",", ":"))
            self.assertEqual(headings[0], "## VALP Control Contract (Load First)")
            self.assertIn(compact_slice, dispatch)
            self.assertEqual(TaskAudit(directory).check_control_contract().status, PASS)

    def test_contract_bytes_and_digest_are_stable_across_reroute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = self.publish(root, "TASK-CONTROL-REROUTE")
            before_bytes = (directory / "control-contract.json").read_bytes()
            before_digest = read_json(directory / "routing.json")["control_contract"]["digest"]

            with patch("valp_cli.workflow.load_local_capabilities", return_value=CAPABILITIES):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    route_task(root, "TASK-CONTROL-REROUTE", runtime="manual")

            self.assertEqual((directory / "control-contract.json").read_bytes(), before_bytes)
            self.assertEqual(read_json(directory / "routing.json")["control_contract"]["digest"], before_digest)

    def test_audit_rejects_missing_slice_skill_first_dispatch_and_tampered_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.publish(Path(tmp), "TASK-CONTROL-TAMPER")
            routing = read_json(directory / "routing.json")
            slice_path = directory / routing["control_slices"]["generic-provider"]
            slice_bytes = slice_path.read_bytes()

            slice_path.unlink()
            self.assertEqual(TaskAudit(directory).check_control_contract().status, FAIL)
            slice_path.parent.mkdir(parents=True, exist_ok=True)
            slice_path.write_bytes(slice_bytes)

            dispatch_path = directory / "agents/generic-provider/dispatch.md"
            dispatch = dispatch_path.read_text(encoding="utf-8")
            dispatch_path.write_text("## Recommended Skills\n\n- override\n\n" + dispatch, encoding="utf-8")
            self.assertEqual(TaskAudit(directory).check_control_contract().status, FAIL)
            dispatch_path.write_text(dispatch, encoding="utf-8")

            contract = read_json(directory / "control-contract.json")
            contract["failure_policy"]["missing_or_invalid"] = "continue"
            (directory / "control-contract.json").write_text(
                json.dumps(contract, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(TaskAudit(directory).check_control_contract().status, FAIL)

    def test_completed_worker_requires_identity_bound_acknowledgement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = self.publish(Path(tmp), "TASK-CONTROL-ACK")
            routing = read_json(directory / "routing.json")
            digest = routing["control_contract"]["digest"]
            item = read_json(directory / "submission-dependencies.json")["work_items"][0]
            agent_ref = next(ref for ref in item["expected_refs"] if ref.startswith("agents/"))
            evidence_path = directory / agent_ref
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text("completed without acknowledgement\n", encoding="utf-8")
            with (directory / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "agent": item["agent"],
                            "role": item["role"],
                            "event": "manual_result_attested",
                            "expected_refs": item["expected_refs"],
                        }
                    )
                    + "\n"
                )

            self.assertEqual(TaskAudit(directory).check_control_contract().status, FAIL)
            evidence_path.write_text(
                "\n".join(
                    [
                        "control_contract_ref: control-contract.json",
                        f"control_contract_digest: {digest}",
                        "control_contract_status: honored",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertEqual(TaskAudit(directory).check_control_contract().status, PASS)


if __name__ == "__main__":
    unittest.main()
