"""V1.3C.1 Console cutover and non-network safety regressions."""

from __future__ import annotations

import io
import unittest
from unittest import mock

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
