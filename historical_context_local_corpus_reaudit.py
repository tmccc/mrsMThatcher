#!/usr/bin/env python3
"""Bounded, local-only advisory re-audit of the eligible quotation corpus.

This runner deliberately orchestrates the maintained historical-context modules
instead of implementing another search or evidence-admission framework.  It
never mutates canonical data and never constructs a network search backend.
"""
from __future__ import annotations

import argparse
import copy
import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from bs4 import BeautifulSoup

import historical_context_search_research as research
from historical_context_formatter import (
    load_and_validate_corpus,
    packet_is_attributed_to_margaret_thatcher,
)
from historical_context_local_archive import (
    LOCAL_ARCHIVE_POLICY_VERSION,
    LOCAL_ARCHIVE_ROOT_ENV,
    LocalArchiveMirror,
    LocalMTFDocumentIndex,
)
from historical_context_project_root import resolve_project_root
from historical_context_reply_semantic_gate import (
    POLICY_VERSION as SEMANTIC_GATE_POLICY_VERSION,
    load_historical_context_semantic_gate,
)
from historical_context_search_query_strategy import (
    EXPECTED_ELIGIBLE_CORPUS_SIZE,
    build_query_plan,
    documented_variant_records,
    enrich_target_from_packet,
    load_eligible_corpus_index,
    parse_source_date,
    word_tokens,
)
from historical_context_targeted_evidence_remediation import (
    compare_primary_wording,
)


PROGRAMME_VERSION = "historical-context-local-corpus-reaudit-v2"
RUN_DIRECTORY_ENV = "MRS_HISTORICAL_REAUDIT_RUN_DIR"
MAXIMUM_DOCUMENT_BYTES = research.MAXIMUM_RESPONSE_BYTES
MAXIMUM_CANDIDATES_PER_QUOTE = 10
OUTPUT_FILENAMES = (
    "archive_inventory_before.json",
    "archive_inventory_after.json",
    "corpus_reaudit_candidates.json",
    "blocked_quote_reassessment.json",
    "proposed_historical_data_changes.json",
    "corpus_reaudit_summary.json",
    "corpus_reaudit_report.md",
)
RESEARCH_RELATIVE = Path("semantic_alignment_research/quote_research_full_001")
AUTHORITATIVE_INPUTS = (
    Path("mrsMThatcher.txt"),
    Path("quote_analysis.json"),
    RESEARCH_RELATIVE / "corpus_manifest.json",
    RESEARCH_RELATIVE / "research_packets.json",
    RESEARCH_RELATIVE / "final_unresolved/final_research_status.json",
    RESEARCH_RELATIVE / "final_unresolved/unresolved_cases.json",
    RESEARCH_RELATIVE / "historical_context_source_role_audit.json",
    RESEARCH_RELATIVE / "historical_context_source_curated_evidence.json",
    RESEARCH_RELATIVE / "historical_context_packet_corrections.json",
    RESEARCH_RELATIVE / "unresolved_quotes.json",
    Path("historical_context_published_reply_semantic_review.json"),
    Path("historical_context_public_projection_review.json"),
    Path(
        "semantic_alignment_research/quote_attribution_cleanup_001/"
        "deployment_candidate/runtime_eligible_quote_manifest.json"
    ),
)

CATEGORY_STRONGER_EVIDENCE = "stronger_primary_evidence"
CATEGORY_WORDING_CORRECTION = "verified_text_correction"
CATEGORY_EVENT_CORRECTION = "event_or_source_correction"
CATEGORY_DATE_CORRECTION = "date_correction"
CATEGORY_LOCATOR_CORRECTION = "stable_locator_or_source_identity_correction"
CATEGORY_ATTRIBUTION_CORRECTION = "attribution_or_speaker_correction"
CATEGORY_POTENTIAL_UNBLOCK = "potentially_unblockable"
CATEGORY_NEUTRAL_MATCH_REVIEW = "attribution_or_noncontiguous_match_review"
CATEGORY_NO_CHANGE = "no_advisory_change"
CATEGORY_ADDITIONAL_OCCURRENCE = "additional_primary_occurrence"
CATEGORY_EXACT_EXCERPT_CONFIRMATION = "exact_excerpt_confirmation"
CATEGORY_CONTRADICTION = "contradictory_evidence"

CATEGORY_DESCRIPTIONS = {
    CATEGORY_STRONGER_EVIDENCE: "stronger primary evidence; no public-data change",
    CATEGORY_WORDING_CORRECTION: "verified-text transcription correction",
    CATEGORY_EVENT_CORRECTION: "event/source correction",
    CATEGORY_DATE_CORRECTION: "date correction",
    CATEGORY_LOCATOR_CORRECTION: "stable-locator/source-identity correction",
    CATEGORY_ATTRIBUTION_CORRECTION: "attribution/speaker correction",
    CATEGORY_POTENTIAL_UNBLOCK: "currently blocked quote potentially unblockable",
    CATEGORY_NEUTRAL_MATCH_REVIEW: (
        "attribution-sensitive or non-contiguous wording requiring neutral review"
    ),
    CATEGORY_ADDITIONAL_OCCURRENCE: (
        "additional verified primary occurrence; preserve the current occurrence"
    ),
    CATEGORY_EXACT_EXCERPT_CONFIRMATION: (
        "stored quotation confirmed as an exact excerpt; verified text unchanged"
    ),
    CATEGORY_CONTRADICTION: (
        "evidence genuinely conflicting with an existing canonical claim"
    ),
}

LEGACY_CATEGORY_NAMES = {
    "A": CATEGORY_STRONGER_EVIDENCE,
    "B": CATEGORY_WORDING_CORRECTION,
    "C": CATEGORY_EVENT_CORRECTION,
    "D": CATEGORY_DATE_CORRECTION,
    "E": CATEGORY_LOCATOR_CORRECTION,
    "F": CATEGORY_ATTRIBUTION_CORRECTION,
    "G": CATEGORY_POTENTIAL_UNBLOCK,
    "H": CATEGORY_NEUTRAL_MATCH_REVIEW,
    "I": CATEGORY_NO_CHANGE,
}

IMPROVEMENT_CATEGORIES = {
    CATEGORY_STRONGER_EVIDENCE,
    CATEGORY_WORDING_CORRECTION,
    CATEGORY_EVENT_CORRECTION,
    CATEGORY_DATE_CORRECTION,
    CATEGORY_LOCATOR_CORRECTION,
    CATEGORY_ATTRIBUTION_CORRECTION,
    CATEGORY_POTENTIAL_UNBLOCK,
    CATEGORY_ADDITIONAL_OCCURRENCE,
    CATEGORY_EXACT_EXCERPT_CONFIRMATION,
}


class ReauditError(RuntimeError):
    """A local re-audit precondition or reproducibility invariant failed."""


