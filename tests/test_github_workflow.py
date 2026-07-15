import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "verify.yml"


class GitHubWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_triggers_do_not_duplicate_feature_branch_push_runs(self) -> None:
        self.assertRegex(self.workflow, r"(?m)^  push:\n    branches:\n      - main\n")
        self.assertRegex(self.workflow, r"(?m)^  pull_request:\n(?:    [^\n]*\n)*\n")

    def test_verify_keeps_the_full_three_by_three_matrix(self) -> None:
        self.assertRegex(
            self.workflow,
            r"(?ms)^        os:\n(?P<os>(?:          - [^\n]+\n){3}).*?^        python:\n(?P<python>(?:          - \"[0-9.]+\"\n){3})",
        )
        os_values = re.search(
            r"(?ms)^        os:\n(?P<values>(?:          - [^\n]+\n){3})",
            self.workflow,
        )
        python_values = re.search(
            r"(?ms)^        python:\n(?P<values>(?:          - \"[0-9.]+\"\n){3})",
            self.workflow,
        )
        self.assertIsNotNone(os_values)
        self.assertIsNotNone(python_values)
        self.assertEqual(os_values.group("values").splitlines(), [
            "          - ubuntu-latest",
            "          - macos-15",
            "          - windows-latest",
        ])
        self.assertEqual(python_values.group("values").splitlines(), [
            '          - "3.9"',
            '          - "3.11"',
            '          - "3.12"',
        ])

    def test_required_smoke_tests_is_a_stable_aggregate_gate(self) -> None:
        self.assertRegex(
            self.workflow,
            r"(?ms)^  smoke:\n    name: Required smoke tests\n    needs: verify\n    if: \$\{\{ always\(\) \}\}\n.*?^        if: \$\{\{ needs\.verify\.result != 'success' \}\}\n        run: exit 1\n",
        )


if __name__ == "__main__":
    unittest.main()
