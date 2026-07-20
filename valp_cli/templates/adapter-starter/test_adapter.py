from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapter import observe


class FakeRuntimeClient:
    def __init__(self, status: str) -> None:
        self.status = status

    def submit(self, payload):
        return {"submission_id": "real-runtime-id", "replay_identity": "thread-1"}

    def get_run(self, submission_id):
        return {"status": self.status}

    def collect(self, submission_id):
        return {"output_refs": ["runtime/output.json"]}


class AdapterContractTests(unittest.TestCase):
    def test_wait_expiry_keeps_worker_active(self):
        client = FakeRuntimeClient("running")
        submission = client.submit({})
        result = observe(client, submission, Path("."), ["evidence/report.md"], wait_seconds=0)
        self.assertEqual(result["status"], "waiting")
        self.assertFalse(result["worker_cancelled"])

    def test_invalid_wait_windows_are_rejected(self):
        client = FakeRuntimeClient("running")
        submission = client.submit({})
        for wait_seconds in (-1, float("nan"), float("inf")):
            with self.subTest(wait_seconds=wait_seconds):
                with self.assertRaises(ValueError):
                    observe(client, submission, Path("."), [], wait_seconds=wait_seconds)

    def test_false_done_blocks_until_expected_evidence_exists(self):
        client = FakeRuntimeClient("success")
        submission = client.submit({})
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            first = observe(client, submission, task_dir, ["evidence/report.md"], wait_seconds=0)
            self.assertEqual(first["status"], "blocked")
            self.assertEqual(first["failure_reason"]["error"], "missing_expected_evidence")

            report = task_dir / "evidence" / "report.md"
            report.parent.mkdir(parents=True)
            report.write_text("verified\n", encoding="utf-8")
            repaired = observe(client, submission, task_dir, ["evidence/report.md"], wait_seconds=0)
            self.assertEqual(repaired["status"], "completed")
            self.assertEqual(repaired["submission_id"], first["submission_id"])


if __name__ == "__main__":
    unittest.main()
