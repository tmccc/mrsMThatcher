"""Digest headline and presentation regressions alongside fixed golden fixtures."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
import json

import pytest

import mrs_log_digest as digest
import mrs_log_digest_state_reporting as state_reporting_owner
from mrs_log_digest_contracts import report_section

from tests.helpers.digest_records import BASE, _normal_main_post_record, record


def test_known_report_section_access_tracks_original_dictionary_and_replacement():
    report = digest.analyse([])
    summary = report_section(report, "summary")
    resume = report_section(report, "resume_context")
    assert summary is report["summary"]
    assert resume is report["resume_context"]
    summary["record_count"] = 7
    resume["pending_mention"] = {"considered_seq": 3}
    assert report["summary"]["record_count"] == 7
    assert report["resume_context"]["pending_mention"]["considered_seq"] == 3

    replacement = dict(summary)
    report["summary"] = replacement
    assert report_section(report, "summary") is replacement
    assert summary is not replacement



def _initial_state_presentation(*, state=None, configs=None, current=0, unavailable=0, safety=None):
    return state_reporting_owner.prepare_headline_and_derived(
        stats=Counter(),
        error_health={
            "current_independent_incident_count": current,
            "historical_resolved_incident_count": 0,
            "resolution_unavailable_incident_count": unavailable,
        },
        current_remote_write_safety=safety,
        handled_api_restrictions=[], media_upload_incidents=[], self_test_errors=[],
        confirmed_post_recovery=[], confirmed_reply_recovery=[], receipt_events=[],
        asset_health=[], records=[],
        latest_state_summary=state if state is not None else {},
        configs=configs if configs is not None else {},
        plural_count=digest.plural_count, int_or_none=digest.int_or_none,
        parse_dt=digest.parse_dt,
    )


@pytest.mark.parametrize("current,unavailable,safety,expected", [
    pytest.param(0, 0, {}, "current health: no unresolved operational incidents", id="healthy"),
    pytest.param(0, 3, {}, "current health: no active incident established", id="status-unavailable"),
    pytest.param(2, 3, {}, "current health: 2 unresolved operational incidents", id="current-incidents"),
    pytest.param(0, 3, {
        "current_health_snapshot_authoritative": True,
        "configured": True, "available": True, "blocking": True,
    }, "current health: remote writes blocked; no independent operational incident established", id="blocked-without-incident"),
    pytest.param(2, 3, {
        "current_health_snapshot_authoritative": True,
        "configured": True, "available": True, "blocking": True,
    }, "current health: 2 unresolved operational incidents", id="blocked-with-incident"),
    pytest.param(2, 3, {
        "current_health_snapshot_authoritative": True,
        "configured": True, "available": False, "blocking": True,
    }, "current health: remote-write safety unknown (inspection unavailable)", id="inspection-overrides-incident"),
    pytest.param(0, 3, {
        "current_health_snapshot_authoritative": False,
        "configured": True, "available": False, "blocking": True,
    }, "current health: no active incident established", id="non-authoritative-safety"),
    pytest.param(0, 0, {
        "current_health_snapshot_authoritative": 1,
        "configured": True, "available": False, "blocking": True,
    }, "current health: no unresolved operational incidents", id="authority-requires-boolean"),
])
def test_initial_and_refreshed_health_keep_safety_precedence(current, unavailable, safety, expected):
    initial = _initial_state_presentation(current=current, unavailable=unavailable, safety=safety)
    assert initial[0]["current_health"] == expected

    base = ["counts kept", "current health: stale", "tail kept"]
    components = {
        **initial[0], "activity": ["counts kept"], "current_health": base[1],
        "observations": ["tail kept"],
    }
    report = {
        "summary": {
            "_headline_without_current_cooldown": base,
            "_headline_components": components,
        },
        "runtime_state_status": {"status": "absent"},
        "error_health": {
            "current_independent_incident_count": current,
            "resolution_unavailable_incident_count": unavailable,
        },
        "remote_write_safety": safety,
    }
    digest.refresh_current_health_headline(report)
    assert report["summary"]["headline"] == (
        f"counts kept; {expected}; tail kept; current API cooldown state unavailable"
    )
    assert base == ["counts kept", "current health: stale", "tail kept"]
    assert components["current_health"] == "current health: stale"


def test_initial_safety_override_skips_incident_formatter_but_refresh_keeps_it(monkeypatch):
    safety = {
        "current_health_snapshot_authoritative": True,
        "configured": True, "available": False,
    }
    original_plural = digest.plural_count

    def plural(*args):
        if args[1] == "unresolved operational incident":
            raise RuntimeError("incident formatter failed")
        return original_plural(*args)

    monkeypatch.setattr(digest, "plural_count", plural)
    initial = _initial_state_presentation(current=1, safety=safety)
    assert initial[0]["current_health"] == "current health: remote-write safety unknown (inspection unavailable)"

    report = {
        "summary": {
            "headline": "unchanged",
            "_headline_without_current_cooldown": ["current health: stale"],
            "_headline_components": initial[0],
        },
        "runtime_state_status": {"status": "absent"},
        "error_health": {"current_independent_incident_count": 1},
        "remote_write_safety": safety,
    }
    with pytest.raises(RuntimeError, match="incident formatter failed"):
        digest.refresh_current_health_headline(report)
    assert report["summary"]["headline"] == "unchanged"
    assert "current_cooldown_status" not in report


@pytest.mark.parametrize("configs,state,values,has_input", [
    pytest.param({}, {}, (None, None, None, None, None, None, None), False, id="missing"),
    pytest.param({
        "MAX_AUTO_REPLIES_PER_DAY": "0", "MAX_REPLIES_PER_AUTHOR_PER_DAY": "0",
        "MAX_QUOTE_REPLIES_PER_DAY": "0",
    }, {"daily_reply_count": "0", "daily_quote_reply_count": "0"},
        (0, 0, 0, 0, 0, 0, 0), True, id="zero-is-known"),
    pytest.param({
        "MAX_AUTO_REPLIES_PER_DAY": "4", "MAX_REPLIES_PER_AUTHOR_PER_DAY": "3",
        "MAX_QUOTE_REPLIES_PER_DAY": "1",
    }, {"daily_reply_count": "5", "daily_quote_reply_count": "2"},
        (5, 4, 3, -1, 2, 1, -1), True, id="over-budget"),
    pytest.param({
        "MAX_AUTO_REPLIES_PER_DAY": "invalid", "MAX_QUOTE_REPLIES_PER_DAY": "7",
    }, {"daily_reply_count": "5", "daily_quote_reply_count": "invalid"},
        (5, None, None, None, None, 7, None), True, id="partially-invalid"),
    pytest.param({"MAX_REPLIES_PER_AUTHOR_PER_DAY": "3"}, {},
        (None, None, 3, None, None, None, None), True, id="author-limit-only"),
])
def test_initial_and_refreshed_budgets_share_values_but_keep_distinct_metadata(configs, state, values, has_input):
    shared_timestamp = ["shared backscan timestamp"]
    shared_priority = ["shared priority"]
    configs = {
        **configs, "_carried_forward": True, "_carried_from_log_backscan": True,
        "_log_backscan_timestamp": shared_timestamp, "_filled_from_previous": True,
        "_filled_from_log_backscan": True, "_config_source": "mrsMThatcher.local.json",
    }
    state = {
        **state, "_carried_forward": True, "_filled_from_previous": True,
        "next_reply_lane_priority": shared_priority,
    }
    expected = dict(zip((
        "auto_used", "auto_limit", "per_author_limit", "auto_remaining",
        "quote_used", "quote_limit", "quote_remaining",
    ), values))
    initial = _initial_state_presentation(state=state, configs=configs)[5]
    assert initial["reply_budget"] == expected
    assert list(initial["reply_budget"]) == list(expected)
    assert initial["reply_lane_priority"]["current_next_priority"] is shared_priority

    report = {"latest_state": state, "latest_config": configs}
    digest.refresh_derived(report)
    expected_refreshed = {
        **expected, "has_any_budget_input": has_input,
        "state_carried_forward": True, "config_carried_forward": True,
        "config_carried_from_log_backscan": True,
        "config_backscan_timestamp": shared_timestamp,
        "state_filled_from_previous": True, "config_filled_from_previous": True,
        "config_filled_from_log_backscan": True, "config_is_on_disk_override": True,
    }
    refreshed = report["derived"]
    assert refreshed["reply_budget"] == expected_refreshed
    assert list(refreshed["reply_budget"]) == list(expected_refreshed)
    assert refreshed["reply_budget"]["config_backscan_timestamp"] is shared_timestamp
    assert refreshed["reply_lane_priority"]["current_next_priority"] is shared_priority
    assert report["latest_state"] is state and report["latest_config"] is configs


def test_prepared_headlines_keep_current_callbacks_timing_and_shared_results(monkeypatch):
    calls, captured = [], {}
    media = [{"status": status} for status in ("handled", "reconciled", "failed")]
    records = [record(0, "INFO", "fixture", "prepared headline fixture")]
    state = {
        "time": digest.dt_text(BASE + timedelta(seconds=60)),
        "api_cooldown_until_epoch": int(BASE.timestamp()) + 120,
        "x_write_api_cooldown_until_epoch": int(BASE.timestamp()),
        "openai_api_cooldown_until_epoch": 0,
        "quote_api_cooldown_until_epoch": int(BASE.timestamp()) - 1,
        "daily_reply_count": "5", "daily_quote_reply_count": "2",
        "next_reply_lane_priority": ["shared priority"],
    }
    original_plural = digest.plural_count
    original_prepare = digest.prepare_headline_and_derived
    original_finalise = digest.prepare_reply_quality_headline
    helper_calls = []
    for name in ("plural_count", "int_or_none", "parse_dt"):
        original = getattr(digest, name)

        def helper(*args, _name=name, _original=original, **kwargs):
            helper_calls.append((_name, args))
            return _original(*args, **kwargs)

        monkeypatch.setattr(digest, name, helper)

    def prepare(**inputs):
        assert inputs["records"] is records
        for name in ("plural_count", "int_or_none", "parse_dt"):
            assert inputs[name] is getattr(digest, name)
        inputs["media_upload_incidents"].extend(media)
        inputs["latest_state_summary"].update(state)
        inputs["configs"].update(MAX_AUTO_REPLIES_PER_DAY="4", MAX_REPLIES_PER_AUTHOR_PER_DAY="3", MAX_QUOTE_REPLIES_PER_DAY="7")
        inputs["error_health"]["transient_provider_timeout_count"] = 2
        helper_calls.clear()
        result = original_prepare(**inputs)
        assert result[1] == 2
        assert result[0]["reply_quality"] == []
        assert result[0]["cooldown"] == ["X read API cooldown active now", "X write API cooldown occurred, now expired"]
        assert [args[0] for name, args in helper_calls if name == "int_or_none"] == [
            state["api_cooldown_until_epoch"], state["x_write_api_cooldown_until_epoch"],
            0, state["quote_api_cooldown_until_epoch"], "4", "3", "7", "5", "2",
        ]
        assert [(name, args) for name, args in helper_calls if name == "parse_dt"] == [("parse_dt", (state["time"],))]
        assert helper_calls[0] == ("plural_count", (0, "quote/image post"))
        assert result[5]["reply_budget"]["auto_remaining"] == -1
        assert result[5]["reply_budget"]["quote_remaining"] == 5
        assert result[5]["reply_lane_priority"]["current_next_priority"] is state["next_reply_lane_priority"]
        captured.update(initial=result, inputs=inputs)
        calls.append("headline")
        return result

    def single_quality(events):
        calls.append("single quality")
        monkeypatch.setattr(digest, "plural_count", lambda *args: "current " + original_plural(*args))
        return {"candidate_evaluation_count": 1}

    def finalise(**inputs):
        assert inputs["headline"] is captured["initial"][0]
        assert inputs["plural_count"] is digest.plural_count
        result = original_finalise(**inputs)
        assert result[3] is not inputs["headline"]
        assert result[2] is not result[1]
        assert inputs["headline"]["reply_quality"] == []
        assert not any("Grok skip" in item for item in result[1])
        assert result[1][6].startswith("current 1 single-call candidate evaluated")
        captured["final"] = result
        calls.append("finalise")
        return result

    def watch(name, label):
        original = getattr(digest, name)

        def call(*args, **kwargs):
            calls.append(label)
            if label == "api":
                assert kwargs["transient_provider_timeouts"] == 2
            return original(*args, **kwargs)

        monkeypatch.setattr(digest, name, call)

    for name, label in (("prepare_api_health", "api"), ("prepare_inferred_reply_strategy_outcomes", "inference"), ("historical_context_quality_summary", "context quality"), ("prepare_mention_control_observations", "mention"), ("api_health_report", "api report")):
        watch(name, label)
    monkeypatch.setattr(digest, "prepare_headline_and_derived", prepare)
    monkeypatch.setattr(digest, "single_call_reply_summary", single_quality)
    monkeypatch.setattr(digest, "prepare_reply_quality_headline", finalise)
    report = digest.analyse(records, generation_time=BASE + timedelta(days=1))
    assert calls == ["headline", "api", "inference", "context quality", "single quality", "finalise", "mention", "api report"]
    for index, key in enumerate(("handled_fallbacks", "reconciled_incidents", "unrecovered_failures"), 2):
        assert report["media_upload"][key] is captured["initial"][index]
        assert report["media_upload"][key][0] is media[index - 2]
    assert report["media_upload"]["incidents"] is captured["inputs"]["media_upload_incidents"]
    assert report["derived"] is captured["initial"][5]
    assert report["legacy_multi_stage"] is captured["final"][0]
    assert report["summary"]["_headline_without_current_cooldown"] is captured["final"][2]
    assert report["summary"]["_headline_components"] is captured["final"][3]


@pytest.mark.parametrize("health", ["current health: supplied", "reworded health claim"])
def test_headline_component_roles_do_not_depend_on_claim_wording(health):
    # Observations can resemble health, legacy skip or cooldown wording; only
    # the designated current-health/cooldown components may be replaced.
    observations = [
        "current health: quoted historical observation", "2 Grok skips",
        "no API cooldown", "X read API cooldown historical observation",
    ]
    headline = {
        "activity": ["keep", "1 Grok skip"], "reply_quality": [],
        "current_health": health, "observations": observations,
        "cooldown": ["reworded historical cooldown claim"],
    }
    original = json.loads(json.dumps(headline))
    legacy, replacement, base, components = state_reporting_owner.prepare_reply_quality_headline(
        events=[{"kind": "reply_strategy_decision"}, {"kind": "reply_pipeline_stage_summary"}],
        headline=headline, single_call_quality={"candidate_evaluation_count": 1},
        plural_count=lambda *args: str(args[1]),
    )
    assert headline == original and components is not headline and base is not replacement
    assert legacy == {"decision_count": 1, "stage_summary_event_count": 1}
    inserted = ["single-call candidate evaluated; reply posted; editorial no-reply decision; operational failure; one-call compliance None", "legacy multi-stage decisions 1; legacy stage summaries 1"]
    assert base == ["keep", "1 Grok skip", *inserted, health, *observations]
    assert replacement == [*base, "reworded historical cooldown claim"]

    report = {
        "summary": {
            "headline": "; ".join(replacement),
            "_headline_without_current_cooldown": base,
            "_headline_components": components,
        },
        "runtime_state_status": {"status": "absent"},
    }
    # JSON round-tripping keeps the private components usable without exposing
    # custom objects or replacing the existing compatibility list.
    report = json.loads(json.dumps(report))
    digest.refresh_current_health_headline(report)
    expected = [
        "keep", "1 Grok skip", *inserted,
        "current health: no unresolved operational incidents", *observations,
        "current API cooldown state unavailable",
    ]
    assert report["summary"]["headline"] == "; ".join(expected)
    assert report["summary"]["_headline_without_current_cooldown"] == base
    assert report["summary"]["_headline_components"] == components
    digest.refresh_current_health_headline(report)
    assert report["summary"]["headline"] == "; ".join(expected)


def test_standalone_headline_keeps_historical_claims_until_runtime_overlay():
    report = digest.analyse([
        record(0, "INFO", "fixture", "headline fixture"),
    ], generation_time=BASE + timedelta(days=1))
    summary = report["summary"]
    original = summary["headline"]
    assert original.endswith("no API cooldown")
    digest.refresh_current_health_headline(report)
    assert summary["headline"] == original

    report["runtime_state_status"] = {"status": "absent"}
    digest.refresh_current_health_headline(report)
    assert summary["headline"].endswith("current API cooldown state unavailable")
    assert summary["_headline_without_current_cooldown"] == original.split("; ")[:-1]


def test_health_refresh_does_not_infer_component_roles_from_legacy_list():
    report = {
        "summary": {
            "headline": "historical headline retained",
            "_headline_without_current_cooldown": ["current health: stale"],
        },
        "runtime_state_status": {"status": "absent"},
    }
    digest.refresh_current_health_headline(report)
    assert report["summary"]["headline"] == "historical headline retained"
    assert "current_cooldown_status" not in report


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
    report = digest.analyse([_normal_main_post_record()])

    assert digest.render_markdown(report).encode() == digest.render_markdown(report).encode()
    assert json.dumps(report, sort_keys=True) == json.dumps(report, sort_keys=True)
