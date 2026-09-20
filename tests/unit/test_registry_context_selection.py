from __future__ import annotations

from pathlib import Path
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
            mock.patch.object(cli, "_choose_registry_model_override", return_value=(None, False)),
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
        self.assertEqual(reload_resolver.call_count, 2)

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
            mock.patch.object(cli, "_choose_registry_model_override", return_value=(None, False)),
            mock.patch.object(cli, "_refresh_selected_preset_resolver", return_value=resolver),
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

    def test_interactive_run_carries_inline_model_choice_as_target_override(self) -> None:
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
            mock.patch.object(cli, "_choose_registry_model_override", side_effect=[("kimi-k2.7", False), (None, False)]),
            mock.patch.object(cli, "_refresh_selected_preset_resolver", return_value=resolver),
            mock.patch.object(cli.RegistryRuntimeResolver, "for_project", return_value=resolver),
            mock.patch.object(cli, "_interactive_concurrency", return_value=1),
            mock.patch.object(cli, "_yes_no", return_value=True),
            mock.patch.object(cli, "preflight_request", side_effect=preflight),
            mock.patch.object(cli, "_print_registry_preflight_summary"),
            mock.patch.object(cli, "_execute_and_print", return_value=0),
        ):
            self.assertEqual(cli._registry_interactive_run([definition], resolver, mock.Mock(), False, None, None), 0)

        self.assertEqual(captured[0].target_model_override, "kimi-k2.7")
        self.assertIsNone(captured[0].judge_model_override)

    def test_target_only_refreshes_before_model_override_for_a_new_preset(self) -> None:
        old = SimpleNamespace(registry=SimpleNamespace(presets={}))
        fresh_preset = SimpleNamespace(id="new-target", model_family_id="gpt", model_id="gpt-5")
        fresh = SimpleNamespace(registry=SimpleNamespace(presets={"new-target": fresh_preset}))
        definition = SimpleNamespace(eval_id="sample", title="Sample")
        seen = []

        with (
            mock.patch.object(cli, "_choose", side_effect=[EvalExecutionMode.TARGET_ONLY, definition, True]),
            mock.patch.object(cli, "_interactive_case_selection", return_value=["case-1"]),
            mock.patch.object(cli, "_choose_registry_preset", return_value="new-target"),
            mock.patch.object(cli.RegistryRuntimeResolver, "for_project", return_value=fresh),
            mock.patch.object(cli, "_choose_registry_model_override", side_effect=lambda resolver, preset_id, *_args, **_kwargs: (seen.append((resolver, preset_id)) or (None, False))),
            mock.patch.object(cli, "_interactive_concurrency", return_value=1),
            mock.patch.object(cli, "_yes_no", return_value=True),
            mock.patch.object(cli, "preflight_request", return_value=(None, None, {}, {})),
            mock.patch.object(cli, "_print_registry_preflight_summary"),
            mock.patch.object(cli, "_execute_and_print", return_value=0),
        ):
            self.assertEqual(cli._registry_interactive_run([definition], old, mock.Mock(), False, None, None), 0)

        self.assertEqual(seen, [(fresh, "new-target")])

    def test_judge_only_refreshes_before_model_override_for_a_new_preset(self) -> None:
        old = SimpleNamespace(registry=SimpleNamespace(presets={}))
        fresh_preset = SimpleNamespace(id="new-judge", model_family_id="gpt", model_id="gpt-5")
        fresh = SimpleNamespace(registry=SimpleNamespace(presets={"new-judge": fresh_preset}))
        definition = SimpleNamespace(eval_id="sample", title="Sample")
        historical = SimpleNamespace(
            run_dir=Path("history"), run_id="history", mode="full", failed_case_ids=(),
            error_case_ids=(), incomplete_case_ids=(),
        )
        metadata = {"console": {"eval_id": "sample"}, "cases": [{"case_id": "case-1"}]}
        seen = []

        with (
            mock.patch.object(cli, "_choose", side_effect=[EvalExecutionMode.JUDGE_ONLY, historical, True]),
            mock.patch.object(cli, "discover_runs", return_value=[historical]),
            mock.patch.object(cli, "find_eval", return_value=definition),
            mock.patch.object(cli.runner, "load_json_object", return_value=metadata),
            mock.patch.object(cli, "_choose_registry_preset", return_value="new-judge"),
            mock.patch.object(cli.RegistryRuntimeResolver, "for_project", return_value=fresh),
            mock.patch.object(cli, "_choose_registry_model_override", side_effect=lambda resolver, preset_id, *_args, **_kwargs: (seen.append((resolver, preset_id)) or (None, False))),
            mock.patch.object(cli, "_interactive_concurrency", return_value=1),
            mock.patch.object(cli, "_yes_no", return_value=True),
            mock.patch.object(cli, "preflight_request", return_value=(None, None, {}, {})),
            mock.patch.object(cli, "_print_registry_preflight_summary"),
            mock.patch.object(cli, "_execute_and_print", return_value=0),
        ):
            self.assertEqual(cli._registry_interactive_run([definition], old, mock.Mock(), False, None, None), 0)

        self.assertEqual(seen, [(fresh, "new-judge")])

    def test_full_run_refreshes_each_new_preset_before_its_model_override(self) -> None:
        old = SimpleNamespace(registry=SimpleNamespace(presets={}))
        target = SimpleNamespace(registry=SimpleNamespace(presets={"new-target": SimpleNamespace(id="new-target", model_family_id="gpt", model_id="gpt-5")}))
        judge = SimpleNamespace(registry=SimpleNamespace(presets={"new-judge": SimpleNamespace(id="new-judge", model_family_id="gpt", model_id="gpt-5")}))
        definition = SimpleNamespace(eval_id="sample", title="Sample")
        seen = []

        with (
            mock.patch.object(cli, "_choose", side_effect=[EvalExecutionMode.FULL, definition, True]),
            mock.patch.object(cli, "_interactive_case_selection", return_value=["case-1"]),
            mock.patch.object(cli, "_choose_registry_preset", side_effect=["new-target", "new-judge"]),
            mock.patch.object(cli.RegistryRuntimeResolver, "for_project", side_effect=[target, judge]),
            mock.patch.object(cli, "_choose_registry_model_override", side_effect=lambda resolver, preset_id, *_args, **_kwargs: (seen.append((resolver, preset_id)) or (None, False))),
            mock.patch.object(cli, "_interactive_concurrency", return_value=1),
            mock.patch.object(cli, "_yes_no", return_value=True),
            mock.patch.object(cli, "preflight_request", return_value=(None, None, {}, {})),
            mock.patch.object(cli, "_print_registry_preflight_summary"),
            mock.patch.object(cli, "_execute_and_print", return_value=0),
        ):
            self.assertEqual(cli._registry_interactive_run([definition], old, mock.Mock(), False, None, None), 0)

        self.assertEqual(seen, [(target, "new-target"), (judge, "new-judge")])

    def test_model_override_reports_missing_preset_after_refresh_without_key_error(self) -> None:
        resolver = SimpleNamespace(registry=SimpleNamespace(presets={}))

        with self.assertRaisesRegex(cli.EvalConsoleError, "刷新后的 Registry"):
            cli._choose_registry_model_override(resolver, "missing", "Target", registry_root=None)

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
