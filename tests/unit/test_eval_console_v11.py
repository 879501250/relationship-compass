"""Eval Console V1.1 UX, setup, secret, and shared test-runner checks."""

from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from eval_console.cli import (  # noqa: E402
    _git_workspace_state,
    _print_environment_summary,
    _print_run_summary,
    _request_from_args,
    build_interactive_request,
    build_parser,
)
from eval_console.configuration import (  # noqa: E402
    create_local_profile_config,
    create_profile,
    role_configuration,
    update_role_configuration,
)
from eval_console.discovery import discover_evals  # noqa: E402
from eval_console.models import EvalRunRequest, ProviderProfile  # noqa: E402
from eval_console.selection import CaseSelectionError, parse_case_selection  # noqa: E402
from eval_console.secrets import SecretResolver  # noqa: E402
from eval_console.test_runner import (  # noqa: E402
    DEFAULT_SUITE_TIMEOUTS,
    TerminalTestReporter,
    TestRunResult,
    TestSuiteRequest,
    TestSuiteResult,
    TestSuiteRunner,
    PROCESS_CLEANUP_GRACE_SECONDS,
    PROCESS_FINAL_DRAIN_SECONDS,
    _SubprocessOutcome,
    _run_process,
    _terminate_managed_processes,
)
from package_skill import build_zip, validate_package  # noqa: E402
import validate_skill  # noqa: E402


class FakeTestSuiteRunner(TestSuiteRunner):
    def __init__(self, outcomes: dict[str, TestSuiteResult]) -> None:
        self.outcomes = outcomes

    def _run_suite(self, key: str) -> TestSuiteResult:
        return self.outcomes[key]


def suite(key: str, *, failures: int = 0, errors: int = 0) -> TestSuiteResult:
    labels = {"unit": "单元测试", "integration": "集成测试", "contract": "Contract Eval"}
    return TestSuiteResult(key, labels[key], 4, failures, errors, 0.1, ("example failure",))


