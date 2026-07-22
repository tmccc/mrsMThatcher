#!/usr/bin/env python3
"""Review the v7-to-v8 public historical-context field projection offline.

The review reconstructs the v7 role-based projection from the current packet
and source-role records, excluding the one source added during the v8 evidence
correction.  It renders every changed packet with the real public formatter
and writes only to the explicit output path.  It performs no network access
and never changes packet or evidence data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from historical_context_formatter import (
    DEFAULT_RESEARCH_DIR,
    _v2_british_date,
    atomic_write_json,
    format_context_reply_public,
    load_and_validate_corpus,
)
from historical_context_source_roles import AUDIT_FILENAME


REVIEW_SCHEMA_VERSION = 1
REVIEW_KIND = "historical_context_public_projection_review"
V7_POLICY = "historical-context-source-roles-v7-curated-source-adjudications"
V8_POLICY = "historical-context-source-roles-v8-claim-specific-public-context"

DOCUMENT_104653_QUOTE_ID = (
    "e1d78bc63369145f6cf7462d8ad5f15dceef929c0469aff64d3bde1e7a188f18"
)
B32_QUOTE_ID = (
    "b32d8cdf5977dee436857e8060d3a83ebfe54de9f6dabb20ffc65a0796338b5c"
)
CLEAN_EVENT_ONLY_QUOTE_ID = (
    "3cced21d7f9bc45fd5479288c7b413bad5e0e48fcf71f103251b6284c8528f12"
)
DOCUMENT_104653_SOURCE_ID = (
    "729ef0ab1006098f0e0b1dbfb6fefc99410908a4d980638d48c024ee268c55f1"
)
V8_ADDED_SOURCE_IDS = frozenset({DOCUMENT_104653_SOURCE_ID})

SAFE_EVENT_ONLY_FALLBACK = (
    "Context — The surviving record identifies an occasion, but does not "
    "establish a reliable date."
)
CLEAN_EVENT_ONLY_CONTEXT = (
    "Context — Publication of her memoir, The Downing Street Years."
)
DOCUMENT_104653_CONTEXT = (
    "Context — Speech to Conservative Women's Conference, 20 May 1981."
)

EXPECTED_MANUAL_HINT_IDS = frozenset({
    "19cb0b567f5dd9f7f3a15dd38c8c0455112573afe574fc3fbce0151783e5f296",
    "1a73e33d64ecdbdcce0ced1691e91457efd50720116168b28a2371d6b8a8eec6",
    "2b1356ba865985190519435bf06d5d3be6ae3bdd50deb80ec295263502c918b4",
    "89a020d133d390624dc61a59ab7ffbb59ba06792b45eac6b5337b564662146d0",
    "8d23f8984b463c3b5218c7b3c7e56b0a8dea3025d3874a9970349a69f4e1e46e",
    "8ee86fceccc2ab211e6b4f20764ba758d8b0278c1fcaa2f46c302f2d440c61b1",
    "e901673a2a91f9dadb8b896ef8232aa5d7e6f20c0602f12721d74810db8233f0",
    "eece24fa8b46957889f60d3c70e278aa5d9f646114030806262b8c530e906f6a",
    "fe8be1f80a36cb747a12ad4925152b57ae2ead8549b083ad14d31179bf418361",
})

_UNKNOWN = frozenset({
    "", "n/a", "n.a.", "none", "not available", "unknown", "unavailable",
})
_DATE_LIKE = re.compile(
    r"(?:\b(?:18|19|20)\d{2}\b|"
    r"\b\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|"
    r"May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\b)",
    re.I,
)
_BARE_DATE = re.compile(
    r"(?:\d{1,2}\s+)?(?:January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\s+\d{4}|(?:18|19|20)\d{2}",
    re.I,
)
_EVENT_LIKE_SOURCE_TITLE = re.compile(
    r"\b(?:speech|interview|article|address|press release|campaign|lecture|"
    r"statement|broadcast|remarks|memoir|book|reported|reports|newspaper|"
    r"magazine|general election|hansard)\b",
    re.I,
)
_DAY_PRECISION_DATE = re.compile(
    r"\d{1,2} (?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December) \d{4}"
)
_MONTH_PRECISION_DATE = re.compile(
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December) \d{4}"
)
_YEAR_PRECISION_DATE = re.compile(r"(?:18|19|20)\d{2}")


def _clean(value: Any) -> str:
    """Return one whitespace-normalised string."""
    return " ".join(str(value or "").split()).strip()


def _known(value: Any) -> bool:
    """Return whether a v7 packet scalar was considered known."""
    return _clean(value).casefold() not in _UNKNOWN


def _file_sha256(path: Path) -> str:
    """Hash one input without modifying it."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _v7_public_context_supported_fields(packet: dict[str, Any]) -> list[str]:
    """Reconstruct the v7 role-based field projection from current records."""
    audit = packet["_source_role_audit"]
    roles: set[str] = set()
    for source in audit.get("renderable_sources", []):
        if source.get("source_id") in V8_ADDED_SOURCE_IDS:
            continue
        roles.update(source.get("assigned_roles", []))
    return [
        field
        for field, role in (
            ("source_event", "source_event_support"),
            ("date", "source_event_support"),
            ("historical_context", "historical_context_support"),
        )
        if role in roles and (field != "date" or _known(packet.get("date")))
    ]


