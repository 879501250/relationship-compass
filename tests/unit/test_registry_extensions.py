"""Model Family extension and immediate Registry validation contracts."""

from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

from eval_console.model_registry import (
    ModelRegistry,
    RegistryValidationError,
    validate_base_url_configuration,
)
from eval_console.interactive import InteractiveReader
from eval_console.registry_cli import (
    _base_url_wizard,
    _choose_base_url_protocol,
    _choose_or_create_model,
    _choose_vendor_protocol,
    _protocol_label,
    _validated_entity_id,
    _validated_model_id,
    _validated_protocol,
    _validated_url,
    _vendor_wizard,
)
from eval_console.registry_store import RegistryStore
from eval_console.registry_runtime import RegistryProviderFactory


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


class ProtocolChoiceInteractionTests(unittest.TestCase):
    def test_vendor_choice_saves_canonical_protocol_and_can_be_unset(self) -> None:
        registry = SimpleNamespace(model_families={"kimi": object()})
        existing = {
            "id": "relay", "name": "Relay", "protocol": "openai_responses",
            "base_urls": [{"id": "primary", "url": "https://api.example/v1", "protocol": "openai_responses", "model_families": ["kimi"], "default_for": ["kimi"]}],
        }
        reader = _ChoiceReader(["openai_compatible_chat", "done"])

        updated = _vendor_wizard(reader, registry, existing)

        self.assertEqual(updated["protocol"], "openai_compatible_chat")
        prompt, choices, default = reader.choice_calls[0]
        self.assertEqual(prompt, "请选择 Vendor 默认 Protocol")
        self.assertEqual(default, "openai_responses")
        self.assertIn(("OpenAI Compatible Chat", "openai_compatible_chat"), choices)
        self.assertEqual(
            {value for _label, value in choices if isinstance(value, str)},
            RegistryProviderFactory.SUPPORTED_PROTOCOLS,
        )

        unset_reader = _ChoiceReader(["__unset__"])
        self.assertIsNone(_choose_vendor_protocol(unset_reader, None))
        self.assertIn("不设置，由各 Base URL 单独指定", [label for label, _ in unset_reader.choice_calls[0][1]])

    def test_base_url_inheritance_is_not_persisted_and_override_wins(self) -> None:
        registry = SimpleNamespace(model_families={"kimi": object()})
        existing = {
            "id": "primary", "url": "https://api.example/v1", "model_families": ["kimi"], "default_for": ["kimi"],
        }
        inherited_reader = _ChoiceReader(["__inherit__"])
        inherited = _base_url_wizard(
            inherited_reader, registry, [existing], existing,
            vendor_protocol="openai_compatible_chat",
        )
        self.assertNotIn("protocol", inherited)
        self.assertIsNotNone(inherited_reader.choice_calls[0][2])

        override_reader = _ChoiceReader(["openai_responses"])
        overridden = _base_url_wizard(
            override_reader, registry, [existing], existing,
            vendor_protocol="openai_compatible_chat",
        )
        self.assertEqual(overridden["protocol"], "openai_responses")
        own_default_reader = _ChoiceReader(["openai_responses"])
        self.assertEqual(
            _choose_base_url_protocol(own_default_reader, "openai_responses", "openai_compatible_chat"),
            "openai_responses",
        )
        self.assertEqual(own_default_reader.choice_calls[0][2], "openai_responses")

    def test_base_url_without_vendor_protocol_requires_explicit_runtime_protocol(self) -> None:
        reader = _ChoiceReader(["openai_compatible_chat"])

        selected = _choose_base_url_protocol(reader, None, None)

        self.assertEqual(selected, "openai_compatible_chat")
        _prompt, choices, default = reader.choice_calls[0]
        self.assertNotIn("继承 Vendor", " ".join(label for label, _ in choices))
        self.assertIsNone(default)

    def test_clearing_vendor_protocol_rejects_base_urls_that_depend_on_it(self) -> None:
        registry = SimpleNamespace(model_families={"kimi": object()})
        existing = {
            "id": "relay", "name": "Relay", "protocol": "openai_compatible_chat",
            "base_urls": [{"id": "official", "url": "https://api.example/v1", "model_families": ["kimi"], "default_for": ["kimi"]}],
        }
        reader = _ChoiceReader(["__unset__", "openai_compatible_chat", "done"])
        output = io.StringIO()

        with redirect_stdout(output):
            updated = _vendor_wizard(reader, registry, existing)

        self.assertEqual(updated["protocol"], "openai_compatible_chat")
        self.assertIn("official", output.getvalue())
        self.assertIn("无法清除 Vendor 默认 Protocol", output.getvalue())

    def test_friendly_protocol_label_falls_back_to_the_canonical_value(self) -> None:
        self.assertEqual(_protocol_label("future_protocol"), "future_protocol")

    def test_choice_reader_shows_friendly_labels_and_accepts_the_default(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            selected = InteractiveReader(input_fn=lambda _prompt: "").choice(
                "请选择 Vendor 默认 Protocol",
                [("OpenAI Compatible Chat", "openai_compatible_chat")],
                default="openai_compatible_chat",
            )

        self.assertEqual(selected, "openai_compatible_chat")
        self.assertIn("OpenAI Compatible Chat（默认）", output.getvalue())
        self.assertNotIn("openai_compatible_chat", output.getvalue())

    def test_choice_reader_does_not_treat_a_none_choice_as_an_implicit_default(self) -> None:
        answers = iter(["", "2"])
        selected = InteractiveReader(input_fn=lambda _prompt: next(answers)).choice(
            "状态", [("保持当前", None), ("Active", "active")],
        )

        self.assertEqual(selected, "active")


class _ChoiceReader:
    def __init__(self, selections: list[str]) -> None:
        self._selections = iter(selections)
        self.choice_calls: list[tuple[str, list[tuple[str, object]], object]] = []

    def text(self, _prompt: str, *, default: str | None = None, required: bool = False) -> str:
        if default is not None:
            return default
        if required:
            return "value"
        return ""

    def optional_value(self, _prompt: str, *, default: str | None = None) -> str | None:
        return default

    def choice(self, prompt: str, choices: list[tuple[str, object]], *, default: object = None) -> object:
        copied = list(choices)
        self.choice_calls.append((prompt, copied, default))
        selected = next(self._selections)
        if selected == "__inherit__":
            return next(value for label, value in copied if label.startswith("继承 Vendor："))
        if selected == "__unset__":
            return next(value for label, value in copied if label.startswith("不设置，"))
        for _label, value in copied:
            if value == selected:
                return value
        raise AssertionError(f"choice {selected!r} was not available for {prompt!r}")

    def multi_choice(self, _prompt: str, choices: list[tuple[str, str]]) -> list[str]:
        return [choices[0][1]]


class _InlineModelReader(_ChoiceReader):
    def __init__(self, model_id: str, api_name: str) -> None:
        super().__init__(["__create_model__", "inherit"])
        self._values = iter([model_id, api_name])

    def text(self, _prompt: str, *, default: str | None = None, required: bool = False) -> str:
        return next(self._values)

    def optional_value(self, _prompt: str, *, default: str | None = None) -> str | None:
        return None


class _RetryInlineModelReader(_ChoiceReader):
    def __init__(self, model_ids: list[str]) -> None:
        super().__init__(["__create_model__", "__create_model__", "inherit"])
        self._model_ids = iter(model_ids)

    def text(self, prompt: str, *, default: str | None = None, required: bool = False) -> str:
        return next(self._model_ids) if prompt == "Model ID: " else default or ""

    def optional_value(self, _prompt: str, *, default: str | None = None) -> str | None:
        return None


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

    def test_inline_model_creation_persists_builtin_family_as_an_extension_and_reloads(self) -> None:
        with self._store() as store:
            original = store.registry()
            reader = _InlineModelReader("kimi-k2.7", "kimi-k2.7-preview")

            selected, refreshed = _choose_or_create_model(
                reader, store, original, "kimi", prompt="选择默认 Model"
            )

            self.assertEqual(selected, "kimi-k2.7")
            self.assertIsNot(refreshed, original)
            self.assertEqual(refreshed.model_families["kimi"].models["kimi-k2.7"].api_name, "kimi-k2.7-preview")
            self.assertTrue((store.user_root / "model_family_extensions" / "kimi.yaml").is_file())

    def test_inline_model_creation_adds_to_a_user_family_without_an_extension(self) -> None:
        with self._store() as store:
            store.create("model_families", {
                "id": "local", "name": "Local", "defaults": {"capabilities": {}},
                "models": {"local-v1": {}},
            })
            original = store.registry()

            selected, refreshed = _choose_or_create_model(
                _InlineModelReader("local-v2", "local-v2"), store, original, "local", prompt="选择 Model"
            )

            self.assertEqual(selected, "local-v2")
            self.assertIn("local-v2", refreshed.model_families["local"].models)
            document = store.read_user_document("model_families", "local")
            self.assertIn("local-v2", document["models"])
            self.assertFalse((store.user_root / "model_family_extensions" / "local.yaml").exists())

    def test_inline_model_creation_retries_invalid_id_and_cancel_does_not_write(self) -> None:
        with self._store() as store:
            registry = store.registry()
            reader = _RetryInlineModelReader(["Kimi-K2.7", "kimi-k2.7"])
            output = io.StringIO()

            with redirect_stdout(output):
                selected, refreshed = _choose_or_create_model(
                    reader, store, registry, "kimi", prompt="选择 Model"
                )

            self.assertEqual(selected, "kimi-k2.7")
            self.assertIn("Model ID 'Kimi-K2.7' 格式无效", output.getvalue())
            self.assertIn("kimi-k2.7", refreshed.model_families["kimi"].models)

        with self._store() as store:
            registry = store.registry()
            answers = iter([str(len(registry.model_families["kimi"].models) + 1), "cancel", "1"])
            reader = InteractiveReader(input_fn=lambda _prompt: next(answers))

            selected, refreshed = _choose_or_create_model(
                reader, store, registry, "kimi", prompt="选择 Model"
            )

            self.assertEqual(selected, "kimi-k2.6")
            self.assertIs(refreshed, registry)
            self.assertFalse((store.user_root / "model_family_extensions" / "kimi.yaml").exists())

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
