"""Role-neutral interactive management for the user Registry overlay."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .credential_service import CredentialService, CredentialUpdateDraft
from .credential_store import LocalFileCredentialSecretStore, mask_token
from .interactive import CLEAR_VALUE, InteractiveBack, InteractiveCancel, InteractiveEOF, InteractiveReader
from .registry_runtime import PresetReadiness, RegistryProviderFactory, RegistryRuntimeResolver, format_preset_readiness_failure
from .registry_store import RegistryStore


DraftStep = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class RegistryBootstrapState:
    """Secret-free readiness summary for first-use Registry guidance."""

    vendor_count: int
    model_family_count: int
    model_count: int
    credential_count: int
    preset_count: int
    runnable_preset_ids: tuple[str, ...]
    problems: tuple[str, ...]

    @property
    def runnable_preset_count(self) -> int:
        return len(self.runnable_preset_ids)

    @property
    def ready(self) -> bool:
        return self.runnable_preset_count > 0


def check_bootstrap_state(
    store: RegistryStore,
    secrets: LocalFileCredentialSecretStore,
) -> RegistryBootstrapState:
    """Assess the current overlay and secret store without making HTTP calls."""
    registry = _configuration_registry(store)
    readiness: list[PresetReadiness] = []
    preset_ids = store.preset_ids() if isinstance(store, RegistryStore) else tuple(sorted(registry.presets))
    for identifier in preset_ids:
        try:
            preset_registry = store.registry_for_preset(identifier) if isinstance(store, RegistryStore) else registry
            readiness.append(
                RegistryRuntimeResolver(preset_registry, secrets)
                .assess_preset_readiness(identifier)
            )
        except ValueError as error:
            readiness.append(PresetReadiness(identifier, False, False, False, False, False, False, (), (format_preset_readiness_failure(identifier, str(error)),)))
    readiness_tuple = tuple(readiness)
    problems = _bootstrap_problems(registry, readiness_tuple)
    return RegistryBootstrapState(
        vendor_count=len(registry.vendors),
        model_family_count=len(registry.model_families),
        model_count=sum(len(item.models) for item in registry.model_families.values()),
        credential_count=len(registry.credentials),
        preset_count=len(preset_ids),
        runnable_preset_ids=tuple(item.preset_id for item in readiness_tuple if item.runnable),
        problems=problems,
    )


def offer_bootstrap_setup(
    root: Path,
    *,
    registry_root: Path | None = None,
    credential_store_path: Path | None = None,
    reader: InteractiveReader | None = None,
) -> RegistryBootstrapState:
    """Offer a first-use setup once, preserving the caller's configured paths."""
    store, secrets, service = _registry_services(root, registry_root, credential_store_path)
    reader = reader or InteractiveReader()
    state = check_bootstrap_state(store, secrets)
    if state.ready:
        return state
    _print_registry_summary(state)
    print("\n当前没有可运行模型配置。\n\n需要创建：\n1. Credential\n2. Preset")
    if reader.confirm("是否进入快速配置？", default=True):
        quick_setup_first_model(reader, store, service, secrets)
        state = check_bootstrap_state(store, secrets)
    return state


def manage_registry(
    root: Path, *, registry_root: Path | None = None, credential_store_path: Path | None = None
) -> int:
    """Manage exactly the Registry/secret paths selected by the Console context."""
    store, secrets, service = _registry_services(root, registry_root, credential_store_path)
    reader = InteractiveReader()
    try:
        while True:
            _print_registry_summary(check_bootstrap_state(store, secrets))
            try:
                choice = reader.choice("模型与令牌管理", [("快速配置第一个模型", "quick_setup"), ("Vendor 管理", "vendors"), ("Model Family 管理", "model_families"), ("访问凭证管理", "credentials"), ("Preset 管理", "presets"), ("检查 Registry", "check"), ("系统管理", "system"), ("返回", "back")])
            except InteractiveBack:
                return 0
            if choice == "back": return 0
            if choice == "quick_setup":
                quick_setup_first_model(reader, store, service, secrets)
                continue
            if choice == "check": _status(store, service, secrets); continue
            if choice == "system":
                _system_management(reader, store, secrets)
                continue
            _manage_kind(reader, store, service, secrets, choice)
    except InteractiveEOF:
        print("检测到输入流已关闭，已安全返回主菜单。")
    return 0


