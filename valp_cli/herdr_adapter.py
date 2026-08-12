from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


RunCommand = Callable[..., dict[str, Any]]
HERDR_PANE_LIST_STDOUT_LIMIT = 200_000
DONE_SESSION_REPROVISION_REQUIRED = (
    "HERDR task-owned done session requires an explicit fenced reprovision"
)


class HerdrSubmissionError(RuntimeError):
    pass


class HerdrAutoVisibleWatcher:
    """Persisted HERDR intake that publishes each source event at most once."""

    _SOURCES = {
        "issue_watcher",
        "queue_watcher",
        "schedule",
        "file_watcher",
        "runtime_api",
    }
    _ACTIONS = {
        "no_valp",
        "publish_only",
        "validate_declared_route",
        "validate_declared_route_and_dispatch",
        "block_for_approval",
    }

    def __init__(
        self,
        workspace: Path,
        publish: Callable[[dict[str, Any]], dict[str, Any]],
    ):
        self.workspace = Path(workspace).resolve()
        self.publish = publish
        self.records_directory = (
            self.workspace / ".herdr-loop" / "watcher-events" / "herdr"
        )

    @staticmethod
    def _identity(event: dict[str, Any]) -> str:
        canonical = json.dumps(
            {
                "source": event["source"],
                "source_event_id": event["source_event_id"],
                "rule_ref": event["rule_ref"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _write_once(path: Path, value: dict[str, Any]) -> None:
        encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        if path.exists():
            if path.read_text(encoding="utf-8") != encoded:
                raise HerdrSubmissionError("HERDR watcher record conflicts with source identity")
            return
        temporary = path.with_name(path.name + ".tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_text(encoding="utf-8") != encoded:
                    raise HerdrSubmissionError(
                        "HERDR watcher record conflicts with source identity"
                    )
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def _identity_lock(self, record_path: Path) -> Iterator[None]:
        lock_path = record_path.with_name(record_path.name + ".lock")
        deadline = time.monotonic() + 5.0
        while True:
            try:
                lock_path.mkdir()
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise HerdrSubmissionError(
                        "HERDR watcher source identity is locked or indeterminate"
                    )
                time.sleep(0.01)
        try:
            yield
        finally:
            lock_path.rmdir()

    def process(self, event: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise HerdrSubmissionError("HERDR watcher event must be an object")
        required = {
            "source",
            "source_event_id",
            "matched_signal",
            "rule_ref",
            "risk_classification",
            "selected_action",
        }
        allowed = required | {"payload"}
        if not required.issubset(event) or not set(event).issubset(allowed) or not all(
            isinstance(event[field], str) and event[field].strip()
            for field in required
        ):
            raise HerdrSubmissionError("HERDR watcher event fields are invalid")
        if "payload" in event and not isinstance(event["payload"], dict):
            raise HerdrSubmissionError("HERDR watcher event payload must be an object")
        if event["source"] not in self._SOURCES:
            raise HerdrSubmissionError("HERDR watcher source is unsupported")
        if event["risk_classification"] not in {"low", "medium", "high"}:
            raise HerdrSubmissionError("HERDR watcher risk classification is invalid")
        if event["selected_action"] not in self._ACTIONS:
            raise HerdrSubmissionError("HERDR watcher selected action is invalid")

        identity = self._identity(event)
        self.records_directory.mkdir(parents=True, exist_ok=True)
        record_path = self.records_directory / (identity.removeprefix("sha256:") + ".json")
        with self._identity_lock(record_path):
            return self._process_locked(event, identity, record_path)

    def _process_locked(
        self,
        event: dict[str, Any],
        identity: str,
        record_path: Path,
    ) -> dict[str, Any]:
        if record_path.is_file():
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if record.get("source_event") != event:
                raise HerdrSubmissionError("HERDR watcher duplicate source event conflicts")
            return record["result"]

        approval_required = event["risk_classification"] == "high"
        selected_action = (
            "block_for_approval" if approval_required else event["selected_action"]
        )
        publication = self.publish(
            {
                **event,
                "deduplication_identity": identity,
                "approval_required": approval_required,
                "selected_action": selected_action,
            }
        )
        task_id = str(publication.get("task_id") or "").strip()
        task_directory_value = publication.get("task_directory")
        task_directory = Path(task_directory_value) if task_directory_value else None
        if not task_id or task_directory is None or not task_directory.is_dir():
            raise HerdrSubmissionError("HERDR watcher publish result lacks a task directory")
        trigger = {
            "schema_version": "valp-trigger-policy.v1",
            "task_id": task_id,
            "trigger_mode": "watcher",
            "trigger_source": event["source"],
            "source_event_id": event["source_event_id"],
            "matched_signal": event["matched_signal"],
            "rule_ref": event["rule_ref"],
            "risk_classification": event["risk_classification"],
            "selected_action": selected_action,
            "approval_required": approval_required,
            "deduplication_identity": identity,
        }
        self._write_once(task_directory / "trigger-policy.json", trigger)
        result = {
            "task_id": task_id,
            "selected_action": selected_action,
            "approval_required": approval_required,
            "deduplication_identity": identity,
            "trigger_policy_ref": "trigger-policy.json",
        }
        self._write_once(record_path, {
            "schema_version": "valp-herdr-auto-visible-watcher-record.v1",
            "source_event": event,
            "result": result,
        })
        return result


def binding_has_verified_bootstrap_lifecycle(binding: dict[str, Any] | None) -> bool:
    if not isinstance(binding, dict) or binding.get("lifecycle") != "bootstrap_ready":
        return False
    verification = binding.get("bootstrap_verification")
    identity = binding.get("runtime_identity")
    evidence_ref = Path(str((verification or {}).get("evidence_ref") or ""))
    return bool(
        isinstance(verification, dict)
        and isinstance(identity, dict)
        and verification.get("status") == "verified"
        and str(evidence_ref).strip()
        and not evidence_ref.is_absolute()
        and ".." not in evidence_ref.parts
        and verification.get("generation") == binding.get("generation")
        and str(verification.get("pane_id") or "").strip()
        == str(identity.get("pane_id") or "").strip()
        and str(verification.get("native_session_id") or "").strip()
        and verification.get("expected_response") == "BOOTSTRAP_READY"
        and verification.get("actual_response") == "BOOTSTRAP_READY"
        and "native_turn_error" in verification
        and verification.get("native_turn_error") is None
        and verification.get("session_identity_status") == "known"
        and verification.get("model_probe_status") == "observed"
        and "consumed_by_dispatch_receipt_id" not in verification
    )


def opaque_process_generation(process_group: str | int) -> str:
    value = str(process_group).strip()
    digest = hashlib.sha256(
        f"herdr-foreground-process-group\0{value}".encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def detect_herdr_session_provisioning_capability(
    herdr: str,
    run_command: RunCommand,
) -> dict[str, Any]:
    agent_help = run_command([herdr, "agent", "--help"], timeout=5.0)
    workspace_help = run_command([herdr, "workspace", "--help"], timeout=5.0)
    pane_help = run_command([herdr, "pane", "--help"], timeout=5.0)
    agent_start = agent_help.get("ok") is True and _help_has_command(
        _command_text(agent_help),
        "herdr agent start",
    )
    workspace_create = workspace_help.get("ok") is True and _help_has_command(
        _command_text(workspace_help),
        "herdr workspace create",
    )
    pane_move = pane_help.get("ok") is True and _help_has_command(
        _command_text(pane_help),
        "herdr pane move",
    )
    if not agent_start and _help_lists_subcommand(_command_text(agent_help), "start"):
        agent_start = _nested_help_has_usage(
            run_command([herdr, "agent", "start", "--help"], timeout=5.0),
            "herdr agent start",
        )
    if not workspace_create and _help_lists_subcommand(
        _command_text(workspace_help),
        "create",
    ):
        workspace_create = _nested_help_has_usage(
            run_command([herdr, "workspace", "create", "--help"], timeout=5.0),
            "herdr workspace create",
        )
    if not pane_move and _help_lists_subcommand(_command_text(pane_help), "move"):
        pane_move = _nested_help_has_usage(
            run_command([herdr, "pane", "move", "--help"], timeout=5.0),
            "herdr pane move",
        )
    available = agent_start and workspace_create and pane_move
    missing = [
        command
        for command, present in (
            ("herdr agent start", agent_start),
            ("herdr workspace create", workspace_create),
            ("herdr pane move", pane_move),
        )
        if not present
    ]
    return {
        "schema_version": "valp-herdr-session-provisioning-capability.v1",
        "status": "pass" if available else "fail",
        "mode": "isolated_workspace_agent_start" if available else "unavailable",
        "message": (
            "HERDR exposes workspace creation, Agent start, and pane isolation for project/task-owned sessions."
            if available
            else "HERDR cannot provision isolated project/task-owned Agent sessions: missing "
            + ", ".join(f"`{command}`" for command in missing)
            + "."
        ),
        "commands": {
            "agent_start": agent_start,
            "workspace_create": workspace_create,
            "pane_move": pane_move,
        },
        "probe_exit_codes": {
            "agent_help": agent_help.get("exit_code"),
            "workspace_help": workspace_help.get("exit_code"),
            "pane_help": pane_help.get("exit_code"),
        },
    }


def provision_herdr_agent_session(
    herdr: str,
    *,
    task_id: str,
    agent: str,
    project_root: Path,
    launch_argv: list[str],
    existing_binding: dict[str, Any] | None,
    run_command: RunCommand,
    allow_launch_argv_change: bool = False,
    allow_done_session_reprovision: bool = False,
    readiness_attempts: int = 5,
    readiness_interval_seconds: float = 0.2,
) -> dict[str, Any]:
    if (
        not task_id.strip()
        or not agent.strip()
        or not launch_argv
        or readiness_attempts < 1
        or readiness_interval_seconds < 0
    ):
        raise HerdrSubmissionError("HERDR session provisioning requires task, agent, and launch argv")
    root = project_root.resolve()
    owner_digest = hashlib.sha256(
        f"{root}\0{task_id}\0{agent}".encode("utf-8")
    ).hexdigest()
    safe_agent = re.sub(r"[^a-z0-9]+", "-", agent.lower()).strip("-")[:20] or "agent"
    session_name = f"valp-{owner_digest[:16]}-{safe_agent}"
    project_identity = f"sha256:{hashlib.sha256(str(root).encode('utf-8')).hexdigest()}"
    if existing_binding is not None:
        generation = existing_binding.get("generation")
        runtime_scope = existing_binding.get("runtime_scope") or {}
        recorded_launch = existing_binding.get("launch")
        recorded_launch_argv = (
            recorded_launch.get("argv")
            if isinstance(recorded_launch, dict)
            else None
        )
        valid_recorded_launch = bool(
            isinstance(recorded_launch_argv, list)
            and recorded_launch_argv
            and all(isinstance(item, str) and item for item in recorded_launch_argv)
        )
        launch_changed = recorded_launch != {"argv": launch_argv}
        expected_owner = {
            "scope": "task",
            "task_id": task_id,
            "project_identity": project_identity,
        }
        metadata_conflicts: list[str] = []
        if existing_binding.get("agent") != agent:
            metadata_conflicts.append("agent")
        if existing_binding.get("session_name") != session_name:
            metadata_conflicts.append("session_name")
        if existing_binding.get("ownership") != expected_owner:
            metadata_conflicts.append("ownership")
        if existing_binding.get("context") != {"cwd": str(root)}:
            metadata_conflicts.append("context")
        if not valid_recorded_launch:
            metadata_conflicts.append("launch")
        if existing_binding.get("focused_at_provisioning") is not False:
            metadata_conflicts.append("focused_at_provisioning")
        if not isinstance(generation, int) or int(generation) < 1:
            metadata_conflicts.append("generation")
        if runtime_scope.get("kind") != "workspace":
            metadata_conflicts.append("runtime_scope.kind")
        if runtime_scope.get("ownership") != "task":
            metadata_conflicts.append("runtime_scope.ownership")
        if runtime_scope.get("workspace_id") != (
            existing_binding.get("runtime_identity") or {}
        ).get("workspace_id"):
            metadata_conflicts.append("runtime_scope.workspace_id")
        if isinstance(generation, int) and generation >= 1:
            expected_scope_label = _session_workspace_label(
                task_id,
                safe_agent,
                generation,
            )
            if runtime_scope.get("label") != expected_scope_label:
                metadata_conflicts.append("runtime_scope.label")
        if metadata_conflicts:
            raise HerdrSubmissionError(
                "HERDR task-owned session binding metadata conflicts: "
                + ", ".join(metadata_conflicts)
            )
        recorded_identity = existing_binding.get("runtime_identity") or {}
        pane_id = str(recorded_identity.get("pane_id") or "").strip()
        workspace_id = str(recorded_identity.get("workspace_id") or "").strip()
        panes_command = [herdr, "pane", "list"]
        if workspace_id:
            panes_command.extend(["--workspace", workspace_id])
        panes_result = run_command(
            panes_command,
            timeout=5.0,
            stdout_limit=HERDR_PANE_LIST_STDOUT_LIMIT,
        )
        if panes_result.get("ok") is True:
            panes_payload = _json_stdout(panes_result)
        else:
            try:
                error_payload = json.loads(str(panes_result.get("stderr") or ""))
            except (TypeError, ValueError):
                error_payload = {}
            panes_payload = error_payload if isinstance(error_payload, dict) else {}
        lookup_error = panes_payload.get("error")
        workspace_absent = bool(
            workspace_id
            and isinstance(lookup_error, dict)
            and lookup_error.get("code") == "workspace_not_found"
        )
        if not workspace_absent:
            _require_success(panes_result, "HERDR task-owned Agent session lookup")
        panes = (
            []
            if workspace_absent
            else ((panes_payload.get("result") or {}).get("panes") or [])
        )
        matching_pane = next(
            (
                pane
                for pane in panes
                if isinstance(pane, dict) and str(pane.get("pane_id") or "") == pane_id
            ),
            None,
        )
        if matching_pane is not None:
            if launch_changed and allow_launch_argv_change:
                raise HerdrSubmissionError(
                    "HERDR cannot replace task-owned session launch argv while the bound session is present"
                )
            runtime_identity = {
                key: str(matching_pane.get(key) or "").strip()
                for key in ("pane_id", "terminal_id", "workspace_id", "tab_id")
            }
            identity_digest = hashlib.sha256(
                json.dumps(runtime_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            expected_identity = {
                **runtime_identity,
                "token": f"sha256:{identity_digest}",
            }
            reported_cwd = str(matching_pane.get("cwd") or "").strip()
            reported_agent = str(matching_pane.get("agent") or "").strip()
            # HERDR 0.8 pane metadata omits the Agent name/label; the bound
            # pane identity and matching Agent are the durable reuse proof.
            reported_label = _pane_session_label(matching_pane) or session_name
            if (
                expected_identity != recorded_identity
                or reported_label != session_name
                or not reported_cwd
                or Path(reported_cwd).resolve() != root
                or reported_agent != agent
            ):
                raise HerdrSubmissionError("HERDR task-owned session runtime identity conflicts")
            runtime_status = str(
                matching_pane.get("agent_status") or ""
            ).strip().lower()
            if (
                runtime_status in {"idle", "done"}
                and binding_has_verified_bootstrap_lifecycle(existing_binding)
            ):
                return dict(existing_binding)
            if runtime_status == "done":
                if not allow_done_session_reprovision:
                    raise HerdrSubmissionError(DONE_SESSION_REPROVISION_REQUIRED)
                close_result = run_command(
                    [herdr, "workspace", "close", workspace_id],
                    timeout=10.0,
                )
                _require_success(close_result, "HERDR task-owned done session fenced reprovision")
                verification = run_command(
                    [herdr, "pane", "list", "--workspace", workspace_id],
                    timeout=5.0,
                    stdout_limit=HERDR_PANE_LIST_STDOUT_LIMIT,
                )
                verification_payload = _json_stdout(verification)
                if verification.get("ok") is not True:
                    try:
                        verification_payload = json.loads(
                            str(verification.get("stderr") or "")
                        )
                    except (TypeError, ValueError):
                        verification_payload = {}
                verification_error = verification_payload.get("error") if isinstance(verification_payload, dict) else {}
                if verification.get("ok") is True or not (
                    isinstance(verification_error, dict)
                    and verification_error.get("code") == "workspace_not_found"
                ):
                    raise HerdrSubmissionError(
                        "HERDR fenced reprovision could not prove the done task workspace is closed"
                    )
                matching_pane = None
            elif runtime_status == "idle" and existing_binding.get("lifecycle") == "provisioned":
                return dict(existing_binding)
            else:
                return {
                    **existing_binding,
                    "lifecycle": "reused",
                    "dispatch_eligible": True,
                }
        if launch_changed and not allow_launch_argv_change:
            raise HerdrSubmissionError(
                "HERDR task-owned session launch argv changed while the bound session is absent"
            )

    generation = int((existing_binding or {}).get("generation") or 0) + 1
    workspace_label = _session_workspace_label(task_id, safe_agent, generation)
    workspace_result = run_command(
        [
            herdr,
            "workspace",
            "create",
            "--cwd",
            str(root),
            "--label",
            workspace_label,
            "--no-focus",
        ],
        timeout=10.0,
    )
    _require_success(workspace_result, "HERDR task-owned workspace provisioning")
    workspace_payload = _json_stdout(workspace_result)
    workspace_result_record = (
        workspace_payload.get("result")
        if isinstance(workspace_payload.get("result"), dict)
        else workspace_payload
    )
    workspace = (
        workspace_result_record.get("workspace")
        if isinstance(workspace_result_record.get("workspace"), dict)
        else workspace_result_record
    )
    workspace_id = str(workspace.get("workspace_id") or "").strip()
    reported_workspace_label = str(workspace.get("label") or "").strip()
    if not workspace_id or (
        reported_workspace_label and reported_workspace_label != workspace_label
    ):
        raise HerdrSubmissionError(
            "HERDR task-owned workspace provisioning returned an invalid identity"
        )

    agent_help = run_command([herdr, "agent", "--help"], timeout=5.0)
    agent_help_text = _command_text(agent_help)
    agent_start_available = agent_help.get("ok") is True and _help_has_command(
        agent_help_text,
        "herdr agent start",
    )
    if not agent_start_available and _help_lists_subcommand(agent_help_text, "start"):
        nested_agent_help = run_command(
            [herdr, "agent", "start", "--help"], timeout=5.0
        )
        agent_start_available = _nested_help_has_usage(
            nested_agent_help, "herdr agent start"
        )
        if agent_start_available:
            agent_help_text = _command_text(nested_agent_help)
    if not agent_start_available:
        raise HerdrSubmissionError(
            "HERDR cannot provision a project/task-owned Agent session: "
            "`herdr agent start` is unavailable"
        )
    initial_pane_id = ""
    if "--pane" in agent_help_text and "--kind" in agent_help_text:
        pane_list_result = run_command(
            [herdr, "pane", "list", "--workspace", workspace_id], timeout=5.0
        )
        _require_success(pane_list_result, "HERDR task-owned Agent pane discovery")
        pane_list_payload = _json_stdout(pane_list_result)
        pane_list_record = (
            pane_list_payload.get("result")
            if isinstance(pane_list_payload.get("result"), dict)
            else pane_list_payload
        )
        panes = pane_list_record.get("panes") if isinstance(pane_list_record, dict) else []
        candidate_panes = [
            pane for pane in panes or []
            if isinstance(pane, dict)
            and str(pane.get("workspace_id") or "") == workspace_id
            and not str(pane.get("agent") or "").strip()
        ]
        if len(candidate_panes) != 1:
            raise HerdrSubmissionError(
                "HERDR task-owned workspace did not expose exactly one empty pane"
            )
        initial_pane_id = str(candidate_panes[0].get("pane_id") or "").strip()
        if not initial_pane_id:
            raise HerdrSubmissionError(
                "HERDR task-owned workspace pane returned no identity"
            )
        requested_agent_args = launch_argv[1:]
        start_command = [
            herdr, "agent", "start", session_name,
            "--kind", agent, "--pane", initial_pane_id, "--timeout", "30000",
            "--", *requested_agent_args,
        ]
    else:
        requested_agent_args = launch_argv
        start_command = [
            herdr, "agent", "start", session_name,
            "--cwd", str(root), "--workspace", workspace_id, "--split", "down",
            "--no-focus", "--env", f"VALP_AGENT_BINDING_GENERATION={generation}",
            "--", *launch_argv,
        ]
    result: dict[str, Any] = {}
    for start_attempt in range(1, readiness_attempts + 1):
        result = run_command(start_command, timeout=40.0)
        if (
            result.get("ok") is not True
            and "agent_pane_busy" in _command_text(result)
            and start_attempt < readiness_attempts
        ):
            if readiness_interval_seconds:
                time.sleep(readiness_interval_seconds)
            continue
        break
    _require_success(result, "HERDR task-owned Agent session provisioning")
    payload = _json_stdout(result)
    started = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    runtime_agent = started.get("agent") if isinstance(started.get("agent"), dict) else {}
    actual_argv = list(started.get("argv") or [])
    argv_matches = actual_argv in (launch_argv, requested_agent_args) or (
        actual_argv
        and len(actual_argv) == len(launch_argv)
        and Path(str(actual_argv[0])).name == Path(str(launch_argv[0])).name
        and actual_argv[1:] == launch_argv[1:]
    )
    if started.get("type") != "agent_started" or not argv_matches:
        raise HerdrSubmissionError(
            "HERDR task-owned Agent session provisioning returned an invalid launch receipt"
        )
    initial_pane_id = str(runtime_agent.get("pane_id") or initial_pane_id).strip()
    if not initial_pane_id:
        raise HerdrSubmissionError(
            "HERDR task-owned Agent session provisioning returned no pane identity"
        )
    if "--pane" not in agent_help_text or "--kind" not in agent_help_text:
        move_result = run_command(
            [herdr, "pane", "move", initial_pane_id, "--new-tab", "--workspace", workspace_id,
             "--label", session_name, "--no-focus"],
            timeout=10.0,
        )
        _require_success(move_result, "HERDR task-owned Agent tab isolation")
    required_identity = ("pane_id", "terminal_id", "workspace_id", "tab_id")
    runtime_agent = {}
    last_status = "unknown"
    agent_identity_reported = False
    for attempt in range(1, readiness_attempts + 1):
        pane_result = run_command([herdr, "pane", "get", initial_pane_id], timeout=5.0)
        if pane_result.get("ok") is not True:
            last_status = "pane_query_failed"
        else:
            pane_payload = _json_stdout(pane_result)
            pane_result_record = (
                pane_payload.get("result")
                if isinstance(pane_payload.get("result"), dict)
                else pane_payload
            )
            observed_agent = (
                pane_result_record.get("pane")
                if isinstance(pane_result_record.get("pane"), dict)
                else pane_result_record
            )
            runtime_agent = observed_agent if isinstance(observed_agent, dict) else {}
            if "--pane" in agent_help_text and "--kind" in agent_help_text:
                agent_result = run_command(
                    [herdr, "agent", "get", initial_pane_id], timeout=5.0
                )
                if agent_result.get("ok") is True:
                    agent_payload = _json_stdout(agent_result)
                    agent_record = (
                        agent_payload.get("result")
                        if isinstance(agent_payload.get("result"), dict)
                        else agent_payload
                    )
                    agent_info = agent_record.get("agent") if isinstance(agent_record, dict) else {}
                    if isinstance(agent_info, dict):
                        for key in ("agent", "name", "label", "agent_status"):
                            if key in agent_info and key not in runtime_agent:
                                runtime_agent[key] = agent_info[key]
            missing_identity = any(
                not str(runtime_agent.get(key) or "").strip()
                for key in required_identity
            )
            reported_cwd = str(runtime_agent.get("cwd") or "").strip()
            reported_label = _pane_session_label(runtime_agent)
            reported_agent = str(runtime_agent.get("agent") or "").strip()
            if (
                missing_identity
                or not reported_cwd
                or not reported_label
                or runtime_agent.get("focused") is None
            ):
                last_status = "incomplete_identity"
            elif not reported_agent and not agent_identity_reported:
                report_result = run_command(
                    [
                        herdr,
                        "pane",
                        "report-agent",
                        initial_pane_id,
                        "--source",
                        f"valp-task-session-{owner_digest[:16]}-g{generation}",
                        "--agent",
                        agent,
                        "--state",
                        "unknown",
                        "--seq",
                        str(generation),
                    ],
                    timeout=5.0,
                )
                _require_success(
                    report_result,
                    "HERDR task-owned Agent identity reporting",
                )
                agent_identity_reported = True
                last_status = "agent_identity_reported"
            elif not reported_agent:
                last_status = "incomplete_identity"
            elif runtime_agent.get("focused") is True:
                raise HerdrSubmissionError(
                    "HERDR task-owned Agent session provisioning did not prove a non-focused pane"
                )
            elif runtime_agent.get("focused") is not False:
                last_status = "focus_unproven"
            elif Path(reported_cwd).resolve() != root:
                raise HerdrSubmissionError(
                    "HERDR task-owned Agent session provisioning escaped the project context"
                )
            elif (
                str(runtime_agent.get("workspace_id") or "").strip() != workspace_id
                or reported_label != session_name
                or reported_agent != agent
            ):
                raise HerdrSubmissionError(
                    "HERDR task-owned Agent tab isolation returned a conflicting runtime identity"
                )
            else:
                break
        if attempt < readiness_attempts and readiness_interval_seconds:
            time.sleep(readiness_interval_seconds)
    else:
        raise HerdrSubmissionError(
            "HERDR task-owned Agent readiness budget exhausted: "
            f"{readiness_attempts} attempt(s), last status {last_status}"
        )
    runtime_identity = {
        key: str(runtime_agent[key]).strip()
        for key in required_identity
    }
    identity_digest = hashlib.sha256(
        json.dumps(runtime_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    provisioned_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "agent": agent,
        "session_name": session_name,
        "generation": generation,
        "ownership": {
            "scope": "task",
            "task_id": task_id,
            "project_identity": project_identity,
        },
        "context": {"cwd": str(root)},
        "launch": {"argv": launch_argv},
        "focused_at_provisioning": False,
        "runtime_scope": {
            "kind": "workspace",
            "ownership": "task",
            "workspace_id": workspace_id,
            "label": workspace_label,
        },
        "runtime_identity": {
            **runtime_identity,
            "token": f"sha256:{identity_digest}",
        },
        "provisioned_at": provisioned_at,
        "lifecycle": "provisioned",
        "dispatch_eligible": True,
    }


def provision_herdr_leader_session(
    herdr: str,
    *,
    installation_id: str,
    principal_id: str,
    agent: str,
    workspace_root: Path,
    launch_argv: list[str],
    leader_epoch: int,
    generation: int,
    run_command: RunCommand,
    readiness_attempts: int = 5,
    readiness_interval_seconds: float = 0.2,
) -> dict[str, Any]:
    if (
        not installation_id.strip()
        or not principal_id.strip()
        or not agent.strip()
        or not launch_argv
        or leader_epoch < 1
        or generation < 1
        or readiness_attempts < 1
        or readiness_interval_seconds < 0
    ):
        raise HerdrSubmissionError(
            "HERDR Leader provisioning requires installation, principal, Agent, launch argv, epoch, and generation"
        )
    root = workspace_root.resolve()
    owner_digest = hashlib.sha256(
        f"{installation_id}\0{principal_id}".encode("utf-8")
    ).hexdigest()
    safe_agent = re.sub(r"[^a-z0-9]+", "-", agent.lower()).strip("-")[:20] or "agent"
    workspace_label = f"valp-leader-{owner_digest[:16]}-g{generation}"
    session_suffix = f"-g{generation}"
    session_base = f"valp-leader-{safe_agent}-{owner_digest[:12]}"
    session_name = f"{session_base[:32 - len(session_suffix)].rstrip('-')}{session_suffix}"
    caller_workspace_id = _focused_herdr_workspace_id(herdr, run_command)

    workspace_result = run_command(
        [
            herdr,
            "workspace",
            "create",
            "--cwd",
            str(root),
            "--label",
            workspace_label,
            "--no-focus",
        ],
        timeout=10.0,
    )
    _require_success(workspace_result, "HERDR installation-owned Leader workspace provisioning")
    workspace_payload = _json_stdout(workspace_result)
    workspace_result_record = (
        workspace_payload.get("result")
        if isinstance(workspace_payload.get("result"), dict)
        else workspace_payload
    )
    workspace = (
        workspace_result_record.get("workspace")
        if isinstance(workspace_result_record.get("workspace"), dict)
        else workspace_result_record
    )
    workspace_id = str(workspace.get("workspace_id") or "").strip()
    reported_workspace_label = str(workspace.get("label") or "").strip()
    if not workspace_id or (
        reported_workspace_label and reported_workspace_label != workspace_label
    ):
        raise HerdrSubmissionError(
            "HERDR installation-owned Leader workspace returned an invalid identity"
        )

    agent_help = run_command([herdr, "agent", "--help"], timeout=5.0)
    agent_help_text = _command_text(agent_help)
    agent_start_available = agent_help.get("ok") is True and _help_has_command(
        agent_help_text,
        "herdr agent start",
    )
    if not agent_start_available and _help_lists_subcommand(agent_help_text, "start"):
        nested_agent_help = run_command(
            [herdr, "agent", "start", "--help"],
            timeout=5.0,
        )
        agent_start_available = _nested_help_has_usage(
            nested_agent_help,
            "herdr agent start",
        )
        if agent_start_available:
            agent_help_text = _command_text(nested_agent_help)
    if not agent_start_available:
        raise HerdrSubmissionError(
            "HERDR cannot provision the installation-owned Leader: `herdr agent start` is unavailable"
        )
    pane_list_result = run_command(
        [herdr, "pane", "list", "--workspace", workspace_id], timeout=5.0
    )
    _require_success(pane_list_result, "HERDR Leader pane discovery")
    pane_list_payload = _json_stdout(pane_list_result)
    pane_list_record = (
        pane_list_payload.get("result")
        if isinstance(pane_list_payload.get("result"), dict)
        else pane_list_payload
    )
    panes = pane_list_record.get("panes") if isinstance(pane_list_record, dict) else []
    candidate_panes = [
        pane for pane in panes or []
        if isinstance(pane, dict)
        and str(pane.get("workspace_id") or "") == workspace_id
        and not str(pane.get("agent") or "").strip()
    ]
    if len(candidate_panes) != 1:
        raise HerdrSubmissionError(
            "HERDR installation-owned Leader workspace did not expose exactly one empty pane"
        )
    pane_id = str(candidate_panes[0].get("pane_id") or "").strip()
    if not pane_id:
        raise HerdrSubmissionError("HERDR installation-owned Leader pane returned no identity")
    if "--pane" in agent_help_text and "--kind" in agent_help_text:
        requested_agent_args = launch_argv[1:]
        start_command = [
            herdr, "agent", "start", session_name,
            "--kind", agent, "--pane", pane_id, "--timeout", "30000",
            "--", *requested_agent_args,
        ]
    else:
        requested_agent_args = launch_argv
        start_command = [
            herdr, "agent", "start", session_name,
            "--cwd", str(root), "--workspace", workspace_id, "--no-focus",
            "--", *launch_argv,
        ]
    start_result: dict[str, Any] = {}
    for start_attempt in range(1, readiness_attempts + 1):
        start_result = run_command(start_command, timeout=40.0)
        if (
            start_result.get("ok") is not True
            and "agent_pane_busy" in _command_text(start_result)
            and start_attempt < readiness_attempts
        ):
            if readiness_interval_seconds:
                time.sleep(readiness_interval_seconds)
            continue
        break
    reused_existing = False
    if start_result.get("ok") is not True and "agent_name_taken" in _command_text(start_result):
        existing_result = run_command([herdr, "agent", "get", session_name], timeout=5.0)
        _require_success(existing_result, "HERDR existing Leader session discovery")
        existing_payload = _json_stdout(existing_result)
        existing_record = (
            existing_payload.get("result")
            if isinstance(existing_payload.get("result"), dict)
            else existing_payload
        )
        runtime_agent = (
            existing_record.get("agent")
            if isinstance(existing_record.get("agent"), dict)
            else {}
        )
        workspace_id = str(runtime_agent.get("workspace_id") or "").strip()
        existing_workspace_result = run_command(
            [herdr, "workspace", "get", workspace_id],
            timeout=5.0,
        )
        _require_success(existing_workspace_result, "HERDR existing Leader workspace discovery")
        existing_workspace_payload = _json_stdout(existing_workspace_result)
        existing_workspace_record = (
            existing_workspace_payload.get("result")
            if isinstance(existing_workspace_payload.get("result"), dict)
            else existing_workspace_payload
        )
        existing_workspace = (
            existing_workspace_record.get("workspace")
            if isinstance(existing_workspace_record.get("workspace"), dict)
            else {}
        )
        if (
            str(runtime_agent.get("name") or "").strip() != session_name
            or str(runtime_agent.get("agent") or "").strip() != agent
            or str(runtime_agent.get("cwd") or "").strip() != str(root)
            or runtime_agent.get("focused") is not False
            or not workspace_id
            or str(existing_workspace.get("workspace_id") or "").strip() != workspace_id
            or str(existing_workspace.get("label") or "").strip() != workspace_label
            or existing_workspace.get("focused") is not False
        ):
            raise HerdrSubmissionError(
                "HERDR existing Leader name is owned by a conflicting runtime session"
            )
        reused_existing = True
    else:
        _require_success(start_result, "HERDR installation-owned Leader session provisioning")
        start_payload = _json_stdout(start_result)
        started = start_payload.get("result") if isinstance(start_payload.get("result"), dict) else start_payload
        runtime_agent = started.get("agent") if isinstance(started.get("agent"), dict) else {}
        actual_argv = list(started.get("argv") or [])
        argv_matches = actual_argv in (launch_argv, requested_agent_args) or (
            len(actual_argv) == len(launch_argv)
            and Path(str(actual_argv[0])).name == Path(str(launch_argv[0])).name
            and actual_argv[1:] == launch_argv[1:]
        )
        if started.get("type") != "agent_started" or not argv_matches:
            raise HerdrSubmissionError(
                "HERDR installation-owned Leader returned an invalid launch receipt"
            )
    pane_id = str(runtime_agent.get("pane_id") or "").strip()
    if not pane_id:
        raise HerdrSubmissionError(
            "HERDR installation-owned Leader returned no pane identity"
        )

    if not reused_existing:
        move_result = run_command(
            [
                herdr,
                "pane",
                "move",
                pane_id,
                "--new-tab",
                "--workspace",
                workspace_id,
                "--label",
                session_name,
                "--no-focus",
            ],
            timeout=10.0,
        )
        _require_success(move_result, "HERDR installation-owned Leader tab isolation")
        focus_result = run_command(
            [herdr, "workspace", "focus", caller_workspace_id], timeout=5.0
        )
        _require_success(
            focus_result,
            "HERDR installation-owned Leader caller-focus restoration",
        )
    required_identity = ("pane_id", "terminal_id", "workspace_id", "tab_id")
    observations: list[dict[str, Any]] = []
    pane: dict[str, Any] = {}
    process_group: str | int | None = None
    for attempt in range(1, readiness_attempts + 1):
        attempt_record: dict[str, Any] = {
            "attempt": attempt,
            "observed_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        pane_result = run_command([herdr, "pane", "get", pane_id], timeout=5.0)
        if pane_result.get("ok") is not True:
            attempt_record["status"] = "pane_query_failed"
        else:
            pane_payload = _json_stdout(pane_result)
            pane_result_record = (
                pane_payload.get("result")
                if isinstance(pane_payload.get("result"), dict)
                else pane_payload
            )
            observed_pane = (
                pane_result_record.get("pane")
                if isinstance(pane_result_record.get("pane"), dict)
                else pane_result_record
            )
            pane = observed_pane if isinstance(observed_pane, dict) else {}
            missing_identity = [
                key
                for key in required_identity
                if not str(pane.get(key) or "").strip()
            ]
            reported_cwd = str(pane.get("cwd") or "").strip()
            # HERDR 0.8 pane metadata omits the Agent name/label. The
            # installation-owned workspace and bound pane identity remain
            # independently validated below, so retain the requested name.
            reported_label = _pane_session_label(pane) or session_name
            reported_agent = str(pane.get("agent") or "").strip()
            if missing_identity or not reported_cwd or not reported_label or not reported_agent:
                attempt_record["status"] = "incomplete_identity"
            elif pane.get("focused") is True:
                raise HerdrSubmissionError(
                    "HERDR installation-owned Leader returned a focused pane"
                )
            elif pane.get("focused") is not False:
                attempt_record["status"] = "focus_unproven"
            elif Path(reported_cwd).resolve() != root:
                raise HerdrSubmissionError(
                    "HERDR installation-owned Leader escaped the requested context"
                )
            elif (
                str(pane.get("workspace_id") or "").strip() != workspace_id
                or reported_label != session_name
                or reported_agent != agent
            ):
                raise HerdrSubmissionError(
                    "HERDR installation-owned Leader returned a conflicting runtime identity"
                )
            else:
                process_result = run_command(
                    [herdr, "pane", "process-info", "--pane", pane_id],
                    timeout=5.0,
                )
                if process_result.get("ok") is not True:
                    attempt_record["status"] = "process_query_failed"
                else:
                    process_payload = _json_stdout(process_result)
                    process_info = (process_payload.get("result") or {}).get("process_info") or {}
                    process_group = process_info.get("foreground_process_group_id")
                    processes = process_info.get("foreground_processes")
                    matching_processes = [
                        process
                        for process in processes or []
                        if isinstance(process, dict)
                        and (
                            list(process.get("argv") or []) == launch_argv
                            or (
                                len(list(process.get("argv") or [])) == len(launch_argv)
                                and Path(str((process.get("argv") or [""])[0])).name
                                == Path(str(launch_argv[0])).name
                                and list(process.get("argv") or [])[1:] == launch_argv[1:]
                            )
                        )
                    ]
                    if (
                        not isinstance(process_group, (str, int))
                        or not str(process_group).strip()
                        or (
                            reused_existing
                            and (
                                len(matching_processes) != 1
                                or str(matching_processes[0].get("pid") or "").strip()
                                != str(process_group).strip()
                                or Path(str(matching_processes[0].get("cwd") or "")).resolve()
                                != root
                            )
                        )
                    ):
                        attempt_record["status"] = "process_identity_unproven"
                    else:
                        attempt_record["status"] = "pass"
                        observations.append(attempt_record)
                        break
        observations.append(attempt_record)
        if attempt < readiness_attempts and readiness_interval_seconds:
            time.sleep(readiness_interval_seconds)
    else:
        last_status = observations[-1]["status"] if observations else "unknown"
        raise HerdrSubmissionError(
            "HERDR installation-owned Leader readiness budget exhausted: "
            f"{readiness_attempts} attempt(s), last status {last_status}"
        )

    assert process_group is not None
    process_generation = opaque_process_generation(process_group)
    runtime_identity = {
        "session_id": str(pane["pane_id"]).strip(),
        **{key: str(pane[key]).strip() for key in required_identity},
        "process_generation": process_generation,
    }
    identity_digest = hashlib.sha256(
        json.dumps(runtime_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "adapter_id": "herdr",
        "adapter_class": "pane_controller",
        "principal_id": principal_id,
        "agent_id": agent,
        "generation": generation,
        "ownership": {
            "scope": "installation",
            "installation_id": installation_id,
        },
        "context": {"cwd": str(root)},
        "launch": {"argv": launch_argv},
        "focused_at_provisioning": False,
        "runtime_scope": {
            "kind": "workspace",
            "ownership": "installation",
            "workspace_id": workspace_id,
            "label": workspace_label,
        },
        "runtime_identity": {
            **runtime_identity,
            "token": f"sha256:{identity_digest}",
        },
        "health": {
            "status": "pass",
            "observed_at": observed_at,
            "evidence": {
                "agent_status": str(pane.get("agent_status") or "unknown"),
                "process_generation": process_generation,
                "readiness_attempts": len(observations),
                "readiness_policy": {
                    "maximum_attempts": readiness_attempts,
                    "interval_seconds": readiness_interval_seconds,
                    "per_attempt_timeout_seconds": 5.0,
                },
                "observations": observations,
            },
        },
        "provisioned_at": observed_at,
    }


def open_herdr_leader_session(
    herdr: str,
    binding: dict[str, Any],
    run_command: RunCommand,
) -> dict[str, Any]:
    """Open the current Leader attachment without adopting a new session.

    The installation Leader is the authority. A HERDR pane is only its
    replaceable presentation attachment, so callers from any workspace may
    focus the current attachment. A missing pane is reported to the caller so
    the control plane can provision a fenced replacement.
    """
    runtime_identity = binding.get("runtime_identity") if isinstance(binding.get("runtime_identity"), dict) else {}
    pane_id = str(runtime_identity.get("pane_id") or runtime_identity.get("session_id") or "").strip()
    workspace_id = str(runtime_identity.get("workspace_id") or "").strip()
    tab_id = str(runtime_identity.get("tab_id") or "").strip()
    if not pane_id or not workspace_id:
        raise HerdrSubmissionError("HERDR Leader attachment identity is incomplete")

    pane_result = run_command([herdr, "pane", "get", pane_id], timeout=5.0)
    if pane_result.get("ok") is not True:
        error_text = " ".join(
            str(pane_result.get(key) or "") for key in ("stdout", "stderr", "error")
        ).lower()
        if not any(marker in error_text for marker in ("pane_not_found", "pane not found", "unknown pane")):
            raise HerdrSubmissionError("HERDR Leader attachment probe failed before a pane-not-found result")
        return {
            "status": "missing",
            "action": "reprovision_required",
            "reason": "leader_attachment_not_found",
            "session_id": pane_id,
        }
    pane_payload = _json_stdout(pane_result)
    pane_record = pane_payload.get("result") if isinstance(pane_payload.get("result"), dict) else pane_payload
    pane = pane_record.get("pane") if isinstance(pane_record.get("pane"), dict) else pane_record
    if not isinstance(pane, dict) or str(pane.get("pane_id") or "").strip() != pane_id:
        return {
            "status": "missing",
            "action": "reprovision_required",
            "reason": "leader_attachment_identity_changed",
            "session_id": pane_id,
        }
    if str(pane.get("workspace_id") or "").strip() != workspace_id:
        raise HerdrSubmissionError("HERDR Leader attachment workspace identity changed")
    expected_agent = str(binding.get("agent_id") or "").strip()
    observed_agent = str(pane.get("agent") or "").strip()
    if expected_agent and observed_agent and expected_agent != observed_agent:
        raise HerdrSubmissionError("HERDR Leader attachment Agent identity changed")

    agent_help = run_command([herdr, "agent", "--help"], timeout=5.0)
    if agent_help.get("ok") is True and _help_has_command(
        _command_text(agent_help),
        "herdr agent focus",
    ):
        focus_result = run_command([herdr, "agent", "focus", pane_id], timeout=5.0)
    elif tab_id:
        focus_result = run_command([herdr, "tab", "focus", tab_id], timeout=5.0)
    else:
        raise HerdrSubmissionError("HERDR cannot open the existing Leader attachment: no supported focus target is available")
    _require_success(focus_result, "HERDR Leader attachment focus")
    return {
        "status": "opened",
        "action": "focused_existing_attachment",
        "session_id": pane_id,
        "workspace_id": workspace_id,
        "binding_digest": binding.get("binding_digest"),
    }


def recover_herdr_leader_session(
    herdr: str,
    *,
    installation_id: str,
    principal_id: str,
    agent: str,
    workspace_root: Path,
    launch_argv: list[str],
    leader_epoch: int,
    generation: int,
    session_id: str,
    recovery_approval: dict[str, Any],
    run_command: RunCommand,
    readiness_attempts: int = 5,
    readiness_interval_seconds: float = 0.2,
) -> dict[str, Any]:
    approved_session_id = str(
        recovery_approval.get("approved_session_id") or ""
    ).strip()
    if (
        not installation_id.strip()
        or not principal_id.strip()
        or not agent.strip()
        or not launch_argv
        or not all(isinstance(item, str) and item.strip() for item in launch_argv)
        or leader_epoch != 1
        or generation != 1
        or not session_id.strip()
        or session_id != session_id.strip()
        or approved_session_id != session_id
        or not str(recovery_approval.get("approval_event_id") or "").strip()
        or not str(recovery_approval.get("failed_receipt_id") or "").strip()
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(recovery_approval.get("failed_receipt_digest") or ""),
        )
        or readiness_attempts < 1
        or readiness_interval_seconds < 0
    ):
        raise HerdrSubmissionError(
            "HERDR Leader recovery requires one approved failed-start session, receipt, epoch, generation, and launch contract"
        )

    root = workspace_root.resolve()
    owner_digest = hashlib.sha256(
        f"{installation_id}\0{principal_id}".encode("utf-8")
    ).hexdigest()
    safe_agent = re.sub(r"[^a-z0-9]+", "-", agent.lower()).strip("-")[:20] or "agent"
    expected_workspace_label = f"valp-leader-{owner_digest[:16]}-g{generation}"
    expected_session_label = f"valp-leader-{safe_agent}-{owner_digest[:12]}-g{generation}"
    required_identity = ("pane_id", "terminal_id", "workspace_id", "tab_id")
    observations: list[dict[str, Any]] = []
    pane: dict[str, Any] = {}
    workspace: dict[str, Any] = {}
    process_generation: str | None = None

    for attempt in range(1, readiness_attempts + 1):
        attempt_record: dict[str, Any] = {
            "attempt": attempt,
            "observed_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        pane_result = run_command(
            [herdr, "pane", "get", session_id],
            timeout=5.0,
        )
        if pane_result.get("ok") is not True:
            attempt_record["status"] = "pane_query_failed"
            observations.append(attempt_record)
        else:
            pane_payload = _json_stdout(pane_result)
            pane_result_record = (
                pane_payload.get("result")
                if isinstance(pane_payload.get("result"), dict)
                else pane_payload
            )
            observed_pane = (
                pane_result_record.get("pane")
                if isinstance(pane_result_record.get("pane"), dict)
                else pane_result_record
            )
            pane = observed_pane if isinstance(observed_pane, dict) else {}
            agent_result = run_command([herdr, "agent", "get", session_id], timeout=5.0)
            if agent_result.get("ok") is True:
                agent_payload = _json_stdout(agent_result)
                agent_record = (
                    agent_payload.get("result")
                    if isinstance(agent_payload.get("result"), dict)
                    else agent_payload
                )
                agent_info = agent_record.get("agent") if isinstance(agent_record, dict) else {}
                if isinstance(agent_info, dict):
                    for key in ("agent", "name", "label", "agent_status"):
                        if key in agent_info and key not in pane:
                            pane[key] = agent_info[key]
            missing_identity = [
                key for key in required_identity if not str(pane.get(key) or "").strip()
            ]
            if missing_identity or not _pane_session_label(pane):
                attempt_record["status"] = "incomplete_pane_identity"
                observations.append(attempt_record)
            else:
                reported_cwd = str(pane.get("cwd") or "").strip()
                foreground_cwd = str(pane.get("foreground_cwd") or reported_cwd).strip()
                workspace_id = str(pane.get("workspace_id") or "").strip()
                if str(pane.get("pane_id") or "").strip() != session_id:
                    raise HerdrSubmissionError(
                        "HERDR Leader recovery returned a different runtime session"
                    )
                if pane.get("focused") is not False:
                    raise HerdrSubmissionError(
                        "HERDR Leader recovery did not prove a non-focused pane"
                    )
                if (
                    not reported_cwd
                    or Path(reported_cwd).resolve() != root
                    or not foreground_cwd
                    or Path(foreground_cwd).resolve() != root
                ):
                    raise HerdrSubmissionError(
                        "HERDR Leader recovery session escaped the requested context"
                    )
                if (
                    _pane_session_label(pane) != expected_session_label
                    or str(pane.get("agent") or "").strip() != agent
                ):
                    raise HerdrSubmissionError(
                        "HERDR Leader recovery returned a conflicting pane identity"
                    )

                workspace_result = run_command(
                    [herdr, "workspace", "get", workspace_id],
                    timeout=5.0,
                )
                if workspace_result.get("ok") is not True:
                    attempt_record["status"] = "workspace_query_failed"
                    observations.append(attempt_record)
                else:
                    workspace_payload = _json_stdout(workspace_result)
                    workspace_result_record = (
                        workspace_payload.get("result")
                        if isinstance(workspace_payload.get("result"), dict)
                        else workspace_payload
                    )
                    observed_workspace = (
                        workspace_result_record.get("workspace")
                        if isinstance(workspace_result_record.get("workspace"), dict)
                        else workspace_result_record
                    )
                    workspace = (
                        observed_workspace
                        if isinstance(observed_workspace, dict)
                        else {}
                    )
                    if (
                        not str(workspace.get("workspace_id") or "").strip()
                        or not str(workspace.get("label") or "").strip()
                    ):
                        attempt_record["status"] = "incomplete_workspace_identity"
                        observations.append(attempt_record)
                    else:
                        if (
                            str(workspace.get("workspace_id") or "").strip()
                            != workspace_id
                            or str(workspace.get("label") or "").strip()
                            != expected_workspace_label
                        ):
                            raise HerdrSubmissionError(
                                "HERDR Leader recovery returned a conflicting installation workspace"
                            )
                        if workspace.get("focused") is not False:
                            raise HerdrSubmissionError(
                                "HERDR Leader recovery did not prove a non-focused workspace"
                            )

                        process_result = run_command(
                            [
                                herdr,
                                "pane",
                                "process-info",
                                "--pane",
                                session_id,
                            ],
                            timeout=5.0,
                        )
                        if process_result.get("ok") is not True:
                            attempt_record["status"] = "process_query_failed"
                            observations.append(attempt_record)
                        else:
                            process_payload = _json_stdout(process_result)
                            process_info = (
                                (process_payload.get("result") or {}).get("process_info")
                                or {}
                            )
                            process_group = process_info.get(
                                "foreground_process_group_id"
                            )
                            processes = process_info.get("foreground_processes")
                            if (
                                process_info.get("pane_id") != session_id
                                or not isinstance(process_group, (str, int))
                                or not str(process_group).strip()
                                or not isinstance(processes, list)
                                or len(processes) != 1
                                or not isinstance(processes[0], dict)
                            ):
                                attempt_record["status"] = "incomplete_process_identity"
                                observations.append(attempt_record)
                            else:
                                process = processes[0]
                                observed_argv = list(process.get("argv") or [])
                                argv_matches = observed_argv == launch_argv or (
                                    len(observed_argv) == len(launch_argv)
                                    and Path(str(observed_argv[0])).name == Path(str(launch_argv[0])).name
                                    and observed_argv[1:] == launch_argv[1:]
                                )
                                if not argv_matches:
                                    raise HerdrSubmissionError(
                                        "HERDR Leader recovery launch argv differs from the selected passport"
                                    )
                                process_cwd = str(process.get("cwd") or "").strip()
                                if (
                                    not process_cwd
                                    or Path(process_cwd).resolve() != root
                                    or str(process.get("pid") or "").strip()
                                    != str(process_group).strip()
                                ):
                                    raise HerdrSubmissionError(
                                        "HERDR Leader recovery returned a conflicting live process identity"
                                    )
                                process_generation = opaque_process_generation(
                                    process_group
                                )
                                attempt_record["status"] = "pass"
                                observations.append(attempt_record)
                                break
        if attempt < readiness_attempts and readiness_interval_seconds:
            time.sleep(readiness_interval_seconds)
    else:
        last_status = observations[-1]["status"] if observations else "unknown"
        raise HerdrSubmissionError(
            "HERDR Leader recovery readiness budget exhausted: "
            f"{readiness_attempts} attempt(s), last status {last_status}"
        )

    assert process_generation is not None
    runtime_identity = {
        "session_id": session_id,
        **{key: str(pane[key]).strip() for key in required_identity},
        "process_generation": process_generation,
    }
    identity_digest = hashlib.sha256(
        json.dumps(runtime_identity, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    observed_at = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return {
        "adapter_id": "herdr",
        "adapter_class": "pane_controller",
        "principal_id": principal_id,
        "agent_id": agent,
        "generation": generation,
        "ownership": {
            "scope": "installation",
            "installation_id": installation_id,
        },
        "context": {"cwd": str(root)},
        "launch": {"argv": launch_argv},
        "focused_at_provisioning": False,
        "runtime_scope": {
            "kind": "workspace",
            "ownership": "installation",
            "workspace_id": str(workspace["workspace_id"]).strip(),
            "label": expected_workspace_label,
        },
        "runtime_identity": {
            **runtime_identity,
            "token": f"sha256:{identity_digest}",
        },
        "health": {
            "status": "pass",
            "observed_at": observed_at,
            "evidence": {
                "agent_status": str(pane.get("agent_status") or "unknown"),
                "process_generation": process_generation,
                "readiness_attempts": len(observations),
                "readiness_policy": {
                    "maximum_attempts": readiness_attempts,
                    "interval_seconds": readiness_interval_seconds,
                    "per_attempt_timeout_seconds": 5.0,
                },
                "runtime_mutation": "none",
                "observations": observations,
            },
        },
        "recovery": dict(recovery_approval),
        "provisioned_at": observed_at,
    }


def _session_workspace_label(task_id: str, safe_agent: str, generation: int) -> str:
    safe_task = re.sub(r"[^a-z0-9]+", "-", task_id.lower()).strip("-")[:36] or "task"
    return f"valp-{safe_task}-{safe_agent}-g{generation}"


def _pane_session_label(pane: dict[str, Any]) -> str:
    return str(pane.get("name") or pane.get("label") or "").strip()


def detect_herdr_submission_capability(
    herdr: str,
    run_command: RunCommand,
) -> dict[str, Any]:
    agent_help = run_command([herdr, "agent", "--help"], timeout=5.0)
    pane_help = run_command([herdr, "pane", "--help"], timeout=5.0)
    agent_text = _command_text(agent_help)
    pane_text = _command_text(pane_help)

    atomic_prompt = bool(
        agent_help.get("ok") is True
        and _help_has_command(agent_text, "herdr agent get")
        and _help_command_has_options(
            agent_text,
            "herdr agent prompt",
            "--wait",
            "--until",
            "--timeout",
        )
    )
    if (
        not atomic_prompt
        and _help_lists_subcommand(agent_text, "get")
        and _help_lists_subcommand(agent_text, "prompt")
    ):
        get_help = run_command([herdr, "agent", "get", "--help"], timeout=5.0)
        prompt_help = run_command([herdr, "agent", "prompt", "--help"], timeout=5.0)
        atomic_prompt = _nested_help_has_usage(
            get_help,
            "herdr agent get",
        ) and _nested_help_has_usage(
            prompt_help,
            "herdr agent prompt",
            "--wait",
            "--until",
            "--timeout",
        )
    pane_send_text = pane_help.get("ok") is True and _help_has_command(pane_text, "herdr pane send-text")
    pane_send_keys = pane_help.get("ok") is True and _help_has_command(pane_text, "herdr pane send-keys")
    agent_wait = agent_help.get("ok") is True and _help_has_command(agent_text, "herdr agent wait")
    if not pane_send_text and _help_lists_subcommand(pane_text, "send-text"):
        pane_send_text = _nested_help_has_usage(
            run_command([herdr, "pane", "send-text", "--help"], timeout=5.0),
            "herdr pane send-text",
        )
    if not pane_send_keys and _help_lists_subcommand(pane_text, "send-keys"):
        pane_send_keys = _nested_help_has_usage(
            run_command([herdr, "pane", "send-keys", "--help"], timeout=5.0),
            "herdr pane send-keys",
        )
    if not agent_wait and _help_lists_subcommand(agent_text, "wait"):
        agent_wait = _nested_help_has_usage(
            run_command([herdr, "agent", "wait", "--help"], timeout=5.0),
            "herdr agent wait",
        )
    if atomic_prompt:
        mode = "agent_prompt"
        status = "pass"
        message = "HERDR exposes atomic Agent prompt/wait submission proof."
    elif pane_send_text and pane_send_keys and agent_wait:
        mode = "pane_send_text_enter"
        status = "warn"
        message = (
            "HERDR exposes pane insertion, Enter, and Agent observation only; "
            "submission is Manual-degraded without independent invocation proof."
        )
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
    session_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    mode = str(capability.get("mode") or "unavailable")
    if capability.get("status") not in {"pass", "warn"} or mode == "unavailable":
        raise HerdrSubmissionError(str(capability.get("message") or "HERDR submission is unavailable"))
    if mode == "agent_prompt" and capability.get("status") != "pass":
        raise HerdrSubmissionError(
            "HERDR atomic Agent prompt/wait capability is not independently proven"
        )
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
    binding_proof: dict[str, Any] = {}
    if session_binding is not None:
        binding_identity = session_binding.get("runtime_identity") or {}
        if (
            session_binding.get("agent") != target
            or session_binding.get("dispatch_eligible") is not True
            or str(binding_identity.get("pane_id") or "") != pane_id
            or not str(binding_identity.get("token") or "").startswith("sha256:")
        ):
            raise HerdrSubmissionError("HERDR submission session binding is invalid or mismatched")
        binding_proof = {
            "ref": "agent-sessions.json",
            "generation": session_binding.get("generation"),
            "identity_token": binding_identity.get("token"),
            "ownership": session_binding.get("ownership"),
        }

    if mode == "agent_prompt":
        baseline_result = run_command(
            [herdr, "agent", "get", runtime_target],
            timeout=5.0,
        )
        _require_success(baseline_result, "HERDR Agent submission baseline")
        baseline = _herdr_agent_response(
            baseline_result,
            expected_type="agent_info",
            action="HERDR Agent submission baseline",
        )
        baseline_agent = baseline["agent"]
        if baseline_agent["pane_id"] != pane_id:
            raise HerdrSubmissionError(
                "HERDR Agent submission baseline does not match the routed pane"
            )
        if baseline_agent["agent_status"] not in {"idle", "done"}:
            raise HerdrSubmissionError(
                "HERDR Agent submission baseline is not settled"
            )
        if binding_proof:
            binding_identity = (session_binding or {}).get("runtime_identity") or {}
            bound_terminal_id = str(binding_identity.get("terminal_id") or "").strip()
            if bound_terminal_id and baseline_agent["terminal_id"] != bound_terminal_id:
                raise HerdrSubmissionError(
                    "HERDR Agent submission baseline does not match the bound terminal"
                )
        timeout_ms = max(1, int(timeout_seconds * 1000))
        result = run_command(
            [
                herdr,
                "agent",
                "prompt",
                runtime_target,
                payload,
                "--wait",
                "--until",
                "working",
                "--timeout",
                str(timeout_ms),
            ],
            timeout=max(5.0, timeout_seconds + 1.0),
        )
        _require_success(result, "atomic HERDR agent prompt")
        prompted = _herdr_agent_response(
            result,
            expected_type="agent_prompted",
            action="atomic HERDR agent prompt",
        )
        prompted_agent = prompted["agent"]
        identity_fields = ("terminal_id", "name", "agent", "pane_id")
        identity = {field: baseline_agent[field] for field in identity_fields}
        if any(prompted_agent[field] != value for field, value in identity.items()):
            raise HerdrSubmissionError(
                "atomic HERDR agent prompt changed the routed Agent identity"
            )
        if prompted_agent["agent_status"] != "working":
            raise HerdrSubmissionError(
                "atomic HERDR agent prompt did not return the requested working state"
            )
        baseline_sequence = baseline_agent["state_change_seq"]
        prompted_sequence = prompted_agent["state_change_seq"]
        if prompted_sequence <= baseline_sequence:
            raise HerdrSubmissionError(
                "atomic HERDR agent prompt did not advance state_change_seq"
            )
        return {
            "runtime": "HERDR",
            "adapter": "VALP packaged HERDR adapter",
            "transport_mode": mode,
            "proof_class": "agent_invocation",
            "pane_id": pane_id,
            "agent_ref": target,
            "runtime_target": runtime_target,
            "payload_digest": payload_digest,
            "runtime_response": prompted,
            "submission_proof": {
                "kind": "identity_bound_state_change",
                "baseline_state_change_seq": baseline_sequence,
                "state_change_seq": prompted_sequence,
                "identity": identity,
            },
            **({"session_binding": binding_proof} if binding_proof else {}),
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
    working_observed = working.get("ok") is True
    return {
        "runtime": "HERDR",
        "adapter": "VALP packaged HERDR adapter",
        "transport_mode": mode,
        "proof_class": "transport_only",
        "manual_degraded": True,
        "pane_id": pane_id,
        "agent_ref": target,
        "runtime_target": runtime_target,
        "payload_digest": payload_digest,
        **({"session_binding": binding_proof} if binding_proof else {}),
        "status_proof": {
            "status": "working" if working_observed else "unproven",
            "enter_attempts": enter_attempts,
            "working_attempt": enter_attempts,
            "runtime_response": _runtime_identity(working),
            **(
                {}
                if working_observed
                else {
                    "error": str(
                        working.get("stderr")
                        or working.get("stdout")
                        or "Agent state was not observed"
                    ).strip()
                }
            ),
        },
    }


def observe_herdr_terminal(
    herdr: str,
    *,
    task_id: str,
    target: str,
    pane_id: str,
    submission_proof: dict[str, Any],
    run_command: Callable[..., dict[str, Any]],
    timeout_seconds: float = 5.0,
    poll_interval_seconds: float = 0.1,
) -> dict[str, Any]:
    """Observe one identity-bound HERDR terminal state after atomic submission."""

    if (
        submission_proof.get("runtime") != "HERDR"
        or submission_proof.get("proof_class") != "agent_invocation"
        or submission_proof.get("agent_ref") != target
        or submission_proof.get("pane_id") != pane_id
    ):
        raise HerdrSubmissionError("HERDR terminal observation lacks exact submission proof")
    submitted = submission_proof.get("submission_proof") or {}
    identity = submitted.get("identity") or {}
    submission_sequence = submitted.get("state_change_seq")
    runtime_target = str(submission_proof.get("runtime_target") or "")
    if (
        not runtime_target
        or type(submission_sequence) is not int
        or not all(str(identity.get(field) or "") for field in (
            "terminal_id", "agent", "pane_id"
        ))
    ):
        raise HerdrSubmissionError("HERDR terminal observation submission identity is incomplete")
    if not herdr:
        raise HerdrSubmissionError("HERDR command is unavailable for terminal observation")
    if timeout_seconds < 0 or poll_interval_seconds < 0:
        raise HerdrSubmissionError("HERDR terminal observation timing must be non-negative")
    deadline = time.monotonic() + timeout_seconds
    while True:
        observed_result = run_command(
            [herdr, "agent", "get", runtime_target],
            timeout=5.0,
        )
        _require_success(observed_result, "HERDR Agent terminal observation")
        observed = _herdr_agent_response(
            observed_result,
            expected_type="agent_info",
            action="HERDR Agent terminal observation",
        )["agent"]
        if (
            observed["terminal_id"] != identity["terminal_id"]
            or observed["agent"] != identity["agent"]
            or observed["pane_id"] != identity["pane_id"]
        ):
            raise HerdrSubmissionError(
                "HERDR terminal state changed the submitted Agent identity"
            )
        if (
            observed["state_change_seq"] > submission_sequence
            and observed["agent_status"] in {"idle", "blocked"}
        ):
            break
        if time.monotonic() >= deadline:
            raise HerdrSubmissionError(
                "HERDR terminal observation timed out before a later idle or blocked state"
            )
        time.sleep(min(poll_interval_seconds, max(0.0, deadline - time.monotonic())))
    status = "blocked" if observed["agent_status"] == "blocked" else "completed"
    proof = {
        "schema_version": "valp-herdr-terminal-observation.v1",
        "runtime": "HERDR",
        "proof_class": "agent_terminal_observation",
        "task_id": task_id,
        "agent": target,
        "terminal_id": observed["terminal_id"],
        "pane_id": observed["pane_id"],
        "submission_state_change_seq": submission_sequence,
        "state_change_seq": observed["state_change_seq"],
        "status": status,
        "acknowledged": True,
    }
    if status == "blocked":
        proof["failure_code"] = "herdr_agent_blocked"
    return proof


def _command_text(result: dict[str, Any]) -> str:
    return "\n".join((str(result.get("stdout") or ""), str(result.get("stderr") or ""))).lower()


def _json_stdout(result: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(result.get("stdout") or ""))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _focused_herdr_workspace_id(herdr: str, run_command: RunCommand) -> str:
    """Return the one caller workspace that must regain focus after launch."""

    result = run_command([herdr, "workspace", "list"], timeout=5.0)
    _require_success(result, "HERDR caller workspace discovery")
    payload = _json_stdout(result)
    record = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    workspaces = record.get("workspaces") if isinstance(record, dict) else []
    focused = [
        str(workspace.get("workspace_id") or "").strip()
        for workspace in workspaces or []
        if isinstance(workspace, dict) and workspace.get("focused") is True
    ]
    if len(focused) != 1 or not focused[0]:
        raise HerdrSubmissionError(
            "HERDR Leader provisioning requires exactly one focused caller workspace"
        )
    return focused[0]


def _help_has_command(help_text: str, command: str) -> bool:
    pattern = rf"(?m)^\s*{re.escape(command.lower())}(?:\s|$)"
    return re.search(pattern, help_text) is not None


def _help_lists_subcommand(help_text: str, subcommand: str) -> bool:
    pattern = rf"(?m)^\s*{re.escape(subcommand.lower())}(?:\s|$)"
    return re.search(pattern, help_text) is not None


def _nested_help_has_usage(
    result: dict[str, Any],
    command: str,
    *options: str,
) -> bool:
    if result.get("ok") is not True:
        return False
    help_text = _command_text(result)
    usage = rf"(?m)^\s*usage:\s*{re.escape(command.lower())}(?:\s|$)"
    return re.search(usage, help_text) is not None and all(
        option.lower() in help_text for option in options
    )


def _help_command_has_options(
    help_text: str,
    command: str,
    *options: str,
) -> bool:
    prefix = command.lower()
    for line in help_text.splitlines():
        candidate = line.strip().lower()
        if candidate == prefix or candidate.startswith(prefix + " "):
            return all(option.lower() in candidate for option in options)
    return False


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


def _herdr_agent_response(
    result: dict[str, Any],
    *,
    expected_type: str,
    action: str,
) -> dict[str, Any]:
    payload = _json_stdout(result)
    response = payload.get("result")
    if not isinstance(response, dict) or response.get("type") != expected_type:
        raise HerdrSubmissionError(
            f"{action} returned an unexpected structured response"
        )
    agent = response.get("agent")
    if not isinstance(agent, dict):
        raise HerdrSubmissionError(f"{action} returned no Agent record")
    required_strings = ("terminal_id", "name", "agent", "agent_status", "pane_id")
    if any(
        not isinstance(agent.get(field), str) or not str(agent[field]).strip()
        for field in required_strings
    ):
        raise HerdrSubmissionError(f"{action} returned an incomplete Agent identity")
    state_change_seq = agent.get("state_change_seq")
    if (
        not isinstance(state_change_seq, int)
        or isinstance(state_change_seq, bool)
        or state_change_seq < 0
    ):
        raise HerdrSubmissionError(f"{action} returned an invalid state_change_seq")
    return {
        "type": expected_type,
        "agent": {
            **agent,
            **{field: str(agent[field]).strip() for field in required_strings},
        },
    }
