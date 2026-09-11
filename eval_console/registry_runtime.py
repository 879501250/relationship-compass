"""Secret-aware runtime adapter between the pure Model Registry and providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from .credential_store import CredentialSecretStore, LocalFileCredentialSecretStore
from .model_registry import Credential, ModelRegistry, ModelRegistryError, ResolvedModelRuntime
from .runner_adapter import runner


class RegistryRuntimeError(ValueError):
    """A Registry runtime cannot be safely constructed for execution."""


@dataclass(frozen=True)
class ResolvedRegistryRuntime:
    """A resolved runtime plus its transient token; never serialize this object."""

    runtime: ResolvedModelRuntime
    token: str
    credential_source: str
    identity_snapshot: Mapping[str, Any]
    audit_snapshot: Mapping[str, Any]
    runtime_hash: str

    @property
    def snapshot(self) -> Mapping[str, Any]:
        """Backward-compatible name for the persisted, secret-free audit view."""
        return self.audit_snapshot

    def __repr__(self) -> str:
        return f"ResolvedRegistryRuntime(preset_id={self.runtime.preset_id!r}, token='********')"


def default_registry_root(project_root: Path) -> Path:
    return project_root / ".eval_console" / "model_registry"


def default_credential_store_path(project_root: Path) -> Path:
    return project_root / ".eval_console" / "credentials.secrets.json"


class RegistryRuntimeResolver:
    """Resolve a preset, then obtain exactly one credential at execution time."""

    def __init__(
        self,
        registry: ModelRegistry,
        secret_store: CredentialSecretStore,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.registry = registry
        self.secret_store = secret_store
        self.environ = environ if environ is not None else os.environ

    @classmethod
    def for_project(
        cls,
        project_root: Path,
        *,
        registry_root: Path | None = None,
        credential_store_path: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "RegistryRuntimeResolver":
        registry = ModelRegistry(
            user_root=registry_root or default_registry_root(project_root)
        )
        return cls(
            registry,
            LocalFileCredentialSecretStore(
                credential_store_path or default_credential_store_path(project_root)
            ),
            environ=environ,
        )

    def resolve_preset(
        self, preset_id: str, *, model_id: str | None = None, context: str = "judge"
    ) -> ResolvedRegistryRuntime:
        try:
            runtime = self.registry.resolve(
                preset_id=preset_id,
                model_id=model_id,
                semantic_parameters=self._context_parameters(preset_id, model_id, context),
            )
        except ModelRegistryError as error:
            raise RegistryRuntimeError(str(error)) from error
        credential = self.registry.credentials[runtime.credential_id]
        if credential.status == "disabled":
            raise RegistryRuntimeError(f"Credential '{credential.id}' 已被禁用。")
        if credential.status == "expired":
            raise RegistryRuntimeError(f"Credential '{credential.id}' 已标记为过期。")
        token, source = self._resolve_token(credential)
        identity = runtime_identity_snapshot(runtime)
        audit = runtime_audit_snapshot(runtime, credential_source=source)
        return ResolvedRegistryRuntime(
            runtime=runtime,
            token=token,
            credential_source=source,
            identity_snapshot=identity,
            audit_snapshot=audit,
            runtime_hash=_snapshot_hash(identity),
        )

    def assess_preset_readiness(self, preset_id: str, *, context: str = "target") -> "PresetReadiness":
        """Check Registry, credentials and provider construction without HTTP."""
        try:
            runtime = self.registry.resolve(
                preset_id=preset_id,
                semantic_parameters=self._context_parameters(preset_id, None, context),
            )
        except (ModelRegistryError, RegistryRuntimeError) as error:
            return PresetReadiness(preset_id, False, False, False, False, False, False, (), (format_preset_readiness_failure(preset_id, str(error)),))
        credential = self.registry.credentials[runtime.credential_id]
        if credential.status == "disabled":
            return PresetReadiness(preset_id, True, False, False, True, False, False, (), (format_preset_readiness_failure(preset_id, f"Credential '{credential.id}' 已被禁用。"),))
        if credential.status == "expired":
            return PresetReadiness(preset_id, True, False, True, True, False, False, (), (format_preset_readiness_failure(preset_id, f"Credential '{credential.id}' 已标记为过期。"),))
        expired = credential.expires_at is not None and date.fromisoformat(credential.expires_at) < date.today()
        protocol_supported = runtime.protocol in RegistryProviderFactory.SUPPORTED_PROTOCOLS
        if expired:
            return PresetReadiness(preset_id, True, False, True, protocol_supported, False, False, (), (format_preset_readiness_failure(preset_id, f"Credential '{credential.id}' 已过期。"),))
        try:
            binding = self.resolve_preset(preset_id, context=context)
        except RegistryRuntimeError as error:
            return PresetReadiness(preset_id, True, False, False, protocol_supported, False, False, (), (format_preset_readiness_failure(preset_id, str(error)),))
        if not protocol_supported:
            return PresetReadiness(preset_id, True, True, False, False, False, False, (), (format_preset_readiness_failure(preset_id, f"当前 Eval Runner 不支持 {runtime.protocol}。"),))
        try:
            RegistryProviderFactory.create(binding, role=context)
        except (RegistryRuntimeError, runner.ModelEvalError) as error:
            return PresetReadiness(preset_id, True, True, False, True, False, False, (), (format_preset_readiness_failure(preset_id, str(error)),))
        return PresetReadiness(preset_id, True, True, False, True, True, True, (), ())

    def _context_parameters(
        self, preset_id: str, model_id: str | None, context: str
    ) -> Mapping[str, str | None]:
        if context not in {"target", "judge"}:
            raise RegistryRuntimeError(f"未知 Runtime Context：{context!r}")
        base = self.registry.resolve(
            preset_id=preset_id, model_id=model_id, semantic_parameters={"structured_output": None}
        )
        if context == "target":
            return {"structured_output": None}
        modes = base.resolved_capabilities.get("structured_output", {}).get("modes", ())
        if "strict_json_schema" in modes:
            return {"structured_output": "strict_json_schema"}
        if "json_object" in modes:
            return {"structured_output": "json_object"}
        supported = ", ".join(str(item) for item in modes) or "无"
        raise RegistryRuntimeError(
            f"无法执行 Judge Run：模型 {base.model_id} 不支持结构化输出；支持模式：{supported}。"
        )

    def _resolve_token(self, credential: Credential) -> tuple[str, str]:
        if credential.status == "disabled":
            raise RegistryRuntimeError(f"Credential '{credential.id}' 已被禁用。")
        if credential.status == "expired":
            raise RegistryRuntimeError(f"Credential '{credential.id}' 已标记为过期。")
        if credential.expires_at is not None and date.fromisoformat(credential.expires_at) < date.today():
            raise RegistryRuntimeError(f"Credential '{credential.id}' 已于 {credential.expires_at} 过期。")
        if credential.environment_variable is not None:
            token = self.environ.get(credential.environment_variable, "")
            if not token.strip():
                raise RegistryRuntimeError(
                    f"Credential '{credential.id}' 缺少环境变量 {credential.environment_variable}。"
                )
            return token, "environment"
        if credential.secret_reference == f"local:{credential.id}":
            token = self.secret_store.get(credential.id)
            if not token or not token.strip():
                raise RegistryRuntimeError(f"Credential '{credential.id}' 的本地令牌不可用。")
            return token, "local"
        raise RegistryRuntimeError(f"Credential '{credential.id}' 的令牌引用不受支持。")


class RegistryProviderFactory:
    """Build existing HTTP providers from an already-resolved Registry runtime."""

    SUPPORTED_PROTOCOLS = frozenset({"openai_responses", "openai_compatible_chat"})

    @classmethod
    def create(cls, binding: ResolvedRegistryRuntime, *, role: str) -> Any:
        runtime = binding.runtime
        if role not in {"target", "judge"}:
            raise RegistryRuntimeError(f"未知运行角色：{role!r}")
        if runtime.protocol not in cls.SUPPORTED_PROTOCOLS:
            raise RegistryRuntimeError(
                f"Preset '{runtime.preset_id}' 使用 {runtime.protocol!r}；Eval Console 尚不支持该协议。"
            )
        common = _provider_arguments(runtime, binding.token, role)
        if runtime.protocol == "openai_responses":
            return runner.OpenAIResponsesProvider(**common)
        return runner.OpenAICompatibleChatProvider(**common)


@dataclass(frozen=True)
class PresetReadiness:
    preset_id: str
    registry_valid: bool
    credential_ready: bool
    credential_expired: bool
    protocol_supported: bool
    parameter_mapping_supported: bool
    provider_constructable: bool
    warnings: tuple[str, ...]
    blocking_errors: tuple[str, ...]

    @property
    def runnable(self) -> bool:
        return self.registry_valid and self.credential_ready and self.protocol_supported and self.parameter_mapping_supported and self.provider_constructable


def format_preset_readiness_failure(preset_id: str, message: str) -> str:
    """Render capability failures as an actionable, secret-free readiness report."""
    result = ["Preset Readiness Failed", f"Preset: {preset_id}"]
    mismatch = re.search(
        r"semantic parameter 'structured_output' value ('[^']+') is not allowed; expected one of (.+)",
        message,
    )
    if mismatch:
        result.extend([
            "Reason: Capability mismatch",
            f"Required: {mismatch.group(1)}",
            f"Supported: {mismatch.group(2)}",
        ])
    else:
        result.extend(["Reason:", message])
    return "\n".join(result)


def runtime_identity_snapshot(runtime: ResolvedModelRuntime) -> dict[str, Any]:
    """Hash input: every provider/request behavior field, never a token/source."""
    return {
        "vendor_id": runtime.vendor_id,
        "base_url_id": runtime.base_url_id,
        "endpoint": sanitize_url(runtime.base_url),
        "protocol": runtime.protocol,
        "model_family_id": runtime.model_family_id,
        "model_id": runtime.model_id,
        "requested_model_name": runtime.requested_model_name,
        "api_model_name": runtime.api_model_name,
        "credential_id": runtime.credential_id,
        "semantic_parameters": _plain(runtime.semantic_parameters),
        "wire_parameters": _plain(runtime.wire_parameters),
        "transport_defaults": _plain(runtime.transport_defaults),
        "resolved_capabilities": _plain(runtime.resolved_capabilities),
    }


def runtime_audit_snapshot(runtime: ResolvedModelRuntime, *, credential_source: str) -> dict[str, Any]:
    """Artifact-facing provenance view; credential source is audit-only."""
    return {
        "preset_id": runtime.preset_id,
        "vendor_id": runtime.vendor_id,
        "vendor_name": runtime.vendor_name,
        "base_url_id": runtime.base_url_id,
        "endpoint": sanitize_url(runtime.base_url),
        "protocol": runtime.protocol,
        "model_family_id": runtime.model_family_id,
        "model_id": runtime.model_id,
        "requested_model_name": runtime.requested_model_name,
        "api_model_name": runtime.api_model_name,
        "credential_id": runtime.credential_id,
        "credential_source": credential_source,
        "identity": runtime_identity_snapshot(runtime),
        "provenance": _plain(runtime.provenance),
    }


def runtime_snapshot(runtime: ResolvedModelRuntime, *, credential_source: str) -> dict[str, Any]:
    """Compatibility alias for callers that persist the audit snapshot."""
    return runtime_audit_snapshot(runtime, credential_source=credential_source)


def sanitize_url(value: str) -> str:
    """Remove credentials, query and fragment before any Console display/write."""
    parts = urlsplit(value)
    host = parts.hostname or ""
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _provider_arguments(runtime: ResolvedModelRuntime, token: str, role: str) -> dict[str, Any]:
    wire = dict(runtime.wire_parameters)
    defaults = dict(runtime.transport_defaults)
    capabilities = _provider_capabilities(runtime, wire)
    max_output = _wire_value(wire, "max_output_tokens", "max_completion_tokens", "max_tokens")
    if not isinstance(max_output, int) or isinstance(max_output, bool):
        max_output = 2400 if role == "judge" else 1200
    structured = wire.get("structured_output")
    if structured is not None and not isinstance(structured, str):
        raise RegistryRuntimeError("Registry structured_output 必须为字符串。")
    thinking = wire.get("thinking")
    if thinking is not None and not isinstance(thinking, str):
        raise RegistryRuntimeError("Registry thinking 必须为字符串。")
    reasoning = wire.get("reasoning_effort")
    if reasoning is not None and not isinstance(reasoning, str):
        raise RegistryRuntimeError("Registry reasoning_effort 必须为字符串。")
    return {
        "api_key": token,
        "model": runtime.api_model_name,
        "base_url": runtime.base_url,
        "endpoint_source": "model_registry",
        "declared_upstream_vendor": runtime.vendor_name,
        "provenance_type": None,
        "reasoning_effort": reasoning,
        "thinking": thinking,
        "structured_output_mode": structured,
        "structured_output_required": role == "judge",
        "capabilities": capabilities,
        "timeout_seconds": _positive_number(defaults.get("timeout_seconds"), 90.0),
        "max_retries": _retry_count(defaults.get("max_retries"), 1),
        "max_output_tokens": max_output,
        "temperature": wire.get("temperature"),
        "top_p": wire.get("top_p"),
        "seed": wire.get("seed"),
        "strict_model_identity": bool(defaults.get("strict_model_identity", True)),
        "input_cost_per_million": defaults.get("input_cost_per_million"),
        "output_cost_per_million": defaults.get("output_cost_per_million"),
    }


def _provider_capabilities(runtime: ResolvedModelRuntime, wire: Mapping[str, Any]) -> dict[str, Any]:
    capability = runtime.resolved_capabilities
    def rule(name: str) -> Mapping[str, Any]:
        value = capability.get(name, {})
        return value if isinstance(value, Mapping) else {}
    def allowed(name: str, field: str) -> list[Any]:
        value = rule(name).get(field, ())
        return list(value) if isinstance(value, (tuple, list)) else []
    protocol_default = "max_output_tokens" if runtime.protocol == "openai_responses" else "max_tokens"
    wire_max = next((key for key in ("max_output_tokens", "max_completion_tokens", "max_tokens") if key in wire), protocol_default)
    return {
        "reasoning_effort_supported": bool(rule("reasoning_effort").get("supported")),
        "allowed_reasoning_efforts": allowed("reasoning_effort", "allowed_values"),
        "structured_output_modes": allowed("structured_output", "modes") or ["strict_json_schema", "json_object", "text_json_fallback"],
        "temperature_supported": bool(rule("temperature").get("supported")),
        "top_p_supported": bool(rule("top_p").get("supported")),
        "seed_supported": bool(rule("seed").get("supported")),
        "max_output_tokens_parameter": wire_max,
        "thinking_supported": bool(rule("thinking").get("supported")),
        "allowed_thinking_types": allowed("thinking", "allowed_values"),
        "thinking_parameter": "thinking",
    }


def _wire_value(values: Mapping[str, Any], *keys: str) -> Any:
    return next((values[key] for key in keys if key in values), None)


def _positive_number(value: Any, default: float) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else default


def _retry_count(value: Any, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 4 else default


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
