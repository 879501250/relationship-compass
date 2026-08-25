# Changelog

## Unreleased

### Added

- ChatGPT Project 手工 Target/Judge 导出与可分段导入路径，并与 API 路径共享 artifact schema、report 和 validator。
- 运行元数据记录动态 pack version、git dirty state、runner revision 与执行环境。
- Behavioral run 保存 prepared、eval definition、profile-specific runtime 与 runner/Skill source snapshots，用于离线重算 fingerprint provenance。
- `api_canonical` 与 `chatgpt_project` runtime profile，以及按 profile 隔离的结果目录和 baseline identity。

### Changed

- Model Eval 结果目录改为从当前 Knowledge pack 动态派生，报告明确区分行为失败、provider/judge 错误与未评估 case。
- Artifact validation 改为交叉验证 fingerprint、case/criterion 集合、responses、judgments、计数和 summary 派生结果。
- Run lifecycle 改为 `PREPARED → TARGET_PARTIAL/TARGET_COMPLETE → JUDGE_PARTIAL → COMPLETED`，失败单独为 `FAILED`；顶层 `completed_at` 仅用于终态。
- `bundle_hash` 统一表示 canonical prepared + runtime snapshot；移除与其语义重复的 `runtime_bundle_hash`。

### Fixed

- Target 输入不再泄漏 case id、reply/analysis mode 或 rubric criterion。
- 重新生成过期的 ChatGPT Knowledge pack，并将 `.work/` 排除出版本控制。

## 1.6.0

### Added

- Current Style、Target Style 与运行时 Comfortable Range 的正式分工。
- One Small Stretch、User Comfort 和按当前请求选择的轻量 Assistance Strategy。

### Changed

- E1–E5 收口为单条消息的表达强度，不再按 Stage、Trend 或积极反馈机械升级。
- 用户草稿优先确认或微调；Actual Send 比 AI Draft 更优先用于风格学习，未照抄不视为失败。
- Review 同时考虑 Authenticity、Clarity、Context Fit 与 Outcome，默认只保留一个 Growth Target。

### Fixed

- 移除线性 G0–G5 成长路径和 Stage→当前表达强度边界表，避免把表达成长误写成等级或人格转换。

## 1.5.0

### Added

- 决策特定的证据充分性判断，以及缺失上下文的高信息量追问规则。
- 回答吸收、状态重算与充分后立即停止的 Guided Interview 闭环。

### Changed

- 简单回复、明确边界和安全行动在证据已足够时直接完成，不因仍有未知而自动追问。
- Contract Eval 与 Model Eval 覆盖无需访谈、baseline 缺口、回答后停止和边界提前停止。

### Fixed

- 明确区分助手向用户补证据的 Guided Interview、聊天结构 `interview mode` 与 Conversation Hook。

## 1.4.0

### Added

- Evidence normalization、关键证据选择，以及 Stage + Recent Trend 双轴关系状态判断。
- 定性 Evidence Strength／Conflict 表达与 Evidence → Current Action 主路径。

### Changed

- 关系走势改为相对同一对象 baseline 判断，并要求多个观察点；单次晚回、短回复或热聊不再翻转 Stage／Trend。
- 明确边界优先于微弱积极信号，线上互动与现实投入可作为冲突证据同时呈现。
- Contract Eval 扩展 D.1 核心行为，Model Eval 保留 Phase C／C.1 baseline 并加入三类高价值关系分析场景。

### Fixed

- 统一 Review Mode 中 Stage、Recent Trend、Evidence Strength、Feedback Color、E 与 Memory confidence 的职责边界。

## 1.3.0

### Added

- 轻量 Serious Mode、基于语义功能的消息分段与个人事实安全优先级。

### Changed

- 简单回复默认输出一个首选和至多一句必要理由；多个版本仅用于明确请求或真实策略分支。
- 回复主路径统一融合请求深度、事实边界、口吻校准、continuation ownership 与自然结束判断。
- 成长目标统一为自然清晰表达、情绪理解、边界、互惠、节奏与独立决策，不塑造固定讨喜人设。

### Fixed

- Contract Eval 同时保护 Phase C 行为与基础关系判断，并校验每类全部关键 expectation。
- confirmed Memory 明确区分可相关复用的 user scope 与仅限当前对象/配对的 object、relationship scope。
- curated knowledge intake 使用能力名，不再让当前规范依赖历史版本号。

## 1.2.0

### Added

- 经用户同意、可撤销且按对象隔离的本地 Memory。
- 具备来源、审批、冲突处理和去重约束的 Knowledge 管理流程。
- 确定性 ChatGPT Project Knowledge 构建与隐私边界校验。
- Contract Eval 与人工判断 Model Eval 定义。

### Changed

- Skill、调用名称、Memory namespace 和安装目录统一使用 `relationship-compass`。
- 即时回复、关系判断、表达成长和复盘共享同一事实/假设与安全政策。
- Contract Eval 收敛为少量高价值行为类别。

### Fixed

- 正式中文 reference 路径及其引用保持一致，并增加异常编码文件名回归检查。
- 生成产物和校验器只依赖 canonical references 与 generated knowledge。

### Removed

- 一次性实现报告、重复 Knowledge 副本和无运行价值的文件标记。
