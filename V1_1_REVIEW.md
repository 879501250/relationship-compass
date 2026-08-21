# goutoujunshi-personal V1.1 Review

## 结论

V1.1 已按“工程加固，不重做产品设计”完成 Local Codex 侧实现，并建立轻量 ChatGPT Project 与人工确认的双向 checkpoint 流程。`DESIGN_ANALYSIS.md` 未继续扩写，`SKILL.md` 只做了一处替换：明确 E 不再表示全局稳定能力，没有增加新章节或扩大入口体积。

## 修改内容

### P0-1 Memory context 对象隔离

- `context` 的 `--subject-id` 改为必填；省略时 argparse 直接拒绝。
- `context obj-a` 只召回 `subject_id=user` 的共享记录与 `obj-a` 记录。
- `show` 保留可选 `--subject-id`，不指定时可做全局审计。
- 自动测试同时写入 obj-a/obj-b，并断言 obj-a context 不出现 obj-b subject 或值。

### P0-2 Contract Eval 与 Model Behavioral Eval

- `run_evals.py` 更名为 `run_contract_evals.py`。
- Contract runner 明确输出 `no model behavior was executed`，只验证 JSON-compatible YAML、案例结构和契约覆盖。
- 新建 `model_evals/cases.yaml`、`rubric.yaml` 和 `run_model_evals.py`。
- Model runner 支持：验证定义、导出真实运行工作项、校验逐条原始输出和外部显式 judgment、汇总 rubric。
- 没有真实模型输出和完整 judgment 时不能通过；当前行为测试状态为 `NOT RUN`，没有伪造结果。
- 原 `IMPLEMENTATION_REPORT.md` 已改正措辞，不再把 contract eval 当模型行为验证。

### P0-3 事件长期保留

- event 新增 `retention=landmark|normal|temporary`，旧库自动迁移为 `normal`。
- event 超过每对象上限时，先淘汰最旧 temporary，再淘汰 normal；landmark 不进入普通 FIFO 删除候选。
- 全局行数压力同样不自动删除 landmark；只有 landmark 导致无法降到硬上限时，写入失败并要求人工整理，而不是静默删节点。
- context 先召回全部 landmark，再召回最近 normal/temporary 关键事件。
- 自动测试写入第一次见面、关系确认和 24 条 temporary，确认两个 landmark 仍在 show/context。

### P1 Memory 与时间线

- event 写入要求带时区的 ISO 8601 `occurred_at`，统一归一为 UTC。
- `occurred_at` 与 `updated_at` 分开返回；event 展示和 context 优先按发生时间。
- 自动测试先写近事件、后补录旧事件，确认旧事件不会因今天写入被排到时间线最前。
- 新增 `delete --scope --subject-id --field [--occurred-at] --confirm`；多条 event 匹配时拒绝模糊删除。
- 字段删除写入 operations，可按 `op_id` undo；测试确认不会误删同对象其他字段或其他对象。
- `tests/test_memory_store.py` 使用标准库和独立临时数据库，可重复运行，不依赖报告文字。

### P1 表达能力、反馈与学习闭环

- 停止新增全局 `trained_expression_level`；写入该 legacy 字段会返回 `DEPRECATED_FIELD`。
- 新增 `capability_profile`，维度为 initiative、self_disclosure、opinion、storytelling、observation_humor、callback、teasing、flirting、continuation。
- 能力状态只允许 emerging/developing/stable；不接受精确分数，稳定 user 画像仍只接受 `user_explicit`。
- 关系反馈改为 green / gray / yellow / red。单次忙、短回或晚回优先 gray，持续趋势或明确不适才进入 yellow。
- 新增对象级 `recent_techniques`，只保留最近 8 次主技巧；必须通过 `record-technique --confirm-sent` 写入，不能跨对象共享，也不保存完整聊天。
- 新增 Review Mode：分开 User Growth 与 Partner / Relationship，最后只选一个训练重点。
- 新增实际发送闭环：比较 AI 建议与实际发送，提炼用户愿意认领的偏好；partner response 不作为版本质量裁判，稳定画像更新仍需确认。

### 工程维护与双系统

