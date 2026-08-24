"""Source registry contract tests."""

import copy
import json
import unittest
from pathlib import Path

from tests.support import SCRIPTS

import knowledge_schema


ROOT = SCRIPTS.parent


def sample_source(source_id: str = "src-example-book", fingerprint: str = "a" * 64) -> dict:
    return {
        "source_id": source_id,
        "title": "Example",
        "author": "Author",
        "source_type": "book",
        "publication_year": 2024,
        "edition": "1",
        "language": "zh-CN",
        "topics": ["conversation"],
        "source_quality": "medium",
        "added_at": "2026-08-21T16:00:00+08:00",
        "last_reviewed_at": "2026-08-21T16:00:00+08:00",
        "freshness": "stable",
        "status": "candidate",
        "content_fingerprint": fingerprint,
    }


class SourceRegistryTests(unittest.TestCase):
    def test_repository_registry_and_json_schema_are_valid_json(self) -> None:
        registry = json.loads(
            (ROOT / "knowledge-management" / "SOURCE_REGISTRY.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(knowledge_schema.validate_registry(registry), registry)
        schema = json.loads(
            (ROOT / "knowledge-management" / "schemas" / "source-registry.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("content_fingerprint", schema["$defs"]["source"]["required"])

    def test_duplicate_id_and_fingerprint_are_rejected(self) -> None:
        source = sample_source()
        duplicate_id = copy.deepcopy(source)
        duplicate_id["content_fingerprint"] = "b" * 64
        with self.assertRaises(knowledge_schema.KnowledgeSchemaError):
            knowledge_schema.validate_registry(
                {"schema_version": 1, "sources": [source, duplicate_id]}
            )

        duplicate_content = sample_source("src-another-book")
        with self.assertRaises(knowledge_schema.KnowledgeSchemaError):
            knowledge_schema.validate_registry(
                {"schema_version": 1, "sources": [source, duplicate_content]}
            )

    def test_source_quality_is_not_claim_evidence_or_local_path(self) -> None:
        source = sample_source()
        source["claim_evidence"] = "A"
        with self.assertRaises(knowledge_schema.KnowledgeSchemaError):
            knowledge_schema.validate_source(source)
        source = sample_source()
        source["local_path"] = "private-source"
        with self.assertRaises(knowledge_schema.KnowledgeSchemaError):
            knowledge_schema.validate_source(source)


if __name__ == "__main__":
    unittest.main()
