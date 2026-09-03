from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_unit_helpers import (
    bot,
    configure_simple_quote_post,
    image_analysis_for_paths,
    isolate_regular_post_receipt,  # noqa: F401 - imported autouse fixture
)


def _production_breaker_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_RESOLVED_MODE", "production")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_MODE_SOURCE", "canonical")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_MODE_RESOLUTION_LOCKED", True)
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_PRODUCTION_POLICY", None)
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_POLICY_FILE_IDENTITY", None)
    bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.reset_for_process(
        resolved_mode="production", mode_source="canonical"
    )
    bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.open("policy_unavailable")


def _install_synthetic_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Make the final guarded wrapper promote the second supplied row."""
    image_dir = tmp_path / "images"
    first = image_dir / "t01.jpg"
    second = image_dir / "t02.jpg"
    second.write_bytes(b"editorial challenger")
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths([first, second]),
    )
    monkeypatch.setattr(bot.random, "choice", lambda choices: choices[0])
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_RESOLVED_MODE", "production")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_MODE_SOURCE", "canonical")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_MODE_RESOLUTION_LOCKED", True)
    bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.reset_for_process(
        resolved_mode="production", mode_source="canonical"
    )

    def promote(
        quote: dict,
        baseline: dict,
        candidates: list[dict],
        _state: dict,
        *,
        selection_phase: str,
    ) -> tuple[dict, dict]:
        challenger = next(row for row in candidates if row is not baseline)
        baseline_score = float(baseline["score"])
        challenger_score = float(challenger["score"])
        decision = {
            "quote_hash": quote["quote_hash"],
            "selection_phase": selection_phase,
            "resolved_mode": "production",
            "action": "accept_promotion",
            "reason": "accepted_editorial_promotion",
            "winner_changed_by_policy": True,
            "baseline": {
                "basename": baseline["basename"],
                "source": baseline["image_source"],
                "content_sha256": baseline["image_hash"],
                "raw_score": baseline_score,
                "editorial_adjustment": 0.0,
                "combined_score": baseline_score,
            },
            "challenger": {
                "basename": challenger["basename"],
                "source": challenger["image_source"],
                "content_sha256": challenger["image_hash"],
                "raw_score": challenger_score,
                "editorial_adjustment": 2.0,
                "combined_score": challenger_score + 2.0,
            },
            "authoritative": {
                "basename": challenger["basename"],
                "source": challenger["image_source"],
                "content_sha256": challenger["image_hash"],
            },
            "policy_margin": challenger_score + 2.0 - baseline_score,
            "baseline_score_loss": baseline_score - challenger_score,
            "policy_id": "test-policy",
            "policy_sha256": "a" * 64,
            "editorial_metadata_sha256": "b" * 64,
            "input_sha256": {
                key: "c" * 64
                for key in bot.ORIGINAL_EDITORIAL_REQUIRED_INPUT_SHA256_KEYS
            },
            "guards": {
                "quote_classification": "editorial_eligible",
                "blocked_promotion": False,
                "recent_confirmed": False,
                "near_duplicate": False,
                "minimum_margin": False,
                "maximum_baseline_loss": False,
            },
            "circuit_breaker": bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.snapshot(),
        }
        bot._ORIGINAL_EDITORIAL_PENDING_DECISION = copy.deepcopy(decision)
        return challenger, copy.deepcopy(decision)

    monkeypatch.setattr(bot, "original_editorial_production_selection", promote)


def _configure_synthetic_runtime_policy(monkeypatch: pytest.MonkeyPatch) -> object:
    """Install the minimum immutable-looking policy needed by wrapper tests."""
    policy = SimpleNamespace(
        policy_id="test-policy",
        policy_sha256="a" * 64,
        input_sha256={"original_editorial_analysis": "b" * 64},
    )
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_RESOLVED_MODE", "production")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_MODE_SOURCE", "canonical")
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_MODE_RESOLUTION_LOCKED", True)
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_PRODUCTION_POLICY", policy)
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_POLICY_FILE_IDENTITY", None)
    monkeypatch.setattr(bot, "editorial_file_sha256", lambda _path: "b" * 64)
    monkeypatch.setattr(bot, "load_original_editorial_analysis", lambda: {})
    bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.reset_for_process(
        resolved_mode="production", mode_source="canonical"
    )
    return policy


def _synthetic_wrapper_inputs() -> tuple[dict, dict, list[dict]]:
    """Return one hash-consistent quote and exact supplied baseline object."""
    text = "A wrapper integrity quotation."
    baseline = {
        "basename": "baseline.jpg",
        "image_hash": hashlib.sha256(b"baseline").hexdigest(),
        "image_source": "original",
        "score": 1.0,
    }
    return (
        {
            "text": text,
            "quote_hash": bot.quote_text_hash(text),
            "analysis": {},
        },
        baseline,
        [baseline],
    )


@pytest.mark.parametrize(
    "reason",
    (
        "runtime_parameter_mismatch",
        "non_finite_score",
        "baseline_not_in_candidates",
        "candidate_content_hash_mismatch",
        "candidate_identity_collision",
        "impossible_score_ordering",
    ),
)
def test_runtime_integrity_failure_retains_baseline_opens_and_latches_breaker(
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_synthetic_runtime_policy(monkeypatch)
    quote, baseline, candidates = _synthetic_wrapper_inputs()
    original_decider = bot.guarded_editorial_decision

    def fail_integrity(**_kwargs: object) -> tuple[dict, dict]:
        raise bot.EditorialDecisionIntegrityError(reason, "synthetic integrity failure")

    monkeypatch.setattr(bot, "guarded_editorial_decision", fail_integrity)
    selected, decision = bot.original_editorial_production_selection(
        quote,
        baseline,
        candidates,
        {},
        selection_phase="normal",
    )
    assert selected is baseline
    assert decision is not None and decision["reason"] == reason
    first = bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.snapshot()
    assert first["open"] is True
    assert first["first_failure_reason"] == reason
    assert first["failure_count"] == 1

    monkeypatch.setattr(bot, "guarded_editorial_decision", original_decider)
    selected_again, later = bot.original_editorial_production_selection(
        quote,
        baseline,
        candidates,
        {},
        selection_phase="normal",
    )
    assert selected_again is baseline
    assert later is not None and later["reason"] == "circuit_breaker_open"
    assert bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.snapshot() == first


def test_wrapper_rejects_selected_object_absent_from_final_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _configure_synthetic_runtime_policy(monkeypatch)
    quote, baseline, candidates = _synthetic_wrapper_inputs()
    rogue = dict(baseline, basename="rogue.jpg")

    def return_rogue(**_kwargs: object) -> tuple[dict, dict]:
        return rogue, {
            "policy_id": policy.policy_id,
            "policy_sha256": policy.policy_sha256,
        }

    monkeypatch.setattr(bot, "guarded_editorial_decision", return_rogue)
    selected, decision = bot.original_editorial_production_selection(
        quote,
        baseline,
        candidates,
        {},
        selection_phase="normal",
    )
    assert selected is baseline
    assert decision is not None and decision["reason"] == "selected_not_in_candidates"
    assert bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.is_open is True
    assert (
        bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.first_failure_reason
        == "selected_not_in_candidates"
    )


def test_expected_wrapper_guard_rejection_does_not_open_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = _configure_synthetic_runtime_policy(monkeypatch)
    quote, baseline, candidates = _synthetic_wrapper_inputs()

    def reject_normally(**_kwargs: object) -> tuple[dict, dict]:
        return baseline, {
            "reason": "blocked_promotion_image",
            "policy_id": policy.policy_id,
            "policy_sha256": policy.policy_sha256,
        }

    monkeypatch.setattr(bot, "guarded_editorial_decision", reject_normally)
    selected, decision = bot.original_editorial_production_selection(
        quote,
        baseline,
        candidates,
        {},
        selection_phase="normal",
    )
    assert selected is baseline
    assert decision is not None and decision["reason"] == "blocked_promotion_image"
    assert bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.is_open is False
    assert bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.failure_count == 0


def test_breaker_open_production_still_pins_and_posts_exact_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines, images, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    _production_breaker_open(monkeypatch)
    observed: dict = {}

    def upload(path: str, **kwargs: object) -> str:
        status, pin = bot.load_regular_post_receipt()
        assert status == "selection_pinned"
        assert pin is not None and bot.original_editorial_selection_pin_is_semantically_valid(pin)
        assert kwargs["original_editorial_selection_pin"] == pin
        observed.update(copy.deepcopy(pin))
        assert Path(path).name == pin["authoritative_selected_basename"]
        assert bot.file_sha256(Path(path)) == pin["authoritative_selected_content_sha256"]
        return "media-1"

    monkeypatch.setattr(bot, "upload_media", upload)
    bot.post_random_quote(lines, images, state)

    assert observed["resolved_editorial_mode"] == "production"
    assert observed["decision_reason"] == "circuit_breaker_open"
    assert observed["baseline_winner_basename"] == "t01.jpg"
    assert observed["authoritative_selected_basename"] == "t01.jpg"
    assert observed["baseline_raw_score"] == 0.0
    assert observed["baseline_editorial_adjustment"] is None
    assert lines == {bot.quote_text_hash("Good quote.")}
    assert images == {"t01.jpg"}
    assert state["last_regular_image_filename"] == "t01.jpg"
    assert len(state["recent_confirmed_regular_images"]) == 1
    assert state["recent_confirmed_regular_images"][0]["image_sha256"] == hashlib.sha256(
        b"fake"
    ).hexdigest()
    assert bot.load_regular_post_receipt() == ("absent", None)


def test_restart_uses_pinned_image_without_rerunning_policy_or_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines, images, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    _production_breaker_open(monkeypatch)
    original_write = bot.write_original_editorial_selection_pin
    pinned: dict = {}

    def crash_after_pin(pin: dict) -> None:
        original_write(pin)
        pinned.update(copy.deepcopy(pin))
        raise RuntimeError("crash after durable selection pin")

    monkeypatch.setattr(bot, "write_original_editorial_selection_pin", crash_after_pin)
    with pytest.raises(RuntimeError, match="crash after durable"):
        bot.post_random_quote(lines, images, state)
    assert bot.load_regular_post_receipt() == ("selection_pinned", pinned)
    assert not lines and not images

    monkeypatch.setattr(bot, "write_original_editorial_selection_pin", original_write)
    monkeypatch.setattr(
        bot,
        "choose_regular_quote_image_pair",
        lambda *_args, **_kwargs: pytest.fail("recovery must not rerun selection"),
    )
    monkeypatch.setattr(
        bot,
        "original_editorial_production_selection",
        lambda *_args, **_kwargs: pytest.fail("recovery must not rerun policy"),
    )
    uploaded: list[str] = []
    monkeypatch.setattr(
        bot,
        "upload_media",
        lambda path, **_kwargs: uploaded.append(Path(path).name) or "media-1",
    )
    # A local rollback cannot alter the receipt-authoritative selection.
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_RESOLVED_MODE", "disabled")
    bot.post_random_quote(lines, images, state)

    assert uploaded == [pinned["authoritative_selected_basename"]]
    assert images == {pinned["authoritative_selected_basename"]}
    assert state["last_regular_image_filename"] == pinned["authoritative_selected_basename"]
    assert len(state["recent_confirmed_regular_images"]) == 1
    assert bot.load_regular_post_receipt() == ("absent", None)


def test_accepted_promotion_restart_marks_only_the_pinned_challenger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines, images, state, *_paths = configure_simple_quote_post(
        tmp_path, monkeypatch
    )
    _install_synthetic_promotion(tmp_path, monkeypatch)
    original_write = bot.write_original_editorial_selection_pin
    pin: dict = {}

    def stop_after_pin(value: dict) -> None:
        original_write(value)
        pin.update(copy.deepcopy(value))
        raise RuntimeError("crash after accepted promotion pin")

    monkeypatch.setattr(bot, "write_original_editorial_selection_pin", stop_after_pin)
    with pytest.raises(RuntimeError, match="accepted promotion pin"):
        bot.post_random_quote(lines, images, state)

    assert pin["winner_changed_by_policy"] is True
    assert pin["baseline_winner_basename"] == "t01.jpg"
    assert pin["authoritative_selected_basename"] == "t02.jpg"
    assert not lines and not images

    monkeypatch.setattr(bot, "write_original_editorial_selection_pin", original_write)
    monkeypatch.setattr(
        bot,
        "choose_regular_quote_image_pair",
        lambda *_args, **_kwargs: pytest.fail("recovery must not rerun selection"),
    )
    monkeypatch.setattr(
        bot,
        "original_editorial_production_selection",
        lambda *_args, **_kwargs: pytest.fail("recovery must not rerun policy"),
    )
    monkeypatch.setattr(bot, "_ORIGINAL_EDITORIAL_RESOLVED_MODE", "disabled")
    uploaded: list[str] = []
    monkeypatch.setattr(
        bot,
        "upload_media",
        lambda path, **_kwargs: uploaded.append(Path(path).name) or "media-1",
    )

    bot.post_random_quote(lines, images, state)

    assert uploaded == ["t02.jpg"]
    assert images == {"t02.jpg"}
    assert "t01.jpg" not in images
    assert state["last_regular_image_filename"] == "t02.jpg"
    assert state["recent_confirmed_regular_images"] == [
        {
            "post_id": "950001",
            "image_basename": "t02.jpg",
            "image_sha256": pin[
                "authoritative_selected_content_sha256"
            ],
        }
    ]
    assert bot.load_regular_post_receipt() == ("absent", None)


def test_changed_pinned_image_fails_without_upload_or_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines, images, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    _production_breaker_open(monkeypatch)
    original_write = bot.write_original_editorial_selection_pin

    def mutate_after_pin(pin: dict) -> None:
        original_write(pin)
        (tmp_path / "images" / pin["authoritative_selected_basename"]).write_bytes(
            b"changed"
        )

    monkeypatch.setattr(bot, "write_original_editorial_selection_pin", mutate_after_pin)
    monkeypatch.setattr(
        bot,
        "upload_media",
        lambda *_args, **_kwargs: pytest.fail("changed pinned bytes must not upload"),
    )
    with pytest.raises(bot.InvalidRegularPostReceipt, match="content changed"):
        bot.post_random_quote(lines, images, state)

    status, pin = bot.load_regular_post_receipt()
    assert status == "selection_pinned" and pin is not None
    assert not lines and not images
    assert bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.is_open


def test_invalid_selection_receipt_fields_are_a_conservative_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines, images, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    _production_breaker_open(monkeypatch)
    original_write = bot.write_original_editorial_selection_pin

    def stop_after_pin(pin: dict) -> None:
        original_write(pin)
        raise RuntimeError("stop")

    monkeypatch.setattr(bot, "write_original_editorial_selection_pin", stop_after_pin)
    with pytest.raises(RuntimeError, match="stop"):
        bot.post_random_quote(lines, images, state)
    status, pin = bot.load_regular_post_receipt()
    assert status == "selection_pinned" and pin is not None
    pin["policy_margin"] = float("nan")
    bot.atomic_write_json(bot.REGULAR_POST_RECEIPT_FILE, pin, durable=True)
    status, loaded = bot.load_regular_post_receipt()
    assert status == "invalid" and loaded is None
    with pytest.raises(bot.InvalidRegularPostReceipt):
        bot.post_random_quote(set(), set(), {})


def test_finite_but_inconsistent_selection_receipt_scores_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines, images, state, *_paths = configure_simple_quote_post(
        tmp_path, monkeypatch
    )
    _install_synthetic_promotion(tmp_path, monkeypatch)
    original_write = bot.write_original_editorial_selection_pin

    def stop_after_pin(pin: dict) -> None:
        original_write(pin)
        raise RuntimeError("stop after promotion pin")

    monkeypatch.setattr(bot, "write_original_editorial_selection_pin", stop_after_pin)
    with pytest.raises(RuntimeError, match="promotion pin"):
        bot.post_random_quote(lines, images, state)
    status, pin = bot.load_regular_post_receipt()
    assert status == "selection_pinned" and pin is not None

    pin["policy_margin"] = float(pin["policy_margin"]) + 0.125
    bot.atomic_write_json(bot.REGULAR_POST_RECEIPT_FILE, pin, durable=True)

    assert bot.load_regular_post_receipt() == ("invalid", None)
    assert not lines and not images


def test_missing_production_decision_aborts_before_pin_then_retries_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines, images, state, *_paths = configure_simple_quote_post(
        tmp_path, monkeypatch
    )
    original_wrapper = bot.original_editorial_production_selection
    original_take = bot.take_original_editorial_pending_decision
    _install_synthetic_promotion(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot, "take_original_editorial_pending_decision", lambda **_kwargs: None
    )
    uploads: list[str] = []
    monkeypatch.setattr(
        bot,
        "upload_media",
        lambda path, **_kwargs: uploads.append(Path(path).name) or "media-1",
    )

    with pytest.raises(RuntimeError, match="aborting before media upload"):
        bot.post_random_quote(lines, images, state)

    assert uploads == []
    assert bot.load_regular_post_receipt() == ("absent", None)
    assert not lines and not images
    assert bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.is_open is True
    assert (
        bot.ORIGINAL_EDITORIAL_CIRCUIT_BREAKER.first_failure_reason
        == "receipt_decision_inconsistency"
    )

    monkeypatch.setattr(bot, "original_editorial_production_selection", original_wrapper)
    monkeypatch.setattr(bot, "take_original_editorial_pending_decision", original_take)
    bot.post_random_quote(lines, images, state)

    assert uploads == ["t01.jpg"]
    assert images == {"t01.jpg"}
    assert state["last_regular_image_filename"] == "t01.jpg"


def test_recent_confirmed_history_is_bounded_and_idempotent() -> None:
    state: dict = {}
    for index in range(70):
        assert bot.record_recent_confirmed_regular_image(
            state,
            post_id=str(1000 + index),
            image_basename=f"t{index:02d}.jpg",
            image_sha256=hashlib.sha256(str(index).encode()).hexdigest(),
        )
    assert len(state["recent_confirmed_regular_images"]) == 64
    final = state["recent_confirmed_regular_images"][-1]
    assert bot.record_recent_confirmed_regular_image(
        state,
        post_id=final["post_id"],
        image_basename=final["image_basename"],
        image_sha256=final["image_sha256"],
    ) is False
    assert len(state["recent_confirmed_regular_images"]) == 64


@pytest.mark.parametrize(
    "boundary",
    ("after_policy_calculation", "after_authoritative_choice_before_pin"),
)
def test_pre_pin_faults_have_no_media_side_effect_and_retry_pins_exact_image(
    boundary: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines, images, state, *_paths = configure_simple_quote_post(
        tmp_path, monkeypatch
    )
    _production_breaker_open(monkeypatch)
    uploads: list[str] = []
    monkeypatch.setattr(
        bot,
        "upload_media",
        lambda path, **_kwargs: uploads.append(Path(path).name) or "media-1",
    )
    if boundary == "after_policy_calculation":
        original = bot.original_editorial_production_selection

        def calculate_then_crash(*args: object, **kwargs: object):
            original(*args, **kwargs)
            raise RuntimeError("fault after policy calculation")

        monkeypatch.setattr(
            bot, "original_editorial_production_selection", calculate_then_crash
        )
    else:
        original = bot.build_original_editorial_selection_pin

        def build_then_crash(*args: object, **kwargs: object):
            pin = original(*args, **kwargs)
            assert bot.original_editorial_selection_pin_is_semantically_valid(pin)
            raise RuntimeError("fault before durable pin")

        monkeypatch.setattr(
            bot, "build_original_editorial_selection_pin", build_then_crash
        )

    with pytest.raises(RuntimeError, match="fault"):
        bot.post_random_quote(lines, images, state)
    assert uploads == []
    assert bot.load_regular_post_receipt() == ("absent", None)
    assert not lines and not images

    if boundary == "after_policy_calculation":
        monkeypatch.setattr(bot, "original_editorial_production_selection", original)
    else:
        monkeypatch.setattr(bot, "build_original_editorial_selection_pin", original)
    bot.post_random_quote(lines, images, state)
    assert uploads == ["t01.jpg"]
    assert images == {"t01.jpg"}
    assert state["last_regular_image_filename"] == "t01.jpg"
    assert len(state["recent_confirmed_regular_images"]) == 1


def test_confirmed_media_restart_reuses_handoff_without_second_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines, images, state, *_paths = configure_simple_quote_post(
        tmp_path, monkeypatch
    )
    _production_breaker_open(monkeypatch)
    original_write = bot.write_original_editorial_selection_pin
    pinned: dict = {}

    def stop_after_pin(pin: dict) -> None:
        original_write(pin)
        pinned.update(copy.deepcopy(pin))
        raise RuntimeError("restart after simulated confirmed media")

    monkeypatch.setattr(bot, "write_original_editorial_selection_pin", stop_after_pin)
    with pytest.raises(RuntimeError, match="simulated confirmed media"):
        bot.post_random_quote(lines, images, state)

    monkeypatch.setattr(bot, "write_original_editorial_selection_pin", original_write)
    monkeypatch.setattr(
        bot,
        "confirmed_media_upload_matches_selection_pin",
        lambda pin: pin == pinned,
    )
    monkeypatch.setattr(
        bot,
        "load_confirmed_media_upload",
        lambda _path: SimpleNamespace(media_id="media-confirmed"),
    )
    monkeypatch.setattr(
        bot,
        "upload_media",
        lambda *_args, **_kwargs: pytest.fail(
            "a confirmed pinned media hand-off must not be uploaded again"
        ),
    )
    selector_calls = 0

    def forbidden_selector(*_args: object, **_kwargs: object):
        nonlocal selector_calls
        selector_calls += 1
        pytest.fail("confirmed-media recovery must not rerun selection")

    monkeypatch.setattr(bot, "choose_regular_quote_image_pair", forbidden_selector)
    bot.post_random_quote(lines, images, state)

    assert selector_calls == 0
    assert images == {pinned["authoritative_selected_basename"]}
    assert len(state["recent_confirmed_regular_images"]) == 1
    assert bot.load_regular_post_receipt() == ("absent", None)


@pytest.mark.parametrize(
    "boundary",
    (
        "after_quote_history_update",
        "after_image_history_update",
        "after_recent_image_history_update",
        "after_state_write",
    ),
)
def test_confirmed_local_update_faults_replay_the_pinned_transition_once(
    boundary: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines, images, state, *_paths = configure_simple_quote_post(
        tmp_path, monkeypatch
    )
    _production_breaker_open(monkeypatch)
    original_create = bot.create_post
    create_calls = 0

    def counted_create(**kwargs: object) -> dict:
        nonlocal create_calls
        create_calls += 1
        return original_create(**kwargs)

    monkeypatch.setattr(bot, "create_post", counted_create)
    original_save_protected = bot.save_regular_post_protected_state
    original_save_quote = bot.save_quote_used_hashes
    original_save_image = bot.save_image_used_basenames
    original_record_recent = bot.record_recent_confirmed_regular_image

    if boundary == "after_quote_history_update":

        def fail_after_quote(
            current_lines: set,
            _current_images: set,
            _current_state: dict,
            *,
            durable: bool,
        ) -> None:
            original_save_quote(
                bot.LINES_USED_FILE, current_lines, durable=durable
            )
            raise RuntimeError("fault after quote-history update")

        monkeypatch.setattr(
            bot, "save_regular_post_protected_state", fail_after_quote
        )
    elif boundary == "after_image_history_update":

        def fail_after_image(
            current_lines: set,
            current_images: set,
            _current_state: dict,
            *,
            durable: bool,
        ) -> None:
            original_save_quote(
                bot.LINES_USED_FILE, current_lines, durable=durable
            )
            original_save_image(
                bot.IMAGES_USED_FILE,
                {str(item) for item in current_images},
                durable=durable,
            )
            raise RuntimeError("fault after image-history update")

        monkeypatch.setattr(
            bot, "save_regular_post_protected_state", fail_after_image
        )
    elif boundary == "after_recent_image_history_update":

        def fail_after_recent(*args: object, **kwargs: object) -> bool:
            assert original_record_recent(*args, **kwargs) is True
            raise RuntimeError("fault after recent-image-history update")

        monkeypatch.setattr(
            bot, "record_recent_confirmed_regular_image", fail_after_recent
        )
    else:

        def fail_after_state(
            current_lines: set,
            current_images: set,
            current_state: dict,
            *,
            durable: bool,
        ) -> None:
            original_save_protected(
                current_lines,
                current_images,
                current_state,
                durable=durable,
            )
            raise RuntimeError("fault after state write")

        monkeypatch.setattr(
            bot, "save_regular_post_protected_state", fail_after_state
        )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError, match="950001"):
        bot.post_random_quote(lines, images, state)

    assert create_calls == 1
    status, receipt = bot.load_regular_post_receipt()
    assert status == "valid" and receipt is not None
    pin = bot.editorial_selection_pin_from_receipt(receipt)
    assert pin is not None

    monkeypatch.setattr(
        bot, "save_regular_post_protected_state", original_save_protected
    )
    monkeypatch.setattr(
        bot, "record_recent_confirmed_regular_image", original_record_recent
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail(
            "confirmed receipt reconciliation must not create a second post"
        ),
    )
    restarted_lines: set[str] = set()
    restarted_images: set[str] = set()
    restarted_state: dict = {}
    assert bot.reconcile_regular_post_receipt(
        restarted_lines, restarted_images, restarted_state
    ) is True

    assert bot.load_regular_post_receipt() == ("absent", None)
    assert restarted_lines == {pin["selected_identity"]["quote_hash"]}
    assert restarted_images == {pin["authoritative_selected_basename"]}
    assert restarted_state["last_regular_image_filename"] == pin[
        "authoritative_selected_basename"
    ]
    assert restarted_state["original_regular_posts_since_generated_image"] == (
        bot.GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN
    )
    assert restarted_state["recent_confirmed_regular_images"] == [
        {
            "post_id": "950001",
            "image_basename": pin["authoritative_selected_basename"],
            "image_sha256": pin[
                "authoritative_selected_content_sha256"
            ],
        }
    ]
