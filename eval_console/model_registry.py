"""Model registry parsing and deterministic model configuration resolution.

The registry stores only identifiers and credential references.  Secrets remain
outside of the repository and are resolved by provider code in a later stage.
Configuration files use JSON-compatible YAML so the console has no additional
runtime parser dependency.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_MODEL_REGISTRY_ROOT = Path(__file__).resolve().parents[1] / "model_registry"
_SECRET_FIELD_NAMES = {
    "api_key",
    "access_key",
    "password",
    "secret",
    "token",
}
_CREDENTIAL_SOURCES = {"env", "local", "runtime"}


class ModelRegistryError(ValueError):
    """Raised when model registry data is invalid or cannot be resolved."""


@dataclass(frozen=True)
class VendorBaseURL:
    """A vendor endpoint that advertises compatible model families."""

    id: str
    url: str
    model_families: tuple[str, ...]
    display_type: str | None
    model_overrides: Mapping[str, Mapping[str, Any]]
    capabilities: Mapping[str, Any]


@dataclass(frozen=True)
class Vendor:
    id: str
    name: str
    protocol: str
    base_urls: tuple[VendorBaseURL, ...]
    website: str | None
    description: str | None
    model_overrides: Mapping[str, Mapping[str, Any]]
    capabilities: Mapping[str, Any]


@dataclass(frozen=True)
class ModelDefinition:
    id: str
    api_name: str | None
    configuration: Mapping[str, Any]


@dataclass(frozen=True)
class ModelFamily:
    id: str
    name: str
    defaults: Mapping[str, Any]
    models: Mapping[str, ModelDefinition]


@dataclass(frozen=True)
class Credential:
    id: str
    vendor_id: str
    source: str
    environment_variable: str | None
    description: str | None


@dataclass(frozen=True)
class Preset:
    id: str
    vendor_id: str
    credential_id: str
    model_id: str
    model_family_id: str | None
    base_url_id: str | None
    parameters: Mapping[str, Any]
    description: str | None


@dataclass(frozen=True)
class ResolvedModelConfiguration:
    """The secret-free provider configuration selected for one runtime role."""

    vendor_id: str
    base_url_id: str
    model_family_id: str
    model_id: str
    credential_id: str
    preset_id: str | None
    protocol: str
    base_url: str
    api_name: str
    capabilities: Mapping[str, Any]
    model_configuration: Mapping[str, Any]
    runtime_parameters: Mapping[str, Any]

    def run_record(self, role: str) -> dict[str, Any]:
        """Return the future run-record payload without credential secrets."""
        if not isinstance(role, str) or not role.strip():
            raise ModelRegistryError("role must be a non-empty string")
        return {
            "vendor_id": self.vendor_id,
            "base_url_id": self.base_url_id,
            "model_family_id": self.model_family_id,
            "model_id": self.model_id,
            "credential_id": self.credential_id,
            "preset_id": self.preset_id,
            "runtime_parameters": deepcopy(dict(self.runtime_parameters)),
            "role": role,
        }


class ModelRegistry:
    """Load a registry and resolve presets into a unified model configuration."""

    def __init__(self, root: Path | str = DEFAULT_MODEL_REGISTRY_ROOT) -> None:
        self.root = Path(root)
        self.vendors = self._load_vendors()
        self.model_families = self._load_model_families()
        self.credentials = self._load_credentials()
        self.presets = self._load_presets()

    def resolve(
        self,
        *,
        vendor_id: str | None = None,
        credential_id: str | None = None,
        model_id: str | None = None,
        model_family_id: str | None = None,
        base_url_id: str | None = None,
        preset_id: str | None = None,
        runtime_parameters: Mapping[str, Any] | None = None,
    ) -> ResolvedModelConfiguration:
        """Resolve explicit selections and an optional preset into one model call shape."""
        preset = self._get_optional(self.presets, preset_id, "preset")
        values = {
            "vendor_id": vendor_id,
            "credential_id": credential_id,
            "model_id": model_id,
            "model_family_id": model_family_id,
            "base_url_id": base_url_id,
        }
        if preset is not None:
            values = self._merge_preset_values(values, preset)

        selected_vendor_id = self._required_value(values["vendor_id"], "vendor_id")
        selected_credential_id = self._required_value(
            values["credential_id"], "credential_id"
        )
        selected_model_id = self._required_value(values["model_id"], "model_id")
        vendor = self._get_required(self.vendors, selected_vendor_id, "vendor")
        credential = self._get_required(
            self.credentials, selected_credential_id, "credential"
        )
        if credential.vendor_id != vendor.id:
            raise ModelRegistryError(
                f"credential '{credential.id}' belongs to vendor "
                f"'{credential.vendor_id}', not '{vendor.id}'"
            )

        family = self._select_model_family(
            selected_model_id, values["model_family_id"]
        )
        model = family.models[selected_model_id]
        endpoint = self._select_base_url(vendor, family.id, values["base_url_id"])
        capabilities = self._resolved_capabilities(vendor, endpoint, family, model)
        api_name = self._resolved_api_name(vendor, endpoint, model)
        model_configuration = _deep_merge(family.defaults, model.configuration)
        model_configuration["capabilities"] = deepcopy(capabilities)
        parameters = _deep_merge(
            preset.parameters if preset is not None else {}, runtime_parameters or {}
        )
        return ResolvedModelConfiguration(
            vendor_id=vendor.id,
            base_url_id=endpoint.id,
            model_family_id=family.id,
            model_id=model.id,
            credential_id=credential.id,
            preset_id=preset.id if preset is not None else None,
            protocol=vendor.protocol,
            base_url=endpoint.url,
            api_name=api_name,
            capabilities=capabilities,
            model_configuration=model_configuration,
            runtime_parameters=parameters,
        )

    def _load_vendors(self) -> dict[str, Vendor]:
        vendors: dict[str, Vendor] = {}
        for path, data in self._load_collection("vendors"):
            identifier = _required_string(data, "id", path)
            base_urls_data = _required_list(data, "base_urls", path)
            base_urls = tuple(
                self._parse_base_url(base_url, path) for base_url in base_urls_data
            )
            _ensure_unique_ids(base_urls, path)
            vendors[identifier] = Vendor(
                id=identifier,
                name=_required_string(data, "name", path),
                protocol=_required_string(data, "protocol", path),
                base_urls=base_urls,
                website=_optional_string(data, "website", path),
                description=_optional_string(data, "description", path),
                model_overrides=_mapping_field(data, "model_overrides", path),
                capabilities=_mapping_field(data, "capabilities", path),
            )
        return vendors

    def _load_model_families(self) -> dict[str, ModelFamily]:
        families: dict[str, ModelFamily] = {}
        for path, data in self._load_collection("model_families"):
            identifier = _required_string(data, "id", path)
            models_data = _required_mapping(data, "models", path)
            models: dict[str, ModelDefinition] = {}
            for model_id, configuration in models_data.items():
                if not isinstance(model_id, str) or not model_id:
                    raise ModelRegistryError(f"{path}: model ids must be non-empty strings")
                if not isinstance(configuration, Mapping):
                    raise ModelRegistryError(f"{path}: model '{model_id}' must be an object")
                models[model_id] = ModelDefinition(
                    id=model_id,
                    api_name=_optional_string(configuration, "api_name", path),
                    configuration=deepcopy(dict(configuration)),
                )
            if not models:
                raise ModelRegistryError(f"{path}: model family must declare at least one model")
            families[identifier] = ModelFamily(
                id=identifier,
                name=_required_string(data, "name", path),
                defaults=_mapping_field(data, "defaults", path),
                models=models,
            )
        return families

    def _load_credentials(self) -> dict[str, Credential]:
        credentials: dict[str, Credential] = {}
        for path, data in self._load_collection("credentials"):
            _reject_secret_fields(data, path)
            identifier = _required_string(data, "id", path)
            source = _required_string(data, "source", path)
            if source not in _CREDENTIAL_SOURCES:
                options = ", ".join(sorted(_CREDENTIAL_SOURCES))
                raise ModelRegistryError(f"{path}: source must be one of {options}")
            environment_variable = _optional_string(data, "env", path)
            if source in {"env", "local"} and environment_variable is None:
                raise ModelRegistryError(f"{path}: source '{source}' requires env")
            credentials[identifier] = Credential(
                id=identifier,
                vendor_id=_required_string(data, "vendor", path),
                source=source,
                environment_variable=environment_variable,
                description=_optional_string(data, "description", path),
            )
        return credentials

    def _load_presets(self) -> dict[str, Preset]:
        presets: dict[str, Preset] = {}
        for path, data in self._load_collection("presets"):
            identifier = _required_string(data, "id", path)
            presets[identifier] = Preset(
                id=identifier,
                vendor_id=_required_string(data, "vendor", path),
                credential_id=_required_string(data, "credential", path),
                model_id=_required_string(data, "model", path),
                model_family_id=_optional_string(data, "model_family", path),
                base_url_id=_optional_string(data, "base_url", path),
                parameters=_mapping_field(data, "parameters", path),
                description=_optional_string(data, "description", path),
            )
        return presets

    def _load_collection(self, directory: str) -> Iterable[tuple[Path, Mapping[str, Any]]]:
        location = self.root / directory
        if not location.is_dir():
            raise ModelRegistryError(f"model registry directory does not exist: {location}")
        paths = sorted((*location.glob("*.yaml"), *location.glob("*.yml")))
        if not paths:
            raise ModelRegistryError(f"model registry directory is empty: {location}")
        seen_ids: set[str] = set()
        for path in paths:
            data = _load_json_yaml(path)
            identifier = _required_string(data, "id", path)
            if identifier in seen_ids:
                raise ModelRegistryError(f"duplicate {directory} id '{identifier}'")
            seen_ids.add(identifier)
            yield path, data

    @staticmethod
    def _parse_base_url(data: Any, path: Path) -> VendorBaseURL:
        if not isinstance(data, Mapping):
            raise ModelRegistryError(f"{path}: base_urls entries must be objects")
        model_families = _required_list(data, "model_families", path)
        if not all(isinstance(item, str) and item for item in model_families):
            raise ModelRegistryError(f"{path}: model_families must contain non-empty strings")
        return VendorBaseURL(
            id=_required_string(data, "id", path),
            url=_required_string(data, "url", path),
            model_families=tuple(model_families),
            display_type=_optional_string(data, "type", path),
            model_overrides=_mapping_field(data, "model_overrides", path),
            capabilities=_mapping_field(data, "capabilities", path),
        )

    @staticmethod
    def _merge_preset_values(
        values: Mapping[str, str | None], preset: Preset
    ) -> dict[str, str | None]:
        preset_values = {
            "vendor_id": preset.vendor_id,
            "credential_id": preset.credential_id,
            "model_id": preset.model_id,
            "model_family_id": preset.model_family_id,
            "base_url_id": preset.base_url_id,
        }
        merged: dict[str, str | None] = {}
        for key, configured_value in preset_values.items():
            requested_value = values[key]
            if requested_value is not None and (
                configured_value is not None and requested_value != configured_value
            ):
                raise ModelRegistryError(
                    f"preset '{preset.id}' conflicts with explicit {key} "
                    f"'{requested_value}'"
                )
            merged[key] = requested_value or configured_value
        return merged

    def _select_model_family(
        self, model_id: str, requested_family_id: str | None
    ) -> ModelFamily:
        if requested_family_id is not None:
            family = self._get_required(
                self.model_families, requested_family_id, "model family"
            )
            if model_id not in family.models:
                raise ModelRegistryError(
                    f"model '{model_id}' is not declared by model family '{family.id}'"
                )
            return family
        matches = [
            family for family in self.model_families.values() if model_id in family.models
        ]
        if len(matches) != 1:
            label = "no" if not matches else "multiple"
            raise ModelRegistryError(f"{label} model families match model '{model_id}'")
        return matches[0]

    @staticmethod
    def _select_base_url(
        vendor: Vendor, family_id: str, requested_base_url_id: str | None
    ) -> VendorBaseURL:
        if requested_base_url_id is not None:
            endpoint = next(
                (item for item in vendor.base_urls if item.id == requested_base_url_id),
                None,
            )
            if endpoint is None:
                raise ModelRegistryError(
                    f"vendor '{vendor.id}' has no base URL '{requested_base_url_id}'"
                )
            if family_id not in endpoint.model_families:
                raise ModelRegistryError(
                    f"base URL '{endpoint.id}' does not support model family '{family_id}'"
                )
            return endpoint
        for endpoint in vendor.base_urls:
            if family_id in endpoint.model_families:
                return endpoint
        raise ModelRegistryError(
            f"vendor '{vendor.id}' has no base URL for model family '{family_id}'"
        )

    @staticmethod
    def _resolved_capabilities(
        vendor: Vendor,
        endpoint: VendorBaseURL,
        family: ModelFamily,
        model: ModelDefinition,
    ) -> dict[str, Any]:
        family_capabilities = _mapping_value(family.defaults, "capabilities")
        model_capabilities = _mapping_value(model.configuration, "capabilities")
        capabilities = _deep_merge(family_capabilities, model_capabilities)
        capabilities = _apply_capability_restrictions(capabilities, vendor.capabilities)
        capabilities = _apply_capability_restrictions(capabilities, endpoint.capabilities)
        vendor_override = _mapping_value(vendor.model_overrides, model.id)
        endpoint_override = _mapping_value(endpoint.model_overrides, model.id)
        capabilities = _apply_capability_restrictions(
            capabilities, _mapping_value(vendor_override, "capabilities")
        )
        return _apply_capability_restrictions(
            capabilities, _mapping_value(endpoint_override, "capabilities")
        )

    @staticmethod
    def _resolved_api_name(
        vendor: Vendor, endpoint: VendorBaseURL, model: ModelDefinition
    ) -> str:
        vendor_override = _mapping_value(vendor.model_overrides, model.id)
        endpoint_override = _mapping_value(endpoint.model_overrides, model.id)
        for configuration in (endpoint_override, vendor_override, model.configuration):
            api_name = configuration.get("api_name")
            if isinstance(api_name, str) and api_name:
                return api_name
        return model.id

    @staticmethod
    def _get_required(
        collection: Mapping[str, Any], identifier: str, label: str
    ) -> Any:
        try:
            return collection[identifier]
        except KeyError as error:
            raise ModelRegistryError(f"unknown {label} '{identifier}'") from error

    @staticmethod
    def _get_optional(
        collection: Mapping[str, Any], identifier: str | None, label: str
    ) -> Any | None:
        if identifier is None:
            return None
        return ModelRegistry._get_required(collection, identifier, label)

    @staticmethod
    def _required_value(value: str | None, label: str) -> str:
        if not isinstance(value, str) or not value:
            raise ModelRegistryError(f"{label} is required")
        return value


def _load_json_yaml(path: Path) -> Mapping[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ModelRegistryError(f"cannot read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ModelRegistryError(
            f"{path}: registry YAML must use JSON-compatible syntax ({error.msg})"
        ) from error
    if not isinstance(data, Mapping):
        raise ModelRegistryError(f"{path}: registry entry must be an object")
    return data


def _required_string(data: Mapping[str, Any], field: str, path: Path) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        raise ModelRegistryError(f"{path}: '{field}' must be a non-empty string")
    return value


def _optional_string(data: Mapping[str, Any], field: str, path: Path) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ModelRegistryError(f"{path}: '{field}' must be a non-empty string when set")
    return value


def _required_list(data: Mapping[str, Any], field: str, path: Path) -> list[Any]:
    value = data.get(field)
    if not isinstance(value, list):
        raise ModelRegistryError(f"{path}: '{field}' must be a list")
    return value


def _required_mapping(data: Mapping[str, Any], field: str, path: Path) -> Mapping[str, Any]:
    value = data.get(field)
    if not isinstance(value, Mapping):
        raise ModelRegistryError(f"{path}: '{field}' must be an object")
    return value


def _mapping_field(data: Mapping[str, Any], field: str, path: Path) -> Mapping[str, Any]:
    value = data.get(field, {})
    if not isinstance(value, Mapping):
        raise ModelRegistryError(f"{path}: '{field}' must be an object")
    return deepcopy(dict(value))


def _mapping_value(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """Return an optional object field as an independent mapping."""
    value = data.get(key, {})
    if not isinstance(value, Mapping):
        raise ModelRegistryError(f"'{key}' must be an object")
    return deepcopy(dict(value))


def _ensure_unique_ids(items: Iterable[Any], path: Path) -> None:
    identifiers = [item.id for item in items]
    if len(identifiers) != len(set(identifiers)):
        raise ModelRegistryError(f"{path}: base URL ids must be unique")


def _reject_secret_fields(data: Mapping[str, Any], path: Path) -> None:
    for key, value in data.items():
        normalized = key.lower().replace("-", "_") if isinstance(key, str) else ""
        if normalized in _SECRET_FIELD_NAMES:
            raise ModelRegistryError(f"{path}: credential secrets must not be persisted")
        if isinstance(value, Mapping):
            _reject_secret_fields(value, path)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    _reject_secret_fields(item, path)


def _deep_merge(*layers: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for layer in layers:
        for key, value in layer.items():
            if isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = deepcopy(value)
    return merged


def _apply_capability_restrictions(
    capabilities: Mapping[str, Any], restrictions: Mapping[str, Any]
) -> dict[str, Any]:
    """Apply a vendor restriction without allowing it to bypass a model limit."""
    merged = deepcopy(dict(capabilities))
    for key, restriction in restrictions.items():
        current = merged.get(key)
        if isinstance(restriction, Mapping):
            merged[key] = _apply_capability_restrictions(
                current if isinstance(current, Mapping) else {}, restriction
            )
        elif isinstance(restriction, bool) and isinstance(current, bool):
            merged[key] = current and restriction
        elif isinstance(restriction, bool) and current is None:
            merged[key] = restriction
        elif isinstance(restriction, (list, tuple)) and isinstance(current, (list, tuple)):
            allowed = set(restriction)
            merged[key] = [item for item in current if item in allowed]
        else:
            merged[key] = deepcopy(restriction)
    return merged
