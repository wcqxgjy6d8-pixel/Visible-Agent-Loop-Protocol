from __future__ import annotations

import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]

EXAMPLE_SCHEMA_BY_NAME = {
    "attention-map.json": "attention-map.schema.json",
    "agent-recommendations.json": "agent-recommendations.schema.json",
    "context-selection.json": "context-selection.schema.json",
    "correction-cycle.json": "correction-cycle.schema.json",
    "evidence-board.json": "evidence-board.schema.json",
    "evidence-status.json": "evidence-status.schema.json",
    "local-overlay.json": "local-overlay.schema.json",
    "mask-list.json": "mask-list.schema.json",
    "routing-feedback.json": "routing-feedback.schema.json",
    "routing.json": "routing.schema.json",
    "skill-recommendations.json": "skill-recommendations.schema.json",
    "state.json": "state.schema.json",
    "trigger-policy.json": "trigger-policy.schema.json",
}


class SchemaExampleTests(unittest.TestCase):
    def test_bundled_json_examples_match_schemas(self) -> None:
        validators = {
            schema_name: Draft202012Validator(
                json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
            )
            for schema_name in set(EXAMPLE_SCHEMA_BY_NAME.values())
        }
        errors: list[str] = []
        for path in sorted((ROOT / "examples").rglob("*.json")):
            schema_name = EXAMPLE_SCHEMA_BY_NAME.get(path.name)
            if not schema_name:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            for error in validators[schema_name].iter_errors(data):
                errors.append(f"{path.relative_to(ROOT)} {error.json_path}: {error.message}")
        self.assertEqual(errors, [])

    def test_bundled_receipt_jsonl_examples_match_schema(self) -> None:
        schema = json.loads((ROOT / "schemas" / "receipts.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        errors: list[str] = []
        for path in sorted((ROOT / "examples").rglob("dispatch-receipts.jsonl")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                data = json.loads(line)
                for error in validator.iter_errors(data):
                    errors.append(f"{path.relative_to(ROOT)}:{lineno} {error.json_path}: {error.message}")
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
