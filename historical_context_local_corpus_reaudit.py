#!/usr/bin/env python3
"""Bounded, local-only advisory re-audit of the eligible quotation corpus.

This runner deliberately orchestrates the maintained historical-context modules
instead of implementing another search or evidence-admission framework.  It
never mutates canonical data and never constructs a network search backend.
"""
from __future__ import annotations

import argparse
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


PROGRAMME_VERSION = "historical-context-local-corpus-reaudit-v1"
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


def resolve_private_inputs(project_root: Path) -> tuple[Path, Path]:
    """Resolve and validate the two operator-supplied private directories."""
    archive_raw = os.environ.get(LOCAL_ARCHIVE_ROOT_ENV, "").strip()
    run_raw = os.environ.get(RUN_DIRECTORY_ENV, "").strip()
    if not archive_raw:
        raise ReauditError(f"{LOCAL_ARCHIVE_ROOT_ENV} must name the local archive root")
    if not run_raw:
        raise ReauditError(f"{RUN_DIRECTORY_ENV} must name a new private run directory")
    archive = Path(archive_raw)
    run_dir = Path(run_raw)
    if not archive.is_absolute():
        raise ReauditError(f"{LOCAL_ARCHIVE_ROOT_ENV} must be an absolute path")
    if not run_dir.is_absolute():
        raise ReauditError(f"{RUN_DIRECTORY_ENV} must be an absolute path")
    try:
        archive = archive.resolve(strict=True)
        run_dir = run_dir.resolve(strict=True)
    except OSError as exc:
        raise ReauditError("a configured private directory does not exist") from exc
    if not archive.is_dir():
        raise ReauditError(f"{LOCAL_ARCHIVE_ROOT_ENV} is not a directory")
    if not run_dir.is_dir():
        raise ReauditError(f"{RUN_DIRECTORY_ENV} is not a directory")
    if run_dir.stat().st_mode & 0o777 != 0o700:
        raise ReauditError(f"{RUN_DIRECTORY_ENV} must have mode 0700")
    if any(run_dir.iterdir()):
        raise ReauditError(f"{RUN_DIRECTORY_ENV} must be empty before the run")
    for worktree in _registered_worktrees(project_root):
        if run_dir == worktree or _is_relative_to(run_dir, worktree):
            raise ReauditError(f"{RUN_DIRECTORY_ENV} must be outside every Git worktree")
    if run_dir == archive or _is_relative_to(run_dir, archive):
        raise ReauditError(f"{RUN_DIRECTORY_ENV} must be outside the local archive")
    return archive, run_dir


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


def current_values(target: Mapping[str, Any]) -> dict[str, Any]:
    packet = target["current_packet"]
    role = target["current_source_role"]
    known_ids = {_document_id_from_value(packet.get("stable_locator"))}
    direct_urls: set[str] = set()
    inspected_ids: set[str] = set()
    inspected_hashes: set[str] = set()
    for row in _source_rows(role):
        for key in ("canonical_url", "public_url", "source_url", "source_title"):
            document_id = _document_id_from_value(row.get(key))
            if document_id:
                known_ids.add(document_id)
            value = str(row.get(key) or "")
            if "margaretthatcher.org/document/" in value and value.startswith(("http://", "https://")):
                direct_urls.add(value)
        if row.get("page_independently_inspected") is True:
            document_id = ""
            for key in ("canonical_url", "public_url", "source_url", "source_title"):
                document_id = document_id or _document_id_from_value(row.get(key))
            if document_id:
                inspected_ids.add(document_id)
            for key in ("page_sha256", "page_text_sha256"):
                value = str(row.get(key) or "")
                if re.fullmatch(r"[0-9a-f]{64}", value):
                    inspected_hashes.add(value)
    known_ids.discard("")
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
        "known_mtf_document_ids": sorted(known_ids, key=int),
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
    """Return separate advisory A-I categories without applying any of them."""
    categories: list[str] = []
    proposed: dict[str, Any] = {}
    document_id = str(candidate.get("candidate_mtf_document_id") or "")
    match_type = str(candidate.get("match_type") or "")
    strong = bool(
        candidate.get("accepted_as_primary_evidence")
        and candidate.get("speaker_author_evidence", {}).get("verified")
        and match_type in {"exact quotation", "recorded variant"}
    )
    if candidate.get("cross_speaker_join_rejected") or (
        match_type in {"partial/assembled wording", "similar sentiment only"}
        and candidate.get("candidate_classification") == "contradictory_evidence"
    ):
        categories.append("H")
        proposed["historical_context_confidence"] = "possible downgrade; manual review required"
    if strong:
        already_inspected = document_id in set(
            current.get("independently_inspected_mtf_document_ids", [])
        )
        if not already_inspected:
            categories.append("A")
            proposed["evidence"] = "retain inspected local primary passage and hashes for review"
        current_date = _normalised_date(current.get("date"))
        candidate_date = _normalised_date(candidate.get("document_date_evidence"))
        same_identity = (
            not current.get("known_mtf_document_ids")
            or document_id in set(current.get("known_mtf_document_ids", []))
        )
        if candidate_date.get("known") and same_identity:
            current_iso = str(current_date.get("iso_date") or "")
            candidate_iso = str(candidate_date.get("iso_date") or "")
            improves_precision = (
                not current_date.get("known")
                or (current_date.get("precision") != "day" and candidate_date.get("precision") == "day")
            )
            differs = bool(current_iso and candidate_iso and current_iso != candidate_iso)
            if improves_precision or differs:
                categories.append("D")
                proposed["date"] = candidate_iso or candidate_date.get("raw")
        event = str(candidate.get("document_event_evidence") or "")
        current_event = str(current.get("source_event") or "")
        if event and same_identity and (
            not current_event or not _event_equivalent(current_event, event)
        ):
            categories.append("C")
            proposed["source_event"] = event
        known_ids = set(current.get("known_mtf_document_ids", []))
        direct_urls = " ".join(current.get("direct_mtf_public_urls", []))
        if not known_ids or (document_id in known_ids and document_id not in direct_urls):
            categories.append("E")
            proposed["stable_locator"] = f"Margaret Thatcher Foundation Document {document_id}"
            proposed["canonical_public_url"] = candidate.get("canonical_public_url")
        passage = str(candidate.get("supporting_passage") or "")
        verified_text = str(current.get("verified_text") or "")
        quotation_text = str(current.get("quotation_text") or "")
        if (
            same_identity
            and passage
            and verified_text
            and word_tokens(verified_text) != word_tokens(quotation_text)
        ):
            comparison = compare_primary_wording(passage, quotation_text)
            if comparison.get("classification") == "exact_primary_wording":
                categories.append("B")
                proposed["verified_text"] = quotation_text
        speaker = str(current.get("speaker") or "")
        if speaker and "thatcher" not in speaker.casefold():
            categories.append("F")
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
            categories.append("G")
    if not categories:
        categories = ["I"]
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
        "proposed_change_category": ["I"],
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
        "manual_review_still_required": categories != ["I"],
        "proposed_unblock": proposed_unblock,
        "proposed_unblock_rationale": unblock_reason,
    })
    return base


