#!/usr/bin/env bash
set -euo pipefail

case_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$case_dir/../.." && pwd)"
task_id="VALP-NON-HERDR-E2E-001"
demo_port="${VALP_DEMO_PORT:-8124}"
demo_workspace="$(mktemp -d "${TMPDIR:-/tmp}/valp-langgraph-repro.XXXXXX")"
task_dir="$demo_workspace/.herdr-loop/tasks/$task_id"
venv_dir="$case_dir/.venv-py312"
server_log="$demo_workspace/langgraph-server.log"
server_pid=""

stop_server() {
  if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
    kill "$server_pid"
    wait "$server_pid" 2>/dev/null || true
  fi
}
trap stop_server EXIT

if [[ ! -x "$venv_dir/bin/langgraph" ]]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required to create the Python 3.12 LangGraph environment" >&2
    exit 1
  fi
  uv venv --python 3.12 "$venv_dir"
  uv pip install --python "$venv_dir/bin/python" -r "$case_dir/requirements.txt"
fi

mkdir -p "$demo_workspace/.herdr-loop/tasks"
cp -R "$case_dir/task" "$task_dir"
python3 - "$task_dir" <<'PY'
import json
import shutil
import sys
from pathlib import Path

task_dir = Path(sys.argv[1])
shutil.rmtree(task_dir / "runtime", ignore_errors=True)
for relative in (
    "agents/langgraph_coordinator/self-review.md",
    "agents/langgraph_worker/evidence.md",
    "agents/langgraph_reviewer/review.md",
    "evidence/verification.md",
    "evidence/first-failure-audit.md",
    "agent-recommendations.json",
    "correction-cycle.json",
    "final-synthesis.md",
    "learning-feedback.json",
    "routing-feedback.json",
):
    (task_dir / relative).unlink(missing_ok=True)

ledger = task_dir / "dispatch-receipts.jsonl"
written = [
    json.loads(line)
    for line in ledger.read_text(encoding="utf-8").splitlines()
    if line.strip() and json.loads(line).get("event") == "dispatch_written"
]
ledger.write_text(
    "".join(json.dumps(receipt, ensure_ascii=False) + "\n" for receipt in written),
    encoding="utf-8",
)

state_path = task_dir / "state.json"
state = json.loads(state_path.read_text(encoding="utf-8"))
state["status"] = "executing"
state["revision"] = 0
state["gates"].update(
    {
        "dispatch_receipts": "pending",
        "expected_evidence": "pending",
        "verification": "pending",
        "review": "pending",
    }
)
state["agent_recommendations"] = {"status": "expected", "ref": "agent-recommendations.json"}
state["final_synthesis"] = {"status": "expected", "ref": "final-synthesis.md"}
state["routing_feedback"] = {"status": "expected", "ref": "routing-feedback.json"}
state["learning_feedback"] = {"status": "expected", "ref": "learning-feedback.json"}
state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

export VALP_DEMO_WORKSPACE="$demo_workspace"
export VALP_LANGGRAPH_API_URL="http://127.0.0.1:$demo_port"

(
  cd "$case_dir"
  "$venv_dir/bin/langgraph" dev --config langgraph.json --no-browser --port "$demo_port" >"$server_log" 2>&1
) &
server_pid=$!

for _ in $(seq 1 120); do
  if curl -fsS "$VALP_LANGGRAPH_API_URL/ok" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "LangGraph server exited before readiness; log: $server_log" >&2
    exit 1
  fi
  sleep 0.25
done
curl -fsS "$VALP_LANGGRAPH_API_URL/ok" >/dev/null

preflight_json="$demo_workspace/preflight.json"
"$repo_root/bin/valp" preflight \
  --runtime langgraph \
  --agent langgraph_coordinator \
  --agent langgraph_worker \
  --agent langgraph_reviewer \
  --json >"$preflight_json"

coordinator_json="$demo_workspace/coordinator.json"
"$repo_root/bin/valp" adapter langgraph run "$task_id" \
  --workspace "$demo_workspace" \
  --agent langgraph_coordinator \
  --role coordinator \
  --wait-seconds 30 \
  --json >"$coordinator_json"

first_json="$demo_workspace/first-false-done.json"
set +e
"$repo_root/bin/valp" adapter langgraph run "$task_id" \
  --workspace "$demo_workspace" \
  --agent langgraph_worker \
  --role implementer \
  --wait-seconds 30 \
  --json >"$first_json"
first_exit=$?
set -e
if [[ "$first_exit" -eq 0 ]]; then
  echo "Expected the first false-done run to be blocked" >&2
  exit 1
fi

