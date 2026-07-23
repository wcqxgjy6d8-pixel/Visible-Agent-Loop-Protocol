from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
EXAMPLES = {
    "examples/minimal-task/": ROOT / "examples" / "minimal-task",
    "examples/full-mode-task/": ROOT / "examples" / "full-mode-task",
    "examples/headless-queue-task/": ROOT / "examples" / "headless-queue-task",
    "examples/real-doc-calibration-task/": ROOT / "examples" / "real-doc-calibration-task",
    "examples/langgraph-false-done/": ROOT / "examples" / "langgraph-false-done" / "task",
}


class ReadmeClaimTests(unittest.TestCase):
    def test_bundled_example_audit_counts_match_readme(self) -> None:
        readme = README.read_text(encoding="utf-8")

        for label, task_directory in EXAMPLES.items():
            with self.subTest(example=label):
                claim = re.search(
                    rf"^\| `{re.escape(label)}` .*?\| `PASS`, `pass=(\d+) warn=(\d+) fail=(\d+)` \|$",
                    readme,
                    re.MULTILINE,
                )
                self.assertIsNotNone(claim, f"README has no exact audit claim for {label}")

                result = subprocess.run(
                    [sys.executable, "-m", "valp_cli", "audit", str(task_directory), "--json"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                report = json.loads(result.stdout)
                self.assertEqual(report["status"], "pass")
                self.assertEqual(
                    tuple(map(int, claim.groups())),
                    (report["pass_count"], report["warn_count"], report["fail_count"]),
                )


if __name__ == "__main__":
    unittest.main()
