"""Source fingerprint tests."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from tests.support import SCRIPTS  # noqa: F401

import knowledge_schema


class SourceFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_sha256_of_exact_bytes(self) -> None:
        payload = "同一来源\n固定内容".encode("utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.bin"
            path.write_bytes(payload)
            self.assertEqual(
                knowledge_schema.fingerprint_file(path), hashlib.sha256(payload).hexdigest()
            )


if __name__ == "__main__":
    unittest.main()

# Modified by AI on 2026-08-21 16:44:58
