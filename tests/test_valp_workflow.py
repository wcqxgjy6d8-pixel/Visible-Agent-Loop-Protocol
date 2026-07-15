from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import contextlib
import errno
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tests.schema_helpers import schema_validator

import valp_cli.workflow as workflow_module
from valp_cli.audit import TaskAudit
from valp_cli.cli import main
from valp_cli.submission import (
    build_submission_dependencies,
    dependency_order_errors,
    unmet_dependencies_for_phases,
    validate_submission_dependencies,
)
from valp_cli.workflow import (
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
    role_assignments_for,
    scan_workspace,
    score_candidates,
    select_agents,
    resume_suspended_task,
    suspend_task,
    translate_legacy_herdr_receipts,
    wait_for_task,
    write_queue_submission,
)


class ValpWorkflowTests(unittest.TestCase):
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

    def test_agent_selection_uses_the_smallest_role_covering_team(self) -> None:
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

        selected = select_agents("agent-runtime", agents, scores)

        self.assertEqual(
            set(selected),
            {"coordinator-reviewer", "implementer-reviewer"},
        )

    def test_explicit_requested_agent_is_added_as_a_supplemental_role(self) -> None:
        agents = {
            "hermes": {"active": True, "role": ["coordination"], "strengths": ["state", "gates"], "mcp_servers": []},
            "codex": {"active": True, "role": ["implementation"], "strengths": ["verification", "tests"], "mcp_servers": []},
            "claude": {"active": True, "role": ["reviewer"], "strengths": ["read-only review"], "mcp_servers": []},
            "agy": {"active": True, "role": ["prototype"], "strengths": ["isolated prototype"], "mcp_servers": []},
        }
        scores = score_candidates("agent-runtime", agents)
        selected = select_agents("agent-runtime", agents, scores, requested_agents=["agy"])
        assignments = role_assignments_for(
            "agent-runtime",
            selected,
            agents,
            scores,
            requested_agents=["agy"],
        )

        self.assertEqual(set(selected), {"hermes", "codex", "claude", "agy"})
        self.assertEqual(assignments["prototype"], "agy")
        dependencies = build_submission_dependencies("TASK-REQUESTED-AGENT", assignments)
        self.assertIn("coordinator-before-prototype", [item["id"] for item in dependencies["dependencies"]])
        self.assertIn("prototype-before-reviewer", [item["id"] for item in dependencies["dependencies"]])

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
        self.assertIn("valp 0.2.0", output.getvalue())

    def test_profile_classification_scores_all_matches(self) -> None:
        self.assertEqual(classify_profile("Fix the HERDR agent connector code"), "agent-runtime")

    def test_high_risk_goal_marks_approval_required_on_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(
                root,
                "TASK-RISK",
                "Deploy the release to production and rotate secrets.",
                route=False,
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
                        task_dir = publish_task(root, "TASK-MANUAL", "Review the task evidence")
                        commands = dispatch_task(root, "TASK-MANUAL")
                        with self.assertRaises(SystemExit):
                            dispatch_task(root, "TASK-MANUAL", submit=True)

            self.assertEqual(read_json(task_dir / "routing.json")["runtime_adapter"]["class"], "manual")
            self.assertTrue(commands)
            self.assertTrue(commands[0].startswith("Manual Mode:"))
            self.assertNotIn("herdr-loop", commands[0])

    def test_wait_command_suspends_and_resumes_on_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                    task_dir = publish_task(
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
                    "--poll-interval",
                    "0",
                    "--json",
                ])

            result = json.loads(output.getvalue())
            state = read_json(task_dir / "state.json")
            self.assertEqual(exit_code, 0)
            self.assertEqual(result["resume_event"], "timeout")
            self.assertEqual(state["status"], "blocked")
            self.assertEqual(state["suspension"]["status"], "resumed")
            self.assertEqual(state["suspension"]["resume_event"], "timeout")

    def test_wait_does_not_accept_a_non_wake_state_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                    task_dir = publish_task(
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
            )

            receipts = [
                json.loads(line)
                for line in (task_dir / "dispatch-receipts.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(result["accepted_wake"]["wake_reason"], "timeout")
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

    def test_current_state_can_progress_after_timeout_without_rewriting_wake_time_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_id = "TASK-WAIT-POST-TIMEOUT-PROGRESS"
            task_dir, _items = self.write_deterministic_wait_fixture(root, task_id)
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

            state["status"] = "executing"
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

            repository = Path(__file__).resolve().parents[1]
            self.assertEqual(
                list(schema_validator(repository / "schemas/state.schema.json").iter_errors(state)),
                [],
            )
            self.assertEqual(TaskAudit(task_dir).check_deterministic_wake().status, "pass")
            self.assertEqual(events[-1]["projection"]["status"], "blocked")
            self.assertEqual(wake_result["resulting_task_status"], "blocked")

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
                    task_dir = publish_task(
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
                    task_dir = publish_task(
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
                    task_dir = publish_task(
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
                    task_dir = publish_task(
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
                    task_dir = publish_task(
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
                        task_dir = publish_task(
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
                    task_dir = publish_task(
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
                    task_dir = publish_task(
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

    def test_dispatch_uses_queue_adapter_without_herdr_command(self) -> None:
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
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = publish_task(root, "TASK-QUEUE", "Fix a bug and run tests", runtime="queue")
            commands = dispatch_task(root, "TASK-QUEUE")

            routing = read_json(task_dir / "routing.json")
            self.assertEqual(routing["runtime_adapter"]["class"], "daemon_queue")
            self.assertTrue(commands)
            self.assertTrue(commands[0].startswith("VALP Queue Mode:"))
            self.assertNotIn("herdr-loop", commands[0])
            preflight = read_json(task_dir / "runtime-preflight.json")
            self.assertEqual(preflight["adapter_class"], "daemon_queue")
            self.assertNotIn("terminal_size_status", json.dumps(preflight))

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
                    task_dir = publish_task(
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
            "checks": {},
            "agents": {"codex": {"status": "pass"}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    with patch("valp_cli.workflow.collect_runtime_preflight", return_value=preflight):
                        task_dir = publish_task(
                            root,
                            "TASK-HERDR-SUBMISSION-ONLY",
                            "Fix a bug and run tests",
                            runtime="herdr",
                        )
                        with patch(
                            "valp_cli.workflow.subprocess.run",
                            return_value=subprocess.CompletedProcess([], 0),
                        ):
                            commands = dispatch_task(
                                root,
                                "TASK-HERDR-SUBMISSION-ONLY",
                                role="coordinator",
                                wait_seconds=0,
                                submit=True,
                            )

            self.assertEqual(len(commands), 1)
            self.assertIn("--wait-seconds 0", commands[0])
            self.assertNotIn("--expect", commands[0])
            policy = read_json(task_dir / "wait-policy.json")
            self.assertEqual(
                policy["required_work_items"][0]["expected_refs"],
                ["agents/codex/self-review.md"],
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
                        publish_task(
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
                    task_dir = publish_task(
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
                    task_dir = publish_task(
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

    def test_publish_auto_scans_routes_and_writes_dispatches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(root, "TASK-SMOKE", "Fix a bug and run tests")

            self.assertTrue((task_dir / "task.md").exists())
            self.assertTrue((task_dir / "state.json").exists())
            self.assertTrue((task_dir / "routing.json").exists())
            self.assertTrue((task_dir / "automation-policy.json").exists())
            self.assertTrue((task_dir / "attention-map.json").exists())
            self.assertTrue((task_dir / "context-selection.json").exists())
            self.assertTrue((task_dir / "context-pack.json").exists())
            self.assertTrue((task_dir / "mask-list.json").exists())
            self.assertTrue((task_dir / "evidence-board.json").exists())
            self.assertTrue((task_dir / "visible-routing.md").exists())
            self.assertTrue((task_dir / "dispatch-receipts.jsonl").exists())
            self.assertTrue((root / ".herdr-loop" / "agents" / "capabilities.json").exists())

            routing = read_json(task_dir / "routing.json")
            self.assertEqual(routing["profile"], "software-code")
            self.assertIn("selected_agents", routing)
            self.assertEqual(routing["automation_policy"]["status"], "recorded")
            self.assertEqual(routing["context_pack"]["status"], "recorded")
            self.assertEqual(routing["visible_attention"]["status"], "recorded")
            self.assertTrue(routing["selected_agents"])
            self.assertIn("Mode: Selected during routing", (task_dir / "task.md").read_text(encoding="utf-8"))

            for agent in routing["selected_agents"]:
                dispatch_path = task_dir / "agents" / agent / "dispatch.md"
                self.assertTrue(dispatch_path.exists())
                dispatch = dispatch_path.read_text(encoding="utf-8")
                self.assertIn("## Project Root", dispatch)
                self.assertIn(f'cd "{root.resolve()}"', dispatch)
                for expected_ref in {
                    "codex": ["agents/codex/evidence.md", "evidence/verification.md"],
                    "claude": ["agents/claude/review.md"],
                    "hermes": ["agents/hermes/self-review.md"],
                    "agy": ["agents/agy/prototype.md"],
                }.get(agent, [f"agents/{agent}/evidence.md"]):
                    self.assertIn(f".herdr-loop/tasks/TASK-SMOKE/{expected_ref}", dispatch)
                self.assertIn("## Visible Attention Slice", dispatch)
                self.assertIn("context-pack.json", dispatch)

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
                    task_dir = publish_task(root, "TASK-BRIEF", long_prompt)

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

    def test_routing_feedback_history_changes_evidence_prior_without_overriding_scan(self) -> None:
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
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_done_feedback_history(root)
            with patch("valp_cli.workflow.load_local_capabilities", return_value=capabilities):
                with patch("valp_cli.workflow.skill_router_command", return_value=None):
                    task_dir = publish_task(root, "TASK-PRIOR", "Fix a bug and run tests", runtime="manual")

            routing = read_json(task_dir / "routing.json")
            score = routing["candidate_scores"]["codex"]
            self.assertGreater(score["evidence_history"], 0.6)
            self.assertTrue(score["evidence_history_refs"])
            self.assertIn("OLD-DONE", score["evidence_history_refs"][0])

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
            task_dir = publish_task(root, "TASK-ROUTE", "Research a source", route=False)
            scan_workspace(root, "TASK-ROUTE")
            routing = route_task(root, "TASK-ROUTE")

            self.assertEqual(task_dir.resolve(), (root / ".herdr-loop" / "tasks" / "TASK-ROUTE").resolve())
            self.assertEqual(routing["profile"], "research")
            self.assertTrue((task_dir / "routing.json").exists())

    def test_reroute_reclassifies_and_clears_stale_approval_risk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            task_dir = publish_task(
                root,
                "TASK-RISK-REROUTE",
                "Do not publish a release, deploy, or delete files.",
                route=False,
            )
            state = read_json(task_dir / "state.json")
            stale_risk = [{"kind": "release", "matched": "release"}]
            state["risk"] = {"approval_required": True, "matches": stale_risk}
            state["approval_required"] = stale_risk
            state["gates"]["approval"] = "needs_approval"
            (task_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

            with patch("valp_cli.workflow.skill_router_command", return_value=None):
                route_task(root, "TASK-RISK-REROUTE", runtime="manual")

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
                        task_dir = publish_task(root, "TASK-SKILL", "Fix a bug and run tests")

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
                    task_dir = publish_task(root, "TASK-ADAPTIVE-BUDGET", "Fix a bug and run tests", runtime="manual")

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
            self.assertEqual(budget["strategy"], "minimum_capable_team")
            self.assertEqual(budget["usage"]["dispatches"], 0)
            self.assertEqual(routing["iteration_budget"], {"status": "recorded", "ref": "iteration-budget.json"})
            self.assertEqual(set(routing["skill_recommendation_slices"]), set(routing["selected_agents"]))
            for agent in routing["selected_agents"]:
                self.assertTrue((task_dir / routing["skill_recommendation_slices"][agent]).exists())

            codex_dispatch = (task_dir / "agents" / "codex" / "dispatch.md").read_text(encoding="utf-8")
            self.assertIn("skill-slices/codex.json", codex_dispatch)
            self.assertNotIn("- `.herdr-loop/tasks/TASK-ADAPTIVE-BUDGET/skill-recommendations.json`", codex_dispatch)
            self.assertIn("iteration-budget.json", codex_dispatch)

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
                "max_fix_review_rounds": 2,
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
                "max_fix_review_rounds": 2,
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