def utc_now() -> str:
    """Return one explicit UTC timestamp."""
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """Return deterministic pretty JSON bytes suitable for private ledgers."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _registered_worktrees(project_root: Path) -> list[Path]:
    """Return registered worktrees so a private run cannot land in any checkout."""
    try:
        result = subprocess.run(
            ("git", "-C", str(project_root), "worktree", "list", "--porcelain"),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReauditError("unable to enumerate registered Git worktrees") from exc
    roots = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            roots.append(Path(line.removeprefix("worktree ")).resolve())
    if project_root not in roots:
        raise ReauditError("configured project root is not a registered worktree")
    return roots


def _resolve_archive_root() -> Path:
    archive_raw = os.environ.get(LOCAL_ARCHIVE_ROOT_ENV, "").strip()
    if not archive_raw:
        raise ReauditError(f"{LOCAL_ARCHIVE_ROOT_ENV} must name the local archive root")
    archive = Path(archive_raw)
    if not archive.is_absolute():
        raise ReauditError(f"{LOCAL_ARCHIVE_ROOT_ENV} must be an absolute path")
    try:
        archive = archive.resolve(strict=True)
    except OSError as exc:
        raise ReauditError("the configured local archive directory does not exist") from exc
    if not archive.is_dir():
        raise ReauditError(f"{LOCAL_ARCHIVE_ROOT_ENV} is not a directory")
    return archive


def _validate_new_private_output_dir(
    project_root: Path, archive: Path, run_dir: Path
) -> Path:
    if not run_dir.is_absolute():
        raise ReauditError("the private output directory must be an absolute path")
    try:
        run_dir = run_dir.resolve(strict=True)
    except OSError as exc:
        raise ReauditError("the configured private output directory does not exist") from exc
    if not run_dir.is_dir():
        raise ReauditError("the private output path is not a directory")
    if run_dir.stat().st_mode & 0o777 != 0o700:
        raise ReauditError("the private output directory must have mode 0700")
    if any(run_dir.iterdir()):
        raise ReauditError("the private output directory must be empty before the run")
    for worktree in _registered_worktrees(project_root):
        if run_dir == worktree or _is_relative_to(run_dir, worktree):
            raise ReauditError("the private output directory must be outside every Git worktree")
    if run_dir == archive or _is_relative_to(run_dir, archive):
        raise ReauditError("the private output directory must be outside the local archive")
    return run_dir


def resolve_private_inputs(project_root: Path) -> tuple[Path, Path]:
    """Resolve and validate the two operator-supplied private directories."""
    archive = _resolve_archive_root()
    run_raw = os.environ.get(RUN_DIRECTORY_ENV, "").strip()
    if not run_raw:
        raise ReauditError(f"{RUN_DIRECTORY_ENV} must name a new private run directory")
    run_dir = _validate_new_private_output_dir(project_root, archive, Path(run_raw))
    return archive, run_dir


def resolve_reclassification_inputs(
    project_root: Path, source_run_dir: Path, output_dir: Path
) -> tuple[Path, Path, Path]:
    """Resolve one read-only source package and one new private output directory."""
    archive = _resolve_archive_root()
    if not source_run_dir.is_absolute():
        raise ReauditError("the source run directory must be an absolute path")
    try:
        source_run_dir = source_run_dir.resolve(strict=True)
    except OSError as exc:
        raise ReauditError("the source run directory does not exist") from exc
    if not source_run_dir.is_dir() or source_run_dir.is_symlink():
        raise ReauditError("the source run path must be a real directory")
    output_dir = _validate_new_private_output_dir(
        project_root, archive, output_dir
    )
    if output_dir == source_run_dir or _is_relative_to(output_dir, source_run_dir):
        raise ReauditError("the new output directory must be outside the source run directory")
    return archive, source_run_dir, output_dir


def _guarded_output_path(run_dir: Path, filename: str) -> Path:
    if filename not in OUTPUT_FILENAMES:
        raise ReauditError(f"unrecognised re-audit output filename: {filename}")
    path = (run_dir / filename).resolve(strict=False)
    if path.parent != run_dir.resolve(strict=True):
        raise ReauditError("re-audit output escaped the private run directory")
    return path


def write_json(run_dir: Path, filename: str, value: Any) -> None:
    """Write one private output without exposing a general-purpose writer."""
    path = _guarded_output_path(run_dir, filename)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(canonical_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def write_report(run_dir: Path, value: str) -> None:
    path = _guarded_output_path(run_dir, "corpus_reaudit_report.md")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        with contextlib.suppress(OSError):
            path.unlink()
        raise


def _remove_incomplete_output_package(run_dir: Path) -> None:
    failures = []
    for filename in OUTPUT_FILENAMES:
        path = run_dir / filename
        try:
            path.unlink(missing_ok=True)
        except OSError:
            failures.append(filename)
    if failures:
        raise ReauditError(
            "failed to remove incomplete reclassification output package: "
            + ", ".join(failures)
        )


@contextlib.contextmanager
def deny_network() -> Iterable[None]:
    """Fail closed if any selected code path attempts DNS or a socket connection."""
    original_getaddrinfo = socket.getaddrinfo
    original_create_connection = socket.create_connection
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise ReauditError("network access is forbidden during the local corpus re-audit")

    socket.getaddrinfo = blocked  # type: ignore[assignment]
    socket.create_connection = blocked  # type: ignore[assignment]
    socket.socket.connect = blocked  # type: ignore[assignment]
    socket.socket.connect_ex = blocked  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo  # type: ignore[assignment]
        socket.create_connection = original_create_connection  # type: ignore[assignment]
        socket.socket.connect = original_connect  # type: ignore[assignment]
        socket.socket.connect_ex = original_connect_ex  # type: ignore[assignment]


def authoritative_hashes(project_root: Path) -> dict[str, str]:
    """Hash the maintained inputs that this advisory runner must never mutate."""
    output: dict[str, str] = {}
    for relative in AUTHORITATIVE_INPUTS:
        path = project_root / relative
        if not path.is_file():
            raise ReauditError(f"required authoritative input is missing: {relative}")
        output[relative.as_posix()] = file_sha256(path)
    return output


def assert_authoritative_inputs_unchanged(
    before: Mapping[str, str], after: Mapping[str, str]
) -> None:
    if dict(before) != dict(after):
        changed = sorted(set(before) | set(after))
        changed = [name for name in changed if before.get(name) != after.get(name)]
        raise ReauditError(
            "canonical input changed during advisory run: " + ", ".join(changed)
        )


def inventory_document(inventory: Any, *, phase: str, timestamp: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_kind": "local_mtf_numeric_document_inventory",
        "phase": phase,
        "run_timestamp": timestamp,
        "local_archive_policy_version": LOCAL_ARCHIVE_POLICY_VERSION,
        "document_count": inventory.document_count,
        "total_bytes": inventory.total_bytes,
        "inventory_sha256": inventory.sha256,
        "private_root_not_recorded": True,
    }


def inventory_is_stable(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    return all(
        before.get(key) == after.get(key)
        for key in ("document_count", "total_bytes", "inventory_sha256")
    )


def require_usable_inventory(inventory: Mapping[str, Any]) -> None:
    """Reject an empty configured mirror without requiring archive completeness."""
    if (
        type(inventory.get("document_count")) is not int
        or int(inventory["document_count"]) <= 0
        or type(inventory.get("total_bytes")) is not int
        or int(inventory["total_bytes"]) <= 0
        or not re.fullmatch(r"[0-9a-f]{64}", str(inventory.get("inventory_sha256") or ""))
    ):
        raise ReauditError("configured local archive has no usable numeric MTF document inventory")


def _resolved_eligible_ids(project_root: Path) -> list[str]:
    document = read_json(
        project_root
        / "semantic_alignment_research/quote_attribution_cleanup_001/"
        "deployment_candidate/runtime_eligible_quote_manifest.json"
    )
    ids = document.get("runtime_eligible_quote_ids")
    aliases = document.get("runtime_quote_aliases")
    if not isinstance(ids, list) or not isinstance(aliases, dict):
        raise ReauditError("runtime-eligible quotation manifest is malformed")
    return [str(aliases.get(str(quote_id), quote_id)) for quote_id in ids]


def _query_strategy_phrases(
    target: Mapping[str, Any], plan: Sequence[Mapping[str, Any]]
) -> list[dict[str, str]]:
    """Extract plain textual fragments, never provider query syntax."""
    phrases: list[dict[str, str]] = []
    for variant in documented_variant_records(target):
        phrases.append({
            "variant": str(variant["search_variant"]),
            "provenance": "authoritative_recorded_variant",
        })
    for row in plan:
        fragment = str(row.get("fragment_text") or row.get("fragment") or "").strip()
        if fragment:
            phrases.append({
                "variant": fragment,
                "provenance": "deterministic_query_strategy_fragment",
            })
        multi = row.get("multi_anchor")
        if isinstance(multi, Mapping):
            for phrase in multi.get("anchor_phrases", []):
                if str(phrase).strip():
                    phrases.append({
                        "variant": str(phrase).strip(),
                        "provenance": "deterministic_query_strategy_multi_anchor",
                    })
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in phrases:
        key = (" ".join(row["variant"].casefold().split()), row["provenance"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def derive_eligible_targets(
    project_root: Path,
    *,
    prepare_search_strategy: bool = True,
) -> dict[str, Any]:
    """Derive exactly the authoritative current 611-member eligible corpus."""
    project_root = project_root.resolve()
    corpus_index = load_eligible_corpus_index(
        project_root, expected_count=EXPECTED_ELIGIBLE_CORPUS_SIZE
    )
    research_dir = project_root / RESEARCH_RELATIVE
    packets, unresolved = load_and_validate_corpus(
        research_dir, require_source_role_audit=True
    )
    attributed = {
        quote_id
        for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    eligible_ids = _resolved_eligible_ids(project_root)
    if (
        len(eligible_ids) != EXPECTED_ELIGIBLE_CORPUS_SIZE
        or len(set(eligible_ids)) != EXPECTED_ELIGIBLE_CORPUS_SIZE
        or set(eligible_ids) != attributed
    ):
        raise ReauditError("authoritative eligible corpus is not exactly 611 quotations")
    gate = load_historical_context_semantic_gate(
        root=project_root, eligible_quote_ids=eligible_ids
    )
    if not gate.available:
        raise ReauditError(f"authoritative semantic gate is unavailable: {gate.reason}")
    source_role = read_json(research_dir / "historical_context_source_role_audit.json")
    source_items = source_role.get("items")
    if not isinstance(source_items, dict):
        raise ReauditError("validated source-role audit has no item mapping")
    review = read_json(project_root / "historical_context_published_reply_semantic_review.json")
    review_rows = {
        str(row.get("quote_id") or ""): row
        for row in review.get("records", [])
        if isinstance(row, dict)
    }
    targets = []
    for quote_id in eligible_ids:
        packet = packets.get(quote_id)
        if not isinstance(packet, dict):
            raise ReauditError(f"eligible quotation lacks a completed packet: {quote_id}")
        base = {
            "quote_id": quote_id,
            "quotation_text": packet["quote_text"],
        }
        enriched = enrich_target_from_packet(
            base,
            packet,
            packet_locator=f"{RESEARCH_RELATIVE.as_posix()}/research_packets.json/items/{quote_id}",
        )
        if prepare_search_strategy:
            plan, diagnostics = build_query_plan(enriched, corpus_index)
        else:
            plan, diagnostics = [], {"preparation_skipped": True}
        variants = [
            str(row["search_variant"])
            for row in documented_variant_records(enriched)
        ]
        fragments = [
            row["variant"]
            for row in _query_strategy_phrases(enriched, plan)
            if row["provenance"].startswith("deterministic_query_strategy")
        ]
        gate_disposition = gate.disposition(quote_id)
        reviewed_disposition = gate.reviewed_disposition(quote_id)
        targets.append({
            **enriched,
            "recorded_variants": variants,
            "distinctive_fragments": fragments[:6],
            "deterministic_clauses": research.deterministic_quote_clauses(
                packet["quote_text"]
            ),
            "local_discovery_phrases": _query_strategy_phrases(enriched, plan),
            "query_strategy_diagnostics_sha256": sha256_bytes(
                research.canonical_json_bytes(diagnostics)
            ),
            "current_gate_status": "blocked" if gate_disposition else "allowed",
            "current_gate_disposition": (
                gate_disposition or reviewed_disposition or "not_reviewed_open"
            ),
            "current_unresolved_status": quote_id in unresolved,
            "current_packet": packet,
            "current_source_role": source_items.get(quote_id, {}),
            "current_gate_review": review_rows.get(quote_id, {}),
        })
    return {
        "targets": targets,
        "packets": packets,
        "unresolved_ids": sorted(unresolved),
        "gate": gate,
        "corpus_index": corpus_index,
    }


_SPEAKER_PREFIX = re.compile(r"^\s*([^:]{1,80})\s*:\s*(.*)$", re.S)
_THATCHER_LABEL = re.compile(
    r"^(?:the\s+)?(?:rt\.?\s+hon\.?\s+)?(?:mrs?\.?\s+|lady\s+|baroness\s+)?"
    r"(?:margaret(?:\s+hilda)?\s+)?thatcher$|^(?:mt|pm|prime minister|answer|a)$",
    re.I,
)
_OTHER_LABEL = re.compile(
    r"^(?:q|question|interviewer|interviewers?|chairman|moderator|journalist|"
    r"press|reporter|audience|mr\.?\s+.+|mrs\.?\s+(?!thatcher).+|lord\s+.+|"
    r"sir\s+.+|[A-Z][A-Z .'-]{1,50})$",
    re.I,
)


def _article_node(body: bytes, validation: Mapping[str, Any]) -> Any:
    soup = BeautifulSoup(body, "lxml")
    selector = str(validation.get("selector_kind") or "")
    if selector == "legacy":
        return soup.select_one("#documentbody")
    if selector == "current_mirror":
        return soup.select_one(".document-body")
    return soup.select_one("article.node-archive-document")


def speaker_segments(body: bytes, validation: Mapping[str, Any]) -> dict[str, Any]:
    """Create contribution-bounded transcript segments without speaker joining."""
    article = _article_node(body, validation)
    if article is None:
        return {"labels_detected": False, "segments": []}
    blocks = [
        " ".join(node.get_text(" ", strip=True).split())
        for node in article.find_all(("p", "li", "blockquote", "h2", "h3"))
    ]
    blocks = [value for value in blocks if value]
    parsed: list[tuple[str, str]] = []
    labels_detected = False
    author_verified = research._is_margaret_thatcher_author(
        str(validation.get("author") or "")
    )
    for block in blocks:
        match = _SPEAKER_PREFIX.match(block)
        label = ""
        value = block
        if match:
            possible = " ".join(match.group(1).split())
            if _THATCHER_LABEL.fullmatch(possible) or _OTHER_LABEL.fullmatch(possible):
                label, value = possible, match.group(2).strip()
                labels_detected = True
        elif block.casefold() in {
            "q", "question", "interviewer", "mt", "pm", "prime minister",
            "mrs thatcher", "margaret thatcher", "answer", "a",
        }:
            label, value = block, ""
            labels_detected = True
        parsed.append((label, value))
    if not labels_detected:
        text = " ".join(article.get_text(" ", strip=True).split())
        return {
            "labels_detected": False,
            "segments": [{
                "speaker_class": "thatcher" if author_verified else "unverified",
                "speaker_label": str(validation.get("author") or ""),
                "text": text,
                "evidence_basis": (
                    "explicit_document_author" if author_verified
                    else "document_author_not_explicitly_verified"
                ),
            }] if text else [],
        }
    segments: list[dict[str, str]] = []
    current_class = "unverified"
    current_label = ""
    for label, value in parsed:
        if label:
            if _THATCHER_LABEL.fullmatch(label) and (
                author_verified
                or "thatcher" in label.casefold()
                or label.casefold() == "mt"
            ):
                current_class = "thatcher"
            elif _OTHER_LABEL.fullmatch(label):
                current_class = "other"
            else:
                current_class = "unverified"
            current_label = label
        if not value:
            continue
        if (
            segments
            and segments[-1]["speaker_class"] == current_class
            and segments[-1]["speaker_label"] == current_label
        ):
            segments[-1]["text"] += " " + value
        else:
            segments.append({
                "speaker_class": current_class,
                "speaker_label": current_label,
                "text": value,
                "evidence_basis": "explicit_transcript_speaker_label",
            })
    return {"labels_detected": True, "segments": segments}


_MATCH_PRIORITY = {
    "exact_quotation": 0,
    "recorded_variant": 1,
    "assembled_clauses": 2,
    "near_exact_variant": 3,
    "distinctive_fragment_only": 4,
    "none": 5,
}


def contribution_aware_match(
    target: Mapping[str, Any],
    extraction: Mapping[str, Any],
    body: bytes,
    validation: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select evidence within one contribution; reject cross-speaker joins."""
    segmentation = speaker_segments(body, validation)
    matches: list[tuple[tuple[int, int, int], dict[str, Any], Mapping[str, str]]] = []
    for order, segment in enumerate(segmentation["segments"]):
        match = research.extract_supporting_passage(target, segment["text"])
        rank = (
            _MATCH_PRIORITY.get(str(match.get("match_type") or "none"), 9),
            0 if segment["speaker_class"] == "thatcher" else 1,
            order,
        )
        matches.append((rank, match, segment))
    if matches:
        _rank, best, segment = min(matches, key=lambda row: row[0])
    else:
        best = {"match_type": "none", "supporting_passage": "", "span": None}
        segment = {
            "speaker_class": "unverified", "speaker_label": "",
            "evidence_basis": "no_transcript_segment",
        }
    full_match = research.extract_supporting_passage(
        target, str(extraction.get("text") or "")
    )
    if segmentation["labels_detected"]:
        joined_match = research.extract_supporting_passage(
            target,
            " ".join(str(row.get("text") or "") for row in segmentation["segments"]),
        )
        if _MATCH_PRIORITY.get(str(joined_match.get("match_type") or "none"), 9) < _MATCH_PRIORITY.get(
            str(full_match.get("match_type") or "none"), 9
        ):
            full_match = joined_match
    cross_speaker = bool(
        segmentation["labels_detected"]
        and _MATCH_PRIORITY.get(str(full_match.get("match_type") or "none"), 9)
        < _MATCH_PRIORITY.get(str(best.get("match_type") or "none"), 9)
    )
    if cross_speaker:
        best = {
            **full_match,
            "match_type": "assembled_clauses",
            "cross_speaker_join_rejected": True,
        }
        segment = {
            "speaker_class": "unverified",
            "speaker_label": "multiple contributions",
            "evidence_basis": "cross_speaker_join_rejected",
        }
    speaker = {
        "verified": segment.get("speaker_class") == "thatcher" and not cross_speaker,
        "speaker_class": segment.get("speaker_class"),
        "speaker_label": segment.get("speaker_label"),
        "evidence_basis": segment.get("evidence_basis"),
        "document_author": str(validation.get("author") or ""),
        "labels_detected": bool(segmentation["labels_detected"]),
        "segments_inspected": len(segmentation["segments"]),
        "cross_speaker_join_rejected": cross_speaker,
    }
    return best, speaker


