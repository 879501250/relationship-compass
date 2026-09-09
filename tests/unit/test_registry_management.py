from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from eval_console.credential_service import CredentialService, CredentialTransactionError, CredentialUpdateDraft
from eval_console.credential_store import LocalFileCredentialSecretStore, mask_token
from eval_console.interactive import InteractiveBack, InteractiveCancel, InteractiveReader
from eval_console.registry_cli import _run_wizard
from eval_console.registry_store import RegistryStore


ROOT = Path(__file__).resolve().parents[2]


class CredentialSecretStoreTests(unittest.TestCase):
    def test_crud_mask_and_atomic_write_failure_preserves_old_secret(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = LocalFileCredentialSecretStore(Path(raw) / "credentials.secrets.json")
            store.set("moonshot-main", "sk-abcdefgh")
            self.assertEqual(store.get("moonshot-main"), "sk-abcdefgh")
            self.assertEqual(mask_token(store.get("moonshot-main")), "sk-****efgh")
            self.assertEqual(mask_token("short"), "********")
            with mock.patch.object(Path, "replace", side_effect=OSError("disk failure")):
                with self.assertRaisesRegex(ValueError, "无法保存"):
                    store.set("moonshot-main", "sk-new-secret")
            self.assertEqual(store.get("moonshot-main"), "sk-abcdefgh")
            store.delete("moonshot-main")
            self.assertFalse(store.exists("moonshot-main"))
            self.assertEqual(store.list_ids(), set())


class CredentialTransactionTests(unittest.TestCase):
    def _document(self, identifier: str = "moonshot-user", *, name: str = "主账号") -> dict[str, str]:
        return {"id": identifier, "name": name, "vendor": "moonshot", "secret_ref": f"local:{identifier}"}

    def _stores(self, raw: str) -> tuple[RegistryStore, LocalFileCredentialSecretStore, CredentialService]:
        registry = RegistryStore(Path(raw) / "user", builtin_root=ROOT / "model_registry")
        secrets = LocalFileCredentialSecretStore(Path(raw) / "credentials.secrets.json")
        return registry, secrets, CredentialService(registry, secrets)

    def test_create_validation_failure_never_writes_secret_and_status_reports_orphan_missing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry, secrets, service = self._stores(raw)
            with self.assertRaises(ValueError):
                service.create({"id": "bad", "vendor": "missing", "secret_ref": "local:bad"}, "sk-never-write")
            self.assertFalse(secrets.exists("bad"))
            service.create(self._document(), "sk-original-token")
            secrets.set("orphan", "sk-orphan-token")
            secrets.delete("moonshot-user")
            status = service.status()
            self.assertEqual(status.orphan_secret_ids, frozenset({"orphan"}))
            self.assertEqual(status.missing_secret_ids, frozenset({"moonshot-user"}))

    def test_create_update_and_delete_compensate_metadata_or_secret_failures(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry, secrets, service = self._stores(raw)
            document = self._document()
            with mock.patch.object(registry, "create", side_effect=OSError("metadata disk failure")):
                with self.assertRaisesRegex(CredentialTransactionError, "已恢复"):
                    service.create(document, "sk-new-secret")
            self.assertFalse(secrets.exists("moonshot-user"))

            service.create(document, "sk-original-token")
            revised = dict(document, name="改名")
            with mock.patch.object(registry, "update", side_effect=OSError("metadata disk failure")):
                with self.assertRaisesRegex(CredentialTransactionError, "已恢复"):
                    service.update("moonshot-user", CredentialUpdateDraft(revised, replace_token=True, new_token="sk-replaced-token"))
            self.assertEqual(secrets.get("moonshot-user"), "sk-original-token")
            self.assertEqual(registry.registry().credentials["moonshot-user"].name, "主账号")

            with mock.patch.object(secrets, "delete", side_effect=OSError("secret disk failure")):
                with self.assertRaisesRegex(CredentialTransactionError, "元数据保持"):
                    service.delete("moonshot-user")
            self.assertIn("moonshot-user", registry.registry().credentials)
            with mock.patch.object(registry, "delete", side_effect=OSError("metadata disk failure")):
                with self.assertRaisesRegex(CredentialTransactionError, "已恢复"):
                    service.delete("moonshot-user")
            self.assertEqual(secrets.get("moonshot-user"), "sk-original-token")
            self.assertIn("moonshot-user", registry.registry().credentials)

    def test_transaction_errors_never_include_tokens_or_draft_repr(self) -> None:
        token = "sk-sensitive-token"
        draft = CredentialUpdateDraft(self._document(), replace_token=True, new_token=token)
        self.assertNotIn(token, repr(draft))
        with tempfile.TemporaryDirectory() as raw:
            registry, secrets, service = self._stores(raw)
            with mock.patch.object(secrets, "set", side_effect=OSError(token)):
                with self.assertRaises(CredentialTransactionError) as caught:
                    service.create(self._document(), token)
            self.assertNotIn(token, str(caught.exception))


class RegistryStoreTests(unittest.TestCase):
    def test_user_registry_crud_is_transactional_and_builtin_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = RegistryStore(Path(raw) / "user", builtin_root=ROOT / "model_registry")
            store.create("vendors", {"id": "office-relay", "name": "Office Relay", "protocol": "openai_compatible_chat", "base_urls": [{"id": "chat", "url": "https://relay.example/v1", "model_families": ["kimi"], "default_for": ["kimi"]}]})
            self.assertIn("office-relay", store.registry().vendors)
            with self.assertRaisesRegex(ValueError, "内置定义"):
                store.create("vendors", {"id": "moonshot", "name": "No", "base_urls": []})
            with self.assertRaises(Exception):
                store.create("vendors", {"id": "broken", "name": "Broken", "base_urls": []})
            self.assertFalse((store.user_root / "vendors" / "broken.yaml").exists())

    def test_credential_references_and_expiry_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = RegistryStore(Path(raw) / "user", builtin_root=ROOT / "model_registry")
            store.create("credentials", {"id": "moonshot-user", "name": "主账号", "vendor": "moonshot", "secret_ref": "local:moonshot-user", "base_url_ids": ["official-cn"], "expires_at": "2027-01-01", "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"})
            store.create("presets", {"id": "user-kimi", "name": "用户 Kimi", "vendor": "moonshot", "base_url": "official-cn", "credential": "moonshot-user", "model_family": "kimi", "model": "kimi-k2.6", "parameters": {}})
            self.assertIn("Preset: user-kimi", store.references("credentials", "moonshot-user"))
            with self.assertRaisesRegex(ValueError, "仍被引用"):
                store.delete("credentials", "moonshot-user")
            store.delete("presets", "user-kimi")
            store.delete("credentials", "moonshot-user")
            store.create("credentials", {"id": "future-key", "name": "Future", "vendor": "moonshot", "secret_ref": "local:future-key", "expires_at": "2027-01-01"})
            self.assertEqual(store.credential_status("future-key", today=date(2026, 12, 22)), "即将过期")
            self.assertEqual(store.credential_status("future-key", today=date(2026, 9, 1)), "有效")
            self.assertEqual(store.credential_status("future-key", today=date(2027, 1, 2)), "已过期")

    def test_multi_endpoint_vendor_family_models_and_credential_scope_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            store = RegistryStore(Path(raw) / "user", builtin_root=ROOT / "model_registry")
            store.create("model_families", {"id": "lab", "name": "Lab", "description": "User family", "defaults": {"context_window": 4096, "capabilities": {}}, "models": {"lab-1": {"api_name": "lab-one"}, "lab-2": {}}})
            store.create("vendors", {"id": "lab-relay", "name": "Lab Relay", "protocol": "openai_compatible_chat", "base_urls": [
                {"id": "primary", "url": "https://primary.example/v1", "model_families": ["lab", "kimi"], "default_for": ["lab"], "parameter_mapping": {"max_output_tokens": "max_tokens"}, "transport_defaults": {"max_retries": 1}, "model_overrides": {"lab-1": {"api_name": "lab-one-api"}}},
                {"id": "backup", "url": "https://backup.example/v1", "protocol": "openai_compatible_chat", "model_families": ["lab"], "default_for": []},
            ]})
            store.create("credentials", {"id": "lab-key", "vendor": "lab-relay", "secret_ref": "local:lab-key", "base_url_ids": ["primary"]})
            store.create("presets", {"id": "lab-preset", "name": "Lab", "vendor": "lab-relay", "base_url": "primary", "credential": "lab-key", "model_family": "lab", "model": "lab-1", "parameters": {}})
            self.assertEqual(store.registry().resolve(preset_id="lab-preset").api_model_name, "lab-one-api")
            with self.assertRaisesRegex(ValueError, "Registry validation failed"):
                store.create("presets", {"id": "out-of-scope", "vendor": "lab-relay", "base_url": "backup", "credential": "lab-key", "model_family": "lab", "model": "lab-1", "parameters": {}})
            self.assertFalse((store.user_root / "presets" / "out-of-scope.yaml").exists())


class InteractiveReaderTests(unittest.TestCase):
    def test_yes_no_aliases_defaults_and_back(self) -> None:
        for raw, expected in (("y", True), ("Y", True), ("yes", True), ("YES", True), ("是", True), ("n", False), ("N", False), ("no", False), ("NO", False), ("否", False), ("", True)):
            reader = InteractiveReader(input_fn=lambda _prompt, value=raw: value)
            self.assertEqual(reader.confirm("继续？", default=True), expected)
        for raw in ("b", "back", "返回"):
            with self.assertRaises(InteractiveBack):
                InteractiveReader(input_fn=lambda _prompt, value=raw: value).text("字段: ")

    def test_cancel_secret_back_multi_choice_and_real_draft_rewind(self) -> None:
        with self.assertRaises(InteractiveCancel):
            InteractiveReader(input_fn=lambda _prompt: "cancel").text("字段: ")
        with self.assertRaises(InteractiveBack):
            InteractiveReader(secret_fn=lambda _prompt: "back").secret("Token: ")
        values = iter(["1, 3"])
        reader = InteractiveReader(input_fn=lambda _prompt: next(values))
        self.assertEqual(reader.multi_choice("Families", [("a", "a"), ("b", "b"), ("c", "c")]), ["a", "c"])

        values = iter(["first", "b", "correct", "second"])
        reader = InteractiveReader(input_fn=lambda _prompt: next(values))
        def one(draft: dict[str, str]) -> None: draft["one"] = reader.text("one: ", required=True)
        def two(draft: dict[str, str]) -> None: draft["two"] = reader.text("two: ", required=True)
        self.assertEqual(_run_wizard([one, two]), {"one": "correct", "two": "second"})
