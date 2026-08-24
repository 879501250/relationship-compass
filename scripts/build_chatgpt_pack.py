#!/usr/bin/env python3
"""Build a deterministic, privacy-bounded ChatGPT Project knowledge pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from date_utils import normalize_iso8601, utc_now_iso
from knowledge_intake import atomic_write_json, atomic_write_text
from knowledge_merge import load_curated_store
from knowledge_schema import load_registry


ROOT = Path(__file__).resolve().parents[1]
PACK_VERSION = "1.2.0"
PACK_MANIFEST: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "01-CORE_POLICY.md",
        "共享核心政策",
        (
            "shared/CORE_POLICY.md",
            "shared/FACT_HYPOTHESIS_POLICY.md",
        ),
    ),
    (
        "02-DAILY_CONVERSATION.md",
        "即时回复、幽默与主动开题",
        (
            "references/personal/自然回复生成器.md",
            "references/personal/幽默与调侃生成器.md",
            "references/personal/主动话题与conversation-hook.md",
        ),
    ),
    (
        "03-RELATIONSHIP_SIGNALS.md",
        "关系阶段、节奏与投入",
        (
            "references/personal/关系阶段与聊天节奏.md",
            "references/personal/投入预算与停止条件.md",
        ),
    ),
    (
        "04-GROWTH_AND_REVIEW.md",
        "表达成长与复盘",
        (
            "references/personal/网络聊天表达升级器.md",
            "references/personal/复盘模式与实际发送学习闭环.md",
        ),
    ),
    (
        "05-SAFETY_AND_EVIDENCE.md",
        "证据、截图、安全与边界",
        (
            "references/personal/微信截图解析协议.md",
            "references/knowledge/01-证据分级与内容边界.md",
            "references/knowledge/05-PUA操控与伦理替代.md",
            "references/knowledge/08-同意边界性与亲密.md",
            "references/knowledge/17-中国法律安全与危机转介.md",
        ),
    ),
    (
        "06-CURATED_CLAIMS.md",
        "经人工批准的增量知识",
        (
            "references/curated/INDEX.md",
            "references/curated/relationship-start.md",
            "references/curated/conversation.md",
            "references/curated/attraction.md",
            "references/curated/intimacy.md",
            "references/curated/boundaries.md",
            "references/curated/conflict-and-repair.md",
            "references/curated/personal-growth.md",
        ),
    ),
)
FORBIDDEN_PACK_TOKENS = (
    "memory.sqlite3",
    "memory_store.py",
    "SOURCE_REGISTRY.local.json",
    "knowledge-management/proposals",
    "knowledge-management/review-decisions",
    "knowledge-management/raw",
)
LOCAL_PATH_PATTERN = re.compile(
    r"(?:[A-Za-z]:\\(?:Users|Documents and Settings)\\|/(?:Users|home)/[^/\s]+/)"
)
class PackBuildError(RuntimeError):
    """Raised when pack inputs or privacy boundaries are invalid."""


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def revision_for_files(root: Path, relative_paths: list[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(relative_paths):
        path = root / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def clean_markdown(content: str) -> str:
    return re.sub(
        r"\[([^\]]+)\]\((?!https?://|mailto:)[^)]+\)",
        r"\1",
        content.rstrip(),
    )


def validate_pack_body(name: str, body: str) -> None:
    for token in FORBIDDEN_PACK_TOKENS:
        if token in body:
            raise PackBuildError(f"{name} contains forbidden runtime token: {token}")
    if LOCAL_PATH_PATTERN.search(body):
        raise PackBuildError(f"{name} contains a local absolute path")


def build_knowledge_bodies(root: Path) -> dict[str, str]:
    bodies: dict[str, str] = {}
    for filename, title, relative_paths in PACK_MANIFEST:
        lines = [
            f"# {title}",
            "",
            "<!-- Generated knowledge body. Do not hand edit. -->",
            "",
        ]
        for relative in relative_paths:
            path = root / relative
            if not path.is_file():
                raise PackBuildError(f"missing pack input: {relative}")
            lines.extend(
                [
                    f"## 来源：`{relative}`",
                    "",
                    clean_markdown(path.read_text(encoding="utf-8")),
                    "",
                ]
            )
        body = "\n".join(lines).rstrip() + "\n"
        validate_pack_body(filename, body)
        bodies[filename] = body
    if not 4 <= len(bodies) <= 8:
        raise PackBuildError("knowledge pack must contain 4-8 theme files")
    return bodies


def pack_metadata(root: Path, built_at: str | None = None) -> dict[str, Any]:
    registry = load_registry(root / "knowledge-management" / "SOURCE_REGISTRY.json")
    store = load_curated_store(root / "knowledge-management" / "CURATED_CLAIMS.json")
    source_status = {
        source["source_id"]: source["status"] for source in registry["sources"]
    }
    included_sources = sorted(
        {
            provenance["source_id"]
            for claim in store["claims"]
            for provenance in claim["sources"]
        }
    )
    rejected = [
        source_id
        for source_id in included_sources
        if source_status.get(source_id) in {None, "rejected"}
    ]
    if rejected:
        raise PackBuildError(
            "curated claims reference unknown or rejected sources: "
            + ", ".join(rejected)
        )
    curated_inputs = [relative for _, _, paths in PACK_MANIFEST for relative in paths if relative.startswith("references/curated/")]
    registry_payload = json.dumps(
        registry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "pack_version": PACK_VERSION,
        "built_at": normalize_iso8601(
            built_at or utc_now_iso(), field_name="built_at"
        ),
        "skill_revision": f"sha256:{sha256_bytes((root / 'SKILL.md').read_bytes())}",
        "curated_revision": revision_for_files(root, curated_inputs),
        "source_registry_revision": f"sha256:{sha256_bytes(registry_payload)}",
        "included_claim_ids": sorted(claim["claim_id"] for claim in store["claims"]),
        "included_sources": included_sources,
    }


def write_pack(
    root: Path, output: Path, built_at: str | None = None
) -> dict[str, Any]:
    bodies = build_knowledge_bodies(root)
    output.mkdir(parents=True, exist_ok=True)
    for filename, body in bodies.items():
        atomic_write_text(output / filename, body)
    metadata = pack_metadata(root, built_at)
    atomic_write_json(output / "KNOWLEDGE_PACK_INFO.json", metadata)
    return {
        "ok": True,
        "output": str(output),
        "theme_files": sorted(bodies),
        "metadata": metadata,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "chatgpt-project" / "generated-knowledge",
    )
    parser.add_argument("--built-at", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = write_pack(
            args.root.resolve(), args.output.resolve(), built_at=args.built_at
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (PackBuildError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
