from pathlib import Path
import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from valp_cli.cli import main
from valp_cli.doctor import (
    DoctorCheck,
    DoctorReport,
    audit_status_to_doctor_status,
    render_markdown_report,
    resolve_report_path,
)


class ValpDoctorTests(unittest.TestCase):
    def test_desktop_report_path_is_explicit_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = resolve_report_path("desktop", home=home, generated_at="2026-07-05T12:00:00Z")
        self.assertEqual(path, home / "Desktop" / "valp-doctor-report-20260705T120000Z.md")

    def test_markdown_report_contains_summary_and_checks(self) -> None:
        report = DoctorReport(
            workspace="/tmp/example",
            generated_at="2026-07-05T12:00:00Z",
            status="warn",
            pass_count=1,
            warn_count=1,
            fail_count=0,
            checks=[
                DoctorCheck("git_tracking", "Local HEAD matches upstream tracking ref", "pass", "HEAD == upstream tracking ref.", ["abc123"]),
                DoctorCheck("ignored_residue", "Ignored local residue is absent", "warn", "Found ignored residue.", ["!! .pytest_cache/"], "Remove caches."),
            ],
        )
        markdown = render_markdown_report(report)
        self.assertIn("# VALP Doctor Report", markdown)
        self.assertIn("Status: **WARN**", markdown)
        self.assertIn("### PASS `git_tracking`", markdown)
        self.assertIn("Suggested action: Remove caches.", markdown)

    def test_cli_json_uses_structured_report(self) -> None:
        report = DoctorReport(
            workspace="/tmp/example",
            generated_at="2026-07-05T12:00:00Z",
            status="pass",
            pass_count=1,
            warn_count=0,
            fail_count=0,
            checks=[
                DoctorCheck("git_tracking", "Local HEAD matches upstream tracking ref", "pass", "HEAD == upstream tracking ref.", ["abc123"]),
            ],
        )
        output = io.StringIO()
        with patch("valp_cli.cli.collect_doctor_report", return_value=report):
            with contextlib.redirect_stdout(output):
                code = main(["doctor", "--workspace", "/tmp/example", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["checks"][0]["id"], "git_tracking")

    def test_audit_warn_status_is_preserved(self) -> None:
        self.assertEqual(audit_status_to_doctor_status("pass"), "pass")
        self.assertEqual(audit_status_to_doctor_status("warn"), "warn")
        self.assertEqual(audit_status_to_doctor_status("fail"), "fail")


if __name__ == "__main__":
    unittest.main()
