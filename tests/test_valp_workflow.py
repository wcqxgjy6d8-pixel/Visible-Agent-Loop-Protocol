from pathlib import Path
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from valp_cli.cli import main
from valp_cli.workflow import (
    classify_profile,
    classify_approval_risks,
    decompose_execution_tasks,
    dispatch_task,
    load_local_capabilities,
    publish_task,
    read_json,
    route_task,
    scan_workspace,
)


class ValpWorkflowTests(unittest.TestCase):
    def test_cli_version_flag(self) -> None:
        output = io.StringIO()
        with self.assertRaises(SystemExit) as raised:
            with contextlib.redirect_stdout(output):
                main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("valp 0.2.0", output.getvalue())

    def test_profile_classification_scores_all_matches(self) -> None:
        self.assertEqual(classify_profile("Fix the HERDR agent connector code"), "agent-runtime")

    def test_high_risk_goal_marks_approval_required_on_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(
                root,
                "TASK-RISK",
                "Deploy the release to production and rotate secrets.",
                route=False,
            )

            state = read_json(task_dir / "state.json")
            self.assertEqual(state["gates"]["approval"], "needs_approval")
            self.assertTrue(state["approval_required"])
            kinds = {item["kind"] for item in state["approval_required"]}
            self.assertIn("deploy", kinds)
            self.assertIn("secrets", kinds)
            task_text = (task_dir / "task.md").read_text(encoding="utf-8")
            self.assertIn("`deploy`", task_text)
            self.assertIn("`secrets`", task_text)

    def test_risk_classifier_uses_word_boundaries(self) -> None:
        kinds = {item["kind"] for item in classify_approval_risks("Deploy release and export private data.")}
        self.assertIn("deploy", kinds)
        self.assertIn("release", kinds)
        self.assertIn("external_private_data", kinds)
        self.assertEqual(classify_approval_risks("Write author notes."), [])
        credential_kinds = {item["kind"] for item in classify_approval_risks("Rotate credentials.")}
        self.assertIn("auth", credential_kinds)

    def test_risk_classifier_ignores_first_install_dry_run_control_words(self) -> None:
        prompt = "Smoke test VALP publish and HERDR dispatch dry run only. Do not submit to agent panes."
        self.assertEqual(classify_approval_risks(prompt), [])
        self.assertEqual(classify_approval_risks("Run a deploy dry run only."), [])
        self.assertEqual(classify_approval_risks("Document `valp publish TASK-001` and `--submit`, but do not execute it."), [])

    def test_risk_classifier_keeps_real_submit_and_release_actions(self) -> None:
        kinds = {item["kind"] for item in classify_approval_risks("Submit the app release and deploy it.")}
        self.assertIn("submit", kinds)
        self.assertIn("release", kinds)
        self.assertIn("deploy", kinds)
        deploy_after_dry_run = {item["kind"] for item in classify_approval_risks("Run a dry run first, then deploy production.")}
        self.assertIn("deploy", deploy_after_dry_run)
        submit_after_smoke_test = {item["kind"] for item in classify_approval_risks("Run smoke test, then submit the app release.")}
        self.assertIn("submit", submit_after_smoke_test)
        self.assertIn("release", submit_after_smoke_test)

    def test_plain_goal_decomposition_keeps_paragraph_together(self) -> None:
        tasks = decompose_execution_tasks("Fix the protocol docs and verify the examples.", "software-code")
        self.assertEqual(tasks[0], "Fix the protocol docs and verify the examples.")

    def test_list_goal_decomposition_uses_explicit_items(self) -> None:
        tasks = decompose_execution_tasks("- Fix SPEC numbering\n- Add minimal example", "generic-analysis")
        self.assertIn("Fix SPEC numbering", tasks)
        self.assertIn("Add minimal example", tasks)

    def test_empty_environment_fallback_is_runtime_neutral(self) -> None:
        with patch("valp_cli.workflow.local_capabilities_path", return_value=Path("/tmp/valp-missing-capabilities.json")):
            capabilities = load_local_capabilities()
        self.assertIn("manual-operator", capabilities["agents"])
        self.assertNotIn("codex", capabilities["agents"])
        operator = capabilities["agents"]["manual-operator"]
        self.assertIn("manual_evidence", operator["role"])
        self.assertIn("must not imply a specific AI agent is installed", operator["must_not_do"])

    def test_capability_lookup_prefers_workspace_valp_paths_over_herdr_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            home = base / "home"
            (root / ".valp" / "agents").mkdir(parents=True)
            (home / ".herdr").mkdir(parents=True)
            (root / ".valp" / "agents" / "capabilities.json").write_text(
                json.dumps({"schema_version": "valp-agent-capabilities.v1", "source": "workspace-valp", "agents": {}}),
                encoding="utf-8",
            )
            (home / ".herdr" / "agent-capabilities.json").write_text(
                json.dumps({"schema_version": "valp-agent-capabilities.v1", "source": "herdr-fallback", "agents": {}}),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                with patch("valp_cli.workflow.Path.home", return_value=home):
                    capabilities = load_local_capabilities(root)

        self.assertEqual(capabilities["source"], "workspace-valp")

    def test_manual_mode_dispatch_prints_manual_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                    with patch("valp_cli.workflow.shutil.which", return_value=None):
                        task_dir = publish_task(root, "TASK-MANUAL", "Review the task evidence")
                        commands = dispatch_task(root, "TASK-MANUAL")
                        with self.assertRaises(SystemExit):
                            dispatch_task(root, "TASK-MANUAL", submit=True)

            self.assertEqual(read_json(task_dir / "routing.json")["runtime_adapter"]["class"], "manual")
            self.assertTrue(commands)
            self.assertTrue(commands[0].startswith("Manual Mode:"))
            self.assertNotIn("herdr-loop", commands[0])

    def test_dispatch_uses_queue_adapter_without_herdr_command(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-05T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests", "writes verification evidence"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = publish_task(root, "TASK-QUEUE", "Fix a bug and run tests", runtime="queue")
            commands = dispatch_task(root, "TASK-QUEUE")

            routing = read_json(task_dir / "routing.json")
            self.assertEqual(routing["runtime_adapter"]["class"], "daemon_queue")
            self.assertTrue(commands)
            self.assertTrue(commands[0].startswith("VALP Queue Mode:"))
            self.assertNotIn("herdr-loop", commands[0])
            preflight = read_json(task_dir / "runtime-preflight.json")
            self.assertEqual(preflight["adapter_class"], "daemon_queue")
            self.assertNotIn("terminal_size_status", json.dumps(preflight))

    def test_publish_auto_scans_routes_and_writes_dispatches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(root, "TASK-SMOKE", "Fix a bug and run tests")

            self.assertTrue((task_dir / "task.md").exists())
            self.assertTrue((task_dir / "state.json").exists())
            self.assertTrue((task_dir / "routing.json").exists())
            self.assertTrue((task_dir / "attention-map.json").exists())
            self.assertTrue((task_dir / "context-selection.json").exists())
            self.assertTrue((task_dir / "mask-list.json").exists())
            self.assertTrue((task_dir / "evidence-board.json").exists())
            self.assertTrue((task_dir / "visible-routing.md").exists())
            self.assertTrue((task_dir / "dispatch-receipts.jsonl").exists())
            self.assertTrue((root / ".herdr-loop" / "agents" / "capabilities.json").exists())

            routing = read_json(task_dir / "routing.json")
            self.assertEqual(routing["profile"], "software-code")
            self.assertIn("selected_agents", routing)
            self.assertEqual(routing["visible_attention"]["status"], "recorded")
            self.assertTrue(routing["selected_agents"])
            self.assertIn("Mode: Selected during routing", (task_dir / "task.md").read_text(encoding="utf-8"))

            for agent in routing["selected_agents"]:
                dispatch_path = task_dir / "agents" / agent / "dispatch.md"
                self.assertTrue(dispatch_path.exists())
                dispatch = dispatch_path.read_text(encoding="utf-8")
                self.assertIn("## Project Root", dispatch)
                self.assertIn(f'cd "{root.resolve()}"', dispatch)
                for expected_ref in {
                    "codex": ["agents/codex/evidence.md", "evidence/verification.md"],
                    "claude": ["agents/claude/review.md"],
                    "hermes": ["agents/hermes/self-review.md"],
                    "agy": ["agents/agy/prototype.md"],
                }.get(agent, [f"agents/{agent}/evidence.md"]):
                    self.assertIn(f".herdr-loop/tasks/TASK-SMOKE/{expected_ref}", dispatch)
                self.assertIn("## Visible Attention Slice", dispatch)

    def test_scan_and_route_existing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(root, "TASK-ROUTE", "Research a source", route=False)
            scan_workspace(root, "TASK-ROUTE")
            routing = route_task(root, "TASK-ROUTE")

            self.assertEqual(task_dir.resolve(), (root / ".herdr-loop" / "tasks" / "TASK-ROUTE").resolve())
            self.assertEqual(routing["profile"], "research")
            self.assertTrue((task_dir / "routing.json").exists())

    def test_skill_recommendations_are_written_into_dispatch(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-05T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": ["tdd"],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests", "writes verification evidence"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        router_payload = {
            "batch": True,
            "num_tasks": 1,
            "results": [
                {
                    "task": "run tests and write verification evidence",
                    "routing": {
                        "priority": "P1",
                        "decision": "auto-load",
                        "reason": "Strong installed workflow match.",
                    },
                    "matches": [
                        {
                            "skill": "verification-before-completion",
                            "installed": True,
                            "path": "/tmp/.agents/skills/verification-before-completion/SKILL.md",
                            "confidence": 0.44,
                            "mode": "auto-load",
                            "reason": "test match",
                        }
                    ],
                    "missing_skills": [],
                }
            ],
            "missing_skills": [],
            "routing": {
                "priority": "P1",
                "decision": "auto-load",
                "reason": "Highest-priority routing decision across batch tasks.",
            },
        }
        codex_payload = {
            "batch": True,
            "num_tasks": 1,
            "results": [
                {
                    "task": "run tests and write verification evidence",
                    "routing": {
                        "priority": "P1",
                        "decision": "auto-load",
                        "reason": "Strong installed workflow match.",
                    },
                    "matches": [
                        {
                            "skill": "tdd",
                            "installed": True,
                            "path": "/tmp/.agents/skills/tdd/SKILL.md",
                            "confidence": 0.51,
                            "mode": "auto-load",
                            "reason": "provider-filtered codex match",
                        }
                    ],
                    "missing_skills": [],
                }
            ],
            "missing_skills": [],
            "routing": {
                "priority": "P1",
                "decision": "auto-load",
                "reason": "Highest-priority routing decision across batch tasks.",
            },
        }

        def fake_run_command(command, timeout=8.0, input_text=None, stdout_limit=4000, stderr_limit=4000):
            if command == ["task-skill-router", "--batch"]:
                return {
                    "command": command,
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps(router_payload),
                    "stderr": "",
                }
            if command == ["task-skill-router", "--agent", "codex", "--batch"]:
                return {
                    "command": command,
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps(codex_payload),
                    "stderr": "",
                }
            if len(command) == 4 and command[:2] == ["task-skill-router", "--agent"] and command[3] == "--batch":
                return {
                    "command": command,
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps({"batch": True, "results": [], "missing_skills": [], "routing": {}}),
                    "stderr": "",
                }
            return {
                "command": command,
                "ok": True,
                "exit_code": 0,
                "stdout": "{}",
                "stderr": "",
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=["task-skill-router"]):
                    with patch("valp_cli.workflow.run_command", side_effect=fake_run_command):
                        task_dir = publish_task(root, "TASK-SKILL", "Fix a bug and run tests")

            recommendations = read_json(task_dir / "skill-recommendations.json")
            self.assertEqual(recommendations["status"], "complete")
            self.assertEqual(recommendations["results"][0]["matches"][0]["skill"], "verification-before-completion")
            self.assertIn("per_agent", recommendations)
            self.assertEqual(recommendations["per_agent"]["codex"]["results"][0]["matches"][0]["skill"], "tdd")

            routing = read_json(task_dir / "routing.json")
            for agent in routing["selected_agents"]:
                dispatch = (task_dir / "agents" / agent / "dispatch.md").read_text(encoding="utf-8")
                self.assertIn("## Recommended Skills", dispatch)
            codex_dispatch = (task_dir / "agents" / "codex" / "dispatch.md").read_text(encoding="utf-8")
            self.assertIn("filtered for `codex`", codex_dispatch)
            self.assertIn("tdd", codex_dispatch)
            self.assertNotIn("verification-before-completion", codex_dispatch)


if __name__ == "__main__":
    unittest.main()
