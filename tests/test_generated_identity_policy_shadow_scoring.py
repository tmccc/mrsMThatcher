from __future__ import annotations

import copy
import json
import random
from datetime import datetime
from pathlib import Path

import pytest

import mrs_log_digest as digest
from tests.test_unit_helpers import bot, image_analysis_for_paths


DIAGNOSTIC = "tg_faf99f3030693b0a55f0116194551792f3c4ea51261d7310eca7fb4d33b667d5.png"


def valid_identity_analysis(policy: str = "unrestricted", **changes: object) -> dict:
    result = {
        "contains_specific_intended_person": True,
        "identity_dependence": "medium",
        "recognisability_to_typical_viewer": 7,
        "recognisability_to_politically_interested_viewer": 8,
        "meaning_retention_without_identity": 8,
        "origin_quote_suitability": 9,
        "recommended_cross_quote_policy": policy,
        "recommended_penalty_strength": 3,
        "confidence": 0.9,
    }
    result.update(changes)
    return result


def write_audit(path: Path, images: list[Path], analyses: dict[str, dict] | None = None, **top_changes: object) -> None:
    analyses = analyses or {}
    payload = {
        "schema_version": 1,
        "analysis_kind": "generated_image_identity_dependence_audit",
        "items": {
            image.name: {
                "basename": image.name,
                "image_sha256": bot.file_sha256(image),
                "origin_quote_hash": bot.generated_image_origin_quote_hash(image.name),
                "analysis": analyses.get(image.name, valid_identity_analysis()),
            }
            for image in images
        },
    }
    payload.update(top_changes)
    path.write_text(json.dumps(payload), encoding="utf-8")


def generated_path(directory: Path, hash_char: str) -> Path:
    path = directory / f"tg_{hash_char * 64}.png"
    path.write_bytes(f"generated-{hash_char}".encode())
    return path


def configure_pool(monkeypatch: pytest.MonkeyPatch, directory: Path, audit_file: Path) -> None:
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(directory))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_FILE", str(audit_file))
    monkeypatch.setattr(bot, "_GENERATED_IDENTITY_AUDIT_CACHE", {})


def candidate(
    basename: str,
    score: float,
    *,
    source: str = "generated",
    origin_match: bool = False,
    origin_boost: float = 0.0,
) -> dict:
    return {
        "basename": basename,
        "score": score,
        "image_source": source,
        "origin_quote_match": origin_match,
        "origin_quote_boost": origin_boost,
    }


def quote(hash_char: str = "f") -> dict:
    return {"quote_hash": hash_char * 64, "line_no": 105, "analysis": {}}


def test_disabled_mode_does_not_require_audit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", False)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_AUDIT_FILE", "/missing/audit.json")
    bot.validate_generated_identity_shadow_startup()


def test_enabled_mode_loads_valid_complete_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = generated_path(tmp_path, "a")
    audit_file = tmp_path / "audit.json"
    write_audit(audit_file, [image])
    configure_pool(monkeypatch, tmp_path, audit_file)
    assert bot.load_generated_identity_audit()[image.name]["recommended_cross_quote_policy"] == "unrestricted"


@pytest.mark.parametrize(
    "mutation,match",
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data.update(analysis_kind="wrong"), "analysis_kind"),
    ],
)
def test_audit_rejects_wrong_top_level_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation,
    match: str,
) -> None:
    image = generated_path(tmp_path, "a")
    audit_file = tmp_path / "audit.json"
    write_audit(audit_file, [image])
    data = json.loads(audit_file.read_text())
    mutation(data)
    audit_file.write_text(json.dumps(data), encoding="utf-8")
    configure_pool(monkeypatch, tmp_path, audit_file)
    with pytest.raises(RuntimeError, match=match):
        bot.load_generated_identity_audit()


