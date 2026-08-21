"""Hypothesis TTL and lifecycle tests."""

from tests.support import DirectMemoryCase
import memory_store
from date_utils import add_days_iso


class MemoryExpirationTests(DirectMemoryCase):
    def test_active_hypothesis_becomes_stale_but_remains_visible_in_show(self) -> None:
        self.enable()
        applied = self.apply(
            self.hypothesis_delta("obj-a", "trend_estimate", "近期可能升温")
        )["memory"]
        self.assertEqual(
            applied["expires_at"], add_days_iso(applied["observed_at"], 14)
        )

        with memory_store.connect() as conn:
            changed = memory_store.refresh_hypothesis_lifecycle(
                conn, add_days_iso(applied["observed_at"], 15)
            )
        self.assertEqual(changed, 1)

        shown = self.show("obj-a")["memories"]
        stale = next(row for row in shown if row["field"] == "trend_estimate")
        self.assertEqual(stale["status"], "stale")
        self.assertFalse(
            any(row["field"] == "trend_estimate" for row in self.context("obj-a")["memories"])
        )

    def test_new_hypothesis_supersedes_old_record_without_deleting_it(self) -> None:
        self.enable()
        first = self.apply(
            self.hypothesis_delta("obj-a", "stage_estimate", "认识阶段")
        )["memory"]
        second = self.apply(
            self.hypothesis_delta("obj-a", "stage_estimate", "熟悉阶段")
        )["memory"]
        rows = [
            row
            for row in self.show("obj-a")["memories"]
            if row["field"] == "stage_estimate"
        ]

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual({row["status"] for row in rows}, {"active", "superseded"})

    def test_ttl_rules_and_fact_hypothesis_gate(self) -> None:
        self.enable()
        expected_days = {
            "stage_estimate": 30,
            "trend_estimate": 14,
            "humor_receptivity": 30,
            "style_update": 30,
        }
        for field, days in expected_days.items():
            row = self.apply(self.hypothesis_delta("obj-a", field, field))["memory"]
            self.assertEqual(row["expires_at"], add_days_iso(row["observed_at"], days))

        invalid = self.object_delta("obj-a", "humor_acceptance", "接受轻调侃")
        with self.assertRaises(memory_store.MemoryErrorWithCode) as caught:
            memory_store.validate_delta(invalid)
        self.assertEqual(caught.exception.code, "FACT_HYPOTHESIS_VIOLATION")

        invalid_time = self.hypothesis_delta("obj-a", "other_estimate", "未知")
        invalid_time["expires_at"] = "2026-08-21 14:00:00"
        with self.assertRaises(memory_store.MemoryErrorWithCode) as caught_time:
            memory_store.validate_delta(invalid_time)
        self.assertEqual(caught_time.exception.code, "INVALID_TIMESTAMP")


if __name__ == "__main__":
    import unittest

    unittest.main()

# Modified by AI on 2026-08-21 14:47:55
