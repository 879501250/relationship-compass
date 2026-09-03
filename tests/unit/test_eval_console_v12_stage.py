"""Stage decoupling, lineage, and true-resume regression tests."""

from __future__ import annotations

import io
import json
from shutil import copytree
import signal
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as runner  # noqa: E402
from eval_console.discovery import HistoricalRun, discover_evals, discover_runs  # noqa: E402
from eval_console.cli import (  # noqa: E402
    _GracefulStop,
    _interactive_history_stage,
    _print_provider_telemetry,
    _request_from_args,
    _resume_concurrency_defaults,
    _resume_request_with_inherited_configuration,
    build_parser,
)
from eval_console.models import (  # noqa: E402
    CURRENT_CONSOLE_SCHEMA_VERSION,
    EvalExecutionMode,
    EvalRunRequest,
    JudgeCaseSelector,
)
from eval_console.service import (  # noqa: E402
    EvalConsoleError,
    _mark_interrupted,
    _prepared_records,
    execute_request,
    judge_only_case_ids,
    plan_stage_execution,
    preflight_request,
)


def judgment(schema: dict[str, Any], *, fail: bool = False) -> str:
    case_id = schema["properties"]["case_id"]["const"]
    criteria = schema["properties"]["criteria"]["items"]["properties"]["criterion"]["enum"]
    return json.dumps(
        {
            "case_id": case_id,
            "criteria": [
                {
                    "criterion": criterion,
                    "passed": not fail,
                    "reason": "Target 原文包含可核对的对应内容。",
                }
                for criterion in criteria
            ],
        },
        ensure_ascii=False,
    )


