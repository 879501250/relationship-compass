"""Regression coverage for V1.3B Final Polish interaction contracts."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from eval_console.credential_store import mask_token
from eval_console.interactive import CLEAR_VALUE, InteractiveBack, InteractiveReader
from eval_console.model_registry import ModelRegistry, RegistryResolutionError, RegistryValidationError
from eval_console.registry_cli import _advanced_base_url, _credential_wizard, _move_default_conflicts, _optional_field, _run_wizard


ROOT = Path(__file__).resolve().parents[2]


class _ConfirmReader:
    def __init__(self, decisions: list[bool]) -> None:
        self.decisions = iter(decisions)

    def confirm(self, *_args, **_kwargs) -> bool:
        return next(self.decisions)


class _BackThenDoneManager:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, state: dict[str, str]) -> None:
        self.calls += 1
        if self.calls == 1: raise InteractiveBack()
        state["manager"] = "done"


class _AdvancedBackReader:
    def choice(self, *_args, **_kwargs):
        raise InteractiveBack()


class FinalPolishInteractionTests(unittest.TestCase):
    def test_default_conflict_uses_stable_endpoint_id_and_moves_real_conflict(self) -> None:
        endpoints = [
            {"id": "a", "default_for": ["kimi"]},
            {"id": "b", "default_for": []},
        ]
        unchanged = {"id": "a", "default_for": ["kimi"]}
        _move_default_conflicts(_ConfirmReader([]), endpoints, unchanged)
        self.assertEqual(unchanged["default_for"], ["kimi"])
        self.assertEqual(endpoints[0]["default_for"], ["kimi"])

        moved = {"id": "b", "default_for": ["kimi"]}
        _move_default_conflicts(_ConfirmReader([True]), endpoints, moved)
        self.assertEqual(endpoints[0]["default_for"], [])
        self.assertEqual(moved["default_for"], ["kimi"])

    def test_nested_back_rewinds_one_layer_without_losing_draft_and_advanced_back_returns(self) -> None:
        manager = _BackThenDoneManager()
        seen: list[str] = []
        def previous(state: dict[str, str]) -> None:
            seen.append(state["name"])
        result = _run_wizard([previous, manager], {"name": "kept"})
        self.assertEqual(result, {"name": "kept", "manager": "done"})
        self.assertEqual(seen, ["kept", "kept"])
        _advanced_base_url(_AdvancedBackReader(), object(), {})

    def test_optional_clear_keeps_default_or_removes_value(self) -> None:
        keep = InteractiveReader(input_fn=lambda _prompt: "").optional_value("Notes: ", default="old")
        clear = InteractiveReader(input_fn=lambda _prompt: "清除").optional_value("Notes: ", default="old")
        self.assertEqual(keep, "old")
        self.assertIs(clear, CLEAR_VALUE)
        state = {"expires_at": "2027-01-01", "notes": "old"}
        _optional_field(InteractiveReader(input_fn=lambda _prompt: "clear"), state, "expires_at", "Expires")
        _optional_field(InteractiveReader(input_fn=lambda _prompt: ""), state, "notes", "Notes")
        self.assertNotIn("expires_at", state)
        self.assertEqual(state["notes"], "old")

    def test_credential_edit_keeps_immutable_id_out_of_input_sequence(self) -> None:
        answers = iter(["", "n", "", ""])
        prompts: list[str] = []
        reader = InteractiveReader(input_fn=lambda prompt: (prompts.append(prompt), next(answers))[1])
        existing = {"id": "moonshot-user", "name": "Main", "vendor": "moonshot", "secret_ref": "local:moonshot-user", "base_url_ids": [], "expires_at": "2027-01-01"}
        draft, token = _credential_wizard(reader, ModelRegistry(ROOT / "model_registry"), existing, immutable_vendor=True, collect_token=False)
        self.assertEqual(draft["id"], "moonshot-user")
        self.assertIsNone(token)
        self.assertFalse(any("Credential ID:" in prompt for prompt in prompts))

    def test_mask_contract_never_shows_short_token_material(self) -> None:
        for length in (1, 7, 8, 12):
            token = "x" * length
            self.assertEqual(mask_token(token), "********")
            self.assertNotIn(token, mask_token(token))
        for length in (13, 24):
            token = "a" * (length - 4) + "LAST"
            self.assertEqual(mask_token(token), "********LAST")
            self.assertNotIn(token, mask_token(token))
        for length in (25, 53):
            token = "pre" + "x" * (length - 7) + "LAST"
            self.assertEqual(mask_token(token), "pre****LAST")
            self.assertNotIn(token, mask_token(token))


class FinalPolishRegistryContractTests(unittest.TestCase):
    def test_unrestricted_scope_is_dynamic_and_restricted_scope_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._write(root, "vendors/vendor.yaml", self._vendor(["a", "b"]))
            self._write(root, "model_families/family.yaml", self._family())
            self._write(root, "presets/preset.yaml", self._preset())
            self._write(root, "credentials/unrestricted.yaml", {"id": "unrestricted", "vendor": "vendor", "secret_ref": "local:unrestricted", "base_url_ids": []})
            self._write(root, "credentials/restricted.yaml", {"id": "restricted", "vendor": "vendor", "secret_ref": "local:restricted", "base_url_ids": ["a"]})
            vendor = json.loads((root / "vendors/vendor.yaml").read_text(encoding="utf-8"))
            vendor["base_urls"].append({"id": "c", "url": "https://c.example/v1", "model_families": ["family"], "default_for": []})
            self._write(root, "vendors/vendor.yaml", vendor)
            registry = ModelRegistry(root)
            self.assertEqual(registry.resolve(vendor_id="vendor", credential_id="unrestricted", model_family_id="family", model_id="model", base_url_id="c").base_url_id, "c")
            with self.assertRaisesRegex(RegistryResolutionError, "not scoped"):
                registry.resolve(vendor_id="vendor", credential_id="restricted", model_family_id="family", model_id="model", base_url_id="c")

    def test_local_secret_ref_must_match_credential_id_while_env_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._write(root, "vendors/vendor.yaml", self._vendor(["a"]))
            self._write(root, "model_families/family.yaml", self._family())
            self._write(root, "presets/preset.yaml", self._preset())
            self._write(root, "credentials/wrong.yaml", {"id": "wrong", "vendor": "vendor", "secret_ref": "local:shared"})
            with self.assertRaisesRegex(RegistryValidationError, "local secret_ref must be 'local:wrong'"):
                ModelRegistry(root)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._write(root, "vendors/vendor.yaml", self._vendor(["a"]))
            self._write(root, "model_families/family.yaml", self._family())
            self._write(root, "presets/preset.yaml", self._preset(credential="env"))
            self._write(root, "credentials/env.yaml", {"id": "env", "vendor": "vendor", "env": "VENDOR_API_KEY"})
            self.assertIn("env", ModelRegistry(root).credentials)

    def test_model_capability_override_is_sparse_and_inherits_after_clear(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._write(root, "vendors/vendor.yaml", self._vendor(["a"]))
            self._write(root, "model_families/family.yaml", {"id": "family", "name": "Family", "defaults": {"capabilities": {"thinking": {"supported": True, "allowed_values": ["enabled", "disabled"]}, "structured_output": {"supported": True, "modes": ["json_object"]}}}, "models": {"model": {"capabilities": {"thinking": {"supported": False}}}}})
            self._write(root, "credentials/key.yaml", {"id": "key", "vendor": "vendor", "env": "VENDOR_API_KEY"})
            self._write(root, "presets/preset.yaml", self._preset(credential="key"))
            registry = ModelRegistry(root)
            runtime = registry.resolve(vendor_id="vendor", credential_id="key", model_family_id="family", model_id="model", base_url_id="a")
            self.assertFalse(runtime.resolved_capabilities["thinking"]["supported"])
            self.assertEqual(runtime.resolved_capabilities["structured_output"]["modes"], ("json_object",))
            family = json.loads((root / "model_families/family.yaml").read_text(encoding="utf-8"))
            family["models"]["model"].pop("capabilities")
            self._write(root, "model_families/family.yaml", family)
            self.assertTrue(ModelRegistry(root).resolve(vendor_id="vendor", credential_id="key", model_family_id="family", model_id="model", base_url_id="a").resolved_capabilities["thinking"]["supported"])

    @staticmethod
    def _vendor(ids: list[str]) -> dict[str, object]:
        return {"id": "vendor", "name": "Vendor", "protocol": "openai_compatible_chat", "base_urls": [{"id": identifier, "url": f"https://{identifier}.example/v1", "model_families": ["family"], "default_for": ["family"] if identifier == "a" else []} for identifier in ids]}

    @staticmethod
    def _family() -> dict[str, object]:
        return {"id": "family", "name": "Family", "defaults": {"capabilities": {}}, "models": {"model": {}}}

    @staticmethod
    def _preset(*, credential: str = "unrestricted") -> dict[str, object]:
        return {"id": "preset", "vendor": "vendor", "base_url": "a", "credential": credential, "model_family": "family", "model": "model", "parameters": {}}

    @staticmethod
    def _write(root: Path, relative: str, value: dict[str, object]) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
