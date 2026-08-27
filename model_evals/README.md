# Model Behavioral Eval

这一层真实执行并评估 Relationship Compass 输出，不等同于 `evals/` 的 Contract Eval。当前固定 19 个 Behavioral Core、11 个 Behavioral Stress，共 30 个 case / 78 个 unique criteria；本轮没有增加或削弱考题。

一轮 Behavioral Reference 必须分别回答：

1. `Execution Status`：Target / Judge 是否完整执行；
2. `Behavioral Status`：criteria 与 Core / Stress 的 PASS/FAIL；
3. `Reference Quality`：endpoint、model identity、relay 与 execution purity 的证据等级；
4. `Acceptance Status`：是否存在独立人工 acceptance evidence；
5. `Comparability`：测量尺、SUT、Target、Judge、execution 分别有哪些差异。

任何 Definition PASS、30/30 PASS、fake E2E 或 report 生成都不会自动把 run 设为 baseline 或 accepted。

## Eval Identity 与 SUT Identity

`Eval Identity` 是测量尺，覆盖 case definitions / prompts、criterion assignments、rubric、suite metadata、Judge prompt / calibration 与 eval schema。新 run 保存：

```text
eval_definition_hash
cases_hash
rubric_hash
judge_prompt_hash
suite_metadata_hash
eval_identity_hash
```

`SUT Identity` 是被测 Relationship Compass，覆盖 product version、Git SHA、runtime profile、Skill / Project Instructions、Generated Knowledge、runtime snapshot 与 `sut_bundle_hash`。兼容字段 `bundle_hash` 在 schema v3 中与 `sut_bundle_hash` 相同，明确属于 SUT，不是 hard comparability gate。

因此，同一 Eval 下的 v1.6 与 v1.7 SUT bundle 变化是标准 regression：只要 Target、Judge、sampling 与 execution policy 相同，结果仍为 `COMPARABLE`。rubric、case prompt、criterion assignment 或 Judge calibration 改变才是 `NOT_COMPARABLE`。

Compare 输出按类别解释差异：

```json
{
  "level": "COMPARABLE",
  "differences": {
    "eval_definition": [],
    "sut": ["product_version", "sut_bundle_hash"],
    "target": [],
    "judge": [],
    "execution": []
  }
}
```

## Runtime profile

- `api_canonical`：每个 Target 获得固定 `TARGET_INSTRUCTIONS`、该 case 路由的 Skill/runtime sources 内容，以及原始用户输入。`target_prompt_version`、canonical prompt hash、system/runtime/user hashes 与去密后的 request envelope hash 会进入 response evidence。
- `chatgpt_project`：Target 由操作者在 ChatGPT Project 手工执行，使用 Project Instructions、Generated Knowledge 与原始用户消息。

API canonical snapshot 明确冻结 `SKILL.md`、`shared/CORE_POLICY.md`、`shared/FACT_HYPOTHESIS_POLICY.md` 和每个 case 的 1–3 个 routed references；ChatGPT Project snapshot 冻结 Project Instructions、Generated Knowledge 与 pack info。

ChatGPT Web 与 API 即使显示同一模型名，也可能因 system configuration、orchestration、memory、tools、hidden routing 或 serving version 不同而产生不同结果。两个 runtime profile 默认最多 `PARTIALLY_COMPARABLE`，不能宣称等价。

## Provider、endpoint 与 model identity

支持三类 transport，不为单个 Kimi alias 硬编码 runner：

| Provider | Transport | 用途 |
| --- | --- | --- |
| `openai_responses` | OpenAI Responses JSON | OpenAI 官方 endpoint 或显式声明的 Responses relay。 |
| `openai_compatible_chat` | Chat Completions-compatible JSON | Moonshot 官方 compatible endpoint 或第三方 relay。 |
| `chatgpt_web_manual` | manual copy/paste | 手工 Target、Judge 或显式 fallback。 |

Target 和 Judge 各自独立记录 `provider_identity` 与 `model_identity`。Model status 为 `MATCHED / MISSING / MISMATCH / MULTIPLE / USER_REPORTED / UNVERIFIED`。API `reported_model` 只来自 provider response；ChatGPT Web 的标签保存为 `user_reported_model`，其 `requested_model` / `reported_model` 均为 `null`。

正式模式默认禁止 silent model failover：direct 或 relay 返回与 requested alias 不同的 model 会产生 `MODEL_IDENTITY_MISMATCH`，不会成为成功 response。Relay 不返回 model 可以继续执行，但 status 为 `MISSING`，Reference Quality 降级。

Endpoint provenance：

- `verified_direct`：adapter、vendor 与内置官方 origin 同时匹配；
- `declared_relay`：relay 声明 upstream，但仓库不能独立验证；
- `unverified_relay`：upstream 身份证据不足；
- `user_reported`：ChatGPT Web / manual 标签由操作者报告。

