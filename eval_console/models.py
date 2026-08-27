"""Typed data exchanged between the Eval Console's UI and execution layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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


@dataclass(frozen=True)
class EvalRunRequest:
    """A fully resolved request shared by interactive and non-interactive runs."""

    eval_id: str
    case_ids: tuple[str, ...]
    target_profile: str
    judge_profile: str
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
