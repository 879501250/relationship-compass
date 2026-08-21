# 隐私、对象隔离与 Checkpoint

ChatGPT Project 不复制或接收 Codex SQLite、memory_store 脚本、完整 upstream references、整段聊天导出或批量截图归档。

跨系统只使用用户审核过的精简 checkpoint：

- 一个 checkpoint 只对应一个对象；
- 用户通用能力与该对象关系信息分栏；
- 只保留当前风格、目标、能力状态、明确边界、landmark、少量近期关键事件和最近技巧摘要；
- 每项标注来源、发生时间和确认状态；
- 固定分为 confirmed、hypothesis、recommendation、unknown；
- stage_estimate、trend_estimate、humor_receptivity/acceptance 只放 hypothesis，不混入 confirmed；
- 对象 A 的反馈、幽默/暧昧接受度和技巧历史不得出现在对象 B checkpoint。

Project 内聊天和文件可能成为项目上下文，因此不要上传不需要长期存在的完整隐私材料。需要更正时，更新或移除相关来源，并在新 checkpoint 中明确 supersedes 关系。

<!-- Modified by AI on 2026-08-21 14:47:55 -->
