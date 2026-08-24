"""ChatGPT knowledge pack CLI integration test."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_chatgpt_pack.py"


class ChatGptPackCliIntegrationTests(unittest.TestCase):
    def test_build_writes_six_safe_themes_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "pack"
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "utf-8"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SCRIPT),
                    "--root",
                    str(ROOT),
                    "--output",
                    str(output),
                    "--built-at",
                    "2026-08-21T17:00:00+08:00",
                ],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["theme_files"]), 6)
            info = json.loads(
                (output / "KNOWLEDGE_PACK_INFO.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                set(info),
                {
                    "pack_version",
                    "built_at",
                    "skill_revision",
                    "curated_revision",
                    "source_registry_revision",
                    "included_claim_ids",
                    "included_sources",
                },
            )
            serialized = "\n".join(
                path.read_text(encoding="utf-8") for path in output.glob("*.md")
            )
            self.assertNotIn("memory_store.py", serialized)
            self.assertNotIn("SOURCE_REGISTRY.local.json", serialized)
            self.assertNotIn("knowledge-management/proposals", serialized)


if __name__ == "__main__":
    unittest.main()
