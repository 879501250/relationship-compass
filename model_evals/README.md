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
| `openai_compatible_chat` | Chat Completions-compatible JSON | Google、DeepSeek、Moonshot 官方 compatible endpoint 或第三方 relay。 |
| `chatgpt_web_manual` | manual copy/paste | 手工 Target、Judge 或显式 fallback。 |

Target 和 Judge 各自独立记录 `provider_identity` 与 `model_identity`。Model status 为 `MATCHED / MISSING / MISMATCH / MULTIPLE / USER_REPORTED / UNVERIFIED`。API `reported_model` 只来自 provider response；ChatGPT Web 的标签保存为 `user_reported_model`，其 `requested_model` / `reported_model` 均为 `null`。

正式模式默认禁止 silent model failover：direct 或 relay 返回与 requested alias 不同的 model 会产生 `MODEL_IDENTITY_MISMATCH`，不会成为成功 response。Relay 不返回 model 可以继续执行，但 status 为 `MISSING`，Reference Quality 降级。

Endpoint provenance：

- `verified_direct`：adapter、vendor 与内置官方 origin 同时匹配；
- `declared_relay`：relay 声明 upstream，但仓库不能独立验证；
- `unverified_relay`：upstream 身份证据不足；
- `user_reported`：ChatGPT Web / manual 标签由操作者报告。

requested alias 相同不等于真实 upstream 可验证。Relay 可能动态路由、改写 system message、忽略参数、降级模型或不返回真实 model identity；其 Behavioral Reference 仍有价值，但 provenance 必须诚实记录。

### Official Provider Provenance Registry

集中 registry 按 `transport + canonical vendor + exact normalized origin` 联合匹配；这里的 transport 指 `provider_identity.transport` / manifest `protocol`，不是 vendor 名称。当前只登记以下组合：

| Transport | Canonical vendor | Official HTTPS origin |
| --- | --- | --- |
| `openai_responses` | `OpenAI` | `https://api.openai.com` |
| `openai_compatible_chat` | `Moonshot AI` | `https://api.moonshot.cn` |
| `openai_compatible_chat` | `Moonshot AI` | `https://api.moonshot.ai` |
| `openai_compatible_chat` | `Google` | `https://generativelanguage.googleapis.com` |
| `openai_compatible_chat` | `DeepSeek` | `https://api.deepseek.com` |

Origin 来自实际配置 URL 的 scheme + authority，小写归一化后精确匹配；不包含 `/v1` 等 path。HTTP、相似域名、子域名、额外端口、错误 vendor 或错误 transport 均不能通过，不使用 hostname contains / endswith。OpenAI 保留原有 Responses 官方支持，本轮未额外登记 OpenAI Chat；Anthropic 官方 Messages adapter 未实现，经 relay 使用 Anthropic 仍是 `declared_relay`。