def _system_management(reader: InteractiveReader, store: RegistryStore, secrets: LocalFileCredentialSecretStore) -> None:
    while True:
        choice = reader.choice("系统管理", [
            ("查看 Registry 状态", "status"),
            ("导出用户配置", "export"),
            ("恢复默认 Registry", "restore"),
            ("清理用户配置", "clear"),
            ("返回", "back"),
        ])
        if choice == "back":
            return
        if choice == "status":
            _print_registry_summary(check_bootstrap_state(store, secrets))
        elif choice == "export":
            exported = store.export_user_configuration(datetime.now().strftime("%Y%m%d-%H%M%S"))
            print(f"已导出用户 Registry 配置：{exported}")
            print("Token 保留在本地 Secret Store，未被导出。")
        elif choice == "restore":
            print("将恢复系统默认模型定义。不会删除 Credential 或 Preset。")
            if reader.confirm("继续？", default=False):
                store.restore_builtin_model_definitions()
                print("已恢复内建 Vendor、Model Family 与 Model 定义。")
        else:
            print("这将清理用户 Credential、Preset 与本地 Secret Store。")
            if not reader.confirm("我已理解此操作不可撤销，继续？", default=False, allow_back=True):
                print("未执行清理。")
            elif reader.text("再次输入 RESET 确认: ", allow_back=True) == "RESET":
                for identifier in secrets.list_ids():
                    secrets.delete(identifier)
                store.clear_user_configuration()
                print("用户配置已清理；内建 Registry 保持不变。")
            else:
                print("未执行清理。")


def _registry_services(
    root: Path,
    registry_root: Path | None,
    credential_store_path: Path | None,
) -> tuple[RegistryStore, LocalFileCredentialSecretStore, CredentialService]:
    store = RegistryStore(registry_root or root / ".eval_console" / "model_registry")
    secrets = LocalFileCredentialSecretStore(
        credential_store_path or root / ".eval_console" / "credentials.secrets.json"
    )
    return store, secrets, CredentialService(store, secrets)


def _configuration_registry(store: RegistryStore) -> Any:
    """Keep management available when a separate user preset is malformed."""
    return store.registry_without_presets() if isinstance(store, RegistryStore) else store.registry()


def quick_setup_first_model(
    reader: InteractiveReader,
    store: RegistryStore,
    service: CredentialService,
    secrets: LocalFileCredentialSecretStore,
) -> RegistryBootstrapState:
    """Create a Vendor-first Credential/Preset pair without asking for IDs."""
    registry = _configuration_registry(store)
    vendor_id = _choose_vendor(reader, registry)
    family_id = _choose_vendor_family(reader, registry, vendor_id)
    model_id = reader.choice(
        "选择 Model", [(item, item) for item in sorted(registry.model_families[family_id].models)]
    )
    base_url_id = _choose_vendor_base_url(reader, registry, vendor_id, family_id)
    credential_id = _choose_or_create_credential(reader, store, service, registry, vendor_id)
    registry = _configuration_registry(store)
    preset_name = reader.text("Preset 名称: ", default=f"{model_id} 默认配置", required=True)
    preset_id = _generated_identifier("preset", model_id, store.preset_ids())
    preset = _preset_document(
        preset_id, preset_name, vendor_id, base_url_id, credential_id, family_id, model_id
    )
    runtime = registry.resolve(
        vendor_id=vendor_id, base_url_id=base_url_id, credential_id=credential_id,
        model_family_id=family_id, model_id=model_id,
    )
    preset["parameters"] = _semantic_parameters(reader, runtime.resolved_capabilities)
    store.create("presets", preset)
    state = check_bootstrap_state(store, secrets)
    print("\n配置完成")
    print(f"内部 ID: {preset_id}")
    print(f"Preset: {preset_id}")
    print(f"Status: {'Runnable' if preset_id in state.runnable_preset_ids else 'Not runnable'}")
    if preset_id not in state.runnable_preset_ids:
        print("Problems:\n" + "\n".join(f"- {item}" for item in state.problems))
    return state


