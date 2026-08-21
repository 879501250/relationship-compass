#!/usr/bin/env python3
"""Shared ISO 8601 helpers for goutoujunshi-personal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def parse_iso8601(value: str, *, field_name: str = "timestamp") -> datetime:
    """Parse a timezone-aware ISO 8601 timestamp."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空 ISO 8601 时间")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是带时区的 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{field_name} 必须包含时区，例如 2026-08-21T14:00:00+08:00"
        )
    return parsed


def normalize_iso8601(value: str, *, field_name: str = "timestamp") -> str:
    """Normalize a timezone-aware ISO timestamp to second-precision UTC."""
    return parse_iso8601(value, field_name=field_name).astimezone(timezone.utc).isoformat(
        timespec="seconds"
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def add_days_iso(value: str, days: int, *, field_name: str = "timestamp") -> str:
    parsed = parse_iso8601(value, field_name=field_name).astimezone(timezone.utc)
    return (parsed + timedelta(days=days)).isoformat(timespec="seconds")


def age_in_days(value: str, *, now: str | None = None) -> int:
    reference = parse_iso8601(now, field_name="now") if now else datetime.now(timezone.utc)
    observed = parse_iso8601(value, field_name="timestamp")
    return (reference.astimezone(timezone.utc) - observed.astimezone(timezone.utc)).days

# Modified by AI on 2026-08-21 14:47:55
