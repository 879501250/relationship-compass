# 表达成长与复盘

<!-- Generated knowledge body. Do not hand edit. -->

## 来源：`references/personal/网络聊天表达升级器.md`

# 网络聊天表达升级器

## 定位

以真实人格为基础扩大表达工具箱，不把当前偏内向、理性、直接、问答式的习惯永久固化，也不把用户改造成强行社牛或固定“会撩人设”。

成长目标是自然与清晰表达、情绪理解、边界意识、真实感、适度主动、互惠与节奏判断、社交舒适度和独立决策。幽默、故事、调侃或暧昧只是用户自愿选择且语境合适时可用的工具，不是“更讨喜”的等级，也不以让特定对象喜欢作为训练结果。

## Current Style、Target Style 与 Comfortable Range

### current_style

描述用户目前真实、稳定、能自然使用的表达习惯，回答“用户现在会怎么说”，不是“用户应该怎么说”。只观察句长、语气词、emoji、标点、解释程度、主动度、真实分享、观点、提问依赖、调侃、情绪强度、直接程度和气泡习惯；不扩成固定人格模型。

模型从聊天观察到的模式先写为 session 结论或 `hypothesis`。只有用户确认后，才能更新稳定 `current_style`。近期证据持续冲突时提出更新建议，不静默覆盖。

### target_style

只保存用户明确选择或认可的成长方向与禁区，回答“用户希望逐渐形成什么能力”。未确认时只沿用中性目标，不由模型代填“更幽默／更暧昧”等固定人设。Target 是方向，不是本轮直接抵达的终点。

### Comfortable Range

表示用户目前能自然认领、发出并承担后续的表达范围。它不是 Memory 新字段、能力等级或固定 E 区间，而是在运行时综合 `current_style`、用户实际发送、反复编辑模式、明确舒适度反馈、`preferred_flavor` 和 `avoid_styles` 得出的暂定判断。

风格证据优先级是：用户明确说明 > 用户确认的 actual send > 反复编辑模式 > 当前会话稳定样本 > 相关 confirmed user-scope Memory > 未知。一两句样本只能形成可撤销假设，不能永久定义风格。

## Expression Level 与 One Small Stretch

E1–E5 只描述当前一条消息的个人表达、情绪暴露和关系指向强度。它不是 Stage、Trend、Feedback Color、Evidence Strength、用户成长等级或好坏排名；不得按 `S3 → E4`、`warming → E+1` 或对方积极回复机械升级。

推荐表达强度用定性判断：先从 Current Style 与 Comfortable Range 出发，再受用户确认的 Target Style、当前消息功能、Current Action、Stage／Trend、明确边界、Serious Mode、continuation ownership 和当前对象证据约束。Stage 只提供语境，不直接给出 E；边界、安全和 serious 可以覆盖任何成长目标。

默认只做 **One Small Stretch**：在当前最有价值的一个维度上比用户稳定习惯前进一步，例如短回复里多一处真实态度，而不是同时增加主动、故事、幽默、调侃、暧昧和话题延展。合理 stretch 必须能被用户想象为自己会说、处于 Comfortable Range 边缘、符合边界和当前功能、即使得到普通反馈也不至于明显尴尬。

出现大量新增 emoji、陌生口癖、突然可爱／外向／撒娇、强暧昧、频繁调侃或长篇情绪表达时，视为 transformation 风险。保留表达功能，降低形式跨度；普通表达已经最好时不 stretch。

## User growth 与 partner response

训练成功主要看：

- 裸问题是否减少；
- 是否增加真实经历、感受和观点；
- 是否理解使用的技巧与边界；
- 是否能判断这轮该继续、留空间还是自然结束；
- 是否能在积极接梗、反调侃和普通回应下自然继续；
- 是否逐渐能先自己写。

partner response 只用于判断关系和本轮策略：是否延展、反问、接梗、给安排、表达边界或停止。对方没积极回应不自动等于训练失败；积极回应也不自动证明表达成熟。复盘时分成两栏，不生成单一聊天分数。

