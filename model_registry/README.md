# Model Registry Foundation

Registry 只有四种定义：

- **Vendor**：从哪里调用模型 API；Base URL 是 Vendor 的内部属性。
- **Model Family**：是什么模型系列及具体 model ID；Base URL 以 Family 作为支持粒度，单模型 override 仅用于例外。
- **Credential**：使用哪个认证身份；只保存 `env` 或 `secret_ref`，从不保存密钥。
- **Preset**：常用的无角色运行组合；Target/Judge 是未来 Run 的角色，不属于 Registry。

所有 `.yaml` 使用 JSON-compatible YAML，且只能由 `eval_console.model_registry` 读取。初始化时 `ModelRegistry.validate()` 会检查所有跨文档引用、Base URL 默认和协议规则，并拒绝任何 secret-bearing field。

Resolver 输出 `ResolvedModelRuntime`。Preset 和未来 Run 只使用语义参数，例如 Kimi 的 `max_output_tokens`；Vendor/Base URL 的 `parameter_mapping` 再将它映射到 wire 参数 `max_completion_tokens`。能力由 Model Family 默认值、具体 Model、Vendor 和 Base URL 依次合并；未知、未支持或不在允许范围内的语义参数都会失败。

当一个 Vendor 的多个 Base URL 同时支持同一 Model Family 时，必须由唯一 `default_for` 决定默认值；否则调用方必须显式选择 `base_url_id`，Resolver 返回 `AMBIGUOUS_BASE_URL`，绝不依赖文件顺序。
