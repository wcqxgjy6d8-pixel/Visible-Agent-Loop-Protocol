"""Durable Reference System storage for canonical Protocol Kernel history."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Optional, Sequence, Tuple

from .protocol_kernel import (
    Accepted,
    AcceptedEvent,
    Attempt,
    AttemptStatus,
    CheckpointAuthentication,
    CheckpointRoot,
    CheckpointTrustPolicy,
    CancellationScope,
    ControlReason,
    ControlState,
    ControlStatus,
    Dependency,
    Evidence,
    Event,
    EventKind,
    GenesisRoot,
    Identity,
    IdentityKind,
    NoOp,
    Rejected,
    Replay,
    ReplayEntry,
    Result,
    State,
    Suspension,
    SuspensionStatus,
    TaskStatus,
    WakeReason,
    WorkItem,
    WorkItemRequirement,
    WorkItemStatus,
    _canonical_json,
    _digest,
    _is_digest,
    replay,
    replay_prefix_digest,
)
from .receipt_store import (
    DURABILITY_PRECOMMIT_ERROR,
    DURABILITY_UNKNOWN_ERROR,
    REJECTED_OUTCOME,
    UNKNOWN_OR_COMMITTED_OUTCOME,
    ReceiptStore,
    ReceiptStoreError,
    _replace_file,
    _sync_directory,
)
from .protocol_receipts import (
    IDEMPOTENCY_CONFLICT_ERROR,
    STATE_CONFLICT_ERROR,
    digest as receipt_digest,
)


KERNEL_STORE_CORRUPT_ERROR = "VALP-E-KERNEL-STORE-CORRUPT"


class KernelStoreError(ReceiptStoreError):
    """Classified Kernel store failure with explicit commit outcome."""


@dataclass(frozen=True)
class KernelRecovery:
    replay: Replay
    entries: Tuple[ReplayEntry, ...]
    used_checkpoint: bool


class KernelEffectStatus(str, Enum):
    FULFILLED = "fulfilled"
    BLOCKED = "blocked"


EMPTY_EFFECT_LEDGER_DIGEST = _digest({
    "schema_version": "valp-kernel-effect-ledger.v1",
    "records": [],
})


@dataclass(frozen=True)
class KernelEffectRecord:
    effect_id: str
    obligation: str
    status: KernelEffectStatus
    proof_ref: str
    proof_digest: str
    ledger_revision: int
    prior_record_digest: str
    record_digest: str

    def canonical_without_digest(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-effect-record.v1",
            "effect_id": self.effect_id,
            "obligation": self.obligation,
            "status": self.status.value,
            "proof_ref": self.proof_ref,
            "proof_digest": self.proof_digest,
            "ledger_revision": self.ledger_revision,
            "prior_record_digest": self.prior_record_digest,
        }

    def canonical(self) -> Mapping[str, Any]:
        return {**self.canonical_without_digest(), "record_digest": self.record_digest}


@dataclass(frozen=True)
class KernelEffectReconciliation:
    pending: Tuple[str, ...]
    fulfilled: Tuple[KernelEffectRecord, ...]
    blocked: Tuple[KernelEffectRecord, ...]

    def canonical(self) -> Mapping[str, Any]:
        return {
            "schema_version": "valp-kernel-effect-reconciliation.v1",
            "pending": list(self.pending),
            "fulfilled": [item.canonical() for item in self.fulfilled],
            "blocked": [item.canonical() for item in self.blocked],
        }


def _shape(value: Any, required: set[str], optional: set[str] = set()) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not required.issubset(value) or not set(value).issubset(required | optional):
        raise ValueError("unexpected Kernel object shape")
    return value


def _identity(value: Any, kind: Optional[IdentityKind] = None) -> Identity:
    item = _shape(value, {"kind", "value"})
    identity = Identity(IdentityKind(item["kind"]), item["value"])
    if kind is not None and identity.kind != kind:
        raise ValueError("Kernel identity kind mismatch")
    return identity


def _dependency(value: Any) -> Dependency:
    item = _shape(value, {"work_item_id", "requirement"})
    return Dependency(
        _identity(item["work_item_id"], IdentityKind.WORK_ITEM),
        WorkItemRequirement(item["requirement"]),
    )


def _attempt(value: Any) -> Attempt:
    item = _shape(value, {
        "task_id", "work_item_id", "attempt_id", "dispatch_id",
        "dispatch_generation", "status",
    })
    return Attempt(
        _identity(item["task_id"], IdentityKind.TASK),
        _identity(item["work_item_id"], IdentityKind.WORK_ITEM),
        _identity(item["attempt_id"], IdentityKind.ATTEMPT),
        _identity(item["dispatch_id"], IdentityKind.DISPATCH),
        item["dispatch_generation"],
        AttemptStatus(item["status"]),
    )


def _work_item(value: Any) -> WorkItem:
    item = _shape(value, {
        "task_id", "work_item_id", "requirement", "status", "dependencies",
        "current_attempt",
    })
    if not isinstance(item["dependencies"], list):
        raise ValueError("Work Item dependencies must be an array")
    return WorkItem(
        _identity(item["task_id"], IdentityKind.TASK),
        _identity(item["work_item_id"], IdentityKind.WORK_ITEM),
        WorkItemRequirement(item["requirement"]),
        WorkItemStatus(item["status"]),
        tuple(_dependency(value) for value in item["dependencies"]),
        _attempt(item["current_attempt"]) if item["current_attempt"] is not None else None,
    )


def _suspension(value: Any) -> Suspension:
    item = _shape(value, {
        "task_id", "suspension_id", "suspension_epoch", "status",
        "wait_policy_id", "wait_policy_digest", "required_work_item_ids",
    }, {"accepted_wake_id", "wake_reason"})
    if not isinstance(item["required_work_item_ids"], list):
        raise ValueError("Suspension frontier must be an array")
    return Suspension(
        task_id=_identity(item["task_id"], IdentityKind.TASK),
        suspension_id=_identity(item["suspension_id"], IdentityKind.SUSPENSION),
        suspension_epoch=item["suspension_epoch"],
        status=SuspensionStatus(item["status"]),
        wait_policy_id=_identity(item["wait_policy_id"], IdentityKind.WAIT_POLICY),
        wait_policy_digest=item["wait_policy_digest"],
        required_work_item_ids=tuple(
            _identity(value, IdentityKind.WORK_ITEM)
            for value in item["required_work_item_ids"]
        ),
        accepted_wake_id=(
            _identity(item["accepted_wake_id"], IdentityKind.WAKE)
            if "accepted_wake_id" in item else None
        ),
        wake_reason=WakeReason(item["wake_reason"]) if "wake_reason" in item else None,
    )


def _control(value: Any) -> ControlState:
    item = _shape(value, {"intent_version", "status"}, {"active_interrupt_id"})
    return ControlState(
        item["intent_version"],
        ControlStatus(item["status"]),
        _identity(item["active_interrupt_id"], IdentityKind.INTERRUPT)
        if "active_interrupt_id" in item else None,
    )


def _accepted_event(value: Any) -> AcceptedEvent:
    item = _shape(value, {"event_id", "result_id", "input_digest", "result_digest"})
    return AcceptedEvent(
        _identity(item["event_id"], IdentityKind.EVENT),
        _identity(item["result_id"], IdentityKind.RESULT),
        item["input_digest"],
        item["result_digest"],
    )


def _state(value: Any) -> State:
    item = _shape(value, {
        "schema_version", "protocol_version", "installation_id", "leader_epoch",
        "task_id", "revision", "status", "accepted_events",
    }, {"work_items", "suspension", "control"})
    if item["schema_version"] != "valp-kernel-state.v1":
        raise ValueError("unsupported Kernel State schema")
    if not isinstance(item["accepted_events"], list) or not isinstance(item.get("work_items", []), list):
        raise ValueError("Kernel State arrays have invalid types")
    return State(
        item["protocol_version"],
        _identity(item["installation_id"], IdentityKind.INSTALLATION),
        item["leader_epoch"],
        _identity(item["task_id"], IdentityKind.TASK),
        item["revision"],
        TaskStatus(item["status"]),
        tuple(_accepted_event(value) for value in item["accepted_events"]),
        tuple(_work_item(value) for value in item.get("work_items", [])),
        _suspension(item["suspension"]) if "suspension" in item else None,
        _control(item["control"]) if "control" in item else None,
    )


def _event(value: Any) -> Event:
    required = {
        "schema_version", "event_id", "installation_id", "leader_epoch", "task_id",
        "kind", "expected_revision",
    }
    optional = {
        "work_item_id", "attempt_id", "dispatch_id", "dispatch_generation",
        "suspension_id", "suspension_epoch", "wait_policy_id", "wait_policy_digest",
        "required_work_item_ids", "wake_id", "wake_reason",
        "authority_principal_id", "authority_evidence_id", "control_reason",
        "cancellation_scope", "interrupt_id", "redirect_id", "intent_version",
        "next_intent_version", "superseded_work_item_ids",
    }
    item = _shape(value, required, optional)
    if item["schema_version"] != "valp-kernel-event.v1":
        raise ValueError("unsupported Kernel Event schema")
    frontier = item.get("required_work_item_ids", [])
    superseded = item.get("superseded_work_item_ids", [])
    if not isinstance(frontier, list) or not isinstance(superseded, list):
        raise ValueError("Event Work Item identities must be arrays")
    return Event(
        event_id=_identity(item["event_id"], IdentityKind.EVENT),
        installation_id=_identity(item["installation_id"], IdentityKind.INSTALLATION),
        leader_epoch=item["leader_epoch"],
        task_id=_identity(item["task_id"], IdentityKind.TASK),
        kind=EventKind(item["kind"]),
        expected_revision=item["expected_revision"],
        work_item_id=_identity(item["work_item_id"], IdentityKind.WORK_ITEM) if "work_item_id" in item else None,
        attempt_id=_identity(item["attempt_id"], IdentityKind.ATTEMPT) if "attempt_id" in item else None,
        dispatch_id=_identity(item["dispatch_id"], IdentityKind.DISPATCH) if "dispatch_id" in item else None,
        dispatch_generation=item.get("dispatch_generation"),
        suspension_id=_identity(item["suspension_id"], IdentityKind.SUSPENSION) if "suspension_id" in item else None,
        suspension_epoch=item.get("suspension_epoch"),
        wait_policy_id=_identity(item["wait_policy_id"], IdentityKind.WAIT_POLICY) if "wait_policy_id" in item else None,
        wait_policy_digest=item.get("wait_policy_digest"),
        required_work_item_ids=tuple(_identity(value, IdentityKind.WORK_ITEM) for value in frontier),
        wake_id=_identity(item["wake_id"], IdentityKind.WAKE) if "wake_id" in item else None,
        wake_reason=WakeReason(item["wake_reason"]) if "wake_reason" in item else None,
        authority_principal_id=_identity(item["authority_principal_id"], IdentityKind.PRINCIPAL) if "authority_principal_id" in item else None,
        authority_evidence_id=_identity(item["authority_evidence_id"], IdentityKind.EVIDENCE) if "authority_evidence_id" in item else None,
        control_reason=ControlReason(item["control_reason"]) if "control_reason" in item else None,
        cancellation_scope=CancellationScope(item["cancellation_scope"]) if "cancellation_scope" in item else None,
        interrupt_id=_identity(item["interrupt_id"], IdentityKind.INTERRUPT) if "interrupt_id" in item else None,
        redirect_id=_identity(item["redirect_id"], IdentityKind.REDIRECT) if "redirect_id" in item else None,
        intent_version=item.get("intent_version"),
        next_intent_version=item.get("next_intent_version"),
        superseded_work_item_ids=tuple(
            _identity(value, IdentityKind.WORK_ITEM) for value in superseded
        ),
    )


def decode_event(value: Any) -> Event:
    """Decode one canonical public Kernel Event without applying it."""

    event = _event(value)
    if event.canonical() != value:
        raise ValueError("Kernel Event canonical bindings mismatch")
    return event


def _evidence(value: Any) -> Evidence:
    item = _shape(value, {"schema_version", "evidence_id", "content_digest"})
    if item["schema_version"] != "valp-kernel-evidence.v1":
        raise ValueError("unsupported Kernel Evidence schema")
    return Evidence(_identity(item["evidence_id"], IdentityKind.EVIDENCE), item["content_digest"])


def _result(value: Any) -> Result:
    if not isinstance(value, dict) or value.get("schema_version") != "valp-kernel-result.v1":
        raise ValueError("unsupported Kernel Result schema")
    variant = value.get("variant")
    if variant == "accepted":
        item = _shape(value, {
            "schema_version", "variant", "result_id", "state", "input_digest",
            "result_digest", "obligations", "audit_facts",
        })
        if not isinstance(item["obligations"], list) or not isinstance(item["audit_facts"], list):
            raise ValueError("accepted Result arrays have invalid types")
        return Result(accepted=Accepted(
            _identity(item["result_id"], IdentityKind.RESULT), _state(item["state"]),
            item["input_digest"], item["result_digest"], tuple(item["obligations"]),
            tuple(item["audit_facts"]),
        ))
    if variant == "no_op":
        item = _shape(value, {
            "schema_version", "variant", "state", "input_digest",
            "prior_result_id", "prior_result_digest",
        })
        return Result(no_op=NoOp(
            _state(item["state"]), item["input_digest"],
            _identity(item["prior_result_id"], IdentityKind.RESULT),
            item["prior_result_digest"],
        ))
    if variant == "rejected":
        item = _shape(value, {
            "schema_version", "variant", "state", "input_digest", "error_code",
        }, {"prior_result_id", "prior_result_digest"})
        return Result(rejected=Rejected(
            _state(item["state"]), item["input_digest"], item["error_code"],
            _identity(item["prior_result_id"], IdentityKind.RESULT) if "prior_result_id" in item else None,
            item.get("prior_result_digest"),
        ))
    raise ValueError("unknown Kernel Result variant")


def decode_replay_entry(value: Any) -> ReplayEntry:
    item = _shape(value, {"schema_version", "event", "evidence_set", "result"})
    if item["schema_version"] != "valp-kernel-replay-entry.v1" or not isinstance(item["evidence_set"], list):
        raise ValueError("unsupported ReplayEntry schema")
    return ReplayEntry(
        _event(item["event"]),
        tuple(_evidence(value) for value in item["evidence_set"]),
        _result(item["result"]),
    )


def decode_genesis_root(value: Any) -> GenesisRoot:
    item = _shape(value, {"schema_version", "state", "prefix_digest"})
    if item["schema_version"] != "valp-kernel-genesis-root.v1":
        raise ValueError("unsupported GenesisRoot schema")
    root = GenesisRoot(_state(item["state"]))
    if root.canonical() != item:
        raise ValueError("GenesisRoot canonical bindings mismatch")
    replay(root, ())
    return root


def _trust_policy(value: Any) -> CheckpointTrustPolicy:
    item = _shape(value, {"schema_version", "trusted_evidence_ids"})
    if item["schema_version"] != "valp-kernel-checkpoint-trust-policy.v1" or not isinstance(item["trusted_evidence_ids"], list):
        raise ValueError("unsupported checkpoint trust policy")
    return CheckpointTrustPolicy(tuple(
        _identity(value, IdentityKind.EVIDENCE) for value in item["trusted_evidence_ids"]
    ))


def _checkpoint_root(value: Any) -> CheckpointRoot:
    item = _shape(value, {
        "schema_version", "state", "state_digest", "accepted_entry_count",
        "prefix_digest", "tail_event_id", "tail_result_id", "tail_result_digest",
        "checkpoint_result_id", "trust_policy_digest",
    })
    if item["schema_version"] != "valp-kernel-checkpoint-root.v1":
        raise ValueError("unsupported CheckpointRoot schema")
    root = CheckpointRoot(
        _state(item["state"]), item["accepted_entry_count"], item["prefix_digest"],
        _identity(item["tail_event_id"], IdentityKind.EVENT),
        _identity(item["tail_result_id"], IdentityKind.RESULT),
        item["tail_result_digest"],
        _identity(item["checkpoint_result_id"], IdentityKind.RESULT),
        item["trust_policy_digest"],
    )
    if root.canonical() != item:
        raise ValueError("CheckpointRoot canonical bindings mismatch")
    return root


def _checkpoint_authentication(value: Any) -> CheckpointAuthentication:
    item = _shape(value, {
        "schema_version", "checkpoint_result", "evidence_set", "trust_policy",
    })
    if item["schema_version"] != "valp-kernel-checkpoint-authentication.v1" or not isinstance(item["evidence_set"], list):
        raise ValueError("unsupported checkpoint authentication")
    return CheckpointAuthentication(
        _result(item["checkpoint_result"]),
        tuple(_evidence(value) for value in item["evidence_set"]),
        _trust_policy(item["trust_policy"]),
    )


class KernelStore(ReceiptStore):
    """Locked canonical Kernel journal with authenticated checkpoint recovery."""

    def __init__(self, root: Path, *, lock_timeout: float = 30.0) -> None:
        self.root = Path(root)
        self.proof_root = (
            self.root.parent.parent
            if self.root.name == "kernel" and self.root.parent.name == "runtime"
            else None
        )
        super().__init__(self.root / "replay.jsonl", "", 0, "", lock_timeout=lock_timeout)
        self.genesis_path = self.root / "genesis.json"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.effects_path = self.root / "effects.jsonl"

    @staticmethod
    def _parse(payload: bytes) -> Any:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=lambda pairs: _reject_pairs(pairs),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )

    def _read_canonical(self, path: Path, decoder) -> Any:
        self._ensure_regular(path)
        payload = path.read_bytes()
        if not payload.endswith(b"\n") or b"\r" in payload or payload.startswith(b"\xef\xbb\xbf"):
            raise ValueError(f"noncanonical Kernel artifact: {path.name}")
        value = decoder(self._parse(payload))
        if payload != _canonical_json(value.canonical()).encode("utf-8"):
            raise ValueError(f"noncanonical Kernel artifact: {path.name}")
        return value

    def _load_genesis(self) -> GenesisRoot:
        if not self.genesis_path.exists():
            raise ValueError("Kernel GenesisRoot is missing")
        return self._read_canonical(self.genesis_path, decode_genesis_root)

    def _load_entries(self) -> Tuple[ReplayEntry, ...]:
        self._ensure_regular(self.path)
        if not self.path.exists():
            return ()
        payload = self.path.read_bytes()
        if payload and (not payload.endswith(b"\n") or b"\r" in payload):
            raise ValueError("Kernel journal is not canonical LF-terminated JSONL")
        entries = []
        for line in payload.splitlines(keepends=True):
            if line == b"\n":
                raise ValueError("Kernel journal contains a blank line")
            entry = decode_replay_entry(self._parse(line[:-1]))
            if line != _canonical_json(entry.canonical()).encode("utf-8"):
                raise ValueError("Kernel journal contains a noncanonical entry")
            entries.append(entry)
        return tuple(entries)

    @staticmethod
    def _effect_id(obligation: str) -> str:
        return _digest({
            "schema_version": "valp-kernel-effect-identity.v1",
            "obligation": obligation,
        })

    @staticmethod
    def _safe_ref(value: Any) -> bool:
        if not isinstance(value, str) or not value or "\\" in value:
            return False
        path = Path(value)
        return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)

    def _load_effects(self) -> Tuple[KernelEffectRecord, ...]:
        self._ensure_regular(self.effects_path)
        if not self.effects_path.exists():
            return ()
        payload = self.effects_path.read_bytes()
        if payload and (not payload.endswith(b"\n") or b"\r" in payload or payload.startswith(b"\xef\xbb\xbf")):
            raise ValueError("Kernel effect ledger is not canonical LF-terminated JSONL")
        records = []
        prior_digest = EMPTY_EFFECT_LEDGER_DIGEST
        for revision, line in enumerate(payload.splitlines(keepends=True), 1):
            if line == b"\n":
                raise ValueError("Kernel effect ledger contains a blank line")
            item = _shape(self._parse(line[:-1]), {
                "schema_version", "effect_id", "obligation", "status", "proof_ref",
                "proof_digest", "ledger_revision", "prior_record_digest", "record_digest",
            })
            if item["schema_version"] != "valp-kernel-effect-record.v1":
                raise ValueError("unsupported Kernel effect record")
            record = KernelEffectRecord(
                effect_id=item["effect_id"],
                obligation=item["obligation"],
                status=KernelEffectStatus(item["status"]),
                proof_ref=item["proof_ref"],
                proof_digest=item["proof_digest"],
                ledger_revision=item["ledger_revision"],
                prior_record_digest=item["prior_record_digest"],
                record_digest=item["record_digest"],
            )
            if (
                not isinstance(record.obligation, str)
                or not record.obligation.startswith("adapter_cancel:")
                or record.effect_id != self._effect_id(record.obligation)
                or not self._safe_ref(record.proof_ref)
                or not _is_digest(record.proof_digest)
                or record.ledger_revision != revision
                or record.prior_record_digest != prior_digest
                or record.record_digest != _digest(record.canonical_without_digest())
                or line != _canonical_json(record.canonical()).encode("utf-8")
            ):
                raise ValueError("Kernel effect record bindings are invalid")
            records.append(record)
            prior_digest = record.record_digest
        if len({record.effect_id for record in records}) != len(records):
            raise ValueError("Kernel effect identity is duplicated")
        return tuple(records)

    @staticmethod
    def _journal_bytes(entries: Sequence[ReplayEntry]) -> bytes:
        return b"".join(
            _canonical_json(entry.canonical()).encode("utf-8") for entry in entries
        )

    def _replace(self, path: Path, payload: bytes, lock_state: dict[str, bool]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _replace_file(temporary, path)
            lock_state["replaced"] = True
            self.directory_sync_supported = _sync_directory(path.parent)
        except OSError as exc:
            code = DURABILITY_UNKNOWN_ERROR if lock_state["replaced"] else DURABILITY_PRECOMMIT_ERROR
            outcome = UNKNOWN_OR_COMMITTED_OUTCOME if lock_state["replaced"] else REJECTED_OUTCOME
            raise KernelStoreError(code, f"Kernel store replacement failed: {exc}", outcome=outcome) from exc
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def initialize(self, genesis: GenesisRoot) -> None:
        replay(genesis, ())
        payload = _canonical_json(genesis.canonical()).encode("utf-8")
        try:
            with self._locked() as lock_state:
                for path in (self.genesis_path, self.checkpoint_path, self.effects_path):
                    self._ensure_regular(path)
                if self.genesis_path.exists():
                    if self.genesis_path.read_bytes() != payload:
                        raise KernelStoreError(IDEMPOTENCY_CONFLICT_ERROR, "GenesisRoot differs")
                    return
                if self.path.exists() or self.checkpoint_path.exists() or self.effects_path.exists():
                    raise KernelStoreError(KERNEL_STORE_CORRUPT_ERROR, "history exists without GenesisRoot")
                self._replace(self.genesis_path, payload, lock_state)
        except ReceiptStoreError as exc:
            if isinstance(exc, KernelStoreError):
                raise
            raise KernelStoreError(exc.code, str(exc), outcome=exc.outcome) from exc

    def append(self, entry: ReplayEntry) -> KernelRecovery:
        if not isinstance(entry, ReplayEntry) or entry.result.accepted is None:
            raise KernelStoreError(STATE_CONFLICT_ERROR, "Kernel journal requires an accepted ReplayEntry")
        try:
            with self._locked() as lock_state:
                genesis = self._load_genesis()
                entries = self._load_entries()
                for prior in entries:
                    if prior.event.event_id == entry.event.event_id:
                        if prior.canonical() == entry.canonical():
                            return self._recover_unlocked(genesis, entries)
                        raise KernelStoreError(IDEMPOTENCY_CONFLICT_ERROR, "Event identity differs")
                replay(genesis, entries + (entry,))
                self._replace(self.path, self._journal_bytes(entries + (entry,)), lock_state)
                return self._recover_unlocked(genesis, entries + (entry,))
        except KernelStoreError:
            raise
        except ReceiptStoreError as exc:
            raise KernelStoreError(exc.code, str(exc), outcome=exc.outcome) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            raise KernelStoreError(KERNEL_STORE_CORRUPT_ERROR, f"Kernel append failed: {exc}") from exc

    def _load_checkpoint(self) -> Optional[Tuple[CheckpointRoot, CheckpointAuthentication]]:
        self._ensure_regular(self.checkpoint_path)
        if not self.checkpoint_path.exists():
            return None
        value = self._parse(self.checkpoint_path.read_bytes())
        item = _shape(value, {"schema_version", "root", "authentication"})
        if item["schema_version"] != "valp-kernel-checkpoint-envelope.v1":
            raise ValueError("unsupported checkpoint envelope")
        root = _checkpoint_root(item["root"])
        authentication = _checkpoint_authentication(item["authentication"])
        canonical = {
            "schema_version": "valp-kernel-checkpoint-envelope.v1",
            "root": root.canonical(),
            "authentication": authentication.canonical(),
        }
        if self.checkpoint_path.read_bytes() != _canonical_json(canonical).encode("utf-8"):
            raise ValueError("checkpoint envelope is noncanonical")
        return root, authentication

    def _recover_unlocked(self, genesis: GenesisRoot, entries: Tuple[ReplayEntry, ...]) -> KernelRecovery:
        full = replay(genesis, entries)
        checkpoint = self._load_checkpoint()
        if checkpoint is None:
            return KernelRecovery(full, entries, False)
        root, authentication = checkpoint
        count = root.accepted_entry_count
        if count > len(entries):
            raise ValueError("checkpoint is ahead of the Kernel journal")
        prefix = entries[:count]
        if root.prefix_digest != replay_prefix_digest(prefix):
            raise ValueError("checkpoint prefix digest does not match journal")
        prefix_replay = replay(genesis, prefix)
        if prefix_replay.state != root.state:
            raise ValueError("checkpoint State does not match journal prefix")
        recovered = replay(root, entries[count:], authentication)
        if recovered.state.canonical() != full.state.canonical():
            raise ValueError("checkpoint recovery differs from full Genesis replay")
        return KernelRecovery(recovered, entries, True)

    def recover(self) -> KernelRecovery:
        try:
            with self._locked():
                return self._recover_unlocked(self._load_genesis(), self._load_entries())
        except KernelStoreError:
            raise
        except ReceiptStoreError as exc:
            raise KernelStoreError(exc.code, str(exc), outcome=exc.outcome) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            raise KernelStoreError(KERNEL_STORE_CORRUPT_ERROR, f"Kernel recovery failed: {exc}") from exc

    @staticmethod
    def _accepted_obligations(entries: Sequence[ReplayEntry]) -> Tuple[str, ...]:
        obligations = []
        seen = set()
        for entry in entries:
            for obligation in entry.result.accepted.obligations:
                if obligation not in seen:
                    obligations.append(obligation)
                    seen.add(obligation)
        return tuple(obligations)

    def _reconcile_effects_unlocked(
        self,
        entries: Sequence[ReplayEntry],
        records: Sequence[KernelEffectRecord],
    ) -> KernelEffectReconciliation:
        obligations = self._accepted_obligations(entries)
        allowed = set(obligations)
        if any(record.obligation not in allowed for record in records):
            raise ValueError("Kernel effect record has no accepted obligation")
        by_obligation = {record.obligation: record for record in records}
        self._validate_effect_proofs(records)
        return KernelEffectReconciliation(
            pending=tuple(item for item in obligations if item not in by_obligation),
            fulfilled=tuple(
                by_obligation[item] for item in obligations
                if item in by_obligation and by_obligation[item].status == KernelEffectStatus.FULFILLED
            ),
            blocked=tuple(
                by_obligation[item] for item in obligations
                if item in by_obligation and by_obligation[item].status == KernelEffectStatus.BLOCKED
            ),
        )

    def _validate_effect_proofs(self, records: Sequence[KernelEffectRecord]) -> None:
        if self.proof_root is None:
            return
        root = self.proof_root.resolve()
        for record in records:
            path = (root / record.proof_ref).resolve()
            try:
                path.relative_to(root)
            except ValueError as error:
                raise ValueError("Kernel effect proof escapes the task root") from error
            self._ensure_regular(path)
            payload = path.read_bytes()
            if not payload or receipt_digest(payload) != record.proof_digest:
                raise ValueError("Kernel effect proof bytes do not match the ledger")

    def reconcile_effects(self) -> KernelEffectReconciliation:
        try:
            with self._locked():
                recovery = self._recover_unlocked(self._load_genesis(), self._load_entries())
                return self._reconcile_effects_unlocked(recovery.entries, self._load_effects())
        except KernelStoreError:
            raise
        except ReceiptStoreError as exc:
            raise KernelStoreError(exc.code, str(exc), outcome=exc.outcome) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            raise KernelStoreError(KERNEL_STORE_CORRUPT_ERROR, f"Kernel effect reconciliation failed: {exc}") from exc

    def record_effect(
        self,
        obligation: str,
        *,
        status: KernelEffectStatus,
        proof_ref: str,
        proof_digest: str,
    ) -> KernelEffectRecord:
        if (
            not isinstance(status, KernelEffectStatus)
            or not isinstance(obligation, str)
            or not obligation.startswith("adapter_cancel:")
            or not self._safe_ref(proof_ref)
            or not _is_digest(proof_digest)
        ):
            raise KernelStoreError(STATE_CONFLICT_ERROR, "Kernel effect proof is invalid")
        if self.proof_root is not None:
            candidate_path = (self.proof_root.resolve() / proof_ref).resolve()
            try:
                candidate_path.relative_to(self.proof_root.resolve())
                self._ensure_regular(candidate_path)
                candidate_payload = candidate_path.read_bytes()
            except (OSError, ValueError) as error:
                raise KernelStoreError(STATE_CONFLICT_ERROR, "Kernel effect proof is unavailable") from error
            if not candidate_payload or receipt_digest(candidate_payload) != proof_digest:
                raise KernelStoreError(STATE_CONFLICT_ERROR, "Kernel effect proof digest differs")
        try:
            with self._locked() as lock_state:
                recovery = self._recover_unlocked(self._load_genesis(), self._load_entries())
                obligations = set(self._accepted_obligations(recovery.entries))
                if obligation not in obligations:
                    raise KernelStoreError(STATE_CONFLICT_ERROR, "Kernel effect has no accepted obligation")
                records = self._load_effects()
                effect_id = self._effect_id(obligation)
                prior = next((item for item in records if item.effect_id == effect_id), None)
                revision = len(records) + 1
                prior_digest = records[-1].record_digest if records else EMPTY_EFFECT_LEDGER_DIGEST
                candidate_body = {
                    "schema_version": "valp-kernel-effect-record.v1",
                    "effect_id": effect_id,
                    "obligation": obligation,
                    "status": status.value,
                    "proof_ref": proof_ref,
                    "proof_digest": proof_digest,
                    "ledger_revision": revision,
                    "prior_record_digest": prior_digest,
                }
                candidate = KernelEffectRecord(
                    effect_id, obligation, status, proof_ref, proof_digest, revision,
                    prior_digest, _digest(candidate_body),
                )
                if prior is not None:
                    if (
                        prior.obligation == obligation
                        and prior.status == status
                        and prior.proof_ref == proof_ref
                        and prior.proof_digest == proof_digest
                    ):
                        return prior
                    raise KernelStoreError(IDEMPOTENCY_CONFLICT_ERROR, "Kernel effect identity differs")
                payload = b"".join(
                    _canonical_json(item.canonical()).encode("utf-8")
                    for item in records + (candidate,)
                )
                try:
                    self._replace(self.effects_path, payload, lock_state)
                except KernelStoreError as exc:
                    if exc.outcome != UNKNOWN_OR_COMMITTED_OUTCOME:
                        raise
                    reconciled = self._load_effects()
                    exact = next((item for item in reconciled if item.effect_id == effect_id), None)
                    if exact != candidate:
                        raise
                return candidate
        except KernelStoreError:
            raise
        except ReceiptStoreError as exc:
            raise KernelStoreError(exc.code, str(exc), outcome=exc.outcome) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            raise KernelStoreError(KERNEL_STORE_CORRUPT_ERROR, f"Kernel effect recording failed: {exc}") from exc

    def persist_checkpoint(
        self,
        root: CheckpointRoot,
        authentication: CheckpointAuthentication,
    ) -> None:
        try:
            with self._locked() as lock_state:
                genesis = self._load_genesis()
                entries = self._load_entries()
                count = root.accepted_entry_count
                if count > len(entries):
                    raise ValueError("checkpoint is ahead of the Kernel journal")
                prefix = entries[:count]
                if root.prefix_digest != replay_prefix_digest(prefix):
                    raise ValueError("checkpoint prefix digest does not match journal")
                if replay(genesis, prefix).state != root.state:
                    raise ValueError("checkpoint State does not match journal prefix")
                replay(root, (), authentication)
                envelope = {
                    "schema_version": "valp-kernel-checkpoint-envelope.v1",
                    "root": root.canonical(),
                    "authentication": authentication.canonical(),
                }
                payload = _canonical_json(envelope).encode("utf-8")
                if self.checkpoint_path.exists() and self.checkpoint_path.read_bytes() == payload:
                    return
                self._replace(self.checkpoint_path, payload, lock_state)
        except KernelStoreError:
            raise
        except ReceiptStoreError as exc:
            raise KernelStoreError(exc.code, str(exc), outcome=exc.outcome) from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            raise KernelStoreError(KERNEL_STORE_CORRUPT_ERROR, f"checkpoint persistence failed: {exc}") from exc


def _reject_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


__all__ = [
    "KERNEL_STORE_CORRUPT_ERROR",
    "EMPTY_EFFECT_LEDGER_DIGEST",
    "KernelEffectRecord",
    "KernelEffectReconciliation",
    "KernelEffectStatus",
    "KernelRecovery",
    "KernelStore",
    "KernelStoreError",
    "decode_genesis_root",
    "decode_event",
    "decode_replay_entry",
]
