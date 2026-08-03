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


def reconciled_remote_write_safety(*, archive_offset: int = 120) -> dict:
    """Return a current clear snapshot with evidence tied to t64.jpg."""

    archive_epoch = int((BASE + timedelta(seconds=archive_offset)).timestamp())
    return {
        "configured": True,
        "available": True,
        "status": "operator_paused",
        "blocking": False,
        "ready_for_remote_writes": False,
        "protocol": {"valid": True},
        "control": {
            "valid": True,
            "generation": 3,
            "active_keys": ["disable_all"],
            "global_pause_active": True,
        },
        "reconciliation_proven": True,
        "media_reconciliation_proven": True,
        "reconciliation_archive": {
            "valid": True,
            "valid_marker_reconciliation_count": 1,
            "valid_media_reconciliation_count": 1,
            "latest_marker_reconciliation": {
                "archived_at_epoch": archive_epoch,
                "audit_path": "archive/marker.reconciliation.json",
            },
            "latest_media_reconciliation": {
                "archived_at_epoch": archive_epoch - 1,
                "audit_path": "archive/media.reconciliation.json",
                "image_basename": "t64.jpg",
            },
        },
        "transport": {"classification": "clear", "blocking": False},
        "media": {"classification": "clear", "blocking": False},
        "retirement_ledgers": [
            {"valid": True, "blocking": False} for _ in range(4)
        ],
        "active_entries": [],
    }


def ambiguous_media_records() -> list[digest.Record]:
    """Represent the production receipt-bound media 503 cascade."""

    return [
        record(
            0,
            "INFO",
            "upload_media_v2",
            "Uploading receipt-bound media via X API v2: t64.jpg",
        ),
        record(
            1,
            "DEBUG",
            "x_request",
            "X request: POST https://api.x.com/2/media/upload",
        ),
        record(
            2,
            "ERROR",
            "x_request",
            'X API error 503: {"detail":"Service Unavailable","status":503}',
        ),
        record(
            3,
            "CRITICAL",
            "upload_media",
            "X media upload outcome is ambiguous; blocking every subsequent "
            "remote write pending manual reconciliation. image=t64.jpg",
        ),
        record(
            4,
            "CRITICAL",
            "record_ambiguous_remote_post",
            "AMBIGUOUS REMOTE X POST OUTCOME: X may have accepted the write, "
            "but a usable confirmation was not received. Automatic posting is "
            "blocked pending manual reconciliation: /srv/ambiguous_post_outcome.json",
        ),
        record(
            5,
            "ERROR",
            "main",
            traceback(
                "Quote/image remote outcome is ambiguous; the remote-write "
                "safety barrier is active and no retry will be scheduled",
                "AmbiguousRemotePostOutcome: X write outcome is not proved by "
                "HTTP status alone; received HTTP 503",
            ),
        ),
        record(
            6,
            "CRITICAL",
            "maintain_global_remote_write_barrier_tick",
            "All remote posting and reply lanes are paused by the durable "
            "remote-write safety barrier; manual reconciliation is required "
            "before a controlled restart",
        ),
    ]


def test_media_503_uses_exact_endpoint_and_one_durably_resolved_incident():
    safety = reconciled_remote_write_safety()
    report = digest.analyse(
        ambiguous_media_records(),
        current_remote_write_safety=safety,
    )
    report["remote_write_safety"] = safety
    rendered = digest.render_markdown(report)

    assert report["api_health"]["media_upload_request_count"] == 1
    assert report["api_health"]["tweet_create_request_count"] == 0
    assert report["api_health"]["posting_attempt_count"] == 0
    assert report["api_health"]["errors"][0]["endpoint"] == "media/upload"
    assert report["api_health"]["errors"][0]["request_method"] == "POST"
    health = report["error_health"]
    assert health["raw_serious_error_record_count"] == 5
    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "remote_write_ambiguity_barrier"
    assert incident["record_count"] == 5
    assert report["media_upload"]["handled_fallbacks"] == []
    assert len(report["media_upload"]["reconciled_incidents"]) == 1
    assert report["media_upload"]["unrecovered_failures"] == []
    assert report["media_upload"]["incidents"][0]["post_result"] == (
        "no tweet-create request observed"
    )
    assert [item["phase"] for item in report["remote_write_transactions"]] == [
        "request_started",
        "ambiguous",
    ]
    assert "## Remote-write safety" in rendered
    assert "media/upload" in rendered
    assert "reconciled_ambiguities = 1" in rendered