def test_audit_rejects_original_basename_and_stale_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    image = generated_path(tmp_path, "a")
    audit_file = tmp_path / "audit.json"
    write_audit(audit_file, [image])
    data = json.loads(audit_file.read_text())
    data["items"]["t01.jpg"] = data["items"].pop(image.name)
    audit_file.write_text(json.dumps(data), encoding="utf-8")
    configure_pool(monkeypatch, tmp_path, audit_file)
    with pytest.raises(RuntimeError, match="non-generated basename"):
        bot.load_generated_identity_audit()

    write_audit(audit_file, [image])
    image.write_bytes(b"changed")
    monkeypatch.setattr(bot, "_GENERATED_IDENTITY_AUDIT_CACHE", {})
    with pytest.raises(RuntimeError, match="stale identity audit SHA"):
        bot.load_generated_identity_audit()


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"recommended_cross_quote_policy": "ban"}, "policy"),
        ({"identity_dependence": "extreme"}, "dependence"),
        ({"recognisability_to_typical_viewer": True}, "number"),
        ({"recognisability_to_typical_viewer": float("nan")}, "finite"),
        ({"meaning_retention_without_identity": float("inf")}, "finite"),
        ({"recommended_penalty_strength": float("-inf")}, "finite"),
        ({"confidence": 1.1}, "0..1"),
    ],
)
def test_audit_rejects_invalid_analysis_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changes: dict,
    match: str,
) -> None:
    image = generated_path(tmp_path, "a")
    audit_file = tmp_path / "audit.json"
    write_audit(audit_file, [image], {image.name: valid_identity_analysis(**changes)})
    configure_pool(monkeypatch, tmp_path, audit_file)
    with pytest.raises(RuntimeError, match=match):
        bot.load_generated_identity_audit()


@pytest.mark.parametrize("key", ["GENERATED_IDENTITY_SHADOW_SMALL_PENALTY", "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY"])
@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), float("-inf"), -1.0])
def test_penalty_config_rejects_invalid_values(key: str, value: object) -> None:
    errors = bot.validate_runtime_config_values({key: value})
    assert any(key in error for error in errors)


def test_policy_actions_use_exact_existing_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY", 6.0)
    monkeypatch.setattr(bot, "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY", 15.0)
    names = {policy: f"tg_{char * 64}.png" for policy, char in zip(bot.GENERATED_IDENTITY_POLICIES, "abcd")}
    audit = {names[policy]: valid_identity_analysis(policy) for policy in names}

    unrestricted = bot.generated_identity_candidate_shadow_row(candidate(names["unrestricted"], 90.415312), audit)
    small = bot.generated_identity_candidate_shadow_row(candidate(names["small_penalty"], 90.415312), audit)
    strong = bot.generated_identity_candidate_shadow_row(candidate(names["strong_penalty"], 90.415312), audit)
    excluded = bot.generated_identity_candidate_shadow_row(candidate(names["origin_quote_only"], 90.415312), audit)
    original = bot.generated_identity_candidate_shadow_row(candidate("t01.jpg", 77.25, source="original"), audit)

    assert unrestricted["identity_shadow_score"] == 90.415312
    assert small["identity_shadow_score"] == pytest.approx(84.415312)
    assert strong["identity_shadow_score"] == pytest.approx(75.415312)
    assert excluded["identity_shadow_score"] is None and excluded["identity_adjustment"] is None
    assert original["identity_shadow_score"] == 77.25


def test_origin_quote_use_preserves_score_and_origin_boost() -> None:
    name = f"tg_{'a' * 64}.png"
    audit = {name: valid_identity_analysis("origin_quote_only")}
    row = bot.generated_identity_candidate_shadow_row(candidate(name, 94.0, origin_match=True, origin_boost=4.0), audit)
    assert row["identity_action"] == "generated_origin_quote_unrestricted"
    assert row["identity_adjustment"] == 0.0
    assert row["identity_shadow_score"] == 94.0


def test_diagnostic_real_audit_record_origin_and_cross_quote_policy() -> None:
    payload = json.loads(Path("generated_image_identity_dependence_audit.json").read_text(encoding="utf-8"))
    item = payload["items"][DIAGNOSTIC]
    assert item["analysis"]["recommended_cross_quote_policy"] == "origin_quote_only"
    assert item["origin_quote_hash"] == bot.generated_image_origin_quote_hash(DIAGNOSTIC)
    assert bot.file_sha256(Path("generated_review_approved_images") / DIAGNOSTIC) == item["image_sha256"]

    audit = {DIAGNOSTIC: item["analysis"]}
    origin = bot.generated_identity_candidate_shadow_row(candidate(DIAGNOSTIC, 94.4, origin_match=True, origin_boost=4), audit)
    cross = bot.generated_identity_candidate_shadow_row(candidate(DIAGNOSTIC, 90.415312), audit)
    assert origin["identity_shadow_score"] == 94.4
    assert cross["identity_shadow_score"] is None


