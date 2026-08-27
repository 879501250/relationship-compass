"""Create a portable UTF-8 ZIP archive of the skill repository."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
SKIPPED_DIRECTORIES = {".git", ".pytest_cache", "__pycache__"}


def iter_package_files(source_root: Path, output_path: Path) -> list[Path]:
    """Return deterministic package entries without generated or VCS artifacts."""
    root = source_root.resolve()
    archive = output_path.resolve()
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.resolve() != archive
        and not any(part in SKIPPED_DIRECTORIES for part in path.relative_to(root).parts)
    ]


def build_zip(source_root: Path, output_path: Path) -> list[str]:
    """Write a ZIP whose entry names preserve Unicode and use POSIX separators."""
    root = source_root.resolve()
    if not root.is_dir():
        raise ValueError(f"Source root does not exist: {root}")
    output = output_path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    entries: list[str] = []
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in iter_package_files(root, output):
            entry_name = path.relative_to(root).as_posix()
            archive.write(path, entry_name)
            entries.append(entry_name)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package relationship-compass as a UTF-8 ZIP archive")
    parser.add_argument("--output", required=True, type=Path, help="destination ZIP path")
    parser.add_argument("--source-root", type=Path, default=ROOT, help="source directory to package")
    args = parser.parse_args(argv)
    entries = build_zip(args.source_root, args.output)
    print(f"Packaged {len(entries)} files: {args.output.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
