#!/usr/bin/env python3
"""Provider-independent logic for the substantive-question engagement trial."""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo


EXPERIMENT_ID = "substantive-question-v1"
APPROVED_CATALOGUE_SCHEMA_VERSION = 1
APPROVED_CATALOGUE_ENTRY_COUNT = 93
APPROVED_CATALOGUE_SHA256 = (
    "cdce2b7f7a7ceb140e907716f1ef7392d77aab99061997bb492cddf98952f6ba"
)
PLAN_SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
ATTEMPT_BINDING_SCHEMA_VERSION = 1
NOTIFICATION_SCHEMA_VERSION = 1
TARGET_COMPLETED_PAIRS = 30
TARGET_TREATMENT_COUNT = 30
QUESTION_PREFIX = "Question — "
TREATMENT_SEPARATOR = "\n\n"
TREATMENT_SUFFIX = TREATMENT_SEPARATOR + QUESTION_PREFIX
MAX_ROOT_WEIGHTED_LENGTH = 280
MAX_CONFIRMED_PUBLICATIONS = TARGET_COMPLETED_PAIRS * 2
MAX_QUOTE_EXCERPT_CHARACTERS = 160
PUBLICATION_GAP_LIMIT_SECONDS = 4 * 60 * 60
LONDON_TIMEZONE = "Europe/London"
QUESTION_SOURCES = {
    "generated",
    "phase2_carry_forward",
    "operator_replacement",
}
PLAN_KINDS = {"preview", "live"}
EXPERIMENT_STATUSES = {
    "not_started",
    "active",
    "paused",
    "completed",
    "invalid",
}
PUBLICATION_ORDERS = {"treatment_first", "control_first"}
ARMS = {"control", "treatment"}
HEX64_RE = re.compile(r"[0-9a-f]{64}")
PAIR_ID_RE = re.compile(r"pair-[0-9a-f]{24}")
POST_ID_RE = re.compile(r"\d{1,30}")
TOPIC_RE = re.compile(r"[a-z0-9][a-z0-9_]{0,63}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
URL_RE = re.compile(r"https?://\S+")

CATALOGUE_FIELDS = {
    "schema_version",
    "experiment_id",
    "source_snapshot_sha256",
    "entries",
}
CATALOGUE_ENTRY_FIELDS = {
    "quote_text_sha256",
    "question_body",
    "question_sha256",
    "complete_treatment_sha256",
    "complete_treatment_weighted_length",
    "question_source",
}
PLAN_FIELDS = {
    "schema_version",
    "experiment_id",
    "plan_kind",
    "approved_catalogue_sha256",
    "approved_catalogue_source_snapshot_sha256",
    "plan_created_at",
    "used_history_sha256_at_creation",
    "target_completed_pairs",
    "pair_count",
    "pairs",
    "plan_sha256",
}
PAIR_FIELDS = {
    "pair_id",
    "topic",
    "quotation_length_band",
    "planned_publication_order",
    "matching",
    "members",
}
PAIR_MATCHING_FIELDS = {
    "verification_label_mismatch",
    "source_class_mismatch",
    "control_weighted_length_difference",
}
MEMBER_FIELDS = {
    "quote_id",
    "arm",
    "position",
    "exact_control_text_sha256",
    "approved_question_body",
    "approved_question_sha256",
    "complete_treatment_sha256",
    "complete_treatment_weighted_length",
    "control_weighted_length",
    "verification_label",
    "source_class",
    "question_source",
}
CANDIDATE_FIELDS = {
    "quote_id",
    "exact_quote_text",
    "topic",
    "verification_label",
    "source_class",
}
STATE_FIELDS = {
    "schema_version",
    "experiment_id",
    "active_plan_sha256",
    "status",
    "current_pair_index",
    "active_pair_id",
    "next_pair_member_position",
    "completed_pair_count",
    "confirmed_publications",
    "last_experimental_publication_local_date",
    "treatment_publication_count",
    "latest_treatment_notification_identity",
    "current_deferral_reason",
}
PUBLICATION_FIELDS = {
    "post_id",
    "pair_id",
    "quote_id",
    "arm",
    "member_position",
    "publication_order",
    "publication_sequence",
    "question_present",
    "approved_question_sha256",
    "public_text_sha256",
    "published_epoch",
    "local_date",
    "pair_member_gap_seconds",
}
DEFERRAL_FIELDS = {"code", "pair_id", "member_position", "recorded_epoch"}
NOTIFICATION_IDENTITY_FIELDS = {
    "post_id",
    "document_sha256",
    "delivered",
    "document",
}
NOTIFICATION_FIELDS = {
    "schema_version",
    "experiment_id",
    "plan_sha256",
    "post_id",
    "pair_id",
    "treatment_number",
    "target_treatment_count",
    "published_epoch",
    "quote_excerpt",
    "question",
    "post_url",
}
ATTEMPT_BINDING_FIELDS = {
    "schema_version",
    "experiment_id",
    "plan_sha256",
    "pair_id",
    "pair_index",
    "member_position",
    "arm",
    "publication_order",
    "publication_sequence",
    "approved_question_sha256",
    "question_present",
    "canonical_quote_sha256",
    "public_text_sha256",
    "expected_transition",
}
EXPECTED_TRANSITION_FIELDS = {
    "confirmed_publication_count_before",
    "completed_pair_count_before",
    "treatment_publication_count_before",
    "current_pair_index_after",
    "active_pair_id_after",
    "next_pair_member_position_after",
    "completed_pair_count_after",
    "status_after",
}


class ExperimentValidationError(ValueError):
    """An experiment catalogue, plan, state, or transition is invalid."""


class PlanPreparationError(ExperimentValidationError):
    """Exact matching cannot produce the frozen target plan."""

    def __init__(self, message: str, diagnostics: Mapping[str, Any]):
        """Retain the exact-group diagnostic payload for an offline report."""
        super().__init__(message)
        self.diagnostics = dict(diagnostics)


def canonical_json_bytes(value: Any) -> bytes:
    """Return canonical UTF-8 JSON bytes for hashes and durable identities."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ExperimentValidationError("value is not canonical JSON") from exc


def canonical_sha256(value: Any) -> str:
    """Return the SHA-256 of canonical JSON bytes."""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def sha256_text(value: str) -> str:
    """Return the raw UTF-8 SHA-256 of exact text."""

    if type(value) is not str:
        raise TypeError("text must be a string")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """Return a file's SHA-256 without changing it."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strict_json_bytes(document: bytes, *, label: str) -> Any:
    """Decode strict JSON, rejecting duplicate fields and non-finite values."""

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ExperimentValidationError(
                    f"{label} contains duplicate object field {key!r}"
                )
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ExperimentValidationError(
            f"{label} contains non-finite JSON value {value}"
        )

    try:
        text = document.decode("utf-8", errors="strict")
        return json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except ExperimentValidationError:
        raise
    except Exception as exc:
        raise ExperimentValidationError(f"{label} is not strict UTF-8 JSON") from exc


def load_strict_json(path: Path, *, label: str) -> tuple[Any, bytes]:
    """Load a strict JSON document and return its exact source bytes."""

    source = path.read_bytes()
    return _strict_json_bytes(source, label=label), source


def x_weighted_length(text: str) -> int:
    """Apply the production weighted-length rule used by the approved catalogue."""

    if type(text) is not str:
        raise TypeError("weighted text must be a string")
    urls = URL_RE.findall(text)
    return len(text) - sum(len(url) for url in urls) + 23 * len(urls)


def quotation_length_band(text: str) -> str:
    """Return the frozen baseline quotation-length band."""

    if type(text) is not str:
        raise TypeError("quotation text must be a string")
    if len(text) <= 122:
        return "short"
    if len(text) <= 203:
        return "medium"
    return "long"


def complete_treatment_text(exact_quote_text: str, question_body: str) -> str:
    """Construct the sole permitted treatment root without normalisation."""

    if type(exact_quote_text) is not str or type(question_body) is not str:
        raise TypeError("treatment components must be strings")
    return exact_quote_text + TREATMENT_SUFFIX + question_body


def _normalised_question_body(value: str) -> str:
    return unicodedata.normalize("NFKC", " ".join(value.split())).casefold()


def _validate_question_body(value: Any) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ExperimentValidationError(
            "approved question body must be non-empty text without outer whitespace"
        )
    if len(value) > 500:
        raise ExperimentValidationError("approved question body is unbounded")
    if "\n" in value or "\r" in value:
        raise ExperimentValidationError("approved question body contains a newline")
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ExperimentValidationError(
            "approved question body contains a control character"
        )
    if not value.endswith("?") or value.count("?") != 1:
        raise ExperimentValidationError(
            "approved question body must contain exactly one final question mark"
        )
    if not any(character.isalpha() for character in value):
        raise ExperimentValidationError("approved question body contains no words")
    return value


def validate_catalogue_document(
    document: Any,
    quote_text_by_id: Mapping[str, str],
    *,
    expected_entry_count: int = APPROVED_CATALOGUE_ENTRY_COUNT,
) -> dict[str, Any]:
    """Strictly validate and recompute every approved catalogue value."""

    if not isinstance(document, dict) or set(document) != CATALOGUE_FIELDS:
        raise ExperimentValidationError("approved catalogue fields mismatch")
    if document.get("schema_version") != APPROVED_CATALOGUE_SCHEMA_VERSION:
        raise ExperimentValidationError("unsupported approved catalogue schema")
    if document.get("experiment_id") != EXPERIMENT_ID:
        raise ExperimentValidationError("approved catalogue experiment ID mismatch")
    if not HEX64_RE.fullmatch(str(document.get("source_snapshot_sha256") or "")):
        raise ExperimentValidationError(
            "approved catalogue source snapshot SHA-256 is invalid"
        )
    entries = document.get("entries")
    if not isinstance(entries, dict) or len(entries) != expected_entry_count:
        raise ExperimentValidationError(
            f"approved catalogue must contain exactly {expected_entry_count} entries"
        )
    normalised_questions: set[str] = set()
    for quote_id, entry in entries.items():
        if type(quote_id) is not str or not HEX64_RE.fullmatch(quote_id):
            raise ExperimentValidationError("catalogue quote ID is not lowercase SHA-256")
        if not isinstance(entry, dict) or set(entry) != CATALOGUE_ENTRY_FIELDS:
            raise ExperimentValidationError(
                f"approved catalogue entry fields mismatch for {quote_id}"
            )
        if entry.get("quote_text_sha256") != quote_id:
            raise ExperimentValidationError(
                f"catalogue map key differs from quote_text_sha256 for {quote_id}"
            )
        exact_quote = quote_text_by_id.get(quote_id)
        if type(exact_quote) is not str or sha256_text(exact_quote) != quote_id:
            raise ExperimentValidationError(
                f"current exact quotation is unavailable or changed for {quote_id}"
            )
        body = _validate_question_body(entry.get("question_body"))
        normalised = _normalised_question_body(body)
        if normalised in normalised_questions:
            raise ExperimentValidationError("duplicate normalised approved question body")
        normalised_questions.add(normalised)
        if entry.get("question_sha256") != sha256_text(body):
            raise ExperimentValidationError(
                f"approved question SHA-256 mismatch for {quote_id}"
            )
        treatment = complete_treatment_text(exact_quote, body)
        if entry.get("complete_treatment_sha256") != sha256_text(treatment):
            raise ExperimentValidationError(
                f"complete treatment SHA-256 mismatch for {quote_id}"
            )
        stored_length = entry.get("complete_treatment_weighted_length")
        actual_length = x_weighted_length(treatment)
        if type(stored_length) is not int or stored_length != actual_length:
            raise ExperimentValidationError(
                f"complete treatment weighted length mismatch for {quote_id}"
            )
        if actual_length > MAX_ROOT_WEIGHTED_LENGTH:
            raise ExperimentValidationError(
                f"complete treatment exceeds {MAX_ROOT_WEIGHTED_LENGTH} for {quote_id}"
            )
        if entry.get("question_source") not in QUESTION_SOURCES:
            raise ExperimentValidationError(
                f"unrecognised approved question source for {quote_id}"
            )
    return json.loads(json.dumps(document, ensure_ascii=False))


def load_approved_catalogue(
    path: Path,
    quote_text_by_id: Mapping[str, str],
    *,
    expected_sha256: str = APPROVED_CATALOGUE_SHA256,
    expected_entry_count: int = APPROVED_CATALOGUE_ENTRY_COUNT,
) -> tuple[dict[str, Any], str]:
    """Load the exact tracked catalogue after validating its file identity."""

    document, source = load_strict_json(path, label="approved question catalogue")
    digest = hashlib.sha256(source).hexdigest()
    if expected_sha256 and digest != expected_sha256:
        raise ExperimentValidationError(
            f"approved catalogue SHA-256 mismatch: expected {expected_sha256}, got {digest}"
        )
    return (
        validate_catalogue_document(
            document,
            quote_text_by_id,
            expected_entry_count=expected_entry_count,
        ),
        digest,
    )


def validate_complete_public_text(
    *,
    exact_quote_text: str,
    catalogue_entry: Mapping[str, Any],
    arm: str,
) -> str:
    """Reconstruct and validate the exact root payload immediately before use."""

    quote_id = sha256_text(exact_quote_text)
    if catalogue_entry.get("quote_text_sha256") != quote_id:
        raise ExperimentValidationError("live canonical quote hash changed")
    body = _validate_question_body(catalogue_entry.get("question_body"))
    if catalogue_entry.get("question_sha256") != sha256_text(body):
        raise ExperimentValidationError("live approved question hash changed")
    treatment = complete_treatment_text(exact_quote_text, body)
    if catalogue_entry.get("complete_treatment_sha256") != sha256_text(treatment):
        raise ExperimentValidationError("live complete treatment hash changed")
    weighted = x_weighted_length(treatment)
    if (
        type(catalogue_entry.get("complete_treatment_weighted_length")) is not int
        or catalogue_entry["complete_treatment_weighted_length"] != weighted
        or weighted > MAX_ROOT_WEIGHTED_LENGTH
    ):
        raise ExperimentValidationError("live complete treatment length changed")
    if arm == "control":
        return exact_quote_text
    if arm == "treatment":
        return treatment
    raise ExperimentValidationError(f"unknown experiment arm {arm!r}")


def candidate_from_catalogue(
    *,
    quote_id: str,
    exact_quote_text: str,
    topic: str,
    verification_label: str,
    source_class: str,
    catalogue: Mapping[str, Any],
) -> dict[str, Any]:
    """Return one validated, currently eligible plan candidate."""

    candidate = {
        "quote_id": quote_id,
        "exact_quote_text": exact_quote_text,
        "topic": topic,
        "verification_label": verification_label,
        "source_class": source_class,
    }
    if set(candidate) != CANDIDATE_FIELDS:
        raise AssertionError("internal candidate schema mismatch")
    if not HEX64_RE.fullmatch(quote_id) or sha256_text(exact_quote_text) != quote_id:
        raise ExperimentValidationError("candidate exact quotation identity mismatch")
    if not TOPIC_RE.fullmatch(topic):
        raise ExperimentValidationError(f"invalid existing broad topic {topic!r}")
    if (
        type(verification_label) is not str
        or not verification_label.strip()
        or len(verification_label) > 200
    ):
        raise ExperimentValidationError("invalid verification label")
    if type(source_class) is not str or not source_class.strip() or len(source_class) > 200:
        raise ExperimentValidationError("invalid source class")
    entries = catalogue.get("entries")
    if not isinstance(entries, Mapping) or quote_id not in entries:
        raise ExperimentValidationError("candidate is outside the approved catalogue")
    validate_complete_public_text(
        exact_quote_text=exact_quote_text,
        catalogue_entry=entries[quote_id],
        arm="treatment",
    )
    return candidate


def _pair_cost(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[int, int, int]:
    return (
        int(left["verification_label"] != right["verification_label"]),
        int(left["source_class"] != right["source_class"]),
        abs(
            x_weighted_length(str(left["exact_quote_text"]))
            - x_weighted_length(str(right["exact_quote_text"]))
        ),
    )


def _minimum_matching(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[Mapping[str, Any], Mapping[str, Any]]], str | None]:
    """Find the exact lexicographic minimum matching for one small group."""

    ordered = tuple(sorted(candidates, key=lambda row: str(row["quote_id"])))
    by_id = {str(row["quote_id"]): row for row in ordered}
    ids = tuple(by_id)

    @lru_cache(maxsize=None)
    def even_match(
        remaining: tuple[str, ...],
    ) -> tuple[tuple[int, int, int, tuple[tuple[str, str], ...]], tuple[tuple[str, str], ...]]:
        if not remaining:
            return (0, 0, 0, ()), ()
        first = remaining[0]
        best: tuple[
            tuple[int, int, int, tuple[tuple[str, str], ...]],
            tuple[tuple[str, str], ...],
        ] | None = None
        for index in range(1, len(remaining)):
            second = remaining[index]
            rest = remaining[1:index] + remaining[index + 1 :]
            sub_cost, sub_pairs = even_match(rest)
            pair_key = tuple(sorted((first, second)))
            tie_pairs = tuple(sorted((pair_key, *sub_pairs)))
            pair_cost = _pair_cost(by_id[first], by_id[second])
            cost = (
                pair_cost[0] + sub_cost[0],
                pair_cost[1] + sub_cost[1],
                pair_cost[2] + sub_cost[2],
                tie_pairs,
            )
            candidate_result = (cost, tie_pairs)
            if best is None or candidate_result[0] < best[0]:
                best = candidate_result
        if best is None:
            raise AssertionError("even matching received an odd group")
        return best

    unpaired: str | None = None
    if len(ids) % 2:
        choices = []
        for quote_id in ids:
            remaining = tuple(value for value in ids if value != quote_id)
            cost, pairs = even_match(remaining)
            choices.append((cost, quote_id, pairs))
        _cost, unpaired, selected_pairs = min(choices, key=lambda row: (row[0], row[1]))
    else:
        _cost, selected_pairs = even_match(ids)
    return [(by_id[left], by_id[right]) for left, right in selected_pairs], unpaired


def _domain_hash(fields: Sequence[str]) -> str:
    return hashlib.sha256(canonical_json_bytes(list(fields))).hexdigest()


def pair_id_for_members(
    catalogue_sha256: str,
    left_quote_id: str,
    right_quote_id: str,
) -> str:
    """Return a stable pair identity independent of publication results."""

    members = sorted((left_quote_id, right_quote_id))
    digest = _domain_hash(
        [EXPERIMENT_ID, catalogue_sha256, *members, "pair-id-v1"]
    )
    return f"pair-{digest[:24]}"


def treatment_quote_id_for_pair(
    catalogue_sha256: str,
    pair_id: str,
    quote_ids: Sequence[str],
) -> str:
    """Assign treatment with the frozen domain-separated hash."""

    ordered = sorted(quote_ids)
    if len(ordered) != 2 or ordered[0] == ordered[1]:
        raise ExperimentValidationError("arm assignment requires two quote IDs")
    digest = _domain_hash(
        [EXPERIMENT_ID, catalogue_sha256, pair_id, "arm-assignment-v1"]
    )
    return ordered[int(digest, 16) & 1]


def publication_order_hash(catalogue_sha256: str, pair_id: str) -> str:
    """Return the separate deterministic time-order assignment hash."""

    return _domain_hash(
        [EXPERIMENT_ID, catalogue_sha256, pair_id, "publication-order-v1"]
    )


def _matching_diagnostics(
    grouped: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    matched: Mapping[tuple[str, str], Sequence[dict[str, Any]]],
    unpaired: Mapping[tuple[str, str], str | None],
) -> dict[str, Any]:
    groups = []
    for key in sorted(grouped):
        topic, band = key
        groups.append(
            {
                "topic": topic,
                "quotation_length_band": band,
                "candidate_count": len(grouped[key]),
                "exact_pair_count": len(matched.get(key, ())),
                "unpaired_quote_id": unpaired.get(key),
            }
        )
    return {
        "eligible_candidate_count": sum(len(value) for value in grouped.values()),
        "possible_exact_pair_count": sum(len(value) for value in matched.values()),
        "target_pair_count": TARGET_COMPLETED_PAIRS,
        "groups": groups,
    }


def construct_exact_matched_pairs(
    candidates: Sequence[Mapping[str, Any]],
    *,
    catalogue_sha256: str,
    target_pair_count: int = TARGET_COMPLETED_PAIRS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Match exact topic/band groups and select pairs by topic round-robin."""

    if target_pair_count != TARGET_COMPLETED_PAIRS:
        raise ExperimentValidationError("plan v1 target must remain exactly 30 pairs")
    if not HEX64_RE.fullmatch(catalogue_sha256):
        raise ExperimentValidationError("catalogue SHA-256 is invalid")
    seen: set[str] = set()
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or set(candidate) != CANDIDATE_FIELDS:
            raise ExperimentValidationError("plan candidate fields mismatch")
        quote_id = str(candidate["quote_id"])
        if quote_id in seen:
            raise ExperimentValidationError("duplicate plan candidate quote ID")
        seen.add(quote_id)
        exact = str(candidate["exact_quote_text"])
        if sha256_text(exact) != quote_id:
            raise ExperimentValidationError("plan candidate quote text changed")
        topic = str(candidate["topic"])
        if not TOPIC_RE.fullmatch(topic):
            raise ExperimentValidationError("plan candidate topic is invalid")
        grouped[(topic, quotation_length_band(exact))].append(candidate)

    matched: dict[tuple[str, str], list[dict[str, Any]]] = {}
    unpaired: dict[tuple[str, str], str | None] = {}
    for group_key, rows in grouped.items():
        pairs, omitted = _minimum_matching(rows)
        unpaired[group_key] = omitted
        group_pairs: list[dict[str, Any]] = []
        for left, right in pairs:
            quote_ids = sorted((str(left["quote_id"]), str(right["quote_id"])))
            cost = _pair_cost(left, right)
            pair_id = pair_id_for_members(
                catalogue_sha256, quote_ids[0], quote_ids[1]
            )
            group_pairs.append(
                {
                    "pair_id": pair_id,
                    "topic": group_key[0],
                    "quotation_length_band": group_key[1],
                    "candidate_members": [left, right],
                    "cost": cost,
                    "selection_tiebreak_sha256": _domain_hash(
                        [
                            EXPERIMENT_ID,
                            catalogue_sha256,
                            pair_id,
                            "pair-selection-v1",
                        ]
                    ),
                }
            )
        matched[group_key] = group_pairs

    diagnostics = _matching_diagnostics(grouped, matched, unpaired)
    if diagnostics["possible_exact_pair_count"] < target_pair_count:
        raise PlanPreparationError(
            "fewer than 30 exact-topic/exact-length pairs are available",
            diagnostics,
        )

    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rows in matched.values():
        for pair in rows:
            by_topic[pair["topic"]].append(pair)
    for topic in by_topic:
        by_topic[topic].sort(
            key=lambda pair: (
                pair["cost"][0],
                pair["cost"][1],
                pair["cost"][2],
                pair["selection_tiebreak_sha256"],
            )
        )

    selected: list[dict[str, Any]] = []
    topic_order = sorted(by_topic)
    while len(selected) < target_pair_count:
        progress = False
        for topic in topic_order:
            if by_topic[topic]:
                selected.append(by_topic[topic].pop(0))
                progress = True
                if len(selected) == target_pair_count:
                    break
        if not progress:
            raise AssertionError("matching diagnostics and selection disagree")
    diagnostics["selected_pair_count"] = len(selected)
    diagnostics["selected_quote_count"] = len(selected) * 2
    return selected, diagnostics


def _member_document(
    candidate: Mapping[str, Any],
    *,
    arm: str,
    position: int,
    catalogue: Mapping[str, Any],
) -> dict[str, Any]:
    quote_id = str(candidate["quote_id"])
    entry = catalogue["entries"][quote_id]
    exact = str(candidate["exact_quote_text"])
    return {
        "quote_id": quote_id,
        "arm": arm,
        "position": position,
        "exact_control_text_sha256": sha256_text(exact),
        "approved_question_body": str(entry["question_body"]),
        "approved_question_sha256": str(entry["question_sha256"]),
        "complete_treatment_sha256": str(entry["complete_treatment_sha256"]),
        "complete_treatment_weighted_length": int(
            entry["complete_treatment_weighted_length"]
        ),
        "control_weighted_length": x_weighted_length(exact),
        "verification_label": str(candidate["verification_label"]),
        "source_class": str(candidate["source_class"]),
        "question_source": str(entry["question_source"]),
    }


def build_plan(
    *,
    catalogue: Mapping[str, Any],
    catalogue_sha256: str,
    candidates: Sequence[Mapping[str, Any]],
    used_history_sha256: str,
    plan_created_at: str,
    plan_kind: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build one canonical 30-pair plan from validated eligible candidates."""

    if catalogue.get("experiment_id") != EXPERIMENT_ID:
        raise ExperimentValidationError("catalogue experiment ID mismatch")
    if plan_kind not in PLAN_KINDS:
        raise ExperimentValidationError("plan kind must be preview or live")
    if not HEX64_RE.fullmatch(used_history_sha256):
        raise ExperimentValidationError("used-history SHA-256 is invalid")
    _validate_iso_timestamp(plan_created_at)
    raw_pairs, diagnostics = construct_exact_matched_pairs(
        candidates,
        catalogue_sha256=catalogue_sha256,
    )

    order_rank = sorted(
        raw_pairs,
        key=lambda pair: (
            publication_order_hash(catalogue_sha256, pair["pair_id"]),
            pair["pair_id"],
        ),
    )
    treatment_first = {
        pair["pair_id"] for pair in order_rank[: TARGET_COMPLETED_PAIRS // 2]
    }
    pairs: list[dict[str, Any]] = []
    for raw_pair in raw_pairs:
        pair_id = str(raw_pair["pair_id"])
        candidates_by_id = {
            str(row["quote_id"]): row for row in raw_pair["candidate_members"]
        }
        treatment_id = treatment_quote_id_for_pair(
            catalogue_sha256,
            pair_id,
            list(candidates_by_id),
        )
        control_id = next(value for value in candidates_by_id if value != treatment_id)
        publication_order = (
            "treatment_first" if pair_id in treatment_first else "control_first"
        )
        ordered_ids = (
            (treatment_id, control_id)
            if publication_order == "treatment_first"
            else (control_id, treatment_id)
        )
        members = [
            _member_document(
                candidates_by_id[quote_id],
                arm="treatment" if quote_id == treatment_id else "control",
                position=position,
                catalogue=catalogue,
            )
            for position, quote_id in enumerate(ordered_ids, 1)
        ]
        pairs.append(
            {
                "pair_id": pair_id,
                "topic": str(raw_pair["topic"]),
                "quotation_length_band": str(raw_pair["quotation_length_band"]),
                "planned_publication_order": publication_order,
                "matching": {
                    "verification_label_mismatch": bool(raw_pair["cost"][0]),
                    "source_class_mismatch": bool(raw_pair["cost"][1]),
                    "control_weighted_length_difference": int(raw_pair["cost"][2]),
                },
                "members": members,
            }
        )

    plan_without_hash = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "plan_kind": plan_kind,
        "approved_catalogue_sha256": catalogue_sha256,
        "approved_catalogue_source_snapshot_sha256": str(
            catalogue["source_snapshot_sha256"]
        ),
        "plan_created_at": plan_created_at,
        "used_history_sha256_at_creation": used_history_sha256,
        "target_completed_pairs": TARGET_COMPLETED_PAIRS,
        "pair_count": len(pairs),
        "pairs": pairs,
    }
    plan = {**plan_without_hash, "plan_sha256": canonical_sha256(plan_without_hash)}
    metadata = {str(row["quote_id"]): dict(row) for row in candidates}
    validate_plan_document(
        plan,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256,
        quote_text_by_id={
            quote_id: str(row["exact_quote_text"])
            for quote_id, row in metadata.items()
        },
        metadata_by_quote_id=metadata,
        require_plan_kind=plan_kind,
    )
    return plan, diagnostics


def _validate_iso_timestamp(value: Any) -> str:
    if type(value) is not str or not value or len(value) > 64:
        raise ExperimentValidationError("plan timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExperimentValidationError("plan timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise ExperimentValidationError("plan timestamp must include a timezone")
    return value


def calculate_plan_sha256(plan: Mapping[str, Any]) -> str:
    """Calculate the canonical plan hash excluding its self-hash field."""

    return canonical_sha256({key: value for key, value in plan.items() if key != "plan_sha256"})


def validate_plan_document(
    document: Any,
    *,
    catalogue: Mapping[str, Any],
    catalogue_sha256: str,
    quote_text_by_id: Mapping[str, str],
    metadata_by_quote_id: Mapping[str, Mapping[str, Any]] | None = None,
    require_plan_kind: str | None = None,
) -> dict[str, Any]:
    """Strictly validate a plan and every current canonical quotation binding."""

    if not isinstance(document, dict) or set(document) != PLAN_FIELDS:
        raise ExperimentValidationError("experiment plan fields mismatch")
    if document.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ExperimentValidationError("unsupported experiment plan schema")
    if document.get("experiment_id") != EXPERIMENT_ID:
        raise ExperimentValidationError("experiment plan ID mismatch")
    plan_kind = document.get("plan_kind")
    if plan_kind not in PLAN_KINDS or (
        require_plan_kind is not None and plan_kind != require_plan_kind
    ):
        raise ExperimentValidationError("experiment plan kind mismatch")
    if document.get("approved_catalogue_sha256") != catalogue_sha256:
        raise ExperimentValidationError("experiment plan catalogue hash mismatch")
    if (
        document.get("approved_catalogue_source_snapshot_sha256")
        != catalogue.get("source_snapshot_sha256")
    ):
        raise ExperimentValidationError("experiment plan source snapshot mismatch")
    _validate_iso_timestamp(document.get("plan_created_at"))
    if not HEX64_RE.fullmatch(str(document.get("used_history_sha256_at_creation") or "")):
        raise ExperimentValidationError("experiment plan used-history hash is invalid")
    if document.get("target_completed_pairs") != TARGET_COMPLETED_PAIRS:
        raise ExperimentValidationError("experiment plan target is not 30 pairs")
    pairs = document.get("pairs")
    if (
        not isinstance(pairs, list)
        or len(pairs) != TARGET_COMPLETED_PAIRS
        or document.get("pair_count") != len(pairs)
    ):
        raise ExperimentValidationError("experiment plan must contain 30 pairs")
    if (
        type(document.get("plan_sha256")) is not str
        or not HEX64_RE.fullmatch(document["plan_sha256"])
        or document["plan_sha256"] != calculate_plan_sha256(document)
    ):
        raise ExperimentValidationError("experiment plan canonical hash mismatch")

    seen_pairs: set[str] = set()
    seen_quotes: set[str] = set()
    order_by_pair: dict[str, str] = {}
    for pair in pairs:
        if not isinstance(pair, dict) or set(pair) != PAIR_FIELDS:
            raise ExperimentValidationError("experiment pair fields mismatch")
        pair_id = pair.get("pair_id")
        if type(pair_id) is not str or not PAIR_ID_RE.fullmatch(pair_id):
            raise ExperimentValidationError("experiment pair ID is invalid")
        if pair_id in seen_pairs:
            raise ExperimentValidationError("duplicate experiment pair ID")
        seen_pairs.add(pair_id)
        topic = pair.get("topic")
        if type(topic) is not str or not TOPIC_RE.fullmatch(topic):
            raise ExperimentValidationError("experiment pair topic is invalid")
        band = pair.get("quotation_length_band")
        if band not in {"short", "medium", "long"}:
            raise ExperimentValidationError("experiment pair length band is invalid")
        order = pair.get("planned_publication_order")
        if order not in PUBLICATION_ORDERS:
            raise ExperimentValidationError("experiment publication order is invalid")
        order_by_pair[pair_id] = str(order)
        matching = pair.get("matching")
        if not isinstance(matching, dict) or set(matching) != PAIR_MATCHING_FIELDS:
            raise ExperimentValidationError("experiment pair matching fields mismatch")
        if any(
            type(matching.get(key)) is not bool
            for key in ("verification_label_mismatch", "source_class_mismatch")
        ):
            raise ExperimentValidationError("experiment pair mismatch flag is invalid")
        difference = matching.get("control_weighted_length_difference")
        if type(difference) is not int or difference < 0:
            raise ExperimentValidationError("experiment pair length difference is invalid")
        members = pair.get("members")
        if not isinstance(members, list) or len(members) != 2:
            raise ExperimentValidationError("experiment pair must have two members")
        if [member.get("position") for member in members if isinstance(member, dict)] != [1, 2]:
            raise ExperimentValidationError("experiment member positions are invalid")
        member_ids: list[str] = []
        arms: list[str] = []
        for member in members:
            if not isinstance(member, dict) or set(member) != MEMBER_FIELDS:
                raise ExperimentValidationError("experiment member fields mismatch")
            quote_id = member.get("quote_id")
            if type(quote_id) is not str or not HEX64_RE.fullmatch(quote_id):
                raise ExperimentValidationError("experiment member quote ID is invalid")
            if quote_id in seen_quotes:
                raise ExperimentValidationError("experiment plan repeats a quote ID")
            seen_quotes.add(quote_id)
            member_ids.append(quote_id)
            arm = member.get("arm")
            if arm not in ARMS:
                raise ExperimentValidationError("experiment member arm is invalid")
            arms.append(str(arm))
            entry = (catalogue.get("entries") or {}).get(quote_id)
            exact = quote_text_by_id.get(quote_id)
            if not isinstance(entry, Mapping) or type(exact) is not str:
                raise ExperimentValidationError("plan member is outside current catalogue/source")
            if sha256_text(exact) != quote_id:
                raise ExperimentValidationError("plan member canonical quote changed")
            validate_complete_public_text(
                exact_quote_text=exact,
                catalogue_entry=entry,
                arm=str(arm),
            )
            expected = {
                "exact_control_text_sha256": quote_id,
                "approved_question_body": entry["question_body"],
                "approved_question_sha256": entry["question_sha256"],
                "complete_treatment_sha256": entry["complete_treatment_sha256"],
                "complete_treatment_weighted_length": entry[
                    "complete_treatment_weighted_length"
                ],
                "control_weighted_length": x_weighted_length(exact),
                "question_source": entry["question_source"],
            }
            for field, value in expected.items():
                if member.get(field) != value:
                    raise ExperimentValidationError(
                        f"experiment member {field} mismatch for {quote_id}"
                    )
            if quotation_length_band(exact) != band:
                raise ExperimentValidationError("experiment pair crosses length bands")
            for field in ("verification_label", "source_class"):
                value = member.get(field)
                if type(value) is not str or not value.strip() or len(value) > 200:
                    raise ExperimentValidationError(
                        f"experiment member {field} is invalid"
                    )
            if metadata_by_quote_id is not None:
                metadata = metadata_by_quote_id.get(quote_id)
                if not isinstance(metadata, Mapping):
                    raise ExperimentValidationError("plan member is no longer eligible")
                if metadata.get("topic") != topic:
                    raise ExperimentValidationError("experiment pair crosses topics")
                for field in ("verification_label", "source_class"):
                    if member.get(field) != metadata.get(field):
                        raise ExperimentValidationError(
                            f"experiment member current {field} changed"
                        )
        if set(arms) != ARMS:
            raise ExperimentValidationError("experiment pair arms are not balanced")
        if pair_id_for_members(catalogue_sha256, *member_ids) != pair_id:
            raise ExperimentValidationError("experiment pair identity changed")
        treatment_id = treatment_quote_id_for_pair(
            catalogue_sha256, pair_id, member_ids
        )
        if next(member["quote_id"] for member in members if member["arm"] == "treatment") != treatment_id:
            raise ExperimentValidationError("experiment arm assignment changed")
        expected_first_arm = "treatment" if order == "treatment_first" else "control"
        if members[0]["arm"] != expected_first_arm:
            raise ExperimentValidationError("experiment publication order changed")
        expected_mismatch = {
            "verification_label_mismatch": members[0]["verification_label"]
            != members[1]["verification_label"],
            "source_class_mismatch": members[0]["source_class"]
            != members[1]["source_class"],
            "control_weighted_length_difference": abs(
                members[0]["control_weighted_length"]
                - members[1]["control_weighted_length"]
            ),
        }
        if matching != expected_mismatch:
            raise ExperimentValidationError("experiment pair matching metadata changed")

    if len(seen_quotes) != TARGET_COMPLETED_PAIRS * 2:
        raise ExperimentValidationError("experiment plan does not contain 60 unique quotes")
    ranked = sorted(
        seen_pairs,
        key=lambda pair_id: (
            publication_order_hash(catalogue_sha256, pair_id),
            pair_id,
        ),
    )
    expected_treatment_first = set(ranked[: TARGET_COMPLETED_PAIRS // 2])
    actual_treatment_first = {
        pair_id for pair_id, order in order_by_pair.items() if order == "treatment_first"
    }
    if actual_treatment_first != expected_treatment_first:
        raise ExperimentValidationError("experiment time-order assignment changed")
    return json.loads(json.dumps(document, ensure_ascii=False))


def new_experiment_state(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Create the bounded state only when an enabled live plan is starting."""

    if plan.get("experiment_id") != EXPERIMENT_ID or plan.get("pair_count") != 30:
        raise ExperimentValidationError("cannot start state from an invalid plan")
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "active_plan_sha256": str(plan["plan_sha256"]),
        "status": "not_started",
        "current_pair_index": 0,
        "active_pair_id": None,
        "next_pair_member_position": 1,
        "completed_pair_count": 0,
        "confirmed_publications": [],
        "last_experimental_publication_local_date": None,
        "treatment_publication_count": 0,
        "latest_treatment_notification_identity": None,
        "current_deferral_reason": None,
    }


def _valid_date(value: Any, *, optional: bool = False) -> bool:
    if optional and value is None:
        return True
    if type(value) is not str or not DATE_RE.fullmatch(value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def local_date_for_epoch(epoch: int) -> str:
    """Return the frozen Europe/London calendar date for an epoch."""

    if type(epoch) is not int or epoch < 0:
        raise ExperimentValidationError("publication epoch is invalid")
    return datetime.fromtimestamp(epoch, tz=ZoneInfo(LONDON_TIMEZONE)).strftime(
        "%Y-%m-%d"
    )


def bounded_quote_excerpt(exact_quote_text: str) -> str:
    """Return a compact single-line quotation excerpt for notification only."""

    if type(exact_quote_text) is not str or not exact_quote_text:
        raise ExperimentValidationError("quotation excerpt source is invalid")
    single_line = " ".join(exact_quote_text.splitlines())
    if len(single_line) <= MAX_QUOTE_EXCERPT_CHARACTERS:
        return single_line
    return single_line[: MAX_QUOTE_EXCERPT_CHARACTERS - 1].rstrip() + "…"


def validate_notification_document(document: Any) -> dict[str, Any]:
    """Validate one compact confirmed-treatment notification document."""

    if not isinstance(document, dict) or set(document) != NOTIFICATION_FIELDS:
        raise ExperimentValidationError("treatment notification fields mismatch")
    if document.get("schema_version") != NOTIFICATION_SCHEMA_VERSION:
        raise ExperimentValidationError("unsupported treatment notification schema")
    if document.get("experiment_id") != EXPERIMENT_ID:
        raise ExperimentValidationError("treatment notification experiment mismatch")
    if not HEX64_RE.fullmatch(str(document.get("plan_sha256") or "")):
        raise ExperimentValidationError("treatment notification plan hash is invalid")
    post_id = document.get("post_id")
    if type(post_id) is not str or not POST_ID_RE.fullmatch(post_id):
        raise ExperimentValidationError("treatment notification post ID is invalid")
    if not PAIR_ID_RE.fullmatch(str(document.get("pair_id") or "")):
        raise ExperimentValidationError("treatment notification pair ID is invalid")
    number = document.get("treatment_number")
    if type(number) is not int or not 1 <= number <= TARGET_TREATMENT_COUNT:
        raise ExperimentValidationError("treatment notification sequence is invalid")
    if document.get("target_treatment_count") != TARGET_TREATMENT_COUNT:
        raise ExperimentValidationError("treatment notification target is invalid")
    epoch = document.get("published_epoch")
    if type(epoch) is not int or epoch < 0:
        raise ExperimentValidationError("treatment notification epoch is invalid")
    excerpt = document.get("quote_excerpt")
    if (
        type(excerpt) is not str
        or not excerpt
        or len(excerpt) > MAX_QUOTE_EXCERPT_CHARACTERS
        or "\n" in excerpt
    ):
        raise ExperimentValidationError("treatment notification excerpt is invalid")
    _validate_question_body(document.get("question"))
    if document.get("post_url") != f"https://x.com/MrsMThatcher/status/{post_id}":
        raise ExperimentValidationError("treatment notification URL is invalid")
    return document


def _validate_notification_identity(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict) or set(value) != NOTIFICATION_IDENTITY_FIELDS:
        return False
    try:
        document = validate_notification_document(value.get("document"))
    except ExperimentValidationError:
        return False
    return bool(
        type(value.get("post_id")) is str
        and value["post_id"] == document["post_id"]
        and type(value.get("document_sha256")) is str
        and value["document_sha256"] == canonical_sha256(document)
        and type(value.get("delivered")) is bool
    )


def validate_experiment_state(
    value: Any,
    *,
    plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Strictly validate bounded protected experiment state."""

    if not isinstance(value, dict) or set(value) != STATE_FIELDS:
        raise ExperimentValidationError("protected experiment state fields mismatch")
    if value.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ExperimentValidationError("unsupported protected experiment state schema")
    if value.get("experiment_id") != EXPERIMENT_ID:
        raise ExperimentValidationError("protected experiment state ID mismatch")
    if not HEX64_RE.fullmatch(str(value.get("active_plan_sha256") or "")):
        raise ExperimentValidationError("protected experiment plan hash is invalid")
    if plan is not None and value["active_plan_sha256"] != plan.get("plan_sha256"):
        raise ExperimentValidationError("protected experiment state is bound to another plan")
    status = value.get("status")
    if status not in EXPERIMENT_STATUSES:
        raise ExperimentValidationError("protected experiment status is invalid")
    current_index = value.get("current_pair_index")
    completed = value.get("completed_pair_count")
    next_position = value.get("next_pair_member_position")
    treatment_count = value.get("treatment_publication_count")
    if (
        type(current_index) is not int
        or not 0 <= current_index <= TARGET_COMPLETED_PAIRS
        or type(completed) is not int
        or not 0 <= completed <= TARGET_COMPLETED_PAIRS
        or completed != current_index
        or type(next_position) is not int
        or next_position not in {1, 2}
        or type(treatment_count) is not int
        or not 0 <= treatment_count <= TARGET_TREATMENT_COUNT
    ):
        raise ExperimentValidationError("protected experiment counters are invalid")
    active_pair_id = value.get("active_pair_id")
    if active_pair_id is not None and (
        type(active_pair_id) is not str or not PAIR_ID_RE.fullmatch(active_pair_id)
    ):
        raise ExperimentValidationError("protected active pair ID is invalid")
    publications = value.get("confirmed_publications")
    if (
        not isinstance(publications, list)
        or len(publications) > MAX_CONFIRMED_PUBLICATIONS
    ):
        raise ExperimentValidationError("protected publication history is invalid")
    seen_posts: set[str] = set()
    seen_quotes: set[str] = set()
    counted_treatments = 0
    latest_date: str | None = None
    pair_publications: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sequence, publication in enumerate(publications, 1):
        if not isinstance(publication, dict) or set(publication) != PUBLICATION_FIELDS:
            raise ExperimentValidationError("protected publication fields mismatch")
        if publication.get("publication_sequence") != sequence:
            raise ExperimentValidationError("protected publication sequence is not contiguous")
        post_id = publication.get("post_id")
        quote_id = publication.get("quote_id")
        pair_id = publication.get("pair_id")
        if (
            type(post_id) is not str
            or not POST_ID_RE.fullmatch(post_id)
            or post_id in seen_posts
            or type(quote_id) is not str
            or not HEX64_RE.fullmatch(quote_id)
            or quote_id in seen_quotes
            or type(pair_id) is not str
            or not PAIR_ID_RE.fullmatch(pair_id)
        ):
            raise ExperimentValidationError("protected publication identity is invalid")
        seen_posts.add(post_id)
        seen_quotes.add(quote_id)
        arm = publication.get("arm")
        position = publication.get("member_position")
        order = publication.get("publication_order")
        if (
            arm not in ARMS
            or type(position) is not int
            or position not in {1, 2}
            or order not in PUBLICATION_ORDERS
            or type(publication.get("question_present")) is not bool
            or publication["question_present"] != (arm == "treatment")
            or not HEX64_RE.fullmatch(
                str(publication.get("approved_question_sha256") or "")
            )
            or not HEX64_RE.fullmatch(
                str(publication.get("public_text_sha256") or "")
            )
        ):
            raise ExperimentValidationError("protected publication arm metadata is invalid")
        epoch = publication.get("published_epoch")
        local_date = publication.get("local_date")
        gap = publication.get("pair_member_gap_seconds")
        if (
            type(epoch) is not int
            or epoch < 0
            or not _valid_date(local_date)
            or local_date_for_epoch(epoch) != local_date
            or (gap is not None and (type(gap) is not int or gap < 0))
        ):
            raise ExperimentValidationError("protected publication timing is invalid")
        pair_rows = pair_publications[pair_id]
        if not pair_rows and gap is not None:
            raise ExperimentValidationError("first pair member cannot have a publication gap")
        if pair_rows:
            if len(pair_rows) != 1 or gap != abs(epoch - pair_rows[0]["published_epoch"]):
                raise ExperimentValidationError("second pair-member gap is invalid")
        pair_rows.append(publication)
        if arm == "treatment":
            counted_treatments += 1
        latest_date = local_date
    if counted_treatments != treatment_count:
        raise ExperimentValidationError("protected treatment count is inconsistent")
    last_date = value.get("last_experimental_publication_local_date")
    if not _valid_date(last_date, optional=True) or last_date != latest_date:
        raise ExperimentValidationError("protected last publication date is inconsistent")
    if not _validate_notification_identity(
        value.get("latest_treatment_notification_identity")
    ):
        raise ExperimentValidationError("protected treatment notification identity is invalid")
    notification = value.get("latest_treatment_notification_identity")
    if treatment_count == 0 and notification is not None:
        raise ExperimentValidationError("notification exists without a treatment")
    if notification is not None:
        latest_treatment = next(
            (row for row in reversed(publications) if row["arm"] == "treatment"),
            None,
        )
        if latest_treatment is None or notification["post_id"] != latest_treatment["post_id"]:
            raise ExperimentValidationError("notification does not identify latest treatment")
    deferral = value.get("current_deferral_reason")
    if deferral is not None:
        if not isinstance(deferral, dict) or set(deferral) != DEFERRAL_FIELDS:
            raise ExperimentValidationError("protected deferral fields mismatch")
        if (
            type(deferral.get("code")) is not str
            or not re.fullmatch(r"[a-z0-9_]{1,80}", deferral["code"])
            or (
                deferral.get("pair_id") is not None
                and not PAIR_ID_RE.fullmatch(str(deferral["pair_id"]))
            )
            or (
                deferral.get("member_position") is not None
                and deferral["member_position"] not in {1, 2}
            )
            or type(deferral.get("recorded_epoch")) is not int
            or deferral["recorded_epoch"] < 0
        ):
            raise ExperimentValidationError("protected deferral reason is invalid")

    expected_member_count = completed * 2 + (next_position - 1 if active_pair_id else 0)
    if status == "not_started" and (
        current_index or active_pair_id is not None or publications
    ):
        raise ExperimentValidationError("not-started experiment contains progress")
    if status == "completed" and (
        completed != TARGET_COMPLETED_PAIRS
        or active_pair_id is not None
        or next_position != 1
        or len(publications) != MAX_CONFIRMED_PUBLICATIONS
    ):
        raise ExperimentValidationError("completed experiment state is incomplete")
    if status in {"active", "paused"} and completed >= TARGET_COMPLETED_PAIRS:
        raise ExperimentValidationError("active experiment already reached its target")
    if status != "invalid" and len(publications) != expected_member_count:
        raise ExperimentValidationError("protected experiment progress shape is inconsistent")
    if active_pair_id is None and status not in {"invalid", "completed", "not_started"} and next_position != 1:
        raise ExperimentValidationError("inactive pair has a pending second position")

    if plan is not None and status != "invalid":
        plan_pairs = plan.get("pairs")
        if not isinstance(plan_pairs, list) or len(plan_pairs) != TARGET_COMPLETED_PAIRS:
            raise ExperimentValidationError("protected state plan is invalid")
        if active_pair_id is not None:
            if current_index >= len(plan_pairs) or plan_pairs[current_index]["pair_id"] != active_pair_id:
                raise ExperimentValidationError("protected active pair differs from plan")
        expected_rows: list[dict[str, Any]] = []
        for pair_index, pair in enumerate(plan_pairs):
            limit = 2 if pair_index < completed else 0
            if pair_index == current_index and active_pair_id is not None:
                limit = next_position - 1
            for member in pair["members"][:limit]:
                expected_rows.append(
                    {
                        "pair_id": pair["pair_id"],
                        "quote_id": member["quote_id"],
                        "arm": member["arm"],
                        "member_position": member["position"],
                        "publication_order": pair["planned_publication_order"],
                        "approved_question_sha256": member[
                            "approved_question_sha256"
                        ],
                    }
                )
        if len(expected_rows) != len(publications):
            raise ExperimentValidationError("protected plan progress length differs")
        for actual, expected in zip(publications, expected_rows):
            if any(actual[field] != expected[field] for field in expected):
                raise ExperimentValidationError("protected publication differs from plan")
    return json.loads(json.dumps(value, ensure_ascii=False))


def start_next_pair(state: dict[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Durably model the next pair before any media upload or remote post."""

    validate_experiment_state(state, plan=plan)
    if state["status"] in {"completed", "invalid"} or state["active_pair_id"] is not None:
        raise ExperimentValidationError("experiment cannot start another pair")
    index = int(state["current_pair_index"])
    if index >= TARGET_COMPLETED_PAIRS:
        raise ExperimentValidationError("experiment has no remaining pair")
    state["status"] = "active"
    state["active_pair_id"] = str(plan["pairs"][index]["pair_id"])
    state["next_pair_member_position"] = 1
    state["current_deferral_reason"] = None
    validate_experiment_state(state, plan=plan)
    return state


def set_experiment_paused(
    state: dict[str, Any],
    *,
    paused: bool,
    plan: Mapping[str, Any] | None = None,
) -> bool:
    """Apply configuration pause/resume without releasing reservations."""

    validate_experiment_state(state, plan=plan)
    if state["status"] in {"completed", "invalid", "not_started"}:
        return False
    desired = "paused" if paused else "active"
    if state["status"] == desired:
        return False
    state["status"] = desired
    validate_experiment_state(state, plan=plan)
    return True


def mark_experiment_invalid(
    state: dict[str, Any], *, code: str, recorded_epoch: int
) -> dict[str, Any]:
    """Stop publication and naturally release reservations after plan invalidation."""

    validate_experiment_state(state)
    state["status"] = "invalid"
    state["current_deferral_reason"] = {
        "code": _bounded_reason_code(code),
        "pair_id": state.get("active_pair_id"),
        "member_position": (
            state.get("next_pair_member_position")
            if state.get("active_pair_id") is not None
            else None
        ),
        "recorded_epoch": int(recorded_epoch),
    }
    validate_experiment_state(state)
    return state


def _bounded_reason_code(value: str) -> str:
    code = re.sub(r"[^a-z0-9_]+", "_", str(value).strip().casefold()).strip("_")
    return (code or "unspecified")[:80]


def record_deferral(
    state: dict[str, Any],
    *,
    code: str,
    recorded_epoch: int,
) -> dict[str, Any]:
    """Record one bounded deterministic deferral without advancing progress."""

    validate_experiment_state(state)
    state["current_deferral_reason"] = {
        "code": _bounded_reason_code(code),
        "pair_id": state.get("active_pair_id"),
        "member_position": (
            state.get("next_pair_member_position")
            if state.get("active_pair_id") is not None
            else None
        ),
        "recorded_epoch": int(recorded_epoch),
    }
    validate_experiment_state(state)
    return state


def reserved_quote_ids(plan: Mapping[str, Any], state: Mapping[str, Any] | None) -> set[str]:
    """Return all selected but unconfirmed IDs for a started valid experiment."""

    if not isinstance(state, Mapping) or state.get("status") not in {"active", "paused"}:
        return set()
    validate_experiment_state(dict(state), plan=plan)
    confirmed = {
        str(row["quote_id"]) for row in state.get("confirmed_publications", [])
    }
    return {
        str(member["quote_id"])
        for pair in plan["pairs"]
        for member in pair["members"]
        if str(member["quote_id"]) not in confirmed
    }


def member_for_current_opportunity(
    plan: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    current_epoch: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return the scheduled member, or a deterministic reason for an ordinary post."""

    validate_experiment_state(dict(state), plan=plan)
    if state["status"] != "active":
        return None, "experiment_not_active"
    today = local_date_for_epoch(current_epoch)
    if state["active_pair_id"] is None:
        if state["last_experimental_publication_local_date"] == today:
            return None, "experimental_publication_already_confirmed_today"
        return None, "pair_start_required"
    pair = plan["pairs"][state["current_pair_index"]]
    position = int(state["next_pair_member_position"])
    member = pair["members"][position - 1]
    if member["arm"] == "treatment" and any(
        row["arm"] == "treatment" and row["local_date"] == today
        for row in state["confirmed_publications"]
    ):
        return None, "treatment_already_confirmed_today"
    return {
        "pair_index": int(state["current_pair_index"]),
        "pair_id": str(pair["pair_id"]),
        "topic": str(pair["topic"]),
        "quotation_length_band": str(pair["quotation_length_band"]),
        "publication_order": str(pair["planned_publication_order"]),
        **dict(member),
    }, None


def build_attempt_binding(
    *,
    plan: Mapping[str, Any],
    state: Mapping[str, Any],
    member: Mapping[str, Any],
    exact_quote_text: str,
    public_text: str,
) -> dict[str, Any]:
    """Bind one exact arm, payload, and expected confirmed-state transition."""

    validate_experiment_state(dict(state), plan=plan)
    pair_index = int(member["pair_index"])
    position = int(member["position"])
    if (
        state["status"] != "active"
        or state["active_pair_id"] != member["pair_id"]
        or state["next_pair_member_position"] != position
        or plan["pairs"][pair_index]["members"][position - 1]["quote_id"]
        != member["quote_id"]
    ):
        raise ExperimentValidationError("pending experiment member differs from state")
    after_completed = int(state["completed_pair_count"]) + (1 if position == 2 else 0)
    after_status = "completed" if after_completed == TARGET_COMPLETED_PAIRS else "active"
    binding = {
        "schema_version": ATTEMPT_BINDING_SCHEMA_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "plan_sha256": str(plan["plan_sha256"]),
        "pair_id": str(member["pair_id"]),
        "pair_index": pair_index,
        "member_position": position,
        "arm": str(member["arm"]),
        "publication_order": str(member["publication_order"]),
        "publication_sequence": len(state["confirmed_publications"]) + 1,
        "approved_question_sha256": str(member["approved_question_sha256"]),
        "question_present": member["arm"] == "treatment",
        "canonical_quote_sha256": sha256_text(exact_quote_text),
        "public_text_sha256": sha256_text(public_text),
        "expected_transition": {
            "confirmed_publication_count_before": len(
                state["confirmed_publications"]
            ),
            "completed_pair_count_before": int(state["completed_pair_count"]),
            "treatment_publication_count_before": int(
                state["treatment_publication_count"]
            ),
            "current_pair_index_after": pair_index + (1 if position == 2 else 0),
            "active_pair_id_after": None if position == 2 else str(member["pair_id"]),
            "next_pair_member_position_after": 1 if position == 2 else 2,
            "completed_pair_count_after": after_completed,
            "status_after": after_status,
        },
    }
    validate_attempt_binding(
        binding,
        plan=plan,
        exact_quote_text=exact_quote_text,
        public_text=public_text,
        state_before=state,
    )
    return binding


def validate_attempt_binding(
    binding: Any,
    *,
    plan: Mapping[str, Any] | None = None,
    exact_quote_text: str | None = None,
    public_text: str | None = None,
    state_before: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate durable pre-write experiment identity and transition data."""

    if not isinstance(binding, dict) or set(binding) != ATTEMPT_BINDING_FIELDS:
        raise ExperimentValidationError("experiment attempt binding fields mismatch")
    if (
        binding.get("schema_version") != ATTEMPT_BINDING_SCHEMA_VERSION
        or binding.get("experiment_id") != EXPERIMENT_ID
        or not HEX64_RE.fullmatch(str(binding.get("plan_sha256") or ""))
        or not PAIR_ID_RE.fullmatch(str(binding.get("pair_id") or ""))
        or type(binding.get("pair_index")) is not int
        or not 0 <= binding["pair_index"] < TARGET_COMPLETED_PAIRS
        or binding.get("member_position") not in {1, 2}
        or binding.get("arm") not in ARMS
        or binding.get("publication_order") not in PUBLICATION_ORDERS
        or type(binding.get("publication_sequence")) is not int
        or not 1 <= binding["publication_sequence"] <= MAX_CONFIRMED_PUBLICATIONS
        or not HEX64_RE.fullmatch(
            str(binding.get("approved_question_sha256") or "")
        )
        or type(binding.get("question_present")) is not bool
        or binding["question_present"] != (binding["arm"] == "treatment")
        or not HEX64_RE.fullmatch(str(binding.get("canonical_quote_sha256") or ""))
        or not HEX64_RE.fullmatch(str(binding.get("public_text_sha256") or ""))
    ):
        raise ExperimentValidationError("experiment attempt binding is invalid")
    transition = binding.get("expected_transition")
    if not isinstance(transition, dict) or set(transition) != EXPECTED_TRANSITION_FIELDS:
        raise ExperimentValidationError("experiment expected transition fields mismatch")
    integer_fields = {
        "confirmed_publication_count_before",
        "completed_pair_count_before",
        "treatment_publication_count_before",
        "current_pair_index_after",
        "next_pair_member_position_after",
        "completed_pair_count_after",
    }
    if any(type(transition.get(field)) is not int for field in integer_fields):
        raise ExperimentValidationError("experiment expected transition counters are invalid")
    if transition.get("active_pair_id_after") is not None and not PAIR_ID_RE.fullmatch(
        str(transition["active_pair_id_after"])
    ):
        raise ExperimentValidationError("experiment expected active pair is invalid")
    if transition.get("status_after") not in {"active", "completed"}:
        raise ExperimentValidationError("experiment expected status is invalid")
    pair_index = int(binding["pair_index"])
    position = int(binding["member_position"])
    expected_confirmed_before = pair_index * 2 + position - 1
    expected_completed_after = pair_index + int(position == 2)
    expected_treatments_before = pair_index + int(
        position == 2 and binding["arm"] == "control"
    )
    expected_transition = {
        "confirmed_publication_count_before": expected_confirmed_before,
        "completed_pair_count_before": pair_index,
        "treatment_publication_count_before": expected_treatments_before,
        "current_pair_index_after": expected_completed_after,
        "active_pair_id_after": None if position == 2 else binding["pair_id"],
        "next_pair_member_position_after": 1 if position == 2 else 2,
        "completed_pair_count_after": expected_completed_after,
        "status_after": (
            "completed"
            if expected_completed_after == TARGET_COMPLETED_PAIRS
            else "active"
        ),
    }
    if transition != expected_transition:
        raise ExperimentValidationError(
            "experiment expected transition is internally inconsistent"
        )
    if binding["publication_sequence"] != expected_confirmed_before + 1:
        raise ExperimentValidationError(
            "experiment publication sequence differs from pair progress"
        )
    if plan is not None:
        if binding["plan_sha256"] != plan.get("plan_sha256"):
            raise ExperimentValidationError("attempt binding plan changed")
        pair = plan["pairs"][binding["pair_index"]]
        member = pair["members"][binding["member_position"] - 1]
        if (
            binding["pair_id"] != pair["pair_id"]
            or binding["arm"] != member["arm"]
            or binding["publication_order"] != pair["planned_publication_order"]
            or binding["approved_question_sha256"]
            != member["approved_question_sha256"]
            or binding["canonical_quote_sha256"] != member["quote_id"]
        ):
            raise ExperimentValidationError("attempt binding member changed")
    if exact_quote_text is not None and sha256_text(exact_quote_text) != binding[
        "canonical_quote_sha256"
    ]:
        raise ExperimentValidationError("attempt binding canonical quote changed")
    if public_text is not None and sha256_text(public_text) != binding[
        "public_text_sha256"
    ]:
        raise ExperimentValidationError("attempt binding public payload changed")
    if state_before is not None:
        if (
            len(state_before["confirmed_publications"])
            != transition["confirmed_publication_count_before"]
            or state_before["completed_pair_count"]
            != transition["completed_pair_count_before"]
            or state_before["treatment_publication_count"]
            != transition["treatment_publication_count_before"]
            or binding["publication_sequence"]
            != len(state_before["confirmed_publications"]) + 1
        ):
            raise ExperimentValidationError("attempt binding state precondition changed")
    return binding


def apply_confirmed_publication(
    state: dict[str, Any],
    *,
    binding: Mapping[str, Any],
    plan: Mapping[str, Any] | None,
    post_id: str,
    published_epoch: int,
    exact_quote_text: str,
    public_text: str,
    approved_question_body: str | None = None,
) -> bool:
    """Advance protected progress exactly once after confirmed publication.

    A current plan is used whenever it is available.  The pre-write receipt is
    deliberately self-contained, however, so an already-confirmed remote post
    can still be represented durably if the configured immutable plan becomes
    unavailable before local recovery completes.
    """

    validate_experiment_state(state, plan=plan)
    validate_attempt_binding(
        dict(binding),
        plan=plan,
        exact_quote_text=exact_quote_text,
        public_text=public_text,
    )
    if not POST_ID_RE.fullmatch(str(post_id)):
        raise ExperimentValidationError("confirmed experimental post ID is invalid")
    existing = next(
        (row for row in state["confirmed_publications"] if row["post_id"] == str(post_id)),
        None,
    )
    if existing is not None:
        expected_identity = {
            "pair_id": binding["pair_id"],
            "quote_id": binding["canonical_quote_sha256"],
            "arm": binding["arm"],
            "member_position": binding["member_position"],
            "publication_sequence": binding["publication_sequence"],
            "public_text_sha256": binding["public_text_sha256"],
        }
        if any(existing[field] != value for field, value in expected_identity.items()):
            raise ExperimentValidationError("confirmed post ID conflicts with prior progress")
        return False
    transition = binding["expected_transition"]
    validate_attempt_binding(
        dict(binding),
        plan=plan,
        exact_quote_text=exact_quote_text,
        public_text=public_text,
        state_before=state,
    )
    if (
        state["active_pair_id"] != binding["pair_id"]
        or state["current_pair_index"] != binding["pair_index"]
        or state["next_pair_member_position"] != binding["member_position"]
    ):
        raise ExperimentValidationError("confirmed experiment transition precondition changed")
    local_date = local_date_for_epoch(int(published_epoch))
    if binding["arm"] == "treatment" and any(
        row["arm"] == "treatment" and row["local_date"] == local_date
        for row in state["confirmed_publications"]
    ):
        raise ExperimentValidationError("two treatments would be confirmed on one local date")
    previous_pair_member = next(
        (
            row
            for row in reversed(state["confirmed_publications"])
            if row["pair_id"] == binding["pair_id"]
        ),
        None,
    )
    gap = (
        abs(int(published_epoch) - int(previous_pair_member["published_epoch"]))
        if previous_pair_member is not None
        else None
    )
    publication = {
        "post_id": str(post_id),
        "pair_id": str(binding["pair_id"]),
        "quote_id": str(binding["canonical_quote_sha256"]),
        "arm": str(binding["arm"]),
        "member_position": int(binding["member_position"]),
        "publication_order": str(binding["publication_order"]),
        "publication_sequence": int(binding["publication_sequence"]),
        "question_present": bool(binding["question_present"]),
        "approved_question_sha256": str(binding["approved_question_sha256"]),
        "public_text_sha256": str(binding["public_text_sha256"]),
        "published_epoch": int(published_epoch),
        "local_date": local_date,
        "pair_member_gap_seconds": gap,
    }
    state["confirmed_publications"].append(publication)
    state["last_experimental_publication_local_date"] = local_date
    state["treatment_publication_count"] += int(binding["arm"] == "treatment")
    state["current_pair_index"] = transition["current_pair_index_after"]
    state["active_pair_id"] = transition["active_pair_id_after"]
    state["next_pair_member_position"] = transition[
        "next_pair_member_position_after"
    ]
    state["completed_pair_count"] = transition["completed_pair_count_after"]
    state["status"] = transition["status_after"]
    state["current_deferral_reason"] = None
    if binding["arm"] == "treatment":
        question = (
            plan["pairs"][binding["pair_index"]]["members"][
                binding["member_position"] - 1
            ]["approved_question_body"]
            if plan is not None
            else approved_question_body
        )
        question = _validate_question_body(question)
        if sha256_text(question) != binding["approved_question_sha256"]:
            raise ExperimentValidationError(
                "receipt-bound approved question body changed"
            )
        notification = {
            "schema_version": NOTIFICATION_SCHEMA_VERSION,
            "experiment_id": EXPERIMENT_ID,
            "plan_sha256": str(binding["plan_sha256"]),
            "post_id": str(post_id),
            "pair_id": str(binding["pair_id"]),
            "treatment_number": int(state["treatment_publication_count"]),
            "target_treatment_count": TARGET_TREATMENT_COUNT,
            "published_epoch": int(published_epoch),
            "quote_excerpt": bounded_quote_excerpt(exact_quote_text),
            "question": str(question),
            "post_url": f"https://x.com/MrsMThatcher/status/{post_id}",
        }
        validate_notification_document(notification)
        state["latest_treatment_notification_identity"] = {
            "post_id": str(post_id),
            "document_sha256": canonical_sha256(notification),
            "delivered": False,
            "document": notification,
        }
    validate_experiment_state(state, plan=plan)
    return True


def mark_notification_delivered(state: dict[str, Any], post_id: str) -> bool:
    """Mark the latest idempotent notification observation as delivered."""

    validate_experiment_state(state)
    identity = state.get("latest_treatment_notification_identity")
    if not isinstance(identity, dict) or identity.get("post_id") != str(post_id):
        raise ExperimentValidationError("notification delivery identity changed")
    if identity["delivered"]:
        return False
    identity["delivered"] = True
    validate_experiment_state(state)
    return True


def experiment_status_summary(
    state: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None,
    *,
    plan_valid: bool,
    state_valid: bool,
) -> dict[str, Any]:
    """Return the bounded offline operator status view."""

    if not isinstance(state, Mapping):
        return {
            "experiment_status": "not_started",
            "active_plan_sha256": plan.get("plan_sha256") if isinstance(plan, Mapping) else None,
            "completed_pairs": 0,
            "target_completed_pairs": TARGET_COMPLETED_PAIRS,
            "control_posts_confirmed": 0,
            "treatment_posts_confirmed": 0,
            "current_active_pair": None,
            "next_pending_member": None,
            "last_experimental_publication": None,
            "latest_treatment_post": None,
            "current_reserved_quote_count": 0,
            "current_deferral_reason": None,
            "pairs_with_publication_gap_over_four_hours": [],
            "plan_valid": plan_valid,
            "state_valid": state_valid,
        }
    publications = list(state.get("confirmed_publications") or [])
    next_member = None
    if (
        isinstance(plan, Mapping)
        and state.get("active_pair_id") is not None
        and type(state.get("current_pair_index")) is int
        and type(state.get("next_pair_member_position")) is int
        and 0 <= state["current_pair_index"] < len(plan.get("pairs") or [])
    ):
        member = plan["pairs"][state["current_pair_index"]]["members"][
            state["next_pair_member_position"] - 1
        ]
        next_member = {
            "position": member["position"],
            "quote_id": member["quote_id"],
            "arm": member["arm"],
        }
    wide = sorted(
        {
            row["pair_id"]
            for row in publications
            if row.get("pair_member_gap_seconds") is not None
            and int(row["pair_member_gap_seconds"]) > PUBLICATION_GAP_LIMIT_SECONDS
        }
    )
    latest_treatment = next(
        (row for row in reversed(publications) if row.get("arm") == "treatment"),
        None,
    )
    reserved = (
        reserved_quote_ids(plan, state)
        if isinstance(plan, Mapping) and plan_valid and state_valid
        else set()
    )
    return {
        "experiment_status": state.get("status"),
        "active_plan_sha256": state.get("active_plan_sha256"),
        "completed_pairs": state.get("completed_pair_count", 0),
        "target_completed_pairs": TARGET_COMPLETED_PAIRS,
        "control_posts_confirmed": sum(row.get("arm") == "control" for row in publications),
        "treatment_posts_confirmed": sum(row.get("arm") == "treatment" for row in publications),
        "current_active_pair": state.get("active_pair_id"),
        "next_pending_member": next_member,
        "last_experimental_publication": publications[-1] if publications else None,
        "latest_treatment_post": latest_treatment,
        "current_reserved_quote_count": len(reserved),
        "current_deferral_reason": state.get("current_deferral_reason"),
        "pairs_with_publication_gap_over_four_hours": wide,
        "plan_valid": bool(plan_valid),
        "state_valid": bool(state_valid),
    }


def plan_selection_summary(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Return deterministic preview counts and length-difference statistics."""

    topics: dict[str, int] = defaultdict(int)
    bands: dict[str, int] = defaultdict(int)
    topic_bands: dict[str, int] = defaultdict(int)
    differences: list[int] = []
    order_counts: dict[str, int] = defaultdict(int)
    for pair in plan["pairs"]:
        topics[str(pair["topic"])] += 1
        bands[str(pair["quotation_length_band"])] += 1
        topic_bands[f"{pair['topic']}/{pair['quotation_length_band']}"] += 1
        differences.append(int(pair["matching"]["control_weighted_length_difference"]))
        order_counts[str(pair["planned_publication_order"])] += 1
    return {
        "pair_count": len(plan["pairs"]),
        "selected_quote_count": len(plan["pairs"]) * 2,
        "selected_pairs_by_topic": dict(sorted(topics.items())),
        "selected_pairs_by_length_band": dict(sorted(bands.items())),
        "selected_pairs_by_topic_and_length_band": dict(sorted(topic_bands.items())),
        "maximum_control_weighted_length_difference": max(differences, default=0),
        "median_control_weighted_length_difference": statistics.median(differences)
        if differences
        else 0,
        "publication_order_counts": dict(sorted(order_counts.items())),
        "arm_counts": {
            "control": len(plan["pairs"]),
            "treatment": len(plan["pairs"]),
        },
    }
