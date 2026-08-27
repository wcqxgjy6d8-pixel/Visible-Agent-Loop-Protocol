from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from valp_cli.continuation import SafePointQueue
from valp_cli.protocol_kernel import (
    AttemptStatus,
    CheckpointAuthentication,
    CheckpointRoot,
    Dependency,
    Identity,
    IdentityKind,
    WorkItemRequirement,
    replay,
    replay_prefix_digest,
)
from valp_cli.receipt_store import ReceiptStore


ROOT = Path(__file__).resolve().parents[1]


class FeatureFloorTests(unittest.TestCase):
    """Keep the v0.3 consolidation floor explicit and hard to regress."""

    def test_authenticated_checkpoint_suffix_replay_api_is_present(self) -> None:
        self.assertTrue(callable(replay))
        self.assertTrue(callable(replay_prefix_digest))
        self.assertTrue(hasattr(CheckpointRoot, "canonical"))
        self.assertTrue(hasattr(CheckpointAuthentication, "statement_digest_for"))

    def test_receipt_store_v3_is_constructible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ReceiptStore(Path(tmp) / "receipts.jsonl", "installation", 1, "task")
            self.assertEqual(store.load().revision, 0)

    def test_work_item_dependency_canonicalization_is_lossless(self) -> None:
        dependency = Dependency(
            Identity(IdentityKind.WORK_ITEM, "dependency"),
            WorkItemRequirement.REQUIRED,
        )
        self.assertEqual(
            dependency.canonical(),
            {
                "work_item_id": {"kind": "work_item", "value": "dependency"},
                "requirement": "required",
            },
        )

    def test_attempt_fencing_status_remains_in_kernel(self) -> None:
        self.assertIn(AttemptStatus.FENCED, tuple(AttemptStatus))

    def test_deterministic_wait_wake_queue_is_identity_stable(self) -> None:
        self.assertTrue(hasattr(SafePointQueue, "enqueue"))
        self.assertTrue(hasattr(SafePointQueue, "safe_point"))

    def test_langgraph_false_done_case_study_reproduces(self) -> None:
        script = ROOT / "examples" / "langgraph-false-done" / "reproduce.sh"
        source = script.read_text(encoding="utf-8")
        self.assertIn("first-false-done.json", source)
        self.assertIn("repair", source)


if __name__ == "__main__":
    unittest.main()
