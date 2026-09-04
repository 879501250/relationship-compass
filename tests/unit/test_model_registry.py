from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from eval_console.model_registry import ModelRegistry, ModelRegistryError


ROOT = Path(__file__).resolve().parents[2]


class ModelRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ModelRegistry(ROOT / "model_registry")

    def test_base_url_is_selected_by_model_family_not_exact_model_version(self) -> None:
        resolved = self.registry.resolve(
            vendor_id="moonshot",
            credential_id="moonshot-main",
            model_id="kimi-k3",
            base_url_id="official-global",
        )

        self.assertEqual(resolved.model_family_id, "kimi")
        self.assertEqual(resolved.base_url_id, "official-global")
        self.assertEqual(resolved.base_url, "https://api.moonshot.ai/v1")

    def test_model_defaults_are_inherited_and_model_fields_override_them(self) -> None:
        resolved = self.registry.resolve(
            vendor_id="moonshot",
            credential_id="moonshot-main",
            model_id="kimi-k3",
        )

        self.assertEqual(resolved.runtime_parameters, {})
        self.assertTrue(resolved.capabilities["thinking"])
        self.assertTrue(resolved.capabilities["structured_output"])
        self.assertEqual(resolved.model_configuration["context_window"], 256000)
        family = self.registry.model_families["kimi"]
        self.assertEqual(family.defaults["context_window"], 131072)
        self.assertEqual(family.models["kimi-k3"].configuration["context_window"], 256000)

    def test_vendor_api_name_and_capability_restriction_override_model(self) -> None:
        resolved = self.registry.resolve(preset_id="openrouter-kimi")

        self.assertEqual(resolved.api_name, "moonshot/kimi-k2.6")
        self.assertFalse(resolved.capabilities["thinking"])
        self.assertTrue(resolved.capabilities["structured_output"])

    def test_multiple_credentials_are_scoped_and_run_record_contains_no_secret(self) -> None:
        moonshot_credentials = [
            credential
            for credential in self.registry.credentials.values()
            if credential.vendor_id == "moonshot"
        ]
        self.assertEqual({item.id for item in moonshot_credentials}, {"moonshot-main", "moonshot-sandbox"})
        resolved = self.registry.resolve(preset_id="kimi-official")
        record = resolved.run_record("judge")

        self.assertEqual(record["credential_id"], "moonshot-main")
        self.assertEqual(record["role"], "judge")
        self.assertNotIn("api_key", record)
        self.assertNotIn("env", record)
        self.assertNotIn("MOONSHOT_API_KEY", json.dumps(record))

    def test_preset_loads_and_runtime_parameters_override_preset_values(self) -> None:
        resolved = self.registry.resolve(
            preset_id="kimi-official",
            runtime_parameters={"temperature": 0.6, "response_format": {"type": "json"}},
        )

        self.assertEqual(resolved.preset_id, "kimi-official")
        self.assertEqual(resolved.runtime_parameters["temperature"], 0.6)
        self.assertEqual(resolved.runtime_parameters["max_tokens"], 4096)
        self.assertEqual(resolved.runtime_parameters["response_format"]["type"], "json")

    def test_capability_merge_applies_vendor_and_base_url_restrictions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_registry(
                root,
                "vendors/vendor.yaml",
                {
                    "id": "vendor",
                    "name": "Vendor",
                    "protocol": "test",
                    "capabilities": {"structured_output": False},
                    "base_urls": [
                        {
                            "id": "primary",
                            "url": "https://vendor.example/v1",
                            "model_families": ["family"],
                            "capabilities": {"thinking": False},
                        }
                    ],
                },
            )
            self._write_registry(
                root,
                "model_families/family.yaml",
                {
                    "id": "family",
                    "name": "Family",
                    "defaults": {
                        "capabilities": {
                            "thinking": True,
                            "structured_output": True,
                            "vision": True,
                        }
                    },
                    "models": {"model-v1": {"api_name": "family/model-v1"}},
                },
            )
            self._write_registry(
                root,
                "credentials/credential.yaml",
                {"id": "credential", "vendor": "vendor", "source": "runtime"},
            )
            self._write_registry(
                root,
                "presets/preset.yaml",
                {
                    "id": "preset",
                    "vendor": "vendor",
                    "credential": "credential",
                    "model": "model-v1",
                },
            )

            resolved = ModelRegistry(root).resolve(preset_id="preset")

        self.assertFalse(resolved.capabilities["thinking"])
        self.assertFalse(resolved.capabilities["structured_output"])
        self.assertTrue(resolved.capabilities["vision"])

    def test_credential_secret_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self._write_registry(
                root,
                "vendors/vendor.yaml",
                {
                    "id": "vendor",
                    "name": "Vendor",
                    "protocol": "test",
                    "base_urls": [
                        {"id": "primary", "url": "https://vendor.example", "model_families": ["family"]}
                    ],
                },
            )
            self._write_registry(
                root,
                "model_families/family.yaml",
                {"id": "family", "name": "Family", "models": {"model": {}}},
            )
            self._write_registry(
                root,
                "credentials/credential.yaml",
                {
                    "id": "credential",
                    "vendor": "vendor",
                    "source": "env",
                    "env": "VENDOR_API_KEY",
                    "api_key": "must-not-be-accepted",
                },
            )
            self._write_registry(
                root,
                "presets/preset.yaml",
                {
                    "id": "preset",
                    "vendor": "vendor",
                    "credential": "credential",
                    "model": "model",
                },
            )

            with self.assertRaisesRegex(ModelRegistryError, "must not be persisted"):
                ModelRegistry(root)

    @staticmethod
    def _write_registry(root: Path, relative_path: str, data: dict[str, object]) -> None:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
