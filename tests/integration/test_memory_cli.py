"""CLI boundary tests for Memory Store."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "memory_store.py"
SUBPROCESS_TIMEOUT = 10


class MemoryCliIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env = os.environ.copy()
        self.env["GOUTOUJUNSHI_PERSONAL_MEMORY_DIR"] = self.temp_dir.name
        self.env["PYTHONIOENCODING"] = "utf-8"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_raw(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *args],
            cwd=ROOT,
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SUBPROCESS_TIMEOUT,
        )

    def run_cli(self, *args: str) -> dict[str, Any]:
        result = self.run_raw(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_context_requires_subject_id_at_cli_boundary(self) -> None:
        result = self.run_raw("context")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--subject-id", result.stderr)

    def test_cli_round_trip_preserves_object_isolation(self) -> None:
        self.run_cli("enable", "--confirm")
        for subject_id, value in (("obj-a", "A-only"), ("obj-b", "B-only")):
            delta = {
                "scope": "object",
                "subject_id": subject_id,
                "field": "nickname",
                "value": value,
                "source_type": "user_report",
                "source_ref": "test:cli",
                "confidence": "high",
            }
            self.run_cli("apply", "--json", json.dumps(delta))
        context = self.run_cli("context", "--subject-id", "obj-a")
        serialized = json.dumps(context, ensure_ascii=False)
        self.assertIn("A-only", serialized)
        self.assertNotIn("B-only", serialized)


if __name__ == "__main__":
    unittest.main()

# Modified by AI on 2026-08-21 14:47:55
