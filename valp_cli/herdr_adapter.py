from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable


RunCommand = Callable[..., dict[str, Any]]


class HerdrSubmissionError(RuntimeError):
    pass


def detect_herdr_submission_capability(
    herdr: str,
    run_command: RunCommand,
) -> dict[str, Any]:
    agent_help = run_command([herdr, "agent", "--help"], timeout=5.0)
    pane_help = run_command([herdr, "pane", "--help"], timeout=5.0)
    agent_text = _command_text(agent_help)
    pane_text = _command_text(pane_help)

    atomic_prompt = agent_help.get("ok") is True and _help_has_command(agent_text, "herdr agent prompt")
    pane_send_text = pane_help.get("ok") is True and _help_has_command(pane_text, "herdr pane send-text")
    pane_send_keys = pane_help.get("ok") is True and _help_has_command(pane_text, "herdr pane send-keys")
    agent_wait = agent_help.get("ok") is True and _help_has_command(agent_text, "herdr agent wait")
    if atomic_prompt:
        mode = "agent_prompt"
        status = "pass"
        message = "HERDR exposes atomic agent prompt submission."
    elif pane_send_text and pane_send_keys and agent_wait:
        mode = "pane_send_text_enter"
        status = "pass"
        message = "HERDR exposes pane insertion, Enter, and agent working-state proof."
    else:
        mode = "unavailable"
        status = "fail"
        fallback_missing = [
            command
            for command, available in (
                ("herdr pane send-text", pane_send_text),
                ("herdr pane send-keys", pane_send_keys),
                ("herdr agent wait", agent_wait),
            )
            if not available
        ]
        message = (
            "HERDR cannot submit a VALP dispatch: atomic command `herdr agent prompt` "
            "is unavailable and the fallback is missing "
            + ", ".join(f"`{command}`" for command in fallback_missing)
            + ". Install a HERDR build with one supported submission path or use Manual Mode."
        )
    return {
        "schema_version": "valp-herdr-submission-capability.v1",
        "status": status,
        "mode": mode,
        "message": message,
        "commands": {
            "agent_prompt": atomic_prompt,
            "pane_send_text": pane_send_text,
            "pane_send_keys": pane_send_keys,
            "agent_wait": agent_wait,
        },
        "probe_exit_codes": {
            "agent_help": agent_help.get("exit_code"),
            "pane_help": pane_help.get("exit_code"),
        },
    }


def describe_herdr_submission(
    capability: dict[str, Any],
    target: str,
    dispatch_ref: str,
) -> str:
    mode = str(capability.get("mode") or "unavailable")
    return (
        f"VALP packaged HERDR adapter: mode={mode}; target={target}; "
        f"dispatch={dispatch_ref}; completion still requires expected evidence"
    )


