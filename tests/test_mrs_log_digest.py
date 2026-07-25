from __future__ import annotations

from datetime import datetime, timedelta

import mrs_log_digest as digest


BASE = datetime(2026, 7, 25, 9, 0, 0)


def record(
    offset: int,
    level: str,
    source: str,
    message: str,
    *,
    line: int = 1,
) -> digest.Record:
    return digest.Record(
        ts=BASE + timedelta(seconds=offset),
        level=level,
        src=source,
        line=line,
        msg=message,
        path="fixture.log",
        ordinal=offset + 1,
    )


def traceback(message: str, exception: str) -> str:
    return (
        f"{message}\n"
        "Traceback (most recent call last):\n"
        '  File "/srv/mrsMThatcher2.py", line 100, in worker\n'
        f"{exception}"
    )


def loaded_gate(offset: int) -> digest.Record:
    return record(
        offset,
        "INFO",
        "log_event",
        'EVENT {"event":"historical_context_semantic_gate","status":"loaded",'
        '"policy_version":"gate-v1","ledger_sha256":"ledger",'
        '"projection_sha256":"projection","blocked_quote_count":21}',
    )


def test_one_root_incident_groups_several_tracebacks_and_resolves():
    root = (
        "RuntimeError: historical-context source-role audit policy is incompatible"
    )
    records = [
        record(
            0,
            "ERROR",
            "post_historical_context_reply",
            traceback("Historical context reply failed independently", root),
        ),
        record(
            1,
            "ERROR",
            "post_quote_image",
            traceback("Quote/image posting failed unexpectedly", root),
        ),
        record(
            2,
            "ERROR",
            "scheduler",
            traceback("Auxiliary worker failed", root),
        ),
        loaded_gate(10),
    ]

    report = digest.analyse(records)
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    assert health["raw_serious_error_record_count"] == 3
    assert health["raw_traceback_count"] == 3
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "historical_context_source_role_incompatibility"
    assert incident["record_count"] == 3
    assert "no unresolved operational incidents" in report["summary"]["headline"]


