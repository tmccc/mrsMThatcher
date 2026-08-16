#!/usr/bin/env python3
"""Run the preserved 332-case generic-context MTF assessment locally.

This is a private, advisory-only wrapper around the earlier reviewed
assessment implementation.  It is inert unless ``--execute`` is supplied.
The preserved 332 targets are taken from the earlier assessment artefact, not
re-derived from current public rendering (where the generic sentence is now
intentionally suppressed).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import socket
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

from historical_context_project_root import resolve_project_root

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parent
OLD_RUNNER_NAME = "assess_generic_mtf_context.py"
OLD_RENDER_NAME = "historical_context_generic_mtf_render_review.txt"
OLD_ASSESSMENT_NAME = "local_mtf_context_remediation_assessment.json"
EXPECTED_HASHES = {
    "old_runner":
        "20bb4f15ea6833465f64b9e74758c0351d2fa95a9cfe3e0627c47cdc50e97079",
    "old_render":
        "12942c85ef541e79cd4871a92b6eeed1bd5979472db523d706fa61372755aad7",
    "old_assessment":
        "4fd87c65ea93e5391aa6b55bb25d5cb0258e3d65425088583b9101e166f5f64b",
}
MAXIMUM_DOCUMENT_BYTES = 5 * 1024 * 1024


def sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest of exact bytes."""
    return hashlib.sha256(value).hexdigest()


def stable_bytes(path: Path) -> bytes:
    """Read a file only if its identity and size remain stable throughout."""
    before = path.stat()
    value = path.read_bytes()
    after = path.stat()
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(value) != after.st_size
    ):
        raise RuntimeError(f"input changed while being read: {path.name}")
    return value


def verify_sha256(path: Path, expected: str) -> bytes:
    """Return stable file bytes after verifying the reviewed digest."""
    value = stable_bytes(path)
    actual = sha256_bytes(value)
    if actual != expected:
        raise RuntimeError(
            f"reviewed input hash mismatch for {path.name}: {actual}"
        )
    return value


def prohibit_network() -> None:
    """Make accidental DNS/HTTP access a hard local failure."""
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("332-case local assessment attempted network access")

    socket.create_connection = forbidden  # type: ignore[assignment]
    socket.socket.connect = forbidden  # type: ignore[assignment]
    socket.socket.connect_ex = forbidden  # type: ignore[assignment]


def load_old_runner(preserved_run_dir: Path) -> ModuleType:
    """Load the hash-bound preserved assessment implementation as a module."""
    old_runner = preserved_run_dir / OLD_RUNNER_NAME
    verify_sha256(old_runner, EXPECTED_HASHES["old_runner"])
    specification = importlib.util.spec_from_file_location(
        "preserved_generic_mtf_assessment",
        old_runner,
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot load preserved 332-case runner")
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def preserved_targets(preserved_run_dir: Path) -> list[dict[str, Any]]:
    """Return the exact 332 targets from the preserved assessment artefact."""
    value = json.loads(
        verify_sha256(
            preserved_run_dir / OLD_ASSESSMENT_NAME,
            EXPECTED_HASHES["old_assessment"],
        )
    )
    items = value.get("items")
    if not isinstance(items, list) or len(items) != 332:
        raise RuntimeError("preserved assessment does not contain 332 targets")
    targets = [
        {
            "quote_id": str(item.get("quote_id") or ""),
            "document_ids": sorted({
                str(number) for number in item.get("document_ids", [])
                if re.fullmatch(r"[0-9]+", str(number))
            }, key=int),
        }
        for item in items
    ]
    if (
        len({item["quote_id"] for item in targets}) != 332
        or len({
            number
            for item in targets
            for number in item["document_ids"]
        }) != 170
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", item["quote_id"])
            or not item["document_ids"]
            for item in targets
        )
    ):
        raise RuntimeError("preserved 332-case target identity is invalid")
    return sorted(targets, key=lambda item: item["quote_id"])


def candidate_paths(www_root: Path, document_id: str) -> list[Path]:
    """Return safe regular representations for one numeric document ID."""
    if not re.fullmatch(r"[0-9]+", document_id):
        raise RuntimeError("MTF document ID is not numeric")
    root = www_root.resolve(strict=True)
    if root.name != "www.margaretthatcher.org" or root.is_symlink():
        raise RuntimeError("mirror root is not the expected safe www host")
    candidates = [
        root / f"document%2F{document_id}",
        root / "document" / document_id,
        root / "document" / f"{document_id}.html",
    ]
    output: list[Path] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise RuntimeError("local MTF candidate is not a regular file")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise RuntimeError("local MTF candidate escapes the mirror") from exc
        if resolved.stat().st_size > MAXIMUM_DOCUMENT_BYTES:
            raise RuntimeError("local MTF candidate exceeds the byte limit")
        output.append(resolved)
    if len(output) > 1:
        digests = {sha256_bytes(stable_bytes(path)) for path in output}
        if len(digests) > 1:
            raise RuntimeError(
                "conflicting local files map to one canonical MTF document"
            )
    return output


