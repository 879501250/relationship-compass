"""Regression tests for Behavioral Core/Stress definitions and calibration."""

from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as runner  # noqa: E402


HISTORICAL_RUN = (
    ROOT
    / "model_evals"
    / "results"
    / "v1.6.0"
    / runner.CHATGPT_RUNTIME_PROFILE
    / "baseline-manual-20260825-01"
)


class ModelEvalDefinitionTests(unittest.TestCase):
    def test_core_ids_and_stress_inventory_are_frozen(self) -> None:
        cases, _ = runner.load_definitions()
        core_ids = tuple(case["id"] for case in cases if case["suite"] == "core")
        stress = [case for case in cases if case["suite"] == "stress"]

        self.assertEqual(core_ids, runner.CORE_CASE_IDS)
        self.assertEqual(len(core_ids), 19)
        self.assertGreaterEqual(len(stress), 8)
        self.assertEqual({case["classification"] for case in cases if case["suite"] == "core"}, {"explicit"})
        self.assertEqual(
            {case["classification"] for case in stress},
            runner.STRESS_CLASSIFICATIONS,
        )

    def test_stress_case_applicability_fixes_are_preserved(self) -> None:
        cases, _ = runner.load_definitions()
        indexed = {case["id"]: case for case in cases}
        preference = indexed["stress-first-person-preference-fabrication"]
        self.assertNotIn("prefer_fact_independent_reply", preference["required_criteria"])
        profile = indexed["stress-profile-confirmation-emerging-pattern"]
        self.assertIn("表达风格有什么变化", profile["prompt"])
        self.assertNotIn("直接把这套风格记成", profile["prompt"])

    def test_case_ids_are_unique_and_all_criteria_exist(self) -> None:
        cases, criteria = runner.load_definitions()
        case_ids = [case["id"] for case in cases]
        self.assertEqual(len(case_ids), len(set(case_ids)))
        for case in cases:
            self.assertTrue(set(case["required_criteria"]).issubset(criteria), case["id"])

    def test_duplicate_case_id_is_rejected(self) -> None:
        definitions = runner.load_json_yaml(runner.CASES_PATH)
        definitions["cases"].append(copy.deepcopy(definitions["cases"][0]))
        with tempfile.TemporaryDirectory() as temp_dir:
            cases_path = Path(temp_dir) / "cases.yaml"
            cases_path.write_text(
                json.dumps(definitions, ensure_ascii=False), encoding="utf-8"
            )
            with mock.patch.object(runner, "CASES_PATH", cases_path):
                with self.assertRaisesRegex(runner.ModelEvalError, "duplicate case id"):
                    runner.load_definitions()

    def test_unknown_criterion_reference_is_rejected(self) -> None:
        definitions = runner.load_json_yaml(runner.CASES_PATH)
        definitions["cases"][0]["required_criteria"][0] = "missing-criterion"
        with tempfile.TemporaryDirectory() as temp_dir:
            cases_path = Path(temp_dir) / "cases.yaml"
            cases_path.write_text(
                json.dumps(definitions, ensure_ascii=False), encoding="utf-8"
            )
            with mock.patch.object(runner, "CASES_PATH", cases_path):
                with self.assertRaisesRegex(runner.ModelEvalError, "unknown criteria"):
                    runner.load_definitions()

    def test_stress_suite_has_no_permanent_maximum_cap(self) -> None:
        definitions = runner.load_json_yaml(runner.CASES_PATH)
        stress = next(case for case in definitions["cases"] if case["suite"] == "stress")
        for suffix in ("extension-a", "extension-b"):
            added = copy.deepcopy(stress)
            added["id"] = f"stress-{suffix}"
            added["title"] = suffix
            definitions["cases"].append(added)
        with tempfile.TemporaryDirectory() as temp_dir:
            cases_path = Path(temp_dir) / "cases.yaml"
            cases_path.write_text(
                json.dumps(definitions, ensure_ascii=False), encoding="utf-8"
            )
            with mock.patch.object(runner, "CASES_PATH", cases_path), mock.patch.object(
                runner, "validate_runtime_routes"
            ):
                cases, _ = runner.load_definitions()
        self.assertEqual(len([case for case in cases if case["suite"] == "stress"]), 13)

    def test_high_risk_criteria_have_core_and_stress_coverage(self) -> None:
        cases, _ = runner.load_definitions()
        counts: Counter[str] = Counter()
        suites: dict[str, set[str]] = defaultdict(set)
        for case in cases:
            for criterion in case["required_criteria"]:
                counts[criterion] += 1
                suites[criterion].add(case["suite"])

        high_risk = {
            "use_real_user_content",
            "explicit_boundary_remains_valid",
            "no_mind_reading",
            "stage_trend_separated",
            "return_continuation_ownership",
            "minimal_change_if_needed",
            "confirmation_before_profile_update",
            "one_best_reply",
            "ask_one_high_value_question",
        }
        for criterion in high_risk:
            self.assertGreaterEqual(counts[criterion], 2, criterion)
            self.assertEqual(suites[criterion], {"core", "stress"}, criterion)

    def test_definition_hash_changes_for_semantic_eval_change(self) -> None:
        original = runner.eval_definition_snapshot()
        changed = copy.deepcopy(original)
        changed["criteria"]["use_real_user_content"]["question"] += " 新约束"
        self.assertNotEqual(
            runner.eval_definition_hash(original), runner.eval_definition_hash(changed)
        )

    def test_judge_calibration_rejects_generic_reason(self) -> None:
        criteria = [{"criterion": "fact_safety", "question": "是否忠于事实？"}]
        payload = json.dumps(
            {
                "criteria": [
                    {
                        "criterion": "fact_safety",
                        "passed": True,
                        "reason": "符合要求。",
                    }
                ]
            },
            ensure_ascii=False,
        )
        with self.assertRaisesRegex(runner.ModelEvalError, "concrete Target evidence"):
            runner.parse_judgment(payload, criteria)
        self.assertIn("第一人称确定陈述", runner.JUDGE_CALIBRATION)
        self.assertIn("逐项独立判断", runner.JUDGE_CALIBRATION)
        self.assertIn("空泛 filler", runner.JUDGE_CALIBRATION)

    def test_historical_run_is_compatible_and_validation_is_read_only(self) -> None:
        self.assertTrue(HISTORICAL_RUN.is_dir())
        before = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in HISTORICAL_RUN.iterdir()
            if path.is_file()
        }
        runner.validate_result_artifacts(HISTORICAL_RUN)
        after = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in HISTORICAL_RUN.iterdir()
            if path.is_file()
        }
        self.assertEqual(after, before)
        historical_definition = runner.load_json_object(
            HISTORICAL_RUN / "eval-definition.json"
        )
        self.assertEqual(
            tuple(case["id"] for case in historical_definition["cases"]),
            runner.CORE_CASE_IDS,
        )
        current_cases, _ = runner.load_definitions()
        current_core = {
            case["id"]: case for case in current_cases if case["suite"] == "core"
        }
        preserved_fields = ("id", "title", "mode", "prompt", "required_criteria")
        for historical_case in historical_definition["cases"]:
            current_case = current_core[historical_case["id"]]
            self.assertEqual(
                {field: current_case[field] for field in preserved_fields},
                {field: historical_case[field] for field in preserved_fields},
                historical_case["id"],
            )


if __name__ == "__main__":
    unittest.main()
