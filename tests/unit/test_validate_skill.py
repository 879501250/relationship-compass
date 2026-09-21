"""Validator CLI observability and exit-code contracts."""

from __future__ import annotations

import io
import sys
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import validate_skill  # noqa: E402


class _TTYStream(io.StringIO):
    def isatty(self) -> bool:
        return True


class ValidationReporterTests(unittest.TestCase):
    def setUp(self) -> None:
        validate_skill.ERRORS.clear()

    def tearDown(self) -> None:
        validate_skill.ERRORS.clear()

    def test_non_tty_phase_output_is_stable_and_includes_elapsed_status(self) -> None:
        output = io.StringIO()
        reporter = validate_skill.ValidationReporter(output)

        reporter.run(1, 2, "Repository convergence", lambda: None)
        reporter.run(2, 2, "Policy parity", lambda: validate_skill.ERRORS.append("expected failure"))

        rendered = output.getvalue()
        self.assertIn("[1/2] [开始] Repository convergence", rendered)
        self.assertIn("[1/2] [PASS] Repository convergence -", rendered)
        self.assertIn("[2/2] [FAIL] Policy parity -", rendered)
        self.assertIn("新增问题：1", rendered)
        self.assertIn("秒", rendered)
        self.assertNotIn("\r", rendered)

    def test_tty_spinner_stops_after_an_exception(self) -> None:
        output = _TTYStream()
        reporter = validate_skill.ValidationReporter(output, spinner_threshold_seconds=0.001)

        def interrupted() -> None:
            time.sleep(0.02)
            raise RuntimeError("boom")

        with self.assertRaisesRegex(RuntimeError, "boom"):
            reporter.run(1, 1, "Runtime boundaries", interrupted)

        self.assertIsNone(reporter._thread)
        self.assertIn("[1/1] [FAIL] Runtime boundaries", output.getvalue())


class ValidationCliTests(unittest.TestCase):
    def setUp(self) -> None:
        validate_skill.ERRORS.clear()

    def tearDown(self) -> None:
        validate_skill.ERRORS.clear()

    def _patch_validation_steps(self, stack: ExitStack) -> None:
        for name in (
            "validate_frontmatter",
            "validate_repository_convergence",
            "validate_skill_budget",
            "validate_inventory",
            "validate_routes_and_invariants",
            "validate_runtime_boundaries",
            "validate_decision_layer_ownership",
            "validate_curated_knowledge",
            "validate_chatgpt_pack",
            "validate_markdown_links",
            "validate_placeholders",
            "validate_upstream_lock",
            "validate_policy_parity",
            "validate_model_eval_definitions",
            "validate_model_eval_artifacts",
            "validate_automated_test_suites",
        ):
            stack.enter_context(mock.patch.object(validate_skill, name))

    def test_full_mode_immediately_reports_phases_and_summary(self) -> None:
        output = io.StringIO()
        with ExitStack() as stack:
            self._patch_validation_steps(stack)
            self.assertEqual(validate_skill.main([], stream=output), 0)

        rendered = output.getvalue()
        self.assertIn("[1/15] [开始] Frontmatter / Skill metadata", rendered)
        self.assertIn("[15/15] [PASS] Automated test suites", rendered)
        self.assertIn("验证汇总", rendered)
        self.assertIn("模式：full", rendered)
        self.assertIn("最终退出状态：0", rendered)

    def test_runtime_and_convergence_modes_only_report_their_own_mode(self) -> None:
        runtime_output = io.StringIO()
        convergence_output = io.StringIO()
        with ExitStack() as stack:
            self._patch_validation_steps(stack)
            self.assertEqual(validate_skill.main(["--runtime"], stream=runtime_output), 0)
            self.assertEqual(validate_skill.main(["--convergence-only"], stream=convergence_output), 0)

        self.assertIn("模式：runtime", runtime_output.getvalue())
        self.assertIn("[11/11] [PASS] Policy parity", runtime_output.getvalue())
        self.assertNotIn("Automated test suites", runtime_output.getvalue())
        self.assertIn("模式：convergence-only", convergence_output.getvalue())
        self.assertIn("[2/2] [PASS] Repository convergence", convergence_output.getvalue())
        self.assertNotIn("Automated test suites", convergence_output.getvalue())

    def test_validation_errors_and_unsupported_arguments_preserve_exit_codes(self) -> None:
        failure_output = io.StringIO()
        with mock.patch.object(validate_skill, "validate_frontmatter", side_effect=lambda: validate_skill.ERRORS.append("frontmatter failure")), mock.patch.object(validate_skill, "validate_repository_convergence"):
            self.assertEqual(validate_skill.main(["--convergence-only"], stream=failure_output), 1)

        self.assertIn("[1/2] [FAIL] Frontmatter / Skill metadata", failure_output.getvalue())
        self.assertIn("ERROR: frontmatter failure", failure_output.getvalue())
        unsupported_output = io.StringIO()
        self.assertEqual(validate_skill.main(["--unknown"], stream=unsupported_output), 2)
        self.assertIn("unsupported arguments", unsupported_output.getvalue())

    def test_keyboard_interrupt_closes_reporter_and_returns_without_traceback(self) -> None:
        output = io.StringIO()
        with mock.patch.object(validate_skill, "validate_frontmatter", side_effect=KeyboardInterrupt):
            self.assertEqual(validate_skill.main(["--convergence-only"], stream=output), 130)

        self.assertIn("验证已取消", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
