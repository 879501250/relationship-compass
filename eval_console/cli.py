"""评测控制台的中文交互入口与兼容命令行入口。"""

from __future__ import annotations

import argparse
import getpass
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, TextIO, TypeVar

from .discovery import (
    HistoricalRun,
    discover_evals,
    discover_provider_profiles,
    discover_runs,
    find_eval,
    run_case_outcomes,
)
from .configuration import (
    create_local_profile_config,
    create_profile,
    profile_api_key_env,
    role_configuration,
    update_role_configuration,
    validate_base_url,
)
from .models import (
    CURRENT_CONSOLE_SCHEMA_VERSION,
    EVAL_CONSOLE_VERSION,
    EvalDefinition,
    EvalExecutionMode,
    EvalRunRequest,
    JudgeCaseSelector,
    ProviderProfile,
)
from .runner_adapter import runner
from .secrets import SecretResolver
from .selection import CaseSelectionError, parse_case_selection
from .service import (
    EvalConsoleError,
    EvaluationInterrupted,
    execute_request,
    judge_only_case_ids,
    plan_stage_execution,
    preflight_request,
    validate_configuration,
)
from .test_runner import TerminalTestReporter, TestSuiteRequest, TestSuiteRunner


T = TypeVar("T")


class InteractiveInputClosed(Exception):
    """Raised when an interactive input stream has reached EOF."""


class InteractiveInputCancelled(Exception):
    """Raised when Ctrl+C interrupts an interactive menu prompt."""


@dataclass
class _RoleConfigurationDraft:
    """In-memory changes collected by one Provider configuration workflow."""

    model: str | None = None
    base_url: str | None = None
    secret_configuration: tuple[str, str] | None = None

    @property
    def has_changes(self) -> bool:
        return (
            self.model is not None
            or self.base_url is not None
            or self.secret_configuration is not None
        )


@dataclass(frozen=True)
class _ActivityState:
    """One in-flight Target or Judge request shown by the Console."""

    phase: str
    case_id: str
    ordinal: int
    total: int
    started_at: float


@dataclass(frozen=True)
class _ActivitySnapshot:
    """A consistent, renderer-safe view of one execution stage."""

    phase: str | None
    total: int
    completed: int
    running: int
    pending: int
    concurrency: int
    active_case_ids: tuple[str, ...]


class _ActivityReporter:
    """Render API wait state without polluting the persisted JSONL event log."""

    def __init__(self, target_concurrency: int = 1, judge_concurrency: int = 1) -> None:
        self._stream = sys.stdout
        self._tty = bool(getattr(self._stream, "isatty", lambda: False)())
        self._lock = threading.Lock()
        self._active: dict[tuple[str, str], _ActivityState] = {}
        self._phase: str | None = None
        self._total = 0
        self._completed = 0
        self._concurrency = {
            "TARGET": max(1, target_concurrency),
            "JUDGE": max(1, judge_concurrency),
        }
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, phase: str, record: dict[str, object], started: int, total: int) -> None:
        case_id = str(record.get("case_id") or "未知 Case")
        with self._lock:
            if self._phase != phase:
                # The service has a stage barrier. Clearing here makes that
                # boundary explicit and prevents stale Target activity from
                # being rendered as Judge activity if a callback is delayed.
                self._phase = phase
                self._active.clear()
                self._completed = 0
            self._total = total
            self._active[(phase, case_id)] = _ActivityState(
                phase, case_id, started, total, time.monotonic()
            )
            snapshot = self._snapshot_locked()
        if not self._tty:
            print(
                f"  [开始] {case_id} {_phase_label(phase)}"
                f"（运行中 {snapshot.running}/{snapshot.total}，等待 {snapshot.pending}）"
            )
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()

    def finish(self, phase: str, record: dict[str, object], completed: int, total: int) -> None:
        case_id = str(record.get("case_id") or "unknown case")
        with self._lock:
            active = self._active.pop((phase, case_id), None)
            if self._phase == phase:
                self._completed = max(self._completed, completed)
                self._total = total
            snapshot = self._snapshot_locked()
        duration = _duration_seconds(record)
        if duration is None and active is not None:
            duration = max(0.0, time.monotonic() - active.started_at)
        label = _result_label(phase, record)
        elapsed = f"{duration:.1f} 秒" if duration is not None else "耗时未知"
        if self._tty:
            print("\r" + " " * 100 + "\r", end="")
            print(f"  [{label}] {_phase_label(phase)} {completed}/{total}: {case_id}（{elapsed}）")
        else:
            print(
                f"  [完成] {case_id} {_phase_label(phase)} {elapsed} - {label}"
                f"（已完成 {snapshot.completed}/{snapshot.total}，运行中 {snapshot.running}，"
                f"等待 {snapshot.pending}）"
            )

    def rate_limit(self, phase: str, delay: float, extended: bool) -> None:
        """Report one 429 state change without adding a polling log stream."""
        if self._stopped.is_set():
            return
        snapshot = self.snapshot()
        action = "已延长" if extended else "已开始"
        message = (
            f"[RATE LIMIT] {_phase_label(phase)} Provider 返回 HTTP 429；"
            f"共享 cooldown {action}：{delay:.1f} 秒。"
        )
        detail = (
            f"暂停安排新的 {_phase_label(phase)} Case"
            f"（运行中 {snapshot.running}，等待 {snapshot.pending}）。"
        )
        if self._tty:
            print("\r" + " " * 100 + "\r", end="")
        print(f"  {message}\n  {detail}")

    def snapshot(self) -> _ActivitySnapshot:
        """Return a locked snapshot for deterministic tests and interrupt UI."""
        with self._lock:
            return self._snapshot_locked()

    def active_case_ids(self) -> tuple[str, ...]:
        return self.snapshot().active_case_ids

    def close(self) -> None:
        self._stopped.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        if self._tty:
            print("\r" + " " * 100 + "\r", end="")

    def _spin(self) -> None:
        frames = "|/-\\"
        frame = 0
        while not self._stopped.wait(0.12):
            with self._lock:
                snapshot = self._snapshot_locked()
                active = tuple(self._active.values())
            if not active or snapshot.phase is None:
                continue
            phase = snapshot.phase
            active_lines = " | ".join(
                f"{item.case_id} {time.monotonic() - item.started_at:.1f}s"
                for item in active
                if item.phase == phase
            )
            print(
                f"\r  [{frames[frame % len(frames)]}] {_phase_label(phase)} 已完成 "
                f"{snapshot.completed}/{snapshot.total}，运行中 {snapshot.running}，"
                f"等待 {snapshot.pending}，并发 {snapshot.concurrency}：{active_lines}",
                end="",
                flush=True,
            )
            frame += 1

    def _snapshot_locked(self) -> _ActivitySnapshot:
        phase = self._phase
        active = tuple(
            state for state in self._active.values() if state.phase == phase
        )
        running = len(active)
        return _ActivitySnapshot(
            phase=phase,
            total=self._total,
            completed=self._completed,
            running=running,
            pending=max(0, self._total - self._completed - running),
            concurrency=self._concurrency.get(phase or "", 1),
            active_case_ids=tuple(state.case_id for state in active),
        )


class _GracefulStop:
    """Treat the first Ctrl+C as a durable stop request and a second as force stop."""

    def __init__(
        self,
        target_concurrency: int,
        judge_concurrency: int,
        *,
        stream: TextIO | None = None,
        active_cases: Callable[[], tuple[str, ...]] | None = None,
    ) -> None:
        self.requested = False
        self.force_requested = False
        self.concurrency = max(target_concurrency, judge_concurrency)
        self._stream = stream or sys.stderr
        self._active_cases = active_cases
        self._stage: str | None = None
        self._previous: object | None = None

    def __enter__(self) -> "_GracefulStop":
        self._previous = signal.getsignal(signal.SIGINT)

        def on_interrupt(_signum: int, _frame: object) -> None:
            self.handle_interrupt()

        signal.signal(signal.SIGINT, on_interrupt)
        return self

    def set_stage(self, stage: str) -> None:
        self._stage = stage if stage in {"TARGET", "JUDGE"} else None

    def handle_interrupt(self) -> None:
        """Handle the lightweight signal transition without touching artifacts."""
        stage = {"TARGET": "Target", "JUDGE": "Judge"}.get(self._stage, "当前")
        if self.requested:
            self.force_requested = True
            print(
                f"\n再次收到 Ctrl+C，正在强制终止当前 {stage} 请求……",
                file=self._stream,
                flush=True,
            )
            raise KeyboardInterrupt
        self.requested = True
        message = (
            f"\n已收到停止请求：当前 {stage} 请求完成后停止并保存进度。"
            "再次按 Ctrl+C 可强制退出。"
        )
        if self.concurrency > 1:
            message += " 已请求停止；不会继续安排新的工作。已开始的请求可能仍会完成。"
        active_case_ids = self._active_cases() if self._active_cases is not None else ()
        if active_case_ids:
            message += "\n当前仍在运行：\n" + "\n".join(
                f"  {case_id}" for case_id in active_case_ids
            )
            message += f"\n这 {len(active_case_ids)} 个请求完成后将保存进度并中断。"
        print(message, file=self._stream, flush=True)

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        if self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)


