"""Validation and orchestration layer above the existing model-eval runner."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .discovery import discover_evals, discover_provider_profiles, find_eval
from .models import (
    CURRENT_CONSOLE_SCHEMA_VERSION,
    CaseStagePlan,
    EvalExecutionMode,
    EvalRunRequest,
    JudgeCaseSelector,
    RunOutcome,
    StagePlan,
    ValidationReport,
)
from .runner_adapter import runner
from .secrets import SecretResolver


ProgressCallback = Callable[[str, dict[str, Any], int, int], None]
ActivityCallback = Callable[[str, dict[str, Any], int, int], None]
StopRequested = Callable[[], bool]


class EvalConsoleError(RuntimeError):
    """An actionable Console error that should not show a traceback by default."""


class EvaluationInterrupted(EvalConsoleError):
    """Carries the artifact path after Ctrl+C interrupted a persisted run."""

    def __init__(
        self, run_dir: Path, stage: str, completed_cases: int, total_cases: int
    ) -> None:
        remaining_cases = max(0, total_cases - completed_cases)
        super().__init__(
            f"评测在 {stage} 阶段完成 {completed_cases}/{total_cases} 个 Case 后中断；"
            f"还剩 {remaining_cases} 个。部分结果位于 {run_dir}。"
        )
        self.run_dir = run_dir
        self.stage = stage
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
    if request.target_concurrency < 1 or request.target_concurrency > 32:
        raise EvalConsoleError("Target 并发数必须介于 1 到 32 之间。")
    if request.judge_concurrency < 1 or request.judge_concurrency > 32:
        raise EvalConsoleError("Judge 并发数必须介于 1 到 32 之间。")
    profiles = {profile.name: profile for profile in discover_provider_profiles(request.profiles_file)}
    needs_target = request.mode in {EvalExecutionMode.FULL, EvalExecutionMode.TARGET_ONLY}
    needs_judge = request.mode in {EvalExecutionMode.FULL, EvalExecutionMode.JUDGE_ONLY}
    if needs_target:
        target = profiles.get(request.target_profile)
        if target is None:
            raise EvalConsoleError(
                _missing_profile_message(request.target_profile, request.profiles_file, profiles)
            )
        if not target.supports_target:
            raise EvalConsoleError(f"Profile {target.name!r} 未定义 Target 配置。")
    if needs_judge:
        judge = profiles.get(request.judge_profile)
        if judge is None:
            raise EvalConsoleError(
                _missing_profile_message(request.judge_profile, request.profiles_file, profiles)
            )
        if not judge.supports_judge:
            raise EvalConsoleError(f"Profile {judge.name!r} 未定义 Judge 配置。")
    if request.mode in {EvalExecutionMode.JUDGE_ONLY, EvalExecutionMode.RESUME} and request.source_run_dir is None:
        raise EvalConsoleError("仅 Judge 或继续运行必须指定历史 Run。")


def preflight_request(request: EvalRunRequest) -> tuple[Any | None, Any | None, dict[str, Any], dict[str, Any]]:
    """Resolve only the providers required by the requested execution stages."""
    SecretResolver(runner.ROOT / ".env.local").prepare_environment()
    validate_request(request)
    try:
        target = None
        judge = None
        target_plan: dict[str, Any] = {"enabled": False, "api_calls": 0}
        judge_plan: dict[str, Any] = {"enabled": False, "api_calls": 0}
        if request.mode is EvalExecutionMode.JUDGE_ONLY and request.source_run_dir is not None:
            selected = judge_only_case_ids(
                request.source_run_dir, request.judge_selector, request.case_ids
            )
            stage_plan = plan_stage_execution(
                request.source_run_dir, selected, EvalExecutionMode.JUDGE_ONLY
            )
        elif request.mode is EvalExecutionMode.RESUME and request.source_run_dir is not None:
            stage_plan = plan_stage_execution(
                request.source_run_dir, request.case_ids, EvalExecutionMode.RESUME
            )
        else:
            stage_plan = None
        needs_target = request.mode in {EvalExecutionMode.FULL, EvalExecutionMode.TARGET_ONLY} or bool(
            stage_plan and stage_plan.target_cases
        )
        needs_judge = request.mode in {EvalExecutionMode.FULL, EvalExecutionMode.JUDGE_ONLY} or bool(
            stage_plan and stage_plan.judge_cases
        )
        if request.mode is EvalExecutionMode.RESUME and stage_plan is not None:
            _validate_resume_profile_availability(request, stage_plan)
        if needs_target:
            try:
                target = _create_profile_provider(
                    request, "target", request.target_profile, request.target_model_override
                )
            except runner.ModelEvalError as exc:
                _raise_resume_credential_error(request, "Target", exc)
                raise
            target_plan = runner.provider_execution_plan(
                target,
                role="target",
                case_count=len(stage_plan.target_cases) if stage_plan else len(request.case_ids),
                runtime_profile=runner.API_RUNTIME_PROFILE,
            )
            target_plan["enabled"] = True
        if needs_judge:
            try:
                judge = _create_profile_provider(
                    request, "judge", request.judge_profile, request.judge_model_override
                )
            except runner.ModelEvalError as exc:
                _raise_resume_credential_error(request, "Judge", exc)
                raise
            judge_plan = runner.provider_execution_plan(
                judge,
                role="judge",
                case_count=len(stage_plan.judge_cases) if stage_plan else len(request.case_ids),
                runtime_profile=runner.API_RUNTIME_PROFILE,
            )
            judge_plan["enabled"] = True
        if request.mode is EvalExecutionMode.RESUME and request.source_run_dir is not None:
            _validate_resume_provider_configuration(request, stage_plan, target, judge)
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
    should_stop: StopRequested | None = None,
) -> RunOutcome:
    """Execute an explicit Target/Judge stage mode with durable per-stage artifacts."""
    if request.mode is EvalExecutionMode.RESUME:
        request = _resume_request_with_persisted_concurrency(request)
    definition = find_eval(request.eval_id)
    resume_stage_plan = (
        plan_stage_execution(request.source_run_dir, request.case_ids, EvalExecutionMode.RESUME)
        if request.mode is EvalExecutionMode.RESUME and request.source_run_dir is not None
        else None
    )
    needs_target = request.mode in {EvalExecutionMode.FULL, EvalExecutionMode.TARGET_ONLY} or bool(
        resume_stage_plan and resume_stage_plan.target_cases
    )
    needs_judge = request.mode in {EvalExecutionMode.FULL, EvalExecutionMode.JUDGE_ONLY} or bool(
        resume_stage_plan and resume_stage_plan.judge_cases
    )
    if (needs_target and target_provider is None) or (needs_judge and judge_provider is None):
        target_provider, judge_provider, target_plan, judge_plan = preflight_request(request)
    else:
        validate_request(request)
        target_plan = _provider_plan(target_provider, "target", request) if needs_target else {"enabled": False, "api_calls": 0}
        judge_plan = _provider_plan(judge_provider, "judge", request) if needs_judge else {"enabled": False, "api_calls": 0}
    try:
        execution_started_at = runner.utc_now()
        if request.mode is EvalExecutionMode.JUDGE_ONLY:
            if judge_provider is None or request.source_run_dir is None:
                raise EvalConsoleError("仅 Judge 运行缺少历史 Target 或 Judge Provider。")
            selected_case_ids = judge_only_case_ids(
                request.source_run_dir, request.judge_selector, request.case_ids
            )
            stage_plan = plan_stage_execution(
                request.source_run_dir, selected_case_ids, EvalExecutionMode.JUDGE_ONLY
            )
            if request.dry_run:
                run_dir = _judge_only_dry_run_dir(request, request.source_run_dir)
                return RunOutcome(run_dir, True, None, None, target_plan, judge_plan, {"target": 0, "judge": len(stage_plan.judge_cases)})
            run_dir, prepared = _create_judge_only_run(request, definition, stage_plan)
            _persist_console_provider_models(run_dir, None, judge_provider)
            before_calls = _api_call_counts(run_dir)
            current_stage = "JUDGE"
            current_stage_total = len(stage_plan.judge_cases)
            current_stage_before_calls = before_calls
            _execute_judge_stage(
                run_dir, request, judge_provider, stage_plan.judge_cases,
                progress, activity, should_stop, resume=False
            )
        elif request.mode is EvalExecutionMode.RESUME:
            if request.source_run_dir is None:
                raise EvalConsoleError("继续运行缺少历史 Run。")
            run_dir = request.source_run_dir.expanduser().resolve()
            stage_plan = resume_stage_plan or plan_stage_execution(run_dir, request.case_ids, EvalExecutionMode.RESUME)
            prepared = _prepared_records(run_dir, request.case_ids)
            if request.dry_run:
                return RunOutcome(run_dir, True, None, None, target_plan, judge_plan, {"target": len(stage_plan.target_cases), "judge": len(stage_plan.judge_cases)})
            before_calls = _api_call_counts(run_dir)
            if stage_plan.target_cases:
                if target_provider is None:
                    raise EvalConsoleError("继续运行仍有 Target Case，需提供 Target Provider。")
                current_stage = "TARGET"
                current_stage_total = len(stage_plan.target_cases)
                current_stage_before_calls = _api_call_counts(run_dir)
                _execute_target_stage(
                    prepared, run_dir, request, target_provider, stage_plan.target_cases,
                    progress, activity, should_stop, resume=True
                )
            if not _stop_requested(should_stop):
                if stage_plan.judge_cases:
                    if judge_provider is None:
                        raise EvalConsoleError("继续运行仍有 Judge Case，需提供 Judge Provider。")
                    current_stage = "JUDGE"
                    current_stage_total = len(stage_plan.judge_cases)
                    current_stage_before_calls = _api_call_counts(run_dir)
                    _execute_judge_stage(
                        run_dir, request, judge_provider, stage_plan.judge_cases,
                        progress, activity, should_stop, resume=True
                    )
        else:
            prepared = _prepare_current_cases(request)
            run_id = runner.validate_run_id(request.run_id or f"console-{runner.run_id_now()}")
            run_dir = (
                runner.results_root(
                    runner.prepared_version(prepared), request.results_root, runner.API_RUNTIME_PROFILE
                )
                / run_id
            )
            stage_plan = _new_run_stage_plan(request.mode, prepared)
            if request.dry_run:
                return RunOutcome(run_dir, True, None, None, target_plan, judge_plan, {"target": len(stage_plan.target_cases), "judge": len(stage_plan.judge_cases)})
            if target_provider is None:
                raise EvalConsoleError("Target 运行缺少 Target Provider。")
            before_calls = {"target": 0, "judge": 0}
            current_stage = "TARGET"
            current_stage_total = len(stage_plan.target_cases)
            current_stage_before_calls = before_calls
            _execute_target_stage(
                prepared, run_dir, request, target_provider, stage_plan.target_cases,
                progress, activity, should_stop, resume=False,
                metadata_extra=_console_metadata(
                    definition,
                    request,
                    prepared,
                    target_provider=target_provider,
                    judge_provider=judge_provider,
                ),
            )
            if request.mode is EvalExecutionMode.FULL and not _stop_requested(should_stop):
                judge_case_ids = _successful_target_case_ids(run_dir, stage_plan.judge_cases)
                if judge_provider is None:
                    raise EvalConsoleError("完整运行缺少 Judge Provider。")
                current_stage = "JUDGE"
                current_stage_total = len(judge_case_ids)
                current_stage_before_calls = _api_call_counts(run_dir)
                if judge_case_ids:
                    _execute_judge_stage(
                        run_dir, request, judge_provider, judge_case_ids,
                        progress, activity, should_stop, resume=False
                    )
                else:
                    _complete_empty_judge_stage(run_dir)
        interrupted = _stop_requested(should_stop)
        if interrupted:
            _mark_interrupted(run_dir)
        api_calls = _api_call_delta(before_calls, _api_call_counts(run_dir))
        _record_execution_metadata(
            run_dir, request, stage_plan, api_calls, interrupted, execution_started_at
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
        return RunOutcome(run_dir, False, summary, metadata, target_plan, judge_plan, api_calls)
    except KeyboardInterrupt as exc:
        if "run_dir" in locals() and run_dir.exists():
            _mark_interrupted(run_dir)
            api_calls = _api_call_delta(
                locals().get("before_calls", {"target": 0, "judge": 0}),
                _api_call_counts(run_dir),
            )
            if "stage_plan" in locals() and "execution_started_at" in locals():
                _record_execution_metadata(
                    run_dir,
                    request,
                    stage_plan,
                    api_calls,
                    True,
                    execution_started_at,
                )
            stage = locals().get("current_stage", "TARGET")
            stage_total = int(locals().get("current_stage_total", 0))
            stage_before_calls = locals().get("current_stage_before_calls", {"target": 0, "judge": 0})
            completed = _api_call_delta(stage_before_calls, _api_call_counts(run_dir)).get(
                stage.lower(), 0
            )
            raise EvaluationInterrupted(run_dir, stage, completed, stage_total) from exc
        raise
    except (OSError, ValueError, runner.ModelEvalError) as exc:
        raise EvalConsoleError(friendly_error(exc)) from exc


def plan_stage_execution(
    run_dir: Path,
    case_ids: tuple[str, ...],
    mode: EvalExecutionMode,
) -> StagePlan:
    """Plan Stage work from persisted Case state instead of a run-level status."""
    run_dir = run_dir.expanduser().resolve()
    try:
        runner.validate_result_artifacts(run_dir)
        metadata = runner.load_json_object(run_dir / "run.json")
        _validate_current_run_schema(metadata)
        all_case_ids = _metadata_case_ids(metadata)
        selected = _validate_selected_case_ids(case_ids, all_case_ids)
        responses = runner.index_response_attempts(
            runner.load_jsonl(run_dir / "responses.jsonl"), set(all_case_ids)
        )
        judgments = runner.index_judgment_attempts(
            runner.load_jsonl(run_dir / "judgments.jsonl")
            if (run_dir / "judgments.jsonl").is_file()
            else [],
            set(all_case_ids),
        )
    except (OSError, ValueError, runner.ModelEvalError) as exc:
        raise EvalConsoleError("该历史 Target 结果与当前评测上下文不兼容。\n无法安全执行 Judge-only。") from exc
    planned: list[CaseStagePlan] = []
    for case_id in selected:
        response = responses.get(case_id)
        judgment = judgments.get(case_id)
        if mode is EvalExecutionMode.JUDGE_ONLY:
            if response is None or response.get("status") != "MODEL_RESPONSE":
                raise EvalConsoleError(f"{case_id} 没有可复用的 Target 成功结果，无法仅 Judge。")
            planned.append(CaseStagePlan(case_id, False, True, "复用历史 Target 成功结果"))
        elif mode is EvalExecutionMode.RESUME:
            if response is None or response.get("status") != "MODEL_RESPONSE":
                planned.append(
                    CaseStagePlan(
                        case_id,
                        True,
                        metadata.get("origin_mode") != EvalExecutionMode.TARGET_ONLY.value,
                        "Target 缺失或执行错误",
                    )
                )
            elif metadata.get("origin_mode") == EvalExecutionMode.TARGET_ONLY.value:
                planned.append(CaseStagePlan(case_id, False, False, "已有 Target 成功结果"))
            elif judgment is None or judgment.get("status") == "NOT_JUDGED":
                planned.append(CaseStagePlan(case_id, False, True, "Judge 尚未运行"))
            elif judgment.get("status") == "JUDGE_ERROR":
                planned.append(CaseStagePlan(case_id, False, True, "上次 Judge ERROR"))
            else:
                planned.append(CaseStagePlan(case_id, False, False, "已有有效 Judgment"))
        else:
            raise EvalConsoleError("Stage Planner 仅用于 Judge-only 或继续运行。")
    return StagePlan(mode, tuple(planned))


def judge_only_case_ids(
    run_dir: Path,
    selector: JudgeCaseSelector,
    requested_case_ids: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Select only source Cases with a persisted successful Target response."""
    run_dir = run_dir.expanduser().resolve()
    try:
        runner.validate_result_artifacts(run_dir)
        metadata = runner.load_json_object(run_dir / "run.json")
        _validate_current_run_schema(metadata)
        all_case_ids = _metadata_case_ids(metadata)
        responses = runner.index_response_attempts(
            runner.load_jsonl(run_dir / "responses.jsonl"), set(all_case_ids)
        )
        judgments = runner.index_judgment_attempts(
            runner.load_jsonl(run_dir / "judgments.jsonl")
            if (run_dir / "judgments.jsonl").is_file()
            else [],
            set(all_case_ids),
        )
    except (OSError, ValueError, runner.ModelEvalError) as exc:
        raise EvalConsoleError("该历史 Target 结果与当前评测上下文不兼容。\n无法安全执行 Judge-only。") from exc
    available = [
        case_id for case_id in all_case_ids
        if responses.get(case_id, {}).get("status") == "MODEL_RESPONSE"
    ]
    if selector is JudgeCaseSelector.ALL_TARGET:
        selected = available
    elif selector is JudgeCaseSelector.JUDGE_ERROR:
        selected = [case_id for case_id in available if judgments.get(case_id, {}).get("status") == "JUDGE_ERROR"]
    elif selector is JudgeCaseSelector.JUDGE_MISSING:
        selected = [case_id for case_id in available if case_id not in judgments or judgments[case_id].get("status") == "NOT_JUDGED"]
    elif selector is JudgeCaseSelector.JUDGE_ERROR_OR_MISSING:
        selected = [
            case_id for case_id in available
            if case_id not in judgments or judgments[case_id].get("status") in {"NOT_JUDGED", "JUDGE_ERROR"}
        ]
    else:
        selected = list(_validate_selected_case_ids(requested_case_ids, available))
    if not selected:
        raise EvalConsoleError("所选历史运行没有符合条件的可评审 Target 结果。")
    return tuple(selected)


