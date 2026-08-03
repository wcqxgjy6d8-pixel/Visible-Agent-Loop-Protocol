from __future__ import annotations

from dataclasses import replace
import errno
import fcntl
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from valp_cli.protocol_receipts import (
    ApprovalBinding,
    IDEMPOTENCY_CONFLICT_ERROR,
    ProofBinding,
    ReceiptDraft,
    ReceiptLedger,
    ReceiptMode,
    ReceiptProofKind,
    ReceiptWriteAccepted,
    ReceiptWriteVariant,
    STATE_CONFLICT_ERROR,
    canonical_json,
    propose_receipt_append,
    receipt_subject_digest,
)
from valp_cli.receipt_store import (
    DURABILITY_PRECOMMIT_ERROR,
    DURABILITY_UNKNOWN_ERROR,
    LEDGER_CORRUPT_ERROR,
    LOCK_TIMEOUT_ERROR,
    LOCK_UNAVAILABLE_ERROR,
    OBLIGATION_ERROR,
    ReceiptStore,
    ReceiptStoreError,
)


GOOD_DIGEST = "sha256:" + "1" * 64
INSTALLATION_ID = "installation-store-1"
LEADER_EPOCH = 7
TASK_ID = "TASK-RECEIPT-STORE"


def make_accepted(ledger: ReceiptLedger, receipt_id: str, *, payload_digest: str = GOOD_DIGEST):
    sequence = ledger.revision + 1
    draft = ReceiptDraft(
        receipt_id=receipt_id,
        installation_id=ledger.installation_id,
        leader_epoch=ledger.leader_epoch,
        task_id=ledger.task_id,
        agent="codex",
        role="implementer",
        work_item_id=f"implementer:{receipt_id}",
        attempt_id=f"attempt:{receipt_id}",
        dispatch_id=f"dispatch:{receipt_id}",
        dispatch_generation=1,
        mode=ReceiptMode.MANUAL,
        event_sequence=sequence,
        expected_revision=ledger.revision,
        prior_receipt_digest=ledger.tail_digest,
        event="manual_dispatch_written",
        ts=f"2026-08-03T10:00:{sequence:02d}Z",
        dispatch_ref=f"agents/codex/{receipt_id}.md",
        payload_digest=payload_digest,
        expected_refs=(f"agents/codex/{receipt_id}-evidence.md",),
        proof_bindings=(),
        approval_binding=ApprovalBinding("not_required", GOOD_DIGEST),
    )
    subject = receipt_subject_digest(draft)
    draft = replace(
        draft,
        proof_bindings=(
            ProofBinding(
                ReceiptProofKind.MANUAL_ATTESTED,
                f"evidence/{receipt_id}.json",
                GOOD_DIGEST,
                subject,
            ),
        ),
    )
    result = propose_receipt_append(ledger, draft)
    if result.accepted is None:
        raise AssertionError(f"test fixture was not accepted: {result}")
    return result.accepted


def _contention_worker(path: str, worker: int, start, output) -> None:
    store = ReceiptStore(Path(path), INSTALLATION_ID, LEADER_EPOCH, TASK_ID)
    start.wait()
    for _attempt in range(100):
        ledger = store.load()
        accepted = make_accepted(ledger, f"receipt-worker-{worker}")
        result = store.append(accepted)
        if result.variant == ReceiptWriteVariant.ACCEPTED:
            output.put((worker, "accepted"))
            return
        if result.rejected is None or result.rejected.error_code != STATE_CONFLICT_ERROR:
            output.put((worker, f"unexpected:{result}"))
            return
    output.put((worker, "retry_exhausted"))


