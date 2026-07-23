from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from valp_cli.doctor import FAIL, runtime_checks
from valp_cli.herdr_adapter import (
    HerdrSubmissionError,
    detect_herdr_submission_capability,
    submit_herdr_dispatch,
)
from valp_cli.submission import has_concrete_runtime_submission_proof
from valp_cli.workflow import (
    collect_herdr_preflight,
    dispatch_task,
    publish_task,
    route_task,
    write_herdr_submission_receipt,
)


TEST_CAPABILITIES = {
    "schema_version": "valp-agent-capabilities.v1",
    "updated_at": "2026-07-22T00:00:00Z",
    "source": "test fixture",
    "agents": {
        "codex": {
            "active": True,
            "role": ["coordination", "review", "risk_review"],
            "skills": [],
            "mcp_servers": [],
            "strengths": ["coordinates and reviews"],
            "must_not_do": ["must not bypass approval gates"],
        }
    },
}


def install_fake_fallback_herdr(root: Path) -> Path:
    fake_bin = root / "bin"
    fake_bin.mkdir()
    stub = fake_bin / "fake_herdr.py"
    stub.write_text(
        """from __future__ import annotations

import sys


RESPONSES = {
    ("agent", "--help"): "herdr agent wait <target> --status <state> [--timeout MS]",
    ("pane", "--help"): "herdr pane send-text <pane_id> <text>\\nherdr pane send-keys <pane_id> <key>",
    ("status", "--json"): '{"client":{"version":"0.7.4"},"server":{"version":"0.7.4"}}',
    ("pane", "list"): '{"result":{"panes":[{"agent":"codex","pane_id":"pane-1","agent_status":"idle"}]}}',
    ("pane", "process-info"): '{"result":{"process_info":{"foreground_process_group_id":41}}}',
    ("pane", "read"): '{"result":{"read":{"text":"test-model high ·"}}}',
    ("pane", "layout"): '{"result":{"layout":{"panes":[{"pane_id":"pane-1","rect":{"width":120,"height":40}}]}}',
    ("pane", "send-text"): '{"result":{"pane_id":"pane-1"}}',
    ("pane", "send-keys"): '{"result":{"pane_id":"pane-1"}}',
    ("agent", "wait"): '{"result":{"agent":{"status":"working","agent_session_id":"session-1"}}}',
}


key = tuple(sys.argv[1:3])
if key not in RESPONSES:
    print("unexpected fake herdr command: " + " ".join(sys.argv[1:]), file=sys.stderr)
    raise SystemExit(2)
print(RESPONSES[key])
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        herdr = fake_bin / "herdr.cmd"
        herdr.write_text(
            f'@echo off\r\n"{sys.executable}" "%~dp0fake_herdr.py" %*\r\n',
            encoding="utf-8",
        )
    else:
        herdr = fake_bin / "herdr"
        herdr.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "${{0%/*}}/fake_herdr.py" "$@"\n',
            encoding="utf-8",
        )
        herdr.chmod(0o755)
    return fake_bin


class PackagedHerdrAdapterTests(unittest.TestCase):
    def publish_routed_task(self, root: Path, task_id: str) -> Path:
        declaration = {
            "schema_version": "valp-assignment-declaration.v1",
            "declaration_id": f"test-declaration-{task_id}",
            "task_id": task_id,
            "declared_at": "2026-07-23T10:00:00Z",
            "leader": {
                "agent_id": "codex",
                "selected_by": "user",
                "selection_ref": f"test-user-selection:{task_id}",
            },
            "assignments": {
                "coordinator": "codex",
                "reviewer": "codex",
            },
            "reasons": {
                "coordinator": "Test Leader explicitly accepted the runtime coordinator role.",
                "reviewer": "Test Leader declared the bounded review role.",
            },
        }
        task_dir = publish_task(
            root,
            task_id,
            "Coordinate a bounded runtime check.",
            profile="generic-analysis",
            runtime="herdr",
        )
        route_task(
            root,
            task_id,
            runtime="herdr",
            assignment_declaration=declaration,
        )
        return task_dir

    def test_existing_invalid_submission_receipt_conflicts_instead_of_being_replaced(self) -> None:
        task_id = "TASK-RECEIPT-CONFLICT"
        expected = ["agents/codex/self-review.md"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            dependency = {
                "work_items": [
                    {
                        "agent": "codex",
                        "role": "coordinator",
                        "work_item_id": "coordinator:codex",
                        "dispatch_id": f"{task_id}:coordinator:1",
                        "dispatch_generation": 1,
                        "expected_refs": expected,
                    }
                ]
            }
            (directory / "submission-dependencies.json").write_text(
                json.dumps(dependency),
                encoding="utf-8",
            )
            invalid = {
                "schema_version": "valp-dispatch-receipt.v2",
                "receipt_id": "receipt-invalid-proof",
                "task_id": task_id,
                "event_sequence": 1,
                "agent": "codex",
                "role": "coordinator",
                "work_item_id": "coordinator:codex",
                "dispatch_id": f"{task_id}:coordinator:1",
                "dispatch_generation": 1,
                "event": "dispatch_submitted",
                "dispatch_ref": "agents/codex/dispatch.md",
                "expected_refs": expected,
                "proof": {"note": "accepted"},
            }
            ledger = directory / "dispatch-receipts.jsonl"
            ledger.write_text(json.dumps(invalid) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "conflicts"):
                write_herdr_submission_receipt(
                    directory,
                    task_id,
                    "codex",
                    "coordinator",
                    expected,
                    {
                        "runtime": "HERDR",
                        "transport_mode": "pane_send_text_enter",
                        "pane_id": "pane-1",
                    },
                )

            self.assertEqual(len(ledger.read_text(encoding="utf-8").splitlines()), 1)

    def test_existing_concrete_submission_receipt_conflicts_on_changed_runtime_proof(self) -> None:
        task_id = "TASK-CONCRETE-RECEIPT-CONFLICT"
        expected = ["agents/codex/self-review.md"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            dependency = {
                "work_items": [
                    {
                        "agent": "codex",
                        "role": "coordinator",
                        "work_item_id": "coordinator:codex",
                        "dispatch_id": f"{task_id}:coordinator:1",
                        "dispatch_generation": 1,
                        "expected_refs": expected,
                    }
                ]
            }
            (directory / "submission-dependencies.json").write_text(
                json.dumps(dependency),
                encoding="utf-8",
            )
            original_proof = {
                "runtime": "HERDR",
                "transport_mode": "pane_send_text_enter",
                "pane_id": "pane-original",
                "submission_id": "submission-original",
            }
            write_herdr_submission_receipt(
                directory,
                task_id,
                "codex",
                "coordinator",
                expected,
                original_proof,
            )
            ledger = directory / "dispatch-receipts.jsonl"
            original_bytes = ledger.read_bytes()

            with self.assertRaisesRegex(SystemExit, "conflicts"):
                write_herdr_submission_receipt(
                    directory,
                    task_id,
                    "codex",
                    "coordinator",
                    expected,
                    {
                        **original_proof,
                        "pane_id": "pane-reused-by-another-session",
                        "submission_id": "submission-conflict",
                    },
                )

            self.assertEqual(ledger.read_bytes(), original_bytes)

    def test_fallback_rejects_enter_without_working_state_proof(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "herdr agent wait <target> --status <state>",
                    "stderr": "",
                }
            if command[1:] == ["pane", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>",
                    "stderr": "",
                }
            if command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                return {"ok": True, "exit_code": 0, "stdout": "{}", "stderr": ""}
            if command[1:3] == ["agent", "wait"]:
                return {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "timed out waiting for working",
                }
            raise AssertionError(f"unexpected command: {command}")

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            with self.assertRaisesRegex(HerdrSubmissionError, "working-state proof failed"):
                submit_herdr_dispatch(
                    "/test/herdr",
                    capability,
                    task_id="TASK-PROOF-FAILURE",
                    target="codex",
                    pane_id="pane-1",
                    dispatch_path=dispatch,
                    run_command=fake_run,
                    proof_seconds=0,
                )

    def test_fallback_retries_enter_after_bounded_working_state_timeout(self) -> None:
        calls: list[list[str]] = []
        wait_attempts = 0

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal wait_attempts
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent wait <target> --status <state>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:] == ["pane", "--help"]:
                stdout = "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                return {"ok": True, "exit_code": 0, "stdout": '{"result":{"pane_id":"pane-1"}}', "stderr": ""}
            if command[1:3] == ["agent", "wait"]:
                wait_attempts += 1
                if wait_attempts == 1:
                    return {
                        "ok": False,
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "timed out waiting for working",
                    }
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": '{"event":"pane.agent_status_changed","data":{"pane_id":"pane-1","agent_status":"working"}}',
                    "stderr": "",
                }
            raise AssertionError(f"unexpected command: {command}")

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-ENTER-RETRY",
                target="claude",
                pane_id="pane-1",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=2,
            )

        enter_calls = [command for command in calls if command[1:3] == ["pane", "send-keys"]]
        self.assertEqual(len(enter_calls), 2)
        self.assertEqual(proof["status_proof"]["enter_attempts"], 2)
        self.assertEqual(proof["status_proof"]["working_attempt"], 2)
        self.assertEqual(proof["status_proof"]["runtime_response"]["pane_id"], "pane-1")

    def test_atomic_agent_prompt_is_preferred_and_preserves_runtime_identity(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = (
                    "herdr agent prompt <target> <text>\n"
                    "herdr agent wait <target> --status <state>"
                )
            elif command[1:] == ["pane", "--help"]:
                stdout = (
                    "herdr pane send-text <pane> <text>\n"
                    "herdr pane send-keys <pane> <key>"
                )
            elif command[1:3] == ["agent", "prompt"]:
                stdout = '{"result":{"submission_id":"prompt-42","status":"working"}}'
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n\nRun the bounded task.\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-ATOMIC",
                target="codex",
                pane_id="pane-1",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=1,
            )

        self.assertEqual(capability["mode"], "agent_prompt")
        self.assertEqual(proof["transport_mode"], "agent_prompt")
        self.assertEqual(proof["runtime_response"]["submission_id"], "prompt-42")
        prompt = next(command for command in calls if command[1:3] == ["agent", "prompt"])
        self.assertEqual(prompt[3], "pane-1")
        self.assertFalse(any(command[1:3] == ["pane", "send-text"] for command in calls))

    def test_atomic_agent_prompt_rejects_success_without_runtime_identity(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent prompt <target> <text>"
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane list"
            elif command[1:3] == ["agent", "prompt"]:
                stdout = "{}"
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            with self.assertRaisesRegex(HerdrSubmissionError, "runtime identity"):
                submit_herdr_dispatch(
                    "/test/herdr",
                    capability,
                    task_id="TASK-ATOMIC-NO-IDENTITY",
                    target="codex",
                    pane_id="pane-1",
                    dispatch_path=dispatch,
                    run_command=fake_run,
                    proof_seconds=1,
                )

    def test_fallback_uses_resolved_pane_for_working_state_proof(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent wait <target> --status <state>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:] == ["pane", "--help"]:
                stdout = "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                return {"ok": True, "exit_code": 0, "stdout": "{}", "stderr": ""}
            if command[1:3] == ["agent", "wait"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": '{"result":{"agent":{"status":"working","agent_session_id":"session-1"}}}',
                    "stderr": "",
                }
            raise AssertionError(f"unexpected command: {command}")

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-EVIDENCE-PROOF",
                target="codex",
                pane_id="pane-1",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=0,
            )

        self.assertEqual(proof["status_proof"]["status"], "working")
        wait = next(command for command in calls if command[1:3] == ["agent", "wait"])
        self.assertEqual(wait[3], "pane-1")

    def test_doctor_fails_when_installed_herdr_cannot_submit(self) -> None:
        def fake_preflight(_agents=None, *, runtime=None):
            if runtime == "queue":
                return {"status": "pass", "adapter_class": "daemon_queue"}
            return {
                "status": "fail",
                "adapter_class": "pane_controller",
                "checks": {
                    "submission_transport": {
                        "status": "fail",
                        "mode": "unavailable",
                    }
                },
            }

        with patch("valp_cli.doctor.shutil.which", return_value="/test/herdr"):
            with patch("valp_cli.doctor.collect_runtime_preflight", side_effect=fake_preflight):
                check = next(item for item in runtime_checks() if item.id == "runtime_herdr")

        self.assertEqual(check.status, FAIL)
        self.assertIn("submission", check.message.lower())

    def test_preflight_fails_when_herdr_has_no_submission_transport(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent list\nherdr agent read <target>"
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane list\nherdr pane read <pane>"
            elif command[1:] == ["status", "--json"]:
                stdout = '{"client":{"version":"0.7.3"},"server":{"version":"0.7.3"}}'
            elif command[1:] == ["pane", "list"]:
                stdout = '{"result":{"panes":[]}}'
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
            with patch("valp_cli.workflow.run_command", side_effect=fake_run):
                preflight = collect_herdr_preflight([])

        transport = preflight["checks"]["submission_transport"]
        self.assertEqual(preflight["status"], "fail")
        self.assertEqual(transport["mode"], "unavailable")
        self.assertIn("Install a HERDR build", transport["message"])

    def test_clean_path_submits_without_external_herdr_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean_path = str(install_fake_fallback_herdr(root))

            with patch.dict(os.environ, {"PATH": clean_path}, clear=False):
                with patch("valp_cli.workflow.load_local_capabilities", return_value=TEST_CAPABILITIES):
                    with patch("valp_cli.workflow.skill_router_command", return_value=None):
                        task_dir = self.publish_routed_task(root, "TASK-CLEAN-HERDR")
                commands = dispatch_task(
                    root,
                    "TASK-CLEAN-HERDR",
                    agent="codex",
                    role="coordinator",
                    submit=True,
                    runtime="herdr",
                    wait_seconds=0,
                    proof_seconds=1,
                )

            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            submitted = [receipt for receipt in receipts if receipt.get("event") == "dispatch_submitted"]
            self.assertEqual(len(submitted), 1)
            self.assertTrue(has_concrete_runtime_submission_proof(submitted[0]))
            self.assertEqual(submitted[0]["proof"]["transport_mode"], "pane_send_text_enter")
            self.assertNotIn("herdr-loop", commands[0])

    def test_evidence_wait_writes_completion_bound_to_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean_path = str(install_fake_fallback_herdr(root))
            with patch.dict(os.environ, {"PATH": clean_path}, clear=False):
                with patch("valp_cli.workflow.load_local_capabilities", return_value=TEST_CAPABILITIES):
                    with patch("valp_cli.workflow.skill_router_command", return_value=None):
                        task_dir = self.publish_routed_task(root, "TASK-HERDR-EVIDENCE")
                evidence = task_dir / "agents" / "codex" / "self-review.md"
                evidence.write_text("# Self Review\n\nPassed.\n", encoding="utf-8")
                dispatch_task(
                    root,
                    "TASK-HERDR-EVIDENCE",
                    agent="codex",
                    role="coordinator",
                    submit=True,
                    runtime="herdr",
                    wait_seconds=0.01,
                    proof_seconds=1,
                )

            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
                if '"schema_version"' in line
            ]
            self.assertEqual([receipt["event"] for receipt in receipts], ["dispatch_submitted", "dispatch_completed"])
            self.assertEqual(
                receipts[1]["proof"]["submission_receipt_id"],
                receipts[0]["receipt_id"],
            )


if __name__ == "__main__":
    unittest.main()