def _choose_vendor(reader: InteractiveReader, registry: Any) -> str:
    choices = [
        (vendor.name, vendor.id)
        for vendor in sorted(registry.vendors.values(), key=lambda item: item.id)
        if any((endpoint.protocol or vendor.protocol) in RegistryProviderFactory.SUPPORTED_PROTOCOLS for endpoint in vendor.base_urls)
    ]
    if not choices:
        raise ValueError("Registry 中没有当前 Eval Runner 支持的 Vendor。")
    return reader.choice("选择 Vendor", choices)


def _choose_vendor_family(reader: InteractiveReader, registry: Any, vendor_id: str) -> str:
    vendor = registry.vendors[vendor_id]
    families = sorted({
        family_id for endpoint in vendor.base_urls
        if (endpoint.protocol or vendor.protocol) in RegistryProviderFactory.SUPPORTED_PROTOCOLS
        for family_id in endpoint.model_families
    })
    if not families:
        raise ValueError("该 Vendor 没有当前 Eval Runner 支持的 Model Family。")
    return reader.choice("选择 Model Family", [(registry.model_families[item].name, item) for item in families])


def _choose_vendor_base_url(reader: InteractiveReader, registry: Any, vendor_id: str, family_id: str) -> str:
    vendor = registry.vendors[vendor_id]
    choices = [
        (endpoint.id, endpoint.id) for endpoint in vendor.base_urls
        if family_id in endpoint.model_families
        and (endpoint.protocol or vendor.protocol) in RegistryProviderFactory.SUPPORTED_PROTOCOLS
    ]
    if not choices:
        raise ValueError("所选 Vendor/Model Family 没有可用 Base URL。")
    return choices[0][1] if len(choices) == 1 else reader.choice("选择 Base URL", choices)


def _choose_or_create_credential(
    reader: InteractiveReader,
    store: RegistryStore,
    service: CredentialService,
    registry: Any,
    vendor_id: str,
) -> str:
    candidates = [item for item in registry.credentials.values() if item.vendor_id == vendor_id]
    choices = [(item.name or item.id, item.id) for item in candidates]
    choices.append(("创建新的访问凭证", "__create__"))
    selected = reader.choice("选择已有 Credential", choices)
    if selected != "__create__":
        return selected
    return _create_credential_for_vendor(reader, store, service, registry, vendor_id)


def _create_credential_for_vendor(
    reader: InteractiveReader,
    store: RegistryStore,
    service: CredentialService,
    registry: Any,
    vendor_id: str,
) -> str:
    document, token = _credential_wizard(reader, registry, vendor_id=vendor_id)
    if document.get("env") is not None:
        store.create("credentials", document)
    else:
        assert token is not None
        service.create(document, token)
    print(f"已保存访问凭证：{document['name']}（内部 ID: {document['id']}）")
    return str(document["id"])


def _generated_identifier(prefix: str, hint: str, existing: Any) -> str:
    stem = "".join(character for character in hint.lower() if character.isalnum()) or "user"
    while True:
        identifier = f"{prefix}_{stem}_{uuid4().hex[:6]}"
        if identifier not in existing:
            return identifier


def _preset_document(
    identifier: str, name: str, vendor_id: str, base_url_id: str,
    credential_id: str, family_id: str, model_id: str,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "name": name,
        "vendor_id": vendor_id,
        "base_url_id": base_url_id,
        "credential_id": credential_id,
        "model_family_id": family_id,
        "default_model_id": model_id,
        "parameters": {},
    }


def _default_identifier(*parts: str) -> str:
    value = "-".join(parts).lower()
    return "".join(character if character.isalnum() or character in "_-" else "-" for character in value).strip("-")


