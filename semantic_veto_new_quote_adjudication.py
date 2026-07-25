#!/usr/bin/env python3
"""Adjudicate one newly runtime-eligible quote against the fixed image corpus.

This is a narrow, resumable transition around the existing v3 semantic-veto
contracts, deterministic rules, typed provider transport, and compiled shadow
manifest.  It never changes an existing pair decision.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import random
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from quote_attribution_cleanup import semantic_pair_coverage
from quote_image_metadata_remediation import (
    FIRST_PROMPT_VERSION,
    IMAGE_CONTRACT_VERSION,
    MODEL,
    MultimodalPrompt,
    PAIR_MAX_OUTPUT_TOKENS,
    PAIR_SCHEMA_VERSION,
    QUOTE_CONTRACT_VERSION,
    RULE_VERSION,
    SECOND_PROMPT_VERSION,
    _adjudication_contract,
    _adjudication_image,
    adjudication_prompt,
    deterministic_pair_decision,
    digest,
    initialise_remediation_ledger,
    normalise_unsupported_model_literalism,
    pair_id,
    text_digest,
)
from semantic_alignment.io import (
    atomic_write_json,
    atomic_write_text,
    read_json,
    sha256_file,
)
from semantic_alignment.quote_image_semantic_veto import (
    ATTRIBUTION_CLEANED_V3_POLICY_VERSION,
    ShadowRuntime,
    validate_compiled_manifest,
)
from semantic_alignment.relation_aware_veto import (
    DeveloperBatchRunner,
    RelationRouter,
    pair_response_schema,
    transport_preflight,
    validate_pair_response,
)


ROOT = Path(__file__).resolve().parent
TARGET_QUOTE_ID = "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729"
EXPECTED_QUOTE_COUNT = 611
EXPECTED_IMAGE_COUNT = 91
EXPECTED_PAIR_UNIVERSE = 55_601
RUN_SCHEMA_VERSION = 1
RUN_VERSION = "new-runtime-quote-semantic-veto-adjudication-v1"
DEFAULT_RUN_DIR = (
    ROOT
    / "semantic_alignment_research"
    / "new_quote_semantic_veto_adjudication_001"
)
BASE_MANIFEST = (
    ROOT
    / "semantic_alignment_research"
    / "quote_attribution_cleanup_001"
    / "deployment_candidate"
    / "material_veto_v3_shadow_manifest.json"
)
PACKETS_PATH = (
    ROOT
    / "semantic_alignment_research"
    / "quote_research_full_001"
    / "research_packets.json"
)
IMAGE_CONTRACTS_PATH = (
    ROOT
    / "semantic_alignment_research"
    / "quote_image_metadata_remediation_001"
    / "image_contracts_v3.jsonl"
)


class NewQuoteAdjudicationError(RuntimeError):
    """Raised when the targeted transition cannot be proved safe."""


def canonical_bytes(value: Any) -> bytes:
    """Return stable JSON bytes for hashing and byte-identity assertions."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    """Return the SHA-256 of stable JSON bytes."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def credential_project_root() -> Path:
    """Return the project root containing the existing provider environment.

    An isolated Git worktree intentionally does not contain the ignored
    production environment file.  This explicit indirection lets the existing
    transport loader read it without copying, logging, or otherwise exposing
    credentials.
    """
    configured = os.getenv("MRS_MTHATCHER_CREDENTIAL_PROJECT_ROOT")
    return Path(configured).resolve() if configured else ROOT


def jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSON-lines file."""
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_target_packet(path: Path = PACKETS_PATH) -> dict[str, Any]:
    """Load the exact canonical packet for the requested quotation."""
    packets = read_json(path).get("items") or {}
    packet = packets.get(TARGET_QUOTE_ID)
    if not isinstance(packet, dict) or packet.get("quote_id") != TARGET_QUOTE_ID:
        raise NewQuoteAdjudicationError("target canonical packet is missing")
    if packet.get("speaker") != "Margaret Thatcher":
        raise NewQuoteAdjudicationError("target canonical speaker is not Margaret Thatcher")
    if packet.get("verification_status") not in {"exact", "variant"}:
        raise NewQuoteAdjudicationError("target canonical wording is not verified")
    return packet


def build_new_quote_contract(packet: dict[str, Any]) -> dict[str, Any]:
    """Build the general v3 contract for a new abstract attributed quotation.

    The fallback is deliberately conservative.  It does not infer a visually
    required entity, event, relationship, action, period, or actor merely from
    contextual packet fields.  If the quotation itself names any concrete
    person other than Thatcher or encodes an event/relationship requirement,
    this fallback refuses the input rather than broadening visual permissions.
    """
    quote_text = str(packet.get("quote_text") or "")
    entities = [
        str(value).strip()
        for value in (packet.get("entities") or [])
        if str(value).strip()
    ]
    concrete_entities = [
        value
        for value in entities
        if value != "Margaret Thatcher"
        and " " in value
        and value.casefold() not in {"conservative party", "labour party"}
    ]
    editorial = packet.get("editorial_guidance") or {}
    historical_requirements = list(editorial.get("historical_requirements") or [])
    if concrete_entities:
        raise NewQuoteAdjudicationError(
            "new-quote fallback cannot infer visual handling for concrete named entities"
        )
    if not packet.get("intended_argument") or not packet.get("literal_meaning"):
        raise NewQuoteAdjudicationError("new-quote packet lacks decision-relevant meaning")
    if not historical_requirements:
        raise NewQuoteAdjudicationError("new-quote packet lacks source-context requirements")

    return {
        "quote_id": packet["quote_id"],
        "quote_text": quote_text,
        "verified_text": str(packet.get("verified_text") or ""),
        "verification_status": packet["verification_status"],
        "research_confidence": packet["research_confidence"],
        "canonical_speaker": "Margaret Thatcher",
        "thatcher_attribution_status": "confirmed_thatcher",
        "dominant_proposition": str(packet["intended_argument"]),
        "claim_type": "abstract_principle",
        "mentioned_entities": entities,
        "visually_required_entities": [],
        "mentioned_relationships": [],
        "visually_required_relationships": [],
        "source_occasion": str(packet.get("source_event") or ""),
        "source_date": str(packet.get("date") or ""),
        "visual_event_requirement": None,
        "visual_period_requirement": None,
        "required_actor_roles": [],
        "required_actor_count_minimum": 0,
        "required_action": None,
        "required_transition": None,
        "literal_visualisation_required": False,
        "neutral_portrait_allowed": True,
        "symbolic_image_allowed": True,
        "multi_person_image_required": False,
        "relationship_evidence_required": False,
        "event_specific_image_required": False,
        "hard_visual_conflicts": [
            "an image that affirmatively presents political relabelling as proof that the underlying beliefs changed",
            "an image whose established dominant story reverses the quotation's criticism of political rebranding",
        ],
        "material_false_implications": list(
            editorial.get("common_visual_mistakes") or []
        ),
        "evidence_fields": [
            "quote_text",
            "verified_text",
            "intended_argument",
            "literal_meaning",
            "editorial_guidance",
        ],
        "contract_confidence": str(packet.get("research_confidence") or "high"),
        "contract_version": QUOTE_CONTRACT_VERSION,
        "source_contract_v2_sha256": None,
        "field_provenance": {
            "canonical_speaker": {
                "source": "canonical_packet",
                "path": f"research_packets.json/items/{packet['quote_id']}/speaker",
            },
            "dominant_proposition": {
                "source": "canonical_packet",
                "path": f"research_packets.json/items/{packet['quote_id']}/intended_argument",
            },
            "visual_requirements": {
                "source": "fail_closed_new_quote_fallback",
                "rule": "no concrete visual requirement inferred from contextual metadata",
            },
        },
    }


def load_image_contracts(path: Path = IMAGE_CONTRACTS_PATH) -> dict[str, dict[str, Any]]:
    """Load and verify the fixed 91-image contract corpus."""
    records = {row["image_hash"]: row for row in jsonl(path)}
    if len(records) != EXPECTED_IMAGE_COUNT:
        raise NewQuoteAdjudicationError("image contract count is not 91")
    if any(row.get("contract_version") != IMAGE_CONTRACT_VERSION for row in records.values()):
        raise NewQuoteAdjudicationError("image contract version mismatch")
    if any(not Path(str(row.get("path") or "")).is_file() for row in records.values()):
        raise NewQuoteAdjudicationError("one or more image contract files are unavailable")
    return records


def verify_base_manifest(
    manifest: dict[str, Any],
    *,
    verify_runtime_sources: bool,
    manifest_path: Path = BASE_MANIFEST,
) -> dict[str, Any]:
    """Verify the starting 611×91 manifest and exact target coverage gap."""
    old_exact = {
        "quote_count": EXPECTED_QUOTE_COUNT,
        "image_count": EXPECTED_IMAGE_COUNT,
        "pair_count": 22_066,
        "allow_count": 21_938,
        "veto_count": 128,
        "adjudicated_unknown_pair_count": 167,
        "not_adjudicated_pair_count": 33_368,
        "total_authorised_pair_count": EXPECTED_PAIR_UNIVERSE,
        "resolved_pair_count": 22_066,
    }
    mismatches = {
        key: {"expected": expected, "actual": manifest.get(key)}
        for key, expected in old_exact.items()
        if manifest.get(key) != expected
    }
    if mismatches:
        raise NewQuoteAdjudicationError(
            f"base manifest exact-count mismatch: {mismatches}"
        )
    pairs = manifest.get("pairs") or {}
    unknown_pairs = manifest.get("adjudicated_unknown_pairs") or {}
    if set(pairs) & set(unknown_pairs):
        raise NewQuoteAdjudicationError("base resolved and unknown rows overlap")
    decision_counts = Counter(row.get("decision") for row in pairs.values())
    if decision_counts != {"allow": 21_938, "veto": 128}:
        raise NewQuoteAdjudicationError("base pair decisions disagree with counts")
    pair_ids = [
        str(row.get("source_pair_id") or "")
        for row in [*pairs.values(), *unknown_pairs.values()]
    ]
    if (
        len(pair_ids) != len(set(pair_ids))
        or any(len(value) != 64 for value in pair_ids)
    ):
        raise NewQuoteAdjudicationError("base pair IDs are invalid or duplicated")
    audit = {
        "valid": True,
        **old_exact,
        "source_pair_ids_unique": True,
        "transition_base_policy": manifest.get("policy_version"),
    }
    if (
        manifest.get("policy_version") != ATTRIBUTION_CLEANED_V3_POLICY_VERSION
        or audit.get("quote_count") != EXPECTED_QUOTE_COUNT
        or audit.get("image_count") != EXPECTED_IMAGE_COUNT
        or manifest.get("total_authorised_pair_count") != EXPECTED_PAIR_UNIVERSE
    ):
        raise NewQuoteAdjudicationError("base manifest policy or dimensions mismatch")
    coverage = (manifest.get("quote_pair_coverage") or {}).get(TARGET_QUOTE_ID)
    if not isinstance(coverage, dict):
        raise NewQuoteAdjudicationError("target quote is absent from base manifest coverage")
    expected = {
        "adjudicated_unknown_count": 0,
        "allow_count": 0,
        "authorised_image_count": EXPECTED_IMAGE_COUNT,
        "complete_pair_coverage": False,
        "fully_resolved_pair_coverage": False,
        "global_no_safe_image": False,
        "not_adjudicated_count": EXPECTED_IMAGE_COUNT,
        "observed_pair_count": 0,
        "resolved_pair_count": 0,
        "veto_count": 0,
    }
    if coverage != expected:
        raise NewQuoteAdjudicationError(
            "target does not have exactly 91 untouched not-adjudicated rows"
        )
    target_keys = {
        key
        for key, row in {
            **(manifest.get("pairs") or {}),
            **(manifest.get("adjudicated_unknown_pairs") or {}),
        }.items()
        if row.get("quote_id") == TARGET_QUOTE_ID
    }
    if target_keys:
        raise NewQuoteAdjudicationError("target already has stored adjudications")
    if verify_runtime_sources:
        for name, source in (manifest.get("source_file_hashes") or {}).items():
            if not isinstance(source, dict):
                raise NewQuoteAdjudicationError(
                    f"base source-hash record is invalid: {name}"
                )
            relative = Path(str(source.get("path") or ""))
            path = (ROOT / relative).resolve()
            try:
                path.relative_to(ROOT)
            except ValueError as exc:
                raise NewQuoteAdjudicationError(
                    f"base source path escapes project: {name}"
                ) from exc
            if (
                not path.is_file()
                or sha256_file(path) != source.get("sha256")
            ):
                raise NewQuoteAdjudicationError(
                    f"base source verification failed: {name}"
                )
    return audit


def prepare_pairs(
    quote: dict[str, Any], images: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply existing deterministic policy and return decided/residual rows."""
    decided: list[dict[str, Any]] = []
    residual: list[dict[str, Any]] = []
    for image_hash, image in sorted(images.items()):
        local = deterministic_pair_decision(quote, image)
        row = {
            "pair_id": pair_id(quote["quote_id"], image_hash),
            "quote_id": quote["quote_id"],
            "image_hash": image_hash,
            "image_id": image["image_id"],
            "deterministic_decision": local["decision"],
            "deterministic_basis": local["basis"],
            "deterministic_reasons": copy.deepcopy(local["reasons"]),
        }
        if local["decision"] in {"allow", "veto"}:
            decided.append(
                final_record(
                    quote,
                    image,
                    decision=local["decision"],
                    basis=local["basis"],
                    rationale=(
                        "No affirmative material contradiction is established; "
                        "the source-grounded image is safe as a neutral Thatcher image."
                        if local["decision"] == "allow"
                        else "; ".join(
                            str(reason.get("detail") or reason)
                            for reason in local["reasons"]
                        )
                    ),
                    confidence="high",
                    contradiction_types=[
                        str(reason.get("rule") or "other")
                        for reason in local["reasons"]
                    ],
                    provider_first=None,
                    provider_second=None,
                )
            )
        else:
            residual.append(row)
    return decided, residual


