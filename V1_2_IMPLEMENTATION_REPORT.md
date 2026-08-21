# 关系罗盘 V1.2 Knowledge Evolution 实现报告

## 1. 结论

V1.2 已建立一条受治理的增量知识链路：私有原始来源只做本机登记与 SHA-256 指纹；候选知识经过 source card、结构化 proposal、用户逐条审核和显式 merge 后，才进入 Local Skill curated runtime 与 ChatGPT 精简知识包。原有关系判断、聊天表达、Memory 对象隔离和安全停止条件没有被改写。

## 2. Phase 0 稳定化

- context 改为 P0 用户/对象/关系核心、P1 active hypothesis（stage/trend 优先）、P2 landmark、P3 recent normal、P4 temporary 的决定性分配。
- 预算按最终渲染 JSON 计算，不再 append 后从尾部盲删；对象隔离和 `max_chars` 同时受测。
- `command_apply` 在事务写入和 prune 前刷新 hypothesis 生命周期，过期记录先转 stale。
- CI 使用 Ubuntu/Windows、Python 3.11 矩阵，并单独校验 model eval 定义。
- V1.1.1 行为基线记录为 `NOT RUN`；没有模型原始输出时不伪造通过。

## 3. Knowledge Architecture

新增 `knowledge-management/` 治理层和 `references/curated/` runtime 层。治理层保存 registry、schema、source card、proposal、review decision、merge report 与机器化 curated store；runtime 只保存 INDEX 和 7 个主题文件。原始书籍、PDF、EPUB、私人笔记和本机路径不进入 Git 或 runtime。

状态流固定为：`raw -> registered -> source card -> proposal -> human review -> approved / partially_approved / rejected -> merge -> curated`。不存在 source 直接写 curated 的路径。

## 4. Source Registry 与指纹

`SOURCE_REGISTRY.json` 使用 schema version 1，覆盖来源身份、出版信息、主题、source quality、freshness、状态、审核时间和 SHA-256 `content_fingerprint`。公开 registry 不允许 `local_path` 或 claim evidence；本机来源路径只进入被 Git 忽略的 `SOURCE_REGISTRY.local.json`。

重复 source_id 或重复内容指纹会被拒绝。source quality 只评价来源整体，不自动升级单条 claim evidence。

## 5. Claim Schema 与证据

候选 claim 记录 canonical claim、来源和 anchor、claim type、A/B/C/D/unknown evidence、confidence、适用/不适用条件、误用风险、与现有 claim 的关系和目标 topic。`claim_id` 由规范化 canonical claim 生成，空格与末尾标点差异不会制造重复记录。

作者经验、临床经验、理论、经验研究、实践启发和伦理规范保持分型；“来源引用论文”不会自动成为 A 级证据。

## 6. Intake CLI

`scripts/knowledge_intake.py` 支持 `register`、`proposal`、`validate`、`status`、`list` 和需要 `--confirm` 的 `deprecate`。register 只读取文件以计算指纹，不复制原始内容，并生成可补写的 source card。proposal 从 claim JSON 生成逐条 approve/reject/revise 审核文档。

README 已给出“如何添加一本新书”、版本变更、deprecate、纠错和冲突流程。新版来源使用新 source_id；已审核记录默认保留审计链，不做无痕删除。

## 7. Human Review 与 Merge Gate

`scripts/knowledge_merge.py review` 要求显式 `--confirm`，且每条 claim 必须只勾选一个决定。审核记录绑定 proposal 文件名与 SHA-256 指纹；审核后的任何原地改写都会令 merge 失败并要求重新审核。

merge 再次要求 `--confirm`，只处理 decision 为 approve 的 claim。reject、revise、缺失 decision 和缺失 review file 都不能进入 curated。每次 merge 生成审计报告并重建 INDEX/topic 文件。

## 8. 去重、Provenance 与冲突

相同 canonical claim 合并为单一 claim block，多来源以紧凑 provenance 列表保存。不同来源不会因为重复出现而覆盖既有证据表述。