def _bootstrap_problems(registry: Any, readiness: tuple[PresetReadiness, ...]) -> tuple[str, ...]:
    problems: list[str] = []
    if not registry.model_families or not any(item.models for item in registry.model_families.values()):
        problems.append("Missing model definition.")
    if not registry.credentials:
        problems.append("Missing credential: 请先创建 Credential。")
    if not readiness:
        problems.append("Missing preset: 请先创建 Preset。")
    for item in readiness:
        if not item.runnable:
            problems.extend(f"Preset {item.preset_id}: {error}" for error in item.blocking_errors)
    return tuple(dict.fromkeys(problems))


def _print_registry_summary(state: RegistryBootstrapState) -> None:
    print(
        "\nRegistry 状态\n"
        f"Vendor: {state.vendor_count}\n"
        f"Model Family: {state.model_family_count}\n"
        f"Models: {state.model_count}\n"
        f"Credentials: {state.credential_count}\n"
        f"Presets: {state.preset_count}\n"
        f"Runnable Presets: {state.runnable_preset_count}"
    )


def _preset_items(store: RegistryStore) -> dict[str, Any]:
    """Return independently readable presets; a broken neighbor remains a warning."""
    items: dict[str, Any] = {}
    for identifier in store.preset_ids():
        try:
            registry = store.registry_for_preset(identifier)
            items[identifier] = registry.presets[identifier]
        except (KeyError, ValueError):
            continue
    return items


def _manage_kind(reader: InteractiveReader, store: RegistryStore, service: CredentialService, secrets: LocalFileCredentialSecretStore, kind: str) -> None:
    if kind == "credentials":
        _manage_credentials(reader, store, service, secrets)
        return
    labels = {"vendors": "Vendor", "model_families": "Model Family", "presets": "Preset"}
    while True:
        try:
            action = reader.choice(f"{labels[kind]} 管理", [("查看", "view"), ("新增", "create"), ("修改", "edit"), ("删除", "delete"), ("返回", "back")])
        except InteractiveBack:
            return
        if action == "back": return
        registry = _configuration_registry(store)
        items = _preset_items(store) if kind == "presets" else getattr(registry, {"vendors": "vendors", "model_families": "model_families"}[kind])
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


def _manage_credentials(
    reader: InteractiveReader,
    store: RegistryStore,
    service: CredentialService,
    secrets: LocalFileCredentialSecretStore,
) -> None:
    registry = _configuration_registry(store)
    vendor_id = _choose_vendor(reader, registry)
    vendor = registry.vendors[vendor_id]
    while True:
        registry = _configuration_registry(store)
        credentials = [item for item in registry.credentials.values() if item.vendor_id == vendor_id]
        print(f"\n{vendor.name} Credentials")
        for index, item in enumerate(credentials, start=1):
            print(f"  {index}. {item.name or item.id}")
        choices: list[tuple[str, str]] = [
            (item.name or item.id, item.id) for item in credentials
        ]
        choices.extend([("创建新的", "__create__"), ("返回", "__back__")])
        selected = reader.choice("选择访问凭证", choices)
        if selected == "__back__":
            return
        try:
            if selected == "__create__":
                _create_credential_for_vendor(reader, store, service, registry, vendor_id)
                continue
            action = reader.choice("Credential 操作", [("查看", "view"), ("修改", "edit"), ("删除", "delete"), ("返回", "back")])
            if action == "view":
                _view("credentials", {selected: registry.credentials[selected]}, store, secrets)
            elif action == "edit":
                _edit_credential(reader, store, service, selected)
            elif action == "delete":
                _delete_credential(reader, store, service, selected)
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
            print(
                f"{item.name or item.id}\n"
                f"  Vendor: {item.vendor_id}\n"
                f"  Status: {item.status}\n"
                f"  Created: {item.created_at or '未知'}\n"
                f"  Updated: {item.updated_at or '未知'}\n"
                f"  Last Used: {item.last_used_at or 'Never'}\n"
                f"  Expires: {item.expires_at or 'Never'}\n"
                f"  Scope: {scope}"
            )
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
        document = _vendor_wizard(reader, _configuration_registry(store))
        if reader.confirm("确认保存 Vendor？", default=True, allow_back=True): store.create(kind, document); print("已保存。")
    elif kind == "model_families":
        document = _family_wizard(reader, _configuration_registry(store))
        if reader.confirm("确认保存 Model Family？", default=True, allow_back=True): store.create(kind, document); print("已保存。")
    elif kind == "credentials":
        registry = _configuration_registry(store)
        _create_credential_for_vendor(reader, store, service, registry, _choose_vendor(reader, registry))
    else:
        registry = _configuration_registry(store)
        if not registry.credentials:
            print("创建 Preset 前需要先创建 Credential。")
            return
        document = _preset_wizard(reader, registry)
        if reader.confirm("确认保存 Preset？", default=True, allow_back=True):
            store.create(kind, document)
            readiness = check_bootstrap_state(store, service.secret_store)
            print(f"已保存。Readiness: {'Runnable' if document['id'] in readiness.runnable_preset_ids else 'Not runnable'}")


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


