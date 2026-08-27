"""评测控制台的中文交互入口与兼容命令行入口。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable, TypeVar

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
    role_configuration,
    update_role_configuration,
    validate_base_url,
)
from .models import EvalDefinition, EvalRunRequest, ProviderProfile
from .runner_adapter import runner
from .secrets import SecretResolver
from .selection import CaseSelectionError, parse_case_selection
from .service import (
    EvalConsoleError,
    EvaluationInterrupted,
    execute_request,
    failed_case_ids,
    preflight_request,
    validate_configuration,
)
from .test_runner import TerminalTestReporter, TestSuiteRequest, TestSuiteRunner


T = TypeVar("T")


class _ActivityReporter:
    """Render API wait state without polluting the persisted JSONL event log."""

    def __init__(self) -> None:
        self._stream = sys.stdout
        self._tty = bool(getattr(self._stream, "isatty", lambda: False)())
        self._lock = threading.Lock()
        self._active: tuple[str, str, int, int, float] | None = None
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, phase: str, record: dict[str, object], started: int, total: int) -> None:
        case_id = str(record.get("case_id") or "未知 Case")
        with self._lock:
            self._active = (phase, case_id, started, total, time.monotonic())
        if not self._tty:
            print(f"  [开始] {case_id} {_phase_label(phase)}")
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()

    def finish(self, phase: str, record: dict[str, object], completed: int, total: int) -> None:
        case_id = str(record.get("case_id") or "unknown case")
        with self._lock:
            active = self._active
            self._active = None
        duration = _duration_seconds(record)
        if duration is None and active is not None:
            duration = max(0.0, time.monotonic() - active[4])
        label = _result_label(phase, record)
        elapsed = f"{duration:.1f} 秒" if duration is not None else "耗时未知"
        if self._tty:
            print("\r" + " " * 100 + "\r", end="")
            print(f"  [{label}] {_phase_label(phase)} {completed}/{total}: {case_id}（{elapsed}）")
        else:
            print(f"  [完成] {case_id} {_phase_label(phase)} {elapsed} - {label}")

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
                active = self._active
            if active is None:
                continue
            phase, case_id, started, total, started_at = active
            verb = "正在生成模型回复..." if phase == "TARGET" else "正在评审回复..."
            elapsed = time.monotonic() - started_at
            print(
                f"\r  [{frames[frame % len(frames)]}] {_phase_label(phase)} {started}/{total}: {case_id} - {verb} 已用时：{elapsed:.1f} 秒",
                end="",
                flush=True,
            )
            frame += 1


def build_parser() -> argparse.ArgumentParser:
    """Build the small command surface; invoking no command opens the wizard."""
    parser = argparse.ArgumentParser(
        description="Relationship Compass 评测控制台 V1.1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="不打开向导，直接运行选定的 Case")
    _add_run_arguments(run)
    run.set_defaults(func=_command_run)

    retry = subparsers.add_parser("rerun-failed", help="重跑历史运行中的失败、错误或未完成 Case")
    retry.add_argument("--from-run", required=True, type=Path)
    retry.add_argument(
        "--mode",
        choices=("failed-and-errors", "failed", "errors", "incomplete"),
        default="failed-and-errors",
    )
    _add_run_arguments(retry, include_eval=False, include_cases=False)
    retry.set_defaults(func=_command_rerun_failed)

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
            f"Target 进度：已保存 {exc.completed_cases}/{exc.total_cases}；"
            f"剩余 {max(0, exc.total_cases - exc.completed_cases)}。"
        )
        print("打开评测控制台并选择“重跑失败 / 错误 / 未完成 Cases”以继续。")
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
) -> None:
    if include_eval:
        parser.add_argument("eval_id", help="可通过 'python -m eval_console interactive' 查看的 Eval ID")
    if include_cases:
        cases = parser.add_mutually_exclusive_group()
        cases.add_argument("--case", action="append", help="一个 Case ID 或从 1 开始的位置；可重复")
        cases.add_argument("--cases", help="all、位置、ID、范围或组合，例如 1,3,5-8")
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
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--debug", action="store_true")


def _command_run(args: argparse.Namespace) -> int:
    definition = find_eval(args.eval_id)
    case_ids = _case_ids_from_args(args, definition)
    request = _request_from_args(args, definition.eval_id, case_ids)
    return _execute_and_print(request)


def _command_rerun_failed(args: argparse.Namespace) -> int:
    run_dir = args.from_run.expanduser().resolve()
    case_ids = failed_case_ids(run_dir, args.mode)
    metadata = runner.load_json_object(run_dir / "run.json")
    console = metadata.get("console") if isinstance(metadata.get("console"), dict) else {}
    eval_id = console.get("eval_id") if isinstance(console.get("eval_id"), str) else discover_evals()[0].eval_id
    args.target_profile = args.target_profile or args.profile or console.get("target_profile")
    args.judge_profile = args.judge_profile or args.profile or console.get("judge_profile")
    if not args.target_profile or not args.judge_profile:
        raise EvalConsoleError(
            "This historical run has no Console profile metadata. Supply --target-profile and --judge-profile."
        )
    request = _request_from_args(args, eval_id, list(case_ids))
    return _execute_and_print(request)


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
            ("重跑失败 / 错误 / 未完成 Cases", lambda: _interactive_rerun_failed(profiles_file, results_root, debug)),
            (
                "查看 Eval 列表",
                lambda: _print_evals(evals),
            ),
            ("退出", lambda: 0),
        ]
        try:
            choice = _choose(
                "请选择操作", [(label, (label, action)) for label, action in options]
            )
            if choice[0] == "退出":
                return 0
            result = choice[1]()
        except KeyboardInterrupt:
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
    print("\nRelationship Compass\n评测控制台 V1.1\n" + "-" * 40)
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
    details = role_configuration(profiles_file, selected.name, role)
    model = details.get("model") or (
        os.environ.get(details["model_env"]) if isinstance(details.get("model_env"), str) else None
    )
    base_url = details.get("base_url")
    if required and not model:
        model = _prompt_required("模型名称")
        update_role_configuration(profiles_file, selected.name, role, model=model)
    if required and not base_url:
        base_url = _prompt_url("API Base URL")
        update_role_configuration(profiles_file, selected.name, role, base_url=base_url)
    api_key_env = details.get("api_key_env")
    if isinstance(api_key_env, str) and api_key_env and not resolver.has(api_key_env):
        _configure_secret(resolver, api_key_env)
    if not required:
        _edit_profile_menu(profiles_file, selected, role, resolver)
    return selected


def _create_profile_interactively(
    profiles_file: Path, role: str, resolver: SecretResolver
) -> ProviderProfile:
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
    profile_name = create_profile(
        profiles_file, name=name, provider=provider, role=role, model=model, base_url=base_url
    )
    profile = next(item for item in discover_provider_profiles(profiles_file) if item.name == profile_name)
    details = role_configuration(profiles_file, profile_name, role)
    api_key_env = details.get("api_key_env")
    if isinstance(api_key_env, str):
        _configure_secret(resolver, api_key_env)
    return profile


def _edit_profile_menu(
    profiles_file: Path, profile: ProviderProfile, role: str, resolver: SecretResolver
) -> None:
    while True:
        choice = _choose(
            f"{role.title()} Profile：{profile.name}",
            [
                ("保留当前配置", "keep"),
                ("修改模型", "model"),
                ("修改 API Base URL", "base_url"),
                ("配置 API Key", "key"),
                ("返回", "back"),
            ],
        )
        if choice in {"keep", "back"}:
            return
        if choice == "model":
            details = role_configuration(profiles_file, profile.name, role)
            update_role_configuration(
                profiles_file,
                profile.name,
                role,
                model=_prompt_with_current("模型名称", details.get("model")),
            )
        elif choice == "base_url":
            details = role_configuration(profiles_file, profile.name, role)
            update_role_configuration(
                profiles_file,
                profile.name,
                role,
                base_url=_prompt_url_with_current("API Base URL", details.get("base_url")),
            )
        else:
            details = role_configuration(profiles_file, profile.name, role)
            env_name = details.get("api_key_env")
            if isinstance(env_name, str):
                _configure_secret(resolver, env_name)
            else:
                print("该 Profile 未声明 API Key 环境变量名。")


def _configure_secret(resolver: SecretResolver, env_name: str) -> None:
    from getpass import getpass

    print(f"未检测到 API Key：{env_name}")
    print("请输入 API Key（输入内容不会显示）：")
    value = getpass("> ")
    if not value.strip():
        print("未修改 API Key。")
        return
    mode = _choose(
        "如何使用这个 API Key",
        [("仅本次会话使用", "session"), ("保存到本地，供以后使用", "local")],
    )
    if mode == "local":
        resolver.save_local(env_name, value)
        print("API Key 已保存到 .env.local；该文件已被 Git 忽略，不会提交到仓库。")
    else:
        resolver.set_session(env_name, value)
        print("API Key 已配置为仅本次 Console 会话使用。")


def _print_provider_configuration(profiles_file: Path, resolver: SecretResolver) -> None:
    profiles = discover_provider_profiles(profiles_file)
    print("\n当前 Provider 配置")
    for profile in profiles:
        print(f"  {profile.name}")
        for role, enabled in (("target", profile.supports_target), ("judge", profile.supports_judge)):
            if not enabled:
                continue
            details = role_configuration(profiles_file, profile.name, role)
            configured = resolver.has(details.get("api_key_env"))
            print(
                f"    {role.title()}：模型={details.get('model') or details.get('model_env') or '缺失'}，"
                f"API Base URL={details.get('base_url') or details.get('base_url_env') or '缺失'}，"
                f"API Key={'已配置' if configured else '缺失'}"
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
    if not model:
        print(f"{role.title()} Profile 尚未配置模型，现在完成配置。")
        update_role_configuration(profiles_file, profile.name, role, model=_prompt_required("模型名称"))
    base_url = details.get("base_url") or (
        os.environ.get(details["base_url_env"])
        if isinstance(details.get("base_url_env"), str)
        else None
    )
    if not base_url:
        print(f"{role.title()} Profile 尚未配置 API Base URL，现在完成配置。")
        update_role_configuration(profiles_file, profile.name, role, base_url=_prompt_url("API Base URL"))
    env_name = details.get("api_key_env")
    if not isinstance(env_name, str) or not env_name:
        print(f"{role.title()} Profile 缺少 API Key 环境变量名。")
        return False
    if not resolver.has(env_name):
        _configure_secret(resolver, env_name)
    return resolver.has(env_name)


def _interactive_concurrency() -> int:
    selected = _choose(
        "并发数",
        [("1（推荐）", 1), ("2", 2), ("4", 4), ("自定义", "custom")],
    )
    if selected != "custom":
        return int(selected)
    while True:
        value = input("请输入并发数（1-32）：").strip()
        if value.isdigit() and 1 <= int(value) <= 32:
            return int(value)
        print("并发数必须介于 1 到 32 之间。")


def _prompt_required(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print(f"{label}不能为空。")


def _prompt_with_current(label: str, current: object) -> str:
    value = str(current).strip() if isinstance(current, str) else ""
    if not value:
        return _prompt_required(label)
    print(f"{label}\n当前值：{value}\n直接回车保留当前值，或输入新的值：")
    return input("> ").strip() or value


def _prompt_url(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
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
        candidate = input("> ").strip() or value
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
    concurrency = _interactive_concurrency()
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
        concurrency=concurrency,
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
    concurrency: int,
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
        concurrency=concurrency,
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
    print(f"并发数：{request.concurrency}")
    print(f"错误处理：{'继续运行' if request.continue_on_error else '立即停止'}")
    print(f"运行模式：{'Dry Run（不调用真实 API）' if request.dry_run else '真实 API 运行'}")
    if request.dry_run:
        print("预计 API 调用：0（Dry Run；真实运行预计约 " + str(estimated_real_calls) + " 次）")
    else:
        print(f"预计 API 调用：约 {estimated_real_calls} 次")
    print(f"Git 状态：{_git_workspace_state(runner.ROOT)}")
    print(f"输出位置：{request.results_root}")


def _interactive_case_selection(definition: EvalDefinition, results_root: Path) -> list[str]:
    modes = [
        ("全部 Cases", "all"),
        ("单个 Case", "single"),
        ("选择多个 Cases", "multiple"),
        ("Case 范围", "range"),
        ("上次运行失败的 Cases", "failed"),
        ("上次运行错误的 Cases", "errors"),
        ("上次运行未完成的 Cases", "incomplete"),
    ]
    mode = _choose("请选择要运行的 Cases", modes)
    if mode == "all":
        return [case.case_id for case in definition.cases]
    if mode in {"failed", "errors", "incomplete"}:
        candidates = [
            run for run in discover_runs(results_root)
            if run.eval_id in {None, definition.eval_id}
            and (run.failed_case_ids or run.error_case_ids or run.incomplete_case_ids)
        ]
        if not candidates:
            raise EvalConsoleError("该 Eval 没有包含 FAIL、ERROR 或 INCOMPLETE Case 的历史运行。")
        run = _choose("请选择历史运行", [(_run_label(item), item) for item in candidates])
        return list(failed_case_ids(run.run_dir, mode))
    _print_cases(definition)
    if mode == "single":
        value = input("\n请输入一个 Case 位置或 Case ID：").strip()
    elif mode == "multiple":
        print("\n请输入位置或 ID，例如 1,3,5-8。输入 all 选择全部；输入 clear 重新选择。")
        while True:
            value = input("选择：").strip()
            if value.lower() == "clear":
                print("已清空选择，请重新输入要运行的 Cases。")
                continue
            break
    else:
        value = input("\n请输入范围或组合，例如 1-10 或 1,3,5-10：").strip()
    selected = parse_case_selection(value, [case.case_id for case in definition.cases])
    print("\n已选择的 Cases：")
    for case_id in selected:
        print(f"  {case_id}")
    print(f"总计：{len(selected)} 个 Cases")
    return selected


def _interactive_profiles(profiles_file: Path) -> tuple[ProviderProfile, ProviderProfile]:
    profiles = discover_provider_profiles(profiles_file)
    targets = [profile for profile in profiles if profile.supports_target]
    judges = [profile for profile in profiles if profile.supports_judge]
    if not targets or not judges:
        raise EvalConsoleError(
            f"未找到可用的 Target / Judge Profile：{profiles_file}。"
            "请在控制台中选择“配置 Provider”。"
        )
    target = _choose(
        "请选择 Target 模型 / Provider",
        [(_profile_label(profile, "target"), profile) for profile in targets],
    )
    judge = _choose(
        "请选择 Judge",
        [(_profile_label(profile, "judge"), profile) for profile in judges],
    )
    return target, judge


def _interactive_rerun_failed(profiles_file: Path, results_root: Path, debug: bool) -> int:
    candidates = [
        run
        for run in discover_runs(results_root)
        if run.failed_case_ids or run.error_case_ids or run.incomplete_case_ids
    ]
    if not candidates:
        print("未找到包含 FAIL、ERROR 或 INCOMPLETE Case 的历史运行。")
        return 0
    selected = _choose("请选择历史运行", [(_run_label(item), item) for item in candidates])
    mode = _choose(
        "重跑范围",
        [
            ("FAIL + ERROR + INCOMPLETE Cases", "failed-and-errors"),
            ("仅 FAIL Cases", "failed"),
            ("仅 ERROR Cases", "errors"),
            ("仅 INCOMPLETE Cases", "incomplete"),
        ],
    )
    case_ids = failed_case_ids(selected.run_dir, mode)
    definition = find_eval(selected.eval_id or discover_evals()[0].eval_id)
    target, judge = _interactive_profiles(profiles_file)
    request = EvalRunRequest(
        eval_id=definition.eval_id,
        case_ids=case_ids,
        target_profile=target.name,
        judge_profile=judge.name,
        profiles_file=profiles_file,
        results_root=results_root,
        allow_dirty_debug=_yes_no("如有需要，允许在有未提交修改的工作区进行调试运行", default=False),
        debug=debug,
    )
    return _execute_and_print(request)


def _execute_and_print(request: EvalRunRequest) -> int:
    target, judge, target_plan, judge_plan = preflight_request(request)
    print("\n运行前检查")
    print(f"  [通过] Target：{target_plan['provider']} / {target_plan['requested_model']}")
    print(f"  [通过] Judge：{judge_plan['provider']} / {judge_plan['requested_model']}")
    print(f"  [通过] Cases：已选择 {len(request.case_ids)} 个")
    if request.dry_run:
        outcome = execute_request(request, target_provider=target, judge_provider=judge)
        print("\nDRY RUN 完成：配置有效，未调用真实 API。")
        print(f"预计执行：{len(request.case_ids)} 个 Cases；真实运行预计约 {len(request.case_ids) * 2} 次 API 调用")
        print(f"输出位置：{outcome.run_dir}")
        return 0
    print("\n正在运行……每个 Case 完成后都会保存进度。")
    activity = _ActivityReporter()
    try:
        outcome = execute_request(
            request,
            target_provider=target,
            judge_provider=judge,
            progress=activity.finish,
            activity=activity.start,
        )
    finally:
        activity.close()
    _print_outcome(outcome)
    return 0 if outcome.summary and outcome.summary.get("completion_status") == "COMPLETED" else 2


def _case_ids_from_args(args: argparse.Namespace, definition: EvalDefinition) -> list[str]:
    available = [case.case_id for case in definition.cases]
    if args.cases:
        return parse_case_selection(args.cases, available)
    if args.case:
        return parse_case_selection(",".join(args.case), available)
    return available


def _request_from_args(args: argparse.Namespace, eval_id: str, case_ids: list[str]) -> EvalRunRequest:
    target_profile = args.target_profile or args.profile
    judge_profile = args.judge_profile or args.profile
    if not target_profile or not judge_profile:
        raise EvalConsoleError(
            "Choose profiles with --profile, or provide both --target-profile and --judge-profile."
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
        concurrency=args.concurrency,
        run_id=args.run_id,
        target_model_override=getattr(args, "target_model", None),
        judge_model_override=getattr(args, "judge_model", None),
        continue_on_error=not getattr(args, "stop_on_error", False),
    )


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
    counts = {
        "PASS": sum(state == "PASS" for state in outcomes.values()),
        "FAIL": sum(state == "FAIL" for state in outcomes.values()),
        "ERROR": sum(state == "ERROR" for state in outcomes.values()),
        "INCOMPLETE": sum(state == "INCOMPLETE" for state in outcomes.values()),
    }
    summary_counts = summary.get("counts", {}) if isinstance(summary, dict) else {}
    selected = console.get("selected_cases", len(outcomes)) if isinstance(console, dict) else len(outcomes)
    total = console.get("total_eval_cases", selected) if isinstance(console, dict) else selected
    print("\nEval 运行完成\n" + "-" * 40)
    print(f"PASS（通过）：{counts['PASS']}")
    print(f"FAIL（行为评测未通过）：{counts['FAIL']}")
    print(f"ERROR（执行错误）：{counts['ERROR']}")
    print(f"INCOMPLETE（未完成）：{counts['INCOMPLETE']}")
    print(f"Cases：本次选择 {selected} / Eval 共 {total}")
    print(f"状态：{summary.get('completion_status')}（已报告 {summary_counts.get('total_cases', selected)} 个 Cases）")
    print(f"结果目录：{outcome.run_dir}")


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
        passed = "?" if run.passed_cases is None else str(run.passed_cases)
        print(
            f"  {index}. {run.run_id} - {run.total_cases} 个 Cases，PASS {passed}，"
            f"FAIL {len(run.failed_case_ids)}，ERROR {len(run.error_case_ids)}，"
            f"INCOMPLETE {len(run.incomplete_case_ids)} [{run.state}]"
        )
        print(f"     {run.created_at or '时间未知'}  {run.run_dir}")


def _choose(prompt: str, choices: list[tuple[str, T]]) -> T:
    print(f"\n{prompt}:")
    for number, (label, _) in enumerate(choices, start=1):
        print(f"  {number}. {label}")
    while True:
        value = input("请输入编号：").strip()
        if value.isdigit() and 1 <= int(value) <= len(choices):
            return choices[int(value) - 1][1]
        print(f"请输入 1 到 {len(choices)} 之间的编号。")


def _yes_no(prompt: str, *, default: bool) -> bool:
    suffix = "[是/否，默认是]" if default else "[是/否，默认否]"
    while True:
        value = input(f"{prompt} {suffix}: ").strip().lower()
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
    return (
        f"{run.run_id}（FAIL {len(run.failed_case_ids)}，"
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