def test_shadow_result_excludes_cross_quote_origin_only_without_mutation() -> None:
    restricted = f"tg_{'a' * 64}.png"
    other = f"tg_{'b' * 64}.png"
    audit = {restricted: valid_identity_analysis("origin_quote_only"), other: valid_identity_analysis("unrestricted")}
    scored = [candidate(restricted, 100.0), candidate(other, 90.0), candidate("t01.jpg", 95.0, source="original")]
    before = copy.deepcopy(scored)
    state = {"original_regular_posts_since_generated_image": 0}
    state_before = copy.deepcopy(state)

    result = bot.generated_identity_policy_shadow_result(quote(), scored[0], scored, selection_phase="normal", audit_by_basename=audit)

    assert result["production_winner"] == restricted
    assert result["production_identity_action"] == "generated_cross_quote_origin_only_excluded"
    assert result["shadow_winner"] == "t01.jpg"
    assert result["shadow_winner_source"] == "original"
    assert result["winner_changed"] is True
    assert scored == before
    assert state == state_before


def test_ties_retain_production_or_use_deterministic_basename_without_rng() -> None:
    restricted = f"tg_{'f' * 64}.png"
    a = candidate("t02.jpg", 80.0, source="original")
    b = candidate("t01.jpg", 80.0, source="original")
    audit = {restricted: valid_identity_analysis("origin_quote_only")}
    state_before = random.getstate()
    retained = bot.generated_identity_policy_shadow_result(quote(), a, [a, b], selection_phase="normal", audit_by_basename={})
    excluded = bot.generated_identity_policy_shadow_result(quote(), candidate(restricted, 100), [candidate(restricted, 100), a, b], selection_phase="normal", audit_by_basename=audit)
    assert retained["shadow_winner"] == "t02.jpg"
    assert retained["shadow_tie_count"] == 2
    assert excluded["shadow_winner"] == "t01.jpg"
    assert random.getstate() == state_before


def test_phase_and_exact_scored_candidate_counts_are_reported() -> None:
    name = f"tg_{'a' * 64}.png"
    audit = {name: valid_identity_analysis("small_penalty")}
    scored = [candidate(name, 10), candidate("t01.jpg", 9, source="original")]
    for phase in ("normal", "forced_cycle_reset", "last_image_fallback"):
        result = bot.generated_identity_policy_shadow_result(quote(), scored[0], scored, selection_phase=phase, audit_by_basename=audit)
        assert result["selection_phase"] == phase
        assert result["eligible_candidate_count"] == 2
        assert result["eligible_generated_count"] == 1
        assert result["eligible_original_count"] == 1


def test_selector_production_tie_is_identical_with_both_shadows_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images = tmp_path / "images"
    images.mkdir()
    paths = [images / "t01.jpg", images / "t02.jpg"]
    for index, path in enumerate(paths):
        path.write_bytes(f"image-{index}".encode())
    metadata = image_analysis_for_paths(paths, {p.name: {"description": p.name, "seasonality": {"avoid_outside_season_or_occasion": False}} for p in paths})
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(images / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", False)
    monkeypatch.setattr(bot, "load_image_analysis", lambda: metadata)
    monkeypatch.setattr(bot, "score_image_for_quote", lambda *_args: (10.0, {"topics": 10.0}, True))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 10))
    monkeypatch.setattr(bot, "load_original_editorial_analysis", lambda: {p.name: {"dimension_scores": {d: 0 for d in bot.ORIGINAL_EDITORIAL_DIMENSIONS}, "overall_editorial_utility": 5.5} for p in paths})
    monkeypatch.setattr(bot, "load_generated_identity_audit", lambda: {})
    state = {"original_regular_posts_since_generated_image": 2}

    random.seed(1234)
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", False)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", False)
    disabled = bot.choose_matched_unused_image(set(), quote(), copy.deepcopy(state))
    random.seed(1234)
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", True)
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", True)
    enabled = bot.choose_matched_unused_image(set(), quote(), copy.deepcopy(state))

    assert enabled["basename"] == disabled["basename"]
    assert enabled["score"] == disabled["score"] == 10.0


