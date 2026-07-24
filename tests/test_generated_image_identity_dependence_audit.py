from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "audit_generated_image_identity_dependence.py"
SPEC = importlib.util.spec_from_file_location("identity_audit", MODULE_PATH)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit
SPEC.loader.exec_module(audit)


HASH = "a" * 64
BASENAME = f"tg_{HASH}.png"


def valid_analysis(**changes: object) -> dict:
    result = {
        "contains_specific_intended_person": True,
        "intended_person_source": "origin_metadata",
        "intended_person_label": "Example Person",
        "person_prominence": "dominant",
        "identity_dependence": "high",
        "recognisability_to_typical_viewer": 3.0,
        "recognisability_to_politically_interested_viewer": 6.0,
        "specialist_knowledge_required": "medium",
        "likeness_ambiguity": "medium",
        "misidentification_risk": "medium",
        "meaning_without_identity": "A serious writer resisting an oppressive state.",
        "meaning_retention_without_identity": 5.0,
        "origin_quote_suitability": 9.0,
        "cross_quote_reuse_safety": "risky",
        "recommended_cross_quote_policy": "strong_penalty",
        "recommended_penalty_strength": 8.0,
        "identity_failure_reason": "The exact face is not widely recognisable.",
        "cross_quote_reasoning": "The generic scene remains meaningful but loses the intended historical specificity.",
        "confidence": 0.9,
    }
    result.update(changes)
    return result


def fixture_corpus(tmp_path: Path) -> tuple[Path, Path, Path]:
    images = tmp_path / "images"
    origins = tmp_path / "origins"
    item = origins / HASH
    images.mkdir()
    item.mkdir(parents=True)
    image = images / BASENAME
    image.write_bytes(b"generated-image")
    (item / "quote.txt").write_text("A quote about Example Person.", encoding="utf-8")
    (item / "generation_prompt.txt").write_text("Concrete visual concepts:\n- Example Person\n", encoding="utf-8")
    (item / "quote_analysis.json").write_text(
        json.dumps({"historical_context": {"referenced_people": ["Example Person"]}}), encoding="utf-8"
    )
    (item / "manifest.json").write_text(json.dumps({"quote_hash": HASH}), encoding="utf-8")
    image_hash = audit.sha256_file(image)
    generated_analysis = tmp_path / "generated_analysis.json"
    generated_analysis.write_text(
        json.dumps({
            "analysis_kind": "images",
            "items": {
                image_hash: {
                    "image_hash": image_hash,
                    "paths": [BASENAME],
                    "analysis": {"description": "A serious writer."},
                }
            },
        }),
        encoding="utf-8",
    )
    return images, origins, generated_analysis


def test_generated_basename_validation() -> None:
    assert audit.generated_origin_hash(BASENAME) == HASH
    for invalid in ("t01.jpg", "tg_short.png", f"tg_{'g' * 64}.png", f"tg_{HASH}.jpg"):
        with pytest.raises(ValueError, match="Invalid generated"):
            audit.generated_origin_hash(invalid)


def test_analysis_schema_validation_and_no_invented_identity() -> None:
    assert audit.validate_analysis(valid_analysis(), ["Example Person"])["identity_dependence"] == "high"
    with pytest.raises(ValueError, match="not grounded"):
        audit.validate_analysis(valid_analysis(intended_person_label="Invented Person"), ["Example Person"])
    with pytest.raises(ValueError, match="cannot be invented"):
        audit.validate_analysis(valid_analysis(), [])
    no_person = valid_analysis(
        contains_specific_intended_person=False,
        intended_person_source="none",
        intended_person_label=None,
        person_prominence="none",
        identity_dependence="none",
    )
    assert audit.validate_analysis(no_person, [])["intended_person_label"] is None


