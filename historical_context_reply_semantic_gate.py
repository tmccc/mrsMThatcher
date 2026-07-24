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
from dataclasses import dataclass
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
    "ffe8e31f7b5e7c34271c6baa9a53c73e19598e8abeca427dab1bea48d79c9fea"
)
EXPECTED_REVIEWED_COUNT = 86
EXPECTED_BLOCKED_COUNT = 15
EXPECTED_DISPOSITION_COUNTS = {
    "future_correction_needed": 6,
    "insufficient_to_assess": 9,
}
EXPECTED_PROJECTION_SHA256 = (
    "27a8fdcfb05cbda87d51bb5b035d7f960775552a5281a08c06a958c5ff15869f"
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


def load_historical_context_semantic_gate(
    *,
    root: Path = ROOT,
    eligible_quote_ids: Collection[str] | None = None,
    expected_ledger_sha256: str = EXPECTED_LEDGER_SHA256,
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
        return HistoricalContextSemanticGate(
            available=True,
            reason="",
            ledger_sha256=ledger_sha256,
            projection_sha256=projection_sha256,
            blocked_dispositions=MappingProxyType(dict(sorted(blocked.items()))),
        )
    except Exception as exc:
        return HistoricalContextSemanticGate.closed(
            f"{type(exc).__name__}: {exc}",
            ledger_sha256=ledger_sha256,
        )