def test_selector_shadow_receives_only_spacing_permitted_scored_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    originals = tmp_path / "originals"
    generated = tmp_path / "generated"
    originals.mkdir()
    generated.mkdir()
    original = originals / "t01.jpg"
    generated_image = generated_path(generated, "a")
    original.write_bytes(b"original")
    metadata = image_analysis_for_paths(
        [original, generated_image],
        {
            original.name: {"description": "original", "seasonality": {"avoid_outside_season_or_occasion": False}},
            generated_image.name: {"description": "generated", "seasonality": {"avoid_outside_season_or_occasion": False}},
        },
    )
    observed: list[list[str]] = []
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(originals / "t*"))
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IMAGE_POOL", True)
    monkeypatch.setattr(bot, "GENERATED_IMAGE_DIR", str(generated))
    monkeypatch.setattr(bot, "GENERATED_IMAGE_GLOB", "*.png")
    monkeypatch.setattr(bot, "load_image_analysis", lambda: metadata)
    monkeypatch.setattr(bot, "score_image_for_quote", lambda *_args: (10.0, {"topics": 10.0}, True))
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 10))
    monkeypatch.setattr(bot, "log_original_editorial_shadow_result", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "log_generated_identity_policy_shadow_result",
        lambda _quote, _choice, scored, **_kwargs: observed.append([item["basename"] for item in scored]),
    )

    selected = bot.choose_matched_unused_image(
        set(), quote(), {"original_regular_posts_since_generated_image": 0}, generated_images_allowed=False,
    )

    assert selected["basename"] == "t01.jpg"
    assert observed == [["t01.jpg"]]


def identity_event(**changes: object) -> dict:
    result = {
        "selection_phase": "normal", "production_source": "generated", "production_winner": "tg_a.png",
        "production_score": 100.0, "production_origin_quote_match": False,
        "production_identity_policy": "origin_quote_only",
        "production_identity_action": "generated_cross_quote_origin_only_excluded",
        "shadow_winner_source": "original", "shadow_winner": "t01.jpg", "shadow_winner_score": 90.0,
        "winner_changed": True, "small_penalty_count": 0, "strong_penalty_count": 0,
        "origin_quote_only_excluded_count": 1, "excluded_generated_basenames": ["tg_a.png"],
        "penalised_generated_basenames": [],
    }
    result.update(changes)
    return result


def test_digest_policy_relevant_denominator_and_zero_safe() -> None:
    relevant = [identity_event() for _ in range(2)] + [identity_event(winner_changed=False) for _ in range(3)]
    irrelevant = [identity_event(
        production_source="original", production_identity_policy=None,
        production_identity_action="original_unchanged", winner_changed=False,
        origin_quote_only_excluded_count=0, excluded_generated_basenames=[],
    ) for _ in range(5)]
    summary = digest.generated_identity_shadow_summary(relevant + irrelevant)
    assert summary["observations"] == 10
    assert summary["policy_relevant_observations"] == 5
    assert summary["winner_changes"] == 2
    assert summary["winner_change_percent"] == 40.0
    assert digest.generated_identity_shadow_summary(irrelevant)["winner_change_percent"] == 0.0


def test_digest_parses_and_renders_shadow_only_tables() -> None:
    payload = identity_event(line_no=105)
    records = [digest.Record(
        ts=datetime(2026, 7, 10, 8, 0), level="INFO", src="mrs", line=1,
        msg="GENERATED_IDENTITY_POLICY_SHADOW_RESULT " + json.dumps(payload, separators=(",", ":")),
        path="test.log", ordinal=1,
    )]
    report = digest.analyse(records)
    rendered = digest.render_markdown(report)
    assert report["generated_identity_shadow"]["summary"]["production_winner_origin_only_excluded"] == 1
    assert "## Generated identity-policy shadow scoring" in rendered
    assert "shadow-only and hypothetical" in rendered
    assert "does not imply that the identity-policy shadow winner was posted" in rendered
    assert "| time | line_no | production | action | shadow | production score | shadow score | phase |" in rendered
    assert "Production winners excluded by origin-quote-only shadow policy:" in rendered


def test_one_log_event_per_selection(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    name = f"tg_{'a' * 64}.png"
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", True)
    monkeypatch.setattr(bot, "load_generated_identity_audit", lambda: {name: valid_identity_analysis()})
    caplog.set_level("INFO", logger=bot.log.name)
    bot.log_generated_identity_policy_shadow_result(quote(), candidate(name, 10), [candidate(name, 10)], selection_phase="normal")
    assert caplog.text.count("GENERATED_IDENTITY_POLICY_SHADOW_RESULT ") == 1


def test_runtime_shadow_failure_cannot_abort_production_selection(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(bot, "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING", True)
    monkeypatch.setattr(bot, "load_generated_identity_audit", lambda: (_ for _ in ()).throw(RuntimeError("broken audit")))
    production = candidate("t01.jpg", 10, source="original")
    caplog.set_level("ERROR", logger=bot.log.name)

    bot.log_generated_identity_policy_shadow_result(quote(), production, [production], selection_phase="normal")

    assert "production selection remains unchanged" in caplog.text
