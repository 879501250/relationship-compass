# Model Eval Results

此目录保存实际 Model Behavioral Eval 结果。当前仓库没有正式 baseline；Definition PASS、fake E2E 或 partial run 都不等于 Behavioral Baseline。

## 目录约定

```text
model_evals/results/v<pack-version>/
├── api_canonical/
│   └── <run-id>/
└── chatgpt_project/
    └── <run-id>/
```

Runtime profile 是 baseline identity 的组成部分，不允许 API canonical 与 ChatGPT Project 结果无标识混合。

每个 run 保存：

```text
run.json
prepared.jsonl
eval-definition.json
runtime-snapshot.json
source-snapshots.json
responses.jsonl
judgments.jsonl
summary.json
summary.md
```

- `run.json`：分离的 Eval/SUT identity、runtime profile、Git SHA/dirty state、Target/Judge provider config、execution purity、生命周期、计数与 recorded fingerprints。
- `prepared.jsonl`：case、criteria、原始输入及 canonical per-case runtime 的 immutable workload snapshot。
- `eval-definition.json`：计算 `eval_definition_hash` 的完整离线来源。
- `runtime-snapshot.json`：实际 profile 的 instruction/runtime identity；schema v3 用它与 per-case runtime 计算属于 SUT 的 `sut_bundle_hash`。
- `source-snapshots.json`：计算 runner 与 Skill content revision 的离线来源。
- `responses.jsonl` / `judgments.jsonl`：schema v3 使用 append-only attempt，逐条携带 run、Eval/SUT、prompt 与 provider binding。
- `summary.json` / `summary.md`：由同一证据集合派生，明确区分行为 FAIL、基础设施错误与未评估。
- `acceptance.json`：可选、独立、不可覆盖的人工接受证据；创建它不会修改前述 evidence。

Validator 按 canonical serialization 重算 fingerprint，而非只检查 `sha256:<64 hex>` 格式；也会拒绝目录 profile/version 不一致、snapshot 篡改、重复或未知 case、criterion 不完整、artifact mix、生命周期冲突、计数或 summary 篡改及 credential-like 字段。

## Baseline 规则

只有 `COMPLETED`、完整保存 Target 原文与独立 Judge 证据、validator 通过并经人工选择的 run 才有 baseline 资格。报告中的 `DIRTY WORKTREE` run 默认不得作为正式 reference baseline。runner 没有自动 promotion 功能，也不会自动将 `baseline` 设为 `true`。

## Frozen historical reference

`v1.6.0/chatgpt_project/baseline-manual-20260825-01` 的 9 个既有文件已由 Git 跟踪并作为 frozen historical reference evidence 保存。它保持 `baseline:false`，不是自动 Gold baseline；当前 validator 只读兼容其 schema v2，禁止回填或改写内容。
