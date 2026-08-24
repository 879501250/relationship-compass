# Changelog

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
