"""Claim conflict and replacement safety tests."""

import unittest

from tests.knowledge_support import sample_claim, sample_source

import knowledge_merge


class ClaimConflictTests(unittest.TestCase):
    def setUp(self) -> None:
        self.existing_claim = sample_claim(text="连续追问容易形成 interview mode")
        decision = [{"claim_id": self.existing_claim["claim_id"], "decision": "approve"}]
        self.store, _ = knowledge_merge.merge_approved_claims(
            {"schema_version": 1, "claims": []},
            [self.existing_claim],
            decision,
            sample_source(),
            "2026-08-21T08:00:00+00:00",
        )

    def test_keep_existing_does_not_overwrite(self) -> None:
        incoming = sample_claim(
            text="连续追问通常最能体现关心",
            relation_type="conflicts",
            relation_ids=[self.existing_claim["claim_id"]],
        )
        decision = [
            {
                "claim_id": incoming["claim_id"],
                "decision": "approve",
                "conflict_resolution": "keep_existing",
            }
        ]
        merged, outcomes = knowledge_merge.merge_approved_claims(
            self.store,
            [incoming],
            decision,
            sample_source(),
            "2026-08-21T08:00:00+00:00",
        )
        self.assertEqual(merged, self.store)
        self.assertEqual(outcomes[0]["action"], "keep_existing")

    def test_replace_requires_exact_claim_confirmation(self) -> None:
        incoming = sample_claim(
            text="可以用分享替代部分连续提问",
            relation_type="conflicts",
            relation_ids=[self.existing_claim["claim_id"]],
        )
        decision = [
            {
                "claim_id": incoming["claim_id"],
                "decision": "approve",
                "conflict_resolution": "replace_existing",
            }
        ]
        with self.assertRaises(knowledge_merge.KnowledgeMergeError):
            knowledge_merge.merge_approved_claims(
                self.store,
                [incoming],
                decision,
                sample_source(),
                "2026-08-21T08:00:00+00:00",
            )
        merged, _ = knowledge_merge.merge_approved_claims(
            self.store,
            [incoming],
            decision,
            sample_source(),
            "2026-08-21T08:00:00+00:00",
            {self.existing_claim["claim_id"]},
        )
        self.assertEqual([item["claim_id"] for item in merged["claims"]], [incoming["claim_id"]])


if __name__ == "__main__":
    unittest.main()
