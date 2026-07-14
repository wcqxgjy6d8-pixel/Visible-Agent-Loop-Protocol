from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .audit import FAIL, TaskAudit, print_text_report, report_to_dict, resolve_task_dir
from .doctor import collect_doctor_report, render_text_summary, report_to_dict as doctor_report_to_dict, write_markdown_report
from .control_plane import ControlPlaneError, InstallationCore, PROTOCOL_VERSION, installation_root, load_observations
from .conformance import run_conformance
from .plugins import load_plugin_manifest
from .task_control import TASK_STATUSES, init_task, task_state, transition_task
from .process_adapter import run_process
from .workflow import RUNTIME_CHOICES, collect_runtime_preflight, dispatch_task, publish_task, read_json, resume_suspended_task, route_task, scan_workspace, wait_for_task


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

    publish = sub.add_parser("publish", help="Create a VALP task and auto-route by default")
    publish.add_argument("task_id", help="Task id")
    publish.add_argument("--workspace", default=".", help="Workspace root")
    publish.add_argument("--prompt", help="Task request")
    publish.add_argument("--prompt-file", help="Read task request from a file")
    publish.add_argument("--profile", help="Override auto profile classification")
    publish.add_argument(
        "--include-agent",
        action="append",
        default=[],
        help="Explicitly include an available agent as a supplemental routed role",
    )
    publish.add_argument("--runtime", choices=sorted(RUNTIME_CHOICES), default="auto", help="Runtime adapter to record and preflight")
    publish.add_argument("--no-route", action="store_true", help="Only create task.md/state.json")
    publish.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    scan = sub.add_parser("scan", help="Scan local capabilities and overlay into a workspace")
    scan.add_argument("--workspace", default=".", help="Workspace root")
    scan.add_argument("--task", dest="task_id", help="Task id to update")
    scan.add_argument("--runtime", choices=sorted(RUNTIME_CHOICES), default="auto", help="Runtime adapter to preflight")
    scan.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    route = sub.add_parser("route", help="Route an existing VALP task")
    route.add_argument("task_id", help="Task id")
    route.add_argument("--workspace", default=".", help="Workspace root")
    route.add_argument("--runtime", choices=sorted(RUNTIME_CHOICES), default="auto", help="Runtime adapter to record and preflight")
    route.add_argument(
        "--include-agent",
        action="append",
        default=None,
        help="Add an available agent to the existing task's requested supplemental roles",
    )
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
    dispatch.add_argument("--submit", action="store_true", help="Actually submit through the selected reference adapter when supported")

    preflight = sub.add_parser("preflight", help="Check selected runtime adapter readiness")
    preflight.add_argument("--agent", action="append", help="Agent name to check; may be repeated")
    preflight.add_argument("--runtime", choices=sorted(RUNTIME_CHOICES), default="auto", help="Runtime adapter to preflight")
    preflight.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    wait = sub.add_parser("wait", help="Suspend coordinator turns until a deterministic resume event")
    wait.add_argument("task_id", help="Task id")
    wait.add_argument("--workspace", default=".", help="Workspace root")
    wait.add_argument("--timeout", type=float, default=300.0, help="Maximum suspended wait in seconds")
    wait.add_argument("--poll-interval", type=float, default=0.25, help="Runtime polling interval in seconds")
    wait.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    resume = sub.add_parser("resume", help="Resume a suspended coordinator from an explicit external event")
    resume.add_argument("task_id", help="Task id")
    resume.add_argument("--workspace", default=".", help="Workspace root")
    resume.add_argument(
        "--event",
        choices=["user_input", "runtime_failure", "cancellation"],
        required=True,
        help="External event that resumes coordinator turns",
    )
    resume.add_argument(
        "--ref",
        dest="resume_ref",
        required=True,
        help="Task-local valp-exception-wake.v1 evidence ref",
    )
    resume.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    install = sub.add_parser("install", help="Manage the v0.3 installation control plane")
    install_sub = install.add_subparsers(dest="install_command", required=True)
    install_init = install_sub.add_parser("init", help="Create a persistent control root and bootstrap metadata")
    install_init.add_argument("--workspace", default=".", help="Workspace root")
    install_init.add_argument("--root", help="Explicit control root; defaults to <workspace>/.valp")
    install_init.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    leader = sub.add_parser("leader", help="Discover, select, inspect, or rotate the Installation Leader")
    leader_sub = leader.add_subparsers(dest="leader_command", required=True)
    candidates = leader_sub.add_parser("candidates", help="Run bounded read-only bootstrap discovery")
    candidates.add_argument("--workspace", default=".", help="Workspace root")
    candidates.add_argument("--root", help="Explicit control root")
    candidates.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    select = leader_sub.add_parser("select", help="Explicitly select and activate a discovered leader")
    select.add_argument("principal", help="Observed principal id")
    select.add_argument("--workspace", default=".", help="Workspace root")
    select.add_argument("--root", help="Explicit control root")
    select.add_argument("--json", action="store_true", help="Print machine-readable JSON")
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
    migrate_plan.add_argument("--apply", action="store_true", help="Apply the existing plan")
    migrate_plan.add_argument("--approve", action="store_true", help="Explicitly approve migration side effects")
    migrate_plan.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    plugin = sub.add_parser("plugin", help="Validate provider-neutral plugin manifests")
    plugin_sub = plugin.add_subparsers(dest="plugin_command", required=True)
    plugin_validate = plugin_sub.add_parser("validate", help="Validate a plugin manifest without enabling it")
    plugin_validate.add_argument("path", help="Plugin manifest JSON path")
    plugin_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    conformance = sub.add_parser("conformance", help="Run isolated v0.3 core conformance fixtures")
    conformance.add_argument("--profile", default="core-writer", choices=["core-reader", "core-writer", "plugin-host", "migration"], help="Conformance profile")
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

    adapter = sub.add_parser("adapter", help="Run an explicit runtime adapter")
    adapter_sub = adapter.add_subparsers(dest="adapter_command", required=True)
    process = adapter_sub.add_parser("process", help="Use the real local-process Full Mode adapter")
    process_sub = process.add_subparsers(dest="process_command", required=True)
    process_run = process_sub.add_parser("run", help="Dry-run or execute one addressable local worker")
    process_run.add_argument("task_id")
    process_run.add_argument("--command", required=True, help="Worker command parsed with shell-like quoting, without a shell")
    process_run.add_argument("--timeout", type=float, default=30.0)
    process_run.add_argument("--approve", action="store_true", help="Approve actual worker execution")
    process_run.add_argument("--workspace", default=".", help="Workspace/control root parent")
    process_run.add_argument("--root", help="Explicit control root")
    process_run.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    audit = sub.add_parser("audit", help="Audit a VALP task evidence folder")
    audit.add_argument("path", nargs="?", default=".", help="Task folder or workspace root")
    audit.add_argument("--task", dest="task_id", help="Task id under <workspace>/.herdr-loop/tasks/")
    audit.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    audit.add_argument("--strict", action="store_true", help="Treat warnings as failures")

    doctor = sub.add_parser("doctor", help="Diagnose VALP workspace health without mutating by default")
    doctor.add_argument("--workspace", default=".", help="Workspace root")
    doctor.add_argument("--task", dest="task_id", help="Optional task id to audit under <workspace>/.herdr-loop/tasks/")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    doctor.add_argument("--report", help="Write a Markdown report to a path, or use 'desktop'")
    return parser


