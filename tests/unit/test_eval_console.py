"""Focused tests for Eval Console discovery, selection, and subset execution."""

from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as runner  # noqa: E402
from eval_console.cli import _ActivityReporter, _result_label  # noqa: E402
from eval_console.discovery import discover_evals, discover_provider_profiles, discover_runs  # noqa: E402
from eval_console.models import CURRENT_CONSOLE_SCHEMA_VERSION, EvalRunRequest  # noqa: E402
from eval_console.selection import CaseSelectionError, parse_case_selection  # noqa: E402
from eval_console.service import execute_request, validate_request  # noqa: E402


class RecordingProvider:
    """Small in-process provider that records every target and judge request."""

    provider_name = "console-test"
    model = "console-test-model"
    public_parameters = {"network": False, "single_sample": True}

    def __init__(self, target_outputs: list[Any] | None = None) -> None:
        self.target_outputs = list(target_outputs or [])
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        self.calls.append({"input": input_text, "structured": response_schema is not None})
        if response_schema is None:
            outcome = self.target_outputs.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return runner.ProviderResult(str(outcome), reported_model=self.model)
        return runner.ProviderResult(
            passing_judgment(response_schema),
            reported_model=self.model,
        )


class MalformedFirstJudge(RecordingProvider):
    """Returns malformed judge JSON once, then proves later cases still execute."""

    def __init__(self) -> None:
        super().__init__()
        self.judge_attempts = 0

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        if response_schema is None:
            return super().generate(
                instructions=instructions,
                input_text=input_text,
                response_schema=response_schema,
            )
        self.calls.append({"input": input_text, "structured": True})
        self.judge_attempts += 1
        text = "not JSON" if self.judge_attempts == 1 else passing_judgment(response_schema)
        return runner.ProviderResult(text, reported_model=self.model)


def passing_judgment(response_schema: dict[str, Any]) -> str:
    """Build an independently parseable all-pass response from the requested schema."""
    case_id = response_schema["properties"]["case_id"]["const"]
    criteria = response_schema["properties"]["criteria"]["items"]["properties"]["criterion"]["enum"]
    return json.dumps(
        {
            "case_id": case_id,
            "criteria": [
                {
                    "criterion": criterion,
                    "passed": True,
                    "reason": "Target 原文包含可核对的对应内容。",
                }
                for criterion in criteria
            ],
        },
        ensure_ascii=False,
    )


class CaseSelectionTests(unittest.TestCase):
    case_ids = ("RC-001", "RC-002", "RC-003", "RC-004", "RC-005", "RC-006")

    def test_all_single_multiple_range_and_mixed_selection(self) -> None:
        expectations = {
            "all": list(self.case_ids),
            "2": ["RC-002"],
            "RC-003": ["RC-003"],
            "1,3,5": ["RC-001", "RC-003", "RC-005"],
            "2-4": ["RC-002", "RC-003", "RC-004"],
            "RC-002..RC-004": ["RC-002", "RC-003", "RC-004"],
            "1,3,5-6": ["RC-001", "RC-003", "RC-005", "RC-006"],
        }
        for expression, expected in expectations.items():
            with self.subTest(expression=expression):
                self.assertEqual(parse_case_selection(expression, self.case_ids), expected)

    def test_invalid_duplicate_out_of_range_reversed_and_empty_are_friendly(self) -> None:
        for expression, message in (
            ("", "不能为空"),
            ("7", "超出范围"),
            ("unknown", "未知 Case"),
            ("4-2", "起始编号不能大于结束编号"),
            ("1,1", "重复选择"),
        ):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(CaseSelectionError, message):
                    parse_case_selection(expression, self.case_ids)