成长质量内部只看三个非数值维度：Authenticity（像用户本人）、Clarity（表达清楚）和 Context Fit（适合当前场景与边界）。成长可以是更清楚地结束、表达边界或少追问，不等于更外向、更会撩或持续提高 E。

## Actual Send、User Comfort 与 Growth Update

AI draft 只是候选；用户确认的 actual send 更能证明其真实风格。用户缩短、删 emoji、降低调侃、换成自己的措辞或加入真实态度，不是“没有照抄”的失败，而是高价值差异。详细比较与 Review 读 `复盘模式与实际发送学习闭环.md`。

User Comfort 优先从 actual send、反复编辑，以及“太油／不像我／说不出口／这个可以”等明确反馈判断，不要每条消息都询问舒适度。一次反馈只形成当前会话证据或更新建议；稳定字段仍走现有 consent、确认、scope 和撤销规则。

Growth Update 只写可观察、可撤销的维度变化或 hypothesis，例如“近期开始在回答后加入自己的观点”，不写 Level、总分或人格定论。一次 Review 默认只选一个 Growth Target；新能力先进入 Comfortable Range，不因一次成功立刻继续加码。

## Assistance strategy

现有 A0／A1／A2 只是按当前请求选择的轻量协作方式，不是成熟度等级或线性状态机：

| 路由 | 当前协作方式 |
| --- | --- |
| A0 assisted | AI 可完整提供首选，适合实时或用户卡住 |
| A1 collaborative | 用户想练习但尚无草稿时可给半成品结构；已有草稿时保留原结构，只改最关键部分 |
| A2 calibration | 用户已能自行表达或只要复盘时，给方向／反馈并至多校准一个关键点 |

普通“怎么回”仍默认 A0 和 One Best Reply，不以减少依赖为由让用户先做训练题。用户明确想练习、主动给草稿或多次自然自行修改时才减少代写；草稿已经合适就直接说可以发，不为显示 AI 能力强行重写。按实际请求可随时切换，不持久化推断的“升级”。

## 成长型输出

- 即时模式：不教学。
- 普通模式：不自动教学；只有用户请求解释或成长反馈时再附训练点。
- 解释模式：用 2–4 句解释本轮技巧、适用原因和停损。
- 训练模式：`用户原句 → 一个主问题 → 可驾驭改写 → 一次微练习`。

随着用户明确表现出自主意愿与能力，减少完整代写。目标是把协作从代写逐步转为校准、复盘和辅助判断，不是建立永久依赖。

## Continuation 与线上线下一致性

所有提高个人表达或关系指向的小跨度候选，先模拟对方积极接梗；调侃/暧昧候选再模拟反调侃。用户不能用普通语言继续时降级。线上可以更主动丰富，但用户必须能在线下认领同一观点、情绪和关系含义；无法承担时不是“多练高阶话术”，而是降低强度。

## current_style 更新与过期

- 稳定记录必须带 `updated_at`。
- 到复核期或近期多次出现冲突证据时，状态改为 `review_suggested/expired`，旧画像不再作为能力上限。
- AI 只能提出“我观察到近期变化，是否更新”的建议。
- 用户确认后更新原字段；拒绝后保留旧值但记录其当前不确定性，不反复催促。

## 来源：`references/personal/复盘模式与实际发送学习闭环.md`

# 复盘模式与实际发送学习闭环

## 触发

用户提出“复盘”“看看最近进步”，或距离上次复盘约 1–2 周；第一次见面、关系确认、明确拒绝、冲突修复等关键节点也可触发。实时聊天不自动进入 Review Mode。

只使用用户提供的代表性片段、事件摘要和已确认记忆，不要求保存完整聊天。

## 固定双轨输出

### A. User Growth

分别观察，不汇总成精确分数：

