from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.schema_helpers import schema_validator
from valp_cli.audit import FAIL, PASS, TaskAudit
from valp_cli.model_identity import (
    model_aware_provider_errors,
    model_aware_role_errors,
    model_identity_for,
)
from valp_cli.workflow import (
    collect_herdr_preflight,
    collect_queue_preflight,
    dynamic_model_dispatch_errors,
    model_probe_from_runtime_metadata,
    publish_task,
    provider_matrix_for,
    read_json,
    route_task,
    score_candidates,
)


ROOT = Path(__file__).resolve().parents[1]


class ModelAwareRoutingTests(unittest.TestCase):
    def test_adapter_visible_metadata_produces_non_secret_model_probe(self) -> None:
        probe = model_probe_from_runtime_metadata(
            "codex",
            {
                "pane_id": "pane-7",
                "terminal_id": "terminal-9",
                "generation": 4,
                "model_id": "model-live",
                "provider": "provider-live",
                "reasoning_mode": "high",
            },
            source="HERDR pane metadata",
            observed_at="2026-07-15T12:00:00Z",
        )

        self.assertEqual(probe["status"], "observed")
        self.assertEqual(probe["model"]["model_id"], "model-live")
        self.assertEqual(probe["session_identity"]["status"], "known")
        self.assertTrue(probe["session_identity"]["token"].startswith("sha256:"))
        self.assertNotIn("terminal-9", json.dumps(probe))

    def test_adapter_probe_does_not_treat_generic_pane_identity_as_model(self) -> None:
        probe = model_probe_from_runtime_metadata(
            "codex",
            {
                "id": "pane-record-7",
                "name": "codex-worker-pane",
                "pane_id": "pane-7",
                "terminal_id": "terminal-9",
            },
            source="HERDR pane metadata",
            observed_at="2026-07-15T12:00:00Z",
        )

        self.assertEqual(probe["status"], "unsupported")
        self.assertEqual(probe["model"]["model_id"], "unknown")

    def test_herdr_preflight_consumes_public_model_probe_instead_of_pane_metadata(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "herdr agent start <name> -- <argv...>\nherdr agent prompt <target> <text>\nherdr agent wait <target>",
                    "stderr": "",
                }
            if command[1:] == ["workspace", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "herdr workspace create [--cwd PATH] [--no-focus]",
                    "stderr": "",
                }
            if command[1:] == ["pane", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "herdr pane move <pane> --new-tab\nherdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>",
                    "stderr": "",
                }
            if command[1:] == ["status", "--json"]:
                payload = {"client": {"version": "1"}, "server": {"version": "1"}}
            elif command[1:] == ["pane", "list"]:
                payload = {
                    "result": {
                        "panes": [
                            {
                                "agent": "codex",
                                "pane_id": "pane-7",
                                "terminal_id": "terminal-9",
                                "generation": 4,
                                "model_id": "pane-spoofed",
                                "provider": "pane-spoofed",
                                "reasoning_mode": "pane-spoofed",
                            }
                        ]
                    }
                }
            elif command[1:3] == ["pane", "layout"]:
                payload = {
                    "result": {
                        "layout": {
                            "panes": [
                                {
                                    "pane_id": "pane-7",
                                    "rect": {"width": 100, "height": 40},
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
                            "detected_agent": "codex",
                            "agent_status": "idle",
                            "interactive_ready": True,
                            "prompt_eligible": True,
                            "session_identity": {
                                "status": "known",
                                "identity": {
                                    "source": "herdr:codex",
                                    "agent": "codex",
                                    "kind": "id",
                                    "value": "session:1234",
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
                            "source": "herdr:codex",
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                            "ttl_seconds": 3600,
                            "model": {
                                "model_id": "model-live",
                                "provider": "provider-live",
                                "reasoning_mode": "high",
                                "confidence": "high",
                            },
                            "session_identity": {
                                "status": "known",
                                "token": "sha256:session",
                                "source": "herdr:codex",
                                "generation": "session:1234",
                            },
                        },
                    }
                }
            else:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "codex-cli test",
                    "stderr": "",
                }
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps(payload),
                "stderr": "",
            }

        with patch("valp_cli.workflow.shutil.which", side_effect=lambda name: f"/test/{name}"):
            with patch("valp_cli.workflow.run_command", side_effect=fake_run):
                preflight = collect_herdr_preflight(
                    ["codex"],
                    launch_argv_by_agent={"codex": ["codex"]},
                    version_command_by_agent={"codex": ["codex", "version"]},
                )

        probe = preflight["agents"]["codex"]["model_probe"]
        self.assertEqual(preflight["status"], "pass", preflight)
        self.assertEqual(probe["status"], "observed")
        self.assertEqual(probe["model"]["model_id"], "model-live")
        self.assertEqual(probe["session_identity"]["status"], "known")

    def test_herdr_preflight_does_not_treat_visible_text_as_model_evidence(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["status", "--json"]:
                payload = {"client": {"version": "1"}, "server": {"version": "1"}}
            elif command[1:] == ["pane", "list"]:
                payload = {
                    "result": {
                        "panes": [
                            {
                                "agent": "codex",
                                "pane_id": "pane-7",
                                "terminal_id": "terminal-private-9",
                                "agent_status": "idle",
                            }
                        ]
                    }
                }
            elif command[1:3] == ["pane", "layout"]:
                payload = {
                    "result": {
                        "layout": {
                            "panes": [
                                {
                                    "pane_id": "pane-7",
                                    "rect": {"width": 100, "height": 40},
                                }
                            ]
                        }
                    }
                }
            elif command[1:3] == ["pane", "process-info"]:
                payload = {
                    "result": {
                        "process_info": {
                            "foreground_process_group_id": 4321,
                            "foreground_processes": [{"pid": 4321}],
                        }
                    }
                }
            elif command[1:3] == ["pane", "read"]:
                raise AssertionError("unstructured pane text must not be read for model evidence")
            else:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "codex-cli test",
                    "stderr": "",
                }
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps(payload),
                "stderr": "",
            }

        with patch("valp_cli.workflow.shutil.which", side_effect=lambda name: f"/test/{name}"):
            with patch("valp_cli.workflow.run_command", side_effect=fake_run):
                preflight = collect_herdr_preflight(["codex"])

        probe = preflight["agents"]["codex"]["model_probe"]
        self.assertEqual(probe["status"], "unavailable")
        self.assertIsNone(probe["model"])
        self.assertIsNone(probe["session_identity"])

    def test_herdr_preflight_does_not_derive_session_identity_from_process_changes(self) -> None:
        foreground_pid = 4321

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["status", "--json"]:
                payload = {"client": {"version": "1"}, "server": {"version": "1"}}
            elif command[1:] == ["pane", "list"]:
                payload = {
                    "result": {
                        "panes": [
                            {
                                "agent": "codex",
                                "pane_id": "pane-7",
                                "terminal_id": "terminal-9",
                                "agent_status": "idle",
                            }
                        ]
                    }
                }
            elif command[1:3] == ["pane", "layout"]:
                payload = {
                    "result": {
                        "layout": {
                            "panes": [
                                {
                                    "pane_id": "pane-7",
                                    "rect": {"width": 100, "height": 40},
                                }
                            ]
                        }
                    }
                }
            elif command[1:3] == ["pane", "process-info"]:
                payload = {
                    "result": {
                        "process_info": {
                            "foreground_process_group_id": foreground_pid,
                            "foreground_processes": [{"pid": foreground_pid}],
                        }
                    }
                }
            elif command[1:3] == ["pane", "read"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "› Prompt\n\n  example-model-a xhigh · ~\n",
                    "stderr": "",
                }
            else:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "codex-cli test",
                    "stderr": "",
                }
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": json.dumps(payload),
                "stderr": "",
            }

        with patch("valp_cli.workflow.shutil.which", side_effect=lambda name: f"/test/{name}"):
            with patch("valp_cli.workflow.run_command", side_effect=fake_run):
                first = collect_herdr_preflight(["codex"])
                foreground_pid = 9876
                second = collect_herdr_preflight(["codex"])

        first_probe = first["agents"]["codex"]["model_probe"]
        second_probe = second["agents"]["codex"]["model_probe"]
        serialized = json.dumps([first, second])
        self.assertEqual(first_probe["status"], "unavailable")
        self.assertEqual(second_probe["status"], "unavailable")
        self.assertNotIn("4321", serialized)
        self.assertNotIn("9876", serialized)

    def test_probe_unsupported_adapter_records_closed_result(self) -> None:
        preflight = collect_queue_preflight(["codex"])
        probe = preflight["agents"]["codex"]["model_probe"]

        self.assertEqual(probe["schema_version"], "valp-model-probe.v1")
        self.assertEqual(probe["status"], "unsupported")
        self.assertEqual(probe["model"]["model_id"], "unknown")
        self.assertEqual(probe["session_identity"]["status"], "unknown")

    def test_unknown_model_cannot_receive_high_risk_roles(self) -> None:
        agents = {
            "codex": {
                "active": True,
                "role": ["implementation", "verification", "code_review"],
                "model_identity": {
                    "declared_model": {
                        "model_id": "declared-model",
                        "confidence": "high",
                    }
                },
            }
        }
        preflight = collect_queue_preflight(["codex"])
        scores = score_candidates(
            "software-code",
            agents,
            runtime_preflight=preflight,
            enforce_model_role_gate=True,
            evaluated_at="2026-07-15T12:05:00Z",
        )
        self.assertEqual(scores["codex"]["model_role_gate"]["status"], "blocked")
        self.assertEqual(scores["codex"]["role_fit"]["implementer"], 0.0)
        self.assertEqual(scores["codex"]["role_fit"]["reviewer"], 0.0)
        self.assertEqual(
            scores["codex"]["model_role_gate"]["fallback_roles"],
            ["discovery", "prototype", "manual"],
        )

    def test_route_blocks_leader_declared_high_risk_assignment_when_probe_is_unsupported(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-15T12:00:00Z",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["coordination", "implementation", "verification", "code_review"],
                    "model_identity": {
                        "declared_model": {
                            "model_id": "declared-model",
                            "confidence": "high",
                        }
                    },
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(
                root,
                "TASK-UNKNOWN-MODEL-GATE",
                "Implement and review a source change.",
                profile="software-code",
            )
            declaration = {
                "schema_version": "valp-assignment-declaration.v1",
                "declaration_id": "decl-unknown-model-1",
                "task_id": "TASK-UNKNOWN-MODEL-GATE",
                "declared_at": "2026-07-15T12:00:00Z",
                "leader": {
                    "agent_id": "codex",
                    "selected_by": "user",
                    "selection_ref": "user-message:codex-leader",
                },
                "assignments": {
                    "coordinator": "codex",
                    "implementer": "codex",
                    "reviewer": "codex",
                },
                "reasons": {
                    "coordinator": "User-selected Leader.",
                    "implementer": "Leader-declared implementer.",
                    "reviewer": "Leader-declared reviewer.",
                },
            }
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities), \
                    patch("valp_cli.workflow.skill_router_command", return_value=None):
                with self.assertRaisesRegex(SystemExit, "Assignment validation blocked"):
                    route_task(
                        root,
                        "TASK-UNKNOWN-MODEL-GATE",
                        runtime="queue",
                        assignment_declaration=declaration,
                    )

            validation = read_json(task_dir / "assignment-validation.json")
            state = read_json(task_dir / "state.json")

        self.assertEqual(validation["status"], "blocked")
        self.assertEqual(
            validation["blockers"],
            ["active_model_identity:implementer:codex", "active_model_identity:reviewer:codex"],
        )
        self.assertEqual(state["status"], "blocked")
        self.assertFalse((task_dir / "routing.json").exists())
        self.assertFalse((task_dir / "dispatch-receipts.jsonl").exists())

    def test_route_enforces_observed_models_without_static_model_identity(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-15T12:00:00Z",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["implementation", "verification"],
                },
                "claude": {
                    "active": True,
                    "role": ["reviewer", "code_review"],
                },
            },
        }

        def probe(model_id: str, provider: str, token: str) -> dict[str, object]:
            return {
                "schema_version": "valp-model-probe.v1",
                "status": "observed",
                "source": "HERDR pane adapter metadata",
                "observed_at": "2026-07-15T12:00:00Z",
                "ttl_seconds": 3600,
                "model": {
                    "model_id": model_id,
                    "provider": provider,
                    "reasoning_mode": "high",
                    "confidence": "high",
                },
                "session_identity": {
                    "status": "known",
                    "token": token,
                    "source": "HERDR pane session metadata",
                    "generation": "3",
                },
            }

        preflight = {
            "status": "pass",
            "runtime": "herdr",
            "adapter_class": "herdr",
            "checks": {},
            "agents": {
                "codex": {"status": "pass", "model_probe": probe("implementation-model", "relay-a", "sha256:codex-session")},
                "claude": {"status": "pass", "model_probe": probe("review-model", "relay-b", "sha256:claude-session")},
            },
        }
        declaration = {
            "schema_version": "valp-assignment-declaration.v1",
            "declaration_id": "decl-observed-models-1",
            "task_id": "TASK-OBSERVED-MODEL-GATE",
            "declared_at": "2026-07-15T12:00:00Z",
            "leader": {
                "agent_id": "codex-app",
                "selected_by": "user",
                "selection_ref": "user-message:codex-app-leader",
            },
            "assignments": {"implementer": "codex", "reviewer": "claude"},
            "reasons": {
                "implementer": "Leader-declared implementer.",
                "reviewer": "Leader-declared reviewer.",
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(
                root,
                "TASK-OBSERVED-MODEL-GATE",
                "Implement and independently review a source change.",
                profile="software-code",
            )
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities), \
                    patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight), \
                    patch("valp_cli.workflow.skill_router_command", return_value=None), \
                    patch("valp_cli.workflow.now_iso", return_value="2026-07-15T12:05:00Z"):
                routing = route_task(
                    root,
                    "TASK-OBSERVED-MODEL-GATE",
                    runtime="herdr",
                    assignment_declaration=declaration,
                )

            validation = read_json(task_dir / "assignment-validation.json")

        self.assertEqual(validation["status"], "pass")
        self.assertTrue(routing["model_role_gate"]["enforced"])
        self.assertEqual(routing["provider_matrix"]["model_awareness"]["status"], "strong")
        self.assertEqual(
            routing["provider_matrix"]["providers"]["codex"]["model_identity"]["observed_model"]["model_id"],
            "implementation-model",
        )
        self.assertEqual(
            routing["provider_matrix"]["providers"]["claude"]["model_identity"]["observed_model"]["provider"],
            "relay-b",
        )

    def test_runtime_observation_freshness_expires_with_fake_clock(self) -> None:
        info = {
            "model_identity": {
                "declared_model": {
                    "model_id": "model-a",
                    "provider": "provider-a",
                    "reasoning_mode": "high",
                    "confidence": "high",
                }
            }
        }
        probe = {
            "schema_version": "valp-model-probe.v1",
            "status": "observed",
            "source": "test adapter metadata",
            "observed_at": "2026-07-15T12:00:00Z",
            "ttl_seconds": 3600,
            "model": {
                "model_id": "model-a",
                "provider": "provider-a",
                "reasoning_mode": "high",
                "confidence": "high",
            },
            "session_identity": {
                "status": "known",
                "token": "sha256:session-a",
                "source": "test adapter generation",
                "generation": "7",
            },
        }

        current = model_identity_for(
            "codex",
            info,
            {},
            runtime_probe=probe,
            evaluated_at="2026-07-15T12:59:59Z",
        )
        expired = model_identity_for(
            "codex",
            info,
            {},
            runtime_probe=probe,
            evaluated_at="2026-07-15T13:00:01Z",
        )

        self.assertEqual(current["observed_model"]["freshness"], "current")
        self.assertEqual(current["observation_age_seconds"], 3599)
        self.assertEqual(expired["observed_model"]["freshness"], "stale")
        self.assertEqual(expired["history_status"], "invalidated")
        self.assertEqual(expired["role_eligibility"]["implementer"], "blocked")

    def test_session_token_change_invalidates_model_bound_history(self) -> None:
        info = {
            "model_identity": {
                "declared_model": {
                    "model_id": "model-a",
                    "provider": "provider-a",
                    "reasoning_mode": "high",
                    "confidence": "high",
                }
            }
        }

        def probe(token: str) -> dict[str, object]:
            return {
                "schema_version": "valp-model-probe.v1",
                "status": "observed",
                "source": "test adapter metadata",
                "observed_at": "2026-07-15T12:00:00Z",
                "ttl_seconds": 3600,
                "model": {
                    "model_id": "model-a",
                    "provider": "provider-a",
                    "reasoning_mode": "high",
                    "confidence": "high",
                },
                "session_identity": {
                    "status": "known",
                    "token": token,
                    "source": "test adapter generation",
                    "generation": "7",
                },
            }

        first = model_identity_for(
            "codex",
            info,
            {},
            runtime_probe=probe("sha256:session-a"),
            evaluated_at="2026-07-15T12:01:00Z",
        )
        info["model_identity"]["history_binding"] = first["history_binding"]
        changed = model_identity_for(
            "codex",
            info,
            {},
            runtime_probe=probe("sha256:session-b"),
            evaluated_at="2026-07-15T12:02:00Z",
        )

        self.assertEqual(changed["observed_model"]["model_id"], "model-a")
        self.assertEqual(changed["history_status"], "invalidated")
        self.assertIn("model-bound history binding changed", changed["history_invalidation_reasons"])

    def test_dispatch_blocks_session_change_or_ttl_expiry_after_routing(self) -> None:
        agents = {
            "codex": {
                "active": True,
                "model_identity": {
                    "declared_model": {
                        "model_id": "model-a",
                        "provider": "provider-a",
                        "reasoning_mode": "high",
                        "confidence": "high",
                    }
                },
            }
        }

        def preflight(token: str) -> dict[str, object]:
            return {
                "status": "pass",
                "agents": {
                    "codex": {
                        "status": "pass",
                        "model_probe": {
                            "schema_version": "valp-model-probe.v1",
                            "status": "observed",
                            "source": "test adapter metadata",
                            "observed_at": "2026-07-15T12:00:00Z",
                            "ttl_seconds": 3600,
                            "model": {
                                "model_id": "model-a",
                                "provider": "provider-a",
                                "reasoning_mode": "high",
                                "confidence": "high",
                            },
                            "session_identity": {
                                "status": "known",
                                "token": token,
                                "source": "test adapter generation",
                                "generation": "7",
                            },
                        },
                    }
                },
            }

        routing = {
            "provider_matrix": provider_matrix_for(
                ["codex"],
                agents,
                {},
                preflight("sha256:session-a"),
                evaluated_at="2026-07-15T12:01:00Z",
            )
        }
        session_errors = dynamic_model_dispatch_errors(
            routing,
            agents,
            {},
            preflight("sha256:session-b"),
            [("codex", "implementer")],
            evaluated_at="2026-07-15T12:02:00Z",
        )
        ttl_errors = dynamic_model_dispatch_errors(
            routing,
            agents,
            {},
            preflight("sha256:session-a"),
            [("codex", "implementer")],
            evaluated_at="2026-07-15T13:00:01Z",
        )
        owned_rebinding_errors = dynamic_model_dispatch_errors(
            routing,
            agents,
            {},
            preflight("sha256:session-b"),
            [("codex", "implementer")],
            evaluated_at="2026-07-15T12:02:00Z",
            allow_session_rebinding=True,
        )
        expired_owned_rebinding_errors = dynamic_model_dispatch_errors(
            routing,
            agents,
            {},
            preflight("sha256:session-b"),
            [("codex", "implementer")],
            evaluated_at="2026-07-15T13:00:01Z",
            allow_session_rebinding=True,
        )

        self.assertTrue(any("binding changed" in error for error in session_errors), session_errors)
        self.assertTrue(any("not eligible" in error for error in ttl_errors), ttl_errors)
        self.assertEqual(owned_rebinding_errors, [])
        self.assertTrue(
            any("not eligible" in error for error in expired_owned_rebinding_errors),
            expired_owned_rebinding_errors,
        )

    def test_active_model_provider_or_reasoning_change_invalidates_history(self) -> None:
        info = {
            "model_identity": {
                "declared_model": {
                    "model_id": "model-a",
                    "provider": "provider-a",
                    "reasoning_mode": "high",
                    "confidence": "high",
                }
            }
        }

        def probe(model_id: str, provider: str, reasoning_mode: str) -> dict[str, object]:
            return {
                "schema_version": "valp-model-probe.v1",
                "status": "observed",
                "source": "test adapter metadata",
                "observed_at": "2026-07-15T12:00:00Z",
                "ttl_seconds": 3600,
                "model": {
                    "model_id": model_id,
                    "provider": provider,
                    "reasoning_mode": reasoning_mode,
                    "confidence": "high",
                },
                "session_identity": {
                    "status": "known",
                    "token": "sha256:session-a",
                    "source": "test adapter generation",
                    "generation": "7",
                },
            }

        first = model_identity_for(
            "codex",
            info,
            {},
            runtime_probe=probe("model-a", "provider-a", "high"),
            evaluated_at="2026-07-15T12:01:00Z",
        )
        info["model_identity"]["history_binding"] = first["history_binding"]

        for label, values in {
            "model": ("model-b", "provider-a", "high"),
            "provider": ("model-a", "provider-b", "high"),
            "reasoning": ("model-a", "provider-a", "low"),
        }.items():
            with self.subTest(change=label):
                changed = model_identity_for(
                    "codex",
                    info,
                    {},
                    runtime_probe=probe(*values),
                    evaluated_at="2026-07-15T12:02:00Z",
                )
                self.assertEqual(changed["mismatch"]["status"], "mismatch")
                self.assertEqual(changed["history_status"], "invalidated")
                self.assertIn("model-bound history binding changed", changed["history_invalidation_reasons"])

    def test_provider_matrix_uses_supported_runtime_probe(self) -> None:
        info = {
            "active": True,
            "model_identity": {
                "declared_model": {
                    "model_id": "model-live",
                    "provider": "provider-live",
                    "reasoning_mode": "high",
                    "confidence": "high",
                },
                "observed_model": {
                    "model_id": "stale-static-model",
                    "provider": "provider-static",
                    "reasoning_mode": "low",
                    "timestamp": "2026-07-01T00:00:00Z",
                    "confidence": "high",
                    "freshness": "current",
                },
            },
        }
        preflight = {
            "status": "pass",
            "agents": {
                "codex": {
                    "status": "pass",
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "observed",
                        "source": "HERDR adapter metadata",
                        "observed_at": "2026-07-15T12:00:00Z",
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "model-live",
                            "provider": "provider-live",
                            "reasoning_mode": "high",
                            "confidence": "high",
                        },
                        "session_identity": {
                            "status": "known",
                            "token": "sha256:live-session",
                            "source": "HERDR adapter generation",
                            "generation": "9",
                        },
                    },
                }
            },
        }

        matrix = provider_matrix_for(
            ["codex"],
            {"codex": info},
            {},
            preflight,
            evaluated_at="2026-07-15T12:05:00Z",
        )
        identity = matrix["providers"]["codex"]["model_identity"]

        self.assertEqual(identity["observed_model"]["model_id"], "model-live")
        self.assertEqual(identity["model_probe"]["status"], "observed")
        self.assertEqual(identity["role_eligibility"]["implementer"], "eligible")
        errors = list(
            schema_validator(ROOT / "schemas" / "provider-matrix-model-aware.schema.json").iter_errors(matrix)
        )
        self.assertEqual(errors, [])

    def test_runtime_observation_is_authoritative_when_no_model_was_declared(self) -> None:
        info = {
            "active": True,
            "role": ["reviewer"],
            "model_identity": {"agent_surface": "review_cli"},
        }
        preflight = {
            "status": "pass",
            "agents": {
                "reviewer": {
                    "status": "pass",
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "observed",
                        "source": "runtime adapter metadata",
                        "observed_at": "2026-07-15T12:00:00Z",
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "non-native-review-model",
                            "provider": "relay-provider",
                            "reasoning_mode": "unknown",
                            "confidence": "high",
                        },
                        "session_identity": {
                            "status": "known",
                            "token": "sha256:review-session",
                            "source": "runtime session metadata",
                            "generation": "unknown",
                        },
                    },
                }
            },
        }

        matrix = provider_matrix_for(
            ["reviewer"],
            {"reviewer": info},
            {},
            preflight,
            evaluated_at="2026-07-15T12:05:00Z",
        )
        identity = matrix["providers"]["reviewer"]["model_identity"]

        self.assertEqual(identity["declared_model"]["model_id"], "unknown")
        self.assertEqual(identity["observed_model"]["model_id"], "non-native-review-model")
        self.assertEqual(identity["mismatch"]["status"], "not_applicable")
        self.assertEqual(identity["evidence_status"], "strong")
        self.assertEqual(identity["role_eligibility"]["final_reviewer"], "eligible")
        with patch("valp_cli.model_identity._now", return_value="2026-07-15T12:05:00Z"):
            self.assertEqual(model_aware_provider_errors(matrix), [])
        errors = list(
            schema_validator(ROOT / "schemas" / "provider-matrix-model-aware.schema.json").iter_errors(matrix)
        )
        self.assertEqual(errors, [])

    def test_provider_matrix_preserves_observed_probe_when_gate_is_advisory(self) -> None:
        preflight = {
            "status": "pass",
            "agents": {
                "reviewer": {
                    "status": "pass",
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "observed",
                        "source": "runtime adapter metadata",
                        "observed_at": "2026-07-15T12:00:00Z",
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "relay-review-model",
                            "provider": "relay-provider",
                            "reasoning_mode": "high",
                            "confidence": "high",
                        },
                        "session_identity": {
                            "status": "known",
                            "token": "sha256:review-session",
                            "source": "runtime session metadata",
                            "generation": "4",
                        },
                    },
                }
            },
        }

        matrix = provider_matrix_for(
            ["reviewer"],
            {"reviewer": {"active": True, "role": ["reviewer"]}},
            {},
            preflight,
            evaluated_at="2026-07-15T12:05:00Z",
            dynamic_discovery_required=False,
        )
        identity = matrix["providers"]["reviewer"]["model_identity"]

        self.assertFalse(matrix["model_awareness"]["dynamic_discovery_required"])
        self.assertEqual(identity["observed_model"]["model_id"], "relay-review-model")
        self.assertEqual(identity["observed_model"]["provider"], "relay-provider")
        self.assertEqual(identity["observed_model"]["reasoning_mode"], "high")
        self.assertEqual(identity["model_probe"]["session_identity"]["token"], "sha256:review-session")

    def test_candidate_scoring_preserves_runtime_probe_without_enforcing_gate(self) -> None:
        preflight = {
            "agents": {
                "codex": {
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "observed",
                        "source": "runtime adapter metadata",
                        "observed_at": "2026-07-15T12:00:00Z",
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "implementation-model",
                            "provider": "relay-provider",
                            "reasoning_mode": "xhigh",
                            "confidence": "high",
                        },
                        "session_identity": {
                            "status": "known",
                            "token": "sha256:implementation-session",
                            "source": "runtime session metadata",
                            "generation": "8",
                        },
                    }
                }
            }
        }

        scores = score_candidates(
            "software-code",
            {"codex": {"active": True, "role": ["implementation"]}},
            runtime_preflight=preflight,
            enforce_model_role_gate=False,
            evaluated_at="2026-07-15T12:05:00Z",
        )

        self.assertFalse(scores["codex"]["model_role_gate"]["enforced"])
        self.assertEqual(scores["codex"]["model_evidence"]["observed_model"], "implementation-model")
        self.assertEqual(scores["codex"]["model_evidence"]["probe_status"], "observed")
        self.assertEqual(scores["codex"]["model_evidence"]["session_status"], "known")

    def test_provider_matrix_fails_closed_for_unsupported_runtime_probe(self) -> None:
        info = {
            "active": True,
            "model_identity": {
                "declared_model": {
                    "model_id": "declared-model",
                    "confidence": "high",
                },
                "observed_model": {
                    "model_id": "static-model",
                    "timestamp": "2026-07-15T12:00:00Z",
                    "confidence": "high",
                    "freshness": "current",
                },
            },
        }
        preflight = {
            "status": "pass",
            "agents": {
                "codex": {
                    "status": "pass",
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "unsupported",
                        "source": "queue adapter metadata",
                        "observed_at": "2026-07-15T12:05:00Z",
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "unknown",
                            "provider": "unknown",
                            "reasoning_mode": "unknown",
                            "confidence": "unknown",
                        },
                        "session_identity": {
                            "status": "unknown",
                            "token": "unknown",
                            "source": "queue adapter metadata",
                            "generation": "unknown",
                        },
                    },
                }
            },
        }

        matrix = provider_matrix_for(
            ["codex"],
            {"codex": info},
            {},
            preflight,
            evaluated_at="2026-07-15T12:05:00Z",
        )
        identity = matrix["providers"]["codex"]["model_identity"]

        self.assertEqual(identity["observed_model"]["model_id"], "unknown")
        self.assertEqual(identity["observed_model"]["freshness"], "unknown")
        self.assertEqual(identity["evidence_status"], "unknown")
        self.assertEqual(identity["role_eligibility"]["final_reviewer"], "blocked")

    def test_provider_matrix_keeps_declared_and_observed_models_separate(self) -> None:
        info = {
            "active": True,
            "role": ["implementation"],
            "model_identity": {
                "agent_surface": "codex_cli",
                "provider": "example-provider-a",
                "permissions": ["task-local-evidence"],
                "declared_model": {
                    "model_id": "example-model-a",
                    "reasoning_mode": "xhigh",
                    "source": "Codex declaration",
                    "timestamp": "2026-07-15T12:24:47Z",
                    "confidence": "high",
                    "freshness": "current",
                },
                "observed_model": {
                    "model_id": "example-model-b",
                    "reasoning_mode": "high",
                    "source": "HERDR pane",
                    "timestamp": "2026-07-15T12:24:47Z",
                    "confidence": "high",
                    "freshness": "current",
                },
            },
        }
        matrix = provider_matrix_for(
            ["codex"],
            {"codex": info},
            {},
            {"status": "pass", "agents": {"codex": {"status": "pass", "cli": {"status": "pass", "version_output": "codex"}}}},
        )
        provider = matrix["providers"]["codex"]
        identity = provider["model_identity"]
        self.assertEqual(identity["declared_model"]["model_id"], "example-model-a")
        self.assertEqual(identity["observed_model"]["model_id"], "example-model-b")
        self.assertEqual(identity["mismatch"]["status"], "mismatch")
        self.assertEqual(identity["history_status"], "invalidated")
        self.assertEqual(provider["model_selection"], "runtime_observed")
        self.assertEqual(matrix["model_awareness"]["status"], "degraded")

    def test_unknown_model_is_explicit_and_downgrades_candidate_evidence(self) -> None:
        info = {"active": True, "role": ["reviewer"], "model_identity": {"agent_surface": "claude"}}
        identity = model_identity_for("claude", info, {})
        self.assertEqual(identity["observed_model"]["model_id"], "unknown")
        self.assertEqual(identity["evidence_status"], "unknown")
        scores = score_candidates("software-code", {"claude": info}, [])
        self.assertEqual(scores["claude"]["model_evidence"]["status"], "unknown")
        self.assertNotEqual(scores["claude"]["confidence"], "high")

    def test_runtime_default_alone_fails_model_aware_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            (task / "task.md").write_text("# Task\n\n## Goal\n\nImplement.\n", encoding="utf-8")
            routing = {
                "provider_matrix": {
                    "model_awareness": {"required": True, "status": "strong"},
                    "providers": {
                        "codex": {
                            "provider_name": "codex",
                            "model_selection": "runtime_default",
                        }
                    },
                }
            }
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")
            (task / "state.json").write_text("{}", encoding="utf-8")
            result = TaskAudit(task).check_provider_matrix()
            self.assertEqual(result.status, FAIL)
            self.assertIn("runtime_default", result.message)

    def test_audit_rejects_unknown_model_high_risk_assignment(self) -> None:
        matrix = provider_matrix_for(
            ["codex"],
            {
                "codex": {
                    "active": True,
                    "model_identity": {
                        "declared_model": {
                            "model_id": "declared-model",
                            "confidence": "high",
                        }
                    },
                }
            },
            {},
            collect_queue_preflight(["codex"]),
            evaluated_at="2026-07-15T12:05:00Z",
        )
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            (task / "task.md").write_text("# Task\n\n## Goal\n\nImplement.\n", encoding="utf-8")
            (task / "routing.json").write_text(
                json.dumps(
                    {
                        "role_assignments": {"implementer": "codex"},
                        "provider_matrix": matrix,
                    }
                ),
                encoding="utf-8",
            )
            (task / "state.json").write_text("{}", encoding="utf-8")

            result = TaskAudit(task).check_provider_matrix()

        self.assertEqual(result.status, FAIL)
        self.assertIn("implementer", result.message)
        self.assertIn("active model identity", result.message)

    def test_audit_rejects_tampered_model_history_binding(self) -> None:
        probe = {
            "schema_version": "valp-model-probe.v1",
            "status": "observed",
            "source": "test adapter metadata",
            "observed_at": "2026-07-15T12:00:00Z",
            "ttl_seconds": 86400,
            "model": {
                "model_id": "model-a",
                "provider": "provider-a",
                "reasoning_mode": "high",
                "confidence": "high",
            },
            "session_identity": {
                "status": "known",
                "token": "sha256:session-a",
                "source": "test adapter generation",
                "generation": "7",
            },
        }
        identity = model_identity_for(
            "codex",
            {
                "model_identity": {
                    "declared_model": {
                        "model_id": "model-a",
                        "provider": "provider-a",
                        "reasoning_mode": "high",
                        "confidence": "high",
                    }
                }
            },
            {},
            runtime_probe=probe,
            evaluated_at="2026-07-15T12:01:00Z",
        )
        identity["history_binding"]["fingerprint"] = "0" * 64
        matrix = {
            "model_awareness": {
                "required": True,
                "dynamic_discovery_required": True,
                "status": "strong",
            },
            "providers": {
                "codex": {
                    "model_selection": "observed_model",
                    "model_identity": identity,
                }
            },
        }

        errors = model_aware_provider_errors(matrix)

        self.assertTrue(any("history binding fingerprint" in error for error in errors), errors)

    def test_audit_uses_recorded_done_time_for_model_probe_freshness(self) -> None:
        probe = {
            "schema_version": "valp-model-probe.v1",
            "status": "observed",
            "source": "test adapter metadata",
            "observed_at": "2026-07-15T12:00:00Z",
            "ttl_seconds": 3600,
            "model": {
                "model_id": "model-a",
                "provider": "provider-a",
                "reasoning_mode": "high",
                "confidence": "high",
            },
            "session_identity": {
                "status": "known",
                "token": "sha256:session-a",
                "source": "test adapter generation",
                "generation": "7",
            },
        }
        identity = model_identity_for(
            "codex",
            {"model_identity": {"declared_model": {**probe["model"]}}},
            {},
            runtime_probe=probe,
            evaluated_at="2026-07-15T12:01:00Z",
        )
        matrix = {
            "model_awareness": {
                "required": True,
                "dynamic_discovery_required": True,
                "status": "strong",
            },
            "providers": {"codex": {"model_selection": "observed_model", "model_identity": identity}},
        }
        routing = {"role_assignments": {"implementer": "codex"}, "provider_matrix": matrix}

        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            (task / "routing.json").write_text(json.dumps(routing), encoding="utf-8")
            (task / "state.json").write_text(
                json.dumps({"status": "done", "updated_at": "2026-07-15T12:02:00Z"}),
                encoding="utf-8",
            )
            self.assertEqual(TaskAudit(task).check_provider_matrix().status, PASS)
            self.assertEqual(
                model_aware_role_errors(
                    matrix,
                    {"reviewer": "codex"},
                    evaluated_at="2026-07-15T12:02:00Z",
                ),
                [],
            )
            self.assertIn(
                "reviewer:codex",
                " ".join(
                    model_aware_role_errors(
                        matrix,
                        {"reviewer": "codex"},
                        evaluated_at="2026-07-15T14:00:00Z",
                    )
                ),
            )

            (task / "state.json").write_text(json.dumps({"status": "dispatching"}), encoding="utf-8")
            result = TaskAudit(task).check_provider_matrix()

        self.assertEqual(result.status, FAIL)
        self.assertIn("freshness", result.message)

    def test_iteration_budget_allows_three_fix_review_rounds(self) -> None:
        from valp_cli.workflow import iteration_budget_for

        budget = iteration_budget_for("MODEL-001", {"implementer": "codex"})
        self.assertEqual(budget["max_fix_review_rounds"], 3)
