"""Parse human-friendly case selectors before an execution plan is built."""

from __future__ import annotations

import re
from collections.abc import Sequence


class CaseSelectionError(ValueError):
    """Raised when a selector cannot resolve to a non-empty unique case set."""


def parse_case_selection(expression: str, case_ids: Sequence[str]) -> list[str]:
    """Resolve ``all``, positions, case IDs, and comma-separated ranges.

    Numeric positions are one based.  Case-ID ranges use ``first..last`` so
    IDs containing hyphens remain unambiguous.  Output always follows the eval
    definition order, which gives reproducible prepared bundles and execution.
    """
    normalized = expression.strip()
    if not normalized:
        raise CaseSelectionError("Case 选择不能为空。请输入 all 或一个或多个 Case。")
    if not case_ids:
        raise CaseSelectionError("当前 Eval 不包含可选择的 Case。")

    known = {case_id: index for index, case_id in enumerate(case_ids)}
    selected: set[int] = set()
    tokens = [token.strip() for token in normalized.split(",")]
    if any(not token for token in tokens):
        raise CaseSelectionError("Case 选择中包含空项目，请删除多余逗号后重试。")
    if "all" in {token.lower() for token in tokens}:
        if len(tokens) != 1:
            raise CaseSelectionError("all 必须单独使用，不能与其他 Case 组合。")
        return list(case_ids)

    for token in tokens:
        indices = _resolve_token(token, known, len(case_ids))
        duplicate = next((index for index in indices if index in selected), None)
        if duplicate is not None:
            raise CaseSelectionError(
                f"Case {case_ids[duplicate]!r} 被重复选择。"
            )
        selected.update(indices)

    if not selected:
        raise CaseSelectionError("Case 选择未解析出任何可运行的 Case。")
    return [case_id for index, case_id in enumerate(case_ids) if index in selected]


def _resolve_token(token: str, known: dict[str, int], count: int) -> list[int]:
    if token in known:
        return [known[token]]
    if ".." in token:
        start_text, end_text = (part.strip() for part in token.split("..", 1))
        return _resolve_range(start_text, end_text, known, count, token)
    match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", token)
    if match:
        return _resolve_range(match.group(1), match.group(2), known, count, token)
    return [_resolve_endpoint(token, known, count)]


def _resolve_range(
    start_text: str,
    end_text: str,
    known: dict[str, int],
    count: int,
    token: str,
) -> list[int]:
    if not start_text or not end_text:
        raise CaseSelectionError(f"Case 范围 {token!r} 需要起始编号和结束编号。")
    start = _resolve_endpoint(start_text, known, count)
    end = _resolve_endpoint(end_text, known, count)
    if start > end:
        raise CaseSelectionError(
            f"Case 范围无效：起始编号不能大于结束编号（输入：{token!r}）。例如：1-10 或 1,3,5-8。"
        )
    return list(range(start, end + 1))


def _resolve_endpoint(token: str, known: dict[str, int], count: int) -> int:
    if token in known:
        return known[token]
    if token.isdigit():
        position = int(token)
        if 1 <= position <= count:
            return position - 1
        raise CaseSelectionError(f"Case 位置 {position} 超出范围；请输入 1 到 {count}。")
    raise CaseSelectionError(
        f"未知 Case：{token!r}。请输入 Case ID 或 1 到 {count} 的位置。"
    )
