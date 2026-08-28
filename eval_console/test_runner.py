"""Shared, bounded runner for repository test suites."""

from __future__ import annotations

import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE_TIMEOUTS: dict[str, float] = {
    "unit": 120.0,
    "integration": 120.0,
    "contract": 120.0,
}
PROCESS_CLEANUP_GRACE_SECONDS = 3.0
PROCESS_FINAL_DRAIN_SECONDS = 1.0
SuiteEvent = Callable[[str, "TestSuiteResult | None", int, int], None]


@dataclass(frozen=True)
class TestSuiteRequest:
    """Choose one or more repository test suites without changing their semantics."""

    unit: bool = True
    integration: bool = True
    contract: bool = True

    def selected(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, enabled in (
                ("unit", self.unit),
                ("integration", self.integration),
                ("contract", self.contract),
            )
            if enabled
        )


@dataclass(frozen=True)
class TestSuiteResult:
    """One completed suite with precise counts from its native runner."""

    key: str
    label: str
    tests_run: int
    failures: int
    errors: int
    duration_seconds: float
    details: tuple[str, ...] = ()
    timed_out: bool = False
    last_active_test: str | None = None
    recent_output: str | None = None

    @property
    def passed(self) -> bool:
        return not self.timed_out and self.failures == 0 and self.errors == 0

    @property
    def status(self) -> str:
        if self.timed_out:
            return "TIMEOUT"
        if self.errors:
            return "ERROR"
        if self.failures:
            return "FAIL"
        return "PASS"


@dataclass(frozen=True)
class TestRunResult:
    """Aggregate outcome returned to the script and interactive Console alike."""

    suites: tuple[TestSuiteResult, ...]
    duration_seconds: float

    @property
    def passed(self) -> bool:
        return all(suite.passed for suite in self.suites)

    @property
    def failures(self) -> int:
        return sum(suite.failures for suite in self.suites)

    @property
    def errors(self) -> int:
        return sum(suite.errors for suite in self.suites)

    @property
    def status(self) -> str:
        if self.errors:
            return "ERROR"
        if self.failures:
            return "FAIL"
        return "PASS"


@dataclass(frozen=True)
class _SubprocessOutcome:
    returncode: int
    output: str
    timed_out: bool
    cleanup_incomplete: bool = False


