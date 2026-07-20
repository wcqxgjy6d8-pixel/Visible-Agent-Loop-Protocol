from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CATALOG_SCHEMA_VERSION = "valp-evidence-catalog.v1"
TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SOURCE_STATUS_TO_CATALOG = {
    "valid": "valid",
    "superseded": "invalid",
    "invalid": "invalid",
    "rejected": "invalid",
    "blocked": "invalid",
}


class CatalogError(ValueError):
    """A deterministic evidence-catalog boundary failure."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


class EvidenceCatalog:
    """Rebuildable local index over explicitly registered VALP evidence."""

    def __init__(self, workspace: Path, database_path: Path | None = None) -> None:
        self.workspace = workspace.resolve()
        requested = database_path or Path(".herdr-loop/evidence-catalog.db")
        candidate = requested if requested.is_absolute() else self.workspace / requested
        self.database_path = candidate.resolve()
        if not _within(self.database_path, self.workspace):
            raise CatalogError("catalog database must stay inside the workspace")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.database_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS catalog_entries (
                    catalog_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    source_ref TEXT NOT NULL,
                    source_locator TEXT NOT NULL,
                    evidence_type TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('valid', 'stale', 'invalid')),
                    source_status TEXT NOT NULL,
                    content_digest TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    byte_length INTEGER NOT NULL,
                    provenance_agent TEXT,
                    provenance_dispatch_id TEXT,
                    provenance_tool_call_id TEXT,
                    anonymous INTEGER NOT NULL DEFAULT 0 CHECK (anonymous IN (0, 1)),
                    indexed_at TEXT NOT NULL,
                    stale_at TEXT,
                    invalidated_at TEXT,
                    invalidation_reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS catalog_entries_task_idx
                    ON catalog_entries(task_id);
                CREATE INDEX IF NOT EXISTS catalog_entries_digest_idx
                    ON catalog_entries(content_digest);
                CREATE INDEX IF NOT EXISTS catalog_entries_status_idx
                    ON catalog_entries(status);
                CREATE VIRTUAL TABLE IF NOT EXISTS catalog_fts USING fts5(
                    catalog_id UNINDEXED,
                    content,
                    tokenize='unicode61 remove_diacritics 2'
                );
                CREATE TABLE IF NOT EXISTS catalog_dependencies (
                    parent_id TEXT NOT NULL REFERENCES catalog_entries(catalog_id),
                    child_id TEXT NOT NULL REFERENCES catalog_entries(catalog_id),
                    PRIMARY KEY (parent_id, child_id),
                    CHECK (parent_id != child_id)
                );
                """
            )

    def _task_dir(self, task_id: str) -> Path:
        if not TASK_ID_PATTERN.fullmatch(task_id) or task_id in {".", ".."}:
            raise CatalogError(f"unsafe task id: {task_id!r}")
        tasks_root = (self.workspace / ".herdr-loop" / "tasks").resolve()
        task_dir = (tasks_root / task_id).resolve()
        if not _within(task_dir, tasks_root) or not task_dir.is_dir():
            raise CatalogError(f"task not found inside workspace: {task_id}")
        return task_dir

    @staticmethod
    def _allowed_evidence_ref(ref: str) -> bool:
        path = Path(ref)
        parts = path.parts
        if path.is_absolute() or ".." in parts or "." in parts:
            return False
        if len(parts) >= 2 and parts[0] == "evidence":
            return True
        return (
            len(parts) >= 3
            and parts[0] == "agents"
            and parts[2] != "dispatch.md"
        )

    @staticmethod
    def _unsafe_ref_syntax(ref: str) -> bool:
        path = Path(ref)
        return (
            path.is_absolute()
            or ".." in path.parts
            or "." in path.parts
            or "\\" in ref
            or ":" in ref
        )

    @staticmethod
    def _catalog_id(task_id: str, source_ref: str) -> str:
        value = f"task\0{task_id}\0{source_ref}".encode("utf-8")
        return f"catalog:{hashlib.sha256(value).hexdigest()}"

    @staticmethod
    def _fixture_catalog_id(evidence_type: str, content_digest: str) -> str:
        value = f"fixture\0{evidence_type}\0{content_digest}".encode("utf-8")
        return f"catalog:{hashlib.sha256(value).hexdigest()}"

    @staticmethod
    def _evidence_type(source_ref: str) -> str:
        return Path(source_ref).stem.replace("_", "-")

    @staticmethod
    def _agent(source_ref: str) -> str | None:
        parts = Path(source_ref).parts
        return parts[1] if len(parts) >= 3 and parts[0] == "agents" else None

    @staticmethod
    def _dispatch_provenance(task_dir: Path) -> dict[str, dict[str, str]]:
        path = task_dir / "submission-dependencies.json"
        if not path.is_file():
            return {}
        document = json.loads(path.read_text(encoding="utf-8"))
        work_items = document.get("work_items")
        if not isinstance(work_items, list):
            raise CatalogError("submission-dependencies.json has no work_items array")
        result: dict[str, dict[str, str]] = {}
        for item in work_items:
            if not isinstance(item, dict):
                continue
            agent = item.get("agent")
            dispatch_id = item.get("dispatch_id")
            refs = item.get("expected_refs")
            if not isinstance(agent, str) or not isinstance(dispatch_id, str):
                continue
            if not isinstance(refs, list):
                continue
            for ref in refs:
                if not isinstance(ref, str):
                    continue
                value = {"agent": agent, "dispatch_id": dispatch_id}
                if ref in result and result[ref] != value:
                    raise CatalogError(f"conflicting dispatch provenance for {ref}")
                result[ref] = value
        return result

    @staticmethod
    def _search_text(path: Path) -> str | None:
        with path.open("rb") as handle:
            payload = handle.read(1024 * 1024 + 1)
        if b"\x00" in payload:
            return None
        try:
            return payload[: 1024 * 1024].decode("utf-8")
        except UnicodeDecodeError:
            return None

    @staticmethod
    def _media_type(path: Path) -> str:
        fixed = {
            ".md": "text/markdown",
            ".markdown": "text/markdown",
            ".json": "application/json",
            ".jsonl": "application/x-ndjson",
            ".txt": "text/plain",
            ".log": "text/plain",
        }
        return fixed.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[\w-]+", query, flags=re.UNICODE)
        if not tokens:
            raise CatalogError("search query must contain a word or digest")
        return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)

    @staticmethod
    def _entry(row: sqlite3.Row) -> dict[str, Any]:
        anonymous = bool(row["anonymous"])
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "catalog_id": row["catalog_id"],
            "task_id": None if anonymous else row["task_id"],
            "source_ref": None if anonymous else row["source_ref"],
            "evidence_type": row["evidence_type"],
            "status": row["status"],
            "source_status": row["source_status"],
            "content_digest": row["content_digest"],
            "media_type": row["media_type"],
            "byte_length": row["byte_length"],
            "provenance": {
                "agent": None if anonymous else row["provenance_agent"],
                "dispatch_id": None if anonymous else row["provenance_dispatch_id"],
                "tool_call_id": None if anonymous else row["provenance_tool_call_id"],
            },
            "anonymous": anonymous,
            "indexed_at": row["indexed_at"],
            "stale_at": row["stale_at"],
            "invalidated_at": row["invalidated_at"],
            "invalidation_reason": row["invalidation_reason"],
            "metadata": json.loads(row["metadata_json"]),
        }

    def index_task(self, task_id: str) -> dict[str, Any]:
        task_dir = self._task_dir(task_id)
        status_path = task_dir / "evidence-status.json"
        if not status_path.is_file():
            raise CatalogError(f"task has no evidence-status.json: {task_id}")
        status_document = json.loads(status_path.read_text(encoding="utf-8"))
        if status_document.get("schema_version") != "valp-evidence-status.v1":
            raise CatalogError(f"unsupported evidence status schema for task {task_id}")
        evidence = status_document.get("evidence")
        if not isinstance(evidence, dict):
            raise CatalogError(f"evidence-status.json has no evidence object for task {task_id}")
        provenance_by_ref = self._dispatch_provenance(task_dir)

        entries: list[dict[str, Any]] = []
        skipped = 0
        with self._connect() as connection:
            for source_ref, source_record in sorted(evidence.items()):
                if not isinstance(source_ref, str) or not isinstance(source_record, dict):
                    skipped += 1
                    continue
                if self._unsafe_ref_syntax(source_ref):
                    raise CatalogError(f"unsafe registered evidence ref: {source_ref!r}")
                if not self._allowed_evidence_ref(source_ref):
                    skipped += 1
                    continue
                source_path = (task_dir / source_ref).resolve()
                if not _within(source_path, task_dir):
                    raise CatalogError(
                        f"registered evidence escaped task directory: {source_ref!r}"
                    )
                if not source_path.is_file():
                    skipped += 1
                    continue
                source_status = source_record.get("status")
                if source_status not in SOURCE_STATUS_TO_CATALOG:
                    skipped += 1
                    continue
                locator = source_path.relative_to(self.workspace).as_posix()
                catalog_id = self._catalog_id(task_id, source_ref)
                media_type = self._media_type(source_path)
                indexed_at = _now()
                path_agent = self._agent(source_ref)
                provenance = provenance_by_ref.get(source_ref, {})
                provenance_agent = provenance.get("agent") or path_agent
                if path_agent and provenance_agent != path_agent:
                    raise CatalogError(
                        f"dispatch provenance agent does not match evidence path: {source_ref}"
                    )
                connection.execute(
                    """
                    INSERT INTO catalog_entries (
                        catalog_id, task_id, source_ref, source_locator,
                        evidence_type, status, source_status, content_digest,
                        media_type, byte_length, provenance_agent,
                        provenance_dispatch_id, provenance_tool_call_id,
                        anonymous, indexed_at, stale_at, invalidated_at,
                        invalidation_reason, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, NULL, NULL, NULL, '{}')
                    ON CONFLICT(catalog_id) DO UPDATE SET
                        source_locator=excluded.source_locator,
                        evidence_type=excluded.evidence_type,
                        status=excluded.status,
                        source_status=excluded.source_status,
                        content_digest=excluded.content_digest,
                        media_type=excluded.media_type,
                        byte_length=excluded.byte_length,
                        provenance_agent=excluded.provenance_agent,
                        provenance_dispatch_id=excluded.provenance_dispatch_id,
                        provenance_tool_call_id=NULL,
                        anonymous=0,
                        indexed_at=excluded.indexed_at,
                        stale_at=NULL,
                        invalidated_at=NULL,
                        invalidation_reason=NULL
                    """,
                    (
                        catalog_id,
                        task_id,
                        source_ref,
                        locator,
                        self._evidence_type(source_ref),
                        SOURCE_STATUS_TO_CATALOG[source_status],
                        source_status,
                        _sha256(source_path),
                        media_type,
                        source_path.stat().st_size,
                        provenance_agent,
                        provenance.get("dispatch_id"),
                        indexed_at,
                    ),
                )
                connection.execute("DELETE FROM catalog_fts WHERE catalog_id = ?", (catalog_id,))
                search_text = self._search_text(source_path)
                if search_text is not None:
                    connection.execute(
                        "INSERT INTO catalog_fts(catalog_id, content) VALUES (?, ?)",
                        (catalog_id, search_text),
                    )
                row = connection.execute(
                    "SELECT * FROM catalog_entries WHERE catalog_id = ?", (catalog_id,)
                ).fetchone()
                if row is not None:
                    entries.append(self._entry(row))
        return {
            "schema_version": CATALOG_SCHEMA_VERSION,
            "task_id": task_id,
            "indexed_count": len(entries),
            "skipped_count": skipped,
            "entries": entries,
        }

    def index_workspace(self) -> dict[str, Any]:
        tasks_root = (self.workspace / ".herdr-loop" / "tasks").resolve()
        if not tasks_root.is_dir():
            raise CatalogError("workspace has no .herdr-loop/tasks directory")
        results: list[dict[str, Any]] = []
        for candidate in sorted(tasks_root.iterdir(), key=lambda path: path.name):
            if not candidate.is_dir():
                continue
            resolved = candidate.resolve()
            if not _within(resolved, tasks_root):
                raise CatalogError(f"task directory escaped workspace: {candidate.name!r}")
            if not (resolved / "evidence-status.json").is_file():
                continue
            results.append(self.index_task(candidate.name))
        return {
            "schema_version": "valp-evidence-catalog-workspace-index.v1",
            "task_count": len(results),
            "indexed_count": sum(result["indexed_count"] for result in results),
            "skipped_count": sum(result["skipped_count"] for result in results),
            "task_ids": [result["task_id"] for result in results],
            "tasks": results,
        }

    @staticmethod
    def _fixture_metadata(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise CatalogError("fixture metadata must be an object")
        forbidden = {
            "task",
            "task_id",
            "source",
            "source_ref",
            "path",
            "agent",
            "dispatch_id",
            "tool_call_id",
            "user",
            "user_id",
        }
        def inspect(node: Any) -> None:
            if isinstance(node, dict):
                leaked = sorted(forbidden.intersection(node))
                if leaked:
                    raise CatalogError(
                        "anonymous fixture metadata contains identifying keys: "
                        + ", ".join(leaked)
                    )
                for child in node.values():
                    inspect(child)
            elif isinstance(node, list):
                for child in node:
                    inspect(child)

        inspect(value)
        return value

    @staticmethod
    def _assert_fixture_graph_acyclic(
        fixtures: dict[str, dict[str, Any]],
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(fixture_id: str) -> None:
            if fixture_id in visiting:
                raise CatalogError(f"anonymous fixture dependency cycle at {fixture_id}")
            if fixture_id in visited:
                return
            visiting.add(fixture_id)
            for parent_id in fixtures[fixture_id]["depends_on"]:
                if parent_id not in fixtures:
                    raise CatalogError(
                        f"anonymous fixture dependency not found: {parent_id}"
                    )
                visit(parent_id)
            visiting.remove(fixture_id)
            visited.add(fixture_id)

        for fixture_id in fixtures:
            visit(fixture_id)

    def index_fixtures(self, manifest_path: Path) -> dict[str, Any]:
        requested = manifest_path if manifest_path.is_absolute() else self.workspace / manifest_path
        manifest = requested.resolve()
        if not _within(manifest, self.workspace) or not manifest.is_file():
            raise CatalogError("fixture manifest must be a file inside the workspace")
        document = json.loads(manifest.read_text(encoding="utf-8"))
        if document.get("schema_version") != "valp-evidence-catalog-fixtures.v1":
            raise CatalogError("unsupported anonymous fixture manifest schema")
        fixture_values = document.get("fixtures")
        if not isinstance(fixture_values, list) or not fixture_values:
            raise CatalogError("fixture manifest must contain a non-empty fixtures array")

        fixture_root = manifest.parent.resolve()
        fixtures: dict[str, dict[str, Any]] = {}
        for value in fixture_values:
            if not isinstance(value, dict):
                raise CatalogError("each anonymous fixture must be an object")
            fixture_id = value.get("fixture_id")
            source_ref = value.get("source_ref")
            evidence_type = value.get("evidence_type")
            status = value.get("status", "valid")
            depends_on = value.get("depends_on", [])
            if not isinstance(fixture_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]*", fixture_id
            ):
                raise CatalogError(f"unsafe anonymous fixture id: {fixture_id!r}")
            if fixture_id in fixtures:
                raise CatalogError(f"duplicate anonymous fixture id: {fixture_id}")
            if not isinstance(source_ref, str) or self._unsafe_ref_syntax(source_ref):
                raise CatalogError(f"unsafe anonymous fixture source_ref: {source_ref!r}")
            if not isinstance(evidence_type, str) or not evidence_type.strip():
                raise CatalogError(f"anonymous fixture {fixture_id} has no evidence_type")
            if status not in {"valid", "stale", "invalid"}:
                raise CatalogError(f"anonymous fixture {fixture_id} has invalid status")
            if not isinstance(depends_on, list) or not all(
                isinstance(item, str) for item in depends_on
            ):
                raise CatalogError(f"anonymous fixture {fixture_id} has invalid depends_on")
            source_path = (fixture_root / source_ref).resolve()
            if not _within(source_path, fixture_root):
                raise CatalogError(
                    f"anonymous fixture escaped manifest directory: {source_ref!r}"
                )
            if not source_path.is_file():
                raise CatalogError(f"anonymous fixture source not found: {source_ref}")
            content_digest = _sha256(source_path)
            fixtures[fixture_id] = {
                "fixture_id": fixture_id,
                "source_ref": source_ref,
                "source_path": source_path,
                "evidence_type": evidence_type,
                "status": status,
                "depends_on": depends_on,
                "metadata": self._fixture_metadata(value.get("metadata", {})),
                "content_digest": content_digest,
                "catalog_id": self._fixture_catalog_id(evidence_type, content_digest),
            }
        self._assert_fixture_graph_acyclic(fixtures)

        indexed: dict[str, dict[str, Any]] = {}
        with self._connect() as connection:
            for fixture in fixtures.values():
                source_path = fixture["source_path"]
                catalog_id = fixture["catalog_id"]
                indexed_at = _now()
                media_type = self._media_type(source_path)
                connection.execute(
                    """
                    INSERT INTO catalog_entries (
                        catalog_id, task_id, source_ref, source_locator,
                        evidence_type, status, source_status, content_digest,
                        media_type, byte_length, provenance_agent,
                        provenance_dispatch_id, provenance_tool_call_id,
                        anonymous, indexed_at, stale_at, invalidated_at,
                        invalidation_reason, metadata_json
                    ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 1, ?, NULL, NULL, NULL, ?)
                    ON CONFLICT(catalog_id) DO UPDATE SET
                        task_id=NULL,
                        source_ref=excluded.source_ref,
                        source_locator=excluded.source_locator,
                        evidence_type=excluded.evidence_type,
                        status=excluded.status,
                        source_status=excluded.source_status,
                        media_type=excluded.media_type,
                        byte_length=excluded.byte_length,
                        provenance_agent=NULL,
                        provenance_dispatch_id=NULL,
                        provenance_tool_call_id=NULL,
                        anonymous=1,
                        indexed_at=excluded.indexed_at,
                        stale_at=NULL,
                        invalidated_at=NULL,
                        invalidation_reason=NULL,
                        metadata_json=excluded.metadata_json
                    """,
                    (
                        catalog_id,
                        fixture["fixture_id"],
                        source_path.relative_to(self.workspace).as_posix(),
                        fixture["evidence_type"],
                        fixture["status"],
                        fixture["status"],
                        fixture["content_digest"],
                        media_type,
                        source_path.stat().st_size,
                        indexed_at,
                        json.dumps(fixture["metadata"], sort_keys=True, ensure_ascii=False),
                    ),
                )
                connection.execute("DELETE FROM catalog_fts WHERE catalog_id = ?", (catalog_id,))
                search_text = self._search_text(source_path)
                if search_text is not None:
                    connection.execute(
                        "INSERT INTO catalog_fts(catalog_id, content) VALUES (?, ?)",
                        (catalog_id, search_text),
                    )
                row = connection.execute(
                    "SELECT * FROM catalog_entries WHERE catalog_id = ?", (catalog_id,)
                ).fetchone()
                if row is not None:
                    indexed[catalog_id] = self._entry(row)

            child_ids = {fixture["catalog_id"] for fixture in fixtures.values()}
            if child_ids:
                placeholders = ",".join("?" for _ in child_ids)
                connection.execute(
                    f"DELETE FROM catalog_dependencies WHERE child_id IN ({placeholders})",
                    list(child_ids),
                )
            for fixture in fixtures.values():
                for parent_fixture_id in fixture["depends_on"]:
                    parent_id = fixtures[parent_fixture_id]["catalog_id"]
                    child_id = fixture["catalog_id"]
                    if parent_id == child_id:
                        raise CatalogError(
                            "anonymous fixture dependency collapses to the same content-addressed entry"
                        )
                    connection.execute(
                        "INSERT OR IGNORE INTO catalog_dependencies(parent_id, child_id) VALUES (?, ?)",
                        (parent_id, child_id),
                    )
        return {
            "schema_version": "valp-evidence-catalog-fixture-index.v1",
            "indexed_count": len(indexed),
            "entries": list(indexed.values()),
        }

    def search(
        self,
        query: str = "",
        *,
        statuses: list[str] | tuple[str, ...] | None = None,
        evidence_type: str | None = None,
        agent: str | None = None,
        task_id: str | None = None,
        content_digest: str | None = None,
        anonymous_only: bool = False,
        limit: int = 20,
    ) -> dict[str, Any]:
        selected_statuses = list(statuses or ["valid"])
        invalid_statuses = sorted(set(selected_statuses) - {"valid", "stale", "invalid"})
        if invalid_statuses:
            raise CatalogError(f"unsupported catalog status: {', '.join(invalid_statuses)}")
        if not 1 <= limit <= 100:
            raise CatalogError("search limit must be between 1 and 100")

        filters = [f"e.status IN ({','.join('?' for _ in selected_statuses)})"]
        values: list[Any] = list(selected_statuses)
        if evidence_type:
            filters.append("e.evidence_type = ?")
            values.append(evidence_type)
        if agent:
            filters.append("e.provenance_agent = ?")
            values.append(agent)
        if task_id:
            filters.append("e.task_id = ?")
            values.append(task_id)
        if content_digest:
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", content_digest):
                raise CatalogError("content digest must use sha256:<64 lowercase hex>")
            filters.append("e.content_digest = ?")
            values.append(content_digest)
        if anonymous_only:
            filters.append("e.anonymous = 1")

        where = " AND ".join(filters)
        with self._connect() as connection:
            if query.strip():
                sql = f"""
                    SELECT e.*, bm25(catalog_fts) AS rank
                    FROM catalog_fts
                    JOIN catalog_entries AS e ON e.catalog_id = catalog_fts.catalog_id
                    WHERE catalog_fts MATCH ? AND {where}
                    ORDER BY rank ASC, e.indexed_at DESC, e.catalog_id ASC
                    LIMIT ?
                """
                rows = connection.execute(
                    sql, [self._fts_query(query), *values, limit]
                ).fetchall()
            else:
                sql = f"""
                    SELECT e.*, 0.0 AS rank
                    FROM catalog_entries AS e
                    WHERE {where}
                    ORDER BY e.indexed_at DESC, e.catalog_id ASC
                    LIMIT ?
                """
                rows = connection.execute(sql, [*values, limit]).fetchall()

        results = []
        for row in rows:
            entry = self._entry(row)
            citation = (
                f"anonymous:{entry['catalog_id']}@{entry['content_digest']}"
                if entry["anonymous"]
                else f"{entry['task_id']}:{entry['source_ref']}@{entry['content_digest']}"
            )
            results.append({"entry": entry, "score": -float(row["rank"]), "citation": citation})
        return {
            "schema_version": "valp-evidence-catalog-search.v1",
            "query": query,
            "statuses": selected_statuses,
            "count": len(results),
            "results": results,
        }

    def _source_path(self, row: sqlite3.Row) -> Path:
        source_path = (self.workspace / row["source_locator"]).resolve()
        if not _within(source_path, self.workspace):
            raise CatalogError(f"catalog source escaped workspace: {row['catalog_id']}")
        return source_path

    def _row(self, catalog_id: str, connection: sqlite3.Connection) -> sqlite3.Row:
        if not re.fullmatch(r"catalog:[0-9a-f]{64}", catalog_id):
            raise CatalogError(f"invalid catalog id: {catalog_id!r}")
        row = connection.execute(
            "SELECT * FROM catalog_entries WHERE catalog_id = ?", (catalog_id,)
        ).fetchone()
        if row is None:
            raise CatalogError(f"catalog entry not found: {catalog_id}")
        return row

    def show(self, catalog_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return self._entry(self._row(catalog_id, connection))

    def verify(self, catalog_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._row(catalog_id, connection)
            entry = self._entry(row)
            source_path = self._source_path(row)
            if not source_path.is_file():
                return {
                    "schema_version": "valp-evidence-catalog-verification.v1",
                    "catalog_id": catalog_id,
                    "ok": False,
                    "reason": "source_missing",
                    "expected_digest": entry["content_digest"],
                    "observed_digest": None,
                }
            observed = _sha256(source_path)
            return {
                "schema_version": "valp-evidence-catalog-verification.v1",
                "catalog_id": catalog_id,
                "ok": observed == entry["content_digest"],
                "reason": "match" if observed == entry["content_digest"] else "digest_mismatch",
                "expected_digest": entry["content_digest"],
                "observed_digest": observed,
            }

    def _current_source_status(self, row: sqlite3.Row) -> str | None:
        if bool(row["anonymous"]):
            return row["source_status"]
        task_id = row["task_id"]
        if not isinstance(task_id, str):
            return None
        try:
            task_dir = self._task_dir(task_id)
            document = json.loads(
                (task_dir / "evidence-status.json").read_text(encoding="utf-8")
            )
        except (CatalogError, OSError, json.JSONDecodeError):
            return None
        record = (document.get("evidence") or {}).get(row["source_ref"])
        return record.get("status") if isinstance(record, dict) else None

    def sweep(self) -> dict[str, Any]:
        stale_count = 0
        invalid_count = 0
        unchanged_count = 0
        changes: list[dict[str, str]] = []
        now = _now()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM catalog_entries ORDER BY catalog_id"
            ).fetchall()
            for row in rows:
                if row["status"] == "invalid":
                    unchanged_count += 1
                    continue
                source_status = self._current_source_status(row)
                if source_status not in SOURCE_STATUS_TO_CATALOG:
                    target = "stale"
                    reason = "source_status_missing"
                elif SOURCE_STATUS_TO_CATALOG[source_status] == "invalid":
                    target = "invalid"
                    reason = f"source_status_{source_status}"
                else:
                    source_path = self._source_path(row)
                    if not source_path.is_file():
                        target = "stale"
                        reason = "source_missing"
                    elif _sha256(source_path) != row["content_digest"]:
                        target = "stale"
                        reason = "digest_mismatch"
                    else:
                        unchanged_count += 1
                        continue
                if target == "invalid":
                    connection.execute(
                        """
                        UPDATE catalog_entries
                        SET status='invalid', source_status=?, invalidated_at=?,
                            invalidation_reason=?, stale_at=NULL
                        WHERE catalog_id=?
                        """,
                        (source_status, now, reason, row["catalog_id"]),
                    )
                    invalid_count += 1
                else:
                    connection.execute(
                        """
                        UPDATE catalog_entries
                        SET status='stale', stale_at=?, invalidation_reason=?
                        WHERE catalog_id=?
                        """,
                        (now, reason, row["catalog_id"]),
                    )
                    stale_count += 1
                changes.append(
                    {"catalog_id": row["catalog_id"], "status": target, "reason": reason}
                )
        return {
            "schema_version": "valp-evidence-catalog-sweep.v1",
            "stale_count": stale_count,
            "invalid_count": invalid_count,
            "unchanged_count": unchanged_count,
            "changes": changes,
        }

    def invalidate(self, catalog_id: str, reason: str) -> dict[str, Any]:
        reason = reason.strip()
        if not reason or len(reason) > 500:
            raise CatalogError("invalidation reason must contain 1-500 characters")
        now = _now()
        with self._connect() as connection:
            self._row(catalog_id, connection)
            descendant_rows = connection.execute(
                """
                WITH RECURSIVE descendants(catalog_id) AS (
                    SELECT child_id
                    FROM catalog_dependencies
                    WHERE parent_id = ?
                    UNION
                    SELECT dependency.child_id
                    FROM catalog_dependencies AS dependency
                    JOIN descendants
                      ON dependency.parent_id = descendants.catalog_id
                )
                SELECT catalog_id FROM descendants ORDER BY catalog_id
                """,
                (catalog_id,),
            ).fetchall()
            descendant_ids = [row["catalog_id"] for row in descendant_rows]
            connection.execute(
                """
                UPDATE catalog_entries
                SET status='invalid', invalidated_at=?, stale_at=NULL,
                    invalidation_reason=?
                WHERE catalog_id=?
                """,
                (now, reason, catalog_id),
            )
            stale_ids: list[str] = []
            for descendant_id in descendant_ids:
                descendant = self._row(descendant_id, connection)
                if descendant["status"] == "invalid":
                    continue
                connection.execute(
                    """
                    UPDATE catalog_entries
                    SET status='stale', stale_at=?, invalidation_reason=?
                    WHERE catalog_id=?
                    """,
                    (now, f"dependency_invalidated:{catalog_id}", descendant_id),
                )
                stale_ids.append(descendant_id)
            entry = self._entry(self._row(catalog_id, connection))
            stale_dependents = [
                self._entry(self._row(descendant_id, connection))
                for descendant_id in stale_ids
            ]
        return {
            "schema_version": "valp-evidence-catalog-invalidation.v1",
            "entry": entry,
            "stale_dependents": stale_dependents,
        }

    def context(
        self,
        query: str,
        *,
        statuses: list[str] | tuple[str, ...] | None = None,
        evidence_type: str | None = None,
        agent: str | None = None,
        task_id: str | None = None,
        limit: int = 5,
        max_chars: int = 4000,
        anonymous_only: bool = False,
    ) -> dict[str, Any]:
        if not 256 <= max_chars <= 50_000:
            raise CatalogError("context max_chars must be between 256 and 50000")
        found = self.search(
            query,
            statuses=statuses,
            evidence_type=evidence_type,
            agent=agent,
            task_id=task_id,
            anonymous_only=anonymous_only,
            limit=limit,
        )
        blocks: list[str] = []
        citations: list[dict[str, Any]] = []
        omitted: list[dict[str, str]] = []
        used = 0
        with self._connect() as connection:
            for item in found["results"]:
                entry = item["entry"]
                row = connection.execute(
                    "SELECT * FROM catalog_entries WHERE catalog_id = ?",
                    (entry["catalog_id"],),
                ).fetchone()
                if row is None:
                    omitted.append({"catalog_id": entry["catalog_id"], "reason": "missing catalog row"})
                    continue
                source_path = self._source_path(row)
                if not source_path.is_file():
                    omitted.append({"catalog_id": entry["catalog_id"], "reason": "source missing"})
                    continue
                if _sha256(source_path) != entry["content_digest"]:
                    omitted.append({"catalog_id": entry["catalog_id"], "reason": "digest drift"})
                    continue
                content = self._search_text(source_path)
                if content is None:
                    omitted.append({"catalog_id": entry["catalog_id"], "reason": "non-text evidence"})
                    continue
                citation_id = f"E{len(citations) + 1}"
                if entry["anonymous"]:
                    header = (
                        f"[{citation_id}] anonymous=true type={entry['evidence_type']} "
                        f"digest={entry['content_digest']}"
                    )
                else:
                    header = (
                        f"[{citation_id}] task={entry['task_id']} ref={entry['source_ref']} "
                        f"digest={entry['content_digest']}"
                    )
                separator = "\n\n" if blocks else ""
                remaining = max_chars - used - len(separator) - len(header) - 1
                if remaining <= 0:
                    omitted.append({"catalog_id": entry["catalog_id"], "reason": "context budget"})
                    continue
                body = content.strip()[:remaining]
                block = f"{header}\n{body}"
                blocks.append(block)
                used += len(separator) + len(block)
                citations.append(
                    {
                        "citation_id": citation_id,
                        "catalog_id": entry["catalog_id"],
                        "content_digest": entry["content_digest"],
                        "task_id": entry["task_id"],
                        "source_ref": entry["source_ref"],
                        "anonymous": entry["anonymous"],
                    }
                )
        return {
            "schema_version": "valp-evidence-context.v1",
            "query": query,
            "count": len(citations),
            "context": "\n\n".join(blocks),
            "citations": citations,
            "omitted": omitted,
        }
