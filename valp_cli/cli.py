from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .audit import FAIL, TaskAudit, print_text_report, report_to_dict, resolve_task_dir
from .cost_governance import CostGovernanceError, append_event, build_cost_report, enforce_cost_budget, estimate_usage
from .catalog import CatalogError, EvidenceCatalog
from .doctor import collect_doctor_report, render_text_summary, report_to_dict as doctor_report_to_dict, write_markdown_report
from .control_plane import (
    ControlPlaneError,
    InstallationCore,
    PROTOCOL_VERSION,
    installation_root,
    leader_installation_root,
    load_observations,
)
from .conformance import run_conformance
from .adapter_starter import AdapterStarterError, initialize_adapter
from .plugins import load_plugin_manifest
from .task_control import TASK_STATUSES, init_task, replay_task, task_state, transition_task
from .process_adapter import run_process
from .remediation import (
    RemediationError,
    build_repair_plan,
    build_recovery_plan,
    collect_doctor_with_snapshot,
    execute_repair_plan,
    read_json_object as read_remediation_json,
    verify_repair_receipt,
    write_json_atomic as write_remediation_json,
)
from .langgraph_adapter import LangGraphAdapterError, resume_langgraph_run, submit_langgraph_run
from .task_graph import build_task_graph, render_task_graph
from .herdr_adapter import (
    HerdrAutoVisibleWatcher,
    HerdrSubmissionError,
    open_herdr_leader_session,
    provision_herdr_leader_session,
    recover_herdr_leader_session,
)
from .workflow import RUNTIME_CHOICES, collect_runtime_preflight, continue_accepted_dependency_ready_wake, dispatch_task, publish_task, read_json, resume_suspended_task, route_task, run_command, scan_workspace, wait_for_task


