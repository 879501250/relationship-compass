# Model Behavioral Eval

这一层真实执行并评估 Skill 输出，不等同于 `evals/` 的 Contract Eval。API 与 ChatGPT Project 手工路径共享 case、criterion、artifact schema、report 与 validator，但明确保留各自的 runtime identity。

## Definition、Behavior 与 Baseline

- Definition Validation：验证 `cases.yaml`、`rubric.yaml`、runtime route 与 19 case / 77 criterion 结构，不调用模型。
- Behavioral Evaluation：在指定 runtime profile 中真实执行 Target，再由独立 Judge 逐项判断。
- Artifact Validation：从保存的 immutable snapshots 重算 provenance、计数与 summary，不调用模型。
- Baseline：人工复核后选择的完整真实 run；runner 不自动 promotion，也不会自动把 `baseline` 设为 `true`。

## Runtime Profile

- `api_canonical`：OpenAI API Target 使用 runner 的 Target instructions、逐 case canonical runtime 和原始用户输入。
- `chatgpt_project`：人工在 ChatGPT Project 中使用 Project Instructions、Generated Knowledge 和原始用户消息。

两种 profile 的 instruction hierarchy 不同，不能声称行为等价。每个 run、summary、report、artifact binding 和目录都记录 profile：

```text
model_evals/results/v<pack-version>/<runtime-profile>/<run-id>/
```

ChatGPT Project 导出保存当时的 Project Instructions、全部 Generated Knowledge 与 pack metadata，代表操作者应使用的 runtime snapshot；它不能远程证明浏览器中 Project 配置未被改动，因此仍需执行者核对。

## Target 与 Judge 隔离

Target 只看到实际 runtime 和原始用户输入，不看到 case id、title、reply/analysis mode、criterion、rubric、预期结论或 baseline。Judge 使用独立 API 调用或独立 Chat，只读取 case、原始输入、Target 原文和完整 criteria。

## Fingerprint Provenance

所有 JSON/JSONL 先解析为语义对象，再用 UTF-8、sorted keys、紧凑 separators 的 canonical JSON 计算 hash；JSON key 顺序、缩进和无意义 whitespace 不影响结果。文本 snapshot 统一换行为 LF。

| 字段 | 精确语义与可重算来源 |
| --- | --- |
| `git_sha` | 执行时仓库 HEAD；配合 `git_dirty` 表示 Git identity，不宣称能由 artifact 内容重算。 |
| `runner_revision` | `source-snapshots.json` 中 runner source 内容的 SHA-256。 |
| `skill_revision` | `source-snapshots.json` 中 `SKILL.md` 内容的 SHA-256。 |
| `eval_definition_hash` | `eval-definition.json` 完整语义对象的 canonical SHA-256。 |
| `bundle_hash` | `prepared.jsonl` 语义记录与 `runtime-snapshot.json` 语义对象组合后的 canonical SHA-256。 |

`runtime_bundle_hash` 已移除，因为此前与 `bundle_hash` 输入完全相同。Responses 与 judgments 逐条绑定 `run_id`、`runtime_profile`、`bundle_hash`，防止同 case 集合的跨 run 混用。

每个结果目录包含：

```text
run.json
prepared.jsonl
eval-definition.json
runtime-snapshot.json
source-snapshots.json
responses.jsonl
judgments.jsonl       # Judge 开始后；手工 run 从 NOT_JUDGED 占位开始
summary.json          # report 后生成
summary.md
```

## 共同准备步骤

```text
python scripts/run_model_evals.py validate
python scripts/run_model_evals.py prepare --output .work/model-eval-prepared.jsonl
```

`.work/` 是 Git 忽略的本地临时区。`prepare` 不调用模型。

## API 自动路径

当前只有 OpenAI Responses API provider。凭证只从 `OPENAI_API_KEY` 读取，模型来自 `OPENAI_MODEL` / `OPENAI_JUDGE_MODEL` 或 `--model`。缺少凭证或模型时明确报告 `behavioral evaluation NOT RUN`，且不创建 run 目录。

```text
python scripts/run_model_evals.py run --prepared .work/model-eval-prepared.jsonl --run-id <run-id>
python scripts/run_model_evals.py judge --run-dir model_evals/results/v<pack-version>/api_canonical/<run-id>
python scripts/run_model_evals.py report --run-dir model_evals/results/v<pack-version>/api_canonical/<run-id>
```

## ChatGPT Project 手工路径

```text
python scripts/run_model_evals.py export-manual --prepared .work/model-eval-prepared.jsonl --output .work/manual-target --run-id <run-id> --target-model <model-label>
python scripts/run_model_evals.py import-responses --manual-dir .work/manual-target --input .work/manual-responses.jsonl
python scripts/run_model_evals.py export-judge --run-dir model_evals/results/v<pack-version>/chatgpt_project/<run-id> --output .work/manual-judge
python scripts/run_model_evals.py import-judgments --run-dir model_evals/results/v<pack-version>/chatgpt_project/<run-id> --input .work/manual-judgments.jsonl --judge-mode manual_chatgpt --judge-model <model-label>
python scripts/run_model_evals.py report --run-dir model_evals/results/v<pack-version>/chatgpt_project/<run-id>
```

逐个 `target/*.md` 新建 Chat 并复制完整正文；正文只有原始用户输入。Target response 必须逐字保存。Responses 可以分批导入，但必须完成全部 Target case 后才能开始 Judge。Judge 可使用 `manual_chatgpt` 或 `manual_human`，可分批导入，但同一 run 的 Judge identity 必须保持一致。

## Lifecycle

| 状态 | 含义 | 顶层 `completed_at` |
| --- | --- | --- |
| `PREPARED` | 已保存身份与 snapshots，Target 尚未产生记录。 | `null` |
| `TARGET_PARTIAL` | 只有部分 Target responses。 | `null` |
| `TARGET_COMPLETE` | 全部 Target responses 完成，Judge 尚未开始。 | `null` |
| `JUDGE_PARTIAL` | Judge 已开始但未覆盖全部 case。 | `null` |
| `COMPLETED` | Target 与 Judge 全部完成。 | 必须存在 |
| `FAILED` | Target 或 Judge 出现终止性基础设施错误。 | 必须存在 |

阶段时间由 `target_started_at`、`target_completed_at`、`judge_started_at`、`judge_completed_at` 表达。`completed_at` 只表示整个 lifecycle 已进入终态。

## Validator 与 Baseline

Validator 会重算 definition/bundle/runner/Skill hashes，交叉验证 snapshots、case/criterion、artifact binding、lifecycle、responses、judgments、summary 和人读报告。错误消息给出 field、recorded、computed 与 source。

报告会显示 runtime profile；`git_dirty=true` 时明确标记 `DIRTY WORKTREE`。Dirty run 可用于开发诊断，但不得默认视为正式 reference baseline。正式 baseline 至少要固定 Git identity、dirty state、runtime profile、runner/Skill/definition/bundle fingerprints、Target/Judge identity、执行时间和完整原文证据。

自动测试、CI 与 `validate_skill.py` 都不调用付费 Behavioral Eval。
