"""Validated, secret-free Model Registry Foundation contract.

Only this module reads registry documents.  It resolves semantic parameters to
wire parameters, so future CLI and execution code do not parse registry YAML.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping


DEFAULT_MODEL_REGISTRY_ROOT = Path(__file__).resolve().parents[1] / "model_registry"
SUPPORTED_PROTOCOLS = frozenset({"openai_compatible_chat", "openai_responses", "anthropic_messages", "gemini_generate"})
_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_ENTITY_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
_MODEL_ID = re.compile(r"^[a-z][a-z0-9_.-]*$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_FIELDS = frozenset({"apikey", "accesstoken", "token", "secret", "authorization", "bearer", "password"})


class ModelRegistryError(ValueError):
    """Base error for registry parsing, validation, and resolution."""


class RegistryValidationError(ModelRegistryError):
    """Registry definitions violate the Foundation contract."""


class RegistryResolutionError(ModelRegistryError):
    """A valid registry cannot resolve a requested runtime."""


@dataclass(frozen=True)
class VendorBaseURL:
    id: str
    url: str
    model_families: tuple[str, ...]
    default_for: tuple[str, ...]
    protocol: str | None
    display_type: str | None
    notes: str | None
    capabilities: Mapping[str, Mapping[str, Any]]
    parameter_mapping: Mapping[str, str]
    transport_defaults: Mapping[str, Any]
    model_overrides: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class Vendor:
    id: str
    name: str
    protocol: str | None
    base_urls: tuple[VendorBaseURL, ...]
    website: str | None
    description: str | None
    notes: str | None
    category: str | None
    capabilities: Mapping[str, Mapping[str, Any]]
    parameter_mapping: Mapping[str, str]
    transport_defaults: Mapping[str, Any]


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    api_name: str | None
    context_window: int | None
    capabilities: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class ModelFamily:
    id: str
    name: str
    context_window: int | None
    capabilities: Mapping[str, Mapping[str, Any]]
    models: Mapping[str, ModelDefinition]
    description: str | None = None


@dataclass(frozen=True)
class Credential:
    id: str
    vendor_id: str
    environment_variable: str | None
    secret_reference: str | None
    name: str | None
    base_url_ids: tuple[str, ...]
    expires_at: str | None
    created_at: str | None
    updated_at: str | None
    description: str | None
    notes: str | None


@dataclass(frozen=True)
class Preset:
    id: str
    name: str | None
    vendor_id: str
    base_url_id: str
    credential_id: str
    model_family_id: str
    model_id: str
    parameters: Mapping[str, Any]
    description: str | None
    notes: str | None


@dataclass(frozen=True)
class ResolvedModelRuntime:
    """Unified, immutable and secret-free runtime contract."""

    vendor_id: str
    vendor_name: str
    base_url_id: str
    base_url: str
    protocol: str
    model_family_id: str
    model_id: str
    requested_model_name: str
    api_model_name: str
    credential_id: str
    preset_id: str | None
    resolved_capabilities: Mapping[str, Mapping[str, Any]]
    semantic_parameters: Mapping[str, Any]
    wire_parameters: Mapping[str, Any]
    transport_defaults: Mapping[str, Any]
    provenance: Mapping[str, Any]
    context_window: int | None

    def run_record(self, role: str) -> dict[str, Any]:
        if not isinstance(role, str) or not role.strip():
            raise RegistryResolutionError("role must be a non-empty string")
        return {
            "vendor_id": self.vendor_id, "base_url_id": self.base_url_id,
            "model_family_id": self.model_family_id, "model_id": self.model_id,
            "credential_id": self.credential_id, "preset_id": self.preset_id,
            "semantic_parameters": _copy(self.semantic_parameters),
            "wire_parameters": _copy(self.wire_parameters), "role": role,
        }


class ModelRegistry:
    """Pure V1.3A registry.  No legacy profile reader or migration exists."""

    def __init__(
        self,
        root: Path | str = DEFAULT_MODEL_REGISTRY_ROOT,
        *,
        user_root: Path | str | None = None,
    ) -> None:
        self.root = Path(root)
        self.user_root = Path(user_root) if user_root is not None else None
        self.vendors = _freeze(self._load_vendors())
        self.model_families = _freeze(self._load_families())
        self.credentials = _freeze(self._load_credentials())
        self.presets = _freeze(self._load_presets())
        self.validate()

    def validate(self) -> None:
        """Check all cross-document references at initialization time."""
        errors: list[str] = []
        models: dict[str, str] = {}
        for family in self.model_families.values():
            for model_id in family.models:
                former = models.setdefault(model_id, family.id)
                if former != family.id:
                    errors.append(f"model '{model_id}' belongs to both '{former}' and '{family.id}'")
        for vendor in self.vendors.values():
            defaults: dict[str, str] = {}
            for endpoint in vendor.base_urls:
                protocol = endpoint.protocol or vendor.protocol
                if protocol is None:
                    errors.append(f"Vendor '{vendor.id}', base URL '{endpoint.id}': protocol is required")
                elif protocol not in SUPPORTED_PROTOCOLS:
                    errors.append(f"Vendor '{vendor.id}', base URL '{endpoint.id}': unsupported protocol '{protocol}'")
                for family_id in endpoint.model_families:
                    if family_id not in self.model_families:
                        errors.append(f"Vendor '{vendor.id}', base URL '{endpoint.id}': unknown model family '{family_id}'")
                for family_id in endpoint.default_for:
                    if family_id not in endpoint.model_families:
                        errors.append(f"Vendor '{vendor.id}', base URL '{endpoint.id}': default_for '{family_id}' is unsupported")
                    former = defaults.setdefault(family_id, endpoint.id)
                    if former != endpoint.id:
                        errors.append(f"Vendor '{vendor.id}': model family '{family_id}' has multiple default base URLs ('{former}', '{endpoint.id}')")
                for model_id, override in endpoint.model_overrides.items():
                    family_id = models.get(model_id)
                    if family_id is None:
                        errors.append(f"Vendor '{vendor.id}', base URL '{endpoint.id}': override references unknown model '{model_id}'")
                    elif family_id not in endpoint.model_families:
                        errors.append(f"Vendor '{vendor.id}', base URL '{endpoint.id}': override '{model_id}' belongs to unsupported family '{family_id}'")
                    _validate_override(override, f"Vendor '{vendor.id}', base URL '{endpoint.id}', model '{model_id}'", errors)
        for credential in self.credentials.values():
            if credential.vendor_id not in self.vendors:
                errors.append(f"Credential '{credential.id}': unknown vendor '{credential.vendor_id}'")
            else:
                vendor = self.vendors[credential.vendor_id]
                for base_url_id in credential.base_url_ids:
                    if _endpoint(vendor, base_url_id) is None:
                        errors.append(f"Credential '{credential.id}': vendor '{vendor.id}' has no base URL '{base_url_id}'")
            if (credential.environment_variable is None) == (credential.secret_reference is None):
                errors.append(f"Credential '{credential.id}': specify exactly one of env or secret_ref")
            if credential.expires_at is not None:
                try:
                    from datetime import date
                    date.fromisoformat(credential.expires_at)
                except ValueError:
                    errors.append(f"Credential '{credential.id}': expires_at must use YYYY-MM-DD")
        for preset in self.presets.values():
            vendor = self.vendors.get(preset.vendor_id)
            credential = self.credentials.get(preset.credential_id)
            family = self.model_families.get(preset.model_family_id)
            if vendor is None:
                errors.append(f"Preset '{preset.id}': unknown vendor '{preset.vendor_id}'")
                continue
            if credential is None:
                errors.append(f"Preset '{preset.id}': unknown credential '{preset.credential_id}'")
            elif credential.vendor_id != vendor.id:
                errors.append(f"Preset '{preset.id}': credential '{credential.id}' belongs to vendor '{credential.vendor_id}', but preset vendor is '{vendor.id}'")
            elif credential.base_url_ids and preset.base_url_id not in credential.base_url_ids:
                errors.append(f"Preset '{preset.id}': credential '{credential.id}' is not scoped to base URL '{preset.base_url_id}'")
            if family is None:
                errors.append(f"Preset '{preset.id}': unknown model family '{preset.model_family_id}'")
                continue
            model = family.models.get(preset.model_id)
            if model is None:
                errors.append(f"Preset '{preset.id}': model '{preset.model_id}' is not declared by model family '{family.id}'")
                continue
            endpoint = _endpoint(vendor, preset.base_url_id)
            if endpoint is None:
                errors.append(f"Preset '{preset.id}': vendor '{vendor.id}' has no base URL '{preset.base_url_id}'")
                continue
            if family.id not in endpoint.model_families:
                errors.append(f"Preset '{preset.id}': base URL '{endpoint.id}' does not support model family '{family.id}'")
                continue
            try:
                self._resolve_parts(vendor, endpoint, family, model, preset.parameters)
            except RegistryResolutionError as error:
                errors.append(f"Preset '{preset.id}': {error}")
        if errors:
            raise RegistryValidationError("Registry validation failed:\n- " + "\n- ".join(errors))

    def resolve(self, *, vendor_id: str | None = None, credential_id: str | None = None,
                model_family_id: str | None = None, model_id: str | None = None,
                base_url_id: str | None = None, preset_id: str | None = None,
                semantic_parameters: Mapping[str, Any] | None = None,
                transport_overrides: Mapping[str, Any] | None = None) -> ResolvedModelRuntime:
        preset = self._lookup(self.presets, preset_id, "preset") if preset_id else None
        choices = {"vendor_id": vendor_id, "credential_id": credential_id,
                   "model_family_id": model_family_id, "model_id": model_id, "base_url_id": base_url_id}
        if preset:
            required = {"vendor_id": preset.vendor_id, "credential_id": preset.credential_id,
                        "model_family_id": preset.model_family_id, "model_id": preset.model_id, "base_url_id": preset.base_url_id}
            for key, value in required.items():
                if choices[key] is not None and choices[key] != value:
                    raise RegistryResolutionError(f"preset '{preset.id}' conflicts with explicit {key} '{choices[key]}'")
                choices[key] = choices[key] or value
        vendor = self._lookup(self.vendors, choices["vendor_id"], "vendor")
        credential = self._lookup(self.credentials, choices["credential_id"], "credential")
        family = self._lookup(self.model_families, choices["model_family_id"], "model family")
        model = self._lookup(family.models, choices["model_id"], "model")
        if credential.vendor_id != vendor.id:
            raise RegistryResolutionError(f"credential '{credential.id}' belongs to vendor '{credential.vendor_id}', not '{vendor.id}'")
        endpoint, selection = self._select_base_url(vendor, family.id, choices["base_url_id"])
        if credential.base_url_ids and endpoint.id not in credential.base_url_ids:
            raise RegistryResolutionError(f"credential '{credential.id}' is not scoped to base URL '{endpoint.id}'")
        parameters = _merge(preset.parameters if preset else {}, semantic_parameters or {})
        parts = self._resolve_parts(vendor, endpoint, family, model, parameters)
        return ResolvedModelRuntime(
            vendor_id=vendor.id, vendor_name=vendor.name, base_url_id=endpoint.id, base_url=endpoint.url,
            protocol=_protocol(vendor, endpoint), model_family_id=family.id, model_id=model.id,
            requested_model_name=model.id, api_model_name=parts["api_model_name"], credential_id=credential.id,
            preset_id=preset.id if preset else None, resolved_capabilities=_freeze(parts["capabilities"]),
            semantic_parameters=_freeze(parameters), wire_parameters=_freeze(parts["wire_parameters"]),
            transport_defaults=_freeze(_merge(vendor.transport_defaults, endpoint.transport_defaults, transport_overrides or {})),
            provenance=_freeze({"base_url_selection": selection, "base_url_protocol_override": endpoint.protocol is not None,
                                "api_name_override": parts["api_model_name"] != model.id}),
            context_window=model.context_window or family.context_window,
        )

    @staticmethod
    def _resolve_parts(vendor: Vendor, endpoint: VendorBaseURL, family: ModelFamily,
                       model: ModelDefinition, parameters: Mapping[str, Any]) -> dict[str, Any]:
        override = endpoint.model_overrides.get(model.id, {})
        capabilities = _merge(family.capabilities, model.capabilities, vendor.capabilities,
                              endpoint.capabilities, override.get("capabilities", {}))
        mapping = _merge(vendor.parameter_mapping, endpoint.parameter_mapping, override.get("parameter_mapping", {}))
        _validate_parameters(parameters, capabilities)
        wire: dict[str, Any] = {}
        for name, value in parameters.items():
            wire_name = mapping.get(name, name)
            if wire_name in wire:
                raise RegistryResolutionError(f"parameter mapping maps multiple parameters to '{wire_name}'")
            wire[wire_name] = _copy(value)
        return {"api_model_name": override.get("api_name") or model.api_name or model.id,
                "capabilities": capabilities, "wire_parameters": wire}

    def _load_vendors(self) -> dict[str, Vendor]:
        result: dict[str, Vendor] = {}
        for path, data in self._documents("vendors"):
            _secret_guard(data, path); _fields(data, {"id", "name", "website", "description", "notes", "category", "protocol", "base_urls", "capabilities", "parameter_mapping", "transport_defaults"}, path)
            urls = tuple(self._base_url(item, path) for item in _list(data, "base_urls", path))
            if not urls or len({item.id for item in urls}) != len(urls):
                raise RegistryValidationError(f"{path}: base URL ids must be present and unique")
            identifier = _id(data, "id", path)
            result[identifier] = Vendor(identifier, _string(data, "name", path), _optional_protocol(data, "protocol", path), urls,
                _optional(data, "website", path), _optional(data, "description", path), _optional(data, "notes", path),
                _category(data, path), _capabilities(data.get("capabilities", {}), str(path)),
                _mapping(data.get("parameter_mapping", {}), str(path)), _object(data, "transport_defaults", path))
        return result

    def _load_families(self) -> dict[str, ModelFamily]:
        result: dict[str, ModelFamily] = {}
        for path, data in self._documents("model_families"):
            _secret_guard(data, path); _fields(data, {"id", "name", "description", "defaults", "models"}, path)
            defaults = _object(data, "defaults", path); _fields(defaults, {"context_window", "capabilities"}, path, "defaults")
            models: dict[str, ModelDefinition] = {}
            for model_id, config in _object_required(data, "models", path).items():
                if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id): raise RegistryValidationError(f"{path}: invalid model id '{model_id}'")
                if not isinstance(config, Mapping): raise RegistryValidationError(f"{path}: model '{model_id}' must be an object")
                _fields(config, {"api_name", "context_window", "capabilities"}, path, f"model '{model_id}'")
                models[model_id] = ModelDefinition(model_id, _optional(config, "api_name", path), _positive(config, "context_window", path), _capabilities(config.get("capabilities", {}), f"{path}: model '{model_id}'"))
            if not models: raise RegistryValidationError(f"{path}: models must not be empty")
            identifier = _id(data, "id", path)
            result[identifier] = ModelFamily(identifier, _string(data, "name", path), _positive(defaults, "context_window", path), _capabilities(defaults.get("capabilities", {}), f"{path}: defaults"), _freeze(models), _optional(data, "description", path))
        return result

    def _load_credentials(self) -> dict[str, Credential]:
        result: dict[str, Credential] = {}
        for path, data in self._documents("credentials"):
            _secret_guard(data, path); _fields(data, {"id", "vendor", "env", "secret_ref", "name", "base_url_ids", "expires_at", "created_at", "updated_at", "description", "notes"}, path)
            env = _optional(data, "env", path)
            if env and not _ENV.fullmatch(env): raise RegistryValidationError(f"{path}: env must be a valid environment variable name")
            identifier = _id(data, "id", path)
            base_url_ids = tuple(_entity_id_value(value, path, "base_url_ids") for value in data.get("base_url_ids", []))
            if len(base_url_ids) != len(set(base_url_ids)):
                raise RegistryValidationError(f"{path}: base_url_ids must be unique")
            result[identifier] = Credential(
                identifier, _id(data, "vendor", path), env, _optional(data, "secret_ref", path),
                _optional(data, "name", path), base_url_ids, _optional(data, "expires_at", path),
                _optional(data, "created_at", path), _optional(data, "updated_at", path),
                _optional(data, "description", path), _optional(data, "notes", path),
            )
        return result

    def _load_presets(self) -> dict[str, Preset]:
        result: dict[str, Preset] = {}
        for path, data in self._documents("presets"):
            _secret_guard(data, path); _fields(data, {"id", "name", "vendor", "base_url", "credential", "model_family", "model", "parameters", "description", "notes"}, path)
            identifier = _id(data, "id", path)
            result[identifier] = Preset(identifier, _optional(data, "name", path), _id(data, "vendor", path), _id(data, "base_url", path), _id(data, "credential", path), _id(data, "model_family", path), _string(data, "model", path), _object(data, "parameters", path), _optional(data, "description", path), _optional(data, "notes", path))
        return result

    @staticmethod
    def _base_url(data: Any, path: Path) -> VendorBaseURL:
        if not isinstance(data, Mapping): raise RegistryValidationError(f"{path}: base_urls entries must be objects")
        _fields(data, {"id", "url", "protocol", "type", "notes", "model_families", "default_for", "capabilities", "parameter_mapping", "transport_defaults", "model_overrides"}, path, "base URL")
        families = tuple(_id_value(item, path, "model_families") for item in _list(data, "model_families", path))
        defaults = tuple(_id_value(item, path, "default_for") for item in data.get("default_for", []))
        if not families or len(set(families)) != len(families) or len(set(defaults)) != len(defaults): raise RegistryValidationError(f"{path}: base URL family lists must be non-empty and unique")
        overrides = _object(data, "model_overrides", path)
        for model_id, config in overrides.items():
            if not isinstance(model_id, str) or not isinstance(config, Mapping): raise RegistryValidationError(f"{path}: model_overrides must map model ids to objects")
        return VendorBaseURL(_id(data, "id", path), _string(data, "url", path), families, defaults, _optional_protocol(data, "protocol", path), _optional(data, "type", path), _optional(data, "notes", path), _capabilities(data.get("capabilities", {}), f"{path}: base URL"), _mapping(data.get("parameter_mapping", {}), f"{path}: base URL"), _object(data, "transport_defaults", path), _freeze(overrides))

    def _documents(self, directory: str):
        folders = [self.root / directory]
        if self.user_root is not None:
            folders.append(self.user_root / directory)
        if not folders[0].is_dir(): raise RegistryValidationError(f"model registry directory does not exist: {folders[0]}")
        paths = [
            path for folder in folders if folder.is_dir()
            for path in sorted((*folder.glob("*.yaml"), *folder.glob("*.yml")))
        ]
        if not paths: raise RegistryValidationError(f"model registry directory is empty: {folders[0]}")
        identifiers: set[str] = set()
        for path in paths:
            data = _json(path); identifier = _id(data, "id", path)
            if identifier in identifiers: raise RegistryValidationError(f"duplicate {directory} id '{identifier}'")
            identifiers.add(identifier); yield path, data

    @staticmethod
    def _lookup(items: Mapping[str, Any], identifier: str | None, label: str) -> Any:
        if not isinstance(identifier, str) or not identifier: raise RegistryResolutionError(f"{label} is required")
        try: return items[identifier]
        except KeyError as error: raise RegistryResolutionError(f"unknown {label} '{identifier}'") from error

    @staticmethod
    def _select_base_url(vendor: Vendor, family: str, requested: str | None) -> tuple[VendorBaseURL, str]:
        if requested:
            endpoint = _endpoint(vendor, requested)
            if endpoint is None: raise RegistryResolutionError(f"vendor '{vendor.id}' has no base URL '{requested}'")
            if family not in endpoint.model_families: raise RegistryResolutionError(f"base URL '{endpoint.id}' does not support model family '{family}'")
            return endpoint, "explicit"
        matches = [item for item in vendor.base_urls if family in item.model_families]
        if len(matches) == 1: return matches[0], "single_match"
        defaults = [item for item in matches if family in item.default_for]
        if len(defaults) == 1: return defaults[0], "family_default"
        if not matches: raise RegistryResolutionError(f"vendor '{vendor.id}' has no base URL for model family '{family}'")
        raise RegistryResolutionError(f"AMBIGUOUS_BASE_URL: vendor '{vendor.id}' has {len(matches)} base URLs for model family '{family}'; select base_url_id explicitly")


def _json(path: Path) -> Mapping[str, Any]:
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error: raise RegistryValidationError(f"{path}: invalid JSON-compatible YAML ({error})") from error
    if not isinstance(value, Mapping): raise RegistryValidationError(f"{path}: registry entry must be an object")
    return value


def _validate_parameters(parameters: Mapping[str, Any], capabilities: Mapping[str, Mapping[str, Any]]) -> None:
    for name, value in parameters.items():
        rule = capabilities.get(name)
        if rule is None: raise RegistryResolutionError(f"unknown semantic parameter '{name}'")
        if not rule["supported"]: raise RegistryResolutionError(f"semantic parameter '{name}' is unsupported")
        allowed = rule.get("allowed_values") or rule.get("modes")
        if allowed is not None and value not in allowed: raise RegistryResolutionError(f"semantic parameter '{name}' value {value!r} is not allowed; expected one of {list(allowed)!r}")


def _validate_override(override: Mapping[str, Any], context: str, errors: list[str]) -> None:
    unknown = set(override) - {"api_name", "capabilities", "parameter_mapping"}
    if unknown: errors.append(f"{context}: unsupported override fields {', '.join(sorted(unknown))}")
    if "api_name" in override and (not isinstance(override["api_name"], str) or not override["api_name"]): errors.append(f"{context}: api_name must be a non-empty string")
    try: _capabilities(override.get("capabilities", {}), context); _mapping(override.get("parameter_mapping", {}), context)
    except RegistryValidationError as error: errors.append(str(error))


def _capabilities(value: Any, context: str) -> Mapping[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping): raise RegistryValidationError(f"{context}: capabilities must be an object")
    result: dict[str, Mapping[str, Any]] = {}
    for name, rule in value.items():
        if not isinstance(name, str) or not _ID.fullmatch(name) or not isinstance(rule, Mapping): raise RegistryValidationError(f"{context}: capabilities must map semantic identifiers to objects")
        unknown = set(rule) - {"supported", "allowed_values", "modes"}
        if unknown or not isinstance(rule.get("supported"), bool): raise RegistryValidationError(f"{context}: capability '{name}' requires boolean supported and no unknown fields")
        item = {"supported": rule["supported"]}
        for key in ("allowed_values", "modes"):
            if key in rule:
                values = rule[key]
                if not isinstance(values, (list, tuple)) or not values or any(isinstance(x, (dict, list)) for x in values): raise RegistryValidationError(f"{context}: capability '{name}.{key}' must be a non-empty scalar list")
                item[key] = tuple(_copy(x) for x in values)
        result[name] = _freeze(item)
    return _freeze(result)


def _mapping(value: Any, context: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping): raise RegistryValidationError(f"{context}: parameter_mapping must be an object")
    result: dict[str, str] = {}
    for semantic, wire in value.items():
        if not isinstance(semantic, str) or not _ID.fullmatch(semantic) or not isinstance(wire, str) or not _ID.fullmatch(wire): raise RegistryValidationError(f"{context}: parameter_mapping must map semantic identifiers to wire identifiers")
        result[semantic] = wire
    return _freeze(result)


def _protocol(vendor: Vendor, endpoint: VendorBaseURL) -> str:
    return endpoint.protocol or vendor.protocol or ""


def _endpoint(vendor: Vendor, identifier: str) -> VendorBaseURL | None:
    return next((item for item in vendor.base_urls if item.id == identifier), None)


def _id(data: Mapping[str, Any], field: str, path: Path) -> str:
    value = _string(data, field, path)
    if not _ENTITY_ID.fullmatch(value): raise RegistryValidationError(f"{path}: '{field}' must be a lowercase identifier")
    return value


def _id_value(value: Any, path: Path, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value): raise RegistryValidationError(f"{path}: '{field}' entries must be lowercase identifiers")
    return value


def _entity_id_value(value: Any, path: Path, field: str) -> str:
    if not isinstance(value, str) or not _ENTITY_ID.fullmatch(value):
        raise RegistryValidationError(f"{path}: '{field}' entries must be lowercase identifiers")
    return value


def _string(data: Mapping[str, Any], field: str, path: Path) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value: raise RegistryValidationError(f"{path}: '{field}' must be a non-empty string")
    return value


def _optional(data: Mapping[str, Any], field: str, path: Path) -> str | None:
    value = data.get(field)
    if value is None: return None
    if not isinstance(value, str) or not value: raise RegistryValidationError(f"{path}: '{field}' must be a non-empty string when set")
    return value


def _positive(data: Mapping[str, Any], field: str, path: Path) -> int | None:
    value = data.get(field)
    if value is None: return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0: raise RegistryValidationError(f"{path}: '{field}' must be a positive integer")
    return value


def _optional_protocol(data: Mapping[str, Any], field: str, path: Path) -> str | None:
    value = _optional(data, field, path)
    if value is not None and value not in SUPPORTED_PROTOCOLS: raise RegistryValidationError(f"{path}: unsupported protocol '{value}'")
    return value


def _category(data: Mapping[str, Any], path: Path) -> str | None:
    value = _optional(data, "category", path)
    if value is not None and value not in {"official", "relay", "enterprise", "local"}: raise RegistryValidationError(f"{path}: unsupported display category '{value}'")
    return value


def _list(data: Mapping[str, Any], field: str, path: Path) -> list[Any]:
    value = data.get(field)
    if not isinstance(value, list): raise RegistryValidationError(f"{path}: '{field}' must be a list")
    return value


def _object(data: Mapping[str, Any], field: str, path: Path) -> Mapping[str, Any]:
    value = data.get(field, {})
    if not isinstance(value, Mapping): raise RegistryValidationError(f"{path}: '{field}' must be an object")
    return _freeze(value)


def _object_required(data: Mapping[str, Any], field: str, path: Path) -> Mapping[str, Any]:
    if field not in data: raise RegistryValidationError(f"{path}: '{field}' must be an object")
    return _object(data, field, path)


def _fields(data: Mapping[str, Any], allowed: set[str], path: Path, context: str = "entry") -> None:
    unknown = set(data) - allowed
    if unknown: raise RegistryValidationError(f"{path}: {context} has unsupported fields {', '.join(sorted(map(str, unknown)))}")


def _secret_guard(data: Mapping[str, Any], path: Path) -> None:
    for key, value in data.items():
        normalized = re.sub(r"[-_]", "", key.lower()) if isinstance(key, str) else ""
        if normalized in _SECRET_FIELDS: raise RegistryValidationError(f"{path}: secret-bearing field '{key}' is forbidden")
        if isinstance(value, Mapping): _secret_guard(value, path)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping): _secret_guard(item, path)


def _merge(*layers: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items(): result[key] = _merge(result[key], value) if isinstance(value, Mapping) and isinstance(result.get(key), Mapping) else _copy(value)
    return result


def _copy(value: Any) -> Any:
    if isinstance(value, Mapping): return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)): return tuple(_copy(item) for item in value)
    return deepcopy(value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping): return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)): return tuple(_freeze(item) for item in value)
    return value
