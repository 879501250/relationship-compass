"""Portable ZIP packaging checks, including Windows-friendly Unicode paths."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from package_skill import build_zip  # noqa: E402


class SkillPackagingTests(unittest.TestCase):
    def test_zip_round_trip_preserves_chinese_english_and_nested_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temporary_root = Path(temp_dir)
            source = temporary_root / "source"
            chinese = source / "资料" / "nested" / "中文说明.md"
            english = source / "English" / "guide.txt"
            chinese.parent.mkdir(parents=True)
            english.parent.mkdir(parents=True)
            chinese.write_text("中文内容", encoding="utf-8")
            english.write_text("English content", encoding="utf-8")
            archive_path = temporary_root / "out" / "relationship-compass.zip"

            entries = build_zip(source, archive_path)

            self.assertEqual(entries, ["English/guide.txt", "资料/nested/中文说明.md"])
            with ZipFile(archive_path) as archive:
                self.assertTrue(archive.getinfo("资料/nested/中文说明.md").flag_bits & 0x800)
                archive.extractall(temporary_root / "extracted")
            extracted = temporary_root / "extracted"
            self.assertEqual(
                (extracted / "资料" / "nested" / "中文说明.md").read_text(encoding="utf-8"),
                "中文内容",
            )
            self.assertEqual(
                (extracted / "English" / "guide.txt").read_text(encoding="utf-8"),
                "English content",
            )


if __name__ == "__main__":
    unittest.main()
