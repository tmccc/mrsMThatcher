from __future__ import annotations

import json
from datetime import datetime, timedelta

import mrs_log_digest as digest


BASE = datetime(2026, 9, 3, 12, 0, 0)


def record(offset: int, message: str, *, path: str = "bot.log") -> digest.Record:
    """Build one parsed-log fixture record."""
    return digest.Record(
        ts=BASE + timedelta(seconds=offset),
        level="INFO",
        src="mrsMThatcher2.py",
        line=1,
        msg=message,
        path=path,
        ordinal=offset + 1,
    )


def decision(
    identity: str,
    *,
    reason: str = "baseline_already_best",
    accepted: bool = False,
    policy_id: str = "policy-a",
    policy_hash: str = "a" * 64,
    breaker_open: bool = False,
) -> dict:
    """Build one authoritative production event."""
    return {
        "schema_version": 1,
        "decision_id": identity,
        "attempt_id": identity,
        "quote_hash": "q" * 64,
        "selection_phase": "normal",
        "resolved_mode": "production",
        "baseline": {
            "basename": "baseline.jpg",
            "content_sha256": "b" * 64,
            "raw_score": 4.0,
            "editorial_adjustment": 0.0,
            "combined_score": 4.0,
        },
        "challenger": {
            "basename": "challenger.jpg",
            "content_sha256": "c" * 64,
            "raw_score": 3.0,
            "editorial_adjustment": 2.0,
            "combined_score": 5.0,
        },
        "authoritative": {
            "basename": "challenger.jpg" if accepted else "baseline.jpg",
            "content_sha256": "c" * 64 if accepted else "b" * 64,
            "source": "original",
        },
        "winner_changed_by_policy": accepted,
        "policy_margin": 1.0,
        "baseline_score_loss": 1.0,
        "action": "accept_promotion" if accepted else "retain_baseline",
        "reason": "accepted_editorial_promotion" if accepted else reason,
        "policy_id": policy_id,
        "policy_sha256": policy_hash,
        "editorial_metadata_sha256": "d" * 64,
        "input_sha256": {},
        "circuit_breaker": {
            "open": breaker_open,
            "first_failure_reason": reason if breaker_open else None,
            "first_failure_time": "2026-09-03T12:00:00Z" if breaker_open else None,
            "failure_count": 1 if breaker_open else 0,
            "resolved_mode": "production",
            "mode_source": "canonical",
            "policy_id": policy_id,
            "policy_sha256": policy_hash,
        },
        "receipt_pinned": True,
    }


def confirmation(identity: str, post_id: str = "123") -> dict:
    """Build one correlated confirmed-production event."""
    return {
        "schema_version": 1,
        "decision_id": identity,
        "attempt_id": identity,
        "post_id": post_id,
        "quote_hash": "q" * 64,
        "actual_image_basename": "challenger.jpg",
        "actual_image_content_sha256": "c" * 64,
        "media_handoff_confirmed": True,
        "winner_changed_by_policy": True,
        "policy_id": "policy-a",
        "policy_sha256": "a" * 64,
        "history_updated": True,
        "receipt_retired": True,
    }


def structured(name: str, payload: dict) -> str:
    """Encode one repository-style uppercase JSON event."""
    return f"{name} {json.dumps(payload, sort_keys=True, separators=(',', ':'))}"


def test_attempted_promotion_is_not_confirmed_without_correlated_event() -> None:
    summary = digest.original_editorial_production_summary(
        [decision("1" * 64, accepted=True)], []
    )
    assert summary["attempted_promotions"] == 1
    assert summary["accepted_promotions"] == 1
    assert summary["confirmed_promotions"] == 0
    assert summary["accepted_promotion_percent"] == 100.0


def test_confirmation_correlates_by_attempt_and_deduplicates_rotated_logs() -> None:
    event = decision("1" * 64, accepted=True)
    confirmed = confirmation("1" * 64)
    records = [
        record(0, structured("ORIGINAL_EDITORIAL_PRODUCTION_DECISION", event), path="bot.log.1"),
        record(1, structured("ORIGINAL_EDITORIAL_PRODUCTION_DECISION", event), path="bot.log"),
        record(2, structured("ORIGINAL_EDITORIAL_PRODUCTION_CONFIRMED", confirmed), path="bot.log"),
        record(3, structured("ORIGINAL_EDITORIAL_PRODUCTION_CONFIRMED", confirmed), path="bot.log"),
    ]
    summary = digest.analyse(records)["original_editorial_production"]["summary"]
    assert summary["opportunities_considered"] == 1
    assert summary["attempted_promotions"] == 1
    assert summary["confirmed_promotions"] == 1
    assert summary["integrity_failure_count"] == 0


def test_unrelated_confirmation_does_not_confirm_an_attempt() -> None:
    summary = digest.original_editorial_production_summary(
        [decision("1" * 64, accepted=True)],
        [confirmation("2" * 64)],
    )
    assert summary["attempted_promotions"] == 1
    assert summary["confirmed_promotions"] == 0


def test_confirmation_must_match_the_pinned_actual_image() -> None:
    event = decision("1" * 64, accepted=True)
    confirmed = confirmation("1" * 64)
    confirmed["actual_image_content_sha256"] = "d" * 64
    summary = digest.original_editorial_production_summary(
        [event], [confirmed]
    )
    assert summary["confirmed_promotions"] == 0
    assert summary["integrity_failure_count"] == 1
    assert summary["first_integrity_failure"]["reason"] == (
        "conflicting_confirmation_event"
    )


