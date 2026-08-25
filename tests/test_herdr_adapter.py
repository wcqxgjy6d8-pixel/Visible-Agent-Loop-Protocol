from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from valp_cli.doctor import FAIL, runtime_checks
from valp_cli.herdr_adapter import (
    HERDR_PANE_LIST_STDOUT_LIMIT,
    HerdrSubmissionError,
    detect_herdr_submission_capability,
    opaque_process_generation,
    open_herdr_leader_session,
    provision_herdr_agent_session,
    provision_herdr_leader_session,
    recover_herdr_leader_session,
    submit_herdr_dispatch,
)
from valp_cli.submission import has_concrete_runtime_submission_proof
from valp_cli.workflow import (
    cli_preflight_for_agent,
    collect_herdr_preflight,
    dispatch_task,
    ensure_herdr_agent_sessions,
    herdr_launch_argv_for,
    publish_task,
    route_task,
    write_herdr_submission_receipt,
)


TEST_CAPABILITIES = {
    "schema_version": "valp-agent-capabilities.v1",
    "updated_at": "2026-07-22T00:00:00Z",
    "source": "test fixture",
    "agents": {
        "codex": {
            "active": True,
            "role": ["coordination", "review", "risk_review"],
            "skills": [],
            "mcp_servers": [],
            "strengths": ["coordinates and reviews"],
            "must_not_do": ["must not bypass approval gates"],
            "runtime": {"launch_argv": ["codex"]},
        }
    },
}


def herdr_invocation_proof(*, state_change_seq: int = 2) -> dict[str, object]:
    return {
        "runtime": "HERDR",
        "transport_mode": "agent_prompt",
        "proof_class": "agent_invocation",
        "pane_id": "pane-1",
        "submission_proof": {
            "kind": "identity_bound_state_change",
            "baseline_state_change_seq": 1,
            "state_change_seq": state_change_seq,
            "identity": {
                "terminal_id": "terminal-1",
                "name": "codex",
                "agent": "codex",
                "pane_id": "pane-1",
            },
        },
    }