def submit_herdr_dispatch(
    herdr: str,
    capability: dict[str, Any],
    *,
    task_id: str,
    target: str,
    pane_id: str,
    dispatch_path: Path,
    run_command: RunCommand,
    proof_seconds: float | None,
) -> dict[str, Any]:
    mode = str(capability.get("mode") or "unavailable")
    if capability.get("status") != "pass" or mode == "unavailable":
        raise HerdrSubmissionError(str(capability.get("message") or "HERDR submission is unavailable"))
    content = dispatch_path.read_text(encoding="utf-8")
    payload = (
        f"[HERDR LOOP DISPATCH {task_id} -> {target}]\n"
        f"{content.rstrip()}\n"
        "[/HERDR LOOP DISPATCH]\n"
    )
    payload_digest = "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
    timeout_seconds = 10.0 if proof_seconds is None else proof_seconds
    runtime_target = pane_id.strip()
    if not runtime_target:
        raise HerdrSubmissionError(f"HERDR submission has no addressable pane for agent {target}")

    if mode == "agent_prompt":
        result = run_command(
            [herdr, "agent", "prompt", runtime_target, payload],
            timeout=max(5.0, timeout_seconds + 1.0),
        )
        _require_success(result, "atomic HERDR agent prompt")
        runtime_identity = _runtime_identity(result)
        if not _has_runtime_identity(runtime_identity):
            raise HerdrSubmissionError(
                "atomic HERDR agent prompt returned success without a runtime identity"
            )
        return {
            "runtime": "HERDR",
            "adapter": "VALP packaged HERDR adapter",
            "transport_mode": mode,
            "pane_id": pane_id,
            "agent_ref": target,
            "runtime_target": runtime_target,
            "payload_digest": payload_digest,
            "runtime_response": runtime_identity,
        }

    inserted = run_command(
        [herdr, "pane", "send-text", pane_id, payload],
        timeout=5.0,
    )
    _require_success(inserted, "HERDR pane text insertion")
    enter_command = [herdr, "pane", "send-keys", pane_id, "Enter"]
    entered = run_command(enter_command, timeout=5.0)
    _require_success(entered, "HERDR pane Enter submission")
    enter_attempts = 1
    first_wait_seconds = min(1.0, timeout_seconds / 2.0) if timeout_seconds > 0 else 0.0
    first_timeout_ms = max(0, int(first_wait_seconds * 1000))
    working = run_command(
        [herdr, "agent", "wait", runtime_target, "--status", "working", "--timeout", str(first_timeout_ms)],
        timeout=max(5.0, first_wait_seconds + 1.0),
    )
    if (
        working.get("ok") is not True
        and timeout_seconds > first_wait_seconds
        and _is_timeout_failure(working)
    ):
        entered = run_command(enter_command, timeout=5.0)
        _require_success(entered, "HERDR pane Enter retry")
        enter_attempts = 2
        remaining_seconds = timeout_seconds - first_wait_seconds
        remaining_timeout_ms = max(0, int(remaining_seconds * 1000))
        working = run_command(
            [
                herdr,
                "agent",
                "wait",
                runtime_target,
                "--status",
                "working",
                "--timeout",
                str(remaining_timeout_ms),
            ],
            timeout=max(5.0, remaining_seconds + 1.0),
        )
    _require_success(working, "HERDR agent working-state proof")
    return {
        "runtime": "HERDR",
        "adapter": "VALP packaged HERDR adapter",
        "transport_mode": mode,
        "pane_id": pane_id,
        "agent_ref": target,
        "runtime_target": runtime_target,
        "payload_digest": payload_digest,
        "status_proof": {
            "status": "working",
            "enter_attempts": enter_attempts,
            "working_attempt": enter_attempts,
            "runtime_response": _runtime_identity(working),
        },
    }


def _command_text(result: dict[str, Any]) -> str:
    return "\n".join((str(result.get("stdout") or ""), str(result.get("stderr") or ""))).lower()


def _help_has_command(help_text: str, command: str) -> bool:
    pattern = rf"(?m)^\s*{re.escape(command.lower())}(?:\s|$)"
    return re.search(pattern, help_text) is not None


def _require_success(result: dict[str, Any], action: str) -> None:
    if result.get("ok") is True:
        return
    detail = str(result.get("stderr") or result.get("stdout") or "unknown runtime failure").strip()
    raise HerdrSubmissionError(f"{action} failed: {detail}")


def _is_timeout_failure(result: dict[str, Any]) -> bool:
    detail = _command_text(result)
    return "timed out" in detail or "timeout" in detail


def _runtime_identity(result: dict[str, Any]) -> dict[str, str]:
    try:
        payload = json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError:
        payload = {}
    identities: dict[str, str] = {}

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for raw_key, child in value.items():
                key = str(raw_key)
                if (
                    (key == "id" or key.endswith("_id") or key.endswith("_ref"))
                    and isinstance(child, (str, int))
                    and str(child).strip()
                ):
                    identities.setdefault(key, str(child).strip())
                elif key == "status" and isinstance(child, str) and child.strip():
                    identities.setdefault(key, child.strip())
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(payload)
    return identities


def _has_runtime_identity(identity: dict[str, str]) -> bool:
    return any(
        key == "id" or key.endswith("_id") or key.endswith("_ref")
        for key in identity
    )
