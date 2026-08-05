"""Load the reviewed fail-closed gate for public historical-context replies.

The gate is deliberately separate from quotation eligibility.  It validates
the immutable published-reply semantic-review ledger, then blocks only the
historical-context reply lane for findings that remain open.  A missing,
changed or internally inconsistent ledger closes that optional lane without
affecting regular quote/image posting.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Collection, Mapping

from historical_context_published_reply_semantic_review import (
    MTF_REVIEW_PATH,
    SOURCE_ROLE_AUDIT_PATH,
    TRUTH_AUDIT_PATH,
    validate_review,
)


ROOT = Path(__file__).resolve().parent
SEMANTIC_REVIEW_PATH = (
    ROOT / "historical_context_published_reply_semantic_review.json"
)
POLICY_VERSION = "historical-context-semantic-gate-v1-open-review-whole-reply"
EXPECTED_LEDGER_SHA256 = (
    "f81832f3c0aa4e121e5ea521f0442fa90c024d1b3afd24fda4e2b49fe3cb0648"
)
EXPECTED_REVIEWED_COUNT = 115
EXPECTED_BLOCKED_COUNT = 13
EXPECTED_DISPOSITION_COUNTS = {
    "future_correction_needed": 6,
    "insufficient_to_assess": 7,
}
EXPECTED_PROJECTION_SHA256 = (
    "933067c8ee4a28759cca2e8c264cb49a3282bf6dd6b6ba1ea472629dfa19e857"
)
_HEX64 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class HistoricalContextSemanticGate:
    """Immutable result of validating the reviewed semantic gate."""

    available: bool
    reason: str
    ledger_sha256: str
    projection_sha256: str
    blocked_dispositions: Mapping[str, str]
    reviewed_dispositions: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    @classmethod
    def closed(cls, reason: str, *, ledger_sha256: str = "") -> "HistoricalContextSemanticGate":
        """Return an unavailable gate that blocks the optional lane globally."""
        return cls(
            available=False,
            reason=str(reason or "semantic review gate unavailable"),
            ledger_sha256=ledger_sha256,
            projection_sha256="",
            blocked_dispositions=MappingProxyType({}),
        )

    def disposition(self, quote_id: str) -> str | None:
        """Return the reviewed open disposition for one canonical quote ID."""
        return self.blocked_dispositions.get(str(quote_id or ""))

    def reviewed_disposition(self, quote_id: str) -> str | None:
        """Return the ledger disposition when this quote received a review."""
        return self.reviewed_dispositions.get(str(quote_id or ""))

    def blocks(self, quote_id: str) -> bool:
        """Return whether one canonical quote is blocked from a public reply."""
        return not self.available or self.disposition(quote_id) is not None


def _sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 of bytes."""
    return hashlib.sha256(value).hexdigest()


