#!/usr/bin/env python3
"""Deterministic author-group exposure controls for proposition-ledger research.

This module operates only on pseudonymous, text-free metadata.  Within-family
groups are keyed by the complete ``(author_key_scheme, principal_author_key)``
identity domain tuple.  Cross-family keys can be derived only through the
explicit availability guard below; the authoritative raw identity is consumed
as HMAC input and is never retained in a returned value.

The module assigns no development or held-out split.  It reports the groups
which a later phase must keep intact and separates within-family preliminary
eligibility from the stronger cross-family-clean claim.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping, Sequence


OUTPUT_SCHEMA_VERSION = "proposition-ledger-author-group-exposure-v1"
WITHIN_FAMILY_KEY_PURPOSE = (
    "mrsMThatcher/proposition-ledger/phase1.2/"
    "within-family-author-group/v1"
)
CROSS_SOURCE_HMAC_PURPOSE = (
    "mrsMThatcher/proposition-ledger/phase1.2/"
    "cross-source-author-group/v1"
)
CROSS_SOURCE_HMAC_KEY_BYTES = 32

DIRECT_EXPOSURE_CATEGORIES = frozenset(
    {
        "calibration",
        "current_manual_incident_review",
        "development_labelled",
        "development_unlabelled_but_seen",
        "human_reviewed",
        "manual_incident_review",
        "otherwise_directly_exposed",
        "prior_human_review",
        "prior_model_experiment",
        "report_example",
        "report_excerpt",
    }
)
STRUCTURAL_EXPOSURE_CATEGORY = "structurally_mined_only"
UNEXPOSED_CATEGORY = "unexposed_candidate"
DIRECT_EXPOSURE_STATUSES = frozenset({"exposed", "directly_exposed"})
STRUCTURAL_EXPOSURE_STATUSES = frozenset(
    {"structurally_mined_only", "structurally_mined"}
)
UNEXPOSED_STATUSES = frozenset(
    {"genuinely_unexposed", "unexposed_candidate", "unexposed"}
)

EXPOSURE_CATEGORY_FIELDS = (
    "prior_exposure_categories",
    "exposure_categories",
    "conversation_exposure_categories",
    "target_exposure_categories",
)
EXPOSURE_REASON_FIELDS = (
    "prior_exposure_reasons",
    "exposure_reasons",
    "conversation_exposure_reasons",
    "target_exposure_reasons",
)
EXPOSURE_STATUS_FIELDS = (
    "prior_exposure_status",
    "conversation_exposure_status",
    "target_exposure_status",
    "effective_exposure_status",
)

RAW_ID_FIELD_NAMES = frozenset(
    {
        "author_id",
        "author_name",
        "author_username",
        "contributor_id",
        "raw_author_id",
        "raw_contributor_id",
        "raw_identity",
        "screen_name",
        "user_id",
        "username",
    }
)

STABLE_CONVERSATION_STATUSES = frozenset(
    {"frozen_historical", "quiescent_at_frozen_cutoff"}
)

WITHIN_ELIGIBILITY_REASON_ORDER = (
    "reconstruction_grade_not_a",
    "conversation_not_frozen_or_quiescent",
    "not_persistent_multiturn_target",
    "conversation_not_genuinely_unexposed",
    "target_not_genuinely_unexposed",
    "within_family_identity_group_unavailable",
    "within_family_identity_group_conflicting",
    "direct_exposure_elsewhere_in_within_family_author_group",
    "target_structural_conflict",
    "outcome_evidence_conflict",
)
CROSS_ELIGIBILITY_REASON_ORDER = (
    *WITHIN_ELIGIBILITY_REASON_ORDER,
    "cross_family_author_identity_unavailable",
    "cross_family_author_identity_conflicting",
    "direct_exposure_elsewhere_in_cross_family_author_group",
)


class AuthorGroupError(ValueError):
    """Report invalid pseudonymous author-group metadata."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _nonempty_identity_component(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def domain_qualified_within_family_key(
    author_key_scheme: Any,
    principal_author_key: Any,
) -> str | None:
    """Return an opaque key derived from the complete identity-domain tuple.

    Neither source family nor any name-like metadata participates.  A missing
    component makes grouping unavailable rather than creating a partial key.
    Length-prefixed canonical JSON prevents delimiter ambiguity.
    """

    scheme = _nonempty_identity_component(author_key_scheme)
    principal = _nonempty_identity_component(principal_author_key)
    if scheme is None or principal is None:
        return None
    material = {
        "author_key_scheme": scheme,
        "principal_author_key": principal,
        "purpose": WITHIN_FAMILY_KEY_PURPOSE,
    }
    digest = hashlib.sha256(_canonical_json_bytes(material)).hexdigest()
    return f"within-family-author-group-sha256-v1-{digest}"


