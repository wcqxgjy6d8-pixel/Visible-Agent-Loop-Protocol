from pathlib import Path
import tempfile
import unittest

from valp_cli.workflow import publish_task, read_json, route_task, scan_workspace


class ValpWorkflowTests(unittest.TestCase):
    def test_publish_auto_scans_routes_and_writes_dispatches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(root, "TASK-SMOKE", "Fix a bug and run tests")

            self.assertTrue((task_dir / "task.md").exists())
            self.assertTrue((task_dir / "state.json").exists())
            self.assertTrue((task_dir / "routing.json").exists())
            self.assertTrue((task_dir / "dispatch-receipts.jsonl").exists())
            self.assertTrue((root / ".herdr-loop" / "agents" / "capabilities.json").exists())

            routing = read_json(task_dir / "routing.json")
            self.assertEqual(routing["profile"], "software-code")
            self.assertIn("selected_agents", routing)
            self.assertTrue(routing["selected_agents"])

            for agent in routing["selected_agents"]:
                self.assertTrue((task_dir / "agents" / agent / "dispatch.md").exists())

    def test_scan_and_route_existing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(root, "TASK-ROUTE", "Research a source", route=False)
            scan_workspace(root, "TASK-ROUTE")
            routing = route_task(root, "TASK-ROUTE")

            self.assertEqual(task_dir.resolve(), (root / ".herdr-loop" / "tasks" / "TASK-ROUTE").resolve())
            self.assertEqual(routing["profile"], "research")
            self.assertTrue((task_dir / "routing.json").exists())


if __name__ == "__main__":
    unittest.main()
