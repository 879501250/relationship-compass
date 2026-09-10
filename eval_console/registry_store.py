"""Transactional storage for user-managed Registry definitions."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Literal, Mapping

from .model_registry import DEFAULT_MODEL_REGISTRY_ROOT, ModelRegistry, RegistryValidationError


RegistryKind = Literal["vendors", "model_families", "credentials", "presets"]
KINDS: tuple[RegistryKind, ...] = ("vendors", "model_families", "credentials", "presets")


class RegistryStore:
    """Validates a complete candidate overlay before every atomic change."""

    def __init__(self, user_root: Path, *, builtin_root: Path = DEFAULT_MODEL_REGISTRY_ROOT) -> None:
        self.user_root, self.builtin_root = user_root, builtin_root

    def registry(self) -> ModelRegistry:
        return ModelRegistry(self.builtin_root, user_root=self.user_root)

    def registry_without_presets(self) -> ModelRegistry:
        """Load definitions needed to configure a preset without unrelated preset failures."""
        return self._isolated_registry()

    def registry_for_preset(self, identifier: str) -> ModelRegistry:
        """Load one preset with its dependencies, isolating unrelated preset documents."""
        return self._isolated_registry(identifier)

    def preset_ids(self) -> tuple[str, ...]:
        """List built-in and user preset ids even if a separate preset is malformed."""
        identifiers = set(ModelRegistry(self.builtin_root).presets)
        directory = self.user_root / "presets"
        for path in directory.glob("*.y*ml") if directory.is_dir() else ():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                identifiers.add(path.stem)
                continue
            value = document.get("id") if isinstance(document, dict) else None
            identifiers.add(value if isinstance(value, str) and value else path.stem)
        return tuple(sorted(identifiers))

    def create(self, kind: RegistryKind, document: Mapping[str, Any]) -> None:
        identifier = self._identifier(document)
        if self._builtin_has(kind, identifier):
            raise ValueError("内置定义不可直接覆盖；请使用新的 ID 创建用户定义。")
        target = self._path(kind, identifier)
        if target.exists(): raise ValueError(f"{identifier} 已存在。")
        self._commit_candidate(kind, identifier, dict(document), delete=False)

    def validate_create(self, kind: RegistryKind, document: Mapping[str, Any]) -> None:
        """Validate a create operation without writing the user overlay."""
        identifier = self._identifier(document)
        if self._builtin_has(kind, identifier):
            raise ValueError("内置定义不可直接覆盖；请使用新的 ID 创建用户定义。")
        if self._path(kind, identifier).exists(): raise ValueError(f"{identifier} 已存在。")
        self._validate_candidate(kind, identifier, dict(document), delete=False)

    def update(self, kind: RegistryKind, identifier: str, document: Mapping[str, Any]) -> None:
        if self._builtin_has(kind, identifier): raise ValueError("内置定义不可直接修改。")
        if self._identifier(document) != identifier: raise ValueError("对象 ID 创建后不可修改。")
        if not self._path(kind, identifier).is_file(): raise ValueError(f"未找到用户定义：{identifier}")
        self._commit_candidate(kind, identifier, dict(document), delete=False)

    def validate_update(self, kind: RegistryKind, identifier: str, document: Mapping[str, Any]) -> None:
        if self._builtin_has(kind, identifier): raise ValueError("内置定义不可直接修改。")
        if self._identifier(document) != identifier: raise ValueError("对象 ID 创建后不可修改。")
        if not self._path(kind, identifier).is_file(): raise ValueError(f"未找到用户定义：{identifier}")
        self._validate_candidate(kind, identifier, dict(document), delete=False)

    def delete(self, kind: RegistryKind, identifier: str) -> None:
        if self._builtin_has(kind, identifier): raise ValueError("内置定义不可直接删除。")
        target = self._path(kind, identifier)
        if not target.is_file(): raise ValueError(f"未找到用户定义：{identifier}")
        references = self.references(kind, identifier)
        if references: raise ValueError("该对象仍被引用：\n" + "\n".join(f"- {item}" for item in references))
        self._commit_candidate(kind, identifier, None, delete=True)

    def validate_delete(self, kind: RegistryKind, identifier: str) -> None:
        if self._builtin_has(kind, identifier): raise ValueError("内置定义不可直接删除。")
        if not self._path(kind, identifier).is_file(): raise ValueError(f"未找到用户定义：{identifier}")
        references = self.references(kind, identifier)
        if references: raise ValueError("该对象仍被引用：\n" + "\n".join(f"- {item}" for item in references))
        self._validate_candidate(kind, identifier, None, delete=True)

    def references(self, kind: RegistryKind, identifier: str) -> list[str]:
        registry = self.registry()
        if kind == "vendors":
            return [f"Credential: {item.id}" for item in registry.credentials.values() if item.vendor_id == identifier] + [f"Preset: {item.id}" for item in registry.presets.values() if item.vendor_id == identifier]
        if kind == "credentials":
            return [f"Preset: {item.id}" for item in registry.presets.values() if item.credential_id == identifier]
        if kind == "model_families":
            return [f"Preset: {item.id}" for item in registry.presets.values() if item.model_family_id == identifier] + [f"Vendor: {vendor.id} / Base URL: {url.id}" for vendor in registry.vendors.values() for url in vendor.base_urls if identifier in url.model_families]
        return []

    def credential_status(self, credential_id: str, *, today: date | None = None) -> str:
        credential = self.registry().credentials[credential_id]
        if credential.expires_at is None: return "未设置过期时间"
        remaining = (date.fromisoformat(credential.expires_at) - (today or date.today())).days
        if remaining < 0: return "已过期"
        return "即将过期" if remaining <= 30 else "有效"

    def read_user_document(self, kind: RegistryKind, identifier: str) -> dict[str, Any]:
        """Return a mutable metadata document without exposing built-in entries."""
        if self._builtin_has(kind, identifier): raise ValueError("内置定义不可直接修改。")
        path = self._path(kind, identifier)
        if not path.is_file(): raise ValueError(f"未找到用户定义：{identifier}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("用户 Registry 文档无法读取。") from error
        if not isinstance(value, dict): raise ValueError("用户 Registry 文档格式无效。")
        return deepcopy(value)

    def mark_credential_used(self, identifier: str, timestamp: str) -> None:
        """Persist usage metadata for user credentials only; never touch secrets."""
        document = self.read_user_document("credentials", identifier)
        document["last_used_at"] = timestamp
        self.update("credentials", identifier, document)

    def restore_builtin_model_definitions(self) -> None:
        """Discard only user Vendor/Model definitions; Credentials and Presets remain."""
        for kind in ("vendors", "model_families"):
            target = self.user_root / kind
            if target.is_dir():
                shutil.rmtree(target)

    def clear_user_configuration(self) -> None:
        """Remove the explicitly configured overlay after caller confirmation."""
        if self.user_root.is_dir():
            shutil.rmtree(self.user_root)

    def export_user_configuration(self, timestamp: str) -> Path:
        """Create a metadata-only Registry export; secrets live in a separate store."""
        destination = self.user_root.parent / f"registry-export-{timestamp}"
        if self.user_root.is_dir():
            shutil.copytree(self.user_root, destination)
        else:
            destination.mkdir(parents=True, exist_ok=False)
        return destination

    def _commit_candidate(self, kind: RegistryKind, identifier: str, document: dict[str, Any] | None, *, delete: bool) -> None:
        self._validate_candidate(kind, identifier, document, delete=delete)
        actual = self._path(kind, identifier)
        if delete:
            actual.unlink()
        else:
            actual.parent.mkdir(parents=True, exist_ok=True)
            _write_json_atomically(actual, document or {})

    def _validate_candidate(self, kind: RegistryKind, identifier: str, document: dict[str, Any] | None, *, delete: bool) -> None:
        with tempfile.TemporaryDirectory(prefix="relationship-compass-registry-") as raw:
            candidate = Path(raw) / "registry"
            if self.user_root.exists(): shutil.copytree(self.user_root, candidate)
            target = candidate / kind / f"{identifier}.yaml"
            if delete:
                target.unlink(missing_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                _write_json_atomically(target, document or {})
            if kind == "presets":
                self._remove_other_presets(candidate, identifier)
            elif kind == "credentials":
                self._remove_other_presets(candidate)
            ModelRegistry(self.builtin_root, user_root=candidate)

    def _isolated_registry(self, preset_id: str | None = None) -> ModelRegistry:
        with tempfile.TemporaryDirectory(prefix="relationship-compass-registry-") as raw:
            candidate = Path(raw) / "registry"
            if self.user_root.exists():
                shutil.copytree(self.user_root, candidate)
            self._remove_other_presets(candidate, preset_id)
            return ModelRegistry(self.builtin_root, user_root=candidate)

    @staticmethod
    def _remove_other_presets(root: Path, keep_id: str | None = None) -> None:
        directory = root / "presets"
        if not directory.is_dir():
            return
        for path in directory.glob("*.y*ml"):
            if keep_id is None or path.stem != keep_id:
                path.unlink()

    def _builtin_has(self, kind: RegistryKind, identifier: str) -> bool:
        registry = ModelRegistry(self.builtin_root)
        return identifier in getattr(registry, {"vendors": "vendors", "model_families": "model_families", "credentials": "credentials", "presets": "presets"}[kind])

    def _path(self, kind: RegistryKind, identifier: str) -> Path:
        return self.user_root / kind / f"{identifier}.yaml"

    @staticmethod
    def _identifier(document: Mapping[str, Any]) -> str:
        value = document.get("id")
        if not isinstance(value, str) or not value: raise ValueError("ID 不能为空。")
        return value


def _write_json_atomically(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name); handle.write(rendered); handle.flush(); os.fsync(handle.fileno())
    temporary.replace(path)
# Modified by AI on 2026-09-10 15:20:16
