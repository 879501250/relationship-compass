#!/usr/bin/env python3
"""使用分阶段中文终端输出运行仓库测试。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval_console.test_runner import TerminalTestReporter, TestSuiteRequest, TestSuiteRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", choices=("unit", "integration", "contract"), action="append",
        help="只运行指定套件；可重复使用",
    )
    parser.add_argument("--skip-contract", action="store_true", help="兼容旧用法：跳过 Contract Eval")
    return parser


def request_from_args(args: argparse.Namespace) -> TestSuiteRequest:
    selected = set(args.suite or ("unit", "integration", "contract"))
    if args.skip_contract:
        selected.discard("contract")
    return TestSuiteRequest(
        unit="unit" in selected,
        integration="integration" in selected,
        contract="contract" in selected,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    reporter = TerminalTestReporter()
    print("Relationship Compass — 自动化测试\n" + "-" * 36, flush=True)
    try:
        result = TestSuiteRunner(ROOT).run(request_from_args(args), on_event=reporter.event)
    except ValueError as exc:
        print(f"无法运行测试：{exc}")
        return 2
    reporter.summary(result)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
