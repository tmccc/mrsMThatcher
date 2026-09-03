#!/usr/bin/env python3
"""Calibrate the guarded original-editorial production policy offline.

The command accepts only explicit local inputs.  It contains no network,
model, provider, or search integration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shlex
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from original_editorial_production import (  # noqa: E402
    POLICY_SCHEMA_VERSION,
    SCORER_VERSION,
    canonical_json_bytes,
    canonical_sha256,
    file_sha256,
    quote_text_sha256,
    strict_json_loads,
)


MARGIN_GRID = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5)
LOSS_GRID = (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0)
SPLIT_SEED = 730_921_119
BOOTSTRAP_SEED = 730_921_120
BOOTSTRAP_RESAMPLES = 10_000
CROSS_VALIDATION_FOLDS = 5
MINIMUM_CONFIRMED_POST_GAP = 12
BLOCKED_CONTENT_IDS = (
    "IMG-3B765D6BF48F9676",
    "IMG-A0A526421FD73A51",
)
HASH_RE = re.compile(r"[0-9a-f]{64}")
CASE_ID_RE = re.compile(r"CASE-[0-9A-F]{12}")
DECISIVE = frozenset({"editorial", "production"})


class CalibrationError(RuntimeError):
    """Frozen input artefacts are missing, duplicate, or inconsistent."""


def _require(condition: object, message: str) -> None:
    if not condition:
        raise CalibrationError(message)


def _load_json(path: Path, *, label: str) -> object:
    try:
        return strict_json_loads(path.read_bytes(), label=label)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"cannot load {label} {path}: {exc}") from exc


def _load_unblinding(path: Path) -> object:
    """Load the frozen key, validating its two legacy one-candidate sentinels.

    The frozen simulator serialised positive ``Infinity`` for ``runner_up_gap``
    when a branch had exactly one candidate.  That field is not a score used
    by calibration.  Accept it only at that exact location and only when the
    associated editorial branch records one candidate; every other non-finite
    token remains an error.
    """
    sentinel = object()
    try:
        document = path.read_text(encoding="utf-8")

        def parse_constant(value: str) -> object:
            if value == "Infinity":
                return sentinel
            raise ValueError(f"unsupported non-finite constant {value}")

        data = json.loads(
            document,
            object_pairs_hook=lambda pairs: _calibration_unique_object(
                pairs, label="unblinding mapping"
            ),
            parse_constant=parse_constant,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"cannot load unblinding mapping {path}: {exc}") from exc
    _require(isinstance(data, dict), "unblinding mapping is not an object")
    sentinel_count = 0
    for case in data.get("cases", []):
        if not isinstance(case, dict):
            continue
        for detail in case.get("details", []):
            if not isinstance(detail, dict) or detail.get("runner_up_gap") is not sentinel:
                continue
            editorial = detail.get("editorial")
            _require(
                isinstance(editorial, dict)
                and editorial.get("candidate_count") == 1,
                "legacy Infinity occurs outside a one-candidate runner_up_gap",
            )
            detail["runner_up_gap"] = None
            sentinel_count += 1

    def contains_sentinel(value: object) -> bool:
        if value is sentinel:
            return True
        if isinstance(value, dict):
            return any(contains_sentinel(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_sentinel(item) for item in value)
        return False

    _require(not contains_sentinel(data), "unvalidated non-finite sentinel remains in unblinding mapping")
    _require(sentinel_count == 2, f"expected exactly two frozen runner-up sentinels, found {sentinel_count}")
    return data


def _calibration_unique_object(
    pairs: list[tuple[str, object]], *, label: str
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"{label} contains duplicate object name {key}")
        result[key] = value
    return result


def _load_jsonl(path: Path, *, label: str) -> list[dict[str, object]]:
    try:
        lines = path.read_bytes().splitlines()
    except OSError as exc:
        raise CalibrationError(f"cannot read {label} {path}: {exc}") from exc
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            raise CalibrationError(f"{label} contains blank line {line_number}")
        try:
            value = strict_json_loads(line, label=f"{label} line {line_number}")
        except Exception as exc:
            raise CalibrationError(f"invalid {label} line {line_number}: {exc}") from exc
        _require(isinstance(value, dict), f"{label} line {line_number} is not an object")
        rows.append(value)
    return rows


def _index_unique(
    rows: Iterable[Mapping[str, object]],
    *,
    key: str,
    label: str,
) -> dict[str, Mapping[str, object]]:
    result: dict[str, Mapping[str, object]] = {}
    for row in rows:
        value = row.get(key)
        _require(type(value) is str and value, f"{label} has missing {key}")
        _require(value not in result, f"{label} has duplicate {key}={value}")
        result[value] = row
    return result


def _valid_hash(value: object) -> bool:
    return type(value) is str and HASH_RE.fullmatch(value) is not None


def _finite(value: object, *, label: str) -> float:
    _require(
        not isinstance(value, bool) and isinstance(value, (int, float)),
        f"{label} is not numeric",
    )
    result = float(value)
    _require(math.isfinite(result), f"{label} is not finite")
    return result


def _current_quote_hashes(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CalibrationError(f"cannot read quotation corpus {path}: {exc}") from exc
    result: dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        quote_hash = quote_text_sha256(line)
        prior = result.get(quote_hash)
        _require(prior in {None, line}, f"quotation corpus hash collision for {quote_hash}")
        result[quote_hash] = line
    _require(result, "quotation corpus contains no quotations")
    return result


def _validate_current_scorer_inputs(
    *,
    quotation_corpus: Path,
    quote_analysis: Path,
    quote_analysis_overrides: Path,
    image_analysis: Path,
    editorial_analysis: Path,
) -> tuple[
    dict[str, str],
    dict[str, str],
    dict[str, Mapping[str, object]],
    dict[str, Mapping[str, object]],
]:
    current_quotes = _current_quote_hashes(quotation_corpus)
    quote_doc = _load_json(quote_analysis, label="quote analysis")
    overrides_doc = _load_json(
        quote_analysis_overrides, label="quote analysis overrides"
    )
    image_doc = _load_json(image_analysis, label="image analysis")
    editorial_doc = _load_json(editorial_analysis, label="original editorial analysis")
    _require(isinstance(quote_doc, dict) and isinstance(quote_doc.get("items"), dict), "quote analysis items missing")
    _require(
        isinstance(overrides_doc, dict)
        and set(overrides_doc) == {"schema_version", "description", "quote_overrides"}
        and overrides_doc.get("schema_version") == 1
        and isinstance(overrides_doc.get("quote_overrides"), dict),
        "quote analysis overrides schema is invalid",
    )
    _require(isinstance(image_doc, dict) and isinstance(image_doc.get("items"), dict), "image analysis items missing")
    _require(isinstance(editorial_doc, dict) and isinstance(editorial_doc.get("items"), dict), "editorial analysis items missing")
    for quote_hash, text in current_quotes.items():
        entry = quote_doc["items"].get(quote_hash)
        _require(isinstance(entry, dict), f"quote analysis is missing current quote {quote_hash}")
        analysed_text = entry.get("text")
        if analysed_text is not None:
            _require(
                quote_text_sha256(analysed_text) == quote_hash,
                f"quote analysis text is stale for {quote_hash}",
            )
    for quote_hash, override in overrides_doc["quote_overrides"].items():
        _require(_valid_hash(quote_hash), "quote analysis override hash is malformed")
        _require(isinstance(override, dict), f"quote analysis override is malformed for {quote_hash}")
        entry = quote_doc["items"].get(quote_hash)
        _require(isinstance(entry, dict), f"quote analysis override refers to absent quote {quote_hash}")
        _require(override.get("expected_text") == entry.get("text"), f"quote analysis override text mismatch for {quote_hash}")
        expected_lines = override.get("expected_line_numbers")
        _require(
            isinstance(expected_lines, list)
            and all(type(value) is int for value in expected_lines)
            and set(expected_lines).issubset(set(entry.get("line_numbers") or [])),
            f"quote analysis override line mapping mismatch for {quote_hash}",
        )
        _require(isinstance(override.get("analysis_patch"), dict), f"quote analysis override patch malformed for {quote_hash}")

    image_hash_by_basename: dict[str, str] = {}
    editorial_by_basename: dict[str, Mapping[str, object]] = {}
    for basename, entry in editorial_doc["items"].items():
        _require(type(basename) is str and Path(basename).name == basename, "editorial image basename malformed")
        _require(isinstance(entry, dict), f"editorial image entry malformed: {basename}")
        image_hash = entry.get("sha256")
        _require(_valid_hash(image_hash), f"editorial image hash malformed: {basename}")
        image_entry = image_doc["items"].get(image_hash)
        _require(isinstance(image_entry, dict), f"image analysis is missing {basename}")
        analysed_hash = image_entry.get("image_hash")
        _require(
            analysed_hash == image_hash,
            f"image/editorial analysis content hash differs for {basename}",
        )
        _require(
            basename in (image_entry.get("paths") or []),
            f"image analysis path mapping is missing {basename}",
        )
        image_path = REPOSITORY_ROOT / "images" / basename
        _require(image_path.is_file(), f"current original image missing: {image_path}")
        _require(file_sha256(image_path) == image_hash, f"current original image changed: {basename}")
        _require(image_hash not in image_hash_by_basename.values(), f"current original byte identity is duplicated: {basename}")
        image_hash_by_basename[basename] = str(image_hash)
        editorial_payload = entry.get("analysis")
        _require(isinstance(editorial_payload, dict), f"editorial analysis payload missing: {basename}")
        editorial_by_basename[basename] = editorial_payload
    _require(image_hash_by_basename, "current original image corpus is empty")
    def merge(base: Mapping[str, object], patch: Mapping[str, object]) -> dict[str, object]:
        result = json.loads(json.dumps(base, allow_nan=False))
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = merge(result[key], value)
            else:
                result[key] = json.loads(json.dumps(value, allow_nan=False))
        return result

    quote_payload_by_hash: dict[str, Mapping[str, object]] = {}
    for quote_hash, entry in quote_doc["items"].items():
        if not isinstance(entry, dict) or not isinstance(entry.get("analysis"), dict):
            continue
        payload: Mapping[str, object] = entry["analysis"]
        override = overrides_doc["quote_overrides"].get(quote_hash)
        if isinstance(override, dict):
            payload = merge(payload, override["analysis_patch"])
        quote_payload_by_hash[quote_hash] = payload
    return (
        current_quotes,
        image_hash_by_basename,
        quote_payload_by_hash,
        editorial_by_basename,
    )


def _source_image_maps(unblinding: Mapping[str, object]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    raw = unblinding.get("source_images")
    _require(isinstance(raw, list), "unblinding source_images missing")
    by_id: dict[str, dict[str, str]] = {}
    by_derivative: dict[str, str] = {}
    for item in raw:
        _require(isinstance(item, dict), "unblinding source image is not an object")
        content_id = item.get("content_id")
        derivative = item.get("derivative_sha256")
        original = item.get("original_sha256")
        snapshot_path = item.get("original_snapshot_path")
        _require(
            type(content_id) is str
            and re.fullmatch(r"IMG-[0-9A-F]{16}", content_id) is not None,
            "unblinding content identity is malformed",
        )
        _require(_valid_hash(derivative) and _valid_hash(original), f"unblinding image hashes malformed for {content_id}")
        _require(type(snapshot_path) is str and snapshot_path, f"unblinding snapshot path missing for {content_id}")
        _require(content_id not in by_id, f"duplicate content identity {content_id}")
        _require(derivative not in by_derivative, f"duplicate derivative content hash {derivative}")
        _require(original not in by_derivative, f"duplicate original content hash {original}")
        by_id[content_id] = {
            "derivative_sha256": str(derivative),
            "original_sha256": str(original),
            "snapshot_path": str(snapshot_path),
            "basename": Path(snapshot_path).name,
            "source": (
                "original"
                if Path(snapshot_path).parent.name == "images"
                else "generated"
                if Path(snapshot_path).parent.name == "manifest-images"
                else "unknown"
            ),
        }
        by_derivative[str(derivative)] = content_id
        by_derivative[str(original)] = content_id
    return by_id, by_derivative


def _case_score_inputs(
    case: Mapping[str, object],
    *,
    by_derivative: Mapping[str, str],
    by_content_id: Mapping[str, Mapping[str, str]],
    quote_payload_by_hash: Mapping[str, Mapping[str, object]],
    editorial_by_basename: Mapping[str, Mapping[str, object]],
    score_adjustment: Any,
) -> tuple[float, float]:
    details = case.get("details")
    _require(isinstance(details, list) and details, f"case {case.get('case_id')} has no score details")
    margins: list[float] = []
    losses: list[float] = []
    expected_editorial_hash = case.get("editorial_image_hash")
    expected_production_hash = case.get("production_image_hash")
    _require(expected_editorial_hash in by_derivative, f"case {case.get('case_id')} editorial derivative is not unblinded")
    _require(expected_production_hash in by_derivative, f"case {case.get('case_id')} production derivative is not unblinded")
    for detail in details:
        _require(isinstance(detail, dict), f"case {case.get('case_id')} detail malformed")
        _require(detail.get("quote_hash") == quote_text_sha256(case.get("quote_text")), f"case {case.get('case_id')} quote detail mismatch")
        editorial = detail.get("editorial")
        production = detail.get("production")
        quote_hash = str(detail["quote_hash"])
        quote_payload = quote_payload_by_hash.get(quote_hash)
        _require(quote_payload is not None, f"case {case.get('case_id')} quote is absent from current scorer input")
        production_content_id = by_derivative[str(expected_production_hash)]
        production_identity = by_content_id[production_content_id]
        if isinstance(editorial, dict) and isinstance(production, dict):
            _require(editorial.get("winner_source") == "original", f"case {case.get('case_id')} editorial winner is not original")
            editorial_raw = _finite(editorial.get("baseline_score"), label=f"case {case.get('case_id')} editorial raw score")
            production_raw = _finite(production.get("baseline_score"), label=f"case {case.get('case_id')} production raw score")
            production_source = production.get("winner_source")
            editorial_combined = _finite(
                editorial.get("policy_score"),
                label=f"case {case.get('case_id')} editorial combined score",
            )
        else:
            _require(
                detail.get("event_family") == "original_editorial"
                and detail.get("observation_classification")
                in {
                    "confirmed_live_production",
                    "unconfirmed_live_production_attempt",
                },
                f"case {case.get('case_id')} has an unknown score-detail format",
            )
            production_raw = _finite(
                detail.get("production_baseline_score"),
                label=f"case {case.get('case_id')} production raw score",
            )
            editorial_raw = _finite(
                detail.get("shadow_winner_baseline_score"),
                label=f"case {case.get('case_id')} editorial raw score",
            )
            production_source = detail.get("production_source")
            editorial_combined = _finite(
                detail.get("shadow_winner_score"),
                label=f"case {case.get('case_id')} editorial combined score",
            )
        production_adjustment = 0.0
        if production_source == "original":
            production_basename = production_identity["basename"]
            production_editorial = editorial_by_basename.get(production_basename)
            _require(production_editorial is not None, f"case {case.get('case_id')} baseline editorial metadata missing")
            production_adjustment, _ = score_adjustment(
                quote_payload,
                production_editorial,
                weight=0.32,
                max_abs_adjustment=4.0,
            )
            production_adjustment = _finite(
                production_adjustment,
                label=f"case {case.get('case_id')} baseline editorial adjustment",
            )
        derived_margin = editorial_combined - (
            production_raw + production_adjustment
        )
        if not isinstance(editorial, dict):
            _require(
                abs(
                    _finite(
                        detail.get("production_editorial_adjustment"),
                        label=f"case {case.get('case_id')} archived production adjustment",
                    )
                    - production_adjustment
                )
                <= 5.1e-5,
                f"case {case.get('case_id')} recovered-live baseline adjustment does not reproduce",
            )
            archived_margin = None
        else:
            archived_margin = detail.get("margin")
        if archived_margin is not None:
            _require(
                abs(
                    _finite(
                        archived_margin,
                        label=f"case {case.get('case_id')} archived margin",
                    )
                    - derived_margin
                )
                <= 1e-9,
                f"case {case.get('case_id')} archived margin differs from exact scorer reconstruction",
            )
        margins.append(derived_margin)
        losses.append(production_raw - editorial_raw)
    _require(max(margins) - min(margins) <= 1e-9, f"case {case.get('case_id')} has inconsistent margins")
    _require(max(losses) - min(losses) <= 1e-9, f"case {case.get('case_id')} has inconsistent baseline losses")
    return min(margins), max(losses)


def _normalise_review(value: object, *, label: str) -> str:
    _require(value in {"editorial", "production", "equal", "neither", "unjudgeable"}, f"{label} has invalid judgement {value!r}")
    return str(value)


def load_and_reconcile(args: argparse.Namespace) -> dict[str, object]:
    """Validate all frozen inputs and reconcile them into case records."""
    manifest_rows = _load_jsonl(args.case_corpus, label="frozen case corpus")
    unblinding = _load_unblinding(args.unblinding)
    gpt_rows = _load_jsonl(args.gpt_results, label="frozen GPT results")
    grok_rows = _load_jsonl(args.grok_results, label="frozen Grok results")
    safety = _load_json(args.historical_safety_results, label="historical safety results")
    _require(isinstance(unblinding, dict), "unblinding mapping is not an object")
    _require(isinstance(safety, dict), "historical safety results are not an object")
    _require(unblinding.get("schema_version") == 1, "unblinding schema version is unsupported")
    cases = unblinding.get("cases")
    _require(isinstance(cases, list), "unblinding cases missing")
    _require(type(unblinding.get("case_count")) is int and unblinding["case_count"] == len(cases), "unblinding case count mismatch")

    manifest = _index_unique(manifest_rows, key="case_id", label="case corpus")
    cases_by_id = _index_unique(cases, key="case_id", label="unblinding")
    gpt = _index_unique(gpt_rows, key="case_id", label="GPT results")
    grok = _index_unique(grok_rows, key="case_id", label="Grok results")
    case_ids = set(cases_by_id)
    _require(case_ids == set(manifest) == set(gpt) == set(grok), "case IDs do not reconcile across frozen artefacts")
    _require(len(case_ids) == 344, f"expected 344 cases, found {len(case_ids)}")
    by_content_id, by_derivative = _source_image_maps(unblinding)

    (
        current_quotes,
        current_images,
        quote_payload_by_hash,
        editorial_by_basename,
    ) = _validate_current_scorer_inputs(
        quotation_corpus=args.quotation_corpus,
        quote_analysis=args.quote_analysis,
        quote_analysis_overrides=args.quote_analysis_overrides,
        image_analysis=args.image_analysis,
        editorial_analysis=args.editorial_analysis,
    )
    os.environ.setdefault("MRS_TEST_MODE", "1")
    os.environ.setdefault("MRS_BASE_DIR", str(REPOSITORY_ROOT))
    os.environ.setdefault(
        "MRS_LOG_FILE", str(REPOSITORY_ROOT / ".calibration-import.log")
    )
    os.environ.setdefault("X_API_BASE_URL", "http://127.0.0.1:9")
    os.environ.setdefault("X_UPLOAD_BASE_URL", "http://127.0.0.1:9")
    os.environ.setdefault("XAI_API_BASE_URL", "http://127.0.0.1:9/v1")
    try:
        import mrsMThatcher2 as bot
    except Exception as exc:
        raise CalibrationError(f"cannot import the existing editorial scorer: {exc}") from exc
    for content_id, identity in by_content_id.items():
        snapshot_path = Path(identity["snapshot_path"])
        _require(snapshot_path.is_file(), f"unblinded source snapshot is missing for {content_id}")
        _require(
            file_sha256(snapshot_path) == identity["original_sha256"],
            f"unblinded source snapshot content changed for {content_id}",
        )
        _require(identity["source"] in {"original", "generated"}, f"unblinded source kind is unknown for {content_id}")
        if identity["source"] == "original":
            basename = identity["basename"]
            _require(basename in current_images, f"unblinded original {content_id} basename is absent from current originals")
            _require(current_images[basename] == identity["original_sha256"], f"unblinded original {content_id} does not match current content")

    blocked_hashes: list[str] = []
    blocked_resolution: dict[str, dict[str, str]] = {}
    for content_id in BLOCKED_CONTENT_IDS:
        identity = by_content_id.get(content_id)
        _require(identity is not None, f"blocked opaque identity is absent from unblinding: {content_id}")
        image_hash = identity["original_sha256"]
        _require(identity["source"] == "original", f"blocked opaque identity is not an original image: {content_id}")
        matches = [name for name, value in current_images.items() if value == image_hash]
        _require(len(matches) == 1 and matches[0] == identity["basename"], f"blocked opaque identity is ambiguous: {content_id}")
        blocked_hashes.append(image_hash)
        blocked_resolution[content_id] = {
            "basename": matches[0],
            "content_sha256": image_hash,
            "derivative_sha256": identity["derivative_sha256"],
        }

    historical_block = safety.get("historically_specific")
    _require(isinstance(historical_block, dict) and isinstance(historical_block.get("cases"), list), "historical safety case list missing")
    historical_ids = {
        str(item.get("case_id"))
        for item in historical_block["cases"]
        if isinstance(item, dict)
    }
    _require(len(historical_ids) == 25, f"expected 25 historically specific cases, found {len(historical_ids)}")
    safety_warning = safety.get("safety_warning_cases")
    _require(isinstance(safety_warning, dict), "safety warning block missing")
    warning_ids = {
        str(item.get("case_id"))
        for item in safety_warning.get("cases", [])
        if isinstance(item, dict)
    }
    false_specific_ids = set(str(value) for value in safety_warning.get("specific_failure_case_ids", []))
    concern_rows = safety.get("unanimous_strong_production_historical_or_false_specific_concern_cases")
    _require(isinstance(concern_rows, list), "unanimous strong production concern list missing")
    concern_ids = {
        str(item.get("case_id")) for item in concern_rows if isinstance(item, dict)
    }
    _require(len(concern_ids) == int(safety.get("unanimous_strong_production_concern_count")), "strong concern count mismatch")
    safety_excluded_ids = warning_ids | false_specific_ids | concern_ids
    _require(safety_excluded_ids.issubset(case_ids), "safety results refer to unknown case")

    records: list[dict[str, object]] = []
    for case_id in sorted(case_ids):
        case = cases_by_id[case_id]
        manifest_row = manifest[case_id]
        gpt_row = gpt[case_id]
        grok_row = grok[case_id]
        _require(CASE_ID_RE.fullmatch(case_id) is not None, f"malformed case ID {case_id}")
        quote_hash = quote_text_sha256(case.get("quote_text"))
        _require(_valid_hash(quote_hash), f"case {case_id} quote hash is malformed")
        _require(manifest_row.get("quote") == case.get("quote_text"), f"case {case_id} manifest quote mismatch")
        _require(gpt_row.get("quote_hash") == quote_hash == grok_row.get("quote_hash"), f"case {case_id} reviewer quote hash mismatch")
        editorial_content_id = by_derivative.get(str(case.get("editorial_image_hash")))
        production_content_id = by_derivative.get(str(case.get("production_image_hash")))
        _require(editorial_content_id is not None and production_content_id is not None, f"case {case_id} image cannot be unblinded")
        _require(gpt_row.get("editorial_image_identity") == editorial_content_id == grok_row.get("editorial_image_identity"), f"case {case_id} editorial identity mismatch")
        _require(gpt_row.get("production_image_identity") == production_content_id == grok_row.get("production_image_identity"), f"case {case_id} production identity mismatch")
        manifest_ids = {manifest_row.get("side_a_content_id"), manifest_row.get("side_b_content_id")}
        _require(manifest_ids == {editorial_content_id, production_content_id}, f"case {case_id} manifest sides mismatch")
        differing = case.get("editorial_image_hash") != case.get("production_image_hash")
        _require(gpt_row.get("policy_candidates_differ") is differing and grok_row.get("policy_candidates_differ") is differing, f"case {case_id} difference flag mismatch")
        historical = case_id in historical_ids
        _require(gpt_row.get("historically_specific") is historical and grok_row.get("historically_specific") is historical, f"case {case_id} historical flag mismatch")
        margin, loss = _case_score_inputs(
            case,
            by_derivative=by_derivative,
            by_content_id=by_content_id,
            quote_payload_by_hash=quote_payload_by_hash,
            editorial_by_basename=editorial_by_basename,
            score_adjustment=bot.original_editorial_shadow_score,
        )
        detail_sources = {
            str(
                detail.get("production", {}).get("winner_source")
                if isinstance(detail.get("production"), dict)
                else detail.get("production_source")
            )
            for detail in case.get("details", [])
            if isinstance(detail, dict)
        }
        _require(len(detail_sources) == 1 and detail_sources <= {"original", "generated"}, f"case {case_id} production source is inconsistent")
        production_source = next(iter(detail_sources))
        _require(
            production_source == by_content_id[production_content_id]["source"],
            f"case {case_id} production source does not match unblinded image",
        )
        source_stratum = grok_row.get("source_stratum")
        expected_live_or_simulated = {
            "recovered-live": "live",
            "counterfactual-simulated": "simulated",
        }.get(str(source_stratum))
        _require(
            expected_live_or_simulated is not None
            and gpt_row.get("live_or_simulated") == expected_live_or_simulated,
            f"case {case_id} source stratum is inconsistent",
        )
        reviews = {
            "gpt_1": _normalise_review(gpt_row.get("reviewer_1_mapped_policy_judgement"), label=f"{case_id} GPT-1"),
            "gpt_2": _normalise_review(gpt_row.get("reviewer_2_mapped_policy_judgement"), label=f"{case_id} GPT-2"),
            "grok_1": _normalise_review(grok_row.get("stream_1_comparison_category"), label=f"{case_id} Grok-1"),
            "grok_2": _normalise_review(grok_row.get("stream_2_comparison_category"), label=f"{case_id} Grok-2"),
        }
        records.append(
            {
                "case_id": case_id,
                "quote_hash": quote_hash,
                "quote_text": str(case.get("quote_text")),
                "differing": differing,
                "historically_specific": historical,
                "safety_concern": case_id in safety_excluded_ids,
                "false_specific_failure": case_id in false_specific_ids,
                "editorial_content_id": editorial_content_id,
                "editorial_content_sha256": by_content_id[editorial_content_id]["original_sha256"],
                "production_content_id": production_content_id,
                "production_content_sha256": by_content_id[production_content_id]["original_sha256"],
                "production_source": production_source,
                "source_stratum": str(source_stratum),
                "policy_margin": margin,
                "baseline_score_loss": loss,
                "reviews": reviews,
                "gpt_strengths": [gpt_row.get("reviewer_1_preference_strength"), gpt_row.get("reviewer_2_preference_strength")],
                "grok_strengths": [
                    (grok_row.get("stream_1") or {}).get("preference_strength") if isinstance(grok_row.get("stream_1"), dict) else None,
                    (grok_row.get("stream_2") or {}).get("preference_strength") if isinstance(grok_row.get("stream_2"), dict) else None,
                ],
            }
        )
    _require(sum(bool(item["differing"]) for item in records) == 274, "expected 274 policy-difference cases")
    aggregate_evidence = validate_supplied_aggregate_evidence(records, safety)
    return {
        "records": records,
        "current_quotes": current_quotes,
        "current_images": current_images,
        "blocked_hashes": sorted(blocked_hashes),
        "blocked_resolution": blocked_resolution,
        "historical_ids": historical_ids,
        "safety_excluded_ids": safety_excluded_ids,
        "false_specific_ids": false_specific_ids,
        "aggregate_evidence_validation": aggregate_evidence,
    }


def validate_supplied_aggregate_evidence(
    records: Sequence[Mapping[str, object]],
    historical_safety: Mapping[str, object],
) -> dict[str, object]:
    """Recompute the supplied aggregate claims from case-level frozen data."""
    differing = [item for item in records if item["differing"]]
    controls = [item for item in records if not item["differing"]]

    def grok_counts(rows: Sequence[Mapping[str, object]]) -> Counter[str]:
        return _review_counts(rows, ("grok_1", "grok_2"))

    policy_counts = grok_counts(differing)
    _require(len(differing) == 274, "aggregate evidence policy-difference count mismatch")
    _require(
        policy_counts["editorial"] == 320
        and policy_counts["production"] == 210
        and policy_counts["editorial"] + policy_counts["production"] == 530,
        "aggregate evidence decisive orientation totals mismatch",
    )
    direct_conflicts = sum(
        {
            item["reviews"]["grok_1"],
            item["reviews"]["grok_2"],
        }
        == DECISIVE
        for item in differing
    )
    _require(direct_conflicts == 56, "aggregate evidence direct mirror-conflict count mismatch")

    stratum_expectations = {
        "recovered-live": (95, 142, 41, 183),
        "counterfactual-simulated": (179, 178, 169, 347),
    }
    strata: dict[str, dict[str, int]] = {}
    for name, expected in stratum_expectations.items():
        rows = [item for item in differing if item["source_stratum"] == name]
        counts = grok_counts(rows)
        observed = (
            len(rows),
            counts["editorial"],
            counts["production"],
            counts["editorial"] + counts["production"],
        )
        _require(observed == expected, f"aggregate evidence {name} totals mismatch")
        strata[name] = {
            "cases": observed[0],
            "editorial": observed[1],
            "production": observed[2],
            "decisive": observed[3],
        }

    control_counts = grok_counts(controls)
    control_decisive = control_counts["editorial"] + control_counts["production"]
    _require(
        len(controls) == 70 and control_decisive == 0,
        "aggregate evidence identical-image control totals mismatch",
    )

    blocked_outcomes: dict[str, dict[str, int]] = {}
    expected_blocked = {
        "IMG-3B765D6BF48F9676": (10, 10, 0),
        "IMG-A0A526421FD73A51": (7, 6, 1),
    }
    for content_id, expected in expected_blocked.items():
        rows = [
            item
            for item in records
            if item["editorial_content_id"] == content_id
        ]
        both_production = sum(
            item["reviews"]["grok_1"] == "production"
            and item["reviews"]["grok_2"] == "production"
            for item in rows
        )
        non_decisive = sum(
            item["reviews"]["grok_1"] not in DECISIVE
            and item["reviews"]["grok_2"] not in DECISIVE
            for item in rows
        )
        observed = (len(rows), both_production, non_decisive)
        _require(
            observed == expected
            and not any(
                "editorial"
                in {
                    item["reviews"]["grok_1"],
                    item["reviews"]["grok_2"],
                }
                for item in rows
            ),
            f"aggregate evidence blocked-image outcome mismatch for {content_id}",
        )
        blocked_outcomes[content_id] = {
            "cases": observed[0],
            "both_favoured_production": observed[1],
            "non_decisive": observed[2],
            "favoured_editorial": 0,
        }

    historical = historical_safety.get("historically_specific")
    warning = historical_safety.get("safety_warning_cases")
    _require(
        isinstance(historical, dict)
        and historical.get("historical_plausibility_concern_reproduced") is True,
        "historical-plausibility production advantage was not reproduced",
    )
    _require(
        isinstance(warning, dict)
        and warning.get("specific_unanimous_strong_production_false_implication_failures")
        == 0,
        "frozen historical safety results contain a strong false-specific failure",
    )
    return {
        "validated": True,
        "policy_difference_cases": len(differing),
        "decisive_orientations": {
            "editorial": policy_counts["editorial"],
            "production": policy_counts["production"],
            "total": policy_counts["editorial"] + policy_counts["production"],
        },
        "strata": strata,
        "direct_mirrored_conflicts": direct_conflicts,
        "identical_image_controls": {
            "cases": len(controls),
            "orientations": len(controls) * 2,
            "spurious_decisive": control_decisive,
        },
        "blocked_image_outcomes": blocked_outcomes,
        "historical_plausibility_concern_reproduced": True,
        "strong_false_specific_failures": 0,
    }


def _group_assignment(quote_hash: str, *, seed: int, modulus: int) -> int:
    digest = hashlib.sha256(f"{seed}:{quote_hash}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") % modulus


def split_records(records: Sequence[Mapping[str, object]]) -> tuple[list[Mapping[str, object]], list[Mapping[str, object]]]:
    """Split deterministically by quote hash into 70/30 partitions."""
    quotes = sorted({str(item["quote_hash"]) for item in records})
    ranked = sorted(
        quotes,
        key=lambda value: (
            hashlib.sha256(f"{SPLIT_SEED}:{value}".encode("ascii")).digest(),
            value,
        ),
    )
    training_count = int(len(ranked) * 0.70)
    training_hashes = set(ranked[:training_count])
    ordered = sorted(records, key=lambda item: str(item["case_id"]))
    training = [item for item in ordered if item["quote_hash"] in training_hashes]
    holdout = [item for item in ordered if item["quote_hash"] not in training_hashes]
    _require(training and holdout, "deterministic quote-group split produced an empty partition")
    return training, holdout


def categorical_eligible(record: Mapping[str, object], blocked_hashes: set[str]) -> bool:
    """Apply non-numeric production exclusions to one frozen case."""
    return bool(
        record["differing"]
        and record["production_source"] == "original"
        and not record["historically_specific"]
        and not record["safety_concern"]
        and record["editorial_content_sha256"] not in blocked_hashes
    )


def accepted_records(
    records: Sequence[Mapping[str, object]],
    *,
    margin: float,
    loss: float,
    blocked_hashes: set[str],
) -> list[Mapping[str, object]]:
    """Return cases accepted by categorical guards and one threshold pair."""
    return [
        item
        for item in records
        if categorical_eligible(item, blocked_hashes)
        and float(item["policy_margin"]) + 1e-12 >= margin
        and float(item["baseline_score_loss"]) - 1e-12 <= loss
    ]


def _review_counts(records: Sequence[Mapping[str, object]], names: Sequence[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for item in records:
        reviews = item["reviews"]
        assert isinstance(reviews, dict)
        for name in names:
            counts[str(reviews[name])] += 1
    return counts


def _share(counts: Mapping[str, int]) -> float | None:
    decisive = int(counts.get("editorial", 0)) + int(counts.get("production", 0))
    return int(counts.get("editorial", 0)) / decisive if decisive else None


def metric_summary(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Summarise bounded reviewer and safety metrics for accepted cases."""
    pooled = _review_counts(records, ("gpt_1", "gpt_2", "grok_1", "grok_2"))
    gpt = _review_counts(records, ("gpt_1", "gpt_2"))
    grok = _review_counts(records, ("grok_1", "grok_2"))
    direct_conflicts = 0
    for item in records:
        reviews = item["reviews"]
        assert isinstance(reviews, dict)
        if {reviews["grok_1"], reviews["grok_2"]} == DECISIVE:
            direct_conflicts += 1
    return {
        "accepted_differing_cases": len(records),
        "quotation_clusters": len({item["quote_hash"] for item in records}),
        "pooled_counts": dict(sorted(pooled.items())),
        "pooled_editorial_decisive_share": _share(pooled),
        "gpt_counts": dict(sorted(gpt.items())),
        "gpt_editorial_decisive_share": _share(gpt),
        "grok_counts": dict(sorted(grok.items())),
        "grok_editorial_decisive_share": _share(grok),
        "direct_mirrored_conflicts": direct_conflicts,
        "direct_mirrored_conflict_rate": direct_conflicts / len(records) if records else 0.0,
        "historically_specific_accepted": sum(bool(item["historically_specific"]) for item in records),
        "blocked_image_accepted": 0,
        "false_specific_failures_accepted": sum(bool(item["false_specific_failure"]) for item in records),
        "safety_concerns_accepted": sum(bool(item["safety_concern"]) for item in records),
    }


