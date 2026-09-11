"""Human-readable, locale-aware presentation helpers for the Eval Console."""

from __future__ import annotations

from datetime import datetime


def format_datetime_for_display(value: str | None, *, missing: str = "未知") -> str:
    """Render ISO-8601 metadata without changing its stored representation."""
    if not value:
        return missing
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value.replace("T", " ").replace("Z", "")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M")


def format_date_for_display(value: str | None, *, missing: str = "永不过期") -> str:
    """Render date-only lifecycle metadata without inventing a midnight time."""
    if not value:
        return missing
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return value.split("T", 1)[0]
