#!/usr/bin/env python3
"""
Package the generated OpenAI quote-image corpus into independent tar archives
suitable for uploading to ChatGPT.

Each archive contains complete quote item directories, including:

    quote.txt
    quote_analysis.json
    quote_analysis_record.json
    generation_prompt.txt
    match_info.json
    image_01.png
    response_metadata.json
    manifest.json

Only items containing image_01.png are included.

Items are ordered by corpus ordinal from corpus_index.json.

Each archive also contains its own chunk_manifest.json describing exactly
which corpus items it contains.

Defaults:

    source:
      /disks/disk1/etc/mrsMThatcher/openai_generated_quote_images

    output:
      /disks/disk1/etc/mrsMThatcher/openai_corpus_upload_chunks

    target archive size:
      120 MiB

This deliberately targets well below ChatGPT's per-file upload ceiling.
"""

from __future__ import annotations

import argparse
import io
import json
import tarfile
from pathlib import Path
from typing import Any


ROOT = Path("/disks/disk1/etc/mrsMThatcher")

DEFAULT_SOURCE = ROOT / "openai_generated_quote_images"
DEFAULT_OUTPUT = ROOT / "openai_corpus_upload_chunks"

DEFAULT_TARGET_MIB = 120


def directory_size(path: Path) -> int:
    """Return the directory size."""
    total = 0

    for entry in path.rglob("*"):
        if entry.is_file():
            total += entry.stat().st_size

    return total


def human_size(value: int) -> str:
    """Return the human size."""
    units = ["B", "KiB", "MiB", "GiB"]
    size = float(value)

    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024

    return f"{value} B"


def load_corpus_index(path: Path) -> dict[str, Any]:
    """Load corpus index."""
    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise RuntimeError("corpus_index.json top level is not an object")

    entries = data.get("entries")

    if not isinstance(entries, list):
        raise RuntimeError("corpus_index.json has no valid entries list")

    return data


def add_bytes(
    archive: tarfile.TarFile,
    arcname: str,
    content: bytes,
) -> None:
    """Add bytes."""
    info = tarfile.TarInfo(name=arcname)
    info.size = len(content)
    info.mtime = 0

    archive.addfile(
        info,
        io.BytesIO(content),
    )


