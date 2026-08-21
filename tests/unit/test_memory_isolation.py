"""Object-isolation tests using direct module imports."""

from tests.support import DirectMemoryCase


class MemoryIsolationTests(DirectMemoryCase):
    def test_obj_a_context_never_contains_obj_b(self) -> None:
        self.enable()
        self.apply(self.user_delta("target_style", "真实且主动"))
        self.apply(self.object_delta("obj-a", "nickname", "A 的专属代号"))
        self.apply(self.object_delta("obj-b", "nickname", "B 的专属代号"))

        context = self.context("obj-a")
        subjects = {item["subject_id"] for item in context["memories"]}
        serialized = str(context)

        self.assertEqual(subjects, {"user", "obj-a"})
        self.assertNotIn("obj-b", serialized)
        self.assertNotIn("B 的专属代号", serialized)


if __name__ == "__main__":
    import unittest

    unittest.main()

# Modified by AI on 2026-08-21 14:47:55