def target_inventory(
    www_root: Path,
    targets: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Inventory local representations for every document used by the targets."""
    records: list[dict[str, Any]] = []
    document_ids = sorted({
        number
        for item in targets
        for number in item["document_ids"]
    }, key=int)
    for document_id in document_ids:
        for path in candidate_paths(www_root, document_id):
            value = stable_bytes(path)
            records.append({
                "document_id": document_id,
                "representation": (
                    "html_suffix" if path.name.endswith(".html")
                    else "encoded_flat" if "%2F" in path.name
                    else "extensionless"
                ),
                "size": len(value),
                "sha256": sha256_bytes(value),
            })
    encoded = json.dumps(
        records,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "target_document_count": len(document_ids),
        "present_representation_count": len(records),
        "present_document_count": len({
            record["document_id"] for record in records
        }),
        "sha256": sha256_bytes(encoded),
        "records": records,
    }


def atomic_json(path: Path, value: Any) -> None:
    """Durably replace one private JSON output using an exclusive temporary file."""
    encoded = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_output(path: Path) -> dict[str, Any]:
    """Validate and return the preserved runner's complete 332-case output."""
    output = json.loads(stable_bytes(path))
    summary = output.get("summary")
    if (
        output.get("schema_version")
        != "generic-mtf-local-remediation-assessment-v1"
        or not isinstance(output.get("items"), list)
        or len(output["items"]) != 332
        or not isinstance(summary, dict)
        or summary.get("generic_context_with_mtf_link_quote_count") != 332
        or summary.get("unique_mtf_document_count") != 170
        or summary.get("automatic_evidence_admissions") != 0
        or output.get("policy", {}).get("canonical_mutations") != 0
        or output.get("policy", {}).get("gate_mutations") != 0
    ):
        raise RuntimeError("332-case assessment output failed validation")
    return output


def execute(
    www_root: Path,
    output_dir: Path,
    *,
    preserved_run_dir: Path,
    project_root: Path,
) -> dict[str, Any]:
    """Run the network-denied assessment and return its validation receipt."""
    prohibit_network()
    preserved_run_dir = preserved_run_dir.resolve(strict=True)
    if preserved_run_dir.is_symlink() or not preserved_run_dir.is_dir():
        raise RuntimeError("preserved run directory is not a safe directory")
    project_root = resolve_project_root(project_root)
    old_render = preserved_run_dir / OLD_RENDER_NAME
    targets = preserved_targets(preserved_run_dir)
    verify_sha256(old_render, EXPECTED_HASHES["old_render"])

    output_dir = output_dir.resolve(strict=False)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError("output directory is not empty")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    www_root = www_root.resolve(strict=True)
    before = target_inventory(www_root, targets)

    module = load_old_runner(preserved_run_dir)
    module.RUN_DIR = output_dir
    module.PRODUCTION_ROOT = project_root
    module.WWW_ROOT = www_root
    module.ARCHIVE_ROOT = www_root.parent / "archive.margaretthatcher.org"
    module.RENDER_REVIEW = old_render
    module.PACKETS_PATH = (
        project_root
        / "semantic_alignment_research/quote_research_full_001/"
        "research_packets.json"
    )
    module.SOURCE_ROLE_PATH = (
        project_root
        / "semantic_alignment_research/quote_research_full_001/"
        "historical_context_source_role_audit.json"
    )
    module.OUTPUT_PATH = (
        output_dir / "local_mtf_context_remediation_assessment.json"
    )
    module.derive_candidates = lambda _render: [
        dict(item) for item in targets
    ]
    module.local_html_paths = lambda document_id: candidate_paths(
        www_root,
        str(document_id),
    )

    result = int(module.main())
    if result != 0:
        raise RuntimeError(f"preserved assessment returned {result}")
    module.OUTPUT_PATH.chmod(0o600)
    output = validate_output(module.OUTPUT_PATH)
    after = target_inventory(www_root, targets)
    if before != after:
        raise RuntimeError(
            "one or more target mirror files changed during assessment"
        )

    validation = {
        "schema_version": 1,
        "document_kind": "generic_mtf_332_archive2_run_validation",
        "target_quote_count": 332,
        "target_document_count": 170,
        "mirror_target_inventory": before,
        "assessment_sha256": sha256_bytes(
            stable_bytes(module.OUTPUT_PATH)
        ),
        "summary": output["summary"],
        "network_calls": 0,
        "provider_calls": 0,
        "llm_calls": 0,
        "automatic_evidence_admissions": 0,
        "canonical_mutations": 0,
        "gate_mutations": 0,
        "production_write_targets": [],
    }
    atomic_json(output_dir / "run_validation.json", validation)
    return validation


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the inert-by-default wrapper."""
    value = argparse.ArgumentParser(
        description="Preserved 332-case local MTF remediation assessment"
    )
    value.add_argument("--execute", action="store_true")
    value.add_argument(
        "--preserved-run-dir",
        type=Path,
        help="private directory containing the three hash-bound preserved inputs",
    )
    value.add_argument(
        "--mirror-www-root",
        type=Path,
    )
    value.add_argument(
        "--project-root",
        type=Path,
        default=DEFAULT_PROJECT_ROOT,
    )
    value.add_argument("--output-dir", type=Path, required=True)
    return value


def main() -> int:
    """Validate command-line authority, execute when requested, and report status."""
    args = parser().parse_args()
    if not args.execute:
        print("NOT STARTED: pass --execute to run the 332-case assessment")
        return 2
    if args.preserved_run_dir is None or args.mirror_www_root is None:
        print(
            "NOT STARTED: --execute requires --preserved-run-dir and "
            "--mirror-www-root",
            file=sys.stderr,
        )
        return 2
    validation = execute(
        args.mirror_www_root,
        args.output_dir,
        preserved_run_dir=args.preserved_run_dir,
        project_root=args.project_root,
    )
    print(json.dumps(validation["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