- 新增根 `README.md`：安装、首次使用、Memory、模式、备份、升级、卸载和 FAQ。
- 新增 `UPSTREAM_LOCK.json`，锁定 original/warm-fork URL、40 位 commit 和复制日期。
- 新建 `chatgpt-project/`：Project instructions、4 份精简知识和部署说明。
- 新建 `sync/`：ChatGPT→Codex、Codex→ChatGPT 和单对象 checkpoint 模板。
- ChatGPT Project 不包含 SQLite、memory_store、完整 knowledge/practical upstream 或聊天导出。

## 自动测试结果

| 检查 | 结果 | 能证明什么 |
| --- | --- | --- |
| `python -B -m unittest discover -s tests -v` | 通过：17 tests | Memory CLI、隔离、retention、时间线、删除/撤销、迁移与 eval runner 语义 |
| `python scripts/run_contract_evals.py` | 通过：9 suites、40 cases | 案例结构和产品契约覆盖；不证明模型行为 |
| `python scripts/run_model_evals.py validate` | 定义通过：9 cases、27 criteria；`NOT RUN` | 只证明 model eval 案例/rubric 可用 |
| `python scripts/validate_skill.py --runtime` | 通过 | Runtime 结构、入口预算、引用和边界 |
| `python scripts/validate_skill.py` | 通过 | 全项目 inventory、标记、contract/model 定义与可重复测试 |
| skill-creator `quick_validate.py` | 环境未执行：缺少 PyYAML | 外部通用校验器依赖缺失；不冒充通过，项目自带校验已通过 |

Memory 自动测试覆盖：consent gate、pause/resume、user source gate、obj-a/obj-b context isolation、event retention、occurred_at/backfill timeline、undo、field delete、style review、context max chars、prune、recent techniques、legacy schema migration、revoke/delete 和 capability profile。

## Contract Eval 与 Model Eval 的边界

Contract eval 检查“案例有没有声明必须隐藏 E、gray 是否不自动降级、landmark 是否受保护”等契约。它不会加载 Skill 生成回复，也不会判断回复是否自然。

Model behavioral eval 必须把合成 prompt 交给真实加载本 Skill 的模型，保存原始输出，再由人工或独立 judge 对明确 rubric 逐项给布尔判断。V1.1 只交付了案例、runner 接口和人工/半自动流程；当前环境没有自动模型调用链，因此不能报告行为通过。

## 仍未解决风险

1. Model behavioral eval 尚未真实运行；不同模型、客户端上下文和指令优先级下的遵循程度未知。
2. landmark 依赖写入时正确分类。系统宁可在大量 landmark 撞到硬上限时拒绝写入，也不自动删除；仍需要人工复核错误标记。
3. recent_techniques 只记录用户确认的实际发送。如果用户不反馈，重复检测看到的是不完整样本。
4. SQLite 使用本机文件权限和同意门控，但没有应用层加密；设备账户或磁盘安全仍由用户负责。
5. ChatGPT Project 的项目内 memory 不具备本地 SQLite 同等级的字段审计和对象查询，双向同步仍是人工确认流程。
6. context 的 8 条非 landmark 事件和最近 8 次技巧是工程默认值，不保证适合所有关系频率。
7. Gray/yellow 的趋势判断仍是语义判断，无法只靠静态规则消除误判。

## 需要真实用户数据才能继续优化

- 哪些短回/晚回组合在用户的真实对象中仍应保持 gray，何时才稳定转为 yellow。
- capability profile 各维度从 emerging 到 developing/stable 所需的真实样本量和用户主观认领标准。
- 最近 8 次技巧是否足以避免重复，还是应按聊天频率使用 5、6 或时间窗口。
- continuation ownership 在对方积极接梗、反调侃和不接梗时，用户实际能否不用再次代写继续。
- Review Mode 每 1 周、2 周或关键节点触发，哪种节奏最有帮助且不制造绩效感。
- AI 建议与实际发送版本长期差异中，哪些稳定反映 preferred_flavor，哪些只是单次语境选择。
- ChatGPT Project 与 Local Codex 在同一 checkpoint 上是否给出一致的阶段、gray/yellow 和边界判断。

<!-- Modified by AI on 2026-08-21 13:52:39 -->
