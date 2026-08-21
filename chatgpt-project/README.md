# 关系罗盘 ChatGPT Project 部署说明

该目录是 `goutoujunshi-personal` 的轻量 ChatGPT Project 版本，不是 Local Codex Skill 的完整复制。

## 建立项目

1. 在 ChatGPT 侧栏选择 New project。
2. 对敏感、长期的个人关系资料，优先选择 project-only memory，使项目上下文与项目外聊天隔离。
3. 在 Project settings 中，把 `PROJECT_INSTRUCTIONS.md` 内容粘贴到 Project instructions。
4. 上传 `knowledge/` 下 4 份精简文件，并额外上传 `../shared/CORE_POLICY.md` 与 `../shared/FACT_HYPOTHESIS_POLICY.md`。
5. 如需迁移当前状态，按 `../sync/CHECKPOINT_TEMPLATE.md` 为每个对象单独生成并审核 checkpoint，再上传或粘贴。

ChatGPT Project 的界面、memory 选项和文件限制可能随账户或工作区设置变化；部署时以当前 Project settings 为准。

## 不要上传

- `memory.sqlite3`、WAL/SHM 或任何 SQLite 副本；
- `scripts/memory_store.py`；
- `references/knowledge` 与 `references/practical` 全库；
- 微信完整导出、长期截图归档、账号、地址或私密影像；
- 同时混合多个对象且没有清楚分栏的 checkpoint。

## 日常使用

- 即时回复：直接说“她刚回……现在怎么回”。
- 普通分析：提供当前对象、最近上下文和目标。
- 复盘：明确说“进入 Review Mode，复盘最近两周/这个关键节点”。
- 实际发送学习：同时提供 AI 建议、实际发送版本和你的主观感受；对方反馈作为单独信息。

ChatGPT Project 无法直接写入 Local Codex Memory。需要同步时生成“待确认 checkpoint”，由用户审核后再交给另一端。

<!-- Modified by AI on 2026-08-21 14:47:55 -->
