"""Pure Protocol Kernel with authenticated CheckpointRoot suffix replay."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple, Union


PROTOCOL_VERSION = "0.3.0"


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
    SUSPENSION = "suspension"
    WAIT_POLICY = "wait_policy"
    WAKE = "wake"
    PRINCIPAL = "principal"
    INTERRUPT = "interrupt"
    REDIRECT = "redirect"


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


class SuspensionStatus(str, Enum):
    WAITING = "waiting"
    RESUMED = "resumed"


class WakeReason(str, Enum):
    DEPENDENCY_READY = "dependency_ready"


class ControlStatus(str, Enum):
    ACTIVE = "active"
    INTERRUPTED = "interrupted"


class ControlReason(str, Enum):
    USER_REQUESTED = "user_requested"
    RUNTIME_FAILED = "runtime_failed"
    POLICY_ENFORCED = "policy_enforced"
    SUPERSEDED_BY_REDIRECT = "superseded_by_redirect"


class CancellationScope(str, Enum):
    TASK = "task"
    WORK_ITEM = "work_item"
    ATTEMPT = "attempt"


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
    WORK_ITEM_ELIGIBLE = "work_item_eligible"
    ATTEMPT_CREATED = "attempt_created"
    ATTEMPT_COMPLETED = "attempt_completed"
    ATTEMPT_FENCED = "attempt_fenced"
    ATTEMPT_SUBMITTED = "attempt_submitted"
    ATTEMPT_RUNNING = "attempt_running"
    ATTEMPT_FAILED = "attempt_failed"
    ATTEMPT_CANCELLED = "attempt_cancelled"
    WORK_ITEM_PARTIAL = "work_item_partial"
    WORK_ITEM_DEGRADED = "work_item_degraded"
    WORK_ITEM_BLOCKED = "work_item_blocked"
    WORK_ITEM_FAILED = "work_item_failed"
    WORK_ITEM_CANCELLED = "work_item_cancelled"
    WORK_ITEM_SKIPPED = "work_item_skipped"
    SUSPENSION_STARTED = "suspension_started"
    WAKE_ACCEPTED = "wake_accepted"
    INTERRUPT_REQUESTED = "interrupt_requested"
    INTERRUPT_RESUMED = "interrupt_resumed"
    REDIRECT_AUTHORIZED = "redirect_authorized"


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
class Dependency:
    work_item_id: Identity
    requirement: WorkItemRequirement

    def canonical(self) -> Mapping[str, Any]:
        return {
            "work_item_id": self.work_item_id.canonical(),
            "requirement": self.requirement.value,
        }


@dataclass(frozen=True)
class Attempt:
    task_id: Identity
    work_item_id: Identity
    attempt_id: Identity
    dispatch_id: Identity
    dispatch_generation: int
    status: AttemptStatus = AttemptStatus.CREATED

    def canonical(self) -> Mapping[str, Any]:
        return {
            "task_id": self.task_id.canonical(),
            "work_item_id": self.work_item_id.canonical(),
            "attempt_id": self.attempt_id.canonical(),
            "dispatch_id": self.dispatch_id.canonical(),
            "dispatch_generation": self.dispatch_generation,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class WorkItem:
    task_id: Identity
    work_item_id: Identity
    requirement: WorkItemRequirement
    status: WorkItemStatus = WorkItemStatus.PENDING
    dependencies: Tuple[Dependency, ...] = ()
    current_attempt: Optional[Attempt] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependencies", tuple(self.dependencies))

    def canonical(self) -> Mapping[str, Any]:
        return {
            "task_id": self.task_id.canonical(),
            "work_item_id": self.work_item_id.canonical(),
            "requirement": self.requirement.value,
            "status": self.status.value,
            "dependencies": [item.canonical() for item in self.dependencies],
            "current_attempt": (
                self.current_attempt.canonical()
                if self.current_attempt is not None
                else None
            ),
        }


@dataclass(frozen=True)
class Suspension:
    task_id: Identity
    suspension_id: Identity
    suspension_epoch: int
    status: SuspensionStatus
    wait_policy_id: Identity
    wait_policy_digest: str
    required_work_item_ids: Tuple[Identity, ...]
    accepted_wake_id: Optional[Identity] = None
    wake_reason: Optional[WakeReason] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "required_work_item_ids", tuple(self.required_work_item_ids)
        )

    def canonical(self) -> Mapping[str, Any]:
        value = {
            "task_id": self.task_id.canonical(),
            "suspension_id": self.suspension_id.canonical(),
            "suspension_epoch": self.suspension_epoch,
            "status": self.status.value,
            "wait_policy_id": self.wait_policy_id.canonical(),
            "wait_policy_digest": self.wait_policy_digest,
            "required_work_item_ids": [
                item.canonical() for item in self.required_work_item_ids
            ],
        }
        if self.accepted_wake_id is not None:
            value["accepted_wake_id"] = self.accepted_wake_id.canonical()
        if self.wake_reason is not None:
            value["wake_reason"] = self.wake_reason.value
        return value


@dataclass(frozen=True)
class ControlState:
    intent_version: int
    status: ControlStatus
    active_interrupt_id: Optional[Identity] = None

    def canonical(self) -> Mapping[str, Any]:
        value = {
            "intent_version": self.intent_version,
            "status": self.status.value,
        }
        if self.active_interrupt_id is not None:
            value["active_interrupt_id"] = self.active_interrupt_id.canonical()
        return value


@dataclass(frozen=True)
class Event:
    event_id: Identity
    installation_id: Identity
    leader_epoch: int
    task_id: Identity
    kind: Union[EventKind, str]
    expected_revision: int
    work_item_id: Optional[Identity] = None
    attempt_id: Optional[Identity] = None
    dispatch_id: Optional[Identity] = None
    dispatch_generation: Optional[int] = None
    suspension_id: Optional[Identity] = None
    suspension_epoch: Optional[int] = None
    wait_policy_id: Optional[Identity] = None
    wait_policy_digest: Optional[str] = None
    required_work_item_ids: Tuple[Identity, ...] = ()
    wake_id: Optional[Identity] = None
    wake_reason: Optional[Union[WakeReason, str]] = None
    authority_principal_id: Optional[Identity] = None
    authority_evidence_id: Optional[Identity] = None
    control_reason: Optional[Union[ControlReason, str]] = None
    cancellation_scope: Optional[Union[CancellationScope, str]] = None
    interrupt_id: Optional[Identity] = None
    redirect_id: Optional[Identity] = None
    intent_version: Optional[int] = None
    next_intent_version: Optional[int] = None
    superseded_work_item_ids: Tuple[Identity, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "required_work_item_ids", tuple(self.required_work_item_ids)
        )
        object.__setattr__(
            self, "superseded_work_item_ids", tuple(self.superseded_work_item_ids)
        )

    def canonical(self) -> Mapping[str, Any]:
        kind = self.kind.value if isinstance(self.kind, EventKind) else self.kind
        value = {
            "schema_version": "valp-kernel-event.v1",
            "event_id": self.event_id.canonical(),
            "installation_id": self.installation_id.canonical(),
            "leader_epoch": self.leader_epoch,
            "task_id": self.task_id.canonical(),
            "kind": kind,
            "expected_revision": self.expected_revision,
        }
        for name, identity in (
            ("work_item_id", self.work_item_id),
            ("attempt_id", self.attempt_id),
            ("dispatch_id", self.dispatch_id),
            ("suspension_id", self.suspension_id),
            ("wait_policy_id", self.wait_policy_id),
            ("wake_id", self.wake_id),
            ("authority_principal_id", self.authority_principal_id),
            ("authority_evidence_id", self.authority_evidence_id),
            ("interrupt_id", self.interrupt_id),
            ("redirect_id", self.redirect_id),
        ):
            if identity is not None:
                value[name] = identity.canonical()
        if self.dispatch_generation is not None:
            value["dispatch_generation"] = self.dispatch_generation
        if self.suspension_epoch is not None:
            value["suspension_epoch"] = self.suspension_epoch
        if self.wait_policy_digest is not None:
            value["wait_policy_digest"] = self.wait_policy_digest
        if self.required_work_item_ids:
            value["required_work_item_ids"] = [
                item.canonical() for item in self.required_work_item_ids
            ]
        if self.wake_reason is not None:
            value["wake_reason"] = (
                self.wake_reason.value
                if isinstance(self.wake_reason, WakeReason)
                else self.wake_reason
            )
        if self.control_reason is not None:
            value["control_reason"] = (
                self.control_reason.value
                if isinstance(self.control_reason, ControlReason)
                else self.control_reason
            )
        if self.cancellation_scope is not None:
            value["cancellation_scope"] = (
                self.cancellation_scope.value
                if isinstance(self.cancellation_scope, CancellationScope)
                else self.cancellation_scope
            )
        if self.intent_version is not None:
            value["intent_version"] = self.intent_version
        if self.next_intent_version is not None:
            value["next_intent_version"] = self.next_intent_version
        if self.superseded_work_item_ids:
            value["superseded_work_item_ids"] = [
                item.canonical() for item in self.superseded_work_item_ids
            ]
        return value


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
    work_items: Tuple[WorkItem, ...] = ()
    suspension: Optional[Suspension] = None
    control: Optional[ControlState] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_items", tuple(self.work_items))

    def canonical(self) -> Mapping[str, Any]:
        status = self.status.value if isinstance(self.status, TaskStatus) else self.status
        value = {
            "schema_version": "valp-kernel-state.v1",
            "protocol_version": self.protocol_version,
            "installation_id": self.installation_id.canonical(),
            "leader_epoch": self.leader_epoch,
            "task_id": self.task_id.canonical(),
            "revision": self.revision,
            "status": status,
            "accepted_events": [item.canonical() for item in self.accepted_events],
        }
        if self.work_items:
            value["work_items"] = [item.canonical() for item in self.work_items]
        if self.suspension is not None:
            value["suspension"] = self.suspension.canonical()
        if self.control is not None:
            value["control"] = self.control.canonical()
        return value


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "applied_result_digests", tuple(self.applied_result_digests))
        object.__setattr__(self, "obligations", tuple(self.obligations))
        if self.obligations:
            raise ValueError("Replay obligations must be empty")

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
class CheckpointTrustPolicy:
    trusted_evidence_ids: Tuple[Identity, ...]

    def __post_init__(self) -> None:
        identities = tuple(self.trusted_evidence_ids)
        if (
            not identities
            or not all(_has_identity_kind(item, IdentityKind.EVIDENCE) for item in identities)
            or len(set(identities)) != len(identities)
        ):
            raise ValueError("CheckpointTrustPolicy requires unique Evidence identities")
        object.__setattr__(
            self,
            "trusted_evidence_ids",
            tuple(sorted(identities, key=lambda item: _canonical_json(item.canonical()))),
        )

    @property
    def digest(self) -> str:
        return _digest(self.canonical())

    def canonical(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-checkpoint-trust-policy.v1",
            "trusted_evidence_ids": [
                item.canonical() for item in self.trusted_evidence_ids
            ],
        }


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
    """Structural bindings that require separate authentication for replay."""

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


def replay_prefix_digest(entries: Iterable[ReplayEntry]) -> str:
    canonical_entries = []
    for entry in entries:
        if not isinstance(entry, ReplayEntry) or entry.result.accepted is None:
            raise ValueError("replay prefix requires accepted ReplayEntry records")
        canonical_entries.append(entry.canonical())
    return _digest(
        {
            "schema_version": "valp-kernel-replay-prefix.v1",
            "entries": canonical_entries,
        }
    )


@dataclass(frozen=True)
class CheckpointAuthentication:
    """Bind a root and accepted Result to an identity-based trust policy.

    This verifies canonical digest statements, not signatures, key authority, or
    freshness. Those trust decisions remain System or Adapter responsibilities.
    """

    checkpoint_result: Result
    evidence_set: Tuple[Evidence, ...]
    trust_policy: CheckpointTrustPolicy

    def __post_init__(self) -> None:
        evidence_set = tuple(self.evidence_set)
        if len({item.evidence_id for item in evidence_set}) != len(evidence_set):
            raise ValueError("CheckpointAuthentication requires unique Evidence identities")
        object.__setattr__(self, "evidence_set", evidence_set)

    @staticmethod
    def statement_digest_for(
        root: CheckpointRoot,
        checkpoint_result: Result,
        trust_policy: CheckpointTrustPolicy,
    ) -> str:
        if not isinstance(root, CheckpointRoot):
            raise ValueError("checkpoint statement requires a CheckpointRoot")
        if not isinstance(checkpoint_result, Result):
            raise ValueError("checkpoint statement requires a Result")
        if not isinstance(trust_policy, CheckpointTrustPolicy):
            raise ValueError("checkpoint statement requires a trust policy")
        return _digest(
            {
                "schema_version": "valp-kernel-checkpoint-statement.v1",
                "checkpoint_root": root.canonical(),
                "checkpoint_result": checkpoint_result.canonical(),
                "trust_policy_digest": trust_policy.digest,
            }
        )

    def canonical(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-checkpoint-authentication.v1",
            "checkpoint_result": self.checkpoint_result.canonical(),
            "evidence_set": list(_canonical_evidence(self.evidence_set)),
            "trust_policy": self.trust_policy.canonical(),
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


def _attempt_is_valid(value: Any) -> bool:
    return (
        isinstance(value, Attempt)
        and _has_identity_kind(value.task_id, IdentityKind.TASK)
        and _has_identity_kind(value.work_item_id, IdentityKind.WORK_ITEM)
        and _has_identity_kind(value.attempt_id, IdentityKind.ATTEMPT)
        and _has_identity_kind(value.dispatch_id, IdentityKind.DISPATCH)
        and _is_non_negative_int(value.dispatch_generation)
        and isinstance(value.status, AttemptStatus)
    )


def _work_item_is_valid(value: Any, task_id: Identity) -> bool:
    if not (
        isinstance(value, WorkItem)
        and value.task_id == task_id
        and _has_identity_kind(value.work_item_id, IdentityKind.WORK_ITEM)
        and isinstance(value.requirement, WorkItemRequirement)
        and isinstance(value.status, WorkItemStatus)
        and all(
            isinstance(dep, Dependency)
            and _has_identity_kind(dep.work_item_id, IdentityKind.WORK_ITEM)
            and isinstance(dep.requirement, WorkItemRequirement)
            and dep.work_item_id != value.work_item_id
            for dep in value.dependencies
        )
        and len({dep.work_item_id for dep in value.dependencies}) == len(value.dependencies)
    ):
        return False
    attempt = value.current_attempt
    return attempt is None or (
        _attempt_is_valid(attempt)
        and attempt.task_id == value.task_id
        and attempt.work_item_id == value.work_item_id
    )


def _suspension_is_valid(value: Any, state: State) -> bool:
    if (
        not isinstance(value, Suspension)
        or value.task_id != state.task_id
        or not _has_identity_kind(value.suspension_id, IdentityKind.SUSPENSION)
        or not _is_non_negative_int(value.suspension_epoch)
        or not isinstance(value.status, SuspensionStatus)
        or not _has_identity_kind(value.wait_policy_id, IdentityKind.WAIT_POLICY)
        or not _is_digest(value.wait_policy_digest)
        or not value.required_work_item_ids
        or len(value.required_work_item_ids) != len(set(value.required_work_item_ids))
        or not all(
            _has_identity_kind(item, IdentityKind.WORK_ITEM)
            for item in value.required_work_item_ids
        )
    ):
        return False
    work_item_ids = {item.work_item_id for item in state.work_items}
    if not set(value.required_work_item_ids).issubset(work_item_ids):
        return False
    if value.status == SuspensionStatus.WAITING:
        return value.accepted_wake_id is None and value.wake_reason is None
    return (
        _has_identity_kind(value.accepted_wake_id, IdentityKind.WAKE)
        and value.wake_reason == WakeReason.DEPENDENCY_READY
    )


def _control_is_valid(value: Any) -> bool:
    if not (
        isinstance(value, ControlState)
        and _is_non_negative_int(value.intent_version)
        and isinstance(value.status, ControlStatus)
    ):
        return False
    if value.status == ControlStatus.ACTIVE:
        return value.active_interrupt_id is None
    return _has_identity_kind(value.active_interrupt_id, IdentityKind.INTERRUPT)


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


def _checkpoint_authentication_is_valid(
    root: CheckpointRoot,
    authentication: Any,
) -> bool:
    if (
        not _checkpoint_root_is_valid(root)
        or not isinstance(authentication, CheckpointAuthentication)
        or not isinstance(authentication.trust_policy, CheckpointTrustPolicy)
        or authentication.trust_policy.digest != root.trust_policy_digest
        or not isinstance(authentication.checkpoint_result, Result)
        or authentication.checkpoint_result.accepted is None
        or not _evidence_set_is_valid(authentication.evidence_set)
    ):
        return False
    accepted = authentication.checkpoint_result.accepted
    if (
        accepted.state != root.state
        or accepted.result_id != root.checkpoint_result_id
        or accepted.result_id != root.tail_result_id
        or accepted.result_digest != root.tail_result_digest
    ):
        return False
    expected_ids = set(authentication.trust_policy.trusted_evidence_ids)
    actual_ids = {item.evidence_id for item in authentication.evidence_set}
    statement_digest = CheckpointAuthentication.statement_digest_for(
        root,
        authentication.checkpoint_result,
        authentication.trust_policy,
    )
    return (
        actual_ids == expected_ids
        and all(
            item.content_digest == statement_digest
            for item in authentication.evidence_set
        )
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
        or not all(_work_item_is_valid(item, value.task_id) for item in value.work_items)
        or (value.suspension is not None and value.status != TaskStatus.EXECUTING)
        or (value.suspension is not None and not _suspension_is_valid(value.suspension, value))
        or (value.control is not None and not _control_is_valid(value.control))
    ):
        return False
    event_ids = [item.event_id for item in value.accepted_events]
    result_ids = [item.result_id for item in value.accepted_events]
    work_item_ids = [item.work_item_id for item in value.work_items]
    return (
        len(event_ids) == len(set(event_ids))
        and len(result_ids) == len(set(result_ids))
        and len(work_item_ids) == len(set(work_item_ids))
    )


def _event_is_valid(value: Any) -> bool:
    return (
        isinstance(value, Event)
        and _has_identity_kind(value.event_id, IdentityKind.EVENT)
        and _has_identity_kind(value.installation_id, IdentityKind.INSTALLATION)
        and _has_identity_kind(value.task_id, IdentityKind.TASK)
        and _is_non_negative_int(value.leader_epoch)
        and _is_non_negative_int(value.expected_revision)
        and isinstance(value.kind, EventKind)
        and (value.work_item_id is None or _has_identity_kind(value.work_item_id, IdentityKind.WORK_ITEM))
        and (value.attempt_id is None or _has_identity_kind(value.attempt_id, IdentityKind.ATTEMPT))
        and (value.dispatch_id is None or _has_identity_kind(value.dispatch_id, IdentityKind.DISPATCH))
        and (value.dispatch_generation is None or _is_non_negative_int(value.dispatch_generation))
        and (value.suspension_id is None or _has_identity_kind(value.suspension_id, IdentityKind.SUSPENSION))
        and (value.suspension_epoch is None or _is_non_negative_int(value.suspension_epoch))
        and (value.wait_policy_id is None or _has_identity_kind(value.wait_policy_id, IdentityKind.WAIT_POLICY))
        and (value.wait_policy_digest is None or _is_digest(value.wait_policy_digest))
        and all(_has_identity_kind(item, IdentityKind.WORK_ITEM) for item in value.required_work_item_ids)
        and (value.wake_id is None or _has_identity_kind(value.wake_id, IdentityKind.WAKE))
        and (value.wake_reason is None or isinstance(value.wake_reason, WakeReason))
        and (value.authority_principal_id is None or _has_identity_kind(value.authority_principal_id, IdentityKind.PRINCIPAL))
        and (value.authority_evidence_id is None or _has_identity_kind(value.authority_evidence_id, IdentityKind.EVIDENCE))
        and (value.control_reason is None or isinstance(value.control_reason, ControlReason))
        and (value.cancellation_scope is None or isinstance(value.cancellation_scope, CancellationScope))
        and (value.interrupt_id is None or _has_identity_kind(value.interrupt_id, IdentityKind.INTERRUPT))
        and (value.redirect_id is None or _has_identity_kind(value.redirect_id, IdentityKind.REDIRECT))
        and (value.intent_version is None or _is_non_negative_int(value.intent_version))
        and (value.next_intent_version is None or _is_non_negative_int(value.next_intent_version))
        and all(_has_identity_kind(item, IdentityKind.WORK_ITEM) for item in value.superseded_work_item_ids)
        and len(value.superseded_work_item_ids) == len(set(value.superseded_work_item_ids))
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


_AUTHORITY_BOUND_EVENTS = frozenset({
    EventKind.TASK_CANCELLED,
    EventKind.WORK_ITEM_CANCELLED,
    EventKind.ATTEMPT_CANCELLED,
    EventKind.INTERRUPT_REQUESTED,
    EventKind.INTERRUPT_RESUMED,
    EventKind.REDIRECT_AUTHORIZED,
})


def _authority_is_valid(event: Event, evidence_set: Sequence[Evidence]) -> bool:
    if event.kind not in _AUTHORITY_BOUND_EVENTS:
        return True
    return (
        _has_identity_kind(event.authority_principal_id, IdentityKind.PRINCIPAL)
        and _has_identity_kind(event.authority_evidence_id, IdentityKind.EVIDENCE)
        and isinstance(event.control_reason, ControlReason)
        and any(
            item.evidence_id == event.authority_evidence_id
            for item in evidence_set
        )
    )


def _adapter_cancel_obligation(attempt: Attempt) -> str:
    return "adapter_cancel:" + _canonical_json({
        "task_id": attempt.task_id.value,
        "work_item_id": attempt.work_item_id.value,
        "attempt_id": attempt.attempt_id.value,
        "dispatch_id": attempt.dispatch_id.value,
        "dispatch_generation": attempt.dispatch_generation,
    })


def _cancelled_work_item(item: WorkItem) -> Tuple[WorkItem, Tuple[str, ...]]:
    attempt = item.current_attempt
    obligations: Tuple[str, ...] = ()
    next_attempt = attempt
    if attempt is not None and attempt.status in {
        AttemptStatus.CREATED,
        AttemptStatus.SUBMITTED,
        AttemptStatus.RUNNING,
    }:
        if attempt.status in {AttemptStatus.SUBMITTED, AttemptStatus.RUNNING}:
            obligations = (_adapter_cancel_obligation(attempt),)
        next_attempt = Attempt(
            attempt.task_id,
            attempt.work_item_id,
            attempt.attempt_id,
            attempt.dispatch_id,
            attempt.dispatch_generation,
            AttemptStatus.CANCELLED,
        )
    return WorkItem(
        item.task_id,
        item.work_item_id,
        item.requirement,
        WorkItemStatus.CANCELLED,
        item.dependencies,
        next_attempt,
    ), obligations


def _input_digest(state: State, event: Event, evidence_set: Sequence[Evidence]) -> str:
    return _digest(
        {
            "protocol_version": state.protocol_version,
            "event": event.canonical(),
            "evidence_set": _canonical_evidence(evidence_set),
        }
    )


def _eligible_work_item(state: State, item: WorkItem) -> Tuple[bool, Tuple[str, ...]]:
    items = {candidate.work_item_id: candidate for candidate in state.work_items}
    audit_facts = []
    for dependency in item.dependencies:
        target = items.get(dependency.work_item_id)
        if target is None:
            return False, ()
        if dependency.requirement == WorkItemRequirement.REQUIRED:
            if target.status != WorkItemStatus.COMPLETED:
                return False, ()
        elif dependency.requirement == WorkItemRequirement.SOFT and target.status != WorkItemStatus.COMPLETED:
            audit_facts.append(
                f"soft_dependency_unmet:{item.work_item_id.value}:{target.work_item_id.value}"
            )
    return True, tuple(audit_facts)


_ATTEMPT_SCOPED_EVENTS = frozenset({
    EventKind.ATTEMPT_CREATED, EventKind.ATTEMPT_SUBMITTED,
    EventKind.ATTEMPT_RUNNING, EventKind.ATTEMPT_COMPLETED,
    EventKind.ATTEMPT_FAILED, EventKind.ATTEMPT_CANCELLED,
    EventKind.ATTEMPT_FENCED,
})


def _attempt_event_is_stale(state: State, event: Event) -> bool:
    if event.kind not in _ATTEMPT_SCOPED_EVENTS:
        return False
    item = next((item for item in state.work_items if item.work_item_id == event.work_item_id), None)
    if item is None:
        return True
    attempt = item.current_attempt
    if attempt is None:
        return event.kind != EventKind.ATTEMPT_CREATED
    event_tuple = (event.attempt_id, event.dispatch_id, event.dispatch_generation)
    current_tuple = (attempt.attempt_id, attempt.dispatch_id, attempt.dispatch_generation)
    if event.kind == EventKind.ATTEMPT_CREATED:
        if event_tuple == current_tuple:
            return False
        return not (
            item.status == WorkItemStatus.BLOCKED
            and event.attempt_id != attempt.attempt_id
            and event.dispatch_generation is not None
            and event.dispatch_generation > attempt.dispatch_generation
        )
    return event_tuple != current_tuple or attempt.status == AttemptStatus.FENCED


def _accepted_result_digest(
    result_id: Identity,
    event_id: Identity,
    prior_state: State,
    next_state: State,
    input_digest: str,
    obligations: Sequence[str],
    audit_facts: Sequence[str],
) -> str:
    next_state_value: Mapping[str, Any]
    if next_state.work_items or next_state.suspension is not None or next_state.control is not None:
        next_state_value = next_state.canonical()
    else:
        next_state_value = {
            "protocol_version": next_state.protocol_version,
            "installation_id": next_state.installation_id.canonical(),
            "leader_epoch": next_state.leader_epoch,
            "task_id": next_state.task_id.canonical(),
            "revision": next_state.revision,
            "status": next_state.status.value,
        }
    return _digest(
        {
            "variant": ResultVariant.ACCEPTED.value,
            "result_id": result_id.canonical(),
            "event_id": event_id.canonical(),
            "input_digest": input_digest,
            "prior_state_digest": _digest(prior_state.canonical()),
            "state": next_state_value,
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
    if (
        not isinstance(state.status, TaskStatus)
        or not isinstance(event.kind, EventKind)
        or (event.wake_reason is not None and not isinstance(event.wake_reason, WakeReason))
        or (event.control_reason is not None and not isinstance(event.control_reason, ControlReason))
        or (event.cancellation_scope is not None and not isinstance(event.cancellation_scope, CancellationScope))
    ):
        return _reject(state, input_digest, UNKNOWN_ENUM_ERROR)
    if (
        not _state_is_valid(state)
        or not _event_is_valid(event)
        or not _evidence_set_is_valid(evidence_set)
        or event.installation_id != state.installation_id
        or event.leader_epoch != state.leader_epoch
        or not _authority_is_valid(event, evidence_set)
    ):
        return _reject(state, input_digest, STATE_CONFLICT_ERROR)

    if _attempt_event_is_stale(state, event):
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

    if event.task_id != state.task_id or event.expected_revision != state.revision:
        return _reject(state, input_digest, STATE_CONFLICT_ERROR)

    work_items = state.work_items
    suspension = state.suspension
    control = state.control
    current_control = control or ControlState(0, ControlStatus.ACTIVE)
    obligations: Tuple[str, ...] = ()
    audit_facts: Tuple[str, ...]
    target_status = KERNEL_TASK_TRANSITIONS.get((state.status, event.kind))
    if (
        current_control.status == ControlStatus.INTERRUPTED
        and event.kind not in {
            EventKind.INTERRUPT_RESUMED,
            EventKind.REDIRECT_AUTHORIZED,
            EventKind.TASK_CANCELLED,
            EventKind.TASK_FAILED,
        }
    ):
        return _reject(state, input_digest, STATE_CONFLICT_ERROR)
    if event.kind == EventKind.INTERRUPT_REQUESTED:
        if (
            state.status in {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}
            or current_control.status != ControlStatus.ACTIVE
            or event.interrupt_id is None
            or event.intent_version != current_control.intent_version
            or event.redirect_id is not None
            or event.next_intent_version is not None
            or event.cancellation_scope is not None
            or event.superseded_work_item_ids
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        control = ControlState(
            current_control.intent_version,
            ControlStatus.INTERRUPTED,
            event.interrupt_id,
        )
        target_status = state.status
        audit_facts = (
            f"interrupted:{event.interrupt_id.value}:{event.authority_principal_id.value}",
        )
    elif event.kind == EventKind.INTERRUPT_RESUMED:
        if (
            current_control.status != ControlStatus.INTERRUPTED
            or event.interrupt_id != current_control.active_interrupt_id
            or event.intent_version != current_control.intent_version
            or event.redirect_id is not None
            or event.next_intent_version is not None
            or event.cancellation_scope is not None
            or event.superseded_work_item_ids
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        control = ControlState(current_control.intent_version, ControlStatus.ACTIVE)
        target_status = state.status
        audit_facts = (
            f"interrupt_resumed:{event.interrupt_id.value}:{event.authority_principal_id.value}",
        )
    elif event.kind == EventKind.REDIRECT_AUTHORIZED:
        item_by_id = {item.work_item_id: item for item in work_items}
        if (
            state.status in {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}
            or event.redirect_id is None
            or event.interrupt_id is not None
            or event.intent_version != current_control.intent_version
            or event.next_intent_version != current_control.intent_version + 1
            or event.cancellation_scope is not None
            or any(item not in item_by_id for item in event.superseded_work_item_ids)
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        invalidated = set(event.superseded_work_item_ids)
        next_items = []
        emitted = []
        terminal_work_items = {
            WorkItemStatus.COMPLETED,
            WorkItemStatus.PARTIAL,
            WorkItemStatus.DEGRADED,
            WorkItemStatus.FAILED,
            WorkItemStatus.CANCELLED,
            WorkItemStatus.SKIPPED,
        }
        for item in work_items:
            if item.work_item_id in invalidated and item.status not in terminal_work_items:
                cancelled, item_obligations = _cancelled_work_item(item)
                next_items.append(cancelled)
                emitted.extend(item_obligations)
            else:
                next_items.append(item)
        work_items = tuple(next_items)
        obligations = tuple(emitted)
        suspension = None
        control = ControlState(event.next_intent_version, ControlStatus.ACTIVE)
        target_status = TaskStatus.FIXING
        audit_facts = (
            f"redirected:{event.redirect_id.value}:{event.intent_version}:{event.next_intent_version}",
            f"redirect_authority:{event.authority_principal_id.value}",
        )
    elif event.kind == EventKind.TASK_CANCELLED:
        if (
            event.cancellation_scope != CancellationScope.TASK
            or event.work_item_id is not None
            or event.attempt_id is not None
            or event.dispatch_id is not None
            or event.dispatch_generation is not None
            or event.interrupt_id is not None
            or event.redirect_id is not None
            or event.intent_version is not None
            or event.next_intent_version is not None
            or event.superseded_work_item_ids
            or (
                (suspension is None and event.suspension_epoch is not None)
                or (
                    suspension is not None
                    and event.suspension_epoch != suspension.suspension_epoch
                )
            )
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        next_items = []
        emitted = []
        terminal_work_items = {
            WorkItemStatus.COMPLETED,
            WorkItemStatus.PARTIAL,
            WorkItemStatus.DEGRADED,
            WorkItemStatus.FAILED,
            WorkItemStatus.CANCELLED,
            WorkItemStatus.SKIPPED,
        }
        for item in work_items:
            if item.status not in terminal_work_items:
                cancelled, item_obligations = _cancelled_work_item(item)
                next_items.append(cancelled)
                emitted.extend(item_obligations)
            else:
                next_items.append(item)
        work_items = tuple(next_items)
        obligations = tuple(emitted)
        suspension = None
        control = ControlState(current_control.intent_version, ControlStatus.ACTIVE)
        audit_facts = (
            f"cancelled:task:{state.task_id.value}:{event.authority_principal_id.value}",
        )
    elif event.kind == EventKind.SUSPENSION_STARTED:
        prior_suspension = state.suspension
        if (
            state.status != TaskStatus.EXECUTING
            or event.suspension_id is None
            or event.suspension_epoch is None
            or event.wait_policy_id is None
            or event.wait_policy_digest is None
            or not event.required_work_item_ids
            or len(event.required_work_item_ids) != len(set(event.required_work_item_ids))
            or event.wake_id is not None
            or event.wake_reason is not None
            or (
                prior_suspension is None
                and event.suspension_epoch != 0
            )
            or (
                prior_suspension is not None
                and (
                    prior_suspension.status != SuspensionStatus.RESUMED
                    or event.suspension_epoch != prior_suspension.suspension_epoch + 1
                    or event.suspension_id == prior_suspension.suspension_id
                )
            )
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        item_ids = {item.work_item_id for item in work_items}
        if not set(event.required_work_item_ids).issubset(item_ids):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        suspension = Suspension(
            task_id=state.task_id,
            suspension_id=event.suspension_id,
            suspension_epoch=event.suspension_epoch,
            status=SuspensionStatus.WAITING,
            wait_policy_id=event.wait_policy_id,
            wait_policy_digest=event.wait_policy_digest,
            required_work_item_ids=event.required_work_item_ids,
        )
        target_status = state.status
        audit_facts = ()
    elif event.kind == EventKind.WAKE_ACCEPTED:
        current = state.suspension
        items = {item.work_item_id: item for item in work_items}
        if (
            state.status != TaskStatus.EXECUTING
            or current is None
            or current.status != SuspensionStatus.WAITING
            or event.suspension_id != current.suspension_id
            or event.suspension_epoch != current.suspension_epoch
            or event.wait_policy_id != current.wait_policy_id
            or event.wait_policy_digest != current.wait_policy_digest
            or event.required_work_item_ids != current.required_work_item_ids
            or event.wake_id is None
            or event.wake_reason != WakeReason.DEPENDENCY_READY
            or any(
                items.get(item) is None
                or items[item].status != WorkItemStatus.COMPLETED
                for item in current.required_work_item_ids
            )
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        suspension = Suspension(
            task_id=current.task_id,
            suspension_id=current.suspension_id,
            suspension_epoch=current.suspension_epoch,
            status=SuspensionStatus.RESUMED,
            wait_policy_id=current.wait_policy_id,
            wait_policy_digest=current.wait_policy_digest,
            required_work_item_ids=current.required_work_item_ids,
            accepted_wake_id=event.wake_id,
            wake_reason=event.wake_reason,
        )
        target_status = state.status
        audit_facts = ()
    elif event.kind in {EventKind.WORK_ITEM_ELIGIBLE, EventKind.ATTEMPT_CREATED}:
        item_index = next(
            (index for index, item in enumerate(work_items) if item.work_item_id == event.work_item_id),
            None,
        )
        if event.work_item_id is None or item_index is None:
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        item = work_items[item_index]
        if event.kind == EventKind.WORK_ITEM_ELIGIBLE:
            eligible, audit_facts = _eligible_work_item(state, item)
            if item.status != WorkItemStatus.PENDING or not eligible:
                return _reject(state, input_digest, STATE_CONFLICT_ERROR)
            next_item = WorkItem(
                task_id=item.task_id, work_item_id=item.work_item_id,
                requirement=item.requirement, status=WorkItemStatus.ELIGIBLE,
                dependencies=item.dependencies, current_attempt=item.current_attempt,
            )
        else:
            audit_facts = ()
            retry = item.status == WorkItemStatus.BLOCKED and item.current_attempt is not None
            if (
                item.status not in {WorkItemStatus.ELIGIBLE, WorkItemStatus.BLOCKED}
                or (item.current_attempt is not None and not retry)
                or event.attempt_id is None
                or event.dispatch_id is None
                or event.dispatch_generation is None
                or (retry and event.dispatch_generation <= item.current_attempt.dispatch_generation)
                or (retry and event.attempt_id == item.current_attempt.attempt_id)
            ):
                return _reject(state, input_digest, STATE_CONFLICT_ERROR)
            next_item = WorkItem(
                task_id=item.task_id, work_item_id=item.work_item_id,
                requirement=item.requirement, status=WorkItemStatus.SUBMITTED,
                dependencies=item.dependencies,
                current_attempt=Attempt(
                    task_id=state.task_id, work_item_id=item.work_item_id,
                    attempt_id=event.attempt_id, dispatch_id=event.dispatch_id,
                    dispatch_generation=event.dispatch_generation,
                ),
            )
        work_items = work_items[:item_index] + (
            next_item,
        ) + work_items[item_index + 1:]
        target_status = state.status
    elif event.kind == EventKind.ATTEMPT_FENCED:
        item_index = next(
            (index for index, item in enumerate(work_items) if item.work_item_id == event.work_item_id),
            None,
        )
        if event.work_item_id is None or item_index is None:
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        item = work_items[item_index]
        attempt = item.current_attempt
        if (
            attempt is None
            or attempt.status not in {
                AttemptStatus.CREATED,
                AttemptStatus.SUBMITTED,
                AttemptStatus.RUNNING,
            }
            or (attempt.attempt_id, attempt.dispatch_id, attempt.dispatch_generation)
            != (event.attempt_id, event.dispatch_id, event.dispatch_generation)
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        work_items = work_items[:item_index] + (
            WorkItem(
                task_id=item.task_id, work_item_id=item.work_item_id,
                requirement=item.requirement, status=item.status,
                dependencies=item.dependencies,
                current_attempt=Attempt(
                    task_id=attempt.task_id, work_item_id=attempt.work_item_id,
                    attempt_id=attempt.attempt_id, dispatch_id=attempt.dispatch_id,
                    dispatch_generation=attempt.dispatch_generation,
                    status=AttemptStatus.FENCED,
                ),
            ),
        ) + work_items[item_index + 1:]
        target_status = state.status
        audit_facts = ()
    elif event.kind in {
        EventKind.ATTEMPT_SUBMITTED, EventKind.ATTEMPT_RUNNING,
        EventKind.ATTEMPT_COMPLETED, EventKind.ATTEMPT_FAILED,
        EventKind.ATTEMPT_CANCELLED,
    }:
        item_index = next((index for index, item in enumerate(work_items) if item.work_item_id == event.work_item_id), None)
        if event.work_item_id is None or item_index is None:
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        item = work_items[item_index]
        attempt = item.current_attempt
        if event.kind == EventKind.ATTEMPT_CANCELLED and (
            event.cancellation_scope != CancellationScope.ATTEMPT
            or event.interrupt_id is not None
            or event.redirect_id is not None
            or event.intent_version is not None
            or event.next_intent_version is not None
            or event.superseded_work_item_ids
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        if (
            attempt is None
            or attempt.status == AttemptStatus.FENCED
            or (attempt.attempt_id, attempt.dispatch_id, attempt.dispatch_generation)
            != (event.attempt_id, event.dispatch_id, event.dispatch_generation)
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        transitions = {
            EventKind.ATTEMPT_SUBMITTED: (WorkItemStatus.SUBMITTED, AttemptStatus.CREATED, WorkItemStatus.RUNNING, AttemptStatus.SUBMITTED),
            EventKind.ATTEMPT_RUNNING: (WorkItemStatus.RUNNING, AttemptStatus.SUBMITTED, WorkItemStatus.RUNNING, AttemptStatus.RUNNING),
            EventKind.ATTEMPT_COMPLETED: (WorkItemStatus.RUNNING, AttemptStatus.RUNNING, WorkItemStatus.COMPLETED, AttemptStatus.COMPLETED),
            EventKind.ATTEMPT_FAILED: (None, None, WorkItemStatus.FAILED, AttemptStatus.FAILED),
            EventKind.ATTEMPT_CANCELLED: (None, None, WorkItemStatus.CANCELLED, AttemptStatus.CANCELLED),
        }
        required_item_status, required_attempt_status, next_item_status, next_attempt_status = transitions[event.kind]
        if (
            (required_item_status is not None and item.status != required_item_status)
            or (required_attempt_status is not None and attempt.status != required_attempt_status)
            or (event.kind in {EventKind.ATTEMPT_FAILED, EventKind.ATTEMPT_CANCELLED}
                and (
                    item.status not in {WorkItemStatus.SUBMITTED, WorkItemStatus.RUNNING}
                    or attempt.status not in {
                        AttemptStatus.CREATED,
                        AttemptStatus.SUBMITTED,
                        AttemptStatus.RUNNING,
                    }
                ))
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        work_items = work_items[:item_index] + (WorkItem(
            task_id=item.task_id, work_item_id=item.work_item_id,
            requirement=item.requirement, status=next_item_status,
            dependencies=item.dependencies, current_attempt=Attempt(
                task_id=attempt.task_id, work_item_id=attempt.work_item_id,
                attempt_id=attempt.attempt_id, dispatch_id=attempt.dispatch_id,
                dispatch_generation=attempt.dispatch_generation, status=next_attempt_status,
            ),
        ),) + work_items[item_index + 1:]
        if (
            event.kind == EventKind.ATTEMPT_CANCELLED
            and attempt.status in {AttemptStatus.SUBMITTED, AttemptStatus.RUNNING}
        ):
            obligations = (_adapter_cancel_obligation(attempt),)
        target_status = state.status
        audit_facts = (
            (
                f"cancelled:attempt:{attempt.attempt_id.value}:"
                f"{event.authority_principal_id.value}"
            ),
        ) if event.kind == EventKind.ATTEMPT_CANCELLED else ()
    elif event.kind in {
        EventKind.WORK_ITEM_PARTIAL, EventKind.WORK_ITEM_DEGRADED,
        EventKind.WORK_ITEM_BLOCKED, EventKind.WORK_ITEM_FAILED,
        EventKind.WORK_ITEM_CANCELLED, EventKind.WORK_ITEM_SKIPPED,
    }:
        item_index = next((index for index, item in enumerate(work_items) if item.work_item_id == event.work_item_id), None)
        if event.work_item_id is None or item_index is None:
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        item = work_items[item_index]
        if event.kind == EventKind.WORK_ITEM_CANCELLED and (
            event.cancellation_scope != CancellationScope.WORK_ITEM
            or event.attempt_id is not None
            or event.dispatch_id is not None
            or event.dispatch_generation is not None
            or event.interrupt_id is not None
            or event.redirect_id is not None
            or event.intent_version is not None
            or event.next_intent_version is not None
            or event.superseded_work_item_ids
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        allowed = {
            EventKind.WORK_ITEM_PARTIAL: {WorkItemStatus.RUNNING},
            EventKind.WORK_ITEM_DEGRADED: {WorkItemStatus.RUNNING},
            EventKind.WORK_ITEM_BLOCKED: {WorkItemStatus.PENDING, WorkItemStatus.ELIGIBLE, WorkItemStatus.SUBMITTED, WorkItemStatus.RUNNING},
            EventKind.WORK_ITEM_FAILED: {WorkItemStatus.PENDING, WorkItemStatus.ELIGIBLE, WorkItemStatus.SUBMITTED, WorkItemStatus.RUNNING, WorkItemStatus.BLOCKED},
            EventKind.WORK_ITEM_CANCELLED: {WorkItemStatus.PENDING, WorkItemStatus.ELIGIBLE, WorkItemStatus.SUBMITTED, WorkItemStatus.RUNNING, WorkItemStatus.BLOCKED},
            EventKind.WORK_ITEM_SKIPPED: {WorkItemStatus.PENDING, WorkItemStatus.ELIGIBLE},
        }
        targets = {
            EventKind.WORK_ITEM_PARTIAL: WorkItemStatus.PARTIAL,
            EventKind.WORK_ITEM_DEGRADED: WorkItemStatus.DEGRADED,
            EventKind.WORK_ITEM_BLOCKED: WorkItemStatus.BLOCKED,
            EventKind.WORK_ITEM_FAILED: WorkItemStatus.FAILED,
            EventKind.WORK_ITEM_CANCELLED: WorkItemStatus.CANCELLED,
            EventKind.WORK_ITEM_SKIPPED: WorkItemStatus.SKIPPED,
        }
        if item.status not in allowed[event.kind] or (
            event.kind == EventKind.WORK_ITEM_SKIPPED
            and (
                item.requirement == WorkItemRequirement.REQUIRED
                or any(
                    dependency.work_item_id == item.work_item_id
                    and dependency.requirement == WorkItemRequirement.REQUIRED
                    for candidate in work_items
                    for dependency in candidate.dependencies
                )
            )
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        if event.kind == EventKind.WORK_ITEM_CANCELLED:
            next_item, obligations = _cancelled_work_item(item)
            audit_facts = (
                f"cancelled:work_item:{item.work_item_id.value}:{event.authority_principal_id.value}",
            )
        else:
            next_item = WorkItem(
                task_id=item.task_id, work_item_id=item.work_item_id,
                requirement=item.requirement, status=targets[event.kind],
                dependencies=item.dependencies, current_attempt=item.current_attempt,
            )
            audit_facts = ()
        work_items = work_items[:item_index] + (next_item,) + work_items[item_index + 1:]
        target_status = state.status
    else:
        audit_facts = ()
    if target_status is None:
        return _reject(state, input_digest, STATE_CONFLICT_ERROR)
    if target_status != state.status and suspension is not None:
        if (
            suspension.status == SuspensionStatus.WAITING
            and event.kind not in {
                EventKind.TASK_BLOCKED,
                EventKind.TASK_FAILED,
                EventKind.TASK_CANCELLED,
            }
        ):
            return _reject(state, input_digest, STATE_CONFLICT_ERROR)
        suspension = None

    result_id = Identity(IdentityKind.RESULT, f"{event.event_id.value}:accepted")
    audit_facts = (
        f"accepted:{event.event_id.kind.value}:{event.event_id.value}",
    ) + audit_facts
    result_state = State(
        protocol_version=state.protocol_version,
        installation_id=state.installation_id,
        leader_epoch=state.leader_epoch,
        task_id=state.task_id,
        revision=state.revision + 1,
        status=target_status,
        work_items=work_items,
        suspension=suspension,
        control=control,
    )
    result_digest = _accepted_result_digest(
        result_id,
        event.event_id,
        state,
        result_state,
        input_digest,
        obligations,
        audit_facts,
    )
    next_state = State(
        protocol_version=state.protocol_version,
        installation_id=state.installation_id,
        leader_epoch=state.leader_epoch,
        task_id=state.task_id,
        revision=state.revision + 1,
        status=target_status,
        work_items=work_items,
        suspension=suspension,
        control=control,
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
            obligations=obligations,
            audit_facts=audit_facts,
        )
    )


def replay(
    root: Union[GenesisRoot, CheckpointRoot],
    entries: Iterable[ReplayEntry],
    checkpoint_authentication: Optional[CheckpointAuthentication] = None,
) -> Replay:
    """Recompute canonical entries from a verified root without emitting effects."""

    if isinstance(root, GenesisRoot):
        if checkpoint_authentication is not None:
            raise ValueError("GenesisRoot does not accept checkpoint authentication")
        if (
            not _state_is_valid(root.state)
            or root.state.status != TaskStatus.PUBLISHED
            or root.state.revision != 0
            or root.state.accepted_events
        ):
            raise ValueError("GenesisRoot must be the canonical empty Task State")
    elif isinstance(root, CheckpointRoot):
        if not _checkpoint_authentication_is_valid(root, checkpoint_authentication):
            raise ValueError("CheckpointRoot authentication is invalid")
    else:
        raise ValueError("replay requires a verified replay root")
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