def derive_cross_family_author_identity(
    hmac_key: bytes,
    authoritative_raw_identity: str | bytes | None,
    *,
    authoritative_identity_available_in_both_families: bool,
    identity_conflicting: bool = False,
) -> dict[str, Any]:
    """Return a guarded cross-family identity descriptor.

    ``authoritative_identity_available_in_both_families`` must be explicitly
    true before a key can be derived.  This prevents a raw identity available
    in just one frozen source family from creating a false common domain.  The
    HMAC message contains the explicit purpose and raw identity bytes only; it
    deliberately has no source-family component.
    """

    if identity_conflicting:
        return {
            "cross_family_author_group_key": None,
            "cross_family_author_identity_status": "conflicting",
            "cross_family_author_identity_reasons": [
                "authoritative_cross_family_identity_conflicting"
            ],
        }
    if not authoritative_identity_available_in_both_families:
        return {
            "cross_family_author_group_key": None,
            "cross_family_author_identity_status": "unavailable",
            "cross_family_author_identity_reasons": [
                "authoritative_raw_identity_unavailable_in_both_source_families"
            ],
        }
    if authoritative_raw_identity is None:
        return {
            "cross_family_author_group_key": None,
            "cross_family_author_identity_status": "unavailable",
            "cross_family_author_identity_reasons": [
                "authoritative_raw_identity_missing"
            ],
        }
    if not isinstance(hmac_key, bytes) or len(hmac_key) != CROSS_SOURCE_HMAC_KEY_BYTES:
        raise AuthorGroupError("cross-family HMAC key must contain exactly 32 bytes")
    if isinstance(authoritative_raw_identity, str):
        raw_identity_bytes = authoritative_raw_identity.encode("utf-8")
    elif isinstance(authoritative_raw_identity, bytes):
        raw_identity_bytes = authoritative_raw_identity
    else:
        raise AuthorGroupError("authoritative raw identity must be text or bytes")
    if not raw_identity_bytes:
        return {
            "cross_family_author_group_key": None,
            "cross_family_author_identity_status": "unavailable",
            "cross_family_author_identity_reasons": [
                "authoritative_raw_identity_missing"
            ],
        }

    purpose = CROSS_SOURCE_HMAC_PURPOSE.encode("utf-8")
    message = (
        len(purpose).to_bytes(4, "big")
        + purpose
        + len(raw_identity_bytes).to_bytes(8, "big")
        + raw_identity_bytes
    )
    digest = hmac.new(hmac_key, message, hashlib.sha256).hexdigest()
    return {
        "cross_family_author_group_key": (
            f"cross-family-author-group-hmac-sha256-v1-{digest}"
        ),
        "cross_family_author_identity_status": "available",
        "cross_family_author_identity_reasons": [],
    }


def _iter_values(value: Any) -> Iterable[str]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set, frozenset)):
        return (str(item) for item in value if item not in (None, ""))
    raise AuthorGroupError("exposure categories and reasons must be strings or arrays")


def _bounded_strings(values: Iterable[str]) -> list[str]:
    return sorted({str(value).replace("\n", " ")[:256] for value in values})[:64]


