from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

from eval_console import cli
from eval_console.models import EvalExecutionMode
from eval_console.registry_runtime import PresetReadiness, RegistryProviderFactory


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

    def test_full_run_reloads_registry_before_judge_selection(self) -> None:
        old = SimpleNamespace(registry=SimpleNamespace(presets={"old": object()}))
        fresh = SimpleNamespace(registry=SimpleNamespace(presets={"new": object()}))
        definition = SimpleNamespace(eval_id="sample", title="Sample")
        selected: list[tuple[str, object]] = []

        def select_preset(resolver: object, role: str, **_kwargs: object) -> str:
            selected.append((role, resolver))
            return "new"

        with (
            mock.patch.object(cli, "_choose", side_effect=[EvalExecutionMode.FULL, definition, True]),
            mock.patch.object(cli, "_interactive_case_selection", return_value=["case-1"]),
            mock.patch.object(cli, "_choose_registry_preset", side_effect=select_preset),
            mock.patch.object(cli.RegistryRuntimeResolver, "for_project", return_value=fresh) as reload_resolver,
            mock.patch.object(cli, "_interactive_concurrency", return_value=1),
            mock.patch.object(cli, "_yes_no", return_value=True),
            mock.patch.object(cli, "preflight_request", return_value=(None, None, {}, {})),
            mock.patch.object(cli, "_print_registry_preflight_summary"),
            mock.patch.object(cli, "_execute_and_print", return_value=0),
        ):
            self.assertEqual(cli._registry_interactive_run([definition], old, mock.Mock(), False, None, None), 0)

        self.assertEqual(selected[0], ("Target", old))
        self.assertEqual(selected[1], ("Judge", fresh))
        self.assertIn("new", selected[1][1].registry.presets)
        reload_resolver.assert_called_once()

    def test_interactive_run_carries_dirty_debug_into_request(self) -> None:
        resolver = SimpleNamespace(registry=SimpleNamespace(presets={}))
        definition = SimpleNamespace(eval_id="sample", title="Sample")
        captured = []

        def preflight(request: object) -> tuple[None, None, dict[str, object], dict[str, object]]:
            captured.append(request)
            return None, None, {}, {}

        with (
            mock.patch.object(cli, "_choose", side_effect=[EvalExecutionMode.FULL, definition, True]),
            mock.patch.object(cli, "_interactive_case_selection", return_value=["case-1"]),
            mock.patch.object(cli, "_choose_registry_preset", side_effect=["target", "judge"]),
            mock.patch.object(cli.RegistryRuntimeResolver, "for_project", return_value=resolver),
            mock.patch.object(cli, "_interactive_concurrency", return_value=1),
            mock.patch.object(cli, "_yes_no", return_value=True),
            mock.patch.object(cli, "preflight_request", side_effect=preflight),
            mock.patch.object(cli, "_print_registry_preflight_summary"),
            mock.patch.object(cli, "_execute_and_print", return_value=0),
        ):
            self.assertEqual(
                cli._registry_interactive_run(
                    [definition], resolver, mock.Mock(), False, None, None, True
                ),
                0,
            )

        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].allow_dirty_debug)

    def test_runtime_back_from_vendor_or_preset_creation_stays_in_selection(self) -> None:
        vendor = SimpleNamespace(id="vendor", name="Vendor", protocol="openai_compatible_chat", base_urls=(SimpleNamespace(protocol=None),))
        resolver = SimpleNamespace(
            registry=SimpleNamespace(vendors={"vendor": vendor}, presets={}),
            assess_preset_readiness=lambda identifier, *, context: PresetReadiness(
                identifier, True, True, False, True, True, True, (), (),
            ),
        )
        with (
            mock.patch.object(cli, "create_vendor_for_runtime", return_value=None) as create_vendor,
            mock.patch.object(cli, "_choose", side_effect=["__create_vendor__", None]),
        ):
            self.assertIsNone(cli._choose_registry_preset(resolver, "Target"))
        create_vendor.assert_called_once()

        with (
            mock.patch.object(cli, "create_preset_for_runtime", return_value=None) as create_preset,
            mock.patch.object(cli, "_choose", side_effect=["vendor", "__create_preset__", "__back__", None]),
        ):
            self.assertIsNone(cli._choose_registry_preset(resolver, "Target"))
        create_preset.assert_called_once()

    def test_configure_then_reselect_uses_reloaded_registry(self) -> None:
        vendor = SimpleNamespace(id="vendor", name="Vendor", protocol="openai_compatible_chat", base_urls=(SimpleNamespace(protocol=None),))
        old = SimpleNamespace(
            registry=SimpleNamespace(vendors={"vendor": vendor}, presets={}),
            assess_preset_readiness=lambda identifier, *, context: PresetReadiness(identifier, True, True, False, True, True, True, (), ()),
        )
        fresh_preset = SimpleNamespace(id="fresh", name="Fresh", model_id="model", vendor_id="vendor")
        fresh = SimpleNamespace(
            registry=SimpleNamespace(vendors={"vendor": vendor}, presets={"fresh": fresh_preset}),
            assess_preset_readiness=lambda identifier, *, context: PresetReadiness(identifier, True, True, False, True, True, True, (), ()),
        )
        with (
            mock.patch.object(cli, "_choose", side_effect=["vendor", "__configure__", "fresh"]),
            mock.patch.object(cli, "manage_registry") as manage,
            mock.patch.object(cli.RegistryRuntimeResolver, "for_project", return_value=fresh) as reload_resolver,
        ):
            self.assertEqual(cli._choose_registry_preset(old, "Target"), "fresh")
        manage.assert_called_once()
        reload_resolver.assert_called_once()

    def test_runtime_vendor_protocol_support_tracks_provider_factory(self) -> None:
        vendor = SimpleNamespace(id="vendor", name="Vendor", protocol="test_protocol", base_urls=(SimpleNamespace(protocol=None),))
        resolver = SimpleNamespace(
            registry=SimpleNamespace(vendors={"vendor": vendor}, presets={}),
            assess_preset_readiness=lambda identifier, *, context: PresetReadiness(identifier, True, True, False, True, True, True, (), ()),
        )
        with (
            mock.patch.object(RegistryProviderFactory, "SUPPORTED_PROTOCOLS", frozenset({"test_protocol"})),
            mock.patch.object(cli, "_choose", return_value=None) as choose,
        ):
            self.assertIsNone(cli._choose_registry_preset(resolver, "Target"))
        self.assertEqual(choose.call_args.args[1][0], ("Vendor（0 个可运行 Preset）", "vendor"))
