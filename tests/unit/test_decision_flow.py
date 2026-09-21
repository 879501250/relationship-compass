"""Executable documentation contracts for v1.7-B2 decision realization flow."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as model_runner  # noqa: E402


DECISION_PATH = "references/personal/回复决策与对话流.md"
NATURAL_PATH = "references/personal/自然回复生成器.md"
HOOK_PATH = "references/personal/主动话题与conversation-hook.md"
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
EXPECTED_HANDOFF_FIELDS = {
    "primary_action",
    "supporting_functions",
    "hard_constraints",
    "stop_conditions",
    "realization_permissions",
    "decision_basis",
}


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def section(content: str, heading: str, next_heading: str) -> str:
    return content.split(heading, 1)[1].split(next_heading, 1)[0]


def table_rows(content: str) -> dict[str, tuple[str, ...]]:
    rows: dict[str, tuple[str, ...]] = {}
    for line in content.splitlines():
        match = re.match(r"^\| `([^`]+)` \| (.+) \| (.+) \|$", line)
        if match:
            rows[match.group(1)] = (match.group(2), match.group(3))
    return rows


def two_column_rows(content: str) -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in content.splitlines():
        match = re.match(r"^\| ([^|]+?) \| (.+) \|$", line)
        if (
            match
            and not match.group(1).startswith("---")
            and match.group(1).strip() != "Entry"
        ):
            rows[match.group(1).strip()] = match.group(2)
    return rows


class DecisionRealizationFlowTests(unittest.TestCase):
    def test_handoff_is_complete_ephemeral_and_not_a_secondary_selector(self) -> None:
        decision = read(DECISION_PATH)
        handoff = section(
            decision,
            "## Decision Handoff 与 realization routing",
            "## Unified entry paths",
        )
        fields = set(re.findall(r"^\| `([a-z_]+)` \|", handoff, re.MULTILINE))

        self.assertEqual(fields, EXPECTED_HANDOFF_FIELDS)
        self.assertIn("Architecture Marker: DECISION_REALIZATION_FLOW_V1", handoff)
        self.assertIn("只在当前轮使用", handoff)
        self.assertIn("不是持久化 schema", handoff)
        self.assertIn("不包含 secondary selector", handoff)
        self.assertIn("action score", handoff)
        self.assertIn("概率", handoff)

    def test_action_specific_routes_preserve_provider_ownership(self) -> None:
        decision = read(DECISION_PATH)
        routing = section(
            decision,
            "Provider 按动作按需加载",
            "`WAIT` 是系统对",
        )
        rows = table_rows(routing)

        self.assertEqual(set(rows), EXPECTED_ACTIONS)
        self.assertIn("Humor provider → Natural Reply", rows["PLAY"][0])
        self.assertIn("Hook material → Natural Reply", rows["TOPIC_SHIFT"][0])
        self.assertIn("invite practical component → Natural Reply", rows["INVITE"][0])
        self.assertIn("handoff 明确允许", rows["EMPATHIZE"][0])
        self.assertIn("no realization provider", rows["WAIT"][0])
        self.assertIn("no sendable candidate", rows["WAIT"][0])
        for stop_action in ("LEAVE_SPACE", "CLOSE"):
            self.assertIn("minimal Natural Reply", rows[stop_action][0])
            self.assertNotIn("Hook", rows[stop_action][0])

    def test_hook_contract_returns_material_only(self) -> None:
        hook = read(HOOK_PATH)
        contract = section(hook, "## Provider output contract", "## 素材来源")

        self.assertIn("Architecture Marker: HOOK_MATERIAL_PROVIDER_V1", hook)
        self.assertIn("Decision Handoff", contract)
        self.assertIn("material candidates only", contract)
        for material in (
            "fact-grounded detail",
            "opinion seed",
            "story seed",
            "callback seed",
            "adjacent-topic seed",
            "activity/topic seed",
        ):
            self.assertIn(material, contract)
        for denied_capability in (
            "no action permission",
            "no replacement action",
            "no supporting function generation",
            "no invite generation",
            "no close/wait selection",
        ):
            self.assertIn(denied_capability, contract)
        self.assertIn("Natural Reply 消费 handoff", contract)

    def test_activity_material_never_becomes_invite_permission(self) -> None:
        hook = read(HOOK_PATH)
        sources = section(hook, "## 素材来源", "## Hook 类型")
        hook_types = section(hook, "## Hook 类型", "## 开题结构")

        self.assertIn("共同活动素材", sources)
        self.assertIn("activity idea != INVITE permission", sources)
        self.assertIn("活动／共同兴趣种子", hook_types)
        self.assertIn("只是 material", hook_types)
        self.assertNotIn("低压力邀约", sources)
        self.assertNotIn("自然邀请", hook_types)

    def test_hook_conversation_evidence_cannot_select_close_or_wait(self) -> None:
        hook = read(HOOK_PATH)
        interview = section(hook, "## 打断 interview mode", "## 小故事三拍")
        rhythm = section(hook, "## 话题节奏作为 evidence", "## Continuation ownership")

        self.assertIn("no material candidate", interview)
        self.assertIn("exhausted-thread／ownership evidence", interview)
        self.assertNotIn("线程已经耗尽时收线", interview)
        self.assertIn("low-extension／ownership evidence", rhythm)
        self.assertIn("ownership／no-new-material constraint", rhythm)
        self.assertIn("不自动补 `SHARE`", rhythm)
        self.assertIn("不选择 `WAIT`", rhythm)
        self.assertIn("不选择 `LEAVE_SPACE / CLOSE / WAIT`", rhythm)
        self.assertNotIn("仍无延展就收线", rhythm)
        self.assertNotIn("等待自然事件或其主动", rhythm)

    def test_wait_is_not_disguised_as_a_sendable_message(self) -> None:
        decision = read(DECISION_PATH)
        natural = read(NATURAL_PATH)

        for phrase in ("那我先等等", "先不打扰你啦"):
            self.assertIn(phrase, decision)
            self.assertIn(phrase, natural)
        self.assertIn("因此不是 `WAIT`", decision)
        self.assertIn("不得用引号伪装成目标回复", natural)

    def test_same_action_repair_never_changes_play(self) -> None:
        decision = read(DECISION_PATH)
        humor = read("references/personal/幽默与调侃生成器.md")
        rejection = section(
            decision,
            "## Realization rejection 与 redecision",
            "## 决策协议",
        )

        self.assertRegex(
            rejection,
            r"Type A[\s\S]+same-action repair[\s\S]+technique-free `PLAY`",
        )
        self.assertRegex(
            humor,
            r"Type A[\s\S]+同一 `PLAY`[\s\S]+technique-free `PLAY`",
        )
        self.assertIn("不改选其他动作", humor)

    def test_decision_conflict_returns_without_downstream_replacement(self) -> None:
        decision = read(DECISION_PATH)
        natural = read(NATURAL_PATH)
        humor = read("references/personal/幽默与调侃生成器.md")

        self.assertRegex(
            decision,
            r"Type B[\s\S]+serious[\s\S]+不得选择 replacement action",
        )
        self.assertIn("不得附带 replacement action", natural)
        self.assertRegex(humor, r"`PLAY` 本身与 serious[\s\S]+Type B")

    def test_redecision_has_a_state_sensitive_termination_invariant(self) -> None:
        decision = read(DECISION_PATH)
        rejection = section(
            decision,
            "## Realization rejection 与 redecision",
            "## 决策协议",
        )

        self.assertIn("临时 decision constraint", rejection)
        self.assertIn("同一 action + 同一 rejection reason", rejection)
        self.assertIn("缩小仍可合法选择的动作集合", rejection)
        self.assertIn("`WAIT / LEAVE_SPACE / CLOSE`", rejection)
        self.assertIn("已无安全可逆动作", rejection)
        self.assertIn("Guided Interview", rejection)

    def test_all_entry_paths_use_the_same_flow(self) -> None:
        decision = read(DECISION_PATH)
        entries = section(
            decision,
            "## Unified entry paths",
            "## Realization rejection 与 redecision",
        )
        rows = two_column_rows(entries)

        self.assertEqual(
            set(rows),
            {"reply-first", "analysis → reply", "draft-first", "training／simulation"},
        )
        self.assertIn("Decision Handoff", entries)
        self.assertIn("Natural Reply", entries)
        self.assertIn("合法就尽量保留", rows["draft-first"])
        self.assertIn("标签不自动成为动作 permission", rows["training／simulation"])

    def test_natural_reply_owns_validation_but_not_redecision(self) -> None:
        natural = read(NATURAL_PATH)
        validation = section(
            natural,
            "## Realization Validation",
            "## Validation outcome 与 failure handling",
        )
        outcomes = section(
            natural,
            "## Validation outcome 与 failure handling",
            "## 表达重复检测",
        )

        for check in (
            "action fidelity",
            "supporting function permission",
            "stop semantics",
            "fact safety",
            "style compatibility",
            "ownership compatibility",
            "serious compatibility",
            "boundary / safety",
        ):
            self.assertIn(check, validation)
        self.assertIn("PASS", outcomes)
        self.assertIn("REPAIRABLE", outcomes)
        self.assertIn("REJECT", outcomes)
        self.assertIn("不参与替代动作选择", outcomes)

    def test_existing_32_case_model_routes_keep_reply_flow_references(self) -> None:
        cases = json.loads(read("model_evals/cases.yaml"))["cases"]
        self.assertEqual(len(cases), 32)
        self.assertEqual(set(model_runner.CASE_RUNTIME_REFERENCES), {case["id"] for case in cases})

        for case in cases:
            if case["mode"] not in {"reply", "realtime"}:
                continue
            refs = model_runner.CASE_RUNTIME_REFERENCES[case["id"]]
            self.assertIn(DECISION_PATH, refs, case["id"])
            self.assertIn(NATURAL_PATH, refs, case["id"])


if __name__ == "__main__":
    unittest.main()
