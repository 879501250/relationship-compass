from __future__ import annotations

import json
import io
import signal
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as runner  # noqa: E402
from eval_console.discovery import discover_evals  # noqa: E402
from eval_console.cli import _GracefulStop  # noqa: E402
from eval_console.models import EvalExecutionMode, EvalRunRequest  # noqa: E402
from eval_console.service import execute_request  # noqa: E402


def judgment(schema: dict[str, Any]) -> str:
    return json.dumps(
        {
            "case_id": schema["properties"]["case_id"]["const"],
            "criteria": [
                {
                    "criterion": criterion,
                    "passed": True,
                    "reason": "可核对的测试判断。",
                }
                for criterion in schema["properties"]["criteria"]["items"]["properties"][
                    "criterion"
                ]["enum"]
            ],
        },
        ensure_ascii=False,
    )


class SchedulerProbe:
    def __init__(self, case_ids: tuple[str, ...]) -> None:
        self.started = {case_id: threading.Event() for case_id in case_ids}
        self.release = {case_id: threading.Event() for case_id in case_ids}
        self._lock = threading.Lock()
        self.calls: list[str] = []
        self.active = 0
        self.peak = 0

    def call(self, case_id: str) -> str:
        with self._lock:
            self.calls.append(case_id)
            self.active += 1
            self.peak = max(self.peak, self.active)
        self.started[case_id].set()
        if not self.release[case_id].wait(3):
            raise AssertionError(f"test did not release {case_id}")
        with self._lock:
            self.active -= 1
        return case_id


class BlockingTargetProvider:
    provider_name = "parallel-test"
    model = "parallel-test-model"
    public_parameters = {"network": False, "single_sample": True}

    def __init__(self, *, block: bool) -> None:
        self.block = block
        self.release = threading.Event()
        self.two_started = threading.Event()
        self._lock = threading.Lock()
        self.calls = 0
        self.active = 0
        self.peak = 0

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        if response_schema is not None:
            raise AssertionError("target fixture received a judge request")
        with self._lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.calls >= 2:
                self.two_started.set()
        if self.block and not self.release.wait(3):
            raise AssertionError("test did not release parallel target calls")
        with self._lock:
            self.active -= 1
        return runner.ProviderResult("parallel target response", reported_model=self.model)


class SuccessProvider(BlockingTargetProvider):
    def __init__(self) -> None:
        super().__init__(block=False)
        self.judge_calls = 0

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        if response_schema is not None:
            with self._lock:
                self.judge_calls += 1
            return runner.ProviderResult(judgment(response_schema), reported_model=self.model)
        return super().generate(
            instructions=instructions,
            input_text=input_text,
            response_schema=response_schema,
        )


class FullBarrierProvider(SuccessProvider):
    def __init__(self) -> None:
        super().__init__()
        self.block = True
        self.judge_started = threading.Event()
        self.target_calls = 0
        self.judge_active = 0
        self.judge_peak = 0
        self.target_calls_when_judge_started: int | None = None

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        if response_schema is None:
            with self._lock:
                self.target_calls += 1
            return super().generate(
                instructions=instructions,
                input_text=input_text,
                response_schema=response_schema,
            )
        with self._lock:
            self.judge_calls += 1
            self.judge_active += 1
            self.judge_peak = max(self.judge_peak, self.judge_active)
            if self.target_calls_when_judge_started is None:
                self.target_calls_when_judge_started = self.target_calls
                self.judge_started.set()
        result = runner.ProviderResult(judgment(response_schema), reported_model=self.model)
        with self._lock:
            self.judge_active -= 1
        return result


class BlockingJudgeProvider(SuccessProvider):
    def __init__(self, *, block: bool, expected_workers: int = 2) -> None:
        super().__init__()
        self.block = block
        self.expected_workers = expected_workers
        self.judge_release = threading.Event()
        self.judge_workers_started = threading.Event()
        self.judge_workers_finished = threading.Event()
        self.judge_active = 0
        self.judge_peak = 0

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
        with self._lock:
            self.judge_calls += 1
            self.judge_active += 1
            self.judge_peak = max(self.judge_peak, self.judge_active)
            if self.judge_calls >= self.expected_workers:
                self.judge_workers_started.set()
        if self.block and not self.judge_release.wait(3):
            raise AssertionError("test did not release parallel Judge calls")
        with self._lock:
            self.judge_active -= 1
            if self.judge_active == 0:
                self.judge_workers_finished.set()
        return runner.ProviderResult(judgment(response_schema), reported_model=self.model)


