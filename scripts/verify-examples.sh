#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -n "${PYTHON:-}" ]]; then
  PYTHON_BIN="$PYTHON"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  PYTHON_BIN="python"
fi
export PYTHONDONTWRITEBYTECODE=1

echo "==> Checking JSON syntax"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import json

for path in sorted([*Path("examples").rglob("*.json"), *Path("schemas").rglob("*.json")]):
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path}: invalid JSON: {exc}") from exc
PY

echo "==> Checking JSONL syntax"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import json

for path in sorted(Path("examples").rglob("*.jsonl")):
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{lineno}: invalid JSONL: {exc}") from exc
PY

echo "==> Running unit tests"
"$PYTHON_BIN" -m unittest tests/test_valp_audit.py tests/test_valp_workflow.py

echo "==> Auditing minimal example"
"$PYTHON_BIN" -m valp_cli audit examples/minimal-task

echo "==> Auditing full-mode example"
"$PYTHON_BIN" -m valp_cli audit examples/full-mode-task

echo "==> VALP example verification complete"