def _credential_wizard(
    reader: InteractiveReader,
    registry: Any,
    existing: dict[str, Any] | None = None,
    *,
    vendor_id: str | None = None,
    immutable_vendor: bool = False,
    collect_token: bool = True,
) -> tuple[dict[str, Any], str | None]:
    draft, token_box = _canonical_relationship_fields(deepcopy(existing or {})), {}
    editing = existing is not None
    if editing: print(f"内部 Credential ID: {draft['id']}（不可修改）")
    elif vendor_id is not None: draft["id"] = _generated_identifier("cred", vendor_id, registry.credentials)
    def identifier(state: dict[str, Any]) -> None: state["id"] = _generated_identifier("cred", state["vendor_id"], registry.credentials)
    def name(state: dict[str, Any]) -> None: state["name"] = reader.text("名称: ", default=state.get("name"), required=True)
    def vendor(state: dict[str, Any]) -> None:
        if vendor_id is not None: state["vendor_id"] = vendor_id
        elif not immutable_vendor: state["vendor_id"] = _choose_vendor(reader, registry)
    def source(state: dict[str, Any]) -> None:
        current = state.get("source") or ("environment" if state.get("env") else "local")
        selected = reader.choice("访问凭证来源", [("本地 Secret（推荐）", "local"), ("环境变量", "environment")])
        state["source"] = selected
        if selected == "environment":
            state["env"] = reader.text("环境变量名: ", default=state.get("env") if current == "environment" else None, required=True)
            state.pop("secret_ref", None)
        else:
            state.pop("env", None)
            state["secret_ref"] = f"local:{state['id']}"
            if collect_token: token_box["token"] = reader.secret("请输入 Token（不回显）: ")
    def expiry(state: dict[str, Any]) -> None: _optional_field(reader, state, "expires_at", "过期日期 YYYY-MM-DD（可留空；clear 清除）")
    def status(state: dict[str, Any]) -> None:
        state["status"] = reader.choice(
            "状态", [("Active", "active"), ("Expired", "expired"), ("Disabled", "disabled")]
        )
    def notes(state: dict[str, Any]) -> None: _optional_field(reader, state, "notes", "备注（可留空；clear 清除）")
    if editing:
        steps: list[DraftStep] = [name, expiry, status, notes]
    else:
        steps = [vendor, identifier, name, source, expiry, notes]
    draft = _run_wizard(steps, draft)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    draft.setdefault("created_at", now); draft.setdefault("status", "active"); draft["updated_at"] = now
    return draft, token_box.get("token")


