"""Human approval gate CLI tests."""

from tests.knowledge_support import MERGE_SCRIPT, KnowledgeCliCase, sample_claim


class KnowledgeApproveGateIntegrationTests(KnowledgeCliCase):
    def test_review_requires_confirmation_and_exactly_one_checkbox(self) -> None:
        self.register()
        proposal = self.create_proposal([sample_claim()])
        no_confirm = self.run_script(
            MERGE_SCRIPT, "review", "--proposal", str(proposal)
        )
        self.assertNotEqual(no_confirm.returncode, 0)
        unchecked = self.run_script(
            MERGE_SCRIPT, "review", "--proposal", str(proposal), "--confirm"
        )
        self.assertNotEqual(unchecked.returncode, 0)

        proposal.write_text(
            proposal.read_text(encoding="utf-8").replace(
                "[ ] approve [ ] reject [ ] revise",
                "[x] approve [ ] reject [ ] revise",
            ),
            encoding="utf-8",
        )
        reviewed = self.run_ok(
            MERGE_SCRIPT,
            "review",
            "--proposal",
            str(proposal),
            "--confirm",
            "--at",
            "2026-08-21T16:30:00+08:00",
        )
        self.assertEqual(reviewed["review"]["decisions"][0]["decision"], "approve")

        proposal.write_text(
            proposal.read_text(encoding="utf-8").replace(
                "- Reason: 待审核", "- Reason: 审核后被修改"
            ),
            encoding="utf-8",
        )
        changed = self.run_script(
            MERGE_SCRIPT, "merge", "--proposal", str(proposal), "--confirm"
        )
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("changed after review", changed.stdout)


if __name__ == "__main__":
    import unittest

    unittest.main()

# Modified by AI on 2026-08-21 17:06:16