requested alias 相同不等于真实 upstream 可验证。Relay 可能动态路由、改写 system message、忽略参数、降级模型或不返回真实 model identity；其 Behavioral Reference 仍有价值，但 provenance 必须诚实记录。

## Sampling policy 与 capability preflight

每个 API role 显式保存 `temperature / top_p / seed / n=1 / reasoning_effort / max_output_tokens`。未配置或不支持的值保持 `null / unsupported`，不会伪造成 temperature=0。正式 reference 固定 `samples_per_case=1`，不做 Best-of-N；未来重复采样必须保存并评价全部 sample。

Local profile 必须声明 capability：reasoning effort、structured output mode、temperature、top_p、seed 与 max-output 参数名。配置了未声明支持的参数会在发请求前失败，不会 silent drop。OpenAI-compatible Chat 的 `max_output_tokens_parameter` 可声明为 `max_tokens` 或 `max_completion_tokens`；payload 只发送声明的一个字段，未知字段在 preflight 失败。Responses adapter 只接受其原生 `max_output_tokens`。

Provider setting 的最终优先级统一为 `CLI explicit > local/provider profile > provider-specific environment fallback > built-in default`。`api_key_env / base_url_env / timeout_seconds / max_retries / max_output_tokens` 在 argparse 中不预填伪装成 CLI 的最终值；resolved effective value 会写入 provider manifest。Target 默认 max output 1200，Judge 默认 2400；两者默认 timeout 90 秒、retry 1 次。

Endpoint 使用更严格的固定顺序：`CLI --base-url > CLI --base-url-env 对应值 > profile base_url > profile base_url_env 对应值 > provider default env > provider official default`。显式 CLI/profile env 名存在但变量未设置时 preflight fail closed，不会悄悄改走全局 endpoint。尤其是 profile 已写静态 relay `base_url` 时，环境中的 `OPENAI_BASE_URL` 不会覆盖它。`isolated-relays-example` 展示 Target/Judge 各自独立的 key env 与静态 endpoint。

Structured output 是 role-aware capability：Behavioral Target 只请求普通 assistant text，manifest 为 `structured_output_required=false / structured_output_mode=null`，不要求 relay 支持 `json_schema` 或 `json_object`；Judge 仍必须声明并通过所选 `strict_json_schema / json_object / text_json_fallback` capability。Target comparability 也不比较这个 N/A 字段。

复制不含 secret 的示例：

```text
Copy-Item model_evals/provider_profiles.example.yaml model_evals/provider_profiles.local.yaml
```

API key 只从最终解析出的环境变量名读取：CLI `--api-key-env` 优先于 profile，二者都没有时才使用 provider built-in env name。本地 profile 已被 Git 忽略；artifact 不保存 key、Authorization、path/query secret、hidden reasoning、thinking 或 analysis trace。价格只允许在 local profile 配置，不硬编码。

## 共同准备、preflight 与 smoke

```text
python scripts/run_model_evals.py validate
python scripts/run_model_evals.py prepare --output .work/model-eval-prepared.jsonl
python scripts/run_model_evals.py provider-check --role target --profile full-api-reference --profiles-file model_evals/provider_profiles.local.yaml
python scripts/run_model_evals.py provider-check --role judge --profile full-api-reference --profiles-file model_evals/provider_profiles.local.yaml
```

以上命令不调用模型。Preflight 输出最终解析后的 provider、requested model、脱敏 endpoint origin/hash/source、provenance、timeout、retry、max output、reasoning 与 role-aware structured output，再检查 adapter、capabilities、token parameter 与 sampling；不会输出 API key、Authorization 或完整 secret URL。若 model existence 无法零成本确认，不会为了 check 强制发付费请求。

Smoke 是显式单请求连通性验证，只能写 `.work/`：

```text
python scripts/run_model_evals.py smoke --role target --prepared .work/model-eval-prepared.jsonl --case-id <case-id> --profile <target-profile> --profiles-file model_evals/provider_profiles.local.yaml
python scripts/run_model_evals.py smoke --role judge --prepared .work/model-eval-prepared.jsonl --case-id <case-id> --profile <judge-profile> --profiles-file model_evals/provider_profiles.local.yaml
```

Judge smoke 使用占位 Target text，只验证 structured output、criteria JSON、reported model 与 relay compatibility，不污染正式 results。

## 四条标准工作流

### A. Manual Target / Manual Judge

