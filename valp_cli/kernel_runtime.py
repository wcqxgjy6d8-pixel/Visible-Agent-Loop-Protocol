"""Durable bridge from adopted runtime receipts to pure Kernel wait/wake truth."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .control_plane import write_json
from .kernel_store import KernelStore, KernelStoreError
from .protocol_kernel import (
    Attempt,
    AttemptStatus,
    Dependency,
    Event,
    EventKind,
    Evidence,
    GenesisRoot,
    Identity,
    IdentityKind,
    PROTOCOL_VERSION,
    ReplayEntry,
    ResultVariant,
    State,
    SuspensionStatus,
    TaskStatus,
    WakeReason,
    WorkItem,
    WorkItemRequirement,
    WorkItemStatus,
    reduce,
)
from .protocol_receipts import digest


class KernelRuntimeError(RuntimeError):
    pass


def _identity(kind: IdentityKind, value: object) -> Identity:
    text = str(value or "")
    if not text:
        raise KernelRuntimeError(f"missing Kernel {kind.value} identity")
    return Identity(kind, text)


def _event_id(task_id: str, kind: EventKind, payload: dict[str, Any]) -> Identity:
    return Identity(IdentityKind.EVENT, digest({
        "task_id": task_id,
        "kind": kind.value,
        "payload": payload,
    }))


def _append(
    store: KernelStore,
    state: State,
    event: Event,
    evidence: Iterable[Evidence] = (),
) -> State:
    evidence_set = tuple(evidence)
    result = reduce(state, event, evidence_set)
    if result.variant == ResultVariant.NO_OP:
        return result.no_op.state
    if result.accepted is None:
        code = result.rejected.error_code if result.rejected else "VALP-E-STATE-CONFLICT"
        raise KernelRuntimeError(f"Kernel runtime event rejected: {code}")
    entry = ReplayEntry(event, evidence_set, result)
    try:
        return store.append(entry).replay.state
    except KernelStoreError as error:
        raise KernelRuntimeError(f"Kernel runtime journal append failed: {error.code}") from error


def _reference_identity(directory: Path) -> tuple[Identity, int]:
    workspace = directory.resolve().parents[2]
    try:
        installation = json.loads(
            (workspace / ".valp" / "installation.json").read_text(encoding="utf-8")
        )
        state = json.loads((workspace / ".valp" / "state.json").read_text(encoding="utf-8"))
    except (IndexError, OSError, json.JSONDecodeError) as error:
        raise KernelRuntimeError("Kernel runtime installation identity is unavailable") from error
    installation_id = str(installation.get("installation_id") or "")
    epoch = state.get("active_leader_epoch")
    if (
        not installation_id
        or state.get("installation_id") != installation_id
        or type(epoch) is not int
        or epoch < 1
        or installation.get("active_leader_epoch") != epoch
    ):
        raise KernelRuntimeError("Kernel runtime installation identity is inconsistent")
    return _identity(IdentityKind.INSTALLATION, installation_id), epoch


def _matching_submission(
    receipts: list[dict[str, Any]], item: dict[str, Any]
) -> dict[str, Any]:
    matches = [
        receipt for receipt in receipts
        if receipt.get("schema_version") == "valp-dispatch-receipt.v3"
        and receipt.get("event") == "dispatch_submitted"
        and receipt.get("work_item_id") == item.get("work_item_id")
        and receipt.get("dispatch_id") == item.get("dispatch_id")
        and receipt.get("dispatch_generation") == item.get("dispatch_generation")
    ]
    if len(matches) != 1:
        raise KernelRuntimeError("Kernel suspension requires one exact v3 submission per work item")
    return matches[0]


def _receipt_evidence(receipt: dict[str, Any]) -> Evidence:
    return Evidence(
        _identity(IdentityKind.EVIDENCE, receipt["receipt_id"]),
        str(receipt["receipt_digest"]),
    )


def _declared_work_items(
    directory: Path,
    task_id: str,
    required: list[dict[str, Any]],
    submissions: list[dict[str, Any]],
    task_identity: Identity,
) -> tuple[WorkItem, ...]:
    path = directory / "submission-dependencies.json"
    if path.is_file():
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise KernelRuntimeError("Kernel runtime Work Item graph is malformed") from error
    else:
        document = {}
    if (
        document.get("schema_version") != "valp-submission-dependencies.v2"
        or document.get("task_id") != task_id
        or not isinstance(document.get("work_items"), list)
    ):
        declared = required
        dependency_records: list[dict[str, Any]] = []
    else:
        declared = [item for item in document["work_items"] if isinstance(item, dict)]
        dependency_records = [
            item for item in document.get("dependencies") or [] if isinstance(item, dict)
        ]
    if not declared:
        raise KernelRuntimeError("Kernel runtime requires a non-empty declared Work Item graph")
    required_submissions = {
        str(item["work_item_id"]): submission
        for item, submission in zip(required, submissions)
    }
    identities = {
        str(item.get("work_item_id") or ""): _identity(
            IdentityKind.WORK_ITEM, item.get("work_item_id")
        )
        for item in declared
    }
    if len(identities) != len(declared) or set(required_submissions) - set(identities):
        raise KernelRuntimeError("Kernel runtime Work Item graph has missing or duplicate identities")
    dependencies_by_target: dict[str, list[Dependency]] = {key: [] for key in identities}
    for edge in dependency_records:
        source = str(edge.get("prerequisite_work_item_id") or "")
        target = str(edge.get("dependent_work_item_id") or "")
        if source not in identities or target not in identities:
            raise KernelRuntimeError("Kernel runtime dependency references an unknown Work Item")
        dependencies_by_target[target].append(
            Dependency(identities[source], WorkItemRequirement.REQUIRED)
        )
    work_items = []
    for item in declared:
        work_item_id = str(item["work_item_id"])
        submission = required_submissions.get(work_item_id)
        work_items.append(WorkItem(
            task_identity,
            identities[work_item_id],
            WorkItemRequirement.REQUIRED,
            WorkItemStatus.RUNNING if submission is not None else WorkItemStatus.PENDING,
            dependencies=tuple(dependencies_by_target[work_item_id]),
            current_attempt=(
                Attempt(
                    task_identity,
                    identities[work_item_id],
                    _identity(IdentityKind.ATTEMPT, submission["attempt_id"]),
                    _identity(IdentityKind.DISPATCH, submission["dispatch_id"]),
                    int(submission["dispatch_generation"]),
                    AttemptStatus.RUNNING,
                )
                if submission is not None else None
            ),
        ))
    return tuple(work_items)


def _start_frontier_attempts(
    store: KernelStore,
    state: State,
    task_id: str,
    required: list[dict[str, Any]],
    submissions: list[dict[str, Any]],
) -> State:
    for item_record, submission in zip(required, submissions):
        work_item_id = _identity(IdentityKind.WORK_ITEM, item_record["work_item_id"])
        item = next(
            (candidate for candidate in state.work_items if candidate.work_item_id == work_item_id),
            None,
        )
        attempt_tuple = (
            _identity(IdentityKind.ATTEMPT, submission["attempt_id"]),
            _identity(IdentityKind.DISPATCH, submission["dispatch_id"]),
            int(submission["dispatch_generation"]),
        )
        if item is None:
            raise KernelRuntimeError("Kernel frontier references an undeclared Work Item")
        if item.status == WorkItemStatus.RUNNING and item.current_attempt is not None:
            if (
                item.current_attempt.attempt_id,
                item.current_attempt.dispatch_id,
                item.current_attempt.dispatch_generation,
            ) != attempt_tuple:
                raise KernelRuntimeError("Kernel frontier running Attempt conflicts with submission")
            continue
        for kind in (
            EventKind.WORK_ITEM_ELIGIBLE,
            EventKind.ATTEMPT_CREATED,
            EventKind.ATTEMPT_SUBMITTED,
            EventKind.ATTEMPT_RUNNING,
        ):
            payload = {
                "work_item_id": work_item_id.value,
                "attempt_id": attempt_tuple[0].value,
                "dispatch_id": attempt_tuple[1].value,
                "dispatch_generation": attempt_tuple[2],
                "receipt_id": submission["receipt_id"],
            }
            event = Event(
                _event_id(task_id, kind, payload),
                state.installation_id,
                state.leader_epoch,
                state.task_id,
                kind,
                state.revision,
                work_item_id=work_item_id,
                attempt_id=(attempt_tuple[0] if kind != EventKind.WORK_ITEM_ELIGIBLE else None),
                dispatch_id=(attempt_tuple[1] if kind != EventKind.WORK_ITEM_ELIGIBLE else None),
                dispatch_generation=(
                    attempt_tuple[2] if kind != EventKind.WORK_ITEM_ELIGIBLE else None
                ),
            )
            state = _append(
                store,
                state,
                event,
                (_receipt_evidence(submission),) if kind == EventKind.ATTEMPT_RUNNING else (),
            )
    return state


def start_kernel_suspension(
    directory: Path,
    task_id: str,
    suspension: dict[str, Any],
    receipts: list[dict[str, Any]],
) -> dict[str, Any]:
    required = [item for item in suspension.get("required_work_items") or [] if isinstance(item, dict)]
    if not required:
        raise KernelRuntimeError("Kernel suspension requires a non-empty Work Item frontier")
    installation_id, leader_epoch = _reference_identity(directory)
    task_identity = _identity(IdentityKind.TASK, task_id)
    submissions = [_matching_submission(receipts, item) for item in required]
    store = KernelStore(directory / "runtime" / "kernel")
    if not store.genesis_path.is_file():
        work_items = _declared_work_items(
            directory, task_id, required, submissions, task_identity
        )
        genesis = GenesisRoot(State(
            PROTOCOL_VERSION,
            installation_id,
            leader_epoch,
            task_identity,
            0,
            TaskStatus.PUBLISHED,
            work_items=work_items,
        ))
        try:
            store.initialize(genesis)
        except KernelStoreError as error:
            raise KernelRuntimeError(f"Kernel runtime initialization failed: {error.code}") from error
        state = genesis.state
        for index, kind in enumerate((
            EventKind.ROUTING_VALIDATION_STARTED,
            EventKind.ROUTING_VALIDATION_PASSED,
            EventKind.DISPATCH_ACCEPTED,
        )):
            event = Event(
                _event_id(task_id, kind, {"spine_index": index}),
                installation_id,
                leader_epoch,
                task_identity,
                kind,
                state.revision,
            )
            state = _append(store, state, event)
    else:
        try:
            state = store.recover().replay.state
        except KernelStoreError as error:
            raise KernelRuntimeError(f"Kernel runtime recovery failed: {error.code}") from error
        if (
            state.installation_id != installation_id
            or state.leader_epoch != leader_epoch
            or state.task_id != task_identity
        ):
            raise KernelRuntimeError("Kernel runtime history identity conflicts with active installation")

    if state.suspension is None or state.suspension.status == SuspensionStatus.RESUMED:
        state = _start_frontier_attempts(
            store, state, task_id, required, submissions
        )

    if state.suspension is not None and state.suspension.status == SuspensionStatus.WAITING:
        binding = json.loads(
            (directory / "runtime" / "kernel" / "workflow-binding.json").read_text(encoding="utf-8")
        )
        if binding.get("workflow_suspension_id") != suspension.get("suspension_id"):
            raise KernelRuntimeError("Kernel runtime already has a different active suspension")
        return binding
    kernel_epoch = 0 if state.suspension is None else state.suspension.suspension_epoch + 1
    policy_ref = str(suspension.get("wait_policy_ref") or "wait-policy.json")
    try:
        policy = json.loads((directory / policy_ref).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise KernelRuntimeError("Kernel suspension wait policy is unavailable") from error
    policy_digest = digest(policy)
    suspension_id = _identity(IdentityKind.SUSPENSION, suspension["suspension_id"])
    wait_policy_id = _identity(IdentityKind.WAIT_POLICY, suspension["wait_policy_id"])
    frontier = tuple(
        _identity(IdentityKind.WORK_ITEM, item["work_item_id"]) for item in required
    )
    event = Event(
        _event_id(task_id, EventKind.SUSPENSION_STARTED, {
            "suspension_id": suspension["suspension_id"],
            "kernel_epoch": kernel_epoch,
            "workflow_epoch": suspension["suspension_epoch"],
            "wait_policy_digest": policy_digest,
            "frontier": [item.value for item in frontier],
        }),
        installation_id,
        leader_epoch,
        task_identity,
        EventKind.SUSPENSION_STARTED,
        state.revision,
        suspension_id=suspension_id,
        suspension_epoch=kernel_epoch,
        wait_policy_id=wait_policy_id,
        wait_policy_digest=policy_digest,
        required_work_item_ids=frontier,
    )
    state = _append(
        store,
        state,
        event,
        [Evidence(_identity(IdentityKind.EVIDENCE, f"wait-policy:{policy_ref}"), policy_digest)]
        + [_receipt_evidence(receipt) for receipt in submissions],
    )
    binding = {
        "schema_version": "valp-kernel-workflow-binding.v1",
        "task_id": task_id,
        "installation_id": installation_id.value,
        "leader_epoch": leader_epoch,
        "workflow_suspension_id": suspension["suspension_id"],
        "workflow_suspension_epoch": suspension["suspension_epoch"],
        "kernel_suspension_epoch": state.suspension.suspension_epoch,
        "wait_policy_id": state.suspension.wait_policy_id.value,
        "wait_policy_digest": state.suspension.wait_policy_digest,
        "required_work_item_ids": [item.value for item in state.suspension.required_work_item_ids],
        "workflow_suspension": suspension,
        "status": state.suspension.status.value,
        "kernel_revision": state.revision,
    }
    write_json(directory / "runtime" / "kernel" / "workflow-binding.json", binding)
    return binding


def record_kernel_completion(
    directory: Path,
    task_id: str,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    if (
        receipt.get("schema_version") != "valp-dispatch-receipt.v3"
        or receipt.get("event") != "dispatch_completed"
        or receipt.get("task_id") != task_id
    ):
        raise KernelRuntimeError("Kernel completion requires one canonical v3 terminal receipt")
    store = KernelStore(directory / "runtime" / "kernel")
    try:
        state = store.recover().replay.state
    except KernelStoreError as error:
        raise KernelRuntimeError(f"Kernel runtime recovery failed: {error.code}") from error
    work_item_id = _identity(IdentityKind.WORK_ITEM, receipt["work_item_id"])
    item = next((candidate for candidate in state.work_items if candidate.work_item_id == work_item_id), None)
    if item is None or item.current_attempt is None:
        raise KernelRuntimeError("Kernel completion Work Item is not in the active frontier")
    if item.status == WorkItemStatus.COMPLETED:
        if item.current_attempt.attempt_id.value != receipt["attempt_id"]:
            raise KernelRuntimeError("Kernel completion exact retry Attempt conflicts")
        return state.canonical()
    event = Event(
        _event_id(task_id, EventKind.ATTEMPT_COMPLETED, {
            "receipt_id": receipt["receipt_id"],
            "receipt_digest": receipt["receipt_digest"],
        }),
        state.installation_id,
        state.leader_epoch,
        state.task_id,
        EventKind.ATTEMPT_COMPLETED,
        state.revision,
        work_item_id=work_item_id,
        attempt_id=_identity(IdentityKind.ATTEMPT, receipt["attempt_id"]),
        dispatch_id=_identity(IdentityKind.DISPATCH, receipt["dispatch_id"]),
        dispatch_generation=int(receipt["dispatch_generation"]),
    )
    return _append(store, state, event, (_receipt_evidence(receipt),)).canonical()


def accept_kernel_wake(
    directory: Path,
    task_id: str,
    wake_id: str,
    *,
    workflow_projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    store = KernelStore(directory / "runtime" / "kernel")
    try:
        state = store.recover().replay.state
    except KernelStoreError as error:
        raise KernelRuntimeError(f"Kernel runtime recovery failed: {error.code}") from error
    suspension = state.suspension
    if suspension is None:
        raise KernelRuntimeError("Kernel wake has no active suspension")
    if suspension.status == SuspensionStatus.RESUMED:
        if suspension.accepted_wake_id.value != wake_id:
            raise KernelRuntimeError("Kernel wake exact retry conflicts")
        return state.canonical()
    event = Event(
        _event_id(task_id, EventKind.WAKE_ACCEPTED, {
            "suspension_id": suspension.suspension_id.value,
            "suspension_epoch": suspension.suspension_epoch,
            "wake_id": wake_id,
        }),
        state.installation_id,
        state.leader_epoch,
        state.task_id,
        EventKind.WAKE_ACCEPTED,
        state.revision,
        suspension_id=suspension.suspension_id,
        suspension_epoch=suspension.suspension_epoch,
        wait_policy_id=suspension.wait_policy_id,
        wait_policy_digest=suspension.wait_policy_digest,
        required_work_item_ids=suspension.required_work_item_ids,
        wake_id=_identity(IdentityKind.WAKE, wake_id),
        wake_reason=WakeReason.DEPENDENCY_READY,
    )
    state = _append(store, state, event)
    binding_path = directory / "runtime" / "kernel" / "workflow-binding.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    binding.update({
        "status": state.suspension.status.value,
        "accepted_wake_id": wake_id,
        "wake_reason": WakeReason.DEPENDENCY_READY.value,
        "kernel_revision": state.revision,
    })
    if workflow_projection is not None:
        binding["workflow_wake_projection"] = workflow_projection
    write_json(binding_path, binding)
    return state.canonical()


__all__ = [
    "KernelRuntimeError",
    "accept_kernel_wake",
    "record_kernel_completion",
    "start_kernel_suspension",
]