def test_stale_reconciliation_evidence_cannot_resolve_new_ambiguity():
    safety = reconciled_remote_write_safety(archive_offset=-60)
    report = digest.analyse(
        ambiguous_media_records(),
        current_remote_write_safety=safety,
    )

    assert report["error_health"]["current_independent_incident_count"] == 1
    assert report["error_health"]["historical_resolved_incident_count"] == 0
    assert report["media_upload"]["reconciled_incidents"] == []
    assert report["media_upload"]["incidents"][0]["status"] == "blocked"


def test_x_request_endpoint_classification_is_path_and_method_specific():
    assert digest.parse_x_request_start(
        "X request: POST https://api.x.com/2/media/upload?command=INIT"
    )["endpoint"] == "media/upload"
    assert digest.parse_x_request_start(
        "X request: POST https://api.x.com/2/tweets"
    )["endpoint"] == "tweet/create"
    assert digest.parse_x_request_start(
        "X bearer request: GET https://api.x.com/2/users/123/mentions"
    )["endpoint"] == "mentions"
    assert digest.parse_x_request_start("unrelated") is None


def test_current_remote_write_transaction_lifecycle_shapes_are_parsed():
    transaction_id = "a" * 64
    cases = [
        (
            "Creating X post with durable transport journal. lane=quote_image "
            f"transaction_id={transaction_id} reply_to_id=none media_count=1 "
            "made_with_ai=false",
            ("tweet_transport", "request_started"),
        ),
        (
            "Wrote main-post sending receipt lane=quote_image attempt_id=attempt-1 "
            "path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "sending_published"),
        ),
        (
            "Promoted main-post receipt to attempting lane=quote_image "
            "attempt_id=attempt-1 path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "attempting"),
        ),
        (
            "Handed confirmed media upload to durable main-post attempt "
            "lane=quote_image attempt_id=attempt-1 media_id=999",
            ("media_upload", "confirmed_handoff"),
        ),
        (
            "Promoted main-post attempt to confirmed pending-schedule receipt "
            "lane=quote_image attempt_id=attempt-1 post_id=123 "
            "path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "confirmed_pending_schedule"),
        ),
        (
            "Removed main-post sending receipt disposition=definite_non_success "
            "lane=quote_image attempt_id=attempt-1 "
            "path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "sending_retired"),
        ),
        (
            "Finalised confirmed pending-schedule receipt lane=quote_image "
            "post_id=123 path=/srv/regular_post_receipt.json",
            ("main_post_receipt", "schedule_finalised"),
        ),
        (
            "Resumed interrupted exact source-receipt retirement "
            "path=/srv/regular_post_receipt.json phase=exchange",
            ("source_receipt_retirement", "exchange"),
        ),
        (
            "Resumed interrupted confirmed-media fence retirement "
            f"lane=quote_image media_transaction_id={transaction_id} media_id=999",
            ("media_retirement", "resumed"),
        ),
        (
            "Recovered crash-left permanent retirement-ledger exchanges count=1",
            ("retirement_ledger", "exchange_recovered"),
        ),
    ]

    for offset, (message, expected) in enumerate(cases):
        parsed = digest.parse_remote_write_transaction_event(
            record(offset, "INFO", "fixture", message)
        )
        assert parsed is not None
        assert (parsed["kind"], parsed["phase"]) == expected


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
            "manifest_policy_version": "obsolete-policy",
            "manifest_sha256": "a" * 64,
            "lookup_failures": 0,
            "manifest_strata": [
                {
                    "manifest_policy_version": "obsolete-policy",
                    "manifest_sha256": "a" * 64,
                    "selection_time_observations": 1,
                    "status_counts": {"allowed": 1},
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
            "configured_manifest_resolved_pair_count": 22157,
            "configured_manifest_allow_pair_count": 22029,
            "configured_manifest_veto_pair_count": 128,
            "configured_manifest_adjudicated_unknown_pair_count": 167,
            "configured_manifest_not_adjudicated_pair_count": 33277,
            "manifest_policy_version": "older-policy",
            "manifest_sha256": "b" * 64,
            "runtime_status_matches_configured_manifest": False,
            "configured_manifest_named_quote_coverage": {
                "quote_id": digest.SEMANTIC_VETO_NAMED_COVERAGE_QUOTE_ID,
                "authorised_image_count": 91,
                "allow_count": 91,
                "veto_count": 0,
                "adjudicated_unknown_count": 0,
                "not_adjudicated_count": 0,
                "complete": True,
            },
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
    assert "allow 22029; veto 128" in rendered
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
    assert digest.SEMANTIC_VETO_NAMED_COVERAGE_QUOTE_ID in rendered
    assert "allow **91**; veto **0**" in rendered
    assert "row complete: **yes**" in rendered
    assert "awaiting first selection observation under configured manifest" in rendered


def test_semantic_veto_configured_observation_resolves_older_runtime_status():
    report = semantic_veto_report()
    report["quote_image_semantic_veto_shadow"]["summary"]["manifest_sha256"] = "c" * 64
    report["quote_image_semantic_veto_shadow"]["summary"]["manifest_strata"].append({
        "manifest_policy_version": "current-policy",
        "manifest_sha256": "c" * 64,
        "selection_time_observations": 1,
        "status_counts": {"allowed": 1},
    })
    rendered = digest.render_markdown(report)

    assert "configured manifest observed active" in rendered
    assert "earlier retained-manifest mismatch is resolved" in rendered
    assert "awaiting first selection observation" not in rendered


def test_semantic_veto_startup_warning_is_resolved_by_later_load():
    records = [
        record(
            0,
            "WARNING",
            "initialise_quote_image_semantic_veto_shadow",
            "Quote/image semantic-veto shadow unavailable; production selection "
            "remains unchanged. status=manifest_stale reason=source hash mismatch",
        ),
        record(
            10,
            "INFO",
            "initialise_quote_image_semantic_veto_shadow",
            "Quote/image semantic-veto shadow manifest loaded. policy=policy-v3 "
            f"sha256={'d' * 64} pairs=22157 active_enforcement=false",
        ),
    ]
    lifecycle = digest.semantic_veto_load_lifecycle(records)
    assert lifecycle["resolved_warning_count"] == 1
    assert lifecycle["unresolved_warning_count"] == 0
    report = digest.analyse(records)
    rendered = digest.render_markdown(report)
    assert "warning(s) resolved by a later successful load" in rendered
    other_warnings = rendered.split("## Other warnings", 1)[1]
    assert "semantic-veto shadow unavailable" not in other_warnings


def test_reply_summary_classifies_declines_duplicates_and_posted_modes():
    events = []
    reasons = (
        ["exact_duplicate_reply"] * 3
        + ["no substantive prompt"] * 2
        + ["editorially declined"] * 4
    )
    for index, reason in enumerate(reasons):
        events.append({
            "kind": "reply_strategy_decision",
            "lane": "mention",
            "target_id": f"decline-{index}",
            "mode": "no_reply",
            "no_reply_reason": reason,
            "factual_claim": False,
            "grounded": False,
        })
    for index in range(2):
        events.append({
            "kind": "reply_strategy_decision",
            "lane": "mention",
            "target_id": f"posted-{index}",
            "mode": "opinion_or_principle",
            "factual_claim": False,
            "grounded": False,
        })
        events.append({
            "kind": "reply_strategy_outcome",
            "status": "posted",
            "lane": "mention",
            "target_id": f"posted-{index}",
            "reply_post_id": f"reply-{index}",
            "mode": "opinion_or_principle",
            "factual_claim": False,
            "grounded": False,
        })

    summary = digest.reply_strategy_summary(events)
    assert summary["conversational_candidate_count"] == 11
    assert summary["confirmed_outcome_count"] == 2
    assert summary["deliberately_declined_count"] == 9
    assert summary["no_reply_category_counts"] == {
        "duplicate_response_rejection": 3,
        "no_substantive_prompt": 2,
        "low_value_or_repetitive_engagement": 4,
    }
    assert summary["repetition_control_counts"]["exact_duplicate_rejected"] == 3
    assert summary["claim_free_opinion_or_principle_count"] == 2
    assert summary["humour_reply_count"] == 0


def test_conversational_strategy_reply_count_is_pluralised():
    analysed = digest.analyse([
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"reply_strategy_decision","lane":"mention",'
            '"target_id":"target-1","mode":"opinion_or_principle",'
            '"factual_claim_made":false,"grounded":false}',
        ),
        record(
            1,
            "INFO",
            "log_event",
            'EVENT {"event":"reply_strategy_outcome","status":"posted",'
            '"lane":"mention","target_id":"target-1","reply_post_id":"reply-1",'
            '"mode":"opinion_or_principle","factual_claim_made":false,'
            '"grounded":false}',
        ),
    ])
    assert (
        "1 conversational candidate AI-reviewed; 1 reply posted"
        in analysed["summary"]["headline"]
    )
    assert "1 conversational candidates" not in analysed["summary"]["headline"]

    report = digest.analyse([])
    report["reply_strategy"] = {
        "conversational_candidate_count": 1,
        "confirmed_outcome_count": 1,
        "deliberately_declined_count": 0,
    }
    singular = digest.render_markdown(report)
    assert "1 conversational candidate AI-reviewed; 1 reply posted" in singular
    assert "1 conversational candidates" not in singular
    assert "1 replies posted" not in singular

    report["reply_strategy"]["conversational_candidate_count"] = 2
    report["reply_strategy"]["confirmed_outcome_count"] = 2
    plural = digest.render_markdown(report)
    assert "2 conversational candidates AI-reviewed; 2 replies posted" in plural


def test_regular_image_scores_are_formatted_without_mutating_report_values():
    image_event = {
        "kind": "regular_image_selected",
        "time": "2026-07-27 04:00:00",
        "source": "generated",
        "basename": "fixture.png",
        "score": "1.1111111111111112",
        "origin_quote_hash": "a" * 64,
        "origin_quote_match": "true",
        "origin_quote_boost": "0.050000000000000003",
        "made_with_ai": "true",
    }
    report = digest.analyse([])
    report["events"] = [dict(image_event)]
    report["regular_image_usage"] = {
        "events": [dict(image_event)],
        "summary": digest.regular_image_usage_summary([image_event]),
    }

    rendered = digest.render_markdown(report)

    assert rendered.count("1.11") == 2
    assert rendered.count("0.05") == 2
    assert "1.1111111111111112" not in rendered
    assert "0.050000000000000003" not in rendered
    assert report["events"][0]["score"] == "1.1111111111111112"
    assert report["events"][0]["origin_quote_boost"] == "0.050000000000000003"
    assert report["regular_image_usage"]["events"][0]["score"] == "1.1111111111111112"
    assert digest.render_markdown(report) == rendered


def test_editorial_shadow_rank_distribution_is_robust_to_outliers():
    events = [
        {
            "production_source": "original",
            "production_shadow_rank": rank,
            "winner_changed": rank != 1,
            "production_winner": f"production-{index}",
            "shadow_original_winner": f"shadow-{index}",
        }
        for index, rank in enumerate([1, 1, 1, 1, 1, 1, 1, 1, 2])
    ]
    summary = digest.original_editorial_shadow_summary(events)
    assert summary["average_production_winner_shadow_rank"] == 10 / 9
    assert summary["median_production_winner_shadow_rank"] == 1
    assert summary["worst_production_winner_shadow_rank"] == 2
    assert summary["production_rank_1"] == 8
    assert summary["production_rank_2_or_3"] == 1
    assert summary["production_rank_10_or_worse"] == 0
    assert summary["severe_disagreements"] == []
    report = digest.analyse([])
    report["original_editorial_shadow"] = {"events": events, "summary": summary}
    rendered = digest.render_markdown(report)
    assert "mean_production_winner_shadow_rank    = 1.11" in rendered
    assert "median_production_winner_shadow_rank  = 1" in rendered
    assert "worst_production_winner_shadow_rank   = 2" in rendered
    assert "1.1111111111111112" not in rendered


def test_equally_frequent_shadow_winners_are_not_silently_capped():
    events = [
        {
            "production_source": "original",
            "production_shadow_rank": 1,
            "winner_changed": False,
            "shadow_original_winner": f"winner-{index:02}.jpg",
        }
        for index in range(9)
    ]
    summary = digest.original_editorial_shadow_summary(events)
    assert summary["most_frequent_shadow_winners"] == [
        (f"winner-{index:02}.jpg", 1) for index in range(9)
    ]
    report = digest.analyse([])
    report["original_editorial_shadow"] = {"events": events, "summary": summary}
    rendered = digest.render_markdown(report)
    for index in range(9):
        assert f"winner-{index:02}.jpg (1)" in rendered


def test_historical_rendering_summary_and_empty_semantic_columns():
    events = [
        {
            "kind": "historical_context_reply",
            "status": "completed",
            "template_variant": "compact_with_meaning",
            "reply_preview": "Context — Speech at Chelsea, 19 September 1975.\\n\\nMeaning — X",
        },
        {
            "kind": "historical_context_reply",
            "status": "completed",
            "template_variant": "compact_with_meaning",
            "reply_preview": "Context — The surviving record dates this wording to 5 June 1987, but does not establish its occasion.",
        },
        {
            "kind": "historical_context_reply",
            "status": "completed",
            "template_variant": "compact_generic_context_omitted",
            "reply_preview": "Meaning — X",
        },
        {
            "kind": "historical_context_reply",
            "status": "completed",
            "reply_preview": "Context — The surviving attribution does not establish an occasion, date or immediate historical issue.",
        },
    ]
    summary = digest.historical_context_quality_summary(events)
    assert summary["rendering_context_counts"] == {
        "concrete_event_or_date_context_included": 1,
        "date_only_qualified_context_included": 1,
        "context_omitted_no_useful_event_or_date": 1,
        "old_generic_fallback_used": 1,
        "rendering_metadata_unavailable": 0,
    }
    report = digest.analyse([])
    report["events"] = events
    report["historical_context_quality"] = summary
    rendered = digest.render_markdown(report)
    historical_table = rendered.split("## Historical context replies", 1)[1]
    historical_header = historical_table.splitlines()[1]
    assert "overall_reply_confidence" in historical_header
    assert "historical_confidence" not in historical_header
    assert "semantic_review_disposition" not in historical_table
    assert "empty columns are omitted" in historical_table


def generated_image_report(*, detailed: bool = False) -> dict:
    report = digest.analyse([])
    report["generated_image_spacing"] = {
        "latest": {
            "pool_enabled": False,
            "allowed": True,
            "required": 3,
            "original_posts_since_generated": 4,
        },
        "events": [
            {
                "time": "2026-07-26 12:00:00",
                "kind": "state",
                "pool_enabled": False,
                "allowed": True,
                "required": 3,
                "original_posts_since_generated": 4,
            },
            {
                "time": "2026-07-27 04:00:00",
                "kind": "state",
                "pool_enabled": False,
                "allowed": True,
                "required": 3,
                "original_posts_since_generated": 4,
            },
        ],
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
    assert "same population is therefore also unused longest" in rendered
    assert rendered.count("Active generated images unused longest") == 0
    assert "All **2** spacing observations had the same state" in rendered


def test_receipt_pairs_and_pending_then_confirmed_are_not_incidents():
    report = digest.analyse([])
    report["main_post_recovery"] = {
        "receipt_events": [
            {"time": "t1", "kind": "regular_written", "level": "WARNING", "lane": "quote_image"},
            {"time": "t2", "kind": "regular_removed", "level": "INFO", "lane": "quote_image"},
        ],
        "confirmed_post_recovery": [],
    }
    report["confirmed_reply_recovery"] = {
        "receipt_events": [
            {
                "time": "t2",
                "kind": "written",
                "level": "WARNING",
                "lane": "mention",
                "target_id": "target-1",
                "reply_post_id": "reply-1",
            },
            {
                "time": "t3",
                "kind": "removed",
                "level": "INFO",
                "lane": "mention",
                "target_id": "target-1",
                "reply_post_id": "reply-1",
            },
        ],
        "warnings": [],
    }
    report["events"] = [
        {
            "kind": "posting_transaction_state",
            "time": "t1",
            "parent_post_id": "post-1",
            "main_post_state": "main_post_confirmed",
            "context_reply_state": "context_reply_pending",
        },
        {
            "kind": "historical_context_obligation",
            "time": "t2",
            "parent_post_id": "post-1",
            "status": "completed",
            "context_reply_state": "context_reply_confirmed",
        },
    ]
    rendered = digest.render_markdown(report)
    assert "## Transactional receipt lifecycle" in rendered
    assert "Routine two-phase receipt write/remove pairs completed: **1**" in rendered
    assert "Routine confirmed-reply receipt write/remove pairs completed: **1**" in rendered
    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert "## Confirmed-reply recovery" not in rendered
    assert "intermediate `context_reply_pending` states subsequently reached" in rendered
    assert "they are not outstanding" in rendered


def test_schema_v3_reply_receipt_lifecycle_is_routine_and_observable():
    records = [
        record(
            0,
            "WARNING",
            "write_sending_reply_receipt",
            "Wrote conversational reply sending receipt "
            "source=mention target_id=123 path=/tmp/reply.json",
        ),
        record(
            1,
            "WARNING",
            "promote_sending_reply_receipt",
            "Promoted conversational reply receipt to confirmed "
            "source=mention target_id=123 reply_post_id=999 "
            "path=/tmp/reply.json",
        ),
        record(
            2,
            "INFO",
            "remove_confirmed_reply_receipt",
            "Removed reconciled confirmed-reply receipt "
            "source=mention target_id=123 reply_post_id=999 "
            "path=/tmp/reply.json",
        ),
    ]

    report = digest.analyse(records)
    rendered = digest.render_markdown(report)

    assert [
        item["kind"]
        for item in report["confirmed_reply_recovery"]["receipt_events"]
    ] == ["sending", "promoted", "removed"]
    assert "Routine confirmed-reply receipt write/remove pairs completed: **1**" in rendered
    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert "## Confirmed-reply recovery" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_unresolved_schema_v3_sending_receipt_stays_current_after_other_reply():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "write_sending_reply_receipt",
                "Wrote conversational reply sending receipt "
                "source=quote_tweet target_id=456 path=/tmp/reply.json",
            ),
            record(
                1,
                "INFO",
                "maybe_reply_to_mentions",
                "Considering mention id=789 author_id=42 text='different transaction'",
            ),
            record(
                2,
                "INFO",
                "maybe_reply_to_mentions",
                "Reply posted successfully",
            ),
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply recovery" in rendered
    assert "sending_unresolved" in rendered
    assert "Pre-send reply receipt remains unresolved" in rendered
    assert report["error_health"]["current_independent_incident_count"] == 1
    assert report["error_health"]["historical_resolved_incident_count"] == 0
    assert report["error_health"]["current_incidents"][0]["category"] == (
        "conversational_reply_receipt_barrier"
    )
    assert "1 unresolved operational incident" in report["summary"]["headline"]
    assert "no unresolved operational incidents" not in report["summary"]["headline"]


def test_confirmed_state_fallback_is_not_labelled_definite_non_success():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "write_sending_reply_receipt",
                "Wrote conversational reply sending receipt "
                "source=mention target_id=123 path=/tmp/reply.json",
            ),
            record(
                1,
                "WARNING",
                "remove_confirmed_reply_receipt",
                "Removed conversational reply sending receipt after confirmed "
                "identity was preserved in canonical state "
                "source=mention target_id=123 path=/tmp/reply.json",
            ),
            record(
                2,
                "CRITICAL",
                "maybe_reply_to_mentions",
                traceback(
                    "Confirmed mention reply required its durable state fallback",
                    "ConfirmedReplyLocalPersistenceError: promotion failed",
                ),
            ),
        ]
    )

    rendered = digest.render_markdown(report)
    receipt_events = report["confirmed_reply_recovery"]["receipt_events"]

    assert "## Confirmed-reply recovery" in rendered
    assert [item["kind"] for item in receipt_events] == [
        "sending",
        "confirmed_state_fallback_removed",
    ]
    assert receipt_events[1]["disposition"] == "confirmed_state_fallback"
    assert (
        report["summary"]["stats"][
            "confirmed_reply_receipt_confirmed_state_fallback_removed"
        ]
        == 1
    )
    assert (
        report["summary"]["stats"].get(
            "confirmed_reply_receipt_sending_removed",
            0,
        )
        == 0
    )
    assert (
        "Confirmed replies preserved through the durable canonical-state "
        "fallback: **1**."
        in rendered
    )
    assert "Prepared reply receipts cleared after a definite non-success" not in rendered
    assert len(report["confirmed_reply_recovery"]["warnings"]) == 1
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_confirmed_state_fallback_removal_is_terminal_across_window_boundary():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "remove_confirmed_reply_receipt",
                "Removed conversational reply sending receipt after confirmed "
                "identity was preserved in canonical state "
                "source=quote_tweet target_id=456 path=/tmp/reply.json",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply recovery" in rendered
    assert (
        "Confirmed replies preserved through the durable canonical-state "
        "fallback: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_definite_non_success_removal_is_terminal_across_window_boundary():
    report = digest.analyse(
        [
            record(
                0,
                "INFO",
                "remove_confirmed_reply_receipt",
                "Removed conversational reply sending receipt after definite "
                "non-success source=mention target_id=123 path=/tmp/reply.json",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert (
        "Prepared reply receipts cleared after a definite non-success: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_confirmed_receipt_removal_is_terminal_across_window_boundary():
    report = digest.analyse(
        [
            record(
                0,
                "INFO",
                "remove_confirmed_reply_receipt",
                "Removed reconciled confirmed-reply receipt "
                "source=mention target_id=123 reply_post_id=999 "
                "path=/tmp/reply.json",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert (
        "Confirmed-reply receipt removals whose opening write was outside "
        "the observed window: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_reconciliation_start_without_terminal_event_remains_current():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "reconcile_confirmed_reply_receipt",
                "Reconciling confirmed reply receipt "
                "source=quote_tweet target_id=456 reply_post_id=999",
            )
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply recovery" in rendered
    assert "reconciliation_unresolved" in rendered
    assert (
        "no terminal receipt removal or completion was observed"
        in rendered
    )
    assert report["error_health"]["current_independent_incident_count"] == 1
    assert report["error_health"]["current_incidents"][0]["category"] == (
        "conversational_reply_receipt_barrier"
    )
    assert "1 unresolved operational incident" in report["summary"]["headline"]


def test_explicit_replay_suppression_completes_reconciliation_observation():
    report = digest.analyse(
        [
            record(
                0,
                "WARNING",
                "reconcile_confirmed_reply_receipt",
                "Reconciling confirmed reply receipt "
                "source=mention target_id=123 reply_post_id=999",
            ),
            record(
                1,
                "WARNING",
                "maybe_reply_to_mentions",
                "Reconciled confirmed reply receipt before checking new "
                "mention candidates",
            ),
        ]
    )

    rendered = digest.render_markdown(report)

    assert "## Confirmed-reply receipt lifecycle" in rendered
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert report["error_health"]["current_independent_incident_count"] == 0


def test_explicit_since_is_exact_and_boundary_is_inclusive(tmp_path):
    requested = "2026-07-26 10:54:03"
    log = tmp_path / "mrsMThatcher.log"
    log.write_text(
        f"{requested} INFO     fixture:1 - exact-boundary-event\n"
        "2026-07-26 10:55:03 INFO     fixture:2 - later-event\n",
        encoding="utf-8",
    )
    first = tmp_path / "digest-regression-a.md"
    second = tmp_path / "digest-regression-b.md"
    arguments = [
        "--project-dir", str(tmp_path),
        "--since", requested,
        "--until", "2026-07-26 10:55:03",
        "--no-state",
    ]
    assert digest.main([*arguments, "--output", str(first)]) == 0
    assert digest.main([*arguments, "--output", str(second)]) == 0
    rendered = first.read_text(encoding="utf-8")
    assert f"Requested since: `{requested}` (inclusive, source=manual --since)" in rendered
    assert (
        f"Observed event window: `{requested}` → `2026-07-26 10:55:03`"
        in rendered
    )
    assert "Records parsed: `2`" in rendered
    assert first.read_bytes() == second.read_bytes()


def test_engagement_metric_labels_state_exact_denominators():
    report = digest.analyse([])
    report["historical_context_engagement"] = {
        "available": True,
        "unavailable_impressions_count": 1,
        "unavailable_click_metrics_count": 295,
    }
    rendered = digest.render_markdown(report)
    assert "Latest analytics snapshots with unavailable impressions: **1**" in rendered
    assert "latest analytics snapshots with unavailable URL-link clicks: **295**" in rendered
    assert "Unavailable impressions/click metrics" not in rendered


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