def _projection_sha256(blocked: Mapping[str, str]) -> str:
    """Hash the exact sorted quote/disposition gate projection."""
    encoded = "".join(
        f"{quote_id}\t{blocked[quote_id]}\n" for quote_id in sorted(blocked)
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _declared_packet_corrections_sha256(truth_path: Path) -> str | None:
    """Return the correction hash declared by the hash-bound truth audit."""
    from historical_context_packet_corrections import (
        PACKET_CORRECTIONS_FILENAME,
    )

    truth = json.loads(truth_path.read_bytes())
    input_hashes = truth.get("input_hashes") if isinstance(truth, dict) else None
    if not isinstance(input_hashes, dict):
        raise RuntimeError("historical-context truth audit input hashes are invalid")
    expected = input_hashes.get(PACKET_CORRECTIONS_FILENAME)
    if expected is None:
        return None
    expected = str(expected)
    if not _HEX64.fullmatch(expected):
        raise RuntimeError(
            "historical-context truth audit correction SHA-256 is invalid"
        )
    return expected


def _validate_current_allowed_renderings(
    review: dict,
    packets: Mapping[str, dict],
    formatter_options: Mapping[str, object] | None = None,
) -> None:
    """Bind every reviewed allowed reply to its current public rendering."""
    from historical_context_formatter import format_context_reply_public

    options = dict(formatter_options or {})
    allowed_options = {
        "maximum_length",
        "include_meaning",
        "include_source",
        "include_verification",
    }
    if not set(options).issubset(allowed_options):
        raise RuntimeError(
            "historical-context semantic gate formatter options are invalid"
        )
    if (
        "maximum_length" in options
        and type(options["maximum_length"]) is not int
    ) or any(
        key in options and type(options[key]) is not bool
        for key in (
            "include_meaning",
            "include_source",
            "include_verification",
        )
    ):
        raise RuntimeError(
            "historical-context semantic gate formatter option types are invalid"
        )

    for record in review.get("records", []):
        if (
            not isinstance(record, dict)
            or record.get("follow_up_status") == "remains_open"
        ):
            continue
        quote_id = str(record.get("quote_id") or "")
        packet = packets.get(quote_id)
        if packet is None:
            raise RuntimeError(
                f"semantic review current packet is missing: {quote_id}"
            )
        rendered = format_context_reply_public(packet, **options)
        current_text = (
            ""
            if rendered is None
            else str(rendered.get("text") or "")
        )
        if _sha256_bytes(current_text.encode("utf-8")) != record.get(
            "current_reply_sha256"
        ):
            raise RuntimeError(
                f"semantic review current rendering differs: {quote_id}"
            )


def load_historical_context_semantic_gate(
    *,
    root: Path = ROOT,
    eligible_quote_ids: Collection[str] | None = None,
    expected_ledger_sha256: str = EXPECTED_LEDGER_SHA256,
    formatter_options: Mapping[str, object] | None = None,
) -> HistoricalContextSemanticGate:
    """Validate and return the exact reviewed open-case projection.

    Validation failures are represented by an unavailable snapshot rather
    than raised.  The caller must treat an unavailable snapshot as a reason to
    skip every historical-context reply while allowing the independent main
    posting lane to continue.
    """
    root = root.resolve()
    ledger_path = root / SEMANTIC_REVIEW_PATH.name
    ledger_sha256 = ""
    try:
        payload = ledger_path.read_bytes()
        ledger_sha256 = _sha256_bytes(payload)
        if ledger_sha256 != expected_ledger_sha256:
            raise RuntimeError("semantic review ledger SHA-256 differs")
        review = json.loads(payload)
        if not isinstance(review, dict):
            raise RuntimeError("semantic review ledger is not an object")

        truth_path = root / TRUTH_AUDIT_PATH.name
        source_role_path = (
            root
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / SOURCE_ROLE_AUDIT_PATH.name
        )
        mtf_path = root / MTF_REVIEW_PATH.name
        counts = validate_review(
            review,
            truth_audit_path=truth_path,
            source_role_audit_path=source_role_path,
            mtf_review_path=mtf_path,
            reference_root=root,
        )
        records = review.get("records")
        remaining = review.get("remaining_items")
        if (
            not isinstance(records, list)
            or len(records) != EXPECTED_REVIEWED_COUNT
            or not isinstance(remaining, list)
            or len(remaining) != EXPECTED_BLOCKED_COUNT
            or counts.get("reviewed") != EXPECTED_REVIEWED_COUNT
            or counts.get("remaining") != EXPECTED_BLOCKED_COUNT
        ):
            raise RuntimeError("semantic review gate counts differ")

        open_records = [
            record
            for record in records
            if isinstance(record, dict)
            and record.get("follow_up_status") == "remains_open"
        ]
        blocked: dict[str, str] = {}
        reviewed = {
            str(record.get("quote_id") or ""): str(record.get("disposition") or "")
            for record in records
            if isinstance(record, dict)
        }
        for record in open_records:
            quote_id = str(record.get("quote_id") or "")
            disposition = str(record.get("disposition") or "")
            if (
                not _HEX64.fullmatch(quote_id)
                or record.get("quote_text_sha256") != quote_id
                or disposition not in EXPECTED_DISPOSITION_COUNTS
                or quote_id in blocked
            ):
                raise RuntimeError("semantic review open record is invalid")
            blocked[quote_id] = disposition

        remaining_projection = {
            str(item.get("quote_id") or ""): str(item.get("disposition") or "")
            for item in remaining
            if isinstance(item, dict)
        }
        if remaining_projection != blocked or len(blocked) != EXPECTED_BLOCKED_COUNT:
            raise RuntimeError("semantic review remaining projection differs")
        disposition_counts = {
            disposition: sum(value == disposition for value in blocked.values())
            for disposition in EXPECTED_DISPOSITION_COUNTS
        }
        if disposition_counts != EXPECTED_DISPOSITION_COUNTS:
            raise RuntimeError("semantic review gate dispositions differ")
        if eligible_quote_ids is not None and not set(blocked).issubset(
            {str(value) for value in eligible_quote_ids}
        ):
            raise RuntimeError("semantic review gate includes an ineligible quote")

        projection_sha256 = _projection_sha256(blocked)
        if projection_sha256 != EXPECTED_PROJECTION_SHA256:
            raise RuntimeError("semantic review gate projection SHA-256 differs")

        # The ledger is derived from a stored truth audit.  Revalidate the
        # current canonical corpus and the exact correction bytes declared by
        # that hash-bound audit, then reproduce every reviewed reply that the
        # gate would allow.  Open records remain individually blocked and need
        # not make the whole optional lane unavailable when their render drifts.
        from historical_context_formatter import load_and_validate_corpus

        research_dir = source_role_path.parent
        expected_corrections_sha256 = _declared_packet_corrections_sha256(
            truth_path
        )
        current_packets, _current_unresolved = load_and_validate_corpus(
            research_dir,
            require_source_role_audit=True,
            expected_packet_corrections_sha256=expected_corrections_sha256,
            require_packet_corrections_hash_binding=True,
        )
        _validate_current_allowed_renderings(
            review,
            current_packets,
            formatter_options,
        )

        return HistoricalContextSemanticGate(
            available=True,
            reason="",
            ledger_sha256=ledger_sha256,
            projection_sha256=projection_sha256,
            blocked_dispositions=MappingProxyType(dict(sorted(blocked.items()))),
            reviewed_dispositions=MappingProxyType(dict(sorted(reviewed.items()))),
        )
    except Exception as exc:
        return HistoricalContextSemanticGate.closed(
            f"{type(exc).__name__}: {exc}",
            ledger_sha256=ledger_sha256,
        )
