# 关系罗盘 ChatGPT Project 部署说明

该目录是 Relationship Compass（关系罗盘）的轻量 ChatGPT Project 版本，不是 Local Codex Skill 的完整复制。

## 建立项目

1. 在 ChatGPT 侧栏选择 New project。
2. 对敏感、长期的个人关系资料，优先选择 project-only memory，使项目上下文与项目外聊天隔离。
3. 在 Project settings 中，把 `PROJECT_INSTRUCTIONS.md` 内容粘贴到 Project instructions。
4. 在项目根目录运行 `python scripts/build_chatgpt_pack.py`。
5. 上传 `generated-knowledge/` 下 6 份 Markdown；共享政策已经自动包含在 `01-CORE_POLICY.md`，无需重复上传。
6. 保留 `KNOWLEDGE_PACK_INFO.json` 用于版本核对；若 ChatGPT Project 接受 JSON，也可一并上传。
7. 如需迁移当前状态，按 `../sync/CHECKPOINT_TEMPLATE.md` 为每个对象单独生成并审核 checkpoint，再上传或粘贴。

ChatGPT Project 的界面、memory 选项和文件限制可能随账户或工作区设置变化；部署时以当前 Project settings 为准。

## 不要上传

- `memory.sqlite3`、WAL/SHM 或任何 SQLite 副本；
- `scripts/memory_store.py`；
- `references/knowledge` 与 `references/practical` 全库；
- `knowledge-management/` 下的 raw、local registry、source card、proposal、review decision、merge report 或 rejected 内容；
- 微信完整导出、长期截图归档、账号、地址或私密影像；
- 同时混合多个对象且没有清楚分栏的 checkpoint。

## 日常使用

- 即时回复：直接说“她刚回……现在怎么回”。
- 普通分析：提供当前对象、最近上下文和目标。
- 复盘：明确说“进入 Review Mode，复盘最近两周/这个关键节点”。
- 实际发送学习：同时提供 AI 建议、实际发送版本和你的主观感受；对方反馈作为单独信息。

ChatGPT Project 无法直接写入 Local Codex Memory。需要同步时生成“待确认 checkpoint”，由用户审核后再交给另一端。

当 Local Codex 的 personal 规则、shared policy 或 curated claims 变化时，重新运行 builder，核对 `KNOWLEDGE_PACK_INFO.json` 后替换 Project 文件。不要手改 generated knowledge；修改其上游文件并重建。
