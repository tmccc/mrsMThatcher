from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mrs_bot_asset_metadata import generated_image_origin_quote_hash
from tools.generated_image_simulation import HistoricalImageSelection


def audit_fixture(tmp_path: Path) -> tuple[HistoricalImageSelection, dict, Path]:
    origin = "a" * 64
    image = tmp_path / f"tg_{origin}.png"
    image.write_bytes(b"offline-image-fixture")
    runtime = SimpleNamespace(
        BASE_DIR=tmp_path,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
        file_sha256=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    policy = HistoricalImageSelection(runtime)
    policy.configured_generated_image_paths = lambda: {image.name: image}
    analysis = {
        "recommended_cross_quote_policy": "small_penalty",
        "identity_dependence": "low",
        "contains_specific_intended_person": True,
        "recognisability_to_typical_viewer": 4.0,
        "recognisability_to_politically_interested_viewer": 7.0,
        "meaning_retention_without_identity": 8.0,
        "origin_quote_suitability": 9.0,
        "recommended_penalty_strength": 3.0,
        "confidence": 0.8,
    }
    payload = {
        "schema_version": policy.GENERATED_IDENTITY_AUDIT_SCHEMA_VERSION,
        "analysis_kind": policy.GENERATED_IDENTITY_AUDIT_KIND,
        "items": {image.name: {
            "image_sha256": runtime.file_sha256(image),
            "origin_quote_hash": origin,
            "analysis": analysis,
        }},
    }
    return policy, payload, image


def test_offline_identity_audit_loads_and_uses_validated_cache(tmp_path: Path) -> None:
    policy, payload, image = audit_fixture(tmp_path)
    path = Path(policy.GENERATED_IDENTITY_AUDIT_FILE)
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = policy.load_generated_identity_audit()

    assert loaded == {image.name: payload["items"][image.name]["analysis"]}
    assert loaded is policy._GENERATED_IDENTITY_AUDIT_CACHE[str(path)]
    path.unlink()
    assert policy.load_generated_identity_audit() is loaded


@pytest.mark.parametrize("invalid", ["stale_hash", "unknown_policy", "invalid_confidence", "missing_pool_item"])
def test_offline_identity_audit_rejects_invalid_input_without_caching(
    tmp_path: Path, invalid: str,
) -> None:
    policy, payload, image = audit_fixture(tmp_path)
    item = payload["items"][image.name]
    if invalid == "stale_hash":
        item["image_sha256"] = "outdated"
    elif invalid == "unknown_policy":
        item["analysis"]["recommended_cross_quote_policy"] = "unknown"
    elif invalid == "invalid_confidence":
        item["analysis"]["confidence"] = True
    else:
        payload["items"] = {}
    Path(policy.GENERATED_IDENTITY_AUDIT_FILE).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Invalid generated identity audit"):
        policy.load_generated_identity_audit()

    assert policy._GENERATED_IDENTITY_AUDIT_CACHE == {}


def test_offline_policy_applies_penalties_and_exclusions_without_mutation(tmp_path: Path) -> None:
    policy = HistoricalImageSelection(SimpleNamespace(BASE_DIR=tmp_path))
    policies = ("unrestricted", "small_penalty", "strong_penalty", "origin_quote_only")
    audit = {name: {"recommended_cross_quote_policy": name} for name in policies}
    scored = [{"basename": name, "image_source": "generated", "score": 20.0} for name in policies]
    scored += [{"basename": "t01.jpg", "image_source": "original", "score": 10.0}]
    before = copy.deepcopy(scored)

    rows, eligible = policy.generated_identity_policy_selection(scored, audit_by_basename=audit)

    assert {row["basename"]: row["score"] for row in eligible} == {
        "unrestricted": 20.0, "small_penalty": 14.0, "strong_penalty": 5.0, "t01.jpg": 10.0,
    }
    assert rows[3]["identity_shadow_score"] is None
    assert scored == before
    origin = {**scored[3], "origin_quote_match": True, "origin_quote_boost": 4.0}
    _, eligible = policy.generated_identity_policy_selection([origin], audit_by_basename=audit)
    assert eligible[0]["score"] == 20.0
    assert eligible[0]["origin_quote_boost"] == 4.0
    assert eligible[0]["identity_action"] == "generated_origin_quote_unrestricted"
