"""Regression tests for multi-field contract expectation validation."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_contract_evals  # noqa: E402


class ContractEvalRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = run_contract_evals.load_cases()

    def test_all_required_expectations_pass(self) -> None:
        run_contract_evals.validate_cases(self.cases)

    def test_wrong_secondary_expectation_fails(self) -> None:
        cases = copy.deepcopy(self.cases)
        serious = next(case for case in cases if case["category"] == "serious_mode")
        serious["expected"]["tease_or_flirt"] = True

        with self.assertRaisesRegex(
            run_contract_evals.ContractError,
            "expected tease_or_flirt=False",
        ):
            run_contract_evals.validate_cases(cases)

    def test_missing_required_expectation_fails(self) -> None:
        cases = copy.deepcopy(self.cases)
        serious = next(case for case in cases if case["category"] == "serious_mode")
        del serious["expected"]["forced_positivity"]

        with self.assertRaisesRegex(
            run_contract_evals.ContractError,
            "missing required expectation forced_positivity",
        ):
            run_contract_evals.validate_cases(cases)


if __name__ == "__main__":
    unittest.main()