def test_composite_intended_person_label_requires_every_name_to_be_grounded() -> None:
    composite = valid_analysis(intended_person_label="Solzhenitsyn (foreground); Stalin (background)")
    assert audit.validate_analysis(composite, ["Solzhenitsyn", "Stalin"])["intended_person_label"].startswith("Solzhenitsyn")
    with pytest.raises(ValueError, match="not grounded"):
        audit.validate_analysis(composite, ["Solzhenitsyn"])


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("recognisability_to_typical_viewer", 10.1, "finite"),
        ("recognisability_to_typical_viewer", float("nan"), "finite"),
        ("identity_dependence", "extreme", "identity_dependence"),
        ("recommended_cross_quote_policy", "ban", "recommended_cross_quote_policy"),
        ("cross_quote_reuse_safety", "unknown", "cross_quote_reuse_safety"),
    ],
)
def test_analysis_schema_rejects_invalid_values(field: str, value: object, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        audit.validate_analysis(valid_analysis(**{field: value}), ["Example Person"])


def test_input_validation_uses_origin_metadata_and_rejects_stale_analysis(tmp_path: Path) -> None:
    images, origins, generated_analysis = fixture_corpus(tmp_path)
    contexts = audit.validate_inputs(images, origins, generated_analysis)
    assert len(contexts) == 1
    assert contexts[0]["grounded_people"] == ["Example Person"]

    (images / BASENAME).write_bytes(b"changed")
    with pytest.raises(ValueError, match="Stale generated analysis hash"):
        audit.validate_inputs(images, origins, generated_analysis)


def test_face_correction_provenance_allows_known_current_replacement(tmp_path: Path) -> None:
    images, origins, generated_analysis = fixture_corpus(tmp_path)
    backup = tmp_path / "backup"
    corrected = images / "grok_face_corrected"
    backup.mkdir()
    corrected.mkdir()
    original = images / BASENAME
    (backup / BASENAME).write_bytes(original.read_bytes())
    original.write_bytes(b"corrected-image")
    (corrected / BASENAME).write_bytes(original.read_bytes())
    manifest = tmp_path / "face_manifest.json"
    manifest.write_text(
        json.dumps({"items": {BASENAME: {"status": "edit_accepted", "output_path": str(corrected / BASENAME)}}}),
        encoding="utf-8",
    )

    contexts = audit.validate_inputs(images, origins, generated_analysis, face_manifest=manifest, pre_face_backup=backup)

    assert contexts[0]["face_correction_provenance"]["status"] == "edit_accepted"
    assert contexts[0]["face_correction_provenance"]["current_sha256"] == audit.sha256_file(original)


def test_missing_origin_metadata_fails_without_inventing_identity(tmp_path: Path) -> None:
    images, origins, generated_analysis = fixture_corpus(tmp_path)
    (origins / HASH / "quote_analysis.json").unlink()
    with pytest.raises(FileNotFoundError):
        audit.validate_inputs(images, origins, generated_analysis)


def test_generic_generation_boilerplate_does_not_ground_thatcher() -> None:
    quote_analysis = {"historical_context": {"referenced_people": []}}
    assert audit.grounded_people_from_origin(quote_analysis) == []
    assert audit.grounded_people_from_origin(
        quote_analysis,
        {"assessment": {"thatcher_present": True}},
    ) == ["Margaret Thatcher"]


def test_prompt_explicitly_rejects_blanket_named_person_restriction() -> None:
    context = {
        "basename": BASENAME,
        "origin_quote_hash": HASH,
        "origin_quote": "quote",
        "grounded_people": ["Margaret Thatcher"],
        "generation_prompt": "prompt",
        "semantic_brief": {},
        "existing_analysis": {},
    }
    prompt = audit.prompt_for_context(context)
    assert "not classify an image as high/essential or origin_quote_only merely because it depicts a named person" in prompt
    assert "Thatcher's identity itself is normal account context" in prompt


def test_atomic_output_and_resume_currentness(tmp_path: Path) -> None:
    output_path = tmp_path / "audit.json"
    payload = audit.empty_output("grok-test")
    payload["items"][BASENAME] = {"value": 1}
    audit.atomic_write_json(output_path, payload)
    assert json.loads(output_path.read_text())["items"][BASENAME]["value"] == 1
    assert not list(tmp_path.glob(".audit.json.*.tmp"))

    context = {"image_sha256": "abc", "origin_quote_hash": HASH}
    item = {
        "image_sha256": "abc", "origin_quote_hash": HASH, "analysis_model": "grok-test",
        "prompt_version": audit.PROMPT_VERSION, "analysis": {},
    }
    assert audit.item_is_current(item, context, "grok-test")
    assert not audit.item_is_current({**item, "image_sha256": "stale"}, context, "grok-test")


def test_penalty_policies_are_deterministic_and_origin_only_excludes() -> None:
    analysis = valid_analysis()
    first = audit.continuous_identity_penalty(analysis)
    second = audit.continuous_identity_penalty(analysis)
    assert first == second == 6.72
    assert audit.adjusted_cross_quote_score(100.0, analysis, "continuous") == 93.28
    assert audit.adjusted_cross_quote_score(100.0, analysis, "fixed") == 82.0
    origin_only = valid_analysis(recommended_cross_quote_policy="origin_quote_only")
    assert audit.adjusted_cross_quote_score(100.0, origin_only, "fixed") is None


def test_offline_simulation_preserves_origin_match_and_can_exclude_cross_quote() -> None:
    other_hash = "b" * 64
    cross = f"tg_{HASH}.png"
    origin = f"tg_{other_hash}.png"
    audit_items = {
        cross: {"analysis": valid_analysis(recommended_cross_quote_policy="origin_quote_only")},
        origin: {"analysis": valid_analysis(recommended_cross_quote_policy="origin_quote_only")},
    }
    groups = [{
        "time": "2026-07-10 00:00:00", "quote_hash": other_hash, "actual_winner": cross,
        "actual_score": 100.0, "candidates": {cross: 100.0, origin: 95.0, "t01.jpg": 94.0},
    }]
    result = audit.simulate_policies(groups, audit_items)["results"][0]
    assert result["fixed_winner"] == origin
    assert result["best_original_recoverable"] == "t01.jpg"


def test_dry_run_makes_no_api_call_and_does_not_touch_production_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    images, origins, generated_analysis = fixture_corpus(tmp_path)
    output = tmp_path / "output.json"
    report = tmp_path / "report.md"
    production_files = [
        Path("mrsMThatcher2.py"), Path("mrs_log_digest.py"), Path("bot_state.json"),
        Path("images_used.json"), Path("generated_image_analysis.json"),
    ]
    before = {
        path: audit.sha256_file(path) if path.is_file() else None
        for path in production_files
    }

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("HTTP call attempted during dry run")

    monkeypatch.setattr(audit.requests.Session, "post", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(MODULE_PATH), "--image-dir", str(images), "--origin-root", str(origins),
            "--generated-analysis", str(generated_analysis), "--output", str(output),
            "--report", str(report), "--dry-run",
        ],
    )
    assert audit.main() == 0
    assert not output.exists()
    assert {
        path: audit.sha256_file(path) if path.is_file() else None
        for path in production_files
    } == before
