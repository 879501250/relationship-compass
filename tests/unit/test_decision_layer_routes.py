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


def section(content: str, heading: str, next_heading: str) -> str:
    return content.split(heading, 1)[1].split(next_heading, 1)[0]


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

    def test_humor_repetition_stays_inside_play_or_returns_to_decision(self) -> None:
        humor = read("references/personal/幽默与调侃生成器.md")
        repetition = section(humor, "## 重复检测", "## Continuation cases")

        self.assertRegex(repetition, r"same `PLAY` realization[\s\S]+return Decision Layer")
        for leaked_fallback in (
            "callback → 换真实新分享或观点",
            "playful framing → 回到现实内容",
            "轻度调侃 → 换具体欣赏或普通交流",
        ):
            self.assertNotIn(leaked_fallback, repetition)

    def test_natural_flow_internal_sections_only_provide_constraints_or_realization(self) -> None:
        natural_flow = read("references/practical/自然流、内在状态与结构化互动：伦理能力转译.md")
        diagnosis = section(natural_flow, "## 一、先诊断一个主问题", "## 二、内在状态")
        pattern = section(natural_flow, "### 观察—表达—可选接点", "### 线程选择")
        feedback = section(natural_flow, "## 七、反馈形成约束", "## 八、输出模板")

        self.assertIn("relationship-level hypothesis", diagnosis)
        self.assertIn("不在本表选 turn action", diagnosis)
        self.assertIn("gated by Primary Action", pattern)
        self.assertIn("不会自动授权 `ASK` 或 `INVITE`", pattern)
        self.assertIn("提供给 Decision Layer 的约束", feedback)
        self.assertIn("当前 turn action 仍由 Decision Layer 决定", feedback)
        self.assertNotIn("降低张力、结束本轮", feedback)

    def test_active_expression_tiers_calibrate_realization_not_permission(self) -> None:
        active = read("references/practical/主动表达、第一次见面与自然接触.md")
        tiers = section(active, "## 一、已获许可后的主动表达档位", "## 二、让喜欢变得明显")
        physical = section(active, "## 四、自然、低强度、可退出的肢体接触", "## 五、含糊回应后的再次主动")

        self.assertIn("只做 realization calibration", tiers)
        self.assertIn("不得决定是否联系、邀约、表白、推进或发生身体接触", tiers)
        self.assertNotIn("| 档位 | 适合动作 |", tiers)
        self.assertIn("不由主动档位、绿灯或约会顺利自动授权", physical)
        self.assertIn("不自动授权升级", physical)
        self.assertNotIn("| 反馈 | 可观察表现 | 下一步 |", physical)

    def test_additional_practical_feedback_and_composition_do_not_select_actions(self) -> None:
        scene = read("references/practical/场景感、松弛感与社交校准：从接话到关系推进.md")
        public_examples = read("references/practical/公开表达案例的伦理转译.md")
        awkward = read("references/practical/化解尴尬：轻松救场的实用指南.md")

        self.assertIn("反馈形成 constraint，不选择 turn action", scene)
        self.assertIn("不在本表选择当前 turn action", scene)
        self.assertIn("不是顺序升级漏斗", public_examples)
        self.assertIn("只有 `Primary Action = INVITE`", public_examples)
        self.assertIn("尴尬类型只能形成 training diagnosis／realization constraint", awkward)


if __name__ == "__main__":
    unittest.main()
