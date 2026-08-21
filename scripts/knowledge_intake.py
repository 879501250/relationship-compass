#!/usr/bin/env python3
"""Register local sources and prepare reviewable knowledge proposals."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from date_utils import normalize_iso8601, utc_now_iso
from knowledge_schema import (
    KnowledgeSchemaError,
    fingerprint_file,
    freshness_status,
    load_registry,
    validate_claim,
    validate_registry,
    validate_source,
)


ROOT = Path(__file__).resolve().parents[1]
PROPOSAL_PATTERN = re.compile(
    r"^## Claim: (?P<claim_id>claim-[0-9a-f]{16})\s*$"
    r"(?P<body>.*?)(?=^## Claim: claim-[0-9a-f]{16}\s*$|\Z)",
    re.MULTILINE | re.DOTALL,
)
JSON_BLOCK_PATTERN = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


class KnowledgeIntakeError(RuntimeError):
    """Raised when an intake transition is unsafe or incomplete."""


@dataclass(frozen=True)
class KnowledgePaths:
    root: Path

    @property
    def management(self) -> Path:
        return self.root / "knowledge-management"

    @property
    def registry(self) -> Path:
        return self.management / "SOURCE_REGISTRY.json"

    @property
    def local_registry(self) -> Path:
        return self.management / "SOURCE_REGISTRY.local.json"

    @property
    def source_cards(self) -> Path:
        return self.management / "source-cards"

    @property
    def proposals(self) -> Path:
        return self.management / "proposals"

    @property
    def curated_store(self) -> Path:
        return self.management / "CURATED_CLAIMS.json"


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content.rstrip() + "\n", encoding="utf-8")
    temporary.replace(path)


def load_local_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "sources": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeIntakeError(f"cannot load local registry: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("sources"), dict)
    ):
        raise KnowledgeIntakeError("invalid SOURCE_REGISTRY.local.json")
    return payload


def render_source_card(source: dict[str, Any]) -> str:
    topics = ", ".join(source["topics"])
    return f"""# Source Card: `{source['source_id']}`

## Metadata

- Source ID: `{source['source_id']}`
- Title: {source['title']}
- Author: {source['author']}
- Edition: {source['edition'] or '未注明'}
- Source type: `{source['source_type']}`
- Topics: {topics}
- Registry status: `candidate`

## Why Added

待补充。

## Core Themes

待补充。

## Candidate Claims

待按 claim schema 提取；以释义为主，不复制章节正文。

## Evidence Assessment

分别判断 source quality 与 claim evidence。

## Conflicts

待与现有 curated claims 比较。

## Practical Value

待补充。

## Risks / Biases

检查操控、刻板印象、伪心理学、作者经验外推、营销和故事因果化。

## Recommended Integration

人工审核前不得写入 curated runtime。

## Rejected Ideas

无。

## Source Anchors

