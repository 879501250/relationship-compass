"""Friendly interactive and non-interactive entrypoints for Eval Console V1."""

from __future__ import annotations

import argparse
import sys
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
from .models import EvalDefinition, EvalRunRequest, ProviderProfile
from .runner_adapter import runner
from .selection import CaseSelectionError, parse_case_selection
from .service import (
    EvalConsoleError,
    EvaluationInterrupted,
    execute_request,
    failed_case_ids,
    preflight_request,
    validate_configuration,
)


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
        case_id = str(record.get("case_id") or "unknown case")
        with self._lock:
            self._active = (phase, case_id, started, total, time.monotonic())
        if not self._tty:
            print(f"  [START] {case_id} {phase}")
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
        elapsed = f"{duration:.1f}s" if duration is not None else "unknown time"
        if self._tty:
            print("\r" + " " * 100 + "\r", end="")
            print(f"  [{label}] {phase} {completed}/{total}: {case_id} ({elapsed})")
        else:
            print(f"  [DONE] {case_id} {phase} {elapsed} - {label}")

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
            verb = "Generating response..." if phase == "TARGET" else "Judging response..."
            elapsed = time.monotonic() - started_at
            print(
                f"\r  [{frames[frame % len(frames)]}] {phase} {started}/{total}: {case_id} - {verb} {elapsed:.1f}s",
                end="",
                flush=True,
            )
            frame += 1