def _normalised_match_type(value: str) -> str:
    return {
        "exact_quotation": "exact quotation",
        "recorded_variant": "recorded variant",
        "assembled_clauses": "partial/assembled wording",
        "distinctive_fragment_only": "partial/assembled wording",
        "near_exact_variant": "similar sentiment only",
        "none": "no support",
    }.get(value, "no support")


def _document_id_from_value(value: Any) -> str:
    match = re.search(
        r"(?:margaretthatcher\.org/document/|\bDocument\s+)([0-9]+)",
        str(value or ""),
        re.I,
    )
    return match.group(1) if match else ""


def _source_rows(role: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for key in (
        "sources", "renderable_sources", "researched_sources", "curated_sources",
        "recovered_sources", "virtual_locator_sources", "model_proposed_source_leads",
    ):
        values = role.get(key)
        if isinstance(values, list):
            rows.extend(value for value in values if isinstance(value, Mapping))
    return rows


def _current_occurrence_source_rows(
    role: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for key in ("sources", "renderable_sources"):
        values = role.get(key)
        if isinstance(values, list):
            rows.extend(value for value in values if isinstance(value, Mapping))
    return rows


def _source_row_document_id(row: Mapping[str, Any]) -> str:
    for key in ("canonical_url", "public_url", "source_url", "source_title"):
        document_id = _document_id_from_value(row.get(key))
        if document_id:
            return document_id
    return ""


def _source_row_is_bound_to_current_occurrence(
    packet: Mapping[str, Any], row: Mapping[str, Any]
) -> bool:
    """Recognise only explicit packet-locator or matching date/event bindings."""
    packet_document_id = _document_id_from_value(packet.get("stable_locator"))
    row_document_id = _source_row_document_id(row)
    if packet_document_id and row_document_id == packet_document_id:
        return True

    packet_date = _normalised_date(packet.get("date"))
    row_date: dict[str, Any] = {"known": False}
    for key in ("source_date", "document_date", "publication_date", "date"):
        row_date = _normalised_date(row.get(key))
        if row_date.get("known"):
            break
    packet_event = str(packet.get("source_event") or "")
    row_event = ""
    for key in ("source_event", "document_event", "event", "event_title"):
        row_event = str(row.get(key) or "")
        if row_event:
            break
    return bool(
        row_document_id
        and packet_date.get("known")
        and row_date.get("known")
        and _dates_directly_bound(packet_date, row_date)
        and packet_event
        and row_event
        and _event_equivalent(packet_event, row_event)
    )


def current_values(target: Mapping[str, Any]) -> dict[str, Any]:
    packet = target["current_packet"]
    role = target["current_source_role"]
    packet_document_id = _document_id_from_value(packet.get("stable_locator"))
    current_occurrence_ids = {packet_document_id}
    known_evidence_ids = {packet_document_id}
    direct_urls: set[str] = set()
    inspected_ids: set[str] = set()
    inspected_hashes: set[str] = set()
    for row in _source_rows(role):
        row_document_id = _source_row_document_id(row)
        if row_document_id:
            known_evidence_ids.add(row_document_id)
        for key in ("canonical_url", "public_url", "source_url", "source_title"):
            value = str(row.get(key) or "")
            if "margaretthatcher.org/document/" in value and value.startswith(("http://", "https://")):
                direct_urls.add(value)
        if row.get("page_independently_inspected") is True:
            if row_document_id:
                inspected_ids.add(row_document_id)
            for key in ("page_sha256", "page_text_sha256"):
                value = str(row.get(key) or "")
                if re.fullmatch(r"[0-9a-f]{64}", value):
                    inspected_hashes.add(value)
    for row in _current_occurrence_source_rows(role):
        if _source_row_is_bound_to_current_occurrence(packet, row):
            current_occurrence_ids.add(_source_row_document_id(row))
    current_occurrence_ids.discard("")
    known_evidence_ids.discard("")
    return {
        "quotation_text": packet.get("quote_text"),
        "verified_text": packet.get("verified_text"),
        "speaker": packet.get("speaker"),
        "source_event": packet.get("source_event"),
        "date": packet.get("date"),
        "stable_locator": packet.get("stable_locator"),
        "verification_status": packet.get("verification_status"),
        "research_confidence": packet.get("research_confidence"),
        "wording_status": role.get("wording_status_after"),
        "historical_context_confidence": (
            role.get("confidence_after", {}).get("historical_context")
            if isinstance(role.get("confidence_after"), Mapping) else ""
        ),
        "current_occurrence_mtf_document_ids": sorted(
            current_occurrence_ids, key=int
        ),
        "known_evidence_mtf_document_ids": sorted(known_evidence_ids, key=int),
        "direct_mtf_public_urls": sorted(direct_urls),
        "independently_inspected_mtf_document_ids": sorted(inspected_ids, key=int),
        "independently_inspected_hashes": sorted(inspected_hashes),
    }


def _normalised_date(value: Any) -> dict[str, Any]:
    parsed = parse_source_date(str(value or ""))
    if parsed.get("known"):
        return parsed
    raw = " ".join(str(value or "").split())
    for pattern in (
        r"(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})",
        r"(?P<year>\d{4})\s+(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})",
        r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})\s+(?P<year>\d{4})",
    ):
        match = re.search(pattern, raw)
        if not match:
            continue
        try:
            month = match.group("month")
            if month.isdigit():
                month_number = int(month)
            else:
                month_number = dt.datetime.strptime(month[:3], "%b").month
            date = dt.date(
                int(match.group("year")), month_number, int(match.group("day"))
            )
        except ValueError:
            continue
        return {
            "raw": raw, "known": True, "iso_date": date.isoformat(),
            "year": date.year, "precision": "day",
        }
    return parsed


