"""Local-only secret storage for registry credential tokens."""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from pathlib import Path
import tempfile
from typing import Any


class CredentialSecretStore(ABC):
    """Secret operations deliberately separate from Registry metadata."""

    @abstractmethod
    def set(self, credential_id: str, token: str) -> None: ...

    @abstractmethod
    def get(self, credential_id: str) -> str | None: ...

    @abstractmethod
    def delete(self, credential_id: str) -> None: ...

    @abstractmethod
    def exists(self, credential_id: str) -> bool: ...

    @abstractmethod
    def list_ids(self) -> set[str]: ...


class LocalFileCredentialSecretStore(CredentialSecretStore):
    """Atomically stores local tokens without pretending to encrypt them."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def set(self, credential_id: str, token: str) -> None:
        if not isinstance(token, str) or not token.strip():
            raise ValueError("令牌不能为空。")
        data = self._read()
        data[credential_id] = {"token": token.strip()}
        self._write(data)

    def get(self, credential_id: str) -> str | None:
        value = self._read().get(credential_id)
        token = value.get("token") if isinstance(value, dict) else None
        return token if isinstance(token, str) else None

    def delete(self, credential_id: str) -> None:
        data = self._read()
        if credential_id in data:
            del data[credential_id]
            self._write(data)

    def exists(self, credential_id: str) -> bool:
        return self.get(credential_id) is not None

    def list_ids(self) -> set[str]:
        return set(self._read())

    def _read(self) -> dict[str, dict[str, str]]:
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("本地令牌存储无法读取。") from error
        if not isinstance(value, dict):
            raise ValueError("本地令牌存储格式无效。")
        return value

    def _write(self, value: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=self.path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            temporary.replace(self.path)
            if os.name != "nt":
                os.chmod(self.path, 0o600)
        except OSError as error:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise ValueError("无法保存本地令牌存储。") from error


def mask_token(token: str | None) -> str:
    """Never return a complete token, including for short values."""
    if not token or len(token) < 8:
        return "********"
    prefix = token[:3].rstrip("-")
    return f"{prefix}-****{token[-4:]}"