def _hold_lock(lock_path: str, ready, release) -> None:
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        ready.set()
        release.wait(10)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ReceiptStoreTests(unittest.TestCase):
    def store(self, path: Path, *, lock_timeout: float = 30.0) -> ReceiptStore:
        return ReceiptStore(
            path,
            INSTALLATION_ID,
            LEADER_EPOCH,
            TASK_ID,
            lock_timeout=lock_timeout,
        )

    def empty_ledger(self) -> ReceiptLedger:
        return ReceiptLedger(INSTALLATION_ID, LEADER_EPOCH, TASK_ID)

    def test_empty_file_first_append_writes_exact_canonical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            path.write_bytes(b"")
            store = self.store(path)
            accepted = make_accepted(self.empty_ledger(), "receipt-1")

            result = store.append(accepted)

            self.assertEqual(result.variant, ReceiptWriteVariant.ACCEPTED)
            self.assertEqual(
                path.read_bytes(),
                canonical_json(accepted.receipt.canonical()).encode("utf-8"),
            )
            self.assertEqual(store.load(), accepted.ledger)
            self.assertIs(store.directory_sync_supported, True)

    def test_exact_retry_is_noop_and_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            store = self.store(path)
            accepted = make_accepted(self.empty_ledger(), "receipt-1")
            store.append(accepted)
            before = path.read_bytes()

            retry = store.append(accepted)

            self.assertEqual(retry.variant, ReceiptWriteVariant.NO_OP)
            self.assertEqual(path.read_bytes(), before)

    def test_exact_retry_after_later_append_is_noop_and_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            store = self.store(path)
            first = make_accepted(self.empty_ledger(), "receipt-1")
            store.append(first)
            second = make_accepted(store.load(), "receipt-2")
            store.append(second)
            before = path.read_bytes()

            retry = store.append(first)

            self.assertEqual(retry.variant, ReceiptWriteVariant.NO_OP)
            self.assertEqual(retry.no_op.prior_receipt, first.receipt)
            self.assertEqual(retry.no_op.ledger.revision, 2)
            self.assertEqual(path.read_bytes(), before)

    def test_changed_same_id_after_later_append_conflicts_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            store = self.store(path)
            first = make_accepted(self.empty_ledger(), "receipt-1")
            changed = make_accepted(
                self.empty_ledger(),
                "receipt-1",
                payload_digest="sha256:" + "2" * 64,
            )
            store.append(first)
            store.append(make_accepted(store.load(), "receipt-2"))
            before = path.read_bytes()

            conflict = store.append(changed)

            self.assertEqual(conflict.rejected.error_code, IDEMPOTENCY_CONFLICT_ERROR)
            self.assertEqual(path.read_bytes(), before)

    def test_same_id_changed_content_conflicts_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            store = self.store(path)
            first = make_accepted(self.empty_ledger(), "receipt-1")
            changed = make_accepted(
                self.empty_ledger(),
                "receipt-1",
                payload_digest="sha256:" + "2" * 64,
            )
            store.append(first)
            before = path.read_bytes()

            conflict = store.append(changed)

            self.assertEqual(conflict.rejected.error_code, IDEMPOTENCY_CONFLICT_ERROR)
            self.assertEqual(path.read_bytes(), before)

    def test_stale_cas_race_commits_one_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            store = self.store(path)
            first = make_accepted(self.empty_ledger(), "receipt-first")
            stale = make_accepted(self.empty_ledger(), "receipt-stale")
            store.append(first)
            before = path.read_bytes()

            loser = store.append(stale)

            self.assertEqual(loser.rejected.error_code, STATE_CONFLICT_ERROR)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(store.load().receipts, (first.receipt,))

    def test_noncanonical_or_corrupt_ledgers_fail_closed_byte_identical(self) -> None:
        accepted = make_accepted(self.empty_ledger(), "receipt-1")
        canonical = canonical_json(accepted.receipt.canonical()).encode("utf-8")
        parsed = json.loads(canonical)
        tampered = {**parsed, "receipt_digest": "sha256:" + "0" * 64}
        unsorted = json.dumps(parsed, ensure_ascii=False).encode("utf-8") + b"\n"
        duplicate_key = b'{"schema_version":"valp-dispatch-receipt.v3",' + canonical[1:]
        cases = {
            "bom": b"\xef\xbb\xbf" + canonical,
            "crlf": canonical[:-1] + b"\r\n",
            "blank-line": canonical + b"\n",
            "missing-final-lf": canonical[:-1],
            "duplicate-key": duplicate_key,
            "nan": b'{"schema_version":"valp-dispatch-receipt.v3","value":NaN}\n',
            "key-order-or-spacing": unsorted,
            "invalid-utf8": b"\xff\n",
            "non-v3": b'{"schema_version":"valp-dispatch-receipt.v2"}\n',
            "tampered-digest": canonical_json(tampered).encode("utf-8"),
            "truncated": canonical[: len(canonical) // 2],
        }
        with tempfile.TemporaryDirectory() as tmp:
            for name, contents in cases.items():
                with self.subTest(name=name):
                    path = Path(tmp) / f"{name}.jsonl"
                    path.write_bytes(contents)
                    before = path.read_bytes()

                    with self.assertRaises(ReceiptStoreError) as raised:
                        self.store(path).load()

                    self.assertEqual(raised.exception.code, LEDGER_CORRUPT_ERROR)
                    self.assertEqual(raised.exception.outcome, "rejected")
                    self.assertEqual(path.read_bytes(), before)

    def test_multiprocess_contention_produces_contiguous_digest_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            context = multiprocessing.get_context("spawn")
            start = context.Event()
            output = context.Queue()
            processes = [
                context.Process(target=_contention_worker, args=(str(path), index, start, output))
                for index in range(6)
            ]
            for process in processes:
                process.start()
            start.set()
            results = [output.get(timeout=20) for _process in processes]
            for process in processes:
                process.join(timeout=20)

            self.assertEqual([process.exitcode for process in processes], [0] * len(processes))
            self.assertEqual({status for _worker, status in results}, {"accepted"})
            ledger = self.store(path).load()
            self.assertEqual(ledger.revision, len(processes))
            self.assertEqual(
                [receipt.ledger_revision for receipt in ledger.receipts],
                list(range(1, len(processes) + 1)),
            )
            self.assertEqual(
                [receipt.draft.event_sequence for receipt in ledger.receipts],
                list(range(1, len(processes) + 1)),
            )
            prior = self.empty_ledger().tail_digest
            for receipt in ledger.receipts:
                self.assertEqual(receipt.draft.prior_receipt_digest, prior)
                prior = receipt.receipt_digest

    def test_precommit_fsync_or_replace_failure_leaves_bytes_unchanged(self) -> None:
        for target in ("os.fsync", "os.replace"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "dispatch-receipts.jsonl"
                path.write_bytes(b"")
                before = path.read_bytes()
                accepted = make_accepted(self.empty_ledger(), "receipt-1")
                store = self.store(path)
                store.load()  # Initialize and sync the stable lock before fault injection.
                with patch(
                    f"valp_cli.receipt_store.{target}",
                    side_effect=OSError(errno.EIO, "injected durability failure"),
                ):
                    with self.assertRaises(ReceiptStoreError) as raised:
                        store.append(accepted)

                self.assertEqual(raised.exception.code, DURABILITY_PRECOMMIT_ERROR)
                self.assertEqual(raised.exception.outcome, "rejected")
                self.assertEqual(path.read_bytes(), before)

    def test_unlock_failure_after_replace_is_unknown_or_committed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            store = self.store(path)
            store.load()
            accepted = make_accepted(self.empty_ledger(), "receipt-1")
            with patch(
                "fcntl.flock",
                side_effect=[None, OSError(errno.EIO, "injected unlock failure")],
            ):
                with self.assertRaises(ReceiptStoreError) as raised:
                    store.append(accepted)

            self.assertEqual(raised.exception.code, DURABILITY_UNKNOWN_ERROR)
            self.assertEqual(raised.exception.outcome, "unknown_or_committed")
            self.assertEqual(
                path.read_bytes(),
                canonical_json(accepted.receipt.canonical()).encode("utf-8"),
            )

    def test_directory_sync_capability_can_be_probed_before_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            accepted = make_accepted(self.empty_ledger(), "receipt-1")
            self.store(path).append(accepted)
            store = self.store(path)

            self.assertIs(store.probe_directory_sync(), True)
            self.assertIs(store.directory_sync_supported, True)
            self.assertEqual(store.append(accepted).variant, ReceiptWriteVariant.NO_OP)

    def test_directory_fsync_after_replace_is_unknown_or_committed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            accepted = make_accepted(self.empty_ledger(), "receipt-1")
            with patch(
                "valp_cli.receipt_store._sync_directory",
                side_effect=OSError(errno.EIO, "injected directory sync failure"),
            ):
                with self.assertRaises(ReceiptStoreError) as raised:
                    self.store(path).append(accepted)

            self.assertEqual(raised.exception.code, DURABILITY_UNKNOWN_ERROR)
            self.assertEqual(raised.exception.outcome, "unknown_or_committed")
            self.assertEqual(
                path.read_bytes(),
                canonical_json(accepted.receipt.canonical()).encode("utf-8"),
            )
            retry = self.store(path).append(accepted)
            self.assertEqual(retry.variant, ReceiptWriteVariant.NO_OP)

    def test_lock_unsupported_is_explicit_and_nonmutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            path.write_bytes(b"")
            before = path.read_bytes()
            accepted = make_accepted(self.empty_ledger(), "receipt-1")
            with patch(
                "fcntl.flock",
                side_effect=OSError(errno.ENOTSUP, "injected unsupported lock"),
            ):
                with self.assertRaises(ReceiptStoreError) as raised:
                    self.store(path).append(accepted)

            self.assertEqual(raised.exception.code, LOCK_UNAVAILABLE_ERROR)
            self.assertEqual(raised.exception.outcome, "rejected")
            self.assertEqual(path.read_bytes(), before)

    def test_forged_accepted_ledger_is_an_obligation_error_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            path.write_bytes(b"")
            accepted = make_accepted(self.empty_ledger(), "receipt-1")
            forged = ReceiptWriteAccepted(
                ledger=self.empty_ledger(),
                receipt=accepted.receipt,
                obligations=accepted.obligations,
            )
            before = path.read_bytes()

            with self.assertRaises(ReceiptStoreError) as raised:
                self.store(path).append(forged)

            self.assertEqual(raised.exception.code, OBLIGATION_ERROR)
            self.assertEqual(raised.exception.outcome, "rejected")
            self.assertEqual(path.read_bytes(), before)

    def test_symlink_and_nonregular_store_paths_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.jsonl"
            target.write_bytes(b"target-bytes")
            symlink = root / "symlink.jsonl"
            symlink.symlink_to(target)
            directory = root / "directory.jsonl"
            directory.mkdir()

            for path in (symlink, directory):
                with self.subTest(path=path):
                    with self.assertRaises(ReceiptStoreError) as raised:
                        self.store(path).load()
                    self.assertEqual(raised.exception.code, LEDGER_CORRUPT_ERROR)
                    self.assertEqual(raised.exception.outcome, "rejected")

            self.assertEqual(target.read_bytes(), b"target-bytes")

    def test_real_lock_timeout_is_explicit_and_nonmutating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dispatch-receipts.jsonl"
            path.write_bytes(b"")
            lock_path = path.with_name(path.name + ".lock")
            context = multiprocessing.get_context("spawn")
            ready = context.Event()
            release = context.Event()
            holder = context.Process(target=_hold_lock, args=(str(lock_path), ready, release))
            holder.start()
            self.assertTrue(ready.wait(10))
            before = path.read_bytes()
            accepted = make_accepted(self.empty_ledger(), "receipt-1")
            try:
                with self.assertRaises(ReceiptStoreError) as raised:
                    self.store(path, lock_timeout=0.05).append(accepted)
            finally:
                release.set()
                holder.join(timeout=10)

            self.assertEqual(holder.exitcode, 0)
            self.assertEqual(raised.exception.code, LOCK_TIMEOUT_ERROR)
            self.assertEqual(raised.exception.outcome, "rejected")
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
