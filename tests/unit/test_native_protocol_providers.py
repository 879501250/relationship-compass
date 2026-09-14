"""Native Anthropic/Gemini adapters share the secret-safe HTTP transport."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import unittest
import urllib.error
from types import SimpleNamespace
from typing import Any

from eval_console.registry_runtime import RegistryProviderFactory, ResolvedRegistryRuntime


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as runner  # noqa: E402


TEST_TOKEN = "TEST_SECRET_DO_NOT_LEAK_123"


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class NativeProtocolProviderTests(unittest.TestCase):
    def test_anthropic_native_request_response_auth_and_usage(self) -> None:
        captured: list[Any] = []

        def opener(request: Any, timeout: float) -> _Response:
            captured.append(request)
            return _Response({
                "id": "msg-1", "model": "claude-test", "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "first "}, {"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "second"}],
                "usage": {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 3},
            })

        provider = runner.AnthropicMessagesProvider(api_key=TEST_TOKEN, model="claude-test", urlopen=opener)
        result = provider.generate(instructions="system instruction", input_text="user input", response_schema={"type": "object"})
        payload = json.loads(captured[0].data)

        self.assertEqual(captured[0].full_url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(captured[0].get_header("X-api-key"), TEST_TOKEN)
        self.assertEqual(captured[0].get_header("Anthropic-version"), "2023-06-01")
        self.assertNotIn("Authorization", captured[0].headers)
        self.assertEqual(payload["system"], "system instruction")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "user input"}])
        self.assertEqual(payload["max_tokens"], 1200)
        self.assertEqual(payload["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn(TEST_TOKEN, captured[0].full_url)
        self.assertNotIn(TEST_TOKEN, json.dumps(payload))
        self.assertEqual(result.text, "first second")
        self.assertEqual(result.usage, {"input_tokens": 11, "output_tokens": 7, "reasoning_tokens": None, "cached_tokens": 3})
        self.assertEqual(result.finish_reason, "end_turn")
        self.assertNotIn(TEST_TOKEN, repr(result))
        self.assertNotIn(TEST_TOKEN, json.dumps(provider.configuration_manifest()))

    def test_anthropic_bearer_override_and_empty_content(self) -> None:
        captured: list[Any] = []
        provider = runner.AnthropicMessagesProvider(
            api_key=TEST_TOKEN, model="claude-test", auth_style="bearer",
            urlopen=lambda request, timeout: captured.append(request) or _Response({"model": "claude-test", "content": []}),
        )
        with self.assertRaises(runner.ProviderInvalidResponse) as caught:
            provider.generate(instructions="s", input_text="u")
        self.assertEqual(caught.exception.code, "EMPTY_RESPONSE")
        self.assertEqual(captured[0].get_header("Authorization"), f"Bearer {TEST_TOKEN}")
        self.assertNotIn(TEST_TOKEN, str(caught.exception))

    def test_gemini_native_request_response_safe_model_path_and_usage(self) -> None:
        captured: list[Any] = []

        def opener(request: Any, timeout: float) -> _Response:
            captured.append(request)
            return _Response({
                "modelVersion": "gemini/test", "candidates": [{
                    "finishReason": "STOP", "content": {"parts": [{"text": "one "}, {"thought": True}, {"text": "two"}]},
                }],
                "usageMetadata": {"promptTokenCount": 13, "candidatesTokenCount": 8, "thoughtsTokenCount": 2, "cachedContentTokenCount": 4, "totalTokenCount": 27},
            })

        provider = runner.GeminiGenerateProvider(api_key=TEST_TOKEN, model="gemini/test", urlopen=opener)
        result = provider.generate(instructions="system instruction", input_text="user input", response_schema={"type": "object"})
        payload = json.loads(captured[0].data)

        self.assertEqual(captured[0].full_url, "https://generativelanguage.googleapis.com/v1beta/models/gemini%2Ftest:generateContent")
        self.assertEqual(captured[0].get_header("X-goog-api-key"), TEST_TOKEN)
        self.assertNotIn(TEST_TOKEN, captured[0].full_url)
        self.assertNotIn(TEST_TOKEN, json.dumps(payload))
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "system instruction")
        self.assertEqual(payload["contents"][0]["parts"][0]["text"], "user input")
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 1200)
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(payload["generationConfig"]["responseJsonSchema"], {"type": "object"})
        self.assertEqual(result.text, "one two")
        self.assertEqual(result.usage, {"input_tokens": 13, "output_tokens": 8, "reasoning_tokens": 2, "cached_tokens": 4})
        self.assertEqual(result.provider_metadata, {"total_tokens": 27})

    def test_gemini_bearer_override_and_safety_error(self) -> None:
        captured: list[Any] = []
        provider = runner.GeminiGenerateProvider(
            api_key=TEST_TOKEN, model="gemini-test", auth_style="bearer",
            urlopen=lambda request, timeout: captured.append(request) or _Response({"candidates": [{"finishReason": "SAFETY"}]}),
        )
        with self.assertRaises(runner.ProviderInvalidResponse) as caught:
            provider.generate(instructions="s", input_text="u")
        self.assertEqual(caught.exception.code, "CONTENT_FILTER")
        self.assertEqual(captured[0].get_header("Authorization"), f"Bearer {TEST_TOKEN}")
        self.assertNotIn(TEST_TOKEN, str(caught.exception))

    def test_native_adapters_reuse_http_error_classification_and_retry(self) -> None:
        for provider_type, kwargs in (
            (runner.AnthropicMessagesProvider, {"model": "claude-test"}),
            (runner.GeminiGenerateProvider, {"model": "gemini-test"}),
        ):
            with self.subTest(provider=provider_type.__name__):
                attempts = 0

                def opener(_request: Any, timeout: float) -> _Response:
                    nonlocal attempts
                    attempts += 1
                    if attempts == 1:
                        raise urllib.error.HTTPError("https://example.test", 429, "slow", {"Retry-After": "0"}, io.BytesIO(b"{}"))
                    return _Response({"content": [{"type": "text", "text": "ok"}], "model": "claude-test"} if provider_type is runner.AnthropicMessagesProvider else {"modelVersion": "gemini-test", "candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

                provider = provider_type(api_key=TEST_TOKEN, urlopen=opener, sleep=lambda _delay: None, **kwargs)
                result = provider.generate(instructions="s", input_text="u")
                self.assertEqual(result.text, "ok")
                self.assertEqual(result.http_telemetry["rate_limit_count"], 1)

    def test_native_adapters_preserve_shared_auth_and_server_error_codes(self) -> None:
        for status, expected in ((401, "AUTH_ERROR"), (503, "PROVIDER_5XX")):
            with self.subTest(status=status):
                provider = runner.AnthropicMessagesProvider(
                    api_key=TEST_TOKEN, model="claude-test", max_retries=0,
                    urlopen=lambda _request, timeout, code=status: (_ for _ in ()).throw(
                        urllib.error.HTTPError("https://example.test", code, "error", {}, io.BytesIO(b"{}"))
                    ),
                )
                with self.assertRaises(runner.ProviderError) as caught:
                    provider.generate(instructions="s", input_text="u")
                self.assertEqual(caught.exception.code, expected)
                self.assertNotIn(TEST_TOKEN, str(caught.exception))

    def test_factory_supports_all_native_protocols_for_one_relay_vendor(self) -> None:
        expected = {
            "openai_compatible_chat": runner.OpenAICompatibleChatProvider,
            "anthropic_messages": runner.AnthropicMessagesProvider,
            "gemini_generate": runner.GeminiGenerateProvider,
        }
        for protocol, provider_type in expected.items():
            with self.subTest(protocol=protocol):
                provider = RegistryProviderFactory.create(self._binding(protocol), role="target")
                self.assertIsInstance(provider, provider_type)
                self.assertEqual(provider.declared_upstream_vendor, "API Nebula")
        self.assertEqual(
            RegistryProviderFactory.SUPPORTED_PROTOCOLS,
            {"openai_compatible_chat", "openai_responses", "anthropic_messages", "gemini_generate"},
        )

    @staticmethod
    def _binding(protocol: str) -> ResolvedRegistryRuntime:
        max_parameter = "max_tokens" if protocol in {"openai_compatible_chat", "anthropic_messages"} else "max_output_tokens"
        runtime = SimpleNamespace(
            preset_id=f"relay-{protocol}", protocol=protocol, api_model_name="native-model",
            base_url="https://relay.example/v1", vendor_name="API Nebula",
            wire_parameters={}, transport_defaults={"auth_style": "bearer"},
            resolved_capabilities={
                "structured_output": {"supported": False, "modes": []},
                "temperature": {"supported": False}, "top_p": {"supported": False},
                "seed": {"supported": False}, "thinking": {"supported": False},
                "reasoning_effort": {"supported": False, "allowed_values": []},
            },
        )
        return ResolvedRegistryRuntime(runtime, TEST_TOKEN, "local", {}, {}, "hash")