class JudgeErrorProvider(SuccessProvider):
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
        with self._lock:
            self.judge_calls += 1
            call_number = self.judge_calls
        if call_number == 2:
            raise runner.ProviderError("judge transport failure", code="NETWORK_ERROR")
        return runner.ProviderResult(judgment(response_schema), reported_model=self.model)


class ParallelExecutionTests(unittest.TestCase):
    @staticmethod
    def case_ids(count: int) -> tuple[str, ...]:
        return tuple(case.case_id for case in discover_evals()[0].cases[:count])

    def profiles(self, root: Path) -> Path:
        path = root / "profiles.json"
        path.write_text(
            json.dumps(
                {
                    "profiles": {
                        "parallel": {
                            "provider": "openai_responses",
                            "target": {"model": "parallel-test-model"},
                            "judge": {"model": "parallel-test-model"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    def request(
        self,
        root: Path,
        case_ids: tuple[str, ...],
        *,
        mode: EvalExecutionMode,
        run_id: str | None = None,
        source: Path | None = None,
        target_concurrency: int = 1,
        judge_concurrency: int = 1,
    ) -> EvalRunRequest:
        return EvalRunRequest(
            eval_id=discover_evals()[0].eval_id,
            case_ids=case_ids,
            target_profile="parallel" if mode is not EvalExecutionMode.JUDGE_ONLY else None,
            judge_profile="parallel" if mode is not EvalExecutionMode.TARGET_ONLY else None,
            profiles_file=self.profiles(root),
            results_root=root / "results",
            allow_dirty_debug=True,
            mode=mode,
            run_id=run_id,
            source_run_dir=source,
            target_concurrency=target_concurrency,
            judge_concurrency=judge_concurrency,
        )

    def test_scheduler_is_bounded_fifo_and_never_eagerly_starts_pending_cases(self) -> None:
        case_ids = ("A", "B", "C", "D", "E")
        probe = SchedulerProbe(case_ids)
        scheduler = runner.BoundedCaseScheduler(
            list(case_ids),
            concurrency=2,
            provider_call=probe.call,
        )
        completed: list[str] = []

        def collect() -> None:
            completed.extend(item.value for item in scheduler.completions())

        collector = threading.Thread(target=collect)
        collector.start()
        try:
            self.assertTrue(probe.started["A"].wait(1))
            self.assertTrue(probe.started["B"].wait(1))
            self.assertFalse(probe.started["C"].is_set())
            self.assertFalse(probe.started["D"].is_set())
            self.assertFalse(probe.started["E"].is_set())
            self.assertEqual(scheduler.peak_in_flight, 2)
            self.assertEqual(probe.peak, 2)

            probe.release["B"].set()
            self.assertTrue(probe.started["C"].wait(1))
            self.assertFalse(probe.started["D"].is_set())
            self.assertFalse(probe.started["E"].is_set())

            probe.release["A"].set()
            probe.release["C"].set()
            self.assertTrue(probe.started["D"].wait(1))
            self.assertTrue(probe.started["E"].wait(1))
        finally:
            for event in probe.release.values():
                event.set()
            collector.join(3)
        self.assertFalse(collector.is_alive())
        self.assertEqual(set(completed), set(case_ids))
        self.assertEqual(scheduler.peak_in_flight, 2)

    def test_parallel_interrupt_resume_and_selected_scope_preserve_case_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(5)
            stop_requested = threading.Event()
            initial = BlockingTargetProvider(block=True)
            result: dict[str, Any] = {}

            def run_initial() -> None:
                result["outcome"] = execute_request(
                    self.request(
                        root,
                        case_ids,
                        mode=EvalExecutionMode.TARGET_ONLY,
                        run_id="parallel-interrupted",
                        target_concurrency=2,
                    ),
                    target_provider=initial,
                    should_stop=stop_requested.is_set,
                )

            execution = threading.Thread(target=run_initial)
            execution.start()
            try:
                self.assertTrue(initial.two_started.wait(3))
                self.assertEqual(initial.calls, 2)
                stop_requested.set()
                initial.release.set()
            finally:
                execution.join(5)
            self.assertFalse(execution.is_alive())
            first = result["outcome"]
            self.assertEqual(first.summary["completion_status"], "INTERRUPTED")
            first_attempts = runner.load_jsonl(first.run_dir / "responses.jsonl")
            self.assertEqual({item["case_id"] for item in first_attempts}, set(case_ids[:2]))
            self.assertEqual(len(first_attempts), 2)

            selected = (case_ids[2], case_ids[4])
            target = SuccessProvider()
            judge = SuccessProvider()
            resumed = execute_request(
                self.request(
                    root,
                    selected,
                    mode=EvalExecutionMode.RESUME,
                    source=first.run_dir,
                    target_concurrency=1,
                    judge_concurrency=1,
                ),
                target_provider=target,
                judge_provider=judge,
            )
            self.assertEqual(resumed.api_calls, {"target": 2, "judge": 2})
            attempts = runner.load_jsonl(first.run_dir / "responses.jsonl")
            self.assertEqual(
                {item["case_id"] for item in attempts},
                {case_ids[0], case_ids[1], case_ids[2], case_ids[4]},
            )
            self.assertFalse(any(item["case_id"] == case_ids[3] for item in attempts))
            metadata = runner.load_json_object(first.run_dir / "run.json")
            self.assertEqual(metadata["console"]["target_concurrency"], 2)
            self.assertEqual(metadata["execution_history"][-1]["target_concurrency"], 2)
            self.assertEqual(metadata["parallel_metrics"]["target_peak_in_flight"], 2)

    def test_full_run_keeps_stage_barrier_and_independent_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            case_ids = self.case_ids(3)
            provider = FullBarrierProvider()
            result: dict[str, Any] = {}

            def execute() -> None:
                result["outcome"] = execute_request(
                    self.request(
                        root,
                        case_ids,
                        mode=EvalExecutionMode.FULL,
                        run_id="full-barrier",
                        target_concurrency=2,
                        judge_concurrency=1,
                    ),
                    target_provider=provider,
                    judge_provider=provider,
                )

            execution = threading.Thread(target=execute)
            execution.start()
            try:
                self.assertTrue(provider.two_started.wait(3))
                self.assertFalse(provider.judge_started.is_set())
                provider.release.set()
            finally:
                execution.join(5)
            self.assertFalse(execution.is_alive())
            outcome = result["outcome"]
            self.assertEqual(outcome.summary["completion_status"], "COMPLETED")
            self.assertEqual(provider.target_calls, len(case_ids))
            self.assertEqual(provider.judge_calls, len(case_ids))
            self.assertEqual(provider.target_calls_when_judge_started, len(case_ids))
            self.assertEqual(provider.peak, 2)
            self.assertEqual(provider.judge_peak, 1)

    def test_parallel_provider_errors_are_isolated_and_artifacts_have_one_writer(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases[:3], criteria)
        outcomes: list[Any] = [
            runner.ProviderError("offline", code="NETWORK_ERROR", retryable=True),
            "ok-1",
            "ok-2",
        ]

        class ErrorProvider(SuccessProvider):
            def generate(self, **kwargs: Any) -> runner.ProviderResult:
                if kwargs.get("response_schema") is not None:
                    return super().generate(**kwargs)
                with self._lock:
                    next_value = outcomes.pop(0)
                if isinstance(next_value, BaseException):
                    raise next_value
                return runner.ProviderResult(str(next_value), reported_model=self.model)

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "single-writer"
            main_thread = threading.get_ident()
            append_threads: list[int] = []
            write_threads: list[int] = []
            original_append = runner.append_jsonl
            original_write = runner.write_json

            def capture_append(handle: Any, record: dict[str, Any]) -> None:
                append_threads.append(threading.get_ident())
                original_append(handle, record)

            def capture_write(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
                write_threads.append(threading.get_ident())
                original_write(path, value, exclusive=exclusive)

            with mock.patch.object(runner, "append_jsonl", side_effect=capture_append), mock.patch.object(
                runner, "write_json", side_effect=capture_write
            ):
                metadata = runner.execute_run(
                    prepared,
                    ErrorProvider(),
                    run_dir,
                    repository_sha="a" * 40,
                    repository_dirty=False,
                    target_concurrency=2,
                    continue_on_error=True,
                )
            records = runner.load_jsonl(run_dir / "responses.jsonl")
            self.assertEqual(metadata["api_calls"]["target"], 3)
            self.assertEqual(len(records), 3)
            self.assertEqual(sum(item["status"] == "TARGET_ERROR" for item in records), 1)
            self.assertTrue(append_threads)
            self.assertTrue(write_threads)
            self.assertEqual(set(append_threads), {main_thread})
            self.assertEqual(set(write_threads), {main_thread})

    def test_parallel_judge_errors_still_complete_the_run_level_phase(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases[:3], criteria)
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = (
                runner.results_root(
                    runner.prepared_version(prepared),
                    Path(temp_dir) / "results",
                    runner.API_RUNTIME_PROFILE,
                )
                / "parallel-judge-errors"
            )
            runner.execute_run(
                prepared,
                SuccessProvider(),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            runner.execute_judge(
                run_dir,
                JudgeErrorProvider(),
                case_ids=tuple(record["case_id"] for record in prepared),
                judge_concurrency=2,
            )
            metadata = runner.load_json_object(run_dir / "run.json")
            self.assertTrue(metadata["judge_phase_completed"])
            self.assertEqual(metadata["status"], "COMPLETED_WITH_ERRORS")
            self.assertEqual(metadata["counts"]["judge_error"], 1)
            runner.validate_result_artifacts(run_dir)

    def test_parallel_force_stop_abandons_daemon_workers_without_late_writes(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases[:3], criteria)
        provider = BlockingTargetProvider(block=True)
        acknowledged = threading.Event()
        stop = _GracefulStop(2, 1, stream=io.StringIO())

        def should_stop() -> bool:
            if stop.requested:
                acknowledged.set()
            return stop.requested

        def interrupt_twice() -> None:
            self.assertTrue(provider.two_started.wait(3))
            signal.raise_signal(signal.SIGINT)
            self.assertTrue(acknowledged.wait(2))
            signal.raise_signal(signal.SIGINT)

        notifier = threading.Thread(target=interrupt_twice, daemon=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "parallel-force-stop"
            notifier.start()
            try:
                with self.assertRaises(KeyboardInterrupt):
                    with stop:
                        runner.execute_run(
                            prepared,
                            provider,
                            run_dir,
                            repository_sha="a" * 40,
                            repository_dirty=False,
                            target_concurrency=2,
                            should_stop=should_stop,
                        )
                self.assertTrue(stop.force_requested)
                self.assertTrue(
                    any(
                        thread.name == "eval-provider-case" and thread.is_alive()
                        for thread in threading.enumerate()
                    )
                )
                self.assertEqual(runner.load_jsonl(run_dir / "responses.jsonl"), [])
            finally:
                provider.release.set()
                notifier.join(3)
        self.assertFalse(notifier.is_alive())

    def test_parallel_judge_force_stop_does_not_wait_for_blocked_workers(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases[:3], criteria)
        acknowledged = threading.Event()
        provider = BlockingJudgeProvider(block=True, expected_workers=3)
        stop = _GracefulStop(1, 3, stream=io.StringIO())

        def should_stop() -> bool:
            if stop.requested:
                acknowledged.set()
            return stop.requested

        def interrupt_twice() -> None:
            self.assertTrue(provider.judge_workers_started.wait(3))
            signal.raise_signal(signal.SIGINT)
            self.assertTrue(acknowledged.wait(2))
            signal.raise_signal(signal.SIGINT)

        notifier = threading.Thread(target=interrupt_twice, daemon=True)
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = (
                runner.results_root(
                    runner.prepared_version(prepared),
                    Path(temp_dir) / "results",
                    runner.API_RUNTIME_PROFILE,
                )
                / "parallel-judge-force-stop"
            )
            runner.execute_run(
                prepared,
                SuccessProvider(),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            notifier.start()
            try:
                with self.assertRaises(KeyboardInterrupt):
                    with stop:
                        runner.execute_judge(
                            run_dir,
                            provider,
                            case_ids=tuple(record["case_id"] for record in prepared),
                            judge_concurrency=3,
                            should_stop=should_stop,
                        )
                self.assertTrue(stop.force_requested)
                self.assertFalse((run_dir / "judgments.jsonl").exists())
            finally:
                provider.judge_release.set()
                notifier.join(3)
            self.assertTrue(provider.judge_workers_finished.wait(2))
            self.assertFalse((run_dir / "judgments.jsonl").exists())
        self.assertFalse(notifier.is_alive())
