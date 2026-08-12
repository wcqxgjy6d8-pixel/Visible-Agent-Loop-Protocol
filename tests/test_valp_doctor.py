from pathlib import Path
import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from tests.schema_helpers import schema_validator
from valp_cli.cli import main
from valp_cli.doctor import (
    DoctorCheck,
    DoctorReport,
    audit_status_to_doctor_status,
    commission_capability_passports,
    collect_doctor_report,
    render_markdown_report,
    resolve_report_path,
    runtime_checks,
)


class ValpDoctorTests(unittest.TestCase):
    def test_herdr_runtime_check_does_not_promote_agent_diagnostics(self) -> None:
        preflight = {
            "status": "warn",
            "adapter_class": "pane_controller",
            "checks": {
                "submission_transport": {"status": "pass", "mode": "agent_prompt"},
                "session_provisioning": {"status": "pass"},
                "herdr_status": {"status": "pass"},
                "pane_list": {"status": "pass"},
            },
            "agents": {
                "pi": {
                    "status": "warn",
                    "model_probe": {"status": "unsupported"},
                }
            },
        }
        with patch("valp_cli.doctor.collect_runtime_preflight") as collect:
            collect.side_effect = [
                {"status": "pass", "adapter_class": "daemon_queue"},
                preflight,
            ]
            with patch("valp_cli.doctor.shutil.which", return_value="/test/herdr"):
                checks = runtime_checks()

        herdr = next(check for check in checks if check.id == "runtime_herdr")
        self.assertEqual(herdr.status, "pass")
        self.assertIn("infrastructure status: pass", herdr.message)

    def collect_report(self, capabilities: dict, preflight: dict) -> DoctorReport:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            capability_path = root / ".valp" / "agents" / "capabilities.json"
            capability_path.parent.mkdir(parents=True)
            capability_path.write_text(json.dumps(capabilities), encoding="utf-8")
            with patch("valp_cli.doctor.now_iso", return_value="2026-07-23T10:00:00Z"), \
                    patch("valp_cli.doctor.git_checks", return_value=[]), \
                    patch("valp_cli.doctor.install_checks", return_value=[]), \
                    patch("valp_cli.doctor.syntax_checks", return_value=[]), \
                    patch("valp_cli.doctor.example_audit_checks", return_value=[]), \
                    patch("valp_cli.doctor.runtime_checks", return_value=[]), \
                    patch("valp_cli.doctor.load_local_overlay", return_value={}), \
                    patch("valp_cli.doctor.collect_runtime_preflight", return_value=preflight):
                return collect_doctor_report(root)

    def test_doctor_passes_capability_declared_runtime_commands_to_preflight(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "source": "test registry",
            "agents": {
                "example-agent": {
                    "active": True,
                    "role": ["review"],
                    "runtime": {
                        "launch_argv": ["example-agent", "run"],
                        "version_command": ["example-agent", "version"],
                    },
                }
            },
        }
        preflight_result = {
            "runtime": "manual",
            "adapter_class": "manual",
            "status": "not_applicable",
            "agents": {"example-agent": {"status": "warn"}},
        }
        with patch(
            "valp_cli.doctor.load_local_capabilities",
            return_value=capabilities,
        ), patch(
            "valp_cli.doctor.load_local_overlay",
            return_value={},
        ), patch(
            "valp_cli.doctor.collect_runtime_preflight",
            return_value=preflight_result,
        ) as preflight:
            commission_capability_passports(
                Path("/example/workspace"),
                evaluated_at="2026-07-26T00:00:00Z",
            )

        preflight.assert_called_once_with(
            ["example-agent"],
            runtime="auto",
            launch_argv_by_agent={"example-agent": ["example-agent", "run"]},
            version_command_by_agent={"example-agent": ["example-agent", "version"]},
        )

    def test_doctor_unions_registry_overlay_and_runtime_discovery(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "source": "test registry",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["implementation"],
                    "runtime": {"launch_argv": ["codex"]},
                }
            },
        }
        overlay = {
            "agent_capability_profiles": {
                "qwen": {
                    "routing_hint_only": True,
                    "likely_roles": ["implementation"],
                }
            }
        }
        preflight_result = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "warn",
            "agents": {
                "codex": {"status": "warn"},
                "qwen": {"status": "warn"},
                "claude": {"status": "warn", "session_id": "pane-claude"},
            },
        }
        with patch(
            "valp_cli.doctor.load_local_capabilities",
            return_value=capabilities,
        ), patch(
            "valp_cli.doctor.load_local_overlay",
            return_value=overlay,
        ), patch(
            "valp_cli.doctor.collect_runtime_preflight",
            return_value=preflight_result,
        ) as preflight:
            passports = commission_capability_passports(
                Path("/example/workspace"),
                evaluated_at="2026-08-05T00:00:00Z",
            )

        preflight.assert_called_once_with(
            ["codex", "qwen"],
            runtime="auto",
            launch_argv_by_agent={"codex": ["codex"]},
            version_command_by_agent={},
        )
        self.assertEqual(
            [passport["agent_id"] for passport in passports],
            ["claude", "codex", "qwen"],
        )
        by_agent = {passport["agent_id"]: passport for passport in passports}
        self.assertEqual(
            by_agent["qwen"]["capability_layers"]["local_presence"]["status"],
            "unknown",
        )
        self.assertEqual(by_agent["qwen"]["role_eligibility"]["implementer"], "not_declared")
        self.assertEqual(
            by_agent["claude"]["capability_layers"]["local_presence"]["status"],
            "unknown",
        )

    def test_doctor_commissions_capability_passport_with_observed_model_identity(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T10:00:00Z",
            "source": "test installation registry",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["implementation", "review"],
                    "model_identity": {
                        "agent_surface": "codex_cli",
                        "declared_model": {
                            "model_id": "gpt-declared",
                            "provider": "relay-a",
                            "reasoning_mode": "high",
                            "source": "operator declaration",
                            "timestamp": "2026-07-23T10:00:00Z",
                            "confidence": "high",
                        },
                    },
                }
            },
        }
        preflight = {
            "generated_at": "2026-07-23T10:00:00Z",
            "runtime": "test-runtime",
            "adapter_class": "local_process_worker",
            "status": "pass",
            "checks": {},
            "agents": {
                "codex": {
                    "status": "pass",
                    "cli": {
                        "status": "pass",
                        "version_output": "codex 1.2.3",
                    },
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "observed",
                        "source": "test runtime metadata",
                        "observed_at": "2026-07-23T10:00:00Z",
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "gpt-observed",
                            "provider": "relay-b",
                            "reasoning_mode": "xhigh",
                            "confidence": "high",
                        },
                        "session_identity": {
                            "status": "known",
                            "token": "sha256:test-session",
                            "source": "test runtime session",
                            "generation": "7",
                        },
                    },
                }
            },
        }

        report = self.collect_report(capabilities, preflight)

        self.assertEqual(len(report.capability_passports), 1)
        passport = report.capability_passports[0]
        self.assertEqual(passport["schema_version"], "valp-capability-passport.v1")
        self.assertEqual(passport["agent_id"], "codex")
        self.assertEqual(passport["agent_surface"], "codex_cli")
        self.assertEqual(passport["model_identity"]["declared_model"]["model_id"], "gpt-declared")
        self.assertEqual(passport["model_identity"]["observed_model"]["model_id"], "gpt-observed")
        self.assertEqual(passport["model_identity"]["observed_model"]["provider"], "relay-b")
        self.assertEqual(passport["model_identity"]["model_probe"]["session_identity"]["token"], "sha256:test-session")
        self.assertEqual(passport["role_eligibility"]["implementer"], "blocked")
        self.assertEqual(passport["role_eligibility"]["reviewer"], "blocked")

        errors = list(
            schema_validator(
                Path(__file__).resolve().parents[1] / "schemas" / "capability-passport.schema.json"
            ).iter_errors(passport)
        )
        self.assertEqual(errors, [])

    def test_doctor_passport_exposes_reachable_tools_and_execution_boundaries(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T10:00:00Z",
            "source": "test installation registry",
            "agents": {
                "engineer": {
                    "active": True,
                    "role": ["coordination", "implementation", "review", "research"],
                    "skills": ["tdd", "security-review"],
                    "mcp_servers": ["repo-mcp"],
                    "mcp_tools": ["repo.read", "repo.search"],
                    "permissions": {
                        "filesystem": ["workspace_read", "workspace_write"],
                        "network": ["outbound_https"],
                        "shell": ["execute"],
                        "mutation": ["source_edit"],
                    },
                    "context_policy": {"hard_compression_pct": 65},
                    "current_context": {"status": "healthy", "usage_pct": 22},
                    "must_not_do": ["release_without_approval"],
                    "runtime": {
                        "adapter_id": "test-runtime",
                        "launch_argv": ["/test/bin/engineer"],
                    },
                    "model_identity": {
                        "agent_surface": "engineer_cli",
                        "declared_model": {
                            "model_id": "model-a",
                            "provider": "relay-a",
                            "reasoning_mode": "high",
                            "source": "operator declaration",
                            "timestamp": "2026-07-23T10:00:00Z",
                            "confidence": "high",
                        },
                    },
                }
            },
        }
        preflight = {
            "runtime": "test-runtime",
            "adapter_class": "local_process_worker",
            "status": "pass",
            "checks": {},
            "agents": {
                "engineer": {
                    "status": "pass",
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "observed",
                        "source": "test runtime metadata",
                        "observed_at": "2026-07-23T10:00:00Z",
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "model-a",
                            "provider": "relay-a",
                            "reasoning_mode": "high",
                            "confidence": "high",
                        },
                        "session_identity": {
                            "status": "known",
                            "token": "sha256:engineer-session",
                            "source": "test runtime session",
                            "generation": "3",
                        },
                    },
                }
            },
        }

        passport = self.collect_report(capabilities, preflight).capability_passports[0]

        self.assertEqual(passport["skills"]["reachable"], ["tdd", "security-review"])
        self.assertEqual(passport["mcp"]["servers"], ["repo-mcp"])
        self.assertEqual(passport["mcp"]["tools"], ["repo.read", "repo.search"])
        self.assertEqual(passport["permissions"]["filesystem"], ["workspace_read", "workspace_write"])
        self.assertEqual(passport["context"]["current"], {"status": "healthy", "usage_pct": 22})
        self.assertEqual(passport["known_limitations"], ["release_without_approval"])
        self.assertEqual(
            passport["role_eligibility"],
            {"leader": "eligible", "implementer": "eligible", "reviewer": "eligible", "researcher": "eligible"},
        )

    def test_doctor_accepts_strong_runtime_model_when_no_default_was_declared(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T10:00:00Z",
            "agents": {
                "reviewer": {
                    "active": True,
                    "role": ["review"],
                    "model_identity": {"agent_surface": "review_cli"},
                }
            },
        }
        preflight = {
            "runtime": "test-runtime",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {},
            "agents": {
                "reviewer": {
                    "status": "pass",
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "observed",
                        "source": "runtime adapter metadata",
                        "observed_at": "2026-07-23T10:00:00Z",
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

        passport = self.collect_report(capabilities, preflight).capability_passports[0]

        self.assertEqual(passport["model_identity"]["mismatch"]["status"], "not_applicable")
        self.assertEqual(passport["model_identity"]["evidence_status"], "strong")
        self.assertEqual(passport["role_eligibility"]["reviewer"], "eligible")
        errors = list(
            schema_validator(
                Path(__file__).resolve().parents[1] / "schemas" / "capability-passport.schema.json"
            ).iter_errors(passport)
        )
        self.assertEqual(errors, [])

    def test_doctor_blocks_declared_leader_without_launch_contract(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T10:00:00Z",
            "agents": {
                "coordinator": {
                    "active": True,
                    "role": ["coordination"],
                    "model_identity": {"agent_surface": "coordinator_cli"},
                    "runtime": {"adapter_id": "herdr"},
                }
            },
        }
        preflight = {
            "runtime": "HERDR",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {},
            "agents": {
                "coordinator": {
                    "status": "pass",
                    "session_id": "pane-coordinator",
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "unsupported",
                        "model": {
                            "model_id": "unknown",
                            "provider": "unknown",
                            "reasoning_mode": "unknown",
                            "confidence": "unknown",
                        },
                        "session_identity": {
                            "status": "known",
                            "token": "sha256:coordinator-session",
                            "source": "test runtime session",
                            "generation": "1",
                        },
                    },
                }
            },
        }

        passport = self.collect_report(capabilities, preflight).capability_passports[0]

        self.assertEqual(passport["runtime"]["launch_argv"], [])
        self.assertEqual(passport["role_eligibility"]["leader"], "blocked")

    def test_doctor_keeps_four_capability_layers_and_binds_history_to_current_session(self) -> None:
        exact_binding = {
            "agent_surface": "review_cli",
            "model_id": "review-model",
            "provider": "relay-r",
            "reasoning_mode": "high",
            "session_token": "sha256:review-session",
        }
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T10:00:00Z",
            "source": "test installation registry",
            "agents": {
                "reviewer": {
                    "active": True,
                    "role": ["review"],
                    "official_capability_claims": [
                        {
                            "capability_id": "code_review",
                            "status": "claimed",
                            "source": "vendor documentation",
                            "evidence_refs": ["https://vendor.example/reviewer"],
                        }
                    ],
                    "installation": {
                        "status": "installed",
                        "version": "2.4.1",
                        "source": "reviewer --version",
                    },
                    "task_verified_history": [
                        {
                            "task_id": "TASK-CURRENT-BINDING",
                            "role": "reviewer",
                            "outcome": "passed",
                            "binding": exact_binding,
                            "evidence_refs": ["tasks/TASK-CURRENT-BINDING/review.md"],
                        },
                        {
                            "task_id": "TASK-OLD-MODEL",
                            "role": "reviewer",
                            "outcome": "passed",
                            "binding": {**exact_binding, "model_id": "old-model"},
                            "evidence_refs": ["tasks/TASK-OLD-MODEL/review.md"],
                        },
                    ],
                    "model_identity": {
                        "agent_surface": "review_cli",
                        "declared_model": {
                            "model_id": "review-model",
                            "provider": "relay-r",
                            "reasoning_mode": "high",
                            "source": "operator declaration",
                            "timestamp": "2026-07-23T10:00:00Z",
                            "confidence": "high",
                        },
                    },
                }
            },
        }
        preflight = {
            "runtime": "test-runtime",
            "adapter_class": "local_process_worker",
            "status": "pass",
            "checks": {},
            "agents": {
                "reviewer": {
                    "status": "pass",
                    "cli": {"status": "pass", "version_output": "reviewer 2.4.1"},
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "observed",
                        "source": "test runtime metadata",
                        "observed_at": "2026-07-23T10:00:00Z",
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "review-model",
                            "provider": "relay-r",
                            "reasoning_mode": "high",
                            "confidence": "high",
                        },
                        "session_identity": {
                            "status": "known",
                            "token": "sha256:review-session",
                            "source": "test runtime session",
                            "generation": "4",
                        },
                    },
                }
            },
        }

        passport = self.collect_report(capabilities, preflight).capability_passports[0]

        self.assertEqual(passport["capability_layers"]["official_claim"]["status"], "present")
        self.assertEqual(passport["capability_layers"]["local_presence"]["status"], "present")
        self.assertEqual(passport["capability_layers"]["live_callable"]["status"], "present")
        self.assertEqual(passport["capability_layers"]["task_verified"]["status"], "present")
        self.assertEqual(passport["official_capability_claims"][0]["source"], "vendor documentation")
        self.assertEqual(passport["local_installation"]["version"], "2.4.1")
        self.assertEqual(passport["live_callability"]["status"], "pass")
        self.assertEqual(passport["task_verified_history"]["qualifying_record_count"], 1)
        self.assertEqual(
            [record["binding_status"] for record in passport["task_verified_history"]["records"]],
            ["current", "mismatch"],
        )

    def test_doctor_commissions_one_passport_per_addressable_session(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T10:00:00Z",
            "source": "test installation registry",
            "agents": {
                "codex": {
                    "active": True,
                    "role": ["leader", "implementation"],
                    "runtime": {
                        "adapter_id": "herdr",
                        "launch_argv": ["/test/bin/codex", "--example-mode"],
                        "version_command": ["/test/bin/codex", "--version"],
                    },
                    "model_identity": {
                        "agent_surface": "codex_cli",
                        "declared_model": {
                            "model_id": "model-a",
                            "provider": "relay",
                            "reasoning_mode": "high",
                            "source": "operator declaration",
                            "timestamp": "2026-07-23T10:00:00Z",
                            "confidence": "high",
                        },
                    },
                }
            },
        }

        def session(session_id: str, model_id: str) -> dict:
            return {
                "session_id": session_id,
                "status": "pass",
                "model_probe": {
                    "schema_version": "valp-model-probe.v1",
                    "status": "observed",
                    "source": "test runtime metadata",
                    "observed_at": "2026-07-23T10:00:00Z",
                    "ttl_seconds": 3600,
                    "model": {
                        "model_id": model_id,
                        "provider": "relay",
                        "reasoning_mode": "high",
                        "confidence": "high",
                    },
                    "session_identity": {
                        "status": "known",
                        "token": f"sha256:{session_id}",
                        "source": "test runtime session",
                        "generation": session_id,
                    },
                },
            }

        preflight = {
            "runtime": "test-runtime",
            "adapter_class": "pane_controller",
            "status": "pass",
            "checks": {},
            "agents": {
                "codex": {
                    "status": "pass",
                    "sessions": [session("session-1", "model-a"), session("session-2", "model-b")],
                }
            },
        }

        passports = self.collect_report(capabilities, preflight).capability_passports

        self.assertEqual(len(passports), 2)
        self.assertEqual(
            [(item["runtime_identity"]["session_id"], item["model_identity"]["observed_model"]["model_id"]) for item in passports],
            [("session-1", "model-a"), ("session-2", "model-b")],
        )
        self.assertEqual(len({item["principal_id"] for item in passports}), 2)
        self.assertEqual(
            [item["runtime"]["launch_argv"] for item in passports],
            [["/test/bin/codex", "--example-mode"], ["/test/bin/codex", "--example-mode"]],
        )
        self.assertTrue(all(item["runtime"]["adapter_id"] == "herdr" for item in passports))

    def test_doctor_prefers_live_runtime_capability_discovery_over_static_registry_hints(self) -> None:
        capabilities = {
            "schema_version": "valp-agent-capabilities.v1",
            "updated_at": "2026-07-23T09:00:00Z",
            "source": "static registry",
            "agents": {
                "worker": {
                    "active": True,
                    "skills": ["stale-skill"],
                    "mcp_servers": ["stale-mcp"],
                    "permissions": {"filesystem": ["read_only"]},
                    "context_policy": {"hard_compression_pct": 50},
                }
            },
        }
        preflight = {
            "runtime": "test-runtime",
            "adapter_class": "local_process_worker",
            "status": "pass",
            "checks": {},
            "agents": {
                "worker": {
                    "status": "pass",
                    "model_probe": {
                        "schema_version": "valp-model-probe.v1",
                        "status": "unsupported",
                        "source": "test runtime metadata",
                        "observed_at": "2026-07-23T10:00:00Z",
                        "ttl_seconds": 3600,
                        "model": {
                            "model_id": "unknown",
                            "provider": "unknown",
                            "reasoning_mode": "unknown",
                            "confidence": "unknown",
                        },
                        "session_identity": {
                            "status": "known",
                            "token": "sha256:worker-session",
                            "source": "test runtime session",
                            "generation": "1",
                        },
                    },
                    "capability_discovery": {
                        "source": "runtime adapter live scan",
                        "skills": ["live-skill"],
                        "mcp_servers": ["live-mcp"],
                        "mcp_tools": ["live.search"],
                        "permissions": {
                            "filesystem": ["workspace_read", "workspace_write"],
                            "network": ["outbound_https"],
                            "shell": ["execute"],
                            "mutation": ["source_edit"],
                        },
                        "context_policy": {"hard_compression_pct": 70},
                        "current_context": {"status": "healthy", "usage_pct": 18},
                        "known_limitations": ["no_release_permission"],
                    },
                }
            },
        }

        passport = self.collect_report(capabilities, preflight).capability_passports[0]

        self.assertEqual(passport["skills"], {"reachable": ["live-skill"], "source": "runtime adapter live scan"})
        self.assertEqual(passport["mcp"]["servers"], ["live-mcp"])
        self.assertEqual(passport["mcp"]["tools"], ["live.search"])
        self.assertEqual(passport["permissions"]["filesystem"], ["workspace_read", "workspace_write"])
        self.assertEqual(passport["context"]["policy"], {"hard_compression_pct": 70})
        self.assertEqual(passport["known_limitations"], ["no_release_permission"])

    def test_desktop_report_path_is_explicit_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = resolve_report_path("desktop", home=home, generated_at="2026-07-05T12:00:00Z")
        self.assertEqual(path, home / "Desktop" / "valp-doctor-report-20260705T120000Z.md")

    def test_markdown_report_contains_summary_and_checks(self) -> None:
        report = DoctorReport(
            workspace="/tmp/example",
            generated_at="2026-07-05T12:00:00Z",
            status="warn",
            pass_count=1,
            warn_count=1,
            fail_count=0,
            checks=[
                DoctorCheck("git_tracking", "Local HEAD matches upstream tracking ref", "pass", "HEAD == upstream tracking ref.", ["abc123"]),
                DoctorCheck("ignored_residue", "Ignored local residue is absent", "warn", "Found ignored residue.", ["!! .pytest_cache/"], "Remove caches."),
            ],
        )
        markdown = render_markdown_report(report)
        self.assertIn("# VALP Doctor Report", markdown)
        self.assertIn("Status: **WARN**", markdown)
        self.assertIn("### PASS `git_tracking`", markdown)
        self.assertIn("Suggested action: Remove caches.", markdown)

    def test_markdown_report_surfaces_capability_passport_model_identity(self) -> None:
        report = DoctorReport(
            workspace="/tmp/example",
            generated_at="2026-07-23T10:00:00Z",
            status="pass",
            pass_count=0,
            warn_count=0,
            fail_count=0,
            checks=[],
            capability_passports=[
                {
                    "agent_id": "codex",
                    "agent_surface": "codex_cli",
                    "model_identity": {
                        "evidence_status": "strong",
                        "declared_model": {"model_id": "declared-model", "provider": "relay"},
                        "observed_model": {"model_id": "observed-model", "provider": "relay"},
                        "model_probe": {"session_identity": {"status": "known"}},
                    },
                    "role_eligibility": {
                        "leader": "eligible",
                        "implementer": "eligible",
                        "reviewer": "eligible",
                        "researcher": "not_declared",
                    },
                }
            ],
        )

        markdown = render_markdown_report(report)

        self.assertIn("## Capability Passports", markdown)
        self.assertIn("### `codex` on `codex_cli`", markdown)
        self.assertIn("Declared model: `declared-model` via `relay`", markdown)
        self.assertIn("Observed model: `observed-model` via `relay`", markdown)
        self.assertIn("Model session: `known`", markdown)
        self.assertIn("Skills: `none observed`", markdown)
        self.assertIn("MCP servers: `none observed`", markdown)
        self.assertIn("Known limitations: `none observed`", markdown)
        self.assertIn("Implementer: `eligible`", markdown)

    def test_cli_json_uses_structured_report(self) -> None:
        report = DoctorReport(
            workspace="/tmp/example",
            generated_at="2026-07-05T12:00:00Z",
            status="pass",
            pass_count=1,
            warn_count=0,
            fail_count=0,
            checks=[
                DoctorCheck("git_tracking", "Local HEAD matches upstream tracking ref", "pass", "HEAD == upstream tracking ref.", ["abc123"]),
            ],
        )
        output = io.StringIO()
        with patch("valp_cli.cli.collect_doctor_report", return_value=report):
            with contextlib.redirect_stdout(output):
                code = main(["doctor", "--workspace", "/tmp/example", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["checks"][0]["id"], "git_tracking")

    def test_audit_warn_status_is_preserved(self) -> None:
        self.assertEqual(audit_status_to_doctor_status("pass"), "pass")
        self.assertEqual(audit_status_to_doctor_status("warn"), "warn")
        self.assertEqual(audit_status_to_doctor_status("fail"), "fail")


if __name__ == "__main__":
    unittest.main()