def build_parser() -> argparse.ArgumentParser:
    """Build the small command surface; invoking no command opens the wizard."""
    parser = argparse.ArgumentParser(
        description=f"Relationship Compass 评测控制台 V{EVAL_CONSOLE_VERSION}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="不打开向导，直接运行选定的 Case")
    _add_run_arguments(run)
    run.set_defaults(func=_command_run)

    resume = subparsers.add_parser("resume", help="按当前 Case/Stage 状态继续或重试历史运行")
    resume.add_argument("--from-run", required=True, type=Path)
    _add_run_arguments(resume, include_eval=False, include_execution_mode=False)
    resume.set_defaults(
        func=_command_resume,
        target_concurrency=None,
        judge_concurrency=None,
    )

    validate = subparsers.add_parser("validate", help="检查 Eval、Provider 配置和输出目录")
    validate.add_argument("--profiles-file", type=Path, default=runner.DEFAULT_PROVIDER_PROFILES)
    validate.add_argument("--results-root", type=Path, default=runner.RESULTS_BASE)
    validate.add_argument("--target-profile")
    validate.add_argument("--judge-profile")
    validate.add_argument("--debug", action="store_true")
    validate.set_defaults(func=_command_validate)

    history = subparsers.add_parser("history", help="查看近期评测结果")
    history.add_argument("--results-root", type=Path, default=runner.RESULTS_BASE)
    history.add_argument("--limit", type=int, default=10)
    history.set_defaults(func=_command_history)

    interactive = subparsers.add_parser("interactive", help="打开中文交互式控制台")
    interactive.add_argument("--profiles-file", type=Path, default=runner.DEFAULT_PROVIDER_PROFILES)
    interactive.add_argument("--results-root", type=Path, default=runner.RESULTS_BASE)
    interactive.add_argument("--debug", action="store_true")
    interactive.set_defaults(func=_command_interactive)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run a CLI command, showing tracebacks only when explicitly requested."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        args = parser.parse_args(["interactive"])
    try:
        return int(args.func(args))
    except EvaluationInterrupted as exc:
        print("\n评测已中断。")
        print(f"已保存部分结果：{exc.run_dir}")
        print(
            f"当前阶段：{_phase_label(exc.stage)}；已保存 {exc.completed_cases}/{exc.total_cases}；"
            f"剩余 {max(0, exc.total_cases - exc.completed_cases)}。"
        )
        print("打开评测控制台并选择“继续 / 重试历史运行”，或使用 resume --from-run 继续。")
        return 130
    except (CaseSelectionError, EvalConsoleError, OSError, ValueError) as exc:
        print(f"\n无法继续：{exc}")
        if getattr(args, "debug", False):
            traceback.print_exc()
        return 1
    except KeyboardInterrupt:
        print("\n评测开始前已取消。")
        return 130


def _add_run_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_eval: bool = True,
    include_cases: bool = True,
    include_execution_mode: bool = True,
) -> None:
    if include_eval:
        parser.add_argument("eval_id", help="可通过 'python -m eval_console interactive' 查看的 Eval ID")
    if include_cases:
        cases = parser.add_mutually_exclusive_group()
        cases.add_argument("--case", action="append", help="一个 Case ID 或从 1 开始的位置；可重复")
        cases.add_argument("--cases", help="all、位置、ID、范围或组合，例如 1,3,5-8")
    if include_execution_mode:
        parser.add_argument(
            "--mode",
            dest="execution_mode",
            choices=("full", "target-only", "judge-only"),
            default="full",
            help="执行 FULL、仅 Target 或仅 Judge",
        )
        parser.add_argument("--source-run", type=Path, help="Judge-only / Resume 的历史运行目录")
        parser.add_argument(
            "--judge-selector",
            choices=("all-target", "judge-error", "judge-missing", "judge-error-or-missing", "selected"),
            default="selected",
            help="Judge-only 时从历史 Target 成功结果中选择 Case",
        )
    parser.add_argument("--profile", help="Target 与 Judge 使用同一个已配置 Profile")
    parser.add_argument("--target-profile")
    parser.add_argument("--judge-profile")
    parser.add_argument("--profiles-file", type=Path, default=runner.DEFAULT_PROVIDER_PROFILES)
    parser.add_argument("--results-root", type=Path, default=runner.RESULTS_BASE)
    parser.add_argument("--run-id")
    parser.add_argument("--target-model", help="仅本次运行覆盖 Target 模型")
    parser.add_argument("--judge-model", help="仅本次运行覆盖 Judge 模型")
    parser.add_argument("--dry-run", action="store_true", help="检查并显示计划，不调用真实 API")
    parser.add_argument("--allow-dirty-debug", action="store_true")
    parser.add_argument("--target-concurrency", type=int, default=1)
    parser.add_argument("--judge-concurrency", type=int, default=1)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--debug", action="store_true")


def _command_run(args: argparse.Namespace) -> int:
    definition = find_eval(args.eval_id)
    mode = _execution_mode_from_args(args)
    if (
        mode in {EvalExecutionMode.JUDGE_ONLY, EvalExecutionMode.RESUME}
        and args.source_run
        and not args.case
        and not args.cases
    ):
        source_metadata = runner.load_json_object(args.source_run.expanduser().resolve() / "run.json")
        case_ids = [
            record["case_id"] for record in source_metadata.get("cases", [])
            if isinstance(record, dict) and isinstance(record.get("case_id"), str)
        ]
    else:
        case_ids = _case_ids_from_args(args, definition)
    request = _request_from_args(args, definition.eval_id, case_ids)
    return _execute_and_print(request)


def _command_resume(args: argparse.Namespace) -> int:
    run_dir = args.from_run.expanduser().resolve()
    metadata = runner.load_json_object(run_dir / "run.json")
    _validate_resume_artifact(metadata)
    console = metadata.get("console") if isinstance(metadata.get("console"), dict) else {}
    eval_id = console.get("eval_id")
    if not isinstance(eval_id, str):
        raise EvalConsoleError("当前 schema 的历史 Run 缺少 eval_id，无法 Resume。")
    definition = find_eval(eval_id)
    if args.cases or args.case:
        case_ids = _case_ids_from_args(args, definition)
    else:
        case_ids = [
            record["case_id"] for record in metadata.get("cases", [])
            if isinstance(record, dict) and isinstance(record.get("case_id"), str)
        ]
    args.source_run = run_dir
    args.execution_mode = "resume"
    default_target, default_judge = _resume_concurrency_defaults(metadata)
    if args.target_concurrency is None:
        args.target_concurrency = default_target
    if args.judge_concurrency is None:
        args.judge_concurrency = default_judge
    request = _request_from_args(args, eval_id, list(case_ids))
    request = _resume_request_with_inherited_configuration(request, metadata)
    return _execute_and_print(request)


def _validate_resume_artifact(metadata: dict[str, object]) -> None:
    console = metadata.get("console")
    if metadata.get("schema_version") != 3 or not isinstance(console, dict):
        raise EvalConsoleError("Unsupported Run Artifact Version：仅支持当前 Console Run。")
    if console.get("schema_version") != CURRENT_CONSOLE_SCHEMA_VERSION:
        raise EvalConsoleError("Unsupported Run Artifact Version：Console artifact 版本不受支持。")
    if console.get("origin_mode") != metadata.get("origin_mode"):
        raise EvalConsoleError("Unsupported Run Artifact Version：origin_mode 不一致。")
    if "concurrency" in console:
        raise EvalConsoleError("Unsupported Run Artifact Version：不支持旧并发配置。")


def _resume_request_with_inherited_configuration(
    request: EvalRunRequest, metadata: dict[str, object]
) -> EvalRunRequest:
    """Use the persisted Console provider identities for same-Run Resume only."""
    _validate_resume_artifact(metadata)
    console = metadata["console"]
    assert isinstance(console, dict)
    expected = {
        "Target": (
            console.get("target_profile"),
            console.get("target_model"),
            request.target_profile,
            request.target_model_override,
        ),
        "Judge": (
            console.get("judge_profile"),
            console.get("judge_model"),
            request.judge_profile,
            request.judge_model_override,
        ),
    }
    inherited_profiles: dict[str, str | None] = {}
    inherited_models: dict[str, str | None] = {}
    for role, (profile, model, supplied_profile, supplied_model) in expected.items():
        saved_profile = profile if isinstance(profile, str) else None
        saved_model = model if isinstance(model, str) else None
        if supplied_profile is not None and supplied_profile != saved_profile:
            raise EvalConsoleError(
                f"Resume configuration mismatch: 原 {role} Profile：{saved_profile or '未记录'}；"
                f"当前指定：{supplied_profile}。Resume 必须继续使用原运行配置；"
                "如需使用新的 Judge，请使用 JUDGE_ONLY。"
            )
        if supplied_model is not None and supplied_model != saved_model:
            raise EvalConsoleError(
                f"Resume configuration mismatch: 原 {role} Model：{saved_model or '未记录'}；"
                f"当前指定：{supplied_model}。Resume 必须继续使用原运行配置；"
                "如需使用新的 Judge，请使用 JUDGE_ONLY。"
            )
        inherited_profiles[role] = saved_profile
        inherited_models[role] = saved_model
    return replace(
        request,
        target_profile=inherited_profiles["Target"],
        judge_profile=inherited_profiles["Judge"],
        resume_target_model=inherited_models["Target"],
        resume_judge_model=inherited_models["Judge"],
    )


def _saved_concurrency(console: dict[str, object], field: str) -> int:
    value = console.get(field)
    if not isinstance(value, int) or value < 1 or value > 32:
        raise EvalConsoleError(
            "Unsupported Run Artifact Version：当前 Console Run 缺少有效并发配置。"
        )
    return value


def _resume_concurrency_defaults(metadata: dict[str, object]) -> tuple[int, int]:
    """Prefer the most recent execution strategy while preserving initial provenance."""
    console = metadata.get("console")
    if not isinstance(console, dict):
        raise EvalConsoleError("Unsupported Run Artifact Version：当前 Console Run 缺少并发配置。")
    initial_target = _saved_concurrency(console, "target_concurrency")
    initial_judge = _saved_concurrency(console, "judge_concurrency")
    history = metadata.get("execution_history")
    if not isinstance(history, list):
        return initial_target, initial_judge
    return (
        _latest_stage_concurrency(history, "target", initial_target),
        _latest_stage_concurrency(history, "judge", initial_judge),
    )


def _latest_stage_concurrency(
    history: list[object], stage: str, fallback: int
) -> int:
    field = f"{stage}_concurrency"
    for require_executed_stage in (True, False):
        for execution in reversed(history):
            if not isinstance(execution, dict):
                continue
            value = execution.get(field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= 32
            ):
                continue
            actual = execution.get("actual_api_calls")
            calls = actual.get(stage) if isinstance(actual, dict) else None
            ran_stage = isinstance(calls, int) and not isinstance(calls, bool) and calls > 0
            if not require_executed_stage or ran_stage:
                return value
    return fallback


