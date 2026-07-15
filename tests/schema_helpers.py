from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
SUSPENSION_SCHEMA_PATH = ROOT / "schemas" / "suspension.schema.json"


def schema_validator(schema_path: Path) -> Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    suspension_schema = json.loads(SUSPENSION_SCHEMA_PATH.read_text(encoding="utf-8"))
    registry = Registry().with_resource(
        suspension_schema["$id"],
        Resource.from_contents(suspension_schema),
    )
    return Draft202012Validator(schema, registry=registry)
