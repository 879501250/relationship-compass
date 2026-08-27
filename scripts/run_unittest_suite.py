#!/usr/bin/env python3
"""Run one native unittest discovery suite and emit a durable final result marker."""

from __future__ import annotations

import argparse
import os
import sys
import unittest


RESULT_PREFIX = "__RELATIONSHIP_COMPASS_TEST_RESULT__"
ACTIVITY_PREFIX = "__RELATIONSHIP_COMPASS_TEST_ACTIVE__"


class _ActivityResult(unittest.TextTestResult):
    """Emit the active test through the OS pipe even if a test replaces sys.stderr."""

    def startTest(self, test: unittest.case.TestCase) -> None:
        os.write(2, f"{ACTIVITY_PREFIX} {test.id()}\n".encode("utf-8", "replace"))
        super().startTest(test)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-directory", required=True)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--top-level-directory", default=".")
    args = parser.parse_args(argv)
    suite = unittest.defaultTestLoader.discover(
        args.start_directory, pattern=args.pattern, top_level_dir=args.top_level_directory
    )
    result = unittest.TextTestRunner(verbosity=2, resultclass=_ActivityResult).run(suite)
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