冲突记录包含 existing claim、新 claim、双方 evidence、解释与 resolution。支持 `keep_existing`、`merge_with_conditions`、`keep_both`、`replace_existing`、`reject_new`；未解决冲突不得 merge，`replace_existing` 还必须逐个提供 `--confirm-replace <claim-id>`。

## 9. Freshness 生命周期

来源支持 stable、semi_dynamic、dynamic，并根据 `last_reviewed_at`、可选 `review_after` 计算 current 或 review_due。默认复核周期分别为 365、180、90 天。到期只标记需要复核，不自动失效、删除或联网更新。curated claim 同样保存 `last_reviewed_at` 与 `review_after`。

## 10. ChatGPT Knowledge Pack

`scripts/build_chatgpt_pack.py` 从固定 allowlist 构建 6 个主题文件：共享政策、即时聊天、关系信号、成长复盘、安全证据、approved curated claims。`KNOWLEDGE_PACK_INFO.json` 保存 pack version、built_at、Skill/curated/registry revision、included claim IDs 和 included sources。

相同输入生成相同知识正文；只有 metadata 的 built_at 与生成文件维护标记可变化。builder 不读取 SQLite、Memory 实现、raw、local registry、proposal、review decision 或 rejected 内容，也拒绝本机绝对路径。仓库内相对链接在上传包中转为纯文本，避免失效跳转。

## 11. 安全、隐私与版权

- 共享 policy 自动进入 ChatGPT pack，Local 与 ChatGPT 继续遵守 fact/hypothesis 分离、对象隔离、四色反馈、continuation ownership、actual send learning、user growth 与 stop conditions。
- source card 与 claim 默认释义，只保留短 anchor；不保存章节、整书或大段连续原文。
- PUA、操控、绕过同意、群体刻板印象、伪心理学、营销承诺、作者故事因果化和模型总结偏差都属于审核项。
- ChatGPT Project 不接收 SQLite、完整聊天导出、跨对象混合 checkpoint 或知识治理工作区。

## 12. 自动验证与兼容性

最终本机验证结果：

- unit tests：34，通过；
- integration tests：10，通过；所有 subprocess timeout 为 10 秒；
- contract eval：9 suites、40 cases，通过，只验证结构与契约；
- model eval definitions：9 cases、27 criteria，可校验；真实行为状态仍为 `NOT RUN`；
- runtime-only validator：通过；
- full validator：通过；
- 完整 unit/integration/contract 执行：6.99 秒，低于 30 秒目标。

系统 `skill-creator` 附带的 `quick_validate.py` 未能启动，因为当前 Python 环境缺少 PyYAML（`ModuleNotFoundError: yaml`）。没有为制造通过结果而临时安装依赖；项目自带的 frontmatter、路由、marker、runtime/full inventory 与交叉一致性校验均已实际通过。

内部 slug 仍是 `goutoujunshi-personal`，安装目录仍是 `relationship-compass`，展示名仍是“关系罗盘”。Memory schema 没有破坏性迁移；两个 upstream 源仓库没有被修改。旧 `chatgpt-project/knowledge/` 保留兼容参考，新部署以 `generated-knowledge/` 为准。

## 13. 提交、未解决风险与后续边界

V1.2 按四个里程碑拆分提交：Phase 0、Knowledge Schema、Intake/Merge、ChatGPT Pack，没有把实现压成单一 commit。

仍需真实数据才能优化的部分：不同书籍的 source card 质量、claim 粒度、跨来源冲突频率、ChatGPT 文件大小上限、真实用户对 curated 建议的可用性，以及真实模型行为回归。当前 registry 与 curated store 为空，因此 pack 的 included claim/source 列表也为空，这是未导入真实来源时的正确状态。

本阶段没有增加 Trend Layer、自动联网、周期抓取、新 Skill 或核心关系决策逻辑。下一步应先用少量真实来源走完整人工审核链并保存真实 model eval 输出；在有证据前不启动 Trend 扩张。

<!-- Modified by AI on 2026-08-21 17:24:27 -->