def split_worker_command(command: str) -> list[str]:
    parts = shlex.split(command, posix=os.name != "nt")
    if os.name != "nt":
        return parts
    return [
        part[1:-1]
        if len(part) >= 2 and part[0] == part[-1] and part[0] in {'"', "'"}
        else part
        for part in parts
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="valp",
        description="VALP reference CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  valp audit examples/minimal-task
  valp doctor --workspace .
  valp publish TASK-001 --workspace . --prompt "Fix the bug and verify it"
  valp dispatch TASK-001 --workspace .

notes:
  dispatch prints Manual Mode instructions for manual tasks.
  dispatch submits through the selected reference adapter when supported.
  HERDR is the reference runtime, not a VALP protocol requirement.
""",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    publish = sub.add_parser("publish", help="Create a VALP task and wait for Leader-declared assignments")
    publish.add_argument("task_id", help="Task id")
    publish.add_argument("--workspace", default=".", help="Workspace root")
    publish.add_argument("--prompt", help="Task request")
    publish.add_argument("--prompt-file", help="Read task request from a file")
    publish.add_argument("--profile", help="Override auto profile classification")
    publish.add_argument("--runtime", choices=sorted(RUNTIME_CHOICES), default="auto", help="Runtime adapter to record and preflight")
    publish.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    watcher_intake = sub.add_parser(
        "watcher-intake",
        help="Publish one HERDR Auto Visible watcher event with durable replay suppression",
    )
    watcher_intake.add_argument("--workspace", default=".", help="Workspace root")
    watcher_intake.add_argument("--task-id", required=True, help="Task id for the first publication")
    watcher_intake.add_argument("--prompt", required=True, help="Task request for the first publication")
    watcher_intake.add_argument("--profile", help="Override auto profile classification")
    watcher_intake.add_argument("--runtime", choices=sorted(RUNTIME_CHOICES), default="auto", help="Runtime adapter to record")
    watcher_event = watcher_intake.add_mutually_exclusive_group(required=True)
    watcher_event.add_argument("--event", help="Structured watcher event as JSON")
    watcher_event.add_argument("--event-file", help="Read structured watcher event JSON from a file")
    watcher_intake.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    scan = sub.add_parser("scan", help="Scan local capabilities and overlay into a workspace")
    scan.add_argument("--workspace", default=".", help="Workspace root")
    scan.add_argument("--task", dest="task_id", help="Task id to update")
    scan.add_argument("--runtime", choices=sorted(RUNTIME_CHOICES), default="auto", help="Runtime adapter to preflight")
    scan.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    route = sub.add_parser("route", help="Validate and record Leader-declared assignments")
    route.add_argument("task_id", help="Task id")
    route.add_argument("--workspace", default=".", help="Workspace root")
    route.add_argument("--runtime", choices=sorted(RUNTIME_CHOICES), default="auto", help="Runtime adapter to record and preflight")
    route.add_argument("--assignments", required=True, help="Leader-authored assignment declaration JSON")
    route.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    dispatch = sub.add_parser("dispatch", help="Print dispatch instructions or submit through the selected reference adapter")
    dispatch.add_argument("task_id", help="Task id")
    dispatch.add_argument("--workspace", default=".", help="Workspace root")
    dispatch.add_argument("--agent", default="all", help="Agent name or all")
    dispatch.add_argument(
        "--role",
        choices=["coordinator", "implementer", "reviewer", "prototype", "researcher", "other"],
        help="Submit only the named role phase; required to disambiguate co-located roles",
    )
    dispatch.add_argument("--runtime", choices=sorted(RUNTIME_CHOICES), default="auto", help="Override the runtime adapter recorded in routing.json")
    dispatch.add_argument("--wait-seconds", type=float, help="Non-negative HERDR evidence wait timeout for submitted dispatches")
    dispatch.add_argument("--proof-seconds", type=float, help="Non-negative HERDR submission proof timeout for submitted dispatches")
    dispatch.add_argument(
        "--recover-incomplete",
        action="store_true",
        help="Resubmit one explicitly selected HERDR work item whose proven submission produced no evidence",
    )
    dispatch.add_argument(
        "--retry-generation",
        type=int,
        help="Explicit incomplete-submission retry generation; the bounded reference path accepts only 1",
    )
    dispatch.add_argument(
        "--replace-owned-session-launch",
        action="store_true",
        help=(
            "Allow one explicitly targeted absent task-owned HERDR session to use "
            "the current capability launch argv in its next generation"
        ),
    )
    dispatch.add_argument(
        "--reprovision-done-session",
        action="store_true",
        help=(
            "Fence and replace one explicitly targeted done task-owned HERDR session "
            "before any delivery receipt exists"
        ),
    )
    dispatch.add_argument("--submit", action="store_true", help="Actually submit through the selected reference adapter when supported")

    preflight = sub.add_parser("preflight", help="Check selected runtime adapter readiness")
    preflight.add_argument("--agent", action="append", help="Agent name to check; may be repeated")
    preflight.add_argument("--runtime", choices=sorted(RUNTIME_CHOICES), default="auto", help="Runtime adapter to preflight")
    preflight.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    wait = sub.add_parser("wait", help="Suspend coordinator turns until a deterministic resume event")
    wait.add_argument("task_id", help="Task id")
    wait.add_argument("--workspace", default=".", help="Workspace root")
    wait.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Observation window in seconds; expiry leaves workers running and the task suspended",
    )
    wait.add_argument(
        "--execution-timeout",
        type=float,
        help="Protocol execution deadline in seconds; required only when creating a new suspension",
    )
    wait.add_argument("--poll-interval", type=float, default=0.25, help="Runtime polling interval in seconds")
    wait.add_argument("--herdr-continuation-socket", help="Unix socket for an active HERDR Leader continuation")
    wait.add_argument("--herdr-continuation-provider", help="Observed provider identity required by HERDR continuation receipts")
    wait.add_argument("--herdr-continuation-timeout", type=float, default=30.0, help="HERDR continuation RPC timeout in seconds")
    wait.add_argument(
        "--herdr-continuation-approval-granted",
        action="store_true",
        help="Explicitly grant approval to the HERDR continuation; defaults to false",
    )
    wait.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    resume = sub.add_parser(
        "resume",
        help="Resume a suspension or recover an accepted timeout from a late completion receipt",
    )
    resume.add_argument("task_id", help="Task id")
    resume.add_argument("--workspace", default=".", help="Workspace root")
    resume.add_argument(
        "--event",
        choices=["receipt", "user_input", "runtime_failure", "cancellation"],
        required=True,
        help="External event, or receipt for an identity-bound late completion recovery",
    )
    resume.add_argument(
        "--ref",
        dest="resume_ref",
        required=True,
        help="Task-local valp-exception-wake.v1 evidence ref",
    )
    resume.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    resume.add_argument("--herdr-continuation-socket", help="Unix socket for an active HERDR Leader continuation")
    resume.add_argument("--herdr-continuation-provider", help="Observed provider identity required by HERDR continuation receipts")
    resume.add_argument("--herdr-continuation-timeout", type=float, default=30.0, help="HERDR continuation RPC timeout in seconds")
    resume.add_argument(
        "--herdr-continuation-approval-granted",
        action="store_true",
        help="Explicitly grant approval to the HERDR continuation; defaults to false",
    )

    install = sub.add_parser("install", help="Manage the v0.3 installation control plane")
    install_sub = install.add_subparsers(dest="install_command", required=True)
    install_init = install_sub.add_parser("init", help="Create a persistent control root and bootstrap metadata")
    install_init.add_argument("--workspace", default=".", help="Workspace root")
    install_init.add_argument("--root", help="Explicit control root; defaults to <workspace>/.valp")
    install_init.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    leader = sub.add_parser(
        "leader",
        help="Discover, select, start, open, recover, restart, inspect, or rotate the Installation Leader",
    )
    leader_sub = leader.add_subparsers(dest="leader_command", required=True)
    candidates = leader_sub.add_parser("candidates", help="Run bounded read-only bootstrap discovery")
    candidates.add_argument("--workspace", default=".", help="Workspace root")
    candidates.add_argument("--root", help="Explicit control root")
    candidates.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    select = leader_sub.add_parser("select", help="Explicitly select a discovered Leader without starting it")
    select.add_argument("principal", help="Observed principal id")
    select.add_argument("--workspace", default=".", help="Workspace root")
    select.add_argument("--root", help="Explicit control root")
    select.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    start = leader_sub.add_parser("start", help="Provision and activate the selected Installation Leader")
    start.add_argument("--workspace", default=".", help="Workspace root")
    start.add_argument("--root", help="Explicit control root")
    start.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    open_leader = leader_sub.add_parser(
        "open",
        help="Open the installation Leader from any caller workspace",
    )
    open_leader.add_argument(
        "--workspace",
        default=".",
        help="Caller workspace or cwd for a replacement attachment",
    )
    open_leader.add_argument("--root", help="Explicit control root")
    open_leader.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    recover_start = leader_sub.add_parser(
        "recover-start",
        help="Recover one explicitly approved failed first-start session without mutating runtime state",
    )
    recover_start.add_argument(
        "--session",
        required=True,
        help="Exact runtime session id from the failed first start",
    )
    recover_start.add_argument(
        "--approve",
        action="store_true",
        help="Record explicit user approval for this exact recovery session",
    )
    recover_start.add_argument("--workspace", default=".", help="Workspace root")
    recover_start.add_argument("--root", help="Explicit control root")
    recover_start.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    restart = leader_sub.add_parser("restart", help="Fence and replace the active Leader with a fresh session")
    restart.add_argument("--workspace", default=".", help="Workspace root")
    restart.add_argument("--root", help="Explicit control root")
    restart.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    show = leader_sub.add_parser("show", help="Show the current leader and epoch")
    show.add_argument("--workspace", default=".", help="Workspace root")
    show.add_argument("--root", help="Explicit control root")
    show.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    rotate = leader_sub.add_parser("rotate", help="Rotate the leader with explicit user approval")
    rotate.add_argument("principal", help="Observed replacement principal id")
    rotate.add_argument("--workspace", default=".", help="Workspace root")
    rotate.add_argument("--root", help="Explicit control root")
    rotate.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    capabilities = sub.add_parser("capabilities", help="Reconcile layered capability observations")
    capabilities_sub = capabilities.add_subparsers(dest="capabilities_command", required=True)
    reconcile = capabilities_sub.add_parser("reconcile", help="Append observations and rebuild the registry projection")
    reconcile.add_argument("--observations", required=True, help="JSON file containing an observations array")
    reconcile.add_argument("--workspace", default=".", help="Workspace root")
    reconcile.add_argument("--root", help="Explicit control root")
    reconcile.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    cp_status = sub.add_parser("status", help="Show installation state and fixed hello response")
    cp_status.add_argument("--workspace", default=".", help="Workspace root")
    cp_status.add_argument("--root", help="Explicit control root")
    cp_status.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    state = sub.add_parser("state", help="Show or replay installation and task projections")
    state_sub = state.add_subparsers(dest="state_command", required=True)
    state_show = state_sub.add_parser("show", help="Show an executable projection")
    state_show.add_argument("--task", dest="task_id", help="Show one control-plane task projection")
    state_show.add_argument("--workspace", default=".", help="Workspace root")
    state_show.add_argument("--root", help="Explicit control root")
    state_show.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    state_replay = state_sub.add_parser("replay", help="Replay and compare an executable projection")
    state_replay.add_argument("--task", dest="task_id", help="Replay one control-plane task projection")
    state_replay.add_argument("--check", action="store_true", help="Fail if replay differs from persisted state")
    state_replay.add_argument("--workspace", default=".", help="Workspace root")
    state_replay.add_argument("--root", help="Explicit control root")
    state_replay.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    hello = sub.add_parser("hello", help="Run the fixed valp-hello.v1 discovery boundary")
    hello.add_argument("--workspace", default=".", help="Workspace root")
    hello.add_argument("--root", help="Explicit control root")
    hello.add_argument("--nonce", help="Canonical base64url correlation nonce")
    hello.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    migrate = sub.add_parser("protocol", help="Plan or apply explicit protocol migrations")
    migrate_sub = migrate.add_subparsers(dest="protocol_command", required=True)
    migrate_plan = migrate_sub.add_parser("migrate", help="Create a migration plan or apply an approved plan")
    migrate_plan.add_argument("--to", default=PROTOCOL_VERSION, help="Target protocol version")
    migrate_plan.add_argument("--workspace", default=".", help="Workspace root")
    migrate_plan.add_argument("--root", help="Explicit control root")
    migrate_plan.add_argument("--dry-run", action="store_true", help="Create a plan without activation")
    migrate_plan.add_argument("--plan", help="Apply this exact digest-matched plan file")
    migrate_plan.add_argument("--apply", action="store_true", help="Apply the existing plan")
    migrate_plan.add_argument("--approve", action="store_true", help="Explicitly approve migration side effects")
    migrate_plan.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    plugin = sub.add_parser("plugin", help="Validate provider-neutral plugin manifests")
    plugin_sub = plugin.add_subparsers(dest="plugin_command", required=True)
    plugin_validate = plugin_sub.add_parser("validate", help="Validate a plugin manifest without enabling it")
    plugin_validate.add_argument("path", help="Plugin manifest JSON path")
    plugin_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    conformance = sub.add_parser("conformance", help="Run isolated v0.3 profile-scoped smoke fixtures")
    conformance.add_argument("--profile", default="core-writer", choices=["core-reader", "core-writer", "plugin-host", "migration"], help="Implemented smoke profile")
    conformance.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    evidence = sub.add_parser("evidence", help="Record content-addressed installation evidence")
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)
    evidence_add = evidence_sub.add_parser("add", help="Add a control-root-relative evidence file")
    evidence_add.add_argument("path", help="Control-root-relative file path")
    evidence_add.add_argument("--kind", required=True, dest="evidence_kind")
    evidence_add.add_argument("--producer", required=True, dest="producer_principal_id")
    evidence_add.add_argument("--workspace", default=".", help="Workspace root")
    evidence_add.add_argument("--root", help="Explicit control root")
    evidence_add.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    claim = sub.add_parser("claim", help="Declare an installation claim bound to evidence")
    claim_sub = claim.add_subparsers(dest="claim_command", required=True)
    claim_declare = claim_sub.add_parser("declare", help="Declare a structured claim")
    claim_declare.add_argument("subject_ref")
    claim_declare.add_argument("--kind", required=True, dest="claim_kind")
    claim_declare.add_argument("--predicate", required=True)
    claim_declare.add_argument("--value", required=True, help="JSON value")
    claim_declare.add_argument("--scope", required=True)
    claim_declare.add_argument("--claimant", required=True, dest="claimant_principal_id")
    claim_declare.add_argument("--evidence", action="append", default=[], dest="evidence_refs")
    claim_declare.add_argument("--workspace", default=".", help="Workspace root")
    claim_declare.add_argument("--root", help="Explicit control root")
    claim_declare.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    review = sub.add_parser("review", help="Record an exact-digest independent review")
    review_sub = review.add_subparsers(dest="review_command", required=True)
    review_record = review_sub.add_parser("record", help="Record a review and derived claim status")
    review_record.add_argument("claim_id")
    review_record.add_argument("--reviewer", required=True, dest="reviewer_principal_id")
    review_record.add_argument("--verdict", required=True, choices=["pass", "fail", "abstain", "blocked"])
    review_record.add_argument("--workspace", default=".", help="Workspace root")
    review_record.add_argument("--root", help="Explicit control root")
    review_record.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    task = sub.add_parser("task", help="Apply the v0.3 legal task-state reducer")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    task_init = task_sub.add_parser("init", help="Create a control-plane task projection")
    task_init.add_argument("task_id")
    task_init.add_argument("--workspace", default=".", help="Workspace root")
    task_init.add_argument("--root", help="Explicit control root")
    task_init.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    task_status = task_sub.add_parser("status", help="Show a control-plane task projection")
    task_status.add_argument("task_id")
    task_status.add_argument("--workspace", default=".", help="Workspace root")
    task_status.add_argument("--root", help="Explicit control root")
    task_status.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    task_transition = task_sub.add_parser("transition", help="Apply one legal task transition")
    task_transition.add_argument("task_id")
    task_transition.add_argument("--to", required=True, choices=sorted(TASK_STATUSES))
    task_transition.add_argument("--expected-revision", type=int)
    task_transition.add_argument("--gates", help="JSON object of gate results")
    task_transition.add_argument("--actor", default="installation-leader")
    task_transition.add_argument("--workspace", default=".", help="Workspace root")
    task_transition.add_argument("--root", help="Explicit control root")
    task_transition.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    kernel = sub.add_parser("kernel", help="Inspect and reconcile durable pure-Kernel effects")
    kernel_sub = kernel.add_subparsers(dest="kernel_command", required=True)
    kernel_effects = kernel_sub.add_parser("effects", help="Inspect or record accepted Kernel effects")
    kernel_effects_sub = kernel_effects.add_subparsers(dest="kernel_effects_command", required=True)
    kernel_effects_status = kernel_effects_sub.add_parser(
        "status", help="Reconcile accepted obligations against durable effect proof"
    )
    kernel_effects_status.add_argument("task_id")
    kernel_effects_status.add_argument("--workspace", default=".", help="Workspace root")
    kernel_effects_status.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    kernel_effects_record = kernel_effects_sub.add_parser(
        "record", help="Record one fulfilled or blocked Adapter effect"
    )
    kernel_effects_record.add_argument("task_id")
    kernel_effects_record.add_argument("--obligation", required=True)
    kernel_effects_record.add_argument("--status", required=True, choices=["fulfilled", "blocked"])
    kernel_effects_record.add_argument("--proof-ref", required=True, help="Task-local effect proof ref")
    kernel_effects_record.add_argument("--workspace", default=".", help="Workspace root")
    kernel_effects_record.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    kernel_effects_execute = kernel_effects_sub.add_parser(
        "execute", help="Execute one accepted cancellation effect through its runtime Adapter"
    )
    kernel_effects_execute.add_argument("task_id")
    kernel_effects_execute.add_argument("--obligation", required=True)
    kernel_effects_execute.add_argument(
        "--approve", action="store_true", help="Approve the exact external cancellation operation"
    )
    kernel_effects_execute.add_argument("--workspace", default=".", help="Workspace root")
    kernel_effects_execute.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    kernel_control = kernel_sub.add_parser(
        "control", help="Apply one canonical authority-bound Kernel control Event"
    )
    kernel_control.add_argument("task_id")
    kernel_control.add_argument("--event-ref", required=True, help="Task-local canonical Event JSON")
    kernel_control.add_argument("--authority-ref", required=True, help="Task-local authority Evidence")
    kernel_control.add_argument("--approve", action="store_true", help="Approve the exact control Event")
    kernel_control.add_argument("--workspace", default=".", help="Workspace root")
    kernel_control.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    adapter = sub.add_parser("adapter", help="Run an explicit runtime adapter")
    adapter_sub = adapter.add_subparsers(dest="adapter_command", required=True)
    adapter_init = adapter_sub.add_parser("init", help="Create a provider-neutral adapter starter")
    adapter_init.add_argument("target", help="New or empty output directory")
    adapter_init.add_argument("--name", required=True, help="Lowercase adapter identifier")
    adapter_init.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    process = adapter_sub.add_parser("process", help="Use the real local-process Full Mode adapter")
    process_sub = process.add_subparsers(dest="process_command", required=True)
    process_run = process_sub.add_parser("run", help="Dry-run or execute one addressable local worker")
    process_run.add_argument("task_id")
    process_run.add_argument(
        "--command",
        dest="worker_command",
        required=True,
        help="Worker command parsed with shell-like quoting, without a shell",
    )
    process_run.add_argument("--timeout", type=float, default=30.0)
    process_run.add_argument("--approve", action="store_true", help="Approve actual worker execution")
    process_run.add_argument("--workspace", default=".", help="Workspace/control root parent")
    process_run.add_argument("--root", help="Explicit control root")
    process_run.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    langgraph = adapter_sub.add_parser("langgraph", help="Use a LangGraph API runtime without HERDR")
    langgraph_sub = langgraph.add_subparsers(dest="langgraph_command", required=True)
    langgraph_run = langgraph_sub.add_parser("run", help="Submit and evidence-gate one LangGraph run")
    langgraph_run.add_argument("task_id")
    langgraph_run.add_argument("--workspace", default=".", help="Workspace root")
    langgraph_run.add_argument("--agent", required=True)
    langgraph_run.add_argument("--role", required=True, choices=["coordinator", "implementer", "reviewer", "prototype", "researcher", "other"])
    langgraph_run.add_argument("--graph-id", help="LangGraph graph/assistant identifier; defaults to agent")
    langgraph_run.add_argument("--thread-id", help="Reuse a prior LangGraph thread for repair or replay")
    langgraph_run.add_argument("--input-json", default="{}", help="JSON object passed to the graph")
    langgraph_run.add_argument("--expected-ref", action="append", help="Override expected task-local evidence ref; repeatable")
    langgraph_run.add_argument("--api-url", help="LangGraph API base URL")
    langgraph_run.add_argument("--wait-seconds", type=float, default=30.0, help="Pause window; expiry keeps the runtime job alive")
    langgraph_run.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    langgraph_resume = langgraph_sub.add_parser("resume", help="Resume waiting for an existing LangGraph run without resubmitting")
    langgraph_resume.add_argument("task_id")
    langgraph_resume.add_argument("--workspace", default=".", help="Workspace root")
    langgraph_resume.add_argument("--run-id", required=True, help="Existing LangGraph runtime run ID")
    langgraph_resume.add_argument("--wait-seconds", type=float, default=30.0, help="Pause window; expiry keeps the runtime job alive")
    langgraph_resume.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    herdr_runtime = adapter_sub.add_parser(
        "herdr", help="Observe the packaged HERDR runtime"
    )
    herdr_runtime_sub = herdr_runtime.add_subparsers(
        dest="herdr_runtime_command", required=True
    )
    herdr_observe = herdr_runtime_sub.add_parser(
        "observe", help="Append a terminal receipt from an identity-bound HERDR observation"
    )
    herdr_observe.add_argument("task_id", help="Task id")
    herdr_observe.add_argument("--workspace", default=".", help="Workspace root")
    herdr_observe.add_argument("--agent", required=True, help="Exact routed Agent")
    herdr_observe.add_argument(
        "--role", required=True,
        choices=["coordinator", "implementer", "reviewer", "prototype", "researcher", "other"],
    )
    herdr_observe.add_argument("--attempt-id", help="Exact submitted Attempt when more than one exists")
    herdr_observe.add_argument(
        "--observation-ref", required=True, help="Task-local HERDR terminal observation JSON ref"
    )
    herdr_observe.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    queue = adapter_sub.add_parser("queue", help="Observe the durable file Queue runtime")
    queue_sub = queue.add_subparsers(dest="queue_command", required=True)
    for queue_action, queue_help in (
        ("claim", "Atomically claim one accepted Queue Attempt"),
        ("cancel", "Request cancellation on one accepted Queue Attempt"),
        ("ack-cancel", "Acknowledge cancellation from the exact claimed worker"),
    ):
        queue_parser = queue_sub.add_parser(queue_action, help=queue_help)
        queue_parser.add_argument("task_id", help="Task id")
        queue_parser.add_argument("--workspace", default=".", help="Workspace root")
        queue_parser.add_argument("--agent", required=True, help="Exact routed Agent")
        queue_parser.add_argument(
            "--role", required=True,
            choices=["coordinator", "implementer", "reviewer", "prototype", "researcher", "other"],
        )
        queue_parser.add_argument("--attempt-id", help="Exact submitted Attempt when more than one exists")
        queue_parser.add_argument("--expected-revision", required=True, type=int, help="Expected Queue lifecycle revision")
        queue_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
        if queue_action in {"claim", "ack-cancel"}:
            queue_parser.add_argument("--worker-id", required=True, help="Exact worker identity")
            queue_parser.add_argument("--run-id", required=True, help="Exact worker run identity")
            queue_parser.add_argument("--claim-token", required=True, help="Opaque claim fencing token")
        if queue_action == "cancel":
            queue_parser.add_argument("--authority", required=True, help="Authorized cancellation principal")
            queue_parser.add_argument("--reason", required=True, help="Cancellation reason")
        if queue_action == "ack-cancel":
            queue_parser.add_argument("--claim-event-id", required=True, help="Accepted claim lifecycle event id")
    queue_observe = queue_sub.add_parser(
        "observe", help="Append a terminal receipt from a real worker observation"
    )
    queue_observe.add_argument("task_id", help="Task id")
    queue_observe.add_argument("--workspace", default=".", help="Workspace root")
    queue_observe.add_argument("--agent", required=True, help="Exact routed Agent")
    queue_observe.add_argument(
        "--role",
        required=True,
        choices=["coordinator", "implementer", "reviewer", "prototype", "researcher", "other"],
    )
    queue_observe.add_argument("--attempt-id", help="Exact submitted Attempt when more than one exists")
    queue_observe.add_argument(
        "--observation-ref", required=True, help="Task-local Queue worker observation JSON ref"
    )
    queue_observe.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    continuation = adapter_sub.add_parser(
        "continuation", help="Consume a persisted runtime-control continuation"
    )
    continuation.add_argument("task_id", help="Task id")
    continuation.add_argument("--workspace", default=".", help="Workspace root")
    continuation.add_argument(
        "--command-json", required=True,
        help="JSON argv array for the provider-owned runtime-control process",
    )
    continuation.add_argument("--provider-id", required=True, help="Exact provider id")
    continuation.add_argument(
        "--coordinator-surface", required=True, help="Exact coordinator surface"
    )
    continuation.add_argument(
        "--identity-evidence-ref", required=True,
        help="Task-local provider identity evidence ref",
    )
    continuation.add_argument(
        "--duplicate-suppression-ref", required=True,
        help="Task-local provider duplicate-suppression evidence ref",
    )
    continuation.add_argument("--wake-id", help="Exact pending wake id when several exist")
    continuation.add_argument(
        "--approve", action="store_true", help="Approve external provider invocation"
    )
    continuation.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    manual = adapter_sub.add_parser("manual", help="Record identity-bound Manual Mode attestations")
    manual_sub = manual.add_subparsers(dest="manual_command", required=True)
    manual_attest = manual_sub.add_parser("attest", help="Append one canonical Manual receipt")
    manual_attest.add_argument("task_id", help="Task id")
    manual_attest.add_argument("--workspace", default=".", help="Workspace root")
    manual_attest.add_argument("--agent", required=True, help="Exact routed Agent")
    manual_attest.add_argument(
        "--role",
        required=True,
        choices=["coordinator", "implementer", "reviewer", "prototype", "researcher", "other"],
    )
    manual_attest.add_argument(
        "--event",
        required=True,
        choices=["manual_delivery_attested", "manual_result_attested", "manual_blocked"],
    )
    manual_attest.add_argument("--authority", required=True, help="Named attesting authority")
    manual_attest.add_argument("--authority-ref", required=True, help="Task-local authority declaration ref")
    manual_attest.add_argument("--statement", required=True, help="Exact attestation statement")
    manual_attest.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    for decision_name in ("revoke", "adjudicate"):
        decision = manual_sub.add_parser(
            decision_name, help=f"Append one Manual attestation {decision_name} decision"
        )
        decision.add_argument("task_id", help="Task id")
        decision.add_argument("--workspace", default=".", help="Workspace root")
        decision.add_argument("--receipt-id", required=True, help="Target Manual receipt ID")
        decision.add_argument("--authority", required=True, help="Named deciding authority")
        decision.add_argument("--authority-ref", required=True, help="Task-local authority declaration ref")
        decision.add_argument("--statement", required=True, help="Exact decision statement")
        if decision_name == "adjudicate":
            decision.add_argument(
                "--conflicting-receipt-id",
                action="append",
                required=True,
                help="Complete conflicting receipt set; repeat for every receipt",
            )
        decision.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    catalog = sub.add_parser("catalog", help="Build and query the optional local evidence catalog")
    catalog_sub = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_index = catalog_sub.add_parser("index", help="Index registered evidence for one task")
    catalog_index.add_argument("task_id", nargs="?", help="Task id under .herdr-loop/tasks")
    catalog_index.add_argument("--all", dest="index_all", action="store_true", help="Index all tasks with evidence-status.json")
    catalog_index.add_argument("--workspace", default=".", help="Workspace root")
    catalog_index.add_argument("--database", help="Workspace-relative catalog database path")
    catalog_index.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    catalog_search = catalog_sub.add_parser("search", help="Search valid catalog evidence by default")
    catalog_search.add_argument("query", nargs="?", default="", help="FTS5 keyword query")
    catalog_search.add_argument(
        "--status",
        action="append",
        choices=["valid", "stale", "invalid"],
        help="Catalog status filter; repeat to include multiple statuses (default: valid)",
    )
    catalog_search.add_argument("--type", dest="evidence_type", help="Exact evidence type filter")
    catalog_search.add_argument("--agent", help="Exact provenance agent filter")
    catalog_search.add_argument("--task", dest="catalog_task_id", help="Exact task id filter")
    catalog_search.add_argument("--digest", help="Exact sha256 content digest filter")
    catalog_search.add_argument("--limit", type=int, default=20, help="Maximum results (1-100)")
    catalog_search.add_argument("--workspace", default=".", help="Workspace root")
    catalog_search.add_argument("--database", help="Workspace-relative catalog database path")
    catalog_search.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    catalog_fixtures = catalog_sub.add_parser(
        "fixtures", help="Index an explicit anonymous synthetic fixture manifest"
    )
    catalog_fixtures.add_argument("manifest", help="Fixture manifest path inside the workspace")
    catalog_fixtures.add_argument("--workspace", default=".", help="Workspace root")
    catalog_fixtures.add_argument("--database", help="Workspace-relative catalog database path")
    catalog_fixtures.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    catalog_fixture = catalog_sub.add_parser(
        "fixture", help="Search only anonymous synthetic fixture evidence"
    )
    catalog_fixture.add_argument("query", nargs="?", default="", help="FTS5 keyword query")
    catalog_fixture.add_argument(
        "--status",
        action="append",
        choices=["valid", "stale", "invalid"],
        help="Catalog status filter (default: valid)",
    )
    catalog_fixture.add_argument("--type", dest="evidence_type", help="Exact evidence type filter")
    catalog_fixture.add_argument("--limit", type=int, default=20, help="Maximum results (1-100)")
    catalog_fixture.add_argument("--workspace", default=".", help="Workspace root")
    catalog_fixture.add_argument("--database", help="Workspace-relative catalog database path")
    catalog_fixture.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    catalog_context = catalog_sub.add_parser(
        "context", help="Assemble digest-verified cited context from catalog evidence"
    )
    catalog_context.add_argument("query", help="FTS5 keyword query")
    catalog_context.add_argument(
        "--status",
        action="append",
        choices=["valid", "stale", "invalid"],
        help="Catalog status filter (default: valid)",
    )
    catalog_context.add_argument("--type", dest="evidence_type", help="Exact evidence type filter")
    catalog_context.add_argument("--agent", help="Exact provenance agent filter")
    catalog_context.add_argument("--task", dest="catalog_task_id", help="Exact task id filter")
    catalog_context.add_argument("--anonymous", action="store_true", help="Use anonymous fixtures only")
    catalog_context.add_argument("--limit", type=int, default=5, help="Maximum evidence items (1-100)")
    catalog_context.add_argument("--max-chars", type=int, default=4000, help="Context budget (256-50000)")
    catalog_context.add_argument("--workspace", default=".", help="Workspace root")
    catalog_context.add_argument("--database", help="Workspace-relative catalog database path")
    catalog_context.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    catalog_show = catalog_sub.add_parser("show", help="Show one catalog entry")
    catalog_show.add_argument("catalog_id")
    catalog_show.add_argument("--workspace", default=".", help="Workspace root")
    catalog_show.add_argument("--database", help="Workspace-relative catalog database path")
    catalog_show.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    catalog_verify = catalog_sub.add_parser("verify", help="Verify one entry against its source digest")
    catalog_verify.add_argument("catalog_id")
    catalog_verify.add_argument("--workspace", default=".", help="Workspace root")
    catalog_verify.add_argument("--database", help="Workspace-relative catalog database path")
    catalog_verify.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    catalog_sweep = catalog_sub.add_parser("sweep", help="Mark drifted entries stale")
    catalog_sweep.add_argument("--workspace", default=".", help="Workspace root")
    catalog_sweep.add_argument("--database", help="Workspace-relative catalog database path")
    catalog_sweep.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    catalog_invalidate = catalog_sub.add_parser(
        "invalidate", help="Invalidate one entry and mark dependent entries stale"
    )
    catalog_invalidate.add_argument("catalog_id")
    catalog_invalidate.add_argument("--reason", required=True, help="Recorded invalidation reason")
    catalog_invalidate.add_argument("--workspace", default=".", help="Workspace root")
    catalog_invalidate.add_argument("--database", help="Workspace-relative catalog database path")
    catalog_invalidate.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    audit = sub.add_parser("audit", help="Audit a VALP task evidence folder")
    audit.add_argument("path", nargs="?", default=".", help="Task folder or workspace root")
    audit.add_argument("--task", dest="task_id", help="Task id under <workspace>/.herdr-loop/tasks/")
    audit.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    audit.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    audit.add_argument(
        "--emit-task-graph",
        action="store_true",
        help="Refresh the evidence-linked user-facing Task Graph after auditing",
    )

    graph = sub.add_parser("graph", help="Generate an evidence-linked user-facing Task Graph")
    graph.add_argument("path", nargs="?", default=".", help="Task folder or workspace root")
    graph.add_argument("--task", dest="task_id", help="Task id under <workspace>/.herdr-loop/tasks/")
    graph.add_argument("--workspace", help="Workspace root when --task is used")
    graph.add_argument(
        "--format",
        choices=["json", "html", "svg", "all"],
        default="all",
        help="Output format (default: all)",
    )
    graph.add_argument("--output-dir", help="Output directory; defaults to <task>/task-graph")
    graph.add_argument("--json", action="store_true", help="Print machine-readable generation metadata")

    cost = sub.add_parser("cost", help="Manage provider-neutral task cost evidence")
    cost_sub = cost.add_subparsers(dest="cost_command", required=True)
    for name, help_text in (("report", "Render a deterministic cost report"), ("estimate", "Check projected cost before dispatch"), ("record-usage", "Append one usage event"), ("record-billing", "Append one billing event")):
        command = cost_sub.add_parser(name, help=help_text)
        command.add_argument("path", nargs="?", default=".", help="Task folder or workspace root")
        command.add_argument("--task", dest="task_id", help="Task id under <workspace>/.herdr-loop/tasks/")
        command.add_argument("--event", help="JSON event for append-only record commands")
        command.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    doctor = sub.add_parser("doctor", help="Diagnose VALP workspace health without mutating by default")
    doctor.add_argument("--workspace", default=".", help="Workspace root")
    doctor.add_argument("--task", dest="task_id", help="Optional task id to audit under <workspace>/.herdr-loop/tasks/")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    doctor.add_argument("--report", help="Write a Markdown report to a path, or use 'desktop'")
    doctor.add_argument(
        "--snapshot",
        help="Explicit opt-in Doctor snapshot path for TTL-bound proof reuse",
    )
    doctor.add_argument(
        "--snapshot-ttl",
        type=int,
        default=300,
        help="Snapshot TTL in seconds, between 1 and 3600 (default: 300)",
    )

    remediate = sub.add_parser(
        "remediate",
        help="Plan, apply, or verify bounded evidence-driven Doctor recovery",
    )
    remediate_sub = remediate.add_subparsers(dest="remediate_command", required=True)
    remediate_plan = remediate_sub.add_parser(
        "plan",
        help="Build a digest-bound repair plan from a fresh Doctor observation",
    )
    remediate_plan.add_argument("--workspace", default=".", help="Workspace root")
    remediate_plan.add_argument("--task", dest="task_id", help="Optional task id to include in Doctor")
    remediate_plan.add_argument("--output", help="Optional JSON repair-plan output path")
    remediate_plan.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    recovery_plan = remediate_sub.add_parser(
        "recovery-plan",
        help="Build an approval-gated plan for one injected MCP process restart",
    )
    recovery_plan.add_argument("--workspace", default=".", help="Workspace root")
    recovery_plan.add_argument("--resource-id", required=True, help="Versioned MCP process identity")
    recovery_plan.add_argument("--resource-version", required=True, help="Observed process generation or version")
    recovery_plan.add_argument("--rollback-token", required=True, help="Provider-issued rollback token; only its digest is stored in the plan")
    recovery_plan.add_argument("--output", help="Optional JSON recovery-plan output path")
    recovery_plan.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    remediate_apply = remediate_sub.add_parser(
        "apply",
        help="Apply one ready low-risk repair plan through the closed reference executor",
    )
    remediate_apply.add_argument("plan", help="Repair-plan JSON path")
    remediate_apply.add_argument("--workspace", default=".", help="Workspace root")
    remediate_apply.add_argument("--receipt", required=True, help="Repair-receipt JSON output path")
    remediate_apply.add_argument("--certificate", help="Proof-certificate JSON output path")
    remediate_apply.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    remediate_verify = remediate_sub.add_parser(
        "verify",
        help="Recheck a repair receipt and report any regressed claims",
    )
    remediate_verify.add_argument("receipt", help="Repair-receipt JSON path")
    remediate_verify.add_argument("--workspace", default=".", help="Workspace root")
    remediate_verify.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser


def prompt_from_args(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.prompt:
        return args.prompt
    raise SystemExit("publish requires --prompt or --prompt-file")


def watcher_event_from_args(args: argparse.Namespace) -> dict[str, Any]:
    raw = Path(args.event_file).read_text(encoding="utf-8") if args.event_file else args.event
    try:
        event = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise SystemExit(f"watcher-intake event must be valid JSON: {error}") from error
    if not isinstance(event, dict):
        raise SystemExit("watcher-intake event must be a JSON object")
    return event


def main(argv: list[str] | None = None) -> int:
    invoked_entrypoint = Path(sys.argv[0]) if argv is None else None
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "publish":
        directory = publish_task(
            Path(args.workspace),
            args.task_id,
            prompt_from_args(args),
            profile=args.profile,
            runtime=args.runtime,
            invoked_entrypoint=invoked_entrypoint,
        )
        result = {"task_id": args.task_id, "task_dir": str(directory), "routed": False}
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"Published VALP task: {args.task_id}")
            print(f"Task dir: {directory}")
            print("Routed: no (awaiting Leader-declared assignments)")
            visible = directory / "visible-routing.md"
            if visible.exists():
                print()
                print(visible.read_text(encoding="utf-8").strip())
        return 0

    if args.command == "watcher-intake":
        workspace = Path(args.workspace)

        def publish(event: dict[str, Any]) -> dict[str, Any]:
            directory = publish_task(
                workspace,
                args.task_id,
                args.prompt,
                profile=args.profile,
                runtime=args.runtime,
                invoked_entrypoint=invoked_entrypoint,
            )
            return {"task_id": args.task_id, "task_directory": str(directory)}

        result = HerdrAutoVisibleWatcher(workspace, publish).process(
            watcher_event_from_args(args)
        )
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"Published VALP watcher task: {result['task_id']}")
            print(f"Trigger policy: {result['trigger_policy_ref']}")
        return 0

    if args.command == "scan":
        capabilities = scan_workspace(
            Path(args.workspace),
            args.task_id,
            runtime=args.runtime,
            invoked_entrypoint=invoked_entrypoint,
        )
        if args.json:
            print(json.dumps(capabilities, indent=2, ensure_ascii=False))
        else:
            print(f"Scanned VALP capabilities into {Path(args.workspace).resolve() / '.herdr-loop' / 'agents' / 'capabilities.json'}")
        return 0

    if args.command == "route":
        declaration = read_json(Path(args.assignments))
        if not declaration:
            raise SystemExit(f"Assignment declaration is missing or invalid JSON: {args.assignments}")
        routing = route_task(
            Path(args.workspace),
            args.task_id,
            runtime=args.runtime,
            assignment_declaration=declaration,
        )
        if args.json:
            print(json.dumps(routing, indent=2, ensure_ascii=False))
        else:
            print(f"Validated Leader assignments for VALP task: {args.task_id}")
            print("Declared agents: " + ", ".join(routing.get("selected_agents") or []))
            visible_ref = ((routing.get("visible_attention") or {}).get("visible_routing")) or "visible-routing.md"
            visible = Path(args.workspace).resolve() / ".herdr-loop" / "tasks" / args.task_id / visible_ref
            if visible.exists():
                print()
                print(visible.read_text(encoding="utf-8").strip())
        return 0

    if args.command == "dispatch":
        commands = dispatch_task(
            Path(args.workspace),
            args.task_id,
            agent=args.agent,
            submit=args.submit,
            runtime=args.runtime,
            role=args.role,
            wait_seconds=args.wait_seconds,
            proof_seconds=args.proof_seconds,
            recover_incomplete=args.recover_incomplete,
            retry_generation=args.retry_generation,
            replace_owned_session_launch=args.replace_owned_session_launch,
            reprovision_done_session=args.reprovision_done_session,
        )
        if args.submit:
            print(f"Submitted dispatch for task {args.task_id}")
        else:
            manual = any(command.startswith("Manual Mode:") for command in commands)
            if manual:
                print("Manual Mode dispatch instructions. Copy dispatches manually and record manual receipts:")
            else:
                print("Dispatch dry run for the selected reference adapter. Use --submit only when the runtime is ready:")
            for command in commands:
                print(command)
        return 0

    if args.command == "preflight":
        report = collect_runtime_preflight(args.agent, runtime=args.runtime)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"VALP runtime preflight: {str(report.get('status', 'unknown')).upper()}")
            for agent, record in (report.get("agents") or {}).items():
                size = record.get("terminal_size") or {}
                session = record.get("pane_id") or record.get("queue_id") or record.get("worker_id") or record.get("session_status")
                print(
                    f"- {agent}: {record.get('status', 'unknown')} "
                    f"session={session} "
                    f"size={size.get('width', '?')}x{size.get('height', '?')}"
                )
        return 1 if report.get("status") == "fail" else 0

    if args.command == "wait":
        result = wait_for_task(
            Path(args.workspace),
            args.task_id,
            timeout_seconds=args.timeout,
            poll_interval_seconds=args.poll_interval,
            execution_timeout_seconds=args.execution_timeout,
        )
        if args.herdr_continuation_socket or args.herdr_continuation_provider:
            if not args.herdr_continuation_socket or not args.herdr_continuation_provider:
                raise SystemExit("HERDR continuation requires both --herdr-continuation-socket and --herdr-continuation-provider")
            if (result.get("accepted_wake") or {}).get("wake_reason") == "dependency_ready":
                result["continuation"] = continue_accepted_dependency_ready_wake(
                    Path(args.workspace),
                    args.task_id,
                    socket_path=args.herdr_continuation_socket,
                    provider_id=args.herdr_continuation_provider,
                    timeout_seconds=args.herdr_continuation_timeout,
                    approval_granted=args.herdr_continuation_approval_granted,
                )
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif result.get("status") == "waiting":
            print("VALP wait paused: workers remain active; wait again or resume when a qualifying receipt arrives.")
        else:
            print(f"VALP wait resumed: {result.get('resume_event', 'unknown')}")
        return 0

    if args.command == "resume":
        result = resume_suspended_task(
            Path(args.workspace),
            args.task_id,
            args.event,
            resume_ref=args.resume_ref,
        )
        if args.herdr_continuation_socket or args.herdr_continuation_provider:
            if not args.herdr_continuation_socket or not args.herdr_continuation_provider:
                raise SystemExit("HERDR continuation requires both --herdr-continuation-socket and --herdr-continuation-provider")
            if (result.get("accepted_wake") or {}).get("wake_reason") == "dependency_ready":
                result["continuation"] = continue_accepted_dependency_ready_wake(
                    Path(args.workspace),
                    args.task_id,
                    socket_path=args.herdr_continuation_socket,
                    provider_id=args.herdr_continuation_provider,
                    timeout_seconds=args.herdr_continuation_timeout,
                    approval_granted=args.herdr_continuation_approval_granted,
                )
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"VALP suspension resumed: {result.get('resume_event', 'unknown')}")
        return 0

    if args.command == "cost":
        task_dir = resolve_task_dir(Path(args.path), args.task_id)
        task_id = str((json.loads((task_dir / "state.json").read_text(encoding="utf-8"))).get("task_id") or task_dir.name)
        try:
            if args.cost_command in {"record-usage", "record-billing"}:
                if not args.event:
                    raise CostGovernanceError("--event is required")
                event = json.loads(args.event)
                if event.get("task_id") != task_id:
                    raise CostGovernanceError("event task_id does not match the task")
                filename, schema = (("usage-events.jsonl", "valp-usage-event.v1") if args.cost_command == "record-usage" else ("billing-events.jsonl", "valp-billing-event.v1"))
                append_event(task_dir / filename, event, schema)
                print(json.dumps({"status": "appended", "ref": filename, "event_id": event["event_id"]}, indent=2) if args.json else f"Appended {args.cost_command} event: {event['event_id']}")
                return 0
            if args.cost_command == "estimate":
                result = enforce_cost_budget(task_dir, task_id)
                if result is None:
                    raise CostGovernanceError("cost-budget.json is required")
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 0
            pricing = json.loads((task_dir / "pricing-snapshots.json").read_text(encoding="utf-8"))
            budget = json.loads((task_dir / "cost-budget.json").read_text(encoding="utf-8"))
            usage = [json.loads(line) for line in (task_dir / "usage-events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            billing_path = task_dir / "billing-events.jsonl"
            billing = [json.loads(line) for line in billing_path.read_text(encoding="utf-8").splitlines() if line.strip()] if billing_path.exists() else []
            result = build_cost_report(task_id, pricing.get("snapshots") or [], usage, billing, budget.get("planned_usage") or [])
        except (OSError, json.JSONDecodeError, CostGovernanceError) as error:
            raise SystemExit(f"Cost governance error: {error}") from error
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.command == "install":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            result = InstallationCore(root).init()
        except ControlPlaneError as error:
            raise SystemExit(f"{error.code}: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"VALP v0.3 installation initialized at {root}")
            print(f"Status: {result['state']['status']}")
        return 0

    if args.command == "leader":
        root = leader_installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        core = InstallationCore(root)
        created_leader_workspaces: list[tuple[str, str]] = []
        activated_leader_workspace: str | None = None

        def tracked_leader_run_command(command: list[str], **kwargs: Any) -> dict[str, Any]:
            command_result = run_command(command, **kwargs)
            if command[1:3] == ["workspace", "create"] and command_result.get("ok") is True:
                try:
                    payload = json.loads(str(command_result.get("stdout") or ""))
                except json.JSONDecodeError:
                    payload = {}
                record = payload.get("result") if isinstance(payload.get("result"), dict) else payload
                workspace = record.get("workspace") if isinstance(record.get("workspace"), dict) else record
                workspace_id = str(workspace.get("workspace_id") or "").strip() if isinstance(workspace, dict) else ""
                if workspace_id:
                    created_leader_workspaces.append((command[0], workspace_id))
            return command_result

        def cleanup_unbound_leader_workspaces(preserve: str | None = None) -> list[str]:
            cleanup_errors: list[str] = []
            for herdr_command, workspace_id in reversed(created_leader_workspaces):
                if workspace_id == preserve:
                    continue
                cleanup_result = run_command(
                    [herdr_command, "workspace", "close", workspace_id],
                    timeout=10.0,
                )
                if cleanup_result.get("ok") is not True:
                    detail = str(
                        cleanup_result.get("stderr")
                        or cleanup_result.get("stdout")
                        or "unknown cleanup failure"
                    ).strip()
                    cleanup_errors.append(f"{workspace_id}: {detail}")
            return cleanup_errors

        try:
            if args.leader_command == "candidates":
                doctor_report = collect_doctor_report(Path(args.workspace))
                result = core.discover_candidates(doctor_report.capability_passports)
            elif args.leader_command == "select":
                result = core.select_leader(args.principal)
            elif args.leader_command in {"start", "open"}:
                state = core.state()
                if (
                    args.leader_command == "open"
                    and state.get("status") == "blocked"
                    and state.get("active_blockers") == ["leader_activation_failed"]
                    and int(state.get("active_leader_epoch") or 0) >= 1
                ):
                    core.restore_active_leader_after_failed_restart()
                    state = core.state()
                selected = state.get("selected_leader") if isinstance(state.get("selected_leader"), dict) else {}
                runtime = selected.get("runtime") if isinstance(selected.get("runtime"), dict) else {}
                if runtime.get("adapter_id") != "herdr":
                    raise ControlPlaneError(
                        "VALP-E-ADAPTER-UNSUPPORTED",
                        "The reference CLI currently starts installation Leaders only through a selected HERDR adapter",
                    )
                herdr = shutil.which("herdr")
                if not herdr:
                    raise ControlPlaneError(
                        "VALP-E-ADAPTER-UNSUPPORTED",
                        "The selected HERDR adapter is not installed or not reachable on PATH",
                    )
                if state.get("status") == "active":
                    binding_path = root / "leader-session-binding.json"
                    binding = read_json(binding_path) if binding_path.exists() else None
                    if not isinstance(binding, dict):
                        raise ControlPlaneError(
                            "VALP-E-REGISTRY-CONSISTENCY",
                            "Active Leader has no runtime attachment record; run `valp leader restart` to repair it",
                            state_effect="blocked",
                        )
                    opened = open_herdr_leader_session(herdr, binding, tracked_leader_run_command)
                    if opened.get("status") == "opened":
                        result = {
                            "status": "active",
                            "action": "opened_existing_leader_attachment",
                            "leader": core.status(),
                            "attachment": opened,
                        }
                    else:
                        prepared = core.prepare_leader_restart()
                        provisioned = provision_herdr_leader_session(
                            herdr,
                            installation_id=state["installation_id"],
                            principal_id=selected["principal_id"],
                            agent=selected["agent_id"],
                            workspace_root=Path(args.workspace),
                            launch_argv=list(runtime["launch_argv"]),
                            leader_epoch=prepared["proposed_leader_epoch"],
                            generation=prepared["generation"],
                            run_command=tracked_leader_run_command,
                        )
                        result = core.activate_leader(provisioned)
                        result["action"] = "reopened_missing_leader_attachment"
                        binding = result["binding"]
                        bound_workspace = str(
                            (binding.get("runtime_identity") or {}).get("workspace_id") or ""
                        ).strip()
                        activated_leader_workspace = bound_workspace
                        cleanup_errors = cleanup_unbound_leader_workspaces(bound_workspace)
                        if cleanup_errors:
                            raise HerdrSubmissionError(
                                "HERDR Leader opened but temporary workspace cleanup failed: "
                                + "; ".join(cleanup_errors)
                            )
                        opened = open_herdr_leader_session(
                            herdr,
                            binding,
                            tracked_leader_run_command,
                        )
                        if opened.get("status") != "opened":
                            raise HerdrSubmissionError(
                                "HERDR activated Leader could not be focused"
                            )
                        result["attachment"] = opened
                else:
                    prepared = core.prepare_leader_start()
                    provisioned = provision_herdr_leader_session(
                        herdr,
                        installation_id=state["installation_id"],
                        principal_id=selected["principal_id"],
                        agent=selected["agent_id"],
                        workspace_root=Path(args.workspace),
                        launch_argv=list(runtime["launch_argv"]),
                        leader_epoch=prepared["proposed_leader_epoch"],
                        generation=1,
                        run_command=tracked_leader_run_command,
                    )
                    result = core.activate_leader(provisioned)
            elif args.leader_command == "recover-start":
                state = core.state()
                selected = state.get("selected_leader") if isinstance(state.get("selected_leader"), dict) else {}
                runtime = selected.get("runtime") if isinstance(selected.get("runtime"), dict) else {}
                if runtime.get("adapter_id") != "herdr":
                    raise ControlPlaneError(
                        "VALP-E-ADAPTER-UNSUPPORTED",
                        "The reference CLI currently recovers installation Leaders only through a selected HERDR adapter",
                    )
                prepared = core.prepare_leader_start_recovery(
                    args.session,
                    approve=args.approve,
                )
                herdr = shutil.which("herdr")
                if not herdr:
                    raise ControlPlaneError(
                        "VALP-E-ADAPTER-UNSUPPORTED",
                        "The selected HERDR adapter is not installed or not reachable on PATH",
                    )
                provisioned = recover_herdr_leader_session(
                    herdr,
                    installation_id=state["installation_id"],
                    principal_id=selected["principal_id"],
                    agent=selected["agent_id"],
                    workspace_root=Path(args.workspace),
                    launch_argv=list(runtime["launch_argv"]),
                    leader_epoch=prepared["proposed_leader_epoch"],
                    generation=prepared["generation"],
                    session_id=args.session,
                    recovery_approval=prepared["recovery_approval"],
                    run_command=run_command,
                )
                result = core.activate_leader(provisioned)
            elif args.leader_command == "restart":
                state = core.state()
                selected = state.get("selected_leader") if isinstance(state.get("selected_leader"), dict) else {}
                runtime = selected.get("runtime") if isinstance(selected.get("runtime"), dict) else {}
                if runtime.get("adapter_id") != "herdr":
                    raise ControlPlaneError(
                        "VALP-E-ADAPTER-UNSUPPORTED",
                        "The reference CLI currently restarts installation Leaders only through a selected HERDR adapter",
                    )
                herdr = shutil.which("herdr")
                if not herdr:
                    raise ControlPlaneError(
                        "VALP-E-ADAPTER-UNSUPPORTED",
                        "The selected HERDR adapter is not installed or not reachable on PATH",
                    )
                prepared = core.prepare_leader_restart()
                provisioned = provision_herdr_leader_session(
                    herdr,
                    installation_id=state["installation_id"],
                    principal_id=selected["principal_id"],
                    agent=selected["agent_id"],
                    workspace_root=Path(args.workspace),
                    launch_argv=list(runtime["launch_argv"]),
                    leader_epoch=prepared["proposed_leader_epoch"],
                    generation=prepared["generation"],
                    run_command=run_command,
                )
                result = core.activate_leader(provisioned)
            elif args.leader_command == "rotate":
                candidates_record = read_json(root / "leader-candidates.json")
                candidate = next(
                    (
                        item
                        for item in candidates_record.get("candidates") or []
                        if item.get("principal_id") == args.principal
                    ),
                    None,
                )
                if candidate is None:
                    raise ControlPlaneError(
                        "VALP-E-PERMISSION-DENIED",
                        "Replacement Leader must have current discovery evidence",
                    )
                runtime = candidate.get("runtime") if isinstance(candidate.get("runtime"), dict) else {}
                if runtime.get("adapter_id") != "herdr":
                    raise ControlPlaneError(
                        "VALP-E-ADAPTER-UNSUPPORTED",
                        "The reference CLI currently rotates installation Leaders only through a selected HERDR adapter",
                    )
                herdr = shutil.which("herdr")
                if not herdr:
                    raise ControlPlaneError(
                        "VALP-E-ADAPTER-UNSUPPORTED",
                        "The selected HERDR adapter is not installed or not reachable on PATH",
                    )
                prepared = core.rotate_leader(args.principal)
                selected = prepared["selected_leader"]
                provisioned = provision_herdr_leader_session(
                    herdr,
                    installation_id=core.state()["installation_id"],
                    principal_id=selected["principal_id"],
                    agent=selected["agent_id"],
                    workspace_root=Path(args.workspace),
                    launch_argv=list(runtime["launch_argv"]),
                    leader_epoch=prepared["proposed_leader_epoch"],
                    generation=prepared["generation"],
                    run_command=run_command,
                )
                result = core.activate_leader(provisioned)
            else:
                result = core.status()
        except HerdrSubmissionError as error:
            cleanup_errors = cleanup_unbound_leader_workspaces(
                activated_leader_workspace
            )
            if cleanup_errors:
                error = HerdrSubmissionError(
                    f"{error}; temporary workspace cleanup failed: "
                    + "; ".join(cleanup_errors)
                )
            if args.leader_command in {"start", "open"} and core.state().get("status") == "active":
                raise SystemExit(f"VALP-E-LEADER-UNREACHABLE: {error}") from error
            failure_operation = (
                "restart"
                if args.leader_command == "open"
                and core.state().get("status") == "restarting_leader"
                else args.leader_command
            )
            try:
                core.fail_leader_activation(
                    failure_operation,
                    adapter_id="herdr",
                    failure_class=type(error).__name__,
                )
            except ControlPlaneError as blocked:
                if (
                    args.leader_command == "open"
                    and failure_operation == "restart"
                    and blocked.code == "VALP-E-LEADER-UNREACHABLE"
                ):
                    core.restore_active_leader_after_failed_restart()
                detail = error if blocked.code == "VALP-E-LEADER-UNREACHABLE" else blocked
                raise SystemExit(f"{blocked.code}: {detail}") from error
            raise AssertionError("Leader activation failure must fail closed") from error
        except ControlPlaneError as error:
            raise SystemExit(f"{error.code}: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            if args.leader_command == "candidates":
                print("Leader candidates discovered; explicit selection is still required.")
                for candidate in result.get("candidates", []):
                    print(f"- {candidate['principal_id']} ({candidate['principal_kind']})")
            elif args.leader_command == "show":
                state = result["state"]
                binding = result.get("leader_session") or {}
                print(f"Installation leader: {(state.get('active_leader') or {}).get('principal_id', 'none')}")
                print(f"Epoch: {state.get('active_leader_epoch', 0)}")
                print(f"Status: {state.get('status')}")
                print(f"Session: {(binding.get('runtime_identity') or {}).get('session_id', 'none')}")
                print(f"Generation: {binding.get('generation', 'none')}")
                print(f"Health: {(binding.get('health') or {}).get('status', 'unknown')}")
            elif args.leader_command in {"start", "open", "recover-start", "restart", "rotate"}:
                if result.get("attachment"):
                    attachment = result["attachment"]
                    print("Installation Leader opened from the current caller.")
                    print(f"Session: {attachment.get('session_id', 'none')}")
                    print(f"Action: {result.get('action', 'opened')}")
                else:
                    binding = result["binding"]
                    print(f"Installation Leader started: {binding['principal_id']}")
                    print(f"Session: {binding['runtime_identity']['session_id']}")
                    print(f"Epoch: {binding['leader_epoch']}")
            else:
                print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.command == "capabilities":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            result = InstallationCore(root).reconcile_capabilities(load_observations(Path(args.observations)))
        except (ControlPlaneError, OSError, ValueError) as error:
            raise SystemExit(str(error)) from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"Capability registry reconciled to revision {result['registry']['registry_revision']}")
        return 0

    if args.command == "status":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            result = InstallationCore(root).status()
        except ControlPlaneError as error:
            raise SystemExit(f"{error.code}: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"VALP installation: {result['installation']['installation_id']}")
            print(f"Status: {result['state']['status']}")
            print(f"Leader: {(result['state'].get('active_leader') or {}).get('principal_id', 'none')} (epoch {result['state'].get('active_leader_epoch', 0)})")
        return 0

    if args.command == "state":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            if args.state_command == "show":
                result = task_state(root, args.task_id) if args.task_id else InstallationCore(root).state()
            else:
                result = replay_task(root, args.task_id) if args.task_id else InstallationCore(root).replay()
        except ControlPlaneError as error:
            raise SystemExit(f"{error.code}: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            scope = f"task {args.task_id}" if args.task_id else "installation"
            print(f"VALP {scope}: {result['status']} (revision {result['revision']})")
        return 0

    if args.command == "hello":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            result = InstallationCore(root).hello(args.nonce)
        except ControlPlaneError as error:
            raise SystemExit(f"{error.code}: {error}") from error
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    if args.command == "protocol" and args.protocol_command == "migrate":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        core = InstallationCore(root)
        try:
            if args.dry_run and args.apply:
                raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "--dry-run and --apply are mutually exclusive")
            if args.plan and not args.apply:
                raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "--plan requires --apply")
            if args.plan:
                core.stage_migration_plan(Path(args.plan))
            result = core.migrate_apply(Path(args.workspace), approve=args.approve) if args.apply else core.migrate_plan(Path(args.workspace), target_version=args.to)
        except ControlPlaneError as error:
            raise SystemExit(f"{error.code}: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"Migration {result.get('status', 'planned')}: {result.get('migration_id')}")
            print(f"Plan digest: {result.get('plan_digest')}")
        return 0

    if args.command == "plugin" and args.plugin_command == "validate":
        try:
            manifest = load_plugin_manifest(Path(args.path))
        except ControlPlaneError as error:
            raise SystemExit(f"{error.code}: {error}") from error
        result = {"status": "PASS", "plugin_id": manifest["plugin_id"], "manifest_digest": manifest["manifest_digest"]}
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else f"Plugin manifest PASS: {manifest['plugin_id']}")
        return 0

    if args.command == "conformance":
        result = run_conformance(args.profile)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json or result["fail_count"] else f"VALP conformance smoke {result['status']}: pass={result['pass_count']} fail={result['fail_count']}")
        return 1 if result["fail_count"] else 0

    if args.command == "evidence" and args.evidence_command == "add":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            result = InstallationCore(root).add_evidence(
                args.path,
                evidence_kind=args.evidence_kind,
                producer_principal_id=args.producer_principal_id,
            )
        except ControlPlaneError as error:
            raise SystemExit(f"{error.code}: {error}") from error
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else f"Evidence recorded: {result['evidence_id']} {result['content_digest']}")
        return 0

    if args.command == "claim" and args.claim_command == "declare":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            value = json.loads(args.value)
            result = InstallationCore(root).declare_claim(
                subject_ref=args.subject_ref,
                claim_kind=args.claim_kind,
                predicate=args.predicate,
                asserted_value=value,
                scope=args.scope,
                claimant_principal_id=args.claimant_principal_id,
                evidence_refs=args.evidence_refs,
            )
        except (ControlPlaneError, json.JSONDecodeError) as error:
            raise SystemExit(str(error)) from error
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else f"Claim recorded: {result['claim_id']} ({result['status']})")
        return 0

    if args.command == "review" and args.review_command == "record":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            result = InstallationCore(root).record_review(
                claim_id=args.claim_id,
                reviewer_principal_id=args.reviewer_principal_id,
                verdict=args.verdict,
            )
        except ControlPlaneError as error:
            raise SystemExit(f"{error.code}: {error}") from error
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json else f"Review recorded: {result['review']['review_id']} ({result['review']['verdict']})")
        return 0

    if args.command == "task":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            if args.task_command == "init":
                result = init_task(root, args.task_id)
            elif args.task_command == "status":
                result = task_state(root, args.task_id)
            else:
                gates = json.loads(args.gates) if args.gates else {}
                if not isinstance(gates, dict):
                    raise ControlPlaneError("VALP-E-MESSAGE-SCHEMA", "--gates must be a JSON object")
                result = transition_task(
                    root,
                    args.task_id,
                    args.to,
                    expected_revision=args.expected_revision,
                    gates=gates,
                    actor=args.actor,
                )
        except (ControlPlaneError, json.JSONDecodeError) as error:
            raise SystemExit(str(error)) from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"Task {args.task_id}: {result['status']} (revision {result['revision']})")
        return 0

    if args.command == "kernel" and args.kernel_command == "control":
        from .kernel_store import KernelStore, KernelStoreError, decode_event
        from .protocol_kernel import Evidence, IdentityKind, ReplayEntry, ResultVariant, reduce
        from .protocol_receipts import digest

        if not args.approve:
            raise SystemExit("Kernel control Event requires explicit --approve")
        workspace = Path(args.workspace).resolve()
        directory = workspace / ".herdr-loop" / "tasks" / args.task_id
        store = KernelStore(directory / "runtime" / "kernel")
        try:
            event_path = (directory / args.event_ref).resolve()
            event_path.relative_to(directory)
            authority_path = (directory / args.authority_ref).resolve()
            authority_path.relative_to(directory)
            event = decode_event(json.loads(event_path.read_text(encoding="utf-8")))
            authority_payload = authority_path.read_bytes()
            if not authority_payload:
                raise ValueError("Kernel control authority Evidence is empty")
            if event.task_id.value != args.task_id:
                raise ValueError("Kernel control Event Task identity differs")
            if (
                event.authority_evidence_id is None
                or event.authority_evidence_id.kind != IdentityKind.EVIDENCE
            ):
                raise ValueError("Kernel control Event lacks authority Evidence identity")
            evidence_set = (
                Evidence(event.authority_evidence_id, digest(authority_payload)),
            )
            recovery = store.recover()
            result = reduce(recovery.replay.state, event, evidence_set)
            if result.variant == ResultVariant.REJECTED:
                raise ValueError(
                    f"Kernel control Event rejected: {result.rejected.error_code}"
                )
            if result.variant == ResultVariant.ACCEPTED:
                recovery = store.append(ReplayEntry(event, evidence_set, result))
                state = recovery.replay.state
            else:
                state = result.no_op.state
            reconciliation = store.reconcile_effects()
            output = {
                "variant": result.variant.value,
                "event_id": event.event_id.value,
                "state": state.canonical(),
                "effects": reconciliation.canonical(),
            }
        except (KernelStoreError, OSError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f"Kernel control Event failed: {error}") from error
        if args.json:
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            print(
                f"Kernel control {output['variant']}: {output['event_id']} "
                f"-> {output['state']['status']}"
            )
            if output["effects"]["pending"]:
                print(f"Pending Adapter effects: {len(output['effects']['pending'])}")
        return 1 if output["effects"]["pending"] or output["effects"]["blocked"] else 0

    if args.command == "kernel" and args.kernel_command == "effects":
        from .kernel_store import KernelEffectStatus, KernelStore, KernelStoreError
        from .protocol_receipts import digest

        workspace = Path(args.workspace).resolve()
        directory = workspace / ".herdr-loop" / "tasks" / args.task_id
        store = KernelStore(directory / "runtime" / "kernel")
        try:
            if args.kernel_effects_command == "status":
                result = store.reconcile_effects().canonical()
            elif args.kernel_effects_command == "record":
                proof_path = (directory / args.proof_ref).resolve()
                proof_path.relative_to(directory)
                proof_payload = proof_path.read_bytes()
                if not proof_payload:
                    raise ValueError("Kernel effect proof is empty")
                record = store.record_effect(
                    args.obligation,
                    status=KernelEffectStatus(args.status),
                    proof_ref=args.proof_ref,
                    proof_digest=digest(proof_payload),
                )
                result = {
                    "record": record.canonical(),
                    "reconciliation": store.reconcile_effects().canonical(),
                }
            else:
                from .effect_runtime import EffectRuntimeError, execute_kernel_effect

                try:
                    result = execute_kernel_effect(
                        workspace,
                        args.task_id,
                        args.obligation,
                        approve=args.approve,
                    )
                except EffectRuntimeError as error:
                    raise ValueError(str(error)) from error
        except (KernelStoreError, OSError, ValueError) as error:
            raise SystemExit(f"Kernel effect reconciliation failed: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.kernel_effects_command == "status":
            print(
                f"Kernel effects: pending={len(result['pending'])} "
                f"fulfilled={len(result['fulfilled'])} blocked={len(result['blocked'])}"
            )
        elif args.kernel_effects_command == "record":
            print(
                f"Recorded Kernel effect {result['record']['effect_id']}: "
                f"{result['record']['status']}"
            )
        elif result["status"] == "dry_run":
            print(
                f"Kernel effect dry run: {result['adapter_id']} run {result['run_id']}"
            )
            print("Re-run with --approve to execute external cancellation.")
        else:
            print(
                f"Executed Kernel effect {result['record']['effect_id']}: "
                f"{result['record']['status']}"
            )
        reconciliation = result if args.kernel_effects_command == "status" else result["reconciliation"]
        if args.kernel_effects_command == "execute" and result["status"] == "dry_run":
            return 0
        return 1 if reconciliation["pending"] or reconciliation["blocked"] else 0

    if args.command == "adapter" and args.adapter_command == "process" and args.process_command == "run":
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            result = run_process(
                root,
                args.task_id,
                split_worker_command(args.worker_command),
                timeout_seconds=args.timeout,
                approve=args.approve,
            )
        except ControlPlaneError as error:
            raise SystemExit(f"{error.code}: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif result["status"] == "dry_run":
            print(f"Process adapter dry run: {result['run_ref']}")
            print("Re-run with --approve to execute the addressable worker.")
        else:
            print(f"Process adapter {result['status']}: {result['run_ref']}")
        return 0 if result["status"] in {"dry_run", "completed"} else 1

    if args.command == "adapter" and args.adapter_command == "init":
        try:
            result = initialize_adapter(Path(args.target), args.name)
        except AdapterStarterError as error:
            raise SystemExit(f"VALP-E-ADAPTER-INIT: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"VALP adapter starter created: {result['target']}")
            print(f"Verify: {result['verification_command']}")
        return 0

    if args.command == "adapter" and args.adapter_command == "langgraph":
        try:
            if args.langgraph_command == "run":
                input_data = json.loads(args.input_json)
                if not isinstance(input_data, dict):
                    raise LangGraphAdapterError("--input-json must be a JSON object")
                result = submit_langgraph_run(
                    Path(args.workspace),
                    args.task_id,
                    args.agent,
                    args.role,
                    graph_id=args.graph_id,
                    input_data=input_data,
                    expected_refs=args.expected_ref,
                    thread_id=args.thread_id,
                    wait_seconds=args.wait_seconds,
                    api_url=args.api_url,
                )
            else:
                result = resume_langgraph_run(
                    Path(args.workspace),
                    args.task_id,
                    args.run_id,
                    wait_seconds=args.wait_seconds,
                )
        except (LangGraphAdapterError, json.JSONDecodeError) as error:
            raise SystemExit(f"VALP-E-LANGGRAPH-ADAPTER: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"LangGraph adapter {result['status']}: {result['run_ref']}")
            if result["status"] == "waiting":
                print("Runtime job remains active; resume this run ID after the pause window.")
        return 0 if result["status"] in {"completed", "waiting"} else 1

    if args.command == "adapter" and args.adapter_command == "herdr":
        from .runtime_adapters import (
            RuntimeAdapterError,
            load_runtime_v3_receipts,
            record_herdr_completion,
        )

        workspace = Path(args.workspace).resolve()
        directory = workspace / ".herdr-loop" / "tasks" / args.task_id
        try:
            submissions = [
                receipt
                for receipt in load_runtime_v3_receipts(directory, "herdr")
                if receipt.get("event") == "dispatch_submitted"
                and receipt.get("agent") == args.agent
                and receipt.get("role") == args.role
                and (args.attempt_id is None or receipt.get("attempt_id") == args.attempt_id)
            ]
            if len(submissions) != 1:
                raise RuntimeAdapterError(
                    "HERDR observation requires exactly one matching submitted Attempt"
                )
            observation_path = (directory / args.observation_ref).resolve()
            observation_path.relative_to(directory)
            terminal_proof = json.loads(observation_path.read_text(encoding="utf-8"))
            receipt, observation = record_herdr_completion(
                directory,
                args.task_id,
                submissions[0],
                list(submissions[0].get("expected_refs") or []),
                terminal_proof,
            )
        except (RuntimeAdapterError, OSError, ValueError, json.JSONDecodeError) as error:
            raise SystemExit(f"HERDR v3 terminal observation failed: {error}") from error
        result = {"receipt": receipt, "observation": observation}
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(
                f"Recorded HERDR v3 terminal observation: {receipt['event']} "
                f"for {args.agent}/{args.role} ({receipt['receipt_id']})"
            )
        return 0 if receipt["event"] == "dispatch_completed" else 1

    if args.command == "adapter" and args.adapter_command == "continuation":
        from .continuation import (
            ContinuationError,
            ContinuationStore,
            SubprocessRuntimeControlAdapter,
            idempotency_key,
        )

        workspace = Path(args.workspace).resolve()
        directory = workspace / ".herdr-loop" / "tasks" / args.task_id
        try:
            command = json.loads(args.command_json)
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(item, str) and item for item in command)
            ):
                raise ContinuationError("--command-json must be a non-empty JSON argv array")
            candidates = []
            for envelope_path in sorted((directory / "continuations").glob("*/envelope.json")):
                envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
                if args.wake_id is not None and envelope.get("wake_id") != args.wake_id:
                    continue
                payload_path = envelope_path.with_name("payload.json")
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
                candidates.append((envelope, payload))
            if len(candidates) != 1:
                raise ContinuationError(
                    "continuation consume requires exactly one matching persisted envelope"
                )
            envelope, payload = candidates[0]
            adapter = SubprocessRuntimeControlAdapter(
                command=tuple(command), provider_id=args.provider_id,
                coordinator_surface=args.coordinator_surface,
                identity_evidence_ref=args.identity_evidence_ref,
                duplicate_suppression_evidence_ref=args.duplicate_suppression_ref,
            )
            capability_path = directory / "continuations" / "capability.json"
            capability = json.loads(capability_path.read_text(encoding="utf-8"))
            if capability != adapter.capability():
                raise ContinuationError(
                    "CLI continuation Adapter conflicts with registered capability"
                )
            if not args.approve:
                result = {
                    "status": "dry_run",
                    "task_id": args.task_id,
                    "wake_id": envelope["wake_id"],
                    "idempotency_key": idempotency_key(envelope),
                    "target": envelope["target"],
                    "command": command,
                }
            else:
                receipt = ContinuationStore(
                    directory, args.task_id
                ).consume_with_adapter(envelope, payload, adapter)
                result = {
                    "status": "consumed",
                    "task_id": args.task_id,
                    "wake_id": envelope["wake_id"],
                    "receipt": receipt,
                }
        except (ContinuationError, OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"VALP-E-CONTINUATION-ADAPTER: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif result["status"] == "dry_run":
            print(
                f"Continuation dry run: {result['wake_id']} "
                f"({result['idempotency_key']})"
            )
        else:
            print(f"Continuation consumed: {result['wake_id']}")
        return 0

    if args.command == "adapter" and args.adapter_command == "queue":
        from .runtime_adapters import (
            RuntimeAdapterError,
            load_runtime_v3_receipts,
            record_queue_cancellation_acknowledgement,
            record_queue_cancellation_proof,
            record_queue_cancellation_request,
            record_queue_claim,
            record_queue_terminal_observation,
        )

        workspace = Path(args.workspace).resolve()
        directory = workspace / ".herdr-loop" / "tasks" / args.task_id
        try:
            submissions = [
                receipt
                for receipt in load_runtime_v3_receipts(directory, "queue")
                if receipt.get("event") == "dispatch_submitted"
                and receipt.get("agent") == args.agent
                and receipt.get("role") == args.role
                and (args.attempt_id is None or receipt.get("attempt_id") == args.attempt_id)
            ]
            if len(submissions) != 1:
                raise RuntimeAdapterError(
                    "Queue observation requires exactly one matching submitted Attempt"
                )
            submission = submissions[0]
            if args.queue_command == "claim":
                lifecycle = record_queue_claim(
                    directory, args.task_id, submission,
                    worker_id=args.worker_id, run_id=args.run_id,
                    claim_token=args.claim_token,
                    expected_revision=args.expected_revision,
                )
                result = {"lifecycle": lifecycle}
                exit_code = 0
            elif args.queue_command == "cancel":
                lifecycle = record_queue_cancellation_request(
                    directory, args.task_id, submission,
                    authority=args.authority, reason=args.reason,
                    expected_revision=args.expected_revision,
                )
                result = {"lifecycle": lifecycle}
                if lifecycle["event"] == "cancelled":
                    proof_ref, observation = record_queue_cancellation_proof(
                        directory, args.task_id, submission, lifecycle
                    )
                    result.update({"proof_ref": proof_ref, "observation": observation})
                exit_code = 0
            elif args.queue_command == "ack-cancel":
                lifecycle, observation = record_queue_cancellation_acknowledgement(
                    directory, args.task_id, submission,
                    worker_id=args.worker_id, run_id=args.run_id,
                    claim_token=args.claim_token,
                    claim_event_id=args.claim_event_id,
                    expected_revision=args.expected_revision,
                )
                result = {"lifecycle": lifecycle, "observation": observation}
                exit_code = 0
            else:
                receipt, observation = record_queue_terminal_observation(
                    directory, args.task_id, submission, args.observation_ref,
                )
                result = {"receipt": receipt, "observation": observation}
                exit_code = 0 if receipt["event"] == "dispatch_completed" else 1
        except RuntimeAdapterError as error:
            raise SystemExit(f"Queue v3 {args.queue_command} failed: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            if args.queue_command == "observe":
                print(
                    f"Recorded Queue v3 terminal observation: {receipt['event']} "
                    f"for {args.agent}/{args.role} ({receipt['receipt_id']})"
                )
            else:
                print(
                    f"Recorded Queue v3 lifecycle event: {result['lifecycle']['event']} "
                    f"for {args.agent}/{args.role} ({result['lifecycle']['event_id']})"
                )
        return exit_code

    if args.command == "adapter" and args.adapter_command == "manual":
        from .runtime_adapters import (
            RuntimeAdapterError,
            record_manual_attestation,
            record_manual_decision,
        )

        workspace = Path(args.workspace).resolve()
        directory = workspace / ".herdr-loop" / "tasks" / args.task_id
        if args.manual_command in {"revoke", "adjudicate"}:
            try:
                decision = record_manual_decision(
                    directory,
                    args.task_id,
                    action=args.manual_command,
                    target_receipt_id=args.receipt_id,
                    conflicting_receipt_ids=(
                        args.conflicting_receipt_id
                        if args.manual_command == "adjudicate"
                        else None
                    ),
                    authority=args.authority,
                    authority_ref=args.authority_ref,
                    statement=args.statement,
                )
            except RuntimeAdapterError as error:
                raise SystemExit(f"Manual v3 decision failed: {error}") from error
            if args.json:
                print(json.dumps(decision, indent=2, ensure_ascii=False))
            else:
                print(
                    f"Recorded Manual v3 {args.manual_command}: "
                    f"{decision['decision_id']}"
                )
            return 0

        from .submission import work_item_identity

        dependencies = read_json(directory / "submission-dependencies.json")
        identity = next(
            (
                item
                for item in dependencies.get("work_items") or []
                if isinstance(item, dict)
                and item.get("agent") == args.agent
                and item.get("role") == args.role
            ),
            work_item_identity(args.task_id, args.agent, args.role),
        )
        try:
            receipt, observation = record_manual_attestation(
                directory,
                args.task_id,
                agent=args.agent,
                role=args.role,
                work_item_id=str(identity["work_item_id"]),
                dispatch_id=str(identity["dispatch_id"]),
                dispatch_generation=int(identity["dispatch_generation"]),
                dispatch_ref=f"agents/{args.agent}/dispatch.md",
                expected_refs=list(identity.get("expected_refs") or []),
                event=args.event,
                authority=args.authority,
                authority_ref=args.authority_ref,
                statement=args.statement,
            )
        except RuntimeAdapterError as error:
            raise SystemExit(f"Manual v3 attestation failed: {error}") from error
        result = {"receipt": receipt, "observation": observation}
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(
                f"Recorded Manual v3 attestation: {args.event} "
                f"for {args.agent}/{args.role} ({receipt['receipt_id']})"
            )
        return 0

    if args.command == "catalog":
        try:
            catalog = EvidenceCatalog(
                Path(args.workspace),
                Path(args.database) if args.database else None,
            )
            if args.catalog_command == "index":
                if bool(args.task_id) == bool(args.index_all):
                    raise CatalogError("catalog index requires exactly one TASK_ID or --all")
                result = catalog.index_workspace() if args.index_all else catalog.index_task(args.task_id)
            elif args.catalog_command == "fixtures":
                result = catalog.index_fixtures(Path(args.manifest))
            elif args.catalog_command == "search":
                result = catalog.search(
                    args.query,
                    statuses=args.status,
                    evidence_type=args.evidence_type,
                    agent=args.agent,
                    task_id=args.catalog_task_id,
                    content_digest=args.digest,
                    limit=args.limit,
                )
            elif args.catalog_command == "fixture":
                result = catalog.search(
                    args.query,
                    statuses=args.status,
                    evidence_type=args.evidence_type,
                    anonymous_only=True,
                    limit=args.limit,
                )
            elif args.catalog_command == "context":
                result = catalog.context(
                    args.query,
                    statuses=args.status,
                    evidence_type=args.evidence_type,
                    agent=args.agent,
                    task_id=args.catalog_task_id,
                    anonymous_only=args.anonymous,
                    limit=args.limit,
                    max_chars=args.max_chars,
                )
            elif args.catalog_command == "show":
                result = catalog.show(args.catalog_id)
            elif args.catalog_command == "verify":
                result = catalog.verify(args.catalog_id)
            elif args.catalog_command == "sweep":
                result = catalog.sweep()
            elif args.catalog_command == "invalidate":
                result = catalog.invalidate(args.catalog_id, args.reason)
            else:  # pragma: no cover - argparse prevents this branch
                raise CatalogError(f"unsupported catalog command: {args.catalog_command}")
        except (CatalogError, OSError, json.JSONDecodeError, sqlite3.Error) as error:
            raise SystemExit(f"VALP-E-CATALOG: {error}") from error
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.catalog_command == "index":
            if args.index_all:
                print(
                    f"Catalog indexed {result['indexed_count']} evidence item(s) "
                    f"across {result['task_count']} task(s) (skipped {result['skipped_count']})."
                )
            else:
                print(
                    f"Catalog indexed {result['indexed_count']} evidence item(s) "
                    f"for {result['task_id']} (skipped {result['skipped_count']})."
                )
        elif args.catalog_command == "fixtures":
            print(f"Catalog indexed {result['indexed_count']} anonymous fixture(s).")
        elif args.catalog_command in {"search", "fixture"}:
            for item in result["results"]:
                print(f"{item['citation']} score={item['score']:.6f}")
            print(f"Results: {result['count']}")
        elif args.catalog_command == "context":
            print(result["context"])
            if result["omitted"]:
                print(f"Omitted: {len(result['omitted'])}")
        elif args.catalog_command == "verify":
            print(
                f"Catalog verify {'PASS' if result['ok'] else 'FAIL'}: "
                f"{result['catalog_id']} ({result['reason']})"
            )
        elif args.catalog_command == "sweep":
            print(
                f"Catalog sweep: stale={result['stale_count']} "
                f"invalid={result['invalid_count']} unchanged={result['unchanged_count']}"
            )
        elif args.catalog_command == "invalidate":
            print(
                f"Catalog invalidated {result['entry']['catalog_id']}; "
                f"stale dependents={len(result['stale_dependents'])}"
            )
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 1 if args.catalog_command == "verify" and not result["ok"] else 0

    if args.command == "audit":
        directory = resolve_task_dir(Path(args.path), args.task_id)
        report = TaskAudit(directory, strict=args.strict).run()
        graph_result = None
        if args.emit_task_graph:
            graph = build_task_graph(directory, report_to_dict(report))
            written = render_task_graph(graph, directory / "task-graph", {"json", "html", "svg"})
            graph_result = {"directory": str(directory / "task-graph"), "files": [str(path) for path in written]}
        if args.json:
            payload = report_to_dict(report)
            if graph_result:
                payload["task_graph"] = graph_result
            print(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            print_text_report(report)
            if graph_result:
                print()
                print(f"Task Graph refreshed: {graph_result['directory']}")
        return 1 if report.status == FAIL else 0

    if args.command == "graph":
        workspace = Path(args.workspace) if args.workspace else Path(args.path)
        directory = resolve_task_dir(workspace, args.task_id)
        report = TaskAudit(directory).run()
        graph = build_task_graph(directory, report_to_dict(report))
        formats = {"json", "html", "svg"} if args.format == "all" else {args.format}
        output_dir = Path(args.output_dir) if args.output_dir else directory / "task-graph"
        written = render_task_graph(graph, output_dir, formats)
        result = {
            "task_id": graph["task_id"],
            "status": graph["status"],
            "audit": graph["audit"],
            "output_dir": str(output_dir.resolve()),
            "files": [str(path.resolve()) for path in written],
            "projection_only": True,
        }
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"Task Graph: {graph['task_id']}")
            print(f"Status: {graph['status']} | Audit: {graph['audit'].get('status', 'not_run')}")
            print(f"Output: {output_dir.resolve()}")
            for path in written:
                print(f"  {path.name}")
        return 1 if report.status == FAIL else 0

    if args.command == "doctor":
        if args.snapshot and args.task_id:
            parser.error("doctor --snapshot cannot be combined with --task")
        if args.snapshot_ttl < 1 or args.snapshot_ttl > 3600:
            parser.error("doctor --snapshot-ttl must be between 1 and 3600")
        snapshot_result = None
        if args.snapshot:
            report, snapshot_result = collect_doctor_with_snapshot(
                Path(args.workspace),
                Path(args.snapshot).expanduser(),
                ttl_seconds=args.snapshot_ttl,
            )
        else:
            report = collect_doctor_report(Path(args.workspace), task_id=args.task_id)
        report_path = write_markdown_report(report, args.report) if args.report else None
        if args.json:
            data = doctor_report_to_dict(report)
            if report_path:
                data["report_path"] = str(report_path)
            if snapshot_result:
                data["snapshot"] = snapshot_result
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(render_text_summary(report))
            if snapshot_result:
                print()
                print(f"Snapshot: {snapshot_result['status']} ({snapshot_result['snapshot_path']})")
            if report_path:
                print()
                print(f"Report written: {report_path}")
        return 1 if report.status == FAIL else 0

    if args.command == "remediate":
        workspace = Path(args.workspace).resolve()
        try:
            if args.remediate_command == "plan":
                report = collect_doctor_report(workspace, task_id=args.task_id)
                plan = build_repair_plan(report, workspace)
                output_path = write_remediation_json(Path(args.output), plan) if args.output else None
                result = dict(plan)
                if output_path:
                    result["artifact_path"] = str(output_path.resolve())
                if args.json:
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                else:
                    print(f"Repair plan: {plan['plan_id']}")
                    print(f"Status: {plan['status']}")
                    print(f"Risk: {plan['risk_classification']}")
                    print(f"Diagnostics: {len(plan['diagnostics'])}")
                    print(f"Actions: {len(plan['actions'])}")
                    if output_path:
                        print(f"Plan written: {output_path}")
                return 1 if plan["status"] == "blocked" else 0

            if args.remediate_command == "recovery-plan":
                report = collect_doctor_report(workspace)
                plan = build_recovery_plan(
                    report,
                    workspace,
                    resource_id=args.resource_id,
                    resource_version=args.resource_version,
                    rollback_token=args.rollback_token,
                )
                output_path = write_remediation_json(Path(args.output), plan) if args.output else None
                result = dict(plan)
                if output_path:
                    result["artifact_path"] = str(output_path.resolve())
                if args.json:
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                else:
                    print(f"Recovery plan: {plan['plan_id']}")
                    print("Status: approval_required")
                    if output_path:
                        print(f"Plan written: {output_path}")
                return 0

            if args.remediate_command == "apply":
                plan = read_remediation_json(Path(args.plan))
                receipt, certificate = execute_repair_plan(plan, workspace)
                receipt_path = write_remediation_json(Path(args.receipt), receipt)
                certificate_path = None
                if certificate:
                    raw_certificate_path = args.certificate or str(
                        receipt_path.with_name(f"{receipt_path.stem}-proof.json")
                    )
                    certificate_path = write_remediation_json(
                        Path(raw_certificate_path), certificate
                    )
                result = {
                    "receipt": receipt,
                    "receipt_path": str(receipt_path.resolve()),
                    "certificate": certificate,
                    "certificate_path": str(certificate_path.resolve()) if certificate_path else None,
                }
                if args.json:
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                else:
                    print(f"Repair receipt: {receipt['receipt_id']}")
                    print(f"Status: {receipt['status']}")
                    print(f"Receipt written: {receipt_path}")
                    if certificate_path:
                        print(f"Proof written: {certificate_path}")
                return 0 if receipt["status"] == "fixed" else 1

            if args.remediate_command == "verify":
                receipt = read_remediation_json(Path(args.receipt))
                result = verify_repair_receipt(receipt, workspace)
                if args.json:
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                else:
                    print(f"Repair receipt: {result['receipt_id']}")
                    print(f"Verification: {result['status']}")
                    print(f"Regressed claims: {len(result['regressed'])}")
                return 0 if result["status"] == "valid" else 1
        except RemediationError as error:
            if args.json:
                print(json.dumps({"status": "error", "error": str(error)}, indent=2))
            else:
                print(f"Remediation blocked: {error}", file=sys.stderr)
            return 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
