"""Crash-bounded durable storage for canonical Protocol 0.3 receipts."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
from typing import Any, Iterator, Mapping, Sequence

from .protocol_receipts import (
    ApprovalBinding,
    MigrationBinding,
    ProofBinding,
    ProtocolReceipt,
    RECEIPT_SCHEMA_VERSION,
    ReceiptDraft,
    ReceiptLedger,
    ReceiptMode,
    ReceiptProofKind,
    ReceiptWriteAccepted,
    ReceiptWriteResult,
    canonical_json,
    propose_receipt_append,
)


LEDGER_CORRUPT_ERROR = "VALP-E-RECEIPT-LEDGER-CORRUPT"
OBLIGATION_ERROR = "VALP-E-RECEIPT-OBLIGATION"
LOCK_TIMEOUT_ERROR = "VALP-E-LOCK-TIMEOUT"
LOCK_UNAVAILABLE_ERROR = "VALP-E-LOCK-UNAVAILABLE"
DURABILITY_PRECOMMIT_ERROR = "VALP-E-DURABILITY-PRECOMMIT"
DURABILITY_UNKNOWN_OR_COMMITTED_ERROR = "VALP-E-DURABILITY-UNKNOWN-OR-COMMITTED"
# Short alias retained for callers that adopted the initial MVP-D tracer API.
DURABILITY_UNKNOWN_ERROR = DURABILITY_UNKNOWN_OR_COMMITTED_ERROR

REJECTED_OUTCOME = "rejected"
UNKNOWN_OR_COMMITTED_OUTCOME = "unknown_or_committed"

_LOCK_RETRY_SECONDS = 0.01
_WINDOWS_REPLACE_RETRY_SECONDS = 0.01
_WINDOWS_REPLACE_TIMEOUT_SECONDS = 1.0
_DIRECTORY_SYNC_UNSUPPORTED = {
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
}


class ReceiptStoreError(RuntimeError):
    """A classified store failure whose commit outcome is explicit."""

    def __init__(self, code: str, message: str, *, outcome: str = REJECTED_OUTCOME):
        super().__init__(message)
        self.code = code
        self.outcome = outcome


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _require_keys(
    value: Any,
    required: set[str],
    optional: set[str] = set(),
) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not required.issubset(value) or not set(value).issubset(required | optional):
        raise ValueError("unexpected receipt object shape")
    return value


def _decode_binding(value: Any) -> ProofBinding:
    item = _require_keys(value, {"proof_kind", "proof_ref", "proof_digest", "subject_digest"})
    return ProofBinding(
        proof_kind=ReceiptProofKind(item["proof_kind"]),
        proof_ref=item["proof_ref"],
        proof_digest=item["proof_digest"],
        subject_digest=item["subject_digest"],
    )


def _decode_approval(value: Any) -> ApprovalBinding:
    item = _require_keys(
        value,
        {"status", "policy_digest"},
        {"approval_id", "approval_ref", "approval_digest", "action_digest"},
    )
    return ApprovalBinding(
        status=item["status"],
        policy_digest=item["policy_digest"],
        approval_id=item.get("approval_id"),
        approval_ref=item.get("approval_ref"),
        approval_digest=item.get("approval_digest"),
        action_digest=item.get("action_digest"),
    )


def _decode_migration(value: Any) -> MigrationBinding:
    item = _require_keys(
        value,
        {
            "migration_id",
            "source_schema_version",
            "source_receipt_digest",
            "reconciliation_evidence_digest",
        },
    )
    return MigrationBinding(
        migration_id=item["migration_id"],
        source_schema_version=item["source_schema_version"],
        source_receipt_digest=item["source_receipt_digest"],
        reconciliation_evidence_digest=item["reconciliation_evidence_digest"],
    )


def _decode_receipt(value: Any) -> ProtocolReceipt:
    required = {
        "schema_version",
        "protocol_version",
        "receipt_id",
        "installation_id",
        "leader_epoch",
        "task_id",
        "agent",
        "role",
        "work_item_id",
        "attempt_id",
        "dispatch_id",
        "dispatch_generation",
        "mode",
        "event_sequence",
        "ledger_revision",
        "event",
        "ts",
        "dispatch_ref",
        "payload_digest",
        "expected_refs",
        "proof_bindings",
        "approval_binding",
        "prior_receipt_digest",
        "receipt_digest",
    }
    item = _require_keys(
        value,
        required,
        {"retry_generation", "suspension_epoch", "migration_binding"},
    )
    if item["schema_version"] != RECEIPT_SCHEMA_VERSION:
        raise ValueError("unsupported receipt schema")
    if not isinstance(item["expected_refs"], list) or not isinstance(item["proof_bindings"], list):
        raise ValueError("receipt arrays have invalid types")
    draft = ReceiptDraft(
        receipt_id=item["receipt_id"],
        installation_id=item["installation_id"],
        leader_epoch=item["leader_epoch"],
        task_id=item["task_id"],
        agent=item["agent"],
        role=item["role"],
        work_item_id=item["work_item_id"],
        attempt_id=item["attempt_id"],
        dispatch_id=item["dispatch_id"],
        dispatch_generation=item["dispatch_generation"],
        mode=ReceiptMode(item["mode"]),
        event_sequence=item["event_sequence"],
        expected_revision=item["ledger_revision"] - 1,
        prior_receipt_digest=item["prior_receipt_digest"],
        event=item["event"],
        ts=item["ts"],
        dispatch_ref=item["dispatch_ref"],
        payload_digest=item["payload_digest"],
        expected_refs=tuple(item["expected_refs"]),
        proof_bindings=tuple(_decode_binding(binding) for binding in item["proof_bindings"]),
        approval_binding=_decode_approval(item["approval_binding"]),
        retry_generation=item.get("retry_generation"),
        suspension_epoch=item.get("suspension_epoch"),
        migration_binding=(
            _decode_migration(item["migration_binding"])
            if "migration_binding" in item
            else None
        ),
    )
    return ProtocolReceipt(
        draft=draft,
        ledger_revision=item["ledger_revision"],
        receipt_digest=item["receipt_digest"],
    )


def _sync_directory(directory: Path) -> bool:
    if os.name == "nt":  # Windows has no portable directory-fsync primitive.
        return False
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError as exc:
        if exc.errno in _DIRECTORY_SYNC_UNSUPPORTED:
            return False
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno in _DIRECTORY_SYNC_UNSUPPORTED:
                return False
            raise
    finally:
        os.close(descriptor)
    return True


def _replace_file(source: str, target: Path) -> None:
    deadline = time.monotonic() + _WINDOWS_REPLACE_TIMEOUT_SECONDS
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32} or time.monotonic() >= deadline:
                raise
            time.sleep(_WINDOWS_REPLACE_RETRY_SECONDS)


class ReceiptStore:
    """Strict JSONL receipt ledger with one locked durable append transaction."""

    def __init__(
        self,
        path: Path,
        installation_id: str,
        leader_epoch: int,
        task_id: str,
        *,
        lock_timeout: float = 30.0,
    ) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.installation_id = installation_id
        self.leader_epoch = leader_epoch
        self.task_id = task_id
        self.lock_timeout = lock_timeout
        self.directory_sync_supported: bool | None = None

    def _empty_ledger(self) -> ReceiptLedger:
        return ReceiptLedger(
            installation_id=self.installation_id,
            leader_epoch=self.leader_epoch,
            task_id=self.task_id,
        )

    def _ensure_regular(self, path: Path) -> None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise ReceiptStoreError(LEDGER_CORRUPT_ERROR, f"non-regular store path: {path}")

    @contextmanager
    def _locked(self) -> Iterator[dict[str, bool]]:
        lock_state = {"replaced": False}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_regular(self.path)
            self._ensure_regular(self.lock_path)
            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.lock_path, flags, 0o600)
            handle = os.fdopen(descriptor, "r+b", closefd=True)
        except ReceiptStoreError:
            raise
        except OSError as exc:
            raise ReceiptStoreError(LOCK_UNAVAILABLE_ERROR, f"cannot open receipt lock: {exc}") from exc
        with handle:
            if os.fstat(handle.fileno()).st_size == 0:
                try:
                    handle.write(b"\0")
                    handle.flush()
                    os.fsync(handle.fileno())
                except OSError as exc:
                    raise ReceiptStoreError(
                        DURABILITY_PRECOMMIT_ERROR,
                        f"receipt lock initialization failed before replacement: {exc}",
                    ) from exc
            deadline = time.monotonic() + max(0.0, self.lock_timeout)
            while True:
                try:
                    if os.name == "nt":  # pragma: no cover - Windows only.
                        import msvcrt

                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (ImportError, AttributeError) as exc:
                    raise ReceiptStoreError(LOCK_UNAVAILABLE_ERROR, "no supported file lock") from exc
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, getattr(errno, "EDEADLK", errno.EAGAIN)}:
                        raise ReceiptStoreError(LOCK_UNAVAILABLE_ERROR, f"cannot lock receipt store: {exc}") from exc
                    if time.monotonic() >= deadline:
                        raise ReceiptStoreError(LOCK_TIMEOUT_ERROR, "receipt store lock timed out") from exc
                    time.sleep(_LOCK_RETRY_SECONDS)
            try:
                yield lock_state
            finally:
                body_failed = sys.exc_info()[0] is not None
                try:
                    handle.seek(0)
                    if os.name == "nt":  # pragma: no cover - Windows only.
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError as exc:
                    if body_failed:
                        pass
                    elif lock_state["replaced"]:
                        raise ReceiptStoreError(
                            DURABILITY_UNKNOWN_ERROR,
                            f"receipt replaced but lock release failed: {exc}",
                            outcome=UNKNOWN_OR_COMMITTED_OUTCOME,
                        ) from exc
                    else:
                        raise ReceiptStoreError(
                            LOCK_UNAVAILABLE_ERROR,
                            f"receipt lock release failed: {exc}",
                        ) from exc

    def _load_unlocked(self) -> ReceiptLedger:
        self._ensure_regular(self.path)
        if not self.path.exists():
            return self._empty_ledger()
        try:
            payload = self.path.read_bytes()
            if payload and (not payload.endswith(b"\n") or b"\r" in payload or payload.startswith(b"\xef\xbb\xbf")):
                raise ValueError("ledger is not canonical LF-terminated UTF-8")
            receipts: list[ProtocolReceipt] = []
            for line_number, line in enumerate(payload.splitlines(keepends=True), 1):
                if line == b"\n":
                    raise ValueError(f"blank ledger line {line_number}")
                try:
                    value = json.loads(
                        line[:-1].decode("utf-8"),
                        object_pairs_hook=_reject_duplicate_keys,
                        parse_constant=lambda constant: (_ for _ in ()).throw(
                            ValueError(f"invalid JSON constant: {constant}")
                        ),
                    )
                    receipt = _decode_receipt(value)
                except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
                    raise ValueError(f"invalid receipt ledger line {line_number}: {exc}") from exc
                if line != canonical_json(receipt.canonical()).encode("utf-8"):
                    raise ValueError(f"noncanonical receipt ledger line {line_number}")
                receipts.append(receipt)
            ledger = ReceiptLedger(
                installation_id=self.installation_id,
                leader_epoch=self.leader_epoch,
                task_id=self.task_id,
                revision=len(receipts),
                receipts=tuple(receipts),
            )
            # The reducer validates the complete prefix before considering any draft.
            if receipts:
                probe = propose_receipt_append(ledger, receipts[-1].draft)
                if probe.rejected is not None and probe.rejected.error_code != "VALP-E-IDEMPOTENCY-CONFLICT":
                    raise ValueError("receipt ledger invariants failed")
                if probe.no_op is None:
                    raise ValueError("receipt ledger invariants failed")
            return ledger
        except ReceiptStoreError:
            raise
        except (OSError, ValueError) as exc:
            raise ReceiptStoreError(LEDGER_CORRUPT_ERROR, f"cannot load receipt ledger: {exc}") from exc

    def load(self) -> ReceiptLedger:
        with self._locked():
            return self._load_unlocked()

    def probe_directory_sync(self) -> bool:
        """Probe and expose parent-directory fsync support for this store."""

        try:
            supported = _sync_directory(self.path.parent)
        except OSError as exc:
            raise ReceiptStoreError(
                DURABILITY_PRECOMMIT_ERROR,
                f"cannot probe receipt directory sync support: {exc}",
            ) from exc
        self.directory_sync_supported = supported
        return supported

    def _validate_obligation(self, accepted: ReceiptWriteAccepted) -> str:
        if not isinstance(accepted, ReceiptWriteAccepted):
            raise ReceiptStoreError(OBLIGATION_ERROR, "store requires an accepted receipt write")
        if not isinstance(accepted.receipt, ProtocolReceipt) or not isinstance(accepted.ledger, ReceiptLedger):
            raise ReceiptStoreError(OBLIGATION_ERROR, "accepted receipt write has invalid types")
        expected = f"append_receipt:{accepted.receipt.receipt_digest}"
        if accepted.obligations != (expected,):
            raise ReceiptStoreError(OBLIGATION_ERROR, "append obligation does not match receipt digest")
        ledger = accepted.ledger
        if (
            not ledger.receipts
            or ledger.revision != accepted.receipt.ledger_revision
            or ledger.receipts[-1] != accepted.receipt
        ):
            raise ReceiptStoreError(OBLIGATION_ERROR, "accepted ledger does not end at obligation receipt")
        prefix = ReceiptLedger(
            installation_id=ledger.installation_id,
            leader_epoch=ledger.leader_epoch,
            task_id=ledger.task_id,
            revision=ledger.revision - 1,
            receipts=ledger.receipts[:-1],
        )
        replayed = propose_receipt_append(prefix, accepted.receipt.draft)
        if replayed.accepted != accepted:
            raise ReceiptStoreError(OBLIGATION_ERROR, "accepted ledger snapshot is not reproducible")
        return expected

    @staticmethod
    def _ledger_bytes(ledger: ReceiptLedger) -> bytes:
        return b"".join(
            canonical_json(receipt.canonical()).encode("utf-8")
            for receipt in ledger.receipts
        )

    def append(self, accepted: ReceiptWriteAccepted) -> ReceiptWriteResult:
        obligation = self._validate_obligation(accepted)
        replaced = False
        temporary_path: str | None = None
        with self._locked() as lock_state:
            durable = self._load_unlocked()
            result = propose_receipt_append(durable, accepted.receipt.draft)
            if result.no_op is not None:
                if (
                    canonical_json(result.no_op.prior_receipt.canonical())
                    != canonical_json(accepted.receipt.canonical())
                ):
                    raise ReceiptStoreError(OBLIGATION_ERROR, "existing receipt differs from accepted obligation")
                return result
            if result.rejected is not None:
                return result
            assert result.accepted is not None
            if (
                result.accepted.obligations != (obligation,)
                or canonical_json(result.accepted.receipt.canonical())
                != canonical_json(accepted.receipt.canonical())
                or result.accepted.ledger != accepted.ledger
            ):
                raise ReceiptStoreError(OBLIGATION_ERROR, "replayed receipt differs from accepted obligation")
            payload = self._ledger_bytes(result.accepted.ledger)
            try:
                descriptor, temporary_path = tempfile.mkstemp(
                    prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
                )
                with os.fdopen(descriptor, "wb", closefd=True) as temporary:
                    temporary.write(payload)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                self._ensure_regular(self.path)
                _replace_file(temporary_path, self.path)
                replaced = True
                lock_state["replaced"] = True
                temporary_path = None
                try:
                    self.directory_sync_supported = _sync_directory(self.path.parent)
                except OSError as exc:
                    raise ReceiptStoreError(
                        DURABILITY_UNKNOWN_ERROR,
                        f"receipt replaced but directory sync failed: {exc}",
                        outcome=UNKNOWN_OR_COMMITTED_OUTCOME,
                    ) from exc
                return result
            except ReceiptStoreError:
                raise
            except OSError as exc:
                if replaced:
                    raise ReceiptStoreError(
                        DURABILITY_UNKNOWN_ERROR,
                        f"receipt replacement outcome requires reconciliation: {exc}",
                        outcome=UNKNOWN_OR_COMMITTED_OUTCOME,
                    ) from exc
                raise ReceiptStoreError(
                    DURABILITY_PRECOMMIT_ERROR,
                    f"receipt append failed before replacement: {exc}",
                ) from exc
            finally:
                if temporary_path is not None:
                    try:
                        os.unlink(temporary_path)
                    except OSError:
                        pass


__all__ = [
    "DURABILITY_PRECOMMIT_ERROR",
    "DURABILITY_UNKNOWN_OR_COMMITTED_ERROR",
    "DURABILITY_UNKNOWN_ERROR",
    "LEDGER_CORRUPT_ERROR",
    "LOCK_TIMEOUT_ERROR",
    "LOCK_UNAVAILABLE_ERROR",
    "OBLIGATION_ERROR",
    "REJECTED_OUTCOME",
    "ReceiptStore",
    "ReceiptStoreError",
    "UNKNOWN_OR_COMMITTED_OUTCOME",
]
