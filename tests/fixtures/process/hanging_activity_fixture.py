#!/usr/bin/env python3
"""Controlled activity-file fixture for TestSuiteRunner timeout diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path


TEST_ID = "fixtures.activity.HangTests.test_hang"


def _write_json(path: Path, payload: dict[str, str]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activity-file", required=True, type=Path)
    parser.add_argument("--suite", required=True)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--release-file", required=True, type=Path)
    args = parser.parse_args(argv)
    _write_json(
        args.activity_file,
        {
            "suite": args.suite,
            "test_id": TEST_ID,
            "started_at": "fixture",
        },
    )
    _write_json(args.ready_file, {"test_id": TEST_ID})
    while not args.release_file.exists():
        time.sleep(0.02)
    time.sleep(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