def make_provider_items(
    quote: dict[str, Any],
    images: dict[str, dict[str, Any]],
    rows: Sequence[dict[str, Any]],
    *,
    second_pass: bool,
) -> list[dict[str, Any]]:
    """Create existing typed multimodal adjudication calls for target rows."""
    phase = "second" if second_pass else "first"
    items = []
    for row in sorted(rows, key=lambda value: value["pair_id"]):
        image = images[row["image_hash"]]
        pair = {
            "pair_id": row["pair_id"],
            "quote_id": row["quote_id"],
            "contract": _adjudication_contract(quote),
            "deterministic_contradictions": [],
        }
        prompt_text = adjudication_prompt(image, [pair], second_pass=second_pass)
        prompt = MultimodalPrompt(prompt_text, Path(image["path"]), image["image_hash"])
        legacy_image = {
            "image_id": image["image_id"],
            "dominant_visual_story": image["dominant_visual_story"],
            "known_participants": list(image.get("known_participants") or []),
            "documented_relationships": list(
                image.get("documented_relationships") or []
            ),
        }
        legacy_pairs = [pair]
        logical_id = (
            f"new-quote-v3-{phase}-{image['image_hash'][:16]}-"
            f"{text_digest(prompt_text)[:12]}"
        )
        validator: Callable[[Any], Any] = (
            lambda value,
            legacy_image=legacy_image,
            legacy_pairs=legacy_pairs: validate_pair_response(
                value, legacy_image, legacy_pairs
            )
        )
        items.append(
            {
                "logical_id": logical_id,
                "prompt": prompt,
                "schema": pair_response_schema(1),
                "max_output_tokens": PAIR_MAX_OUTPUT_TOKENS,
                "pair_ids": [row["pair_id"]],
                "expected_pair_ids": [row["pair_id"]],
                "image": image,
                "pair": pair,
                "validator": validator,
            }
        )
    return items