class TestSuiteRunner:
    """Run native suites once each, with bounded subprocess lifecycles."""

    def __init__(
        self,
        root: Path = ROOT,
        *,
        suite_timeouts: Mapping[str, float] | None = None,
        contract_main: Callable[[], int] | None = None,
        contract_count: Callable[[], int] | None = None,
    ) -> None:
        self.root = root
        self.suite_timeouts = _validated_timeouts(suite_timeouts)
        self.contract_main = contract_main
        self.contract_count = contract_count

    def run(
        self, request: TestSuiteRequest, *, on_event: SuiteEvent | None = None
    ) -> TestRunResult:
        selected = request.selected()
        if not selected:
            raise ValueError("至少选择一个测试套件。")
        started = time.perf_counter()
        results: list[TestSuiteResult] = []
        for index, key in enumerate(selected, start=1):
            if on_event is not None:
                on_event("start", _suite_descriptor(key), index, len(selected))
            result = self._run_suite(key)
            results.append(result)
            if on_event is not None:
                on_event("complete", result, index, len(selected))
            if result.timed_out:
                break
        return TestRunResult(tuple(results), time.perf_counter() - started)

    def _run_suite(self, key: str) -> TestSuiteResult:
        if key == "contract":
            return self._run_contract()
        return self._run_unittest(key, _suite_label(key))

    def _run_unittest(self, directory: str, label: str) -> TestSuiteResult:
        command = [
            sys.executable,
            "-B",
            "scripts/run_unittest_suite.py",
            "--start-directory",
            f"tests/{directory}",
            "--pattern",
            "test_*.py",
            "--top-level-directory",
            ".",
        ]
        return self._run_command(directory, label, command, activity_suite=directory)

    def _run_contract(self) -> TestSuiteResult:
        if self.contract_main is not None:
            return self._run_injected_contract()
        scripts = str(self.root / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        from run_contract_evals import load_cases

        return self._run_command(
            "contract",
            _suite_label("contract"),
            [sys.executable, "-B", "scripts/run_contract_evals.py"],
            completed_test_count=len(load_cases()),
        )

    def _run_injected_contract(self) -> TestSuiteResult:
        """Keep a narrow in-process hook for deterministic unit tests only."""
        assert self.contract_main is not None
        started = time.perf_counter()
        output = io.StringIO()
        try:
            with _redirect_stdout(output):
                status = self.contract_main()
            count = self.contract_count() if self.contract_count is not None else 0
        except Exception as exc:
            return TestSuiteResult(
                "contract", _suite_label("contract"), 0, 0, 1,
                time.perf_counter() - started, (f"Contract Eval: {exc}",),
            )
        return TestSuiteResult(
            "contract",
            _suite_label("contract"),
            count,
            0 if status == 0 else 1,
            0,
            time.perf_counter() - started,
            () if status == 0 else _contract_details(output.getvalue()),
        )

    def _run_command(
        self,
        key: str,
        label: str,
        command: Sequence[str],
        *,
        completed_test_count: int | None = None,
        activity_suite: str | None = None,
    ) -> TestSuiteResult:
        started = time.perf_counter()
        timeout_seconds = self.suite_timeouts[key]
        last_active: str | None = None
        try:
            if activity_suite is None:
                outcome = _run_process(command, self.root, timeout_seconds)
            else:
                with tempfile.TemporaryDirectory(prefix="relationship-compass-test-") as directory:
                    activity_file = Path(directory) / "activity.json"
                    outcome = _run_process(
                        [
                            *command,
                            "--activity-file",
                            str(activity_file),
                            "--suite",
                            activity_suite,
                        ],
                        self.root,
                        timeout_seconds,
                    )
                    last_active = _read_last_active_test(activity_file)
        except OSError as exc:
            return TestSuiteResult(
                key, label, 0, 0, 1, time.perf_counter() - started,
                (f"无法启动测试进程：{exc}",),
            )
        duration = time.perf_counter() - started
        recent_output = _recent_output(outcome.output)
        last_active = last_active or _last_active_test(outcome.output)
        if outcome.timed_out:
            diagnostics = [
                f"测试超时：超过 {timeout_seconds:.0f} 秒仍未完成。",
                f"清理等待：最多 {PROCESS_CLEANUP_GRACE_SECONDS:.0f} 秒。",
                "可能原因：测试进程卡住、子进程未退出或 integration test deadlock。",
            ]
            if last_active:
                diagnostics.append(f"最后一个检测到的测试：{last_active}")
            if recent_output:
                diagnostics.append(f"最近输出：{recent_output}")
            if outcome.cleanup_incomplete:
                diagnostics.append("警告：部分子进程可能未正常响应终止请求，Test Runner 已停止等待并返回。")
            diagnostics.append("测试进程已发出终止请求；已停止后续测试。")
            return TestSuiteResult(
                key,
                label,
                _reported_test_count(outcome.output) or 0,
                0,
                1,
                duration,
                tuple(diagnostics),
                timed_out=True,
                last_active_test=last_active,
                recent_output=recent_output,
            )
        reported = _reported_native_result(outcome.output)
        tests_run = reported[0] if reported is not None else _reported_test_count(outcome.output) or completed_test_count or 0
        failures, errors = reported[1:] if reported is not None else _reported_failures_and_errors(outcome.output)
        if outcome.returncode and failures == 0 and errors == 0:
            errors = 1
        details = () if outcome.returncode == 0 else _subprocess_details(outcome.output)
        return TestSuiteResult(
            key,
            label,
            tests_run,
            failures,
            errors,
            duration,
            details,
            last_active_test=last_active,
            recent_output=recent_output,
        )


class TerminalTestReporter:
    """TTY spinner and CI-friendly Chinese renderer shared by both entrypoints."""

    def __init__(self, stream: io.TextIOBase | None = None) -> None:
        self.stream = stream or sys.__stdout__
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self._active: tuple[str, int, int, float] | None = None
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def event(self, kind: str, result: TestSuiteResult | None, index: int, total: int) -> None:
        if kind == "start":
            label = result.label if result is not None else "测试套件"
            with self._lock:
                self._active = (label, index, total, time.perf_counter())
            if self.tty:
                if self._thread is None:
                    self._thread = threading.Thread(target=self._spin, daemon=True)
                    self._thread.start()
                print(f"\n[{index}/{total}] {label}", file=self.stream, flush=True)
            else:
                print(f"[开始] {label}", file=self.stream, flush=True)
            return
        if result is None:
            return
        with self._lock:
            self._active = None
        passed = max(0, result.tests_run - result.failures - result.errors)
        marker = result.status
        if self.tty:
            print("\r" + " " * 100 + "\r", end="", file=self.stream)
            state = result.status
            print(
                f"{state} {result.label}\n{passed} / {result.tests_run} 通过\n耗时：{result.duration_seconds:.1f} 秒",
                file=self.stream,
                flush=True,
            )
        else:
            print(
                f"[{marker}] {result.label} - {passed}/{result.tests_run} - {result.duration_seconds:.1f} 秒",
                file=self.stream,
                flush=True,
            )
        if not result.passed:
            for detail in result.details:
                print(f"  {detail}", file=self.stream, flush=True)

    def summary(self, result: TestRunResult) -> None:
        self.close()
        print("\n测试汇总\n" + "-" * 36, file=self.stream, flush=True)
        for suite in result.suites:
            passed = max(0, suite.tests_run - suite.failures - suite.errors)
            marker = f"[{suite.status}]"
            print(f"{marker} {suite.label:<14} {passed}/{suite.tests_run}", file=self.stream, flush=True)
        print(f"失败：{result.failures}", file=self.stream, flush=True)
        print(f"错误：{result.errors}", file=self.stream, flush=True)
        print(f"总耗时：{result.duration_seconds:.1f} 秒", file=self.stream, flush=True)
        print(f"最终状态：{result.status}", file=self.stream, flush=True)
        print(f"STATUS: {result.status}", file=self.stream, flush=True)

    def close(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self.tty:
            print("\r" + " " * 100 + "\r", end="", file=self.stream)

    def _spin(self) -> None:
        frames = "|/-\\"
        frame = 0
        while not self._stopped.wait(0.12):
            with self._lock:
                active = self._active
            if active is None:
                continue
            _, _, _, started = active
            print(
                f"\r{frames[frame % len(frames)]} 正在运行... 已用时：{time.perf_counter() - started:.1f} 秒",
                end="",
                flush=True,
                file=self.stream,
            )
            frame += 1


def _run_process(command: Sequence[str], root: Path, timeout_seconds: float) -> _SubprocessOutcome:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    popen_kwargs: dict[str, object] = {
        "cwd": root,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": environment,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(list(command), **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return _SubprocessOutcome(process.returncode or 0, _join_output(stdout, stderr), False)
    except subprocess.TimeoutExpired as exc:
        _terminate_managed_processes(process)
        output_parts: list[object] = [exc.stdout, exc.stderr]
        cleanup_incomplete = False
        try:
            stdout, stderr = process.communicate(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
            output_parts.extend((stdout, stderr))
        except subprocess.TimeoutExpired as cleanup_exc:
            output_parts.extend((cleanup_exc.stdout, cleanup_exc.stderr))
            _force_terminate_managed_processes(process)
            try:
                stdout, stderr = process.communicate(timeout=PROCESS_FINAL_DRAIN_SECONDS)
                output_parts.extend((stdout, stderr))
            except subprocess.TimeoutExpired as final_exc:
                output_parts.extend((final_exc.stdout, final_exc.stderr))
                _close_process_pipes(process)
                cleanup_incomplete = True
        return _SubprocessOutcome(
            process.returncode or 1,
            _join_output(*output_parts),
            True,
            cleanup_incomplete,
        )
    except KeyboardInterrupt:
        _terminate_managed_processes(process)
        try:
            process.communicate(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _force_terminate_managed_processes(process)
            _close_process_pipes(process)
        raise


def _terminate_managed_processes(process: subprocess.Popen[str]) -> None:
    """Request cleanup for the managed process group or Windows process tree."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        _run_taskkill(process.pid)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _force_terminate_managed_processes(process: subprocess.Popen[str]) -> None:
    """Escalate managed-process cleanup without introducing an unbounded wait."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        _run_taskkill(process.pid)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.kill()
    except OSError:
        pass


def _run_taskkill(pid: int) -> None:
    """Ask Windows taskkill to terminate the managed process tree within a bound."""
    taskkill = subprocess.Popen(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        taskkill.communicate(timeout=PROCESS_CLEANUP_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        taskkill.kill()
        try:
            taskkill.communicate(timeout=PROCESS_FINAL_DRAIN_SECONDS)
        except subprocess.TimeoutExpired:
            _close_process_pipes(taskkill)


def _close_process_pipes(process: subprocess.Popen[str]) -> None:
    """Release pipe handles without waiting on a Windows reader thread lock."""
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is None:
            continue
        threading.Thread(target=_close_stream, args=(stream,), daemon=True).start()


def _close_stream(stream: io.IOBase) -> None:
    try:
        stream.close()
    except OSError:
        pass


def _validated_timeouts(overrides: Mapping[str, float] | None) -> dict[str, float]:
    resolved = dict(DEFAULT_SUITE_TIMEOUTS)
    for key, value in (overrides or {}).items():
        if key not in resolved:
            raise ValueError(f"未知测试套件超时配置：{key}")
        if value <= 0:
            raise ValueError(f"测试套件超时必须大于 0：{key}")
        resolved[key] = float(value)
    return resolved


def _suite_label(key: str) -> str:
    return {"unit": "单元测试", "integration": "集成测试", "contract": "Contract Eval"}[key]


def _suite_descriptor(key: str) -> TestSuiteResult:
    return TestSuiteResult(key, _suite_label(key), 0, 0, 0, 0.0)


def _reported_test_count(output: str) -> int | None:
    match = re.search(r"Ran (\d+) tests?", output)
    return int(match.group(1)) if match else None


def _last_active_test(output: str) -> str | None:
    matches = re.findall(r"__RELATIONSHIP_COMPASS_TEST_ACTIVE__\s+(.+)", output)
    return matches[-1].strip() if matches else None


def _read_last_active_test(activity_file: Path) -> str | None:
    """Read the final activity marker without relying on suite stdout/stderr pipes."""
    try:
        payload = json.loads(activity_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    test_id = payload.get("test_id") if isinstance(payload, dict) else None
    return test_id.strip() if isinstance(test_id, str) and test_id.strip() else None


def _recent_output(output: str) -> str | None:
    lines = [
        line.strip()
        for line in output.splitlines()
        if line.strip() and not line.startswith("__RELATIONSHIP_COMPASS_TEST_ACTIVE__")
    ]
    return lines[-1][:240] if lines else None


def _reported_native_result(output: str) -> tuple[int, int, int] | None:
    match = re.search(
        r"__RELATIONSHIP_COMPASS_TEST_RESULT__ tests_run=(\d+) failures=(\d+) errors=(\d+)",
        output,
    )
    if match is None:
        return None
    return tuple(int(value) for value in match.groups())


def _reported_failures_and_errors(output: str) -> tuple[int, int]:
    summary = re.search(r"FAILED \(([^)]*)\)", output)
    if summary is None:
        return 0, 0
    values = summary.group(1)
    return _summary_count(values, "failures?"), _summary_count(values, "errors?")


def _summary_count(summary: str, name: str) -> int:
    match = re.search(rf"{name}=(\d+)", summary)
    return int(match.group(1)) if match else 0


def _subprocess_details(output: str) -> tuple[str, ...]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    interesting = [
        line
        for line in lines
        if line.startswith(("FAIL:", "ERROR:", "AssertionError", "Exception", "ERROR"))
    ]
    return tuple(interesting[:5] or lines[-5:] or ["测试进程失败，但未返回详细信息。"])


def _contract_details(output: str) -> tuple[str, ...]:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return tuple(lines[-3:] or ["Contract Eval 失败。"])


def _join_output(*parts: object) -> str:
    values = [
        part.decode("utf-8", "replace") if isinstance(part, bytes) else str(part)
        for part in parts
        if part
    ]
    return "\n".join(values)


class _redirect_stdout:
    """Tiny local replacement that keeps this module dependency-free."""

    def __init__(self, target: io.StringIO) -> None:
        self.target = target
        self.original: io.TextIOBase | None = None

    def __enter__(self) -> io.StringIO:
        self.original = sys.stdout
        sys.stdout = self.target
        return self.target

    def __exit__(self, *unused: object) -> None:
        assert self.original is not None
        sys.stdout = self.original