def _prepare_current_cases(request: EvalRunRequest) -> list[dict[str, Any]]:
    selected_cases, criteria = runner.load_definitions()
    wanted = set(request.case_ids)
    selected_cases = [case for case in selected_cases if case["id"] in wanted]
    if len(selected_cases) != len(request.case_ids):
        raise EvalConsoleError("已选择的 Cases 与当前 Eval 定义不再匹配。")
    return runner.prepare_cases(selected_cases, criteria)


def _prepared_records(run_dir: Path, case_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    try:
        runner.validate_result_artifacts(run_dir)
        metadata = runner.load_json_object(run_dir / "run.json")
        _validate_current_run_schema(metadata)
        prepared = runner.load_run_snapshots(run_dir)["prepared"]
    except (OSError, ValueError, runner.ModelEvalError) as exc:
        raise EvalConsoleError("该历史运行缺少兼容的 immutable snapshot，无法安全恢复。") from exc
    selected = [record for record in prepared if record.get("case_id") in set(case_ids)]
    if len(selected) != len(case_ids):
        raise EvalConsoleError("历史运行缺少所选 Case 的 immutable snapshot。")
    return selected


def _successful_target_case_ids(run_dir: Path, case_ids: tuple[str, ...]) -> tuple[str, ...]:
    """Retain the current execution scope while excluding failed Target Cases."""
    responses = runner.index_response_attempts(
        runner.load_jsonl(run_dir / "responses.jsonl"), set(case_ids)
    )
    return tuple(
        case_id
        for case_id in case_ids
        if responses.get(case_id, {}).get("status") == "MODEL_RESPONSE"
    )


def _complete_empty_judge_stage(run_dir: Path) -> None:
    """Persist the completed no-op Judge phase for a FULL run with no Target success."""
    metadata = runner.load_json_object(run_dir / "run.json")
    responses = runner.load_jsonl(run_dir / "responses.jsonl")
    judgments_path = run_dir / "judgments.jsonl"
    judgments = runner.load_jsonl(judgments_path) if judgments_path.is_file() else []
    completed_at = runner.utc_now()
    metadata["judge_phase_completed"] = True
    metadata["judge_started_at"] = completed_at
    metadata["judge_completed_at"] = completed_at
    metadata["completed_at"] = completed_at
    runner.refresh_run_metadata(metadata, responses, judgments)
    runner.write_json(run_dir / "run.json", metadata)


def _judge_only_dry_run_dir(request: EvalRunRequest, source_dir: Path) -> Path:
    """Return a deterministic preview location without creating a child Run."""
    snapshots = runner.load_run_snapshots(source_dir)
    version = runner.prepared_version(snapshots["prepared"])
    return (
        runner.results_root(version, request.results_root, runner.API_RUNTIME_PROFILE)
        / "console-judge-dry-run"
    )


def _create_judge_only_run(
    request: EvalRunRequest, definition: Any, stage_plan: StagePlan
) -> tuple[Path, list[dict[str, Any]]]:
    if request.source_run_dir is None:
        raise EvalConsoleError("仅 Judge 运行必须指定历史 Target Run。")
    source_dir = request.source_run_dir.expanduser().resolve()
    try:
        runner.validate_result_artifacts(source_dir)
        source_metadata = runner.load_json_object(source_dir / "run.json")
        _validate_current_run_schema(source_metadata)
        snapshots = deepcopy(runner.load_run_snapshots(source_dir))
        prepared = _prepared_records(source_dir, stage_plan.judge_cases)
        source_responses = runner.index_response_attempts(
            runner.load_jsonl(source_dir / "responses.jsonl"),
            set(_metadata_case_ids(source_metadata)),
        )
    except (OSError, ValueError, runner.ModelEvalError) as exc:
        raise EvalConsoleError("该历史 Target 结果与当前评测上下文不兼容。\n无法安全执行 Judge-only。") from exc
    snapshots["prepared"] = deepcopy(prepared)
    run_id = runner.validate_run_id(request.run_id or f"console-judge-{runner.run_id_now()}")
    runtime_profile = source_metadata["runtime_profile"]
    run_dir = (
        runner.results_root(runner.prepared_version(prepared), request.results_root, runtime_profile)
        / run_id
    )
    if run_dir.exists():
        raise EvalConsoleError(f"结果目录已存在：{run_dir}")
    metadata = runner.new_run_metadata(
        prepared,
        snapshots,
        run_id=run_id,
        target=source_metadata["target"],
        runtime_profile=runtime_profile,
        repository_sha=source_metadata.get("git_sha"),
        repository_dirty=source_metadata.get("git_dirty"),
        origin_mode=EvalExecutionMode.JUDGE_ONLY.value,
    )
    metadata["source_target_run_id"] = source_metadata["run_id"]
    source_console = source_metadata.get("console") if isinstance(source_metadata.get("console"), dict) else {}
    metadata["console"] = {
        "schema_version": CURRENT_CONSOLE_SCHEMA_VERSION,
        "origin_mode": EvalExecutionMode.JUDGE_ONLY.value,
        "eval_id": definition.eval_id,
        "selected_case_ids": list(stage_plan.judge_cases),
        "selected_cases": len(stage_plan.judge_cases),
        "total_eval_cases": len(definition.cases),
        "target_profile": source_console.get("target_profile"),
        "judge_profile": request.judge_profile,
        "target_model": _requested_model(source_metadata.get("target")),
        "judge_model": request.judge_model_override,
        "target_concurrency": request.target_concurrency,
        "judge_concurrency": request.judge_concurrency,
        "source_target_run_id": source_metadata["run_id"],
    }
    cloned_responses: list[dict[str, Any]] = []
    for prepared_record in prepared:
        source = source_responses.get(prepared_record["case_id"])
        if source is None or source.get("status") != "MODEL_RESPONSE":
            raise EvalConsoleError(f"{prepared_record['case_id']} 没有可复用的 Target 成功结果。")
        copied = deepcopy(source)
        copied.update(runner.artifact_binding(metadata))
        copied["attempt"] = 1
        copied["source_target_run_id"] = source_metadata["run_id"]
        copied["source_target_attempt"] = source.get("attempt", 1)
        cloned_responses.append(copied)
    checkpoint = runner.utc_now()
    metadata["target_started_at"] = checkpoint
    metadata["target_completed_at"] = checkpoint
    metadata["judge_phase_completed"] = False
    runner.refresh_run_metadata(metadata, cloned_responses, [])
    runner.write_run_snapshots(run_dir, snapshots)
    runner.write_json(run_dir / "run.json", metadata, exclusive=True)
    runner.write_jsonl(run_dir / "responses.jsonl", cloned_responses, exclusive=True)
    return run_dir, prepared


def _execute_target_stage(
    prepared: list[dict[str, Any]],
    run_dir: Path,
    request: EvalRunRequest,
    provider: Any,
    case_ids: tuple[str, ...],
    progress: ProgressCallback | None,
    activity: ActivityCallback | None,
    should_stop: StopRequested | None,
    *,
    resume: bool,
    metadata_extra: dict[str, Any] | None = None,
) -> None:
    runner.execute_run(
        prepared,
        provider,
        run_dir,
        resume=resume,
        allow_dirty_debug=request.allow_dirty_debug,
        target_concurrency=request.target_concurrency,
        continue_on_error=request.continue_on_error,
        metadata_extra=metadata_extra,
        on_case_start=lambda record, started, total: _emit_activity(activity, "TARGET", record, started, total),
        on_case_complete=lambda record, completed, total: _emit_progress(run_dir, request, progress, "TARGET", record, completed, total),
        should_stop=should_stop,
        case_ids=case_ids,
        origin_mode=request.mode.value,
    )


def _execute_judge_stage(
    run_dir: Path,
    request: EvalRunRequest,
    provider: Any,
    case_ids: tuple[str, ...],
    progress: ProgressCallback | None,
    activity: ActivityCallback | None,
    should_stop: StopRequested | None,
    *,
    resume: bool,
) -> None:
    runner.execute_judge(
        run_dir,
        provider,
        resume=resume,
        on_case_start=lambda record, started, total: _emit_activity(activity, "JUDGE", record, started, total),
        on_case_complete=lambda record, completed, total: _emit_progress(run_dir, request, progress, "JUDGE", record, completed, total),
        should_stop=should_stop,
        case_ids=case_ids,
        judge_concurrency=request.judge_concurrency,
    )


def _emit_activity(
    activity: ActivityCallback | None, phase: str, record: dict[str, Any], started: int, total: int
) -> None:
    if activity is not None:
        activity(phase, record, started, total)


def _emit_progress(
    run_dir: Path, request: EvalRunRequest, progress: ProgressCallback | None,
    phase: str, record: dict[str, Any], completed: int, total: int,
) -> None:
    _append_log(run_dir, request, phase, record, completed, total)
    if progress is not None:
        progress(phase, record, completed, total)


def _new_run_stage_plan(mode: EvalExecutionMode, prepared: list[dict[str, Any]]) -> StagePlan:
    return StagePlan(
        mode,
        tuple(
            CaseStagePlan(
                record["case_id"],
                True,
                mode is EvalExecutionMode.FULL,
                "完整运行" if mode is EvalExecutionMode.FULL else "仅运行 Target",
            )
            for record in prepared
        ),
    )


def _console_metadata(
    definition: Any,
    request: EvalRunRequest,
    prepared: list[dict[str, Any]],
    *,
    target_provider: Any | None = None,
    judge_provider: Any | None = None,
) -> dict[str, Any]:
    return {
        "console": {
            "schema_version": CURRENT_CONSOLE_SCHEMA_VERSION,
            "origin_mode": request.mode.value,
            "eval_id": definition.eval_id,
            "selected_case_ids": [record["case_id"] for record in prepared],
            "selected_cases": len(prepared),
            "total_eval_cases": len(definition.cases),
            "target_profile": request.target_profile,
            "judge_profile": request.judge_profile,
            "target_model": _provider_requested_model(target_provider)
            or request.target_model_override,
            "judge_model": _provider_requested_model(judge_provider)
            or request.judge_model_override,
            "target_concurrency": request.target_concurrency,
            "judge_concurrency": request.judge_concurrency,
        }
    }


def _provider_plan(provider: Any | None, role: str, request: EvalRunRequest) -> dict[str, Any]:
    if provider is None:
        return {"enabled": False, "api_calls": 0}
    plan = runner.provider_execution_plan(
        provider,
        role=role,
        case_count=len(request.case_ids),
        runtime_profile=runner.API_RUNTIME_PROFILE,
    )
    plan["enabled"] = True
    return plan


def _requested_model(manifest: Any) -> str | None:
    if not isinstance(manifest, dict):
        return None
    value = manifest.get("requested_model")
    return value if isinstance(value, str) else None


def _provider_requested_model(provider: Any | None) -> str | None:
    return _requested_model(runner.provider_metadata(provider)) if provider is not None else None


def _persist_console_provider_models(
    run_dir: Path, target_provider: Any | None, judge_provider: Any | None
) -> None:
    metadata = runner.load_json_object(run_dir / "run.json")
    _validate_current_run_schema(metadata)
    console = metadata["console"]
    if target_provider is not None:
        console["target_model"] = _provider_requested_model(target_provider)
    if judge_provider is not None:
        console["judge_model"] = _provider_requested_model(judge_provider)
    runner.write_json(run_dir / "run.json", metadata)


def _resume_metadata(request: EvalRunRequest) -> dict[str, Any]:
    if request.source_run_dir is None:
        raise EvalConsoleError("Resume 缺少原运行目录。")
    metadata = runner.load_json_object(request.source_run_dir / "run.json")
    _validate_current_run_schema(metadata)
    return metadata


def _resume_request_with_persisted_concurrency(request: EvalRunRequest) -> EvalRunRequest:
    """Keep Resume execution strategy fixed to the current Run artifact."""
    metadata = _resume_metadata(request)
    console = metadata["console"]
    assert isinstance(console, dict)
    values: dict[str, int] = {}
    for field in ("target_concurrency", "judge_concurrency"):
        value = console.get(field)
        if not isinstance(value, int) or value < 1 or value > 32:
            raise EvalConsoleError(
                "Unsupported Run Artifact Version: current concurrency configuration is required"
            )
        values[field] = value
    return replace(request, **values)


def _validate_resume_profile_availability(
    request: EvalRunRequest, stage_plan: StagePlan
) -> None:
    available = {item.name: item for item in discover_provider_profiles(request.profiles_file)}
    required = (
        ("Target", request.target_profile, bool(stage_plan.target_cases), "supports_target"),
        ("Judge", request.judge_profile, bool(stage_plan.judge_cases), "supports_judge"),
    )
    for label, profile_name, needed, capability in required:
        if not needed:
            continue
        if not profile_name:
            raise EvalConsoleError(
                f"无法继续该运行：原 {label} Provider Profile 缺失。"
                "Resume 要求使用原运行配置；如需使用新的 Judge，请使用 JUDGE_ONLY。"
            )
        profile = available.get(profile_name)
        if profile is None or not bool(getattr(profile, capability)):
            raise EvalConsoleError(
                f"无法继续该运行：原 {label} Provider Profile 不存在或不支持该角色："
                f"{profile_name}。Resume 要求使用原运行配置；如需使用新的 Judge，请使用 JUDGE_ONLY。"
            )


def _raise_resume_credential_error(
    request: EvalRunRequest, label: str, error: runner.ModelEvalError
) -> None:
    if request.mode is EvalExecutionMode.RESUME and "environment variable" in str(error):
        raise EvalConsoleError(
            f"无法继续该运行：原 {label} Profile 已找到，但当前缺少 API Credential。"
            f"请先配置：{error}"
        ) from error


def _validate_resume_provider_configuration(
    request: EvalRunRequest,
    stage_plan: StagePlan,
    target: Any | None,
    judge: Any | None,
) -> None:
    metadata = _resume_metadata(request)
    checks = (
        ("Target", bool(stage_plan.target_cases), target, metadata.get("target")),
        ("Judge", bool(stage_plan.judge_cases), judge, metadata.get("judge")),
    )
    for label, needed, provider, expected in checks:
        if not needed:
            continue
        actual = runner.provider_metadata(provider) if provider is not None else None
        if not isinstance(expected, dict) or actual != expected:
            raise EvalConsoleError(
                f"Resume configuration mismatch: 原 {label} Provider 配置已变化。"
                "Resume 必须继续使用原运行配置；如需使用新的 Judge，请使用 JUDGE_ONLY。"
            )


def _metadata_case_ids(metadata: dict[str, Any]) -> tuple[str, ...]:
    cases = metadata.get("cases")
    if not isinstance(cases, list):
        raise runner.ModelEvalError("run.json is missing case snapshots")
    return tuple(
        record["case_id"] for record in cases
        if isinstance(record, dict) and isinstance(record.get("case_id"), str)
    )


def _validate_current_run_schema(metadata: dict[str, Any]) -> None:
    if metadata.get("schema_version") != 3:
        raise runner.ModelEvalError("Unsupported Run Artifact Version: runner schema is not supported")
    if metadata.get("origin_mode") not in {
        EvalExecutionMode.FULL.value,
        EvalExecutionMode.TARGET_ONLY.value,
        EvalExecutionMode.JUDGE_ONLY.value,
    }:
        raise runner.ModelEvalError("Unsupported Run Artifact Version: origin_mode is missing")
    console = metadata.get("console")
    if not isinstance(console, dict) or console.get("schema_version") != CURRENT_CONSOLE_SCHEMA_VERSION:
        raise runner.ModelEvalError(
            "Unsupported Run Artifact Version: current Console artifact is required"
        )
    if console.get("origin_mode") != metadata.get("origin_mode"):
        raise runner.ModelEvalError(
            "Unsupported Run Artifact Version: Console origin_mode does not match run"
        )
    if "concurrency" in console:
        raise runner.ModelEvalError(
            "Unsupported Run Artifact Version: legacy Console concurrency is not supported"
        )
    for field in ("target_concurrency", "judge_concurrency"):
        value = console.get(field)
        if not isinstance(value, int) or value < 1 or value > 32:
            raise runner.ModelEvalError(
                "Unsupported Run Artifact Version: current concurrency configuration is required"
            )


def _validate_selected_case_ids(case_ids: tuple[str, ...], available: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    available_set = set(available)
    if not case_ids:
        raise EvalConsoleError("至少选择一个 Case。")
    unknown = [case_id for case_id in case_ids if case_id not in available_set]
    if unknown:
        raise EvalConsoleError("历史运行中不存在所选 Case：" + ", ".join(unknown))
    if len(set(case_ids)) != len(case_ids):
        raise EvalConsoleError("每个 Case 只能选择一次。")
    return tuple(case_ids)


def _api_call_counts(run_dir: Path) -> dict[str, int]:
    metadata = runner.load_json_object(run_dir / "run.json")
    counts = metadata.get("api_calls") if isinstance(metadata.get("api_calls"), dict) else {}
    return {
        "target": int(counts.get("target", 0)),
        "judge": int(counts.get("judge", 0)),
    }


def _api_call_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: max(0, after.get(key, 0) - before.get(key, 0)) for key in ("target", "judge")}


def _stop_requested(callback: StopRequested | None) -> bool:
    return bool(callback is not None and callback())


def _mark_interrupted(run_dir: Path) -> None:
    metadata = runner.load_json_object(run_dir / "run.json")
    responses = runner.load_jsonl(run_dir / "responses.jsonl")
    judgments = runner.load_jsonl(run_dir / "judgments.jsonl") if (run_dir / "judgments.jsonl").is_file() else []
    metadata["interrupted"] = True
    metadata["interrupted_at"] = runner.utc_now()
    metadata["judge_completed_at"] = None
    metadata["completed_at"] = None
    runner.invalidate_report(run_dir, metadata)
    runner.refresh_run_metadata(metadata, responses, judgments)
    runner.write_json(run_dir / "run.json", metadata)


def _record_execution_metadata(
    run_dir: Path,
    request: EvalRunRequest,
    stage_plan: StagePlan,
    api_calls: dict[str, int],
    interrupted: bool,
    started_at: str,
) -> None:
    metadata = runner.load_json_object(run_dir / "run.json")
    _validate_current_run_schema(metadata)
    console = metadata["console"]
    console["target_model"] = _requested_model(metadata.get("target"))
    console["judge_model"] = _requested_model(metadata.get("judge"))
    console["target_concurrency"] = request.target_concurrency
    console["judge_concurrency"] = request.judge_concurrency
    history = metadata.get("execution_history")
    if not isinstance(history, list):
        history = []
        metadata["execution_history"] = history
    planned_api_calls = {
        "target": len(stage_plan.target_cases),
        "judge": len(stage_plan.judge_cases),
    }
    history.append(
        {
            "execution_id": f"execution-{len(history) + 1:04d}",
            "mode": request.mode.value,
            "started_at": started_at,
            "completed_at": runner.utc_now(),
            "requested_case_ids": list(request.case_ids),
            "stage_case_ids": [item.case_id for item in stage_plan.cases],
            "planned_api_calls": planned_api_calls,
            "actual_api_calls": dict(api_calls),
            "api_call_plan_match": api_calls == planned_api_calls,
            "interrupted": interrupted,
            "target_concurrency": request.target_concurrency,
            "judge_concurrency": request.judge_concurrency,
            "target_peak_in_flight": (
                metadata.get("parallel_metrics", {}).get("target_peak_in_flight")
                if isinstance(metadata.get("parallel_metrics"), dict)
                else None
            ),
            "judge_peak_in_flight": (
                metadata.get("parallel_metrics", {}).get("judge_peak_in_flight")
                if isinstance(metadata.get("parallel_metrics"), dict)
                else None
            ),
        }
    )
    runner.write_json(run_dir / "run.json", metadata)


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
    request: EvalRunRequest, role: str, profile: str | None, model_override: str | None = None
) -> Any:
    if not profile:
        raise EvalConsoleError(f"{role.title()} 运行缺少 Provider Profile。")
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
            (datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))).total_seconds(),
        )
    except ValueError:
        return None
