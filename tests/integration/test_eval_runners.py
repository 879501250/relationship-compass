"""Contract/model-eval boundary tests with bounded subprocesses."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUBPROCESS_TIMEOUT = 10


class EvalRunnerTests(unittest.TestCase):
    def run_python(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, *args],
            cwd=ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SUBPROCESS_TIMEOUT,
        )

    def test_contract_eval_explicitly_does_not_run_model(self) -> None:
        result = self.run_python("scripts/run_contract_evals.py")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("no model behavior was executed", result.stdout)

    def test_model_eval_validation_reports_not_run(self) -> None:
        result = self.run_python("scripts/run_model_evals.py", "validate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("behavioral evaluation NOT RUN", result.stdout)

    def test_incomplete_model_judgment_cannot_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_items = Path(temp_dir) / "items.jsonl"
            prepared = self.run_python(
                "scripts/run_model_evals.py", "prepare", "--output", str(work_items)
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            first = json.loads(work_items.read_text(encoding="utf-8").splitlines()[0])
            responses = Path(temp_dir) / "responses.jsonl"
            judgments = Path(temp_dir) / "judgments.jsonl"
            responses.write_text(
                json.dumps({"case_id": first["case_id"], "output": "sample"}) + "\n",
                encoding="utf-8",
            )
            judgments.write_text(
                json.dumps({"case_id": first["case_id"], "judge": "human", "criteria": {}})
                + "\n",
                encoding="utf-8",
            )
            judged = self.run_python(
                "scripts/run_model_evals.py",
                "judge",
                "--responses",
                str(responses),
                "--judgments",
                str(judgments),
            )
            self.assertNotEqual(judged.returncode, 0)
            self.assertIn("every case exactly once", judged.stdout)


if __name__ == "__main__":
    unittest.main()

# Modified by AI on 2026-08-21 14:47:55
