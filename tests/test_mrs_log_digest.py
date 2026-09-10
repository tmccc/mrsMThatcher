from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import builtins
import json

import pytest

import mrs_log_digest as digest

from tests.helpers.digest_incidents import ambiguous_media_records, reconciled_remote_write_safety
from tests.helpers.digest_records import (
    BASE,
    _normal_main_post_record,
    loaded_gate,
    record,
    traceback,
)


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
        "no later tweet-create request observed in window (uncorrelated)"
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


def _retired_observer_event_name() -> str:
    return "_".join(("quote", "image", "semantic", "veto", "shadow"))


def _retired_load_lifecycle_field() -> str:
    return "_".join(("semantic", "veto", "load", "lifecycle"))


def test_normal_main_post_analysis_has_no_retired_observer_output() -> None:
    report = digest.analyse([_normal_main_post_record()])

    assert report["summary"]["record_count"] == 1
    assert _retired_observer_event_name() not in report
    assert _retired_load_lifecycle_field() not in report
    rendered = digest.render_markdown(report)
    retired_heading = "## Quote/image " + "semantic veto shadow"
    assert retired_heading not in rendered


def test_old_retired_observer_event_is_ignored_safely() -> None:
    historical = {
        "event": _retired_observer_event_name(),
        "quote_hash": "a" * 64,
        "selected_image_hash": "b" * 64,
        "selected_image_basename": "t01.jpg",
        "shadow_status": "veto",
        "would_" + "veto_production_winner": True,
    }
    report = digest.analyse(
        [
            record(0, "INFO", "log_event", "EVENT " + json.dumps(historical)),
            record(1, "INFO", "log_event", _normal_main_post_record().msg),
        ]
    )

    assert report["summary"]["record_count"] == 2
    assert all(
        event.get("kind") != _retired_observer_event_name()
        for event in report["events"]
    )
    assert _retired_observer_event_name() not in report
    assert _retired_load_lifecycle_field() not in report


def test_digest_does_not_load_retired_observer_code_or_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module_name = ".".join(
        ("semantic_alignment", "_".join(("quote", "image", "semantic", "veto")))
    )
    runtime_name = "_".join(("quote", "image", "semantic", "veto", "runtime"))
    runtime_dir = tmp_path / runtime_name
    runtime_dir.mkdir()
    (runtime_dir / "shadow_status.json").write_text("{invalid", encoding="utf-8")
    log_path = tmp_path / "fixture.log"
    log_path.write_text(
        "2026-07-25 09:00:00 INFO main:1 - Main loop tick\n",
        encoding="utf-8",
    )
    imported: list[str] = []
    read_paths: list[Path] = []
    original_import = builtins.__import__
    original_read_bytes = Path.read_bytes

    def guarded_import(name, *args, **kwargs):
        imported.append(str(name))
        if name == module_name:
            raise AssertionError("retired observer module import attempted")
        return original_import(name, *args, **kwargs)

    def tracked_read_bytes(path: Path) -> bytes:
        read_paths.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(Path, "read_bytes", tracked_read_bytes)

    result = digest.main(
        [
            "--project-dir",
            str(tmp_path),
            "--no-state",
            "--json",
            str(log_path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert result == 0
    assert module_name not in imported
    assert not any(runtime_name in path.parts for path in read_paths)
    assert _retired_observer_event_name() not in payload
    assert _retired_load_lifecycle_field() not in payload


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
