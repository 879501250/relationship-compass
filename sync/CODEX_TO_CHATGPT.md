# Codex → ChatGPT

## 目的

从 Local Codex 提取一个可读、可撤销、按对象隔离的精简 checkpoint，供 ChatGPT Project 日常对话使用。

## 流程

1. 选择一个明确对象，例如 `obj-a`。
2. 运行 `python scripts/memory_store.py context --subject-id obj-a --max-chars 4000`，不得省略 subject ID。
3. 不直接上传 JSON 输出。按 `CHECKPOINT_TEMPLATE.md` 人工筛选：保留 user 共享状态、该对象的边界、landmark、少量近期关键事件和最近技巧摘要。
4. 删除不必要的 source_ref、内部 ID、过期 hypothesis 和任何隐私细节。
5. 标记导出时间、对象 ID、用户确认状态和来源版本。
6. 用户审核后，再把 checkpoint 上传或粘贴到对应 ChatGPT Project。
7. 后续新 checkpoint 写明 supersedes，避免新旧版本同时被当成有效事实。

## 隔离检查

导出前搜索其他对象 ID、代号和对象专属内容。发现任何对象 B 数据时停止上传并回到 Local Codex 检查；用户通用 capability profile 可以保留，但对象反馈和 recent_techniques 不能共享。

<!-- Modified by AI on 2026-08-21 13:48:02 -->
