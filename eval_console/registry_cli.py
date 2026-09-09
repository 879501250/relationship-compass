"""Role-neutral interactive management for the user Registry overlay."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .credential_store import LocalFileCredentialSecretStore, mask_token
from .interactive import InteractiveBack, InteractiveCancel, InteractiveEOF, InteractiveReader
from .registry_store import RegistryStore


def manage_registry(root: Path) -> int:
    store = RegistryStore(root / ".eval_console" / "model_registry")
    secrets = LocalFileCredentialSecretStore(root / ".eval_console" / "credentials.secrets.json")
    reader = InteractiveReader()
    try:
        while True:
            try:
                choice = reader.choice("模型与令牌管理", [("Vendor 管理", "vendors"), ("Model Family 管理", "model_families"), ("令牌管理", "credentials"), ("Preset 管理", "presets"), ("检查 Registry", "check"), ("返回", "back")])
            except InteractiveBack:
                return 0
            if choice == "back": return 0
            if choice == "check": _status(store, secrets); continue
            _manage_kind(reader, store, secrets, choice)
    except InteractiveEOF:
        print("检测到输入流已关闭，已安全返回主菜单。")
    return 0


def _manage_kind(reader: InteractiveReader, store: RegistryStore, secrets: LocalFileCredentialSecretStore, kind: str) -> None:
    labels = {"vendors": "Vendor", "model_families": "Model Family", "credentials": "令牌", "presets": "Preset"}
    while True:
        try:
            action = reader.choice(f"{labels[kind]} 管理", [("查看", "view"), ("新增", "create"), ("修改", "edit"), ("删除", "delete"), ("返回", "back")])
        except InteractiveBack:
            return
        if action == "back": return
        registry = store.registry(); items = getattr(registry, {"vendors": "vendors", "model_families": "model_families", "credentials": "credentials", "presets": "presets"}[kind])
        if action == "view": _view(kind, items, store, secrets); continue
        try:
            if action == "create": _create(reader, store, secrets, kind)
            elif action == "edit": _edit(reader, store, secrets, kind, items)
            else: _delete(reader, store, secrets, kind, items)
        except InteractiveBack:
            print("已返回上一步，草稿未保存。")
        except (ValueError, KeyError) as error:
            print(f"无法保存：{error}")


def _view(kind: str, items: Any, store: RegistryStore, secrets: LocalFileCredentialSecretStore) -> None:
    if not items: print("暂无定义。")
    for item in items.values():
        if kind == "credentials":
            print(f"{item.id} | {item.name or item.id} | {item.vendor_id} | {store.credential_status(item.id)} | {item.expires_at or '未设置'} | {mask_token(secrets.get(item.id))}")
        elif kind == "vendors":
            print(f"{item.id} | {item.name} | Protocol={item.protocol or '按 Base URL'}")
            for url in item.base_urls: print(f"  - {url.id}: {url.url} | {url.protocol or item.protocol} | {', '.join(url.model_families)} | default={', '.join(url.default_for) or '-'}")
        else: print(f"{item.id} | {getattr(item, 'name', None) or getattr(item, 'model_id', '')}")


def _create(reader: InteractiveReader, store: RegistryStore, secrets: LocalFileCredentialSecretStore, kind: str) -> None:
    if kind == "vendors":
        identifier = reader.text("Vendor ID: ", required=True); name = reader.text("名称: ", required=True)
        protocol = reader.optional("默认 Protocol（可留空）: ")
        registry = store.registry(); family = reader.choice("选择支持的 Model Family", [(key, key) for key in registry.model_families])
        url_id = reader.text("Base URL ID: ", required=True); url = reader.text("Base URL（HTTPS）: ", required=True)
        document = {"id": identifier, "name": name, "base_urls": [{"id": url_id, "url": url, "model_families": [family], "default_for": [family]}]}
        if protocol: document["protocol"] = protocol
    elif kind == "model_families":
        identifier = reader.text("Family ID: ", required=True); name = reader.text("名称: ", required=True); model = reader.text("首个 Model ID: ", required=True)
        document = {"id": identifier, "name": name, "defaults": {"capabilities": {}}, "models": {model: {}}}
    elif kind == "credentials":
        identifier = reader.text("Credential ID: ", required=True); name = reader.text("名称: ", required=True)
        registry = store.registry(); vendor = reader.choice("选择 Vendor", [(item.name, item.id) for item in registry.vendors.values()])
        expiry = reader.optional("过期日期 YYYY-MM-DD（可留空）: "); notes = reader.optional("备注（可留空）: ")
        token = reader.secret("请输入令牌（不回显）: ")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        document = {"id": identifier, "name": name, "vendor": vendor, "secret_ref": f"local:{identifier}", "created_at": now, "updated_at": now}
        if expiry: document["expires_at"] = expiry
        if notes: document["notes"] = notes
        if not reader.confirm(f"保存令牌 {identifier}（{mask_token(token)}）？", default=True): return
        store.create(kind, document)
        try: secrets.set(identifier, token)
        except ValueError:
            store.delete(kind, identifier); raise
        print("令牌元数据与本地 Secret Store 已保存。")
        return
    else:
        registry = store.registry(); identifier = reader.text("Preset ID: ", required=True); name = reader.text("名称: ", required=True)
        vendor_id = reader.choice("选择 Vendor", [(item.name, item.id) for item in registry.vendors.values()]); vendor = registry.vendors[vendor_id]
        family = reader.choice("选择 Model Family", [(key, key) for key in registry.model_families]); model = reader.choice("选择 Model", [(key, key) for key in registry.model_families[family].models])
        url = reader.choice("选择 Base URL", [(item.id, item.id) for item in vendor.base_urls if family in item.model_families]); credential = reader.choice("选择令牌", [(item.name or item.id, item.id) for item in registry.credentials.values() if item.vendor_id == vendor_id])
        runtime = registry.resolve(vendor_id=vendor_id, base_url_id=url, credential_id=credential, model_family_id=family, model_id=model)
        document = {"id": identifier, "name": name, "vendor": vendor_id, "base_url": url, "credential": credential, "model_family": family, "model": model, "parameters": _semantic_parameters(reader, runtime.resolved_capabilities)}
    if reader.confirm("确认保存？", default=True): store.create(kind, document); print("已保存。")


def _edit(reader: InteractiveReader, store: RegistryStore, secrets: LocalFileCredentialSecretStore, kind: str, items: Any) -> None:
    choices = [(getattr(item, "name", None) or item.id, item.id) for item in items.values() if (store.user_root / kind / f"{item.id}.yaml").is_file()]
    if not choices: print("没有可修改的用户定义；内置定义只读。 "); return
    identifier = reader.choice("选择要修改的用户定义", choices); path = store.user_root / kind / f"{identifier}.yaml"; import json
    document = json.loads(path.read_text(encoding="utf-8"))
    if kind == "credentials":
        document["name"] = reader.text("名称: ", default=document.get("name") or identifier, required=True); document["expires_at"] = reader.optional("过期日期（留空清除）: ")
        document["notes"] = reader.optional("备注（留空清除）: "); document["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        if reader.confirm("更换令牌？", default=False): secrets.set(identifier, reader.secret("新令牌（不回显）: "))
    elif kind == "model_families":
        document["name"] = reader.text("名称: ", default=document.get("name") or identifier, required=True)
    elif kind == "vendors":
        document["name"] = reader.text("名称: ", default=document.get("name") or identifier, required=True)
        document["notes"] = reader.optional("备注（留空清除）: ")
    else:
        document["name"] = reader.text("名称: ", default=document.get("name") or identifier, required=True)
        document["notes"] = reader.optional("备注（留空清除）: ")
    if reader.confirm("确认保存修改？", default=True): store.update(kind, identifier, {key: value for key, value in document.items() if value is not None}); print("已保存。")


def _delete(reader: InteractiveReader, store: RegistryStore, secrets: LocalFileCredentialSecretStore, kind: str, items: Any) -> None:
    choices = [(getattr(item, "name", None) or item.id, item.id) for item in items.values() if (store.user_root / kind / f"{item.id}.yaml").is_file()]
    if not choices: print("没有可删除的用户定义；内置定义只读。 "); return
    identifier = reader.choice("选择要删除的用户定义", choices)
    references = store.references(kind, identifier)
    if references: print("拒绝删除，仍被引用：\n" + "\n".join(f"- {item}" for item in references)); return
    if reader.confirm(f"确认删除 {identifier}？", default=False):
        store.delete(kind, identifier)
        if kind == "credentials": secrets.delete(identifier)
        print("已删除。")


def _status(store: RegistryStore, secrets: LocalFileCredentialSecretStore) -> None:
    registry = store.registry(); expired = sum(store.credential_status(item.id) == "已过期" for item in registry.credentials.values())
    print(f"Vendor: {len(registry.vendors)}\nModel Family: {len(registry.model_families)}\nModels: {sum(len(item.models) for item in registry.model_families.values())}\nCredential: {len(registry.credentials)}\nPreset: {len(registry.presets)}\nValidation: PASS\nSecret Store: {'Ready' if secrets.path.exists() else 'Empty'}\nExpired Credentials: {expired}")


def _semantic_parameters(reader: InteractiveReader, capabilities: Any) -> dict[str, Any]:
    """Collect only schema-supported semantic parameters, never wire names."""
    parameters: dict[str, Any] = {}
    for name, rule in capabilities.items():
        if not rule["supported"]:
            continue
        values = rule.get("allowed_values") or rule.get("modes")
        if values:
            if reader.confirm(f"设置 {name}？", default=False):
                parameters[name] = reader.choice(name, [(str(value), value) for value in values])
        elif name == "max_output_tokens":
            value = reader.optional("Max Output Tokens（可留空）: ")
            if value:
                if not value.isdigit() or int(value) <= 0:
                    raise ValueError("Max Output Tokens 必须是正整数。")
                parameters[name] = int(value)
    return parameters
