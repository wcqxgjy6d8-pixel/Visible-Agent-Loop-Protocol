from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tests.schema_helpers import schema_validator
from valp_cli.audit import TaskAudit, report_to_dict
from valp_cli.task_graph import build_task_graph, render_task_graph, _task_state_transition_digest
from valp_cli.workflow import TASK_STATE_STATUSES


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "full-mode-task"


class TaskGraphTests(unittest.TestCase):
    def _copy_example(self, tmp: str) -> Path:
        task_dir = Path(tmp) / "full-mode-task"
        shutil.copytree(EXAMPLE, task_dir)
        return task_dir

    def test_full_mode_projection_contains_execution_chain_and_matches_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            report = TaskAudit(task_dir).run()
            graph = build_task_graph(task_dir, report_to_dict(report))

            kinds = {node["kind"] for node in graph["nodes"]}
            self.assertEqual(report.status, "pass")
            self.assertTrue({"task", "workitem", "agent", "evidence", "receipt", "audit"} <= kinds)
            self.assertTrue(graph["projection_only"])
            self.assertEqual(graph["audit"]["status"], "pass")
            self.assertEqual(list(schema_validator(ROOT / "schemas" / "task-graph.schema.json").iter_errors(graph)), [])

    def test_canonical_graph_is_repeatable_and_never_serializes_task_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            report = report_to_dict(TaskAudit(task_dir).run())
            first = build_task_graph(task_dir, report)
            second = build_task_graph(task_dir, report)
            first_bytes = json.dumps(first, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            second_bytes = json.dumps(second, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")

            self.assertEqual(first_bytes, second_bytes)
            serialized = first_bytes.decode("utf-8")
            self.assertNotIn(str(task_dir.resolve()), serialized)
            self.assertNotIn("task_dir", serialized)
            self.assertNotIn("generated_at", serialized)
            self.assertNotIn("items", first["audit"])

    def test_unsafe_expected_ref_and_audit_payload_cannot_leak_or_create_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            board_path = task_dir / "evidence-board.json"
            board = json.loads(board_path.read_text(encoding="utf-8"))
            unsafe_refs = [
                "/Users/private/evidence.md",
                "C:\\private\\evidence.md",
                "\\\\server\\share\\evidence.md",
            ]
            board["claims"][0]["required_evidence"].extend(unsafe_refs)
            board_path.write_text(json.dumps(board), encoding="utf-8")
            graph = build_task_graph(task_dir, {
                "status": "pass", "task_dir": "/Users/private/task", "items": [{"message": "/Users/private/secret"}],
                "pass_count": 1, "warn_count": 0, "fail_count": 0, "skip_count": 0,
            })

            serialized = json.dumps(graph, ensure_ascii=False)
            self.assertNotIn("/Users/private", serialized)
            self.assertNotIn("evidence:/Users", serialized)
            labels = [node["label"] for node in graph["nodes"]]
            for unsafe_ref in unsafe_refs:
                self.assertNotIn(unsafe_ref, serialized)
                self.assertNotIn(unsafe_ref, labels)
            self.assertEqual(graph["audit"], {"status": "pass", "pass_count": 1, "warn_count": 0, "fail_count": 0, "skip_count": 0})
            self.assertEqual(list(schema_validator(ROOT / "schemas" / "task-graph.schema.json").iter_errors(graph)), [])

    def test_schema_rejects_absolute_and_traversal_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            graph = build_task_graph(task_dir, report_to_dict(TaskAudit(task_dir).run()))
            validator = schema_validator(ROOT / "schemas" / "task-graph.schema.json")
            for unsafe_ref in ("/Users/private/evidence.md", "evidence/../../private.md"):
                unsafe = deepcopy(graph)
                unsafe["nodes"][0]["refs"] = [unsafe_ref]
                self.assertTrue(list(validator.iter_errors(unsafe)), unsafe_ref)

    def test_schema_accepts_every_reference_task_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            state_path = task_dir / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            validator = schema_validator(ROOT / "schemas" / "task-graph.schema.json")

            for status in sorted(TASK_STATE_STATUSES):
                with self.subTest(status=status):
                    state["status"] = status
                    state_path.write_text(json.dumps(state), encoding="utf-8")
                    graph = build_task_graph(task_dir)
                    self.assertEqual(graph["status"], status)
                    self.assertEqual(list(validator.iter_errors(graph)), [])

    def test_display_text_redacts_unix_and_windows_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            task_dir.joinpath("task.md").write_text(
                "# Task\n\n## Goal\nInspect /Users/example/private and C:\\Users\\example\\private.\n",
                encoding="utf-8",
            )
            graph = build_task_graph(task_dir)
            serialized = json.dumps(graph, ensure_ascii=False)

            self.assertNotIn("/Users/example", serialized)
            self.assertNotIn("C:\\\\Users", serialized)
            self.assertIn("<redacted-path>", serialized)

    def test_missing_evidence_is_visible_but_cannot_become_audit_proof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            missing = task_dir / "evidence" / "verification.md"
            missing.unlink()
            report = TaskAudit(task_dir).run()
            graph = build_task_graph(task_dir, report_to_dict(report))

            evidence = {node["label"]: node for node in graph["nodes"] if node["kind"] == "evidence"}
            self.assertEqual(evidence["evidence/verification.md"]["status"], "missing")
            self.assertEqual(report.status, "fail")
            self.assertEqual(graph["audit"]["status"], "fail")
            self.assertTrue(graph["projection_only"])

    def test_state_status_is_authoritative_when_audit_and_receipts_look_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            state_path = task_dir / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "dispatching"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            report = {"status": "pass", "pass_count": 26, "warn_count": 0, "fail_count": 0, "skip_count": 0}

            graph = build_task_graph(task_dir, report)

            self.assertEqual(graph["status"], "dispatching")
            self.assertEqual(graph["summary"]["current_status"], "dispatching")
            expected = _task_state_transition_digest(state)
            self.assertEqual(graph["task_state_transition_digest"], expected)
            self.assertEqual(graph["summary"]["task_state_transition_digest"], expected)
            self.assertEqual(list(schema_validator(ROOT / "schemas" / "task-graph.schema.json").iter_errors(graph)), [])

    def test_task_state_transition_digest_changes_with_status_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            state_path = task_dir / "state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            first = build_task_graph(task_dir)["task_state_transition_digest"]
            state["status"] = "dispatching"
            state["revision"] = 1
            state_path.write_text(json.dumps(state), encoding="utf-8")
            second = build_task_graph(task_dir)["task_state_transition_digest"]

            self.assertNotEqual(first, second)
            self.assertRegex(second, r"^sha256:[0-9a-f]{64}$")

    def test_latest_blocked_receipt_overrides_historical_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": "2026-07-03T00:07:00Z",
                    "agent": "codex",
                    "event": "dispatch_blocked",
                    "summary": "Retry stopped pending new evidence",
                }) + "\n")
            graph = build_task_graph(task_dir, report_to_dict(TaskAudit(task_dir).run()))
            workitem = next(node for node in graph["nodes"] if node["kind"] == "workitem" and node["label"] == "implementer")
            agent = next(node for node in graph["nodes"] if node["kind"] == "agent" and node["label"] == "codex")
            self.assertEqual(workitem["status"], "blocked")
            self.assertEqual(agent["status"], "blocked")
            self.assertIn("Resolve current blockers", graph["summary"]["next_action"])
            self.assertEqual(list(schema_validator(ROOT / "schemas" / "task-graph.schema.json").iter_errors(graph)), [])

    def test_summary_exposes_correction_automation_approval_cost_and_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            graph = build_task_graph(task_dir, report_to_dict(TaskAudit(task_dir).run()))
            summary = graph["summary"]
            self.assertEqual(summary["current_status"], "done")
            self.assertEqual(summary["correction"], {"round": 1, "max_rounds": 3, "status": "fixed"})
            self.assertEqual(summary["automation"]["selected_action"], "continue_until_gate")
            self.assertEqual(summary["approval"]["gate"], "not_required")
            self.assertEqual(summary["cost"]["status"], "not_recorded")
            self.assertEqual(summary["continuation"]["status"], "not_available")
            self.assertEqual(summary["missing_evidence"], [])

    def test_adopted_v3_ledger_is_visible_and_evidence_classes_are_not_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            (task_dir / "dispatch-receipts.jsonl").unlink()
            (task_dir / "dispatch-receipts.jsonl").write_text("", encoding="utf-8")
            runtime = task_dir / "runtime" / "manual"
            runtime.mkdir(parents=True)
            (runtime / "adoption.json").write_text(json.dumps({
                "schema_version": "valp-runtime-receipt-adoption.v1",
                "task_id": "TASK-EXAMPLE-001",
                "adapter_id": "manual",
                "abi_version": "1.0",
                "ledger_ref": "runtime/manual/receipts.v3.jsonl",
                "compatibility_ledger_ref": "dispatch-receipts.jsonl",
                "write_schema": "valp-dispatch-receipt.v3",
            }), encoding="utf-8")
            (runtime / "receipts.v3.jsonl").write_text(json.dumps({
                "agent": "codex",
                "event": "manual_result_attested",
                "event_sequence": 1,
            }) + "\n" + json.dumps({
                "agent": "claude",
                "event": "manual_result_attested",
                "event_sequence": 2,
            }) + "\n", encoding="utf-8")
            report = {"status": "pass", "pass_count": 1, "warn_count": 0, "fail_count": 0, "skip_count": 0}
            graph = build_task_graph(task_dir, report)

            self.assertEqual(graph["status"], "done")
            self.assertEqual(graph["summary"]["missing_evidence"], [])
            receipt_nodes = [node for node in graph["nodes"] if node["kind"] == "receipt"]
            self.assertTrue(receipt_nodes)
            self.assertEqual(receipt_nodes[0]["refs"], ["runtime/manual/receipts.v3.jsonl"])
            labels = {node["label"] for node in graph["nodes"] if node["kind"] == "evidence"}
            self.assertNotIn("command log", labels)
            self.assertNotIn("gate JSON", labels)
            self.assertNotIn("task evidence path", labels)

    def test_rendered_html_contains_embedded_svg_and_projection_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = self._copy_example(tmp)
            graph = build_task_graph(task_dir, report_to_dict(TaskAudit(task_dir).run()))
            output_dir = Path(tmp) / "graph"
            written = render_task_graph(graph, output_dir, {"json", "html", "svg"})

            self.assertEqual({path.name for path in written}, {"task-graph.json", "task-graph.html", "task-graph.svg"})
            html = (output_dir / "task-graph.html").read_text(encoding="utf-8")
            self.assertIn("<svg", html)
            self.assertIn("Projection only: yes", html)
            payload = json.loads((output_dir / "task-graph.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["task_id"], "TASK-EXAMPLE-001")


if __name__ == "__main__":
    unittest.main()