class DiscoveryAndExecutionTests(unittest.TestCase):
    def write_profiles(self, directory: Path) -> Path:
        profiles = directory / "profiles.json"
        profiles.write_text(
            json.dumps(
                {
                    "profiles": {
                        "fake": {
                            "provider": "openai_responses",
                            "target": {"model": "target-test"},
                            "judge": {"model": "judge-test"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return profiles

    def test_discovery_uses_current_eval_and_profile_files(self) -> None:
        definitions = discover_evals()
        self.assertEqual(len(definitions), 1)
        self.assertGreater(len(definitions[0].cases), 0)
        self.assertTrue(definitions[0].source_path.is_file())
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = discover_provider_profiles(self.write_profiles(Path(temp_dir)))
        self.assertEqual([(item.name, item.supports_target, item.supports_judge) for item in profiles], [("fake", True, True)])

    def test_selected_subset_is_the_only_provider_and_judge_work(self) -> None:
        definition = discover_evals()[0]
        selected_ids = [
            definition.cases[0].case_id,
            definition.cases[2].case_id,
            definition.cases[5].case_id,
        ]
        cases, criteria = runner.load_definitions()
        selected_records = [case for case in cases if case["id"] in set(selected_ids)]
        expected_inputs = [
            runner.target_input(record)
            for record in runner.prepare_cases(selected_records, criteria)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = self.write_profiles(root)
            request = EvalRunRequest(
                eval_id=definition.eval_id,
                case_ids=tuple(selected_ids),
                target_profile="fake",
                judge_profile="fake",
                profiles_file=profiles,
                results_root=root / "results",
                allow_dirty_debug=True,
                run_id="selected-subset",
            )
            validate_request(request)
            target = RecordingProvider(["answer 1", "answer 2", "answer 3"])
            judge = RecordingProvider()
            outcome = execute_request(
                request, target_provider=target, judge_provider=judge
            )
            self.assertEqual([call["input"] for call in target.calls], expected_inputs)
            self.assertEqual(len(target.calls), 3)
            self.assertEqual(len(judge.calls), 3)
            self.assertTrue(all(call["structured"] for call in judge.calls))
            self.assertEqual(outcome.summary["counts"]["total_cases"], 3)
            metadata = runner.load_json_object(outcome.run_dir / "run.json")
            self.assertEqual(metadata["console"]["selected_case_ids"], selected_ids)
            self.assertEqual(metadata["console"]["selected_cases"], 3)
            self.assertEqual(metadata["console"]["total_eval_cases"], len(definition.cases))
            self.assertEqual(
                [record["case_id"] for record in runner.load_jsonl(outcome.run_dir / "responses.jsonl")],
                selected_ids,
            )
            log_records = [
                json.loads(line)
                for line in (outcome.run_dir / "run.log").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(log_records)
            self.assertTrue(
                {
                    "timestamp",
                    "run_id",
                    "eval_id",
                    "phase",
                    "provider",
                    "model",
                    "judge_profile",
                    "duration_seconds",
                }.issubset(log_records[0])
            )
            runner.validate_result_artifacts(outcome.run_dir)

    def test_target_errors_continue_to_later_selected_cases_and_are_persisted(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases[:2], criteria)
        provider = RecordingProvider(
            [runner.ProviderError("temporary failure", code="NETWORK_ERROR", retryable=True), "answer"]
        )
        callbacks: list[tuple[str, int, int]] = []
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "v1.6.0" / runner.API_RUNTIME_PROFILE / "continue-errors"
            metadata = runner.execute_run(
                prepared,
                provider,
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
                continue_on_error=True,
                on_case_complete=lambda record, done, total: callbacks.append(
                    (record["case_id"], done, total)
                ),
            )
            responses = runner.load_jsonl(run_dir / "responses.jsonl")
            self.assertEqual(len(provider.calls), 2)
            self.assertEqual([record["status"] for record in responses], ["TARGET_ERROR", "MODEL_RESPONSE"])
            self.assertEqual(callbacks, [(prepared[0]["case_id"], 1, 2), (prepared[1]["case_id"], 2, 2)])
            self.assertEqual(metadata["counts"]["attempted_cases"], 2)

    def test_partial_target_run_judges_each_successful_response(self) -> None:
        definition = discover_evals()[0]
        selected_ids = tuple(case.case_id for case in definition.cases[:2])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request = EvalRunRequest(
                eval_id=definition.eval_id,
                case_ids=selected_ids,
                target_profile="fake",
                judge_profile="fake",
                profiles_file=self.write_profiles(root),
                results_root=root / "results",
                allow_dirty_debug=True,
                run_id="partial-target-two-cases",
            )
            target = RecordingProvider(
                [
                    runner.ProviderError("target unavailable", code="NETWORK_ERROR", retryable=True),
                    "second answer",
                ]
            )
            judge = RecordingProvider()
            outcome = execute_request(request, target_provider=target, judge_provider=judge)
            self.assertEqual(len(target.calls), 2)
            self.assertEqual(len(judge.calls), 1)
            self.assertEqual(
                [record["case_id"] for record in runner.load_jsonl(outcome.run_dir / "judgments.jsonl")],
                [selected_ids[1]],
            )
            self.assertEqual(outcome.summary["completion_status"], "COMPLETED_WITH_ERRORS")
            runner.validate_result_artifacts(outcome.run_dir)

    def test_partial_target_run_judges_two_successes_around_one_error(self) -> None:
        definition = discover_evals()[0]
        selected_ids = tuple(case.case_id for case in definition.cases[:3])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request = EvalRunRequest(
                eval_id=definition.eval_id,
                case_ids=selected_ids,
                target_profile="fake",
                judge_profile="fake",
                profiles_file=self.write_profiles(root),
                results_root=root / "results",
                allow_dirty_debug=True,
                run_id="partial-target-three-cases",
            )
            target = RecordingProvider(
                [
                    "first answer",
                    runner.ProviderError("target unavailable", code="NETWORK_ERROR", retryable=True),
                    "third answer",
                ]
            )
            judge = RecordingProvider()
            outcome = execute_request(request, target_provider=target, judge_provider=judge)
            self.assertEqual(len(target.calls), 3)
            self.assertEqual(len(judge.calls), 2)
            self.assertEqual(
                [record["case_id"] for record in runner.load_jsonl(outcome.run_dir / "judgments.jsonl")],
                [selected_ids[0], selected_ids[2]],
            )
            self.assertEqual(outcome.summary["completion_status"], "COMPLETED_WITH_ERRORS")
            runner.validate_result_artifacts(outcome.run_dir)

    def test_all_target_errors_complete_without_judge_calls(self) -> None:
        definition = discover_evals()[0]
        selected_ids = tuple(case.case_id for case in definition.cases[:2])
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            request = EvalRunRequest(
                eval_id=definition.eval_id,
                case_ids=selected_ids,
                target_profile="fake",
                judge_profile="fake",
                profiles_file=self.write_profiles(root),
                results_root=root / "results",
                allow_dirty_debug=True,
                run_id="all-target-errors",
            )
            target = RecordingProvider(
                [
                    runner.ProviderError("first failure", code="NETWORK_ERROR", retryable=True),
                    runner.ProviderError("second failure", code="NETWORK_ERROR", retryable=True),
                ]
            )
            judge = RecordingProvider()
            outcome = execute_request(request, target_provider=target, judge_provider=judge)
            self.assertEqual(len(judge.calls), 0)
            self.assertEqual(outcome.summary["completion_status"], "COMPLETED_WITH_ERRORS")
            self.assertEqual(outcome.summary["counts"]["errored_cases"], 2)
            runner.validate_result_artifacts(outcome.run_dir)

    def test_successful_target_without_judgment_is_incomplete_and_retried_by_default(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases[:2], criteria)
        with tempfile.TemporaryDirectory() as temp_dir:
            results_root = Path(temp_dir) / "results"
            run_dir = results_root / "v1.6.0" / runner.API_RUNTIME_PROFILE / "unfinished-judge"
            runner.execute_run(
                prepared,
                RecordingProvider(["first answer", "second answer"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            self.assertEqual(discover_runs(results_root), [])
            metadata = runner.load_json_object(run_dir / "run.json")
            metadata["origin_mode"] = "FULL"
            metadata["console"] = {
                "schema_version": CURRENT_CONSOLE_SCHEMA_VERSION,
                "origin_mode": "FULL",
                "eval_id": discover_evals()[0].eval_id,
                "selected_case_ids": [record["case_id"] for record in prepared],
                "selected_cases": len(prepared),
                "total_eval_cases": len(discover_evals()[0].cases),
                "target_profile": "fake",
                "judge_profile": "fake",
                "target_model": metadata["target"]["requested_model"],
                "judge_model": None,
                "target_concurrency": 1,
                "judge_concurrency": 1,
            }
            runner.write_json(run_dir / "run.json", metadata)
            history = discover_runs(results_root)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].state, "INCOMPLETE")
            self.assertEqual(history[0].incomplete_case_ids, tuple(record["case_id"] for record in prepared))

    def test_console_labels_failed_judgment_and_non_tty_waits(self) -> None:
        self.assertEqual(
            _result_label(
                "JUDGE",
                {"status": "JUDGMENT", "criteria": [{"passed": False}]},
            ),
            "FAIL",
        )
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            reporter = _ActivityReporter()
            reporter.start("TARGET", {"case_id": "RC-001"}, 1, 1)
            reporter.finish(
                "TARGET",
                {"case_id": "RC-001", "status": "MODEL_RESPONSE"},
                1,
                1,
            )
            reporter.close()
        self.assertIn("[开始] RC-001 Target", output.getvalue())
        self.assertIn("[完成] RC-001 Target", output.getvalue())

    def test_activity_reporter_tracks_multiple_active_cases(self) -> None:
        reporter = _ActivityReporter(target_concurrency=2)
        reporter.start("TARGET", {"case_id": "A"}, 1, 4)
        reporter.start("TARGET", {"case_id": "B"}, 2, 4)
        snapshot = reporter.snapshot()
        self.assertEqual(snapshot.active_case_ids, ("A", "B"))
        self.assertEqual(snapshot.running, 2)
        self.assertEqual(snapshot.concurrency, 2)
        reporter.close()

    def test_activity_reporter_finish_removes_only_completed_case(self) -> None:
        reporter = _ActivityReporter(target_concurrency=2)
        reporter.start("TARGET", {"case_id": "A"}, 1, 3)
        reporter.start("TARGET", {"case_id": "B"}, 2, 3)
        reporter.finish("TARGET", {"case_id": "A", "status": "MODEL_RESPONSE"}, 1, 3)
        snapshot = reporter.snapshot()
        self.assertEqual(snapshot.active_case_ids, ("B",))
        self.assertEqual(snapshot.completed, 1)
        reporter.close()

    def test_parallel_activity_snapshot_reports_running_and_pending_counts(self) -> None:
        reporter = _ActivityReporter(target_concurrency=2)
        reporter.start("TARGET", {"case_id": "A"}, 1, 4)
        reporter.start("TARGET", {"case_id": "B"}, 2, 4)
        reporter.finish("TARGET", {"case_id": "A", "status": "MODEL_RESPONSE"}, 1, 4)
        reporter.start("TARGET", {"case_id": "C"}, 3, 4)
        snapshot = reporter.snapshot()
        self.assertEqual((snapshot.completed, snapshot.running, snapshot.pending), (1, 2, 1))
        self.assertEqual(snapshot.active_case_ids, ("B", "C"))
        reporter.close()

    def test_malformed_judge_case_is_saved_as_error_and_later_cases_continue(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases[:2], criteria)
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "v1.6.0" / runner.API_RUNTIME_PROFILE / "judge-errors"
            runner.execute_run(
                prepared,
                RecordingProvider(["answer one", "answer two"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            judge = MalformedFirstJudge()
            progress: list[tuple[str, int, int]] = []
            counts = runner.execute_judge(
                run_dir,
                judge,
                case_ids=runner.planned_judge_case_ids(run_dir),
                on_case_complete=lambda record, done, total: progress.append(
                    (record["status"], done, total)
                ),
            )
            self.assertEqual(len(judge.calls), 2)
            self.assertEqual(progress, [("JUDGE_ERROR", 1, 2), ("JUDGMENT", 2, 2)])
            self.assertEqual(counts, {"judged": 1, "judge_error": 1, "not_judged": 0})
            self.assertEqual(
                [record["status"] for record in runner.load_jsonl(run_dir / "judgments.jsonl")],
                ["JUDGE_ERROR", "JUDGMENT"],
            )
            runner.validate_result_artifacts(run_dir)