def main() -> int:
    """Run the command-line entry point."""
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )

    parser.add_argument(
        "--target-mib",
        type=int,
        default=DEFAULT_TARGET_MIB,
        help="Approximate target size of each tar archive",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing output directory",
    )

    args = parser.parse_args()

    if args.target_mib < 10:
        parser.error("--target-mib must be at least 10")

    source = args.source.resolve()
    output = args.output.resolve()

    corpus_index_path = source / "corpus_index.json"
    items_root = source / "items"

    if not corpus_index_path.is_file():
        raise SystemExit(
            f"ERROR: missing {corpus_index_path}"
        )

    if not items_root.is_dir():
        raise SystemExit(
            f"ERROR: missing {items_root}"
        )

    if output.exists():
        if not args.force:
            raise SystemExit(
                f"ERROR: output directory already exists: {output}\n"
                "Use --force to replace it."
            )

        for entry in output.iterdir():
            if entry.is_file() or entry.is_symlink():
                entry.unlink()
            elif entry.is_dir():
                import shutil
                shutil.rmtree(entry)
    else:
        output.mkdir(parents=True)

    corpus_index = load_corpus_index(
        corpus_index_path
    )

    raw_entries = corpus_index["entries"]

    completed: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    print("Scanning completed corpus items...", flush=True)

    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue

        quote_hash = str(
            entry.get("quote_hash", "")
        ).strip()

        if not quote_hash:
            continue

        item_dir = items_root / quote_hash
        image_path = item_dir / "image_01.png"

        if not image_path.is_file():
            missing.append(entry)
            continue

        size = directory_size(item_dir)

        completed.append(
            {
                **entry,
                "_item_dir": item_dir,
                "_size_bytes": size,
            }
        )

    print()
    print(f"Unique corpus entries: {len(raw_entries)}")
    print(f"Completed images:       {len(completed)}")
    print(f"Missing images:         {len(missing)}")

    target_bytes = (
        args.target_mib * 1024 * 1024
    )

    # Group complete item directories without ever splitting one quote item
    # across archives.
    groups: list[list[dict[str, Any]]] = []
    current_group: list[dict[str, Any]] = []
    current_size = 0

    for entry in completed:
        item_size = int(entry["_size_bytes"])

        if (
            current_group
            and current_size + item_size > target_bytes
        ):
            groups.append(current_group)
            current_group = []
            current_size = 0

        current_group.append(entry)
        current_size += item_size

    if current_group:
        groups.append(current_group)

    print(f"Planned archives:       {len(groups)}")
    print(f"Target size:            {args.target_mib} MiB")
    print()

    global_manifest: dict[str, Any] = {
        "source_directory": str(source),
        "target_size_mib": args.target_mib,
        "unique_corpus_entries": len(raw_entries),
        "completed_images": len(completed),
        "missing_images": len(missing),
        "archives": [],
        "missing_entries": [
            {
                key: value
                for key, value in entry.items()
                if not key.startswith("_")
            }
            for entry in missing
        ],
    }

    for chunk_number, group in enumerate(
        groups,
        start=1,
    ):
        first_ordinal = int(
            group[0]["corpus_ordinal"]
        )

        last_ordinal = int(
            group[-1]["corpus_ordinal"]
        )

        filename = (
            f"openai_corpus_"
            f"{chunk_number:02d}_"
            f"ordinals_{first_ordinal:03d}-"
            f"{last_ordinal:03d}.tar"
        )

        archive_path = output / filename

        clean_entries = [
            {
                key: value
                for key, value in entry.items()
                if not key.startswith("_")
            }
            for entry in group
        ]

        chunk_manifest = {
            "chunk_number": chunk_number,
            "archive_filename": filename,
            "first_corpus_ordinal": first_ordinal,
            "last_corpus_ordinal": last_ordinal,
            "item_count": len(group),
            "entries": clean_entries,
        }

        print(
            f"[{chunk_number}/{len(groups)}] "
            f"{filename}"
        )

        with tarfile.open(
            archive_path,
            mode="w",
        ) as archive:
            manifest_bytes = (
                json.dumps(
                    chunk_manifest,
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")

            add_bytes(
                archive,
                "chunk_manifest.json",
                manifest_bytes,
            )

            for entry in group:
                item_dir = entry["_item_dir"]
                quote_hash = entry["quote_hash"]

                archive.add(
                    item_dir,
                    arcname=(
                        "openai_generated_quote_images/"
                        f"items/{quote_hash}"
                    ),
                    recursive=True,
                )

        archive_size = archive_path.stat().st_size

        print(
            f"    {len(group)} items, "
            f"{human_size(archive_size)}"
        )

        global_manifest["archives"].append(
            {
                "filename": filename,
                "chunk_number": chunk_number,
                "item_count": len(group),
                "first_corpus_ordinal": first_ordinal,
                "last_corpus_ordinal": last_ordinal,
                "size_bytes": archive_size,
            }
        )

    global_manifest_path = (
        output / "upload_manifest.json"
    )

    global_manifest_path.write_text(
        json.dumps(
            global_manifest,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("Done.")
    print(f"Output: {output}")
    print()
    print("Archives:")

    total_size = 0

    for archive in global_manifest["archives"]:
        total_size += archive["size_bytes"]

        print(
            f"  {archive['filename']}: "
            f"{archive['item_count']} items, "
            f"{human_size(archive['size_bytes'])}"
        )

    print()
    print(
        f"Total archive size: {human_size(total_size)}"
    )
    print(
        f"Manifest: {global_manifest_path}"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
