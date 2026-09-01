"""Typed data exchanged between the Eval Console's UI and execution layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


CURRENT_CONSOLE_SCHEMA_VERSION = 4
EVAL_CONSOLE_VERSION = "1.2A"


@dataclass(frozen=True)
class EvalCase:
    """A discoverable case with enough information for a concise picker."""

    case_id: str
    title: str
    summary: str


@dataclass(frozen=True)
class EvalDefinition:
    """The current runner-backed eval available to the console."""

    eval_id: str
    title: str
    description: str
    source_path: Path
    cases: tuple[EvalCase, ...]


@dataclass(frozen=True)
class ProviderProfile:
    """One locally configured profile and its supported execution roles."""

    name: str
    provider: str | None
    target_model: str | None
    judge_model: str | None
    supports_target: bool
    supports_judge: bool


class EvalExecutionMode(str, Enum):
    """The explicit stages supported by a Console evaluation request."""

    FULL = "FULL"
    TARGET_ONLY = "TARGET_ONLY"
    JUDGE_ONLY = "JUDGE_ONLY"
    RESUME = "RESUME"


class JudgeCaseSelector(str, Enum):
    """Reusable filters over successful historical Target responses."""

    ALL_TARGET = "ALL_TARGET"
    JUDGE_ERROR = "JUDGE_ERROR"
    JUDGE_MISSING = "JUDGE_MISSING"
    JUDGE_ERROR_OR_MISSING = "JUDGE_ERROR_OR_MISSING"
    SELECTED = "SELECTED"


@dataclass(frozen=True)
class CaseStagePlan:
    """One Case's stage decision, kept independent from Console presentation."""

    case_id: str
    run_target: bool
    run_judge: bool
    reason: str


@dataclass(frozen=True)
class StagePlan:
    """A deterministic plan for one stage-aware execution request."""

    mode: EvalExecutionMode
    cases: tuple[CaseStagePlan, ...]

    @property
    def target_cases(self) -> tuple[str, ...]:
        return tuple(item.case_id for item in self.cases if item.run_target)

    @property
    def judge_cases(self) -> tuple[str, ...]:
        return tuple(item.case_id for item in self.cases if item.run_judge)

    @property
    def skipped_cases(self) -> tuple[str, ...]:
        return tuple(
            item.case_id for item in self.cases if not item.run_target and not item.run_judge
        )


@dataclass(frozen=True)
class EvalRunRequest:
    """A fully resolved request shared by interactive and non-interactive runs."""

    eval_id: str
    case_ids: tuple[str, ...]
    target_profile: str | None
    judge_profile: str | None
    profiles_file: Path
    results_root: Path
    dry_run: bool = False
    debug: bool = False
    allow_dirty_debug: bool = False
    concurrency: int = 1
    run_id: str | None = None
    target_model_override: str | None = None
    judge_model_override: str | None = None
    continue_on_error: bool = True
    mode: EvalExecutionMode = EvalExecutionMode.FULL
    source_run_dir: Path | None = None
    judge_selector: JudgeCaseSelector = JudgeCaseSelector.SELECTED
    resume_target_model: str | None = None
    resume_judge_model: str | None = None


@dataclass(frozen=True)
class ValidationReport:
    """Non-network configuration validation outcome."""

    checks: tuple[str, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class RunOutcome:
    """Result returned after a dry-run, completed run, or partial run."""

    run_dir: Path
    dry_run: bool
    summary: dict[str, object] | None
    metadata: dict[str, object] | None
    target_plan: dict[str, object]
    judge_plan: dict[str, object]
    api_calls: dict[str, int] = field(default_factory=lambda: {"target": 0, "judge": 0})