def _dates_directly_bound(
    current: Mapping[str, Any], candidate: Mapping[str, Any]
) -> bool:
    if not current.get("known") or not candidate.get("known"):
        return False
    if current.get("precision") != candidate.get("precision"):
        return False
    if current.get("precision") == "day":
        return bool(
            current.get("iso_date")
            and current.get("iso_date") == candidate.get("iso_date")
        )
    return bool(
        current.get("precision") == "year"
        and current.get("year") == candidate.get("year")
    )


def _date_evidence_conflicts(
    current: Mapping[str, Any], candidate: Mapping[str, Any]
) -> bool:
    if not current.get("known") or not candidate.get("known"):
        return False
    if current.get("year") != candidate.get("year"):
        return True
    return bool(
        current.get("precision") == "day"
        and candidate.get("precision") == "day"
        and current.get("iso_date") != candidate.get("iso_date")
    )


def _event_equivalent(current: str, candidate: str) -> bool:
    def tokens(value: str) -> set[str]:
        return {
            token for token in word_tokens(value)
            if token not in {"the", "a", "an", "for", "at", "on", "to", "and"}
        }
    left, right = tokens(current), tokens(candidate)
    if not left or not right:
        return False
    return left <= right or right <= left or len(left & right) / len(left | right) >= 0.55


def _occurrence_relation(
    candidate: Mapping[str, Any], current: Mapping[str, Any]
) -> str:
    """Resolve occurrence identity once before considering individual fields."""
    document_id = str(candidate.get("candidate_mtf_document_id") or "")
    current_ids = set(current.get("current_occurrence_mtf_document_ids", []))
    if candidate.get("current_occurrence_disproved") is True:
        return "current_occurrence_explicitly_disproved"
    if document_id and document_id in current_ids:
        return "same_current_occurrence"

    current_date = _normalised_date(current.get("date"))
    candidate_date = _normalised_date(candidate.get("document_date_evidence"))
    date_conflicts = _date_evidence_conflicts(current_date, candidate_date)
    current_event = str(current.get("source_event") or "")
    candidate_event = str(candidate.get("document_event_evidence") or "")
    event_conflicts = bool(
        current_event
        and candidate_event
        and not _event_equivalent(current_event, candidate_event)
    )
    if date_conflicts or event_conflicts:
        return "distinct_additional_occurrence"
    return "identity_unknown_nonconflicting"


def _contains_token_sequence(container: str, excerpt: str) -> bool:
    container_tokens = word_tokens(container)
    excerpt_tokens = word_tokens(excerpt)
    if not container_tokens or not excerpt_tokens or len(excerpt_tokens) > len(container_tokens):
        return False
    width = len(excerpt_tokens)
    return any(
        container_tokens[index:index + width] == excerpt_tokens
        for index in range(len(container_tokens) - width + 1)
    )


def _authoritative_candidate_wording(
    target: Mapping[str, Any], candidate: Mapping[str, Any]
) -> str:
    """Return only an exact quotation or recorded variant present in the passage."""
    passage = str(candidate.get("supporting_passage") or "")
    quotation = str(target.get("quotation_text") or "")
    if quotation and _contains_token_sequence(passage, quotation):
        return quotation
    if candidate.get("match_type") == "recorded variant":
        for variant in target.get("recorded_variants", []):
            value = str(variant or "").strip()
            if value and _contains_token_sequence(passage, value):
                return value
    return ""


def _neutral_match_review(candidate: Mapping[str, Any]) -> bool:
    match_type = str(candidate.get("match_type") or "")
    return bool(
        candidate.get("cross_speaker_join_rejected")
        or (
            match_type in {"partial/assembled wording", "similar sentiment only"}
            and candidate.get("candidate_classification") == "contradictory_evidence"
        )
    )


def _genuine_canonical_contradiction(candidate: Mapping[str, Any]) -> bool:
    if candidate.get("conflicts_with_existing_canonical_claim") is True:
        return True
    return bool(
        candidate.get("candidate_classification") == "contradictory_evidence"
        and not _neutral_match_review(candidate)
    )


def _blocking_reason_resolved(
    disposition: str,
    reason: str,
    *,
    speaker_verified: bool,
    wording_verified: bool,
    context_date_source_sufficient: bool,
) -> tuple[bool, str]:
    """Apply a conservative evidence-only projection of the maintained gate."""
    folded = reason.casefold()
    if disposition == "future_correction_needed":
        return False, "primary evidence does not itself apply or review the required future semantic correction"
    if "outside the present evidence-admission scope" in folded:
        return False, "the maintained gate requires a separate semantic review, not only source evidence"
    if any(marker in folded for marker in (
        "meaning adds", "meaning implies", "meaning strengthens",
        "published meaning", "unsupported claims about",
    )):
        return False, "the source finding does not by itself resolve the open meaning/interpretation issue"
    if not speaker_verified:
        return False, "speaker or attribution is not verified"
    if not wording_verified:
        return False, "exact or recorded primary wording is not verified"
    if not context_date_source_sufficient:
        return False, "context, date, or source identity remains insufficient"
    return True, "verified primary evidence resolves the stated evidence-only blocking reason"


