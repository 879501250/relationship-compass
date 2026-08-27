#!/usr/bin/env python3
"""Validate the distributable relationship-compass skill."""

from __future__ import annotations

import argparse
import contextlib
import io
import re
import json
import sys
import unittest
from math import ceil
from pathlib import Path

sys.dont_write_bytecode = True

from date_utils import normalize_iso8601
from build_chatgpt_pack import build_knowledge_bodies, pack_metadata
from knowledge_intake import KnowledgeIntakeError, parse_proposal
from knowledge_schema import KnowledgeSchemaError, load_registry, stable_claim_id
from run_contract_evals import main as run_contract_evals
from run_model_evals import (
    ModelEvalError,
    RUNTIME_PROFILES,
    command_validate as validate_model_eval_command,
    validate_result_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
SKILL_MAX_LINES = 150
SKILL_MAX_CHARACTERS = 5_000
SKILL_MAX_APPROX_TOKENS = 4_500

REQUIRED_KNOWLEDGE = (
    "01-证据分级与内容边界.md",
    "05-PUA操控与伦理替代.md",
    "08-同意边界性与亲密.md",
    "09-在线约会与数字关系.md",
    "17-中国法律安全与危机转介.md",
    "20-经典社交体系的机制、证据与风险边界.md",
)
REQUIRED_PRACTICAL = (
    "00-导读与使用分级.md",
    "关系投入失衡：互惠判断、降级投入与退出决策.md",
    "场景感、松弛感与社交校准：从接话到关系推进.md",
    "实战话术编排器：从一句回复到后续分支.md",
    "主动表达、第一次见面与自然接触.md",
    "自然流、内在状态与结构化互动：伦理能力转译.md",
    "ChatLab聊天记录分析适配.md",
    "长期记忆与关系档案.md",
)
REQUIRED_PERSONAL = (
    "缺失上下文与高信息量追问.md",
    "微信截图解析协议.md",
    "关系阶段与聊天节奏.md",
    "自然回复生成器.md",
    "网络聊天表达升级器.md",
    "幽默与调侃生成器.md",
    "主动话题与conversation-hook.md",
    "投入预算与停止条件.md",
    "成长状态与记忆适配.md",
    "复盘模式与实际发送学习闭环.md",
    "memory_lifecycle.md",
)
REQUIRED_CURATED = (
    "INDEX.md",
    "relationship-start.md",
    "conversation.md",
    "attraction.md",
    "intimacy.md",
    "boundaries.md",
    "conflict-and-repair.md",
    "personal-growth.md",
)
REQUIRED_EVALS = (
    "contract_cases.yaml",
)


def require(path: str) -> Path:
    target = ROOT / path
    if not target.exists():
        ERRORS.append(f"missing required path: {path}")
    return target


def require_test_suite(path: str) -> None:
    directory = require(path)
    if directory.is_dir() and not any(directory.glob("test_*.py")):
        ERRORS.append(f"test suite has no test_*.py files: {path}")


def validate_frontmatter() -> None:
    skill = require("SKILL.md")
    if not skill.is_file():
        return
    content = skill.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not match:
        ERRORS.append("SKILL.md has invalid YAML frontmatter boundaries")
        return
    frontmatter = match.group(1)
    keys = re.findall(r"^([A-Za-z0-9_-]+):", frontmatter, re.MULTILINE)
    if keys != ["name", "description"]:
        ERRORS.append(f"frontmatter keys must be name, description; got {keys}")
    name_match = re.search(r"^name:\s*([^\n]+)$", frontmatter, re.MULTILINE)
    description_match = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    name = name_match.group(1).strip() if name_match else ""
    description = description_match.group(1).strip() if description_match else ""
    if name != "relationship-compass" or not re.fullmatch(r"[a-z0-9-]{1,64}", name):
        ERRORS.append(f"invalid skill name: {name!r}")
    if not description or len(description) > 1024 or "<" in description or ">" in description:
        ERRORS.append("description is empty, too long, or contains angle brackets")


def approximate_token_count(content: str) -> int:
    cjk = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", content))
    latin_words = len(re.findall(r"[A-Za-z0-9_]+", content))
    other = len(re.findall(r"[^\sA-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", content))
    return cjk + ceil(latin_words * 1.3) + ceil(other / 4)


def validate_skill_budget() -> None:
    skill = ROOT / "SKILL.md"
    if not skill.is_file():
        return
    content = skill.read_text(encoding="utf-8")
    lines = len(content.splitlines())
    characters = len(content)
    tokens = approximate_token_count(content)
    if lines > SKILL_MAX_LINES:
        ERRORS.append(f"SKILL.md exceeds {SKILL_MAX_LINES} lines: {lines}")
    if characters > SKILL_MAX_CHARACTERS:
        ERRORS.append(f"SKILL.md exceeds {SKILL_MAX_CHARACTERS} characters: {characters}")
    if tokens > SKILL_MAX_APPROX_TOKENS:
        ERRORS.append(f"SKILL.md exceeds approximate token budget {SKILL_MAX_APPROX_TOKENS}: {tokens}")


def validate_inventory(runtime_only: bool) -> None:
    require("agents/openai.yaml")
    require("scripts/memory_store.py")
    require("scripts/date_utils.py")
    require("scripts/run_tests.py")
    require("scripts/run_contract_evals.py")
    require("scripts/run_model_evals.py")
    require("scripts/knowledge_schema.py")
    require("scripts/knowledge_intake.py")
    require("scripts/knowledge_merge.py")
    require("scripts/build_chatgpt_pack.py")
    require("LICENSE")
    for filename in REQUIRED_KNOWLEDGE:
        require(f"references/knowledge/{filename}")
    for filename in REQUIRED_PRACTICAL:
        require(f"references/practical/{filename}")
    for filename in REQUIRED_PERSONAL:
        require(f"references/personal/{filename}")
    for filename in REQUIRED_CURATED:
        require(f"references/curated/{filename}")
    if not runtime_only:
        require("README.md")
        require("NOTICE.md")
        require("CHANGELOG.md")
        require("UPSTREAM_LOCK.json")
        require("UPSTREAM_LOCK.md")
        require("shared/CORE_POLICY.md")
        require("shared/FACT_HYPOTHESIS_POLICY.md")
        require_test_suite("tests/unit")
        require_test_suite("tests/integration")
        require("tests/unit/test_repository_convergence.py")
        require("model_evals/cases.yaml")
        require("model_evals/rubric.yaml")
        require("model_evals/README.md")
        require("model_evals/provider_profiles.example.yaml")
        require("model_evals/results/README.md")
        require("knowledge-management/KNOWLEDGE_GOVERNANCE.md")
        require("knowledge-management/SOURCE_REGISTRY.json")
        require("knowledge-management/CURATED_CLAIMS.json")
        require("knowledge-management/schemas/source-registry.schema.json")
        require("knowledge-management/schemas/claim.schema.json")
        require("knowledge-management/source-cards/TEMPLATE.md")
        require("knowledge-management/proposals/README.md")
        require("knowledge-management/review-decisions/README.md")
        require("knowledge-management/merge-reports/README.md")
        require("chatgpt-project/PROJECT_INSTRUCTIONS.md")
        require("chatgpt-project/README.md")
        for filename in (
            "01-CORE_POLICY.md",
            "02-DAILY_CONVERSATION.md",
            "03-RELATIONSHIP_SIGNALS.md",
            "04-GROWTH_AND_REVIEW.md",
            "05-SAFETY_AND_EVIDENCE.md",
            "06-CURATED_CLAIMS.md",
            "KNOWLEDGE_PACK_INFO.json",
        ):
            require(f"chatgpt-project/generated-knowledge/{filename}")
        require("sync/CHATGPT_TO_CODEX.md")
        require("sync/CODEX_TO_CHATGPT.md")
        require("sync/CHECKPOINT_TEMPLATE.md")
        for filename in REQUIRED_EVALS:
            require(f"evals/{filename}")
    agent = ROOT / "agents/openai.yaml"
    if agent.is_file():
        text = agent.read_text(encoding="utf-8")
        if "$relationship-compass" not in text:
            ERRORS.append("agents/openai.yaml must mention $relationship-compass")
        if 'display_name: "关系罗盘"' not in text:
            ERRORS.append("agents/openai.yaml must use the display name 关系罗盘")


def validate_repository_convergence(runtime_only: bool = False) -> None:
    """Validate the current identity and reject repository-cleanliness regressions."""
    text_suffixes = {".md", ".py", ".yaml", ".yml", ".json", ".txt"}
    marker = "Modified" + " by AI"
    encoded_name = re.compile(r"#U[0-9A-Fa-f]{4}")
    for path in ROOT.rglob("*"):
        if ".git" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if any(encoded_name.search(part) for part in path.relative_to(ROOT).parts):
            ERRORS.append(f"encoded Unicode path: {relative}")
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if marker in content:
            ERRORS.append(f"repository marker found: {relative}")
    memory_store = ROOT / "scripts" / "memory_store.py"
    if memory_store.is_file():
        memory_text = memory_store.read_text(encoding="utf-8")
        for required in (
            'MEMORY_NAMESPACE = "relationship-compass"',
            'MEMORY_ENV = "RELATIONSHIP_COMPASS_MEMORY_DIR"',
        ):
            if required not in memory_text:
                ERRORS.append(f"current Memory identity missing: {required}")
    if runtime_only:
        return
    for relative in (
        "chatgpt-project/knowledge",
        "IMPLEMENTATION_REPORT.md",
        "INTERNAL_RENAME_REPORT.md",
        "RENAME_INVENTORY.md",
        "V1_1_REVIEW.md",
        "V1_1_1_IMPLEMENTATION_REPORT.md",
        "V1_2_IMPLEMENTATION_REPORT.md",
    ):
        if (ROOT / relative).exists():
            ERRORS.append(f"obsolete repository artifact: {relative}")


def validate_routes_and_invariants() -> None:
    skill = ROOT / "SKILL.md"
    if not skill.is_file():
        return
    content = skill.read_text(encoding="utf-8")
    required = (
        "默认只读当前问题直接需要的 1–3 份参考",
        "references/personal/微信截图解析协议.md",
        "references/personal/缺失上下文与高信息量追问.md",
        "references/personal/关系阶段与聊天节奏.md",
        "references/personal/自然回复生成器.md",
        "references/personal/网络聊天表达升级器.md",
        "references/personal/幽默与调侃生成器.md",
        "references/personal/主动话题与conversation-hook.md",
        "references/personal/投入预算与停止条件.md",
        "references/personal/成长状态与记忆适配.md",
        "E1–E5 只表示当前消息的表达强度",
        "只做一个能发出的 small stretch",
        "允许无技巧回复",
        "Continuation ownership",
        "积极接梗",
        "A0 assisted",
        "A1 collaborative",
        "A2 calibration",
        "对方反馈用于关系策略，不作为用户成长的主要分数",
        "按对象隔离",
        "线上可比线下主动丰富",
        "她刚回",
        "明确表示不发展、要求别联系或反复不欢迎时停止",
        "不声称能直接读取、解密或导出",
        "Observed Fact → Reasonable Interpretation → Relationship Conclusion",
        "Evidence → Stage + Trend → Evidence Strength／Conflict → Current Action",
        "走势必须相对该对象的既有互动基线",
        "references/curated/INDEX.md",
    )
    for marker in required:
        if marker not in content:
            ERRORS.append(f"SKILL.md missing invariant or route: {marker}")


def validate_runtime_boundaries() -> None:
    runtime_roots = (
        ROOT / "SKILL.md",
        ROOT / "agents",
        ROOT / "references",
        ROOT / "scripts",
        ROOT / "shared",
        ROOT / "assets",
    )
    forbidden_parts = {"research", "documentation", ".git", "__pycache__", "evals"}
    for runtime_root in runtime_roots:
        if not runtime_root.exists():
            continue
        paths = (runtime_root,) if runtime_root.is_file() else runtime_root.rglob("*")
        for path in paths:
            if forbidden_parts.intersection(path.relative_to(ROOT).parts):
                ERRORS.append(f"non-runtime content inside runtime allowlist: {path.relative_to(ROOT)}")
            if path.is_file() and path.suffix in {".pyc", ".pyo"}:
                ERRORS.append(f"compiled artifact found: {path.relative_to(ROOT)}")


def validate_curated_knowledge(runtime_only: bool) -> None:
    runtime_claim_ids: set[str] = set()
    claim_markers = (
        "- Claim ID:",
        "- Practical Meaning:",
        "- Evidence:",
        "- Applicable When:",
        "- Limits:",
        "- Risks:",
        "- Sources:",
    )
    for filename in REQUIRED_CURATED[1:]:
        path = ROOT / "references" / "curated" / filename
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for block in text.split("\n## Claim\n")[1:]:
            for marker in claim_markers:
                if marker not in block:
                    ERRORS.append(f"curated claim in {filename} missing {marker}")
            match = re.search(r"- Claim ID: `(claim-[0-9a-f]{16})`", block)
            if not match:
                ERRORS.append(f"curated claim in {filename} has invalid Claim ID")
            elif match.group(1) in runtime_claim_ids:
                ERRORS.append(f"duplicate curated claim block: {match.group(1)}")
            else:
                runtime_claim_ids.add(match.group(1))
    index = ROOT / "references" / "curated" / "INDEX.md"
    if index.is_file():
        index_claim_ids = set(
            re.findall(r"`(claim-[0-9a-f]{16})`", index.read_text(encoding="utf-8"))
        )
        if index_claim_ids != runtime_claim_ids:
            ERRORS.append("curated INDEX claim IDs do not match topic files")
    if runtime_only:
        return

    registry_path = ROOT / "knowledge-management" / "SOURCE_REGISTRY.json"
    try:
        registry = load_registry(registry_path)
    except (KnowledgeSchemaError, OSError) as exc:
        ERRORS.append(f"knowledge registry validation failed: {exc}")
        return
    sources = {item["source_id"]: item for item in registry["sources"]}
    for source_id in sources:
        if not (ROOT / "knowledge-management" / "source-cards" / f"{source_id}.md").is_file():
            ERRORS.append(f"registered source is missing source card: {source_id}")
    for proposal_path in sorted(
        (ROOT / "knowledge-management" / "proposals").glob("*-proposal.md")
    ):
        try:
            proposal = parse_proposal(proposal_path)
        except KnowledgeIntakeError as exc:
            ERRORS.append(f"proposal validation failed for {proposal_path.name}: {exc}")
            continue
        if proposal["source_id"] not in sources:
            ERRORS.append(f"proposal references unregistered source: {proposal['source_id']}")

    store_path = ROOT / "knowledge-management" / "CURATED_CLAIMS.json"
    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        ERRORS.append(f"cannot load CURATED_CLAIMS.json: {exc}")
        return
    if set(store) != {"schema_version", "claims"} or store.get("schema_version") != 1:
        ERRORS.append("CURATED_CLAIMS.json must use schema_version 1 and claims only")
        return
    if not isinstance(store.get("claims"), list):
        ERRORS.append("CURATED_CLAIMS.json claims must be a list")
        return
    store_ids: set[str] = set()
    required_fields = {
        "claim_id",
        "canonical_claim",
        "practical_meaning",
        "evidence",
        "applicable_when",
        "limits",
        "risks",
        "sources",
        "destination",
        "last_reviewed_at",
        "review_after",
    }
    for claim in store["claims"]:
        if not isinstance(claim, dict) or set(claim) != required_fields:
            ERRORS.append("curated store claim has invalid fields")
            continue
        claim_id = claim["claim_id"]
        if claim_id != stable_claim_id(claim["canonical_claim"]):
            ERRORS.append(f"curated store claim has unstable ID: {claim_id}")
        if claim_id in store_ids:
            ERRORS.append(f"duplicate claim in curated store: {claim_id}")
        store_ids.add(claim_id)
        for provenance in claim["sources"]:
            source_id = provenance.get("source_id") if isinstance(provenance, dict) else None
            if source_id not in sources:
                ERRORS.append(f"curated claim references unknown source: {source_id}")
            elif sources[source_id]["status"] == "rejected":
                ERRORS.append(f"rejected source appears in curated runtime: {source_id}")
    if store_ids != runtime_claim_ids:
        ERRORS.append("CURATED_CLAIMS.json does not match curated runtime claim IDs")


def validate_chatgpt_pack(runtime_only: bool) -> None:
    if runtime_only:
        return
    output = ROOT / "chatgpt-project" / "generated-knowledge"
    try:
        expected_bodies = build_knowledge_bodies(ROOT)
    except (OSError, ValueError) as exc:
        ERRORS.append(f"cannot build expected ChatGPT pack: {exc}")
        return
    for filename, expected in expected_bodies.items():
        path = output / filename
        if not path.is_file():
            continue
        actual = path.read_text(encoding="utf-8").rstrip()
        if actual != expected.rstrip():
            ERRORS.append(f"generated ChatGPT pack is stale: {filename}")
    info_path = output / "KNOWLEDGE_PACK_INFO.json"
    if not info_path.is_file():
        return
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
        expected_info = pack_metadata(ROOT, info.get("built_at"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        ERRORS.append(f"invalid KNOWLEDGE_PACK_INFO.json: {exc}")
        return
    if info != expected_info:
        ERRORS.append("KNOWLEDGE_PACK_INFO.json does not match current inputs")


def validate_markdown_links() -> None:
    link_pattern = re.compile(r"\]\(([^)]+)\)")
    for markdown in ROOT.rglob("*.md"):
        text = markdown.read_text(encoding="utf-8")
        for raw_target in link_pattern.findall(text):
            target = raw_target.strip().split("#", 1)[0]
            if not target or re.match(r"^(?:https?://|mailto:)", target):
                continue
            if not (markdown.parent / target).resolve().exists():
                ERRORS.append(f"broken local link in {markdown.relative_to(ROOT)}: {raw_target}")


def validate_placeholders() -> None:
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix.lower() not in {".md", ".yaml", ".yml", ".py"}:
            continue
        if "[" + "TODO" in path.read_text(encoding="utf-8"):
            ERRORS.append(f"template placeholder in {path.relative_to(ROOT)}")


def validate_upstream_lock(runtime_only: bool) -> None:
    if runtime_only:
        return
    path = ROOT / "UPSTREAM_LOCK.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        ERRORS.append(f"UPSTREAM_LOCK.json is invalid JSON: {exc}")
        return
    if set(data) != {"schema_version", "sources"} or data.get("schema_version") != 1:
        ERRORS.append("UPSTREAM_LOCK.json must use schema_version 1 and sources only")
    sources = data.get("sources")
    if not isinstance(sources, list) or len(sources) != 2:
        ERRORS.append("UPSTREAM_LOCK.json must contain exactly two sources")
        return
    repositories = {item.get("repository") for item in sources if isinstance(item, dict)}
    expected_repositories = {
        "https://github.com/powerycy/goutoujunshi.git",
        "https://github.com/liuzitong901/goutoujunshi-warm-fork.git",
    }
    if repositories != expected_repositories:
        ERRORS.append("UPSTREAM_LOCK.json must lock original and warm-fork repositories")
    for item in sources:
        if not isinstance(item, dict):
            ERRORS.append("UPSTREAM_LOCK.json source must be an object")
            continue
        allowed = {"repository", "commit", "copied_at"}
        selector = {key for key in ("branch", "tag") if key in item}
        if len(selector) != 1 or set(item) != allowed | selector:
            ERRORS.append("each source must contain repository, commit, copied_at, and one branch/tag")
        if not re.fullmatch(r"[0-9a-f]{40}", str(item.get("commit", ""))):
            ERRORS.append(f"UPSTREAM_LOCK.json has invalid commit for {item.get('repository')}")
        if not str(item.get("repository", "")).startswith("https://github.com/"):
            ERRORS.append(f"UPSTREAM_LOCK.json has invalid repository: {item.get('repository')}")
        try:
            normalize_iso8601(str(item.get("copied_at", "")), field_name="copied_at")
        except ValueError as exc:
            ERRORS.append(f"UPSTREAM_LOCK.json invalid copied_at: {exc}")
    serialized = json.dumps(data, ensure_ascii=False)
    if re.search(r"[A-Za-z]:\\\\|(?:^|[\"'])/(?:Users|home)/", serialized):
        ERRORS.append("UPSTREAM_LOCK.json must not contain local absolute paths")


def validate_evals(runtime_only: bool) -> None:
    if runtime_only or not (ROOT / "scripts" / "run_contract_evals.py").is_file():
        return
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = run_contract_evals()
    if result:
        ERRORS.append("contract eval validation failed: " + output.getvalue().strip())
    else:
        print("contract eval: PASS")


def validate_model_eval_definitions(runtime_only: bool) -> None:
    if runtime_only or not (ROOT / "scripts" / "run_model_evals.py").is_file():
        return
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream):
        result = validate_model_eval_command(argparse.Namespace())
    output = stream.getvalue().strip()
    if result:
        ERRORS.append("model eval definition validation failed: " + output)
    elif "behavioral evaluation NOT RUN" not in output:
        ERRORS.append("model eval validator must explicitly report behavioral evaluation NOT RUN")


def validate_model_eval_artifacts(runtime_only: bool) -> None:
    """Statically validate saved run schemas without executing a provider."""
    if runtime_only:
        return
    results_root = ROOT / "model_evals" / "results"
    if not results_root.is_dir():
        return
    for version_dir in sorted(path for path in results_root.iterdir() if path.is_dir()):
        if not re.fullmatch(r"v\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", version_dir.name):
            ERRORS.append(f"invalid model eval version directory: {version_dir.name}")
            continue
        for profile_dir in sorted(path for path in version_dir.iterdir() if path.is_dir()):
            if profile_dir.name not in RUNTIME_PROFILES:
                ERRORS.append(f"invalid model eval runtime profile: {profile_dir.name}")
                continue
            for run_dir in sorted(path for path in profile_dir.iterdir() if path.is_dir()):
                try:
                    validate_result_artifacts(run_dir)
                except (ModelEvalError, OSError) as exc:
                    ERRORS.append(f"model eval artifact validation failed: {exc}")


def validate_test_suites(runtime_only: bool) -> None:
    if runtime_only:
        return
    for label, directory in (("unit tests", "unit"), ("integration tests", "integration")):
        suite = unittest.defaultTestLoader.discover(
            str(ROOT / "tests" / directory), top_level_dir=str(ROOT)
        )
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
        if not result.wasSuccessful():
            ERRORS.append(f"{label} failed: {stream.getvalue().strip()}")
        else:
            print(f"{label}: PASS ({result.testsRun})")


def validate_policy_parity(runtime_only: bool) -> None:
    core = require("shared/CORE_POLICY.md")
    if not core.is_file():
        return
    content = core.read_text(encoding="utf-8")
    for marker in (
        "fact != hypothesis",
        "object isolation",
        "green / gray / yellow / red",
        "continuation ownership",
        "actual send learning",
        "user growth != partner response",
        "stop conditions",
    ):
        if marker not in content:
            ERRORS.append(f"shared/CORE_POLICY.md missing parity rule: {marker}")
    for path in ("SKILL.md", "chatgpt-project/PROJECT_INSTRUCTIONS.md"):
        target = ROOT / path
        if target.is_file() and "CORE_POLICY.md" not in target.read_text(encoding="utf-8"):
            ERRORS.append(f"{path} must reference CORE_POLICY.md")
    if not runtime_only:
        checkpoint = ROOT / "sync" / "CHECKPOINT_TEMPLATE.md"
        if checkpoint.is_file():
            text = checkpoint.read_text(encoding="utf-8")
            for heading in ("## confirmed", "## hypothesis", "## recommendation", "## unknown"):
                if heading not in text:
                    ERRORS.append(f"checkpoint missing structure: {heading}")


def main() -> int:
    supported = {"--runtime", "--convergence-only"}
    unexpected = [arg for arg in sys.argv[1:] if arg not in supported]
    if unexpected:
        print(f"ERROR: unsupported arguments: {' '.join(unexpected)}")
        return 2
    if "--runtime" in sys.argv[1:] and "--convergence-only" in sys.argv[1:]:
        print("ERROR: --runtime and --convergence-only are mutually exclusive")
        return 2
    runtime_only = "--runtime" in sys.argv[1:]
    convergence_only = "--convergence-only" in sys.argv[1:]
    validate_frontmatter()
    validate_repository_convergence(runtime_only)
    if convergence_only:
        if ERRORS:
            for error in ERRORS:
                print(f"ERROR: {error}")
            return 1
        print("relationship-compass repository convergence validation passed")
        return 0
    validate_skill_budget()
    validate_inventory(runtime_only)
    validate_routes_and_invariants()
    validate_runtime_boundaries()
    validate_curated_knowledge(runtime_only)
    validate_chatgpt_pack(runtime_only)
    validate_markdown_links()
    validate_placeholders()
    validate_upstream_lock(runtime_only)
    validate_policy_parity(runtime_only)
    validate_evals(runtime_only)
    validate_model_eval_definitions(runtime_only)
    validate_model_eval_artifacts(runtime_only)
    validate_test_suites(runtime_only)
    if ERRORS:
        for error in ERRORS:
            print(f"ERROR: {error}")
        return 1
    print("relationship-compass validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
