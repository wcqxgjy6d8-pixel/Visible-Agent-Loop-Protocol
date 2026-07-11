from pathlib import Path
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from valp_cli.cli import main
from valp_cli.workflow import (
    classify_profile,
    classify_approval_risks,
    decompose_execution_tasks,
    dispatch_task,
    feedback_prior_for_agent,
    load_local_capabilities,
    load_routing_feedback_history,
    publish_task,
    read_json,
    route_task,
    scan_workspace,
    resume_suspended_task,
    suspend_task,
    wait_for_task,
)


class ValpWorkflowTests(unittest.TestCase):
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
        self.assertEqual(classify_approval_risks("Write author notes."), [])
        credential_kinds = {item["kind"] for item in classify_approval_risks("Rotate credentials.")}
        self.assertIn("auth", credential_kinds)

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
            suspend_task(root, "TASK-WAIT-USER", timeout_seconds=60)

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main([
                    "resume",
                    "TASK-WAIT-USER",
                    "--workspace",
                    str(root),
                    "--event",
                    "user_input",
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
            suspend_task(root, "TASK-WAIT-FAILURE", timeout_seconds=60)

            with self.assertRaises(SystemExit):
                resume_suspended_task(
                    root,
                    "TASK-WAIT-FAILURE",
                    "runtime_failure",
                    resume_ref="evidence/missing-runtime-failure.log",
                )

    def test_failure_and_cancellation_resume_to_visible_handling_states(self) -> None:
        for resume_event, expected_status in [("runtime_failure", "blocked"), ("cancellation", "cancelled")]:
            with self.subTest(resume_event=resume_event):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    with patch("valp_cli.workflow.local_capabilities_path", return_value=root / "missing-capabilities.json"):
                        with patch("valp_cli.workflow.local_overlay_path", return_value=root / "missing-overlay.json"):
                            task_dir = publish_task(
                                root,
                                f"TASK-WAIT-{resume_event.upper()}",
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
                    suspend_task(root, f"TASK-WAIT-{resume_event.upper()}", timeout_seconds=60)
                    resume_ref = None
                    if resume_event == "runtime_failure":
                        failure_path = task_dir / "evidence" / "runtime-failure.log"
                        failure_path.parent.mkdir(parents=True, exist_ok=True)
                        failure_path.write_text("runtime failed\n", encoding="utf-8")
                        resume_ref = "evidence/runtime-failure.log"

                    resume_suspended_task(
                        root,
                        f"TASK-WAIT-{resume_event.upper()}",
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
                    submit=True,
                    runtime="queue",
                )

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
            self.assertIn("filtered for `codex`", codex_dispatch)
            self.assertIn("tdd", codex_dispatch)
            self.assertNotIn("verification-before-completion", codex_dispatch)
            self.assertIn("Full recommendation records remain in `skill-recommendations.json`", codex_dispatch)
            self.assertIn("Work item 1", codex_dispatch)
            self.assertNotIn("UNIQUE_SKILL_RECOMMENDATION_TAIL", codex_dispatch)


if __name__ == "__main__":
    unittest.main()
