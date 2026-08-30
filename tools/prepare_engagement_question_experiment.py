#!/usr/bin/env python3
"""Prepare, validate, or inspect the offline substantive-question experiment."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


PROJECT_MODULE_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_MODULE_ROOT))

import engagement_question_experiment as experiment  # noqa: E402
from historical_context_formatter import (  # noqa: E402
    THATCHER_ATTRIBUTION_RULE_VERSION,
    format_context_reply_public,
    load_and_validate_corpus,
    packet_for_posted_quote,
    packet_is_attributed_to_margaret_thatcher,
)

CATALOGUE_RELATIVE_PATH = Path(
    "engagement_question_experiment/approved_question_catalogue.json"
)
LINES_USED_NAME = "lines_used.json"
REGULAR_RECEIPT_NAME = "regular_post_receipt.json"
BOT_STATE_NAME = "bot_state.json"
QUOTE_SOURCE_NAME = "mrsMThatcher.txt"
QUOTE_ANALYSIS_NAME = "quote_analysis.json"
QUOTE_OVERRIDES_NAME = "quote_analysis_overrides.json"
LOCAL_CONFIG_NAME = "mrsMThatcher.local.json"
RUNTIME_MANIFEST_RELATIVE_PATH = Path(
    "semantic_alignment_research/quote_attribution_cleanup_001/"
    "deployment_candidate/runtime_eligible_quote_manifest.json"
)
RESEARCH_RELATIVE_PATH = Path(
    "semantic_alignment_research/quote_research_full_001"
)
RESEARCH_PACKETS_NAME = "research_packets.json"
VERIFICATION_ALLOWED = {"exact", "normalised", "excerpt", "variant"}
CONFIDENCE_ALLOWED = {"high", "medium"}
HISTORICAL_CONTEXT_DEFAULT = {
    "enabled": False,
    "maximum_length": 4000,
    "include_meaning": True,
    "include_source": True,
    "include_verification": True,
}


class PreparationError(RuntimeError):
    """The requested offline preparation cannot be completed safely."""


def iso_utc(value: datetime | None = None) -> str:
    """Return a stable UTC timestamp."""

    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _stat_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def stable_read_bytes(path: Path, *, label: str, maximum_bytes: int) -> bytes:
    """Read one unchanged regular file without following a symbolic link."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        before_path = os.lstat(absolute)
    except OSError as exc:
        raise PreparationError(f"cannot inspect {label}: {absolute}: {exc}") from exc
    if not stat.S_ISREG(before_path.st_mode):
        raise PreparationError(f"{label} must be a regular file: {absolute}")
    if before_path.st_size > maximum_bytes:
        raise PreparationError(f"{label} exceeds its bounded size: {absolute}")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise PreparationError("stable input capture requires O_NOFOLLOW support")
    try:
        descriptor = os.open(absolute, flags | nofollow)
    except OSError as exc:
        raise PreparationError(f"cannot open stable {label}: {absolute}: {exc}") from exc
    try:
        before_fd = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before_fd.st_mode)
            or _stat_identity(before_fd) != _stat_identity(before_path)
        ):
            raise PreparationError(f"{label} changed before capture: {absolute}")
        chunks: list[bytes] = []
        observed = 0
        while observed <= maximum_bytes:
            chunk = os.read(descriptor, min(65536, maximum_bytes + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
        source = b"".join(chunks)
        repeated = os.pread(descriptor, before_fd.st_size + 1, 0)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after_path = os.lstat(absolute)
    except OSError as exc:
        raise PreparationError(f"{label} disappeared during capture: {absolute}") from exc
    if (
        len(source) != before_fd.st_size
        or source != repeated
        or _stat_identity(before_fd) != _stat_identity(after_fd)
        or _stat_identity(before_fd) != _stat_identity(after_path)
    ):
        raise PreparationError(f"{label} changed during stable capture: {absolute}")
    return source


def strict_json_bytes(source: bytes, *, label: str) -> Any:
    """Decode strict UTF-8 JSON with duplicate-field rejection."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PreparationError(f"{label} contains duplicate field {key!r}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise PreparationError(f"{label} contains non-finite JSON value {value}")

    try:
        return json.loads(
            source.decode("utf-8", errors="strict"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except PreparationError:
        raise
    except Exception as exc:
        raise PreparationError(f"{label} is not strict UTF-8 JSON") from exc


def stable_json(path: Path, *, label: str, maximum_bytes: int) -> tuple[Any, bytes]:
    """Load strict JSON from one stable bounded regular file."""
    source = stable_read_bytes(path, label=label, maximum_bytes=maximum_bytes)
    return strict_json_bytes(source, label=label), source


def regular_receipt_must_be_absent(runtime_root: Path) -> None:
    """Refuse even a malformed or symbolic-link receipt namespace entry."""

    path = runtime_root / REGULAR_RECEIPT_NAME
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PreparationError(f"cannot inspect regular-post receipt: {path}: {exc}") from exc
    raise PreparationError(
        f"plan preparation is blocked while {REGULAR_RECEIPT_NAME} exists: {path}"
    )


def capture_used_history(runtime_root: Path) -> tuple[set[str], bytes, str]:
    """Capture two identical complete used-history reads."""

    path = runtime_root / LINES_USED_NAME
    first = stable_read_bytes(
        path,
        label="used-quotation history",
        maximum_bytes=2 * 1024 * 1024,
    )
    second = stable_read_bytes(
        path,
        label="used-quotation history",
        maximum_bytes=2 * 1024 * 1024,
    )
    if first != second:
        raise PreparationError("used-quotation history changed during capture")
    value = strict_json_bytes(first, label="used-quotation history")
    if not isinstance(value, list):
        raise PreparationError("used-quotation history must be a JSON list")
    if value != sorted(set(value)):
        raise PreparationError("used-quotation history is not canonical and unique")
    if any(type(item) is not str or not experiment.HEX64_RE.fullmatch(item) for item in value):
        raise PreparationError("used-quotation history contains a non-SHA-256 identity")
    return set(value), first, hashlib.sha256(first).hexdigest()


def canonical_quote_source(runtime_root: Path) -> tuple[dict[str, str], list[str], bytes]:
    """Resolve exact current production roots without silent normalisation."""

    source = stable_read_bytes(
        runtime_root / QUOTE_SOURCE_NAME,
        label="canonical quotation source",
        maximum_bytes=4 * 1024 * 1024,
    )
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PreparationError("canonical quotation source is not UTF-8") from exc
    lines = text.splitlines(keepends=True)
    result: dict[str, str] = {}
    canonical_lines: list[str] = []
    for physical_line, raw in enumerate(lines, 1):
        if raw.endswith("\r\n"):
            exact = raw[:-2]
        elif raw.endswith(("\n", "\r")):
            exact = raw[:-1]
        else:
            exact = raw
        # Production's existing root helper calls rstrip(). A trailing-space
        # source would therefore make raw source text and public root identity
        # ambiguous; preparation fails instead of normalising it silently.
        if exact != exact.rstrip():
            raise PreparationError(
                f"canonical quotation line {physical_line} has trailing whitespace"
            )
        canonical_lines.append(exact + (raw[len(exact) :] if len(raw) > len(exact) else ""))
        if not exact:
            continue
        quote_id = experiment.sha256_text(exact)
        previous = result.setdefault(quote_id, exact)
        if previous != exact:
            raise PreparationError("canonical quotation SHA-256 collision")
    if not result:
        raise PreparationError("canonical quotation source contains no roots")
    return result, canonical_lines, source


def _deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_quote_analysis(runtime_root: Path) -> dict[str, Any]:
    """Load current quote analysis with the production override semantics."""

    raw, _source = stable_json(
        runtime_root / QUOTE_ANALYSIS_NAME,
        label="quote analysis",
        maximum_bytes=32 * 1024 * 1024,
    )
    if (
        not isinstance(raw, dict)
        or raw.get("schema_version") != 2
        or raw.get("analysis_kind") != "quotes"
        or not isinstance(raw.get("items"), dict)
    ):
        raise PreparationError("quote analysis structure is invalid")
    override_path = runtime_root / QUOTE_OVERRIDES_NAME
    if not override_path.exists():
        return raw
    overrides, _override_source = stable_json(
        override_path,
        label="quote analysis overrides",
        maximum_bytes=4 * 1024 * 1024,
    )
    if not isinstance(overrides, dict):
        raise PreparationError("quote analysis overrides are invalid")
    quote_overrides = overrides.get("quote_overrides", {})
    if not isinstance(quote_overrides, dict):
        raise PreparationError("quote analysis override map is invalid")
    result = copy.deepcopy(raw)
    for quote_id, override in quote_overrides.items():
        if not isinstance(override, dict):
            raise PreparationError(f"quote analysis override is invalid for {quote_id}")
        item = result["items"].get(str(quote_id))
        if not isinstance(item, dict):
            continue
        expected_text = override.get("expected_text")
        if expected_text is not None and expected_text != item.get("text"):
            continue
        expected_lines = override.get("expected_line_numbers", [])
        if not isinstance(expected_lines, list):
            raise PreparationError(f"quote analysis override lines are invalid for {quote_id}")
        try:
            if not {int(value) for value in expected_lines}.issubset(
                {int(value) for value in item.get("line_numbers", [])}
            ):
                continue
        except (TypeError, ValueError) as exc:
            raise PreparationError(
                f"quote analysis override lines are invalid for {quote_id}"
            ) from exc
        patch = override.get("analysis_patch")
        if not isinstance(patch, dict) or not isinstance(item.get("analysis"), dict):
            raise PreparationError(f"quote analysis override patch is invalid for {quote_id}")
        item["analysis"] = _deep_merge(item["analysis"], patch)
    return result


def runtime_manifest_ids(
    runtime_root: Path,
    quote_text_by_id: Mapping[str, str],
    packets: Mapping[str, Mapping[str, Any]],
) -> tuple[set[str], dict[str, str]]:
    """Revalidate the current exact attribution-eligible runtime partition."""

    path = runtime_root / RUNTIME_MANIFEST_RELATIVE_PATH
    manifest, _source = stable_json(
        path,
        label="runtime eligible quotation manifest",
        maximum_bytes=4 * 1024 * 1024,
    )
    if not isinstance(manifest, dict):
        raise PreparationError("runtime eligible quotation manifest is invalid")
    runtime_ids = manifest.get("runtime_eligible_quote_ids")
    resolved_ids = manifest.get("resolved_manifest_quote_ids")
    aliases = manifest.get("runtime_quote_aliases")
    source_hashes = manifest.get("source_file_hashes")
    count = manifest.get("runtime_eligible_quote_count")
    source_count = manifest.get("source_record_count")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("eligibility_rule_version")
        != THATCHER_ATTRIBUTION_RULE_VERSION
        or type(count) is not int
        or type(source_count) is not int
        or not isinstance(runtime_ids, list)
        or not isinstance(resolved_ids, list)
        or not isinstance(aliases, dict)
        or not isinstance(source_hashes, dict)
        or any(
            type(value) is not str or not experiment.HEX64_RE.fullmatch(value)
            for value in runtime_ids
        )
        or any(
            type(value) is not str or not experiment.HEX64_RE.fullmatch(value)
            for value in resolved_ids
        )
        or any(
            type(key) is not str
            or not experiment.HEX64_RE.fullmatch(key)
            or type(value) is not str
            or not experiment.HEX64_RE.fullmatch(value)
            for key, value in aliases.items()
        )
    ):
        raise PreparationError("runtime eligible quotation manifest structure is invalid")
    if (
        count <= 0
        or len(runtime_ids) != count
        or len(set(runtime_ids)) != count
        or len(resolved_ids) != count
        or len(set(resolved_ids)) != count
        or not set(aliases).issubset(set(runtime_ids))
        or sorted(aliases.get(value, value) for value in runtime_ids) != resolved_ids
        or source_count != len(quote_text_by_id)
        # The production identity helper collapses whitespace.  The manifest's
        # five audited aliases therefore map runtime identities to exact raw
        # research/source hashes.  Plan and catalogue identities are the exact
        # raw hashes, so source membership is checked after alias resolution.
        or not set(resolved_ids).issubset(quote_text_by_id)
    ):
        raise PreparationError("runtime eligible quotation manifest counts are invalid")
    if (
        source_hashes.get("active_source")
        != experiment.sha256_file(runtime_root / QUOTE_SOURCE_NAME)
        or source_hashes.get("completed_quote_research")
        != experiment.sha256_file(
            runtime_root / RESEARCH_RELATIVE_PATH / RESEARCH_PACKETS_NAME
        )
    ):
        raise PreparationError("runtime eligible quotation manifest sources are stale")
    eligible_packet_ids = {
        str(quote_id)
        for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    if set(resolved_ids) != eligible_packet_ids:
        raise PreparationError(
            "runtime eligible quotation manifest differs from validated attribution"
        )
    return set(resolved_ids), {str(key): str(value) for key, value in aliases.items()}


def historical_context_config(runtime_root: Path) -> dict[str, Any]:
    """Read only the existing local formatter object, never credentials or providers."""

    path = runtime_root / LOCAL_CONFIG_NAME
    if not path.exists():
        return dict(HISTORICAL_CONTEXT_DEFAULT)
    local, _source = stable_json(
        path,
        label="local bot configuration",
        maximum_bytes=64 * 1024,
    )
    if not isinstance(local, dict):
        raise PreparationError("local bot configuration is not an object")
    value = local.get("historical_context_reply", HISTORICAL_CONTEXT_DEFAULT)
    required = {
        "enabled",
        "maximum_length",
        "include_meaning",
        "include_source",
        "include_verification",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise PreparationError("historical-context configuration fields mismatch")
    if any(
        type(value.get(key)) is not bool
        for key in ("enabled", "include_meaning", "include_source", "include_verification")
    ):
        raise PreparationError("historical-context configuration booleans are invalid")
    if type(value.get("maximum_length")) is not int or not 120 <= value["maximum_length"] <= 25_000:
        raise PreparationError("historical-context maximum length is invalid")
    return dict(value)


def _mm_dd_in_window(value: str, start: str, end: str) -> bool:
    if start <= end:
        return start <= value <= end
    return value >= start or value <= end


def currently_production_eligible(analysis: Mapping[str, Any], *, today_mm_dd: str) -> bool:
    """Apply the current hard seasonal exclusion without selection RNG."""

    seasonality = analysis.get("seasonality")
    if not isinstance(seasonality, Mapping):
        return True
    windows = seasonality.get("preferred_windows")
    if not isinstance(windows, list) or not windows:
        return True
    in_window = False
    for window in windows:
        if not isinstance(window, Mapping):
            continue
        start = str(window.get("start_mm_dd") or "")
        end = str(window.get("end_mm_dd") or "")
        if re.fullmatch(r"\d{2}-\d{2}", start) and re.fullmatch(r"\d{2}-\d{2}", end):
            in_window = in_window or _mm_dd_in_window(today_mm_dd, start, end)
    return not bool(seasonality.get("hard_exclude_outside_windows")) or in_window


def candidate_metadata(
    *,
    runtime_root: Path,
    quote_text_by_id: Mapping[str, str],
    catalogue: Mapping[str, Any],
    used_ids: set[str],
    enforce_unused: bool,
    selected_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Revalidate attribution, analysis, renderability, and treatment bounds."""

    research_root = runtime_root / RESEARCH_RELATIVE_PATH
    try:
        packets, unresolved = load_and_validate_corpus(
            research_root,
            require_source_role_audit=True,
        )
    except Exception as exc:
        raise PreparationError(f"historical-context corpus validation failed: {exc}") from exc
    runtime_ids, _aliases = runtime_manifest_ids(
        runtime_root,
        quote_text_by_id,
        packets,
    )
    analysis_document = load_quote_analysis(runtime_root)
    context_config = historical_context_config(runtime_root)
    today_mm_dd = datetime.now(ZoneInfo(experiment.LONDON_TIMEZONE)).strftime("%m-%d")
    candidates: list[dict[str, Any]] = []
    excluded: dict[str, list[str]] = {}
    catalogue_entries = catalogue["entries"]
    ids = sorted(selected_ids if selected_ids is not None else catalogue_entries)
    for quote_id in ids:
        reasons: list[str] = []
        exact_quote = quote_text_by_id.get(quote_id)
        entry = catalogue_entries.get(quote_id)
        if type(exact_quote) is not str or not isinstance(entry, Mapping):
            reasons.append("current_exact_quote_unavailable")
            excluded[quote_id] = reasons
            continue
        if experiment.sha256_text(exact_quote) != quote_id:
            reasons.append("current_exact_quote_sha256_changed")
        if enforce_unused and quote_id in used_ids:
            reasons.append("used_history")
        if quote_id not in runtime_ids:
            reasons.append("outside_runtime_eligible_partition")
        packet = packet_for_posted_quote(packets, unresolved, quote_id, exact_quote)
        if packet is None:
            reasons.append("canonical_research_packet_unavailable")
        else:
            if not packet_is_attributed_to_margaret_thatcher(packet):
                reasons.append("attribution_ineligible")
            if packet.get("verification_status") not in VERIFICATION_ALLOWED:
                reasons.append("verification_status_ineligible")
            if packet.get("research_confidence") not in CONFIDENCE_ALLOWED:
                reasons.append("research_confidence_ineligible")
        analysis_item = (analysis_document.get("items") or {}).get(quote_id)
        analysis = analysis_item.get("analysis") if isinstance(analysis_item, Mapping) else None
        topic = None
        if isinstance(analysis_item, Mapping):
            analysed_text = analysis_item.get("text")
            if analysed_text is not None and experiment.sha256_text(str(analysed_text)) != quote_id:
                reasons.append("quote_analysis_text_changed")
        if not isinstance(analysis, Mapping):
            reasons.append("quote_analysis_unavailable")
        else:
            topics = analysis.get("primary_topics")
            if (
                isinstance(topics, list)
                and topics
                and type(topics[0]) is str
                and experiment.TOPIC_RE.fullmatch(topics[0])
            ):
                topic = topics[0]
            else:
                reasons.append("existing_broad_topic_unavailable")
            if not currently_production_eligible(analysis, today_mm_dd=today_mm_dd):
                reasons.append("currently_hard_seasonally_excluded")
        formatted = None
        if packet is not None:
            try:
                formatted = format_context_reply_public(
                    packet,
                    maximum_length=int(context_config["maximum_length"]),
                    include_meaning=bool(context_config["include_meaning"]),
                    include_source=bool(context_config["include_source"]),
                    include_verification=bool(context_config["include_verification"]),
                )
            except Exception:
                formatted = None
        if (
            not isinstance(formatted, Mapping)
            or type(formatted.get("text")) is not str
            or not formatted.get("text")
            or formatted.get("rendering_mode") != "public"
            or type(formatted.get("verification_label")) is not str
            or not formatted.get("verification_label")
            or type(formatted.get("source_class")) is not str
            or not formatted.get("source_class")
        ):
            reasons.append("historical_context_not_publicly_renderable")
        try:
            experiment.validate_complete_public_text(
                exact_quote_text=exact_quote,
                catalogue_entry=entry,
                arm="treatment",
            )
        except experiment.ExperimentValidationError:
            reasons.append("approved_treatment_revalidation_failed")
        if reasons:
            excluded[quote_id] = sorted(set(reasons))
            continue
        assert topic is not None and formatted is not None
        candidates.append(
            experiment.candidate_from_catalogue(
                quote_id=quote_id,
                exact_quote_text=exact_quote,
                topic=topic,
                verification_label=str(formatted["verification_label"]),
                source_class=str(formatted["source_class"]),
                catalogue=catalogue,
            )
        )
    return candidates, excluded


def validate_catalogue_from_paths(
    repository_root: Path,
    runtime_root: Path,
    catalogue_path: Path,
) -> tuple[dict[str, Any], str, dict[str, str]]:
    """Validate the exact catalogue against the current canonical source."""
    quote_text_by_id, _lines, _source = canonical_quote_source(runtime_root)
    catalogue, catalogue_sha = experiment.load_approved_catalogue(
        catalogue_path,
        quote_text_by_id,
    )
    return catalogue, catalogue_sha, quote_text_by_id


def _safe_output_paths(paths: Sequence[Path]) -> None:
    resolved: set[Path] = set()
    for path in paths:
        absolute = Path(os.path.abspath(os.fspath(path)))
        if absolute in resolved:
            raise PreparationError("requested output paths must be distinct")
        resolved.add(absolute)
        try:
            os.lstat(absolute)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise PreparationError(f"cannot inspect requested output path {absolute}: {exc}") from exc
        else:
            raise PreparationError(f"refusing to overwrite requested output path: {absolute}")


def write_new_file(path: Path, content: bytes, *, mode: int = 0o600) -> None:
    """Durably create one requested output without overwriting a race winner."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    absolute.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{absolute.name}.", dir=absolute.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, absolute)
        except FileExistsError as exc:
            raise PreparationError(
                f"requested output appeared during preparation: {absolute}"
            ) from exc
        directory = os.open(absolute.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def pretty_json_bytes(value: Any) -> bytes:
    """Return human-readable deterministic UTF-8 JSON file bytes."""
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def fenced_exact_text(value: str) -> list[str]:
    """Render exact catalogue text without changing it."""

    marker = "```text"
    if "```" in value:
        return ["<pre>", value, "</pre>"]
    return [marker, value, "```"]


def render_preview_report(
    plan: Mapping[str, Any],
    quote_text_by_id: Mapping[str, str],
    *,
    catalogue_sha256: str,
    eligible_approved_count: int,
) -> str:
    """Render the complete human-review plan without reserving anything."""

    summary = experiment.plan_selection_summary(plan)
    lines = [
        "# Proposed substantive-question experiment plan",
        "",
        "> **NON-LIVE PREVIEW.** This file is not an active plan, reserves no quotation, and must not be copied into production as runtime state.",
        "",
        f"- Experiment: `{experiment.EXPERIMENT_ID}`",
        f"- Approved catalogue SHA-256: `{catalogue_sha256}`",
        f"- Plan SHA-256: `{plan['plan_sha256']}`",
        f"- Currently unused and eligible approved entries: **{eligible_approved_count}**",
        f"- Exact matched pairs: **{summary['pair_count']}**",
        f"- Arms: **{summary['arm_counts']['control']} control / {summary['arm_counts']['treatment']} treatment**",
        f"- Order: **{summary['publication_order_counts'].get('treatment_first', 0)} treatment-first / {summary['publication_order_counts'].get('control_first', 0)} control-first**",
        f"- Maximum within-pair control weighted-length difference: **{summary['maximum_control_weighted_length_difference']}**",
        f"- Median within-pair control weighted-length difference: **{summary['median_control_weighted_length_difference']}**",
        "",
        "## Selection balance",
        "",
        f"- By topic: `{json.dumps(summary['selected_pairs_by_topic'], sort_keys=True)}`",
        f"- By length band: `{json.dumps(summary['selected_pairs_by_length_band'], sort_keys=True)}`",
        "",
        "## All 30 pairs",
        "",
    ]
    for sequence, pair in enumerate(plan["pairs"], 1):
        matching = pair["matching"]
        lines.extend(
            [
                f"### {sequence:02d}. `{pair['pair_id']}`",
                "",
                f"- Topic: `{pair['topic']}`",
                f"- Quotation-length band: `{pair['quotation_length_band']}`",
                f"- Planned order: `{pair['planned_publication_order']}`",
                f"- Control weighted-length difference: `{matching['control_weighted_length_difference']}`",
                f"- Verification-label mismatch: `{str(matching['verification_label_mismatch']).lower()}`",
                f"- Source-class mismatch: `{str(matching['source_class_mismatch']).lower()}`",
                "",
            ]
        )
        for member in pair["members"]:
            lines.extend(
                [
                    f"#### Position {member['position']}: {member['arm']}",
                    "",
                    f"Quote ID: `{member['quote_id']}`",
                    "",
                    "Exact quotation:",
                    "",
                    *fenced_exact_text(quote_text_by_id[member["quote_id"]]),
                    "",
                    "Approved question (withheld for control):",
                    "",
                    *fenced_exact_text(member["approved_question_body"]),
                    "",
                    f"Verification: `{member['verification_label']}`; source class: `{member['source_class']}`; question source: `{member['question_source']}`.",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def render_shortfall_report(
    diagnostics: Mapping[str, Any],
    exclusions: Mapping[str, Sequence[str]],
) -> str:
    """Render an exact-group shortfall without weakening the match."""

    lines = [
        "# Substantive-question plan preparation shortfall",
        "",
        "> No plan was produced. Exact topic and quotation-length matching was not weakened.",
        "",
        f"- Eligible approved candidates: **{diagnostics.get('eligible_candidate_count', 0)}**",
        f"- Possible exact pairs: **{diagnostics.get('possible_exact_pair_count', 0)}**",
        f"- Required exact pairs: **{experiment.TARGET_COMPLETED_PAIRS}**",
        "",
        "## Exact groups",
        "",
        "| topic | band | candidates | pairs | unpaired quote |",
        "|---|---|---:|---:|---|",
    ]
    for row in diagnostics.get("groups", []):
        lines.append(
            f"| {row['topic']} | {row['quotation_length_band']} | {row['candidate_count']} | {row['exact_pair_count']} | {row.get('unpaired_quote_id') or ''} |"
        )
    reason_counts = Counter(reason for reasons in exclusions.values() for reason in reasons)
    lines.extend(["", "## Current exclusions", ""])
    for reason, count in sorted(reason_counts.items()):
        lines.append(f"- `{reason}`: {count}")
    return "\n".join(lines).rstrip() + "\n"


def prepare_plan(args: argparse.Namespace) -> dict[str, Any]:
    """Prepare one non-reserving plan and its requested offline reports."""
    repository_root = args.repository_root.resolve()
    runtime_root = args.runtime_root.resolve()
    catalogue_path = (
        args.catalogue_path.resolve()
        if args.catalogue_path is not None
        else repository_root / CATALOGUE_RELATIVE_PATH
    )
    outputs = [args.plan_path.resolve(), args.report_path.resolve()]
    if args.manifest_path is not None:
        outputs.append(args.manifest_path.resolve())
    _safe_output_paths(outputs)
    regular_receipt_must_be_absent(runtime_root)
    used_ids, initial_used_source, used_sha = capture_used_history(runtime_root)
    catalogue, catalogue_sha, quote_text_by_id = validate_catalogue_from_paths(
        repository_root,
        runtime_root,
        catalogue_path,
    )
    candidates, exclusions = candidate_metadata(
        runtime_root=runtime_root,
        quote_text_by_id=quote_text_by_id,
        catalogue=catalogue,
        used_ids=used_ids,
        enforce_unused=True,
    )
    try:
        plan, diagnostics = experiment.build_plan(
            catalogue=catalogue,
            catalogue_sha256=catalogue_sha,
            candidates=candidates,
            used_history_sha256=used_sha,
            plan_created_at=iso_utc(),
            plan_kind=args.plan_kind,
        )
    except experiment.PlanPreparationError as exc:
        report = render_shortfall_report(exc.diagnostics, exclusions)
        # The history and receipt are checked again before even the diagnostic
        # is published, so it describes one stable production snapshot.
        regular_receipt_must_be_absent(runtime_root)
        _ids_again, final_used_source, _sha_again = capture_used_history(runtime_root)
        if final_used_source != initial_used_source:
            raise PreparationError(
                "used-quotation history changed before shortfall publication"
            )
        write_new_file(args.report_path, report.encode("utf-8"))
        raise PreparationError(str(exc)) from exc

    report = render_preview_report(
        plan,
        quote_text_by_id,
        catalogue_sha256=catalogue_sha,
        eligible_approved_count=len(candidates),
    )
    plan_bytes = pretty_json_bytes(plan)
    report_bytes = report.encode("utf-8")
    summary = experiment.plan_selection_summary(plan)
    manifest = {
        "schema_version": 1,
        "experiment_id": experiment.EXPERIMENT_ID,
        "plan_kind": args.plan_kind,
        "non_live_preview": args.plan_kind == "preview",
        "generated_at": iso_utc(),
        "approved_catalogue_sha256": catalogue_sha,
        "approved_catalogue_entry_count": len(catalogue["entries"]),
        "used_history_sha256": used_sha,
        "used_history_entry_count": len(used_ids),
        "currently_unused_eligible_approved_entry_count": len(candidates),
        "excluded_approved_entry_count": len(exclusions),
        "plan_sha256": plan["plan_sha256"],
        "plan_file_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "report_file_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "plan_path": str(args.plan_path.resolve()),
        "report_path": str(args.report_path.resolve()),
        "reservation_created": False,
        "production_runtime_files_modified": False,
        "network_requests_made": 0,
        "selection_summary": summary,
        "matching_diagnostics": diagnostics,
    }

    # Re-capture at the last possible point. The tool never marks a quote used.
    regular_receipt_must_be_absent(runtime_root)
    _used_again, final_used_source, final_used_sha = capture_used_history(runtime_root)
    if final_used_source != initial_used_source or final_used_sha != used_sha:
        raise PreparationError("used-quotation history changed before plan publication")
    write_new_file(args.plan_path, plan_bytes, mode=0o600)
    write_new_file(args.report_path, report_bytes, mode=0o600)
    if args.manifest_path is not None:
        write_new_file(args.manifest_path, pretty_json_bytes(manifest), mode=0o600)
    return manifest


def load_and_validate_plan(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load a plan and revalidate every member against current local data."""
    repository_root = args.repository_root.resolve()
    runtime_root = args.runtime_root.resolve()
    catalogue_path = (
        args.catalogue_path.resolve()
        if args.catalogue_path is not None
        else repository_root / CATALOGUE_RELATIVE_PATH
    )
    catalogue, catalogue_sha, quote_text_by_id = validate_catalogue_from_paths(
        repository_root,
        runtime_root,
        catalogue_path,
    )
    plan_document, _plan_source = stable_json(
        args.plan_path.resolve(),
        label="experiment plan",
        maximum_bytes=2 * 1024 * 1024,
    )
    selected_ids = {
        str(member.get("quote_id"))
        for pair in (plan_document.get("pairs") if isinstance(plan_document, dict) else [])
        if isinstance(pair, dict)
        for member in (pair.get("members") or [])
        if isinstance(member, dict)
    }
    candidates, exclusions = candidate_metadata(
        runtime_root=runtime_root,
        quote_text_by_id=quote_text_by_id,
        catalogue=catalogue,
        used_ids=set(),
        enforce_unused=False,
        selected_ids=selected_ids,
    )
    metadata = {row["quote_id"]: row for row in candidates}
    if exclusions:
        raise PreparationError(
            "plan member current eligibility failed: "
            + json.dumps(exclusions, sort_keys=True)
        )
    plan = experiment.validate_plan_document(
        plan_document,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha,
        quote_text_by_id=quote_text_by_id,
        metadata_by_quote_id=metadata,
        require_plan_kind=args.require_plan_kind,
    )
    return plan, experiment.plan_selection_summary(plan)


def offline_status(args: argparse.Namespace) -> dict[str, Any]:
    """Return bounded plan/protected-state status without network access."""
    plan: dict[str, Any] | None = None
    plan_valid = False
    state_valid = False
    plan_error: str | None = None
    state_error: str | None = None
    try:
        plan, _summary = load_and_validate_plan(args)
        plan_valid = True
    except Exception as exc:
        plan_error = f"{type(exc).__name__}: {exc}"
    state_path = (
        args.state_path.resolve()
        if args.state_path is not None
        else args.runtime_root.resolve() / BOT_STATE_NAME
    )
    experiment_state: dict[str, Any] | None = None
    try:
        state_document, _source = stable_json(
            state_path,
            label="protected bot state",
            maximum_bytes=32 * 1024 * 1024,
        )
        if not isinstance(state_document, dict):
            raise PreparationError("protected bot state is not an object")
        raw_state = state_document.get("engagement_question_experiment")
        if raw_state is not None:
            experiment_state = experiment.validate_experiment_state(
                raw_state,
                plan=plan if plan_valid else None,
            )
        state_valid = True
    except FileNotFoundError:
        state_valid = True
    except Exception as exc:
        state_error = f"{type(exc).__name__}: {exc}"
    result = experiment.experiment_status_summary(
        experiment_state,
        plan,
        plan_valid=plan_valid,
        state_valid=state_valid,
    )
    result["plan_error"] = plan_error
    result["state_error"] = state_error
    result["plan_and_state_validate"] = bool(plan_valid and state_valid)
    return result


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit offline administration command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare-plan", action="store_true")
    modes.add_argument("--validate-plan", action="store_true")
    modes.add_argument("--status", action="store_true")
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--catalogue-path", type=Path)
    parser.add_argument("--plan-path", type=Path, required=True)
    parser.add_argument("--report-path", type=Path)
    parser.add_argument("--manifest-path", type=Path)
    parser.add_argument("--state-path", type=Path)
    parser.add_argument("--plan-kind", choices=sorted(experiment.PLAN_KINDS), default="preview")
    parser.add_argument("--require-plan-kind", choices=sorted(experiment.PLAN_KINDS))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one requested offline administration mode."""
    args = build_parser().parse_args(argv)
    if args.prepare_plan:
        if args.report_path is None:
            raise SystemExit("--prepare-plan requires --report-path")
        result = prepare_plan(args)
    elif args.validate_plan:
        plan, summary = load_and_validate_plan(args)
        result = {
            "status": "valid",
            "experiment_id": plan["experiment_id"],
            "plan_kind": plan["plan_kind"],
            "plan_sha256": plan["plan_sha256"],
            **summary,
            "network_requests_made": 0,
        }
    else:
        result = offline_status(args)
    sys.stdout.write(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PreparationError, experiment.ExperimentValidationError) as exc:
        print(f"prepare_engagement_question_experiment.py: error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
