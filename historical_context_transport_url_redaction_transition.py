#!/usr/bin/env python3
"""Build and validate the reviewed transport-URL redaction transition.

This module does not fetch, resolve, or admit evidence.  It compares an
explicit predecessor source-resolution, research and audit records with the
current records and accepts only the reviewed deletion of transient provider
signing query parameters.
The resulting manifest contains hashes and identifiers, never URL values.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import unquote_plus, urlsplit


ROOT = Path(__file__).resolve().parent
MANIFEST_FILENAME = (
    "historical_context_v9_transport_url_redaction_transition_manifest.json"
)
PREDECESSOR_TRANSITION_FILENAME = (
    "historical_context_v9_mtf_live_context_transition_manifest.json"
)
RESOLUTION_FILENAME = "historical_context_source_resolution.json"
AUDIT_FILENAME = "historical_context_source_role_audit.json"
OPENAI_RESEARCH_FILENAME = "historical_context_source_openai_research.json"
EVIDENCE_TRUTH_FILENAME = "historical_context_evidence_truth_audit.json"

SCHEMA_VERSION = 1
MANIFEST_KIND = "historical_context_v9_transport_url_redaction_transition"
TRANSITION_STATUS = "reviewed_transport_only_no_evidence_semantic_change"
EVIDENCE_TRUTH_SCHEMA_VERSION = 2
EVIDENCE_TRUTH_AUDIT_KIND = "historical_context_evidence_truth_triage_audit"
BEFORE_POLICY = "saved-grounding-redirect-resolution-v1"
AFTER_POLICY = (
    "saved-grounding-redirect-resolution-v2-transient-query-redaction"
)
SOURCE_ROLE_POLICY = "historical-context-source-roles-v9-archive-provenance"

EXPECTED_RESOLUTION_ITEM_ID = (
    "a3172f76c55338d1e55d9b77ba09bb8e77c990fd64eaa42e819babb59687e368"
)
EXPECTED_QUOTE_ID = (
    "1005a705248e9a619623b60321cda394a7507124823e6ac6926857339fad48fc"
)
EXPECTED_SOURCE_ID = (
    "09fa8172732cc819559632ee9134961e1060c72743990359942f8fa4151c5527"
)
EXPECTED_SOURCE_INDEX = 9
EXPECTED_SIGNED_HOST = "s3.eu-central-1.amazonaws.com"
EXPECTED_OPENAI_RESEARCH_PATHS = (
    (
        "0f3c7da5b7971b89c68fb0a5a07b1b23c563c32a94ee6b3ae5a77fce11d95bcc",
        14,
    ),
    (
        "e87e2815cb5136fe8d5b0ae9db6114478ea34ae7c7acd30090eb96f2b7aca918",
        3,
    ),
    (
        "eca9b542f2b3b84e3d917eb4dda8b4539505b74db73cfc3b5c0278bdf96593e4",
        2,
    ),
    (
        "eca9b542f2b3b84e3d917eb4dda8b4539505b74db73cfc3b5c0278bdf96593e4",
        3,
    ),
    (
        "eca9b542f2b3b84e3d917eb4dda8b4539505b74db73cfc3b5c0278bdf96593e4",
        4,
    ),
    (
        "eca9b542f2b3b84e3d917eb4dda8b4539505b74db73cfc3b5c0278bdf96593e4",
        14,
    ),
    (
        "f0d85c7301e8b27bc694ac030d7c5f6b1d15ff3bcdf31cbdfb03a1c05bbe83ea",
        3,
    ),
)
EXPECTED_OPENAI_SIGNED_HOSTS = frozenset({
    (
        "s3-euw1-ap-pe-df-pch-content-store-p."
        "s3.eu-west-1.amazonaws.com"
    ),
    "storage.freidok.ub.uni-freiburg.de",
})
EXPECTED_OPENAI_REMOVED_QUERY_KEYS = (
    "AWSAccessKeyId",
    "Signature",
    "X-Amz-Algorithm",
    "X-Amz-Content-Sha256",
    "X-Amz-Credential",
    "X-Amz-Date",
    "X-Amz-Expires",
    "X-Amz-Signature",
    "X-Amz-SignedHeaders",
    "x-amz-security-token",
)
EXPECTED_REMOVED_QUERY_KEYS = (
    "X-Amz-Algorithm",
    "X-Amz-Credential",
    "X-Amz-Date",
    "X-Amz-Expires",
    "X-Amz-Signature",
    "X-Amz-SignedHeaders",
)
SIGNED_QUERY_PREFIXES = ("x-amz-", "x-goog-")
SIGNED_QUERY_KEYS = frozenset({
    "awsaccesskeyid",
    "googleaccessid",
    "signature",
})

EXPECTED_CHANGE_COUNTS = {
    "openai_research.items.*.rejected_sources[].source.url": 7,
    "resolution.items.*.final_url": 1,
    "resolution.items.*.redirect_chain[]": 1,
    "resolution.policy_version": 1,
    "source_role_audit.items.*.sources[].resolution_record_sha256": 1,
    "source_role_audit.items.*.sources[].resolved_url": 1,
    (
        "source_role_audit.source_file_hashes."
        "historical_context_source_resolution.json"
    ): 1,
    (
        "source_role_audit.source_file_hashes."
        "historical_context_source_openai_research.json"
    ): 1,
}
REQUIRED_INVARIANT_LABELS = frozenset({
    "corpus",
    "historical_context_gate",
    "ordinary_cycle",
    "semantic_veto",
    "unresolved",
})

# The frozen transition binds the historical semantic-gate audit.  Its only
# accepted successor refreshes both pins for the reviewed semantic ledger.
HISTORICAL_CONTEXT_GATE_REPOSITORY_PATH = (
    "historical_context_reply_semantic_gate_audit.json"
)
HISTORICAL_CONTEXT_GATE_HISTORICAL_BEFORE_SHA256 = (
    "8f453580c49ec98cd114525fd54d0844767254359f8ba18ad6db88384a8cb732"
)
HISTORICAL_CONTEXT_GATE_HISTORICAL_SHA256 = (
    "b1c163e09a2c7ebf7ae33221d093ad6855e2ba7e1ddc5b4bf78706e43633ff4d"
)
HISTORICAL_CONTEXT_GATE_SUCCESSOR_SHA256 = (
    "7ad0af49ee90ab1cff0f0f2fd76391afaa5c6dbfe15bb481d765d52d84f9c4f2"
)
HISTORICAL_CONTEXT_GATE_LEDGER_REPOSITORY_PATH = (
    "historical_context_published_reply_semantic_review.json"
)
HISTORICAL_CONTEXT_GATE_HISTORICAL_LEDGER_SHA256 = (
    "dd041bb7745fa0b018ed09c3c0c9f8db676c0755d530572bc7e4bf56cdfc793e"
)
HISTORICAL_CONTEXT_GATE_SUCCESSOR_LEDGER_SHA256 = (
    "f81832f3c0aa4e121e5ea521f0442fa90c024d1b3afd24fda4e2b49fe3cb0648"
)
HISTORICAL_CONTEXT_GATE_SEMANTIC_SUMMARY = {
    "blocked_quote_count": 13,
    "blocked_quote_ids_sha256": (
        "7b1e3a2d0599860aff61c1503b84a4bca0252c5a209525da2d00c4824fbae611"
    ),
    "normalised_audit_sha256": (
        "3d365c1465b39c2d6ce7629b9cc77a53956f63c7bde6acbbe15acabc4beca5e6"
    ),
    "policy_version": (
        "historical-context-semantic-gate-v1-open-review-whole-reply"
    ),
}

# The immutable transport-redaction transition predates reviewed,
# provenance-only refreshes of the eligibility and semantic-veto manifests.
# The formatter gained receipt-durability code without changing the
# attribution predicate, pair judgements, policy, population or counts.  Keep
# these compatibility tuples deliberately exact: they must never become a
# generic source-pin bypass.
SEMANTIC_VETO_REPOSITORY_PATH = (
    "semantic_alignment_research/quote_attribution_cleanup_001/"
    "deployment_candidate/material_veto_v3_shadow_manifest.json"
)
SEMANTIC_VETO_HISTORICAL_SHA256 = (
    "6dd8eaf84bd913c359caf55bb213c79dbefaeeb5b0bbe4d9b0ad7f4414869d32"
)
SEMANTIC_VETO_SUCCESSOR_SHA256 = (
    "50fd87e23fb32bb26149f611459c42326a203430af9ff09d4aa8bd357eb1efd8"
)
SEMANTIC_VETO_HISTORICAL_CANONICAL_SHA256 = (
    "ab4ac8138ee2faad2e761796df4a43fc04af63f39d08befbb437fb50322319c7"
)
ATTRIBUTION_PREDICATE_PATH = "historical_context_formatter.py"
ATTRIBUTION_PREDICATE_HISTORICAL_SHA256 = (
    "8b9848106390806ceefb282630ad4ffbce59af40e2f4a16eeb16f398a1d4a04d"
)
ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256 = (
    "55982ee4906c020e6dc3fbcd0ea09950d4de6a3e3a75968240ca04163dd806a8"
)
ATTRIBUTION_PREDICATE_AST_PROJECTION_SHA256 = (
    "66e7912a6b2891e42cc7fce27c773c602ad81abd4448d6b5b38921475e6b668c"
)
ATTRIBUTION_PREDICATE_FUNCTION = (
    "packet_is_attributed_to_margaret_thatcher"
)
ATTRIBUTION_PREDICATE_GLOBALS = (
    "_MARGARET_THATCHER_CANONICAL_SPEAKER",
    "THATCHER_ATTRIBUTION_RULE_VERSION",
)
RUNTIME_ELIGIBILITY_REPOSITORY_PATH = (
    "semantic_alignment_research/quote_attribution_cleanup_001/"
    "deployment_candidate/runtime_eligible_quote_manifest.json"
)
RUNTIME_ELIGIBILITY_HISTORICAL_SHA256 = (
    "8b74dab5db91082e5448b89eb0d5a9e13d79939ff2af5d30b922aadacaa0141f"
)
RUNTIME_ELIGIBILITY_SUCCESSOR_SHA256 = (
    "44c999cd52ce86c62611e0cf6b27889437493255671705d037a34a00b804d6f8"
)
RUNTIME_ELIGIBILITY_HISTORICAL_PREDICATE_SHA256 = (
    "1bfc42e977316dd36beee8093d23e83816371988e18c8d22798e4a6a54a64ffb"
)
RUNTIME_ELIGIBILITY_SEMANTIC_SUMMARY = {
    "runtime_eligible_quote_count": 611,
    "runtime_eligible_quote_ids_in_order_sha256": (
        "db72c0c0e8cf766c5ac463bb3ad0d2538733d37e8c1938f59c90cf73c9bb87d2"
    ),
}


class TransitionError(RuntimeError):
    """Raised when the transition is broader than the reviewed redaction."""


@dataclass(frozen=True)
class InvariantPair:
    """One exact before/after input which must remain byte-identical."""

    before: Path
    after: Path
    repository_path: str


def _reject_duplicate(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TransitionError(f"duplicate JSON object name: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise TransitionError(f"non-finite JSON constant is forbidden: {value}")


def _load_json_bytes(data: bytes, *, source_name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data,
            object_pairs_hook=_reject_duplicate,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TransitionError(f"invalid JSON input: {source_name}") from exc
    if not isinstance(value, dict):
        raise TransitionError(f"expected a JSON object: {source_name}")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise TransitionError(f"invalid JSON input: {path.name}") from exc
    return _load_json_bytes(data, source_name=path.name)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_json(value))


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _resolution_record_sha256(value: dict[str, Any]) -> str:
    return _sha256_json(value)


def _query_key(raw_pair: str) -> str:
    return unquote_plus(raw_pair.partition("=")[0])


def _is_signed_query_key(key: str) -> bool:
    folded = key.casefold()
    return (
        folded in SIGNED_QUERY_KEYS
        or folded.startswith(SIGNED_QUERY_PREFIXES)
    )


@dataclass(frozen=True)
class UrlRedaction:
    """One deletion-only signed-query transformation."""

    removed_keys: tuple[str, ...]


def _compare_redacted_url(
    before: Any,
    after: Any,
    *,
    allowed_hosts: frozenset[str] = frozenset({EXPECTED_SIGNED_HOST}),
) -> UrlRedaction | None:
    """Accept only byte-preserving deletion of signed query pairs."""
    if not isinstance(before, str) or not isinstance(after, str):
        if before == after:
            return None
        raise TransitionError("transport URL type or presence changed")
    if before == after:
        return None

    old = urlsplit(before)
    new = urlsplit(after)
    if (
        old.scheme != new.scheme
        or old.netloc != new.netloc
        or old.path != new.path
        or old.fragment != new.fragment
    ):
        raise TransitionError("transport URL identity changed")
    if (old.hostname or "").casefold() not in allowed_hosts:
        raise TransitionError("redaction affected an unreviewed transport host")

    old_pairs = old.query.split("&") if old.query else []
    new_pairs = new.query.split("&") if new.query else []
    retained: list[str] = []
    removed: list[str] = []
    for pair in old_pairs:
        key = _query_key(pair)
        if _is_signed_query_key(key):
            removed.append(key)
        else:
            retained.append(pair)
    if not removed:
        raise TransitionError("transport URL changed without signed parameters")
    if new_pairs != retained:
        raise TransitionError(
            "transport URL redaction changed retained query bytes or order"
        )
    if any(_is_signed_query_key(_query_key(pair)) for pair in new_pairs):
        raise TransitionError("transport URL retains signed query parameters")
    return UrlRedaction(tuple(removed))


def _compare_resolution(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Return the exact, reviewed resolution change description."""
    if before.get("policy_version") != BEFORE_POLICY:
        raise TransitionError("predecessor resolution policy differs")
    if after.get("policy_version") != AFTER_POLICY:
        raise TransitionError("current resolution policy differs")

    before_items = before.get("items")
    after_items = after.get("items")
    if (
        not isinstance(before_items, dict)
        or not isinstance(after_items, dict)
        or set(before_items) != set(after_items)
    ):
        raise TransitionError("resolution item scope differs")

    expected_after = copy.deepcopy(before)
    expected_after["policy_version"] = AFTER_POLICY
    affected_item_ids: list[str] = []
    affected_quote_ids: set[str] = set()
    removed_keys: set[str] = set()
    removed_query_value_count = 0
    final_url_changes = 0
    redirect_changes = 0

    for item_id in sorted(before_items):
        old_row = before_items[item_id]
        new_row = after_items[item_id]
        if not isinstance(old_row, dict) or not isinstance(new_row, dict):
            raise TransitionError("resolution item is not an object")
        expected_row = expected_after["items"][item_id]
        row_changed = False

        redaction = _compare_redacted_url(
            old_row.get("final_url"), new_row.get("final_url")
        )
        if redaction is not None:
            expected_row["final_url"] = new_row["final_url"]
            final_url_changes += 1
            row_changed = True
            removed_keys.update(redaction.removed_keys)
            removed_query_value_count += len(redaction.removed_keys)

        old_chain = old_row.get("redirect_chain")
        new_chain = new_row.get("redirect_chain")
        if not isinstance(old_chain, list) or not isinstance(new_chain, list):
            raise TransitionError("resolution redirect chain is invalid")
        if len(old_chain) != len(new_chain):
            raise TransitionError("resolution redirect-chain length changed")
        for index, (old_url, new_url) in enumerate(zip(old_chain, new_chain)):
            redaction = _compare_redacted_url(old_url, new_url)
            if redaction is not None:
                expected_row["redirect_chain"][index] = new_url
                redirect_changes += 1
                row_changed = True
                removed_keys.update(redaction.removed_keys)
                removed_query_value_count += len(redaction.removed_keys)

        if row_changed:
            affected_item_ids.append(item_id)
            quote_ids = old_row.get("quote_ids")
            if not isinstance(quote_ids, list) or not all(
                isinstance(value, str) for value in quote_ids
            ):
                raise TransitionError("resolution quote identities are invalid")
            affected_quote_ids.update(quote_ids)

    if expected_after != after:
        raise TransitionError(
            "resolution contains changes beyond signed-query deletion"
        )
    if affected_item_ids != [EXPECTED_RESOLUTION_ITEM_ID]:
        raise TransitionError("resolution redaction scope differs")
    if affected_quote_ids != {EXPECTED_QUOTE_ID}:
        raise TransitionError("resolution quotation scope differs")
    if tuple(sorted(removed_keys)) != EXPECTED_REMOVED_QUERY_KEYS:
        raise TransitionError("removed signed-query key set differs")
    if final_url_changes != 1 or redirect_changes != 1:
        raise TransitionError("signed transport URL occurrence count differs")
    if removed_query_value_count != 12:
        raise TransitionError("signed query-value deletion count differs")

    normalised_before = copy.deepcopy(before)
    normalised_before["policy_version"] = AFTER_POLICY
    normalised_before["items"][EXPECTED_RESOLUTION_ITEM_ID] = copy.deepcopy(
        after_items[EXPECTED_RESOLUTION_ITEM_ID]
    )
    normalised_digest = _sha256_json(normalised_before)
    if normalised_digest != _sha256_json(after):
        raise TransitionError("resolution semantic normalisation differs")

    return {
        "affected_item_ids": affected_item_ids,
        "affected_quote_ids": sorted(affected_quote_ids),
        "final_url_change_count": final_url_changes,
        "redirect_url_change_count": redirect_changes,
        "removed_query_keys": sorted(removed_keys),
        "removed_query_value_count": removed_query_value_count,
        "transport_normalised_sha256": normalised_digest,
    }