def bootstrap_lower_bound(records: Sequence[Mapping[str, object]]) -> float | None:
    """Return the fixed-seed quote-cluster bootstrap lower 95% bound."""
    grouped: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for item in records:
        grouped[str(item["quote_hash"])].append(item)
    keys = sorted(grouped)
    if not keys:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    shares: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample: list[Mapping[str, object]] = []
        for _index in keys:
            sample.extend(grouped[rng.choice(keys)])
        share = _share(_review_counts(sample, ("gpt_1", "gpt_2", "grok_1", "grok_2")))
        if share is not None:
            shares.append(share)
    if not shares:
        return None
    shares.sort()
    return shares[max(0, math.floor(0.025 * len(shares)) - 1)]


def training_floors(summary: Mapping[str, object]) -> dict[str, bool]:
    """High-precision floors used before the optimiser can rank a pair."""
    return {
        "at_least_60_cases": int(summary["accepted_differing_cases"]) >= 60,
        "pooled_share_at_least_65_percent": float(summary["pooled_editorial_decisive_share"] or 0.0) >= 0.65,
        "gpt_share_at_least_55_percent": float(summary["gpt_editorial_decisive_share"] or 0.0) >= 0.55,
        "grok_share_at_least_55_percent": float(summary["grok_editorial_decisive_share"] or 0.0) >= 0.55,
        "direct_conflict_no_more_than_15_percent": float(summary["direct_mirrored_conflict_rate"]) <= 0.15,
        "zero_categorical_safety_failures": all(
            int(summary[key]) == 0
            for key in (
                "historically_specific_accepted",
                "blocked_image_accepted",
                "false_specific_failures_accepted",
                "safety_concerns_accepted",
            )
        ),
    }


