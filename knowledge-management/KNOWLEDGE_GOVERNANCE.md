# Knowledge Governance

## 边界

Knowledge Evolution 只维护“可追溯、可审核、可更新”的知识。原始书籍、PDF、EPUB、课程文件和私人笔记不进入 Git，也不进入 Skill runtime。公开仓库只保存元数据、SHA-256 指纹、释义后的 claim、短锚点、审核决定和合并报告。

本机原始路径只可写入被 Git 忽略的 `SOURCE_REGISTRY.local.json`，不得进入 `SOURCE_REGISTRY.json`、source card、proposal、日志、测试夹具或 ChatGPT knowledge pack。

## 状态流

`raw -> registered -> source card -> proposal -> human review -> approved / partially_approved / rejected -> merge -> curated`

任何来源都不得直接写入 `references/curated/`。AI 生成 source card 只是在组织候选内容，不构成证据，也不代表用户批准。

## 三种不同判断

- `source_quality`：来源整体的出版、研究与可复核质量。
- `claim_evidence`：单条主张实际得到的证据强度，采用 A/B/C/D/unknown。
- `confidence`：当前抽取和表述是否准确的置信度。

三者不得互相替代。书籍或名人作者不自动获得高 claim evidence；带参考文献的营销性叙述也不得自动升级。

## Claim 与冲突治理

`claim_id` 从规范化后的 `canonical_claim` 生成并保持稳定。同一主张从多个来源出现时合并 provenance，不复制 runtime 块。新主张与既有主张冲突时，必须记录双方证据、适用条件、冲突解释与人工 resolution；默认不得覆盖。

允许的冲突结果：`keep_existing`、`merge_with_conditions`、`keep_both`、`replace_existing`、`reject_new`。`replace_existing` 必须再次明确确认。

## 新鲜度

- `stable`：基础关系机制、伦理边界等变化缓慢内容，默认 365 天复核。
- `semi_dynamic`：平台习惯、实践指南等，默认 180 天复核。
- `dynamic`：法律、平台政策、公共卫生或快速变化信息，默认 90 天复核。

`last_reviewed_at` 与可选 `review_after` 都使用带时区 ISO 8601。到期只变为 `review_due`，不自动失效、不自动联网更新，也不自动删除。

## 安全审查

每条候选内容都检查：操控与 PUA、群体刻板印象、绕过同意、伪心理诊断、作者个人经验外推、营销承诺、故事当因果、模型对来源的二次误读。发现风险时应降低证据等级、增加限制、改写为防御性知识或拒绝整合。

关系安全与停止条件高于聊天效果。不得把“吸引”“张力”“推进”解释为绕过拒绝、制造焦虑、贬低、孤立、性施压或欺骗。

## 版权与数据最小化

- 默认释义，不收录章节、整本书或大段连续原文。
- 引用只保留复核所需的短片段和 anchor。
- 不把用户私人材料、聊天记录或 Memory 数据混入知识库。
- rejected/proposals/raw/local registry 不得进入 runtime 或 ChatGPT pack。

<!-- Modified by AI on 2026-08-21 16:38:32 -->
