"""Small fake-provider E2E for prepare -> run -> judge -> report."""

from __future__ import annotations

import json
import os
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
from eval_console.models import (  # noqa: E402
    EvalExecutionMode,
    EvalRunRequest,
    JudgeCaseSelector,
)
from eval_console.service import execute_request  # noqa: E402


class RecordingFakeProvider:
    provider_name = "fake-e2e"
    model = "fake-e2e-model"
    public_parameters = {"network": False, "single_sample": True}

    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        self.calls.append(
            {
                "instructions": instructions,
                "input": input_text,
                "structured": response_schema is not None,
            }
        )
        return runner.ProviderResult(
            self.outputs.pop(0),
            reported_model=self.model,
            usage={
                "input_tokens": 12,
                "output_tokens": 4,
                "reasoning_tokens": 1,
                "cached_tokens": 2,
            },
        )


class BlockingFakeProvider(RecordingFakeProvider):
    """One deterministic in-flight Target request for responsive Console coverage."""

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
            raise AssertionError("blocking integration fixture only supports Target")
        self.started.set()
        if not self.release.wait(2):
            raise AssertionError("test did not release the in-flight provider call")
        return super().generate(
            instructions=instructions,
            input_text=input_text,
            response_schema=response_schema,
        )


class ParallelFakeProvider:
    """Event-driven fake provider for bounded Target/Judge integration coverage."""

    provider_name = "parallel-fake-e2e"
    model = "parallel-fake-e2e-model"
    public_parameters = {"network": False, "single_sample": True}

    def __init__(self, *, block_target: bool = False, block_judge: bool = False) -> None:
        self.block_target = block_target
        self.block_judge = block_judge
        self.target_release = threading.Event()
        self.judge_release = threading.Event()
        self.target_two_started = threading.Event()
        self.judge_two_started = threading.Event()
        self._lock = threading.Lock()
        self.target_calls = 0
        self.judge_calls = 0
        self.judge_case_ids: list[str] = []
        self.target_active = 0
        self.judge_active = 0
        self.target_peak = 0
        self.judge_peak = 0

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
                self.target_active += 1
                self.target_peak = max(self.target_peak, self.target_active)
                if self.target_calls >= 2:
                    self.target_two_started.set()
            if self.block_target and not self.target_release.wait(3):
                raise AssertionError("test did not release parallel Target calls")
            with self._lock:
                self.target_active -= 1
            return runner.ProviderResult("parallel target response", reported_model=self.model)
        with self._lock:
            self.judge_calls += 1
            self.judge_case_ids.append(
                response_schema["properties"]["case_id"]["const"]
            )
            self.judge_active += 1
            self.judge_peak = max(self.judge_peak, self.judge_active)
            if self.judge_calls >= 2:
                self.judge_two_started.set()
        if self.block_judge and not self.judge_release.wait(3):
            raise AssertionError("test did not release parallel Judge calls")
        with self._lock:
            self.judge_active -= 1
        criteria = response_schema["properties"]["criteria"]["items"]["properties"]["criterion"]["enum"]
        return runner.ProviderResult(
            json.dumps(
                {
                    "case_id": response_schema["properties"]["case_id"]["const"],
                    "criteria": [
                        {"criterion": item, "passed": True, "reason": "可核对的 fake integration 判断。"}
                        for item in criteria
                    ],
                },
                ensure_ascii=False,
            ),
            reported_model=self.model,
        )


def all_pass(record: dict[str, Any]) -> str:
    return json.dumps(
        {
            "case_id": record["case_id"],
            "criteria": [
                {
                    "criterion": item["criterion"],
                    "passed": True,
                    "reason": "Target 原文给出了该项要求对应的可核对内容",
                }
                for item in record["criteria"]
            ]
        },
        ensure_ascii=False,
    )