def cross_validation(
    training: Sequence[Mapping[str, object]],
    *,
    margin: float,
    loss: float,
    blocked_hashes: set[str],
) -> dict[str, object]:
    """Evaluate threshold stability across deterministic quote groups."""
    folds: list[dict[str, object]] = []
    for fold in range(CROSS_VALIDATION_FOLDS):
        validation = [
            item
            for item in training
            if _group_assignment(str(item["quote_hash"]), seed=SPLIT_SEED + 1, modulus=CROSS_VALIDATION_FOLDS) == fold
        ]
        accepted = accepted_records(validation, margin=margin, loss=loss, blocked_hashes=blocked_hashes)
        summary = metric_summary(accepted)
        floors = {
            "at_least_8_cases": int(summary["accepted_differing_cases"]) >= 8,
            "pooled_share_at_least_55_percent": float(summary["pooled_editorial_decisive_share"] or 0.0) >= 0.55,
            "gpt_share_at_least_50_percent": float(summary["gpt_editorial_decisive_share"] or 0.0) >= 0.50,
            "grok_share_at_least_50_percent": float(summary["grok_editorial_decisive_share"] or 0.0) >= 0.50,
            "direct_conflict_no_more_than_25_percent": float(summary["direct_mirrored_conflict_rate"]) <= 0.25,
            "zero_categorical_safety_failures": all(
                int(summary[key]) == 0
                for key in (
                    "historically_specific_accepted",
                    "blocked_image_accepted",
                    "false_specific_failures_accepted",
                    "safety_concerns_accepted",
                )
            ),
        }
        folds.append({"fold": fold, "summary": summary, "floors": floors, "passes": all(floors.values())})
    return {"folds": folds, "passes": all(bool(item["passes"]) for item in folds)}


