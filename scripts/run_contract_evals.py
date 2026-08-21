#!/usr/bin/env python3
"""Validate eval definitions and product contracts without running a language model."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = ROOT / "evals"
REQUIRED = {
    "screenshot_cases.yaml": "screenshot",
    "reply_cases.yaml": "reply",
    "stage_cases.yaml": "stage",
    "expression_growth_cases.yaml": "expression_growth",
    "interview_mode_cases.yaml": "interview_mode",
    "continuation_cases.yaml": "continuation",
    "memory_cases.yaml": "memory",
    "review_cases.yaml": "review",
    "actual_send_cases.yaml": "actual_send",
}


class ContractError(RuntimeError):
    pass


def load_json_yaml(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[-1].startswith("# Modified by AI on "):
        lines.pop()
    try:
        data = json.loads("\n".join(lines))
    except json.JSONDecodeError as exc:
        raise ContractError(f"{path.name}: invalid JSON-compatible YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("cases"), list):
        raise ContractError(f"{path.name}: root must contain a cases list")
    return data


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def validate_generic(filename: str, expected_suite: str, data: dict[str, Any], ids: set[str]) -> None:
    require(data.get("suite") == expected_suite, f"{filename}: unexpected suite")
    require(bool(data["cases"]), f"{filename}: cases must not be empty")
    for case in data["cases"]:
        require(isinstance(case, dict), f"{filename}: case must be an object")
        case_id = case.get("id")
        require(isinstance(case_id, str) and case_id, f"{filename}: missing id")
        require(case_id not in ids, f"duplicate case id: {case_id}")
        ids.add(case_id)
        require(isinstance(case.get("input"), dict), f"{case_id}: input must be an object")
        require(isinstance(case.get("expected"), dict), f"{case_id}: expected must be an object")


def validate_coverage(suites: dict[str, dict[str, Any]]) -> None:
    continuation = {
        case["input"].get("partner_outcome") for case in suites["continuation"]["cases"]
    }
    require(
        continuation == {"positive_play", "counter_tease", "ordinary", "no_play"},
        "continuation_cases must cover positive_play, counter_tease, ordinary, no_play",
    )
    for case in suites["continuation"]["cases"]:
        expected = case["expected"]
        require(
            expected.get("test_first_line_and_followup") is True,
            f"{case['id']}: must test continuation",
        )
        require("fallback" in expected, f"{case['id']}: missing fallback")

    realtime = [case for case in suites["reply"]["cases"] if case["input"].get("realtime")]
    require(bool(realtime), "reply_cases must include realtime mode")
    for case in realtime:
        require(case["expected"].get("show_e_label") is False, f"{case['id']}: realtime must hide E")
        require(
            case["expected"].get("show_teaching") is False,
            f"{case['id']}: realtime must hide teaching",
        )

    growth_kinds = {case["input"].get("focus") for case in suites["expression_growth"]["cases"]}
    for focus in {"cap_not_default", "user_vs_partner", "autonomy", "online_offline", "capability_profile"}:
        require(focus in growth_kinds, f"growth evals must cover {focus}")

    interview_actions = {
        case["expected"].get("next_primary_action") for case in suites["interview_mode"]["cases"]
    }
    require("bare_question" not in interview_actions, "interview mode must not recommend a bare question")

    memory_kinds = {case["input"].get("operation") for case in suites["memory"]["cases"]}
    for operation in {
        "style_review",
        "object_isolation",
        "autonomy_update",
        "context_requires_subject",
        "landmark_retention",
        "timeline",
        "field_delete",
        "technique_history",
        "capability_profile",
    }:
        require(operation in memory_kinds, f"memory evals must cover {operation}")

    reply_features = {case["input"].get("feature") for case in suites["reply"]["cases"]}
    require("no_technique" in reply_features, "reply evals must allow no-technique output")
    require("repetition" in reply_features, "reply evals must cover technique repetition")

    gray = [case for case in suites["stage"]["cases"] if case["input"].get("partner_feedback") == "gray"]
    require(bool(gray), "stage evals must include gray / insufficient-evidence feedback")
    require(
        all(case["expected"].get("auto_downgrade") is False for case in gray),
        "gray feedback must not automatically downgrade expression or investment",
    )

    for case in suites["review"]["cases"]:
        expected = case["expected"]
        require(expected.get("separate_user_and_partner") is True, f"{case['id']}: review must split tracks")
        require(expected.get("next_training_focus_count") == 1, f"{case['id']}: review must pick one focus")

    for case in suites["actual_send"]["cases"]:
        expected = case["expected"]
        require(expected.get("compare_suggestion_and_actual") is True, f"{case['id']}: must compare actual send")
        require(expected.get("partner_feedback_decides_quality") is False, f"{case['id']}: partner feedback cannot decide quality")
        require(expected.get("stable_update_requires_confirmation") is True, f"{case['id']}: stable update needs confirmation")


def main() -> int:
    try:
        suites: dict[str, dict[str, Any]] = {}
        ids: set[str] = set()
        for filename, suite in REQUIRED.items():
            path = EVAL_DIR / filename
            require(path.is_file(), f"missing eval file: {filename}")
            data = load_json_yaml(path)
            validate_generic(filename, suite, data, ids)
            suites[suite] = data
        validate_coverage(suites)
    except (OSError, ContractError) as exc:
        print(f"ERROR: {exc}")
        return 1
    total = sum(len(data["cases"]) for data in suites.values())
    print(
        f"contract eval validation passed: {len(suites)} suites, {total} cases; "
        "no model behavior was executed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Modified by AI on 2026-08-21 13:48:02