待补充页码、章节、论文节或其他可复核定位。
"""


def register_source(
    paths: KnowledgePaths, source_path: Path, metadata: dict[str, Any], timestamp: str
) -> dict[str, Any]:
    if not source_path.is_file():
        raise KnowledgeIntakeError(f"source file not found: {source_path}")
    registry = load_registry(paths.registry)
    fingerprint = fingerprint_file(source_path)
    if any(item["source_id"] == metadata["source_id"] for item in registry["sources"]):
        raise KnowledgeIntakeError(f"duplicate source_id: {metadata['source_id']}")
    duplicate = next(
        (
            item
            for item in registry["sources"]
            if item["content_fingerprint"] == fingerprint
        ),
        None,
    )
    if duplicate:
        raise KnowledgeIntakeError(
            f"duplicate source content: already registered as {duplicate['source_id']}"
        )
    normalized_time = normalize_iso8601(timestamp, field_name="timestamp")
    source = validate_source(
        {
            **metadata,
            "added_at": normalized_time,
            "last_reviewed_at": normalized_time,
            "status": "candidate",
            "content_fingerprint": fingerprint,
        }
    )
    updated_registry = {
        "schema_version": 1,
        "sources": sorted(
            [*registry["sources"], source], key=lambda item: item["source_id"]
        ),
    }
    validate_registry(updated_registry)

    local = load_local_registry(paths.local_registry)
    local["sources"][source["source_id"]] = {
        "path": str(source_path.resolve()),
        "content_fingerprint": fingerprint,
    }
    atomic_write_json(paths.registry, updated_registry)
    atomic_write_json(paths.local_registry, local)
    atomic_write_text(
        paths.source_cards / f"{source['source_id']}.md", render_source_card(source)
    )
    return source


def load_claim_input(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeIntakeError(f"cannot load claims: {exc}") from exc
    claims = payload.get("claims") if isinstance(payload, dict) else payload
    if not isinstance(claims, list) or not claims:
        raise KnowledgeIntakeError("claims input must be a non-empty list")
    return [validate_claim(claim) for claim in claims]


def render_proposal(source_id: str, claims: list[dict[str, Any]]) -> str:
    parts = [
        f"# Knowledge Proposal: `{source_id}`",
        "",
        "每条 claim 必须且只能勾选一个决定。`revise` 后先修改 JSON claim，再重新校验；冲突 claim 还必须填写 resolution。",
        "",
    ]
    for claim in sorted(claims, key=lambda item: item["claim_id"]):
        relation = claim["relation_to_existing"]
        parts.extend(
            [
                f"## Claim: {claim['claim_id']}",
                "",
                "- Decision: [ ] approve [ ] reject [ ] revise",
                f"- Evidence: `{claim['claim_evidence']}`",
                f"- Overlap / Conflict: `{relation['type']}` -> {', '.join(relation['claim_ids']) or 'none'}",
                "- Conflict Resolution: `pending`",
                "- Reason: 待审核",
                "",
                "```json",
                json.dumps(claim, ensure_ascii=False, indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    return "\n".join(parts)


def parse_proposal(path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise KnowledgeIntakeError(f"cannot load proposal: {exc}") from exc
    header = re.search(r"^# Knowledge Proposal: `(?P<source_id>src-[a-z0-9-]+)`\s*$", content, re.MULTILINE)
    if not header:
        raise KnowledgeIntakeError("proposal is missing a valid source header")
    source_id = header.group("source_id")
    entries: list[dict[str, Any]] = []
    for match in PROPOSAL_PATTERN.finditer(content):
        block = JSON_BLOCK_PATTERN.search(match.group("body"))
        if not block:
            raise KnowledgeIntakeError(f"{match.group('claim_id')} is missing a JSON block")
        try:
            claim = validate_claim(json.loads(block.group(1)))
        except (json.JSONDecodeError, KnowledgeSchemaError) as exc:
            raise KnowledgeIntakeError(
                f"invalid claim {match.group('claim_id')}: {exc}"
            ) from exc
        if claim["claim_id"] != match.group("claim_id"):
            raise KnowledgeIntakeError("claim heading and JSON claim_id do not match")
        if claim["source_id"] != source_id:
            raise KnowledgeIntakeError("proposal contains a claim for another source")
        entries.append({"claim": claim, "review_text": match.group("body")})
    if not entries:
        raise KnowledgeIntakeError("proposal contains no claims")
    claim_ids = [entry["claim"]["claim_id"] for entry in entries]
    if len(claim_ids) != len(set(claim_ids)):
        raise KnowledgeIntakeError("proposal contains duplicate claim_id values")
    return {"source_id": source_id, "entries": entries, "path": path}


def command_register(args: argparse.Namespace) -> None:
    paths = KnowledgePaths(args.root.resolve())
    metadata = {
        "source_id": args.source_id,
        "title": args.title,
        "author": args.author,
        "source_type": args.source_type,
        "publication_year": args.publication_year,
        "edition": args.edition,
        "language": args.language,
        "topics": args.topics,
        "source_quality": args.source_quality,
        "freshness": args.freshness,
    }
    source = register_source(paths, args.source.resolve(), metadata, args.at or utc_now_iso())
    emit({"ok": True, "source": source, "raw_copied": False})


def command_proposal(args: argparse.Namespace) -> None:
    paths = KnowledgePaths(args.root.resolve())
    registry = load_registry(paths.registry)
    if not any(item["source_id"] == args.source_id for item in registry["sources"]):
        raise KnowledgeIntakeError(f"unknown source_id: {args.source_id}")
    claims = load_claim_input(args.claims)
    if any(claim["source_id"] != args.source_id for claim in claims):
        raise KnowledgeIntakeError("all claims must belong to --source-id")
    target = paths.proposals / f"{args.source_id}-proposal.md"
    atomic_write_text(target, render_proposal(args.source_id, claims))
    emit({"ok": True, "proposal": str(target), "claims": len(claims), "status": "pending_review"})


def command_validate(args: argparse.Namespace) -> None:
    paths = KnowledgePaths(args.root.resolve())
    registry = load_registry(paths.registry)
    proposal_count = 0
    targets = [args.proposal.resolve()] if args.proposal else sorted(paths.proposals.glob("*-proposal.md"))
    for target in targets:
        if target.is_file():
            parse_proposal(target)
            proposal_count += 1
    emit({"ok": True, "sources": len(registry["sources"]), "proposals": proposal_count})


def command_status(args: argparse.Namespace) -> None:
    registry = load_registry(KnowledgePaths(args.root.resolve()).registry)
    reference_at = args.at or utc_now_iso()
    statuses: dict[str, int] = {}
    review_due = 0
    for source in registry["sources"]:
        statuses[source["status"]] = statuses.get(source["status"], 0) + 1
        if freshness_status(source, reference_at) == "review_due":
            review_due += 1
    emit({"ok": True, "sources": len(registry["sources"]), "statuses": statuses, "review_due": review_due})


def command_list(args: argparse.Namespace) -> None:
    registry = load_registry(KnowledgePaths(args.root.resolve()).registry)
    sources = registry["sources"]
    if args.status:
        sources = [source for source in sources if source["status"] == args.status]
    emit({"ok": True, "count": len(sources), "sources": sources})


def command_deprecate(args: argparse.Namespace) -> None:
    if not args.confirm:
        raise KnowledgeIntakeError("deprecate requires --confirm")
    paths = KnowledgePaths(args.root.resolve())
    registry = load_registry(paths.registry)
    source = next(
        (item for item in registry["sources"] if item["source_id"] == args.source_id),
        None,
    )
    if not source:
        raise KnowledgeIntakeError(f"unknown source_id: {args.source_id}")
    source["status"] = "deprecated"
    source["last_reviewed_at"] = normalize_iso8601(
        args.at or utc_now_iso(), field_name="timestamp"
    )
    validate_registry(registry)
    atomic_write_json(paths.registry, registry)
    emit({"ok": True, "source_id": args.source_id, "status": "deprecated"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    register = subparsers.add_parser("register")
    register.add_argument("source", type=Path)
    register.add_argument("--source-id", required=True)
    register.add_argument("--title", required=True)
    register.add_argument("--author", required=True)
    register.add_argument("--source-type", choices=["book", "paper", "course", "article", "note"], required=True)
    register.add_argument("--publication-year", type=int, required=True)
    register.add_argument("--edition")
    register.add_argument("--language", default="zh-CN")
    register.add_argument("--topics", nargs="+", required=True)
    register.add_argument("--source-quality", choices=["high", "medium", "low", "unknown"], default="unknown")
    register.add_argument("--freshness", choices=["stable", "semi_dynamic", "dynamic"], required=True)
    register.add_argument("--at", help=argparse.SUPPRESS)
    register.set_defaults(func=command_register)

    proposal = subparsers.add_parser("proposal")
    proposal.add_argument("--source-id", required=True)
    proposal.add_argument("--claims", type=Path, required=True)
    proposal.set_defaults(func=command_proposal)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--proposal", type=Path)
    validate.set_defaults(func=command_validate)

    status = subparsers.add_parser("status")
    status.add_argument("--at", help=argparse.SUPPRESS)
    status.set_defaults(func=command_status)

    listing = subparsers.add_parser("list")
    listing.add_argument("--status")
    listing.set_defaults(func=command_list)
    deprecate = subparsers.add_parser("deprecate")
    deprecate.add_argument("--source-id", required=True)
    deprecate.add_argument("--confirm", action="store_true")
    deprecate.add_argument("--at", help=argparse.SUPPRESS)
    deprecate.set_defaults(func=command_deprecate)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (KnowledgeIntakeError, KnowledgeSchemaError, ValueError) as exc:
        emit({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

# Modified by AI on 2026-08-21 17:01:55