def _candidate_sort_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
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
            if category == "I":
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
    useful = lambda row: any(value != "I" for value in row.get("proposed_change_category", []))
    return {
        "schema_version": 1,
        "record_kind": "historical_context_local_corpus_reaudit_summary",
        "programme_version": PROGRAMME_VERSION,
        "completed_at": completed_at,
        "eligible_quotations_assessed": len(targets),
        "quotations_with_at_least_one_local_candidate": len({row["quote_id"] for row in candidates}),
        "quotations_with_verified_useful_evidence": _quote_count(candidates, useful),
        "exact_primary_matches": _quote_count(
            candidates, lambda row: row.get("accepted_as_primary_evidence") and row.get("match_type") == "exact quotation"
        ),
        "primary_variants": _quote_count(
            candidates, lambda row: row.get("accepted_as_primary_evidence") and row.get("match_type") == "recorded variant"
        ),
        "proposed_date_improvements": _quote_count(candidates, lambda row: categories(row, "D")),
        "proposed_event_source_improvements": _quote_count(candidates, lambda row: categories(row, "C")),
        "proposed_locator_source_identity_improvements": _quote_count(candidates, lambda row: categories(row, "E")),
        "proposed_wording_corrections": _quote_count(candidates, lambda row: categories(row, "B")),
        "contradictory_downgrade_candidates": _quote_count(candidates, lambda row: categories(row, "H")),
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
        f"- Contradictory/downgrade candidates: {summary['contradictory_downgrade_candidates']}",
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


def assert_no_private_path_disclosure(
    values: Sequence[Any], archive_root: Path, run_dir: Path
) -> None:
    encoded = "\n".join(
        value if isinstance(value, str) else canonical_json_bytes(value).decode("utf-8")
        for value in values
    )
    for private in (str(archive_root), str(run_dir)):
        if private and private in encoded:
            raise ReauditError("generated advisory output discloses a private absolute path")


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
        "schema_version": 1,
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
        "schema_version": 1,
        "record_kind": "proposed_historical_data_changes",
        "change_count": len(changes),
        "records": changes,
        "categories": {
            "A": "stronger evidence, no public-data change",
            "B": "wording/variant correction",
            "C": "event/source correction",
            "D": "date correction",
            "E": "stable-locator/source-identity correction",
            "F": "attribution/speaker correction",
            "G": "currently blocked quote potentially unblockable",
            "H": "contradictory evidence / possible downgrade",
        },
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
    parser.add_argument(
        "--execute", action="store_true",
        help="perform the local-only advisory run using the two required environment variables",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    if not args.execute:
        print("NOT STARTED: pass --execute after preparing the required private directories")
        return 2
    try:
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
        "network_attempt_count": 0,
    }, sort_keys=True))
    return 0 if summary["reproducible_against_one_stable_inventory"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
