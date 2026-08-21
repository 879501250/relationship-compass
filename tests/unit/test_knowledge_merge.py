"""Knowledge merge gate unit tests."""

import unittest

from tests.knowledge_support import sample_claim, sample_source

import knowledge_merge


class KnowledgeMergeTests(unittest.TestCase):
    def test_unapproved_and_rejected_claims_never_enter_curated_store(self) -> None:
        claims = [
            sample_claim(text="approved claim"),
            sample_claim(text="rejected claim"),
            sample_claim(text="revision pending claim"),
        ]
        decisions = [
            {"claim_id": claims[0]["claim_id"], "decision": "approve"},
            {"claim_id": claims[1]["claim_id"], "decision": "reject"},
        ]
        store, outcomes = knowledge_merge.merge_approved_claims(
            {"schema_version": 1, "claims": []},
            claims,
            decisions,
            sample_source(),
            "2026-08-21T08:00:00+00:00",
        )

        self.assertEqual([item["claim_id"] for item in store["claims"]], [claims[0]["claim_id"]])
        actions = {item["claim_id"]: item["action"] for item in outcomes}
        self.assertEqual(actions[claims[0]["claim_id"]], "added")
        self.assertEqual(actions[claims[1]["claim_id"]], "not_approved")
        self.assertEqual(actions[claims[2]["claim_id"]], "not_approved")


if __name__ == "__main__":
    unittest.main()

# Modified by AI on 2026-08-21 16:56:24
