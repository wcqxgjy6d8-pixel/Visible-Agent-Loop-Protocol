from pathlib import Path
import json
import shutil
import tempfile
import unittest

from valp_cli.audit import FAIL, PASS, TaskAudit


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "full-mode-task"


class ValpAuditTests(unittest.TestCase):
    def test_full_mode_example_passes(self) -> None:
        report = TaskAudit(EXAMPLE).run()
        self.assertEqual(report.status, PASS)
        self.assertEqual(report.fail_count, 0)

    def test_missing_expected_evidence_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            (task / "agents" / "codex" / "evidence.md").unlink()

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "expected_evidence" and item.status == FAIL for item in report.items))

    def test_later_blocked_receipt_supersedes_completed_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp) / "task"
            shutil.copytree(EXAMPLE, task)
            blocked = {
                "ts": "2026-07-03T00:07:00Z",
                "agent": "claude",
                "event": "dispatch_blocked",
                "dispatch_ref": "agents/claude/dispatch.md",
                "expected_refs": ["agents/claude/review.md"],
                "summary": "Later retry failed to prove expected evidence",
            }
            with (task / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(blocked) + "\n")

            report = TaskAudit(task).run()
            self.assertEqual(report.status, FAIL)
            self.assertTrue(any(item.id == "dispatch_receipts" and item.status == FAIL for item in report.items))


if __name__ == "__main__":
    unittest.main()