def _command_validate(args: argparse.Namespace) -> int:
    report = validate_configuration(
        args.profiles_file.expanduser().resolve(), args.results_root.expanduser().resolve()
    )
    print("\n运行环境检查")
    for check in report.checks:
        print(f"  [通过] {check}")
    for warning in report.warnings:
        print(f"  [警告] {warning}")
    for error in report.errors:
        print(f"  [错误] {error}")
    resolver = SecretResolver(runner.ROOT / ".env.local")
    resolver.prepare_environment()
    _print_provider_configuration(args.profiles_file.expanduser().resolve(), resolver)
    if args.target_profile or args.judge_profile:
        if not args.target_profile or not args.judge_profile:
            raise EvalConsoleError("Provider 预检查需要同时提供 --target-profile 和 --judge-profile。")
        definition = discover_evals()[0]
        request = EvalRunRequest(
            eval_id=definition.eval_id,
            case_ids=(definition.cases[0].case_id,),
            target_profile=args.target_profile,
            judge_profile=args.judge_profile,
            profiles_file=args.profiles_file.expanduser().resolve(),
            results_root=args.results_root.expanduser().resolve(),
            dry_run=True,
            debug=args.debug,
        )
        _, _, target_plan, judge_plan = preflight_request(request)
        print("  [通过] Target Provider 预检查")
        print(f"    Target 模型：{target_plan['requested_model']}")
        print("  [通过] Judge Provider 预检查")
        print(f"    Judge 模型：{judge_plan['requested_model']}")
    return 1 if report.errors else 0


def _command_history(args: argparse.Namespace) -> int:
    _print_history(discover_runs(args.results_root.expanduser().resolve())[: max(args.limit, 0)])
    return 0


def _command_interactive(args: argparse.Namespace) -> int:
    return interactive_console(
        args.profiles_file.expanduser().resolve(),
        args.results_root.expanduser().resolve(),
        debug=args.debug,
    )


def interactive_console(profiles_file: Path, results_root: Path, *, debug: bool = False) -> int:
    """Run the non-developer interactive menu using only standard-library prompts."""
    while True:
        try:
            evals = discover_evals()
            profiles = discover_provider_profiles(profiles_file)
            resolver = SecretResolver(runner.ROOT / ".env.local")
            resolver.prepare_environment()
            _print_environment_summary(evals, profiles, profiles_file, results_root, resolver)
            if _setup_required(profiles_file, profiles, resolver) and _yes_no(
                "检测到首次配置尚未完成。现在开始配置吗？", default=True
            ):
                _setup_wizard(profiles_file, results_root, resolver)
                continue
            options: list[tuple[str, Callable[[], int | None]]] = [
                ("运行行为评测", lambda: _interactive_run(evals, profiles_file, results_root, debug, resolver)),
                ("运行自动化测试", lambda: _interactive_tests()),
                ("检查运行环境", lambda: _interactive_validate(profiles_file, results_root, debug)),
                ("配置 Provider", lambda: _configure_providers(profiles_file, resolver)),
                ("查看历史运行", lambda: _print_history(discover_runs(results_root))),
                ("继续 / 重试历史运行", lambda: _interactive_history_stage(
                    evals, profiles_file, results_root, debug, resolver, EvalExecutionMode.RESUME
                )),
                (
                    "查看 Eval 列表",
                    lambda: _print_evals(evals),
                ),
                ("退出", lambda: 0),
            ]
            choice = _choose(
                "请选择操作", [(label, (label, action)) for label, action in options]
            )
        except InteractiveInputClosed:
            print("\n检测到输入流已关闭，Eval Console 已安全退出。")
            return 0
        except InteractiveInputCancelled:
            print("\n已取消操作，Eval Console 已退出。")
            return 0
        if choice[0] == "退出":
            return 0
        try:
            result = choice[1]()
        except InteractiveInputClosed:
            print("\n检测到输入流已关闭，Eval Console 已安全退出。")
            return 0
        except InteractiveInputCancelled:
            print("\n操作已取消，正在返回主菜单。")
            continue
        if result not in (None, 0):
            print("\n操作未完成。你可以调整选择后重试。")


def _print_environment_summary(
    evals: list[EvalDefinition],
    profiles: list[ProviderProfile],
    profiles_file: Path,
    results_root: Path,
    resolver: SecretResolver,
) -> None:
    git_state = _git_workspace_state(runner.ROOT)
    output_state = _output_directory_state(results_root)
    targets = [profile for profile in profiles if profile.supports_target]
    judges = [profile for profile in profiles if profile.supports_judge]
    target_credential = _role_credential_available(profiles_file, targets, "target", resolver)
    judge_credential = _role_credential_available(profiles_file, judges, "judge", resolver)
    print(f"\nRelationship Compass\n评测控制台 V{EVAL_CONSOLE_VERSION}\n" + "-" * 40)
    print("环境状态")
    print(f"  {git_state}")
    print(f"  [已检测] Eval：{len(evals)} 个")
    print(f"  [已检测] Cases：{sum(len(item.cases) for item in evals)} 个")
    print(f"  {'[已配置]' if profiles_file.is_file() else '[缺失]'} 本地 Provider 配置")
    print(f"  {'[可用]' if targets else '[缺失]'} Target：{'可用' if targets else '未找到可用 Profile'}")
    print(f"  {'[可用]' if judges else '[缺失]'} Judge：{'可用' if judges else '未找到可用 Profile'}")
    print(f"  {'[已配置]' if target_credential else '[缺失]'} Target API 凭据")
    print(f"  {'[已配置]' if judge_credential else '[缺失]'} Judge API 凭据")
    print(f"  {output_state}")


def _git_workspace_state(root: Path) -> str:
    if not (root / ".git").exists():
        return "[警告] 当前目录不是 Git 工作区（不会阻止 Dry Run）"
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
    except OSError:
        return "[警告] Git 工作区状态无法读取"
    if completed.returncode != 0:
        return "[警告] Git 工作区状态无法读取"
    return "[已检测] Git 工作区：Clean" if not completed.stdout.strip() else "[警告] Git 工作区：有未提交修改"


def _output_directory_state(results_root: Path) -> str:
    try:
        results_root.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=results_root, delete=False) as handle:
            probe = Path(handle.name)
        probe.unlink()
    except OSError:
        return f"[缺失] 输出目录不可写：{results_root}"
    return f"[可用] 输出目录：可写（{results_root}）"


def _role_credential_available(
    profiles_file: Path, profiles: list[ProviderProfile], role: str, resolver: SecretResolver
) -> bool:
    for profile in profiles:
        try:
            env_name = role_configuration(profiles_file, profile.name, role).get("api_key_env")
        except (OSError, ValueError, runner.ModelEvalError):
            continue
        if isinstance(env_name, str) and resolver.has(env_name):
            return True
    return False


def _profile_credential_names(
    profiles_file: Path, profiles: list[ProviderProfile]
) -> set[str]:
    names: set[str] = set()
    if not profiles_file.is_file():
        return names
    for profile in profiles:
        for role, enabled in (("target", profile.supports_target), ("judge", profile.supports_judge)):
            if not enabled:
                continue
            try:
                value = role_configuration(profiles_file, profile.name, role).get("api_key_env")
            except (OSError, ValueError, runner.ModelEvalError):
                continue
            if isinstance(value, str) and value:
                names.add(value)
    return names


def _setup_required(
    profiles_file: Path, profiles: list[ProviderProfile], resolver: SecretResolver
) -> bool:
    if not profiles_file.is_file() or not profiles:
        return True
    for role in ("target", "judge"):
        ready = False
        for profile in profiles:
            if role == "target" and not profile.supports_target:
                continue
            if role == "judge" and not profile.supports_judge:
                continue
            try:
                details = role_configuration(profiles_file, profile.name, role)
            except (OSError, ValueError, runner.ModelEvalError):
                continue
            model = details.get("model") or (
                os.environ.get(details["model_env"])
                if isinstance(details.get("model_env"), str)
                else None
            )
            base_url = details.get("base_url") or (
                os.environ.get(details["base_url_env"])
                if isinstance(details.get("base_url_env"), str)
                else None
            )
            if model and base_url and resolver.has(details.get("api_key_env")):
                ready = True
                break
        if not ready:
            return True
    return False


def _interactive_tests() -> int:
    choice = _choose(
        "请选择要运行的测试",
        [
            ("完整测试套件", TestSuiteRequest()),
            ("单元测试", TestSuiteRequest(integration=False, contract=False)),
            ("集成测试", TestSuiteRequest(unit=False, contract=False)),
            ("Contract Eval", TestSuiteRequest(unit=False, integration=False)),
            ("返回", None),
        ],
    )
    if choice is None:
        return 0
    reporter = TerminalTestReporter()
    print("\nRelationship Compass — 自动化测试\n" + "-" * 36)
    result = TestSuiteRunner().run(choice, on_event=reporter.event)
    reporter.summary(result)
    return 0 if result.passed else 1


def _interactive_validate(profiles_file: Path, results_root: Path, debug: bool) -> int:
    print("\n运行环境检查\n" + "-" * 40)
    return _command_validate(_validation_args(profiles_file, results_root, debug))


def _setup_wizard(profiles_file: Path, results_root: Path, resolver: SecretResolver) -> int:
    print("\n首次配置\n" + "-" * 40)
    if not profiles_file.exists():
        print("步骤 1/4：创建本地 Provider 配置")
        created = create_local_profile_config(runner.PROVIDER_PROFILE_EXAMPLE, profiles_file)
        print("  [已配置] 本地 Provider 配置已创建" if created else "  [已配置] 本地 Provider 配置已存在")
    print("步骤 2/4：配置 Target")
    target = _configure_role(profiles_file, resolver, "target", required=True)
    print("步骤 3/4：配置 Judge")
    judge = _configure_role(profiles_file, resolver, "judge", required=True)
    print("步骤 4/4：检查配置")
    result = _interactive_validate(profiles_file, results_root, debug=False)
    if result == 0 and target is not None and judge is not None:
        definition = discover_evals()[0]
        try:
            preflight_request(
                EvalRunRequest(
                    eval_id=definition.eval_id,
                    case_ids=(definition.cases[0].case_id,),
                    target_profile=target.name,
                    judge_profile=judge.name,
                    profiles_file=profiles_file,
                    results_root=results_root,
                    dry_run=True,
                )
            )
            print("  [已配置] Target 与 Judge 的配置和 API 凭据已就绪")
        except EvalConsoleError as exc:
            print(f"  [错误] {exc}")
            result = 1
    if result == 0:
        print("\n首次配置完成，可以开始运行评测。")
    return result