def _forbidden_raw_identity_paths(value: Any, path: str = "$") -> list[str]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            name = str(key)
            child_path = f"{path}.{name}"
            if name in RAW_ID_FIELD_NAMES:
                paths.append(child_path)
            paths.extend(_forbidden_raw_identity_paths(child, child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            paths.extend(_forbidden_raw_identity_paths(child, f"{path}[{index}]"))
    return paths


def _copied_rows(rows: Sequence[Mapping[str, Any]], label: str) -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise AuthorGroupError(f"{label}[{index}] is not an object")
        forbidden = _forbidden_raw_identity_paths(row)
        if forbidden:
            raise AuthorGroupError(
                f"{label}[{index}] contains forbidden raw identity field: {forbidden[0]}"
            )
        conversation_key = row.get("conversation_key")
        if not isinstance(conversation_key, str) or not conversation_key:
            raise AuthorGroupError(f"{label}[{index}] has no conversation_key")
        copied.append(copy.deepcopy(dict(row)))
    return copied


def _exposure_facts(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    categories: set[str] = set()
    reasons: set[str] = set()
    statuses: set[str] = set()
    for row in rows:
        for field in EXPOSURE_CATEGORY_FIELDS:
            categories.update(_iter_values(row.get(field)))
        for field in EXPOSURE_REASON_FIELDS:
            reasons.update(_iter_values(row.get(field)))
        for field in EXPOSURE_STATUS_FIELDS:
            status = row.get(field)
            if status not in (None, ""):
                statuses.add(str(status))

    direct_categories = categories & DIRECT_EXPOSURE_CATEGORIES
    contains_direct = bool(direct_categories or statuses & DIRECT_EXPOSURE_STATUSES)
    contains_structural = bool(
        STRUCTURAL_EXPOSURE_CATEGORY in categories
        or statuses & STRUCTURAL_EXPOSURE_STATUSES
    )
    contains_unexposed = bool(
        UNEXPOSED_CATEGORY in categories or statuses & UNEXPOSED_STATUSES
    )
    if contains_direct and not direct_categories:
        categories.add("direct_exposure_unspecified")
        direct_categories.add("direct_exposure_unspecified")
    reasons.update(
        f"direct_exposure_category:{category}"
        for category in direct_categories
    )
    if contains_structural:
        reasons.add("structurally_mined_material_present")
    if contains_unexposed and not contains_direct and not contains_structural:
        reasons.add("all_observed_material_genuinely_unexposed")
    return {
        "categories": _bounded_strings(categories),
        "reasons": _bounded_strings(reasons),
        "contains_direct": contains_direct,
        "contains_structural": contains_structural,
        "contains_unexposed": contains_unexposed,
    }


def _group_exposure_status(facts: Mapping[str, Any]) -> str:
    if facts["contains_direct"]:
        return "contains_direct_exposure"
    if facts["contains_structural"] and facts["contains_unexposed"]:
        return "mixed_unexposed_and_structurally_mined"
    if facts["contains_structural"]:
        return "contains_structurally_mined_material"
    if facts["contains_unexposed"]:
        return "clean_genuinely_unexposed_group"
    return "exposure_group_unknown"


def _identity_assignments(
    rows_by_conversation: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, Any]]:
    assignments: dict[str, dict[str, Any]] = {}
    for conversation_key, rows in rows_by_conversation.items():
        pairs: set[tuple[str, str]] = set()
        explicit_conflict = False
        for row in rows:
            scheme = _nonempty_identity_component(row.get("author_key_scheme"))
            principal = _nonempty_identity_component(row.get("principal_author_key"))
            if scheme is not None and principal is not None:
                pairs.add((scheme, principal))
            if row.get("author_group_comparability_status") in {
                "identity_group_conflicting",
                "conflicting",
            } or row.get("within_family_author_identity_status") == "conflicting":
                explicit_conflict = True
        if explicit_conflict or len(pairs) > 1:
            assignments[conversation_key] = {
                "status": "conflicting",
                "key": None,
            }
        elif not pairs:
            assignments[conversation_key] = {
                "status": "unavailable",
                "key": None,
            }
        else:
            scheme, principal = next(iter(pairs))
            assignments[conversation_key] = {
                "status": "available",
                "key": domain_qualified_within_family_key(scheme, principal),
            }
    return assignments


def _cross_identity_assignments(
    rows_by_conversation: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, Any]]:
    assignments: dict[str, dict[str, Any]] = {}
    for conversation_key, rows in rows_by_conversation.items():
        keys: set[str] = set()
        statuses: set[str] = set()
        identity_reasons: set[str] = set()
        for row in rows:
            key = row.get("cross_family_author_group_key")
            if isinstance(key, str) and key:
                keys.add(key)
            status = row.get("cross_family_author_identity_status")
            if status not in (None, ""):
                statuses.add(str(status))
            identity_reasons.update(
                _iter_values(row.get("cross_family_author_identity_reasons"))
            )

        conflicting = (
            "conflicting" in statuses
            or len(keys) > 1
            or ({"available", "unavailable"} <= statuses)
            or (bool(keys) and statuses == {"unavailable"})
            or ("available" in statuses and not keys)
        )
        if conflicting:
            assignments[conversation_key] = {
                "status": "conflicting",
                "key": None,
                "reasons": _bounded_strings(
                    [
                        *identity_reasons,
                        "cross_family_identity_metadata_conflicting",
                    ]
                ),
            }
        elif keys:
            assignments[conversation_key] = {
                "status": "available",
                "key": next(iter(keys)),
                "reasons": _bounded_strings(identity_reasons),
            }
        else:
            assignments[conversation_key] = {
                "status": "unavailable",
                "key": None,
                "reasons": _bounded_strings(
                    [
                        *identity_reasons,
                        "cross_family_author_identity_unavailable",
                    ]
                ),
            }
    return assignments


def _group_summary(
    *,
    group_key: str,
    conversation_keys: Sequence[str],
    rows_by_conversation: Mapping[str, Sequence[Mapping[str, Any]]],
    target_counts: Mapping[str, int],
    cross_family: bool,
) -> dict[str, Any]:
    observations = [
        row
        for conversation_key in conversation_keys
        for row in rows_by_conversation[conversation_key]
    ]
    facts = _exposure_facts(observations)
    status = _group_exposure_status(facts)
    conversation_count = len(conversation_keys)
    target_count = sum(target_counts.get(key, 0) for key in conversation_keys)
    requires_groupwise_split = bool(
        facts["contains_structural"] or conversation_count > 1
    )
    if cross_family:
        return {
            "cross_family_author_group_key": group_key,
            "cross_family_author_identity_status": "available",
            "cross_family_author_group_exposure_status": status,
            "cross_family_author_group_exposure_categories": facts["categories"],
            "cross_family_author_group_exposure_reasons": facts["reasons"],
            "cross_family_author_group_conversation_count": conversation_count,
            "cross_family_author_group_target_count": target_count,
            "cross_family_author_group_contains_directly_exposed_material": facts[
                "contains_direct"
            ],
            "cross_family_author_group_contains_structurally_mined_material": facts[
                "contains_structural"
            ],
            "cross_family_author_group_contains_genuinely_unexposed_material": facts[
                "contains_unexposed"
            ],
            "cross_family_author_group_requires_groupwise_split": (
                requires_groupwise_split
            ),
            "cross_family_author_group_split_assignment": None,
        }
    return {
        "within_family_author_group_key": group_key,
        "author_group_exposure_status": status,
        "author_group_exposure_categories": facts["categories"],
        "author_group_exposure_reasons": facts["reasons"],
        "author_group_conversation_count": conversation_count,
        "author_group_target_count": target_count,
        "author_group_contains_directly_exposed_material": facts["contains_direct"],
        "author_group_contains_structurally_mined_material": facts[
            "contains_structural"
        ],
        "author_group_contains_genuinely_unexposed_material": facts[
            "contains_unexposed"
        ],
        "author_group_requires_groupwise_split": requires_groupwise_split,
        "author_group_split_assignment": None,
    }


def _unavailable_group_annotation(
    *,
    conversation_key: str,
    rows: Sequence[Mapping[str, Any]],
    target_count: int,
    identity_status: str,
) -> dict[str, Any]:
    facts = _exposure_facts(rows)
    status = (
        "identity_group_conflicting"
        if identity_status == "conflicting"
        else "identity_group_unavailable"
    )
    reasons = set(facts["reasons"])
    reasons.add(
        "within_family_identity_group_conflicting"
        if identity_status == "conflicting"
        else "within_family_identity_group_unavailable"
    )
    return {
        "within_family_author_group_key": None,
        "author_group_exposure_status": status,
        "author_group_exposure_categories": facts["categories"],
        "author_group_exposure_reasons": _bounded_strings(reasons),
        "author_group_conversation_count": 1,
        "author_group_target_count": target_count,
        "author_group_contains_directly_exposed_material": facts["contains_direct"],
        "author_group_contains_structurally_mined_material": facts[
            "contains_structural"
        ],
        "author_group_contains_genuinely_unexposed_material": facts[
            "contains_unexposed"
        ],
        "author_group_requires_groupwise_split": bool(facts["contains_structural"]),
        "author_group_split_assignment": None,
        "_conversation_key": conversation_key,
    }


def _unavailable_cross_group_annotation(
    *,
    rows: Sequence[Mapping[str, Any]],
    target_count: int,
    assignment: Mapping[str, Any],
) -> dict[str, Any]:
    facts = _exposure_facts(rows)
    identity_status = str(assignment["status"])
    status = (
        "identity_group_conflicting"
        if identity_status == "conflicting"
        else "identity_group_unavailable"
    )
    reasons = set(facts["reasons"])
    reasons.update(str(value) for value in assignment.get("reasons", []))
    return {
        "cross_family_author_group_key": None,
        "cross_family_author_identity_status": identity_status,
        "cross_family_author_group_exposure_status": status,
        "cross_family_author_group_exposure_categories": facts["categories"],
        "cross_family_author_group_exposure_reasons": _bounded_strings(reasons),
        "cross_family_author_group_conversation_count": 1,
        "cross_family_author_group_target_count": target_count,
        "cross_family_author_group_contains_directly_exposed_material": facts[
            "contains_direct"
        ],
        "cross_family_author_group_contains_structurally_mined_material": facts[
            "contains_structural"
        ],
        "cross_family_author_group_contains_genuinely_unexposed_material": facts[
            "contains_unexposed"
        ],
        "cross_family_author_group_requires_groupwise_split": bool(
            facts["contains_structural"]
        ),
        "cross_family_author_group_split_assignment": None,
    }


def _ordered_reasons(reasons: Iterable[str], order: Sequence[str]) -> list[str]:
    values = set(reasons)
    rank = {reason: index for index, reason in enumerate(order)}
    return sorted(values, key=lambda reason: (rank.get(reason, len(rank)), reason))


def evaluate_preliminary_eligibility(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return separate within-family and cross-family-clean eligibility fields."""

    within_reasons: list[str] = []
    if row.get("reconstruction_grade") != "A":
        within_reasons.append("reconstruction_grade_not_a")
    if row.get("stability_status") not in STABLE_CONVERSATION_STATUSES:
        within_reasons.append("conversation_not_frozen_or_quiescent")
    persistent = (
        row.get("target_sequence_class") == "persistent_multiturn_target"
        if "target_sequence_class" in row
        else row.get("persistent_multiturn_evaluation_candidate") is True
    )
    if not persistent:
        within_reasons.append("not_persistent_multiturn_target")

    effective = row.get("effective_exposure_status")
    conversation_exposure = row.get("conversation_exposure_status", effective)
    target_exposure = row.get("target_exposure_status", effective)
    if conversation_exposure not in UNEXPOSED_STATUSES:
        within_reasons.append("conversation_not_genuinely_unexposed")
    if target_exposure not in UNEXPOSED_STATUSES:
        within_reasons.append("target_not_genuinely_unexposed")

    group_status = row.get("author_group_exposure_status")
    if group_status == "identity_group_unavailable" or not row.get(
        "within_family_author_group_key"
    ):
        within_reasons.append("within_family_identity_group_unavailable")
    if group_status == "identity_group_conflicting":
        within_reasons = [
            reason
            for reason in within_reasons
            if reason != "within_family_identity_group_unavailable"
        ]
        within_reasons.append("within_family_identity_group_conflicting")
    if row.get("author_group_contains_directly_exposed_material") is True:
        within_reasons.append(
            "direct_exposure_elsewhere_in_within_family_author_group"
        )
    if (
        row.get("complete_target_ancestry") is False
        or row.get("structural_conflict") is True
        or bool(row.get("structural_exclusion_reasons"))
    ):
        within_reasons.append("target_structural_conflict")
    if (
        row.get("outcome_conflict") is True
        or row.get("outcome_evidence_class") == "conflicting_outcome_evidence"
    ):
        within_reasons.append("outcome_evidence_conflict")

    within_reasons = _ordered_reasons(
        within_reasons, WITHIN_ELIGIBILITY_REASON_ORDER
    )
    cross_reasons = list(within_reasons)
    cross_status = row.get("cross_family_author_identity_status")
    if cross_status == "conflicting":
        cross_reasons.append("cross_family_author_identity_conflicting")
    elif cross_status != "available" or not row.get("cross_family_author_group_key"):
        cross_reasons.append("cross_family_author_identity_unavailable")
    if row.get("cross_family_author_group_contains_directly_exposed_material") is True:
        cross_reasons.append("direct_exposure_elsewhere_in_cross_family_author_group")
    cross_reasons = _ordered_reasons(
        cross_reasons, CROSS_ELIGIBILITY_REASON_ORDER
    )

    return {
        "preliminary_within_family_held_out_eligibility": not within_reasons,
        "preliminary_within_family_held_out_exclusion_reasons": within_reasons,
        "preliminary_cross_family_clean_held_out_eligibility": not cross_reasons,
        "preliminary_cross_family_clean_held_out_eligibility_status": (
            "eligible"
            if not cross_reasons
            else "pending_cross_family_identity_unavailable"
            if cross_reasons == ["cross_family_author_identity_unavailable"]
            else "ineligible"
        ),
        "preliminary_cross_family_clean_held_out_exclusion_reasons": cross_reasons,
    }


def build_author_group_crosstab(
    target_rows: Sequence[Mapping[str, Any]],
    conversation_rows: Sequence[Mapping[str, Any]],
    within_family_groups: Sequence[Mapping[str, Any]],
    cross_family_groups: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build an exact group/row crosstab without selecting any split."""

    def counts(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
        return dict(sorted(Counter(str(row.get(field)) for row in rows).items()))

    dimensions = {
        "target_author_group_exposure_status": counts(
            target_rows, "author_group_exposure_status"
        ),
        "conversation_author_group_exposure_status": counts(
            conversation_rows, "author_group_exposure_status"
        ),
        "within_family_group_exposure_status": counts(
            within_family_groups, "author_group_exposure_status"
        ),
        "target_cross_family_author_identity_status": counts(
            target_rows, "cross_family_author_identity_status"
        ),
        "conversation_cross_family_author_identity_status": counts(
            conversation_rows, "cross_family_author_identity_status"
        ),
        "cross_family_group_exposure_status": counts(
            cross_family_groups, "cross_family_author_group_exposure_status"
        ),
        "preliminary_within_family_held_out_eligibility": counts(
            target_rows, "preliminary_within_family_held_out_eligibility"
        ),
        "preliminary_cross_family_clean_held_out_eligibility": counts(
            target_rows,
            "preliminary_cross_family_clean_held_out_eligibility",
        ),
    }
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "target_count": len(target_rows),
        "conversation_count": len(conversation_rows),
        "comparable_within_family_author_group_count": len(within_family_groups),
        "comparable_cross_family_author_group_count": len(cross_family_groups),
        "dimensions": dimensions,
        "headline_counts": {
            "within_family_groups_containing_direct_exposure": sum(
                row.get("author_group_contains_directly_exposed_material") is True
                for row in within_family_groups
            ),
            "within_family_groups_requiring_groupwise_split": sum(
                row.get("author_group_requires_groupwise_split") is True
                for row in within_family_groups
            ),
            "within_family_eligible_target_count": sum(
                row.get("preliminary_within_family_held_out_eligibility") is True
                for row in target_rows
            ),
            "cross_family_clean_eligible_target_count": sum(
                row.get("preliminary_cross_family_clean_held_out_eligibility") is True
                for row in target_rows
            ),
            "cross_family_identity_available_conversation_count": sum(
                row.get("cross_family_author_identity_status") == "available"
                for row in conversation_rows
            ),
            "cross_family_identity_unavailable_conversation_count": sum(
                row.get("cross_family_author_identity_status") == "unavailable"
                for row in conversation_rows
            ),
            "cross_family_identity_conflicting_conversation_count": sum(
                row.get("cross_family_author_identity_status") == "conflicting"
                for row in conversation_rows
            ),
        },
        "author_group_split_performed": False,
    }


def author_group_crosstab_errors(
    target_rows: Sequence[Mapping[str, Any]],
    conversation_rows: Sequence[Mapping[str, Any]],
    within_family_groups: Sequence[Mapping[str, Any]],
    cross_family_groups: Sequence[Mapping[str, Any]],
    crosstab: Mapping[str, Any],
) -> list[str]:
    """Return deterministic reconciliation errors for an author-group crosstab."""

    expected = build_author_group_crosstab(
        target_rows,
        conversation_rows,
        within_family_groups,
        cross_family_groups,
    )
    errors: list[str] = []
    if _canonical_json_bytes(expected) != _canonical_json_bytes(crosstab):
        errors.append("author_group_crosstab_does_not_reproduce_rows")
    dimensions = crosstab.get("dimensions", {})
    totals = (
        ("target_author_group_exposure_status", len(target_rows)),
        ("conversation_author_group_exposure_status", len(conversation_rows)),
        ("within_family_group_exposure_status", len(within_family_groups)),
        ("target_cross_family_author_identity_status", len(target_rows)),
        ("conversation_cross_family_author_identity_status", len(conversation_rows)),
        ("cross_family_group_exposure_status", len(cross_family_groups)),
    )
    for field, expected_total in totals:
        actual = sum(int(value) for value in dimensions.get(field, {}).values())
        if actual != expected_total:
            errors.append(f"{field}_total_mismatch")
    if crosstab.get("author_group_split_performed") is not False:
        errors.append("author_group_split_was_selected")
    return errors


def apply_author_group_exposure(
    target_rows: Sequence[Mapping[str, Any]],
    conversation_rows: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Aggregate author exposure, annotate targets, and return exact crosstabs.

    Conversation rows should include every frozen canonical conversation, even
    if it contributes no structurally usable target.  This is what lets direct
    exposure in a sibling conversation disqualify an otherwise clean target.
    If the sequence is omitted, target rows form the complete observation
    universe for small synthetic callers.
    """

    targets = _copied_rows(target_rows, "target_rows")
    conversations = _copied_rows(conversation_rows, "conversation_rows")
    conversation_keys = [str(row["conversation_key"]) for row in conversations]
    if len(conversation_keys) != len(set(conversation_keys)):
        raise AuthorGroupError("conversation_rows contains duplicate conversation_key")

    conversation_by_key = {
        str(row["conversation_key"]): row for row in conversations
    }
    for target in targets:
        key = str(target["conversation_key"])
        if key not in conversation_by_key:
            identity_and_exposure = {
                field: copy.deepcopy(target[field])
                for field in (
                    "author_key_scheme",
                    "principal_author_key",
                    "cross_family_author_group_key",
                    "cross_family_author_identity_status",
                    "cross_family_author_identity_reasons",
                    *EXPOSURE_CATEGORY_FIELDS,
                    *EXPOSURE_REASON_FIELDS,
                    *EXPOSURE_STATUS_FIELDS,
                )
                if field in target
            }
            conversation_by_key[key] = {
                "conversation_key": key,
                **identity_and_exposure,
            }
    conversations = [conversation_by_key[key] for key in sorted(conversation_by_key)]

    rows_by_conversation: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for conversation in conversations:
        rows_by_conversation[str(conversation["conversation_key"])].append(conversation)
    target_counts: Counter[str] = Counter()
    for target in targets:
        key = str(target["conversation_key"])
        rows_by_conversation[key].append(target)
        target_counts[key] += 1

    within_assignments = _identity_assignments(rows_by_conversation)
    cross_assignments = _cross_identity_assignments(rows_by_conversation)

    within_members: dict[str, list[str]] = defaultdict(list)
    cross_members: dict[str, list[str]] = defaultdict(list)
    for conversation_key in sorted(rows_by_conversation):
        within = within_assignments[conversation_key]
        cross = cross_assignments[conversation_key]
        if within["status"] == "available":
            within_members[str(within["key"])].append(conversation_key)
        if cross["status"] == "available":
            cross_members[str(cross["key"])].append(conversation_key)

    within_summaries = [
        _group_summary(
            group_key=group_key,
            conversation_keys=conversation_keys_for_group,
            rows_by_conversation=rows_by_conversation,
            target_counts=target_counts,
            cross_family=False,
        )
        for group_key, conversation_keys_for_group in sorted(within_members.items())
    ]
    cross_summaries = [
        _group_summary(
            group_key=group_key,
            conversation_keys=conversation_keys_for_group,
            rows_by_conversation=rows_by_conversation,
            target_counts=target_counts,
            cross_family=True,
        )
        for group_key, conversation_keys_for_group in sorted(cross_members.items())
    ]
    within_summary_by_key = {
        str(row["within_family_author_group_key"]): row
        for row in within_summaries
    }
    cross_summary_by_key = {
        str(row["cross_family_author_group_key"]): row for row in cross_summaries
    }

    within_annotations: dict[str, dict[str, Any]] = {}
    cross_annotations: dict[str, dict[str, Any]] = {}
    for conversation_key, rows in rows_by_conversation.items():
        within_assignment = within_assignments[conversation_key]
        if within_assignment["status"] == "available":
            within_annotations[conversation_key] = within_summary_by_key[
                str(within_assignment["key"])
            ]
        else:
            within_annotations[conversation_key] = _unavailable_group_annotation(
                conversation_key=conversation_key,
                rows=rows,
                target_count=target_counts[conversation_key],
                identity_status=str(within_assignment["status"]),
            )

        cross_assignment = cross_assignments[conversation_key]
        if cross_assignment["status"] == "available":
            cross_annotations[conversation_key] = cross_summary_by_key[
                str(cross_assignment["key"])
            ]
        else:
            cross_annotations[conversation_key] = _unavailable_cross_group_annotation(
                rows=rows,
                target_count=target_counts[conversation_key],
                assignment=cross_assignment,
            )

    def annotate(row: dict[str, Any], *, eligibility: bool) -> dict[str, Any]:
        conversation_key = str(row["conversation_key"])
        result = copy.deepcopy(row)
        within = {
            key: copy.deepcopy(value)
            for key, value in within_annotations[conversation_key].items()
            if key != "_conversation_key"
        }
        result.update(within)
        result.update(copy.deepcopy(cross_annotations[conversation_key]))
        if eligibility:
            result.update(evaluate_preliminary_eligibility(result))
        return result

    annotated_conversations = [
        annotate(row, eligibility=False) for row in conversations
    ]
    annotated_targets = [annotate(row, eligibility=True) for row in targets]
    crosstab = build_author_group_crosstab(
        annotated_targets,
        annotated_conversations,
        within_summaries,
        cross_summaries,
    )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "target_rows": annotated_targets,
        "conversation_rows": annotated_conversations,
        "within_family_author_groups": within_summaries,
        "cross_family_author_groups": cross_summaries,
        "crosstab": crosstab,
        "author_group_split_performed": False,
    }
