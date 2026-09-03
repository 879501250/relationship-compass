"""Discover runner-backed evals, profiles, and historical result artifacts."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    CURRENT_CONSOLE_SCHEMA_VERSION,
    EvalCase,
    EvalDefinition,
    ProviderProfile,
)
from .runner_adapter import ROOT, runner


@dataclass(frozen=True)
class HistoricalRun:
    """A compact view of a prior run suitable for history and retry menus."""

    run_dir: Path
    eval_id: str | None
    run_id: str
    created_at: str | None
    total_cases: int
    passed_cases: int | None
    failed_case_ids: tuple[str, ...]
    error_case_ids: tuple[str, ...]
    incomplete_case_ids: tuple[str, ...]
    state: str
    target_profile: str | None
    judge_profile: str | None
    mode: str
    source_target_run_id: str | None
    target_model: str | None
    target_api_calls: int
    judge_api_calls: int
    target_successes: int
    target_errors: int
    target_missing: int
    judge_completed: int
    judge_errors: int
    judge_missing: int
    target_http_attempts: int | None = None
    target_rate_limits: int | None = None
    target_retries: int | None = None
    judge_http_attempts: int | None = None
    judge_rate_limits: int | None = None
    judge_retries: int | None = None


def discover_evals() -> list[EvalDefinition]:
    """Discover the current runner's model-eval definition without case hardcoding."""
    cases, _ = runner.load_definitions()
    relative = runner.CASES_PATH.relative_to(ROOT)
    eval_id = relative.with_suffix("").as_posix().replace("/", "-").replace("_", "-")
    display_name = relative.parent.name.replace("_", " ").title()
    discovered_cases = tuple(
        EvalCase(
            case_id=case["id"],
            title=str(case.get("title") or case["id"]),
            summary=_summary(str(case.get("prompt", ""))),
        )
        for case in cases
    )
    return [
        EvalDefinition(
            eval_id=eval_id,
            title=f"{display_name} behavioral eval",
            description=f"{len(discovered_cases)} cases discovered from {relative.as_posix()}.",
            source_path=runner.CASES_PATH,
            cases=discovered_cases,
        )
    ]


def find_eval(eval_id: str) -> EvalDefinition:
    """Return an available eval or explain the discovered alternatives."""
    available = {definition.eval_id: definition for definition in discover_evals()}
    if eval_id in available:
        return available[eval_id]
    choices = ", ".join(sorted(available)) or "none"
    raise ValueError(f"未找到 Eval：{eval_id!r}。可用 Eval：{choices}。")


def discover_provider_profiles(profiles_file: Path) -> list[ProviderProfile]:
    """Read local profiles without resolving credentials or making network calls."""
    if not profiles_file.is_file():
        return []
    data = runner.load_json_yaml(profiles_file)
    profiles = data.get("profiles")
    if not isinstance(profiles, dict):
        raise ValueError(f"{profiles_file} 必须包含 'profiles' 对象。")
    discovered: list[ProviderProfile] = []
    for name, profile in profiles.items():
        if not isinstance(name, str) or not isinstance(profile, dict):
            continue
        target = profile.get("target") if isinstance(profile.get("target"), dict) else {}
        judge = profile.get("judge") if isinstance(profile.get("judge"), dict) else {}
        discovered.append(
            ProviderProfile(
                name=name,
                provider=_string(profile.get("provider")),
                target_model=_model_label(target),
                judge_model=_model_label(judge),
                supports_target=bool(target or profile.get("model") or profile.get("model_env")),
                supports_judge=bool(judge),
            )
        )
    return sorted(discovered, key=lambda profile: profile.name)


