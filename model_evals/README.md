# Model Behavioral Eval

这一层真实评估 Skill 输出，不等同于 `evals/` 的 contract eval。

## 三步流程

1. 验证案例和 rubric：

   ```bash
   python scripts/run_model_evals.py validate
   ```

2. 导出工作项，把每条 prompt 交给实际加载本 Skill 的模型运行，原样保存输出：

   ```bash
   python scripts/run_model_evals.py prepare --output work/model_eval_items.jsonl
   ```

3. 建立两个 JSONL 文件：

   - responses：每行 `{"case_id":"...","output":"模型原始输出"}`；
   - judgments：每行包含 `case_id`、非空 `judge`、`notes`，以及该案例全部 rubric 的布尔判断，例如 `{"case_id":"...","judge":"human","criteria":{"sendable_first":true}}`。

   汇总：

   ```bash
   python scripts/run_model_evals.py judge --responses work/responses.jsonl --judgments work/judgments.jsonl
   ```

Runner 只验证材料完整性并汇总明确判断，不会根据关键词伪装成行为测评，也不会自动调用模型。没有逐条真实输出和外部判断时，状态必须写作 `NOT RUN`，不能报告通过。
