#!/usr/bin/env python3
"""Controlled local fixture that leaves a detached child holding inherited pipes."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _write_json(path: Path, payload: dict[str, int]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def _wait_for_release(release_file: Path) -> None:
    while not release_file.exists():
        time.sleep(0.02)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--pid-file", type=Path)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--release-file", type=Path)
    args = parser.parse_args(argv)
    if args.child:
        time.sleep(30)
        return 0
    if not all((args.pid_file, args.ready_file, args.release_file)):
        parser.error("parent mode requires --pid-file, --ready-file, and --release-file")
    child_kwargs: dict[str, object] = {}
    if os.name == "nt":
        child_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        child_kwargs["start_new_session"] = True
    child = subprocess.Popen([sys.executable, __file__, "--child"], **child_kwargs)
    payload = {"child_pid": child.pid}
    _write_json(args.pid_file, payload)
    _write_json(args.ready_file, payload)
    _wait_for_release(args.release_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
