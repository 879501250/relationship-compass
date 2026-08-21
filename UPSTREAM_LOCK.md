# Upstream 更新规范

`UPSTREAM_LOCK.json` 只记录可公开复现的仓库元数据：repository、commit、branch 或 tag、copied_at。禁止写入本机绝对路径、用户目录或未提交工作树状态。

## 更新 upstream

1. 在独立临时 clone 中获取 upstream；不要直接用含本地修改的源工作树覆盖 personal。
2. 记录当前 lock commit 和候选新 commit，确认两者都是 40 位提交 ID。
3. 检查候选提交的分支或 tag，阅读变更日志和安全相关改动。
4. 只把明确需要的增量合入 personal；保留个人版的 slug、Memory namespace、对象隔离、shared policy 和测试。
5. 运行完整验证并人工检查差异。
6. 只有合并和验证完成后，才更新 `UPSTREAM_LOCK.json` 的 commit、branch/tag 与 copied_at。

## 比较差异

对每个来源分别执行，避免把 original 和 warm-fork 混成一次无法审计的 diff：

```text
git fetch origin
git diff --stat <locked-commit>..<candidate-commit>
git diff <locked-commit>..<candidate-commit> -- SKILL.md agents references scripts
```

先看目录级统计，再检查入口、Memory、安全规则和 references。不要复制 `.git`、IDE 配置、数据库、缓存或未提交文件。

## 合并规则

- 优先 cherry-pick 或逐文件应用小批次差异，每批都可回滚和测试。
- upstream 的通用知识与工程修复可以吸收；personal 的表达成长、对象隔离和双系统约束不能被覆盖。
- 出现 schema 变化时，先写兼容迁移和测试，再处理文档。
- 出现政策冲突时，以 `shared/CORE_POLICY.md` 和 `shared/FACT_HYPOTHESIS_POLICY.md` 为个人版底线。
- 源仓库工作树非 clean 时只报告，不恢复、不覆盖、不把未提交内容写入 lock。

## 更新后验证

```text
python scripts/validate_skill.py
python scripts/run_tests.py
```

Model behavioral eval 不属于自动 CI 通过项；需要真实加载 Skill 的模型输出和明确 judge 结果。

<!-- Modified by AI on 2026-08-21 14:47:55 -->
