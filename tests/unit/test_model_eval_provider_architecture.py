"""Provider, provenance, resume, and reference-qualification regression tests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as runner  # noqa: E402


class HTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "HTTPResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class RecordingProvider:
    provider_name = "fake-provider"
    public_parameters = {"network": False, "single_sample": True}

    def __init__(self, outputs: list[str | Exception], *, model: str = "fake-model") -> None:
        self.outputs = list(outputs)
        self.model = model
        self.calls = 0

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> runner.ProviderResult:
        self.calls += 1
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return runner.ProviderResult(
            value,
            response_id=f"fake-{self.calls}",
            usage={"input_tokens": 10, "output_tokens": 4, "reasoning_tokens": 1},
            reported_model=self.model,
            finish_reason="stop",
            created_at=123,
            system_fingerprint="fp-test",
        )


def passing_judgment(record: dict[str, Any]) -> str:
    return json.dumps(
        {
            "case_id": record["case_id"],
            "criteria": [
                {
                    "criterion": item["criterion"],
                    "passed": True,
                    "reason": "Target 原文给出了该判据对应的具体、可核对内容",
                }
                for item in record["criteria"]
            ],
        },
        ensure_ascii=False,
    )


class ModelEvalProviderArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cases, criteria = runner.load_definitions()
        cls.core = runner.prepare_cases(
            [next(case for case in cases if case["suite"] == "core")], criteria
        )[0]
        cls.stress = runner.prepare_cases(
            [next(case for case in cases if case["suite"] == "stress")], criteria
        )[0]

    @staticmethod
    def run_dir(root: Path, name: str) -> Path:
        return root / "v1.6.0" / runner.API_RUNTIME_PROFILE / name

    def test_openai_responses_protocol_records_safe_response_evidence(self) -> None:
        captured: list[Any] = []

        def opener(request: Any, timeout: float) -> HTTPResponse:
            captured.append((request, timeout))
            return HTTPResponse(
                {
                    "id": "resp-1",
                    "status": "completed",
                    "model": "gpt-test-requested",
                    "output_text": "ok",
                    "created_at": 123,
                    "system_fingerprint": "fp-1",
                    "service_tier": "default",
                    "usage": {
                        "input_tokens": 7,
                        "output_tokens": 3,
                        "output_tokens_details": {"reasoning_tokens": 2},
                    },
                }
            )

        provider = runner.OpenAIResponsesProvider(
            api_key="local-test-key",
            model="gpt-test-requested",
            urlopen=opener,
        )
        result = provider.generate(
            instructions="system",
            input_text="input",
            response_schema={"type": "object"},
        )
        request_payload = json.loads(captured[0][0].data)
        self.assertEqual(captured[0][0].full_url, "https://api.openai.com/v1/responses")
        self.assertFalse(request_payload["store"])
        self.assertTrue(request_payload["text"]["format"]["strict"])
        self.assertEqual(result.reported_model, "gpt-test-requested")
        self.assertEqual(result.response_id, "resp-1")
        self.assertEqual(result.usage["reasoning_tokens"], 2)
        self.assertEqual(provider.configuration_manifest()["provenance_type"], "verified_direct")

    def test_openai_compatible_chat_protocol_has_independent_shape(self) -> None:
        captured: list[Any] = []

        def opener(request: Any, timeout: float) -> HTTPResponse:
            captured.append(request)
            return HTTPResponse(
                {
                    "id": "chat-1",
                    "model": "relay-requested",
                    "created": 456,
                    "choices": [
                        {"message": {"content": "chat ok"}, "finish_reason": "stop"}
                    ],
                    "usage": {
                        "prompt_tokens": 8,
                        "completion_tokens": 5,
                        "completion_tokens_details": {"reasoning_tokens": 1},
                    },
                }
            )

        provider = runner.OpenAICompatibleChatProvider(
            api_key="local-test-key",
            model="relay-requested",
            base_url="https://relay.example/proxy/v1",
            declared_upstream_vendor="Vendor X",
            urlopen=opener,
        )
        result = provider.generate(
            instructions="system",
            input_text="input",
            response_schema={"type": "object"},
        )
        payload = json.loads(captured[0].data)
        self.assertEqual(captured[0].full_url, "https://relay.example/proxy/v1/chat/completions")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        self.assertEqual(result.text, "chat ok")
        self.assertEqual(result.usage["input_tokens"], 8)

    def test_chat_token_parameter_is_capability_driven_and_exclusive(self) -> None:
        base_capabilities = {
            "reasoning_effort_supported": False,
            "allowed_reasoning_efforts": [],
            "structured_output_modes": [],
            "temperature_supported": False,
            "top_p_supported": False,
            "seed_supported": False,
        }
        for parameter, excluded in (
            ("max_tokens", "max_completion_tokens"),
            ("max_completion_tokens", "max_tokens"),
        ):
            provider = runner.OpenAICompatibleChatProvider(
                api_key="key",
                model="model",
                base_url="https://relay.example/v1",
                structured_output_required=False,
                capabilities=base_capabilities
                | {"max_output_tokens_parameter": parameter},
                max_output_tokens=777,
            )
            payload = provider.build_request_payload(
                instructions="system", input_text="input"
            )
            self.assertEqual(payload[parameter], 777)
            self.assertNotIn(excluded, payload)
        with self.assertRaisesRegex(
            runner.ModelEvalError, "invalid max_output_tokens_parameter"
        ):
            runner.OpenAICompatibleChatProvider(
                api_key="key",
                model="model",
                base_url="https://relay.example/v1",
                structured_output_required=False,
                capabilities=base_capabilities
                | {"max_output_tokens_parameter": "unknown_tokens"},
            )

    def test_profile_settings_resolve_cli_then_profile_then_role_defaults(self) -> None:
        capabilities = {
            "reasoning_effort_supported": False,
            "allowed_reasoning_efforts": [],
            "structured_output_modes": ["json_object"],
            "temperature_supported": False,
            "top_p_supported": False,
            "seed_supported": False,
            "max_output_tokens_parameter": "max_tokens",
        }
        profile = {
            "profiles": {
                "configured": {
                    "provider": "openai_compatible_chat",
                    "api_key_env": "TEST_PROVIDER_KEY",
                    "base_url": "https://relay.example/v1",
                    "model": "model-a",
                    "capabilities": capabilities,
                    "timeout_seconds": 12,
                    "max_retries": 4,
                    "max_output_tokens": 7777,
                    "judge": {"structured_output_mode": "json_object"},
                },
                "defaults": {
                    "provider": "openai_compatible_chat",
                    "api_key_env": "TEST_PROVIDER_KEY",
                    "base_url": "https://relay.example/v1",
                    "model": "model-a",
                    "capabilities": capabilities,
                    "judge": {"structured_output_mode": "json_object"},
                },
            }
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = Path(temp_dir) / "profiles.json"
            profiles.write_text(json.dumps(profile), encoding="utf-8")

            def make_args(name: str, role: str, *extra: str) -> Any:
                return runner.build_parser().parse_args(
                    [
                        "provider-check",
                        "--role",
                        role,
                        "--profile",
                        name,
                        "--profiles-file",
                        str(profiles),
                        *extra,
                    ]
                )

            with mock.patch.dict(os.environ, {"TEST_PROVIDER_KEY": "key"}):
                configured = runner.create_provider(
                    make_args("configured", "target"), role="target"
                )
                self.assertEqual(configured.timeout_seconds, 12)
                self.assertEqual(configured.max_retries, 4)
                self.assertEqual(configured.max_output_tokens, 7777)
                effective = configured.configuration_manifest()["parameters"]
                self.assertEqual(effective["timeout_seconds"], 12)
                self.assertEqual(effective["max_retries"], 4)
                self.assertEqual(effective["max_output_tokens"], 7777)

                overridden = runner.create_provider(
                    make_args(
                        "configured",
                        "target",
                        "--timeout-seconds",
                        "30",
                        "--max-retries",
                        "2",
                        "--max-output-tokens",
                        "888",
                    ),
                    role="target",
                )
                self.assertEqual(overridden.timeout_seconds, 30)
                self.assertEqual(overridden.max_retries, 2)
                self.assertEqual(overridden.max_output_tokens, 888)

                target_default = runner.create_provider(
                    make_args("defaults", "target"), role="target"
                )
                judge_default = runner.create_provider(
                    make_args("defaults", "judge"), role="judge"
                )
                self.assertEqual(target_default.max_output_tokens, 1200)
                self.assertEqual(judge_default.max_output_tokens, 2400)
                self.assertEqual(target_default.timeout_seconds, 90.0)
                self.assertEqual(judge_default.max_retries, 1)
                self.assertFalse(target_default.structured_output_required)
                self.assertTrue(judge_default.structured_output_required)

    def test_api_key_and_endpoint_sources_follow_explicit_precedence(self) -> None:
        capabilities = {
            "reasoning_effort_supported": False,
            "allowed_reasoning_efforts": [],
            "structured_output_modes": [],
            "temperature_supported": False,
            "top_p_supported": False,
            "seed_supported": False,
            "max_output_tokens_parameter": "max_tokens",
        }
        common = {
            "provider": "openai_compatible_chat",
            "model": "model-a",
            "capabilities": capabilities,
        }
        profile = {
            "profiles": {
                "static": common
                | {
                    "api_key_env": "PROFILE_KEY",
                    "base_url": "https://profile.example/v1",
                },
                "profile-env": common
                | {
                    "api_key_env": "PROFILE_KEY",
                    "base_url_env": "PROFILE_URL",
                },
                "fallback": common,
            }
        }
        environment = {
            "PROFILE_KEY": "profile-value",
            "CLI_KEY": "cli-value",
            "CLI_URL": "https://cli-env.example/v1",
            "PROFILE_URL": "https://profile-env.example/v1",
            "OPENAI_API_KEY": "fallback-value",
            "OPENAI_BASE_URL": "https://global.example/v1",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = Path(temp_dir) / "profiles.json"
            profiles.write_text(json.dumps(profile), encoding="utf-8")

            def make_args(name: str, *extra: str) -> Any:
                return runner.build_parser().parse_args(
                    [
                        "provider-check",
                        "--role",
                        "target",
                        "--profile",
                        name,
                        "--profiles-file",
                        str(profiles),
                        *extra,
                    ]
                )

            with mock.patch.dict(os.environ, environment, clear=True):
                cli_key = runner.create_provider(
                    make_args("static", "--api-key-env", "CLI_KEY"), role="target"
                )
                self.assertEqual(cli_key._api_key, "cli-value")

                cli_env = runner.create_provider(
                    make_args("static", "--base-url-env", "CLI_URL"), role="target"
                )
                self.assertEqual(cli_env.endpoint_origin, "https://cli-env.example")
                self.assertEqual(cli_env.endpoint_source, "cli:--base-url-env:CLI_URL")

                profile_static = runner.create_provider(
                    make_args("static"), role="target"
                )
                self.assertEqual(
                    profile_static.endpoint_origin, "https://profile.example"
                )
                self.assertEqual(profile_static.endpoint_source, "profile:base_url")

                cli_static = runner.create_provider(
                    make_args(
                        "static", "--base-url", "https://cli.example/v1"
                    ),
                    role="target",
                )
                self.assertEqual(cli_static.endpoint_origin, "https://cli.example")

                profile_env = runner.create_provider(
                    make_args("profile-env"), role="target"
                )
                self.assertEqual(
                    profile_env.endpoint_origin, "https://profile-env.example"
                )
                self.assertEqual(
                    profile_env.endpoint_source,
                    "profile:base_url_env:PROFILE_URL",
                )

                fallback = runner.create_provider(
                    make_args("fallback"), role="target"
                )
                self.assertEqual(fallback._api_key, "fallback-value")
                self.assertEqual(fallback.endpoint_origin, "https://global.example")
                self.assertEqual(
                    fallback.endpoint_source, "built-in-env:OPENAI_BASE_URL"
                )

                with mock.patch("sys.stdout", new=io.StringIO()) as captured:
                    self.assertEqual(
                        runner.command_provider_check(make_args("static")), 0
                    )
                    plan = json.loads(captured.getvalue())
                self.assertEqual(plan["endpoint_origin"], "https://profile.example")
                self.assertRegex(plan["endpoint_hash"], r"^sha256:[0-9a-f]{64}$")
                self.assertEqual(plan["requested_model"], "model-a")
                self.assertEqual(plan["provenance_type"], "unverified_relay")
                self.assertEqual(plan["timeout_seconds"], 90.0)
                self.assertEqual(plan["max_retries"], 1)
                self.assertEqual(plan["max_output_tokens"], 1200)
                self.assertIsNone(plan["reasoning_effort"])
                self.assertIsNone(plan["structured_output_mode"])
                self.assertFalse(plan["structured_output_required"])
                self.assertNotIn("profile-value", captured.getvalue())

            with mock.patch.dict(
                os.environ,
                {"PROFILE_KEY": "profile-value"},
                clear=True,
            ):
                with self.assertRaisesRegex(
                    runner.ModelEvalError, "CLI_URL.*not set"
                ):
                    runner.create_provider(
                        make_args("static", "--base-url-env", "CLI_URL"),
                        role="target",
                    )

    def test_target_and_judge_endpoints_are_role_isolated_from_global_env(self) -> None:
        profile = {
            "profiles": {
                "isolated": {
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

            def make_args(role: str) -> Any:
                return runner.build_parser().parse_args(
                    [
                        "provider-check",
                        "--role",
                        role,
                        "--profile",
                        "isolated",
                        "--profiles-file",
                        str(profiles),
                    ]
                )

            with mock.patch.dict(
                os.environ,
                {
                    "TARGET_KEY": "target-value",
                    "JUDGE_KEY": "judge-value",
                    "OPENAI_BASE_URL": "https://global-third.example/v1",
                },
                clear=True,
            ):
                target = runner.create_provider(make_args("target"), role="target")
                judge = runner.create_provider(make_args("judge"), role="judge")
            self.assertEqual(target.endpoint_origin, "https://relay-a.example")
            self.assertEqual(judge.endpoint_origin, "https://relay-b.example")
            self.assertNotEqual(target.endpoint_hash, judge.endpoint_hash)
            self.assertIsNone(target.structured_output_mode)
            self.assertEqual(judge.structured_output_mode, "json_object")

    def test_target_does_not_require_judge_structured_output_capability(self) -> None:
        capabilities = {
            "reasoning_effort_supported": False,
            "allowed_reasoning_efforts": [],
            "structured_output_modes": [],
            "temperature_supported": False,
            "top_p_supported": False,
            "seed_supported": False,
            "max_output_tokens_parameter": "max_tokens",
        }
        target = runner.OpenAICompatibleChatProvider(
            api_key="key",
            model="model",
            base_url="https://relay.example/v1",
            structured_output_required=False,
            capabilities=capabilities,
        )
        payload = target.build_request_payload(instructions="system", input_text="input")
        self.assertNotIn("response_format", payload)
        with self.assertRaisesRegex(runner.ModelEvalError, "not declared supported"):
            runner.OpenAICompatibleChatProvider(
                api_key="key",
                model="model",
                base_url="https://relay.example/v1",
                structured_output_required=True,
                capabilities=capabilities,
            )

    def test_endpoint_identity_redacts_path_query_and_secret(self) -> None:
        provider = runner.OpenAICompatibleChatProvider(
            api_key="not-persisted",
            model="alias",
            base_url="https://relay.example/private/v1?api_key=url-secret",
            declared_upstream_vendor="Vendor X",
        )
        metadata = runner.provider_metadata(provider)
        serialized = json.dumps(metadata)
        self.assertEqual(metadata["endpoint_origin"], "https://relay.example")
        self.assertNotIn("private", serialized)
        self.assertNotIn("url-secret", serialized)
        self.assertRegex(metadata["endpoint_hash"], r"^sha256:[0-9a-f]{64}$")
        other = runner.OpenAICompatibleChatProvider(
            api_key="not-persisted",
            model="alias",
            base_url="https://other-relay.example/v1",
        )
        self.assertNotEqual(
            metadata["endpoint_hash"], runner.provider_metadata(other)["endpoint_hash"]
        )
        self.assertEqual(
            runner.forbidden_secret_key({"url": "https://x.test/v1?access_token=secret"}),
            "url_query_secret",
        )

    def test_target_and_judge_can_use_different_endpoints(self) -> None:
        target = runner.provider_metadata(
            runner.OpenAIResponsesProvider(api_key="key", model="target-model")
        )
        judge = runner.provider_metadata(
            runner.OpenAICompatibleChatProvider(
                api_key="key",
                model="judge-model",
                base_url="https://api.moonshot.cn/v1",
                declared_upstream_vendor="Moonshot AI",
                provenance_type="verified_direct",
            )
        )
        self.assertNotEqual(target["endpoint_origin"], judge["endpoint_origin"])
        self.assertNotEqual(target["endpoint_hash"], judge["endpoint_hash"])
        self.assertNotEqual(target["provider_config_hash"], judge["provider_config_hash"])

    def test_reported_model_can_be_missing_without_being_fabricated(self) -> None:
        provider = runner.OpenAIResponsesProvider(
            api_key="key",
            model="requested",
            urlopen=lambda *_args, **_kwargs: HTTPResponse(
                {"id": "r", "status": "completed", "output_text": "ok"}
            ),
        )
        result = provider.generate(instructions="s", input_text="i")
        self.assertIsNone(result.reported_model)
        self.assertIsNone(result.finish_reason)
        self.assertEqual(
            result.usage,
            {
                "input_tokens": None,
                "output_tokens": None,
                "reasoning_tokens": None,
                "cached_tokens": None,
            },
        )

    def test_relay_alias_never_becomes_verified_direct(self) -> None:
        relay = runner.OpenAICompatibleChatProvider(
            api_key="key",
            model="gpt-5",
            base_url="https://relay.example/v1",
            declared_upstream_vendor="OpenAI",
        )
        self.assertEqual(relay.provenance_type, "declared_relay")
        self.assertFalse(
            runner.provider_metadata(relay)["provider_identity"]["endpoint_verified"]
        )
        with self.assertRaisesRegex(runner.ModelEvalError, "official provider"):
            runner.OpenAICompatibleChatProvider(
                api_key="key",
                model="gpt-5",
                base_url="https://relay.example/v1",
                declared_upstream_vendor="OpenAI",
                provenance_type="verified_direct",
            )
        moonshot = runner.OpenAICompatibleChatProvider(
            api_key="key",
            model="kimi-test",
            base_url="https://api.moonshot.cn/v1",
            declared_upstream_vendor="Moonshot AI",
            provenance_type="verified_direct",
        )
        self.assertTrue(
            runner.provider_metadata(moonshot)["provider_identity"]["endpoint_verified"]
        )

    def test_capability_mismatches_fail_before_network(self) -> None:
        with self.assertRaisesRegex(runner.ModelEvalError, "reasoning_effort"):
            runner.OpenAICompatibleChatProvider(
                api_key="key",
                model="m",
                base_url="https://relay.example/v1",
                reasoning_effort="high",
            )
        with self.assertRaisesRegex(runner.ModelEvalError, "not declared supported"):
            runner.OpenAICompatibleChatProvider(
                api_key="key",
                model="m",
                base_url="https://relay.example/v1",
                structured_output_mode="strict_json_schema",
                capabilities={
                    "reasoning_effort_supported": False,
                    "allowed_reasoning_efforts": [],
                    "structured_output_modes": ["json_object"],
                },
            )

    def test_error_classification_and_retryability_are_explicit(self) -> None:
        expectations = {
            401: ("AUTH_ERROR", False),
            429: ("RATE_LIMIT", True),
            408: ("TIMEOUT", True),
            400: ("UNSUPPORTED_PARAMETER", False),
            503: ("PROVIDER_5XX", True),
            501: ("PROVIDER_5XX", False),
        }
        for status, expected in expectations.items():
            body = b"unsupported parameter" if status == 400 else b""
            self.assertEqual(runner.classify_http_error(status, body), expected)

    def test_retry_uses_one_two_four_second_backoff(self) -> None:
        events: list[Any] = [
            urllib.error.HTTPError("u", 429, "rate", None, io.BytesIO(b"{}")),
            TimeoutError("slow"),
            urllib.error.HTTPError("u", 503, "down", None, io.BytesIO(b"{}")),
            HTTPResponse({"status": "completed", "output_text": "ok"}),
        ]
        sleeps: list[float] = []

        def opener(*_args: Any, **_kwargs: Any) -> Any:
            event = events.pop(0)
            if isinstance(event, Exception):
                raise event
            return event

        provider = runner.OpenAIResponsesProvider(
            api_key="key",
            model="m",
            max_retries=4,
            urlopen=opener,
            sleep=sleeps.append,
        )
        self.assertEqual(provider.generate(instructions="s", input_text="i").text, "ok")
        self.assertEqual(sleeps, [1.0, 2.0, 4.0])

    def test_auth_error_is_not_retried(self) -> None:
        calls = 0

        def opener(*_args: Any, **_kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise urllib.error.HTTPError("u", 401, "auth", None, io.BytesIO(b"{}"))

        provider = runner.OpenAIResponsesProvider(
            api_key="key", model="m", max_retries=4, urlopen=opener, sleep=lambda _: None
        )
        with self.assertRaises(runner.ProviderError) as captured:
            provider.generate(instructions="s", input_text="i")
        self.assertEqual(captured.exception.code, "AUTH_ERROR")
        self.assertFalse(captured.exception.retryable)
        self.assertEqual(calls, 1)

    def test_strict_judge_json_rejects_identity_coverage_and_extra_fields(self) -> None:
        criteria = [
            {"criterion": "c1", "question": "q1"},
            {"criterion": "c2", "question": "q2"},
        ]
        valid_items = [
            {"criterion": "c1", "passed": True, "reason": "Target 原文证据一"},
            {"criterion": "c2", "passed": False, "reason": "Target 原文证据二"},
        ]
        invalid = [
            ({"case_id": "other", "criteria": valid_items}, "case_id"),
            ({"case_id": "case", "criteria": valid_items[:1]}, "every required"),
            (
                {
                    "case_id": "case",
                    "criteria": valid_items
                    + [{"criterion": "unknown", "passed": True, "reason": "Target 原文证据"}],
                },
                "every required",
            ),
            (
                {
                    "case_id": "case",
                    "criteria": [dict(valid_items[0]) | {"extra": 1}, valid_items[1]],
                },
                "extra fields",
            ),
            ({"case_id": "case", "criteria": valid_items, "extra": 1}, "top-level"),
        ]
        for payload, pattern in invalid:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(runner.ModelEvalError, pattern):
                    runner.parse_judgment(
                        json.dumps(payload), criteria, expected_case_id="case"
                    )

    def test_suite_metadata_flows_to_records_and_summary(self) -> None:
        prepared = [self.core, self.stress]
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "suite-flow")
            runner.execute_run(
                prepared,
                RecordingProvider(["core answer", "stress answer"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            runner.execute_judge(
                run_dir,
                RecordingProvider([passing_judgment(item) for item in prepared]),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            summary = runner.build_report(run_dir)
            self.assertEqual(set(summary["suites"]), {"core", "stress"})
            self.assertEqual(summary["suites"]["core"]["total_cases"], 1)
            self.assertEqual(summary["suites"]["stress"]["passed_cases"], 1)
            self.assertEqual(summary["suites"]["core"]["status"], "PASS")
            self.assertEqual(summary["suites"]["stress"]["status"], "PASS")
            self.assertEqual(summary["usage"]["target"]["reasoning_tokens"], 2)
            self.assertIn("Target provenance", (run_dir / "summary.md").read_text(encoding="utf-8"))
            for filename in ("prepared.jsonl", "responses.jsonl", "judgments.jsonl"):
                records = runner.load_jsonl(run_dir / filename)
                self.assertTrue(all(record.get("suite") in {"core", "stress"} for record in records))
            response = runner.load_jsonl(run_dir / "responses.jsonl")[0]
            self.assertEqual(response["request_id"], "fake-1")
            self.assertEqual(response["reported_model"], "fake-model")
            self.assertEqual(response["finish_reason"], "stop")

    def test_judge_resume_retries_retryable_and_skips_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "resume")
            runner.execute_run(
                [self.core],
                RecordingProvider(["answer"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            first = RecordingProvider(
                [runner.ProviderError("network", code="NETWORK_ERROR", retryable=True)]
            )
            self.assertEqual(
                runner.execute_judge(
                    run_dir, first, case_ids=runner.planned_judge_case_ids(run_dir)
                )["judge_error"],
                1,
            )
            second = RecordingProvider([passing_judgment(self.core)])
            counts = runner.execute_judge(
                run_dir,
                second,
                resume=True,
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            self.assertEqual(counts, {"judged": 1, "judge_error": 0, "not_judged": 0})
            attempts = runner.load_jsonl(run_dir / "judgments.jsonl")
            self.assertEqual([item["attempt"] for item in attempts], [1, 2])
            third = RecordingProvider([])
            runner.execute_judge(
                run_dir,
                third,
                resume=True,
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            self.assertEqual(third.calls, 0)

    def test_judge_resume_rejects_configuration_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "resume-mismatch")
            runner.execute_run(
                [self.core],
                RecordingProvider(["answer"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            runner.execute_judge(
                run_dir,
                RecordingProvider(
                    [runner.ProviderError("network", code="NETWORK_ERROR", retryable=True)]
                ),
                case_ids=runner.planned_judge_case_ids(run_dir),
            )
            with self.assertRaisesRegex(runner.ModelEvalError, "configuration mismatch"):
                runner.execute_judge(
                    run_dir,
                    RecordingProvider([passing_judgment(self.core)], model="other-model"),
                    resume=True,
                    case_ids=runner.planned_judge_case_ids(run_dir),
                )

    def test_provider_error_is_classified_without_silent_failover(self) -> None:
        provider = RecordingProvider(
            [runner.ProviderError("bad key", code="AUTH_ERROR", retryable=False)]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "no-failover")
            runner.execute_run(
                [self.core],
                provider,
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            record = runner.load_jsonl(run_dir / "responses.jsonl")[0]
            self.assertEqual(record["provider"], "fake-provider")
            self.assertEqual(record["error_code"], "AUTH_ERROR")
            self.assertFalse(record["retryable"])
            self.assertEqual(provider.calls, 1)

    def test_provider_manifest_is_hash_bound_and_tamper_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = self.run_dir(Path(temp_dir), "manifest")
            runner.execute_run(
                [self.core],
                RecordingProvider(["answer"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            metadata = runner.load_json_object(run_dir / "run.json")
            metadata["target"]["parameters"]["single_sample"] = False
            metadata["provider_manifest"]["target"] = metadata["target"]
            runner.write_json(run_dir / "run.json", metadata)
            with self.assertRaisesRegex(runner.ModelEvalError, "provider_config_hash mismatch"):
                runner.validate_result_artifacts(run_dir)

    def test_reference_qualification_is_separate_and_never_auto_baseline(self) -> None:
        metadata = {
            "git_dirty": False,
            "target": {"provenance_type": "verified_direct"},
            "judge": {"provenance_type": "verified_direct"},
            "reference_acceptance": {"accepted": False},
        }
        self.assertEqual(
            runner.reference_qualification(metadata, "COMPLETED"),
            "REFERENCE_PROVISIONAL",
        )
        metadata["reference_acceptance"]["accepted"] = True
        self.assertEqual(
            runner.reference_qualification(metadata, "COMPLETED"),
            "REFERENCE_ELIGIBLE",
        )
        metadata["git_dirty"] = True
        self.assertEqual(
            runner.reference_qualification(metadata, "COMPLETED"),
            "REFERENCE_NOT_ELIGIBLE",
        )

    def test_comparability_distinguishes_target_and_judge_differences(self) -> None:
        base = {
            "bundle_hash": "bundle",
            "rubric_hash": "rubric",
            "runtime_profile": "api_canonical",
            "suites": {"core": {"total_cases": 1, "total_criteria": 2}},
            "provider_manifest": {
                "target": {
                    "provider": "p",
                    "requested_model": "m1",
                    "endpoint_hash": "e",
                    "reasoning_effort": "medium",
                },
                "judge": {
                    "provider": "j",
                    "requested_model": "jm",
                    "endpoint_hash": "je",
                    "reasoning_effort": "medium",
                },
            },
        }
        same = json.loads(json.dumps(base))
        self.assertEqual(runner.assess_comparability(base, same)["level"], "COMPARABLE")
        same["provider_manifest"]["target"]["requested_model"] = "m2"
        self.assertEqual(
            runner.assess_comparability(base, same)["level"], "PARTIALLY_COMPARABLE"
        )
        same["provider_manifest"]["judge"]["requested_model"] = "other-judge"
        self.assertEqual(
            runner.assess_comparability(base, same)["level"], "PARTIALLY_COMPARABLE"
        )
        same["rubric_hash"] = "other-rubric"
        self.assertEqual(
            runner.assess_comparability(base, same)["level"], "NOT_COMPARABLE"
        )

    def test_target_structured_mode_is_not_a_comparability_dimension(self) -> None:
        base = {
            "runtime_profile": runner.API_RUNTIME_PROFILE,
            "provider_manifest": {
                "target": {
                    "provider": "p",
                    "requested_model": "m",
                    "structured_output_mode": "strict_json_schema",
                },
                "judge": {
                    "provider": "j",
                    "requested_model": "jm",
                    "structured_output_mode": "strict_json_schema",
                },
            },
        }
        target_only = json.loads(json.dumps(base))
        target_only["provider_manifest"]["target"][
            "structured_output_mode"
        ] = "json_object"
        self.assertEqual(
            runner.assess_comparability(base, target_only)["level"], "COMPARABLE"
        )
        judge_change = json.loads(json.dumps(base))
        judge_change["provider_manifest"]["judge"][
            "structured_output_mode"
        ] = "json_object"
        self.assertEqual(
            runner.assess_comparability(base, judge_change)["level"],
            "PARTIALLY_COMPARABLE",
        )

    def test_comparability_includes_actual_model_identity_evidence(self) -> None:
        base = {
            "runtime_profile": runner.API_RUNTIME_PROFILE,
            "provider_manifest": {
                "target": {"requested_model": "model-alias"},
                "judge": {"requested_model": "judge-model"},
            },
            "provider_provenance": {
                "target": {
                    "provenance_type": "declared_relay",
                    "provider_identity": {
                        "vendor": "Relay",
                        "transport": "openai_compatible_chat",
                        "endpoint_origin": "https://relay.example",
                        "endpoint_verified": False,
                    },
                    "model_identity": {
                        "requested_model": "model-alias",
                        "reported_models": ["model-a"],
                        "status": "MATCHED",
                    },
                },
                "judge": {
                    "provenance_type": "verified_direct",
                    "provider_identity": {},
                    "model_identity": {
                        "requested_model": "judge-model",
                        "reported_models": ["judge-model"],
                        "status": "MATCHED",
                    },
                },
            },
        }
        self.assertEqual(
            runner.assess_comparability(base, json.loads(json.dumps(base)))["level"],
            "COMPARABLE",
        )

        missing = json.loads(json.dumps(base))
        missing["provider_provenance"]["target"]["model_identity"].update(
            {"reported_models": [], "status": "MISSING"}
        )
        result = runner.assess_comparability(base, missing)
        self.assertEqual(result["level"], "PARTIALLY_COMPARABLE")
        self.assertIn("model_identity.status", result["differences"]["target"])
        self.assertIn(
            {
                "field": "target.model_identity.status",
                "first": "MATCHED",
                "second": "MISSING",
            },
            result["difference_details"]["target"],
        )

        mismatch = json.loads(json.dumps(base))
        mismatch["provider_provenance"]["target"]["model_identity"].update(
            {"reported_models": ["model-b"], "status": "MISMATCH"}
        )
        self.assertEqual(
            runner.assess_comparability(base, mismatch)["level"],
            "PARTIALLY_COMPARABLE",
        )
        different_reported = json.loads(json.dumps(base))
        different_reported["provider_provenance"]["target"]["model_identity"][
            "reported_models"
        ] = ["model-b"]
        self.assertIn(
            "model_identity.reported_models",
            runner.assess_comparability(base, different_reported)["differences"][
                "target"
            ],
        )
        relay_change = json.loads(json.dumps(base))
        relay_change["provider_provenance"]["target"][
            "provenance_type"
        ] = "unverified_relay"
        self.assertIn(
            "provider_provenance.provenance_type",
            runner.assess_comparability(base, relay_change)["differences"]["target"],
        )

    def test_usage_and_optional_cost_do_not_require_hardcoded_prices(self) -> None:
        usage = {"input_tokens": 1_000_000, "output_tokens": 500_000}
        self.assertIsNone(runner.estimated_phase_cost(usage, {"pricing": {}}))
        cost = runner.estimated_phase_cost(
            usage,
            {
                "pricing": {
                    "input_per_million_tokens": 2.0,
                    "output_per_million_tokens": 4.0,
                }
            },
        )
        self.assertEqual(cost, 4.0)

    def test_single_case_debug_stays_in_work_and_preserves_formal_artifacts(self) -> None:
        work_root = ROOT / ".work"
        work_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory(
            dir=work_root
        ) as debug_dir:
            run_dir = self.run_dir(Path(temp_dir), "debug")
            runner.execute_run(
                [self.core],
                RecordingProvider(["answer"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            before = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in run_dir.iterdir()
                if path.is_file()
            }
            output = Path(debug_dir) / "judge.json"
            record = runner.execute_judge_case_debug(
                run_dir,
                RecordingProvider([passing_judgment(self.core)]),
                self.core["case_id"],
                output,
            )
            after = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in run_dir.iterdir()
                if path.is_file()
            }
            self.assertEqual(after, before)
            self.assertTrue(record["debug_only"])
            self.assertFalse(record["formal_result_modified"])

    def test_judge_case_uses_successful_append_only_target_attempt(self) -> None:
        work_root = ROOT / ".work"
        work_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory() as temp_dir, tempfile.TemporaryDirectory(
            dir=work_root
        ) as debug_dir:
            run_dir = self.run_dir(Path(temp_dir), "debug-retry")
            runner.execute_run(
                [self.core],
                RecordingProvider(
                    [runner.ProviderError("network", code="NETWORK_ERROR", retryable=True)]
                ),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            runner.execute_run(
                [self.core],
                RecordingProvider(["successful retry response"]),
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
                resume=True,
            )
            output = Path(debug_dir) / "judge-retry.json"
            record = runner.execute_judge_case_debug(
                run_dir,
                RecordingProvider([passing_judgment(self.core)]),
                self.core["case_id"],
                output,
            )
            self.assertTrue(record["debug_only"])
            self.assertTrue(output.is_file())

    def test_profile_example_uses_env_names_and_target_judge_are_independent(self) -> None:
        example = runner.load_json_yaml(runner.PROVIDER_PROFILE_EXAMPLE)
        serialized = json.dumps(example)
        self.assertNotIn("sk-", serialized)
        target = runner.load_provider_profile(
            runner.PROVIDER_PROFILE_EXAMPLE, "openai-direct", "target"
        )
        judge = runner.load_provider_profile(
            runner.PROVIDER_PROFILE_EXAMPLE, "openai-direct", "judge"
        )
        self.assertEqual(target["model_env"], "OPENAI_MODEL")
        self.assertEqual(judge["model_env"], "OPENAI_JUDGE_MODEL")
        self.assertIn(
            "model_evals/provider_profiles.local.yaml",
            (ROOT / ".gitignore").read_text(encoding="utf-8"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            unsafe = Path(temp_dir) / "profiles.yaml"
            unsafe.write_text(
                json.dumps(
                    {"profiles": {"bad": {"api_key": "must-not-be-here"}}}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(runner.ModelEvalError, "forbidden secret"):
                runner.load_provider_profile(unsafe, "bad", "target")

    def test_strict_model_identity_mismatch_is_classified(self) -> None:
        provider = runner.OpenAIResponsesProvider(
            api_key="key",
            model="requested",
            strict_model_identity=True,
            urlopen=lambda *_args, **_kwargs: HTTPResponse(
                {
                    "id": "r",
                    "status": "completed",
                    "model": "reported-other",
                    "output_text": "ok",
                }
            ),
        )
        with self.assertRaises(runner.ProviderError) as captured:
            provider.generate(instructions="s", input_text="i")
        self.assertEqual(captured.exception.code, "MODEL_IDENTITY_MISMATCH")


if __name__ == "__main__":
    unittest.main()
