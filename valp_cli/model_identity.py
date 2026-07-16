from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


UNKNOWN_MODEL = "unknown"
MODEL_EVIDENCE_STATUSES = {"strong", "degraded", "unknown", "invalid"}
DEFAULT_OBSERVATION_TTL_SECONDS = 3600
MIN_OBSERVATION_TTL_SECONDS = 60
MAX_OBSERVATION_TTL_SECONDS = 86400
MAX_FUTURE_CLOCK_SKEW_SECONDS = 300


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def bounded_observation_ttl(value: Any) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = DEFAULT_OBSERVATION_TTL_SECONDS
    return max(MIN_OBSERVATION_TTL_SECONDS, min(MAX_OBSERVATION_TTL_SECONDS, requested))


def observation_freshness(
    timestamp: Any,
    *,
    evaluated_at: Any,
    ttl_seconds: int,
) -> tuple[str, int | None]:
    observed = _parse_timestamp(timestamp)
    evaluated = _parse_timestamp(evaluated_at)
    if observed is None or evaluated is None:
        return "unknown", None
    age = int((evaluated - observed).total_seconds())
    if age < -MAX_FUTURE_CLOCK_SKEW_SECONDS:
        return "unknown", age
    age = max(0, age)
    return ("current" if age <= ttl_seconds else "stale"), age


def _value(value: Any, default: str = UNKNOWN_MODEL) -> str:
    text = str(value or "").strip()
    return text or default


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def _model_record(
    value: Any,
    *,
    fallback_provider: str,
    fallback_source: str,
    fallback_timestamp: str,
    fallback_reasoning: str,
) -> dict[str, str]:
    if isinstance(value, str):
        value = {"model_id": value}
    value = value if isinstance(value, dict) else {}
    return {
        "model_id": _value(value.get("model_id") or value.get("name")),
        "provider": _value(value.get("provider"), fallback_provider),
        "reasoning_mode": _value(value.get("reasoning_mode"), fallback_reasoning),
        "source": _value(value.get("source"), fallback_source),
        "timestamp": _value(value.get("timestamp") or value.get("observed_at"), fallback_timestamp),
        "confidence": _value(value.get("confidence"), "unknown"),
        "freshness": _value(value.get("freshness"), "unknown"),
    }