def _context_line(public_reply_text: str) -> str:
    """Extract the first public Context section without altering it."""
    return public_reply_text.split("\n\n", 1)[0]


def _public_source_projection(rendered: dict[str, Any]) -> list[dict[str, Any]]:
    """Return concise public source identity fields used by this review."""
    return [
        {
            "claims_supported": list(source.get("claims_supported", [])),
            "title": _clean(source.get("title")),
            "url": _clean(source.get("url")),
        }
        for source in rendered.get("sources", [])
    ]


def _public_date_precision(
    packet: dict[str, Any], v8_fields: list[str],
) -> str:
    """Classify the displayed precision of a date-only projection."""
    if v8_fields != ["date"]:
        return "not_applicable"
    public_date = _v2_british_date(packet.get("date"))
    for precision, pattern in (
        ("day", _DAY_PRECISION_DATE),
        ("month", _MONTH_PRECISION_DATE),
        ("year", _YEAR_PRECISION_DATE),
    ):
        if pattern.fullmatch(public_date):
            return precision
    return "other"


def _base_presentation_flags(
    packet: dict[str, Any],
    v8_fields: list[str],
    context_line: str,
    public_sources: list[dict[str, Any]],
) -> list[str]:
    """Classify deterministic presentation properties of one Context line."""
    flags: list[str] = []
    prefix = "Context — "
    body = context_line[len(prefix):] if context_line.startswith(prefix) else ""
    if not body:
        flags.append("empty_context")
    if body and _BARE_DATE.fullmatch(body.rstrip(".!?")):
        flags.append("bare_date_context")
    if (
        not context_line.startswith(prefix)
        or "\n" in context_line
        or not body
        or not context_line.endswith((".", "?", "!"))
    ):
        flags.append("malformed_context")
    if "date" not in v8_fields and _DATE_LIKE.search(body):
        flags.append("unadmitted_date_in_context")
    if "/" in body:
        flags.append("diagnostic_slash_in_context")

    if v8_fields == ["date"]:
        expected = (
            "Context — The surviving record dates this wording to "
            f"{_v2_british_date(packet.get('date'))}, but does not establish "
            "its occasion."
        )
        if context_line == expected:
            flags.append("safe_date_only_context")
        matching_titles = sorted({
            source["title"] for source in public_sources
            if _EVENT_LIKE_SOURCE_TITLE.search(source["title"])
        })
        if matching_titles:
            flags.append("source_title_event_tension_manual_hint")
    elif v8_fields == ["source_event"]:
        if context_line == SAFE_EVENT_ONLY_FALLBACK:
            flags.append("safe_event_only_fallback")
        elif not {
            "empty_context", "malformed_context", "unadmitted_date_in_context",
            "diagnostic_slash_in_context",
        } & set(flags):
            flags.append("safe_event_only_context")
    elif v8_fields == ["source_event", "date"]:
        flags.append("event_and_date_context")
    return sorted(flags)