def _configure_providers(profiles_file: Path, resolver: SecretResolver) -> int:
    while True:
        choice = _choose(
            "配置 Provider",
            [
                ("配置 Target", "target"),
                ("配置 Judge", "judge"),
                ("查看当前配置", "view"),
                ("返回", "back"),
            ],
        )
        if choice == "back":
            return 0
        if choice == "view":
            _print_provider_configuration(profiles_file, resolver)
            continue
        if not profiles_file.exists():
            create_local_profile_config(runner.PROVIDER_PROFILE_EXAMPLE, profiles_file)
            print("[已配置] 本地 Provider 配置已创建。")
        _configure_role(profiles_file, resolver, choice, required=False)


def _configure_role(
    profiles_file: Path, resolver: SecretResolver, role: str, *, required: bool
) -> ProviderProfile | None:
    profiles = [
        profile
        for profile in discover_provider_profiles(profiles_file)
        if (profile.supports_target if role == "target" else profile.supports_judge)
    ]
    if not profiles:
        return _create_profile_interactively(profiles_file, role, resolver)
    choices: list[tuple[str, ProviderProfile | str]] = [
        (_profile_label(profile, role), profile) for profile in profiles
    ]
    choices.extend([("创建新的本地 Profile", "create"), ("返回", "back")])
    selected = _choose(f"请选择 {role.title()} Profile", choices)
    if selected == "back":
        return None
    if selected == "create":
        return _create_profile_interactively(profiles_file, role, resolver)
    assert isinstance(selected, ProviderProfile)
    if required:
        if not _ensure_role_ready(profiles_file, selected, role, resolver):
            return None
    else:
        _edit_profile_menu(profiles_file, selected, role, resolver)
    return selected


def _create_profile_interactively(
    profiles_file: Path, role: str, resolver: SecretResolver
) -> ProviderProfile | None:
    name = _prompt_required("Profile 名称")
    provider = _choose(
        "Provider 类型",
        [
            ("OpenAI Responses", "openai_responses"),
            ("OpenAI-compatible", "openai_compatible_chat"),
        ],
    )
    base_url = _prompt_url("API Base URL")
    model = _prompt_required("模型名称")
    api_key_env = profile_api_key_env(name)
    secret_configuration = _collect_secret_configuration(api_key_env)
    if secret_configuration is None:
        print("未提供 API Key，未保存 Provider 配置。")
        return None
    if not _confirm_provider_configuration():
        print("未保存 Provider 配置。")
        return None
    profile_name = create_profile(
        profiles_file, name=name, provider=provider, role=role, model=model, base_url=base_url
    )
    profile = next(item for item in discover_provider_profiles(profiles_file) if item.name == profile_name)
    _store_secret(resolver, api_key_env, *secret_configuration)
    return profile


def _edit_profile_menu(
    profiles_file: Path, profile: ProviderProfile, role: str, resolver: SecretResolver
) -> None:
    details = role_configuration(profiles_file, profile.name, role)
    env_name = details.get("api_key_env")
    draft = _RoleConfigurationDraft()
    while True:
        choice = _choose(
            f"{role.title()} Profile：{profile.name}",
            [
                ("保存本次修改并返回", "save"),
                ("放弃本次修改", "cancel"),
                ("修改模型", "model"),
                ("修改 API Base URL", "base_url"),
                ("配置 API Key", "key"),
            ],
        )
        if choice == "cancel":
            print("未保存 Provider 配置。")
            return
        if choice == "save":
            _commit_role_configuration(profiles_file, profile.name, role, resolver, env_name, draft)
            return
        if choice == "model":
            draft.model = _prompt_with_current(
                "模型名称", draft.model if draft.model is not None else details.get("model")
            )
        elif choice == "base_url":
            draft.base_url = _prompt_url_with_current(
                "API Base URL",
                draft.base_url if draft.base_url is not None else details.get("base_url"),
            )
        else:
            if isinstance(env_name, str):
                draft.secret_configuration = _collect_secret_configuration(env_name)
            else:
                print("该 Profile 未声明 API Key 环境变量名。")


def _configure_secret(resolver: SecretResolver, env_name: str) -> None:
    configuration = _collect_secret_configuration(env_name)
    if configuration is not None:
        _store_secret(resolver, env_name, *configuration)


def _collect_secret_configuration(env_name: str) -> tuple[str, str] | None:
    print(f"未检测到 API Key：{env_name}")
    print("请输入 API Key（输入内容不会显示）：")
    value = _read_interactive_secret("> ")
    if not value.strip():
        print("未修改 API Key。")
        return
    mode = _choose(
        "如何使用这个 API Key",
        [("仅本次会话使用", "session"), ("保存到本地，供以后使用", "local")],
    )
    return value, mode


def _store_secret(resolver: SecretResolver, env_name: str, value: str, mode: str) -> None:
    if mode == "local":
        resolver.save_local(env_name, value)
        print("API Key 已保存到 .env.local；该文件已被 Git 忽略，不会提交到仓库。")
    else:
        resolver.set_session(env_name, value)
        print("API Key 已配置为仅本次 Console 会话使用。")


def _confirm_provider_configuration() -> bool:
    return _yes_no("确认保存本次 Provider 配置吗？", default=True)


def _commit_role_configuration(
    profiles_file: Path,
    profile_name: str,
    role: str,
    resolver: SecretResolver,
    env_name: object,
    draft: _RoleConfigurationDraft,
) -> bool:
    if not draft.has_changes:
        return True
    if not _confirm_provider_configuration():
        print("未保存 Provider 配置。")
        return False
    if draft.model is not None or draft.base_url is not None:
        update_role_configuration(
            profiles_file,
            profile_name,
            role,
            model=draft.model,
            base_url=draft.base_url,
        )
    if draft.secret_configuration is not None:
        if not isinstance(env_name, str) or not env_name:
            raise ValueError("Profile 缺少 API Key 环境变量名。")
        _store_secret(resolver, env_name, *draft.secret_configuration)
    return True


def _effective_provider_configuration(
    profiles_file: Path, profile_name: str, role: str, resolver: SecretResolver
) -> dict[str, object]:
    """Resolve one role's display-safe configuration without creating a provider."""
    details = role_configuration(profiles_file, profile_name, role)
    capabilities = details.get("capabilities")
    token_parameter = (
        capabilities.get("max_output_tokens_parameter")
        if isinstance(capabilities, dict)
        else None
    )
    model_env = details.get("model_env")
    resolved_model = details.get("model") or (
        os.environ.get(model_env) if isinstance(model_env, str) else None
    )
    base_url_env = details.get("base_url_env")
    resolved_base_url = details.get("base_url") or (
        os.environ.get(base_url_env) if isinstance(base_url_env, str) else None
    )
    api_key_env = details.get("api_key_env")
    configured = resolver.has(api_key_env) if isinstance(api_key_env, str) else False
    missing: list[str] = []
    if not details.get("provider"):
        missing.append("Provider")
    if not resolved_model:
        missing.append("模型")
    if not isinstance(api_key_env, str) or not api_key_env:
        missing.append("API Key 环境变量")
    elif not configured:
        missing.append("API Key")
    return {
        **details,
        "resolved_model": resolved_model,
        "resolved_base_url": resolved_base_url,
        "api_key_configured": configured,
        "token_parameter": token_parameter,
        "missing": tuple(missing),
    }


def _print_provider_configuration(profiles_file: Path, resolver: SecretResolver) -> None:
    """Render each available role config using the same effective resolution path."""
    profiles = discover_provider_profiles(profiles_file)
    print("\nProvider 有效配置")
    for role, label in (("target", "Target"), ("judge", "Judge")):
        eligible = (
            [profile for profile in profiles if profile.supports_target]
            if role == "target"
            else [profile for profile in profiles if profile.supports_judge]
        )
        print(f"\n可用 {label} Profiles")
        if not eligible:
            print("  （未找到支持该角色的 Profile）")
            continue
        for index, profile in enumerate(eligible, start=1):
            try:
                details = _effective_provider_configuration(
                    profiles_file, profile.name, role, resolver
                )
            except (OSError, ValueError, runner.ModelEvalError) as exc:
                print(f"  {index}. [配置错误] {profile.name}：{exc}")
                continue
            missing = details["missing"]
            status = "可用" if not missing else f"缺少{' / '.join(missing)}"
            structured_output = details.get("structured_output_mode")
            if structured_output is None:
                structured_output = "不适用（Target 普通文本）" if role == "target" else "未声明"
            role_defaults = runner.PROVIDER_ROLE_DEFAULTS[role]
            configured = "是" if details["api_key_configured"] else "否"
            print(f"  {index}. [{status}] {profile.name}")
            print(
                f"    {label}：Profile={profile.name}，"
                f"Provider={details.get('provider') or '缺失'}，"
                f"Vendor={details.get('declared_upstream_vendor') or '未声明'}，"
                f"Model={details['resolved_model'] or '未解析'}，"
                f"Structured Output={structured_output}，"
                f"Thinking={details.get('thinking') or 'provider-default'}"
            )
            print(
                f"      Max Output Tokens={details.get('max_output_tokens') or role_defaults['max_output_tokens']}，"
                f"Token Parameter={details['token_parameter'] or '未声明'}，"
                f"Max Retries={details.get('max_retries') or role_defaults['max_retries']}，"
                f"Model Env={details.get('model_env') or '未声明'}"
            )
            print(
                f"      API Base URL={details['resolved_base_url'] or '未解析'}，"
                f"Base URL Env={details.get('base_url_env') or '未声明'}，"
                f"API Key 环境变量={details.get('api_key_env') or '缺失'}，"
                f"API Key 已配置={configured}"
            )


def _interactive_model_override(profile: ProviderProfile, role: str) -> str | None:
    current = profile.target_model if role == "target" else profile.judge_model
    if not current or current.startswith("$"):
        return _prompt_required(f"{role.title()} 模型名称")
    choice = _choose(
        f"{role.title()} 模型（当前值：{current}）",
        [("使用当前模型", None), ("仅本次运行修改", "change")],
    )
    return _prompt_required(f"{role.title()} 模型名称") if choice == "change" else None


