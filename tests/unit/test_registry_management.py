from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from eval_console.credential_store import LocalFileCredentialSecretStore, mask_token
from eval_console.interactive import InteractiveBack, InteractiveReader
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
            store.create("credentials", {"id": "moonshot-user", "name": "主账号", "vendor": "moonshot", "secret_ref": "local:moonshot-user", "expires_at": "2027-01-01", "created_at": "2026-01-01T00:00:00+00:00", "updated_at": "2026-01-01T00:00:00+00:00"})
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


class InteractiveReaderTests(unittest.TestCase):
    def test_yes_no_aliases_defaults_and_back(self) -> None:
        for raw, expected in (("y", True), ("Y", True), ("yes", True), ("YES", True), ("是", True), ("n", False), ("N", False), ("no", False), ("NO", False), ("否", False), ("", True)):
            reader = InteractiveReader(input_fn=lambda _prompt, value=raw: value)
            self.assertEqual(reader.confirm("继续？", default=True), expected)
        for raw in ("b", "back", "返回"):
            with self.assertRaises(InteractiveBack):
                InteractiveReader(input_fn=lambda _prompt, value=raw: value).text("字段: ")
