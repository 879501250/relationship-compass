"""Structural regressions for v1.7-B1 action-selector ownership."""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_model_evals as model_runner  # noqa: E402


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class DecisionLayerOwnershipTests(unittest.TestCase):
    def test_decision_layer_keeps_the_only_exact_action_taxonomy(self) -> None:
        decision = read("references/personal/回复决策与对话流.md")
        self.assertIn("唯一决策层", decision)
        actions = set(re.findall(r"^\| `([A-Z_]+)` \|", decision, flags=re.MULTILINE))
        self.assertEqual(
            actions,
            {
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
            },
        )
        self.assertIn("Module Ownership Matrix", decision)

    def test_realization_modules_cannot_replace_the_selected_action(self) -> None:
        natural = read("references/personal/自然回复生成器.md")
        humor = read("references/personal/幽默与调侃生成器.md")
        hook = read("references/personal/主动话题与conversation-hook.md")
        expression = read("references/personal/网络聊天表达升级器.md")

        self.assertIn("不得静默改成另一个 Primary Action", natural)
        self.assertIn("Primary Action = PLAY", humor)
        self.assertIn("不得重新判断这一轮该不该 `PLAY`", humor)
        self.assertIn("拒绝 realization 并返回 Decision Layer", humor)
        self.assertIn("本文件不代选动作", hook)
        self.assertIn("成长目标不能反向授权 action", expression)
        self.assertIn("growth observation，不参与 runtime action selection", expression)

    def test_investment_and_practical_references_are_not_selectors(self) -> None:
        investment = read("references/personal/投入预算与停止条件.md")
        practical = read("references/practical/实战话术编排器：从一句回复到后续分支.md")
        practical_index = read("references/practical/00-导读与使用分级.md")

        self.assertIn("relationship strategy / constraint provider", investment)
        self.assertIn("不得直接把当前一轮决定为", investment)
        self.assertNotIn("从下面七种策略中只选一个主策略", practical)
        self.assertIn("本文件不再从七种主策略中选择", practical)
        self.assertIn("不是机械一一映射", practical)
        self.assertIn("不得建立新的主策略 taxonomy", practical_index)

    def test_skill_loads_action_selector_before_realization_references(self) -> None:
        skill = read("SKILL.md")
        self.assertIn("只由 `回复决策与对话流.md` 选择 Primary Action", skill)
        self.assertIn("动作确定后读 `references/personal/自然回复生成器.md`", skill)
        self.assertIn("`PLAY` 读 `references/personal/幽默与调侃生成器.md`", skill)
        self.assertIn("CORE_POLICY → canonical personal → Decision Layer → practical realization", skill)

    def test_all_model_routes_preserve_need_specific_ownership(self) -> None:
        routes = model_runner.CASE_RUNTIME_REFERENCES
        self.assertEqual(len(routes), 32)
        humor_ref = "references/personal/幽默与调侃生成器.md"
        hook_ref = "references/personal/主动话题与conversation-hook.md"
        practical_prefix = "references/practical/"
        self.assertEqual(
            {case_id for case_id, refs in routes.items() if humor_ref in refs},
            {"model-partner-opens-thread"},
        )
        self.assertEqual(
            {case_id for case_id, refs in routes.items() if hook_ref in refs},
            {"stress-topic-material-does-not-override-ownership"},
        )
        self.assertFalse(
            [ref for refs in routes.values() for ref in refs if ref.startswith(practical_prefix)]
        )


if __name__ == "__main__":
    unittest.main()
