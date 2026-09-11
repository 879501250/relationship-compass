from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from eval_console import cli
from eval_console.registry_runtime import PresetReadiness


class RegistryContextSelectionTests(unittest.TestCase):
    def test_target_and_judge_select_vendor_before_context_runnable_preset(self) -> None:
        vendor = SimpleNamespace(id="vendor", name="Vendor", protocol="openai_compatible_chat", base_urls=(SimpleNamespace(protocol=None),))
        ready = SimpleNamespace(id="ready", name="Ready", model_id="ready-model", vendor_id="vendor")
        unavailable = SimpleNamespace(id="blocked", name="Blocked", model_id="blocked-model", vendor_id="vendor")
        resolver = SimpleNamespace(
            registry=SimpleNamespace(vendors={"vendor": vendor}, presets={"ready": ready, "blocked": unavailable}),
            assess_preset_readiness=lambda identifier, *, context: PresetReadiness(
                identifier, identifier == "ready", identifier == "ready", False,
                identifier == "ready", identifier == "ready", identifier == "ready", (), (),
            ),
        )
        with mock.patch.object(cli, "_choose", side_effect=["vendor", "ready"]) as choose:
            self.assertEqual(cli._choose_registry_preset(resolver, "Target"), "ready")
        self.assertEqual(choose.call_args_list[0].args[1][0], ("Vendor（1 个可运行 Preset）", "vendor"))
        self.assertEqual(choose.call_args_list[1].args[1][0], ("Ready（ready-model）", "ready"))

    def test_vendor_with_no_runnable_preset_offers_configuration_instead_of_error(self) -> None:
        vendor = SimpleNamespace(id="vendor", name="Vendor", protocol="openai_compatible_chat", base_urls=(SimpleNamespace(protocol=None),))
        text_only = SimpleNamespace(id="text-only", name="Text", model_id="text-model", vendor_id="vendor")
        resolver = SimpleNamespace(
            registry=SimpleNamespace(vendors={"vendor": vendor}, presets={"text-only": text_only}),
            assess_preset_readiness=lambda identifier, *, context: PresetReadiness(
                identifier, True, True, False, True, False, False, (), ("capability mismatch",),
            ),
        )
        with mock.patch.object(cli, "_choose", side_effect=["vendor", "__back__", None]):
            self.assertIsNone(cli._choose_registry_preset(resolver, "Judge"))
