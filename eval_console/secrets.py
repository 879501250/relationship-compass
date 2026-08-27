"""Local, non-serialized secret resolution for the interactive Console."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretResolver:
    """Resolve session, OS, then `.env.local` secrets without revealing values."""

    def __init__(self, local_path: Path) -> None:
        self.local_path = local_path
        self._session: dict[str, str] = {}
        self._local = self._read_local()

    def get(self, name: str) -> str | None:
        self._validate_name(name)
        return self._session.get(name) or os.environ.get(name) or self._local.get(name)

    def has(self, name: str | None) -> bool:
        return bool(name and self.get(name))

    def set_session(self, name: str, value: str) -> None:
        self._validate_name(name)
        if not value.strip():
            raise ValueError("API key cannot be empty.")
        self._session[name] = value.strip()
        os.environ[name] = value.strip()

    def save_local(self, name: str, value: str) -> None:
        self.set_session(name, value)
        self._local[name] = value.strip()
        self.local_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = "".join(f"{key}={item}\n" for key, item in sorted(self._local.items()))
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=self.local_path.parent, delete=False
        ) as handle:
            handle.write(rendered)
            temporary = Path(handle.name)
        temporary.replace(self.local_path)

    def prepare_environment(self) -> None:
        for name, value in self._local.items():
            os.environ.setdefault(name, value)
        os.environ.update(self._session)

    def _read_local(self) -> dict[str, str]:
        if not self.local_path.is_file():
            return {}
        values: dict[str, str] = {}
        for raw_line in self.local_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            name, separator, value = line.partition("=")
            if not separator or not ENV_NAME.fullmatch(name):
                continue
            values[name] = value
        return values

    @staticmethod
    def _validate_name(name: str) -> None:
        if not ENV_NAME.fullmatch(name):
            raise ValueError("Secret name must be a valid environment variable name.")
