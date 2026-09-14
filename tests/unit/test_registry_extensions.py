"""Model Family extension and immediate Registry validation contracts."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from eval_console.model_registry import (
    ModelRegistry,
    RegistryValidationError,
    validate_base_url_configuration,
)
from eval_console.registry_cli import (
    _validated_entity_id,
    _validated_model_id,
    _validated_protocol,
    _validated_url,
)
from eval_console.registry_store import RegistryStore


ROOT = Path(__file__).resolve().parents[2]


class ImmediateRegistryValidationTests(unittest.TestCase):
    def test_vendor_and_base_url_ids_are_rejected_with_field_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "(?s)Vendor ID 'Api.Nebula'.*小写字母"):
            _validated_entity_id("Api.Nebula", "Vendor ID")
        with self.assertRaisesRegex(ValueError, "(?s)Base URL ID 'api.nebula'.*小写字母"):
            _validated_entity_id("api.nebula", "Base URL ID")

    def test_registry_identifier_rules_accept_current_valid_forms(self) -> None:
        self.assertEqual(_validated_entity_id("apinebula", "Vendor ID"), "apinebula")
        self.assertEqual(_validated_entity_id("official", "Base URL ID"), "official")
        self.assertEqual(_validated_entity_id("official-cn", "Base URL ID"), "official-cn")
        self.assertEqual(_validated_entity_id("api_v1", "Base URL ID"), "api_v1")
        self.assertEqual(_validated_model_id("kimi-k2.7"), "kimi-k2.7")

    def test_base_url_subobject_validation_rejects_invalid_url_protocol_and_references(self) -> None:
        families = {"kimi": object()}
        with self.assertRaisesRegex(RegistryValidationError, "absolute HTTP\\(S\\) URL"):
            validate_base_url_configuration(
                {"id": "official", "url": "api.example.com", "protocol": "openai_compatible_chat", "model_families": ["kimi"]},
                known_model_families=families,
            )
        with self.assertRaisesRegex(RegistryValidationError, "unsupported protocol"):
            validate_base_url_configuration(
                {"id": "official", "url": "https://api.example.com/v1", "protocol": "unknown", "model_families": ["kimi"]},
                known_model_families=families,
            )
        with self.assertRaisesRegex(RegistryValidationError, "unknown model family"):
            validate_base_url_configuration(
                {"id": "official", "url": "https://api.example.com/v1", "protocol": "openai_compatible_chat", "model_families": ["missing"]},
                known_model_families=families,
            )
        with self.assertRaisesRegex(RegistryValidationError, "duplicate base URL id"):
            validate_base_url_configuration(
                {"id": "official", "url": "https://api.example.com/v1", "protocol": "openai_compatible_chat", "model_families": ["kimi"], "default_for": ["kimi"]},
                known_model_families=families,
                sibling_ids=("official",),
            )
        with self.assertRaisesRegex(RegistryValidationError, "default_for 'other' is unsupported"):
            validate_base_url_configuration(
                {"id": "official", "url": "https://api.example.com/v1", "protocol": "openai_compatible_chat", "model_families": ["kimi"], "default_for": ["other"]},
                known_model_families={"kimi": object(), "other": object()},
            )

    def test_protocol_and_url_helpers_use_registry_contract(self) -> None:
        self.assertEqual(_validated_protocol("openai_compatible_chat", "Base URL Protocol"), "openai_compatible_chat")
        self.assertEqual(_validated_url("https://api.example.com/v1"), "https://api.example.com/v1")
        with self.assertRaisesRegex(ValueError, "不受支持"):
            _validated_protocol("unsupported", "Base URL Protocol")


class ModelFamilyExtensionTests(unittest.TestCase):
    def test_builtin_family_protection_and_effective_extension_merge(self) -> None:
        with self._store() as store:
            with self.assertRaisesRegex(ValueError, "内置定义不可直接修改"):
                store.update("model_families", "kimi", {"id": "kimi"})
            with self.assertRaisesRegex(ValueError, "内置定义不可直接删除"):
                store.delete("model_families", "kimi")
            with self.assertRaisesRegex(ValueError, "未找到用户扩展 Model"):
                store.update_extension_model("kimi", "kimi-k2.6", self._definition())
            with self.assertRaisesRegex(ValueError, "未找到用户扩展 Model"):
                store.delete_extension_model("kimi", "kimi-k2.6")

            store.create_extension_model("kimi", "kimi-k2.7", self._definition())
            family = store.registry().model_families["kimi"]

            self.assertIn("kimi-k2.6", family.models)
            self.assertIn("kimi-k2.7", family.models)
            self.assertEqual(family.models["kimi-k2.7"].context_window, 262144)

    def test_extension_preset_resolves_through_existing_vendor_base_url(self) -> None:
        with self._store() as store:
            store.create_extension_model("kimi", "kimi-k2.7", self._definition())
            store.create("presets", {
                "id": "extension-kimi", "name": "Kimi K2.7", "vendor_id": "moonshot",
                "base_url_id": "official-cn", "credential_id": "moonshot-main",
                "model_family_id": "kimi", "model_id": "kimi-k2.7", "parameters": {},
            })

            runtime = store.registry().resolve(preset_id="extension-kimi")

            self.assertEqual(runtime.model_id, "kimi-k2.7")
            self.assertEqual(runtime.base_url_id, "official-cn")
            self.assertEqual(runtime.api_model_name, "kimi-k2.7")

    def test_extension_cannot_override_builtin_or_reference_unknown_family(self) -> None:
        with self._store() as store:
            with self.assertRaisesRegex(ValueError, "Model 'kimi-k2.6'"):
                store.create_extension_model("kimi", "kimi-k2.6", self._definition())
            with self.assertRaisesRegex(ValueError, "未找到 Model Family"):
                store.create_extension_model("missing", "missing-v1", self._definition())

    def test_user_extension_models_can_update_and_delete(self) -> None:
        with self._store() as store:
            store.create_extension_model("kimi", "kimi-k2.7", self._definition())
            store.update_extension_model("kimi", "kimi-k2.7", {"api_name": "kimi-k2.7-preview"})
            self.assertEqual(
                store.registry().model_families["kimi"].models["kimi-k2.7"].api_name,
                "kimi-k2.7-preview",
            )
            store.delete_extension_model("kimi", "kimi-k2.7")
            self.assertNotIn("kimi-k2.7", store.registry().model_families["kimi"].models)

    def test_restore_rejects_referenced_extension_and_preserves_registry(self) -> None:
        with self._store() as store:
            store.create_extension_model("kimi", "kimi-k2.7", self._definition())
            store.create("presets", {
                "id": "extension-kimi", "vendor_id": "moonshot", "base_url_id": "official-cn",
                "credential_id": "moonshot-main", "model_family_id": "kimi",
                "model_id": "kimi-k2.7", "parameters": {},
            })
            extension_path = store.user_root / "model_family_extensions" / "kimi.yaml"
            before = extension_path.read_bytes()

            with self.assertRaisesRegex(ValueError, "extension-kimi"):
                store.restore_builtin_model_definitions()

            self.assertEqual(extension_path.read_bytes(), before)
            self.assertIn("kimi-k2.7", store.registry().model_families["kimi"].models)

    def test_restore_removes_unreferenced_extension(self) -> None:
        with self._store() as store:
            store.create_extension_model("kimi", "kimi-k2.7", self._definition())
            store.restore_builtin_model_definitions()

            self.assertFalse((store.user_root / "model_family_extensions" / "kimi.yaml").exists())
            self.assertNotIn("kimi-k2.7", store.registry().model_families["kimi"].models)

    def test_raw_extension_rejects_global_model_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            user_root = Path(raw) / "user"
            path = user_root / "model_family_extensions" / "kimi.yaml"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "model_family_id": "kimi", "models": {"gpt-5": self._definition()},
            }), encoding="utf-8")

            with self.assertRaisesRegex(RegistryValidationError, "cannot override"):
                ModelRegistry(ROOT / "model_registry", user_root=user_root)

    @staticmethod
    def _definition() -> dict[str, object]:
        return {"api_name": "kimi-k2.7", "context_window": 262144, "capabilities": {}}

    @staticmethod
    def _store():
        directory = tempfile.TemporaryDirectory()
        return _TemporaryStore(directory)


class _TemporaryStore:
    def __init__(self, directory: tempfile.TemporaryDirectory[str]) -> None:
        self._directory = directory
        self.store = RegistryStore(
            Path(directory.name) / "user", builtin_root=ROOT / "model_registry"
        )

    def __enter__(self) -> RegistryStore:
        return self.store

    def __exit__(self, *_args: object) -> None:
        self._directory.cleanup()
