from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from valp_cli.protocol_kernel import (
    CancellationScope,
    ControlReason,
    ControlState,
    ControlStatus,
    Evidence,
    Event,
    EventKind,
    EMPTY_REPLAY_PREFIX_DIGEST,
    CheckpointAuthentication,
    CheckpointRoot,
    CheckpointTrustPolicy,
    GenesisRoot,
    Identity,
    IdentityKind,
    IDEMPOTENCY_CONFLICT_ERROR,
    PROTOCOL_VERSION,
    Replay,
    ReplayEntry,
    Result,
    ResultVariant,
    STATE_CONFLICT_ERROR,
    State,
    Suspension,
    SuspensionStatus,
    TaskStatus,
    Dependency,
    Attempt,
    AttemptStatus,
    WorkItem,
    WorkItemRequirement,
    WorkItemStatus,
    WakeReason,
    UNKNOWN_ENUM_ERROR,
    reduce,
    replay,
    replay_prefix_digest,
)


class ProtocolKernelTests(unittest.TestCase):
    WAIT_POLICY_DIGEST = "sha256:" + "1" * 64
    AUTHORITY_DIGEST = "sha256:" + "a" * 64

    def authority(self, name="authority"):
        evidence_id = Identity(IdentityKind.EVIDENCE, f"{name}-evidence")
        return {
            "authority_principal_id": Identity(IdentityKind.PRINCIPAL, f"{name}-principal"),
            "authority_evidence_id": evidence_id,
            "control_reason": ControlReason.USER_REQUESTED,
        }, (Evidence(evidence_id, self.AUTHORITY_DIGEST),)

    def make_executing_state(self, *, dependency_status=WorkItemStatus.COMPLETED):
        installation = Identity(IdentityKind.INSTALLATION, "installation-suspension")
        task = Identity(IdentityKind.TASK, "task-suspension")
        dependency = Identity(IdentityKind.WORK_ITEM, "dependency-suspension")
        state = State(
            PROTOCOL_VERSION,
            installation,
            1,
            task,
            0,
            TaskStatus.PUBLISHED,
            work_items=(WorkItem(
                task,
                dependency,
                WorkItemRequirement.REQUIRED,
                status=dependency_status,
            ),),
        )
        for index, kind in enumerate((
            EventKind.ROUTING_VALIDATION_STARTED,
            EventKind.ROUTING_VALIDATION_PASSED,
            EventKind.DISPATCH_ACCEPTED,
        )):
            result = reduce(state, Event(
                Identity(IdentityKind.EVENT, f"suspension-spine-{index}"),
                installation,
                1,
                task,
                kind,
                state.revision,
            ), ())
            self.assertEqual(result.variant, ResultVariant.ACCEPTED)
            state = result.accepted.state
        return state, dependency

    def suspension_event(self, state, dependency, *, event_id="suspend", epoch=0):
        return Event(
            event_id=Identity(IdentityKind.EVENT, event_id),
            installation_id=state.installation_id,
            leader_epoch=state.leader_epoch,
            task_id=state.task_id,
            kind=EventKind.SUSPENSION_STARTED,
            expected_revision=state.revision,
            suspension_id=Identity(IdentityKind.SUSPENSION, f"suspension-{epoch}"),
            suspension_epoch=epoch,
            wait_policy_id=Identity(IdentityKind.WAIT_POLICY, "wait-policy"),
            wait_policy_digest=self.WAIT_POLICY_DIGEST,
            required_work_item_ids=(dependency,),
        )

    def wake_event(self, state, *, event_id="wake", **changes):
        suspension = state.suspension
        values = {
            "event_id": Identity(IdentityKind.EVENT, event_id),
            "installation_id": state.installation_id,
            "leader_epoch": state.leader_epoch,
            "task_id": state.task_id,
            "kind": EventKind.WAKE_ACCEPTED,
            "expected_revision": state.revision,
            "suspension_id": suspension.suspension_id,
            "suspension_epoch": suspension.suspension_epoch,
            "wait_policy_id": suspension.wait_policy_id,
            "wait_policy_digest": suspension.wait_policy_digest,
            "required_work_item_ids": suspension.required_work_item_ids,
            "wake_id": Identity(IdentityKind.WAKE, "wake-0"),
            "wake_reason": WakeReason.DEPENDENCY_READY,
        }
        values.update(changes)
        return Event(**values)

    def test_suspension_and_dependency_ready_wake_are_kernel_truth(self) -> None:
        executing, dependency = self.make_executing_state()
        started = reduce(executing, self.suspension_event(executing, dependency), ())

        self.assertEqual(started.variant, ResultVariant.ACCEPTED)
        waiting = started.accepted.state
        self.assertEqual(waiting.status, TaskStatus.EXECUTING)
        self.assertEqual(waiting.suspension.status, SuspensionStatus.WAITING)
        self.assertEqual(waiting.suspension.required_work_item_ids, (dependency,))

        accepted = reduce(waiting, self.wake_event(waiting), ())

        self.assertEqual(accepted.variant, ResultVariant.ACCEPTED)
        self.assertEqual(accepted.accepted.state.status, TaskStatus.EXECUTING)
        self.assertEqual(accepted.accepted.state.suspension.status, SuspensionStatus.RESUMED)
        self.assertEqual(accepted.accepted.state.suspension.accepted_wake_id,
                         Identity(IdentityKind.WAKE, "wake-0"))
        self.assertEqual(accepted.accepted.obligations, ())

    def test_dependency_ready_wake_is_computed_from_work_item_truth(self) -> None:
        executing, dependency = self.make_executing_state(
            dependency_status=WorkItemStatus.RUNNING)
        waiting = reduce(
            executing, self.suspension_event(executing, dependency), ()
        ).accepted.state

        result = reduce(waiting, self.wake_event(waiting), ())

        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)
        self.assertIs(result.rejected.state, waiting)

    def test_wake_requires_exact_suspension_policy_frontier_and_cas_bindings(self) -> None:
        executing, dependency = self.make_executing_state()
        waiting = reduce(
            executing, self.suspension_event(executing, dependency), ()
        ).accepted.state
        cases = {
            "suspension": {"suspension_id": Identity(IdentityKind.SUSPENSION, "other")},
            "epoch": {"suspension_epoch": 1},
            "policy": {"wait_policy_digest": "sha256:" + "2" * 64},
            "frontier": {"required_work_item_ids": ()},
            "revision": {"expected_revision": waiting.revision - 1},
            "task": {"task_id": Identity(IdentityKind.TASK, "other-task")},
        }
        for name, changes in cases.items():
            with self.subTest(name=name):
                result = reduce(waiting, self.wake_event(
                    waiting, event_id=f"wake-{name}", **changes), ())
                self.assertEqual(result.variant, ResultVariant.REJECTED)
                self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_next_suspension_requires_new_identity_and_exact_next_epoch(self) -> None:
        executing, dependency = self.make_executing_state()
        waiting = reduce(
            executing, self.suspension_event(executing, dependency), ()
        ).accepted.state
        resumed = reduce(waiting, self.wake_event(waiting), ()).accepted.state

        for name, suspension_id, epoch in (
            ("same-id", waiting.suspension.suspension_id, 1),
            ("stale-epoch", Identity(IdentityKind.SUSPENSION, "new"), 0),
            ("skipped-epoch", Identity(IdentityKind.SUSPENSION, "new"), 2),
        ):
            with self.subTest(name=name):
                event = self.suspension_event(
                    resumed, dependency, event_id=f"start-{name}", epoch=epoch)
                event = replace(event, suspension_id=suspension_id)
                result = reduce(resumed, event, ())
                self.assertEqual(result.variant, ResultVariant.REJECTED)
                self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

        accepted = reduce(
            resumed, self.suspension_event(
                resumed, dependency, event_id="suspend-next", epoch=1), ())
        self.assertEqual(accepted.variant, ResultVariant.ACCEPTED)
        self.assertEqual(accepted.accepted.state.suspension.suspension_epoch, 1)

    def test_unknown_wake_reason_uses_closed_enum_error(self) -> None:
        executing, dependency = self.make_executing_state()
        waiting = reduce(
            executing, self.suspension_event(executing, dependency), ()
        ).accepted.state

        result = reduce(
            waiting, self.wake_event(waiting, wake_reason="future_reason"), ())

        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, UNKNOWN_ENUM_ERROR)

    def test_waiting_blocks_normal_progress_and_resume_clears_suspension(self) -> None:
        executing, dependency = self.make_executing_state()
        waiting = reduce(
            executing, self.suspension_event(executing, dependency), ()
        ).accepted.state
        work_completed = Event(
            Identity(IdentityKind.EVENT, "work-after-wait"),
            waiting.installation_id, waiting.leader_epoch, waiting.task_id,
            EventKind.WORK_COMPLETED, waiting.revision,
        )

        blocked = reduce(waiting, work_completed, ())
        self.assertEqual(blocked.variant, ResultVariant.REJECTED)
        self.assertEqual(blocked.rejected.error_code, STATE_CONFLICT_ERROR)

        resumed = reduce(waiting, self.wake_event(waiting), ()).accepted.state
        progressed = reduce(resumed, replace(
            work_completed,
            event_id=Identity(IdentityKind.EVENT, "work-after-resume"),
            expected_revision=resumed.revision,
        ), ())
        self.assertEqual(progressed.variant, ResultVariant.ACCEPTED)
        self.assertEqual(progressed.accepted.state.status, TaskStatus.VERIFYING)
        self.assertIsNone(progressed.accepted.state.suspension)

    def test_explicit_terminal_event_clears_waiting_suspension(self) -> None:
        for kind, target in (
            (EventKind.TASK_BLOCKED, TaskStatus.BLOCKED),
            (EventKind.TASK_FAILED, TaskStatus.FAILED),
            (EventKind.TASK_CANCELLED, TaskStatus.CANCELLED),
        ):
            with self.subTest(kind=kind.value):
                executing, dependency = self.make_executing_state()
                waiting = reduce(
                    executing, self.suspension_event(executing, dependency), ()
                ).accepted.state
                authority, evidence = self.authority(f"terminate-{kind.value}")
                extra = {}
                if kind == EventKind.TASK_CANCELLED:
                    extra = {
                        **authority,
                        "cancellation_scope": CancellationScope.TASK,
                        "suspension_epoch": waiting.suspension.suspension_epoch,
                    }
                result = reduce(waiting, Event(
                    Identity(IdentityKind.EVENT, f"terminate-{kind.value}"),
                    waiting.installation_id, waiting.leader_epoch, waiting.task_id,
                    kind, waiting.revision, **extra,
                ), evidence if kind == EventKind.TASK_CANCELLED else ())
                self.assertEqual(result.variant, ResultVariant.ACCEPTED)
                self.assertEqual(result.accepted.state.status, target)
                self.assertIsNone(result.accepted.state.suspension)

    def test_suspension_events_are_idempotent_and_replay_without_obligations(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "installation-replay-suspension")
        task = Identity(IdentityKind.TASK, "task-replay-suspension")
        dependency = Identity(IdentityKind.WORK_ITEM, "dependency-replay-suspension")
        genesis = State(
            PROTOCOL_VERSION, installation, 1, task, 0, TaskStatus.PUBLISHED,
            work_items=(WorkItem(
                task, dependency, WorkItemRequirement.REQUIRED,
                status=WorkItemStatus.COMPLETED,
            ),),
        )
        current = genesis
        entries = []
        for index, kind in enumerate((
            EventKind.ROUTING_VALIDATION_STARTED,
            EventKind.ROUTING_VALIDATION_PASSED,
            EventKind.DISPATCH_ACCEPTED,
        )):
            event = Event(
                Identity(IdentityKind.EVENT, f"replay-suspension-spine-{index}"),
                installation, 1, task, kind, current.revision,
            )
            result = reduce(current, event, ())
            entries.append(ReplayEntry(event, (), result))
            current = result.accepted.state

        start_event = self.suspension_event(current, dependency)
        started = reduce(current, start_event, ())
        duplicate = reduce(started.accepted.state, start_event, ())
        self.assertEqual(duplicate.variant, ResultVariant.NO_OP)
        wake_event = self.wake_event(started.accepted.state)
        woken = reduce(started.accepted.state, wake_event, ())
        entries.extend((
            ReplayEntry(start_event, (), started),
            ReplayEntry(wake_event, (), woken),
        ))

        replayed = replay(GenesisRoot(genesis), tuple(entries))

        self.assertEqual(replayed.state, woken.accepted.state)
        self.assertEqual(replayed.obligations, ())

    def test_task_only_canonical_bytes_remain_unchanged_without_suspension(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "legacy-installation")
        task = Identity(IdentityKind.TASK, "legacy-task")
        state = State(PROTOCOL_VERSION, installation, 7, task, 0, TaskStatus.PUBLISHED)
        canonical_bytes = json.dumps(
            state.canonical(), sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ) + "\n"
        self.assertEqual(canonical_bytes,
            '{"accepted_events":[],"installation_id":{"kind":"installation","value":"legacy-installation"},'
            '"leader_epoch":7,"protocol_version":"0.3.0-draft","revision":0,'
            '"schema_version":"valp-kernel-state.v1","status":"published",'
            '"task_id":{"kind":"task","value":"legacy-task"}}\n')

    def test_authorized_attempt_cancellation_emits_reconcilable_effect_and_fences_late_output(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "installation-cancel")
        task = Identity(IdentityKind.TASK, "task-cancel")
        work = Identity(IdentityKind.WORK_ITEM, "work-cancel")
        attempt_id = Identity(IdentityKind.ATTEMPT, "attempt-cancel")
        dispatch = Identity(IdentityKind.DISPATCH, "dispatch-cancel")
        state = State(
            PROTOCOL_VERSION, installation, 1, task, 0, TaskStatus.PUBLISHED,
            work_items=(WorkItem(
                task, work, WorkItemRequirement.REQUIRED, WorkItemStatus.RUNNING,
                current_attempt=Attempt(
                    task, work, attempt_id, dispatch, 4, AttemptStatus.RUNNING,
                ),
            ),),
        )
        authority, evidence = self.authority("cancel-attempt")
        event = Event(
            Identity(IdentityKind.EVENT, "cancel-attempt"), installation, 1, task,
            EventKind.ATTEMPT_CANCELLED, 0,
            work_item_id=work, attempt_id=attempt_id, dispatch_id=dispatch,
            dispatch_generation=4, cancellation_scope=CancellationScope.ATTEMPT,
            **authority,
        )

        missing_authority = reduce(state, event, ())
        self.assertEqual(missing_authority.variant, ResultVariant.REJECTED)
        accepted = reduce(state, event, evidence)
        self.assertEqual(accepted.variant, ResultVariant.ACCEPTED)
        self.assertEqual(
            accepted.accepted.state.work_items[0].current_attempt.status,
            AttemptStatus.CANCELLED,
        )
        self.assertEqual(len(accepted.accepted.obligations), 1)
        self.assertTrue(accepted.accepted.obligations[0].startswith("adapter_cancel:"))
        late = reduce(accepted.accepted.state, replace(
            event,
            event_id=Identity(IdentityKind.EVENT, "late-after-cancel"),
            kind=EventKind.ATTEMPT_COMPLETED,
            expected_revision=1,
            authority_principal_id=None,
            authority_evidence_id=None,
            control_reason=None,
            cancellation_scope=None,
        ), ())
        self.assertEqual(late.variant, ResultVariant.REJECTED)

    def test_interrupt_freezes_progress_until_exact_authorized_resume(self) -> None:
        executing, _ = self.make_executing_state()
        authority, evidence = self.authority("interrupt")
        interrupt_id = Identity(IdentityKind.INTERRUPT, "interrupt-1")
        interrupted = reduce(executing, Event(
            Identity(IdentityKind.EVENT, "interrupt-requested"),
            executing.installation_id, executing.leader_epoch, executing.task_id,
            EventKind.INTERRUPT_REQUESTED, executing.revision,
            interrupt_id=interrupt_id, intent_version=0, **authority,
        ), evidence)
        self.assertEqual(interrupted.variant, ResultVariant.ACCEPTED)
        frozen = interrupted.accepted.state
        self.assertEqual(frozen.control.status, ControlStatus.INTERRUPTED)

        progress = reduce(frozen, Event(
            Identity(IdentityKind.EVENT, "progress-while-interrupted"),
            frozen.installation_id, frozen.leader_epoch, frozen.task_id,
            EventKind.WORK_COMPLETED, frozen.revision,
        ), ())
        self.assertEqual(progress.variant, ResultVariant.REJECTED)

        resume_authority, resume_evidence = self.authority("resume")
        resumed = reduce(frozen, Event(
            Identity(IdentityKind.EVENT, "interrupt-resumed"),
            frozen.installation_id, frozen.leader_epoch, frozen.task_id,
            EventKind.INTERRUPT_RESUMED, frozen.revision,
            interrupt_id=interrupt_id, intent_version=0, **resume_authority,
        ), resume_evidence)
        self.assertEqual(resumed.variant, ResultVariant.ACCEPTED)
        self.assertEqual(resumed.accepted.state.control.status, ControlStatus.ACTIVE)

    def test_redirect_versions_intent_cancels_invalidated_work_and_enters_fixing(self) -> None:
        executing, dependency = self.make_executing_state(
            dependency_status=WorkItemStatus.RUNNING
        )
        attempt_id = Identity(IdentityKind.ATTEMPT, "redirect-attempt")
        dispatch = Identity(IdentityKind.DISPATCH, "redirect-dispatch")
        executing = replace(executing, work_items=(replace(
            executing.work_items[0],
            current_attempt=Attempt(
                executing.task_id, dependency, attempt_id, dispatch, 2,
                AttemptStatus.RUNNING,
            ),
        ),))
        authority, evidence = self.authority("redirect")
        redirected = reduce(executing, Event(
            Identity(IdentityKind.EVENT, "redirect-authorized"),
            executing.installation_id, executing.leader_epoch, executing.task_id,
            EventKind.REDIRECT_AUTHORIZED, executing.revision,
            redirect_id=Identity(IdentityKind.REDIRECT, "redirect-1"),
            intent_version=0, next_intent_version=1,
            superseded_work_item_ids=(dependency,), **authority,
        ), evidence)

        self.assertEqual(redirected.variant, ResultVariant.ACCEPTED)
        state = redirected.accepted.state
        self.assertEqual(state.status, TaskStatus.FIXING)
        self.assertEqual(state.control, ControlState(1, ControlStatus.ACTIVE, None))
        self.assertEqual(state.work_items[0].status, WorkItemStatus.CANCELLED)
        self.assertEqual(state.work_items[0].current_attempt.status, AttemptStatus.CANCELLED)
        self.assertEqual(len(redirected.accepted.obligations), 1)

        stale = reduce(state, Event(
            Identity(IdentityKind.EVENT, "stale-redirect"),
            state.installation_id, state.leader_epoch, state.task_id,
            EventKind.REDIRECT_AUTHORIZED, state.revision,
            redirect_id=Identity(IdentityKind.REDIRECT, "redirect-2"),
            intent_version=0, next_intent_version=1,
            superseded_work_item_ids=(), **authority,
        ), evidence)
        self.assertEqual(stale.variant, ResultVariant.REJECTED)

    def test_dependency_satisfied_work_item_becomes_eligible(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "installation-work")
        task = Identity(IdentityKind.TASK, "task-work")
        dependency_id = Identity(IdentityKind.WORK_ITEM, "dependency")
        work_item_id = Identity(IdentityKind.WORK_ITEM, "target")
        state = State(
            protocol_version=PROTOCOL_VERSION,
            installation_id=installation,
            leader_epoch=1,
            task_id=task,
            revision=0,
            status=TaskStatus.PUBLISHED,
            work_items=(
                WorkItem(task_id=task, work_item_id=dependency_id,
                         requirement=WorkItemRequirement.REQUIRED,
                         status=WorkItemStatus.COMPLETED),
                WorkItem(task_id=task, work_item_id=work_item_id,
                         requirement=WorkItemRequirement.REQUIRED,
                         dependencies=(Dependency(dependency_id, WorkItemRequirement.REQUIRED),)),
            ),
        )
        result = reduce(
            state,
            Event(
                event_id=Identity(IdentityKind.EVENT, "eligible-target"),
                installation_id=installation,
                leader_epoch=1,
                task_id=task,
                kind=EventKind.WORK_ITEM_ELIGIBLE,
                expected_revision=0,
                work_item_id=work_item_id,
            ),
            (),
        )

        self.assertEqual(result.variant, ResultVariant.ACCEPTED)
        self.assertEqual(result.accepted.state.work_items[1].status, WorkItemStatus.ELIGIBLE)

    def test_eligible_work_item_creates_identity_bound_attempt(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "installation-attempt")
        task = Identity(IdentityKind.TASK, "task-attempt")
        work_item_id = Identity(IdentityKind.WORK_ITEM, "work-attempt")
        attempt_id = Identity(IdentityKind.ATTEMPT, "attempt-1")
        dispatch_id = Identity(IdentityKind.DISPATCH, "dispatch-1")
        state = State(
            protocol_version=PROTOCOL_VERSION, installation_id=installation,
            leader_epoch=1, task_id=task, revision=0, status=TaskStatus.PUBLISHED,
            work_items=(WorkItem(task, work_item_id, WorkItemRequirement.REQUIRED,
                                 status=WorkItemStatus.ELIGIBLE),),
        )

        result = reduce(state, Event(
            event_id=Identity(IdentityKind.EVENT, "create-attempt"),
            installation_id=installation, leader_epoch=1, task_id=task,
            kind=EventKind.ATTEMPT_CREATED, expected_revision=0,
            work_item_id=work_item_id, attempt_id=attempt_id,
            dispatch_id=dispatch_id, dispatch_generation=0,
        ), ())

        self.assertEqual(result.variant, ResultVariant.ACCEPTED)
        item = result.accepted.state.work_items[0]
        self.assertEqual(item.status, WorkItemStatus.SUBMITTED)
        self.assertEqual(item.current_attempt.attempt_id, attempt_id)
        self.assertEqual(item.current_attempt.dispatch_generation, 0)

    def test_duplicate_current_attempt_created_is_an_idempotent_no_op(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "installation-attempt-no-op")
        task = Identity(IdentityKind.TASK, "task-attempt-no-op")
        work = Identity(IdentityKind.WORK_ITEM, "work-attempt-no-op")
        event = Event(
            event_id=Identity(IdentityKind.EVENT, "create-attempt-no-op"),
            installation_id=installation,
            leader_epoch=1,
            task_id=task,
            kind=EventKind.ATTEMPT_CREATED,
            expected_revision=0,
            work_item_id=work,
            attempt_id=Identity(IdentityKind.ATTEMPT, "attempt-no-op"),
            dispatch_id=Identity(IdentityKind.DISPATCH, "dispatch-no-op"),
            dispatch_generation=0,
        )
        initial = State(
            protocol_version=PROTOCOL_VERSION,
            installation_id=installation,
            leader_epoch=1,
            task_id=task,
            revision=0,
            status=TaskStatus.PUBLISHED,
            work_items=(WorkItem(
                task,
                work,
                WorkItemRequirement.REQUIRED,
                status=WorkItemStatus.ELIGIBLE,
            ),),
        )
        accepted = reduce(initial, event, ())

        duplicate = reduce(accepted.accepted.state, event, ())

        self.assertEqual(duplicate.variant, ResultVariant.NO_OP)
        self.assertIs(duplicate.no_op.state, accepted.accepted.state)

    def test_fenced_attempt_rejects_late_completion_for_the_same_generation(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "installation-fence")
        task = Identity(IdentityKind.TASK, "task-fence")
        work_item_id = Identity(IdentityKind.WORK_ITEM, "work-fence")
        attempt_id = Identity(IdentityKind.ATTEMPT, "attempt-fence")
        dispatch_id = Identity(IdentityKind.DISPATCH, "dispatch-fence")
        attempt = Attempt(task, work_item_id, attempt_id, dispatch_id, 3,
                          status=AttemptStatus.RUNNING)
        state = State(
            protocol_version=PROTOCOL_VERSION, installation_id=installation,
            leader_epoch=1, task_id=task, revision=0, status=TaskStatus.PUBLISHED,
            work_items=(WorkItem(task, work_item_id, WorkItemRequirement.REQUIRED,
                                 status=WorkItemStatus.RUNNING, current_attempt=attempt),),
        )
        fence = Event(
            event_id=Identity(IdentityKind.EVENT, "fence"), installation_id=installation,
            leader_epoch=1, task_id=task, kind=EventKind.ATTEMPT_FENCED,
            expected_revision=0, work_item_id=work_item_id, attempt_id=attempt_id,
            dispatch_id=dispatch_id, dispatch_generation=3,
        )
        fenced = reduce(state, fence, ())

        self.assertEqual(fenced.variant, ResultVariant.ACCEPTED)
        self.assertEqual(fenced.accepted.state.work_items[0].current_attempt.status, AttemptStatus.FENCED)
        stale = reduce(fenced.accepted.state, Event(
            event_id=Identity(IdentityKind.EVENT, "late-completion"),
            installation_id=installation, leader_epoch=1, task_id=task,
            kind=EventKind.ATTEMPT_COMPLETED, expected_revision=1,
            work_item_id=work_item_id, attempt_id=attempt_id,
            dispatch_id=dispatch_id, dispatch_generation=3,
        ), ())
        self.assertEqual(stale.variant, ResultVariant.REJECTED)
        self.assertEqual(stale.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_fenced_attempt_rejects_failed_and_cancelled_events(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "install-fenced-terminal")
        task = Identity(IdentityKind.TASK, "task-fenced-terminal")
        work = Identity(IdentityKind.WORK_ITEM, "work-fenced-terminal")
        attempt = Identity(IdentityKind.ATTEMPT, "attempt-fenced-terminal")
        dispatch = Identity(IdentityKind.DISPATCH, "dispatch-fenced-terminal")
        state = State(PROTOCOL_VERSION, installation, 1, task, 0, TaskStatus.PUBLISHED,
                      work_items=(WorkItem(task, work, WorkItemRequirement.REQUIRED,
                          status=WorkItemStatus.RUNNING,
                          current_attempt=Attempt(task, work, attempt, dispatch, 0,
                              AttemptStatus.FENCED)),))
        for kind in (EventKind.ATTEMPT_FAILED, EventKind.ATTEMPT_CANCELLED):
            with self.subTest(kind=kind.value):
                result = reduce(state, Event(
                    Identity(IdentityKind.EVENT, f"fenced-{kind.value}"), installation,
                    1, task, kind, 0, work, attempt, dispatch, 0), ())
                self.assertEqual(result.variant, ResultVariant.REJECTED)
                self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_terminal_attempts_reject_fencing(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "install-terminal-fence")
        task = Identity(IdentityKind.TASK, "task-terminal-fence")
        work = Identity(IdentityKind.WORK_ITEM, "work-terminal-fence")
        attempt_id = Identity(IdentityKind.ATTEMPT, "attempt-terminal-fence")
        dispatch = Identity(IdentityKind.DISPATCH, "dispatch-terminal-fence")
        for attempt_status, work_status in (
            (AttemptStatus.COMPLETED, WorkItemStatus.COMPLETED),
            (AttemptStatus.FAILED, WorkItemStatus.FAILED),
            (AttemptStatus.CANCELLED, WorkItemStatus.CANCELLED),
        ):
            with self.subTest(status=attempt_status.value):
                state = State(
                    PROTOCOL_VERSION,
                    installation,
                    1,
                    task,
                    0,
                    TaskStatus.PUBLISHED,
                    work_items=(WorkItem(
                        task,
                        work,
                        WorkItemRequirement.REQUIRED,
                        status=work_status,
                        current_attempt=Attempt(
                            task,
                            work,
                            attempt_id,
                            dispatch,
                            0,
                            attempt_status,
                        ),
                    ),),
                )
                result = reduce(state, Event(
                    Identity(IdentityKind.EVENT, f"fence-{attempt_status.value}"),
                    installation,
                    1,
                    task,
                    EventKind.ATTEMPT_FENCED,
                    0,
                    work,
                    attempt_id,
                    dispatch,
                    0,
                ), ())
                self.assertEqual(result.variant, ResultVariant.REJECTED)
                self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_terminal_attempts_reject_failed_and_cancelled_even_if_work_item_is_running(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "install-terminal-event")
        task = Identity(IdentityKind.TASK, "task-terminal-event")
        work = Identity(IdentityKind.WORK_ITEM, "work-terminal-event")
        attempt_id = Identity(IdentityKind.ATTEMPT, "attempt-terminal-event")
        dispatch = Identity(IdentityKind.DISPATCH, "dispatch-terminal-event")
        for attempt_status in (
            AttemptStatus.COMPLETED,
            AttemptStatus.FAILED,
            AttemptStatus.CANCELLED,
        ):
            for kind in (EventKind.ATTEMPT_FAILED, EventKind.ATTEMPT_CANCELLED):
                with self.subTest(status=attempt_status.value, kind=kind.value):
                    state = State(
                        PROTOCOL_VERSION,
                        installation,
                        1,
                        task,
                        0,
                        TaskStatus.PUBLISHED,
                        work_items=(WorkItem(
                            task,
                            work,
                            WorkItemRequirement.REQUIRED,
                            status=WorkItemStatus.RUNNING,
                            current_attempt=Attempt(
                                task,
                                work,
                                attempt_id,
                                dispatch,
                                0,
                                attempt_status,
                            ),
                        ),),
                    )
                    result = reduce(state, Event(
                        Identity(IdentityKind.EVENT, f"{kind.value}-{attempt_status.value}"),
                        installation,
                        1,
                        task,
                        kind,
                        0,
                        work,
                        attempt_id,
                        dispatch,
                        0,
                    ), ())
                    self.assertEqual(result.variant, ResultVariant.REJECTED)
                    self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_blocked_retry_requires_new_attempt_identity_and_higher_generation(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "install-retry")
        task = Identity(IdentityKind.TASK, "task-retry")
        work = Identity(IdentityKind.WORK_ITEM, "work-retry")
        attempt = Identity(IdentityKind.ATTEMPT, "attempt-retry")
        dispatch = Identity(IdentityKind.DISPATCH, "dispatch-retry")
        state = State(PROTOCOL_VERSION, installation, 1, task, 0, TaskStatus.PUBLISHED,
                      work_items=(WorkItem(task, work, WorkItemRequirement.REQUIRED,
                          status=WorkItemStatus.BLOCKED,
                          current_attempt=Attempt(task, work, attempt, dispatch, 0,
                              AttemptStatus.FENCED)),))
        result = reduce(state, Event(
            Identity(IdentityKind.EVENT, "same-attempt-retry"), installation, 1, task,
            EventKind.ATTEMPT_CREATED, 0, work, attempt,
            Identity(IdentityKind.DISPATCH, "dispatch-retry-1"), 1), ())
        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_superseded_accepted_attempt_event_rejects_before_idempotency_no_op(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "install-superseded")
        task = Identity(IdentityKind.TASK, "task-superseded")
        work = Identity(IdentityKind.WORK_ITEM, "work-superseded")
        old_attempt = Identity(IdentityKind.ATTEMPT, "attempt-old")
        old_dispatch = Identity(IdentityKind.DISPATCH, "dispatch-old")
        state = State(PROTOCOL_VERSION, installation, 1, task, 0, TaskStatus.PUBLISHED,
                      work_items=(WorkItem(task, work, WorkItemRequirement.REQUIRED,
                                           status=WorkItemStatus.ELIGIBLE),))
        old_create = Event(Identity(IdentityKind.EVENT, "old-create"), installation, 1, task,
                           EventKind.ATTEMPT_CREATED, 0, work, old_attempt, old_dispatch, 0)
        state = reduce(state, old_create, ()).accepted.state
        state = reduce(state, Event(Identity(IdentityKind.EVENT, "block-old"), installation,
            1, task, EventKind.WORK_ITEM_BLOCKED, 1, work), ()).accepted.state
        state = reduce(state, Event(Identity(IdentityKind.EVENT, "retry-new"), installation,
            1, task, EventKind.ATTEMPT_CREATED, 2, work,
            Identity(IdentityKind.ATTEMPT, "attempt-new"),
            Identity(IdentityKind.DISPATCH, "dispatch-new"), 1), ()).accepted.state

        stale = reduce(state, old_create, ())

        self.assertEqual(stale.variant, ResultVariant.REJECTED)
        self.assertEqual(stale.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_attempt_spine_reaches_completed_work_item_through_public_reduce(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "installation-spine")
        task = Identity(IdentityKind.TASK, "task-spine")
        work = Identity(IdentityKind.WORK_ITEM, "work-spine")
        attempt = Identity(IdentityKind.ATTEMPT, "attempt-spine")
        dispatch = Identity(IdentityKind.DISPATCH, "dispatch-spine")
        state = State(PROTOCOL_VERSION, installation, 1, task, 0, TaskStatus.PUBLISHED,
                      work_items=(WorkItem(task, work, WorkItemRequirement.REQUIRED,
                                           status=WorkItemStatus.ELIGIBLE),))
        for index, kind in enumerate((
            EventKind.ATTEMPT_CREATED, EventKind.ATTEMPT_SUBMITTED,
            EventKind.ATTEMPT_RUNNING, EventKind.ATTEMPT_COMPLETED,
        )):
            result = reduce(state, Event(
                event_id=Identity(IdentityKind.EVENT, f"spine-{index}"),
                installation_id=installation, leader_epoch=1, task_id=task,
                kind=kind, expected_revision=state.revision, work_item_id=work,
                attempt_id=attempt, dispatch_id=dispatch, dispatch_generation=0,
            ), ())
            self.assertEqual(result.variant, ResultVariant.ACCEPTED)
            state = result.accepted.state
        self.assertEqual(state.work_items[0].status, WorkItemStatus.COMPLETED)
        self.assertEqual(state.work_items[0].current_attempt.status, AttemptStatus.COMPLETED)

    def test_required_incomplete_dependency_cannot_become_eligible(self) -> None:
        state, event = self.make_transition()
        dependency = Identity(IdentityKind.WORK_ITEM, "required-dependency")
        target = Identity(IdentityKind.WORK_ITEM, "required-target")
        state = replace(state, work_items=(
            WorkItem(state.task_id, dependency, WorkItemRequirement.REQUIRED),
            WorkItem(state.task_id, target, WorkItemRequirement.REQUIRED,
                     dependencies=(Dependency(dependency, WorkItemRequirement.REQUIRED),)),
        ))
        result = reduce(state, replace(event, kind=EventKind.WORK_ITEM_ELIGIBLE,
                                       work_item_id=target), ())
        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def test_optional_and_soft_dependencies_are_eligible_with_soft_audit_fact(self) -> None:
        state, event = self.make_transition()
        dependency = Identity(IdentityKind.WORK_ITEM, "nonrequired-dependency")
        for requirement, expects_soft_fact in (
            (WorkItemRequirement.OPTIONAL, False),
            (WorkItemRequirement.SOFT, True),
        ):
            with self.subTest(requirement=requirement.value):
                target = Identity(IdentityKind.WORK_ITEM, f"target-{requirement.value}")
                candidate = replace(state, work_items=(
                    WorkItem(state.task_id, dependency, WorkItemRequirement.REQUIRED),
                    WorkItem(state.task_id, target, WorkItemRequirement.REQUIRED,
                             dependencies=(Dependency(dependency, requirement),)),
                ))
                result = reduce(candidate, replace(event, event_id=Identity(IdentityKind.EVENT,
                    f"eligible-{requirement.value}"), kind=EventKind.WORK_ITEM_ELIGIBLE,
                    work_item_id=target), ())
                self.assertEqual(result.variant, ResultVariant.ACCEPTED)
                self.assertEqual(any(fact.startswith("soft_dependency_unmet")
                    for fact in result.accepted.audit_facts), expects_soft_fact)

    def test_work_item_targeted_by_required_dependency_cannot_be_skipped(self) -> None:
        state, event = self.make_transition()
        prerequisite = Identity(IdentityKind.WORK_ITEM, "required-edge-prerequisite")
        dependent = Identity(IdentityKind.WORK_ITEM, "required-edge-dependent")
        state = replace(state, work_items=(
            WorkItem(state.task_id, prerequisite, WorkItemRequirement.OPTIONAL),
            WorkItem(
                state.task_id,
                dependent,
                WorkItemRequirement.REQUIRED,
                dependencies=(Dependency(
                    prerequisite,
                    WorkItemRequirement.REQUIRED,
                ),),
            ),
        ))

        result = reduce(state, replace(
            event,
            event_id=Identity(IdentityKind.EVENT, "skip-required-edge-target"),
            kind=EventKind.WORK_ITEM_SKIPPED,
            work_item_id=prerequisite,
        ), ())

        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)

    def machine_contract_validator(self) -> Draft202012Validator:
        schema = json.loads(
            (Path(__file__).parents[1] / "schemas/protocol-kernel.schema.json").read_text(
                encoding="utf-8"
            )
        )
        return Draft202012Validator(schema)

    def make_transition(self):
        installation_id = Identity(IdentityKind.INSTALLATION, "installation-1")
        task_id = Identity(IdentityKind.TASK, "task-1")
        return (
            State(
                protocol_version=PROTOCOL_VERSION,
                installation_id=installation_id,
                leader_epoch=1,
                task_id=task_id,
                revision=0,
                status=TaskStatus.PUBLISHED,
            ),
            Event(
                event_id=Identity(IdentityKind.EVENT, "event-1"),
                installation_id=installation_id,
                leader_epoch=1,
                task_id=task_id,
                kind=EventKind.ROUTING_VALIDATION_STARTED,
                expected_revision=0,
            ),
        )

    def make_authenticated_checkpoint(self):
        initial, _ = self.make_transition()
        sequence = (
            EventKind.ROUTING_VALIDATION_STARTED,
            EventKind.ROUTING_VALIDATION_PASSED,
            EventKind.DISPATCH_ACCEPTED,
        )
        current = initial
        entries = []
        for index, kind in enumerate(sequence):
            event = self.event_for(current, f"checkpoint-replay-{index}", kind)
            result = reduce(current, event, ())
            self.assertEqual(result.variant, ResultVariant.ACCEPTED)
            entries.append(ReplayEntry(event, (), result))
            current = result.accepted.state
        prefix = tuple(entries[:2])
        checkpoint_result = prefix[-1].result
        checkpoint_state = checkpoint_result.accepted.state
        trusted_evidence_id = Identity(IdentityKind.EVIDENCE, "checkpoint-authority-1")
        policy = CheckpointTrustPolicy((trusted_evidence_id,))
        root = CheckpointRoot(
            state=checkpoint_state,
            accepted_entry_count=len(prefix),
            prefix_digest=replay_prefix_digest(prefix),
            tail_event_id=checkpoint_state.accepted_events[-1].event_id,
            tail_result_id=checkpoint_state.accepted_events[-1].result_id,
            tail_result_digest=checkpoint_state.accepted_events[-1].result_digest,
            checkpoint_result_id=checkpoint_result.accepted.result_id,
            trust_policy_digest=policy.digest,
        )
        statement_digest = CheckpointAuthentication.statement_digest_for(
            root, checkpoint_result, policy
        )
        authentication = CheckpointAuthentication(
            checkpoint_result=checkpoint_result,
            evidence_set=(Evidence(trusted_evidence_id, statement_digest),),
            trust_policy=policy,
        )
        return initial, tuple(entries), root, authentication

    def test_empty_replay_prefix_digest_is_a_cross_runtime_golden(self) -> None:
        self.assertEqual(
            EMPTY_REPLAY_PREFIX_DIGEST,
            "sha256:fa1f226ad4960367691ffda3176c5f45"
            "a463c102a791799d33dcf2bbfa08b54d",
        )

    def test_authenticated_checkpoint_prefix_digest_is_a_cross_runtime_golden(self) -> None:
        _, _, root, _ = self.make_authenticated_checkpoint()

        self.assertEqual(
            root.prefix_digest,
            "sha256:96debe3c80c80eda97052e4277458381"
            "f9f40db274a695bea563de1f1fd8321a",
        )

    def test_task_only_state_canonical_form_remains_compatible(self) -> None:
        state, _ = self.make_transition()
        self.assertEqual(state.canonical(), {
            "schema_version": "valp-kernel-state.v1",
            "protocol_version": PROTOCOL_VERSION,
            "installation_id": {"kind": "installation", "value": "installation-1"},
            "leader_epoch": 1,
            "task_id": {"kind": "task", "value": "task-1"},
            "revision": 0,
            "status": "published",
            "accepted_events": [],
        })

    def test_nonempty_work_item_table_is_present_in_canonical_state(self) -> None:
        state, _ = self.make_transition()
        work = Identity(IdentityKind.WORK_ITEM, "canonical-work")
        state = replace(state, work_items=(WorkItem(
            state.task_id,
            work,
            WorkItemRequirement.REQUIRED,
        ),))

        canonical = state.canonical()

        self.assertEqual(canonical["work_items"], [
            {
                "task_id": state.task_id.canonical(),
                "work_item_id": work.canonical(),
                "requirement": "required",
                "status": "pending",
                "dependencies": [],
                "current_attempt": None,
            },
        ])
        self.machine_contract_validator().validate(canonical)

    def test_dependency_has_canonical_machine_contract_object(self) -> None:
        task = Identity(IdentityKind.TASK, "dependency-canonical-task")
        prerequisite = Identity(IdentityKind.WORK_ITEM, "dependency-canonical-source")
        target = Identity(IdentityKind.WORK_ITEM, "dependency-canonical-target")
        state = State(
            PROTOCOL_VERSION,
            Identity(IdentityKind.INSTALLATION, "dependency-canonical-installation"),
            1,
            task,
            0,
            TaskStatus.PUBLISHED,
            work_items=(
                WorkItem(task, prerequisite, WorkItemRequirement.REQUIRED),
                WorkItem(
                    task,
                    target,
                    WorkItemRequirement.REQUIRED,
                    dependencies=(Dependency(
                        prerequisite, WorkItemRequirement.REQUIRED),),
                ),
            ),
        )

        self.assertEqual(state.canonical()["work_items"][1]["dependencies"], [{
            "work_item_id": prerequisite.canonical(),
            "requirement": "required",
        }])
        self.machine_contract_validator().validate(state.canonical())

    def test_work_event_schema_requires_its_exact_identity_fields(self) -> None:
        state, _ = self.make_transition()
        validator = self.machine_contract_validator()
        attempt_without_tuple = Event(
            Identity(IdentityKind.EVENT, "schema-attempt"), state.installation_id,
            state.leader_epoch, state.task_id, EventKind.ATTEMPT_FENCED, 0)
        work_without_item = Event(
            Identity(IdentityKind.EVENT, "schema-work"), state.installation_id,
            state.leader_epoch, state.task_id, EventKind.WORK_ITEM_ELIGIBLE, 0)
        self.assertFalse(validator.is_valid(attempt_without_tuple.canonical()))
        self.assertFalse(validator.is_valid(work_without_item.canonical()))

    def test_genesis_replay_rebuilds_nonempty_work_item_attempt_spine_without_obligations(self) -> None:
        installation = Identity(IdentityKind.INSTALLATION, "installation-replay-work")
        task = Identity(IdentityKind.TASK, "task-replay-work")
        work = Identity(IdentityKind.WORK_ITEM, "work-replay-work")
        attempt = Identity(IdentityKind.ATTEMPT, "attempt-replay-work")
        dispatch = Identity(IdentityKind.DISPATCH, "dispatch-replay-work")
        state = State(PROTOCOL_VERSION, installation, 1, task, 0, TaskStatus.PUBLISHED,
                      work_items=(WorkItem(task, work, WorkItemRequirement.REQUIRED),))
        entries = []
        for index, kind in enumerate((EventKind.WORK_ITEM_ELIGIBLE, EventKind.ATTEMPT_CREATED,
                                      EventKind.ATTEMPT_SUBMITTED, EventKind.ATTEMPT_RUNNING,
                                      EventKind.ATTEMPT_COMPLETED)):
            event = Event(Identity(IdentityKind.EVENT, f"replay-work-{index}"), installation,
                1, task, kind, state.revision, work,
                attempt if kind != EventKind.WORK_ITEM_ELIGIBLE else None,
                dispatch if kind != EventKind.WORK_ITEM_ELIGIBLE else None,
                0 if kind != EventKind.WORK_ITEM_ELIGIBLE else None)
            result = reduce(state, event, ())
            self.assertEqual(result.variant, ResultVariant.ACCEPTED)
            entries.append(ReplayEntry(event, (), result))
            state = result.accepted.state
        replayed = replay(GenesisRoot(State(PROTOCOL_VERSION, installation, 1, task, 0,
            TaskStatus.PUBLISHED, work_items=(WorkItem(task, work, WorkItemRequirement.REQUIRED),))), entries)
        self.assertEqual(replayed.state, state)
        self.assertEqual(replayed.obligations, ())

    def test_routing_validation_transition_is_accepted(self) -> None:
        state, event = self.make_transition()

        result = reduce(state, event, ())

        self.assertEqual(result.variant, ResultVariant.ACCEPTED)
        self.assertIsNotNone(result.accepted)
        self.assertEqual(result.accepted.state.revision, 1)
        self.assertEqual(result.accepted.state.status, TaskStatus.ROUTING_VALIDATION)
        self.assertEqual(result.accepted.obligations, ())

    def test_routing_validation_passed_transitions_to_dispatching(self) -> None:
        state, event = self.make_transition()
        routing = reduce(state, event, ()).accepted.state
        passed = Event(
            event_id=Identity(IdentityKind.EVENT, "event-2"),
            installation_id=routing.installation_id,
            leader_epoch=routing.leader_epoch,
            task_id=routing.task_id,
            kind=EventKind.ROUTING_VALIDATION_PASSED,
            expected_revision=routing.revision,
        )

        result = reduce(routing, passed, ())

        self.assertEqual(result.variant, ResultVariant.ACCEPTED)
        self.assertEqual(result.accepted.state.status, TaskStatus.DISPATCHING)

    def event_for(self, state, event_id: str, kind: EventKind) -> Event:
        work_item_id = None
        attempt_id = None
        dispatch_id = None
        dispatch_generation = None
        suspension_fields = {}
        control_fields = {}
        if kind.value.startswith("work_item_"):
            work_item_id = Identity(IdentityKind.WORK_ITEM, f"work-{event_id}")
        if kind.value.startswith("attempt_"):
            work_item_id = Identity(IdentityKind.WORK_ITEM, f"work-{event_id}")
            attempt_id = Identity(IdentityKind.ATTEMPT, f"attempt-{event_id}")
            dispatch_id = Identity(IdentityKind.DISPATCH, f"dispatch-{event_id}")
            dispatch_generation = 0
        if kind in {EventKind.SUSPENSION_STARTED, EventKind.WAKE_ACCEPTED}:
            suspension_fields = {
                "suspension_id": Identity(IdentityKind.SUSPENSION, f"suspension-{event_id}"),
                "suspension_epoch": 0,
                "wait_policy_id": Identity(IdentityKind.WAIT_POLICY, f"policy-{event_id}"),
                "wait_policy_digest": self.WAIT_POLICY_DIGEST,
                "required_work_item_ids": (
                    Identity(IdentityKind.WORK_ITEM, f"required-{event_id}"),
                ),
            }
        if kind == EventKind.WAKE_ACCEPTED:
            suspension_fields.update({
                "wake_id": Identity(IdentityKind.WAKE, f"wake-{event_id}"),
                "wake_reason": WakeReason.DEPENDENCY_READY,
            })
        if kind in {
            EventKind.TASK_CANCELLED,
            EventKind.WORK_ITEM_CANCELLED,
            EventKind.ATTEMPT_CANCELLED,
            EventKind.INTERRUPT_REQUESTED,
            EventKind.INTERRUPT_RESUMED,
            EventKind.REDIRECT_AUTHORIZED,
        }:
            control_fields = {
                "authority_principal_id": Identity(
                    IdentityKind.PRINCIPAL, f"principal-{event_id}"
                ),
                "authority_evidence_id": Identity(
                    IdentityKind.EVIDENCE, f"evidence-{event_id}"
                ),
                "control_reason": ControlReason.USER_REQUESTED,
            }
        if kind in {
            EventKind.TASK_CANCELLED,
            EventKind.WORK_ITEM_CANCELLED,
            EventKind.ATTEMPT_CANCELLED,
        }:
            control_fields["cancellation_scope"] = {
                EventKind.TASK_CANCELLED: CancellationScope.TASK,
                EventKind.WORK_ITEM_CANCELLED: CancellationScope.WORK_ITEM,
                EventKind.ATTEMPT_CANCELLED: CancellationScope.ATTEMPT,
            }[kind]
        if kind in {EventKind.INTERRUPT_REQUESTED, EventKind.INTERRUPT_RESUMED}:
            control_fields.update({
                "interrupt_id": Identity(IdentityKind.INTERRUPT, f"interrupt-{event_id}"),
                "intent_version": 0,
            })
        if kind == EventKind.REDIRECT_AUTHORIZED:
            control_fields.update({
                "redirect_id": Identity(IdentityKind.REDIRECT, f"redirect-{event_id}"),
                "intent_version": 0,
                "next_intent_version": 1,
            })
        return Event(
            event_id=Identity(IdentityKind.EVENT, event_id),
            installation_id=state.installation_id,
            leader_epoch=state.leader_epoch,
            task_id=state.task_id,
            kind=kind,
            expected_revision=state.revision,
            work_item_id=work_item_id,
            attempt_id=attempt_id,
            dispatch_id=dispatch_id,
            dispatch_generation=dispatch_generation,
            **suspension_fields,
            **control_fields,
        )

    def evidence_for_event(self, event: Event):
        if event.authority_evidence_id is None:
            return ()
        return (Evidence(event.authority_evidence_id, self.AUTHORITY_DIGEST),)

    def state_reached_from_genesis(self, target: TaskStatus, prefix: str) -> State:
        initial, _ = self.make_transition()
        paths = {
            TaskStatus.PUBLISHED: (),
            TaskStatus.ROUTING_VALIDATION: (EventKind.ROUTING_VALIDATION_STARTED,),
            TaskStatus.DISPATCHING: (
                EventKind.ROUTING_VALIDATION_STARTED,
                EventKind.ROUTING_VALIDATION_PASSED,
            ),
            TaskStatus.EXECUTING: (
                EventKind.ROUTING_VALIDATION_STARTED,
                EventKind.ROUTING_VALIDATION_PASSED,
                EventKind.DISPATCH_ACCEPTED,
            ),
            TaskStatus.VERIFYING: (
                EventKind.ROUTING_VALIDATION_STARTED,
                EventKind.ROUTING_VALIDATION_PASSED,
                EventKind.DISPATCH_ACCEPTED,
                EventKind.WORK_COMPLETED,
            ),
            TaskStatus.REVIEWING: (
                EventKind.ROUTING_VALIDATION_STARTED,
                EventKind.ROUTING_VALIDATION_PASSED,
                EventKind.DISPATCH_ACCEPTED,
                EventKind.WORK_COMPLETED,
                EventKind.VERIFICATION_PASSED,
            ),
            TaskStatus.FIXING: (
                EventKind.ROUTING_VALIDATION_STARTED,
                EventKind.ROUTING_VALIDATION_PASSED,
                EventKind.DISPATCH_ACCEPTED,
                EventKind.WORK_COMPLETED,
                EventKind.VERIFICATION_FAILED,
            ),
            TaskStatus.APPROVAL_REQUIRED: (
                EventKind.ROUTING_VALIDATION_STARTED,
                EventKind.ROUTING_VALIDATION_PASSED,
                EventKind.DISPATCH_ACCEPTED,
                EventKind.WORK_COMPLETED,
                EventKind.VERIFICATION_PASSED,
                EventKind.APPROVAL_REQUIRED_RAISED,
            ),
            TaskStatus.RECORDING: (
                EventKind.ROUTING_VALIDATION_STARTED,
                EventKind.ROUTING_VALIDATION_PASSED,
                EventKind.DISPATCH_ACCEPTED,
                EventKind.WORK_COMPLETED,
                EventKind.VERIFICATION_PASSED,
                EventKind.REVIEW_PASSED,
            ),
            TaskStatus.DONE: (
                EventKind.ROUTING_VALIDATION_STARTED,
                EventKind.ROUTING_VALIDATION_PASSED,
                EventKind.DISPATCH_ACCEPTED,
                EventKind.WORK_COMPLETED,
                EventKind.VERIFICATION_PASSED,
                EventKind.REVIEW_PASSED,
                EventKind.RECORDING_COMPLETED,
            ),
            TaskStatus.BLOCKED: (
                EventKind.ROUTING_VALIDATION_STARTED,
                EventKind.ROUTING_VALIDATION_PASSED,
                EventKind.DISPATCH_ACCEPTED,
                EventKind.TASK_BLOCKED,
            ),
            TaskStatus.FAILED: (EventKind.TASK_FAILED,),
            TaskStatus.CANCELLED: (EventKind.TASK_CANCELLED,),
        }
        current = initial
        for index, kind in enumerate(paths[target]):
            event = self.event_for(current, f"{prefix}-reach-{index}", kind)
            result = reduce(current, event, self.evidence_for_event(event))
            self.assertEqual(result.variant, ResultVariant.ACCEPTED)
            current = result.accepted.state
        self.assertEqual(current.status, target)
        return current

    def test_every_specified_kernel_edge_is_accepted(self) -> None:
        explicit_edges = (
            (TaskStatus.PUBLISHED, EventKind.ROUTING_VALIDATION_STARTED, TaskStatus.ROUTING_VALIDATION),
            (TaskStatus.ROUTING_VALIDATION, EventKind.ROUTING_VALIDATION_PASSED, TaskStatus.DISPATCHING),
            (TaskStatus.DISPATCHING, EventKind.DISPATCH_ACCEPTED, TaskStatus.EXECUTING),
            (TaskStatus.EXECUTING, EventKind.WORK_COMPLETED, TaskStatus.VERIFYING),
            (TaskStatus.VERIFYING, EventKind.VERIFICATION_PASSED, TaskStatus.REVIEWING),
            (TaskStatus.VERIFYING, EventKind.VERIFICATION_FAILED, TaskStatus.FIXING),
            (TaskStatus.REVIEWING, EventKind.REVIEW_PASSED, TaskStatus.RECORDING),
            (TaskStatus.REVIEWING, EventKind.REVIEW_REJECTED, TaskStatus.FIXING),
            (TaskStatus.REVIEWING, EventKind.APPROVAL_REQUIRED_RAISED, TaskStatus.APPROVAL_REQUIRED),
            (TaskStatus.FIXING, EventKind.FIX_DISPATCH_REQUESTED, TaskStatus.DISPATCHING),
            (TaskStatus.APPROVAL_REQUIRED, EventKind.APPROVAL_GRANTED, TaskStatus.RECORDING),
            (TaskStatus.APPROVAL_REQUIRED, EventKind.APPROVAL_DENIED, TaskStatus.FIXING),
            (TaskStatus.RECORDING, EventKind.RECORDING_COMPLETED, TaskStatus.DONE),
            (TaskStatus.EXECUTING, EventKind.TASK_BLOCKED, TaskStatus.BLOCKED),
            (TaskStatus.VERIFYING, EventKind.TASK_BLOCKED, TaskStatus.BLOCKED),
            (TaskStatus.REVIEWING, EventKind.TASK_BLOCKED, TaskStatus.BLOCKED),
            (TaskStatus.FIXING, EventKind.TASK_BLOCKED, TaskStatus.BLOCKED),
            (TaskStatus.BLOCKED, EventKind.BLOCKED_RECOVERY_TO_FIXING, TaskStatus.FIXING),
        )
        non_terminals = tuple(
            status
            for status in TaskStatus
            if status not in {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}
        )
        terminal_edges = tuple(
            (status, EventKind.TASK_FAILED, TaskStatus.FAILED)
            for status in non_terminals
        ) + tuple(
            (status, EventKind.TASK_CANCELLED, TaskStatus.CANCELLED)
            for status in non_terminals
        )

        for index, (source, kind, target) in enumerate(explicit_edges + terminal_edges):
            with self.subTest(source=source.value, kind=kind.value):
                state = self.state_reached_from_genesis(source, f"edge-{index}")
                event = self.event_for(state, f"edge-{index}", kind)
                result = reduce(state, event, self.evidence_for_event(event))
                self.assertEqual(result.variant, ResultVariant.ACCEPTED)
                self.assertEqual(result.accepted.state.status, target)
                self.assertEqual(result.accepted.state.revision, state.revision + 1)

    def test_event_kind_vocabulary_is_closed_and_matches_machine_contract(self) -> None:
        self.assertEqual(
            {kind.value for kind in EventKind},
            {
                "routing_validation_started", "routing_validation_passed", "dispatch_accepted",
                "work_completed", "verification_passed", "verification_failed", "review_passed",
                "review_rejected", "approval_required_raised", "fix_dispatch_requested",
                "approval_granted", "approval_denied", "recording_completed", "task_blocked",
                "blocked_recovery_to_fixing", "task_failed", "task_cancelled",
                "work_item_eligible", "attempt_created", "attempt_submitted", "attempt_running",
                "attempt_completed", "attempt_failed", "attempt_cancelled", "attempt_fenced",
                "work_item_partial", "work_item_degraded", "work_item_blocked",
                "work_item_failed", "work_item_cancelled", "work_item_skipped",
                "suspension_started", "wake_accepted", "interrupt_requested",
                "interrupt_resumed", "redirect_authorized",
            },
        )
        state, _ = self.make_transition()
        for index, kind in enumerate(EventKind):
            with self.subTest(kind=kind.value):
                self.machine_contract_validator().validate(
                    self.event_for(state, f"schema-event-{index}", kind).canonical()
                )

    def test_illegal_and_terminal_edges_fail_closed(self) -> None:
        cases = (
            (TaskStatus.PUBLISHED, EventKind.WORK_COMPLETED),
            (TaskStatus.VERIFYING, EventKind.DISPATCH_ACCEPTED),
            (TaskStatus.APPROVAL_REQUIRED, EventKind.RECORDING_COMPLETED),
            (TaskStatus.BLOCKED, EventKind.WORK_COMPLETED),
            (TaskStatus.DONE, EventKind.TASK_CANCELLED),
            (TaskStatus.FAILED, EventKind.TASK_FAILED),
            (TaskStatus.CANCELLED, EventKind.TASK_BLOCKED),
        )
        for index, (status, kind) in enumerate(cases):
            with self.subTest(status=status.value, kind=kind.value):
                state = self.state_reached_from_genesis(status, f"illegal-{index}")
                event = self.event_for(state, f"illegal-{index}", kind)
                result = reduce(state, event, self.evidence_for_event(event))
                self.assertEqual(result.variant, ResultVariant.REJECTED)
                self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)
                self.assertIs(result.rejected.state, state)

    def test_forged_state_revision_and_history_are_rejected(self) -> None:
        initial, _ = self.make_transition()
        forged_routing = replace(initial, status=TaskStatus.ROUTING_VALIDATION)
        forged_nonzero_empty = replace(initial, revision=1)
        reached_routing = self.state_reached_from_genesis(TaskStatus.ROUTING_VALIDATION, "mismatch")
        forged_count = replace(reached_routing, revision=2)
        cases = (
            (forged_routing, EventKind.ROUTING_VALIDATION_PASSED, False),
            (forged_nonzero_empty, EventKind.ROUTING_VALIDATION_STARTED, False),
            (forged_count, EventKind.ROUTING_VALIDATION_PASSED, True),
        )
        for index, (state, kind, schema_valid) in enumerate(cases):
            with self.subTest(status=state.status.value, revision=state.revision):
                self.assertEqual(
                    self.machine_contract_validator().is_valid(state.canonical()),
                    schema_valid,
                )
                result = reduce(state, self.event_for(state, f"forged-{index}", kind), ())
                self.assertEqual(result.variant, ResultVariant.REJECTED)
                self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)
                self.assertIs(result.rejected.state, state)

    def test_genesis_replay_recomputes_multi_step_spine_without_obligations(self) -> None:
        initial, _ = self.make_transition()
        sequence = (
            EventKind.ROUTING_VALIDATION_STARTED,
            EventKind.ROUTING_VALIDATION_PASSED,
            EventKind.DISPATCH_ACCEPTED,
            EventKind.WORK_COMPLETED,
            EventKind.VERIFICATION_PASSED,
            EventKind.REVIEW_PASSED,
            EventKind.RECORDING_COMPLETED,
        )
        current = initial
        entries = []
        for index, kind in enumerate(sequence):
            event = self.event_for(current, f"replay-{index}", kind)
            result = reduce(current, event, ())
            self.assertEqual(result.variant, ResultVariant.ACCEPTED)
            entries.append(ReplayEntry(event=event, evidence_set=(), result=result))
            current = result.accepted.state

        replayed = replay(GenesisRoot(state=initial), tuple(entries))

        self.assertEqual(replayed.state, current)
        self.assertEqual(replayed.state.status, TaskStatus.DONE)
        self.assertEqual(replayed.applied_result_digests, tuple(
            entry.result.accepted.result_digest for entry in entries
        ))
        self.assertEqual(replayed.obligations, ())

    def test_identical_duplicate_is_no_op_bound_to_prior_result(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())

        duplicate = reduce(accepted.accepted.state, event, ())

        self.assertEqual(duplicate.variant, ResultVariant.NO_OP)
        self.assertIs(duplicate.no_op.state, accepted.accepted.state)
        self.assertEqual(
            duplicate.no_op.prior_result_digest,
            accepted.accepted.result_digest,
        )

    def test_changed_content_with_same_event_identity_is_rejected(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())
        conflicting = Event(
            event_id=event.event_id,
            installation_id=event.installation_id,
            leader_epoch=event.leader_epoch,
            task_id=event.task_id,
            kind=event.kind,
            expected_revision=1,
        )

        rejected = reduce(accepted.accepted.state, conflicting, ())

        self.assertEqual(rejected.variant, ResultVariant.REJECTED)
        self.assertIs(rejected.rejected.state, accepted.accepted.state)
        self.assertEqual(rejected.rejected.error_code, IDEMPOTENCY_CONFLICT_ERROR)
        self.assertEqual(
            rejected.rejected.prior_result_id,
            accepted.accepted.result_id,
        )
        self.assertEqual(
            rejected.rejected.prior_result_digest,
            accepted.accepted.result_digest,
        )

    def test_unknown_event_kind_fails_with_closed_error(self) -> None:
        state, event = self.make_transition()
        unknown = Event(
            event_id=event.event_id,
            installation_id=event.installation_id,
            leader_epoch=event.leader_epoch,
            task_id=event.task_id,
            kind="future_event_kind",
            expected_revision=event.expected_revision,
        )

        result = reduce(state, unknown, ())

        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertIs(result.rejected.state, state)
        self.assertEqual(result.rejected.error_code, UNKNOWN_ENUM_ERROR)

    def test_unknown_task_status_fails_with_closed_error(self) -> None:
        state, event = self.make_transition()
        unknown = replace(state, status="future_task_status")

        result = reduce(unknown, event, ())

        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertIs(result.rejected.state, unknown)
        self.assertEqual(result.rejected.error_code, UNKNOWN_ENUM_ERROR)

    def test_replay_is_deterministic_and_emits_no_obligations(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())
        root = GenesisRoot(state=state)
        entry = ReplayEntry(event=event, evidence_set=(), result=accepted)

        first = replay(root, (entry,))
        second = replay(root, (entry,))

        self.assertEqual(first, second)
        self.assertEqual(first.state, accepted.accepted.state)
        self.assertEqual(first.obligations, ())
        self.machine_contract_validator().validate(first.canonical())
        self.assertEqual(
            first.applied_result_digests,
            (accepted.accepted.result_digest,),
        )

    def test_replay_rejects_tampered_accepted_state(self) -> None:
        state, event = self.make_transition()
        result = reduce(state, event, ())
        tampered_state = replace(
            result.accepted.state,
            status=TaskStatus.DONE,
            accepted_events=(),
        )
        tampered = Result(
            accepted=replace(result.accepted, state=tampered_state),
        )
        entry = ReplayEntry(event=event, evidence_set=(), result=tampered)

        with self.assertRaises(ValueError):
            replay(GenesisRoot(state=state), (entry,))

    def test_replay_rejects_tampered_result_digest(self) -> None:
        state, event = self.make_transition()
        result = reduce(state, event, ())
        tampered = Result(
            accepted=replace(
                result.accepted,
                result_digest="sha256:" + "0" * 64,
            ),
        )
        entry = ReplayEntry(event=event, evidence_set=(), result=tampered)

        with self.assertRaises(ValueError):
            replay(GenesisRoot(state=state), (entry,))

    def test_replay_rejects_bare_state_root(self) -> None:
        state, _ = self.make_transition()

        with self.assertRaises(ValueError):
            replay(state, ())

    def test_replay_rejects_impossible_genesis_revision(self) -> None:
        state, _ = self.make_transition()
        invalid_root = GenesisRoot(state=replace(state, revision=7))

        self.assertFalse(
            self.machine_contract_validator().is_valid(invalid_root.canonical())
        )

        with self.assertRaises(ValueError):
            replay(invalid_root, ())

    def test_replay_rejects_nonempty_genesis_history(self) -> None:
        state, event = self.make_transition()
        result = reduce(state, event, ())
        impossible = replace(state, accepted_events=result.accepted.state.accepted_events)

        with self.assertRaises(ValueError):
            replay(GenesisRoot(state=impossible), ())

    def test_checkpoint_root_is_a_schema_valid_structural_contract(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())
        accepted_event = accepted.accepted.state.accepted_events[-1]
        root = CheckpointRoot(
            state=accepted.accepted.state,
            accepted_entry_count=1,
            prefix_digest="sha256:" + "a" * 64,
            tail_event_id=accepted_event.event_id,
            tail_result_id=accepted_event.result_id,
            tail_result_digest=accepted_event.result_digest,
            checkpoint_result_id=Identity(IdentityKind.RESULT, "checkpoint-1"),
            trust_policy_digest="sha256:" + "b" * 64,
        )

        self.machine_contract_validator().validate(root.canonical())
        self.assertEqual(root.state_digest, root.state_digest)

    def test_checkpoint_authentication_is_schema_valid(self) -> None:
        _, _, _, authentication = self.make_authenticated_checkpoint()
        validator = self.machine_contract_validator()

        validator.validate(authentication.trust_policy.canonical())
        validator.validate(authentication.canonical())

    def test_checkpoint_authentication_rejects_duplicate_evidence_identities(self) -> None:
        _, _, _, authentication = self.make_authenticated_checkpoint()
        evidence = authentication.evidence_set[0]

        with self.assertRaisesRegex(ValueError, "unique Evidence identities"):
            CheckpointAuthentication(
                checkpoint_result=authentication.checkpoint_result,
                evidence_set=(evidence, evidence),
                trust_policy=authentication.trust_policy,
            )

    def test_checkpoint_trust_policy_is_nonempty_unique_and_canonical(self) -> None:
        first = Identity(IdentityKind.EVIDENCE, "checkpoint-authority-a")
        second = Identity(IdentityKind.EVIDENCE, "checkpoint-authority-b")

        self.assertEqual(
            CheckpointTrustPolicy((second, first)).digest,
            CheckpointTrustPolicy((first, second)).digest,
        )
        for identities in (
            (),
            (first, first),
            (Identity(IdentityKind.TASK, "not-evidence"),),
        ):
            with self.subTest(identities=identities):
                with self.assertRaises(ValueError):
                    CheckpointTrustPolicy(identities)

    def test_authenticated_checkpoint_suffix_matches_full_genesis_replay(self) -> None:
        initial, entries, root, authentication = self.make_authenticated_checkpoint()

        full = replay(GenesisRoot(initial), tuple(entries))
        suffix = replay(root, tuple(entries[2:]), authentication)

        self.assertEqual(suffix.state, full.state)
        self.assertEqual(
            suffix.applied_result_digests,
            (entries[2].result.accepted.result_digest,),
        )
        self.assertEqual(suffix.obligations, ())

    def test_invalid_checkpoint_authentication_fails_before_reading_suffix(self) -> None:
        _, _, root, authentication = self.make_authenticated_checkpoint()
        trusted_id = authentication.trust_policy.trusted_evidence_ids[0]
        other_id = Identity(IdentityKind.EVIDENCE, "checkpoint-authority-other")
        other_policy = CheckpointTrustPolicy((other_id,))
        cases = (
            None,
            replace(authentication, trust_policy=other_policy),
            replace(authentication, evidence_set=()),
            replace(
                authentication,
                evidence_set=authentication.evidence_set
                + (Evidence(other_id, authentication.evidence_set[0].content_digest),),
            ),
            replace(
                authentication,
                evidence_set=(Evidence(trusted_id, "sha256:" + "0" * 64),),
            ),
            replace(
                authentication,
                evidence_set=(
                    Evidence(
                        Identity(IdentityKind.TASK, trusted_id.value),
                        authentication.evidence_set[0].content_digest,
                    ),
                ),
            ),
        )

        def unread_suffix():
            raise AssertionError("invalid authentication read a suffix entry")
            yield

        for case in cases:
            with self.subTest(authentication=case):
                with self.assertRaisesRegex(ValueError, "authentication is invalid"):
                    replay(root, unread_suffix(), case)

    def test_checkpoint_result_and_root_binding_mismatches_fail_closed(self) -> None:
        _, entries, root, authentication = self.make_authenticated_checkpoint()
        accepted = authentication.checkpoint_result.accepted
        wrong_result_id = Identity(IdentityKind.RESULT, "wrong-checkpoint-result")
        cases = (
            (
                root,
                replace(
                    authentication,
                    checkpoint_result=Result(accepted=replace(
                        accepted,
                        result_id=wrong_result_id,
                    )),
                ),
            ),
            (
                root,
                replace(
                    authentication,
                    checkpoint_result=Result(accepted=replace(
                        accepted,
                        result_digest="sha256:" + "0" * 64,
                    )),
                ),
            ),
            (
                root,
                replace(authentication, checkpoint_result=entries[0].result),
            ),
            (
                replace(root, checkpoint_result_id=wrong_result_id),
                authentication,
            ),
            (
                replace(root, prefix_digest="sha256:" + "0" * 64),
                authentication,
            ),
        )

        def unread_suffix():
            raise AssertionError("invalid checkpoint binding read a suffix entry")
            yield

        for changed_root, changed_authentication in cases:
            with self.subTest(root=changed_root, authentication=changed_authentication):
                with self.assertRaisesRegex(ValueError, "authentication is invalid"):
                    replay(changed_root, unread_suffix(), changed_authentication)

    def test_checkpoint_suffix_rejects_gaps_identity_drift_and_recorded_mismatch(self) -> None:
        _, entries, root, authentication = self.make_authenticated_checkpoint()
        suffix = entries[2]
        wrong_installation = Identity(IdentityKind.INSTALLATION, "wrong-installation")
        wrong_task = Identity(IdentityKind.TASK, "wrong-task")
        cases = (
            entries[1],
            replace(suffix, event=replace(
                suffix.event,
                expected_revision=suffix.event.expected_revision + 1,
            )),
            replace(suffix, event=replace(
                suffix.event,
                installation_id=wrong_installation,
            )),
            replace(suffix, event=replace(
                suffix.event,
                leader_epoch=suffix.event.leader_epoch + 1,
            )),
            replace(suffix, event=replace(
                suffix.event,
                task_id=wrong_task,
            )),
            replace(suffix, result=Result(accepted=replace(
                suffix.result.accepted,
                result_digest="sha256:" + "0" * 64,
            ))),
            replace(suffix, result=Result(accepted=replace(
                suffix.result.accepted,
                obligations=("must-not-be-reemitted",),
            ))),
        )

        for changed_suffix in cases:
            with self.subTest(suffix=changed_suffix):
                with self.assertRaises(ValueError):
                    replay(root, (changed_suffix,), authentication)

    def test_checkpoint_suffix_rejects_reordering_and_duplicate_identities(self) -> None:
        _, entries, root, authentication = self.make_authenticated_checkpoint()
        first = entries[2]
        next_event = self.event_for(
            first.result.accepted.state,
            "checkpoint-replay-3",
            EventKind.WORK_COMPLETED,
        )
        next_result = reduce(first.result.accepted.state, next_event, ())
        self.assertEqual(next_result.variant, ResultVariant.ACCEPTED)
        second = ReplayEntry(next_event, (), next_result)
        duplicate_result = replace(
            second,
            result=Result(accepted=replace(
                second.result.accepted,
                result_id=first.result.accepted.result_id,
            )),
        )

        cases = (
            (second, first),
            (first, first),
            (first, duplicate_result),
        )
        for changed_suffix in cases:
            with self.subTest(suffix=changed_suffix):
                with self.assertRaises(ValueError):
                    replay(root, changed_suffix, authentication)

    def test_checkpoint_root_rejects_count_or_tail_binding_mismatch(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())
        accepted_event = accepted.accepted.state.accepted_events[-1]
        fields = {
            "state": accepted.accepted.state,
            "accepted_entry_count": 1,
            "prefix_digest": "sha256:" + "a" * 64,
            "tail_event_id": accepted_event.event_id,
            "tail_result_id": accepted_event.result_id,
            "tail_result_digest": accepted_event.result_digest,
            "checkpoint_result_id": Identity(IdentityKind.RESULT, "checkpoint-1"),
            "trust_policy_digest": "sha256:" + "b" * 64,
        }

        with self.assertRaises(ValueError):
            CheckpointRoot(**{**fields, "accepted_entry_count": 0})
        with self.assertRaises(ValueError):
            CheckpointRoot(
                **{
                    **fields,
                    "tail_event_id": Identity(IdentityKind.EVENT, "other-event"),
                }
            )

    def test_checkpoint_schema_rejects_nonzero_revision_with_empty_history(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())
        accepted_event = accepted.accepted.state.accepted_events[-1]
        root = CheckpointRoot(
            state=accepted.accepted.state,
            accepted_entry_count=1,
            prefix_digest="sha256:" + "a" * 64,
            tail_event_id=accepted_event.event_id,
            tail_result_id=accepted_event.result_id,
            tail_result_digest=accepted_event.result_digest,
            checkpoint_result_id=Identity(IdentityKind.RESULT, "checkpoint-1"),
            trust_policy_digest="sha256:" + "b" * 64,
        ).canonical()
        root["state"]["accepted_events"] = []

        self.assertFalse(self.machine_contract_validator().is_valid(root))

    def test_checkpoint_root_without_authentication_is_rejected(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())
        accepted_event = accepted.accepted.state.accepted_events[-1]
        root = CheckpointRoot(
            state=accepted.accepted.state,
            accepted_entry_count=1,
            prefix_digest="sha256:" + "a" * 64,
            tail_event_id=accepted_event.event_id,
            tail_result_id=accepted_event.result_id,
            tail_result_digest=accepted_event.result_digest,
            checkpoint_result_id=Identity(IdentityKind.RESULT, "checkpoint-1"),
            trust_policy_digest="sha256:" + "b" * 64,
        )

        with self.assertRaises(ValueError):
            replay(root, ())

    def test_replay_envelope_cannot_be_constructed_with_obligations(self) -> None:
        state, _ = self.make_transition()

        with self.assertRaises(ValueError):
            Replay(state, (), ("must-not-be-reemitted",))

    def test_replay_recomputes_event_and_evidence_inputs(self) -> None:
        state, event = self.make_transition()
        recorded = reduce(state, event, ())
        different_evidence = (
            Evidence(
                evidence_id=Identity(IdentityKind.EVIDENCE, "evidence-1"),
                content_digest="sha256:" + "a" * 64,
            ),
        )
        entry = ReplayEntry(
            event=event,
            evidence_set=different_evidence,
            result=recorded,
        )

        with self.assertRaises(ValueError):
            replay(GenesisRoot(state=state), (entry,))

    def test_replay_rejects_non_accepted_result(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())
        no_op = reduce(accepted.accepted.state, event, ())
        entry = ReplayEntry(event=event, evidence_set=(), result=no_op)

        self.assertFalse(
            self.machine_contract_validator().is_valid(entry.canonical())
        )

        with self.assertRaises(ValueError):
            replay(GenesisRoot(state=state), (entry,))

    def test_replay_rejects_duplicate_entry_already_in_ledger(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())
        entry = ReplayEntry(event=event, evidence_set=(), result=accepted)

        with self.assertRaises(ValueError):
            replay(GenesisRoot(state=state), (entry, entry))

    def test_identity_kinds_and_leader_binding_cannot_be_substituted(self) -> None:
        state, event = self.make_transition()
        wrong_identity = Event(
            event_id=Identity(IdentityKind.TASK, "event-1"),
            installation_id=event.installation_id,
            leader_epoch=event.leader_epoch,
            task_id=event.task_id,
            kind=event.kind,
            expected_revision=event.expected_revision,
        )

        result = reduce(state, wrong_identity, ())

        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)
        self.assertIs(result.rejected.state, state)

    def test_evidence_identity_kind_cannot_be_substituted(self) -> None:
        state, event = self.make_transition()
        evidence = Evidence(
            evidence_id=Identity(IdentityKind.TASK, "evidence-1"),
            content_digest="sha256:" + "a" * 64,
        )
        self.assertFalse(
            self.machine_contract_validator().is_valid(evidence.canonical())
        )

        result = reduce(state, event, (evidence,))

        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)
        self.assertIs(result.rejected.state, state)

    def test_invalid_evidence_digest_is_rejected(self) -> None:
        state, event = self.make_transition()
        evidence = Evidence(
            evidence_id=Identity(IdentityKind.EVIDENCE, "evidence-1"),
            content_digest="not-a-sha256-digest",
        )
        self.assertFalse(
            self.machine_contract_validator().is_valid(evidence.canonical())
        )

        result = reduce(state, event, (evidence,))

        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)
        self.assertIs(result.rejected.state, state)

    def test_negative_leader_epoch_is_rejected(self) -> None:
        state, event = self.make_transition()
        invalid_state = replace(state, leader_epoch=-1)
        invalid_event = replace(event, leader_epoch=-1)
        validator = self.machine_contract_validator()
        self.assertFalse(validator.is_valid(invalid_state.canonical()))
        self.assertFalse(validator.is_valid(invalid_event.canonical()))

        result = reduce(invalid_state, invalid_event, ())

        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)
        self.assertIs(result.rejected.state, invalid_state)

    def test_negative_revision_is_rejected(self) -> None:
        state, event = self.make_transition()
        invalid_state = replace(state, revision=-1)
        invalid_event = replace(event, expected_revision=-1)
        validator = self.machine_contract_validator()
        self.assertFalse(validator.is_valid(invalid_state.canonical()))
        self.assertFalse(validator.is_valid(invalid_event.canonical()))

        result = reduce(invalid_state, invalid_event, ())

        self.assertEqual(result.variant, ResultVariant.REJECTED)
        self.assertEqual(result.rejected.error_code, STATE_CONFLICT_ERROR)
        self.assertIs(result.rejected.state, invalid_state)

    def test_result_contains_exactly_one_variant(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())
        no_op = reduce(accepted.accepted.state, event, ())

        with self.assertRaises(ValueError):
            Result()
        with self.assertRaises(ValueError):
            Result(accepted=accepted.accepted, no_op=no_op.no_op)

    def test_canonical_artifacts_match_the_machine_contract(self) -> None:
        state, event = self.make_transition()
        accepted = reduce(state, event, ())
        no_op = reduce(accepted.accepted.state, event, ())
        replay_root = GenesisRoot(state=state)
        replay_entry = ReplayEntry(event=event, evidence_set=(), result=accepted)
        unknown = Event(
            event_id=Identity(IdentityKind.EVENT, "event-2"),
            installation_id=event.installation_id,
            leader_epoch=event.leader_epoch,
            task_id=event.task_id,
            kind="future_event_kind",
            expected_revision=accepted.accepted.state.revision,
        )
        rejected = reduce(accepted.accepted.state, unknown, ())
        validator = self.machine_contract_validator()

        for artifact in (
            state.canonical(),
            event.canonical(),
            accepted.canonical(),
            no_op.canonical(),
            rejected.canonical(),
            replay_root.canonical(),
            replay_entry.canonical(),
        ):
            validator.validate(artifact)


if __name__ == "__main__":
    unittest.main()