def _duplicate_groups(
    records: list[dict[str, Any]], field: str,
) -> list[dict[str, Any]]:
    """Return deterministic duplicate-value groups for one record field."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for record in records:
        grouped[record[field]].append(record["quote_id"])
    return [
        {field: value, "quote_ids": sorted(quote_ids)}
        for value, quote_ids in sorted(grouped.items())
        if len(quote_ids) > 1
    ]


def _assert_review(review: dict[str, Any]) -> None:
    """Reject any result that no longer describes the reviewed 68 changes."""
    failed = sorted(
        name for name, passed in review["invariants"].items() if not passed
    )
    if failed:
        raise RuntimeError(
            "public projection review invariants failed: " + ", ".join(failed)
        )


def build_review(
    research_dir: Path = DEFAULT_RESEARCH_DIR,
) -> dict[str, Any]:
    """Build the deterministic, offline public projection review."""
    research_dir = research_dir.resolve()
    packets, _ = load_and_validate_corpus(
        research_dir,
        require_source_role_audit=True,
    )
    records: list[dict[str, Any]] = []
    for quote_id in sorted(packets):
        packet = packets[quote_id]
        audit = packet["_source_role_audit"]
        if audit.get("policy_version") != V8_POLICY:
            raise RuntimeError(f"unexpected source-role policy for {quote_id}")
        v7_fields = _v7_public_context_supported_fields(packet)
        v8_fields = list(audit.get("public_context_supported_fields", []))
        if v7_fields == v8_fields:
            continue
        rendered = format_context_reply_public(packet)
        if rendered is None:
            raise RuntimeError(f"public formatter failed for {quote_id}")
        public_text = rendered["text"]
        context = _context_line(public_text)
        public_sources = _public_source_projection(rendered)
        records.append({
            "change_kind": (
                "downgraded" if len(v8_fields) < len(v7_fields) else "upgraded"
            ),
            "context_line": context,
            "date": _clean(packet.get("date")),
            "presentation_flags": _base_presentation_flags(
                packet, v8_fields, context, public_sources,
            ),
            "public_date_precision": _public_date_precision(packet, v8_fields),
            "public_reply_text": public_text,
            "public_sources": public_sources,
            "quote_id": quote_id,
            "quote_text": packet["quote_text"],
            "source_event": _clean(packet.get("source_event")),
            "v7_public_context_supported_fields": v7_fields,
            "v8_public_context_supported_fields": v8_fields,
        })

    downgraded = [row for row in records if row["change_kind"] == "downgraded"]
    upgraded = [row for row in records if row["change_kind"] == "upgraded"]
    duplicate_contexts = _duplicate_groups(downgraded, "context_line")
    duplicate_full_replies = _duplicate_groups(downgraded, "public_reply_text")
    duplicate_context_ids = {
        quote_id
        for group in duplicate_contexts
        for quote_id in group["quote_ids"]
    }
    duplicate_reply_ids = {
        quote_id
        for group in duplicate_full_replies
        for quote_id in group["quote_ids"]
    }
    for record in records:
        flags = set(record["presentation_flags"])
        if record["quote_id"] in duplicate_context_ids:
            flags.add("duplicate_context_line")
        if record["quote_id"] in duplicate_reply_ids:
            flags.add("duplicate_full_reply")
        record["presentation_flags"] = sorted(flags)

    manual_hints = [
        {
            "kind": "source_title_event_tension",
            "promotes_public_fields": False,
            "public_source_titles": sorted({
                source["title"] for source in record["public_sources"]
                if _EVENT_LIKE_SOURCE_TITLE.search(source["title"])
            }),
            "quote_id": record["quote_id"],
            "recommended_action": "manual_evidence_review_only",
        }
        for record in downgraded
        if "source_title_event_tension_manual_hint"
        in record["presentation_flags"]
    ]
    flag_counts = Counter(
        flag for record in records for flag in record["presentation_flags"]
    )
    pattern_counts = Counter(
        (
            tuple(record["v7_public_context_supported_fields"]),
            tuple(record["v8_public_context_supported_fields"]),
        )
        for record in records
    )
    date_precision_counts = Counter(
        record["public_date_precision"] for record in downgraded
        if record["v8_public_context_supported_fields"] == ["date"]
    )
    by_id = {record["quote_id"]: record for record in records}
    blocker_flags = {
        "empty_context", "bare_date_context", "malformed_context",
        "unadmitted_date_in_context", "diagnostic_slash_in_context",
        "duplicate_full_reply",
    }
    invariants = {
        "change_count_is_68": len(records) == 68,
        "downgraded_count_is_67": len(downgraded) == 67,
        "upgraded_count_is_1": len(upgraded) == 1,
        "date_only_downgrade_count_is_65": pattern_counts[
            (("source_event", "date"), ("date",))
        ] == 65,
        "event_only_downgrade_count_is_2": pattern_counts[
            (("source_event", "date"), ("source_event",))
        ] == 2,
        "event_and_date_upgrade_count_is_1": pattern_counts[
            ((), ("source_event", "date"))
        ] == 1,
        "all_65_date_only_contexts_are_safe": flag_counts[
            "safe_date_only_context"
        ] == 65,
        "date_only_precision_is_60_day_3_month_2_year": (
            date_precision_counts
            == {"day": 60, "month": 3, "year": 2}
        ),
        "b32_uses_safe_fallback": (
            by_id.get(B32_QUOTE_ID, {}).get("context_line")
            == SAFE_EVENT_ONLY_FALLBACK
            and all(
                marker not in by_id[B32_QUOTE_ID]["context_line"]
                for marker in ("1979", "1984", "/")
            )
        ),
        "clean_event_only_context_is_preserved": (
            by_id.get(CLEAN_EVENT_ONLY_QUOTE_ID, {}).get("context_line")
            == CLEAN_EVENT_ONLY_CONTEXT
        ),
        "document_104653_upgrade_is_correct": (
            by_id.get(DOCUMENT_104653_QUOTE_ID, {}).get("change_kind")
            == "upgraded"
            and by_id[DOCUMENT_104653_QUOTE_ID]["context_line"]
            == DOCUMENT_104653_CONTEXT
            and by_id[DOCUMENT_104653_QUOTE_ID][
                "v7_public_context_supported_fields"
            ] == []
            and by_id[DOCUMENT_104653_QUOTE_ID][
                "v8_public_context_supported_fields"
            ] == ["source_event", "date"]
        ),
        "no_blocking_presentation_flags": not any(
            blocker_flags & set(record["presentation_flags"])
            for record in records
        ),
        "no_duplicate_full_reply": not duplicate_full_replies,
        "manual_hint_count_is_9": len(manual_hints) == 9,
        "manual_hint_ids_match_reviewed_set": (
            {row["quote_id"] for row in manual_hints}
            == EXPECTED_MANUAL_HINT_IDS
        ),
        "manual_hints_do_not_promote_fields": all(
            row["promotes_public_fields"] is False for row in manual_hints
        ),
    }
    root = Path(__file__).resolve().parent
    input_hashes = {
        AUDIT_FILENAME: _file_sha256(research_dir / AUDIT_FILENAME),
        "corpus_manifest.json": _file_sha256(
            research_dir / "corpus_manifest.json"
        ),
        "historical_context_formatter.py": _file_sha256(
            root / "historical_context_formatter.py"
        ),
        "historical_context_public_projection_review.py": _file_sha256(
            Path(__file__).resolve()
        ),
        "research_packets.json": _file_sha256(
            research_dir / "research_packets.json"
        ),
    }
    counts = {
        "change_count": len(records),
        "date_only_day_precision_count": date_precision_counts["day"],
        "date_only_month_precision_count": date_precision_counts["month"],
        "date_only_year_precision_count": date_precision_counts["year"],
        "downgraded_count": len(downgraded),
        "duplicate_context_group_count": len(duplicate_contexts),
        "duplicate_context_record_count": len(duplicate_context_ids),
        "duplicate_full_reply_group_count": len(duplicate_full_replies),
        "event_only_downgrade_count": pattern_counts[
            (("source_event", "date"), ("source_event",))
        ],
        "manual_hint_count": len(manual_hints),
        "safe_date_only_count": flag_counts["safe_date_only_context"],
        "safe_event_only_context_count": flag_counts[
            "safe_event_only_context"
        ],
        "safe_event_only_fallback_count": flag_counts[
            "safe_event_only_fallback"
        ],
        "upgraded_count": len(upgraded),
    }
    review = {
        "audit_kind": REVIEW_KIND,
        "counts": counts,
        "downgraded_records": downgraded,
        "duplicate_context_groups": duplicate_contexts,
        "duplicate_full_reply_groups": duplicate_full_replies,
        "input_hashes": input_hashes,
        "interpretation": {
            "duplicate_context_lines": (
                "Shared dates legitimately reuse the deterministic date-only "
                "sentence; only duplicate full replies are blocking."
            ),
            "manual_hints": (
                "Source-title/event tensions are review hints only and never "
                "promote a public field without claim-specific evidence."
            ),
            "v7_reconstruction": (
                "The role-based v7 projection is reconstructed from current "
                "renderable records after excluding the explicitly identified "
                "document 104653 source added with v8."
            ),
        },
        "invariants": invariants,
        "manual_hints": manual_hints,
        "policy_comparison": {"from": V7_POLICY, "to": V8_POLICY},
        "review_ready": all(invariants.values()),
        "schema_version": REVIEW_SCHEMA_VERSION,
        "upgraded_records": upgraded,
    }
    _assert_review(review)
    return review


def _validate_output_path(
    research_dir: Path,
    output: Path,
    *,
    overwrite: bool = False,
) -> Path:
    """Allow replacement only of an existing artifact of this exact kind."""
    if output.is_symlink():
        raise ValueError("--output must not be a symbolic link")
    research = research_dir.resolve()
    resolved = output.resolve(strict=False)
    if resolved == research or research in resolved.parents:
        raise ValueError("--output must be outside the immutable research directory")
    root = Path(__file__).resolve().parent
    protected = {
        (root / name).resolve(strict=False)
        for name in (
            "historical_context_formatter.py",
            "historical_context_public_projection_review.py",
            "historical_context_source_roles.py",
            "mrsMThatcher.txt",
            "quote_analysis.json",
        )
    }
    if resolved in protected:
        raise ValueError("--output resolves to a protected project input")
    if resolved.exists():
        if not overwrite:
            raise FileExistsError(
                "--output already exists; pass --overwrite only for a prior "
                "projection-review artifact"
            )
        try:
            existing = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "--overwrite target is not a prior projection-review artifact"
            ) from exc
        if (
            not isinstance(existing, dict)
            or existing.get("audit_kind") != REVIEW_KIND
            or existing.get("schema_version") != REVIEW_SCHEMA_VERSION
        ):
            raise ValueError(
                "--overwrite target is not a prior projection-review artifact"
            )
    return resolved


def main(argv: list[str] | None = None) -> int:
    """Write one explicit deterministic review artifact."""
    parser = argparse.ArgumentParser(
        description="Review v7-to-v8 public historical-context projections",
    )
    parser.add_argument(
        "--research-dir", type=Path, default=DEFAULT_RESEARCH_DIR,
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace only an existing artifact of this exact review kind",
    )
    args = parser.parse_args(argv)
    output = _validate_output_path(
        args.research_dir,
        args.output,
        overwrite=args.overwrite,
    )
    review = build_review(args.research_dir)
    atomic_write_json(output, review)
    print(json.dumps({
        "counts": review["counts"],
        "output": str(output),
        "review_ready": review["review_ready"],
    }, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
