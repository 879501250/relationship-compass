"""Fast direct-import tests for existing Memory behavior."""

import json

from tests.support import DirectMemoryCase
import memory_store


class MemoryCoreTests(DirectMemoryCase):
    def test_consent_pause_resume_and_user_source_gate(self) -> None:
        with self.assertRaises(memory_store.MemoryErrorWithCode) as missing:
            self.apply(self.user_delta("current_style", "直接"))
        self.assertEqual(missing.exception.code, "NOT_INITIALIZED")

        self.enable()
        self.call(memory_store.command_pause)
        with self.assertRaises(memory_store.MemoryErrorWithCode) as paused:
            self.apply(self.user_delta("target_style", "更主动"))
        self.assertEqual(paused.exception.code, "MEMORY_PAUSED")
        self.call(memory_store.command_resume)
        self.apply(self.user_delta("target_style", "更主动"))

        with self.assertRaises(memory_store.MemoryErrorWithCode) as source:
            memory_store.validate_delta(
                self.user_delta("current_style", "模型猜测", "assistant_inference")
            )
        self.assertEqual(source.exception.code, "SOURCE_NOT_ELIGIBLE")

    def test_capability_style_context_and_undo(self) -> None:
        self.enable()
        profile = json.dumps({"initiative": "developing", "continuation": "emerging"})
        applied = self.apply(self.user_delta("capability_profile", profile))
        self.apply(self.user_delta("current_style", "直接、问答式"))

        style = self.call(memory_store.command_style_status, max_age_days=0)
        self.assertEqual(style["review_status"], "expired")
        context = self.context("obj-a", max_chars=500)
        self.assertLessEqual(len(json.dumps(context["memories"], ensure_ascii=False)), 500)

        self.call(memory_store.command_undo, op_id=applied["op_id"])
        self.assertFalse(
            any(row["field"] == "capability_profile" for row in self.show()["memories"])
        )

    def test_hypothesis_prune_keeps_history_and_limits_active_context(self) -> None:
        self.enable()
        for index in range(7):
            self.apply(
                self.hypothesis_delta("obj-a", f"observation_{index}", f"假设 {index}")
            )
        shown = [
            row for row in self.show("obj-a")["memories"] if row["scope"] == "hypothesis"
        ]
        context = [
            row for row in self.context("obj-a")["memories"] if row["scope"] == "hypothesis"
        ]
        self.assertEqual(len(shown), 7)
        self.assertEqual(len(context), 5)
        self.assertEqual(sum(row["status"] == "superseded" for row in shown), 2)

    def test_recent_techniques_are_bounded_and_object_specific(self) -> None:
        self.enable()
        sequence = [
            "callback",
            "fake_serious",
            "plain",
            "light_teasing",
            "observation_humor",
            "micro_story",
            "contrast",
            "playful_framing",
            "self_deprecation",
            "plain",
        ]
        for technique in sequence:
            self.call(
                memory_store.command_record_technique,
                subject_id="obj-a",
                technique=technique,
                confirm_sent=True,
            )
        row = next(
            item
            for item in self.context("obj-a")["memories"]
            if item["field"] == "recent_techniques"
        )
        self.assertEqual(json.loads(row["value"]), sequence[-8:])
        self.assertFalse(
            any(item["field"] == "recent_techniques" for item in self.context("obj-b")["memories"])
        )

    def test_revoke_delete_removes_database(self) -> None:
        self.enable()
        self.apply(self.user_delta("target_style", "主动但真实"))
        self.call(memory_store.command_revoke, confirm=True, delete=False)
        self.enable()
        deleted = self.call(memory_store.command_revoke, confirm=True, delete=True)
        self.assertTrue(deleted["deleted"])
        status = self.call(memory_store.command_status)
        self.assertFalse(status["exists"])


if __name__ == "__main__":
    import unittest

    unittest.main()

# Modified by AI on 2026-08-21 14:47:55
