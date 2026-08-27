"""Offline official-origin, profile, and historical-provenance regressions."""

from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import run_model_evals as runner  # noqa: E402


GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


class ProviderProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cases, criteria = runner.load_definitions()
        cls.prepared = runner.prepare_cases(cases[:1], criteria)

    def setUp(self) -> None:
        network = mock.patch(
            "urllib.request.urlopen", side_effect=AssertionError("unexpected network call")
        )
        self.network = network.start()
        self.addCleanup(network.stop)

    @staticmethod
    def chat_provider(**overrides: Any) -> runner.OpenAICompatibleChatProvider:
        config = {
            "api_key": "test-only-key",
            "model": "model-a",
            "base_url": GOOGLE_BASE_URL,
            "declared_upstream_vendor": "Google",
            "structured_output_required": False,
        }
        return runner.OpenAICompatibleChatProvider(**(config | overrides))

    @staticmethod
    def chat_response(model: str, text: str = "answer") -> mock.Mock:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {
                "id": "mock-chat-response",
                "model": model,
                "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            }
        ).encode("utf-8")
        return mock.Mock(return_value=response)

    def assert_provenance(self, provider: Any, expected: str) -> dict[str, Any]:
        manifest = provider.configuration_manifest()
        runner.validate_provider_manifest(manifest, "test")
        self.assertEqual(manifest["provenance_type"], expected)
        self.assertIs(
            manifest["provider_identity"]["endpoint_verified"],
            expected == "verified_direct",
        )
        return manifest

    def test_registry_contains_only_supported_exact_official_tuples(self) -> None:
        self.assertEqual(
            runner.VERIFIED_PROVIDER_ORIGINS,
            {
                ("openai_responses", "OpenAI"): {"https://api.openai.com"},
                ("openai_compatible_chat", "Moonshot AI"): {
                    "https://api.moonshot.cn", "https://api.moonshot.ai"
                },
                ("openai_compatible_chat", "Google"): {
                    "https://generativelanguage.googleapis.com"
                },
                ("openai_compatible_chat", "DeepSeek"): {"https://api.deepseek.com"},
            },
        )

    def test_google_official_origin_is_normalized_independently_of_path(self) -> None:
        for url in (
            "https://generativelanguage.googleapis.com",
            GOOGLE_BASE_URL,
            "HTTPS://GENERATIVELANGUAGE.GOOGLEAPIS.COM/v1beta/openai",
        ):
            with self.subTest(url=url):
                manifest = self.assert_provenance(
                    self.chat_provider(base_url=url), "verified_direct"
                )
                self.assertEqual(
                    manifest["endpoint_origin"],
                    "https://generativelanguage.googleapis.com",
                )

    def test_google_nonofficial_origins_never_verify_even_when_forced(self) -> None:
        for url in (
            "https://relay.example.com/v1",
            "http://generativelanguage.googleapis.com/v1beta/openai/",
            "https://generativelanguage.googleapis.com.fake-domain.com/v1",
            "https://fake-generativelanguage.googleapis.com/v1",
            "https://proxy.generativelanguage.googleapis.com/v1",
            "https://generativelanguage.googleapis.com:8443/v1",
            "https://generativelanguage.googleapis.com:443/v1",
            "https://relay.example.com/generativelanguage.googleapis.com",
        ):
            with self.subTest(url=url):
                self.assert_provenance(self.chat_provider(base_url=url), "declared_relay")
                with self.assertRaisesRegex(runner.ModelEvalError, "official provider"):
                    self.chat_provider(base_url=url, provenance_type="verified_direct")

    def test_google_wrong_or_missing_canonical_vendor_does_not_verify(self) -> None:
        for vendor in (None, "google", "Google ", "Gemini", "OpenAI", "Moonshot AI"):
            with self.subTest(vendor=vendor):
                self.assert_provenance(
                    self.chat_provider(declared_upstream_vendor=vendor),
                    "declared_relay" if vendor else "unverified_relay",
                )
                with self.assertRaisesRegex(runner.ModelEvalError, "official provider"):
                    self.chat_provider(
                        declared_upstream_vendor=vendor, provenance_type="verified_direct"
                    )

    def test_google_wrong_transport_does_not_verify(self) -> None:
        config = {
            "api_key": "test-only-key",
            "model": "model-a",
            "base_url": GOOGLE_BASE_URL,
            "declared_upstream_vendor": "Google",
        }
        self.assert_provenance(runner.OpenAIResponsesProvider(**config), "declared_relay")
        with self.assertRaisesRegex(runner.ModelEvalError, "official provider"):
            runner.OpenAIResponsesProvider(**config, provenance_type="verified_direct")

    def test_deepseek_official_and_relay_remain_distinct(self) -> None:
        for url, expected in (
            ("https://api.deepseek.com", "verified_direct"),
            ("https://api.deepseek.com/v1/", "verified_direct"),
            ("https://relay.example.com/v1", "declared_relay"),
            ("http://api.deepseek.com/v1", "declared_relay"),
            ("https://api.deepseek.com.fake-domain.com/v1", "declared_relay"),
        ):
            with self.subTest(url=url):
                self.assert_provenance(
                    self.chat_provider(base_url=url, declared_upstream_vendor="DeepSeek"),
                    expected,
                )

    def test_moonshot_official_and_relay_support_is_preserved(self) -> None:
        for url, expected in (
            ("https://api.moonshot.cn", "verified_direct"),
            ("https://api.moonshot.cn/v1", "verified_direct"),
            ("https://api.moonshot.ai", "verified_direct"),
            ("https://api.moonshot.ai/v1", "verified_direct"),
            ("HTTPS://API.MOONSHOT.AI/v1/", "verified_direct"),
            ("https://relay.example.com/v1", "declared_relay"),
            ("http://api.moonshot.ai/v1", "declared_relay"),
            ("https://api.moonshot.ai.example.com/v1", "declared_relay"),
            ("https://proxy.api.moonshot.ai/v1", "declared_relay"),
            ("https://moonshot-relay.example.com/v1", "declared_relay"),
            ("https://api.moonshot.cn.example.com/v1", "declared_relay"),
        ):
            with self.subTest(url=url):
                manifest = self.assert_provenance(
                    self.chat_provider(base_url=url, declared_upstream_vendor="Moonshot AI"),
                    expected,
                )
                self.assertEqual(manifest["endpoint_origin"], url.lower().split("/v1")[0])
                if expected != "verified_direct":
                    with self.assertRaisesRegex(runner.ModelEvalError, "official provider"):
                        self.chat_provider(
                            base_url=url, declared_upstream_vendor="Moonshot AI",
                            provenance_type="verified_direct",
                        )

    def test_moonshot_global_requires_canonical_vendor_and_matching_transport(self) -> None:
        for provider_class, vendor in (
            (runner.OpenAICompatibleChatProvider, "Google"),
            (runner.OpenAICompatibleChatProvider, "Kimi"),
            (runner.OpenAICompatibleChatProvider, "Moonshot"),
            (runner.OpenAICompatibleChatProvider, "MoonshotAI"),
            (runner.OpenAICompatibleChatProvider, "Moonshot Global"),
            (runner.OpenAIResponsesProvider, "Moonshot AI"),
        ):
            with self.subTest(provider=provider_class.provider_name, vendor=vendor):
                config = {
                    "api_key": "test-only-key",
                    "model": "kimi-k3",
                    "base_url": "https://api.moonshot.ai/v1",
                    "declared_upstream_vendor": vendor,
                }
                self.assert_provenance(provider_class(**config), "declared_relay")
                with self.assertRaisesRegex(runner.ModelEvalError, "official provider"):
                    provider_class(**config, provenance_type="verified_direct")

    def test_moonshot_retry_and_resume_never_switch_official_regions(self) -> None:
        for region, other_region in (("cn", "ai"), ("ai", "cn")):
            with self.subTest(region=region), tempfile.TemporaryDirectory() as temp_dir:
                opener = mock.Mock(side_effect=TimeoutError("offline timeout fixture"))
                sleep = mock.Mock()
                config = {
                    "declared_upstream_vendor": "Moonshot AI",
                    "model": "kimi-k3",
                    "max_retries": 1,
                    "urlopen": opener,
                    "sleep": sleep,
                }
                provider = self.chat_provider(
                    base_url=f"https://api.moonshot.{region}/v1", **config
                )
                run_dir = Path(temp_dir) / "v1.6.0" / runner.API_RUNTIME_PROFILE / "region"
                runner.execute_run(
                    self.prepared, provider, run_dir,
                    repository_sha="a" * 40, repository_dirty=False,
                )
                metadata = runner.execute_run(self.prepared, provider, run_dir, resume=True)
                self.assertEqual(opener.call_count, 4)
                self.assertEqual(
                    {call.args[0].full_url for call in opener.call_args_list},
                    {f"https://api.moonshot.{region}/v1/chat/completions"},
                )
                self.assertEqual(sleep.call_args_list, [mock.call(1.0), mock.call(1.0)])
                records = runner.load_jsonl(run_dir / "responses.jsonl")
                self.assertEqual(len(records), 2)
                self.assertTrue(all(record["error_code"] == "TIMEOUT" for record in records))
                self.assertTrue(all(record["retryable"] for record in records))
                self.assertEqual(
                    metadata["target"]["endpoint_origin"], f"https://api.moonshot.{region}"
                )
                other_provider = self.chat_provider(
                    base_url=f"https://api.moonshot.{other_region}/v1", **config
                )
                before = (run_dir / "responses.jsonl").read_bytes()
                with self.assertRaisesRegex(runner.ModelEvalError, "configuration mismatch"):
                    runner.execute_run(self.prepared, other_provider, run_dir, resume=True)
                self.assertEqual((run_dir / "responses.jsonl").read_bytes(), before)
                self.assertEqual(opener.call_count, 4)
                runner.validate_result_artifacts(run_dir)
        self.network.assert_not_called()

    def test_openai_responses_default_inference_and_relay_support_are_preserved(self) -> None:
        for config, expected in (
            ({}, "verified_direct"),
            ({"declared_upstream_vendor": "OpenAI"}, "verified_direct"),
            ({"declared_upstream_vendor": "Google"}, "declared_relay"),
            ({"base_url": "http://api.openai.com/v1"}, "unverified_relay"),
            ({"base_url": "https://relay.example/v1"}, "unverified_relay"),
            (
                {"base_url": "https://relay.example/v1", "declared_upstream_vendor": "OpenAI"},
                "declared_relay",
            ),
        ):
            with self.subTest(config=config):
                self.assert_provenance(
                    runner.OpenAIResponsesProvider(
                        api_key="test-only-key", model="model-a", **config
                    ),
                    expected,
                )
        self.assert_provenance(
            self.chat_provider(
                base_url="https://api.openai.com/v1", declared_upstream_vendor="OpenAI"
            ),
            "declared_relay",
        )

    def test_explicit_conservative_provenance_is_not_upgraded(self) -> None:
        for declared in ("declared_relay", "unverified_relay"):
            with self.subTest(declared=declared):
                self.assert_provenance(self.chat_provider(provenance_type=declared), declared)

    def test_manifest_validation_rejects_forged_official_tuple(self) -> None:
        original = self.chat_provider().configuration_manifest()
        for field, value in (
            ("endpoint_origin", "http://generativelanguage.googleapis.com"),
            ("endpoint_origin", "https://relay.example.com"),
            ("declared_upstream_vendor", "Gemini"),
            ("protocol", "openai_responses"),
            ("provider", "openai_responses"),
        ):
            with self.subTest(field=field):
                manifest = copy.deepcopy(original)
                manifest[field] = value
                manifest["provider_identity"].update(
                    vendor=manifest["declared_upstream_vendor"],
                    transport=manifest["protocol"],
                    endpoint_origin=manifest["endpoint_origin"],
                )
                manifest.pop("provider_config_hash")
                manifest["provider_config_hash"] = runner.sha256_bytes(
                    runner.canonical_json_bytes(manifest)
                )
                with self.assertRaisesRegex(runner.ModelEvalError, "verified provider origin"):
                    runner.validate_provider_manifest(manifest, "forged")

    def test_matched_model_never_upgrades_relay_for_any_vendor(self) -> None:
        for vendor in ("Google", "DeepSeek", "Moonshot AI", "OpenAI", "Anthropic"):
            with self.subTest(vendor=vendor):
                model = "kimi-k3" if vendor == "Moonshot AI" else "model-a"
                provider = self.chat_provider(
                    base_url="https://relay.example.com/v1",
                    declared_upstream_vendor=vendor,
                    model=model,
                    urlopen=self.chat_response(model),
                )
                result = provider.generate(instructions="system", input_text="input")
                manifest = self.assert_provenance(provider, "declared_relay")
                self.assertEqual(
                    runner.model_identity_from_records(
                        manifest, [{"reported_model": result.reported_model}]
                    )["status"],
                    "MATCHED",
                )
        self.network.assert_not_called()

    def test_official_google_model_mismatch_keeps_verified_endpoint_and_fails_run(self) -> None:
        provider = self.chat_provider(urlopen=self.chat_response("different-model"))
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "v1.6.0" / runner.API_RUNTIME_PROFILE / "mismatch"
            runner.execute_run(
                self.prepared,
                provider,
                run_dir,
                repository_sha="a" * 40,
                repository_dirty=False,
            )
            metadata = runner.load_json_object(run_dir / "run.json")
            record = runner.load_jsonl(run_dir / "responses.jsonl")[0]
            self.assertEqual(record["error_code"], "MODEL_IDENTITY_MISMATCH")
            self.assertFalse(record["retryable"])
            self.assertEqual(record["reported_model"], "different-model")
            self.assertEqual(
                metadata["identities"]["target"]["model_identity"]["status"], "MISMATCH"
            )
            self.assertTrue(metadata["target"]["provider_identity"]["endpoint_verified"])
            self.assertEqual(metadata["target"]["provenance_type"], "verified_direct")
            self.assertEqual(runner.reference_quality(metadata), "Level C")
            self.assertEqual(
                runner.reference_qualification(metadata, metadata["status"]),
                "REFERENCE_NOT_ELIGIBLE",
            )
            runner.validate_result_artifacts(run_dir)

    def test_moonshot_global_model_mismatch_does_not_hide_behind_official_origin(self) -> None:
        provider = self.chat_provider(
            base_url="https://api.moonshot.ai/v1",
            declared_upstream_vendor="Moonshot AI",
            model="model-A",
            urlopen=self.chat_response("model-B"),
        )
        with self.assertRaises(runner.ProviderError) as captured:
            provider.generate(instructions="system", input_text="input")
        self.assertEqual(captured.exception.code, "MODEL_IDENTITY_MISMATCH")
        self.assertFalse(captured.exception.retryable)
        manifest = self.assert_provenance(provider, "verified_direct")
        self.assertEqual(
            runner.model_identity_from_records(manifest, [{
                "reported_model": captured.exception.reported_model,
                "error_code": captured.exception.code,
            }])["status"],
            "MISMATCH",
        )

    @staticmethod
    def profile_args(name: str, role: str, *extra: str) -> Any:
        return runner.build_parser().parse_args(
            [
                "provider-check", "--profiles-file", str(runner.PROVIDER_PROFILE_EXAMPLE),
                "--profile", name, "--role", role, *extra,
            ]
        )

    def test_purpose_profiles_preflight_independently_without_network(self) -> None:
        env = {
            "GEMINI_API_KEY": "test-only-gemini-key",
            "GEMINI_TARGET_MODEL": "gemini-target-test",
            "GEMINI_JUDGE_MODEL": "gemini-judge-test",
            "RELATIONSHIP_EVAL_TARGET_API_KEY": "test-only-relay-key",
            "RELATIONSHIP_EVAL_TARGET_BASE_URL": "https://relay.example.com/v1",
            "RELATIONSHIP_EVAL_TARGET_MODEL": "relay-target-test",
            "MOONSHOT_API_KEY": "test-only-moonshot-key",
            "MOONSHOT_JUDGE_MODEL": "kimi-judge-test",
            "DEEPSEEK_API_KEY": "test-only-deepseek-key",
            "DEEPSEEK_JUDGE_MODEL": "deepseek-judge-test",
            "OPENAI_BASE_URL": "https://unrelated.example.com/v1",
        }
        for name, role, model, provenance, mode, reasoning in (
            (
                "validation-gemini", "target", "gemini-target-test",
                "verified_direct", None, "medium",
            ),
            (
                "validation-gemini", "judge", "gemini-judge-test",
                "verified_direct", "strict_json_schema", "high",
            ),
            (
                "reference-target-relay-openai", "target", "relay-target-test",
                "declared_relay", None, None,
            ),
            (
                "reference-judge-kimi-official", "judge", "kimi-judge-test",
                "verified_direct", "text_json_fallback", None,
            ),
            (
                "reference-judge-deepseek-official", "judge", "deepseek-judge-test",
                "verified_direct", "text_json_fallback", None,
            ),
            (
                "reference-judge-gemini-official", "judge", "gemini-judge-test",
                "verified_direct", "strict_json_schema", "high",
            ),
        ):
            with self.subTest(profile=name, role=role), mock.patch.dict(os.environ, env, clear=True):
                args = self.profile_args(name, role)
                output = io.StringIO()
                with mock.patch("sys.stdout", output):
                    self.assertEqual(args.func(args), 0)
                plan = json.loads(output.getvalue())
                self.assertEqual(plan["status"], "PREFLIGHT_OK")
                self.assertFalse(plan["network_called"])
                self.assertEqual(plan["requested_model"], model)
                self.assertEqual(plan["provenance_type"], provenance)
                self.assertIs(
                    plan["provider_identity"]["endpoint_verified"],
                    provenance == "verified_direct",
                )
                self.assertEqual(plan["structured_output_mode"], mode)
                self.assertIs(plan["structured_output_required"], role == "judge")
                self.assertEqual(plan["reasoning_effort"], reasoning)
                self.assertEqual(plan["max_retries"], 4 if name == "validation-gemini" else 1)
                self.assertEqual(plan["timeout_seconds"], 120 if name == "validation-gemini" else 90)
                self.assertNotIn("test-only-", output.getvalue())
                provider = runner.create_provider(args, role=role)
                self.assert_provenance(provider, provenance)
                payload = provider.build_request_payload(
                    instructions="system",
                    input_text="input",
                    response_schema={"type": "object"} if role == "judge" else None,
                )
                self.assertIn("max_tokens", payload)
                self.assertNotIn("max_completion_tokens", payload)
                if role == "target" or mode == "text_json_fallback":
                    self.assertNotIn("response_format", payload)
                else:
                    self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        self.network.assert_not_called()

    def test_kimi_profile_can_explicitly_select_cn_or_global_endpoint(self) -> None:
        with mock.patch.dict(os.environ, {
            "MOONSHOT_API_KEY": "test-only-key",
            "MOONSHOT_JUDGE_MODEL": "kimi-k3",
            "MOONSHOT_BASE_URL": "https://api.moonshot.ai/v1",
        }, clear=True):
            for options, expected_origin in (
                ([], "https://api.moonshot.cn"),
                (["--base-url", "https://api.moonshot.cn/v1"], "https://api.moonshot.cn"),
                (["--base-url", "https://api.moonshot.ai/v1"], "https://api.moonshot.ai"),
                (["--base-url-env", "MOONSHOT_BASE_URL"], "https://api.moonshot.ai"),
            ):
                with self.subTest(options=options):
                    args = self.profile_args("reference-judge-kimi-official", "judge", *options)
                    output = io.StringIO()
                    with mock.patch("sys.stdout", output):
                        self.assertEqual(args.func(args), 0)
                    plan = json.loads(output.getvalue())
                    self.assertEqual(plan["status"], "PREFLIGHT_OK")
                    self.assertEqual(plan["endpoint_origin"], expected_origin)
                    self.assertEqual(plan["provenance_type"], "verified_direct")
                    self.assertTrue(plan["provider_identity"]["endpoint_verified"])
                    self.assertFalse(plan["network_called"])
        self.network.assert_not_called()

    def test_gemini_judge_preflight_still_requires_declared_structured_mode(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-only-key", "GEMINI_JUDGE_MODEL": "judge-test"},
            clear=True,
        ):
            args = self.profile_args(
                "validation-gemini", "judge", "--structured-output-mode", "json_object"
            )
            with self.assertRaisesRegex(runner.ModelEvalError, "structured output mode"):
                runner.create_provider(args, role="judge")
        self.network.assert_not_called()

    def test_profile_names_cannot_change_identity_or_qualification(self) -> None:
        config = runner.load_provider_profile(
            runner.PROVIDER_PROFILE_EXAMPLE, "validation-gemini", "target"
        )
        manifests = []
        for name in ("validation-gemini", "reference-gemini"):
            with mock.patch.object(
                runner, "load_provider_profile", return_value=config
            ), mock.patch.dict(
                os.environ,
                {"GEMINI_API_KEY": "test-only-key", "GEMINI_TARGET_MODEL": "model-a"},
                clear=True,
            ):
                provider = runner.create_provider(
                    self.profile_args(name, "target"), role="target"
                )
                manifests.append(provider.configuration_manifest())
        self.assertEqual(manifests[0], manifests[1])
        for manifest in manifests:
            metadata = {
                "git_dirty": False,
                "target": manifest,
                "judge": manifest,
                "identities": {
                    role: {"model_identity": {"status": "MATCHED"}}
                    for role in ("target", "judge")
                },
                "execution": {role: "PURE_API" for role in ("target", "judge")},
            }
            self.assertEqual(
                runner.reference_qualification(metadata, "COMPLETED"), "REFERENCE_PROVISIONAL"
            )
            self.assertEqual(
                runner.reference_qualification(metadata, "COMPLETED", {"accepted": True}),
                "REFERENCE_ELIGIBLE",
            )

    def test_registry_expansion_does_not_reinterpret_or_rewrite_historical_google_run(self) -> None:
        legacy_registry = {
            key: value
            for key, value in runner.VERIFIED_PROVIDER_ORIGINS.items()
            if key[1] not in {"Google", "DeepSeek"}
        }
        judgment = json.dumps(
            {
                "case_id": self.prepared[0]["case_id"],
                "criteria": [
                    {
                        "criterion": item["criterion"],
                        "passed": True,
                        "reason": "Target 原文给出了该判据对应的具体、可核对内容",
                    }
                    for item in self.prepared[0]["criteria"]
                ],
            }
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            run_dir = Path(temp_dir) / "v1.6.0" / runner.API_RUNTIME_PROFILE / "historical-google"
            with mock.patch.object(runner, "VERIFIED_PROVIDER_ORIGINS", legacy_registry):
                target = self.chat_provider(urlopen=self.chat_response("model-a"))
                judge = self.chat_provider(
                    model="judge-a",
                    structured_output_required=True,
                    urlopen=self.chat_response("judge-a", judgment),
                )
                runner.execute_run(
                    self.prepared,
                    target,
                    run_dir,
                    repository_sha="a" * 40,
                    repository_dirty=False,
                )
                runner.execute_judge(run_dir, judge)
                old_summary = runner.build_report(run_dir)
                runner.accept_reference(run_dir, notes="offline historical test fixture only")
                old_status = runner.effective_reference_status(run_dir)
            before = {path.name: path.read_bytes() for path in run_dir.iterdir() if path.is_file()}
            runner.validate_result_artifacts(run_dir)
            new_status = runner.effective_reference_status(run_dir)
            self.assertEqual(new_status, old_status)
            self.assertEqual(runner.build_report(run_dir), old_summary)
            self.assertEqual(new_status["reference_quality"], "Level B")
            self.assertEqual(new_status["acceptance_status"], "ACCEPTED")
            self.assertEqual(
                new_status["effective_reference_qualification"], "REFERENCE_PROVISIONAL"
            )
            for role in ("target", "judge"):
                evidence = new_status["provider_provenance"][role]
                self.assertEqual(evidence["provenance_type"], "declared_relay")
                self.assertFalse(evidence["provider_identity"]["endpoint_verified"])
            self.assertEqual(
                before,
                {path.name: path.read_bytes() for path in run_dir.iterdir() if path.is_file()},
            )
        self.assert_provenance(self.chat_provider(), "verified_direct")
        self.network.assert_not_called()

    def test_compare_moonshot_cn_and_global_with_same_alias_is_partially_comparable(self) -> None:
        manifests = [
            self.chat_provider(
                base_url=f"https://api.moonshot.{region}/v1",
                declared_upstream_vendor="Moonshot AI", model="kimi-k3",
            ).configuration_manifest()
            for region in ("cn", "ai")
        ]
        base = {
            "runtime_profile": runner.API_RUNTIME_PROFILE,
            "provider_manifest": {role: manifests[0] for role in ("target", "judge")},
        }
        for role, other in (("target", "judge"), ("judge", "target")):
            with self.subTest(role=role):
                changed = copy.deepcopy(base)
                changed["provider_manifest"][role] = manifests[1]
                result = runner.assess_comparability(base, changed)
                self.assertEqual(result["level"], "PARTIALLY_COMPARABLE")
                self.assertEqual(result["differences"][role], ["endpoint_hash"])
                self.assertEqual(result["differences"][other], [])
                self.assertTrue(all(m["provider_identity"]["endpoint_verified"] for m in manifests))

    def test_compare_official_and_relay_with_same_alias_is_only_partially_comparable(self) -> None:
        official = self.chat_provider().configuration_manifest()
        relay = self.chat_provider(base_url="https://relay.example.com/v1").configuration_manifest()

        def evidence(manifest: dict[str, Any]) -> dict[str, Any]:
            return {
                "provenance_type": manifest["provenance_type"],
                "provider_identity": manifest["provider_identity"],
                "model_identity": runner.model_identity_from_records(
                    manifest, [{"reported_model": "model-a"}]
                ),
            }

        base = {
            "runtime_profile": runner.API_RUNTIME_PROFILE,
            "provider_manifest": {role: official for role in ("target", "judge")},
            "provider_provenance": {role: evidence(official) for role in ("target", "judge")},
        }
        for role, other in (("target", "judge"), ("judge", "target")):
            with self.subTest(role=role):
                changed = copy.deepcopy(base)
                changed["provider_manifest"][role] = relay
                changed["provider_provenance"][role] = evidence(relay)
                result = runner.assess_comparability(base, changed)
                self.assertEqual(result["level"], "PARTIALLY_COMPARABLE")
                self.assertIn("endpoint_hash", result["differences"][role])
                self.assertIn("provider_identity.endpoint_origin", result["differences"][role])
                self.assertIn("provider_provenance.provenance_type", result["differences"][role])
                self.assertNotIn("model_identity.status", result["differences"][role])
                self.assertEqual(result["differences"][other], [])


if __name__ == "__main__":
    unittest.main()