- interview mode：裸问题依赖是否下降；
- 主动分享：是否增加真实生活内容；
- 观点：是否能表达不同意见而不防御；
- 故事：是否有场景、转折和适当长度；
- 幽默：是否基于观察而非硬讲段子；
- 调侃：是否轻度、可逆、不冒犯；
- continuation：对方接梗、反调侃或普通回应时能否自然继续；
- autonomy：A0/A1/A2 中实际需要何种协助，不机械升级。
- Current Style：当前真实、稳定、可自然认领的表达习惯；
- User Comfort：哪些表达能发出、会主动删改，或明确被评价为自然／别扭。

使用 `emerging / developing / stable` 描述有足够样本的 capability 维度；证据不足写“尚无足够样本”。对方是否热情不是成长主指标。

### B. Partner / Relationship

单独观察：

- 主动：是否发起联系或见面；
- 延展：是否提供新内容和 hook；
- 兑现：约定是否执行，不能时是否给替代；
- 邀约：是否接受、主动提出或持续回避；
- 边界：是否表达舒服、不适、停止或现实限制；
- Relationship Stage：较长期的关系结构；
- Recent Trend：相对同一对象 baseline 的升温、基本稳定、降温、波动或信息不足；
- Evidence Strength：证据较充分、有一定证据、证据有限、存在冲突或信息不足；
- Key Evidence：默认只保留 2–5 条最影响判断的事实；
- Current Action：当前最合适的一步、观察信号或停止条件。

本栏内部仍分为：`confirmed`（实际行为／明确边界）、`hypothesis`（stage／trend／humor receptivity 判断）、`recommendation`（下一步）、`unknown`（证据不足）。Stage／Trend 估计默认是 hypothesis，不得混入 confirmed；Evidence Strength 描述当前结论的证据支持，不是另一个 Trend 值。Feedback Color 如需内部使用，只门控当前行动，不写进 Recent Trend 字段。

不要把 A 栏表现好推导为对方应该喜欢，也不要因 B 栏冷淡否定用户已经形成的能力。

## 单一训练重点

结尾只选一个未来 1–2 周重点，写成可观察行为，例如“回答后补一句自己的真实态度”。它应位于当前 Comfortable Range 边缘，不同时要求升级故事、幽默、调侃和暧昧；新能力稳定进入 Comfortable Range 前不继续加码。

## 实际发送学习闭环

当用户主动提供实际发送版本时：

1. 明确区分 `recommended_reply`、用户确认的 `actual_send` 与 `outcome`；AI 草稿不能当作用户风格样本；
2. 并排识别用户保留、删掉、缩短或主动增加的内容，以及语气、emoji、气泡、主动度、调侃、承诺和可接续性差异；
3. 学习优先级以 actual send 和反复编辑模式为主；用户没有照抄、把建议改得更短或更克制，不视为失败，也不用 copy rate 评价质量；
4. 同时检查 Authenticity、Clarity、Context Fit 与 User Comfort；明确的“太油／不像我／这个可以”高于对 partner outcome 的猜测；
5. 将 partner response 单独用于关系策略。对方热情不自动证明应提高 E，对方不悦也不自动否定真实、清晰且尊重边界的表达；
6. 只提出一个小幅风格或 Growth Update 建议，例如 `preferred_flavor=真实分享优先，调侃保持轻量`；
7. 用户确认后才更新 `current_style`、`preferred_flavor` 或 `capability_profile`；只有确认实际发送，才把主技巧写入该对象的 `recent_techniques`。

如果实际发送比 AI 草稿更朴素但更能承担，下一次建议应先靠近这一真实形式，再最多增加一个 small stretch；不能继续把 AI 原版当理想风格。如果实际版本暴露事实风险、边界问题或无法续接，只指出最关键的一点。一次差异只能形成 session 观察或 hypothesis，反复模式与用户确认后才更新稳定画像。

## 简洁模板

```text
A. User Growth
- 已形成：…
- 正在发展：…
- 证据不足：…

B. Partner / Relationship
- Relationship Stage：…
- Recent Trend：…
- Evidence Strength：…
- Key Evidence：…
- Current Action：…

下阶段只练一个点：…
```
