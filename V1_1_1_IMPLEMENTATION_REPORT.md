# 关系罗盘 V1.1.1 Implementation Report

## 结论

V1.1.1 已完成工程稳定化，没有增加 Knowledge Intake、Trend Layer、新 Skill 或新的聊天业务能力，也没有大幅改写 references。内部 slug 和 Memory namespace 继续使用 `goutoujunshi-personal`，展示名称统一为“关系罗盘”。两个源仓库均未修改。

## 1. 修改文件列表

### 安装、升级与展示规范

- 重写 `README.md`：项目介绍、跨平台安装、ChatGPT Project、Local Codex、Memory、备份、升级、卸载、测试和 FAQ；安装根统一为 `.agents/skills`。
- 更新 `agents/openai.yaml` 与 `SKILL.md` 的展示标题；frontmatter slug 未变。
- 重构 `UPSTREAM_LOCK.json` 为 schema version 1 的 `sources`，移除本机路径和非必要字段。
- 新增 `UPSTREAM_LOCK.md`，说明 upstream 更新、比较、分批合并和验证流程。
- 新增 `.github/workflows/test.yml`，执行 runtime validation、unit、integration 和 contract eval；不运行真实模型 eval。

### Memory、时间与数据政策

- 新增 `scripts/date_utils.py`：集中处理带时区 ISO 8601 的解析、归一、加天数和年龄计算。
- 更新 `scripts/memory_store.py`：schema version 4、`expires_at`、hypothesis TTL、active/stale/superseded 生命周期、旧库时间校验与 TTL 回填、事实/推测字段门控。
- SQLite 上下文退出时现在显式关闭文件句柄，解决 Windows 测试清理、备份和 revoke/delete 可能被占用的问题。
- 新增 `references/personal/memory_lifecycle.md`。
- 新增 `shared/CORE_POLICY.md` 与 `shared/FACT_HYPOTHESIS_POLICY.md`。
- 小幅更新 `references/personal/成长状态与记忆适配.md`、`复盘模式与实际发送学习闭环.md`，只补生命周期与四类数据边界。

### 双系统一致性

- 更新 `chatgpt-project/PROJECT_INSTRUCTIONS.md`、`chatgpt-project/README.md` 和 `knowledge/PRIVACY_AND_CHECKPOINTS.md`，要求上传并遵循共享政策。
- `sync/CHECKPOINT_TEMPLATE.md` 升级为 version 2，固定分为 confirmed、hypothesis、recommendation、unknown；stage/trend/humor receptivity 只允许进入 hypothesis。
- Local Skill 和 ChatGPT Project 都引用同一份 `shared/CORE_POLICY.md`，避免复制后漂移。

### 测试与验证工具

- 新增 `scripts/run_tests.py`，分开统计 unit tests、integration tests 和 contract eval。
- 重组 `tests/unit/` 与 `tests/integration/`；删除原先全部依赖 subprocess 的 `tests/test_memory_store.py` 和旧 `tests/test_eval_runners.py`。
- 更新 `scripts/validate_skill.py`：校验新目录、display name、shared policy parity、upstream lock schema，并直接加载测试套件与 contract eval。

## 2. 新增测试

Unit tests 直接 import Python 模块，不通过 CLI 子进程：

- `tests/unit/test_memory_isolation.py`：obj-a context 不出现 obj-b subject 或内容。
- `tests/unit/test_memory_retention.py`：超过 event limit 后 landmark 保留、temporary 可淘汰。
- `tests/unit/test_memory_delete.py`：删除单条字段不影响其他对象和其他字段，且可 undo。
- `tests/unit/test_memory_expiration.py`：TTL 规则、active→stale、旧判断 superseded、show 可审计、context 不召回 stale、事实/推测字段门控。
- `tests/unit/test_date_utils.py`：时区必填、UTC 归一和 TTL 日期计算。
- `tests/unit/test_memory_schema.py`：旧 schema 增加 retention/expires_at、旧 hypothesis 回填 TTL、restore 拒绝无时区时间。
- `tests/unit/test_memory_core.py`：consent、pause/resume、source gate、capability、style review、context 长度、undo、prune、技巧历史和 revoke/delete。

