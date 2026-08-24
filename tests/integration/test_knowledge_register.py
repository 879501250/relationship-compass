"""Knowledge source registration CLI tests."""

import json

from tests.knowledge_support import INTAKE_SCRIPT, KnowledgeCliCase


class KnowledgeRegisterIntegrationTests(KnowledgeCliCase):
    def test_register_validate_status_list_and_duplicate_gate(self) -> None:
        registered = self.register()
        self.assertFalse(registered["raw_copied"])
        public_registry = json.loads(
            (self.project / "knowledge-management" / "SOURCE_REGISTRY.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("path", json.dumps(public_registry))
        self.assertTrue(
            (self.project / "knowledge-management" / "SOURCE_REGISTRY.local.json").is_file()
        )
        self.assertEqual(self.run_ok(INTAKE_SCRIPT, "validate")["sources"], 1)
        self.assertEqual(self.run_ok(INTAKE_SCRIPT, "status")["sources"], 1)
        self.assertEqual(self.run_ok(INTAKE_SCRIPT, "list")["count"], 1)

        duplicate = self.run_script(
            INTAKE_SCRIPT,
            "register",
            str(self.raw),
            "--source-id",
            "src-duplicate-book",
            "--title",
            "Duplicate",
            "--author",
            "Author",
            "--source-type",
            "book",
            "--publication-year",
            "2024",
            "--topics",
            "conversation",
            "--freshness",
            "stable",
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("duplicate source content", duplicate.stdout)

        no_confirm = self.run_script(
            INTAKE_SCRIPT, "deprecate", "--source-id", "src-example-book"
        )
        self.assertNotEqual(no_confirm.returncode, 0)
        deprecated = self.run_ok(
            INTAKE_SCRIPT,
            "deprecate",
            "--source-id",
            "src-example-book",
            "--confirm",
        )
        self.assertEqual(deprecated["status"], "deprecated")


if __name__ == "__main__":
    import unittest

    unittest.main()
