# Model Eval Results

此目录用于保存实际执行的 Model Behavioral Eval 结果。当前仓库暂未提交正式 baseline。

`python scripts/run_model_evals.py validate` 只验证案例与 rubric 定义，不执行模型，也不等价于模型行为测试通过。

只有同时具备以下材料时，才应创建正式 baseline：

- 每个 case 的逐字模型输出；
- 完整运行配置与被测 Skill revision；
- 独立 judge 身份及每项 rubric 的明确判断；
- 可复核的 responses、judgments 和汇总结果。

未来结果文件应采用能说明执行日期或被测 revision 的名称，并在内容中记录模型、配置和评审信息。仅有 `NOT RUN` 状态时不要创建 baseline 文件。
