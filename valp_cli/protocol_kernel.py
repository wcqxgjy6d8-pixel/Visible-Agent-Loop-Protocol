"""Pure Protocol Kernel Slice 1 plus a structural CheckpointRoot contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple, Union


PROTOCOL_VERSION = "0.3.0-draft"


class IdentityKind(str, Enum):
    INSTALLATION = "installation"
    TASK = "task"
    WORK_ITEM = "work_item"
    ATTEMPT = "attempt"
    DISPATCH = "dispatch"
    EVENT = "event"
    EVIDENCE = "evidence"
    RECEIPT = "receipt"
    CLAIM = "claim"
    RESULT = "result"


class TaskStatus(str, Enum):
    PUBLISHED = "published"
    ROUTING_VALIDATION = "routing_validation"
    DISPATCHING = "dispatching"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    FIXING = "fixing"
    APPROVAL_REQUIRED = "approval_required"
    RECORDING = "recording"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkItemStatus(str, Enum):
    PENDING = "pending"
    ELIGIBLE = "eligible"
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class WorkItemRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    SOFT = "soft"


class AttemptStatus(str, Enum):
    CREATED = "created"
    SUBMITTED = "submitted"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    FENCED = "fenced"


class ClaimResult(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    NOT_APPLICABLE = "not_applicable"


class EventKind(str, Enum):
    ROUTING_VALIDATION_STARTED = "routing_validation_started"
    ROUTING_VALIDATION_PASSED = "routing_validation_passed"
    DISPATCH_ACCEPTED = "dispatch_accepted"
    WORK_COMPLETED = "work_completed"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    REVIEW_PASSED = "review_passed"
    REVIEW_REJECTED = "review_rejected"
    APPROVAL_REQUIRED_RAISED = "approval_required_raised"
    FIX_DISPATCH_REQUESTED = "fix_dispatch_requested"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    RECORDING_COMPLETED = "recording_completed"
    TASK_BLOCKED = "task_blocked"
    BLOCKED_RECOVERY_TO_FIXING = "blocked_recovery_to_fixing"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"


KERNEL_TASK_TRANSITIONS: Mapping[Tuple[TaskStatus, EventKind], TaskStatus] = {
    (TaskStatus.PUBLISHED, EventKind.ROUTING_VALIDATION_STARTED): TaskStatus.ROUTING_VALIDATION,
    (TaskStatus.ROUTING_VALIDATION, EventKind.ROUTING_VALIDATION_PASSED): TaskStatus.DISPATCHING,
    (TaskStatus.DISPATCHING, EventKind.DISPATCH_ACCEPTED): TaskStatus.EXECUTING,
    (TaskStatus.EXECUTING, EventKind.WORK_COMPLETED): TaskStatus.VERIFYING,
    (TaskStatus.VERIFYING, EventKind.VERIFICATION_PASSED): TaskStatus.REVIEWING,
    (TaskStatus.VERIFYING, EventKind.VERIFICATION_FAILED): TaskStatus.FIXING,
    (TaskStatus.REVIEWING, EventKind.REVIEW_PASSED): TaskStatus.RECORDING,
    (TaskStatus.REVIEWING, EventKind.REVIEW_REJECTED): TaskStatus.FIXING,
    (TaskStatus.REVIEWING, EventKind.APPROVAL_REQUIRED_RAISED): TaskStatus.APPROVAL_REQUIRED,
    (TaskStatus.FIXING, EventKind.FIX_DISPATCH_REQUESTED): TaskStatus.DISPATCHING,
    (TaskStatus.APPROVAL_REQUIRED, EventKind.APPROVAL_GRANTED): TaskStatus.RECORDING,
    (TaskStatus.APPROVAL_REQUIRED, EventKind.APPROVAL_DENIED): TaskStatus.FIXING,
    (TaskStatus.RECORDING, EventKind.RECORDING_COMPLETED): TaskStatus.DONE,
    (TaskStatus.EXECUTING, EventKind.TASK_BLOCKED): TaskStatus.BLOCKED,
    (TaskStatus.VERIFYING, EventKind.TASK_BLOCKED): TaskStatus.BLOCKED,
    (TaskStatus.REVIEWING, EventKind.TASK_BLOCKED): TaskStatus.BLOCKED,
    (TaskStatus.FIXING, EventKind.TASK_BLOCKED): TaskStatus.BLOCKED,
    (TaskStatus.BLOCKED, EventKind.BLOCKED_RECOVERY_TO_FIXING): TaskStatus.FIXING,
    **{
        (status, EventKind.TASK_FAILED): TaskStatus.FAILED
        for status in TaskStatus
        if status not in {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}
    },
    **{
        (status, EventKind.TASK_CANCELLED): TaskStatus.CANCELLED
        for status in TaskStatus
        if status not in {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}
    },
}


class ResultVariant(str, Enum):
    ACCEPTED = "accepted"
    NO_OP = "no_op"
    REJECTED = "rejected"


class KernelErrorCode(str, Enum):
    UNKNOWN_ENUM_VALUE = "VALP-E-UNKNOWN-ENUM-VALUE"
    STATE_CONFLICT = "VALP-E-STATE-CONFLICT"
    IDEMPOTENCY_CONFLICT = "VALP-E-IDEMPOTENCY-CONFLICT"


UNKNOWN_ENUM_ERROR = KernelErrorCode.UNKNOWN_ENUM_VALUE.value
STATE_CONFLICT_ERROR = KernelErrorCode.STATE_CONFLICT.value
IDEMPOTENCY_CONFLICT_ERROR = KernelErrorCode.IDEMPOTENCY_CONFLICT.value


@dataclass(frozen=True)
class Identity:
    kind: IdentityKind
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, IdentityKind):
            raise ValueError("identity kind must be closed")
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("identity value must be a non-empty string")

    def canonical(self) -> Mapping[str, str]:
        return {"kind": self.kind.value, "value": self.value}


@dataclass(frozen=True)
class Evidence:
    evidence_id: Identity
    content_digest: str

    def canonical(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-evidence.v1",
            "evidence_id": self.evidence_id.canonical(),
            "content_digest": self.content_digest,
        }


@dataclass(frozen=True)
class Event:
    event_id: Identity
    installation_id: Identity
    leader_epoch: int
    task_id: Identity
    kind: Union[EventKind, str]
    expected_revision: int

    def canonical(self) -> Mapping[str, Any]:
        kind = self.kind.value if isinstance(self.kind, EventKind) else self.kind
        return {
            "schema_version": "valp-kernel-event.v1",
            "event_id": self.event_id.canonical(),
            "installation_id": self.installation_id.canonical(),
            "leader_epoch": self.leader_epoch,
            "task_id": self.task_id.canonical(),
            "kind": kind,
            "expected_revision": self.expected_revision,
        }


@dataclass(frozen=True)
class AcceptedEvent:
    event_id: Identity
    result_id: Identity
    input_digest: str
    result_digest: str

    def canonical(self) -> Mapping[str, Any]:
        return {
            "event_id": self.event_id.canonical(),
            "result_id": self.result_id.canonical(),
            "input_digest": self.input_digest,
            "result_digest": self.result_digest,
        }


@dataclass(frozen=True)
class State:
    protocol_version: str
    installation_id: Identity
    leader_epoch: int
    task_id: Identity
    revision: int
    status: Union[TaskStatus, str]
    accepted_events: Tuple[AcceptedEvent, ...] = ()

    def canonical(self) -> Mapping[str, Any]:
        status = self.status.value if isinstance(self.status, TaskStatus) else self.status
        return {
            "schema_version": "valp-kernel-state.v1",
            "protocol_version": self.protocol_version,
            "installation_id": self.installation_id.canonical(),
            "leader_epoch": self.leader_epoch,
            "task_id": self.task_id.canonical(),
            "revision": self.revision,
            "status": status,
            "accepted_events": [item.canonical() for item in self.accepted_events],
        }


@dataclass(frozen=True)
class Accepted:
    result_id: Identity
    state: State
    input_digest: str
    result_digest: str
    obligations: Tuple[str, ...] = ()
    audit_facts: Tuple[str, ...] = ()

    def canonical(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-result.v1",
            "variant": ResultVariant.ACCEPTED.value,
            "result_id": self.result_id.canonical(),
            "state": self.state.canonical(),
            "input_digest": self.input_digest,
            "result_digest": self.result_digest,
            "obligations": list(self.obligations),
            "audit_facts": list(self.audit_facts),
        }


@dataclass(frozen=True)
class NoOp:
    state: State
    input_digest: str
    prior_result_id: Identity
    prior_result_digest: str

    def canonical(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-result.v1",
            "variant": ResultVariant.NO_OP.value,
            "state": self.state.canonical(),
            "input_digest": self.input_digest,
            "prior_result_id": self.prior_result_id.canonical(),
            "prior_result_digest": self.prior_result_digest,
        }


@dataclass(frozen=True)
class Rejected:
    state: State
    input_digest: str
    error_code: str
    prior_result_id: Optional[Identity] = None
    prior_result_digest: Optional[str] = None

    def canonical(self) -> Mapping[str, Any]:
        value = {
            "schema_version": "valp-kernel-result.v1",
            "variant": ResultVariant.REJECTED.value,
            "state": self.state.canonical(),
            "input_digest": self.input_digest,
            "error_code": self.error_code,
        }
        if self.prior_result_id is not None:
            value["prior_result_id"] = self.prior_result_id.canonical()
        if self.prior_result_digest is not None:
            value["prior_result_digest"] = self.prior_result_digest
        return value


@dataclass(frozen=True)
class Result:
    accepted: Optional[Accepted] = None
    no_op: Optional[NoOp] = None
    rejected: Optional[Rejected] = None

    def __post_init__(self) -> None:
        if sum(item is not None for item in (self.accepted, self.no_op, self.rejected)) != 1:
            raise ValueError("Result must contain exactly one variant")

    @property
    def variant(self) -> ResultVariant:
        if self.accepted is not None:
            return ResultVariant.ACCEPTED
        if self.no_op is not None:
            return ResultVariant.NO_OP
        return ResultVariant.REJECTED

    def canonical(self) -> Mapping[str, Any]:
        if self.accepted is not None:
            return self.accepted.canonical()
        if self.no_op is not None:
            return self.no_op.canonical()
        return self.rejected.canonical()


@dataclass(frozen=True)
class Replay:
    state: State
    applied_result_digests: Tuple[str, ...]
    obligations: Tuple[str, ...] = ()

    def canonical(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-replay.v1",
            "state": self.state.canonical(),
            "applied_result_digests": list(self.applied_result_digests),
            "obligations": list(self.obligations),
        }


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


EMPTY_REPLAY_PREFIX_DIGEST = _digest(
    {
        "schema_version": "valp-kernel-replay-prefix.v1",
        "entries": [],
    }
)


@dataclass(frozen=True)
class GenesisRoot:
    state: State

    def canonical(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-genesis-root.v1",
            "state": self.state.canonical(),
            "prefix_digest": EMPTY_REPLAY_PREFIX_DIGEST,
        }


@dataclass(frozen=True)
class CheckpointRoot:
    """Phase 1 structural checkpoint contract; it is not replay-authorizing."""

    state: State
    accepted_entry_count: int
    prefix_digest: str
    tail_event_id: Identity
    tail_result_id: Identity
    tail_result_digest: str
    checkpoint_result_id: Identity
    trust_policy_digest: str

    def __post_init__(self) -> None:
        if not _checkpoint_root_is_valid(self):
            raise ValueError("CheckpointRoot bindings are invalid")

    @property
    def state_digest(self) -> str:
        return _digest(self.state.canonical())

    def canonical(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-checkpoint-root.v1",
            "state": self.state.canonical(),
            "state_digest": self.state_digest,
            "accepted_entry_count": self.accepted_entry_count,
            "prefix_digest": self.prefix_digest,
            "tail_event_id": self.tail_event_id.canonical(),
            "tail_result_id": self.tail_result_id.canonical(),
            "tail_result_digest": self.tail_result_digest,
            "checkpoint_result_id": self.checkpoint_result_id.canonical(),
            "trust_policy_digest": self.trust_policy_digest,
        }


@dataclass(frozen=True)
class ReplayEntry:
    event: Event
    evidence_set: Tuple[Evidence, ...]
    result: Result

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_set", tuple(self.evidence_set))

    def canonical(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-replay-entry.v1",
            "event": self.event.canonical(),
            "evidence_set": list(_canonical_evidence(self.evidence_set)),
            "result": self.result.canonical(),
        }


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _has_identity_kind(value: Any, kind: IdentityKind) -> bool:
    return isinstance(value, Identity) and value.kind == kind


def _accepted_event_is_valid(value: Any) -> bool:
    return (
        isinstance(value, AcceptedEvent)
        and _has_identity_kind(value.event_id, IdentityKind.EVENT)
        and _has_identity_kind(value.result_id, IdentityKind.RESULT)
        and _is_digest(value.input_digest)
        and _is_digest(value.result_digest)
    )


def _checkpoint_root_is_valid(value: Any) -> bool:
    if not isinstance(value, CheckpointRoot) or not _state_is_valid(value.state):
        return False
    if (
        value.state.revision < 1
        or value.accepted_entry_count != value.state.revision
        or value.accepted_entry_count != len(value.state.accepted_events)
        or not _is_digest(value.prefix_digest)
        or not _is_digest(value.tail_result_digest)
        or not _is_digest(value.trust_policy_digest)
        or not _has_identity_kind(value.tail_event_id, IdentityKind.EVENT)
        or not _has_identity_kind(value.tail_result_id, IdentityKind.RESULT)
        or not _has_identity_kind(value.checkpoint_result_id, IdentityKind.RESULT)
    ):
        return False
    tail = value.state.accepted_events[-1]
    return (
        value.tail_event_id == tail.event_id
        and value.tail_result_id == tail.result_id
        and value.tail_result_digest == tail.result_digest
    )


def _state_is_valid(value: Any) -> bool:
    if not isinstance(value, State):
        return False
    if (
        value.protocol_version != PROTOCOL_VERSION
        or not _has_identity_kind(value.installation_id, IdentityKind.INSTALLATION)
        or not _has_identity_kind(value.task_id, IdentityKind.TASK)
        or not _is_non_negative_int(value.leader_epoch)
        or not _is_non_negative_int(value.revision)
        or not isinstance(value.status, TaskStatus)
        or value.revision != len(value.accepted_events)
        or (value.revision == 0 and value.status != TaskStatus.PUBLISHED)
        or not all(_accepted_event_is_valid(item) for item in value.accepted_events)
    ):
        return False
    event_ids = [item.event_id for item in value.accepted_events]
    result_ids = [item.result_id for item in value.accepted_events]
    return len(event_ids) == len(set(event_ids)) and len(result_ids) == len(set(result_ids))


def _event_is_valid(value: Any) -> bool:
    return (
        isinstance(value, Event)
        and _has_identity_kind(value.event_id, IdentityKind.EVENT)
        and _has_identity_kind(value.installation_id, IdentityKind.INSTALLATION)
        and _has_identity_kind(value.task_id, IdentityKind.TASK)
        and _is_non_negative_int(value.leader_epoch)
        and _is_non_negative_int(value.expected_revision)
        and isinstance(value.kind, EventKind)
    )


def _evidence_set_is_valid(evidence_set: Sequence[Evidence]) -> bool:
    if not all(
        isinstance(item, Evidence)
        and _has_identity_kind(item.evidence_id, IdentityKind.EVIDENCE)
        and _is_digest(item.content_digest)
        for item in evidence_set
    ):
        return False
    evidence_ids = [item.evidence_id for item in evidence_set]
    return len(evidence_ids) == len(set(evidence_ids))


def _canonical_evidence(evidence_set: Sequence[Evidence]) -> Tuple[Mapping[str, Any], ...]:
    values = [item.canonical() for item in evidence_set]
    return tuple(sorted(values, key=_canonical_json))


def _input_digest(state: State, event: Event, evidence_set: Sequence[Evidence]) -> str:
    return _digest(
        {
            "protocol_version": state.protocol_version,
            "event": event.canonical(),
            "evidence_set": _canonical_evidence(evidence_set),
        }
    )


def _accepted_result_digest(
    result_id: Identity,
    event_id: Identity,
    prior_state: State,
    next_state: State,
    input_digest: str,
    obligations: Sequence[str],
    audit_facts: Sequence[str],
) -> str:
    return _digest(
        {
            "variant": ResultVariant.ACCEPTED.value,
            "result_id": result_id.canonical(),
            "event_id": event_id.canonical(),
            "input_digest": input_digest,
            "prior_state_digest": _digest(prior_state.canonical()),
            "state": {
                "protocol_version": next_state.protocol_version,
                "installation_id": next_state.installation_id.canonical(),
                "leader_epoch": next_state.leader_epoch,
                "task_id": next_state.task_id.canonical(),
                "revision": next_state.revision,
                "status": next_state.status.value,
            },
            "obligations": tuple(obligations),
            "audit_facts": tuple(audit_facts),
        }
    )


def _reject(
    state: State,
    input_digest: str,
    error_code: Union[KernelErrorCode, str],
    prior_result_id: Optional[Identity] = None,
    prior_result_digest: Optional[str] = None,
) -> Result:
    return Result(
        rejected=Rejected(
            state=state,
            input_digest=input_digest,
            error_code=(
                error_code.value
                if isinstance(error_code, KernelErrorCode)
                else error_code
            ),
            prior_result_id=prior_result_id,
            prior_result_digest=prior_result_digest,
        )
    )


def reduce(state: State, event: Event, evidence_set: Sequence[Evidence]) -> Result:
    """Apply the first bounded Kernel transition without performing effects."""

    evidence_set = tuple(evidence_set)
    input_digest = _input_digest(state, event, evidence_set)
    if not isinstance(state.status, TaskStatus) or not isinstance(event.kind, EventKind):
        return _reject(state, input_digest, UNKNOWN_ENUM_ERROR)
    if (
        not _state_is_valid(state)
        or not _event_is_valid(event)
        or not _evidence_set_is_valid(evidence_set)
        or event.installation_id != state.installation_id
        or event.leader_epoch != state.leader_epoch
    ):
        return _reject(state, input_digest, STATE_CONFLICT_ERROR)

    prior = next(
        (item for item in state.accepted_events if item.event_id == event.event_id),
        None,
    )
    if prior is not None:
        if prior.input_digest == input_digest:
            return Result(
                no_op=NoOp(
                    state=state,
                    input_digest=input_digest,
                    prior_result_id=prior.result_id,
                    prior_result_digest=prior.result_digest,
                )
            )
        return _reject(
            state,
            input_digest,
            IDEMPOTENCY_CONFLICT_ERROR,
            prior_result_id=prior.result_id,
            prior_result_digest=prior.result_digest,
        )

    target_status = KERNEL_TASK_TRANSITIONS.get((state.status, event.kind))
    if (
        target_status is None
        or event.task_id != state.task_id
        or event.expected_revision != state.revision
    ):
        return _reject(state, input_digest, STATE_CONFLICT_ERROR)

    result_id = Identity(IdentityKind.RESULT, f"{event.event_id.value}:accepted")
    audit_facts = (f"accepted:{event.event_id.kind.value}:{event.event_id.value}",)
    result_state = State(
        protocol_version=state.protocol_version,
        installation_id=state.installation_id,
        leader_epoch=state.leader_epoch,
        task_id=state.task_id,
        revision=state.revision + 1,
        status=target_status,
    )
    result_digest = _accepted_result_digest(
        result_id,
        event.event_id,
        state,
        result_state,
        input_digest,
        (),
        audit_facts,
    )
    next_state = State(
        protocol_version=state.protocol_version,
        installation_id=state.installation_id,
        leader_epoch=state.leader_epoch,
        task_id=state.task_id,
        revision=state.revision + 1,
        status=target_status,
        accepted_events=state.accepted_events
        + (
            AcceptedEvent(
                event_id=event.event_id,
                result_id=result_id,
                input_digest=input_digest,
                result_digest=result_digest,
            ),
        ),
    )
    return Result(
        accepted=Accepted(
            result_id=result_id,
            state=next_state,
            input_digest=input_digest,
            result_digest=result_digest,
            obligations=(),
            audit_facts=audit_facts,
        )
    )


def replay(root: GenesisRoot, entries: Iterable[ReplayEntry]) -> Replay:
    """Recompute canonical entries from a verified genesis without emitting effects."""

    if not isinstance(root, GenesisRoot):
        raise ValueError("replay requires a verified replay root")
    if (
        not _state_is_valid(root.state)
        or root.state.status != TaskStatus.PUBLISHED
        or root.state.revision != 0
        or root.state.accepted_events
    ):
        raise ValueError("GenesisRoot must be the canonical empty Task State")
    current = root.state
    applied = []
    for entry in entries:
        if not isinstance(entry, ReplayEntry):
            raise ValueError("replay accepts only canonical ReplayEntry records")
        if entry.result.accepted is None:
            raise ValueError("ReplayEntry must contain an accepted Result")
        recomputed = reduce(current, entry.event, entry.evidence_set)
        if recomputed.accepted is None:
            raise ValueError("ReplayEntry inputs do not produce an accepted Result")
        if _canonical_json(recomputed.canonical()) != _canonical_json(
            entry.result.canonical()
        ):
            raise ValueError("ReplayEntry Result does not match recomputed Result")
        current = recomputed.accepted.state
        applied.append(recomputed.accepted.result_digest)
    return Replay(
        state=current,
        applied_result_digests=tuple(applied),
        obligations=(),
    )
