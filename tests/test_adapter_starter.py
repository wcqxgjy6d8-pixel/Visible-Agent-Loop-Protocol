from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from valp_cli.adapter_starter import AdapterStarterError, initialize_adapter


class AdapterStarterTests(unittest.TestCase):
    def test_init_writes_runnable_starter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "demo-adapter"
            result = initialize_adapter(target, "demo-runtime")
            manifest = json.loads((target / "adapter.json").read_text(encoding="utf-8"))
            completed = subprocess.run(
                [sys.executable, "-m", "unittest", "test_adapter.py"],
                cwd=target,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result["status"], "created")
            self.assertEqual(manifest["adapter_id"], "demo-runtime")
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_init_refuses_nonempty_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "occupied"
            target.mkdir()
            (target / "keep.txt").write_text("user-owned\n", encoding="utf-8")

            with self.assertRaisesRegex(AdapterStarterError, "must be empty"):
                initialize_adapter(target, "demo-runtime")


if __name__ == "__main__":
    unittest.main()
