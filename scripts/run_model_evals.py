#!/usr/bin/env python3
"""Execute reproducible model behavioral evals without coupling them to validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = ROOT / "model_evals" / "cases.yaml"
RUBRIC_PATH = ROOT / "model_evals" / "rubric.yaml"
RESULTS_BASE = ROOT / "model_evals" / "results"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
VERSION_PATTERN = re.compile(r"v?(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)")
API_RUNTIME_PROFILE = "api_canonical"
CHATGPT_RUNTIME_PROFILE = "chatgpt_project"
RUNTIME_PROFILES = {API_RUNTIME_PROFILE, CHATGPT_RUNTIME_PROFILE}

BASE_RUNTIME_SOURCES = (
    "SKILL.md",
    "shared/CORE_POLICY.md",
    "shared/FACT_HYPOTHESIS_POLICY.md",
)
CASE_RUNTIME_REFERENCES: dict[str, tuple[str, ...]] = {
    "model-realtime-one-best": (
        "references/personal/自然回复生成器.md",
        "references/personal/关系阶段与聊天节奏.md",
    ),
    "model-explicit-multiple-versions": (
        "references/personal/自然回复生成器.md",
    ),
    "model-tone-and-chunking": (
        "references/personal/自然回复生成器.md",
        "references/personal/网络聊天表达升级器.md",
    ),
    "model-serious-disclosure": (
        "references/personal/自然回复生成器.md",
        "references/personal/关系阶段与聊天节奏.md",
    ),
    "model-unknown-user-fact": (
        "references/personal/自然回复生成器.md",
    ),
    "model-return-continuation-ownership": (
        "references/personal/自然回复生成器.md",
        "references/personal/关系阶段与聊天节奏.md",
    ),
    "model-partner-opens-thread": (
        "references/personal/自然回复生成器.md",
        "references/personal/幽默与调侃生成器.md",
    ),
    "model-review-split": (
        "references/personal/网络聊天表达升级器.md",
        "references/personal/复盘模式与实际发送学习闭环.md",
    ),
    "model-actual-send-learning": (
        "references/personal/网络聊天表达升级器.md",
        "references/personal/复盘模式与实际发送学习闭环.md",
    ),
    "model-user-draft-first": (
        "references/personal/自然回复生成器.md",
        "references/personal/网络聊天表达升级器.md",
    ),
    "model-explicit-boundary-stop": (
        "references/personal/关系阶段与聊天节奏.md",
        "references/knowledge/08-同意边界性与亲密.md",
    ),
    "model-boundary-outcome-growth": (
        "references/personal/复盘模式与实际发送学习闭环.md",
        "references/knowledge/08-同意边界性与亲密.md",
    ),
    "model-single-event-uncertainty": (
        "references/personal/关系阶段与聊天节奏.md",
        "references/personal/缺失上下文与高信息量追问.md",
    ),
    "model-cross-object-isolation": (
        "references/personal/关系阶段与聊天节奏.md",
        "references/personal/网络聊天表达升级器.md",
    ),
    "model-stage-trend-separation": (
        "references/personal/关系阶段与聊天节奏.md",
        "references/personal/投入预算与停止条件.md",
    ),
    "model-conflicting-evidence": (
        "references/personal/关系阶段与聊天节奏.md",
        "references/personal/投入预算与停止条件.md",
    ),
    "model-boundary-and-warm-interaction": (
        "references/personal/关系阶段与聊天节奏.md",
        "references/knowledge/08-同意边界性与亲密.md",
    ),
    "model-minimum-information-gain": (
        "references/personal/缺失上下文与高信息量追问.md",
        "references/personal/关系阶段与聊天节奏.md",
    ),
    "model-stop-after-answer": (
        "references/personal/缺失上下文与高信息量追问.md",
        "references/personal/关系阶段与聊天节奏.md",
    ),
}

TARGET_INSTRUCTIONS = """以下内容是 Relationship Compass 的正式运行规则。
请遵循这些规则，直接回答最后的用户输入，不要解释或复述内部规则。"""

JUDGE_INSTRUCTIONS = """你是 relationship-compass 行为评测的独立 judge。
只依据给出的用户输入、目标模型回答和逐项判据进行判断。
每个 criterion 必须给出 passed 布尔值与简短、可复核的中文 reason。
不要推测目标模型的隐藏上下文，不要给总分，也不要决定 baseline。"""


class ModelEvalError(RuntimeError):
    """Raised when eval inputs or artifacts are invalid."""


class ProviderError(RuntimeError):
    """Raised for a provider failure after bounded retries."""


class ProviderTimeout(ProviderError):
    """Raised when a provider request times out after bounded retries."""


class ProviderInvalidResponse(ProviderError):
    """Raised when the provider returns an unusable response envelope."""


@dataclass(frozen=True)
class ProviderResult:
    text: str
    response_id: str | None = None
    usage: dict[str, Any] | None = None


class ModelProvider(Protocol):
    provider_name: str
    model: str
    public_parameters: dict[str, Any]

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> ProviderResult:
        """Generate one isolated response."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_text(path: Path) -> str:
    """Read text with platform-independent newlines for stable snapshots."""
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def normalize_pack_version(value: Any) -> str:
    if not isinstance(value, str):
        raise ModelEvalError("Unable to determine Relationship Compass version.")
    match = VERSION_PATTERN.fullmatch(value.strip())
    if not match:
        raise ModelEvalError("Unable to determine Relationship Compass version.")
    return match.group(1)


def pack_version(root: Path = ROOT) -> str:
    path = root / "chatgpt-project" / "generated-knowledge" / "KNOWLEDGE_PACK_INFO.json"
    try:
        payload = load_json_object(path)
    except (OSError, ModelEvalError) as exc:
        raise ModelEvalError("Unable to determine Relationship Compass version.") from exc
    return normalize_pack_version(payload.get("pack_version"))


def version_directory(version: str) -> str:
    return "v" + normalize_pack_version(version)


def results_root(
    version: str,
    base: Path = RESULTS_BASE,
    runtime_profile: str | None = None,
) -> Path:
    root = base / version_directory(version)
    if runtime_profile is None:
        return root
    if runtime_profile not in RUNTIME_PROFILES:
        raise ModelEvalError(f"invalid runtime profile: {runtime_profile!r}")
    return root / runtime_profile


