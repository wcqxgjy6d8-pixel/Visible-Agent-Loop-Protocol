"""Provider-neutral, evidence-bound task cost accounting."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from datetime import datetime
import json
from pathlib import Path
from typing import Any


PRICE_CLASSES = ("official_list", "relay_account", "subscription_marginal")
ATTRIBUTION_FIELDS = ("dispatch_id", "work_item_id", "retry_of", "parent_agent")
ZERO = Decimal("0")


class CostGovernanceError(ValueError):
    pass


def append_event(path: Path, event: dict[str, Any], schema_version: str) -> None:
    if event.get("schema_version") != schema_version:
        raise CostGovernanceError(f"event schema_version must be {schema_version}")
    event_id = str(event.get("event_id") or "")
    if not event_id:
        raise CostGovernanceError("event_id is required")
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                prior = json.loads(line)
                existing[str(prior.get("event_id") or "")] = prior
    if event_id in existing:
        if event == existing[event_id]:
            return
        raise CostGovernanceError("conflicting event_id")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise CostGovernanceError(f"{field} must be a decimal") from error
    if not result.is_finite() or result < ZERO:
        raise CostGovernanceError(f"{field} must be a non-negative finite decimal")
    return result


def _tokens(value: Any, field: str) -> int:
    if type(value) is not int or value < 0:
        raise CostGovernanceError(f"{field} must be a non-negative integer")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CostGovernanceError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CostGovernanceError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise CostGovernanceError(f"{field} must include a timezone")
    return parsed


def _format(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.000001")))


def _snapshot_index(snapshots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        if snapshot.get("schema_version") != "valp-pricing-snapshot.v1":
            raise CostGovernanceError("pricing snapshot has unsupported schema_version")
        snapshot_id = str(snapshot.get("snapshot_id") or "")
        if not snapshot_id or snapshot_id in indexed:
            raise CostGovernanceError("pricing snapshots require unique snapshot_id values")
        if snapshot.get("currency") != "USD":
            raise CostGovernanceError("only USD pricing snapshots are supported")
        if not str(snapshot.get("provider") or "").strip() or not str(snapshot.get("model") or "").strip():
            raise CostGovernanceError("pricing snapshot requires provider and model")
        _timestamp(snapshot.get("effective_at"), "pricing snapshot effective_at")
        rates = snapshot.get("rates_per_million_tokens")
        if not isinstance(rates, dict) or set(rates) != set(PRICE_CLASSES):
            raise CostGovernanceError("pricing snapshot must define every price class")
        for price_class in PRICE_CLASSES:
            rate = rates[price_class]
            if not isinstance(rate, dict):
                raise CostGovernanceError(f"{price_class} rate must be an object")
            _decimal(rate.get("input"), f"{price_class}.input")
            _decimal(rate.get("output"), f"{price_class}.output")
        indexed[snapshot_id] = snapshot
    return indexed


def estimate_usage(snapshot: dict[str, Any], input_tokens: int, output_tokens: int) -> dict[str, str]:
    _tokens(input_tokens, "input_tokens")
    _tokens(output_tokens, "output_tokens")
    indexed = _snapshot_index([snapshot])
    rates = indexed[str(snapshot["snapshot_id"])]["rates_per_million_tokens"]
    return {
        price_class: _format(
            (Decimal(input_tokens) * _decimal(rates[price_class]["input"], f"{price_class}.input")
             + Decimal(output_tokens) * _decimal(rates[price_class]["output"], f"{price_class}.output"))
            / Decimal(1_000_000)
        )
        for price_class in PRICE_CLASSES
    }


def _empty_estimate() -> dict[str, Decimal]:
    return {price_class: ZERO for price_class in PRICE_CLASSES}


def _serialise_estimate(values: dict[str, Decimal]) -> dict[str, str]:
    return {price_class: _format(values[price_class]) for price_class in PRICE_CLASSES}


def _add_event(
    totals: dict[str, Decimal], snapshots: dict[str, dict[str, Any]], event: dict[str, Any], task_id: str
) -> tuple[str, dict[str, Decimal]]:
    if event.get("schema_version") != "valp-usage-event.v1" or event.get("task_id") != task_id:
        raise CostGovernanceError("usage event has invalid schema_version or task_id")
    agent = str(event.get("agent") or "")
    snapshot = snapshots.get(str(event.get("snapshot_id") or ""))
    if not agent or snapshot is None:
        raise CostGovernanceError("usage event must name an agent and known pricing snapshot")
    if "event_id" in event:
        for field in ("provider", "model", "currency"):
            if event.get(field) != snapshot.get(field):
                raise CostGovernanceError(f"usage event {field} does not match pricing snapshot")
        if _timestamp(event.get("observed_at"), "usage event observed_at") < _timestamp(
            snapshot.get("effective_at"), "pricing snapshot effective_at"
        ):
            raise CostGovernanceError("usage event predates pricing snapshot effective_at")
    values = estimate_usage(snapshot, event.get("input_tokens"), event.get("output_tokens"))
    for price_class in PRICE_CLASSES:
        totals[price_class] += Decimal(values[price_class])
    return agent, totals


def _usage_attribution(event: dict[str, Any]) -> dict[str, str]:
    attribution = {
        "event_id": str(event.get("event_id") or ""),
        "agent": str(event.get("agent") or ""),
    }
    for field in ATTRIBUTION_FIELDS:
        value = event.get(field)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise CostGovernanceError(f"usage event {field} must be a non-empty string")
            attribution[field] = value
    return attribution


def build_cost_report(
    task_id: str,
    snapshots: list[dict[str, Any]],
    usage_events: list[dict[str, Any]],
    billing_events: list[dict[str, Any]],
    planned_usage: list[dict[str, Any]],
) -> dict[str, Any]:
    indexed = _snapshot_index(snapshots)
    accumulated = _empty_estimate()
    projected_extra = _empty_estimate()
    per_agent: dict[str, dict[str, dict[str, Decimal]]] = {}
    seen_usage: dict[str, dict[str, Any]] = {}
    usage_attribution: list[dict[str, str]] = []
    for event in usage_events:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            raise CostGovernanceError("usage event_id is required")
        if event_id in seen_usage:
            if event == seen_usage[event_id]:
                continue
            raise CostGovernanceError("conflicting usage event_id")
        seen_usage[event_id] = event
        agent, accumulated = _add_event(accumulated, indexed, event, task_id)
        usage_attribution.append(_usage_attribution(event))
        agent_totals = per_agent.setdefault(agent, {"accumulated": _empty_estimate(), "projected": _empty_estimate()})
        _add_event(agent_totals["accumulated"], indexed, event, task_id)
    for event in planned_usage:
        agent, projected_extra = _add_event(projected_extra, indexed, {**event, "schema_version": "valp-usage-event.v1", "task_id": task_id}, task_id)
        agent_totals = per_agent.setdefault(agent, {"accumulated": _empty_estimate(), "projected": _empty_estimate()})
        _add_event(agent_totals["projected"], indexed, {**event, "schema_version": "valp-usage-event.v1", "task_id": task_id}, task_id)
    actual_total: Decimal | None = None
    seen_billing: set[str] = set()
    for event in billing_events:
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in seen_billing:
            raise CostGovernanceError("duplicate billing event_id")
        seen_billing.add(event_id)
        if event.get("schema_version") != "valp-billing-event.v1" or event.get("task_id") != task_id or event.get("currency") != "USD":
            raise CostGovernanceError("billing event has invalid schema_version, task_id, or currency")
        if not str(event.get("billing_evidence_ref") or "").strip():
            raise CostGovernanceError("billing event requires billing_evidence_ref")
        amount = _decimal(event.get("amount"), "billing amount")
        actual_total = (actual_total or ZERO) + amount
    projected = {key: accumulated[key] + projected_extra[key] for key in PRICE_CLASSES}
    return {
        "schema_version": "valp-cost-report.v1", "task_id": task_id, "currency": "USD",
        "accumulated_estimate": _serialise_estimate(accumulated),
        "projected_estimate": _serialise_estimate(projected),
        "actual_billed": _format(actual_total) if actual_total is not None else None,
        "actual_billed_status": "evidenced" if actual_total is not None else "not_available",
        "usage_attribution": usage_attribution,
        "per_agent": {
            agent: {"accumulated_estimate": _serialise_estimate(values["accumulated"]), "projected_estimate": _serialise_estimate({key: values["accumulated"][key] + values["projected"][key] for key in PRICE_CLASSES})}
            for agent, values in sorted(per_agent.items())
        },
    }


def enforce_cost_budget(task_dir: Path, task_id: str) -> dict[str, Any] | None:
    budget_path = task_dir / "cost-budget.json"
    if not budget_path.is_file():
        return None
    try:
        pricing = json.loads((task_dir / "pricing-snapshots.json").read_text(encoding="utf-8"))
        budget = json.loads(budget_path.read_text(encoding="utf-8"))
        usage = [
            json.loads(line)
            for line in (task_dir / "usage-events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        billing_path = task_dir / "billing-events.jsonl"
        billing = [
            json.loads(line)
            for line in billing_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ] if billing_path.is_file() else []
    except (OSError, json.JSONDecodeError) as error:
        raise CostGovernanceError(f"cost evidence cannot be loaded: {error}") from error
    if pricing.get("task_id") != task_id or budget.get("task_id") != task_id:
        raise CostGovernanceError("cost evidence task_id does not match the task")
    report = build_cost_report(
        task_id,
        list(pricing.get("snapshots") or []),
        usage,
        billing,
        list(budget.get("planned_usage") or []),
    )
    hard_cap = budget.get("max_projected_official_list")
    if hard_cap is not None and Decimal(report["projected_estimate"]["official_list"]) > _decimal(
        hard_cap, "max_projected_official_list"
    ):
        raise CostGovernanceError("projected official-list cost exceeds the hard cap")
    return report
