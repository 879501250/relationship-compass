"""Validation and orchestration layer above the existing model-eval runner."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .discovery import discover_evals, discover_provider_profiles, find_eval
from .models import EvalRunRequest, RunOutcome, ValidationReport
from .runner_adapter import runner
from .secrets import SecretResolver


ProgressCallback = Callable[[str, dict[str, Any], int, int], None]
ActivityCallback = Callable[[str, dict[str, Any], int, int], None]


class EvalConsoleError(RuntimeError):
    """An actionable Console error that should not show a traceback by default."""


class EvaluationInterrupted(EvalConsoleError):
    """Carries the artifact path after Ctrl+C interrupted a persisted run."""

    def __init__(self, run_dir: Path, completed_cases: int, total_cases: int) -> None:
        remaining_cases = max(0, total_cases - completed_cases)
        super().__init__(
            f"评测在 Target 完成 {completed_cases}/{total_cases} 个 Case 后中断；"
            f"还剩 {remaining_cases} 个。部分结果位于 {run_dir}。"
        )
        self.run_dir = run_dir
        self.completed_cases = completed_cases
        self.total_cases = total_cases


def validate_configuration(
    profiles_file: Path, results_root: Path | None = None
) -> ValidationReport:
    """Validate discoverable eval definitions and local profile structure offline."""
    checks: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    try:
        evals = discover_evals()
        case_count = sum(len(definition.cases) for definition in evals)
        checks.append(f"Eval：已发现 {len(evals)} 个，已校验 {case_count} 个 Cases")
        checks.append("Case ID：唯一性与 runner schema 已校验")
    except (OSError, ValueError, runner.ModelEvalError) as exc:
        errors.append(f"Eval 定义：{exc}")
    if not profiles_file.is_file():
        warnings.append(
            f"Provider 配置：未找到 {profiles_file}；真实 API 运行前请使用控制台完成首次配置"
        )
    else:
        try:
            profiles = discover_provider_profiles(profiles_file)
            target_count = sum(profile.supports_target for profile in profiles)
            judge_count = sum(profile.supports_judge for profile in profiles)
            checks.append(
                f"Provider 配置：找到 {len(profiles)} 个 Profile；Target={target_count}，Judge={judge_count}"
            )
            if not target_count or not judge_count:
                warnings.append("Profile 尚未同时提供 Target 与 Judge 角色")
        except (OSError, ValueError, runner.ModelEvalError) as exc:
            errors.append(f"Provider 配置：{exc}")
    output_path = (results_root or runner.RESULTS_BASE).expanduser()
    if output_path.exists() and not output_path.is_dir():
        errors.append(f"结果输出目录：{output_path} 不是目录")
    else:
        writable_parent = output_path
        while not writable_parent.exists() and writable_parent != writable_parent.parent:
            writable_parent = writable_parent.parent
        if os.access(writable_parent, os.W_OK):
            checks.append(
                f"结果输出目录：{output_path} 可创建或写入"
            )
        else:
            errors.append(f"结果输出目录：{writable_parent} 不可写")
    return ValidationReport(tuple(checks), tuple(warnings), tuple(errors))


def validate_request(request: EvalRunRequest) -> None:
    """Check Console-specific selections before provider preflight or API work."""
    try:
        definition = find_eval(request.eval_id)
    except ValueError as exc:
        raise EvalConsoleError(str(exc)) from exc
    available_ids = {case.case_id for case in definition.cases}
    if not request.case_ids:
        raise EvalConsoleError("运行 Eval 前至少选择一个 Case。")
    unknown = [case_id for case_id in request.case_ids if case_id not in available_ids]
    if unknown:
        raise EvalConsoleError(
            "未知的 Case ID：" + ", ".join(sorted(unknown)) + "。"
        )
    if len(set(request.case_ids)) != len(request.case_ids):
        raise EvalConsoleError("每个 Case 只能选择一次。")
    if request.concurrency < 1 or request.concurrency > 32:
        raise EvalConsoleError("并发数必须介于 1 到 32 之间。")
    profiles = {profile.name: profile for profile in discover_provider_profiles(request.profiles_file)}
    target = profiles.get(request.target_profile)
    judge = profiles.get(request.judge_profile)
    if target is None:
        raise EvalConsoleError(
            _missing_profile_message(request.target_profile, request.profiles_file, profiles)
        )
    if judge is None:
        raise EvalConsoleError(
            _missing_profile_message(request.judge_profile, request.profiles_file, profiles)
        )
    if not target.supports_target:
        raise EvalConsoleError(f"Profile {target.name!r} 未定义 Target 配置。")
    if not judge.supports_judge:
        raise EvalConsoleError(f"Profile {judge.name!r} 未定义 Judge 配置。")


def preflight_request(request: EvalRunRequest) -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    """Resolve both providers using existing runner preflight logic and no API calls."""
    SecretResolver(runner.ROOT / ".env.local").prepare_environment()
    validate_request(request)
    try:
        target = _create_profile_provider(
            request, "target", request.target_profile, request.target_model_override
        )
        judge = _create_profile_provider(
            request, "judge", request.judge_profile, request.judge_model_override
        )
        target_plan = runner.provider_execution_plan(
            target,
            role="target",
            case_count=len(request.case_ids),
            runtime_profile=runner.API_RUNTIME_PROFILE,
        )
        judge_plan = runner.provider_execution_plan(
            judge,
            role="judge",
            case_count=len(request.case_ids),
            runtime_profile=runner.API_RUNTIME_PROFILE,
        )
        return target, judge, target_plan, judge_plan
    except (OSError, ValueError, runner.ModelEvalError) as exc:
        raise EvalConsoleError(friendly_error(exc)) from exc


def execute_request(
    request: EvalRunRequest,
    *,
    progress: ProgressCallback | None = None,
    activity: ActivityCallback | None = None,
    target_provider: Any | None = None,
    judge_provider: Any | None = None,
) -> RunOutcome:
    """Run only the selected cases through the existing target → judge → report pipeline."""
    definition = find_eval(request.eval_id)
    if target_provider is None or judge_provider is None:
        target_provider, judge_provider, target_plan, judge_plan = preflight_request(request)
    else:
        validate_request(request)
        target_plan = runner.provider_execution_plan(
            target_provider,
            role="target",
            case_count=len(request.case_ids),
            runtime_profile=runner.API_RUNTIME_PROFILE,
        )
        judge_plan = runner.provider_execution_plan(
            judge_provider,
            role="judge",
            case_count=len(request.case_ids),
            runtime_profile=runner.API_RUNTIME_PROFILE,
        )
    selected_cases, criteria = runner.load_definitions()
    wanted = set(request.case_ids)
    selected_cases = [case for case in selected_cases if case["id"] in wanted]
    if len(selected_cases) != len(request.case_ids):
        raise EvalConsoleError("已选择的 Cases 与当前 Eval 定义不再匹配。")
    prepared = runner.prepare_cases(selected_cases, criteria)
    run_id = runner.validate_run_id(request.run_id or f"console-{runner.run_id_now()}")
    run_dir = (
        runner.results_root(
            runner.prepared_version(prepared), request.results_root, runner.API_RUNTIME_PROFILE
        )
        / run_id
    )
    if request.dry_run:
        return RunOutcome(run_dir, True, None, None, target_plan, judge_plan)

    metadata_extra = {
        "console": {
            "schema_version": 1,
            "eval_id": definition.eval_id,
            "selected_case_ids": [record["case_id"] for record in prepared],
            "selected_cases": len(prepared),
            "total_eval_cases": len(definition.cases),
            "target_profile": request.target_profile,
            "judge_profile": request.judge_profile,
        }
    }

    def on_progress(phase: str, record: dict[str, Any], completed: int, total: int) -> None:
        _append_log(run_dir, request, phase, record, completed, total)
        if progress is not None:
            progress(phase, record, completed, total)

    def on_activity(phase: str, record: dict[str, Any], started: int, total: int) -> None:
        if activity is not None:
            activity(phase, record, started, total)

    try:
        metadata = runner.execute_run(
            prepared,
            target_provider,
            run_dir,
            allow_dirty_debug=request.allow_dirty_debug,
            concurrency=request.concurrency,
            continue_on_error=request.continue_on_error,
            metadata_extra=metadata_extra,
            on_case_start=lambda record, started, total: on_activity(
                "TARGET", record, started, total
            ),
            on_case_complete=lambda record, completed, total: on_progress(
                "TARGET", record, completed, total
            ),
        )
        runner.execute_judge(
            run_dir,
            judge_provider,
            on_case_start=lambda record, started, total: on_activity(
                "JUDGE", record, started, total
            ),
            on_case_complete=lambda record, completed, total: on_progress(
                "JUDGE", record, completed, total
            ),
        )
        summary = runner.build_report(run_dir)
        _append_log(
            run_dir,
            request,
            "SUMMARY",
            {"status": summary["behavioral_status"]},
            1,
            1,
        )
        metadata = runner.load_json_object(run_dir / "run.json")
        return RunOutcome(run_dir, False, summary, metadata, target_plan, judge_plan)
    except KeyboardInterrupt as exc:
        response_records = (
            runner.load_jsonl(run_dir / "responses.jsonl")
            if (run_dir / "responses.jsonl").is_file()
            else []
        )
        completed = len(
            runner.index_response_attempts(
                response_records, {record["case_id"] for record in prepared}
            )
        )
        raise EvaluationInterrupted(run_dir, completed, len(prepared)) from exc
    except (OSError, ValueError, runner.ModelEvalError) as exc:
        raise EvalConsoleError(friendly_error(exc)) from exc


def failed_case_ids(run_dir: Path, mode: str) -> tuple[str, ...]:
    """Return selected non-passing case IDs, including incomplete cases by default."""
    from .discovery import discover_runs

    selected = next((item for item in discover_runs(run_dir.parents[2]) if item.run_dir == run_dir), None)
    if selected is None:
        raise EvalConsoleError(f"无法读取历史 Eval 运行：{run_dir}。")
    values: list[str] = []
    if mode in {"failed", "failed-and-errors"}:
        values.extend(selected.failed_case_ids)
    if mode in {"errors", "failed-and-errors"}:
        values.extend(selected.error_case_ids)
    if mode in {"incomplete", "failed-and-errors"}:
        values.extend(selected.incomplete_case_ids)
    resolved = tuple(dict.fromkeys(values))
    if not resolved:
        raise EvalConsoleError("所选历史运行没有符合该重跑范围的 Case。")
    return resolved


def friendly_error(error: BaseException) -> str:
    """Translate common runner failures into non-developer-oriented next actions."""
    message = str(error)
    if "provider profile" in message and "was not found" in message:
        return message + " 请在“配置 Provider”中选择可用 Profile，或创建新的本地 Profile。"
    if "environment variable" in message and "is not set" in message:
        return message + " 可在控制台中输入 API Key，或在 shell 中设置该环境变量后重新启动。"
    if "requires a clean Git worktree" in message:
        return message + " 仅调试时可使用 --allow-dirty-debug；该运行不能作为正式参考。"
    return message


def _create_profile_provider(
    request: EvalRunRequest, role: str, profile: str, model_override: str | None = None
) -> Any:
    arguments = [
        "provider-check",
        "--role",
        role,
        "--profile",
        profile,
        "--profiles-file",
        str(request.profiles_file),
    ]
    if model_override:
        arguments.extend(("--model", model_override))
    args = runner.build_parser().parse_args(arguments)
    if role == "judge":
        args.model_env = "OPENAI_JUDGE_MODEL"
    return runner.create_provider(args, role=role)


def _missing_profile_message(name: str, profiles_file: Path, profiles: dict[str, Any]) -> str:
    available = ", ".join(sorted(profiles)) or "无"
    return (
        f"未找到 Provider Profile：{name!r}。\n"
        f"配置文件：{profiles_file}\n"
        f"可用 Profile：{available}\n"
        "请选择一个有效 Profile。"
    )


def _append_log(
    run_dir: Path,
    request: EvalRunRequest,
    phase: str,
    record: dict[str, Any],
    completed: int,
    total: int,
) -> None:
    judge = record.get("judge") if isinstance(record.get("judge"), dict) else {}
    payload = {
        "timestamp": runner.utc_now(),
        "run_id": run_dir.name,
        "eval_id": request.eval_id,
        "phase": phase,
        "completed": completed,
        "total": total,
        "case_id": record.get("case_id"),
        "status": record.get("status"),
        "provider": record.get("provider") or judge.get("provider"),
        "model": record.get("requested_model") or record.get("model") or judge.get("requested_model"),
        "judge_profile": request.judge_profile,
        "duration_seconds": _record_duration(record),
        "error_code": record.get("error_code"),
    }
    with (run_dir / "run.log").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _record_duration(record: dict[str, Any]) -> float | None:
    start = record.get("started_at") or record.get("evaluated_at")
    end = record.get("completed_at")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        return max(
            0.0,
            (datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds(),
        )
    except ValueError:
        return None
