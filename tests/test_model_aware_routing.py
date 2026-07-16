from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.schema_helpers import schema_validator
from valp_cli.audit import FAIL, TaskAudit
from valp_cli.model_identity import model_aware_provider_errors, model_identity_for
from valp_cli.workflow import (
    collect_herdr_preflight,
    collect_queue_preflight,
    dynamic_model_dispatch_errors,
    model_probe_from_runtime_metadata,
    publish_task,
    provider_matrix_for,
    read_json,
    role_assignments_for,
    score_candidates,
    select_agents,
    visible_model_metadata_for_agent,
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

    def test_herdr_preflight_emits_supported_model_probe_from_pane_metadata(self) -> None:
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
                                "generation": 4,
                                "model_id": "model-live",
                                "provider": "provider-live",
                                "reasoning_mode": "high",
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
        self.assertEqual(preflight["status"], "pass")
        self.assertEqual(probe["status"], "observed")
        self.assertEqual(probe["model"]["model_id"], "model-live")
        self.assertEqual(probe["session_identity"]["status"], "known")

    def test_herdr_preflight_observes_codex_model_from_visible_footer(self) -> None:
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
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "private conversation text\n\n› Prompt\n\n  gpt-5.6-sol xhigh · ~\n",
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
                preflight = collect_herdr_preflight(["codex"])

        probe = preflight["agents"]["codex"]["model_probe"]
        serialized = json.dumps(probe)
        self.assertEqual(probe["status"], "observed")
        self.assertEqual(probe["model"]["model_id"], "gpt-5.6-sol")
        self.assertEqual(probe["model"]["reasoning_mode"], "xhigh")
        self.assertEqual(probe["session_identity"]["status"], "known")
        self.assertNotIn("terminal-private-9", serialized)
        self.assertNotIn("4321", serialized)
        self.assertNotIn("private conversation text", serialized)

    def test_visible_model_metadata_parses_claude_footer(self) -> None:
        metadata = visible_model_metadata_for_agent(
            "claude",
            "conversation mentions deepseek-v4-pro\n"
            "[PONYTAIL] deepseek-v4-pro ░░░░ 4%  in:44,77…\n"
            "bypass permissions on\n",
        )

        self.assertEqual(
            metadata,
            {"model_id": "deepseek-v4-pro", "provider": "PONYTAIL"},
        )

    def test_visible_model_metadata_parses_hermes_footer(self) -> None:
        metadata = visible_model_metadata_for_agent(
            "hermes",
            "conversation text\n"
            " ⚕ deepseek-v4-pro · 7% · 31m\n"
            "❯\n",
        )

        self.assertEqual(metadata, {"model_id": "deepseek-v4-pro"})

    def test_visible_model_metadata_parses_agy_footer(self) -> None:
        metadata = visible_model_metadata_for_agent(
            "agy",
            "private account line must be ignored\n"
            "? for shortcuts                             Gemini 3.5 Flash (High)\n",
        )

        self.assertEqual(
            metadata,
            {"model_id": "Gemini 3.5 Flash", "reasoning_mode": "high"},
        )

    def test_herdr_preflight_session_token_changes_with_foreground_process(self) -> None:
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
                    "stdout": "› Prompt\n\n  gpt-5.6-sol xhigh · ~\n",
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

        first_token = first["agents"]["codex"]["model_probe"]["session_identity"]["token"]
        second_token = second["agents"]["codex"]["model_probe"]["session_identity"]["token"]
        serialized = json.dumps([first, second])
        self.assertNotEqual(first_token, second_token)
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
        selected = select_agents("software-code", agents, scores)
        assignments = role_assignments_for(
            "software-code",
            selected,
            agents,
            scores,
            enforce_model_role_gate=True,
        )

        self.assertIn("codex", selected)
        self.assertNotIn("implementer", assignments)
        self.assertNotIn("reviewer", assignments)
        self.assertEqual(scores["codex"]["model_role_gate"]["status"], "blocked")
        self.assertEqual(
            scores["codex"]["model_role_gate"]["fallback_roles"],
            ["discovery", "prototype", "manual"],
        )

    def test_publish_blocks_high_risk_route_when_probe_is_unsupported(self) -> None:
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
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = publish_task(
                        root,
                        "TASK-UNKNOWN-MODEL-GATE",
                        "Implement and review a source change.",
                        profile="software-code",
                        runtime="queue",
                    )

            routing = read_json(task_dir / "routing.json")
            state = read_json(task_dir / "state.json")

        self.assertEqual(routing["model_role_gate"]["status"], "blocked")
        self.assertEqual(routing["model_role_gate"]["blocked_roles"], ["implementer", "reviewer"])
        self.assertEqual(
            routing["capabilities_missing"],
            ["active_model_identity:implementer", "active_model_identity:reviewer"],
        )
        self.assertNotIn("implementer", routing["role_assignments"])
        self.assertNotIn("reviewer", routing["role_assignments"])
        self.assertEqual(state["status"], "blocked")

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

        self.assertTrue(any("binding changed" in error for error in session_errors), session_errors)
        self.assertTrue(any("not eligible" in error for error in ttl_errors), ttl_errors)

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
                "provider": "CodexPlusPlus",
                "permissions": ["task-local-evidence"],
                "declared_model": {
                    "model_id": "gpt-5.6-sol",
                    "reasoning_mode": "xhigh",
                    "source": "Codex declaration",
                    "timestamp": "2026-07-15T12:24:47Z",
                    "confidence": "high",
                    "freshness": "current",
                },
                "observed_model": {
                    "model_id": "gpt-5.6-luna",
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
        self.assertEqual(identity["declared_model"]["model_id"], "gpt-5.6-sol")
        self.assertEqual(identity["observed_model"]["model_id"], "gpt-5.6-luna")
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

    def test_iteration_budget_allows_three_fix_review_rounds(self) -> None:
        from valp_cli.workflow import iteration_budget_for

        budget = iteration_budget_for("MODEL-001", {"implementer": "codex"})
        self.assertEqual(budget["max_fix_review_rounds"], 3)
