"""Curated claim deduplication tests."""

import unittest

from tests.knowledge_support import sample_claim, sample_source

import knowledge_merge


class ClaimDedupTests(unittest.TestCase):
    def test_same_canonical_claim_merges_source_provenance(self) -> None:
        first = sample_claim()
        first_decision = [{"claim_id": first["claim_id"], "decision": "approve"}]
        store, _ = knowledge_merge.merge_approved_claims(
            {"schema_version": 1, "claims": []},
            [first],
            first_decision,
            sample_source(),
            "2026-08-21T08:00:00+00:00",
        )
        second = sample_claim("src-second-book")
        second["relation_to_existing"] = {
            "type": "duplicate",
            "claim_ids": [first["claim_id"]],
        }
        second_decision = [{"claim_id": second["claim_id"], "decision": "approve"}]
        second_source = sample_source("src-second-book")
        second_source["content_fingerprint"] = "b" * 64
        merged, outcomes = knowledge_merge.merge_approved_claims(
            store,
            [second],
            second_decision,
            second_source,
            "2026-08-21T08:00:00+00:00",
        )

        self.assertEqual(len(merged["claims"]), 1)
        self.assertEqual(
            {item["source_id"] for item in merged["claims"][0]["sources"]},
            {"src-example-book", "src-second-book"},
        )
        self.assertEqual(outcomes[0]["action"], "merged_provenance")


if __name__ == "__main__":
    unittest.main()

# Modified by AI on 2026-08-21 16:53:25
