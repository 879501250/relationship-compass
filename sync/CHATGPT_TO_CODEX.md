# ChatGPT → Codex

## 目的

把 ChatGPT Project 中形成的少量、用户确认信息带回 Local Codex，不传输 Project 全部聊天，也不直接改 SQLite。

## 流程

1. 在 ChatGPT Project 中要求：“按 CHECKPOINT_TEMPLATE 为对象 X 生成待确认 checkpoint”。
2. ChatGPT 只总结用户通用状态、该对象边界、landmark、少量关键事件、实际发送偏好和未确认 hypotheses。
3. 用户逐项审核，删除不想长期保存的内容，确认对象 ID 和事件 `occurred_at`。
4. 把已审核 checkpoint 交给 Local Codex。
5. Codex 先用 `show --subject-id` 对比现有记录，列出新增、修改、删除建议；未经用户确认不写稳定字段。
6. 用户确认后，Codex 用 `memory_store.py apply` 逐项写入；landmark 指明 retention，实际发送技巧另用 `record-technique --confirm-sent`。
7. 保留每次写入的 `op_id`，当场提供 undo 方法。

## 禁止

- 不把 ChatGPT Project memory 当作已经核实的数据库；
- 不导入完整聊天、整批截图或多个对象混合摘要；
- 不把 ChatGPT 推断直接写入 user/object/relationship 稳定字段；
- 不因对方反馈好坏自动更新用户能力画像。