def _ensure_role_ready(
    profiles_file: Path, profile: ProviderProfile, role: str, resolver: SecretResolver
) -> bool:
    details = role_configuration(profiles_file, profile.name, role)
    model = details.get("model") or (
        os.environ.get(details["model_env"])
        if isinstance(details.get("model_env"), str)
        else None
    )
    draft = _RoleConfigurationDraft()
    if not model:
        print(f"{role.title()} Profile 尚未配置模型，现在完成配置。")
        draft.model = _prompt_required("模型名称")
    base_url = details.get("base_url") or (
        os.environ.get(details["base_url_env"])
        if isinstance(details.get("base_url_env"), str)
        else None
    )
    if not base_url:
        print(f"{role.title()} Profile 尚未配置 API Base URL，现在完成配置。")
        draft.base_url = _prompt_url("API Base URL")
    env_name = details.get("api_key_env")
    if not isinstance(env_name, str) or not env_name:
        print(f"{role.title()} Profile 缺少 API Key 环境变量名。")
        return False
    if not resolver.has(env_name):
        draft.secret_configuration = _collect_secret_configuration(env_name)
        if draft.secret_configuration is None:
            print("未提供 API Key，未保存 Provider 配置。")
            return False
    if not _commit_role_configuration(
        profiles_file, profile.name, role, resolver, env_name, draft
    ):
        return False
    return resolver.has(env_name)


def _interactive_concurrency(role: str) -> int:
    selected = _choose(
        f"{role} 并发数",
        [("1（推荐）", 1), ("2", 2), ("4", 4), ("自定义", "custom")],
    )
    if selected != "custom":
        return int(selected)
    while True:
        value = _read_interactive_input(f"请输入 {role} 并发数（1-32）：").strip()
        if value.isdigit() and 1 <= int(value) <= 32:
            return int(value)
        print("并发数必须介于 1 到 32 之间。")


def _prompt_required(label: str) -> str:
    while True:
        value = _read_interactive_input(f"{label}: ").strip()
        if value:
            return value
        print(f"{label}不能为空。")


def _prompt_with_current(label: str, current: object) -> str:
    value = str(current).strip() if isinstance(current, str) else ""
    if not value:
        return _prompt_required(label)
    print(f"{label}\n当前值：{value}\n直接回车保留当前值，或输入新的值：")
    return _read_interactive_input("> ").strip() or value


def _prompt_url(label: str) -> str:
    while True:
        value = _read_interactive_input(f"{label}: ").strip()
        try:
            return validate_base_url(value)
        except ValueError as exc:
            print(_chinese_error_message(exc))


def _prompt_url_with_current(label: str, current: object) -> str:
    value = str(current).strip() if isinstance(current, str) else ""
    if not value:
        return _prompt_url(label)
    while True:
        print(f"{label}\n当前值：{value}\n直接回车保留当前值，或输入新的地址：")
        candidate = _read_interactive_input("> ").strip() or value
        try:
            return validate_base_url(candidate)
        except ValueError as exc:
            print(_chinese_error_message(exc))


def _interactive_run(
    evals: list[EvalDefinition], profiles_file: Path, results_root: Path, debug: bool,
    resolver: SecretResolver,
) -> int:
    if not discover_provider_profiles(profiles_file):
        print("运行评测前需要先完成 Provider 配置。")
        _setup_wizard(profiles_file, results_root, resolver)
        if not discover_provider_profiles(profiles_file):
            return 1
    execution_mode = _choose(
        "请选择评测方式",
        [
            ("完整运行（Target + Judge）", EvalExecutionMode.FULL),
            ("仅运行 Target（保存回复，暂不 Judge）", EvalExecutionMode.TARGET_ONLY),
            ("仅运行 Judge（复用历史 Target）", EvalExecutionMode.JUDGE_ONLY),
            ("继续运行（按 Case 状态只补缺失阶段）", EvalExecutionMode.RESUME),
            ("返回", None),
        ],
    )
    if execution_mode is None:
        return 0
    if execution_mode is EvalExecutionMode.TARGET_ONLY:
        return _interactive_target_only(evals, profiles_file, results_root, debug, resolver)
    if execution_mode in {EvalExecutionMode.JUDGE_ONLY, EvalExecutionMode.RESUME}:
        return _interactive_history_stage(
            evals, profiles_file, results_root, debug, resolver, execution_mode
        )
    definition = _choose("请选择 Eval", [(item.title, item) for item in evals])
    print(f"Cases：{len(definition.cases)}\n说明：{definition.description}")
    case_ids = _interactive_case_selection(definition, results_root)
    target_profile, judge_profile = _interactive_profiles(profiles_file)
    if not _ensure_role_ready(profiles_file, target_profile, "target", resolver):
        return 1
    if not _ensure_role_ready(profiles_file, judge_profile, "judge", resolver):
        return 1
    refreshed_profiles = {profile.name: profile for profile in discover_provider_profiles(profiles_file)}
    target_profile = refreshed_profiles[target_profile.name]
    judge_profile = refreshed_profiles[judge_profile.name]
    target_model = _interactive_model_override(target_profile, "target")
    judge_model = _interactive_model_override(judge_profile, "judge")
    target_concurrency = _interactive_concurrency("Target")
    judge_concurrency = _interactive_concurrency("Judge")
    continue_on_error = _choose(
        "单个 Case 出错时",
        [("继续运行剩余 Cases（推荐）", True), ("立即停止", False)],
    )
    dry_run = _choose("运行模式", [("Dry Run（不调用真实 API）", True), ("真实 API 运行", False)])
    if not dry_run:
        print("注意：本次运行将调用外部 API，可能产生 API 使用费用。")
    request = build_interactive_request(
        definition=definition,
        case_ids=case_ids,
        target_profile=target_profile.name,
        judge_profile=judge_profile.name,
        profiles_file=profiles_file,
        results_root=results_root,
        dry_run=dry_run,
        allow_dirty_debug=_yes_no(
            "如有需要，允许在有未提交修改的工作区进行调试运行？此类运行不能作为正式参考", default=False
        ),
        debug=debug,
        target_concurrency=target_concurrency,
        judge_concurrency=judge_concurrency,
        target_model_override=target_model,
        judge_model_override=judge_model,
        continue_on_error=continue_on_error,
    )
    _print_run_summary(
        definition, request, target_profile, judge_profile, resolver,
        target_model=target_model, judge_model=judge_model,
    )
    action = _choose(
        "开始运行吗",
        [("开始", "start"), ("返回修改", "back"), ("取消", "cancel")],
    )
    if action == "back":
        return _interactive_run(evals, profiles_file, results_root, debug, resolver)
    if action != "start":
        print("未运行任何评测。")
        return 0
    return _execute_and_print(request)


def _interactive_target_only(
    evals: list[EvalDefinition], profiles_file: Path, results_root: Path, debug: bool,
    resolver: SecretResolver,
) -> int:
    definition = _choose("请选择 Eval", [(item.title, item) for item in evals])
    print(f"Cases：{len(definition.cases)}\n说明：{definition.description}")
    case_ids = _interactive_case_selection(definition, results_root)
    target = _interactive_profile(profiles_file, "target")
    if not _ensure_role_ready(profiles_file, target, "target", resolver):
        return 1
    target = {item.name: item for item in discover_provider_profiles(profiles_file)}[target.name]
    target_model = _interactive_model_override(target, "target")
    dry_run = _choose("运行模式", [("Dry Run（不调用真实 API）", True), ("真实 API 运行", False)])
    request = EvalRunRequest(
        eval_id=definition.eval_id,
        case_ids=tuple(case_ids),
        target_profile=target.name,
        judge_profile=None,
        profiles_file=profiles_file,
        results_root=results_root,
        dry_run=dry_run,
        debug=debug,
        allow_dirty_debug=_yes_no("如有需要，允许在有未提交修改的工作区进行调试运行", default=False),
        target_concurrency=_interactive_concurrency("Target"),
        target_model_override=target_model,
        continue_on_error=_choose("单个 Case 出错时", [("继续运行剩余 Cases（推荐）", True), ("立即停止", False)]),
        mode=EvalExecutionMode.TARGET_ONLY,
    )
    _print_stage_request_summary(definition, request, target, None, None)
    if _choose("开始运行吗", [("开始", True), ("取消", False)]):
        return _execute_and_print(request)
    print("未运行任何评测。")
    return 0


