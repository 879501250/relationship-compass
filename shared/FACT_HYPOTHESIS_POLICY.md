# Fact / Hypothesis Policy

所有 Memory、checkpoint 和 Review Mode 输出都使用四类结构：

## confirmed

可直接追溯的明确事实：可见原文、用户明确陈述、已发生事件、明确边界、兑现或取消。记录来源；事件使用实际 `occurred_at`。

## hypothesis

模型基于证据作出的可撤销判断，必须带证据、置信度和生命周期。`stage_estimate`、`trend_estimate`、`humor_receptivity` / `humor_acceptance`、`style_update` 永远属于 hypothesis，不得进入 confirmed。

## recommendation

下一步建议、回复方案、投入或停止动作。建议不是事实，也不是对方意图证明。

## unknown

证据不足且会影响判断的未知项。gray 通常应保留为 unknown，而不是强行改写成负面 hypothesis。

## 转换规则

- hypothesis 到期只转为 stale，不自动变成事实或被删除。
- 新证据与旧 hypothesis 冲突时，新建判断并把旧记录标为 superseded。
- 用户确认可以更新稳定 user 画像，但不能把“模型预测得到确认”误写成先前已经成立的事实。
- checkpoint 和 review 必须保持四类分栏；空栏写“无”或“证据不足”，不能互相填充。
