from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from jsonschema import Draft202012Validator

from valp_cli.catalog import CatalogError, EvidenceCatalog
from valp_cli.cli import build_parser, main


ROOT = Path(__file__).resolve().parents[1]


class EvidenceCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="valp-catalog-test-")
        self.workspace = Path(self.temporary.name)
        self.task = self.workspace / ".herdr-loop" / "tasks" / "TASK-A"
        (self.task / "evidence").mkdir(parents=True)
        (self.task / "agents" / "codex").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_status(self, evidence: dict[str, dict[str, str]]) -> None:
        (self.task / "evidence-status.json").write_text(
            json.dumps({"schema_version": "valp-evidence-status.v1", "evidence": evidence}),
            encoding="utf-8",
        )

    def test_index_task_only_catalogs_registered_evidence_with_digest(self) -> None:
        proof = self.task / "evidence" / "verification.md"
        proof.write_text("anonymous verification passed\n", encoding="utf-8")
        (self.task / "agents" / "codex" / "dispatch.md").write_text(
            "private dispatch text\n", encoding="utf-8"
        )
        (self.task / "control-contract.json").write_text("{}\n", encoding="utf-8")
        self.write_status({"evidence/verification.md": {"status": "valid"}})

        result = EvidenceCatalog(self.workspace).index_task("TASK-A")

        self.assertEqual(result["indexed_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        entry = result["entries"][0]
        self.assertEqual(entry["task_id"], "TASK-A")
        self.assertEqual(entry["source_ref"], "evidence/verification.md")
        self.assertEqual(entry["status"], "valid")
        self.assertEqual(entry["media_type"], "text/markdown")
        self.assertRegex(entry["content_digest"], r"^sha256:[0-9a-f]{64}$")

    def test_catalog_index_cli_returns_machine_readable_result(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "fixture verification passed\n", encoding="utf-8"
        )
        self.write_status({"evidence/verification.md": {"status": "valid"}})
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "catalog",
                    "index",
                    "TASK-A",
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["indexed_count"], 1)
        self.assertTrue((self.workspace / ".herdr-loop" / "evidence-catalog.db").is_file())

    def test_indexed_entry_matches_catalog_schema(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "schema fixture\n", encoding="utf-8"
        )
        self.write_status({"evidence/verification.md": {"status": "valid"}})
        entry = EvidenceCatalog(self.workspace).index_task("TASK-A")["entries"][0]
        schema = json.loads(
            (ROOT / "schemas" / "evidence-catalog-entry.schema.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(list(Draft202012Validator(schema).iter_errors(entry)), [])

    def test_search_defaults_to_valid_and_supports_explicit_status_filter(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "deployment reliability passed\n", encoding="utf-8"
        )
        (self.task / "agents" / "claude" ).mkdir(parents=True)
        (self.task / "agents" / "claude" / "review.md").write_text(
            "deployment reliability rejected\n", encoding="utf-8"
        )
        self.write_status(
            {
                "evidence/verification.md": {"status": "valid"},
                "agents/claude/review.md": {"status": "rejected"},
            }
        )
        catalog = EvidenceCatalog(self.workspace)
        catalog.index_task("TASK-A")

        valid = catalog.search("deployment")
        invalid = catalog.search("deployment", statuses=["invalid"])

        self.assertEqual([item["entry"]["source_ref"] for item in valid["results"]], [
            "evidence/verification.md"
        ])
        self.assertEqual([item["entry"]["source_ref"] for item in invalid["results"]], [
            "agents/claude/review.md"
        ])

    def test_catalog_search_cli_combines_keyword_and_status_filters(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "hybrid recall passed\n", encoding="utf-8"
        )
        self.write_status({"evidence/verification.md": {"status": "valid"}})
        EvidenceCatalog(self.workspace).index_task("TASK-A")
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "catalog",
                    "search",
                    "hybrid",
                    "--workspace",
                    str(self.workspace),
                    "--status",
                    "valid",
                    "--type",
                    "verification",
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["results"][0]["entry"]["source_ref"], "evidence/verification.md")

    def test_context_assembly_includes_citation_digest_and_verified_text(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "deterministic cited context\n", encoding="utf-8"
        )
        self.write_status({"evidence/verification.md": {"status": "valid"}})
        catalog = EvidenceCatalog(self.workspace)
        catalog.index_task("TASK-A")

        result = catalog.context("deterministic", max_chars=1000)

        self.assertEqual(result["count"], 1)
        self.assertIn("[E1] task=TASK-A ref=evidence/verification.md", result["context"])
        self.assertRegex(result["context"], r"digest=sha256:[0-9a-f]{64}")
        self.assertIn("deterministic cited context", result["context"])

    def test_verify_reports_drift_and_sweep_marks_entry_stale(self) -> None:
        proof = self.task / "evidence" / "verification.md"
        proof.write_text("original proof\n", encoding="utf-8")
        self.write_status({"evidence/verification.md": {"status": "valid"}})
        catalog = EvidenceCatalog(self.workspace)
        entry = catalog.index_task("TASK-A")["entries"][0]
        proof.write_text("tampered proof\n", encoding="utf-8")

        verification = catalog.verify(entry["catalog_id"])
        swept = catalog.sweep()

        self.assertFalse(verification["ok"])
        self.assertEqual(verification["reason"], "digest_mismatch")
        self.assertEqual(swept["stale_count"], 1)
        self.assertEqual(catalog.search("tampered")["count"], 0)
        stale = catalog.search("original", statuses=["stale"])
        self.assertEqual(stale["count"], 1)

    def test_index_rejects_registered_symlink_that_escapes_task(self) -> None:
        outside = self.workspace / "private.txt"
        outside.write_text("must not be indexed\n", encoding="utf-8")
        (self.task / "evidence" / "escape.md").symlink_to(outside)
        self.write_status({"evidence/escape.md": {"status": "valid"}})

        with self.assertRaisesRegex(CatalogError, "escaped task directory"):
            EvidenceCatalog(self.workspace).index_task("TASK-A")

    def test_anonymous_fixture_index_hides_source_and_task_identity(self) -> None:
        fixture_root = self.workspace / "fixtures"
        fixture_root.mkdir()
        (fixture_root / "proof.md").write_text(
            "synthetic anonymous recall proof\n", encoding="utf-8"
        )
        manifest = fixture_root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "valp-evidence-catalog-fixtures.v1",
                    "fixtures": [
                        {
                            "fixture_id": "anonymous-proof",
                            "source_ref": "proof.md",
                            "evidence_type": "test-output",
                            "status": "valid",
                            "depends_on": [],
                            "metadata": {"synthetic": True},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        catalog = EvidenceCatalog(self.workspace)

        result = catalog.index_fixtures(manifest)
        recalled = catalog.search("synthetic", anonymous_only=True)

        self.assertEqual(result["indexed_count"], 1)
        entry = recalled["results"][0]["entry"]
        self.assertTrue(entry["anonymous"])
        self.assertIsNone(entry["task_id"])
        self.assertIsNone(entry["source_ref"])
        self.assertEqual(entry["provenance"], {
            "agent": None,
            "dispatch_id": None,
            "tool_call_id": None,
        })

    def test_bundled_anonymous_fixture_manifest_matches_schema(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "evidence-catalog-fixtures.schema.json").read_text(
                encoding="utf-8"
            )
        )
        manifest = json.loads(
            (ROOT / "tests" / "fixtures" / "evidence-catalog" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(list(Draft202012Validator(schema).iter_errors(manifest)), [])

    def test_invalidate_marks_root_invalid_and_descendants_stale(self) -> None:
        fixture_root = self.workspace / "fixtures"
        shutil.copytree(ROOT / "tests" / "fixtures" / "evidence-catalog", fixture_root)
        catalog = EvidenceCatalog(self.workspace)
        catalog.index_fixtures(fixture_root / "manifest.json")
        root_entry = catalog.search(
            "deterministic", evidence_type="test-output", anonymous_only=True
        )["results"][0]["entry"]

        result = catalog.invalidate(root_entry["catalog_id"], "fixture revoked")

        self.assertEqual(result["entry"]["status"], "invalid")
        self.assertEqual(len(result["stale_dependents"]), 1)
        self.assertEqual(result["stale_dependents"][0]["status"], "stale")
        self.assertEqual(
            catalog.search("derived", statuses=["valid"], anonymous_only=True)["count"],
            0,
        )

    def test_catalog_fixtures_cli_indexes_anonymous_manifest(self) -> None:
        fixture_root = self.workspace / "fixtures"
        shutil.copytree(ROOT / "tests" / "fixtures" / "evidence-catalog", fixture_root)
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "catalog",
                    "fixtures",
                    str(fixture_root / "manifest.json"),
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["indexed_count"], 2)
        self.assertTrue(all(entry["anonymous"] for entry in result["entries"]))

    def test_catalog_help_exposes_recall_and_maintenance_commands(self) -> None:
        parser = build_parser()
        catalog_action = next(
            action for action in parser._actions if action.dest == "command"
        )
        catalog_parser = catalog_action.choices["catalog"]
        catalog_sub_action = next(
            action for action in catalog_parser._actions if action.dest == "catalog_command"
        )

        self.assertEqual(
            set(catalog_sub_action.choices),
            {
                "index",
                "search",
                "fixtures",
                "fixture",
                "context",
                "show",
                "verify",
                "sweep",
                "invalidate",
            },
        )

    def test_catalog_context_cli_returns_cited_verified_context(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "context command verification\n", encoding="utf-8"
        )
        self.write_status({"evidence/verification.md": {"status": "valid"}})
        EvidenceCatalog(self.workspace).index_task("TASK-A")
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "catalog",
                    "context",
                    "verification",
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["count"], 1)
        self.assertIn("[E1] task=TASK-A", result["context"])

    def test_index_workspace_catalogs_registered_evidence_across_tasks(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "first cross task proof\n", encoding="utf-8"
        )
        self.write_status({"evidence/verification.md": {"status": "valid"}})
        task_b = self.workspace / ".herdr-loop" / "tasks" / "TASK-B"
        (task_b / "evidence").mkdir(parents=True)
        (task_b / "evidence" / "review.md").write_text(
            "second cross task proof\n", encoding="utf-8"
        )
        (task_b / "evidence-status.json").write_text(
            json.dumps(
                {
                    "schema_version": "valp-evidence-status.v1",
                    "evidence": {"evidence/review.md": {"status": "valid"}},
                }
            ),
            encoding="utf-8",
        )

        result = EvidenceCatalog(self.workspace).index_workspace()

        self.assertEqual(result["task_count"], 2)
        self.assertEqual(result["indexed_count"], 2)
        self.assertEqual(result["task_ids"], ["TASK-A", "TASK-B"])

    def test_catalog_index_all_cli_indexes_workspace_tasks(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "workspace index proof\n", encoding="utf-8"
        )
        self.write_status({"evidence/verification.md": {"status": "valid"}})
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(
                [
                    "catalog",
                    "index",
                    "--all",
                    "--workspace",
                    str(self.workspace),
                    "--json",
                ]
            )

        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["task_count"], 1)
        self.assertEqual(result["indexed_count"], 1)

    def test_index_binds_evidence_to_declared_dispatch_provenance(self) -> None:
        evidence = self.task / "agents" / "codex" / "evidence.md"
        evidence.write_text("implementation proof\n", encoding="utf-8")
        self.write_status({"agents/codex/evidence.md": {"status": "valid"}})
        (self.task / "submission-dependencies.json").write_text(
            json.dumps(
                {
                    "schema_version": "valp-submission-dependencies.v2",
                    "task_id": "TASK-A",
                    "work_items": [
                        {
                            "work_item_id": "implementer:codex",
                            "agent": "codex",
                            "role": "implementer",
                            "dispatch_id": "TASK-A:implementer:1",
                            "dispatch_generation": 1,
                            "expected_refs": ["agents/codex/evidence.md"],
                        }
                    ],
                    "dependencies": [],
                }
            ),
            encoding="utf-8",
        )

        entry = EvidenceCatalog(self.workspace).index_task("TASK-A")["entries"][0]

        self.assertEqual(entry["provenance"]["agent"], "codex")
        self.assertEqual(entry["provenance"]["dispatch_id"], "TASK-A:implementer:1")

    def test_index_excludes_registered_control_and_dispatch_resources(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "allowed proof\n", encoding="utf-8"
        )
        (self.task / "control-contract.json").write_text("{}\n", encoding="utf-8")
        (self.task / "agents" / "codex" / "dispatch.md").write_text(
            "protected dispatch\n", encoding="utf-8"
        )
        self.write_status(
            {
                "evidence/verification.md": {"status": "valid"},
                "control-contract.json": {"status": "valid"},
                "agents/codex/dispatch.md": {"status": "valid"},
            }
        )

        result = EvidenceCatalog(self.workspace).index_task("TASK-A")

        self.assertEqual(result["indexed_count"], 1)
        self.assertEqual(result["skipped_count"], 2)
        self.assertEqual(result["entries"][0]["source_ref"], "evidence/verification.md")

    def test_empty_catalog_search_returns_empty_result(self) -> None:
        result = EvidenceCatalog(self.workspace).search("anything")

        self.assertEqual(result["count"], 0)
        self.assertEqual(result["results"], [])

    def test_show_rejects_invalid_catalog_id(self) -> None:
        with self.assertRaisesRegex(CatalogError, "invalid catalog id"):
            EvidenceCatalog(self.workspace).show("not-a-catalog-id")

    def test_search_recalls_unicode_evidence(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "中文检索证据通过验证\n", encoding="utf-8"
        )
        self.write_status({"evidence/verification.md": {"status": "valid"}})
        catalog = EvidenceCatalog(self.workspace)
        catalog.index_task("TASK-A")

        result = catalog.search("中文检索证据通过验证")

        self.assertEqual(result["count"], 1)

    def test_fixture_dependency_cycle_is_rejected_before_indexing(self) -> None:
        fixture_root = self.workspace / "fixtures"
        fixture_root.mkdir()
        (fixture_root / "a.md").write_text("fixture a\n", encoding="utf-8")
        (fixture_root / "b.md").write_text("fixture b\n", encoding="utf-8")
        manifest = fixture_root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "valp-evidence-catalog-fixtures.v1",
                    "fixtures": [
                        {
                            "fixture_id": "a",
                            "source_ref": "a.md",
                            "evidence_type": "proof",
                            "status": "valid",
                            "depends_on": ["b"],
                            "metadata": {},
                        },
                        {
                            "fixture_id": "b",
                            "source_ref": "b.md",
                            "evidence_type": "review",
                            "status": "valid",
                            "depends_on": ["a"],
                            "metadata": {},
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(CatalogError, "dependency cycle"):
            EvidenceCatalog(self.workspace).index_fixtures(manifest)

    def test_sweep_tracks_source_evidence_status_invalidation(self) -> None:
        (self.task / "evidence" / "verification.md").write_text(
            "unchanged but revoked proof\n", encoding="utf-8"
        )
        self.write_status({"evidence/verification.md": {"status": "valid"}})
        catalog = EvidenceCatalog(self.workspace)
        catalog.index_task("TASK-A")
        self.write_status({"evidence/verification.md": {"status": "invalid"}})

        result = catalog.sweep()

        self.assertEqual(result["invalid_count"], 1)
        invalid = catalog.search("revoked", statuses=["invalid"])
        self.assertEqual(invalid["count"], 1)
        self.assertEqual(invalid["results"][0]["entry"]["source_status"], "invalid")

    def test_fixture_metadata_rejects_nested_identifying_keys(self) -> None:
        fixture_root = self.workspace / "fixtures"
        fixture_root.mkdir()
        (fixture_root / "proof.md").write_text("synthetic proof\n", encoding="utf-8")
        manifest = fixture_root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "valp-evidence-catalog-fixtures.v1",
                    "fixtures": [
                        {
                            "fixture_id": "proof",
                            "source_ref": "proof.md",
                            "evidence_type": "test-output",
                            "status": "valid",
                            "depends_on": [],
                            "metadata": {"nested": {"task_id": "must-not-leak"}},
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(CatalogError, "identifying keys"):
            EvidenceCatalog(self.workspace).index_fixtures(manifest)

    def test_fixture_schema_rejects_nested_identifying_metadata(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "evidence-catalog-fixtures.schema.json").read_text(
                encoding="utf-8"
            )
        )
        fixture = {
            "schema_version": "valp-evidence-catalog-fixtures.v1",
            "fixtures": [
                {
                    "fixture_id": "proof",
                    "source_ref": "proof.md",
                    "evidence_type": "test-output",
                    "status": "valid",
                    "depends_on": [],
                    "metadata": {"nested": {"task_id": "must-not-leak"}},
                }
            ],
        }

        self.assertFalse(Draft202012Validator(schema).is_valid(fixture))


if __name__ == "__main__":
    unittest.main()