class StageProvider:
    provider_name = "stage-test"
    model = "stage-test-model"
    public_parameters = {"network": False, "single_sample": True}

    def __init__(
        self,
        target_outputs: list[Any] | None = None,
        judge_outputs: dict[str, Any] | None = None,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        self.target_outputs = list(target_outputs or [])
        self.judge_outputs = dict(judge_outputs or {})
        self.delay_seconds = delay_seconds
        self.target_calls = 0
        self.judge_calls = 0

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if response_schema is None:
            self.target_calls += 1
            output = self.target_outputs.pop(0)
            if isinstance(output, BaseException):
                raise output
            return runner.ProviderResult(str(output), reported_model=self.model)
        self.judge_calls += 1
        case_id = response_schema["properties"]["case_id"]["const"]
        output = self.judge_outputs.get(case_id, judgment(response_schema))
        if isinstance(output, BaseException):
            raise output
        return runner.ProviderResult(str(output), reported_model=self.model)


class BlockingJudgeProvider(StageProvider):
    """Deterministic in-flight Judge fixture for graceful-stop regression tests."""

    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        if response_schema is None:
            raise AssertionError("blocking fixture only supports Judge requests")
        self.started.set()
        if not self.release.wait(2):
            raise AssertionError("test did not release the in-flight Judge request")
        return super().generate(
            instructions=instructions,
            input_text=input_text,
            response_schema=response_schema,
        )


class BlockingTargetProvider(StageProvider):
    """Deterministic in-flight Target fixture for responsive-call regressions."""

    def __init__(self) -> None:
        super().__init__(["completed target"])
        self.started = threading.Event()
        self.release = threading.Event()

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        if response_schema is not None:
            raise AssertionError("blocking fixture only supports Target requests")
        self.started.set()
        if not self.release.wait(2):
            raise AssertionError("test did not release the in-flight Target request")
        return super().generate(
            instructions=instructions,
            input_text=input_text,
            response_schema=response_schema,
        )


class StageDecouplingTests(unittest.TestCase):
    def write_profiles(self, root: Path) -> Path:
        profiles = root / "profiles.json"
        profiles.write_text(
            json.dumps(
                {
                    "profiles": {
                        "fake": {
                            "provider": "openai_responses",
                            "target": {"model": "stage-test-model"},
                            "judge": {"model": "stage-test-model"},
                        },
                        "judge-b": {
                            "provider": "openai_responses",
                            "judge": {"model": "different-judge-model"},
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        return profiles

    def request(
        self,
        root: Path,
        case_ids: tuple[str, ...],
        *,
        mode: EvalExecutionMode,
        run_id: str | None = None,
        source: Path | None = None,
    ) -> EvalRunRequest:
        return EvalRunRequest(
            eval_id=discover_evals()[0].eval_id,
            case_ids=case_ids,
            target_profile="fake" if mode is not EvalExecutionMode.JUDGE_ONLY else None,
            judge_profile="fake" if mode is not EvalExecutionMode.TARGET_ONLY else None,
            profiles_file=self.write_profiles(root),
            results_root=root / "results",
            allow_dirty_debug=True,
            run_id=run_id,
            mode=mode,
            source_run_dir=source,
        )

    @staticmethod
    def case_ids(count: int) -> tuple[str, ...]:
        return tuple(case.case_id for case in discover_evals()[0].cases[:count])

    def target_only(self, root: Path, case_ids: tuple[str, ...], *, run_id: str) -> tuple[Any, StageProvider]:
        target = StageProvider([f"response {index}" for index in range(len(case_ids))])
        outcome = execute_request(
            self.request(root, case_ids, mode=EvalExecutionMode.TARGET_ONLY, run_id=run_id),
            target_provider=target,
        )
        return outcome, target

    def target_only_fast(
        self, root: Path, case_ids: tuple[str, ...], *, run_id: str
    ) -> tuple[Any, StageProvider]:
        with mock.patch.object(
            runner, "git_fingerprint", return_value={"git_sha": "a" * 40, "git_dirty": False}
        ):
            return self.target_only(root, case_ids, run_id=run_id)

    def full_with_judge_error(
        self, root: Path, case_ids: tuple[str, ...], *, run_id: str
    ) -> Any:
        return execute_request(
            self.request(root, case_ids, mode=EvalExecutionMode.FULL, run_id=run_id),
            target_provider=StageProvider(["response"] * len(case_ids)),
            judge_provider=StageProvider(
                judge_outputs={case_id: "not json" for case_id in case_ids}
            ),
        )

    def mark_current_console_run(
        self,
        run_dir: Path,
        case_ids: tuple[str, ...],
        *,
        origin_mode: EvalExecutionMode = EvalExecutionMode.FULL,
    ) -> None:
        metadata = runner.load_json_object(run_dir / "run.json")
        metadata["origin_mode"] = origin_mode.value
        metadata["console"] = {
            "schema_version": CURRENT_CONSOLE_SCHEMA_VERSION,
            "origin_mode": origin_mode.value,
            "eval_id": discover_evals()[0].eval_id,
            "selected_case_ids": list(case_ids),
            "selected_cases": len(case_ids),
            "total_eval_cases": len(discover_evals()[0].cases),
            "target_profile": "fake",
            "judge_profile": "fake",
            "target_model": metadata["target"].get("requested_model"),
            "judge_model": (
                metadata["judge"].get("requested_model")
                if isinstance(metadata.get("judge"), dict)
                else None
            ),
            "target_concurrency": 1,
            "judge_concurrency": 1,
        }
        runner.write_json(run_dir / "run.json", metadata)

    def test_target_only_saves_complete_target_artifacts_without_judge_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(3)
            target = StageProvider(["one", "two", "three"])
            outcome = execute_request(
                self.request(root, case_ids, mode=EvalExecutionMode.TARGET_ONLY, run_id="target-only"),
                target_provider=target,
            )
            self.assertEqual(target.target_calls, 3)
            self.assertEqual(outcome.api_calls, {"target": 3, "judge": 0})
            self.assertEqual(outcome.summary["completion_status"], "TARGET_COMPLETE")
            self.assertEqual(outcome.summary["origin_mode"], "TARGET_ONLY")
            self.assertEqual(outcome.summary["api_calls"], {"target": 3, "judge": 0})
            metadata = runner.load_json_object(outcome.run_dir / "run.json")
            self.assertEqual(metadata["origin_mode"], "TARGET_ONLY")
            self.assertEqual(
                metadata["console"]["schema_version"], CURRENT_CONSOLE_SCHEMA_VERSION
            )
            self.assertEqual(metadata["console"]["origin_mode"], "TARGET_ONLY")
            self.assertEqual(metadata["console"]["target_model"], "stage-test-model")
            for name in ("run.json", "responses.jsonl", "summary.json", "summary.md", "run.log"):
                self.assertTrue((outcome.run_dir / name).is_file(), name)
            self.assertFalse((outcome.run_dir / "judgments.jsonl").exists())
            runner.validate_result_artifacts(outcome.run_dir)

    def test_judge_only_reuses_source_target_immutably_and_filters_judge_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(3)
            source, _ = self.target_only(root, case_ids, run_id="source-errors")
            errors = StageProvider(judge_outputs={case_ids[0]: "not json", case_ids[2]: "not json"})
            runner.execute_judge(source.run_dir, errors, case_ids=case_ids)
            source_before = (source.run_dir / "responses.jsonl").read_bytes()
            selected = judge_only_case_ids(source.run_dir, JudgeCaseSelector.JUDGE_ERROR)
            self.assertEqual(selected, (case_ids[0], case_ids[2]))
            target = StageProvider(["must not be used"])
            judge = StageProvider()
            request = replace(
                self.request(
                    root, selected, mode=EvalExecutionMode.JUDGE_ONLY,
                    run_id="judge-only-errors", source=source.run_dir,
                ),
                judge_profile="judge-b",
            )
            outcome = execute_request(
                request,
                target_provider=target,
                judge_provider=judge,
            )
            self.assertEqual(target.target_calls, 0)
            self.assertEqual(judge.judge_calls, 2)
            self.assertEqual(outcome.api_calls, {"target": 0, "judge": 2})
            self.assertEqual((source.run_dir / "responses.jsonl").read_bytes(), source_before)
            metadata = runner.load_json_object(outcome.run_dir / "run.json")
            self.assertEqual(metadata["source_target_run_id"], "source-errors")
            self.assertEqual(
                metadata["console"]["schema_version"], CURRENT_CONSOLE_SCHEMA_VERSION
            )
            self.assertEqual(metadata["console"]["origin_mode"], "JUDGE_ONLY")
            self.assertEqual(metadata["console"]["judge_profile"], "judge-b")
            self.assertEqual(metadata["console"]["judge_model"], "stage-test-model")
            self.assertEqual(metadata["api_calls"], {"target": 0, "judge": 2})
            runner.validate_result_artifacts(outcome.run_dir)

    def test_equivalent_real_error_run_retries_thirteen_judges_without_target_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(30)
            source, target_source = self.target_only(root, case_ids, run_id="console-20260827T104640Z-17572")
            self.assertEqual(target_source.target_calls, 30)
            judge_errors = {case_id: "not json" for case_id in case_ids[17:]}
            runner.execute_judge(
                source.run_dir, StageProvider(judge_outputs=judge_errors), case_ids=case_ids
            )
            selected = judge_only_case_ids(source.run_dir, JudgeCaseSelector.JUDGE_ERROR)
            self.assertEqual(len(selected), 13)
            target = StageProvider(["must not be used"])
            judge = StageProvider()
            outcome = execute_request(
                self.request(root, selected, mode=EvalExecutionMode.JUDGE_ONLY, run_id="retry-13", source=source.run_dir),
                target_provider=target,
                judge_provider=judge,
            )
            self.assertEqual(target.target_calls, 0)
            self.assertEqual(judge.judge_calls, 13)
            self.assertEqual(outcome.api_calls, {"target": 0, "judge": 13})

    def test_resume_matrix_retries_only_needed_stages_and_preserves_judge_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(6)
            prepared = [record for record in runner.prepare_cases(*runner.load_definitions()) if record["case_id"] in set(case_ids)]
            run_dir = root / "results" / "v1.6.0" / runner.API_RUNTIME_PROFILE / "resume-matrix"
            target = StageProvider(
                ["a", "b", "c", "d", runner.ProviderError("target", code="NETWORK_ERROR", retryable=False)]
            )
            runner.execute_run(
                prepared, target, run_dir, repository_sha="a" * 40, repository_dirty=False,
                continue_on_error=False,
            )
            malformed = {case_ids[2]: "not json"}
            runner.execute_judge(
                run_dir,
                StageProvider(judge_outputs=malformed),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            metadata = runner.load_json_object(run_dir / "run.json")
            judgments = runner.load_jsonl(run_dir / "judgments.jsonl")
            pending = next(record for record in judgments if record["case_id"] == case_ids[3])
            pending.update({"status": "NOT_JUDGED", "criteria": None, "error_code": None, "retryable": None, "error": "judge pending"})
            runner.write_jsonl(run_dir / "judgments.jsonl", [
                pending if record["case_id"] == case_ids[3] else record for record in judgments
            ])
            runner.refresh_run_metadata(metadata, runner.load_jsonl(run_dir / "responses.jsonl"), runner.load_jsonl(run_dir / "judgments.jsonl"))
            runner.write_json(run_dir / "run.json", metadata)
            self.mark_current_console_run(run_dir, case_ids)
            plan = plan_stage_execution(run_dir, case_ids, EvalExecutionMode.RESUME)
            self.assertEqual(plan.target_cases, (case_ids[4], case_ids[5]))
            self.assertEqual(plan.judge_cases, (case_ids[2], case_ids[3], case_ids[4], case_ids[5]))
            resumed_target = StageProvider(["e", "f"])
            resumed_judge = StageProvider()
            outcome = execute_request(
                self.request(root, case_ids, mode=EvalExecutionMode.RESUME, source=run_dir),
                target_provider=resumed_target,
                judge_provider=resumed_judge,
            )
            self.assertEqual(resumed_target.target_calls, 2)
            self.assertEqual(resumed_judge.judge_calls, 4)
            attempts = [
                item for item in runner.load_jsonl(run_dir / "judgments.jsonl")
                if item["case_id"] == case_ids[2]
            ]
            self.assertEqual([item["attempt"] for item in attempts], [1, 2])
            self.assertEqual(attempts[-1]["status"], "JUDGMENT")
            self.assertEqual(outcome.api_calls, {"target": 2, "judge": 4})
            runner.validate_result_artifacts(run_dir)

    def test_interrupted_checkpoint_resumes_without_repeating_successful_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(3)
            stop = {"requested": False}
            target = StageProvider(["one", "two", "three"])
            request = self.request(root, case_ids, mode=EvalExecutionMode.TARGET_ONLY, run_id="interrupted")
            outcome = execute_request(
                request,
                target_provider=target,
                progress=lambda phase, record, completed, total: stop.update(requested=True),
                should_stop=lambda: stop["requested"],
            )
            self.assertEqual(target.target_calls, 1)
            self.assertEqual(outcome.summary["completion_status"], "INTERRUPTED")
            resumed_target = StageProvider(["two", "three"])
            resumed_judge = StageProvider()
            resumed = execute_request(
                self.request(root, case_ids, mode=EvalExecutionMode.RESUME, source=outcome.run_dir),
                target_provider=resumed_target,
                judge_provider=resumed_judge,
            )
            self.assertEqual(resumed_target.target_calls, 2)
            self.assertEqual(resumed_judge.judge_calls, 0)
            self.assertEqual(resumed.summary["completion_status"], "TARGET_COMPLETE")

    def test_interrupted_judge_resume_uses_only_missing_scoped_judgments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(3)
            prepared = [
                record
                for record in runner.prepare_cases(*runner.load_definitions())
                if record["case_id"] in set(case_ids)
            ]
            source_run_dir = (
                root / "results" / "v1.6.0" / runner.API_RUNTIME_PROFILE / "interrupted-judge"
            )
            target = StageProvider(["one", "two", "three"])
            runner.execute_run(
                prepared,
                target,
                source_run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            self.mark_current_console_run(source_run_dir, case_ids)
            runner.execute_judge(
                source_run_dir, StageProvider(), case_ids=(case_ids[0],)
            )
            _mark_interrupted(source_run_dir)
            selected = (case_ids[2], case_ids[1])
            prepared = _prepared_records(source_run_dir, selected)
            self.assertEqual(
                [record["case_id"] for record in prepared], [case_ids[1], case_ids[2]]
            )
            source_before = {
                path.relative_to(source_run_dir): path.read_bytes()
                for path in source_run_dir.rglob("*")
                if path.is_file()
            }
            child_target = StageProvider(["must not be used"])
            child_judge = StageProvider()
            child_outcome = execute_request(
                self.request(
                    root,
                    selected,
                    mode=EvalExecutionMode.JUDGE_ONLY,
                    run_id="interrupted-subset-child",
                    source=source_run_dir,
                ),
                target_provider=child_target,
                judge_provider=child_judge,
            )
            self.assertEqual(child_target.target_calls, 0)
            self.assertEqual(child_judge.judge_calls, 2)
            child_snapshots = runner.load_run_snapshots(child_outcome.run_dir)["prepared"]
            self.assertEqual(
                [record["case_id"] for record in child_snapshots], [case_ids[1], case_ids[2]]
            )
            child_metadata = runner.load_json_object(child_outcome.run_dir / "run.json")
            self.assertEqual(
                [record["case_id"] for record in child_metadata["cases"]],
                [case_ids[1], case_ids[2]],
            )
            self.assertEqual(child_metadata["source_target_run_id"], "interrupted-judge")
            self.assertEqual(
                source_before,
                {
                    path.relative_to(source_run_dir): path.read_bytes()
                    for path in source_run_dir.rglob("*")
                    if path.is_file()
                },
            )
            selected_run_dir = root / "selected-copy" / source_run_dir.relative_to(
                root / "results"
            )
            copytree(source_run_dir, selected_run_dir)
            before_c = [
                record
                for record in runner.load_jsonl(selected_run_dir / "judgments.jsonl")
                if record["case_id"] == case_ids[2]
            ]
            selected_judge = StageProvider()
            selected_outcome = execute_request(
                self.request(
                    root,
                    (case_ids[1],),
                    mode=EvalExecutionMode.RESUME,
                    source=selected_run_dir,
                ),
                judge_provider=selected_judge,
            )
            self.assertEqual(selected_judge.judge_calls, 1)
            self.assertEqual(selected_outcome.api_calls, {"target": 0, "judge": 1})
            after_c = [
                record
                for record in runner.load_jsonl(selected_run_dir / "judgments.jsonl")
                if record["case_id"] == case_ids[2]
            ]
            self.assertEqual(after_c, before_c)
            plan = plan_stage_execution(
                source_run_dir, case_ids, EvalExecutionMode.RESUME
            )
            self.assertEqual(plan.target_cases, ())
            self.assertEqual(plan.judge_cases, case_ids[1:])
            judge = StageProvider()
            outcome = execute_request(
                self.request(
                    root, case_ids, mode=EvalExecutionMode.RESUME, source=source_run_dir
                ),
                judge_provider=judge,
            )
            self.assertEqual(target.target_calls, 3)
            self.assertEqual(judge.judge_calls, 2)
            self.assertEqual(outcome.api_calls, {"target": 0, "judge": 2})
            self.assertEqual(outcome.summary["completion_status"], "COMPLETED")

    def test_explicit_judge_scope_requires_successful_target_per_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(2)
            prepared = [
                record
                for record in runner.prepare_cases(*runner.load_definitions())
                if record["case_id"] in set(case_ids)
            ]
            run_dir = (
                root / "v1.6.0" / runner.API_RUNTIME_PROFILE / "mixed-target"
            )
            with mock.patch.object(
                runner, "git_fingerprint", return_value={"git_sha": "a" * 40, "git_dirty": False}
            ):
                runner.execute_run(
                    prepared,
                    StageProvider(
                        ["available", runner.ProviderError("failed", code="NETWORK_ERROR")]
                    ),
                    run_dir,
                    repository_sha="a" * 40,
                    repository_dirty=False,
                    continue_on_error=True,
                )
            _mark_interrupted(run_dir)
            judge = StageProvider()
            self.assertEqual(
                runner.execute_judge(
                    run_dir, judge, case_ids=(case_ids[0],)
                )["judged"],
                1,
            )
            with self.assertRaisesRegex(
                runner.ModelEvalError,
                "without successful target responses: " + case_ids[1],
            ):
                runner.execute_judge(
                    run_dir, StageProvider(), case_ids=(case_ids[1],)
                )
            statuses = (
                "TARGET_COMPLETE",
                "TARGET_PARTIAL",
                "JUDGE_PARTIAL",
                "COMPLETED_WITH_ERRORS",
                "INTERRUPTED",
            )
            for status in statuses:
                with self.subTest(status=status):
                    metadata = runner.load_json_object(run_dir / "run.json")
                    metadata["status"] = status
                    metadata["interrupted"] = status == "INTERRUPTED"
                    runner.write_json(run_dir / "run.json", metadata)
                    # The synthetic aggregate statuses deliberately do not match
                    # the unchanged per-Case evidence. This isolates the
                    # executor's eligibility decision from artifact lifecycle
                    # validation, which is covered by the real fixtures above.
                    with mock.patch.object(runner, "validate_result_artifacts"):
                        result = runner.execute_judge(
                            run_dir,
                            StageProvider(),
                            case_ids=(case_ids[0],),
                            resume=True,
                        )
                self.assertEqual(result["judged"], 1)

    def test_graceful_stop_acknowledges_once_and_force_stop_is_stage_aware(self) -> None:
        for phase, label in (("TARGET", "Target"), ("JUDGE", "Judge")):
            with self.subTest(phase=phase):
                output = io.StringIO()
                stop = _GracefulStop(1, 1, stream=output)
                stop.set_stage(phase)
                stop.handle_interrupt()
                self.assertTrue(stop.requested)
                self.assertFalse(stop.force_requested)
                self.assertIn("已收到停止请求", output.getvalue())
                self.assertIn(label, output.getvalue())
                with self.assertRaises(KeyboardInterrupt):
                    stop.handle_interrupt()
                self.assertTrue(stop.force_requested)
                self.assertIn(
                    f"正在强制终止当前 {label} 请求", output.getvalue()
                )

    def test_parallel_graceful_stop_lists_all_inflight_cases(self) -> None:
        output = io.StringIO()
        stop = _GracefulStop(2, 1, stream=output, active_cases=lambda: ("A", "B"))
        stop.set_stage("TARGET")
        stop.handle_interrupt()
        self.assertIn("不会继续安排新的工作", output.getvalue())
        self.assertIn("当前仍在运行：\n  A\n  B", output.getvalue())
        self.assertIn("这 2 个请求完成后将保存进度并中断", output.getvalue())

    def test_graceful_stop_acknowledges_before_inflight_judge_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(3)
            prepared = [
                record
                for record in runner.prepare_cases(*runner.load_definitions())
                if record["case_id"] in set(case_ids)
            ]
            source_run_dir = (
                root / "results" / "v1.6.0" / runner.API_RUNTIME_PROFILE / "blocking-judge"
            )
            runner.execute_run(
                prepared,
                StageProvider(["one", "two", "three"]),
                source_run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            self.mark_current_console_run(source_run_dir, case_ids)
            stop_output = io.StringIO()
            stop = _GracefulStop(1, 1, stream=stop_output)
            judge = BlockingJudgeProvider()
            result: dict[str, Any] = {}
            errors: list[BaseException] = []

            def execute() -> None:
                try:
                    result["outcome"] = execute_request(
                        self.request(
                            root,
                            case_ids,
                            mode=EvalExecutionMode.RESUME,
                            source=source_run_dir,
                        ),
                        judge_provider=judge,
                        activity=lambda phase, *_: stop.set_stage(phase),
                        should_stop=lambda: stop.requested,
                    )
                except BaseException as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            worker = threading.Thread(target=execute)
            worker.start()
            try:
                self.assertTrue(judge.started.wait(3))
                acknowledged_at = time.monotonic()
                stop.handle_interrupt()
                self.assertLess(time.monotonic() - acknowledged_at, 1)
                self.assertIn("已收到停止请求", stop_output.getvalue())
                self.assertTrue(worker.is_alive())
                self.assertFalse((source_run_dir / "judgments.jsonl").exists())
                force_started_at = time.monotonic()
                with self.assertRaises(KeyboardInterrupt):
                    stop.handle_interrupt()
                self.assertLess(time.monotonic() - force_started_at, 1)
                self.assertTrue(stop.force_requested)
                self.assertTrue(worker.is_alive())
            finally:
                judge.release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            outcome = result["outcome"]
            self.assertEqual(judge.judge_calls, 1)
            self.assertEqual(outcome.api_calls, {"target": 0, "judge": 1})
            self.assertEqual(outcome.summary["completion_status"], "INTERRUPTED")
            judgments = runner.load_jsonl(source_run_dir / "judgments.jsonl")
            self.assertEqual(judgments[0]["status"], "JUDGMENT")
            resumed_judge = StageProvider()
            resumed = execute_request(
                self.request(
                    root, case_ids, mode=EvalExecutionMode.RESUME, source=source_run_dir
                ),
                judge_provider=resumed_judge,
            )
            self.assertEqual(resumed_judge.judge_calls, 2)
            self.assertEqual(resumed.api_calls, {"target": 0, "judge": 2})
            self.assertEqual(resumed.summary["completion_status"], "COMPLETED")
            self.assertFalse(
                any(thread.name == "eval-provider-call" and thread.is_alive() for thread in threading.enumerate())
            )

    def test_responsive_target_call_stops_after_current_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(2)
            stop_output = io.StringIO()
            stop = _GracefulStop(1, 1, stream=stop_output)
            target = BlockingTargetProvider()
            result: dict[str, Any] = {}
            errors: list[BaseException] = []

            def execute() -> None:
                try:
                    result["outcome"] = execute_request(
                        self.request(
                            root, case_ids, mode=EvalExecutionMode.TARGET_ONLY, run_id="blocking-target"
                        ),
                        target_provider=target,
                        activity=lambda phase, *_: stop.set_stage(phase),
                        should_stop=lambda: stop.requested,
                    )
                except BaseException as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            worker = threading.Thread(target=execute)
            worker.start()
            try:
                self.assertTrue(target.started.wait(3))
                stop.handle_interrupt()
                self.assertIn("已收到停止请求", stop_output.getvalue())
                run_dir = root / "results" / "v1.6.0" / runner.API_RUNTIME_PROFILE / "blocking-target"
                self.assertEqual(runner.load_jsonl(run_dir / "responses.jsonl"), [])
            finally:
                target.release.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            outcome = result["outcome"]
            self.assertEqual(target.target_calls, 1)
            self.assertEqual(outcome.api_calls, {"target": 1, "judge": 0})
            self.assertEqual(outcome.summary["completion_status"], "INTERRUPTED")

    def test_responsive_provider_call_handles_main_thread_signals_and_preserves_errors(self) -> None:
        started = threading.Event()
        release = threading.Event()
        output = io.StringIO()
        stop = _GracefulStop(1, 1, stream=output)
        observed: dict[str, float] = {}

        def blocking_call() -> runner.ProviderResult:
            started.set()
            self.assertTrue(release.wait(2))
            return runner.ProviderResult("completed")

        def interrupt_once() -> None:
            self.assertTrue(started.wait(1))
            requested_at = time.monotonic()
            signal.raise_signal(signal.SIGINT)
            deadline = requested_at + 1
            while not stop.requested and time.monotonic() < deadline:
                time.sleep(0.01)
            observed["delay"] = time.monotonic() - requested_at
            release.set()

        notifier = threading.Thread(target=interrupt_once, daemon=True)
        notifier.start()
        with stop:
            result = runner.run_responsive_provider_call(blocking_call, poll_interval=0.02)
        notifier.join(2)
        self.assertFalse(notifier.is_alive())
        self.assertEqual(result.text, "completed")
        self.assertTrue(stop.requested)
        self.assertLess(observed["delay"], 1)
        self.assertIn("已收到停止请求", output.getvalue())

        expected = runner.ProviderTimeout("timed out")
        with self.assertRaises(runner.ProviderTimeout) as raised:
            runner.run_responsive_provider_call(lambda: (_ for _ in ()).throw(expected))
        self.assertIs(raised.exception, expected)

    def test_second_main_thread_interrupt_abandons_blocked_provider_without_waiting(self) -> None:
        started = threading.Event()
        release = threading.Event()
        output = io.StringIO()
        stop = _GracefulStop(1, 1, stream=output)
        observed: dict[str, float] = {}

        def blocking_call() -> runner.ProviderResult:
            started.set()
            release.wait(5)
            return runner.ProviderResult("completed")

        def interrupt_twice() -> None:
            self.assertTrue(started.wait(1))
            signal.raise_signal(signal.SIGINT)
            deadline = time.monotonic() + 1
            while not stop.requested and time.monotonic() < deadline:
                time.sleep(0.01)
            observed["force_sent_at"] = time.monotonic()
            signal.raise_signal(signal.SIGINT)

        notifier = threading.Thread(target=interrupt_twice, daemon=True)
        notifier.start()
        try:
            with self.assertRaises(KeyboardInterrupt):
                with stop:
                    runner.run_responsive_provider_call(blocking_call, poll_interval=0.02)
            self.assertTrue(stop.force_requested)
            self.assertLess(time.monotonic() - observed["force_sent_at"], 1)
            self.assertIn("再次收到 Ctrl+C", output.getvalue())
            self.assertTrue(
                any(
                    thread.name == "eval-provider-call" and thread.is_alive()
                    for thread in threading.enumerate()
                )
            )
        finally:
            release.set()
        notifier.join(2)
        self.assertFalse(notifier.is_alive())

    def test_old_console_version_is_rejected_and_diagnostics_are_secret_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(1)
            source, _ = self.target_only(root, case_ids, run_id="legacy-source")
            metadata = runner.load_json_object(source.run_dir / "run.json")
            metadata["console"]["schema_version"] = CURRENT_CONSOLE_SCHEMA_VERSION - 1
            runner.write_json(source.run_dir / "run.json", metadata)
            self.assertEqual(discover_runs(root / "results"), [])
            with self.assertRaises(EvalConsoleError):
                judge_only_case_ids(source.run_dir, JudgeCaseSelector.ALL_TARGET)
            with self.assertRaises(EvalConsoleError):
                plan_stage_execution(source.run_dir, case_ids, EvalExecutionMode.RESUME)
            old_request = self.request(
                root, case_ids, mode=EvalExecutionMode.RESUME, source=source.run_dir
            )
            with self.assertRaises(EvalConsoleError):
                _resume_request_with_inherited_configuration(old_request, metadata)
            metadata["console"]["schema_version"] = CURRENT_CONSOLE_SCHEMA_VERSION
            runner.write_json(source.run_dir / "run.json", metadata)
            runner.execute_judge(
                source.run_dir,
                StageProvider(judge_outputs={case_ids[0]: "fake-api-key malformed"}),
                case_ids=case_ids,
            )
            record = runner.load_jsonl(source.run_dir / "judgments.jsonl")[0]
            self.assertEqual(record["status"], "JUDGE_ERROR")
            self.assertNotIn("fake-api-key", record["diagnostics"]["raw_excerpt"])

    def test_duration_uses_fractional_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(1)
            outcome = execute_request(
                self.request(root, case_ids, mode=EvalExecutionMode.TARGET_ONLY, run_id="timing"),
                target_provider=StageProvider(["answer"], delay_seconds=0.15),
            )
            record = runner.load_jsonl(outcome.run_dir / "responses.jsonl")[0]
            started = record["started_at"].replace("Z", "+00:00")
            completed = record["completed_at"].replace("Z", "+00:00")
            duration = (__import__("datetime").datetime.fromisoformat(completed) - __import__("datetime").datetime.fromisoformat(started)).total_seconds()
            self.assertGreater(duration, 0.1)
            self.assertIsInstance(duration, float)
            self.assertIsInstance(record["duration_seconds"], float)
            self.assertGreater(record["duration_seconds"], 0.1)

    def test_judge_only_dry_run_does_not_create_a_child_or_mutate_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(2)
            source, _ = self.target_only(root, case_ids, run_id="dry-source")
            source_before = {
                path.name: path.read_bytes()
                for path in source.run_dir.iterdir()
                if path.is_file()
            }
            siblings_before = {path.name for path in source.run_dir.parent.iterdir()}
            request = replace(
                self.request(root, case_ids, mode=EvalExecutionMode.JUDGE_ONLY, source=source.run_dir),
                dry_run=True,
            )
            outcome = execute_request(request, judge_provider=StageProvider())
            self.assertTrue(outcome.dry_run)
            self.assertEqual(outcome.api_calls, {"target": 0, "judge": 2})
            self.assertEqual(siblings_before, {path.name for path in source.run_dir.parent.iterdir()})
            self.assertEqual(
                source_before,
                {path.name: path.read_bytes() for path in source.run_dir.iterdir() if path.is_file()},
            )

    def test_resume_with_only_judge_work_needs_no_target_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(1)
            source = self.full_with_judge_error(root, case_ids, run_id="resume-judge-only")
            request = self.request(
                root, case_ids, mode=EvalExecutionMode.RESUME, source=source.run_dir
            )
            judge = StageProvider()
            outcome = execute_request(request, judge_provider=judge)
            self.assertEqual(judge.judge_calls, 1)
            self.assertEqual(outcome.api_calls, {"target": 0, "judge": 1})
            self.assertEqual(outcome.summary["completion_status"], "COMPLETED")
            metadata = runner.load_json_object(source.run_dir / "run.json")
            self.assertEqual(metadata["origin_mode"], "FULL")
            self.assertEqual(
                metadata["console"]["schema_version"], CURRENT_CONSOLE_SCHEMA_VERSION
            )
            self.assertEqual(len(metadata["execution_history"]), 2)
            resumed_execution = metadata["execution_history"][-1]
            self.assertEqual(resumed_execution["mode"], "RESUME")
            self.assertEqual(resumed_execution["requested_case_ids"], list(case_ids))
            self.assertEqual(resumed_execution["planned_api_calls"], {"target": 0, "judge": 1})
            self.assertEqual(resumed_execution["actual_api_calls"], {"target": 0, "judge": 1})
            self.assertTrue(resumed_execution["api_call_plan_match"])

    def test_cli_resume_inherits_saved_profiles_and_rejects_explicit_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(1)
            source = self.full_with_judge_error(root, case_ids, run_id="resume-inherit")
            metadata = runner.load_json_object(source.run_dir / "run.json")
            args = build_parser().parse_args(["resume", "--from-run", str(source.run_dir)])
            args.source_run = source.run_dir
            args.execution_mode = "resume"
            request = _resume_request_with_inherited_configuration(
                _request_from_args(args, source.metadata["console"]["eval_id"], list(case_ids)),
                metadata,
            )
            self.assertEqual(request.target_profile, "fake")
            self.assertEqual(request.judge_profile, "fake")
            self.assertEqual(request.resume_target_model, "stage-test-model")
            self.assertEqual(request.resume_judge_model, "stage-test-model")
            plan = plan_stage_execution(source.run_dir, case_ids, EvalExecutionMode.RESUME)
            self.assertEqual(plan.target_cases, ())
            self.assertEqual(plan.judge_cases, case_ids)

            same_args = build_parser().parse_args(
                [
                    "resume", "--from-run", str(source.run_dir),
                    "--target-profile", "fake", "--judge-profile", "fake",
                    "--target-model", "stage-test-model", "--judge-model", "stage-test-model",
                ]
            )
            same_args.source_run = source.run_dir
            same_args.execution_mode = "resume"
            _resume_request_with_inherited_configuration(
                _request_from_args(
                    same_args, source.metadata["console"]["eval_id"], list(case_ids)
                ),
                metadata,
            )

            mismatch_args = build_parser().parse_args(
                ["resume", "--from-run", str(source.run_dir), "--judge-profile", "judge-b"]
            )
            mismatch_args.source_run = source.run_dir
            mismatch_args.execution_mode = "resume"
            with self.assertRaisesRegex(EvalConsoleError, "Resume configuration mismatch"):
                _resume_request_with_inherited_configuration(
                    _request_from_args(
                        mismatch_args, source.metadata["console"]["eval_id"], list(case_ids)
                    ),
                    metadata,
                )

    def test_resume_concurrency_defaults_use_the_latest_execution_for_each_stage(self) -> None:
        metadata: dict[str, object] = {
            "console": {"target_concurrency": 2, "judge_concurrency": 2},
            "execution_history": [
                {
                    "target_concurrency": 2,
                    "judge_concurrency": 2,
                    "actual_api_calls": {"target": 4, "judge": 4},
                },
                {
                    "target_concurrency": 1,
                    "judge_concurrency": 1,
                    "actual_api_calls": {"target": 0, "judge": 3},
                },
            ],
        }
        self.assertEqual(_resume_concurrency_defaults(metadata), (2, 1))

    def test_provider_http_summary_separates_logical_calls_from_telemetry_coverage(self) -> None:
        output = io.StringIO()
        metadata = {
            "execution_history": [
                {
                    "actual_api_calls": {"target": 4, "judge": 0},
                    "provider_telemetry": {
                        "target": {
                            "logical_calls_with_http_telemetry": 3,
                            "http_attempts": 5,
                            "retries": 2,
                            "rate_limit_responses": 1,
                            "retry_delay_seconds": 5.0,
                            "recovered_after_retry": 1,
                            "rate_limit_exhausted": 0,
                        }
                    },
                }
            ]
        }

        with redirect_stdout(output):
            _print_provider_telemetry(metadata)

        rendered = output.getvalue()
        self.assertIn("Logical Calls: 4", rendered)
        self.assertIn("Telemetry Coverage: 3/4", rendered)
        self.assertNotIn("Logical Calls: 3", rendered)

    def test_resume_preflight_rejects_missing_or_changed_original_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(1)
            source = self.full_with_judge_error(root, case_ids, run_id="resume-provider-check")
            metadata = runner.load_json_object(source.run_dir / "run.json")
            request = _resume_request_with_inherited_configuration(
                self.request(root, case_ids, mode=EvalExecutionMode.RESUME, source=source.run_dir),
                metadata,
            )
            missing_profiles = root / "missing-profiles.json"
            missing_profiles.write_text('{"profiles": {}}', encoding="utf-8")
            with self.assertRaisesRegex(EvalConsoleError, "原 Judge Provider Profile 不存在"):
                preflight_request(replace(request, profiles_file=missing_profiles))

            with mock.patch(
                "eval_console.service._create_profile_provider", return_value=StageProvider()
            ):
                _, judge, _, judge_plan = preflight_request(request)
            self.assertIsNotNone(judge)
            self.assertTrue(judge_plan["enabled"])

            changed = StageProvider()
            changed.model = "changed-judge-model"
            with mock.patch(
                "eval_console.service._create_profile_provider", return_value=changed
            ):
                with self.assertRaisesRegex(EvalConsoleError, "Resume configuration mismatch"):
                    preflight_request(request)

    def test_interactive_resume_reuses_saved_profiles_without_profile_picker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(1)
            source = self.full_with_judge_error(root, case_ids, run_id="interactive-resume")
            history = discover_runs(root / "results")
            self.assertEqual(len(history), 1)
            self.assertIsInstance(history[0], HistoricalRun)
            output = io.StringIO()
            with redirect_stdout(output), mock.patch(
                "eval_console.cli._choose",
                side_effect=[history[0], "auto", True, "start"],
            ), mock.patch(
                "eval_console.cli._yes_no", return_value=False
            ), mock.patch(
                "builtins.input", return_value=""
            ), mock.patch(
                "eval_console.cli._interactive_profile",
                side_effect=AssertionError("Resume must not open the Provider picker"),
            ), mock.patch("eval_console.cli._execute_and_print", return_value=0) as execute:
                result = _interactive_history_stage(
                    discover_evals(),
                    self.write_profiles(root),
                    root / "results",
                    False,
                    object(),
                    EvalExecutionMode.RESUME,
                )
            self.assertEqual(result, 0)
            self.assertIn("运行确认", output.getvalue())
            request = execute.call_args.args[0]
            self.assertEqual(request.target_profile, "fake")
            self.assertEqual(request.judge_profile, "fake")
            self.assertEqual(request.resume_judge_model, "stage-test-model")

    def test_resume_selected_judge_error_does_not_mutate_unselected_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(2)
            source = self.full_with_judge_error(
                root, case_ids, run_id="scope-judge"
            )
            before_b = [
                record for record in runner.load_jsonl(source.run_dir / "judgments.jsonl")
                if record["case_id"] == case_ids[1]
            ]
            target = StageProvider(["must not be called"])
            judge = StageProvider()
            outcome = execute_request(
                self.request(
                    root, (case_ids[0],), mode=EvalExecutionMode.RESUME, source=source.run_dir
                ),
                target_provider=target,
                judge_provider=judge,
            )
            self.assertEqual(target.target_calls, 0)
            self.assertEqual(judge.judge_calls, 1)
            after_b = [
                record for record in runner.load_jsonl(outcome.run_dir / "judgments.jsonl")
                if record["case_id"] == case_ids[1]
            ]
            self.assertEqual(after_b, before_b)
            self.assertEqual(len(after_b), 1)

    def test_resume_mixed_scope_runs_only_planned_target_and_judge_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(4)
            prepared = [
                record for record in runner.prepare_cases(*runner.load_definitions())
                if record["case_id"] in set(case_ids)
            ]
            run_dir = root / "results" / "v1.6.0" / runner.API_RUNTIME_PROFILE / "scope-mixed"
            runner.execute_run(
                prepared,
                StageProvider(["a", runner.ProviderError("target", code="NETWORK_ERROR", retryable=False), "c", "d"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
                continue_on_error=True,
            )
            runner.execute_judge(
                run_dir,
                StageProvider(judge_outputs={case_ids[0]: "not json", case_ids[3]: "not json"}),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            self.mark_current_console_run(run_dir, case_ids)
            before_c = [record for record in runner.load_jsonl(run_dir / "judgments.jsonl") if record["case_id"] == case_ids[2]]
            before_d = [record for record in runner.load_jsonl(run_dir / "judgments.jsonl") if record["case_id"] == case_ids[3]]
            target = StageProvider(["b"])
            judge = StageProvider()
            outcome = execute_request(
                self.request(root, case_ids[:2], mode=EvalExecutionMode.RESUME, source=run_dir),
                target_provider=target,
                judge_provider=judge,
            )
            self.assertEqual(target.target_calls, 1)
            self.assertEqual(judge.judge_calls, 2)
            self.assertEqual(outcome.api_calls, {"target": 1, "judge": 2})
            after_c = [record for record in runner.load_jsonl(run_dir / "judgments.jsonl") if record["case_id"] == case_ids[2]]
            after_d = [record for record in runner.load_jsonl(run_dir / "judgments.jsonl") if record["case_id"] == case_ids[3]]
            self.assertEqual(after_c, before_c)
            self.assertEqual(after_d, before_d)

    def test_resume_thirty_case_judge_error_scope_has_exact_actual_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(30)
            source = execute_request(
                self.request(root, case_ids, mode=EvalExecutionMode.FULL, run_id="resume-thirty"),
                target_provider=StageProvider(["response"] * len(case_ids)),
                judge_provider=StageProvider(
                    judge_outputs={case_id: "not json" for case_id in case_ids[17:]}
                ),
            )
            target = StageProvider(["must not be called"])
            judge = StageProvider()
            outcome = execute_request(
                self.request(root, case_ids, mode=EvalExecutionMode.RESUME, source=source.run_dir),
                target_provider=target,
                judge_provider=judge,
            )
            self.assertEqual(target.target_calls, 0)
            self.assertEqual(judge.judge_calls, 13)
            self.assertEqual(outcome.api_calls, {"target": 0, "judge": 13})

    def test_cli_maps_stage_mode_source_and_selector_without_breaking_full_default(self) -> None:
        definition = discover_evals()[0]
        args = build_parser().parse_args(
            [
                "run", definition.eval_id, "--mode", "judge-only", "--source-run", "C:/tmp/source",
                "--judge-profile", "fake", "--judge-selector", "judge-error-or-missing",
            ]
        )
        request = _request_from_args(args, definition.eval_id, [definition.cases[0].case_id])
        self.assertEqual(request.mode, EvalExecutionMode.JUDGE_ONLY)
        self.assertEqual(request.judge_selector, JudgeCaseSelector.JUDGE_ERROR_OR_MISSING)
        self.assertEqual(request.target_profile, None)
        self.assertEqual(request.judge_profile, "fake")
        default_args = build_parser().parse_args(["run", definition.eval_id, "--profile", "fake"])
        self.assertEqual(_request_from_args(default_args, definition.eval_id, [definition.cases[0].case_id]).mode, EvalExecutionMode.FULL)
        resume_args = build_parser().parse_args(["resume", "--from-run", "C:/tmp/source"])
        self.assertEqual(resume_args.command, "resume")
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit):
            build_parser().parse_args(["rerun-failed"])
        self.assertIn("invalid choice", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