def discover_runs(results_root: Path) -> list[HistoricalRun]:
    """Read historical Console-compatible results without changing their artifacts."""
    if not results_root.is_dir():
        return []
    runs: list[HistoricalRun] = []
    for metadata_path in results_root.glob("v*/**/run.json"):
        try:
            metadata = runner.load_json_object(metadata_path)
            if metadata.get("schema_version") != 3 or metadata.get("origin_mode") not in {
                "FULL",
                "TARGET_ONLY",
                "JUDGE_ONLY",
            }:
                continue
            console = metadata.get("console")
            if (
                not isinstance(console, dict)
                or console.get("schema_version") != CURRENT_CONSOLE_SCHEMA_VERSION
                or console.get("origin_mode") != metadata.get("origin_mode")
            ):
                continue
            outcomes = run_case_outcomes(metadata_path.parent, metadata)
            target_only = metadata.get("origin_mode") == "TARGET_ONLY"
            failed = [case_id for case_id, state in outcomes.items() if state == "FAIL"]
            errors = [case_id for case_id, state in outcomes.items() if state == "ERROR"]
            incomplete = [
                case_id for case_id, state in outcomes.items() if state == "INCOMPLETE"
            ]
            console = metadata.get("console") if isinstance(metadata.get("console"), dict) else {}
            api_calls = metadata.get("api_calls") if isinstance(metadata.get("api_calls"), dict) else {}
            target_manifest = metadata.get("target") if isinstance(metadata.get("target"), dict) else {}
            counts = metadata.get("counts") if isinstance(metadata.get("counts"), dict) else {}
            execution_history = metadata.get("execution_history")
            latest_execution = (
                execution_history[-1]
                if isinstance(execution_history, list) and execution_history
                else None
            )
            provider_telemetry = (
                latest_execution.get("provider_telemetry")
                if isinstance(latest_execution, dict)
                else None
            )
            target_telemetry = (
                provider_telemetry.get("target")
                if isinstance(provider_telemetry, dict)
                else None
            )
            judge_telemetry = (
                provider_telemetry.get("judge")
                if isinstance(provider_telemetry, dict)
                else None
            )
            runs.append(
                HistoricalRun(
                    run_dir=metadata_path.parent,
                    eval_id=_string(console.get("eval_id")),
                    run_id=str(metadata.get("run_id") or metadata_path.parent.name),
                    created_at=_string(metadata.get("created_at")),
                    total_cases=int(counts.get("total_cases", len(outcomes))),
                    passed_cases=None if target_only else sum(
                        state == "PASS" for state in outcomes.values()
                    ),
                    failed_case_ids=() if target_only else tuple(failed),
                    error_case_ids=() if target_only else tuple(errors),
                    incomplete_case_ids=() if target_only else tuple(incomplete),
                    state=(
                        str(metadata.get("status") or "TARGET_COMPLETE")
                        if target_only
                        else _run_state(incomplete, errors, failed)
                    ),
                    target_profile=_string(console.get("target_profile")),
                    judge_profile=_string(console.get("judge_profile")),
                    mode=_string(metadata.get("origin_mode")),
                    source_target_run_id=_string(metadata.get("source_target_run_id")),
                    target_model=_string(target_manifest.get("requested_model")),
                    target_api_calls=_as_nonnegative_int(api_calls.get("target")),
                    judge_api_calls=_as_nonnegative_int(api_calls.get("judge")),
                    target_successes=_as_nonnegative_int(counts.get("model_response")),
                    target_errors=_as_nonnegative_int(counts.get("target_error")),
                    target_missing=_as_nonnegative_int(counts.get("not_run")),
                    judge_completed=_as_nonnegative_int(counts.get("judged")),
                    judge_errors=_as_nonnegative_int(counts.get("judge_error")),
                    judge_missing=_as_nonnegative_int(counts.get("not_judged")),
                    target_http_attempts=_optional_nonnegative_int(
                        target_telemetry, "http_attempts"
                    ),
                    target_rate_limits=_optional_nonnegative_int(
                        target_telemetry, "rate_limit_responses"
                    ),
                    target_retries=_optional_nonnegative_int(target_telemetry, "retries"),
                    judge_http_attempts=_optional_nonnegative_int(
                        judge_telemetry, "http_attempts"
                    ),
                    judge_rate_limits=_optional_nonnegative_int(
                        judge_telemetry, "rate_limit_responses"
                    ),
                    judge_retries=_optional_nonnegative_int(judge_telemetry, "retries"),
                )
            )
        except (OSError, ValueError, TypeError, runner.ModelEvalError):
            continue
    return sorted(runs, key=lambda item: item.created_at or "", reverse=True)


