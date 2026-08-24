#!/usr/bin/env python3
"""Run fast unit, integration, and contract checks with clear summaries."""

from __future__ import annotations

import argparse
import sys
import time
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

from run_contract_evals import main as run_contract_evals


ROOT = Path(__file__).resolve().parents[1]


def run_unittest_group(label: str, directory: str) -> bool:
    started = time.perf_counter()
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests" / directory), top_level_dir=str(ROOT)
    )
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    elapsed = time.perf_counter() - started
    status = "PASS" if result.wasSuccessful() else "FAIL"
    print(
        f"[{label}] {status}: {result.testsRun} tests, "
        f"failures={len(result.failures)}, errors={len(result.errors)}, {elapsed:.2f}s"
    )
    return result.wasSuccessful()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-contract", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started = time.perf_counter()
    unit_ok = run_unittest_group("unit tests", "unit")
    integration_ok = run_unittest_group("integration tests", "integration")
    contract_ok = True
    if not args.skip_contract:
        contract_started = time.perf_counter()
        contract_ok = run_contract_evals() == 0
        status = "PASS" if contract_ok else "FAIL"
        print(f"[contract eval] {status}: {time.perf_counter() - contract_started:.2f}s")
    print(f"[total] {time.perf_counter() - started:.2f}s")
    return 0 if unit_ok and integration_ok and contract_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
