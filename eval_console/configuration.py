"""Safe local provider-profile creation and small interactive edits."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .runner_adapter import runner


def create_local_profile_config(example_path: Path, destination: Path) -> bool:
    """Copy the repository template once; never overwrite local configuration."""
    if destination.exists():
        return False
    if not example_path.is_file():
        raise ValueError(f"未找到 Provider 配置模板：{example_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=destination.parent, delete=False) as handle:
        temporary = Path(handle.name)
    shutil.copyfile(example_path, temporary)
    temporary.replace(destination)
    return True


def role_configuration(path: Path, profile_name: str, role: str) -> dict[str, Any]:
    profile = runner.load_provider_profile(path, profile_name, role)
    return {
        "provider": profile.get("provider"),
        "model": profile.get("model"),
        "model_env": profile.get("model_env"),
        "base_url": profile.get("base_url"),
        "base_url_env": profile.get("base_url_env"),
        "api_key_env": profile.get("api_key_env"),
        "structured_output_mode": profile.get("structured_output_mode"),
    }


def update_role_configuration(
    path: Path,
    profile_name: str,
    role: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> None:
    data = runner.load_json_yaml(path)
    profiles = data.get("profiles")
    if not isinstance(profiles, dict) or not isinstance(profiles.get(profile_name), dict):
        raise ValueError(f"未找到 Profile：{profile_name!r}。")
    profile = profiles[profile_name]
    role_data = profile.setdefault(role, {})
    if not isinstance(role_data, dict):
        raise ValueError(f"Profile {profile_name!r} 的 {role} 配置段无效。")
    if model is not None:
        role_data["model"] = _required(model, "模型名称")
    if base_url is not None:
        role_data["base_url"] = validate_base_url(base_url)
        role_data.pop("base_url_env", None)
    _write_json_atomically(path, data)


def create_profile(
    path: Path,
    *,
    name: str,
    provider: str,
    role: str,
    model: str,
    base_url: str,
) -> str:
    data = runner.load_json_yaml(path)
    profiles = data.setdefault("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("Provider 配置中的 profiles 段无效。")
    normalized = _required(name, "Profile name")
    if normalized in profiles:
        raise ValueError(f"Profile {normalized!r} 已存在。")
    if provider not in runner.PROVIDER_TYPES - {"chatgpt_web_manual"}:
        raise ValueError(f"不支持的 Provider 类型：{provider}")
    env_prefix = "".join(char if char.isalnum() else "_" for char in normalized.upper())
    structured_mode = "strict_json_schema" if provider == "openai_responses" else "json_object"
    capabilities = {
        "reasoning_effort_supported": provider == "openai_responses",
        "allowed_reasoning_efforts": ["none", "minimal", "low", "medium", "high"]
        if provider == "openai_responses"
        else [],
        "structured_output_modes": ["strict_json_schema", "json_object", "text_json_fallback"]
        if provider == "openai_responses"
        else ["json_object", "text_json_fallback"],
        "temperature_supported": provider == "openai_responses",
        "top_p_supported": provider == "openai_responses",
        "seed_supported": False,
        "max_output_tokens_parameter": "max_output_tokens"
        if provider == "openai_responses"
        else "max_tokens",
    }
    profiles[normalized] = {
        "provider": provider,
        "api_key_env": f"RELATIONSHIP_EVAL_{env_prefix}_API_KEY",
        "base_url": validate_base_url(base_url),
        "declared_upstream_vendor": "configure-locally",
        "provenance_type": "declared_relay",
        "strict_model_identity": True,
        "capabilities": capabilities,
        role: {
            "model": _required(model, "模型名称"),
            **({"structured_output_mode": structured_mode} if role == "judge" else {}),
        },
    }
    _write_json_atomically(path, data)
    return normalized


def validate_base_url(value: str) -> str:
    candidate = _required(value, "API Base URL")
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("API Base URL 格式无效。请输入完整的 HTTPS 地址，例如：https://api.example.com/v1")
    return candidate


def _required(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label}不能为空。")
    return normalized


def _write_json_atomically(path: Path, value: dict[str, Any]) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(rendered)
        temporary = Path(handle.name)
    temporary.replace(path)
