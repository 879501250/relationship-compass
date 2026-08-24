"""Deterministic ChatGPT knowledge pack tests."""

import unittest

from tests.support import ROOT, SCRIPTS  # noqa: F401

import build_chatgpt_pack


class ChatGptPackTests(unittest.TestCase):
    def test_same_inputs_produce_identical_knowledge_bodies(self) -> None:
        first = build_chatgpt_pack.build_knowledge_bodies(ROOT)
        second = build_chatgpt_pack.build_knowledge_bodies(ROOT)
        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 4)
        self.assertLessEqual(len(first), 8)

    def test_pack_excludes_private_and_workflow_artifacts(self) -> None:
        serialized = "\n".join(build_chatgpt_pack.build_knowledge_bodies(ROOT).values())
        for forbidden in build_chatgpt_pack.FORBIDDEN_PACK_TOKENS:
            self.assertNotIn(forbidden, serialized)
        self.assertIsNone(build_chatgpt_pack.LOCAL_PATH_PATTERN.search(serialized))


if __name__ == "__main__":
    unittest.main()