def threshold_search(
    training: Sequence[Mapping[str, object]],
    *,
    blocked_hashes: set[str],
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Choose thresholds using only training data and fixed tie-breaks."""
    candidates: list[dict[str, object]] = []
    for margin in MARGIN_GRID:
        for loss in LOSS_GRID:
            accepted = accepted_records(training, margin=margin, loss=loss, blocked_hashes=blocked_hashes)
            summary = metric_summary(accepted)
            floors = training_floors(summary)
            cv = cross_validation(
                training,
                margin=margin,
                loss=loss,
                blocked_hashes=blocked_hashes,
            )
            passes = all(floors.values()) and bool(cv["passes"])
            candidates.append(
                {
                    "minimum_policy_margin": margin,
                    "maximum_baseline_score_loss": loss,
                    "summary": summary,
                    "training_floors": floors,
                    "cross_validation": cv,
                    "passes": passes,
                }
            )
    eligible = [item for item in candidates if item["passes"]]
    ranked_pool = eligible or candidates

    def rank(item: Mapping[str, object]) -> tuple[object, ...]:
        summary = item["summary"]
        assert isinstance(summary, dict)
        return (
            1 if item["passes"] else 0,
            int(summary["accepted_differing_cases"]),
            float(summary["pooled_editorial_decisive_share"] or -1.0),
            -float(summary["direct_mirrored_conflict_rate"]),
            float(item["minimum_policy_margin"]),
            -float(item["maximum_baseline_score_loss"]),
        )

    selected = max(ranked_pool, key=rank)
    return selected, candidates


def holdout_gates(summary: Mapping[str, object], bootstrap_lower: float | None) -> dict[str, dict[str, object]]:
    """Evaluate every frozen production-authorisation gate."""
    values = {
        "at_least_30_accepted_differing_cases": int(summary["accepted_differing_cases"]) >= 30,
        "pooled_editorial_decisive_share_at_least_65_percent": float(summary["pooled_editorial_decisive_share"] or 0.0) >= 0.65,
        "gpt_editorial_decisive_share_at_least_55_percent": float(summary["gpt_editorial_decisive_share"] or 0.0) >= 0.55,
        "grok_editorial_decisive_share_at_least_55_percent": float(summary["grok_editorial_decisive_share"] or 0.0) >= 0.55,
        "quote_cluster_bootstrap_lower_95_above_50_percent": bootstrap_lower is not None and bootstrap_lower > 0.50,
        "direct_mirrored_conflict_no_more_than_15_percent": float(summary["direct_mirrored_conflict_rate"]) <= 0.15,
        "zero_historically_specific_cases": int(summary["historically_specific_accepted"]) == 0,
        "zero_blocked_image_promotions": int(summary["blocked_image_accepted"]) == 0,
        "zero_unanimous_strong_false_specific_failures": int(summary["false_specific_failures_accepted"]) == 0,
        "zero_designated_historical_safety_concerns": int(summary["safety_concerns_accepted"]) == 0,
    }
    return {name: {"passed": passed} for name, passed in values.items()}


def sensitivity_summary(records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Report accepted outcomes grouped by recurring editorial identity."""
    groups: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for item in records:
        groups[str(item["editorial_content_id"])].append(item)
    result: list[dict[str, object]] = []
    for identity, rows in groups.items():
        summary = metric_summary(rows)
        result.append(
            {
                "editorial_image_identity": identity,
                "accepted_cases": len(rows),
                "quotation_clusters": summary["quotation_clusters"],
                "pooled_editorial_decisive_share": summary["pooled_editorial_decisive_share"],
            }
        )
    return sorted(result, key=lambda item: (-int(item["accepted_cases"]), str(item["editorial_image_identity"])))


def build_regression_fixture(
    *,
    reconciled: Mapping[str, object],
    accepted_full: Sequence[Mapping[str, object]],
    input_hashes: Mapping[str, str],
    thresholds: Mapping[str, object],
) -> dict[str, object]:
    """Derive a compact, provenance-pinned policy-boundary fixture."""
    records = reconciled["records"]
    assert isinstance(records, list)
    blocked = set(reconciled["blocked_hashes"])
    quote_policy = build_quote_policy(reconciled["current_quotes"], records)
    must_reject = [
        item
        for item in records
        if item["differing"]
        and (
            item["historically_specific"]
            or item["safety_concern"]
            or item["editorial_content_sha256"] in blocked
        )
    ]
    strong = []
    for item in accepted_full:
        reviews = item["reviews"]
        strengths = list(item["gpt_strengths"]) + list(item["grok_strengths"])
        if (
            isinstance(reviews, dict)
            and set(reviews.values()) == {"editorial"}
            and all(value in {"moderate", "strong"} for value in strengths)
            and quote_policy.get(str(item["quote_hash"]))
            == "editorial_eligible"
        ):
            strong.append(item)
    ambiguous = []
    for item in records:
        if not item["differing"]:
            continue
        reviews = item["reviews"]
        assert isinstance(reviews, dict)
        direct = {reviews["grok_1"], reviews["grok_2"]} == DECISIVE
        cross_family = (
            _share(Counter([reviews["gpt_1"], reviews["gpt_2"]])) is not None
            and _share(Counter([reviews["grok_1"], reviews["grok_2"]])) is not None
            and (reviews["gpt_1"] == reviews["gpt_2"])
            and (reviews["grok_1"] == reviews["grok_2"])
            and reviews["gpt_1"] != reviews["grok_1"]
        )
        nondecisive = any(value not in DECISIVE for value in reviews.values())
        if direct or cross_family or nondecisive:
            ambiguous.append(item)

    def compact(item: Mapping[str, object], expected: str) -> dict[str, object]:
        return {
            "case_id": item["case_id"],
            "quote_hash": item["quote_hash"],
            "source_stratum": item["source_stratum"],
            "editorial_image_identity": item["editorial_content_id"],
            "editorial_image_sha256": item["editorial_content_sha256"],
            "production_image_identity": item["production_content_id"],
            "production_image_sha256": item["production_content_sha256"],
            "policy_margin": item["policy_margin"],
            "baseline_score_loss": item["baseline_score_loss"],
            "historically_specific": item["historically_specific"],
            "safety_concern": item["safety_concern"],
            "reviews": item["reviews"],
            "expected": expected,
        }

    return {
        "schema_version": 1,
        "fixture_id": "original-editorial-production-v1-boundaries",
        "source_sha256": dict(input_hashes),
        "thresholds": dict(thresholds),
        "blocked_identity_resolution": reconciled["blocked_resolution"],
        "must_reject": [compact(item, "reject") for item in sorted(must_reject, key=lambda row: str(row["case_id"]))],
        "must_accept_or_remain_eligible": [compact(item, "eligible") for item in sorted(strong, key=lambda row: str(row["case_id"]))[:16]],
        "ambiguous": [compact(item, "ambiguous") for item in sorted(ambiguous, key=lambda row: str(row["case_id"]))[:16]],
    }


def build_quote_policy(
    current_quotes: Mapping[str, str],
    records: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    """Classify every current quotation explicitly and conservatively."""
    historical_hashes = {
        str(item["quote_hash"]) for item in records if item["historically_specific"]
    }
    safety_hashes = {
        str(item["quote_hash"]) for item in records if item["safety_concern"]
    }
    evaluated_hashes = {str(item["quote_hash"]) for item in records}
    result: dict[str, str] = {}
    for quote_hash in sorted(current_quotes):
        if quote_hash in historical_hashes:
            result[quote_hash] = "baseline_only_historically_specific"
        elif quote_hash in safety_hashes or quote_hash not in evaluated_hashes:
            result[quote_hash] = "baseline_only_unreviewed"
        else:
            result[quote_hash] = "editorial_eligible"
    return result


def markdown_summary(result: Mapping[str, object]) -> str:
    """Render a concise deterministic human-readable calibration summary."""
    selected = result["selected_thresholds"]
    holdout = result["holdout"]
    assert isinstance(selected, dict) and isinstance(holdout, dict)
    gates = result["authorisation_gates"]
    assert isinstance(gates, dict)
    lines = [
        "# Original editorial production calibration",
        "",
        f"Status: **{'authorised' if result['authorised_for_production'] else 'not authorised'}**.",
        "",
        "The optimiser used a deterministic quotation-hash 70/30 split. The 30% group is an optimiser-frozen hold-out; prior aggregate corpus results were already known.",
        "",
        f"- Training/grouped-CV threshold status: `{'PASS' if result['training_passed'] else 'FAIL — no grid pair passed; thresholds below are diagnostic only'}`",
        f"- Minimum policy margin: `{selected['minimum_policy_margin']}`",
        f"- Maximum baseline-score loss: `{selected['maximum_baseline_score_loss']}`",
        f"- Hold-out accepted differing-image cases: `{holdout['accepted_differing_cases']}`",
        f"- Hold-out pooled editorial decisive share: `{holdout['pooled_editorial_decisive_share']}`",
        f"- Hold-out quotation-cluster bootstrap lower 95% bound: `{holdout['bootstrap_lower_95']}`",
        "",
        "The supplied aggregate evidence was recomputed from the frozen case-level reviewer rows, including 274 policy differences, 530 decisive mirrored orientations, 56 direct conflicts, and zero spurious decisive choices in 140 identical-image control orientations.",
        "",
        "## Authorisation gates",
        "",
    ]
    for name, gate in gates.items():
        assert isinstance(gate, dict)
        lines.append(f"- {'PASS' if gate['passed'] else 'FAIL'} — `{name}`")
    lines.extend(
        [
            "",
            "## Opaque-image resolution",
            "",
        ]
    )
    resolution = result["blocked_identity_resolution"]
    assert isinstance(resolution, dict)
    for identity, item in sorted(resolution.items()):
        assert isinstance(item, dict)
        lines.append(f"- `{identity}` → `{item['basename']}` → `{item['content_sha256']}`")
    lines.extend(
        [
            "",
            "No near-duplicate clusters were populated: the current original corpus has no byte-identical files and no frozen validated strict perceptual-equivalence mapping was supplied.",
            "",
        ]
    )
    return "\n".join(lines)


def calibrate(args: argparse.Namespace) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Run offline calibration and return policy, report, and fixture."""
    reconciled = load_and_reconcile(args)
    records = reconciled["records"]
    assert isinstance(records, list)
    blocked_hashes = set(reconciled["blocked_hashes"])
    policy_difference = [item for item in records if item["differing"]]
    training, holdout = split_records(policy_difference)
    selected, grid = threshold_search(training, blocked_hashes=blocked_hashes)
    margin = float(selected["minimum_policy_margin"])
    loss = float(selected["maximum_baseline_score_loss"])
    accepted_holdout = accepted_records(holdout, margin=margin, loss=loss, blocked_hashes=blocked_hashes)
    holdout_summary = metric_summary(accepted_holdout)
    bootstrap_lower = bootstrap_lower_bound(accepted_holdout)
    holdout_summary["bootstrap_lower_95"] = bootstrap_lower
    gates = holdout_gates(holdout_summary, bootstrap_lower)
    training_passed = bool(selected["passes"])
    authorised = training_passed and all(bool(item["passed"]) for item in gates.values())
    accepted_full = accepted_records(policy_difference, margin=margin, loss=loss, blocked_hashes=blocked_hashes)

    input_hashes = {
        "quotation_corpus": file_sha256(args.quotation_corpus),
        "quote_analysis": file_sha256(args.quote_analysis),
        "quote_analysis_overrides": file_sha256(args.quote_analysis_overrides),
        "image_analysis": file_sha256(args.image_analysis),
        "original_editorial_analysis": file_sha256(args.editorial_analysis),
        "evaluation_manifest": file_sha256(args.case_corpus),
        "gpt_reviewer_results": file_sha256(args.gpt_results),
        "grok_reviewer_results": file_sha256(args.grok_results),
        "unblinding_mapping": file_sha256(args.unblinding),
        "historical_safety_results": file_sha256(args.historical_safety_results),
        # No validated perceptual-equivalence input exists. Pin the exact
        # derived empty-cluster document rather than pretending another file
        # supplied similarity evidence.
        "near_duplicate_input": canonical_sha256([]),
    }
    thresholds = {
        "minimum_policy_margin": margin,
        "maximum_baseline_score_loss": loss,
    }
    fixture = build_regression_fixture(
        reconciled=reconciled,
        accepted_full=accepted_full,
        input_hashes=input_hashes,
        thresholds=thresholds,
    )
    fixture_hash = canonical_sha256(fixture)
    command = " ".join(shlex.quote(value) for value in [sys.executable, *sys.argv])
    calibration_record = {
        "status": "authorised" if authorised else "unauthorised",
        "selection_rule": (
            "pass all training and grouped-CV floors; maximise accepted differing-image cases; "
            "then pooled editorial decisive share; then minimise mirrored conflict; then prefer "
            "higher margin and lower maximum baseline loss; if no pair passes the floors, retain "
            "the same deterministically ranked pair for diagnostics only and forbid authorisation"
        ),
        "split_algorithm": "sha256(seed:quote_hash), sorted; first floor(70%) quote groups train, remainder frozen hold-out",
        "split_seed": SPLIT_SEED,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "training": selected["summary"],
        "cross_validation": selected["cross_validation"],
        "holdout": holdout_summary,
        "authorisation_gates": gates,
        "regression_fixture_sha256": fixture_hash,
        "command": command,
    }
    policy = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_id": "original-editorial-production-v1-20260903",
        "authorised_for_production": authorised,
        "scorer_version": SCORER_VERSION,
        "editorial_weight": 0.32,
        "maximum_abs_adjustment": 4.0,
        "minimum_policy_margin": margin,
        "maximum_baseline_score_loss": loss,
        "minimum_confirmed_post_gap": MINIMUM_CONFIRMED_POST_GAP,
        "input_sha256": input_hashes,
        "quote_policy": build_quote_policy(reconciled["current_quotes"], records),
        "blocked_promotion_image_sha256": sorted(blocked_hashes),
        "near_duplicate_clusters": [],
        "calibration": calibration_record,
        "generated_at": args.generated_at,
    }
    report = {
        "schema_version": 1,
        "authorised_for_production": authorised,
        "policy_sha256": canonical_sha256(policy),
        "input_sha256": input_hashes,
        "case_counts": {
            "all": len(records),
            "policy_difference": len(policy_difference),
            "training": len(training),
            "holdout": len(holdout),
            "training_quote_clusters": len({item["quote_hash"] for item in training}),
            "holdout_quote_clusters": len({item["quote_hash"] for item in holdout}),
        },
        "selected_thresholds": thresholds,
        "threshold_selection_status": (
            "passed_training_and_grouped_cv"
            if training_passed
            else "no_pair_passed_training_and_grouped_cv_diagnostic_only"
        ),
        "training_passed": training_passed,
        "training": selected["summary"],
        "cross_validation": selected["cross_validation"],
        "holdout": holdout_summary,
        "authorisation_gates": gates,
        "full_corpus_accepted": metric_summary(accepted_full),
        "sensitivity_by_editorial_image_identity": sensitivity_summary(accepted_full),
        "blocked_identity_resolution": reconciled["blocked_resolution"],
        "near_duplicate_clusters": [],
        "aggregate_evidence_validation": reconciled[
            "aggregate_evidence_validation"
        ],
        "grid": grid,
        "command": command,
    }
    return policy, report, fixture


def write_output(path: Path, data: bytes) -> None:
    """Replace one requested output with deterministic bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse explicit frozen-input and output paths."""
    parser = argparse.ArgumentParser(
        description="Offline deterministic calibration for guarded original-editorial production selection."
    )
    parser.add_argument("--case-corpus", type=Path, required=True)
    parser.add_argument("--unblinding", type=Path, required=True)
    parser.add_argument("--gpt-results", type=Path, required=True)
    parser.add_argument("--grok-results", type=Path, required=True)
    parser.add_argument("--historical-safety-results", type=Path, required=True)
    parser.add_argument("--quotation-corpus", type=Path, required=True)
    parser.add_argument("--quote-analysis", type=Path, required=True)
    parser.add_argument("--quote-analysis-overrides", type=Path, required=True)
    parser.add_argument("--image-analysis", type=Path, required=True)
    parser.add_argument("--editorial-analysis", type=Path, required=True)
    parser.add_argument("--output-policy", type=Path, required=True)
    parser.add_argument("--output-report-json", type=Path, required=True)
    parser.add_argument("--output-summary", type=Path, required=True)
    parser.add_argument("--output-regression", type=Path, required=True)
    parser.add_argument(
        "--generated-at",
        required=True,
        help="Explicit timezone-aware timestamp; keeping it explicit makes reruns byte deterministic.",
    )
    args = parser.parse_args(argv)
    try:
        generated = __import__("datetime").datetime.fromisoformat(
            args.generated_at.replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        parser.error(f"--generated-at is invalid: {exc}")
    if generated.tzinfo is None:
        parser.error("--generated-at must include a timezone")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Run calibration without network access and write requested artefacts."""
    args = parse_args(argv)
    try:
        policy, report, fixture = calibrate(args)
    except CalibrationError as exc:
        print(f"calibration failed: {exc}", file=sys.stderr)
        return 2
    write_output(args.output_policy, canonical_json_bytes(policy) + b"\n")
    write_output(args.output_report_json, canonical_json_bytes(report) + b"\n")
    write_output(args.output_regression, canonical_json_bytes(fixture) + b"\n")
    write_output(args.output_summary, markdown_summary(report).encode("utf-8"))
    print(
        json.dumps(
            {
                "authorised_for_production": policy["authorised_for_production"],
                "minimum_policy_margin": policy["minimum_policy_margin"],
                "maximum_baseline_score_loss": policy["maximum_baseline_score_loss"],
                "policy_sha256": canonical_sha256(policy),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
