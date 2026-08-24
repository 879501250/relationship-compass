"""Candidate claim schema tests."""

import json
import unittest

from tests.support import SCRIPTS

import knowledge_schema


ROOT = SCRIPTS.parent


def sample_claim(text: str = "先分享自己的真实内容通常比连续裸问题更自然") -> dict:
    return {
        "claim_id": knowledge_schema.stable_claim_id(text),
        "canonical_claim": text,
        "source_id": "src-example-book",
        "source_anchor": "chapter 2, p. 18",
        "claim_type": "practical_heuristic",
        "claim_evidence": "C",
        "confidence": "medium",
        "applicable_when": ["普通低风险聊天"],
        "not_applicable_when": ["对方明确要求停止联系"],
        "risk_of_misuse": ["把分享变成表演"],
        "relation_to_existing": {"type": "new", "claim_ids": []},
        "proposed_destination": "conversation",
    }


class ClaimSchemaTests(unittest.TestCase):
    def test_claim_and_declarative_schema(self) -> None:
        claim = sample_claim()
        self.assertEqual(knowledge_schema.validate_claim(claim), claim)
        schema = json.loads(
            (ROOT / "knowledge-management" / "schemas" / "claim.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("risk_of_misuse", schema["required"])

    def test_claim_id_is_stable_for_spacing_and_terminal_punctuation(self) -> None:
        first = knowledge_schema.stable_claim_id("先分享，再提问。")
        second = knowledge_schema.stable_claim_id("  先分享，再提问  ")
        self.assertEqual(first, second)

    def test_invalid_evidence_and_mismatched_id_are_rejected(self) -> None:
        claim = sample_claim()
        claim["claim_evidence"] = "high"
        with self.assertRaises(knowledge_schema.KnowledgeSchemaError):
            knowledge_schema.validate_claim(claim)
        claim = sample_claim()
        claim["claim_id"] = "claim-0000000000000000"
        with self.assertRaises(knowledge_schema.KnowledgeSchemaError):
            knowledge_schema.validate_claim(claim)


if __name__ == "__main__":
    unittest.main()
