#!/usr/bin/env python3
"""Run one native unittest discovery suite and emit a durable final result marker."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unittest
from functools import partial
from pathlib import Path


RESULT_PREFIX = "__RELATIONSHIP_COMPASS_TEST_RESULT__"
ACTIVITY_PREFIX = "__RELATIONSHIP_COMPASS_TEST_ACTIVE__"


class _ActivityResult(unittest.TextTestResult):
    """Emit and persist the active test without depending on captured suite output."""

    def __init__(
        self,
        *args: object,
        activity_file: Path | None = None,
        suite_name: str = "",
        **kwargs: object,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.activity_file = activity_file
        self.suite_name = suite_name

    def startTest(self, test: unittest.case.TestCase) -> None:
        test_id = test.id()
        if self.activity_file is not None:
            _write_activity(self.activity_file, self.suite_name, test_id)
        os.write(2, f"{ACTIVITY_PREFIX} {test_id}\n".encode("utf-8", "replace"))
        super().startTest(test)


def _write_activity(activity_file: Path, suite_name: str, test_id: str) -> None:
    """Atomically replace the tiny activity marker; diagnostics must not break tests."""
    payload = {
        "suite": suite_name,
        "test_id": test_id,
        "started_at": time_now_iso8601(),
    }
    temporary = activity_file.with_name(f".{activity_file.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(activity_file)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def time_now_iso8601() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-directory", required=True)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--top-level-directory", default=".")
    parser.add_argument("--activity-file", type=Path)
    parser.add_argument("--suite", default="")
    args = parser.parse_args(argv)
    suite = unittest.defaultTestLoader.discover(
        args.start_directory, pattern=args.pattern, top_level_dir=args.top_level_directory
    )
    result_class = partial(
        _ActivityResult,
        activity_file=args.activity_file,
        suite_name=args.suite,
    )
    result = unittest.TextTestRunner(verbosity=2, resultclass=result_class).run(suite)
    os.write(
        1,
        (
            f"\n{RESULT_PREFIX} tests_run={result.testsRun} "
            f"failures={len(result.failures)} errors={len(result.errors)}\n"
        ).encode("utf-8"),
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
