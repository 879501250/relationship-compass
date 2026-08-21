"""Knowledge freshness lifecycle tests."""

import unittest

from tests.support import SCRIPTS  # noqa: F401

import knowledge_schema


class FreshnessTests(unittest.TestCase):
    def test_review_due_is_a_status_not_invalidation(self) -> None:
        record = {
            "last_reviewed_at": "2025-01-01T00:00:00+00:00",
            "freshness": "stable",
        }
        self.assertEqual(
            knowledge_schema.freshness_status(
                record, reference_at="2025-12-31T23:59:59+00:00"
            ),
            "current",
        )
        self.assertEqual(
            knowledge_schema.freshness_status(
                record, reference_at="2026-01-01T00:00:00+00:00"
            ),
            "review_due",
        )
        self.assertEqual(record["freshness"], "stable")

    def test_explicit_review_after_takes_precedence(self) -> None:
        record = {
            "last_reviewed_at": "2026-01-01T00:00:00+00:00",
            "freshness": "stable",
            "review_after": "2026-01-15T00:00:00+00:00",
        }
        self.assertEqual(
            knowledge_schema.freshness_status(
                record, reference_at="2026-01-16T00:00:00+00:00"
            ),
            "review_due",
        )


if __name__ == "__main__":
    unittest.main()

# Modified by AI on 2026-08-21 16:44:58
