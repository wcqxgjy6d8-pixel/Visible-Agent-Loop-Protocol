from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .control_plane import ControlPlaneError, digest_without


PLUGIN_KINDS = {"discovery", "runtime_adapter", "provider_adapter", "tool", "transport"}
CORE_LEDGER_PERMISSIONS = {
    "installation.write",
    "leader.write",
    "registry.write",
    "message.write",
    "event.write",
    "state.write",
    "claim.write",
    "evidence.write",
    "failure.write",
    "review.write",
    "migration.write",
}


def validate_plugin_manifest(value: dict[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "plugin_id", "implementation_id", "plugin_kind",
        "protocol_read_versions", "protocol_write_versions", "entrypoint",
        "permissions", "provided_capabilities", "required_capabilities",
        "resource_limits", "isolation", "manifest_digest",
    }
    missing = sorted(required - set(value))
    if missing:
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Plugin manifest missing: " + ", ".join(missing))
    unknown = sorted(set(value) - required)
    if unknown:
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Unknown plugin manifest fields: " + ", ".join(unknown))
    if value.get("schema_version") != "valp-plugin-manifest.v1":
        raise ControlPlaneError("VALP-E-PROTOCOL-UNSUPPORTED", "Unsupported plugin manifest schema")
    if value.get("plugin_kind") not in PLUGIN_KINDS:
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Unknown plugin kind")
    if not isinstance(value.get("permissions"), list) or not all(isinstance(item, str) for item in value["permissions"]):
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Plugin permissions must be strings")
    denied = sorted(CORE_LEDGER_PERMISSIONS.intersection(value["permissions"]))
    if denied:
        raise ControlPlaneError("VALP-E-PLUGIN-BOUNDARY", "Plugin cannot write core ledgers: " + ", ".join(denied), state_effect="quarantine_plugin")
    if value.get("manifest_digest") != digest_without(value, "manifest_digest"):
        raise ControlPlaneError("VALP-E-MESSAGE-DIGEST", "Plugin manifest digest mismatch")
    return value


def load_plugin_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", f"Cannot read plugin manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "Plugin manifest must be an object")
    return validate_plugin_manifest(value)


def check_plugin_permission(manifest: dict[str, Any], operation: str) -> None:
    validate_plugin_manifest(manifest)
    permissions = set(manifest.get("permissions") or [])
    if operation not in permissions:
        raise ControlPlaneError("VALP-E-PLUGIN-BOUNDARY", f"Plugin lacks permission {operation}", state_effect="quarantine_plugin")