def _interactive_history_stage(
    evals: list[EvalDefinition], profiles_file: Path, results_root: Path, debug: bool,
    resolver: SecretResolver, mode: EvalExecutionMode,
) -> int:
    candidates = discover_runs(results_root)
    if not candidates:
        print("尚未找到可复用的历史运行。")
        return 0
    source = _choose("请选择历史运行", [(_run_label(item), item) for item in candidates])
    definition = find_eval(source.eval_id) if source.eval_id else _choose("请选择 Eval", [(item.title, item) for item in evals])
    source_metadata = runner.load_json_object(source.run_dir / "run.json")
    all_case_ids = [
        record["case_id"] for record in source_metadata.get("cases", [])
        if isinstance(record, dict) and isinstance(record.get("case_id"), str)
    ]
    if mode is EvalExecutionMode.JUDGE_ONLY:
        selector = _choose(
            "选择要 Judge 的历史 Target 回复",
            [
                ("全部成功 Target", JudgeCaseSelector.ALL_TARGET),
                ("仅 Judge ERROR", JudgeCaseSelector.JUDGE_ERROR),
                ("仅未 Judge", JudgeCaseSelector.JUDGE_MISSING),
                ("Judge ERROR 或未 Judge", JudgeCaseSelector.JUDGE_ERROR_OR_MISSING),
                ("手动选择 Case", JudgeCaseSelector.SELECTED),
            ],
        )
        requested = all_case_ids
        if selector is JudgeCaseSelector.SELECTED:
            _print_cases(definition)
            requested = parse_case_selection(
                _read_interactive_input("请输入位置、ID 或范围：").strip(),
                [case.case_id for case in definition.cases],
            )
        case_ids = list(judge_only_case_ids(source.run_dir, selector, tuple(requested)))
        stage_plan = plan_stage_execution(source.run_dir, tuple(case_ids), mode)
        target = None
        judge = _interactive_profile(profiles_file, "judge")
        if not _ensure_role_ready(profiles_file, judge, "judge", resolver):
            return 1
        judge = {item.name: item for item in discover_provider_profiles(profiles_file)}[judge.name]
        target_model = None
        judge_model = _interactive_model_override(judge, "judge")
        target_concurrency = 1
        judge_concurrency = _interactive_concurrency("Judge")
    else:
        scope = _choose(
            "选择继续范围",
            [("自动继续全部未完成或 ERROR Case", "auto"), ("手动选择 Case", "selected")],
        )
        if scope == "selected":
            _print_cases(definition)
            case_ids = parse_case_selection(
                _read_interactive_input("请输入位置、ID 或范围：").strip(),
                [case.case_id for case in definition.cases],
            )
        else:
            case_ids = all_case_ids
        stage_plan = plan_stage_execution(source.run_dir, tuple(case_ids), mode)
        target = None
        judge = None
        target_model = None
        judge_model = None
        selector = JudgeCaseSelector.SELECTED
        target_concurrency, judge_concurrency = _interactive_resume_concurrency(
            source.run_dir, source_metadata, stage_plan
        )
    dry_run = _choose("运行模式", [("Dry Run（不调用真实 API）", True), ("真实 API 运行", False)])
    request = EvalRunRequest(
        eval_id=definition.eval_id,
        case_ids=tuple(case_ids),
        target_profile=target.name if target is not None else None,
        judge_profile=judge.name if judge is not None else None,
        profiles_file=profiles_file,
        results_root=results_root,
        dry_run=dry_run,
        debug=debug,
        allow_dirty_debug=_yes_no("如有需要，允许在有未提交修改的工作区进行调试运行", default=False),
        target_concurrency=target_concurrency,
        judge_concurrency=judge_concurrency,
        target_model_override=target_model,
        judge_model_override=judge_model,
        mode=mode,
        source_run_dir=source.run_dir,
        judge_selector=selector,
    )
    if mode is EvalExecutionMode.RESUME:
        request = _resume_request_with_inherited_configuration(request, source_metadata)
        configured = {item.name: item for item in discover_provider_profiles(profiles_file)}
        target = configured.get(request.target_profile) if stage_plan.target_cases else None
        judge = configured.get(request.judge_profile) if stage_plan.judge_cases else None
    _print_stage_request_summary(definition, request, target, judge, stage_plan)
    if mode is EvalExecutionMode.RESUME:
        while True:
            action = _choose(
                "继续运行吗",
                [("继续运行", "start"), ("查看 Cases", "cases"), ("返回", "back"), ("取消", "cancel")],
            )
            if action == "cases":
                _print_stage_plan(stage_plan)
                continue
            if action == "start":
                return _execute_and_print(request)
            return 0
    if _choose("开始运行吗", [("开始", True), ("取消", False)]):
        return _execute_and_print(request)
    print("未运行任何评测。")
    return 0


def _interactive_resume_concurrency(
    run_dir: Path, metadata: dict[str, object], stage_plan: object
) -> tuple[int, int]:
    """Ask only for the stages the current Resume planner will actually run."""
    console = metadata.get("console")
    if not isinstance(console, dict):
        raise EvalConsoleError("Unsupported Run Artifact Version：当前 Console Run 缺少并发配置。")
    initial_target = _saved_concurrency(console, "target_concurrency")
    initial_judge = _saved_concurrency(console, "judge_concurrency")
    recent_target, recent_judge = _resume_concurrency_defaults(metadata)
    values = {"Target": recent_target, "Judge": recent_judge}
    initials = {"Target": initial_target, "Judge": initial_judge}
    planned = {
        "Target": bool(getattr(stage_plan, "target_cases", ())),
        "Judge": bool(getattr(stage_plan, "judge_cases", ())),
    }
    for role in ("Target", "Judge"):
        if not planned[role]:
            continue
        previous = initials[role]
        recent = values[role]
        print(
            f"{role} 初始并发：{previous}；上次 Execution 并发：{recent}。"
        )
        if previous > 1 and _historical_rate_limit_detected(run_dir, role):
            print(
                f"提示：上次运行检测到 {role} HTTP 429；"
                f"可考虑本次降低并发，例如 1（不会自动修改）。"
            )
        values[role] = _prompt_concurrency_with_default(role, recent)
    return values["Target"], values["Judge"]


def _prompt_concurrency_with_default(role: str, default: int) -> int:
    while True:
        value = _read_interactive_input(f"本次 {role} 并发 [{default}]：").strip()
        if not value:
            return default
        if value.isdigit() and 1 <= int(value) <= 32:
            return int(value)
        print("并发数必须介于 1 到 32 之间。")


def _historical_rate_limit_detected(run_dir: Path, role: str) -> bool:
    path = run_dir / ("responses.jsonl" if role == "Target" else "judgments.jsonl")
    if not path.is_file():
        return False
    for record in runner.load_jsonl(path):
        if record.get("error_code") == "RATE_LIMIT":
            return True
        telemetry = record.get("http_telemetry")
        if isinstance(telemetry, dict):
            count = telemetry.get("rate_limit_count")
            if isinstance(count, int) and not isinstance(count, bool) and count > 0:
                return True
        diagnostics = record.get("diagnostics")
        if isinstance(diagnostics, dict) and diagnostics.get("http_status") == 429:
            return True
    return False


def build_interactive_request(
    *,
    definition: EvalDefinition,
    case_ids: list[str],
    target_profile: str,
    judge_profile: str,
    profiles_file: Path,
    results_root: Path,
    dry_run: bool,
    allow_dirty_debug: bool,
    debug: bool,
    target_concurrency: int,
    judge_concurrency: int,
    target_model_override: str | None,
    judge_model_override: str | None,
    continue_on_error: bool,
) -> EvalRunRequest:
    """Build the same request used by the wizard without executing an eval."""
    return EvalRunRequest(
        eval_id=definition.eval_id,
        case_ids=tuple(case_ids),
        target_profile=target_profile,
        judge_profile=judge_profile,
        profiles_file=profiles_file,
        results_root=results_root,
        dry_run=dry_run,
        allow_dirty_debug=allow_dirty_debug,
        debug=debug,
        target_concurrency=target_concurrency,
        judge_concurrency=judge_concurrency,
        target_model_override=target_model_override,
        judge_model_override=judge_model_override,
        continue_on_error=continue_on_error,
    )


def _print_run_summary(
    definition: EvalDefinition,
    request: EvalRunRequest,
    target_profile: ProviderProfile,
    judge_profile: ProviderProfile,
    resolver: SecretResolver,
    *,
    target_model: str | None,
    judge_model: str | None,
) -> None:
    target_details = role_configuration(request.profiles_file, target_profile.name, "target")
    judge_details = role_configuration(request.profiles_file, judge_profile.name, "judge")
    target_name = target_model or target_profile.target_model or "将在预检查时解析"
    judge_name = judge_model or judge_profile.judge_model or "将在预检查时解析"
    target_key = target_details.get("api_key_env")
    judge_key = judge_details.get("api_key_env")
    estimated_real_calls = len(request.case_ids) * 2
    print("\n运行确认\n" + "-" * 40)
    print(f"Eval：{definition.eval_id}")
    print(f"Cases：{len(request.case_ids)} / {len(definition.cases)}")
    print(f"Target Profile：{target_profile.name}")
    print(f"Target 模型：{target_name}")
    print(f"Target API 凭据：{'已配置' if isinstance(target_key, str) and resolver.has(target_key) else '缺失'}")
    print(f"Judge Profile：{judge_profile.name}")
    print(f"Judge 模型：{judge_name}")
    print(f"Judge API 凭据：{'已配置' if isinstance(judge_key, str) and resolver.has(judge_key) else '缺失'}")
    print("Target：")
    print(f"  Cases：{len(request.case_ids)}")
    print(f"  Concurrency：{request.target_concurrency}")
    print(f"  Planned API Calls：{len(request.case_ids)}")
    print("Judge：")
    print(f"  Cases：{len(request.case_ids)}")
    print(f"  Concurrency：{request.judge_concurrency}")
    print(f"  Planned API Calls：{len(request.case_ids)}")
    if max(request.target_concurrency, request.judge_concurrency) > 1:
        print("停止提示：停止后不会安排新的 Case；已开始的请求完成后将保存进度。")
    print(f"错误处理：{'继续运行' if request.continue_on_error else '立即停止'}")
    print(f"运行模式：{'Dry Run（不调用真实 API）' if request.dry_run else '真实 API 运行'}")
    if request.dry_run:
        print("预计 API 调用：0（Dry Run；真实运行预计约 " + str(estimated_real_calls) + " 次）")
    else:
        print(f"预计 API 调用：约 {estimated_real_calls} 次")
    print(f"Git 状态：{_git_workspace_state(runner.ROOT)}")
    print(f"输出位置：{request.results_root}")


def _print_stage_request_summary(
    definition: EvalDefinition,
    request: EvalRunRequest,
    target_profile: ProviderProfile | None,
    judge_profile: ProviderProfile | None,
    stage_plan: object | None,
) -> None:
    """Chinese confirmation for stage-scoped requests without hidden API work."""
    labels = {
        EvalExecutionMode.FULL: "完整运行（Target + Judge）",
        EvalExecutionMode.TARGET_ONLY: "仅 Target",
        EvalExecutionMode.JUDGE_ONLY: "仅 Judge（复用历史 Target）",
        EvalExecutionMode.RESUME: "继续运行（仅补缺失阶段）",
    }
    target_count = len(request.case_ids)
    judge_count = len(request.case_ids)
    if stage_plan is not None:
        target_count = len(getattr(stage_plan, "target_cases", ()))
        judge_count = len(getattr(stage_plan, "judge_cases", ()))
    elif request.mode is EvalExecutionMode.TARGET_ONLY:
        judge_count = 0
    print("\n运行确认\n" + "-" * 40)
    print(f"方式：{labels[request.mode]}")
    print(f"Eval：{definition.eval_id}")
    print(f"Cases：{len(request.case_ids)} / {len(definition.cases)}")
    if request.source_run_dir is not None:
        print(f"来源运行：{request.source_run_dir}")
    print("Target：")
    print(f"  Cases：{target_count}")
    print(f"  Concurrency：{request.target_concurrency}")
    print(f"  Planned API Calls：{target_count}")
    if target_count:
        target_name = request.resume_target_model or (
            target_profile.target_model if target_profile else None
        ) or "将在预检查时解析"
        print(f"Target：{target_profile.name if target_profile else request.target_profile}")
        print(f"Target 模型：{target_name}")
    else:
        print("Target：已有结果，不调用 API")
    print("Judge：")
    print(f"  Cases：{judge_count}")
    print(f"  Concurrency：{request.judge_concurrency}")
    print(f"  Planned API Calls：{judge_count}")
    if judge_count:
        judge_name = request.resume_judge_model or (
            judge_profile.judge_model if judge_profile else None
        ) or "将在预检查时解析"
        print(f"Judge：{judge_profile.name if judge_profile else request.judge_profile}")
        print(f"Judge 模型：{judge_name}")
    else:
        print("Judge：无需执行")
    if request.mode is EvalExecutionMode.RESUME:
        print("Resume 将继续使用原运行的 Provider；本次可为需要的 Stage 单独调整并发。")
    if max(request.target_concurrency, request.judge_concurrency) > 1:
        print("停止提示：停止后不会安排新的 Case；已开始的请求完成后将保存进度。")
    print(f"运行模式：{'Dry Run（不调用真实 API）' if request.dry_run else '真实 API 运行'}")
    print(f"输出位置：{request.results_root}")


