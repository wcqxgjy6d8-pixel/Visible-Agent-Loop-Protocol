#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON:-python3}"
AUDIT_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/valp-wheel-audit.XXXXXX")"

cleanup() {
  rm -rf -- "$AUDIT_ROOT"
}
trap cleanup EXIT

WHEEL_DIR="$AUDIT_ROOT/wheel"
VENV_DIR="$AUDIT_ROOT/venv"
mkdir -p "$WHEEL_DIR"

"$PYTHON_BIN" -m pip wheel . --no-deps --wheel-dir "$WHEEL_DIR" >/dev/null
WHEEL_PATH="$(find "$WHEEL_DIR" -maxdepth 1 -type f -name 'visible_agent_loop_protocol-*.whl' -print -quit)"
if [[ -z "$WHEEL_PATH" ]]; then
  echo "VALP wheel was not created" >&2
  exit 1
fi

"$PYTHON_BIN" - "$WHEEL_PATH" <<'PY'
import sys
import zipfile
from email.parser import BytesParser

wheel = sys.argv[1]
with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
    metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
    metadata = BytesParser().parsebytes(archive.read(metadata_name))

required = {
    "valp_cli/__init__.py",
    "valp_cli/__main__.py",
    "valp_cli/templates/adapter-starter/adapter.py",
}
missing = sorted(required.difference(names))
if missing:
    raise SystemExit(f"wheel is missing required CLI files: {missing}")
if metadata.get("Version") != "0.3.0rc1":
    raise SystemExit(
        f"wheel metadata is not the 0.3.0rc1 candidate: {metadata.get('Version')!r}"
    )

for name in names:
    lowered = name.casefold()
    if any(token in lowered for token in (".valp/", ".herdr-loop/", "local-qwen", "ontology-routing")):
        raise SystemExit(f"wheel contains local/private state: {name}")
PY

"$PYTHON_BIN" -m venv "$VENV_DIR"
if [[ -f "$VENV_DIR/Scripts/python.exe" ]]; then
  VENV_PYTHON="$VENV_DIR/Scripts/python.exe"
  VENV_VALP="$VENV_DIR/Scripts/valp.exe"
else
  VENV_PYTHON="$VENV_DIR/bin/python"
  VENV_VALP="$VENV_DIR/bin/valp"
fi
"$VENV_PYTHON" -m pip install --no-deps "$WHEEL_PATH" >/dev/null
"$VENV_VALP" --version | grep -Fx "valp 0.3.0rc1" >/dev/null
for profile in core-reader core-writer plugin-host migration; do
  (
    cd "$AUDIT_ROOT"
    "$VENV_VALP" conformance --profile "$profile" >/dev/null
  )
done

(
  cd "$AUDIT_ROOT"
  "$VENV_PYTHON" - <<'PY'
import valp_cli
from valp_cli.workflow import observe_source_provenance

if valp_cli.__version__ != "0.3.0rc1":
    raise SystemExit(f"installed CLI version mismatch: {valp_cli.__version__}")
if observe_source_provenance()["status"] != "unavailable":
    raise SystemExit("standalone wheel must not invent Git source provenance")
PY
)

echo "VALP wheel smoke PASS: CLI-only 0.3.0rc1 artifact; full RFC profile conformance remains a separate release gate"