def classify_changes(
    target: Mapping[str, Any],
    candidate: Mapping[str, Any],
    current: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any], bool, str]:
    """Return semantic advisory categories without applying any proposed value."""
    categories: list[str] = []
    proposed: dict[str, Any] = {}
    if candidate.get("candidate_evidence_stale") is True:
        return (
            [CATEGORY_NO_CHANGE],
            proposed,
            False,
            "candidate evidence identity is stale and cannot support admission",
        )
    document_id = str(candidate.get("candidate_mtf_document_id") or "")
    match_type = str(candidate.get("match_type") or "")
    strong = bool(
        candidate.get("accepted_as_primary_evidence")
        and candidate.get("speaker_author_evidence", {}).get("verified")
        and match_type in {"exact quotation", "recorded variant"}
    )
    if _neutral_match_review(candidate):
        categories.append(CATEGORY_NEUTRAL_MATCH_REVIEW)
    elif _genuine_canonical_contradiction(candidate):
        categories.append(CATEGORY_CONTRADICTION)
        proposed["historical_context_confidence"] = (
            "possible downgrade; genuine conflict requires manual review"
        )
    if strong:
        already_inspected = document_id in set(
            current.get("independently_inspected_mtf_document_ids", [])
        )
        if not already_inspected:
            categories.append(CATEGORY_STRONGER_EVIDENCE)
            proposed["evidence"] = "retain inspected local primary passage and hashes for review"
        current_date = _normalised_date(current.get("date"))
        candidate_date = _normalised_date(candidate.get("document_date_evidence"))
        current_occurrence_ids = set(
            current.get("current_occurrence_mtf_document_ids", [])
        )
        occurrence_relation = _occurrence_relation(candidate, current)
        same_identity = occurrence_relation == "same_current_occurrence"
        current_disproved = (
            occurrence_relation == "current_occurrence_explicitly_disproved"
        )
        correction_identity = same_identity or current_disproved
        identity_unknown = not current_occurrence_ids
        can_fill_unknown = bool(
            occurrence_relation == "identity_unknown_nonconflicting"
            and identity_unknown
        )
        additional_occurrence = (
            occurrence_relation == "distinct_additional_occurrence"
        )
        if candidate_date.get("known") and not additional_occurrence:
            current_iso = str(current_date.get("iso_date") or "")
            candidate_iso = str(candidate_date.get("iso_date") or "")
            improves_precision = (
                not current_date.get("known")
                or (current_date.get("precision") != "day" and candidate_date.get("precision") == "day")
            )
            differs = bool(current_iso and candidate_iso and current_iso != candidate_iso)
            if improves_precision and (correction_identity or can_fill_unknown):
                categories.append(CATEGORY_DATE_CORRECTION)
                proposed["date"] = candidate_iso or candidate_date.get("raw")
            elif differs and correction_identity:
                categories.append(CATEGORY_DATE_CORRECTION)
                proposed["date"] = candidate_iso or candidate_date.get("raw")
        event = str(candidate.get("document_event_evidence") or "")
        current_event = str(current.get("source_event") or "")
        if (
            event
            and not current_event
            and not additional_occurrence
            and (correction_identity or can_fill_unknown)
        ):
            categories.append(CATEGORY_EVENT_CORRECTION)
            proposed["source_event"] = event
        elif (
            event
            and current_event
            and not additional_occurrence
            and not _event_equivalent(current_event, event)
            and correction_identity
        ):
            categories.append(CATEGORY_EVENT_CORRECTION)
            proposed["source_event"] = event
        if additional_occurrence and not current_disproved:
            categories.append(CATEGORY_ADDITIONAL_OCCURRENCE)
            proposed["additional_primary_occurrence"] = {
                "date": candidate_date.get("iso_date") or candidate_date.get("raw"),
                "source_event": event,
                "mtf_document_id": document_id,
            }
        direct_urls = " ".join(current.get("direct_mtf_public_urls", []))
        if not additional_occurrence and (
            (identity_unknown and document_id)
            or (same_identity and document_id not in direct_urls)
            or (current_disproved and document_id)
        ):
            categories.append(CATEGORY_LOCATOR_CORRECTION)
            proposed["stable_locator"] = f"Margaret Thatcher Foundation Document {document_id}"
            proposed["canonical_public_url"] = candidate.get("canonical_public_url")
        passage = str(candidate.get("supporting_passage") or "")
        verified_text = str(current.get("verified_text") or "")
        quotation_text = str(current.get("quotation_text") or "")
        authoritative_wording = _authoritative_candidate_wording(target, candidate)
        quote_is_exact_excerpt = bool(
            quotation_text
            and authoritative_wording == quotation_text
            and _contains_token_sequence(passage, quotation_text)
            and _contains_token_sequence(verified_text, quotation_text)
            and (
                len(word_tokens(passage)) > len(word_tokens(quotation_text))
                or len(word_tokens(verified_text)) > len(word_tokens(quotation_text))
            )
        )
        if quote_is_exact_excerpt:
            categories.append(CATEGORY_EXACT_EXCERPT_CONFIRMATION)
        elif (
            correction_identity
            and verified_text
            and authoritative_wording
            and not _contains_token_sequence(verified_text, authoritative_wording)
        ):
            comparison = compare_primary_wording(
                authoritative_wording, verified_text
            )
            if comparison.get("classification") in {
                "exact_primary_wording", "primary_variant"
            }:
                categories.append(CATEGORY_WORDING_CORRECTION)
                proposed["verified_text"] = authoritative_wording
        speaker = str(current.get("speaker") or "")
        if speaker and "thatcher" not in speaker.casefold():
            categories.append(CATEGORY_ATTRIBUTION_CORRECTION)
            proposed["speaker"] = candidate.get("speaker_author_evidence", {}).get("speaker_label") or "Margaret Thatcher"
    review = target.get("current_gate_review")
    reason = str(review.get("reason") or "") if isinstance(review, Mapping) else ""
    disposition = str(target.get("current_gate_disposition") or "")
    wording_verified = strong
    context_sufficient = bool(
        strong
        and candidate.get("document_date_evidence")
        and candidate.get("document_event_evidence")
        and candidate.get("canonical_public_url")
        and candidate.get("surrounding_context")
    )
    proposed_unblock = False
    unblock_reason = "quotation is not currently blocked"
    if target.get("current_gate_status") == "blocked":
        proposed_unblock, unblock_reason = _blocking_reason_resolved(
            disposition,
            reason,
            speaker_verified=bool(candidate.get("speaker_author_evidence", {}).get("verified")),
            wording_verified=wording_verified,
            context_date_source_sufficient=context_sufficient,
        )
        if proposed_unblock:
            categories.append(CATEGORY_POTENTIAL_UNBLOCK)
    if not categories:
        categories = [CATEGORY_NO_CHANGE]
    return list(dict.fromkeys(categories)), proposed, proposed_unblock, unblock_reason


