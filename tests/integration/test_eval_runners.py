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
    def run_python(
        self, *args: str, without_credentials: bool = False
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        if without_credentials:
            for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_JUDGE_MODEL"):
                env.pop(name, None)
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

    def test_prepare_assembles_runtime_and_missing_credentials_do_not_create_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            work_items = Path(temp_dir) / "items.jsonl"
            prepared = self.run_python(
                "scripts/run_model_evals.py", "prepare", "--output", str(work_items)
            )
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            first = json.loads(work_items.read_text(encoding="utf-8").splitlines()[0])
            self.assertIn("SKILL.md", first["runtime"]["sources"])
            self.assertIn("relationship-compass", first["runtime"]["content"])
            results_root = Path(temp_dir) / "results"
            executed = self.run_python(
                "scripts/run_model_evals.py",
                "run",
                "--prepared",
                str(work_items),
                "--results-root",
                str(results_root),
                "--run-id",
                "missing-credentials",
                "--model",
                "explicit-test-model",
                without_credentials=True,
            )
            self.assertNotEqual(executed.returncode, 0)
            self.assertIn("behavioral evaluation NOT RUN", executed.stdout)
            self.assertFalse(results_root.exists())


if __name__ == "__main__":
    unittest.main()
