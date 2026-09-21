#!/usr/bin/env python3
"""Audit runtime reference ownership and known selector-leak regressions."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "tests" / "fixtures" / "runtime_reference_roles.json"
CANONICAL_SELECTOR = "references/personal/回复决策与对话流.md"
HOOK_PROVIDER = "references/personal/主动话题与conversation-hook.md"
ALLOWED_ROLES = {
    "CANONICAL_SELECTOR",
    "CONSTRAINT_PROVIDER",
    "EVIDENCE_PROVIDER",
    "REALIZATION_PROVIDER",
    "COMPOSITION_PROVIDER",
    "STYLE_GROWTH_PROVIDER",
    "REVIEW_TRAINING_ONLY",
    "SAFETY_KNOWLEDGE",
    "GENERATED_MIRROR",
}
PRACTICAL_ROLES = {
    "CONSTRAINT_PROVIDER",
    "EVIDENCE_PROVIDER",
    "COMPOSITION_PROVIDER",
    "STYLE_GROWTH_PROVIDER",
    "REVIEW_TRAINING_ONLY",
}
HIGH_RISK_PRACTICAL = (
    "references/practical/00-导读与使用分级.md",
    "references/practical/为他人提供情绪价值：温暖且有效的回应指南.md",
    "references/practical/巧妙接话技巧：让沟通更流畅的实用指南.md",
    "references/practical/聊天化被动为主动：引导互动的实用指南.md",
)
LEGACY_LEAK_PHRASES = (
    "用可观察反馈决定继续、降级或停止",
    "适度分享或留白",
    "再延伸话题（问细节、聊相关经历）",
    "用“提问”或“分享”接话",
    "先“分享”再“提问”",
    "先肯定对方，再用“分享+提问”延伸",
    "再问“吃零食”的细节",
    "再问“环节”细节",
    "再问对方“有没有类似经历”",
    "再轻微引导对方继续说",
    "分享自己的类似经历拉近距离",
    "分享“很多人都这样”减少对方的孤独感",
    "先顺势接住 + 再把话题引向见面或具体行动",
    "可在合理间隔后根据新信息换时间或场景再具体邀请一次",
    "用“提问/分享”开启新话题",
    "最后自然转话题，不冷场",
    "自然救场",
)
EXPECTED_ACTIONS = {
    "ACKNOWLEDGE",
    "EMPATHIZE",
    "SHARE",
    "ASK",
    "CLARIFY",
    "PLAY",
    "TOPIC_SHIFT",
    "INVITE",
    "REPAIR",
    "BOUNDARY",
    "DEESCALATE",
    "LEAVE_SPACE",
    "CLOSE",
    "WAIT",
}


def relative(path: Path, root: Path = ROOT) -> str:
    return path.relative_to(root).as_posix()


def discover_runtime_references(root: Path = ROOT) -> set[str]:
    paths = {
        "SKILL.md",
        "chatgpt-project/PROJECT_INSTRUCTIONS.md",
    }
    for directory in (
        "shared",
        "references/personal",
        "references/practical",
        "references/knowledge",
        "references/curated",
        "chatgpt-project/generated-knowledge",
    ):
        paths.update(relative(path, root) for path in (root / directory).glob("*.md"))
    return paths


def load_role_registry(path: Path = REGISTRY_PATH) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("roles"), dict):
        raise ValueError("runtime role registry must use schema_version 1 and grouped roles")
    assignments: dict[str, str] = {}
    for role, paths in payload["roles"].items():
        if role not in ALLOWED_ROLES:
            raise ValueError(f"unknown runtime role: {role}")
        if not isinstance(paths, list):
            raise ValueError(f"role {role} must contain a path list")
        for item in paths:
            if item in assignments:
                raise ValueError(f"runtime reference has multiple roles: {item}")
            assignments[item] = role
    return assignments


def collect_errors(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    registry_path = root / "tests" / "fixtures" / "runtime_reference_roles.json"
    try:
        assignments = load_role_registry(registry_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"cannot load runtime role registry: {exc}"]

    discovered = discover_runtime_references(root)
    declared = set(assignments)
    for missing in sorted(discovered - declared):
        errors.append(f"runtime reference has no declared role: {missing}")
    for extra in sorted(declared - discovered):
        errors.append(f"role registry path is not runtime-reachable: {extra}")

    selectors = sorted(path for path, role in assignments.items() if role == "CANONICAL_SELECTOR")
    if selectors != [CANONICAL_SELECTOR]:
        errors.append(f"canonical selector must be exactly {CANONICAL_SELECTOR}; got {selectors}")

    for path, role in assignments.items():
        if path.startswith("references/practical/") and role not in PRACTICAL_ROLES:
            errors.append(f"practical reference has invalid role {role}: {path}")

    expected_roles = {
        "references/personal/幽默与调侃生成器.md": "REALIZATION_PROVIDER",
        HOOK_PROVIDER: "REALIZATION_PROVIDER",
        "references/personal/投入预算与停止条件.md": "CONSTRAINT_PROVIDER",
    }
    for path, expected_role in expected_roles.items():
        if assignments.get(path) != expected_role:
            errors.append(f"{path} must use role {expected_role}")

    canonical_paths = sorted(
        path for path, role in assignments.items() if role != "GENERATED_MIRROR"
    )
    for path in canonical_paths:
        target = root / path
        if not target.is_file():
            continue
        content = target.read_text(encoding="utf-8")
        for phrase in LEGACY_LEAK_PHRASES:
            if phrase in content:
                errors.append(f"known selector/composite leak in {path}: {phrase}")

    decision = (root / CANONICAL_SELECTOR).read_text(encoding="utf-8")
    actions = set(re.findall(r"^\| `([A-Z_]+)` \|", decision, flags=re.MULTILINE))
    if actions != EXPECTED_ACTIONS:
        errors.append(f"Primary Action taxonomy changed: {sorted(actions)}")
    for marker in (
        "Architecture Marker: COMPOSITION_GATING_V1",
        "Architecture Marker: DECISION_REALIZATION_FLOW_V1",
        "0..N explicitly permitted supporting functions",
        "same-action repair",
        "reject candidate → return Decision Layer",
        "`WAIT` 不产生任何可发送消息",
        "`LEAVE_SPACE` 只可带不制造接续义务的 acknowledgment",
        "`CLOSE` 不加尾钩",
    ):
        if marker not in decision:
            errors.append(f"Decision Layer missing composition guardrail: {marker}")

    handoff_fields = (
        "primary_action",
        "supporting_functions",
        "hard_constraints",
        "stop_conditions",
        "realization_permissions",
        "decision_basis",
    )
    for field in handoff_fields:
        if f"| `{field}` |" not in decision:
            errors.append(f"Decision Handoff missing field: {field}")
    for marker in (
        "## Unified entry paths",
        "Type A — realization failure",
        "Type B — decision-level conflict",
        "同一 action + 同一 rejection reason",
        "no realization provider；no sendable candidate",
    ):
        if marker not in decision:
            errors.append(f"Decision Layer missing B2 flow guardrail: {marker}")

    natural = (root / "references/personal/自然回复生成器.md").read_text(
        encoding="utf-8"
    )
    for marker in (
        "Architecture Marker: DECISION_REALIZATION_FLOW_V1",
        "## Decision Handoff 输入",
        "## Validation outcome 与 failure handling",
        "不得附带 replacement action",
    ):
        if marker not in natural:
            errors.append(f"Natural Reply missing B2 lifecycle guardrail: {marker}")

    hook = (root / HOOK_PROVIDER).read_text(encoding="utf-8")
    for marker in (
        "Architecture Marker: HOOK_MATERIAL_PROVIDER_V1",
        "material candidates only",
        "no action permission",
        "no replacement action",
        "no supporting function generation",
        "no invite generation",
        "no close/wait selection",
        "no material candidate",
    ):
        if marker not in hook:
            errors.append(f"Hook missing material-provider guardrail: {marker}")

    for path in HIGH_RISK_PRACTICAL:
        content = (root / path).read_text(encoding="utf-8")
        for marker in (
            "Architecture Marker: PRACTICAL_COMPOSITION_GATING_V1",
            "Decision Layer",
            "Primary Action",
            "supporting",
            "WAIT",
            "LEAVE_SPACE",
            "CLOSE",
        ):
            if marker not in content:
                errors.append(f"high-risk practical missing gating marker {marker}: {path}")

    return errors


def role_counts(root: Path = ROOT) -> Counter[str]:
    path = root / "tests" / "fixtures" / "runtime_reference_roles.json"
    return Counter(load_role_registry(path).values())


def main() -> int:
    errors = collect_errors(ROOT)
    try:
        counts = role_counts(ROOT)
    except (OSError, json.JSONDecodeError, ValueError):
        counts = Counter()
    print(f"runtime references scanned: {sum(counts.values())}")
    for role, count in sorted(counts.items()):
        print(f"  {role}: {count}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("canonical selector count: 1")
    print("unresolved selector/composite leakage: 0")
    print("decision-layer ownership closure audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
