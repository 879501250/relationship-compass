"""V1.3C execution adapter tests: Registry identity never serializes tokens."""

from __future__ import annotations

from pathlib import Path
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
)


ROOT = Path(__file__).resolve().parents[2]


class RegistryRuntimeResolverTests(unittest.TestCase):
    def test_env_credential_builds_provider_without_exposing_token(self) -> None:
        resolver = RegistryRuntimeResolver(
            ModelRegistry(), LocalFileCredentialSecretStore(Path(tempfile.gettempdir()) / "unused-registry-secret.json"),
            environ={"MOONSHOT_API_KEY": "token-for-test-only"},
        )
        binding = resolver.resolve_preset("kimi-official")
        provider = RegistryProviderFactory.create(binding, role="judge")
        self.assertEqual(provider.protocol, "openai_compatible_chat")
        self.assertEqual(provider.model, "kimi-k2.6")
        self.assertEqual(provider.max_output_tokens_parameter, "max_completion_tokens")
        self.assertNotIn("token-for-test-only", repr(binding))
        self.assertNotIn("token-for-test-only", str(binding.snapshot))

    def test_missing_env_credential_fails_before_provider_creation(self) -> None:
        resolver = RegistryRuntimeResolver(
            ModelRegistry(), LocalFileCredentialSecretStore(Path(tempfile.gettempdir()) / "unused-registry-secret.json"), environ={}
        )
        with self.assertRaisesRegex(RegistryRuntimeError, "MOONSHOT_API_KEY"):
            resolver.resolve_preset("kimi-official")

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
        binding = ResolvedRegistryRuntime(runtime, "token", "environment", {}, "hash")
        with self.assertRaisesRegex(RegistryRuntimeError, "尚不支持"):
            RegistryProviderFactory.create(binding, role="target")
