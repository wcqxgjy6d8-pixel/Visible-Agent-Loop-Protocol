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

echo "==> Validating bundled examples against schemas"
"$PYTHON_BIN" - <<'PY'
from pathlib import Path
import json
try:
    from jsonschema import Draft202012Validator
except ImportError as exc:
    raise SystemExit(
        "jsonschema is required for schema validation. Install with: "
        "python -m pip install -r requirements-dev.txt"
    ) from exc

root = Path(".")
schema_by_name = {
    "attention-map.json": "attention-map.schema.json",
    "automation-policy.json": "automation-policy.schema.json",
    "agent-recommendations.json": "agent-recommendations.schema.json",
    "context-pack.json": "context-pack.schema.json",
    "context-selection.json": "context-selection.schema.json",
    "correction-cycle.json": "correction-cycle.schema.json",
    "evidence-board.json": "evidence-board.schema.json",
    "evidence-status.json": "evidence-status.schema.json",
    "local-overlay.json": "local-overlay.schema.json",
    "mask-list.json": "mask-list.schema.json",
    "routing-feedback.json": "routing-feedback.schema.json",
    "learning-feedback.json": "learning-feedback.schema.json",
    "routing.json": "routing.schema.json",
    "skill-recommendations.json": "skill-recommendations.schema.json",
    "state.json": "state.schema.json",
    "trigger-policy.json": "trigger-policy.schema.json",
}
validators = {
    schema_name: Draft202012Validator(json.loads((root / "schemas" / schema_name).read_text(encoding="utf-8")))
    for schema_name in set(schema_by_name.values())
}
errors = []
for path in sorted((root / "examples").rglob("*.json")):
    schema_name = schema_by_name.get(path.name)
    if not schema_name:
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    for error in validators[schema_name].iter_errors(data):
        errors.append(f"{path} {error.json_path}: {error.message}")

receipt_validator = Draft202012Validator(json.loads((root / "schemas" / "receipts.schema.json").read_text(encoding="utf-8")))
for path in sorted((root / "examples").rglob("dispatch-receipts.jsonl")):
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        data = json.loads(line)
        for error in receipt_validator.iter_errors(data):
            errors.append(f"{path}:{lineno} {error.json_path}: {error.message}")

if errors:
    raise SystemExit("\n".join(errors))
PY

echo "==> Running unit tests"
"$PYTHON_BIN" -m unittest tests/test_valp_audit.py tests/test_valp_doctor.py tests/test_valp_workflow.py tests/test_schema_examples.py

echo "==> Auditing minimal example"
"$PYTHON_BIN" -m valp_cli audit examples/minimal-task

echo "==> Auditing full-mode example"
"$PYTHON_BIN" -m valp_cli audit examples/full-mode-task

echo "==> Auditing headless queue example"
"$PYTHON_BIN" -m valp_cli audit examples/headless-queue-task

echo "==> Auditing real documentation calibration case study"
"$PYTHON_BIN" -m valp_cli audit examples/real-doc-calibration-task

echo "==> Benchmarking role-budgeted dispatch size"
"$PYTHON_BIN" scripts/benchmark-dispatch-size.py

echo "==> VALP example verification complete"
