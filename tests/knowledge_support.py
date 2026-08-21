"""Shared fixtures for knowledge pipeline unit and CLI tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.support import ROOT, SCRIPTS

import knowledge_schema


INTAKE_SCRIPT = SCRIPTS / "knowledge_intake.py"
MERGE_SCRIPT = SCRIPTS / "knowledge_merge.py"
SUBPROCESS_TIMEOUT = 10


def sample_source(source_id: str = "src-example-book") -> dict[str, Any]:
    return {
        "source_id": source_id,
        "title": "Example",
        "author": "Author",
        "source_type": "book",
        "publication_year": 2024,
        "edition": "1",
        "language": "zh-CN",
        "topics": ["conversation"],
        "source_quality": "medium",
        "added_at": "2026-08-21T08:00:00+00:00",
        "last_reviewed_at": "2026-08-21T08:00:00+00:00",
        "freshness": "stable",
        "status": "approved",
        "content_fingerprint": "a" * 64,
    }


def sample_claim(
    source_id: str = "src-example-book",
    text: str = "先分享自己的真实内容通常比连续裸问题更自然",
    relation_type: str = "new",
    relation_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "claim_id": knowledge_schema.stable_claim_id(text),
        "canonical_claim": text,
        "source_id": source_id,
        "source_anchor": "chapter 2, p. 18",
        "claim_type": "practical_heuristic",
        "claim_evidence": "C",
        "confidence": "medium",
        "applicable_when": ["普通低风险聊天"],
        "not_applicable_when": ["对方明确要求停止联系"],
        "risk_of_misuse": ["把分享变成表演"],
        "relation_to_existing": {
            "type": relation_type,
            "claim_ids": relation_ids or [],
        },
        "proposed_destination": "conversation",
    }


def set_proposal_decision(path: Path, claim_id: str, decision: str) -> None:
    content = path.read_text(encoding="utf-8")
    start = content.index(f"## Claim: {claim_id}")
    next_start = content.find("## Claim: ", start + 1)
    if next_start < 0:
        next_start = len(content)
    section = content[start:next_start]
    choices = {
        "approve": "[x] approve [ ] reject [ ] revise",
        "reject": "[ ] approve [x] reject [ ] revise",
        "revise": "[ ] approve [ ] reject [x] revise",
    }
    section = section.replace(
        "[ ] approve [ ] reject [ ] revise", choices[decision]
    )
    path.write_text(content[:start] + section + content[next_start:], encoding="utf-8")


class KnowledgeCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project = Path(self.temp_dir.name)
        management = self.project / "knowledge-management"
        for relative in (
            "source-cards",
            "proposals",
            "review-decisions",
            "merge-reports",
        ):
            (management / relative).mkdir(parents=True, exist_ok=True)
        (self.project / "references" / "curated").mkdir(parents=True)
        self.write_json(
            management / "SOURCE_REGISTRY.json",
            {"schema_version": 1, "sources": []},
        )
        self.write_json(
            management / "CURATED_CLAIMS.json",
            {"schema_version": 1, "claims": []},
        )
        self.raw = self.project / "private-input.bin"
        self.raw.write_bytes("source-content".encode("utf-8"))

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def run_script(
        self, script: Path, *args: str
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(self.project), *args],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SUBPROCESS_TIMEOUT,
        )

    def run_ok(self, script: Path, *args: str) -> dict[str, Any]:
        result = self.run_script(script, *args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def register(self, source_id: str = "src-example-book") -> dict[str, Any]:
        return self.run_ok(
            INTAKE_SCRIPT,
            "register",
            str(self.raw),
            "--source-id",
            source_id,
            "--title",
            "Example",
            "--author",
            "Author",
            "--source-type",
            "book",
            "--publication-year",
            "2024",
            "--edition",
            "1",
            "--language",
            "zh-CN",
            "--topics",
            "conversation",
            "--source-quality",
            "medium",
            "--freshness",
            "stable",
            "--at",
            "2026-08-21T16:00:00+08:00",
        )

    def create_proposal(self, claims: list[dict[str, Any]]) -> Path:
        claims_path = self.project / "claims.json"
        self.write_json(claims_path, {"claims": claims})
        self.run_ok(
            INTAKE_SCRIPT,
            "proposal",
            "--source-id",
            claims[0]["source_id"],
            "--claims",
            str(claims_path),
        )
        return (
            self.project
            / "knowledge-management"
            / "proposals"
            / f"{claims[0]['source_id']}-proposal.md"
        )


# Modified by AI on 2026-08-21 16:58:58
