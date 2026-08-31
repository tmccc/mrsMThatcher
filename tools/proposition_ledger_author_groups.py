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


OUTPUT_SCHEMA_VERSION = "proposition-ledger-author-group-exposure-v2"
WITHIN_FAMILY_KEY_PURPOSE = (
    "mrsMThatcher/proposition-ledger/phase1.2/"
    "within-family-author-group/v1"
)
CONTRIBUTOR_OBSERVATION_KEY_PURPOSE = (
    "mrsMThatcher/proposition-ledger/phase1.2/"
    "contributor-exposure-observation/v1"
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
    "contributor_identity_binding_reasons",
)
EXPOSURE_STATUS_FIELDS = (
    "prior_exposure_status",
    "conversation_exposure_status",
    "target_exposure_status",
    "effective_exposure_status",
)
AUTHOR_GROUP_EXPOSURE_STATUS_FIELDS = (
    "author_group_conversation_exposure_status",
    "author_group_target_exposure_status",
    "author_group_effective_exposure_status",
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
    "target_author_identity_unavailable",
    "target_author_identity_conflicting",
    "within_family_identity_group_unavailable",
    "within_family_identity_group_conflicting",
    "scoped_exposure_contributor_unresolved",
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
        status_fields = (
            AUTHOR_GROUP_EXPOSURE_STATUS_FIELDS
            if any(field in row for field in AUTHOR_GROUP_EXPOSURE_STATUS_FIELDS)
            else EXPOSURE_STATUS_FIELDS
        )
        for field in status_fields:
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


def _within_identity_assignment(
    row: Mapping[str, Any], *, target: bool
) -> dict[str, Any]:
    """Resolve one row without consulting another row in its conversation."""

    status_field = (
        "target_author_identity_status"
        if target
        else "within_family_author_identity_status"
    )
    explicit_status = row.get(status_field)
    comparability = row.get("author_group_comparability_status")
    contributor_binding_status = (
        None if target else row.get("contributor_identity_binding_status")
    )
    if (
        contributor_binding_status == "conflicting"
        or explicit_status in {"conflicting", "identity_group_conflicting"}
        or comparability in {"conflicting", "identity_group_conflicting"}
    ):
        return {"status": "conflicting", "key": None}
    if explicit_status in {"unavailable", "identity_group_unavailable"}:
        return {"status": "unavailable", "key": None}

    scheme = _nonempty_identity_component(row.get("author_key_scheme"))
    principal = _nonempty_identity_component(row.get("principal_author_key"))
    if scheme is None or principal is None:
        return {"status": "unavailable", "key": None}
    return {
        "status": "available",
        "key": domain_qualified_within_family_key(scheme, principal),
        "scheme": scheme,
        "principal": principal,
    }


def _cross_identity_assignment(
    row: Mapping[str, Any], within_assignment: Mapping[str, Any]
) -> dict[str, Any]:
    """Resolve one row's guarded cross-family descriptor independently."""

    reasons = set(_iter_values(row.get("cross_family_author_identity_reasons")))
    if within_assignment.get("status") == "conflicting":
        reasons.add("target_or_contributor_identity_conflicting")
        return {
            "status": "conflicting",
            "key": None,
            "reasons": _bounded_strings(reasons),
        }
    if within_assignment.get("status") != "available":
        reasons.add("cross_family_author_identity_unavailable")
        return {
            "status": "unavailable",
            "key": None,
            "reasons": _bounded_strings(reasons),
        }

    key = row.get("cross_family_author_group_key")
    key = key if isinstance(key, str) and key else None
    status = row.get("cross_family_author_identity_status")
    status = str(status) if status not in (None, "") else None
    conflicting = (
        status == "conflicting"
        or (key is not None and status == "unavailable")
        or (status == "available" and key is None)
    )
    if conflicting:
        reasons.add("cross_family_identity_metadata_conflicting")
        return {
            "status": "conflicting",
            "key": None,
            "reasons": _bounded_strings(reasons),
        }
    if key is not None:
        return {
            "status": "available",
            "key": key,
            "reasons": _bounded_strings(reasons),
        }
    reasons.add("cross_family_author_identity_unavailable")
    return {
        "status": "unavailable",
        "key": None,
        "reasons": _bounded_strings(reasons),
    }


def _legacy_contributor_observations(
    conversation_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Derive a single-author compatibility view when no view was supplied.

    This fallback carries conversation exposure only.  It is never consulted to
    determine a target's author; production multi-author callers must provide
    observations enumerated from their canonical user turns.
    """

    fields = (
        "author_key_scheme",
        "principal_author_key",
        "cross_family_author_group_key",
        "cross_family_author_identity_status",
        "cross_family_author_identity_reasons",
        *EXPOSURE_CATEGORY_FIELDS,
        *EXPOSURE_REASON_FIELDS,
        *EXPOSURE_STATUS_FIELDS,
    )
    observations: list[dict[str, Any]] = []
    for conversation in conversation_rows:
        scheme = _nonempty_identity_component(conversation.get("author_key_scheme"))
        principal = _nonempty_identity_component(
            conversation.get("principal_author_key")
        )
        if scheme is None or principal is None:
            continue
        observation = {
            "conversation_key": str(conversation["conversation_key"]),
            "contributor_identity_binding_status": "available",
            "contributor_identity_binding_reasons": [],
        }
        observation.update(
            {
                field: copy.deepcopy(conversation[field])
                for field in fields
                if field in conversation
            }
        )
        observations.append(observation)
    return observations


def _observation_key(
    row: Mapping[str, Any], assignment: Mapping[str, Any]
) -> str:
    material: dict[str, Any] = {
        "purpose": CONTRIBUTOR_OBSERVATION_KEY_PURPOSE,
        "conversation_key": str(row["conversation_key"]),
    }
    if assignment.get("status") == "available":
        material.update(
            {
                "author_key_scheme": assignment["scheme"],
                "principal_author_key": assignment["principal"],
            }
        )
    else:
        material["unresolved_observation"] = {
            key: value
            for key, value in row.items()
            if key
            not in {
                "contributor_exposure_observation_key",
                "row_sha256",
            }
        }
    return (
        "contributor-exposure-observation-sha256-v1-"
        + hashlib.sha256(_canonical_json_bytes(material)).hexdigest()
    )


def _group_summary(
    *,
    group_key: str,
    contributor_rows: Sequence[Mapping[str, Any]],
    target_rows: Sequence[Mapping[str, Any]],
    cross_family: bool,
    unresolved_scoped_exposure: bool,
) -> dict[str, Any]:
    observations = [*contributor_rows, *target_rows]
    facts = _exposure_facts(observations)
    reasons = set(facts["reasons"])
    if unresolved_scoped_exposure:
        reasons.add("scoped_exposure_contributor_unresolved")
    status = _group_exposure_status(facts)
    if unresolved_scoped_exposure and status in {
        "clean_genuinely_unexposed_group",
        "exposure_group_unknown",
    }:
        status = "contains_unresolved_scoped_exposure"
    conversation_count = len(
        {str(row["conversation_key"]) for row in observations}
    )
    target_count = len(target_rows)
    requires_groupwise_split = bool(
        facts["contains_structural"]
        or conversation_count > 1
        or unresolved_scoped_exposure
    )
    if cross_family:
        return {
            "cross_family_author_group_key": group_key,
            "cross_family_author_identity_status": "available",
            "cross_family_author_group_exposure_status": status,
            "cross_family_author_group_exposure_categories": facts["categories"],
            "cross_family_author_group_exposure_reasons": _bounded_strings(reasons),
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
            "cross_family_author_group_contains_unresolved_scoped_exposure": (
                unresolved_scoped_exposure
            ),
            "cross_family_author_group_requires_groupwise_split": (
                requires_groupwise_split
            ),
            "cross_family_author_group_split_assignment": None,
        }
    return {
        "within_family_author_group_key": group_key,
        "author_group_exposure_status": status,
        "author_group_exposure_categories": facts["categories"],
        "author_group_exposure_reasons": _bounded_strings(reasons),
        "author_group_conversation_count": conversation_count,
        "author_group_target_count": target_count,
        "author_group_contains_directly_exposed_material": facts["contains_direct"],
        "author_group_contains_structurally_mined_material": facts[
            "contains_structural"
        ],
        "author_group_contains_genuinely_unexposed_material": facts[
            "contains_unexposed"
        ],
        "author_group_contains_unresolved_scoped_exposure": (
            unresolved_scoped_exposure
        ),
        "author_group_requires_groupwise_split": requires_groupwise_split,
        "author_group_split_assignment": None,
    }


def _unavailable_group_annotation(
    row: Mapping[str, Any], *, target_count: int, identity_status: str
) -> dict[str, Any]:
    facts = _exposure_facts([row])
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
    unresolved = row.get("contributor_identity_binding_status") in {
        "unresolved",
        "conflicting",
    }
    if unresolved:
        reasons.add("scoped_exposure_contributor_unresolved")
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
        "author_group_contains_unresolved_scoped_exposure": unresolved,
        "author_group_requires_groupwise_split": bool(
            facts["contains_structural"] or unresolved
        ),
        "author_group_split_assignment": None,
    }


def _unavailable_cross_group_annotation(
    row: Mapping[str, Any], *, target_count: int, assignment: Mapping[str, Any]
) -> dict[str, Any]:
    facts = _exposure_facts([row])
    identity_status = str(assignment["status"])
    status = (
        "identity_group_conflicting"
        if identity_status == "conflicting"
        else "identity_group_unavailable"
    )
    reasons = set(facts["reasons"])
    reasons.update(str(value) for value in assignment.get("reasons", []))
    unresolved = row.get("contributor_identity_binding_status") in {
        "unresolved",
        "conflicting",
    }
    if unresolved:
        reasons.add("scoped_exposure_contributor_unresolved")
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
        "cross_family_author_group_contains_unresolved_scoped_exposure": unresolved,
        "cross_family_author_group_requires_groupwise_split": bool(
            facts["contains_structural"] or unresolved
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
    conversation_exposure = row.get(
        "author_group_conversation_exposure_status",
        row.get("conversation_exposure_status", effective),
    )
    target_exposure = row.get(
        "author_group_target_exposure_status",
        row.get("target_exposure_status", effective),
    )
    if conversation_exposure not in UNEXPOSED_STATUSES:
        within_reasons.append("conversation_not_genuinely_unexposed")
    if target_exposure not in UNEXPOSED_STATUSES:
        within_reasons.append("target_not_genuinely_unexposed")

    target_identity_status = row.get("target_author_identity_status")
    if target_identity_status == "conflicting":
        within_reasons.append("target_author_identity_conflicting")
    elif target_identity_status != "available":
        within_reasons.append("target_author_identity_unavailable")

    group_status = row.get("author_group_exposure_status")
    if group_status == "identity_group_conflicting":
        within_reasons.append("within_family_identity_group_conflicting")
    elif group_status == "identity_group_unavailable" or not row.get(
        "within_family_author_group_key"
    ):
        within_reasons.append("within_family_identity_group_unavailable")
    if row.get("author_group_contains_unresolved_scoped_exposure") is True:
        within_reasons.append("scoped_exposure_contributor_unresolved")
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
    contributor_exposure_observations: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build an exact group/row crosstab without selecting any split."""

    def counts(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
        return dict(sorted(Counter(str(row.get(field)) for row in rows).items()))

    observation_pairs_by_conversation: dict[str, set[tuple[str, str]]] = defaultdict(
        set
    )
    cross_statuses_by_conversation: dict[str, set[str]] = defaultdict(set)
    for row in contributor_exposure_observations:
        conversation_key = str(row["conversation_key"])
        scheme = _nonempty_identity_component(row.get("author_key_scheme"))
        principal = _nonempty_identity_component(row.get("principal_author_key"))
        if scheme is not None and principal is not None:
            observation_pairs_by_conversation[conversation_key].add(
                (scheme, principal)
            )
        status = row.get("cross_family_author_identity_status")
        if status not in (None, ""):
            cross_statuses_by_conversation[conversation_key].add(str(status))
    for row in target_rows:
        conversation_key = str(row["conversation_key"])
        status = row.get("cross_family_author_identity_status")
        if status not in (None, ""):
            cross_statuses_by_conversation[conversation_key].add(str(status))

    cardinality_rows: list[dict[str, str]] = []
    conversation_cross_rows: list[dict[str, str]] = []
    for conversation in conversation_rows:
        conversation_key = str(conversation["conversation_key"])
        contributor_count = len(
            observation_pairs_by_conversation.get(conversation_key, set())
        )
        cardinality = (
            "zero"
            if contributor_count == 0
            else "one"
            if contributor_count == 1
            else "multiple"
        )
        cardinality_rows.append({"value": cardinality})

        statuses = cross_statuses_by_conversation.get(conversation_key, set())
        if "conflicting" in statuses:
            aggregate_cross_status = "conflicting"
        elif statuses == {"available"}:
            aggregate_cross_status = "available"
        elif not statuses or statuses == {"unavailable"}:
            aggregate_cross_status = "unavailable"
        else:
            aggregate_cross_status = "conflicting"
        conversation_cross_rows.append({"value": aggregate_cross_status})

    dimensions = {
        "target_author_group_exposure_status": counts(
            target_rows, "author_group_exposure_status"
        ),
        "conversation_external_contributor_cardinality": counts(
            cardinality_rows, "value"
        ),
        "contributor_observation_author_group_exposure_status": counts(
            contributor_exposure_observations, "author_group_exposure_status"
        ),
        "contributor_observation_identity_binding_status": counts(
            contributor_exposure_observations,
            "contributor_identity_binding_status",
        ),
        "within_family_group_exposure_status": counts(
            within_family_groups, "author_group_exposure_status"
        ),
        "target_cross_family_author_identity_status": counts(
            target_rows, "cross_family_author_identity_status"
        ),
        "conversation_cross_family_author_identity_status": counts(
            conversation_cross_rows, "value"
        ),
        "contributor_observation_cross_family_author_identity_status": counts(
            contributor_exposure_observations,
            "cross_family_author_identity_status",
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
        "contributor_exposure_observation_count": len(
            contributor_exposure_observations
        ),
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
                row["value"] == "available" for row in conversation_cross_rows
            ),
            "cross_family_identity_unavailable_conversation_count": sum(
                row["value"] == "unavailable" for row in conversation_cross_rows
            ),
            "cross_family_identity_conflicting_conversation_count": sum(
                row["value"] == "conflicting" for row in conversation_cross_rows
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
    contributor_exposure_observations: Sequence[Mapping[str, Any]] = (),
) -> list[str]:
    """Return deterministic reconciliation errors for an author-group crosstab."""

    expected = build_author_group_crosstab(
        target_rows,
        conversation_rows,
        within_family_groups,
        cross_family_groups,
        contributor_exposure_observations,
    )
    errors: list[str] = []
    if _canonical_json_bytes(expected) != _canonical_json_bytes(crosstab):
        errors.append("author_group_crosstab_does_not_reproduce_rows")
    dimensions = crosstab.get("dimensions", {})
    totals = (
        ("target_author_group_exposure_status", len(target_rows)),
        ("conversation_external_contributor_cardinality", len(conversation_rows)),
        (
            "contributor_observation_author_group_exposure_status",
            len(contributor_exposure_observations),
        ),
        (
            "contributor_observation_identity_binding_status",
            len(contributor_exposure_observations),
        ),
        ("within_family_group_exposure_status", len(within_family_groups)),
        ("target_cross_family_author_identity_status", len(target_rows)),
        ("conversation_cross_family_author_identity_status", len(conversation_rows)),
        (
            "contributor_observation_cross_family_author_identity_status",
            len(contributor_exposure_observations),
        ),
        ("cross_family_group_exposure_status", len(cross_family_groups)),
        ("preliminary_within_family_held_out_eligibility", len(target_rows)),
        (
            "preliminary_cross_family_clean_held_out_eligibility",
            len(target_rows),
        ),
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
    contributor_exposure_observations: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate author exposure, annotate targets, and return exact crosstabs.

    Canonical conversation rows remain one row per conversation and receive no
    singular group annotation.  Explicit contributor observations should
    enumerate each complete ``(author_key_scheme, principal_author_key)`` tuple
    present among that conversation's user turns and carry exposure bound to
    that contributor.  If observations are omitted, a one-contributor view is
    derived from legacy conversation metadata for small synthetic callers only.

    A target is always assigned from its own identity tuple and
    ``target_author_identity_status``.  Conversation metadata is never used to
    fill, override, or conflict a target's author identity.
    """

    targets = _copied_rows(target_rows, "target_rows")
    conversations = _copied_rows(conversation_rows, "conversation_rows")
    conversation_keys = [str(row["conversation_key"]) for row in conversations]
    if len(conversation_keys) != len(set(conversation_keys)):
        raise AuthorGroupError("conversation_rows contains duplicate conversation_key")

    conversation_by_key = {
        str(row["conversation_key"]): row for row in conversations
    }
    supplied_observations = contributor_exposure_observations is not None
    raw_observations = (
        _copied_rows(
            contributor_exposure_observations or (),
            "contributor_exposure_observations",
        )
        if supplied_observations
        else []
    )
    for row in [*targets, *raw_observations]:
        key = str(row["conversation_key"])
        if key not in conversation_by_key:
            conversation_by_key[key] = {"conversation_key": key}
    conversations = [conversation_by_key[key] for key in sorted(conversation_by_key)]

    observations = (
        raw_observations
        if supplied_observations
        else _legacy_contributor_observations(conversations)
    )
    observation_within_assignments = [
        _within_identity_assignment(row, target=False) for row in observations
    ]
    observation_cross_assignments = [
        _cross_identity_assignment(row, assignment)
        for row, assignment in zip(observations, observation_within_assignments)
    ]
    observation_keys = [
        _observation_key(row, assignment)
        for row, assignment in zip(observations, observation_within_assignments)
    ]
    if len(observation_keys) != len(set(observation_keys)):
        raise AuthorGroupError(
            "contributor_exposure_observations contains a duplicate contributor "
            "tuple or unresolved observation"
        )

    target_within_assignments = [
        _within_identity_assignment(row, target=True) for row in targets
    ]
    observation_cross_by_within_key: dict[str, list[Mapping[str, Any]]] = (
        defaultdict(list)
    )
    for within, cross in zip(
        observation_within_assignments, observation_cross_assignments
    ):
        if within["status"] == "available":
            observation_cross_by_within_key[str(within["key"])].append(cross)
    target_cross_assignments: list[dict[str, Any]] = []
    for row, within in zip(targets, target_within_assignments):
        cross = _cross_identity_assignment(row, within)
        cross_metadata_absent = (
            row.get("cross_family_author_group_key") in (None, "")
            and row.get("cross_family_author_identity_status") in (None, "")
        )
        if cross_metadata_absent and within["status"] == "available":
            matching = observation_cross_by_within_key.get(
                str(within["key"]), []
            )
            available_keys = {
                str(value["key"])
                for value in matching
                if value.get("status") == "available"
            }
            matching_statuses = {str(value.get("status")) for value in matching}
            if len(available_keys) == 1 and matching_statuses == {"available"}:
                cross = {
                    "status": "available",
                    "key": next(iter(available_keys)),
                    "reasons": _bounded_strings(
                        reason
                        for value in matching
                        for reason in value.get("reasons", [])
                    ),
                }
        target_cross_assignments.append(cross)

    cross_assignments_by_within_key: dict[
        str, list[tuple[str, dict[str, Any]]]
    ] = (
        defaultdict(list)
    )
    for row, within, cross in [
        *zip(
            observations,
            observation_within_assignments,
            observation_cross_assignments,
        ),
        *zip(targets, target_within_assignments, target_cross_assignments),
    ]:
        if within["status"] == "available":
            cross_assignments_by_within_key[str(within["key"])].append(
                (str(row["conversation_key"]), cross)
            )
    for entries in cross_assignments_by_within_key.values():
        available_keys = {
            str(assignment["key"])
            for _conversation_key, assignment in entries
            if assignment.get("status") == "available"
        }
        entries_by_conversation: dict[str, list[dict[str, Any]]] = defaultdict(
            list
        )
        for conversation_key, assignment in entries:
            entries_by_conversation[conversation_key].append(assignment)
        conflicting_conversations = {
            conversation_key
            for conversation_key, assignments in entries_by_conversation.items()
            if (
                "conflicting"
                in {str(assignment.get("status")) for assignment in assignments}
                or {"available", "unavailable"}
                <= {
                    str(assignment.get("status"))
                    for assignment in assignments
                }
            )
        }
        affected = (
            [assignment for _conversation_key, assignment in entries]
            if len(available_keys) > 1
            else [
                assignment
                for conversation_key, assignment in entries
                if conversation_key in conflicting_conversations
            ]
        )
        if not affected:
            continue
        reasons = _bounded_strings(
            [
                "cross_family_identity_metadata_conflicting",
                *(
                    reason
                    for assignment in affected
                    for reason in assignment.get("reasons", [])
                ),
            ]
        )
        for assignment in affected:
            assignment.clear()
            assignment.update(
                {"status": "conflicting", "key": None, "reasons": reasons}
            )

    within_contributors: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    within_targets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    cross_contributors: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    cross_targets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    within_keys_by_conversation: dict[str, set[str]] = defaultdict(set)
    cross_keys_by_conversation: dict[str, set[str]] = defaultdict(set)

    for row, within, cross in zip(
        observations, observation_within_assignments, observation_cross_assignments
    ):
        conversation_key = str(row["conversation_key"])
        if within["status"] == "available":
            key = str(within["key"])
            within_contributors[key].append(row)
            within_keys_by_conversation[conversation_key].add(key)
        if cross["status"] == "available":
            key = str(cross["key"])
            cross_contributors[key].append(row)
            cross_keys_by_conversation[conversation_key].add(key)
    for row, within, cross in zip(
        targets, target_within_assignments, target_cross_assignments
    ):
        conversation_key = str(row["conversation_key"])
        if within["status"] == "available":
            key = str(within["key"])
            within_targets[key].append(row)
            within_keys_by_conversation[conversation_key].add(key)
        if cross["status"] == "available":
            key = str(cross["key"])
            cross_targets[key].append(row)
            cross_keys_by_conversation[conversation_key].add(key)

    unresolved_within_keys: set[str] = set()
    unresolved_cross_keys: set[str] = set()
    for row, within, cross in zip(
        observations, observation_within_assignments, observation_cross_assignments
    ):
        if (
            row.get("contributor_identity_binding_status")
            not in {"unresolved", "conflicting"}
            and within["status"] != "conflicting"
        ):
            continue
        conversation_key = str(row["conversation_key"])
        if within["status"] == "available":
            unresolved_within_keys.add(str(within["key"]))
        else:
            unresolved_within_keys.update(
                within_keys_by_conversation.get(conversation_key, set())
            )
        if cross["status"] == "available":
            unresolved_cross_keys.add(str(cross["key"]))
        else:
            unresolved_cross_keys.update(
                cross_keys_by_conversation.get(conversation_key, set())
            )

    within_group_keys = sorted(set(within_contributors) | set(within_targets))
    cross_group_keys = sorted(set(cross_contributors) | set(cross_targets))
    within_summaries = [
        _group_summary(
            group_key=group_key,
            contributor_rows=within_contributors[group_key],
            target_rows=within_targets[group_key],
            cross_family=False,
            unresolved_scoped_exposure=group_key in unresolved_within_keys,
        )
        for group_key in within_group_keys
    ]
    cross_summaries = [
        _group_summary(
            group_key=group_key,
            contributor_rows=cross_contributors[group_key],
            target_rows=cross_targets[group_key],
            cross_family=True,
            unresolved_scoped_exposure=group_key in unresolved_cross_keys,
        )
        for group_key in cross_group_keys
    ]
    within_summary_by_key = {
        str(row["within_family_author_group_key"]): row
        for row in within_summaries
    }
    cross_summary_by_key = {
        str(row["cross_family_author_group_key"]): row for row in cross_summaries
    }

    def group_annotations(
        row: Mapping[str, Any],
        within_assignment: Mapping[str, Any],
        cross_assignment: Mapping[str, Any],
        *,
        target_count: int,
    ) -> dict[str, Any]:
        result = copy.deepcopy(row)
        if within_assignment["status"] == "available":
            result.update(
                copy.deepcopy(
                    within_summary_by_key[str(within_assignment["key"])]
                )
            )
        else:
            result.update(
                _unavailable_group_annotation(
                    row,
                    target_count=target_count,
                    identity_status=str(within_assignment["status"]),
                )
            )
        if cross_assignment["status"] == "available":
            result.update(
                copy.deepcopy(cross_summary_by_key[str(cross_assignment["key"])])
            )
        else:
            result.update(
                _unavailable_cross_group_annotation(
                    row,
                    target_count=target_count,
                    assignment=cross_assignment,
                )
            )
        return result

    annotated_targets: list[dict[str, Any]] = []
    for row, within, cross in zip(
        targets, target_within_assignments, target_cross_assignments
    ):
        annotated = group_annotations(
            row, within, cross, target_count=1
        )
        identity_status = str(within["status"])
        identity_reasons = set(
            _iter_values(annotated.get("target_author_identity_reasons"))
        )
        if identity_status == "conflicting":
            identity_reasons.add("target_author_identity_conflicting")
        elif identity_status == "unavailable":
            identity_reasons.add("target_author_identity_unavailable")
        annotated["target_author_identity_status"] = identity_status
        annotated["target_author_identity_reasons"] = _bounded_strings(
            identity_reasons
        )
        annotated.update(evaluate_preliminary_eligibility(annotated))
        annotated_targets.append(annotated)

    annotated_observations: list[dict[str, Any]] = []
    for row, within, cross, observation_key in zip(
        observations,
        observation_within_assignments,
        observation_cross_assignments,
        observation_keys,
    ):
        annotated = group_annotations(row, within, cross, target_count=0)
        annotated["contributor_exposure_observation_key"] = observation_key
        annotated["within_family_author_identity_status"] = str(within["status"])
        if annotated.get("contributor_identity_binding_status") not in {
            "available",
            "unresolved",
            "conflicting",
        }:
            annotated["contributor_identity_binding_status"] = (
                "available" if within["status"] == "available" else "unresolved"
            )
        annotated_observations.append(annotated)

    annotated_targets.sort(
        key=lambda row: (
            str(row.get("conversation_key") or ""),
            str(row.get("target_turn_id") or ""),
            str(row.get("target_key") or ""),
            _canonical_json_bytes(row),
        )
    )
    annotated_observations.sort(
        key=lambda row: (
            str(row["contributor_exposure_observation_key"]),
            _canonical_json_bytes(row),
        )
    )

    annotated_conversations: list[dict[str, Any]] = []
    for row in conversations:
        canonical = copy.deepcopy(row)
        for field in list(canonical):
            if (
                field == "within_family_author_group_key"
                or field.startswith("author_group_")
                or field.startswith("cross_family_author_group_")
                or field.startswith("preliminary_")
            ):
                canonical.pop(field, None)
        annotated_conversations.append(canonical)
    annotated_conversations.sort(key=lambda row: str(row["conversation_key"]))

    crosstab = build_author_group_crosstab(
        annotated_targets,
        annotated_conversations,
        within_summaries,
        cross_summaries,
        annotated_observations,
    )
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "target_rows": annotated_targets,
        "conversation_rows": annotated_conversations,
        "contributor_exposure_observations": annotated_observations,
        "within_family_author_groups": within_summaries,
        "cross_family_author_groups": cross_summaries,
        "crosstab": crosstab,
        "author_group_split_performed": False,
    }
