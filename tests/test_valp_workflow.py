from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from valp_cli.workflow import publish_task, read_json, route_task, scan_workspace


class ValpWorkflowTests(unittest.TestCase):
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
