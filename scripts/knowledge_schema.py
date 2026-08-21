#!/usr/bin/env python3
"""Dependency-free validation helpers for governed knowledge artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from date_utils import add_days_iso, normalize_iso8601, parse_iso8601, utc_now_iso


SCHEMA_VERSION = 1
SOURCE_TYPES = {"book", "paper", "course", "article", "note"}
SOURCE_QUALITIES = {"high", "medium", "low", "unknown"}
FRESHNESS_LEVELS = {"stable", "semi_dynamic", "dynamic"}
SOURCE_STATUSES = {
    "candidate",
    "reviewed",
    "approved",
    "partially_approved",
    "rejected",
    "deprecated",
}
CLAIM_TYPES = {
    "empirical",
    "theoretical",
    "clinical",
    "author_experience",
    "practical_heuristic",
    "ethical_norm",
}
CLAIM_EVIDENCE_LEVELS = {"A", "B", "C", "D", "unknown"}
CONFIDENCE_LEVELS = {"high", "medium", "low", "unknown"}
RELATION_TYPES = {"new", "duplicate", "extends", "conflicts"}
TOPICS = {
    "relationship-start",
    "conversation",
    "attraction",
    "intimacy",
    "boundaries",
    "conflict-and-repair",
    "personal-growth",
}
FRESHNESS_REVIEW_DAYS = {
    "stable": 365,
    "semi_dynamic": 180,
    "dynamic": 90,
}

SOURCE_REQUIRED_FIELDS = {
    "source_id",
    "title",
    "author",
    "source_type",
    "publication_year",
    "edition",
    "language",
    "topics",
    "source_quality",
    "added_at",
    "last_reviewed_at",
    "freshness",
    "status",
    "content_fingerprint",
}
SOURCE_OPTIONAL_FIELDS = {"review_after"}
CLAIM_REQUIRED_FIELDS = {
    "claim_id",
    "canonical_claim",
    "source_id",
    "source_anchor",
    "claim_type",
    "claim_evidence",
    "confidence",
    "applicable_when",
    "not_applicable_when",
    "risk_of_misuse",
    "relation_to_existing",
    "proposed_destination",
}
CLAIM_OPTIONAL_FIELDS = {"last_reviewed_at", "review_after"}


class KnowledgeSchemaError(ValueError):
    """Raised when governed knowledge violates a public schema."""


def _require_exact_fields(
    record: dict[str, Any], required: set[str], optional: set[str], label: str
) -> None:
    missing = required - set(record)
    unknown = set(record) - required - optional
    if missing:
        raise KnowledgeSchemaError(f"{label} missing fields: {', '.join(sorted(missing))}")
    if unknown:
        raise KnowledgeSchemaError(f"{label} unknown fields: {', '.join(sorted(unknown))}")


def _nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeSchemaError(f"{field} must be non-empty text")
    return value.strip()


def _text_list(value: Any, field: str, *, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise KnowledgeSchemaError(f"{field} must be a list of text values")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise KnowledgeSchemaError(f"{field} contains an invalid text value")
    return value


def fingerprint_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_claim_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", _nonempty_text(value, "canonical_claim"))
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    return normalized.rstrip("。.!！?？;； ")


def stable_claim_id(canonical_claim: str) -> str:
    digest = hashlib.sha256(normalize_claim_text(canonical_claim).encode("utf-8")).hexdigest()
    return f"claim-{digest[:16]}"


def validate_source(source: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise KnowledgeSchemaError("source must be an object")
    _require_exact_fields(source, SOURCE_REQUIRED_FIELDS, SOURCE_OPTIONAL_FIELDS, "source")
    source_id = _nonempty_text(source["source_id"], "source_id")
    if not re.fullmatch(r"src-[a-z0-9][a-z0-9-]{2,63}", source_id):
        raise KnowledgeSchemaError("source_id must match src-[a-z0-9-]")
    for field in ("title", "author", "language"):
        _nonempty_text(source[field], field)
    if source["source_type"] not in SOURCE_TYPES:
        raise KnowledgeSchemaError("invalid source_type")
    year = source["publication_year"]
    if not isinstance(year, int) or isinstance(year, bool) or not 1000 <= year <= 3000:
        raise KnowledgeSchemaError("publication_year must be an integer year")
    if source["edition"] is not None and not isinstance(source["edition"], str):
        raise KnowledgeSchemaError("edition must be text or null")
    topics = _text_list(source["topics"], "topics", allow_empty=False)
    unknown_topics = set(topics) - TOPICS
    if unknown_topics:
        raise KnowledgeSchemaError(f"unknown topics: {', '.join(sorted(unknown_topics))}")
    if source["source_quality"] not in SOURCE_QUALITIES:
        raise KnowledgeSchemaError("invalid source_quality")
    if source["freshness"] not in FRESHNESS_LEVELS:
        raise KnowledgeSchemaError("invalid freshness")
    if source["status"] not in SOURCE_STATUSES:
        raise KnowledgeSchemaError("invalid source status")
    for field in ("added_at", "last_reviewed_at"):
        normalize_iso8601(source[field], field_name=field)
    if source.get("review_after") is not None:
        normalize_iso8601(source["review_after"], field_name="review_after")
    fingerprint = source["content_fingerprint"]
    if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise KnowledgeSchemaError("content_fingerprint must be lowercase SHA-256")
    return source


def validate_registry(registry: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(registry, dict) or set(registry) != {"schema_version", "sources"}:
        raise KnowledgeSchemaError("registry must contain only schema_version and sources")
    if registry["schema_version"] != SCHEMA_VERSION:
        raise KnowledgeSchemaError(f"unsupported schema_version: {registry['schema_version']}")
    if not isinstance(registry["sources"], list):
        raise KnowledgeSchemaError("sources must be a list")
    seen_ids: set[str] = set()
    seen_fingerprints: set[str] = set()
    for source in registry["sources"]:
        validate_source(source)
        if source["source_id"] in seen_ids:
            raise KnowledgeSchemaError(f"duplicate source_id: {source['source_id']}")
        if source["content_fingerprint"] in seen_fingerprints:
            raise KnowledgeSchemaError(
                f"duplicate content_fingerprint: {source['content_fingerprint']}"
            )
        seen_ids.add(source["source_id"])
        seen_fingerprints.add(source["content_fingerprint"])
    return registry


def validate_claim(claim: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(claim, dict):
        raise KnowledgeSchemaError("claim must be an object")
    _require_exact_fields(claim, CLAIM_REQUIRED_FIELDS, CLAIM_OPTIONAL_FIELDS, "claim")
    canonical = _nonempty_text(claim["canonical_claim"], "canonical_claim")
    expected_id = stable_claim_id(canonical)
    if claim["claim_id"] != expected_id:
        raise KnowledgeSchemaError(f"claim_id must be {expected_id}")
    if not re.fullmatch(r"src-[a-z0-9][a-z0-9-]{2,63}", claim["source_id"]):
        raise KnowledgeSchemaError("invalid claim source_id")
    _nonempty_text(claim["source_anchor"], "source_anchor")
    if claim["claim_type"] not in CLAIM_TYPES:
        raise KnowledgeSchemaError("invalid claim_type")
    if claim["claim_evidence"] not in CLAIM_EVIDENCE_LEVELS:
        raise KnowledgeSchemaError("invalid claim_evidence")
    if claim["confidence"] not in CONFIDENCE_LEVELS:
        raise KnowledgeSchemaError("invalid claim confidence")
    for field in ("applicable_when", "not_applicable_when", "risk_of_misuse"):
        _text_list(claim[field], field)
    relation = claim["relation_to_existing"]
    if not isinstance(relation, dict) or set(relation) != {"type", "claim_ids"}:
        raise KnowledgeSchemaError("relation_to_existing requires type and claim_ids")
    if relation["type"] not in RELATION_TYPES:
        raise KnowledgeSchemaError("invalid relation_to_existing type")
    claim_ids = _text_list(relation["claim_ids"], "relation_to_existing.claim_ids")
    if relation["type"] == "new" and claim_ids:
        raise KnowledgeSchemaError("new claims cannot reference existing claim_ids")
    if relation["type"] != "new" and not claim_ids:
        raise KnowledgeSchemaError("non-new relations must reference existing claim_ids")
    if claim["proposed_destination"] not in TOPICS:
        raise KnowledgeSchemaError("invalid proposed_destination")
    for field in CLAIM_OPTIONAL_FIELDS:
        if claim.get(field) is not None:
            normalize_iso8601(claim[field], field_name=field)
    return claim


def freshness_status(record: dict[str, Any], reference_at: str | None = None) -> str:
    reviewed_at = record.get("last_reviewed_at")
    freshness = record.get("freshness")
    if not reviewed_at:
        return "unknown"
    normalize_iso8601(reviewed_at, field_name="last_reviewed_at")
    review_after = record.get("review_after")
    if review_after:
        due_at = normalize_iso8601(review_after, field_name="review_after")
    elif freshness in FRESHNESS_REVIEW_DAYS:
        due_at = add_days_iso(
            reviewed_at,
            FRESHNESS_REVIEW_DAYS[freshness],
            field_name="last_reviewed_at",
        )
    else:
        return "unknown"
    reference = normalize_iso8601(
        reference_at or utc_now_iso(), field_name="reference_at"
    )
    return "review_due" if parse_iso8601(reference) >= parse_iso8601(due_at) else "current"


def load_registry(path: Path) -> dict[str, Any]:
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeSchemaError(f"cannot load registry: {exc}") from exc
    return validate_registry(registry)


# Modified by AI on 2026-08-21 16:38:32