def load_json_yaml(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelEvalError(f"{path.name}: invalid JSON-compatible YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelEvalError(f"{path.name}: root must be an object")
    return data


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ModelEvalError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelEvalError(f"{path}: root must be an object")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ModelEvalError(f"missing artifact: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ModelEvalError(f"{path}:{line_number}: invalid JSONL") from exc
        if not isinstance(record, dict):
            raise ModelEvalError(f"{path}:{line_number}: record must be an object")
        records.append(record)
    return records


def write_json(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    if exclusive and path.exists():
        raise ModelEvalError(f"refusing to overwrite existing artifact: {path}")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def append_jsonl(handle: Any, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())


def load_definitions() -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    case_data = load_json_yaml(CASES_PATH)
    rubric_data = load_json_yaml(RUBRIC_PATH)
    cases = case_data.get("cases")
    criteria = rubric_data.get("criteria")
    if not isinstance(cases, list) or not cases:
        raise ModelEvalError("cases.yaml must contain a non-empty cases list")
    if not isinstance(criteria, dict) or not criteria:
        raise ModelEvalError("rubric.yaml must contain a non-empty criteria object")
    seen: set[str] = set()
    used_criteria: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ModelEvalError("each model eval case must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ModelEvalError(f"invalid or duplicate case id: {case_id!r}")
        seen.add(case_id)
        if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            raise ModelEvalError(f"{case_id}: prompt is required")
        required = case.get("required_criteria")
        if not isinstance(required, list) or not required:
            raise ModelEvalError(f"{case_id}: required_criteria must be a non-empty list")
        if len(required) != len(set(required)):
            raise ModelEvalError(f"{case_id}: duplicate required criterion")
        unknown = set(required) - set(criteria)
        if unknown:
            raise ModelEvalError(f"{case_id}: unknown criteria: {', '.join(sorted(unknown))}")
        used_criteria.update(required)
    if set(criteria) != used_criteria:
        unused = set(criteria) - used_criteria
        raise ModelEvalError(f"rubric contains unused criteria: {', '.join(sorted(unused))}")
    validate_runtime_routes(cases)
    return cases, criteria


def validate_runtime_routes(cases: list[dict[str, Any]], root: Path = ROOT) -> None:
    case_ids = {case["id"] for case in cases}
    if set(CASE_RUNTIME_REFERENCES) != case_ids:
        missing = case_ids - set(CASE_RUNTIME_REFERENCES)
        extra = set(CASE_RUNTIME_REFERENCES) - case_ids
        raise ModelEvalError(
            f"runtime route mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
        )
    for case_id, references in CASE_RUNTIME_REFERENCES.items():
        if not 1 <= len(references) <= 3:
            raise ModelEvalError(f"{case_id}: runtime route must use 1-3 references")
        for relative in (*BASE_RUNTIME_SOURCES, *references):
            if not (root / relative).is_file():
                raise ModelEvalError(f"{case_id}: missing runtime source {relative}")


def assemble_runtime(case: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    sources = [*BASE_RUNTIME_SOURCES, *CASE_RUNTIME_REFERENCES[case["id"]]]
    sections = []
    for relative in sources:
        content = canonical_text(root / relative)
        sections.append(f"## Runtime source: {relative}\n\n{content.rstrip()}")
    return {
        "sources": sources,
        "content": "\n\n".join(sections) + "\n",
    }


def prepare_cases(
    cases: list[dict[str, Any]],
    criteria: dict[str, dict[str, str]],
    root: Path = ROOT,
    product_version: str | None = None,
) -> list[dict[str, Any]]:
    current_version = normalize_pack_version(product_version or pack_version(root))
    prepared: list[dict[str, Any]] = []
    for case in cases:
        prepared.append(
            {
                "schema_version": 1,
                "pack_version": current_version,
                "case_id": case["id"],
                "title": case["title"],
                "mode": case["mode"],
                "input": case["prompt"],
                "criteria": [
                    {"criterion": criterion, "question": criteria[criterion]["question"]}
                    for criterion in case["required_criteria"]
                ],
                "runtime": assemble_runtime(case, root),
            }
        )
    return prepared


def write_prepared(path: Path, records: list[dict[str, Any]]) -> None:
    if path.exists():
        raise ModelEvalError(f"refusing to overwrite existing prepared bundle: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for record in records:
            append_jsonl(handle, record)


def write_jsonl(
    path: Path, records: list[dict[str, Any]], *, exclusive: bool = False
) -> None:
    if exclusive and path.exists():
        raise ModelEvalError(f"refusing to overwrite existing artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def validate_prepared_records(
    records: list[dict[str, Any]],
    *,
    require_all: bool = True,
    expected_version: str | None = None,
) -> None:
    cases, _ = load_definitions()
    current_version = normalize_pack_version(expected_version or pack_version())
    expected = {case["id"]: case for case in cases}
    seen: set[str] = set()
    for record in records:
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or case_id not in expected or case_id in seen:
            raise ModelEvalError(f"invalid or duplicate prepared case: {case_id!r}")
        seen.add(case_id)
        if record.get("pack_version") != current_version:
            raise ModelEvalError(f"{case_id}: prepared pack version differs from current pack")
        if record.get("input") != expected[case_id]["prompt"]:
            raise ModelEvalError(f"{case_id}: prepared input differs from definition")
        criterion_ids = [item.get("criterion") for item in record.get("criteria", [])]
        if criterion_ids != expected[case_id]["required_criteria"]:
            raise ModelEvalError(f"{case_id}: prepared criteria differ from definition")
        runtime = record.get("runtime")
        if not isinstance(runtime, dict) or not isinstance(runtime.get("content"), str):
            raise ModelEvalError(f"{case_id}: prepared runtime is missing")
        expected_runtime = assemble_runtime(expected[case_id])
        if runtime.get("sources") != expected_runtime["sources"]:
            raise ModelEvalError(f"{case_id}: prepared runtime sources differ from route")
        if runtime["content"] != expected_runtime["content"]:
            raise ModelEvalError(f"{case_id}: prepared runtime is stale")
    if require_all and seen != set(expected):
        raise ModelEvalError("prepared bundle must contain every model eval case exactly once")


def eval_definition_snapshot() -> dict[str, Any]:
    cases, criteria = load_definitions()
    return {"schema_version": 1, "cases": cases, "criteria": criteria}


def eval_definition_hash(snapshot: dict[str, Any] | None = None) -> str:
    return sha256_bytes(canonical_json_bytes(snapshot or eval_definition_snapshot()))


def source_snapshots(root: Path = ROOT) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "runner": {
            "path": "scripts/run_model_evals.py",
            "content": canonical_text(root / "scripts" / "run_model_evals.py"),
        },
        "skill": {
            "path": "SKILL.md",
            "content": canonical_text(root / "SKILL.md"),
        },
    }


def source_content_hash(snapshots: dict[str, Any], name: str) -> str:
    source = snapshots.get(name)
    if not isinstance(source, dict) or not isinstance(source.get("content"), str):
        raise ModelEvalError(f"source-snapshots.json is missing {name} content")
    return sha256_bytes(source["content"].encode("utf-8"))


def runtime_snapshot(
    runtime_profile: str,
    prepared_records: list[dict[str, Any]],
    root: Path = ROOT,
) -> dict[str, Any]:
    if runtime_profile == API_RUNTIME_PROFILE:
        return {
            "schema_version": 1,
            "runtime_profile": runtime_profile,
            "strategy": "target_instructions_plus_prepared_per_case_runtime",
            "target_instructions": TARGET_INSTRUCTIONS,
            "prepared_runtime_source": "prepared.jsonl",
        }
    if runtime_profile == CHATGPT_RUNTIME_PROFILE:
        generated_root = root / "chatgpt-project" / "generated-knowledge"
        knowledge = [
            {"path": path.relative_to(root).as_posix(), "content": canonical_text(path)}
            for path in sorted(generated_root.glob("*.md"))
        ]
        if not knowledge:
            raise ModelEvalError("ChatGPT Project runtime has no generated Knowledge files")
        pack_info = load_json_object(generated_root / "KNOWLEDGE_PACK_INFO.json")
        if normalize_pack_version(pack_info.get("pack_version")) != prepared_version(
            prepared_records
        ):
            raise ModelEvalError("ChatGPT Project pack version differs from prepared bundle")
        return {
            "schema_version": 1,
            "runtime_profile": runtime_profile,
            "strategy": "project_instructions_plus_generated_knowledge",
            "project_instructions": {
                "path": "chatgpt-project/PROJECT_INSTRUCTIONS.md",
                "content": canonical_text(root / "chatgpt-project" / "PROJECT_INSTRUCTIONS.md"),
            },
            "knowledge": knowledge,
            "pack_info": pack_info,
        }
    raise ModelEvalError(f"invalid runtime profile: {runtime_profile!r}")


def bundle_hash(
    prepared_records: list[dict[str, Any]], runtime: dict[str, Any]
) -> str:
    return sha256_bytes(
        canonical_json_bytes({"prepared": prepared_records, "runtime": runtime})
    )


def build_run_snapshots(
    prepared_records: list[dict[str, Any]], runtime_profile: str
) -> dict[str, Any]:
    return {
        "prepared": prepared_records,
        "eval_definition": eval_definition_snapshot(),
        "runtime": runtime_snapshot(runtime_profile, prepared_records),
        "sources": source_snapshots(),
    }


def write_run_snapshots(
    directory: Path, snapshots: dict[str, Any], *, exclusive: bool = True
) -> None:
    write_jsonl(
        directory / "prepared.jsonl", snapshots["prepared"], exclusive=exclusive
    )
    write_json(
        directory / "eval-definition.json",
        snapshots["eval_definition"],
        exclusive=exclusive,
    )
    write_json(
        directory / "runtime-snapshot.json", snapshots["runtime"], exclusive=exclusive
    )
    write_json(
        directory / "source-snapshots.json", snapshots["sources"], exclusive=exclusive
    )


def load_run_snapshots(directory: Path) -> dict[str, Any]:
    return {
        "prepared": load_jsonl(directory / "prepared.jsonl"),
        "eval_definition": load_json_object(directory / "eval-definition.json"),
        "runtime": load_json_object(directory / "runtime-snapshot.json"),
        "sources": load_json_object(directory / "source-snapshots.json"),
    }


def run_git(*args: str, root: Path = ROOT) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-c", f"safe.directory={root.as_posix()}", *args],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def git_fingerprint(root: Path = ROOT) -> dict[str, Any]:
    revision = run_git("rev-parse", "HEAD", root=root)
    status = run_git("status", "--porcelain", root=root)
    value = revision.stdout.strip() if revision else ""
    sha = (
        value
        if revision and revision.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value)
        else "unavailable"
    )
    dirty = bool(status.stdout.strip()) if status and status.returncode == 0 else None
    return {"git_sha": sha, "git_dirty": dirty}


def runner_revision() -> str:
    return sha256_bytes(canonical_text(Path(__file__).resolve()).encode("utf-8"))


def provider_metadata(provider: ModelProvider) -> dict[str, Any]:
    return {
        "provider": provider.provider_name,
        "model": provider.model,
        "parameters": dict(provider.public_parameters),
    }


class OpenAIResponsesProvider:
    """Minimal standard-library client for one real provider."""

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        timeout_seconds: float = 90.0,
        max_retries: int = 1,
        max_output_tokens: int = 1200,
    ) -> None:
        if not api_key:
            raise ModelEvalError("OPENAI_API_KEY is not set; behavioral evaluation NOT RUN")
        if not model:
            raise ModelEvalError("model is required; behavioral evaluation NOT RUN")
        if max_retries not in {0, 1, 2}:
            raise ModelEvalError("max_retries must be between 0 and 2")
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/responses"
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_output_tokens = max_output_tokens
        self.public_parameters = {
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
            "max_output_tokens": max_output_tokens,
            "store": False,
            "single_sample": True,
        }

    def generate(
        self,
        *,
        instructions: str,
        input_text: str,
        response_schema: dict[str, Any] | None = None,
    ) -> ProviderResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": input_text,
            "max_output_tokens": self.max_output_tokens,
            "store": False,
        }
        if response_schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "relationship_compass_judgment",
                    "strict": True,
                    "schema": response_schema,
                }
            }
        request = urllib.request.Request(
            self._url,
            data=canonical_json_bytes(payload),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    body = response.read()
                return self._parse_response(body)
            except urllib.error.HTTPError as exc:
                retryable = exc.code in {408, 409, 429} or exc.code >= 500
                if attempt < self.max_retries and retryable:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderError(f"OpenAI HTTP {exc.code}") from exc
            except (TimeoutError, socket.timeout) as exc:
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderTimeout("OpenAI request timed out") from exc
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                    if attempt < self.max_retries:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    raise ProviderTimeout("OpenAI request timed out") from exc
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ProviderError("OpenAI transport error") from exc
        raise ProviderError("OpenAI request failed")

    @staticmethod
    def _parse_response(body: bytes) -> ProviderResult:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderInvalidResponse("OpenAI returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("status") != "completed":
            raise ProviderInvalidResponse("OpenAI response did not complete")
        text = payload.get("output_text")
        if not isinstance(text, str):
            chunks: list[str] = []
            for item in payload.get("output", []):
                if not isinstance(item, dict):
                    continue
                for content in item.get("content", []):
                    if (
                        isinstance(content, dict)
                        and content.get("type") == "output_text"
                        and isinstance(content.get("text"), str)
                    ):
                        chunks.append(content["text"])
            text = "".join(chunks)
        if not isinstance(text, str):
            raise ProviderInvalidResponse("OpenAI response has no text output")
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
        response_id = payload.get("id") if isinstance(payload.get("id"), str) else None
        return ProviderResult(text=text, response_id=response_id, usage=usage)


def target_input(record: dict[str, Any]) -> str:
    return record["runtime"]["content"] + "\n## 用户输入\n\n" + record["input"]


def run_id_now() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{os.getpid()}"


def prepared_version(records: list[dict[str, Any]]) -> str:
    versions = {record.get("pack_version") for record in records}
    if len(versions) != 1:
        raise ModelEvalError("prepared bundle must use exactly one pack version")
    return normalize_pack_version(next(iter(versions)))


def case_snapshots(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": record["case_id"],
            "title": record["title"],
            "mode": record["mode"],
            "input": record["input"],
            "criteria": record["criteria"],
            "runtime_sources": record["runtime"]["sources"],
        }
        for record in records
    ]


def index_case_records(
    records: list[dict[str, Any]], expected_ids: set[str], label: str
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        case_id = record.get("case_id")
        if not isinstance(case_id, str) or case_id not in expected_ids:
            raise ModelEvalError(f"{label} contains unknown case_id: {case_id!r}")
        if case_id in indexed:
            raise ModelEvalError(f"{label} contains duplicate case_id: {case_id}")
        indexed[case_id] = record
    return indexed


def run_counts(
    cases: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
) -> dict[str, int]:
    total = len(cases)
    response_statuses = [record.get("status") for record in responses]
    judgment_statuses = [record.get("status") for record in judgments]
    return {
        "total_cases": total,
        "response_records": len(responses),
        "model_response": response_statuses.count("MODEL_RESPONSE"),
        "provider_error": response_statuses.count("PROVIDER_ERROR"),
        "timeout": response_statuses.count("TIMEOUT"),
        "invalid_response": response_statuses.count("INVALID_RESPONSE"),
        "not_run": total - len(responses),
        "judged": judgment_statuses.count("JUDGMENT"),
        "judge_error": judgment_statuses.count("JUDGE_ERROR"),
        "not_judged": judgment_statuses.count("NOT_JUDGED") + total - len(judgments),
    }


def derive_run_status(
    cases: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
) -> str:
    total = len(cases)
    if any(record.get("status") != "MODEL_RESPONSE" for record in responses):
        return "FAILED"
    if any(record.get("status") == "JUDGE_ERROR" for record in judgments):
        return "FAILED"
    if not responses:
        return "PREPARED"
    if len(responses) < total:
        return "TARGET_PARTIAL"
    judged = sum(record.get("status") == "JUDGMENT" for record in judgments)
    if judged == 0:
        return "TARGET_COMPLETE"
    if judged < total or any(record.get("status") != "JUDGMENT" for record in judgments):
        return "JUDGE_PARTIAL"
    return "COMPLETED"


def new_run_metadata(
    prepared_records: list[dict[str, Any]],
    snapshots: dict[str, Any],
    *,
    run_id: str,
    target: dict[str, Any],
    runtime_profile: str,
    repository_sha: str | None = None,
    repository_dirty: bool | None = None,
) -> dict[str, Any]:
    version = prepared_version(prepared_records)
    fingerprint = git_fingerprint()
    recorded_bundle_hash = bundle_hash(prepared_records, snapshots["runtime"])
    cases = case_snapshots(prepared_records)
    metadata: dict[str, Any] = {
        "schema_version": 2,
        "evaluation_type": "model_behavioral",
        "product_version": version,
        "pack_version": version,
        "version_directory": version_directory(version),
        "run_id": run_id,
        "status": "PREPARED",
        "baseline": False,
        "runtime_profile": runtime_profile,
        "git_sha": repository_sha or fingerprint["git_sha"],
        "git_dirty": (
            repository_dirty if repository_dirty is not None else fingerprint["git_dirty"]
        ),
        "runner_revision": source_content_hash(snapshots["sources"], "runner"),
        "eval_definition_hash": eval_definition_hash(snapshots["eval_definition"]),
        "bundle_hash": recorded_bundle_hash,
        "skill_revision": source_content_hash(snapshots["sources"], "skill"),
        "target": target,
        "judge": None,
        "created_at": utc_now(),
        "target_started_at": None,
        "target_completed_at": None,
        "judge_started_at": None,
        "judge_completed_at": None,
        "completed_at": None,
        "counts": run_counts(cases, [], []),
        "cases": cases,
    }
    return metadata


def refresh_run_metadata(
    metadata: dict[str, Any],
    responses: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
) -> None:
    cases = metadata["cases"]
    metadata["counts"] = run_counts(cases, responses, judgments)
    metadata["status"] = derive_run_status(cases, responses, judgments)


def artifact_binding(metadata: dict[str, Any]) -> dict[str, str]:
    return {
        "run_id": metadata["run_id"],
        "bundle_hash": metadata["bundle_hash"],
        "runtime_profile": metadata["runtime_profile"],
    }


def execute_run(
    prepared_records: list[dict[str, Any]],
    provider: ModelProvider,
    run_dir: Path,
    *,
    repository_sha: str | None = None,
    repository_dirty: bool | None = None,
    knowledge_pack_version: str | None = None,
) -> dict[str, Any]:
    validate_prepared_records(prepared_records, require_all=False)
    if knowledge_pack_version is not None:
        expected = normalize_pack_version(knowledge_pack_version)
        if prepared_version(prepared_records) != expected:
            raise ModelEvalError("prepared bundle version does not match requested run version")
    if run_dir.exists():
        raise ModelEvalError(f"refusing to overwrite existing run directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)
    snapshots = build_run_snapshots(prepared_records, API_RUNTIME_PROFILE)
    metadata = new_run_metadata(
        prepared_records,
        snapshots,
        run_id=run_dir.name,
        target=provider_metadata(provider),
        runtime_profile=API_RUNTIME_PROFILE,
        repository_sha=repository_sha,
        repository_dirty=repository_dirty,
    )
    metadata["target_started_at"] = utc_now()
    write_run_snapshots(run_dir, snapshots)
    write_json(run_dir / "run.json", metadata, exclusive=True)
    responses_path = run_dir / "responses.jsonl"
    with responses_path.open("x", encoding="utf-8", newline="\n") as handle:
        for record in prepared_records:
            response_record: dict[str, Any] = {
                "schema_version": 1,
                **artifact_binding(metadata),
                "case_id": record["case_id"],
                "started_at": utc_now(),
                "completed_at": None,
                "status": None,
                "response": None,
                "provider": provider.provider_name,
                "model": provider.model,
                "provider_response_id": None,
                "usage": None,
                "error": None,
            }
            try:
                result = provider.generate(
                    instructions=TARGET_INSTRUCTIONS,
                    input_text=target_input(record),
                )
                if not result.text.strip():
                    raise ProviderInvalidResponse("target returned empty text")
                response_record.update(
                    {
                        "status": "MODEL_RESPONSE",
                        "response": result.text,
                        "provider_response_id": result.response_id,
                        "usage": result.usage,
                    }
                )
            except ProviderTimeout as exc:
                response_record.update({"status": "TIMEOUT", "error": str(exc)})
            except ProviderInvalidResponse as exc:
                response_record.update({"status": "INVALID_RESPONSE", "error": str(exc)})
            except ProviderError as exc:
                response_record.update({"status": "PROVIDER_ERROR", "error": str(exc)})
            response_record["completed_at"] = utc_now()
            append_jsonl(handle, response_record)
            saved_responses = load_jsonl(responses_path)
            refresh_run_metadata(metadata, saved_responses, [])
            write_json(run_dir / "run.json", metadata)
            if response_record["status"] != "MODEL_RESPONSE":
                break
    saved_responses = load_jsonl(responses_path)
    refresh_run_metadata(metadata, saved_responses, [])
    finished_at = utc_now()
    if metadata["status"] == "TARGET_COMPLETE":
        metadata["target_completed_at"] = finished_at
    elif metadata["status"] == "FAILED":
        metadata["completed_at"] = finished_at
    write_json(run_dir / "run.json", metadata)
    return metadata


def judgment_schema(criteria: list[dict[str, str]]) -> dict[str, Any]:
    criterion_ids = [item["criterion"] for item in criteria]
    return {
        "type": "object",
        "properties": {
            "criteria": {
                "type": "array",
                "minItems": len(criterion_ids),
                "maxItems": len(criterion_ids),
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string", "enum": criterion_ids},
                        "passed": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["criterion", "passed", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["criteria"],
        "additionalProperties": False,
    }


def judge_input(case: dict[str, Any], response: str) -> str:
    rubric_lines = "\n".join(
        f"- {item['criterion']}: {item['question']}" for item in case["criteria"]
    )
    return (
        f"## Case ID\n{case['case_id']}\n\n"
        f"## 用户输入\n{case['input']}\n\n"
        f"## 目标模型回答\n{response}\n\n"
        f"## 判据\n{rubric_lines}"
    )


def parse_judgment(text: str, criteria: list[dict[str, str]]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModelEvalError("judge returned malformed JSON") from exc
    items = payload.get("criteria") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ModelEvalError("judge response is missing criteria array")
    expected = [item["criterion"] for item in criteria]
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ModelEvalError("judge criterion must be an object")
        criterion = item.get("criterion")
        passed = item.get("passed")
        reason = item.get("reason")
        if isinstance(criterion, str) and criterion in indexed:
            raise ModelEvalError("judge response contains duplicate criteria")
        if (
            not isinstance(criterion, str)
            or not isinstance(passed, bool)
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise ModelEvalError("judge criterion has invalid id, passed, or reason")
        indexed[criterion] = {
            "criterion": criterion,
            "passed": passed,
            "reason": reason.strip(),
        }
    if set(indexed) != set(expected):
        raise ModelEvalError("judge response does not cover every required criterion exactly once")
    return [indexed[criterion] for criterion in expected]


def execute_judge(run_dir: Path, provider: ModelProvider) -> dict[str, int]:
    validate_result_artifacts(run_dir)
    metadata = load_json_object(run_dir / "run.json")
    if metadata.get("status") != "TARGET_COMPLETE":
        raise ModelEvalError("judge requires a complete target phase")
    judgments_path = run_dir / "judgments.jsonl"
    if judgments_path.exists():
        raise ModelEvalError(f"refusing to overwrite existing artifact: {judgments_path}")
    cases = metadata.get("cases")
    if not isinstance(cases, list):
        raise ModelEvalError("run.json is missing case snapshots")
    case_ids = {case["case_id"] for case in cases}
    responses = index_case_records(
        load_jsonl(run_dir / "responses.jsonl"), case_ids, "responses.jsonl"
    )
    counts = {"judged": 0, "judge_error": 0, "not_judged": 0}
    stop_provider_calls = False
    metadata["judge"] = provider_metadata(provider)
    metadata["judge_started_at"] = utc_now()
    with judgments_path.open("x", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            case_id = case["case_id"]
            target = responses.get(case_id)
            if not target or target.get("status") != "MODEL_RESPONSE":
                record = {
                    "schema_version": 1,
                    **artifact_binding(metadata),
                    "case_id": case_id,
                    "status": "NOT_JUDGED",
                    "error": "target response is unavailable",
                    "criteria": None,
                }
                append_jsonl(handle, record)
                counts["not_judged"] += 1
                continue
            if stop_provider_calls:
                record = {
                    "schema_version": 1,
                    **artifact_binding(metadata),
                    "case_id": case_id,
                    "status": "NOT_JUDGED",
                    "error": "judge stopped after provider failure",
                    "criteria": None,
                }
                append_jsonl(handle, record)
                counts["not_judged"] += 1
                continue
            record = {
                "schema_version": 1,
                **artifact_binding(metadata),
                "case_id": case_id,
                "status": None,
                "criteria": None,
                "judge": {"provider": provider.provider_name, "model": provider.model},
                "provider_response_id": None,
                "usage": None,
                "evaluated_at": utc_now(),
                "error": None,
            }
            try:
                result = provider.generate(
                    instructions=JUDGE_INSTRUCTIONS,
                    input_text=judge_input(case, target["response"]),
                    response_schema=judgment_schema(case["criteria"]),
                )
                record.update(
                    {
                        "status": "JUDGMENT",
                        "criteria": parse_judgment(result.text, case["criteria"]),
                        "provider_response_id": result.response_id,
                        "usage": result.usage,
                    }
                )
                counts["judged"] += 1
            except ProviderTimeout as exc:
                record.update({"status": "JUDGE_ERROR", "error": f"TIMEOUT: {exc}"})
                counts["judge_error"] += 1
                stop_provider_calls = True
            except ProviderError as exc:
                record.update({"status": "JUDGE_ERROR", "error": f"PROVIDER_ERROR: {exc}"})
                counts["judge_error"] += 1
                stop_provider_calls = True
            except ModelEvalError as exc:
                record.update({"status": "JUDGE_ERROR", "error": str(exc)})
                counts["judge_error"] += 1
            append_jsonl(handle, record)
    metadata["judge_counts"] = counts
    refresh_run_metadata(
        metadata,
        list(responses.values()),
        load_jsonl(judgments_path),
    )
    finished_at = utc_now()
    if metadata["status"] == "COMPLETED":
        metadata["judge_completed_at"] = finished_at
        metadata["completed_at"] = finished_at
    elif metadata["status"] == "FAILED":
        metadata["completed_at"] = finished_at
    write_json(run_dir / "run.json", metadata)
    return counts


def validate_case_judgment(
    case: dict[str, Any], judgment: dict[str, Any]
) -> list[dict[str, Any]]:
    if judgment.get("status") != "JUDGMENT":
        raise ModelEvalError(str(judgment.get("error") or judgment.get("status")))
    items = judgment.get("criteria")
    if not isinstance(items, list):
        raise ModelEvalError("judgment is missing criteria")
    expected = [item["criterion"] for item in case["criteria"]]
    indexed = {
        item.get("criterion"): item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("criterion"), str)
    }
    if set(indexed) != set(expected) or len(items) != len(expected):
        raise ModelEvalError("judgment criteria do not match run snapshot")
    for criterion in expected:
        item = indexed[criterion]
        if (
            not isinstance(item.get("passed"), bool)
            or not isinstance(item.get("reason"), str)
            or not item["reason"].strip()
        ):
            raise ModelEvalError("judgment criterion has invalid passed or reason")
    return [indexed[criterion] for criterion in expected]


def aggregate_results(
    metadata: dict[str, Any],
    response_records: list[dict[str, Any]],
    judgment_records: list[dict[str, Any]],
) -> dict[str, Any]:
    cases = metadata.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ModelEvalError("run.json is missing case snapshots")
    case_ids = {case["case_id"] for case in cases}
    responses = index_case_records(response_records, case_ids, "responses.jsonl")
    judgments = index_case_records(judgment_records, case_ids, "judgments.jsonl")
    passed_cases: list[str] = []
    failed_cases: list[dict[str, Any]] = []
    errored_cases: list[dict[str, Any]] = []
    not_evaluable_cases: list[dict[str, Any]] = []
    passed_criteria = 0
    failed_criteria = 0
    total_criteria = sum(len(case["criteria"]) for case in cases)
    for case in cases:
        case_id = case["case_id"]
        target = responses.get(case_id)
        if not target:
            not_evaluable_cases.append(
                {
                    "case_id": case_id,
                    "stage": "target",
                    "status": "PENDING_RESPONSE",
                    "reason": "target response has not been imported or executed",
                }
            )
            continue
        if target.get("status") != "MODEL_RESPONSE":
            errored_cases.append(
                {
                    "case_id": case_id,
                    "stage": "target",
                    "status": target.get("status"),
                    "reason": target.get("error") or "target execution failed",
                }
            )
            continue
        judgment = judgments.get(case_id)
        if not judgment or judgment.get("status") == "NOT_JUDGED":
            not_evaluable_cases.append(
                {
                    "case_id": case_id,
                    "stage": "judge",
                    "status": judgment.get("status") if judgment else "MISSING_JUDGMENT",
                    "reason": (
                        judgment.get("error")
                        if judgment
                        else "judgment has not been imported or executed"
                    ),
                }
            )
            continue
        try:
            items = validate_case_judgment(case, judgment)
        except ModelEvalError as exc:
            errored_cases.append(
                {
                    "case_id": case_id,
                    "stage": "judge",
                    "status": judgment.get("status"),
                    "reason": str(exc),
                }
            )
            continue
        failures = [
            {"criterion": item["criterion"], "reason": item["reason"]}
            for item in items
            if not item["passed"]
        ]
        passed_criteria += len(items) - len(failures)
        failed_criteria += len(failures)
        if failures:
            failed_cases.append(
                {
                    "case_id": case_id,
                    "input": case["input"],
                    "response": target["response"],
                    "failed_criteria": failures,
                }
            )
        else:
            passed_cases.append(case_id)
    judged_criteria = passed_criteria + failed_criteria
    actual_status = derive_run_status(cases, response_records, judgment_records)
    behavioral_status = (
        "NOT_EVALUABLE"
        if errored_cases or not_evaluable_cases
        else ("FAIL" if failed_cases else "PASS")
    )
    return {
        "schema_version": 2,
        "evaluation_type": "model_behavioral",
        "run_id": metadata.get("run_id"),
        "product_version": metadata.get("product_version"),
        "runtime_profile": metadata.get("runtime_profile"),
        "git_sha": metadata.get("git_sha"),
        "git_dirty": metadata.get("git_dirty"),
        "bundle_hash": metadata.get("bundle_hash"),
        "target": metadata.get("target"),
        "judge": metadata.get("judge"),
        "completion_status": actual_status,
        "behavioral_status": behavioral_status,
        "baseline": False,
        "counts": {
            "total_cases": len(cases),
            "response_records": len(response_records),
            "executed_cases": len(response_records),
            "pending_cases": len(cases) - len(response_records),
            "passed_cases": len(passed_cases),
            "failed_cases": len(failed_cases),
            "errored_cases": len(errored_cases),
            "not_evaluable_cases": len(not_evaluable_cases),
            "total_criteria": total_criteria,
            "judged_criteria": judged_criteria,
            "passed_criteria": passed_criteria,
            "failed_criteria": failed_criteria,
            "unjudged_criteria": total_criteria - judged_criteria,
        },
        "passed_case_ids": passed_cases,
        "failed_cases": failed_cases,
        "errored_cases": errored_cases,
        "not_evaluable_cases": not_evaluable_cases,
    }


def render_summary_markdown(summary: dict[str, Any]) -> str:
    counts = summary["counts"]
    markdown = [
        f"# Model Behavioral Eval — {summary.get('run_id')}",
        "",
        f"- Completion: `{summary['completion_status']}`",
        f"- Behavioral status: `{summary['behavioral_status']}`",
        f"- Runtime profile: `{summary['runtime_profile']}`",
        (
            "- Worktree: `DIRTY WORKTREE`（不得默认作为正式 baseline）"
            if summary["git_dirty"] is True
            else f"- Worktree dirty: `{str(summary['git_dirty']).lower()}`"
        ),
        (
            f"- Cases: {counts['passed_cases']} pass / {counts['failed_cases']} fail / "
            f"{counts['errored_cases']} error / {counts['not_evaluable_cases']} not evaluable"
        ),
        (
            f"- Criteria: {counts['passed_criteria']} pass / "
            f"{counts['failed_criteria']} fail / {counts['unjudged_criteria']} unjudged"
        ),
        "- Baseline: `false`（只能经人工复核后另行选择）",
        "",
    ]
    if summary["failed_cases"]:
        markdown.extend(["## Failed Cases", ""])
        for case in summary["failed_cases"]:
            markdown.append(f"- `{case['case_id']}`")
            for failure in case["failed_criteria"]:
                reason = failure["reason"].replace("\n", " ")
                markdown.append(f"  - `{failure['criterion']}`：{reason}")
        markdown.append("")
    if summary["errored_cases"]:
        markdown.extend(["## Provider / Judge Errors", ""])
        for case in summary["errored_cases"]:
            reason = str(case["reason"]).replace("\n", " ")
            markdown.append(
                f"- `{case['case_id']}` — `{case['stage']}/{case['status']}`：{reason}"
            )
        markdown.append("")
    if summary["not_evaluable_cases"]:
        markdown.extend(["## Not Evaluated", ""])
        for case in summary["not_evaluable_cases"]:
            markdown.append(f"- `{case['case_id']}` — `{case['status']}`")
        markdown.append("")
    return "\n".join(markdown)


def build_report(run_dir: Path) -> dict[str, Any]:
    validate_result_artifacts(run_dir)
    metadata = load_json_object(run_dir / "run.json")
    responses = load_jsonl(run_dir / "responses.jsonl")
    judgments = (
        load_jsonl(run_dir / "judgments.jsonl")
        if (run_dir / "judgments.jsonl").exists()
        else []
    )
    refresh_run_metadata(metadata, responses, judgments)
    summary = aggregate_results(metadata, responses, judgments)
    summary["generated_at"] = utc_now()
    summary["artifacts"] = {
        "run": "run.json",
        "prepared": "prepared.jsonl",
        "eval_definition": "eval-definition.json",
        "runtime_snapshot": "runtime-snapshot.json",
        "source_snapshots": "source-snapshots.json",
        "responses": "responses.jsonl",
        "judgments": "judgments.jsonl",
        "summary": "summary.json",
        "summary_markdown": "summary.md",
    }
    write_json(run_dir / "summary.json", summary)
    (run_dir / "summary.md").write_text(
        render_summary_markdown(summary), encoding="utf-8", newline="\n"
    )
    metadata["report"] = {
        "generated_at": summary["generated_at"],
        "completion_status": summary["completion_status"],
        "behavioral_status": summary["behavioral_status"],
        "runtime_profile": summary["runtime_profile"],
        "bundle_hash": summary["bundle_hash"],
        "counts": summary["counts"],
    }
    write_json(run_dir / "run.json", metadata)
    return summary


def validate_response_record(record: dict[str, Any]) -> None:
    status = record.get("status")
    valid_statuses = {
        "MODEL_RESPONSE",
        "PROVIDER_ERROR",
        "TIMEOUT",
        "INVALID_RESPONSE",
    }
    if status not in valid_statuses:
        raise ModelEvalError(f"invalid target response status: {status!r}")
    response = record.get("response")
    if status == "MODEL_RESPONSE":
        if not isinstance(response, str) or not response.strip():
            raise ModelEvalError(f"{record.get('case_id')}: MODEL_RESPONSE needs text")
        if record.get("error") is not None:
            raise ModelEvalError(f"{record.get('case_id')}: MODEL_RESPONSE must not contain error")
    else:
        if response is not None:
            raise ModelEvalError(f"{record.get('case_id')}: error response must not contain text")
        if not isinstance(record.get("error"), str) or not record["error"].strip():
            raise ModelEvalError(f"{record.get('case_id')}: error response needs a reason")


def forbidden_secret_key(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in {"api_key", "authorization", "password", "secret"}:
                return str(key)
            found = forbidden_secret_key(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = forbidden_secret_key(nested)
            if found:
                return found
    return None


def fingerprint_mismatch(
    field: str, recorded: Any, computed: str, source: str
) -> ModelEvalError:
    return ModelEvalError(
        f"{field} mismatch: recorded={recorded!r}; computed={computed}; source={source}"
    )


def validate_prepared_snapshot(
    prepared: list[dict[str, Any]], definition: dict[str, Any]
) -> None:
    cases = definition.get("cases")
    criteria = definition.get("criteria")
    if not isinstance(cases, list) or not isinstance(criteria, dict):
        raise ModelEvalError("eval-definition.json has invalid cases or criteria")
    if any(
        not isinstance(value, dict) or not isinstance(value.get("question"), str)
        for value in criteria.values()
    ):
        raise ModelEvalError("eval-definition.json has invalid criterion definitions")
    expected = {
        case.get("id"): case for case in cases if isinstance(case, dict)
    }
    if len(expected) != len(cases) or None in expected:
        raise ModelEvalError("eval-definition.json has duplicate or invalid case ids")
    seen: set[str] = set()
    for record in prepared:
        case_id = record.get("case_id")
        case = expected.get(case_id)
        if not isinstance(case_id, str) or not isinstance(case, dict) or case_id in seen:
            raise ModelEvalError(f"prepared.jsonl has invalid or duplicate case: {case_id!r}")
        seen.add(case_id)
        if (
            record.get("title") != case.get("title")
            or record.get("mode") != case.get("mode")
            or record.get("input") != case.get("prompt")
        ):
            raise ModelEvalError(f"{case_id}: prepared snapshot differs from eval definition")
        required = case.get("required_criteria")
        if not isinstance(required, list):
            raise ModelEvalError(f"{case_id}: eval definition criteria are invalid")
        expected_criteria = [
            {"criterion": criterion, "question": criteria.get(criterion, {}).get("question")}
            for criterion in required
        ]
        if record.get("criteria") != expected_criteria:
            raise ModelEvalError(f"{case_id}: prepared criteria differ from eval definition")
        runtime = record.get("runtime")
        if (
            not isinstance(runtime, dict)
            or not isinstance(runtime.get("sources"), list)
            or not runtime["sources"]
            or not all(isinstance(item, str) and item for item in runtime["sources"])
            or not isinstance(runtime.get("content"), str)
            or not runtime["content"].strip()
        ):
            raise ModelEvalError(f"{case_id}: prepared runtime snapshot is invalid")


def validate_runtime_snapshot(
    snapshot: dict[str, Any], metadata: dict[str, Any]
) -> None:
    profile = snapshot.get("runtime_profile")
    if profile != metadata.get("runtime_profile") or profile not in RUNTIME_PROFILES:
        raise ModelEvalError(
            "runtime profile mismatch: "
            f"run.json={metadata.get('runtime_profile')!r}; "
            f"runtime-snapshot.json={profile!r}"
        )
    if profile == API_RUNTIME_PROFILE:
        if (
            snapshot.get("strategy")
            != "target_instructions_plus_prepared_per_case_runtime"
            or snapshot.get("prepared_runtime_source") != "prepared.jsonl"
            or not isinstance(snapshot.get("target_instructions"), str)
        ):
            raise ModelEvalError("runtime-snapshot.json has an invalid API runtime profile")
        return
    project = snapshot.get("project_instructions")
    knowledge = snapshot.get("knowledge")
    pack_info = snapshot.get("pack_info")
    if (
        snapshot.get("strategy") != "project_instructions_plus_generated_knowledge"
        or not isinstance(project, dict)
        or not isinstance(project.get("content"), str)
        or not isinstance(knowledge, list)
        or not knowledge
        or not isinstance(pack_info, dict)
    ):
        raise ModelEvalError("runtime-snapshot.json has an invalid ChatGPT Project profile")
    paths = [item.get("path") for item in knowledge if isinstance(item, dict)]
    if (
        len(paths) != len(knowledge)
        or len(set(paths)) != len(paths)
        or any(
            not isinstance(item.get("content"), str) or not item["content"].strip()
            for item in knowledge
            if isinstance(item, dict)
        )
    ):
        raise ModelEvalError("runtime-snapshot.json has invalid Knowledge snapshots")
    if normalize_pack_version(pack_info.get("pack_version")) != metadata.get("pack_version"):
        raise ModelEvalError("runtime-snapshot.json pack version differs from run.json")


def validate_provenance(
    metadata: dict[str, Any], snapshots: dict[str, Any]
) -> list[dict[str, Any]]:
    prepared = snapshots["prepared"]
    definition = snapshots["eval_definition"]
    runtime = snapshots["runtime"]
    sources = snapshots["sources"]
    validate_prepared_snapshot(prepared, definition)
    validate_runtime_snapshot(runtime, metadata)
    computed_definition = eval_definition_hash(definition)
    if metadata.get("eval_definition_hash") != computed_definition:
        raise fingerprint_mismatch(
            "eval_definition_hash",
            metadata.get("eval_definition_hash"),
            computed_definition,
            "eval-definition.json",
        )
    computed_bundle = bundle_hash(prepared, runtime)
    if metadata.get("bundle_hash") != computed_bundle:
        raise fingerprint_mismatch(
            "bundle_hash",
            metadata.get("bundle_hash"),
            computed_bundle,
            "prepared.jsonl + runtime-snapshot.json",
        )
    for field, source_name in (
        ("runner_revision", "runner"),
        ("skill_revision", "skill"),
    ):
        computed = source_content_hash(sources, source_name)
        if metadata.get(field) != computed:
            raise fingerprint_mismatch(
                field, metadata.get(field), computed, "source-snapshots.json"
            )
    if metadata.get("cases") != case_snapshots(prepared):
        raise ModelEvalError("run.json cases differ from prepared.jsonl")
    if metadata.get("pack_version") != prepared_version(prepared):
        raise ModelEvalError("run.json pack version differs from prepared.jsonl")
    return prepared


def validate_artifact_binding(
    record: dict[str, Any], metadata: dict[str, Any], label: str
) -> None:
    for field, expected in artifact_binding(metadata).items():
        if record.get(field) != expected:
            raise ModelEvalError(
                f"{label} artifact binding mismatch for {field}: "
                f"expected={expected!r}; actual={record.get(field)!r}"
            )


def parse_timestamp(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ModelEvalError(f"run.json {field} must be an ISO timestamp or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ModelEvalError(f"run.json {field} is not a valid ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ModelEvalError(f"run.json {field} must include a timezone")
    return parsed


def validate_lifecycle(
    metadata: dict[str, Any],
    responses: list[dict[str, Any]],
    judgments: list[dict[str, Any]],
) -> None:
    status = metadata.get("status")
    fields = (
        "created_at",
        "target_started_at",
        "target_completed_at",
        "judge_started_at",
        "judge_completed_at",
        "completed_at",
    )
    timestamps = {field: parse_timestamp(metadata.get(field), field) for field in fields}
    if timestamps["created_at"] is None:
        raise ModelEvalError("run.json created_at is required")
    target_complete = bool(responses) and len(responses) == len(metadata["cases"]) and all(
        record.get("status") == "MODEL_RESPONSE" for record in responses
    )
    judge_started = any(
        record.get("status") in {"JUDGMENT", "JUDGE_ERROR"} for record in judgments
    )
    requirements = {
        "target_started_at": bool(responses),
        "target_completed_at": target_complete,
        "judge_started_at": judge_started,
        "judge_completed_at": status == "COMPLETED",
        "completed_at": status in {"COMPLETED", "FAILED"},
    }
    for field, required in requirements.items():
        if (timestamps[field] is not None) != required:
            raise ModelEvalError(
                f"run lifecycle conflict: status={status}; {field}={metadata.get(field)!r}"
            )
    ordered = [timestamp for timestamp in timestamps.values() if timestamp is not None]
    if ordered != sorted(ordered):
        raise ModelEvalError("run lifecycle timestamps are out of order")


def validate_result_artifacts(run_dir: Path) -> None:
    metadata = load_json_object(run_dir / "run.json")
    required = {
        "schema_version",
        "evaluation_type",
        "product_version",
        "pack_version",
        "version_directory",
        "run_id",
        "status",
        "baseline",
        "runtime_profile",
        "git_sha",
        "git_dirty",
        "runner_revision",
        "eval_definition_hash",
        "bundle_hash",
        "skill_revision",
        "target",
        "created_at",
        "target_started_at",
        "target_completed_at",
        "judge_started_at",
        "judge_completed_at",
        "completed_at",
        "counts",
        "cases",
    }
    missing = required - set(metadata)
    if missing:
        raise ModelEvalError(f"{run_dir}: run.json missing fields {sorted(missing)}")
    if metadata.get("schema_version") != 2 or metadata.get("evaluation_type") != "model_behavioral":
        raise ModelEvalError(f"{run_dir}: unsupported run artifact schema")
    if metadata.get("run_id") != run_dir.name or metadata.get("baseline") is not False:
        raise ModelEvalError(f"{run_dir}: invalid run identity or automatic baseline flag")
    version = normalize_pack_version(metadata.get("pack_version"))
    if metadata.get("product_version") != version:
        raise ModelEvalError(f"{run_dir}: product_version differs from pack_version")
    if metadata.get("version_directory") != version_directory(version):
        raise ModelEvalError(f"{run_dir}: invalid version_directory")
    runtime_profile = metadata.get("runtime_profile")
    if runtime_profile not in RUNTIME_PROFILES:
        raise ModelEvalError(f"{run_dir}: invalid runtime_profile")
    if (
        run_dir.parent.name != runtime_profile
        or run_dir.parent.parent.name != version_directory(version)
    ):
        raise ModelEvalError(
            f"{run_dir}: result directory must be v{version}/{runtime_profile}/<run-id>"
        )
    hash_fields = (
        "runner_revision",
        "eval_definition_hash",
        "bundle_hash",
        "skill_revision",
    )
    for field in hash_fields:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(metadata.get(field))):
            raise ModelEvalError(f"{run_dir}: invalid {field}")
    if not re.fullmatch(r"[0-9a-f]{40}|unavailable", str(metadata.get("git_sha"))):
        raise ModelEvalError(f"{run_dir}: invalid git_sha")
    if metadata.get("git_dirty") not in {True, False, None}:
        raise ModelEvalError(f"{run_dir}: git_dirty must be boolean or null")
    target = metadata.get("target")
    if (
        not isinstance(target, dict)
        or not isinstance(target.get("provider"), str)
        or target.get("model") is not None
        and not isinstance(target.get("model"), str)
        or not isinstance(target.get("parameters"), dict)
    ):
        raise ModelEvalError(f"{run_dir}: invalid target identity")
    judge = metadata.get("judge")
    if judge is not None and (
        not isinstance(judge, dict)
        or not isinstance(judge.get("provider"), str)
        or judge.get("model") is not None
        and not isinstance(judge.get("model"), str)
        or not isinstance(judge.get("parameters"), dict)
    ):
        raise ModelEvalError(f"{run_dir}: invalid judge identity")
    snapshots = load_run_snapshots(run_dir)
    prepared = validate_provenance(metadata, snapshots)
    cases = metadata.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ModelEvalError(f"{run_dir}: cases snapshot is missing")
    case_ids: set[str] = set()
    for case in cases:
        case_id = case.get("case_id") if isinstance(case, dict) else None
        if not isinstance(case_id, str) or case_id in case_ids:
            raise ModelEvalError(f"{run_dir}: duplicate or invalid case snapshot")
        case_ids.add(case_id)
        criteria = case.get("criteria")
        if not isinstance(criteria, list) or not criteria:
            raise ModelEvalError(f"{run_dir}: {case_id} has no criteria snapshot")
        criterion_ids = [item.get("criterion") for item in criteria if isinstance(item, dict)]
        if len(criterion_ids) != len(criteria) or len(set(criterion_ids)) != len(criteria):
            raise ModelEvalError(f"{run_dir}: {case_id} has invalid criteria snapshot")
    responses = load_jsonl(run_dir / "responses.jsonl")
    response_index = index_case_records(responses, case_ids, "responses.jsonl")
    for record in responses:
        validate_artifact_binding(record, metadata, "responses.jsonl")
        validate_response_record(record)
    judgments = (
        load_jsonl(run_dir / "judgments.jsonl")
        if (run_dir / "judgments.jsonl").exists()
        else []
    )
    judgment_index = index_case_records(judgments, case_ids, "judgments.jsonl")
    cases_by_id = {case["case_id"]: case for case in cases}
    for case_id, judgment in judgment_index.items():
        validate_artifact_binding(judgment, metadata, "judgments.jsonl")
        status = judgment.get("status")
        if status not in {"JUDGMENT", "JUDGE_ERROR", "NOT_JUDGED"}:
            raise ModelEvalError(f"{run_dir}: invalid judgment status")
        target = response_index.get(case_id)
        if status == "JUDGMENT":
            if not target or target.get("status") != "MODEL_RESPONSE":
                raise ModelEvalError(f"{run_dir}: non-response case has behavioral judgment")
            validate_case_judgment(cases_by_id[case_id], judgment)
        elif judgment.get("criteria") is not None or not isinstance(
            judgment.get("error"), str
        ):
            raise ModelEvalError(f"{run_dir}: invalid non-judgment evidence")
    if judgments:
        for case_id, response in response_index.items():
            if response.get("status") == "MODEL_RESPONSE" and case_id not in judgment_index:
                raise ModelEvalError(f"{run_dir}: {case_id} is missing explicit judgment status")
    expected_counts = run_counts(cases, responses, judgments)
    if metadata.get("counts") != expected_counts:
        raise ModelEvalError(f"{run_dir}: run counts do not match artifacts")
    expected_status = derive_run_status(cases, responses, judgments)
    if metadata.get("status") != expected_status:
        raise ModelEvalError(f"{run_dir}: run status does not match artifacts")
    validate_lifecycle(metadata, responses, judgments)
    for artifact in (metadata, prepared, snapshots, responses, judgments):
        secret_key = forbidden_secret_key(artifact)
        if secret_key:
            raise ModelEvalError(f"{run_dir}: forbidden secret field {secret_key}")
    summary_path = run_dir / "summary.json"
    summary_markdown_path = run_dir / "summary.md"
    if summary_path.exists() != summary_markdown_path.exists():
        raise ModelEvalError(f"{run_dir}: summary artifacts must exist together")
    if summary_path.exists():
        summary = load_json_object(summary_path)
        expected_summary = aggregate_results(metadata, responses, judgments)
        for key, expected in expected_summary.items():
            if summary.get(key) != expected:
                raise ModelEvalError(f"{run_dir}: summary field {key} does not match artifacts")
        counts = summary.get("counts", {})
        criteria_sum = (
            counts.get("passed_criteria", -1)
            + counts.get("failed_criteria", -1)
            + counts.get("unjudged_criteria", -1)
        )
        if criteria_sum != counts.get("total_criteria"):
            raise ModelEvalError(f"{run_dir}: summary criteria counts are inconsistent")
        if forbidden_secret_key(summary):
            raise ModelEvalError(f"{run_dir}: summary contains a forbidden secret field")
        expected_report = {
            "generated_at": summary.get("generated_at"),
            "completion_status": summary.get("completion_status"),
            "behavioral_status": summary.get("behavioral_status"),
            "runtime_profile": summary.get("runtime_profile"),
            "bundle_hash": summary.get("bundle_hash"),
            "counts": counts,
        }
        if metadata.get("report") != expected_report:
            raise ModelEvalError(f"{run_dir}: run report metadata differs from summary")
        expected_markdown = render_summary_markdown(summary)
        if summary_markdown_path.read_text(encoding="utf-8") != expected_markdown:
            raise ModelEvalError(f"{run_dir}: summary.md differs from summary.json")


def validate_run_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
        raise ModelEvalError("run-id must use 1-80 safe filename characters")
    return value


def manual_target_text(user_input: str) -> str:
    return user_input.rstrip() + "\n"


def manual_instructions_text() -> str:
    return """# Manual ChatGPT Target Instructions

1. 使用专门的 Relationship Compass Eval Project。
2. 确认 Project Instructions 与 Generated Knowledge 来自本次导出的版本。
3. 不导入真实私人 Memory 或真实聊天数据。
4. 每个 target 文件新建一个独立 Chat。
5. 复制 target 文件的完整正文；文件正文只有原始用户输入。
6. 不发送文件名、case id、criteria、mode 或其他测试信息。
7. 原样保存 ChatGPT response，不人工润色或截断。
8. 把已完成 case 填入 `responses-template.jsonl`；未完成行应删除后再导入。
9. Target 与 Judge 必须使用不同 Chat context。
"""


def export_manual_bundle(
    prepared_records: list[dict[str, Any]],
    output_dir: Path,
    *,
    run_id: str | None = None,
    target_model: str | None = None,
) -> dict[str, Any]:
    validate_prepared_records(prepared_records)
    if output_dir.exists():
        raise ModelEvalError(f"refusing to overwrite existing manual export: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)
    target_dir = output_dir / "target"
    target_dir.mkdir()
    selected_run_id = validate_run_id(run_id or run_id_now())
    target = {
        "provider": "chatgpt_web_manual",
        "model": target_model,
        "parameters": {
            "execution": "explicit_copy_paste",
            "user_reported_model": target_model is not None,
        },
    }
    snapshots = build_run_snapshots(prepared_records, CHATGPT_RUNTIME_PROFILE)
    metadata = new_run_metadata(
        prepared_records,
        snapshots,
        run_id=selected_run_id,
        target=target,
        runtime_profile=CHATGPT_RUNTIME_PROFILE,
    )
    metadata["export_type"] = "manual_chatgpt_target"
    metadata["exported_at"] = utc_now()
    write_json(output_dir / "manual.json", metadata, exclusive=True)
    write_run_snapshots(output_dir, snapshots)
    (output_dir / "INSTRUCTIONS.md").write_text(
        manual_instructions_text(), encoding="utf-8", newline="\n"
    )
    templates: list[dict[str, Any]] = []
    for index, record in enumerate(prepared_records, 1):
        filename = f"{index:02d}-{record['case_id']}.md"
        (target_dir / filename).write_text(
            manual_target_text(record["input"]), encoding="utf-8", newline="\n"
        )
        templates.append({"case_id": record["case_id"], "response": ""})
    write_jsonl(output_dir / "responses-template.jsonl", templates, exclusive=True)
    return metadata


def load_manual_bundle(
    manual_dir: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    metadata = load_json_object(manual_dir / "manual.json")
    snapshots = load_run_snapshots(manual_dir)
    prepared = validate_provenance(metadata, snapshots)
    if metadata.get("runtime_profile") != CHATGPT_RUNTIME_PROFILE:
        raise ModelEvalError("manual export has an invalid runtime profile")
    validate_run_id(str(metadata.get("run_id", "")))
    if not re.fullmatch(r"[0-9a-f]{40}|unavailable", str(metadata.get("git_sha"))):
        raise ModelEvalError("manual export has an invalid git_sha")
    if metadata.get("git_dirty") not in {True, False, None}:
        raise ModelEvalError("manual export has an invalid git_dirty value")
    version = prepared_version(prepared)
    if (
        metadata.get("pack_version") != version
        or metadata.get("version_directory") != version_directory(version)
        or metadata.get("status") != "PREPARED"
        or metadata.get("baseline") is not False
    ):
        raise ModelEvalError("manual export identity differs from its snapshots")
    validate_lifecycle(metadata, [], [])
    if forbidden_secret_key([metadata, snapshots]):
        raise ModelEvalError("manual export contains a forbidden secret field")
    return metadata, prepared, snapshots


def pending_judgments(
    metadata: dict[str, Any],
    responses: list[dict[str, Any]],
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    cases = metadata["cases"]
    case_ids = {case["case_id"] for case in cases}
    response_index = index_case_records(responses, case_ids, "responses.jsonl")
    existing_index = index_case_records(existing or [], case_ids, "judgments.jsonl")
    records: list[dict[str, Any]] = []
    for case in cases:
        case_id = case["case_id"]
        current = existing_index.get(case_id)
        if current and current.get("status") != "NOT_JUDGED":
            records.append(current)
            continue
        target = response_index.get(case_id)
        reason = (
            "manual judgment pending"
            if target and target.get("status") == "MODEL_RESPONSE"
            else "target response pending"
        )
        records.append(
            {
                "schema_version": 1,
                **artifact_binding(metadata),
                "case_id": case_id,
                "status": "NOT_JUDGED",
                "criteria": None,
                "error": reason,
            }
        )
    return records


def invalidate_report(run_dir: Path, metadata: dict[str, Any]) -> None:
    for filename in ("summary.json", "summary.md"):
        path = run_dir / filename
        if path.exists():
            path.unlink()
    metadata.pop("report", None)


def initialize_manual_result(
    manual_metadata: dict[str, Any],
    snapshots: dict[str, Any],
    run_dir: Path,
) -> dict[str, Any]:
    if run_dir.exists():
        raise ModelEvalError(f"refusing to overwrite existing run directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=False)
    metadata = json.loads(json.dumps(manual_metadata, ensure_ascii=False))
    metadata["manual_export"] = {
        "exported_at": metadata.pop("exported_at"),
        "bundle_hash": metadata["bundle_hash"],
    }
    metadata.pop("export_type", None)
    responses: list[dict[str, Any]] = []
    judgments = pending_judgments(metadata, responses)
    refresh_run_metadata(metadata, responses, judgments)
    write_run_snapshots(run_dir, snapshots)
    write_json(run_dir / "run.json", metadata, exclusive=True)
    write_jsonl(run_dir / "responses.jsonl", responses, exclusive=True)
    write_jsonl(run_dir / "judgments.jsonl", judgments, exclusive=True)
    return metadata


def validate_manual_response_import(
    records: list[dict[str, Any]], case_ids: set[str]
) -> str | None:
    if not records:
        raise ModelEvalError("manual response import is empty")
    index_case_records(records, case_ids, "manual response import")
    reported_models: set[str] = set()
    for record in records:
        response = record.get("response")
        if not isinstance(response, str) or not response.strip():
            raise ModelEvalError(f"{record.get('case_id')}: manual response must be non-empty")
        model = record.get("target_model")
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                raise ModelEvalError("target_model must be a non-empty string or omitted")
            reported_models.add(model.strip())
    if len(reported_models) > 1:
        raise ModelEvalError("manual response import reports multiple target models")
    return next(iter(reported_models), None)


def import_manual_responses(
    manual_dir: Path,
    input_path: Path,
    results_base: Path,
    *,
    target_model: str | None = None,
) -> tuple[Path, dict[str, Any], int]:
    manual_metadata, prepared, snapshots = load_manual_bundle(manual_dir)
    import_records = load_jsonl(input_path)
    case_ids = {record["case_id"] for record in prepared}
    imported_model = validate_manual_response_import(import_records, case_ids)
    selected_model = target_model or imported_model
    exported_model = manual_metadata["target"].get("model")
    if exported_model and selected_model and exported_model != selected_model:
        raise ModelEvalError("target model differs from manual export metadata")
    selected_model = selected_model or exported_model
    version = prepared_version(prepared)
    run_dir = (
        results_root(version, results_base, CHATGPT_RUNTIME_PROFILE)
        / manual_metadata["run_id"]
    )
    if run_dir.exists():
        validate_result_artifacts(run_dir)
        metadata = load_json_object(run_dir / "run.json")
        for field in (
            "pack_version",
            "eval_definition_hash",
            "bundle_hash",
            "runner_revision",
            "skill_revision",
            "runtime_profile",
            "git_sha",
            "git_dirty",
        ):
            if metadata.get(field) != manual_metadata.get(field):
                raise ModelEvalError(f"existing manual run differs on {field}")
    else:
        metadata = initialize_manual_result(manual_metadata, snapshots, run_dir)
    existing = load_jsonl(run_dir / "responses.jsonl")
    existing_ids = {record["case_id"] for record in existing}
    duplicate_ids = existing_ids & {record["case_id"] for record in import_records}
    if duplicate_ids:
        raise ModelEvalError(
            "manual response import would overwrite existing cases: "
            + ", ".join(sorted(duplicate_ids))
        )
    current_model = metadata["target"].get("model")
    if current_model and selected_model and current_model != selected_model:
        raise ModelEvalError("target model differs from existing run metadata")
    if selected_model:
        metadata["target"]["model"] = selected_model
        metadata["target"]["parameters"]["user_reported_model"] = True
    imported_at = utc_now()
    if metadata.get("target_started_at") is None:
        metadata["target_started_at"] = imported_at
    appended = [
        {
            "schema_version": 1,
            **artifact_binding(metadata),
            "case_id": record["case_id"],
            "started_at": None,
            "completed_at": imported_at,
            "status": "MODEL_RESPONSE",
            "response": record["response"],
            "provider": "chatgpt_web_manual",
            "model": metadata["target"].get("model"),
            "provider_response_id": None,
            "usage": None,
            "error": None,
        }
        for record in import_records
    ]
    responses = [*existing, *appended]
    judgments = pending_judgments(
        metadata, responses, load_jsonl(run_dir / "judgments.jsonl")
    )
    write_jsonl(run_dir / "responses.jsonl", responses)
    write_jsonl(run_dir / "judgments.jsonl", judgments)
    refresh_run_metadata(metadata, responses, judgments)
    if metadata["counts"]["not_run"] == 0:
        metadata["target_completed_at"] = imported_at
    invalidate_report(run_dir, metadata)
    write_json(run_dir / "run.json", metadata)
    return run_dir, metadata, len(appended)


def manual_judge_text(case: dict[str, Any], response: str) -> str:
    criteria = "\n".join(
        f"- `{item['criterion']}`：{item['question']}" for item in case["criteria"]
    )
    criterion_shape = ",\n".join(
        (
            "    {"
            f'"criterion":"{item["criterion"]}",'
            '"passed":<true-or-false>,"reason":"简短理由"}'
        )
        for item in case["criteria"]
    )
    return f"""# Independent Judge Input

请在与 Target 完全独立的 Chat 或人工评审环境中判断。不要使用此前 Target Chat 的上下文。

## Case ID

`{case['case_id']}`

## User Input

{case['input']}

## Target Response

{response}

## Criteria

{criteria}

## Required Output

每个 criterion 必须给出 `passed` 布尔值和非空 `reason`，不要给总分或 baseline 判断：

```text
{{"case_id":"{case['case_id']}","criteria":[
{criterion_shape}
]}}
```
"""


def export_manual_judge(run_dir: Path, output_dir: Path) -> int:
    validate_result_artifacts(run_dir)
    if output_dir.exists():
        raise ModelEvalError(f"refusing to overwrite existing judge export: {output_dir}")
    metadata = load_json_object(run_dir / "run.json")
    if metadata.get("runtime_profile") != CHATGPT_RUNTIME_PROFILE:
        raise ModelEvalError("manual judge export requires chatgpt_project runtime")
    if metadata.get("status") not in {"TARGET_COMPLETE", "JUDGE_PARTIAL"}:
        raise ModelEvalError("manual judge export requires a complete target phase")
    responses = index_case_records(
        load_jsonl(run_dir / "responses.jsonl"),
        {case["case_id"] for case in metadata["cases"]},
        "responses.jsonl",
    )
    judgments = index_case_records(
        load_jsonl(run_dir / "judgments.jsonl")
        if (run_dir / "judgments.jsonl").exists()
        else [],
        {case["case_id"] for case in metadata["cases"]},
        "judgments.jsonl",
    )
    candidates = [
        case
        for case in metadata["cases"]
        if responses.get(case["case_id"], {}).get("status") == "MODEL_RESPONSE"
        and judgments.get(case["case_id"], {}).get("status") != "JUDGMENT"
    ]
    if not candidates:
        raise ModelEvalError("no unjudged MODEL_RESPONSE cases are available")
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "INSTRUCTIONS.md").write_text(
        "# Manual Judge Instructions\n\n"
        "每个 case 使用独立于 Target 的 Chat 或人工评审。逐项填写布尔 PASS/FAIL 与理由；"
        "不要参考既有得分或决定 baseline。\n",
        encoding="utf-8",
        newline="\n",
    )
    templates: list[dict[str, Any]] = []
    for index, case in enumerate(candidates, 1):
        case_id = case["case_id"]
        (output_dir / f"{index:02d}-{case_id}.md").write_text(
            manual_judge_text(case, responses[case_id]["response"]),
            encoding="utf-8",
            newline="\n",
        )
        templates.append(
            {
                "case_id": case_id,
                "criteria": [
                    {"criterion": item["criterion"], "passed": None, "reason": ""}
                    for item in case["criteria"]
                ],
            }
        )
    write_jsonl(output_dir / "judgments-template.jsonl", templates, exclusive=True)
    return len(candidates)


def import_manual_judgments(
    run_dir: Path,
    input_path: Path,
    *,
    judge_mode: str,
    judge_model: str | None = None,
) -> tuple[dict[str, Any], int]:
    validate_result_artifacts(run_dir)
    metadata = load_json_object(run_dir / "run.json")
    if metadata.get("runtime_profile") != CHATGPT_RUNTIME_PROFILE:
        raise ModelEvalError("manual judgment import requires chatgpt_project runtime")
    if metadata.get("status") not in {"TARGET_COMPLETE", "JUDGE_PARTIAL"}:
        raise ModelEvalError("manual judgment import requires a complete target phase")
    cases = metadata["cases"]
    case_ids = {case["case_id"] for case in cases}
    cases_by_id = {case["case_id"]: case for case in cases}
    responses = index_case_records(
        load_jsonl(run_dir / "responses.jsonl"), case_ids, "responses.jsonl"
    )
    imports = load_jsonl(input_path)
    import_index = index_case_records(imports, case_ids, "manual judgment import")
    if not imports:
        raise ModelEvalError("manual judgment import is empty")
    normalized: dict[str, list[dict[str, Any]]] = {}
    for case_id, record in import_index.items():
        target = responses.get(case_id)
        if not target or target.get("status") != "MODEL_RESPONSE":
            raise ModelEvalError(f"{case_id}: cannot judge a missing or failed target response")
        normalized[case_id] = parse_judgment(
            json.dumps({"criteria": record.get("criteria")}, ensure_ascii=False),
            cases_by_id[case_id]["criteria"],
        )
    existing_records = (
        load_jsonl(run_dir / "judgments.jsonl")
        if (run_dir / "judgments.jsonl").exists()
        else pending_judgments(metadata, list(responses.values()))
    )
    existing = index_case_records(existing_records, case_ids, "judgments.jsonl")
    duplicates = [
        case_id
        for case_id in normalized
        if existing.get(case_id, {}).get("status") != "NOT_JUDGED"
    ]
    if duplicates:
        raise ModelEvalError(
            "manual judgment import would overwrite existing cases: "
            + ", ".join(sorted(duplicates))
        )
    provider = "human" if judge_mode == "manual_human" else "chatgpt_web_manual"
    current_judge = metadata.get("judge")
    if current_judge is not None and (
        current_judge.get("provider") != provider
        or current_judge.get("model") != judge_model
    ):
        raise ModelEvalError("manual judgment identity differs from existing judge metadata")
    imported_at = utc_now()
    for case_id, criteria in normalized.items():
        existing[case_id] = {
            "schema_version": 1,
            **artifact_binding(metadata),
            "case_id": case_id,
            "status": "JUDGMENT",
            "criteria": criteria,
            "judge": {"provider": provider, "model": judge_model},
            "provider_response_id": None,
            "usage": None,
            "evaluated_at": imported_at,
            "error": None,
        }
    judgments = [existing[case["case_id"]] for case in cases]
    metadata["judge"] = {
        "provider": provider,
        "model": judge_model,
        "parameters": {
            "judge_mode": judge_mode,
            "independent_context_required": True,
        },
    }
    if metadata.get("judge_started_at") is None:
        metadata["judge_started_at"] = imported_at
    write_jsonl(run_dir / "judgments.jsonl", judgments)
    refresh_run_metadata(metadata, list(responses.values()), judgments)
    if metadata["status"] == "COMPLETED":
        metadata["judge_completed_at"] = imported_at
        metadata["completed_at"] = imported_at
    invalidate_report(run_dir, metadata)
    write_json(run_dir / "run.json", metadata)
    return metadata, len(normalized)


def command_validate(_: argparse.Namespace) -> int:
    cases, criteria = load_definitions()
    print(
        f"model eval definitions valid: {len(cases)} cases, {len(criteria)} criteria; "
        "behavioral evaluation NOT RUN"
    )
    return 0


def command_prepare(args: argparse.Namespace) -> int:
    cases, criteria = load_definitions()
    output = Path(args.output).expanduser().resolve()
    records = prepare_cases(cases, criteria)
    write_prepared(output, records)
    print(f"prepared {len(records)} canonical runtime work items: {output}")
    print(f"bundle_hash={bundle_hash(records, runtime_snapshot(API_RUNTIME_PROFILE, records))}")
    print("behavioral evaluation NOT RUN")
    return 0


def create_openai_provider(args: argparse.Namespace) -> OpenAIResponsesProvider:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    model = args.model or os.environ.get(args.model_env, "")
    base_url = os.environ.get("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL)
    return OpenAIResponsesProvider(
        api_key=api_key,
        model=model,
        base_url=base_url,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
        max_output_tokens=args.max_output_tokens,
    )


def command_run(args: argparse.Namespace) -> int:
    prepared = load_jsonl(Path(args.prepared).expanduser().resolve())
    validate_prepared_records(prepared)
    provider = create_openai_provider(args)
    results_base = Path(args.results_root).expanduser().resolve()
    selected_run_id = validate_run_id(args.run_id or run_id_now())
    candidate = (
        results_root(prepared_version(prepared), results_base, API_RUNTIME_PROFILE)
        / selected_run_id
    )
    if candidate.exists():
        raise ModelEvalError(f"refusing to overwrite existing run directory: {candidate}")
    metadata = execute_run(prepared, provider, candidate)
    print(f"run_dir={candidate}")
    print(json.dumps({"status": metadata["status"], "counts": metadata["counts"]}, indent=2))
    return 0 if metadata["counts"]["model_response"] == len(prepared) else 2


def command_judge(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    provider = create_openai_provider(args)
    counts = execute_judge(run_dir, provider)
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0 if counts["judge_error"] == 0 and counts["not_judged"] == 0 else 2


def command_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    summary = build_report(run_dir)
    print(
        f"{summary['behavioral_status']}: "
        f"cases={summary['counts']['passed_cases']} pass/"
        f"{summary['counts']['failed_cases']} fail/"
        f"{summary['counts']['errored_cases']} error; "
        f"summary={run_dir / 'summary.json'}"
    )
    return 0 if summary["completion_status"] == "COMPLETED" else 2


def command_export_manual(args: argparse.Namespace) -> int:
    prepared = load_jsonl(Path(args.prepared).expanduser().resolve())
    output = Path(args.output).expanduser().resolve()
    metadata = export_manual_bundle(
        prepared,
        output,
        run_id=args.run_id,
        target_model=args.target_model,
    )
    print(f"manual_target_dir={output / 'target'}")
    print(f"run_id={metadata['run_id']}")
    print(f"bundle_hash={metadata['bundle_hash']}")
    return 0


def command_import_responses(args: argparse.Namespace) -> int:
    run_dir, metadata, imported = import_manual_responses(
        Path(args.manual_dir).expanduser().resolve(),
        Path(args.input).expanduser().resolve(),
        Path(args.results_root).expanduser().resolve(),
        target_model=args.target_model,
    )
    print(f"run_dir={run_dir}")
    print(
        f"imported={imported}; completed={metadata['counts']['model_response']}; "
        f"pending={metadata['counts']['not_run']}; status={metadata['status']}"
    )
    return 0


def command_export_judge(args: argparse.Namespace) -> int:
    output = Path(args.output).expanduser().resolve()
    count = export_manual_judge(
        Path(args.run_dir).expanduser().resolve(),
        output,
    )
    print(f"exported_judge_cases={count}; output={output}")
    return 0


def command_import_judgments(args: argparse.Namespace) -> int:
    metadata, imported = import_manual_judgments(
        Path(args.run_dir).expanduser().resolve(),
        Path(args.input).expanduser().resolve(),
        judge_mode=args.judge_mode,
        judge_model=args.judge_model,
    )
    print(
        f"imported={imported}; judged={metadata['counts']['judged']}; "
        f"not_judged={metadata['counts']['not_judged']}; status={metadata['status']}"
    )
    return 0


def add_provider_arguments(parser: argparse.ArgumentParser, *, judge: bool) -> None:
    parser.add_argument("--model")
    parser.set_defaults(model_env="OPENAI_JUDGE_MODEL" if judge else "OPENAI_MODEL")
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--max-output-tokens", type=int, default=1200 if not judge else 2400)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate definitions; never call a model")
    validate.set_defaults(func=command_validate)
    prepare = subparsers.add_parser("prepare", help="assemble canonical runtime bundles")
    prepare.add_argument("--output", required=True)
    prepare.set_defaults(func=command_prepare)
    run = subparsers.add_parser("run", help="execute target model and save each response")
    run.add_argument("--prepared", required=True)
    run.add_argument("--results-root", default=str(RESULTS_BASE))
    run.add_argument("--run-id")
    add_provider_arguments(run, judge=False)
    run.set_defaults(func=command_run)
    judge = subparsers.add_parser("judge", help="independently judge saved target responses")
    judge.add_argument("--run-dir", required=True)
    add_provider_arguments(judge, judge=True)
    judge.set_defaults(func=command_judge)
    export_manual = subparsers.add_parser(
        "export-manual", help="export user-only cases for manual ChatGPT Project execution"
    )
    export_manual.add_argument("--prepared", required=True)
    export_manual.add_argument("--output", required=True)
    export_manual.add_argument("--run-id")
    export_manual.add_argument("--target-model")
    export_manual.set_defaults(func=command_export_manual)
    import_responses = subparsers.add_parser(
        "import-responses", help="append verbatim manual target responses"
    )
    import_responses.add_argument("--manual-dir", required=True)
    import_responses.add_argument("--input", required=True)
    import_responses.add_argument("--results-root", default=str(RESULTS_BASE))
    import_responses.add_argument("--target-model")
    import_responses.set_defaults(func=command_import_responses)
    export_judge = subparsers.add_parser(
        "export-judge", help="export independent manual judge cases"
    )
    export_judge.add_argument("--run-dir", required=True)
    export_judge.add_argument("--output", required=True)
    export_judge.set_defaults(func=command_export_judge)
    import_judgments = subparsers.add_parser(
        "import-judgments", help="import manual human or ChatGPT judgments"
    )
    import_judgments.add_argument("--run-dir", required=True)
    import_judgments.add_argument("--input", required=True)
    import_judgments.add_argument(
        "--judge-mode", choices=("manual_human", "manual_chatgpt"), required=True
    )
    import_judgments.add_argument("--judge-model")
    import_judgments.set_defaults(func=command_import_judgments)
    report = subparsers.add_parser("report", help="aggregate judgments into summary artifacts")
    report.add_argument("--run-dir", required=True)
    report.set_defaults(func=command_report)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        return args.func(args)
    except (OSError, ModelEvalError) as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