Moonshot 中国 `.cn` 与国际 `.ai` origin 在三条件匹配时均为 `verified_direct`；国际 base URL 见[官方 Quickstart](https://platform.kimi.ai/docs/overview)。单个 Behavioral Reference run 必须显式固定一个 endpoint，runner 不做 automatic region failover：失败只在原 endpoint retry/resume。CN ↔ Global 改动需要新 run，即使 vendor/model 相同，compare 也至少为 `PARTIALLY_COMPARABLE`。

新配置未显式指定 provenance 时，命中上述组合得到 `verified_direct / endpoint_verified=true`；否则有 vendor 声明时为 `declared_relay`，无声明时为 `unverified_relay`。显式 `verified_direct` 必须命中 registry；显式保守声明 `declared_relay / unverified_relay` 不会被强行升级。OpenAI Responses 的既有官方默认 vendor 推断保持不变。

Registry 验证的是**配置的接入路径**，不是模型权重版本或 capability：Google 模型经第三方 relay 仍不算 Google 官方直连；即使 requested/reported model 相同也不会升级。官方 endpoint 返回不同 model 时，`endpoint_verified=true` 与 `model_identity.status=MISMATCH` 可以同时存在，严格模式仍报 `MODEL_IDENTITY_MISMATCH`。

Google 使用 [官方 OpenAI compatibility 文档](https://ai.google.dev/gemini-api/docs/openai)中的完整 base URL `https://generativelanguage.googleapis.com/v1beta/openai/`；DeepSeek 使用[官方 Chat/JSON 文档](https://api-docs.deepseek.com/guides/json_mode/)中的 base URL `https://api.deepseek.com`。本轮仅核对文档与本地测试，没有调用真实模型 API。

Registry 扩展只影响新配置。历史 Google run 若记录为 `declared_relay / endpoint_verified=false`，仍按原始 evidence 校验和派生 Reference Quality；不会重写 run、response、judgment、summary、acceptance，也不会自动升级旧 reference。

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

### Purpose-oriented example profiles

| Profile | 用途与配置边界 |
| --- | --- |
| `validation-gemini` | Google 官方开发验证；`GEMINI_API_KEY / GEMINI_TARGET_MODEL / GEMINI_JUDGE_MODEL`，Target medium + 普通文本，Judge high + strict JSON schema，`max_tokens`。 |
| `reference-target-relay-openai` | 正式 Target 候选；endpoint/key/model 分别读取 `RELATIONSHIP_EVAL_TARGET_BASE_URL / RELATIONSHIP_EVAL_TARGET_API_KEY / RELATIONSHIP_EVAL_TARGET_MODEL`，upstream 声明 OpenAI，但保持 `declared_relay`。 |
| `reference-judge-kimi-official` | Moonshot 官方 Judge 候选；`MOONSHOT_API_KEY / MOONSHOT_JUDGE_MODEL`，`json_object`、`thinking=disabled`、`max_completion_tokens=4096`、`max_retries=2`。 |
| `reference-judge-deepseek-official` | DeepSeek 官方 Judge 候选；`DEEPSEEK_API_KEY / DEEPSEEK_JUDGE_MODEL`，保守 text JSON fallback。 |
| `reference-judge-gemini-official` | Google 官方 Judge 候选或备用；`GEMINI_API_KEY / GEMINI_JUDGE_MODEL`，high + strict JSON schema。 |

`reference-judge-kimi-official` 默认保留 `https://api.moonshot.cn/v1`。用户可在自己的 local profile 中将 `base_url` 显式改为 `https://api.moonshot.ai/v1`；如需环境变量选择，删除该静态 `base_url`，改用 `"base_url_env": "MOONSHOT_BASE_URL"` 并设置对应变量（这是显式配置，不是自动 fallback）。也可用 CLI `--base-url` 覆盖。两个平台的账号、key 和模型可用性需分别核对。本轮不修改私有 local profile。

仅 `validation-gemini` 示例设为 `max_retries=4 / timeout_seconds=120`，用于用户报告的暂时性 500/503 容量错误；全局与其他 formal reference 示例仍沿用原默认值。两个 Gemini 示例均保持官方 provenance；旧 local Gemini profile 如仍显式写 `declared_relay`，请用户自行改为 `verified_direct` 或删除显式 provenance 让 registry 识别，仅用于后续新运行。旧 smoke/artifact 保留原样，不重写或升级。

Gemini 配置依据**用户已报告的真实 smoke evidence**：同一 `gemini-3.7-flash` requested/reported model 为 MATCHED，Target medium、Judge high + strict_json_schema、`max_tokens` 已通过现有 adapter。本轮未重复 smoke；该结果不保证其他 Gemini alias、参数组合或未来服务版本也受支持，示例通过 model_env 选择具体模型。

`reference-judge-kimi-official` 的 Judge payload 使用 `json_object`（`response_format: {"type": "json_object"}`）、`thinking=disabled` 与 `max_completion_tokens=4096`；Markdown JSON fence 会在解析前规范化，HTTP 429 会遵守 `Retry-After` 并按 `max_retries=2` 有界重试。DeepSeek 仍使用 `text_json_fallback`，只要求文本返回后按 Judge schema 解析，不声称服务端保证 schema。两者均不声明 reasoning、temperature、top_p 或 seed。`max_tokens` 是其他 compatible profile 待 smoke 确认的最小 token 参数配置，不是已实测承诺；relay Target 的 token 参数也必须按实际服务核对。原 `moonshot-direct` 等兼容示例保留，不代表新增验证结论。正式使用任何 provider / model / capability 组合前都必须分别做 Target/Judge `provider-check` + `smoke`。

### Validation Run 与 Behavioral Reference Run

Validation Run 用于 runner smoke、provider compatibility、schema 校验和改动后的快速 runtime 回归。可在实际账户额度允许时用 Gemini 官方免费额度或低成本配置；是否免费以账户与服务当前政策为准。它不自动成为正式 Behavioral Reference。

**Gemini Validation PASS 不等于正式 OpenAI relay Target PASS**：它只能证明当前基础设施和 runtime 能沿 Gemini 路径执行；不同 Target/model/execution identity 必须独立测试。

Behavioral Reference Run 用于产品版本回归、长期比较和 Core/Stress 行为测量，要求固定 Target/模型、固定 Judge/模型、完整 provenance、固定 Eval Identity、Git clean、完整执行、Human Audit 与 Human Acceptance。`validation-* / reference-*` 只是示例用途名称，不参与 runner qualification 判断，也不绕过既有质量或 acceptance 规则。

推荐长期流程：

- 开发验证：Gemini Official → 两个 role 各自 provider-check → Target smoke → Judge smoke → 必要时完整 30-case validation。
- 正式 reference：固定 Target provider/model → 固定 cross-vendor Judge → 两个 role 各自 provider-check + smoke → 30-case run → Report/Compare → Human Audit → Accept Reference。

例如 relay OpenAI Target 搭配 Kimi official Judge，不合适时再单独测试 DeepSeek official 或 Gemini official。跨厂商只是降低同模型自评相关性风险的建议，不禁止 Target == Judge；保留 `CORRELATED_JUDGE_RISK / TARGET_JUDGE_IDENTITY_CORRELATED` 警告。备用/Secondary Judge 指独立人工安排的另一轮评估，本轮不提供 Multi-Judge 编排、投票或共识分数。Human Audit 应检查全部 FAIL、抽查 PASS，并复核 disagreement；profile 名称和单次 30/30 PASS 均不能代替人工接受。

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
