"""Shared policy parity tests for generated knowledge."""

import unittest

from tests.support import ROOT, SCRIPTS  # noqa: F401

import build_chatgpt_pack


class PolicyPackTests(unittest.TestCase):
    def test_core_policy_is_automatically_included(self) -> None:
        policy = build_chatgpt_pack.build_knowledge_bodies(ROOT)["01-CORE_POLICY.md"]
        for marker in (
            "fact != hypothesis",
            "object isolation",
            "green / gray / yellow / red",
            "continuation ownership",
            "actual send learning",
            "user growth != partner response",
            "stop conditions",
        ):
            self.assertIn(marker, policy)


if __name__ == "__main__":
    unittest.main()
