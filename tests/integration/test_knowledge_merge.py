"""Reviewed proposal merge CLI tests."""

import json

from tests.knowledge_support import (
    MERGE_SCRIPT,
    KnowledgeCliCase,
    sample_claim,
    set_proposal_decision,
)


class KnowledgeMergeIntegrationTests(KnowledgeCliCase):
    def test_only_approved_claim_reaches_curated_runtime(self) -> None:
        self.register()
        approved = sample_claim(text="先分享能减少裸问题依赖")
        rejected = sample_claim(text="故意冷淡可以制造吸引")
        proposal = self.create_proposal([approved, rejected])
        set_proposal_decision(proposal, approved["claim_id"], "approve")
        set_proposal_decision(proposal, rejected["claim_id"], "reject")

        before_review = self.run_script(
            MERGE_SCRIPT, "merge", "--proposal", str(proposal), "--confirm"
        )
        self.assertNotEqual(before_review.returncode, 0)
        self.run_ok(
            MERGE_SCRIPT,
            "review",
            "--proposal",
            str(proposal),
            "--confirm",
            "--at",
            "2026-08-21T16:30:00+08:00",
        )
        merged = self.run_ok(
            MERGE_SCRIPT,
            "merge",
            "--proposal",
            str(proposal),
            "--confirm",
            "--at",
            "2026-08-21T16:40:00+08:00",
        )
        self.assertEqual(merged["curated_claims"], 1)
        store = json.loads(
            (self.project / "knowledge-management" / "CURATED_CLAIMS.json").read_text(
                encoding="utf-8"
            )
        )
        serialized = json.dumps(store, ensure_ascii=False)
        self.assertIn(approved["canonical_claim"], serialized)
        self.assertNotIn(rejected["canonical_claim"], serialized)
        runtime = (
            self.project / "references" / "curated" / "conversation.md"
        ).read_text(encoding="utf-8")
        self.assertIn(approved["claim_id"], runtime)
        self.assertNotIn(rejected["claim_id"], runtime)


if __name__ == "__main__":
    import unittest

    unittest.main()
