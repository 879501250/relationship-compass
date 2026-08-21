"""Proposal generation and validation CLI tests."""

from tests.knowledge_support import INTAKE_SCRIPT, KnowledgeCliCase, sample_claim


class ProposalValidationIntegrationTests(KnowledgeCliCase):
    def test_generated_proposal_validates_and_corruption_fails(self) -> None:
        self.register()
        proposal = self.create_proposal([sample_claim()])
        result = self.run_ok(INTAKE_SCRIPT, "validate", "--proposal", str(proposal))
        self.assertEqual(result["proposals"], 1)

        content = proposal.read_text(encoding="utf-8").replace(
            '"claim_evidence": "C"', '"claim_evidence": "high"'
        )
        proposal.write_text(content, encoding="utf-8")
        invalid = self.run_script(INTAKE_SCRIPT, "validate", "--proposal", str(proposal))
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("invalid claim", invalid.stdout)


if __name__ == "__main__":
    import unittest

    unittest.main()

# Modified by AI on 2026-08-21 16:53:25
