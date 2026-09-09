"""Logical transactions for credential metadata and local secret storage.

The Registry overlay and secret file are separate durable stores.  This module
is the only coordinator: callers build a fully validated draft, obtain a final
confirmation, and then call one of these methods.  Tokens never appear in
transaction errors or representations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .credential_store import CredentialSecretStore
from .registry_store import RegistryStore


class CredentialTransactionError(ValueError):
    """A coordinated credential operation could not reach a safe outcome."""


@dataclass(frozen=True)
class CredentialUpdateDraft:
    """In-memory only update intent; repr deliberately excludes token material."""

    document: Mapping[str, Any]
    replace_token: bool = False
    new_token: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.replace_token != (self.new_token is not None):
            raise ValueError("令牌替换状态无效。")


@dataclass(frozen=True)
class CredentialStoreStatus:
    metadata_ids: frozenset[str]
    secret_ids: frozenset[str]
    orphan_secret_ids: frozenset[str]
    missing_secret_ids: frozenset[str]


class CredentialService:
    """Compensating transaction boundary for credential CRUD."""

    def __init__(self, registry_store: RegistryStore, secret_store: CredentialSecretStore) -> None:
        self.registry_store = registry_store
        self.secret_store = secret_store

    def create(self, document: Mapping[str, Any], token: str) -> None:
        identifier = _identifier(document)
        secret_id = _secret_id(document)
        self.registry_store.validate_create("credentials", document)
        self._set_then_metadata(secret_id, token, lambda: self.registry_store.create("credentials", document))

    def update(self, identifier: str, draft: CredentialUpdateDraft) -> None:
        if _identifier(draft.document) != identifier:
            raise ValueError("Credential ID 创建后不可修改。")
        secret_id = _secret_id(draft.document)
        self.registry_store.validate_update("credentials", identifier, draft.document)
        if not draft.replace_token:
            self.registry_store.update("credentials", identifier, draft.document)
            return
        assert draft.new_token is not None
        self._set_then_metadata(secret_id, draft.new_token, lambda: self.registry_store.update("credentials", identifier, draft.document))

    def delete(self, identifier: str) -> None:
        self.registry_store.validate_delete("credentials", identifier)
        credential = self.registry_store.registry().credentials[identifier]
        secret_id = _secret_id({"id": credential.id, "secret_ref": credential.secret_reference})
        old_token = self.secret_store.get(secret_id)
        try:
            self.secret_store.delete(secret_id)
        except Exception as error:
            raise CredentialTransactionError("无法删除本地令牌；元数据保持不变。") from error
        try:
            self.registry_store.delete("credentials", identifier)
        except Exception as error:
            self._restore(secret_id, old_token)
            raise CredentialTransactionError("无法删除凭证元数据；已恢复本地令牌。") from error

    def status(self) -> CredentialStoreStatus:
        registry = self.registry_store.registry()
        metadata_ids = frozenset(registry.credentials)
        secret_ids = frozenset(self.secret_store.list_ids())
        referenced_ids = frozenset(_secret_id({"id": item.id, "secret_ref": item.secret_reference}) for item in registry.credentials.values() if item.secret_reference is not None)
        return CredentialStoreStatus(
            metadata_ids=metadata_ids,
            secret_ids=secret_ids,
            orphan_secret_ids=secret_ids - referenced_ids,
            missing_secret_ids=referenced_ids - secret_ids,
        )

    def _set_then_metadata(self, identifier: str, token: str, commit_metadata: Callable[[], None]) -> None:
        old_token = self.secret_store.get(identifier)
        try:
            self.secret_store.set(identifier, token)
        except Exception as error:
            raise CredentialTransactionError("无法保存本地令牌；元数据保持不变。") from error
        try:
            commit_metadata()
        except Exception as error:
            self._restore(identifier, old_token)
            raise CredentialTransactionError("无法保存凭证元数据；已恢复本地令牌。") from error

    def _restore(self, identifier: str, old_token: str | None) -> None:
        try:
            if old_token is None:
                self.secret_store.delete(identifier)
            else:
                self.secret_store.set(identifier, old_token)
        except Exception as error:
            raise CredentialTransactionError("凭证事务补偿失败；请运行 Registry 检查。") from error


def _identifier(document: Mapping[str, Any]) -> str:
    identifier = document.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("Credential ID 不能为空。")
    return identifier


def _secret_id(document: Mapping[str, Any]) -> str:
    reference = document.get("secret_ref")
    identifier = _identifier(document)
    if not isinstance(reference, str) or not reference:
        raise ValueError("Credential 必须引用本地 Secret Store。")
    prefix, separator, value = reference.partition(":")
    if prefix != "local" or not separator or not value:
        raise ValueError("Credential secret_ref 必须是 local:<id>。")
    return value