def test_mixed_historical_and_current_errors_are_separated():
    records = [
        record(
            0,
            "ERROR",
            "post_historical_context_reply",
            traceback(
                "Historical context reply failed independently",
                "RuntimeError: historical-context source-role audit policy is incompatible",
            ),
        ),
        loaded_gate(10),
        record(
            20,
            "ERROR",
            "daily_worker",
            traceback("Current worker failed", "ValueError: current fixture failure"),
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]
    rendered = digest.render_markdown(report)

    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 1
    assert "1 unresolved operational incident" in report["summary"]["headline"]
    assert "## Current independent errors" in rendered
    assert "## Historical/resolved incident errors" in rendered
    assert "source role incompatibility" in rendered


def test_process_crash_is_historical_after_later_successful_startup():
    records = [
        record(
            0,
            "ERROR",
            "<module>",
            traceback(
                "Bot crashed with unhandled exception",
                "RuntimeError: Missing X credentials.",
            ),
        ),
        record(10, "INFO", "main", "Bot started successfully"),
    ]

    report = digest.analyse(records)

    assert report["error_health"]["current_independent_incident_count"] == 0
    incident = report["error_health"]["historical_resolved_incidents"][0]
    assert incident["category"] == "process_crash"
    assert incident["resolution_reason"] == "later successful bot startup observed"


def test_deleted_or_inaccessible_tweet_403_is_handled_separately():
    records = [
        record(
            0,
            "ERROR",
            "x_request",
            'X API error 403: {"detail":"You attempted to reply to a Tweet '
            'that is deleted or not visible to you."}',
        ),
        record(
            1,
            "ERROR",
            "post_generated_reply",
            "Failed to post generated reply",
        ),
    ]

    report = digest.analyse(records)
    rendered = digest.render_markdown(report)

    assert report["error_health"]["current_independent_incident_count"] == 0
    assert report["api_health"]["deleted_or_inaccessible_tweet_403_count"] == 1
    assert report["api_health"]["target_eligibility_403_count"] == 0
    assert "deleted/inaccessible-tweet 403 responses: **1**" in rendered
    assert "deleted or inaccessible tweet" in rendered


def semantic_veto_report() -> dict:
    report = digest.analyse([])
    report["quote_image_semantic_veto_shadow"] = {
        "summary": {
            "available": True,
            "selection_time_observations": 3,
            "confirmed_successful_posts": 2,
            "mixed_manifest_versions": True,
            "window_event_count_all_manifests": 3,
            "events_excluded_from_current_manifest_summary": 1,
            "allowed_production_winners": 1,
            "vetoed_production_winners": 0,
            "unknown_unjudged": 1,
            "generated_out_of_scope": 0,
            "in_scope_historical_selections": 2,
            "vetoed_with_allowed_alternative": 0,
            "vetoed_without_allowed_alternative": 0,
            "selection_error_candidate_available": 0,
            "coverage_gap_no_safe_image": 0,
            "quotes_with_no_globally_allowed_candidate": 0,
            "quotes_with_incomplete_pair_coverage": 1,
            "adjudicated_unknown_selections": 0,
            "not_adjudicated_selections": 1,
            "median_alternative_score_delta": None,
            "manifest_policy_version": "current-policy",
            "manifest_sha256": "c" * 64,
            "lookup_failures": 0,
            "manifest_strata": [
                {
                    "manifest_policy_version": "obsolete-policy",
                    "manifest_sha256": "a" * 64,
                    "selection_time_observations": 1,
                    "status_counts": {"allowed": 1},
                },
                {
                    "manifest_policy_version": "current-policy",
                    "manifest_sha256": "c" * 64,
                    "selection_time_observations": 2,
                    "status_counts": {"allowed": 1, "unknown": 1},
                },
            ],
        },
        "runtime_summary": {
            "available": True,
            "configured_manifest_present": True,
            "configured_manifest_available": True,
            "configured_manifest_status": "loaded",
            "configured_manifest_mode": "shadow",
            "configured_manifest_policy_version": "current-policy",
            "configured_manifest_sha256": "c" * 64,
            "configured_manifest_quote_count": 611,
            "configured_manifest_image_count": 91,
            "configured_manifest_total_authorised_pair_count": 55601,
            "configured_manifest_resolved_pair_count": 22066,
            "configured_manifest_adjudicated_unknown_pair_count": 167,
            "configured_manifest_not_adjudicated_pair_count": 33368,
            "configured_manifest_fully_unadjudicated_quotes": [
                {
                    "quote_id": "new-quotation",
                    "quote_preview": "New quotation",
                    "authorised_image_count": 91,
                    "resolved_pair_count": 0,
                    "adjudicated_unknown_count": 0,
                    "not_adjudicated_count": 91,
                }
            ],
        },
    }
    return report


def test_semantic_veto_reports_authorised_universe_strata_and_new_quote():
    rendered = digest.render_markdown(semantic_veto_report())

    assert "Mode: **shadow**" in rendered
    assert "active enforcement: **disabled" in rendered
    assert "611 quotations × 91 images = 55601 pairs" in rendered
    assert "22066 resolved" in rendered
    assert "Manifest-version strata" in rendered
    assert "obsolete-policy" in rendered
    assert "current-policy" in rendered
    assert (
        "selected quotations whose full image matrices remain unadjudicated"
        in rendered
    )
    assert "`new-quotation`" in rendered
    assert "not adjudicated **91 / 91**" in rendered
    assert "coverage state, not a runtime error" in rendered


def generated_image_report(*, detailed: bool = False) -> dict:
    report = digest.analyse([])
    report["generated_image_spacing"] = {
        "latest": {
            "pool_enabled": False,
            "allowed": True,
            "required": 3,
            "original_posts_since_generated": 4,
        },
        "events": [],
    }
    report["generated_image_pool_health"] = {
        "active_generated_images": 12,
        "quarantined_generated_images": 0,
        "total_known_generated_images": 12,
        "active_analysis_records": 12,
        "active_identity_records": 12,
        "metadata_coverage": "complete",
        "hash_valid": 12,
        "hash_total": 12,
        "hash_validation": "complete",
        "health": "OK",
        "active_never_used": 12,
    }
    rows = [
        {
            "image": f"unused-{index:02}.png",
            "origin_quote_hash": f"quote-{index:02}",
            "last_successful_post": None,
            "successful_posts": 0,
        }
        for index in range(12)
    ]
    report["generated_image_utilisation"] = {
        "active_generated_images": 12,
        "active_images_used_in_observed_logs": 0,
        "active_images_not_seen_in_observed_logs": 12,
        "active_pool_observed_usage_percentage": 0.0,
        "active_images_used_in_current_cycle": 0,
        "active_images_unused_in_current_cycle": 12,
        "total_successful_generated_posts_observed": 0,
        "maximum_successful_posts_for_one_image": 0,
        "most_frequently_used": [],
        "never_used": rows,
        "never_used_total": 12,
        "unused_longest": rows,
    }
    report["detailed_appendix"] = detailed
    return report


def test_disabled_generated_pool_and_filename_samples_are_clear():
    rendered = digest.render_markdown(generated_image_report())

    assert "generated-image pool is intentionally disabled" in rendered
    assert "`allowed=true` means a spacing rule would permit selection" in rendered
    assert "Current-cycle history records whether an image is marked used" in rendered
    assert "Bounded structured-log observations count successful post records" in rendered
    assert "count: **12**; sample: **5**" in rendered
    assert "7 additional active images omitted" in rendered
    assert "unused-04.png" in rendered
    assert "unused-05.png" not in rendered
    assert "Detailed generated-image filename appendix" not in rendered


def test_detailed_filename_appendix_is_optional():
    rendered = digest.render_markdown(generated_image_report(detailed=True))

    assert "Detailed generated-image filename appendix" in rendered
    assert "unused-11.png" in rendered


def test_singular_and_plural_wording_is_deterministic():
    assert digest.plural_count(1, "reply", "replies") == "1 reply"
    assert digest.plural_count(2, "reply", "replies") == "2 replies"
    assert digest.plural_count(1, "daily meme") == "1 daily meme"
    assert digest.plural_count(2, "daily meme") == "2 daily memes"


def test_repeated_output_is_byte_identical():
    report = semantic_veto_report()

    assert digest.render_markdown(report).encode() == digest.render_markdown(report).encode()