```text
python scripts/run_model_evals.py export-manual --prepared .work/model-eval-prepared.jsonl --output .work/manual-target --run-id <run-id> --target-model <user-reported-label>
python scripts/run_model_evals.py import-responses --manual-dir .work/manual-target --input .work/manual-responses.jsonl
python scripts/run_model_evals.py export-judge --run-dir model_evals/results/v<version>/chatgpt_project/<run-id> --output .work/manual-judge
python scripts/run_model_evals.py import-judgments --run-dir model_evals/results/v<version>/chatgpt_project/<run-id> --input .work/manual-judgments.jsonl --judge-mode manual_chatgpt --judge-model <user-reported-label>
python scripts/run_model_evals.py report --run-dir model_evals/results/v<version>/chatgpt_project/<run-id>
```

### B. Manual Target / API Judge

```text
python scripts/run_model_evals.py export-manual --prepared .work/model-eval-prepared.jsonl --output .work/manual-target --run-id <run-id> --target-model <user-reported-label>
python scripts/run_model_evals.py import-responses --manual-dir .work/manual-target --input .work/manual-responses.jsonl
python scripts/run_model_evals.py judge --run-dir model_evals/results/v<version>/chatgpt_project/<run-id> --profile <judge-profile> --profiles-file model_evals/provider_profiles.local.yaml
python scripts/run_model_evals.py report --run-dir model_evals/results/v<version>/chatgpt_project/<run-id>
```

### C. API Target / Manual Judge

```text
python scripts/run_model_evals.py run --prepared .work/model-eval-prepared.jsonl --run-id <run-id> --profile <target-profile> --profiles-file model_evals/provider_profiles.local.yaml
python scripts/run_model_evals.py export-judge --run-dir model_evals/results/v<version>/api_canonical/<run-id> --output .work/manual-judge
python scripts/run_model_evals.py import-judgments --run-dir model_evals/results/v<version>/api_canonical/<run-id> --input .work/manual-judgments.jsonl --judge-mode manual_chatgpt --judge-model <user-reported-label>
python scripts/run_model_evals.py report --run-dir model_evals/results/v<version>/api_canonical/<run-id>
```

### D. Full API Reference（推荐）

按 `validate → prepare → provider-check target → provider-check judge → smoke target → smoke judge → run → judge → report → compare → human acceptance` 执行。

先预览不可忽略的执行摘要，不发请求：

```text
python scripts/run_model_evals.py run --prepared .work/model-eval-prepared.jsonl --run-id <run-id> --profile <target-profile> --profiles-file model_evals/provider_profiles.local.yaml --dry-run
```

正式执行：

```text
python scripts/run_model_evals.py run --prepared .work/model-eval-prepared.jsonl --run-id <run-id> --profile <target-profile> --profiles-file model_evals/provider_profiles.local.yaml --concurrency 1
python scripts/run_model_evals.py judge --run-dir model_evals/results/v<version>/api_canonical/<run-id> --profile <judge-profile> --profiles-file model_evals/provider_profiles.local.yaml --dry-run
python scripts/run_model_evals.py judge --run-dir model_evals/results/v<version>/api_canonical/<run-id> --profile <judge-profile> --profiles-file model_evals/provider_profiles.local.yaml
python scripts/run_model_evals.py report --run-dir model_evals/results/v<version>/api_canonical/<run-id>
python scripts/run_model_evals.py compare --run-a <run-a> --run-b <run-b>
python scripts/run_model_evals.py accept-reference --run-dir model_evals/results/v<version>/api_canonical/<run-id> --notes "human reviewed"
python scripts/run_model_evals.py reference-status --run-dir model_evals/results/v<version>/api_canonical/<run-id>
```

Target / Judge 可以来自同一 relay，也可以使用不同 provider、endpoint 与 model。Target 与 Judge requested model 相同会产生 `CORRELATED_JUDGE_RISK`；同 relay endpoint + 同 alias 且 upstream 不可验证时另有 `TARGET_JUDGE_IDENTITY_CORRELATED`。

## Retry、resume 与错误恢复

HTTP adapter 仅对 429、timeout、network reset 和选定 5xx 使用有限 1/2/4/8 秒 backoff。401/403、invalid request/model、unsupported parameter 与 model mismatch 不自动重试。

Target `responses.jsonl` 与 Judge `judgments.jsonl` 均为 append-only attempt evidence。Target 有效回答是该 case 最新成功 attempt；resume 跳过成功，只重试 `retryable=true` 的 `TARGET_ERROR`：

```text
python scripts/run_model_evals.py run --prepared .work/model-eval-prepared.jsonl --run-id <run-id> --profile <target-profile> --profiles-file model_evals/provider_profiles.local.yaml --resume
python scripts/run_model_evals.py judge --run-dir <run-dir> --profile <judge-profile> --profiles-file model_evals/provider_profiles.local.yaml --resume
```

