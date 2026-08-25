from __future__ import annotations

import html
import hashlib
import json
from pathlib import Path
import re
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return records
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _task_goal(task_dir: Path, task_id: str) -> str:
    try:
        lines = task_dir.joinpath("task.md").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return task_id
    in_goal = False
    goal: list[str] = []
    for line in lines:
        if line.strip().lower() == "## goal":
            in_goal = True
            continue
        if in_goal and line.startswith("## "):
            break
        if in_goal and line.strip():
            goal.append(line.strip())
    return " ".join(goal) or task_id


def _expected_refs(task_dir: Path, evidence_board: dict[str, Any], receipts: list[dict[str, Any]]) -> list[str]:
    refs: set[str] = set()
    for claim in evidence_board.get("claims", []) if isinstance(evidence_board.get("claims"), list) else []:
        if not isinstance(claim, dict):
            continue
        values = claim.get("required_evidence", [])
        if isinstance(values, list):
            refs.update(str(value) for value in values if value)
    for receipt in receipts:
        values = receipt.get("expected_refs", [])
        if isinstance(values, list):
            refs.update(str(value) for value in values if value)
    expanded: set[str] = set()
    agents = set()
    for path in sorted(task_dir.joinpath("agents").glob("*/")):
        agents.add(path.name)
    for ref in refs:
        if "<agent>" in ref:
            for agent in agents:
                expanded.add(ref.replace("<agent>", agent))
        else:
            expanded.add(ref)
    return sorted(ref for ref in expanded if _is_safe_task_ref(ref))


def _is_safe_task_ref(value: str) -> bool:
    """Accept only non-empty task-relative POSIX paths without traversal."""
    path = Path(value)
    return bool(
        value
        and not path.is_absolute()
        and "\\" not in value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


_ABSOLUTE_PATH = re.compile(r"(?<![:/\\w.-])/(?:[^\s\"'<>]+)")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9_.-])(?:[A-Za-z]:\\|\\\\)[^\s\"'<>]+")


def _display(value: object) -> str:
    """Keep source labels useful without serializing machine-local paths."""
    redacted = _WINDOWS_ABSOLUTE_PATH.sub("<redacted-path>", str(value or ""))
    return _ABSOLUTE_PATH.sub("<redacted-path>", redacted)


def _graph_id(value: object, prefix: str) -> str:
    """Use source identifiers when safe; otherwise keep a deterministic opaque ID."""
    text = str(value or "")
    if text and _is_safe_task_ref(text) and re.fullmatch(r"[A-Za-z0-9._-]+", text):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _audit_summary(audit_report: dict[str, Any] | None) -> dict[str, Any]:
    """A graph shows audit state, never a copied audit payload or its task path."""
    audit = audit_report if isinstance(audit_report, dict) else {}
    counts = {}
    for key in ("pass_count", "warn_count", "fail_count", "skip_count"):
        value = audit.get(key, 0)
        counts[key] = value if isinstance(value, int) and value >= 0 else 0
    status = str(audit.get("status") or "not_run")
    return {"status": status if status in {"pass", "warn", "fail", "not_run"} else "not_run", **counts}


def _receipt_status(receipts: list[dict[str, Any]], agent: str) -> str:
    events = [str(item.get("event") or "") for item in receipts if str(item.get("agent") or "") == agent]
    if "dispatch_completed" in events:
        return "completed"
    if "dispatch_submitted" in events:
        return "running"
    if "dispatch_blocked" in events or "manual_blocked" in events:
        return "blocked"
    return "planned"


def _role_assignments(state: dict[str, Any], routing: dict[str, Any], declaration: dict[str, Any]) -> dict[str, str]:
    for source in (state, routing, declaration):
        values = source.get("role_assignments")
        if isinstance(values, dict) and values:
            return {str(role): str(agent) for role, agent in values.items() if role and agent}
    values = declaration.get("assignments")
    return {str(role): str(agent) for role, agent in values.items()} if isinstance(values, dict) else {}


