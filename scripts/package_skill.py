"""Create a portable UTF-8 ZIP archive of the skill repository."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[1]
SKIPPED_DIRECTORIES = {".git", ".idea", ".pytest_cache", "__pycache__", ".work"}
SKIPPED_FILES = {
    ".DS_Store",
    ".env",
    ".env.local",
    "provider_profiles.local.yaml",
    "Thumbs.db",
}


def is_forbidden_package_entry(entry_name: str) -> bool:
    """Return whether a ZIP member is a local/compiled artifact that must never ship."""
    parts = entry_name.replace("\\", "/").split("/")
    basename = parts[-1]
    return (
        basename in SKIPPED_FILES
        or basename.startswith(".env.")
        or basename.endswith(".local.yaml")
        or basename.endswith(".local.yml")
        or basename.endswith(".pyc")
        or "__pycache__" in parts
        or ".work" in parts
        or ".idea" in parts
    )


def iter_package_files(source_root: Path, output_path: Path) -> list[Path]:
    """Return deterministic package entries without generated or VCS artifacts."""
    root = source_root.resolve()
    archive = output_path.resolve()
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.resolve() != archive
        and path.name not in SKIPPED_FILES
        and not path.name.startswith(".env.")
        and not path.name.endswith(".local.yaml")
        and not path.name.endswith(".local.yml")
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
    try:
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
            for path in iter_package_files(root, output):
                entry_name = path.relative_to(root).as_posix()
                archive.write(path, entry_name)
                entries.append(entry_name)
        validate_package(output)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return entries


def validate_package(output_path: Path | str) -> tuple[int, int]:
    """Fail closed when a generated release ZIP contains local or compiled files."""
    output = Path(output_path).expanduser().resolve()
    with ZipFile(output, "r") as archive:
        names = archive.namelist()
    forbidden = [name for name in names if is_forbidden_package_entry(name)]
    if forbidden:
        raise ValueError("安全打包校验失败：发现禁止条目：" + ", ".join(forbidden))
    unicode_entries = sum(any(ord(character) > 127 for character in name) for name in names)
    return len(names), unicode_entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Package relationship-compass as a UTF-8 ZIP archive")
    parser.add_argument("--output", required=True, type=Path, help="destination ZIP path")
    parser.add_argument("--source-root", type=Path, default=ROOT, help="source directory to package")
    args = parser.parse_args(argv)
    output = args.output.expanduser()
    try:
        entries = build_zip(args.source_root, output)
        _, unicode_entries = validate_package(output)
    except (BadZipFile, OSError, ValueError) as exc:
        output.unlink(missing_ok=True)
        print(f"安全打包失败：{exc}")
        return 1
    print("安全打包完成")
    print(f"文件数：{len(entries)}")
    print("已排除：.env、.env.*、provider_profiles.local.yaml、*.local.yaml、*.local.yml、.idea、.work、__pycache__、*.pyc、.DS_Store、Thumbs.db")
    print("Secret / Local Metadata 检查：PASS")
    print(f"Unicode 文件名：PASS（{unicode_entries} 条）")
    print(f"输出：{_display_output_path(output)}")
    return 0


def _display_output_path(output: Path) -> str:
    """Keep package summaries portable and free of workstation-specific paths."""
    try:
        return output.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return output.name


if __name__ == "__main__":
    raise SystemExit(main())
