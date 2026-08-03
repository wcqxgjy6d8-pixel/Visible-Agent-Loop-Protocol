from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import unittest

from jsonschema import Draft202012Validator

from valp_cli.protocol_kernel import (
    Evidence,
    Event,
    EventKind,
    EMPTY_REPLAY_PREFIX_DIGEST,
    CheckpointRoot,
    GenesisRoot,
    Identity,
    IdentityKind,
    IDEMPOTENCY_CONFLICT_ERROR,
    PROTOCOL_VERSION,
    ReplayEntry,
    Result,
    ResultVariant,
    STATE_CONFLICT_ERROR,
    State,
    TaskStatus,
    UNKNOWN_ENUM_ERROR,
    reduce,
    replay,
)


class ProtocolKernelTests(unittest.TestCase):
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

    def test_empty_replay_prefix_digest_is_a_cross_runtime_golden(self) -> None:
        self.assertEqual(
            EMPTY_REPLAY_PREFIX_DIGEST,
            "sha256:fa1f226ad4960367691ffda3176c5f45"
            "a463c102a791799d33dcf2bbfa08b54d",
        )

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
        return Event(
            event_id=Identity(IdentityKind.EVENT, event_id),
            installation_id=state.installation_id,
            leader_epoch=state.leader_epoch,
            task_id=state.task_id,
            kind=kind,
            expected_revision=state.revision,
        )

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
            result = reduce(current, self.event_for(current, f"{prefix}-reach-{index}", kind), ())
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
                result = reduce(state, self.event_for(state, f"edge-{index}", kind), ())
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
                result = reduce(state, self.event_for(state, f"illegal-{index}", kind), ())
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

    def test_structural_checkpoint_root_cannot_start_replay_before_stage_2(self) -> None:
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