def build_parser() -> argparse.ArgumentParser:
    """Build the small command surface; invoking no command opens the wizard."""
    parser = argparse.ArgumentParser(
        description="Relationship Compass Eval Console V1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    run = subparsers.add_parser("run", help="run a selected subset without opening the wizard")
    _add_run_arguments(run)
    run.set_defaults(func=_command_run)

    retry = subparsers.add_parser("rerun-failed", help="run failed and/or error cases from a prior run")
    retry.add_argument("--from-run", required=True, type=Path)
    retry.add_argument(
        "--mode",
        choices=("failed-and-errors", "failed", "errors", "incomplete"),
        default="failed-and-errors",
    )
    _add_run_arguments(retry, include_eval=False, include_cases=False)
    retry.set_defaults(func=_command_rerun_failed)

    validate = subparsers.add_parser("validate", help="validate evals and local profile structure")
    validate.add_argument("--profiles-file", type=Path, default=runner.DEFAULT_PROVIDER_PROFILES)
    validate.add_argument("--results-root", type=Path, default=runner.RESULTS_BASE)
    validate.add_argument("--target-profile")
    validate.add_argument("--judge-profile")
    validate.add_argument("--debug", action="store_true")
    validate.set_defaults(func=_command_validate)

    history = subparsers.add_parser("history", help="show recent eval results")
    history.add_argument("--results-root", type=Path, default=runner.RESULTS_BASE)
    history.add_argument("--limit", type=int, default=10)
    history.set_defaults(func=_command_history)

    interactive = subparsers.add_parser("interactive", help="open the interactive Console")
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
        print("\nEvaluation interrupted.")
        print(f"Partial results saved: {exc.run_dir}")
        print(
            f"Target progress: {exc.completed_cases}/{exc.total_cases} saved; "
            f"{max(0, exc.total_cases - exc.completed_cases)} remaining."
        )
        print("Open Eval Console and choose 'Re-run failed cases' to resume incomplete work.")
        return 130
    except (CaseSelectionError, EvalConsoleError, OSError, ValueError) as exc:
        print(f"\nCould not continue: {exc}")
        if getattr(args, "debug", False):
            traceback.print_exc()
        return 1
    except KeyboardInterrupt:
        print("\nCancelled before an evaluation started.")
        return 130


def _add_run_arguments(
    parser: argparse.ArgumentParser,
    *,
    include_eval: bool = True,
    include_cases: bool = True,
) -> None:
    if include_eval:
        parser.add_argument("eval_id", help="an eval ID listed by 'python -m eval_console interactive'")
    if include_cases:
        cases = parser.add_mutually_exclusive_group()
        cases.add_argument("--case", action="append", help="one case ID or one-based position; repeatable")
        cases.add_argument("--cases", help="all, positions, IDs, ranges, or a mix (for example 1,3,5-8)")
    parser.add_argument("--profile", help="use the same configured profile for target and judge")
    parser.add_argument("--target-profile")
    parser.add_argument("--judge-profile")
    parser.add_argument("--profiles-file", type=Path, default=runner.DEFAULT_PROVIDER_PROFILES)
    parser.add_argument("--results-root", type=Path, default=runner.RESULTS_BASE)
    parser.add_argument("--run-id")
    parser.add_argument("--dry-run", action="store_true", help="validate and show the plan without API calls")
    parser.add_argument("--allow-dirty-debug", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1)
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
    print("\nConfiguration Validation")
    for check in report.checks:
        print(f"  [OK] {check}")
    for warning in report.warnings:
        print(f"  [WARN] {warning}")
    for error in report.errors:
        print(f"  [ERROR] {error}")
    if args.target_profile or args.judge_profile:
        if not args.target_profile or not args.judge_profile:
            raise EvalConsoleError("Use both --target-profile and --judge-profile for provider preflight.")
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
        print("  [OK] target provider preflight")
        print(f"    Target model: {target_plan['requested_model']}")
        print("  [OK] judge provider preflight")
        print(f"    Judge model: {judge_plan['requested_model']}")
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
        print("\nRelationship Compass\nEval Console V1\n" + "-" * 40)
        print("Environment")
        print(f"  Providers : {'[OK]' if profiles else '[WARN] not configured'}")
        print(f"  Judges    : {'[OK]' if any(item.supports_judge for item in profiles) else '[WARN] not configured'}")
        print(f"  Evals     : {len(evals)} found")
        print(f"  Results   : {results_root}")
        options: list[tuple[str, Callable[[], int | None]]] = [
            ("Run eval", lambda: _interactive_run(evals, profiles_file, results_root, debug)),
            ("Re-run failed cases", lambda: _interactive_rerun_failed(profiles_file, results_root, debug)),
            ("View evals", lambda: _print_evals(evals)),
            ("View previous results", lambda: _print_history(discover_runs(results_root))),
            (
                "Validate configuration",
                lambda: _command_validate(_validation_args(profiles_file, results_root, debug)),
            ),
            ("Exit", lambda: 0),
        ]
        choice = _choose(
            "What would you like to do", [(label, (label, action)) for label, action in options]
        )
        if choice[0] == "Exit":
            return 0
        result = choice[1]()
        if result not in (None, 0):
            print("\nThe action did not complete. You can adjust the selection and try again.")


def _interactive_run(
    evals: list[EvalDefinition], profiles_file: Path, results_root: Path, debug: bool
) -> int:
    definition = _choose("Select eval", [(item.title, item) for item in evals])
    print(f"Cases: {len(definition.cases)}\nDescription: {definition.description}")
    case_ids = _interactive_case_selection(definition, results_root)
    target_profile, judge_profile = _interactive_profiles(profiles_file)
    request = EvalRunRequest(
        eval_id=definition.eval_id,
        case_ids=tuple(case_ids),
        target_profile=target_profile.name,
        judge_profile=judge_profile.name,
        profiles_file=profiles_file,
        results_root=results_root,
        dry_run=_yes_no("Dry run first? It validates without calling APIs", default=False),
        allow_dirty_debug=_yes_no(
            "Allow a dirty-worktree debug run if needed? It cannot be a formal reference", default=False
        ),
        debug=debug,
    )
    print(
        f"\nReady: {len(case_ids)} selected from {len(definition.cases)} total\n"
        f"Target: {target_profile.name} ({target_profile.target_model or 'model resolved at preflight'})\n"
        f"Judge:  {judge_profile.name} ({judge_profile.judge_model or 'model resolved at preflight'})"
    )
    if not _yes_no("Start this eval", default=False):
        print("Nothing was run.")
        return 0
    return _execute_and_print(request)


def _interactive_case_selection(definition: EvalDefinition, results_root: Path) -> list[str]:
    modes = [
        ("All cases", "all"),
        ("Single case", "single"),
        ("Select multiple cases", "multiple"),
        ("Case range", "range"),
        ("Failed cases from previous run", "failed"),
    ]
    mode = _choose("Which cases do you want to run", modes)
    if mode == "all":
        return [case.case_id for case in definition.cases]
    if mode == "failed":
        candidates = [
            run for run in discover_runs(results_root)
            if run.eval_id in {None, definition.eval_id}
            and (run.failed_case_ids or run.error_case_ids or run.incomplete_case_ids)
        ]
        if not candidates:
            raise EvalConsoleError("No previous run contains failed, error, or incomplete cases for this eval.")
        run = _choose("Select a previous run", [(_run_label(item), item) for item in candidates])
        return list(failed_case_ids(run.run_dir, "failed-and-errors"))
    _print_cases(definition)
    if mode == "single":
        value = input("\nEnter one case position or case ID: ").strip()
    elif mode == "multiple":
        print("\nEnter positions/IDs such as 1,3,5-8. Type 'all' for Select all; type 'clear' to start over.")
        while True:
            value = input("Selection: ").strip()
            if value.lower() == "clear":
                print("Selection cleared. Enter the cases you want to run.")
                continue
            break
    else:
        value = input("\nEnter a range or mix, for example 1-10 or 1,3,5-10: ").strip()
    selected = parse_case_selection(value, [case.case_id for case in definition.cases])
    print("\nSelected cases:")
    for case_id in selected:
        print(f"  {case_id}")
    print(f"Total: {len(selected)} cases")
    return selected


def _interactive_profiles(profiles_file: Path) -> tuple[ProviderProfile, ProviderProfile]:
    profiles = discover_provider_profiles(profiles_file)
    targets = [profile for profile in profiles if profile.supports_target]
    judges = [profile for profile in profiles if profile.supports_judge]
    if not targets or not judges:
        raise EvalConsoleError(
            f"No usable target/judge profiles were found in {profiles_file}. "
            "Copy model_evals/provider_profiles.example.yaml to provider_profiles.local.yaml and configure it."
        )
    target = _choose(
        "Select target model/provider",
        [(_profile_label(profile, "target"), profile) for profile in targets],
    )
    judge = _choose(
        "Select judge",
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
        print("No prior failed, error, or incomplete cases were found.")
        return 0
    selected = _choose("Select a previous run", [(_run_label(item), item) for item in candidates])
    mode = _choose(
        "Re-run",
        [
            ("Failed + error + incomplete cases", "failed-and-errors"),
            ("Failed cases only", "failed"),
            ("Error cases only", "errors"),
            ("Incomplete cases only", "incomplete"),
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
        allow_dirty_debug=_yes_no("Allow a dirty-worktree debug run if needed", default=False),
        debug=debug,
    )
    return _execute_and_print(request)


def _execute_and_print(request: EvalRunRequest) -> int:
    target, judge, target_plan, judge_plan = preflight_request(request)
    print("\nPreflight validation")
    print(f"  [OK] Target: {target_plan['provider']} / {target_plan['requested_model']}")
    print(f"  [OK] Judge:  {judge_plan['provider']} / {judge_plan['requested_model']}")
    print(f"  [OK] Cases:  {len(request.case_ids)} selected")
    if request.dry_run:
        outcome = execute_request(request, target_provider=target, judge_provider=judge)
        print("\nDRY RUN - everything looks valid. No API calls were made.")
        print(f"Would execute: {len(request.case_ids)} cases; {len(request.case_ids) * 2} estimated API calls")
        print(f"Output path: {outcome.run_dir}")
        return 0
    print("\nRunning... progress is saved after each case.")
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
    case_id = str(record.get("case_id") or "unknown case")
    symbol = f"[{_result_label(phase, record)}]"
    suffix = f" ({record['error_code']})" if record.get("error_code") else ""
    print(f"  {symbol} {phase} {completed}/{total}: {case_id} - {status}{suffix}")


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
    print("\nEval complete\n" + "-" * 40)
    print(f"Passed:    {counts['PASS']}")
    print(f"Failed:    {counts['FAIL']}")
    print(f"Errors:    {counts['ERROR']}")
    print(f"Incomplete:{counts['INCOMPLETE']}")
    print(f"Cases:     {selected} selected from {total} total")
    print(f"Status:    {summary.get('completion_status')} ({summary_counts.get('total_cases', selected)} cases reported)")
    print(f"Result:    {outcome.run_dir}")


def _print_evals(evals: list[EvalDefinition]) -> None:
    print("\nAvailable evals")
    for definition in evals:
        print(f"  {definition.eval_id}: {definition.title} ({len(definition.cases)} cases)")
        print(f"    {definition.description}")


def _print_cases(definition: EvalDefinition) -> None:
    print("\nCases")
    for index, case in enumerate(definition.cases, start=1):
        print(f"  {index:>2}. {case.case_id}  {case.title}")


def _print_history(runs: list[HistoricalRun]) -> None:
    print("\nRecent runs")
    if not runs:
        print("  No result artifacts found yet.")
        return
    for index, run in enumerate(runs, start=1):
        passed = "?" if run.passed_cases is None else str(run.passed_cases)
        print(
            f"  {index}. {run.run_id} - {run.total_cases} cases, pass {passed}, "
            f"fail {len(run.failed_case_ids)}, errors {len(run.error_case_ids)}, "
            f"incomplete {len(run.incomplete_case_ids)} [{run.state}]"
        )
        print(f"     {run.created_at or 'unknown time'}  {run.run_dir}")


def _choose(prompt: str, choices: list[tuple[str, T]]) -> T:
    print(f"\n{prompt}:")
    for number, (label, _) in enumerate(choices, start=1):
        print(f"  {number}. {label}")
    while True:
        value = input("Enter a number: ").strip()
        if value.isdigit() and 1 <= int(value) <= len(choices):
            return choices[int(value) - 1][1]
        print(f"Please enter a number from 1 to {len(choices)}.")


def _yes_no(prompt: str, *, default: bool) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        value = input(f"{prompt} {suffix}: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer y or n.")


def _profile_label(profile: ProviderProfile, role: str) -> str:
    model = profile.target_model if role == "target" else profile.judge_model
    return f"{profile.name} - provider: {profile.provider or 'configured per role'}, model: {model or 'resolved at preflight'}"


def _run_label(run: HistoricalRun) -> str:
    return (
        f"{run.run_id} ({len(run.failed_case_ids)} failed, "
        f"{len(run.error_case_ids)} errors, {len(run.incomplete_case_ids)} incomplete)"
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