def _print_stage_plan(stage_plan: object) -> None:
    print("\nCase 执行计划")
    for item in getattr(stage_plan, "cases", ()):
        if item.run_target:
            action = "TARGET → JUDGE"
        elif item.run_judge:
            action = "JUDGE"
        else:
            action = "SKIP"
        print(f"  {item.case_id}  {action} — {item.reason}")


def _interactive_case_selection(definition: EvalDefinition, results_root: Path) -> list[str]:
    modes = [
        ("全部 Cases", "all"),
        ("单个 Case", "single"),
        ("选择多个 Cases", "multiple"),
        ("Case 范围", "range"),
    ]
    mode = _choose("请选择要运行的 Cases", modes)
    if mode == "all":
        return [case.case_id for case in definition.cases]
    _print_cases(definition)
    if mode == "single":
        value = _read_interactive_input("\n请输入一个 Case 位置或 Case ID：").strip()
    elif mode == "multiple":
        print("\n请输入位置或 ID，例如 1,3,5-8。输入 all 选择全部；输入 clear 重新选择。")
        while True:
            value = _read_interactive_input("选择：").strip()
            if value.lower() == "clear":
                print("已清空选择，请重新输入要运行的 Cases。")
                continue
            break
    else:
        value = _read_interactive_input("\n请输入范围或组合，例如 1-10 或 1,3,5-10：").strip()
    selected = parse_case_selection(value, [case.case_id for case in definition.cases])
    print("\n已选择的 Cases：")
    for case_id in selected:
        print(f"  {case_id}")
    print(f"总计：{len(selected)} 个 Cases")
    return selected


def _interactive_profiles(profiles_file: Path) -> tuple[ProviderProfile, ProviderProfile]:
    return _interactive_profile(profiles_file, "target"), _interactive_profile(profiles_file, "judge")


def _interactive_profile(profiles_file: Path, role: str) -> ProviderProfile:
    profiles = [
        profile
        for profile in discover_provider_profiles(profiles_file)
        if (profile.supports_target if role == "target" else profile.supports_judge)
    ]
    if not profiles:
        raise EvalConsoleError(
            f"未找到可用的 {role.title()} Profile：{profiles_file}。"
            "请在控制台中选择“配置 Provider”。"
        )
    label = "请选择 Target 模型 / Provider" if role == "target" else "请选择 Judge"
    return _choose(label, [(_profile_label(profile, role), profile) for profile in profiles])


def _execute_and_print(request: EvalRunRequest) -> int:
    target, judge, target_plan, judge_plan = preflight_request(request)
    print("\n运行前检查")
    _print_stage_preflight("Target", target_plan)
    _print_stage_preflight("Judge", judge_plan)
    print(f"  [通过] Cases：已选择 {len(request.case_ids)} 个")
    if request.dry_run:
        outcome = execute_request(request, target_provider=target, judge_provider=judge)
        print("\nDRY RUN 完成：配置有效，未调用真实 API。")
        print(
            "预计 API 调用：Target " + str(outcome.api_calls["target"])
            + "，Judge " + str(outcome.api_calls["judge"])
        )
        print(f"输出位置：{outcome.run_dir}")
        return 0
    print("\n正在运行……每个 Case 完成后都会保存进度。")
    activity = _ActivityReporter(request.target_concurrency, request.judge_concurrency)
    try:
        with _GracefulStop(
            request.target_concurrency,
            request.judge_concurrency,
            active_cases=activity.active_case_ids,
        ) as stop:
            def on_activity(
                phase: str, record: dict[str, object], started: int, total: int
            ) -> None:
                stop.set_stage(phase)
                activity.start(phase, record, started, total)

            outcome = execute_request(
                request,
                target_provider=target,
                judge_provider=judge,
                progress=activity.finish,
                activity=on_activity,
                should_stop=lambda: stop.requested,
                rate_limit=activity.rate_limit,
            )
    finally:
        activity.close()
    _print_outcome(outcome)
    return 0 if outcome.summary and outcome.summary.get("completion_status") in {"COMPLETED", "TARGET_COMPLETE"} else 2


def _case_ids_from_args(args: argparse.Namespace, definition: EvalDefinition) -> list[str]:
    available = [case.case_id for case in definition.cases]
    if args.cases:
        return parse_case_selection(args.cases, available)
    if args.case:
        return parse_case_selection(",".join(args.case), available)
    return available


def _request_from_args(args: argparse.Namespace, eval_id: str, case_ids: list[str]) -> EvalRunRequest:
    mode = _execution_mode_from_args(args)
    target_profile = args.target_profile or args.profile
    judge_profile = args.judge_profile or args.profile
    requires_target = mode in {EvalExecutionMode.FULL, EvalExecutionMode.TARGET_ONLY}
    requires_judge = mode in {EvalExecutionMode.FULL, EvalExecutionMode.JUDGE_ONLY}
    if (requires_target and not target_profile) or (requires_judge and not judge_profile):
        raise EvalConsoleError(
            "请使用 --profile，或按所选模式提供所需的 --target-profile / --judge-profile。"
        )
    return EvalRunRequest(
        eval_id=eval_id,
        case_ids=tuple(case_ids),
        target_profile=target_profile,
        judge_profile=judge_profile,
        profiles_file=args.profiles_file.expanduser().resolve(),
        results_root=args.results_root.expanduser().resolve(),
        dry_run=args.dry_run,
        debug=args.debug,
        allow_dirty_debug=args.allow_dirty_debug,
        target_concurrency=(
            args.target_concurrency if isinstance(args.target_concurrency, int) else 1
        ),
        judge_concurrency=(
            args.judge_concurrency if isinstance(args.judge_concurrency, int) else 1
        ),
        run_id=args.run_id,
        target_model_override=getattr(args, "target_model", None),
        judge_model_override=getattr(args, "judge_model", None),
        continue_on_error=not getattr(args, "stop_on_error", False),
        mode=mode,
        source_run_dir=(
            args.source_run.expanduser().resolve()
            if getattr(args, "source_run", None) is not None
            else None
        ),
        judge_selector=_judge_selector_from_args(args),
    )


def _execution_mode_from_args(args: argparse.Namespace) -> EvalExecutionMode:
    value = getattr(args, "execution_mode", "full")
    return {
        "full": EvalExecutionMode.FULL,
        "target-only": EvalExecutionMode.TARGET_ONLY,
        "judge-only": EvalExecutionMode.JUDGE_ONLY,
        "resume": EvalExecutionMode.RESUME,
    }[value]


def _judge_selector_from_args(args: argparse.Namespace) -> JudgeCaseSelector:
    value = getattr(args, "judge_selector", "selected")
    return {
        "all-target": JudgeCaseSelector.ALL_TARGET,
        "judge-error": JudgeCaseSelector.JUDGE_ERROR,
        "judge-missing": JudgeCaseSelector.JUDGE_MISSING,
        "judge-error-or-missing": JudgeCaseSelector.JUDGE_ERROR_OR_MISSING,
        "selected": JudgeCaseSelector.SELECTED,
    }[value]


def _print_stage_preflight(label: str, plan: dict[str, object]) -> None:
    if not plan.get("enabled"):
        print(f"  [跳过] {label}：本次模式无需执行")
        return
    print(f"  [通过] {label}：{plan['provider']} / {plan['requested_model']}")


def _result_label(phase: str, record: dict[str, object]) -> str:
    status = str(record.get("status") or "UNKNOWN")
    if phase == "TARGET":
        return "OK" if status == "MODEL_RESPONSE" else "ERROR"
    if status == "JUDGE_ERROR":
        return "ERROR"
    if status == "JUDGMENT":
        criteria = record.get("criteria")
        if isinstance(criteria, list) and any(
            item.get("passed") is False for item in criteria if isinstance(item, dict)
        ):
            return "FAIL"
        return "PASS"
    return "ERROR"


def _phase_label(phase: str) -> str:
    return {"TARGET": "Target", "JUDGE": "Judge"}.get(phase, phase)


def _chinese_error_message(error: BaseException) -> str:
    """Keep input validation messages concise while preserving technical detail in debug mode."""
    return str(error)


def _duration_seconds(record: dict[str, object]) -> float | None:
    saved = record.get("duration_seconds")
    if isinstance(saved, (int, float)) and not isinstance(saved, bool):
        return float(saved)
    start = record.get("started_at") or record.get("evaluated_at")
    end = record.get("completed_at")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        return max(
            0.0,
            (
                datetime.fromisoformat(end.replace("Z", "+00:00"))
                - datetime.fromisoformat(start.replace("Z", "+00:00"))
            ).total_seconds(),
        )
    except ValueError:
        return None


def _print_progress(phase: str, record: dict[str, object], completed: int, total: int) -> None:
    """Compatibility renderer for callers that only provide completion events."""
    status = str(record.get("status") or "UNKNOWN")
    case_id = str(record.get("case_id") or "未知 Case")
    symbol = f"[{_result_label(phase, record)}]"
    suffix = f" ({record['error_code']})" if record.get("error_code") else ""
    print(f"  {symbol} {_phase_label(phase)} {completed}/{total}: {case_id} - {status}{suffix}")