def prompt_from_args(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.prompt:
        return args.prompt
    raise SystemExit("publish requires --prompt or --prompt-file")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "publish":
        directory = publish_task(
            Path(args.workspace),
            args.task_id,
            prompt_from_args(args),
            profile=args.profile,
            route=not args.no_route,
            runtime=args.runtime,
            include_agents=args.include_agent,
        )
        result = {"task_id": args.task_id, "task_dir": str(directory), "routed": not args.no_route}
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"Published VALP task: {args.task_id}")
            print(f"Task dir: {directory}")
            print("Routed: " + ("yes" if not args.no_route else "no"))
            visible = directory / "visible-routing.md"
            if visible.exists():
                print()
                print(visible.read_text(encoding="utf-8").strip())
        return 0

    if args.command == "scan":
        capabilities = scan_workspace(Path(args.workspace), args.task_id, runtime=args.runtime)
        if args.json:
            print(json.dumps(capabilities, indent=2, ensure_ascii=False))
        else:
            print(f"Scanned VALP capabilities into {Path(args.workspace).resolve() / '.herdr-loop' / 'agents' / 'capabilities.json'}")
        return 0

    if args.command == "route":
        if args.include_agent:
            task_state_path = Path(args.workspace).resolve() / ".herdr-loop" / "tasks" / args.task_id / "state.json"
            task_state = read_json(task_state_path) or {}
            current = list(task_state.get("requested_agents") or [])
            task_state["requested_agents"] = list(dict.fromkeys(current + args.include_agent))
            task_state_path.write_text(json.dumps(task_state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        routing = route_task(Path(args.workspace), args.task_id, runtime=args.runtime)
        if args.json:
            print(json.dumps(routing, indent=2, ensure_ascii=False))
        else:
            print(f"Routed VALP task: {args.task_id}")
            print("Selected agents: " + ", ".join(routing.get("selected_agents") or []))
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
        )
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
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
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"VALP suspension resumed: {result.get('resume_event', 'unknown')}")
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
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        core = InstallationCore(root)
        try:
            if args.leader_command == "candidates":
                result = core.discover_candidates()
            elif args.leader_command == "select":
                result = core.select_leader(args.principal)
            elif args.leader_command == "rotate":
                result = core.rotate_leader(args.principal)
            else:
                result = core.status()
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
                print(f"Installation leader: {(state.get('active_leader') or {}).get('principal_id', 'none')}")
                print(f"Epoch: {state.get('active_leader_epoch', 0)}")
                print(f"Status: {state.get('status')}")
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
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.json or result["fail_count"] else f"VALP conformance {result['status']}: pass={result['pass_count']} fail={result['fail_count']}")
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

    if args.command == "adapter" and args.adapter_command == "process" and args.process_command == "run":
        import shlex
        root = installation_root(Path(args.workspace), Path(args.root) if args.root else None)
        try:
            result = run_process(root, args.task_id, shlex.split(args.command), timeout_seconds=args.timeout, approve=args.approve)
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

    if args.command == "audit":
        directory = resolve_task_dir(Path(args.path), args.task_id)
        report = TaskAudit(directory, strict=args.strict).run()
        if args.json:
            print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
        else:
            print_text_report(report)
        return 1 if report.status == FAIL else 0

    if args.command == "doctor":
        report = collect_doctor_report(Path(args.workspace), task_id=args.task_id)
        report_path = write_markdown_report(report, args.report) if args.report else None
        if args.json:
            data = doctor_report_to_dict(report)
            if report_path:
                data["report_path"] = str(report_path)
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(render_text_summary(report))
            if report_path:
                print()
                print(f"Report written: {report_path}")
        return 1 if report.status == FAIL else 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
