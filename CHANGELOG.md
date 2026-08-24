# Changelog

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