def flatten_provider_results(
    items: Sequence[dict[str, Any]], results: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Flatten schema-validated provider responses by immutable pair ID."""
    output: dict[str, dict[str, Any]] = {}
    for item in items:
        result = results.get(item["logical_id"])
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise NewQuoteAdjudicationError(
                f"provider result missing or incomplete: {item['logical_id']}"
            )
        rows = result.get("response")
        if not isinstance(rows, list) or len(rows) != 1:
            raise NewQuoteAdjudicationError(
                f"provider response cardinality mismatch: {item['logical_id']}"
            )
        row = copy.deepcopy(rows[0])
        pair_id_value = row.get("pair_id")
        if pair_id_value in output or pair_id_value != item["pair_ids"][0]:
            raise NewQuoteAdjudicationError("provider response pair identity mismatch")
        row["transport"] = result.get("transport")
        row["model_version"] = result.get("model_version") or MODEL
        row["logical_call_id"] = item["logical_id"]
        row["cost_usd"] = float(result.get("cost_usd") or 0.0)
        output[pair_id_value] = row
    return output


def normalise_provider_row(
    row: dict[str, Any],
    quote: dict[str, Any],
    image: dict[str, Any],
) -> dict[str, Any]:
    """Apply the existing anti-literalism policy to one provider row."""
    return normalise_unsupported_model_literalism(row, quote, image, [])


def final_record(
    quote: dict[str, Any],
    image: dict[str, Any],
    *,
    decision: str,
    basis: str,
    rationale: str,
    confidence: str,
    contradiction_types: Sequence[str],
    provider_first: dict[str, Any] | None,
    provider_second: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one complete structured decision record."""
    if decision not in {"allow", "veto", "adjudicated_unknown"}:
        raise NewQuoteAdjudicationError(f"invalid final decision: {decision}")
    conflicts = sorted(set(contradiction_types) - {"none"})
    if decision == "allow" and conflicts:
        raise NewQuoteAdjudicationError("allow record contains a contradiction")
    return {
        "quote_id": quote["quote_id"],
        "image_hash": image["image_hash"],
        "image_id": image["image_id"],
        "source_pair_id": pair_id(quote["quote_id"], image["image_hash"]),
        "decision": decision,
        "rationale": str(rationale).strip(),
        "contradicted_quotation_concept": (
            str(quote["dominant_proposition"])
            if decision == "veto"
            else "none — no affirmative material contradiction established"
            if decision == "allow"
            else str(quote["dominant_proposition"])
        ),
        "conflicting_image_concept": (
            str((provider_first or {}).get("dominant_visual_message") or image["dominant_visual_story"])
            if decision != "allow"
            else "none — image does not affirmatively contradict the quotation"
        ),
        "confidence": confidence,
        "policy_version": ATTRIBUTION_CLEANED_V3_POLICY_VERSION,
        "adjudication_rule_version": RULE_VERSION,
        "first_prompt_version": FIRST_PROMPT_VERSION if provider_first else None,
        "second_prompt_version": SECOND_PROMPT_VERSION if provider_second else None,
        "basis": basis,
        "contradiction_types": conflicts,
        "materially_misleading": decision == "veto",
        "provider_first_pass": provider_first,
        "provider_second_pass": provider_second,
    }


def resolve_provider_rows(
    quote: dict[str, Any],
    images: dict[str, dict[str, Any]],
    residual: Sequence[dict[str, Any]],
    first: dict[str, dict[str, Any]],
    second: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the existing two-pass fail-closed resolution policy."""
    output = []
    for row in sorted(residual, key=lambda value: value["pair_id"]):
        image = images[row["image_hash"]]
        first_row = normalise_provider_row(first[row["pair_id"]], quote, image)
        if first_row.get("decision") == "uncertain" or first_row.get("final_decision") == "unknown":
            output.append(
                final_record(
                    quote,
                    image,
                    decision="adjudicated_unknown",
                    basis="provider_first_pass_uncertain",
                    rationale=first_row["reason"],
                    confidence=first_row["confidence"],
                    contradiction_types=first_row.get("contradiction_types") or [],
                    provider_first=first_row,
                    provider_second=None,
                )
            )
            continue
        if first_row.get("final_decision") == "veto":
            output.append(
                final_record(
                    quote,
                    image,
                    decision="veto",
                    basis="provider_first_pass_affirmative_material_contradiction",
                    rationale=first_row["reason"],
                    confidence=first_row["confidence"],
                    contradiction_types=first_row.get("contradiction_types") or [],
                    provider_first=first_row,
                    provider_second=None,
                )
            )
            continue
        second_row = second.get(row["pair_id"])
        if second_row is None:
            raise NewQuoteAdjudicationError(
                f"second pass missing after first-pass allow: {row['pair_id']}"
            )
        second_row = normalise_provider_row(second_row, quote, image)
        if (
            second_row.get("decision") != "uncertain"
            and second_row.get("final_decision") == "allow"
        ):
            raw_passes_both_allow = (
                first_row.get("decision") == "allow"
                and second_row.get("decision") == "allow"
            )
            output.append(
                final_record(
                    quote,
                    image,
                    decision="allow",
                    basis="provider_independent_two_pass_confirmed_safe",
                    rationale=(
                        second_row["reason"]
                        if raw_passes_both_allow
                        else (
                            "Both independently reviewed passes are allow after "
                            "the binding narrow-policy normalisation: non-literal, "
                            "generic-theme or unrelated-event mismatch is not an "
                            "affirmative material contradiction."
                        )
                    ),
                    confidence=(
                        min(
                            [first_row["confidence"], second_row["confidence"]],
                            key=lambda value: {"low": 0, "medium": 1, "high": 2}[
                                value
                            ],
                        )
                        if raw_passes_both_allow
                        else "medium"
                    ),
                    contradiction_types=[],
                    provider_first=first_row,
                    provider_second=second_row,
                )
            )
        else:
            output.append(
                final_record(
                    quote,
                    image,
                    decision="adjudicated_unknown",
                    basis="provider_pass_disagreement_or_uncertainty",
                    rationale=(
                        "Independent passes did not both establish allow: "
                        f"first={first_row.get('decision')}; second={second_row.get('decision')}."
                    ),
                    confidence="low",
                    contradiction_types=sorted(
                        set(first_row.get("contradiction_types") or [])
                        | set(second_row.get("contradiction_types") or [])
                    ),
                    provider_first=first_row,
                    provider_second=second_row,
                )
            )
    return output


def validate_adjudications(
    records: Sequence[dict[str, Any]],
    image_hashes: Iterable[str],
) -> dict[str, Any]:
    """Validate exact one-row coverage and structured decision semantics."""
    hashes = set(image_hashes)
    if len(records) != EXPECTED_IMAGE_COUNT:
        raise NewQuoteAdjudicationError("adjudication row count is not 91")
    keys = {(row.get("quote_id"), row.get("image_hash")) for row in records}
    if keys != {(TARGET_QUOTE_ID, image_hash) for image_hash in hashes}:
        raise NewQuoteAdjudicationError("adjudications do not exactly cover target universe")
    required = {
        "rationale",
        "contradicted_quotation_concept",
        "conflicting_image_concept",
        "confidence",
        "policy_version",
    }
    for row in records:
        if row.get("decision") not in {"allow", "veto", "adjudicated_unknown"}:
            raise NewQuoteAdjudicationError("adjudication has invalid decision")
        if any(not str(row.get(field) or "").strip() for field in required):
            raise NewQuoteAdjudicationError(
                f"adjudication lacks structured rationale: {row.get('source_pair_id')}"
            )
        if row.get("policy_version") != ATTRIBUTION_CLEANED_V3_POLICY_VERSION:
            raise NewQuoteAdjudicationError("adjudication policy version mismatch")
    counts = Counter(row["decision"] for row in records)
    return {
        "valid": True,
        "row_count": len(records),
        "decision_counts": dict(sorted(counts.items())),
        "row_hash": canonical_hash(sorted(records, key=lambda row: row["image_hash"])),
    }


def manifest_pair_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a rich target adjudication to a runtime resolved-pair row."""
    return {
        "decision": row["decision"],
        "deterministic_contradictions": list(row.get("contradiction_types") or []),
        "final_reason": row["rationale"],
        "image_hash": row["image_hash"],
        "image_id": row["image_id"],
        "materially_misleading": row["decision"] == "veto",
        "model_confidence": row["confidence"],
        "model_decision": (
            (row.get("provider_first_pass") or {}).get("decision")
            if row.get("provider_first_pass")
            else None
        ),
        "quote_id": row["quote_id"],
        "selector_score_at_research_time": None,
        "source_pair_id": row["source_pair_id"],
        "veto_reason_codes": (
            list(row.get("contradiction_types") or [])
            if row["decision"] == "veto"
            else []
        ),
        "structured_adjudication": {
            "rationale": row["rationale"],
            "contradicted_quotation_concept": row[
                "contradicted_quotation_concept"
            ],
            "conflicting_image_concept": row["conflicting_image_concept"],
            "confidence": row["confidence"],
            "policy_version": row["policy_version"],
            "basis": row["basis"],
        },
    }


def manifest_unknown_row(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a rich target adjudication to a runtime unknown-pair row."""
    return {
        "adjudication_status": "unknown",
        "deterministic_reasons": list(row.get("contradiction_types") or []),
        "image_hash": row["image_hash"],
        "image_id": row["image_id"],
        "quote_id": row["quote_id"],
        "reason": row["rationale"],
        "source_pair_id": row["source_pair_id"],
        "structured_adjudication": {
            "rationale": row["rationale"],
            "contradicted_quotation_concept": row[
                "contradicted_quotation_concept"
            ],
            "conflicting_image_concept": row["conflicting_image_concept"],
            "confidence": row["confidence"],
            "policy_version": row["policy_version"],
            "basis": row["basis"],
        },
    }


def compile_manifest(
    base: dict[str, Any],
    adjudications: Sequence[dict[str, Any]],
    *,
    adjudication_path: Path,
) -> dict[str, Any]:
    """Add only the target's 91 rows and recompute complete coverage."""
    validate_adjudications(adjudications, base.get("image_hashes") or [])
    result = copy.deepcopy(base)
    old_pairs = copy.deepcopy(result["pairs"])
    old_unknown = copy.deepcopy(result["adjudicated_unknown_pairs"])
    for row in adjudications:
        key = f"{row['quote_id']}:{row['image_hash']}"
        if key in result["pairs"] or key in result["adjudicated_unknown_pairs"]:
            raise NewQuoteAdjudicationError(f"attempted to overwrite pair: {key}")
        if row["decision"] == "adjudicated_unknown":
            result["adjudicated_unknown_pairs"][key] = manifest_unknown_row(row)
        else:
            result["pairs"][key] = manifest_pair_row(row)
    if old_pairs != {
        key: result["pairs"][key] for key in old_pairs
    } or old_unknown != {
        key: result["adjudicated_unknown_pairs"][key] for key in old_unknown
    }:
        raise NewQuoteAdjudicationError("an existing adjudication changed")

    coverage_records = [
        *result["pairs"].values(),
        *(
            {
                **row,
                "decision": "unknown",
                "basis": row.get("reason"),
            }
            for row in result["adjudicated_unknown_pairs"].values()
        ),
    ]
    active_ids = set(result["quote_pair_coverage"])
    coverage = semantic_pair_coverage(
        active_ids,
        coverage_records,
        set(result["image_hashes"]),
    )
    result.update(coverage)
    decisions = Counter(row["decision"] for row in result["pairs"].values())
    result["pair_count"] = len(result["pairs"])
    result["resolved_pair_count"] = len(result["pairs"])
    result["allow_count"] = decisions["allow"]
    result["veto_count"] = decisions["veto"]
    result["source_run_id"] = (
        str(base.get("source_run_id") or "")
        + "-new-quote-0a67-complete"
    )
    result["source_file_hashes"] = copy.deepcopy(base["source_file_hashes"])
    result["source_file_hashes"]["new_quote_pair_adjudications"] = {
        "path": str(adjudication_path.relative_to(ROOT)),
        "sha256": sha256_file(adjudication_path),
    }
    result["validation_evidence"] = copy.deepcopy(
        base.get("validation_evidence") or {}
    )
    result["validation_evidence"].update(
        {
            "new_unadjudicated_quote_count": 0,
            "new_unadjudicated_quote_ids": [],
            "newly_completed_quote_adjudication_count": 1,
            "newly_completed_quote_adjudication_ids": [TARGET_QUOTE_ID],
            "new_quote_adjudication_row_count": EXPECTED_IMAGE_COUNT,
        }
    )
    return result


def prior_entry_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    """Hash every prior matrix entry independently."""
    return {
        f"pairs/{key}": canonical_hash(row)
        for key, row in (manifest.get("pairs") or {}).items()
    } | {
        f"adjudicated_unknown_pairs/{key}": canonical_hash(row)
        for key, row in (manifest.get("adjudicated_unknown_pairs") or {}).items()
    }


def verify_prior_entries(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Prove all pre-existing pair rows remained byte-identical."""
    before_hashes = prior_entry_hashes(before)
    after_hashes = prior_entry_hashes(after)
    changed = [
        key
        for key, value in before_hashes.items()
        if after_hashes.get(key) != value
    ]
    if changed:
        raise NewQuoteAdjudicationError(
            f"pre-existing matrix entries changed: {changed[:3]}"
        )
    return {
        "prior_entry_count": len(before_hashes),
        "prior_entries_byte_identical": True,
        "changed_prior_entry_keys": [],
        "before_entry_set_sha256": canonical_hash(before_hashes),
        "after_prior_entry_set_sha256": canonical_hash(
            {key: after_hashes[key] for key in before_hashes}
        ),
    }


def deterministic_allow_sample(
    records: Sequence[dict[str, Any]], minimum: int = 10
) -> list[dict[str, Any]]:
    """Return a reproducible random sample of allow records."""
    allows = sorted(
        (row for row in records if row["decision"] == "allow"),
        key=lambda row: row["image_hash"],
    )
    count = min(len(allows), max(10, minimum))
    seed = int(hashlib.sha256(TARGET_QUOTE_ID.encode()).hexdigest()[:16], 16)
    indexes = sorted(random.Random(seed).sample(range(len(allows)), count))
    return [allows[index] for index in indexes]


def write_review_report(
    path: Path,
    records: Sequence[dict[str, Any]],
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    provider_failures: Sequence[dict[str, Any]],
    validation: dict[str, Any],
) -> None:
    """Write the requested compact human review."""
    vetoes = [row for row in records if row["decision"] == "veto"]
    unknowns = [
        row for row in records if row["decision"] == "adjudicated_unknown"
    ]
    normalised_allows = [
        row
        for row in records
        if row["decision"] == "allow"
        and any(
            (row.get(field) or {}).get("decision") == "veto"
            for field in ("provider_first_pass", "provider_second_pass")
        )
    ]
    sample = deterministic_allow_sample(records)
    target_counts = Counter(row["decision"] for row in records)
    target_count_text = ", ".join(
        f"{target_counts.get(decision, 0)} {decision}"
        for decision in ("allow", "veto", "adjudicated_unknown")
    )
    lines = [
        "# New quotation semantic-veto adjudication review",
        "",
        f"- Quote ID: `{TARGET_QUOTE_ID}`",
        f"- Policy: `{ATTRIBUTION_CLEANED_V3_POLICY_VERSION}`",
        "- Mode: shadow",
        "- Enforcement enabled: no",
        f"- Before: {before['allow_count']} allow, {before['veto_count']} veto, "
        f"{before['adjudicated_unknown_pair_count']} adjudicated unknown, "
        f"{before['not_adjudicated_pair_count']} not adjudicated",
        f"- After: {after['allow_count']} allow, {after['veto_count']} veto, "
        f"{after['adjudicated_unknown_pair_count']} adjudicated unknown, "
        f"{after['not_adjudicated_pair_count']} not adjudicated",
        f"- Target decisions: {target_count_text}",
        f"- Provider/parsing failures: {len(provider_failures)}",
        f"- Deterministic double-build: {validation['deterministic_double_build']}",
        f"- Previous entries byte-identical: {validation['prior_entries_byte_identical']}",
        "",
        "## Vetoes",
        "",
    ]
    if not vetoes:
        lines.append("- None.")
    for row in vetoes:
        lines.append(
            f"- `{row['image_id']}` / `{row['image_hash']}` — "
            f"{row['rationale']} (confidence: {row['confidence']})"
        )
    lines += ["", "## Adjudicated unknowns", ""]
    if not unknowns:
        lines.append("- None.")
    for row in unknowns:
        lines.append(
            f"- `{row['image_id']}` / `{row['image_hash']}` — "
            f"{row['rationale']} (confidence: {row['confidence']})"
        )
    lines += ["", "## Deterministic random sample of allows", ""]
    for row in sample:
        lines.append(
            f"- `{row['image_id']}` / `{row['image_hash']}` — "
            f"{row['rationale']} (confidence: {row['confidence']})"
        )
    lines += ["", "## Reviewed narrow-policy normalisations", ""]
    if not normalised_allows:
        lines.append("- None.")
    for row in normalised_allows:
        raw_codes = sorted(
            {
                code
                for field in ("provider_first_pass", "provider_second_pass")
                for code in ((row.get(field) or {}).get("contradiction_types") or [])
                if code != "none"
            }
        )
        lines.append(
            f"- `{row['image_id']}` / `{row['image_hash']}` — raw provider "
            f"code(s) `{', '.join(raw_codes)}` were retained, but the effective "
            "decision is allow because neither independent pass established an "
            "affirmative contradiction under the binding narrow policy."
        )
    lines += ["", "## Provider or parsing failures", ""]
    if not provider_failures:
        lines.append("- None.")
    else:
        for row in provider_failures:
            lines.append(f"- `{row.get('logical_call_id')}` — {row.get('error')}")
    lines += [
        "",
        "## Focused validation",
        "",
        f"- Focused tests: {validation.get('focused_test_result', 'pending')}",
        f"- Python compilation: {validation.get('python_compilation', 'pending')}",
        f"- Diff check: {validation.get('diff_check', 'pending')}",
        f"- Broad suite run: {str(bool(validation.get('broad_suite_ran'))).lower()}",
    ]
    lines += [
        "",
        "Only the target quotation's 91 previously missing pair rows were added. "
        "No existing resolved or adjudicated-unknown row changed.",
        "",
    ]
    atomic_write_text(path, "\n".join(lines))


def execute(run_dir: Path, output_manifest: Path) -> dict[str, Any]:
    """Run the complete targeted adjudication and deterministic transition."""
    run_dir.mkdir(parents=True, exist_ok=True)
    base_manifest_sha256 = sha256_file(BASE_MANIFEST)
    base = read_json(BASE_MANIFEST)
    base_audit = verify_base_manifest(base, verify_runtime_sources=True)
    packet = load_target_packet()
    quote = build_new_quote_contract(packet)
    images = load_image_contracts()
    if set(images) != set(base["image_hashes"]):
        raise NewQuoteAdjudicationError(
            "image contract hashes differ from authorised manifest hashes"
        )
    atomic_write_json(run_dir / "target_quote_contract.json", quote)
    decided, residual = prepare_pairs(quote, images)
    atomic_write_json(
        run_dir / "target_pair_plan.json",
        {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_version": RUN_VERSION,
            "quote_id": TARGET_QUOTE_ID,
            "deterministic_count": len(decided),
            "provider_residual_count": len(residual),
            "records": residual,
            "source_hashes": {
                "base_manifest": sha256_file(BASE_MANIFEST),
                "canonical_packets": sha256_file(PACKETS_PATH),
                "image_contracts": sha256_file(IMAGE_CONTRACTS_PATH),
            },
        },
    )

    first_rows: dict[str, dict[str, Any]] = {}
    second_rows: dict[str, dict[str, Any]] = {}
    if residual:
        first_items = make_provider_items(
            quote, images, residual, second_pass=False
        )
        estimated_second = make_provider_items(
            quote, images, residual, second_pass=True
        )
        atomic_write_json(
            run_dir / "provider_preflight.json",
            {
                "model": MODEL,
                "first_pass_calls": len(first_items),
                "maximum_second_pass_calls": len(estimated_second),
                "schema_version": PAIR_SCHEMA_VERSION,
                "tools": False,
                "search": False,
                "fail_closed": True,
            },
        )
        developer, vertex, _transport = transport_preflight(
            credential_project_root(), run_dir, inspect_availability=True
        )
        ledger = initialise_remediation_ledger(run_dir)
        router = RelationRouter(run_dir, developer, vertex, ledger)
        runner = DeveloperBatchRunner(run_dir, router, ledger, poll_seconds=15.0)
        first_batch_suffix = canonical_hash(
            [item["logical_id"] for item in first_items]
        )[:10]
        first_results = runner.run(
            batch_id=f"new-quote-first-{TARGET_QUOTE_ID[:12]}-{first_batch_suffix}",
            items=first_items,
            validators={
                item["logical_id"]: item["validator"] for item in first_items
            },
        )
        first_rows = flatten_provider_results(first_items, first_results)
        second_candidates = [
            row
            for row in residual
            if (
                normalise_provider_row(
                    first_rows[row["pair_id"]], quote, images[row["image_hash"]]
                ).get("final_decision")
                == "allow"
            )
        ]
        second_items = make_provider_items(
            quote, images, second_candidates, second_pass=True
        )
        if second_items:
            second_batch_suffix = canonical_hash(
                [item["logical_id"] for item in second_items]
            )[:10]
            second_results = runner.run(
                batch_id=(
                    f"new-quote-second-{TARGET_QUOTE_ID[:12]}-"
                    f"{second_batch_suffix}"
                ),
                items=second_items,
                validators={
                    item["logical_id"]: item["validator"] for item in second_items
                },
            )
            second_rows = flatten_provider_results(second_items, second_results)
        atomic_write_json(
            run_dir / "provider_execution_summary.json",
            {
                "first_pass_completed": len(first_rows),
                "second_pass_completed": len(second_rows),
                "provider_failures": [],
                "known_spend_usd": read_json(run_dir / "cost_ledger.json").get(
                    "known_spend_usd"
                ),
            },
        )

    records = sorted(
        [
            *decided,
            *resolve_provider_rows(
                quote, images, residual, first_rows, second_rows
            ),
        ],
        key=lambda row: row["image_hash"],
    )
    adjudication_validation = validate_adjudications(records, images)
    adjudication_path = run_dir / "new_quote_pair_adjudications.json"
    atomic_write_json(
        adjudication_path,
        {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_version": RUN_VERSION,
            "quote_id": TARGET_QUOTE_ID,
            "policy_version": ATTRIBUTION_CLEANED_V3_POLICY_VERSION,
            "quote_contract_sha256": canonical_hash(quote),
            "image_contracts_sha256": sha256_file(IMAGE_CONTRACTS_PATH),
            "decision_counts": adjudication_validation["decision_counts"],
            "records": records,
        },
    )

    first_build = compile_manifest(
        base, records, adjudication_path=adjudication_path
    )
    second_build = compile_manifest(
        base, records, adjudication_path=adjudication_path
    )
    first_bytes = canonical_bytes(first_build) + b"\n"
    second_bytes = canonical_bytes(second_build) + b"\n"
    if first_bytes != second_bytes:
        raise NewQuoteAdjudicationError(
            "full shadow manifest is not byte-identical on repeat"
        )
    preservation = verify_prior_entries(base, first_build)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.write_bytes(first_bytes)
    audit = validate_compiled_manifest(first_build, strict=True)
    target_coverage = first_build["quote_pair_coverage"][TARGET_QUOTE_ID]
    if (
        audit["quote_count"] != EXPECTED_QUOTE_COUNT
        or audit["image_count"] != EXPECTED_IMAGE_COUNT
        or first_build["total_authorised_pair_count"] != EXPECTED_PAIR_UNIVERSE
        or target_coverage["not_adjudicated_count"] != 0
    ):
        raise NewQuoteAdjudicationError("compiled manifest invariants failed")
    if first_build.get("live_production_enabled") is not False:
        raise NewQuoteAdjudicationError("compiled manifest enabled enforcement")
    runtime_config = {
        "enabled": True,
        "mode": "shadow",
        "manifest_path": str(output_manifest.relative_to(ROOT)),
        "fail_open": True,
        "record_best_allowed_alternative": True,
        "maximum_shadow_history": 10_000,
    }
    runtime = ShadowRuntime.load(
        ROOT,
        runtime_config,
        verify_source_hashes=True,
        enable_history=False,
    )
    if not runtime.available:
        raise NewQuoteAdjudicationError(
            f"compiled manifest runtime load failed: {runtime.reason}"
        )

    validation = {
        "schema_version": RUN_SCHEMA_VERSION,
        "run_version": RUN_VERSION,
        "passed": True,
        "base_manifest_sha256": base_manifest_sha256,
        "output_manifest_sha256": sha256_file(output_manifest),
        "quote_count": audit["quote_count"],
        "image_count": audit["image_count"],
        "total_authorised_pair_count": first_build[
            "total_authorised_pair_count"
        ],
        "target_not_adjudicated_after": target_coverage[
            "not_adjudicated_count"
        ],
        "target_decision_counts": adjudication_validation["decision_counts"],
        "deterministic_double_build": True,
        "double_build_sha256": hashlib.sha256(first_bytes).hexdigest(),
        **preservation,
        "mode": "shadow",
        "enforcement_enabled": False,
        "runtime_load_available": True,
        "provider_failures": [],
        "base_validation": base_audit,
    }
    atomic_write_json(run_dir / "final_validation.json", validation)
    write_review_report(
        run_dir / "new_quote_semantic_veto_review_report.md",
        records,
        before=base,
        after=first_build,
        provider_failures=[],
        validation=validation,
    )
    return validation


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    value.add_argument("--output-manifest", type=Path, default=BASE_MANIFEST)
    value.add_argument(
        "--execute-provider",
        action="store_true",
        help="Required acknowledgement for residual multimodal provider calls.",
    )
    return value


def main() -> int:
    """Run the targeted adjudication command."""
    args = parser().parse_args()
    if not args.execute_provider:
        raise SystemExit(
            "--execute-provider is required; residual decisions must not be fabricated"
        )
    validation = execute(args.run_dir.resolve(), args.output_manifest.resolve())
    print(json.dumps(validation, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