只有显式 `--retry-non-retryable` 才会重试 Target non-retryable error。Resume 校验 provider/transport/model/endpoint、reasoning、sampling、max output、runtime、SUT bundle、Eval identity、Target/Judge prompt；任一变化都拒绝混入同一 run。

错误通过 `TARGET_ERROR / JUDGE_ERROR + error_code + retryable` 记录。分类包括 `AUTH_ERROR`、`RATE_LIMIT`、`TIMEOUT`、`NETWORK_ERROR`、`PROVIDER_4XX`、`PROVIDER_5XX`、`UNSUPPORTED_PARAMETER`、`MODEL_IDENTITY_MISMATCH`、`EMPTY_RESPONSE`、`CONTENT_FILTER`、`INVALID_RESPONSE`、`INVALID_STRUCTURED_OUTPUT` 与 `NON_RETRYABLE_ERROR`。

## Manual fallback 与 execution purity

API Target 永久失败时，可只导出剩余 case：

```text
python scripts/run_model_evals.py export-manual-target --run-dir <run-dir> --output .work/manual-target-fallback --remaining
python scripts/run_model_evals.py import-manual-target --run-dir <run-dir> --input .work/manual-target-fallback-responses.jsonl --user-reported-model <label>
```

API Judge 部分失败后也可 `export-judge` / `import-judgments` 做人工 adjudication。Schema v3 读取最新 append-only attempt，不会因 duplicate case_id 崩溃。

报告分别给出 Target / Judge 的 `PURE_API / PURE_MANUAL / MIXED_EXECUTION`。Mixed run 可以生成 Behavioral report，但 Reference Quality / comparability 降级，绝不会伪装成纯 API。

Comparability 除 Eval、SUT、configured provider 与 execution identity 外，也比较实际 Target/Judge model evidence：`requested_model / model_identity.status / reported_models / provider provenance`。因此 MATCHED 对 MISSING、相同 alias 但 reported model 不同、或 relay provenance 变化都会明确列入 Target/Judge differences，并至少降为 `PARTIALLY_COMPARABLE`；纯 SUT bundle 变化仍保持 `COMPARABLE`。

## Git clean gate 与 acceptance evidence

正式 API `run` 默认要求 `git_dirty=false`。`--allow-dirty-debug` 只产生明确的 `DIRTY_DEBUG / REFERENCE_NOT_ELIGIBLE` 证据，不能用于正式 reference。

`accept-reference` 只能由人工显式执行，并新建独立 `acceptance.json`，保存 `run_id / summary_hash / accepted / acceptance_type / accepted_at / notes`。Acceptance 不修改 `run.json`、responses、judgments、summary 或其他旧 evidence；已有 acceptance 也不会被覆盖。

`reference-status --run-dir <run-dir>` 动态读取并校验 `run.json / summary.json / acceptance.json / provider provenance`，输出 Execution、Behavioral、Reference Quality、Acceptance 与 Effective Reference Qualification。它是只读 derived view；acceptance 后仍不回写 immutable summary。若 acceptance 的 summary hash 不匹配，命令直接报错。

## Artifact、usage 与质量等级

正式目录包含 `run.json`、prepared/eval/runtime/source snapshots、append-only responses/judgments、summary，以及人工接受后可选的 `acceptance.json`。

Target / Judge usage 分别聚合 `input_tokens / output_tokens / reasoning_tokens / cached_tokens`。可选成本只使用 local profile 的 input/output per-million price。

- Level A：official verified endpoint、requested known、reported compatible、model status MATCHED、PURE_API、无 silent failover；
- Level B：declared relay 或 user-reported/manual，但身份与执行证据明确；
- Level C：unverified relay、reported model missing/multiple/mismatch、test double 或证据不足。

`REFERENCE_ELIGIBLE / PROVISIONAL / NOT_ELIGIBLE` 与 Behavioral PASS/FAIL 分离。Model mismatch/multiple、dirty worktree 或未完成执行不能成为正式 eligible reference。

## Historical reference

`model_evals/results/v1.6.0/chatgpt_project/baseline-manual-20260825-01` 已作为 frozen historical reference evidence 由 Git 跟踪；9 个既有 artifact 保持字节级只读，`baseline:false` 不变。它不是自动 Gold baseline。Schema v2 继续由当前 validator 只读兼容，新 schema v3 不回填或改写历史文件。

所有仓库自动测试均使用 fake provider、mock HTTP 或 fixture，不调用 OpenAI、Moonshot、Kimi、relay 或其他付费 endpoint。直接运行 unittest 时使用 `python -B -m unittest ...`；CI 已使用 `-B`，`validate_skill.py` 也在加载测试前设置 `sys.dont_write_bytecode=True`。Validator 保持只读并继续把已有 `__pycache__ / .pyc` 判为污染，不会自动删除工作区内容。
