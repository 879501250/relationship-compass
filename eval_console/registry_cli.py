"""Role-neutral interactive management for the user Registry overlay."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .credential_service import CredentialService, CredentialUpdateDraft
from .credential_store import LocalFileCredentialSecretStore, mask_token
from .interactive import CLEAR_VALUE, InteractiveBack, InteractiveCancel, InteractiveEOF, InteractiveReader
from .registry_store import RegistryStore


DraftStep = Callable[[dict[str, Any]], None]


def manage_registry(
    root: Path, *, registry_root: Path | None = None, credential_store_path: Path | None = None
) -> int:
    """Manage exactly the Registry/secret paths selected by the Console context."""
    store = RegistryStore(registry_root or root / ".eval_console" / "model_registry")
    secrets = LocalFileCredentialSecretStore(credential_store_path or root / ".eval_console" / "credentials.secrets.json")
    service = CredentialService(store, secrets)
    reader = InteractiveReader()
    try:
        while True:
            try:
                choice = reader.choice("模型与令牌管理", [("Vendor 管理", "vendors"), ("Model Family 管理", "model_families"), ("令牌管理", "credentials"), ("Preset 管理", "presets"), ("检查 Registry", "check"), ("返回", "back")])
            except InteractiveBack:
                return 0
            if choice == "back": return 0
            if choice == "check": _status(store, service, secrets); continue
            _manage_kind(reader, store, service, secrets, choice)
    except InteractiveEOF:
        print("检测到输入流已关闭，已安全返回主菜单。")
    return 0


def _manage_kind(reader: InteractiveReader, store: RegistryStore, service: CredentialService, secrets: LocalFileCredentialSecretStore, kind: str) -> None:
    labels = {"vendors": "Vendor", "model_families": "Model Family", "credentials": "令牌", "presets": "Preset"}
    while True:
        try:
            action = reader.choice(f"{labels[kind]} 管理", [("查看", "view"), ("新增", "create"), ("修改", "edit"), ("删除", "delete"), ("返回", "back")])
        except InteractiveBack:
            return
        if action == "back": return
        registry = store.registry()
        items = getattr(registry, {"vendors": "vendors", "model_families": "model_families", "credentials": "credentials", "presets": "presets"}[kind])
        if action == "view": _view(kind, items, store, secrets); continue
        try:
            if action == "create": _create(reader, store, service, kind)
            elif action == "edit": _edit(reader, store, service, kind, items)
            else: _delete(reader, store, service, kind, items)
        except InteractiveCancel:
            print("已取消，草稿未保存。")
        except InteractiveBack:
            print("已返回上级菜单，草稿未保存。")
        except (ValueError, KeyError) as error:
            print(f"无法保存：{error}")


def _run_wizard(steps: list[DraftStep], draft: dict[str, Any] | None = None) -> dict[str, Any]:
    """State-machine fields: Back rewinds one field while retaining the draft."""
    state = draft if draft is not None else {}
    index = 0
    while index < len(steps):
        try: steps[index](state)
        except InteractiveBack: index = max(0, index - 1)
        except ValueError as error: print(f"输入无效：{error}")
        else: index += 1
    return state


def _optional_field(reader: InteractiveReader, state: dict[str, Any], key: str, prompt: str) -> None:
    """Blank keeps an edit value; clear/none/清除 removes an optional field."""
    current = state.get(key)
    suffix = f" [{current}]" if current is not None else ""
    value = reader.optional_value(f"{prompt}{suffix}: ", default=current)
    if value is CLEAR_VALUE or value is None: state.pop(key, None)
    else: state[key] = value


def _view(kind: str, items: Any, store: RegistryStore, secrets: LocalFileCredentialSecretStore) -> None:
    if not items: print("暂无定义。"); return
    for item in items.values():
        if kind == "credentials":
            scope = ", ".join(item.base_url_ids) or "全部 Base URL"
            print(f"{item.id} | {item.name or item.id} | {item.vendor_id} | {scope} | {store.credential_status(item.id)} | {item.expires_at or '未设置'} | {mask_token(secrets.get(item.id))}")
        elif kind == "vendors":
            print(f"{item.id} | {item.name} | Protocol={item.protocol or '按 Base URL'}")
            for url in item.base_urls:
                print(f"  - {url.id}: {url.url} | {url.protocol or item.protocol} | {', '.join(url.model_families)} | default={', '.join(url.default_for) or '-'}")
                if url.model_overrides: print(f"    model overrides: {', '.join(url.model_overrides)}")
                if url.parameter_mapping: print(f"    parameter mapping: {dict(url.parameter_mapping)}")
                if url.transport_defaults: print(f"    transport defaults: {dict(url.transport_defaults)}")
        elif kind == "model_families": print(f"{item.id} | {item.name} | Models: {', '.join(item.models)}")
        else: print(f"{item.id} | {item.name or item.model_id} | {item.vendor_id}/{item.base_url_id}/{item.model_id}")


def _create(reader: InteractiveReader, store: RegistryStore, service: CredentialService, kind: str) -> None:
    if kind == "vendors":
        document = _vendor_wizard(reader, store.registry())
        if reader.confirm("确认保存 Vendor？", default=True, allow_back=True): store.create(kind, document); print("已保存。")
    elif kind == "model_families":
        document = _family_wizard(reader, store.registry())
        if reader.confirm("确认保存 Model Family？", default=True, allow_back=True): store.create(kind, document); print("已保存。")
    elif kind == "credentials":
        document, token = _credential_wizard(reader, store.registry())
        if reader.confirm(f"保存令牌 {document['id']}（{mask_token(token)}）？", default=True, allow_back=True):
            service.create(document, token); print("令牌元数据与本地 Secret Store 已保存。")
    else:
        document = _preset_wizard(reader, store.registry())
        if reader.confirm("确认保存 Preset？", default=True, allow_back=True): store.create(kind, document); print("已保存。")


def _vendor_wizard(reader: InteractiveReader, registry: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    draft = deepcopy(existing or {})
    editing = existing is not None
    if editing: print(f"Vendor ID: {draft['id']}（不可修改）")
    def field(key: str, prompt: str, required: bool = False) -> DraftStep:
        return lambda state: state.__setitem__(key, reader.text(prompt, default=state.get(key), required=required))
    steps: list[DraftStep] = ([] if editing else [field("id", "Vendor ID: ", True)]) + [
        field("name", "名称: ", True),
        lambda state: _optional_field(reader, state, "website", "Website（可留空；clear 清除）"),
        lambda state: _optional_field(reader, state, "description", "描述（可留空；clear 清除）"),
        lambda state: _optional_field(reader, state, "notes", "备注（可留空；clear 清除）"),
        lambda state: _optional_field(reader, state, "category", "分类 official/relay/enterprise/local（可留空；clear 清除）"),
        lambda state: _optional_field(reader, state, "protocol", "默认 Protocol（可留空；clear 清除）"),
        lambda state: _base_url_manager(reader, registry, state.setdefault("base_urls", [])),
    ]
    draft = _run_wizard(steps, draft)
    return draft


def _base_url_manager(reader: InteractiveReader, registry: Any, endpoints: list[dict[str, Any]]) -> None:
    while True:
        action = reader.choice("Base URL 管理", [("添加", "add"), ("修改", "edit"), ("删除", "delete"), ("高级设置", "advanced"), ("完成", "done")])
        if action == "done":
            if not endpoints: raise ValueError("至少需要一个 Base URL。")
            return
        if action == "add": endpoints.append(_base_url_wizard(reader, registry, endpoints))
        else:
            if not endpoints: raise ValueError("暂无 Base URL。")
            selected = reader.choice("选择 Base URL", [(item["id"], item) for item in endpoints])
            if action == "delete":
                if len(endpoints) == 1: raise ValueError("Vendor 至少需要一个 Base URL。")
                endpoints.remove(selected)
            elif action == "edit": selected.update(_base_url_wizard(reader, registry, endpoints, selected))
            else: _advanced_base_url(reader, registry, selected)


def _base_url_wizard(reader: InteractiveReader, registry: Any, endpoints: list[dict[str, Any]], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    draft = deepcopy(existing or {})
    editing = existing is not None
    if editing: print(f"Base URL ID: {draft['id']}（不可修改）")
    def identifier(state: dict[str, Any]) -> None: state["id"] = reader.text("Base URL ID: ", required=True)
    def url(state: dict[str, Any]) -> None: state["url"] = reader.text("Base URL（HTTPS）: ", default=state.get("url"), required=True)
    def protocol(state: dict[str, Any]) -> None: _optional_field(reader, state, "protocol", "Protocol（留空继承 Vendor；clear 清除）")
    def families(state: dict[str, Any]) -> None: state["model_families"] = reader.multi_choice("选择支持的 Model Family", [(key, key) for key in registry.model_families])
    def defaults(state: dict[str, Any]) -> None: state["default_for"] = reader.multi_choice("选择 default_for（仅限已选 Family）", [(key, key) for key in state["model_families"]])
    draft = _run_wizard(([] if editing else [identifier]) + [url, protocol, families, defaults], draft)
    if not draft.get("protocol"): draft.pop("protocol", None)
    _move_default_conflicts(reader, endpoints, draft)
    return draft


def _move_default_conflicts(reader: InteractiveReader, endpoints: list[dict[str, Any]], candidate: dict[str, Any]) -> None:
    for family in list(candidate.get("default_for", [])):
        for old in [item for item in endpoints if item.get("id") != candidate.get("id") and family in item.get("default_for", [])]:
            if reader.confirm(f"{family} 当前默认 Base URL 是 {old['id']}。移动到 {candidate['id']}？", default=False): old["default_for"].remove(family)
            else: candidate["default_for"].remove(family)


def _advanced_base_url(reader: InteractiveReader, registry: Any, endpoint: dict[str, Any]) -> None:
    while True:
        try:
            action = reader.choice("Base URL 高级设置", [("模型 API 名称覆盖", "model"), ("语义参数映射", "mapping"), ("Transport defaults", "transport"), ("返回", "back")])
        except InteractiveBack:
            return
        if action == "back": return
        if action == "model":
            family = reader.choice("Model Family", [(key, key) for key in endpoint["model_families"]])
            model = reader.choice("Model", [(key, key) for key in registry.model_families[family].models])
            override = endpoint.setdefault("model_overrides", {}).setdefault(model, {})
            override["api_name"] = reader.text("API model name: ", required=True)
            if reader.confirm("编辑该模型的 Capability override？", default=False):
                _capabilities_manager(reader, registry.model_families[family].capabilities, override.setdefault("capabilities", {}))
        elif action == "mapping":
            family = reader.choice("选择 Model Family", [(key, key) for key in endpoint["model_families"]])
            semantic = reader.choice("语义参数", [(key, key) for key in registry.model_families[family].capabilities])
            endpoint.setdefault("parameter_mapping", {})[semantic] = reader.text("Wire parameter 名称: ", required=True)
        else:
            key = reader.choice("Transport 设置", [("timeout_seconds", "timeout_seconds"), ("max_retries", "max_retries")])
            value = reader.text(f"{key}: ", required=True)
            if not value.isdigit() or int(value) < 0: raise ValueError("Transport 值必须是非负整数。")
            endpoint.setdefault("transport_defaults", {})[key] = int(value)


def _family_wizard(reader: InteractiveReader, registry: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    draft = deepcopy(existing or {})
    editing = existing is not None
    if editing: print(f"Model Family ID: {draft['id']}（不可修改）")
    def identifier(state: dict[str, Any]) -> None: state["id"] = reader.text("Family ID: ", required=True)
    def name(state: dict[str, Any]) -> None: state["name"] = reader.text("名称: ", default=state.get("name"), required=True)
    def description(state: dict[str, Any]) -> None: _optional_field(reader, state, "description", "描述（可留空；clear 清除）")
    def context(state: dict[str, Any]) -> None: _optional_positive(reader, state.setdefault("defaults", {"capabilities": {}}), "context_window", "默认 Context Window（可留空；clear 清除）")
    def capabilities(state: dict[str, Any]) -> None: _capabilities_manager(reader, _known_capabilities(registry), state.setdefault("defaults", {"capabilities": {}}).setdefault("capabilities", {}))
    def models(state: dict[str, Any]) -> None:
        known = dict(_known_capabilities(registry)); known.update(state.setdefault("defaults", {}).setdefault("capabilities", {}))
        _models_manager(reader, state.setdefault("models", {}), known)
    draft = _run_wizard(([] if editing else [identifier]) + [name, description, context, capabilities, models], draft)
    return draft


def _models_manager(reader: InteractiveReader, models: dict[str, Any], known_capabilities: Any) -> None:
    while True:
        action = reader.choice("Models 管理", [("添加", "add"), ("修改", "edit"), ("删除", "delete"), ("完成", "done")])
        if action == "done":
            if not models: raise ValueError("Model Family 至少需要一个 Model。")
            return
        if action == "add":
            model_id = reader.text("Model ID: ", required=True)
            if model_id in models: raise ValueError("Model ID 已存在。")
            models[model_id] = _model_fields(reader, {}, known_capabilities)
        else:
            if not models: raise ValueError("暂无 Model。")
            model_id = reader.choice("选择 Model", [(key, key) for key in models])
            if action == "delete":
                if len(models) == 1: raise ValueError("Model Family 至少需要一个 Model。")
                del models[model_id]
            else: models[model_id] = _model_fields(reader, models[model_id], known_capabilities)


def _model_fields(reader: InteractiveReader, existing: dict[str, Any], known_capabilities: Any) -> dict[str, Any]:
    result = deepcopy(existing)
    _optional_field(reader, result, "api_name", "API model name（可留空；clear 清除）")
    _optional_positive(reader, result, "context_window", "Context Window（可留空；clear 清除）")
    _capabilities_manager(reader, known_capabilities, result.setdefault("capabilities", {}))
    if not result.get("capabilities"): result.pop("capabilities", None)
    return result


def _known_capabilities(registry: Any) -> dict[str, Any]:
    """The immutable registry is the schema source; the CLI invents no names."""
    known: dict[str, Any] = {}
    for family in registry.model_families.values():
        known.update(family.capabilities)
        for model in family.models.values(): known.update(model.capabilities)
    return known


def _optional_positive(reader: InteractiveReader, state: dict[str, Any], key: str, prompt: str) -> None:
    current = state.get(key)
    suffix = f" [{current}]" if current is not None else ""
    value = reader.optional_value(f"{prompt}{suffix}: ", default=str(current) if current is not None else None)
    if value is CLEAR_VALUE or value is None: state.pop(key, None); return
    if not isinstance(value, str) or not value.isdigit() or int(value) <= 0: raise ValueError("必须是正整数。")
    state[key] = int(value)


def _capabilities_manager(reader: InteractiveReader, known: Any, capabilities: dict[str, Any]) -> None:
    """Edit only capability definitions already declared by the Foundation."""
    if not known: return
    while True:
        action = reader.choice("Capability 设置", [("编辑", "edit"), ("恢复继承", "clear"), ("完成", "done")])
        if action == "done": return
        name = reader.choice("选择 Foundation 已定义的 Capability", [(key, key) for key in known])
        if action == "clear": capabilities.pop(name, None); continue
        capabilities[name] = _edit_capability_definition(reader, known[name], capabilities.get(name, {}))


def _edit_capability_definition(reader: InteractiveReader, schema: Any, existing: Any) -> dict[str, Any]:
    result = {"supported": reader.confirm("Supported？", default=bool(existing.get("supported", schema.get("supported", False))), allow_back=True)}
    if not result["supported"]: return result
    for key, label in (("allowed_values", "Allowed Values"), ("modes", "Modes")):
        values = schema.get(key)
        if values and reader.confirm(f"限制 {label}？", default=key in existing, allow_back=True):
            result[key] = reader.multi_choice(label, [(str(value), value) for value in values])
    return result


def _credential_wizard(reader: InteractiveReader, registry: Any, existing: dict[str, Any] | None = None, *, immutable_vendor: bool = False, collect_token: bool = True) -> tuple[dict[str, Any], str | None]:
    draft, token_box = deepcopy(existing or {}), {}
    editing = existing is not None
    if editing: print(f"Credential ID: {draft['id']}（不可修改）")
    def identifier(state: dict[str, Any]) -> None: state["id"] = reader.text("Credential ID: ", required=True)
    def name(state: dict[str, Any]) -> None: state["name"] = reader.text("名称: ", default=state.get("name"), required=True)
    def vendor(state: dict[str, Any]) -> None:
        if not immutable_vendor: state["vendor"] = reader.choice("选择 Vendor", [(item.name, item.id) for item in registry.vendors.values()])
    def scope(state: dict[str, Any]) -> None:
        restricted = reader.confirm("是否限制此令牌只能用于部分 Base URL？", default=bool(state.get("base_url_ids")), allow_back=True)
        state["base_url_ids"] = reader.multi_choice("选择允许使用的 Base URL", [(item.id, item.id) for item in registry.vendors[state["vendor"]].base_urls]) if restricted else []
    def expiry(state: dict[str, Any]) -> None: _optional_field(reader, state, "expires_at", "过期日期 YYYY-MM-DD（可留空；clear 清除）")
    def notes(state: dict[str, Any]) -> None: _optional_field(reader, state, "notes", "备注（可留空；clear 清除）")
    def token(state: dict[str, Any]) -> None: token_box["token"] = reader.secret("请输入令牌（不回显）: ")
    steps = ([] if editing else [identifier]) + [name, vendor, scope, expiry, notes]
    if collect_token: steps.append(token)
    draft = _run_wizard(steps, draft)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    draft.setdefault("secret_ref", f"local:{draft['id']}"); draft.setdefault("created_at", now); draft["updated_at"] = now
    return draft, token_box.get("token")


def _preset_wizard(reader: InteractiveReader, registry: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    draft = deepcopy(existing or {})
    editing = existing is not None
    if editing: print(f"Preset ID: {draft['id']}（不可修改）")
    def identifier(state: dict[str, Any]) -> None: state["id"] = reader.text("Preset ID: ", required=True)
    def name(state: dict[str, Any]) -> None: state["name"] = reader.text("名称: ", default=state.get("name"), required=True)
    def vendor(state: dict[str, Any]) -> None: state["vendor"] = reader.choice("选择 Vendor", [(item.name, item.id) for item in registry.vendors.values()])
    def family(state: dict[str, Any]) -> None: state["model_family"] = reader.choice("选择 Model Family", [(key, key) for key in registry.model_families])
    def model(state: dict[str, Any]) -> None: state["model"] = reader.choice("选择 Model", [(key, key) for key in registry.model_families[state["model_family"]].models])
    def url(state: dict[str, Any]) -> None: state["base_url"] = reader.choice("选择 Base URL", [(item.id, item.id) for item in registry.vendors[state["vendor"]].base_urls if state["model_family"] in item.model_families])
    def credential(state: dict[str, Any]) -> None:
        candidates = [item for item in registry.credentials.values() if item.vendor_id == state["vendor"] and (not item.base_url_ids or state["base_url"] in item.base_url_ids)]
        if not candidates: raise ValueError("没有与 Vendor/Base URL scope 兼容的令牌。")
        state["credential"] = reader.choice("选择令牌", [(item.name or item.id, item.id) for item in candidates])
    def description(state: dict[str, Any]) -> None: _optional_field(reader, state, "description", "描述（可留空；clear 清除）")
    def notes(state: dict[str, Any]) -> None: _optional_field(reader, state, "notes", "备注（可留空；clear 清除）")
    draft = _run_wizard(([] if editing else [identifier]) + [name, vendor, family, model, url, credential, description, notes], draft)
    runtime = registry.resolve(vendor_id=draft["vendor"], base_url_id=draft["base_url"], credential_id=draft["credential"], model_family_id=draft["model_family"], model_id=draft["model"])
    draft["parameters"] = _semantic_parameters(reader, runtime.resolved_capabilities)
    return draft


def _edit(reader: InteractiveReader, store: RegistryStore, service: CredentialService, kind: str, items: Any) -> None:
    choices = [(getattr(item, "name", None) or item.id, item.id) for item in items.values() if (store.user_root / kind / f"{item.id}.yaml").is_file()]
    if not choices: print("没有可修改的用户定义；内置定义只读。"); return
    identifier, document = reader.choice("选择要修改的用户定义", choices), None
    document = store.read_user_document(kind, identifier)
    if kind == "credentials":
        document, _ = _credential_wizard(reader, store.registry(), document, immutable_vendor=True, collect_token=False)
        replace = reader.confirm("确认更换令牌？", default=False, allow_back=True)
        token = reader.secret("新令牌（不回显）: ") if replace else None
        if reader.confirm("确认保存修改？", default=True, allow_back=True):
            service.update(identifier, CredentialUpdateDraft(document, replace_token=replace, new_token=token if replace else None)); print("已保存。")
    else:
        document = _vendor_wizard(reader, store.registry(), document) if kind == "vendors" else _family_wizard(reader, store.registry(), document) if kind == "model_families" else _preset_wizard(reader, store.registry(), document)
        if reader.confirm("确认保存修改？", default=True, allow_back=True): store.update(kind, identifier, document); print("已保存。")


def _delete(reader: InteractiveReader, store: RegistryStore, service: CredentialService, kind: str, items: Any) -> None:
    choices = [(getattr(item, "name", None) or item.id, item.id) for item in items.values() if (store.user_root / kind / f"{item.id}.yaml").is_file()]
    if not choices: print("没有可删除的用户定义；内置定义只读。"); return
    identifier = reader.choice("选择要删除的用户定义", choices)
    if store.references(kind, identifier): raise ValueError("该对象仍被引用，不能删除。")
    if reader.confirm(f"确认删除 {identifier}？", default=False):
        if kind == "credentials": service.delete(identifier)
        else: store.delete(kind, identifier)
        print("已删除。")


def _status(store: RegistryStore, service: CredentialService, secrets: LocalFileCredentialSecretStore) -> None:
    registry, status = store.registry(), service.status()
    expired = sum(store.credential_status(item.id) == "已过期" for item in registry.credentials.values())
    print(f"Vendor: {len(registry.vendors)}\nModel Family: {len(registry.model_families)}\nModels: {sum(len(item.models) for item in registry.model_families.values())}\nCredential Metadata: {len(status.metadata_ids)}\nPreset: {len(registry.presets)}\nValidation: PASS\nSecret Store: {'Ready' if secrets.path.exists() else 'Empty'}\nSecrets: {len(status.secret_ids)}\nOrphan Secrets: {len(status.orphan_secret_ids)}\nMissing Secrets: {len(status.missing_secret_ids)}\nExpired Credentials: {expired}")


def _semantic_parameters(reader: InteractiveReader, capabilities: Any) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for name, rule in capabilities.items():
        if not rule["supported"]: continue
        values = rule.get("allowed_values") or rule.get("modes")
        if values:
            if reader.confirm(f"设置 {name}？", default=False): parameters[name] = reader.choice(name, [(str(value), value) for value in values])
        elif name == "max_output_tokens":
            value = reader.text("Max Output Tokens（可留空）: ")
            if value:
                if not value.isdigit() or int(value) <= 0: raise ValueError("Max Output Tokens 必须是正整数。")
                parameters[name] = int(value)
    return parameters
