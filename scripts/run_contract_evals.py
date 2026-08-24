#!/usr/bin/env python3
"""Validate compact product contracts without running a language model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "evals" / "contract_cases.yaml"
REQUIRED_EXPECTATIONS: dict[str, dict[str, Any]] = {
    "one_best_reply": {
        "one_best_reply": True,
        "automatic_parallel_options": False,
        "long_analysis_required": False,
        "reason_at_most_one_brief": True,
    },
    "explicit_multiple_versions": {
        "multiple_versions_allowed": True,
        "one_best_reply_mandatory": False,
        "honor_requested_count": True,
    },
    "reply_depth_routing": {
        "reply_first": True,
        "long_analysis_required": False,
        "sendable_content_first": True,
    },
    "semantic_chunking": {
        "chunk_by_semantic_function": True,
        "two_bubbles_allowed": True,
        "character_threshold_rule": False,
    },
    "no_forced_chunking": {
        "force_split_short_reply": False,
        "single_bubble_allowed": True,
    },
    "unknown_user_fact": {
        "invent_user_fact": False,
        "unknown_remains_unknown": True,
        "prefer_fact_independent_reply": True,
        "hypothesis_as_fact": False,
    },
    "confirmed_memory_use": {
        "use_relevant_confirmed_fact": True,
        "user_scope_cross_object_allowed": True,
        "current_object_fact_allowed": True,
        "cross_object_fact_reuse": False,
        "relationship_scope_current_pair_only": True,
        "leak_irrelevant_memory": False,
    },
    "normal_tone_calibration": {
        "user_style_first": True,
        "light_humor_allowed": True,
        "mechanically_copy_partner_style": False,
    },
    "serious_mode": {
        "serious_tone": True,
        "tease_or_flirt": False,
        "forced_positivity": False,
        "comfort_essay_required": False,
    },
    "boundary_serious_mode": {
        "respect_boundary": True,
        "continue_pressure": False,
        "reengagement_hook": False,
    },
    "continuation_low_investment": {
        "forced_followup_question": False,
        "return_ownership": True,
        "natural_stop_allowed": True,
    },
    "continuation_partner_opens": {
        "continue_open_thread": True,
        "follow_one_relevant_point": True,
    },
    "concise_style_consistency": {
        "avoid_persona_jump": True,
        "preserve_concise_density": True,
        "sudden_emoji_flood": False,
    },
    "neutral_growth_goal": {
        "fixed_pleasing_persona_goal": False,
        "independent_judgment_goal": True,
        "manipulative_rules": False,
    },
    "uncertainty_over_inference": {
        "single_event_not_enough": True,
        "single_event_not_enough_for_trend_change": True,
        "must_express_uncertainty": True,
        "mind_reading_not_allowed": True,
        "automatic_negative_stage_change": False,
        "need_trend_or_more_evidence": True,
    },
    "stage_trend_separation": {
        "stage_and_trend_separate": True,
        "stage_not_auto_upgraded": True,
        "warming_trend_allowed": True,
    },
    "risk_evidence": {
        "risk_must_be_evidence_based": True,
        "single_weak_signal_not_high_risk": True,
        "certainty_should_match_evidence": True,
    },
    "insufficient_information": {
        "insufficient_information": True,
        "do_not_invent_context": True,
        "ask_or_identify_key_missing_evidence": True,
        "do_not_force_stage_label": True,
    },
    "user_emotional_input": {
        "acknowledge_user_emotion": True,
        "reinforce_catastrophizing": False,
        "evidence_boundary_still_applies": True,
    },
    "facts_vs_hypotheses": {
        "separate_fact_and_hypothesis": True,
        "promote_theory_to_fact": False,
    },
    "explicit_boundary_priority": {
        "boundary_remains_valid": True,
        "warm_interaction_does_not_override_boundary": True,
        "trend_and_boundary_can_coexist": True,
    },
    "conflicting_evidence": {
        "conflicting_evidence": True,
        "force_single_positive_conclusion": False,
        "force_single_negative_conclusion": False,
    },
    "baseline_relative_trend": {
        "fixed_reply_time_rule": False,
        "relative_to_baseline": True,
        "automatic_cooling": False,
    },
    "key_evidence_selection": {
        "prioritize_high_value_evidence": True,
        "line_by_line_analysis_required": False,
    },
    "memory_consent": {
        "stable_write_without_consent": False,
        "ask_explicit_consent": True,
    },
    "memory_isolation": {
        "copy_object_memory": False,
        "shared_user_skill_allowed": True,
    },
    "knowledge_privacy": {
        "approved_sources_only": True,
        "use_proposal_as_runtime": False,
        "store_raw_chat": False,
    },
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
    require(len(cases) == len(REQUIRED_EXPECTATIONS), f"contract eval must contain {len(REQUIRED_EXPECTATIONS)} cases",)
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
        for key, value in REQUIRED_EXPECTATIONS[category].items():
            require(key in expected, f"{case_id}: missing required expectation {key}")
            require(expected[key] == value, f"{case_id}: expected {key}={value!r}")
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
