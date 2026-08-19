from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jsonschema import Draft202012Validator

from valp_cli.cli import main
from valp_cli.control_plane import (
    ControlPlaneError,
    InstallationCore,
    digest_without,
    leader_installation_root,
    write_json,
)
from valp_cli.herdr_adapter import HerdrSubmissionError
from valp_cli.plugins import validate_plugin_manifest
from valp_cli.task_control import init_task, task_state, transition_task
from valp_cli.process_adapter import run_process


ROOT = Path(__file__).resolve().parents[1]


class ControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="valp-control-plane-test-")
        self.workspace = Path(self.temporary.name)
        self.root = self.workspace / ".valp"
        self.core = InstallationCore(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_leader_root_reuses_configured_installation_from_another_workspace(self) -> None:
        global_root = self.workspace / "global-control"
        caller_workspace = self.workspace / "another-window"
        caller_workspace.mkdir()
        InstallationCore(global_root).init()

        with patch.dict(os.environ, {"VALP_CONTROL_ROOT": str(global_root)}):
            resolved = leader_installation_root(caller_workspace)

        self.assertEqual(resolved, global_root.resolve())

    def _bootstrap(self) -> None:
        self.core.init()
        self.core.discover_candidates([self._leader_passport()])
        self.core.select_leader("agent-codex-session-a")
        self.core.prepare_leader_start()
        self.core.activate_leader(self._provisioned_leader())

    def _leader_passport(
        self,
        *,
        principal_id: str = "agent-codex-session-a",
        agent_id: str = "codex",
        session_id: str = "session-a",
        leader_eligibility: str = "eligible",
        launch_argv: list[str] | None = None,
    ) -> dict:
        return {
            "schema_version": "valp-capability-passport.v1",
            "generated_at": "2026-07-26T12:00:00Z",
            "principal_id": principal_id,
            "agent_id": agent_id,
            "agent_surface": f"{agent_id}_cli",
            "runtime_identity": {
                "runtime": "HERDR",
                "adapter_class": "pane_controller",
                "session_id": session_id,
                "session": {
                    "status": "known",
                    "token": f"sha256:{session_id}",
                    "source": "test runtime metadata",
                    "generation": "1",
                },
            },
            "runtime": {
                "adapter_id": "herdr",
                "adapter_class": "pane_controller",
                "launch_argv": launch_argv or ["/test/bin/codex", "--example-mode"],
                "version_command": ["/test/bin/codex", "--version"],
            },
            "live_callability": {"status": "pass"},
            "role_eligibility": {"leader": leader_eligibility},
        }

    def _provisioned_leader(self) -> dict:
        return {
            "adapter_id": "herdr",
            "adapter_class": "pane_controller",
            "principal_id": "agent-codex-session-a",
            "agent_id": "codex",
            "generation": 1,
            "ownership": {
                "scope": "installation",
                "installation_id": self.core.state()["installation_id"],
            },
            "context": {"cwd": str(self.workspace.resolve())},
            "launch": {"argv": ["/test/bin/codex", "--example-mode"]},
            "focused_at_provisioning": False,
            "runtime_scope": {
                "kind": "workspace",
                "ownership": "installation",
                "workspace_id": "workspace-leader",
            },
            "runtime_identity": {
                "session_id": "pane-leader-fresh",
                "pane_id": "pane-leader-fresh",
                "terminal_id": "terminal-leader-fresh",
                "workspace_id": "workspace-leader",
                "tab_id": "tab-leader-fresh",
                "token": "sha256:" + ("1" * 64),
            },
            "health": {
                "status": "pass",
                "observed_at": "2026-07-26T12:01:00Z",
                "evidence": {"agent_status": "idle"},
            },
            "provisioned_at": "2026-07-26T12:01:00Z",
        }

    def _block_first_leader_start(self) -> dict:
        self.core.init()
        self.core.discover_candidates([self._leader_passport()])
        self.core.select_leader("agent-codex-session-a")
        self.core.prepare_leader_start()
        with self.assertRaises(ControlPlaneError):
            self.core.fail_leader_activation(
                "start",
                adapter_id="herdr",
                failure_class="HerdrSubmissionError",
            )
        return json.loads(
            (self.root / "leader-session-receipts.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[-1]
        )

    def test_candidate_discovery_uses_only_observed_launchable_passports(self) -> None:
        self.core.init()

        result = self.core.discover_candidates([
            self._leader_passport(),
            self._leader_passport(
                principal_id="agent-claude-session-b",
                agent_id="claude",
                session_id="session-b",
                leader_eligibility="blocked",
                launch_argv=["/test/bin/claude"],
            ),
        ])

        self.assertEqual(
            [candidate["principal_id"] for candidate in result["candidates"]],
            ["agent-codex-session-a"],
        )
        candidate = result["candidates"][0]
        self.assertEqual(candidate["principal_kind"], "agent")
        self.assertEqual(candidate["runtime"]["launch_argv"], ["/test/bin/codex", "--example-mode"])
        self.assertEqual(candidate["runtime"]["adapter_id"], "herdr")
        self.assertRegex(candidate["passport_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("manual-user", {item["principal_id"] for item in result["candidates"]})
        self.assertNotIn("valp-reference-cli", {item["principal_id"] for item in result["candidates"]})

    def test_candidate_discovery_can_refresh_before_leader_selection(self) -> None:
        self.core.init()
        first = self.core.discover_candidates([])

        refreshed = self.core.discover_candidates([self._leader_passport()])

        self.assertEqual(first["candidates"], [])
        self.assertEqual(
            [candidate["principal_id"] for candidate in refreshed["candidates"]],
            ["agent-codex-session-a"],
        )
        self.assertEqual(self.core.state()["status"], "awaiting_leader_selection")
        events = [
            json.loads(line)
            for line in (self.root / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [event["event_kind"] for event in events],
            [
                "installation_initialized",
                "bootstrap_discovery_started",
                "leader_candidate_discovery_completed",
                "bootstrap_discovery_started",
                "leader_candidate_discovery_completed",
            ],
        )
        self.assertEqual(len({event["event_id"] for event in events}), 5)

    def test_leader_candidates_cli_uses_fresh_doctor_passports(self) -> None:
        self.core.init()
        output = io.StringIO()

        with patch(
            "valp_cli.cli.collect_doctor_report",
            return_value=SimpleNamespace(capability_passports=[self._leader_passport()]),
        ), contextlib.redirect_stdout(output):
            code = main([
                "leader",
                "candidates",
                "--workspace",
                str(self.workspace),
                "--json",
            ])

        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(
            [candidate["principal_id"] for candidate in payload["candidates"]],
            ["agent-codex-session-a"],
        )

    def test_leader_selection_persists_intent_without_activation(self) -> None:
        self.core.init()
        self.core.discover_candidates([self._leader_passport()])

        result = self.core.select_leader("agent-codex-session-a")

        state = self.core.state()
        self.assertEqual(state["status"], "awaiting_leader_start")
        self.assertIsNone(state["active_leader"])
        self.assertEqual(state["active_leader_epoch"], 0)
        self.assertEqual(state["selected_leader"]["principal_id"], "agent-codex-session-a")
        self.assertEqual(result["selection"]["proposed_leader_epoch"], 1)
        self.assertEqual(result["selection"]["passport_ref"], state["selected_leader"]["passport_ref"])

    def test_leader_start_activates_only_the_fresh_installation_owned_session(self) -> None:
        self.core.init()
        self.core.discover_candidates([self._leader_passport()])
        self.core.select_leader("agent-codex-session-a")
        provisioned = {
            "adapter_id": "herdr",
            "adapter_class": "pane_controller",
            "principal_id": "agent-codex-session-a",
            "agent_id": "codex",
            "generation": 1,
            "ownership": {
                "scope": "installation",
                "installation_id": self.core.state()["installation_id"],
            },
            "context": {"cwd": str(self.workspace.resolve())},
            "launch": {"argv": ["/test/bin/codex", "--example-mode"]},
            "focused_at_provisioning": False,
            "runtime_scope": {
                "kind": "workspace",
                "ownership": "installation",
                "workspace_id": "workspace-leader",
            },
            "runtime_identity": {
                "session_id": "pane-leader-fresh",
                "pane_id": "pane-leader-fresh",
                "terminal_id": "terminal-leader-fresh",
                "workspace_id": "workspace-leader",
                "tab_id": "tab-leader-fresh",
                "token": "sha256:" + ("1" * 64),
            },
            "health": {
                "status": "pass",
                "observed_at": "2026-07-26T12:01:00Z",
                "evidence": {"agent_status": "idle"},
            },
            "provisioned_at": "2026-07-26T12:01:00Z",
        }
        output = io.StringIO()

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.provision_herdr_leader_session",
            return_value=provisioned,
        ) as provision, contextlib.redirect_stdout(output):
            code = main([
                "leader",
                "start",
                "--workspace",
                str(self.workspace),
                "--json",
            ])

        self.assertEqual(code, 0)
        provision.assert_called_once()
        state = self.core.state()
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["active_leader_epoch"], 1)
        self.assertEqual(state["active_leader"]["principal_id"], "agent-codex-session-a")
        self.assertNotIn("session_id", state["active_leader"])
        binding = json.loads((self.root / "leader-session-binding.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["ownership"]["scope"], "installation")
        self.assertEqual(binding["leader_epoch"], 1)
        self.assertEqual(binding["runtime_identity"]["session_id"], "pane-leader-fresh")
        receipts = [
            json.loads(line)
            for line in (self.root / "leader-session-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            [receipt["receipt_type"] for receipt in receipts],
            ["leader_session_provisioned", "leader_session_activated"],
        )

    def test_leader_show_reports_the_exact_active_session_binding(self) -> None:
        self.core.init()
        self.core.discover_candidates([self._leader_passport()])
        self.core.select_leader("agent-codex-session-a")
        self.core.prepare_leader_start()
        self.core.activate_leader(self._provisioned_leader())
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            code = main([
                "leader",
                "show",
                "--workspace",
                str(self.workspace),
                "--json",
            ])

        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        binding = payload["leader_session"]
        self.assertEqual(binding["runtime_identity"]["session_id"], "pane-leader-fresh")
        self.assertEqual(binding["generation"], 1)
        self.assertRegex(binding["launch"]["argv_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(binding["health"]["status"], "pass")

    def test_leader_start_provisioning_failure_records_blocked_receipt(self) -> None:
        self.core.init()
        self.core.discover_candidates([self._leader_passport()])
        self.core.select_leader("agent-codex-session-a")

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.provision_herdr_leader_session",
            side_effect=HerdrSubmissionError("simulated provisioning failure"),
        ):
            with self.assertRaises(SystemExit) as context:
                main([
                    "leader",
                    "start",
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ])

        self.assertIn("VALP-E-LEADER-UNREACHABLE", str(context.exception))
        state = self.core.state()
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["active_leader_epoch"], 0)
        self.assertIsNone(state["active_leader"])
        receipts = [
            json.loads(line)
            for line in (self.root / "leader-session-receipts.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        self.assertEqual(receipt["receipt_type"], "leader_session_start_failed")
        self.assertEqual(receipt["operation"], "start")
        self.assertEqual(receipt["principal_id"], "agent-codex-session-a")
        self.assertEqual(receipt["leader_epoch"], 1)
        self.assertEqual(receipt["generation"], 1)
        self.assertEqual(receipt["failure_code"], "VALP-E-LEADER-UNREACHABLE")
        self.assertNotIn("binding_digest", receipt)
        self.assertNotIn("runtime_session_id", receipt)
        schema = json.loads(
            (ROOT / "schemas" / "leader-session-receipt.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(receipt)), [])

    def test_leader_start_rejects_invalid_runtime_binding_into_blocked_state(self) -> None:
        self.core.init()
        self.core.discover_candidates([self._leader_passport()])
        self.core.select_leader("agent-codex-session-a")
        invalid_binding = self._provisioned_leader()
        invalid_binding["focused_at_provisioning"] = True

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.provision_herdr_leader_session",
            return_value=invalid_binding,
        ):
            with self.assertRaises(SystemExit) as context:
                main([
                    "leader",
                    "start",
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ])

        self.assertIn("VALP-E-LEADER-UNREACHABLE", str(context.exception))
        state = self.core.state()
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["active_leader_epoch"], 0)
        self.assertFalse((self.root / "leader-session-binding.json").exists())
        receipts = [
            json.loads(line)
            for line in (self.root / "leader-session-receipts.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        self.assertEqual(receipts[-1]["receipt_type"], "leader_session_start_failed")
        self.assertEqual(receipts[-1]["failure_class"], "LeaderSessionBindingValidationError")
        self.assertNotIn("runtime_session_id", receipts[-1])

    def test_leader_recover_start_requires_explicit_approval_before_runtime_access(self) -> None:
        self._block_first_leader_start()

        with patch("valp_cli.cli.shutil.which") as which, patch(
            "valp_cli.cli.recover_herdr_leader_session"
        ) as recover:
            with self.assertRaises(SystemExit) as context:
                main([
                    "leader",
                    "recover-start",
                    "--workspace",
                    str(self.workspace),
                    "--session",
                    "workspace-leader:pane-leader-fresh",
                    "--json",
                ])

        self.assertIn("VALP-E-APPROVAL-REQUIRED", str(context.exception))
        which.assert_not_called()
        recover.assert_not_called()
        self.assertEqual(self.core.state()["status"], "blocked")
        self.assertEqual(self.core.state()["revision"], 6)

    def test_leader_recover_start_rejects_a_nonmatching_latest_failed_receipt(self) -> None:
        original = self._block_first_leader_start()
        conflicting = {
            **original,
            "receipt_id": "leader-receipt-conflicting-recovery",
            "operation": "recover-start",
            "recorded_at": "2026-07-26T12:02:00Z",
        }
        conflicting["receipt_digest"] = digest_without(conflicting, "receipt_digest")
        with (self.root / "leader-session-receipts.jsonl").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(json.dumps(conflicting, sort_keys=True) + "\n")

        with patch("valp_cli.cli.shutil.which") as which, patch(
            "valp_cli.cli.recover_herdr_leader_session"
        ) as recover:
            with self.assertRaises(SystemExit) as context:
                main([
                    "leader",
                    "recover-start",
                    "--workspace",
                    str(self.workspace),
                    "--session",
                    "workspace-leader:pane-leader-fresh",
                    "--approve",
                    "--json",
                ])

        self.assertIn("VALP-E-REGISTRY-CONSISTENCY", str(context.exception))
        which.assert_not_called()
        recover.assert_not_called()
        self.assertEqual(self.core.state()["status"], "blocked")

    def test_write_json_retries_transient_windows_replace_conflict(self) -> None:
        path = self.workspace / "state.json"
        real_replace = os.replace
        conflict = PermissionError("injected Windows sharing violation")
        conflict.winerror = 5
        calls = 0

        def replace_after_conflict(source, target):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise conflict
            return real_replace(source, target)

        with patch("valp_cli.control_plane.os.replace", side_effect=replace_after_conflict):
            write_json(path, {"status": "prepared"})

        self.assertEqual(calls, 2)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"status": "prepared"})

    def test_leader_recover_start_activates_only_the_user_named_failed_session(self) -> None:
        failed = self._block_first_leader_start()
        recovered = self._provisioned_leader()
        recovered["runtime_identity"]["session_id"] = "workspace-leader:pane-recovered"
        recovered["runtime_identity"]["pane_id"] = "workspace-leader:pane-recovered"
        recovered["runtime_identity"]["process_generation"] = "sha256:" + ("2" * 64)
        recovered["runtime_identity"]["token"] = "sha256:" + ("3" * 64)
        output = io.StringIO()

        def recovered_session(*_args: object, **kwargs: object) -> dict:
            return {
                **recovered,
                "recovery": kwargs["recovery_approval"],
            }

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.recover_herdr_leader_session",
            side_effect=recovered_session,
        ) as recover, contextlib.redirect_stdout(output):
            code = main([
                "leader",
                "recover-start",
                "--workspace",
                str(self.workspace),
                "--session",
                "workspace-leader:pane-recovered",
                "--approve",
                "--json",
            ])

        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["binding"]["runtime_identity"]["session_id"], "workspace-leader:pane-recovered")
        self.assertEqual(recover.call_args.kwargs["session_id"], "workspace-leader:pane-recovered")
        self.assertEqual(recover.call_args.kwargs["generation"], 1)
        self.assertEqual(recover.call_args.kwargs["leader_epoch"], 1)
        approval = recover.call_args.kwargs["recovery_approval"]
        self.assertEqual(approval["failed_receipt_digest"], failed["receipt_digest"])
        self.assertEqual(approval["approved_session_id"], "workspace-leader:pane-recovered")

        state = self.core.state()
        self.assertEqual(state["status"], "active")
        self.assertEqual(state["active_leader_epoch"], 1)
        binding = json.loads((self.root / "leader-session-binding.json").read_text(encoding="utf-8"))
        self.assertNotIn("session_id", state["active_leader"])
        self.assertEqual(binding["runtime_identity"]["session_id"], "workspace-leader:pane-recovered")
        self.assertEqual(binding["recovery"]["approval_event_id"], approval["approval_event_id"])
        self.assertEqual(binding["recovery"]["failed_receipt_digest"], failed["receipt_digest"])
        binding_schema = json.loads(
            (ROOT / "schemas" / "leader-session-binding.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            list(Draft202012Validator(binding_schema).iter_errors(binding)),
            [],
        )
        receipts = [
            json.loads(line)
            for line in (self.root / "leader-session-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(
            [receipt["receipt_type"] for receipt in receipts],
            [
                "leader_session_start_failed",
                "leader_session_provisioned",
                "leader_session_activated",
            ],
        )
        self.assertEqual(receipts[0], failed)
        self.assertEqual(receipts[1]["recovery"]["failed_receipt_digest"], failed["receipt_digest"])
        self.assertEqual(receipts[2]["recovery"]["approved_session_id"], "workspace-leader:pane-recovered")
        receipt_schema = json.loads(
            (ROOT / "schemas" / "leader-session-receipt.schema.json").read_text(
                encoding="utf-8"
            )
        )
        for receipt in receipts:
            self.assertEqual(
                list(Draft202012Validator(receipt_schema).iter_errors(receipt)),
                [],
            )

    def test_failed_leader_recovery_preserves_the_original_failed_receipt(self) -> None:
        original = self._block_first_leader_start()

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.recover_herdr_leader_session",
            side_effect=HerdrSubmissionError("simulated recovery mismatch"),
        ):
            with self.assertRaises(SystemExit) as context:
                main([
                    "leader",
                    "recover-start",
                    "--workspace",
                    str(self.workspace),
                    "--session",
                    "workspace-leader:pane-recovered",
                    "--approve",
                    "--json",
                ])

        self.assertIn("VALP-E-LEADER-UNREACHABLE", str(context.exception))
        self.assertEqual(self.core.state()["status"], "blocked")
        self.assertEqual(self.core.state()["active_leader_epoch"], 0)
        receipts = [
            json.loads(line)
            for line in (self.root / "leader-session-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(receipts[0], original)
        self.assertEqual(receipts[-1]["receipt_type"], "leader_session_start_failed")
        self.assertEqual(receipts[-1]["operation"], "recover-start")
        self.assertNotEqual(receipts[-1]["receipt_digest"], original["receipt_digest"])
        self.assertEqual(
            receipts[-1]["recovery"]["approved_session_id"],
            "workspace-leader:pane-recovered",
        )

    def test_second_leader_start_opens_the_active_attachment_without_provisioning(self) -> None:
        self.core.init()
        self.core.discover_candidates([self._leader_passport()])
        self.core.select_leader("agent-codex-session-a")
        self.core.prepare_leader_start()
        self.core.activate_leader(self._provisioned_leader())

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.open_herdr_leader_session",
            return_value={
                "status": "opened",
                "action": "focused_existing_attachment",
                "session_id": "pane-leader-fresh",
                "workspace_id": "workspace-leader",
                "binding_digest": "sha256:" + ("a" * 64),
            },
        ) as opened, patch(
            "valp_cli.cli.provision_herdr_leader_session"
        ) as provision:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "leader",
                    "start",
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ])

        self.assertEqual(code, 0)
        opened.assert_called_once()
        provision.assert_not_called()
        self.assertEqual(self.core.state()["active_leader_epoch"], 1)

    def test_leader_open_reprovisions_only_when_the_attachment_is_gone(self) -> None:
        self._bootstrap()
        replacement = self._provisioned_leader()
        replacement["generation"] = 2
        replacement["runtime_identity"] = {
            **replacement["runtime_identity"],
            "session_id": "pane-leader-reopened",
            "pane_id": "pane-leader-reopened",
        }

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.open_herdr_leader_session",
            side_effect=[
                {
                    "status": "missing",
                    "action": "reprovision_required",
                    "reason": "leader_attachment_not_found",
                },
                {
                    "status": "opened",
                    "action": "focused_existing_attachment",
                    "session_id": "pane-leader-reopened",
                    "workspace_id": "workspace-leader",
                },
            ],
        ) as opened, patch(
            "valp_cli.cli.provision_herdr_leader_session",
            return_value=replacement,
        ) as provision:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main([
                    "leader",
                    "open",
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ])

        self.assertEqual(code, 0)
        provision.assert_called_once()
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(self.core.state()["active_leader_epoch"], 2)
        self.assertEqual(json.loads(output.getvalue())["attachment"]["status"], "opened")
        self.assertEqual(
            json.loads((self.root / "leader-session-binding.json").read_text(encoding="utf-8"))[
                "runtime_identity"
            ]["session_id"],
            "pane-leader-reopened",
        )

    def test_leader_open_records_restart_failure_when_reprovisioning_fails(self) -> None:
        self._bootstrap()

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.open_herdr_leader_session",
            return_value={
                "status": "missing",
                "action": "reprovision_required",
                "reason": "leader_attachment_not_found",
            },
        ), patch(
            "valp_cli.cli.provision_herdr_leader_session",
            side_effect=HerdrSubmissionError("simulated open replacement failure"),
        ):
            with self.assertRaises(SystemExit) as context:
                main([
                    "leader",
                    "open",
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ])

        self.assertIn("VALP-E-LEADER-UNREACHABLE", str(context.exception))
        self.assertEqual(self.core.state()["status"], "active")
        receipts = [
            json.loads(line)
            for line in (self.root / "leader-session-receipts.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        self.assertEqual(receipts[-1]["operation"], "restart")

    def test_explicit_leader_restart_fences_epoch_and_preserves_binding_history(self) -> None:
        self.core.init()
        self.core.discover_candidates([self._leader_passport()])
        self.core.select_leader("agent-codex-session-a")
        self.core.prepare_leader_start()
        self.core.activate_leader(self._provisioned_leader())
        first_binding = json.loads(
            (self.root / "leader-session-binding.json").read_text(encoding="utf-8")
        )
        replacement = self._provisioned_leader()
        replacement["generation"] = 2
        replacement["runtime_scope"] = {
            "kind": "workspace",
            "ownership": "installation",
            "workspace_id": "workspace-leader-2",
        }
        replacement["runtime_identity"] = {
            "session_id": "pane-leader-fresh-2",
            "pane_id": "pane-leader-fresh-2",
            "terminal_id": "terminal-leader-fresh-2",
            "workspace_id": "workspace-leader-2",
            "tab_id": "tab-leader-fresh-2",
            "token": "sha256:" + ("2" * 64),
        }
        replacement["provisioned_at"] = "2026-07-26T12:02:00Z"
        output = io.StringIO()

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.provision_herdr_leader_session",
            return_value=replacement,
        ) as provision, contextlib.redirect_stdout(output):
            code = main([
                "leader",
                "restart",
                "--workspace",
                str(self.workspace),
                "--json",
            ])

        self.assertEqual(code, 0)
        self.assertEqual(provision.call_args.kwargs["leader_epoch"], 2)
        self.assertEqual(provision.call_args.kwargs["generation"], 2)
        state = self.core.state()
        self.assertEqual(state["active_leader_epoch"], 2)
        self.assertNotIn("session_id", state["active_leader"])
        historical = list((self.root / "leader-session-bindings").glob("*.json"))
        historical_bindings = [json.loads(path.read_text(encoding="utf-8")) for path in historical]
        self.assertIn(first_binding["binding_digest"], {
            binding["binding_digest"] for binding in historical_bindings
        })
        receipts = [
            json.loads(line)
            for line in (self.root / "leader-session-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(receipts[-2]["receipt_type"], "leader_session_replaced")
        self.assertEqual(receipts[-1]["receipt_type"], "leader_session_activated")
        with self.assertRaises(ControlPlaneError) as stale:
            current = self.core.state()
            self.core._transition(
                event_kind="test.stale_worker",
                message_kind="command.test.stale_worker",
                principal_id="old-leader",
                principal_kind="installation-leader",
                epoch=1,
                expected_revision=current["revision"],
                payload={},
                target_status="degraded",
                idempotency_key="stale-after-restart",
            )
        self.assertEqual(stale.exception.code, "VALP-E-LEADER-EPOCH")

    def test_leader_restart_provisioning_failure_preserves_the_active_epoch(self) -> None:
        self._bootstrap()
        prior_binding = json.loads(
            (self.root / "leader-session-binding.json").read_text(encoding="utf-8")
        )

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.provision_herdr_leader_session",
            side_effect=HerdrSubmissionError("simulated restart failure"),
        ):
            with self.assertRaises(SystemExit) as context:
                main([
                    "leader",
                    "restart",
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ])

        self.assertIn("VALP-E-LEADER-UNREACHABLE", str(context.exception))
        state = self.core.state()
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["active_leader_epoch"], 1)
        self.assertNotIn("session_id", state["active_leader"])
        self.assertEqual(
            json.loads(
                (self.root / "leader-session-binding.json").read_text(encoding="utf-8")
            )["binding_digest"],
            prior_binding["binding_digest"],
        )
        receipts = [
            json.loads(line)
            for line in (self.root / "leader-session-receipts.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        self.assertEqual(receipts[-1]["receipt_type"], "leader_session_start_failed")
        self.assertEqual(receipts[-1]["operation"], "restart")
        self.assertEqual(receipts[-1]["leader_epoch"], 2)
        self.assertEqual(receipts[-1]["generation"], 2)
        self.assertNotIn("replaced_binding_digest", receipts[-1])

    def test_leader_restart_can_retry_after_failed_attempt_is_rolled_back(self) -> None:
        self._bootstrap()

        first_attempt = self.core.prepare_leader_restart()
        with self.assertRaises(ControlPlaneError):
            self.core.fail_leader_activation(
                "restart",
                adapter_id="herdr",
                failure_class="HerdrSubmissionError",
            )
        self.core.restore_active_leader_after_failed_restart()
        second_attempt = self.core.prepare_leader_restart()

        self.assertEqual(first_attempt["proposed_leader_epoch"], 2)
        self.assertEqual(second_attempt["proposed_leader_epoch"], 2)
        self.assertNotEqual(
            first_attempt["restart"]["message_id"],
            second_attempt["restart"]["message_id"],
        )
        self.assertEqual(self.core.state()["status"], "restarting_leader")
        with self.assertRaises(ControlPlaneError) as second_failure:
            self.core.fail_leader_activation(
                "restart",
                adapter_id="herdr",
                failure_class="HerdrSubmissionError",
            )
        self.assertEqual(second_failure.exception.code, "VALP-E-LEADER-UNREACHABLE")
        self.core.restore_active_leader_after_failed_restart()
        self.assertEqual(self.core.state()["status"], "active")

    def test_leader_rotation_provisions_replacement_before_changing_authority(self) -> None:
        replacement_passport = self._leader_passport(
            principal_id="agent-claude-session-b",
            agent_id="claude",
            session_id="session-b",
            launch_argv=["/test/bin/claude"],
        )
        self.core.init()
        self.core.discover_candidates([self._leader_passport(), replacement_passport])
        self.core.select_leader("agent-codex-session-a")
        self.core.prepare_leader_start()
        self.core.activate_leader(self._provisioned_leader())
        replacement = self._provisioned_leader()
        replacement.update({
            "principal_id": "agent-claude-session-b",
            "agent_id": "claude",
            "generation": 2,
            "launch": {"argv": ["/test/bin/claude"]},
            "runtime_scope": {
                "kind": "workspace",
                "ownership": "installation",
                "workspace_id": "workspace-claude-leader",
            },
            "runtime_identity": {
                "session_id": "pane-claude-leader",
                "pane_id": "pane-claude-leader",
                "terminal_id": "terminal-claude-leader",
                "workspace_id": "workspace-claude-leader",
                "tab_id": "tab-claude-leader",
                "token": "sha256:" + ("3" * 64),
            },
        })

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.provision_herdr_leader_session",
            return_value=replacement,
        ) as provision:
            code = main([
                "leader",
                "rotate",
                "agent-claude-session-b",
                "--workspace",
                str(self.workspace),
                "--json",
            ])

        self.assertEqual(code, 0)
        provision.assert_called_once()
        state = self.core.state()
        self.assertEqual(state["active_leader_epoch"], 2)
        self.assertEqual(state["active_leader"]["principal_id"], "agent-claude-session-b")
        self.assertNotIn("session_id", state["active_leader"])

    def test_leader_rotation_provisioning_failure_does_not_activate_replacement(self) -> None:
        replacement_passport = self._leader_passport(
            principal_id="agent-claude-session-b",
            agent_id="claude",
            session_id="session-b",
            launch_argv=["/test/bin/claude"],
        )
        self.core.init()
        self.core.discover_candidates([self._leader_passport(), replacement_passport])
        self.core.select_leader("agent-codex-session-a")
        self.core.prepare_leader_start()
        self.core.activate_leader(self._provisioned_leader())
        prior_binding = json.loads(
            (self.root / "leader-session-binding.json").read_text(encoding="utf-8")
        )

        with patch("valp_cli.cli.shutil.which", return_value="/test/herdr"), patch(
            "valp_cli.cli.provision_herdr_leader_session",
            side_effect=HerdrSubmissionError("simulated rotation failure"),
        ):
            with self.assertRaises(SystemExit) as context:
                main([
                    "leader",
                    "rotate",
                    "agent-claude-session-b",
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ])

        self.assertIn("VALP-E-LEADER-UNREACHABLE", str(context.exception))
        state = self.core.state()
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["active_leader_epoch"], 1)
        self.assertEqual(state["active_leader"]["principal_id"], "agent-codex-session-a")
        self.assertEqual(state["selected_leader"]["principal_id"], "agent-claude-session-b")
        self.assertEqual(
            json.loads(
                (self.root / "leader-session-binding.json").read_text(encoding="utf-8")
            )["binding_digest"],
            prior_binding["binding_digest"],
        )
        receipts = [
            json.loads(line)
            for line in (self.root / "leader-session-receipts.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        self.assertEqual(receipts[-1]["receipt_type"], "leader_session_start_failed")
        self.assertEqual(receipts[-1]["operation"], "rotate")
        self.assertEqual(receipts[-1]["principal_id"], "agent-claude-session-b")
        self.assertEqual(receipts[-1]["leader_epoch"], 2)
        self.assertEqual(receipts[-1]["generation"], 2)

    def test_bootstrap_requires_explicit_leader_selection(self) -> None:
        self.core.init()
        self.core.discover_candidates()
        self.assertEqual(self.core.state()["status"], "awaiting_leader_selection")
        with self.assertRaises(ControlPlaneError) as context:
            self.core.select_leader("missing-principal")
        self.assertEqual(context.exception.code, "VALP-E-PERMISSION-DENIED")

    def test_epoch_zero_is_fenced_after_activation(self) -> None:
        self._bootstrap()
        state = self.core.state()
        with self.assertRaises(ControlPlaneError) as context:
            self.core._transition(
                event_kind="test.fenced",
                message_kind="command.test.fenced",
                principal_id="bootstrap-controller",
                principal_kind="bootstrap-controller",
                epoch=0,
                expected_revision=state["revision"],
                payload={},
                target_status="degraded",
                idempotency_key="test-fenced",
            )
        self.assertEqual(context.exception.code, "VALP-E-LEADER-EPOCH")
        self.assertEqual(self.core.state()["status"], "active")

    def test_stale_revision_is_fail_closed(self) -> None:
        self._bootstrap()
        state = self.core.state()
        with self.assertRaises(ControlPlaneError) as context:
            self.core._transition(
                event_kind="test.cas",
                message_kind="command.test.cas",
                principal_id="manual-user",
                principal_kind="human",
                epoch=state["active_leader_epoch"],
                expected_revision=state["revision"] - 1,
                payload={},
                target_status="degraded",
                idempotency_key="test-cas",
            )
        self.assertEqual(context.exception.code, "VALP-E-STATE-CONFLICT")
        self.assertEqual(self.core.state()["revision"], state["revision"])
        failures = [json.loads(line) for line in (self.root / "failures.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual(failures[-1]["error_code"], "VALP-E-STATE-CONFLICT")

    def test_idempotency_replay_and_conflict(self) -> None:
        self._bootstrap()
        state = self.core.state()
        arguments = dict(
            event_kind="test.degraded",
            message_kind="command.test.degraded",
            principal_id="manual-user",
            principal_kind="human",
            epoch=state["active_leader_epoch"],
            expected_revision=state["revision"],
            payload={"reason": "test"},
            target_status="degraded",
            idempotency_key="test-idempotent",
        )
        first = self.core._transition(**arguments)
        second = self.core._transition(**arguments)
        self.assertEqual(first, second)
        self.assertEqual(self.core.state()["revision"], state["revision"] + 1)
        arguments["payload"] = {"reason": "different"}
        with self.assertRaises(ControlPlaneError) as context:
            self.core._transition(**arguments)
        self.assertEqual(context.exception.code, "VALP-E-IDEMPOTENCY-CONFLICT")

    def test_capability_registry_keeps_layers_separate(self) -> None:
        self._bootstrap()
        result = self.core.reconcile_capabilities([
            {"subject_id": "manual-user", "capability_id": "coordination", "layer": "official_claim", "status": "present"},
            {"subject_id": "manual-user", "capability_id": "coordination", "layer": "local_presence", "status": "present"},
            {"subject_id": "manual-user", "capability_id": "coordination", "layer": "live_callable", "status": "pass"},
        ])
        entry = result["registry"]["entries"]["manual-user::coordination"]
        self.assertEqual(set(entry["layers"]), {"official_claim", "local_presence", "live_callable"})
        self.assertEqual(entry["effective_status"], "pass")

    def test_event_replay_detects_tampering(self) -> None:
        self._bootstrap()
        events = self.root / "events.jsonl"
        original = events.read_text(encoding="utf-8")
        tampered = original.replace('"event_kind": "leader_activated"', '"event_kind": "tampered"', 1)
        events.write_text(tampered, encoding="utf-8")
        with self.assertRaises(ControlPlaneError) as context:
            self.core.replay()
        self.assertEqual(context.exception.code, "VALP-E-REGISTRY-CONSISTENCY")

    def test_generated_core_artifacts_match_schemas(self) -> None:
        self._bootstrap()
        self.core.reconcile_capabilities([
            {"subject_id": "manual-user", "capability_id": "coordination", "layer": "live_callable", "status": "pass"},
        ])
        mappings = {
            "installation.json": "installation.schema.json",
            "protocol-manifest.json": "protocol-manifest.schema.json",
            "state.json": "executable-state.schema.json",
            "leader-candidates.json": "leader-candidates.schema.json",
            "leader-session-binding.json": "leader-session-binding.schema.json",
            "capability-registry.json": "capability-registry.schema.json",
            "evidence-manifest.json": "evidence-manifest.schema.json",
        }
        for artifact, schema_name in mappings.items():
            with self.subTest(artifact=artifact):
                schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
                value = json.loads((self.root / artifact).read_text(encoding="utf-8"))
                self.assertEqual(list(Draft202012Validator(schema).iter_errors(value)), [])
        for artifact, schema_name in (
            ("messages.jsonl", "message.schema.json"),
            ("events.jsonl", "event.schema.json"),
            ("leader-selections.jsonl", "leader-selection.schema.json"),
            ("leader-session-receipts.jsonl", "leader-session-receipt.schema.json"),
        ):
            schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
            validator = Draft202012Validator(schema)
            for line in (self.root / artifact).read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.assertEqual(list(validator.iter_errors(json.loads(line))), [])

    def test_plugin_cannot_write_authoritative_ledgers(self) -> None:
        manifest = {
            "schema_version": "valp-plugin-manifest.v1",
            "plugin_id": "safe-discovery",
            "implementation_id": "test",
            "plugin_kind": "discovery",
            "protocol_read_versions": ["0.3.0"],
            "protocol_write_versions": ["0.3.0"],
            "entrypoint": "test:run",
            "permissions": ["capability.observe"],
            "provided_capabilities": ["coordination"],
            "required_capabilities": [],
            "resource_limits": {"timeout_seconds": 1},
            "isolation": "process",
            "manifest_digest": "",
        }
        manifest["manifest_digest"] = digest_without(manifest, "manifest_digest")
        validate_plugin_manifest(manifest)
        manifest["permissions"] = ["state.write"]
        manifest["manifest_digest"] = digest_without(manifest, "manifest_digest")
        with self.assertRaises(ControlPlaneError) as context:
            validate_plugin_manifest(manifest)
        self.assertEqual(context.exception.code, "VALP-E-PLUGIN-BOUNDARY")

    def test_evidence_claim_and_independent_review_preserve_history(self) -> None:
        self._bootstrap()
        subject = self.root / "artifacts" / "result.txt"
        subject.parent.mkdir(parents=True)
        subject.write_text("verified output\n", encoding="utf-8")
        evidence = self.core.add_evidence("artifacts/result.txt", evidence_kind="test-output", producer_principal_id="worker")
        claim = self.core.declare_claim(
            subject_ref="artifacts/result.txt",
            claim_kind="done",
            predicate="artifact is verified",
            asserted_value=True,
            scope="installation-test",
            claimant_principal_id="worker",
            evidence_refs=["artifacts/result.txt"],
        )
        self.assertEqual(claim["status"], "supported")
        reviewed = self.core.record_review(claim_id=claim["claim_id"], reviewer_principal_id="reviewer", verdict="pass")
        self.assertEqual(reviewed["claim"]["status"], "verified")
        claim_records = [json.loads(line) for line in (self.root / "claims.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual([record["status"] for record in claim_records], ["supported", "verified"])
        self.assertEqual(reviewed["review"]["reviewed_subject_digests"], [evidence["content_digest"]])
        for schema_name, value in (
            ("claim.schema.json", claim_records[0]),
            ("claim.schema.json", claim_records[1]),
            ("review.schema.json", reviewed["review"]),
            ("evidence-manifest.schema.json", json.loads((self.root / "evidence-manifest.json").read_text(encoding="utf-8"))),
        ):
            schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
            self.assertEqual(list(Draft202012Validator(schema).iter_errors(value)), [])

    def test_passing_review_requires_evidence_and_independence(self) -> None:
        self._bootstrap()
        subject = self.root / "result.txt"
        subject.write_text("output\n", encoding="utf-8")
        claim = self.core.declare_claim(
            subject_ref="result.txt",
            claim_kind="done",
            predicate="artifact is verified",
            asserted_value=True,
            scope="test",
            claimant_principal_id="worker",
            evidence_refs=[],
        )
        with self.assertRaises(ControlPlaneError) as missing:
            self.core.record_review(claim_id=claim["claim_id"], reviewer_principal_id="reviewer", verdict="pass")
        self.assertEqual(missing.exception.code, "VALP-E-EVIDENCE-MISSING")

        self.core.add_evidence("result.txt", evidence_kind="test-output", producer_principal_id="worker")
        supported = self.core.declare_claim(
            subject_ref="result.txt",
            claim_kind="done",
            predicate="artifact is verified",
            asserted_value=True,
            scope="test",
            claimant_principal_id="worker",
            evidence_refs=["result.txt"],
        )
        with self.assertRaises(ControlPlaneError) as independent:
            self.core.record_review(claim_id=supported["claim_id"], reviewer_principal_id="worker", verdict="pass")
        self.assertEqual(independent.exception.code, "VALP-E-REVIEW-BLOCKED")

    def test_task_reducer_blocks_runtime_completed_to_done(self) -> None:
        self._bootstrap()
        init_task(self.root, "TASK-001")
        current = task_state(self.root, "TASK-001")
        with self.assertRaises(ControlPlaneError) as direct_done:
            transition_task(self.root, "TASK-001", "done", expected_revision=current["revision"])
        self.assertEqual(direct_done.exception.code, "VALP-E-STATE-TRANSITION")

        published = transition_task(self.root, "TASK-001", "published", expected_revision=current["revision"])
        self.assertEqual(published["status"], "published")
        with self.assertRaises(ControlPlaneError) as missing_gates:
            transition_task(self.root, "TASK-001", "scanning_capabilities", expected_revision=published["revision"])
            transition_task(self.root, "TASK-001", "done", expected_revision=published["revision"] + 1)
        self.assertIn(missing_gates.exception.code, {"VALP-E-STATE-TRANSITION", "VALP-E-EVIDENCE-MISSING"})

    def test_task_done_requires_all_recorded_gates(self) -> None:
        self._bootstrap()
        init_task(self.root, "TASK-002")
        current = task_state(self.root, "TASK-002")
        # Drive the legal path with explicit gate records; runtime completion alone is not a Done gate.
        status = "new"
        revision = current["revision"]
        for target in ["published", "scanning_capabilities", "scanning_context", "loading_local_overlay", "selecting_runtime_adapter", "classifying_task", "selecting_profile", "decomposing_tasks", "recommending_skills", "building_provider_matrix", "scoring_routes", "routing_capabilities", "dispatching", "executing", "verifying", "reviewing", "recording"]:
            state = transition_task(self.root, "TASK-002", target, expected_revision=revision)
            status, revision = state["status"], state["revision"]
        with self.assertRaises(ControlPlaneError) as missing:
            transition_task(self.root, "TASK-002", "done", expected_revision=revision, gates={"receipts": True})
        self.assertEqual(missing.exception.code, "VALP-E-EVIDENCE-MISSING")
        gates = {name: True for name in ("receipts", "expected_evidence", "verification", "review", "approvals", "final_synthesis", "audit")}
        done = transition_task(self.root, "TASK-002", "done", expected_revision=revision, gates=gates)
        self.assertEqual(done["status"], "done")
        schema = json.loads((ROOT / "schemas" / "task-state.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(done)), [])

    def test_local_process_adapter_has_real_submission_and_output_evidence(self) -> None:
        self._bootstrap()
        command = [sys.executable, "-c", "print('process-adapter-ok')"]
        dry_run = run_process(self.root, "PROCESS-001", command)
        self.assertEqual(dry_run["status"], "dry_run")
        result = run_process(self.root, "PROCESS-001", command, approve=True)
        self.assertEqual(result["status"], "completed")
        run_record = result["run"]
        self.assertEqual(run_record["runtime"], "local-process")
        self.assertEqual(run_record["exit_code"], 0)
        self.assertEqual((self.root / run_record["stdout_ref"]).read_text(encoding="utf-8").strip(), "process-adapter-ok")
        schema = json.loads((ROOT / "schemas" / "process-adapter-run.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(run_record)), [])

    def test_public_cli_runs_the_local_process_adapter(self) -> None:
        self._bootstrap()
        command = f'{sys.executable} -c "print(\'process-cli-ok\')"'

        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin" / "valp"),
                "adapter",
                "process",
                "run",
                "PROCESS-CLI-001",
                "--workspace",
                str(self.workspace),
                "--command",
                command,
                "--approve",
                "--json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "completed")
        stdout_path = self.root / result["run"]["stdout_ref"]
        self.assertEqual(stdout_path.read_text(encoding="utf-8").strip(), "process-cli-ok")