def _openai_rejected_source_url(
    manifest: dict[str, Any],
    quote_id: str,
    index: int,
) -> str:
    try:
        value = manifest["items"][quote_id]["rejected_sources"][index][
            "source"
        ]["url"]
    except (KeyError, IndexError, TypeError) as exc:
        raise TransitionError(
            "reviewed OpenAI rejected-source path is missing"
        ) from exc
    if not isinstance(value, str):
        raise TransitionError("OpenAI rejected-source URL is invalid")
    return value


def _compare_openai_research(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Accept the seven reviewed rejected-source URL redactions only."""
    if before.get("policy_version") != after.get("policy_version"):
        raise TransitionError("OpenAI research policy changed")
    if before.get("schema_version") != after.get("schema_version"):
        raise TransitionError("OpenAI research schema changed")

    expected_after = copy.deepcopy(before)
    removed_keys: set[str] = set()
    removed_query_value_count = 0
    path_records: list[dict[str, Any]] = []
    for quote_id, index in EXPECTED_OPENAI_RESEARCH_PATHS:
        old_url = _openai_rejected_source_url(before, quote_id, index)
        new_url = _openai_rejected_source_url(after, quote_id, index)
        redaction = _compare_redacted_url(
            old_url,
            new_url,
            allowed_hosts=EXPECTED_OPENAI_SIGNED_HOSTS,
        )
        if redaction is None:
            raise TransitionError(
                "reviewed OpenAI rejected-source URL was not redacted"
            )
        expected_after["items"][quote_id]["rejected_sources"][index][
            "source"
        ]["url"] = new_url
        removed_keys.update(redaction.removed_keys)
        removed_query_value_count += len(redaction.removed_keys)
        path_records.append({
            "quote_id": quote_id,
            "rejected_source_index": index,
        })

    if expected_after != after:
        raise TransitionError(
            "OpenAI research contains changes beyond reviewed URL redaction"
        )
    if tuple(sorted(removed_keys)) != EXPECTED_OPENAI_REMOVED_QUERY_KEYS:
        raise TransitionError("OpenAI removed signed-query key set differs")
    if removed_query_value_count != 37:
        raise TransitionError(
            "OpenAI signed query-value deletion count differs"
        )
    normalised_digest = _sha256_json(expected_after)
    if normalised_digest != _sha256_json(after):
        raise TransitionError("OpenAI research semantic normalisation differs")
    return {
        "affected_paths": path_records,
        "affected_quote_ids": sorted({
            quote_id for quote_id, _index in EXPECTED_OPENAI_RESEARCH_PATHS
        }),
        "removed_query_keys": sorted(removed_keys),
        "removed_query_value_count": removed_query_value_count,
        "transport_normalised_sha256": normalised_digest,
        "url_change_count": len(path_records),
    }


def _without_transport_fields(audit: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(audit)
    source_hashes = result.get("source_file_hashes")
    if isinstance(source_hashes, dict):
        source_hashes.pop(RESOLUTION_FILENAME, None)
        source_hashes.pop(OPENAI_RESEARCH_FILENAME, None)
    for item in result.get("items", {}).values():
        if not isinstance(item, dict):
            continue
        for source in item.get("sources", []):
            if isinstance(source, dict) and source.get(
                "resolved_redirect_record"
            ):
                source.pop("resolved_url", None)
                source.pop("resolution_record_sha256", None)
    return result


def _public_projection(audit: dict[str, Any]) -> dict[str, Any]:
    public_keys = (
        "attribution_eligible",
        "confidence_after",
        "date",
        "public_context_supported_fields",
        "public_output_changes",
        "public_verification_wording",
        "quote_id",
        "quote_text",
        "quote_text_sha256",
        "renderable_sources",
        "requires_further_research",
        "source_event",
        "speaker",
        "stable_locator",
        "supported_public_roles",
        "wording_status_after",
    )
    items = audit.get("items")
    if not isinstance(items, dict):
        raise TransitionError("source-role audit item scope is invalid")
    return {
        quote_id: {
            key: copy.deepcopy(item.get(key))
            for key in public_keys
        }
        for quote_id, item in sorted(items.items())
        if isinstance(item, dict)
    }


def _compare_audit(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    before_resolution: dict[str, Any],
    after_resolution: dict[str, Any],
    before_resolution_sha256: str,
    after_resolution_sha256: str,
    before_openai_research_sha256: str,
    after_openai_research_sha256: str,
) -> dict[str, Any]:
    """Accept only transport-derived source-role audit changes."""
    if (
        before.get("policy_version") != SOURCE_ROLE_POLICY
        or after.get("policy_version") != SOURCE_ROLE_POLICY
    ):
        raise TransitionError("source-role policy differs")
    expected_after = copy.deepcopy(before)
    hashes = expected_after.get("source_file_hashes")
    if not isinstance(hashes, dict):
        raise TransitionError("source-role input hashes are invalid")
    if hashes.get(RESOLUTION_FILENAME) != before_resolution_sha256:
        raise TransitionError("predecessor audit does not bind resolution")
    hashes[RESOLUTION_FILENAME] = after_resolution_sha256
    if (
        hashes.get(OPENAI_RESEARCH_FILENAME)
        != before_openai_research_sha256
    ):
        raise TransitionError(
            "predecessor audit does not bind OpenAI research"
        )
    hashes[OPENAI_RESEARCH_FILENAME] = after_openai_research_sha256

    before_items = before.get("items")
    after_items = after.get("items")
    if (
        not isinstance(before_items, dict)
        or not isinstance(after_items, dict)
        or set(before_items) != set(after_items)
    ):
        raise TransitionError("source-role audit item scope differs")

    old_resolution_row = before_resolution["items"][
        EXPECTED_RESOLUTION_ITEM_ID
    ]
    new_resolution_row = after_resolution["items"][
        EXPECTED_RESOLUTION_ITEM_ID
    ]
    old_row_hash = _resolution_record_sha256(old_resolution_row)
    new_row_hash = _resolution_record_sha256(new_resolution_row)
    old_url = old_resolution_row.get("final_url")
    new_url = new_resolution_row.get("final_url")

    matching_sources: list[tuple[str, int, str]] = []
    for quote_id, item in before_items.items():
        if not isinstance(item, dict):
            raise TransitionError("source-role audit item is invalid")
        for index, source in enumerate(item.get("sources", [])):
            if (
                isinstance(source, dict)
                and source.get("resolved_redirect_record") is True
                and source.get("resolved_url") == old_url
                and source.get("resolution_record_sha256") == old_row_hash
            ):
                matching_sources.append((
                    quote_id,
                    index,
                    str(source.get("source_id") or ""),
                ))
    if matching_sources != [(
        EXPECTED_QUOTE_ID,
        EXPECTED_SOURCE_INDEX,
        EXPECTED_SOURCE_ID,
    )]:
        raise TransitionError("source-role transport binding scope differs")

    source = expected_after["items"][EXPECTED_QUOTE_ID]["sources"][
        EXPECTED_SOURCE_INDEX
    ]
    source["resolved_url"] = new_url
    source["resolution_record_sha256"] = new_row_hash
    if expected_after != after:
        raise TransitionError(
            "source-role audit contains non-transport semantic changes"
        )

    evidence_before = _sha256_json(_without_transport_fields(before))
    evidence_after = _sha256_json(_without_transport_fields(after))
    public_before = _sha256_json(_public_projection(before))
    public_after = _sha256_json(_public_projection(after))
    if evidence_before != evidence_after or public_before != public_after:
        raise TransitionError("evidence or public semantics changed")

    return {
        "affected_source_ids": [EXPECTED_SOURCE_ID],
        "affected_source_indexes": [EXPECTED_SOURCE_INDEX],
        "evidence_semantics_sha256": evidence_before,
        "public_semantics_sha256": public_before,
        "resolution_record_sha256": {
            "before": old_row_hash,
            "after": new_row_hash,
        },
    }


def _ids_sha256(values: Sequence[str]) -> str:
    return _sha256_bytes(
        "".join(f"{value}\n" for value in values).encode("utf-8")
    )


def _invariant_summary(label: str, value: dict[str, Any]) -> dict[str, Any]:
    if label == "corpus":
        records = value.get("records")
        if not isinstance(records, list):
            raise TransitionError("corpus invariant has no record list")
        quote_ids = [
            str(row.get("quote_id") or "")
            for row in records
            if isinstance(row, dict)
        ]
        if len(quote_ids) != len(records):
            raise TransitionError("corpus invariant record is invalid")
        return {
            "record_count": value.get("record_count"),
            "quote_ids_in_order_sha256": _ids_sha256(quote_ids),
            "quote_text_projection_sha256": _sha256_json([
                {
                    "quote_id": row.get("quote_id"),
                    "quote_text": row.get("quote_text"),
                }
                for row in records
            ]),
        }
    if label == "ordinary_cycle":
        quote_ids = value.get("runtime_eligible_quote_ids")
        if not isinstance(quote_ids, list) or not all(
            isinstance(item, str) for item in quote_ids
        ):
            raise TransitionError("ordinary-cycle invariant is invalid")
        return {
            "runtime_eligible_quote_count": value.get(
                "runtime_eligible_quote_count"
            ),
            "runtime_eligible_quote_ids_in_order_sha256": _ids_sha256(
                quote_ids
            ),
        }
    if label == "historical_context_gate":
        gate = value.get("gate")
        records = value.get("records")
        input_hashes = value.get("input_hashes")
        if (
            not isinstance(gate, dict)
            or not isinstance(records, list)
            or not isinstance(input_hashes, dict)
        ):
            raise TransitionError("historical-context gate invariant is invalid")
        normalised = copy.deepcopy(value)
        normalised_gate = normalised["gate"]
        normalised_hashes = normalised["input_hashes"]
        for container, key in (
            (normalised_gate, "semantic_review_ledger_sha256"),
            (
                normalised_hashes,
                "historical_context_published_reply_semantic_review.json",
            ),
            (
                normalised_hashes,
                "historical_context_source_role_audit.json",
            ),
        ):
            if not _is_sha256(container.get(key)):
                raise TransitionError(
                    "historical-context gate rebind hash is invalid"
                )
            container.pop(key)
        blocked_ids = sorted(
            str(row.get("quote_id"))
            for row in records
            if isinstance(row, dict)
            and row.get("public_reply_decision") != "eligible_allow"
            and row.get("attribution_eligible") is True
        )
        return {
            "blocked_quote_count": gate.get("blocked_quote_count"),
            "blocked_quote_ids_sha256": _ids_sha256(blocked_ids),
            "normalised_audit_sha256": _sha256_json(normalised),
            "policy_version": value.get("policy_version"),
        }
    return {"canonical_json_sha256": _sha256_json(value)}


def _validate_repository_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or any(ord(character) < 32 for character in value)
    ):
        raise TransitionError("invariant repository path is unsafe")
    return path.as_posix()


def _build_unchanged_invariants(
    pairs: Mapping[str, InvariantPair] | None,
) -> dict[str, Any]:
    if set(pairs or {}) != REQUIRED_INVARIANT_LABELS:
        raise TransitionError(
            "unchanged invariant labels differ from the required set"
        )
    result: dict[str, Any] = {}
    for label, pair in sorted((pairs or {}).items()):
        before_bytes = pair.before.read_bytes()
        after_bytes = pair.after.read_bytes()
        before_value = _load_json(pair.before)
        after_value = _load_json(pair.after)
        before_summary = _invariant_summary(label, before_value)
        after_summary = _invariant_summary(label, after_value)
        if before_summary != after_summary:
            raise TransitionError(f"{label} invariant semantics changed")
        result[label] = {
            "repository_path": _validate_repository_path(
                pair.repository_path
            ),
            "before_sha256": _sha256_bytes(before_bytes),
            "after_sha256": _sha256_bytes(after_bytes),
            "bytes_unchanged": before_bytes == after_bytes,
            "semantic_summary": after_summary,
            "unchanged": True,
        }
    return result


def build_transition(
    *,
    predecessor_resolution: Path,
    current_resolution: Path,
    predecessor_openai_research: Path,
    current_openai_research: Path,
    predecessor_source_role_audit: Path,
    current_source_role_audit: Path,
    predecessor_transition: Path,
    unchanged_invariants: Mapping[str, InvariantPair] | None = None,
) -> dict[str, Any]:
    """Build the deterministic reviewed transport-redaction manifest."""
    before_resolution = _load_json(predecessor_resolution)
    after_resolution = _load_json(current_resolution)
    before_openai = _load_json(predecessor_openai_research)
    after_openai = _load_json(current_openai_research)
    before_audit = _load_json(predecessor_source_role_audit)
    after_audit = _load_json(current_source_role_audit)
    predecessor = _load_json(predecessor_transition)

    before_resolution_hash = _sha256_file(predecessor_resolution)
    after_resolution_hash = _sha256_file(current_resolution)
    before_openai_hash = _sha256_file(predecessor_openai_research)
    after_openai_hash = _sha256_file(current_openai_research)
    before_audit_hash = _sha256_file(predecessor_source_role_audit)
    after_audit_hash = _sha256_file(current_source_role_audit)
    bound_audit_hash = predecessor.get("input_hashes", {}).get(AUDIT_FILENAME)
    if bound_audit_hash != before_audit_hash:
        raise TransitionError(
            "predecessor transition does not bind predecessor source-role audit"
        )

    resolution_change = _compare_resolution(
        before_resolution, after_resolution
    )
    openai_change = _compare_openai_research(before_openai, after_openai)
    audit_change = _compare_audit(
        before_audit,
        after_audit,
        before_resolution=before_resolution,
        after_resolution=after_resolution,
        before_resolution_sha256=before_resolution_hash,
        after_resolution_sha256=after_resolution_hash,
        before_openai_research_sha256=before_openai_hash,
        after_openai_research_sha256=after_openai_hash,
    )
    invariants = _build_unchanged_invariants(unchanged_invariants)

    return {
        "changed_json_path_class_counts": dict(EXPECTED_CHANGE_COUNTS),
        "files": {
            AUDIT_FILENAME: {
                "after_sha256": after_audit_hash,
                "before_sha256": before_audit_hash,
            },
            RESOLUTION_FILENAME: {
                "after_sha256": after_resolution_hash,
                "before_sha256": before_resolution_hash,
            },
            OPENAI_RESEARCH_FILENAME: {
                "after_sha256": after_openai_hash,
                "before_sha256": before_openai_hash,
            },
        },
        "manifest_kind": MANIFEST_KIND,
        "policy_transition": {
            "from": BEFORE_POLICY,
            "source_role_policy": SOURCE_ROLE_POLICY,
            "to": AFTER_POLICY,
        },
        "predecessor_transition": {
            "path": PREDECESSOR_TRANSITION_FILENAME,
            "sha256": _sha256_file(predecessor_transition),
            "source_role_audit_sha256": bound_audit_hash,
        },
        "proofs": {
            "evidence_semantics_sha256": audit_change[
                "evidence_semantics_sha256"
            ],
            "public_semantics_sha256": audit_change[
                "public_semantics_sha256"
            ],
            "openai_research_transport_normalised_sha256": openai_change[
                "transport_normalised_sha256"
            ],
            "resolution_transport_normalised_sha256": resolution_change[
                "transport_normalised_sha256"
            ],
        },
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "quote_ids": resolution_change["affected_quote_ids"],
            "quote_ids_sha256": _ids_sha256(
                resolution_change["affected_quote_ids"]
            ),
            "resolution_item_ids": resolution_change["affected_item_ids"],
            "source_ids": audit_change["affected_source_ids"],
            "source_indexes": audit_change["affected_source_indexes"],
            "openai_research_quote_ids": openai_change[
                "affected_quote_ids"
            ],
            "openai_research_rejected_source_paths": openai_change[
                "affected_paths"
            ],
        },
        "transport_redaction": {
            "final_url_change_count": resolution_change[
                "final_url_change_count"
            ],
            "redirect_url_change_count": resolution_change[
                "redirect_url_change_count"
            ],
            "removed_query_keys": resolution_change["removed_query_keys"],
            "removed_query_value_count": resolution_change[
                "removed_query_value_count"
            ],
            "retained_query_bytes_and_order_unchanged": True,
            "signed_host": EXPECTED_SIGNED_HOST,
            "openai_research": {
                "removed_query_keys": openai_change[
                    "removed_query_keys"
                ],
                "removed_query_value_count": openai_change[
                    "removed_query_value_count"
                ],
                "retained_query_bytes_and_order_unchanged": True,
                "signed_hosts": sorted(EXPECTED_OPENAI_SIGNED_HOSTS),
                "url_change_count": openai_change["url_change_count"],
            },
        },
        "transition_status": TRANSITION_STATUS,
        "unchanged_invariants": invariants,
    }


def validate_transition(
    manifest: dict[str, Any],
    *,
    predecessor_resolution: Path,
    current_resolution: Path,
    predecessor_openai_research: Path,
    current_openai_research: Path,
    predecessor_source_role_audit: Path,
    current_source_role_audit: Path,
    predecessor_transition: Path,
    unchanged_invariants: Mapping[str, InvariantPair] | None = None,
) -> dict[str, Any]:
    """Rebuild and require exact manifest equality."""
    expected = build_transition(
        predecessor_resolution=predecessor_resolution,
        current_resolution=current_resolution,
        predecessor_openai_research=predecessor_openai_research,
        current_openai_research=current_openai_research,
        predecessor_source_role_audit=predecessor_source_role_audit,
        current_source_role_audit=current_source_role_audit,
        predecessor_transition=predecessor_transition,
        unchanged_invariants=unchanged_invariants,
    )
    if manifest != expected:
        raise TransitionError("transport-redaction transition manifest differs")
    return manifest


def _resolved_beneath(root: Path, relative: str) -> Path:
    relative = _validate_repository_path(relative)
    base = root.resolve()
    candidate = (base / relative).resolve()
    if candidate != base and base not in candidate.parents:
        raise TransitionError("invariant path escapes the project root")
    return candidate


def _expected_historical_context_gate_historical_binding() -> dict[str, Any]:
    return {
        "after_sha256": HISTORICAL_CONTEXT_GATE_HISTORICAL_SHA256,
        "before_sha256": HISTORICAL_CONTEXT_GATE_HISTORICAL_BEFORE_SHA256,
        "bytes_unchanged": False,
        "repository_path": HISTORICAL_CONTEXT_GATE_REPOSITORY_PATH,
        "semantic_summary": dict(HISTORICAL_CONTEXT_GATE_SEMANTIC_SUMMARY),
        "unchanged": True,
    }


def _validate_historical_context_gate_provenance_successor(
    *,
    root: Path,
    current_bytes: bytes,
    current_value: dict[str, Any],
    item: dict[str, Any],
) -> None:
    """Accept one exact semantic-ledger-pin-only gate-audit successor."""
    if item != _expected_historical_context_gate_historical_binding():
        raise TransitionError(
            "historical_context_gate historical invariant binding differs"
        )
    if _sha256_bytes(current_bytes) != (
        HISTORICAL_CONTEXT_GATE_SUCCESSOR_SHA256
    ):
        raise TransitionError(
            "historical_context_gate provenance successor hash differs"
        )

    gate = current_value.get("gate")
    input_hashes = current_value.get("input_hashes")
    if (
        not isinstance(gate, dict)
        or not isinstance(input_hashes, dict)
        or gate.get("semantic_review_ledger_sha256")
        != HISTORICAL_CONTEXT_GATE_SUCCESSOR_LEDGER_SHA256
        or input_hashes.get(
            HISTORICAL_CONTEXT_GATE_LEDGER_REPOSITORY_PATH
        ) != HISTORICAL_CONTEXT_GATE_SUCCESSOR_LEDGER_SHA256
    ):
        raise TransitionError(
            "historical_context_gate successor ledger binding differs"
        )

    ledger_path = _resolved_beneath(
        root,
        HISTORICAL_CONTEXT_GATE_LEDGER_REPOSITORY_PATH,
    )
    try:
        ledger_bytes = ledger_path.read_bytes()
    except OSError as exc:
        raise TransitionError(
            "historical_context_gate successor ledger cannot be read"
        ) from exc
    if _sha256_bytes(ledger_bytes) != (
        HISTORICAL_CONTEXT_GATE_SUCCESSOR_LEDGER_SHA256
    ):
        raise TransitionError(
            "historical_context_gate successor ledger hash differs"
        )

    successor_hash = (
        HISTORICAL_CONTEXT_GATE_SUCCESSOR_LEDGER_SHA256.encode("ascii")
    )
    historical_hash = (
        HISTORICAL_CONTEXT_GATE_HISTORICAL_LEDGER_SHA256.encode("ascii")
    )
    if (
        current_bytes.count(successor_hash) != 2
        or historical_hash in current_bytes
    ):
        raise TransitionError(
            "historical_context_gate provenance successor byte binding "
            "is ambiguous"
        )
    reconstructed_bytes = current_bytes.replace(
        successor_hash,
        historical_hash,
    )
    if _sha256_bytes(reconstructed_bytes) != (
        HISTORICAL_CONTEXT_GATE_HISTORICAL_SHA256
    ):
        raise TransitionError(
            "historical_context_gate provenance successor changes "
            "additional bytes"
        )
    if item.get("semantic_summary") != _invariant_summary(
        "historical_context_gate", current_value
    ):
        raise TransitionError(
            "historical_context_gate invariant semantics differ"
        )


def _expected_semantic_veto_historical_binding() -> dict[str, Any]:
    return {
        "after_sha256": SEMANTIC_VETO_HISTORICAL_SHA256,
        "before_sha256": SEMANTIC_VETO_HISTORICAL_SHA256,
        "bytes_unchanged": True,
        "repository_path": SEMANTIC_VETO_REPOSITORY_PATH,
        "semantic_summary": {
            "canonical_json_sha256":
                SEMANTIC_VETO_HISTORICAL_CANONICAL_SHA256,
        },
        "unchanged": True,
    }


def _expected_runtime_eligibility_historical_binding() -> dict[str, Any]:
    return {
        "after_sha256": RUNTIME_ELIGIBILITY_HISTORICAL_SHA256,
        "before_sha256": RUNTIME_ELIGIBILITY_HISTORICAL_SHA256,
        "bytes_unchanged": True,
        "repository_path": RUNTIME_ELIGIBILITY_REPOSITORY_PATH,
        "semantic_summary": dict(RUNTIME_ELIGIBILITY_SEMANTIC_SUMMARY),
        "unchanged": True,
    }


def _attribution_predicate_ast_projection_sha256(source: bytes) -> str:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise TransitionError(
            "semantic_veto attribution-predicate source is invalid"
        ) from exc

    assignments: dict[str, ast.AST] = {}
    functions: list[ast.AST] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id in ATTRIBUTION_PREDICATE_GLOBALS
                ):
                    if target.id in assignments:
                        raise TransitionError(
                            "semantic_veto attribution-predicate global "
                            "is duplicated"
                        )
                    assignments[target.id] = node
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == ATTRIBUTION_PREDICATE_FUNCTION
        ):
            functions.append(node)
    if (
        set(assignments) != set(ATTRIBUTION_PREDICATE_GLOBALS)
        or len(functions) != 1
    ):
        raise TransitionError(
            "semantic_veto attribution-predicate projection is incomplete"
        )

    nodes = [
        assignments[name]
        for name in ATTRIBUTION_PREDICATE_GLOBALS
    ] + functions
    projection = [
        ast.dump(node, annotate_fields=True, include_attributes=False)
        for node in nodes
    ]
    return _sha256_json(projection)


def _validate_attribution_predicate_source(
    *,
    root: Path,
    expected_sha256: str,
) -> None:
    formatter_path = _resolved_beneath(root, ATTRIBUTION_PREDICATE_PATH)
    try:
        formatter_bytes = formatter_path.read_bytes()
    except OSError as exc:
        raise TransitionError(
            "semantic_veto attribution-predicate source cannot be read"
        ) from exc
    if _sha256_bytes(formatter_bytes) != expected_sha256:
        raise TransitionError(
            "semantic_veto attribution-predicate source hash differs"
        )
    if _attribution_predicate_ast_projection_sha256(formatter_bytes) != (
        ATTRIBUTION_PREDICATE_AST_PROJECTION_SHA256
    ):
        raise TransitionError(
            "semantic_veto attribution-predicate semantics differ"
        )


def _validate_semantic_veto_historical_source(
    *,
    root: Path,
    current_bytes: bytes,
    current_value: dict[str, Any],
    item: dict[str, Any],
) -> None:
    if item != _expected_semantic_veto_historical_binding():
        raise TransitionError(
            "semantic_veto historical invariant binding differs"
        )
    if _sha256_bytes(current_bytes) != SEMANTIC_VETO_HISTORICAL_SHA256:
        raise TransitionError("semantic_veto historical manifest hash differs")
    if current_value.get("source_file_hashes", {}).get(
        "attribution_predicate"
    ) != {
        "path": ATTRIBUTION_PREDICATE_PATH,
        "sha256": ATTRIBUTION_PREDICATE_HISTORICAL_SHA256,
    }:
        raise TransitionError(
            "semantic_veto historical attribution-predicate binding differs"
        )
    _validate_attribution_predicate_source(
        root=root,
        expected_sha256=ATTRIBUTION_PREDICATE_HISTORICAL_SHA256,
    )


def _validate_semantic_veto_provenance_successor(
    *,
    root: Path,
    current_bytes: bytes,
    current_value: dict[str, Any],
    item: dict[str, Any],
) -> None:
    """Accept one exact source-pin-only successor to a historical invariant.

    Replacing the successor formatter hash with the historically bound hash
    must reproduce both the historical raw manifest SHA-256 and its canonical
    semantic summary.  This proves that no policy, pair, count, quotation,
    image or other provenance field changed.
    """
    if item != _expected_semantic_veto_historical_binding():
        raise TransitionError(
            "semantic_veto historical invariant binding differs"
        )
    if _sha256_bytes(current_bytes) != SEMANTIC_VETO_SUCCESSOR_SHA256:
        raise TransitionError(
            "semantic_veto provenance successor hash differs"
        )

    source_hashes = current_value.get("source_file_hashes")
    if not isinstance(source_hashes, dict):
        raise TransitionError(
            "semantic_veto provenance successor source hashes are invalid"
        )
    predicate_binding = source_hashes.get("attribution_predicate")
    if predicate_binding != {
        "path": ATTRIBUTION_PREDICATE_PATH,
        "sha256": ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256,
    }:
        raise TransitionError(
            "semantic_veto attribution-predicate successor binding differs"
        )
    eligibility_binding = source_hashes.get(
        "runtime_eligible_quote_manifest"
    )
    if eligibility_binding != {
        "path": RUNTIME_ELIGIBILITY_REPOSITORY_PATH,
        "sha256": RUNTIME_ELIGIBILITY_SUCCESSOR_SHA256,
    }:
        raise TransitionError(
            "semantic_veto runtime-eligibility successor binding differs"
        )
    _validate_attribution_predicate_source(
        root=root,
        expected_sha256=ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256,
    )

    successor_hash = ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256.encode("ascii")
    historical_hash = ATTRIBUTION_PREDICATE_HISTORICAL_SHA256.encode("ascii")
    eligibility_successor_hash = (
        RUNTIME_ELIGIBILITY_SUCCESSOR_SHA256.encode("ascii")
    )
    eligibility_historical_hash = (
        RUNTIME_ELIGIBILITY_HISTORICAL_SHA256.encode("ascii")
    )
    if (
        current_bytes.count(successor_hash) != 1
        or historical_hash in current_bytes
        or current_bytes.count(eligibility_successor_hash) != 1
        or eligibility_historical_hash in current_bytes
    ):
        raise TransitionError(
            "semantic_veto provenance successor byte binding is ambiguous"
        )
    reconstructed_bytes = current_bytes.replace(
        successor_hash,
        historical_hash,
        1,
    ).replace(
        eligibility_successor_hash,
        eligibility_historical_hash,
        1,
    )
    if _sha256_bytes(reconstructed_bytes) != SEMANTIC_VETO_HISTORICAL_SHA256:
        raise TransitionError(
            "semantic_veto provenance successor changes additional bytes"
        )

    reconstructed_value = copy.deepcopy(current_value)
    reconstructed_value["source_file_hashes"]["attribution_predicate"][
        "sha256"
    ] = ATTRIBUTION_PREDICATE_HISTORICAL_SHA256
    reconstructed_value["source_file_hashes"][
        "runtime_eligible_quote_manifest"
    ]["sha256"] = RUNTIME_ELIGIBILITY_HISTORICAL_SHA256
    if _sha256_json(reconstructed_value) != (
        SEMANTIC_VETO_HISTORICAL_CANONICAL_SHA256
    ):
        raise TransitionError(
            "semantic_veto provenance successor changes semantics"
        )


def _validate_runtime_eligibility_provenance_successor(
    *,
    root: Path,
    current_bytes: bytes,
    current_value: dict[str, Any],
    item: dict[str, Any],
) -> None:
    """Accept the exact predicate-pin-only ordinary-cycle successor."""
    if item != _expected_runtime_eligibility_historical_binding():
        raise TransitionError(
            "ordinary_cycle historical invariant binding differs"
        )
    if _sha256_bytes(current_bytes) != RUNTIME_ELIGIBILITY_SUCCESSOR_SHA256:
        raise TransitionError(
            "ordinary_cycle provenance successor hash differs"
        )
    source_hashes = current_value.get("source_file_hashes")
    if (
        not isinstance(source_hashes, dict)
        or source_hashes.get("attribution_predicate")
        != ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256
    ):
        raise TransitionError(
            "ordinary_cycle attribution-predicate successor binding differs"
        )
    _validate_attribution_predicate_source(
        root=root,
        expected_sha256=ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256,
    )
    successor_hash = ATTRIBUTION_PREDICATE_SUCCESSOR_SHA256.encode("ascii")
    historical_hash = (
        RUNTIME_ELIGIBILITY_HISTORICAL_PREDICATE_SHA256.encode("ascii")
    )
    if (
        current_bytes.count(successor_hash) != 1
        or historical_hash in current_bytes
    ):
        raise TransitionError(
            "ordinary_cycle provenance successor byte binding is ambiguous"
        )
    reconstructed_bytes = current_bytes.replace(
        successor_hash,
        historical_hash,
        1,
    )
    if _sha256_bytes(reconstructed_bytes) != (
        RUNTIME_ELIGIBILITY_HISTORICAL_SHA256
    ):
        raise TransitionError(
            "ordinary_cycle provenance successor changes additional bytes"
        )
    if item.get("semantic_summary") != _invariant_summary(
        "ordinary_cycle", current_value
    ):
        raise TransitionError("ordinary_cycle invariant semantics differ")


def load_and_validate_transition(
    path: Path,
    *,
    research_dir: Path,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Validate a frozen transition against current runtime-consumed inputs.

    The predecessor bytes are intentionally not required at runtime.  Their
    source-role-audit identity is chained through the immutable predecessor
    transition, while the current file identities and unchanged invariants are
    rechecked directly.
    """
    manifest = _load_json(path)
    if (
        manifest.get("schema_version") != SCHEMA_VERSION
        or manifest.get("manifest_kind") != MANIFEST_KIND
        or manifest.get("transition_status") != TRANSITION_STATUS
        or manifest.get("policy_transition") != {
            "from": BEFORE_POLICY,
            "source_role_policy": SOURCE_ROLE_POLICY,
            "to": AFTER_POLICY,
        }
        or manifest.get("changed_json_path_class_counts")
        != EXPECTED_CHANGE_COUNTS
    ):
        raise TransitionError("transport-redaction transition header differs")
    scope = manifest.get("scope")
    if not isinstance(scope, dict) or scope != {
        "quote_ids": [EXPECTED_QUOTE_ID],
        "quote_ids_sha256": _ids_sha256([EXPECTED_QUOTE_ID]),
        "resolution_item_ids": [EXPECTED_RESOLUTION_ITEM_ID],
        "source_ids": [EXPECTED_SOURCE_ID],
        "source_indexes": [EXPECTED_SOURCE_INDEX],
        "openai_research_quote_ids": sorted({
            quote_id for quote_id, _index in EXPECTED_OPENAI_RESEARCH_PATHS
        }),
        "openai_research_rejected_source_paths": [
            {"quote_id": quote_id, "rejected_source_index": index}
            for quote_id, index in EXPECTED_OPENAI_RESEARCH_PATHS
        ],
    }:
        raise TransitionError("transport-redaction transition scope differs")
    redaction = manifest.get("transport_redaction")
    if not isinstance(redaction, dict) or redaction != {
        "final_url_change_count": 1,
        "redirect_url_change_count": 1,
        "removed_query_keys": list(EXPECTED_REMOVED_QUERY_KEYS),
        "removed_query_value_count": 12,
        "retained_query_bytes_and_order_unchanged": True,
        "signed_host": EXPECTED_SIGNED_HOST,
        "openai_research": {
            "removed_query_keys": list(
                EXPECTED_OPENAI_REMOVED_QUERY_KEYS
            ),
            "removed_query_value_count": 37,
            "retained_query_bytes_and_order_unchanged": True,
            "signed_hosts": sorted(EXPECTED_OPENAI_SIGNED_HOSTS),
            "url_change_count": 7,
        },
    }:
        raise TransitionError("transport-redaction declaration differs")

    research_dir = research_dir.resolve()
    root = root.resolve()
    current_resolution = research_dir / RESOLUTION_FILENAME
    current_openai = research_dir / OPENAI_RESEARCH_FILENAME
    current_audit = research_dir / AUDIT_FILENAME
    files = manifest.get("files")
    if (
        not isinstance(files, dict)
        or set(files) != {
            RESOLUTION_FILENAME,
            OPENAI_RESEARCH_FILENAME,
            AUDIT_FILENAME,
        }
        or any(
            not isinstance(item, dict)
            or set(item) != {"before_sha256", "after_sha256"}
            or not all(_is_sha256(item.get(name)) for name in item)
            for item in files.values()
        )
    ):
        raise TransitionError("transport-redaction file bindings are invalid")
    if (
        files.get(RESOLUTION_FILENAME, {}).get("after_sha256")
        != _sha256_file(current_resolution)
        or files.get(OPENAI_RESEARCH_FILENAME, {}).get("after_sha256")
        != _sha256_file(current_openai)
        or files.get(AUDIT_FILENAME, {}).get("after_sha256")
        != _sha256_file(current_audit)
    ):
        raise TransitionError("current transport-redaction input hash differs")

    resolution = _load_json(current_resolution)
    _load_json(current_openai)
    audit = _load_json(current_audit)
    if resolution.get("policy_version") != AFTER_POLICY:
        raise TransitionError("current resolution policy differs")
    if audit.get("policy_version") != SOURCE_ROLE_POLICY:
        raise TransitionError("current source-role policy differs")
    if audit.get("source_file_hashes", {}).get(RESOLUTION_FILENAME) != (
        files[RESOLUTION_FILENAME]["after_sha256"]
    ):
        raise TransitionError("current source-role resolution binding differs")
    if audit.get("source_file_hashes", {}).get(
        OPENAI_RESEARCH_FILENAME
    ) != files[OPENAI_RESEARCH_FILENAME]["after_sha256"]:
        raise TransitionError(
            "current source-role OpenAI research binding differs"
        )

    predecessor_binding = manifest.get("predecessor_transition")
    predecessor_path = root / PREDECESSOR_TRANSITION_FILENAME
    predecessor = _load_json(predecessor_path)
    if (
        not isinstance(predecessor_binding, dict)
        or predecessor_binding.get("path")
        != PREDECESSOR_TRANSITION_FILENAME
        or predecessor_binding.get("sha256") != _sha256_file(predecessor_path)
        or predecessor_binding.get("source_role_audit_sha256")
        != files[AUDIT_FILENAME]["before_sha256"]
        or predecessor.get("input_hashes", {}).get(AUDIT_FILENAME)
        != files[AUDIT_FILENAME]["before_sha256"]
    ):
        raise TransitionError("predecessor transition chain differs")

    proofs = manifest.get("proofs")
    if (
        not isinstance(proofs, dict)
        or set(proofs) != {
            "evidence_semantics_sha256",
            "openai_research_transport_normalised_sha256",
            "public_semantics_sha256",
            "resolution_transport_normalised_sha256",
        }
        or not all(_is_sha256(value) for value in proofs.values())
    ):
        raise TransitionError("transport-redaction semantic proofs are invalid")

    invariants = manifest.get("unchanged_invariants")
    if (
        not isinstance(invariants, dict)
        or set(invariants) != REQUIRED_INVARIANT_LABELS
    ):
        raise TransitionError("unchanged invariant bindings are invalid")
    for label, item in sorted(invariants.items()):
        if (
            not isinstance(item, dict)
            or item.get("unchanged") is not True
            or not isinstance(item.get("repository_path"), str)
        ):
            raise TransitionError(f"{label} invariant binding is invalid")
        current_path = _resolved_beneath(root, item["repository_path"])
        try:
            current_bytes = current_path.read_bytes()
        except OSError as exc:
            raise TransitionError(
                f"{label} invariant cannot be read"
            ) from exc
        value = _load_json_bytes(
            current_bytes,
            source_name=current_path.name,
        )
        current_sha256 = _sha256_bytes(current_bytes)
        exact_historical_context_gate_binding = (
            label == "historical_context_gate"
            and (
                item.get("repository_path")
                == HISTORICAL_CONTEXT_GATE_REPOSITORY_PATH
                or item.get("after_sha256") in {
                    HISTORICAL_CONTEXT_GATE_HISTORICAL_SHA256,
                    HISTORICAL_CONTEXT_GATE_SUCCESSOR_SHA256,
                }
                or current_sha256 in {
                    HISTORICAL_CONTEXT_GATE_HISTORICAL_SHA256,
                    HISTORICAL_CONTEXT_GATE_SUCCESSOR_SHA256,
                }
            )
        )
        if exact_historical_context_gate_binding:
            if current_sha256 == HISTORICAL_CONTEXT_GATE_HISTORICAL_SHA256:
                if item != (
                    _expected_historical_context_gate_historical_binding()
                ):
                    raise TransitionError(
                        "historical_context_gate historical invariant "
                        "binding differs"
                    )
                if item.get("semantic_summary") != _invariant_summary(
                    "historical_context_gate", value
                ):
                    raise TransitionError(
                        "historical_context_gate invariant semantics differ"
                    )
            else:
                _validate_historical_context_gate_provenance_successor(
                    root=root,
                    current_bytes=current_bytes,
                    current_value=value,
                    item=item,
                )
            continue
        exact_runtime_eligibility_binding = (
            label == "ordinary_cycle"
            and (
                item.get("repository_path")
                == RUNTIME_ELIGIBILITY_REPOSITORY_PATH
                or item.get("after_sha256") in {
                    RUNTIME_ELIGIBILITY_HISTORICAL_SHA256,
                    RUNTIME_ELIGIBILITY_SUCCESSOR_SHA256,
                }
                or current_sha256 in {
                    RUNTIME_ELIGIBILITY_HISTORICAL_SHA256,
                    RUNTIME_ELIGIBILITY_SUCCESSOR_SHA256,
                }
            )
        )
        if exact_runtime_eligibility_binding:
            if current_sha256 == RUNTIME_ELIGIBILITY_HISTORICAL_SHA256:
                if item != _expected_runtime_eligibility_historical_binding():
                    raise TransitionError(
                        "ordinary_cycle historical invariant binding differs"
                    )
            else:
                _validate_runtime_eligibility_provenance_successor(
                    root=root,
                    current_bytes=current_bytes,
                    current_value=value,
                    item=item,
                )
            continue
        exact_semantic_veto_binding = (
            label == "semantic_veto"
            and (
                item.get("repository_path")
                == SEMANTIC_VETO_REPOSITORY_PATH
                or item.get("after_sha256") in {
                    SEMANTIC_VETO_HISTORICAL_SHA256,
                    SEMANTIC_VETO_SUCCESSOR_SHA256,
                }
                or current_sha256 in {
                    SEMANTIC_VETO_HISTORICAL_SHA256,
                    SEMANTIC_VETO_SUCCESSOR_SHA256,
                }
            )
        )
        if exact_semantic_veto_binding:
            if current_sha256 == SEMANTIC_VETO_HISTORICAL_SHA256:
                _validate_semantic_veto_historical_source(
                    root=root,
                    current_bytes=current_bytes,
                    current_value=value,
                    item=item,
                )
            else:
                _validate_semantic_veto_provenance_successor(
                    root=root,
                    current_bytes=current_bytes,
                    current_value=value,
                    item=item,
                )
            continue
        if item.get("after_sha256") != current_sha256:
            if label != "semantic_veto":
                raise TransitionError(f"{label} invariant hash differs")
            _validate_semantic_veto_provenance_successor(
                root=root,
                current_bytes=current_bytes,
                current_value=value,
                item=item,
            )
            continue
        if item.get("semantic_summary") != _invariant_summary(label, value):
            raise TransitionError(f"{label} invariant semantics differ")
    return manifest


def rebind_evidence_truth_audit(
    truth_audit_path: Path,
    transition_manifest_path: Path,
    *,
    research_dir: Path,
    root: Path = ROOT,
) -> dict[str, Any]:
    """Rebind one history-derived truth audit after transport-only redaction.

    The historical reply bytes used to build this audit may no longer be
    available.  Rebuilding it from a later history would change its reviewed
    population.  The validated transport transition proves that evidence and
    public semantics are unchanged, so this operation permits only replacing
    the predecessor source-role-audit hash with the transition's current hash.
    """
    manifest = load_and_validate_transition(
        transition_manifest_path,
        research_dir=research_dir,
        root=root,
    )
    truth = _load_json(truth_audit_path)
    if (
        truth.get("schema_version") != EVIDENCE_TRUTH_SCHEMA_VERSION
        or truth.get("audit_kind") != EVIDENCE_TRUTH_AUDIT_KIND
    ):
        raise TransitionError("evidence-truth audit identity differs")
    input_hashes = truth.get("input_hashes")
    if (
        not isinstance(input_hashes, dict)
        or not input_hashes
        or any(
            not isinstance(name, str) or not _is_sha256(value)
            for name, value in input_hashes.items()
        )
    ):
        raise TransitionError("evidence-truth input hashes are invalid")

    file_bindings = manifest.get("files")
    if not isinstance(file_bindings, dict):
        raise TransitionError("transport-redaction file bindings are invalid")
    audit_binding = file_bindings.get(AUDIT_FILENAME)
    if not isinstance(audit_binding, dict):
        raise TransitionError("source-role transition binding is invalid")
    predecessor_hash = audit_binding.get("before_sha256")
    current_hash = audit_binding.get("after_sha256")
    if (
        not _is_sha256(predecessor_hash)
        or not _is_sha256(current_hash)
        or predecessor_hash == current_hash
    ):
        raise TransitionError("source-role transition hashes are invalid")
    if input_hashes.get(AUDIT_FILENAME) != predecessor_hash:
        raise TransitionError(
            "evidence-truth source-role hash does not match transition "
            "predecessor"
        )

    result = copy.deepcopy(truth)
    result["input_hashes"][AUDIT_FILENAME] = current_hash
    before_semantics = copy.deepcopy(truth)
    after_semantics = copy.deepcopy(result)
    before_semantics["input_hashes"].pop(AUDIT_FILENAME)
    after_semantics["input_hashes"].pop(AUDIT_FILENAME)
    if before_semantics != after_semantics:
        raise TransitionError(
            "evidence-truth rebind changed non-binding semantics"
        )
    return result


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical_json(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _atomic_write_pretty_json(path: Path, value: dict[str, Any]) -> None:
    """Write a repository-style deterministic JSON artefact atomically."""
    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _invariant_pairs(
    rows: Sequence[Sequence[str]],
) -> dict[str, InvariantPair]:
    result: dict[str, InvariantPair] = {}
    for label, before, after, repository_path in rows:
        if label in result:
            raise TransitionError(f"duplicate unchanged invariant: {label}")
        result[label] = InvariantPair(
            before=Path(before),
            after=Path(after),
            repository_path=repository_path,
        )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    """Build, validate, or apply the transport-URL redaction transition."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    for name in (
        "predecessor-resolution",
        "current-resolution",
        "predecessor-openai-research",
        "current-openai-research",
        "predecessor-source-role-audit",
        "current-source-role-audit",
        "predecessor-transition",
    ):
        build.add_argument(f"--{name}", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument(
        "--unchanged-invariant",
        action="append",
        default=[],
        nargs=4,
        metavar=("LABEL", "BEFORE", "AFTER", "REPOSITORY_PATH"),
    )
    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--research-dir", type=Path, required=True)
    validate.add_argument("--root", type=Path, default=ROOT)
    rebind = subparsers.add_parser("rebind-evidence-truth")
    rebind.add_argument("--truth-audit", type=Path, required=True)
    rebind.add_argument("--manifest", type=Path, required=True)
    rebind.add_argument("--research-dir", type=Path, required=True)
    rebind.add_argument("--root", type=Path, default=ROOT)
    rebind.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "build":
        manifest = build_transition(
            predecessor_resolution=args.predecessor_resolution,
            current_resolution=args.current_resolution,
            predecessor_openai_research=args.predecessor_openai_research,
            current_openai_research=args.current_openai_research,
            predecessor_source_role_audit=args.predecessor_source_role_audit,
            current_source_role_audit=args.current_source_role_audit,
            predecessor_transition=args.predecessor_transition,
            unchanged_invariants=_invariant_pairs(
                args.unchanged_invariant
            ),
        )
        _atomic_write_json(args.output, manifest)
        print(json.dumps({
            "manifest_sha256": _sha256_file(args.output),
            "quote_ids": manifest["scope"]["quote_ids"],
            "transition_status": manifest["transition_status"],
        }, indent=2, sort_keys=True))
        return 0

    if args.command == "rebind-evidence-truth":
        truth = rebind_evidence_truth_audit(
            args.truth_audit,
            args.manifest,
            research_dir=args.research_dir,
            root=args.root,
        )
        _atomic_write_pretty_json(args.output, truth)
        print(json.dumps({
            "evidence_truth_sha256": _sha256_file(args.output),
            "source_role_audit_sha256": truth["input_hashes"][
                AUDIT_FILENAME
            ],
            "transition_status": TRANSITION_STATUS,
        }, indent=2, sort_keys=True))
        return 0

    manifest = load_and_validate_transition(
        args.manifest,
        research_dir=args.research_dir,
        root=args.root,
    )
    print(json.dumps({
        "manifest_sha256": _sha256_file(args.manifest),
        "predecessor_source_role_audit_sha256": manifest[
            "predecessor_transition"
        ]["source_role_audit_sha256"],
        "transition_status": manifest["transition_status"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
