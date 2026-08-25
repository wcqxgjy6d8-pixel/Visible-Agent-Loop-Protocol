from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
EDITABLE_INSTALL_DOCS = [
    README,
    ROOT / "INSTALL.md",
    ROOT / "docs" / "quickstart.md",
]
HERDR_LICENSE_DOCS = [
    ROOT / "INSTALL.md",
    ROOT / "docs" / "quickstart.md",
    ROOT / "docs" / "runtime-adapters.md",
    ROOT / "docs" / "project-status.md",
    ROOT / "docs" / "faq.md",
    ROOT / "docs" / "comparison.md",
]
PUBLIC_HERDR_DOCS = [README, *HERDR_LICENSE_DOCS]
FIRST_TIME_SECTIONS = {
    README: ("## Fast Start", "## Reference CLI"),
    ROOT / "INSTALL.md": ("## First Run Health Gate", "## Platform Quick Start"),
    ROOT / "docs" / "quickstart.md": (
        "## Path B: Try Full Mode With HERDR",
        "## For Runtime Implementers",
    ),
}
EXAMPLES = {
    "examples/minimal-task/": ROOT / "examples" / "minimal-task",
    "examples/full-mode-task/": ROOT / "examples" / "full-mode-task",
    "examples/headless-queue-task/": ROOT / "examples" / "headless-queue-task",
    "examples/real-doc-calibration-task/": ROOT / "examples" / "real-doc-calibration-task",
    "examples/langgraph-false-done/": ROOT / "examples" / "langgraph-false-done" / "task",
}


class ReadmeClaimTests(unittest.TestCase):
    def test_first_time_full_mode_path_reaches_visible_reviewed_agents_in_order(self) -> None:
        ordered_markers = (
            "valp doctor",
            "valp leader select",
            "valp leader start",
            "valp leader show",
            "valp leader open",
            "valp publish",
            "valp route",
            "valp dispatch",
            "--submit",
            "Leader pane",
            "Worker pane",
            "dispatch_submitted",
            "dispatch_completed",
            "independent review",
            "valp audit",
        )

        for path, (start, end) in FIRST_TIME_SECTIONS.items():
            with self.subTest(path=path.relative_to(ROOT)):
                document = path.read_text(encoding="utf-8")
                section = document.split(start, 1)[1].split(end, 1)[0]
                normalized = " ".join(section.split()).casefold()
                positions = [normalized.find(marker.casefold()) for marker in ordered_markers]
                self.assertNotIn(-1, positions)
                self.assertEqual(positions, sorted(positions))

    def test_public_herdr_docs_do_not_promote_transport_to_submission(self) -> None:
        required_claims = (
            "agent prompt",
            "--wait --until working",
            "state_change_seq",
            "dispatch_inserted",
            "dispatch_submitted",
            "Manual-degraded",
        )

        for path in PUBLIC_HERDR_DOCS:
            with self.subTest(path=path.relative_to(ROOT)):
                document = " ".join(path.read_text(encoding="utf-8").split()).casefold()
                for claim in required_claims:
                    self.assertIn(claim.casefold(), document)

    def test_herdr_license_claims_preserve_release_and_master_boundary(self) -> None:
        required_claims = (
            "v0.7.5",
            "AGPL-3.0-or-later",
            "commercial license",
            "upstream `master`",
            "cd5ea1be0e69",
            "Apache-2.0",
        )

        for path in HERDR_LICENSE_DOCS:
            with self.subTest(path=path.relative_to(ROOT)):
                document = " ".join(path.read_text(encoding="utf-8").split())
                for claim in required_claims:
                    self.assertIn(claim.casefold(), document.casefold())

    def test_editable_install_instructions_bootstrap_packaging_tools(self) -> None:
        bootstrap = 'python -m pip install --upgrade pip setuptools'
        editable_install = 'python -m pip install -e ".[dev]"'

        for path in EDITABLE_INSTALL_DOCS:
            with self.subTest(path=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8")
                self.assertIn(editable_install, text)
                self.assertIn(bootstrap, text)
                self.assertLess(text.index(bootstrap), text.index(editable_install))

    def test_dispatch_size_benchmark_measures_generated_dispatches(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/benchmark-dispatch-size.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        report = json.loads(result.stdout)
        expected = {
            "full-mode/codex",
            "full-mode/claude",
            "headless-queue/codex",
            "headless-queue/claude",
        }
        self.assertEqual(set(report["new_files"]), expected)
        self.assertGreater(report["new_total_chars"], 0)
        self.assertGreater(report["reduction_chars"], 0)

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
