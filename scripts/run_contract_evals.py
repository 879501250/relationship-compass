#!/usr/bin/env python3
"""Validate compact product contracts without running a language model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "contract_cases.yaml"
REQUIRED_EXPECTATIONS: dict[str, tuple[str, Any]] = {
    "signal_analysis": ("prioritize_repeated_behavior", True),
    "uncertainty_expression": ("label_uncertainty", True),
    "avoid_over_inference": ("mind_reading", False),
    "reply_advice": ("sendable_first", True),
    "reply_styles": ("styles_distinct", True),
    "boundary_judgment": ("stop_progression", True),
    "relationship_stage": ("separate_stage_and_trend", True),
    "risk_signals": ("evidence_based_risk", True),
    "facts_vs_hypotheses": ("separate_fact_and_hypothesis", True),
    "memory_consent": ("stable_write_without_consent", False),
    "memory_recall": ("subject_id_required", True),
    "memory_isolation": ("copy_object_memory", False),
    "knowledge_use": ("approved_sources_only", True),
    "privacy_boundary": ("store_raw_chat", False),
    "insufficient_information": ("ask_one_decisive_question", True),
    "emotional_input": ("acknowledge_emotion_first", True),
    "ambiguous_relationship": ("state_uncertainty", True),
    "follow_up": ("test_first_line_and_followup", True),
}


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def load_cases() -> list[dict[str, Any]]:
    try:
        data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContractError(f"{CASES_PATH.name}: invalid JSON-compatible YAML: {exc}") from exc
    require(
        isinstance(data, dict) and data.get("suite") == "relationship_compass_contracts",
        f"{CASES_PATH.name}: unexpected suite",
    )
    cases = data.get("cases")
    require(isinstance(cases, list), f"{CASES_PATH.name}: root must contain a cases list")
    return cases


def validate_cases(cases: list[dict[str, Any]]) -> None:
    require(len(cases) == len(REQUIRED_EXPECTATIONS), "contract eval must contain 18 cases")
    ids: set[str] = set()
    categories: set[str] = set()
    for case in cases:
        require(isinstance(case, dict), "each case must be an object")
        case_id = case.get("id")
        category = case.get("category")
        require(isinstance(case_id, str) and case_id, "case id is required")
        require(case_id not in ids, f"duplicate case id: {case_id}")
        ids.add(case_id)
        require(
            isinstance(category, str) and category in REQUIRED_EXPECTATIONS,
            f"{case_id}: unknown category {category!r}",
        )
        require(category not in categories, f"duplicate category: {category}")
        categories.add(category)
        require(isinstance(case.get("input"), dict), f"{case_id}: input must be an object")
        expected = case.get("expected")
        require(isinstance(expected, dict), f"{case_id}: expected must be an object")
        key, value = REQUIRED_EXPECTATIONS[category]
        require(expected.get(key) == value, f"{case_id}: expected {key}={value!r}")
    require(categories == set(REQUIRED_EXPECTATIONS), "contract eval category coverage is incomplete")


def main() -> int:
    try:
        cases = load_cases()
        validate_cases(cases)
    except (OSError, ContractError) as exc:
        print(f"ERROR: {exc}")
        return 1
    print(
        f"contract eval validation passed: {len(cases)} cases, {len(REQUIRED_EXPECTATIONS)} categories; "
        "no model behavior was executed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
