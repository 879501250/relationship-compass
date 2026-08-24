"""Repository identity, filename, and generated-artifact convergence checks."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEXT_SUFFIXES = {".md", ".py", ".yaml", ".yml", ".json", ".txt"}


class RepositoryConvergenceTests(unittest.TestCase):
    def repository_files(self) -> list[Path]:
        return [
            path
            for path in ROOT.rglob("*")
            if path.is_file() and ".git" not in path.parts
        ]

    def test_official_paths_have_valid_unicode_names(self) -> None:
        encoded = re.compile(r"#U[0-9A-Fa-f]{4}")
        invalid = [
            path.relative_to(ROOT).as_posix()
            for path in self.repository_files()
            if any(encoded.search(part) for part in path.relative_to(ROOT).parts)
        ]
        self.assertEqual(invalid, [])

    def test_repository_has_no_generated_edit_markers(self) -> None:
        marker = "Modified" + " by AI"
        invalid = []
        for path in self.repository_files():
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            if marker in path.read_text(encoding="utf-8"):
                invalid.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(invalid, [])

    def test_only_current_product_identity_is_present(self) -> None:
        retired_slug = "goutoujunshi" + "-personal"
        retired_env = "GOUTOUJUNSHI" + "_PERSONAL"
        invalid = []
        for path in self.repository_files():
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            content = path.read_text(encoding="utf-8")
            if retired_slug in content or retired_env in content:
                invalid.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(invalid, [])

    def test_generated_knowledge_is_the_only_chatgpt_pack(self) -> None:
        self.assertFalse((ROOT / "chatgpt-project" / "knowledge").exists())
        generated = ROOT / "chatgpt-project" / "generated-knowledge"
        self.assertTrue((generated / "KNOWLEDGE_PACK_INFO.json").is_file())


if __name__ == "__main__":
    unittest.main()