Integration tests 只覆盖真实进程边界，并给每个 subprocess 设置 10 秒 timeout：

- `tests/integration/test_memory_cli.py`：CLI 强制 subject-id、CLI 往返对象隔离。
- `tests/integration/test_eval_runners.py`：contract 不运行模型、model definition 明确 NOT RUN、不完整 judgment 不得通过。

## 3. 测试结果

| 命令 | 结果 | 耗时 |
| --- | --- | --- |
| `python -B -m unittest discover -s tests/unit -v` | 通过；最终套件 16 tests | 分类 runner 中约 1.0 秒 |
| `python -B -m unittest discover -s tests/integration -v` | 通过；5 tests | 分类 runner 中约 1.0 秒 |
| `python scripts/run_tests.py` | 通过；16 unit、5 integration、9 suites/40 contract cases | 内部统计 2.06 秒，墙钟约 2.48 秒 |
| `python -B scripts/validate_skill.py --runtime` | 通过 | 约 2 秒内 |
| `python -B scripts/validate_skill.py` | 通过；policy、lock、tests、eval 定义全量校验 | 约 3 秒 |
| `python -B scripts/run_model_evals.py validate` | 定义通过；9 cases、27 criteria；`NOT RUN` | 不执行模型行为 |

目标“完整测试 30 秒以内”已达到。CI 配置没有执行真实模型 eval，也没有把 contract eval 描述成模型行为验证。

## 4. 兼容性说明

- `name: goutoujunshi-personal`、调用 `$goutoujunshi-personal` 和本地 Memory namespace 不变；只改展示名。
- V1.1 SQLite 首次连接时自动增加 `expires_at`；已有合法时区时间归一为 UTC。受管字段的旧 active hypothesis 会根据 `observed_at` 回填 TTL。
- `context` 仍只返回 user + 指定对象，并默认只召回 active；`show` 现在会同时展示 stale/superseded，属于审计视图的预期增强。
- TTL 到期不删除 hypothesis。新同字段 hypothesis 会保留旧记录并标为 superseded。
- checkpoint schema 从 version 1 升到 version 2；旧 checkpoint 导入前需要把关系阶段、走势、幽默接受度移到 hypothesis 分栏。
- `UPSTREAM_LOCK.json` 的顶层从旧 `upstreams` 变为 `sources`；依赖旧字段的外部脚本需要按 schema version 1 新结构调整。

## 5. 未解决问题

1. SQLite 仍无应用层加密，数据安全依赖设备账户、文件权限、磁盘加密和备份管理。
2. Model behavioral eval 尚未真实运行；当前只验证 9 个案例和 27 条 rubric 定义。
3. stale/superseded hypothesis 按“不因 TTL 删除”的要求长期保留；大量历史最终可能触达 200 行硬上限，需要人工审计，而不是自动清除。
4. 旧数据库若包含无时区或损坏时间现在会明确报 `CORRUPT_TIMESTAMP`；项目没有自动猜测并修复错误时间。
5. ChatGPT Project 是否实际上传两份 shared policy、是否使用 project-only memory，仍依赖人工部署核对。
6. GitHub Actions 工作流已建立但当前本地环境无法代表远端 runner；首次推送后仍需观察真实 CI 结果。
7. 两个源仓库当前均有未提交内容：original 修改了 Memory 脚本并有未跟踪文档，warm-fork 有未跟踪 IDE 目录。本阶段没有覆盖、恢复或复制这些内容。

## 6. 下一阶段建议

下一阶段仍以维护验证为主：首次提交后确认 GitHub Actions 结果；做一次 Memory 备份/恢复演练；用一份 version 1 checkpoint 演练迁移到 version 2；积累真实模型输出后再运行 behavioral eval。除非这些稳定性证据显示明确缺口，不建议继续扩张产品能力。

<!-- Modified by AI on 2026-08-21 14:47:55 -->
