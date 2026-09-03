"""Kimi Judge compatibility and safe empty-response diagnostics regressions."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.error
import unittest
from email.message import Message
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as runner  # noqa: E402
from eval_console.discovery import discover_evals  # noqa: E402
from eval_console.models import EvalExecutionMode, EvalRunRequest  # noqa: E402
from eval_console.service import execute_request  # noqa: E402


class HTTPResponse:
    def __init__(self, payload: dict[str, Any], *, status: int = 200) -> None:
        self.body = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self) -> "HTTPResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class ConsoleProvider:
    provider_name = "console-fake"

    def __init__(self, *, model: str, thinking: str, target_text: str = "target") -> None:
        self.model = model
        self.target_text = target_text
        self.public_parameters = {"network": False, "thinking": thinking}
        self.target_calls = 0
        self.judge_calls = 0

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        if response_schema is None:
            self.target_calls += 1
            return runner.ProviderResult(self.target_text, reported_model=self.model)
        self.judge_calls += 1
        case_id = response_schema["properties"]["case_id"]["const"]
        criteria = response_schema["properties"]["criteria"]["items"]["properties"][
            "criterion"
        ]["enum"]
        return runner.ProviderResult(
            json.dumps(
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
            ),
            reported_model=self.model,
        )


class KimiJudgeCompatibilityTests(unittest.TestCase):
    @staticmethod
    def kimi_capabilities() -> dict[str, Any]:
        return {
            "reasoning_effort_supported": False,
            "allowed_reasoning_efforts": [],
            "structured_output_modes": ["json_object"],
            "temperature_supported": False,
            "top_p_supported": False,
            "seed_supported": False,
            "max_output_tokens_parameter": "max_completion_tokens",
            "thinking_supported": True,
            "allowed_thinking_types": ["enabled", "disabled"],
            "thinking_parameter": "thinking",
        }

    def kimi_provider(
        self,
        payload: dict[str, Any],
        *,
        model: str = "kimi-k2.6",
        thinking: str | None = "disabled",
        captured: list[dict[str, Any]] | None = None,
        urlopen: Any | None = None,
        sleep: Any | None = None,
        max_retries: int = 1,
    ) -> runner.OpenAICompatibleChatProvider:
        def opener(request: Any, timeout: float) -> HTTPResponse:
            if captured is not None:
                captured.append(json.loads(request.data))
            return HTTPResponse(payload)

        return runner.OpenAICompatibleChatProvider(
            api_key="fake-kimi-key",
            model=model,
            base_url="https://api.moonshot.cn/v1",
            declared_upstream_vendor="Moonshot AI",
            structured_output_mode="json_object",
            structured_output_required=True,
            capabilities=self.kimi_capabilities(),
            max_output_tokens=4096,
            max_retries=max_retries,
            thinking=thinking,
            urlopen=urlopen or opener,
            sleep=sleep,
        )

    @staticmethod
    def rate_limit_error(retry_after: str | None = None) -> urllib.error.HTTPError:
        headers = Message()
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return urllib.error.HTTPError(
            "https://api.moonshot.cn/v1/chat/completions",
            429,
            "rate limit",
            headers,
            io.BytesIO(b'{"error":"rate limit"}'),
        )

    @staticmethod
    def chat_payload(content: str) -> dict[str, Any]:
        return {
            "id": "kimi-response",
            "model": "kimi-k2.6",
            "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        }

    @staticmethod
    def judgment_payload(case: dict[str, Any]) -> dict[str, Any]:
        return {
            "case_id": case["case_id"],
            "criteria": [
                {
                    "criterion": item["criterion"],
                    "passed": True,
                    "reason": "Target 原文包含可核对的对应内容。",
                }
                for item in case["criteria"]
            ],
        }

    @staticmethod
    def opener_from_events(events: list[Any]) -> Any:
        def opener(*_args: Any, **_kwargs: Any) -> Any:
            event = events.pop(0)
            if isinstance(event, BaseException):
                raise event
            return event

        return opener

    def test_single_outer_markdown_fence_normalization_is_strict(self) -> None:
        criteria = [{"criterion": "criterion", "question": "question"}]
        payload = {
            "case_id": "case",
            "criteria": [
                {
                    "criterion": "criterion",
                    "passed": True,
                    "reason": "Target 原文包含可核对的对应内容。",
                }
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False)
        cases = (
            (raw, "none"),
            (f"```json\n{raw}\n```", "markdown_json_fence"),
            (f"```JSON\n{raw}\n```", "markdown_json_fence"),
            (f"```\n{raw}\n```", "markdown_code_fence"),
        )
        for text, expected_normalization in cases:
            with self.subTest(normalization=expected_normalization):
                normalized, normalization = runner.normalize_structured_output_text(text)
                self.assertEqual(normalization, expected_normalization)
                self.assertEqual(
                    runner.parse_judgment(
                        normalized,
                        criteria,
                        expected_case_id="case",
                        normalize_outer_fence=False,
                    ),
                    payload["criteria"],
                )
        invalid = (
            f"Here is the result:\n```json\n{raw}\n```",
            f"```json\n{raw}",
            "```json\n{\"case_id\":\n```",
            "```json\n{\"case_id\": \"case\", \"criteria\": []}\n```",
            f"```json\n{raw}\n```\n```json\n{raw}\n```",
        )
        for text in invalid:
            with self.subTest(text=text):
                with self.assertRaises(runner.ModelEvalError):
                    runner.parse_judgment(text, criteria, expected_case_id="case")

    def test_judge_attempt_persists_structured_output_normalization(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases([cases[0]], criteria)
        judgment = json.dumps(self.judgment_payload(prepared[0]), ensure_ascii=False)
        fixtures = (
            ("raw", judgment, "none"),
            ("json-fence", f"```json\n{judgment}\n```", "markdown_json_fence"),
            ("code-fence", f"```\n{judgment}\n```", "markdown_code_fence"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            for run_id, content, expected_normalization in fixtures:
                with self.subTest(normalization=expected_normalization):
                    run_dir = (
                        Path(temp_dir)
                        / "v1.6.0"
                        / runner.API_RUNTIME_PROFILE
                        / run_id
                    )
                    runner.execute_run(
                        prepared,
                        ConsoleProvider(model="target", thinking="provider-default"),
                        run_dir,
                        repository_sha="a" * 40,
                        repository_dirty=False,
                    )
                    judge = self.kimi_provider(self.chat_payload(content))
                    self.assertEqual(
                        runner.execute_judge(
                            run_dir, judge, case_ids=runner.planned_judge_case_ids(run_dir)
                        )["judged"],
                        1,
                    )
                    record = runner.load_jsonl(run_dir / "judgments.jsonl")[0]
                    self.assertEqual(record["status"], "JUDGMENT")
                    self.assertEqual(
                        record["structured_output_normalization"], expected_normalization
                    )
                    self.assertNotIn("raw_excerpt", record)
                    runner.validate_result_artifacts(run_dir)

    def test_invalid_structured_output_preserves_successful_http_telemetry(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases([cases[0]], criteria)
        judge = self.kimi_provider(self.chat_payload("not valid judgment JSON"))
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = (
                Path(temp_dir)
                / "v1.6.0"
                / runner.API_RUNTIME_PROFILE
                / "invalid-structured-output"
            )
            runner.execute_run(
                prepared,
                ConsoleProvider(model="target", thinking="provider-default"),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )

            counts = runner.execute_judge(
                run_dir, judge, case_ids=runner.planned_judge_case_ids(run_dir)
            )

            record = runner.load_jsonl(run_dir / "judgments.jsonl")[0]
            self.assertEqual(counts["judge_error"], 1)
            self.assertEqual(record["error_code"], "INVALID_STRUCTURED_OUTPUT")
            self.assertEqual(
                record["http_telemetry"],
                {
                    "http_attempts": 1,
                    "retry_count": 0,
                    "rate_limit_count": 0,
                    "total_retry_delay_seconds": 0,
                    "recovered_after_retry": False,
                    "final_http_status": 200,
                    "retry_after_seconds": None,
                    "retry_delay_source": None,
                    "retry_delays_seconds": [],
                    "retry_delay_sources": [],
                },
            )
            runner.validate_result_artifacts(run_dir)

    def test_rate_limit_retry_after_uses_bounded_or_fallback_delay(self) -> None:
        fixtures = (
            ("5", 5.0),
            (None, 1.0),
            ("invalid", 1.0),
            ("99999", runner.MAX_RETRY_AFTER_SECONDS),
        )
        for retry_after, expected_delay in fixtures:
            with self.subTest(retry_after=retry_after):
                events = [
                    self.rate_limit_error(retry_after),
                    HTTPResponse(self.chat_payload("ok")),
                ]
                sleeps: list[float] = []
                provider = self.kimi_provider(
                    {},
                    urlopen=self.opener_from_events(events),
                    sleep=sleeps.append,
                    max_retries=1,
                )
                result = provider.generate(instructions="system", input_text="input")
                self.assertEqual(result.text, "ok")
                self.assertEqual(
                    result.http_telemetry,
                    {
                        "http_attempts": 2,
                        "retry_count": 1,
                        "rate_limit_count": 1,
                        "total_retry_delay_seconds": expected_delay,
                        "recovered_after_retry": True,
                        "final_http_status": 200,
                        "retry_after_seconds": (
                            expected_delay if retry_after not in {None, "invalid"} else None
                        ),
                        "retry_delay_source": (
                            "retry_after"
                            if retry_after not in {None, "invalid"}
                            else "fallback_backoff"
                        ),
                        "retry_delays_seconds": [expected_delay],
                        "retry_delay_sources": [
                            "retry_after"
                            if retry_after not in {None, "invalid"}
                            else "fallback_backoff"
                        ],
                    },
                )
                self.assertEqual(sleeps, [expected_delay])
                self.assertEqual(events, [])

    def test_retry_telemetry_preserves_selected_delay_after_runtime_cooldown_advances(self) -> None:
        clock = {"now": 100.0}
        runtime_remaining: list[float] = []
        telemetry_waits: list[float] = []
        coordinator: runner.ProviderRateLimitCoordinator

        def observe_runtime_cooldown(_delay: float, _extended: bool) -> None:
            clock["now"] += 0.00001
            runtime_remaining.append(coordinator.remaining_cooldown())

        def wait(delay: float) -> None:
            telemetry_waits.append(delay)
            clock["now"] += delay

        coordinator = runner.ProviderRateLimitCoordinator(
            monotonic=lambda: clock["now"],
            wait=wait,
            on_cooldown=observe_runtime_cooldown,
        )
        provider = self.kimi_provider(
            {},
            urlopen=self.opener_from_events(
                [self.rate_limit_error("5"), HTTPResponse(self.chat_payload("ok"))]
            ),
            sleep=lambda _delay: self.fail("429 retry must use the shared coordinator"),
            max_retries=1,
        )
        provider.set_rate_limit_coordinator(coordinator)

        result = provider.generate(instructions="system", input_text="input")

        self.assertEqual(result.http_telemetry["retry_delays_seconds"], [5.0])
        self.assertEqual(result.http_telemetry["retry_delay_sources"], ["retry_after"])
        self.assertEqual(result.http_telemetry["total_retry_delay_seconds"], 5.0)
        self.assertEqual(len(runtime_remaining), 1)
        self.assertLess(runtime_remaining[0], 5.0)
        self.assertEqual(len(telemetry_waits), 1)
        self.assertLess(telemetry_waits[0], 5.0)

    def test_rate_limit_exhaustion_persists_safe_attempt_diagnostics(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases([cases[0]], criteria)
        sleeps: list[float] = []
        judge = self.kimi_provider(
            {},
            urlopen=self.opener_from_events(
                [self.rate_limit_error("5") for _ in range(3)]
            ),
            sleep=sleeps.append,
            max_retries=2,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "v1.6.0" / runner.API_RUNTIME_PROFILE / "rate-limit"
            runner.execute_run(
                prepared,
                ConsoleProvider(model="target", thinking="provider-default"),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            self.assertEqual(
                runner.execute_judge(
                    run_dir, judge, case_ids=runner.planned_judge_case_ids(run_dir)
                )["judge_error"],
                1,
            )
            record = runner.load_jsonl(run_dir / "judgments.jsonl")[0]
            self.assertEqual(record["status"], "JUDGE_ERROR")
            self.assertEqual(record["error_code"], "RATE_LIMIT")
            self.assertTrue(record["retryable"])
            self.assertEqual(record["diagnostics"]["http_status"], 429)
            self.assertEqual(record["diagnostics"]["retry_after_seconds"], 5.0)
            self.assertEqual(record["diagnostics"]["retry_attempt"], 3)
            self.assertEqual(record["diagnostics"]["max_retries"], 2)
            self.assertEqual(record["diagnostics"]["provider_http_attempts"], 3)
            self.assertEqual(
                record["http_telemetry"],
                {
                    "http_attempts": 3,
                    "retry_count": 2,
                    "rate_limit_count": 3,
                    "total_retry_delay_seconds": 10.0,
                    "recovered_after_retry": False,
                    "final_http_status": 429,
                    "retry_after_seconds": 5.0,
                    "retry_delay_source": "retry_after",
                    "retry_delays_seconds": [5.0, 5.0],
                    "retry_delay_sources": ["retry_after", "retry_after"],
                },
            )
            self.assertEqual(len(sleeps), 2)
            self.assertTrue(all(0.0 < delay <= 5.0 for delay in sleeps))
            metadata = runner.load_json_object(run_dir / "run.json")
            self.assertEqual(metadata["api_calls"], {"target": 1, "judge": 1})
            runner.validate_result_artifacts(run_dir)

    def test_rate_limit_then_fenced_judgment_is_one_logical_judge_call(self) -> None:
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases([cases[0]], criteria)
        judgment = json.dumps(self.judgment_payload(prepared[0]), ensure_ascii=False)
        sleeps: list[float] = []
        cooldown_events: list[tuple[float, bool]] = []
        judge = self.kimi_provider(
            {},
            urlopen=self.opener_from_events(
                [
                    self.rate_limit_error("5"),
                    HTTPResponse(self.chat_payload(f"```json\n{judgment}\n```")),
                ]
            ),
            sleep=sleeps.append,
            max_retries=2,
        )
        coordinator = runner.ProviderRateLimitCoordinator(wait=sleeps.append)
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "v1.6.0" / runner.API_RUNTIME_PROFILE / "retry-success"
            runner.execute_run(
                prepared,
                ConsoleProvider(model="target", thinking="provider-default"),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            self.assertEqual(
                runner.execute_judge(
                    run_dir,
                    judge,
                    case_ids=runner.planned_judge_case_ids(run_dir),
                    rate_limit_coordinator=coordinator,
                    on_rate_limit=lambda delay, extended: cooldown_events.append(
                        (delay, extended)
                    ),
                )["judged"],
                1,
            )
            record = runner.load_jsonl(run_dir / "judgments.jsonl")[0]
            self.assertEqual(record["status"], "JUDGMENT")
            self.assertEqual(
                record["structured_output_normalization"], "markdown_json_fence"
            )
            self.assertEqual(
                record["http_telemetry"],
                {
                    "http_attempts": 2,
                    "retry_count": 1,
                    "rate_limit_count": 1,
                    "total_retry_delay_seconds": 5.0,
                    "recovered_after_retry": True,
                    "final_http_status": 200,
                    "retry_after_seconds": 5.0,
                    "retry_delay_source": "retry_after",
                    "retry_delays_seconds": [5.0],
                    "retry_delay_sources": ["retry_after"],
                },
            )
            self.assertEqual(len(sleeps), 1)
            self.assertTrue(0.0 < sleeps[0] <= 5.0)
            self.assertEqual(cooldown_events, [(5.0, False)])
            metadata = runner.load_json_object(run_dir / "run.json")
            self.assertEqual(metadata["api_calls"], {"target": 1, "judge": 1})
            runner.validate_result_artifacts(run_dir)

    def test_empty_response_reason_codes_are_explicit_and_safe(self) -> None:
        cases = (
            ({"choices": []}, "NO_CHOICES"),
            ({"choices": [{"finish_reason": "stop"}]}, "MESSAGE_MISSING"),
            ({"choices": [{"message": {"content": ""}}]}, "CONTENT_EMPTY_STRING"),
            ({"choices": [{"message": {"content": {"not": "text"}}}]}, "CONTENT_NON_TEXT"),
        )
        for payload, reason in cases:
            with self.subTest(reason=reason):
                provider = self.kimi_provider(payload)
                with self.assertRaises(runner.ProviderInvalidResponse) as captured:
                    provider.generate(instructions="system", input_text="input")
                self.assertEqual(captured.exception.code, "EMPTY_RESPONSE")
                self.assertEqual(
                    captured.exception.safe_diagnostics["empty_response_reason"], reason
                )

    def test_content_null_keeps_safe_metadata_without_reasoning_text(self) -> None:
        hidden_reasoning = "DO NOT PERSIST THIS TEXT"
        payload = {
            "id": "resp-test-1",
            "model": "kimi-k2.6",
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {
                        "content": None,
                        "reasoning_content": hidden_reasoning,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 2400,
                "reasoning_tokens": 2300,
                "total_tokens": 2900,
            },
        }
        provider = self.kimi_provider(payload)
        with self.assertRaises(runner.ProviderInvalidResponse) as captured:
            provider.generate(instructions="system", input_text="input")
        error = captured.exception
        self.assertEqual(error.code, "EMPTY_RESPONSE")
        self.assertEqual(error.safe_diagnostics["empty_response_reason"], "CONTENT_NULL")
        self.assertEqual(error.safe_diagnostics["response_id"], "resp-test-1")
        self.assertEqual(error.safe_diagnostics["reported_model"], "kimi-k2.6")
        self.assertEqual(error.safe_diagnostics["finish_reason"], "length")
        self.assertTrue(error.safe_diagnostics["reasoning_present"])
        self.assertGreater(error.safe_diagnostics["reasoning_length"], 0)
        self.assertEqual(error.safe_diagnostics["input_tokens"], 500)
        self.assertEqual(error.safe_diagnostics["output_tokens"], 2400)
        self.assertEqual(error.safe_diagnostics["completion_tokens"], 2400)
        self.assertEqual(error.safe_diagnostics["reasoning_tokens"], 2300)
        self.assertEqual(error.safe_diagnostics["total_tokens"], 2900)
        self.assertNotIn(hidden_reasoning, str(error))
        self.assertNotIn(hidden_reasoning, json.dumps(error.safe_diagnostics))

    def test_empty_judge_attempt_persists_only_safe_diagnostics(self) -> None:
        hidden_reasoning = "DO NOT PERSIST THIS TEXT"
        cases, criteria = runner.load_definitions()
        prepared = runner.prepare_cases([cases[0]], criteria)
        target = self.kimi_provider(
            {
                "id": "target-1",
                "model": "target-kimi",
                "choices": [{"message": {"content": "target answer"}}],
            },
            model="target-kimi",
            thinking="provider-default",
        )
        judge = self.kimi_provider(
            {
                "id": "resp-test-1",
                "model": "kimi-k2.6",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": None,
                            "reasoning_content": hidden_reasoning,
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 500,
                    "completion_tokens": 2400,
                    "reasoning_tokens": 2300,
                    "total_tokens": 2900,
                },
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "v1.6.0" / runner.API_RUNTIME_PROFILE / "kimi-empty"
            runner.execute_run(
                prepared,
                target,
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            self.assertEqual(
                runner.execute_judge(
                    run_dir, judge, case_ids=runner.planned_judge_case_ids(run_dir)
                )["judge_error"],
                1,
            )
            record = runner.load_jsonl(run_dir / "judgments.jsonl")[0]
            self.assertEqual(record["error_code"], "EMPTY_RESPONSE")
            self.assertEqual(record["provider_response_id"], "resp-test-1")
            self.assertEqual(record["reported_model"], "kimi-k2.6")
            self.assertEqual(record["finish_reason"], "length")
            self.assertEqual(record["usage"]["output_tokens"], 2400)
            self.assertEqual(record["diagnostics"]["http_status"], 200)
            self.assertEqual(
                record["diagnostics"]["empty_response_reason"], "CONTENT_NULL"
            )
            self.assertTrue(record["diagnostics"]["reasoning_present"])
            self.assertEqual(record["diagnostics"]["reasoning_tokens"], 2300)
            changed_judge = self.kimi_provider(
                {"choices": []}, thinking="enabled"
            )
            with self.assertRaisesRegex(
                runner.ModelEvalError, "judge resume configuration mismatch"
            ):
                runner.execute_judge(
                    run_dir,
                    changed_judge,
                    resume=True,
                    case_ids=runner.planned_judge_case_ids(run_dir),
                )
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in run_dir.rglob("*")
                if path.is_file()
            )
            self.assertNotIn(hidden_reasoning, persisted)
            runner.validate_result_artifacts(run_dir)

    def test_kimi_profile_payload_and_role_thinking_are_capability_driven(self) -> None:
        profile = {
            "profiles": {
                "kimi-roles": {
                    "provider": "openai_compatible_chat",
                    "api_key_env": "KIMI_TEST_KEY",
                    "base_url": "https://api.moonshot.cn/v1",
                    "declared_upstream_vendor": "Moonshot AI",
                    "capabilities": self.kimi_capabilities(),
                    "target": {"model": "target-kimi", "thinking": "enabled"},
                    "judge": {
                        "model": "judge-kimi",
                        "thinking": "disabled",
                        "structured_output_mode": "json_object",
                        "max_output_tokens": 4096,
                    },
                }
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = Path(temp_dir) / "profiles.json"
            profiles.write_text(json.dumps(profile), encoding="utf-8")

            def args(role: str) -> Any:
                return runner.build_parser().parse_args(
                    [
                        "provider-check",
                        "--role",
                        role,
                        "--profile",
                        "kimi-roles",
                        "--profiles-file",
                        str(profiles),
                    ]
                )

            with mock.patch.dict(os.environ, {"KIMI_TEST_KEY": "fake-key"}, clear=True):
                target = runner.create_provider(args("target"), role="target")
                judge = runner.create_provider(args("judge"), role="judge")
            target_payload = target.build_request_payload(
                instructions="system", input_text="input"
            )
            judge_payload = judge.build_request_payload(
                instructions="system", input_text="input", response_schema={"type": "object"}
            )
            self.assertEqual(target_payload["thinking"], {"type": "enabled"})
            self.assertEqual(judge_payload["thinking"], {"type": "disabled"})
            self.assertEqual(judge_payload["max_completion_tokens"], 4096)
            self.assertNotIn("max_tokens", judge_payload)
            self.assertNotEqual(
                target.configuration_manifest()["provider_config_hash"],
                judge.configuration_manifest()["provider_config_hash"],
            )
            generic = runner.OpenAICompatibleChatProvider(
                api_key="key",
                model="generic",
                base_url="https://relay.example/v1",
                structured_output_required=False,
            )
            self.assertNotIn(
                "thinking",
                generic.build_request_payload(instructions="system", input_text="input"),
            )
            with self.assertRaisesRegex(runner.ModelEvalError, "thinking"):
                runner.OpenAICompatibleChatProvider(
                    api_key="key",
                    model="generic",
                    base_url="https://relay.example/v1",
                    structured_output_required=False,
                    thinking="disabled",
                )
            with self.assertRaisesRegex(runner.ModelEvalError, "thinking"):
                runner.OpenAIResponsesProvider(
                    api_key="key",
                    model="generic",
                    capabilities=self.kimi_capabilities(),
                    structured_output_mode="json_object",
                    thinking="disabled",
                )

    def test_judge_only_creates_a_child_run_for_a_new_judge_configuration(self) -> None:
        profile = {
            "profiles": {
                "source": {"provider": "openai_responses", "target": {"model": "target"}},
                "new-judge": {"provider": "openai_responses", "judge": {"model": "judge"}},
            }
        }
        case_id = discover_evals()[0].cases[0].case_id
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = root / "profiles.json"
            profiles.write_text(json.dumps(profile), encoding="utf-8")
            source_request = EvalRunRequest(
                eval_id=discover_evals()[0].eval_id,
                case_ids=(case_id,),
                target_profile="source",
                judge_profile=None,
                profiles_file=profiles,
                results_root=root / "results",
                allow_dirty_debug=True,
                run_id="source",
                mode=EvalExecutionMode.TARGET_ONLY,
            )
            source_target = ConsoleProvider(model="target", thinking="enabled")
            source = execute_request(source_request, target_provider=source_target)
            judge_request = EvalRunRequest(
                eval_id=discover_evals()[0].eval_id,
                case_ids=(case_id,),
                target_profile=None,
                judge_profile="new-judge",
                profiles_file=profiles,
                results_root=root / "results",
                allow_dirty_debug=True,
                run_id="new-judge",
                mode=EvalExecutionMode.JUDGE_ONLY,
                source_run_dir=source.run_dir,
            )
            new_judge = ConsoleProvider(model="judge", thinking="disabled")
            outcome = execute_request(judge_request, judge_provider=new_judge)
            self.assertEqual(source_target.target_calls, 1)
            self.assertEqual(new_judge.target_calls, 0)
            self.assertEqual(new_judge.judge_calls, 1)
            self.assertEqual(outcome.api_calls, {"target": 0, "judge": 1})
            self.assertNotEqual(outcome.run_dir, source.run_dir)
