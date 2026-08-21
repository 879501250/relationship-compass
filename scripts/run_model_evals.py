#!/usr/bin/env python3
"""Prepare and aggregate human-judged model behavioral evals; never calls a model itself."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "model_evals" / "cases.yaml"
RUBRIC_PATH = ROOT / "model_evals" / "rubric.yaml"


class ModelEvalError(RuntimeError):
    pass


def load_json_yaml(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[-1].startswith("# Modified by AI on "):
        lines.pop()
    try:
        data = json.loads("\n".join(lines))
    except json.JSONDecodeError as exc:
        raise ModelEvalError(f"{path.name}: invalid JSON-compatible YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelEvalError(f"{path.name}: root must be an object")
    return data


def load_definitions() -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    case_data = load_json_yaml(CASES_PATH)
    rubric_data = load_json_yaml(RUBRIC_PATH)
    cases = case_data.get("cases")
    criteria = rubric_data.get("criteria")
    if not isinstance(cases, list) or not cases:
        raise ModelEvalError("cases.yaml must contain a non-empty cases list")
    if not isinstance(criteria, dict) or not criteria:
        raise ModelEvalError("rubric.yaml must contain a non-empty criteria object")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ModelEvalError("each model eval case must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ModelEvalError(f"invalid or duplicate case id: {case_id!r}")
        seen.add(case_id)
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            raise ModelEvalError(f"{case_id}: prompt is required")
        required = case.get("required_criteria")
        if not isinstance(required, list) or not required:
            raise ModelEvalError(f"{case_id}: required_criteria must be a non-empty list")
        unknown = set(required) - set(criteria)
        if unknown:
            raise ModelEvalError(f"{case_id}: unknown criteria: {', '.join(sorted(unknown))}")
    return cases, criteria


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ModelEvalError(f"{path}:{line_number}: invalid JSONL") from exc
        if not isinstance(record, dict):
            raise ModelEvalError(f"{path}:{line_number}: record must be an object")
        records.append(record)
    return records


def command_validate(_: argparse.Namespace) -> int:
    cases, criteria = load_definitions()
    print(
        f"model eval definitions valid: {len(cases)} cases, {len(criteria)} criteria; "
        "behavioral evaluation NOT RUN"
    )
    return 0


def command_prepare(args: argparse.Namespace) -> int:
    cases, criteria = load_definitions()
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for case in cases:
        lines.append(
            json.dumps(
                {
                    "case_id": case["id"],
                    "title": case["title"],
                    "mode": case["mode"],
                    "skill_path": str(ROOT / "SKILL.md"),
                    "prompt": case["prompt"],
                    "rubric": {
                        criterion: criteria[criterion]["question"]
                        for criterion in case["required_criteria"]
                    },
                },
                ensure_ascii=False,
            )
        )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"prepared {len(lines)} model eval work items: {output}")
    print("Run the actual Skill for each prompt, then record verbatim outputs and independent judgments.")
    return 0


def command_judge(args: argparse.Namespace) -> int:
    cases, _ = load_definitions()
    response_records = load_jsonl(Path(args.responses))
    judgment_records = load_jsonl(Path(args.judgments))
    responses = {item.get("case_id"): item for item in response_records}
    judgments = {item.get("case_id"): item for item in judgment_records}
    expected_ids = {case["id"] for case in cases}
    if (
        len(response_records) != len(expected_ids)
        or len(judgment_records) != len(expected_ids)
        or set(responses) != expected_ids
        or set(judgments) != expected_ids
    ):
        raise ModelEvalError("responses and judgments must each contain every case exactly once")
    failed: list[dict[str, Any]] = []
    for case in cases:
        case_id = case["id"]
        output = responses[case_id].get("output")
        if not isinstance(output, str) or not output.strip():
            raise ModelEvalError(f"{case_id}: missing verbatim model output")
        judgment = judgments[case_id]
        if not isinstance(judgment.get("judge"), str) or not judgment["judge"].strip():
            raise ModelEvalError(f"{case_id}: human or independent judge identity is required")
        results = judgment.get("criteria")
        if not isinstance(results, dict):
            raise ModelEvalError(f"{case_id}: criteria judgment object is required")
        required = set(case["required_criteria"])
        if set(results) != required or any(not isinstance(value, bool) for value in results.values()):
            raise ModelEvalError(f"{case_id}: every required criterion needs an explicit boolean")
        failed_criteria = sorted(key for key, passed in results.items() if not passed)
        if failed_criteria:
            failed.append({"case_id": case_id, "failed_criteria": failed_criteria})
    summary = {
        "evaluation_type": "model_behavioral",
        "source": "verbatim_outputs_plus_explicit_external_judgments",
        "total": len(cases),
        "passed": len(cases) - len(failed),
        "failed": failed,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.set_defaults(func=command_validate)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--output", required=True)
    prepare.set_defaults(func=command_prepare)
    judge = subparsers.add_parser("judge")
    judge.add_argument("--responses", required=True)
    judge.add_argument("--judgments", required=True)
    judge.set_defaults(func=command_judge)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return args.func(args)
    except (OSError, ModelEvalError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

# Modified by AI on 2026-08-21 13:48:02
