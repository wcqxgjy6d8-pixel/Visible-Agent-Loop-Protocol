from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .audit import FAIL, TaskAudit, print_text_report, report_to_dict, resolve_task_dir
from .workflow import collect_runtime_preflight, dispatch_task, publish_task, route_task, scan_workspace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="valp",
        description="VALP reference CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  valp audit examples/minimal-task
  valp publish TASK-001 --workspace . --prompt "Fix the bug and verify it"
  valp dispatch TASK-001 --workspace .

notes:
  dispatch prints Manual Mode instructions for manual tasks.
  dispatch submits only through the HERDR reference adapter today.
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
    publish.add_argument("--no-route", action="store_true", help="Only create task.md/state.json")
    publish.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    scan = sub.add_parser("scan", help="Scan local capabilities and overlay into a workspace")
    scan.add_argument("--workspace", default=".", help="Workspace root")
    scan.add_argument("--task", dest="task_id", help="Task id to update")
    scan.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    route = sub.add_parser("route", help="Route an existing VALP task")
    route.add_argument("task_id", help="Task id")
    route.add_argument("--workspace", default=".", help="Workspace root")
    route.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    dispatch = sub.add_parser("dispatch", help="Print dispatch instructions or submit through the HERDR reference adapter")
    dispatch.add_argument("task_id", help="Task id")
    dispatch.add_argument("--workspace", default=".", help="Workspace root")
    dispatch.add_argument("--agent", default="all", help="Agent name or all")
    dispatch.add_argument("--submit", action="store_true", help="Actually call herdr-loop submit-dispatch through the HERDR reference adapter")

    preflight = sub.add_parser("preflight", help="Check runtime panes, CLI probes, and terminal sizing")
    preflight.add_argument("--agent", action="append", help="Agent name to check; may be repeated")
    preflight.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    audit = sub.add_parser("audit", help="Audit a VALP task evidence folder")
    audit.add_argument("path", nargs="?", default=".", help="Task folder or workspace root")
    audit.add_argument("--task", dest="task_id", help="Task id under <workspace>/.herdr-loop/tasks/")
    audit.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    audit.add_argument("--strict", action="store_true", help="Treat warnings as failures")
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
        capabilities = scan_workspace(Path(args.workspace), args.task_id)
        if args.json:
            print(json.dumps(capabilities, indent=2, ensure_ascii=False))
        else:
            print(f"Scanned VALP capabilities into {Path(args.workspace).resolve() / '.herdr-loop' / 'agents' / 'capabilities.json'}")
        return 0

    if args.command == "route":
        routing = route_task(Path(args.workspace), args.task_id)
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
        commands = dispatch_task(Path(args.workspace), args.task_id, agent=args.agent, submit=args.submit)
        if args.submit:
            print(f"Submitted dispatch for task {args.task_id}")
        else:
            manual = any(command.startswith("Manual Mode:") for command in commands)
            if manual:
                print("Manual Mode dispatch instructions. Copy dispatches manually and record manual receipts:")
            else:
                print("Dispatch dry run for the HERDR reference adapter. Use --submit only when HERDR is ready:")
            for command in commands:
                print(command)
        return 0

    if args.command == "preflight":
        report = collect_runtime_preflight(args.agent)
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(f"VALP runtime preflight: {str(report.get('status', 'unknown')).upper()}")
            for agent, record in (report.get("agents") or {}).items():
                size = record.get("terminal_size") or {}
                print(
                    f"- {agent}: {record.get('status', 'unknown')} "
                    f"pane={record.get('pane_id')} "
                    f"size={size.get('width', '?')}x{size.get('height', '?')}"
                )
        return 1 if report.get("status") == "fail" else 0

    if args.command == "audit":
        directory = resolve_task_dir(Path(args.path), args.task_id)
        report = TaskAudit(directory, strict=args.strict).run()
        if args.json:
            print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
        else:
            print_text_report(report)
        return 1 if report.status == FAIL else 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
