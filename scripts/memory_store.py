#!/usr/bin/env python3
"""Local, bounded, consent-gated memory for goutoujunshi-personal."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from date_utils import add_days_iso, age_in_days, normalize_iso8601, utc_now_iso


POLICY_VERSION = "4"
MAX_VALUE_CHARS = 600
MAX_SOURCE_CHARS = 200
MAX_ROWS = 200
MAX_OPERATIONS = 20
STYLE_REVIEW_DAYS = 90
CONTEXT_EVENT_LIMIT = 8
RECENT_TECHNIQUE_LIMIT = 8
SCOPE_LIMITS = {
    "user": 30,
    "object": 15,
    "relationship": 10,
    "event": 20,
    "hypothesis": 5,
}
SCOPES = set(SCOPE_LIMITS)
SOURCE_TYPES = {
    "user_explicit",
    "user_report",
    "chatlab",
    "tool",
    "assistant_inference",
}
CONFIDENCE_LEVELS = {"high", "medium", "low"}
AUTONOMY_STATES = {"A0", "A1", "A2"}
EVENT_RETENTIONS = {"landmark", "normal", "temporary"}
CAPABILITY_STATES = {"emerging", "developing", "stable"}
CAPABILITY_DIMENSIONS = {
    "initiative",
    "self_disclosure",
    "opinion",
    "storytelling",
    "observation_humor",
    "callback",
    "teasing",
    "flirting",
    "continuation",
}
TECHNIQUES = {
    "plain",
    "observation_humor",
    "light_exaggeration",
    "deadpan_absurd",
    "fake_serious",
    "callback",
    "light_teasing",
    "self_deprecation",
    "situational_imagining",
    "micro_story",
    "contrast",
    "playful_framing",
}
HYPOTHESIS_TTL_DAYS = {
    "stage_estimate": 30,
    "trend_estimate": 14,
    "humor_receptivity": 30,
    "humor_acceptance": 30,
    "style_update": 30,
    "style_update_suggestion": 30,
}
HYPOTHESIS_ONLY_FIELDS = set(HYPOTHESIS_TTL_DAYS)


class MemoryErrorWithCode(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ClosingConnection(sqlite3.Connection):
    """SQLite context manager that also releases the file handle on exit."""

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()


def now_iso() -> str:
    return utc_now_iso()


def memory_dir() -> Path:
    override = os.environ.get("GOUTOUJUNSHI_PERSONAL_MEMORY_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "goutoujunshi-personal"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return base / "goutoujunshi-personal"
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    return base / "goutoujunshi-personal"


def db_path() -> Path:
    return memory_dir() / "memory.sqlite3"


def normalize_database_timestamps(conn: sqlite3.Connection) -> None:
    """Validate and normalize persisted timestamps during schema connection."""
    rows = conn.execute(
        "SELECT id, scope, field, observed_at, occurred_at, expires_at, "
        "created_at, updated_at FROM memories"
    ).fetchall()
    for row in rows:
        try:
            normalized = {
                "observed_at": normalize_iso8601(
                    row["observed_at"], field_name="observed_at"
                ),
                "occurred_at": normalize_iso8601(
                    row["occurred_at"], field_name="occurred_at"
                )
                if row["occurred_at"]
                else None,
                "expires_at": normalize_iso8601(
                    row["expires_at"], field_name="expires_at"
                )
                if row["expires_at"]
                else None,
                "created_at": normalize_iso8601(
                    row["created_at"], field_name="created_at"
                ),
                "updated_at": normalize_iso8601(
                    row["updated_at"], field_name="updated_at"
                ),
            }
        except ValueError as exc:
            raise MemoryErrorWithCode(
                "CORRUPT_TIMESTAMP", f"memory {row['id']} 存在无效时间字段: {exc}"
            ) from exc
        if (
            row["scope"] == "hypothesis"
            and normalized["expires_at"] is None
            and row["field"] in HYPOTHESIS_TTL_DAYS
        ):
            normalized["expires_at"] = add_days_iso(
                normalized["observed_at"], HYPOTHESIS_TTL_DAYS[row["field"]]
            )
        conn.execute(
            "UPDATE memories SET observed_at = ?, occurred_at = ?, expires_at = ?, "
            "created_at = ?, updated_at = ? WHERE id = ?",
            (
                normalized["observed_at"],
                normalized["occurred_at"],
                normalized["expires_at"],
                normalized["created_at"],
                normalized["updated_at"],
                row["id"],
            ),
        )
    operations = conn.execute("SELECT op_id, created_at FROM operations").fetchall()
    for operation in operations:
        try:
            created_at = normalize_iso8601(
                operation["created_at"], field_name="created_at"
            )
        except ValueError as exc:
            raise MemoryErrorWithCode(
                "CORRUPT_TIMESTAMP",
                f"operation {operation['op_id']} 存在无效 created_at: {exc}",
            ) from exc
        conn.execute(
            "UPDATE operations SET created_at = ? WHERE op_id = ?",
            (created_at, operation["op_id"]),
        )


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def connect(create: bool = False) -> sqlite3.Connection:
    target = db_path()
    if not target.exists() and not create:
        raise MemoryErrorWithCode("NOT_INITIALIZED", "尚未启用长期记忆")
    if create:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            target.parent.chmod(0o700)
        except OSError:
            pass
    conn = sqlite3.connect(target, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            field TEXT NOT NULL,
            value TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL DEFAULT '',
            occurred_at TEXT,
            retention TEXT NOT NULL DEFAULT 'normal',
            observed_at TEXT NOT NULL,
            confidence TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            expires_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memories_lookup
            ON memories(scope, subject_id, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_memories_timeline
            ON memories(scope, subject_id, status, occurred_at);
        CREATE TABLE IF NOT EXISTS operations (
            op_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            action TEXT NOT NULL,
            before_json TEXT NOT NULL,
            after_json TEXT NOT NULL
        );
        """
    )
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(memories)")}
    if "retention" not in columns:
        conn.execute(
            "ALTER TABLE memories ADD COLUMN retention TEXT NOT NULL DEFAULT 'normal'"
        )
    if "expires_at" not in columns:
        conn.execute("ALTER TABLE memories ADD COLUMN expires_at TEXT")
    try:
        normalize_database_timestamps(conn)
    except Exception:
        conn.close()
        raise
    conn.commit()
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def require_enabled(conn: sqlite3.Connection) -> None:
    if get_setting(conn, "consent_enabled") != "true":
        raise MemoryErrorWithCode("CONSENT_REQUIRED", "长期记忆尚未获得用户同意")
    if get_setting(conn, "paused") == "true":
        raise MemoryErrorWithCode("MEMORY_PAUSED", "长期记忆当前已暂停")


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def validate_text(name: str, value: Any, maximum: int, required: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise MemoryErrorWithCode("INVALID_DELTA", f"{name} 必须是字符串")
    value = value.strip()
    if required and not value:
        raise MemoryErrorWithCode("INVALID_DELTA", f"{name} 不能为空")
    if len(value) > maximum:
        raise MemoryErrorWithCode("INVALID_DELTA", f"{name} 超过 {maximum} 字符")
    if any(ord(char) < 32 and char not in "\t\n" for char in value):
        raise MemoryErrorWithCode("INVALID_DELTA", f"{name} 包含控制字符")
    return value


def validate_iso_timestamp(name: str, value: Any, required: bool = False) -> str | None:
    text = validate_text(name, value, 64, required=required)
    if not text:
        return None
    try:
        return normalize_iso8601(text, field_name=name)
    except ValueError as exc:
        raise MemoryErrorWithCode(
            "INVALID_TIMESTAMP", f"{name} 必须是带时区的 ISO 8601 时间"
        ) from exc


def normalize_stored_timestamp(name: str, value: Any, required: bool) -> str | None:
    """Validate timestamps before a row is restored or reused."""
    return validate_iso_timestamp(name, value, required=required)


def hypothesis_expires_at(field: str, observed_at: str, requested: str | None) -> str | None:
    ttl_days = HYPOTHESIS_TTL_DAYS.get(field)
    if ttl_days is not None:
        return add_days_iso(observed_at, ttl_days, field_name="observed_at")
    return requested


def refresh_hypothesis_lifecycle(
    conn: sqlite3.Connection, reference_at: str | None = None
) -> int:
    """Mark expired active hypotheses stale without deleting history."""
    timestamp = validate_iso_timestamp(
        "reference_at", reference_at or now_iso(), required=True
    )
    result = conn.execute(
        "UPDATE memories SET status = 'stale', updated_at = ? "
        "WHERE scope = 'hypothesis' AND status = 'active' "
        "AND expires_at IS NOT NULL AND expires_at <= ?",
        (timestamp, timestamp),
    )
    conn.commit()
    return result.rowcount


def validate_capability_profile(value: str) -> str:
    try:
        profile = json.loads(value)
    except json.JSONDecodeError as exc:
        raise MemoryErrorWithCode(
            "INVALID_CAPABILITY_PROFILE", "capability_profile 必须是 JSON 对象"
        ) from exc
    if not isinstance(profile, dict) or not profile:
        raise MemoryErrorWithCode(
            "INVALID_CAPABILITY_PROFILE", "capability_profile 必须包含至少一个能力维度"
        )
    unknown = set(profile) - CAPABILITY_DIMENSIONS
    if unknown:
        raise MemoryErrorWithCode(
            "INVALID_CAPABILITY_PROFILE", f"不支持的能力维度: {', '.join(sorted(unknown))}"
        )
    if any(state not in CAPABILITY_STATES for state in profile.values()):
        raise MemoryErrorWithCode(
            "INVALID_CAPABILITY_PROFILE",
            "能力状态只能是 emerging、developing 或 stable",
        )
    return json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_technique_history(value: str) -> str:
    try:
        history = json.loads(value)
    except json.JSONDecodeError as exc:
        raise MemoryErrorWithCode(
            "INVALID_TECHNIQUE_HISTORY", "recent_techniques 必须是 JSON 数组"
        ) from exc
    if not isinstance(history, list) or len(history) > RECENT_TECHNIQUE_LIMIT:
        raise MemoryErrorWithCode(
            "INVALID_TECHNIQUE_HISTORY",
            f"recent_techniques 必须是不超过 {RECENT_TECHNIQUE_LIMIT} 项的数组",
        )
    if any(not isinstance(item, str) or item not in TECHNIQUES for item in history):
        raise MemoryErrorWithCode(
            "INVALID_TECHNIQUE_HISTORY", "recent_techniques 包含未知技巧"
        )
    return json.dumps(history, ensure_ascii=False, separators=(",", ":"))


def validate_delta(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MemoryErrorWithCode("INVALID_DELTA", "记忆变更必须是 JSON 对象")
    scope = validate_text("scope", raw.get("scope"), 32)
    if scope not in SCOPES:
        raise MemoryErrorWithCode("INVALID_DELTA", f"不支持的 scope: {scope}")
    subject_id = validate_text("subject_id", raw.get("subject_id"), 64)
    field = validate_text("field", raw.get("field"), 64)
    value = validate_text("value", raw.get("value"), MAX_VALUE_CHARS)
    source_type = validate_text("source_type", raw.get("source_type"), 32)
    if source_type not in SOURCE_TYPES:
        raise MemoryErrorWithCode("INVALID_DELTA", f"不支持的 source_type: {source_type}")
    confidence = validate_text("confidence", raw.get("confidence", "medium"), 16)
    if confidence not in CONFIDENCE_LEVELS:
        raise MemoryErrorWithCode("INVALID_DELTA", f"不支持的 confidence: {confidence}")
    source_ref = validate_text(
        "source_ref", raw.get("source_ref", ""), MAX_SOURCE_CHARS, required=False
    )
    occurred_at = validate_iso_timestamp(
        "occurred_at", raw.get("occurred_at", ""), required=scope == "event"
    )
    expires_at = validate_iso_timestamp(
        "expires_at", raw.get("expires_at", ""), required=False
    )
    retention = validate_text("retention", raw.get("retention", "normal"), 16)
    if retention not in EVENT_RETENTIONS:
        raise MemoryErrorWithCode(
            "INVALID_RETENTION", "retention 必须是 landmark、normal 或 temporary"
        )
    if scope != "event" and retention != "normal":
        raise MemoryErrorWithCode(
            "INVALID_RETENTION", "只有 event scope 可以设置非 normal retention"
        )
    if expires_at and scope != "hypothesis":
        raise MemoryErrorWithCode(
            "INVALID_TIMESTAMP", "只有 hypothesis scope 可以设置 expires_at"
        )
    if field in HYPOTHESIS_ONLY_FIELDS and scope != "hypothesis":
        raise MemoryErrorWithCode(
            "FACT_HYPOTHESIS_VIOLATION",
            f"{field} 是模型判断，必须写入 hypothesis，不能写入 confirmed scope",
        )
    if scope == "user" and subject_id != "user":
        raise MemoryErrorWithCode(
            "INVALID_SUBJECT", "用户共享状态必须使用 subject_id=user"
        )
    if scope == "user" and source_type != "user_explicit":
        raise MemoryErrorWithCode(
            "SOURCE_NOT_ELIGIBLE", "用户稳定档案只接受用户明确陈述"
        )
    if scope == "user" and field == "trained_expression_level":
        raise MemoryErrorWithCode(
            "DEPRECATED_FIELD",
            "trained_expression_level 已停用；请用经用户确认的 capability_profile",
        )
    if scope == "user" and field == "capability_profile":
        value = validate_capability_profile(value)
    if scope == "user" and field == "autonomy_state" and value not in AUTONOMY_STATES:
        raise MemoryErrorWithCode(
            "INVALID_DELTA", "autonomy_state 必须是 A0、A1 或 A2"
        )
    if scope in {"object", "relationship"} and source_type not in {
        "user_explicit",
        "user_report",
    }:
        raise MemoryErrorWithCode(
            "SOURCE_NOT_ELIGIBLE", "对象事实和关系快照只接受用户明确陈述或转述"
        )
    if field == "recent_techniques":
        if scope != "object":
            raise MemoryErrorWithCode(
                "INVALID_SCOPE", "recent_techniques 必须按对象存入 object scope"
            )
        if source_ref != "record-technique:confirmed-sent":
            raise MemoryErrorWithCode(
                "ACTUAL_SEND_REQUIRED",
                "recent_techniques 只能通过 record-technique --confirm-sent 更新",
            )
        value = validate_technique_history(value)
    if source_type in {"chatlab", "tool", "assistant_inference"} and scope not in {
        "event",
        "hypothesis",
    }:
        raise MemoryErrorWithCode(
            "SOURCE_NOT_ELIGIBLE", "外部工具和模型推断只能写入事件或假设"
        )
    if source_type == "assistant_inference" and scope != "hypothesis":
        raise MemoryErrorWithCode(
            "SOURCE_NOT_ELIGIBLE", "模型推断只能写入带置信度的假设"
        )
    return {
        "scope": scope,
        "subject_id": subject_id,
        "field": field,
        "value": value,
        "source_type": source_type,
        "source_ref": source_ref,
        "occurred_at": occurred_at,
        "expires_at": expires_at,
        "retention": retention,
        "confidence": confidence,
    }


def current_style_review_status(updated_at: str, max_age_days: int = STYLE_REVIEW_DAYS) -> str:
    try:
        age_days = age_in_days(updated_at)
    except (TypeError, ValueError):
        return "review_suggested"
    return "expired" if age_days >= max_age_days else "current"


def prune_operation_history(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM operations WHERE op_id NOT IN ("
        "SELECT op_id FROM operations ORDER BY created_at DESC, rowid DESC LIMIT ?)",
        (MAX_OPERATIONS,),
    )


def prune_rows(conn: sqlite3.Connection, delta: dict[str, Any]) -> list[dict[str, Any]]:
    removed: list[dict[str, Any]] = []
    scope = delta["scope"]
    subject_id = delta["subject_id"]
    scope_count = conn.execute(
        "SELECT COUNT(*) AS n FROM memories "
        "WHERE scope = ? AND subject_id = ? AND status = 'active'",
        (scope, subject_id),
    ).fetchone()["n"]
    if scope in {"user", "object", "relationship"} and scope_count > SCOPE_LIMITS[scope]:
        raise MemoryErrorWithCode(
            "MEMORY_LIMIT_REACHED",
            f"{scope} 档案已达 {SCOPE_LIMITS[scope]} 条上限，请先合并或删除旧字段",
        )
    if scope == "event":
        excess = scope_count - SCOPE_LIMITS[scope]
        if excess > 0:
            rows = conn.execute(
                "SELECT * FROM memories WHERE scope = 'event' AND subject_id = ? "
                "AND status = 'active' AND retention != 'landmark' "
                "ORDER BY CASE retention WHEN 'temporary' THEN 0 ELSE 1 END, "
                "CASE WHEN occurred_at IS NULL THEN 1 ELSE 0 END, occurred_at ASC, "
                "updated_at ASC LIMIT ?",
                (subject_id, excess),
            ).fetchall()
            for row in rows:
                removed.append(row_dict(row))
                conn.execute("DELETE FROM memories WHERE id = ?", (row["id"],))
    elif scope == "hypothesis":
        excess = scope_count - SCOPE_LIMITS[scope]
        if excess > 0:
            rows = conn.execute(
                "SELECT * FROM memories WHERE scope = 'hypothesis' AND subject_id = ? "
                "AND status = 'active' ORDER BY updated_at ASC LIMIT ?",
                (subject_id, excess),
            ).fetchall()
            for row in rows:
                removed.append(row_dict(row))
                conn.execute(
                    "UPDATE memories SET status = 'superseded', updated_at = ? WHERE id = ?",
                    (now_iso(), row["id"]),
                )

    total = conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"]
    excess_total = total - MAX_ROWS
    if excess_total > 0:
        rows = conn.execute(
            "SELECT * FROM memories WHERE "
            "scope = 'event' AND retention != 'landmark' "
            "ORDER BY CASE "
            "WHEN scope = 'event' AND retention = 'temporary' THEN 0 "
            "ELSE 1 END, "
            "CASE WHEN occurred_at IS NULL THEN 1 ELSE 0 END, occurred_at ASC, "
            "updated_at ASC LIMIT ?",
            (excess_total,),
        ).fetchall()
        for row in rows:
            if not any(item["id"] == row["id"] for item in removed):
                removed.append(row_dict(row))
                conn.execute("DELETE FROM memories WHERE id = ?", (row["id"],))
    if conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"] > MAX_ROWS:
        raise MemoryErrorWithCode(
            "MEMORY_LIMIT_REACHED", "长期记忆已达上限，请先归档或删除旧对象"
        )
    return removed


def command_status(_: argparse.Namespace) -> None:
    target = db_path()
    if not target.exists():
        emit(
            {
                "exists": False,
                "consent_enabled": False,
                "paused": False,
                "policy_version": POLICY_VERSION,
                "namespace": "goutoujunshi-personal",
                "path": str(target),
            }
        )
        return
    with connect() as conn:
        emit(
            {
                "exists": True,
                "consent_enabled": get_setting(conn, "consent_enabled") == "true",
                "consent_at": get_setting(conn, "consent_at") or None,
                "paused": get_setting(conn, "paused") == "true",
                "policy_version": get_setting(conn, "policy_version", POLICY_VERSION),
                "namespace": "goutoujunshi-personal",
                "memory_count": conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()["n"],
                "undo_count": conn.execute("SELECT COUNT(*) AS n FROM operations").fetchone()["n"],
                "path": str(target),
            }
        )


def command_enable(args: argparse.Namespace) -> None:
    if not args.confirm:
        raise MemoryErrorWithCode("CONFIRMATION_REQUIRED", "需要 --confirm 表示用户已明确同意")
    with connect(create=True) as conn:
        timestamp = now_iso()
        if not get_setting(conn, "consent_at"):
            set_setting(conn, "consent_at", timestamp)
        set_setting(conn, "consent_enabled", "true")
        set_setting(conn, "paused", "false")
        set_setting(conn, "policy_version", POLICY_VERSION)
        conn.commit()
    emit({"ok": True, "consent_enabled": True, "paused": False})


def command_pause(_: argparse.Namespace) -> None:
    with connect() as conn:
        if get_setting(conn, "consent_enabled") != "true":
            raise MemoryErrorWithCode("CONSENT_REQUIRED", "长期记忆尚未启用")
        set_setting(conn, "paused", "true")
        conn.commit()
    emit({"ok": True, "paused": True})


def command_resume(_: argparse.Namespace) -> None:
    with connect() as conn:
        if get_setting(conn, "consent_enabled") != "true":
            raise MemoryErrorWithCode("CONSENT_REQUIRED", "长期记忆尚未启用")
        set_setting(conn, "paused", "false")
        conn.commit()
    emit({"ok": True, "paused": False})


def load_delta(args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
    elif args.json:
        raw = json.loads(args.json)
    else:
        raw = json.load(sys.stdin)
    return validate_delta(raw)


def command_apply(args: argparse.Namespace) -> None:
    delta = load_delta(args)
    with connect() as conn:
        require_enabled(conn)
        refresh_hypothesis_lifecycle(conn)
        timestamp = now_iso()
        conn.execute("BEGIN IMMEDIATE")
        before: list[dict[str, Any]] = []
        was_update = False
        if delta["scope"] == "event":
            existing_rows = conn.execute(
                "SELECT * FROM memories WHERE scope = 'event' AND subject_id = ? "
                "AND field = ? AND occurred_at IS ? AND status = 'active' "
                "ORDER BY updated_at DESC, rowid DESC",
                (delta["subject_id"], delta["field"], delta["occurred_at"]),
            ).fetchall()
            existing = existing_rows[0] if existing_rows else None
            if existing_rows:
                before.extend(row_dict(row) for row in existing_rows)
                for duplicate in existing_rows[1:]:
                    conn.execute("DELETE FROM memories WHERE id = ?", (duplicate["id"],))
        elif delta["scope"] == "hypothesis":
            existing = conn.execute(
                "SELECT * FROM memories WHERE scope = 'hypothesis' AND subject_id = ? "
                "AND field = ? AND status = 'active' LIMIT 1",
                (delta["subject_id"], delta["field"]),
            ).fetchone()
            if existing:
                before.append(row_dict(existing))
                conn.execute(
                    "UPDATE memories SET status = 'superseded', updated_at = ? WHERE id = ?",
                    (timestamp, existing["id"]),
                )
            existing = None
        else:
            existing = conn.execute(
                "SELECT * FROM memories WHERE scope = ? AND subject_id = ? "
                "AND field = ? AND status = 'active' LIMIT 1",
                (delta["scope"], delta["subject_id"], delta["field"]),
            ).fetchone()
            if existing:
                before.append(row_dict(existing))
        if existing:
            memory_id = existing["id"]
            created_at = existing["created_at"]
            was_update = True
        else:
            memory_id = uuid.uuid4().hex
            created_at = timestamp
        row = {
            "id": memory_id,
            **delta,
            "observed_at": timestamp,
            "expires_at": hypothesis_expires_at(
                delta["field"], timestamp, delta.get("expires_at")
            )
            if delta["scope"] == "hypothesis"
            else None,
            "status": "active",
            "created_at": created_at,
            "updated_at": timestamp,
        }
        conn.execute(
            """
            INSERT INTO memories(
                id, scope, subject_id, field, value, source_type, source_ref,
                occurred_at, retention, observed_at, confidence, status, expires_at,
                created_at, updated_at
            ) VALUES(
                :id, :scope, :subject_id, :field, :value, :source_type, :source_ref,
                :occurred_at, :retention, :observed_at, :confidence, :status, :expires_at,
                :created_at, :updated_at
            ) ON CONFLICT(id) DO UPDATE SET
                value = excluded.value,
                source_type = excluded.source_type,
                source_ref = excluded.source_ref,
                occurred_at = excluded.occurred_at,
                retention = excluded.retention,
                observed_at = excluded.observed_at,
                confidence = excluded.confidence,
                status = excluded.status,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            row,
        )
        before.extend(prune_rows(conn, delta))
        op_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO operations(op_id, created_at, action, before_json, after_json) "
            "VALUES(?, ?, 'apply', ?, ?)",
            (
                op_id,
                timestamp,
                json.dumps(before, ensure_ascii=False),
                json.dumps([row], ensure_ascii=False),
            ),
        )
        prune_operation_history(conn)
        conn.commit()
    emit(
        {
            "ok": True,
            "op_id": op_id,
            "memory": row,
            "pruned": len(before) - (1 if was_update else 0),
        }
    )


def restore_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        row = {"retention": "normal", "expires_at": None, **row}
        for name in ("observed_at", "created_at", "updated_at"):
            row[name] = normalize_stored_timestamp(name, row.get(name), required=True)
        for name in ("occurred_at", "expires_at"):
            row[name] = normalize_stored_timestamp(name, row.get(name), required=False)
        conn.execute(
            """
            INSERT OR REPLACE INTO memories(
                id, scope, subject_id, field, value, source_type, source_ref,
                occurred_at, retention, observed_at, confidence, status, expires_at,
                created_at, updated_at
            ) VALUES(
                :id, :scope, :subject_id, :field, :value, :source_type, :source_ref,
                :occurred_at, :retention, :observed_at, :confidence, :status, :expires_at,
                :created_at, :updated_at
            )
            """,
            row,
        )


def command_undo(args: argparse.Namespace) -> None:
    with connect() as conn:
        if args.op_id:
            operation = conn.execute(
                "SELECT * FROM operations WHERE op_id = ?", (args.op_id,)
            ).fetchone()
        else:
            operation = conn.execute(
                "SELECT * FROM operations ORDER BY created_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if not operation:
            raise MemoryErrorWithCode("NOTHING_TO_UNDO", "没有可撤销的记忆更新")
        before = json.loads(operation["before_json"])
        after = json.loads(operation["after_json"])
        affected_ids = {row["id"] for row in before + after}
        conn.execute("BEGIN IMMEDIATE")
        for memory_id in affected_ids:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        restore_rows(conn, before)
        conn.execute("DELETE FROM operations WHERE op_id = ?", (operation["op_id"],))
        conn.commit()
    emit({"ok": True, "undone_op_id": operation["op_id"]})


def list_memories(
    conn: sqlite3.Connection, subject_id: str | None, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    status_clause = "" if include_inactive else " AND status = 'active'"
    if subject_id:
        rows = conn.execute(
            "SELECT * FROM memories WHERE subject_id IN ('user', ?)" + status_clause + " "
            "ORDER BY CASE scope WHEN 'user' THEN 0 WHEN 'object' THEN 1 "
            "WHEN 'relationship' THEN 2 WHEN 'event' THEN 3 ELSE 4 END, "
            "CASE status WHEN 'active' THEN 0 WHEN 'stale' THEN 1 ELSE 2 END, "
            "CASE WHEN scope = 'event' AND occurred_at IS NULL THEN 1 ELSE 0 END, "
            "CASE WHEN scope = 'event' THEN occurred_at END DESC, updated_at DESC",
            (subject_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM memories WHERE 1 = 1" + status_clause + " "
            "ORDER BY CASE WHEN scope = 'event' THEN 0 ELSE 1 END, "
            "CASE status WHEN 'active' THEN 0 WHEN 'stale' THEN 1 ELSE 2 END, "
            "CASE WHEN scope = 'event' AND occurred_at IS NULL THEN 1 ELSE 0 END, "
            "CASE WHEN scope = 'event' THEN occurred_at END DESC, updated_at DESC"
        ).fetchall()
    return [row_dict(row) for row in rows]


def command_show(args: argparse.Namespace) -> None:
    with connect() as conn:
        refresh_hypothesis_lifecycle(conn)
        rows = list_memories(conn, args.subject_id, include_inactive=True)
    emit({"count": len(rows), "memories": rows})


def compact_context_row(row: dict[str, Any]) -> dict[str, Any]:
    item = {
        "id": row["id"],
        "scope": row["scope"],
        "subject_id": row["subject_id"],
        "field": row["field"],
        "value": row["value"],
        "occurred_at": row["occurred_at"],
        "updated_at": row["updated_at"],
        "observed_at": row["observed_at"],
        "retention": row["retention"],
        "confidence": row["confidence"],
        "expires_at": row["expires_at"],
        "source_type": row["source_type"],
        "source_ref": row["source_ref"],
    }
    if row["scope"] == "user" and row["field"] == "current_style":
        item["review_status"] = current_style_review_status(row["updated_at"])
    return item


def context_priority_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order context candidates by policy before applying the character budget."""
    core = [row for row in rows if row["scope"] in {"user", "object", "relationship"}]
    hypotheses = [row for row in rows if row["scope"] == "hypothesis"]
    hypothesis_rank = {"stage_estimate": 0, "trend_estimate": 1}
    hypotheses.sort(
        key=lambda row: (
            hypothesis_rank.get(row["field"], 2),
            row["field"],
            row["id"],
        )
    )
    events = [row for row in rows if row["scope"] == "event"]
    landmarks = [row for row in events if row["retention"] == "landmark"]
    recent_normal = [row for row in events if row["retention"] == "normal"]
    recent_temporary = [row for row in events if row["retention"] == "temporary"]
    recent_slots = CONTEXT_EVENT_LIMIT
    normal_candidates = recent_normal[:recent_slots]
    recent_slots -= len(normal_candidates)
    temporary_candidates = recent_temporary[:recent_slots]
    return core + hypotheses + landmarks + normal_candidates + temporary_candidates


def build_context_payload(
    rows: list[dict[str, Any]], subject_id: str, max_chars: int
) -> dict[str, Any]:
    """Select whole records deterministically without exceeding rendered output size."""
    selected: list[dict[str, Any]] = []
    for row in context_priority_rows(rows):
        candidate = selected + [compact_context_row(row)]
        payload = {
            "count": len(candidate),
            "subject_id": subject_id,
            "max_chars": max_chars,
            "memories": candidate,
        }
        rendered_chars = len(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        ) + 1
        if rendered_chars <= max_chars:
            selected = candidate
    return {
        "count": len(selected),
        "subject_id": subject_id,
        "max_chars": max_chars,
        "memories": selected,
    }


def command_context(args: argparse.Namespace) -> None:
    subject_id = validate_text("subject_id", args.subject_id, 64)
    with connect() as conn:
        require_enabled(conn)
        refresh_hypothesis_lifecycle(conn)
        rows = list_memories(conn, subject_id)
    emit(build_context_payload(rows, subject_id, args.max_chars))


def command_style_status(args: argparse.Namespace) -> None:
    with connect() as conn:
        require_enabled(conn)
        row = conn.execute(
            "SELECT * FROM memories WHERE scope = 'user' AND subject_id = 'user' "
            "AND field = 'current_style' AND status = 'active' LIMIT 1"
        ).fetchone()
    if not row:
        emit(
            {
                "exists": False,
                "review_status": "missing",
                "action": "ask_user_before_creating_current_style",
            }
        )
        return
    review_status = current_style_review_status(row["updated_at"], args.max_age_days)
    emit(
        {
            "exists": True,
            "current_style": row["value"],
            "updated_at": row["updated_at"],
            "review_status": review_status,
            "action": (
                "suggest_update_and_wait_for_user_confirmation"
                if review_status != "current"
                else "use_as_baseline_not_as_cap"
            ),
        }
    )


def command_delete(args: argparse.Namespace) -> None:
    if not args.confirm:
        raise MemoryErrorWithCode(
            "CONFIRMATION_REQUIRED", "需要 --confirm 才能删除指定记忆字段"
        )
    scope = validate_text("scope", args.scope, 32)
    if scope not in SCOPES:
        raise MemoryErrorWithCode("INVALID_SCOPE", f"不支持的 scope: {scope}")
    subject_id = validate_text("subject_id", args.subject_id, 64)
    field = validate_text("field", args.field, 64)
    if scope == "user" and subject_id != "user":
        raise MemoryErrorWithCode(
            "INVALID_SUBJECT", "用户共享状态必须使用 subject_id=user"
        )
    occurred_at = validate_iso_timestamp(
        "occurred_at", args.occurred_at or "", required=False
    )
    if occurred_at and scope != "event":
        raise MemoryErrorWithCode(
            "INVALID_TIMESTAMP", "--occurred-at 只用于区分 event 记录"
        )
    with connect() as conn:
        query = (
            "SELECT * FROM memories WHERE scope = ? AND subject_id = ? "
            "AND field = ? AND status = 'active'"
        )
        params: list[Any] = [scope, subject_id, field]
        if occurred_at:
            query += " AND occurred_at = ?"
            params.append(occurred_at)
        rows = conn.execute(query, params).fetchall()
        if not rows:
            raise MemoryErrorWithCode("NOT_FOUND", "没有匹配的活动记忆记录")
        if len(rows) > 1:
            raise MemoryErrorWithCode(
                "AMBIGUOUS_DELETE", "匹配多条事件；请补充 --occurred-at 精确删除"
            )
        before = [row_dict(rows[0])]
        op_id = uuid.uuid4().hex
        timestamp = now_iso()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM memories WHERE id = ?", (rows[0]["id"],))
        conn.execute(
            "INSERT INTO operations(op_id, created_at, action, before_json, after_json) "
            "VALUES(?, ?, 'delete', ?, '[]')",
            (op_id, timestamp, json.dumps(before, ensure_ascii=False)),
        )
        prune_operation_history(conn)
        conn.commit()
    emit(
        {
            "ok": True,
            "op_id": op_id,
            "deleted": {"scope": scope, "subject_id": subject_id, "field": field},
        }
    )


def command_record_technique(args: argparse.Namespace) -> None:
    if not args.confirm_sent:
        raise MemoryErrorWithCode(
            "CONFIRMATION_REQUIRED",
            "只有用户确认实际发送后才能使用 --confirm-sent 记录技巧",
        )
    subject_id = validate_text("subject_id", args.subject_id, 64)
    if subject_id == "user":
        raise MemoryErrorWithCode(
            "INVALID_SUBJECT", "recent_techniques 必须绑定具体对象，不能写入 user"
        )
    with connect() as conn:
        require_enabled(conn)
        row = conn.execute(
            "SELECT value FROM memories WHERE scope = 'object' AND subject_id = ? "
            "AND field = 'recent_techniques' AND status = 'active' LIMIT 1",
            (subject_id,),
        ).fetchone()
    history: list[str] = []
    if row:
        try:
            loaded = json.loads(row["value"])
        except json.JSONDecodeError as exc:
            raise MemoryErrorWithCode(
                "CORRUPT_MEMORY", "recent_techniques 不是有效 JSON，请先查看并修复"
            ) from exc
        if not isinstance(loaded, list):
            raise MemoryErrorWithCode(
                "CORRUPT_MEMORY", "recent_techniques 不是数组，请先查看并修复"
            )
        history = loaded
    history.append(args.technique)
    history = history[-RECENT_TECHNIQUE_LIMIT:]
    delta = {
        "scope": "object",
        "subject_id": subject_id,
        "field": "recent_techniques",
        "value": json.dumps(history, ensure_ascii=False, separators=(",", ":")),
        "source_type": "user_report",
        "source_ref": "record-technique:confirmed-sent",
        "confidence": "high",
    }
    command_apply(argparse.Namespace(json=json.dumps(delta, ensure_ascii=False), file=None))


def command_forget_object(args: argparse.Namespace) -> None:
    if not args.confirm:
        raise MemoryErrorWithCode("CONFIRMATION_REQUIRED", "需要 --confirm 才能永久删除对象档案")
    subject_id = validate_text("subject_id", args.subject_id, 64)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        deleted = conn.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE subject_id = ?", (subject_id,)
        ).fetchone()["n"]
        conn.execute("DELETE FROM memories WHERE subject_id = ?", (subject_id,))
        conn.execute("DELETE FROM operations")
        conn.commit()
        conn.execute("VACUUM")
    emit({"ok": True, "subject_id": subject_id, "deleted": deleted, "undo_history_cleared": True})


def command_revoke(args: argparse.Namespace) -> None:
    if not args.confirm:
        raise MemoryErrorWithCode("CONFIRMATION_REQUIRED", "需要 --confirm 才能撤回长期记忆同意")
    if args.delete:
        target = db_path()
        if target.exists():
            target.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(target) + suffix)
            if sidecar.exists():
                sidecar.unlink()
        emit({"ok": True, "consent_enabled": False, "deleted": True})
        return
    with connect() as conn:
        set_setting(conn, "consent_enabled", "false")
        set_setting(conn, "paused", "true")
        conn.commit()
    emit({"ok": True, "consent_enabled": False, "deleted": False})


def command_clear(args: argparse.Namespace) -> None:
    if not args.confirm:
        raise MemoryErrorWithCode("CONFIRMATION_REQUIRED", "需要 --confirm 才能永久清空长期记忆")
    target = db_path()
    if target.exists():
        target.unlink()
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(target) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    emit({"ok": True, "deleted": True})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status").set_defaults(func=command_status)
    enable = subparsers.add_parser("enable")
    enable.add_argument("--confirm", action="store_true")
    enable.set_defaults(func=command_enable)
    subparsers.add_parser("pause").set_defaults(func=command_pause)
    subparsers.add_parser("resume").set_defaults(func=command_resume)

    apply_cmd = subparsers.add_parser("apply")
    source = apply_cmd.add_mutually_exclusive_group()
    source.add_argument("--json")
    source.add_argument("--file")
    apply_cmd.set_defaults(func=command_apply)

    undo = subparsers.add_parser("undo")
    undo.add_argument("--op-id")
    undo.set_defaults(func=command_undo)

    show = subparsers.add_parser("show")
    show.add_argument("--subject-id")
    show.set_defaults(func=command_show)
    context = subparsers.add_parser("context")
    context.add_argument("--subject-id", required=True)
    context.add_argument("--max-chars", type=int, default=4000, choices=range(500, 8001))
    context.set_defaults(func=command_context)
    style_status = subparsers.add_parser("style-status")
    style_status.add_argument(
        "--max-age-days", type=int, default=STYLE_REVIEW_DAYS, choices=range(0, 3651)
    )
    style_status.set_defaults(func=command_style_status)

    delete = subparsers.add_parser("delete")
    delete.add_argument("--scope", required=True)
    delete.add_argument("--subject-id", required=True)
    delete.add_argument("--field", required=True)
    delete.add_argument("--occurred-at")
    delete.add_argument("--confirm", action="store_true")
    delete.set_defaults(func=command_delete)

    technique = subparsers.add_parser("record-technique")
    technique.add_argument("--subject-id", required=True)
    technique.add_argument("--technique", required=True, choices=sorted(TECHNIQUES))
    technique.add_argument("--confirm-sent", action="store_true")
    technique.set_defaults(func=command_record_technique)

    forget = subparsers.add_parser("forget-object")
    forget.add_argument("subject_id")
    forget.add_argument("--confirm", action="store_true")
    forget.set_defaults(func=command_forget_object)

    revoke = subparsers.add_parser("revoke")
    revoke.add_argument("--delete", action="store_true")
    revoke.add_argument("--confirm", action="store_true")
    revoke.set_defaults(func=command_revoke)
    clear = subparsers.add_parser("clear")
    clear.add_argument("--confirm", action="store_true")
    clear.set_defaults(func=command_clear)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        args.func(args)
        return 0
    except (MemoryErrorWithCode, json.JSONDecodeError, OSError, sqlite3.Error) as exc:
        code = exc.code if isinstance(exc, MemoryErrorWithCode) else exc.__class__.__name__.upper()
        emit({"ok": False, "error": {"code": code, "message": str(exc)}})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

# Modified by AI on 2026-08-21 16:32:17