def run_case_outcomes(
    run_dir: Path, metadata: dict[str, Any] | None = None
) -> dict[str, str]:
    """Classify Cases using the stage semantics persisted by the source Run."""
    metadata = metadata or runner.load_json_object(run_dir / "run.json")
    responses = _read_jsonl(run_dir / "responses.jsonl")
    judgments = _read_jsonl(run_dir / "judgments.jsonl")
    response_latest = _latest_by_case(responses)
    judgment_latest = _latest_by_case(judgments)
    snapshots = metadata.get("cases")
    case_ids = [
        record["case_id"]
        for record in snapshots
        if isinstance(record, dict) and isinstance(record.get("case_id"), str)
    ] if isinstance(snapshots, list) else []
    if not case_ids:
        case_ids = list(dict.fromkeys([*response_latest, *judgment_latest]))
    outcomes: dict[str, str] = {}
    target_only = metadata.get("origin_mode") == "TARGET_ONLY"
    for case_id in case_ids:
        response = response_latest.get(case_id)
        if target_only:
            if response is None:
                outcomes[case_id] = "NOT_RUN"
            elif response.get("status") == "MODEL_RESPONSE":
                outcomes[case_id] = "TARGET_SUCCESS"
            else:
                outcomes[case_id] = "TARGET_ERROR"
            continue
        if response is None:
            outcomes[case_id] = "INCOMPLETE"
            continue
        if response.get("status") != "MODEL_RESPONSE":
            outcomes[case_id] = "ERROR"
            continue
        judgment = judgment_latest.get(case_id)
        if judgment is None or judgment.get("status") == "NOT_JUDGED":
            outcomes[case_id] = "INCOMPLETE"
            continue
        if judgment.get("status") == "JUDGE_ERROR":
            outcomes[case_id] = "ERROR"
            continue
        if judgment.get("status") == "JUDGMENT":
            outcomes[case_id] = (
                "FAIL"
                if any(
                    item.get("passed") is False
                    for item in judgment.get("criteria", [])
                    if isinstance(item, dict)
                )
                else "PASS"
            )
            continue
        outcomes[case_id] = "INCOMPLETE"
    return outcomes


def _run_state(incomplete: list[str], errors: list[str], failed: list[str]) -> str:
    if incomplete:
        return "INCOMPLETE"
    if errors:
        return "COMPLETED_WITH_ERRORS"
    if failed:
        return "COMPLETED_WITH_FAILURES"
    return "COMPLETED"


def _latest_by_case(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        case_id = record.get("case_id")
        if not isinstance(case_id, str):
            continue
        previous = latest.get(case_id)
        if previous is None or int(record.get("attempt", 1)) >= int(previous.get("attempt", 1)):
            latest[case_id] = record
    return latest


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return runner.load_jsonl(path) if path.is_file() else []


def _model_label(config: dict[str, Any]) -> str | None:
    model = _string(config.get("model"))
    if model:
        return model
    env_name = _string(config.get("model_env"))
    if not env_name:
        return None
    return os.environ.get(env_name) or f"${env_name}"


def _string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _as_nonnegative_int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _optional_nonnegative_int(container: Any, field: str) -> int | None:
    if not isinstance(container, dict):
        return None
    value = container.get(field)
    return value if isinstance(value, int) and value >= 0 else None


def _summary(prompt: str) -> str:
    compact = " ".join(prompt.split())
    return compact if len(compact) <= 52 else compact[:49] + "..."
