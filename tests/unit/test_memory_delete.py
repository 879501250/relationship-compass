"""Precise and auditable field-deletion tests."""

from tests.support import DirectMemoryCase
import memory_store


class MemoryDeleteTests(DirectMemoryCase):
    def test_delete_one_field_does_not_affect_other_subjects_or_fields(self) -> None:
        self.enable()
        self.apply(self.object_delta("obj-a", "nickname", "A"))
        self.apply(self.object_delta("obj-a", "boundary", "不公开调侃"))
        self.apply(self.object_delta("obj-b", "nickname", "B"))

        deleted = self.call(
            memory_store.command_delete,
            scope="object",
            subject_id="obj-a",
            field="nickname",
            occurred_at=None,
            confirm=True,
        )
        rows = {(row["subject_id"], row["field"]) for row in self.show()["memories"]}

        self.assertTrue(deleted["op_id"])
        self.assertNotIn(("obj-a", "nickname"), rows)
        self.assertIn(("obj-a", "boundary"), rows)
        self.assertIn(("obj-b", "nickname"), rows)

        self.call(memory_store.command_undo, op_id=deleted["op_id"])
        restored = {(row["subject_id"], row["field"]) for row in self.show()["memories"]}
        self.assertIn(("obj-a", "nickname"), restored)


if __name__ == "__main__":
    import unittest

    unittest.main()
