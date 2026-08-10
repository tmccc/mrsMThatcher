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
from difflib import SequenceMatcher
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


PROGRAMME_VERSION = "historical-context-local-corpus-reaudit-v11"
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

_PLACEHOLDER_FORMS = frozenset({
    "unknown",
    "not known",
    "unavailable",
    "not available",
    "not available in primary sources",
    "none",
    "null",
    "n a",
    "na",
    "unspecified",
    "undated",
})
_NON_MEANINGFUL_VARIANT_TOKENS = frozenset({
    "a", "an", "and", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "was",
    "were", "with",
})


def _placeholder_form(value: Any) -> str:
    """Normalise case, spacing, and punctuation for placeholder detection."""
    return " ".join(word_tokens(str(value or "")))


def is_placeholder_text(value: Any) -> bool:
    """Return whether an optional historical value is an absence marker."""
    form = _placeholder_form(value)
    return bool(form and form in _PLACEHOLDER_FORMS)


def _optional_historical_text(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return "" if not text or is_placeholder_text(text) else text


def _reviewed_non_substantive_variant(
    quotation: str,
    variant: str,
    record: Mapping[str, Any] | None,
) -> bool:
    """Recognise only provenance-bound transformations with deterministic text."""
    if not isinstance(record, Mapping) or record.get("substantive") is not False:
        return False
    provenance = record.get("variant_provenance")
    if not isinstance(provenance, Mapping) or not str(provenance.get("path") or ""):
        return False
    transformation = str(record.get("transformation_type") or "")
    if transformation == "typography_or_punctuation_normalisation":
        return word_tokens(quotation) == word_tokens(variant)
    if transformation == "editorial_bracket_removal":
        without_brackets = re.sub(r"\[[^\[\]]{1,120}\]", "", quotation)
        return word_tokens(without_brackets) == word_tokens(variant)
    return False


def _variant_relationship_diagnostics(
    quotation: str,
    variant: str,
    record: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one deterministic, length-sensitive variant relationship result."""
    quotation_tokens = word_tokens(quotation)
    variant_tokens = word_tokens(variant)
    quotation_content = {
        token for token in quotation_tokens
        if token not in _NON_MEANINGFUL_VARIANT_TOKENS
    }
    variant_content = {
        token for token in variant_tokens
        if token not in _NON_MEANINGFUL_VARIANT_TOKENS
    }
    shared_content = sorted(quotation_content & variant_content)
    maximum_content_count = max(len(quotation_content), len(variant_content))
    content_overlap = (
        len(shared_content) / maximum_content_count
        if maximum_content_count else 0.0
    )
    sequence_similarity = (
        SequenceMatcher(
            a=quotation_tokens, b=variant_tokens, autojunk=False
        ).ratio()
        if quotation_tokens and variant_tokens else 0.0
    )
    short_wording = maximum_content_count <= 4
    minimum_content_overlap = 2 / 3 if short_wording else 0.40
    minimum_sequence_similarity = 0.65 if short_wording else 0.50
    normalised_identity = bool(
        quotation_tokens and quotation_tokens == variant_tokens
    )
    reviewed_non_substantive = bool(
        sequence_similarity >= 0.50
        and _reviewed_non_substantive_variant(quotation, variant, record)
    )
    ordinary_overlap = bool(
        len(shared_content) >= 2
        and content_overlap >= minimum_content_overlap
        and sequence_similarity >= minimum_sequence_similarity
    )
    accepted = normalised_identity or reviewed_non_substantive or ordinary_overlap
    if normalised_identity:
        reason = "normalised_wording_identity"
    elif reviewed_non_substantive:
        reason = "reviewed_non_substantive_transformation"
    elif len(shared_content) < 2:
        reason = "fewer_than_two_shared_content_tokens"
    elif content_overlap < minimum_content_overlap:
        reason = "content_overlap_below_length_sensitive_threshold"
    elif sequence_similarity < minimum_sequence_similarity:
        reason = "word_sequence_similarity_below_length_sensitive_threshold"
    else:
        reason = "meaningful_overlap_accepted"
    return {
        "accepted": accepted,
        "reason": reason,
        "quotation_token_count": len(quotation_tokens),
        "variant_token_count": len(variant_tokens),
        "quotation_content_token_count": len(quotation_content),
        "variant_content_token_count": len(variant_content),
        "shared_content_token_count": len(shared_content),
        "shared_content_tokens": shared_content,
        "content_overlap": round(content_overlap, 6),
        "minimum_content_overlap": round(minimum_content_overlap, 6),
        "word_sequence_similarity": round(sequence_similarity, 6),
        "minimum_word_sequence_similarity": minimum_sequence_similarity,
        "short_wording_rule_applied": short_wording,
        "reviewed_non_substantive_transformation": reviewed_non_substantive,
    }


def _variant_has_lexical_relationship(
    quotation: str,
    variant: str,
    record: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether the shared variant relationship rule accepts the wording."""
    return bool(
        _variant_relationship_diagnostics(quotation, variant, record)["accepted"]
    )


def _filter_authorised_variants(
    quotation: str,
    variants: Iterable[Any],
    records: Iterable[Any] = (),
) -> tuple[list[str], int, int, list[dict[str, Any]]]:
    accepted: list[str] = []
    placeholder_rejections = 0
    lexical_rejections = 0
    diagnostics: list[dict[str, Any]] = []
    records_by_value = {
        " ".join(str(row.get("search_variant") or "").split()): row
        for row in records
        if isinstance(row, Mapping)
    }
    for raw in variants:
        record = raw if isinstance(raw, Mapping) else records_by_value.get(
            " ".join(str(raw or "").split())
        )
        value = " ".join(str(
            raw.get("search_variant") if isinstance(raw, Mapping) else raw or ""
        ).split())
        if not value:
            continue
        if is_placeholder_text(value):
            placeholder_rejections += 1
            diagnostics.append({
                "variant": value,
                "accepted": False,
                "reason": "placeholder_variant",
            })
            continue
        relationship = _variant_relationship_diagnostics(
            quotation, value, record if isinstance(record, Mapping) else None
        )
        diagnostics.append({"variant": value, **relationship})
        if not relationship["accepted"]:
            lexical_rejections += 1
            continue
        if value not in accepted:
            accepted.append(value)
    return accepted, placeholder_rejections, lexical_rejections, diagnostics


def _semantic_match_target(target: Mapping[str, Any]) -> dict[str, Any]:
    """Return a matching target containing only meaningful authorised variants."""
    result = copy.deepcopy(dict(target))
    existing_diagnostics = result.get("variant_relationship_diagnostics")
    quotation = str(result.get("quotation_text") or "")
    variants, placeholder_count, lexical_count, diagnostics = (
        _filter_authorised_variants(
            quotation,
            result.get("recorded_variants", []),
            result.get("documented_variant_records", []),
        )
    )
    result["recorded_variants"] = variants
    result["variant_relationship_diagnostics"] = (
        copy.deepcopy(existing_diagnostics)
        if isinstance(existing_diagnostics, list) and existing_diagnostics
        else diagnostics
    )
    result["placeholder_variant_rejection_count"] = max(
        int(result.get("placeholder_variant_rejection_count") or 0),
        placeholder_count,
    )
    result["lexically_unrelated_variant_rejection_count"] = max(
        int(result.get("lexically_unrelated_variant_rejection_count") or 0),
        lexical_count,
    )
    return result


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
        value = str(variant["search_variant"])
        if not is_placeholder_text(value):
            phrases.append({
                "variant": value,
                "provenance": "authoritative_recorded_variant",
            })
    for row in plan:
        fragment = str(row.get("fragment_text") or row.get("fragment") or "").strip()
        if fragment and not is_placeholder_text(fragment):
            phrases.append({
                "variant": fragment,
                "provenance": "deterministic_query_strategy_fragment",
            })
        multi = row.get("multi_anchor")
        if isinstance(multi, Mapping):
            for phrase in multi.get("anchor_phrases", []):
                if str(phrase).strip() and not is_placeholder_text(phrase):
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


def _normalise_enriched_target(
    target: Mapping[str, Any], packet: Mapping[str, Any]
) -> dict[str, Any]:
    """Filter optional packet metadata while retaining the packet as provenance."""
    result = copy.deepcopy(dict(target))
    quotation = str(result.get("quotation_text") or "")
    records = documented_variant_records(result)
    variants, placeholder_count, lexical_count, relationship_diagnostics = (
        _filter_authorised_variants(
            quotation,
            (row.get("search_variant") for row in records),
            records,
        )
    )
    remaining = list(variants)
    filtered_records = []
    for row in records:
        value = " ".join(str(row.get("search_variant") or "").split())
        if value in remaining:
            filtered_records.append(copy.deepcopy(dict(row)))
            remaining.remove(value)
    result["documented_variant_records"] = filtered_records
    metadata = dict(result.get("source_metadata") or {})
    for key in (
        "source_event", "stable_locator", "source_type",
        "verification_status", "text_variation_notes",
    ):
        metadata[key] = _optional_historical_text(metadata.get(key))
    metadata["entities"] = [
        value
        for value in (
            _optional_historical_text(item)
            for item in metadata.get("entities", [])
        )
        if value
    ]
    raw_date = packet.get("date")
    metadata["date"] = _normalised_date(raw_date)
    metadata["date_relation_to_marriage"] = (
        "unknown" if not metadata["date"].get("known")
        else metadata.get("date_relation_to_marriage")
    )
    result["source_metadata"] = metadata
    result["historical_metadata_provenance"] = {
        "packet_values_preserved": {
            key: packet.get(key)
            for key in (
                "verified_text", "date", "source_event", "stable_locator"
            )
        },
        "placeholder_values_treated_as_missing_for_classification": True,
    }
    result["placeholder_variant_rejection_count"] = placeholder_count
    result["lexically_unrelated_variant_rejection_count"] = lexical_count
    result["variant_relationship_diagnostics"] = relationship_diagnostics
    return result


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
        enriched = _normalise_enriched_target(enriched, packet)
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
_THATCHER_ROLE_LABEL = re.compile(
    r"^(?:(?:the\s+)?(?:rt\.?\s+hon\.?\s+)?"
    r"mrs\.?\s+(?:margaret\s+)?thatcher|"
    r"mt|pm|prime minister|answer|a)$",
    re.I,
)
_GENERIC_OTHER_LABEL = re.compile(
    r"^(?:q|question|interviewer|interviewers?|chair(?:man|woman|person)|"
    r"moderator|journalist|press|reporter|audience)$",
    re.I,
)
_TITLED_PERSON_LABEL = re.compile(
    r"^(?:Sir|Dame|Lord|Lady|Mr|Mrs|Ms|Miss|Dr|Professor)\.?\s+"
    r"[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3}$"
)
_NAME_WITH_OUTLET_LABEL = re.compile(
    r"^[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){1,3},\s*"
    r"(?:[A-Z]{2,12}|[A-Z][A-Za-z&.-]+(?:\s+[A-Z][A-Za-z&.-]+){0,3})$"
)
_UNTITLED_PERSON_LABEL = re.compile(
    r"^(?P<name>"
    r"[A-Z][A-Za-z'’.-]*[a-z][A-Za-z'’.-]*"
    r"(?:\s+[A-Z][A-Za-z'’.-]*[a-z][A-Za-z'’.-]*){1,3}"
    r")"
    r"(?:\s+\((?:[A-Z]{2,12}|[A-Z][A-Za-z&.'’-]+)"
    r"(?:\s+(?:[A-Z]{2,12}|[A-Z][A-Za-z&.'’-]+)){0,4}\))?$"
)
_NON_PERSON_HEADING_WORDS = frozenset({
    "affairs", "agriculture", "budget", "chapter", "conference", "defence",
    "defense", "economic", "economics", "economy", "education", "election",
    "employment", "energy", "environment", "europe", "european", "foreign",
    "health", "home", "industry", "issues", "policy", "politics", "section",
    "speech", "statement", "tax", "taxation", "trade", "transport",
    "unemployment",
})
_NON_PERSON_INITIALS = frozenset({"AI", "EU", "IT", "TV", "UK", "UN", "US"})
_CONTRIBUTION_TAGS = frozenset({"p", "li", "blockquote"})
_STRUCTURAL_HEADING_TAGS = frozenset({"h1", "h2", "h3"})
_ARCHIVE_ATTRIBUTION_CLASS_TOKENS = frozenset({
    "mt", "intmt", "nonmt", "intnonmt",
})
_ARCHIVE_MT_PROVENANCE = frozenset({
    "explicit_archive_mt_content", "inherited_archive_mt_content",
    "archive_mt_run", "editorial_source_section_mt_baseline",
})
_ARCHIVE_NONMT_PROVENANCE = frozenset({
    "explicit_archive_nonmt_content", "inherited_archive_nonmt_content",
    "archive_nonmt_run", "editorial_source_section_nonmt_baseline",
})
_ARCHIVE_CONFLICT_PROVENANCE = frozenset({
    "conflicting_archive_attribution", "conflicting_archive_run",
})
_ARCHIVE_RUN_PROVENANCE = {
    "mt": "archive_mt_run",
    "nonmt": "archive_nonmt_run",
    "conflicting": "conflicting_archive_run",
}
_NUMBERED_EDITORIAL_SOURCE_LABEL = re.compile(r"^\((\d{1,3})\)\s*(.+)$", re.S)
_NUMBERED_METADATA_ENTRY = re.compile(r"(?:^|\s)\((\d{1,3})\)\s*")
_DIRECT_EDITORIAL_SOURCE_FORMS = frozenset({
    "speaking text",
    "modified speaking text begins",
    "full speaking text begins",
})
_REPORTORIAL_EDITORIAL_SOURCE_FORMS = frozenset({
    "partial paraphrase",
    "partial paraphrase of speaking text",
    "opening of press release partial paraphrase of speaking text",
})
_EDITORIAL_ONLY_END_FORMS = frozenset({
    "end of partial paraphrase",
    "end of partial paraphrase of speaking text",
})
_MAX_EDITORIAL_SOURCE_DIAGNOSTICS = 50
_MAX_EDITORIAL_SOURCE_LABEL_LENGTH = 240


def archive_attribution_classification(
    element_class_tokens: Sequence[str],
    ancestor_class_tokens: Sequence[str] = (),
) -> str:
    """Classify exact MTF attribution tokens on one block and its ancestry."""
    tokens = {
        str(token).casefold()
        for token in (*element_class_tokens, *ancestor_class_tokens)
        if str(token).casefold() in _ARCHIVE_ATTRIBUTION_CLASS_TOKENS
    }
    mt_polarity = bool(tokens & {"mt", "intmt"})
    nonmt_polarity = bool(tokens & {"nonmt", "intnonmt"})
    if mt_polarity and nonmt_polarity:
        return "conflicting_archive_attribution"
    if "mt" in tokens:
        return "mt_content"
    if "nonmt" in tokens:
        return "nonmt_content"
    if "intmt" in tokens:
        return "mt_label"
    if "intnonmt" in tokens:
        return "nonmt_label"
    return "no_archive_attribution"


def _node_class_tokens(node: Any) -> list[str]:
    value = node.get("class", []) if getattr(node, "attrs", None) else []
    if isinstance(value, str):
        return value.split()
    return [str(token) for token in value]


def _normalised_dom_text(node: Any) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def _normalised_dom_text_excluding(node: Any, excluded: Sequence[Any]) -> str:
    values: list[str] = []
    for text_node in node.find_all(string=True):
        ancestor = text_node.parent
        within_excluded = False
        while ancestor is not None and ancestor is not node:
            if any(ancestor is item for item in excluded):
                within_excluded = True
                break
            ancestor = ancestor.parent
        if not within_excluded:
            values.append(str(text_node))
    return " ".join(" ".join(values).split())


def _substantive_text_precedes(parent: Any, marker: Any) -> bool:
    """Treat a marker as leading when only layout/page-number text precedes it."""
    for descendant in parent.descendants:
        if descendant is marker:
            return False
        if getattr(descendant, "name", None):
            continue
        if not str(descendant).strip():
            continue
        ancestor = descendant.parent
        ignored = False
        while ancestor is not None and ancestor is not parent:
            tag = str(getattr(ancestor, "name", "") or "")
            if (
                tag == "span"
                and "pagenum" in {
                    token.casefold() for token in _node_class_tokens(ancestor)
                }
            ):
                ignored = True
                break
            ancestor = ancestor.parent
        if not ignored:
            return True
    return False


def _normalised_dom_text_chunks_excluding(
    node: Any, excluded: Sequence[Any]
) -> list[str]:
    """Split parent text at excluded markers so matching cannot cross them."""
    chunks: list[list[str]] = [[]]
    for descendant in node.descendants:
        if any(descendant is item for item in excluded):
            chunks.append([])
            continue
        if getattr(descendant, "name", None):
            continue
        ancestor = descendant.parent
        within_excluded = False
        while ancestor is not None and ancestor is not node:
            if any(ancestor is item for item in excluded):
                within_excluded = True
                break
            ancestor = ancestor.parent
        if not within_excluded:
            chunks[-1].append(str(descendant))
    return [
        normalised
        for values in chunks
        if (normalised := " ".join(" ".join(values).split()))
    ]


def _normalised_editorial_phrase(value: str) -> str:
    value = re.sub(r"^\(\d{1,3}\)\s*", "", " ".join(value.split()))
    value = value.casefold().strip(" .:;–—-")
    return " ".join(re.sub(r"[^\w]+", " ", value).split())


def _maintained_editorial_marker_semantics(value: str) -> str | None:
    """Recognise only complete, maintained editorial marker phrases."""
    phrase = _normalised_editorial_phrase(value)
    if phrase in _DIRECT_EDITORIAL_SOURCE_FORMS:
        return "mt"
    if phrase in _REPORTORIAL_EDITORIAL_SOURCE_FORMS:
        return "nonmt"
    if phrase in _EDITORIAL_ONLY_END_FORMS:
        return "editorial_only"
    if re.fullmatch(r"(?:beginning|end) of section checked against .+", phrase):
        return "editorial_only"
    return None


def _explicit_non_direct_source_semantics(value: str) -> bool:
    phrase = _normalised_editorial_phrase(value)
    return bool(re.search(
        r"\bpartial paraphrase\b|\bnewspaper report\b|"
        r"\breportorial account\b|\breport of (?:the )?event\b|"
        r"\bpress report\b|\bnon direct source\b",
        phrase,
    ))


def _numbered_editorial_label_semantics(value: str) -> str | None:
    """Recognise bounded numbered labels without widening italic markers."""
    numbered = _NUMBERED_EDITORIAL_SOURCE_LABEL.match(value)
    if numbered is None:
        return None
    label = numbered.group(2)
    maintained = _maintained_editorial_marker_semantics(label)
    if maintained in {"nonmt", "editorial_only"}:
        return maintained
    if _explicit_non_direct_source_semantics(label):
        return "nonmt"
    if maintained == "mt":
        return "mt"
    bounded_label = " ".join(label.split()).casefold().strip()
    if bounded_label.endswith("."):
        bounded_label = bounded_label[:-1].rstrip()
    source_qualified = re.fullmatch(
        r"thatcher archive(?:\s*:\s*|\s*[–—]\s*|\s+-\s+)"
        r"(speaking text|modified speaking text begins|"
        r"full speaking text begins)",
        bounded_label,
    )
    return "mt" if source_qualified else None


def _structured_editorial_metadata(body: bytes) -> dict[str, Any]:
    """Read only the Source/editorial rows in the already-opened MTF HTML."""
    soup = BeautifulSoup(body, "lxml")
    fields: dict[str, str] = {}
    for row in soup.find_all("tr"):
        heading = row.find("th")
        value = row.find("td")
        if heading is None or value is None:
            continue
        key = _normalised_dom_text(heading).casefold().rstrip(":")
        if key in {"source", "editorial comments"}:
            fields[key] = _normalised_dom_text(value)
    numbered: dict[str, list[dict[str, str]]] = {}
    for field_name in ("source", "editorial comments"):
        value = fields.get(field_name, "")
        matches = list(_NUMBERED_METADATA_ENTRY.finditer(value))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
            descriptor = " ".join(value[match.end():end].split()).strip(" ;")
            if descriptor:
                numbered.setdefault(match.group(1), []).append({
                    "metadata_field": field_name,
                    "descriptor": descriptor,
                })
    return {
        "source": fields.get("source", ""),
        "editorial_comments": fields.get("editorial comments", ""),
        "numbered_entries": numbered,
        "metadata_available": bool(fields),
    }


def _explicit_metadata_source_semantics(value: str) -> str | None:
    phrase = _normalised_editorial_phrase(value)
    if _explicit_non_direct_source_semantics(phrase):
        return "nonmt"
    if re.search(
        r"\b(?:modified |full )?(?:speaking|speech) text\b|"
        r"\bdirect thatcher text\b|"
        r"\bdirect speech text\b",
        phrase,
    ):
        return "mt"
    return None


def _metadata_explicitly_reports_source(
    descriptor: str, editorial_comments: str
) -> bool:
    """Require a named source plus an explicit reported-account statement."""
    source_name = re.split(r"[,;:]", descriptor, maxsplit=1)[0]
    source_name = _normalised_editorial_phrase(source_name)
    comments = _normalised_editorial_phrase(editorial_comments)
    if len(source_name.split()) < 2 or source_name not in comments:
        return False
    if re.search(
        rf"\b{re.escape(source_name)}\b(?:\s+\w+){{0,6}}\s+reported\b",
        comments,
    ):
        return True
    source_pattern = r"\s+".join(
        re.escape(word) for word in source_name.split()
    )
    punctuation_preserving_comments = " ".join(
        editorial_comments.split()
    ).casefold()
    return bool(re.search(
        rf"\b{source_pattern}\b(?:\s+\w+){{0,3}}"
        r"\s+report\s+(?:of|on|from)\b",
        punctuation_preserving_comments,
    ))


def _numbered_source_section_baseline(
    label: str,
    number: str,
    metadata: Mapping[str, Any],
    *,
    author_verified: bool,
) -> tuple[str, str]:
    entries = list((metadata.get("numbered_entries") or {}).get(number, []))
    if not entries:
        return "unverified", "numbered_source_metadata_entry_missing"
    descriptors = [str(row.get("descriptor") or "") for row in entries]
    label_descriptor = _NUMBERED_EDITORIAL_SOURCE_LABEL.match(label)
    label_value = label_descriptor.group(2) if label_descriptor else label
    label_phrase = _normalised_editorial_phrase(label_value)
    label_semantics = _numbered_editorial_label_semantics(label)
    all_entry_semantics = {
        _explicit_metadata_source_semantics(descriptor)
        for descriptor in descriptors
    } - {None}
    if len(all_entry_semantics) > 1 or (
        label_semantics in {"mt", "nonmt"}
        and any(
            semantics != label_semantics
            for semantics in all_entry_semantics
        )
    ):
        return (
            "unverified",
            "numbered_source_label_metadata_polarity_conflict",
        )
    consistent = [
        descriptor for descriptor in descriptors
        if label_phrase in _normalised_editorial_phrase(descriptor)
        or _normalised_editorial_phrase(descriptor) in label_phrase
    ]
    if not consistent:
        return "unverified", "numbered_source_label_metadata_inconsistent"
    entry_semantics = {
        _explicit_metadata_source_semantics(descriptor)
        for descriptor in consistent
    } - {None}
    if label_semantics == "mt" and entry_semantics == {"mt"}:
        if author_verified:
            reason = (
                "numbered_source_metadata_verified_direct_mt_text"
                if _maintained_editorial_marker_semantics(label) == "mt"
                else "numbered_source_qualified_direct_mt_text"
            )
            return "mt", reason
        return "unverified", "direct_source_without_verified_thatcher_author"
    if label_semantics == "nonmt" and entry_semantics == {"nonmt"}:
        return "nonmt", "numbered_source_metadata_verified_non_direct_text"
    if any(
        _metadata_explicitly_reports_source(
            descriptor, str(metadata.get("editorial_comments") or "")
        )
        for descriptor in consistent
    ):
        return "nonmt", "numbered_source_metadata_verified_reportorial_account"
    return "unverified", "numbered_source_metadata_semantics_ambiguous"


def _only_marker_and_page_number(parent: Any, marker: Any) -> bool:
    for text_node in parent.find_all(string=True):
        if not str(text_node).strip():
            continue
        ancestor = text_node.parent
        permitted = False
        while ancestor is not None and ancestor is not parent:
            if ancestor is marker:
                permitted = True
                break
            if (
                str(getattr(ancestor, "name", "") or "") == "span"
                and "pagenum" in {
                    token.casefold() for token in _node_class_tokens(ancestor)
                }
            ):
                permitted = True
                break
            ancestor = ancestor.parent
        if not permitted:
            return False
    return True


def _bounded_editorial_label(value: str) -> str:
    return " ".join(value.split())[:_MAX_EDITORIAL_SOURCE_LABEL_LENGTH]


def _editorial_event_record(
    *,
    label: str,
    kind: str,
    baseline: str = "unverified",
    reason: str,
    number: str = "",
) -> dict[str, Any]:
    return {
        "ordered_body_event_kind": kind,
        "element_tag": "editorial-marker",
        "element_class_tokens": [],
        "ancestor_archive_attribution_class_tokens": [],
        "archive_attribution_class_tokens": [],
        "normalised_text": _bounded_editorial_label(label),
        "archive_attribution_classification": "no_archive_attribution",
        "effective_archive_attribution_classification": "no_archive_attribution",
        "archive_attribution_provenance": "no_archive_attribution",
        "archive_attribution_conflict": False,
        "archive_attribution_run_id": None,
        "archive_attribution_run_polarity": "none",
        "archive_source_section_boundary_detected": kind == "editorial_source_boundary",
        "archive_source_section_boundary_kind": kind,
        "archive_source_section_boundary_reason": reason,
        "archive_source_section_baseline_polarity": baseline,
        "archive_source_section_label": _bounded_editorial_label(label),
        "archive_source_section_number": number,
        "editorial_marker_non_searchable": True,
    }


def _archive_block_record(
    node: Any,
    text: str,
    *,
    body_root: Any,
    root_italic: bool = False,
) -> dict[str, Any]:
    element_tokens = _node_class_tokens(node)
    ancestor_tokens: list[str] = []
    ancestor = node.parent
    while ancestor is not None:
        ancestor_tokens.extend(
            token
            for token in _node_class_tokens(ancestor)
            if token.casefold() in _ARCHIVE_ATTRIBUTION_CLASS_TOKENS
        )
        if ancestor is body_root:
            break
        ancestor = ancestor.parent
    archive_tokens = [
        token
        for token in (*element_tokens, *ancestor_tokens)
        if token.casefold() in _ARCHIVE_ATTRIBUTION_CLASS_TOKENS
    ]
    tag = str(node.name or "")
    classification = (
        archive_attribution_classification(element_tokens, ancestor_tokens)
        if tag in _CONTRIBUTION_TAGS
        else "no_archive_attribution"
    )
    provenance = {
        "mt_content": "explicit_archive_mt_content",
        "nonmt_content": "explicit_archive_nonmt_content",
        "mt_label": "explicit_archive_mt_label_state",
        "nonmt_label": "explicit_archive_nonmt_label_state",
        "conflicting_archive_attribution": "conflicting_archive_attribution",
    }.get(classification, "no_archive_attribution")
    return {
        "ordered_body_event_kind": "contribution_block",
        "root_level_ordinary_italic": root_italic,
        "element_tag": tag,
        "element_class_tokens": element_tokens,
        "ancestor_archive_attribution_class_tokens": ancestor_tokens,
        "archive_attribution_class_tokens": archive_tokens,
        "normalised_text": text,
        "archive_attribution_classification": classification,
        "effective_archive_attribution_classification": classification,
        "archive_attribution_provenance": provenance,
        "archive_attribution_conflict": classification == "conflicting_archive_attribution",
        "archive_attribution_run_id": None,
        "archive_attribution_run_polarity": "none",
        "editorial_marker_non_searchable": False,
    }


def _archive_ordered_body_events(
    article: Any,
    body: bytes,
    *,
    author_verified: bool,
) -> list[dict[str, Any]]:
    """Retain searchable blocks and constrained non-searchable events in DOM order."""
    metadata = _structured_editorial_metadata(body)
    events: list[dict[str, Any]] = []
    consumed_markers: set[int] = set()
    block_tags = _CONTRIBUTION_TAGS | _STRUCTURAL_HEADING_TAGS
    for node in article.descendants:
        tag = str(getattr(node, "name", "") or "")
        if not tag or id(node) in consumed_markers:
            continue
        if tag in block_tags:
            ed_comments = [
                item for item in node.find_all("ed-comment")
                if _normalised_dom_text(item)
            ]
            recognised_ed_comments = []
            recognised_details = []
            for marker in ed_comments:
                label = _normalised_dom_text(marker)
                numbered = _NUMBERED_EDITORIAL_SOURCE_LABEL.match(label)
                semantics = _maintained_editorial_marker_semantics(label)
                if numbered:
                    recognised_ed_comments.append(marker)
                    recognised_details.append((
                        marker, label, numbered.group(1), semantics,
                    ))
                elif semantics == "editorial_only":
                    recognised_ed_comments.append(marker)
                    recognised_details.append((marker, label, "", semantics))
            ambiguous_inline_parent = any(
                number and _substantive_text_precedes(node, marker)
                for marker, _label, number, _semantics in recognised_details
            )
            for marker, label, number, semantics in recognised_details:
                if number and ambiguous_inline_parent:
                    events.append(_editorial_event_record(
                        label=label,
                        kind="ambiguous_inline_source_marker",
                        reason="inline_source_marker_after_substantive_text",
                        number=number,
                    ))
                elif number:
                    baseline, reason = _numbered_source_section_baseline(
                        label,
                        number,
                        metadata,
                        author_verified=author_verified,
                    )
                    events.append(_editorial_event_record(
                        label=label,
                        kind="editorial_source_boundary",
                        baseline=baseline,
                        reason=reason,
                        number=number,
                    ))
                elif semantics == "editorial_only":
                    events.append(_editorial_event_record(
                        label=label,
                        kind="editorial_only_marker",
                        reason="maintained_editorial_check_or_end_marker",
                    ))
            if recognised_ed_comments:
                consumed_markers.update(id(marker) for marker in recognised_ed_comments)
                if ambiguous_inline_parent:
                    for part, text in enumerate(
                        _normalised_dom_text_chunks_excluding(
                            node, recognised_ed_comments
                        ),
                        start=1,
                    ):
                        record = _archive_block_record(
                            node, text, body_root=article
                        )
                        record["ambiguous_inline_source_marker"] = True
                        record["ambiguous_inline_source_part"] = part
                        events.append(record)
                    continue
                if all(
                    _only_marker_and_page_number(node, marker)
                    for marker in recognised_ed_comments
                ):
                    continue
            italic_markers = list(node.find_all("i")) if tag == "p" else []
            italic_event = False
            for marker in italic_markers:
                label = _normalised_dom_text(marker)
                semantics = _maintained_editorial_marker_semantics(label)
                if semantics is None or not _only_marker_and_page_number(node, marker):
                    continue
                consumed_markers.add(id(marker))
                italic_event = True
                if semantics in {"mt", "nonmt"}:
                    baseline = semantics
                    reason = (
                        "italic_marker_verified_direct_mt_text"
                        if semantics == "mt" and author_verified
                        else "direct_source_without_verified_thatcher_author"
                        if semantics == "mt"
                        else "italic_marker_verified_non_direct_text"
                    )
                    if semantics == "mt" and not author_verified:
                        baseline = "unverified"
                    number_match = _NUMBERED_EDITORIAL_SOURCE_LABEL.match(label)
                    events.append(_editorial_event_record(
                        label=label,
                        kind="editorial_source_boundary",
                        baseline=baseline,
                        reason=reason,
                        number=number_match.group(1) if number_match else "",
                    ))
                else:
                    events.append(_editorial_event_record(
                        label=label,
                        kind="editorial_only_marker",
                        reason="maintained_editorial_check_or_end_marker",
                    ))
                break
            if italic_event:
                continue
            text = _normalised_dom_text_excluding(
                node, recognised_ed_comments
            )
            if text:
                events.append(_archive_block_record(
                    node, text, body_root=article
                ))
            continue
        if tag == "ed-comment":
            label = _normalised_dom_text(node)
            numbered = _NUMBERED_EDITORIAL_SOURCE_LABEL.match(label)
            semantics = _maintained_editorial_marker_semantics(label)
            if numbered:
                baseline, reason = _numbered_source_section_baseline(
                    label,
                    numbered.group(1),
                    metadata,
                    author_verified=author_verified,
                )
                events.append(_editorial_event_record(
                    label=label,
                    kind="editorial_source_boundary",
                    baseline=baseline,
                    reason=reason,
                    number=numbered.group(1),
                ))
            elif semantics == "editorial_only":
                events.append(_editorial_event_record(
                    label=label,
                    kind="editorial_only_marker",
                    reason="maintained_editorial_check_or_end_marker",
                ))
            continue
        if tag == "i" and node.parent is article:
            label = _normalised_dom_text(node)
            semantics = _maintained_editorial_marker_semantics(label)
            if semantics in {"mt", "nonmt"}:
                baseline = semantics
                reason = (
                    "italic_marker_verified_direct_mt_text"
                    if semantics == "mt" and author_verified
                    else "direct_source_without_verified_thatcher_author"
                    if semantics == "mt"
                    else "italic_marker_verified_non_direct_text"
                )
                if semantics == "mt" and not author_verified:
                    baseline = "unverified"
                number_match = _NUMBERED_EDITORIAL_SOURCE_LABEL.match(label)
                events.append(_editorial_event_record(
                    label=label,
                    kind="editorial_source_boundary",
                    baseline=baseline,
                    reason=reason,
                    number=number_match.group(1) if number_match else "",
                ))
            elif semantics == "editorial_only":
                events.append(_editorial_event_record(
                    label=label,
                    kind="editorial_only_marker",
                    reason="maintained_editorial_check_or_end_marker",
                ))
            else:
                events.append(_archive_block_record(
                    node, label, body_root=article, root_italic=True
                ))
    return events


def _is_searchable_contribution_record(record: Mapping[str, Any]) -> bool:
    return bool(
        record.get("ordered_body_event_kind") == "contribution_block"
        and (
            record.get("element_tag") in _CONTRIBUTION_TAGS
            or record.get("root_level_ordinary_italic")
        )
    )




def _is_untitled_person_label(value: str) -> bool:
    match = _UNTITLED_PERSON_LABEL.fullmatch(value)
    if not match:
        return False
    name_words = {
        word.casefold().strip(".'’-_")
        for word in match.group("name").split()
    }
    return not bool(name_words & _NON_PERSON_HEADING_WORDS)


def _is_short_interviewer_initials(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{2}", value)) and value not in _NON_PERSON_INITIALS


def _speaker_heading_class(
    value: str, *, allow_personal_name: bool = False
) -> str | None:
    """Classify only conservative explicit transcript labels/headings."""
    label = " ".join(value.split()).strip(" -–—")
    if not label or len(label) > 80:
        return None
    if _THATCHER_ROLE_LABEL.fullmatch(label):
        return "thatcher"
    if _GENERIC_OTHER_LABEL.fullmatch(label):
        return "other"
    if allow_personal_name and _THATCHER_LABEL.fullmatch(label):
        return "thatcher"
    if allow_personal_name and (
        _TITLED_PERSON_LABEL.fullmatch(label)
        or _NAME_WITH_OUTLET_LABEL.fullmatch(label)
        or _is_untitled_person_label(label)
    ):
        return "other"
    return None


def _document_body_node(
    body: bytes, validation: Mapping[str, Any]
) -> tuple[Any | None, str]:
    """Resolve one contribution root, failing closed on ambiguous modern bodies."""
    soup = BeautifulSoup(body, "lxml")
    selector = str(validation.get("selector_kind") or "")
    if selector == "legacy":
        node = soup.select_one("#documentbody")
        return (
            node,
            "legacy_document_body" if node is not None
            else "unsupported_or_ambiguous_body",
        )
    if selector == "current_mirror":
        node = soup.select_one(".document-body")
        return (
            node,
            "current_mirror_article_fallback" if node is not None
            else "unsupported_or_ambiguous_body",
        )
    article = soup.select_one("article.node-archive-document")
    if article is None:
        return None, "unsupported_or_ambiguous_body"
    usable_field_bodies = [
        node for node in article.select(".field-body")
        if " ".join(node.get_text(" ", strip=True).split())
    ]
    if len(usable_field_bodies) == 1:
        return usable_field_bodies[0], "current_mirror_field_body"
    if usable_field_bodies:
        return None, "unsupported_or_ambiguous_body"
    return article, "current_mirror_article_fallback"


def _matching_units(
    segments: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Coalesce only consecutive searchable blocks in one archive run."""
    units: list[dict[str, Any]] = []
    for source_segment in segments:
        segment = copy.deepcopy(dict(source_segment))
        text = str(segment.get("text") or "")
        if not text:
            continue
        run_id = segment.get("archive_attribution_run_id")
        provenance = str(
            segment.get("archive_attribution_provenance")
            or "no_archive_attribution"
        )
        component = {
            "start": 0,
            "end": len(text),
            "archive_attribution_provenance": provenance,
            "archive_attribution_classification": segment.get(
                "archive_attribution_classification",
                "no_archive_attribution",
            ),
            "archive_attribution_class_tokens": list(
                segment.get("archive_attribution_class_tokens") or []
            ),
            "archive_attribution_conflict": bool(
                segment.get("archive_attribution_conflict")
            ),
            "evidence_basis": segment.get("evidence_basis"),
            "archive_source_section_id": segment.get(
                "archive_source_section_id"
            ),
            "archive_source_section_baseline_polarity": segment.get(
                "archive_source_section_baseline_polarity", "unverified"
            ),
            "archive_source_section_baseline_applied": bool(
                segment.get("archive_source_section_baseline_applied")
            ),
        }
        if (
            run_id is not None
            and units
            and units[-1].get("archive_attribution_run_id") == run_id
            and units[-1].get("archive_source_section_id")
            == segment.get("archive_source_section_id")
        ):
            unit = units[-1]
            start = len(str(unit["text"])) + 1
            unit["text"] = f'{unit["text"]} {text}'
            component["start"] = start
            component["end"] = start + len(text)
            unit["_archive_attribution_components"].append(component)
            unit["archive_attribution_component_count"] += 1
            component_provenance = unit[
                "archive_attribution_component_provenance"
            ]
            if provenance not in component_provenance:
                component_provenance.append(provenance)
            unit["archive_attribution_class_tokens"] = list(dict.fromkeys(
                [
                    *unit.get("archive_attribution_class_tokens", []),
                    *segment.get("archive_attribution_class_tokens", []),
                ]
            ))
            unit["archive_attribution_conflict"] = bool(
                unit.get("archive_attribution_conflict")
                or segment.get("archive_attribution_conflict")
            )
            if not unit.get("speaker_label") and segment.get("speaker_label"):
                unit["speaker_label"] = segment["speaker_label"]
            continue
        segment["archive_attribution_component_count"] = 1
        segment["archive_attribution_component_provenance"] = [provenance]
        segment["archive_attribution_run_provenance"] = (
            str(segment.get("evidence_basis") or "")
            if segment.get("archive_source_section_baseline_applied")
            else _ARCHIVE_RUN_PROVENANCE.get(str(
                segment.get("archive_attribution_run_polarity") or "none"
            ), "")
        )
        segment["_archive_attribution_components"] = [component]
        units.append(segment)
    return units


def _matching_segment_for_span(
    unit: Mapping[str, Any], match: Mapping[str, Any]
) -> dict[str, Any]:
    """Describe the block provenance actually touched by one unit match."""
    segment = copy.deepcopy(dict(unit))
    components = list(segment.pop("_archive_attribution_components", []))
    segment["archive_attribution_matched_component_provenance"] = []
    segment["archive_attribution_match_spans_inherited_content"] = False
    segment["archive_attribution_match_spans_multiple_blocks"] = False
    span_values = match.get("component_spans") or (
        [match.get("span")] if match.get("span") else []
    )
    spans = [
        (int(value[0]), int(value[1]))
        for value in span_values
        if isinstance(value, (list, tuple)) and len(value) == 2
    ]
    if not spans or not components:
        return segment
    matched_components = [
        component
        for component in components
        if any(
            int(component["start"]) < end
            and int(component["end"]) > start
            for start, end in spans
        )
    ]
    if not matched_components:
        return segment
    matched_provenance = list(dict.fromkeys(
        str(component.get("archive_attribution_provenance") or "")
        for component in matched_components
    ))
    segment["archive_attribution_matched_component_provenance"] = (
        matched_provenance
    )
    spans_inherited = any(
        provenance.startswith("inherited_")
        for provenance in matched_provenance
    )
    segment["archive_attribution_match_spans_inherited_content"] = (
        spans_inherited
    )
    segment["archive_attribution_match_spans_multiple_blocks"] = (
        len(matched_components) > 1
    )
    if len(matched_components) == 1:
        component = matched_components[0]
        segment["archive_attribution_classification"] = component.get(
            "archive_attribution_classification", "no_archive_attribution"
        )
        segment["archive_attribution_provenance"] = component.get(
            "archive_attribution_provenance", "no_archive_attribution"
        )
        segment["archive_attribution_class_tokens"] = list(
            component.get("archive_attribution_class_tokens") or []
        )
        segment["archive_attribution_conflict"] = bool(
            component.get("archive_attribution_conflict")
        )
        segment["evidence_basis"] = component.get("evidence_basis")
        segment["archive_source_section_id"] = component.get(
            "archive_source_section_id"
        )
        segment["archive_source_section_baseline_polarity"] = component.get(
            "archive_source_section_baseline_polarity", "unverified"
        )
        segment["archive_source_section_baseline_applied"] = bool(
            component.get("archive_source_section_baseline_applied")
        )
    elif segment.get("archive_attribution_run_id") is not None:
        run_provenance = str(
            segment.get("archive_attribution_run_provenance") or ""
        )
        if run_provenance:
            segment["archive_attribution_provenance"] = run_provenance
            segment["evidence_basis"] = run_provenance
        segment["archive_attribution_conflict"] = bool(
            segment.get("archive_attribution_run_polarity") == "conflicting"
            or segment.get("archive_attribution_conflict")
        )
    return segment


def speaker_segments(body: bytes, validation: Mapping[str, Any]) -> dict[str, Any]:
    """Create contribution-bounded transcript segments without speaker joining."""
    document_body, body_selector_kind = _document_body_node(body, validation)
    if document_body is None:
        return {
            "labels_detected": False,
            "archive_attribution_markup_detected": False,
            "editorial_source_sections_detected": False,
            "reported_nonmt_editorial_sections_detected": False,
            "archive_source_section_count": 0,
            "archive_source_section_events": [],
            "archive_editorial_marker_count": 0,
            "archive_editorial_marker_events": [],
            "ordered_body_events": [],
            "document_body_selector_kind": body_selector_kind,
            "contribution_blocks": [],
            "segments": [],
            "matching_units": [],
        }
    author_verified = research._is_margaret_thatcher_author(
        str(validation.get("author") or "")
    )
    blocks = _archive_ordered_body_events(
        document_body, body, author_verified=author_verified
    )
    archive_markup_detected = any(
        _is_searchable_contribution_record(block)
        and block["archive_attribution_classification"]
        != "no_archive_attribution"
        for block in blocks
    )
    editorial_source_sections_detected = any(
        block.get("ordered_body_event_kind") == "editorial_source_boundary"
        for block in blocks
    )
    editorial_markers_detected = any(
        block.get("ordered_body_event_kind") in {
            "editorial_source_boundary", "editorial_only_marker",
            "ambiguous_inline_source_marker",
        }
        for block in blocks
    )
    base_classes: list[str | None] = []
    for block in blocks:
        value = str(block["normalised_text"])
        if not _is_searchable_contribution_record(block):
            base_classes.append(None)
            continue
        if block.get("ambiguous_inline_source_marker"):
            base_classes.append(None)
            continue
        if block["archive_attribution_classification"] != "no_archive_attribution":
            base_classes.append(None)
            continue
        match = _SPEAKER_PREFIX.match(value)
        if match:
            possible = " ".join(match.group(1).split())
            base_classes.append(_speaker_heading_class(
                possible, allow_personal_name=True
            ))
        else:
            base_classes.append(_speaker_heading_class(value))
    transcript_structure = bool(
        archive_markup_detected
        or any(value is not None for value in base_classes)
    )

    def contribution_text_at(index: int) -> bool:
        return bool(
            0 <= index < len(blocks)
            and _is_searchable_contribution_record(blocks[index])
            and base_classes[index] is None
            and blocks[index]["archive_attribution_classification"]
            == "no_archive_attribution"
            and _SPEAKER_PREFIX.match(blocks[index]["normalised_text"]) is None
        )

    def alternates_with_explicit_thatcher(index: int) -> bool:
        return bool(
            (
                contribution_text_at(index + 1)
                and index + 2 < len(blocks)
                and base_classes[index + 2] == "thatcher"
            )
            or (
                index >= 2
                and base_classes[index - 2] == "thatcher"
                and contribution_text_at(index - 1)
                and contribution_text_at(index + 1)
            )
        )

    repeated_context_labels: dict[str, int] = {}
    for index, block in enumerate(blocks):
        value = str(block["normalised_text"])
        if (
            _is_searchable_contribution_record(block)
            and block["archive_attribution_classification"]
            == "no_archive_attribution"
            and contribution_text_at(index + 1)
            and (
                _is_untitled_person_label(value)
                or _is_short_interviewer_initials(value)
            )
        ):
            repeated_context_labels[value] = repeated_context_labels.get(value, 0) + 1
    contextual_other_labels: set[int] = set()
    for index, block in enumerate(blocks):
        value = str(block["normalised_text"])
        if (
            transcript_structure
            and _is_searchable_contribution_record(block)
            and block["archive_attribution_classification"]
            == "no_archive_attribution"
            and contribution_text_at(index + 1)
            and (
                (
                    _is_untitled_person_label(value)
                    and (
                        alternates_with_explicit_thatcher(index)
                        or repeated_context_labels.get(value, 0) >= 2
                    )
                )
                or (
                    _is_short_interviewer_initials(value)
                    and alternates_with_explicit_thatcher(index)
                )
            )
        ):
            contextual_other_labels.add(index)
    parsed: list[dict[str, Any]] = []
    maintained_labels_detected = False
    for index, block_record in enumerate(blocks):
        block = str(block_record["normalised_text"])
        if not _is_searchable_contribution_record(block_record):
            parsed.append({
                **block_record,
                "maintained_label": "",
                "maintained_value": "",
                "maintained_speaker_class": None,
            })
            continue
        if block_record.get("ambiguous_inline_source_marker"):
            parsed.append({
                **block_record,
                "maintained_label": "",
                "maintained_value": block,
                "maintained_speaker_class": None,
            })
            continue
        match = _SPEAKER_PREFIX.match(block)
        label = ""
        value = block
        speaker_class: str | None = None
        if (
            block_record["archive_attribution_classification"]
            == "no_archive_attribution"
            and match
        ):
            possible = " ".join(match.group(1).split())
            speaker_class = _speaker_heading_class(
                possible, allow_personal_name=True
            )
            if speaker_class is not None:
                label, value = possible, match.group(2).strip()
                maintained_labels_detected = True
        elif (
            block_record["archive_attribution_classification"]
            == "no_archive_attribution"
        ):
            speaker_class = _speaker_heading_class(block)
            if (
                speaker_class is None
                and transcript_structure
                and (
                    _TITLED_PERSON_LABEL.fullmatch(block)
                    or _NAME_WITH_OUTLET_LABEL.fullmatch(block)
                    or index in contextual_other_labels
                )
            ):
                speaker_class = _speaker_heading_class(
                    block, allow_personal_name=True
                )
                if speaker_class is None and index in contextual_other_labels:
                    speaker_class = "other"
        if not match and speaker_class is not None:
            label, value = block, ""
            maintained_labels_detected = True
        parsed.append({
            **block_record,
            "maintained_label": label,
            "maintained_value": value,
            "maintained_speaker_class": speaker_class,
        })
    if (
        not maintained_labels_detected
        and not archive_markup_detected
        and not editorial_markers_detected
    ):
        text = " ".join(document_body.get_text(" ", strip=True).split())
        fallback_segments = [{
            "speaker_class": "thatcher" if author_verified else "unverified",
            "speaker_label": str(validation.get("author") or ""),
            "text": text,
            "evidence_basis": (
                "explicit_document_author" if author_verified
                else "document_author_not_explicitly_verified"
            ),
            "archive_attribution_classification": (
                "no_archive_attribution"
            ),
            "archive_attribution_provenance": "no_archive_attribution",
            "archive_attribution_class_tokens": [],
            "archive_attribution_conflict": False,
            "archive_attribution_run_id": None,
            "archive_attribution_run_polarity": "none",
            "archive_source_section_id": 0,
            "archive_source_section_baseline_polarity": "unverified",
            "archive_source_section_boundary_detected": False,
            "archive_source_section_boundary_kind": "",
            "archive_source_section_boundary_reason": "",
            "archive_source_section_label": "",
            "archive_source_section_baseline_applied": False,
        }] if text else []
        return {
            "labels_detected": False,
            "archive_attribution_markup_detected": False,
            "editorial_source_sections_detected": False,
            "reported_nonmt_editorial_sections_detected": False,
            "archive_source_section_count": 0,
            "archive_source_section_events": [],
            "archive_editorial_marker_count": 0,
            "archive_editorial_marker_events": [],
            "ordered_body_events": parsed,
            "document_body_selector_kind": body_selector_kind,
            "contribution_blocks": [
                row for row in parsed
                if row.get("ordered_body_event_kind") == "contribution_block"
            ],
            "segments": fallback_segments,
            "matching_units": _matching_units(fallback_segments),
        }
    segments: list[dict[str, Any]] = []
    current_class = "unverified"
    current_label = ""
    current_basis = "unlabelled_material_before_transcript"
    archive_state_class: str | None = None
    archive_state_label = ""
    archive_state_classification = "no_archive_attribution"
    archive_state_tokens: list[str] = []
    archive_state_run_id: int | None = None
    archive_state_run_polarity = "none"
    archive_state_origin = "none"
    archive_run_counter = 0
    source_section_id = 0
    source_section_baseline = "unverified"
    source_section_boundary_kind = ""
    source_section_boundary_reason = ""
    source_section_label = ""
    source_section_baseline_run_id: int | None = None
    source_section_events: list[dict[str, Any]] = []
    editorial_marker_events: list[dict[str, Any]] = []
    editorial_marker_count = 0
    maintained_transcript_turn_active = False

    def start_archive_run(polarity: str) -> int:
        nonlocal archive_run_counter
        archive_run_counter += 1
        return archive_run_counter

    def terminate_section_speaker_state() -> None:
        nonlocal archive_state_class
        nonlocal archive_state_label
        nonlocal archive_state_classification
        nonlocal archive_state_tokens
        nonlocal archive_state_run_id
        nonlocal archive_state_run_polarity
        nonlocal archive_state_origin
        nonlocal source_section_baseline_run_id
        nonlocal maintained_transcript_turn_active
        nonlocal current_class
        nonlocal current_label
        nonlocal current_basis
        archive_state_class = None
        archive_state_label = ""
        archive_state_classification = "no_archive_attribution"
        archive_state_tokens = []
        archive_state_run_id = None
        archive_state_run_polarity = "none"
        archive_state_origin = "none"
        source_section_baseline_run_id = None
        maintained_transcript_turn_active = False
        current_class = "unverified"
        current_label = ""
        current_basis = "unlabelled_material_before_transcript"

    def append_segment(
        value: str,
        *,
        speaker_class: str,
        speaker_label: str,
        evidence_basis: str,
        archive_classification: str,
        archive_provenance: str,
        archive_tokens: Sequence[str],
        archive_conflict: bool = False,
        archive_run_id: int | None = None,
        archive_run_polarity: str = "none",
        source_section_baseline_applied: bool = False,
    ) -> None:
        if not value:
            return
        tokens = list(dict.fromkeys(str(token) for token in archive_tokens))
        segment = {
            "speaker_class": speaker_class,
            "speaker_label": speaker_label,
            "text": value,
            "evidence_basis": evidence_basis,
            "archive_attribution_classification": archive_classification,
            "archive_attribution_provenance": archive_provenance,
            "archive_attribution_class_tokens": tokens,
            "archive_attribution_conflict": archive_conflict,
            "archive_attribution_run_id": archive_run_id,
            "archive_attribution_run_polarity": archive_run_polarity,
            "archive_source_section_id": source_section_id,
            "archive_source_section_baseline_polarity": source_section_baseline,
            "archive_source_section_boundary_detected": bool(source_section_id),
            "archive_source_section_boundary_kind": source_section_boundary_kind,
            "archive_source_section_boundary_reason": source_section_boundary_reason,
            "archive_source_section_label": source_section_label,
            "archive_source_section_baseline_applied": (
                source_section_baseline_applied
            ),
        }
        merge_keys = (
            "speaker_class", "speaker_label", "evidence_basis",
            "archive_attribution_classification",
            "archive_attribution_provenance",
            "archive_attribution_class_tokens", "archive_attribution_conflict",
            "archive_attribution_run_id",
            "archive_attribution_run_polarity",
            "archive_source_section_id",
            "archive_source_section_baseline_polarity",
            "archive_source_section_boundary_detected",
            "archive_source_section_boundary_kind",
            "archive_source_section_boundary_reason",
            "archive_source_section_label",
            "archive_source_section_baseline_applied",
        )
        if archive_run_id is None and segments and all(
            segments[-1].get(key) == segment.get(key) for key in merge_keys
        ):
            segments[-1]["text"] += " " + value
        else:
            segments.append(segment)

    if (
        author_verified
        and not maintained_labels_detected
        and not archive_markup_detected
        and not editorial_source_sections_detected
    ):
        current_class = "thatcher"
        current_label = str(validation.get("author") or "")
        current_basis = "explicit_document_author"

    for block_record in parsed:
        event_kind = str(block_record.get("ordered_body_event_kind") or "")
        if event_kind == "ambiguous_inline_source_marker":
            editorial_marker_count += 1
            terminate_section_speaker_state()
            source_section_baseline = "unverified"
            source_section_boundary_kind = event_kind
            source_section_boundary_reason = str(
                block_record.get("archive_source_section_boundary_reason") or ""
            )
            source_section_label = ""
            block_record["archive_source_section_id"] = source_section_id
            if len(editorial_marker_events) < _MAX_EDITORIAL_SOURCE_DIAGNOSTICS:
                editorial_marker_events.append({
                    "archive_source_section_id": source_section_id,
                    "editorial_marker_kind": event_kind,
                    "editorial_marker_reason": source_section_boundary_reason,
                    "editorial_marker_label": str(
                        block_record.get("archive_source_section_label") or ""
                    ),
                    "editorial_marker_non_searchable": True,
                })
            continue
        if event_kind == "editorial_source_boundary":
            editorial_marker_count += 1
            source_section_id += 1
            source_section_baseline = str(
                block_record.get("archive_source_section_baseline_polarity")
                or "unverified"
            )
            source_section_boundary_kind = str(
                block_record.get("archive_source_section_boundary_kind") or ""
            )
            source_section_boundary_reason = str(
                block_record.get("archive_source_section_boundary_reason") or ""
            )
            source_section_label = str(
                block_record.get("archive_source_section_label") or ""
            )
            block_record["archive_source_section_id"] = source_section_id
            archive_state_class = None
            archive_state_label = ""
            archive_state_classification = "no_archive_attribution"
            archive_state_tokens = []
            archive_state_run_id = None
            archive_state_run_polarity = "none"
            archive_state_origin = "none"
            source_section_baseline_run_id = None
            current_class = "unverified"
            current_label = ""
            current_basis = "unlabelled_material_before_transcript"
            maintained_transcript_turn_active = False
            if len(source_section_events) < _MAX_EDITORIAL_SOURCE_DIAGNOSTICS:
                source_section_events.append({
                    "archive_source_section_id": source_section_id,
                    "archive_source_section_number": str(
                        block_record.get("archive_source_section_number") or ""
                    ),
                    "archive_source_section_baseline_polarity": (
                        source_section_baseline
                    ),
                    "archive_source_section_boundary_kind": (
                        source_section_boundary_kind
                    ),
                    "archive_source_section_boundary_reason": (
                        source_section_boundary_reason
                    ),
                    "archive_source_section_label": source_section_label,
                })
            if len(editorial_marker_events) < _MAX_EDITORIAL_SOURCE_DIAGNOSTICS:
                editorial_marker_events.append({
                    "archive_source_section_id": source_section_id,
                    "editorial_marker_kind": event_kind,
                    "editorial_marker_reason": source_section_boundary_reason,
                    "editorial_marker_label": source_section_label,
                    "editorial_marker_non_searchable": True,
                })
            continue
        if event_kind == "editorial_only_marker":
            editorial_marker_count += 1
            block_record["archive_source_section_id"] = source_section_id
            block_record["archive_source_section_baseline_polarity"] = (
                source_section_baseline
            )
            if len(editorial_marker_events) < _MAX_EDITORIAL_SOURCE_DIAGNOSTICS:
                editorial_marker_events.append({
                    "archive_source_section_id": source_section_id,
                    "editorial_marker_kind": event_kind,
                    "editorial_marker_reason": str(
                        block_record.get(
                            "archive_source_section_boundary_reason"
                        ) or ""
                    ),
                    "editorial_marker_label": str(
                        block_record.get("archive_source_section_label") or ""
                    ),
                    "editorial_marker_non_searchable": True,
                })
            continue
        block_record.update({
            "archive_source_section_id": source_section_id,
            "archive_source_section_baseline_polarity": source_section_baseline,
            "archive_source_section_boundary_detected": bool(source_section_id),
            "archive_source_section_boundary_kind": source_section_boundary_kind,
            "archive_source_section_boundary_reason": source_section_boundary_reason,
            "archive_source_section_label": source_section_label,
            "archive_source_section_baseline_applied": False,
        })
        if not _is_searchable_contribution_record(block_record):
            continue
        block = str(block_record["normalised_text"])
        if block_record.get("ambiguous_inline_source_marker"):
            ambiguous_run_id = start_archive_run("unverified")
            block_record["archive_attribution_provenance"] = (
                "inline_source_marker_after_substantive_text"
            )
            block_record["effective_archive_attribution_classification"] = (
                "ambiguous_inline_source_marker"
            )
            block_record["archive_attribution_conflict"] = False
            block_record["archive_attribution_run_id"] = ambiguous_run_id
            block_record["archive_attribution_run_polarity"] = "unverified"
            append_segment(
                block,
                speaker_class="unverified",
                speaker_label="",
                evidence_basis="inline_source_marker_after_substantive_text",
                archive_classification="ambiguous_inline_source_marker",
                archive_provenance="inline_source_marker_after_substantive_text",
                archive_tokens=[],
                archive_run_id=ambiguous_run_id,
                archive_run_polarity="unverified",
            )
            source_section_baseline_run_id = None
            continue
        archive_classification = str(
            block_record["archive_attribution_classification"]
        )
        archive_tokens = list(
            block_record["archive_attribution_class_tokens"]
        )
        token_polarities = {token.casefold() for token in archive_tokens}
        if archive_classification in {"mt_label", "nonmt_label"}:
            archive_state_run_polarity = (
                "mt" if archive_classification == "mt_label" else "nonmt"
            )
            archive_state_run_id = start_archive_run(
                archive_state_run_polarity
            )
            archive_state_class = (
                "thatcher" if archive_classification == "mt_label" else "other"
            )
            archive_state_label = block
            archive_state_classification = archive_classification
            archive_state_tokens = archive_tokens
            archive_state_origin = "label"
            source_section_baseline_run_id = None
            maintained_transcript_turn_active = False
            block_record["archive_attribution_run_id"] = archive_state_run_id
            block_record["archive_attribution_run_polarity"] = (
                archive_state_run_polarity
            )
            continue
        if archive_classification == "conflicting_archive_attribution":
            conflict_run_id = start_archive_run("conflicting")
            block_record["archive_attribution_run_id"] = conflict_run_id
            block_record["archive_attribution_run_polarity"] = (
                "conflicting"
            )
            is_content_block = bool(token_polarities & {"mt", "nonmt"})
            if is_content_block:
                append_segment(
                    block,
                    speaker_class="unverified",
                    speaker_label="conflicting archive attribution",
                    evidence_basis="conflicting_archive_attribution",
                    archive_classification=archive_classification,
                    archive_provenance="conflicting_archive_attribution",
                    archive_tokens=archive_tokens,
                    archive_conflict=True,
                    archive_run_id=conflict_run_id,
                    archive_run_polarity="conflicting",
                )
            source_section_baseline_run_id = None
            if source_section_id:
                terminate_section_speaker_state()
            else:
                archive_state_run_id = conflict_run_id
                archive_state_run_polarity = "conflicting"
                archive_state_class = "unverified"
                archive_state_label = (
                    "conflicting archive attribution" if is_content_block else block
                )
                archive_state_classification = archive_classification
                archive_state_tokens = archive_tokens
                archive_state_origin = "content"
            continue
        if archive_classification in {"mt_content", "nonmt_content"}:
            speaker_class = (
                "thatcher" if archive_classification == "mt_content" else "other"
            )
            polarity = "mt" if speaker_class == "thatcher" else "nonmt"
            compatible_label = bool(
                archive_state_origin == "label"
                and archive_state_run_polarity == polarity
            )
            compatible_state = archive_state_run_polarity == polarity
            if source_section_id:
                content_run_id = (
                    archive_state_run_id
                    if compatible_label
                    else start_archive_run(polarity)
                )
            else:
                if not compatible_state:
                    archive_state_run_id = start_archive_run(polarity)
                content_run_id = archive_state_run_id
                archive_state_run_polarity = polarity
            block_record["archive_attribution_run_id"] = content_run_id
            block_record["archive_attribution_run_polarity"] = polarity
            append_segment(
                block,
                speaker_class=speaker_class,
                speaker_label=(
                    archive_state_label if compatible_label or (
                        not source_section_id and compatible_state
                    )
                    else ""
                ),
                evidence_basis=(
                    "explicit_archive_mt_content"
                    if speaker_class == "thatcher"
                    else "explicit_archive_nonmt_content"
                ),
                archive_classification=archive_classification,
                archive_provenance=(
                    "explicit_archive_mt_content"
                    if speaker_class == "thatcher"
                    else "explicit_archive_nonmt_content"
                ),
                archive_tokens=archive_tokens,
                archive_run_id=content_run_id,
                archive_run_polarity=polarity,
            )
            source_section_baseline_run_id = None
            if source_section_id and not compatible_label:
                terminate_section_speaker_state()
            elif not source_section_id:
                archive_state_class = speaker_class
                archive_state_label = archive_state_label if compatible_state else ""
                archive_state_classification = archive_classification
                archive_state_tokens = archive_tokens
                archive_state_origin = "content"
            continue
        if archive_state_class is not None:
            inherited_conflict = (
                archive_state_classification
                == "conflicting_archive_attribution"
            )
            inherited_provenance = (
                "conflicting_archive_attribution"
                if inherited_conflict
                else "inherited_archive_mt_content"
                if archive_state_class == "thatcher"
                else "inherited_archive_nonmt_content"
            )
            block_record["archive_attribution_provenance"] = (
                inherited_provenance
            )
            block_record["effective_archive_attribution_classification"] = (
                archive_state_classification
            )
            block_record["archive_attribution_conflict"] = inherited_conflict
            block_record["archive_attribution_run_id"] = archive_state_run_id
            block_record["archive_attribution_run_polarity"] = (
                archive_state_run_polarity
            )
            append_segment(
                block,
                speaker_class=archive_state_class,
                speaker_label=archive_state_label,
                evidence_basis=(
                    "conflicting_archive_attribution"
                    if inherited_conflict
                    else "inherited_archive_mt_run"
                    if archive_state_class == "thatcher"
                    else "inherited_archive_nonmt_run"
                ),
                archive_classification=archive_state_classification,
                archive_provenance=inherited_provenance,
                archive_tokens=archive_state_tokens,
                archive_conflict=inherited_conflict,
                archive_run_id=archive_state_run_id,
                archive_run_polarity=archive_state_run_polarity,
            )
            continue
        label = str(block_record["maintained_label"])
        value = str(block_record["maintained_value"])
        heading_class = block_record["maintained_speaker_class"]
        if label:
            if heading_class == "thatcher" and (
                author_verified
                or "thatcher" in label.casefold()
                or label.casefold() == "mt"
            ):
                current_class = "thatcher"
            elif heading_class == "other":
                current_class = "other"
            else:
                current_class = "unverified"
            current_label = label
            current_basis = "explicit_transcript_speaker_label"
            maintained_transcript_turn_active = True
            source_section_baseline_run_id = None
        if maintained_transcript_turn_active or not source_section_id:
            append_segment(
                value,
                speaker_class=current_class,
                speaker_label=current_label,
                evidence_basis=current_basis,
                archive_classification="no_archive_attribution",
                archive_provenance="no_archive_attribution",
                archive_tokens=[],
            )
            continue
        baseline_speaker = (
            "thatcher" if source_section_baseline == "mt"
            else "other" if source_section_baseline == "nonmt"
            else "unverified"
        )
        baseline_basis = (
            "editorial_source_section_mt_baseline"
            if source_section_baseline == "mt"
            else "editorial_source_section_nonmt_baseline"
            if source_section_baseline == "nonmt"
            else "editorial_source_section_unverified_baseline"
        )
        if source_section_baseline_run_id is None:
            source_section_baseline_run_id = start_archive_run(
                source_section_baseline
            )
        block_record["archive_attribution_provenance"] = baseline_basis
        block_record["effective_archive_attribution_classification"] = (
            f"editorial_source_section_{source_section_baseline}_baseline"
        )
        block_record["archive_attribution_run_id"] = (
            source_section_baseline_run_id
        )
        block_record["archive_attribution_run_polarity"] = (
            source_section_baseline
        )
        block_record["archive_source_section_baseline_applied"] = True
        append_segment(
            value,
            speaker_class=baseline_speaker,
            speaker_label=source_section_label,
            evidence_basis=baseline_basis,
            archive_classification=(
                f"editorial_source_section_{source_section_baseline}_baseline"
            ),
            archive_provenance=baseline_basis,
            archive_tokens=[],
            archive_run_id=source_section_baseline_run_id,
            archive_run_polarity=source_section_baseline,
            source_section_baseline_applied=True,
        )
    return {
        "labels_detected": bool(
            maintained_labels_detected
            or archive_markup_detected
            or editorial_markers_detected
        ),
        "archive_attribution_markup_detected": archive_markup_detected,
        "editorial_source_sections_detected": editorial_source_sections_detected,
        "reported_nonmt_editorial_sections_detected": any(
            row["archive_source_section_baseline_polarity"] == "nonmt"
            for row in source_section_events
        ),
        "archive_source_section_count": source_section_id,
        "archive_source_section_events": source_section_events,
        "archive_editorial_marker_count": editorial_marker_count,
        "archive_editorial_marker_events": editorial_marker_events,
        "ordered_body_events": parsed,
        "document_body_selector_kind": body_selector_kind,
        "contribution_blocks": [
            row for row in parsed
            if row.get("ordered_body_event_kind") == "contribution_block"
        ],
        "segments": segments,
        "matching_units": _matching_units(segments),
    }


_MATCH_PRIORITY = {
    "exact_quotation": 0,
    "recorded_variant": 1,
    "assembled_clauses": 2,
    "near_exact_variant": 3,
    "distinctive_fragment_only": 4,
    "none": 5,
}


def _recorded_variant_match_relationship(
    target: Mapping[str, Any], match: Mapping[str, Any]
) -> dict[str, Any]:
    value = str(match.get("matched_recorded_wording") or "").strip()
    if not value:
        return {
            "accepted": False,
            "reason": "matched_recorded_wording_missing",
        }
    record = next((
        row for row in target.get("documented_variant_records", [])
        if isinstance(row, Mapping)
        and " ".join(str(row.get("search_variant") or "").split()) == value
    ), None)
    return _variant_relationship_diagnostics(
        str(target.get("quotation_text") or ""), value, record
    )


def _acceptable_primary_contribution_match(
    target: Mapping[str, Any], match: Mapping[str, Any]
) -> bool:
    match_type = str(match.get("match_type") or "none")
    if match_type == "exact_quotation":
        return True
    if match_type != "recorded_variant":
        return False
    relationship = _recorded_variant_match_relationship(target, match)
    return bool(
        relationship["accepted"]
        and float(match.get("wording_similarity") or 0.0) > 0.0
    )


def _match_crosses_editorial_source_sections(
    units: Sequence[Mapping[str, Any]], match: Mapping[str, Any]
) -> bool:
    span_values = match.get("component_spans") or (
        [match.get("span")] if match.get("span") else []
    )
    spans = [
        (int(value[0]), int(value[1]))
        for value in span_values
        if isinstance(value, (list, tuple)) and len(value) == 2
    ]
    if not spans:
        return False
    ranges: list[tuple[int, int, Any]] = []
    offset = 0
    for unit in units:
        text = str(unit.get("text") or "")
        ranges.append((offset, offset + len(text), unit.get("archive_source_section_id")))
        offset += len(text) + 1
    touched = {
        section_id
        for start, end, section_id in ranges
        if any(start < span_end and end > span_start for span_start, span_end in spans)
    }
    return len(touched) > 1


def contribution_aware_match(
    target: Mapping[str, Any],
    extraction: Mapping[str, Any],
    body: bytes,
    validation: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select evidence within one attribution run; reject boundary joins."""
    match_target = _semantic_match_target(target)
    segmentation = speaker_segments(body, validation)
    matches: list[
        tuple[tuple[int, int, int], dict[str, Any], Mapping[str, Any]]
    ] = []
    matching_units = segmentation.get("matching_units", [])
    for order, unit in enumerate(matching_units):
        match = research.extract_supporting_passage(match_target, unit["text"])
        segment = _matching_segment_for_span(unit, match)
        rank = (
            _MATCH_PRIORITY.get(str(match.get("match_type") or "none"), 9),
            0 if segment["speaker_class"] == "thatcher" else 1,
            order,
        )
        matches.append((rank, match, segment))
    archive_nonmt_matches = [
        row for row in matches
        if (
            row[2].get("archive_attribution_provenance")
            in _ARCHIVE_NONMT_PROVENANCE
            or row[2].get("archive_attribution_classification")
            in {"nonmt_content", "nonmt_label"}
        )
        and _acceptable_primary_contribution_match(match_target, row[1])
    ]
    archive_conflict_matches = [
        row for row in matches
        if (
            row[2].get("archive_attribution_provenance")
            in _ARCHIVE_CONFLICT_PROVENANCE
            or row[2].get("archive_attribution_classification")
            == "conflicting_archive_attribution"
        )
        and _acceptable_primary_contribution_match(match_target, row[1])
    ]
    archive_nonmt_evidence: dict[str, Any] | None = None
    if archive_nonmt_matches:
        _nonmt_rank, nonmt_match, nonmt_segment = min(
            archive_nonmt_matches, key=lambda row: row[0]
        )
        archive_nonmt_evidence = {
            "match_type": nonmt_match.get("match_type"),
            "wording_similarity": nonmt_match.get("wording_similarity"),
            "supporting_passage": nonmt_match.get("supporting_passage"),
            "surrounding_context": nonmt_match.get("surrounding_context"),
            "speaker_label": nonmt_segment.get("speaker_label"),
            "archive_attribution_classification": nonmt_segment.get(
                "archive_attribution_classification"
            ),
            "archive_attribution_provenance": nonmt_segment.get(
                "archive_attribution_provenance"
            ),
            "archive_attribution_inherited": bool(
                nonmt_segment.get(
                    "archive_attribution_match_spans_inherited_content"
                )
                or str(
                    nonmt_segment.get("archive_attribution_provenance") or ""
                ).startswith("inherited_")
            ),
            "archive_attribution_match_spans_inherited_content": bool(
                nonmt_segment.get(
                    "archive_attribution_match_spans_inherited_content"
                )
            ),
            "archive_attribution_match_spans_multiple_blocks": bool(
                nonmt_segment.get(
                    "archive_attribution_match_spans_multiple_blocks"
                )
            ),
            "archive_attribution_run_id": nonmt_segment.get(
                "archive_attribution_run_id"
            ),
            "archive_attribution_run_polarity": nonmt_segment.get(
                "archive_attribution_run_polarity", "none"
            ),
            "archive_source_section_id": nonmt_segment.get(
                "archive_source_section_id"
            ),
            "archive_source_section_baseline_polarity": nonmt_segment.get(
                "archive_source_section_baseline_polarity", "unverified"
            ),
            "archive_source_section_baseline_applied": bool(
                nonmt_segment.get("archive_source_section_baseline_applied")
            ),
            "archive_attribution_component_provenance": list(
                nonmt_segment.get(
                    "archive_attribution_component_provenance"
                ) or []
            ),
            "archive_attribution_matched_component_provenance": list(
                nonmt_segment.get(
                    "archive_attribution_matched_component_provenance"
                ) or []
            ),
            "archive_attribution_class_tokens": list(
                nonmt_segment.get("archive_attribution_class_tokens") or []
            ),
        }
    if matches:
        diagnostic_rank, diagnostic_match, diagnostic_segment = min(
            matches, key=lambda row: row[0]
        )
        positive_matches = [
            row for row in matches
            if row[2].get("speaker_class") == "thatcher"
            and row[2].get("archive_attribution_provenance")
            not in _ARCHIVE_NONMT_PROVENANCE | _ARCHIVE_CONFLICT_PROVENANCE
            and _acceptable_primary_contribution_match(match_target, row[1])
        ]
        if positive_matches:
            _positive_rank, best, segment = min(
                positive_matches,
                key=lambda row: (
                    _MATCH_PRIORITY.get(
                        str(row[1].get("match_type") or "none"), 9
                    ),
                    row[0][2],
                ),
            )
            if (
                diagnostic_segment.get("speaker_class") != "thatcher"
                and diagnostic_rank[0]
                < _MATCH_PRIORITY.get(
                    str(best.get("match_type") or "none"), 9
                )
            ):
                best = {
                    **best,
                    "stronger_non_thatcher_occurrence": {
                        "match_type": diagnostic_match.get("match_type"),
                        "speaker_class": diagnostic_segment.get("speaker_class"),
                        "speaker_label": diagnostic_segment.get("speaker_label"),
                        "archive_attribution_classification": (
                            diagnostic_segment.get(
                                "archive_attribution_classification"
                            )
                        ),
                        "archive_attribution_provenance": (
                            diagnostic_segment.get(
                                "archive_attribution_provenance"
                            )
                        ),
                        "archive_attribution_run_id": diagnostic_segment.get(
                            "archive_attribution_run_id"
                        ),
                        "archive_attribution_run_polarity": (
                            diagnostic_segment.get(
                                "archive_attribution_run_polarity", "none"
                            )
                        ),
                        "archive_source_section_id": diagnostic_segment.get(
                            "archive_source_section_id"
                        ),
                        "archive_source_section_baseline_polarity": (
                            diagnostic_segment.get(
                                "archive_source_section_baseline_polarity",
                                "unverified",
                            )
                        ),
                        "archive_attribution_component_provenance": list(
                            diagnostic_segment.get(
                                "archive_attribution_component_provenance"
                            ) or []
                        ),
                    },
                }
        else:
            best, segment = diagnostic_match, diagnostic_segment
    else:
        positive_matches = []
        best = {"match_type": "none", "supporting_passage": "", "span": None}
        segment = {
            "speaker_class": "unverified", "speaker_label": "",
            "evidence_basis": "no_transcript_segment",
            "archive_attribution_classification": "no_archive_attribution",
            "archive_attribution_provenance": "no_archive_attribution",
            "archive_attribution_class_tokens": [],
            "archive_attribution_conflict": False,
            "archive_attribution_run_id": None,
            "archive_attribution_run_polarity": "none",
            "archive_source_section_id": None,
            "archive_source_section_baseline_polarity": "unverified",
            "archive_source_section_baseline_applied": False,
        }
    cross_speaker = False
    cross_editorial_section = False
    acceptable_within_unit_matches = [
        row for row in matches
        if _acceptable_primary_contribution_match(match_target, row[1])
    ]
    if (
        segmentation["labels_detected"]
        and not acceptable_within_unit_matches
        and matches
    ):
        joined_match = research.extract_supporting_passage(
            match_target,
            " ".join(str(row.get("text") or "") for row in matching_units),
        )
        cross_speaker = bool(
            _MATCH_PRIORITY.get(
                str(joined_match.get("match_type") or "none"), 9
            )
            < _MATCH_PRIORITY.get(str(best.get("match_type") or "none"), 9)
        )
        cross_editorial_section = bool(
            cross_speaker
            and _match_crosses_editorial_source_sections(
                matching_units, joined_match
            )
        )
    if cross_speaker:
        best = {
            **joined_match,
            "match_type": "assembled_clauses",
            "cross_speaker_join_rejected": True,
        }
        segment = {
            "speaker_class": "unverified",
            "speaker_label": "multiple contributions",
            "evidence_basis": "cross_speaker_join_rejected",
            "archive_attribution_classification": "no_archive_attribution",
            "archive_attribution_provenance": "no_archive_attribution",
            "archive_attribution_class_tokens": [],
            "archive_attribution_conflict": False,
            "archive_attribution_run_id": None,
            "archive_attribution_run_polarity": "none",
            "archive_source_section_id": None,
            "archive_source_section_baseline_polarity": "unverified",
            "archive_source_section_baseline_applied": False,
        }
    if best.get("match_type") == "recorded_variant" and (
        not (
            relationship := _recorded_variant_match_relationship(
                match_target, best
            )
        )["accepted"]
        or float(best.get("wording_similarity") or 0.0) <= 0.0
    ):
        rejection_reason = (
            "zero_wording_similarity"
            if float(best.get("wording_similarity") or 0.0) <= 0.0
            else str(relationship["reason"])
        )
        best = {
            "match_type": "none",
            "wording_similarity": 0.0,
            "supporting_passage": "",
            "surrounding_context": "",
            "span": None,
            "recorded_variant_rejected": rejection_reason,
            "recorded_variant_relationship": relationship,
        }
        segment = {
            "speaker_class": "unverified",
            "speaker_label": "",
            "evidence_basis": "recorded_variant_without_lexical_relationship",
            "archive_attribution_classification": "no_archive_attribution",
            "archive_attribution_provenance": "no_archive_attribution",
            "archive_attribution_class_tokens": [],
            "archive_attribution_conflict": False,
            "archive_attribution_run_id": None,
            "archive_attribution_run_polarity": "none",
            "archive_source_section_id": None,
            "archive_source_section_baseline_polarity": "unverified",
            "archive_source_section_baseline_applied": False,
        }
        cross_speaker = False
        cross_editorial_section = False
    elif best.get("match_type") == "recorded_variant":
        best = {
            **best,
            "recorded_variant_relationship": (
                _recorded_variant_match_relationship(match_target, best)
            ),
        }
    speaker = {
        "verified": bool(
            segment.get("speaker_class") == "thatcher"
            and segment.get("archive_attribution_provenance")
            not in _ARCHIVE_NONMT_PROVENANCE | _ARCHIVE_CONFLICT_PROVENANCE
            and not cross_speaker
        ),
        "speaker_class": segment.get("speaker_class"),
        "speaker_label": segment.get("speaker_label"),
        "evidence_basis": segment.get("evidence_basis"),
        "document_author": str(validation.get("author") or ""),
        "labels_detected": bool(segmentation["labels_detected"]),
        "segments_inspected": len(segmentation["segments"]),
        "cross_speaker_join_rejected": cross_speaker,
        "cross_editorial_section_boundary_match_rejected": (
            cross_editorial_section
        ),
        "document_body_selector_kind": segmentation.get(
            "document_body_selector_kind", "unsupported_or_ambiguous_body"
        ),
        "archive_attribution_markup_detected": bool(
            segmentation.get("archive_attribution_markup_detected")
        ),
        "archive_attribution_classification": segment.get(
            "archive_attribution_classification", "no_archive_attribution"
        ),
        "archive_attribution_provenance": segment.get(
            "archive_attribution_provenance", "no_archive_attribution"
        ),
        "archive_attribution_inherited": bool(
            segment.get("archive_attribution_match_spans_inherited_content")
            or str(
                segment.get("archive_attribution_provenance") or ""
            ).startswith("inherited_")
        ),
        "archive_attribution_match_spans_inherited_content": bool(
            segment.get("archive_attribution_match_spans_inherited_content")
        ),
        "archive_attribution_match_spans_multiple_blocks": bool(
            segment.get("archive_attribution_match_spans_multiple_blocks")
        ),
        "archive_attribution_run_id": segment.get(
            "archive_attribution_run_id"
        ),
        "archive_attribution_run_polarity": segment.get(
            "archive_attribution_run_polarity", "none"
        ),
        "editorial_source_sections_detected": bool(
            segmentation.get("editorial_source_sections_detected")
        ),
        "reported_nonmt_editorial_sections_detected": bool(
            segmentation.get("reported_nonmt_editorial_sections_detected")
        ),
        "archive_source_section_count": int(
            segmentation.get("archive_source_section_count") or 0
        ),
        "archive_source_section_events": copy.deepcopy(
            segmentation.get("archive_source_section_events") or []
        ),
        "archive_editorial_marker_count": int(
            segmentation.get("archive_editorial_marker_count") or 0
        ),
        "archive_editorial_marker_events": copy.deepcopy(
            segmentation.get("archive_editorial_marker_events") or []
        ),
        "archive_source_section_id": segment.get(
            "archive_source_section_id"
        ),
        "archive_source_section_baseline_polarity": segment.get(
            "archive_source_section_baseline_polarity", "unverified"
        ),
        "archive_source_section_boundary_detected": bool(
            segment.get("archive_source_section_boundary_detected")
        ),
        "archive_source_section_boundary_kind": str(
            segment.get("archive_source_section_boundary_kind") or ""
        ),
        "archive_source_section_boundary_reason": str(
            segment.get("archive_source_section_boundary_reason") or ""
        ),
        "archive_source_section_label": str(
            segment.get("archive_source_section_label") or ""
        ),
        "archive_source_section_baseline_applied": bool(
            segment.get("archive_source_section_baseline_applied")
        ),
        "archive_attribution_component_provenance": list(
            segment.get("archive_attribution_component_provenance") or []
        ),
        "archive_attribution_matched_component_provenance": list(
            segment.get(
                "archive_attribution_matched_component_provenance"
            ) or []
        ),
        "archive_attribution_class_tokens": list(
            segment.get("archive_attribution_class_tokens") or []
        ),
        "archive_attribution_conflict": bool(
            segment.get("archive_attribution_conflict")
            or archive_conflict_matches
        ),
        "reported_or_secondary_nonmt_match": bool(archive_nonmt_evidence),
        "reported_or_secondary_nonmt_match_evidence": archive_nonmt_evidence,
        "direct_primary_attribution_basis": (
            str(segment.get("evidence_basis") or "")
            if (
                segment.get("speaker_class") == "thatcher"
                and segment.get("archive_attribution_provenance")
                not in (
                    _ARCHIVE_NONMT_PROVENANCE
                    | _ARCHIVE_CONFLICT_PROVENANCE
                )
                and not cross_speaker
            )
            else ""
        ),
    }
    best = {
        **best,
        "reported_or_secondary_nonmt_match": bool(archive_nonmt_evidence),
        "reported_or_secondary_nonmt_match_evidence": archive_nonmt_evidence,
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
            for value in values:
                if not isinstance(value, Mapping):
                    continue
                assigned_roles = set(value.get("assigned_roles") or [])
                trustworthy_packet_source = bool(
                    key == "sources"
                    and assigned_roles
                    & {
                        "wording_verification",
                        "attribution_support",
                        "source_event_support",
                        "historical_context_support",
                    }
                    and not assigned_roles
                    & {"discovery_only", "rejected_irrelevant"}
                )
                if key == "renderable_sources" or trustworthy_packet_source:
                    rows.append(value)
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
    """Recognise an explicit locator, exact-day, or date-and-event binding."""
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
    packet_event = _optional_historical_text(packet.get("source_event"))
    row_event = ""
    for key in ("source_event", "document_event", "event", "event_title"):
        row_event = _optional_historical_text(row.get(key))
        if row_event:
            break
    exact_day = bool(
        packet_date.get("known")
        and row_date.get("known")
        and packet_date.get("precision") == "day"
        and row_date.get("precision") == "day"
        and packet_date.get("iso_date") == row_date.get("iso_date")
    )
    return bool(
        row_document_id
        and (
            exact_day
            or (
                packet_date.get("known")
                and row_date.get("known")
                and _dates_directly_bound(packet_date, row_date)
                and packet_event
                and row_event
                and _event_equivalent(packet_event, row_event)
            )
        )
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
        "verified_text": _optional_historical_text(packet.get("verified_text")),
        "speaker": _optional_historical_text(packet.get("speaker")),
        "source_event": _optional_historical_text(packet.get("source_event")),
        "date": _optional_historical_text(packet.get("date")),
        "stable_locator": _optional_historical_text(packet.get("stable_locator")),
        "original_packet_values": {
            key: packet.get(key)
            for key in (
                "verified_text", "speaker", "source_event", "date",
                "stable_locator",
            )
        },
        "placeholder_values_treated_as_missing_for_classification": True,
        "verification_status": packet.get("verification_status"),
        "research_confidence": packet.get("research_confidence"),
        "wording_status": role.get("wording_status_after"),
        "historical_context_confidence": (
            role.get("confidence_after", {}).get("historical_context")
            if isinstance(role.get("confidence_after"), Mapping) else ""
        ),
        "current_occurrence_direct_mtf_document_ids": sorted(
            current_occurrence_ids, key=int
        ),
        "known_evidence_direct_mtf_document_ids": sorted(
            known_evidence_ids, key=int
        ),
        "known_evidence_direct_mtf_public_urls": sorted(direct_urls),
        "independently_inspected_mtf_document_ids": sorted(inspected_ids, key=int),
        "independently_inspected_hashes": sorted(inspected_hashes),
    }


def _normalised_date(value: Any) -> dict[str, Any]:
    if is_placeholder_text(value):
        return {
            "raw": " ".join(str(value or "").split()),
            "known": False,
            "iso_date": "",
            "year": None,
            "precision": "unknown",
            "placeholder_treated_as_missing": True,
        }
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


_GENERIC_EVENT_WRAPPERS = frozenset({
    "a", "an", "and", "at", "bracket", "commons", "debate", "hc",
    "house", "motion", "of", "on", "report", "s", "speech", "statement", "the",
    "to",
})


def _event_subject_tokens(value: Any) -> set[str]:
    text = _optional_historical_text(value)
    return {
        token for token in word_tokens(text)
        if token not in _GENERIC_EVENT_WRAPPERS
    }


def _event_equivalent(current: str, candidate: str) -> bool:
    left, right = _event_subject_tokens(current), _event_subject_tokens(candidate)
    if not left or not right:
        return False
    return left <= right or right <= left or len(left & right) / len(left | right) >= 0.55


def _occurrence_relation(
    candidate: Mapping[str, Any], current: Mapping[str, Any]
) -> str:
    """Resolve occurrence identity once before considering individual fields."""
    document_id = str(candidate.get("candidate_mtf_document_id") or "")
    current_ids = set(
        current.get("current_occurrence_direct_mtf_document_ids", [])
    )
    if candidate.get("current_occurrence_disproved") is True:
        return "current_occurrence_explicitly_disproved"
    if document_id and document_id in current_ids:
        return "same_current_occurrence"

    current_date = _normalised_date(current.get("date"))
    candidate_date = _normalised_date(candidate.get("document_date_evidence"))
    exact_day = bool(
        current_date.get("known")
        and candidate_date.get("known")
        and current_date.get("precision") == "day"
        and candidate_date.get("precision") == "day"
        and current_date.get("iso_date") == candidate_date.get("iso_date")
    )
    date_conflicts = _date_evidence_conflicts(current_date, candidate_date)
    current_event = _optional_historical_text(current.get("source_event"))
    candidate_event = _optional_historical_text(
        candidate.get("document_event_evidence")
    )
    if exact_day and _event_equivalent(current_event, candidate_event):
        return "same_current_occurrence"
    current_subject = _event_subject_tokens(current_event)
    candidate_subject = _event_subject_tokens(candidate_event)
    event_conflicts = bool(
        current_subject
        and candidate_subject
        and not _event_equivalent(current_event, candidate_event)
    )
    if date_conflicts:
        return "distinct_additional_occurrence"
    if exact_day:
        if event_conflicts and not (current_subject & candidate_subject):
            return "distinct_additional_occurrence"
        return "same_day_occurrence_ambiguous"
    if event_conflicts:
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


def _authorised_variant_relationship_in_passage(
    target: Mapping[str, Any], passage: str
) -> dict[str, Any]:
    """Return the shared relationship result for an authorised present variant."""
    for variant in target.get("recorded_variants", []):
        value = str(variant or "").strip()
        if not value or not _contains_token_sequence(passage, value):
            continue
        record = next((
            row for row in target.get("documented_variant_records", [])
            if isinstance(row, Mapping)
            and " ".join(str(row.get("search_variant") or "").split()) == value
        ), None)
        relationship = _variant_relationship_diagnostics(
            str(target.get("quotation_text") or ""), value, record
        )
        if relationship["accepted"]:
            return {"variant": value, **relationship}
    return {
        "accepted": False,
        "reason": "no_acceptable_recorded_variant_present_in_passage",
    }


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


def _requires_separate_semantic_review(disposition: str, reason: str) -> bool:
    folded = reason.casefold()
    return bool(
        "outside the present evidence-admission scope" in folded
        or any(marker in folded for marker in (
            "meaning adds", "meaning implies", "meaning strengthens",
            "published meaning", "unsupported claims about",
            "semantic review", "meaning review",
        ))
        or (
            disposition == "future_correction_needed"
            and any(word in folded for word in ("meaning", "interpretation", "semantic"))
        )
    )


def classify_changes(
    target: Mapping[str, Any],
    candidate: Mapping[str, Any],
    current: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any], bool, str]:
    """Return semantic advisory categories without applying any proposed value."""
    categories: list[str] = []
    proposed: dict[str, Any] = {}
    if candidate.get("candidate_semantic_reverification_status") == (
        "not_selected_for_reverification"
    ):
        return (
            [CATEGORY_NO_CHANGE],
            proposed,
            False,
            "candidate was not selected for fresh semantic reverification",
        )
    if candidate.get("candidate_evidence_stale") is True:
        return (
            [CATEGORY_NO_CHANGE],
            proposed,
            False,
            "candidate evidence identity is stale and cannot support admission",
        )
    document_id = str(candidate.get("candidate_mtf_document_id") or "")
    match_type = str(candidate.get("match_type") or "")
    wording_similarity = candidate.get("wording_similarity")
    if wording_similarity is None:
        wording_similarity = 1.0 if match_type in {
            "exact quotation", "recorded variant"
        } else 0.0
    passage_text = str(candidate.get("supporting_passage") or "")
    semantic_target = _semantic_match_target(target)
    variant_relationship = _authorised_variant_relationship_in_passage(
        semantic_target, passage_text
    )
    recorded_variant_is_authorised = bool(
        match_type != "recorded variant"
        or variant_relationship["accepted"]
    )
    strong = bool(
        candidate.get("accepted_as_primary_evidence")
        and candidate.get("speaker_author_evidence", {}).get("verified")
        and match_type in {"exact quotation", "recorded variant"}
        and float(wording_similarity or 0.0) > 0.0
        and passage_text.strip()
        and recorded_variant_is_authorised
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
            current.get("current_occurrence_direct_mtf_document_ids", [])
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
        occurrence_ambiguous = (
            occurrence_relation == "same_day_occurrence_ambiguous"
        )
        if occurrence_ambiguous:
            categories.append(CATEGORY_NEUTRAL_MATCH_REVIEW)
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
        event = _optional_historical_text(
            candidate.get("document_event_evidence")
        )
        current_event = _optional_historical_text(current.get("source_event"))
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
        direct_url_ids = set(
            current.get("current_occurrence_direct_mtf_document_ids", [])
        )
        if not additional_occurrence and (
            (identity_unknown and document_id)
            or (same_identity and document_id not in direct_url_ids)
            or (current_disproved and document_id)
        ) and not occurrence_ambiguous:
            categories.append(CATEGORY_LOCATOR_CORRECTION)
            proposed["stable_locator"] = f"Margaret Thatcher Foundation Document {document_id}"
            proposed["canonical_public_url"] = candidate.get("canonical_public_url")
        passage = str(candidate.get("supporting_passage") or "")
        verified_text = _optional_historical_text(current.get("verified_text"))
        quotation_text = str(current.get("quotation_text") or "")
        authoritative_wording = _authoritative_candidate_wording(
            semantic_target, candidate
        )
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
        speaker = _optional_historical_text(current.get("speaker"))
        provisional_speaker = bool(re.search(
            r"\b(?:attributed|attribution|provisional|possibly|unverified)\b",
            speaker,
            re.I,
        ))
        if not speaker or "thatcher" not in speaker.casefold() or provisional_speaker:
            categories.append(CATEGORY_ATTRIBUTION_CORRECTION)
            proposed["speaker"] = "Margaret Thatcher"
    review = target.get("current_gate_review")
    reason = str(review.get("reason") or "") if isinstance(review, Mapping) else ""
    disposition = str(target.get("current_gate_disposition") or "")
    wording_verified = strong
    context_sufficient = bool(
        strong
        and _normalised_date(candidate.get("document_date_evidence")).get("known")
        and _optional_historical_text(candidate.get("document_event_evidence"))
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


_SEMANTIC_FIELDS = (
    "raw_match_type",
    "match_type",
    "wording_similarity",
    "supporting_passage",
    "supporting_passage_sha256",
    "surrounding_context",
    "speaker_author_evidence",
    "candidate_classification",
    "candidate_classification_reason",
    "accepted_as_primary_evidence",
    "cross_speaker_join_rejected",
    "cross_editorial_section_boundary_match_rejected",
    "recorded_variant_relationship",
    "variant_relationship_diagnostics",
    "stronger_non_thatcher_occurrence",
    "document_body_selector_kind",
    "archive_attribution_markup_detected",
    "archive_attribution_classification",
    "archive_attribution_provenance",
    "archive_attribution_inherited",
    "archive_attribution_match_spans_inherited_content",
    "archive_attribution_match_spans_multiple_blocks",
    "archive_attribution_run_id",
    "archive_attribution_run_polarity",
    "editorial_source_sections_detected",
    "reported_nonmt_editorial_sections_detected",
    "archive_source_section_count",
    "archive_source_section_events",
    "archive_editorial_marker_count",
    "archive_editorial_marker_events",
    "archive_source_section_id",
    "archive_source_section_baseline_polarity",
    "archive_source_section_boundary_detected",
    "archive_source_section_boundary_kind",
    "archive_source_section_boundary_reason",
    "archive_source_section_label",
    "archive_source_section_baseline_applied",
    "archive_attribution_component_provenance",
    "archive_attribution_matched_component_provenance",
    "archive_attribution_class_tokens",
    "archive_attribution_conflict",
    "reported_or_secondary_nonmt_match",
    "reported_or_secondary_nonmt_match_evidence",
    "direct_primary_attribution_basis",
    "confidence",
)


def _fresh_semantic_fields(
    target: Mapping[str, Any],
    *,
    url: str,
    body: bytes,
    validation: Mapping[str, Any],
    extraction: Mapping[str, Any],
    reverified: bool,
) -> dict[str, Any]:
    """Rematch and reclassify one already-opened local MTF document."""
    match_target = _semantic_match_target(target)
    match, speaker = contribution_aware_match(
        match_target, extraction, body, validation
    )
    classification_extraction = dict(extraction)
    if speaker.get("verified"):
        metadata = dict(classification_extraction.get("metadata") or {})
        if speaker.get("direct_primary_attribution_basis") in {
            "explicit_archive_mt_content", "inherited_archive_mt_run",
            "archive_mt_run", "editorial_source_section_mt_baseline",
        }:
            metadata["author"] = "Margaret Thatcher"
        else:
            metadata["author"] = (
                str(metadata.get("author") or "") or "Margaret Thatcher"
            )
        classification_extraction["metadata"] = metadata
    classified = research.classify_candidate(
        match_target,
        url=url,
        extraction=classification_extraction,
        match=match,
        fetch_status="fetched",
    )
    raw_match_type = str(match.get("match_type") or "none")
    wording_similarity = float(match.get("wording_similarity") or 0.0)
    passage = str(match.get("supporting_passage") or "")
    has_primary_wording = bool(
        _acceptable_primary_contribution_match(match_target, match)
        and wording_similarity > 0.0
        and passage.strip()
    )
    accepted = bool(
        classified.get("classification") == "strong_primary_evidence"
        and classified.get("accepted_as_evidence")
        and speaker.get("verified")
        and has_primary_wording
    )
    archive_classification = str(
        speaker.get("archive_attribution_classification")
        or "no_archive_attribution"
    )
    archive_provenance = str(
        speaker.get("archive_attribution_provenance")
        or "no_archive_attribution"
    )
    archive_conflict = bool(speaker.get("archive_attribution_conflict"))
    selected_archive_conflict = bool(
        archive_provenance in _ARCHIVE_CONFLICT_PROVENANCE
        or archive_classification == "conflicting_archive_attribution"
    )
    reported_nonmt = bool(speaker.get("reported_or_secondary_nonmt_match"))
    archive_nonmt = bool(
        archive_provenance in _ARCHIVE_NONMT_PROVENANCE
        or archive_classification in {"nonmt_content", "nonmt_label"}
    )
    if archive_nonmt or selected_archive_conflict:
        accepted = False
    candidate_classification = str(
        classified.get("classification") or "no_support"
    )
    candidate_classification_reason = str(
        classified.get("decision_reason") or ""
    )
    if has_primary_wording and archive_nonmt:
        candidate_classification = "reported_or_secondary_archive_nonmt"
        candidate_classification_reason = (
            "wording occurs in archive-marked non-Thatcher material and is "
            "not direct Thatcher primary evidence"
        )
    elif has_primary_wording and selected_archive_conflict:
        candidate_classification = "archive_attribution_conflict_unverified"
        candidate_classification_reason = (
            "matching contribution has conflicting archive attribution markup"
        )
    fields = {
        "raw_match_type": raw_match_type,
        "match_type": _normalised_match_type(raw_match_type),
        "wording_similarity": wording_similarity,
        "supporting_passage": passage,
        "supporting_passage_sha256": sha256_bytes(passage.encode("utf-8")),
        "surrounding_context": str(match.get("surrounding_context") or ""),
        "speaker_author_evidence": speaker,
        "candidate_classification": candidate_classification,
        "candidate_classification_reason": candidate_classification_reason,
        "accepted_as_primary_evidence": accepted,
        "cross_speaker_join_rejected": bool(
            speaker.get("cross_speaker_join_rejected")
        ),
        "cross_editorial_section_boundary_match_rejected": bool(
            speaker.get("cross_editorial_section_boundary_match_rejected")
        ),
        "recorded_variant_relationship": copy.deepcopy(
            match.get("recorded_variant_relationship")
        ),
        "variant_relationship_diagnostics": copy.deepcopy(
            match_target.get("variant_relationship_diagnostics", [])
        ),
        "stronger_non_thatcher_occurrence": copy.deepcopy(
            match.get("stronger_non_thatcher_occurrence")
        ),
        "document_body_selector_kind": str(
            speaker.get("document_body_selector_kind")
            or "unsupported_or_ambiguous_body"
        ),
        "archive_attribution_markup_detected": bool(
            speaker.get("archive_attribution_markup_detected")
        ),
        "archive_attribution_classification": archive_classification,
        "archive_attribution_provenance": archive_provenance,
        "archive_attribution_inherited": bool(
            speaker.get("archive_attribution_inherited")
        ),
        "archive_attribution_match_spans_inherited_content": bool(
            speaker.get(
                "archive_attribution_match_spans_inherited_content"
            )
        ),
        "archive_attribution_match_spans_multiple_blocks": bool(
            speaker.get("archive_attribution_match_spans_multiple_blocks")
        ),
        "archive_attribution_run_id": speaker.get(
            "archive_attribution_run_id"
        ),
        "archive_attribution_run_polarity": str(
            speaker.get("archive_attribution_run_polarity") or "none"
        ),
        "editorial_source_sections_detected": bool(
            speaker.get("editorial_source_sections_detected")
        ),
        "reported_nonmt_editorial_sections_detected": bool(
            speaker.get("reported_nonmt_editorial_sections_detected")
        ),
        "archive_source_section_count": int(
            speaker.get("archive_source_section_count") or 0
        ),
        "archive_source_section_events": copy.deepcopy(
            speaker.get("archive_source_section_events") or []
        ),
        "archive_editorial_marker_count": int(
            speaker.get("archive_editorial_marker_count") or 0
        ),
        "archive_editorial_marker_events": copy.deepcopy(
            speaker.get("archive_editorial_marker_events") or []
        ),
        "archive_source_section_id": speaker.get("archive_source_section_id"),
        "archive_source_section_baseline_polarity": str(
            speaker.get("archive_source_section_baseline_polarity")
            or "unverified"
        ),
        "archive_source_section_boundary_detected": bool(
            speaker.get("archive_source_section_boundary_detected")
        ),
        "archive_source_section_boundary_kind": str(
            speaker.get("archive_source_section_boundary_kind") or ""
        ),
        "archive_source_section_boundary_reason": str(
            speaker.get("archive_source_section_boundary_reason") or ""
        ),
        "archive_source_section_label": str(
            speaker.get("archive_source_section_label") or ""
        ),
        "archive_source_section_baseline_applied": bool(
            speaker.get("archive_source_section_baseline_applied")
        ),
        "archive_attribution_component_provenance": list(
            speaker.get("archive_attribution_component_provenance") or []
        ),
        "archive_attribution_matched_component_provenance": list(
            speaker.get(
                "archive_attribution_matched_component_provenance"
            ) or []
        ),
        "archive_attribution_class_tokens": list(
            speaker.get("archive_attribution_class_tokens") or []
        ),
        "archive_attribution_conflict": archive_conflict,
        "reported_or_secondary_nonmt_match": reported_nonmt,
        "reported_or_secondary_nonmt_match_evidence": copy.deepcopy(
            speaker.get("reported_or_secondary_nonmt_match_evidence")
        ),
        "direct_primary_attribution_basis": str(
            speaker.get("direct_primary_attribution_basis") or ""
        ),
        "confidence": (
            "high" if accepted else "medium"
            if raw_match_type not in {"none", "distinctive_fragment_only"}
            else "low"
        ),
    }
    if reverified:
        if accepted:
            status = "reverified_accepted"
            reason = "fresh document rematch verified primary wording and Thatcher contribution"
        elif has_primary_wording and archive_nonmt:
            status = "reverified_rejected_archive_nonmt"
            reason = (
                "fresh wording occurs only as archive-marked non-Thatcher, "
                "reported, or secondary material"
            )
        elif has_primary_wording and selected_archive_conflict:
            status = "reverified_rejected_archive_attribution_conflict"
            reason = "fresh wording has conflicting archive attribution markup"
        elif fields["cross_speaker_join_rejected"] or raw_match_type == "assembled_clauses":
            status = "reverified_neutral_noncontiguous"
            reason = "fresh document rematch found only non-contiguous or cross-contribution wording"
        elif has_primary_wording and not speaker.get("verified"):
            status = "reverified_rejected_speaker"
            reason = "fresh wording occurs outside a verified Thatcher contribution"
        else:
            status = "reverified_rejected_no_support"
            reason = "fresh document rematch found no acceptable exact or authorised-variant support"
        fields.update({
            "candidate_semantic_reverification_status": status,
            "candidate_semantic_reverification_reason": reason,
            "candidate_semantically_reverified": True,
        })
    return fields


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
        "cross_editorial_section_boundary_match_rejected": False,
        "document_body_selector_kind": "unsupported_or_ambiguous_body",
        "archive_attribution_markup_detected": False,
        "archive_attribution_classification": "no_archive_attribution",
        "archive_attribution_provenance": "no_archive_attribution",
        "archive_attribution_inherited": False,
        "archive_attribution_match_spans_inherited_content": False,
        "archive_attribution_match_spans_multiple_blocks": False,
        "archive_attribution_run_id": None,
        "archive_attribution_run_polarity": "none",
        "editorial_source_sections_detected": False,
        "reported_nonmt_editorial_sections_detected": False,
        "archive_source_section_count": 0,
        "archive_source_section_events": [],
        "archive_editorial_marker_count": 0,
        "archive_editorial_marker_events": [],
        "archive_source_section_id": None,
        "archive_source_section_baseline_polarity": "unverified",
        "archive_source_section_boundary_detected": False,
        "archive_source_section_boundary_kind": "",
        "archive_source_section_boundary_reason": "",
        "archive_source_section_label": "",
        "archive_source_section_baseline_applied": False,
        "archive_attribution_component_provenance": [],
        "archive_attribution_matched_component_provenance": [],
        "archive_attribution_class_tokens": [],
        "archive_attribution_conflict": False,
        "reported_or_secondary_nonmt_match": False,
        "reported_or_secondary_nonmt_match_evidence": None,
        "direct_primary_attribution_basis": "",
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
    semantic_fields = _fresh_semantic_fields(
        target,
        url=url,
        body=body,
        validation=validation,
        extraction=extraction,
        reverified=False,
    )
    base.update({
        "candidate_status": "inspected",
        "local_file_sha256": str(record.get("local_archive_file_sha256") or ""),
        "document_event_evidence": str(validation.get("title") or ""),
        "document_date_evidence": str(validation.get("date") or ""),
        "document_identity_evidence": {
            "document_id": document_id,
            "canonical_url": str(validation.get("canonical_url") or ""),
            "declared_canonical_url": str(validation.get("declared_canonical_url") or ""),
            "publisher": "Margaret Thatcher Foundation",
            "article_text_sha256": str(validation.get("article_text_sha256") or ""),
        },
        "actual_regular_file_verified": True,
        **semantic_fields,
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
        match_type = str(best.get("match_type") or "") if best else ""
        wording_present = bool(
            best
            and match_type in {"exact quotation", "recorded variant"}
            and float(best.get("wording_similarity") or 0.0) > 0.0
            and str(best.get("supporting_passage") or "").strip()
            and best.get("candidate_semantic_reverification_status")
            != "not_selected_for_reverification"
        )
        speaker_verified = bool(
            wording_present
            and best
            and best.get("speaker_author_evidence", {}).get("verified")
        )
        wording_verified = bool(
            wording_present
            and best
            and best.get("accepted_as_primary_evidence")
        )
        context_sufficient = bool(
            wording_verified
            and best
            and best.get("surrounding_context")
            and _normalised_date(best.get("document_date_evidence")).get("known")
            and _optional_historical_text(best.get("document_event_evidence"))
            and best.get("canonical_public_url")
        )
        separate_semantic_review = bool(
            wording_verified
            and speaker_verified
            and context_sufficient
            and _requires_separate_semantic_review(
                str(target.get("current_gate_disposition") or ""), reason
            )
        )
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
                "candidate_semantic_reverification_status": best.get(
                    "candidate_semantic_reverification_status", "fresh_discovery_match"
                ),
                "confidence": best.get("confidence"),
            } if best else None),
            "speaker_attribution_verified": speaker_verified,
            "exact_or_acceptable_primary_variant_verified": wording_verified,
            "supporting_context_date_source_identity_sufficient": context_sufficient,
            "evidence_complete_but_separate_semantic_review_required": (
                separate_semantic_review
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
    semantic_review_ids = sorted(
        str(row["quote_id"])
        for row in blocked
        if row.get("evidence_complete_but_separate_semantic_review_required")
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
        "evidence_complete_semantic_review_quote_count": len(semantic_review_ids),
        "evidence_complete_semantic_review_quote_ids": semantic_review_ids,
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


def _candidate_reverification_selection_reason(
    candidate: Mapping[str, Any],
) -> str:
    categories = _normalised_existing_categories(candidate)
    existing_signal = bool(
        candidate.get("accepted_as_primary_evidence")
        or candidate.get("proposed_unblock")
        or candidate.get("candidate_classification") == "contradictory_evidence"
        or any(category != CATEGORY_NO_CHANGE for category in categories)
    )
    if existing_signal:
        return "existing_admission_or_advisory_signal"

    document_id = str(candidate.get("candidate_mtf_document_id") or "")
    url = str(candidate.get("canonical_public_url") or "")
    url_match = re.fullmatch(
        r"https://www\.margaretthatcher\.org/document/([0-9]+)", url
    )
    identity_shape_valid = bool(
        document_id.isdigit()
        and url_match
        and url_match.group(1) == document_id
        and re.fullmatch(
            r"[0-9a-f]{64}", str(candidate.get("local_file_sha256") or "")
        )
        and str(candidate.get("supporting_passage") or "").strip()
    )
    primary_wording_signal = bool(
        candidate.get("raw_match_type") in {"exact_quotation", "recorded_variant"}
        or candidate.get("match_type") in {"exact quotation", "recorded variant"}
        or candidate.get("candidate_classification") == "strong_primary_evidence"
    )
    if identity_shape_valid and primary_wording_signal:
        return "recorded_primary_wording_signal"
    return "not_selected_no_reverification_signal"


def _candidate_requires_identity_revalidation(candidate: Mapping[str, Any]) -> bool:
    return _candidate_reverification_selection_reason(candidate) != (
        "not_selected_no_reverification_signal"
    )


def _stale_identity_result(reason: str) -> dict[str, Any]:
    return {
        "candidate_evidence_identity_status": "stale",
        "candidate_evidence_stale": True,
        "candidate_evidence_identity_reason": reason,
    }


def _open_candidate_for_reverification(
    mirror: LocalArchiveMirror, candidate: Mapping[str, Any]
) -> tuple[dict[str, Any], Mapping[str, Any] | None, Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Open and identity-check one selected candidate without any discovery."""
    selection_reason = _candidate_reverification_selection_reason(candidate)
    if selection_reason == "not_selected_no_reverification_signal":
        return ({
            "candidate_evidence_identity_status": "not_selected_for_revalidation",
            "candidate_evidence_stale": False,
            "candidate_evidence_identity_reason": (
                "candidate did not retain a bounded reverification signal"
            ),
            "candidate_reverification_selection_reason": selection_reason,
        }, None, None, None)
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
        return ({
            **_stale_identity_result("recorded_candidate_identity_is_invalid"),
            "candidate_reverification_selection_reason": selection_reason,
        }, None, None, None)
    try:
        record = mirror.read(url)
    except Exception:
        return ({
            **_stale_identity_result("candidate_file_is_missing_or_unsafe"),
            "candidate_reverification_selection_reason": selection_reason,
        }, None, None, None)
    if not record or record.get("status") != "fetched":
        return ({
            **_stale_identity_result("candidate_file_is_missing_or_unreadable"),
            "candidate_reverification_selection_reason": selection_reason,
        }, None, None, None)
    current_hash = str(record.get("local_archive_file_sha256") or "")
    if current_hash != recorded_hash:
        return ({
            **_stale_identity_result("candidate_file_sha256_changed"),
            "candidate_reverification_selection_reason": selection_reason,
        }, None, None, None)
    body = bytes(record.get("body") or b"")
    validation = research.inspect_mtf_document(
        url, str(record.get("content_type") or ""), body
    )
    if (
        not validation.get("valid")
        or str(validation.get("document_number") or "") != document_id
        or _document_id_from_value(validation.get("canonical_url")) != document_id
        or _document_id_from_value(validation.get("declared_canonical_url")) != document_id
    ):
        return ({
            **_stale_identity_result("candidate_mtf_document_identity_changed"),
            "candidate_reverification_selection_reason": selection_reason,
        }, None, None, None)
    extraction = research.extract_page_text({
        **record,
        "mtf_document_validation": validation,
    })
    return ({
        "candidate_evidence_identity_status": "valid",
        "candidate_evidence_stale": False,
        "candidate_evidence_identity_reason": (
            "recorded MTF document identity and exact local file SHA-256 still match"
        ),
        "candidate_reverification_selection_reason": selection_reason,
    }, record, validation, extraction)


def revalidate_candidate_identity(
    mirror: LocalArchiveMirror, candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Revalidate only a ledger candidate's local-file identity."""
    identity, _record, _validation, _extraction = (
        _open_candidate_for_reverification(mirror, candidate)
    )
    return identity


def _neutral_semantic_fields(
    *, status: str, reason: str, reverified: bool = False
) -> dict[str, Any]:
    return {
        "raw_match_type": "none",
        "match_type": "no support",
        "wording_similarity": 0.0,
        "supporting_passage": "",
        "supporting_passage_sha256": sha256_bytes(b""),
        "surrounding_context": "",
        "speaker_author_evidence": {
            "verified": False,
            "speaker_class": "unverified",
            "evidence_basis": reason,
        },
        "candidate_classification": "no_support",
        "candidate_classification_reason": reason,
        "accepted_as_primary_evidence": False,
        "cross_speaker_join_rejected": False,
        "cross_editorial_section_boundary_match_rejected": False,
        "document_body_selector_kind": "unsupported_or_ambiguous_body",
        "archive_attribution_markup_detected": False,
        "archive_attribution_classification": "no_archive_attribution",
        "archive_attribution_provenance": "no_archive_attribution",
        "archive_attribution_inherited": False,
        "archive_attribution_match_spans_inherited_content": False,
        "archive_attribution_match_spans_multiple_blocks": False,
        "archive_attribution_run_id": None,
        "archive_attribution_run_polarity": "none",
        "editorial_source_sections_detected": False,
        "reported_nonmt_editorial_sections_detected": False,
        "archive_source_section_count": 0,
        "archive_source_section_events": [],
        "archive_editorial_marker_count": 0,
        "archive_editorial_marker_events": [],
        "archive_source_section_id": None,
        "archive_source_section_baseline_polarity": "unverified",
        "archive_source_section_boundary_detected": False,
        "archive_source_section_boundary_kind": "",
        "archive_source_section_boundary_reason": "",
        "archive_source_section_label": "",
        "archive_source_section_baseline_applied": False,
        "archive_attribution_component_provenance": [],
        "archive_attribution_matched_component_provenance": [],
        "archive_attribution_class_tokens": [],
        "archive_attribution_conflict": False,
        "reported_or_secondary_nonmt_match": False,
        "reported_or_secondary_nonmt_match_evidence": None,
        "direct_primary_attribution_basis": "",
        "confidence": "low",
        "candidate_semantic_reverification_status": status,
        "candidate_semantic_reverification_reason": reason,
        "candidate_semantically_reverified": reverified,
    }


def _reclassify_candidate(
    mirror: LocalArchiveMirror,
    target: Mapping[str, Any],
    source_candidate: Mapping[str, Any],
) -> dict[str, Any]:
    candidate = copy.deepcopy(dict(source_candidate))
    recorded_history = {
        key: copy.deepcopy(source_candidate.get(key))
        for key in _SEMANTIC_FIELDS
        if key in source_candidate
    }
    identity, record, validation, extraction = _open_candidate_for_reverification(
        mirror, source_candidate
    )
    candidate.update({
        "quotation_text": target["quotation_text"],
        "current_gate_disposition": target["current_gate_disposition"],
        "current_gate_status": target["current_gate_status"],
        "current_unresolved_status": target["current_unresolved_status"],
        "current_values": current_values(target),
        "recorded_semantic_history": recorded_history,
        **identity,
    })
    candidate["recorded_accepted_as_primary_evidence"] = bool(
        source_candidate.get("accepted_as_primary_evidence")
    )
    if identity["candidate_evidence_identity_status"] == "valid":
        assert record is not None and validation is not None and extraction is not None
        candidate.update({
            "candidate_status": "inspected",
            "actual_regular_file_verified": True,
            "local_file_sha256": str(
                record.get("local_archive_file_sha256") or ""
            ),
            "document_event_evidence": _optional_historical_text(
                validation.get("title")
            ),
            "document_date_evidence": _optional_historical_text(
                validation.get("date")
            ),
            "document_identity_evidence": {
                "document_id": str(candidate.get("candidate_mtf_document_id") or ""),
                "canonical_url": str(validation.get("canonical_url") or ""),
                "declared_canonical_url": str(
                    validation.get("declared_canonical_url") or ""
                ),
                "publisher": "Margaret Thatcher Foundation",
                "article_text_sha256": str(
                    validation.get("article_text_sha256") or ""
                ),
            },
        })
        if extraction.get("status") == "extracted":
            candidate.update(_fresh_semantic_fields(
                target,
                url=str(candidate.get("canonical_public_url") or ""),
                body=bytes(record.get("body") or b""),
                validation=validation,
                extraction=extraction,
                reverified=True,
            ))
        else:
            candidate.update(_neutral_semantic_fields(
                status="semantic_reverification_failed_text_extraction",
                reason="current candidate document text could not be extracted",
            ))
    elif identity["candidate_evidence_identity_status"] == "stale":
        candidate.update(_neutral_semantic_fields(
            status="not_reverified_identity_stale",
            reason="semantic reverification was not possible because file identity is stale",
        ))
    else:
        candidate.update(_neutral_semantic_fields(
            status="not_selected_for_reverification",
            reason="candidate was not selected by the existing revalidation policy",
        ))
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
        "classification_rerun_without_discovery": bool(
            candidate.get("candidate_semantically_reverified")
        ),
        "candidate_document_rematched_without_discovery": bool(
            candidate.get("candidate_semantically_reverified")
        ),
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
        "candidate_semantic_reverification_required_count": len(selected),
        "candidate_semantic_reverified_count": sum(
            bool(row.get("candidate_semantically_reverified"))
            for row in selected
        ),
        "candidate_semantic_reverification_accepted_count": sum(
            row.get("candidate_semantic_reverification_status")
            == "reverified_accepted"
            for row in selected
        ),
        "candidate_semantic_reverification_rejected_count": sum(
            bool(row.get("candidate_semantically_reverified"))
            and not row.get("accepted_as_primary_evidence")
            for row in selected
        ),
        "reverified_positive_candidate_count": sum(
            row.get("candidate_semantic_reverification_status")
            == "reverified_accepted"
            for row in selected
        ),
        "placeholder_variant_rejection_count": sum(
            int(
                _semantic_match_target(row).get(
                    "placeholder_variant_rejection_count"
                ) or 0
            )
            for row in targets
        ),
        "recorded_positive_candidates_remaining_valid": sum(
            row.get("candidate_evidence_identity_status") == "valid"
            and row.get("candidate_semantic_reverification_status")
            == "reverified_accepted"
            for row in positive
        ),
        "positive_candidates_remaining_valid": sum(
            row.get("candidate_evidence_identity_status") == "valid"
            and row.get("candidate_semantic_reverification_status")
            == "reverified_accepted"
            for row in selected
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
            "candidate_semantically_reverified_advisory_package_with_provisional_negatives"
        ),
        **_archive_attribution_summary_counters(selected),
    })
    return summary


def _archive_attribution_summary_counters(
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Count markup effects once per freshly opened candidate document."""
    reverified = [
        row for row in candidates if row.get("candidate_semantically_reverified")
    ]
    return {
        "candidates_with_archive_attribution_markup": sum(
            bool(row.get("archive_attribution_markup_detected"))
            for row in reverified
        ),
        "accepted_candidates_using_archive_mt_markup": sum(
            bool(row.get("accepted_as_primary_evidence"))
            and row.get("direct_primary_attribution_basis")
            in {
                "explicit_archive_mt_content",
                "inherited_archive_mt_run",
                "archive_mt_run",
            }
            for row in reverified
        ),
        "rejected_candidates_with_direct_match_in_archive_nonmt": sum(
            row.get("candidate_semantic_reverification_status")
            == "reverified_rejected_archive_nonmt"
            for row in reverified
        ),
        "reported_or_secondary_nonmt_match_count": sum(
            bool(row.get("reported_or_secondary_nonmt_match"))
            for row in reverified
        ),
        "archive_attribution_conflict_count": sum(
            bool(row.get("archive_attribution_conflict"))
            for row in reverified
        ),
        "candidates_with_editorial_source_sections": sum(
            bool(row.get("editorial_source_sections_detected"))
            for row in reverified
        ),
        "accepted_candidates_using_editorial_mt_section_baseline": sum(
            bool(row.get("accepted_as_primary_evidence"))
            and row.get("direct_primary_attribution_basis")
            == "editorial_source_section_mt_baseline"
            for row in reverified
        ),
        "candidates_with_reported_nonmt_editorial_sections": sum(
            bool(row.get("reported_nonmt_editorial_sections_detected"))
            for row in reverified
        ),
        "rejected_cross_editorial_section_boundary_matches": sum(
            bool(row.get("cross_editorial_section_boundary_match_rejected"))
            for row in reverified
        ),
    }


def render_reclassification_report(summary: Mapping[str, Any]) -> str:
    return "\n".join((
        "# Existing-package historical-context reclassification",
        "",
        f"Programme: `{PROGRAMME_VERSION}`",
        "",
        "This private advisory package reused the recorded candidate ledger, reopened selected local MTF documents, and freshly rematched and reclassified them. No quotation discovery or archive inventory scan was performed.",
        "",
        "## Candidate evidence boundary",
        "",
        f"- Source run inventory was unstable: {str(summary['source_run_inventory_was_unstable']).lower()}",
        f"- Candidate-level evidence identities revalidated: {str(summary['candidate_level_evidence_identities_revalidated']).lower()}",
        f"- Candidates selected for identity revalidation: {summary['candidate_identity_revalidation_required_count']}",
        f"- Candidate identities still valid: {summary['candidate_identity_valid_count']}",
        f"- Stale candidates: {summary['stale_candidate_count']}",
        f"- Candidates requiring semantic reverification: {summary['candidate_semantic_reverification_required_count']}",
        f"- Candidates semantically reverified: {summary['candidate_semantic_reverified_count']}",
        f"- Fresh semantic acceptances: {summary['candidate_semantic_reverification_accepted_count']}",
        f"- Fresh semantic rejections: {summary['candidate_semantic_reverification_rejected_count']}",
        f"- Previously recorded positive candidates: {summary['recorded_positive_candidate_count']}",
        f"- Previously recorded positives remaining identity-valid and freshly accepted: {summary['recorded_positive_candidates_remaining_valid']}",
        f"- Freshly accepted reverified selected candidates: {summary['reverified_positive_candidate_count']}",
        f"- Identity-valid freshly accepted candidates across all prior statuses: {summary['positive_candidates_remaining_valid']}",
        f"- Placeholder variants rejected: {summary['placeholder_variant_rejection_count']}",
        f"- Candidates whose selected body contains recognised archive attribution markup: {summary['candidates_with_archive_attribution_markup']}",
        f"- Accepted candidates using explicit or inherited archive MT attribution: {summary['accepted_candidates_using_archive_mt_markup']}",
        f"- Rejected candidates matched directly in explicit or inherited archive non-MT material: {summary['rejected_candidates_with_direct_match_in_archive_nonmt']}",
        f"- Candidates retaining explicit or inherited reported/secondary non-MT matches: {summary['reported_or_secondary_nonmt_match_count']}",
        f"- Candidates with a match under explicit or inherited conflicting archive attribution: {summary['archive_attribution_conflict_count']}",
        f"- Candidates whose selected body contains constrained editorial source sections: {summary['candidates_with_editorial_source_sections']}",
        f"- Accepted candidates using an editorial MT source-section baseline: {summary['accepted_candidates_using_editorial_mt_section_baseline']}",
        f"- Candidates containing a reportorial non-MT editorial source section: {summary['candidates_with_reported_nonmt_editorial_sections']}",
        f"- Candidates whose wording was rejected across an editorial source-section boundary: {summary['rejected_cross_editorial_section_boundary_matches']}",
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
        f"- Evidence-complete quotations still requiring separate semantic review: {summary['evidence_complete_semantic_review_quote_count']} ({', '.join(summary['evidence_complete_semantic_review_quote_ids']) if summary['evidence_complete_semantic_review_quote_ids'] else 'none'})",
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
        "selected_candidate_documents_rematched_without_discovery": True,
        "records": candidates,
        "advisory_only": True,
        "private_root_not_recorded": True,
    }
    blocked_document = {
        "schema_version": 2,
        "record_kind": "blocked_historical_context_quote_reassessment",
        "semantic_gate_policy_version": SEMANTIC_GATE_POLICY_VERSION,
        "blocked_quote_count": len(blocked),
        "evidence_complete_semantic_review_quote_count": summary[
            "evidence_complete_semantic_review_quote_count"
        ],
        "evidence_complete_semantic_review_quote_ids": summary[
            "evidence_complete_semantic_review_quote_ids"
        ],
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
        "recorded_positive_candidates_remaining_valid": summary.get(
            "recorded_positive_candidates_remaining_valid"
        ),
        "reverified_positive_candidate_count": summary.get(
            "reverified_positive_candidate_count"
        ),
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
