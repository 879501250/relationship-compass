# 关系罗盘 V1.1.1

`goutoujunshi-personal` 是内部 Skill 名称与调用 slug；`relationship-compass` 是安装目录和 GitHub 仓库名；“关系罗盘”是面向用户的展示名称。

这是一个个人版 Local Codex Skill，并附带轻量 ChatGPT Project 配置。它用于微信聊天截图与节奏分析、自然回复、关系阶段和投入判断、表达成长复盘，以及在用户明确同意后维护按对象隔离、可撤销的本地记忆。它不导出聊天软件数据，不自动发送消息。

## 安装步骤

安装前在项目根目录运行：

```text
python scripts/validate_skill.py
python scripts/run_tests.py
```

将整个项目目录复制到以下位置。安装目录名使用 `relationship-compass`，内部 Skill slug 仍为 `goutoujunshi-personal`：

- Linux/macOS：`$HOME/.agents/skills/relationship-compass`
- Windows：`%USERPROFILE%\.agents\skills\relationship-compass`

Windows PowerShell 示例：

```powershell
$SkillRoot = Join-Path $env:USERPROFILE '.agents\skills'
New-Item -ItemType Directory -Path $SkillRoot -Force
Copy-Item -LiteralPath '.\relationship-compass' -Destination $SkillRoot -Recurse
```

Linux/macOS 示例：

```bash
mkdir -p "$HOME/.agents/skills"
cp -R ./relationship-compass "$HOME/.agents/skills/"
```

目标目录已存在时先备份并按“升级方式”比较，不要直接覆盖。安装后重启或刷新 Codex。

## ChatGPT Project 配置方式

1. 新建一个 ChatGPT Project，展示名可设为“关系罗盘”。
2. 将 `chatgpt-project/PROJECT_INSTRUCTIONS.md` 内容粘贴到 Project instructions。
3. 上传 `chatgpt-project/knowledge/` 下 4 份精简知识文件。
4. 同时上传 `shared/CORE_POLICY.md` 和 `shared/FACT_HYPOTHESIS_POLICY.md`，作为双系统共同政策。
5. 如需同步状态，按 `sync/CHECKPOINT_TEMPLATE.md` 为每个对象生成一份待确认 checkpoint；用户审核后再上传。

不要上传 SQLite、WAL/SHM、`memory_store.py`、完整 upstream references、整段聊天导出或跨对象混合 checkpoint。ChatGPT Project 不能直接写 Local Codex Memory。

## Local Codex 配置方式

安装目录使用 `relationship-compass`；`SKILL.md` frontmatter 和调用 slug 保持 `goutoujunshi-personal`。`agents/openai.yaml` 中的展示名是“关系罗盘”。

明确调用：

```text
$goutoujunshi-personal 分析这段聊天，给我自然回复。
```

实时说“她刚回”“现在怎么回”时，系统默认先给可发送内容并隐藏 E 标签与成长教学。长期一致性底线由 `shared/CORE_POLICY.md` 约束。

## Memory 使用说明

Memory 是 Skill 目录外的独立 SQLite 文件。即时分析无需启用长期记忆；首次启用必须由用户明确确认：

```text
python scripts/memory_store.py status
python scripts/memory_store.py enable --confirm
```

常用命令：

```text
python scripts/memory_store.py context --subject-id obj-a --max-chars 4000
python scripts/memory_store.py show
python scripts/memory_store.py show --subject-id obj-a
python scripts/memory_store.py pause
python scripts/memory_store.py resume
python scripts/memory_store.py undo
python scripts/memory_store.py style-status
```

`context` 强制指定一个对象，只返回 user 共享状态和该对象的 active 数据；`show` 可全局审计，并保留显示 stale/superseded hypothesis。字段级删除：

```text
python scripts/memory_store.py delete --scope object --subject-id obj-a --field nickname --confirm
```

event 必须使用带时区的 ISO 8601 `occurred_at`，并标注 `landmark`、`normal` 或 `temporary`。hypothesis 的 TTL、状态和召回规则见 `references/personal/memory_lifecycle.md`。

## 如何添加一本新书

原始书籍留在本机私有位置，不复制到仓库。先登记元数据和 SHA-256 指纹：

```text
python scripts/knowledge_intake.py register <本机书籍路径> --source-id src-example-book --title "书名" --author "作者" --source-type book --publication-year 2024 --edition "1" --language zh-CN --topics conversation personal-growth --source-quality unknown --freshness stable
python scripts/knowledge_intake.py validate
python scripts/knowledge_intake.py status
python scripts/knowledge_intake.py list
```

登记会生成 source card，但不会复制原文件，也不会直接改 curated。补完 source card 后，把符合 claim schema 的候选数组保存为本机 JSON，再生成 proposal：

