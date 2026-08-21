"""Schema migration and stored timestamp validation tests."""

import sqlite3
from pathlib import Path

from tests.support import DirectMemoryCase
import memory_store


class MemorySchemaTests(DirectMemoryCase):
    def test_legacy_schema_adds_retention_and_expires_at(self) -> None:
        database = Path(self.temp_dir.name) / "memory.sqlite3"
        conn = sqlite3.connect(database)
        try:
            conn.executescript(
                """
                CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE memories (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, subject_id TEXT NOT NULL,
                    field TEXT NOT NULL, value TEXT NOT NULL, source_type TEXT NOT NULL,
                    source_ref TEXT NOT NULL DEFAULT '', occurred_at TEXT,
                    observed_at TEXT NOT NULL, confidence TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE operations (
                    op_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, action TEXT NOT NULL,
                    before_json TEXT NOT NULL, after_json TEXT NOT NULL
                );
                INSERT INTO settings(key, value) VALUES('consent_enabled', 'true');
                """
            )
            conn.commit()
        finally:
            conn.close()

        self.call(memory_store.command_status)
        conn = sqlite3.connect(database)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
        finally:
            conn.close()
        self.assertIn("retention", columns)
        self.assertIn("expires_at", columns)

    def test_existing_hypothesis_gets_ttl_during_migration(self) -> None:
        self.enable()
        with memory_store.connect() as conn:
            conn.execute(
                "INSERT INTO memories(id, scope, subject_id, field, value, source_type, "
                "source_ref, occurred_at, retention, observed_at, confidence, status, "
                "expires_at, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy-hypothesis",
                    "hypothesis",
                    "obj-a",
                    "trend_estimate",
                    "旧趋势判断",
                    "assistant_inference",
                    "legacy",
                    None,
                    "normal",
                    "2026-08-01T12:00:00+08:00",
                    "medium",
                    "active",
                    None,
                    "2026-08-01T12:00:00+08:00",
                    "2026-08-01T12:00:00+08:00",
                ),
            )
        with memory_store.connect() as conn:
            row = conn.execute(
                "SELECT expires_at FROM memories WHERE id = 'legacy-hypothesis'"
            ).fetchone()
        self.assertEqual(row["expires_at"], "2026-08-15T04:00:00+00:00")

    def test_restore_rejects_invalid_created_or_updated_time(self) -> None:
        self.enable()
        row = {
            "id": "bad-time",
            "scope": "object",
            "subject_id": "obj-a",
            "field": "nickname",
            "value": "A",
            "source_type": "user_report",
            "source_ref": "test",
            "occurred_at": None,
            "observed_at": "2026-08-21T14:00:00+08:00",
            "confidence": "high",
            "status": "active",
            "created_at": "2026-08-21T14:00:00",
            "updated_at": "2026-08-21T14:00:00+08:00",
        }
        with memory_store.connect() as conn:
            with self.assertRaises(memory_store.MemoryErrorWithCode) as caught:
                memory_store.restore_rows(conn, [row])
        self.assertEqual(caught.exception.code, "INVALID_TIMESTAMP")


if __name__ == "__main__":
    import unittest

    unittest.main()

# Modified by AI on 2026-08-21 14:47:55
