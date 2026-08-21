"""Deterministic context-budget allocation tests."""

import json

from tests.support import DirectMemoryCase


class ContextBudgetTests(DirectMemoryCase):
    def test_priority_isolation_and_rendered_budget(self) -> None:
        self.enable()
        self.apply(self.user_delta("current_style", "真实、直接"))
        self.apply(self.object_delta("obj-a", "nickname", "A"))
        self.apply(self.object_delta("obj-b", "nickname", "B-PRIVATE"))
        self.apply(
            {
                "scope": "relationship",
                "subject_id": "obj-a",
                "field": "known_boundary",
                "value": "不公开调侃",
                "source_type": "user_report",
                "source_ref": "test:relationship",
                "confidence": "high",
            }
        )
        self.apply(self.hypothesis_delta("obj-a", "stage_estimate", "熟悉阶段"))
        self.apply(self.hypothesis_delta("obj-a", "trend_estimate", "证据不足"))
        self.apply(
            self.event_delta(
                "obj-a",
                "first_meeting",
                "第一次见面",
                "2026-01-01T10:00:00+08:00",
                "landmark",
            )
        )
        filler = "普通事件内容" * 35
        for index in range(4):
            self.apply(
                self.event_delta(
                    "obj-a",
                    f"normal_{index}",
                    filler,
                    f"2026-02-{index + 1:02d}T10:00:00+08:00",
                    "normal",
                )
            )
            self.apply(
                self.event_delta(
                    "obj-a",
                    f"temporary_{index}",
                    filler,
                    f"2026-03-{index + 1:02d}T10:00:00+08:00",
                    "temporary",
                )
            )

        max_chars = 4200
        context = self.context("obj-a", max_chars=max_chars)
        rendered = json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fields = {row["field"] for row in context["memories"]}
        subjects = {row["subject_id"] for row in context["memories"]}

        self.assertLessEqual(len(rendered), max_chars)
        self.assertNotIn("obj-b", subjects)
        self.assertNotIn("B-PRIVATE", rendered)
        self.assertTrue(
            {"current_style", "nickname", "known_boundary"}.issubset(fields)
        )
        self.assertTrue({"stage_estimate", "trend_estimate", "first_meeting"}.issubset(fields))
        included_normal = sum(field.startswith("normal_") for field in fields)
        included_temporary = sum(field.startswith("temporary_") for field in fields)
        self.assertGreater(included_normal, 0)
        self.assertEqual(included_temporary, 0)


if __name__ == "__main__":
    import unittest

    unittest.main()

# Modified by AI on 2026-08-21 16:32:17
