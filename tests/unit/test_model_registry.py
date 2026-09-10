from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from contextlib import contextmanager

from eval_console.model_registry import ModelRegistry, RegistryResolutionError, RegistryValidationError


ROOT = Path(__file__).resolve().parents[2]


class ModelRegistryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ModelRegistry(ROOT / "model_registry")

    def test_kimi_canonical_semantic_and_wire_contract(self) -> None:
        runtime = self.registry.resolve(preset_id="kimi-official")
        self.assertEqual(runtime.semantic_parameters["thinking"], "disabled")
        self.assertEqual(runtime.semantic_parameters["max_output_tokens"], 4096)
        self.assertEqual(runtime.wire_parameters["max_completion_tokens"], 4096)
        self.assertNotIn("max_tokens", runtime.semantic_parameters)
        self.assertFalse(runtime.resolved_capabilities["temperature"]["supported"])
        self.assertEqual(runtime.transport_defaults["max_retries"], 2)

    def test_default_api_name_and_model_inheritance(self) -> None:
        runtime = self.registry.resolve(vendor_id="moonshot", credential_id="moonshot-main", model_family_id="kimi", model_id="kimi-k3")
        self.assertEqual(runtime.api_model_name, "kimi-k3")
        self.assertEqual(runtime.context_window, 256000)
        self.assertEqual(runtime.resolved_capabilities["thinking"]["allowed_values"], ("enabled", "disabled"))

    def test_base_url_default_and_explicit_selection(self) -> None:
        default_runtime = self.registry.resolve(vendor_id="moonshot", credential_id="moonshot-main", model_family_id="kimi", model_id="kimi-k2.6")
        explicit_runtime = self.registry.resolve(vendor_id="moonshot", credential_id="moonshot-main", model_family_id="kimi", model_id="kimi-k2.6", base_url_id="official-global")
        self.assertEqual(default_runtime.base_url_id, "official-cn")
        self.assertEqual(default_runtime.provenance["base_url_selection"], "family_default")
        self.assertEqual(explicit_runtime.base_url_id, "official-global")

    def test_preset_default_model_can_be_overridden_within_its_family(self) -> None:
        runtime = self.registry.resolve(preset_id="kimi-official", model_id="kimi-k3")
        self.assertEqual(runtime.model_id, "kimi-k3")
        self.assertEqual(self.registry.presets["kimi-official"].default_model_id, "kimi-k2.6")

    def test_supported_modes_is_normalized_to_runtime_modes(self) -> None:
        with self._temporary_registry() as root:
            family_path = root / "model_families/family.yaml"
            family = json.loads(family_path.read_text(encoding="utf-8"))
            family["defaults"]["capabilities"]["structured_output"] = {
                "supported": True, "supported_modes": ["json_object"]
            }
            family_path.write_text(json.dumps(family), encoding="utf-8")
            runtime = ModelRegistry(root).resolve(preset_id="preset", semantic_parameters={"structured_output": "json_object"})
        self.assertEqual(runtime.resolved_capabilities["structured_output"]["modes"], ("json_object",))

    def test_base_url_ambiguity_never_uses_document_order(self) -> None:
        with self._temporary_registry() as root:
            vendor = self._vendor(urls=[self._url("one"), self._url("two")])
            self._write(root, "vendors/vendor.yaml", vendor)
            with self.assertRaisesRegex(RegistryResolutionError, "AMBIGUOUS_BASE_URL"):
                ModelRegistry(root).resolve(vendor_id="vendor", credential_id="office-relay-key", model_family_id="family", model_id="model-v1")

    def test_duplicate_default_and_invalid_protocol_are_validation_errors(self) -> None:
        with self._temporary_registry() as root:
            self._write(root, "vendors/vendor.yaml", self._vendor(urls=[self._url("one", default=True), self._url("two", default=True)]))
            with self.assertRaisesRegex(RegistryValidationError, "multiple default"):
                ModelRegistry(root)
        with self._temporary_registry() as root:
            self._write(root, "vendors/vendor.yaml", self._vendor(protocol=None, urls=[self._url("one", protocol=None)]))
            with self.assertRaisesRegex(RegistryValidationError, "protocol is required"):
                ModelRegistry(root)
        with self._temporary_registry() as root:
            self._write(root, "vendors/vendor.yaml", self._vendor(protocol="unsupported", urls=[self._url("one")]))
            with self.assertRaisesRegex(RegistryValidationError, "unsupported protocol"):
                ModelRegistry(root)

    def test_base_url_protocol_and_transport_override_vendor(self) -> None:
        with self._temporary_registry() as root:
            self._write(root, "vendors/vendor.yaml", self._vendor(urls=[self._url("one", protocol="anthropic_messages", transport={"max_retries": 4})], transport={"timeout_seconds": 90, "max_retries": 2}))
            runtime = ModelRegistry(root).resolve(preset_id="preset")
        self.assertEqual(runtime.protocol, "anthropic_messages")
        self.assertEqual(runtime.transport_defaults, {"timeout_seconds": 90, "max_retries": 4})

    def test_base_url_model_override_capability_and_mapping(self) -> None:
        with self._temporary_registry() as root:
            url = self._url("one", overrides={"model-v1": {"api_name": "vendor/model-v1", "capabilities": {"thinking": {"supported": False}}, "parameter_mapping": {"max_output_tokens": "max_completion_tokens"}}})
            self._write(root, "vendors/vendor.yaml", self._vendor(urls=[url]))
            runtime = ModelRegistry(root).resolve(preset_id="preset", semantic_parameters={"max_output_tokens": 12})
        self.assertEqual(runtime.api_model_name, "vendor/model-v1")
        self.assertFalse(runtime.resolved_capabilities["thinking"]["supported"])
        self.assertEqual(runtime.wire_parameters["max_completion_tokens"], 12)

    def test_unknown_unsupported_and_invalid_value_parameters_fail(self) -> None:
        with self.assertRaisesRegex(RegistryResolutionError, "unknown semantic"):
            self.registry.resolve(preset_id="kimi-official", semantic_parameters={"foo_bar": 1})
        with self.assertRaisesRegex(RegistryResolutionError, "temperature.*unsupported"):
            self.registry.resolve(preset_id="kimi-official", semantic_parameters={"temperature": 0.2})
        with self.assertRaisesRegex(RegistryResolutionError, "thinking.*not allowed"):
            self.registry.resolve(preset_id="kimi-official", semantic_parameters={"thinking": "auto"})

    def test_credential_security_and_role_neutral_preset(self) -> None:
        self.assertEqual({item.id for item in self.registry.credentials.values() if item.vendor_id == "moonshot"}, {"moonshot-main", "moonshot-sandbox"})
        record = self.registry.resolve(preset_id="kimi-official").run_record("judge")
        self.assertEqual(record["credential_id"], "moonshot-main")
        self.assertNotIn("env", record)
        self.assertNotIn("MOONSHOT_API_KEY", json.dumps(record))
        self.assertNotIn("role", self.registry.presets["kimi-official"].__dict__)

    def test_credential_and_preset_integrity_errors(self) -> None:
        with self._temporary_registry() as root:
            self._write(root, "credentials/credential.yaml", {"id": "office-relay-key", "vendor": "missing", "env": "KEY"})
            with self.assertRaisesRegex(RegistryValidationError, "unknown vendor 'missing'"):
                ModelRegistry(root)
        with self._temporary_registry() as root:
            self._write(root, "vendors/other.yaml", {**self._vendor(), "id": "other"})
            self._write(root, "presets/preset.yaml", self._preset(vendor="other"))
            with self.assertRaisesRegex(RegistryValidationError, "credential.*belongs to vendor"):
                ModelRegistry(root)

    def test_all_registry_documents_reject_secret_fields(self) -> None:
        for directory, relative, mutation in (
            ("vendors", "vendor.yaml", lambda data: data.update({"api_key": "x"})),
            ("model_families", "family.yaml", lambda data: data["defaults"].update({"token": "x"})),
            ("credentials", "credential.yaml", lambda data: data.update({"authorization": "x"})),
            ("presets", "preset.yaml", lambda data: data["parameters"].update({"secret": "x"})),
        ):
            with self.subTest(directory=directory), self._temporary_registry() as root:
                path = root / directory / relative
                data = json.loads(path.read_text(encoding="utf-8"))
                mutation(data); path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaisesRegex(RegistryValidationError, "secret-bearing field"):
                    ModelRegistry(root)

    def test_referential_integrity_and_duplicate_ids(self) -> None:
        with self._temporary_registry() as root:
            self._write(root, "vendors/vendor.yaml", self._vendor(urls=[{**self._url("one"), "model_families": ["missing"]}]))
            with self.assertRaisesRegex(RegistryValidationError, "unknown model family"):
                ModelRegistry(root)
        with self._temporary_registry() as root:
            self._write(root, "vendors/duplicate.yaml", self._vendor())
            with self.assertRaisesRegex(RegistryValidationError, "duplicate vendors id"):
                ModelRegistry(root)
        with self._temporary_registry() as root:
            self._write(root, "presets/preset.yaml", {**self._preset(), "base_url": "missing"})
            with self.assertRaisesRegex(RegistryValidationError, "has no base URL"):
                ModelRegistry(root)
        with self._temporary_registry() as root:
            self._write(root, "presets/preset.yaml", {**self._preset(), "credential": "missing"})
            with self.assertRaisesRegex(RegistryValidationError, "unknown credential"):
                ModelRegistry(root)

    def test_resolution_rejects_unknown_model_and_model_family_mismatch(self) -> None:
        with self.assertRaisesRegex(RegistryResolutionError, "unknown model"):
            self.registry.resolve(vendor_id="moonshot", credential_id="moonshot-main", model_family_id="kimi", model_id="missing")
        with self.assertRaisesRegex(RegistryResolutionError, "unknown model"):
            self.registry.resolve(vendor_id="moonshot", credential_id="moonshot-main", model_family_id="gpt", model_id="kimi-k2.6")

    def test_definitions_are_immutable(self) -> None:
        with self.assertRaises(TypeError):
            self.registry.vendors["moonshot"] = self.registry.vendors["moonshot"]

    @contextmanager
    def _temporary_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, "vendors/vendor.yaml", self._vendor())
            self._write(root, "model_families/family.yaml", {"id": "family", "name": "Family", "defaults": {"capabilities": {"thinking": {"supported": True, "allowed_values": ["enabled", "disabled"]}, "max_output_tokens": {"supported": True}}}, "models": {"model-v1": {}}})
            self._write(root, "credentials/credential.yaml", {"id": "office-relay-key", "vendor": "vendor", "env": "VENDOR_API_KEY"})
            self._write(root, "presets/preset.yaml", self._preset())
            yield root

    @staticmethod
    def _vendor(*, protocol="openai_compatible_chat", urls=None, transport=None):
        return {"id": "vendor", "name": "Vendor", **({"protocol": protocol} if protocol else {}), "transport_defaults": transport or {}, "base_urls": urls or [ModelRegistryContractTests._url("one")]}

    @staticmethod
    def _url(identifier, *, default=False, protocol=None, transport=None, overrides=None):
        result = {"id": identifier, "url": f"https://{identifier}.example/v1", "model_families": ["family"], "transport_defaults": transport or {}, "model_overrides": overrides or {}}
        if default: result["default_for"] = ["family"]
        if protocol: result["protocol"] = protocol
        return result

    @staticmethod
    def _preset(*, vendor="vendor"):
        return {"id": "preset", "vendor": vendor, "base_url": "one", "credential": "office-relay-key", "model_family": "family", "model": "model-v1", "parameters": {}}

    @staticmethod
    def _write(root: Path, relative: str, data: dict) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
# Modified by AI on 2026-09-10 15:20:16