def model_identity_for(
    agent: str,
    info: dict[str, Any] | None,
    overlay_profile: dict[str, Any] | None,
    *,
    task_evidence: list[str] | None = None,
    observed_at: str | None = None,
    runtime_probe: dict[str, Any] | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any]:
    info = info or {}
    overlay_profile = overlay_profile or {}
    raw = info.get("model_identity") or {}
    if not isinstance(raw, dict):
        raw = {}
    evaluation_time = evaluated_at or observed_at or _now()
    timestamp = observed_at or evaluation_time
    provider = _value(raw.get("provider") or info.get("provider") or overlay_profile.get("provider"), agent)
    surface = _value(
        raw.get("agent_surface") or info.get("agent_surface"),
        "codex_cli" if agent == "codex" else agent,
    )
    reasoning = _value(raw.get("reasoning_mode") or info.get("reasoning_mode"), "unknown")
    declared = _model_record(
        raw.get("declared_model") or info.get("declared_model") or info.get("model"),
        fallback_provider=provider,
        fallback_source="declared capability registry",
        fallback_timestamp=timestamp,
        fallback_reasoning=reasoning,
    )
    probe = runtime_probe if isinstance(runtime_probe, dict) else {}
    probe_status = _value(probe.get("status"), "unsupported")
    probe_model = probe.get("model") if isinstance(probe.get("model"), dict) else None
    observed_value = probe_model if probe_status == "observed" else raw.get("observed_model") or info.get("observed_model")
    observed = _model_record(
        observed_value,
        fallback_provider=provider,
        fallback_source=_value(probe.get("source"), "runtime observation unavailable"),
        fallback_timestamp=_value(probe.get("observed_at"), timestamp),
        fallback_reasoning=reasoning,
    )
    if runtime_probe is not None and probe_status != "observed":
        observed = _model_record(
            None,
            fallback_provider=provider,
            fallback_source=_value(probe.get("source"), "runtime model probe unavailable"),
            fallback_timestamp=_value(probe.get("observed_at"), timestamp),
            fallback_reasoning=reasoning,
        )
    ttl_seconds = bounded_observation_ttl(probe.get("ttl_seconds"))
    freshness, observation_age = observation_freshness(
        observed.get("timestamp"),
        evaluated_at=evaluation_time,
        ttl_seconds=ttl_seconds,
    )
    if runtime_probe is not None and probe_status != "observed":
        freshness = "unknown"
        observation_age = None
    observed["freshness"] = freshness
    session_raw = probe.get("session_identity") if isinstance(probe.get("session_identity"), dict) else {}
    session_identity = {
        "status": _value(session_raw.get("status"), "unknown"),
        "token": _value(session_raw.get("token")),
        "source": _value(session_raw.get("source"), "runtime session identity unavailable"),
        "generation": _value(session_raw.get("generation")),
    }
    declared_id = declared["model_id"]
    observed_id = observed["model_id"]
    mismatch_fields: list[str] = []
    if declared_id != UNKNOWN_MODEL and observed_id != UNKNOWN_MODEL and declared_id != observed_id:
        mismatch_fields.append("model_id")
    if (
        declared["provider"] != UNKNOWN_MODEL
        and observed["provider"] != UNKNOWN_MODEL
        and declared["provider"] != observed["provider"]
    ):
        mismatch_fields.append("provider")
    if (
        declared["reasoning_mode"] != UNKNOWN_MODEL
        and observed["reasoning_mode"] != UNKNOWN_MODEL
        and declared["reasoning_mode"] != observed["reasoning_mode"]
    ):
        mismatch_fields.append("reasoning_mode")
    if declared_id == UNKNOWN_MODEL or observed_id == UNKNOWN_MODEL:
        mismatch_status = "unknown"
        mismatch_handling = "downgrade"
        mismatch_details = "Declared or observed model identity is unknown."
    elif mismatch_fields:
        mismatch_status = "mismatch"
        mismatch_handling = "invalidate"
        mismatch_details = "Runtime observation differs from the declaration: " + ", ".join(mismatch_fields) + "."
    else:
        mismatch_status = "match"
        mismatch_handling = "preserve"
        mismatch_details = "Declared and observed model identities match."

    session_known = session_identity["status"] == "known" and session_identity["token"] != UNKNOWN_MODEL
    if observed_id == UNKNOWN_MODEL:
        evidence_status = "unknown"
        history_status = "invalidated" if runtime_probe is not None else "downgraded"
    elif mismatch_status == "mismatch":
        evidence_status = "degraded"
        history_status = "invalidated"
    elif observed["freshness"] == "stale":
        evidence_status = "degraded"
        history_status = "invalidated"
    elif (
        observed["confidence"] != "high"
        or observed["freshness"] != "current"
        or declared["confidence"] != "high"
        or probe_status != "observed"
        or not session_known
    ):
        evidence_status = "degraded"
        history_status = "invalidated" if runtime_probe is not None and not session_known else "downgraded"
    else:
        evidence_status = "strong"
        history_status = "valid"

    history_binding_values = {
        "agent_surface": surface,
        "model_id": observed_id,
        "provider": observed["provider"],
        "reasoning_mode": observed["reasoning_mode"],
        "freshness": observed["freshness"],
        "session_token": session_identity["token"],
    }
    history_binding = {
        **history_binding_values,
        "fingerprint": hashlib.sha256(
            json.dumps(history_binding_values, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    invalidation_reasons: list[str] = []
    previous_binding = raw.get("history_binding") or info.get("history_binding")
    if runtime_probe is not None and not isinstance(previous_binding, dict):
        invalidation_reasons.append("model-bound history has no comparable prior binding")
    elif isinstance(previous_binding, dict) and previous_binding.get("fingerprint") != history_binding["fingerprint"]:
        invalidation_reasons.append("model-bound history binding changed")
    if mismatch_status == "mismatch":
        invalidation_reasons.append("declared and observed model identities differ")
    if observed["freshness"] == "stale":
        invalidation_reasons.append("observation TTL expired")
    if runtime_probe is not None and probe_status != "observed":
        invalidation_reasons.append("runtime probe did not produce an active model identity")
    if runtime_probe is not None and not session_known:
        invalidation_reasons.append("runtime session identity is unknown")
    if invalidation_reasons:
        history_status = "invalidated"

    high_risk_eligible = (
        probe_status == "observed"
        and observed_id != UNKNOWN_MODEL
        and observed["freshness"] == "current"
        and session_known
    )
    role_status = "eligible" if high_risk_eligible else "blocked"

    return {
        "agent_surface": surface,
        "provider": observed["provider"] if observed_id != UNKNOWN_MODEL else provider,
        "reasoning_mode": observed["reasoning_mode"] if observed_id != UNKNOWN_MODEL else reasoning,
        "permissions": _list(raw.get("permissions") or info.get("permissions")),
        "context": dict(raw.get("context") or info.get("context_policy") or {}),
        "task_evidence": _list(task_evidence or raw.get("task_evidence") or info.get("task_evidence")),
        "declared_model": declared,
        "observed_model": observed,
        "model_probe": {
            "schema_version": _value(probe.get("schema_version"), "valp-model-probe.v1"),
            "status": probe_status,
            "source": _value(probe.get("source"), "runtime model probe unavailable"),
            "observed_at": _value(probe.get("observed_at"), timestamp),
            "ttl_seconds": ttl_seconds,
            "model": {
                "model_id": observed["model_id"],
                "provider": observed["provider"],
                "reasoning_mode": observed["reasoning_mode"],
                "confidence": observed["confidence"],
            },
            "session_identity": session_identity,
        },
        "freshness_evaluated_at": evaluation_time,
        "observation_age_seconds": observation_age,
        "observation_ttl_seconds": ttl_seconds,
        "mismatch": {
            "status": mismatch_status,
            "handling": mismatch_handling,
            "details": mismatch_details,
        },
        "history_binding": history_binding,
        "history_invalidation_reasons": invalidation_reasons,
        "history_status": history_status,
        "evidence_status": evidence_status,
        "role_eligibility": {
            "implementer": role_status,
            "final_reviewer": role_status,
        },
    }


def model_evidence_score(identity: dict[str, Any]) -> float:
    score = {
        "strong": 1.0,
        "degraded": 0.45,
        "unknown": 0.25,
        "invalid": 0.0,
    }.get(str(identity.get("evidence_status") or "unknown"), 0.25)
    history_status = str(identity.get("history_status") or "downgraded")
    if history_status == "invalidated":
        return 0.0
    if history_status == "downgraded":
        return min(score, 0.25)
    return score


def model_selection_for(identity: dict[str, Any]) -> str:
    status = str(identity.get("evidence_status") or "unknown")
    if status == "unknown":
        return "unknown"
    if (identity.get("mismatch") or {}).get("status") == "mismatch":
        return "runtime_observed"
    return "observed_model"


def model_awareness_for(
    providers: dict[str, dict[str, Any]],
    *,
    dynamic_discovery_required: bool = True,
) -> dict[str, Any]:
    statuses = [str((record.get("model_identity") or {}).get("evidence_status") or "unknown") for record in providers.values()]
    if any(status == "invalid" for status in statuses):
        status = "invalid"
    elif any(status != "strong" for status in statuses):
        status = "degraded"
    else:
        status = "strong"
    return {
        "required": True,
        "dynamic_discovery_required": dynamic_discovery_required,
        "status": status,
        "provider_count": len(providers),
        "unknown_providers": [
            name for name, record in providers.items()
            if (record.get("model_identity") or {}).get("evidence_status") == "unknown"
        ],
    }


def model_aware_provider_errors(matrix: dict[str, Any]) -> list[str]:
    awareness = matrix.get("model_awareness") or {}
    if awareness.get("required") is not True:
        return []
    dynamic_required = awareness.get("dynamic_discovery_required") is True
    providers = matrix.get("providers")
    if isinstance(providers, list):
        providers = {
            str(record.get("provider_name") or index): record
            for index, record in enumerate(providers)
            if isinstance(record, dict)
        }
    if not isinstance(providers, dict) or not providers:
        return ["model-aware provider matrix has no provider records"]
    errors: list[str] = []
    for name, record in providers.items():
        if record.get("model_selection") == "runtime_default":
            errors.append(f"{name}: runtime_default is not model-aware evidence")
        identity = record.get("model_identity")
        if not isinstance(identity, dict):
            errors.append(f"{name}: missing model_identity")
            continue
        status = identity.get("evidence_status")
        observed = identity.get("observed_model") or {}
        probe = identity.get("model_probe") if dynamic_required else {}
        if dynamic_required:
            if not isinstance(probe, dict) or probe.get("schema_version") != "valp-model-probe.v1":
                errors.append(f"{name}: missing dynamic model probe evidence")
                probe = {}
            probe_status = probe.get("status")
            if probe_status not in {"observed", "unsupported", "unavailable", "error"}:
                errors.append(f"{name}: invalid dynamic model probe status")
            ttl_seconds = probe.get("ttl_seconds")
            if type(ttl_seconds) is not int or not MIN_OBSERVATION_TTL_SECONDS <= ttl_seconds <= MAX_OBSERVATION_TTL_SECONDS:
                errors.append(f"{name}: model observation TTL is outside protocol bounds")
            else:
                computed_freshness, _age = observation_freshness(
                    observed.get("timestamp"),
                    evaluated_at=_now(),
                    ttl_seconds=ttl_seconds,
                )
                if probe_status != "observed":
                    computed_freshness = "unknown"
                if observed.get("freshness") != computed_freshness:
                    errors.append(f"{name}: recorded model freshness does not match TTL evaluation")
        probe_status = probe.get("status") if isinstance(probe, dict) else None
        session = probe.get("session_identity") if isinstance(probe, dict) and isinstance(probe.get("session_identity"), dict) else {}
        if dynamic_required:
            probe_model = probe.get("model") if isinstance(probe.get("model"), dict) else {}
            for key in ("model_id", "provider", "reasoning_mode", "confidence"):
                if probe_model.get(key) != observed.get(key):
                    errors.append(f"{name}: model probe {key} does not match evaluated observation")
            if probe.get("observed_at") != observed.get("timestamp"):
                errors.append(f"{name}: model probe timestamp does not match evaluated observation")

            binding = identity.get("history_binding")
            expected_binding = {
                "agent_surface": identity.get("agent_surface"),
                "model_id": observed.get("model_id"),
                "provider": observed.get("provider"),
                "reasoning_mode": observed.get("reasoning_mode"),
                "freshness": observed.get("freshness"),
                "session_token": session.get("token"),
            }
            expected_fingerprint = hashlib.sha256(
                json.dumps(expected_binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if not isinstance(binding, dict) or any(binding.get(key) != value for key, value in expected_binding.items()):
                errors.append(f"{name}: model-bound history binding does not match active identity")
            elif binding.get("fingerprint") != expected_fingerprint:
                errors.append(f"{name}: model-bound history binding fingerprint is invalid")
            invalidation_reasons = identity.get("history_invalidation_reasons")
            if not isinstance(invalidation_reasons, list):
                errors.append(f"{name}: history invalidation reasons are missing")
            elif invalidation_reasons and identity.get("history_status") != "invalidated":
                errors.append(f"{name}: model-bound history invalidation was not enforced")
            if observed.get("freshness") != "current" and identity.get("history_status") != "invalidated":
                errors.append(f"{name}: non-current observation did not invalidate capability history")

            expected_role_status = (
                "eligible"
                if (
                    probe_status == "observed"
                    and observed.get("model_id") not in {None, "", UNKNOWN_MODEL}
                    and observed.get("freshness") == "current"
                    and session.get("status") == "known"
                    and session.get("token") not in {None, "", UNKNOWN_MODEL}
                )
                else "blocked"
            )
            role_eligibility = identity.get("role_eligibility") or {}
            if (
                role_eligibility.get("implementer") != expected_role_status
                or role_eligibility.get("final_reviewer") != expected_role_status
            ):
                errors.append(f"{name}: high-risk role eligibility is inconsistent with active model evidence")
        if status == "strong" and (
            observed.get("model_id") == UNKNOWN_MODEL
            or observed.get("confidence") != "high"
            or observed.get("freshness") != "current"
            or (identity.get("mismatch") or {}).get("status") != "match"
            or (dynamic_required and probe_status != "observed")
            or (dynamic_required and session.get("status") != "known")
            or (dynamic_required and session.get("token") in {None, "", UNKNOWN_MODEL})
        ):
            errors.append(
                f"{name}: strong model evidence has unknown, stale, low-confidence, mismatched, or session-unbound identity"
            )
        if (identity.get("mismatch") or {}).get("status") == "mismatch" and identity.get("history_status") != "invalidated":
            errors.append(f"{name}: model mismatch did not invalidate capability history")
    if awareness.get("status") == "strong" and any(
        (record.get("model_identity") or {}).get("evidence_status") != "strong"
        for record in providers.values()
    ):
        errors.append("model_awareness status strong is inconsistent with provider evidence")
    return errors


def model_aware_role_errors(
    matrix: dict[str, Any],
    role_assignments: dict[str, Any] | None,
) -> list[str]:
    awareness = matrix.get("model_awareness") or {}
    if awareness.get("dynamic_discovery_required") is not True:
        return []
    providers = matrix.get("providers") or {}
    if isinstance(providers, list):
        providers = {
            str(record.get("provider_name") or index): record
            for index, record in enumerate(providers)
            if isinstance(record, dict)
        }
    if not isinstance(providers, dict):
        return []
    errors: list[str] = []
    for role, eligibility_key in (("implementer", "implementer"), ("reviewer", "final_reviewer")):
        agent = str((role_assignments or {}).get(role) or "")
        if not agent:
            continue
        record = providers.get(agent)
        identity = record.get("model_identity") if isinstance(record, dict) else {}
        observed = identity.get("observed_model") if isinstance(identity, dict) else {}
        probe = identity.get("model_probe") if isinstance(identity, dict) else {}
        session = probe.get("session_identity") if isinstance(probe, dict) else {}
        recorded_eligibility = (
            (identity.get("role_eligibility") or {}).get(eligibility_key)
            if isinstance(identity, dict)
            else None
        )
        eligible = (
            isinstance(observed, dict)
            and observed.get("model_id") not in {None, "", UNKNOWN_MODEL}
            and observed.get("freshness") == "current"
            and isinstance(probe, dict)
            and probe.get("status") == "observed"
            and isinstance(session, dict)
            and session.get("status") == "known"
            and session.get("token") not in {None, "", UNKNOWN_MODEL}
            and recorded_eligibility == "eligible"
        )
        if not eligible:
            errors.append(f"{role}:{agent} lacks an observed, current, session-bound active model identity")
    return errors