def _canonical_relationship_fields(document: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a legacy user document to the explicit relationship field names."""
    for canonical, legacy in (
        ("vendor_id", "vendor"),
        ("credential_id", "credential"),
        ("base_url_id", "base_url"),
        ("model_family_id", "model_family"),
        ("model_id", "model"),
    ):
        if canonical not in document and legacy in document:
            document[canonical] = document.pop(legacy)
    if "model_id" not in document and "default_model_id" in document:
        document["model_id"] = document.pop("default_model_id")
    return document


def _preset_wizard(reader: InteractiveReader, registry: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    draft = _canonical_relationship_fields(deepcopy(existing or {}))
    editing = existing is not None
    if editing: print(f"内部 Preset ID: {draft['id']}（不可修改）")
    def identifier(state: dict[str, Any]) -> None: state["id"] = _generated_identifier("preset", state["model_id"], registry.presets)
    def name(state: dict[str, Any]) -> None: state["name"] = reader.text("名称: ", default=state.get("name"), required=True)
    def vendor(state: dict[str, Any]) -> None: state["vendor_id"] = _choose_vendor(reader, registry)
    def family(state: dict[str, Any]) -> None: state["model_family_id"] = _choose_vendor_family(reader, registry, state["vendor_id"])
    def model(state: dict[str, Any]) -> None: state["model_id"] = reader.choice("选择默认 Model", [(key, key) for key in sorted(registry.model_families[state["model_family_id"]].models)])
    def url(state: dict[str, Any]) -> None: state["base_url_id"] = _choose_vendor_base_url(reader, registry, state["vendor_id"], state["model_family_id"])
    def credential(state: dict[str, Any]) -> None:
        candidates = [item for item in registry.credentials.values() if item.vendor_id == state["vendor_id"] and (not item.base_url_ids or state["base_url_id"] in item.base_url_ids)]
        if not candidates: raise ValueError("该 Vendor 尚无兼容的访问凭证；请先创建 Credential。")
        state["credential_id"] = reader.choice("选择 Credential", [(item.name or item.id, item.id) for item in candidates])
    def description(state: dict[str, Any]) -> None: _optional_field(reader, state, "description", "描述（可留空；clear 清除）")
    def notes(state: dict[str, Any]) -> None: _optional_field(reader, state, "notes", "备注（可留空；clear 清除）")
    draft = _run_wizard([vendor, family, model, url, credential, name] + ([] if editing else [identifier]) + [description, notes], draft)
    runtime = registry.resolve(vendor_id=draft["vendor_id"], base_url_id=draft["base_url_id"], credential_id=draft["credential_id"], model_family_id=draft["model_family_id"], model_id=draft["model_id"])
    draft["parameters"] = _semantic_parameters(reader, runtime.resolved_capabilities)
    draft["default_model_id"] = draft.pop("model_id")
    return draft


def _edit(reader: InteractiveReader, store: RegistryStore, service: CredentialService, kind: str, items: Any) -> None:
    choices = [(getattr(item, "name", None) or item.id, item.id) for item in items.values() if (store.user_root / kind / f"{item.id}.yaml").is_file()]
    if not choices: print("没有可修改的用户定义；内置定义只读。"); return
    identifier, document = reader.choice("选择要修改的用户定义", choices), None
    if kind == "credentials":
        _edit_credential(reader, store, service, identifier)
        return
    document = store.read_user_document(kind, identifier)
    registry = store.registry_for_preset(identifier) if kind == "presets" else _configuration_registry(store)
    document = _vendor_wizard(reader, registry, document) if kind == "vendors" else _family_wizard(reader, registry, document) if kind == "model_families" else _preset_wizard(reader, registry, document)
    if reader.confirm("确认保存修改？", default=True, allow_back=True): store.update(kind, identifier, document); print("已保存。")


def _edit_credential(reader: InteractiveReader, store: RegistryStore, service: CredentialService, identifier: str) -> None:
    document = store.read_user_document("credentials", identifier)
    document, token = _credential_wizard(
        reader, _configuration_registry(store), document, immutable_vendor=True, collect_token=False
    )
    replace_token = document.get("secret_ref") is not None and reader.confirm("确认更换本地 Token？", default=False, allow_back=True)
    token = reader.secret("新 Token（不回显）: ") if replace_token else None
    if reader.confirm("确认保存修改？", default=True, allow_back=True):
        if document.get("env") is not None:
            service.update(identifier, CredentialUpdateDraft(document))
        else:
            service.update(
                identifier,
                CredentialUpdateDraft(document, replace_token=replace_token, new_token=token),
            )
        print("已保存。")


def _delete_credential(reader: InteractiveReader, store: RegistryStore, service: CredentialService, identifier: str) -> None:
    references = store.references("credentials", identifier)
    if references:
        print("无法删除。请先删除或迁移引用该 Credential 的 Preset：")
        print("\n".join(f"- {item}" for item in references))
        return
    if reader.confirm("确认删除该 Credential？", default=False):
        service.delete(identifier)
        print("已删除。")


def _delete(reader: InteractiveReader, store: RegistryStore, service: CredentialService, kind: str, items: Any) -> None:
    choices = [(getattr(item, "name", None) or item.id, item.id) for item in items.values() if (store.user_root / kind / f"{item.id}.yaml").is_file()]
    if not choices: print("没有可删除的用户定义；内置定义只读。"); return
    identifier = reader.choice("选择要删除的用户定义", choices)
    if kind == "credentials":
        _delete_credential(reader, store, service, identifier)
        return
    if store.references(kind, identifier): raise ValueError("该对象仍被引用，不能删除。")
    if reader.confirm(f"确认删除 {identifier}？", default=False):
        if kind == "credentials": service.delete(identifier)
        else: store.delete(kind, identifier)
        print("已删除。")


def _status(store: RegistryStore, service: CredentialService, secrets: LocalFileCredentialSecretStore) -> None:
    registry = _configuration_registry(store)
    secret_ids = secrets.list_ids()
    local_credentials = {
        item.id for item in registry.credentials.values()
        if item.secret_reference == f"local:{item.id}"
    }
    expired = sum(
        item.status == "expired" or (
            item.expires_at is not None and item.expires_at < datetime.now().date().isoformat()
        )
        for item in registry.credentials.values()
    )
    state = check_bootstrap_state(store, secrets)
    _print_registry_summary(state)
    print(
        "\nRegistry Summary\n"
        f"Validation: PASS\n"
        f"Secret Store: {'Ready' if secrets.path.exists() else 'Empty'}\n"
        f"Secrets: {len(secret_ids)}\n"
        f"Orphan Secrets: {len(secret_ids - local_credentials)}\n"
        f"Missing Secrets: {len(local_credentials - secret_ids)}\n"
        f"Expired Credentials: {expired}"
    )
    if state.problems:
        print("\nProblems:\n" + "\n".join(f"- {item}" for item in state.problems))
    by_vendor: dict[str, int] = {}
    for credential in registry.credentials.values():
        by_vendor[credential.vendor_id] = by_vendor.get(credential.vendor_id, 0) + 1
    print("\nCredentials by Vendor:")
    for vendor_id, count in sorted(by_vendor.items()):
        print(f"{registry.vendors[vendor_id].name}: {count}")


def _semantic_parameters(reader: InteractiveReader, capabilities: Any) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    structured = capabilities.get("structured_output", {})
    if structured.get("supported"):
        modes = set(structured.get("modes", ()))
        choices: list[tuple[str, str | None]] = [("普通文本", None)]
        if "json_object" in modes:
            choices.append(("JSON Object", "json_object"))
        if "strict_json_schema" in modes:
            choices.append(("Strict JSON Schema（高级）", "strict_json_schema"))
        selected = reader.choice("输出模式", choices)
        if selected is not None:
            parameters["structured_output"] = selected
    thinking = capabilities.get("thinking", {})
    if thinking.get("supported"):
        values = set(thinking.get("allowed_values", ()))
        choices = [("默认", None)]
        if "enabled" in values:
            choices.append(("开启推理", "enabled"))
        if "disabled" in values:
            choices.append(("关闭推理", "disabled"))
        selected = reader.choice("推理模式", choices)
        if selected is not None:
            parameters["thinking"] = selected
    tokens = capabilities.get("max_output_tokens", {})
    if tokens.get("supported"):
        value = reader.text("Max Output Tokens（留空表示使用模型默认限制）: ")
        if value:
            if not value.isdigit() or int(value) <= 0:
                raise ValueError("Max Output Tokens 必须是正整数。")
            parameters["max_output_tokens"] = int(value)
    return parameters
# Modified by AI on 2026-09-10 15:20:16