def build_task_graph(task_dir: Path, audit_report: dict[str, Any] | None = None) -> dict[str, Any]:
    task_dir = task_dir.resolve()
    state = _load_json(task_dir / "state.json")
    routing = _load_json(task_dir / "routing.json")
    declaration = _load_json(task_dir / "assignment-declaration.json")
    evidence_board = _load_json(task_dir / "evidence-board.json")
    receipts = _load_jsonl(task_dir / "dispatch-receipts.jsonl")
    source_task_id = str(state.get("task_id") or routing.get("task_id") or task_dir.name)
    task_id = _graph_id(source_task_id, "task")
    assignments = dict(sorted(_role_assignments(state, routing, declaration).items()))
    expected_refs = _expected_refs(task_dir, evidence_board, receipts)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    nodes.append({
        "id": f"task:{task_id}",
        "kind": "task",
        "label": _display(source_task_id),
        "status": str(state.get("status") or "unknown"),
        "detail": _display(_task_goal(task_dir, source_task_id)),
        "refs": ["state.json", "task.md"],
    })

    for role, agent in assignments.items():
        role_id = _graph_id(role, "role")
        agent_token = _graph_id(agent, "agent")
        work_id = f"workitem:{role_id}"
        status = _receipt_status(receipts, agent)
        nodes.append({
            "id": work_id,
            "kind": "workitem",
            "label": _display(role),
            "status": status,
            "detail": f"Assigned to {_display(agent)}",
            "refs": ["assignment-declaration.json", "routing.json"],
        })
        edges.append({"from": f"task:{task_id}", "to": work_id, "type": "HAS_WORK_ITEM"})
        agent_id = f"agent:{agent_token}"
        if not any(node["id"] == agent_id for node in nodes):
            nodes.append({
                "id": agent_id,
                "kind": "agent",
                "label": _display(agent),
                "status": _receipt_status(receipts, agent),
                "detail": "Selected by the Leader declaration",
                "refs": ["assignment-declaration.json"],
            })
        edges.append({"from": work_id, "to": agent_id, "type": "ASSIGNED_TO"})

    for ref in expected_refs:
        path = task_dir / ref
        exists = path.is_file()
        evidence_id = f"evidence:{ref}"
        nodes.append({
            "id": evidence_id,
            "kind": "evidence",
            "label": ref,
            "status": "present" if exists else "missing",
            "detail": "Expected artifact" if not exists else "Artifact exists in the task folder",
            "refs": [ref],
        })
        owners = [role for role, agent in assignments.items() if ref.startswith(f"agents/{agent}/")]
        for role in owners or list(assignments)[:1]:
            if role:
                edges.append({"from": f"workitem:{_graph_id(role, 'role')}", "to": evidence_id, "type": "PRODUCES"})

    for index, receipt in enumerate(receipts):
        event = str(receipt.get("event") or "unknown")
        agent = str(receipt.get("agent") or "unknown")
        # The ledger line is the durable reference. Its provider identifier can
        # contain runtime-specific data, so a graph uses a stable line ordinal.
        node_id = f"receipt:{index + 1}"
        nodes.append({
            "id": node_id,
            "kind": "receipt",
            "label": _display(event),
            "status": "recorded",
            "detail": _display(f"{agent} · {receipt.get('ts', '')}".strip(" ·")),
            "refs": ["dispatch-receipts.jsonl"],
        })
        role = next((_graph_id(role, "role") for role, owner in assignments.items() if owner == agent), None)
        if role:
            edges.append({"from": f"workitem:{role}", "to": node_id, "type": "RECORDED_AS"})

    audit = _audit_summary(audit_report)
    audit_status = audit["status"]
    nodes.append({
        "id": "audit:valp",
        "kind": "audit",
        "label": "valp audit",
        "status": audit_status,
        "detail": f"pass={audit['pass_count']} warn={audit['warn_count']} fail={audit['fail_count']}",
        "refs": ["state.json", "routing.json"],
    })
    for role in assignments:
        edges.append({"from": f"workitem:{_graph_id(role, 'role')}", "to": "audit:valp", "type": "CHECKED_BY"})

    return {
        "schema_version": "valp-task-graph.v1",
        "graph_kind": "task_graph",
        "authority": "task-local-ledger-and-audit",
        "projection_only": True,
        "task_id": task_id,
        "status": str(state.get("status") or "unknown"),
        "audit": audit,
        "nodes": sorted(nodes, key=lambda node: str(node["id"])),
        "edges": sorted(edges, key=lambda edge: (str(edge["from"]), str(edge["to"]), str(edge["type"]))),
        "ontology_ref": "ontology is used for routing; it is not this user-facing graph",
    }