class TestRunnerTests(unittest.TestCase):
    def test_selects_unit_integration_contract_and_full_suites(self) -> None:
        fake = FakeTestSuiteRunner({key: suite(key) for key in ("unit", "integration", "contract")})
        for request, expected in (
            (TestSuiteRequest(unit=True, integration=False, contract=False), ("unit",)),
            (TestSuiteRequest(unit=False, integration=True, contract=False), ("integration",)),
            (TestSuiteRequest(unit=False, integration=False, contract=True), ("contract",)),
            (TestSuiteRequest(), ("unit", "integration", "contract")),
        ):
            with self.subTest(request=request):
                result = fake.run(request)
                self.assertEqual(tuple(item.key for item in result.suites), expected)
                self.assertTrue(result.passed)

    def test_reports_failed_suite_and_ci_friendly_events(self) -> None:
        fake = FakeTestSuiteRunner({"unit": suite("unit", failures=1), "integration": suite("integration"), "contract": suite("contract")})
        output = io.StringIO()
        reporter = TerminalTestReporter(output)
        result = fake.run(TestSuiteRequest(integration=False, contract=False), on_event=reporter.event)
        reporter.summary(result)
        self.assertFalse(result.passed)
        self.assertEqual(result.failures, 1)
        self.assertIn("[开始] 单元测试", output.getvalue())
        self.assertIn("[FAIL] 单元测试", output.getvalue())
        self.assertIn("STATUS: FAIL", output.getvalue())

    def test_native_discovery_runs_once_per_selected_suite(self) -> None:
        outcomes = [
            _SubprocessOutcome(0, "Ran 168 tests\n\nOK", False),
            _SubprocessOutcome(0, "Ran 14 tests\n\nOK", False),
            _SubprocessOutcome(0, "contract eval validation passed", False),
        ]
        with mock.patch("eval_console.test_runner._run_process", side_effect=outcomes) as run_process:
            result = TestSuiteRunner(ROOT).run(TestSuiteRequest())
        self.assertTrue(result.passed)
        self.assertEqual(run_process.call_count, 3)
        unit_command = run_process.call_args_list[0].args[0]
        integration_command = run_process.call_args_list[1].args[0]
        contract_command = run_process.call_args_list[2].args[0]
        self.assertIn("scripts/run_unittest_suite.py", unit_command)
        self.assertIn("tests/unit", unit_command)
        self.assertIn("--top-level-directory", unit_command)
        self.assertIn("scripts/run_unittest_suite.py", integration_command)
        self.assertIn("tests/integration", integration_command)
        self.assertEqual(contract_command[-1], "scripts/run_contract_evals.py")

    def test_timeout_stops_following_suites_and_reports_timeout(self) -> None:
        with mock.patch(
            "eval_console.test_runner._run_process",
            return_value=_SubprocessOutcome(
                1,
                "__RELATIONSHIP_COMPASS_TEST_ACTIVE__ tests.integration.test_knowledge_register.KnowledgeRegisterIntegrationTests.test_register_validate_status_list_and_duplicate_gate\n",
                True,
            ),
        ) as run_process:
            result = TestSuiteRunner(ROOT, suite_timeouts={"unit": 1}).run(TestSuiteRequest())
        self.assertEqual(run_process.call_count, 1)
        self.assertEqual(len(result.suites), 1)
        self.assertTrue(result.suites[0].timed_out)
        self.assertEqual(result.status, "ERROR")
        self.assertIn("test_knowledge_register", "\n".join(result.suites[0].details))

    def test_timeout_prefers_activity_file_and_cleans_its_temporary_directory(self) -> None:
        activity_paths: list[Path] = []

        def timed_out_process(command: list[str], *unused: object) -> _SubprocessOutcome:
            activity_file = Path(command[command.index("--activity-file") + 1])
            activity_file.write_text(
                json.dumps(
                    {
                        "suite": "unit",
                        "test_id": "tests.unit.test_hang.ActivityTests.test_hang",
                        "started_at": "2026-08-28T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            activity_paths.append(activity_file)
            return _SubprocessOutcome(
                1,
                "__RELATIONSHIP_COMPASS_TEST_ACTIVE__ stdout-fallback-test\n",
                True,
            )

        with mock.patch("eval_console.test_runner._run_process", side_effect=timed_out_process):
            result = TestSuiteRunner(ROOT, suite_timeouts={"unit": 1}).run(
                TestSuiteRequest(unit=True, integration=False, contract=False)
            )

        timed_out = result.suites[0]
        self.assertTrue(timed_out.timed_out)
        self.assertEqual(timed_out.last_active_test, "tests.unit.test_hang.ActivityTests.test_hang")
        self.assertIn("ActivityTests.test_hang", "\n".join(timed_out.details))
        self.assertEqual(len(activity_paths), 1)
        self.assertFalse(activity_paths[0].parent.exists())

    def test_unittest_wrapper_records_the_last_active_test_in_an_activity_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            test_root = Path(temp_dir)
            (test_root / "test_activity.py").write_text(
                "import unittest\n"
                "class ActivityTests(unittest.TestCase):\n"
                "    def test_fast(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            activity_file = test_root / "activity.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts" / "run_unittest_suite.py"),
                    "--start-directory",
                    str(test_root),
                    "--top-level-directory",
                    str(test_root),
                    "--activity-file",
                    str(activity_file),
                    "--suite",
                    "unit",
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            activity = json.loads(activity_file.read_text(encoding="utf-8"))
            self.assertEqual(activity["suite"], "unit")
            self.assertEqual(activity["test_id"], "test_activity.ActivityTests.test_fast")
            self.assertIn("started_at", activity)

    def test_process_timeout_terminates_the_child_process(self) -> None:
        process = mock.Mock()
        process.returncode = 1
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(["python"], 0.1),
            ("", ""),
        ]
        with mock.patch("eval_console.test_runner.subprocess.Popen", return_value=process), mock.patch(
            "eval_console.test_runner._terminate_managed_processes"
        ) as terminate:
            outcome = _run_process(["python", "-V"], ROOT, 0.1)
        self.assertTrue(outcome.timed_out)
        terminate.assert_called_once_with(process)
        self.assertEqual(
            process.communicate.call_args_list[1],
            mock.call(timeout=PROCESS_CLEANUP_GRACE_SECONDS),
        )

    def test_timeout_cleanup_is_bounded_when_detached_descendant_holds_pipe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture = ROOT / "tests" / "fixtures" / "process" / "detached_pipe_holder.py"
            pid_file = Path(temp_dir) / "descendant.json"
            ready_file = Path(temp_dir) / "ready.json"
            release_file = Path(temp_dir) / "release"
            result: dict[str, _SubprocessOutcome] = {}
            failures: list[BaseException] = []

            def run_fixture() -> None:
                try:
                    result["outcome"] = _run_process(
                        [
                            sys.executable,
                            "-B",
                            str(fixture),
                            "--pid-file",
                            str(pid_file),
                            "--ready-file",
                            str(ready_file),
                            "--release-file",
                            str(release_file),
                        ],
                        ROOT,
                        3.0,
                    )
                except BaseException as exc:  # pragma: no cover - assertion below reports setup failure.
                    failures.append(exc)

            worker = threading.Thread(target=run_fixture, daemon=True)
            worker.start()
            child_pid: int | None = None
            try:
                ready = self._wait_for_json(ready_file)
                child_pid = int(ready["child_pid"])
                self.assertEqual(json.loads(pid_file.read_text(encoding="utf-8")), ready)
                release_file.write_text("release", encoding="utf-8")
                worker.join(timeout=12)
                self.assertFalse(worker.is_alive(), "TEST FIXTURE SETUP FAILED: timeout harness did not return")
                self.assertFalse(failures, f"TEST FIXTURE SETUP FAILED: {failures!r}")
                outcome = result["outcome"]
                self.assertTrue(outcome.timed_out)
                self.assertTrue(outcome.cleanup_incomplete)
            finally:
                release_file.write_text("release", encoding="utf-8")
                child_pid = child_pid or self._recorded_child_pid(pid_file)
                if child_pid is not None:
                    self._stop_detached_process(child_pid)
                    self.assertFalse(self._process_is_alive(child_pid), "fixture child process still exists")
                worker.join(timeout=8)
                self.assertFalse(worker.is_alive(), "TEST FIXTURE SETUP FAILED: fixture parent did not exit")

    @staticmethod
    def _wait_for_json(path: Path, timeout_seconds: float = 5.0) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                time.sleep(0.02)
                continue
            if isinstance(payload, dict):
                return payload
        raise AssertionError(f"TEST FIXTURE SETUP FAILED: did not receive ready marker at {path}")

    @staticmethod
    def _recorded_child_pid(pid_file: Path) -> int | None:
        try:
            payload = json.loads(pid_file.read_text(encoding="utf-8"))
            return int(payload["child_pid"])
        except (KeyError, OSError, TypeError, ValueError):
            return None

    @staticmethod
    def _process_is_alive(process_id: int) -> bool:
        if os.name == "nt":
            probe = subprocess.run(
                ["tasklist", "/FI", f"PID eq {process_id}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                check=False,
                timeout=2,
            )
            return probe.returncode == 0 and f'"{process_id}"' in probe.stdout
        try:
            os.kill(process_id, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @classmethod
    def _stop_detached_process(cls, process_id: int) -> None:
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process_id), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            except subprocess.TimeoutExpired:
                pass
        else:
            try:
                os.kill(process_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 2.0
        while cls._process_is_alive(process_id) and time.monotonic() < deadline:
            time.sleep(0.02)

    def test_windows_cleanup_uses_taskkill_for_managed_processes(self) -> None:
        process = mock.Mock()
        process.poll.return_value = None
        process.pid = 1234
        taskkill_process = mock.Mock()
        taskkill_process.communicate.return_value = ("", "")
        with mock.patch("eval_console.test_runner.os.name", "nt"), mock.patch(
            "eval_console.test_runner.subprocess.Popen", return_value=taskkill_process
        ) as taskkill:
            _terminate_managed_processes(process)
        taskkill.assert_called_once_with(
            ["taskkill", "/PID", "1234", "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        taskkill_process.communicate.assert_called_once_with(timeout=PROCESS_CLEANUP_GRACE_SECONDS)


class ConfigurationAndSecretTests(unittest.TestCase):
    def test_local_config_is_created_once_without_changing_template(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            example = root / "example.yaml"
            example.write_text('{"profiles": {}}\n', encoding="utf-8")
            destination = root / "provider_profiles.local.yaml"
            self.assertTrue(create_local_profile_config(example, destination))
            self.assertEqual(destination.read_text(encoding="utf-8"), example.read_text(encoding="utf-8"))
            destination.write_text("user configuration", encoding="utf-8")
            self.assertFalse(create_local_profile_config(example, destination))
            self.assertEqual(destination.read_text(encoding="utf-8"), "user configuration")

    def test_profile_edit_and_secret_storage_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(temp_dir)
            profiles = root / "provider_profiles.local.yaml"
            profiles.write_text('{"version": 1, "profiles": {}}\n', encoding="utf-8")
            name = create_profile(
                profiles,
                name="local-target",
                provider="openai_compatible_chat",
                role="target",
                model="target-model",
                base_url="https://relay.example/v1",
            )
            update_role_configuration(
                profiles, name, "target", model="updated-model", base_url="https://other.example/v1"
            )
            details = role_configuration(profiles, name, "target")
            resolver = SecretResolver(root / ".env.local")
            resolver.save_local(str(details["api_key_env"]), "secret-value-that-must-not-leak")
            self.assertEqual(details["model"], "updated-model")
            self.assertEqual(details["base_url"], "https://other.example/v1")
            self.assertTrue(resolver.has(str(details["api_key_env"])))
            self.assertNotIn("secret-value-that-must-not-leak", profiles.read_text(encoding="utf-8"))
            self.assertIn(".env.*", (ROOT / ".gitignore").read_text(encoding="utf-8"))

    def test_session_secret_overrides_os_and_local_without_printing_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {"TEST_SECRET": "os-secret"}, clear=True):
            path = Path(temp_dir) / ".env.local"
            path.write_text("TEST_SECRET=local-secret\n", encoding="utf-8")
            resolver = SecretResolver(path)
            self.assertEqual(resolver.get("TEST_SECRET"), "os-secret")
            resolver.set_session("TEST_SECRET", "session-secret")
            self.assertEqual(resolver.get("TEST_SECRET"), "session-secret")
            self.assertEqual(resolver.has("TEST_SECRET"), True)

    def test_zip_excludes_local_secret_and_provider_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "source"
            root.mkdir()
            (root / "README.md").write_text("safe", encoding="utf-8")
            (root / ".env.local").write_text("SECRET=value", encoding="utf-8")
            config_dir = root / "model_evals"
            config_dir.mkdir()
            (config_dir / "provider_profiles.local.yaml").write_text("private", encoding="utf-8")
            references = root / "references"
            references.mkdir()
            (references / "中文说明.md").write_text("safe unicode", encoding="utf-8")
            work = root / ".work"
            work.mkdir()
            (work / "generated-local-output.txt").write_text("private", encoding="utf-8")
            idea = root / ".idea"
            idea.mkdir()
            (idea / "workspace.xml").write_text("local workspace state", encoding="utf-8")
            entries = build_zip(root, Path(temp_dir) / "archive.zip")
            self.assertEqual(entries, ["README.md", "references/中文说明.md"])
            self.assertEqual(validate_package(str(Path(temp_dir) / "archive.zip")), (2, 1))

    def test_package_validator_fails_closed_for_a_forbidden_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "unsafe.zip"
            for entry_name in (
                "model_evals/provider_profiles.local.yaml",
                ".idea/workspace.xml",
                ".work/generated-local-output.txt",
                "package/__pycache__/module.pyc",
            ):
                with self.subTest(entry_name=entry_name):
                    with zipfile.ZipFile(archive_path, "w") as archive:
                        archive.writestr(entry_name, "private")
                    with self.assertRaisesRegex(ValueError, "安全打包校验失败"):
                        validate_package(archive_path)


class ValidationRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        validate_skill.ERRORS.clear()

    def tearDown(self) -> None:
        validate_skill.ERRORS.clear()

    def test_validate_skill_uses_the_shared_test_suite_runner(self) -> None:
        result = TestRunResult(
            (
                suite("unit"),
                suite("integration"),
                suite("contract"),
            ),
            0.1,
        )
        output = io.StringIO()
        with mock.patch("validate_skill.TestSuiteRunner") as runner, mock.patch("sys.stdout", output):
            runner.return_value.run.return_value = result
            validate_skill.validate_automated_test_suites(False)
        runner.assert_called_once_with(validate_skill.ROOT)
        runner.return_value.run.assert_called_once_with(TestSuiteRequest())
        self.assertIn("unit tests: PASS", output.getvalue())
        self.assertIn("integration tests: PASS", output.getvalue())
        self.assertIn("contract eval: PASS", output.getvalue())
        self.assertEqual(validate_skill.ERRORS, [])

    def test_automated_test_entrypoints_share_runner_type_and_timeout_defaults(self) -> None:
        import run_tests

        self.assertIs(run_tests.TestSuiteRunner, TestSuiteRunner)
        self.assertIs(validate_skill.TestSuiteRunner, TestSuiteRunner)
        self.assertEqual(TestSuiteRunner(ROOT).suite_timeouts, DEFAULT_SUITE_TIMEOUTS)

    def test_validate_skill_reports_runner_timeout_with_activity_diagnostics(self) -> None:
        timeout = TestSuiteResult(
            "unit",
            "单元测试",
            0,
            0,
            1,
            1.0,
            ("测试超时：超过 1 秒仍未完成。",),
            timed_out=True,
            last_active_test="tests.unit.test_hang.ActivityTests.test_hang",
        )
        output = io.StringIO()
        with mock.patch("validate_skill.TestSuiteRunner") as runner, mock.patch("sys.stdout", output):
            runner.return_value.run.return_value = TestRunResult((timeout,), 1.0)
            validate_skill.validate_automated_test_suites(False)
        self.assertIn("unit tests: TIMEOUT", output.getvalue())
        self.assertIn("last active test: tests.unit.test_hang", output.getvalue())
        self.assertTrue(validate_skill.ERRORS)


class InteractiveRequestTests(unittest.TestCase):
    def test_run_wizard_builds_the_expected_dry_run_request(self) -> None:
        definition = discover_evals()[0]
        request = build_interactive_request(
            definition=definition,
            case_ids=[definition.cases[0].case_id, definition.cases[2].case_id, definition.cases[5].case_id],
            target_profile="target-profile",
            judge_profile="judge-profile",
            profiles_file=ROOT / "model_evals" / "provider_profiles.local.yaml",
            results_root=ROOT / "model_evals" / "results",
            dry_run=True,
            allow_dirty_debug=False,
            debug=False,
            concurrency=1,
            target_model_override="target-override",
            judge_model_override="judge-override",
            continue_on_error=True,
        )
        self.assertEqual(len(request.case_ids), 3)
        self.assertEqual(request.target_profile, "target-profile")
        self.assertEqual(request.judge_profile, "judge-profile")
        self.assertTrue(request.dry_run)
        self.assertEqual(request.concurrency, 1)
        self.assertTrue(request.continue_on_error)
        self.assertEqual(request.target_model_override, "target-override")

    def test_chinese_environment_summary_reports_role_and_credential_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(temp_dir)
            profiles_file = root / "profiles.json"
            profiles_file.write_text(
                json.dumps(
                    {
                        "profiles": {
                            "target": {"provider": "openai_responses", "api_key_env": "TARGET_KEY", "target": {"model": "target-model"}},
                            "judge": {"provider": "openai_responses", "api_key_env": "JUDGE_KEY", "judge": {"model": "judge-model"}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            profiles = [
                ProviderProfile("target", "openai_responses", "target-model", None, True, False),
                ProviderProfile("judge", "openai_responses", None, "judge-model", False, True),
            ]
            resolver = SecretResolver(root / ".env.local")
            resolver.set_session("TARGET_KEY", "session-only")
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                _print_environment_summary(discover_evals(), profiles, profiles_file, root / "results", resolver)
            rendered = output.getvalue()
            self.assertIn("评测控制台 V1.1", rendered)
            self.assertIn("Target：可用", rendered)
            self.assertIn("Judge：可用", rendered)
            self.assertIn("Target API 凭据", rendered)
            self.assertIn("Judge API 凭据", rendered)
            self.assertIn("[已配置] Target API 凭据", rendered)
            self.assertIn("[缺失] Judge API 凭据", rendered)
            self.assertIn("输出目录：可写", rendered)

    def test_environment_summary_does_not_claim_a_missing_target_profile_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles_file = root / "profiles.json"
            profiles_file.write_text(
                json.dumps({"profiles": {"judge": {"api_key_env": "JUDGE_KEY", "judge": {"model": "judge-model"}}}}),
                encoding="utf-8",
            )
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                _print_environment_summary(
                    discover_evals(),
                    [ProviderProfile("judge", "openai_responses", None, "judge-model", False, True)],
                    profiles_file,
                    root / "results",
                    SecretResolver(root / ".env.local"),
                )
            self.assertIn("[缺失] Target：未找到可用 Profile", output.getvalue())

    def test_non_git_directory_is_not_reported_as_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertIn("不是 Git 工作区", _git_workspace_state(Path(temp_dir)))

    def test_run_summary_prefers_target_model_override(self) -> None:
        definition = discover_evals()[0]
        with tempfile.TemporaryDirectory() as temp_dir, mock.patch.dict(os.environ, {}, clear=True):
            root = Path(temp_dir)
            profiles_file = root / "profiles.json"
            profiles_file.write_text(
                json.dumps(
                    {"profiles": {"shared": {"provider": "openai_responses", "api_key_env": "SHARED_KEY", "target": {"model": "profile-target"}, "judge": {"model": "profile-judge"}}}}
                ),
                encoding="utf-8",
            )
            request = EvalRunRequest(
                definition.eval_id, (definition.cases[0].case_id,), "shared", "shared", profiles_file, root / "results", dry_run=True,
                target_model_override="run-target-override",
            )
            profile = ProviderProfile("shared", "openai_responses", "profile-target", "profile-judge", True, True)
            output = io.StringIO()
            with mock.patch("sys.stdout", output):
                _print_run_summary(definition, request, profile, profile, SecretResolver(root / ".env.local"), target_model="run-target-override", judge_model=None)
            rendered = output.getvalue()
            self.assertIn("Target 模型：run-target-override", rendered)
            self.assertIn("预计 API 调用：0（Dry Run", rendered)
            self.assertIn("输出位置：", rendered)

    def test_chinese_input_errors_are_understandable(self) -> None:
        with self.assertRaisesRegex(CaseSelectionError, "起始编号不能大于结束编号"):
            parse_case_selection("4-2", ("RC-001", "RC-002", "RC-003", "RC-004"))

    def test_existing_non_interactive_cli_arguments_remain_supported(self) -> None:
        args = build_parser().parse_args(
            [
                "run",
                "model-evals-cases",
                "--cases",
                "1,3,5",
                "--target-profile",
                "target-profile",
                "--judge-profile",
                "judge-profile",
                "--concurrency",
                "1",
                "--dry-run",
            ]
        )
        definition = discover_evals()[0]
        request = _request_from_args(
            args,
            definition.eval_id,
            [definition.cases[0].case_id, definition.cases[2].case_id, definition.cases[4].case_id],
        )
        self.assertTrue(request.dry_run)
        self.assertEqual(request.concurrency, 1)
        self.assertTrue(request.continue_on_error)


if __name__ == "__main__":
    unittest.main()
