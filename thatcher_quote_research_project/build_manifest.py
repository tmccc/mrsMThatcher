#!/usr/bin/env python3
"""Build the frozen quote manifest and deliberately varied pilot batch."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import OrderedDict
from pathlib import Path


EXPECTED_SOURCE_SHA256 = (
    "4a3eec7b9233f74c58ddfe8a3484bd32d72d6fe658ba1882f0dfd6b3b8479496"
)
BUILDER_VERSION = "1.0.0"
CANONICAL_SOURCE_NAME = "mrsMThatcher.txt"
PILOT_LINES = (1, 5, 35, 65, 69, 77, 120, 416, 556, 632)


def sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 bytes."""
    return hashlib.sha256(value).hexdigest()


def main() -> None:
    """Run the command-line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()

    raw = args.source.read_bytes()
    source_hash = sha256_bytes(raw)
    if source_hash != EXPECTED_SOURCE_SHA256:
        raise SystemExit(
            f"source SHA-256 mismatch: expected {EXPECTED_SOURCE_SHA256}, got {source_hash}"
        )
    if not raw.endswith(b"\n"):
        raise SystemExit("source must end in LF")

    text = raw.decode("ascii")
    lines = text.splitlines()
    if len(lines) != 633 or any(not line for line in lines):
        raise SystemExit("expected exactly 633 non-empty quote lines")

    records: OrderedDict[str, dict] = OrderedDict()
    occurrences: list[dict] = []
    for line_number, quote_text in enumerate(lines, start=1):
        quote_hash = sha256_bytes(quote_text.encode("utf-8"))
        occurrence = {"line_number": line_number}
        occurrences.append({**occurrence, "quote_id": quote_hash})
        if quote_hash not in records:
            records[quote_hash] = {
                "quote_id": quote_hash,
                "quote_hash": quote_hash,
                "quote_text": quote_text,
                "source_occurrences": [occurrence],
                "research_status": "pending",
            }
        else:
            if records[quote_hash]["quote_text"] != quote_text:
                raise SystemExit(f"SHA-256 collision at line {line_number}")
            records[quote_hash]["source_occurrences"].append(occurrence)

    manifest = {
        "manifest_version": 1,
        "builder_version": BUILDER_VERSION,
        "source_file": {
            "name": CANONICAL_SOURCE_NAME,
            "sha256": source_hash,
            "encoding": "US-ASCII",
            "line_endings": "LF",
            "final_newline": True,
            "source_occurrence_count": len(lines),
            "unique_quote_count": len(records),
        },
        "quote_id_algorithm": "sha256(exact UTF-8 quote_text without line ending)",
        "records": list(records.values()),
        "occurrences": occurrences,
    }

    by_line = {
        occurrence["line_number"]: record
        for record in records.values()
        for occurrence in record["source_occurrences"]
    }
    pilot_records = [by_line[line_number] for line_number in PILOT_LINES]
    pilot = {
        "batch_schema_version": 1,
        "manifest_version": manifest["manifest_version"],
        "builder_version": BUILDER_VERSION,
        "batch_id": "pilot_001",
        "purpose": "Schema and research-method pilot; not production import",
        "source_file_sha256": source_hash,
        "record_count": len(pilot_records),
        "records": pilot_records,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    pilot["manifest_sha256"] = sha256_bytes(manifest_bytes)
    (args.output_dir / "quote_manifest.json").write_bytes(manifest_bytes)
    (args.output_dir / "pilot_batch_001.json").write_text(
        json.dumps(pilot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