def verify_candidate(
    mirror: LocalArchiveMirror,
    target: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Load and inspect one real local MTF file before any evidence claim."""
    url = str(result.get("result_url") or "")
    document_id = str(result.get("document_id") or "")
    record = mirror.read(url)
    base = {
        "quote_id": target["quote_id"],
        "quotation_text": target["quotation_text"],
        "current_gate_disposition": target["current_gate_disposition"],
        "current_gate_status": target["current_gate_status"],
        "current_unresolved_status": target["current_unresolved_status"],
        "candidate_mtf_document_id": document_id,
        "canonical_public_url": url,
        "local_file_sha256": "",
        "match_type": "no support",
        "supporting_passage": "",
        "supporting_passage_sha256": sha256_bytes(b""),
        "surrounding_context": "",
        "speaker_author_evidence": {"verified": False, "evidence_basis": "file_not_verified"},
        "document_event_evidence": "",
        "document_date_evidence": "",
        "candidate_classification": "inaccessible",
        "accepted_as_primary_evidence": False,
        "actual_regular_file_verified": False,
        "cross_speaker_join_rejected": False,
        "current_values": current_values(target),
        "proposed_values": {},
        "proposed_change_category": [CATEGORY_NO_CHANGE],
        "confidence": "low",
        "manual_review_still_required": False,
        "proposed_unblock": False,
        "proposed_unblock_rationale": "candidate file was not verified",
    }
    if not record or record.get("status") != "fetched":
        base["candidate_status"] = (
            "local_archive_miss" if record is None else str(record.get("status"))
        )
        return base
    body = bytes(record.get("body") or b"")
    validation = research.inspect_mtf_document(
        url, str(record.get("content_type") or ""), body
    )
    if not validation.get("valid"):
        base.update({
            "candidate_status": str(validation.get("status") or "invalid_official_document"),
            "local_file_sha256": str(record.get("local_archive_file_sha256") or ""),
            "speaker_author_evidence": {
                "verified": False,
                "evidence_basis": str(validation.get("reason") or "invalid_document_identity"),
            },
        })
        return base
    fetch = {**record, "mtf_document_validation": validation}
    extraction = research.extract_page_text(fetch)
    if extraction.get("status") != "extracted":
        base["candidate_status"] = str(extraction.get("error") or "extraction_failed")
        return base
    match, speaker = contribution_aware_match(target, extraction, body, validation)
    classification_extraction = dict(extraction)
    if speaker.get("verified"):
        metadata = dict(classification_extraction.get("metadata") or {})
        metadata["author"] = (
            str(metadata.get("author") or "") or "Margaret Thatcher"
        )
        classification_extraction["metadata"] = metadata
    classified = research.classify_candidate(
        target,
        url=url,
        extraction=classification_extraction,
        match=match,
        fetch_status="fetched",
    )
    accepted = bool(
        classified.get("classification") == "strong_primary_evidence"
        and classified.get("accepted_as_evidence")
        and speaker.get("verified")
        and match.get("match_type") in {"exact_quotation", "recorded_variant"}
    )
    passage = str(match.get("supporting_passage") or "")
    base.update({
        "candidate_status": "inspected",
        "local_file_sha256": str(record.get("local_archive_file_sha256") or ""),
        "match_type": _normalised_match_type(str(match.get("match_type") or "none")),
        "raw_match_type": str(match.get("match_type") or "none"),
        "wording_similarity": match.get("wording_similarity", 0.0),
        "supporting_passage": passage,
        "supporting_passage_sha256": sha256_bytes(passage.encode("utf-8")),
        "surrounding_context": str(match.get("surrounding_context") or ""),
        "speaker_author_evidence": speaker,
        "document_event_evidence": str(validation.get("title") or ""),
        "document_date_evidence": str(validation.get("date") or ""),
        "document_identity_evidence": {
            "document_id": document_id,
            "canonical_url": str(validation.get("canonical_url") or ""),
            "declared_canonical_url": str(validation.get("declared_canonical_url") or ""),
            "publisher": "Margaret Thatcher Foundation",
            "article_text_sha256": str(validation.get("article_text_sha256") or ""),
        },
        "candidate_classification": str(classified.get("classification") or "no_support"),
        "candidate_classification_reason": str(classified.get("decision_reason") or ""),
        "accepted_as_primary_evidence": accepted,
        "actual_regular_file_verified": True,
        "cross_speaker_join_rejected": bool(speaker.get("cross_speaker_join_rejected")),
        "confidence": (
            "high" if accepted else "medium"
            if match.get("match_type") not in {"none", "distinctive_fragment_only"}
            else "low"
        ),
    })
    categories, proposed, proposed_unblock, unblock_reason = classify_changes(
        target, base, base["current_values"]
    )
    base.update({
        "proposed_values": proposed,
        "proposed_change_category": categories,
        "manual_review_still_required": categories != [CATEGORY_NO_CHANGE],
        "proposed_unblock": proposed_unblock,
        "proposed_unblock_rationale": unblock_reason,
    })
    return base


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        1 if candidate.get("candidate_evidence_stale") else 0,
        0 if candidate.get("accepted_as_primary_evidence") else 1,
        {"exact quotation": 0, "recorded variant": 1, "partial/assembled wording": 2,
         "similar sentiment only": 3, "no support": 4}.get(str(candidate.get("match_type")), 5),
        0 if candidate.get("speaker_author_evidence", {}).get("verified") else 1,
        int(str(candidate.get("candidate_mtf_document_id") or "999999999")),
    )


def reassess_blocked(
    targets: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    by_quote: dict[str, list[Mapping[str, Any]]] = {}
    for candidate in candidates:
        by_quote.setdefault(str(candidate["quote_id"]), []).append(candidate)
    output = []
    for target in targets:
        if target.get("current_gate_status") != "blocked":
            continue
        rows = sorted(by_quote.get(str(target["quote_id"]), []), key=_candidate_sort_key)
        best = rows[0] if rows else None
        review = target.get("current_gate_review")
        reason = str(review.get("reason") or "") if isinstance(review, Mapping) else ""
        output.append({
            "quote_id": target["quote_id"],
            "quotation_text": target["quotation_text"],
            "current_gate_disposition": target["current_gate_disposition"],
            "current_gate_reason": reason,
            "best_newly_found_local_evidence": ({
                "candidate_mtf_document_id": best.get("candidate_mtf_document_id"),
                "canonical_public_url": best.get("canonical_public_url"),
                "match_type": best.get("match_type"),
                "supporting_passage": best.get("supporting_passage"),
                "supporting_passage_sha256": best.get("supporting_passage_sha256"),
                "local_file_sha256": best.get("local_file_sha256"),
                "candidate_evidence_identity_status": best.get(
                    "candidate_evidence_identity_status", "not_reclassified"
                ),
                "confidence": best.get("confidence"),
            } if best else None),
            "speaker_attribution_verified": bool(
                best and best.get("speaker_author_evidence", {}).get("verified")
            ),
            "exact_or_acceptable_primary_variant_verified": bool(
                best and best.get("accepted_as_primary_evidence")
                and best.get("match_type") in {"exact quotation", "recorded variant"}
            ),
            "supporting_context_date_source_identity_sufficient": bool(
                best and best.get("accepted_as_primary_evidence")
                and best.get("surrounding_context")
                and best.get("document_date_evidence")
                and best.get("document_event_evidence")
                and best.get("canonical_public_url")
            ),
            "current_blocking_reason_resolved": bool(best and best.get("proposed_unblock")),
            "blocking_reason_assessment": (
                str(best.get("proposed_unblock_rationale")) if best
                else "no local candidate was discovered"
            ),
            "proposed_unblock": bool(best and best.get("proposed_unblock")),
            "manual_review_still_required": True,
        })
    return output


def proposed_changes(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for candidate in candidates:
        categories = list(candidate.get("proposed_change_category") or [])
        for category in categories:
            if category == CATEGORY_NO_CHANGE:
                continue
            output.append({
                "quote_id": candidate["quote_id"],
                "quotation_text": candidate["quotation_text"],
                "candidate_mtf_document_id": candidate["candidate_mtf_document_id"],
                "canonical_public_url": candidate["canonical_public_url"],
                "proposed_change_category": category,
                "current_values": candidate["current_values"],
                "proposed_values": candidate["proposed_values"],
                "confidence": candidate["confidence"],
                "candidate_evidence_identity_status": candidate.get(
                    "candidate_evidence_identity_status", "not_reclassified"
                ),
                "proposed_unblock": candidate["proposed_unblock"],
                "manual_review_required": True,
                "advisory_only_not_applied": True,
            })
    output.sort(key=lambda row: (
        row["quote_id"], row["proposed_change_category"],
        int(row["candidate_mtf_document_id"] or "999999999"),
    ))
    return output


def _quote_count(candidates: Sequence[Mapping[str, Any]], predicate: Any) -> int:
    return len({str(row["quote_id"]) for row in candidates if predicate(row)})


def build_summary(
    targets: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    input_hashes_stable: bool,
    completed_at: str,
) -> dict[str, Any]:
    stable = inventory_is_stable(before, after)
    categories = lambda row, value: value in set(row.get("proposed_change_category", []))
    useful = lambda row: bool(
        set(row.get("proposed_change_category", [])) & IMPROVEMENT_CATEGORIES
    )
    return {
        "schema_version": 2,
        "record_kind": "historical_context_local_corpus_reaudit_summary",
        "programme_version": PROGRAMME_VERSION,
        "completed_at": completed_at,
        "eligible_quotations_assessed": len(targets),
        "quotations_with_at_least_one_local_candidate": len({row["quote_id"] for row in candidates}),
        "quotations_with_verified_useful_evidence": _quote_count(
            candidates,
            lambda row: row.get("accepted_as_primary_evidence")
            and not row.get("candidate_evidence_stale"),
        ),
        "exact_primary_matches": _quote_count(
            candidates, lambda row: row.get("accepted_as_primary_evidence") and row.get("match_type") == "exact quotation"
        ),
        "primary_variants": _quote_count(
            candidates, lambda row: row.get("accepted_as_primary_evidence") and row.get("match_type") == "recorded variant"
        ),
        "proposed_date_improvements": _quote_count(
            candidates, lambda row: categories(row, CATEGORY_DATE_CORRECTION)
        ),
        "proposed_event_source_improvements": _quote_count(
            candidates, lambda row: categories(row, CATEGORY_EVENT_CORRECTION)
        ),
        "proposed_locator_source_identity_improvements": _quote_count(
            candidates, lambda row: categories(row, CATEGORY_LOCATOR_CORRECTION)
        ),
        "proposed_wording_corrections": _quote_count(
            candidates, lambda row: categories(row, CATEGORY_WORDING_CORRECTION)
        ),
        "additional_primary_occurrences": _quote_count(
            candidates, lambda row: categories(row, CATEGORY_ADDITIONAL_OCCURRENCE)
        ),
        "exact_excerpt_confirmations": _quote_count(
            candidates,
            lambda row: categories(row, CATEGORY_EXACT_EXCERPT_CONFIRMATION),
        ),
        "attribution_or_noncontiguous_match_reviews": _quote_count(
            candidates, lambda row: categories(row, CATEGORY_NEUTRAL_MATCH_REVIEW)
        ),
        "genuine_contradiction_candidates": _quote_count(
            candidates, lambda row: categories(row, CATEGORY_CONTRADICTION)
        ),
        "blocked_quotes_reassessed": len(blocked),
        "blocked_quotes_with_proposed_unblock_true": sum(bool(row.get("proposed_unblock")) for row in blocked),
        "proposed_unblock_quote_ids": sorted(
            str(row["quote_id"]) for row in blocked if row.get("proposed_unblock")
        ),
        "unresolved_quotes_with_improved_evidence": _quote_count(
            candidates, lambda row: row.get("current_unresolved_status") and useful(row)
        ),
        "archive_inventory_before": dict(before),
        "archive_inventory_after": dict(after),
        "archive_inventory_stable": stable,
        "authoritative_inputs_stable": input_hashes_stable,
        "reproducible_against_one_stable_inventory": stable and input_hashes_stable,
        "run_finality": (
            "final_advisory_package" if stable and input_hashes_stable
            else "non_reproducible_incomplete_advisory_package"
        ),
        "network_attempt_count": 0,
        "external_provider_call_count": 0,
        "canonical_write_count": 0,
        "private_root_not_recorded": True,
        "advisory_only": True,
    }


def render_report(summary: Mapping[str, Any]) -> str:
    before = summary["archive_inventory_before"]
    after = summary["archive_inventory_after"]
    unblock_ids = summary["proposed_unblock_quote_ids"]
    return "\n".join((
        "# Local historical-context corpus re-audit",
        "",
        f"Programme: `{PROGRAMME_VERSION}`",
        "",
        "This is a private, local-only advisory package. No evidence was admitted and no canonical data was changed.",
        "",
        "## Assessment",
        "",
        f"- Eligible quotations assessed: {summary['eligible_quotations_assessed']}",
        f"- Quotations with a local candidate: {summary['quotations_with_at_least_one_local_candidate']}",
        f"- Quotations with verified useful evidence: {summary['quotations_with_verified_useful_evidence']}",
        f"- Exact primary matches: {summary['exact_primary_matches']}",
        f"- Primary variants: {summary['primary_variants']}",
        f"- Proposed date improvements: {summary['proposed_date_improvements']}",
        f"- Proposed event/source improvements: {summary['proposed_event_source_improvements']}",
        f"- Proposed locator/source-identity improvements: {summary['proposed_locator_source_identity_improvements']}",
        f"- Proposed wording corrections: {summary['proposed_wording_corrections']}",
        f"- Additional verified primary occurrences: {summary['additional_primary_occurrences']}",
        f"- Exact excerpt confirmations: {summary['exact_excerpt_confirmations']}",
        f"- Attribution/non-contiguous match reviews: {summary['attribution_or_noncontiguous_match_reviews']}",
        f"- Genuine canonical-claim conflicts: {summary['genuine_contradiction_candidates']}",
        f"- Blocked quotations reassessed: {summary['blocked_quotes_reassessed']}",
        f"- Proposed unblock quotations: {summary['blocked_quotes_with_proposed_unblock_true']} ({', '.join(unblock_ids) if unblock_ids else 'none'})",
        f"- Unresolved quotations with improved evidence: {summary['unresolved_quotes_with_improved_evidence']}",
        "",
        "## Archive reproducibility boundary",
        "",
        f"- Before: {before['document_count']} documents, {before['total_bytes']} bytes, `{before['inventory_sha256']}`",
        f"- After: {after['document_count']} documents, {after['total_bytes']} bytes, `{after['inventory_sha256']}`",
        f"- Stable inventory: {str(summary['archive_inventory_stable']).lower()}",
        f"- Reproducible final advisory package: {str(summary['reproducible_against_one_stable_inventory']).lower()}",
        "",
        "## Safety",
        "",
        "Socket connection and DNS entry points were denied inside the process. The runner instantiated no cloud backend and recorded zero network, external-provider, and canonical-write operations.",
        "",
    ))


def assert_no_private_path_disclosure(values: Sequence[Any], *private_paths: Path) -> None:
    encoded = "\n".join(
        value if isinstance(value, str) else canonical_json_bytes(value).decode("utf-8")
        for value in values
    )
    for private in (str(path) for path in private_paths):
        if private and private in encoded:
            raise ReauditError("generated advisory output discloses a private absolute path")


def _source_run_file(source_run_dir: Path, filename: str) -> Path:
    if filename not in OUTPUT_FILENAMES:
        raise ReauditError(f"unrecognised source-run filename: {filename}")
    path = source_run_dir / filename
    if path.is_symlink() or not path.is_file():
        raise ReauditError(f"source run is missing a regular {filename}")
    if path.resolve(strict=True).parent != source_run_dir:
        raise ReauditError("source-run file escaped its directory")
    return path


def _source_run_snapshot(source_run_dir: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    for path in sorted(source_run_dir.iterdir(), key=lambda item: item.name):
        if path.is_symlink() or not path.is_file():
            raise ReauditError("source run contains a non-regular entry")
        output[path.name] = file_sha256(path)
    return output


def _normalised_existing_categories(candidate: Mapping[str, Any]) -> set[str]:
    return {
        LEGACY_CATEGORY_NAMES.get(str(value), str(value))
        for value in candidate.get("proposed_change_category", [])
    }


def _candidate_requires_identity_revalidation(candidate: Mapping[str, Any]) -> bool:
    categories = _normalised_existing_categories(candidate)
    return bool(
        candidate.get("accepted_as_primary_evidence")
        or candidate.get("proposed_unblock")
        or candidate.get("candidate_classification") == "contradictory_evidence"
        or any(category != CATEGORY_NO_CHANGE for category in categories)
    )


def _candidate_requires_supporting_passage_revalidation(
    candidate: Mapping[str, Any]
) -> bool:
    match_type = str(candidate.get("match_type") or "")
    return bool(
        (
            candidate.get("accepted_as_primary_evidence")
            or candidate.get("proposed_unblock")
        )
        and match_type in {
            "exact quotation", "recorded variant",
            "exact_quotation", "recorded_variant",
        }
    )


def _stale_identity_result(reason: str) -> dict[str, Any]:
    return {
        "candidate_evidence_identity_status": "stale",
        "candidate_evidence_stale": True,
        "candidate_evidence_identity_reason": reason,
    }


def revalidate_candidate_identity(
    mirror: LocalArchiveMirror, candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Revalidate a selected ledger candidate without searching or rematching text."""
    if not _candidate_requires_identity_revalidation(candidate):
        return {
            "candidate_evidence_identity_status": "not_selected_for_revalidation",
            "candidate_evidence_stale": False,
            "candidate_evidence_identity_reason": (
                "candidate was not selected for positive or admission consideration"
            ),
        }
    document_id = str(candidate.get("candidate_mtf_document_id") or "")
    url = str(candidate.get("canonical_public_url") or "")
    recorded_hash = str(candidate.get("local_file_sha256") or "")
    recorded_identity = candidate.get("document_identity_evidence")
    if (
        not document_id.isdigit()
        or _document_id_from_value(url) != document_id
        or not re.fullmatch(r"[0-9a-f]{64}", recorded_hash)
        or (
            isinstance(recorded_identity, Mapping)
            and str(recorded_identity.get("document_id") or document_id) != document_id
        )
    ):
        return _stale_identity_result("recorded_candidate_identity_is_invalid")
    try:
        record = mirror.read(url)
    except Exception:
        return _stale_identity_result("candidate_file_is_missing_or_unsafe")
    if not record or record.get("status") != "fetched":
        return _stale_identity_result("candidate_file_is_missing_or_unreadable")
    current_hash = str(record.get("local_archive_file_sha256") or "")
    if current_hash != recorded_hash:
        return _stale_identity_result("candidate_file_sha256_changed")
    validation = research.inspect_mtf_document(
        url, str(record.get("content_type") or ""), bytes(record.get("body") or b"")
    )
    if (
        not validation.get("valid")
        or str(validation.get("document_number") or "") != document_id
        or _document_id_from_value(validation.get("canonical_url")) != document_id
        or _document_id_from_value(validation.get("declared_canonical_url")) != document_id
    ):
        return _stale_identity_result("candidate_mtf_document_identity_changed")
    passage_revalidation_required = (
        _candidate_requires_supporting_passage_revalidation(candidate)
    )
    if passage_revalidation_required:
        passage = str(candidate.get("supporting_passage") or "")
        passage_hash = str(candidate.get("supporting_passage_sha256") or "")
        if not passage.strip():
            return _stale_identity_result("supporting_passage_is_empty")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", passage_hash):
            return _stale_identity_result("supporting_passage_sha256_is_invalid")
        if sha256_bytes(passage.encode("utf-8")) != passage_hash.casefold():
            return _stale_identity_result("supporting_passage_sha256_mismatch")
        extraction = research.extract_page_text({
            **record,
            "mtf_document_validation": validation,
        })
        if extraction.get("status") != "extracted":
            return _stale_identity_result("candidate_document_text_is_unreadable")
        if not _contains_token_sequence(
            str(extraction.get("text") or ""), passage
        ):
            return _stale_identity_result(
                "supporting_passage_absent_from_current_document"
            )
    return {
        "candidate_evidence_identity_status": "valid",
        "candidate_evidence_stale": False,
        "candidate_evidence_identity_reason": (
            "recorded MTF document, file SHA-256, and required supporting passage still match"
            if passage_revalidation_required
            else "recorded MTF document identity and local file SHA-256 still match"
        ),
    }


def _reclassify_candidate(
    mirror: LocalArchiveMirror,
    target: Mapping[str, Any],
    source_candidate: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(source_candidate))
    candidate.update({
        "quotation_text": target["quotation_text"],
        "current_gate_disposition": target["current_gate_disposition"],
        "current_gate_status": target["current_gate_status"],
        "current_unresolved_status": target["current_unresolved_status"],
        "current_values": current_values(target),
        **revalidate_candidate_identity(mirror, candidate),
    })
    candidate["recorded_accepted_as_primary_evidence"] = bool(
        source_candidate.get("accepted_as_primary_evidence")
    )
    if candidate["candidate_evidence_stale"]:
        candidate["accepted_as_primary_evidence"] = False
    categories, proposed, proposed_unblock, unblock_reason = classify_changes(
        target, candidate, candidate["current_values"]
    )
    candidate.update({
        "proposed_values": proposed,
        "proposed_change_category": categories,
        "manual_review_still_required": (
            candidate["candidate_evidence_stale"]
            or categories != [CATEGORY_NO_CHANGE]
        ),
        "proposed_unblock": proposed_unblock,
        "proposed_unblock_rationale": unblock_reason,
        "classification_rerun_without_discovery": True,
    })
    return candidate


def _build_reclassification_summary(
    targets: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    source_before: Mapping[str, Any],
    source_after: Mapping[str, Any],
    *,
    input_hashes_stable: bool,
    completed_at: str,
    source_candidate_ledger_sha256: str,
) -> dict[str, Any]:
    summary = build_summary(
        targets,
        candidates,
        blocked,
        source_before,
        source_after,
        input_hashes_stable=input_hashes_stable,
        completed_at=completed_at,
    )
    selected = [
        row for row in candidates
        if row.get("candidate_evidence_identity_status") in {"valid", "stale"}
    ]
    positive = [
        row for row in candidates
        if row.get("recorded_accepted_as_primary_evidence")
    ]
    summary.update({
        "reclassification_mode": True,
        "source_candidate_ledger_sha256": source_candidate_ledger_sha256,
        "source_run_inventory_was_unstable": not inventory_is_stable(
            source_before, source_after
        ),
        "candidate_level_evidence_identities_revalidated": True,
        "candidate_identity_revalidation_required_count": len(selected),
        "candidate_identity_valid_count": sum(
            row.get("candidate_evidence_identity_status") == "valid"
            for row in selected
        ),
        "stale_candidate_count": sum(
            row.get("candidate_evidence_identity_status") == "stale"
            for row in selected
        ),
        "recorded_positive_candidate_count": len(positive),
        "positive_candidates_remaining_valid": sum(
            row.get("candidate_evidence_identity_status") == "valid"
            for row in positive
        ),
        "positive_candidates_stale": sum(
            row.get("candidate_evidence_identity_status") == "stale"
            for row in positive
        ),
        "negative_no_hit_conclusions_provisional": True,
        "search_index_discovery_call_count": 0,
        "archive_inventory_rescan_performed": False,
        "original_source_run_write_count": 0,
        "run_finality": (
            "candidate_revalidated_advisory_package_with_provisional_negatives"
        ),
    })
    return summary


def render_reclassification_report(summary: Mapping[str, Any]) -> str:
    return "\n".join((
        "# Existing-package historical-context reclassification",
        "",
        f"Programme: `{PROGRAMME_VERSION}`",
        "",
        "This private advisory package reused the recorded candidate ledger and reran classification only. No quotation discovery or archive inventory scan was performed.",
        "",
        "## Candidate evidence boundary",
        "",
        f"- Source run inventory was unstable: {str(summary['source_run_inventory_was_unstable']).lower()}",
        f"- Candidate-level evidence identities revalidated: {str(summary['candidate_level_evidence_identities_revalidated']).lower()}",
        f"- Candidates selected for identity revalidation: {summary['candidate_identity_revalidation_required_count']}",
        f"- Candidate identities still valid: {summary['candidate_identity_valid_count']}",
        f"- Stale candidates: {summary['stale_candidate_count']}",
        f"- Positive candidates remaining valid: {summary['positive_candidates_remaining_valid']}",
        f"- Negative/no-hit conclusions remain provisional: {str(summary['negative_no_hit_conclusions_provisional']).lower()}",
        "",
        "## Revised advisory classification",
        "",
        f"- Verified useful evidence quotations: {summary['quotations_with_verified_useful_evidence']}",
        f"- Proposed date corrections/fills: {summary['proposed_date_improvements']}",
        f"- Proposed event/source corrections/fills: {summary['proposed_event_source_improvements']}",
        f"- Proposed locator/source-identity corrections: {summary['proposed_locator_source_identity_improvements']}",
        f"- Proposed verified-text corrections: {summary['proposed_wording_corrections']}",
        f"- Additional verified primary occurrences: {summary['additional_primary_occurrences']}",
        f"- Exact excerpt confirmations: {summary['exact_excerpt_confirmations']}",
        f"- Attribution/non-contiguous match reviews: {summary['attribution_or_noncontiguous_match_reviews']}",
        f"- Genuine canonical-claim conflicts: {summary['genuine_contradiction_candidates']}",
        "",
        "## Safety",
        "",
        "The source package was read only. Socket and DNS entry points were denied; search/index discovery calls, external-provider calls, canonical writes, and source-package writes were all zero.",
        "",
    ))


def reclassify_existing(
    project_root: Path, source_run_dir: Path, output_dir: Path
) -> dict[str, Any]:
    """Cheaply reclassify one completed package without rerunning discovery."""
    project_root = resolve_project_root(project_root)
    archive_root, source_run_dir, output_dir = resolve_reclassification_inputs(
        project_root, source_run_dir, output_dir
    )
    source_snapshot = _source_run_snapshot(source_run_dir)
    source_ledger_sha256 = source_snapshot.get(
        "corpus_reaudit_candidates.json", ""
    )
    candidate_path = _source_run_file(
        source_run_dir, "corpus_reaudit_candidates.json"
    )
    source_candidate_document = read_json(candidate_path)
    source_before = read_json(
        _source_run_file(source_run_dir, "archive_inventory_before.json")
    )
    source_after = read_json(
        _source_run_file(source_run_dir, "archive_inventory_after.json")
    )
    if not isinstance(source_candidate_document, Mapping) or not isinstance(
        source_candidate_document.get("records"), list
    ):
        raise ReauditError("source candidate ledger is malformed")
    source_records = source_candidate_document["records"]
    if not all(isinstance(row, Mapping) for row in source_records):
        raise ReauditError("source candidate ledger contains a malformed record")

    input_before = authoritative_hashes(project_root)
    mirror = LocalArchiveMirror(archive_root, maximum_bytes=MAXIMUM_DOCUMENT_BYTES)
    with deny_network():
        derived = derive_eligible_targets(
            project_root, prepare_search_strategy=False
        )
        targets = derived["targets"]
        targets_by_id = {str(row["quote_id"]): row for row in targets}
        candidates = []
        for source_candidate in source_records:
            quote_id = str(source_candidate.get("quote_id") or "")
            target = targets_by_id.get(quote_id)
            if target is None:
                raise ReauditError(
                    "source candidate no longer maps to an authoritative quotation"
                )
            candidates.append(
                _reclassify_candidate(mirror, target, source_candidate)
            )

    input_after = authoritative_hashes(project_root)
    assert_authoritative_inputs_unchanged(input_before, input_after)
    blocked = reassess_blocked(targets, candidates)
    changes = proposed_changes(candidates)
    summary = _build_reclassification_summary(
        targets,
        candidates,
        blocked,
        source_before,
        source_after,
        input_hashes_stable=input_before == input_after,
        completed_at=utc_now(),
        source_candidate_ledger_sha256=source_ledger_sha256,
    )
    candidate_document = {
        "schema_version": 2,
        "record_kind": "historical_context_local_corpus_reaudit_candidates",
        "programme_version": PROGRAMME_VERSION,
        "eligible_quote_count": len(targets),
        "candidate_count": len(candidates),
        "source_candidate_ledger_sha256": source_ledger_sha256,
        "source_candidate_ledger_referenced_by_sha256": True,
        "source_candidate_records_reused_without_discovery": True,
        "records": candidates,
        "advisory_only": True,
        "private_root_not_recorded": True,
    }
    blocked_document = {
        "schema_version": 2,
        "record_kind": "blocked_historical_context_quote_reassessment",
        "semantic_gate_policy_version": SEMANTIC_GATE_POLICY_VERSION,
        "blocked_quote_count": len(blocked),
        "records": blocked,
        "advisory_only": True,
    }
    change_document = {
        "schema_version": 2,
        "record_kind": "proposed_historical_data_changes",
        "change_count": len(changes),
        "records": changes,
        "categories": CATEGORY_DESCRIPTIONS,
        "advisory_only_not_applied": True,
    }
    report = render_reclassification_report(summary)
    assert_no_private_path_disclosure(
        [candidate_document, blocked_document, change_document, summary, report],
        archive_root,
        source_run_dir,
        output_dir,
    )
    if source_snapshot != _source_run_snapshot(source_run_dir):
        raise ReauditError("source run changed during reclassification")
    try:
        write_json(output_dir, "corpus_reaudit_candidates.json", candidate_document)
        write_json(output_dir, "blocked_quote_reassessment.json", blocked_document)
        write_json(
            output_dir, "proposed_historical_data_changes.json", change_document
        )
        write_json(output_dir, "corpus_reaudit_summary.json", summary)
        write_report(output_dir, report)
    except Exception as exc:
        try:
            _remove_incomplete_output_package(output_dir)
        except ReauditError as cleanup_error:
            raise cleanup_error from exc
        raise
    return summary


def execute(project_root: Path) -> dict[str, Any]:
    project_root = resolve_project_root(project_root)
    archive_root, run_dir = resolve_private_inputs(project_root)
    started_at = utc_now()
    input_before = authoritative_hashes(project_root)
    mirror = LocalArchiveMirror(archive_root, maximum_bytes=MAXIMUM_DOCUMENT_BYTES)
    before_inventory = inventory_document(
        mirror.inventory(), phase="before", timestamp=started_at
    )
    require_usable_inventory(before_inventory)
    write_json(run_dir, "archive_inventory_before.json", before_inventory)

    with deny_network():
        derived = derive_eligible_targets(project_root)
        targets = derived["targets"]
        if len(targets) != EXPECTED_ELIGIBLE_CORPUS_SIZE:
            raise ReauditError("eligible target derivation did not produce exactly 611 quotations")
        index = LocalMTFDocumentIndex(
            mirror, maximum_results=MAXIMUM_CANDIDATES_PER_QUOTE
        )
        candidates: list[dict[str, Any]] = []
        for target in targets:
            discovery = index.discover(
                str(target["quotation_text"]),
                variants=target.get("local_discovery_phrases", []),
            )
            seen_documents: set[str] = set()
            for result in discovery.get("results", [])[:MAXIMUM_CANDIDATES_PER_QUOTE]:
                document_id = str(result.get("document_id") or "")
                if not document_id or document_id in seen_documents:
                    continue
                seen_documents.add(document_id)
                candidates.append(verify_candidate(mirror, target, result))

    after_inventory = inventory_document(
        mirror.inventory(), phase="after", timestamp=utc_now()
    )
    input_after = authoritative_hashes(project_root)
    input_stable = input_before == input_after
    if not input_stable:
        assert_authoritative_inputs_unchanged(input_before, input_after)
    blocked = reassess_blocked(targets, candidates)
    changes = proposed_changes(candidates)
    summary = build_summary(
        targets, candidates, blocked, before_inventory, after_inventory,
        input_hashes_stable=input_stable, completed_at=utc_now(),
    )
    candidate_document = {
        "schema_version": 2,
        "record_kind": "historical_context_local_corpus_reaudit_candidates",
        "programme_version": PROGRAMME_VERSION,
        "eligible_quote_count": len(targets),
        "candidate_cap_per_quote": MAXIMUM_CANDIDATES_PER_QUOTE,
        "candidate_count": len(candidates),
        "records": candidates,
        "advisory_only": True,
        "private_root_not_recorded": True,
    }
    blocked_document = {
        "schema_version": 1,
        "record_kind": "blocked_historical_context_quote_reassessment",
        "semantic_gate_policy_version": SEMANTIC_GATE_POLICY_VERSION,
        "blocked_quote_count": len(blocked),
        "records": blocked,
        "advisory_only": True,
    }
    change_document = {
        "schema_version": 2,
        "record_kind": "proposed_historical_data_changes",
        "change_count": len(changes),
        "records": changes,
        "categories": CATEGORY_DESCRIPTIONS,
        "advisory_only_not_applied": True,
    }
    report = render_report(summary)
    assert_no_private_path_disclosure(
        [before_inventory, after_inventory, candidate_document, blocked_document,
         change_document, summary, report],
        archive_root,
        run_dir,
    )
    write_json(run_dir, "archive_inventory_after.json", after_inventory)
    write_json(run_dir, "corpus_reaudit_candidates.json", candidate_document)
    write_json(run_dir, "blocked_quote_reassessment.json", blocked_document)
    write_json(run_dir, "proposed_historical_data_changes.json", change_document)
    write_json(run_dir, "corpus_reaudit_summary.json", summary)
    write_report(run_dir, report)
    return summary


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--execute", action="store_true",
        help="perform the local-only advisory run using the two required environment variables",
    )
    mode.add_argument(
        "--reclassify-existing",
        metavar="SOURCE_RUN_DIR",
        type=Path,
        help="reuse one completed private candidate ledger without discovery",
    )
    parser.add_argument(
        "--output-dir",
        metavar="NEW_RUN_DIR",
        type=Path,
        help="new empty mode-0700 private directory for reclassified outputs",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if args.reclassify_existing is not None and args.output_dir is None:
        print(
            "NOT STARTED: --reclassify-existing requires --output-dir",
            file=sys.stderr,
        )
        return 2
    if args.reclassify_existing is None and args.output_dir is not None:
        print(
            "NOT STARTED: --output-dir is only valid with --reclassify-existing",
            file=sys.stderr,
        )
        return 2
    if not args.execute and args.reclassify_existing is None:
        print("NOT STARTED: pass --execute after preparing the required private directories")
        return 2
    try:
        if args.reclassify_existing is not None:
            summary = reclassify_existing(
                Path(__file__).resolve().parent,
                args.reclassify_existing,
                args.output_dir,
            )
        else:
            summary = execute(Path(__file__).resolve().parent)
    except Exception as exc:
        print(f"LOCAL CORPUS RE-AUDIT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": summary["run_finality"],
        "eligible_quotations_assessed": summary["eligible_quotations_assessed"],
        "verified_useful_evidence": summary["quotations_with_verified_useful_evidence"],
        "proposed_unblock_quote_ids": summary["proposed_unblock_quote_ids"],
        "archive_inventory_stable": summary["archive_inventory_stable"],
        "reclassification_mode": bool(summary.get("reclassification_mode")),
        "stale_candidate_count": summary.get("stale_candidate_count", 0),
        "positive_candidates_remaining_valid": summary.get(
            "positive_candidates_remaining_valid"
        ),
        "network_attempt_count": 0,
    }, sort_keys=True))
    if summary.get("reclassification_mode"):
        return 0
    return 0 if summary["reproducible_against_one_stable_inventory"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
