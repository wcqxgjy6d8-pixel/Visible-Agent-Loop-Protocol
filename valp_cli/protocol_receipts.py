"""Pure Protocol 0.3 receipt-write and compatibility projection contract."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import hashlib
import json
from typing import Any, Mapping, Optional, Sequence, Tuple


PROTOCOL_VERSION = "0.3.0-draft"
RECEIPT_SCHEMA_VERSION = "valp-dispatch-receipt.v3"
MIGRATION_UNSUPPORTED_ERROR = "VALP-E-MIGRATION-UNSUPPORTED"
STATE_CONFLICT_ERROR = "VALP-E-STATE-CONFLICT"
IDEMPOTENCY_CONFLICT_ERROR = "VALP-E-IDEMPOTENCY-CONFLICT"


class ReceiptMode(str, Enum):
    FULL = "full"
    REMOTE = "remote"
    MANUAL = "manual"


class ReceiptProofKind(str, Enum):
    PROCESS_BOUND = "process_bound"
    CONTENT_BOUND = "content_bound"
    MANUAL_ATTESTED = "manual_attested"
    TRANSPORT_ONLY = "transport_only"


class ReceiptWriteVariant(str, Enum):
    ACCEPTED = "accepted"
    NO_OP = "no_op"
    REJECTED = "rejected"


RECEIPT_EVENTS = frozenset(
    {
        "dispatch_written",
        "dispatch_inserted",
        "dispatch_submitted",
        "dispatch_completed",
        "dispatch_blocked",
        "manual_dispatch_written",
        "manual_delivery_attested",
        "manual_result_attested",
        "manual_blocked",
    }
)
MANUAL_EVENTS = frozenset(
    {
        "manual_dispatch_written",
        "manual_delivery_attested",
        "manual_result_attested",
        "manual_blocked",
    }
)
TERMINAL_EVENTS = frozenset(
    {"dispatch_completed", "dispatch_blocked", "manual_result_attested", "manual_blocked"}
)
ROLES = frozenset(
    {"coordinator", "implementer", "reviewer", "prototype", "researcher", "other"}
)
V2_SUBMISSION_PROOF_FIELDS = frozenset(
    {
        "submission_id",
        "payload_digest",
        "acknowledged",
        "receipt_id",
        "task_id",
        "agent",
        "role",
        "work_item_id",
        "dispatch_id",
        "dispatch_generation",
        "event_sequence",
        "dispatch_ref",
        "expected_refs",
    }
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


def digest(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    else:
        payload = canonical_json(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


EMPTY_RECEIPT_LEDGER_DIGEST = digest(
    {"receipts": [], "schema_version": "valp-receipt-ledger-prefix.v1"}
)


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _is_int(value: Any, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _is_safe_ref(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    if "\\" in value or ":" in value:
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


@dataclass(frozen=True)
class ProofBinding:
    proof_kind: ReceiptProofKind | str
    proof_ref: str
    proof_digest: str
    subject_digest: str

    def canonical(self) -> Mapping[str, Any]:
        kind = self.proof_kind.value if isinstance(self.proof_kind, ReceiptProofKind) else self.proof_kind
        return {
            "proof_kind": kind,
            "proof_ref": self.proof_ref,
            "proof_digest": self.proof_digest,
            "subject_digest": self.subject_digest,
        }


@dataclass(frozen=True)
class ApprovalBinding:
    status: str
    policy_digest: str
    approval_id: Optional[str] = None
    approval_ref: Optional[str] = None
    approval_digest: Optional[str] = None
    action_digest: Optional[str] = None

    def canonical(self) -> Mapping[str, Any]:
        value: dict[str, Any] = {
            "status": self.status,
            "policy_digest": self.policy_digest,
        }
        for key in ("approval_id", "approval_ref", "approval_digest", "action_digest"):
            item = getattr(self, key)
            if item is not None:
                value[key] = item
        return value


@dataclass(frozen=True)
class MigrationBinding:
    migration_id: str
    source_schema_version: str
    source_receipt_digest: str
    reconciliation_evidence_digest: str

    def canonical(self) -> Mapping[str, Any]:
        return {
            "migration_id": self.migration_id,
            "source_schema_version": self.source_schema_version,
            "source_receipt_digest": self.source_receipt_digest,
            "reconciliation_evidence_digest": self.reconciliation_evidence_digest,
        }


@dataclass(frozen=True)
class ReceiptDraft:
    receipt_id: str
    installation_id: str
    leader_epoch: int
    task_id: str
    agent: str
    role: str
    work_item_id: str
    attempt_id: str
    dispatch_id: str
    dispatch_generation: int
    mode: ReceiptMode | str
    event_sequence: int
    expected_revision: int
    prior_receipt_digest: str
    event: str
    ts: str
    dispatch_ref: str
    payload_digest: str
    expected_refs: Tuple[str, ...]
    proof_bindings: Tuple[ProofBinding, ...]
    approval_binding: ApprovalBinding
    retry_generation: Optional[int] = None
    suspension_epoch: Optional[int] = None
    migration_binding: Optional[MigrationBinding] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "expected_refs", tuple(self.expected_refs))
        object.__setattr__(self, "proof_bindings", tuple(self.proof_bindings))


def receipt_subject_digest(draft: ReceiptDraft) -> str:
    mode = draft.mode.value if isinstance(draft.mode, ReceiptMode) else draft.mode
    return digest(
        {
            "protocol_version": PROTOCOL_VERSION,
            "receipt_id": draft.receipt_id,
            "installation_id": draft.installation_id,
            "leader_epoch": draft.leader_epoch,
            "task_id": draft.task_id,
            "agent": draft.agent,
            "role": draft.role,
            "work_item_id": draft.work_item_id,
            "attempt_id": draft.attempt_id,
            "dispatch_id": draft.dispatch_id,
            "dispatch_generation": draft.dispatch_generation,
            "retry_generation": draft.retry_generation,
            "mode": mode,
            "event_sequence": draft.event_sequence,
            "expected_revision": draft.expected_revision,
            "prior_receipt_digest": draft.prior_receipt_digest,
            "event": draft.event,
            "suspension_epoch": draft.suspension_epoch,
            "payload_digest": draft.payload_digest,
            "expected_refs": list(draft.expected_refs),
        }
    )


@dataclass(frozen=True)
class ProtocolReceipt:
    draft: ReceiptDraft
    ledger_revision: int
    receipt_digest: str

    def canonical_without_digest(self) -> Mapping[str, Any]:
        draft = self.draft
        mode = draft.mode.value if isinstance(draft.mode, ReceiptMode) else draft.mode
        value: dict[str, Any] = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "receipt_id": draft.receipt_id,
            "installation_id": draft.installation_id,
            "leader_epoch": draft.leader_epoch,
            "task_id": draft.task_id,
            "agent": draft.agent,
            "role": draft.role,
            "work_item_id": draft.work_item_id,
            "attempt_id": draft.attempt_id,
            "dispatch_id": draft.dispatch_id,
            "dispatch_generation": draft.dispatch_generation,
            "mode": mode,
            "event_sequence": draft.event_sequence,
            "ledger_revision": self.ledger_revision,
            "event": draft.event,
            "ts": draft.ts,
            "dispatch_ref": draft.dispatch_ref,
            "payload_digest": draft.payload_digest,
            "expected_refs": list(draft.expected_refs),
            "proof_bindings": [
                item.canonical()
                for item in sorted(
                    draft.proof_bindings,
                    key=lambda binding: canonical_json(binding.canonical()),
                )
            ],
            "approval_binding": draft.approval_binding.canonical(),
            "prior_receipt_digest": draft.prior_receipt_digest,
        }
        if draft.retry_generation is not None:
            value["retry_generation"] = draft.retry_generation
        if draft.suspension_epoch is not None:
            value["suspension_epoch"] = draft.suspension_epoch
        if draft.migration_binding is not None:
            value["migration_binding"] = draft.migration_binding.canonical()
        return value

    def canonical(self) -> Mapping[str, Any]:
        return {**self.canonical_without_digest(), "receipt_digest": self.receipt_digest}


@dataclass(frozen=True)
class ReceiptLedger:
    installation_id: str
    leader_epoch: int
    task_id: str
    revision: int = 0
    receipts: Tuple[ProtocolReceipt, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipts", tuple(self.receipts))

    @property
    def tail_digest(self) -> str:
        return self.receipts[-1].receipt_digest if self.receipts else EMPTY_RECEIPT_LEDGER_DIGEST


@dataclass(frozen=True)
class ReceiptWriteAccepted:
    ledger: ReceiptLedger
    receipt: ProtocolReceipt
    obligations: Tuple[str, ...]


@dataclass(frozen=True)
class ReceiptWriteNoOp:
    ledger: ReceiptLedger
    prior_receipt: ProtocolReceipt


@dataclass(frozen=True)
class ReceiptWriteRejected:
    ledger: ReceiptLedger
    error_code: str


@dataclass(frozen=True)
class ReceiptWriteResult:
    accepted: Optional[ReceiptWriteAccepted] = None
    no_op: Optional[ReceiptWriteNoOp] = None
    rejected: Optional[ReceiptWriteRejected] = None

    def __post_init__(self) -> None:
        if sum(item is not None for item in (self.accepted, self.no_op, self.rejected)) != 1:
            raise ValueError("ReceiptWriteResult must contain exactly one variant")

    @property
    def variant(self) -> ReceiptWriteVariant:
        if self.accepted is not None:
            return ReceiptWriteVariant.ACCEPTED
        if self.no_op is not None:
            return ReceiptWriteVariant.NO_OP
        return ReceiptWriteVariant.REJECTED


@dataclass(frozen=True)
class ReceiptMigrationResult:
    source_bytes: bytes
    source_digest: str
    write_result: ReceiptWriteResult


def _proof_binding_valid(binding: ProofBinding, subject_digest: str) -> bool:
    return (
        isinstance(binding, ProofBinding)
        and isinstance(binding.proof_kind, ReceiptProofKind)
        and _is_safe_ref(binding.proof_ref)
        and _is_digest(binding.proof_digest)
        and binding.subject_digest == subject_digest
    )


def _approval_binding_valid(binding: ApprovalBinding, subject_digest: str) -> bool:
    if not isinstance(binding, ApprovalBinding) or not _is_digest(binding.policy_digest):
        return False
    if binding.status == "not_required":
        return all(
            item is None
            for item in (
                binding.approval_id,
                binding.approval_ref,
                binding.approval_digest,
                binding.action_digest,
            )
        )
    return (
        binding.status == "granted"
        and isinstance(binding.approval_id, str)
        and bool(binding.approval_id)
        and _is_safe_ref(binding.approval_ref)
        and _is_digest(binding.approval_digest)
        and binding.action_digest == subject_digest
    )


def _receipt_valid(receipt: ProtocolReceipt) -> bool:
    if not isinstance(receipt, ProtocolReceipt):
        return False
    draft = receipt.draft
    subject = receipt_subject_digest(draft)
    if (
        not isinstance(draft.mode, ReceiptMode)
        or draft.event not in RECEIPT_EVENTS
        or draft.role not in ROLES
        or not all(isinstance(item, str) and item for item in (
            draft.receipt_id,
            draft.installation_id,
            draft.task_id,
            draft.agent,
            draft.work_item_id,
            draft.attempt_id,
            draft.dispatch_id,
            draft.ts,
        ))
        or not _is_int(draft.leader_epoch)
        or not _is_int(draft.dispatch_generation, 1)
        or not _is_int(draft.event_sequence, 1)
        or not _is_int(draft.expected_revision)
        or receipt.ledger_revision != draft.expected_revision + 1
        or not _is_digest(draft.prior_receipt_digest)
        or not _is_digest(draft.payload_digest)
        or not _is_safe_ref(draft.dispatch_ref)
        or not draft.proof_bindings
        or not all(_proof_binding_valid(item, subject) for item in draft.proof_bindings)
        or len({canonical_json(item.canonical()) for item in draft.proof_bindings}) != len(draft.proof_bindings)
        or not _approval_binding_valid(draft.approval_binding, subject)
        or not all(_is_safe_ref(item) for item in draft.expected_refs)
        or len(set(draft.expected_refs)) != len(draft.expected_refs)
        or (draft.retry_generation is not None and not _is_int(draft.retry_generation, 1))
        or (draft.event in TERMINAL_EVENTS) != (draft.suspension_epoch is not None)
        or (draft.suspension_epoch is not None and not _is_int(draft.suspension_epoch, 1))
    ):
        return False
    proof_kinds = {item.proof_kind for item in draft.proof_bindings}
    if draft.mode == ReceiptMode.MANUAL:
        manual_attestation = (
            draft.event in MANUAL_EVENTS
            and ReceiptProofKind.MANUAL_ATTESTED in proof_kinds
        )
        degraded_transport = (
            draft.event == "dispatch_inserted"
            and proof_kinds == {ReceiptProofKind.TRANSPORT_ONLY}
        )
        if not manual_attestation and not degraded_transport:
            return False
    elif draft.event in MANUAL_EVENTS:
        return False
    if draft.event in {"dispatch_submitted", "dispatch_completed"} and not {
        ReceiptProofKind.PROCESS_BOUND,
        ReceiptProofKind.CONTENT_BOUND,
    }.issubset(proof_kinds):
        return False
    if draft.event in {"dispatch_submitted", "dispatch_completed"}:
        required_proofs = [
            item
            for item in draft.proof_bindings
            if item.proof_kind
            in {ReceiptProofKind.PROCESS_BOUND, ReceiptProofKind.CONTENT_BOUND}
        ]
        if len({item.proof_ref for item in required_proofs}) != len(required_proofs):
            return False
        if len({item.proof_digest for item in required_proofs}) != len(required_proofs):
            return False
    if draft.event in {"dispatch_submitted", "dispatch_completed"} and ReceiptProofKind.TRANSPORT_ONLY in proof_kinds:
        return False
    if draft.migration_binding is not None:
        migration = draft.migration_binding
        if (
            not isinstance(migration.migration_id, str)
            or not migration.migration_id
            or migration.source_schema_version not in {"legacy", "valp-dispatch-receipt.v2"}
            or not _is_digest(migration.source_receipt_digest)
            or not _is_digest(migration.reconciliation_evidence_digest)
        ):
            return False
    return receipt.receipt_digest == digest(receipt.canonical_without_digest())


def _ledger_valid(ledger: ReceiptLedger) -> bool:
    if (
        not isinstance(ledger, ReceiptLedger)
        or not ledger.installation_id
        or not ledger.task_id
        or not _is_int(ledger.leader_epoch)
        or not _is_int(ledger.revision)
        or ledger.revision != len(ledger.receipts)
    ):
        return False
    prior = EMPTY_RECEIPT_LEDGER_DIGEST
    receipt_ids: set[str] = set()
    logical_keys: set[Tuple[Any, ...]] = set()
    migration_ids: set[str] = set()
    for index, receipt in enumerate(ledger.receipts, 1):
        draft = receipt.draft
        logical_key = _logical_key(draft)
        if (
            not _receipt_valid(receipt)
            or receipt.ledger_revision != index
            or draft.event_sequence != index
            or draft.prior_receipt_digest != prior
            or draft.installation_id != ledger.installation_id
            or draft.leader_epoch != ledger.leader_epoch
            or draft.task_id != ledger.task_id
            or draft.receipt_id in receipt_ids
            or logical_key in logical_keys
            or (
                draft.migration_binding is not None
                and draft.migration_binding.migration_id in migration_ids
            )
        ):
            return False
        receipt_ids.add(draft.receipt_id)
        logical_keys.add(logical_key)
        if draft.migration_binding is not None:
            migration_ids.add(draft.migration_binding.migration_id)
        prior = receipt.receipt_digest
    return True


def _logical_key(draft: ReceiptDraft) -> Tuple[Any, ...]:
    return (
        draft.task_id,
        draft.work_item_id,
        draft.attempt_id,
        draft.dispatch_id,
        draft.dispatch_generation,
        draft.retry_generation,
        draft.event,
    )


def _submission_identity(draft: ReceiptDraft) -> Tuple[Any, ...]:
    return (
        draft.installation_id,
        draft.leader_epoch,
        draft.task_id,
        draft.agent,
        draft.role,
        draft.work_item_id,
        draft.attempt_id,
        draft.dispatch_id,
        draft.dispatch_generation,
        draft.retry_generation,
        draft.mode,
        draft.dispatch_ref,
        draft.payload_digest,
        draft.expected_refs,
    )


def _make_receipt(draft: ReceiptDraft) -> ProtocolReceipt:
    receipt = ProtocolReceipt(draft=draft, ledger_revision=draft.expected_revision + 1, receipt_digest="")
    return replace(receipt, receipt_digest=digest(receipt.canonical_without_digest()))


def _reject(ledger: ReceiptLedger, error_code: str) -> ReceiptWriteResult:
    return ReceiptWriteResult(rejected=ReceiptWriteRejected(ledger=ledger, error_code=error_code))


def propose_receipt_append(ledger: ReceiptLedger, draft: ReceiptDraft) -> ReceiptWriteResult:
    """Validate one canonical receipt append without performing the append side effect."""

    if not _ledger_valid(ledger):
        return _reject(ledger, STATE_CONFLICT_ERROR)
    if (
        not isinstance(draft, ReceiptDraft)
        or not isinstance(draft.mode, ReceiptMode)
        or draft.event not in RECEIPT_EVENTS
        or not all(
            isinstance(item, ProofBinding)
            and isinstance(item.proof_kind, ReceiptProofKind)
            for item in draft.proof_bindings
        )
        or not isinstance(draft.approval_binding, ApprovalBinding)
        or (
            draft.migration_binding is not None
            and not isinstance(draft.migration_binding, MigrationBinding)
        )
    ):
        return _reject(ledger, MIGRATION_UNSUPPORTED_ERROR)
    candidate = _make_receipt(draft)
    prior_by_id = next(
        (item for item in ledger.receipts if item.draft.receipt_id == draft.receipt_id),
        None,
    )
    if prior_by_id is not None:
        if canonical_json(prior_by_id.canonical()) == canonical_json(candidate.canonical()):
            return ReceiptWriteResult(no_op=ReceiptWriteNoOp(ledger=ledger, prior_receipt=prior_by_id))
        return _reject(ledger, IDEMPOTENCY_CONFLICT_ERROR)
    if not _receipt_valid(candidate):
        return _reject(ledger, STATE_CONFLICT_ERROR)
    if any(_logical_key(item.draft) == _logical_key(draft) for item in ledger.receipts):
        return _reject(ledger, IDEMPOTENCY_CONFLICT_ERROR)
    if draft.migration_binding is not None and any(
        item.draft.migration_binding is not None
        and item.draft.migration_binding.migration_id == draft.migration_binding.migration_id
        for item in ledger.receipts
    ):
        return _reject(ledger, IDEMPOTENCY_CONFLICT_ERROR)
    if (
        draft.expected_revision != ledger.revision
        or draft.event_sequence != ledger.revision + 1
        or draft.prior_receipt_digest != ledger.tail_digest
        or draft.installation_id != ledger.installation_id
        or draft.leader_epoch != ledger.leader_epoch
        or draft.task_id != ledger.task_id
    ):
        return _reject(ledger, STATE_CONFLICT_ERROR)
    if draft.event == "dispatch_completed" and not any(
        item.draft.event == "dispatch_submitted"
        and _submission_identity(item.draft) == _submission_identity(draft)
        for item in ledger.receipts
    ):
        return _reject(ledger, STATE_CONFLICT_ERROR)

    next_ledger = ReceiptLedger(
        installation_id=ledger.installation_id,
        leader_epoch=ledger.leader_epoch,
        task_id=ledger.task_id,
        revision=ledger.revision + 1,
        receipts=ledger.receipts + (candidate,),
    )
    return ReceiptWriteResult(
        accepted=ReceiptWriteAccepted(
            ledger=next_ledger,
            receipt=candidate,
            obligations=(f"append_receipt:{candidate.receipt_digest}",),
        )
    )


def migrate_receipt(
    source_bytes: bytes,
    ledger: ReceiptLedger,
    draft: ReceiptDraft,
    reconciliation_evidence_digest: str,
    migration_id: str,
) -> ReceiptMigrationResult:
    """Project immutable legacy/v2 bytes into a separately bound v3 receipt."""

    if not isinstance(source_bytes, bytes):
        return ReceiptMigrationResult(source_bytes, "", _reject(ledger, MIGRATION_UNSUPPORTED_ERROR))
    source_digest = digest(source_bytes)
    if (
        not _is_digest(reconciliation_evidence_digest)
        or not isinstance(migration_id, str)
        or not migration_id
        or not isinstance(draft, ReceiptDraft)
    ):
        return ReceiptMigrationResult(source_bytes, source_digest, _reject(ledger, MIGRATION_UNSUPPORTED_ERROR))
    try:
        source = json.loads(source_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ReceiptMigrationResult(source_bytes, source_digest, _reject(ledger, MIGRATION_UNSUPPORTED_ERROR))
    if not isinstance(source, dict):
        return ReceiptMigrationResult(source_bytes, source_digest, _reject(ledger, MIGRATION_UNSUPPORTED_ERROR))
    schema_version = source.get("schema_version")
    if schema_version is None:
        source_schema = "legacy"
        if not _legacy_source_valid(source) or not _source_matches_draft(source, draft, legacy=True):
            return ReceiptMigrationResult(source_bytes, source_digest, _reject(ledger, MIGRATION_UNSUPPORTED_ERROR))
    elif schema_version == "valp-dispatch-receipt.v2":
        source_schema = schema_version
        if not _v2_source_valid(source, draft) or not _source_matches_draft(source, draft, legacy=False):
            return ReceiptMigrationResult(source_bytes, source_digest, _reject(ledger, MIGRATION_UNSUPPORTED_ERROR))
    else:
        return ReceiptMigrationResult(source_bytes, source_digest, _reject(ledger, MIGRATION_UNSUPPORTED_ERROR))
    projected = replace(
        draft,
        migration_binding=MigrationBinding(
            migration_id=migration_id,
            source_schema_version=source_schema,
            source_receipt_digest=source_digest,
            reconciliation_evidence_digest=reconciliation_evidence_digest,
        ),
    )
    return ReceiptMigrationResult(source_bytes, source_digest, propose_receipt_append(ledger, projected))


def _refs_valid(value: Any) -> bool:
    return (
        isinstance(value, list)
        and all(_is_safe_ref(item) for item in value)
        and len(set(value)) == len(value)
    )


def _legacy_source_valid(source: Mapping[str, Any]) -> bool:
    required = {"ts", "agent", "event", "dispatch_ref"}
    allowed = required | {
        "role", "exit_code", "summary", "expected_refs", "proof", "runtime",
    }
    if not required.issubset(source) or not set(source).issubset(allowed):
        return False
    if (
        not isinstance(source["ts"], str)
        or not source["ts"]
        or not isinstance(source["agent"], str)
        or not source["agent"]
        or source["event"] not in RECEIPT_EVENTS
        or not _is_safe_ref(source["dispatch_ref"])
        or ("role" in source and source["role"] not in ROLES)
        or ("expected_refs" in source and not _refs_valid(source["expected_refs"]))
        or ("proof" in source and not isinstance(source["proof"], dict))
        or ("runtime" in source and not isinstance(source["runtime"], dict))
        or ("summary" in source and not isinstance(source["summary"], str))
        or (
            "exit_code" in source
            and (
                not isinstance(source["exit_code"], int)
                or isinstance(source["exit_code"], bool)
            )
        )
    ):
        return False
    return True


def _v2_source_valid(source: Mapping[str, Any], draft: ReceiptDraft) -> bool:
    required = {
        "schema_version", "receipt_id", "task_id", "event_sequence", "ts",
        "agent", "role", "work_item_id", "dispatch_id", "dispatch_generation",
        "event", "dispatch_ref", "expected_refs",
    }
    allowed = required | {
        "retry_generation", "suspension_epoch", "exit_code", "summary", "proof", "runtime",
    }
    if not required.issubset(source) or not set(source).issubset(allowed):
        return False
    if (
        source.get("schema_version") != "valp-dispatch-receipt.v2"
        or not all(isinstance(source.get(key), str) and source[key] for key in (
            "receipt_id", "task_id", "ts", "agent", "work_item_id", "dispatch_id",
        ))
        or source.get("role") not in ROLES
        or source.get("event") not in RECEIPT_EVENTS
        or source.get("event") in TERMINAL_EVENTS
        or not _is_int(source.get("event_sequence"), 1)
        or not _is_int(source.get("dispatch_generation"), 1)
        or not _is_safe_ref(source.get("dispatch_ref"))
        or not _refs_valid(source.get("expected_refs"))
        or ("retry_generation" in source and not _is_int(source["retry_generation"], 1))
        or (source["event"] in TERMINAL_EVENTS) != ("suspension_epoch" in source)
        or ("suspension_epoch" in source and not _is_int(source["suspension_epoch"], 1))
        or (source["event"] == "dispatch_submitted" and not isinstance(source.get("proof"), dict))
        or ("proof" in source and not isinstance(source["proof"], dict))
        or ("runtime" in source and not isinstance(source["runtime"], dict))
        or ("summary" in source and not isinstance(source["summary"], str))
        or (
            "exit_code" in source
            and (
                not isinstance(source["exit_code"], int)
                or isinstance(source["exit_code"], bool)
            )
        )
    ):
        return False
    return source.get("event") != "dispatch_submitted" or _v2_submission_proof_valid(
        source["proof"], source, draft
    )


def _v2_submission_proof_valid(
    proof: Mapping[str, Any], source: Mapping[str, Any], draft: ReceiptDraft
) -> bool:
    if not isinstance(proof, dict) or set(proof) != {"adapter_record"}:
        return False
    record = proof.get("adapter_record")
    if not isinstance(record, dict) or set(record) != V2_SUBMISSION_PROOF_FIELDS:
        return False
    if (
        not isinstance(record["submission_id"], str)
        or not record["submission_id"].strip()
        or not _is_digest(record["payload_digest"])
        or record["payload_digest"] != draft.payload_digest
        or record["acknowledged"] is not True
    ):
        return False
    expected = {
        "receipt_id": source["receipt_id"],
        "task_id": source["task_id"],
        "agent": source["agent"],
        "role": source["role"],
        "work_item_id": source["work_item_id"],
        "dispatch_id": source["dispatch_id"],
        "dispatch_generation": source["dispatch_generation"],
        "event_sequence": source["event_sequence"],
        "dispatch_ref": source["dispatch_ref"],
        "expected_refs": source["expected_refs"],
    }
    projected = {
        "receipt_id": draft.receipt_id,
        "task_id": draft.task_id,
        "agent": draft.agent,
        "role": draft.role,
        "work_item_id": draft.work_item_id,
        "dispatch_id": draft.dispatch_id,
        "dispatch_generation": draft.dispatch_generation,
        "event_sequence": draft.event_sequence,
        "dispatch_ref": draft.dispatch_ref,
        "expected_refs": list(draft.expected_refs),
    }
    return all(record[key] == value for key, value in expected.items()) and all(
        record[key] == value for key, value in projected.items()
    )


def _source_matches_draft(
    source: Mapping[str, Any], draft: ReceiptDraft, *, legacy: bool
) -> bool:
    shared = {
        "ts": draft.ts,
        "agent": draft.agent,
        "event": draft.event,
        "dispatch_ref": draft.dispatch_ref,
    }
    if not legacy:
        shared.update(
            {
                "receipt_id": draft.receipt_id,
                "task_id": draft.task_id,
                "role": draft.role,
                "work_item_id": draft.work_item_id,
                "dispatch_id": draft.dispatch_id,
                "dispatch_generation": draft.dispatch_generation,
                "expected_refs": list(draft.expected_refs),
            }
        )
    else:
        if "role" in source:
            shared["role"] = draft.role
        if "expected_refs" in source:
            shared["expected_refs"] = list(draft.expected_refs)
    if "retry_generation" in source:
        shared["retry_generation"] = draft.retry_generation
    if "suspension_epoch" in source:
        shared["suspension_epoch"] = draft.suspension_epoch
    return all(source.get(key) == value for key, value in shared.items())
