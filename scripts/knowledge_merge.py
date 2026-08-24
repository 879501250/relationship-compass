#!/usr/bin/env python3
"""Review proposals and merge only explicitly approved claims into runtime."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from date_utils import add_days_iso, normalize_iso8601, utc_now_iso
from knowledge_intake import (
    KnowledgeIntakeError,
    KnowledgePaths,
    atomic_write_json,
    atomic_write_text,
    emit,
    parse_proposal,
)
from knowledge_schema import (
    FRESHNESS_REVIEW_DAYS,
    KnowledgeSchemaError,
    fingerprint_file,
    load_registry,
)


ROOT = Path(__file__).resolve().parents[1]
DECISION_PATTERN = re.compile(r"\[([ xX])\]\s*(approve|reject|revise)")
RESOLUTION_PATTERN = re.compile(r"^- Conflict Resolution:\s*`?([a-z_]+)`?\s*$", re.MULTILINE)
REASON_PATTERN = re.compile(r"^- Reason:\s*(.+?)\s*$", re.MULTILINE)
CONFLICT_RESOLUTIONS = {
    "keep_existing",
    "merge_with_conditions",
    "keep_both",
    "replace_existing",
    "reject_new",
}


class KnowledgeMergeError(RuntimeError):
    """Raised when review or merge gates are not satisfied."""


def find_source(registry: dict[str, Any], source_id: str) -> dict[str, Any]:
    source = next(
        (item for item in registry["sources"] if item["source_id"] == source_id),
        None,
    )
    if not source:
        raise KnowledgeMergeError(f"unknown source_id: {source_id}")
    return source


def parse_review(entry: dict[str, Any]) -> dict[str, Any]:
    review_text = entry["review_text"]
    choices = DECISION_PATTERN.findall(review_text)
    if {label for _, label in choices} != {"approve", "reject", "revise"}:
        raise KnowledgeMergeError(
            f"{entry['claim']['claim_id']} must contain approve/reject/revise checkboxes"
        )
    checked = [label for mark, label in choices if mark.strip().lower() == "x"]
    if len(checked) != 1:
        raise KnowledgeMergeError(
            f"{entry['claim']['claim_id']} must select exactly one decision"
        )
    resolution_match = RESOLUTION_PATTERN.search(review_text)
    resolution = resolution_match.group(1) if resolution_match else "pending"
    reason_match = REASON_PATTERN.search(review_text)
    reason = reason_match.group(1).strip() if reason_match else ""
    if checked[0] == "approve" and entry["claim"]["relation_to_existing"]["type"] == "conflicts":
        if resolution not in CONFLICT_RESOLUTIONS:
            raise KnowledgeMergeError(
                f"{entry['claim']['claim_id']} requires an explicit conflict resolution"
            )
    return {
        "claim_id": entry["claim"]["claim_id"],
        "decision": checked[0],
        "conflict_resolution": resolution,
        "reason": reason,
    }


def review_proposal(
    paths: KnowledgePaths,
    proposal_path: Path,
    reviewer: str,
    timestamp: str,
    *,
    confirmed: bool,
) -> dict[str, Any]:
    if not confirmed:
        raise KnowledgeMergeError("review requires --confirm")
    parsed = parse_proposal(proposal_path)
    registry = load_registry(paths.registry)
    source = find_source(registry, parsed["source_id"])
    decisions = [parse_review(entry) for entry in parsed["entries"]]
    curated = load_curated_store(paths.curated_store)
    curated_by_id = {item["claim_id"]: item for item in curated["claims"]}
    for entry, decision in zip(parsed["entries"], decisions):
        claim = entry["claim"]
        if claim["relation_to_existing"]["type"] == "conflicts":
            existing_claims = []
            for claim_id in claim["relation_to_existing"]["claim_ids"]:
                existing = curated_by_id.get(claim_id)
                existing_claims.append(
                    {
                        "claim_id": claim_id,
                        "canonical_claim": existing.get("canonical_claim") if existing else None,
                        "evidence": existing.get("evidence") if existing else "unknown",
                    }
                )
            decision["conflict"] = {
                "existing_claims": existing_claims,
                "new_claim": claim["canonical_claim"],
                "new_evidence": claim["claim_evidence"],
                "explanation": decision["reason"],
                "resolution": decision["conflict_resolution"],
            }
    normalized_time = normalize_iso8601(timestamp, field_name="timestamp")
    selected = {decision["decision"] for decision in decisions}
    if selected == {"approve"}:
        source["status"] = "approved"
    elif selected == {"reject"}:
        source["status"] = "rejected"
    elif "approve" in selected:
        source["status"] = "partially_approved"
    else:
        source["status"] = "reviewed"
    source["last_reviewed_at"] = normalized_time
    decision_record = {
        "schema_version": 1,
        "source_id": parsed["source_id"],
        "proposal": proposal_path.name,
        "proposal_fingerprint": fingerprint_file(proposal_path),
        "reviewed_at": normalized_time,
        "reviewer": reviewer,
        "decisions": decisions,
    }
    decision_path = paths.management / "review-decisions" / f"{parsed['source_id']}-review.json"
    atomic_write_json(paths.registry, registry)
    atomic_write_json(decision_path, decision_record)
    return decision_record


def load_curated_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "claims": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeMergeError(f"cannot load curated store: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("claims"), list)
    ):
        raise KnowledgeMergeError("invalid CURATED_CLAIMS.json")
    return payload


def source_provenance(claim: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": claim["source_id"],
        "source_anchor": claim["source_anchor"],
        "claim_type": claim["claim_type"],
        "claim_evidence": claim["claim_evidence"],
        "confidence": claim["confidence"],
    }


def curated_claim(
    claim: dict[str, Any], source: dict[str, Any], reviewed_at: str
) -> dict[str, Any]:
    last_reviewed_at = claim.get("last_reviewed_at") or reviewed_at
    review_after = claim.get("review_after") or source.get("review_after")
    if not review_after:
        review_after = add_days_iso(
            last_reviewed_at,
            FRESHNESS_REVIEW_DAYS[source["freshness"]],
            field_name="last_reviewed_at",
        )
    return {
        "claim_id": claim["claim_id"],
        "canonical_claim": claim["canonical_claim"],
        "practical_meaning": claim["canonical_claim"],
        "evidence": claim["claim_evidence"],
        "applicable_when": claim["applicable_when"],
        "limits": claim["not_applicable_when"],
        "risks": claim["risk_of_misuse"],
        "sources": [source_provenance(claim)],
        "destination": claim["proposed_destination"],
        "last_reviewed_at": last_reviewed_at,
        "review_after": review_after,
    }


def merge_approved_claims(
    store: dict[str, Any],
    claims: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    source: dict[str, Any],
    reviewed_at: str,
    confirmed_replacements: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    confirmed_replacements = confirmed_replacements or set()
    working = copy.deepcopy(store)
    by_id = {item["claim_id"]: item for item in working["claims"]}
    decision_by_id = {item["claim_id"]: item for item in decisions}
    outcomes: list[dict[str, Any]] = []
    for claim in sorted(claims, key=lambda item: item["claim_id"]):
        decision = decision_by_id.get(claim["claim_id"])
        if not decision or decision["decision"] != "approve":
            outcomes.append({"claim_id": claim["claim_id"], "action": "not_approved"})
            continue
        relation = claim["relation_to_existing"]
        if relation["type"] == "conflicts":
            missing_existing = set(relation["claim_ids"]) - set(by_id)
            if missing_existing:
                raise KnowledgeMergeError(
                    "conflict references missing claims: "
                    + ", ".join(sorted(missing_existing))
                )
            resolution = decision.get("conflict_resolution")
            if resolution not in CONFLICT_RESOLUTIONS:
                raise KnowledgeMergeError(f"unresolved conflict: {claim['claim_id']}")
            if resolution in {"keep_existing", "reject_new"}:
                outcomes.append({"claim_id": claim["claim_id"], "action": resolution})
                continue
            if resolution == "replace_existing":
                required = set(relation["claim_ids"])
                if not required.issubset(confirmed_replacements):
                    missing = ", ".join(sorted(required - confirmed_replacements))
                    raise KnowledgeMergeError(
                        f"replace_existing requires --confirm-replace for: {missing}"
                    )
                working["claims"] = [
                    item for item in working["claims"] if item["claim_id"] not in required
                ]
                by_id = {item["claim_id"]: item for item in working["claims"]}
        existing = by_id.get(claim["claim_id"])
        provenance = source_provenance(claim)
        if existing:
            if not any(
                item["source_id"] == provenance["source_id"]
                and item["source_anchor"] == provenance["source_anchor"]
                for item in existing["sources"]
            ):
                existing["sources"].append(provenance)
                existing["sources"].sort(
                    key=lambda item: (item["source_id"], item["source_anchor"])
                )
            action = "merged_provenance"
        else:
            created = curated_claim(claim, source, reviewed_at)
            working["claims"].append(created)
            by_id[claim["claim_id"]] = created
            action = "added"
        outcomes.append(
            {
                "claim_id": claim["claim_id"],
                "action": action,
                "conflict_resolution": decision.get("conflict_resolution"),
            }
        )
    working["claims"].sort(key=lambda item: item["claim_id"])
    return working, outcomes


def render_topic(topic: str, claims: list[dict[str, Any]]) -> str:
    title = topic.replace("-", " ").title()
    lines = [f"# {title}", ""]
    if not claims:
        lines.extend(["暂无经 curated knowledge intake 流程批准的新增 claim。", ""])
    for claim in claims:
        sources = "; ".join(
            f"{source['source_id']} ({source['source_anchor']})"
            for source in claim["sources"]
        )
        lines.extend(
            [
                "## Claim",
                "",
                f"- Claim ID: `{claim['claim_id']}`",
                f"- Practical Meaning: {claim['practical_meaning']}",
                f"- Evidence: `{claim['evidence']}`",
                f"- Applicable When: {'；'.join(claim['applicable_when']) or '未限定'}",
                f"- Limits: {'；'.join(claim['limits']) or '无额外限制'}",
                f"- Risks: {'；'.join(claim['risks']) or '未发现特定风险'}",
                f"- Sources: {sources}",
                "",
            ]
        )
    return "\n".join(lines)


def render_index(claims: list[dict[str, Any]]) -> str:
    lines = [
        "# Curated Knowledge Index",
        "",
        "| Topic | Claim ID | Destination File | Evidence | Last Reviewed | Sources |",
        "|---|---|---|---|---|---|",
    ]
    for claim in sorted(claims, key=lambda item: (item["destination"], item["claim_id"])):
        source_ids = ", ".join(source["source_id"] for source in claim["sources"])
        lines.append(
            f"| {claim['destination']} | `{claim['claim_id']}` | "
            f"`{claim['destination']}.md` | {claim['evidence']} | "
            f"{claim['last_reviewed_at']} | {source_ids} |"
        )
    return "\n".join(lines)


def write_curated_runtime(paths: KnowledgePaths, store: dict[str, Any]) -> None:
    curated_dir = paths.root / "references" / "curated"
    topics = (
        "relationship-start",
        "conversation",
        "attraction",
        "intimacy",
        "boundaries",
        "conflict-and-repair",
        "personal-growth",
    )
    for topic in topics:
        claims = [item for item in store["claims"] if item["destination"] == topic]
        atomic_write_text(
            curated_dir / f"{topic}.md",
            render_topic(topic, claims),
        )
    atomic_write_text(
        curated_dir / "INDEX.md", render_index(store["claims"])
    )


def command_review(args: argparse.Namespace) -> None:
    paths = KnowledgePaths(args.root.resolve())
    record = review_proposal(
        paths,
        args.proposal.resolve(),
        args.reviewer,
        args.at or utc_now_iso(),
        confirmed=args.confirm,
    )
    emit({"ok": True, "review": record})


def command_merge(args: argparse.Namespace) -> None:
    if not args.confirm:
        raise KnowledgeMergeError("merge requires --confirm")
    paths = KnowledgePaths(args.root.resolve())
    proposal_path = args.proposal.resolve()
    parsed = parse_proposal(proposal_path)
    decision_path = paths.management / "review-decisions" / f"{parsed['source_id']}-review.json"
    if not decision_path.is_file():
        raise KnowledgeMergeError("missing human review decision")
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    if decision.get("proposal") != proposal_path.name:
        raise KnowledgeMergeError("review decision does not match proposal")
    if decision.get("proposal_fingerprint") != fingerprint_file(proposal_path):
        raise KnowledgeMergeError("proposal changed after review; review it again")
    registry = load_registry(paths.registry)
    source = find_source(registry, parsed["source_id"])
    claims = [entry["claim"] for entry in parsed["entries"]]
    store = load_curated_store(paths.curated_store)
    updated, outcomes = merge_approved_claims(
        store,
        claims,
        decision["decisions"],
        source,
        decision["reviewed_at"],
        set(args.confirm_replace or []),
    )
    merged_at = normalize_iso8601(args.at or utc_now_iso(), field_name="timestamp")
    report = {
        "schema_version": 1,
        "source_id": parsed["source_id"],
        "proposal": proposal_path.name,
        "merged_at": merged_at,
        "outcomes": outcomes,
    }
    atomic_write_json(paths.curated_store, updated)
    write_curated_runtime(paths, updated)
    atomic_write_json(
        paths.management / "merge-reports" / f"{parsed['source_id']}-{merged_at[:10]}.json",
        report,
    )
    emit({"ok": True, "report": report, "curated_claims": len(updated["claims"])})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)
    review = subparsers.add_parser("review")
    review.add_argument("--proposal", type=Path, required=True)
    review.add_argument("--reviewer", default="user")
    review.add_argument("--confirm", action="store_true")
    review.add_argument("--at", help=argparse.SUPPRESS)
    review.set_defaults(func=command_review)
    merge = subparsers.add_parser("merge")
    merge.add_argument("--proposal", type=Path, required=True)
    merge.add_argument("--confirm", action="store_true")
    merge.add_argument("--confirm-replace", action="append")
    merge.add_argument("--at", help=argparse.SUPPRESS)
    merge.set_defaults(func=command_merge)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (
        KnowledgeMergeError,
        KnowledgeIntakeError,
        KnowledgeSchemaError,
        OSError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        emit({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
