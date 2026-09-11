"""Human-readable, locale-aware presentation helpers for the Eval Console."""

from __future__ import annotations

from datetime import datetime


def format_datetime_for_display(value: str | None) -> str:
    """Render ISO-8601 metadata without changing its stored representation."""
    if not value:
        return "Never"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value.replace("T", " ").replace("Z", "")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return parsed.strftime("%Y-%m-%d %H:%M")