class ModelEvalPipelineTests(unittest.TestCase):
    def _parallel_profiles(self, root: Path) -> Path:
        profiles = root / "profiles.json"
        profiles.write_text(
            json.dumps(
                {
                    "profiles": {
                        "parallel": {
                            "provider": "openai_responses",
                            "target": {"model": "parallel-fake-e2e-model"},
                            "judge": {"model": "parallel-fake-e2e-model"},
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        return profiles

    def _interrupted_parallel_judge_child(
        self, root: Path, run_id: str
    ) -> tuple[Any, Any, ParallelFakeProvider, tuple[str, ...], Path, bytes]:
        profiles = self._parallel_profiles(root)
        case_ids = tuple(case.case_id for case in discover_evals()[0].cases[:5])
        source = execute_request(
            EvalRunRequest(
                eval_id=discover_evals()[0].eval_id,
                case_ids=case_ids,
                target_profile="parallel",
                judge_profile=None,
                profiles_file=profiles,
                results_root=root / "results",
                allow_dirty_debug=True,
                run_id=f"{run_id}-source",
                mode=EvalExecutionMode.TARGET_ONLY,
                target_concurrency=2,
            ),
            target_provider=ParallelFakeProvider(),
        )
        source_responses = (source.run_dir / "responses.jsonl").read_bytes()
        stop_requested = threading.Event()
        judge = ParallelFakeProvider(block_judge=True)
        result: dict[str, Any] = {}
        errors: list[BaseException] = []

        def execute() -> None:
            try:
                result["outcome"] = execute_request(
                    EvalRunRequest(
                        eval_id=discover_evals()[0].eval_id,
                        case_ids=case_ids,
                        target_profile=None,
                        judge_profile="parallel",
                        profiles_file=profiles,
                        results_root=root / "results",
                        allow_dirty_debug=True,
                        run_id=f"{run_id}-judge",
                        mode=EvalExecutionMode.JUDGE_ONLY,
                        source_run_dir=source.run_dir,
                        judge_selector=JudgeCaseSelector.ALL_TARGET,
                        judge_concurrency=2,
                    ),
                    judge_provider=judge,
                    should_stop=stop_requested.is_set,
                )
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        worker = threading.Thread(target=execute)
        worker.start()
        try:
            self.assertTrue(judge.judge_two_started.wait(3))
            self.assertEqual(judge.target_calls, 0)
            self.assertEqual(judge.judge_case_ids, list(case_ids[:2]))
            stop_requested.set()
            self.assertFalse(any(case_id in judge.judge_case_ids for case_id in case_ids[2:]))
        finally:
            judge.judge_release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        return source, result["outcome"], judge, case_ids, profiles, source_responses

    def test_console_responsive_target_stop_persists_only_completed_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = root / "profiles.json"
            profiles.write_text(
                json.dumps(
                    {"profiles": {"fake": {"provider": "openai_responses", "target": {"model": "fake-e2e-model"}}}}
                ),
                encoding="utf-8",
            )
            case_ids = tuple(case.case_id for case in discover_evals()[0].cases[:2])
            request = EvalRunRequest(
                eval_id=discover_evals()[0].eval_id,
                case_ids=case_ids,
                target_profile="fake",
                judge_profile=None,
                profiles_file=profiles,
                results_root=root / "results",
                allow_dirty_debug=True,
                run_id="responsive-target-stop",
                mode=EvalExecutionMode.TARGET_ONLY,
            )
            provider = BlockingFakeProvider()
            stop_requested = {"value": False}
            result: dict[str, Any] = {}
            errors: list[BaseException] = []

            def execute() -> None:
                try:
                    result["outcome"] = execute_request(
                        request,
                        target_provider=provider,
                        should_stop=lambda: stop_requested["value"],
                    )
                except BaseException as exc:  # pragma: no cover - asserted below
                    errors.append(exc)

            with mock.patch.object(
                runner, "git_fingerprint", return_value={"git_sha": "a" * 40, "git_dirty": False}
            ):
                worker = threading.Thread(target=execute)
                worker.start()
                try:
                    self.assertTrue(provider.started.wait(3))
                    run_dir = root / "results" / "v1.6.0" / runner.API_RUNTIME_PROFILE / request.run_id
                    self.assertEqual(runner.load_jsonl(run_dir / "responses.jsonl"), [])
                    stop_requested["value"] = True
                finally:
                    provider.release.set()
                    worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            outcome = result["outcome"]
            self.assertEqual(provider.calls[0]["structured"], False)
            self.assertEqual(outcome.api_calls, {"target": 1, "judge": 0})
            self.assertEqual(outcome.summary["completion_status"], "INTERRUPTED")
            self.assertEqual(len(runner.load_jsonl(outcome.run_dir / "responses.jsonl")), 1)

    def test_console_full_bounds_target_and_judge_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = root / "profiles.json"
            profiles.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "parallel": {
                                "provider": "openai_responses",
                                "target": {"model": "parallel-fake-e2e-model"},
                                "judge": {"model": "parallel-fake-e2e-model"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            case_ids = tuple(case.case_id for case in discover_evals()[0].cases[:3])
            request = EvalRunRequest(
                eval_id=discover_evals()[0].eval_id,
                case_ids=case_ids,
                target_profile="parallel",
                judge_profile="parallel",
                profiles_file=profiles,
                results_root=root / "results",
                allow_dirty_debug=True,
                run_id="parallel-full",
                mode=EvalExecutionMode.FULL,
                target_concurrency=2,
                judge_concurrency=2,
            )
            provider = ParallelFakeProvider(block_target=True, block_judge=True)
            result: dict[str, Any] = {}

            def execute() -> None:
                result["outcome"] = execute_request(
                    request, target_provider=provider, judge_provider=provider
                )

            worker = threading.Thread(target=execute)
            worker.start()
            try:
                self.assertTrue(provider.target_two_started.wait(3))
                self.assertEqual(provider.target_calls, 2)
                self.assertEqual(provider.judge_calls, 0)
                provider.target_release.set()
                self.assertTrue(provider.judge_two_started.wait(3))
                self.assertEqual(provider.target_calls, len(case_ids))
                provider.judge_release.set()
            finally:
                provider.target_release.set()
                provider.judge_release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())
            outcome = result["outcome"]
            self.assertEqual(outcome.summary["completion_status"], "COMPLETED")
            self.assertEqual(provider.target_peak, 2)
            self.assertEqual(provider.judge_peak, 2)
            metadata = runner.load_json_object(outcome.run_dir / "run.json")
            self.assertEqual(metadata["parallel_metrics"], {
                "target_peak_in_flight": 2,
                "judge_peak_in_flight": 2,
            })
            self.assertTrue(metadata["judge_phase_completed"])

    def test_parallel_judge_interrupt_marks_phase_incomplete_and_resume_runs_missing_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, interrupted, judge, case_ids, profiles, source_responses = (
                self._interrupted_parallel_judge_child(root, "judge-interrupted")
            )
            self.assertEqual(interrupted.api_calls, {"target": 0, "judge": 2})
            metadata = runner.load_json_object(interrupted.run_dir / "run.json")
            self.assertEqual(metadata["status"], "INTERRUPTED")
            self.assertTrue(metadata["interrupted"])
            self.assertFalse(metadata["judge_phase_completed"])
            self.assertIsNone(metadata["judge_completed_at"])
            self.assertIsNone(metadata["completed_at"])
            self.assertEqual(metadata["parallel_metrics"]["judge_peak_in_flight"], 2)
            judgments = runner.load_jsonl(interrupted.run_dir / "judgments.jsonl")
            self.assertEqual({record["case_id"] for record in judgments}, set(case_ids[:2]))
            self.assertEqual(judge.judge_case_ids, list(case_ids[:2]))
            self.assertEqual((source.run_dir / "responses.jsonl").read_bytes(), source_responses)
            runner.validate_result_artifacts(interrupted.run_dir)

            resumed_judge = ParallelFakeProvider()
            resumed = execute_request(
                EvalRunRequest(
                    eval_id=discover_evals()[0].eval_id,
                    case_ids=case_ids,
                    target_profile="parallel",
                    judge_profile="parallel",
                    profiles_file=profiles,
                    results_root=root / "results",
                    allow_dirty_debug=True,
                    mode=EvalExecutionMode.RESUME,
                    source_run_dir=interrupted.run_dir,
                    judge_concurrency=1,
                ),
                judge_provider=resumed_judge,
            )
            self.assertEqual(resumed.api_calls, {"target": 0, "judge": 3})
            self.assertEqual(resumed_judge.judge_case_ids, list(case_ids[2:]))
            metadata = runner.load_json_object(interrupted.run_dir / "run.json")
            self.assertTrue(metadata["judge_phase_completed"])
            self.assertIn(metadata["status"], {"COMPLETED", "COMPLETED_WITH_ERRORS"})
            runner.validate_result_artifacts(interrupted.run_dir)

    def test_parallel_judge_selected_resume_keeps_run_phase_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, interrupted, _, case_ids, profiles, _ = self._interrupted_parallel_judge_child(
                root, "judge-selected"
            )
            selected = (case_ids[2], case_ids[4])
            resumed_judge = ParallelFakeProvider()
            resumed = execute_request(
                EvalRunRequest(
                    eval_id=discover_evals()[0].eval_id,
                    case_ids=selected,
                    target_profile="parallel",
                    judge_profile="parallel",
                    profiles_file=profiles,
                    results_root=root / "results",
                    allow_dirty_debug=True,
                    mode=EvalExecutionMode.RESUME,
                    source_run_dir=interrupted.run_dir,
                ),
                judge_provider=resumed_judge,
            )
            self.assertEqual(resumed.api_calls, {"target": 0, "judge": 2})
            self.assertEqual(resumed_judge.judge_case_ids, list(selected))
            judgments = runner.load_jsonl(interrupted.run_dir / "judgments.jsonl")
            self.assertEqual({record["case_id"] for record in judgments}, set(case_ids) - {case_ids[3]})
            metadata = runner.load_json_object(interrupted.run_dir / "run.json")
            self.assertFalse(metadata["judge_phase_completed"])
            self.assertEqual(metadata["status"], "JUDGE_PARTIAL")
            runner.validate_result_artifacts(interrupted.run_dir)

    def test_parallel_interrupt_resume_preserves_missing_case_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = root / "profiles.json"
            profiles.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "parallel": {
                                "provider": "openai_responses",
                                "target": {"model": "parallel-fake-e2e-model"},
                                "judge": {"model": "parallel-fake-e2e-model"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            case_ids = tuple(case.case_id for case in discover_evals()[0].cases[:5])
            stop_requested = threading.Event()
            initial_request = EvalRunRequest(
                eval_id=discover_evals()[0].eval_id,
                case_ids=case_ids,
                target_profile="parallel",
                judge_profile="parallel",
                profiles_file=profiles,
                results_root=root / "results",
                allow_dirty_debug=True,
                run_id="parallel-interrupted",
                mode=EvalExecutionMode.FULL,
                target_concurrency=2,
            )
            initial_provider = ParallelFakeProvider(block_target=True)
            result: dict[str, Any] = {}

            def execute_initial() -> None:
                result["outcome"] = execute_request(
                    initial_request,
                    target_provider=initial_provider,
                    judge_provider=ParallelFakeProvider(),
                    should_stop=stop_requested.is_set,
                )

            worker = threading.Thread(target=execute_initial)
            worker.start()
            try:
                self.assertTrue(initial_provider.target_two_started.wait(3))
                stop_requested.set()
                initial_provider.target_release.set()
            finally:
                worker.join(5)
            self.assertFalse(worker.is_alive())
            source = result["outcome"]
            self.assertEqual(source.summary["completion_status"], "INTERRUPTED")
            self.assertEqual(len(runner.load_jsonl(source.run_dir / "responses.jsonl")), 2)

            selected = (case_ids[2], case_ids[4])
            resumed = execute_request(
                EvalRunRequest(
                    eval_id=discover_evals()[0].eval_id,
                    case_ids=selected,
                    target_profile="parallel",
                    judge_profile="parallel",
                    profiles_file=profiles,
                    results_root=root / "results",
                    allow_dirty_debug=True,
                    mode=EvalExecutionMode.RESUME,
                    source_run_dir=source.run_dir,
                    target_concurrency=1,
                    judge_concurrency=1,
                ),
                target_provider=ParallelFakeProvider(),
                judge_provider=ParallelFakeProvider(),
            )
            self.assertEqual(resumed.api_calls, {"target": 2, "judge": 2})
            attempts = runner.load_jsonl(source.run_dir / "responses.jsonl")
            self.assertFalse(any(item["case_id"] == case_ids[3] for item in attempts))
            metadata = runner.load_json_object(source.run_dir / "run.json")
            self.assertEqual(metadata["console"]["target_concurrency"], 2)

    def test_role_specific_relay_profiles_ignore_global_openai_base_url(self) -> None:
        profile = {
            "profiles": {
                "split-relays": {
                    "provider": "openai_compatible_chat",
                    "capabilities": {
                        "reasoning_effort_supported": False,
                        "allowed_reasoning_efforts": [],
                        "structured_output_modes": ["json_object"],
                        "temperature_supported": False,
                        "top_p_supported": False,
                        "seed_supported": False,
                        "max_output_tokens_parameter": "max_tokens",
                    },
                    "target": {
                        "api_key_env": "TARGET_KEY",
                        "base_url": "https://relay-a.example/v1",
                        "model": "target-model",
                    },
                    "judge": {
                        "api_key_env": "JUDGE_KEY",
                        "base_url": "https://relay-b.example/v1",
                        "model": "judge-model",
                        "structured_output_mode": "json_object",
                    },
                }
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = Path(temp_dir) / "profiles.json"
            profiles.write_text(json.dumps(profile), encoding="utf-8")

            def provider(role: str) -> runner.ModelProvider:
                args = runner.build_parser().parse_args(
                    [
                        "provider-check",
                        "--role",
                        role,
                        "--profile",
                        "split-relays",
                        "--profiles-file",
                        str(profiles),
                    ]
                )
                return runner.create_provider(args, role=role)

            with mock.patch.dict(
                os.environ,
                {
                    "TARGET_KEY": "target-value",
                    "JUDGE_KEY": "judge-value",
                    "OPENAI_BASE_URL": "https://global-third.example/v1",
                },
                clear=True,
            ):
                target = provider("target")
                judge = provider("judge")
        self.assertEqual(target.endpoint_origin, "https://relay-a.example")
        self.assertEqual(judge.endpoint_origin, "https://relay-b.example")
        self.assertNotEqual(target.endpoint_hash, judge.endpoint_hash)

    def test_two_case_fake_provider_pipeline(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases[:2], criteria)
        self.assertIn("SKILL.md", prepared[0]["runtime"]["sources"])
        self.assertIn("relationship-compass", prepared[0]["runtime"]["content"])

        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = (
                Path(temp_dir)
                / "v1.6.0"
                / runner.API_RUNTIME_PROFILE
                / "fake-e2e-run"
            )
            target = RecordingFakeProvider(["回复一", "回复二"])
            runner.execute_run(
                prepared,
                target,
                run_dir,
                repository_sha="b" * 40,
                repository_dirty=False,
                knowledge_pack_version="1.6.0",
            )
            judge = RecordingFakeProvider([all_pass(prepared[0]), all_pass(prepared[1])])
            runner.execute_judge(
                run_dir, judge, case_ids=runner.planned_judge_case_ids(run_dir)
            )
            summary = runner.build_report(run_dir)

            self.assertEqual(len(target.calls), 2)
            self.assertTrue(all(not call["structured"] for call in target.calls))
            self.assertEqual(len(judge.calls), 2)
            self.assertTrue(all(call["structured"] for call in judge.calls))
            self.assertEqual(summary["completion_status"], "COMPLETED")
            self.assertEqual(summary["behavioral_status"], "PASS")
            self.assertEqual(summary["runtime_profile"], runner.API_RUNTIME_PROFILE)
            self.assertFalse(summary["baseline"])
            self.assertEqual(summary["execution"]["target"], "PURE_API")
            self.assertEqual(summary["execution"]["judge"], "PURE_API")
            self.assertEqual(
                summary["provider_provenance"]["target"]["model_identity"]["status"],
                "MATCHED",
            )
            self.assertEqual(summary["usage"]["target"]["cached_tokens"], 4)
            self.assertIn("core", summary["suites"])
            for call, record in zip(target.calls, prepared, strict=True):
                self.assertEqual(call["input"], runner.target_input(record))
                self.assertNotIn(record["case_id"], call["input"])
                self.assertNotIn("当前评测模式", call["input"])
                for criterion in record["criteria"]:
                    self.assertNotIn(criterion["criterion"], call["input"])
            runner.validate_result_artifacts(run_dir)
            for filename in (
                "run.json",
                "prepared.jsonl",
                "eval-definition.json",
                "runtime-snapshot.json",
                "source-snapshots.json",
                "responses.jsonl",
                "judgments.jsonl",
                "summary.json",
            ):
                self.assertTrue((run_dir / filename).is_file(), filename)

    def test_full_fake_manual_pipeline(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases, criteria)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manual_dir = root / ".work" / "manual-full"
            runner.export_manual_bundle(
                prepared,
                manual_dir,
                run_id="manual-full-e2e",
                target_model="chatgpt-project",
            )
            runtime_snapshot = runner.load_json_object(
                manual_dir / "runtime-snapshot.json"
            )
            self.assertEqual(
                runtime_snapshot["runtime_profile"], runner.CHATGPT_RUNTIME_PROFILE
            )
            self.assertIn("project_instructions", runtime_snapshot)
            self.assertGreater(len(runtime_snapshot["knowledge"]), 0)

            target_files = sorted((manual_dir / "target").glob("*.md"))
            self.assertEqual(len(target_files), len(prepared))
            for path, record in zip(target_files, prepared, strict=True):
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text, record["input"].rstrip() + "\n")
                self.assertNotIn(record["case_id"], text)
                self.assertNotIn("当前评测模式", text)
                for criterion in record["criteria"]:
                    self.assertNotIn(criterion["criterion"], text)

            response_file = root / "manual-responses.jsonl"
            runner.write_jsonl(
                response_file,
                [
                    {"case_id": record["case_id"], "response": f"回答-{index:02d}"}
                    for index, record in enumerate(prepared, start=1)
                ],
            )
            run_dir, metadata, imported = runner.import_manual_responses(
                manual_dir, response_file, root / "results"
            )
            self.assertEqual(imported, len(prepared))
            self.assertEqual(metadata["runtime_profile"], runner.CHATGPT_RUNTIME_PROFILE)
            self.assertEqual(metadata["status"], "TARGET_COMPLETE")
            self.assertEqual(metadata["counts"]["model_response"], len(prepared))

            judge_dir = root / ".work" / "judge-full"
            runner.export_manual_judge(run_dir, judge_dir)
            judge_files = sorted(
                path
                for path in judge_dir.glob("*.md")
                if path.name != "INSTRUCTIONS.md"
            )
            self.assertEqual(len(judge_files), len(prepared))
            first_judge_prompt = judge_files[0].read_text(encoding="utf-8")
            self.assertIn(prepared[0]["input"], first_judge_prompt)
            self.assertIn("回答-01", first_judge_prompt)
            self.assertIn(runner.JUDGE_CALIBRATION, first_judge_prompt)
            for criterion in prepared[0]["criteria"]:
                self.assertIn(criterion["criterion"], first_judge_prompt)
                self.assertIn(criterion["question"], first_judge_prompt)

            judgment_file = root / "manual-judgments.jsonl"
            runner.write_jsonl(
                judgment_file,
                [
                    json.loads(all_pass(record)) | {"case_id": record["case_id"]}
                    for record in prepared
                ],
            )
            metadata, imported = runner.import_manual_judgments(
                run_dir,
                judgment_file,
                judge_mode="manual_chatgpt",
                judge_model="chatgpt-project-independent-judge",
            )
            self.assertEqual(imported, len(prepared))
            self.assertEqual(metadata["status"], "COMPLETED")
            self.assertEqual(metadata["judge"]["provider"], "chatgpt_web_manual")
            self.assertEqual(
                metadata["judge"]["parameters"]["judge_mode"], "manual_chatgpt"
            )

            summary = runner.build_report(run_dir)
            self.assertEqual(summary["completion_status"], "COMPLETED")
            self.assertEqual(summary["behavioral_status"], "PASS")
            self.assertEqual(summary["runtime_profile"], runner.CHATGPT_RUNTIME_PROFILE)
            self.assertEqual(summary["counts"]["passed_cases"], len(prepared))
            runner.validate_result_artifacts(run_dir)

    def test_manual_target_can_use_independent_api_judge(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases(cases, criteria)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manual_dir = root / ".work" / "hybrid-target"
            runner.export_manual_bundle(
                prepared,
                manual_dir,
                run_id="hybrid-e2e",
                target_model="chatgpt-user-reported",
            )
            response_file = root / "manual-responses.jsonl"
            runner.write_jsonl(
                response_file,
                [
                    {"case_id": record["case_id"], "response": f"回答-{index:02d}"}
                    for index, record in enumerate(prepared, start=1)
                ],
            )
            run_dir, _, _ = runner.import_manual_responses(
                manual_dir, response_file, root / "results"
            )
            judge = RecordingFakeProvider([all_pass(record) for record in prepared])
            counts = runner.execute_judge(
                run_dir, judge, case_ids=runner.planned_judge_case_ids(run_dir)
            )
            summary = runner.build_report(run_dir)

            self.assertEqual(counts["judged"], len(prepared))
            self.assertEqual(summary["completion_status"], "COMPLETED")
            self.assertEqual(
                summary["provider_manifest"]["target"]["provider"],
                "chatgpt_web_manual",
            )
            self.assertEqual(summary["provider_manifest"]["judge"]["provider"], "fake-e2e")
            self.assertNotEqual(
                summary["provider_manifest"]["target"]["provider_config_hash"],
                summary["provider_manifest"]["judge"]["provider_config_hash"],
            )
            runner.validate_result_artifacts(run_dir)


if __name__ == "__main__":
    unittest.main()
