# Relationship Compass

## 关系罗盘

- Repository：`relationship-compass`
- Codex Skill：`relationship-compass`
- Invoke：`$relationship-compass`
- Display：关系罗盘 / Relationship Compass

这是一个个人版 Local Codex Skill，并附带轻量 ChatGPT Project 配置。它用于微信聊天截图与节奏分析、自然回复、关系阶段和投入判断、表达成长复盘，以及在用户明确同意后维护按对象隔离、可撤销的本地记忆。它不导出聊天软件数据，不自动发送消息。

## 主要能力

- 将聊天中的可见事实、用户转述、合理推测和未知信息分开处理。
- 给出与关系阶段、互惠程度和边界相匹配的自然回复与后续节奏。
- 支持聊天表达训练、实际发送复盘和按对象隔离的技巧历史。
- 在明确同意后使用本地 Memory，并提供暂停、撤回、字段删除和对象删除。
- 通过人工批准的 Knowledge 流程维护来源、冲突、去重和可追溯性。

## 安装步骤

安装前在项目根目录运行：

```text
python scripts/validate_skill.py
```

将整个 `relationship-compass` 项目目录复制到以下正式位置：

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
3. 运行 `python scripts/build_chatgpt_pack.py`。
4. 上传 `chatgpt-project/generated-knowledge/` 下 6 份主题 Markdown；共享政策已自动包含在 `01-CORE_POLICY.md`。
5. 使用 `KNOWLEDGE_PACK_INFO.json` 核对 pack、Skill、curated 与 registry revision。
6. 如需同步状态，按 `sync/CHECKPOINT_TEMPLATE.md` 为每个对象生成一份待确认 checkpoint；用户审核后再上传。

不要上传 SQLite、WAL/SHM、`memory_store.py`、完整 upstream references、整段聊天导出或跨对象混合 checkpoint。ChatGPT Project 不能直接写 Local Codex Memory。

## Local Codex 配置方式

安装目录、`SKILL.md` frontmatter 和调用 slug 均使用 `relationship-compass`。`agents/openai.yaml` 中的展示名是“关系罗盘”。

明确调用：

```text
$relationship-compass 分析这段聊天，给我自然回复。
```

实时说“她刚回”“现在怎么回”时，系统默认先给可发送内容并隐藏 E 标签与成长教学。当前回复行为还包括：

- 普通“怎么回”默认给一个首选；
- 简单场景不自动展开长分析；
- 未知的个人经历、偏好或计划不会自动编造；
- 认真倾诉、冲突或边界场景会降低调侃和技巧感。

长期一致性底线由 `shared/CORE_POLICY.md` 约束。

## Memory 使用说明

Memory 是 Skill 目录外的独立 SQLite 文件。即时分析无需启用长期记忆；首次启用必须由用户明确确认：

```text
python scripts/memory_store.py status
python scripts/memory_store.py enable --confirm
```

正式环境变量为 `RELATIONSHIP_COMPASS_MEMORY_DIR`。未设置时，默认目录 basename 是 `relationship-compass`：Windows 使用 `%LOCALAPPDATA%\relationship-compass`，macOS 使用 `$HOME/Library/Application Support/relationship-compass`，Linux 使用 `$XDG_DATA_HOME/relationship-compass` 或 `$HOME/.local/share/relationship-compass`。

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

生成 ChatGPT 精简包：

```text
python scripts/build_chatgpt_pack.py
```

builder 只读取固定 allowlist 中的 shared policy、personal 规则、必要安全知识与已批准 curated 内容。它不会打包 SQLite、Memory 实现、raw、local registry、proposal、review decision、rejected 内容或本机绝对路径。

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
4. 运行 `python scripts/validate_skill.py`。
5. 对比 personal 配置、scripts、tests、shared policy 和 checkpoint schema。
6. 验证通过后替换精确的 `relationship-compass` 安装目录。

Memory schema 采用向后兼容列升级；升级不会把 SQLite 复制进 Skill。未来 upstream 增量同步遵循 `UPSTREAM_LOCK.md`。

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

## 验证与测试

### 完整仓库验收

```text
python scripts/validate_skill.py
```

验证仓库结构、引用、生成产物、Unit、Integration、Contract Eval 和 Model Eval 定义。发布或完成较大改动前使用此命令。

### 仅运行自动测试

```text
python scripts/run_tests.py
```

运行 Unit、Integration 和 Contract Eval，适合开发过程中的快速反馈；不会运行模型行为测试。

### 仅验证 Runtime Skill 包

```text
python scripts/validate_skill.py --runtime
```

只验证正式 Skill runtime 所需结构、引用和运行边界，不执行完整测试套件。

### 仅验证 Model Eval 定义

```text
python scripts/run_model_evals.py validate
```

只校验 Model Eval cases 与 criteria，并明确报告 `NOT RUN`；没有真实模型输出和独立 judge 结果时，不得报告行为评测通过。

## 仓库结构

```text
SKILL.md                         Skill 入口与运行规则
references/                     canonical 知识与个人化规则
scripts/                        Memory、Knowledge、构建与校验工具
tests/                          单元与集成行为测试
evals/                          精简后的 Contract Eval
model_evals/                    高价值人工判断案例与 rubric
knowledge-management/           来源、claim schema 与审批记录
chatgpt-project/generated-knowledge/  由 references 构建的唯一 Knowledge 产物
shared/                         Local / ChatGPT 共享政策
sync/                           经用户确认的同步模板
```

## 隐私说明

Memory 只写入 Skill 目录外的本地 SQLite；首次保存必须明确同意，不保存完整聊天、截图或导出。ChatGPT Knowledge 构建使用固定 allowlist，不包含私有 Memory、本机绝对路径、proposal、review decision 或未批准来源。

## Upstream / License

来源 commit、版本与同步基线见 `UPSTREAM_LOCK.md` 和 `UPSTREAM_LOCK.json`；版权、fork 来源和许可证归属见 `NOTICE.md` 与 `LICENSE`。

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