```text
python scripts/knowledge_intake.py proposal --source-id src-example-book --claims <claims.json>
```

在 `knowledge-management/proposals/src-example-book-proposal.md` 中逐条勾选 approve、reject 或 revise。只有用户亲自审核后才能执行：

```text
python scripts/knowledge_merge.py review --proposal knowledge-management/proposals/src-example-book-proposal.md --confirm
python scripts/knowledge_merge.py merge --proposal knowledge-management/proposals/src-example-book-proposal.md --confirm
```

冲突不能静默覆盖。`keep_existing`、`merge_with_conditions`、`keep_both`、`reject_new` 写在 proposal 的 Conflict Resolution；`replace_existing` 除审核和 merge 确认外，还要对每个旧 claim 增加 `--confirm-replace <claim-id>`。

删除本机原始书籍不会删除审计记录。来源不再推荐时使用 `knowledge_intake.py deprecate --source-id <id> --confirm`；不物理删除已审核来源。新版/修订版用新的 source_id 和指纹重新登记，旧版再 deprecate。元数据笔误可在 registry/source card 修正后运行 validate；已进入 curated 的实质性纠错必须走新 proposal，不能手改覆盖。完整流程见 `knowledge-management/KNOWLEDGE_GOVERNANCE.md`。

## 数据备份方式

先运行 `status` 获取真实路径。备份前暂停写入并确认没有 Memory 命令正在执行，然后复制整个 Memory 目录，而不是只复制主数据库：

```powershell
python scripts\memory_store.py pause
Copy-Item -LiteralPath '<status 返回的 memory 目录>' -Destination '<备份目录>' -Recurse
python scripts\memory_store.py resume
```

目录中可能同时存在 `memory.sqlite3`、`memory.sqlite3-wal` 和 `memory.sqlite3-shm`。备份文件包含敏感关系信息，应使用受控设备或加密存储。

## 升级方式

1. 阅读 `UPSTREAM_LOCK.json` 和 `UPSTREAM_LOCK.md`，确认旧版同步基线。
2. 备份 Memory 和当前安装目录。
3. 把新版解压到临时目录，不要覆盖两个源仓库或现有安装。
4. 运行 `python scripts/validate_skill.py` 和 `python scripts/run_tests.py`。
5. 对比 personal 配置、scripts、tests、shared policy 和 checkpoint schema。
6. 验证通过后替换精确的 `relationship-compass` 安装目录。
7. 首次连接旧库后检查 `status`、单对象 `context`、全局 `show`，并确认 hypothesis 生命周期迁移正常。

Memory schema 采用向后兼容列迁移；升级不会把 SQLite 复制进 Skill。未来 upstream 增量同步遵循 `UPSTREAM_LOCK.md`。

## 卸载方式

仅卸载 Skill：关闭相关任务后，只删除以下精确目录，不要删除整个 skills 根目录：

- Linux/macOS：`$HOME/.agents/skills/relationship-compass`
- Windows：`%USERPROFILE%\.agents\skills\relationship-compass`

撤回同意但保留本地数据库：

```text
python scripts/memory_store.py revoke --confirm
```

同时永久删除 Memory：

```text
python scripts/memory_store.py revoke --delete --confirm
```

## 测试与 Eval

```text
python scripts/run_tests.py
python scripts/validate_skill.py
```

`run_tests.py` 分别统计 unit tests、integration tests 和 contract eval。Contract eval 只验证案例结构与契约，不运行模型。`python scripts/run_model_evals.py validate` 只校验 model eval 定义并明确报告 `NOT RUN`；真实模型行为不得伪造通过。

## 常见问题

### 为什么安装在 `.agents/skills`？

这是本项目统一采用的个人 Skill 路径，可让内部 slug、升级说明和多平台部署保持一致。

### 为什么 context 不能省略对象？

这是对象隔离硬约束。需要全局审计时使用 `show`，不要把全量结果作为某个对象的上下文。

### 为什么过期 hypothesis 还在 show 中？

TTL 只把它从 active 变为 stale，不删除历史。`context` 默认不召回 stale；`show` 仍可审计。

### 对方一次晚回属于 yellow 吗？

通常优先是 gray/证据不足。只有持续趋势、明确不适或边界证据才升级为 yellow/red。

### AI 建议和实际发送不同，记哪一个？

只有用户确认实际发送的版本可进入技巧历史。对方反馈不能自动裁定哪一版更优秀，稳定画像更新仍需用户确认。

### Memory 是否加密？

没有应用层加密。它依赖本机账户、目录权限和磁盘安全；备份也应按敏感数据管理。

<!-- Modified by AI on 2026-08-21 17:01:55 -->
