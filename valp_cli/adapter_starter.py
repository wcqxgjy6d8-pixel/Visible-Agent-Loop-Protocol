from __future__ import annotations

import re
from importlib import resources
from pathlib import Path
from typing import Any


ADAPTER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")
TEMPLATE_FILES = (
    "README.md",
    "adapter.json",
    "adapter.py",
    "test_adapter.py",
)


class AdapterStarterError(ValueError):
    pass


def initialize_adapter(target: Path, name: str) -> dict[str, Any]:
    if not ADAPTER_NAME_PATTERN.fullmatch(name):
        raise AdapterStarterError(
            "Adapter name must start with a lowercase letter and contain only lowercase letters, digits, '-' or '_'"
        )

    destination = target.expanduser().resolve()
    if destination.exists() and not destination.is_dir():
        raise AdapterStarterError(f"Adapter target is not a directory: {destination}")
    if destination.exists() and any(destination.iterdir()):
        raise AdapterStarterError(f"Adapter target must be empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)

    template_root = resources.files("valp_cli").joinpath("templates", "adapter-starter")
    written: list[str] = []
    for filename in TEMPLATE_FILES:
        content = template_root.joinpath(filename).read_text(encoding="utf-8")
        output = destination / filename
        output.write_text(content.replace("{{adapter_name}}", name), encoding="utf-8")
        written.append(filename)
    (destination / "adapter.py").chmod(0o755)

    return {
        "schema_version": "valp-adapter-starter-result.v1",
        "status": "created",
        "adapter_name": name,
        "target": str(destination),
        "files": written,
        "verification_command": f"python3 -m unittest discover -s {destination} -p 'test_*.py'",
    }