def test_latest_breaker_and_policy_use_event_time_not_input_grouping() -> None:
    earlier = decision(
        "1" * 64, policy_id="policy-a", policy_hash="a" * 64
    )
    earlier["time"] = "2026-09-03 12:00:01"
    later = decision(
        "2" * 64,
        reason="non_finite_score",
        policy_id="policy-b",
        policy_hash="b" * 64,
        breaker_open=True,
    )
    later["time"] = "2026-09-03 12:00:03"
    explicit_breaker = dict(later["circuit_breaker"])
    explicit_breaker["time"] = "2026-09-03 12:00:04"
    explicit_breaker["failure_count"] = 2
    summary = digest.original_editorial_production_summary(
        [later, earlier], [], [explicit_breaker]
    )
    assert summary["current_policy_id"] == "policy-b"
    assert summary["current_policy_sha256"] == "b" * 64
    assert summary["circuit_breaker"]["failure_count"] == 2

    breaker_only = digest.original_editorial_production_summary(
        [], [], [explicit_breaker]
    )
    assert breaker_only["current_policy_id"] == "policy-b"
    assert breaker_only["current_policy_sha256"] == "b" * 64


def test_expected_rejections_and_integrity_failures_are_separate() -> None:
    decisions = [
        decision("1" * 64, reason="blocked_promotion_image"),
        decision("2" * 64, reason="recent_confirmed_image"),
        decision("3" * 64, reason="near_duplicate"),
        decision("4" * 64, reason="insufficient_policy_margin"),
        decision("5" * 64, reason="non_finite_score", breaker_open=True),
    ]
    summary = digest.original_editorial_production_summary(decisions, [])
    assert summary["blocked_image_exclusions"] == 1
    assert summary["recent_image_exclusions"] == 1
    assert summary["near_duplicate_exclusions"] == 1
    assert summary["insufficient_margin_exclusions"] == 1
    assert summary["integrity_failure_count"] == 1
    assert summary["circuit_breaker"]["open"] is True
    assert summary["reconciles"] is True


def test_open_integrity_breaker_is_not_recounted_as_policy_unavailable() -> None:
    first = decision(
        "1" * 64, reason="non_finite_score", breaker_open=True
    )
    later = decision(
        "2" * 64, reason="circuit_breaker_open", breaker_open=True
    )
    later["circuit_breaker"]["first_failure_reason"] = "non_finite_score"

    summary = digest.original_editorial_production_summary([first, later], [])

    assert summary["integrity_failure_count"] == 1
    assert summary["policy_unavailable_stale_unauthorised_exclusions"] == 0
    assert summary["decision_reason_counts"]["circuit_breaker_open"] == 1


def test_policy_breaker_retentions_are_policy_exclusions() -> None:
    first = decision(
        "1" * 64, reason="policy_unauthorised", breaker_open=True
    )
    later = decision(
        "2" * 64, reason="circuit_breaker_open", breaker_open=True
    )
    later["circuit_breaker"]["first_failure_reason"] = "policy_unauthorised"

    summary = digest.original_editorial_production_summary([first, later], [])

    assert summary["integrity_failure_count"] == 0
    assert summary["policy_unavailable_stale_unauthorised_exclusions"] == 2


def test_zero_denominators_and_examples_are_bounded() -> None:
    empty = digest.original_editorial_production_summary([], [])
    assert empty["accepted_promotion_percent"] == 0.0
    assert empty["reconciles"] is True
    decisions = [decision(f"{index:064x}", accepted=True) for index in range(20)]
    confirmations = [confirmation(f"{index:064x}", str(100 + index)) for index in range(20)]
    summary = digest.original_editorial_production_summary(decisions, confirmations)
    assert len(summary["accepted_examples"]) == 8
    assert len(summary["confirmation_examples"]) == 8


def test_mixed_policy_versions_are_stratified() -> None:
    summary = digest.original_editorial_production_summary(
        [
            decision("1" * 64, policy_id="policy-a", policy_hash="a" * 64),
            decision("2" * 64, policy_id="policy-b", policy_hash="b" * 64),
        ],
        [],
    )
    assert summary["mixed_policy_versions"] is True
    assert len(summary["policy_version_strata"]) == 2


def test_conflicting_duplicate_representation_is_integrity_evidence() -> None:
    first = decision("1" * 64)
    changed = decision("1" * 64, reason="historically_specific_quote")
    summary = digest.original_editorial_production_summary([first, changed], [])
    assert summary["opportunities_considered"] == 1
    assert summary["integrity_failure_count"] == 1
    assert summary["conflicting_decision_ids"] == ["1" * 64]


def test_markdown_labels_shadow_as_hypothetical_and_production_as_authoritative() -> None:
    report = digest.analyse(
        [
            record(
                0,
                structured(
                    "ORIGINAL_EDITORIAL_PRODUCTION_DECISION",
                    decision("1" * 64, accepted=True),
                ),
            ),
            record(
                1,
                "ORIGINAL_EDITORIAL_SHADOW_RESULT "
                + json.dumps(
                    {
                        "production_source": "original",
                        "winner_changed": True,
                        "shadow_original_winner": "shadow.jpg",
                    }
                ),
            ),
        ]
    )
    rendered = digest.render_markdown(report)
    assert "This section is shadow-only" in rendered
    assert "Original editorial authoritative production decisions" in rendered
    assert "confirmed_promotions           = 0" in rendered