def _print_outcome(outcome: object) -> None:
    from .models import RunOutcome

    if not isinstance(outcome, RunOutcome) or outcome.summary is None:
        return
    summary = outcome.summary
    metadata = runner.load_json_object(outcome.run_dir / "run.json")
    console = metadata.get("console", {})
    outcomes = run_case_outcomes(outcome.run_dir, metadata)
    origin_mode = str(metadata.get("origin_mode") or "")
    counts = {
        "PASS": sum(state == "PASS" for state in outcomes.values()),
        "FAIL": sum(state == "FAIL" for state in outcomes.values()),
        "ERROR": sum(state == "ERROR" for state in outcomes.values()),
        "INCOMPLETE": sum(state == "INCOMPLETE" for state in outcomes.values()),
    }
    summary_counts = summary.get("counts", {}) if isinstance(summary, dict) else {}
    selected = console.get("selected_cases", len(outcomes)) if isinstance(console, dict) else len(outcomes)
    total = console.get("total_eval_cases", selected) if isinstance(console, dict) else selected
    if origin_mode == EvalExecutionMode.TARGET_ONLY.value:
        target_counts = {
            "TARGET_SUCCESS": sum(state == "TARGET_SUCCESS" for state in outcomes.values()),
            "TARGET_ERROR": sum(state == "TARGET_ERROR" for state in outcomes.values()),
            "NOT_RUN": sum(state == "NOT_RUN" for state in outcomes.values()),
        }
        print("\nTarget 运行结果\n" + "-" * 40)
        print(f"SUCCESS（已生成模型回复）：{target_counts['TARGET_SUCCESS']}")
        print(f"ERROR（Target 执行错误）：{target_counts['TARGET_ERROR']}")
        print(f"NOT RUN（未执行）：{target_counts['NOT_RUN']}")
        print(f"Cases：{selected}")
        print(f"状态：{summary.get('completion_status')}")
        print(
            f"API 调用：Target {outcome.api_calls.get('target', 0)} / "
            f"Judge {outcome.api_calls.get('judge', 0)}"
        )
        _print_provider_telemetry(metadata)
        print(f"结果目录：{outcome.run_dir}")
        return
    print("\nBehavioral Eval 结果\n" + "-" * 40)
    print(f"PASS（通过）：{counts['PASS']}")
    print(f"FAIL（行为评测未通过）：{counts['FAIL']}")
    print(f"ERROR（执行错误）：{counts['ERROR']}")
    print(f"INCOMPLETE（未完成）：{counts['INCOMPLETE']}")
    print(f"Cases：本次选择 {selected} / Eval 共 {total}")
    print(f"状态：{summary.get('completion_status')}（已报告 {summary_counts.get('total_cases', selected)} 个 Cases）")
    _print_provider_telemetry(metadata)
    print(f"结果目录：{outcome.run_dir}")


def _print_provider_telemetry(metadata: dict[str, object]) -> None:
    history = metadata.get("execution_history")
    execution = history[-1] if isinstance(history, list) and history else None
    provider_telemetry = (
        execution.get("provider_telemetry") if isinstance(execution, dict) else None
    )
    if not isinstance(execution, dict):
        print("Provider HTTP：HTTP Attempts：无历史数据")
        return
    shown = False
    actual_api_calls = execution.get("actual_api_calls")
    for role, label in (("target", "Target"), ("judge", "Judge")):
        logical_calls = (
            actual_api_calls.get(role, 0)
            if isinstance(actual_api_calls, dict)
            and isinstance(actual_api_calls.get(role, 0), int)
            else 0
        )
        telemetry = (
            provider_telemetry.get(role)
            if isinstance(provider_telemetry, dict)
            else None
        )
        if telemetry is None and logical_calls == 0:
            continue
        shown = True
        coverage = (
            telemetry.get("logical_calls_with_http_telemetry", 0)
            if isinstance(telemetry, dict)
            else 0
        )
        print(f"{label} HTTP：")
        print(
            f"  Logical Calls: {logical_calls}；Telemetry Coverage: {coverage}/{logical_calls}；"
            f"HTTP Attempts: {telemetry.get('http_attempts', '无历史数据') if isinstance(telemetry, dict) else '无历史数据'}；"
            f"Retries: {telemetry.get('retries', 0) if isinstance(telemetry, dict) else 0}；"
            f"HTTP 429: {telemetry.get('rate_limit_responses', 0) if isinstance(telemetry, dict) else 0}"
        )
        retry_delay = telemetry.get("retry_delay_seconds") if isinstance(telemetry, dict) else 0.0
        retry_wait = float(retry_delay) if isinstance(retry_delay, (int, float)) else 0.0
        print(
            f"  Recovered After Retry: {telemetry.get('recovered_after_retry', 0) if isinstance(telemetry, dict) else 0}；"
            f"Rate-limit Exhausted: {telemetry.get('rate_limit_exhausted', 0) if isinstance(telemetry, dict) else 0}；"
            f"Selected Retry Delay: {retry_wait:.1f}s"
        )
    if not shown:
        print("Provider HTTP：HTTP Attempts：无历史数据")


def _print_evals(evals: list[EvalDefinition]) -> None:
    print("\n可用 Eval")
    for definition in evals:
        print(f"  {definition.eval_id}：{definition.title}（{len(definition.cases)} 个 Cases）")
        print(f"    {definition.description}")


def _print_cases(definition: EvalDefinition) -> None:
    print("\nCases")
    for index, case in enumerate(definition.cases, start=1):
        print(f"  {index:>2}. {case.case_id}  {case.title}")


def _print_history(runs: list[HistoricalRun]) -> None:
    print("\n历史运行")
    if not runs:
        print("  尚未找到结果产物。")
        return
    for index, run in enumerate(runs, start=1):
        if run.mode == EvalExecutionMode.TARGET_ONLY.value:
            print(
                f"  {index}. {run.run_id} - {run.total_cases} 个 Cases，"
                f"Target SUCCESS {run.target_successes}，Target ERROR {run.target_errors}，"
                f"NOT RUN {run.target_missing} [{run.state}]"
            )
            print(
                f"     初始方式={run.mode}；来源 Target={run.source_target_run_id or '当前 Run'}；"
                f"Target 模型={run.target_model or '未知'}；"
                f"API 调用 Target={run.target_api_calls} / Judge={run.judge_api_calls}"
            )
            _print_history_provider_telemetry(run)
            print(f"     {run.created_at or '时间未知'}  {run.run_dir}")
            continue
        passed = "?" if run.passed_cases is None else str(run.passed_cases)
        print(
            f"  {index}. {run.run_id} - {run.total_cases} 个 Cases，PASS {passed}，"
            f"FAIL {len(run.failed_case_ids)}，ERROR {len(run.error_case_ids)}，"
            f"INCOMPLETE {len(run.incomplete_case_ids)} [{run.state}]"
        )
        source = run.source_target_run_id or "当前 Run"
        print(
            f"     初始方式={run.mode}；来源 Target={source}；"
            f"Target 模型={run.target_model or '未知'}；"
            f"API 调用 Target={run.target_api_calls} / Judge={run.judge_api_calls}"
        )
        print(
            f"     Target：{run.target_successes} SUCCESS / {run.target_errors} ERROR / {run.target_missing} MISSING；"
            f"Judge：{run.judge_completed} DONE / {run.judge_errors} ERROR / {run.judge_missing} MISSING"
        )
        _print_history_provider_telemetry(run)
        print(f"     {run.created_at or '时间未知'}  {run.run_dir}")


def _print_history_provider_telemetry(run: HistoricalRun) -> None:
    items: list[str] = []
    if run.target_http_attempts is not None:
        items.append(
            f"Target HTTP Attempts {run.target_http_attempts}，429 {run.target_rate_limits or 0}，Retries {run.target_retries or 0}"
        )
    if run.judge_http_attempts is not None:
        items.append(
            f"Judge HTTP Attempts {run.judge_http_attempts}，429 {run.judge_rate_limits or 0}，Retries {run.judge_retries or 0}"
        )
    if items:
        print("     " + "；".join(items))


def _read_interactive_input(prompt: str) -> str:
    """Read a visible Console prompt without conflating EOF and Ctrl+C."""
    try:
        return input(prompt)
    except EOFError as exc:
        raise InteractiveInputClosed() from exc
    except KeyboardInterrupt as exc:
        raise InteractiveInputCancelled() from exc


def _read_interactive_secret(prompt: str) -> str:
    """Read a secret prompt with the same lifecycle semantics as visible input."""
    try:
        return getpass.getpass(prompt)
    except EOFError as exc:
        raise InteractiveInputClosed() from exc
    except KeyboardInterrupt as exc:
        raise InteractiveInputCancelled() from exc


def _choose(prompt: str, choices: list[tuple[str, T]]) -> T:
    print(f"\n{prompt}:")
    for number, (label, _) in enumerate(choices, start=1):
        print(f"  {number}. {label}")
    while True:
        value = _read_interactive_input("请输入编号：").strip()
        if value.isdigit() and 1 <= int(value) <= len(choices):
            return choices[int(value) - 1][1]
        print(f"请输入 1 到 {len(choices)} 之间的编号。")


def _yes_no(prompt: str, *, default: bool) -> bool:
    suffix = "[是/否，默认是]" if default else "[是/否，默认否]"
    while True:
        value = _read_interactive_input(f"{prompt} {suffix}: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes", "是"}:
            return True
        if value in {"n", "no", "否"}:
            return False
        print("请输入 是 或 否。")


def _profile_label(profile: ProviderProfile, role: str) -> str:
    model = profile.target_model if role == "target" else profile.judge_model
    return f"{profile.name} - Provider：{profile.provider or '按角色配置'}，模型：{model or '将在预检查时解析'}"


def _run_label(run: HistoricalRun) -> str:
    if run.mode == EvalExecutionMode.TARGET_ONLY.value:
        return (
            f"{run.run_id} [初始 {run.mode}]（Target SUCCESS {run.target_successes}，"
            f"Target ERROR {run.target_errors}，NOT RUN {run.target_missing}）"
        )
    return (
        f"{run.run_id} [初始 {run.mode}]（FAIL {len(run.failed_case_ids)}，"
        f"ERROR {len(run.error_case_ids)}，INCOMPLETE {len(run.incomplete_case_ids)}）"
    )


def _validation_args(
    profiles_file: Path, results_root: Path, debug: bool
) -> argparse.Namespace:
    return argparse.Namespace(
        profiles_file=profiles_file,
        results_root=results_root,
        target_profile=None,
        judge_profile=None,
        debug=debug,
    )
