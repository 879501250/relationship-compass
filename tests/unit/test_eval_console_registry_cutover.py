"""V1.3C.1 Console cutover and non-network safety regressions."""

from __future__ import annotations

import io
import unittest
from unittest import mock

from eval_console import cli
from eval_console.cli import _request_from_args, build_parser, registry_interactive_console
from eval_console.discovery import discover_evals
from eval_console.service import EvalConsoleError, validate_request


class RegistryCutoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = discover_evals()[0]

    def test_profile_arguments_are_rejected_before_new_run(self) -> None:
        args = build_parser().parse_args([
            "run", self.definition.eval_id, "--profile", "old-profile",
        ])
        with self.assertRaisesRegex(EvalConsoleError, "Model Registry"):
            _request_from_args(args, self.definition.eval_id, [self.definition.cases[0].case_id])

    def test_target_only_requires_no_judge_preset(self) -> None:
        args = build_parser().parse_args([
            "run", self.definition.eval_id, "--mode", "target-only", "--target-preset", "kimi-official", "--dry-run",
        ])
        request = _request_from_args(args, self.definition.eval_id, [self.definition.cases[0].case_id])
        self.assertIsNone(request.judge_preset_id)
        validate_request(request)

    def test_full_run_requires_both_presets(self) -> None:
        args = build_parser().parse_args([
            "run", self.definition.eval_id, "--target-preset", "kimi-official",
        ])
        with self.assertRaisesRegex(EvalConsoleError, "judge-preset"):
            _request_from_args(args, self.definition.eval_id, [self.definition.cases[0].case_id])

    def test_interactive_cancel_exits_normally_without_traceback(self) -> None:
        output = io.StringIO()
        with mock.patch("eval_console.cli.offer_bootstrap_setup"), mock.patch("builtins.input", side_effect=KeyboardInterrupt), mock.patch("sys.stdout", output):
            result = registry_interactive_console(
                self.definition.source_path.parent / "results",
                debug=False,
                registry_root=None,
                credential_store_path=None,
            )
        self.assertEqual(result, 0)
        self.assertIn("操作已取消", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_interactive_parser_accepts_allow_dirty_debug_and_defaults_to_false(self) -> None:
        clean = build_parser().parse_args(["interactive"])
        dirty = build_parser().parse_args(["interactive", "--allow-dirty-debug"])

        self.assertFalse(clean.allow_dirty_debug)
        self.assertTrue(dirty.allow_dirty_debug)

    def test_interactive_command_passes_dirty_debug_to_registry_console(self) -> None:
        args = build_parser().parse_args(["interactive", "--allow-dirty-debug"])
        with mock.patch.object(cli, "registry_interactive_console", return_value=0) as console:
            self.assertEqual(cli._command_interactive(args), 0)

        self.assertTrue(console.call_args.kwargs["allow_dirty_debug"])

    def test_dirty_debug_warning_is_explicit_and_absent_in_normal_console(self) -> None:
        dirty_output = io.StringIO()
        with mock.patch.object(cli, "_registry_interactive_loop", return_value=0), mock.patch("sys.stdout", dirty_output):
            self.assertEqual(
                registry_interactive_console(
                    self.definition.source_path.parent / "results",
                    debug=False,
                    allow_dirty_debug=True,
                    registry_root=None,
                    credential_store_path=None,
                ),
                0,
            )
        self.assertIn("Dirty Debug 模式已启用", dirty_output.getvalue())
        self.assertIn("不能作为正式 Reference", dirty_output.getvalue())

        clean_output = io.StringIO()
        with mock.patch.object(cli, "_registry_interactive_loop", return_value=0), mock.patch("sys.stdout", clean_output):
            registry_interactive_console(
                self.definition.source_path.parent / "results",
                debug=False,
                registry_root=None,
                credential_store_path=None,
            )
        self.assertNotIn("Dirty Debug 模式已启用", clean_output.getvalue())

    def test_interactive_homepage_shows_preset_count_without_internal_ids(self) -> None:
        output = io.StringIO()
        resolver = mock.Mock()
        resolver.registry.presets = {"preset_kimik26_internal": mock.Mock()}
        with (
            mock.patch.object(cli, "offer_bootstrap_setup"),
            mock.patch.object(cli, "discover_evals", return_value=[self.definition]),
            mock.patch.object(cli.RegistryRuntimeResolver, "for_project", return_value=resolver),
            mock.patch.object(cli, "_choose", return_value="exit"),
            mock.patch("sys.stdout", output),
        ):
            self.assertEqual(
                cli._registry_interactive_loop(
                    self.definition.source_path.parent / "results",
                    debug=False,
                    registry_root=None,
                    credential_store_path=None,
                ),
                0,
            )

        self.assertIn("可用 Preset：1 个", output.getvalue())
        self.assertNotIn("preset_kimik26_internal", output.getvalue())
