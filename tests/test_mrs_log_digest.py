from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

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
    marker_audit = {
        "archived_at_epoch": archive_epoch,
        "audit_path": "archive/marker.reconciliation.json",
        "marker_sha256": "a" * 64,
    }
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
            "marker_reconciliations": [marker_audit],
            "latest_marker_reconciliation": marker_audit,
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
        "active_marker_names": [],
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


RECONCILED_REPLY_TARGET = "2090236705231995281"


def digest044_reply_ambiguity_records() -> list[digest.Record]:
    """Represent the correlated reply receipt/barrier shape from digest044."""

    return [
        record(
            0,
            "WARNING",
            "write_sending_reply_receipt",
            "Wrote conversational reply sending receipt source=mention "
            f"target_id={RECONCILED_REPLY_TARGET} path=/srv/reply.json",
        ),
        record(
            1,
            "INFO",
            "create_post",
            "Creating X post with durable transport journal. "
            f"lane=conversational_reply transaction_id={'e' * 64} "
            f"reply_to_id={RECONCILED_REPLY_TARGET} media_count=0 "
            "made_with_ai=True",
        ),
        record(
            1,
            "DEBUG",
            "x_request",
            "X request: POST https://api.x.com/2/tweets",
            line=2,
        ),
        record(
            2,
            "ERROR",
            "x_request",
            "X API error 403: "
            '{"detail":"You attempted to reply to a Tweet that is deleted or '
            'not visible to you.","status":403,"title":"Forbidden",'
            '"type":"about:blank"}',
        ),
        record(
            2,
            "CRITICAL",
            "record_ambiguous_remote_post",
            "AMBIGUOUS REMOTE X POST OUTCOME: X may have accepted the write, "
            "but a usable confirmation was not received. Automatic posting is "
            "blocked pending manual reconciliation: /srv/ambiguous_post_outcome.json",
            line=2,
        ),
        record(
            2,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_outcome",'
            '"status":"posting_failed_retryable","lane":"mention",'
            f'"target_id":"{RECONCILED_REPLY_TARGET}",'
            '"failure_reason":"ambiguous_remote_outcome"}',
            line=3,
        ),
        record(
            3,
            "CRITICAL",
            "run_normal_check",
            "Normal reply lane stopped by the global remote-write safety barrier",
        ),
        record(
            60,
            "CRITICAL",
            "maintain_global_remote_write_barrier_tick",
            "All remote posting and reply lanes are paused by the durable "
            "remote-write safety barrier; manual reconciliation is required "
            "before a controlled restart",
        ),
        record(
            180,
            "INFO",
            "maybe_reply_to_mentions",
            "Considering mention id=2090237404426694900 author_id=42 "
            "text='later candidate'",
        ),
        record(
            181,
            "INFO",
            "maybe_reply_to_mentions",
            "Reply posted successfully",
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


def test_digest044_reply_barrier_symptoms_join_one_reconciled_root_everywhere():
    safety = reconciled_remote_write_safety()
    report = digest.analyse(
        digest044_reply_ambiguity_records(),
        current_remote_write_safety=safety,
    )
    rendered = digest.render_markdown(report)
    json_report = json.loads(json.dumps(report))
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    assert health["current_incidents"] == []
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "remote_write_ambiguity_barrier"
    assert incident["record_count"] == 4
    assert incident["correlated_subordinate_symptom_counts"] == {
        "conversational_reply_receipt_barrier": 1,
        "normal reply lane stopped by the global remote-write safety barrier": 1,
    }
    assert incident["correlated_reply_receipt_identities"] == [
        {"lane": "mention", "target_id": RECONCILED_REPLY_TARGET}
    ]
    assert report["confirmed_reply_recovery"][
        "durably_reconciled_ambiguity_receipts"
    ][0]["target_id"] == RECONCILED_REPLY_TARGET
    assert report["confirmed_reply_recovery"][
        "durably_reconciled_ambiguity_receipts"
    ][0]["source_time"] == digest.dt_text(BASE)
    assert "current health: no unresolved operational incidents" in report[
        "summary"
    ]["headline"]
    current_section = rendered.split("## Current independent errors", 1)[1].split(
        "## Historical/resolved incident errors", 1
    )[0]
    historical_section = rendered.split(
        "## Historical/resolved incident errors", 1
    )[1].split("## Other warnings", 1)[0]
    assert "None unresolved in the selected window." in current_section
    assert "remote write ambiguity barrier" in historical_section
    assert "conversational reply receipt barrier" not in current_section
    assert "normal reply lane stopped" not in current_section
    assert "Stale or unresolved confirmed-reply receipts:" not in rendered
    assert (
        "Sending-receipt barrier observations durably reconciled with their "
        "remote-write ambiguity: **1**."
        in rendered
    )
    assert json_report["error_health"][
        "current_independent_incident_count"
    ] == 0
    assert json_report["error_health"][
        "historical_resolved_incident_count"
    ] == 1
    assert len(json_report["error_health"]["historical_resolved_incidents"]) == 1


def test_same_reply_receipt_identity_after_reconciliation_remains_current():
    records = [
        *digest044_reply_ambiguity_records(),
        record(
            240,
            "WARNING",
            "write_sending_reply_receipt",
            "Wrote conversational reply sending receipt source=mention "
            f"target_id={RECONCILED_REPLY_TARGET} path=/srv/reply.json",
        ),
    ]
    report = digest.analyse(
        records,
        current_remote_write_safety=reconciled_remote_write_safety(),
    )
    rendered = digest.render_markdown(report)
    json_report = json.loads(json.dumps(report))

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 1
    assert health["current_incidents"][0]["category"] == (
        "conversational_reply_receipt_barrier"
    )
    assert health["current_incidents"][0]["first_seen"] == digest.dt_text(
        BASE + timedelta(seconds=240)
    )
    assert "current health: 1 unresolved operational incident" in report[
        "summary"
    ]["headline"]
    assert json_report["error_health"][
        "current_independent_incident_count"
    ] == 1
    current_section = rendered.split("## Current independent errors", 1)[1].split(
        "## Historical/resolved incident errors", 1
    )[0]
    assert "conversational reply receipt barrier" in current_section
    assert (
        "Sending-receipt barrier observations durably reconciled with their "
        "remote-write ambiguity: **1**."
        in rendered
    )
    assert "Stale or unresolved confirmed-reply receipts:" in rendered
    assert digest.dt_text(BASE + timedelta(seconds=240)) in rendered


def test_invalid_reconciliation_evidence_resolves_no_reply_barrier_symptoms():
    safety = reconciled_remote_write_safety()
    safety["reconciliation_archive"]["valid"] = False

    report = digest.analyse(
        digest044_reply_ambiguity_records(),
        current_remote_write_safety=safety,
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["category"] == (
        "remote_write_ambiguity_barrier"
    )
    assert report["confirmed_reply_recovery"][
        "durably_reconciled_ambiguity_receipts"
    ] == []


def test_clean_snapshot_does_not_resolve_unmatched_transaction_barrier():
    report = digest.analyse(
        [
            record(
                0,
                "CRITICAL",
                "remote_write_guard",
                "Remote-write transport journal blocks this unrelated write",
            )
        ],
        current_remote_write_safety=reconciled_remote_write_safety(),
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["category"] == (
        "remote_write_transaction_barrier"
    )


@pytest.mark.parametrize("active_barrier", ["receipt", "journal", "marker"])
def test_current_reply_receipt_journal_or_marker_keeps_root_current(
    active_barrier: str,
):
    safety = reconciled_remote_write_safety()
    safety["blocking"] = True
    if active_barrier == "journal":
        safety["transport"] = {
            "classification": "attempting",
            "blocking": True,
        }
    else:
        safety["active_entries"] = [
            {
                "name": (
                    "confirmed_reply_receipt.json"
                    if active_barrier == "receipt"
                    else "ambiguous_post_outcome.json"
                ),
                "kind": (
                    "source_receipt"
                    if active_barrier == "receipt"
                    else "ambiguity_marker"
                ),
                "safe_regular": True,
            }
        ]
        if active_barrier == "marker":
            safety["active_marker_names"] = ["ambiguous_post_outcome.json"]

    report = digest.analyse(
        digest044_reply_ambiguity_records(),
        current_remote_write_safety=safety,
    )

    assert report["error_health"]["current_independent_incident_count"] == 1
    assert report["error_health"]["historical_resolved_incident_count"] == 0


def test_unrelated_current_error_stays_current_after_reply_root_reconciliation():
    report = digest.analyse(
        [
            *digest044_reply_ambiguity_records(),
            record(
                220,
                "ERROR",
                "unrelated_worker",
                "ValueError: unrelated current failure",
            ),
        ],
        current_remote_write_safety=reconciled_remote_write_safety(),
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 1
    assert health["current_incidents"][0]["category"] == "valueerror"
    assert health["historical_resolved_incidents"][0]["category"] == (
        "remote_write_ambiguity_barrier"
    )


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
    assert summary["deliberately_declined_count"] == 6
    assert summary["terminal_repetition_rejection_count"] == 3
    assert summary["outcome_status_counts"] == {
        "posted": 2,
        "terminal_no_reply": 6,
        "terminal_repetition_rejection": 3,
    }
    assert summary["no_reply_category_counts"] == {
        "no_substantive_prompt": 2,
        "low_value_or_repetitive_engagement": 4,
    }
    assert summary["repetition_control_counts"]["exact_duplicate_rejected"] == 3
    assert summary["claim_free_opinion_or_principle_count"] == 2
    assert summary["humour_reply_count"] == 0


def test_pipeline_failure_and_apierror_wrapper_are_one_current_incident():
    target_id = "2086177789732958385"
    records = [
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"mention",'
            f'"target_id":"{target_id}",'
            '"reason":"no_reply_reviewer_invalid"}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: reviewer returned an invalid no-reply verdict",
            ),
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["raw_serious_error_record_count"] == 1
    assert health["raw_traceback_count"] == 1
    incident = health["current_incidents"][0]
    assert incident["category"] == "reply_strategy_pipeline_failure"
    assert incident["lane"] == "mention"
    assert incident["target_id"] == target_id
    assert incident["pipeline_failure_event_count"] == 1
    assert incident["wrapper_record_count"] == 1
    assert incident["record_count"] == 1
    assert incident["pipeline_failure_reason_counts"] == {
        "no_reply_reviewer_invalid": 1
    }
    raw_errors = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "Failed to ask Grok for reply" in raw_errors
    assert "APIError" in raw_errors
    assert "Traceback" in raw_errors


def test_pipeline_failure_raw_evidence_resolves_as_one_incident():
    target_id = "2086177789732958385"
    records = [
        record(
            0,
            "ERROR",
            "build_ai_reply",
            "AI-first reply pipeline ended status=operational_failure lane=mention "
            f"target_id={target_id} reason=no_reply_reviewer_invalid "
            "calls=3 revisions=0",
        ),
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"mention",'
            f'"target_id":"{target_id}",'
            '"reason":"no_reply_reviewer_invalid"}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: reviewer returned an invalid no-reply verdict",
            ),
        ),
        record(
            10,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision",'
            '"status":"no_reply","lane":"mention",'
            f'"target_id":"{target_id}","mode":"no_reply",'
            '"reviewer_verdict":"confirm_no_reply",'
            '"reason":"independent_no_reply_confirmed"}',
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "reply_strategy_pipeline_failure"
    assert incident["record_count"] == 2
    assert incident["wrapper_record_count"] == 1
    assert incident["pipeline_failure_event_count"] == 1
    assert incident["traceback_count"] == 1
    assert incident["pipeline_failure_reason_counts"] == {
        "no_reply_reviewer_invalid": 1
    }
    raw_errors = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "AI-first reply pipeline ended" in raw_errors
    assert "Failed to ask Grok for reply" in raw_errors
    assert "APIError" in raw_errors
    assert "Traceback" in raw_errors


def test_pipeline_failure_raw_error_without_structured_match_stays_independent():
    target_id = "2086177789732958385"
    report = digest.analyse(
        [
            record(
                0,
                "ERROR",
                "build_ai_reply",
                "AI-first reply pipeline ended status=operational_failure "
                f"lane=mention target_id={target_id} "
                "reason=no_reply_reviewer_invalid calls=3 revisions=0",
            )
        ]
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["record_count"] == 1
    raw_errors = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "AI-first reply pipeline ended" in raw_errors


def test_pipeline_failure_wrapper_resolves_after_same_target_terminal_no_reply():
    target_id = "2086177789732958385"
    records = [
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"mention",'
            f'"target_id":"{target_id}",'
            '"reason":"no_reply_reviewer_invalid"}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: reviewer returned an invalid no-reply verdict",
            ),
        ),
        record(
            10,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision",'
            '"status":"no_reply","lane":"mention",'
            f'"target_id":"{target_id}","mode":"no_reply",'
            '"reviewer_verdict":"confirm_no_reply",'
            '"reason":"independent_no_reply_confirmed"}',
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]
    rendered = digest.render_markdown(report)

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "reply_strategy_pipeline_failure"
    assert incident["target_id"] == target_id
    assert incident["record_count"] == 1
    assert incident["resolution_reason"] == (
        "later terminal no-reply decision observed for mention target " + target_id
    )
    assert incident["resolution_time"] == "2026-07-25 09:00:10"
    assert "None unresolved in the selected window." in rendered
    assert "reply strategy pipeline failure" in rendered
    raw_errors = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "Failed to ask Grok for reply" in raw_errors
    assert "APIError" in raw_errors
    assert "Traceback" in raw_errors


def test_later_terminal_pipeline_failure_does_not_resolve_pipeline_incident():
    target_id = "2086177789732958385"
    records = [
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"mention",'
            f'"target_id":"{target_id}","reason":"writer_invalid"}}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: writer returned an invalid reply",
            ),
        ),
        record(
            10,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision",'
            '"status":"no_reply","lane":"mention",'
            f'"target_id":"{target_id}","mode":"no_reply",'
            '"reason":"revision_limit_reached"}',
        ),
    ]

    health = digest.analyse(records)["error_health"]

    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["target_id"] == target_id


@pytest.mark.parametrize("duplicate_reason", ["exact_duplicate_reply", "near_duplicate_reply"])
def test_pipeline_failure_wrapper_resolves_after_terminal_duplicate_decision(
    duplicate_reason: str,
):
    target_id = "2086177789732958385"
    records = [
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"mention",'
            f'"target_id":"{target_id}",'
            '"reason":"no_reply_reviewer_invalid"}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: reviewer returned an invalid no-reply verdict",
            ),
        ),
        record(
            10,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision",'
            '"status":"no_reply","lane":"mention",'
            f'"target_id":"{target_id}","mode":"courtesy",'
            f'"reason":"{duplicate_reason}","reviewer_verdict":"not_run"}}',
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "reply_strategy_pipeline_failure"
    assert incident["lane"] == "mention"
    assert incident["target_id"] == target_id
    assert incident["pipeline_failure_event_count"] == 1
    assert incident["wrapper_record_count"] == 1
    assert incident["record_count"] == 1
    assert incident["resolution_reason"] == (
        "later terminal local decision observed for mention target " + target_id
    )
    assert incident["resolution_time"] == "2026-07-25 09:00:10"
    raw_errors = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "Failed to ask Grok for reply" in raw_errors
    assert "APIError" in raw_errors
    assert "Traceback" in raw_errors


def test_hot_post_pipeline_wrapper_from_combined_handler_resolves_once():
    target_id = "2086177789732958386"
    records = [
        record(
            0,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_failure",'
            '"status":"operational_failure","lane":"hot-post",'
            f'"target_id":"{target_id}",'
            '"reason":"no_reply_reviewer_invalid"}',
        ),
        record(
            0,
            "ERROR",
            "maybe_reply_to_mentions",
            traceback(
                "Failed to ask Grok for reply",
                "APIError: reviewer returned an invalid no-reply verdict",
            ),
        ),
        record(
            10,
            "INFO",
            "log_event",
            'EVENT {"event":"ai_reply_pipeline_decision",'
            '"status":"no_reply","lane":"hot-post",'
            f'"target_id":"{target_id}","mode":"courtesy",'
            '"reason":"exact_duplicate_reply","reviewer_verdict":"not_run"}',
        ),
    ]

    report = digest.analyse(records)
    health = report["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "reply_strategy_pipeline_failure"
    assert incident["lane"] == "hot-post"
    assert incident["target_id"] == target_id
    assert incident["pipeline_failure_event_count"] == 1
    assert incident["wrapper_record_count"] == 1
    assert incident["record_count"] == 1
    assert incident["resolution_reason"] == (
        "later terminal local decision observed for hot-post target " + target_id
    )


def test_unrelated_apierror_remains_independent_of_resolved_pipeline_wrapper():
    target_id = "2086177789732958385"
    report = digest.analyse(
        [
            record(
                0,
                "INFO",
                "log_event",
                'EVENT {"event":"ai_reply_pipeline_failure",'
                '"status":"operational_failure","lane":"mention",'
                f'"target_id":"{target_id}",'
                '"reason":"no_reply_reviewer_invalid"}',
            ),
            record(
                0,
                "ERROR",
                "maybe_reply_to_mentions",
                traceback(
                    "Failed to ask Grok for reply",
                    "APIError: reviewer returned an invalid no-reply verdict",
                ),
            ),
            record(
                10,
                "INFO",
                "log_event",
                'EVENT {"event":"ai_reply_pipeline_decision",'
                '"status":"no_reply","lane":"mention",'
                f'"target_id":"{target_id}","mode":"no_reply",'
                '"reviewer_verdict":"confirm_no_reply",'
                '"reason":"independent_no_reply_confirmed"}',
            ),
            record(
                20,
                "ERROR",
                "unrelated_worker",
                traceback(
                    "Unrelated provider operation failed",
                    "APIError: unrelated fixture failure",
                ),
            ),
        ]
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 1
    assert health["current_incidents"][0]["category"] == "apierror"
    assert health["current_incidents"][0]["record_count"] == 1
    assert health["raw_serious_error_record_count"] == 2


def carried_state_report(state_timestamp: str | None) -> dict:
    report = digest.analyse([])
    report["summary"]["time_start"] = "2026-07-25 09:00:00"
    report["summary"]["time_end"] = "2026-07-25 12:00:00"
    report["latest_state"] = {
        "time": state_timestamp,
        "_carried_forward": True,
        "daily_reply_date": "2026-07-24",
        "daily_reply_count": 7,
        "daily_quote_reply_date": "2026-07-24",
        "daily_quote_reply_count": 3,
        "next_reply_lane_priority": "quote-tweet",
        "next_quote_post_epoch": 1784984400,
        "next_quote_post_human": "2026-07-25 13:00:00",
        "next_meme_post_epoch": 1784988000,
        "next_meme_post_human": "2026-07-25 14:00:00",
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": "2026-07-25",
    }
    report["latest_config"] = {
        "MAX_AUTO_REPLIES_PER_DAY": "12",
        "MAX_QUOTE_REPLIES_PER_DAY": "6",
        "ENABLE_DAILY_MEME_POSTS": "true",
    }
    digest.refresh_derived(report)
    return report


def test_carried_forward_state_before_window_is_stale_snapshot():
    rendered = digest.render_markdown(
        carried_state_report("2026-07-24 09:00:00")
    )

    assert "## Latest state (stale carried-forward snapshot)" in rendered
    assert "stale snapshot age at window end: 1 day 3 hours" in rendered
    assert "counters and schedules below are not current" in rendered
    assert "snapshot_daily_reply_count       = 7" in rendered
    assert "snapshot_next_quote_post" in rendered
    assert "snapshot_next_meme_post" in rendered
    assert "snapshot_next_meme" in rendered
    assert "current_next_meme" not in rendered
    assert "snapshot auto replies used  = 7 / 12" in rendered
    assert "snapshot quote replies used = 3 / 6" in rendered
    assert "snapshot_next_priority" in rendered
    assert "current_next_priority" not in rendered


def test_carried_forward_state_timestamp_within_window_is_not_stale():
    rendered = digest.render_markdown(
        carried_state_report("2026-07-25 10:00:00")
    )

    assert "## Latest state\n" in rendered
    assert (
        "State timestamp: `2026-07-25 10:00:00` "
        "(carried forward from previous digest state)"
    ) in rendered
    assert "stale snapshot" not in rendered
    assert "daily_reply_count       = 7" in rendered
    assert "snapshot_daily_reply_count" not in rendered
    assert "current_next_meme" in rendered
    assert "auto replies used  = 7 / 12" in rendered
    assert "current_next_priority" in rendered


def test_carried_forward_state_without_timestamp_has_unknown_age():
    rendered = digest.render_markdown(carried_state_report(None))

    assert "## Latest state (carried-forward snapshot; age unavailable)" in rendered
    assert "age and staleness unavailable" in rendered
    assert "without a state timestamp their currentness cannot be established" in rendered
    assert "snapshot_daily_reply_count       = 7" in rendered
    assert "snapshot_next_quote_post" in rendered
    assert "snapshot_next_meme_post" in rendered
    assert "snapshot auto replies used  = 7 / 12" in rendered


def test_current_runtime_state_fresh_absent_and_malformed(tmp_path):
    state_path = tmp_path / "bot_state.json"

    state, path, timestamp, status = digest.load_current_runtime_state(tmp_path)
    assert state is None
    assert path == state_path
    assert timestamp is None
    assert status == "absent"

    state_path.write_text("not json", encoding="utf-8")
    state, _path, timestamp, status = digest.load_current_runtime_state(tmp_path)
    assert state is None
    assert timestamp is None
    assert status.startswith("malformed:")

    state_path.write_text(
        json.dumps(
            {
                "daily_reply_count": 4,
                "daily_quote_reply_count": 2,
                "daily_reply_date": "2026-08-16",
            }
        ),
        encoding="utf-8",
    )
    state, _path, timestamp, status = digest.load_current_runtime_state(tmp_path)
    assert status == "available"
    assert state["daily_reply_count"] == 4
    assert timestamp is not None


def test_digest_resume_state_never_falls_back_as_current_runtime_state(tmp_path):
    resume_path = tmp_path / "digest-resume.json"
    resume_path.write_text(
        json.dumps(
            {
                "last_log_entry_time": "2026-08-07 12:00:00",
                "last_known_latest_state": {
                    "time": "2026-08-07 12:00:00",
                    "daily_reply_count": 999,
                    "next_reply_lane_priority": "quote",
                },
                "last_known_latest_config": {
                    "MAX_AUTO_REPLIES_PER_DAY": "999"
                },
            }
        ),
        encoding="utf-8",
    )
    report = digest.analyse([])
    report["latest_state"] = {}
    report["latest_config"] = {}

    digest.apply_saved_context(report, resume_path)

    assert report["latest_state"] == {}
    assert report["latest_config"] == {}
    assert report["historical_retained_state"]["daily_reply_count"] == 999
    assert report["historical_retained_config"]["MAX_AUTO_REPLIES_PER_DAY"] == "999"
    report["runtime_state_status"] = {
        "status": "absent",
        "path": str(tmp_path / "bot_state.json"),
    }
    report["runtime_config_status"] = {
        "status": "absent",
        "path": str(tmp_path / "mrsMThatcher.local.json"),
    }
    rendered = digest.render_markdown(report)
    assert "Current bot runtime state: **unavailable**" in rendered
    assert "## Historical retained diagnostic snapshots" in rendered
    assert "for historical diagnosis only" in rendered
    assert '"daily_reply_count": 999' in rendered
    assert '"MAX_AUTO_REPLIES_PER_DAY": "999"' in rendered
    assert "daily_reply_count       = 999" not in rendered
    assert "MAX_AUTO_REPLIES_PER_DAY=999" not in rendered


def test_unavailable_runtime_reads_retain_historical_snapshots_across_runs(tmp_path):
    resume_path = tmp_path / "digest-resume.json"
    log_path = tmp_path / "bot.log"
    original_state = {
        "time": "2026-07-25 08:00:00",
        "daily_reply_count": 7,
    }
    original_config = {"MAX_AUTO_REPLIES_PER_DAY": 48}
    resume_path.write_text(
        json.dumps(
            {
                "last_log_entry_time": "2026-07-25 08:00:00",
                "last_known_latest_state": original_state,
                "last_known_latest_config": original_config,
            }
        ),
        encoding="utf-8",
    )

    for offset in (0, 1):
        records = [record(offset, "INFO", "worker", "still running")]
        report = digest.analyse(records)
        digest.apply_saved_context(report, resume_path)
        report["latest_state"] = {}
        report["latest_config"] = {}
        report["runtime_state_status"] = {"status": "absent"}
        report["runtime_config_status"] = {
            "status": "malformed: JSONDecodeError"
        }

        digest.save_resume_time(
            resume_path,
            records[-1].ts,
            records,
            report,
            [log_path],
        )
        saved = digest.read_resume_data(resume_path)
        assert saved["last_known_latest_state"] == original_state
        assert saved["last_known_latest_config"] == original_config

    empty_override = tmp_path / "mrsMThatcher.local.json"
    empty_override.write_text("{}", encoding="utf-8")
    current_config, _path, _timestamp, config_status = (
        digest.load_current_runtime_config(tmp_path)
    )
    records = [record(2, "INFO", "worker", "runtime reads recovered")]
    report = digest.analyse(records)
    digest.apply_saved_context(report, resume_path)
    report["latest_state"] = {"daily_reply_count": 2}
    report["latest_config"] = current_config or {}
    report["runtime_state_status"] = {"status": "available"}
    report["runtime_config_status"] = {"status": config_status}

    digest.save_resume_time(
        resume_path,
        records[-1].ts,
        records,
        report,
        [log_path],
    )
    saved = digest.read_resume_data(resume_path)
    assert saved["last_known_latest_state"] == {"daily_reply_count": 2}
    assert saved["last_known_latest_config"] == {}


def test_current_runtime_config_is_allow_listed_and_validated(tmp_path):
    config_path = tmp_path / "mrsMThatcher.local.json"
    config_path.write_text(
        json.dumps(
            {
                "MAX_AUTO_REPLIES_PER_DAY": 48,
                "MAX_REPLIES_PER_AUTHOR_PER_DAY": 6,
                "MAX_QUOTE_REPLIES_PER_DAY": 12,
                "OPENAI_API_KEY": "must-not-appear",
            }
        ),
        encoding="utf-8",
    )

    config, path, timestamp, status = digest.load_current_runtime_config(tmp_path)

    assert status == "available"
    assert path == config_path
    assert timestamp is not None
    assert config["MAX_AUTO_REPLIES_PER_DAY"] == 48
    assert config["MAX_REPLIES_PER_AUTHOR_PER_DAY"] == 6
    assert config["MAX_QUOTE_REPLIES_PER_DAY"] == 12
    assert "OPENAI_API_KEY" not in config


@pytest.mark.parametrize(
    ("runtime_status", "state", "headline_text", "body_text"),
    [
        (
            "absent",
            None,
            "current API cooldown state unavailable",
            "Current bot runtime state: **unavailable**",
        ),
        (
            "available",
            {
                "api_cooldown_until_epoch": 0,
                "x_write_api_cooldown_until_epoch": 0,
                "xai_api_cooldown_until_epoch": int(BASE.timestamp()) + 60,
                "quote_api_cooldown_until_epoch": 0,
            },
            "xAI cooldown active now",
            f"xai_api_cooldown_until  = {int(BASE.timestamp()) + 60}  2026-07-25 09:01:00  active",
        ),
        (
            "available",
            {
                "api_cooldown_until_epoch": 0,
                "x_write_api_cooldown_until_epoch": 0,
                "xai_api_cooldown_until_epoch": int(BASE.timestamp()) - 60,
                "quote_api_cooldown_until_epoch": 0,
            },
            "xAI cooldown occurred, now expired",
            f"xai_api_cooldown_until  = {int(BASE.timestamp()) - 60}  2026-07-25 08:59:00  expired",
        ),
        (
            "available",
            {
                "api_cooldown_until_epoch": 0,
                "x_write_api_cooldown_until_epoch": 0,
                "xai_api_cooldown_until_epoch": 0,
                "quote_api_cooldown_until_epoch": 0,
            },
            "no API cooldown",
            "xai_api_cooldown_until  = 0  none  cleared",
        ),
    ],
)
def test_current_cooldown_headline_and_body_use_generation_time(
    runtime_status,
    state,
    headline_text,
    body_text,
    tmp_path,
):
    generation_epoch = int(BASE.timestamp())
    report = digest.analyse([], generation_time=BASE)
    report["generation_epoch"] = generation_epoch
    report["runtime_state_status"] = {
        "status": runtime_status,
        "path": str(tmp_path / "bot_state.json"),
    }
    # Deliberately use a state-file timestamp on the opposite side of the
    # cooldown to prove that current health is evaluated at generation time.
    state_file_time = BASE - timedelta(days=30)
    if state and state.get("xai_api_cooldown_until_epoch", 0) < generation_epoch:
        state_file_time = BASE + timedelta(days=30)
    report["latest_state"] = (
        digest.summarize_latest_state(
            state,
            state_file_time,
            source="bot_state.json",
            source_path=tmp_path / "bot_state.json",
        )
        if state is not None
        else {}
    )

    digest.refresh_derived(report)
    rendered = digest.render_markdown(report)

    assert headline_text in report["summary"]["headline"]
    assert headline_text in rendered
    assert body_text in rendered


@pytest.mark.parametrize("contents", [None, {}, {"MAX_AUTO_REPLIES_PER_DAY": 48}])
def test_local_config_is_described_as_on_disk_not_effective_live(
    contents,
    tmp_path,
):
    config_path = tmp_path / "mrsMThatcher.local.json"
    if contents is not None:
        config_path.write_text(json.dumps(contents), encoding="utf-8")
    config, path, timestamp, status = digest.load_current_runtime_config(tmp_path)
    report = digest.analyse([])
    report["latest_config"] = config or {}
    report["runtime_config_status"] = {
        "status": status,
        "path": str(path),
        "time": digest.dt_text(timestamp) if timestamp else None,
    }
    rendered = digest.render_markdown(report)

    assert "## On-disk local configuration overrides" in rendered
    assert "Current production configuration" not in rendered
    if contents is None:
        assert "On-disk local overrides: **unavailable**" in rendered
        assert "Effective live configuration is not established" in rendered
    else:
        assert "read-only file observation does not establish" in rendered
        assert "effective in a live process" in rendered


def test_reply_accounting_reconciles_terminal_local_rejections_and_timeout_wrapper():
    def event_row(offset: int, payload: dict) -> digest.Record:
        return record(
            offset,
            "INFO",
            "log_event",
            "EVENT " + json.dumps(payload, separators=(",", ":")),
        )

    records: list[digest.Record] = []
    for index in range(6):
        target_id = str(100 + index)
        records.extend(
            [
                event_row(
                    index * 3,
                    {
                        "event": "ai_reply_pipeline_decision",
                        "status": "approved",
                        "lane": "mention",
                        "target_id": target_id,
                        "mode": "opinion_or_principle",
                        "tone": "firm",
                        "factual_claim_count": 0,
                        "model_call_count": 2,
                        "revision_count": 0,
                    },
                ),
                event_row(
                    index * 3 + 1,
                    {
                        "event": "ai_reply_pipeline_outcome",
                        "status": "confirmed",
                        "lane": "mention",
                        "target_id": target_id,
                        "reply_post_id": str(900 + index),
                        "mode": "opinion_or_principle",
                        "tone": "firm",
                        "factual_claim_count": 0,
                        "model_call_count": 2,
                        "revision_count": 0,
                    },
                ),
            ]
        )
    records.append(
        event_row(
            20,
            {
                "event": "ai_reply_pipeline_decision",
                "status": "no_reply",
                "lane": "mention",
                "target_id": "200",
                "mode": "opinion_or_principle",
                "tone": "firm",
                "factual_claim_count": 0,
                "reason": "near_duplicate_reply",
                "model_call_count": 1,
                "revision_count": 0,
            },
        )
    )
    records.extend(
        [
            record(
                22,
                "INFO",
                "maybe_reply_to_mentions",
                "Considering mention id=300 author_id=42 text='Which way?'",
            ),
            event_row(
                23,
                {
                    "event": "ai_reply_pipeline_decision",
                    "status": "approved",
                    "lane": "mention",
                    "target_id": "300",
                    "mode": "opinion_or_principle",
                    "tone": "firm",
                    "factual_claim_count": 0,
                    "model_call_count": 3,
                    "revision_count": 0,
                },
            ),
            record(
                24,
                "ERROR",
                "maybe_reply_to_mentions",
                "Clarification reply lacks direct_factual_answer mode; refusing "
                "target_id=300",
            ),
        ]
    )
    timeout_detail = (
        "HTTPSConnectionPool(host='api.x.ai', port=443): Read timed out. "
        "(read timeout=30)"
    )
    records.extend(
        [
            record(
                30,
                "ERROR",
                "xai_structured_reply_call",
                "xAI reply stage=proposer failed before receiving a response\n"
                "Traceback (most recent call last):\n"
                '  File "/srv/mrsMThatcher2.py", line 1, in xai_structured_reply_call\n'
                f"requests.exceptions.ReadTimeout: {timeout_detail}",
            ),
            record(
                30,
                "ERROR",
                "maybe_reply_to_mentions",
                "Failed to ask Grok for reply\n"
                "Traceback (most recent call last):\n"
                '  File "/srv/mrsMThatcher2.py", line 2, in maybe_reply_to_mentions\n'
                f"requests.exceptions.ReadTimeout: {timeout_detail}\n"
                "The above exception was the direct cause of the following exception:\n"
                "Traceback (most recent call last):\n"
                '  File "/srv/mrsMThatcher2.py", line 3, in maybe_reply_to_mentions\n'
                f"ApiError: {timeout_detail}",
            ),
        ]
    )

    report = digest.analyse(records)
    strategy = report["reply_strategy"]
    cost = report["xai_usage"]["cost_summary"]
    health = report["error_health"]
    outcome_counts = {
        row["outcome"]: row["candidate_count"] for row in cost["outcomes"]
    }

    assert strategy["conversational_candidate_count"] == 8
    assert strategy["confirmed_outcome_count"] == 6
    assert strategy["terminal_repetition_rejection_count"] == 1
    assert strategy["terminal_clarification_mode_rejection_count"] == 1
    assert strategy["deliberately_declined_count"] == 0
    assert cost["candidate_count"] == 8
    assert cost["published_candidate_count"] == 6
    assert outcome_counts == {
        "published": 6,
        "terminal_clarification_mode_rejection": 1,
        "terminal_repetition_rejection": 1,
    }
    assert all(
        row["outcome"] != "approved_not_confirmed_in_window"
        for row in cost["candidates"]
    )
    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 0
    assert health["transient_provider_timeout_count"] == 1
    assert health["transient_provider_timeout_record_count"] == 2
    assert health["transient_provider_observation_count"] == 1
    observation = health["transient_provider_observations"][0]
    assert observation["record_count"] == 2
    assert observation["status"] == "transient_observation_recovery_unverified"
    assert report["api_health"]["transient_failure_count"] == 1
    assert len(report["errors_and_warnings"]) == 3
    raw_error_detail = "\n".join(
        row["message"] for row in report["errors_and_warnings"]
    )
    assert "Clarification reply lacks direct_factual_answer mode" in raw_error_detail
    assert "requests.exceptions.ReadTimeout" in raw_error_detail
    assert "ApiError" in raw_error_detail
    assert sum(
        "Traceback" in row["message"] for row in report["errors_and_warnings"]
    ) == 2
    assert "8 conversational candidates AI-reviewed" in report["summary"]["headline"]
    assert "6 replies posted" in report["summary"]["headline"]
    assert "1 terminal repetition rejection" in report["summary"]["headline"]
    assert "1 terminal clarification-mode rejection" in report["summary"]["headline"]
    assert "current health: no unresolved operational incidents" in report["summary"]["headline"]
    assert "1 transient provider timeout" in report["summary"]["headline"]
    assert "provider recovery unverified" in report["summary"]["headline"]
    rendered = digest.render_markdown(report)
    assert "## Transient provider observations" in rendered
    assert "Provider recovery is unverified" in rendered
    assert "xai provider timeout" in rendered


def test_lone_x_transient_failure_is_observed_without_claiming_resolution():
    report = digest.analyse(
        [record(0, "ERROR", "x_request", "X API error 503: Service Unavailable")]
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 0
    assert health["transient_provider_observation_count"] == 1
    assert health["transient_provider_observations"][0]["category"] == (
        "x_api_transient_failure"
    )
    rendered = digest.render_markdown(report)
    assert "Provider recovery is unverified" in rendered
    assert "historically resolved incidents" in rendered


def test_x_429_and_cooldown_are_one_resolved_incident_after_later_success():
    records = [
        record(
            0,
            "ERROR",
            "maybe_reply_to_quote_tweets",
            traceback(
                "Failed to fetch quote tweets for post 900",
                'ApiError: X bearer API error 429: {"status":429}',
            ),
        ),
        record(
            1,
            "ERROR",
            "record_api_error",
            "Entering API cooldown after 429 until 2026-07-25 09:01:00",
        ),
        record(120, "INFO", "get_mentions", "Fetched 0 mentions"),
    ]

    health = digest.analyse(records)["error_health"]

    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "x_api_rate_limit"
    assert incident["record_count"] == 2
    assert incident["resolution_reason"] == (
        "cooldown deadline passed and later successful X activity was observed"
    )


def test_success_before_later_429_does_not_resolve_that_incident():
    records = [
        record(0, "INFO", "get_mentions", "Fetched 0 mentions"),
        record(
            60,
            "ERROR",
            "x_request",
            'X bearer API error 429: {"status":429}',
        ),
        record(
            61,
            "ERROR",
            "record_api_error",
            "Entering API cooldown after 429 until 2026-07-25 09:02:00",
        ),
    ]

    health = digest.analyse(
        records,
        generation_time=BASE + timedelta(minutes=10),
    )["error_health"]

    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["category"] == "x_api_rate_limit"


def test_active_x_429_cooldown_remains_one_current_incident():
    records = [
        record(
            0,
            "ERROR",
            "x_request",
            'X bearer API error 429: {"status":429}',
        ),
        record(
            1,
            "ERROR",
            "record_api_error",
            "Entering API cooldown after 429 until 2099-07-25 09:01:00",
        ),
    ]

    health = digest.analyse(records)["error_health"]

    assert health["current_independent_incident_count"] == 1
    assert health["historical_resolved_incident_count"] == 0
    assert health["current_incidents"][0]["category"] == "x_api_rate_limit"
    assert health["current_incidents"][0]["record_count"] == 2


def test_repeated_quote_token_event_is_warning_not_failure():
    payload = {
        "event": "quote_pagination_repeated_token",
        "post_id": "900",
        "token_fingerprint": "abc123",
        "pages_completed": 2,
        "results_retained": 7,
    }
    report = digest.analyse(
        [
            record(
                0,
                "INFO",
                "log_event",
                "EVENT " + json.dumps(payload, separators=(",", ":")),
            )
        ]
    )

    assert report["error_health"]["current_independent_incident_count"] == 0
    assert report["events"][0]["kind"] == "quote_pagination_repeated_token"
    rendered = digest.render_markdown(report)
    assert "## Bounded protocol warnings" in rendered
    assert "warnings, not operational failures" in rendered


def test_prefixed_quote_pagination_traceback_stays_visible_but_resolves():
    report = digest.analyse(
        [
            record(
                0,
                "ERROR",
                "maybe_reply_to_quote_tweets",
                traceback(
                    "Failed to fetch quote tweets for post 900",
                    "PaginationCursorProtocolError: X quote tweets for 900 "
                    "returned a repeated pagination token",
                ),
            ),
            record(
                60,
                "INFO",
                "get_quote_tweets_for_post",
                "Fetched 0 quote tweet(s) for post_id=901",
            ),
        ]
    )

    health = report["error_health"]
    assert health["current_independent_incident_count"] == 0
    assert health["historical_resolved_incident_count"] == 1
    incident = health["historical_resolved_incidents"][0]
    assert incident["category"] == "quote_pagination_protocol_anomaly"
    assert incident["traceback_count"] == 1
    assert "PaginationCursorProtocolError" in report["errors_and_warnings"][0]["message"]


def test_tested_pipeline_coverage_counts_decisions_and_stage_telemetry():
    old_version = "tested-reply-pipeline-20260816"
    current_version = "tested-reply-pipeline-20260817"
    version_by_target = {
        str(index): old_version if index < 8 else current_version
        for index in range(9)
    }
    events = []
    for target_id, version in version_by_target.items():
        events.extend([
            {
                "kind": "reply_strategy_decision",
                "strategy_version": version,
                "lane": "mention",
                "target_id": target_id,
            },
            {
                "kind": "reply_pipeline_stage_summary",
                "strategy_version": version,
                "lane": "mention",
                "target_id": target_id,
                "provider_call_counts": {"xAI": 1, "OpenAI": 1},
            },
        ])

    summary = digest.reply_pipeline_stage_summary(events)

    assert summary["tested_pipeline_decision_count"] == 9
    assert summary["all_stage_summary_event_count"] == 9
    assert summary["complete_stage_telemetry_count"] == 9
    assert summary["partial_or_legacy_telemetry_count"] == 0
    assert summary["evaluation_count"] == 9
    assert summary["strategy_version_counts"] == {
        old_version: {
            "decision_count": 8,
            "stage_summary_count": 8,
            "complete_stage_telemetry_count": 8,
            "partial_or_legacy_telemetry_count": 0,
        },
        current_version: {
            "decision_count": 1,
            "stage_summary_count": 1,
            "complete_stage_telemetry_count": 1,
            "partial_or_legacy_telemetry_count": 0,
        },
    }
    assert summary["latest_strategy_version"] == current_version
    assert summary["latest_strategy_version_decision_count"] == 1


def test_tested_pipeline_partial_telemetry_remains_explicit():
    version = "tested-reply-pipeline-20260817"
    events = [
        {
            "kind": "reply_strategy_decision",
            "strategy_version": version,
            "lane": "mention",
            "target_id": str(index),
        }
        for index in range(4)
    ]
    events.append({
        "kind": "reply_pipeline_stage_summary",
        "strategy_version": version,
        "lane": "mention",
        "target_id": "0",
    })

    summary = digest.reply_pipeline_stage_summary(events)

    assert summary["complete_stage_telemetry_count"] == 1
    assert summary["partial_or_legacy_telemetry_count"] == 3


def test_new_pipeline_evidence_fields_distinguish_supply_from_unknown_use():
    payload = {
        "event": "ai_reply_pipeline_decision",
        "status": "approved",
        "strategy_version": "tested-reply-pipeline-20260817",
        "lane": "mention",
        "target_id": "100",
        "mode": "direct_factual_answer",
        "final_reply_kind": "factual",
        "tone": "unknown",
        "reply_requirement": "supported_factual",
        "route_source": "supported_authentication_route",
        "trusted_facts_supplied_count": 2,
        "trusted_fact_ids_supplied": ["fact-1", "fact-2"],
        "used_fact_count": "unknown",
        "used_fact_ids": None,
        "claim_risk_categories": ["quotation_or_source"],
        "model_call_count": 5,
    }

    report = digest.analyse(
        [record(0, "INFO", "log_event", "EVENT " + json.dumps(payload))]
    )
    decision = next(
        event
        for event in report["events"]
        if event["kind"] == "reply_strategy_decision"
    )

    assert decision["final_reply_kind"] == "factual"
    assert decision["tone"] == "unknown"
    assert decision["trusted_facts_supplied_count"] == 2
    assert decision["trusted_fact_ids_supplied"] == ["fact-1", "fact-2"]
    assert decision["used_fact_count"] == "unknown"
    assert decision["evidence_reference_count"] is None
    assert decision["grounded"] is None


def test_nonfactual_pipeline_mode_and_unknown_reply_kind_stay_independent():
    common = {
        "strategy_version": "tested-reply-pipeline-20260817",
        "lane": "mention",
        "target_id": "100",
        "mode": "opinion_or_principle",
        "final_reply_kind": "unknown",
        "tone": "unknown",
        "reply_requirement": "general",
        "route_source": "xai_gate",
    }
    decision = {
        "event": "ai_reply_pipeline_decision",
        "status": "approved",
        **common,
    }
    outcome = {
        "event": "ai_reply_pipeline_outcome",
        "status": "confirmed",
        "reply_post_id": "900",
        **common,
    }

    report = digest.analyse(
        [
            record(0, "INFO", "log_event", "EVENT " + json.dumps(decision)),
            record(1, "INFO", "log_event", "EVENT " + json.dumps(outcome)),
        ]
    )
    events = report["events"]
    strategy = report["reply_strategy"]
    parsed_decision = next(
        event for event in events if event["kind"] == "reply_strategy_decision"
    )
    parsed_outcome = next(
        event for event in events if event["kind"] == "reply_strategy_outcome"
    )

    assert parsed_decision["mode"] == "opinion_or_principle"
    assert parsed_decision["final_reply_kind"] == "unknown"
    assert parsed_outcome["mode"] == "opinion_or_principle"
    assert parsed_outcome["final_reply_kind"] == "unknown"
    assert strategy["generated_mode_counts"]["opinion_or_principle"] == 1
    assert strategy["mode_counts"]["opinion_or_principle"] == 1
    assert strategy["generated_mode_counts"]["strategy metadata unavailable"] == 0
    assert strategy["mode_counts"]["strategy metadata unavailable"] == 0
    assert strategy["generated_final_reply_kind_counts"] == {"unknown": 1}
    assert strategy["final_reply_kind_counts"] == {"unknown": 1}
    rendered = digest.render_markdown(report)
    assert "Generated final reply kinds: unknown=1" in rendered
    assert "Published/terminal final reply kinds: unknown=1" in rendered


def test_provider_cost_unknown_zero_nonzero_and_lower_bound_average():
    usage = [
        {
            "provider": "OpenAI",
            "stage": "review",
            "lane": "mention",
            "context_id": "1",
            "cost_in_usd_ticks": None,
        },
        {
            "provider": "xAI",
            "stage": "gate",
            "lane": "mention",
            "context_id": "2",
            "cost_in_usd_ticks": 0,
        },
        {
            "provider": "xAI",
            "stage": "gate",
            "lane": "mention",
            "context_id": "3",
            "cost_in_usd_ticks": 10,
        },
    ]
    reply_events = [
        {
            "kind": "reply_strategy_decision",
            "lane": "mention",
            "target_id": str(index),
            "model_call_count": 1,
        }
        for index in range(1, 4)
    ]

    totals = digest.xai_usage_totals(usage)
    summary = digest.xai_reply_cost_summary(usage, reply_events)

    assert totals["cost_in_usd_ticks"] == 10
    assert totals["costed_call_count"] == 2
    assert totals["uncosted_successful_call_count"] == 1
    assert summary["known_cost_in_usd_ticks"] == 10
    assert summary["known_cost_per_costed_call"] == {"ticks": 10, "divisor": 2}
    openai = next(row for row in summary["providers"] if row["provider"] == "OpenAI")
    known_zero = next(
        row for row in summary["candidates"] if row["context_id"] == "2"
    )
    assert digest.format_reported_cost(openai) == "unknown"
    assert digest.format_reported_cost(known_zero) == digest.format_usd_ticks(0)


@pytest.mark.parametrize(
    ("terminal_event", "expected"),
    [
        ({"status": "no_reply", "reason": "claim_auditor_detected_unresolved_factual_claim"}, "pipeline_failed"),
        ({"status": "no_reply", "reason": "revision_limit_reached"}, "pipeline_failed"),
        ({"status": "no_reply", "reason": "editorial_no_reply", "mode": "no_reply"}, "deliberately_declined"),
    ],
)
def test_terminal_outcome_overrides_provisional_approval(terminal_event, expected):
    base = {
        "kind": "reply_strategy_decision",
        "lane": "mention",
        "target_id": "100",
        "model_call_count": 1,
        "status": "approved",
        "mode": "opinion_or_principle",
        "time": "2026-07-25 09:00:00",
    }
    terminal = {
        **base,
        **terminal_event,
        "time": "2026-07-25 09:01:00",
    }

    result = digest.xai_reply_cost_summary([], [base, terminal])

    assert result["candidates"][0]["outcome"] == expected


def test_published_confirmation_overrides_terminal_pipeline_failure():
    events = [
        {
            "kind": "reply_strategy_decision",
            "lane": "mention",
            "target_id": "100",
            "model_call_count": 1,
            "status": "no_reply",
            "reason": "revision_limit_reached",
        },
        {
            "kind": "reply_strategy_outcome",
            "lane": "mention",
            "target_id": "100",
            "status": "confirmed",
            "reply_post_id": "900",
            "model_call_count": 1,
        },
    ]

    result = digest.xai_reply_cost_summary([], events)

    assert result["candidates"][0]["outcome"] == "published"


def test_genuinely_approved_without_terminal_evidence_remains_unresolved():
    result = digest.xai_reply_cost_summary(
        [],
        [
            {
                "kind": "reply_strategy_decision",
                "lane": "mention",
                "target_id": "100",
                "model_call_count": 1,
                "status": "approved",
                "mode": "opinion_or_principle",
            }
        ],
    )

    assert result["candidates"][0]["outcome"] == (
        "approved_not_confirmed_in_window"
    )


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


def structured_main_post_lifecycle(
    start: int,
    *,
    lane: str,
    attempt_id: str,
) -> list[digest.Record]:
    receipt_name = (
        "regular_post_receipt.json"
        if lane == "quote_image"
        else "meme_post_receipt.json"
    )
    removal_label = "regular-post" if lane == "quote_image" else "meme-post"
    path = f"/srv/{receipt_name}"
    return [
        record(
            start,
            "WARNING",
            "write_main_post_attempt",
            f"Wrote main-post sending receipt lane={lane} "
            f"attempt_id={attempt_id} path={path}",
        ),
        record(
            start + 1,
            "WARNING",
            "mark_main_post_attempt_attempting",
            f"Promoted main-post receipt to attempting lane={lane} "
            f"attempt_id={attempt_id} path={path}",
        ),
        record(
            start + 2,
            "WARNING",
            "promote_main_post_attempt_to_pending_schedule",
            "Promoted main-post attempt to confirmed pending-schedule receipt "
            f"lane={lane} attempt_id={attempt_id} post_id={start + 1000} "
            f"path={path}",
        ),
        record(
            start + 3,
            "WARNING",
            "finalize_confirmed_pending_schedule_receipt",
            "Finalised confirmed pending-schedule receipt "
            f"lane={lane} post_id={start + 1000} path={path}",
        ),
        record(
            start + 4,
            "INFO",
            f"remove_{removal_label.replace('-', '_')}_receipt",
            f"Removed reconciled {removal_label} receipt: {path}",
        ),
    ]


@pytest.mark.parametrize(
    ("lane", "lane_summary"),
    [
        ("quote_image", "regular quote/image **1**; daily-meme **0**"),
        ("daily_meme", "regular quote/image **0**; daily-meme **1**"),
    ],
)
def test_structured_main_post_lifecycle_is_completed_not_unresolved(
    lane: str,
    lane_summary: str,
):
    report = digest.analyse(
        structured_main_post_lifecycle(0, lane=lane, attempt_id="attempt-1")
    )
    rendered = digest.render_markdown(report)

    assert "Routine two-phase receipt write/remove pairs completed: **1**" in rendered
    assert lane_summary in rendered
    assert "Stale or unresolved receipt events:" not in rendered


def test_main_post_removal_at_window_boundary_is_not_unresolved():
    report = digest.analyse([
        record(
            0,
            "INFO",
            "remove_regular_post_receipt",
            "Removed reconciled regular-post receipt: "
            "/srv/regular_post_receipt.json",
        )
    ])
    rendered = digest.render_markdown(report)

    assert "opening write was outside the selected window: **1**" in rendered
    assert "Stale or unresolved receipt events:" not in rendered


def test_structured_main_post_write_without_removal_remains_unresolved():
    records = structured_main_post_lifecycle(
        0,
        lane="quote_image",
        attempt_id="attempt-unresolved",
    )[:-1]
    rendered = digest.render_markdown(digest.analyse(records))

    assert "Routine two-phase receipt write/remove pairs completed: **0**" in rendered
    assert "Stale or unresolved receipt events:" in rendered
    assert "attempt-unresolved" in rendered


def test_mixed_structured_main_post_fixture_counts_fifteen_regular_and_one_meme():
    records: list[digest.Record] = []
    for index in range(15):
        records.extend(
            structured_main_post_lifecycle(
                index * 10,
                lane="quote_image",
                attempt_id=f"regular-{index}",
            )
        )
    records.extend(
        structured_main_post_lifecycle(
            200,
            lane="daily_meme",
            attempt_id="meme-1",
        )
    )

    rendered = digest.render_markdown(digest.analyse(records))

    assert "Routine two-phase receipt write/remove pairs completed: **16**" in rendered
    assert "regular quote/image **15**; daily-meme **1**" in rendered
    assert "Stale or unresolved receipt events:" not in rendered


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
