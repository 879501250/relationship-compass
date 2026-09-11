from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from eval_console import cli
from eval_console.registry_runtime import PresetReadiness


class RegistryContextSelectionTests(unittest.TestCase):
    def test_target_and_judge_only_offer_context_runnable_presets(self) -> None:
        ready = SimpleNamespace(model_id="ready-model")
        unavailable = SimpleNamespace(model_id="blocked-model")
        resolver = SimpleNamespace(
            registry=SimpleNamespace(presets={"ready": ready, "blocked": unavailable}),
            assess_preset_readiness=lambda identifier, *, context: PresetReadiness(
                identifier, identifier == "ready", identifier == "ready", False,
                identifier == "ready", identifier == "ready", identifier == "ready", (), (),
            ),
        )
        with mock.patch.object(cli, "_choose", return_value="ready") as choose:
            self.assertEqual(cli._choose_registry_preset(resolver, "Target"), "ready")
        self.assertEqual(choose.call_args.args[1], [("ready（ready-model，可用）", "ready")])

    def test_judge_context_reports_no_compatible_preset_without_exposing_traceback(self) -> None:
        resolver = SimpleNamespace(
            registry=SimpleNamespace(presets={"text-only": SimpleNamespace(model_id="text-model")}),
            assess_preset_readiness=lambda identifier, *, context: PresetReadiness(
                identifier, True, True, False, True, False, False, (), ("capability mismatch",),
            ),
        )
        with self.assertRaisesRegex(cli.EvalConsoleError, "Judge Context"):
            cli._choose_registry_preset(resolver, "Judge")
