"""V1.3C execution adapter tests: Registry identity never serializes tokens."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from eval_console.credential_store import LocalFileCredentialSecretStore
from eval_console.model_registry import ModelRegistry
from eval_console.registry_runtime import (
    RegistryProviderFactory,
    RegistryRuntimeError,
    RegistryRuntimeResolver,
    ResolvedRegistryRuntime,
    runtime_audit_snapshot,
    runtime_identity_snapshot,
    sanitize_url,
)


ROOT = Path(__file__).resolve().parents[2]


class RegistryRuntimeResolverTests(unittest.TestCase):
    def test_env_credential_builds_provider_without_exposing_token(self) -> None:
        resolver = RegistryRuntimeResolver(
            ModelRegistry(), LocalFileCredentialSecretStore(Path(tempfile.gettempdir()) / "unused-registry-secret.json"),
            environ={"MOONSHOT_API_KEY": "token-for-test-only"},
        )
        binding = resolver.resolve_preset("kimi-official", context="judge")
        provider = RegistryProviderFactory.create(binding, role="judge")
        self.assertEqual(provider.protocol, "openai_compatible_chat")
        self.assertEqual(provider.model, "kimi-k2.6")
        self.assertEqual(provider.max_output_tokens_parameter, "max_completion_tokens")
        self.assertNotIn("token-for-test-only", repr(binding))
        self.assertNotIn("token-for-test-only", str(binding.snapshot))

    def test_runtime_context_keeps_preset_role_neutral_and_selects_output_mode(self) -> None:
        resolver = RegistryRuntimeResolver(
            ModelRegistry(), LocalFileCredentialSecretStore(Path(tempfile.gettempdir()) / "unused-registry-secret.json"),
            environ={"MOONSHOT_API_KEY": "token-for-test-only"},
        )
        target = resolver.resolve_preset("kimi-official", context="target")
        judge = resolver.resolve_preset("kimi-official", context="judge")
        self.assertNotIn("role", resolver.registry.presets["kimi-official"].__dict__)
        self.assertNotIn("structured_output", target.runtime.semantic_parameters)
        self.assertEqual(judge.runtime.semantic_parameters["structured_output"], "json_object")

    def test_runtime_context_is_required_and_missing_env_fails_before_provider_creation(self) -> None:
        resolver = RegistryRuntimeResolver(
            ModelRegistry(), LocalFileCredentialSecretStore(Path(tempfile.gettempdir()) / "unused-registry-secret.json"), environ={}
        )
        with self.assertRaises(TypeError):
            resolver.resolve_preset("kimi-official")
        with self.assertRaisesRegex(RegistryRuntimeError, "MOONSHOT_API_KEY"):
            resolver.resolve_preset("kimi-official", context="target")

    def test_judge_text_json_fallback_is_explicitly_warned(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "registry"
            shutil.copytree(ROOT / "model_registry", root)
            family_path = root / "model_families" / "kimi.yaml"
            family = json.loads(family_path.read_text(encoding="utf-8"))
            family["defaults"]["capabilities"]["structured_output"]["modes"] = ["text_json_fallback"]
            family_path.write_text(json.dumps(family), encoding="utf-8")
            for preset_path in (root / "presets").glob("*.yaml"):
                preset = json.loads(preset_path.read_text(encoding="utf-8"))
                preset.setdefault("parameters", {})["structured_output"] = "text_json_fallback"
                preset_path.write_text(json.dumps(preset), encoding="utf-8")
            resolver = RegistryRuntimeResolver(
                ModelRegistry(root),
                LocalFileCredentialSecretStore(Path(raw) / "credentials.json"),
                environ={"MOONSHOT_API_KEY": "token-for-test-only"},
            )
            binding = resolver.resolve_preset("kimi-official", context="judge")
            readiness = resolver.assess_preset_readiness("kimi-official", context="judge")
        self.assertEqual(binding.runtime.semantic_parameters["structured_output"], "text_json_fallback")
        self.assertTrue(binding.context_warnings)
        self.assertTrue(readiness.runnable)
        self.assertTrue(readiness.warnings)

    def test_local_credential_is_resolved_from_store(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalFileCredentialSecretStore(root / "credentials.json")
            store.set("moonshot-main", "local-token")
            registry = ModelRegistry()
            credential = registry.credentials["moonshot-main"]
            local_credential = mock.Mock(
                id=credential.id, environment_variable=None,
                secret_reference="local:moonshot-main", expires_at=None,
            )
            resolver = RegistryRuntimeResolver(registry, store, environ={})
            token, source = resolver._resolve_token(local_credential)
            self.assertEqual((token, source), ("local-token", "local"))

    def test_unsupported_protocol_fails_closed(self) -> None:
        runtime = mock.Mock(protocol="anthropic_messages", preset_id="unsupported")
        binding = ResolvedRegistryRuntime(runtime, "token", "environment", {}, {}, "hash")
        with self.assertRaisesRegex(RegistryRuntimeError, "尚不支持"):
            RegistryProviderFactory.create(binding, role="target")

    def test_identity_covers_capabilities_but_not_credential_source(self) -> None:
        registry = ModelRegistry()
        runtime = registry.resolve(preset_id="kimi-official")
        identity = runtime_identity_snapshot(runtime)
        audit = runtime_audit_snapshot(runtime, credential_source="environment")
        self.assertIn("resolved_capabilities", identity)
        self.assertNotIn("credential_source", identity)
        self.assertEqual(audit["credential_source"], "environment")
        modified = dict(identity)
        modified["resolved_capabilities"] = {"thinking": {"supported": False}}
        self.assertNotEqual(str(identity), str(modified))

    def test_url_sanitization_removes_credentials_query_and_fragment(self) -> None:
        self.assertEqual(
            sanitize_url("https://user:pass@example.test/v1?api_key=secret#part"),
            "https://example.test/v1",
        )