thread_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run"]["thread_id"])' "$first_json")"
first_run_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run"]["run_id"])' "$first_json")"

first_audit="$demo_workspace/first-failure-audit.txt"
set +e
"$repo_root/bin/valp" audit "$task_dir" >"$first_audit"
first_audit_exit=$?
set -e
if [[ "$first_audit_exit" -eq 0 ]]; then
  echo "Expected the first task audit to fail before repair" >&2
  exit 1
fi
python3 - "$task_dir/evidence/first-failure-audit.md" "$first_audit" "$first_run_id" <<'PY'
import sys
from pathlib import Path

output_path = Path(sys.argv[1])
audit_path = Path(sys.argv[2])
run_id = sys.argv[3]
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_text(
    "# First False-Done Audit\n\n"
    f"LangGraph run `{run_id}` returned runtime success without the expected evidence.\n\n"
    "```text\n"
    + audit_path.read_text(encoding="utf-8").rstrip()
    + "\n```\n",
    encoding="utf-8",
)
PY

repair_json="$demo_workspace/repair.json"
"$repo_root/bin/valp" adapter langgraph run "$task_id" \
  --workspace "$demo_workspace" \
  --agent langgraph_worker \
  --role implementer \
  --thread-id "$thread_id" \
  --input-json '{"attempt":"repair"}' \
  --wait-seconds 30 \
  --json >"$repair_json"

review_json="$demo_workspace/review.json"
"$repo_root/bin/valp" adapter langgraph run "$task_id" \
  --workspace "$demo_workspace" \
  --agent langgraph_reviewer \
  --role reviewer \
  --wait-seconds 30 \
  --json >"$review_json"

coordinator_run_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run"]["run_id"])' "$coordinator_json")"
repair_run_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run"]["run_id"])' "$repair_json")"
review_run_id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run"]["run_id"])' "$review_json")"

python3 - "$case_dir/task" "$task_dir" "$preflight_json" \
  "$coordinator_run_id" "$first_run_id" "$repair_run_id" "$review_run_id" "$thread_id" <<'PY'
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

source = Path(sys.argv[1])
task_dir = Path(sys.argv[2])
preflight = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
live_ids = sys.argv[4:9]
fixture_ids = (
    "019f7e44-8147-7942-bfc7-16c116b73a2a",
    "019f7e44-b84c-7061-80bc-14ebd75c10c3",
    "019f7e45-7266-75c0-9301-d73f77714f34",
    "019f7e45-aaf0-7f61-8d4c-856a446b5b35",
    "019f7e44-b84b-7893-bd81-d2b0e6d28a22",
)
for name in (
    "agent-recommendations.json",
    "correction-cycle.json",
    "final-synthesis.md",
    "learning-feedback.json",
    "routing-feedback.json",
):
    shutil.copyfile(source / name, task_dir / name)

replacements = dict(zip(fixture_ids, live_ids))
for path in task_dir.rglob("*"):
    if not path.is_file() or path.suffix not in {".json", ".jsonl", ".md"}:
        continue
    content = path.read_text(encoding="utf-8")
    for old, new in replacements.items():
        content = content.replace(old, new)
    path.write_text(content, encoding="utf-8")

state_path = task_dir / "state.json"
state = json.loads((source / "state.json").read_text(encoding="utf-8"))
state["runtime_adapter"]["preflight"] = preflight
state["updated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

routing_path = task_dir / "routing.json"
routing = json.loads(routing_path.read_text(encoding="utf-8"))
routing["runtime_adapter"]["preflight"] = preflight
routing_path.write_text(json.dumps(routing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY

python3 -c '
import json, pathlib, sys
ledger = pathlib.Path(sys.argv[1])
events = [json.loads(line)["event"] for line in ledger.read_text().splitlines() if line.strip()]
expected = [
    "dispatch_written", "dispatch_written", "dispatch_written",
    "dispatch_submitted", "dispatch_completed",
    "dispatch_submitted", "dispatch_blocked",
    "dispatch_submitted", "dispatch_completed",
    "dispatch_submitted", "dispatch_completed",
]
if events != expected:
    raise SystemExit(f"unexpected receipt sequence: {events}")
print("receipt sequence PASS:", " -> ".join(events))
' "$task_dir/dispatch-receipts.jsonl"

"$repo_root/bin/valp" audit "$task_dir"

echo "coordinator_run_id=$coordinator_run_id"
echo "false_done_run_id=$first_run_id"
echo "worker_thread_id=$thread_id"
echo "repair_run_id=$repair_run_id"
echo "review_run_id=$review_run_id"
echo "reproduction_workspace=$demo_workspace"