def install_fake_fallback_herdr(root: Path) -> Path:
    fake_bin = root / "bin"
    fake_bin.mkdir()
    stub = fake_bin / "fake_herdr.py"
    stub.write_text(
        """from __future__ import annotations

import json
import sys
from pathlib import Path


STATE_PATH = Path(__file__).with_name("owned-session-state.json")


RESPONSES = {
    ("agent", "--help"): "herdr agent start <name> [--cwd PATH] -- <argv...>\\nherdr agent wait <target> --status <state> [--timeout MS]",
    ("workspace", "--help"): "herdr workspace create [--cwd PATH] [--label TEXT] [--no-focus]",
    ("pane", "--help"): "herdr pane move <pane_id> --new-tab [--workspace ID] [--no-focus]\\nherdr pane send-text <pane_id> <text>\\nherdr pane send-keys <pane_id> <key>",
    ("status", "--json"): '{"client":{"version":"0.7.4"},"server":{"version":"0.7.4"}}',
    ("pane", "list"): '{"result":{"panes":[{"agent":"codex","name":"user-codex","pane_id":"pane-1","terminal_id":"terminal-1","workspace_id":"workspace-user","tab_id":"tab-user","cwd":"/tmp","agent_status":"idle","model_id":"test-model","provider":"test-provider","reasoning_mode":"high","session_id":"session-user","generation":1},{"agent":"codex","pane_id":"pane-owned","terminal_id":"terminal-owned","workspace_id":"workspace-owned","tab_id":"tab-agent","agent_status":"idle","model_id":"test-model","provider":"test-provider","reasoning_mode":"high","session_id":"session-owned","generation":1}]}}',
    ("pane", "process-info"): '{"result":{"process_info":{"foreground_process_group_id":41}}}',
    ("pane", "read"): '{"result":{"read":{"text":"test-model high ·"}}}',
    ("pane", "layout"): '{"result":{"layout":{"panes":[{"pane_id":"pane-1","rect":{"width":120,"height":40}},{"pane_id":"pane-owned","rect":{"width":120,"height":40}}]}}',
    ("pane", "send-text"): '{"result":{"pane_id":"pane-1"}}',
    ("pane", "send-keys"): '{"result":{"pane_id":"pane-1"}}',
    ("agent", "wait"): '{"result":{"agent":{"status":"working","agent_session_id":"session-1"}}}',
}


key = tuple(sys.argv[1:3])
if key == ("pane", "list") and STATE_PATH.exists():
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    print(json.dumps({"result": {"panes": [{
        "agent": "codex",
        "name": state["name"],
        "pane_id": "pane-owned",
        "terminal_id": "terminal-owned",
        "workspace_id": "workspace-owned",
        "tab_id": "tab-agent",
        "cwd": state["cwd"],
        "agent_status": "idle",
        "model_id": "test-model",
        "provider": "test-provider",
        "reasoning_mode": "high",
        "session_id": "session-owned",
        "generation": 1,
    }]}}))
    raise SystemExit(0)
if key == ("workspace", "create"):
    label = sys.argv[sys.argv.index("--label") + 1]
    print(json.dumps({
        "result": {
            "type": "workspace_created",
            "workspace": {"workspace_id": "workspace-owned", "label": label},
        }
    }))
    raise SystemExit(0)
if key == ("agent", "start"):
    cwd = sys.argv[sys.argv.index("--cwd") + 1]
    argv = sys.argv[sys.argv.index("--") + 1:]
    STATE_PATH.write_text(
        json.dumps({"cwd": cwd, "name": sys.argv[3]}),
        encoding="utf-8",
    )
    print(json.dumps({
        "result": {
            "type": "agent_started",
            "agent": {
                "agent": "codex",
                "name": sys.argv[3],
                "pane_id": "pane-owned",
                "terminal_id": "terminal-owned",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-owned",
                "cwd": cwd,
                "agent_status": "idle",
                "focused": False,
                "revision": 1,
            },
            "argv": argv,
        }
    }))
    raise SystemExit(0)
if key == ("pane", "move"):
    print('{"result":{"type":"pane_moved","pane_id":"pane-owned"}}')
    raise SystemExit(0)
if key == ("pane", "get"):
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    print(json.dumps({
        "result": {
            "type": "pane_info",
            "pane": {
                "agent": "codex",
                "name": state["name"],
                "pane_id": "pane-owned",
                "terminal_id": "terminal-owned",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-agent",
                "cwd": state["cwd"],
                "focused": False,
            },
        }
    }))
    raise SystemExit(0)
if key not in RESPONSES:
    print("unexpected fake herdr command: " + " ".join(sys.argv[1:]), file=sys.stderr)
    raise SystemExit(2)
print(RESPONSES[key])
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        herdr = fake_bin / "herdr.cmd"
        herdr.write_text(
            f'@echo off\r\n"{sys.executable}" "%~dp0fake_herdr.py" %*\r\n',
            encoding="utf-8",
        )
        (fake_bin / "codex.cmd").write_text("@exit /b 0\r\n", encoding="utf-8")
    else:
        herdr = fake_bin / "herdr"
        herdr.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "${{0%/*}}/fake_herdr.py" "$@"\n',
            encoding="utf-8",
        )
        herdr.chmod(0o755)
        codex = fake_bin / "codex"
        codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        codex.chmod(0o755)
    return fake_bin


class PackagedHerdrAdapterTests(unittest.TestCase):
    def test_leader_provisioning_creates_fresh_installation_owned_session(self) -> None:
        calls: list[list[str]] = []
        project_root = Path("/example/project")

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:3] == ["workspace", "create"]:
                label = command[command.index("--label") + 1]
                stdout = json.dumps({
                    "result": {
                        "workspace": {
                            "workspace_id": "workspace-leader",
                            "label": label,
                        }
                    }
                })
            elif command[1:] == ["agent", "--help"]:
                stdout = "herdr agent start <name> -- <argv...>"
            elif command[1:3] == ["agent", "start"]:
                name = command[3]
                stdout = json.dumps({
                    "result": {
                        "type": "agent_started",
                        "argv": command[command.index("--") + 1:],
                        "agent": {"pane_id": "pane-leader", "name": name},
                    }
                })
            elif command[1:3] == ["pane", "move"]:
                stdout = '{"result":{"type":"pane_moved","pane_id":"pane-leader"}}'
            elif command[1:3] == ["pane", "get"]:
                name = next(
                    call[call.index("--label") + 1]
                    for call in calls
                    if call[1:3] == ["pane", "move"]
                )
                stdout = json.dumps({
                    "result": {
                        "type": "pane_info",
                        "pane": {
                            "agent": "codex",
                            "agent_status": "idle",
                            "name": name,
                            "pane_id": "pane-leader",
                            "terminal_id": "terminal-leader",
                            "workspace_id": "workspace-leader",
                            "tab_id": "tab-leader",
                            "cwd": str(project_root),
                            "focused": False,
                        },
                    }
                })
            elif command[1:3] == ["pane", "process-info"]:
                stdout = '{"result":{"process_info":{"foreground_process_group_id":73}}}'
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        binding = provision_herdr_leader_session(
            "/test/herdr",
            installation_id="installation-test",
            principal_id="agent-codex-session-a",
            agent="codex",
            workspace_root=project_root,
            launch_argv=["/test/bin/codex", "--example-mode"],
            leader_epoch=1,
            generation=1,
            run_command=fake_run,
        )

        self.assertEqual(binding["ownership"], {
            "scope": "installation",
            "installation_id": "installation-test",
        })
        self.assertEqual(binding["runtime_identity"]["session_id"], "pane-leader")
        self.assertRegex(binding["runtime_identity"]["process_generation"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("foreground-process-group:73", json.dumps(binding))
        self.assertEqual(binding["health"]["status"], "pass")
        self.assertFalse(any(command[1:3] == ["pane", "list"] for command in calls))
        workspace_create = next(command for command in calls if command[1:3] == ["workspace", "create"])
        agent_start = next(command for command in calls if command[1:3] == ["agent", "start"])
        self.assertIn("--no-focus", workspace_create)
        self.assertIn("--no-focus", agent_start)

    def test_open_leader_focuses_existing_attachment_without_reprovisioning(self) -> None:
        calls: list[list[str]] = []
        binding = {
            "binding_digest": "sha256:" + ("a" * 64),
            "runtime_identity": {
                "session_id": "pane-leader",
                "pane_id": "pane-leader",
                "workspace_id": "workspace-leader",
            },
        }

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:3] == ["pane", "get"]:
                stdout = json.dumps({
                    "result": {
                        "pane": {
                            "pane_id": "pane-leader",
                            "workspace_id": "workspace-leader",
                            "agent": "codex",
                        }
                    }
                })
            elif command[1:] == ["agent", "--help"]:
                stdout = "herdr agent focus <target>"
            elif command[1:3] == ["agent", "focus"]:
                stdout = json.dumps({"result": {"pane_id": "pane-leader"}})
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        opened = open_herdr_leader_session("/test/herdr", binding, fake_run)

        self.assertEqual(opened["status"], "opened")
        self.assertEqual(opened["action"], "focused_existing_attachment")
        self.assertEqual(opened["session_id"], "pane-leader")
        self.assertEqual(calls[-1], ["/test/herdr", "agent", "focus", "pane-leader"])
        self.assertFalse(any(command[1:3] == ["workspace", "create"] for command in calls))

    def test_open_leader_only_reprovisions_on_explicit_pane_not_found(self) -> None:
        binding = {
            "runtime_identity": {
                "session_id": "pane-gone",
                "pane_id": "pane-gone",
                "workspace_id": "workspace-leader",
            }
        }

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            self.assertEqual(command[1:3], ["pane", "get"])
            return {
                "ok": False,
                "exit_code": 1,
                "stdout": '{"error":{"code":"pane_not_found"}}',
                "stderr": "",
            }

        opened = open_herdr_leader_session("/test/herdr", binding, fake_run)

        self.assertEqual(opened["status"], "missing")
        self.assertEqual(opened["action"], "reprovision_required")

    def test_leader_provisioning_retries_transient_identity_readiness(self) -> None:
        calls: list[list[str]] = []
        pane_get_attempts = 0
        project_root = Path("/example/project")

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal pane_get_attempts
            calls.append(command)
            if command[1:3] == ["workspace", "create"]:
                label = command[command.index("--label") + 1]
                stdout = json.dumps({
                    "result": {
                        "workspace": {
                            "workspace_id": "workspace-leader",
                            "label": label,
                        }
                    }
                })
            elif command[1:] == ["agent", "--help"]:
                stdout = "herdr agent start <name> -- <argv...>"
            elif command[1:3] == ["agent", "start"]:
                name = command[3]
                stdout = json.dumps({
                    "result": {
                        "type": "agent_started",
                        "argv": command[command.index("--") + 1:],
                        "agent": {"pane_id": "pane-leader", "name": name},
                    }
                })
            elif command[1:3] == ["pane", "move"]:
                stdout = '{"result":{"type":"pane_moved","pane_id":"pane-leader"}}'
            elif command[1:3] == ["pane", "get"]:
                pane_get_attempts += 1
                name = next(
                    call[call.index("--label") + 1]
                    for call in calls
                    if call[1:3] == ["pane", "move"]
                )
                pane = {
                    "pane_id": "pane-leader",
                    "terminal_id": "terminal-leader",
                    "workspace_id": "workspace-leader",
                    "tab_id": "tab-leader",
                    "cwd": str(project_root),
                    "focused": False,
                    "name": name,
                }
                if pane_get_attempts > 1:
                    pane.update({"agent": "codex", "agent_status": "idle"})
                stdout = json.dumps({"result": {"type": "pane_info", "pane": pane}})
            elif command[1:3] == ["pane", "process-info"]:
                stdout = '{"result":{"process_info":{"foreground_process_group_id":73}}}'
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        binding = provision_herdr_leader_session(
            "/test/herdr",
            installation_id="installation-test",
            principal_id="agent-codex-session-a",
            agent="codex",
            workspace_root=project_root,
            launch_argv=["/test/bin/codex", "--example-mode"],
            leader_epoch=1,
            generation=1,
            run_command=fake_run,
            readiness_attempts=2,
            readiness_interval_seconds=0,
        )

        self.assertEqual(pane_get_attempts, 2)
        self.assertEqual(binding["health"]["evidence"]["readiness_attempts"], 2)
        self.assertEqual(
            [item["status"] for item in binding["health"]["evidence"]["observations"]],
            ["incomplete_identity", "pass"],
        )

    def test_leader_recovery_reads_only_the_exact_approved_session(self) -> None:
        calls: list[list[str]] = []
        project_root = Path("/example/project")
        approval = {
            "approval_event_id": "event-recovery-approved",
            "approved_session_id": "workspace-leader:pane-recovered",
            "failed_receipt_id": "leader-receipt-failed",
            "failed_receipt_digest": "sha256:" + ("1" * 64),
        }

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:] == ["pane", "get", "workspace-leader:pane-recovered"]:
                stdout = json.dumps({
                    "result": {"pane": {
                        "agent": "codex",
                        "agent_status": "idle",
                        "cwd": str(project_root),
                        "focused": False,
                        "label": "valp-leader-codex-e7eee1444888-g1",
                        "pane_id": "workspace-leader:pane-recovered",
                        "tab_id": "workspace-leader:tab-recovered",
                        "terminal_id": "terminal-recovered",
                        "workspace_id": "workspace-leader",
                    }}
                })
            elif command[1:] == ["workspace", "get", "workspace-leader"]:
                stdout = json.dumps({
                    "result": {"workspace": {
                        "focused": False,
                        "label": "valp-leader-e7eee14448880e8d-g1",
                        "workspace_id": "workspace-leader",
                    }}
                })
            elif command[1:] == ["pane", "process-info", "--pane", "workspace-leader:pane-recovered"]:
                stdout = json.dumps({
                    "result": {"process_info": {
                        "foreground_process_group_id": 73,
                        "foreground_processes": [{
                            "argv": ["/test/bin/codex", "--example-mode"],
                            "cwd": str(project_root),
                            "pid": 73,
                        }],
                        "pane_id": "workspace-leader:pane-recovered",
                    }}
                })
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        binding = recover_herdr_leader_session(
            "/test/herdr",
            installation_id="installation-test",
            principal_id="agent-codex-session-a",
            agent="codex",
            workspace_root=project_root,
            launch_argv=["/test/bin/codex", "--example-mode"],
            leader_epoch=1,
            generation=1,
            session_id="workspace-leader:pane-recovered",
            recovery_approval=approval,
            run_command=fake_run,
            readiness_interval_seconds=0,
        )

        self.assertEqual(
            calls,
            [
                ["/test/herdr", "pane", "get", "workspace-leader:pane-recovered"],
                ["/test/herdr", "workspace", "get", "workspace-leader"],
                ["/test/herdr", "pane", "process-info", "--pane", "workspace-leader:pane-recovered"],
            ],
        )
        self.assertEqual(binding["runtime_identity"]["session_id"], "workspace-leader:pane-recovered")
        self.assertEqual(binding["launch"]["argv"], ["/test/bin/codex", "--example-mode"])
        self.assertEqual(binding["recovery"], approval)
        self.assertEqual(binding["health"]["status"], "pass")

    def test_leader_recovery_rejects_a_complete_but_different_launch_argv(self) -> None:
        project_root = Path("/example/project")

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:3] == ["pane", "get"]:
                stdout = json.dumps({
                    "result": {"pane": {
                        "agent": "codex",
                        "cwd": str(project_root),
                        "focused": False,
                        "label": "valp-leader-codex-e7eee1444888-g1",
                        "pane_id": "workspace-leader:pane-recovered",
                        "tab_id": "workspace-leader:tab-recovered",
                        "terminal_id": "terminal-recovered",
                        "workspace_id": "workspace-leader",
                    }}
                })
            elif command[1:3] == ["workspace", "get"]:
                stdout = json.dumps({
                    "result": {"workspace": {
                        "focused": False,
                        "label": "valp-leader-e7eee14448880e8d-g1",
                        "workspace_id": "workspace-leader",
                    }}
                })
            elif command[1:3] == ["pane", "process-info"]:
                stdout = json.dumps({
                    "result": {"process_info": {
                        "foreground_process_group_id": 73,
                        "foreground_processes": [{
                            "argv": ["/test/bin/codex", "--different-mode"],
                            "cwd": str(project_root),
                            "pid": 73,
                        }],
                        "pane_id": "workspace-leader:pane-recovered",
                    }}
                })
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        with self.assertRaisesRegex(HerdrSubmissionError, "launch argv"):
            recover_herdr_leader_session(
                "/test/herdr",
                installation_id="installation-test",
                principal_id="agent-codex-session-a",
                agent="codex",
                workspace_root=project_root,
                launch_argv=["/test/bin/codex", "--example-mode"],
                leader_epoch=1,
                generation=1,
                session_id="workspace-leader:pane-recovered",
                recovery_approval={
                    "approval_event_id": "event-recovery-approved",
                    "approved_session_id": "workspace-leader:pane-recovered",
                    "failed_receipt_id": "leader-receipt-failed",
                    "failed_receipt_digest": "sha256:" + ("1" * 64),
                },
                run_command=fake_run,
                readiness_interval_seconds=0,
            )

    def test_unbound_preflight_preserves_every_addressable_session(self) -> None:
        panes = [
            {
                "agent": "codex",
                "agent_status": "idle",
                "cwd": "/example/bootstrap",
                "pane_id": "pane-codex-bootstrap",
                "terminal_id": "terminal-bootstrap",
                "workspace_id": "workspace-user",
                "tab_id": "tab-bootstrap",
            },
            {
                "agent": "codex",
                "agent_status": "idle",
                "cwd": "/example/other-task",
                "pane_id": "pane-codex-other",
                "terminal_id": "terminal-other",
                "workspace_id": "workspace-other",
                "tab_id": "tab-other",
            },
        ]

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent start <name> -- <argv...>\nherdr agent wait <target> --status <state>"
            elif command[1:] == ["workspace", "--help"]:
                stdout = "herdr workspace create [--cwd PATH] [--no-focus]"
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane move <pane> --new-tab\nherdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
            elif command[1:] == ["status", "--json"]:
                stdout = '{"client":{"version":"0.7.4"},"server":{"version":"0.7.4"}}'
            elif command[1:] == ["pane", "list"]:
                stdout = json.dumps({"result": {"panes": panes}})
            elif command[1:3] == ["pane", "process-info"]:
                pane_id = command[-1]
                process_group = 41 if pane_id == "pane-codex-bootstrap" else 42
                stdout = json.dumps({"result": {"process_info": {"foreground_process_group_id": process_group}}})
            elif command[1:3] == ["pane", "layout"]:
                stdout = json.dumps({
                    "result": {
                        "layout": {
                            "panes": [
                                {"pane_id": pane["pane_id"], "rect": {"width": 120, "height": 40}}
                                for pane in panes
                            ]
                        }
                    }
                })
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.workflow.run_command", side_effect=fake_run
        ), patch(
            "valp_cli.workflow.cli_preflight_for_agent", return_value={"status": "pass"}
        ):
            preflight = collect_herdr_preflight(["codex"])

        sessions = preflight["agents"]["codex"]["sessions"]
        self.assertEqual(
            [session["session_id"] for session in sessions],
            ["pane-codex-bootstrap", "pane-codex-other"],
        )
        self.assertEqual(
            [session["model_probe"]["session_identity"]["generation"] for session in sessions],
            [
                opaque_process_generation(41),
                opaque_process_generation(42),
            ],
        )

    def test_cli_preflight_uses_task_owned_launch_entrypoint(self) -> None:
        launch_argv = ["/verified/agy-stable"]

        with patch(
            "valp_cli.workflow.shutil.which",
            side_effect=lambda command: command if command == launch_argv[0] else None,
        ):
            with patch(
                "valp_cli.workflow.run_command",
                return_value={
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "1.0.14\n",
                    "stderr": "",
                },
            ) as run:
                preflight = cli_preflight_for_agent(
                    "agy",
                    launch_argv=launch_argv,
                    version_command=["/verified/agy-stable", "version"],
                )

        self.assertEqual(preflight["command"], ["/verified/agy-stable", "version"])
        self.assertEqual(preflight["version_output"], "1.0.14")
        run.assert_called_once_with(["/verified/agy-stable", "version"], timeout=5.0)

    def test_cli_preflight_does_not_invent_a_version_flag(self) -> None:
        with patch("valp_cli.workflow.shutil.which") as which, patch(
            "valp_cli.workflow.run_command"
        ) as run:
            preflight = cli_preflight_for_agent(
                "example-agent",
                launch_argv=["example-agent", "run"],
            )

        self.assertEqual(preflight["status"], "warn")
        self.assertIn("runtime.version_command", preflight["message"])
        which.assert_not_called()
        run.assert_not_called()

    def test_bound_preflight_queries_the_recorded_workspace(self) -> None:
        project_root = Path("/example/project")
        calls: list[list[str]] = []
        binding = {
            "agent": "claude",
            "generation": 1,
            "ownership": {"scope": "task", "task_id": "TASK", "project_identity": "sha256:test"},
            "context": {"cwd": str(project_root)},
            "runtime_identity": {
                "pane_id": "pane-owned",
                "terminal_id": "terminal-owned",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-owned",
                "token": "sha256:" + ("1" * 64),
            },
        }

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent start <name> -- <argv...>\nherdr agent wait <target> --status <state>"
            elif command[1:] == ["workspace", "--help"]:
                stdout = "herdr workspace create [--cwd PATH] [--no-focus]"
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane move <pane> --new-tab\nherdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
            elif command[1:] == ["status", "--json"]:
                stdout = '{"client":{"version":"0.7.4"},"server":{"version":"0.7.4"}}'
            elif command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                stdout = json.dumps({"result": {"panes": [{
                    "agent": "claude",
                    "agent_status": "idle",
                    "cwd": str(project_root),
                    "pane_id": "pane-owned",
                    "terminal_id": "terminal-owned",
                    "workspace_id": "workspace-owned",
                    "tab_id": "tab-owned",
                }]}})
            elif command[1:3] == ["pane", "process-info"]:
                stdout = '{"result":{"process_info":{"foreground_process_group_id":41}}}'
            elif command[1:3] == ["pane", "read"]:
                stdout = '{"result":{"read":{"text":"[EXAMPLE_PROVIDER] example-model-2026 ░░ 0%"}}}'
            elif command[1:3] == ["pane", "layout"]:
                stdout = '{"result":{"layout":{"panes":[{"pane_id":"pane-owned","rect":{"width":120,"height":40}}]}}}'
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
            with patch("valp_cli.workflow.run_command", side_effect=fake_run):
                with patch(
                    "valp_cli.workflow.cli_preflight_for_agent",
                    return_value={"status": "pass"},
                ):
                    preflight = collect_herdr_preflight(
                        ["claude"],
                        session_bindings={"claude": binding},
                    )

        self.assertEqual(preflight["status"], "pass")
        self.assertEqual(preflight["agents"]["claude"]["session_binding"]["status"], "bound")
        self.assertIn(
            ["/test/herdr", "pane", "list", "--workspace", "workspace-owned"],
            calls,
        )
        self.assertNotIn(["/test/herdr", "pane", "list"], calls)

        for missing_field in ("cwd", "agent"):
            with self.subTest(missing_field=missing_field):
                def incomplete_run(
                    command: list[str],
                    **kwargs: object,
                ) -> dict[str, object]:
                    result = fake_run(command, **kwargs)
                    if command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                        payload = json.loads(str(result["stdout"]))
                        payload["result"]["panes"][0].pop(missing_field, None)
                        return {**result, "stdout": json.dumps(payload)}
                    return result

                with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                    with patch("valp_cli.workflow.run_command", side_effect=incomplete_run):
                        incomplete = collect_herdr_preflight(
                            ["claude"],
                            session_bindings={"claude": binding},
                        )
                self.assertEqual(incomplete["agents"]["claude"]["status"], "fail")
                self.assertIn(
                    "conflicts",
                    incomplete["agents"]["claude"]["notes"][0],
                )

        def unknown_state_run(
            command: list[str],
            **kwargs: object,
        ) -> dict[str, object]:
            result = fake_run(command, **kwargs)
            if command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                payload = json.loads(str(result["stdout"]))
                payload["result"]["panes"][0]["agent_status"] = "unknown"
                return {**result, "stdout": json.dumps(payload)}
            return result

        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
            with patch("valp_cli.workflow.run_command", side_effect=unknown_state_run):
                unknown_state = collect_herdr_preflight(
                    ["claude"],
                    session_bindings={"claude": binding},
                )
        self.assertEqual(unknown_state["agents"]["claude"]["status"], "fail")
        self.assertIn(
            "Task-owned session has no structured idle/working Agent state.",
            unknown_state["agents"]["claude"]["notes"],
        )

        def task_reporter_state_run(
            command: list[str],
            **kwargs: object,
        ) -> dict[str, object]:
            result = fake_run(command, **kwargs)
            if command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                payload = json.loads(str(result["stdout"]))
                pane = payload["result"]["panes"][0]
                pane["agent_status"] = "done"
                pane["tokens"] = {
                    "valp_agent_state": "idle",
                    "valp_agent_state_source": "task-owned-launcher",
                    "valp_agent_state_sequence": "1",
                    "valp_agent_state_generation": "1",
                    "valp_agent_session_id": "TASK:claude:review:1",
                }
                return {**result, "stdout": json.dumps(payload)}
            return result

        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
            with patch("valp_cli.workflow.run_command", side_effect=task_reporter_state_run):
                with patch(
                    "valp_cli.workflow.cli_preflight_for_agent",
                    return_value={"status": "pass"},
                ):
                    reporter_state = collect_herdr_preflight(
                        ["claude"],
                        session_bindings={"claude": binding},
                    )
        reporter_record = reporter_state["agents"]["claude"]
        self.assertEqual(reporter_record["status"], "pass")
        self.assertEqual(reporter_record["agent_status"], "idle")
        self.assertEqual(
            reporter_record["agent_status_observation"],
            {
                "status": "bound",
                "source": "task_owned_reporter",
                "state": "idle",
                "native_state": "done",
                "reporter_source": "task-owned-launcher",
                "sequence": 1,
                "session_id": "TASK:claude:review:1",
                "generation": 1,
            },
        )

        def mismatched_reporter_state_run(
            command: list[str],
            **kwargs: object,
        ) -> dict[str, object]:
            result = task_reporter_state_run(command, **kwargs)
            if command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                payload = json.loads(str(result["stdout"]))
                payload["result"]["panes"][0]["tokens"]["valp_agent_state_generation"] = "2"
                return {**result, "stdout": json.dumps(payload)}
            return result

        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
            with patch(
                "valp_cli.workflow.run_command",
                side_effect=mismatched_reporter_state_run,
            ):
                with patch(
                    "valp_cli.workflow.cli_preflight_for_agent",
                    return_value={"status": "pass"},
                ):
                    mismatched_reporter = collect_herdr_preflight(
                        ["claude"],
                        session_bindings={"claude": binding},
                    )
        self.assertEqual(mismatched_reporter["agents"]["claude"]["status"], "fail")
        self.assertIn(
            "Task-owned structured Agent state conflicts with the accepted binding: generation",
            mismatched_reporter["agents"]["claude"]["notes"],
        )

        def cross_task_reporter_state_run(
            command: list[str],
            **kwargs: object,
        ) -> dict[str, object]:
            result = task_reporter_state_run(command, **kwargs)
            if command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                payload = json.loads(str(result["stdout"]))
                payload["result"]["panes"][0]["tokens"]["valp_agent_session_id"] = (
                    "OTHER-TASK:claude:review:1"
                )
                return {**result, "stdout": json.dumps(payload)}
            return result

        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
            with patch(
                "valp_cli.workflow.run_command",
                side_effect=cross_task_reporter_state_run,
            ):
                with patch(
                    "valp_cli.workflow.cli_preflight_for_agent",
                    return_value={"status": "pass"},
                ):
                    cross_task_reporter = collect_herdr_preflight(
                        ["claude"],
                        session_bindings={"claude": binding},
                    )
        self.assertEqual(cross_task_reporter["agents"]["claude"]["status"], "fail")
        self.assertIn(
            "Task-owned structured Agent state conflicts with the accepted binding: session_id",
            cross_task_reporter["agents"]["claude"]["notes"],
        )

    def test_bound_agent_preflight_uses_structured_model_metadata(self) -> None:
        project_root = Path("/example/project")
        binding = {
            "agent": "hermes",
            "generation": 1,
            "ownership": {"scope": "task", "task_id": "TASK", "project_identity": "sha256:test"},
            "context": {"cwd": str(project_root)},
            "launch": {"argv": ["/verified/hermes"]},
            "runtime_identity": {
                "pane_id": "pane-hermes",
                "terminal_id": "terminal-hermes",
                "workspace_id": "workspace-hermes",
                "tab_id": "tab-hermes",
                "token": "sha256:" + ("2" * 64),
            },
        }

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent start <name> -- <argv...>\nherdr agent wait <target> --status <state>"
            elif command[1:] == ["workspace", "--help"]:
                stdout = "herdr workspace create [--cwd PATH] [--no-focus]"
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane move <pane> --new-tab\nherdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
            elif command[1:] == ["status", "--json"]:
                stdout = '{"client":{"version":"0.7.4"},"server":{"version":"0.7.4"}}'
            elif command[1:] == ["pane", "list", "--workspace", "workspace-hermes"]:
                stdout = json.dumps({"result": {"panes": [{
                    "agent": "hermes",
                    "agent_status": "idle",
                    "cwd": str(project_root),
                    "pane_id": "pane-hermes",
                    "terminal_id": "terminal-hermes",
                    "workspace_id": "workspace-hermes",
                    "tab_id": "tab-hermes",
                    "tokens": {
                        "active_model_id": "example-model-2026",
                        "model_provider": "Example Provider",
                    },
                }]}})
            elif command[1:3] == ["pane", "process-info"]:
                stdout = '{"result":{"process_info":{"foreground_process_group_id":42}}}'
            elif command[1:3] == ["pane", "layout"]:
                stdout = '{"result":{"layout":{"panes":[{"pane_id":"pane-hermes","rect":{"width":120,"height":40}}]}}}'
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
            with patch("valp_cli.workflow.run_command", side_effect=fake_run):
                with patch(
                    "valp_cli.workflow.cli_preflight_for_agent",
                    return_value={"status": "pass"},
                ):
                    preflight = collect_herdr_preflight(
                        ["hermes"],
                        session_bindings={"hermes": binding},
                    )

        self.assertEqual(preflight["agents"]["hermes"]["model_probe"]["status"], "observed")
        self.assertEqual(
            preflight["agents"]["hermes"]["model_probe"]["model"],
            {
                "model_id": "example-model-2026",
                "provider": "Example Provider",
                "reasoning_mode": "unknown",
                "confidence": "high",
            },
        )
    def test_launch_argv_resolves_bare_entrypoint_before_daemon_handoff(self) -> None:
        resolved_entrypoint = str((Path(sys.executable).parent / "build-agent").resolve())
        capabilities = {
            "agents": {
                "build-agent": {
                    "runtime": {"launch_argv": ["build-agent", "--profile", "review"]}
                }
            }
        }

        with patch(
            "valp_cli.workflow.shutil.which",
            return_value=resolved_entrypoint,
        ) as which:
            argv = herdr_launch_argv_for("build-agent", capabilities)

        self.assertEqual(
            argv,
            [resolved_entrypoint, "--profile", "review"],
        )
        which.assert_called_once_with("build-agent")

    def test_launch_argv_has_no_built_in_agent_command_fallback(self) -> None:
        with patch("valp_cli.workflow.shutil.which") as which:
            argv = herdr_launch_argv_for(
                "claude",
                {"agents": {"claude": {}}},
            )

        self.assertEqual(argv, [])
        which.assert_not_called()

    def test_launch_argv_fails_closed_for_relative_path_entrypoint(self) -> None:
        capabilities = {
            "agents": {
                "claude": {
                    "runtime": {"launch_argv": ["./bin/claude"]}
                }
            }
        }

        with patch("valp_cli.workflow.shutil.which") as which:
            with self.assertRaisesRegex(
                HerdrSubmissionError,
                "launch executable './bin/claude' is not absolute",
            ):
                herdr_launch_argv_for("claude", capabilities)

        which.assert_not_called()

    def test_launch_argv_fails_closed_when_absolute_entrypoint_is_not_executable(self) -> None:
        missing_entrypoint = str((Path(sys.executable).parent / "missing-claude").resolve())
        capabilities = {
            "agents": {
                "claude": {
                    "runtime": {"launch_argv": [missing_entrypoint]}
                }
            }
        }

        with patch("valp_cli.workflow.shutil.which", return_value=None) as which:
            with self.assertRaisesRegex(
                HerdrSubmissionError,
                "to an executable absolute path",
            ):
                herdr_launch_argv_for("claude", capabilities)

        which.assert_called_once_with(missing_entrypoint)

    def test_provision_creates_task_owned_session_instead_of_using_unrelated_pane(self) -> None:
        calls: list[list[str]] = []
        project_root = Path("/example/project").resolve()

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent start <name> [--cwd PATH] -- <argv...>"
            elif command[1:3] == ["workspace", "create"]:
                stdout = json.dumps(
                    {
                        "result": {
                            "type": "workspace_created",
                            "workspace": {
                                "workspace_id": "workspace-owned",
                                "label": command[command.index("--label") + 1],
                            },
                        }
                    }
                )
            elif command[1:3] == ["agent", "start"]:
                stdout = json.dumps(
                    {
                        "result": {
                            "type": "agent_started",
                            "agent": {
                                "agent": "codex",
                                "name": command[3],
                                "pane_id": "pane-owned",
                                "terminal_id": "terminal-owned",
                                "workspace_id": "workspace-owned",
                                "tab_id": "tab-owned",
                                "cwd": str(project_root),
                                "agent_status": "idle",
                                "focused": False,
                                "revision": 1,
                            },
                            "argv": ["codex"],
                        }
                    }
                )
            elif command[1:3] == ["pane", "move"]:
                stdout = '{"result":{"type":"pane_moved","pane_id":"pane-owned"}}'
            elif command[1:3] == ["pane", "get"]:
                stdout = json.dumps(
                    {
                        "result": {
                            "type": "pane_info",
                            "pane": {
                                "agent": "codex",
                                "label": next(
                                    call[3]
                                    for call in calls
                                    if call[1:3] == ["agent", "start"]
                                ),
                                "pane_id": "pane-owned",
                                "terminal_id": "terminal-owned",
                                "workspace_id": "workspace-owned",
                                "tab_id": "tab-agent",
                                "cwd": str(project_root),
                                "agent_status": "idle",
                                "focused": False,
                                "revision": 2,
                            },
                        }
                    }
                )
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        binding = provision_herdr_agent_session(
            "/test/herdr",
            task_id="TASK-OWNED-SESSION",
            agent="codex",
            project_root=project_root,
            launch_argv=["codex"],
            existing_binding=None,
            run_command=fake_run,
        )

        start = next(command for command in calls if command[1:3] == ["agent", "start"])
        self.assertIn("--workspace", start)
        self.assertEqual(start[start.index("--workspace") + 1], "workspace-owned")
        self.assertIn("--no-focus", start)
        self.assertEqual(start[-2:], ["--", "codex"])
        move = next(command for command in calls if command[1:3] == ["pane", "move"])
        self.assertEqual(move[3:6], ["pane-owned", "--new-tab", "--workspace"])
        self.assertEqual(move[6], "workspace-owned")
        self.assertIn("--no-focus", move)
        self.assertEqual(binding["ownership"]["scope"], "task")
        self.assertEqual(binding["ownership"]["task_id"], "TASK-OWNED-SESSION")
        self.assertEqual(binding["context"]["cwd"], str(project_root))
        self.assertEqual(binding["runtime_scope"]["kind"], "workspace")
        self.assertEqual(binding["runtime_scope"]["ownership"], "task")
        self.assertEqual(binding["runtime_scope"]["workspace_id"], "workspace-owned")
        self.assertEqual(binding["runtime_identity"]["pane_id"], "pane-owned")
        self.assertEqual(binding["runtime_identity"]["terminal_id"], "terminal-owned")
        self.assertEqual(binding["runtime_identity"]["tab_id"], "tab-agent")
        self.assertIs(binding["focused_at_provisioning"], False)
        self.assertEqual(binding["lifecycle"], "provisioned")

        def focused_run(command: list[str], **kwargs: object) -> dict[str, object]:
            result = fake_run(command, **kwargs)
            if command[1:3] == ["pane", "get"]:
                payload = json.loads(str(result["stdout"]))
                payload["result"]["pane"]["focused"] = True
                result = {**result, "stdout": json.dumps(payload)}
            return result

        with self.assertRaisesRegex(
            HerdrSubmissionError,
            "did not prove a non-focused pane",
        ):
            provision_herdr_agent_session(
                "/test/herdr",
                task_id="TASK-OWNED-SESSION",
                agent="codex",
                project_root=project_root,
                launch_argv=["codex"],
                existing_binding=None,
                run_command=focused_run,
            )

    def test_provision_retries_incomplete_task_owned_runtime_identity(self) -> None:
        project_root = Path("/example/project").resolve()
        session_name = "valp-" + hashlib.sha256(
            f"{project_root}\0TASK-OWNED-SESSION\0codex".encode("utf-8")
        ).hexdigest()[:16] + "-codex"
        pane_get_attempts = 0

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal pane_get_attempts
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent start <name> [--cwd PATH] -- <argv...>"
            elif command[1:3] == ["workspace", "create"]:
                stdout = json.dumps({
                    "result": {
                        "workspace": {
                            "workspace_id": "workspace-owned",
                            "label": command[command.index("--label") + 1],
                        }
                    }
                })
            elif command[1:3] == ["agent", "start"]:
                stdout = json.dumps({
                    "result": {
                        "type": "agent_started",
                        "agent": {"pane_id": "pane-owned"},
                        "argv": ["codex"],
                    }
                })
            elif command[1:3] == ["pane", "move"]:
                stdout = '{"result":{"type":"pane_moved","pane_id":"pane-owned"}}'
            elif command[1:3] == ["pane", "get"]:
                pane_get_attempts += 1
                pane = {
                    "pane_id": "pane-owned",
                    "terminal_id": "terminal-owned",
                    "workspace_id": "workspace-owned",
                    "tab_id": "tab-owned",
                    "cwd": str(project_root),
                    "focused": False,
                }
                if pane_get_attempts > 1:
                    pane.update({
                        "agent": "codex",
                        "label": session_name,
                    })
                stdout = json.dumps({"result": {"pane": pane}})
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        binding = provision_herdr_agent_session(
            "/test/herdr",
            task_id="TASK-OWNED-SESSION",
            agent="codex",
            project_root=project_root,
            launch_argv=["codex"],
            existing_binding=None,
            run_command=fake_run,
            readiness_attempts=2,
            readiness_interval_seconds=0,
        )

        self.assertEqual(pane_get_attempts, 2)
        self.assertEqual(binding["runtime_identity"]["pane_id"], "pane-owned")

    def test_provision_reports_launched_agent_when_runtime_detection_is_missing(self) -> None:
        project_root = Path("/example/project").resolve()
        session_name = "valp-" + hashlib.sha256(
            f"{project_root}\0TASK-REPORT-AGENT\0qwen".encode("utf-8")
        ).hexdigest()[:16] + "-qwen"
        agent_reported = False
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal agent_reported
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent start <name> [--cwd PATH] -- <argv...>"
            elif command[1:3] == ["workspace", "create"]:
                stdout = json.dumps({
                    "result": {
                        "workspace": {
                            "workspace_id": "workspace-owned",
                            "label": command[command.index("--label") + 1],
                        }
                    }
                })
            elif command[1:3] == ["agent", "start"]:
                stdout = json.dumps({
                    "result": {
                        "type": "agent_started",
                        "agent": {"pane_id": "pane-owned"},
                        "argv": ["qwen"],
                    }
                })
            elif command[1:3] == ["pane", "move"]:
                stdout = '{"result":{"type":"pane_moved","pane_id":"pane-owned"}}'
            elif command[1:3] == ["pane", "report-agent"]:
                agent_reported = True
                stdout = '{"result":{"type":"pane_agent_reported"}}'
            elif command[1:3] == ["pane", "get"]:
                pane = {
                    "label": session_name,
                    "pane_id": "pane-owned",
                    "terminal_id": "terminal-owned",
                    "workspace_id": "workspace-owned",
                    "tab_id": "tab-owned",
                    "cwd": str(project_root),
                    "focused": False,
                }
                if agent_reported:
                    pane["agent"] = "qwen"
                stdout = json.dumps({"result": {"pane": pane}})
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        binding = provision_herdr_agent_session(
            "/test/herdr",
            task_id="TASK-REPORT-AGENT",
            agent="qwen",
            project_root=project_root,
            launch_argv=["qwen"],
            existing_binding=None,
            run_command=fake_run,
            readiness_attempts=2,
            readiness_interval_seconds=0,
        )

        report = next(command for command in calls if command[1:3] == ["pane", "report-agent"])
        self.assertEqual(report[3], "pane-owned")
        self.assertEqual(report[report.index("--agent") + 1], "qwen")
        self.assertEqual(report[report.index("--state") + 1], "unknown")
        self.assertEqual(binding["runtime_identity"]["pane_id"], "pane-owned")

    def test_provision_reuses_only_the_exact_task_owned_runtime_identity(self) -> None:
        project_root = Path("/example/project").resolve()
        project_identity = "sha256:" + hashlib.sha256(
            str(project_root).encode("utf-8")
        ).hexdigest()
        session_name = "valp-" + hashlib.sha256(
            f"{project_root}\0TASK-OWNED-REUSE\0codex".encode("utf-8")
        ).hexdigest()[:16] + "-codex"
        calls: list[list[str]] = []
        pane_list_stdout_limits: list[object] = []
        existing = {
            "agent": "codex",
            "session_name": session_name,
            "generation": 3,
            "ownership": {
                "scope": "task",
                "task_id": "TASK-OWNED-REUSE",
                "project_identity": project_identity,
            },
            "context": {"cwd": str(project_root)},
            "launch": {"argv": ["codex"]},
            "focused_at_provisioning": False,
            "runtime_scope": {
                "kind": "workspace",
                "ownership": "task",
                "workspace_id": "workspace-owned",
                "label": "valp-task-owned-reuse-codex-g3",
            },
            "runtime_identity": {
                "pane_id": "pane-owned",
                "terminal_id": "terminal-owned",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-owned",
                "token": "sha256:40aff8c31759f422dac72eab7be0c3135bdf304667efa01524582d6e279eb2e9",
            },
            "lifecycle": "provisioned",
            "dispatch_eligible": True,
        }

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                pane_list_stdout_limits.append(_kwargs.get("stdout_limit"))
                stdout = json.dumps(
                    {
                        "result": {
                            "panes": [
                                {
                                    "agent": "codex",
                                    "name": "user-codex",
                                    "pane_id": "pane-unrelated",
                                    "terminal_id": "terminal-unrelated",
                                    "workspace_id": "workspace-user",
                                    "tab_id": "tab-user",
                                    "cwd": "/tmp",
                                },
                                {
                                    "agent": "codex",
                                    "name": session_name,
                                    "pane_id": "pane-owned",
                                    "terminal_id": "terminal-owned",
                                    "workspace_id": "workspace-owned",
                                    "tab_id": "tab-owned",
                                    "cwd": str(project_root),
                                },
                            ]
                        }
                    }
                )
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            raise AssertionError(f"unexpected command: {command}")

        binding = provision_herdr_agent_session(
            "/test/herdr",
            task_id="TASK-OWNED-REUSE",
            agent="codex",
            project_root=project_root,
            launch_argv=["codex"],
            existing_binding=existing,
            run_command=fake_run,
        )

        self.assertEqual(binding["runtime_identity"]["pane_id"], "pane-owned")
        self.assertEqual(binding["generation"], 3)
        self.assertEqual(binding["lifecycle"], "reused")
        self.assertEqual(
            pane_list_stdout_limits,
            [HERDR_PANE_LIST_STDOUT_LIMIT],
        )
        self.assertFalse(any(command[1:3] == ["agent", "start"] for command in calls))

        reused_after_capability_drift = provision_herdr_agent_session(
            "/test/herdr",
            task_id="TASK-OWNED-REUSE",
            agent="codex",
            project_root=project_root,
            launch_argv=["codex-metadata-launcher"],
            existing_binding=existing,
            run_command=fake_run,
        )
        self.assertEqual(reused_after_capability_drift["generation"], 3)
        self.assertEqual(reused_after_capability_drift["lifecycle"], "reused")
        self.assertEqual(reused_after_capability_drift["launch"], {"argv": ["codex"]})

        with self.assertRaisesRegex(
            HerdrSubmissionError,
            "while the bound session is present",
        ):
            provision_herdr_agent_session(
                "/test/herdr",
                task_id="TASK-OWNED-REUSE",
                agent="codex",
                project_root=project_root,
                launch_argv=["codex-metadata-launcher"],
                existing_binding=existing,
                run_command=fake_run,
                allow_launch_argv_change=True,
            )

        conflicting_context = {**existing, "context": {"cwd": "/example/other"}}
        with self.assertRaisesRegex(
            HerdrSubmissionError,
            r"binding metadata conflicts: context",
        ):
            provision_herdr_agent_session(
                "/test/herdr",
                task_id="TASK-OWNED-REUSE",
                agent="codex",
                project_root=project_root,
                launch_argv=["codex"],
                existing_binding=conflicting_context,
                run_command=fake_run,
            )

        for missing_field in ("cwd", "agent"):
            with self.subTest(missing_field=missing_field):
                def incomplete_run(
                    command: list[str],
                    **kwargs: object,
                ) -> dict[str, object]:
                    result = fake_run(command, **kwargs)
                    payload = json.loads(str(result["stdout"]))
                    payload["result"]["panes"][1].pop(missing_field, None)
                    return {**result, "stdout": json.dumps(payload)}

                with self.assertRaisesRegex(
                    HerdrSubmissionError,
                    "runtime identity conflicts",
                ):
                    provision_herdr_agent_session(
                        "/test/herdr",
                        task_id="TASK-OWNED-REUSE",
                        agent="codex",
                        project_root=project_root,
                        launch_argv=["codex"],
                        existing_binding=existing,
                        run_command=incomplete_run,
                    )

    def test_session_ledger_reuses_live_binding_when_capability_launch_drifts(self) -> None:
        task_id = "TASK-OWNED-LEDGER-REUSE"
        agent = "claude"
        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            task = root / ".herdr-loop" / "tasks" / task_id
            task.mkdir(parents=True)
            project_identity = "sha256:" + hashlib.sha256(
                str(root).encode("utf-8")
            ).hexdigest()
            owner_digest = hashlib.sha256(
                f"{root}\0{task_id}\0{agent}".encode("utf-8")
            ).hexdigest()
            runtime_identity = {
                "pane_id": "pane-owned",
                "terminal_id": "terminal-owned",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-owned",
            }
            identity_token = "sha256:" + hashlib.sha256(
                json.dumps(
                    runtime_identity,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            binding = {
                "agent": agent,
                "session_name": f"valp-{owner_digest[:16]}-{agent}",
                "generation": 1,
                "ownership": {
                    "scope": "task",
                    "task_id": task_id,
                    "project_identity": project_identity,
                },
                "context": {"cwd": str(root)},
                "launch": {"argv": ["/test/accepted-launcher"]},
                "focused_at_provisioning": False,
                "runtime_scope": {
                    "kind": "workspace",
                    "ownership": "task",
                    "workspace_id": "workspace-owned",
                    "label": "valp-task-owned-ledger-reuse-claude-g1",
                },
                "runtime_identity": {
                    **runtime_identity,
                    "token": identity_token,
                },
                "lifecycle": "provisioned",
                "dispatch_eligible": True,
            }
            projection = {
                "schema_version": "valp-agent-sessions.v1",
                "task_id": task_id,
                "adapter": "herdr",
                "status": "ready",
                "bindings": {agent: binding},
                "updated_at": "2026-07-27T00:00:00Z",
            }
            receipt = {
                "schema_version": "valp-agent-session-receipt.v1",
                "adapter": "herdr",
                "task_id": task_id,
                "event_sequence": 1,
                "ts": "2026-07-27T00:00:00Z",
                "agent": agent,
                "event": "agent_session_provisioned",
                "binding_ref": "agent-sessions.json",
                "generation": 1,
                "identity_token": identity_token,
                "ownership": binding["ownership"],
                "context": binding["context"],
                "launch": binding["launch"],
                "focused_at_provisioning": False,
                "runtime_scope": binding["runtime_scope"],
                "runtime_identity": binding["runtime_identity"],
            }
            (task / "agent-sessions.json").write_text(
                json.dumps(projection),
                encoding="utf-8",
            )
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(receipt) + "\n",
                encoding="utf-8",
            )
            capabilities = {
                "agents": {
                    agent: {
                        "runtime": {
                            "launch_argv": ["current-capability-launcher"],
                        }
                    }
                }
            }

            def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
                calls.append(command)
                if command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                    return {
                        "ok": True,
                        "exit_code": 0,
                        "stdout": json.dumps(
                            {
                                "result": {
                                    "panes": [
                                        {
                                            "agent": agent,
                                            "name": binding["session_name"],
                                            **runtime_identity,
                                            "cwd": str(root),
                                        }
                                    ]
                                }
                            }
                        ),
                        "stderr": "",
                    }
                raise AssertionError(f"unexpected command: {command}")

            def resolve_command(name: str) -> str | None:
                if name == "herdr":
                    return "/test/herdr"
                if name == "current-capability-launcher":
                    return "/test/current-capability-launcher"
                return None

            with patch("valp_cli.workflow.shutil.which", side_effect=resolve_command):
                with patch("valp_cli.workflow.run_command", side_effect=fake_run):
                    reused = ensure_herdr_agent_sessions(
                        root,
                        task,
                        task_id,
                        [agent],
                        capabilities,
                    )

            accepted = reused["bindings"][agent]
            self.assertEqual(accepted["generation"], 1)
            self.assertEqual(accepted["lifecycle"], "reused")
            self.assertEqual(accepted["launch"], binding["launch"])
            self.assertFalse(any(command[1:3] == ["agent", "start"] for command in calls))
            receipts = [
                json.loads(line)
                for line in (task / "agent-session-receipts.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                [record["event"] for record in receipts],
                ["agent_session_provisioned", "agent_session_reused"],
            )
            self.assertEqual(receipts[-1]["generation"], 1)
            self.assertEqual(receipts[-1]["launch"], binding["launch"])

    def test_provision_fails_closed_when_bound_pane_identity_changes(self) -> None:
        project_root = Path("/example/project").resolve()
        project_identity = "sha256:" + hashlib.sha256(
            str(project_root).encode("utf-8")
        ).hexdigest()
        session_name = "valp-" + hashlib.sha256(
            f"{project_root}\0TASK-OWNED-CONFLICT\0codex".encode("utf-8")
        ).hexdigest()[:16] + "-codex"
        existing = {
            "agent": "codex",
            "session_name": session_name,
            "generation": 1,
            "ownership": {
                "scope": "task",
                "task_id": "TASK-OWNED-CONFLICT",
                "project_identity": project_identity,
            },
            "context": {"cwd": str(project_root)},
            "launch": {"argv": ["codex"]},
            "focused_at_provisioning": False,
            "runtime_scope": {
                "kind": "workspace",
                "ownership": "task",
                "workspace_id": "workspace-owned",
                "label": "valp-task-owned-conflict-codex-g1",
            },
            "runtime_identity": {
                "pane_id": "pane-owned",
                "terminal_id": "terminal-original",
                "workspace_id": "workspace-owned",
                "tab_id": "tab-owned",
                "token": "sha256:original",
            },
            "lifecycle": "provisioned",
            "dispatch_eligible": True,
        }

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["pane", "list", "--workspace", "workspace-owned"]:
                stdout = json.dumps(
                    {
                        "result": {
                            "panes": [
                                {
                                    "agent": "codex",
                                    "name": session_name,
                                    "pane_id": "pane-owned",
                                    "terminal_id": "terminal-replacement",
                                    "workspace_id": "workspace-owned",
                                    "tab_id": "tab-owned",
                                    "cwd": str(project_root),
                                }
                            ]
                        }
                    }
                )
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            raise AssertionError(f"unexpected command: {command}")

        with self.assertRaisesRegex(HerdrSubmissionError, "runtime identity conflicts"):
            provision_herdr_agent_session(
                "/test/herdr",
                task_id="TASK-OWNED-CONFLICT",
                agent="codex",
                project_root=project_root,
                launch_argv=["codex"],
                existing_binding=existing,
                run_command=fake_run,
            )

    def test_provision_replaces_absent_owned_session_with_next_generation(self) -> None:
        project_root = Path("/example/project").resolve()
        project_identity = "sha256:" + hashlib.sha256(
            str(project_root).encode("utf-8")
        ).hexdigest()
        session_name = "valp-" + hashlib.sha256(
            f"{project_root}\0TASK-OWNED-REPLACE\0codex".encode("utf-8")
        ).hexdigest()[:16] + "-codex"
        calls: list[list[str]] = []
        existing = {
            "agent": "codex",
            "session_name": session_name,
            "generation": 1,
            "ownership": {
                "scope": "task",
                "task_id": "TASK-OWNED-REPLACE",
                "project_identity": project_identity,
            },
            "context": {"cwd": str(project_root)},
            "launch": {"argv": ["codex-old"]},
            "focused_at_provisioning": False,
            "runtime_scope": {
                "kind": "workspace",
                "ownership": "task",
                "workspace_id": "workspace-old",
                "label": "valp-task-owned-replace-codex-g1",
            },
            "runtime_identity": {
                "pane_id": "pane-old",
                "terminal_id": "terminal-old",
                "workspace_id": "workspace-old",
                "tab_id": "tab-old",
                "token": "sha256:" + ("0" * 64),
            },
            "lifecycle": "provisioned",
            "dispatch_eligible": True,
        }

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:] == ["pane", "list", "--workspace", "workspace-old"]:
                return {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": json.dumps(
                        {
                            "error": {
                                "code": "workspace_not_found",
                                "message": "workspace workspace-old not found",
                            },
                            "id": "cli:pane:list",
                        }
                    ),
                }
            elif command[1:3] == ["workspace", "create"]:
                stdout = json.dumps({
                    "result": {
                        "type": "workspace_created",
                        "workspace": {
                            "workspace_id": "workspace-new",
                            "label": command[command.index("--label") + 1],
                        },
                    }
                })
            elif command[1:] == ["agent", "--help"]:
                stdout = "herdr agent start <name> [--cwd PATH] -- <argv...>"
            elif command[1:3] == ["agent", "start"]:
                stdout = json.dumps({
                    "result": {
                        "type": "agent_started",
                        "agent": {
                            "agent": "codex",
                            "name": command[3],
                            "pane_id": "pane-new",
                            "terminal_id": "terminal-new",
                            "workspace_id": "workspace-new",
                            "tab_id": "tab-new",
                            "cwd": str(project_root),
                        },
                        "argv": ["codex-metadata-launcher"],
                    }
                })
            elif command[1:3] == ["pane", "move"]:
                stdout = '{"result":{"type":"pane_moved","pane_id":"pane-new"}}'
            elif command[1:3] == ["pane", "get"]:
                stdout = json.dumps({
                    "result": {
                        "type": "pane_info",
                        "pane": {
                            "agent": "codex",
                            "label": session_name,
                            "pane_id": "pane-new",
                            "terminal_id": "terminal-new",
                            "workspace_id": "workspace-new",
                            "tab_id": "tab-agent",
                            "cwd": str(project_root),
                            "focused": False,
                        },
                    }
                })
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        replacement = provision_herdr_agent_session(
            "/test/herdr",
            task_id="TASK-OWNED-REPLACE",
            agent="codex",
            project_root=project_root,
            launch_argv=["codex-metadata-launcher"],
            existing_binding=existing,
            run_command=fake_run,
            allow_launch_argv_change=True,
        )

        self.assertEqual(replacement["generation"], 2)
        self.assertEqual(replacement["runtime_identity"]["pane_id"], "pane-new")
        self.assertEqual(replacement["lifecycle"], "provisioned")
        self.assertEqual(
            replacement["launch"]["argv"],
            ["codex-metadata-launcher"],
        )
        start = next(command for command in calls if command[1:3] == ["agent", "start"])
        self.assertIn("--env", start)
        self.assertEqual(
            start[start.index("--env") + 1],
            "VALP_AGENT_BINDING_GENERATION=2",
        )

    def test_session_provisioning_rejects_non_contiguous_receipt_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task = root / ".herdr-loop" / "tasks" / "TASK-SESSION-LEDGER"
            task.mkdir(parents=True)
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps({
                    "schema_version": "valp-agent-session-receipt.v1",
                    "task_id": "TASK-SESSION-LEDGER",
                    "event_sequence": 2,
                    "event": "agent_session_provisioned",
                    "binding_ref": "agent-sessions.json",
                    "generation": 1,
                    "identity_token": "sha256:" + ("0" * 64),
                }) + "\n",
                encoding="utf-8",
            )

            with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                with self.assertRaisesRegex(HerdrSubmissionError, "receipt ledger"):
                    ensure_herdr_agent_sessions(
                        root,
                        task,
                        "TASK-SESSION-LEDGER",
                        ["codex"],
                        TEST_CAPABILITIES,
                    )

    def test_session_provisioning_rejects_receipt_from_another_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-SESSION-ADAPTER-CONFLICT"
            task = root / ".herdr-loop" / "tasks" / task_id
            task.mkdir(parents=True)
            projection = json.loads(
                (Path(__file__).parents[1] / "examples" / "agent-sessions.json").read_text(
                    encoding="utf-8"
                )
            )
            projection["task_id"] = task_id
            projection["bindings"]["example-agent"]["ownership"]["task_id"] = task_id
            receipt = json.loads(
                (Path(__file__).parents[1] / "examples" / "agent-session-receipts.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            receipt["adapter"] = "another-adapter"
            receipt["task_id"] = task_id
            receipt["ownership"]["task_id"] = task_id
            (task / "agent-sessions.json").write_text(
                json.dumps(projection),
                encoding="utf-8",
            )
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(receipt) + "\n",
                encoding="utf-8",
            )

            with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                with self.assertRaisesRegex(
                    HerdrSubmissionError,
                    "another adapter",
                ):
                    ensure_herdr_agent_sessions(
                        root,
                        task,
                        task_id,
                        ["example-agent"],
                        {"agents": {"example-agent": {"runtime": {"launch_argv": ["example-agent"]}}}},
                    )

    def test_session_provisioning_rejects_projection_without_provisioning_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-SESSION-PROVENANCE"
            task = root / ".herdr-loop" / "tasks" / task_id
            task.mkdir(parents=True)
            projection = json.loads(
                (Path(__file__).parents[1] / "examples" / "agent-sessions.json").read_text(
                    encoding="utf-8"
                )
            )
            projection["task_id"] = task_id
            example_binding = next(iter(projection["bindings"].values()))
            example_binding["ownership"]["task_id"] = task_id
            (task / "agent-sessions.json").write_text(
                json.dumps(projection),
                encoding="utf-8",
            )

            with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                with self.assertRaisesRegex(HerdrSubmissionError, "provisioning generations"):
                    ensure_herdr_agent_sessions(
                        root,
                        task,
                        task_id,
                        ["codex"],
                        TEST_CAPABILITIES,
                    )

    def test_session_provisioning_rejects_receipts_without_projection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-SESSION-ORPHAN-RECEIPT"
            task = root / ".herdr-loop" / "tasks" / task_id
            task.mkdir(parents=True)
            receipt = json.loads(
                (Path(__file__).parents[1] / "examples" / "agent-session-receipts.jsonl").read_text(
                    encoding="utf-8"
                )
            )
            receipt["task_id"] = task_id
            receipt["ownership"]["task_id"] = task_id
            (task / "agent-session-receipts.jsonl").write_text(
                json.dumps(receipt) + "\n",
                encoding="utf-8",
            )

            with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
                with self.assertRaisesRegex(HerdrSubmissionError, "without an Agent session projection"):
                    ensure_herdr_agent_sessions(
                        root,
                        task,
                        task_id,
                        ["codex"],
                        TEST_CAPABILITIES,
                    )

    def publish_routed_task(self, root: Path, task_id: str) -> Path:
        declaration = {
            "schema_version": "valp-assignment-declaration.v1",
            "declaration_id": f"test-declaration-{task_id}",
            "task_id": task_id,
            "declared_at": "2026-07-23T10:00:00Z",
            "leader": {
                "agent_id": "codex",
                "selected_by": "user",
                "selection_ref": f"test-user-selection:{task_id}",
            },
            "assignments": {
                "coordinator": "codex",
                "reviewer": "codex",
            },
            "reasons": {
                "coordinator": "Test Leader explicitly accepted the runtime coordinator role.",
                "reviewer": "Test Leader declared the bounded review role.",
            },
        }
        task_dir = publish_task(
            root,
            task_id,
            "Coordinate a bounded runtime check.",
            profile="generic-analysis",
            runtime="herdr",
        )
        route_task(
            root,
            task_id,
            runtime="herdr",
            assignment_declaration=declaration,
        )
        return task_dir

    def test_existing_invalid_submission_receipt_conflicts_instead_of_being_replaced(self) -> None:
        task_id = "TASK-RECEIPT-CONFLICT"
        expected = ["agents/codex/self-review.md"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            dependency = {
                "work_items": [
                    {
                        "agent": "codex",
                        "role": "coordinator",
                        "work_item_id": "coordinator:codex",
                        "dispatch_id": f"{task_id}:coordinator:1",
                        "dispatch_generation": 1,
                        "expected_refs": expected,
                    }
                ]
            }
            (directory / "submission-dependencies.json").write_text(
                json.dumps(dependency),
                encoding="utf-8",
            )
            invalid = {
                "schema_version": "valp-dispatch-receipt.v2",
                "receipt_id": "receipt-invalid-proof",
                "task_id": task_id,
                "event_sequence": 1,
                "agent": "codex",
                "role": "coordinator",
                "work_item_id": "coordinator:codex",
                "dispatch_id": f"{task_id}:coordinator:1",
                "dispatch_generation": 1,
                "event": "dispatch_submitted",
                "dispatch_ref": "agents/codex/dispatch.md",
                "expected_refs": expected,
                "proof": {"note": "accepted"},
            }
            ledger = directory / "dispatch-receipts.jsonl"
            ledger.write_text(json.dumps(invalid) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(SystemExit, "conflicts"):
                write_herdr_submission_receipt(
                    directory,
                    task_id,
                    "codex",
                    "coordinator",
                    expected,
                    herdr_invocation_proof(),
                )

            self.assertEqual(len(ledger.read_text(encoding="utf-8").splitlines()), 1)

    def test_existing_concrete_submission_receipt_conflicts_on_changed_runtime_proof(self) -> None:
        task_id = "TASK-CONCRETE-RECEIPT-CONFLICT"
        expected = ["agents/codex/self-review.md"]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            dependency = {
                "work_items": [
                    {
                        "agent": "codex",
                        "role": "coordinator",
                        "work_item_id": "coordinator:codex",
                        "dispatch_id": f"{task_id}:coordinator:1",
                        "dispatch_generation": 1,
                        "expected_refs": expected,
                    }
                ]
            }
            (directory / "submission-dependencies.json").write_text(
                json.dumps(dependency),
                encoding="utf-8",
            )
            original_proof = herdr_invocation_proof()
            write_herdr_submission_receipt(
                directory,
                task_id,
                "codex",
                "coordinator",
                expected,
                original_proof,
            )
            ledger = directory / "dispatch-receipts.jsonl"
            original_bytes = ledger.read_bytes()

            with self.assertRaisesRegex(SystemExit, "conflicts"):
                write_herdr_submission_receipt(
                    directory,
                    task_id,
                    "codex",
                    "coordinator",
                    expected,
                    {
                        **original_proof,
                        "submission_proof": {
                            **original_proof["submission_proof"],
                            "state_change_seq": 3,
                        },
                    },
                )

            self.assertEqual(ledger.read_bytes(), original_bytes)

    def test_submission_capability_downgrades_prompt_without_wait_support(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": (
                        "herdr agent get <target>\n"
                        "herdr agent prompt <target> <text>\n"
                        "herdr agent wait <target> --status <state>"
                    ),
                    "stderr": "",
                }
            if command[1:] == ["pane", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": (
                        "herdr pane send-text <pane> <text>\n"
                        "herdr pane send-keys <pane> <key>"
                    ),
                    "stderr": "",
                }
            raise AssertionError(f"unexpected command: {command}")

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)

        self.assertEqual(capability["status"], "warn")
        self.assertEqual(capability["mode"], "pane_send_text_enter")
        self.assertFalse(capability["commands"]["agent_prompt"])

    def test_fallback_keeps_transport_when_working_state_is_unproven(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "herdr agent wait <target> --status <state>",
                    "stderr": "",
                }
            if command[1:] == ["pane", "--help"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>",
                    "stderr": "",
                }
            if command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                return {"ok": True, "exit_code": 0, "stdout": "{}", "stderr": ""}
            if command[1:3] == ["agent", "wait"]:
                return {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "timed out waiting for working",
                }
            raise AssertionError(f"unexpected command: {command}")

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-PROOF-FAILURE",
                target="codex",
                pane_id="pane-1",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=0,
            )

        self.assertEqual(proof["proof_class"], "transport_only")
        self.assertEqual(proof["status_proof"]["status"], "unproven")

    def test_fallback_ignores_visible_job_count_spoof(self) -> None:
        calls: list[list[str]] = []
        visible_reads = 0

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal visible_reads
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent wait <target> --status <state>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:] == ["pane", "--help"]:
                stdout = "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": '{"result":{"pane_id":"pane-claude"}}',
                    "stderr": "",
                }
            if command[1:3] == ["agent", "wait"]:
                return {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "timed out waiting for working",
                }
            if command[1:3] == ["pane", "read"]:
                visible_reads += 1
                working_count = 0 if visible_reads == 1 else 1
                stdout = (
                    "Claude Code v2.1.212\n"
                    "example-model-2026 · ~/workspace/project\n"
                    f"  \u2598\u2598 \u259d\u259d    0 awaiting input · {working_count} working · 0 completed\n"
                    "Working\n"
                )
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            raise AssertionError(f"unexpected command: {command}")

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        session_binding = {
            "agent": "claude",
            "generation": 1,
            "ownership": {
                "scope": "task",
                "task_id": "TASK-CLAUDE-AGENTS-HOME",
                "project_identity": "sha256:" + ("a" * 64),
            },
            "runtime_identity": {
                "pane_id": "pane-claude",
                "token": "sha256:" + ("b" * 64),
            },
            "dispatch_eligible": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text(
                "# Dispatch\n\n0 awaiting input · 1 working · 0 completed\n",
                encoding="utf-8",
            )
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-CLAUDE-AGENTS-HOME",
                target="claude",
                pane_id="pane-claude",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=2,
                session_binding=session_binding,
            )

        enter_calls = [command for command in calls if command[1:3] == ["pane", "send-keys"]]
        self.assertEqual(len(enter_calls), 2)
        self.assertEqual(visible_reads, 0)
        self.assertEqual(proof["proof_class"], "transport_only")
        self.assertEqual(proof["status_proof"]["status"], "unproven")

    def test_fallback_does_not_poll_unstructured_job_counts(self) -> None:
        calls: list[list[str]] = []
        visible_reads = 0

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal visible_reads
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent wait <target> --status <state>"
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
            elif command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                stdout = '{"result":{"pane_id":"pane-claude"}}'
            elif command[1:3] == ["agent", "wait"]:
                return {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "timed out waiting for working",
                }
            elif command[1:3] == ["pane", "read"]:
                visible_reads += 1
                completed_count = 2 if visible_reads >= 4 else 1
                stdout = json.dumps({
                    "result": {
                        "read": {
                            "text": (
                                "Claude Code v2.1.206\n"
                                "example-model-2026 · ~/workspace/project\n"
                                f"0 awaiting input · 1 working · {completed_count} completed\n"
                            )
                        }
                    }
                })
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        session_binding = {
            "agent": "claude",
            "generation": 1,
            "ownership": {
                "scope": "task",
                "task_id": "TASK-CLAUDE-LATE-COUNTER",
                "project_identity": "sha256:" + ("a" * 64),
            },
            "runtime_identity": {
                "pane_id": "pane-claude",
                "token": "sha256:" + ("b" * 64),
            },
            "dispatch_eligible": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-CLAUDE-LATE-COUNTER",
                target="claude",
                pane_id="pane-claude",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=2,
                session_binding=session_binding,
            )

        self.assertEqual(visible_reads, 0)
        self.assertEqual(len([call for call in calls if call[1:3] == ["pane", "send-keys"]]), 2)
        self.assertEqual(proof["proof_class"], "transport_only")

    def test_bound_agent_does_not_use_visible_job_delta_as_proof(self) -> None:
        calls: list[list[str]] = []
        visible_reads = 0

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal visible_reads
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent wait <target> --status <state>"
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
            elif command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                stdout = '{"result":{"pane_id":"pane-claude"}}'
            elif command[1:3] == ["agent", "wait"]:
                return {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "timed out waiting for working",
                }
            elif command[1:3] == ["pane", "read"]:
                visible_reads += 1
                completed_count = 2 if visible_reads >= 8 else 1
                stdout = json.dumps({
                    "result": {
                        "read": {
                            "text": f"0 awaiting input · 1 working · {completed_count} completed\n"
                        }
                    }
                })
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        session_binding = {
            "agent": "claude",
            "generation": 1,
            "ownership": {
                "scope": "task",
                "task_id": "TASK-CLAUDE-PROOF-BUDGET",
                "project_identity": "sha256:" + ("a" * 64),
            },
            "runtime_identity": {
                "pane_id": "pane-claude",
                "token": "sha256:" + ("b" * 64),
            },
            "dispatch_eligible": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-CLAUDE-PROOF-BUDGET",
                target="claude",
                pane_id="pane-claude",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=4,
                session_binding=session_binding,
            )

        self.assertEqual(visible_reads, 0)
        self.assertEqual(len([call for call in calls if call[1:3] == ["agent", "wait"]]), 2)
        self.assertEqual(len([call for call in calls if call[1:3] == ["pane", "send-keys"]]), 2)
        self.assertEqual(proof["proof_class"], "transport_only")

    def test_fallback_ignores_unchanged_claude_agents_home_working_counter(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent wait <target> --status <state>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:] == ["pane", "--help"]:
                stdout = "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                return {"ok": True, "exit_code": 0, "stdout": "{}", "stderr": ""}
            if command[1:3] == ["agent", "wait"]:
                return {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "timed out waiting for working",
                }
            if command[1:3] == ["pane", "read"]:
                stdout = json.dumps({
                    "result": {
                        "read": {
                            "text": "0 awaiting input · 1 working · 0 completed\n"
                        }
                    }
                })
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            raise AssertionError(f"unexpected command: {command}")

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        session_binding = {
            "agent": "claude",
            "generation": 1,
            "ownership": {
                "scope": "task",
                "task_id": "TASK-CLAUDE-UNCHANGED-COUNTER",
                "project_identity": "sha256:" + ("a" * 64),
            },
            "runtime_identity": {
                "pane_id": "pane-claude",
                "token": "sha256:" + ("b" * 64),
            },
            "dispatch_eligible": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-CLAUDE-UNCHANGED-COUNTER",
                target="claude",
                pane_id="pane-claude",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=2,
                session_binding=session_binding,
            )

        self.assertEqual(proof["proof_class"], "transport_only")
        self.assertEqual(proof["status_proof"]["status"], "unproven")

    def test_fallback_ignores_job_delta_without_agent_invocation_proof(self) -> None:
        visible_reads = 0

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal visible_reads
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent wait <target> --status <state>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:] == ["pane", "--help"]:
                stdout = "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                return {"ok": True, "exit_code": 0, "stdout": "{}", "stderr": ""}
            if command[1:3] == ["agent", "wait"]:
                return {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "timed out waiting for working",
                }
            if command[1:3] == ["pane", "read"]:
                visible_reads += 1
                completed_count = 1 if visible_reads == 1 else 2
                stdout = json.dumps({
                    "result": {
                        "read": {
                            "text": f"0 awaiting input · 0 working · {completed_count} completed\n"
                        }
                    }
                })
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            raise AssertionError(f"unexpected command: {command}")

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        session_binding = {
            "agent": "claude",
            "generation": 1,
            "ownership": {
                "scope": "task",
                "task_id": "TASK-CLAUDE-NO-WORKING-JOB",
                "project_identity": "sha256:" + ("a" * 64),
            },
            "runtime_identity": {
                "pane_id": "pane-claude",
                "token": "sha256:" + ("b" * 64),
            },
            "dispatch_eligible": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-CLAUDE-NO-WORKING-JOB",
                target="claude",
                pane_id="pane-claude",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=2,
                session_binding=session_binding,
            )

        self.assertEqual(visible_reads, 0)
        self.assertEqual(proof["proof_class"], "transport_only")

    def test_fallback_retries_enter_after_bounded_working_state_timeout(self) -> None:
        calls: list[list[str]] = []
        wait_attempts = 0

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            nonlocal wait_attempts
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent wait <target> --status <state>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:] == ["pane", "--help"]:
                stdout = "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                return {"ok": True, "exit_code": 0, "stdout": '{"result":{"pane_id":"pane-1"}}', "stderr": ""}
            if command[1:3] == ["agent", "wait"]:
                wait_attempts += 1
                if wait_attempts == 1:
                    return {
                        "ok": False,
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": "timed out waiting for working",
                    }
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": '{"event":"pane.agent_status_changed","data":{"pane_id":"pane-1","agent_status":"working"}}',
                    "stderr": "",
                }
            raise AssertionError(f"unexpected command: {command}")

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-ENTER-RETRY",
                target="claude",
                pane_id="pane-1",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=2,
            )

        enter_calls = [command for command in calls if command[1:3] == ["pane", "send-keys"]]
        self.assertEqual(len(enter_calls), 2)
        self.assertEqual(proof["status_proof"]["enter_attempts"], 2)
        self.assertEqual(proof["status_proof"]["working_attempt"], 2)
        self.assertEqual(proof["status_proof"]["runtime_response"]["pane_id"], "pane-1")

    def test_atomic_agent_prompt_requires_wait_and_identity_bound_state_change(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = (
                    "herdr agent get <target>\n"
                    "herdr agent prompt <target> <text> [--wait] [--until STATUS] [--timeout MS]\n"
                    "herdr agent wait <target> [--until STATUS]"
                )
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane list"
            elif command[1:3] == ["agent", "get"]:
                stdout = json.dumps(
                    {
                        "id": "cli:agent:get",
                        "result": {
                            "type": "agent_info",
                            "agent": {
                                "terminal_id": "term-1",
                                "name": "codex",
                                "agent": "codex",
                                "agent_status": "idle",
                                "workspace_id": "workspace-1",
                                "tab_id": "tab-1",
                                "pane_id": "pane-1",
                                "focused": False,
                                "state_change_seq": 41,
                                "revision": 7,
                            },
                        },
                    }
                )
            elif command[1:3] == ["agent", "prompt"]:
                self.assertEqual(
                    command[-5:],
                    ["--wait", "--until", "working", "--timeout", "1000"],
                )
                stdout = json.dumps(
                    {
                        "id": "cli:agent:prompt",
                        "result": {
                            "type": "agent_prompted",
                            "agent": {
                                "terminal_id": "term-1",
                                "name": "codex",
                                "agent": "codex",
                                "agent_status": "working",
                                "workspace_id": "workspace-1",
                                "tab_id": "tab-1",
                                "pane_id": "pane-1",
                                "focused": False,
                                "state_change_seq": 43,
                                "revision": 11,
                            },
                        },
                    }
                )
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n\nRun the bounded task.\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-ATOMIC",
                target="codex",
                pane_id="pane-1",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=1,
            )

        self.assertEqual(capability["mode"], "agent_prompt")
        self.assertEqual(proof["transport_mode"], "agent_prompt")
        self.assertEqual(
            proof["submission_proof"],
            {
                "kind": "identity_bound_state_change",
                "baseline_state_change_seq": 41,
                "state_change_seq": 43,
                "identity": {
                    "terminal_id": "term-1",
                    "name": "codex",
                    "agent": "codex",
                    "pane_id": "pane-1",
                },
            },
        )
        prompt = next(command for command in calls if command[1:3] == ["agent", "prompt"])
        self.assertEqual(prompt[3], "pane-1")
        self.assertTrue(any(command[1:3] == ["agent", "get"] for command in calls))
        self.assertFalse(any(command[1:3] == ["pane", "send-text"] for command in calls))

    def test_atomic_agent_prompt_rejects_generic_and_fabricated_ids(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                stdout = (
                    "herdr agent get <target>\n"
                    "herdr agent prompt <target> <text> [--wait] [--until STATUS] [--timeout MS]"
                )
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane list"
            elif command[1:3] == ["agent", "get"]:
                stdout = json.dumps(
                    {
                        "id": "cli:agent:get",
                        "result": {
                            "type": "agent_info",
                            "agent": {
                                "terminal_id": "term-1",
                                "name": "codex",
                                "agent": "codex",
                                "agent_status": "idle",
                                "pane_id": "pane-1",
                                "state_change_seq": 7,
                            },
                        },
                    }
                )
            elif command[1:3] == ["agent", "prompt"]:
                stdout = json.dumps(
                    {
                        "id": "generic-response-42",
                        "result": {
                            "submission_id": "fabricated-submission-42",
                            "status": "working",
                        },
                    }
                )
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            with self.assertRaisesRegex(HerdrSubmissionError, "unexpected structured response"):
                submit_herdr_dispatch(
                    "/test/herdr",
                    capability,
                    task_id="TASK-ATOMIC-NO-IDENTITY",
                    target="codex",
                    pane_id="pane-1",
                    dispatch_path=dispatch,
                    run_command=fake_run,
                    proof_seconds=1,
                )

    def test_fallback_uses_resolved_pane_for_working_state_proof(self) -> None:
        calls: list[list[str]] = []

        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            calls.append(command)
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent wait <target> --status <state>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:] == ["pane", "--help"]:
                stdout = "herdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
                return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}
            if command[1:3] in (["pane", "send-text"], ["pane", "send-keys"]):
                return {"ok": True, "exit_code": 0, "stdout": "{}", "stderr": ""}
            if command[1:3] == ["agent", "wait"]:
                return {
                    "ok": True,
                    "exit_code": 0,
                    "stdout": '{"result":{"agent":{"status":"working","agent_session_id":"session-1"}}}',
                    "stderr": "",
                }
            raise AssertionError(f"unexpected command: {command}")

        capability = detect_herdr_submission_capability("/test/herdr", fake_run)
        with tempfile.TemporaryDirectory() as tmp:
            dispatch = Path(tmp) / "dispatch.md"
            dispatch.write_text("# Dispatch\n", encoding="utf-8")
            proof = submit_herdr_dispatch(
                "/test/herdr",
                capability,
                task_id="TASK-EVIDENCE-PROOF",
                target="codex",
                pane_id="pane-1",
                dispatch_path=dispatch,
                run_command=fake_run,
                proof_seconds=0,
            )

        self.assertEqual(proof["status_proof"]["status"], "working")
        wait = next(command for command in calls if command[1:3] == ["agent", "wait"])
        self.assertEqual(wait[3], "pane-1")

    def test_doctor_fails_when_installed_herdr_cannot_submit(self) -> None:
        def fake_preflight(_agents=None, *, runtime=None):
            if runtime == "queue":
                return {"status": "pass", "adapter_class": "daemon_queue"}
            return {
                "status": "fail",
                "adapter_class": "pane_controller",
                "checks": {
                    "submission_transport": {
                        "status": "fail",
                        "mode": "unavailable",
                    }
                },
            }

        with patch("valp_cli.doctor.shutil.which", return_value="/test/herdr"):
            with patch("valp_cli.doctor.collect_runtime_preflight", side_effect=fake_preflight):
                check = next(item for item in runtime_checks() if item.id == "runtime_herdr")

        self.assertEqual(check.status, FAIL)
        self.assertIn("submission", check.message.lower())

    def test_preflight_fails_when_herdr_has_no_submission_transport(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent list\nherdr agent read <target>"
            elif command[1:] == ["workspace", "--help"]:
                stdout = "herdr workspace create [--cwd PATH] [--no-focus]"
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane move <pane> --new-tab\nherdr pane list\nherdr pane read <pane>"
            elif command[1:] == ["status", "--json"]:
                stdout = '{"client":{"version":"0.7.3"},"server":{"version":"0.7.3"}}'
            elif command[1:] == ["pane", "list"]:
                stdout = '{"result":{"panes":[]}}'
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
            with patch("valp_cli.workflow.run_command", side_effect=fake_run):
                preflight = collect_herdr_preflight([])

        transport = preflight["checks"]["submission_transport"]
        self.assertEqual(preflight["status"], "fail")
        self.assertEqual(transport["mode"], "unavailable")
        self.assertIn("Install a HERDR build", transport["message"])

    def test_preflight_fails_when_herdr_cannot_provision_owned_sessions(self) -> None:
        def fake_run(command: list[str], **_kwargs: object) -> dict[str, object]:
            if command[1:] == ["agent", "--help"]:
                stdout = "herdr agent wait <target> --status <state>"
            elif command[1:] == ["workspace", "--help"]:
                stdout = "herdr workspace list"
            elif command[1:] == ["pane", "--help"]:
                stdout = "herdr pane move <pane> --new-tab\nherdr pane send-text <pane> <text>\nherdr pane send-keys <pane> <key>"
            elif command[1:] == ["status", "--json"]:
                stdout = '{"client":{"version":"0.7.4"},"server":{"version":"0.7.4"}}'
            elif command[1:] == ["pane", "list"]:
                stdout = '{"result":{"panes":[]}}'
            else:
                raise AssertionError(f"unexpected command: {command}")
            return {"ok": True, "exit_code": 0, "stdout": stdout, "stderr": ""}

        with patch("valp_cli.workflow.shutil.which", return_value="/test/herdr"):
            with patch("valp_cli.workflow.run_command", side_effect=fake_run):
                preflight = collect_herdr_preflight([])

        provisioning = preflight["checks"]["session_provisioning"]
        self.assertEqual(preflight["status"], "fail")
        self.assertEqual(provisioning["status"], "fail")
        self.assertIn("agent start", provisioning["message"])

    def test_dry_run_does_not_require_owned_session_model_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean_path = str(install_fake_fallback_herdr(root))

            with patch.dict(os.environ, {"PATH": clean_path}, clear=False):
                with patch("valp_cli.workflow.load_local_capabilities", return_value=TEST_CAPABILITIES):
                    with patch("valp_cli.workflow.skill_router_command", return_value=None):
                        task_id = "TASK-HERDR-DRY-RUN"
                        task_dir = publish_task(
                            root,
                            task_id,
                            "Review a bounded runtime change.",
                            profile="generic-analysis",
                            runtime="herdr",
                        )
                        route_task(
                            root,
                            task_id,
                            runtime="herdr",
                            assignment_declaration={
                                "schema_version": "valp-assignment-declaration.v1",
                                "declaration_id": "test-declaration-TASK-HERDR-DRY-RUN",
                                "task_id": task_id,
                                "declared_at": "2026-07-23T10:00:00Z",
                                "leader": {
                                    "agent_id": "codex",
                                    "selected_by": "user",
                                    "selection_ref": "test-user-selection:TASK-HERDR-DRY-RUN",
                                },
                                "assignments": {"reviewer": "codex"},
                                "reasons": {
                                    "reviewer": "Test Leader declared a reviewer-only dry run."
                                },
                            },
                        )
                unknown_model_preflight = {
                    "status": "warn",
                    "checks": {
                        "submission_transport": {
                            "status": "pass",
                            "mode": "pane_send_text_enter",
                            "commands": {
                                "agent_prompt": False,
                                "pane_send_text": True,
                                "pane_send_keys": True,
                                "agent_wait": True,
                            },
                        }
                    },
                    "agents": {
                        "codex": {
                            "status": "warn",
                            "model_probe": {
                                "status": "unavailable",
                                "session_identity": {"status": "unknown"},
                            },
                        }
                    },
                }
                with patch(
                    "valp_cli.workflow.collect_runtime_preflight",
                    return_value=unknown_model_preflight,
                ):
                    commands = dispatch_task(
                        root,
                        "TASK-HERDR-DRY-RUN",
                        agent="codex",
                        role="reviewer",
                        submit=False,
                        runtime="herdr",
                    )

            self.assertEqual(len(commands), 1)
            self.assertIn("agents/codex/dispatch.md", commands[0])
            budget = json.loads(
                (task_dir / "iteration-budget.json").read_text(encoding="utf-8")
            )
            self.assertEqual(budget["status"], "active")
            self.assertFalse((task_dir / "model-identity-dispatch-block.json").exists())

    def test_fallback_submit_records_transport_only_and_degrades_manual(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean_path = str(install_fake_fallback_herdr(root))

            with patch.dict(os.environ, {"PATH": clean_path}, clear=False):
                with patch("valp_cli.workflow.load_local_capabilities", return_value=TEST_CAPABILITIES):
                    with patch("valp_cli.workflow.skill_router_command", return_value=None):
                        task_dir = self.publish_routed_task(root, "TASK-CLEAN-HERDR")
                    with self.assertRaisesRegex(SystemExit, "Manual-degraded"):
                        dispatch_task(
                            root,
                            "TASK-CLEAN-HERDR",
                            agent="codex",
                            role="coordinator",
                            submit=True,
                            runtime="herdr",
                            wait_seconds=0,
                            proof_seconds=1,
                        )

            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            inserted = [receipt for receipt in receipts if receipt.get("event") == "dispatch_inserted"]
            submitted = [receipt for receipt in receipts if receipt.get("event") == "dispatch_submitted"]
            self.assertEqual(len(inserted), 1)
            self.assertEqual(submitted, [])
            self.assertEqual(inserted[0]["proof"]["transport_mode"], "pane_send_text_enter")
            self.assertEqual(inserted[0]["proof"]["pane_id"], "pane-owned")
            self.assertEqual(
                inserted[0]["proof"]["session_binding"]["ref"],
                "agent-sessions.json",
            )
            self.assertEqual(inserted[0]["proof"]["proof_class"], "transport_only")
            sessions = json.loads((task_dir / "agent-sessions.json").read_text(encoding="utf-8"))
            self.assertEqual(sessions["bindings"]["codex"]["ownership"]["scope"], "task")
            self.assertEqual(
                sessions["bindings"]["codex"]["runtime_identity"]["pane_id"],
                "pane-owned",
            )
            session_receipts = [
                json.loads(line)
                for line in (task_dir / "agent-session-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(session_receipts[-1]["event"], "agent_session_provisioned")
            budget = json.loads((task_dir / "iteration-budget.json").read_text(encoding="utf-8"))
            self.assertEqual(budget["status"], "blocked")
            self.assertEqual(budget["stop_reason"], "runtime dispatch failure")

    def test_fallback_evidence_cannot_upgrade_transport_to_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            clean_path = str(install_fake_fallback_herdr(root))
            with patch.dict(os.environ, {"PATH": clean_path}, clear=False):
                with patch("valp_cli.workflow.load_local_capabilities", return_value=TEST_CAPABILITIES):
                    with patch("valp_cli.workflow.skill_router_command", return_value=None):
                        task_dir = self.publish_routed_task(root, "TASK-HERDR-EVIDENCE")
                    evidence = task_dir / "agents" / "codex" / "self-review.md"
                    evidence.write_text("# Self Review\n\nPassed.\n", encoding="utf-8")
                    with self.assertRaisesRegex(SystemExit, "Manual-degraded"):
                        dispatch_task(
                            root,
                            "TASK-HERDR-EVIDENCE",
                            agent="codex",
                            role="coordinator",
                            submit=True,
                            runtime="herdr",
                            wait_seconds=0.01,
                            proof_seconds=1,
                        )

            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
                if '"schema_version"' in line
            ]
            self.assertEqual([receipt["event"] for receipt in receipts], ["dispatch_inserted"])


if __name__ == "__main__":
    unittest.main()