def _svg(graph: dict[str, Any]) -> str:
    columns = {"task": 40, "workitem": 300, "agent": 560, "evidence": 820, "receipt": 1080, "audit": 1340}
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in columns}
    for node in graph.get("nodes", []):
        grouped.setdefault(str(node.get("kind")), []).append(node)
    positions: dict[str, tuple[int, int]] = {}
    for kind, nodes in grouped.items():
        for index, node in enumerate(nodes):
            positions[str(node["id"])] = (columns.get(kind, 40), 70 + index * 92)
    height = max(360, max((y for _, y in positions.values()), default=280) + 110)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 {height}" role="img" aria-label="VALP task graph">',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#708090"/></marker></defs>',
        '<rect width="100%" height="100%" fill="#0b1015"/>',
    ]
    for edge in graph.get("edges", []):
        source = positions.get(str(edge.get("from")))
        target = positions.get(str(edge.get("to")))
        if not source or not target:
            continue
        x1, y1 = source[0] + 190, source[1] + 28
        x2, y2 = target[0], target[1] + 28
        parts.append(f'<path d="M{x1} {y1} C{x1 + 40} {y1}, {x2 - 40} {y2}, {x2} {y2}" fill="none" stroke="#536575" stroke-width="1.4" marker-end="url(#arrow)"/>')
    colors = {"task": "#2b6f9f", "workitem": "#3c7d5a", "agent": "#7652a5", "evidence": "#966b2b", "receipt": "#476d82", "audit": "#a14545"}
    for node in graph.get("nodes", []):
        node_id = str(node["id"])
        x, y = positions[node_id]
        color = colors.get(str(node.get("kind")), "#3b4a56")
        label = html.escape(str(node.get("label") or node_id)[:30])
        status = html.escape(str(node.get("status") or "unknown")[:18])
        parts.append(f'<g><rect x="{x}" y="{y}" width="190" height="56" rx="4" fill="{color}" stroke="#dce6ed" stroke-opacity=".25"/><text x="{x + 10}" y="{y + 22}" fill="#fff" font-size="13" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif">{label}</text><text x="{x + 10}" y="{y + 42}" fill="#e4edf2" font-size="11" font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif">{status}</text></g>')
    parts.append("</svg>")
    return "".join(parts)


def render_task_graph(graph: dict[str, Any], output_dir: Path, formats: set[str]) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    if "json" in formats:
        path = output_dir / "task-graph.json"
        path.write_text(json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    svg = _svg(graph)
    if "svg" in formats:
        path = output_dir / "task-graph.svg"
        path.write_text(svg + "\n", encoding="utf-8")
        written.append(path)
    if "html" in formats:
        path = output_dir / "task-graph.html"
        payload = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
        page = f"""<!doctype html><meta charset=\"utf-8\"><title>VALP Task Graph · {html.escape(graph['task_id'])}</title>
<style>body{{margin:0;background:#0b1015;color:#dce6ed;font:14px -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}}header{{padding:20px 24px;border-bottom:1px solid #2a3945}}h1{{margin:0 0 6px;font-size:20px}}p{{margin:0;color:#9fb0bc}}main{{overflow:auto;padding:18px}}svg{{display:block;min-width:1100px;max-width:none}}.meta{{display:flex;gap:18px;flex-wrap:wrap;margin-top:12px;color:#b9c9d4}}a{{color:#8dc7ff}}</style>
<header><h1>VALP Task Graph · {html.escape(graph['task_id'])}</h1><p>{html.escape(str(graph.get('ontology_ref', '')))}</p><div class=\"meta\"><span>Status: {html.escape(str(graph.get('status')))}</span><span>Audit: {html.escape(str(graph.get('audit', {}).get('status','not_run')))}</span><span>Projection only: yes</span></div></header><main>{svg}</main><script type=\"application/json\" id=\"task-graph\">{payload}</script>"""
        path.write_text(page, encoding="utf-8")
        written.append(path)
    return written
