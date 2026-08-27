from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import contextlib
import errno
import hashlib
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tests.schema_helpers import schema_validator

import valp_cli.workflow as workflow_module
from valp_cli.audit import TaskAudit
from valp_cli.cli import main
from valp_cli.herdr_adapter import HerdrSubmissionError
from valp_cli.model_identity import model_identity_for
from valp_cli.submission import (
    build_submission_dependencies,
    dependency_order_errors,
    role_expected_refs,
    roles_for_agent,
    unmet_dependencies_for_phases,
    validate_submission_dependencies,
)
from valp_cli.workflow import (
    await_owned_session_model_preflight,
    classify_profile,
    classify_approval_risks,
    decompose_execution_tasks,
    dispatch_task,
    enforce_iteration_budget,
    feedback_prior_for_agent,
    load_local_capabilities,
    load_routing_feedback_history,
    publish_task,
    read_json,
    route_task,
    runtime_dispatch_retry_pending,
    scan_workspace,
    score_candidates,
    resume_suspended_task,
    suspend_task,
    translate_legacy_herdr_receipts,
    wait_for_task,
    write_queue_submission,
)


def herdr_invocation_proof(*, pane_id: str = "pane-1") -> dict[str, object]:
    return {
        "runtime": "HERDR",
        "transport_mode": "agent_prompt",
        "proof_class": "agent_invocation",
        "pane_id": pane_id,
        "submission_proof": {
            "kind": "identity_bound_state_change",
            "baseline_state_change_seq": 1,
            "state_change_seq": 2,
            "identity": {
                "terminal_id": "terminal-1",
                "name": "codex",
                "agent": "codex",
                "pane_id": pane_id,
            },
        },
    }


class ValpWorkflowTests(unittest.TestCase):
    def test_herdr_expected_evidence_requires_a_post_submission_content_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            ref = "agents/codex/evidence.md"
            evidence = directory / ref
            evidence.parent.mkdir(parents=True)
            evidence.write_text("old evidence\n", encoding="utf-8")

            baseline = workflow_module.expected_evidence_snapshot(directory, [ref])
            completed, existing, missing = workflow_module.herdr_expected_ref_status(
                directory, [ref], baseline
            )
            self.assertFalse(completed)
            self.assertEqual(existing, [])
            self.assertEqual(missing, [ref])

            evidence.write_text("fresh evidence\n", encoding="utf-8")
            completed, existing, missing = workflow_module.herdr_expected_ref_status(
                directory, [ref], baseline
            )
            self.assertTrue(completed)
            self.assertEqual(existing, [ref])
            self.assertEqual(missing, [])

    def test_bootstrap_task_owned_codex_session_runs_once_before_formal_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            task_id = "TASK-CODEX-BOOTSTRAP"
            pane_id = "workspace-owned:p1"
            contract = workflow_module.build_control_contract(
                task_id, "2026-08-08T06:00:00Z"
            )
            contract_bytes = (json.dumps(contract, indent=2, ensure_ascii=False) + "\n").encode()
            digest = workflow_module.control_contract_digest(contract, contract_bytes)
            control_slice = workflow_module.build_control_slice(
                task_id,
                "codex",
                ["implementer:codex"],
                digest,
            )
            (directory / "control-slices").mkdir()
            (directory / "control-contract.json").write_bytes(contract_bytes)
            (directory / "control-slices" / "codex.json").write_text(
                json.dumps(control_slice, indent=2) + "\n", encoding="utf-8"
            )
            binding = {
                "agent": "codex",
                "session_name": "task-codex",
                "generation": 1,
                "ownership": {
                    "scope": "task",
                    "task_id": task_id,
                    "project_identity": "sha256:" + ("c" * 64),
                },
                "context": {"cwd": str(directory)},
                "launch": {"argv": ["/test/codex", "-m", "model-implementation-large"]},
                "focused_at_provisioning": False,
                "runtime_scope": {"kind": "workspace", "ownership": "task"},
                "runtime_identity": {
                    "pane_id": pane_id,
                    "terminal_id": "terminal-owned",
                    "workspace_id": "workspace-owned",
                    "tab_id": "tab-owned",
                    "token": "sha256:" + ("a" * 64),
                },
                "lifecycle": "provisioned",
                "dispatch_eligible": True,
            }
            projection = {
                "schema_version": "valp-agent-sessions.v1",
                "task_id": task_id,
                "adapter": "herdr",
                "status": "ready",
                "bindings": {"codex": binding},
                "updated_at": "2026-08-08T06:00:00Z",
            }
            (directory / "agent-sessions.json").write_text(
                json.dumps(projection), encoding="utf-8"
            )
            (directory / "agent-session-receipts.jsonl").write_text(
                "", encoding="utf-8"
            )
            (directory / "dispatch-receipts.jsonl").write_text(
                json.dumps({"event": "dispatch_written", "agent": "codex"}) + "\n",
                encoding="utf-8",
            )
            calls: list[list[str]] = []
            readiness_calls = 0

            def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
                nonlocal readiness_calls
                calls.append(command)
                if command[1:3] == ["agent", "readiness"]:
                    readiness_calls += 1
                    if readiness_calls == 1:
                        readiness = {
                            "schema_version": "valp-named-agent-readiness.v1",
                            "ready": False,
                            "reason_code": "session_identity_unknown",
                            "addressable": True,
                            "detected_agent": "codex",
                            "agent_status": "idle",
                            "interactive_ready": True,
                            "prompt_eligible": False,
                            "session_identity": {"status": "unknown"},
                            "state_change_seq": 10,
                        }
                    else:
                        readiness = {
                            "schema_version": "valp-named-agent-readiness.v1",
                            "ready": True,
                            "reason_code": "ready",
                            "addressable": True,
                            "detected_agent": "codex",
                            "agent_status": "done",
                            "interactive_ready": True,
                            "prompt_eligible": True,
                            "session_identity": {
                                "status": "known",
                                "identity": {
                                    "source": "herdr:codex",
                                    "agent": "codex",
                                    "kind": "id",
                                    "value": "native-session",
                                },
                            },
                            "state_change_seq": 12,
                        }
                    stdout = json.dumps(
                        {"result": {"type": "agent_readiness", "readiness": readiness}}
                    )
                elif command[1:3] == ["agent", "get"]:
                    stdout = json.dumps({"result": {"type": "agent_info", "agent": {
                        "terminal_id": "terminal-owned", "name": "task-codex",
                        "agent": "codex", "agent_status": "idle", "pane_id": pane_id,
                        "state_change_seq": 10,
                    }}})
                elif command[1:3] == ["pane", "read"]:
                    stdout = "Codex ready for input\n"
                elif command[1:3] == ["agent", "prompt"]:
                    self.assertTrue(command[4].startswith("[VALP CONTROL SLICE]\n"))
                    self.assertIn(digest, command[4])
                    self.assertIn(
                        "Resolve control_contract_ref relative to this task directory: "
                        f"{directory.resolve()}",
                        command[4],
                    )
                    stdout = json.dumps({"result": {"type": "agent_prompted", "agent": {
                        "terminal_id": "terminal-owned", "name": "task-codex",
                        "agent": "codex", "agent_status": "done", "pane_id": pane_id,
                        "state_change_seq": 12,
                    }}})
                elif command[1:3] == ["pane", "wait-output"]:
                    self.assertEqual(
                        command[5],
                        r"^(?:BOOTSTRAP_READY|• BOOTSTRAP_READY|⏺ BOOTSTRAP_READY)$",
                    )
                    stdout = json.dumps({"result": {
                        "type": "output_matched",
                        "pane_id": pane_id,
                        "matched_line": "• BOOTSTRAP_READY",
                    }})
                elif command[1:3] == ["agent", "model-probe"]:
                    native_digest = hashlib.sha256(b"native-session").hexdigest()
                    stdout = json.dumps({"result": {"type": "agent_model_probe", "probe": {
                        "schema_version": "valp-model-probe.v1", "status": "observed",
                        "source": "herdr:codex structured Stop hook", "observed_at": workflow_module.now_iso(),
                        "ttl_seconds": 3600,
                        "model": {"model_id": "model-implementation-large", "provider": "provider-relay", "reasoning_mode": "medium", "confidence": "high"},
                        "session_identity": {
                            "status": "known",
                            "token": f"sha256:{native_digest}",
                            "source": "herdr:codex",
                            "generation": f"session:{native_digest[:16]}",
                        },
                    }}})
                else:
                    raise AssertionError(f"unexpected command: {command}")
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

            updated = workflow_module.bootstrap_task_owned_herdr_session(
                directory,
                task_id,
                "codex",
                binding,
                herdr="/test/herdr",
                run_command_fn=fake_run,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )

            verified = updated["bindings"]["codex"]
            self.assertEqual(verified["lifecycle"], "bootstrap_ready")
            evidence = json.loads(
                (directory / verified["bootstrap_verification"]["evidence_ref"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(evidence["formal_dispatch_count"], 0)
            self.assertEqual(evidence["native_turn"]["actual_response"], "BOOTSTRAP_READY")
            self.assertEqual(
                evidence["response_proof"]["authority"], "response_only_not_identity_or_model"
            )
            self.assertEqual(
                evidence["response_proof"]["raw_matched_line"], "• BOOTSTRAP_READY"
            )
            self.assertEqual(
                evidence["response_proof"]["renderer_envelope"], "codex_list_marker"
            )
            self.assertFalse(any(call[1:3] == ["pane", "send-text"] for call in calls))

            before = len(calls)
            repeated = workflow_module.bootstrap_task_owned_herdr_session(
                directory,
                task_id,
                "codex",
                verified,
                herdr="/test/herdr",
                run_command_fn=fake_run,
                timeout_seconds=1,
                poll_interval_seconds=0,
            )
            self.assertEqual(repeated["bindings"]["codex"]["lifecycle"], "bootstrap_ready")
            self.assertEqual(len(calls), before)
            self.assertEqual(
                list(
                    schema_validator(
                        Path("schemas/agent-sessions.schema.json")
                    ).iter_errors(repeated)
                ),
                [],
            )

    def test_bootstrap_response_normalization_accepts_only_declared_envelopes(self) -> None:
        accepted = {
            "BOOTSTRAP_READY": "bare",
            "• BOOTSTRAP_READY": "codex_list_marker",
            "⏺ BOOTSTRAP_READY": "claude_action_marker",
        }
        for raw_line, envelope in accepted.items():
            with self.subTest(raw_line=raw_line):
                self.assertEqual(
                    workflow_module._normalize_herdr_bootstrap_response(raw_line),
                    ("BOOTSTRAP_READY", envelope),
                )

        for raw_line in (
            "⏺  BOOTSTRAP_READY",
            "⏺ BOOTSTRAP_READY extra",
            "prefix ⏺ BOOTSTRAP_READY",
            "● BOOTSTRAP_READY",
        ):
            with self.subTest(raw_line=raw_line):
                self.assertIsNone(
                    workflow_module._normalize_herdr_bootstrap_response(raw_line)
                )

    def test_bootstrap_task_owned_codex_session_fails_closed_before_prompt(self) -> None:
        for case in ("wrong_reason", "formal_dispatch"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                task_id = "TASK-CODEX-BOOTSTRAP-REJECT"
                contract = workflow_module.build_control_contract(
                    task_id, "2026-08-08T06:00:00Z"
                )
                contract_bytes = (json.dumps(contract, indent=2, ensure_ascii=False) + "\n").encode()
                digest = workflow_module.control_contract_digest(contract, contract_bytes)
                control_slice = workflow_module.build_control_slice(
                    task_id, "codex", ["implementer:codex"], digest
                )
                (directory / "control-slices").mkdir()
                (directory / "control-contract.json").write_bytes(contract_bytes)
                (directory / "control-slices" / "codex.json").write_text(
                    json.dumps(control_slice), encoding="utf-8"
                )
                binding = {
                    "agent": "codex", "generation": 1,
                    "ownership": {"scope": "task", "task_id": task_id},
                    "runtime_identity": {"pane_id": "pane-1", "token": "sha256:" + ("a" * 64)},
                    "lifecycle": "provisioned", "dispatch_eligible": True,
                }
                (directory / "agent-sessions.json").write_text(json.dumps({
                    "schema_version": "valp-agent-sessions.v1", "task_id": task_id,
                    "adapter": "herdr", "status": "ready", "bindings": {"codex": binding},
                }), encoding="utf-8")
                (directory / "agent-session-receipts.jsonl").write_text("", encoding="utf-8")
                receipt = {"event": "dispatch_submitted", "agent": "codex"} if case == "formal_dispatch" else {"event": "dispatch_written", "agent": "codex"}
                (directory / "dispatch-receipts.jsonl").write_text(json.dumps(receipt) + "\n", encoding="utf-8")
                calls: list[list[str]] = []

                def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
                    calls.append(command)
                    readiness = {
                        "schema_version": "valp-named-agent-readiness.v1", "ready": False,
                        "reason_code": "not_interactive" if case == "wrong_reason" else "session_identity_unknown",
                        "addressable": True, "detected_agent": "codex", "agent_status": "idle",
                        "interactive_ready": case != "wrong_reason", "prompt_eligible": False,
                        "session_identity": {"status": "unknown"}, "state_change_seq": 1,
                    }
                    return {"ok": True, "exit_code": 0, "stdout": json.dumps({"result": {"type": "agent_readiness", "readiness": readiness}}), "stderr": ""}

                with self.assertRaises(HerdrSubmissionError):
                    workflow_module.bootstrap_task_owned_herdr_session(
                        directory, task_id, "codex", binding,
                        herdr="/test/herdr", run_command_fn=fake_run,
                        timeout_seconds=1, poll_interval_seconds=0,
                    )
                self.assertFalse(any(call[1:3] == ["agent", "prompt"] for call in calls))

    def test_bootstrap_task_owned_claude_session_observes_model_on_same_native_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            task_id = "TASK-CLAUDE-MODEL-BOOTSTRAP"
            pane_id = "workspace-claude:p1"
            native_session_id = "claude-native-session"
            native_digest = hashlib.sha256(native_session_id.encode()).hexdigest()
            contract = workflow_module.build_control_contract(
                task_id, "2026-08-08T10:00:00Z"
            )
            contract_bytes = (json.dumps(contract, indent=2) + "\n").encode()
            digest = workflow_module.control_contract_digest(contract, contract_bytes)
            control_slice = workflow_module.build_control_slice(
                task_id, "claude", ["reviewer:claude"], digest
            )
            (directory / "control-slices").mkdir()
            (directory / "control-contract.json").write_bytes(contract_bytes)
            (directory / "control-slices" / "claude.json").write_text(
                json.dumps(control_slice), encoding="utf-8"
            )
            binding = {
                "agent": "claude",
                "session_name": "task-claude",
                "generation": 2,
                "ownership": {"scope": "task", "task_id": task_id},
                "context": {"cwd": str(directory)},
                "launch": {"argv": ["/test/claude"]},
                "focused_at_provisioning": False,
                "runtime_scope": {"kind": "workspace", "ownership": "task"},
                "runtime_identity": {
                    "pane_id": pane_id,
                    "terminal_id": "terminal-claude",
                    "token": "sha256:" + ("a" * 64),
                },
                "lifecycle": "provisioned",
                "dispatch_eligible": True,
            }
            (directory / "agent-sessions.json").write_text(json.dumps({
                "schema_version": "valp-agent-sessions.v1",
                "task_id": task_id,
                "adapter": "herdr",
                "status": "ready",
                "bindings": {"claude": binding},
            }), encoding="utf-8")
            (directory / "agent-session-receipts.jsonl").write_text("", encoding="utf-8")
            (directory / "dispatch-receipts.jsonl").write_text(
                json.dumps({"event": "dispatch_written", "agent": "claude"}) + "\n"
                + json.dumps({
                    "schema_version": "valp-dispatch-receipt.v2",
                    "event": "dispatch_submitted",
                    "agent": "claude",
                    "role": "coordinator",
                    "work_item_id": "coordinator:claude",
                    "proof": {"session_binding": {"generation": 1}},
                }) + "\n",
                encoding="utf-8",
            )
            calls: list[list[str]] = []
            readiness_calls = 0
            model_probe_calls = 0

            def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
                nonlocal readiness_calls, model_probe_calls
                calls.append(command)
                if command[1:3] == ["agent", "readiness"]:
                    readiness_calls += 1
                    readiness = {
                        "schema_version": "valp-named-agent-readiness.v1",
                        "ready": True,
                        "reason_code": "ready",
                        "addressable": True,
                        "detected_agent": "claude",
                        "agent_status": "idle",
                        "interactive_ready": True,
                        "prompt_eligible": True,
                        "session_identity": {
                            "status": "known",
                            "identity": {
                                "source": "herdr:claude",
                                "agent": "claude",
                                "kind": "id",
                                "value": native_session_id,
                            },
                        },
                        "state_change_seq": 22 if readiness_calls > 1 else 20,
                    }
                    stdout = json.dumps({"result": {
                        "type": "agent_readiness", "readiness": readiness,
                    }})
                elif command[1:3] == ["agent", "model-probe"]:
                    model_probe_calls += 1
                    probe = {
                        "schema_version": "valp-model-probe.v1",
                        "status": "unsupported",
                        "source": "runtime integration has no current model observation",
                        "ttl_seconds": 3600,
                    } if model_probe_calls == 1 else {
                        "schema_version": "valp-model-probe.v1",
                        "status": "observed",
                        "source": "herdr:claude structured Stop hook",
                        "observed_at": workflow_module.now_iso(),
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "model-review-large",
                            "provider": "deepseek",
                            "reasoning_mode": "low",
                            "confidence": "high",
                        },
                        "session_identity": {
                            "status": "known",
                            "token": f"sha256:{native_digest}",
                            "source": "herdr:claude",
                            "generation": f"session:{native_digest[:16]}",
                        },
                    }
                    stdout = json.dumps({"result": {
                        "type": "agent_model_probe", "probe": probe,
                    }})
                elif command[1:3] == ["agent", "get"]:
                    stdout = json.dumps({"result": {"type": "agent_info", "agent": {
                        "terminal_id": "terminal-claude", "name": "task-claude",
                        "agent": "claude", "agent_status": "idle", "pane_id": pane_id,
                        "state_change_seq": 20,
                    }}})
                elif command[1:3] == ["pane", "read"]:
                    stdout = "Claude ready for input\n"
                elif command[1:3] == ["agent", "prompt"]:
                    self.assertTrue(command[4].startswith("[VALP CONTROL SLICE]\n"))
                    self.assertIn(digest, command[4])
                    stdout = json.dumps({"result": {"type": "agent_prompted", "agent": {
                        "terminal_id": "terminal-claude", "name": "task-claude",
                        "agent": "claude", "agent_status": "idle", "pane_id": pane_id,
                        "state_change_seq": 22,
                    }}})
                elif command[1:3] == ["pane", "wait-output"]:
                    stdout = json.dumps({"result": {
                        "type": "output_matched", "pane_id": pane_id,
                        "matched_line": "BOOTSTRAP_READY",
                    }})
                else:
                    raise AssertionError(f"unexpected command: {command}")
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

            updated = workflow_module.bootstrap_task_owned_herdr_session(
                directory, task_id, "claude", binding,
                herdr="/test/herdr", run_command_fn=fake_run,
                timeout_seconds=1, poll_interval_seconds=0,
            )

            verified = updated["bindings"]["claude"]
            evidence = json.loads((
                directory / verified["bootstrap_verification"]["evidence_ref"]
            ).read_text(encoding="utf-8"))
            self.assertEqual(verified["lifecycle"], "bootstrap_ready")
            self.assertEqual(evidence["target"]["native_session_id"], native_session_id)
            self.assertEqual(evidence["structured_observation"]["session_id"], native_session_id)
            self.assertEqual(evidence["native_turn"]["provider"], "deepseek")
            self.assertEqual(evidence["native_turn"]["model"], "model-review-large")
            self.assertEqual(evidence["native_turn"]["reasoning_mode"], "low")
            self.assertEqual(evidence["formal_dispatch_count"], 0)
            self.assertEqual(model_probe_calls, 2)
            self.assertTrue(any(call[1:3] == ["agent", "prompt"] for call in calls))

    def test_bootstrap_task_owned_hermes_session_uses_the_strong_model_path(self) -> None:
        source = inspect.getsource(workflow_module.bootstrap_task_owned_herdr_session)

        self.assertIn('{"codex", "claude", "hermes"}', source)
        self.assertIn('hermes_bootstrap_ready', source)
        self.assertIn('agent == "hermes"', source)
        self.assertIn('reason_code") == "session_identity_unknown"', source)
        self.assertIn('if agent in {"claude", "hermes"}', source)
        self.assertNotIn('agent in {"claude", "hermes", "agy"}', source)
        self.assertNotIn('agent in {"claude", "hermes", "grok"}', source)

    def test_done_session_reprovision_is_a_bounded_runtime_retry(self) -> None:
        source = inspect.getsource(workflow_module.dispatch_task)

        self.assertIn("done_session_reprovision_retry", source)
        self.assertIn('state.get("status") == "dispatching"', source)
        self.assertIn('== "runtime dispatch retry exhausted"', source)
        self.assertIn("or done_session_reprovision_retry", source)

    def test_existing_provisioning_receipt_is_not_duplicated_before_bootstrap(self) -> None:
        source = inspect.getsource(workflow_module.ensure_herdr_agent_sessions)

        self.assertIn('binding["lifecycle"] == "provisioned" and any(', source)
        self.assertIn('record.get("event") == "agent_session_provisioned"', source)
        self.assertIn('record.get("identity_token")', source)

    def test_bootstrap_task_owned_claude_session_rejects_unsupported_probe_with_model_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            task_id = "TASK-CLAUDE-AMBIGUOUS-MODEL-BOOTSTRAP"
            pane_id = "workspace-claude:p1"
            contract = workflow_module.build_control_contract(
                task_id, "2026-08-08T10:00:00Z"
            )
            contract_bytes = (json.dumps(contract, indent=2) + "\n").encode()
            digest = workflow_module.control_contract_digest(contract, contract_bytes)
            control_slice = workflow_module.build_control_slice(
                task_id, "claude", ["reviewer:claude"], digest
            )
            (directory / "control-slices").mkdir()
            (directory / "control-contract.json").write_bytes(contract_bytes)
            (directory / "control-slices" / "claude.json").write_text(
                json.dumps(control_slice), encoding="utf-8"
            )
            binding = {
                "agent": "claude", "generation": 1,
                "ownership": {"scope": "task", "task_id": task_id},
                "runtime_identity": {
                    "pane_id": pane_id,
                    "terminal_id": "terminal-claude",
                    "token": "sha256:" + ("a" * 64),
                },
                "lifecycle": "provisioned", "dispatch_eligible": True,
            }
            (directory / "agent-sessions.json").write_text(json.dumps({
                "schema_version": "valp-agent-sessions.v1", "task_id": task_id,
                "adapter": "herdr", "status": "ready", "bindings": {"claude": binding},
            }), encoding="utf-8")
            (directory / "agent-session-receipts.jsonl").write_text("", encoding="utf-8")
            (directory / "dispatch-receipts.jsonl").write_text(
                json.dumps({"event": "dispatch_written", "agent": "claude"}) + "\n",
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
                calls.append(command)
                if command[1:3] == ["agent", "readiness"]:
                    stdout = json.dumps({"result": {"type": "agent_readiness", "readiness": {
                        "schema_version": "valp-named-agent-readiness.v1",
                        "ready": True, "reason_code": "ready", "addressable": True,
                        "detected_agent": "claude", "agent_status": "idle",
                        "interactive_ready": True, "prompt_eligible": True,
                        "session_identity": {"status": "known", "identity": {
                            "source": "herdr:claude", "agent": "claude", "kind": "id",
                            "value": "claude-native-session",
                        }},
                        "state_change_seq": 20,
                    }}})
                elif command[1:3] == ["agent", "model-probe"]:
                    stdout = json.dumps({"result": {"type": "agent_model_probe", "probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "unsupported",
                        "source": "conflicting runtime payload",
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "spoofed-model", "provider": "spoofed-provider",
                            "reasoning_mode": "unknown", "confidence": "low",
                        },
                    }}})
                else:
                    raise AssertionError(f"bootstrap advanced past ambiguous model proof: {command}")
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

            with self.assertRaises(HerdrSubmissionError):
                workflow_module.bootstrap_task_owned_herdr_session(
                    directory, task_id, "claude", binding,
                    herdr="/test/herdr", run_command_fn=fake_run,
                    timeout_seconds=0, poll_interval_seconds=0,
                )
            self.assertFalse(any(call[1:3] == ["agent", "prompt"] for call in calls))

    def test_bootstrap_task_owned_codex_session_rejects_identity_and_replay_conflicts(self) -> None:
        for case in (
            "control_digest",
            "wrong_pane",
            "wrong_generation",
            "model_session",
            "model_token",
            "model_generation",
            "raw_native_generation",
            "preexisting_response",
            "prompt_spoof",
            "missing_matched_line",
            "repeated_unverified",
            "current_generation_delivery",
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                task_id = "TASK-CODEX-BOOTSTRAP-CONFLICT"
                pane_id = "workspace-owned:p1"
                contract = workflow_module.build_control_contract(
                    task_id, "2026-08-08T06:00:00Z"
                )
                contract_bytes = (
                    json.dumps(contract, indent=2, ensure_ascii=False) + "\n"
                ).encode()
                digest = workflow_module.control_contract_digest(contract, contract_bytes)
                control_slice = workflow_module.build_control_slice(
                    task_id, "codex", ["implementer:codex"], digest
                )
                if case == "control_digest":
                    control_slice["control_contract_digest"] = "sha256:" + ("f" * 64)
                (directory / "control-slices").mkdir()
                (directory / "control-contract.json").write_bytes(contract_bytes)
                (directory / "control-slices" / "codex.json").write_text(
                    json.dumps(control_slice), encoding="utf-8"
                )
                binding = {
                    "agent": "codex",
                    "session_name": "task-codex",
                    "generation": 1,
                    "ownership": {"scope": "task", "task_id": task_id},
                    "context": {"cwd": str(directory)},
                    "launch": {"argv": ["/test/codex", "-m", "model-implementation-large"]},
                    "focused_at_provisioning": False,
                    "runtime_scope": {"kind": "workspace", "ownership": "task"},
                    "runtime_identity": {
                        "pane_id": pane_id,
                        "terminal_id": "terminal-owned",
                        "token": "sha256:" + ("a" * 64),
                    },
                    "lifecycle": "provisioned",
                    "dispatch_eligible": True,
                }
                (directory / "agent-sessions.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "valp-agent-sessions.v1",
                            "task_id": task_id,
                            "adapter": "herdr",
                            "status": "ready",
                            "bindings": {"codex": binding},
                        }
                    ),
                    encoding="utf-8",
                )
                (directory / "agent-session-receipts.jsonl").write_text(
                    "", encoding="utf-8"
                )
                receipt = {"event": "dispatch_written", "agent": "codex"}
                if case == "current_generation_delivery":
                    receipt = {
                        "schema_version": "valp-dispatch-receipt.v2",
                        "event": "dispatch_submitted",
                        "agent": "codex",
                        "proof": {"session_binding": {"generation": 1}},
                    }
                (directory / "dispatch-receipts.jsonl").write_text(
                    json.dumps(receipt) + "\n", encoding="utf-8"
                )
                if case == "repeated_unverified":
                    evidence_dir = directory / "evidence"
                    evidence_dir.mkdir()
                    (evidence_dir / "bootstrap-probe-codex-g1.json").write_text(
                        json.dumps({"accepted": False}), encoding="utf-8"
                    )

                calls: list[list[str]] = []
                readiness_calls = 0

                def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
                    nonlocal readiness_calls
                    calls.append(command)
                    if command[1:3] == ["agent", "readiness"]:
                        readiness_calls += 1
                        known = readiness_calls > 1
                        readiness = {
                            "schema_version": "valp-named-agent-readiness.v1",
                            "ready": known,
                            "reason_code": "ready" if known else "session_identity_unknown",
                            "addressable": True,
                            "detected_agent": "codex",
                            "agent_status": "done" if known else "idle",
                            "interactive_ready": True,
                            "prompt_eligible": known,
                            "session_identity": (
                                {
                                    "status": "known",
                                    "identity": {
                                        "source": "herdr:codex",
                                        "agent": "codex",
                                        "kind": "id",
                                        "value": "native-session",
                                    },
                                }
                                if known
                                else {"status": "unknown"}
                            ),
                            "state_change_seq": 12 if known else 10,
                        }
                        stdout = json.dumps(
                            {"result": {"type": "agent_readiness", "readiness": readiness}}
                        )
                    elif command[1:3] == ["agent", "get"]:
                        stdout = json.dumps(
                            {
                                "result": {
                                    "type": "agent_info",
                                    "agent": {
                                        "terminal_id": "terminal-owned",
                                        "name": "task-codex",
                                        "agent": "codex",
                                        "agent_status": "idle",
                                        "pane_id": "other:p1" if case == "wrong_pane" else pane_id,
                                        "state_change_seq": 10,
                                    },
                                }
                            }
                        )
                    elif command[1:3] == ["pane", "read"]:
                        stdout = (
                            "• BOOTSTRAP_READY\n"
                            if case == "preexisting_response"
                            else "Codex ready for input\n"
                        )
                    elif command[1:3] == ["agent", "prompt"]:
                        stdout = json.dumps(
                            {
                                "result": {
                                    "type": "agent_prompted",
                                    "agent": {
                                        "terminal_id": "terminal-owned",
                                        "name": "task-codex",
                                        "agent": "codex",
                                        "agent_status": "done",
                                        "pane_id": pane_id,
                                        "state_change_seq": 12,
                                    },
                                }
                            }
                        )
                    elif command[1:3] == ["pane", "wait-output"]:
                        matched_line = (
                            "Load and honor the control slice, then respond with exactly "
                            "BOOTSTRAP_READY and nothing else."
                            if case == "prompt_spoof"
                            else None if case == "missing_matched_line" else "BOOTSTRAP_READY"
                        )
                        stdout = json.dumps(
                            {
                                "result": {
                                    "type": "output_matched",
                                    "pane_id": pane_id,
                                    "matched_line": matched_line,
                                }
                            }
                        )
                    elif command[1:3] == ["agent", "model-probe"]:
                        native_digest = hashlib.sha256(b"native-session").hexdigest()
                        stdout = json.dumps(
                            {
                                "result": {
                                    "type": "agent_model_probe",
                                    "probe": {
                                        "schema_version": "valp-model-probe.v1",
                                        "status": "observed",
                                        "source": "herdr:codex structured Stop hook",
                                        "observed_at": workflow_module.now_iso(),
                                        "ttl_seconds": 3600,
                                        "model": {
                                            "model_id": "model-implementation-large",
                                            "provider": "provider-relay",
                                            "reasoning_mode": "medium",
                                            "confidence": "high",
                                        },
                                        "session_identity": {
                                            "status": "known",
                                            "token": (
                                                "sha256:" + ("b" * 64)
                                                if case in {"model_session", "model_token"}
                                                else f"sha256:{native_digest}"
                                            ),
                                            "source": "herdr:codex",
                                            "generation": (
                                                "other-session"
                                                if case == "model_session"
                                                else "session:" + ("0" * 16)
                                                if case == "model_generation"
                                                else "native-session"
                                                if case == "raw_native_generation"
                                                else f"session:{native_digest[:16]}"
                                            ),
                                        },
                                    },
                                }
                            }
                        )
                    else:
                        raise AssertionError(f"unexpected command: {command}")
                    return {
                        "ok": True,
                        "exit_code": 0,
                        "stdout": stdout,
                        "stderr": "",
                    }

                supplied_binding = json.loads(json.dumps(binding))
                if case == "wrong_generation":
                    supplied_binding["generation"] = 2
                with self.assertRaises(HerdrSubmissionError):
                    workflow_module.bootstrap_task_owned_herdr_session(
                        directory,
                        task_id,
                        "codex",
                        supplied_binding,
                        herdr="/test/herdr",
                        run_command_fn=fake_run,
                        timeout_seconds=0,
                        poll_interval_seconds=0,
                    )
                if case not in {
                    "model_session",
                    "model_token",
                    "model_generation",
                    "raw_native_generation",
                    "prompt_spoof",
                    "missing_matched_line",
                }:
                    self.assertFalse(
                        any(call[1:3] == ["agent", "prompt"] for call in calls)
                    )

    def test_sequential_dispatch_preflight_preserves_prior_task_owned_agents(self) -> None:
        task_id = "TASK-SEQUENTIAL-PREFLIGHT"

        def agent_record(agent: str, pane_id: str, *, owner: str = task_id) -> dict:
            return {
                "status": "pass",
                "pane_id": pane_id,
                "model_probe": {
                    "status": "observed",
                    "model": {
                        "model_id": f"{agent}-model",
                        "provider": f"{agent}-provider",
                        "reasoning_mode": "unknown",
                        "confidence": "high",
                    },
                    "session_identity": {
                        "status": "known",
                        "token": f"sha256:{agent}",
                    },
                },
                "session_binding": {
                    "status": "bound",
                    "ownership": {"task_id": owner},
                },
            }

        previous = {
            "generated_at": "2026-07-26T07:00:00Z",
            "status": "pass",
            "checks": {},
            "agents": {
                "hermes": agent_record("hermes", "pane-hermes"),
                "codex": agent_record("codex", "pane-wrong", owner="OTHER-TASK"),
            },
        }
        current = {
            "generated_at": "2026-07-26T07:10:00Z",
            "status": "pass",
            "checks": {},
            "agents": {
                "codex": agent_record("codex", "pane-codex"),
            },
        }

        merged = workflow_module.merge_task_owned_runtime_preflight(
            previous,
            current,
            ["hermes", "codex", "claude"],
            task_id,
        )

        self.assertEqual(merged["agents"]["hermes"]["pane_id"], "pane-hermes")
        self.assertEqual(merged["agents"]["codex"]["pane_id"], "pane-codex")
        self.assertNotIn("claude", merged["agents"])
        self.assertEqual(merged["status"], "warn")
        self.assertEqual(
            merged["checks"]["owned_session_model_readiness"],
            {"status": "warn", "pending_agents": ["claude"]},
        )

    def test_owned_session_model_preflight_waits_for_observed_identity(self) -> None:
        initial = {
            "checks": {},
            "agents": {
                "codex": {
                    "agent_status": "unknown",
                    "model_probe": {
                        "status": "unsupported",
                        "session_identity": {"status": "known"},
                    }
                }
            },
        }
        observed = {
            "checks": {},
            "agents": {
                "codex": {
                    "agent_status": "idle",
                    "model_probe": {
                        "status": "observed",
                        "session_identity": {"status": "known"},
                    }
                }
            },
        }

        with patch("valp_cli.workflow.time.sleep") as sleep:
            with patch(
                "valp_cli.workflow.collect_runtime_preflight",
                return_value=observed,
            ) as collect:
                result = await_owned_session_model_preflight(
                    ["codex"],
                    "herdr",
                    {"codex": {"runtime_identity": {"pane_id": "pane-owned"}}},
                    initial,
                    max_attempts=3,
                )

        sleep.assert_called_once_with(0.25)
        collect.assert_called_once()
        self.assertEqual(
            result["checks"]["owned_session_model_readiness"],
            {"status": "pass", "attempts": 2, "pending_agents": []},
        )

    def test_owned_session_model_preflight_allows_startup_beyond_five_seconds(self) -> None:
        pending = {
            "checks": {},
            "agents": {
                "codex": {
                    "agent_status": "unknown",
                    "model_probe": {
                        "status": "unsupported",
                        "session_identity": {"status": "known"},
                    },
                }
            },
        }
        observed = {
            "checks": {},
            "agents": {
                "codex": {
                    "agent_status": "idle",
                    "model_probe": {
                        "status": "observed",
                        "session_identity": {"status": "known"},
                    },
                }
            },
        }

        with patch("valp_cli.workflow.time.sleep") as sleep:
            with patch(
                "valp_cli.workflow.collect_runtime_preflight",
                side_effect=([pending] * 20) + [observed],
            ) as collect:
                result = await_owned_session_model_preflight(
                    ["codex"],
                    "herdr",
                    {"codex": {"runtime_identity": {"pane_id": "pane-owned"}}},
                    pending,
                )

        self.assertEqual(sleep.call_count, 21)
        self.assertEqual(collect.call_count, 21)
        self.assertEqual(
            result["checks"]["owned_session_model_readiness"],
            {"status": "pass", "attempts": 22, "pending_agents": []},
        )

    def test_owned_session_model_preflight_keeps_launch_attestation_separate_from_runtime_observation(self) -> None:
        initial = {
            "checks": {},
            "agents": {
                "codex": {
                    "agent_status": "idle",
                    "model_probe": {
                        "status": "unsupported",
                        "session_identity": {"status": "known"},
                    },
                }
            },
        }
        binding = {
            "agent": "codex",
            "dispatch_eligible": True,
            "provisioned_at": "2026-08-06T00:00:00Z",
            "ownership": {"scope": "task", "task_id": "TASK-LAUNCH-OBSERVATION"},
            "launch": {
                "argv": [
                    "/test/bin/codex",
                    "-m",
                    "model-implementation-large",
                    "-c",
                    'model_reasoning_effort="medium"',
                ]
            },
            "runtime_identity": {
                "pane_id": "pane-owned",
                "terminal_id": "terminal-owned",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-owned",
                "token": "sha256:" + ("a" * 64),
            },
        }

        result = await_owned_session_model_preflight(
            ["codex"],
            "herdr",
            {"codex": binding},
            initial,
            max_attempts=1,
        )

        self.assertEqual(result["agents"]["codex"]["model_probe"]["status"], "unsupported")
        self.assertEqual(
            result["agents"]["codex"]["launch_attestation"]["model"],
            {
                "model_id": "model-implementation-large",
                "provider": "unknown",
                "reasoning_mode": "medium",
                "confidence": "low",
            },
        )
        self.assertEqual(result["agents"]["codex"]["launch_attestation"]["status"], "launch_attested")
        self.assertEqual(result["agents"]["codex"]["launch_attestation"]["attested_at"], "2026-08-06T00:00:00Z")
        self.assertEqual(
            result["checks"]["owned_session_model_readiness"],
            {"status": "warn", "attempts": 1, "pending_agents": ["codex"]},
        )

    def test_dynamic_model_gate_rejects_launch_attestation_without_runtime_observation(self) -> None:
        probe = {
            "schema_version": "valp-model-probe.v1",
            "status": "unsupported",
            "source": "HERDR metadata unsupported",
            "observed_at": "2026-08-06T00:00:00Z",
            "ttl_seconds": 3600,
            "model": {
                "model_id": "model-implementation-large",
                "provider": "unknown",
                "reasoning_mode": "medium",
                "confidence": "unknown",
            },
            "session_identity": {
                "status": "known",
                "token": "sha256:" + ("a" * 64),
                "source": "HERDR metadata unsupported",
                "generation": "1",
            },
        }
        agent_info = {
            "model_identity": {
                "provider": "codex",
                "declared_model": {
                    "model_id": "model-implementation-large",
                    "reasoning_mode": "medium",
                    "confidence": "high",
                },
            }
        }
        identity = workflow_module.model_identity_for(
            "codex",
            agent_info,
            {},
            runtime_probe=probe,
            evaluated_at="2026-08-06T00:01:00Z",
        )

        errors = workflow_module.dynamic_model_dispatch_errors(
            {
                "provider_matrix": {
                    "model_awareness": {"dynamic_discovery_required": True},
                    "providers": {"codex": {"model_identity": identity}},
                }
            },
            {"codex": agent_info},
            {"agent_capability_profiles": {}},
            {"agents": {"codex": {"model_probe": probe}}},
            [("codex", "implementer")],
            evaluated_at="2026-08-06T00:01:00Z",
            allow_session_rebinding=True,
        )

        self.assertEqual(
            errors,
            ["implementer:codex active model identity is not eligible at dispatch preflight"],
        )

    def test_launch_attestation_freshness_is_anchored_to_provisioning_time(self) -> None:
        binding = {
            "agent": "codex",
            "dispatch_eligible": True,
            "provisioned_at": "2026-08-06T00:00:00Z",
            "ownership": {"scope": "task", "task_id": "TASK-IMMUTABLE-ATTESTATION"},
            "launch": {"argv": ["/test/bin/codex", "-m", "model-implementation-large"]},
            "runtime_identity": {
                "pane_id": "pane-owned",
                "terminal_id": "terminal-owned",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-owned",
                "token": "sha256:" + ("c" * 64),
            },
        }
        unsupported_probe = {"status": "unsupported"}

        with patch("valp_cli.workflow.now_iso", return_value="2026-08-06T02:00:01Z"):
            attestation = workflow_module.launch_attestation_from_task_owned_binding(
                "codex",
                binding,
                unsupported_probe,
            )

        self.assertEqual(attestation["attested_at"], "2026-08-06T00:00:00Z")
        self.assertEqual(attestation["freshness"], "stale")
        self.assertEqual(attestation["age_seconds"], 7201)

    def test_owned_session_model_preflight_rejects_ambiguous_launch_model(self) -> None:
        initial = {
            "checks": {},
            "agents": {
                "codex": {
                    "agent_status": "idle",
                    "model_probe": {"status": "unsupported"},
                }
            },
        }
        binding = {
            "agent": "codex",
            "dispatch_eligible": True,
            "ownership": {"scope": "task", "task_id": "TASK-AMBIGUOUS-LAUNCH"},
            "launch": {"argv": ["/test/bin/codex", "-m", "model-a", "--model=model-b"]},
            "runtime_identity": {
                "pane_id": "pane-owned",
                "terminal_id": "terminal-owned",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-owned",
                "token": "sha256:" + ("b" * 64),
            },
        }

        result = await_owned_session_model_preflight(
            ["codex"],
            "herdr",
            {"codex": binding},
            initial,
            max_attempts=1,
        )

        self.assertEqual(result["agents"]["codex"]["model_probe"]["status"], "unsupported")
        self.assertEqual(
            result["checks"]["owned_session_model_readiness"],
            {"status": "warn", "attempts": 1, "pending_agents": ["codex"]},
        )

    def test_owned_session_readiness_does_not_stop_before_agent_idle(self) -> None:
        observed_but_starting = {
            "checks": {},
            "agents": {
                "claude": {
                    "agent_status": "done",
                    "model_probe": {
                        "status": "observed",
                        "session_identity": {"status": "known"},
                    },
                }
            },
        }
        ready = {
            "checks": {},
            "agents": {
                "claude": {
                    "agent_status": "idle",
                    "model_probe": {
                        "status": "observed",
                        "session_identity": {"status": "known"},
                    },
                }
            },
        }

        with patch("valp_cli.workflow.time.sleep") as sleep:
            with patch(
                "valp_cli.workflow.collect_runtime_preflight",
                return_value=ready,
            ) as collect:
                result = await_owned_session_model_preflight(
                    ["claude"],
                    "herdr",
                    {"claude": {"runtime_identity": {"pane_id": "pane-owned"}}},
                    observed_but_starting,
                    max_attempts=3,
                )

        sleep.assert_called_once_with(0.25)
        collect.assert_called_once()
        self.assertEqual(result["agents"]["claude"]["agent_status"], "idle")
        self.assertEqual(
            result["checks"]["owned_session_model_readiness"],
            {"status": "pass", "attempts": 2, "pending_agents": []},
        )

    def test_owned_session_model_preflight_accepts_verified_bootstrap_done_state(self) -> None:
        verified_bootstrap = {
            "checks": {},
            "agents": {
                "codex": {
                    "agent_status": "done",
                    "model_probe": {
                        "status": "observed",
                        "session_identity": {"status": "known"},
                    },
                }
            },
        }
        binding = {
            "lifecycle": "bootstrap_ready",
            "bootstrap_verification": {
                "status": "verified",
                "evidence_ref": "evidence/bootstrap-probe-result.json",
                "generation": 1,
                "pane_id": "pane-owned",
                "native_session_id": "session-native",
                "expected_response": "BOOTSTRAP_READY",
                "actual_response": "BOOTSTRAP_READY",
                "native_turn_error": None,
                "session_identity_status": "known",
                "model_probe_status": "observed",
            },
            "generation": 1,
            "runtime_identity": {"pane_id": "pane-owned"},
        }

        result = await_owned_session_model_preflight(
            ["codex"],
            "herdr",
            {"codex": binding},
            verified_bootstrap,
            max_attempts=1,
        )

        self.assertEqual(
            result["checks"]["owned_session_model_readiness"],
            {"status": "pass", "attempts": 1, "pending_agents": []},
        )

    def test_owned_session_model_preflight_rejects_unverified_done_state(self) -> None:
        done_without_bootstrap = {
            "checks": {},
            "agents": {
                "codex": {
                    "agent_status": "done",
                    "model_probe": {
                        "status": "observed",
                        "session_identity": {"status": "known"},
                    },
                }
            },
        }

        result = await_owned_session_model_preflight(
            ["codex"],
            "herdr",
            {
                "codex": {
                    "generation": 1,
                    "lifecycle": "provisioned",
                    "runtime_identity": {"pane_id": "pane-owned"},
                }
            },
            done_without_bootstrap,
            max_attempts=1,
        )

        self.assertEqual(
            result["checks"]["owned_session_model_readiness"],
            {"status": "warn", "attempts": 1, "pending_agents": ["codex"]},
        )

    def test_records_verified_idle_bootstrap_against_exact_native_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            task_id = "TASK-VERIFIED-BOOTSTRAP"
            pane_id = "workspace-owned:p1"
            projection = {
                "schema_version": "valp-agent-sessions.v1",
                "task_id": task_id,
                "adapter": "herdr",
                "status": "ready",
                "bindings": {
                    "codex": {
                        "agent": "codex",
                        "generation": 1,
                        "ownership": {"scope": "task", "task_id": task_id},
                        "runtime_identity": {
                            "pane_id": pane_id,
                            "token": "sha256:" + ("a" * 64),
                        },
                        "lifecycle": "provisioned",
                        "dispatch_eligible": True,
                    }
                },
            }
            (directory / "agent-sessions.json").write_text(
                json.dumps(projection),
                encoding="utf-8",
            )
            (directory / "agent-session-receipts.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "valp-agent-session-receipt.v1",
                        "adapter": "herdr",
                        "task_id": task_id,
                        "event_sequence": 1,
                        "event": "agent_session_provisioned",
                        "agent": "codex",
                        "generation": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            evidence_ref = "evidence/bootstrap-probe-result.json"
            evidence_path = directory / evidence_ref
            evidence_path.parent.mkdir()
            evidence_path.write_text(
                json.dumps(
                    {
                        "schema_version": "valp-bootstrap-probe-result.v1",
                        "task_id": task_id,
                        "target": {
                            "agent": "codex",
                            "pane_id": pane_id,
                            "generation": 1,
                            "native_session_id": "session-native",
                        },
                        "classification": "non_task_bootstrap_probe",
                        "native_turn": {
                            "expected_response": "BOOTSTRAP_READY",
                            "actual_response": "BOOTSTRAP_READY",
                            "error": None,
                            "model": "model-implementation-large",
                            "provider": "provider-relay",
                            "reasoning_mode": "medium",
                            "completed_turn_count": 2,
                            "aborted_turn_count": 0,
                        },
                        "runtime_after": {
                            "agent_status": "idle",
                            "readiness_status": "ready",
                            "prompt_eligible": True,
                            "session_identity_status": "known",
                            "model_probe_status": "observed",
                        },
                        "structured_observation": {
                            "session_id": "session-native",
                            "model_id": "model-implementation-large",
                            "provider": "provider-relay",
                            "reasoning_mode": "medium",
                            "task_complete_timestamps": [
                                "2026-08-07T12:10:41.468Z",
                                "2026-08-07T12:11:47.189Z",
                            ],
                        },
                        "formal_dispatch_count": 0,
                        "accepted": True,
                    }
                ),
                encoding="utf-8",
            )

            updated = workflow_module.record_verified_bootstrap_lifecycle(
                directory,
                "codex",
                evidence_ref,
            )

            binding = updated["bindings"]["codex"]
            self.assertEqual(binding["lifecycle"], "bootstrap_ready")
            self.assertEqual(
                binding["bootstrap_verification"],
                {
                    "status": "verified",
                    "evidence_ref": evidence_ref,
                    "generation": 1,
                    "pane_id": pane_id,
                    "native_session_id": "session-native",
                    "expected_response": "BOOTSTRAP_READY",
                    "actual_response": "BOOTSTRAP_READY",
                    "native_turn_error": None,
                    "session_identity_status": "known",
                    "model_probe_status": "observed",
                },
            )
            receipts = [
                json.loads(line)
                for line in (directory / "agent-session-receipts.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(receipts[-1]["event"], "agent_session_bootstrap_verified")
            self.assertEqual(receipts[-1]["evidence_ref"], evidence_ref)

    def test_record_verified_bootstrap_rejects_mismatched_evidence(self) -> None:
        def use_atomic_response_proof(
            evidence: dict[str, object], *, raw_line: str, envelope: str
        ) -> None:
            evidence["structured_observation"]["task_complete_timestamps"] = []
            evidence["runtime_before"] = {"state_change_seq": 10}
            evidence["runtime_after"]["state_change_seq"] = 12
            evidence["response_proof"] = {
                "source": "HERDR pane wait-output renderer-aware anchored exact-line match",
                "authority": "response_only_not_identity_or_model",
                "raw_matched_line": raw_line,
                "renderer_envelope": envelope,
            }

        mutations = {
            "missing_task_complete": lambda evidence: evidence[
                "structured_observation"
            ].pop("task_complete_timestamps"),
            "turn_abort": lambda evidence: evidence["native_turn"].update(
                aborted_turn_count=1
            ),
            "response": lambda evidence: evidence["native_turn"].update(
                actual_response="OTHER"
            ),
            "renderer_line": lambda evidence: use_atomic_response_proof(
                evidence,
                raw_line="Load the prompt containing BOOTSTRAP_READY",
                envelope="codex_list_marker",
            ),
            "renderer_envelope": lambda evidence: use_atomic_response_proof(
                evidence,
                raw_line="• BOOTSTRAP_READY",
                envelope="arbitrary_marker",
            ),
            "native_session": lambda evidence: evidence[
                "structured_observation"
            ].update(session_id="other-session"),
            "model": lambda evidence: evidence["structured_observation"].update(
                model_id="other-model"
            ),
            "missing_model_identity": lambda evidence: (
                [
                    evidence["native_turn"].pop(key)
                    for key in ("model", "provider", "reasoning_mode")
                ],
                [
                    evidence["structured_observation"].pop(key)
                    for key in ("model_id", "provider", "reasoning_mode")
                ],
            ),
            "placeholder_model_identity": lambda evidence: (
                evidence["native_turn"].update(provider="unknown"),
                evidence["structured_observation"].update(provider="unknown"),
            ),
            "formal_dispatch": lambda evidence: evidence.update(formal_dispatch_count=1),
            "generation": lambda evidence: evidence["target"].update(generation=2),
            "pane_id": lambda evidence: evidence["target"].update(pane_id="other:p1"),
            "native_turn_error": lambda evidence: evidence["native_turn"].update(
                error={"code": "server_overloaded"}
            ),
            "classification": lambda evidence: evidence.update(classification="task_turn"),
        }

        for case, mutate in mutations.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                task_id = "TASK-REJECT-BOOTSTRAP"
                pane_id = "workspace-owned:p1"
                projection = {
                    "schema_version": "valp-agent-sessions.v1",
                    "task_id": task_id,
                    "adapter": "herdr",
                    "status": "ready",
                    "bindings": {
                        "codex": {
                            "agent": "codex",
                            "generation": 1,
                            "ownership": {"scope": "task", "task_id": task_id},
                            "runtime_identity": {
                                "pane_id": pane_id,
                                "token": "sha256:" + ("a" * 64),
                            },
                            "lifecycle": "provisioned",
                            "dispatch_eligible": True,
                        }
                    },
                }
                (directory / "agent-sessions.json").write_text(
                    json.dumps(projection), encoding="utf-8"
                )
                (directory / "agent-session-receipts.jsonl").write_text(
                    "", encoding="utf-8"
                )
                evidence_ref = "evidence/bootstrap-probe-result.json"
                evidence_path = directory / evidence_ref
                evidence_path.parent.mkdir()
                evidence = {
                    "schema_version": "valp-bootstrap-probe-result.v1",
                    "task_id": task_id,
                    "target": {
                        "agent": "codex",
                        "pane_id": pane_id,
                        "generation": 1,
                        "native_session_id": "session-native",
                    },
                    "classification": "non_task_bootstrap_probe",
                    "native_turn": {
                        "expected_response": "BOOTSTRAP_READY",
                        "actual_response": "BOOTSTRAP_READY",
                        "error": None,
                        "model": "model-implementation-large",
                        "provider": "provider-relay",
                        "reasoning_mode": "medium",
                        "completed_turn_count": 1,
                        "aborted_turn_count": 0,
                    },
                    "runtime_after": {
                        "agent_status": "idle",
                        "readiness_status": "ready",
                        "prompt_eligible": True,
                        "session_identity_status": "known",
                        "model_probe_status": "observed",
                    },
                    "structured_observation": {
                        "session_id": "session-native",
                        "model_id": "model-implementation-large",
                        "provider": "provider-relay",
                        "reasoning_mode": "medium",
                        "task_complete_timestamps": ["2026-08-07T12:10:41.468Z"],
                    },
                    "formal_dispatch_count": 0,
                    "accepted": True,
                }
                mutate(evidence)
                evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

                with self.assertRaisesRegex(
                    HerdrSubmissionError,
                    "does not match the exact task-owned session",
                ):
                    workflow_module.record_verified_bootstrap_lifecycle(
                        directory, "codex", evidence_ref
                    )

                unchanged = json.loads(
                    (directory / "agent-sessions.json").read_text(encoding="utf-8")
                )
                self.assertEqual(unchanged["bindings"]["codex"]["lifecycle"], "provisioned")
                self.assertEqual(
                    (directory / "agent-session-receipts.jsonl").read_text(encoding="utf-8"),
                    "",
                )

    def test_runtime_retry_accepts_unknown_owned_session_model_readiness(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "iteration-budget.json").write_text(
                json.dumps(
                    {
                        "status": "blocked",
                        "stop_reason": "dynamic model identity changed after routing",
                    }
                ),
                encoding="utf-8",
            )
            (directory / "runtime-preflight.json").write_text(
                json.dumps(
                    {
                        "agents": {
                            "codex": {
                                "session_binding": {"status": "bound"},
                                "model_probe": {"status": "unsupported"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (directory / "model-identity-dispatch-block.json").write_text(
                json.dumps({"status": "blocked", "errors": ["model is not eligible"]}),
                encoding="utf-8",
            )

            self.assertTrue(
                runtime_dispatch_retry_pending(
                    directory,
                    {"status": "dispatching"},
                    "herdr",
                )
            )

    def test_runtime_retry_accepts_legacy_pre_delivery_failure_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for reason in (
                "runtime session provisioning failure",
                "runtime preflight failure",
            ):
                with self.subTest(reason=reason):
                    (directory / "iteration-budget.json").write_text(
                        json.dumps({"status": "blocked", "stop_reason": reason}),
                        encoding="utf-8",
                    )
                    self.assertTrue(
                        runtime_dispatch_retry_pending(
                            directory,
                            {"status": "dispatching"},
                            "herdr",
                        )
                    )

    def test_runtime_retry_accepts_route_time_preflight_block_before_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "iteration-budget.json").write_text(
                json.dumps(
                    {
                        "status": "blocked",
                        "stop_reason": "task status is blocked",
                        "usage": {"dispatches": 0},
                    }
                ),
                encoding="utf-8",
            )
            (directory / "runtime-preflight.json").write_text(
                json.dumps({"status": "fail"}),
                encoding="utf-8",
            )

            self.assertTrue(
                runtime_dispatch_retry_pending(
                    directory,
                    {"task_id": "TASK", "status": "blocked"},
                    "herdr",
                )
            )

    def test_runtime_retry_preserves_original_state_preflight_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "iteration-budget.json").write_text(
                json.dumps(
                    {
                        "status": "blocked",
                        "stop_reason": "task status is blocked",
                        "usage": {"dispatches": 0},
                    }
                ),
                encoding="utf-8",
            )
            (directory / "runtime-preflight.json").write_text(
                json.dumps({"status": "warn"}), encoding="utf-8"
            )
            (directory / "routing.json").write_text(
                json.dumps({"runtime_adapter": {"preflight": {"status": "warn"}}}),
                encoding="utf-8",
            )
            state = {
                "task_id": "TASK",
                "status": "blocked",
                "runtime_adapter": {"preflight": {"status": "fail"}},
            }

            self.assertTrue(
                runtime_dispatch_retry_pending(directory, state, "herdr")
            )

    def test_runtime_retry_accepts_orphaned_pre_delivery_warn_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "iteration-budget.json").write_text(
                json.dumps(
                    {
                        "status": "blocked",
                        "stop_reason": "task status is blocked",
                        "usage": {"dispatches": 0},
                    }
                ),
                encoding="utf-8",
            )
            (directory / "runtime-preflight.json").write_text(
                json.dumps({"status": "warn"}), encoding="utf-8"
            )
            (directory / "routing.json").write_text(
                json.dumps({"runtime_adapter": {"preflight": {"status": "warn"}}}),
                encoding="utf-8",
            )
            (directory / "assignment-validation.json").write_text(
                json.dumps({"status": "pass"}), encoding="utf-8"
            )
            state = {
                "task_id": "TASK",
                "status": "blocked",
                "gates": {"approval": "not_required"},
                "capabilities_missing": [],
                "runtime_adapter": {"preflight": {"status": "warn"}},
            }

            self.assertTrue(runtime_dispatch_retry_pending(directory, state, "herdr"))
            state["gates"]["approval"] = "blocked"
            self.assertFalse(runtime_dispatch_retry_pending(directory, state, "herdr"))

    def test_runtime_retry_rejects_route_time_block_after_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "iteration-budget.json").write_text(
                json.dumps(
                    {
                        "status": "blocked",
                        "stop_reason": "task status is blocked",
                        "usage": {"dispatches": 0},
                    }
                ),
                encoding="utf-8",
            )
            (directory / "runtime-preflight.json").write_text(
                json.dumps({"status": "fail"}),
                encoding="utf-8",
            )
            (directory / "dispatch-receipts.jsonl").write_text(
                json.dumps(
                    {
                        "task_id": "TASK",
                        "event": "dispatch_submitted",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertFalse(
                runtime_dispatch_retry_pending(
                    directory,
                    {"task_id": "TASK", "status": "blocked"},
                    "herdr",
                )
            )

    def test_route_time_preflight_recovery_resumes_dispatching_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state = {
                "task_id": "TASK",
                "status": "blocked",
                "gates": {},
            }
            budget = {
                "status": "blocked",
                "stop_reason": "task status is blocked",
                "usage": {
                    "dispatches": 0,
                    "dispatch_reference_tokens": 0,
                    "reroutes": 0,
                    "fix_review_rounds": 0,
                },
                "max_dispatches": 3,
                "max_dispatch_reference_tokens": 1000,
                "max_reroutes": 1,
                "max_fix_review_rounds": 1,
            }
            (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (directory / "iteration-budget.json").write_text(
                json.dumps(budget), encoding="utf-8"
            )
            (directory / "runtime-preflight.json").write_text(
                json.dumps({"status": "fail"}), encoding="utf-8"
            )

            resumed = workflow_module.resume_runtime_dispatch_retry(
                directory,
                {"dispatch_payload_budgets": {}},
                [("codex", "implementer")],
                expected_stop_reason="task status is blocked",
            )

            self.assertEqual(read_json(directory / "state.json")["status"], "dispatching")
            self.assertEqual(resumed["status"], "active")
            self.assertIsNone(resumed["stop_reason"])

    def test_runtime_retry_accepts_resumed_executing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "iteration-budget.json").write_text(
                json.dumps({
                    "status": "blocked",
                    "stop_reason": "runtime dispatch failure",
                }),
                encoding="utf-8",
            )

            self.assertTrue(
                runtime_dispatch_retry_pending(
                    directory,
                    {"status": "executing"},
                    "herdr",
                    [("claude", "reviewer")],
                )
            )

    def test_herdr_route_defers_model_gate_until_owned_session_preflight(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "review", "risk_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates and reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "warn",
            "checks": {
                "submission_transport": {"status": "pass", "mode": "pane_send_text_enter"},
                "session_provisioning": {"status": "pass", "mode": "agent_start"},
            },
            "agents": {
                "codex": {
                    "status": "warn",
                    "pane_id": None,
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "unavailable",
                        "model": {
                            "model_id": "unknown",
                            "provider": "unknown",
                            "reasoning_mode": "unknown",
                            "confidence": "unknown",
                        },
                        "session_identity": {
                            "status": "unknown",
                            "token": "unknown",
                            "source": "No current pane",
                            "generation": "unknown",
                        },
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-HERDR-NO-USER-PANE"
            publish_task(
                root,
                task_id,
                "Coordinate and review a bounded runtime check.",
                profile="generic-analysis",
                runtime="herdr",
            )
            declaration = {
                "schema_version": "valp-assignment-declaration.v1",
                "declaration_id": "test-no-user-pane",
                "task_id": task_id,
                "declared_at": "2026-07-25T10:00:00Z",
                "leader": {
                    "agent_id": "codex",
                    "selected_by": "user",
                    "selection_ref": "test-user-selection:no-user-pane",
                },
                "assignments": {"coordinator": "codex", "reviewer": "codex"},
                "reasons": {
                    "coordinator": "User-selected test Leader.",
                    "reviewer": "Declared bounded reviewer.",
                },
            }
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                    with patch("valp_cli.workflow.skill_router_command", return_value=None):
                        routing = route_task(
                            root,
                            task_id,
                            runtime="herdr",
                            assignment_declaration=declaration,
                        )

            self.assertEqual(routing["assignment_validation"]["status"], "pass")
            self.assertEqual(routing["model_role_gate"]["status"], "pass")
            self.assertIn("task-owned session", routing["model_role_gate"]["reason"])
            self.assertTrue(
                routing["provider_matrix"]["model_awareness"]["dynamic_discovery_required"]
            )

    def model_aware_test_preflight(self, preflight: dict) -> dict:
        if preflight.get("adapter_class") == "manual":
            return preflight
        observed_at = datetime.now().astimezone().isoformat()
        for agent, record in (preflight.get("agents") or {}).items():
            if not isinstance(record, dict):
                continue
            probe = record.get("model_probe") or {}
            if probe.get("status") == "observed":
                continue
            session_digest = hashlib.sha256(f"test-session:{agent}".encode("utf-8")).hexdigest()
            record["model_probe"] = {
                "schema_version": "valp-model-probe.v1",
                "status": "observed",
                "source": "explicit workflow test fixture",
                "observed_at": observed_at,
                "ttl_seconds": 3600,
                "model": {
                    "model_id": f"test-model-{agent}",
                    "provider": "test-provider",
                    "reasoning_mode": "high",
                    "confidence": "high",
                },
                "session_identity": {
                    "status": "known",
                    "token": f"sha256:{session_digest}",
                    "source": "explicit workflow test fixture",
                    "generation": "1",
                },
            }
        return preflight

    def routed_test_preflight(self, task_dir: Path) -> dict:
        return read_json(task_dir / "routing.json")["provider_matrix"]["runtime_preflight"]

    def owned_session_projection(
        self,
        task_id: str,
        pane_id: str = "pane-1",
        *,
        lifecycle: str = "reused",
    ) -> dict:
        return {
            "schema_version": "valp-agent-sessions.v1",
            "task_id": task_id,
            "adapter": "herdr",
            "status": "ready",
            "bindings": {
                "codex": {
                    "agent": "codex",
                    "session_name": "valp-test-codex",
                    "generation": 1,
                    "ownership": {
                        "scope": "task",
                        "task_id": task_id,
                        "project_identity": "sha256:test-project",
                    },
                    "context": {"cwd": "/test/project"},
                    "launch": {"argv": ["codex"]},
                    "runtime_scope": {
                        "kind": "workspace",
                        "ownership": "task",
                        "workspace_id": "workspace-owned",
                        "label": "valp-test-codex-g1",
                    },
                    "runtime_identity": {
                        "pane_id": pane_id,
                        "terminal_id": "terminal-owned",
                        "workspace_id": "workspace-owned",
                        "tab_id": "tab-owned",
                        "token": "sha256:test-owned-session",
                    },
                    "lifecycle": lifecycle,
                    "dispatch_eligible": True,
                }
            },
        }

    def assignment_declaration(
        self,
        root: Path,
        task_id: str,
        profile: str,
        include_agents: list[str] | None = None,
    ) -> dict:
        capabilities = workflow_module.load_local_capabilities(root)
        agents = capabilities.get("agents") or {}
        roles = list(
            workflow_module.PROFILE_ROLE_REQUIREMENTS.get(
                profile,
                workflow_module.PROFILE_ROLE_REQUIREMENTS["generic-analysis"],
            )
        )
        assignments: dict[str, str] = {}
        active_agents = [name for name, info in agents.items() if bool(info.get("active", True))]
        for role in roles:
            assignments[role] = max(
                active_agents,
                key=lambda name: workflow_module.role_fit_score(agents[name], role),
            )
        for agent in include_agents or []:
            role = workflow_module.inferred_primary_role(agents.get(agent) or {})
            if role in {"coordinator", "implementer", "reviewer", "prototype", "researcher"}:
                assignments[role] = agent
        leader = assignments.get("coordinator") or active_agents[0]
        assignments.setdefault("coordinator", leader)
        return {
            "schema_version": "valp-assignment-declaration.v1",
            "declaration_id": f"test-declaration-{task_id}",
            "task_id": task_id,
            "declared_at": "2026-07-23T10:00:00Z",
            "leader": {
                "agent_id": leader,
                "selected_by": "user",
                "selection_ref": f"test-user-selection:{task_id}",
            },
            "assignments": assignments,
            "reasons": {
                role: "Explicit test Leader assignment from fixture capability evidence."
                for role in assignments
            },
        }

    def publish_routed_task(
        self,
        root: Path,
        task_id: str,
        prompt: str,
        profile: str | None = None,
        runtime: str | None = None,
        include_agents: list[str] | None = None,
        **_ignored: object,
    ) -> Path:
        selected_profile = profile or classify_profile(prompt)
        directory = publish_task(
            root,
            task_id,
            prompt,
            profile=selected_profile,
            runtime=runtime,
        )
        collect_preflight = workflow_module.collect_runtime_preflight

        def collect_model_aware_preflight(
            agent_names=None,
            runtime=None,
            launch_argv_by_agent=None,
            version_command_by_agent=None,
        ):
            return self.model_aware_test_preflight(
                collect_preflight(
                    agent_names,
                    runtime=runtime,
                    launch_argv_by_agent=launch_argv_by_agent,
                    version_command_by_agent=version_command_by_agent,
                )
            )

        with patch("valp_cli.workflow.collect_runtime_preflight", side_effect=collect_model_aware_preflight):
            route_task(
                root,
                task_id,
                runtime=runtime,
                assignment_declaration=self.assignment_declaration(
                    root,
                    task_id,
                    selected_profile,
                    include_agents,
                ),
            )
        return directory

    def test_qwen_cli_preflight_uses_version_probe(self) -> None:
        result = {
            "ok": True,
            "exit_code": 0,
            "stdout": "0.20.1\n",
            "stderr": "",
        }
        with patch("valp_cli.workflow.shutil.which", return_value="/opt/example/bin/qwen"):
            with patch("valp_cli.workflow.run_command", return_value=result) as run:
                preflight = workflow_module.cli_preflight_for_agent(
                    "qwen",
                    launch_argv=["qwen"],
                    version_command=["qwen", "version"],
                )

        run.assert_called_once_with(["qwen", "version"], timeout=5.0)
        self.assertEqual(preflight["status"], "pass")
        self.assertEqual(preflight["version_output"], "0.20.1")

    def test_herdr_preflight_addresses_generic_agent_and_binds_structured_model(self) -> None:
        pane = {
            "pane_id": "example-workspace:example-qwen",
            "agent": "qwen",
            "agent_status": "idle",
            "model_id": "qwen-example-model",
        }
        pane_list_stdout_limits: list[object] = []

        def command_result(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "herdr agent start <name> -- <argv...>\nherdr agent prompt\nherdr agent wait\n",
                    "stderr": "",
                }
            if command[1:] == ["workspace", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "herdr workspace create [--cwd PATH] [--no-focus]\n",
                    "stderr": "",
                }
            if command[1:] == ["pane", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "herdr pane move --new-tab\nherdr pane send-text\nherdr pane send-keys\n",
                    "stderr": "",
                }
            if command[1:] == ["status", "--json"]:
                payload = {
                    "client": {"version": "0.7.4"},
                    "server": {"version": "0.7.4", "restart_needed": False},
                }
            elif command[1:] == ["pane", "list"]:
                pane_list_stdout_limits.append(_kwargs.get("stdout_limit"))
                payload = {"result": {"panes": [pane]}}
            elif command[1:4] == ["pane", "process-info", "--pane"]:
                payload = {
                    "result": {
                        "process_info": {"foreground_process_group_id": 4242}
                    }
                }
            elif command[1:4] == ["pane", "layout", "--pane"]:
                payload = {
                    "result": {
                        "layout": {
                            "panes": [
                                {
                                    "pane_id": "example-workspace:example-qwen",
                                    "rect": {"width": 80, "height": 30},
                                }
                            ]
                        }
                    }
                }
            elif command[1:3] == ["agent", "readiness"]:
                payload = {
                    "result": {
                        "type": "agent_readiness",
                        "readiness": {
                            "schema_version": "valp-named-agent-readiness.v1",
                            "ready": True,
                            "reason_code": "ready",
                            "addressable": True,
                            "detected_agent": "qwen",
                            "agent_status": "idle",
                            "interactive_ready": True,
                            "prompt_eligible": True,
                            "session_identity": {
                                "status": "known",
                                "identity": {
                                    "source": "herdr:qwen",
                                    "agent": "qwen",
                                    "kind": "id",
                                    "value": "session-qwen",
                                },
                            },
                            "state_change_seq": 1,
                        },
                    }
                }
            elif command[1:3] == ["agent", "model-probe"]:
                payload = {
                    "result": {
                        "type": "agent_model_probe",
                        "probe": {
                            "schema_version": "valp-model-probe.v1",
                            "status": "observed",
                            "source": "herdr:qwen",
                            "observed_at": "2026-08-06T04:00:00Z",
                            "ttl_seconds": 3600,
                            "model": {
                                "model_id": "qwen-example-model",
                                "provider": "unknown",
                                "reasoning_mode": "unknown",
                                "confidence": "high",
                            },
                            "session_identity": {
                                "status": "known",
                                "token": "sha256:qwen-session",
                                "source": "herdr:qwen",
                                "generation": "session:qwen",
                            },
                        },
                    }
                }
            elif command == ["qwen", "version"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "0.20.1\n",
                    "stderr": "",
                }
            else:
                self.fail(f"unexpected command: {command}")
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps(payload),
                "stderr": "",
            }

        with patch(
            "valp_cli.workflow.shutil.which",
            side_effect=lambda name: f"/opt/example/bin/{name}",
        ):
            with patch("valp_cli.workflow.run_command", side_effect=command_result):
                preflight = workflow_module.collect_herdr_preflight(
                    ["qwen"],
                    launch_argv_by_agent={"qwen": ["qwen"]},
                    version_command_by_agent={"qwen": ["qwen", "version"]},
                )

        qwen = preflight["agents"]["qwen"]
        self.assertEqual(preflight["status"], "pass")
        self.assertEqual(qwen["pane_id"], "example-workspace:example-qwen")
        self.assertEqual(qwen["cli"]["version_output"], "0.20.1")
        self.assertEqual(qwen["model_probe"]["model"]["model_id"], "qwen-example-model")
        self.assertEqual(qwen["model_probe"]["model"]["provider"], "unknown")
        self.assertEqual(qwen["model_probe"]["session_identity"]["status"], "known")
        self.assertEqual(
            pane_list_stdout_limits,
            [workflow_module.HERDR_PANE_LIST_STDOUT_LIMIT],
        )

    def test_unbound_herdr_preflight_prefers_healthy_qwen_session_over_stale_session(self) -> None:
        stale_pane_id = "stale-workspace:p1"
        healthy_pane_id = "healthy-workspace:p1"
        panes = [
            {"pane_id": stale_pane_id, "agent": "qwen", "agent_status": "unknown"},
            {"pane_id": healthy_pane_id, "agent": "qwen", "agent_status": "idle"},
        ]

        def command_result(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent start <name> -- <argv...>\nherdr agent prompt\nherdr agent wait\n"
            elif command[1:] == ["workspace", "--help"]:
                stdout = "herdr workspace create [--cwd PATH] [--no-focus]\n"
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane move --new-tab\nherdr pane send-text\nherdr pane send-keys\n"
            elif command[1:] == ["status", "--json"]:
                stdout = json.dumps({
                    "client": {"version": "0.7.4"},
                    "server": {"version": "0.7.4", "restart_needed": False},
                })
            elif command[1:] == ["pane", "list"]:
                stdout = json.dumps({"result": {"panes": panes}})
            elif command[1:3] == ["agent", "model-probe"]:
                pane_id = command[3]
                observed = pane_id == healthy_pane_id
                stdout = json.dumps({"result": {"type": "agent_model_probe", "probe": {
                    "schema_version": "valp-model-probe.v1",
                    "status": "observed" if observed else "unavailable",
                    "source": "herdr:qwen",
                    "observed_at": "2026-08-09T16:00:00Z" if observed else None,
                    "ttl_seconds": 3600,
                    "model": {"model_id": "model-research-large", "provider": "qwen-code"} if observed else None,
                    "session_identity": {"status": "known" if observed else "unknown"},
                }}})
            elif command[1:3] == ["agent", "readiness"]:
                pane_id = command[3]
                ready = pane_id == healthy_pane_id
                stdout = json.dumps({"result": {"type": "agent_readiness", "readiness": {
                    "schema_version": "valp-named-agent-readiness.v1",
                    "ready": ready,
                    "reason_code": "ready" if ready else "agent_not_addressable",
                    "addressable": ready,
                    "detected_agent": "qwen" if ready else None,
                    "agent_status": "idle" if ready else None,
                    "interactive_ready": ready,
                    "prompt_eligible": ready,
                    "session_identity": {"status": "known" if ready else "unknown"},
                    "state_change_seq": 1,
                }}})
            elif command[1:4] == ["pane", "process-info", "--pane"]:
                stdout = json.dumps({"result": {"process_info": {"foreground_process_group_id": 4242}}})
            elif command[1:4] == ["pane", "layout", "--pane"]:
                pane_id = command[4]
                stdout = json.dumps({"result": {"layout": {"panes": [
                    {"pane_id": pane_id, "rect": {"width": 80, "height": 30}}
                ]}}})
            elif command == ["qwen", "version"]:
                return {"ok": True, "exit_code": 0, "stdout": "0.21.8\n", "stderr": ""}
            else:
                self.fail(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        with patch("valp_cli.workflow.activated_herdr_executable", return_value="/test/herdr"):
            with patch("valp_cli.workflow.shutil.which", return_value="/test/qwen"):
                with patch("valp_cli.workflow.run_command", side_effect=command_result):
                    preflight = workflow_module.collect_herdr_preflight(
                        ["qwen"],
                        launch_argv_by_agent={"qwen": ["qwen"]},
                        version_command_by_agent={"qwen": ["qwen", "version"]},
                    )

        qwen = preflight["agents"]["qwen"]
        self.assertEqual(preflight["status"], "pass")
        self.assertEqual(qwen["status"], "pass")
        self.assertEqual(qwen["pane_id"], healthy_pane_id)
        self.assertTrue(qwen["readiness"]["ready"])
        self.assertEqual(qwen["model_probe"]["status"], "observed")
        self.assertEqual(len(qwen["sessions"]), 2)
        self.assertEqual([record["status"] for record in qwen["sessions"]], ["fail", "pass"])

    def test_herdr_preflight_accepts_verified_bootstrap_done_state(self) -> None:
        task_id = "TASK-VERIFIED-BOOTSTRAP"
        pane_id = "workspace-owned:p1"
        pane = {
            "pane_id": pane_id,
            "terminal_id": "terminal-owned",
            "workspace_id": "workspace-owned",
            "tab_id": "tab-owned",
            "agent": "codex",
            "agent_status": "done",
            "cwd": "/test/project",
        }
        binding = {
            "agent": "codex",
            "generation": 1,
            "ownership": {
                "scope": "task",
                "task_id": task_id,
                "project_identity": "sha256:test-project",
            },
            "context": {"cwd": "/test/project"},
            "launch": {
                "argv": [
                    "/test/bin/codex",
                    "-m",
                    "model-implementation-large",
                    "-c",
                    'model_reasoning_effort="medium"',
                ]
            },
            "runtime_identity": {
                "pane_id": pane_id,
                "terminal_id": "terminal-owned",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-owned",
                "token": "sha256:test-owned-session",
            },
            "lifecycle": "bootstrap_ready",
            "bootstrap_verification": {
                "status": "verified",
                "evidence_ref": "evidence/bootstrap-probe-result.json",
                "generation": 1,
                "pane_id": pane_id,
                "native_session_id": "session-native",
                "expected_response": "BOOTSTRAP_READY",
                "actual_response": "BOOTSTRAP_READY",
                "native_turn_error": None,
                "session_identity_status": "known",
                "model_probe_status": "observed",
            },
        }

        def command_result(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["status", "--json"]:
                payload = {
                    "client": {"version": "0.8.0"},
                    "server": {"version": "0.8.0", "restart_needed": False},
                }
            elif command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                payload = {"result": {"panes": [pane]}}
            elif command[1:4] == ["pane", "layout", "--pane"]:
                payload = {
                    "result": {
                        "layout": {
                            "panes": [
                                {
                                    "pane_id": pane_id,
                                    "rect": {"width": 80, "height": 30},
                                }
                            ]
                        }
                    }
                }
            else:
                self.fail(f"unexpected command: {command}")
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps(payload),
                "stderr": "",
            }

        readiness = {
            "schema_version": "valp-named-agent-readiness.v1",
            "ready": True,
            "reason_code": "ready",
            "addressable": True,
            "detected_agent": "codex",
            "agent_status": "done",
            "interactive_ready": True,
            "prompt_eligible": True,
            "session_identity": {"status": "known", "identity": {"value": "session-native"}},
            "state_change_seq": 2,
        }
        model_probe = {
            "schema_version": "valp-model-probe.v1",
            "status": "observed",
            "observed_at": workflow_module.now_iso(),
            "ttl_seconds": 3600,
            "model": {
                "model_id": "model-implementation-large",
                "provider": "provider-relay",
                "reasoning_mode": "medium",
                "confidence": "high",
            },
            "session_identity": {"status": "known", "generation": "session-native"},
        }

        with patch("valp_cli.workflow.shutil.which", return_value="/opt/example/bin/herdr"):
            with patch("valp_cli.workflow.run_command", side_effect=command_result):
                with patch(
                    "valp_cli.workflow.detect_herdr_submission_capability",
                    return_value={"status": "pass"},
                ):
                    with patch(
                        "valp_cli.workflow.detect_herdr_session_provisioning_capability",
                        return_value={"status": "pass"},
                    ):
                        with patch("valp_cli.workflow.herdr_named_agent_readiness", return_value=readiness):
                            with patch("valp_cli.workflow.herdr_model_probe", return_value=model_probe):
                                with patch(
                                    "valp_cli.workflow.cli_preflight_for_agent",
                                    return_value={"status": "pass"},
                                ):
                                    verified_preflight = workflow_module.collect_herdr_preflight(
                                        ["codex"],
                                        session_bindings={"codex": binding},
                                    )
                                    mismatched_probe = {
                                        **model_probe,
                                        "model": {**model_probe["model"], "model_id": "other-model"},
                                    }
                                    with patch(
                                        "valp_cli.workflow.herdr_model_probe",
                                        return_value=mismatched_probe,
                                    ):
                                        mismatched_preflight = workflow_module.collect_herdr_preflight(
                                            ["codex"],
                                            session_bindings={"codex": binding},
                                        )
                                    binding["lifecycle"] = "provisioned"
                                    binding.pop("bootstrap_verification")
                                    unverified_preflight = workflow_module.collect_herdr_preflight(
                                        ["codex"],
                                        session_bindings={"codex": binding},
                                    )

        self.assertEqual(verified_preflight["status"], "pass")
        self.assertEqual(verified_preflight["agents"]["codex"]["status"], "pass")
        self.assertEqual(mismatched_preflight["status"], "fail")
        self.assertIn(
            "current structured model observation does not match the task-owned binding",
            mismatched_preflight["agents"]["codex"]["notes"],
        )
        self.assertEqual(unverified_preflight["status"], "fail")
        self.assertEqual(unverified_preflight["agents"]["codex"]["status"], "fail")

    def test_herdr_preflight_accepts_observed_model_for_provider_managed_launch(self) -> None:
        task_id = "TASK-PROVIDER-MANAGED-BOOTSTRAP"
        pane_id = "workspace-owned:p1"
        binding = {
            "agent": "claude",
            "generation": 1,
            "ownership": {
                "scope": "task",
                "task_id": task_id,
                "project_identity": "sha256:test-project",
            },
            "context": {"cwd": "/test/project"},
            "launch": {"argv": ["/test/bin/claude", "--dangerously-skip-permissions"]},
            "runtime_identity": {
                "pane_id": pane_id,
                "terminal_id": "terminal-owned",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-owned",
                "token": "sha256:test-owned-session",
            },
            "lifecycle": "bootstrap_ready",
            "bootstrap_verification": {
                "status": "verified",
                "evidence_ref": "evidence/bootstrap-probe-result.json",
                "generation": 1,
                "pane_id": pane_id,
                "native_session_id": "session-native",
                "expected_response": "BOOTSTRAP_READY",
                "actual_response": "BOOTSTRAP_READY",
                "native_turn_error": None,
                "session_identity_status": "known",
                "model_probe_status": "observed",
            },
        }
        readiness = {
            "schema_version": "valp-named-agent-readiness.v1",
            "ready": True,
            "reason_code": "ready",
            "addressable": True,
            "detected_agent": "claude",
            "agent_status": "idle",
            "interactive_ready": True,
            "prompt_eligible": True,
            "session_identity": {
                "status": "known",
                "identity": {"value": "session-native"},
            },
            "state_change_seq": 2,
        }
        model_probe = {
            "schema_version": "valp-model-probe.v1",
            "status": "observed",
            "observed_at": workflow_module.now_iso(),
            "ttl_seconds": 3600,
            "model": {
                "model_id": "model-review-large",
                "provider": "deepseek",
                "reasoning_mode": "low",
                "confidence": "high",
            },
            "session_identity": {"status": "known", "generation": "session-native"},
        }

        def command_result(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["status", "--json"]:
                payload = {
                    "client": {"version": "0.8.0"},
                    "server": {"version": "0.8.0", "restart_needed": False},
                }
            elif command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                payload = {
                    "result": {
                        "panes": [
                            {
                                "pane_id": pane_id,
                                "terminal_id": "terminal-owned",
                                "workspace_id": "workspace-owned",
                                "tab_id": "tab-owned",
                                "agent": "claude",
                                "agent_status": "idle",
                                "cwd": "/test/project",
                            }
                        ]
                    }
                }
            elif command[1:4] == ["pane", "layout", "--pane"]:
                payload = {
                    "result": {
                        "layout": {
                            "panes": [
                                {"pane_id": pane_id, "rect": {"width": 80, "height": 30}}
                            ]
                        }
                    }
                }
            else:
                self.fail(f"unexpected command: {command}")
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps(payload),
                "stderr": "",
            }

        with patch("valp_cli.workflow.shutil.which", return_value="/opt/example/bin/herdr"):
            with patch("valp_cli.workflow.run_command", side_effect=command_result):
                with patch(
                    "valp_cli.workflow.detect_herdr_submission_capability",
                    return_value={"status": "pass"},
                ):
                    with patch(
                        "valp_cli.workflow.detect_herdr_session_provisioning_capability",
                        return_value={"status": "pass"},
                    ):
                        with patch(
                            "valp_cli.workflow.herdr_named_agent_readiness",
                            return_value=readiness,
                        ):
                            with patch(
                                "valp_cli.workflow.herdr_model_probe",
                                return_value=model_probe,
                            ):
                                with patch(
                                    "valp_cli.workflow.cli_preflight_for_agent",
                                    return_value={"status": "pass"},
                                ):
                                    preflight = workflow_module.collect_herdr_preflight(
                                        ["claude"],
                                        session_bindings={"claude": binding},
                                    )

        self.assertEqual(preflight["status"], "pass")
        self.assertEqual(preflight["agents"]["claude"]["status"], "pass")

    def test_read_only_agent_is_never_scored_as_implementer(self) -> None:
        self.assertEqual(
            workflow_module.role_fit_score(
                {
                    "role": ["review", "code_review"],
                    "strengths": ["read-only review"],
                    "must_not_do": ["must not edit source"],
                },
                "implementer",
            ),
            0.0,
        )
        self.assertGreater(
            workflow_module.role_fit_score(
                {
                    "role": ["implementation", "verification"],
                    "strengths": ["edits files", "runs tests"],
                },
                "implementer",
            ),
            0.0,
        )

    def test_candidate_scores_are_advisory_capability_facts(self) -> None:
        agents = {
            "coordinator-reviewer": {"active": True, "role": ["coordination", "review"]},
            "implementer-reviewer": {"active": True, "role": ["implementation", "review"]},
            "specialist-reviewer": {"active": True, "role": ["review"]},
        }
        scores = {
            "coordinator-reviewer": {
                "overall": 0.82,
                "role_fit": {"coordinator": 0.9, "implementer": 0.25, "reviewer": 0.75},
            },
            "implementer-reviewer": {
                "overall": 0.84,
                "role_fit": {"coordinator": 0.25, "implementer": 0.9, "reviewer": 0.7},
            },
            "specialist-reviewer": {
                "overall": 0.95,
                "role_fit": {"coordinator": 0.25, "implementer": 0.25, "reviewer": 0.95},
            },
        }

        self.assertGreater(
            scores["specialist-reviewer"]["role_fit"]["reviewer"],
            scores["coordinator-reviewer"]["role_fit"]["reviewer"],
        )
        self.assertTrue(all("selected_agent" not in score for score in scores.values()))

    def test_legacy_herdr_receipts_translate_to_v2_work_item_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / ".herdr-loop" / "tasks" / "TASK-HERDR-TRANSLATION"
            task_dir.mkdir(parents=True)
            task_id = "TASK-HERDR-TRANSLATION"
            dependencies = build_submission_dependencies(
                task_id,
                {"coordinator": "hermes", "implementer": "codex", "reviewer": "claude"},
            )
            (task_dir / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            (task_dir / "state.json").write_text(
                json.dumps({"schema_version": "valp-visible-loop-state.v2", "status": "dispatching"}),
                encoding="utf-8",
            )
            expected = ["agents/hermes/self-review.md"]
            (task_dir / expected[0]).parent.mkdir(parents=True, exist_ok=True)
            (task_dir / expected[0]).write_text("done\n", encoding="utf-8")
            legacy = [
                {
                    "ts": "2026-07-14T00:00:00Z",
                    "agent": "hermes",
                    "event": "dispatch_submitted",
                    "exit_code": 0,
                    "dispatch_ref": "agents/hermes/dispatch.md",
                    "expected_refs": expected,
                    "proof": {"submit_proof": {"status": "working"}},
                    "runtime": {"pane_id": "w5:p5", "terminal_id": "term-1"},
                },
                {
                    "ts": "2026-07-14T00:00:01Z",
                    "agent": "hermes",
                    "event": "dispatch_completed",
                    "exit_code": 0,
                    "dispatch_ref": "agents/hermes/dispatch.md",
                    "expected_refs": expected,
                    "runtime": {"pane_id": "w5:p5", "terminal_id": "term-1"},
                },
            ]
            (task_dir / "dispatch-receipts.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in legacy),
                encoding="utf-8",
            )

            self.assertEqual(translate_legacy_herdr_receipts(task_dir, task_id), 2)
            translated = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("schema_version") == "valp-dispatch-receipt.v2"
            ]
            self.assertEqual([record["event"] for record in translated], ["dispatch_submitted", "dispatch_completed"])
            self.assertEqual(translated[0]["work_item_id"], "coordinator:hermes")
            self.assertEqual(translated[0]["dispatch_generation"], 1)
            self.assertEqual(translated[0]["proof"]["pane_id"], "w5:p5")
            self.assertEqual(translated[1]["suspension_epoch"], 1)

    def test_submission_only_receipt_uses_phase_to_restore_expected_refs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_id = "TASK-HERDR-SUBMISSION-ONLY"
            task_dir = Path(tmp) / ".herdr-loop" / "tasks" / task_id
            task_dir.mkdir(parents=True)
            dependencies = build_submission_dependencies(
                task_id,
                {"coordinator": "codex", "implementer": "codex", "reviewer": "codex"},
            )
            (task_dir / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            (task_dir / "state.json").write_text(
                json.dumps({"schema_version": "valp-visible-loop-state.v2", "status": "dispatching"}),
                encoding="utf-8",
            )
            (task_dir / "dispatch-receipts.jsonl").write_text(
                json.dumps(
                    {
                        "ts": "2026-07-14T00:00:00Z",
                        "agent": "codex",
                        "event": "dispatch_submitted",
                        "exit_code": 0,
                        "dispatch_ref": "agents/codex/dispatch.md",
                        "expected_refs": [],
                        "proof": {"submit_proof": {"status": "working"}},
                        "runtime": {"pane_id": "w5:pS"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            translated_count = translate_legacy_herdr_receipts(
                task_dir,
                task_id,
                phase=("codex", "implementer"),
            )

            self.assertEqual(translated_count, 1)
            translated = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ][-1]
            self.assertEqual(translated["work_item_id"], "implementer:codex")
            self.assertEqual(
                translated["expected_refs"],
                ["agents/codex/evidence.md", "evidence/verification.md"],
            )

    def test_dispatch_translates_existing_legacy_receipts_before_dependency_check(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-16T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "hermes": {"active": True, "role": ["coordination"], "strengths": ["state"]},
                "codex": {"active": True, "role": ["implementation"], "strengths": ["edits files"]},
                "claude": {"active": True, "role": ["reviewer"], "strengths": ["read-only review"]},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-LEGACY-DEPENDENCY-TRANSLATION",
                        "Fix a bug, verify it, and review the result.",
                        runtime="queue",
                    )

            expected_by_agent = {
                "hermes": ["agents/hermes/self-review.md"],
                "codex": ["agents/codex/evidence.md", "evidence/verification.md"],
            }
            for refs in expected_by_agent.values():
                for ref in refs:
                    path = task_dir / ref
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("complete\n", encoding="utf-8")
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                for agent, refs in expected_by_agent.items():
                    for event in ("dispatch_submitted", "dispatch_completed"):
                        handle.write(
                            json.dumps(
                                {
                                    "ts": "2026-07-16T00:00:00Z",
                                    "agent": agent,
                                    "event": event,
                                    "exit_code": 0,
                                    "dispatch_ref": f"agents/{agent}/dispatch.md",
                                    "expected_refs": refs,
                                    "proof": {"submit_proof": {"pane_id": f"pane-{agent}"}},
                                    "runtime": {"pane_id": f"pane-{agent}"},
                                }
                            )
                            + "\n"
                        )

            with patch(
                "valp_cli.workflow.collect_runtime_preflight",
                return_value=self.routed_test_preflight(task_dir),
            ):
                commands = dispatch_task(
                    root,
                    "TASK-LEGACY-DEPENDENCY-TRANSLATION",
                    agent="claude",
                    submit=True,
                    runtime="queue",
                )

        self.assertEqual(len(commands), 1)
        self.assertIn("phase=reviewer", commands[0])

    def test_colocated_submission_only_translation_consumes_each_legacy_receipt_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_id = "TASK-HERDR-COLOCATED-TRANSLATION"
            task_dir = Path(tmp) / ".herdr-loop" / "tasks" / task_id
            task_dir.mkdir(parents=True)
            dependencies = build_submission_dependencies(
                task_id,
                {"coordinator": "codex", "implementer": "codex", "reviewer": "codex"},
            )
            (task_dir / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            (task_dir / "state.json").write_text(
                json.dumps({"schema_version": "valp-visible-loop-state.v2", "status": "dispatching"}),
                encoding="utf-8",
            )
            receipt_path = task_dir / "dispatch-receipts.jsonl"
            legacy_receipt = {
                "ts": "2026-07-14T00:00:00Z",
                "agent": "codex",
                "event": "dispatch_submitted",
                "exit_code": 0,
                "dispatch_ref": "agents/codex/dispatch.md",
                "expected_refs": [],
                "proof": {"submit_proof": {"status": "working"}},
                "runtime": {"pane_id": "w5:pS"},
            }
            receipt_path.write_text(json.dumps(legacy_receipt) + "\n", encoding="utf-8")

            self.assertEqual(
                translate_legacy_herdr_receipts(
                    task_dir,
                    task_id,
                    phase=("codex", "coordinator"),
                ),
                1,
            )
            second_legacy_receipt = {
                **legacy_receipt,
                "proof": {"submit_proof": {"status": "working", "attempts": 2}},
            }
            with receipt_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(second_legacy_receipt) + "\n")

            self.assertEqual(
                translate_legacy_herdr_receipts(
                    task_dir,
                    task_id,
                    phase=("codex", "implementer"),
                ),
                1,
            )
            translated = [
                json.loads(line)
                for line in receipt_path.read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("schema_version") == "valp-dispatch-receipt.v2"
            ]
            self.assertEqual(
                [record["work_item_id"] for record in translated],
                ["coordinator:codex", "implementer:codex"],
            )

    def test_task_local_evidence_refs_are_platform_neutral(self) -> None:
        valid_refs = [
            "evidence/verification.md",
            "agents/claude/review.md",
            ".well-known/checkpoint.json",
        ]
        invalid_refs = [
            "/tmp/checkpoint.json",
            "../checkpoint.json",
            "evidence/../checkpoint.json",
            "./checkpoint.json",
            "evidence//checkpoint.json",
            "C:/checkpoint.json",
            "C:\\checkpoint.json",
            "evidence:checkpoint.json",
        ]

        for ref in valid_refs:
            with self.subTest(ref=ref):
                self.assertTrue(workflow_module.safe_task_evidence_ref(ref))
        for ref in invalid_refs:
            with self.subTest(ref=ref):
                self.assertFalse(workflow_module.safe_task_evidence_ref(ref))

    def test_atomic_write_text_preserves_utf8_lf_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            workflow_module.atomic_write_text(path, '{"line": "value"}\n')
            self.assertEqual(path.read_bytes(), b'{"line": "value"}\n')

    def test_run_command_replaces_undecodable_output_bytes(self) -> None:
        result = workflow_module.run_command(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(bytes([0xff]))",
            ]
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["stdout"], "\ufffd")
        self.assertEqual(result["stderr"], "")

    def test_directory_fsync_propagates_io_errors_and_scopes_unsupported_filesystems(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows directory durability is an explicit reference-adapter limitation")
        with patch("valp_cli.workflow.os.open", return_value=42):
            with patch("valp_cli.workflow.os.close"):
                with patch("valp_cli.workflow.os.fsync", side_effect=OSError(errno.EIO, "I/O failure")):
                    with self.assertRaises(OSError) as raised:
                        workflow_module.fsync_directory(Path("/tmp"))
        self.assertEqual(raised.exception.errno, errno.EIO)

        with patch("valp_cli.workflow.os.open", return_value=42):
            with patch("valp_cli.workflow.os.close"):
                with patch(
                    "valp_cli.workflow.os.fsync",
                    side_effect=OSError(errno.EINVAL, "unsupported"),
                ):
                    self.assertFalse(workflow_module.fsync_directory(Path("/tmp")))

    def test_durable_jsonl_append_syncs_parent_only_when_creating_the_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "wait-events.jsonl"
            with patch("valp_cli.workflow.fsync_directory", return_value=True) as sync_directory:
                workflow_module.append_json_line_durable(ledger, {"event_sequence": 1})
                workflow_module.append_json_line_durable(ledger, {"event_sequence": 2})

            sync_directory.assert_called_once_with(ledger.parent)

    def test_file_lock_retry_has_a_bounded_contention_policy(self) -> None:
        attempts = 0

        def eventually_acquired() -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise OSError(errno.EACCES, "lock is held")

        with patch("valp_cli.workflow.time.monotonic", side_effect=[0.0, 0.1, 0.2]):
            with patch("valp_cli.workflow.time.sleep") as sleep:
                workflow_module.retry_file_lock(
                    eventually_acquired,
                    timeout_seconds=1.0,
                    retry_seconds=0.01,
                )
        self.assertEqual(attempts, 3)
        self.assertEqual(sleep.call_count, 2)

        with self.assertRaises(OSError) as raised:
            workflow_module.retry_file_lock(
                lambda: (_ for _ in ()).throw(OSError(errno.EIO, "I/O failure")),
                timeout_seconds=1.0,
                retry_seconds=0,
            )
        self.assertEqual(raised.exception.errno, errno.EIO)

        with patch("valp_cli.workflow.time.monotonic", side_effect=[0.0, 1.0]):
            with self.assertRaises(TimeoutError):
                workflow_module.retry_file_lock(
                    lambda: (_ for _ in ()).throw(OSError(errno.EAGAIN, "lock is held")),
                    timeout_seconds=0.5,
                    retry_seconds=0,
                )

    def write_done_feedback_history(self, root: Path, task_id: str = "OLD-DONE") -> Path:
        directory = root / ".herdr-loop" / "tasks" / task_id
        evidence_ref = "evidence/verification.md"
        (directory / "evidence").mkdir(parents=True)
        (directory / evidence_ref).write_text("verified\n", encoding="utf-8")
        state = {
            "schema_version": "valp-visible-loop-state.v1",
            "task_id": task_id,
            "profile": "software-code",
            "status": "done",
            "selected_agents": ["codex"],
            "gates": {
                "dispatch_receipts": "passed",
                "expected_evidence": "passed",
                "verification": "passed",
                "review": "passed",
                "approval": "not_required",
            },
        }
        feedback = {
            "schema_version": "valp-routing-feedback.v1",
            "task_id": task_id,
            "profile": "software-code",
            "selected_agents": ["codex"],
            "actual_evidence": [evidence_ref],
            "verification_result": "passed",
            "review_result": "passed",
            "result": "done",
            "updated_at": "2026-07-09T00:00:00Z",
        }
        (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (directory / "routing-feedback.json").write_text(json.dumps(feedback), encoding="utf-8")
        history = root / ".herdr-loop" / "routing-feedback.jsonl"
        history.parent.mkdir(parents=True, exist_ok=True)
        history.write_text(json.dumps(feedback) + "\n", encoding="utf-8")
        return directory

    def write_deterministic_wait_fixture(
        self,
        root: Path,
        task_id: str,
        work_items: list[dict[str, object]] | None = None,
        runtime_class: str = "daemon_queue",
    ) -> tuple[Path, list[dict[str, object]]]:
        requested_items = work_items or [
            {
                "agent": "codex",
                "role": "implementer",
            }
        ]
        task_dir = root / ".herdr-loop" / "tasks" / task_id
        task_dir.mkdir(parents=True)
        role_assignments = {
            str(item["role"]): str(item["agent"])
            for item in requested_items
        }
        dependencies = build_submission_dependencies(task_id, role_assignments)
        items = dependencies["work_items"]
        (task_dir / "state.json").write_text(json.dumps({
            "schema_version": "valp-visible-loop-state.v2",
            "task_id": task_id,
            "profile": "agent-runtime",
            "status": "executing",
            "revision": 0,
            "runtime_adapter": {"class": runtime_class},
            "selected_agents": list(dict.fromkeys(str(item["agent"]) for item in items)),
            "role_assignments": role_assignments,
        }), encoding="utf-8")
        (task_dir / "submission-dependencies.json").write_text(
            json.dumps(dependencies),
            encoding="utf-8",
        )
        (task_dir / "wait-policy.json").write_text(json.dumps({
            "schema_version": "valp-wait-policy.v1",
            "task_id": task_id,
            "wait_policy_id": "next-step-results",
            "mode": "dependency_ready",
            "exception_policy": "exception_short_circuit",
            "dependency_ref": "submission-dependencies.json",
            "required_work_items": items,
            "exception_events": [
                "dispatch_blocked",
                "manual_blocked",
                "runtime_failure",
                "cancellation",
                "timeout",
                "user_input",
            ],
        }), encoding="utf-8")
        for item in items:
            for ref in item["expected_refs"]:
                evidence_path = task_dir / str(ref)
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text("verified\n", encoding="utf-8")
        receipts = [
            self.deterministic_receipt(task_id, item, "dispatch_submitted", sequence)
            for sequence, item in enumerate(items, 1)
        ]
        (task_dir / "dispatch-receipts.jsonl").write_text(
            "".join(json.dumps(receipt) + "\n" for receipt in receipts),
            encoding="utf-8",
        )
        return task_dir, items

    def deterministic_receipt(
        self,
        task_id: str,
        item: dict[str, object],
        event: str,
        sequence: int,
        suspension_epoch: int | None = None,
    ) -> dict[str, object]:
        receipt: dict[str, object] = {
            "schema_version": "valp-dispatch-receipt.v2",
            "receipt_id": f"receipt-{sequence}",
            "task_id": task_id,
            "event_sequence": sequence,
            "ts": f"2026-07-13T10:28:{sequence:02d}Z",
            "agent": item["agent"],
            "role": item["role"],
            "work_item_id": item["work_item_id"],
            "dispatch_id": item["dispatch_id"],
            "dispatch_generation": item["dispatch_generation"],
            "event": event,
            "dispatch_ref": f"agents/{item['agent']}/dispatch.md",
            "expected_refs": item["expected_refs"],
        }
        if suspension_epoch is not None:
            receipt["suspension_epoch"] = suspension_epoch
        if event == "dispatch_submitted":
            receipt["proof"] = {
                "runtime": "test queue adapter",
                "submission_id": f"submission-{sequence}",
            }
        return receipt

    def incomplete_recovery_fixture(
        self,
        root: Path,
        task_id: str,
    ) -> tuple[Path, dict[str, object], dict[str, object], dict[str, object]]:
        capabilities: dict[str, object] = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-22T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests", "reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        preflight: dict[str, object] = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {
                "submission_transport": {
                    "status": "pass",
                    "mode": "pane_send_text_enter",
                }
            },
            "agents": {"codex": {"status": "pass", "pane_id": "pane-fresh"}},
        }
        with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
            with patch("valp_cli.workflow.skill_router_command", return_value=None):
                with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                    task_dir = self.publish_routed_task(
                        root,
                        task_id,
                        "Coordinate and review a bounded runtime repair.",
                        profile="generic-analysis",
                        runtime="herdr",
                    )
        dependencies = read_json(task_dir / "submission-dependencies.json")
        coordinator = next(item for item in dependencies["work_items"] if item["role"] == "coordinator")
        return task_dir, capabilities, preflight, coordinator

    def recover_incomplete_cli_args(self, root: Path, task_id: str, role: str = "coordinator") -> list[str]:
        return [
            "dispatch",
            task_id,
            "--workspace",
            str(root),
            "--agent",
            "codex",
            "--role",
            role,
            "--runtime",
            "herdr",
            "--wait-seconds",
            "0",
            "--recover-incomplete",
            "--retry-generation",
            "1",
            "--submit",
        ]

    def test_public_incomplete_recovery_can_fence_done_owned_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-DONE-SESSION"
            task_dir, capabilities, preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["proof"] = {
                "runtime": "HERDR",
                "transport_mode": "agent_prompt",
                "pane_id": "pane-original",
                "submission_id": "original",
            }
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")

            args = self.recover_incomplete_cli_args(root, task_id)
            args.insert(-1, "--reprovision-done-session")
            replacement = self.owned_session_projection(task_id, "pane-fresh")
            replacement["bindings"]["codex"]["generation"] = 2
            invocation_proof = herdr_invocation_proof(pane_id="pane-fresh")
            invocation_proof["session_binding"] = {
                "ref": "agent-sessions.json",
                "generation": 2,
                "identity_token": "sha256:test-owned-session",
                "ownership": replacement["bindings"]["codex"]["ownership"],
            }
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                    with patch(
                        "valp_cli.workflow.ensure_herdr_agent_sessions",
                        return_value=replacement,
                    ) as ensure_sessions:
                        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                            with patch(
                                "valp_cli.workflow.submit_herdr_dispatch",
                                return_value=invocation_proof,
                            ):
                                self.assertEqual(main(args), 0)

            self.assertTrue(ensure_sessions.call_args.kwargs["allow_done_session_reprovision"])
            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
                if '"schema_version"' in line
            ]
            self.assertEqual(
                [receipt["event"] for receipt in receipts],
                ["dispatch_submitted", "dispatch_submitted"],
            )
            self.assertEqual(receipts[-1]["retry_generation"], 1)
            self.assertEqual(receipts[-1]["proof"]["session_binding"]["generation"], 2)

    def write_exception_wake_evidence(
        self,
        task_dir: Path,
        task_id: str,
        suspension: dict[str, object],
        event: str,
        principal_type: str,
        supporting_refs: list[str] | None = None,
    ) -> str:
        ref = f"evidence/wake-requests/{event}.json"
        path = task_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "schema_version": "valp-exception-wake.v1",
                "task_id": task_id,
                "suspension_id": suspension["suspension_id"],
                "suspension_epoch": suspension["suspension_epoch"],
                "event": event,
                "principal": {"type": principal_type, "id": f"test-{principal_type}"},
                "reason": f"test {event}",
                "recorded_at": "2026-07-13T10:29:00Z",
                "supporting_refs": supporting_refs or [],
            }),
            encoding="utf-8",
        )
        return ref

    def test_cli_version_flag(self) -> None:
        output = io.StringIO()
        with self.assertRaises(SystemExit) as raised:
            with contextlib.redirect_stdout(output):
                main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("valp 0.3.0rc1", output.getvalue())

    def test_profile_classification_scores_all_matches(self) -> None:
        self.assertEqual(classify_profile("Fix the HERDR agent connector code"), "agent-runtime")

    def test_high_risk_goal_marks_approval_required_on_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(
                root,
                "TASK-RISK",
                "Deploy the release to production and rotate secrets.",
            )

            state = read_json(task_dir / "state.json")
            self.assertEqual(state["gates"]["approval"], "needs_approval")
            self.assertTrue(state["approval_required"])
            kinds = {item["kind"] for item in state["approval_required"]}
            self.assertIn("deploy", kinds)
            self.assertIn("secrets", kinds)
            task_text = (task_dir / "task.md").read_text(encoding="utf-8")
            self.assertIn("`deploy`", task_text)
            self.assertIn("`secrets`", task_text)

    def test_risk_classifier_uses_word_boundaries(self) -> None:
        kinds = {item["kind"] for item in classify_approval_risks("Deploy release and export private data.")}
        self.assertIn("deploy", kinds)
        self.assertIn("release", kinds)
        self.assertIn("external_private_data", kinds)

        config_kinds = {
            item["kind"]
            for item in classify_approval_risks("Install a plugin and patch a skill for the live agent.")
        }
        self.assertEqual(config_kinds, {"plugin_config", "skill_config"})
        self.assertEqual(classify_approval_risks("Write author notes."), [])
        credential_kinds = {item["kind"] for item in classify_approval_risks("Rotate credentials.")}
        self.assertIn("auth", credential_kinds)

    def test_risk_classifier_requires_approval_for_live_skill_and_plugin_mutations(self) -> None:
        for subject, expected_kind in (
            ("skill", "skill_config"),
            ("plugin", "plugin_config"),
        ):
            for verb in ("update", "edit", "change", "upgrade", "configure", "reconfigure"):
                for article in ("", "a ", "the "):
                    with self.subTest(subject=subject, verb=verb, article=article):
                        kinds = {
                            item["kind"]
                            for item in classify_approval_risks(
                                f"{verb.capitalize()} {article}{subject} for the live agent."
                            )
                        }
                        self.assertIn(expected_kind, kinds)

    def test_risk_classifier_scopes_live_mutation_articles_and_negation_per_clause(self) -> None:
        for subject, expected_kind in (
            ("skill", "skill_config"),
            ("plugin", "plugin_config"),
        ):
            for verb in (
                "install",
                "modify",
                "patch",
                "enable",
                "disable",
                "update",
                "edit",
                "change",
                "upgrade",
                "configure",
                "reconfigure",
            ):
                for article in ("a ", "the "):
                    with self.subTest(subject=subject, verb=verb, article=article):
                        kinds = {
                            item["kind"]
                            for item in classify_approval_risks(
                                f"{verb.capitalize()} {article}{subject} for the live agent."
                            )
                        }
                        self.assertIn(expected_kind, kinds)

        kinds = {
            item["kind"]
            for item in classify_approval_risks(
                "Do not update the skill. Reconfigure the plugin."
            )
        }
        self.assertEqual(kinds, {"plugin_config"})

    def test_risk_classifier_covers_live_context_modifiers_and_effect_synonyms(self) -> None:
        contextual_cases = {
            "Do not disable the skill but enable the plugin.": {"plugin_config"},
            "Run a dry run but disable the plugin in production.": {"plugin_config"},
            "Review the dry-run notes and disable the plugin in production.": {"plugin_config"},
            "It is not true that we should not disable the plugin.": {"plugin_config"},
            "Not only disable the plugin but enable the skill.": {"plugin_config", "skill_config"},
            "Do not hesitate to disable the plugin.": {"plugin_config"},
            "Avoid downtime and disable the plugin.": {"plugin_config"},
        }
        for text, expected in contextual_cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    {item["kind"] for item in classify_approval_risks(text)},
                    expected,
                )

        object_cases = {
            "Disable my plugin.": "plugin_config",
            "Update our skill.": "skill_config",
            "Configure this plugin.": "plugin_config",
            "Upgrade all plugins.": "plugin_config",
            "Patch the production plugin.": "plugin_config",
            "Enable a live skill.": "skill_config",
        }
        for text, expected in object_cases.items():
            with self.subTest(text=text):
                self.assertIn(expected, {item["kind"] for item in classify_approval_risks(text)})

        for verb in ("uninstall", "remove", "turn off", "deactivate", "activate"):
            with self.subTest(verb=verb):
                self.assertIn(
                    "plugin_config",
                    {
                        item["kind"]
                        for item in classify_approval_risks(
                            f"{verb.capitalize()} the production plugin."
                        )
                    },
                )

        for non_actionable in (
            "Do not uninstall the plugin or deactivate the skill.",
            "Documentation only: describe how to turn off the plugin.",
            "Document `remove the plugin` without running it.",
            "Example:\n```sh\nactivate the plugin\n```",
            "Under no circumstances should anyone in the production environment disable the plugin.",
            "For documentation only, explain how to disable the plugin.",
            "Document how to disable the plugin without executing it.",
            'Quote "disable the plugin" in the guide; do not execute it.',
            "Documentation example:\n```sh\nactivate the plugin\ndisable the skill\n```\nDo not execute.",
            "For a dry run, disable the plugin.",
        ):
            with self.subTest(non_actionable=non_actionable):
                self.assertEqual(classify_approval_risks(non_actionable), [])

    def test_risk_classifier_ignores_negated_live_skill_and_plugin_mutations(self) -> None:
        self.assertEqual(
            classify_approval_risks("Do not update the skill or reconfigure a plugin."),
            [],
        )

    def test_risk_classifier_ignores_docs_only_skill_and_plugin_mutations(self) -> None:
        self.assertEqual(
            classify_approval_risks(
                "Documentation only: explain how to configure the plugin and upgrade a skill."
            ),
            [],
        )

    def test_risk_classifier_ignores_quoted_skill_and_plugin_commands(self) -> None:
        self.assertEqual(
            classify_approval_risks(
                "Document `reconfigure the skill` and `update a plugin` commands without executing them."
            ),
            [],
        )

    def test_risk_classifier_detects_explicitly_executed_inline_literal(self) -> None:
        self.assertEqual(
            {item["kind"] for item in classify_approval_risks("Run `deploy production` now.")},
            {"deploy"},
        )

    def test_risk_classifier_detects_explicitly_executed_double_quoted_literal(self) -> None:
        self.assertEqual(
            {item["kind"] for item in classify_approval_risks('Execute "submit the app" now.')},
            {"submit"},
        )

    def test_risk_classifier_distinguishes_executed_single_quoted_literal_from_discussion(self) -> None:
        self.assertEqual(
            (
                {
                    item["kind"]
                    for item in classify_approval_risks("Execute 'rm -rf build/' now.")
                },
                classify_approval_risks(
                    "Quote 'rm -rf build/' in the guide; do not execute it."
                ),
            ),
            ({"delete"}, []),
        )

    def test_risk_classifier_detects_explicitly_executed_fenced_literal(self) -> None:
        self.assertEqual(
            {
                item["kind"]
                for item in classify_approval_risks(
                    "Execute this command:\n```sh\nrm -rf build/\n```"
                )
            },
            {"delete"},
        )

    def test_risk_classifier_ignores_noun_only_skill_and_plugin_wording(self) -> None:
        self.assertEqual(
            classify_approval_risks(
                "Skill updates and plugin configuration guidance are in the handbook."
            ),
            [],
        )

    def test_risk_classifier_distinguishes_model_tokens_from_auth_tokens(self) -> None:
        self.assertEqual(
            classify_approval_risks("Reduce LLM token consumption and prompt token budget."),
            [],
        )
        self.assertEqual(
            classify_approval_risks("Use strict character/token budgets for compact dispatch context."),
            [],
        )
        self.assertEqual(
            classify_approval_risks("Keep zero-token routing and token-efficient dispatches."),
            [],
        )
        auth_kinds = {item["kind"] for item in classify_approval_risks("Rotate the auth token.")}
        self.assertIn("auth", auth_kinds)
        standalone_kinds = {item["kind"] for item in classify_approval_risks("Revoke the access token.")}
        self.assertIn("auth", standalone_kinds)

    def test_risk_classifier_ignores_model_probe_contract_nouns_but_keeps_actions(self) -> None:
        description = (
            "Implement deployment-grade dynamic model identity discovery using "
            "provider-neutral adapter-visible non-sensitive metadata and bind observations "
            "to a non-sensitive session or adapter generation token."
        )
        actionable = "Deploy the service, rotate the session token, and update app metadata."

        self.assertEqual(classify_approval_risks(description), [])
        self.assertEqual(
            {item["kind"] for item in classify_approval_risks(actionable)},
            {"auth", "deploy", "metadata"},
        )

    def test_risk_classifier_ignores_first_install_dry_run_control_words(self) -> None:
        prompt = "Smoke test VALP publish and HERDR dispatch dry run only. Do not submit to agent panes."
        self.assertEqual(classify_approval_risks(prompt), [])
        self.assertEqual(classify_approval_risks("Run a deploy dry run only."), [])
        self.assertEqual(classify_approval_risks("Document `valp publish TASK-001` and `--submit`, but do not execute it."), [])

    def test_risk_classifier_keeps_real_submit_and_release_actions(self) -> None:
        kinds = {item["kind"] for item in classify_approval_risks("Submit the app release and deploy it.")}
        self.assertIn("submit", kinds)
        self.assertIn("release", kinds)
        self.assertIn("deploy", kinds)
        deploy_after_dry_run = {item["kind"] for item in classify_approval_risks("Run a dry run first, then deploy production.")}
        self.assertIn("deploy", deploy_after_dry_run)
        submit_after_smoke_test = {item["kind"] for item in classify_approval_risks("Run smoke test, then submit the app release.")}
        self.assertIn("submit", submit_after_smoke_test)
        self.assertIn("release", submit_after_smoke_test)

    def test_risk_classifier_limits_non_actionable_context_to_its_clause(self) -> None:
        cases = {
            "Documentation only first, then deploy production.": {"deploy"},
            "Print only the plan, but submit the app tomorrow.": {"submit"},
            "Make no GitHub, config, or release changes, then deploy production.": {"deploy"},
            "Make no release changes. Then submit the app.": {"submit"},
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    {item["kind"] for item in classify_approval_risks(text)},
                    expected,
                )

    def test_risk_classifier_detects_action_after_completed_negated_predicate(self) -> None:
        self.assertEqual(
            {
                item["kind"]
                for item in classify_approval_risks(
                    "Make no release changes and deploy production."
                )
            },
            {"deploy"},
        )

    def test_risk_classifier_distinguishes_arbitrary_bare_no_object_from_shared_list(self) -> None:
        self.assertEqual(
            (
                {
                    item["kind"]
                    for item in classify_approval_risks(
                        "Make no credential rotations and upload the package."
                    )
                },
                classify_approval_risks(
                    "Make no credential or upload changes."
                ),
            ),
            ({"upload"}, []),
        )

    def test_risk_classifier_detects_action_after_completed_print_only_predicate(self) -> None:
        self.assertEqual(
            {
                item["kind"]
                for item in classify_approval_risks(
                    "Print only the summary and submit the final report."
                )
            },
            {"submit"},
        )

    def test_risk_classifier_distinguishes_arbitrary_print_only_object_from_shared_list(self) -> None:
        self.assertEqual(
            (
                {
                    item["kind"]
                    for item in classify_approval_risks(
                        "Print only the checksum and release the archive."
                    )
                },
                classify_approval_risks(
                    "Print only release and submit commands."
                ),
            ),
            ({"release"}, []),
        )

    def test_risk_classifier_ignores_printed_high_risk_command_labels(self) -> None:
        self.assertEqual(
            classify_approval_risks("Print only the release and submit commands."),
            [],
        )

    def test_risk_classifier_negates_every_risk_in_comma_coordinate_list(self) -> None:
        for text in (
            "Make no release, deploy, or upload changes.",
            "Do not deploy, submit, or release the app.",
        ):
            with self.subTest(text=text):
                self.assertEqual(classify_approval_risks(text), [])

        self.assertEqual(
            {item["kind"] for item in classify_approval_risks(
                "Make no release changes, then deploy production."
            )},
            {"deploy"},
        )

    def test_risk_classifier_negates_shared_verb_phrase_list(self) -> None:
        self.assertEqual(
            classify_approval_risks(
                "Do not commit, push, update or open a PR, publish a release, "
                "deploy, merge, or delete files."
            ),
            [],
        )

    def test_risk_classifier_negates_risk_noun_inside_prohibited_action(self) -> None:
        self.assertEqual(
            classify_approval_risks(
                "Do not publish a release, deploy, or delete files."
            ),
            [],
        )

    def test_risk_classifier_negates_shared_change_verb_before_auth(self) -> None:
        self.assertEqual(
            classify_approval_risks(
                "Do not change auth, agent configuration, or credentials."
            ),
            [],
        )

    def test_risk_classifier_ignores_metalinguistic_release_verification(self) -> None:
        self.assertEqual(
            classify_approval_risks(
                "Verify that negated release wording does not request approval."
            ),
            [],
        )

    def test_plain_goal_decomposition_keeps_paragraph_together(self) -> None:
        tasks = decompose_execution_tasks("Fix the protocol docs and verify the examples.", "software-code")
        self.assertEqual(tasks[0], "Fix the protocol docs and verify the examples.")

    def test_list_goal_decomposition_uses_explicit_items(self) -> None:
        tasks = decompose_execution_tasks("- Fix SPEC numbering\n- Add minimal example", "generic-analysis")
        self.assertIn("Fix SPEC numbering", tasks)
        self.assertIn("Add minimal example", tasks)

    def test_empty_environment_fallback_is_runtime_neutral(self) -> None:
        with patch("valp_cli.workflow.local_capabilities_path", return_value=Path("/tmp/valp-missing-capabilities.json")):
            capabilities = load_local_capabilities()
        self.assertIn("manual-operator", capabilities["agents"])
        self.assertNotIn("codex", capabilities["agents"])
        operator = capabilities["agents"]["manual-operator"]
        self.assertIn("manual_evidence", operator["role"])
        self.assertIn("must not imply a specific AI agent is installed", operator["must_not_do"])

    def test_capability_lookup_prefers_workspace_valp_paths_over_herdr_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "workspace"
            home = base / "home"
            (root / ".valp" / "agents").mkdir(parents=True)
            (home / ".herdr").mkdir(parents=True)
            (root / ".valp" / "agents" / "capabilities.json").write_text(
                json.dumps({"schema_version": "valp-agent-capabilities.v1", "source": "workspace-valp", "agents": {}}),
                encoding="utf-8",
            )
            (home / ".herdr" / "agent-capabilities.json").write_text(
                json.dumps({"schema_version": "valp-agent-capabilities.v1", "source": "herdr-fallback", "agents": {}}),
                encoding="utf-8",
            )

            with patch.dict(os.environ, {}, clear=True):
                with patch("valp_cli.workflow.Path.home", return_value=home):
                    capabilities = load_local_capabilities(root)

        self.assertEqual(capabilities["source"], "workspace-valp")

    def test_manual_mode_dispatch_prints_manual_instruction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                    with patch("valp_cli.workflow.shutil.which", return_value=None):
                        task_dir = self.publish_routed_task(root, "TASK-MANUAL", "Review the task evidence")
                        commands = dispatch_task(root, "TASK-MANUAL")
                        with self.assertRaises(SystemExit):
                            dispatch_task(root, "TASK-MANUAL", submit=True)

            self.assertEqual(read_json(task_dir / "routing.json")["runtime_adapter"]["class"], "manual")
            self.assertTrue(commands)
            self.assertTrue(commands[0].startswith("Manual Mode:"))
            self.assertNotIn("herdr-loop", commands[0])

    def test_wait_command_timeout_keeps_worker_and_suspension_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-WAIT-TIMEOUT",
                        "Review the task evidence",
                        runtime="manual",
                    )

            agent = read_json(task_dir / "routing.json")["selected_agents"][0]
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": "2026-07-11T00:00:00Z",
                    "agent": agent,
                    "event": "manual_delivery_attested",
                    "dispatch_ref": f"agents/{agent}/dispatch.md",
                }) + "\n")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main([
                    "wait",
                    "TASK-WAIT-TIMEOUT",
                    "--workspace",
                    str(root),
                    "--timeout",
                    "0",
                    "--execution-timeout",
                    "60",
                    "--poll-interval",
                    "0",
                    "--json",
                ])

            result = json.loads(output.getvalue())
            state = read_json(task_dir / "state.json")
            suspension = state["suspension"]
            entered_at = datetime.fromisoformat(suspension["entered_at"].replace("Z", "+00:00"))
            execution_deadline = datetime.fromisoformat(
                suspension["execution_deadline"].replace("Z", "+00:00")
            )
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["status"], "waiting")
            self.assertEqual(result["wait_window"]["status"], "elapsed")
            self.assertFalse(result["wait_window"]["worker_cancelled"])
            self.assertEqual(state["status"], "suspended")
            self.assertEqual(suspension["status"], "waiting")
            self.assertEqual((execution_deadline - entered_at).total_seconds(), 60)
            self.assertNotIn("accepted_wake", suspension)

            second_output = io.StringIO()
            with contextlib.redirect_stdout(second_output):
                second_exit_code = main([
                    "wait",
                    "TASK-WAIT-TIMEOUT",
                    "--workspace",
                    str(root),
                    "--timeout",
                    "0",
                    "--poll-interval",
                    "0",
                    "--json",
                ])

            second_result = json.loads(second_output.getvalue())
            second_state = read_json(task_dir / "state.json")
            self.assertEqual(second_exit_code, 0)
            self.assertEqual(second_result["status"], "waiting")
            self.assertEqual(second_state["suspension"]["suspension_epoch"], suspension["suspension_epoch"])
            self.assertEqual(second_state["suspension"]["execution_deadline"], suspension["execution_deadline"])

    def test_late_reviewer_receipt_wakes_after_wait_window_elapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-SLOW-REVIEWER"
            task_dir, items = self.write_deterministic_wait_fixture(
                root,
                task_id,
                [{"agent": "claude", "role": "reviewer"}],
            )

            waiting = wait_for_task(
                root,
                task_id,
                timeout_seconds=0,
                poll_interval_seconds=0,
                execution_timeout_seconds=60,
            )
            state_while_waiting = read_json(task_dir / "state.json")
            receipts_while_waiting = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(waiting["status"], "waiting")
            self.assertEqual(state_while_waiting["status"], "suspended")
            self.assertEqual(state_while_waiting["suspension"]["pending_work_item_ids"], ["reviewer:claude"])
            self.assertFalse(
                any(receipt.get("event") in {"dispatch_blocked", "cancellation"} for receipt in receipts_while_waiting)
            )

            evidence_ref = str(items[0]["expected_refs"][0])
            evidence_path = task_dir / evidence_ref
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text("Independent review passed after a long-running analysis.\n", encoding="utf-8")
            completion = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                2,
                suspension_epoch=int(waiting["suspension_epoch"]),
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(completion) + "\n")

            resumed = wait_for_task(
                root,
                task_id,
                timeout_seconds=0,
                poll_interval_seconds=0,
            )
            final_state = read_json(task_dir / "state.json")

            self.assertEqual(resumed["status"], "resumed")
            self.assertEqual(resumed["resume_event"], "receipt")
            self.assertEqual(resumed["accepted_wake"]["wake_reason"], "dependency_ready")
            self.assertEqual(resumed["suspension_epoch"], waiting["suspension_epoch"])
            self.assertEqual(resumed["execution_deadline"], waiting["execution_deadline"])
            self.assertEqual(final_state["status"], "executing")

    def test_reached_execution_deadline_blocks_without_cancelling_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-WAIT-EXECUTION-DEADLINE",
                        "Review the task evidence",
                        runtime="manual",
                    )

            agent = read_json(task_dir / "routing.json")["selected_agents"][0]
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": "2026-07-11T00:00:00Z",
                    "agent": agent,
                    "event": "manual_delivery_attested",
                    "dispatch_ref": f"agents/{agent}/dispatch.md",
                }) + "\n")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main([
                    "wait",
                    "TASK-WAIT-EXECUTION-DEADLINE",
                    "--workspace",
                    str(root),
                    "--timeout",
                    "0",
                    "--execution-timeout",
                    "0",
                    "--poll-interval",
                    "0",
                    "--json",
                ])

            result = json.loads(output.getvalue())
            state = read_json(task_dir / "state.json")
            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["accepted_wake"]["wake_reason"], "timeout")
            self.assertEqual(state["status"], "blocked")
            self.assertEqual(state["suspension"]["resume_event"], "timeout")
            self.assertFalse(any(receipt.get("event") == "cancellation" for receipt in receipts))

    def test_wait_does_not_accept_a_non_wake_state_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-WAIT-OVERWRITE",
                        "Review the task evidence",
                        runtime="manual",
                    )

            agent = read_json(task_dir / "routing.json")["selected_agents"][0]
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": "2026-07-13T10:28:15Z",
                    "agent": agent,
                    "event": "manual_delivery_attested",
                    "dispatch_ref": f"agents/{agent}/dispatch.md",
                }) + "\n")

            def overwrite_and_complete(_seconds: float) -> None:
                overwritten = read_json(task_dir / "state.json")
                overwritten["status"] = "planned"
                (task_dir / "state.json").write_text(json.dumps(overwritten), encoding="utf-8")
                with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({
                        "ts": "2026-07-13T10:28:55Z",
                        "agent": agent,
                        "event": "manual_result_attested",
                        "dispatch_ref": f"agents/{agent}/dispatch.md",
                    }) + "\n")

            with patch("valp_cli.workflow.time.sleep", side_effect=overwrite_and_complete):
                result = wait_for_task(
                    root,
                    "TASK-WAIT-OVERWRITE",
                    timeout_seconds=60,
                    poll_interval_seconds=0,
                    execution_timeout_seconds=60,
                )

            state = read_json(task_dir / "state.json")
            self.assertEqual(result["resume_event"], "receipt")
            self.assertEqual(state["status"], "executing")
            self.assertEqual(state["suspension"]["status"], "resumed")

    def test_dependency_ready_waits_for_every_required_work_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-BARRIER"
            task_dir = root / ".herdr-loop" / "tasks" / task_id
            task_dir.mkdir(parents=True)
            role_assignments = {"implementer": "codex", "reviewer": "claude"}
            dependencies = build_submission_dependencies(task_id, role_assignments)
            work_items = dependencies["work_items"]
            (task_dir / "state.json").write_text(json.dumps({
                "schema_version": "valp-visible-loop-state.v2",
                "task_id": task_id,
                "profile": "agent-runtime",
                "status": "executing",
                "revision": 0,
                "selected_agents": ["codex", "claude"],
                "role_assignments": role_assignments,
            }), encoding="utf-8")
            (task_dir / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            (task_dir / "wait-policy.json").write_text(json.dumps({
                "schema_version": "valp-wait-policy.v1",
                "task_id": task_id,
                "wait_policy_id": "next-step-results",
                "mode": "dependency_ready",
                "exception_policy": "exception_short_circuit",
                "dependency_ref": "submission-dependencies.json",
                "required_work_items": work_items,
                "exception_events": [
                    "dispatch_blocked",
                    "runtime_failure",
                    "cancellation",
                    "timeout",
                    "user_input",
                ],
            }), encoding="utf-8")
            for item in work_items:
                for ref in item["expected_refs"]:
                    evidence_path = task_dir / ref
                    evidence_path.parent.mkdir(parents=True, exist_ok=True)
                    evidence_path.write_text("verified\n", encoding="utf-8")

            receipts_path = task_dir / "dispatch-receipts.jsonl"

            def receipt(item: dict[str, object], event: str, sequence: int) -> dict[str, object]:
                record: dict[str, object] = {
                    "schema_version": "valp-dispatch-receipt.v2",
                    "receipt_id": f"receipt-{sequence}",
                    "task_id": task_id,
                    "event_sequence": sequence,
                    "ts": f"2026-07-13T10:28:{sequence:02d}Z",
                    "agent": item["agent"],
                    "role": item["role"],
                    "work_item_id": item["work_item_id"],
                    "dispatch_id": item["dispatch_id"],
                    "dispatch_generation": item["dispatch_generation"],
                    "event": event,
                    "dispatch_ref": f"agents/{item['agent']}/dispatch.md",
                    "expected_refs": item["expected_refs"],
                }
                if event in {"dispatch_completed", "dispatch_blocked"}:
                    record["suspension_epoch"] = read_json(task_dir / "state.json")["suspension"]["suspension_epoch"]
                if event == "dispatch_submitted":
                    record["proof"] = {
                        "runtime": "test queue adapter",
                        "submission_id": f"submission-{sequence}",
                    }
                return record

            receipts_path.write_text(
                "".join(json.dumps(receipt(item, "dispatch_submitted", index)) + "\n" for index, item in enumerate(work_items, 1)),
                encoding="utf-8",
            )
            sleep_count = 0

            def complete_one_work_item(_seconds: float) -> None:
                nonlocal sleep_count
                item = work_items[sleep_count]
                sequence = len(work_items) + sleep_count + 1
                with receipts_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(receipt(item, "dispatch_completed", sequence)) + "\n")
                sleep_count += 1

            with patch("valp_cli.workflow.time.sleep", side_effect=complete_one_work_item):
                result = wait_for_task(
                    root,
                    task_id,
                    timeout_seconds=60,
                    poll_interval_seconds=0,
                    execution_timeout_seconds=60,
                )

            state = read_json(task_dir / "state.json")
            self.assertEqual(sleep_count, 2)
            self.assertEqual(result["accepted_wake"]["wake_reason"], "dependency_ready")
            self.assertEqual(
                state["suspension"]["completed_work_item_ids"],
                ["implementer:codex", "reviewer:claude"],
            )
            self.assertEqual(state["suspension"]["pending_work_item_ids"], [])

    def test_wait_observes_new_expected_evidence_without_model_polling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-ZERO-TOKEN-WAIT"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            for item in items:
                for ref in item["expected_refs"]:
                    (task_dir / str(ref)).unlink()

            sleep_count = 0

            def worker_finishes(_seconds: float) -> None:
                nonlocal sleep_count
                sleep_count += 1
                if sleep_count > 1:
                    raise AssertionError("wait did not observe completed expected evidence")
                for item in items:
                    for ref in item["expected_refs"]:
                        evidence_path = task_dir / str(ref)
                        evidence_path.parent.mkdir(parents=True, exist_ok=True)
                        evidence_path.write_text("verified after suspension\n", encoding="utf-8")

            with patch("valp_cli.workflow.time.sleep", side_effect=worker_finishes):
                result = wait_for_task(
                    root,
                    task_id,
                    timeout_seconds=60,
                    poll_interval_seconds=0,
                    execution_timeout_seconds=60,
                )

            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            observed = [
                receipt
                for receipt in receipts
                if receipt.get("event") == "dispatch_completed"
                and (receipt.get("proof") or {}).get("observer") == "valp.wait.expected-evidence"
            ]
            self.assertEqual(sleep_count, 1)
            self.assertEqual(result["accepted_wake"]["wake_reason"], "dependency_ready")
            self.assertEqual(len(observed), 1)
            self.assertEqual(observed[0]["work_item_id"], "implementer:codex")

    def test_wait_does_not_convert_preexisting_evidence_into_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-STALE-EVIDENCE-WAIT"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)

            result = wait_for_task(
                root,
                task_id,
                timeout_seconds=0,
                poll_interval_seconds=0,
                execution_timeout_seconds=60,
            )

            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            state = read_json(task_dir / "state.json")
            self.assertEqual(result["status"], "waiting")
            self.assertNotIn("accepted_wake", result)
            self.assertEqual(state["status"], "suspended")
            self.assertEqual(state["suspension"]["status"], "waiting")
            self.assertFalse(
                any(
                    receipt.get("event") == "dispatch_completed"
                    and (receipt.get("proof") or {}).get("observer") == "valp.wait.expected-evidence"
                    for receipt in receipts
                )
            )

    def test_wait_ignores_unrelated_terminal_receipt_from_a_required_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-UNRELATED-WORK"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            receipts_path = task_dir / "dispatch-receipts.jsonl"
            sleep_count = 0
            direct_rejection = ""

            def append_receipt(_seconds: float) -> None:
                nonlocal direct_rejection, sleep_count
                suspension = read_json(task_dir / "state.json")["suspension"]
                if sleep_count == 0:
                    unrelated = {
                        **items[0],
                        "role": "reviewer",
                        "work_item_id": "reviewer:codex",
                        "dispatch_id": f"{task_id}:reviewer:1",
                        "expected_refs": ["agents/codex/review.md"],
                    }
                    receipt = self.deterministic_receipt(
                        task_id,
                        unrelated,
                        "dispatch_completed",
                        2,
                        suspension_epoch=int(suspension["suspension_epoch"]),
                    )
                else:
                    receipt = self.deterministic_receipt(
                        task_id,
                        items[0],
                        "dispatch_completed",
                        3,
                        suspension_epoch=int(suspension["suspension_epoch"]),
                    )
                with receipts_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(receipt) + "\n")
                if sleep_count == 0:
                    try:
                        resume_suspended_task(
                            root,
                            task_id,
                            "receipt",
                            resume_ref="dispatch-receipts.jsonl#2",
                        )
                    except SystemExit as exc:
                        direct_rejection = str(exc)
                sleep_count += 1

            with patch("valp_cli.workflow.time.sleep", side_effect=append_receipt):
                result = wait_for_task(
                    root,
                    task_id,
                    timeout_seconds=60,
                    poll_interval_seconds=0,
                    execution_timeout_seconds=60,
                )

            self.assertEqual(sleep_count, 2)
            self.assertIn("required work item identity", direct_rejection)
            self.assertEqual(result["accepted_wake"]["wake_reason"], "dependency_ready")
            self.assertEqual(result["completed_work_item_ids"], ["implementer:codex"])

    def test_deterministic_wake_rejects_cross_identity_and_stale_receipts(self) -> None:
        mutations = {
            "task_id": "OTHER-TASK",
            "role": "reviewer",
            "work_item_id": "other:codex",
            "dispatch_id": "stale-dispatch",
            "dispatch_generation": 2,
            "suspension_epoch": 0,
            "event_sequence": 1,
        }
        for field, invalid_value in mutations.items():
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    task_id = f"TASK-WAIT-IDENTITY-{field.upper()}"
                    task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
                    suspension = suspend_task(root, task_id, timeout_seconds=60)
                    receipt = self.deterministic_receipt(
                        task_id,
                        items[0],
                        "dispatch_completed",
                        2,
                        suspension_epoch=int(suspension["suspension_epoch"]),
                    )
                    receipt[field] = invalid_value
                    with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(receipt) + "\n")

                    with self.assertRaisesRegex(SystemExit, "work item identity|receipt ledger"):
                        resume_suspended_task(
                            root,
                            task_id,
                            "receipt",
                            resume_ref="dispatch-receipts.jsonl#2",
                        )

                    state = read_json(task_dir / "state.json")
                    self.assertEqual(state["status"], "suspended")
                    self.assertEqual(state["suspension"]["status"], "waiting")

    def test_wait_policy_cannot_invent_a_work_item_outside_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-INVENTED-ITEM"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            dependencies = read_json(task_dir / "submission-dependencies.json")
            dependencies["schema_version"] = "valp-submission-dependencies.v2"
            dependencies["work_items"] = []
            (task_dir / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "dependency work item"):
                suspend_task(root, task_id, timeout_seconds=60)

    def test_deterministic_suspend_rejects_coordinated_routing_identity_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-ROUTING-TAMPER"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            forged_item = {
                "work_item_id": "reviewer:mallory",
                "agent": "mallory",
                "role": "reviewer",
                "dispatch_id": "forged-review-dispatch",
                "dispatch_generation": 1,
                "expected_refs": ["agents/mallory/review.md"],
            }
            dependencies = read_json(task_dir / "submission-dependencies.json")
            dependencies["work_items"] = [forged_item]
            (task_dir / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            policy = read_json(task_dir / "wait-policy.json")
            policy["required_work_items"] = [forged_item]
            (task_dir / "wait-policy.json").write_text(json.dumps(policy), encoding="utf-8")
            forged_receipt = self.deterministic_receipt(
                task_id,
                forged_item,
                "dispatch_submitted",
                1,
            )
            (task_dir / "dispatch-receipts.jsonl").write_text(
                json.dumps(forged_receipt) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "routed role assignments"):
                suspend_task(root, task_id, timeout_seconds=60)

            self.assertEqual(read_json(task_dir / "state.json")["revision"], 0)
            self.assertFalse((task_dir / "wait-events.jsonl").exists())

    def test_deterministic_suspend_rejects_coordinated_dispatch_identity_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-DISPATCH-TAMPER"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            forged_item = {
                **items[0],
                "work_item_id": "rogue",
                "dispatch_id": "rogue",
                "dispatch_generation": 99,
            }
            dependencies = read_json(task_dir / "submission-dependencies.json")
            dependencies["work_items"] = [forged_item]
            (task_dir / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            policy = read_json(task_dir / "wait-policy.json")
            policy["required_work_items"] = [forged_item]
            (task_dir / "wait-policy.json").write_text(json.dumps(policy), encoding="utf-8")
            forged_receipt = self.deterministic_receipt(
                task_id,
                forged_item,
                "dispatch_submitted",
                1,
            )
            (task_dir / "dispatch-receipts.jsonl").write_text(
                json.dumps(forged_receipt) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "routed role assignments"):
                suspend_task(root, task_id, timeout_seconds=60)

            self.assertEqual(read_json(task_dir / "state.json")["revision"], 0)

    def test_full_mode_wait_requires_an_explicit_wait_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-MISSING-POLICY"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            (task_dir / "wait-policy.json").unlink()

            with self.assertRaisesRegex(SystemExit, "wait-policy.json"):
                suspend_task(root, task_id, timeout_seconds=60)

    def test_deterministic_suspend_requires_concrete_adapter_delivery_proof(self) -> None:
        cases = {
            "missing": {"event": "dispatch_submitted"},
            "empty": {"event": "dispatch_submitted", "proof": {}},
            "note_only": {"event": "dispatch_submitted", "proof": {"note": "accepted"}},
            "simulated": {"event": "dispatch_submitted", "proof": {"mode": "simulated"}},
            "boolean_id": {"event": "dispatch_submitted", "proof": {"id": True}},
            "boolean_record": {"event": "dispatch_submitted", "proof": {"record": True}},
            "generic_proof": {"event": "dispatch_submitted", "proof": {"proof": "accepted"}},
            "string_attempts": {"event": "dispatch_submitted", "proof": {"attempts": "42"}},
            "recorded_status": {
                "event": "dispatch_submitted",
                "proof": {"recorded_status": "accepted"},
            },
            "manual": {"event": "manual_delivery_attested", "proof": {"runtime": "manual"}},
        }
        for case, mutation in cases.items():
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    task_id = f"TASK-WAIT-PROOF-{case.upper()}"
                    task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
                    receipt = json.loads(
                        (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").strip()
                    )
                    receipt.pop("proof", None)
                    receipt.update(mutation)
                    (task_dir / "dispatch-receipts.jsonl").write_text(
                        json.dumps(receipt) + "\n",
                        encoding="utf-8",
                    )

                    with self.assertRaisesRegex(SystemExit, "delivery proof"):
                        suspend_task(root, task_id, timeout_seconds=60)

                    state = read_json(task_dir / "state.json")
                    self.assertEqual(state["status"], "executing")
                    self.assertEqual(state["revision"], 0)
                    self.assertFalse((task_dir / "wait-events.jsonl").exists())

    def test_deterministic_suspend_rejects_invalid_exception_event_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-INVALID-EXCEPTIONS"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            policy = read_json(task_dir / "wait-policy.json")
            policy["exception_events"] = "runtime_failure,cancellation,timeout,user_input"
            (task_dir / "wait-policy.json").write_text(json.dumps(policy), encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "exception_events"):
                suspend_task(root, task_id, timeout_seconds=60)

            self.assertEqual(read_json(task_dir / "state.json")["revision"], 0)

    def test_deterministic_receipt_ledger_rejects_boolean_identity_numbers(self) -> None:
        for field in ["event_sequence", "dispatch_generation"]:
            with self.subTest(phase="delivery", field=field):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    task_id = f"TASK-WAIT-BOOLEAN-DELIVERY-{field.upper()}"
                    task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
                    receipt = json.loads(
                        (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").strip()
                    )
                    receipt[field] = True
                    (task_dir / "dispatch-receipts.jsonl").write_text(
                        json.dumps(receipt) + "\n",
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(SystemExit, "receipt ledger"):
                        suspend_task(root, task_id, timeout_seconds=60)

        for field in ["event_sequence", "suspension_epoch"]:
            with self.subTest(phase="completion", field=field):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    task_id = f"TASK-WAIT-BOOLEAN-COMPLETION-{field.upper()}"
                    task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
                    suspension = suspend_task(root, task_id, timeout_seconds=60)
                    completion = self.deterministic_receipt(
                        task_id,
                        items[0],
                        "dispatch_completed",
                        2,
                        suspension_epoch=int(suspension["suspension_epoch"]),
                    )
                    completion[field] = True
                    with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(completion) + "\n")
                    with self.assertRaisesRegex(SystemExit, "receipt ledger"):
                        resume_suspended_task(
                            root,
                            task_id,
                            "receipt",
                            resume_ref="dispatch-receipts.jsonl#2",
                        )
                    self.assertEqual(read_json(task_dir / "state.json")["status"], "suspended")

    def test_deterministic_receipt_ledger_rejects_conflicting_duplicate_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-DUPLICATE-RECEIPT-ID"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            first = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                2,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            second = {
                **first,
                "event_sequence": 3,
                "event": "dispatch_blocked",
            }
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(first) + "\n")
                handle.write(json.dumps(second) + "\n")

            with self.assertRaisesRegex(SystemExit, "duplicate receipt_id"):
                resume_suspended_task(
                    root,
                    task_id,
                    "receipt",
                    resume_ref="dispatch-receipts.jsonl#2",
                )
            self.assertEqual(read_json(task_dir / "state.json")["status"], "suspended")
            audit_item = TaskAudit(task_dir).check_deterministic_wake()
            self.assertEqual(audit_item.status, "fail")
            self.assertIn("receipt ledger", audit_item.message)

    def test_identical_duplicate_completion_receipt_is_an_idempotent_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-IDENTICAL-RECEIPT"
            work_items = [
                {
                    "work_item_id": "implementation:codex",
                    "agent": "codex",
                    "role": "implementer",
                    "dispatch_id": "dispatch-implementation-1",
                    "dispatch_generation": 1,
                    "expected_refs": ["agents/codex/evidence.md"],
                },
                {
                    "work_item_id": "review:claude",
                    "agent": "claude",
                    "role": "reviewer",
                    "dispatch_id": "dispatch-review-1",
                    "dispatch_generation": 1,
                    "expected_refs": ["agents/claude/review.md"],
                },
            ]
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id, work_items)
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            completion = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                3,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(completion) + "\n")
                handle.write(json.dumps(completion) + "\n")

            first = resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#3",
            )
            state_after_first = (task_dir / "state.json").read_bytes()
            events_after_first = (task_dir / "wait-events.jsonl").read_bytes()
            duplicate = resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#4",
            )

            self.assertEqual(duplicate, first)
            self.assertEqual((task_dir / "state.json").read_bytes(), state_after_first)
            self.assertEqual((task_dir / "wait-events.jsonl").read_bytes(), events_after_first)

    def test_dispatch_blocked_short_circuits_without_satisfying_the_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-BLOCKED"
            work_items = [
                {
                    "work_item_id": "implementation:codex",
                    "agent": "codex",
                    "role": "implementer",
                    "dispatch_id": "dispatch-implementation-1",
                    "dispatch_generation": 1,
                    "expected_refs": ["agents/codex/evidence.md"],
                },
                {
                    "work_item_id": "review:claude",
                    "agent": "claude",
                    "role": "reviewer",
                    "dispatch_id": "dispatch-review-1",
                    "dispatch_generation": 1,
                    "expected_refs": ["agents/claude/review.md"],
                },
            ]
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id, work_items)
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            blocked = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_blocked",
                3,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(blocked) + "\n")

            result = resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#3",
            )

            state = read_json(task_dir / "state.json")
            self.assertEqual(state["status"], "blocked")
            self.assertEqual(result["accepted_wake"]["wake_reason"], "dispatch_blocked")
            self.assertEqual(result["completed_work_item_ids"], [])
            self.assertEqual(result["pending_work_item_ids"], ["implementer:codex", "reviewer:claude"])
            self.assertEqual(result["failed_work_item_ids"], ["implementer:codex"])

            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            receipts[-1]["event"] = "dispatch_completed"
            (task_dir / "dispatch-receipts.jsonl").write_text(
                "".join(json.dumps(receipt) + "\n" for receipt in receipts),
                encoding="utf-8",
            )
            runtime_rejected = False
            try:
                resume_suspended_task(
                    root,
                    task_id,
                    "receipt",
                    resume_ref="dispatch-receipts.jsonl#3",
                )
            except SystemExit:
                runtime_rejected = True
            audit_item = TaskAudit(task_dir).check_deterministic_wake()

            self.assertEqual((runtime_rejected, audit_item.status), (True, "fail"))
            self.assertIn("receipt", audit_item.message.lower())

    def test_timeout_cannot_resume_before_the_recorded_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-EARLY-TIMEOUT"
            _task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            suspend_task(root, task_id, timeout_seconds=60)

            with self.assertRaisesRegex(SystemExit, "deadline"):
                resume_suspended_task(root, task_id, "timeout")

    def test_full_mode_rejects_manual_completion_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-FULL-MANUAL"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            receipt = self.deterministic_receipt(
                task_id,
                items[0],
                "manual_result_attested",
                2,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt) + "\n")

            with self.assertRaisesRegex(SystemExit, "Manual receipt"):
                resume_suspended_task(
                    root,
                    task_id,
                    "receipt",
                    resume_ref="dispatch-receipts.jsonl#2",
                )

    def test_invalid_evidence_cannot_satisfy_dependency_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-INVALID-EVIDENCE"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            evidence_ref = str(items[0]["expected_refs"][0])
            (task_dir / "evidence-status.json").write_text(json.dumps({
                "evidence": {
                    evidence_ref: {"status": "invalid"},
                }
            }), encoding="utf-8")
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            receipt = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                2,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt) + "\n")

            with self.assertRaisesRegex(SystemExit, "missing or invalid"):
                resume_suspended_task(
                    root,
                    task_id,
                    "receipt",
                    resume_ref="dispatch-receipts.jsonl#2",
                )

    def test_dependency_ready_revalidates_evidence_for_every_completed_work_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-REVALIDATE-EVIDENCE"
            task_dir, items = self.write_deterministic_wait_fixture(
                root,
                task_id,
                [
                    {"agent": "codex", "role": "implementer"},
                    {"agent": "claude", "role": "reviewer"},
                ],
            )
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            receipts_path = task_dir / "dispatch-receipts.jsonl"
            first_completion = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                3,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            with receipts_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(first_completion) + "\n")
            resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#3",
            )

            invalid_ref = str(items[0]["expected_refs"][0])
            (task_dir / "evidence-status.json").write_text(
                json.dumps({"evidence": {invalid_ref: {"status": "invalid"}}}),
                encoding="utf-8",
            )
            second_completion = self.deterministic_receipt(
                task_id,
                items[1],
                "dispatch_completed",
                4,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            with receipts_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(second_completion) + "\n")

            with self.assertRaisesRegex(SystemExit, "required work item evidence"):
                resume_suspended_task(
                    root,
                    task_id,
                    "receipt",
                    resume_ref="dispatch-receipts.jsonl#4",
                )

            unchanged = read_json(task_dir / "state.json")["suspension"]
            self.assertEqual(unchanged["completed_work_item_ids"], ["implementer:codex"])
            self.assertEqual(unchanged["pending_work_item_ids"], ["reviewer:claude"])
            self.assertEqual(unchanged["status"], "waiting")

    def test_each_suspension_epoch_replays_its_immutable_wait_policy_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-POLICY-EPOCHS"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            first = suspend_task(root, task_id, timeout_seconds=60)
            completion = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                2,
                suspension_epoch=int(first["suspension_epoch"]),
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(completion) + "\n")
            resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#2",
            )

            policy = read_json(task_dir / "wait-policy.json")
            policy["wait_policy_id"] = "following-step-results"
            (task_dir / "wait-policy.json").write_text(json.dumps(policy), encoding="utf-8")

            second = suspend_task(root, task_id, timeout_seconds=60)

            self.assertEqual((first["suspension_epoch"], second["suspension_epoch"]), (1, 2))
            self.assertNotEqual(first["wait_policy_ref"], second["wait_policy_ref"])
            self.assertEqual(
                read_json(task_dir / str(first["wait_policy_ref"]))["wait_policy_id"],
                "next-step-results",
            )
            self.assertEqual(
                read_json(task_dir / str(second["wait_policy_ref"]))["wait_policy_id"],
                "following-step-results",
            )
            self.assertEqual(TaskAudit(task_dir).check_deterministic_wake().status, "pass")

    def test_historical_policy_snapshot_rejects_work_items_outside_canonical_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-INTRUDER-SNAPSHOT"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            suspend_task(root, task_id, timeout_seconds=60)
            intruder = {
                **items[0],
                "work_item_id": "reviewer:intruder",
                "agent": "intruder",
                "role": "reviewer",
                "dispatch_id": f"{task_id}:reviewer:1",
                "expected_refs": ["agents/intruder/review.md"],
            }
            policy = read_json(task_dir / "wait-policy.json")
            policy["required_work_items"] = [intruder]
            serialized = json.dumps(policy, indent=2, ensure_ascii=False) + "\n"
            digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            policy_ref = f"wait-policies/{digest}.json"
            snapshot_path = task_dir / policy_ref
            snapshot_path.parent.mkdir(exist_ok=True)
            workflow_module.atomic_write_text(snapshot_path, serialized)

            state = read_json(task_dir / "state.json")
            state["suspension"].update({
                "wait_policy_ref": policy_ref,
                "required_work_items": [intruder],
                "required_work_item_ids": [intruder["work_item_id"]],
                "pending_work_item_ids": [intruder["work_item_id"]],
                "waiting_for_agents": [intruder["agent"]],
            })
            events = [
                json.loads(line)
                for line in (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            events[-1]["projection"]["suspension"] = state["suspension"]
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task_dir / "wait-events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )

            runtime_rejected = False
            try:
                suspend_task(root, task_id, timeout_seconds=60)
            except SystemExit:
                runtime_rejected = True
            audit_item = TaskAudit(task_dir).check_deterministic_wake()

            self.assertEqual((runtime_rejected, audit_item.status), (True, "fail"))
            self.assertIn("unknown dependency work item", audit_item.message)

    def test_suspension_epoch_comes_from_history_and_wake_results_are_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-HISTORY-EPOCH"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            first = suspend_task(root, task_id, timeout_seconds=0)
            first_result = resume_suspended_task(root, task_id, "timeout")
            first_ref = str(first_result["accepted_wake"]["result_ref"])
            first_bytes = (task_dir / first_ref).read_bytes()

            state = read_json(task_dir / "state.json")
            state.pop("suspension")
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

            second = suspend_task(root, task_id, timeout_seconds=0)
            second_result = resume_suspended_task(root, task_id, "timeout")
            second_ref = str(second_result["accepted_wake"]["result_ref"])

            self.assertEqual((first["suspension_epoch"], second["suspension_epoch"]), (1, 2))
            self.assertNotEqual(first_ref, second_ref)
            self.assertEqual((task_dir / first_ref).read_bytes(), first_bytes)

    def test_resume_recovers_byte_identical_orphan_result_and_rejects_conflicting_bytes(self) -> None:
        for conflicting in (False, True):
            with self.subTest(conflicting=conflicting):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    task_id = f"TASK-WAIT-ORPHAN-RESULT-{conflicting}"
                    task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
                    suspend_task(root, task_id, timeout_seconds=0)

                    with patch("valp_cli.workflow.now_iso", return_value="2026-07-13T10:30:00Z"):
                        with patch(
                            "valp_cli.workflow.commit_wait_state",
                            side_effect=OSError("simulated crash before event commit"),
                        ):
                            with self.assertRaisesRegex(OSError, "simulated crash"):
                                resume_suspended_task(root, task_id, "timeout")

                    result_paths = list((task_dir / "wake-results").glob("*.json"))
                    self.assertEqual(len(result_paths), 1)
                    orphan_path = result_paths[0]
                    orphan_bytes = orphan_path.read_bytes()
                    if conflicting:
                        changed = read_json(orphan_path)
                        changed["resulting_task_status"] = "executing"
                        orphan_path.write_text(json.dumps(changed, indent=2) + "\n", encoding="utf-8")

                    with patch("valp_cli.workflow.now_iso", return_value="2026-07-13T10:31:00Z"):
                        if conflicting:
                            with self.assertRaisesRegex(SystemExit, "conflicts"):
                                resume_suspended_task(root, task_id, "timeout")
                        else:
                            resumed = resume_suspended_task(root, task_id, "timeout")
                            self.assertEqual(resumed["status"], "resumed")
                            self.assertEqual(orphan_path.read_bytes(), orphan_bytes)
                            self.assertEqual(
                                len(
                                    (task_dir / "wait-events.jsonl")
                                    .read_text(encoding="utf-8")
                                    .splitlines()
                                ),
                                2,
                            )

    def test_resume_event_and_wake_reason_matrix_is_closed_in_runtime_and_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-REASON-MATRIX"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            suspend_task(root, task_id, timeout_seconds=0)
            result = resume_suspended_task(root, task_id, "timeout")
            result_ref = str(result["accepted_wake"]["result_ref"])

            state = read_json(task_dir / "state.json")
            state["suspension"]["accepted_wake"]["wake_reason"] = "dispatch_blocked"
            state["suspension"]["failed_work_item_ids"] = [items[0]["work_item_id"]]
            events = [
                json.loads(line)
                for line in (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            events[-1]["wake_reason"] = "dispatch_blocked"
            events[-1]["projection"]["suspension"] = state["suspension"]
            wake_result = read_json(task_dir / result_ref)
            wake_result["wake_reason"] = "dispatch_blocked"
            wake_result["failed_work_item_ids"] = [items[0]["work_item_id"]]
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task_dir / "wait-events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            (task_dir / result_ref).write_text(json.dumps(wake_result), encoding="utf-8")

            repository = Path(__file__).resolve().parents[1]
            schema_checks = []
            for schema_name, document in (
                ("state.schema.json", state),
                ("wait-event.schema.json", events[-1]),
                ("wake-result.schema.json", wake_result),
            ):
                validator = schema_validator(repository / "schemas" / schema_name)
                schema_checks.append(bool(list(validator.iter_errors(document))))

            runtime_rejected = False
            try:
                suspend_task(root, task_id, timeout_seconds=0)
            except SystemExit:
                runtime_rejected = True

            self.assertEqual(
                (runtime_rejected, *schema_checks),
                (True, True, True, True),
            )

    def test_wake_reason_and_resulting_task_status_matrix_is_closed_everywhere(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-STATUS-MATRIX"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            suspend_task(root, task_id, timeout_seconds=0)
            result = resume_suspended_task(root, task_id, "timeout")
            result_ref = str(result["accepted_wake"]["result_ref"])

            state = read_json(task_dir / "state.json")
            state["status"] = "executing"
            events = [
                json.loads(line)
                for line in (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            events[-1]["projection"]["status"] = "executing"
            wake_result = read_json(task_dir / result_ref)
            wake_result["resulting_task_status"] = "executing"
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task_dir / "wait-events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            (task_dir / result_ref).write_text(json.dumps(wake_result), encoding="utf-8")

            repository = Path(__file__).resolve().parents[1]
            schema_checks = [
                bool(list(schema_validator(repository / "schemas" / schema_name).iter_errors(document)))
                for schema_name, document in (
                    ("state.schema.json", state),
                    ("wait-event.schema.json", events[-1]),
                    ("wake-result.schema.json", wake_result),
                )
            ]
            audit_item = TaskAudit(task_dir).check_deterministic_wake()
            runtime_rejected = False
            try:
                resume_suspended_task(root, task_id, "timeout")
            except SystemExit:
                runtime_rejected = True

            self.assertEqual(
                (runtime_rejected, audit_item.status, *schema_checks),
                (True, "fail", False, True, True),
            )

    def test_late_completion_recovery_progresses_without_rewriting_timeout_wake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-POST-TIMEOUT-PROGRESS"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            suspend_task(root, task_id, timeout_seconds=0)
            result = resume_suspended_task(root, task_id, "timeout")
            result_ref = str(result["accepted_wake"]["result_ref"])

            state = read_json(task_dir / "state.json")
            events = [
                json.loads(line)
                for line in (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            wake_result = read_json(task_dir / result_ref)
            self.assertEqual(
                (state["status"], events[-1]["projection"]["status"], wake_result["resulting_task_status"]),
                ("blocked", "blocked", "blocked"),
            )

            wait_event_bytes = (task_dir / "wait-events.jsonl").read_bytes()
            wake_result_bytes = (task_dir / result_ref).read_bytes()
            (task_dir / "routing.json").write_text(
                json.dumps({"dispatch_payload_budgets": {}}),
                encoding="utf-8",
            )
            (task_dir / "iteration-budget.json").write_text(
                json.dumps({
                    "task_id": task_id,
                    "status": "blocked",
                    "stop_reason": "task status is blocked",
                    "usage": {
                        "dispatch_reference_tokens": 0,
                        "dispatches": 1,
                        "reroutes": 0,
                        "fix_review_rounds": 0,
                    },
                    "max_dispatch_reference_tokens": 100,
                    "max_dispatches": 5,
                    "max_reroutes": 1,
                    "max_fix_review_rounds": 3,
                }),
                encoding="utf-8",
            )
            completion = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                2,
                suspension_epoch=int(result["suspension_epoch"]),
            )
            completion["proof"] = {"submission_receipt_id": "receipt-1"}
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(completion) + "\n")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main([
                    "resume",
                    task_id,
                    "--workspace",
                    str(root),
                    "--event",
                    "receipt",
                    "--ref",
                    "dispatch-receipts.jsonl#2",
                    "--json",
                ])
            recovery = json.loads(output.getvalue())
            state = read_json(task_dir / "state.json")

            repository = Path(__file__).resolve().parents[1]
            self.assertEqual(exit_code, 0)
            self.assertEqual(recovery["recovery_event"], "late_completion")
            self.assertEqual(state["status"], "dispatching")
            self.assertEqual(read_json(task_dir / "iteration-budget.json")["status"], "active")
            self.assertEqual(
                list(schema_validator(repository / "schemas/state.schema.json").iter_errors(state)),
                [],
            )
            self.assertEqual(TaskAudit(task_dir).check_deterministic_wake().status, "pass")
            self.assertEqual((task_dir / "wait-events.jsonl").read_bytes(), wait_event_bytes)
            self.assertEqual((task_dir / result_ref).read_bytes(), wake_result_bytes)
            self.assertEqual(events[-1]["projection"]["status"], "blocked")
            self.assertEqual(wake_result["resulting_task_status"], "blocked")

    def test_late_completion_recovery_rejects_invalid_proof_boundaries(self) -> None:
        for case in ("wrong_epoch", "missing_submission_binding", "invalid_evidence"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                task_id = f"TASK-WAIT-LATE-RECOVERY-{case}"
                task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
                suspend_task(root, task_id, timeout_seconds=0)
                timeout_result = resume_suspended_task(root, task_id, "timeout")
                result_ref = str(timeout_result["accepted_wake"]["result_ref"])
                wait_event_bytes = (task_dir / "wait-events.jsonl").read_bytes()
                wake_result_bytes = (task_dir / result_ref).read_bytes()

                completion = self.deterministic_receipt(
                    task_id,
                    items[0],
                    "dispatch_completed",
                    2,
                    suspension_epoch=int(timeout_result["suspension_epoch"]),
                )
                completion["proof"] = {"submission_receipt_id": "receipt-1"}
                if case == "wrong_epoch":
                    completion["suspension_epoch"] = int(timeout_result["suspension_epoch"]) + 1
                elif case == "missing_submission_binding":
                    completion["proof"] = {}
                else:
                    evidence_ref = str(items[0]["expected_refs"][0])
                    (task_dir / "evidence-status.json").write_text(
                        json.dumps({"evidence": {evidence_ref: {"status": "invalid"}}}),
                        encoding="utf-8",
                    )
                with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(completion) + "\n")

                with self.assertRaises(SystemExit):
                    resume_suspended_task(
                        root,
                        task_id,
                        "receipt",
                        resume_ref="dispatch-receipts.jsonl#2",
                    )

                self.assertEqual(read_json(task_dir / "state.json")["status"], "blocked")
                self.assertEqual((task_dir / "wait-events.jsonl").read_bytes(), wait_event_bytes)
                self.assertEqual((task_dir / result_ref).read_bytes(), wake_result_bytes)
                timeline = [
                    json.loads(line)
                    for line in (task_dir / "timeline.jsonl").read_text(encoding="utf-8").splitlines()
                ]
                self.assertFalse(
                    any(
                        event.get("event") == "late_completion_recovered"
                        for event in timeline
                    )
                )

    def test_wait_event_projection_uses_the_closed_state_suspension_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-CLOSED-PROJECTION"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            suspend_task(root, task_id, timeout_seconds=60)
            state = read_json(task_dir / "state.json")
            events = [
                json.loads(line)
                for line in (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            state["suspension"]["force_resume"] = True
            events[-1]["projection"]["suspension"]["force_resume"] = True
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task_dir / "wait-events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )

            validator = schema_validator(
                Path(__file__).resolve().parents[1] / "schemas" / "wait-event.schema.json"
            )
            schema_rejected = bool(list(validator.iter_errors(events[-1])))
            audit_item = TaskAudit(task_dir).check_deterministic_wake()
            runtime_rejected = False
            try:
                suspend_task(root, task_id, timeout_seconds=60)
            except SystemExit:
                runtime_rejected = True

            self.assertEqual(
                (runtime_rejected, audit_item.status, schema_rejected),
                (True, "fail", True),
            )

    def test_optional_checkpoint_ref_requires_a_real_safe_task_local_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-OPTIONAL-CHECKPOINT"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            suspend_task(root, task_id, timeout_seconds=60)
            checkpoint_ref = "evidence/checkpoints/epoch-1.json"
            checkpoint_path = task_dir / checkpoint_ref
            checkpoint_path.parent.mkdir(parents=True)
            checkpoint_path.write_text('{"cursor": 1}\n', encoding="utf-8")

            state = read_json(task_dir / "state.json")
            state["suspension"]["checkpoint_ref"] = checkpoint_ref
            events = [
                json.loads(line)
                for line in (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            events[-1]["projection"]["suspension"]["checkpoint_ref"] = checkpoint_ref
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task_dir / "wait-events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )

            repository = Path(__file__).resolve().parents[1]
            state_validator = schema_validator(repository / "schemas/state.schema.json")
            event_validator = schema_validator(repository / "schemas/wait-event.schema.json")
            safe_schema_errors = [
                *state_validator.iter_errors(state),
                *event_validator.iter_errors(events[-1]),
            ]
            runtime_accepted = True
            try:
                suspend_task(root, task_id, timeout_seconds=60)
            except SystemExit:
                runtime_accepted = False
            audit_item = TaskAudit(task_dir).check_deterministic_wake()

            unsafe_rejections = []
            for unsafe_ref in ("/tmp/checkpoint.json", "../checkpoint.json"):
                unsafe_state = json.loads(json.dumps(state))
                unsafe_event = json.loads(json.dumps(events[-1]))
                unsafe_state["suspension"]["checkpoint_ref"] = unsafe_ref
                unsafe_event["projection"]["suspension"]["checkpoint_ref"] = unsafe_ref
                unsafe_rejections.append(
                    bool(list(state_validator.iter_errors(unsafe_state)))
                    and bool(list(event_validator.iter_errors(unsafe_event)))
                )

            self.assertEqual(
                (runtime_accepted, audit_item.status, safe_schema_errors, unsafe_rejections),
                (True, "pass", [], [True, True]),
            )

    def test_duplicate_wake_returns_the_recorded_result_and_conflict_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-DUPLICATE"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            receipt = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                2,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt) + "\n")

            first = resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#2",
            )
            state_after_first = read_json(task_dir / "state.json")
            events_after_first = (task_dir / "wait-events.jsonl").read_bytes()
            result_ref = first["accepted_wake"]["result_ref"]
            result_after_first = (task_dir / result_ref).read_bytes()

            duplicate = resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#2",
            )

            self.assertEqual(duplicate, first)
            self.assertEqual(read_json(task_dir / "state.json")["revision"], state_after_first["revision"])
            self.assertEqual((task_dir / "wait-events.jsonl").read_bytes(), events_after_first)
            self.assertEqual((task_dir / result_ref).read_bytes(), result_after_first)
            with self.assertRaisesRegex(SystemExit, "Conflicting wake"):
                resume_suspended_task(
                    root,
                    task_id,
                    "receipt",
                    resume_ref="dispatch-receipts.jsonl#1",
                )

    def test_concurrent_duplicate_wake_commits_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-CONCURRENT"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            receipt = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                2,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt) + "\n")

            def wake() -> dict[str, object]:
                return resume_suspended_task(
                    root,
                    task_id,
                    "receipt",
                    resume_ref="dispatch-receipts.jsonl#2",
                )

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _index: wake(), range(2)))

            self.assertEqual(results[0], results[1])
            events = (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(events), 2)
            self.assertEqual(read_json(task_dir / "state.json")["revision"], 2)

    def test_receipt_and_execution_deadline_race_commits_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-RECEIPT-DEADLINE-RACE"
            task_dir, items = self.write_deterministic_wait_fixture(root, task_id)
            suspension = suspend_task(root, task_id, timeout_seconds=0)
            receipt = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                2,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(receipt) + "\n")

            def wake(resume_event: str) -> tuple[str, object]:
                try:
                    resume_ref = (
                        "dispatch-receipts.jsonl#2"
                        if resume_event == "receipt"
                        else "state.json#execution_deadline"
                    )
                    return (
                        "accepted",
                        resume_suspended_task(
                            root,
                            task_id,
                            resume_event,
                            resume_ref=resume_ref,
                        ),
                    )
                except SystemExit as error:
                    return ("rejected", str(error))

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(wake, ["receipt", "timeout"]))

            accepted = [value for status, value in results if status == "accepted"]
            rejected = [value for status, value in results if status == "rejected"]
            state = read_json(task_dir / "state.json")
            events = (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual((len(accepted), len(rejected)), (1, 1))
            self.assertIn("Conflicting wake", str(rejected[0]))
            self.assertIn(state["suspension"]["accepted_wake"]["wake_reason"], {"dependency_ready", "timeout"})
            self.assertEqual(len(events), 2)
            self.assertEqual(state["revision"], 2)

    def test_committed_wait_event_repairs_missing_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-REPLAY"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)

            with patch("valp_cli.workflow.write_json", side_effect=OSError("crash before projection")):
                with self.assertRaisesRegex(OSError, "crash before projection"):
                    suspend_task(root, task_id, timeout_seconds=60)

            self.assertEqual(read_json(task_dir / "state.json")["revision"], 0)
            recovered = suspend_task(root, task_id, timeout_seconds=60)
            state = read_json(task_dir / "state.json")
            self.assertEqual(recovered["status"], "waiting")
            self.assertEqual(state["status"], "suspended")
            self.assertEqual(state["revision"], 1)
            self.assertEqual(len((task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()), 1)

    def test_malformed_wait_event_ledger_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-MALFORMED"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            suspend_task(root, task_id, timeout_seconds=60)
            with (task_dir / "wait-events.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("{truncated\n")

            with self.assertRaisesRegex(SystemExit, "Invalid JSONL record"):
                suspend_task(root, task_id, timeout_seconds=60)

    def test_wait_event_replay_rejects_boolean_sequence_and_revision_fields(self) -> None:
        mutations = {
            "event_sequence": True,
            "state_revision_before": False,
            "state_revision_after": True,
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    task_id = f"TASK-WAIT-BOOLEAN-EVENT-{field.upper()}"
                    task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
                    suspend_task(root, task_id, timeout_seconds=60)
                    event = json.loads(
                        (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").strip()
                    )
                    event[field] = value
                    (task_dir / "wait-events.jsonl").write_text(
                        json.dumps(event) + "\n",
                        encoding="utf-8",
                    )

                    runtime_rejected = False
                    try:
                        suspend_task(root, task_id, timeout_seconds=60)
                    except SystemExit:
                        runtime_rejected = True
                    audit_item = TaskAudit(task_dir).check_deterministic_wake()

                    self.assertEqual((runtime_rejected, audit_item.status), (True, "fail"))

    def test_wait_replay_and_audit_reject_boolean_task_revision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-BOOLEAN-STATE-REVISION"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            suspend_task(root, task_id, timeout_seconds=60)
            state = read_json(task_dir / "state.json")
            state["revision"] = True
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

            runtime_rejected = False
            try:
                suspend_task(root, task_id, timeout_seconds=60)
            except SystemExit:
                runtime_rejected = True
            audit_item = TaskAudit(task_dir).check_deterministic_wake()

            self.assertEqual((runtime_rejected, audit_item.status), (True, "fail"))

    def test_wait_resumes_from_new_terminal_worker_receipt_without_sleeping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-WAIT-RECEIPT",
                        "Review the task evidence",
                        runtime="manual",
                    )

            agent = read_json(task_dir / "routing.json")["selected_agents"][0]
            receipts_path = task_dir / "dispatch-receipts.jsonl"
            with receipts_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": "2026-07-11T00:00:00Z",
                    "agent": agent,
                    "event": "manual_delivery_attested",
                    "dispatch_ref": f"agents/{agent}/dispatch.md",
                }) + "\n")
            suspend_task(root, "TASK-WAIT-RECEIPT", timeout_seconds=60)
            with receipts_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": "2026-07-11T00:00:01Z",
                    "agent": agent,
                    "event": "manual_result_attested",
                    "dispatch_ref": f"agents/{agent}/dispatch.md",
                }) + "\n")

            with patch("valp_cli.workflow.time.sleep", side_effect=AssertionError("receipt should resume before sleep")):
                result = wait_for_task(root, "TASK-WAIT-RECEIPT", timeout_seconds=60)

            state = read_json(task_dir / "state.json")
            self.assertEqual(result["resume_event"], "receipt")
            self.assertEqual(result["resume_ref"], "dispatch-receipts.jsonl#3")
            self.assertEqual(state["status"], "executing")

    def test_resume_command_records_explicit_user_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-WAIT-USER",
                        "Review the task evidence",
                        runtime="manual",
                    )

            agent = read_json(task_dir / "routing.json")["selected_agents"][0]
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": "2026-07-11T00:00:00Z",
                    "agent": agent,
                    "event": "manual_delivery_attested",
                    "dispatch_ref": f"agents/{agent}/dispatch.md",
                }) + "\n")
            suspension = suspend_task(root, "TASK-WAIT-USER", timeout_seconds=60)
            resume_ref = self.write_exception_wake_evidence(
                task_dir,
                "TASK-WAIT-USER",
                suspension,
                "user_input",
                "user",
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main([
                    "resume",
                    "TASK-WAIT-USER",
                    "--workspace",
                    str(root),
                    "--event",
                    "user_input",
                    "--ref",
                    resume_ref,
                    "--json",
                ])

            result = json.loads(output.getvalue())
            state = read_json(task_dir / "state.json")
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["resume_event"], "user_input")
            self.assertEqual(state["status"], "executing")

    def test_runtime_failure_resume_requires_existing_task_local_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-WAIT-FAILURE",
                        "Review the task evidence",
                        runtime="manual",
                    )

            agent = read_json(task_dir / "routing.json")["selected_agents"][0]
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "ts": "2026-07-11T00:00:00Z",
                    "agent": agent,
                    "event": "manual_delivery_attested",
                    "dispatch_ref": f"agents/{agent}/dispatch.md",
                }) + "\n")
            suspension = suspend_task(root, "TASK-WAIT-FAILURE", timeout_seconds=60)

            with self.assertRaises(SystemExit):
                resume_suspended_task(
                    root,
                    "TASK-WAIT-FAILURE",
                    "runtime_failure",
                    resume_ref="evidence/missing-runtime-failure.log",
                )
            resume_ref = self.write_exception_wake_evidence(
                task_dir,
                "TASK-WAIT-FAILURE",
                suspension,
                "runtime_failure",
                "runtime",
            )
            with self.assertRaisesRegex(SystemExit, "supporting evidence"):
                resume_suspended_task(
                    root,
                    "TASK-WAIT-FAILURE",
                    "runtime_failure",
                    resume_ref=resume_ref,
                )

    def test_external_wakes_require_a_structured_evidence_ref(self) -> None:
        for resume_event in ["runtime_failure", "cancellation", "user_input"]:
            with self.subTest(resume_event=resume_event):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    task_id = f"TASK-WAIT-EXTERNAL-{resume_event.upper()}"
                    task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
                    suspend_task(root, task_id, timeout_seconds=60)
                    before = read_json(task_dir / "state.json")

                    with self.assertRaises(SystemExit):
                        resume_suspended_task(root, task_id, resume_event)

                    after = read_json(task_dir / "state.json")
                    self.assertEqual(after, before)

    def test_external_wake_evidence_binds_the_current_suspension(self) -> None:
        cases = [
            "extra_field",
            "task_id",
            "suspension_id",
            "suspension_epoch",
            "event",
            "principal_type",
            "reason",
        ]
        for case in cases:
            with self.subTest(case=case):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    task_id = f"TASK-WAIT-EVIDENCE-{case.upper()}"
                    task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
                    suspension = suspend_task(root, task_id, timeout_seconds=60)
                    ref = self.write_exception_wake_evidence(
                        task_dir,
                        task_id,
                        suspension,
                        "user_input",
                        "user",
                    )
                    evidence = read_json(task_dir / ref)
                    if case == "extra_field":
                        evidence["unexpected"] = True
                    elif case == "task_id":
                        evidence["task_id"] = "OTHER-TASK"
                    elif case == "suspension_id":
                        evidence["suspension_id"] = "sha256:" + "0" * 64
                    elif case == "suspension_epoch":
                        evidence["suspension_epoch"] = int(suspension["suspension_epoch"]) + 1
                    elif case == "event":
                        evidence["event"] = "cancellation"
                    elif case == "principal_type":
                        evidence["principal"]["type"] = "runtime"
                    elif case == "reason":
                        evidence["reason"] = ""
                    (task_dir / ref).write_text(json.dumps(evidence), encoding="utf-8")
                    before = read_json(task_dir / "state.json")

                    with self.assertRaises(SystemExit):
                        resume_suspended_task(
                            root,
                            task_id,
                            "user_input",
                            resume_ref=ref,
                        )

                    self.assertEqual(read_json(task_dir / "state.json"), before)

    def test_external_wake_records_source_digest_and_rejects_changed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-EXTERNAL-DIGEST"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            ref = self.write_exception_wake_evidence(
                task_dir,
                task_id,
                suspension,
                "user_input",
                "user",
            )
            source_bytes = (task_dir / ref).read_bytes()
            expected_digest = "sha256:" + hashlib.sha256(source_bytes).hexdigest()

            result = resume_suspended_task(
                root,
                task_id,
                "user_input",
                resume_ref=ref,
            )

            external_event = result["accepted_wake"]["external_event"]
            self.assertEqual(external_event["source_ref"], ref)
            self.assertEqual(external_event["source_digest"], expected_digest)
            self.assertEqual(external_event["principal"], {"type": "user", "id": "test-user"})
            wait_event = json.loads(
                (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()[-1]
            )
            self.assertEqual(wait_event["external_event"], external_event)
            wake_result = read_json(task_dir / result["accepted_wake"]["result_ref"])
            self.assertEqual(wake_result["external_event"], external_event)
            self.assertEqual(TaskAudit(task_dir).check_deterministic_wake().status, "pass")
            repository_root = Path(__file__).resolve().parents[1]
            schema_artifacts = [
                (task_dir / ref, repository_root / "schemas/exception-wake.schema.json"),
                (task_dir / "state.json", repository_root / "schemas/state.schema.json"),
                (
                    task_dir / result["accepted_wake"]["result_ref"],
                    repository_root / "schemas/wake-result.schema.json",
                ),
            ]
            for artifact_path, schema_path in schema_artifacts:
                validator = schema_validator(schema_path)
                self.assertEqual(
                    list(validator.iter_errors(read_json(artifact_path))),
                    [],
                    artifact_path.name,
                )
            event_validator = schema_validator(repository_root / "schemas/wait-event.schema.json")
            for line in (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines():
                self.assertEqual(list(event_validator.iter_errors(json.loads(line))), [])

            changed = read_json(task_dir / ref)
            changed["reason"] = "changed user input"
            (task_dir / ref).write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "source evidence changed"):
                resume_suspended_task(
                    root,
                    task_id,
                    "user_input",
                    resume_ref=ref,
                )
            tampered = TaskAudit(task_dir).check_deterministic_wake()
            self.assertEqual(tampered.status, "fail")
            self.assertIn("changed", tampered.message.lower())

    def test_exception_wake_cannot_forge_work_item_sets_across_replay_and_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-EXCEPTION-SETS"
            task_dir, items = self.write_deterministic_wait_fixture(
                root,
                task_id,
                [
                    {"agent": "codex", "role": "implementer"},
                    {"agent": "claude", "role": "reviewer"},
                ],
            )
            suspension = suspend_task(root, task_id, timeout_seconds=60)
            completion = self.deterministic_receipt(
                task_id,
                items[0],
                "dispatch_completed",
                3,
                suspension_epoch=int(suspension["suspension_epoch"]),
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(completion) + "\n")
            resume_suspended_task(
                root,
                task_id,
                "receipt",
                resume_ref="dispatch-receipts.jsonl#3",
            )
            waiting = read_json(task_dir / "state.json")["suspension"]
            resume_ref = self.write_exception_wake_evidence(
                task_dir,
                task_id,
                waiting,
                "user_input",
                "user",
            )
            accepted = resume_suspended_task(
                root,
                task_id,
                "user_input",
                resume_ref=resume_ref,
            )

            state = read_json(task_dir / "state.json")
            result_ref = str(accepted["accepted_wake"]["result_ref"])
            events = [
                json.loads(line)
                for line in (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            forged_suspension = state["suspension"]
            forged_suspension["completed_work_item_ids"] = [
                str(items[0]["work_item_id"]),
                str(items[1]["work_item_id"]),
            ]
            forged_suspension["pending_work_item_ids"] = []
            events[-1]["projection"]["suspension"] = forged_suspension
            wake_result = read_json(task_dir / result_ref)
            wake_result["completed_work_item_ids"] = forged_suspension["completed_work_item_ids"]
            wake_result["pending_work_item_ids"] = []
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task_dir / "wait-events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )
            (task_dir / result_ref).write_text(json.dumps(wake_result), encoding="utf-8")

            runtime_rejected = False
            try:
                resume_suspended_task(
                    root,
                    task_id,
                    "user_input",
                    resume_ref=resume_ref,
                )
            except SystemExit:
                runtime_rejected = True
            audit_item = TaskAudit(task_dir).check_deterministic_wake()

            self.assertEqual((runtime_rejected, audit_item.status), (True, "fail"))
            self.assertIn("work-item", audit_item.message.lower())

    def test_committed_suspension_barrier_must_match_the_wait_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-BARRIER-SHRINK"
            task_dir, items = self.write_deterministic_wait_fixture(
                root,
                task_id,
                [
                    {"agent": "codex", "role": "implementer"},
                    {"agent": "claude", "role": "reviewer"},
                ],
            )
            suspend_task(root, task_id, timeout_seconds=60)
            state = read_json(task_dir / "state.json")
            state["suspension"]["required_work_items"] = [items[0]]
            state["suspension"]["required_work_item_ids"] = [items[0]["work_item_id"]]
            state["suspension"]["pending_work_item_ids"] = [items[0]["work_item_id"]]
            events = [
                json.loads(line)
                for line in (task_dir / "wait-events.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            events[0]["projection"]["suspension"] = state["suspension"]
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (task_dir / "wait-events.jsonl").write_text(
                "".join(json.dumps(event) + "\n" for event in events),
                encoding="utf-8",
            )

            runtime_rejected = False
            try:
                suspend_task(root, task_id, timeout_seconds=60)
            except SystemExit:
                runtime_rejected = True
            audit_item = TaskAudit(task_dir).check_deterministic_wake()

            self.assertEqual((runtime_rejected, audit_item.status), (True, "fail"))
            self.assertIn("policy", audit_item.message.lower())

    def test_failure_and_cancellation_resume_to_visible_handling_states(self) -> None:
        cases = [
            ("runtime_failure", "blocked", "runtime"),
            ("cancellation", "cancelled", "policy"),
        ]
        for resume_event, expected_status, principal_type in cases:
            with self.subTest(resume_event=resume_event):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    task_id = f"TASK-WAIT-{resume_event.upper()}"
                    task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
                    suspension = suspend_task(root, task_id, timeout_seconds=60)
                    supporting_refs: list[str] = []
                    if resume_event == "runtime_failure":
                        failure_path = task_dir / "evidence" / "runtime-failure.log"
                        failure_path.parent.mkdir(parents=True, exist_ok=True)
                        failure_path.write_text("runtime failed\n", encoding="utf-8")
                        supporting_refs = ["evidence/runtime-failure.log"]
                    resume_ref = self.write_exception_wake_evidence(
                        task_dir,
                        task_id,
                        suspension,
                        resume_event,
                        principal_type,
                        supporting_refs,
                    )

                    resume_suspended_task(
                        root,
                        task_id,
                        resume_event,
                        resume_ref=resume_ref,
                    )

                    state = read_json(task_dir / "state.json")
                    self.assertEqual(state["status"], expected_status)
                    self.assertEqual(state["suspension"]["resume_event"], resume_event)

    def test_generated_dispatches_enforce_role_specific_total_budgets(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-11T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests"],
                    "must_not_do": ["must not bypass approval gates"],
                },
                "claude": {
                    "active": True,
                    "role": ["review", "code_review", "risk_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["reviews source and evidence"],
                    "must_not_do": ["must not edit source"],
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-DISPATCH-BUDGET",
                        "Fix the runtime state machine, run focused tests, and review receipt semantics.",
                        runtime="queue",
                    )

            routing = read_json(task_dir / "routing.json")
            budgets = routing["dispatch_payload_budgets"]
            self.assertEqual(budgets["codex"]["role"], "implementer")
            self.assertEqual(budgets["claude"]["role"], "reviewer")
            for agent in ["codex", "claude"]:
                dispatch = (task_dir / "agents" / agent / "dispatch.md").read_text(encoding="utf-8")
                budget = budgets[agent]
                self.assertLessEqual(len(dispatch), budget["max_chars"])
                self.assertLessEqual((len(dispatch) + 3) // 4, budget["max_reference_tokens"])
                self.assertEqual(budget["actual_chars"], len(dispatch))
                self.assertIn("## Permission Boundary", dispatch)
                self.assertIn("Do not write skills, plugins, memory, MCP configuration, or agent configuration", dispatch)
                self.assertIn("## Expected Evidence", dispatch)
                self.assertIn("Payload budget:", dispatch)

            codex_dispatch = task_dir / "agents" / "codex" / "dispatch.md"
            codex_dispatch.write_text(
                codex_dispatch.read_text(encoding="utf-8") + ("x" * 500),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SystemExit, "exceeds role budget"):
                dispatch_task(
                    root,
                    "TASK-DISPATCH-BUDGET",
                    agent="codex",
                    role="coordinator",
                    submit=True,
                    runtime="queue",
                )

            delegation_policy = read_json(task_dir / "delegation-policy.json")
            self.assertEqual(
                delegation_policy["live_self_modification"]["mode"],
                "forbidden",
            )

    def test_long_task_identity_keeps_reviewer_dispatch_within_role_budget(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-08-07T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification"],
                    "skills": [],
                    "mcp_servers": [],
                },
                "claude": {
                    "active": True,
                    "role": ["review", "code_review", "risk_review"],
                    "skills": [],
                    "mcp_servers": [],
                },
            },
        }
        task_id = "VALP-HERDR-FRESH-CODEX-BOOTSTRAP-20260807-G5-S7-PREFLIGHT-REVIEW"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(
                        root,
                        task_id,
                        "Repair preflight projection and independently review the frozen source.",
                        profile="agent-runtime",
                        runtime="queue",
                    )

            routing = read_json(task_dir / "routing.json")
            budget = routing["dispatch_payload_budgets"]["claude"]
            dispatch = (task_dir / "agents" / "claude" / "dispatch.md").read_text(
                encoding="utf-8"
            )

        self.assertLessEqual(len(dispatch), budget["max_chars"])
        self.assertLessEqual((len(dispatch) + 3) // 4, budget["max_reference_tokens"])
        self.assertIn("## VALP Control Contract (Load First)", dispatch)
        self.assertIn("## Permission Boundary", dispatch)
        self.assertIn("read-only", dispatch.lower())
        self.assertIn("## Expected Evidence", dispatch)
        self.assertIn("control_contract_status: honored", dispatch)
        self.assertIn("task.md", dispatch)
        self.assertIn("context-pack.json", dispatch)

    def test_reroute_preserves_delegation_violations_and_blocked_state(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-13T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "review", "risk_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates, edits, tests, and reviews"],
                    "must_not_do": ["must not write memory while delegated"],
                }
            },
        }
        violation = {
            "agent": "codex",
            "surface": "memory",
            "evidence_ref": "evidence/delegation-violation.md",
            "detected_at": "2026-07-13T10:30:00Z",
            "earliest_affected_receipt": 2,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-DELEGATION-REROUTE",
                        "Implement and independently review a runtime correction.",
                        runtime="queue",
                    )
                    evidence_path = task_dir / violation["evidence_ref"]
                    evidence_path.parent.mkdir(parents=True, exist_ok=True)
                    evidence_path.write_text("violation observed\n", encoding="utf-8")
                    policy = read_json(task_dir / "delegation-policy.json")
                    policy["violations"] = [violation]
                    (task_dir / "delegation-policy.json").write_text(json.dumps(policy), encoding="utf-8")
                    state = read_json(task_dir / "state.json")
                    state["status"] = "blocked"
                    state["gates"]["expected_evidence"] = "blocked"
                    (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

                    with patch(
                        "valp_cli.workflow.collect_runtime_preflight",
                        return_value=self.routed_test_preflight(task_dir),
                    ):
                        route_task(root, "TASK-DELEGATION-REROUTE", runtime="queue")

            rerouted_policy = read_json(task_dir / "delegation-policy.json")
            rerouted_state = read_json(task_dir / "state.json")
            self.assertEqual(rerouted_policy["violations"], [violation])
            self.assertEqual(rerouted_state["status"], "blocked")
            self.assertEqual(rerouted_state["gates"]["expected_evidence"], "blocked")

    def test_publish_compacts_reviewer_dispatch_with_multiple_skill_recommendations(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-13T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification"],
                    "skills": ["tdd"],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests"],
                    "must_not_do": ["must not bypass approval gates"],
                },
                "reviewer-provider": {
                    "active": True,
                    "role": ["review", "code_review", "risk_review"],
                    "skills": ["triage", "handoff", "code-review"],
                    "mcp_servers": [],
                    "strengths": ["reviews source and evidence"],
                    "must_not_do": ["must not edit source"],
                },
            },
        }
        long_recommendation_task = (
            "Review the change against the task contract, verification evidence, runtime boundary, "
            "failure behavior, replay guarantees, and provider-neutral conformance requirements."
        )
        recommendation_payload = {
            "batch": True,
            "num_tasks": 1,
            "results": [
                {
                    "task": long_recommendation_task,
                    "routing": {
                        "priority": "P1",
                        "decision": "auto-load",
                        "reason": "Strong installed workflow match.",
                    },
                    "matches": [
                        {
                            "skill": skill,
                            "installed": True,
                            "confidence": confidence,
                            "mode": "auto-load",
                        }
                        for skill, confidence in [
                            ("triage", 0.41),
                            ("handoff", 0.36),
                            ("code-review", 0.32),
                        ]
                    ],
                    "missing_skills": [],
                }
            ],
            "missing_skills": [],
            "routing": {
                "priority": "P1",
                "decision": "auto-load",
                "reason": "Highest-priority routing decision across batch tasks.",
            },
        }

        def fake_run_command(command, timeout=8.0, input_text=None, stdout_limit=4000, stderr_limit=4000):
            if command[0] == "task-skill-router":
                return {
                    "command": command,
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps(recommendation_payload),
                    "stderr": "",
                }
            return {
                "command": command,
                "ok": True,
                "exit_code": 0,
                "stdout": "{}",
                "stderr": "",
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=["task-skill-router"]):
                    with patch("valp_cli.workflow.run_command", side_effect=fake_run_command):
                        task_dir = self.publish_routed_task(
                            root,
                            "TASK-REVIEWER-BUDGET",
                            "Implement and independently review a runtime correction.",
                            runtime="queue",
                        )

            routing = read_json(task_dir / "routing.json")
            reviewer_budget = routing["dispatch_payload_budgets"]["reviewer-provider"]
            reviewer_dispatch = (task_dir / "agents" / "reviewer-provider" / "dispatch.md").read_text(
                encoding="utf-8"
            )
            self.assertEqual(reviewer_budget["role"], "reviewer")
            self.assertLessEqual(len(reviewer_dispatch), 2400)
            self.assertIn("## Permission Boundary", reviewer_dispatch)
            self.assertIn("## Expected Evidence", reviewer_dispatch)
            self.assertIn("skill-slices/reviewer-provider.json", reviewer_dispatch)
            self.assertNotIn("- `.herdr-loop/tasks/TASK-REVIEWER-BUDGET/skill-recommendations.json`", reviewer_dispatch)

    def test_dispatch_submit_enforces_role_evidence_dependencies(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-12T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "hermes": {
                    "active": True,
                    "role": ["coordination", "state", "approval"],
                    "strengths": ["coordination", "state gates"],
                    "skills": [],
                    "mcp_servers": [],
                },
                "codex": {
                    "active": True,
                    "role": ["implementation", "verification"],
                    "strengths": ["edits files", "runs tests"],
                    "skills": [],
                    "mcp_servers": [],
                },
                "claude": {
                    "active": True,
                    "role": ["review", "code_review", "risk_review"],
                    "strengths": ["read-only review"],
                    "skills": [],
                    "mcp_servers": [],
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-STAGED-DISPATCH",
                        "Fix agent runtime code and review it",
                        runtime="queue",
                    )

            dependencies = read_json(task_dir / "submission-dependencies.json")
            self.assertEqual(
                [item["id"] for item in dependencies["dependencies"]],
                ["coordinator-before-implementer", "implementer-before-reviewer"],
            )
            self.assertEqual(
                dependencies["dependencies"][0]["prerequisite_refs"],
                ["agents/hermes/self-review.md"],
            )
            self.assertEqual(
                dependencies["dependencies"][1]["prerequisite_refs"],
                ["agents/codex/evidence.md", "evidence/verification.md"],
            )
            dry_run_commands = dispatch_task(
                root,
                "TASK-STAGED-DISPATCH",
                runtime="queue",
            )
            self.assertEqual(len(dry_run_commands), 1)
            self.assertIn("phase=coordinator", dry_run_commands[0])
            state = read_json(task_dir / "state.json")
            delegation_marker = state.pop("delegation_policy")
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "Delegation policy"):
                dispatch_task(
                    root,
                    "TASK-STAGED-DISPATCH",
                    agent="hermes",
                    role="coordinator",
                    submit=True,
                    runtime="queue",
                )
            state["delegation_policy"] = delegation_marker
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

            delegation_path = task_dir / "delegation-policy.json"
            delegation_policy = read_json(delegation_path)
            delegation_policy["violations"] = [
                {
                    "agent": "hermes",
                    "surface": "skills",
                    "evidence_ref": "evidence/live-config-violation.md",
                    "detected_at": "2026-07-12T00:00:30Z",
                }
            ]
            delegation_path.write_text(json.dumps(delegation_policy), encoding="utf-8")
            receipts = task_dir / "dispatch-receipts.jsonl"
            receipts_before = receipts.read_bytes()
            preflight_path = task_dir / "runtime-preflight.json"
            preflight_before = preflight_path.read_bytes() if preflight_path.exists() else None
            with patch("valp_cli.workflow.collect_runtime_preflight") as collect_preflight:
                with self.assertRaisesRegex(SystemExit, "live self-modification violation"):
                    dispatch_task(
                        root,
                        "TASK-STAGED-DISPATCH",
                        agent="hermes",
                        role="coordinator",
                        submit=True,
                        runtime="queue",
                    )
            collect_preflight.assert_not_called()
            self.assertEqual(receipts.read_bytes(), receipts_before)
            if preflight_before is None:
                self.assertFalse(preflight_path.exists())
            else:
                self.assertEqual(preflight_path.read_bytes(), preflight_before)
            self.assertFalse((task_dir / "queue" / "hermes-coordinator.json").exists())
            delegation_policy["violations"] = []
            delegation_path.write_text(json.dumps(delegation_policy), encoding="utf-8")

            commands = dispatch_task(
                root,
                "TASK-STAGED-DISPATCH",
                submit=True,
                runtime="queue",
            )
            self.assertEqual(len(commands), 1)
            self.assertIn("phase=coordinator", commands[0])
            self.assertTrue((task_dir / "queue" / "hermes-coordinator.json").is_file())
            receipts_before = receipts.read_bytes()
            preflight_before = preflight_path.read_bytes() if preflight_path.exists() else None
            with patch("valp_cli.workflow.collect_runtime_preflight") as collect_preflight:
                with self.assertRaisesRegex(SystemExit, "unmet prerequisites"):
                    dispatch_task(
                        root,
                        "TASK-STAGED-DISPATCH",
                        agent="codex",
                        role="implementer",
                        submit=True,
                        runtime="queue",
                    )
            collect_preflight.assert_not_called()
            self.assertEqual(receipts.read_bytes(), receipts_before)
            if preflight_before is None:
                self.assertFalse(preflight_path.exists())
            else:
                self.assertEqual(preflight_path.read_bytes(), preflight_before)
            self.assertFalse((task_dir / "queue" / "codex-implementer.json").exists())

            (task_dir / "agents" / "hermes" / "self-review.md").write_text("gate passed\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "unmet prerequisites"):
                dispatch_task(
                    root,
                    "TASK-STAGED-DISPATCH",
                    agent="codex",
                    role="implementer",
                    submit=True,
                    runtime="queue",
                )

            coordinator_item = next(
                item for item in dependencies["work_items"] if item["role"] == "coordinator"
            )
            rogue_completion = self.deterministic_receipt(
                "TASK-STAGED-DISPATCH",
                coordinator_item,
                "dispatch_completed",
                2,
                suspension_epoch=1,
            )
            rogue_completion.update({
                "role": "reviewer",
                "work_item_id": "reviewer:hermes",
                "dispatch_id": "rogue-dispatch",
                "dispatch_generation": 99,
            })
            with receipts.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(rogue_completion) + "\n")
            with self.assertRaisesRegex(SystemExit, "unmet prerequisites"):
                dispatch_task(
                    root,
                    "TASK-STAGED-DISPATCH",
                    agent="codex",
                    role="implementer",
                    submit=True,
                    runtime="queue",
                )
            self.assertFalse((task_dir / "queue" / "codex-implementer.json").exists())

            with receipts.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self.deterministic_receipt(
                            "TASK-STAGED-DISPATCH",
                            coordinator_item,
                            "dispatch_completed",
                            3,
                            suspension_epoch=1,
                        )
                    )
                    + "\n"
                )
            with patch(
                "valp_cli.workflow.collect_runtime_preflight",
                return_value=self.routed_test_preflight(task_dir),
            ):
                dispatch_task(
                    root,
                    "TASK-STAGED-DISPATCH",
                    agent="codex",
                    role="implementer",
                    submit=True,
                    runtime="queue",
                )

            (task_dir / "agents" / "codex" / "evidence.md").write_text("implemented\n", encoding="utf-8")
            (task_dir / "evidence").mkdir(exist_ok=True)
            (task_dir / "evidence" / "verification.md").write_text("verified\n", encoding="utf-8")
            (task_dir / "evidence-status.json").write_text(
                json.dumps(
                    {
                        "schema_version": "valp-evidence-status.v1",
                        "evidence": {
                            "agents/codex/evidence.md": {"status": "invalid"},
                            "evidence/verification.md": {"status": "valid"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            with receipts.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self.deterministic_receipt(
                            "TASK-STAGED-DISPATCH",
                            next(
                                item
                                for item in dependencies["work_items"]
                                if item["role"] == "implementer"
                            ),
                            "dispatch_completed",
                            5,
                            suspension_epoch=1,
                        )
                    )
                    + "\n"
                )
            with self.assertRaisesRegex(SystemExit, "unmet prerequisites"):
                dispatch_task(
                    root,
                    "TASK-STAGED-DISPATCH",
                    agent="claude",
                    role="reviewer",
                    submit=True,
                    runtime="queue",
                )

            evidence_status = read_json(task_dir / "evidence-status.json")
            evidence_status["evidence"]["agents/codex/evidence.md"]["status"] = "valid"
            (task_dir / "evidence-status.json").write_text(json.dumps(evidence_status), encoding="utf-8")
            with patch(
                "valp_cli.workflow.collect_runtime_preflight",
                return_value=self.routed_test_preflight(task_dir),
            ):
                dispatch_task(
                    root,
                    "TASK-STAGED-DISPATCH",
                    agent="claude",
                    role="reviewer",
                    submit=True,
                    runtime="queue",
                )
            self.assertTrue((task_dir / "queue" / "claude-reviewer.json").is_file())

    def test_dependency_order_uses_receipt_line_order_not_timestamps(self) -> None:
        task_id = "TASK-ORDERED-RECEIPTS"
        dependencies = build_submission_dependencies(
            task_id,
            {"coordinator": "hermes", "implementer": "codex"},
        )
        coordinator = next(
            item for item in dependencies["work_items"] if item["role"] == "coordinator"
        )
        implementer = next(
            item for item in dependencies["work_items"] if item["role"] == "implementer"
        )
        prerequisite = self.deterministic_receipt(
            task_id,
            coordinator,
            "dispatch_completed",
            1,
            suspension_epoch=1,
        )
        prerequisite["ts"] = "2026-07-12T00:02:00Z"
        dependent = self.deterministic_receipt(
            task_id,
            implementer,
            "dispatch_submitted",
            2,
        )
        dependent["ts"] = "2026-07-12T00:01:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self_review = task_dir / "agents" / "hermes" / "self-review.md"
            self_review.parent.mkdir(parents=True)
            self_review.write_text("gate passed\n", encoding="utf-8")
            evidence_status = {
                "evidence": {"agents/hermes/self-review.md": {"status": "valid"}}
            }

            self.assertEqual(
                dependency_order_errors(
                    dependencies,
                    [prerequisite, dependent],
                    task_dir,
                    evidence_status,
                    manual_mode=False,
                ),
                [],
            )
            errors = dependency_order_errors(
                dependencies,
                [dependent, prerequisite],
                task_dir,
                evidence_status,
                manual_mode=False,
            )
        self.assertEqual(len(errors), 1)
        self.assertIn("before receipt line 1", errors[0])

    def test_v2_dependency_order_ignores_preserved_legacy_receipts(self) -> None:
        task_id = "TASK-LEGACY-ORDERED-RECEIPTS"
        dependencies = build_submission_dependencies(
            task_id,
            {"coordinator": "hermes", "implementer": "codex"},
        )
        coordinator = next(
            item for item in dependencies["work_items"] if item["role"] == "coordinator"
        )
        implementer = next(
            item for item in dependencies["work_items"] if item["role"] == "implementer"
        )
        prerequisite = self.deterministic_receipt(
            task_id,
            coordinator,
            "dispatch_completed",
            1,
            suspension_epoch=1,
        )
        legacy_dependent = {
            "agent": "codex",
            "event": "dispatch_submitted",
            "exit_code": 0,
            "dispatch_ref": "agents/codex/dispatch.md",
            "expected_refs": implementer["expected_refs"],
            "runtime": {"pane_id": "w5:pS"},
        }

        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            self_review = task_dir / "agents" / "hermes" / "self-review.md"
            self_review.parent.mkdir(parents=True)
            self_review.write_text("gate passed\n", encoding="utf-8")
            evidence_status = {
                "evidence": {"agents/hermes/self-review.md": {"status": "valid"}}
            }

            self.assertEqual(
                dependency_order_errors(
                    dependencies,
                    [prerequisite, legacy_dependent],
                    task_dir,
                    evidence_status,
                    manual_mode=False,
                ),
                [],
            )

    def test_v2_dependency_order_rejects_cross_role_prerequisite_receipt(self) -> None:
        task_id = "TASK-ORDERED-IDENTITY"
        dependencies = build_submission_dependencies(
            task_id,
            {"coordinator": "hermes", "implementer": "codex"},
        )
        coordinator = next(
            item for item in dependencies["work_items"] if item["role"] == "coordinator"
        )
        implementer = next(
            item for item in dependencies["work_items"] if item["role"] == "implementer"
        )
        wrong_identity = {
            **coordinator,
            "role": "reviewer",
            "work_item_id": "reviewer:hermes",
            "dispatch_id": "rogue-dispatch",
            "dispatch_generation": 99,
        }
        prerequisite = self.deterministic_receipt(
            task_id,
            wrong_identity,
            "dispatch_completed",
            1,
            suspension_epoch=1,
        )
        dependent = self.deterministic_receipt(
            task_id,
            implementer,
            "dispatch_submitted",
            2,
        )

        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            evidence_ref = str(coordinator["expected_refs"][0])
            evidence_path = task_dir / evidence_ref
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_text("verified\n", encoding="utf-8")
            errors = dependency_order_errors(
                dependencies,
                [prerequisite, dependent],
                task_dir,
                {"evidence": {evidence_ref: {"status": "valid"}}},
                manual_mode=False,
            )

        self.assertEqual(
            errors,
            ["coordinator-before-implementer was not satisfied before receipt line 2"],
        )

    def test_colocated_reviewer_role_dispatches_only_after_implementer_completion(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-12T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": [
                        "coordination",
                        "state",
                        "approval",
                        "implementation",
                        "verification",
                        "review",
                        "code_review",
                        "risk_review",
                    ],
                    "strengths": ["coordinates, implements, verifies, and reviews"],
                    "skills": [],
                    "mcp_servers": [],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-COLOCATED-ROLES",
                        "Fix runtime code, verify it, and review the result.",
                        runtime="queue",
                    )

            routing = read_json(task_dir / "routing.json")
            self.assertEqual(
                routing["role_assignments"],
                {"coordinator": "codex", "implementer": "codex", "reviewer": "codex"},
            )
            for ref, content in {
                "agents/codex/self-review.md": "gate passed\n",
                "agents/codex/evidence.md": "implemented\n",
                "evidence/verification.md": "verified\n",
            }.items():
                path = task_dir / ref
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            (task_dir / "evidence-status.json").write_text(
                json.dumps(
                    {
                        "schema_version": "valp-evidence-status.v1",
                        "evidence": {
                            "agents/codex/self-review.md": {"status": "valid"},
                            "agents/codex/evidence.md": {"status": "valid"},
                            "evidence/verification.md": {"status": "valid"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            receipts = task_dir / "dispatch-receipts.jsonl"
            dependencies = read_json(task_dir / "submission-dependencies.json")
            with receipts.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self.deterministic_receipt(
                            "TASK-COLOCATED-ROLES",
                            next(
                                item
                                for item in dependencies["work_items"]
                                if item["role"] == "coordinator"
                            ),
                            "dispatch_completed",
                            1,
                            suspension_epoch=1,
                        )
                    )
                    + "\n"
                )
            with self.assertRaisesRegex(SystemExit, "unmet prerequisites"):
                main(
                    [
                        "dispatch",
                        "TASK-COLOCATED-ROLES",
                        "--workspace",
                        str(root),
                        "--agent",
                        "codex",
                        "--role",
                        "reviewer",
                        "--runtime",
                        "queue",
                        "--submit",
                    ]
                )
            self.assertFalse((task_dir / "queue" / "codex-reviewer.json").exists())

            with receipts.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self.deterministic_receipt(
                            "TASK-COLOCATED-ROLES",
                            next(
                                item
                                for item in dependencies["work_items"]
                                if item["role"] == "implementer"
                            ),
                            "dispatch_completed",
                            2,
                            suspension_epoch=1,
                        )
                    )
                    + "\n"
                )
            output = io.StringIO()
            with patch(
                "valp_cli.workflow.collect_runtime_preflight",
                return_value=self.routed_test_preflight(task_dir),
            ):
                with contextlib.redirect_stdout(output):
                    self.assertEqual(
                        main(
                            [
                                "dispatch",
                                "TASK-COLOCATED-ROLES",
                                "--workspace",
                                str(root),
                                "--agent",
                                "codex",
                                "--role",
                                "reviewer",
                                "--runtime",
                                "queue",
                                "--submit",
                            ]
                        ),
                        0,
                    )
            self.assertIn("Submitted dispatch", output.getvalue())
            queue_path = task_dir / "queue" / "codex-reviewer.json"
            self.assertTrue(queue_path.is_file())
            self.assertFalse((task_dir / "queue" / "codex-coordinator.json").exists())
            self.assertFalse((task_dir / "queue" / "codex-implementer.json").exists())
            queue_record = read_json(queue_path)
            latest_receipt = json.loads(receipts.read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(queue_record["work_item_id"], "reviewer:codex")
            self.assertEqual(queue_record["dispatch_generation"], 1)
            self.assertEqual(latest_receipt["schema_version"], "valp-dispatch-receipt.v2")
            self.assertEqual(latest_receipt["task_id"], "TASK-COLOCATED-ROLES")
            self.assertEqual(latest_receipt["work_item_id"], "reviewer:codex")
            self.assertEqual(latest_receipt["role"], "reviewer")
            self.assertEqual(latest_receipt["dispatch_generation"], 1)
            self.assertEqual(latest_receipt["event_sequence"], 3)

    def test_submission_dependencies_cover_all_producer_profiles(self) -> None:
        research = build_submission_dependencies(
            "TASK-RESEARCH",
            {"coordinator": "hermes", "researcher": "codex", "reviewer": "claude"},
        )
        self.assertEqual(
            [item["id"] for item in research["dependencies"]],
            ["coordinator-before-researcher", "researcher-before-reviewer"],
        )

        apple = build_submission_dependencies(
            "TASK-APPLE",
            {
                "coordinator": "hermes",
                "implementer": "codex",
                "prototype": "agy",
                "reviewer": "claude",
            },
        )
        self.assertEqual(
            [item["id"] for item in apple["dependencies"]],
            [
                "coordinator-before-implementer",
                "coordinator-before-prototype",
                "implementer-before-reviewer",
                "prototype-before-reviewer",
            ],
        )

    def test_v2_submission_dependency_work_items_match_routed_identity(self) -> None:
        role_assignments = {
            "coordinator": "hermes",
            "implementer": "codex",
            "reviewer": "claude",
        }
        dependencies = build_submission_dependencies("TASK-IDENTITY", role_assignments)
        dependencies["work_items"][0]["dispatch_generation"] = 2

        errors = validate_submission_dependencies(
            dependencies,
            "TASK-IDENTITY",
            role_assignments,
        )

        self.assertIn(
            "submission dependency work items do not match current role assignments and required refs",
            errors,
        )

    def test_v2_stale_generation_cannot_satisfy_submission_dependency(self) -> None:
        task_id = "TASK-STALE-DEPENDENCY"
        dependencies = build_submission_dependencies(
            task_id,
            {"implementer": "codex", "reviewer": "claude"},
        )
        implementer = next(
            item for item in dependencies["work_items"] if item["role"] == "implementer"
        )
        stale_identity = {**implementer, "dispatch_generation": 2}
        stale_receipt = self.deterministic_receipt(
            task_id,
            stale_identity,
            "dispatch_completed",
            1,
            suspension_epoch=1,
        )

        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            evidence_records = {}
            for raw_ref in implementer["expected_refs"]:
                evidence_ref = str(raw_ref)
                evidence_path = task_dir / evidence_ref
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text("verified\n", encoding="utf-8")
                evidence_records[evidence_ref] = {"status": "valid"}

            errors = unmet_dependencies_for_phases(
                dependencies,
                [("claude", "reviewer")],
                [stale_receipt],
                task_dir,
                {"evidence": evidence_records},
            )

        self.assertEqual(errors, ["implementer-before-reviewer completion receipt"])

    def test_fixed_correction_generation_satisfies_later_reviewer_dependency(self) -> None:
        task_id = "TASK-CORRECTED-DEPENDENCY"
        dependencies = build_submission_dependencies(
            task_id,
            {"implementer": "codex", "reviewer": "claude"},
        )
        implementer = next(
            item for item in dependencies["work_items"] if item["role"] == "implementer"
        )
        reviewer = next(
            item for item in dependencies["work_items"] if item["role"] == "reviewer"
        )
        replacement_refs = [
            "agents/codex/evidence-round-2.md",
            "evidence/verification-round-2.md",
        ]
        corrected_implementer = {
            **implementer,
            "dispatch_id": f"{task_id}:implementer:2",
            "dispatch_generation": 2,
            "expected_refs": replacement_refs,
        }
        completed = self.deterministic_receipt(
            task_id,
            corrected_implementer,
            "dispatch_completed",
            1,
            suspension_epoch=1,
        )
        submitted = self.deterministic_receipt(
            task_id,
            reviewer,
            "dispatch_submitted",
            2,
        )
        correction_cycle = {
            "schema_version": "valp-correction-cycle.v1",
            "task_id": task_id,
            "status": "fixed",
            "max_rounds": 3,
            "rounds": [{
                "round": 1,
                "trigger": "evidence_superseded",
                "owner": "implementer:codex",
                "status": "fixed",
                "started_at": "2026-07-21T00:00:00Z",
                "ended_at": "2026-07-21T00:01:00Z",
                "rejected_refs": implementer["expected_refs"],
                "evidence_refs": replacement_refs,
                "receipt_refs": ["dispatch-receipts.jsonl"],
            }],
            "final_outcome": "fixed",
            "final_evidence_refs": replacement_refs,
        }

        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            for ref in replacement_refs:
                path = task_dir / ref
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("verified\n", encoding="utf-8")
            evidence_status = {
                "evidence": {
                    **{
                        str(ref): {"status": "superseded"}
                        for ref in implementer["expected_refs"]
                    },
                    **{ref: {"status": "valid"} for ref in replacement_refs},
                }
            }

            errors = dependency_order_errors(
                dependencies,
                [completed, submitted],
                task_dir,
                evidence_status,
                manual_mode=False,
                correction_cycle=correction_cycle,
            )
            frontier_errors = unmet_dependencies_for_phases(
                dependencies,
                [("claude", "reviewer")],
                [completed],
                task_dir,
                evidence_status,
                correction_cycle=correction_cycle,
            )

        self.assertEqual(errors, [])
        self.assertEqual(frontier_errors, [])

    def test_correction_generation_dependency_fails_closed_for_invalid_proof(self) -> None:
        task_id = "TASK-INVALID-CORRECTED-DEPENDENCY"
        dependencies = build_submission_dependencies(
            task_id,
            {"implementer": "codex", "reviewer": "claude"},
        )
        implementer = next(
            item for item in dependencies["work_items"] if item["role"] == "implementer"
        )
        reviewer = next(
            item for item in dependencies["work_items"] if item["role"] == "reviewer"
        )
        replacement_refs = [
            "agents/codex/evidence-round-2.md",
            "evidence/verification-round-2.md",
        ]
        corrected_implementer = {
            **implementer,
            "dispatch_id": f"{task_id}:implementer:2",
            "dispatch_generation": 2,
            "expected_refs": replacement_refs,
        }
        base_completed = self.deterministic_receipt(
            task_id,
            corrected_implementer,
            "dispatch_completed",
            1,
            suspension_epoch=1,
        )
        submitted = self.deterministic_receipt(
            task_id,
            reviewer,
            "dispatch_submitted",
            2,
        )
        base_cycle = {
            "schema_version": "valp-correction-cycle.v1",
            "task_id": task_id,
            "status": "fixed",
            "max_rounds": 3,
            "rounds": [{
                "round": 1,
                "trigger": "evidence_superseded",
                "owner": "implementer:codex",
                "status": "fixed",
                "started_at": "2026-07-21T00:00:00Z",
                "ended_at": "2026-07-21T00:01:00Z",
                "rejected_refs": implementer["expected_refs"],
                "evidence_refs": replacement_refs,
                "receipt_refs": ["dispatch-receipts.jsonl"],
            }],
            "final_outcome": "fixed",
            "final_evidence_refs": replacement_refs,
        }

        for case in [
            "missing_cycle",
            "non_fixed_cycle",
            "invalid_replacement",
            "reversed_order",
            "cross_work_item",
            "blocked_after_completion",
        ]:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                task_dir = Path(tmp)
                completed = json.loads(json.dumps(base_completed))
                correction_cycle = json.loads(json.dumps(base_cycle))
                evidence_records = {
                    **{
                        str(ref): {"status": "superseded"}
                        for ref in implementer["expected_refs"]
                    },
                    **{ref: {"status": "valid"} for ref in replacement_refs},
                }
                for ref in replacement_refs:
                    path = task_dir / ref
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("verified\n", encoding="utf-8")

                if case == "missing_cycle":
                    correction_cycle = {}
                elif case == "non_fixed_cycle":
                    correction_cycle["status"] = "active"
                    correction_cycle["final_outcome"] = "blocked"
                elif case == "invalid_replacement":
                    evidence_records[replacement_refs[0]]["status"] = "invalid"
                elif case == "cross_work_item":
                    completed["work_item_id"] = "prototype:codex"
                if case == "reversed_order":
                    receipts = [submitted, completed]
                elif case == "blocked_after_completion":
                    blocked = self.deterministic_receipt(
                        task_id,
                        corrected_implementer,
                        "dispatch_blocked",
                        2,
                        suspension_epoch=1,
                    )
                    later_submitted = json.loads(json.dumps(submitted))
                    later_submitted["receipt_id"] = "receipt-3"
                    later_submitted["event_sequence"] = 3
                    receipts = [completed, blocked, later_submitted]
                else:
                    receipts = [completed, submitted]

                errors = dependency_order_errors(
                    dependencies,
                    receipts,
                    task_dir,
                    {"evidence": evidence_records},
                    manual_mode=False,
                    correction_cycle=correction_cycle,
                )

                self.assertEqual(len(errors), 1)
                self.assertIn("was not satisfied before receipt line", errors[0])

    def test_dispatch_uses_queue_adapter_without_herdr_command(self) -> None:
        observed_at = datetime.now().astimezone().isoformat()
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-05T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests", "writes verification evidence"],
                    "must_not_do": ["must not bypass approval gates"],
                    "model_identity": {
                        "agent_surface": "codex_cli",
                        "provider": "test-provider",
                        "declared_model": {
                            "model_id": "test-model",
                            "source": "test fixture",
                            "timestamp": "2026-07-10T00:00:00Z",
                            "confidence": "high",
                            "freshness": "current",
                        },
                        "observed_model": {
                            "model_id": "test-model",
                            "source": "test fixture",
                            "timestamp": "2026-07-10T00:00:00Z",
                            "confidence": "high",
                            "freshness": "current",
                        },
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch(
                        "valp_cli.workflow.collect_runtime_preflight",
                        return_value={
                            "generated_at": observed_at,
                            "runtime": "test queue",
                            "adapter_class": "daemon_queue",
                            "status": "pass",
                            "agents": {
                                "codex": {
                                    "status": "pass",
                                    "model_probe": {
                                        "schema_version": "valp-model-probe.v1",
                                        "status": "observed",
                                        "source": "test queue metadata",
                                        "observed_at": observed_at,
                                        "ttl_seconds": 86400,
                                        "model": {
                                            "model_id": "test-model",
                                            "provider": "test-provider",
                                            "reasoning_mode": "unknown",
                                            "confidence": "high",
                                        },
                                        "session_identity": {
                                            "status": "known",
                                            "token": "sha256:test-session",
                                            "source": "test queue generation",
                                            "generation": "1",
                                        },
                                    },
                                }
                            },
                        },
                    ):
                        task_dir = self.publish_routed_task(
                            root,
                            "TASK-QUEUE",
                            "Fix a bug and run tests",
                            runtime="queue",
                        )
            commands = dispatch_task(root, "TASK-QUEUE")

            routing = read_json(task_dir / "routing.json")
            self.assertEqual(routing["runtime_adapter"]["class"], "daemon_queue")
            self.assertTrue(commands)
            self.assertTrue(commands[0].startswith("VALP Queue Mode:"))
            self.assertNotIn("herdr-loop", commands[0])
            preflight = routing["runtime_adapter"]["preflight"]
            self.assertEqual(preflight["adapter_class"], "daemon_queue")
            self.assertNotIn("terminal_size_status", json.dumps(preflight))
            self.assertFalse((task_dir / "runtime-preflight.json").exists())

    def test_submitted_phase_writes_wait_policy_for_exact_work_items(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-15T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests", "writes verification evidence"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-PHASE-WAIT-POLICY",
                        "Fix a bug and run tests",
                        runtime="queue",
                    )

            dispatch_task(
                root,
                "TASK-PHASE-WAIT-POLICY",
                role="coordinator",
                submit=True,
            )

            policy = read_json(task_dir / "wait-policy.json")
            self.assertEqual(policy["schema_version"], "valp-wait-policy.v1")
            self.assertEqual(policy["task_id"], "TASK-PHASE-WAIT-POLICY")
            self.assertEqual(
                [item["work_item_id"] for item in policy["required_work_items"]],
                ["coordinator:codex"],
            )
            self.assertEqual(policy["dependency_ref"], "submission-dependencies.json")

    def test_zero_evidence_wait_generates_submission_only_herdr_command(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-15T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests", "reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {
                "submission_transport": {
                    "status": "pass",
                    "mode": "pane_send_text_enter",
                }
            },
            "agents": {"codex": {"status": "pass", "pane_id": "pane-1"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                        task_dir = self.publish_routed_task(
                            root,
                            "TASK-HERDR-SUBMISSION-ONLY",
                            "Fix a bug and run tests",
                            runtime="herdr",
                        )
                        with patch(
                            "valp_cli.workflow.ensure_herdr_agent_sessions",
                            return_value=self.owned_session_projection(
                                "TASK-HERDR-SUBMISSION-ONLY"
                            ),
                        ):
                            with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                                with patch(
                                    "valp_cli.workflow.submit_herdr_dispatch",
                                    return_value=herdr_invocation_proof(),
                                ):
                                    commands = dispatch_task(
                                        root,
                                        "TASK-HERDR-SUBMISSION-ONLY",
                                        role="coordinator",
                                        wait_seconds=0,
                                        submit=True,
                                    )

            self.assertEqual(len(commands), 1)
            self.assertIn("mode=pane_send_text_enter", commands[0])
            self.assertNotIn("herdr-loop", commands[0])
            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [receipt["event"] for receipt in receipts if receipt.get("schema_version")],
                ["dispatch_submitted"],
            )
            policy = read_json(task_dir / "wait-policy.json")
            self.assertEqual(
                policy["required_work_items"][0]["expected_refs"],
                ["agents/codex/self-review.md"],
            )

    def test_dispatch_uses_evidence_wait_for_codex_bootstrap_timeout(self) -> None:
        task_id = "TASK-HERDR-BOOTSTRAP-WAIT"
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-08-08T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests", "reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        routed_preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {"submission_transport": {"status": "pass", "mode": "agent_prompt"}},
            "agents": {"codex": {"status": "pass", "pane_id": "pane-owned"}},
        }
        bootstrap_preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "fail",
            "checks": {"submission_transport": {"status": "pass", "mode": "agent_prompt"}},
            "agents": {
                "codex": {
                    "status": "fail",
                    "pane_id": "pane-owned",
                    "readiness": {
                        "ready": False,
                        "reason_code": "session_identity_unknown",
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch(
                        "valp_cli.workflow.collect_runtime_preflight",
                        return_value=routed_preflight,
                    ):
                        task_dir = self.publish_routed_task(
                            root,
                            task_id,
                            "Coordinate and verify an agent runtime change",
                            runtime="herdr",
                        )

            provisioned = self.owned_session_projection(
                task_id,
                "pane-owned",
                lifecycle="provisioned",
            )
            verified = self.owned_session_projection(
                task_id,
                "pane-owned",
                lifecycle="bootstrap_ready",
            )

            def submit_with_evidence(*_args: object, **_kwargs: object) -> dict[str, object]:
                evidence_path = task_dir / "agents/codex/self-review.md"
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text("completed\n", encoding="utf-8")
                return herdr_invocation_proof(pane_id="pane-owned")

            with patch(
                "valp_cli.workflow.ensure_herdr_agent_sessions",
                return_value=provisioned,
            ):
                with patch(
                    "valp_cli.workflow.collect_runtime_preflight",
                    side_effect=[bootstrap_preflight, routed_preflight],
                ):
                    with patch(
                        "valp_cli.workflow.bootstrap_task_owned_herdr_session",
                        return_value=verified,
                    ) as bootstrap:
                        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                            with patch(
                                "valp_cli.workflow.submit_herdr_dispatch",
                                side_effect=submit_with_evidence,
                            ):
                                dispatch_task(
                                    root,
                                    task_id,
                                    role="coordinator",
                                    wait_seconds=240,
                                    submit=True,
                                )

            self.assertEqual(bootstrap.call_args.kwargs["timeout_seconds"], 240)

    def test_dispatch_enforces_minimum_bootstrap_timeout_for_startup_hooks(self) -> None:
        task_id = "TASK-HERDR-BOOTSTRAP-MINIMUM-WAIT"
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-08-12T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests", "reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        routed_preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {"submission_transport": {"status": "pass", "mode": "agent_prompt"}},
            "agents": {"codex": {"status": "pass", "pane_id": "pane-owned"}},
        }
        bootstrap_preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "fail",
            "checks": {"submission_transport": {"status": "pass", "mode": "agent_prompt"}},
            "agents": {
                "codex": {
                    "status": "fail",
                    "pane_id": "pane-owned",
                    "readiness": {"ready": False, "reason_code": "session_identity_unknown"},
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=routed_preflight):
                        task_dir = self.publish_routed_task(
                            root,
                            task_id,
                            "Coordinate and verify an agent runtime change",
                            runtime="herdr",
                        )
            provisioned = self.owned_session_projection(task_id, "pane-owned", lifecycle="provisioned")
            verified = self.owned_session_projection(task_id, "pane-owned", lifecycle="bootstrap_ready")

            def submit_with_evidence(*_args: object, **_kwargs: object) -> dict[str, object]:
                evidence_path = task_dir / "agents/codex/self-review.md"
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text("completed\n", encoding="utf-8")
                return herdr_invocation_proof(pane_id="pane-owned")

            with patch("valp_cli.workflow.ensure_herdr_agent_sessions", return_value=provisioned):
                with patch(
                    "valp_cli.workflow.collect_runtime_preflight",
                    side_effect=[bootstrap_preflight, routed_preflight],
                ):
                    with patch(
                        "valp_cli.workflow.bootstrap_task_owned_herdr_session",
                        return_value=verified,
                    ) as bootstrap:
                        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                            with patch(
                                "valp_cli.workflow.submit_herdr_dispatch",
                                side_effect=submit_with_evidence,
                            ):
                                dispatch_task(
                                    root,
                                    task_id,
                                    role="coordinator",
                                    wait_seconds=20,
                                    submit=True,
                                )

            self.assertEqual(bootstrap.call_args.kwargs["timeout_seconds"], 60)

    def test_dispatch_uses_digest_bound_task_runtime_launch_capabilities(self) -> None:
        task_id = "TASK-HERDR-TASK-RUNTIME-CAPABILITIES"
        global_capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-27T00:00:00Z",
            "source": "global test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests", "reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                    "runtime": {
                        "launch_argv": ["/test/global-launcher"],
                        "version_command": ["/test/global-launcher", "--version"],
                    },
                }
            },
        }
        task_capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "source": "task-owned test fixture",
            "agents": {
                "codex": {
                    "runtime": {
                        "launch_argv": ["/test/task-launcher", "--config", "/test/task.json"],
                        "version_command": ["/test/task-cli", "--version"],
                    }
                }
            },
        }
        preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {
                "submission_transport": {
                    "status": "pass",
                    "mode": "pane_send_text_enter",
                }
            },
            "agents": {"codex": {"status": "pass", "pane_id": "pane-owned"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=global_capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                        task_dir = self.publish_routed_task(
                            root,
                            task_id,
                            "Fix a runtime bug and run tests",
                            runtime="herdr",
                        )

            capability_path = task_dir / "runtime/task-capabilities.json"
            capability_path.parent.mkdir(parents=True, exist_ok=True)
            capability_bytes = (json.dumps(task_capabilities, indent=2) + "\n").encode("utf-8")
            capability_path.write_bytes(capability_bytes)
            marker = {
                "status": "recorded",
                "ref": "runtime/task-capabilities.json",
                "digest": "sha256:" + hashlib.sha256(capability_bytes).hexdigest(),
            }
            for name in ("routing.json", "state.json"):
                path = task_dir / name
                record = read_json(path)
                record["task_runtime_capabilities"] = marker
                path.write_text(json.dumps(record), encoding="utf-8")

            with patch("valp_cli.workflow.load_local_capabilities", return_value=global_capabilities):
                with patch("valp_cli.workflow.ensure_herdr_agent_sessions") as ensure_sessions:
                    ensure_sessions.return_value = self.owned_session_projection(
                        task_id,
                        "pane-owned",
                    )
                    with patch(
                        "valp_cli.workflow.collect_runtime_preflight",
                        return_value=preflight,
                    ) as collect_preflight:
                        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                            with patch(
                                "valp_cli.workflow.submit_herdr_dispatch",
                                return_value=herdr_invocation_proof(pane_id="pane-owned"),
                            ):
                                dispatch_task(
                                    root,
                                    task_id,
                                    role="coordinator",
                                    wait_seconds=0,
                                    submit=True,
                                )

            resolved_capabilities = ensure_sessions.call_args.args[4]
            self.assertEqual(
                resolved_capabilities["agents"]["codex"]["runtime"]["launch_argv"],
                task_capabilities["agents"]["codex"]["runtime"]["launch_argv"],
            )
            self.assertEqual(
                collect_preflight.call_args.kwargs["launch_argv_by_agent"]["codex"],
                task_capabilities["agents"]["codex"]["runtime"]["launch_argv"],
            )
            self.assertEqual(
                collect_preflight.call_args.kwargs["version_command_by_agent"]["codex"],
                task_capabilities["agents"]["codex"]["runtime"]["version_command"],
            )

    def test_task_runtime_capability_marker_and_digest_fail_closed(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "agents": {"codex": {"runtime": {"launch_argv": ["/test/global"]}}},
        }
        task_capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "agents": {"codex": {"runtime": {"launch_argv": ["/test/task"]}}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / ".herdr-loop/tasks/TASK-RUNTIME-MARKER"
            capability_path = directory / "runtime/task-capabilities.json"
            capability_path.parent.mkdir(parents=True)
            raw = (json.dumps(task_capabilities) + "\n").encode("utf-8")
            capability_path.write_bytes(raw)
            marker = {
                "status": "recorded",
                "ref": "runtime/task-capabilities.json",
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }

            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with self.assertRaisesRegex(SystemExit, "marker is missing or inconsistent"):
                    workflow_module.load_dispatch_capabilities(
                        root,
                        directory,
                        {"task_runtime_capabilities": marker},
                        {},
                    )

                bad_marker = {**marker, "digest": "sha256:" + ("0" * 64)}
                with self.assertRaisesRegex(SystemExit, "digest mismatch"):
                    workflow_module.load_dispatch_capabilities(
                        root,
                        directory,
                        {"task_runtime_capabilities": bad_marker},
                        {"task_runtime_capabilities": bad_marker},
                    )

    def test_dispatch_retries_transient_runtime_failure_after_fresh_preflight(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-22T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates, edits files, runs tests, and reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {
                "submission_transport": {
                    "status": "pass",
                    "mode": "pane_send_text_enter",
                }
            },
            "agents": {"codex": {"status": "pass", "pane_id": "pane-1"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                        task_dir = self.publish_routed_task(
                            root,
                            "TASK-HERDR-TRANSIENT-RETRY",
                            "Coordinate and verify an agent runtime change",
                            runtime="herdr",
                        )

            budget_path = task_dir / "iteration-budget.json"
            budget = read_json(budget_path)
            budget["status"] = "blocked"
            budget["stop_reason"] = "runtime dispatch failure"
            budget_path.write_text(json.dumps(budget), encoding="utf-8")

            with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight) as collect_preflight:
                with patch(
                    "valp_cli.workflow.ensure_herdr_agent_sessions",
                    return_value=self.owned_session_projection("TASK-HERDR-TRANSIENT-RETRY"),
                ):
                    with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                        with patch(
                            "valp_cli.workflow.submit_herdr_dispatch",
                            return_value=herdr_invocation_proof(),
                        ):
                            commands = dispatch_task(
                                root,
                                "TASK-HERDR-TRANSIENT-RETRY",
                                role="coordinator",
                                wait_seconds=0,
                                submit=True,
                            )

            collect_preflight.assert_called_once()
            self.assertEqual(len(commands), 1)
            recovered = read_json(budget_path)
            self.assertEqual(recovered["status"], "active")
            self.assertIsNone(recovered["stop_reason"])
            timeline = [
                json.loads(line)
                for line in (task_dir / "timeline.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(timeline[-1]["event"], "runtime_dispatch_retry_started")
            self.assertEqual(timeline[-1]["work_item_ids"], ["coordinator:codex"])

    def test_done_session_reprovision_precondition_does_not_consume_runtime_retry(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-08-07T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates, edits files, runs tests, and reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {"submission_transport": {"status": "pass", "mode": "agent_prompt"}},
            "agents": {"codex": {"status": "pass", "pane_id": "pane-1"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                        task_dir = self.publish_routed_task(
                            root,
                            "TASK-HERDR-DONE-REPROVISION-PRECONDITION",
                            "Coordinate and verify an agent runtime change",
                            runtime="herdr",
                        )

            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch(
                    "valp_cli.workflow.ensure_herdr_agent_sessions",
                    side_effect=HerdrSubmissionError(
                        "HERDR task-owned done session requires an explicit fenced reprovision"
                    ),
                ):
                    with patch("valp_cli.workflow.collect_runtime_preflight") as collect_preflight:
                        with self.assertRaisesRegex(SystemExit, "explicit fenced reprovision"):
                            dispatch_task(
                                root,
                                "TASK-HERDR-DONE-REPROVISION-PRECONDITION",
                                role="coordinator",
                                wait_seconds=0,
                                submit=True,
                            )

            collect_preflight.assert_not_called()
            budget = read_json(task_dir / "iteration-budget.json")
            self.assertEqual(budget["status"], "active")
            self.assertIsNone(budget["stop_reason"])
            self.assertFalse((task_dir / "agent-session-block.json").exists())
            timeline = [
                json.loads(line)
                for line in (task_dir / "timeline.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(timeline[-1]["event"], "agent_session_reprovision_required")

    def test_dispatch_dry_run_does_not_preflight_or_mutate_runtime_failure_state(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-08-07T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates, edits files, runs tests, and reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        passing_preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {"submission_transport": {"status": "pass", "mode": "agent_prompt"}},
            "agents": {"codex": {"status": "pass", "pane_id": "pane-1"}},
        }
        failing_preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "fail",
            "checks": {},
            "agents": {"codex": {"status": "fail", "pane_id": None}},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=passing_preflight):
                        task_dir = self.publish_routed_task(
                            root,
                            "TASK-HERDR-DRY-RUN-NO-MUTATION",
                            "Coordinate and verify an agent runtime change",
                            runtime="herdr",
                        )

            tracked = ["iteration-budget.json", "timeline.jsonl", "runtime-preflight.json"]
            before = {
                name: (task_dir / name).read_bytes() if (task_dir / name).exists() else None
                for name in tracked
            }

            with patch(
                "valp_cli.workflow.collect_runtime_preflight",
                return_value=failing_preflight,
            ) as collect_preflight:
                commands = dispatch_task(
                    root,
                    "TASK-HERDR-DRY-RUN-NO-MUTATION",
                    role="coordinator",
                    submit=False,
                )

            collect_preflight.assert_not_called()
            self.assertEqual(len(commands), 1)
            after = {
                name: (task_dir / name).read_bytes() if (task_dir / name).exists() else None
                for name in tracked
            }
            self.assertEqual(after, before)

    def test_targeted_session_bindings_excludes_completed_sibling_sessions(self) -> None:
        projection = {
            "bindings": {
                "codex": {"runtime_identity": {"pane_id": "closed-codex"}},
                "claude": {"runtime_identity": {"pane_id": "live-claude"}},
            }
        }

        self.assertEqual(
            workflow_module.targeted_session_bindings(projection, ["claude"]),
            {"claude": {"runtime_identity": {"pane_id": "live-claude"}}},
        )

    def test_dispatch_stops_after_retried_runtime_failure(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-22T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates, edits files, runs tests, and reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {
                "submission_transport": {
                    "status": "pass",
                    "mode": "pane_send_text_enter",
                }
            },
            "agents": {"codex": {"status": "pass", "pane_id": "pane-1"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                        task_dir = self.publish_routed_task(
                            root,
                            "TASK-HERDR-RETRY-EXHAUSTED",
                            "Coordinate and verify an agent runtime change",
                            runtime="herdr",
                        )

            budget_path = task_dir / "iteration-budget.json"
            budget = read_json(budget_path)
            budget["status"] = "blocked"
            budget["stop_reason"] = "runtime dispatch failure"
            budget_path.write_text(json.dumps(budget), encoding="utf-8")

            with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                with patch(
                    "valp_cli.workflow.ensure_herdr_agent_sessions",
                    return_value=self.owned_session_projection("TASK-HERDR-RETRY-EXHAUSTED"),
                ):
                    with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                        with patch(
                            "valp_cli.workflow.submit_herdr_dispatch",
                            side_effect=HerdrSubmissionError("working proof timed out"),
                        ):
                            with self.assertRaisesRegex(SystemExit, "working proof timed out"):
                                dispatch_task(
                                    root,
                                    "TASK-HERDR-RETRY-EXHAUSTED",
                                    role="coordinator",
                                    wait_seconds=0,
                                    submit=True,
                                )

            exhausted = read_json(budget_path)
            self.assertEqual(exhausted["status"], "blocked")
            self.assertEqual(exhausted["stop_reason"], "runtime dispatch retry exhausted")

            with patch("valp_cli.workflow.collect_runtime_preflight") as collect_preflight:
                with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                    with self.assertRaisesRegex(SystemExit, "runtime dispatch retry exhausted"):
                        dispatch_task(
                            root,
                            "TASK-HERDR-RETRY-EXHAUSTED",
                            role="coordinator",
                            wait_seconds=0,
                            submit=True,
                        )
            collect_preflight.assert_not_called()
            submit_dispatch.assert_not_called()

    def test_dispatch_reconciles_late_model_identity_on_same_owned_work_item(self) -> None:
        task_id = "TASK-HERDR-LATE-MODEL-IDENTITY"
        route_preflight = self.model_aware_test_preflight({
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {"submission_transport": {"status": "pass"}},
            "agents": {"codex": {"status": "pass", "pane_id": "pane-route"}},
        })
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-27T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests", "reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch(
                        "valp_cli.workflow.collect_runtime_preflight",
                        return_value=route_preflight,
                    ):
                        task_dir = self.publish_routed_task(
                            root,
                            task_id,
                            "Fix a bug and run tests",
                            runtime="herdr",
                        )

            projection = self.owned_session_projection(
                task_id,
                pane_id="pane-owned",
                lifecycle="reused",
            )
            binding = projection["bindings"]["codex"]
            binding["focused_at_provisioning"] = False
            binding["runtime_identity"]["token"] = "sha256:" + ("a" * 64)
            (task_dir / "agent-sessions.json").write_text(
                json.dumps(projection),
                encoding="utf-8",
            )
            (task_dir / "agent-session-receipts.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "valp-agent-session-receipt.v1",
                        "adapter": "herdr",
                        "task_id": task_id,
                        "event_sequence": 1,
                        "ts": "2026-07-27T00:00:00Z",
                        "agent": "codex",
                        "event": "agent_session_provisioned",
                        "binding_ref": "agent-sessions.json",
                        "generation": 1,
                        "identity_token": binding["runtime_identity"]["token"],
                        "ownership": binding["ownership"],
                        "context": binding["context"],
                        "launch": binding["launch"],
                        "focused_at_provisioning": False,
                        "runtime_scope": binding["runtime_scope"],
                        "runtime_identity": binding["runtime_identity"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            dependencies = read_json(task_dir / "submission-dependencies.json")
            coordinator_item = next(
                item
                for item in dependencies["work_items"]
                if item["role"] == "coordinator"
            )
            for ref in coordinator_item["expected_refs"]:
                evidence_path = task_dir / ref
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text("coordinator complete\n", encoding="utf-8")
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self.deterministic_receipt(
                            task_id,
                            coordinator_item,
                            "dispatch_submitted",
                            1,
                        )
                    )
                    + "\n"
                )
                handle.write(
                    json.dumps(
                        self.deterministic_receipt(
                            task_id,
                            coordinator_item,
                            "dispatch_completed",
                            2,
                            suspension_epoch=1,
                        )
                    )
                    + "\n"
                )
            budget_path = task_dir / "iteration-budget.json"
            budget = read_json(budget_path)
            budget["status"] = "blocked"
            budget["stop_reason"] = "runtime dispatch retry exhausted"
            budget["usage"]["reroutes"] = 1
            budget_path.write_text(json.dumps(budget), encoding="utf-8")
            (task_dir / "model-identity-dispatch-block.json").write_text(
                json.dumps(
                    {
                        "schema_version": "valp-model-identity-dispatch-block.v1",
                        "task_id": task_id,
                        "status": "blocked",
                        "reason": "owned_session_model_readiness_timeout",
                        "errors": ["implementer:codex active model identity is not eligible"],
                        "runtime_preflight_ref": "runtime-preflight.json",
                        "recorded_at": "2026-07-27T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            observed_preflight = self.model_aware_test_preflight(
                {
                    "runtime": "HERDR",
                    "adapter_class": "pane_controller",
                    "status": "pass",
                    "checks": {
                        "submission_transport": {
                            "status": "pass",
                            "mode": "pane_send_text_enter",
                        }
                    },
                    "agents": {
                        "codex": {
                            "status": "pass",
                            "pane_id": "pane-owned",
                            "session_binding": {
                                "status": "bound",
                                "ref": "agent-sessions.json",
                                "generation": 1,
                                "identity_token": binding["runtime_identity"]["token"],
                                "ownership": binding["ownership"],
                            },
                        }
                    },
                }
            )

            with patch("valp_cli.workflow.collect_runtime_preflight", return_value=observed_preflight):
                with patch(
                    "valp_cli.workflow.ensure_herdr_agent_sessions",
                    return_value=projection,
                ) as ensure_sessions:
                    with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                        with patch(
                            "valp_cli.workflow.submit_herdr_dispatch",
                            return_value=herdr_invocation_proof(pane_id="pane-owned"),
                        ) as submit_dispatch:
                            commands = dispatch_task(
                                root,
                                task_id,
                                role="implementer",
                                wait_seconds=0,
                                submit=True,
                            )

            self.assertEqual(len(commands), 1)
            ensure_sessions.assert_called_once()
            self.assertEqual(
                submit_dispatch.call_args.kwargs["session_binding"]["runtime_identity"]["pane_id"],
                "pane-owned",
            )
            recovered = read_json(budget_path)
            self.assertEqual(recovered["status"], "active")
            self.assertIsNone(recovered["stop_reason"])
            self.assertEqual(recovered["usage"]["reroutes"], 1)
            submitted = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
                if json.loads(line).get("event") == "dispatch_submitted"
                and json.loads(line).get("work_item_id") == "implementer:codex"
            ]
            self.assertEqual(len(submitted), 1)
            self.assertEqual(submitted[0]["work_item_id"], "implementer:codex")

    def test_dispatch_does_not_reopen_late_model_recovery_after_submit_failure(self) -> None:
        task_id = "TASK-HERDR-LATE-MODEL-SUBMIT-FAILED"
        route_preflight = self.model_aware_test_preflight({
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {"submission_transport": {"status": "pass"}},
            "agents": {"codex": {"status": "pass", "pane_id": "pane-route"}},
        })
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-27T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests", "reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch(
                        "valp_cli.workflow.collect_runtime_preflight",
                        return_value=route_preflight,
                    ):
                        task_dir = self.publish_routed_task(
                            root,
                            task_id,
                            "Fix a bug and run tests",
                            runtime="herdr",
                        )

            projection = self.owned_session_projection(
                task_id,
                pane_id="pane-owned",
                lifecycle="reused",
            )
            binding = projection["bindings"]["codex"]
            binding["focused_at_provisioning"] = False
            binding["runtime_identity"]["token"] = "sha256:" + ("a" * 64)
            (task_dir / "agent-sessions.json").write_text(
                json.dumps(projection),
                encoding="utf-8",
            )
            (task_dir / "agent-session-receipts.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": "valp-agent-session-receipt.v1",
                        "adapter": "herdr",
                        "task_id": task_id,
                        "event_sequence": 1,
                        "ts": "2026-07-27T00:00:00Z",
                        "agent": "codex",
                        "event": "agent_session_provisioned",
                        "binding_ref": "agent-sessions.json",
                        "generation": 1,
                        "identity_token": binding["runtime_identity"]["token"],
                        "ownership": binding["ownership"],
                        "context": binding["context"],
                        "launch": binding["launch"],
                        "focused_at_provisioning": False,
                        "runtime_scope": binding["runtime_scope"],
                        "runtime_identity": binding["runtime_identity"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            budget_path = task_dir / "iteration-budget.json"
            budget = read_json(budget_path)
            budget["status"] = "blocked"
            budget["stop_reason"] = "runtime dispatch retry exhausted"
            budget_path.write_text(json.dumps(budget), encoding="utf-8")
            (task_dir / "model-identity-dispatch-block.json").write_text(
                json.dumps(
                    {
                        "schema_version": "valp-model-identity-dispatch-block.v1",
                        "task_id": task_id,
                        "status": "blocked",
                        "reason": "owned_session_model_readiness_timeout",
                        "errors": ["coordinator:codex active model identity is not eligible"],
                        "runtime_preflight_ref": "runtime-preflight.json",
                        "recorded_at": "2026-07-27T00:01:00Z",
                    }
                ),
                encoding="utf-8",
            )
            with (task_dir / "timeline.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "ts": "2026-07-27T00:02:00Z",
                            "event": "dispatch_submit_failed",
                            "summary": "working-state proof timed out",
                            "agent": "codex",
                            "role": "coordinator",
                            "work_item_id": "coordinator:codex",
                            "attempt": "retry",
                        }
                    )
                    + "\n"
                )

            with patch("valp_cli.workflow.ensure_herdr_agent_sessions") as ensure_sessions:
                with patch("valp_cli.workflow.collect_runtime_preflight") as collect_preflight:
                    with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                        with self.assertRaisesRegex(SystemExit, "runtime dispatch retry exhausted"):
                            dispatch_task(
                                root,
                                task_id,
                                role="coordinator",
                                wait_seconds=0,
                                submit=True,
                            )

            ensure_sessions.assert_not_called()
            collect_preflight.assert_not_called()
            submit_dispatch.assert_not_called()

    def test_dispatch_launch_replacement_requires_explicit_single_submit(self) -> None:
        with self.assertRaisesRegex(
            SystemExit,
            "--replace-owned-session-launch requires --submit",
        ):
            dispatch_task(
                Path("/unused"),
                "TASK-REPLACE-LAUNCH",
                agent="codex",
                role="implementer",
                replace_owned_session_launch=True,
            )

        with self.assertRaisesRegex(
            SystemExit,
            "--replace-owned-session-launch requires --submit",
        ):
            dispatch_task(
                Path("/unused"),
                "TASK-REPLACE-LAUNCH",
                submit=True,
                replace_owned_session_launch=True,
            )

    def test_dispatch_rejects_negative_evidence_wait(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-15T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests", "reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {},
            "agents": {"codex": {"status": "pass"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                        self.publish_routed_task(
                            root,
                            "TASK-HERDR-NEGATIVE-WAIT",
                            "Fix a bug and run tests",
                            runtime="herdr",
                        )
                        with patch(
                            "valp_cli.workflow.subprocess.run",
                            return_value=subprocess.CompletedProcess([], 0),
                        ):
                            with self.assertRaisesRegex(SystemExit, "finite non-negative"):
                                dispatch_task(
                                    root,
                                    "TASK-HERDR-NEGATIVE-WAIT",
                                    role="coordinator",
                                    wait_seconds=-1,
                                    submit=True,
                                )

    def test_default_frontier_retries_submission_without_concrete_runtime_proof(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-15T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests", "reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-UNPROVEN-FRONTIER"
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(
                        root,
                        task_id,
                        "Fix a bug and review it",
                        runtime="queue",
                    )
            dependencies = read_json(task_dir / "submission-dependencies.json")
            coordinator = next(
                item for item in dependencies["work_items"] if item["role"] == "coordinator"
            )
            unproven = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            unproven["proof"] = {"note": "accepted"}
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(unproven) + "\n")

            commands = dispatch_task(root, task_id, submit=True, runtime="queue")

            self.assertEqual(len(commands), 1)
            self.assertIn("phase=coordinator", commands[0])
            self.assertTrue((task_dir / "queue" / "codex-coordinator.json").is_file())

    def test_public_dispatch_recovers_one_incomplete_identity_bound_submission(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-22T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests", "reviews"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {
                "submission_transport": {
                    "status": "pass",
                    "mode": "pane_send_text_enter",
                }
            },
            "agents": {"codex": {"status": "pass", "pane_id": "pane-fresh"}},
        }
        proofs = [
            herdr_invocation_proof(pane_id="pane-original"),
            herdr_invocation_proof(pane_id="pane-fresh"),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-SUBMISSION-RECOVERY"
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                        task_dir = self.publish_routed_task(
                            root,
                            task_id,
                            "Coordinate and review a bounded runtime repair.",
                            profile="generic-analysis",
                            runtime="herdr",
                        )
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                    with patch(
                        "valp_cli.workflow.ensure_herdr_agent_sessions",
                        return_value=self.owned_session_projection(task_id, "pane-fresh"),
                    ):
                        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                            with patch(
                                "valp_cli.workflow.submit_herdr_dispatch",
                                side_effect=proofs,
                            ) as submit_dispatch:
                                self.assertEqual(
                                    main([
                                        "dispatch",
                                        task_id,
                                        "--workspace",
                                        str(root),
                                        "--runtime",
                                        "herdr",
                                        "--wait-seconds",
                                        "0",
                                        "--submit",
                                    ]),
                                    0,
                                )
                                receipt_path = task_dir / "dispatch-receipts.jsonl"
                                original_lines = receipt_path.read_text(encoding="utf-8").splitlines()
                                with self.assertRaisesRegex(SystemExit, "no ready phase"):
                                    main([
                                        "dispatch",
                                        task_id,
                                        "--workspace",
                                        str(root),
                                        "--runtime",
                                        "herdr",
                                        "--wait-seconds",
                                        "0",
                                        "--submit",
                                    ])
                                self.assertEqual(
                                    main([
                                        "dispatch",
                                        task_id,
                                        "--workspace",
                                        str(root),
                                        "--agent",
                                        "codex",
                                        "--role",
                                        "coordinator",
                                        "--runtime",
                                        "herdr",
                                        "--wait-seconds",
                                        "0",
                                        "--recover-incomplete",
                                        "--retry-generation",
                                        "1",
                                        "--submit",
                                    ]),
                                    0,
                                )

            receipt_lines = receipt_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(receipt_lines[: len(original_lines)], original_lines)
            receipts = [json.loads(line) for line in receipt_lines if '"schema_version"' in line]
            self.assertEqual([receipt["event"] for receipt in receipts], ["dispatch_submitted"] * 2)
            original, recovery = receipts
            for field in [
                "task_id",
                "agent",
                "role",
                "work_item_id",
                "dispatch_id",
                "dispatch_generation",
                "dispatch_ref",
                "expected_refs",
            ]:
                self.assertEqual(recovery[field], original[field])
            self.assertEqual(recovery["retry_generation"], 1)
            self.assertEqual(
                recovery["proof"]["recovery"]["originating_submission_receipt_id"],
                original["receipt_id"],
            )
            self.assertEqual(
                recovery["proof"]["recovery"]["control_contract_digest"],
                read_json(task_dir / "routing.json")["control_contract"]["digest"],
            )
            self.assertEqual(submit_dispatch.call_count, 2)

    def test_public_incomplete_recovery_reconciles_late_valid_evidence_without_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-LATE-EVIDENCE"
            task_dir, capabilities, _preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["proof"] = {
                "runtime": "HERDR",
                "transport_mode": "pane_send_text_enter",
                "pane_id": "pane-original",
                "submission_id": "original",
            }
            receipt_path = task_dir / "dispatch-receipts.jsonl"
            with receipt_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")
            original_lines = receipt_path.read_text(encoding="utf-8").splitlines()
            for ref in coordinator["expected_refs"]:
                evidence_path = task_dir / str(ref)
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text("verified late evidence\n", encoding="utf-8")

            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight") as collect:
                    with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                        self.assertEqual(main(self.recover_incomplete_cli_args(root, task_id)), 0)

            collect.assert_not_called()
            submit_dispatch.assert_not_called()
            receipt_lines = receipt_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(receipt_lines[: len(original_lines)], original_lines)
            receipts = [json.loads(line) for line in receipt_lines if '"schema_version"' in line]
            self.assertEqual([receipt["event"] for receipt in receipts], [
                "dispatch_submitted",
                "dispatch_completed",
            ])
            completion = receipts[-1]
            self.assertEqual(completion["proof"]["submission_receipt_id"], submitted["receipt_id"])
            self.assertEqual(completion["expected_refs"], coordinator["expected_refs"])
            self.assertNotIn("retry_generation", completion)
            completed_lines = receipt_path.read_text(encoding="utf-8").splitlines()
            with patch("valp_cli.workflow.collect_runtime_preflight") as replay_preflight:
                with patch("valp_cli.workflow.submit_herdr_dispatch") as replay_submit:
                    with self.assertRaisesRegex(SystemExit, "already terminal"):
                        main(self.recover_incomplete_cli_args(root, task_id))
            replay_preflight.assert_not_called()
            replay_submit.assert_not_called()
            self.assertEqual(receipt_path.read_text(encoding="utf-8").splitlines(), completed_lines)

    def test_public_incomplete_recovery_retries_unchanged_pre_submission_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-UNCHANGED-EVIDENCE"
            task_dir, capabilities, preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            baseline: dict[str, str] = {}
            for ref in coordinator["expected_refs"]:
                evidence_path = task_dir / str(ref)
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text("pre-existing evidence\n", encoding="utf-8")
                baseline[str(ref)] = "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest()

            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["proof"] = {
                "runtime": "HERDR",
                "transport_mode": "pane_send_text_enter",
                "pane_id": "pane-original",
                "submission_id": "original",
                "expected_evidence_baseline": baseline,
            }
            receipt_path = task_dir / "dispatch-receipts.jsonl"
            with receipt_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")
            original_lines = receipt_path.read_text(encoding="utf-8").splitlines()

            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                    with patch(
                        "valp_cli.workflow.ensure_herdr_agent_sessions",
                        return_value=self.owned_session_projection(task_id, "pane-fresh"),
                    ):
                        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                            with patch(
                                "valp_cli.workflow.submit_herdr_dispatch",
                                return_value=herdr_invocation_proof(pane_id="pane-fresh"),
                            ) as submit_dispatch:
                                self.assertEqual(main(self.recover_incomplete_cli_args(root, task_id)), 0)

            submit_dispatch.assert_called_once()
            receipt_lines = receipt_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(receipt_lines[: len(original_lines)], original_lines)
            receipts = [json.loads(line) for line in receipt_lines if '"schema_version"' in line]
            self.assertEqual([receipt["event"] for receipt in receipts], [
                "dispatch_submitted",
                "dispatch_submitted",
            ])
            self.assertEqual(receipts[-1]["retry_generation"], 1)

    def test_public_incomplete_recovery_reconciles_late_evidence_after_retry_without_transport(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-LATE-EVIDENCE-AFTER-RETRY"
            task_dir, capabilities, _preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["proof"] = {
                "runtime": "HERDR",
                "transport_mode": "pane_send_text_enter",
                "pane_id": "pane-original",
                "submission_id": "original",
            }
            retry = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                2,
            )
            retry["retry_generation"] = 1
            retry["proof"] = {
                "runtime": "HERDR",
                "transport_mode": "pane_send_text_enter",
                "pane_id": "pane-retry",
                "submission_id": "retry-1",
                "recovery": {
                    "kind": "incomplete_submission",
                    "retry_generation": 1,
                    "originating_submission_receipt_id": submitted["receipt_id"],
                    "control_contract_digest": read_json(task_dir / "routing.json")[
                        "control_contract"
                    ]["digest"],
                },
            }
            receipt_path = task_dir / "dispatch-receipts.jsonl"
            with receipt_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")
                handle.write(json.dumps(retry) + "\n")
            original_lines = receipt_path.read_text(encoding="utf-8").splitlines()
            for ref in coordinator["expected_refs"]:
                evidence_path = task_dir / str(ref)
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                evidence_path.write_text("verified after retry\n", encoding="utf-8")

            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight") as collect:
                    with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                        self.assertEqual(main(self.recover_incomplete_cli_args(root, task_id)), 0)

            collect.assert_not_called()
            submit_dispatch.assert_not_called()
            receipt_lines = receipt_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(receipt_lines[: len(original_lines)], original_lines)
            receipts = [json.loads(line) for line in receipt_lines if '"schema_version"' in line]
            self.assertEqual(
                [receipt["event"] for receipt in receipts],
                ["dispatch_submitted", "dispatch_submitted", "dispatch_completed"],
            )
            completion = receipts[-1]
            self.assertEqual(completion["proof"]["submission_receipt_id"], retry["receipt_id"])
            self.assertEqual(completion["retry_generation"], 1)
            self.assertEqual(completion["expected_refs"], coordinator["expected_refs"])

    def test_public_incomplete_recovery_rejects_missing_submission_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-MISSING-RECEIPT"
            _task_dir, capabilities, preflight, _coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight) as collect:
                    with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                        with self.assertRaisesRegex(SystemExit, "exactly one concrete"):
                            main(self.recover_incomplete_cli_args(root, task_id))
            collect.assert_not_called()
            submit_dispatch.assert_not_called()

    def test_public_incomplete_recovery_rejects_wrong_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.incomplete_recovery_fixture(root, "TASK-INCOMPLETE-ORIGINAL")
            with patch("valp_cli.workflow.collect_runtime_preflight") as collect:
                with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                    with self.assertRaisesRegex(SystemExit, "Missing routing.json"):
                        main(self.recover_incomplete_cli_args(root, "TASK-INCOMPLETE-WRONG"))
            collect.assert_not_called()
            submit_dispatch.assert_not_called()

    def test_public_incomplete_recovery_rejects_wrong_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-WRONG-ROLE"
            task_dir, capabilities, _preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["proof"] = {"runtime": "HERDR", "submission_id": "original"}
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight") as collect:
                    with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                        with self.assertRaisesRegex(SystemExit, "unmet prerequisites"):
                            main(self.recover_incomplete_cli_args(root, task_id, role="reviewer"))
            collect.assert_not_called()
            submit_dispatch.assert_not_called()

    def test_public_incomplete_recovery_rejects_changed_dispatch_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-WRONG-DISPATCH"
            task_dir, capabilities, _preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["dispatch_id"] = f"{task_id}:coordinator:changed"
            submitted["proof"] = {"runtime": "HERDR", "submission_id": "wrong-dispatch"}
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight") as collect:
                    with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                        with self.assertRaisesRegex(SystemExit, "exactly one concrete"):
                            main(self.recover_incomplete_cli_args(root, task_id))
            collect.assert_not_called()
            submit_dispatch.assert_not_called()

    def test_public_incomplete_recovery_rejects_already_completed_work_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-ALREADY-COMPLETED"
            task_dir, capabilities, _preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["proof"] = {"runtime": "HERDR", "submission_id": "original"}
            completed = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_completed",
                2,
                suspension_epoch=1,
            )
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")
                handle.write(json.dumps(completed) + "\n")
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight") as collect:
                    with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                        with self.assertRaisesRegex(SystemExit, "already terminal"):
                            main(self.recover_incomplete_cli_args(root, task_id))
            collect.assert_not_called()
            submit_dispatch.assert_not_called()

    def test_public_incomplete_recovery_rejects_duplicate_retry_without_resubmitting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-DUPLICATE-RETRY"
            task_dir, capabilities, preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["proof"] = {"runtime": "HERDR", "submission_id": "original"}
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")
            retry_proof = herdr_invocation_proof(pane_id="pane-fresh")
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                    with patch(
                        "valp_cli.workflow.ensure_herdr_agent_sessions",
                        return_value=self.owned_session_projection(task_id, "pane-fresh"),
                    ):
                        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                            with patch(
                                "valp_cli.workflow.submit_herdr_dispatch",
                                return_value=retry_proof,
                            ) as submit_dispatch:
                                self.assertEqual(main(self.recover_incomplete_cli_args(root, task_id)), 0)
                                with self.assertRaisesRegex(SystemExit, "already attempted"):
                                    main(self.recover_incomplete_cli_args(root, task_id))
            self.assertEqual(submit_dispatch.call_count, 1)
            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
                if '"schema_version"' in line
            ]
            self.assertEqual([receipt["retry_generation"] for receipt in receipts[1:]], [1])

    def test_failed_incomplete_recovery_transport_cannot_enter_another_retry_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-RECOVERY-TRANSPORT-FAILED"
            task_dir, capabilities, preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["proof"] = {"runtime": "HERDR", "submission_id": "original"}
            receipt_path = task_dir / "dispatch-receipts.jsonl"
            with receipt_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")

            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch(
                    "valp_cli.workflow.collect_runtime_preflight",
                    return_value=preflight,
                ) as collect_preflight:
                    with patch(
                        "valp_cli.workflow.ensure_herdr_agent_sessions",
                        return_value=self.owned_session_projection(task_id, "pane-fresh"),
                    ):
                        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                            with patch(
                                "valp_cli.workflow.submit_herdr_dispatch",
                                side_effect=HerdrSubmissionError("recovery transport failed"),
                            ) as submit_dispatch:
                                with self.assertRaisesRegex(SystemExit, "recovery transport failed"):
                                    main(self.recover_incomplete_cli_args(root, task_id))
                                with self.assertRaisesRegex(
                                    SystemExit,
                                    "incomplete submission recovery failed",
                                ):
                                    main(self.recover_incomplete_cli_args(root, task_id))
                                with self.assertRaisesRegex(SystemExit, "no ready phase"):
                                    main([
                                        "dispatch",
                                        task_id,
                                        "--workspace",
                                        str(root),
                                        "--runtime",
                                        "herdr",
                                        "--wait-seconds",
                                        "0",
                                        "--submit",
                                    ])

            collect_preflight.assert_called_once()
            submit_dispatch.assert_called_once()
            receipts = [
                json.loads(line)
                for line in receipt_path.read_text(encoding="utf-8").splitlines()
                if '"schema_version"' in line
            ]
            self.assertEqual(receipts, [submitted])
            budget = read_json(task_dir / "iteration-budget.json")
            self.assertEqual(budget["status"], "blocked")
            self.assertEqual(budget["stop_reason"], "incomplete submission recovery failed")

    def test_public_incomplete_recovery_rejects_control_contract_digest_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-CONTROL-DRIFT"
            task_dir, capabilities, _preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["proof"] = {"runtime": "HERDR", "submission_id": "original"}
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")
            contract_path = task_dir / "control-contract.json"
            contract = read_json(contract_path)
            contract["created_at"] = "2026-07-22T00:00:01Z"
            contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight") as collect:
                    with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                        with self.assertRaisesRegex(SystemExit, "control-contract identity mismatch"):
                            main(self.recover_incomplete_cli_args(root, task_id))
            collect.assert_not_called()
            submit_dispatch.assert_not_called()

    def test_public_incomplete_recovery_rejects_second_retry_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-RETRY-TWO"
            _task_dir, capabilities, _preflight, _coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            args = self.recover_incomplete_cli_args(root, task_id)
            args[args.index("1")] = "2"
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight") as collect:
                    with patch("valp_cli.workflow.submit_herdr_dispatch") as submit_dispatch:
                        with self.assertRaisesRegex(SystemExit, "only retry generation 1"):
                            main(args)
            collect.assert_not_called()
            submit_dispatch.assert_not_called()

    def test_public_dispatch_rejects_unmarked_resubmission_of_incomplete_work_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-UNMARKED-RESUBMIT"
            task_dir, capabilities, preflight, coordinator = self.incomplete_recovery_fixture(
                root,
                task_id,
            )
            submitted = self.deterministic_receipt(
                task_id,
                coordinator,
                "dispatch_submitted",
                1,
            )
            submitted["proof"] = {"runtime": "HERDR", "submission_id": "original"}
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(submitted) + "\n")
            args = [
                value
                for value in self.recover_incomplete_cli_args(root, task_id)
                if value not in {"--recover-incomplete", "--retry-generation", "1"}
            ]
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                    with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                        with patch(
                            "valp_cli.workflow.submit_herdr_dispatch",
                            return_value={
                                "runtime": "HERDR",
                                "pane_id": "pane-fresh",
                                "submission_id": "unmarked-resubmit",
                            },
                        ) as submit_dispatch:
                            with self.assertRaisesRegex(SystemExit, "requires --recover-incomplete"):
                                main(args)
            submit_dispatch.assert_not_called()

    def test_default_frontier_dispatches_all_ready_work_once(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-15T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "hermes": {
                    "active": True,
                    "role": ["coordination"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["state", "gates", "coordination"],
                },
                "codex": {
                    "active": True,
                    "role": ["implementation", "verification"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests"],
                },
                "claude": {
                    "active": True,
                    "role": ["reviewer"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["read-only review", "risk review"],
                },
                "agy": {
                    "active": True,
                    "role": ["prototype"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["isolated prototype"],
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-MULTI-READY-FRONTIER"
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(
                        root,
                        task_id,
                        "Fix agent runtime code, prototype an alternative, and review both.",
                        runtime="queue",
                        include_agents=["agy"],
                    )

            first = dispatch_task(root, task_id, submit=True, runtime="queue")
            self.assertEqual(len(first), 1)
            self.assertIn("phase=coordinator", first[0])
            with self.assertRaisesRegex(SystemExit, "no ready phase"):
                dispatch_task(root, task_id, submit=True, runtime="queue")

            dependencies = read_json(task_dir / "submission-dependencies.json")
            coordinator = next(
                item for item in dependencies["work_items"] if item["role"] == "coordinator"
            )
            coordinator_evidence = task_dir / str(coordinator["expected_refs"][0])
            coordinator_evidence.parent.mkdir(parents=True, exist_ok=True)
            coordinator_evidence.write_text("ready\n", encoding="utf-8")
            with (task_dir / "dispatch-receipts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        self.deterministic_receipt(
                            task_id,
                            coordinator,
                            "dispatch_completed",
                            2,
                            suspension_epoch=1,
                        )
                    )
                    + "\n"
                )

            with patch(
                "valp_cli.workflow.collect_runtime_preflight",
                return_value=self.routed_test_preflight(task_dir),
            ):
                second = dispatch_task(root, task_id, submit=True, runtime="queue")

            self.assertEqual(len(second), 2)
            self.assertEqual(
                {command.split("phase=", 1)[1].split(";", 1)[0] for command in second},
                {"implementer", "prototype"},
            )
            with self.assertRaisesRegex(SystemExit, "no ready phase"):
                dispatch_task(root, task_id, submit=True, runtime="queue")

    def test_concurrent_queue_submissions_allocate_one_contiguous_receipt_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            task_dir.mkdir()
            start_marker = task_dir / "start"
            script = "\n".join([
                "import sys, time",
                "from pathlib import Path",
                "from valp_cli.workflow import write_queue_submission",
                "task_dir = Path(sys.argv[1])",
                "while not (task_dir / 'start').exists(): time.sleep(0.001)",
                "target = sys.argv[3]",
                "write_queue_submission(task_dir, sys.argv[2], target, 'other', [f'agents/{target}/evidence.md'])",
            ])
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(task_dir), "TASK-QUEUE-RACE", f"worker-{index}"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for index in range(8)
            ]
            start_marker.write_text("go\n", encoding="utf-8")
            results = [process.communicate(timeout=20) for process in processes]

            self.assertEqual(
                [process.returncode for process in processes],
                [0] * len(processes),
                results,
            )
            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            sequences = [receipt["event_sequence"] for receipt in receipts]
            self.assertEqual(sorted(sequences), list(range(1, len(processes) + 1)))
            self.assertEqual(len(set(receipt["receipt_id"] for receipt in receipts)), len(processes))

    def test_queue_submission_retry_after_receipt_directory_fsync_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "task"
            task_dir.mkdir()
            expected_refs = ["agents/codex/evidence.md"]

            with patch(
                "valp_cli.workflow.fsync_directory",
                side_effect=[True, OSError(errno.EIO, "I/O failure")],
            ):
                with self.assertRaises(OSError):
                    write_queue_submission(
                        task_dir,
                        "TASK-QUEUE-RETRY",
                        "codex",
                        "implementer",
                        expected_refs,
                    )

            first_queue_record = read_json(task_dir / "queue/codex-implementer.json")
            retried_queue_record = write_queue_submission(
                task_dir,
                "TASK-QUEUE-RETRY",
                "codex",
                "implementer",
                expected_refs,
            )
            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(retried_queue_record, first_queue_record)
            self.assertEqual(len(receipts), 1)
            self.assertEqual(receipts[0]["event_sequence"], 1)

    def test_publish_stops_before_routing_until_leader_declares_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(root, "TASK-SMOKE", "Fix a bug and run tests")

            self.assertTrue((task_dir / "task.md").exists())
            self.assertTrue((task_dir / "state.json").exists())
            self.assertFalse((task_dir / "routing.json").exists())
            self.assertFalse((task_dir / "dispatch-receipts.jsonl").exists())
            state = read_json(task_dir / "state.json")
            self.assertEqual(state["status"], "published")
            self.assertEqual(state["selected_agents"], [])
            self.assertIn("Mode: Awaiting Leader assignment", (task_dir / "task.md").read_text(encoding="utf-8"))

    def test_cli_publish_does_not_route_without_a_leader_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "publish",
                        "TASK-CLI-PUBLISH",
                        "--workspace",
                        tmp,
                        "--prompt",
                        "Fix a bug and run tests",
                        "--json",
                    ]
                )

            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertFalse(payload["routed"])
            self.assertFalse(Path(payload["task_dir"]).joinpath("routing.json").exists())

    def test_cli_publish_and_scan_record_exact_source_provenance(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        entrypoint = repository / "bin" / "valp"
        expected_commit = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        expected_tree = subprocess.check_output(
            ["git", "-C", str(repository), "rev-parse", "HEAD^{tree}"],
            text=True,
        ).strip()

        with tempfile.TemporaryDirectory() as tmp:
            publish = subprocess.run(
                [
                    sys.executable,
                    str(entrypoint),
                    "publish",
                    "TASK-SOURCE-PROVENANCE",
                    "--workspace",
                    tmp,
                    "--prompt",
                    "Verify source provenance",
                    "--runtime",
                    "manual",
                    "--json",
                ],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
            task_dir = Path(json.loads(publish.stdout)["task_dir"])
            published_state = read_json(task_dir / "state.json")
            provenance = published_state["source_provenance"]
            task_start = provenance["task_start"]

            self.assertEqual(provenance["schema_version"], "valp-source-provenance.v1")
            self.assertEqual(provenance["last_observed"], task_start)
            self.assertEqual(task_start["implementation_id"], "valp-reference-cli")
            self.assertEqual(task_start["invoked_entrypoint"], str(entrypoint))
            self.assertEqual(task_start["resolved_entrypoint"], str(entrypoint.resolve()))
            self.assertEqual(task_start["source_root"], str(repository.resolve()))
            self.assertEqual(task_start["vcs"]["kind"], "git")
            self.assertEqual(task_start["vcs"]["commit"], expected_commit)
            self.assertEqual(task_start["vcs"]["tree"], expected_tree)
            self.assertIn(task_start["vcs"]["worktree_status"], {"clean", "dirty"})
            self.assertEqual(
                task_start["status"],
                f"resolved_{task_start['vcs']['worktree_status']}",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(entrypoint),
                    "scan",
                    "--workspace",
                    tmp,
                    "--task",
                    "TASK-SOURCE-PROVENANCE",
                    "--runtime",
                    "manual",
                    "--json",
                ],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
            scanned_state = read_json(task_dir / "state.json")

            self.assertEqual(scanned_state["source_provenance"]["task_start"], task_start)
            self.assertEqual(
                scanned_state["source_provenance"]["last_observed"]["vcs"]["commit"],
                expected_commit,
            )
            self.assertEqual(
                list(
                    schema_validator(repository / "schemas" / "state.schema.json").iter_errors(
                        scanned_state
                    )
                ),
                [],
            )

            legacy_state = dict(scanned_state)
            legacy_state.pop("source_provenance")
            workflow_module.write_json(task_dir / "state.json", legacy_state)
            subprocess.run(
                [
                    sys.executable,
                    str(entrypoint),
                    "scan",
                    "--workspace",
                    tmp,
                    "--task",
                    "TASK-SOURCE-PROVENANCE",
                    "--runtime",
                    "manual",
                    "--json",
                ],
                cwd=repository,
                check=True,
                capture_output=True,
                text=True,
            )
            legacy_scanned_state = read_json(task_dir / "state.json")
            self.assertIsNone(legacy_scanned_state["source_provenance"]["task_start"])

    def test_cli_publish_marks_non_git_source_provenance_unavailable(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            isolated_source = Path(tmp) / "isolated-source"
            shutil.copytree(repository / "valp_cli", isolated_source / "valp_cli")
            (isolated_source / "bin").mkdir(parents=True)
            shutil.copy2(repository / "bin" / "valp", isolated_source / "bin" / "valp")
            workspace = Path(tmp) / "workspace"
            publish = subprocess.run(
                [
                    sys.executable,
                    str(isolated_source / "bin" / "valp"),
                    "publish",
                    "TASK-NON-GIT-SOURCE",
                    "--workspace",
                    str(workspace),
                    "--prompt",
                    "Verify unavailable source provenance",
                    "--runtime",
                    "manual",
                    "--json",
                ],
                cwd=isolated_source,
                check=True,
                capture_output=True,
                text=True,
            )
            task_dir = Path(json.loads(publish.stdout)["task_dir"])
            observation = read_json(task_dir / "state.json")["source_provenance"]["task_start"]

            self.assertEqual(observation["status"], "unavailable")
            self.assertEqual(observation["source_root"], str(isolated_source.resolve()))
            self.assertEqual(
                observation["vcs"],
                {
                    "kind": "none",
                    "commit": None,
                    "tree": None,
                    "worktree_status": "unavailable",
                },
            )

    def test_dispatch_payload_uses_concise_brief_and_task_refs(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-08T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests", "writes verification evidence"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        long_tail = "UNIQUE_LONG_CONTEXT_TAIL_SHOULD_STAY_ONLY_IN_TASK_SOURCE"
        long_prompt = "Fix the routing bug and verify it. " + ("background detail " * 150) + long_tail

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(root, "TASK-BRIEF", long_prompt)

            task_text = (task_dir / "task.md").read_text(encoding="utf-8")
            dispatch = (task_dir / "agents" / "codex" / "dispatch.md").read_text(encoding="utf-8")

        self.assertIn(long_tail, task_text)
        self.assertNotIn(long_tail, dispatch)
        self.assertNotIn("## User Request", dispatch)
        self.assertIn("## Task Brief", dispatch)
        self.assertIn("## Task References", dispatch)
        self.assertIn("## Payload Budget", dispatch)
        self.assertIn("coordinator/leader owns dispatch precision", dispatch)
        self.assertIn(".herdr-loop/tasks/TASK-BRIEF/task.md", dispatch)
        self.assertIn(".herdr-loop/tasks/TASK-BRIEF/context-pack.json", dispatch)
        self.assertIn(".herdr-loop/tasks/TASK-BRIEF/skill-recommendations.json", dispatch)
        self.assertIn(".herdr-loop/tasks/TASK-BRIEF/control-contract.json", dispatch)
        self.assertIn(".herdr-loop/tasks/TASK-BRIEF/control-slices/codex.json", dispatch)

    def test_unbound_live_identity_invalidates_feedback_prior(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-10T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests", "writes verification evidence"],
                    "must_not_do": ["must not bypass approval gates"],
                    "model_identity": {
                        "agent_surface": "codex_cli",
                        "provider": "test-provider",
                        "declared_model": {
                            "model_id": "test-model",
                            "source": "test fixture",
                            "timestamp": "2026-07-10T00:00:00Z",
                            "confidence": "high",
                            "freshness": "current",
                        },
                        "observed_model": {
                            "model_id": "test-model",
                            "source": "test fixture",
                            "timestamp": "2026-07-10T00:00:00Z",
                            "confidence": "high",
                            "freshness": "current",
                        },
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_done_feedback_history(root)
            observed_at = datetime.now().astimezone().isoformat()
            live_preflight = {
                "generated_at": observed_at,
                "runtime": "test queue",
                "adapter_class": "daemon_queue",
                "status": "pass",
                "agents": {
                    "codex": {
                        "status": "pass",
                        "model_probe": {
                            "schema_version": "valp-model-probe.v1",
                            "status": "observed",
                            "source": "test queue metadata",
                            "observed_at": observed_at,
                            "ttl_seconds": 86400,
                            "model": {
                                "model_id": "test-model",
                                "provider": "test-provider",
                                "reasoning_mode": "unknown",
                                "confidence": "high",
                            },
                            "session_identity": {
                                "status": "known",
                                "token": "sha256:test-session",
                                "source": "test queue generation",
                                "generation": "1",
                            },
                        },
                    }
                },
            }
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=live_preflight):
                        task_dir = self.publish_routed_task(root, "TASK-PRIOR", "Fix a bug and run tests", runtime="queue")

            routing = read_json(task_dir / "routing.json")
            score = routing["candidate_scores"]["codex"]
            self.assertEqual(score["evidence_history"], 0.0)
            self.assertTrue(score["evidence_history_refs"])
            self.assertIn("OLD-DONE", score["evidence_history_refs"][0])
            self.assertEqual(score["model_evidence"]["history_status"], "invalidated")

    def test_completed_task_feedback_is_consumed_by_next_task_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_done_feedback_history(root, task_id="TASK-A-DONE")
            publish_task(root, "TASK-B-NEXT", "Fix a bug and run tests", runtime="queue")

            history = load_routing_feedback_history(root)
            self.assertEqual([record["task_id"] for record in history], ["TASK-A-DONE"])

            probe = {
                "schema_version": "valp-model-probe.v1",
                "status": "observed",
                "source": "test runtime metadata",
                "observed_at": "2026-07-10T00:00:00Z",
                "ttl_seconds": 86400,
                "model": {
                    "model_id": "test-model",
                    "provider": "test-provider",
                    "reasoning_mode": "unknown",
                    "confidence": "high",
                },
                "session_identity": {
                    "status": "known",
                    "token": "sha256:test-session",
                    "source": "test runtime metadata",
                    "generation": "1",
                },
            }
            agent_info = {
                "active": True,
                "role": ["implementation"],
                "model_identity": {
                    "declared_model": {
                        "model_id": "test-model",
                        "provider": "test-provider",
                        "reasoning_mode": "unknown",
                        "confidence": "high",
                    }
                },
            }
            first_identity = model_identity_for(
                "codex",
                agent_info,
                {},
                runtime_probe=probe,
                evaluated_at="2026-07-10T00:00:00Z",
            )
            agent_info["model_identity"]["history_binding"] = first_identity["history_binding"]

            scores = score_candidates(
                "software-code",
                {"codex": agent_info},
                history,
                runtime_preflight={
                    "agents": {
                        "codex": {
                            "model_probe": probe
                        }
                    }
                },
                evaluated_at="2026-07-10T00:05:00Z",
            )

            self.assertEqual(scores["codex"]["evidence_history"], 0.68)
            self.assertIn("TASK-A-DONE", scores["codex"]["evidence_history_refs"][0])

    def test_historical_success_cannot_route_to_missing_current_agent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_done_feedback_history(root, task_id="TASK-A-DONE")
            capabilities_path = root / ".valp" / "agents" / "capabilities.json"
            capabilities_path.parent.mkdir(parents=True)
            capabilities_path.write_text(
                json.dumps(
                    {
                        "schema_version": "valp-agent-capabilities.v1",
                        "updated_at": "2026-07-10T00:00:00Z",
                        "agents": {
                            "manual-operator": {
                                "active": True,
                                "role": ["review"],
                                "strengths": ["writes manual evidence"],
                                "skills": [],
                                "mcp_servers": [],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            task_dir = publish_task(
                root,
                "TASK-B-MISSING-CURRENT",
                "Review a source",
                profile="generic-analysis",
                runtime="manual",
            )
            declaration = {
                "schema_version": "valp-assignment-declaration.v1",
                "declaration_id": "decl-TASK-B-MISSING-CURRENT",
                "task_id": "TASK-B-MISSING-CURRENT",
                "declared_at": "2026-07-10T00:01:00Z",
                "leader": {
                    "agent_id": "manual-leader",
                    "selected_by": "user",
                    "selection_ref": "test-user-selection:TASK-B-MISSING-CURRENT",
                },
                "assignments": {"reviewer": "codex"},
                "reasons": {"reviewer": "Historical success must not substitute for current capability evidence."},
            }

            with patch("valp_cli.workflow.load_local_overlay", return_value={}):
                with self.assertRaisesRegex(SystemExit, "unknown_agent:reviewer:codex"):
                    route_task(
                        root,
                        "TASK-B-MISSING-CURRENT",
                        runtime="manual",
                        assignment_declaration=declaration,
                    )

            blocked = read_json(task_dir / "assignment-validation.json")
            self.assertEqual(blocked["status"], "blocked")
            self.assertIn("unknown_agent:reviewer:codex", blocked["blockers"])

    def test_learning_feedback_is_not_consumed_or_written_back_during_routing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_a = root / ".herdr-loop" / "tasks" / "TASK-A-LEARNING-ONLY"
            task_a.mkdir(parents=True)
            (task_a / "learning-feedback.json").write_text(
                json.dumps(
                    {
                        "schema_version": "valp-learning-feedback.v1",
                        "task_id": "TASK-A-LEARNING-ONLY",
                        "profile": "generic-analysis",
                        "result": "done",
                        "learning_items": [
                            {
                                "kind": "routing",
                                "observation": "A proposal exists but is not routing authority.",
                                "evidence_refs": [],
                                "confidence": "high",
                                "next_effect": "Requires explicit disposition.",
                            }
                        ],
                        "proposed_updates": [],
                        "privacy_notes": ["test fixture"],
                        "updated_at": "2026-07-10T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            registry = root / ".valp" / "registry.json"
            passport = root / ".valp" / "passport.json"
            capabilities = root / ".valp" / "agents" / "capabilities.json"
            capabilities.parent.mkdir(parents=True)
            capabilities.write_text(
                json.dumps(
                    {
                        "schema_version": "valp-agent-capabilities.v1",
                        "updated_at": "2026-07-10T00:00:00Z",
                        "agents": {
                            "manual-operator": {
                                "active": True,
                                "role": ["review"],
                                "strengths": ["writes manual evidence"],
                                "skills": [],
                                "mcp_servers": [],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            registry.parent.mkdir(parents=True, exist_ok=True)
            registry.write_text('{"version":1,"entries":[]}', encoding="utf-8")
            passport.write_text('{"version":1,"capabilities":[]}', encoding="utf-8")
            before_registry = registry.read_bytes()
            before_passport = passport.read_bytes()

            self.assertEqual(load_routing_feedback_history(root), [])
            task_b = publish_task(
                root,
                "TASK-B-LEARNING-ONLY",
                "Review a source",
                profile="generic-analysis",
                runtime="manual",
            )
            routing = route_task(
                root,
                "TASK-B-LEARNING-ONLY",
                runtime="manual",
                assignment_declaration={
                    "schema_version": "valp-assignment-declaration.v1",
                    "declaration_id": "decl-TASK-B-LEARNING-ONLY",
                    "task_id": "TASK-B-LEARNING-ONLY",
                    "declared_at": "2026-07-10T00:01:00Z",
                    "leader": {
                        "agent_id": "manual-leader",
                        "selected_by": "user",
                        "selection_ref": "test-user-selection:TASK-B-LEARNING-ONLY",
                    },
                    "assignments": {"reviewer": "manual-operator"},
                    "reasons": {"reviewer": "Manual reviewer is current and reachable for this fixture."},
                },
            )

            self.assertEqual(routing["candidate_scores"]["manual-operator"]["evidence_history_refs"], [])
            self.assertEqual(registry.read_bytes(), before_registry)
            self.assertEqual(passport.read_bytes(), before_passport)
            self.assertTrue((task_b / "routing.json").exists())

    def test_unbacked_feedback_index_does_not_affect_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            history = root / ".herdr-loop" / "routing-feedback.jsonl"
            history.parent.mkdir(parents=True)
            history.write_text(
                json.dumps(
                    {
                        "schema_version": "valp-routing-feedback.v1",
                        "task_id": "MISSING-TASK",
                        "profile": "software-code",
                        "selected_agents": ["codex"],
                        "result": "done",
                        "updated_at": "2026-07-09T00:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            loaded = load_routing_feedback_history(root)
            self.assertEqual(loaded, [])
            self.assertEqual(feedback_prior_for_agent("codex", "software-code", loaded)["score"], 0.6)

    def test_done_feedback_with_failed_task_gate_does_not_affect_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = self.write_done_feedback_history(root)
            state = read_json(directory / "state.json")
            state["gates"]["review"] = "needs_evidence"
            (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")

            self.assertEqual(load_routing_feedback_history(root), [])

    def test_altered_feedback_index_does_not_affect_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_done_feedback_history(root)
            history = root / ".herdr-loop" / "routing-feedback.jsonl"
            indexed = json.loads(history.read_text(encoding="utf-8"))
            indexed["selected_agents"] = ["claude"]
            history.write_text(json.dumps(indexed) + "\n", encoding="utf-8")

            self.assertEqual(load_routing_feedback_history(root), [])

    def test_divergent_task_local_result_does_not_affect_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = self.write_done_feedback_history(root)
            task_feedback = read_json(directory / "routing-feedback.json")
            task_feedback["result"] = "blocked"
            (directory / "routing-feedback.json").write_text(json.dumps(task_feedback), encoding="utf-8")

            self.assertEqual(load_routing_feedback_history(root), [])

    def test_feedback_evidence_symlink_cannot_escape_task_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = self.write_done_feedback_history(root)
            evidence = directory / "evidence" / "verification.md"
            evidence.unlink()
            outside = root / "outside-proof.md"
            outside.write_text("not task-local\n", encoding="utf-8")
            try:
                evidence.symlink_to(outside)
            except OSError:
                self.skipTest("Symlink creation is unavailable on this platform")

            self.assertEqual(load_routing_feedback_history(root), [])

    def test_scan_and_route_existing_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(root, "TASK-ROUTE", "Research a source", runtime="manual")
            scan_workspace(root, "TASK-ROUTE", runtime="manual")
            routing = route_task(
                root,
                "TASK-ROUTE",
                runtime="manual",
                assignment_declaration=self.assignment_declaration(
                    root,
                    "TASK-ROUTE",
                    "research",
                ),
            )

            self.assertEqual(task_dir.resolve(), (root / ".herdr-loop" / "tasks" / "TASK-ROUTE").resolve())
            self.assertEqual(routing["profile"], "research")
            self.assertTrue((task_dir / "routing.json").exists())

    def test_scan_excludes_overlay_only_agent_from_routing_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capabilities_path = root / ".valp" / "agents" / "capabilities.json"
            capabilities_path.parent.mkdir(parents=True)
            capabilities_path.write_text(
                json.dumps({"schema_version": "valp-agent-capabilities.v1", "agents": {}}),
                encoding="utf-8",
            )
            overlay = {
                "agent_capability_profiles": {
                    "overlay-only-agent": {
                        "routing_hint_only": True,
                        "likely_roles": ["implementation", "verification"],
                        "model_identity": {
                            "agent_surface": "local-surface",
                            "provider": "local-provider",
                            "declared_model": {"model_id": "local-model"},
                        },
                    }
                }
            }
            with patch("valp_cli.workflow.load_local_overlay", return_value=overlay):
                scanned = scan_workspace(root, runtime="manual")

            self.assertNotIn("overlay-only-agent", scanned["agents"])
            scores = score_candidates("software-code", scanned["agents"])
            self.assertNotIn("overlay-only-agent", scores)

    def test_refresh_scan_preserves_routed_task_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(root, "TASK-SCAN-REFRESH", "Inspect runtime", runtime="manual")
            state = read_json(task_dir / "state.json")
            state["status"] = "dispatching"
            workflow_module.write_json(task_dir / "state.json", state)

            scan_workspace(root, "TASK-SCAN-REFRESH", runtime="manual")

            refreshed = read_json(task_dir / "state.json")
            self.assertEqual(refreshed["status"], "dispatching")
            self.assertEqual(refreshed["capabilities_ref"], ".herdr-loop/agents/capabilities.json")

            refreshed["status"] = "scanning_capabilities"
            refreshed["selected_agents"] = ["codex"]
            workflow_module.write_json(task_dir / "state.json", refreshed)
            workflow_module.write_json(task_dir / "routing.json", {"selected_agents": ["codex"]})

            scan_workspace(root, "TASK-SCAN-REFRESH", runtime="manual")

            recovered = read_json(task_dir / "state.json")
            self.assertEqual(recovered["status"], "dispatching")

    def test_refresh_scan_reconciles_bound_provider_evidence_without_rewriting_dispatch_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-SCAN-BOUND-PROVIDERS"
            directory = root / ".herdr-loop" / "tasks" / task_id
            directory.mkdir(parents=True)
            historical_preflight = {
                "generated_at": "2026-08-07T17:13:07Z",
                "runtime": "HERDR",
                "adapter_class": "pane_controller",
                "status": "warn",
                "checks": {},
                "agents": {},
            }
            workflow_module.write_json(
                directory / "routing.json",
                {
                    "task_id": task_id,
                    "selected_agents": ["codex", "claude"],
                    "role_assignments": {
                        "coordinator": "codex",
                        "implementer": "codex",
                        "reviewer": "claude",
                    },
                    "runtime_adapter": {
                        "id": "herdr",
                        "class": "pane_controller",
                        "preflight": historical_preflight,
                    },
                    "provider_matrix": {
                        "generated_at": "2026-08-07T17:36:36Z",
                        "runtime_preflight": historical_preflight,
                        "model_awareness": {
                            "required": True,
                            "dynamic_discovery_required": True,
                        },
                        "providers": {},
                    },
                    "control_contract": {
                        "status": "recorded",
                        "ref": "control-contract.json",
                        "digest": "sha256:immutable",
                    },
                },
            )
            workflow_module.write_json(
                directory / "state.json",
                {
                    "task_id": task_id,
                    "status": "dispatching",
                    "selected_agents": ["codex", "claude"],
                },
            )
            workflow_module.write_json(
                directory / "agent-sessions.json",
                {
                    "schema_version": "valp-agent-sessions.v1",
                    "task_id": task_id,
                    "bindings": {
                        "codex": {"runtime_identity": {"pane_id": "pane-codex"}},
                        "claude": {"runtime_identity": {"pane_id": "pane-claude"}},
                    },
                },
            )
            contract_bytes = b'{"immutable":true}\n'
            receipt_bytes = b'{"event":"dispatch_completed"}\n'
            (directory / "control-contract.json").write_bytes(contract_bytes)
            (directory / "dispatch-receipts.jsonl").write_bytes(receipt_bytes)
            capabilities = {
                "schema_version": "valp-agent-capabilities.v1",
                "agents": {
                    "codex": {"active": True, "mcp_servers": [], "must_not_do": []},
                    "claude": {"active": True, "mcp_servers": [], "must_not_do": []},
                },
            }

            def probe(agent: str, timestamp: str) -> dict[str, object]:
                return {
                    "status": "observed",
                    "source": f"herdr:{agent}",
                    "observed_at": timestamp,
                    "ttl_seconds": 3600,
                    "model": {
                        "model_id": f"{agent}-model",
                        "provider": f"{agent}-provider",
                        "reasoning_mode": "medium",
                        "confidence": "high",
                    },
                    "session_identity": {
                        "status": "known",
                        "token": f"sha256:{agent}",
                        "source": f"herdr:{agent}",
                        "generation": f"session:{agent}",
                    },
                }

            current_preflight = {
                "generated_at": "2026-08-08T03:00:00Z",
                "runtime": "HERDR",
                "adapter_class": "pane_controller",
                "status": "pass",
                "checks": {},
                "agents": {
                    "codex": {"status": "pass", "model_probe": probe("codex", "2026-08-08T02:57:51Z")},
                    "claude": {"status": "pass", "model_probe": probe("claude", "2026-08-08T02:58:07Z")},
                },
            }
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities), \
                    patch("valp_cli.workflow.load_local_overlay", return_value={}), \
                    patch("valp_cli.workflow.collect_runtime_preflight", return_value=current_preflight):
                scan_workspace(root, task_id, runtime="herdr")

            refreshed = read_json(directory / "routing.json")
            self.assertEqual(
                refreshed["provider_matrix"]["providers"]["codex"]["model_identity"]["observed_model"]["timestamp"],
                "2026-08-08T02:57:51Z",
            )
            self.assertEqual(
                refreshed["provider_matrix"]["providers"]["claude"]["model_identity"]["observed_model"]["timestamp"],
                "2026-08-08T02:58:07Z",
            )
            self.assertEqual(refreshed["provider_matrix"]["runtime_preflight"], historical_preflight)
            self.assertEqual(refreshed["control_contract"]["digest"], "sha256:immutable")
            self.assertEqual((directory / "control-contract.json").read_bytes(), contract_bytes)
            self.assertEqual((directory / "dispatch-receipts.jsonl").read_bytes(), receipt_bytes)

    def test_route_validates_and_records_leader_declared_assignments_without_selecting_agents(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T10:00:00Z",
            "source": "test fixture",
            "agents": {
                "engineer": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "review"],
                    "strengths": ["state", "code", "tests", "review"],
                    "skills": [],
                    "mcp_servers": [],
                }
            },
        }
        declaration = {
            "schema_version": "valp-assignment-declaration.v1",
            "declaration_id": "decl-TASK-LEADER-ASSIGNMENT-1",
            "task_id": "TASK-LEADER-ASSIGNMENT",
            "declared_at": "2026-07-23T10:00:00Z",
            "leader": {
                "agent_id": "engineer",
                "selected_by": "user",
                "selection_ref": "user-message:approved-leader",
            },
            "assignments": {
                "coordinator": "engineer",
                "implementer": "engineer",
                "reviewer": "engineer",
            },
            "reasons": {
                "coordinator": "User-selected Leader owns visible state.",
                "implementer": "Observed implementation capability.",
                "reviewer": "Manual-mode fixture review assignment.",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(
                root,
                "TASK-LEADER-ASSIGNMENT",
                "Verify a runtime protocol change.",
                profile="agent-runtime",
            )
            self.assertFalse(hasattr(workflow_module, "select_agents"))
            self.assertFalse(hasattr(workflow_module, "role_assignments_for"))
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities), \
                    patch("valp_cli.workflow.skill_router_command", return_value=None):
                routing = route_task(
                    root,
                    "TASK-LEADER-ASSIGNMENT",
                    runtime="manual",
                    assignment_declaration=declaration,
                )

            self.assertEqual(routing["selected_agents"], ["engineer"])
            self.assertEqual(routing["role_assignments"], declaration["assignments"])
            self.assertEqual(routing["assignment_authority"], "leader_declared")
            self.assertEqual(read_json(task_dir / "assignment-declaration.json"), declaration)

    def test_codex_cli_leader_can_assign_a_separate_codex_cli_worker(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T10:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["implementation", "verification"],
                    "skills": [],
                    "mcp_servers": [],
                },
                "reviewer": {
                    "active": True,
                    "role": ["review", "risk_review"],
                    "skills": [],
                    "mcp_servers": [],
                },
            },
        }
        declaration = {
            "schema_version": "valp-assignment-declaration.v1",
            "declaration_id": "decl-TASK-SEPARATE-LEADER-1",
            "task_id": "TASK-SEPARATE-LEADER",
            "declared_at": "2026-07-23T10:00:00Z",
            "leader": {
                "agent_id": "codex",
                "selected_by": "user",
                "selection_ref": "leader-session-binding:codex-cli-leader",
            },
            "assignments": {
                "implementer": "codex",
                "reviewer": "reviewer",
            },
            "reasons": {
                "implementer": "The Codex CLI Leader explicitly assigned a separate Codex CLI Worker session.",
                "reviewer": "Leader-declared independent reviewer.",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(
                root,
                "TASK-SEPARATE-LEADER",
                "Implement and independently review a source change.",
                profile="software-code",
            )
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities), \
                    patch("valp_cli.workflow.skill_router_command", return_value=None):
                routing = route_task(
                    root,
                    "TASK-SEPARATE-LEADER",
                    runtime="manual",
                    assignment_declaration=declaration,
                )

            self.assertEqual(routing["coordinator_selection"]["selected_agent"], "codex")
            self.assertEqual(routing["selected_agents"], ["codex", "reviewer"])
            self.assertEqual(routing["role_requirements"], ["implementer", "reviewer"])
            attention_map = read_json(task_dir / "attention-map.json")
            self.assertEqual(attention_map["leader_agent"], "codex")
            self.assertEqual(attention_map["heads"]["state_gate"]["selected"], "codex")
            self.assertEqual(attention_map["heads"]["state_gate"]["status"], "user_selected_leader")
            self.assertNotIn("coordinator", attention_map["role_assignments"])
            codex_slice = read_json(task_dir / "control-slices" / "codex.json")
            self.assertEqual(codex_slice["agent"], "codex")
            self.assertEqual(codex_slice["work_item_ids"], ["implementer:codex"])
            self.assertNotIn("leader_epoch", codex_slice)
            self.assertNotIn("installation_id", codex_slice)
            dispatch = (task_dir / "agents" / "codex" / "dispatch.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Role: `implementer`", dispatch)
            self.assertNotIn("Role: `leader`", dispatch)
            declaration_errors = list(
                schema_validator(
                    Path(__file__).resolve().parents[1] / "schemas" / "assignment-declaration.schema.json"
                ).iter_errors(declaration)
            )
            self.assertEqual(declaration_errors, [])
            self.assertEqual(TaskAudit(task_dir).check_assignment_authority().status, "pass")

    def test_route_blocks_before_scan_or_dispatch_when_assignment_declaration_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(root, "TASK-NO-ASSIGNMENT", "Research a source")

            with patch("valp_cli.workflow.scan_workspace", side_effect=AssertionError("scan ran before declaration")):
                with self.assertRaisesRegex(SystemExit, "Leader-authored assignment declaration"):
                    route_task(root, "TASK-NO-ASSIGNMENT")

            self.assertFalse((task_dir / "routing.json").exists())
            self.assertFalse((task_dir / "dispatch-receipts.jsonl").exists())

    def test_route_rejects_incomplete_leader_declaration_before_capability_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-INCOMPLETE-ASSIGNMENT"
            task_dir = publish_task(root, task_id, "Research a source", profile="research")
            declaration = self.assignment_declaration(root, task_id, "research")
            declaration["reasons"].pop("reviewer")

            with patch(
                "valp_cli.workflow.scan_workspace",
                side_effect=AssertionError("scan ran before declaration validation"),
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "missing_assignment_reason:reviewer",
                ):
                    route_task(root, task_id, assignment_declaration=declaration)

            self.assertFalse((task_dir / "assignment-declaration.json").exists())
            self.assertFalse((task_dir / "routing.json").exists())
            self.assertFalse((task_dir / "dispatch-receipts.jsonl").exists())

    def test_route_rejects_fields_outside_closed_assignment_declaration_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-EXTRA-ASSIGNMENT-FIELD"
            task_dir = publish_task(root, task_id, "Research a source", profile="research")
            declaration = self.assignment_declaration(root, task_id, "research")
            declaration["valp_selected_fallback"] = "reviewer"

            with patch(
                "valp_cli.workflow.scan_workspace",
                side_effect=AssertionError("scan ran before declaration validation"),
            ):
                with self.assertRaisesRegex(SystemExit, "unexpected_fields"):
                    route_task(root, task_id, assignment_declaration=declaration)

            self.assertFalse((task_dir / "assignment-declaration.json").exists())
            self.assertFalse((task_dir / "routing.json").exists())
            self.assertFalse((task_dir / "dispatch-receipts.jsonl").exists())

    def test_cli_route_requires_and_records_assignment_declaration_file(self) -> None:
        declaration = {
            "schema_version": "valp-assignment-declaration.v1",
            "declaration_id": "decl-TASK-CLI-ROUTE-1",
            "task_id": "TASK-CLI-ROUTE",
            "declared_at": "2026-07-23T10:00:00Z",
            "leader": {
                "agent_id": "manual-operator",
                "selected_by": "user",
                "selection_ref": "user-message:manual-leader",
            },
            "assignments": {
                "coordinator": "manual-operator",
                "researcher": "manual-operator",
                "reviewer": "manual-operator",
            },
            "reasons": {
                "coordinator": "User selected the manual operator as Leader.",
                "researcher": "Manual research assignment.",
                "reviewer": "Manual review assignment.",
            },
        }
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T10:00:00Z",
            "source": "test fixture",
            "agents": {
                "manual-operator": {
                    "active": True,
                    "role": ["coordination", "research", "review"],
                    "skills": [],
                    "mcp_servers": [],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            publish_task(root, "TASK-CLI-ROUTE", "Research a source", profile="research")
            declaration_path = root / "leader-assignments.json"
            declaration_path.write_text(json.dumps(declaration), encoding="utf-8")
            output = io.StringIO()

            with contextlib.redirect_stdout(output), \
                    patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities), \
                    patch("valp_cli.workflow.skill_router_command", return_value=None):
                code = main(
                    [
                        "route",
                        "TASK-CLI-ROUTE",
                        "--workspace",
                        str(root),
                        "--runtime",
                        "manual",
                        "--assignments",
                        str(declaration_path),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0)
            routing = json.loads(output.getvalue())
            self.assertEqual(routing["assignment_authority"], "leader_declared")
            self.assertEqual(routing["role_assignments"], declaration["assignments"])

    def test_route_blocks_leader_assignment_that_violates_agent_role_boundary(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T10:00:00Z",
            "source": "test fixture",
            "agents": {
                "leader": {
                    "active": True,
                    "role": ["coordination", "review"],
                    "strengths": ["state", "review"],
                },
                "readonly": {
                    "active": True,
                    "role": ["review", "code_review"],
                    "strengths": ["read-only review"],
                    "must_not_do": ["must not edit source"],
                },
            },
        }
        declaration = {
            "schema_version": "valp-assignment-declaration.v1",
            "declaration_id": "decl-role-boundary-1",
            "task_id": "TASK-ROLE-BOUNDARY",
            "declared_at": "2026-07-23T10:00:00Z",
            "leader": {
                "agent_id": "leader",
                "selected_by": "user",
                "selection_ref": "user-message:leader",
            },
            "assignments": {
                "coordinator": "leader",
                "implementer": "readonly",
                "reviewer": "leader",
            },
            "reasons": {
                "coordinator": "User-selected Leader.",
                "implementer": "Leader declaration under validation.",
                "reviewer": "Review assignment.",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(
                root,
                "TASK-ROLE-BOUNDARY",
                "Implement and review a source change.",
                profile="software-code",
            )
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities), \
                    patch("valp_cli.workflow.skill_router_command", return_value=None):
                with self.assertRaisesRegex(SystemExit, "role_ineligible:implementer:readonly"):
                    route_task(
                        root,
                        "TASK-ROLE-BOUNDARY",
                        runtime="manual",
                        assignment_declaration=declaration,
                    )

            validation = read_json(task_dir / "assignment-validation.json")
            blocked_state = read_json(task_dir / "state.json")
            self.assertIn("role_ineligible:implementer:readonly", validation["blockers"])
            self.assertEqual(blocked_state["assignment_authority"], "leader_declared")
            self.assertEqual(blocked_state["assignment_declaration"]["leader_agent"], "leader")
            self.assertEqual(blocked_state["assignment_validation"]["status"], "blocked")
            self.assertFalse((task_dir / "dispatch-receipts.jsonl").exists())

    def test_reroute_reclassifies_and_clears_stale_approval_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(
                root,
                "TASK-RISK-REROUTE",
                "Do not publish a release, deploy, or delete files.",
            )
            state = read_json(task_dir / "state.json")
            stale_risk = [{"kind": "release", "matched": "release"}]
            state["risk"] = {"approval_required": True, "matches": stale_risk}
            state["approval_required"] = stale_risk
            state["gates"]["approval"] = "needs_approval"
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

            with patch("valp_cli.workflow.skill_router_command", return_value=None):
                route_task(
                    root,
                    "TASK-RISK-REROUTE",
                    runtime="manual",
                    assignment_declaration=self.assignment_declaration(
                        root,
                        "TASK-RISK-REROUTE",
                        classify_profile("Do not publish a release, deploy, or delete files."),
                    ),
                )

            rerouted = read_json(task_dir / "state.json")
            self.assertEqual(rerouted["risk"], {"approval_required": False, "matches": []})
            self.assertEqual(rerouted["approval_required"], [])
            self.assertEqual(rerouted["gates"]["approval"], "not_required")

    def test_skill_recommendations_are_written_into_dispatch(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-05T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "skills": ["tdd"],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests", "writes verification evidence"],
                    "must_not_do": ["must not bypass approval gates"],
                }
            },
        }
        long_skill_task = (
            "run tests and write verification evidence "
            + ("with repeated implementation context " * 40)
            + "UNIQUE_SKILL_RECOMMENDATION_TAIL"
        )
        router_payload = {
            "batch": True,
            "num_tasks": 1,
            "results": [
                {
                    "task": long_skill_task,
                    "routing": {
                        "priority": "P1",
                        "decision": "auto-load",
                        "reason": "Strong installed workflow match.",
                    },
                    "matches": [
                        {
                            "skill": "verification-before-completion",
                            "installed": True,
                            "path": "/tmp/.agents/skills/verification-before-completion/SKILL.md",
                            "confidence": 0.44,
                            "mode": "auto-load",
                            "reason": "test match",
                        }
                    ],
                    "missing_skills": [],
                }
            ],
            "missing_skills": [],
            "routing": {
                "priority": "P1",
                "decision": "auto-load",
                "reason": "Highest-priority routing decision across batch tasks.",
            },
        }
        codex_payload = {
            "batch": True,
            "num_tasks": 1,
            "results": [
                {
                    "task": long_skill_task,
                    "routing": {
                        "priority": "P1",
                        "decision": "auto-load",
                        "reason": "Strong installed workflow match.",
                    },
                    "matches": [
                        {
                            "skill": "tdd",
                            "installed": True,
                            "path": "/tmp/.agents/skills/tdd/SKILL.md",
                            "confidence": 0.51,
                            "mode": "auto-load",
                            "reason": "provider-filtered codex match",
                        }
                    ],
                    "missing_skills": [],
                }
            ],
            "missing_skills": [],
            "routing": {
                "priority": "P1",
                "decision": "auto-load",
                "reason": "Highest-priority routing decision across batch tasks.",
            },
        }

        def fake_run_command(command, timeout=8.0, input_text=None, stdout_limit=4000, stderr_limit=4000):
            if command == ["task-skill-router", "--batch"]:
                return {
                    "command": command,
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps(router_payload),
                    "stderr": "",
                }
            if command == ["task-skill-router", "--agent", "codex", "--batch"]:
                return {
                    "command": command,
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps(codex_payload),
                    "stderr": "",
                }
            if len(command) == 4 and command[:2] == ["task-skill-router", "--agent"] and command[3] == "--batch":
                return {
                    "command": command,
                    "ok": True,
                    "exit_code": 0,
                    "stdout": json.dumps({"batch": True, "results": [], "missing_skills": [], "routing": {}}),
                    "stderr": "",
                }
            return {
                "command": command,
                "ok": True,
                "exit_code": 0,
                "stdout": "{}",
                "stderr": "",
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=["task-skill-router"]):
                    with patch("valp_cli.workflow.run_command", side_effect=fake_run_command):
                        task_dir = self.publish_routed_task(root, "TASK-SKILL", "Fix a bug and run tests")

            recommendations = read_json(task_dir / "skill-recommendations.json")
            self.assertEqual(recommendations["status"], "complete")
            self.assertEqual(recommendations["results"][0]["matches"][0]["skill"], "verification-before-completion")
            self.assertIn("per_agent", recommendations)
            self.assertEqual(recommendations["per_agent"]["codex"]["results"][0]["matches"][0]["skill"], "tdd")
            self.assertIn("UNIQUE_SKILL_RECOMMENDATION_TAIL", recommendations["per_agent"]["codex"]["results"][0]["task"])

            routing = read_json(task_dir / "routing.json")
            for agent in routing["selected_agents"]:
                dispatch = (task_dir / "agents" / agent / "dispatch.md").read_text(encoding="utf-8")
                self.assertIn("## Recommended Skills", dispatch)
            codex_dispatch = (task_dir / "agents" / "codex" / "dispatch.md").read_text(encoding="utf-8")
            self.assertIn("tdd", codex_dispatch)
            self.assertIn("skill-slices/codex.json", codex_dispatch)
            self.assertNotIn("UNIQUE_SKILL_RECOMMENDATION_TAIL", codex_dispatch)
            self.assertTrue((task_dir / "skill-slices" / "codex.json").exists())
            if routing["role_assignments"].get("coordinator") == "codex":
                self.assertIn("Full recommendation records remain in `skill-recommendations.json`", codex_dispatch)
            else:
                self.assertNotIn("- `.herdr-loop/tasks/TASK-SKILL/skill-recommendations.json`", codex_dispatch)

    def test_adaptive_budget_and_provider_reachable_slices_are_recorded(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-15T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "hermes": {
                    "active": True,
                    "role": ["coordination"],
                    "skills": [],
                    "mcp_servers": ["hermes-mcp"],
                    "strengths": ["state", "gates", "coordination"],
                },
                "codex": {
                    "active": True,
                    "role": ["implementation"],
                    "skills": ["tdd"],
                    "mcp_servers": ["repo-mcp"],
                    "strengths": ["edits files", "runs tests", "verification"],
                },
                "claude": {
                    "active": True,
                    "role": ["reviewer"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["read-only review", "risk review"],
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(root, "TASK-ADAPTIVE-BUDGET", "Fix a bug and run tests", runtime="manual")

            routing = read_json(task_dir / "routing.json")
            budget = read_json(task_dir / "iteration-budget.json")
            state_validator = schema_validator(
                Path(__file__).resolve().parents[1] / "schemas" / "state.schema.json"
            )
            self.assertEqual(
                list(state_validator.iter_errors(read_json(task_dir / "state.json"))),
                [],
            )
            self.assertEqual(budget["schema_version"], "valp-iteration-budget.v1")
            self.assertEqual(budget["strategy"], "leader_declared_bounded_team")
            self.assertEqual(budget["usage"]["dispatches"], 0)
            self.assertEqual(routing["iteration_budget"], {"status": "recorded", "ref": "iteration-budget.json"})
            self.assertEqual(set(routing["skill_recommendation_slices"]), set(routing["selected_agents"]))
            for agent in routing["selected_agents"]:
                self.assertTrue((task_dir / routing["skill_recommendation_slices"][agent]).exists())

            codex_dispatch = (task_dir / "agents" / "codex" / "dispatch.md").read_text(encoding="utf-8")
            self.assertIn("skill-slices/codex.json", codex_dispatch)
            self.assertNotIn("- `.herdr-loop/tasks/TASK-ADAPTIVE-BUDGET/skill-recommendations.json`", codex_dispatch)
            self.assertIn("iteration-budget.json", codex_dispatch)

            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                route_task(root, "TASK-ADAPTIVE-BUDGET", runtime="manual")
            history = (task_dir / "routing-history.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(history), 1)
            self.assertEqual(read_json(task_dir / "iteration-budget.json")["usage"]["reroutes"], 1)

            budget["max_dispatches"] = 1
            (task_dir / "iteration-budget.json").write_text(json.dumps(budget), encoding="utf-8")
            state = read_json(task_dir / "state.json")
            with self.assertRaises(SystemExit):
                enforce_iteration_budget(
                    task_dir,
                    routing,
                    state,
                    [("codex", "implementer"), ("claude", "reviewer")],
                )
            stopped = read_json(task_dir / "iteration-budget.json")
            self.assertEqual(stopped["status"], "blocked")
            self.assertIn("dispatch-count", stopped["stop_reason"])

    def test_route_resumes_inflight_reroute_after_runtime_preflight_recovers(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-16T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "hermes": {
                    "active": True,
                    "role": ["coordination"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["state", "coordination"],
                },
                "codex": {
                    "active": True,
                    "role": ["implementation", "verification"],
                    "skills": ["tdd"],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests"],
                },
                "claude": {
                    "active": True,
                    "role": ["reviewer"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["read-only review"],
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = self.publish_routed_task(
                        root,
                        "TASK-REROUTE-RECOVERY",
                        "Fix a bug and run tests",
                        runtime="queue",
                    )
                    routing = read_json(task_dir / "routing.json")
                    digest = "sha256:" + hashlib.sha256(
                        json.dumps(routing, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest()
                    budget = read_json(task_dir / "iteration-budget.json")
                    budget["status"] = "blocked"
                    budget["stop_reason"] = "runtime preflight failure"
                    budget["usage"]["reroutes"] = 1
                    (task_dir / "iteration-budget.json").write_text(
                        json.dumps(budget),
                        encoding="utf-8",
                    )
                    (task_dir / "routing-history.jsonl").write_text(
                        json.dumps(
                            {
                                "schema_version": "valp-routing-history.v1",
                                "task_id": "TASK-REROUTE-RECOVERY",
                                "event": "reroute_started",
                                "reroute_number": 1,
                                "previous_routing_digest": digest,
                                "previous_selected_agents": routing["selected_agents"],
                                "previous_role_assignments": routing["role_assignments"],
                                "recorded_at": "2026-07-16T00:00:00Z",
                            }
                        )
                        + "\n",
                        encoding="utf-8",
                    )

                    with patch(
                        "valp_cli.workflow.collect_runtime_preflight",
                        return_value=self.routed_test_preflight(task_dir),
                    ):
                        route_task(root, "TASK-REROUTE-RECOVERY", runtime="queue")

            resumed_budget = read_json(task_dir / "iteration-budget.json")
            history = [
                json.loads(line)
                for line in (task_dir / "routing-history.jsonl").read_text(encoding="utf-8").splitlines()
            ]

        self.assertEqual(resumed_budget["status"], "active")
        self.assertEqual(resumed_budget["usage"]["reroutes"], 1)
        self.assertEqual(history[-1]["event"], "reroute_resumed")
        self.assertEqual(history[-1]["reroute_number"], 1)

    def test_revised_assignments_can_reroute_after_runtime_retry_exhaustion(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-26T00:00:00Z",
            "source": "test fixture",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["coordinates", "edits files", "runs tests"],
                },
                "qwen": {
                    "active": True,
                    "role": ["implementation", "verification"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["edits files", "runs tests"],
                },
                "claude": {
                    "active": True,
                    "role": ["reviewer"],
                    "skills": [],
                    "mcp_servers": [],
                    "strengths": ["read-only review"],
                },
            },
        }
        task_id = "TASK-REROUTE-AFTER-RUNTIME-EXHAUSTION"
        first = {
            "schema_version": "valp-assignment-declaration.v1",
            "declaration_id": "decl-runtime-exhaustion-1",
            "task_id": task_id,
            "declared_at": "2026-07-26T00:00:00Z",
            "leader": {
                "agent_id": "codex",
                "selected_by": "user",
                "selection_ref": "test-user-selection:runtime-exhaustion",
            },
            "assignments": {
                "coordinator": "codex",
                "implementer": "codex",
                "reviewer": "claude",
            },
            "reasons": {
                "coordinator": "User-selected test Leader.",
                "implementer": "Initial implementation route.",
                "reviewer": "Independent review route.",
            },
        }
        revised = {
            **first,
            "declaration_id": "decl-runtime-exhaustion-2",
            "declared_at": "2026-07-26T00:01:00Z",
            "assignments": {
                "coordinator": "codex",
                "implementer": "qwen",
                "reviewer": "claude",
            },
            "reasons": {
                "coordinator": "User-selected test Leader.",
                "implementer": "Scope-reduced replacement after the prior runtime exhausted its retry.",
                "reviewer": "Independent review route.",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    publish_task(
                        root,
                        task_id,
                        "Repair and independently review an agent runtime.",
                        profile="agent-runtime",
                        runtime="manual",
                    )
                    route_task(
                        root,
                        task_id,
                        runtime="manual",
                        assignment_declaration=first,
                    )
                    task_dir = root / ".herdr-loop" / "tasks" / task_id
                    budget = read_json(task_dir / "iteration-budget.json")
                    budget["status"] = "blocked"
                    budget["stop_reason"] = "runtime dispatch retry exhausted"
                    (task_dir / "iteration-budget.json").write_text(
                        json.dumps(budget), encoding="utf-8"
                    )

                    route_task(
                        root,
                        task_id,
                        runtime="manual",
                        assignment_declaration=revised,
                    )

            rerouted = read_json(task_dir / "routing.json")
            rerouted_budget = read_json(task_dir / "iteration-budget.json")
            history = [
                json.loads(line)
                for line in (task_dir / "routing-history.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]

        self.assertEqual(rerouted["role_assignments"]["implementer"], "qwen")
        self.assertEqual(rerouted_budget["status"], "active")
        self.assertIsNone(rerouted_budget["stop_reason"])
        self.assertEqual(rerouted_budget["usage"]["reroutes"], 1)
        self.assertEqual(history[-1]["event"], "reroute_started")
        self.assertEqual(history[-1]["reroute_number"], 1)

    def test_iteration_budget_reopens_for_evidence_producing_review_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp)
            budget = {
                "schema_version": "valp-iteration-budget.v1",
                "task_id": "TASK-REVIEW-RECOVERY",
                "max_dispatch_reference_tokens": 1000,
                "max_dispatches": 3,
                "max_reroutes": 1,
                "max_fix_review_rounds": 3,
                "usage": {
                    "dispatch_reference_tokens": 0,
                    "dispatches": 0,
                    "reroutes": 1,
                    "fix_review_rounds": 1,
                },
                "status": "blocked",
                "stop_reason": "critical or high review blocker; missing expected evidence",
            }
            (task_dir / "iteration-budget.json").write_text(json.dumps(budget), encoding="utf-8")
            state = {
                "status": "dispatching",
                "gates": {
                    "approval": "passed",
                    "verification": "passed",
                    "review": "blocked",
                    "expected_evidence": "blocked",
                },
            }
            routing = {
                "dispatch_payload_budgets": {
                    "claude": {"actual_reference_tokens": 100}
                }
            }

            reopened = enforce_iteration_budget(
                task_dir,
                routing,
                state,
                [("claude", "reviewer")],
            )

        self.assertEqual(reopened["status"], "active")
        self.assertIsNone(reopened["stop_reason"])

    def test_iteration_budget_counts_legacy_and_v2_submission_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            expected_refs = ["agents/codex/evidence.md", "evidence/verification.md"]
            records = [
                {
                    "ts": "2026-07-14T00:00:00Z",
                    "agent": "codex",
                    "event": "dispatch_submitted",
                    "dispatch_ref": "agents/codex/dispatch.md",
                    "expected_refs": [],
                },
                {
                    "schema_version": "valp-dispatch-receipt.v2",
                    "receipt_id": "receipt-codex-1",
                    "task_id": "TASK-BUDGET-DEDUP",
                    "event_sequence": 1,
                    "ts": "2026-07-14T00:00:00Z",
                    "agent": "codex",
                    "role": "implementer",
                    "work_item_id": "implementer:codex",
                    "dispatch_id": "TASK-BUDGET-DEDUP:implementer:1",
                    "dispatch_generation": 1,
                    "event": "dispatch_submitted",
                    "dispatch_ref": "agents/codex/dispatch.md",
                    "expected_refs": expected_refs,
                },
            ]
            (directory / "dispatch-receipts.jsonl").write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            routing = {
                "dispatch_payload_budgets": {
                    "codex": {"actual_reference_tokens": 605}
                }
            }
            budget = {
                "schema_version": "valp-iteration-budget.v1",
                "task_id": "TASK-BUDGET-DEDUP",
                "max_dispatch_reference_tokens": 2000,
                "max_dispatches": 3,
                "max_reroutes": 1,
                "max_fix_review_rounds": 3,
                "usage": {
                    "dispatch_reference_tokens": 0,
                    "dispatches": 0,
                    "reroutes": 0,
                    "fix_review_rounds": 0,
                },
                "status": "exhausted",
                "stop_reason": "dispatches budget exhausted",
            }

            refreshed = workflow_module.refresh_iteration_budget(
                directory,
                routing,
                budget,
            )

            self.assertEqual(refreshed["usage"]["dispatches"], 1)
            self.assertEqual(refreshed["usage"]["dispatch_reference_tokens"], 605)
            self.assertEqual(refreshed["status"], "active")

    def test_iteration_budget_preserves_untranslated_same_second_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            task_id = "TASK-BUDGET-SAME-SECOND"
            dependencies = build_submission_dependencies(
                task_id,
                {"coordinator": "codex", "implementer": "codex"},
            )
            (directory / "submission-dependencies.json").write_text(
                json.dumps(dependencies),
                encoding="utf-8",
            )
            (directory / "state.json").write_text(
                json.dumps({"schema_version": "valp-visible-loop-state.v2", "status": "dispatching"}),
                encoding="utf-8",
            )
            receipt_path = directory / "dispatch-receipts.jsonl"
            first = {
                "ts": "2026-07-14T00:00:00Z",
                "agent": "codex",
                "event": "dispatch_submitted",
                "dispatch_ref": "agents/codex/dispatch.md",
                "expected_refs": [],
                "proof": {"submit_proof": {"status": "working", "attempts": 1}},
                "runtime": {"pane_id": "w5:pS"},
            }
            receipt_path.write_text(json.dumps(first) + "\n", encoding="utf-8")
            self.assertEqual(
                translate_legacy_herdr_receipts(
                    directory,
                    task_id,
                    phase=("codex", "coordinator"),
                ),
                1,
            )
            second = {
                **first,
                "proof": {"submit_proof": {"status": "working", "attempts": 2}},
            }
            with receipt_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(second) + "\n")

            routing = {
                "dispatch_payload_budgets": {
                    "codex": {"actual_reference_tokens": 605}
                }
            }
            budget = {
                "schema_version": "valp-iteration-budget.v1",
                "task_id": task_id,
                "max_dispatch_reference_tokens": 2000,
                "max_dispatches": 3,
                "max_reroutes": 1,
                "max_fix_review_rounds": 3,
                "usage": {
                    "dispatch_reference_tokens": 0,
                    "dispatches": 0,
                    "reroutes": 0,
                    "fix_review_rounds": 0,
                },
                "status": "active",
                "stop_reason": None,
            }

            refreshed = workflow_module.refresh_iteration_budget(
                directory,
                routing,
                budget,
            )

            self.assertEqual(refreshed["usage"]["dispatches"], 2)
            self.assertEqual(refreshed["usage"]["dispatch_reference_tokens"], 1210)


if __name__ == "__main__":
    unittest.main()
